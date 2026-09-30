"""Authenticated, bounded continuation tokens for Docs resource lists.

A cursor is a position, never an authority: callers still pass the route's current
authentication and object checks. The existing root secret derives a separate HMAC key;
no new persisted secret or schema is needed. Different workers can open the same cursor.
Event-feed checkpoints have a different lifetime and are not resource-list cursors.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from time import time as _clock

from synwevo_core.auth import Auth
from synwevo_core.errors import ServiceError

from . import idempotency

TTL_S = 24 * 60 * 60
MAX_LENGTH = 4096
_KEY_LABEL = b"synwevo/docs-list-cursor/v1"
_PART = re.compile(r"[A-Za-z0-9_-]+")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _key(store) -> bytes:
    return hmac.new(Auth(store).secret(), _KEY_LABEL, hashlib.sha256).digest()


def encode(store, tenant: str, name: str, position: list, filters: dict | None) -> str:
    raw = json.dumps({"l": name, "t": tenant, "k": position,
                      "f": idempotency.fingerprint(filters or {}), "e": int(_clock()) + TTL_S},
                     separators=(",", ":"), allow_nan=False).encode()
    payload = "c1." + _b64(raw)
    return payload + "." + _b64(hmac.new(_key(store), payload.encode("ascii"), hashlib.sha256).digest())


def decode(store, tenant: str, raw: str, name: str, filters: dict | None, width: int) -> list:
    bad = ServiceError(400, "invalid_cursor", "游标无效或已过期；请移除 cursor 从第一页重新读取",
                       {"field": "cursor"})
    # Bound work before decoding JSON or accessing the signing key. In particular, no
    # legacy unsigned payload is accepted as a fallback for a damaged signed token.
    if len(raw) > MAX_LENGTH or not raw.isascii():
        raise bad
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != "c1" or not all(_PART.fullmatch(p) for p in parts[1:]):
        raise bad
    payload = ".".join(parts[:2])
    expected = _b64(hmac.new(_key(store), payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(parts[2], expected):
        raise bad
    try:
        data = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeError) as exc:
        raise bad from exc
    if not isinstance(data, dict) or data.get("l") != name or data.get("t") != tenant \
            or data.get("f") != idempotency.fingerprint(filters or {}) \
            or type(data.get("e")) is not int or data["e"] <= _clock():
        raise bad
    position = data.get("k")
    if not isinstance(position, list) or len(position) != width or not all(
            type(v) in (str, int) or (type(v) is float and math.isfinite(v)) for v in position):
        raise bad
    return position
