"""Machine API: webhook subscriptions, their deliveries, and the workspace event feed.

Included by `v1.build_router` under the /api/v1 prefix, behind the same machine authentication
as every other v1 route. There is deliberately no product-UI counterpart: subscriptions and
deliveries are managed through this API and ``python -m synwevo_docs.cli webhooks`` only.

* Managing subscriptions and reading their deliveries needs scope ``admin`` (the platform's
  ``docweave.manage``). The workspace always comes from the credential, never from the request:
  another workspace's subscription answers 404, not 403, so its existence is not disclosed.
* The event feed (``GET /events``) needs ``read``.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from synwevo_core import authority
from synwevo_core.errors import ServiceError
from synwevo_core.idempotency import Idempotency

from .. import webhooks as W


def build_router(app_, views, jobs, auth) -> APIRouter:
    from .v1 import _ox

    r = APIRouter(tags=["v1"])
    store = app_.store

    def principal(request: Request) -> dict:
        p = getattr(request.state, "principal", None)
        if not p:
            raise ServiceError(401, "unauthorized", "缺少 API 凭据")
        return p

    def workspace(p: dict) -> str:
        t = p.get("tenant")
        if not t:
            raise ServiceError(403, "forbidden", "该凭据没有绑定工作空间")
        return t

    def manage(request: Request) -> tuple[dict, str]:
        p = principal(request)
        auth.require(p, "admin")
        return p, workspace(p)

    def service(request: Request) -> W.Subscriptions:
        return W.Subscriptions(store, getattr(request.app.state, "webhooks", None))

    def by(p: dict) -> dict:
        return authority.describe(p, tenant=workspace(p))

    def etag(response: Response, sub: dict) -> dict:
        response.headers["ETag"] = f'"{sub["version"]}"'
        return sub

    def if_match(request: Request, body: dict):
        raw = (request.headers.get("If-Match") or "").strip()
        if raw == "*":
            return None                               # "any current version" (RFC 9110)
        if raw:
            raw = raw[2:] if raw.startswith("W/") else raw
            return raw.strip('"')
        return body.get("version") if isinstance(body, dict) else None

    def reject_idempotency(request: Request) -> None:
        # Secrets are returned once, never put in the generic plaintext replay cache. Until
        # these operations have a durable secret-safe replay contract, reject the header
        # explicitly rather than silently duplicating a subscription/rotation/ping.
        if "Idempotency-Key" in request.headers:
            raise ServiceError(400, "idempotency_not_supported",
                               "此 Webhook 操作不支持 Idempotency-Key", {"field": "Idempotency-Key"})

    # ------------------------------------------------------------ subscriptions
    @r.post("/webhooks/subscriptions", status_code=201, summary="创建 Webhook 订阅（密钥只在此返回一次）", openapi_extra=_ox("admin"))
    def create_subscription(request: Request, response: Response, body: dict = Body(...)):
        p, tenant = manage(request)
        reject_idempotency(request)
        sub = service(request).create(tenant, body, by(p))
        response.headers["Cache-Control"] = "no-store"
        return etag(response, sub)

    @r.get("/webhooks/subscriptions", summary="本工作空间的 Webhook 订阅", openapi_extra=_ox("admin"))
    def list_subscriptions(request: Request):
        _, tenant = manage(request)
        return {"items": service(request).list(tenant), "limit": W.MAX_SUBSCRIPTIONS}

    @r.get("/webhooks/subscriptions/{sid}", summary="Webhook 订阅详情（不含密钥）", openapi_extra=_ox("admin"))
    def get_subscription(request: Request, response: Response, sid: str):
        _, tenant = manage(request)
        return etag(response, service(request).get(tenant, sid))

    @r.patch("/webhooks/subscriptions/{sid}", summary="修改订阅（If-Match 或 version 防止覆盖他人修改）", openapi_extra=_ox("admin"))
    def update_subscription(request: Request, response: Response, sid: str, body: dict = Body(...)):
        p, tenant = manage(request)
        return etag(response, service(request).update(tenant, sid, body, if_match(request, body), by(p)))

    @r.delete("/webhooks/subscriptions/{sid}", summary="删除订阅（未完成的重试随之停止）", openapi_extra=_ox("admin"))
    def delete_subscription(request: Request, sid: str):
        p, tenant = manage(request)
        return service(request).delete(tenant, sid, by(p))

    @r.post("/webhooks/subscriptions/{sid}/pause", summary="暂停订阅（排队中的投递不再发送）", openapi_extra=_ox("admin"))
    def pause_subscription(request: Request, response: Response, sid: str):
        p, tenant = manage(request)
        return etag(response, service(request).pause(tenant, sid, by(p)))

    @r.post("/webhooks/subscriptions/{sid}/resume", summary="恢复订阅（此后的事件才会投递）", openapi_extra=_ox("admin"))
    def resume_subscription(request: Request, response: Response, sid: str):
        p, tenant = manage(request)
        return etag(response, service(request).resume(tenant, sid, by(p)))

    @r.post("/webhooks/subscriptions/{sid}/rotate-secret", summary="更换签名密钥（可设重叠期，期间两个签名并存）", openapi_extra=_ox("admin"))
    def rotate_secret(request: Request, response: Response, sid: str, body: dict = Body(default={})):
        p, tenant = manage(request)
        reject_idempotency(request)
        sub = service(request).rotate_secret(tenant, sid, (body or {}).get("overlap_s"), by(p))
        response.headers["Cache-Control"] = "no-store"
        return etag(response, sub)

    @r.post("/webhooks/subscriptions/{sid}/ping", summary="立即发送一次签名的 webhook.ping（只尝试一次）", openapi_extra=_ox("admin"))
    def ping_subscription(request: Request, sid: str):
        p, tenant = manage(request)
        reject_idempotency(request)
        return service(request).ping(tenant, sid, by(p))

    # ------------------------------------------------------------ deliveries
    @r.get("/webhooks/subscriptions/{sid}/deliveries", summary="投递记录（游标分页，最新在前）", openapi_extra=_ox("admin"))
    def list_deliveries(request: Request, sid: str, status: str | None = None, cursor: str | None = None,
                        limit: int | None = None):
        _, tenant = manage(request)
        return service(request).deliveries(tenant, sid, status, cursor, limit)

    @r.get("/webhooks/subscriptions/{sid}/deliveries/{did}", summary="一条投递记录", openapi_extra=_ox("admin"))
    def get_delivery(request: Request, sid: str, did: str):
        _, tenant = manage(request)
        return service(request).delivery(tenant, sid, did)

    @r.post("/webhooks/subscriptions/{sid}/deliveries/{did}/redeliver", status_code=202, openapi_extra=_ox("admin", idempotent=True),
            summary="重新投递（新记录，webhook-id 不变；限 7 天内的事件）")
    def redeliver(request: Request, sid: str, did: str):
        _, tenant = manage(request)
        with store.tx():
            # Gateway admission may precede a concurrent revocation. Re-verify the actual
            # bearer under the writer lock, for new work AND cached/conflict presentation.
            p = auth.verify(request.headers.get("Authorization"))
            auth.require(p, "admin")
            if workspace(p) != tenant:
                raise ServiceError(403, "credential_authority_changed", "服务凭据权限已改变，请重新请求")
            request.state.principal = p
            svc = service(request)
            return Idempotency(store).run(request,
                "/api/v1/webhooks/subscriptions/{sid}/deliveries/{did}/redeliver", {"sid": sid, "did": did},
                lambda: svc.redeliver(tenant, sid, did, by(p)),
                replay_check=lambda out: svc.replay_delivery(tenant, out["id"]), status=202)

    # ------------------------------------------------------------ event feed
    @r.get("/events", summary="本工作空间的事件流（游标分页；id 与 Webhook 的 webhook-id 相同）", openapi_extra=_ox("read"))
    def events(request: Request, cursor: str | None = None, types: str | None = None, limit: int | None = None):
        p = principal(request)
        auth.require(p, "read")
        return W.events_feed(store, workspace(p), cursor, types, limit)

    return r
