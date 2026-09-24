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

from .store import Store

FILENAME = "security-journal.jsonl"

KINDS = {
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
}

_REQUIRED = {
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


def read(root: str | Path, since: float = 0.0) -> list[dict]:
    return [entry for entry, _ in validated_lines(path(root)) if entry["at"] > since]


def apply(store: Store, entries: list[dict]) -> dict:
    """Make every withdrawal in `entries` true in `store`. Returns counts per kind.

    Runs in one transaction, so a restore either has every newer withdrawal re-applied or none.
    """
    for number, entry in enumerate(entries, 1):
        _validate(entry, number)
    t = time.time()
    counts: dict[str, int] = {}
    demoted_workspaces: set[str] = set()
    role_rank = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
    with store.tx() as c:
        for e in entries:
            k = e.get("kind")
            n = 0
            if k == "api_key_revoked":
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
                status = "suspended" if k == "user_suspended" else "deleted"
                n = c.execute("UPDATE users SET status=? WHERE id=? AND status='active'", (status, e.get("user_id"))).rowcount
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
        for wid in sorted(demoted_workspaces):
            owner = c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.workspace_id=? AND m.role='owner' "
                              "AND u.status='active' AND u.deleted_at IS NULL LIMIT 1", (wid,)).fetchone()
            if not owner:
                n = c.execute("UPDATE workspaces SET status='frozen', updated_at=? "
                              "WHERE id=? AND status='active'", (t, wid)).rowcount
                counts["role_changed_workspaces_frozen"] = counts.get("role_changed_workspaces_frozen", 0) + n
    return counts
