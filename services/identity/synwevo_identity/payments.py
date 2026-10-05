"""Wallet top-ups: a payment-provider abstraction, the Stripe adapter, and the webhook inbox.

The flow (agent_deepseek_prompt §4.3, owner decisions §6.1) — nothing in it lets a browser decide
an amount or a credit:

1. ``TopupService.create`` prices the order on the server (an Admin-defined package or a bounded
   custom amount), applies Turnstile (when configured) and per-account velocity limits, records an
   immutable ``billing_orders`` row and a ``payment_topups`` lifecycle row, then asks the provider
   for a hosted checkout and returns its URL.
2. The provider calls ``POST /api/auth/payments/stripe/webhook``. ``verify_signature`` checks the
   ``Stripe-Signature`` header (HMAC-SHA256 of ``"{t}.{raw body}"`` with the endpoint secret, any
   of several ``v1`` signatures, constant-time comparison, a 300-second timestamp tolerance). Only
   then is the event written, once, into ``payment_webhook_inbox`` (a whitelisted fact subset and
   the payload's SHA-256; no customer name, address or card data).
3. Credit happens only on ``checkout.session.completed`` with ``payment_status='paid'`` or on
   ``checkout.session.async_payment_succeeded``, through ``Ledger.credit_verified_payment``, whose
   verifier re-reads that inbox row inside the ledger's own transaction and matches order, amount,
   currency and payment. ``charge.dispute.created`` becomes ``Ledger.record_chargeback`` (recover
   what is left, freeze, never negative). There is no refund flow: top-ups are final (§6.1).
4. The return page polls ``GET /api/auth/wallet/topups/{order_id}``; a receipt exists only for a
   credited order and carries what really happened (amount, currency, method as the provider
   reported it, time, the configured operator name).

The provider secret key and webhook secret come from ``billing.payment_settings`` (sealed). The
Stripe API base is fixed; tests inject an ``httpx.MockTransport``, never a different host.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from typing import Any, Protocol
from urllib.parse import quote, urlencode

import httpx

from synwevo_core.billing import BillingError, DisputeNotice, Ledger, PaymentReceipt, Unit
from synwevo_core.billing.payment_settings import MERCHANT, PROVIDER, PaymentSettings
from synwevo_core.billing.wallet_config import (CURRENCY, EXCHANGE_VERSION, cny_unit, environment_from_env,
                                                fen_to_units, units_from_env)
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, new_id

STRIPE_API = "https://api.stripe.com"
CHECKOUT_HOST = "https://checkout.stripe.com/"
SIGNATURE_TOLERANCE_S = 300
MAX_WEBHOOK_BYTES = 512 * 1024
CHECKOUT_TTL_S = 3600                 # Stripe accepts 30 minutes .. 24 hours
NAMESPACE = "wallet-topup"
CREDIT_EVENTS = ("checkout.session.completed", "checkout.session.async_payment_succeeded")
HANDLED_EVENTS = CREDIT_EVENTS + ("checkout.session.async_payment_failed", "checkout.session.expired",
                                  "charge.succeeded", "charge.dispute.created")
#: The crash-recovery sweep (process_pending) gives a row this many tries in all (deliveries
#: included), each failed one at least min(2^attempts minutes, a day) after the last: about the
#: provider's own three-day retry window. Then the row stays 'failed' for an administrator.
PENDING_MAX_ATTEMPTS = 12
PENDING_BACKOFF_MAX_S = 86400
#: Disputes waiting for their payment's credit; applied as soon as it is credited (_revive).
WAITING_DISPUTE_CODES = ("payment_not_credited", "dispute_payment_unknown")


def _alert(store, environment: str, code: str) -> None:
    """An administrator alert for a payment event that needs a person (billing.risk). Only the
    provider and a stable code travel; an alert that cannot be delivered never undoes the record."""
    from synwevo_core.billing import risk
    try:
        risk.alert_payment_webhook_failure(store, environment=environment, provider=PROVIDER, code=code)
    except Exception:  # noqa: BLE001 - the inbox row and the finance view still show it
        pass


class SignatureError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    """The signature Stripe computes (exposed for tests and for documenting the scheme)."""
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, header: str | None, secret: str, *, now: int,
                     tolerance: int = SIGNATURE_TOLERANCE_S) -> int:
    """Return the signed timestamp, or raise SignatureError. Never compares with ``==``."""
    if not isinstance(header, str) or not header or len(header) > 4096:
        raise SignatureError("missing")
    timestamp, signatures = None, []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isascii() and value.isdecimal() and len(value) <= 12:
            timestamp = int(value)
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("malformed")
    expected = sign(payload, secret, timestamp)
    if not any(hmac.compare_digest(expected.encode(), candidate.encode()) for candidate in signatures):
        raise SignatureError("mismatch")
    if abs(now - timestamp) > tolerance:
        raise SignatureError("stale")
    return timestamp


def _form(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe's bracketed form encoding: a[b][0][c]=v. Booleans as true/false."""
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            out += _form(item, f"{prefix}[{key}]" if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            out += _form(item, f"{prefix}[{index}]")
    elif isinstance(value, bool):
        out.append((prefix, "true" if value else "false"))
    elif value is not None:
        out.append((prefix, str(value)))
    return out


class ProviderUnavailable(Exception):
    """The provider could not create a checkout (network, 5xx, refusal). Nothing was charged."""


class PaymentProvider(Protocol):
    """What a payment provider adapter must do. Crediting never goes through the adapter: it
    happens only from a signature-verified notification in the inbox (``TopupService.webhook``)."""

    name: str

    def create_checkout(self, *, order_id: str, amount_minor: int, currency: str, product_name: str,
                        success_url: str, cancel_url: str, environment: str, expires_at: int) -> "Checkout": ...


@dataclass(frozen=True)
class Checkout:
    session_id: str
    url: str
    expires_at: int


class StripeCheckout:
    """The Stripe adapter: one call, Checkout Session creation (docs.stripe.com/api/checkout/sessions/create,
    read 2026-10-05). ``transport`` is for tests only; the host is always api.stripe.com."""

    name = PROVIDER

    def __init__(self, secret_key: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 15.0):
        self._secret = secret_key
        self._transport = transport
        self._timeout = timeout

    def create_checkout(self, *, order_id: str, amount_minor: int, currency: str, product_name: str,
                        success_url: str, cancel_url: str, environment: str, expires_at: int) -> Checkout:
        params = {
            "mode": "payment",
            "client_reference_id": order_id,
            "line_items": [{"quantity": 1, "price_data": {
                "currency": currency.lower(), "unit_amount": amount_minor,
                "product_data": {"name": product_name}}}],
            "payment_method_types": ["card", "wechat_pay", "alipay"],
            "payment_method_options": {"wechat_pay": {"client": "web"}},
            "success_url": success_url, "cancel_url": cancel_url,
            "expires_at": expires_at,
            "metadata": {"order_id": order_id, "environment": environment},
            "payment_intent_data": {"metadata": {"order_id": order_id, "environment": environment}},
        }
        headers = {"Authorization": "Bearer " + self._secret, "Idempotency-Key": "checkout-" + order_id,
                   "Content-Type": "application/x-www-form-urlencoded"}
        try:
            with httpx.Client(base_url=STRIPE_API, transport=self._transport, timeout=self._timeout,
                              follow_redirects=False, trust_env=False) as client:
                response = client.post("/v1/checkout/sessions", content=urlencode(_form(params)), headers=headers)
        except httpx.HTTPError:
            raise ProviderUnavailable("network") from None
        if response.status_code != 200:
            raise ProviderUnavailable(f"http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise ProviderUnavailable("body") from None
        session_id, url = body.get("id"), body.get("url")
        if (not isinstance(session_id, str) or not session_id.startswith("cs_") or len(session_id) > 200
                or not isinstance(url, str) or not url.startswith(CHECKOUT_HOST) or len(url) > 2048):
            raise ProviderUnavailable("body")
        expiry = body.get("expires_at")
        return Checkout(session_id, url, expiry if type(expiry) is int else expires_at)


def _facts(event: dict) -> dict | None:
    """The whitelisted subset of an event we keep. None when the shape is not the documented one."""
    obj = (event.get("data") or {}).get("object")
    if not isinstance(obj, dict):
        return None
    kind = event["type"]
    if kind.startswith("checkout.session."):
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        payment = obj.get("payment_intent")
        if isinstance(payment, dict):
            payment = payment.get("id")
        return {"object": "checkout.session", "id": obj.get("id"), "status": obj.get("status"),
                "payment_status": obj.get("payment_status"), "payment_intent": payment,
                "amount_total": obj.get("amount_total"), "currency": obj.get("currency"),
                "client_reference_id": obj.get("client_reference_id"),
                "order_id": metadata.get("order_id"), "environment": metadata.get("environment")}
    if kind == "charge.succeeded":
        details = obj.get("payment_method_details") if isinstance(obj.get("payment_method_details"), dict) else {}
        return {"object": "charge", "id": obj.get("id"), "payment_intent": obj.get("payment_intent"),
                "method": details.get("type"), "amount": obj.get("amount"), "currency": obj.get("currency")}
    if kind.startswith("charge.dispute."):
        return {"object": "dispute", "id": obj.get("id"), "payment_intent": obj.get("payment_intent"),
                "charge": obj.get("charge"), "amount": obj.get("amount"), "currency": obj.get("currency"),
                "status": obj.get("status")}
    return {"object": obj.get("object") if isinstance(obj.get("object"), str) else None}


def _inquiry(facts: dict) -> bool:
    """Stripe reports an inquiry (the issuer asked; no funds withdrawn) as a dispute whose status
    starts with ``warning_`` (docs.stripe.com/api/disputes/object, read 2026-10-05)."""
    return str(facts.get("status") or "").startswith("warning_")


def _ident(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 200 and value.isascii() and value.strip() == value \
        and all(c.isalnum() or c in "_-" for c in value)


class TopupService:
    """Top-ups for one billing environment. ``provider_factory(secret_key)`` returns the adapter
    (StripeCheckout by default); tests pass one that uses an httpx.MockTransport."""

    def __init__(self, store: Store, *, environment: str | None, units=(), provider_factory=None,
                 clock=None, return_base: str | None = None):
        self.store = store
        self.environment = environment
        self.unit: Unit | None = cny_unit(units)
        self.clock = clock or (lambda: int(time.time()))
        self.provider_factory = provider_factory or (lambda secret: StripeCheckout(secret))
        self.return_base = return_base
        self.settings = PaymentSettings(store, environment=environment, clock=self.clock) if environment else None
        self.ledger = (Ledger(store, environment=environment, namespace=NAMESPACE, clock=self.clock,
                              verify_payment=self._verify_payment, verify_dispute=self._verify_dispute)
                       if environment else None)

    @classmethod
    def from_host(cls, store: Store, **kwargs) -> "TopupService":
        return cls(store, environment=environment_from_env(), units=units_from_env(), **kwargs)

    # ------------------------------------------------------------------ availability
    def configuration(self):
        """(resolved settings, None) or (None, reason) — reason is a stable code."""
        if self.environment is None or self.settings is None or self.unit is None:
            return None, "not_configured"
        resolved = self.settings.resolve()
        if resolved is None:
            return None, "not_configured"
        return resolved, None

    def frozen(self, payer_kind: str, payer_id: str) -> bool:
        return bool(self.ledger and self.unit) and self.ledger.wallet_frozen(
            payer_kind=payer_kind, payer_id=payer_id, unit=self.unit)

    @staticmethod
    def packages(spec: dict) -> list[dict]:
        return [{"id": p["id"], "amount_minor": p["amount_minor"], "label": p["label"]} for p in spec["packages"]]

    # ------------------------------------------------------------------ velocity
    def _velocity(self, initiator_id: str, amount_minor: int, velocity: dict) -> None:
        now = self.clock()
        rows = self.store.all("SELECT status,amount_minor,created_at,expires_at FROM payment_topups "
                              "WHERE initiator_id=? AND environment=? AND created_at>?",
                              (initiator_id, self.environment, now - 86400))
        hour = sum(1 for r in rows if r["created_at"] > now - 3600)
        pending = sum(1 for r in rows if r["status"] in ("creating", "pending")
                      and (r["expires_at"] is None or r["expires_at"] > now))
        spent = sum(r["amount_minor"] for r in rows if r["status"] != "failed")
        if hour >= velocity["max_orders_per_hour"] or len(rows) >= velocity["max_orders_per_day"]:
            raise ServiceError(429, "topup_rate_limited", "充值订单过于频繁，请稍后再试", {"retry_after": 3600})
        if pending >= velocity["max_pending"]:
            raise ServiceError(409, "topup_pending_limit", "你还有未完成的充值订单，请先完成或等待其过期")
        if spent + amount_minor > velocity["max_amount_per_day_minor"]:
            raise ServiceError(409, "topup_daily_amount", "已超过今日充值金额上限，请明天再试")

    # ------------------------------------------------------------------ create
    def create(self, *, initiator: dict, payer_kind: str, payer_id: str, package_id: str | None,
               amount_minor: int | None) -> dict:
        resolved, reason = self.configuration()
        if resolved is None:
            raise ServiceError(503, "topup_not_configured", "充值暂未开放")
        spec = resolved.spec
        if (package_id is None) == (amount_minor is None):
            raise ServiceError(400, "topup_amount_invalid", "请选择一个充值套餐或填写充值金额")
        if package_id is not None:
            package = next((p for p in spec["packages"] if p["id"] == package_id), None)
            if package is None:
                raise ServiceError(400, "topup_amount_invalid", "请选择一个充值套餐或填写充值金额")
            amount = package["amount_minor"]
        else:
            if not spec["custom_amount"] or type(amount_minor) is not int or not (
                    spec["min_amount_minor"] <= amount_minor <= spec["max_amount_minor"]):
                raise ServiceError(400, "topup_amount_invalid", "请选择一个充值套餐或填写充值金额")
            amount = amount_minor
        if self.frozen(payer_kind, payer_id):
            raise ServiceError(409, "wallet_frozen", "该钱包因支付争议已被冻结，暂不能充值")
        order_id = new_id("top")
        credit = fen_to_units(amount, self.unit)
        now = self.clock()
        expires = now + CHECKOUT_TTL_S
        # The limits and the order they admit are one write transaction, so two requests at once
        # (a burst of card tests, a double click) cannot both pass the open-order, hourly, daily
        # or amount limit. The ledger order follows in the ledger's own transaction.
        with self.store.tx():
            self._velocity(initiator["id"], amount, spec["velocity"])
            self.store.execute(
                "INSERT INTO payment_topups(order_id,environment,provider,payer_kind,payer_id,initiator_id,package_id,"
                "amount_minor,currency,unit,credit_units,settings_revision,status,created_at,expires_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'creating',?,?)",
                (order_id, self.environment, PROVIDER, payer_kind, payer_id, initiator["id"], package_id, amount,
                 CURRENCY, self.unit.code, credit, resolved.revision, now, expires))
        try:
            self.ledger.create_payment_order(
                order_id=order_id, payer_user_id=initiator["id"], provider=PROVIDER, merchant_id=MERCHANT,
                amount_minor=amount, currency=CURRENCY, unit=self.unit, credit_units=credit,
                exchange_version=EXCHANGE_VERSION, payer_kind=payer_kind,
                payer_org_id=payer_id if payer_kind == "organization" else None)
        except BillingError as exc:
            self.store.execute("UPDATE payment_topups SET status='failed',failure_code='order_refused' "
                               "WHERE order_id=? AND status='creating'", (order_id,))
            if exc.code in ("organization_unavailable", "account_unavailable"):
                raise ServiceError(409, "topup_payer_unavailable", "该付款方当前不能充值") from None
            raise ServiceError(503, "topup_unavailable", "充值暂不可用，请稍后再试") from None
        # Back to where the payment was started: a person's wallet, or the organization's
        # payments section for an organization top-up. Both pages poll the order status.
        base = (self.return_base or "").rstrip("/")
        page = (f"{base}/organizations/{quote(payer_id, safe='')}" if payer_kind == "organization"
                else f"{base}/account/wallet")
        try:
            checkout = self.provider_factory(resolved.secret_key).create_checkout(
                order_id=order_id, amount_minor=amount, currency=CURRENCY,
                product_name=spec["operator_name"], environment=self.environment, expires_at=expires,
                success_url=f"{page}?topup={order_id}&result=success",
                cancel_url=f"{page}?topup={order_id}&result=cancel")
        except ProviderUnavailable:
            self.store.execute("UPDATE payment_topups SET status='failed',failure_code='provider_unavailable' "
                               "WHERE order_id=? AND status='creating'", (order_id,))
            raise ServiceError(502, "topup_provider_unavailable", "支付服务暂时无法创建收银台，未扣款，请稍后再试") from None
        self.store.execute("UPDATE payment_topups SET status='pending',checkout_session_id=?,checkout_url=?,expires_at=? "
                           "WHERE order_id=? AND status='creating'",
                           (checkout.session_id, checkout.url, checkout.expires_at, order_id))
        return {"order_id": order_id, "checkout_url": checkout.url}

    # ------------------------------------------------------------------ read
    def _row(self, order_id: str) -> dict | None:
        if not _ident(order_id):
            return None
        return self.store.one("SELECT * FROM payment_topups WHERE order_id=? AND environment=?",
                              (order_id, self.environment))

    def status(self, row: dict) -> dict:
        state = row["status"]
        if state == "creating" and row["created_at"] < self.clock() - 300:
            state = "failed"
        elif state == "creating":
            state = "pending"
        elif state == "pending" and row["expires_at"] is not None and row["expires_at"] <= self.clock():
            state = "expired"
        out = {"order_id": row["order_id"], "status": state, "amount_minor": row["amount_minor"],
               "currency": row["currency"], "payer_kind": row["payer_kind"], "payer_id": row["payer_id"],
               "created_at": row["created_at"]}
        if row["credited_at"] is not None:
            out["credited_at"] = row["credited_at"]
        return out

    def receipt(self, row: dict) -> dict:
        if row["status"] != "paid" or row["credited_at"] is None:
            raise ServiceError(409, "receipt_not_available", "订单尚未入账，暂无收据")
        settings = self.settings.read(row["settings_revision"])
        operator = (settings["spec"] or {}).get("operator_name")
        return {"order_id": row["order_id"], "status": "paid", "amount_minor": row["amount_minor"],
                "currency": row["currency"], "method": row["method"], "provider": PROVIDER,
                "provider_reference": row["payment_id"], "paid_at": row["paid_at"],
                "credited_at": row["credited_at"], "operator_name": operator,
                "payer_kind": row["payer_kind"], "payer_id": row["payer_id"]}

    # ------------------------------------------------------------------ verifiers (ledger callbacks)
    def _inbox(self, t, event_id):
        return t.execute("SELECT * FROM payment_webhook_inbox WHERE provider=? AND event_id=? AND environment=? "
                         "AND signature_verified=1", (PROVIDER, event_id, self.environment)).fetchone()

    def _verify_payment(self, t, receipt: PaymentReceipt) -> bool:
        row = self._inbox(t, receipt.event_id)
        if not row or row["type"] not in CREDIT_EVENTS or row["payload_sha256"] != receipt.payload_sha256:
            return False
        facts = json.loads(row["facts_json"])
        return (facts.get("order_id") == receipt.order_id and facts.get("payment_intent") == receipt.payment_id
                and facts.get("amount_total") == receipt.amount_minor
                and str(facts.get("currency", "")).upper() == receipt.currency
                and facts.get("payment_status") == "paid" and receipt.provider == PROVIDER
                and receipt.merchant_id == MERCHANT and receipt.evidence_ref == "stripe-inbox:" + receipt.event_id)

    def _verify_dispute(self, t, notice: DisputeNotice) -> bool:
        row = self._inbox(t, notice.event_id)
        if not row or row["type"] != "charge.dispute.created" or row["payload_sha256"] != notice.payload_sha256:
            return False
        facts = json.loads(row["facts_json"])
        return (facts.get("id") == notice.dispute_id and facts.get("payment_intent") == notice.payment_id
                and facts.get("amount") == notice.amount_minor
                and str(facts.get("currency", "")).upper() == notice.currency
                and _inquiry(facts) == notice.inquiry
                and notice.provider == PROVIDER and notice.merchant_id == MERCHANT)

    # ------------------------------------------------------------------ webhook
    def webhook(self, payload: bytes, signature: str | None) -> tuple[int, dict]:
        """(HTTP status, body). 2xx tells the provider to stop retrying; 5xx asks it to retry."""
        if self.environment is None or self.settings is None:
            return 503, {"error": "payments_not_configured"}
        secret = self.settings.webhook_secret()
        if secret is None:
            return 503, {"error": "payments_not_configured"}
        if len(payload) > MAX_WEBHOOK_BYTES:
            return 400, {"error": "payload_too_large"}
        try:
            verify_signature(payload, signature, secret, now=self.clock())
        except SignatureError as exc:
            return 400, {"error": "signature_invalid", "reason": exc.reason}
        try:
            event = json.loads(payload)
        except ValueError:
            return 400, {"error": "payload_invalid"}
        if (not isinstance(event, dict) or not _ident(event.get("id")) or not str(event.get("id")).startswith("evt_")
                or not isinstance(event.get("type"), str) or len(event["type"]) > 100
                or type(event.get("livemode")) is not bool):
            return 400, {"error": "payload_invalid"}
        if event["livemode"] != (self.environment == "live"):
            return 400, {"error": "environment_mismatch"}
        facts = _facts(event)
        if facts is None:
            return 400, {"error": "payload_invalid"}
        digest = hashlib.sha256(payload).hexdigest()
        encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"))
        with self.store.tx() as t:
            row = t.execute("SELECT * FROM payment_webhook_inbox WHERE provider=? AND event_id=?",
                            (PROVIDER, event["id"])).fetchone()
            if row is None:
                t.execute("INSERT INTO payment_webhook_inbox(provider,event_id,environment,type,payload_sha256,facts_json,"
                          "signature_verified,received_at,status) VALUES (?,?,?,?,?,?,1,?,'received')",
                          (PROVIDER, event["id"], self.environment, event["type"], digest, encoded, self.clock()))
            elif row["facts_json"] != encoded or row["type"] != event["type"] or row["environment"] != self.environment:
                return 400, {"error": "event_conflict"}
            elif row["status"] in ("processed", "ignored", "rejected"):
                return 200, {"received": True, "status": row["status"]}
            t.execute("UPDATE payment_webhook_inbox SET attempts=attempts+1 WHERE provider=? AND event_id=?",
                      (PROVIDER, event["id"]))
        try:
            status, code = self._process(event["id"], event["type"], facts, digest)
        except Exception:  # noqa: BLE001 - recorded as failed and retried by the provider; nothing echoed
            status, code = "failed", "processing_error"
        self.store.execute("UPDATE payment_webhook_inbox SET status=?,result_code=?,processed_at=? "
                           "WHERE provider=? AND event_id=?", (status, code, self.clock(), PROVIDER, event["id"]))
        if status == "failed":
            return 500, {"error": "processing_failed"}
        return 200, {"received": True, "status": status}

    def process_pending(self, limit: int = 50) -> int:
        """Re-run inbox rows left 'received'/'failed' (e.g. a crash after the insert). Bounded.

        Fewest attempts first, so rows that keep failing can never starve a newer crash-left row
        (review payments-2); a failed row waits min(2^attempts minutes, a day) after its last
        try; after PENDING_MAX_ATTEMPTS tries it stays 'failed' (the finance view counts it) and
        administrators are alerted once, instead of it being retried forever."""
        now = self.clock()
        rows = self.store.all("SELECT event_id,type,facts_json,payload_sha256,attempts FROM payment_webhook_inbox "
                              "WHERE environment=? AND status IN ('received','failed') AND attempts<? "
                              "AND (status='received' OR processed_at IS NULL "
                              "OR processed_at+MIN(?,60*(1<<MIN(attempts,20)))<=?) "
                              "ORDER BY attempts,received_at LIMIT ?",
                              (self.environment, PENDING_MAX_ATTEMPTS, PENDING_BACKOFF_MAX_S, now, limit))
        done = 0
        for row in rows:
            status, code = self._replay(row, attempt=True)
            if status == "failed" and row["attempts"] + 1 >= PENDING_MAX_ATTEMPTS:
                _alert(self.store, self.environment, "webhook_retry_exhausted")
            done += status != "failed"
        return done

    def _replay(self, row, *, attempt: bool) -> tuple[str, str | None]:
        try:
            status, code = self._process(row["event_id"], row["type"], json.loads(row["facts_json"]),
                                         row["payload_sha256"])
        except Exception:  # noqa: BLE001
            status, code = "failed", "processing_error"
        self.store.execute("UPDATE payment_webhook_inbox SET status=?,result_code=?,processed_at=?,attempts=attempts+? "
                           "WHERE provider=? AND event_id=?",
                           (status, code, self.clock(), int(attempt), PROVIDER, row["event_id"]))
        return status, code

    def _revive(self, payment_id: str) -> None:
        """Apply disputes that arrived before this payment's credit (out of order), now that it
        is credited: they were kept in the inbox, waiting ('failed') or rejected as unknown."""
        rows = self.store.all("SELECT event_id,type,facts_json,payload_sha256 FROM payment_webhook_inbox "
                              "WHERE provider=? AND environment=? AND type='charge.dispute.created' "
                              "AND status IN ('failed','rejected') AND result_code IN (?,?) "
                              "AND json_extract(facts_json,'$.payment_intent')=?",
                              (PROVIDER, self.environment, *WAITING_DISPUTE_CODES, payment_id))
        for row in rows:
            self._replay(row, attempt=False)

    def _credit_expected(self, payment_id: str) -> bool:
        """Did one of our checkouts report this payment (not rejected), so its credit may follow?"""
        return self.store.one("SELECT 1 AS x FROM payment_webhook_inbox WHERE provider=? AND environment=? "
                              "AND type LIKE 'checkout.session.%' AND status<>'rejected' "
                              "AND json_extract(facts_json,'$.payment_intent')=? LIMIT 1",
                              (PROVIDER, self.environment, payment_id)) is not None

    def _process(self, event_id: str, kind: str, facts: dict, digest: str) -> tuple[str, str | None]:
        if kind not in HANDLED_EVENTS:
            return "ignored", "unhandled_type"
        if kind.startswith("checkout.session."):
            order_id = facts.get("order_id")
            if not _ident(order_id) or facts.get("client_reference_id") not in (None, order_id):
                return "rejected", "order_unknown"
            if facts.get("environment") not in (None, self.environment):
                return "rejected", "environment_mismatch"
            row = self._row(order_id)
            if row is None:
                return "rejected", "order_unknown"
            if facts.get("id") != row["checkout_session_id"] and row["checkout_session_id"] is not None:
                return "rejected", "session_mismatch"
            if kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
                if row["status"] in ("creating", "pending"):
                    self.store.execute("UPDATE payment_topups SET status=?,failure_code=? WHERE order_id=? "
                                       "AND status IN ('creating','pending')",
                                       ("expired" if kind.endswith("expired") else "failed",
                                        kind.rsplit(".", 1)[1], order_id))
                return "processed", None
            if facts.get("payment_status") != "paid":
                return "ignored", "not_paid_yet"       # async methods settle later
            if (facts.get("amount_total") != row["amount_minor"]
                    or str(facts.get("currency", "")).upper() != row["currency"]
                    or not _ident(facts.get("payment_intent"))):
                return "rejected", "amount_mismatch"
            receipt = PaymentReceipt(self.environment, PROVIDER, MERCHANT, facts["payment_intent"], event_id,
                                     order_id, row["amount_minor"], row["currency"], "stripe-inbox:" + event_id, digest)
            try:
                journal = self.ledger.credit_verified_payment(receipt)
            except BillingError as exc:
                if exc.code in ("payment_order_mismatch", "payment_transaction_conflict", "order_already_funded",
                                "payment_event_conflict"):
                    return "rejected", exc.code
                raise
            now = self.clock()
            self.store.execute("UPDATE payment_topups SET status='paid',payment_id=COALESCE(payment_id,?),"
                               "paid_at=COALESCE(paid_at,?),credited_at=COALESCE(credited_at,?),journal_id=COALESCE(journal_id,?),"
                               "failure_code=NULL WHERE order_id=?",
                               (facts["payment_intent"], now, now, journal, order_id))
            method = self.store.one("SELECT facts_json FROM payment_webhook_inbox WHERE provider=? AND type='charge.succeeded' "
                                    "AND json_extract(facts_json,'$.payment_intent')=? LIMIT 1", (PROVIDER, facts["payment_intent"]))
            if method:
                self._method(facts["payment_intent"], json.loads(method["facts_json"]).get("method"))
            self._revive(facts["payment_intent"])
            return "processed", None
        if kind == "charge.succeeded":
            if _ident(facts.get("payment_intent")):
                self._method(facts["payment_intent"], facts.get("method"))
            return "processed", None
        # charge.dispute.created
        if (not _ident(facts.get("id")) or not _ident(facts.get("payment_intent"))
                or type(facts.get("amount")) is not int or facts["amount"] <= 0 or not isinstance(facts.get("currency"), str)):
            return "rejected", "dispute_invalid"
        notice = DisputeNotice(self.environment, PROVIDER, MERCHANT, facts["id"], event_id, facts["payment_intent"],
                               facts["amount"], facts["currency"].upper(), "stripe-inbox:" + event_id, digest,
                               inquiry=_inquiry(facts))
        try:
            self.ledger.record_chargeback(notice)
        except BillingError as exc:
            if exc.code == "payment_not_credited":
                if self._credit_expected(facts["payment_intent"]):
                    # Our checkout reported it and the credit is still to come: retry later
                    # (applied at once when it is credited, _revive).
                    return "failed", exc.code
                # Not a payment credited here (another product on the same provider account, or
                # a rejected top-up). Kept and alerted, never retried forever; applied if this
                # payment is credited later after all.
                _alert(self.store, self.environment, "dispute_payment_unknown")
                return "rejected", "dispute_payment_unknown"
            if exc.code == "dispute_payment_mismatch":
                # Merchant or currency differ from the credited payment: a person must look.
                _alert(self.store, self.environment, exc.code)
                return "rejected", exc.code
            raise
        return "processed", None

    def _method(self, payment_id: str, method) -> None:
        if isinstance(method, str) and method in ("card", "wechat_pay", "alipay"):
            self.store.execute("UPDATE payment_topups SET method=? WHERE provider=? AND payment_id=? AND method IS NULL",
                               (method, PROVIDER, payment_id))
