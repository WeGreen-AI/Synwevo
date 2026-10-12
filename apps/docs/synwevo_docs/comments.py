"""Comments and mentions on a project.

A comment belongs to a project and is written by a member of the project's workspace; it may
be anchored to something in the project — a template field, a mapping, a source file, a job —
or to the project as a whole. Anchors are checked when comments are read, not trusted from when
they were written: a field that was deleted, a mapping that is gone, a template that was
replaced makes the anchor stale, and a stale anchor is shown as such instead of jumping to
whatever now sits at that position.

Who is told: people the author mentions (members of the workspace only — mentioning someone
who cannot open the project tells them nothing), and the people already in the conversation on
that project. The author is never told about their own comment. Editing and deleting are the
author's; resolving a thread is for anyone who may write to the project or the author.
"""
from __future__ import annotations

import json
import re
import unicodedata

from synwevo_core import auth as _auth
from synwevo_core import context
from synwevo_core.errors import ServiceError
from synwevo_core.store import new_id, now

MAX_BODY = 4000
MAX_MENTIONS = 20
MAX_LIST = 500
ANCHOR_KINDS = ("project", "slot", "mapping", "source", "job")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_body(text) -> str:
    if not isinstance(text, str):
        raise ServiceError(400, "invalid_field", "评论内容无效", {"field": "body"})
    text = unicodedata.normalize("NFC", _CONTROL.sub("", text)).strip()
    if not text:
        raise ServiceError(400, "invalid_field", "评论不能为空", {"field": "body"})
    if len(text) > MAX_BODY:
        raise ServiceError(400, "invalid_field", f"评论最多 {MAX_BODY} 个字符", {"field": "body", "limit": MAX_BODY})
    return text


def excerpt(text: str, n: int = 120) -> str:
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"


class Comments:
    def __init__(self, app, ident, notifier=None):
        self.app, self.ident, self.notifier = app, ident, notifier
        self.store = app.store

    # ------------------------------------------------------------------ reading
    def members(self, pid: str) -> list[dict]:
        """Who can be mentioned: the members of the project's workspace, names only."""
        p = self.app.get_project(pid)
        rows = self.store.all("SELECT m.user_id, u.display_name, u.email, u.status FROM memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.workspace_id=? AND u.deleted_at IS NULL ORDER BY u.display_name", (p["tenant"],))
        return [{"user_id": r["user_id"], "name": r["display_name"] or (r["email"] or "").split("@")[0] or "—"}
                for r in rows if r["status"] == "active"]

    def list(self, pid: str, *, include_resolved: bool = False) -> list[dict]:
        p = self.app.get_project(pid)
        rows = self.store.all("SELECT * FROM project_comments WHERE project_id=? AND deleted_at IS NULL ORDER BY created_at LIMIT ?", (pid, MAX_LIST))
        if not include_resolved:
            resolved = {r["id"] for r in rows if r.get("resolved_at")}
            rows = [r for r in rows if not r.get("resolved_at") and r.get("parent_id") not in resolved]   # a resolved thread hides with its replies
        ctx = self._anchor_context(p)
        names = self._names({r["author_id"] for r in rows} | {r["resolved_by"] for r in rows if r.get("resolved_by")})
        return [self._public(r, ctx, names) for r in rows]

    def get(self, cid: str) -> dict:
        r = self.store.one("SELECT * FROM project_comments WHERE id=? AND deleted_at IS NULL", (cid,))
        if not r:
            raise ServiceError(404, "no_comment", "找不到该评论")
        self.app.get_project(r["project_id"])          # the tenant check: a comment in another workspace is 404
        return r

    # ------------------------------------------------------------------ writing
    def _audit(self, action: str, actor_id: str, row: dict, **meta) -> None:
        """One row per change to a comment, in the transaction of the change (admin coverage G26,
        decision D7): the request's principal when there is one, else the person named by the
        caller; the comment and project identifiers only — never the body."""
        _auth.audit_event(self.store, action, actor=context.principal() or {"kind": "user", "user_id": actor_id},
                          workspace_id=row["tenant"], object_kind="comment", object_id=row["id"],
                          meta={"project_id": row["project_id"], "parent_id": row.get("parent_id"),
                                **{k: v for k, v in meta.items() if v is not None}})

    def create(self, pid: str, author_id: str, body, *, anchor=None, mentions=None, parent_id=None) -> dict:
        p = self.app.get_project(pid)
        text = clean_body(body)
        anchor = self._check_anchor(p, anchor)
        mentioned = self._check_mentions(p, mentions, author_id)
        if parent_id:
            parent = self.get(parent_id)
            if parent["project_id"] != pid or parent.get("parent_id"):
                raise ServiceError(400, "invalid_field", "只能回复本项目的一级评论", {"field": "parent_id"})
        row = {"id": new_id("cm"), "project_id": pid, "tenant": p["tenant"], "author_id": author_id, "body": text,
               "anchor": json.dumps(anchor, ensure_ascii=False) if anchor else None,
               "mentions": json.dumps(mentioned) if mentioned else None, "parent_id": parent_id or None,
               "resolved_at": None, "resolved_by": None, "created_at": now(), "edited_at": None, "deleted_at": None}
        with self.store.tx() as t:
            self.store.insert("project_comments", row)
            # The card for the project's conversation (CI-75) rides on the same transaction as the
            # comment: stored together or not at all, keyed by the comment so a retry is one card.
            from synwevo_core.messaging_bridge import emit_comment
            emit_comment(t, comment_id=row["id"], project_id=pid, workspace_id=p["tenant"], author_id=author_id,
                         mentions=mentioned, parent_id=parent_id or None)
            self._audit("docs.comment.create", author_id, row, anchored=bool(anchor) or None, mentions=len(mentioned) or None)
        self._tell(p, row, mentioned, author_id)
        # Whoever has the project open sees the comment arrive (the project room's change frame)
        # and the activity feed attributes it. The body itself is not copied into the feed: it is
        # read from the comment, which can be edited or deleted later.
        self._changed(pid, "comment.reply" if parent_id else "comment.add", {"id": row["id"], "parent_id": parent_id or None, "anchored": bool(anchor), "mentions": len(mentioned)})
        return self._public(self.store.one("SELECT * FROM project_comments WHERE id=?", (row["id"],)), self._anchor_context(p), self._names({author_id}))

    def edit(self, cid: str, actor_id: str, body, *, mentions=None) -> dict:
        r = self.get(cid)
        if r["author_id"] != actor_id:
            raise ServiceError(403, "forbidden", "只有作者可以修改这条评论")
        p = self.app.get_project(r["project_id"])
        text = clean_body(body)
        before = set(json.loads(r["mentions"]) if r.get("mentions") else [])
        mentioned = self._check_mentions(p, mentions if mentions is not None else list(before), actor_id)
        with self.store.tx():
            self.store.update("project_comments", {"id": cid}, {"body": text, "mentions": json.dumps(mentioned) if mentioned else None, "edited_at": now()})
            self._audit("docs.comment.edit", actor_id, r, mentions_added=len([m for m in mentioned if m not in before]) or None)
        row = self.store.one("SELECT * FROM project_comments WHERE id=?", (cid,))
        self._tell(p, row, [m for m in mentioned if m not in before], actor_id, only_mentions=True)
        self._changed(r["project_id"], "comment.edit", {"id": cid, "parent_id": r.get("parent_id")})
        return self._public(row, self._anchor_context(p), self._names({actor_id}))

    def delete(self, cid: str, actor_id: str) -> None:
        r = self.get(cid)
        if r["author_id"] != actor_id and not self._administers(r["tenant"], actor_id):
            raise ServiceError(403, "forbidden", "只有作者或工作空间管理员可以删除这条评论")
        with self.store.tx():
            self.store.update("project_comments", {"id": cid}, {"deleted_at": now()})
            replies = self.store.execute("UPDATE project_comments SET deleted_at=? WHERE parent_id=? AND deleted_at IS NULL", (now(), cid))
            self._audit("docs.comment.delete", actor_id, r, replies_removed=replies or None,
                        by_administrator=True if r["author_id"] != actor_id else None)
        self._changed(r["project_id"], "comment.delete", {"id": cid, "parent_id": r.get("parent_id")})

    def resolve(self, cid: str, actor_id: str, resolved: bool) -> dict:
        r = self.get(cid)
        if r.get("parent_id"):
            raise ServiceError(400, "invalid_field", "请在一级评论上标记解决")
        m = self.ident.membership(r["tenant"], actor_id) or {}
        if r["author_id"] != actor_id and m.get("role") not in ("owner", "admin", "editor"):
            raise ServiceError(403, "forbidden", "只有作者或能编辑项目的成员可以标记解决")
        if resolved and r.get("resolved_at"):
            raise ServiceError(409, "already_resolved", "这条评论已经标记为解决")
        if not resolved and not r.get("resolved_at"):
            raise ServiceError(409, "not_resolved", "这条评论没有被标记为解决")
        with self.store.tx():
            self.store.update("project_comments", {"id": cid}, {"resolved_at": now() if resolved else None, "resolved_by": actor_id if resolved else None})
            self._audit("docs.comment.resolve" if resolved else "docs.comment.reopen", actor_id, r)
        p = self.app.get_project(r["project_id"])
        row = self.store.one("SELECT * FROM project_comments WHERE id=?", (cid,))
        self._changed(r["project_id"], "comment.resolve" if resolved else "comment.reopen", {"id": cid})
        return self._public(row, self._anchor_context(p), self._names({row["author_id"], actor_id}))

    def _changed(self, pid: str, kind: str, detail: dict) -> None:
        hook = getattr(self.app, "_project_changed", None)
        if hook is not None:
            hook(pid, kind, detail)

    # ------------------------------------------------------------------ anchors
    def _anchor_context(self, p: dict) -> dict:
        """What currently exists in the project, so each anchor can be judged at read time."""
        slots: dict[str, dict] = {}
        tpl_version = None
        if p.get("template_id"):
            try:
                tpl = self.app.get_template(p["template_id"])
                ver = self.app.get_version(tpl["id"], p.get("template_version") or tpl["head"])
                tpl_version = ver["version_no"]
                slots = {s["id"]: s for s in (ver.get("slots") or [])}
            except ServiceError:
                pass
        maps = {m["id"]: m for m in ((self.app.get_mappings(p["id"]) or {}).get("mappings") or []) if m.get("id")}
        sources = {s["id"]: s for s in self.app.list_sources(p["id"])}
        return {"template_id": p.get("template_id"), "template_version": tpl_version, "slots": slots, "mappings": maps, "sources": sources, "pid": p["id"]}

    def _check_anchor(self, p: dict, anchor) -> dict | None:
        if not anchor:
            return None
        if not isinstance(anchor, dict) or anchor.get("kind") not in ANCHOR_KINDS:
            raise ServiceError(400, "invalid_field", "锚点类型无效", {"field": "anchor"})
        kind, aid = anchor["kind"], str(anchor.get("id") or "")
        ctx = self._anchor_context(p)
        if kind == "project":
            return {"kind": "project"}
        if kind == "slot":
            s = ctx["slots"].get(aid)
            if not s:
                raise ServiceError(400, "invalid_field", "锚点指向的字段不存在", {"field": "anchor"})
            return {"kind": "slot", "id": aid, "label": s.get("name") or aid, "template_id": ctx["template_id"], "template_version": ctx["template_version"]}
        if kind == "mapping":
            m = ctx["mappings"].get(aid)
            if not m:
                raise ServiceError(400, "invalid_field", "锚点指向的映射不存在", {"field": "anchor"})
            slot = ctx["slots"].get((m.get("target") or {}).get("binding") or "") or {}
            return {"kind": "mapping", "id": aid, "label": slot.get("name") or aid, "mapping_rev": p.get("mapping_rev") or 0}
        if kind == "source":
            s = ctx["sources"].get(aid)
            if not s:
                raise ServiceError(400, "invalid_field", "锚点指向的来源文件不存在", {"field": "anchor"})
            return {"kind": "source", "id": aid, "label": s.get("filename") or aid, "version": s.get("version") or 1}
        j = self.store.one("SELECT id FROM jobs WHERE id=? AND project_id=?", (aid, p["id"]))
        if not j:
            raise ServiceError(400, "invalid_field", "锚点指向的任务不存在", {"field": "anchor"})
        return {"kind": "job", "id": aid, "label": aid}

    @staticmethod
    def _judge(anchor: dict | None, ctx: dict) -> dict | None:
        """The anchor as stored, plus whether it still points at the same thing."""
        if not anchor:
            return None
        out = dict(anchor)
        kind, aid = anchor.get("kind"), anchor.get("id")
        if kind == "project":
            out["valid"] = True
        elif kind == "slot":
            s = ctx["slots"].get(aid)
            same_template = anchor.get("template_id") == ctx["template_id"]
            out["valid"] = bool(s) and same_template
            out["reason"] = None if out["valid"] else ("template_changed" if not same_template else "slot_gone")
            if s:
                out["label"] = s.get("name") or out.get("label")
        elif kind == "mapping":
            m = ctx["mappings"].get(aid)
            out["valid"] = bool(m)
            out["reason"] = None if m else "mapping_gone"
        elif kind == "source":
            s = ctx["sources"].get(aid)
            out["valid"] = bool(s) and not s.get("removed")
            out["reason"] = None if out["valid"] else "source_gone"
            if s and (s.get("version") or 1) != anchor.get("version"):
                out["changed"] = True
        elif kind == "job":
            out["valid"] = True
        return out

    # ------------------------------------------------------------------ people
    def _check_mentions(self, p: dict, mentions, author_id: str) -> list[str]:
        if not mentions:
            return []
        if not isinstance(mentions, list) or len(mentions) > MAX_MENTIONS:
            raise ServiceError(400, "invalid_field", "提及的人过多", {"field": "mentions", "limit": MAX_MENTIONS})
        out: list[str] = []
        for uid in mentions:
            if not isinstance(uid, str) or uid == author_id or uid in out:
                continue
            m = self.ident.membership(p["tenant"], uid)
            u = self.ident.get_user(uid) if m else None
            if m and u and u.get("status") == "active" and not u.get("deleted_at"):
                out.append(uid)          # members only; a name that cannot open the project is dropped, not announced
        return out

    def _administers(self, tenant: str, uid: str) -> bool:
        m = self.ident.membership(tenant, uid) or {}
        return m.get("role") in ("owner", "admin")

    def _names(self, uids: set[str]) -> dict[str, str]:
        out = {}
        for uid in uids:
            if not uid:
                continue
            u = self.ident.get_user(uid) or {}
            out[uid] = u.get("display_name") or (u.get("email") or "").split("@")[0] or "—"
        return out

    def _participants(self, pid: str, except_ids: set[str]) -> list[str]:
        rows = self.store.all("SELECT DISTINCT author_id FROM project_comments WHERE project_id=? AND deleted_at IS NULL", (pid,))
        return [r["author_id"] for r in rows if r["author_id"] not in except_ids]

    def _tell(self, p: dict, row: dict, mentioned: list[str], author_id: str, *, only_mentions: bool = False) -> None:
        if not self.notifier:
            return
        who = self._names({author_id}).get(author_id, "—")
        target = {"origin": "docs", "path": f"/p/{p['id']}?comment={row['id']}"}
        params = {"who": who, "project": p["name"], "excerpt": excerpt(row["body"])}
        for uid in mentioned:
            self.notifier.emit(uid, "docs.mention", params=params, workspace_id=p["tenant"], object_kind="comment", object_id=row["id"],
                               dedupe_key=f"comment:{row['id']}:mention:{uid}", actionable=True, target=target)
        if only_mentions:
            return
        for uid in self._participants(p["id"], {author_id, *mentioned}):
            if not self.ident.membership(p["tenant"], uid):
                continue                                       # someone who left the workspace is not told
            self.notifier.emit(uid, "docs.comment", params=params, workspace_id=p["tenant"], object_kind="comment", object_id=row["id"],
                               dedupe_key=f"comment:{row['id']}:participant:{uid}", target=target)

    # ------------------------------------------------------------------ view
    def _public(self, r: dict, ctx: dict, names: dict[str, str]) -> dict:
        anchor = json.loads(r["anchor"]) if r.get("anchor") else None
        return {"id": r["id"], "project_id": r["project_id"], "parent_id": r.get("parent_id"),
                "author": {"user_id": r["author_id"], "name": names.get(r["author_id"]) or self._names({r["author_id"]}).get(r["author_id"], "—")},
                "body": r["body"], "mentions": json.loads(r["mentions"]) if r.get("mentions") else [],
                "anchor": self._judge(anchor, ctx), "resolved_at": r.get("resolved_at"),
                "resolved_by": ({"user_id": r["resolved_by"], "name": names.get(r["resolved_by"]) or self._names({r["resolved_by"]}).get(r["resolved_by"], "—")}
                                if r.get("resolved_by") else None),
                "created_at": r["created_at"], "edited_at": r.get("edited_at")}
