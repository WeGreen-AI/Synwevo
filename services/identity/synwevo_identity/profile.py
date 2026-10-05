"""Account-owned profile operations. Store remains the only connection/migration owner.

Integration contract (add in Store, never execute here):
  users: locale TEXT NOT NULL DEFAULT 'zh-Hans', timezone TEXT NOT NULL DEFAULT 'UTC',
         avatar_blob BLOB, deletion_requested_at REAL, deletion_due_at REAL
  account_recoveries(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
      token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL,
      expires_at REAL NOT NULL, used_at REAL)
Call finalize_deletions(store) from the identity sweeper.
"""
from __future__ import annotations

import hashlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from synwevo_core.avatar_image import MAX_AVATAR_BYTES, canonical_avatar  # noqa: F401 — the platform's one set of avatar rules (CI-78)
from synwevo_core.errors import ServiceError
from synwevo_core.oidc import Grants
from synwevo_core.platform_access import SYSTEM_REASONS, PlatformAccess
from synwevo_core.sessions import Sessions
from synwevo_core.store import now

LOCALES = ("zh-Hans", "zh-Hant", "en")
GRACE_SECONDS = 7 * 86400


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def public_profile(user: dict) -> dict:
    return {key: user.get(key) for key in (
        "id", "email", "display_name", "status", "email_verified_at", "created_at",
        "updated_at", "locale", "timezone", "deletion_requested_at", "deletion_due_at"
    )} | {"has_avatar": bool(user.get("avatar_blob"))}


def validate_preferences(body: dict) -> dict:
    if not body or set(body) - {"display_name", "locale", "timezone"}:
        raise ServiceError(400, "profile_fields", "只能修改名称、语言和时区")
    values = {}
    for key, value in body.items():
        if not isinstance(value, str):
            raise ServiceError(400, "profile_fields", "请输入有效的个人资料")
        if key == "display_name":
            value = value.strip()
            if not 1 <= len(value) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ServiceError(400, "bad_display_name", "名称须为 1 至 80 个字符")
        elif key == "locale" and value not in LOCALES:
            raise ServiceError(400, "bad_locale", "请选择支持的语言")
        elif key == "timezone":
            try:
                if len(value) > 100:
                    raise ValueError()
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ServiceError(400, "bad_timezone", "请选择有效的 IANA 时区") from None
        values[key] = value
    return values


def invalidate_access(store, uid: str, reason: str) -> None:
    """Atomic when called within Store.tx; no old grant becomes live after recovery."""
    Sessions(store).revoke_all_for_user(uid, reason)
    Grants(store).revoke_for_user(uid, reason)
    ts = now()
    store.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE user_id=?", (ts, uid))
    store.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=?", (ts, uid))
    store.execute("DELETE FROM mfa_tickets WHERE user_id=?", (uid,))
    store.execute("DELETE FROM webauthn_challenges WHERE user_id=?", (uid,))
    store.execute("DELETE FROM reauth_challenges WHERE user_id=?", (uid,))


def check_deletion_constraints(store, user: dict) -> None:
    uid = user["id"]
    # Called inside the deletion's write transaction, so two last super administrators deleting
    # themselves at once cannot both pass (the second re-reads the first's pending_delete).
    access = PlatformAccess(store)
    if access.is_usable_super_admin(user) and not access.usable_super_admins(excluding=uid):
        raise ServiceError(409, "last_super_admin",
                           "你是最后一名可用的最高管理员。请先授予另一位最高管理员，并确认对方能登录并完成身份确认")
    if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_memberships'"):
        for member in store.all("SELECT m.organization_id,m.role FROM organization_memberships m "
                                "JOIN organizations o ON o.id=m.organization_id WHERE m.user_id=? "
                                "AND m.role IN ('owner','admin') AND (o.status<>'deleted' OR o.recover_until>?)", (uid, now())):
            others = store.all("SELECT m.role FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                               "WHERE m.organization_id=? AND m.user_id<>? AND u.status='active' AND u.deleted_at IS NULL",
                               (member["organization_id"], uid))
            roles = {other["role"] for other in others}
            if (member["role"] == "owner" and "owner" not in roles) or not roles.intersection({"owner", "admin"}):
                raise ServiceError(409, "organization_ownership_required", "请先移交组织的所有权和管理权限")
    # A personal workspace containing only this person may remain inaccessible. Shared
    # personal workspaces have the same stewardship requirement as organizations.
    for member in store.all(
            "SELECT m.workspace_id,m.role,w.kind FROM memberships m JOIN workspaces w ON w.id=m.workspace_id "
            "WHERE m.user_id=? AND m.role IN ('owner','admin') AND w.deleted_at IS NULL", (uid,)):
        wid = member["workspace_id"]
        if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organizations'") and store.one(
                "SELECT 1 FROM workspaces w JOIN organizations o ON o.id=w.organization_id "
                "WHERE w.id=? AND o.status='deleted' AND (o.recover_until IS NULL OR o.recover_until<=?)", (wid, now())):
            continue
        others = store.all("SELECT m.role,u.status FROM memberships m JOIN users u ON u.id=m.user_id "
                           "WHERE m.workspace_id=? AND m.user_id<>? AND u.deleted_at IS NULL", (wid, uid))
        if member["kind"] == "personal" and not others:
            continue
        eligible = {row["role"] for row in others if row["status"] == "active"}
        if (member["role"] == "owner" and "owner" not in eligible) or not eligible.intersection({"owner", "admin"}):
            raise ServiceError(409, "ownership_required", "请先移交共享工作空间的所有权和管理权限")


def depart_deleted_accounts(store) -> int:
    """Every deleted account still listed in a conversation leaves it (CI-81): a group it owned
    passes to the earliest-joined admin, else member, with the hand-over written in the group;
    a group left empty is archived. Covers this sweep's deletions and any made before the rule
    existed. Outside the deletion transaction on purpose — messaging keeps its own — and
    idempotent, so a failure here is simply retried on the next sweep."""
    try:
        rows = store.all("SELECT DISTINCT m.user_id FROM conversation_members m JOIN users u ON u.id=m.user_id "
                         "WHERE m.left_at IS NULL AND u.status='deleted'")
    except Exception:  # noqa: BLE001 - a deployment without messaging tables has nothing to do
        return 0
    if not rows:
        return 0
    from synwevo_core.identity import Identity
    from synwevo_core.messaging import Messaging
    messaging = Messaging(store, Identity(store))
    return sum(messaging.depart(r["user_id"], reason="account_deleted") for r in rows)


def finalize_deletions(store) -> int:
    """Idempotent expiry: preserve immutable user IDs and organization contributions."""
    removed = []
    with store.tx():
        for user in store.all("SELECT id FROM users WHERE status='pending_delete' AND deletion_due_at<=?", (now(),)):
            uid = user["id"]
            invalidate_access(store, uid, "account_deleted")
            for table in ("user_passwords", "passkeys", "webauthn_credentials", "totp_secrets", "recovery_codes", "account_recoveries"):
                store.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            # Synwevo Message blocks (CI-78): the person's own list, and blocks naming them.
            from synwevo_core.messaging import forget_blocks
            forget_blocks(store, uid)
            # The personal Agent's content goes; financial and consent records stay under the
            # permanent, now nameless id (synwevo_core.agent_records).
            from synwevo_core.agent_records import erase as erase_agent
            erase_agent(store, uid)
            # email_key remains reserved: recycling it would resurrect old address-bound invites.
            store.execute("UPDATE users SET status='deleted',deleted_at=?,updated_at=?,display_name=NULL,"
                          "email='',email_verified_at=NULL,avatar_blob=NULL,locale='zh-Hans',timezone='UTC',"
                          "platform_role='none' WHERE id=?", (now(), now(), uid))
            # Platform grants end with the account (they stopped counting at pending_delete).
            PlatformAccess(store).system_revoke_all(uid, source="system:account_deleted",
                                                    reason=SYSTEM_REASONS["account_deleted"])
            # Commit a retry marker with the deletion. A filesystem failure after this
            # transaction must not make the sweeper permanently forget the withdrawal.
            store.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                          (f"account-deletion-journal:{uid}", uid))
            removed.append(uid)
    depart_deleted_accounts(store)
    from .withdrawals import record
    for pending in store.all("SELECT key,value FROM meta WHERE key GLOB 'account-deletion-journal:*'"):
        if record(store, "user_deleted", user_id=pending["value"]):
            # A crash before this removal can append a duplicate withdrawal; journal replay
            # is idempotent. Losing an unrecorded withdrawal is the unsafe alternative.
            store.execute("DELETE FROM meta WHERE key=? AND value=?", (pending["key"], pending["value"]))
    return len(removed)
