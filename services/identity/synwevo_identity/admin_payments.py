"""Platform administration of money: payment settings, the finance view, grants, freezes.

Permissions (synwevo_core.platform_access): ``platform.billing.view`` reads, ``platform.billing.manage``
writes; both belong to the super administrator and to the ``finance`` role, to no other role.
Writes are dangerous operations (a strict, recent re-authentication), carry a mandatory reason,
are idempotent per actor and key (``admin_operations``), and commit with their audit row.

The finance view shows amounts and metadata only — who paid or received how much, when, how and
why — never a conversation, a prompt, a document or a card number. Organization payers are named
by their organization; persons by their address, which a finance administrator needs to answer a
dispute. The Stripe secret key and webhook secret are write-only: no response, audit row or log
line contains them; the state says only whether they are configured.

Routes (prefix ``/api/admin/billing``):

  GET  /payments                      payment settings state (no secrets) and the webhook address
  GET  /payments/readiness            read-only Stripe default-channel diagnostic, saved keys only
  POST /payments/publish              publish a new settings version (and replace/revoke secrets)
  GET  /finance                       totals, recent top-ups, grants, chargebacks, unused balances
  POST /grants                        credit a person or an organization, with a reason
  POST /freezes/{freeze_id}/lift      end a dispute freeze, with a reason
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from synwevo_core.billing import BillingError, Ledger
from synwevo_core.billing.payment_settings import PROVIDER, PaymentSettings
from synwevo_core.billing.wallet_config import cny_unit, environment_from_env, fen_to_units, units_from_env
from synwevo_core.errors import ServiceError
from synwevo_core.oidc import issuer
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import canonical

VIEW, MANAGE = "platform.billing.view", "platform.billing.manage"
WEBHOOK_PATH = "/api/auth/payments/stripe/webhook"
#: Japan's Payment Services Act counts unused prepaid balances on these base dates (§4.3); the
#: day ends at 24:00 Japan Standard Time (UTC+9), the jurisdiction the report exists for.
JST = timezone(timedelta(hours=9))
BASE_DATES = ((3, 31), (9, 30))
LIST_LIMIT = 50


def _reason(body: dict) -> str:
    reason = body.get("reason")
    if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
        raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
    return reason.strip()


def _key(body: dict) -> str:
    key = body.get("idempotency_key")
    if not isinstance(key, str) or not 16 <= len(key) <= 128 or not all(c.isalnum() or c in "_-" for c in key):
        raise ServiceError(400, "billing_admin_invalid", "请求字段无效，请检查后重试")
    return key


def base_dates(now: float, count: int = 4) -> list[tuple[str, int]]:
    """The last ``count`` base dates that have ended, newest first: (YYYY-MM-DD, end timestamp)."""
    out = []
    year = datetime.fromtimestamp(now, JST).year + 1
    while len(out) < count:
        for month, day in reversed(BASE_DATES):
            end = datetime(year, month, day, tzinfo=JST) + timedelta(days=1)
            if end.timestamp() <= now and len(out) < count:
                out.append((f"{year:04d}-{month:02d}-{day:02d}", int(end.timestamp()) - 1))
        year -= 1
    return out


def build_router(store, audit) -> APIRouter:
    router = APIRouter(prefix="/api/admin/billing", tags=["admin"])
    environment = environment_from_env()
    units = units_from_env()
    unit = cny_unit(units)
    ledger = Ledger(store, environment=environment, namespace="admin-billing") if environment else None

    def actor(request, permission, *, dangerous=False):
        return require_platform(request, permission, store=store, audit=audit, dangerous=dangerous)

    def configured():
        if environment is None:
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        return PaymentSettings(store, environment=environment)

    def replay(request, user, action, key, request_hash):
        previous = store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?", (user["id"], key))
        if previous:
            if previous["request_hash"] != request_hash or previous["action"] != action:
                raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
            return json.loads(previous["result_json"])
        return None

    def record(transaction, user, action, target, key, request_hash, result):
        transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,"
                            "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                            (result["operation_id"], user["id"], action, target, key, request_hash,
                             canonical(result), time.time()))

    def state(settings):
        if settings is None:
            return {"environment": None, "provider": PROVIDER, "revision": 0, "spec": None,
                    "credential_revision": 0, "credential_configured": False, "actor_id": None,
                    "created_at": None, "reason": None, "operation_id": None,
                    "status": "unconfigured", "wallet_unit": None, "webhook_url": issuer() + WEBHOOK_PATH,
                    "webhook_events": WEBHOOK_EVENTS, "open_checkouts": {"count": 0, "until": None}}
        current = settings.read()
        # Checkouts that can still be paid: revoking the keys would strand them (payments-1).
        return {**current, "status": "configured" if current["spec"] else "unconfigured",
                "wallet_unit": {"code": unit.code, "scale": unit.scale} if unit else None,
                "webhook_url": issuer() + WEBHOOK_PATH, "webhook_events": WEBHOOK_EVENTS,
                "open_checkouts": settings.open_checkouts()}

    @router.get("/payments")
    def read(request: Request):
        actor(request, VIEW)
        return state(PaymentSettings(store, environment=environment) if environment else None)

    @router.get("/payments/readiness")
    def readiness(request: Request):
        actor(request, VIEW)
        settings = configured()
        saved = settings.inspection_credentials()
        if saved is None:
            raise ServiceError(409, "payment_readiness_no_keys", "请先通过发布流程保存支付密钥，再检查已保存的支付渠道")
        # No network request inside a Store transaction. Keys never reach the response.
        result = request.app.state.payment_readiness.inspect(saved.secret_key, environment=environment)
        actor(request, VIEW)
        current = settings.read()
        if current["revision"] != saved.revision:
            raise ServiceError(409, "payment_revision_conflict", "支付配置已变化，请保留输入并重新读取")
        return JSONResponse({**result, "environment": environment, "revision": current["revision"],
                             "credential_revision": current["credential_revision"]},
                            headers={"Cache-Control": "no-store"})

    @router.post("/payments/publish")
    def publish(request: Request, body: dict = Body(...)):
        user = actor(request, MANAGE, dangerous=True)
        action = "admin.billing.payments.publish"
        try:
            settings = configured()
            reason, key = _reason(body), _key(body)
            if set(body) - {"expected_revision", "spec", "credential", "reason", "idempotency_key"}:
                raise ServiceError(400, "payment_config_invalid", "支付配置字段无效，请检查套餐、金额与密钥")
            from synwevo_core.billing.payment_settings import CredentialChange, _valid
            credential = _valid(CredentialChange, body.get("credential"))
            commitment = credential.commitment()
            request_hash = hashlib.sha256(canonical({"action": action, "environment": environment,
                "revision": body.get("expected_revision"), "spec": body.get("spec"), "credential": commitment,
                "reason": reason}).encode()).hexdigest()
            with store.tx() as transaction:
                user = actor(request, MANAGE, dangerous=True)
                previous = replay(request, user, action, key, request_hash)
                if previous is not None:
                    return previous
                before = settings.read()
                stranded = settings.open_checkouts()["count"] if credential.override_open_checkouts else 0
                operation_id = new_id("ao")
                saved = settings.append(body.get("spec"), credential, expected_revision=body.get("expected_revision"),
                                        actor_id=user["id"], reason=reason, operation_id=operation_id)
                result = {"operation_id": operation_id, "status": "succeeded", "state": state(settings)}
                audit(request, action, actor_id=user["id"], object_kind="payment_settings", object_id=environment,
                      workspace_given=True, result="ok",
                      meta={"operation_id": operation_id, "reason": reason, "before_revision": before["revision"],
                            "revision": saved["revision"], "credential_action": commitment["action"],
                            "enabled": saved["spec"]["enabled"],
                            **({"override_open_checkouts": True, "open_checkouts": stranded}
                               if credential.override_open_checkouts else {})})
                record(transaction, user, action, environment, key, request_hash, result)
                return result
        except ServiceError as exc:
            audit(request, action, actor_id=user["id"], object_kind="payment_settings", object_id=environment or "-",
                  workspace_given=True, result="denied" if exc.status < 500 else "error", meta={"code": exc.code})
            raise

    @router.get("/finance")
    def finance(request: Request):
        actor(request, VIEW)
        if environment is None or unit is None:
            return {"environment": environment, "configured": False, "currency": "CNY", "unit": None,
                    "totals": None, "payments": [], "grants": [], "chargebacks": [], "base_dates": []}
        now = time.time()
        payments = store.all(
            "SELECT t.order_id,t.payer_kind,t.payer_id,t.amount_minor,t.currency,t.status,t.method,t.created_at,"
            "t.expires_at,t.credited_at,t.failure_code,u.email AS initiator_email,o.name AS organization_name,"
            "pu.email AS payer_email FROM payment_topups t JOIN users u ON u.id=t.initiator_id "
            "LEFT JOIN organizations o ON t.payer_kind='organization' AND o.id=t.payer_id "
            "LEFT JOIN users pu ON t.payer_kind='user' AND pu.id=t.payer_id "
            "WHERE t.environment=? ORDER BY t.created_at DESC LIMIT ?", (environment, LIST_LIMIT))
        for row in payments:
            if row["status"] == "pending" and row["expires_at"] and row["expires_at"] <= now:
                row["status"] = "expired"
        grants = store.all(
            "SELECT g.id,g.kind,g.payer_kind,g.payer_id,g.amount,g.reason,g.actor_id,g.created_at,"
            "u.email AS actor_email,pu.email AS payer_email,o.name AS organization_name FROM billing_grants g "
            "LEFT JOIN users u ON u.id=g.actor_id LEFT JOIN users pu ON g.payer_kind='user' AND pu.id=g.payer_id "
            "LEFT JOIN organizations o ON g.payer_kind='organization' AND o.id=g.payer_id "
            "WHERE g.environment=? AND g.unit=? ORDER BY g.created_at DESC LIMIT ?", (environment, unit.code, LIST_LIMIT))
        chargebacks = store.all(
            "SELECT c.id,c.dispute_id,c.amount_minor,c.currency,c.disputed_units,c.recovered_units,c.outstanding_units,"
            "c.created_at,a.kind AS account_kind,a.owner,f.id AS freeze_id,f.lifted_at,f.lift_reason,"
            "pu.email AS payer_email,o.name AS organization_name FROM billing_chargebacks c "
            "JOIN billing_accounts a ON a.id=c.account_id LEFT JOIN billing_account_freezes f ON f.chargeback_id=c.id "
            "LEFT JOIN users pu ON a.kind='wallet' AND pu.id=a.owner "
            "LEFT JOIN organizations o ON a.kind='org_wallet' AND o.id=a.owner "
            "WHERE c.environment=? ORDER BY c.created_at DESC LIMIT ?", (environment, LIST_LIMIT))
        for row in chargebacks:
            row["payer_kind"] = "organization" if row.pop("account_kind") == "org_wallet" else "user"
            row["payer_id"] = row.pop("owner")
            row["frozen"] = row["freeze_id"] is not None and row["lifted_at"] is None
        totals = store.one(
            "SELECT COALESCE(SUM(CASE WHEN status='paid' THEN amount_minor END),0) AS paid_minor,"
            "COUNT(CASE WHEN status='paid' THEN 1 END) AS paid_count FROM payment_topups WHERE environment=?",
            (environment,))
        totals.update(store.one(
            "SELECT COALESCE(SUM(CASE WHEN kind='welcome' THEN amount END),0) AS welcome_units,"
            "COALESCE(SUM(CASE WHEN kind='admin' THEN amount END),0) AS admin_grant_units "
            "FROM billing_grants WHERE environment=? AND unit=?", (environment, unit.code)))
        totals.update(store.one(
            "SELECT COALESCE(SUM(recovered_units),0) AS recovered_units,COALESCE(SUM(outstanding_units),0) AS outstanding_units,"
            "COUNT(*) AS chargeback_count FROM billing_chargebacks WHERE environment=?", (environment,)))
        totals["webhook_failures"] = store.one(
            "SELECT COUNT(*) AS n FROM payment_webhook_inbox WHERE environment=? AND status IN ('failed','rejected')",
            (environment,))["n"]
        totals["frozen_wallets"] = store.one(
            "SELECT COUNT(DISTINCT f.account_id) AS n FROM billing_account_freezes f JOIN billing_accounts a ON a.id=f.account_id "
            "WHERE f.lifted_at IS NULL AND a.environment=?", (environment,))["n"]

        def unused(cutoff):
            row = store.one(
                "SELECT COALESCE(SUM(CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END),0) AS units,"
                "COUNT(DISTINCT CASE WHEN a.kind='wallet' THEN a.id END) AS persons,"
                "COUNT(DISTINCT CASE WHEN a.kind='org_wallet' THEN a.id END) AS organizations "
                "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account,j.credit_account) "
                "WHERE a.kind IN ('wallet','org_wallet') AND a.environment=? AND a.unit=? AND j.created_at<=?",
                (environment, unit.code, cutoff))
            kinds = {r["kind"]: r["units"] for r in store.all(
                "SELECT j.kind,COALESCE(SUM(CASE WHEN a.id=j.credit_account THEN j.amount ELSE -j.amount END),0) AS units "
                "FROM billing_journal j JOIN billing_accounts a ON a.id IN (j.debit_account,j.credit_account) "
                "WHERE a.kind IN ('wallet','org_wallet') AND a.environment=? AND a.unit=? AND j.created_at<=? "
                "GROUP BY j.kind", (environment, unit.code, cutoff))}
            return {"unused_units": row["units"], "wallets_with_history": row["persons"] + row["organizations"],
                    "by_kind": kinds}

        dates = [{"date": label, "cutoff": cutoff, **unused(cutoff)} for label, cutoff in base_dates(now)]
        return {"environment": environment, "configured": True, "currency": "CNY",
                "unit": {"code": unit.code, "scale": unit.scale}, "totals": totals, "payments": payments,
                "grants": grants, "chargebacks": chargebacks,
                "current": {"cutoff": int(now), **unused(int(now))}, "base_dates": dates,
                "base_date_timezone": "Asia/Tokyo"}

    @router.post("/grants")
    def grant(request: Request, body: dict = Body(...)):
        user = actor(request, MANAGE, dangerous=True)
        action = "admin.billing.grant"
        try:
            if ledger is None or unit is None:
                raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
            reason, key = _reason(body), _key(body)
            if set(body) - {"payer_kind", "payer_id", "email", "amount_minor", "reason", "idempotency_key"}:
                raise ServiceError(400, "billing_admin_invalid", "请求字段无效，请检查后重试")
            kind, amount = body.get("payer_kind"), body.get("amount_minor")
            if kind not in ("user", "organization") or type(amount) is not int or not 1 <= amount <= 1_000_000:
                raise ServiceError(400, "billing_admin_invalid", "请求字段无效，请检查后重试")
            if kind == "user":
                email = body.get("email")
                target = store.one("SELECT id FROM users WHERE lower(email)=lower(?) AND status='active' AND deleted_at IS NULL",
                                   (email,)) if isinstance(email, str) and len(email) <= 254 else None
                target = target or (store.one("SELECT id FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                                              (body.get("payer_id"),)) if isinstance(body.get("payer_id"), str) else None)
            else:
                target = store.one("SELECT id FROM organizations WHERE id=? AND status='active' AND deleted_at IS NULL",
                                   (body.get("payer_id"),)) if isinstance(body.get("payer_id"), str) else None
            if not target:
                raise ServiceError(404, "grant_target_not_found", "找不到要赠送的账户或组织")
            request_hash = hashlib.sha256(canonical({"action": action, "kind": kind, "target": target["id"],
                                                     "amount": amount, "reason": reason}).encode()).hexdigest()
            with store.tx():
                user = actor(request, MANAGE, dangerous=True)
                previous = replay(request, user, action, key, request_hash)
                if previous is not None:
                    return previous
            operation_id = new_id("ao")
            # The ledger owns its transaction; the operation record and audit follow it. A crash in
            # between leaves the grant (idempotent by its id) and a retry records the operation.
            try:
                row = ledger.grant(grant_id=f"admin:{user['id']}:{key}", kind="admin", payer_kind=kind,
                                   payer_id=target["id"], unit=unit, amount=fen_to_units(amount, unit),
                                   reason=reason, actor_id=user["id"])
            except BillingError as exc:
                if exc.code in ("account_unavailable", "organization_unavailable"):
                    raise ServiceError(404, "grant_target_not_found", "找不到要赠送的账户或组织") from None
                raise ServiceError(409, "grant_refused", "赠送未完成，请核对后重试") from None
            result = {"operation_id": operation_id, "status": "succeeded",
                      "grant": {"id": row["id"], "payer_kind": kind, "payer_id": target["id"], "amount_minor": amount}}
            with store.tx() as transaction:
                user = actor(request, MANAGE)
                audit(request, action, actor_id=user["id"], object_kind="billing_grant", object_id=row["id"],
                      workspace_given=True, result="ok",
                      meta={"operation_id": operation_id, "reason": reason, "payer_kind": kind,
                            "payer_id": target["id"], "amount_minor": amount})
                record(transaction, user, action, row["id"], key, request_hash, result)
            return result
        except ServiceError as exc:
            audit(request, action, actor_id=user["id"], object_kind="billing_grant", object_id="-",
                  workspace_given=True, result="denied" if exc.status < 500 else "error", meta={"code": exc.code})
            raise

    @router.post("/freezes/{freeze_id}/lift")
    def lift(freeze_id: str, request: Request, body: dict = Body(...)):
        user = actor(request, MANAGE, dangerous=True)
        action = "admin.billing.freeze.lift"
        try:
            if ledger is None:
                raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
            reason = _reason(body)
            with store.tx():
                user = actor(request, MANAGE, dangerous=True)
            try:
                result = ledger.lift_freeze(freeze_id, actor_id=user["id"], reason=reason)
            except BillingError as exc:
                if exc.code in ("freeze_not_found", "invalid_identifier"):
                    raise ServiceError(404, "freeze_not_found", "找不到这项冻结") from None
                raise ServiceError(409, "freeze_refused", "解除冻结未完成，请核对后重试") from None
            audit(request, action, actor_id=user["id"], object_kind="billing_freeze", object_id=freeze_id,
                  workspace_given=True, result="ok", meta={"reason": reason, "account_id": result["account_id"]})
            return {"status": "succeeded", "freeze": result}
        except ServiceError as exc:
            audit(request, action, actor_id=user["id"], object_kind="billing_freeze", object_id=freeze_id[:100],
                  workspace_given=True, result="denied" if exc.status < 500 else "error", meta={"code": exc.code})
            raise

    return router


#: The events the Stripe webhook endpoint should be subscribed to (shown on the settings page).
WEBHOOK_EVENTS = ["checkout.session.completed", "checkout.session.async_payment_succeeded",
                  "checkout.session.async_payment_failed", "checkout.session.expired", "charge.succeeded",
                  "charge.dispute.created"]
