"""The security journal: withdrawals of access, recorded outside the database.

Restoring a backup puts the database back the way it was when the backup was taken. For most
data that is the point. For **withdrawn access** it is the opposite of the point: a key revoked
because it leaked, an account suspended, a member removed, a client disabled — each of those
comes back to life, silently, the moment an older database is restored.

So every withdrawal is also appended here, to a file that lives next to the database but is not
part of it. A restore keeps the live journal (it is never overwritten by the backup's copy) and
re-applies every entry newer than the backup (`apply()`), before the service starts.

Entries hold identifiers and, for credential replacements, the same digests the database already
holds — never a secret, a token or document content. The file is created 0600.

Re-applying is idempotent: each entry is expressed as "make this state true", so applying an
entry twice, or applying one the restored database already reflects, changes nothing.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path

from .store import Store, new_id
from .suite_schema import webhooks as suite_webhooks

FILENAME = "security-journal.jsonl"

# Organization withdrawals are write-ahead intents. The same SQLite transaction
# as the withdrawal stores a receipt in the existing meta table. A backup can be
# taken between fsync and COMMIT, so read() must include these even before its
# time cutoff; apply() skips only receipts actually present in that backup.
# This also preserves explicit recovery/regrant completed before the backup.
# No historical journal line or tenant association is rewritten.
ORGANIZATION_KINDS = {
    "organization_frozen", "organization_deleted", "organization_member_removed",
    "organization_role_changed", "organization_policy_restricted",
}
_ORG_RECEIPT = "organization_withdrawal:"

# Undoing the assignment of the historical data (`admin.workspace.unclaim`) is one entry, written
# ahead of the transaction it describes, like the organization withdrawals and for the same two
# reasons: a process that dies between COMMIT and a journal line written afterwards would let an
# older backup give the former members their access back, and a backup taken between a line and
# its COMMIT has neither. Instead of a receipt it carries its own time and is applied "as of"
# then: only memberships, grants, enablements, invitations and tokens that existed at `at` are
# withdrawn, and the status goes back to `pending_claim` only if nobody was assigned the data
# since. So it is re-applied on every restore (read() ignores the cutoff for it) and can never
# undo a later assignment — a restore from a backup taken after a new claim keeps that claim.
UNCLAIM_KIND = "workspace_unclaimed"
#: Entries re-applied whatever the backup's time: their own rules decide what is still to do.
# Older restore code must reject this new kind instead of silently losing a demotion.
# Code rollback retains the mirror; recovery requires a reader supporting platform grants.
PLATFORM_GRANT_KIND = "platform_grant_revoked"
_PLATFORM_RECEIPT = "platform_withdrawal:"
# Paid model dispatch stopped by the risk controls (billing/risk.py). Written ahead of the SQL
# change, so a backup taken between the line and its COMMIT lacks both: replayed whatever the
# backup's time and applied by id, which keeps an administrator's later lift that the backup
# already holds, and re-suspends (re-opens) only what the restored database never saw.
RISK_KINDS = {"model_dispatch_suspended", "model_breaker_opened"}
ALWAYS_REPLAYED = ORGANIZATION_KINDS | {UNCLAIM_KIND, PLATFORM_GRANT_KIND} | RISK_KINDS

KINDS = {
    "api_platform_approval_revoked",  # {approval_id}, including unconsumed approvals
    "api_key_revoked",          # {key_id} — and every key of its lineage
    "client_disabled",          # {client_id}
    "client_secret_rotated",    # {client_id, secret_hash}
    "tokens_revoked",           # {client_id? , user_id?, family_id?}
    "user_suspended",           # {user_id}
    "user_deleted",             # {user_id}
    "password_changed",         # {user_id, hash_id}  (hash stays in the database; see apply())
    "sessions_revoked",         # {user_id}
    "member_removed",           # {workspace_id, user_id}
    "role_changed",             # {workspace_id, user_id, role}  — only recorded for downgrades
    "entitlement_revoked",      # {workspace_id, app_id, user_id}
    "workspace_frozen",         # {workspace_id}
    "webhook_disabled",         # {subscription_id}
    "passkey_removed",          # {credential_id}
    "workspace_app_disabled",   # {workspace_id, app_id}
    "app_disabled",             # {app_id} — the application switched off platform-wide
    "invitation_revoked",       # {invitation_id}
    "api_key_superseded",       # {key_id} — a rotated-out key; the successor is not recreated
    PLATFORM_GRANT_KIND,       # {event_id, user_id, grant_id, role, granted_at, granted_by}
    UNCLAIM_KIND,               # {workspace_id, label_restored} — the assignment of legacy data undone
    "consent_withdrawn",        # {user_id, client_id, workspace_id?} — the consent and that client's tokens
    "model_dispatch_suspended", # {suspension_id, environment, scope_kind, scope_id} — paid model calls of a payer/route
    "model_breaker_opened",     # {event_id, environment} — paid model calls stopped platform-wide
    # The personal Agent (agent_safety.py): a person's withdrawn consent to send content to a
    # model provider, and an administrator's suspension of a person's Agent access.
    "agent_provider_consent_withdrawn",  # {user_id, provider}
    "agent_access_suspended",   # {suspension_id, user_id}
    # Model governance: a workspace's AI processing policy made narrower, the platform model
    # credential withdrawn (revoked or replaced) and the platform model provider switched off.
    "ai_policy_narrowed",       # {workspace_id, processing, providers}
    "model_credential_withdrawn",  # {environment, credential_revision} — that revision and older
    "model_provider_disabled",  # {event_id, environment}
} | ORGANIZATION_KINDS

#: A workspace's AI processing policy, narrowest first (suite agent tools.workspace_policy).
AI_PROCESSING = ("none", "platform", "any")
#: The providers a workspace may name (suite agent tools.MODEL_PROVIDERS).
AI_PROVIDERS = ("anthropic", "deepseek")

_REQUIRED = {
    "api_platform_approval_revoked": ("approval_id",),
    "api_key_revoked": ("key_id",), "api_key_superseded": ("key_id",),
    "client_disabled": ("client_id",), "client_secret_rotated": ("client_id", "secret_hash"),
    "user_suspended": ("user_id",), "user_deleted": ("user_id",),
    "password_changed": ("user_id",), "sessions_revoked": ("user_id",),
    "member_removed": ("workspace_id", "user_id"),
    "role_changed": ("workspace_id", "user_id", "role"),
    "entitlement_revoked": ("workspace_id", "app_id", "user_id"),
    "workspace_frozen": ("workspace_id",), "webhook_disabled": ("subscription_id",),
    "passkey_removed": ("credential_id",), "workspace_app_disabled": ("workspace_id", "app_id"),
    "app_disabled": ("app_id",),
    "invitation_revoked": ("invitation_id",), "tokens_revoked": (),
    PLATFORM_GRANT_KIND: ("event_id", "user_id", "grant_id", "role", "granted_by"),
    UNCLAIM_KIND: ("workspace_id",),
    "consent_withdrawn": ("user_id", "client_id"),
    "model_dispatch_suspended": ("suspension_id", "environment", "scope_kind", "scope_id"),
    "model_breaker_opened": ("event_id", "environment"),
    "agent_provider_consent_withdrawn": ("user_id", "provider"),
    "agent_access_suspended": ("suspension_id", "user_id"),
    "ai_policy_narrowed": ("workspace_id", "processing"),
    "model_credential_withdrawn": ("environment",),
    "model_provider_disabled": ("event_id", "environment"),
    **{kind: ("event_id", "organization_id") for kind in ORGANIZATION_KINDS},
    "organization_member_removed": ("event_id", "organization_id", "user_id"),
    "organization_role_changed": ("event_id", "organization_id", "user_id", "role"),
}


def _validate(entry, line: int) -> None:
    # Do not echo raw input: a journal can contain password-verifier material.
    def bad():
        raise ValueError(f"invalid security journal at line {line}; restore refused; recover the intact journal before retrying")
    if not isinstance(entry, dict):
        bad()
    kind, at = entry.get("kind"), entry.get("at")
    if not isinstance(kind, str) or kind not in KINDS or type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        bad()
    if "account_event_id" in entry and (kind not in {"user_suspended", "sessions_revoked"}
            or not isinstance(entry["account_event_id"], str) or not entry["account_event_id"].strip()):
        bad()
    required = _REQUIRED[kind]
    if kind == "tokens_revoked":
        required = tuple(k for k in ("client_id", "user_id", "family_id") if k in entry)
        if not required:
            bad()
    if any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in required):
        bad()
    if kind == PLATFORM_GRANT_KIND:
        from .platform_access import ROLES
        granted_at = entry.get("granted_at")
        if (entry["role"] not in ROLES or type(granted_at) not in (int, float)
                or not math.isfinite(granted_at) or not 0 <= granted_at <= at):
            bad()
    if kind == "role_changed" and entry["role"] not in {"owner", "admin", "editor", "viewer"}:
        bad()
    if kind == UNCLAIM_KIND and type(entry.get("label_restored", False)) is not bool:
        bad()
    if kind in RISK_KINDS and (entry["environment"] not in ("test", "live") or (
            kind == "model_dispatch_suspended" and entry["scope_kind"] not in ("user", "organization", "route"))):
        bad()
    if kind == "ai_policy_narrowed":
        providers = entry.get("providers")
        if (entry["processing"] not in AI_PROCESSING or not isinstance(providers, list)
                or any(not isinstance(p, str) or not p.strip() for p in providers)):
            bad()
    if kind in ("model_credential_withdrawn", "model_provider_disabled") and entry["environment"] not in ("test", "live"):
        bad()
    if kind == "model_credential_withdrawn" and (type(entry.get("credential_revision")) is not int
                                                 or entry["credential_revision"] < 1):
        bad()
    if kind in ORGANIZATION_KINDS:
        scope = entry.get("workspace_ids")
        if (not isinstance(scope, list) or any(not isinstance(w, str) or not w.strip() for w in scope)
                or len(set(scope)) != len(scope)):
            bad()
        if kind == "organization_role_changed" and entry["role"] not in {"admin", "member"}:
            bad()
        if kind == "organization_deleted":
            if any(type(entry.get(k)) not in (int, float) or not math.isfinite(entry[k]) or entry[k] < 0
                   for k in ("deleted_at", "recover_until")):
                bad()
            if (entry["recover_until"] != entry["deleted_at"] + 7 * 24 * 3600
                    or entry.get("previous_status") not in {"active", "frozen"}):
                bad()


def validated_lines(p: Path) -> list[tuple[dict, str]]:
    """Read complete, validated entries; even a torn final withdrawal is fatal.

    Guessing which withdrawal was lost is unsafe. The caller must fail before installing a
    restored database; the original journal stays untouched for operator-led recovery.
    """
    if not p.exists():
        return []
    out = []
    for number, line in enumerate(p.read_text("utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            raise ValueError(f"invalid security journal at line {number}; restore refused; recover the intact journal before retrying") from None
        _validate(entry, number)
        out.append((entry, line))
    return out


def path(root: str | Path) -> Path:
    return Path(root) / FILENAME


def record(store: Store, kind: str, **fields) -> None:
    """Append one entry. Raises on failure: a withdrawal that cannot be journalled must not be
    reported as durable, so callers decide deliberately whether to proceed."""
    if kind not in KINDS:
        raise ValueError(f"unknown journal entry kind: {kind}")
    entry = {"at": time.time(), "kind": kind, **{k: v for k, v in fields.items() if v is not None}}
    _validate(entry, 1)
    p = path(store.root)
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        if os.write(fd, payload) != len(payload):
            raise OSError("incomplete security journal write")
        os.fsync(fd)
    finally:
        os.close(fd)
    # fsync(file) alone does not make a newly created directory entry durable.
    directory = os.open(p.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def record_organization(store: Store, kind: str, *, organization_id: str, **fields) -> None:
    """Authorize/audit first, then call before SQL withdrawals in a write tx.

    An append/fsync failure aborts that transaction. A later SQL/commit failure
    leaves a restrictive intent without a receipt, which restore must replay.
    The receipt commits atomically with the caller's withdrawal, never separately.
    Only IDs and lifecycle timestamps go into the journal, not names or reasons.
    """
    if kind not in ORGANIZATION_KINDS:
        raise ValueError("not an organization withdrawal")
    with store.conn() as c:
        if not c.in_transaction:
            raise RuntimeError("organization withdrawal requires a write transaction")
        eid = new_id("ow")
        workspace_ids = [r["id"] for r in c.execute(
            "SELECT id FROM workspaces WHERE organization_id=? ORDER BY id", (organization_id,))]
        record(store, kind, event_id=eid, organization_id=organization_id,
               workspace_ids=workspace_ids, **fields)
        c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (_ORG_RECEIPT + eid,))


def record_account(store: Store, kind: str, *, user_id: str, event_id: str) -> None:
    """Write-ahead account withdrawal plus receipt, like organization withdrawals.

    Existing kinds stay readable by older releases. New restores replay an unreceipted
    intent even across the backup cutoff, and do not reapply a withdrawal already in a
    backup after an explicit recovery. No credential or content enters this file.
    """
    if kind not in {"user_suspended", "sessions_revoked"}:
        raise ValueError("not an account withdrawal")
    with store.conn() as c:
        if not c.in_transaction:
            raise RuntimeError("account withdrawal requires a write transaction")
        record(store, kind, user_id=user_id, account_event_id=event_id)
        c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", ("account_withdrawal:" + event_id,))


def record_platform_revocation(store: Store, grant: dict, *, at: float) -> None:
    """Persist the exact grant withdrawal before COMMIT, after its authorization and audit.

    The receipt commits with the demotion. A failed fsync aborts the live change; a later
    commit failure can leave a restrictive intent which recovery must honor. No mailbox,
    administrator-supplied reason or credential is written to the journal.
    """
    with store.conn() as c:
        if not c.in_transaction:
            raise RuntimeError("platform withdrawal requires a write transaction")
        eid = new_id("pw")
        record(store, PLATFORM_GRANT_KIND, at=at, event_id=eid, user_id=grant["user_id"],
               grant_id=grant["id"], role=grant["role"], granted_at=grant["granted_at"],
               granted_by=grant["granted_by"])
        c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (_PLATFORM_RECEIPT + eid,))


def read(root: str | Path, since: float = 0.0) -> list[dict]:
    return [entry for entry, _ in validated_lines(path(root))
            if entry["at"] > since or entry["kind"] in ALWAYS_REPLAYED or entry.get("account_event_id")]


def _apply_unclaim(c, e) -> int:
    """Make "the assignment undone at `at`" true, touching nothing granted after it.

    Everything is compared with the entry's own time: a membership, grant, enablement,
    invitation, token or code from after it belongs to a later assignment and stays. The
    status goes back to `pending_claim` only when no membership is left and the workspace row
    itself has not changed since (a later claim sets `updated_at`)."""
    from .apps import DOCS_APP_ID
    from .store import LEGACY_WORKSPACE_NAME
    wid, at = e["workspace_id"], e["at"]
    if not c.execute("SELECT 1 FROM workspaces WHERE id=? AND kind='legacy'", (wid,)).fetchone():
        return 0
    removed = [r["user_id"] for r in c.execute(
        "SELECT user_id FROM memberships WHERE workspace_id=? AND created_at<=?", (wid, at))]
    apps = {r["app_id"] for r in c.execute(
        "SELECT app_id FROM workspace_apps WHERE workspace_id=? AND enabled_at<=?", (wid, at))} | {DOCS_APP_ID}
    for uid in removed:
        for app_id in sorted(apps):
            # an explicit revoked row per former member, as the undo wrote it (under the `members`
            # policy "no row" means "allowed"); the withdrawal keeps its own time
            c.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                      "VALUES (?,?,?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) "
                      "DO UPDATE SET revoked_at=MAX(COALESCE(app_entitlements.revoked_at,0),excluded.revoked_at)",
                      (wid, app_id, uid, at, at))
    n = c.execute("DELETE FROM memberships WHERE workspace_id=? AND created_at<=?", (wid, at)).rowcount
    c.execute("UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? AND revoked_at IS NULL "
              "AND granted_at<=?", (at, wid, at))
    c.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND enabled_at<=?", (wid, at))
    c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
              "AND revoked_at IS NULL AND created_at<=?", (at, wid, at))
    c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='workspace_unclaimed' "
              "WHERE workspace_id=? AND revoked_at IS NULL AND issued_at<=?", (at, wid, at))
    c.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE workspace_id=? AND created_at<=?",
              (at, wid, at))
    name = ", name=?" if e.get("label_restored") else ""
    n += c.execute(f"UPDATE workspaces SET status='pending_claim'{name}, updated_at=? WHERE id=? AND kind='legacy' "
                   "AND status IN ('active','frozen') AND updated_at<=? "
                   "AND NOT EXISTS (SELECT 1 FROM memberships WHERE workspace_id=?)",
                   ((LEGACY_WORKSPACE_NAME,) if name else ()) + (at, wid, at, wid)).rowcount
    return n


def _org_scope(c, e):
    # Include the event's old IDs: the backup may predate explicit adoption.
    return sorted(set(e["workspace_ids"]) | {r["id"] for r in c.execute(
        "SELECT id FROM workspaces WHERE organization_id=?", (e["organization_id"],))})


def _org_revoke(c, workspace_ids, at, uid=None):
    """Match live org withdrawal scope; personal/unrelated credentials survive."""
    for wid in workspace_ids:
        user_sql, user_args = (" AND user_id=?", (uid,)) if uid else ("", ())
        for table in ("sessions", "oauth_tokens"):
            c.execute(f"UPDATE {table} SET revoked_at=?,revoked_reason='organization_access_withdrawn' "
                      "WHERE workspace_id=? AND revoked_at IS NULL" + user_sql, (at, wid, *user_args))
        c.execute("UPDATE oauth_codes SET used_at=? WHERE workspace_id=? AND used_at IS NULL" + user_sql,
                  (at, wid, *user_args))
        c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
                  "AND revoked_at IS NULL" + (" AND invited_by=?" if uid else ""), (at, wid, *user_args))
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_platform_approvals'").fetchone():
            c.execute("UPDATE api_platform_approvals SET status='revoked',revoked_at=? WHERE workspace_id=? "
                      "AND status IN ('pending','approved')" + (" AND (requested_by=? OR approved_by=?)" if uid else ""),
                      (at, wid, *((uid, uid) if uid else ())))


def _apply_organization(c, e, orphan_orgs, orphan_workspaces):
    oid, at, kind = e["organization_id"], e["at"], e["kind"]
    if c.execute("SELECT 1 FROM meta WHERE key=?", (_ORG_RECEIPT + e["event_id"],)).fetchone():
        return 0
    scope = _org_scope(c, e)
    if kind in {"organization_frozen", "organization_deleted"}:
        if kind == "organization_frozen":
            c.execute("UPDATE organizations SET status='frozen',updated_at=?,version=version+1 "
                      "WHERE id=? AND status='active'", (at, oid))
        else:
            # Never restart the seven-day clock on restore or replace a newer deletion.
            c.execute("UPDATE organizations SET status='deleted',deleted_at=?,recover_until=?,previous_status=?, "
                      "updated_at=?,version=version+1 WHERE id=? AND (deleted_at IS NULL OR deleted_at<?)",
                      (e["deleted_at"], e["recover_until"], e["previous_status"], at, oid, e["deleted_at"]))
        _org_revoke(c, scope, at)
        for wid in scope:
            # An old backup can contain the historical workspace but no org row
            # or attachment. Freeze it instead of inventing that missing grant.
            c.execute("UPDATE workspaces SET status='frozen',updated_at=? WHERE id=? AND status='active' "
                      "AND (organization_id IS NULL OR organization_id<>? OR NOT EXISTS "
                      "(SELECT 1 FROM organizations WHERE id=?))", (at, wid, oid, oid))
    elif kind == "organization_member_removed":
        uid = e["user_id"]
        _org_revoke(c, scope, at, uid)
        c.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, uid))
        for wid in scope:
            c.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))
            c.execute("UPDATE app_entitlements SET revoked_at=COALESCE(revoked_at,?) "
                      "WHERE workspace_id=? AND user_id=?", (at, wid, uid))
        orphan_orgs.add(oid)
        orphan_workspaces.update(scope)
    elif kind == "organization_role_changed":
        uid, role = e["user_id"], e["role"]
        # A transfer's new owner is a grant and is deliberately not recreated.
        c.execute("UPDATE organization_memberships SET role=?,updated_at=? WHERE organization_id=? AND user_id=? "
                  "AND (role='owner' OR (role='admin' AND ?='member'))", (role, at, oid, uid, role))
        for wid in scope:
            c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND invited_by=? "
                      "AND accepted_at IS NULL AND revoked_at IS NULL", (at, wid, uid))
        orphan_orgs.add(oid)
    elif kind == "organization_policy_restricted":
        c.execute("UPDATE organizations SET invitation_policy='owners',updated_at=?,version=version+1 "
                  "WHERE id=? AND invitation_policy<>'owners'", (at, oid))
        for wid in scope:
            c.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND accepted_at IS NULL "
                      "AND revoked_at IS NULL AND invited_by NOT IN (SELECT m.user_id FROM organization_memberships m "
                      "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND m.role='owner' "
                      "AND u.status='active' AND u.deleted_at IS NULL)", (at, wid, oid))
    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (_ORG_RECEIPT + e["event_id"],))
    return 1


def _end_platform_grants(c, uid: str, t: float) -> None:
    """A replayed deletion ends the account's platform grants too, as the deletion itself did
    (profile.finalize_deletions), with the same `admin.platform.revoke` record. Left live, they
    would disagree with the `platform_role='none'` just written, and the next Store open would
    end them as if the previous release had demoted the account during a rollback."""
    from .platform_access import SYSTEM_REASONS
    reason = SYSTEM_REASONS["journal_deleted"]
    try:
        rows = c.execute("SELECT id, role FROM platform_role_grants WHERE user_id=? AND revoked_at IS NULL "
                         "ORDER BY granted_at, id", (uid,)).fetchall()
    except sqlite3.OperationalError:   # a database from before schema 27 has no grants
        return
    for g in rows:
        c.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:journal_replay', "
                  "revoke_reason=? WHERE id=?", (t, reason, g["id"]))
        c.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
                  "result,request_id,ip,meta) VALUES (?,?,'system',NULL,NULL,'admin.platform.revoke','user',?,"
                  "'ok',NULL,NULL,?)",
                  (new_id("ae"), t, uid, json.dumps({"grant_id": g["id"], "role": g["role"],
                                                      "source": "system:journal_replay",
                                                      "reason": reason}, ensure_ascii=False)))


def _apply_platform_revocation(store: Store, c, entry: dict) -> int:
    """End the named grant, or its regenerated migration equivalent from a v26 backup.

    A pre-v27 backup cannot hold the original grant ID. Store has just migrated its legacy
    admin mirror into a new host grant. Its unchanged users.updated_at proves that this
    mirror predates the withdrawal. A later legacy regrant updates that timestamp; a later
    normal grant has a different ID and source. Neither is revoked by this old intent.
    """
    from .platform_access import PlatformAccess, SUPER_ADMIN, _system_audit
    receipt = _PLATFORM_RECEIPT + entry["event_id"]
    if c.execute("SELECT 1 FROM meta WHERE key=?", (receipt,)).fetchone():
        return 0
    uid, at = entry["user_id"], entry["at"]
    exact = c.execute("SELECT * FROM platform_role_grants WHERE id=?", (entry["grant_id"],)).fetchone()
    candidates = []
    if exact:
        if any(exact[key] != entry[key] for key in ("user_id", "role", "granted_at", "granted_by")):
            raise ValueError("platform withdrawal does not match the restored grant; restore refused")
        if exact["revoked_at"] is None:
            candidates.append(dict(exact))
    elif entry["role"] == SUPER_ADMIN:
        candidates = [dict(row) for row in c.execute(
            "SELECT g.* FROM platform_role_grants g JOIN users u ON u.id=g.user_id "
            "WHERE g.user_id=? AND g.role=? AND g.revoked_at IS NULL "
            "AND g.granted_by IN ('migration:v27','reconcile:mirror') AND g.granted_at>? "
            "AND u.platform_role='admin' AND u.updated_at<=?", (uid, SUPER_ADMIN, at, at))]
    for grant in candidates:
        c.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:journal_replay', "
                  "revoke_reason='security_journal_replay' WHERE id=? AND revoked_at IS NULL",
                  (at, grant["id"]))
        _system_audit(store, "admin.platform.revoke", uid,
                      {"grant_id": grant["id"], "role": grant["role"], "source": "system:journal_replay",
                       "withdrawal_id": entry["event_id"], "original_grant_id": entry["grant_id"],
                       "reason": "平台授权已撤销（安全日志重放）"})
    if candidates:
        PlatformAccess(store)._sync_mirror(uid, at)
    c.execute("INSERT INTO meta (key,value) VALUES (?, '1')", (receipt,))
    return len(candidates)


def _disable_suite_webhook(c: sqlite3.Connection, sid, t: float) -> int:
    """Stop a business application's subscription as its own dispatcher stops one: what was
    waiting is dropped, where it stopped is kept as the place to pull from, and its version and
    the time of the change move with the status. A resume then starts from the present, and never
    with a delivery the backup still held. One that was already disabled keeps its reason and when."""
    row = c.execute("SELECT * FROM suite_webhook_subscriptions WHERE id=?", (sid,)).fetchone()
    if not row:
        return 0
    reason = row["disabled_reason"] or "journal_replay"
    if row["status"] == "disabled":
        c.execute("UPDATE suite_webhook_subscriptions SET disabled_reason=?, updated_at=? WHERE id=?", (reason, t, sid))
    else:
        c.execute("UPDATE suite_webhook_subscriptions SET status='disabled', disabled_reason=?, status_changed_at=?, "
                  "updated_at=?, gap_cursor=?, gap_at=?, version=version+1 WHERE id=?",
                  (reason, t, t, *suite_webhooks.stopping(c, row), sid))
    c.execute("UPDATE suite_webhook_deliveries SET status='dropped', last_error=?, next_attempt_at=NULL "
              "WHERE subscription_id=? AND status='pending'", (f"subscription_disabled:{reason}", sid))
    return 1


def _apply_risk(c, e) -> int:
    """Re-suspend / re-open only what the restored database never recorded (by id)."""
    at = int(e["at"])
    if e["kind"] == "model_dispatch_suspended":
        return c.execute("INSERT OR IGNORE INTO model_risk_suspensions(id,environment,scope_kind,scope_id,source,"
                         "reason_code,created_at) VALUES (?,?,?,?,'journal','journal_replay',?)",
                         (e["suspension_id"], e["environment"], e["scope_kind"], e["scope_id"], at)).rowcount
    if c.execute("SELECT 1 FROM model_risk_breaker_events WHERE id=?", (e["event_id"],)).fetchone():
        return 0
    latest = c.execute("SELECT seq,state FROM model_risk_breaker_events WHERE environment=? ORDER BY seq DESC LIMIT 1",
                       (e["environment"],)).fetchone()
    if latest is not None and latest["state"] == "open":
        return 0
    c.execute("INSERT INTO model_risk_breaker_events(id,environment,seq,state,source,reason_code,created_at) "
              "VALUES (?,?,?,'open','journal','journal_replay',?)",
              (e["event_id"], e["environment"], (latest["seq"] if latest else 0) + 1, at))
    return 1


def _apply_ai_policy(c, e, t: float) -> int:
    """Make the workspace's policy at most as open as the journalled one: the narrower
    processing and the intersection of providers. A restored row (or its absence, which means
    the default) that is already as narrow is left alone, so a replay changes nothing twice."""
    wid = e["workspace_id"]
    ws = c.execute("SELECT kind FROM workspaces WHERE id=?", (wid,)).fetchone()
    if not ws:
        return 0
    row = c.execute("SELECT processing FROM agent_workspace_policies WHERE workspace_id=?", (wid,)).fetchone()
    prov = c.execute("SELECT providers_json FROM agent_workspace_model_providers WHERE workspace_id=?", (wid,)).fetchone()
    # The defaults of tools.workspace_policy: a personal workspace without a policy is open to
    # every provider; any other is closed, with the legacy provider list.
    personal_default = row is None and ws["kind"] == "personal"
    processing = row["processing"] if row else ("any" if personal_default else "none")
    try:
        providers = json.loads(prov["providers_json"]) if prov else (list(AI_PROVIDERS) if personal_default else ["anthropic"])
        providers = [p for p in providers if isinstance(p, str)] if isinstance(providers, list) else []
    except ValueError:
        providers = []
    narrowed = min(processing, e["processing"], key=AI_PROCESSING.index) if processing in AI_PROCESSING else "none"
    allowed = sorted(set(providers) & set(e["providers"]))
    if narrowed == processing and row is not None and allowed == sorted(providers) and prov is not None:
        return 0
    c.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,'journal',?) "
              "ON CONFLICT(workspace_id) DO UPDATE SET processing=excluded.processing, updated_by='journal', "
              "updated_at=excluded.updated_at", (wid, narrowed, t))
    c.execute("INSERT INTO agent_workspace_model_providers (workspace_id, providers_json, updated_by, updated_at) "
              "VALUES (?,?,'journal',?) ON CONFLICT(workspace_id) DO UPDATE SET providers_json=excluded.providers_json, "
              "updated_by='journal', updated_at=excluded.updated_at", (wid, json.dumps(allowed), t))
    return 1


def _apply_provider_disabled(c, e) -> int:
    """Append, if the restored history's newest version is enabled, an immutable version that
    switches the platform model provider off — keyed by the journal event, so it happens once.
    It reuses the newest version's author (who exists in that database) and says why."""
    op = "journal_" + e["event_id"]
    if c.execute("SELECT 1 FROM model_provider_versions WHERE operation_id=?", (op,)).fetchone():
        return 0
    latest = c.execute("SELECT * FROM model_provider_versions WHERE environment=? ORDER BY revision DESC LIMIT 1",
                       (e["environment"],)).fetchone()
    if latest is None:
        return 0
    spec = json.loads(latest["spec_json"])
    if not spec.get("enabled"):
        return 0
    spec["enabled"] = False
    c.execute("INSERT INTO model_provider_versions(environment,revision,spec_json,credential_revision,credential_configured,"
              "actor_id,reason,operation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
              (e["environment"], latest["revision"] + 1, json.dumps(spec, sort_keys=True, separators=(",", ":")),
               latest["credential_revision"], latest["credential_configured"], latest["actor_id"],
               "Journal replay: the model provider was switched off after this backup", op, int(e["at"])))
    return 1


def apply(store: Store, entries: list[dict]) -> dict:
    """Make every withdrawal in `entries` true in `store`. Returns counts per kind.

    Runs in one transaction, so a restore either has every newer withdrawal re-applied or none.
    """
    for number, entry in enumerate(entries, 1):
        _validate(entry, number)
    t = time.time()
    counts: dict[str, int] = {}
    demoted_workspaces: set[str] = set()
    demoted_orgs: set[str] = set()
    role_rank = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    with store.tx() as c:
        from .platform_access import PlatformAccess
        # A fresh/never-bootstrapped archive may legitimately have no administrator. Reject
        # only a replay that removes the last usable one present at the start, whether by
        # grant withdrawal, account suspension/deletion or loss of its last sign-in proof.
        had_usable_super_admin = bool(PlatformAccess(store).usable_super_admins())
        for e in entries:
            k = e.get("kind")
            n = 0
            account_event = e.get("account_event_id")
            if account_event and c.execute("SELECT 1 FROM meta WHERE key=?",
                                           ("account_withdrawal:" + account_event,)).fetchone():
                continue
            if k == PLATFORM_GRANT_KIND:
                n = _apply_platform_revocation(store, c, e)
            elif k in RISK_KINDS:
                n = _apply_risk(c, e)
            elif k in ORGANIZATION_KINDS:
                n = _apply_organization(c, e, demoted_orgs, demoted_workspaces)
            elif k == UNCLAIM_KIND:
                n = _apply_unclaim(c, e)
            elif k == "api_key_revoked":
                kid = e.get("key_id")
                row = c.execute("SELECT COALESCE(lineage, id) AS l FROM api_keys WHERE id=?", (kid,)).fetchone()
                lineage = row["l"] if row else kid
                n = c.execute("UPDATE api_keys SET revoked_at=COALESCE(revoked_at, ?), revoke_reason=COALESCE(revoke_reason,'revoked') "
                              "WHERE id=? OR lineage=? OR id=?", (t, kid, lineage, lineage)).rowcount
            elif k == "client_disabled":
                n = c.execute("UPDATE oauth_clients SET disabled_at=COALESCE(disabled_at, ?) WHERE client_id=?",
                              (t, e.get("client_id"))).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='client_disabled' "
                          "WHERE client_id=? AND revoked_at IS NULL", (t, e.get("client_id")))
            elif k == "consent_withdrawn":
                # A restored backup must not bring back a consent the person withdrew afterwards: it
                # would let the client's next authorization skip the consent page.
                ws = e.get("workspace_id") or ""
                n = c.execute("UPDATE oauth_consents SET revoked_at=?, revoked_reason='journal_replay', updated_at=? "
                              "WHERE user_id=? AND client_id=? AND workspace_id=? AND revoked_at IS NULL AND granted_at<=?",
                              (t, t, e["user_id"], e["client_id"], ws, e["at"])).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND client_id=? "
                          "AND COALESCE(workspace_id,'')=? AND revoked_at IS NULL", (t, e["user_id"], e["client_id"], ws))
            elif k == "agent_provider_consent_withdrawn":
                # Only a consent given before the withdrawal is withdrawn again; one the person
                # gave afterwards (and that the restored database holds) stands.
                n = c.execute("UPDATE agent_provider_consents SET withdrawn_at=? WHERE user_id=? AND provider=? "
                              "AND withdrawn_at IS NULL AND accepted_at<=?",
                              (e["at"], e["user_id"], e["provider"], e["at"])).rowcount
            elif k == "agent_access_suspended":
                # By id: a suspension the backup already holds (lifted or not) is left as it is.
                n = c.execute("INSERT OR IGNORE INTO agent_access_suspensions (id,user_id,reason,created_by,created_at) "
                              "VALUES (?,?,'journal_replay','journal',?)",
                              (e["suspension_id"], e["user_id"], e["at"])).rowcount
            elif k == "ai_policy_narrowed":
                n = _apply_ai_policy(c, e, t)
            elif k == "model_credential_withdrawn":
                # The withdrawn revision and anything older loses its sealed secret; a newer
                # credential the restored database already holds is left alone.
                n = c.execute("UPDATE model_provider_credentials SET secret_enc=NULL WHERE environment=? "
                              "AND revision<=? AND secret_enc IS NOT NULL",
                              (e["environment"], e["credential_revision"])).rowcount
            elif k == "model_provider_disabled":
                n = _apply_provider_disabled(c, e)
            elif k == "client_secret_rotated":
                n = c.execute("UPDATE oauth_clients SET secret_hash=? WHERE client_id=?",
                              (e.get("secret_hash"), e.get("client_id"))).rowcount
            elif k == "tokens_revoked":
                where, args = [], []
                for col in ("client_id", "user_id", "family_id"):
                    if e.get(col):
                        where.append(f"{col}=?")
                        args.append(e[col])
                if where:
                    n = c.execute(f"UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' "
                                  f"WHERE revoked_at IS NULL AND {' AND '.join(where)}", (t, *args)).rowcount
            elif k in ("user_suspended", "user_deleted"):
                uid, at = e["user_id"], e["at"]
                if k == "user_deleted":
                    # Deletion is terminal even if an earlier withdrawal already suspended
                    # the user. Restore must also erase the profile/credentials which the
                    # older backup still contains. Keep the stable ID and reserved email_key;
                    # never cascade into organization contributions or coworker documents.
                    n = c.execute("UPDATE users SET status='deleted',deleted_at=COALESCE(deleted_at,?),"
                                  "updated_at=?,display_name=NULL,email='',email_verified_at=NULL,"
                                  "avatar_blob=NULL,locale='zh-Hans',timezone='UTC',platform_role='none' WHERE id=?",
                                  (at, at, uid)).rowcount
                    for table in ("user_passwords", "passkeys", "webauthn_credentials", "totp_secrets",
                                  "recovery_codes", "account_recoveries", "mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    c.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                    _end_platform_grants(c, uid, t)
                    # The older backup still holds the person's Agent conversations and memories.
                    from .agent_records import erase as erase_agent
                    erase_agent(store, uid)
                else:
                    for table in ("mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    for table in ("email_tokens", "account_recoveries"):
                        c.execute(f"UPDATE {table} SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                    n = c.execute("UPDATE users SET status='suspended' WHERE id=? AND status IN ('active','pending')",
                                  (uid,)).rowcount
                c.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                c.execute("UPDATE sessions SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
            elif k in ("sessions_revoked", "password_changed"):
                # The new password hash is not journalled. A restore that predates a password
                # change therefore brings the *old* password back — so every session and token of
                # that account is ended and the account is flagged for a reset rather than
                # silently re-opened with a credential that may be the reason it was changed.
                n = c.execute("UPDATE sessions SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                              (t, e.get("user_id"))).rowcount
                c.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='journal_replay' WHERE user_id=? AND revoked_at IS NULL",
                          (t, e.get("user_id")))
                if k == "sessions_revoked":
                    # The live account operation also ends unfinished authentication. An old
                    # backup must not turn its spent MFA/email/Passkey/OIDC proof into a new
                    # session after its restored sessions were revoked. Match invalidate_access.
                    for table in ("oauth_codes", "email_tokens"):
                        c.execute(f"UPDATE {table} SET used_at=COALESCE(used_at,?) WHERE user_id=?",
                                  (t, e["user_id"]))
                    for table in ("mfa_tickets", "webauthn_challenges", "reauth_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (e["user_id"],))
                if k == "password_changed":
                    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                              (f"password_reset_required:{e.get('user_id')}", str(t)))
            elif k == "member_removed":
                n = c.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                              (e.get("workspace_id"), e.get("user_id"))).rowcount
                c.execute("UPDATE app_entitlements SET revoked_at=COALESCE(revoked_at, ?) WHERE workspace_id=? AND user_id=?",
                          (t, e.get("workspace_id"), e.get("user_id")))
            elif k == "role_changed":
                # Grants are not journalled. The new owner may therefore be absent from this
                # backup; retaining the old owner's withdrawn authority is not a safe repair.
                # Apply only reductions, then freeze any affected workspace with no usable
                # owner after all withdrawals. Never recreate a missing grant or membership.
                wid, uid, role = e.get("workspace_id"), e.get("user_id"), e.get("role")
                cur = c.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                                (wid, uid)).fetchone()
                if role != "owner":
                    demoted_workspaces.add(wid)
                if cur and (role_rank[role] < role_rank.get(cur["role"], -1)
                            or role == cur["role"] == "viewer" and cur["can_export"]):
                    n = c.execute("UPDATE memberships SET role=?, "
                                  "can_export=CASE WHEN ?='viewer' THEN 0 ELSE can_export END, "
                                  "updated_at=? WHERE workspace_id=? AND user_id=?",
                                  (role, role, t, wid, uid)).rowcount
            elif k == "workspace_app_disabled":
                n = c.execute("DELETE FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (e.get("workspace_id"), e.get("app_id"))).rowcount
            elif k == "app_disabled":
                n = c.execute("UPDATE apps SET status='disabled', updated_at=? WHERE id=? AND status<>'disabled'",
                              (t, e.get("app_id"))).rowcount
            elif k == "invitation_revoked":
                n = c.execute("UPDATE invitations SET revoked_at=COALESCE(revoked_at, ?) WHERE id=? AND accepted_at IS NULL",
                              (t, e.get("invitation_id"))).rowcount
            elif k == "api_key_superseded":
                n = c.execute("UPDATE api_keys SET revoked_at=COALESCE(revoked_at, ?), revoke_reason=COALESCE(revoke_reason,'rotated') "
                              "WHERE id=?", (t, e.get("key_id"))).rowcount
            elif k == "api_platform_approval_revoked":
                n = c.execute("UPDATE api_platform_approvals SET status='revoked',revoked_at=COALESCE(revoked_at,?) WHERE id=?",
                              (e.get("at", t), e.get("approval_id"))).rowcount
            elif k == "entitlement_revoked":
                # The members policy needs no grant row, so older backups can have nothing
                # to UPDATE. A prior membership's stale revocation must also be replaced:
                # current access and deferred work compare it with membership.created_at.
                # Keep the durable withdrawal time, not each replay's wall clock: repeating
                # a restore must not move this revocation past a later membership/grant.
                revoked_at = e.get("at", t)
                n = c.execute("INSERT INTO app_entitlements "
                              "(workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                              "VALUES (?,?,?,?,NULL,?) ON CONFLICT(workspace_id,app_id,user_id) "
                              "DO UPDATE SET revoked_at=MAX(COALESCE(app_entitlements.revoked_at,0),excluded.revoked_at)",
                              (e.get("workspace_id"), e.get("app_id"), e.get("user_id"), revoked_at, revoked_at)).rowcount
            elif k == "workspace_frozen":
                n = c.execute("UPDATE workspaces SET status='frozen', updated_at=? WHERE id=? AND status='active'",
                              (t, e.get("workspace_id"))).rowcount
            elif k == "webhook_disabled":
                n = c.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason=COALESCE(disabled_reason,'journal_replay'), "
                              "updated_at=? WHERE id=?", (t, e.get("subscription_id"))).rowcount
                # The business applications' subscriptions are journalled under the same kind; their
                # ids do not collide with Docs' (suite_schema/webhooks.py).
                n += _disable_suite_webhook(c, e.get("subscription_id"), t)
            elif k == "passkey_removed":
                for table in ("webauthn_credentials", "passkeys"):
                    try:
                        n += c.execute(f"DELETE FROM {table} WHERE id=?", (e.get("credential_id"),)).rowcount
                    except Exception:  # noqa: BLE001 - table name differs across versions
                        pass
            if account_event:
                c.execute("INSERT OR IGNORE INTO meta (key,value) VALUES (?, '1')",
                          ("account_withdrawal:" + account_event,))
            counts[k] = counts.get(k, 0) + n
        if had_usable_super_admin and not PlatformAccess(store).usable_super_admins():
            # Never resurrect a revoked administrator when the backup lacks its successor.
            raise ValueError("restored backup has no usable super administrator after security withdrawals; "
                             "restore refused; use a newer backup containing an authorized successor")
        for oid in sorted(demoted_orgs):
            owner = c.execute("SELECT 1 FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.organization_id=? AND m.role='owner' "
                              "AND u.status='active' AND u.deleted_at IS NULL LIMIT 1", (oid,)).fetchone()
            if not owner:
                n = c.execute("UPDATE organizations SET status='frozen',updated_at=?,version=version+1 "
                              "WHERE id=? AND status='active'", (t, oid)).rowcount
                scope = [r["id"] for r in c.execute("SELECT id FROM workspaces WHERE organization_id=?", (oid,))]
                _org_revoke(c, scope, t)
                counts["ownerless_organizations_frozen"] = counts.get("ownerless_organizations_frozen", 0) + n
        for wid in sorted(demoted_workspaces):
            owner = c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
                              "JOIN workspaces w ON w.id=m.workspace_id LEFT JOIN organization_memberships om "
                              "ON om.organization_id=w.organization_id AND om.user_id=u.id "
                              "WHERE m.workspace_id=? AND m.role='owner' "
                              "AND (w.organization_id IS NULL OR om.user_id IS NOT NULL) "
                              "AND u.status='active' AND u.deleted_at IS NULL LIMIT 1", (wid,)).fetchone()
            if not owner:
                n = c.execute("UPDATE workspaces SET status='frozen', updated_at=? "
                              "WHERE id=? AND status='active'", (t, wid)).rowcount
                counts["role_changed_workspaces_frozen"] = counts.get("role_changed_workspaces_frozen", 0) + n
    return counts
