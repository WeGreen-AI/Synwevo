"""Synwevo Forms: the business rules, used by the operations (pages, API, flows, Agent) and by the
fill-in links (people without an account).

Every read and write names its workspace: a form, request or submission is looked up *within* the
workspace the caller was authorized for, so an id from another workspace reads as missing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from typing import Any

from synwevo_core import data_flow, env, sealed
from synwevo_core.capabilities import Call, canonical, fingerprint, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, new_id, now

from .definition import Definition, check_answers, problems
from ..common.files import read_blob

log = logging.getLogger("synwevo.forms")
APP = "forms"
LINK_PURPOSE = "forms-fill-link"
#: One request's prefill: bounded like any answer.
MAX_PREFILL_BYTES = 64 * 1024
MAX_DRAFT_BYTES = 256 * 1024
REQUEST_STATES_OPEN = ("open", "changes_requested")


def _j(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return default


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def fill_url(token: str) -> str:
    """The address a person opens to fill in. From this process's own configured origin (the
    workplace origin); a relative path when none is configured."""
    base = (env.get("APP_URL") or "").rstrip("/")
    return f"{base}/forms/fill/{token}"


class Forms:
    def __init__(self, store: Store, identity, capabilities=None):
        self.store = store
        self.identity = identity
        self.caps = capabilities
        self._expire_lock = threading.Lock()

    def file_bytes(self, user_id: str, workspace_id: str, file_id: str, *, submission_id: str | None = None):
        """Export a submitted attachment through the same policy for downloads and adapters."""
        self.caps.check_workspace(user_id, APP, workspace_id, "read")
        member = self.identity.membership(workspace_id, user_id)
        if not (member and (member["role"] in ("owner", "admin", "editor") or member.get("can_export"))):
            raise ServiceError(403, "export_not_allowed", "你在该工作空间没有导出权限，不能下载附件")
        row = self.store.one("SELECT * FROM form_files WHERE id=? AND workspace_id=? AND state='attached'",
                             (file_id, workspace_id))
        if not row or (submission_id is not None and row["submission_id"] != submission_id):
            raise ServiceError(404, "file_not_found", "附件不存在或不属于该提交")
        return row, read_blob(self.store, row["blob"])

    # ============================================================== definitions
    def _form(self, workspace_id: str, form_id: str) -> dict:
        row = self.store.one("SELECT * FROM forms WHERE id=? AND workspace_id=?", (form_id, workspace_id))
        if not row:
            raise ServiceError(404, "form_not_found", "表单不存在或不在该工作空间")
        return row

    def _version(self, form_id: str, version: int) -> dict:
        row = self.store.one("SELECT * FROM form_versions WHERE form_id=? AND version=?", (form_id, version))
        if not row:
            raise ServiceError(404, "form_version_not_found", "该表单版本不存在")
        return row

    def form_json(self, row: dict, *, with_versions: bool = False, can_write: bool = False) -> dict:
        out = {"id": row["id"], "workspace_id": row["workspace_id"], "title": row["title"],
               "description": row["description"], "status": row["status"],
               "draft": _j(row["draft_json"], {}), "draft_rev": row["draft_rev"],
               "published_version": row["published_version"],
               "public_link": None, "public_version": row["public_version"],
               "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
        if row["public_token_sealed"]:
            out["public_link"] = {"enabled": True,
                                  "url": fill_url(sealed.unseal(self.store, LINK_PURPOSE, row["public_token_sealed"]))
                                  if can_write else None}
        if with_versions:
            out["versions"] = [
                {"version": v["version"], "status": v["status"], "title": v["title"], "note": v["note"],
                 "published_by": v["published_by"], "published_at": v["published_at"], "retired_at": v["retired_at"],
                 "definition_hash": v["definition_hash"]}
                for v in self.store.all("SELECT * FROM form_versions WHERE form_id=? ORDER BY version DESC", (row["id"],))]
        return out

    def version_json(self, workspace_id: str, form_id: str, version: int) -> dict:
        self._form(workspace_id, form_id)
        v = self._version(form_id, version)
        return {"form_id": form_id, "version": v["version"], "status": v["status"], "title": v["title"],
                "definition": _j(v["definition_json"], {}), "definition_hash": v["definition_hash"],
                "published_at": v["published_at"], "note": v["note"]}

    def list_forms(self, workspace_id: str, *, status: str = "active", q: str | None = None) -> list[dict]:
        sql = ("SELECT f.*, (SELECT COUNT(*) FROM form_requests r WHERE r.form_id=f.id AND r.kind='personal') AS n_requests, "
               "(SELECT COUNT(*) FROM form_submissions s WHERE s.form_id=f.id AND s.status!='superseded') AS n_submissions "
               "FROM forms f WHERE f.workspace_id=? AND f.status=?")
        args: list = [workspace_id, status]
        if q:
            sql += " AND f.title LIKE ? ESCAPE '\\'"
            args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        rows = self.store.all(sql + " ORDER BY f.updated_at DESC LIMIT 500", tuple(args))
        return [{"id": r["id"], "title": r["title"], "description": r["description"], "status": r["status"],
                 "published_version": r["published_version"], "public": bool(r["public_token_hash"]),
                 "requests": r["n_requests"], "submissions": r["n_submissions"], "updated_at": r["updated_at"]}
                for r in rows]

    def create(self, call: Call, *, title: str, description: str, definition: Definition) -> dict:
        fid = new_id("frm")
        ts = now()
        self.store.execute(
            "INSERT INTO forms (id,workspace_id,title,description,status,draft_json,draft_rev,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,'active',?,1,?,?,?)",
            (fid, call.workspace_id, title, description, canonical(definition.model_dump(mode="json", by_alias=True)),
             call.user_id, ts, ts))
        call.touches("form", fid)
        return self.form_json(self._form(call.workspace_id, fid), with_versions=True, can_write=True)

    def update_draft(self, call: Call, form_id: str, *, draft_rev: int, title: str | None,
                     description: str | None, definition: Definition | None) -> dict:
        row = self._form(call.workspace_id, form_id)
        if row["status"] != "active":
            raise ServiceError(409, "form_archived", "表单已归档，恢复后才能编辑")
        if row["draft_rev"] != draft_rev:
            # Someone else saved in between: say so and hand back what is there now, so the editor
            # can show the difference instead of silently overwriting either version.
            raise ServiceError(409, "draft_conflict", "表单草稿已被其他人修改，请查看最新内容后再保存",
                               {"current": self.form_json(row, can_write=True)})
        sets, args = ["draft_rev=draft_rev+1", "updated_at=?"], [now()]
        if title is not None:
            sets.append("title=?")
            args.append(title)
        if description is not None:
            sets.append("description=?")
            args.append(description)
        if definition is not None:
            sets.append("draft_json=?")
            args.append(canonical(definition.model_dump(mode="json", by_alias=True)))
        n = self.store.execute(f"UPDATE forms SET {', '.join(sets)} WHERE id=? AND draft_rev=?",
                               (*args, form_id, draft_rev))
        if n != 1:
            raise ServiceError(409, "draft_conflict", "表单草稿已被其他人修改，请查看最新内容后再保存")
        call.touches("form", form_id)
        return self.form_json(self._form(call.workspace_id, form_id), with_versions=True, can_write=True)

    def publish(self, call: Call, form_id: str, *, draft_rev: int, note: str) -> dict:
        row = self._form(call.workspace_id, form_id)
        if row["status"] != "active":
            raise ServiceError(409, "form_archived", "表单已归档，恢复后才能发布")
        if row["draft_rev"] != draft_rev:
            raise ServiceError(409, "draft_conflict", "表单草稿已被其他人修改，请查看最新内容后再发布")
        defn = Definition.model_validate(_j(row["draft_json"], {}))
        found = problems(defn)
        if found:
            raise ServiceError(422, "form_not_publishable", "表单还不能发布，请先处理列出的问题", {"problems": found})
        payload = defn.model_dump(mode="json", by_alias=True)
        digest = fingerprint(payload)
        last = self.store.one("SELECT * FROM form_versions WHERE form_id=? ORDER BY version DESC LIMIT 1", (form_id,))
        if last and last["definition_hash"] == digest and last["title"] == row["title"] \
                and (last["description"] or "") == row["description"] and last["status"] == "published":
            # Nothing changed since the last version: publishing again would mint a version that
            # differs only in its number and move every new request onto it for no reason.
            raise ServiceError(409, "nothing_to_publish", "草稿与最新发布的版本相同，不需要再次发布",
                               {"version": last["version"]})
        if row["public_token_hash"] and not row["public_version"]:
            # The public link follows the latest version, so publishing puts this version in front
            # of everyone holding that link: content this call carried from other workspaces is
            # an outward send there, asked as one (docs/platform/data-flow.md §3, rule 8).
            for source in (call.delegation or {}).get("transfer_from") or ():
                if source != call.workspace_id:
                    data_flow.check(self.store, user_id=call.user_id, source=source, target=call.workspace_id,
                                    external=True)
        version = (last["version"] if last else 0) + 1
        ts = now()
        with self.store.tx() as t:
            t.execute("INSERT INTO form_versions (form_id,version,workspace_id,definition_json,definition_hash,title,"
                      "description,status,note,published_by,published_at) VALUES (?,?,?,?,?,?,?,'published',?,?,?)",
                      (form_id, version, call.workspace_id, canonical(payload), digest, row["title"],
                       row["description"], note, call.user_id, ts))
            t.execute("UPDATE forms SET published_version=?, updated_at=? WHERE id=?", (version, ts, form_id))
        call.touches("form", form_id)
        call.emit("forms.form.published", object_kind="form", object_id=form_id,
                  data={"form_id": form_id, "version": version, "definition_hash": digest})
        return {"form_id": form_id, "version": version, "definition_hash": digest}

    def retire_version(self, call: Call, form_id: str, version: int) -> dict:
        self._form(call.workspace_id, form_id)
        v = self._version(form_id, version)
        if v["status"] == "retired":
            return {"form_id": form_id, "version": version, "status": "retired"}
        self.store.execute("UPDATE form_versions SET status='retired', retired_at=? WHERE form_id=? AND version=?",
                           (now(), form_id, version))
        latest = self.store.one("SELECT MAX(version) AS v FROM form_versions WHERE form_id=? AND status='published'",
                                (form_id,))
        self.store.execute("UPDATE forms SET published_version=?, updated_at=? WHERE id=?",
                           (latest["v"] if latest else None, now(), form_id))
        call.touches("form", form_id)
        return {"form_id": form_id, "version": version, "status": "retired"}

    def set_archived(self, call: Call, form_id: str, archived: bool) -> dict:
        row = self._form(call.workspace_id, form_id)
        if archived and row["status"] == "active":
            self.store.execute("UPDATE forms SET status='archived', archived_at=?, updated_at=? WHERE id=?",
                               (now(), now(), form_id))
        elif not archived and row["status"] == "archived":
            self.store.execute("UPDATE forms SET status='active', archived_at=NULL, updated_at=? WHERE id=?",
                               (now(), form_id))
        call.touches("form", form_id)
        return self.form_json(self._form(call.workspace_id, form_id), can_write=True)

    def duplicate(self, call: Call, form_id: str, *, target_workspace_id: str | None, title: str | None) -> dict:
        """Copy the draft definition — and nothing else: no versions, no links, no requests, no
        submissions, no files. Into another workspace only where the caller can create forms and
        the source's data-flow policy lets its content go (synwevo_core.data_flow)."""
        row = self._form(call.workspace_id, form_id)
        target = target_workspace_id or call.workspace_id
        if target != call.workspace_id:
            self.caps.check_workspace(call.user_id, APP, target, "write")
            data_flow.check(self.store, user_id=call.user_id, source=call.workspace_id, target=target)
        elif not call.can("write"):
            call.require("write")
        defn = Definition.model_validate(_j(row["draft_json"], {}))
        fid = new_id("frm")
        ts = now()
        self.store.execute(
            "INSERT INTO forms (id,workspace_id,title,description,status,draft_json,draft_rev,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,'active',?,1,?,?,?)",
            (fid, target, title or row["title"], row["description"],
             canonical(defn.model_dump(mode="json", by_alias=True)), call.user_id, ts, ts))
        call.touches("form", fid)
        return {"form_id": fid, "workspace_id": target}

    # ============================================================== links
    def _mint(self) -> tuple[str, str, str]:
        token = secrets.token_urlsafe(24)
        return token, token_hash(token), sealed.seal(self.store, LINK_PURPOSE, token)

    def set_public_link(self, call: Call, form_id: str, *, enabled: bool, version: int | None) -> dict:
        row = self._form(call.workspace_id, form_id)
        if not enabled:
            with self.store.tx() as t:
                t.execute("UPDATE forms SET public_token_hash=NULL, public_token_sealed=NULL, updated_at=? WHERE id=?",
                          (now(), form_id))
                if row["public_request_id"]:
                    t.execute("UPDATE form_requests SET status='closed', closed_at=?, closed_reason='public_link_disabled', "
                              "updated_at=? WHERE id=? AND status='open'", (now(), now(), row["public_request_id"]))
            call.touches("form", form_id)
            return {"form_id": form_id, "enabled": False, "url": None}
        if row["status"] != "active":
            raise ServiceError(409, "form_archived", "表单已归档，不能开放公开填写")
        if not row["published_version"]:
            raise ServiceError(409, "form_not_published", "表单还没有发布的版本，发布后才能开放公开填写")
        if version is not None:
            v = self._version(form_id, version)
            if v["status"] != "published":
                raise ServiceError(409, "form_version_retired", "该版本已停用，不能用于公开填写")
        token, digest, seal_ = self._mint()
        ts = now()
        rid = row["public_request_id"]
        with self.store.tx() as t:
            if not rid or not t.execute("SELECT 1 FROM form_requests WHERE id=?", (rid,)).fetchone():
                rid = new_id("freq")
                t.execute("INSERT INTO form_requests (id,workspace_id,form_id,form_version,kind,title,submission_policy,status,"
                          "created_by,created_at,updated_at) VALUES (?,?,?,?,'public','','multiple','open',?,?,?)",
                          (rid, call.workspace_id, form_id, version or row["published_version"], call.user_id, ts, ts))
            else:
                t.execute("UPDATE form_requests SET status='open', closed_at=NULL, closed_reason=NULL, form_version=?, "
                          "updated_at=? WHERE id=?", (version or row["published_version"], ts, rid))
            t.execute("UPDATE forms SET public_token_hash=?, public_token_sealed=?, public_version=?, public_request_id=?, "
                      "updated_at=? WHERE id=?", (digest, seal_, version, rid, ts, form_id))
        call.touches("form", form_id)
        return {"form_id": form_id, "enabled": True, "url": fill_url(token), "version": version,
                "follows_latest": version is None}

    # ============================================================== requests
    def create_request(self, call: Call, form_id: str, *, version: int | None, recipient: dict,
                       prefill: dict, due_at: float | None, policy: str, require_sign_in: bool,
                       title: str | None) -> dict:
        row = self._form(call.workspace_id, form_id)
        if row["status"] != "active":
            raise ServiceError(409, "form_archived", "表单已归档，不能发出填写请求")
        version = version or row["published_version"]
        if not version:
            raise ServiceError(409, "form_not_published", "表单还没有发布的版本，发布后才能发出填写请求")
        v = self._version(form_id, version)
        if v["status"] != "published":
            raise ServiceError(409, "form_version_retired", "该版本已停用，不能用于新的填写请求")
        if due_at is not None and due_at <= time.time():
            raise ServiceError(400, "due_in_past", "截止时间必须晚于现在")
        defn = Definition.model_validate(_j(v["definition_json"], {}))
        if len(canonical(prefill).encode("utf-8")) > MAX_PREFILL_BYTES:
            raise ServiceError(413, "prefill_too_large", "预填内容过大")
        unknown = [k for k in prefill if k not in {f.id for f in defn.fields}]
        if unknown:
            raise ServiceError(422, "prefill_unknown_field", "预填里有表单没有的字段", {"fields": unknown[:20]})
        if any(f.type == "file" for f in defn.fields if f.id in prefill):
            raise ServiceError(422, "prefill_file_field", "附件字段不能预填")
        clean, errors = check_answers(defn, prefill, final=False)
        if errors:
            raise ServiceError(422, "prefill_invalid", "预填内容不符合表单要求", {"errors": errors})
        email = (recipient.get("email") or "").strip() or None
        name = (recipient.get("name") or "").strip() or None
        user_id = recipient.get("user_id")
        target_user = None
        if user_id:
            target_user = self.identity.get_user(user_id)
            if not target_user or target_user["status"] != "active":
                raise ServiceError(404, "recipient_not_found", "收件人账户不存在或不可用")
            email = email or target_user["email"]
        elif email:
            target_user = self.identity.by_email(email)
            if target_user and target_user["status"] != "active":
                target_user = None
        if require_sign_in and not email:
            raise ServiceError(400, "recipient_required", "要求登录填写时必须指定收件人的邮箱或账户")
        token, digest, seal_ = self._mint()
        rid = new_id("freq")
        ts = now()
        source = {"via": call.via, "run_id": call.run_id}
        if call.delegation:
            source["delegation"] = call.delegation
        self.store.execute(
            "INSERT INTO form_requests (id,workspace_id,form_id,form_version,kind,title,recipient_email,recipient_user_id,"
            "recipient_name,require_sign_in,token_hash,token_sealed,prefill_json,submission_policy,due_at,status,"
            "correlation_id,source_json,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,'personal',?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?,?)",
            (rid, call.workspace_id, form_id, version, title or row["title"], email,
             target_user["id"] if target_user else None, name, int(bool(require_sign_in)), digest, seal_,
             canonical(clean), policy, due_at, call.correlation_id, canonical(source), call.user_id, ts, ts))
        call.touches("form_request", rid)
        call.emit("forms.request.created", object_kind="form_request", object_id=rid,
                  data={"request_id": rid, "form_id": form_id, "version": version, "due_at": due_at,
                        "recipient_user_id": target_user["id"] if target_user else None})
        if target_user and target_user["id"] != call.user_id:
            requester = call.user.get("display_name") or call.user["email"]
            self.store.after_commit(lambda: self._notify(
                target_user["id"], "forms.request.assigned", {"requester": requester, "form": row["title"]},
                call.workspace_id, "form_request", rid, f"/forms/fill/{token}", actionable=True,
                dedupe=f"forms.request.assigned:{rid}"))
        return {"request_id": rid, "form_id": form_id, "version": version, "status": "open",
                "url": fill_url(token), "due_at": due_at}

    def _notify(self, user_id, kind, params, workspace_id, object_kind, object_id, path, *, actionable=False,
                dedupe=None):
        try:
            from synwevo_core.notifications import Notifier
            Notifier(self.store).emit(user_id, kind, params=params, workspace_id=workspace_id,
                                      object_kind=object_kind, object_id=object_id, dedupe_key=dedupe,
                                      target={"origin": "portal", "path": path}, actionable=actionable)
        except Exception:  # noqa: BLE001 — a notification is a courtesy, never part of the result
            log.exception("forms notification %s failed", kind)

    def _request(self, workspace_id: str, request_id: str) -> dict:
        row = self.store.one("SELECT * FROM form_requests WHERE id=? AND workspace_id=?", (request_id, workspace_id))
        if not row:
            raise ServiceError(404, "request_not_found", "填写请求不存在或不在该工作空间")
        return self._expire_if_due(row)

    def request_json(self, row: dict, *, can_write: bool) -> dict:
        out = {"id": row["id"], "workspace_id": row["workspace_id"], "form_id": row["form_id"],
               "form_version": row["form_version"], "kind": row["kind"], "title": row["title"],
               "recipient": {"email": row["recipient_email"], "name": row["recipient_name"],
                             "user_id": row["recipient_user_id"]} if row["kind"] == "personal" else None,
               "require_sign_in": bool(row["require_sign_in"]), "prefill": _j(row["prefill_json"], {}),
               "submission_policy": row["submission_policy"], "due_at": row["due_at"], "status": row["status"],
               "created_by": row["created_by"], "created_at": row["created_at"], "updated_at": row["updated_at"],
               "closed_at": row["closed_at"], "closed_reason": row["closed_reason"],
               "source": _j(row["source_json"], None), "url": None}
        if can_write and row["token_sealed"] and row["status"] not in ("revoked",):
            out["url"] = fill_url(sealed.unseal(self.store, LINK_PURPOSE, row["token_sealed"]))
        return out

    def list_requests(self, workspace_id: str, *, form_id: str | None, status: str | None, limit: int,
                      before: float | None, can_write: bool) -> dict:
        sql = "SELECT r.*, f.title AS form_title FROM form_requests r JOIN forms f ON f.id=r.form_id WHERE r.workspace_id=? AND r.kind='personal'"
        args: list = [workspace_id]
        if form_id:
            sql += " AND r.form_id=?"
            args.append(form_id)
        if status:
            sql += " AND r.status=?"
            args.append(status)
        if before is not None:
            sql += " AND r.created_at<?"
            args.append(before)
        rows = self.store.all(sql + " ORDER BY r.created_at DESC LIMIT ?", (*args, limit + 1))
        more = len(rows) > limit
        rows = [self._expire_if_due(r) | {"form_title": r["form_title"]} for r in rows[:limit]]
        return {"requests": [self.request_json(r, can_write=can_write) | {"form_title": r["form_title"]} for r in rows],
                "next_before": rows[-1]["created_at"] if more and rows else None}

    def get_request(self, workspace_id: str, request_id: str, *, can_write: bool) -> dict:
        row = self._request(workspace_id, request_id)
        subs = self.store.all("SELECT * FROM form_submissions WHERE request_id=? ORDER BY created_at DESC LIMIT 200",
                              (request_id,))
        out = self.request_json(row, can_write=can_write)
        out["submissions"] = [self.submission_summary(s) for s in subs]
        form = self._form(workspace_id, row["form_id"])
        out["form_title"] = form["title"]
        return out

    def close_request(self, call: Call, request_id: str, *, mode: str, reason: str) -> dict:
        row = self._request(call.workspace_id, request_id)
        if row["status"] in ("closed", "revoked", "expired", "accepted") and not (mode == "revoke" and row["status"] != "revoked"):
            return self.request_json(row, can_write=True)
        state = "revoked" if mode == "revoke" else "closed"
        with self.store.tx() as t:
            t.execute("UPDATE form_requests SET status=?, closed_at=?, closed_reason=?, updated_at=?"
                      + (", token_sealed=NULL" if state == "revoked" else "") + " WHERE id=?",
                      (state, now(), reason or mode, now(), request_id))
        call.touches("form_request", request_id)
        call.emit(f"forms.request.{state}", object_kind="form_request", object_id=request_id,
                  data={"request_id": request_id, "form_id": row["form_id"], "reason": reason or mode})
        return self.request_json(self._request(call.workspace_id, request_id), can_write=True)

    def extend_request(self, call: Call, request_id: str, due_at: float | None) -> dict:
        row = self._request(call.workspace_id, request_id)
        if row["status"] in ("revoked", "closed", "accepted"):
            raise ServiceError(409, "request_finished", "这份填写请求已经结束，不能再修改截止时间")
        if due_at is not None and due_at <= time.time():
            raise ServiceError(400, "due_in_past", "截止时间必须晚于现在")
        status = row["status"]
        if status == "expired":
            # Reopen: what the filler had submitted, if anything, decides where it resumes.
            had = self.store.one("SELECT status FROM form_submissions WHERE request_id=? AND status!='superseded' "
                                 "ORDER BY created_at DESC LIMIT 1", (request_id,))
            status = "changes_requested" if had and had["status"] == "changes_requested" else "open"
        self.store.execute("UPDATE form_requests SET due_at=?, status=?, updated_at=? WHERE id=?",
                           (due_at, status, now(), request_id))
        call.touches("form_request", request_id)
        return self.request_json(self._request(call.workspace_id, request_id), can_write=True)

    def reissue_link(self, call: Call, request_id: str) -> dict:
        row = self._request(call.workspace_id, request_id)
        if row["status"] in ("revoked", "closed", "accepted"):
            raise ServiceError(409, "request_finished", "这份填写请求已经结束，不能重新生成链接")
        token, digest, seal_ = self._mint()
        self.store.execute("UPDATE form_requests SET token_hash=?, token_sealed=?, updated_at=? WHERE id=?",
                           (digest, seal_, now(), request_id))
        call.touches("form_request", request_id)
        return {"request_id": request_id, "url": fill_url(token)}

    def _expire_if_due(self, row: dict) -> dict:
        """A request past its due time is expired, whenever anyone looks at it (and the sweeper
        does the same for the ones nobody looks at), with exactly one event."""
        if row["status"] in REQUEST_STATES_OPEN and row["due_at"] is not None and row["due_at"] <= time.time():
            with self.store.tx() as t:
                n = t.execute("UPDATE form_requests SET status='expired', updated_at=? WHERE id=? AND status IN ('open','changes_requested')",
                              (now(), row["id"])).rowcount
                if n:
                    ev = record_event(t, type_="forms.request.expired", app=APP, workspace_id=row["workspace_id"],
                                      object_kind="form_request", object_id=row["id"],
                                      data={"request_id": row["id"], "form_id": row["form_id"]},
                                      correlation_id=row["correlation_id"])
            if n and self.caps:
                self.caps.announce([ev])
            row = self.store.one("SELECT * FROM form_requests WHERE id=?", (row["id"],))
        return row

    def sweep_expired(self) -> int:
        with self._expire_lock:
            rows = self.store.all("SELECT * FROM form_requests WHERE status IN ('open','changes_requested') "
                                  "AND due_at IS NOT NULL AND due_at<=? LIMIT 500", (time.time(),))
            for r in rows:
                self._expire_if_due(r)
            return len(rows)

    # ============================================================== submissions
    def submission_summary(self, s: dict) -> dict:
        return {"id": s["id"], "request_id": s["request_id"], "form_id": s["form_id"], "form_version": s["form_version"],
                "revision": s["revision"], "status": s["status"], "submitted_by": s["submitted_by"],
                "submitter_label": s["submitter_label"], "created_at": s["created_at"],
                "reviewed_by": s["reviewed_by"], "reviewed_at": s["reviewed_at"], "review_note": s["review_note"]}

    def _submission(self, workspace_id: str, submission_id: str) -> dict:
        row = self.store.one("SELECT * FROM form_submissions WHERE id=? AND workspace_id=?", (submission_id, workspace_id))
        if not row:
            raise ServiceError(404, "submission_not_found", "提交记录不存在或不在该工作空间")
        return row

    def get_submission(self, workspace_id: str, submission_id: str) -> dict:
        s = self._submission(workspace_id, submission_id)
        v = self._version(s["form_id"], s["form_version"])
        defn = Definition.model_validate(_j(v["definition_json"], {}))
        files = self.store.all("SELECT id, field_id, filename, content_type, size, scan FROM form_files "
                               "WHERE submission_id=? ORDER BY created_at", (submission_id,))
        items = self.store.all("SELECT field_id, note, created_by, created_at FROM form_review_items "
                               "WHERE submission_id=? ORDER BY created_at", (submission_id,))
        history = self.store.all("SELECT id, revision, status, created_at FROM form_submissions WHERE request_id=? "
                                 "AND filler_key=? ORDER BY revision", (s["request_id"], s["filler_key"]))
        return {**self.submission_summary(s), "answers": _j(s["answers_json"], {}), "answers_hash": s["answers_hash"],
                "fields": _field_list(defn), "files": [{**f, "scan": _scan_label(f["scan"]),
                                                        "url": f"/api/suite/forms/files/{f['id']}?workspace_id={workspace_id}"}
                                                       for f in files],
                "review_items": items, "history": history}

    def list_submissions(self, workspace_id: str, *, form_id: str | None, request_id: str | None,
                         status: str | None, limit: int, before: float | None) -> dict:
        sql = "SELECT * FROM form_submissions WHERE workspace_id=?"
        args: list = [workspace_id]
        for col, val in (("form_id", form_id), ("request_id", request_id), ("status", status)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        if not status:
            sql += " AND status!='superseded'"
        if before is not None:
            sql += " AND created_at<?"
            args.append(before)
        rows = self.store.all(sql + " ORDER BY created_at DESC LIMIT ?", (*args, limit + 1))
        more = len(rows) > limit
        rows = rows[:limit]
        return {"submissions": [self.submission_summary(r) | {"answers": _j(r["answers_json"], {})} for r in rows],
                "next_before": rows[-1]["created_at"] if more and rows else None}

    def request_changes(self, call: Call, submission_id: str, *, note: str, items: list[dict]) -> dict:
        s = self._submission(call.workspace_id, submission_id)
        if s["status"] != "submitted":
            raise ServiceError(409, "submission_not_reviewable", "只有待确认的提交可以要求补充或修改",
                               {"status": s["status"]})
        req = self._request(call.workspace_id, s["request_id"])
        if req["status"] in ("revoked", "closed"):
            raise ServiceError(409, "request_finished", "这份填写请求已经结束，不能再要求补充")
        v = self._version(s["form_id"], s["form_version"])
        known = {f.id for f in Definition.model_validate(_j(v["definition_json"], {})).fields}
        for it in items:
            if it.get("field_id") and it["field_id"] not in known:
                raise ServiceError(422, "unknown_field", "补充要求里有表单没有的字段", {"field_id": it["field_id"]})
        ts = now()
        with self.store.tx() as t:
            n = t.execute("UPDATE form_submissions SET status='changes_requested', review_note=?, reviewed_by=?, reviewed_at=? "
                          "WHERE id=? AND status='submitted'", (note, call.user_id, ts, submission_id)).rowcount
            if n != 1:
                raise ServiceError(409, "submission_not_reviewable", "该提交刚刚被其他人处理过，请刷新")
            for it in items:
                t.execute("INSERT INTO form_review_items (id,submission_id,field_id,note,created_by,created_at) VALUES (?,?,?,?,?,?)",
                          (new_id("fri"), submission_id, it.get("field_id"), it["note"], call.user_id, ts))
            if req["kind"] == "personal":
                t.execute("UPDATE form_requests SET status='changes_requested', updated_at=? WHERE id=?", (ts, req["id"]))
        call.touches("form_submission", submission_id)
        call.emit("forms.submission.changes_requested", object_kind="form_submission", object_id=submission_id,
                  data={"submission_id": submission_id, "request_id": s["request_id"], "form_id": s["form_id"],
                        "fields": [it.get("field_id") for it in items if it.get("field_id")]})
        if s["submitted_by"] and s["submitted_by"] != call.user_id and req["token_sealed"]:
            # The submitter may be outside the workspace: name the version they answered, never
            # the live draft row.
            form = self._version(s["form_id"], s["form_version"])
            token = sealed.unseal(self.store, LINK_PURPOSE, req["token_sealed"])
            reviewer = call.user.get("display_name") or call.user["email"]
            self.store.after_commit(lambda: self._notify(
                s["submitted_by"], "forms.submission.changes_requested", {"form": form["title"], "reviewer": reviewer},
                call.workspace_id, "form_submission", submission_id, f"/forms/fill/{token}", actionable=True,
                dedupe=f"forms.changes:{submission_id}"))
        return self.get_submission(call.workspace_id, submission_id)

    def accept(self, call: Call, submission_id: str, *, note: str) -> dict:
        s = self._submission(call.workspace_id, submission_id)
        if s["status"] == "accepted":
            return self.get_submission(call.workspace_id, submission_id)
        if s["status"] != "submitted":
            raise ServiceError(409, "submission_not_reviewable", "只有待确认的提交可以确认", {"status": s["status"]})
        req = self._request(call.workspace_id, s["request_id"])
        ts = now()
        with self.store.tx() as t:
            n = t.execute("UPDATE form_submissions SET status='accepted', review_note=?, reviewed_by=?, reviewed_at=? "
                          "WHERE id=? AND status='submitted'", (note, call.user_id, ts, submission_id)).rowcount
            if n != 1:
                raise ServiceError(409, "submission_not_reviewable", "该提交刚刚被其他人处理过，请刷新")
            if req["kind"] == "personal" and req["submission_policy"] == "once":
                t.execute("UPDATE form_requests SET status='accepted', closed_at=?, updated_at=? WHERE id=?", (ts, ts, req["id"]))
        call.touches("form_submission", submission_id)
        call.emit("forms.submission.accepted", object_kind="form_submission", object_id=submission_id,
                  data={"submission_id": submission_id, "request_id": s["request_id"], "form_id": s["form_id"]})
        if s["submitted_by"] and s["submitted_by"] != call.user_id:
            form = self._version(s["form_id"], s["form_version"])
            reviewer = call.user.get("display_name") or call.user["email"]
            self.store.after_commit(lambda: self._notify(
                s["submitted_by"], "forms.submission.accepted", {"form": form["title"], "reviewer": reviewer},
                call.workspace_id, "form_submission", submission_id, "/forms", dedupe=f"forms.accepted:{submission_id}"))
        return self.get_submission(call.workspace_id, submission_id)


def _field_list(defn: Definition) -> list[dict]:
    out = []
    for f in defn.fields:
        item = {"id": f.id, "type": f.type, "label": f.label}
        if f.type in ("select", "multiselect"):
            item["options"] = [o.model_dump() for o in f.options]
        if f.type == "money":
            item["currency"] = f.currency
        if f.type == "repeat":
            item["fields"] = [{"id": s.id, "type": s.type, "label": s.label,
                               **({"options": [o.model_dump() for o in s.options]} if s.options else {})}
                              for s in f.fields]
        out.append(item)
    return out


def _scan_label(scan) -> str:
    """What the page may say about an attachment's malware check. `not_configured` is said as
    such — never as "checked"."""
    status = (scan or {}).get("status") if isinstance(scan, dict) else None
    return status if status in ("clean", "not_configured") else "unknown"
