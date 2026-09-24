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

import base64
import binascii
import hashlib
import struct
import zlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from synwevo_core.errors import ServiceError
from synwevo_core.oidc import Grants
from synwevo_core.sessions import Sessions
from synwevo_core.store import now

LOCALES = ("zh-Hans", "zh-Hant", "en")
GRACE_SECONDS = 7 * 86400
MAX_AVATAR_BYTES = 256 * 1024


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


def canonical_avatar(encoded: str) -> bytes:
    """Accept bounded, non-interlaced 8-bit PNG; rebuild only IHDR/IDAT/IEND.

    No SVG, animation, filenames, URLs, external fetches, metadata or new image dependency.
    Bound compressed input AND inflated scanlines before accepting any pixel data.
    """
    def bad():
        return ServiceError(400, "bad_avatar", "请上传不超过 256 KB、512 × 512 像素的普通 PNG 图片")

    if not isinstance(encoded, str) or len(encoded) > (MAX_AVATAR_BYTES * 4 // 3 + 4):
        raise bad()
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AVATAR_BYTES or raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError()
        pos, header, compressed, ended, after_idat = 8, None, bytearray(), False, False
        while pos < len(raw):
            length = struct.unpack(">I", raw[pos:pos + 4])[0]
            kind = raw[pos + 4:pos + 8]
            payload = raw[pos + 8:pos + 8 + length]
            end = pos + 12 + length
            if end > len(raw) or len(kind) != 4:
                raise ValueError()
            crc = struct.unpack(">I", raw[pos + 8 + length:end])[0]
            if binascii.crc32(kind + payload) & 0xffffffff != crc:
                raise ValueError()
            if header is None:
                if kind != b"IHDR" or length != 13:
                    raise ValueError()
                header = payload
            elif kind == b"IDAT":
                if after_idat:
                    raise ValueError()
                compressed.extend(payload)
            elif kind == b"IEND":
                if length or end != len(raw):
                    raise ValueError()
                ended = True
            else:
                # Unknown critical chunks and APNG are rejected; ancillary metadata is stripped.
                if not kind[0] & 32 or kind in (b"acTL", b"fcTL", b"fdAT"):
                    raise ValueError()
                if compressed:
                    after_idat = True
            pos = end
        if not ended or not header or not compressed:
            raise ValueError()
        width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
        if (not channels or not 1 <= width <= 512 or not 1 <= height <= 512
                or depth != 8 or compression or filtering or interlace):
            raise ValueError()
        stride = 1 + width * channels
        expected = height * stride
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), expected + 1)
        if (len(pixels) != expected or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, expected, stride))):
            raise ValueError()
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
        return raw[:8] + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
    except (ValueError, struct.error, zlib.error, binascii.Error):
        raise bad() from None


def invalidate_access(store, uid: str, reason: str) -> None:
    """Atomic when called within Store.tx; no old grant becomes live after recovery."""
    Sessions(store).revoke_all_for_user(uid, reason)
    Grants(store).revoke_for_user(uid, reason)
    ts = now()
    store.execute("UPDATE oauth_codes SET used_at=COALESCE(used_at,?) WHERE user_id=?", (ts, uid))
    store.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=?", (ts, uid))
    store.execute("DELETE FROM mfa_tickets WHERE user_id=?", (uid,))
    store.execute("DELETE FROM webauthn_challenges WHERE user_id=?", (uid,))


def check_deletion_constraints(store, user: dict) -> None:
    uid = user["id"]
    if user["platform_role"] == "admin" and not store.one(
            "SELECT 1 FROM users WHERE id<>? AND platform_role='admin' AND status='active' AND deleted_at IS NULL", (uid,)):
        raise ServiceError(409, "last_admin", "你是最后一个平台管理员，请先移交管理权限")
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


def finalize_deletions(store) -> int:
    """Idempotent expiry: preserve immutable user IDs and organization contributions."""
    removed = []
    with store.tx():
        for user in store.all("SELECT id FROM users WHERE status='pending_delete' AND deletion_due_at<=?", (now(),)):
            uid = user["id"]
            invalidate_access(store, uid, "account_deleted")
            for table in ("user_passwords", "passkeys", "webauthn_credentials", "totp_secrets", "recovery_codes", "account_recoveries"):
                store.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            # email_key remains reserved: recycling it would resurrect old address-bound invites.
            store.execute("UPDATE users SET status='deleted',deleted_at=?,updated_at=?,display_name=NULL,"
                          "email='',email_verified_at=NULL,avatar_blob=NULL,locale='zh-Hans',timezone='UTC',"
                          "platform_role='none' WHERE id=?", (now(), now(), uid))
            # Commit a retry marker with the deletion. A filesystem failure after this
            # transaction must not make the sweeper permanently forget the withdrawal.
            store.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                          (f"account-deletion-journal:{uid}", uid))
            removed.append(uid)
    from .withdrawals import record
    for pending in store.all("SELECT key,value FROM meta WHERE key GLOB 'account-deletion-journal:*'"):
        if record(store, "user_deleted", user_id=pending["value"]):
            # A crash before this removal can append a duplicate withdrawal; journal replay
            # is idempotent. Losing an unrecorded withdrawal is the unsafe alternative.
            store.execute("DELETE FROM meta WHERE key=? AND value=?", (pending["key"], pending["value"]))
    return len(removed)
