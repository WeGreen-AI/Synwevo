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
import time
from pathlib import Path

from .store import Store, new_id

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
    "invitation_revoked",       # {invitation_id}
    "api_key_superseded",       # {key_id} — a rotated-out key; the successor is not recreated
    "consent_withdrawn",        # {user_id, client_id, workspace_id?} — the consent and that client's tokens
} | ORGANIZATION_KINDS

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
    "invitation_revoked": ("invitation_id",), "tokens_revoked": (),
    "consent_withdrawn": ("user_id", "client_id"),
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
    required = _REQUIRED[kind]
    if kind == "tokens_revoked":
        required = tuple(k for k in ("client_id", "user_id", "family_id") if k in entry)
        if not required:
            bad()
    if any(not isinstance(entry.get(k), str) or not entry[k].strip() for k in required):
        bad()
    if kind == "role_changed" and entry["role"] not in {"owner", "admin", "editor", "viewer"}:
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


def read(root: str | Path, since: float = 0.0) -> list[dict]:
    return [entry for entry, _ in validated_lines(path(root))
            if entry["at"] > since or entry["kind"] in ORGANIZATION_KINDS]


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
        for e in entries:
            k = e.get("kind")
            n = 0
            if k in ORGANIZATION_KINDS:
                n = _apply_organization(c, e, demoted_orgs, demoted_workspaces)
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
                                  "recovery_codes", "account_recoveries", "mfa_tickets", "webauthn_challenges"):
                        c.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                    c.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=?", (at, uid))
                else:
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
            elif k == "passkey_removed":
                for table in ("webauthn_credentials", "passkeys"):
                    try:
                        n += c.execute(f"DELETE FROM {table} WHERE id=?", (e.get("credential_id"),)).rowcount
                    except Exception:  # noqa: BLE001 - table name differs across versions
                        pass
            counts[k] = counts.get(k, 0) + n
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
