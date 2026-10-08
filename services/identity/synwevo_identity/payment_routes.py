"""Wallet top-up routes on the accounts origin, and the payment provider's webhook.

The shared contract (org-payments package, consumed by the usage pages):

  GET  /api/auth/wallet/topup-options?payer_kind=&payer_id=
       -> {available, reason?, currency, payment_methods, packages: [{id, amount_minor, label}], min_amount_minor, max_amount_minor}
  POST /api/auth/wallet/topups {payer_kind, payer_id, package_id? | amount_minor, idempotency_key?, challenge_token?}
       -> {order_id, status, creation_status, checkout_url?, replayed}
       201 new / 200 replay / 202 verifying; same key with different intent -> 409.
       Omitted keys retain legacy independent creation, with no retry guarantee.
  GET  /api/auth/wallet/topups/{order_id}
       -> {order_id, status: pending|paid|failed|expired, amount_minor, credited_at?, ...}
  GET  /api/auth/wallet/receipts/{order_id}   -> the receipt of a credited order (JSON)
  POST /api/auth/payments/stripe/webhook      -> anonymous; the Stripe-Signature is the authority

Who may pay for whom: a person tops up their own wallet (``payer_kind=user``, ``payer_id`` = their
own id); an organization's wallet only by someone who currently holds ``org.billing.manage`` in
that active organization (owners hold it; others by a custom role). Reading an order follows the
same rule (the person who started it, or a current billing manager of the paying organization).
A person must have a verified address to top up. Every answer is ``private, no-store``.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from synwevo_core.billing.org_payers import manages_billing
from synwevo_core.billing.payment_settings import payment_methods
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import Sessions

from .auth_routes import _challenge
from .payments import TopupService


def build_router(ident: Identity, sessions: Sessions, audit, topups: TopupService) -> APIRouter:
    router = APIRouter(tags=["account"])
    store = ident.store

    def current(request: Request) -> dict:
        live = sessions.current(getattr(request.state, "session", None))
        user = ident.get_user(live["user_id"]) if live else None
        if not user or user["status"] != "active" or user.get("deleted_at") is not None:
            raise ServiceError(401, "unauthorized", "请先登录可用账户")
        return user

    def payer(user: dict, kind, pid) -> tuple[str, str]:
        if kind in (None, "", "user"):
            if pid not in (None, "", user["id"]):
                raise ServiceError(403, "payer_forbidden", "你不能为这个付款方充值或查看其订单")
            return "user", user["id"]
        if kind != "organization" or not isinstance(pid, str) or not pid or len(pid) > 200:
            raise ServiceError(400, "payer_invalid", "付款方无效")
        # The organization-billing rule (org_payers.manages_billing): an active organization's
        # owner or a holder of org.billing.manage; the usage reads use the same function.
        if not manages_billing(store, pid, user["id"]):
            raise ServiceError(403, "payer_forbidden", "你不能为这个付款方充值或查看其订单")
        return "organization", pid

    def private(response: Response) -> None:
        response.headers["Cache-Control"] = "private, no-store"

    def readable(user: dict, order_id: str) -> dict:
        row = topups._row(order_id)
        if row is None:
            raise ServiceError(404, "topup_not_found", "找不到这个充值订单")
        if row["payer_kind"] == "organization" or row["initiator_id"] != user["id"]:
            allowed = False
            if row["payer_kind"] == "organization":
                try:
                    payer(user, "organization", row["payer_id"])
                    allowed = True
                except ServiceError:
                    allowed = False
            if not allowed:
                raise ServiceError(404, "topup_not_found", "找不到这个充值订单")
        return row

    @router.get("/api/auth/wallet/topup-options")
    def options(request: Request, response: Response, payer_kind: str | None = None, payer_id: str | None = None):
        user = current(request)
        kind, pid = payer(user, payer_kind, payer_id)
        private(response)
        resolved, reason = topups.configuration()
        base = {"currency": "CNY", "payer_kind": kind, "payer_id": pid,
                "initiator_id": user["id"], "environment": topups.environment}
        if resolved is None:
            return {**base, "available": False, "reason": reason, "packages": [],
                    "payment_methods": [],
                    "min_amount_minor": None, "max_amount_minor": None}
        spec = resolved.spec
        out = {**base, "available": True, "packages": topups.packages(spec),
               "payment_methods": payment_methods(spec),
               "custom_amount": spec["custom_amount"],
               "min_amount_minor": spec["min_amount_minor"] if spec["custom_amount"] else None,
               "max_amount_minor": spec["max_amount_minor"] if spec["custom_amount"] else None,
               "challenge": "topup" if _challenge(request).enabled else None}
        if not user.get("email_verified_at"):
            out.update(available=False, reason="email_unverified")
        elif topups.frozen(kind, pid):
            out.update(available=False, reason="wallet_frozen")
        return out

    @router.post("/api/auth/wallet/topups", status_code=201)
    def create(request: Request, response: Response, body: dict = Body(...)):
        user = current(request)
        if not isinstance(body, dict) or set(body) - {"payer_kind", "payer_id", "package_id", "amount_minor", "challenge_token",
                                                     "idempotency_key", "initiator_id", "environment"}:
            raise ServiceError(400, "topup_amount_invalid", "请选择一个充值套餐或填写充值金额")
        # New UI pins its original session/environment even for organization payers. A login
        # or deployment switch must not reinterpret its saved request as a new actor's intent.
        if (("initiator_id" in body and body["initiator_id"] != user["id"])
                or ("environment" in body and body["environment"] != topups.environment)):
            raise ServiceError(409, "topup_context_changed", "充值账户或环境已变化，请返回原账户核实订单")
        kind, pid = payer(user, body.get("payer_kind"), body.get("payer_id"))
        if not user.get("email_verified_at"):
            raise ServiceError(403, "email_verification_required", "请先验证邮箱再继续")
        package_id, amount = body.get("package_id"), body.get("amount_minor")
        if (package_id is not None and (not isinstance(package_id, str) or len(package_id) > 40)) or (
                amount is not None and type(amount) is not int):
            raise ServiceError(400, "topup_amount_invalid", "请选择一个充值套餐或填写充值金额")
        if "idempotency_key" in body and body["idempotency_key"] is None:
            raise ServiceError(400, "topup_idempotency_invalid", "充值请求标识无效")
        result = topups.create(initiator=user, payer_kind=kind, payer_id=pid, package_id=package_id, amount_minor=amount,
                               idempotency_key=body.get("idempotency_key"),
                               require_challenge=lambda: _challenge(request).require(request, body, "topup", audit=audit))
        # The amount and payer are financial metadata, not content: the audit row says who
        # started which order, never a card or a provider secret.
        if not result["replayed"]:
            audit(request, "wallet.topup.created", actor_id=user["id"], object_kind="payment_order",
                  object_id=result["order_id"], result="ok", meta={"payer_kind": kind}, workspace_given=True)
        response.status_code = (202 if result["creation_status"] == "verifying" else 200 if result["replayed"] else 201)
        private(response)
        return result

    @router.get("/api/auth/wallet/topups/{order_id}")
    def status(order_id: str, request: Request, response: Response):
        user = current(request)
        row = readable(user, order_id)
        private(response)
        return topups.status(row)

    @router.get("/api/auth/wallet/receipts/{order_id}")
    def receipt(order_id: str, request: Request, response: Response):
        user = current(request)
        row = readable(user, order_id)
        private(response)
        return topups.receipt(row)

    @router.post("/api/auth/payments/stripe/webhook", include_in_schema=False)
    async def webhook(request: Request):
        payload = b""
        async for chunk in request.stream():
            payload += chunk
            if len(payload) > 512 * 1024:
                return JSONResponse({"error": "payload_too_large"}, status_code=400)
        code, body = await run_in_threadpool(topups.webhook, payload, request.headers.get("stripe-signature"))
        return JSONResponse(body, status_code=code, headers={"Cache-Control": "no-store"})

    return router
