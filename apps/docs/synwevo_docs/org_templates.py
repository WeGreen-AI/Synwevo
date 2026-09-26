"""Organization-shared templates (docs/platform/org-templates.md).

Publishing needs the same authority as editing the template (an editor of its workspace) and
an organization the workspace belongs to; every release is a frozen copy of the head version.
Anyone who is a member of the organization can list and use a release from any of the
organization's workspaces; "using" creates an ordinary template in the current workspace with
the release's file and fields and the origin recorded, so nothing downstream learns a new kind
of template. Deprecating hides a publication from the pickers and refuses new uses; existing
copies and the jobs made from them are untouched. Maintainers are the editors of the source
workspace and the organization's owners/admins.
"""
from __future__ import annotations

import json

from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import current_membership
from synwevo_core.store import new_id, now

MAX_DESCRIPTION = 500
EDITOR_ROLES = ("editor", "admin", "owner")


class OrgTemplates:
    def __init__(self, app, ident):
        self.app, self.ident = app, ident
        self.store = app.store

    # ------------------------------------------------------------------ context
    def organization_of(self, tenant: str) -> dict | None:
        """The active organization a workspace belongs to; None for personal workspaces."""
        return self.store.one("SELECT o.id, o.name, o.status FROM workspaces w JOIN organizations o ON o.id=w.organization_id "
                              "WHERE w.id=? AND w.deleted_at IS NULL", (tenant,))

    def _current_org(self) -> dict | None:
        return self.organization_of(current_tenant())

    def _org_member(self, oid: str, uid: str) -> dict:
        m = current_membership(self.store, oid, uid)
        if not m:
            raise ServiceError(404, "not_found", "找不到该组织共享模板")
        return m

    def _workspace_role(self, tenant: str, uid: str) -> str | None:
        r = self.store.one("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (tenant, uid))
        return r["role"] if r else None

    def _can_manage(self, pub: dict, uid: str, org_member: dict | None) -> bool:
        if self._workspace_role(pub["source_tenant"], uid) in EDITOR_ROLES:
            return True
        return bool(org_member and org_member["role"] in ("owner", "admin"))

    def _get(self, pid: str, uid: str) -> tuple[dict, dict]:
        """The publication and the caller's membership in its organization. The current
        workspace must belong to the same organization: a publication is not reachable from
        a personal workspace or from another organization's workspace."""
        pub = self.store.one("SELECT * FROM organization_templates WHERE id=?", (pid,))
        org = self._current_org()
        if not pub or not org or org["id"] != pub["organization_id"]:
            raise ServiceError(404, "not_found", "找不到该组织共享模板")
        return pub, self._org_member(pub["organization_id"], uid)

    # ------------------------------------------------------------------ reading
    def status_for_templates(self, tids: list[str]) -> dict[str, dict]:
        """Publication state of this workspace's templates, for the library list."""
        if not tids:
            return {}
        rows = self.store.all(f"SELECT * FROM organization_templates WHERE source_tenant=? AND template_id IN ({','.join('?' * len(tids))})",
                              (current_tenant(), *tids))
        return {r["template_id"]: {"id": r["id"], "release_no": r["release_no"], "version_no": r["version_no"], "deprecated": bool(r["deprecated_at"]),
                                   "updated_at": r["updated_at"], "use_count": r["use_count"]} for r in rows}

    def list(self, uid: str, *, include_deprecated: bool = False) -> dict:
        org = self._current_org()
        if not org:
            return {"organization": None, "items": []}
        member = self._org_member(org["id"], uid)
        rows = self.store.all("SELECT * FROM organization_templates WHERE organization_id=? " + ("" if include_deprecated else "AND deprecated_at IS NULL ")
                              + "ORDER BY deprecated_at IS NOT NULL, updated_at DESC", (org["id"],))
        return {"organization": {"id": org["id"], "name": org["name"]}, "items": [self._out(r, uid, member) for r in rows]}

    def get(self, pid: str, uid: str) -> dict:
        pub, member = self._get(pid, uid)
        out = self._out(pub, uid, member)
        out["releases"] = [{"release_no": r["release_no"], "version_no": r["version_no"], "note": r["note"], "released_at": r["released_at"],
                            "released_by": self._name(r["released_by"])}
                           for r in self.store.all("SELECT * FROM organization_template_releases WHERE publication_id=? ORDER BY release_no DESC LIMIT 50", (pid,))]
        return out

    def _out(self, r: dict, uid: str, member: dict | None) -> dict:
        ws = self.store.one("SELECT name FROM workspaces WHERE id=?", (r["source_tenant"],)) or {}
        source = self.store.one("SELECT head, deleted FROM templates WHERE id=?", (r["template_id"],))
        slots = r["slots"] if isinstance(r["slots"], list) else json.loads(r["slots"] or "[]")
        return {"id": r["id"], "organization_id": r["organization_id"], "name": r["name"], "description": r["description"], "format": r["format"], "kind": r["kind"],
                "slot_count": len(slots), "release_no": r["release_no"], "version_no": r["version_no"],
                "source_workspace": {"id": r["source_tenant"], "name": ws.get("name")}, "source_template_id": r["template_id"] if r["source_tenant"] == current_tenant() else None,
                "published_by": {"user_id": r["published_by"], "name": self._name(r["published_by"])}, "published_at": r["published_at"],
                "updated_by": ({"user_id": r["updated_by"], "name": self._name(r["updated_by"])} if r.get("updated_by") else None), "updated_at": r["updated_at"],
                "deprecated": bool(r["deprecated_at"]), "deprecated_at": r["deprecated_at"], "deprecation_note": r.get("deprecation_note"), "use_count": r["use_count"],
                "can_manage": self._can_manage(r, uid, member),
                # a newer head in the source workspace that has not been released yet (maintainers only)
                "source_ahead": bool(source and not source["deleted"] and source["head"] != r["version_no"]) if self._can_manage(r, uid, member) else None,
                "source_gone": bool(not source or source["deleted"])}

    def _name(self, uid: str) -> str:
        u = self.ident.get_user(uid) or {}
        return u.get("display_name") or (u.get("email") or "").split("@")[0] or "—"

    # ------------------------------------------------------------------ writing
    def _frozen(self, tid: str) -> tuple[dict, dict]:
        t = self.app.get_template(tid)
        v = self.app.get_version(tid, t["head"])
        return t, v

    def publish(self, tid: str, uid: str, description: str | None = None) -> dict:
        org = self._current_org()
        if not org:
            raise ServiceError(400, "no_organization", "个人空间的模板不能发布到组织；请在组织的工作空间里发布")
        if org["status"] != "active":
            raise ServiceError(409, "organization_unavailable", "该组织当前不可用")
        member = self._org_member(org["id"], uid)
        t, v = self._frozen(tid)
        if self.store.one("SELECT 1 AS x FROM organization_templates WHERE organization_id=? AND template_id=?", (org["id"], tid)):
            raise ServiceError(409, "already_published", "这个模板已经发布到组织；要发布新版本请用「更新发布」")
        pid, ts = new_id("ot"), now()
        info = t.get("info") or {}
        row = {"id": pid, "organization_id": org["id"], "source_tenant": current_tenant(), "template_id": tid, "name": t["name"],
               "description": (str(description).strip()[:MAX_DESCRIPTION] or None) if description else None, "format": t["format"], "kind": t["kind"],
               "release_no": 1, "version_no": v["version_no"], "working_blob": v["working_blob"], "slots": v["slots"] or [], "info": info,
               "published_by": uid, "published_at": ts, "updated_by": None, "updated_at": ts, "deprecated_at": None, "deprecated_by": None,
               "deprecation_note": None, "use_count": 0}
        self.store.insert("organization_templates", row)
        self.store.insert("organization_template_releases", {"publication_id": pid, "release_no": 1, "version_no": v["version_no"], "working_blob": v["working_blob"],
                                                             "slots": v["slots"] or [], "info": info, "note": v.get("note"), "released_by": uid, "released_at": ts})
        return self._out(self.store.one("SELECT * FROM organization_templates WHERE id=?", (pid,)), uid, member)

    def update(self, pid: str, uid: str, description: str | None = None, *, set_description: bool = False) -> dict:
        """A new release from the source template's current head (and/or a new description).
        Nothing changes for copies already made; the organization sees the new release."""
        pub, member = self._get(pid, uid)
        if not self._can_manage(pub, uid, member):
            raise ServiceError(403, "forbidden", "只有源工作空间的编辑者或组织管理员可以维护这个共享模板")
        source = self.store.one("SELECT * FROM templates WHERE id=? AND deleted=0", (pub["template_id"],))
        changes: dict = {"updated_by": uid, "updated_at": now()}
        if set_description:
            changes["description"] = (str(description).strip()[:MAX_DESCRIPTION] or None) if description else None
        released = False
        if source and source["head"] != pub["version_no"]:
            v = self.app.get_version(source["id"], source["head"])
            no = pub["release_no"] + 1
            changes.update({"release_no": no, "version_no": v["version_no"], "working_blob": v["working_blob"], "slots": v["slots"] or [], "info": source.get("info") or {},
                            "name": source["name"], "format": source["format"], "kind": source["kind"]})
            self.store.insert("organization_template_releases", {"publication_id": pid, "release_no": no, "version_no": v["version_no"], "working_blob": v["working_blob"],
                                                                 "slots": v["slots"] or [], "info": source.get("info") or {}, "note": v.get("note"), "released_by": uid,
                                                                 "released_at": changes["updated_at"]})
            released = True
        elif not source and not set_description:
            raise ServiceError(409, "source_gone", "源模板已删除，不能再发布新版本；现有发布仍可使用")
        elif not set_description:
            raise ServiceError(409, "unchanged", "源模板没有新版本，无需更新发布")
        self.store.update("organization_templates", {"id": pid}, changes)
        out = self._out(self.store.one("SELECT * FROM organization_templates WHERE id=?", (pid,)), uid, member)
        out["released"] = released
        return out

    def set_deprecated(self, pid: str, uid: str, deprecated: bool, note: str | None = None) -> dict:
        pub, member = self._get(pid, uid)
        if not self._can_manage(pub, uid, member):
            raise ServiceError(403, "forbidden", "只有源工作空间的编辑者或组织管理员可以维护这个共享模板")
        if bool(pub["deprecated_at"]) == deprecated:
            raise ServiceError(409, "unchanged", "已经是这个状态")
        self.store.update("organization_templates", {"id": pid}, {"deprecated_at": now() if deprecated else None, "deprecated_by": uid if deprecated else None,
                                                                   "deprecation_note": (str(note).strip()[:MAX_DESCRIPTION] or None) if deprecated and note else None,
                                                                   "updated_by": uid, "updated_at": now()})
        return self._out(self.store.one("SELECT * FROM organization_templates WHERE id=?", (pid,)), uid, member)

    def use(self, pid: str, uid: str, name: str | None = None) -> dict:
        """A copy of the current release as a template of the current workspace."""
        pub, _ = self._get(pid, uid)
        if pub["deprecated_at"]:
            raise ServiceError(409, "deprecated", "这个共享模板已弃用，不能再用于新项目")
        tid, ts = new_id("t"), now()
        slots = pub["slots"] if isinstance(pub["slots"], list) else json.loads(pub["slots"] or "[]")
        info = dict(pub.get("info") or {})
        info["origin"] = {"publication_id": pub["id"], "organization_id": pub["organization_id"], "release_no": pub["release_no"], "version_no": pub["version_no"],
                          "name": pub["name"], "used_at": ts}
        self.store.insert("templates", {"id": tid, "tenant": current_tenant(), "name": (name or pub["name"])[:100], "kind": pub["kind"] or "uploaded", "format": pub["format"],
                                        "original_blob": pub["working_blob"], "original_name": pub["name"], "head": 1, "created_at": ts, "updated_at": ts, "info": info})
        self.store.insert("template_versions", {"template_id": tid, "version_no": 1, "parent_no": None, "working_blob": pub["working_blob"], "slots": slots,
                                                "note": f"来自组织共享模板「{pub['name']}」第 {pub['release_no']} 次发布", "created_at": ts})
        self.store.execute("UPDATE organization_templates SET use_count=use_count+1 WHERE id=?", (pid,))
        return self.app.template_detail(tid)
