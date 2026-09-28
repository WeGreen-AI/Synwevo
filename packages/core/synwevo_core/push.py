"""Web Push (RFC 8030/8188/8291/8292) for the platform inbox: keys, subscriptions, the queue and
the sender.

A browser subscribes through its service worker and hands us an endpoint plus two keys; the
endpoint is a capability URL — whoever holds it can push to that device — so it is stored but
never shown back (only its hash, so a device can recognise itself). A subscription belongs to a
user *and* to the session that created it: signing that session out revokes the subscription,
so a shared computer's next person never receives the previous one's notifications.

Sending uses two small libraries that only need `cryptography` (py-vapid for the VAPID keys and
JWT, http-ece for the payload encryption) and the HTTP client the platform already has; the
popular all-in-one library would have pulled `requests` and `aiohttp` into the image for one
feature. Delivery results are per subscription: 404/410 revoke it (the browser dropped it),
429/5xx retry with backoff, anything else counts as a failure; a subscription that keeps failing
is revoked. Payloads carry the rendered title/body and the link — the same text the inbox shows
in the reader's language — never secrets, and are capped well under the 4 KiB push limit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import urlsplit

from . import env
from .errors import ServiceError
from .store import Store, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_keys (
  id TEXT PRIMARY KEY, private_pem TEXT NOT NULL, public_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')), created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT, endpoint TEXT NOT NULL UNIQUE, endpoint_hash TEXT NOT NULL,
  p256dh TEXT NOT NULL, auth TEXT NOT NULL, user_agent TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  last_sent_at REAL, last_error TEXT, fail_count INTEGER NOT NULL DEFAULT 0, revoked_at REAL, revoked_reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_push_subs_user ON push_subscriptions(user_id, revoked_at);
CREATE INDEX IF NOT EXISTS ix_push_subs_session ON push_subscriptions(session_id);
CREATE TABLE IF NOT EXISTS push_queue (
  notification_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','claimed','sent','skipped','failed')),
  not_before REAL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_push_queue_due ON push_queue(status, not_before);
"""

MAX_SUBSCRIPTIONS_PER_USER = 10
PAYLOAD_MAX_BYTES = 3000           # the push services cap the encrypted body at 4096; keep headroom
TTL_S = 24 * 3600                  # a phone that is off for a day still gets the item; older ones are in the inbox anyway
FAIL_LIMIT = 5
JWT_LIFETIME_S = 12 * 3600         # RFC 8292 §2: at most 24 h


def _b64url_decode(s: str) -> bytes:
    s = (s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


def contact() -> str:
    """The VAPID `sub` claim: who to contact about our pushes (RFC 8292 §2.1)."""
    c = (env.get("PUSH_CONTACT") or env.get("MAIL_FROM") or "").strip()
    if c.startswith(("mailto:", "https://")):
        return c
    return f"mailto:{c}" if "@" in c else ""


def enabled() -> bool:
    return bool(contact()) and (env.get("PUSH") or "on").strip().lower() not in ("off", "0", "false")


class PushKeys:
    """One active VAPID key pair for this deployment, created on first use and kept in the
    database like the OIDC signing keys: a key regenerated at start would orphan every
    subscription, because the browser binds a subscription to the public key it was made with."""

    def __init__(self, store: Store):
        self.store = store

    def active(self) -> dict:
        row = self.store.one("SELECT * FROM push_keys WHERE status='active' ORDER BY created_at DESC LIMIT 1")
        if row:
            return row
        from py_vapid import Vapid02
        v = Vapid02()
        v.generate_keys()
        from cryptography.hazmat.primitives import serialization
        pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        row = {"id": new_id("vk"), "private_pem": v.private_pem().decode(), "public_key": _b64url(pub), "status": "active", "created_at": now()}
        self.store.insert("push_keys", row)
        return row

    def public_key(self) -> str:
        return self.active()["public_key"]

    def vapid(self):
        from py_vapid import Vapid02
        return Vapid02.from_pem(self.active()["private_pem"].encode())


class PushSubscriptions:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def public(row: dict, *, endpoint_hashes: set[str] | None = None) -> dict:
        return {"id": row["id"], "endpoint_hash": row["endpoint_hash"], "user_agent": row.get("user_agent"),
                "created_at": row["created_at"], "last_sent_at": row.get("last_sent_at"), "fail_count": row.get("fail_count") or 0,
                "last_error": row.get("last_error"), "this_device": bool(endpoint_hashes and row["endpoint_hash"] in endpoint_hashes)}

    def add(self, user_id: str, session_id: str | None, subscription: dict, *, user_agent: str | None = None) -> dict:
        """Store what the browser's PushManager returned. Same endpoint again (a re-subscribe, or
        the device now used by another account) replaces the row's owner and keys."""
        if not isinstance(subscription, dict):
            raise ServiceError(400, "invalid_field", "订阅内容无效", {"field": "subscription"})
        endpoint = str(subscription.get("endpoint") or "").strip()
        keys = subscription.get("keys") or {}
        u = urlsplit(endpoint)
        local = u.hostname in ("127.0.0.1", "localhost", "::1")
        if not endpoint or len(endpoint) > 2048 or u.scheme not in ("https",) + (("http",) if local else ()) or not u.netloc:
            raise ServiceError(400, "invalid_field", "推送端点必须是 https 地址", {"field": "endpoint"})
        try:
            p256dh, auth = _b64url_decode(str(keys.get("p256dh") or "")), _b64url_decode(str(keys.get("auth") or ""))
        except (ValueError, TypeError):
            raise ServiceError(400, "invalid_field", "订阅密钥无效", {"field": "keys"}) from None
        if len(p256dh) != 65 or p256dh[0] != 4 or len(auth) < 16:
            raise ServiceError(400, "invalid_field", "订阅密钥无效", {"field": "keys"})
        ts = now()
        ua = (user_agent or "")[:200] or None
        existing = self.store.one("SELECT * FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        if existing:
            self.store.update("push_subscriptions", {"id": existing["id"]},
                              {"user_id": user_id, "session_id": session_id, "p256dh": _b64url(p256dh), "auth": _b64url(auth), "user_agent": ua,
                               "updated_at": ts, "fail_count": 0, "last_error": None, "revoked_at": None, "revoked_reason": None})
            return self.store.one("SELECT * FROM push_subscriptions WHERE id=?", (existing["id"],))
        n = self.store.one("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=? AND revoked_at IS NULL", (user_id,))["n"]
        if n >= MAX_SUBSCRIPTIONS_PER_USER:
            raise ServiceError(409, "too_many_devices", f"最多 {MAX_SUBSCRIPTIONS_PER_USER} 个设备，请先移除不用的设备",
                               {"limit": MAX_SUBSCRIPTIONS_PER_USER})
        row = {"id": new_id("ps"), "user_id": user_id, "session_id": session_id, "endpoint": endpoint, "endpoint_hash": endpoint_hash(endpoint),
               "p256dh": _b64url(p256dh), "auth": _b64url(auth), "user_agent": ua, "created_at": ts, "updated_at": ts,
               "last_sent_at": None, "last_error": None, "fail_count": 0, "revoked_at": None, "revoked_reason": None}
        self.store.insert("push_subscriptions", row)
        return row

    def get(self, user_id: str, sid: str) -> dict | None:
        return self.store.one("SELECT * FROM push_subscriptions WHERE id=? AND user_id=?", (sid, user_id))

    def list_for(self, user_id: str) -> list[dict]:
        return self.store.all("SELECT * FROM push_subscriptions WHERE user_id=? AND revoked_at IS NULL ORDER BY created_at", (user_id,))

    def active_for(self, user_id: str) -> list[dict]:
        """Subscriptions that may still be pushed to: not revoked, and whose creating session is
        still alive (a signed-out session's device is nobody's any more)."""
        ts = now()
        return self.store.all("SELECT p.* FROM push_subscriptions p LEFT JOIN sessions s ON s.id=p.session_id "
                              "WHERE p.user_id=? AND p.revoked_at IS NULL AND (p.session_id IS NULL OR (s.id IS NOT NULL AND s.revoked_at IS NULL "
                              "AND s.idle_expires_at>? AND s.absolute_expires_at>?))", (user_id, ts, ts))

    def has_active(self, user_id: str) -> bool:
        return bool(self.active_for(user_id))

    def revoke(self, user_id: str, sid: str, reason: str) -> bool:
        return self.store.execute("UPDATE push_subscriptions SET revoked_at=?, revoked_reason=?, updated_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                                  (now(), reason, now(), sid, user_id)) > 0

    def revoke_for_session(self, session_id: str, reason: str = "signed_out") -> int:
        return self.store.execute("UPDATE push_subscriptions SET revoked_at=?, revoked_reason=?, updated_at=? WHERE session_id=? AND revoked_at IS NULL",
                                  (now(), reason, now(), session_id))

    def revoke_all_for_user(self, user_id: str, reason: str) -> int:
        return self.store.execute("UPDATE push_subscriptions SET revoked_at=?, revoked_reason=?, updated_at=? WHERE user_id=? AND revoked_at IS NULL",
                                  (now(), reason, now(), user_id))

    def record(self, sid: str, outcome: str, error: str | None = None) -> None:
        if outcome == "sent":
            self.store.execute("UPDATE push_subscriptions SET last_sent_at=?, fail_count=0, last_error=NULL, updated_at=? WHERE id=?", (now(), now(), sid))
        elif outcome == "gone":
            self.store.execute("UPDATE push_subscriptions SET revoked_at=?, revoked_reason='gone', last_error=?, updated_at=? WHERE id=? AND revoked_at IS NULL",
                               (now(), (error or "gone")[:200], now(), sid))
        else:
            row = self.store.one("SELECT fail_count FROM push_subscriptions WHERE id=?", (sid,)) or {}
            n = int(row.get("fail_count") or 0) + 1
            if n >= FAIL_LIMIT:
                self.store.execute("UPDATE push_subscriptions SET fail_count=?, last_error=?, revoked_at=?, revoked_reason='failing', updated_at=? WHERE id=?",
                                   (n, (error or outcome)[:200], now(), now(), sid))
            else:
                self.store.execute("UPDATE push_subscriptions SET fail_count=?, last_error=?, updated_at=? WHERE id=?", (n, (error or outcome)[:200], now(), sid))


def payload_for(item: dict) -> bytes:
    """What the service worker shows: the rendered title/body/link of one inbox item (the
    reader's language, minimal preview honoured upstream), capped for the push limit."""
    body = {"id": item.get("id"), "kind": item.get("kind"), "severity": item.get("severity"),
            "title": (item.get("title") or "")[:120], "body": (item.get("body") or "")[:240], "href": item.get("href"),
            "created_at": item.get("created_at")}
    raw = json.dumps(body, ensure_ascii=False).encode()
    while len(raw) > PAYLOAD_MAX_BYTES and body["body"]:
        body["body"] = body["body"][: max(0, len(body["body"]) - 40)]
        raw = json.dumps(body, ensure_ascii=False).encode()
    return raw


class WebPush:
    """Encrypts and posts one payload to one subscription. `transport(endpoint, headers, body)`
    returns (status_code, text); the default uses the platform's HTTP client, tests inject one."""

    def __init__(self, store: Store, *, transport=None, timeout_s: float = 10.0):
        self.store = store
        self.keys = PushKeys(store)
        self.transport = transport or self._post
        self.timeout_s = timeout_s

    def _post(self, endpoint: str, headers: dict, body: bytes) -> tuple[int, str]:
        import httpx
        with httpx.Client(timeout=self.timeout_s, follow_redirects=False) as client:
            r = client.post(endpoint, content=body, headers=headers)
            return r.status_code, r.text[:200]

    def encrypt(self, sub: dict, data: bytes) -> bytes:
        import http_ece
        from cryptography.hazmat.primitives.asymmetric import ec
        sender = ec.generate_private_key(ec.SECP256R1())
        return http_ece.encrypt(data, private_key=sender, dh=_b64url_decode(sub["p256dh"]), auth_secret=_b64url_decode(sub["auth"]), version="aes128gcm")

    def headers(self, sub: dict, *, ttl: int, urgency: str) -> dict:
        u = urlsplit(sub["endpoint"])
        claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + JWT_LIFETIME_S, "sub": contact()}
        auth = self.keys.vapid().sign(claims)
        return {**auth, "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": urgency}

    def send(self, sub: dict, data: bytes, *, ttl: int = TTL_S, urgency: str = "normal") -> tuple[str, int | None, str | None]:
        """Returns (outcome, http status, error): sent | gone | retry | failed."""
        try:
            body = self.encrypt(sub, data)
            headers = self.headers(sub, ttl=ttl, urgency=urgency)
            status, text = self.transport(sub["endpoint"], headers, body)
        except Exception as e:  # noqa: BLE001 - a push service that cannot be reached is a retry, not a crash
            return "retry", None, f"{type(e).__name__}: {e}"[:200]
        if status in (200, 201, 202):
            return "sent", status, None
        if status in (404, 410):
            return "gone", status, f"push service answered {status}"
        if status == 429 or status >= 500:
            return "retry", status, f"push service answered {status}: {text[:80]}"
        return "failed", status, f"push service answered {status}: {text[:80]}"
