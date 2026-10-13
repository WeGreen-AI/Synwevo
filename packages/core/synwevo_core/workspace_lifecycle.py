"""The platform's workspace lifecycle (A44): rename, soft delete with a seven-day recovery, recover.

The state machine of `organizations.lifecycle_values` / `Organizations._transition` /
`Organizations.platform_transition`, for one workspace, so a later owner path can share it. No new
status: a deleted workspace is `status='deleted'` with `deleted_at` set — the two are written
together here, the one writer, and nowhere else (no trigger: existing fixtures write either column
alone, and a CHECK would need a table rebuild). Every gate already refuses a workspace that is not
`active`, and `Identity.get_workspace` hides one whose `deleted_at` is set, so a deleted workspace is
unreachable everywhere — the switcher, organization lists, the session gate, machine credentials —
and hidden from the platform's default list.

* **rename** — `active` or `frozen`; organization and personal workspaces. Not journalled (a restore
  from an older backup brings the old name back: `rename_not_journalled`).
* **delete** — `active` or `frozen` → `deleted`, organization workspaces only (a personal workspace
  deleted would make `Identity.ensure_personal_workspace` create a second one). In the same
  transaction, and never given back by a recovery: sessions are moved off the workspace (as a
  freeze moves them), its OAuth tokens revoked (`workspace_deleted`), unused authorization codes
  spent, pending invitations revoked, API-platform approvals (pending or approved) revoked and the
  credentials bound to them ended as `api_platform.revoke_approval` ends them (the key's whole
  lineage revoked, the service account disabled and its tokens revoked) — a bound credential can
  never work again without its approval, so it is not left stored as if it would. What stays
  stored and is refused live, and comes back with a recovery: memberships, entitlements, the
  workspace's applications, legacy (unbound) API keys and service accounts, Forms links. What ends
  for good at its next check: queued or running Docs jobs of anyone but the operator (halted),
  Docs webhook subscriptions (disabled at their next delivery), the business applications'
  webhooks (their grant was revoked). Written ahead to the security journal (`workspace_deleted`)
  with its receipt in `meta`. An attempt that fails after the line was appended — the directory
  fsync, or the SQL or COMMIT behind it — rolls back here, and its rollback voids the line once a
  later committed transaction has found no receipt (journal._intent), so no restore applies it.
  Only an attempt the process did not survive (stopped before its COMMIT or before the void) stays
  an intent without a receipt that a later restore applies (fail-closed, as the organization
  withdrawals): the workspace is deleted again in the restored copy, with that attempt's clock.
* **recover** — `deleted` and before `recover_until` → the status it had before (`previous_status`;
  frozen when unknown). Not journalled (`recover_not_journalled`): restoring a backup taken before
  the deletion deletes it again, with the original clock; a freeze journalled after the recovery
  is then kept for the restored copy's recovery (journal._replay_freeze).

Legacy (the historical workspace, `pending_claim` or claimed) has its own lifecycle — claim and
unclaim — and refuses every act here (`workspace_legacy`). Purge and moving a workspace between
organizations do not exist.
"""
from __future__ import annotations

from .audit_change import change
from .errors import ServiceError
from .journal import WORKSPACE_DELETED_KIND, record_workspace
from .organizations import RECOVERY_SECONDS, _name, audit_reason
from .store import Store, new_id, now

ACTIONS = ("rename", "delete", "recover")
#: The states a deleted workspace may have come from, and that a recovery returns to.
RECOVERABLE_STATES = ("active", "frozen")
#: Store's unconditional ADD COLUMN loop (no CHECK change, no table rebuild); `deleted_at` exists.
COLUMNS = [("workspaces", "previous_status", "TEXT"), ("workspaces", "recover_until", "REAL")]
#: Why a deletion revoked a token (`oauth_tokens.revoked_reason`).
WITHDRAWAL_REASON = "workspace_deleted"
#: The acts the security journal does not record: a restore from a backup taken before them undoes
#: them (the code the result carries, as the freeze route's UNFREEZE_RESTORE_NOTE).
RESTORE_NOTES = {"rename": "rename_not_journalled", "recover": "recover_not_journalled"}


def check_action(action) -> str:
    """400 `bad_action` outside ACTIONS."""
    if action not in ACTIONS:
        raise ServiceError(400, "bad_action", "请选择有效的工作空间操作", {"field": "action"})
    return action


def row(store: Store, wid) -> dict | None:
    """The workspace row as stored, a deleted one included (`Identity.get_workspace` hides it)."""
    if not isinstance(wid, str) or not wid or len(wid) > 128:
        return None
    return store.one("SELECT * FROM workspaces WHERE id=?", (wid,))


def is_legacy(ws: dict) -> bool:
    return ws["kind"] == "legacy" or ws["status"] == "pending_claim"


def lifecycle_values(ws: dict, action, ts: float, name=None) -> dict:
    """The column values `action` writes from the workspace's current row — or the refusal: 400
    `bad_action`; 409 `workspace_legacy` (the historical workspace, whatever the act); 409
    `workspace_personal` (deleting or recovering a personal workspace); 409
    `workspace_state_conflict` (an act the status does not allow); 409
    `workspace_recovery_expired` (a recovery at or past `recover_until`); for a rename 400
    `invalid_field` (1 to 100 characters after trimming, no control characters — the rule of
    workspace creation) and 409 `workspace_name_unchanged`. The one state machine behind
    `platform_transition` and the console's preview, which asks it the same question without
    writing anything."""
    check_action(action)
    if is_legacy(ws):
        raise ServiceError(409, "workspace_legacy", "历史数据空间由认领流程管理，不能在这里重命名、删除或恢复")
    if action != "rename" and ws["kind"] == "personal":
        raise ServiceError(409, "workspace_personal", "个人空间不能删除或恢复，只能重命名")
    live = ws["status"] in RECOVERABLE_STATES and ws.get("deleted_at") is None
    if action == "rename":
        if not live:
            raise ServiceError(409, "workspace_state_conflict", "工作空间当前状态不允许此操作")
        new = _name(name)
        if new == ws["name"]:
            raise ServiceError(409, "workspace_name_unchanged", "新名称与当前名称相同", {"field": "name"})
        return {"name": new}
    if action == "delete" and live:
        return {"status": "deleted", "deleted_at": ts, "recover_until": ts + RECOVERY_SECONDS,
                "previous_status": ws["status"]}
    if action == "recover" and ws["status"] == "deleted":
        if not ws.get("recover_until") or ts >= ws["recover_until"]:
            raise ServiceError(409, "workspace_recovery_expired", "该工作空间的七天恢复期限已过")
        previous = ws.get("previous_status")
        return {"status": previous if previous in RECOVERABLE_STATES else "frozen", "deleted_at": None,
                "recover_until": None, "previous_status": None}
    raise ServiceError(409, "workspace_state_conflict", "工作空间当前状态不允许此操作")


def _table(c, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def withdraw(c, wid: str, ts: float) -> dict:
    """A deletion's withdrawals, on connection `c` inside the caller's write transaction — the live
    deletion and its journal replay (journal._apply_workspace_deleted) alike: sessions moved off the
    workspace, unrevoked tokens revoked, unused authorization codes spent, pending invitations
    (not accepted, declined or revoked) revoked and API-platform approvals (pending or approved)
    revoked, with the credentials bound to them ended by the SQL of `api_platform.revoke_approval`
    (`Auth.revoke_key`: the key's lineage, reason `approval_revoked`; `disable_client`: the
    service account disabled, its secret's grace period ended, its tokens revoked). The replay of
    the deletion's journal entry makes these again, so no journal line of their own is needed.
    Returns the counts as `{sessions, tokens, codes, invitations, approvals}` — the predicates the
    console's preview counts by (a service account's tokens carry its workspace, so its own
    revocation finds none left beyond them)."""
    out = {
        "sessions": c.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=? AND revoked_at IS NULL",
                              (wid,)).rowcount,
        "tokens": c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? WHERE workspace_id=? "
                            "AND revoked_at IS NULL", (ts, WITHDRAWAL_REASON, wid)).rowcount,
        "codes": c.execute("UPDATE oauth_codes SET used_at=? WHERE workspace_id=? AND used_at IS NULL",
                           (ts, wid)).rowcount,
        "invitations": c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND revoked_at IS NULL "
                                 "AND accepted_at IS NULL AND declined_at IS NULL", (ts, wid)).rowcount,
        "approvals": 0,
    }
    # API governance is a separately composed core capability: never create its schema here.
    if _table(c, "api_platform_approvals"):
        bound = c.execute("SELECT b.kind, b.credential_id FROM api_platform_bindings b JOIN api_platform_approvals a "
                          "ON a.id=b.approval_id WHERE a.workspace_id=? AND a.status IN ('pending','approved') "
                          "ORDER BY b.kind, b.credential_id", (wid,)).fetchall() if _table(c, "api_platform_bindings") else []
        out["approvals"] = c.execute("UPDATE api_platform_approvals SET status='revoked', revoked_at=? "
                                     "WHERE workspace_id=? AND status IN ('pending','approved')", (ts, wid)).rowcount
        for binding in bound:
            _end_bound_credential(c, binding["kind"], binding["credential_id"], ts)
    return out


def _end_bound_credential(c, kind: str, credential_id: str, ts: float) -> None:
    """What `api_platform.revoke_approval` does to the credential an approval is bound to, as SQL on
    the caller's connection: an API key's whole lineage revoked (`approval_revoked`, Auth.revoke_key),
    a service account disabled with its secret's grace period ended (Clients.disable) and its tokens
    revoked (`api_platform_disabled`, Grants.revoke_for_client)."""
    if kind == "api_key":
        c.execute("UPDATE api_keys SET revoked_at=?, revoke_reason='approval_revoked' "
                  "WHERE (id=? OR lineage=?) AND revoked_at IS NULL", (ts, credential_id, credential_id))
        return
    from .oidc import Clients
    c.execute("UPDATE oauth_clients SET disabled_at=?, updated_at=? WHERE client_id=? AND disabled_at IS NULL",
              (ts, ts, credential_id))
    c.execute("DELETE FROM meta WHERE key=?", (Clients._grace_key(credential_id),))
    c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='api_platform_disabled' "
              "WHERE client_id=? AND revoked_at IS NULL", (ts, credential_id))


def _record(store: Store, audit, actor_id: str, ws: dict, action: str, meta: dict) -> None:
    """The organization's own record of the act (`organization.workspace.<action>`), when the
    workspace belongs to one: its governance log shows what the platform did to its workspace.
    Inside the mutation transaction; a failure rolls it back."""
    oid = ws.get("organization_id")
    if not oid:
        return
    event = "organization.workspace." + action
    meta = {"workspace_id": ws["id"], **meta}
    if audit:
        audit(event, actor_id=actor_id, workspace_id=ws["id"], object_kind="organization", object_id=oid,
              result="ok", meta=meta)
    else:
        store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user", "actor_id": actor_id,
                                      "workspace_id": ws["id"], "action": event, "object_kind": "organization",
                                      "object_id": oid, "result": "ok", "meta": meta})


def _transition(store: Store, ws: dict, actor_id: str, action: str, reason: str, *, name=None, audit=None,
                meta=None) -> dict | None:
    """The lifecycle write, inside the caller's write transaction with the workspace's current row,
    after the caller's own authority check: the organization's record, then — for a deletion — the
    security journal entry with its receipt, the withdrawals, and last the row. Returns the
    withdrawal counts of a deletion, else None."""
    ts = now()
    values = lifecycle_values(ws, action, ts, name=name)
    keys = ("name",) if action == "rename" else ("status",)
    _record(store, audit, actor_id, ws, action, {
        "reason": reason, **change({k: ws[k] for k in keys}, {k: values[k] for k in keys}), **(meta or {})})
    withdrawn = None
    if action == "delete":
        # Write-ahead: no withdrawal commits without its fsynced intent; a later failure in this
        # process voids the intent on rollback, and one the process did not survive leaves an
        # intent without a receipt, which a restore replays (fail-closed).
        record_workspace(store, WORKSPACE_DELETED_KIND, workspace_id=ws["id"], deleted_at=values["deleted_at"],
                         recover_until=values["recover_until"], previous_status=values["previous_status"])
        with store.conn() as c:
            withdrawn = withdraw(c, ws["id"], ts)
    store.update("workspaces", {"id": ws["id"]}, {**values, "updated_at": ts})
    return withdrawn


def platform_transition(store: Store, wid: str, actor_id: str, action, *, reason, name=None, audit=None) -> dict:
    """The platform's act on any workspace (A44): `actor_id` is a platform administrator who need
    not be a member and gains no membership, entitlement or token here. 400 `missing_reason`
    (8 to 500 characters), 400 `bad_action`, 404 `no_workspace` (a deleted one is found), then
    `lifecycle_values`' refusals. The organization's own record carries `"by": "platform"`.
    `audit(event, **kw)` writes the records (the console's request-bound writer); without one they
    are inserted directly. Returns `{"workspace": the row after, "withdrawn": counts | None,
    "restore_note": code | None}`; joins an enclosing transaction."""
    reason = audit_reason(reason)
    check_action(action)
    with store.tx():
        ws = row(store, wid)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        withdrawn = _transition(store, ws, actor_id, action, reason, name=name, audit=audit, meta={"by": "platform"})
        return {"workspace": row(store, wid), "withdrawn": withdrawn, "restore_note": RESTORE_NOTES.get(action)}
