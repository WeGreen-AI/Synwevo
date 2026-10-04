"""Verifying webhook requests from Synwevo Docs.

Workspace subscriptions sign every delivery the Standard Webhooks way
(https://www.standardwebhooks.com/)::

    webhook-id:        <event id; the same on every retry and redelivery — dedupe on it>
    webhook-timestamp: <unix seconds>
    webhook-signature: v1,<base64(HMAC-SHA256(key, "<id>.<timestamp>.<raw body>"))> [v1,<…>]

``key`` is the subscription secret: ``whsec_<base64>`` is base64-decoded after the prefix is
removed. While a secret is being rotated the header carries one signature per valid secret,
separated by spaces, and any one of them matching is enough.

Deployment-wide endpoints (configured by the operator) additionally carry the older
``X-DocWeave-Signature: v1=<hex(HMAC-SHA256(secret, "<timestamp>.<raw body>"))>`` with
``X-DocWeave-Timestamp``; `verify_legacy_signature` checks that one.

Always verify the **raw request body bytes** — re-serialising parsed JSON changes the bytes and
breaks the signature — and verify before acting on anything in it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from typing import Iterable, Mapping

from .errors import WebhookVerificationError

__all__ = ["verify_webhook", "verify_legacy_signature", "sign_webhook", "WebhookVerificationError"]

SECRET_PREFIX = "whsec_"
DEFAULT_TOLERANCE = 300
#: Shorter key material is a configuration mistake (an unset variable, a truncated file), and an
#: empty HMAC key would let anyone sign: refused rather than verified with.
MIN_KEY_BYTES = 16


def _lower(headers) -> dict[str, str]:
    items = headers.items() if hasattr(headers, "items") else headers
    out: dict[str, str] = {}
    for k, v in items:
        out[str(k).lower()] = v if isinstance(v, str) else (v.decode("latin-1") if isinstance(v, bytes) else str(v))
    return out


def _key_bytes(secret: str | bytes) -> bytes:
    if isinstance(secret, bytes):
        return secret
    if secret.startswith(SECRET_PREFIX):
        try:
            return base64.b64decode(secret[len(SECRET_PREFIX):], validate=True)
        except (binascii.Error, ValueError):
            pass
    return secret.encode("utf-8")


def _secrets(secret) -> list:
    out = [secret] if isinstance(secret, (str, bytes)) else [s for s in (secret or []) if s]
    if not out:
        raise WebhookVerificationError("没有配置用于校验的密钥")
    if any(len(_key_bytes(s)) < MIN_KEY_BYTES for s in out):
        raise WebhookVerificationError(f"签名密钥为空或过短（至少 {MIN_KEY_BYTES} 字节）：请检查配置")
    return out


def _body_bytes(body) -> bytes:
    if isinstance(body, (bytes, bytearray, memoryview)):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    raise TypeError("body 必须是收到的原始字节（bytes），不是解析后的对象")


def _check_time(ts_raw: str | None, tolerance: int, now: float | None) -> int:
    if not ts_raw:
        raise WebhookVerificationError("缺少时间戳请求头")
    try:
        ts = int(ts_raw.strip())
    except ValueError:
        raise WebhookVerificationError("时间戳不是整数") from None
    t = time.time() if now is None else now
    if abs(t - ts) > tolerance:
        raise WebhookVerificationError(f"时间戳与当前时间相差 {int(abs(t - ts))} 秒，超过允许的 {tolerance} 秒（可能是重放）")
    return ts


def sign_webhook(msg_id: str, timestamp: int, body: bytes, secret: str | bytes) -> str:
    """The ``v1,<base64>`` signature for one secret (what the sender computes)."""
    mac = hmac.new(_key_bytes(secret), f"{msg_id}.{timestamp}.".encode() + _body_bytes(body), hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


def verify_webhook(headers: Mapping[str, str] | Iterable, body: bytes | str,
                   secret: str | bytes | Iterable[str | bytes], tolerance: int = DEFAULT_TOLERANCE, *,
                   now: float | None = None) -> dict:
    """Verify a Standard Webhooks request and return its parsed JSON payload.

    ``secret`` may be a list (for example the old and new secret during your own rollout).
    Raises `WebhookVerificationError` on a missing header, a timestamp outside ``tolerance``
    seconds, no matching signature, or a body that is not a JSON object.
    """
    h = _lower(headers)
    msg_id = (h.get("webhook-id") or "").strip()
    if not msg_id:
        raise WebhookVerificationError("缺少 webhook-id 请求头")
    ts = _check_time(h.get("webhook-timestamp"), tolerance, now)
    offered = [tok.partition(",") for tok in (h.get("webhook-signature") or "").split()]
    offered_v1 = [sig for ver, sep, sig in offered if ver == "v1" and sep and sig]
    if not offered_v1:
        raise WebhookVerificationError("缺少 v1 签名（webhook-signature）")
    raw = _body_bytes(body)
    for s in _secrets(secret):
        want = sign_webhook(msg_id, ts, raw, s).partition(",")[2]
        # compare_digest for every candidate: an early exit on the first byte that differs would
        # tell an attacker how much of a forged signature was right.
        if any(hmac.compare_digest(want.encode(), sig.encode()) for sig in offered_v1):
            return _payload(raw, msg_id)
    raise WebhookVerificationError("签名不匹配")


def verify_legacy_signature(headers: Mapping[str, str] | Iterable, body: bytes | str, secret: str,
                            tolerance: int = DEFAULT_TOLERANCE, *, now: float | None = None) -> dict:
    """Verify the deployment endpoint signature ``X-DocWeave-Signature: v1=<hex>`` and return the payload.

    ``secret`` is the deployment secret as text, used as its UTF-8 bytes; a missing or empty one
    raises `WebhookVerificationError`. No minimum length is imposed here: the server signs with
    whatever non-empty secret the deployment configured, and refusing a short one would reject
    every genuine event of such a deployment."""
    # Before anything else: an unset variable (``os.environ.get(..., "")``) would make the HMAC key
    # empty, and a signature anyone can make would verify.
    if not isinstance(secret, str) or not secret:
        raise WebhookVerificationError("没有配置用于校验的密钥")
    key = secret.encode("utf-8")
    h = _lower(headers)
    ts = _check_time(h.get("x-docweave-timestamp"), tolerance, now)
    sig = (h.get("x-docweave-signature") or "").strip()
    if not sig.startswith("v1="):
        raise WebhookVerificationError("缺少 v1= 签名（X-DocWeave-Signature）")
    raw = _body_bytes(body)
    want = "v1=" + hmac.new(key, f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want.encode(), sig.encode()):
        raise WebhookVerificationError("签名不匹配")
    return _payload(raw, h.get("x-docweave-event-id"))


def _payload(raw: bytes, msg_id: str | None) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise WebhookVerificationError("签名正确，但请求体不是合法的 JSON") from None
    if not isinstance(data, dict):
        raise WebhookVerificationError("签名正确，但请求体不是 JSON 对象")
    if msg_id and data.get("id") not in (None, msg_id):
        # The signed id header and the id inside the body must agree; a receiver deduplicates on
        # one of them and must not be able to be told two different things.
        raise WebhookVerificationError("webhook-id 与请求体中的 id 不一致")
    return data
