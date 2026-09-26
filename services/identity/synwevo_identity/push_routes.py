"""Web Push subscriptions for the platform inbox: the public VAPID key, this person's devices,
subscribing from a service worker, a test push, and removal. Everything is per signed-in user;
the endpoint the browser hands over is a capability URL and is never returned."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, Request
from fastapi.responses import Response

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.notifications import Notifier
from synwevo_core.push import PushKeys, PushSubscriptions, WebPush, enabled, endpoint_hash, payload_for

STATIC = Path(__file__).resolve().parent / "static"


def build_router(store, ident: Identity, sessions, audit, *, notifier: Notifier) -> APIRouter:
    r = APIRouter(prefix="/api/auth/push", tags=["push"])
    keys, subs = PushKeys(store), PushSubscriptions(store)

    def session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def limited(uid: str, bucket: str, limit: int, window_s: int) -> None:
        allowed, wait = ident.window_allows(f"{bucket}:{uid}", limit=limit, window_s=window_s)
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, int(wait or 60))})

    @r.get("/config")
    def config(request: Request):
        """Whether this deployment can push, and the key a browser subscribes with. Signed in
        only: the key is not secret, but there is no reason to hand it to anonymous traffic."""
        session(request)
        if not enabled():
            return {"enabled": False, "public_key": None}
        return {"enabled": True, "public_key": keys.public_key()}

    @r.get("/subscriptions")
    def list_subscriptions(request: Request, endpoint_hash_of: str = ""):
        s = session(request)
        mine = {endpoint_hash_of} if endpoint_hash_of else set()
        return {"items": [PushSubscriptions.public(x, endpoint_hashes=mine) for x in subs.list_for(s["user_id"])]}

    @r.post("/subscriptions", status_code=201)
    def subscribe(request: Request, body: dict = Body(...)):
        s = session(request)
        if not enabled():
            raise ServiceError(409, "push_disabled", "此部署未启用浏览器推送")
        limited(s["user_id"], "push-subscribe", 20, 3600)
        row = subs.add(s["user_id"], s["id"], body.get("subscription") if isinstance(body.get("subscription"), dict) else body,
                       user_agent=request.headers.get("user-agent"))
        audit(request, "auth.push.subscribe", actor_id=s["user_id"], result="ok", meta={"subscription_id": row["id"]})
        return {**PushSubscriptions.public(row, endpoint_hashes={row["endpoint_hash"]}), "endpoint_hash": row["endpoint_hash"]}

    @r.delete("/subscriptions/{sid}")
    def unsubscribe(request: Request, sid: str):
        s = session(request)
        if not subs.revoke(s["user_id"], sid, "removed"):
            raise ServiceError(404, "not_found", "找不到该设备")
        audit(request, "auth.push.unsubscribe", actor_id=s["user_id"], result="ok", meta={"subscription_id": sid})
        return {"ok": True}

    @r.post("/subscriptions/{sid}/test")
    def test_push(request: Request, sid: str):
        """One real push to one device, so the person can see it arrive where they expect."""
        s = session(request)
        limited(s["user_id"], "push-test", 5, 600)
        row = subs.get(s["user_id"], sid)
        if not row or row.get("revoked_at"):
            raise ServiceError(404, "not_found", "找不到该设备")
        user = ident.get_user(s["user_id"]) or {}
        locale = user.get("locale") if user.get("locale") in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
        title = {"zh-Hans": "Synwevo 推送测试", "zh-Hant": "Synwevo 推播測試", "en": "Synwevo push test"}[locale]
        body = {"zh-Hans": "这台设备可以收到通知。", "zh-Hant": "這台裝置可以收到通知。", "en": "This device can receive notifications."}[locale]
        outcome, status, error = WebPush(store).send(row, payload_for({"id": "test", "kind": "test", "severity": "info", "title": title, "body": body,
                                                                        "href": None}), ttl=60, urgency="high")
        subs.record(row["id"], outcome, error)
        audit(request, "auth.push.test", actor_id=s["user_id"], result="ok" if outcome == "sent" else "error",
              meta={"subscription_id": sid, "outcome": outcome, "status": status})
        if outcome != "sent":
            raise ServiceError(502, "push_failed", "推送服务没有接受这条测试消息", {"outcome": outcome, "status": status, "error": error})
        return {"ok": True, "status": status}

    return r


def static_routes() -> APIRouter:
    """The service worker and the web app manifest, served by the account service itself so
    the shell's build stays untouched and the files carry the headers a worker needs."""
    r = APIRouter(tags=["push"])

    @r.get("/sw.js", include_in_schema=False)
    def service_worker():
        return Response((STATIC / "sw.js").read_bytes(), media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @r.get("/brand/email-logo.png", include_in_schema=False)
    def email_logo():
        """The brand image every mail references: a stable public path (no cookie, no fingerprint)
        so mail clients' image proxies can fetch it; a day of caching, and the file itself is
        derived from the confirmed logo (docs/design.md §2.5)."""
        return Response((STATIC / "email-logo.png").read_bytes(), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

    @r.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return Response((STATIC / "manifest.webmanifest").read_bytes(), media_type="application/manifest+json",
                        headers={"Cache-Control": "no-cache"})

    return r


__all__ = ["build_router", "static_routes", "endpoint_hash"]
