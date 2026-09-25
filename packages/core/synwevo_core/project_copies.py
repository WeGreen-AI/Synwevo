"""Copying a personal Synwevo Docs project into an organization workspace, with the target's
approval (decision record in docs/platform/project-copy.md).

Who may do what:

* **The source owner asks.** Only the owner of the personal workspace the project lives in can
  request a copy, and only into an organization workspace they can already write to. The
  request carries an immutable manifest of exactly what would be copied (template, mapping
  revision, source files by name/size/hash, counts) and a digest of the source state; nothing in
  it is a file body, and the approver never gets to browse the personal workspace.
* **The target decides.** An organization member holding `org.approvals.decide` who is also an
  admin or owner of the target workspace approves or rejects, with a reason. Platform
  administrators get nothing from their platform role here. The requester may decide their own
  request only when they hold that authority themselves; it is recorded as a self-approval.
* **Execution re-checks everything.** Synwevo Docs runs approved copies: the request must still be
  approved and unexpired, the requester must still own the source and still be able to write to
  the target, the decider must still hold the deciding authority, and the source must still match
  the digest — otherwise the copy is marked `stale` and has to be asked for again. Secrets,
  credentials, jobs, artifacts, webhooks and grants are never copied.
* The account service owns the decision routes; the Docs application owns request creation and
  execution. Both go through this module, so there is one state machine.
"""
from __future__ import annotations

import json

from .errors import ServiceError
from .identity import Identity, ROLE_RANK
from .org_roles import OrgRoles
from .organizations import current_membership, organization_for_workspace
from .store import Store, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_copies (
  id TEXT PRIMARY KEY, source_workspace_id TEXT NOT NULL, source_project_id TEXT NOT NULL, requester_id TEXT NOT NULL,
  target_organization_id TEXT NOT NULL, target_workspace_id TEXT NOT NULL,
  name TEXT NOT NULL, purpose TEXT, scope TEXT NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL,
  status TEXT NOT NULL, decided_by TEXT, decided_at REAL, decision_reason TEXT, self_approved INTEGER NOT NULL DEFAULT 0,
  approval_expires_at REAL, executed_at REAL, result TEXT, error TEXT, version INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_project_copies_target ON project_copies(target_organization_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_project_copies_requester ON project_copies(requester_id, created_at);
"""

STATUSES = ("pending", "approved", "rejected", "withdrawn", "expired", "running", "done", "failed", "stale")
PENDING_TTL_S = 7 * 24 * 3600          # a request nobody decides on expires
APPROVAL_TTL_S = 7 * 24 * 3600         # an approval that was never executed expires
SCOPE_KEYS = ("sources", "corrections")


def _load(v):
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


class ProjectCopies:
    def __init__(self, store: Store, ident: Identity):
        self.store, self.ident = store, ident

    # ------------------------------------------------------------------ reading
    def _out(self, r: dict | None) -> dict | None:
        if not r:
            return None
        d = dict(r)
        for k in ("scope", "manifest", "result"):
            d[k] = _load(d.get(k))
        d["self_approved"] = bool(d.get("self_approved"))
        return d

    def get(self, cid: str) -> dict | None:
        return self._out(self.store.one("SELECT * FROM project_copies WHERE id=?", (cid,)))

    def public(self, r: dict, *, for_requester: bool = False) -> dict:
        d = {k: r.get(k) for k in ("id", "source_project_id", "target_organization_id", "target_workspace_id", "name", "purpose", "scope",
                                    "manifest", "digest", "status", "decided_at", "decision_reason", "self_approved", "approval_expires_at",
                                    "executed_at", "result", "error", "version", "created_at", "updated_at", "expires_at")}
        d["requester"] = self._who(r["requester_id"])
        d["decider"] = self._who(r["decided_by"]) if r.get("decided_by") else None
        if not for_requester:
            d["source_workspace_id"] = r["source_workspace_id"]
        return d

    def _who(self, uid: str | None) -> dict | None:
        u = self.ident.get_user(uid) if uid else None
        return {"user_id": uid, "name": (u or {}).get("display_name") or ((u or {}).get("email") or "").split("@")[0] or "—"} if u else None

    # ------------------------------------------------------------------ who may decide
    def can_decide(self, oid: str, wid: str, uid: str) -> bool:
        """Holding `org.approvals.decide` is not enough: the decider must also administer the
        target workspace — organization management does not read or accept content on its own."""
        if not current_membership(self.store, oid, uid):
            return False
        if not OrgRoles(self.store).has(oid, uid, "org.approvals.decide"):
            return False
        m = self.ident.membership(wid, uid)
        return bool(m and m["role"] in ("admin", "owner"))

    def deciders(self, oid: str, wid: str) -> list[str]:
        rows = self.store.all("SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role IN ('admin','owner') "
                              "AND u.status='active' AND u.deleted_at IS NULL", (wid,))
        return [r["user_id"] for r in rows if self.can_decide(oid, wid, r["user_id"])]

    def requester_may_target(self, uid: str, wid: str) -> dict:
        """The target must be an active organization workspace the requester can write to."""
        ws = self.ident.get_workspace(wid)
        if not ws or ws.get("deleted_at") or ws["status"] != "active" or ws.get("kind") != "org" or not ws.get("organization_id"):
            raise ServiceError(404, "no_workspace", "找不到可作为目标的组织工作空间")
        m = self.ident.membership(wid, uid)
        if not m or ROLE_RANK.get(m["role"], -1) < ROLE_RANK["editor"]:
            raise ServiceError(403, "target_write_required", "你需要是目标工作空间的编辑成员（或以上）才能把项目复制过去")
        if not current_membership(self.store, ws["organization_id"], uid):
            raise ServiceError(403, "target_write_required", "你不是目标组织的成员")
        return ws

    # ------------------------------------------------------------------ lifecycle
    def create(self, *, requester_id: str, source_workspace_id: str, source_project_id: str, target_workspace_id: str,
               name: str, purpose: str | None, scope: dict, manifest: dict, digest: str) -> dict:
        src = self.ident.get_workspace(source_workspace_id)
        if not src or src.get("kind") != "personal":
            raise ServiceError(409, "source_not_personal", "只有个人空间里的项目可以申请复制到组织工作空间")
        sm = self.ident.membership(source_workspace_id, requester_id)
        if not sm or sm["role"] != "owner":
            raise ServiceError(403, "source_owner_required", "只有项目所在个人空间的所有者可以申请复制")
        ws = self.requester_may_target(requester_id, target_workspace_id)
        name = (name or "").strip()[:100]
        if not name:
            raise ServiceError(400, "invalid_field", "请为副本命名", {"field": "name"})
        clean_scope = {k: bool((scope or {}).get(k)) for k in SCOPE_KEYS}
        open_ = self.store.one("SELECT id FROM project_copies WHERE source_project_id=? AND target_workspace_id=? AND status IN ('pending','approved','running')",
                               (source_project_id, target_workspace_id))
        if open_:
            raise ServiceError(409, "copy_already_requested", "这个项目已有一个未完成的复制申请指向该工作空间", {"copy_id": open_["id"]})
        ts = now()
        cid = new_id("pc")
        self.store.insert("project_copies", {
            "id": cid, "source_workspace_id": source_workspace_id, "source_project_id": source_project_id, "requester_id": requester_id,
            "target_organization_id": ws["organization_id"], "target_workspace_id": target_workspace_id, "name": name,
            "purpose": (purpose or "").strip()[:500] or None, "scope": json.dumps(clean_scope), "manifest": json.dumps(manifest, ensure_ascii=False),
            "digest": digest, "status": "pending", "created_at": ts, "updated_at": ts, "expires_at": ts + PENDING_TTL_S})
        return self.get(cid)

    def list_for_organization(self, oid: str, *, status: str | None = None, limit: int = 50) -> list[dict]:
        where, args = "target_organization_id=?", [oid]
        if status:
            where += " AND status=?"; args.append(status)
        args.append(limit)
        return [self._out(r) for r in self.store.all(f"SELECT * FROM project_copies WHERE {where} ORDER BY created_at DESC LIMIT ?", tuple(args))]

    def list_for_requester(self, uid: str, *, source_project_id: str | None = None, limit: int = 50) -> list[dict]:
        where, args = "requester_id=?", [uid]
        if source_project_id:
            where += " AND source_project_id=?"; args.append(source_project_id)
        args.append(limit)
        return [self._out(r) for r in self.store.all(f"SELECT * FROM project_copies WHERE {where} ORDER BY created_at DESC LIMIT ?", tuple(args))]

    def decide(self, cid: str, uid: str, action: str, *, reason: str, version: int | None = None) -> dict:
        if action not in ("approve", "reject"):
            raise ServiceError(400, "invalid_field", "action 只能是 approve 或 reject", {"field": "action"})
        reason = (reason or "").strip()
        if len(reason) < 4:
            raise ServiceError(400, "reason_required", "请写明批准或拒绝的理由（至少 4 个字符）", {"field": "reason"})
        with self.store.tx() as t:
            r = t.execute("SELECT * FROM project_copies WHERE id=?", (cid,)).fetchone()
            if not r:
                raise ServiceError(404, "no_copy_request", "找不到该复制申请")
            r = dict(r)
            if not self.can_decide(r["target_organization_id"], r["target_workspace_id"], uid):
                raise ServiceError(403, "forbidden", "只有持有审批权限且管理目标工作空间的成员可以决定")
            if r["status"] != "pending":
                raise ServiceError(409, "not_pending", "这个申请已经处理过了", {"status": r["status"]})
            if r["expires_at"] <= now():
                t.execute("UPDATE project_copies SET status='expired', updated_at=? WHERE id=?", (now(), cid))
                raise ServiceError(409, "not_pending", "这个申请已过期，请让申请人重新提交", {"status": "expired"})
            if version is not None and version != r["version"]:
                raise ServiceError(409, "version_conflict", "申请已更新，请刷新后再决定", {"version": r["version"]})
            ts = now()
            status = "approved" if action == "approve" else "rejected"
            t.execute("UPDATE project_copies SET status=?, decided_by=?, decided_at=?, decision_reason=?, self_approved=?, approval_expires_at=?, "
                      "updated_at=?, version=version+1 WHERE id=?",
                      (status, uid, ts, reason[:500], 1 if uid == r["requester_id"] else 0, ts + APPROVAL_TTL_S if action == "approve" else None, ts, cid))
        return self.get(cid)

    def withdraw(self, cid: str, uid: str) -> dict:
        with self.store.tx() as t:
            r = t.execute("SELECT * FROM project_copies WHERE id=?", (cid,)).fetchone()
            if not r or r["requester_id"] != uid:
                raise ServiceError(404, "no_copy_request", "找不到该复制申请")
            if r["status"] not in ("pending", "approved"):
                raise ServiceError(409, "not_withdrawable", "只有待审批或已批准未执行的申请可以撤回", {"status": r["status"]})
            t.execute("UPDATE project_copies SET status='withdrawn', updated_at=?, version=version+1 WHERE id=?", (now(), cid))
        return self.get(cid)

    # ------------------------------------------------------------------ execution bookkeeping (Docs owns the work)
    def claim_approved(self, cid: str) -> dict | None:
        """Move one approved request to `running`; None when someone else got it or it is not
        approved any more. Execution-time authority checks happen in the caller, before this."""
        with self.store.tx() as t:
            n = t.execute("UPDATE project_copies SET status='running', updated_at=? WHERE id=? AND status='approved' AND approval_expires_at>?",
                          (now(), cid, now())).rowcount
        return self.get(cid) if n else None

    def next_approved(self) -> list[dict]:
        return [self._out(r) for r in self.store.all("SELECT * FROM project_copies WHERE status='approved' AND approval_expires_at>? ORDER BY decided_at LIMIT 10", (now(),))]

    def finish(self, cid: str, status: str, *, result: dict | None = None, error: str | None = None) -> dict:
        if status not in ("done", "failed", "stale"):
            raise ValueError(status)
        self.store.execute("UPDATE project_copies SET status=?, result=?, error=?, executed_at=?, updated_at=?, version=version+1 WHERE id=?",
                           (status, json.dumps(result, ensure_ascii=False) if result is not None else None, (error or None), now() if status == "done" else None, now(), cid))
        return self.get(cid)

    def reset_stale(self, cid: str, *, error: str) -> dict:
        """An approval whose preconditions no longer hold (source changed, authority gone)."""
        return self.finish(cid, "stale", error=error)

    def sweep(self) -> int:
        ts = now()
        n = self.store.execute("UPDATE project_copies SET status='expired', updated_at=? WHERE status='pending' AND expires_at<=?", (ts, ts))
        n += self.store.execute("UPDATE project_copies SET status='expired', updated_at=? WHERE status='approved' AND approval_expires_at<=?", (ts, ts))
        return n

    def preconditions(self, r: dict) -> str | None:
        """Why an approved request may not run now, or None. Re-run right before copying."""
        if r["status"] != "approved":
            return f"status:{r['status']}"
        if (r.get("approval_expires_at") or 0) <= now():
            return "approval_expired"
        src = self.ident.get_workspace(r["source_workspace_id"])
        sm = self.ident.membership(r["source_workspace_id"], r["requester_id"])
        if not src or src.get("kind") != "personal" or not sm or sm["role"] != "owner":
            return "source_authority_lost"
        try:
            self.requester_may_target(r["requester_id"], r["target_workspace_id"])
        except ServiceError as e:
            return f"target:{e.code}"
        if not r.get("decided_by") or not self.can_decide(r["target_organization_id"], r["target_workspace_id"], r["decided_by"]):
            return "decider_authority_lost"
        org = self.store.one("SELECT status FROM organizations WHERE id=?", (r["target_organization_id"],))
        if not org or org["status"] != "active":
            return "organization_unavailable"
        return None
