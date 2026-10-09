"""Bounded, personal history cursors. A moving view, not a stored snapshot."""
from __future__ import annotations

import json
import math
import re
import struct

from synwevo_core import sealed_token
from synwevo_core.errors import ServiceError
from synwevo_core.store import now

CURSOR_TTL = 3600
MAX_CURSOR = 256
_LABEL = b"synwevo/agent-history/v1"
_FORMAT = ">dd17s"


def _bound(user_id, status, q):
    return json.dumps([user_id, status, q or ""], separators=(",", ":")).encode()


def seal_cursor(store, user_id, status, q, row):
    body = struct.pack(_FORMAT, row["updated_at"], now() + CURSOR_TTL, row["id"].encode("ascii"))
    return "h1." + sealed_token.seal(store, _LABEL, body, _bound(user_id, status, q))


def open_cursor(store, user_id, status, q, cursor):
    bad = ServiceError(400, "invalid_cursor", "会话列表位置无效，请重新加载列表。")
    if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR or not cursor.startswith("h1."):
        raise bad
    body = sealed_token.unseal(store, _LABEL, cursor[3:], _bound(user_id, status, q), max_length=MAX_CURSOR)
    if body is None or len(body) != struct.calcsize(_FORMAT):
        raise bad
    timestamp, expires, raw_id = struct.unpack(_FORMAT, body)
    if not math.isfinite(timestamp) or not math.isfinite(expires) or not re.fullmatch(rb"conv_[a-f0-9]{12}", raw_id):
        raise bad
    if now() >= expires:
        raise ServiceError(410, "cursor_expired", "会话列表已过期，请重新加载列表。")
    return timestamp, raw_id.decode("ascii")
