"""Platform-scope organization lifecycle (A35): freeze, unfreeze, soft-delete and recover any
organization from the console, by a platform administrator who need not be a member.

Until now the console route delegated to core `Organizations.transition`, which demands
organization ownership: a platform administrator who was not an owner got 404, and an owner using
the console acted as an owner. From here on everyone in the console takes the platform path —
the confirmed-operation protocol of `admin_operations.AccountOperations`:

* **preview** (`platform.orgs.manage`, not dangerous): the organization as the platform sees it,
  the status the act would leave it in, and what it would withdraw — members, workspaces,
  unrevoked sessions and tokens in its workspaces, pending invitations, API approvals and the
  Docs jobs still running there — bound into a signed, five-minute plan that names the actor,
  the transition, the organization, the origin and a digest of its version;
* **execute** (`platform.orgs.manage`, dangerous: strict re-authentication before the body is
  read): the written reason, the preview token and an idempotency key; one transaction under the
  writer lock in which the admission, the organization's state and the version digest are checked
  again, then core `Organizations.platform_transition` — the owner's state machine, journal entry
  and revocations, with `"by": "platform"` in the organization's own audit record — the platform's
  audit row and the durable result. The same key with the same request returns that result and
  writes nothing.

The administrator gains no membership, workspace access, entitlement or token: the platform
freezes an organization the way it freezes a workspace, from outside. Counts of the applications'
rows are read by SQL guarded on the table's existence, never by importing an application.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from fastapi import APIRouter, Body, Request

from synwevo_core.audit_change import change
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.organizations import LIFECYCLE_ACTIONS, PLATFORM_VIEW_COLUMNS, Organizations, lifecycle_values
from synwevo_core.platform_access import require_platform
from synwevo_core.store import Store, new_id, now

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical

PERMISSION = "platform.orgs.manage"
#: The Docs jobs that are still doing work — `ACTIVE` in apps/docs/synwevo_docs/jobs.py: a job is
#: `queued` until a worker claims it, `running` while it holds the lease, `cancelling` after a
#: cancel request until the worker stops. Every other status (success, failed, cancelled, halted…)
#: is terminal. Read from the application's source, not imported from it: the account service may
#: not import an application.
RUNNING_JOB_STATUSES = ("queued", "running", "cancelling")
#: The organization's workspaces, as `Organizations._revoke_workspace_authority` scopes a freeze:
#: every workspace attached to it (a deleted workspace's sessions and tokens are withdrawn too).
WORKSPACE_SCOPE = "workspace_id IN (SELECT id FROM workspaces WHERE organization_id=?)"
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")


class OrganizationLifecycle(SignedPreview):
    action = "admin.organization.lifecycle"

    def __init__(self, store: Store, ident: Identity, audit):
        self.store, self.ident, self.audit = store, ident, audit

    def _actor(self, request, *, execute=False):
        return require_platform(request, PERMISSION, store=self.store, audit=self.audit, dangerous=execute)

    def _table(self, name: str) -> bool:
        return bool(self.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    @staticmethod
    def _transition(action) -> str:
        if action not in LIFECYCLE_ACTIONS:
            raise ServiceError(400, "bad_action", "请选择有效的组织生命周期操作", {"field": "action"})
        return action

    def _organization(self, oid: str) -> dict:
        org = self.store.one("SELECT * FROM organizations WHERE id=?", (oid,))
        if not org:
            raise ServiceError(404, "no_organization", "找不到该组织")
        return org

    @staticmethod
    def _view(org: dict) -> dict:
        return {column: org[column] for column in PLATFORM_VIEW_COLUMNS}

    def _count(self, sql: str, args: tuple) -> int:
        return self.store.one(sql, args)["n"]

    def _impact(self, oid: str) -> dict:
        """What the act would reach. Sessions, tokens, invitations and approvals are counted with
        the predicates `_revoke_workspace_authority` withdraws by, so a freeze's preview says what
        its result will say. Counts only: never a document, a project name or an address."""
        return {
            "members": self._count("SELECT COUNT(*) AS n FROM organization_memberships WHERE organization_id=?", (oid,)),
            "workspaces": self._count("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=? AND deleted_at IS NULL", (oid,)),
            "sessions": self._count(f"SELECT COUNT(*) AS n FROM sessions WHERE {WORKSPACE_SCOPE} AND revoked_at IS NULL", (oid,)),
            "tokens": self._count(f"SELECT COUNT(*) AS n FROM oauth_tokens WHERE {WORKSPACE_SCOPE} AND revoked_at IS NULL", (oid,)),
            "invitations": self._count(f"SELECT COUNT(*) AS n FROM invitations WHERE {WORKSPACE_SCOPE} "
                                       "AND revoked_at IS NULL AND accepted_at IS NULL", (oid,)),
            # API governance and the Docs job queue are separately composed; an absent table is
            # zero, an existing but incompatible one is an error, never silently skipped.
            "approvals": self._count(f"SELECT COUNT(*) AS n FROM api_platform_approvals WHERE {WORKSPACE_SCOPE} "
                                     "AND status IN ('pending','approved')", (oid,)) if self._table("api_platform_approvals") else 0,
            "running_jobs": self._count("SELECT COUNT(*) AS n FROM jobs WHERE tenant IN (SELECT id FROM workspaces "
                                        f"WHERE organization_id=?) AND status IN ({','.join('?' * len(RUNNING_JOB_STATUSES))})",
                                        (oid, *RUNNING_JOB_STATUSES)) if self._table("jobs") else 0,
        }

    def _digest(self, org: dict, impact: dict) -> str:
        """The organization's version as the plan binds it: its lifecycle columns and version,
        plus who and what is in it — by identity, not by count. Membership and workspace changes
        do not bump `organizations.version`, so one member leaving while another joins (or a
        workspace deleted while another is created) must still make the preview stale, as
        AccountOperations._snapshot binds row references rather than numbers."""
        oid = org["id"]
        members = [[row["user_id"], row["role"]] for row in self.store.all(
            "SELECT user_id, role FROM organization_memberships WHERE organization_id=? ORDER BY user_id", (oid,))]
        workspaces = [row["id"] for row in self.store.all(
            "SELECT id FROM workspaces WHERE organization_id=? AND deleted_at IS NULL ORDER BY id", (oid,))]
        version = {**{column: org[column] for column in PLATFORM_VIEW_COLUMNS},
                   "members": members, "workspaces": workspaces,
                   "counts": [impact["members"], impact["workspaces"]]}
        return hashlib.sha256(canonical(version).encode()).hexdigest()

    def preview(self, request, oid: str, action):
        actor = self._actor(request)
        try:
            with self.store.tx():
                actor = self._actor(request)
                transition = self._transition(action)
                org = self._organization(oid)
                result_status = lifecycle_values(org, transition, now())["status"]
                impact = self._impact(oid)
                expires = time.time() + PREVIEW_SECONDS
                plan = {"v": 1, "actor": actor["id"], "action": self.action, "target": oid,
                        "transition": transition, "origin": self._environment(request),
                        "version": self._digest(org, impact), "expires_at": expires, "nonce": new_id("plan")}
        except Exception as exc:
            self._failure(request, actor, oid, exc, phase="preview", transition=action)
            raise
        return {"action": self.action, "scope": "platform", "risk": "high", "transition": transition,
                "target": self._view(org), "result_status": result_status, "impact": impact,
                "expires_at": expires, "preview_token": self._sign(plan)}

    def execute(self, request, oid: str, action, body: dict):
        actor = self._actor(request, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        key, token = body.get("idempotency_key"), body.get("preview_token")
        origin = self._environment(request)
        request_hash = hashlib.sha256(canonical({"action": self.action, "transition": action, "reason": reason,
                                                "preview_token": token, "target": oid, "origin": origin}).encode()).hexdigest()
        try:
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            with self.store.tx() as tx:
                actor = self._actor(request, execute=True)
                if isinstance(key, str):
                    previous = self.store.one("SELECT action,request_hash,result_json FROM admin_operations "
                                              "WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
                    if previous:
                        if previous["action"] != self.action or previous["request_hash"] != request_hash:
                            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                        return json.loads(previous["result_json"])
                transition = self._transition(action)
                org = self._organization(oid)
                # The state refusal before the confirmation checks, as AccountOperations._target:
                # an act the status does not allow is refused as such, token or no token.
                target_status = lifecycle_values(org, transition, now())["status"]
                if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key) or not isinstance(token, str):
                    raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "target": oid,
                            "transition": transition, "origin": origin}
                if any(plan.get(field) != value for field, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if plan.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                impact = self._impact(oid)
                if plan.get("version") != self._digest(org, impact):
                    raise ServiceError(409, "admin_preview_stale", "组织或其成员已变化，请重新预览")
                operation_id = new_id("ao")
                # The platform record first, as AccountOperations keeps the security journal last:
                # everything that can still fail after the journal entry is durable widens the
                # window in which a restore replays an intent without its receipt. The target status
                # is the state machine's answer for the row read under this same lock.
                # workspace_given: a platform action on an organization belongs to no workspace — not
                # to the administrator's own session workspace.
                self.audit(request, self.action, actor_id=actor["id"], object_kind="organization", object_id=oid,
                           workspace_given=True, result="ok",
                           meta={**change({"status": org["status"]}, {"status": target_status}),
                                 "transition": transition, "reason": reason, "operation_id": operation_id,
                                 "impact": impact, "environment": origin})
                # The organization's own audit record (`organization.<action>`, with `by: platform`)
                # is written by core, before its journal entry.
                orgs = Organizations(self.store, self.ident,
                                     lambda event, **kw: self.audit(request, event, workspace_given=True, **kw))
                try:
                    out = orgs.platform_transition(oid, actor["id"], transition, reason=reason)
                except OSError:
                    # The security journal could not append or fsync: nothing is committed.
                    raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
                organization = out.pop("organization")
                result = {"ok": True, "operation_id": operation_id, "state": "succeeded", "transition": transition,
                          "organization": organization, **out}
                # The receipt of the operation: an insert into a table this transaction alone writes
                # for this (actor, key), checked above under the writer lock.
                tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                           "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (operation_id, actor["id"], self.action, oid, key, request_hash, canonical(result), time.time()))
                return result
        except Exception as exc:
            self._failure(request, actor, oid, exc, phase="execute", reason=reason, transition=action)
            raise

    def _failure(self, request, actor, oid, exc, *, phase, reason="", transition=None):
        # Outside the failed transaction. Invalid inputs and exception messages can contain
        # arbitrary data; audit only a stable code, phase, a catalogued transition and a validated
        # operation reason.
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "environment": self._environment(request)}
        if transition in LIFECYCLE_ACTIONS:
            meta["transition"] = transition
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        self.audit(request, self.action, actor_id=actor["id"], object_kind="organization", object_id=oid,
                   workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta=meta)


def build_router(store: Store, ident: Identity, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin, and included after
    the ownership-recovery router (whose `/organizations/{oid}/recover-owner` must stay ahead of
    the generic `{action}`) and before the parent declares its own routes. FastAPI matches in
    declaration order: `/organizations/{oid}/preview` is declared here first, so a preview is never
    read as the act "preview" (test_admin_organization_lifecycle proves both directions)."""
    r = APIRouter(tags=["admin"])
    lifecycle = OrganizationLifecycle(store, ident, audit)

    @r.post("/organizations/{oid}/preview")
    def preview_organization_lifecycle(request: Request, oid: str, body: dict = Body(...)):
        """`{"action": "freeze"|"unfreeze"|"delete"|"recover"}` → the signed impact preview."""
        return lifecycle.preview(request, oid, body.get("action") if isinstance(body, dict) else None)

    @r.post("/organizations/{oid}/{action}")
    def govern_organization(request: Request, oid: str, action: str, body: dict = Body(...)):
        """`{"reason", "preview_token", "idempotency_key"}` → the confirmed act, once."""
        return lifecycle.execute(request, oid, action, body if isinstance(body, dict) else {})

    return r
