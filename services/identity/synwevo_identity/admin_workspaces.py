"""A44: the platform's workspace lifecycle from the console — rename, soft delete with a seven-day
recovery, recover — through the confirmed-operation protocol of
`admin_organizations.OrganizationLifecycle`. The state machine, the journal entry and the
withdrawals are core's (`synwevo_core.workspace_lifecycle`); this module is the console's ceremony.

* `POST /api/admin/workspaces/{wid}/lifecycle/preview` `{"action": "rename"|"delete"|"recover",
  "name"?}` — `platform.workspaces.manage`, not dangerous, changes nothing, read in one deferred
  snapshot (no writer lock). Answers `{action, transition: {from, to}, target: WorkspaceRow, name,
  impact, notes, risk, expires_at, preview_token}`: the signed five-minute plan binds the actor, the
  act, the workspace, the normalised new name of a rename, the origin and a digest of the
  workspace's version (lifecycle columns, name, `updated_at`, its organization and that
  organization's status, and who its members are — by identity, not by count).
* `POST /api/admin/workspaces/{wid}/lifecycle/{action}` `{"reason", "idempotency_key",
  "preview_token", "name"?}` — dangerous: strict re-authentication before the body is read. In one
  write transaction: re-admission, the stored result of a repeated key, the state refusals again,
  the confirmation, the plan, its expiry, the version; then the platform's audit row
  (`admin.workspace.lifecycle`), core `platform_transition` — the organization's own record
  (`organization.workspace.<action>`, `by: platform`) when the workspace belongs to one, and for a
  deletion the journal entry with its receipt, the withdrawals and the row — and last the receipt
  in `admin_operations`. Answers `{ok, operation_id, state, action, workspace, withdrawn,
  restore_note}`.

The `lifecycle` segment keeps the acts clear of the parent's `/workspaces/{wid}/claim`, `/unclaim`,
`/freeze`, `/unfreeze` and A37's `/recover-owner`. The administrator gains no membership,
entitlement or token. The applications' rows (jobs, webhooks, Forms links, projects, organization
templates) are counted by SQL guarded on the table's existence, never by importing an application;
`null` means the table is absent.

What the counts mean, so that "ends for good" and "comes back" are never claimed for the same row:
`api_keys` / `service_accounts` are the credentials **not** bound to an API-platform approval (keys
by lineage) — paused, they come back with a recovery; a bound one ends with its approval
(`approvals`, `approvals_revoked_for_good`). `running_jobs` are the queued or running Docs jobs a
deletion halts — not operator work (no descriptor, or kind `operator`), which `authority.check`
lets run whatever the workspace's state. A recovery's notes say what the deletion ended and what
comes back, judged by this workspace's rows now (`_ended`).

A deletion that fails after its journal line was appended answers 503 `journal_unavailable` and
commits nothing here — but, write-ahead, a later restore applies the line (core
`workspace_lifecycle`), so the answer does not claim the act is void: re-preview and check.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

from fastapi import APIRouter, Body, Request

from synwevo_core import authority
from synwevo_core.audit_change import change
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import require_platform
from synwevo_core.store import Store, new_id, now
from synwevo_core.workspace_lifecycle import ACTIONS, check_action, lifecycle_values, platform_transition, row

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical
from .admin_organizations import IDEMPOTENCY_KEY, RUNNING_JOB_STATUSES

PERMISSION = "platform.workspaces.manage"
ACTION = "admin.workspace.lifecycle"
REASON_MIN, REASON_MAX = 8, 500
#: The 503 when the security journal fails during a deletion (the only act it records).
JOURNAL_UNAVAILABLE = "安全记录暂不可用，删除未完成；如果之后从备份还原，这次删除仍可能生效。请重新预览并核实工作空间状态后再试"
#: The principal Docs' job runner asks about (JobRunner._authorized): the current run's, else the job's.
_RUN_PRINCIPAL = ("COALESCE(NULLIF((SELECT r.principal FROM job_runs r WHERE r.job_id=jobs.id "
                  "AND r.run_no=COALESCE(jobs.run_no, 1)), ''), jobs.principal)")
#: Of a stored descriptor `p`: is it work `authority.check` refuses in a workspace that is not
#: runnable? Anything but operator work — no descriptor (jobs from before descriptors, a command
#: line) or kind `operator` — which it lets run whatever the workspace's state, so a deletion does
#: not halt it. A malformed descriptor is refused (`authority.loads` reads it as `invalid`).
_NOT_OPERATOR = ("(CASE WHEN p IS NULL OR p='' THEN 0 WHEN json_valid(p) THEN "
                 "(json_type(p)<>'object' OR json_extract(p, '$.kind') IS NOT 'operator') ELSE 1 END)")
#: The columns the plan's version binds, besides the members and the organization's status.
VERSION_COLUMNS = ("id", "name", "kind", "status", "deleted_at", "recover_until", "previous_status",
                   "organization_id", "updated_at")


def workspace_row(store: Store, w: dict) -> dict:
    """A workspace as the platform's lifecycle sees it (WorkspaceRow): lifecycle columns, its
    organization (`{id, name, status}`, or null) and the number of members — never content."""
    org = store.one("SELECT id, name, status FROM organizations WHERE id=?",
                    (w["organization_id"],)) if w.get("organization_id") else None
    return {"id": w["id"], "name": w["name"], "kind": w["kind"], "status": w["status"],
            "organization_id": w.get("organization_id"), "organization": org, "created_at": w["created_at"],
            "deleted_at": w.get("deleted_at"), "recover_until": w.get("recover_until"),
            "previous_status": w.get("previous_status"),
            "members": store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (w["id"],))["n"]}


class WorkspaceLifecycle(SignedPreview):
    action = ACTION

    def __init__(self, store: Store, ident: Identity, audit):
        self.store, self.ident, self.audit = store, ident, audit

    def _actor(self, request, *, execute=False):
        return require_platform(request, PERMISSION, store=self.store, audit=self.audit, dangerous=execute)

    def _table(self, name: str) -> bool:
        return bool(self.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    @contextmanager
    def snapshot(self):
        """One committed state for every statement of the preview, without the writer lock (the
        deferred read of admin_apps.AppGovernance.snapshot). A write inside would fail."""
        with self.store.conn() as c:
            if c.in_transaction:
                yield
                return
            c.execute("BEGIN DEFERRED")
            try:
                yield
            finally:
                if c.in_transaction:
                    c.execute("ROLLBACK")

    def _workspace(self, wid) -> dict:
        ws = row(self.store, wid)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        return ws

    def _count(self, sql: str, args: tuple) -> int:
        return self.store.one(sql, args)["n"]

    def _counted(self, table: str, sql: str, args: tuple):
        return self._count(sql, args) if self._table(table) else None

    def _impact(self, ws: dict) -> dict:
        """What a deletion would reach, counted with the predicates `workspace_lifecycle.withdraw`
        withdraws by (sessions, tokens, invitations, approvals) and the live gates refuse by (the
        rest). API keys and service accounts: only those not bound to an approval (keys counted by
        lineage); jobs: only those the deletion halts (not operator work). Counts only: never a
        name, an address or a document."""
        wid, t = ws["id"], now()
        bindings = self._table("api_platform_bindings")
        unbound_key = (" AND NOT EXISTS (SELECT 1 FROM api_platform_bindings b WHERE b.kind='api_key' "
                       "AND b.credential_id=COALESCE(api_keys.lineage, api_keys.id))") if bindings else ""
        unbound_client = (" AND NOT EXISTS (SELECT 1 FROM api_platform_bindings b WHERE b.kind='service_account' "
                          "AND b.credential_id=oauth_clients.client_id)") if bindings else ""
        principal = _RUN_PRINCIPAL if self._table("job_runs") else "jobs.principal"
        webhooks = None
        if self._table("webhook_subscriptions"):
            webhooks = self._count("SELECT COUNT(*) AS n FROM webhook_subscriptions WHERE tenant=? AND status='active' "
                                   "AND deleted_at IS NULL", (wid,))
        if self._table("suite_webhook_subscriptions"):
            webhooks = (webhooks or 0) + self._count("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions "
                                                     "WHERE workspace_id=? AND status='active'", (wid,))
        return {
            "members": self._count("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (wid,)),
            "usable_owners": len(self.ident.usable_owners(wid)),
            "sessions": self._count("SELECT COUNT(*) AS n FROM sessions WHERE workspace_id=? AND revoked_at IS NULL", (wid,)),
            "tokens": self._count("SELECT COUNT(*) AS n FROM oauth_tokens WHERE workspace_id=? AND revoked_at IS NULL", (wid,)),
            "invitations": self._count("SELECT COUNT(*) AS n FROM invitations WHERE workspace_id=? AND revoked_at IS NULL "
                                       "AND accepted_at IS NULL AND declined_at IS NULL", (wid,)),
            "approvals": self._counted("api_platform_approvals", "SELECT COUNT(*) AS n FROM api_platform_approvals "
                                       "WHERE workspace_id=? AND status IN ('pending','approved')", (wid,)) or 0,
            "api_keys": self._counted("api_keys", "SELECT COUNT(DISTINCT COALESCE(lineage, id)) AS n FROM api_keys "
                                      "WHERE tenant=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)"
                                      + unbound_key, (wid, t)),
            "service_accounts": self._counted("oauth_clients", "SELECT COUNT(*) AS n FROM oauth_clients WHERE kind='service' "
                                              "AND workspace_id=? AND disabled_at IS NULL" + unbound_client, (wid,)),
            "running_jobs": self._counted("jobs", f"SELECT COUNT(*) AS n FROM (SELECT {principal} AS p FROM jobs "
                                          "WHERE tenant=? AND status IN "
                                          f"({','.join('?' * len(RUNNING_JOB_STATUSES))})) WHERE {_NOT_OPERATOR}",
                                          (wid, *RUNNING_JOB_STATUSES)),
            "webhooks": webhooks,
            "form_links": self._counted("form_requests", "SELECT COUNT(*) AS n FROM form_requests WHERE workspace_id=? "
                                        "AND status='open'", (wid,)),
            "projects": self._counted("projects", "SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0", (wid,)),
            "org_templates": self._counted("organization_templates", "SELECT COUNT(*) AS n FROM organization_templates "
                                           "WHERE source_tenant=?", (wid,)),
        }

    def _organization_status(self, ws: dict):
        if not ws.get("organization_id"):
            return None
        org = self.store.one("SELECT status FROM organizations WHERE id=?", (ws["organization_id"],))
        return org["status"] if org else "missing"

    def _ended(self, raw, wid: str) -> bool:
        """Whether deferred work of the workspace held by this stored descriptor (a Docs job's run
        principal, a webhook subscription's creator) stays refused once the workspace is active
        again: the credential, the grant or the approval behind it has ended — what a deletion
        revokes — or the descriptor is not one `authority.check` accepts there. Operator work and
        a person's own work (judged by the membership, which a deletion keeps) are not."""
        desc = authority.loads(raw)
        if desc is None or desc.get("kind") in ("operator", "user"):
            return False
        kind = desc.get("kind")
        if desc.get("tenant") not in (None, wid) or kind not in ("api_key", "service_account", "delegated"):
            return True
        if kind == "api_key":
            lineage = desc.get("lineage") or desc.get("key_id")
            return not self.store.one("SELECT 1 FROM api_keys WHERE (id=? OR lineage=?) AND revoked_at IS NULL AND "
                                      "(expires_at IS NULL OR expires_at>?)", (lineage, lineage, now())) \
                or self._approval_ended("api_key", lineage)
        client = self.store.one("SELECT disabled_at FROM oauth_clients WHERE client_id=?", (desc.get("client_id"),))
        if not client or client["disabled_at"]:
            return True
        if kind == "service_account":
            return self._approval_ended("service_account", desc.get("client_id"))
        # A delegated grant: its newest token revoked other than by a refresh rotation
        # (authority._check_person, Capabilities.grant_scopes) — a deletion revokes them all.
        family = desc.get("family_id")
        last = self.store.one("SELECT revoked_at, revoked_reason FROM oauth_tokens WHERE (family_id=? OR id=?) "
                              "AND client_id=? AND user_id=? AND workspace_id=? ORDER BY issued_at DESC, "
                              "(revoked_at IS NULL) DESC LIMIT 1",
                              (family, family, desc.get("client_id"), desc.get("user_id"), wid)) if family else None
        return not last or bool(last["revoked_at"] and (last["revoked_reason"] or "") != "rotated")

    def _approval_ended(self, kind: str, credential_id) -> bool:
        """A credential bound to an approval that is no longer approved (api_platform.approval_failure)."""
        if not self._table("api_platform_bindings"):
            return False
        row = self.store.one("SELECT a.status, a.revoked_at FROM api_platform_bindings b JOIN api_platform_approvals a "
                             "ON a.id=b.approval_id WHERE b.kind=? AND b.credential_id=?", (kind, credential_id))
        return bool(row and (row["status"] != "approved" or row["revoked_at"]))

    def _ended_by_deletion(self, ws: dict) -> dict:
        """For a recovery: what the deletion ended for good and that the recovery does not give back,
        as counts — approvals it revoked (`revoked_at` at or after `deleted_at`; a journal replay
        revokes at the entry's own time, which is later), Docs jobs halted for their authority since
        `deleted_at` plus queued or running ones whose descriptor has ended (`_ended`), and webhook
        subscriptions (Docs and the business applications') disabled since `deleted_at` plus active
        ones whose creator has ended."""
        wid, since = ws["id"], ws.get("deleted_at") or 0
        out = {"approvals": 0, "jobs": 0, "webhooks": 0}
        if self._table("api_platform_approvals"):
            out["approvals"] = self._count("SELECT COUNT(*) AS n FROM api_platform_approvals WHERE workspace_id=? "
                                           "AND status='revoked' AND revoked_at>=?", (wid, since))
        if self._table("jobs"):
            principal = _RUN_PRINCIPAL if self._table("job_runs") else "jobs.principal"
            out["jobs"] = self._count("SELECT COUNT(*) AS n FROM jobs WHERE tenant=? AND status='cancelled' "
                                      "AND halt_reason LIKE 'authorization_revoked:%' AND finished_at>=?", (wid, since))
            out["jobs"] += sum(self._ended(r["p"], wid) for r in self.store.all(
                f"SELECT {principal} AS p FROM jobs WHERE tenant=? AND status IN "
                f"({','.join('?' * len(RUNNING_JOB_STATUSES))})", (wid, *RUNNING_JOB_STATUSES)))
        if self._table("webhook_subscriptions"):
            out["webhooks"] += self._count("SELECT COUNT(*) AS n FROM webhook_subscriptions WHERE tenant=? "
                                           "AND deleted_at IS NULL AND status='disabled' AND updated_at>=?", (wid, since))
            out["webhooks"] += sum(self._ended(r["created_by"], wid) for r in self.store.all(
                "SELECT created_by FROM webhook_subscriptions WHERE tenant=? AND deleted_at IS NULL AND status='active'",
                (wid,)))
        if self._table("suite_webhook_subscriptions"):
            out["webhooks"] += self._count("SELECT COUNT(*) AS n FROM suite_webhook_subscriptions WHERE workspace_id=? "
                                           "AND deleted_at IS NULL AND status='disabled' AND status_changed_at>=?",
                                           (wid, since))
            out["webhooks"] += sum(self._ended(r["created_by"], wid) for r in self.store.all(
                "SELECT created_by FROM suite_webhook_subscriptions WHERE workspace_id=? AND deleted_at IS NULL "
                "AND status='active'", (wid,)))
        return out

    @staticmethod
    def _notes(action: str, impact: dict, organization_status, ended: dict | None = None) -> list[str]:
        """The note codes true for this act and this data (the console words each). A recovery's
        notes say what its deletion ended for good (`ended`, from `_ended_by_deletion`) and what
        comes back with it (unbound credentials, Forms links; organization templates were never
        gone) — never the seven-day deadline, which the recovery itself ends."""
        if action == "rename":
            return ["rename_visible_everywhere", "rename_not_journalled"]
        if action == "recover":
            ended = ended or {}
            notes = ["data_kept"]
            for code, count in (("approvals_revoked_for_good", ended.get("approvals")),
                                ("jobs_halt_for_good", ended.get("jobs")),
                                ("webhooks_disabled_for_good", ended.get("webhooks")),
                                ("credentials_paused", (impact["api_keys"] or 0) + (impact["service_accounts"] or 0)),
                                ("forms_links_paused", impact["form_links"]),
                                ("org_templates_kept", impact["org_templates"])):
                if count:
                    notes.append(code)
            notes.append("recover_not_journalled")
            if organization_status not in (None, "active"):
                notes.append("recover_blocked_by_organization")
            if not impact["usable_owners"]:
                notes.append("recover_without_usable_owner")
            return notes
        notes = ["data_kept"]
        for code, count in (("sessions_moved", impact["sessions"]), ("tokens_revoked", impact["tokens"]),
                            ("invitations_revoked", impact["invitations"]),
                            ("approvals_revoked_for_good", impact["approvals"]),
                            ("jobs_halt_for_good", impact["running_jobs"]),
                            ("webhooks_disabled_for_good", impact["webhooks"]),
                            ("credentials_paused", (impact["api_keys"] or 0) + (impact["service_accounts"] or 0)),
                            ("forms_links_paused", impact["form_links"]),
                            ("org_templates_kept", impact["org_templates"])):
            if count:
                notes.append(code)
        notes.append("recover_within_7_days")
        return notes

    def _digest(self, ws: dict) -> str:
        """The workspace's version as the plan binds it. Membership changes do not touch the
        workspace row, so who is in it is bound by identity: one member leaving while another joins
        still makes the preview stale."""
        members = [[r["user_id"], r["role"]] for r in self.store.all(
            "SELECT user_id, role FROM memberships WHERE workspace_id=? ORDER BY user_id", (ws["id"],))]
        version = {**{column: ws.get(column) for column in VERSION_COLUMNS}, "members": members,
                   "organization_status": self._organization_status(ws)}
        return hashlib.sha256(canonical(version).encode()).hexdigest()

    def preview(self, request, wid: str, body: dict) -> dict:
        actor = self._actor(request)
        action, name = body.get("action"), body.get("name")
        try:
            with self.snapshot():
                transition = check_action(action)
                ws = self._workspace(wid)
                values = lifecycle_values(ws, transition, now(), name=name)
                impact = self._impact(ws)
                ended = self._ended_by_deletion(ws) if transition == "recover" else None
                notes = self._notes(transition, impact, self._organization_status(ws), ended)
                version = self._digest(ws)
                target = workspace_row(self.store, ws)
        except Exception as exc:
            self._failure(request, actor, wid, exc, phase="preview", transition=action)
            raise
        expires = time.time() + PREVIEW_SECONDS
        plan = {"v": 1, "actor": actor["id"], "action": self.action, "target": wid, "transition": transition,
                "name": values.get("name"), "origin": self._environment(request), "version": version,
                "expires_at": expires, "nonce": new_id("plan")}
        return {"action": transition, "transition": {"from": ws["status"], "to": values.get("status", ws["status"])},
                "target": target, "name": values.get("name"), "impact": impact, "notes": notes,
                "risk": "high" if transition == "delete" else "medium", "expires_at": expires,
                "preview_token": self._sign(plan)}

    def execute(self, request, wid: str, action: str, body: dict) -> dict:
        actor = self._actor(request, execute=True)
        raw_reason, raw_name = body.get("reason"), body.get("name")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        key, token = body.get("idempotency_key"), body.get("preview_token")
        origin = self._environment(request)
        request_hash = hashlib.sha256(canonical({
            "action": self.action, "transition": action, "reason": reason, "preview_token": token, "target": wid,
            "name": raw_name.strip() if isinstance(raw_name, str) else None, "origin": origin}).encode()).hexdigest()
        try:
            if not REASON_MIN <= len(reason) <= REASON_MAX:
                raise ServiceError(400, "missing_reason", f"请写明理由（{REASON_MIN} 至 {REASON_MAX} 个字）")
            with self.store.tx() as tx:
                actor = self._actor(request, execute=True)
                if isinstance(key, str):
                    previous = self.store.one("SELECT action,request_hash,result_json FROM admin_operations "
                                              "WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
                    if previous:
                        if previous["action"] != self.action or previous["request_hash"] != request_hash:
                            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                        return json.loads(previous["result_json"])
                transition = check_action(action)
                ws = self._workspace(wid)
                # The state refusals before the confirmation checks, as the organization lifecycle:
                # an act the row does not allow is refused as such, token or no token.
                values = lifecycle_values(ws, transition, now(), name=raw_name)
                if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key) or not isinstance(token, str):
                    raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "target": wid, "transition": transition,
                            "name": values.get("name"), "origin": origin}
                if any(plan.get(field) != value for field, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if plan.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                if plan.get("version") != self._digest(ws):
                    raise ServiceError(409, "admin_preview_stale", "工作空间或其成员已变化，请重新预览")
                impact = self._impact(ws)
                operation_id = new_id("ao")
                keys = ("name",) if transition == "rename" else ("status",)
                meta = {**change({k: ws[k] for k in keys}, {k: values[k] for k in keys}), "transition": transition,
                        "reason": reason, "operation_id": operation_id, "impact": impact, "environment": origin}
                if ws.get("organization_id"):
                    meta["organization_id"] = ws["organization_id"]
                # The platform record first; the organization's own record, the journal entry with its
                # receipt, the withdrawals and the row are core's, in that order; the receipt of the
                # operation last. Filed under the workspace it is about, never the actor's own.
                self.audit(request, self.action, actor_id=actor["id"], workspace_id=wid, workspace_given=True,
                           object_kind="workspace", object_id=wid, result="ok", meta=meta)
                try:
                    out = platform_transition(self.store, wid, actor["id"], transition, reason=reason, name=raw_name,
                                              audit=lambda event, **kw: self.audit(request, event, workspace_given=True, **kw))
                except OSError:
                    # The security journal could not append or fsync: this transaction rolls back. The
                    # line may already be written (the directory fsync comes after it), and a later
                    # restore applies a written line (write-ahead, as the organization withdrawals): the
                    # answer must not claim the deletion is void.
                    raise ServiceError(503, "journal_unavailable", JOURNAL_UNAVAILABLE) from None
                result = {"ok": True, "operation_id": operation_id, "state": "succeeded", "action": transition,
                          "workspace": workspace_row(self.store, out["workspace"]), "withdrawn": out["withdrawn"],
                          "restore_note": out["restore_note"]}
                tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                           "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (operation_id, actor["id"], self.action, wid, key, request_hash, canonical(result), time.time()))
                return result
        except Exception as exc:
            self._failure(request, actor, wid, exc, phase="execute", reason=reason, transition=action)
            raise

    def _failure(self, request, actor, wid, exc, *, phase, reason="", transition=None):
        """Outside the failed transaction. Only a stable code, the phase, a catalogued act, a valid
        reason and a workspace that exists are kept: invalid inputs and exception messages can hold
        arbitrary data (a refused new name is not recorded)."""
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "environment": self._environment(request)}
        if transition in ACTIONS:
            meta["transition"] = transition
        if REASON_MIN <= len(reason) <= REASON_MAX:
            meta["reason"] = reason
        known = wid if row(self.store, wid) else None
        self.audit(request, self.action, actor_id=actor["id"], workspace_id=known, workspace_given=True,
                   object_kind="workspace", object_id=known,
                   result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error", meta=meta)


def build_router(store: Store, ident: Identity, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin. `/lifecycle/preview` is
    declared before the generic `/lifecycle/{action}`, so a preview is never read as an act."""
    r = APIRouter(tags=["admin"])
    lifecycle = WorkspaceLifecycle(store, ident, audit)

    @r.post("/workspaces/{wid}/lifecycle/preview")
    def preview_workspace_lifecycle(request: Request, wid: str, body: dict = Body(...)):
        """`{"action": "rename"|"delete"|"recover", "name"?}` → the signed impact preview."""
        return lifecycle.preview(request, wid, body if isinstance(body, dict) else {})

    @r.post("/workspaces/{wid}/lifecycle/{action}")
    def govern_workspace_lifecycle(request: Request, wid: str, action: str, body: dict = Body(...)):
        """`{"reason", "preview_token", "idempotency_key", "name"?}` → the confirmed act, once."""
        return lifecycle.execute(request, wid, action, body if isinstance(body, dict) else {})

    return r
