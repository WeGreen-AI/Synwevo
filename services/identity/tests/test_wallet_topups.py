"""Wallet top-ups end to end on the real accounts application, with fixtures only.

The Stripe API is an httpx.MockTransport fixture (never api.stripe.com); webhook deliveries are
signed here with a fixture endpoint secret using the documented scheme (HMAC-SHA256 of
``"{t}.{body}"``). The keys below are fixture values shaped like Stripe test-mode keys; they are
not real credentials. Isolated temporary data directory; nothing reaches the real data/.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from synwevo_core.billing import Unit
from synwevo_core.billing.payment_settings import PaymentSettings
from synwevo_core.challenge import Policy, Turnstile
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import now
from synwevo_identity.payments import StripeCheckout

PW = "topup-http-fixture-passphrase-123"
FIXTURE_SECRET_KEY = "sk_test_" + "fixture0not0a0real0key0000"
FIXTURE_WEBHOOK_SECRET = "whsec_" + "fixture0not0a0real0endpoint0secret"
CNY = Unit("CNY", 6)
HOOK = "/api/auth/payments/stripe/webhook"
SPEC = {"enabled": True, "operator_name": "Synwevo Fixture Operator",
        "packages": [{"id": "cny-10", "amount_minor": 1000, "label": None},
                     {"id": "cny-50", "amount_minor": 5000, "label": "Fixture 50"}],
        "custom_amount": True, "min_amount_minor": 500, "max_amount_minor": 20000,
        "velocity": {"max_orders_per_hour": 5, "max_orders_per_day": 10, "max_pending": 3,
                     "max_amount_per_day_minor": 100000}}


class FakeStripe:
    """Test double of the Checkout Session endpoint (labelled fixture)."""

    def __init__(self):
        self.requests = []
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.stripe.com" and request.url.path == "/v1/checkout/sessions"
        self.requests.append(request)
        if self.fail:
            return httpx.Response(500, json={"error": {"message": "fixture outage"}})
        n = len(self.requests)
        return httpx.Response(200, json={"id": f"cs_test_fixture{n}", "url": f"https://checkout.stripe.com/c/pay/cs_test_fixture{n}",
                                         "expires_at": int(time.time()) + 3600})


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "disabled", "PUBLIC_URL": "http://testserver", "ISSUER": "http://accounts.testserver",
                       "BILLING_ENVIRONMENT": "test", "TEST_INSTANCE": "1",
                       "BILLING_WALLET_UNITS": json.dumps([{"code": "CNY", "scale": 6}]),
                       "SECRET": "synthetic-topup-http-fixture-key-123456789"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store, ident = api.state.identity.store, api.state.identity
    stripe = FakeStripe()
    api.state.topups.provider_factory = lambda secret: StripeCheckout(secret, transport=httpx.MockTransport(stripe))
    users = {}
    for key in ("alice", "bob", "carol"):
        u = ident.create_user(f"{key}@example.test", PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        users[key] = ident.get_user(u["id"])
    clients = []

    def login(key):
        c = TestClient(api)
        r = c.post("/api/auth/login", json={"email": users[key]["email"], "password": PW})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    def configure(spec=SPEC, credential=None):
        settings = PaymentSettings(store, environment="test")
        with store.tx():
            settings.append(spec, credential or {"action": "replace", "secret_key": FIXTURE_SECRET_KEY,
                                                 "webhook_secret": FIXTURE_WEBHOOK_SECRET},
                            expected_revision=settings.read()["revision"], actor_id=users["carol"]["id"],
                            reason="fixture payment configuration", operation_id="op_" + str(len(clients)) + str(time.time_ns()))

    w = {"api": api, "store": store, "ident": ident, "users": users, "login": login, "stripe": stripe,
         "configure": configure, "anon": TestClient(api)}
    yield w
    for c in clients + [w["anon"]]:
        c.close()


def post(client, path, body):
    return client.post(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def deliver(w, event, *, secret=FIXTURE_WEBHOOK_SECRET, at=None, extra_signatures=()):
    body = json.dumps(event).encode()
    ts = int(at if at is not None else time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    header = ",".join([f"t={ts}", *(f"v1={s}" for s in extra_signatures), f"v1={sig}"])
    return w["anon"].post(HOOK, content=body, headers={"Stripe-Signature": header, "content-type": "application/json"})


def session_event(order_id, *, kind="checkout.session.completed", paid=True, amount=1000, currency="cny",
                  pi="pi_fixture1", event_id=None, session_id="cs_test_fixture1", livemode=False):
    return {"id": event_id or f"evt_{kind.replace('.', '_')}_{order_id}", "object": "event", "type": kind,
            "livemode": livemode, "data": {"object": {
                "id": session_id, "object": "checkout.session", "status": "complete",
                "payment_status": "paid" if paid else "unpaid", "payment_intent": pi, "amount_total": amount,
                "currency": currency, "client_reference_id": order_id,
                "customer_details": {"email": "payer@example.test", "name": "Private Name"},
                "metadata": {"order_id": order_id, "environment": "test"}}}}


def cny_booked(w, key="alice"):
    from synwevo_core.billing import Ledger
    uid = w["users"][key]["id"]
    return Ledger(w["store"], environment="test", namespace="read").balance_for_user(
        requester_user_id=uid, payer_user_id=uid, unit=CNY).booked


# ---------------------------------------------------------------- availability
def test_without_payment_settings_top_up_says_not_configured_and_creates_nothing(world):
    c = world["login"]("alice")
    r = c.get("/api/auth/wallet/topup-options")
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["reason"] == "not_configured"
    assert r.json()["currency"] == "CNY" and r.json()["packages"] == []
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 503 and r.json()["error"] == "topup_not_configured"
    assert world["stripe"].requests == [] and world["store"].all("SELECT * FROM payment_topups") == []
    # a webhook before configuration is refused (the provider retries later), never trusted
    assert deliver(world, session_event("top_x")).status_code == 503


def test_options_list_server_priced_packages(world):
    world["configure"]()
    r = world["login"]("alice").get("/api/auth/wallet/topup-options")
    body = r.json()
    assert body["available"] is True and body["currency"] == "CNY"
    assert body["packages"] == [{"id": "cny-10", "amount_minor": 1000, "label": None},
                                {"id": "cny-50", "amount_minor": 5000, "label": "Fixture 50"}]
    assert (body["min_amount_minor"], body["max_amount_minor"]) == (500, 20000)
    assert FIXTURE_SECRET_KEY not in r.text and FIXTURE_WEBHOOK_SECRET not in r.text
    assert r.headers["cache-control"] == "private, no-store"


# ---------------------------------------------------------------- checkout
def test_a_top_up_creates_a_server_priced_stripe_checkout_and_credits_nothing_yet(world):
    world["configure"]()
    c = world["login"]("alice")
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 201, r.text
    order = r.json()["order_id"]
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    request = world["stripe"].requests[0]
    form = parse_qs(request.content.decode())
    assert request.headers["authorization"] == "Bearer " + FIXTURE_SECRET_KEY
    assert request.headers["idempotency-key"] == "checkout-" + order
    assert form["mode"] == ["payment"]
    assert form["line_items[0][price_data][currency]"] == ["cny"]
    assert form["line_items[0][price_data][unit_amount]"] == ["1000"]
    assert [form[f"payment_method_types[{i}]"][0] for i in range(3)] == ["card", "wechat_pay", "alipay"]
    assert form["payment_method_options[wechat_pay][client]"] == ["web"]
    assert form["success_url"][0].startswith("http://accounts.testserver/account/wallet?topup=" + order)
    assert form["cancel_url"][0].startswith("http://accounts.testserver/account/wallet?topup=" + order)
    assert form["metadata[order_id]"] == [order] and form["client_reference_id"] == [order]
    assert c.get(f"/api/auth/wallet/topups/{order}").json()["status"] == "pending"
    assert cny_booked(world) == 0, "创建订单不入账"
    assert FIXTURE_SECRET_KEY not in json.dumps(world["store"].all("SELECT * FROM audit_events"))


def test_the_browser_cannot_choose_an_amount_outside_the_server_rules(world):
    world["configure"]({**SPEC, "custom_amount": False})
    c = world["login"]("alice")
    for body in ({"amount_minor": 1000}, {"package_id": "nope"}, {"package_id": "cny-10", "amount_minor": 1},
                 {}, {"package_id": "cny-10", "credit_units": 10**9}, {"amount_minor": "1000"}):
        r = post(c, "/api/auth/wallet/topups", body)
        assert r.status_code == 400 and r.json()["error"] == "topup_amount_invalid", (body, r.text)
    world["configure"]({**SPEC})
    for amount in (499, 20001, 0, -5):
        r = post(c, "/api/auth/wallet/topups", {"amount_minor": amount})
        assert r.status_code == 400, amount
    assert post(c, "/api/auth/wallet/topups", {"amount_minor": 777}).status_code == 201
    assert world["stripe"].requests[-1].content.decode().count("unit_amount%5D=777") == 1


def test_a_provider_failure_is_reported_honestly_and_charges_nothing(world):
    world["configure"]()
    world["stripe"].fail = True
    c = world["login"]("alice")
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 502 and r.json()["error"] == "topup_provider_unavailable"
    row = world["store"].one("SELECT status,failure_code FROM payment_topups")
    assert row == {"status": "failed", "failure_code": "provider_unavailable"}


def test_velocity_limits_bound_open_orders(world):
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_pending": 1}})
    c = world["login"]("alice")
    assert post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"}).status_code == 201
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 409 and r.json()["error"] == "topup_pending_limit"
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_amount_per_day_minor": 1500}})
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 409 and r.json()["error"] == "topup_daily_amount"


def test_two_top_ups_at_once_cannot_both_pass_the_open_order_limit(world, monkeypatch):
    """A burst (card testing, a double click) must not slip two orders past ``max_pending``: the
    limit check and the order it admits are one write, so the second request sees the first."""
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_pending": 1}})
    service = world["api"].state.topups
    checked = service._velocity
    barrier = threading.Barrier(2, timeout=1.5)

    def check_then_wait(*args, **kwargs):
        checked(*args, **kwargs)
        try:
            barrier.wait()      # both requests have passed the check before either records its order
        except threading.BrokenBarrierError:
            pass                # the other request is still waiting to be checked: it must see this one

    monkeypatch.setattr(service, "_velocity", check_then_wait)
    clients, codes = [world["login"]("alice"), world["login"]("alice")], []
    threads = [threading.Thread(target=lambda c=c: codes.append(post(c, "/api/auth/wallet/topups",
                                                                        {"package_id": "cny-10"}).status_code))
               for c in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert sorted(codes) == [201, 409]
    assert world["store"].one("SELECT COUNT(*) AS n FROM payment_topups")["n"] == 1
    assert len(world["stripe"].requests) == 1


def test_turnstile_is_required_when_configured(world):
    world["configure"]()
    world["api"].state.challenge = Turnstile(Policy("fixture-site", "fixture-secret", frozenset({"accounts.testserver"}), 5),
                                             transport=lambda form, timeout: (200, {"success": False, "error-codes": ["invalid-input-response"]}))
    c = world["login"]("alice")
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_required"
    assert c.get("/api/auth/wallet/topup-options").json()["challenge"] == "topup"
    assert world["stripe"].requests == []


def test_an_unverified_address_cannot_top_up(world):
    world["configure"]()
    world["store"].execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (world["users"]["bob"]["id"],))
    c = world["login"]("bob")
    assert c.get("/api/auth/wallet/topup-options").json()["reason"] == "email_unverified"


# ---------------------------------------------------------------- webhook and credit
def created(world, key="alice", package="cny-10"):
    c = world["login"](key)
    r = post(c, "/api/auth/wallet/topups", {"package_id": package})
    assert r.status_code == 201, r.text
    session = world["store"].one("SELECT checkout_session_id FROM payment_topups WHERE order_id=?", (r.json()["order_id"],))
    return c, r.json()["order_id"], session["checkout_session_id"]


def test_only_a_correctly_signed_fresh_event_reaches_the_inbox(world):
    world["configure"]()
    c, order, session = created(world)
    event = session_event(order, session_id=session)
    assert deliver(world, event, secret="whsec_" + "fixture0wrong0secret0000000").status_code == 400
    assert deliver(world, event, at=time.time() - 301).status_code == 400, "超出 300 秒容差"
    r = world["anon"].post(HOOK, content=json.dumps(event).encode(), headers={"Stripe-Signature": "t=1"})
    assert r.status_code == 400
    assert world["anon"].post(HOOK, content=b"{}").status_code == 400
    assert world["store"].all("SELECT * FROM payment_webhook_inbox") == [] and cny_booked(world) == 0
    # several v1 signatures (endpoint secret rotation): one valid one is enough
    r = deliver(world, event, extra_signatures=("0" * 64,))
    assert r.status_code == 200 and r.json()["status"] == "processed", r.text
    assert cny_booked(world) == 1000 * 10**4, "10 元 = 1000 分 = 10^7 个单位"


def test_a_paid_checkout_credits_once_whatever_the_provider_redelivers(world):
    world["configure"]()
    c, order, session = created(world)
    event = session_event(order, session_id=session)
    for _ in range(3):
        assert deliver(world, event).status_code == 200
    # a second, different event for the same payment does not credit again
    async_event = session_event(order, kind="checkout.session.async_payment_succeeded", session_id=session)
    assert deliver(world, async_event).status_code == 200
    assert cny_booked(world) == 10**7
    assert world["store"].one("SELECT COUNT(*) AS n FROM billing_payments")["n"] == 1
    status = c.get(f"/api/auth/wallet/topups/{order}").json()
    assert status["status"] == "paid" and status["amount_minor"] == 1000 and status["credited_at"]
    statement = c.get("/api/auth/wallet/statement")
    # the wallet statement route needs its own configured ledger: only check the facts here
    facts = world["store"].one("SELECT facts_json FROM payment_webhook_inbox WHERE event_id=?", (event["id"],))
    assert "Private Name" not in facts["facts_json"] and "payer@example.test" not in facts["facts_json"], \
        "收件箱只保留白名单事实，不存客户姓名或邮箱"
    assert statement.status_code in (200, 503)


def test_unpaid_completion_waits_for_the_async_success(world):
    world["configure"]()
    c, order, session = created(world)
    assert deliver(world, session_event(order, paid=False, session_id=session)).json()["status"] == "ignored"
    assert cny_booked(world) == 0
    paid = session_event(order, kind="checkout.session.async_payment_succeeded", session_id=session)
    assert deliver(world, paid).json()["status"] == "processed"
    assert cny_booked(world) == 10**7


def test_mismatched_or_foreign_events_never_credit(world):
    world["configure"]()
    c, order, session = created(world)
    assert deliver(world, session_event(order, amount=999999, session_id=session,
                                        event_id="evt_amount")).json()["status"] == "rejected"
    assert deliver(world, session_event(order, currency="usd", session_id=session,
                                        event_id="evt_currency")).json()["status"] == "rejected"
    assert deliver(world, session_event("top_unknown", event_id="evt_unknown")).json()["status"] == "rejected"
    assert deliver(world, session_event(order, session_id="cs_test_other", event_id="evt_session")).json()["status"] == "rejected"
    assert deliver(world, session_event(order, livemode=True, session_id=session, event_id="evt_live")).status_code == 400
    assert cny_booked(world) == 0
    assert c.get(f"/api/auth/wallet/topups/{order}").json()["status"] == "pending"
    # the same event id cannot be replayed with different facts
    good = session_event(order, session_id=session, event_id="evt_good")
    assert deliver(world, good).json()["status"] == "processed"
    tampered = session_event(order, session_id=session, event_id="evt_good", pi="pi_other")
    assert deliver(world, tampered).status_code == 400
    assert cny_booked(world) == 10**7


def test_receipt_only_for_a_credited_order_with_what_really_happened(world):
    world["configure"]()
    c, order, session = created(world)
    r = c.get(f"/api/auth/wallet/receipts/{order}")
    assert r.status_code == 409 and r.json()["error"] == "receipt_not_available"
    charge = {"id": "evt_charge_1", "object": "event", "type": "charge.succeeded", "livemode": False,
              "data": {"object": {"id": "ch_fixture1", "object": "charge", "payment_intent": "pi_fixture1",
                                  "amount": 1000, "currency": "cny",
                                  "payment_method_details": {"type": "wechat_pay"}}}}
    assert deliver(world, charge).status_code == 200
    assert deliver(world, session_event(order, session_id=session)).status_code == 200
    receipt = c.get(f"/api/auth/wallet/receipts/{order}").json()
    assert receipt["order_id"] == order and receipt["amount_minor"] == 1000 and receipt["currency"] == "CNY"
    assert receipt["method"] == "wechat_pay" and receipt["operator_name"] == "Synwevo Fixture Operator"
    assert receipt["paid_at"] and receipt["provider_reference"] == "pi_fixture1"
    # someone else cannot read the order or its receipt
    other = world["login"]("bob")
    assert other.get(f"/api/auth/wallet/topups/{order}").status_code == 404
    assert other.get(f"/api/auth/wallet/receipts/{order}").status_code == 404


def test_a_dispute_after_spend_freezes_the_wallet_and_closes_top_up(world):
    world["configure"]()
    c, order, session = created(world)
    deliver(world, session_event(order, session_id=session))
    dispute = {"id": "evt_dispute_1", "object": "event", "type": "charge.dispute.created", "livemode": False,
               "data": {"object": {"id": "dp_fixture1", "object": "dispute", "payment_intent": "pi_fixture1",
                                   "charge": "ch_fixture1", "amount": 1000, "currency": "cny",
                                   "status": "needs_response"}}}
    assert deliver(world, dispute).json()["status"] == "processed"
    assert cny_booked(world) == 0
    row = world["store"].one("SELECT recovered_units,outstanding_units FROM billing_chargebacks")
    assert row == {"recovered_units": 10**7, "outstanding_units": 0}
    options = c.get("/api/auth/wallet/topup-options").json()
    assert options["available"] is False and options["reason"] == "wallet_frozen"
    r = post(c, "/api/auth/wallet/topups", {"package_id": "cny-10"})
    assert r.status_code == 409 and r.json()["error"] == "wallet_frozen"


def test_a_dispute_before_the_credit_is_retried_not_lost(world):
    world["configure"]()
    c, order, session = created(world)
    dispute = {"id": "evt_dispute_early", "object": "event", "type": "charge.dispute.created", "livemode": False,
               "data": {"object": {"id": "dp_early", "object": "dispute", "payment_intent": "pi_fixture1",
                                   "amount": 1000, "currency": "cny", "status": "needs_response"}}}
    assert deliver(world, dispute).status_code == 500, "付款尚未入账：让支付渠道稍后重试"
    deliver(world, session_event(order, session_id=session))
    assert deliver(world, dispute).json()["status"] == "processed"
    assert cny_booked(world) == 0


# ---------------------------------------------------------------- failure alerts
def webhook_alerts(w, uid):
    return w["store"].all("SELECT * FROM notifications WHERE user_id=? "
                          "AND kind='billing.risk.payment_webhook_failed' ORDER BY seq", (uid,))


def alerted_codes(w, uid):
    return [json.loads(r["params"])["code"] for r in webhook_alerts(w, uid)]


def test_failed_or_rejected_webhooks_alert_administrators_once_a_day_per_code_with_no_payload(world, monkeypatch):
    from synwevo_core.platform_access import PlatformAccess
    world["configure"]()
    boss = world["users"]["carol"]["id"]
    PlatformAccess(world["store"]).host_grant(boss, "super_admin", source="fixture:webhook-alerts",
                                              reason="isolated alert recipient")
    c, order, session = created(world)
    event = session_event(order, session_id=session)
    # Signature failures never reach the inbox; administrators still hear of them, by code only.
    assert deliver(world, event, secret="whsec_" + "fixture0wrong0secret0000000").status_code == 400
    assert deliver(world, event, secret="whsec_" + "fixture0wrong0secret0000000").status_code == 400
    assert deliver(world, event, at=time.time() - 301).status_code == 400
    assert alerted_codes(world, boss) == ["signature_mismatch", "signature_stale"], "每种代码每天只告警一次"
    # A rejected event (never credited) and one that failed (the provider retries it).
    assert deliver(world, session_event(order, amount=999999, session_id=session, event_id="evt_amount")).json()["status"] == "rejected"
    early = {"id": "evt_dispute_early", "object": "event", "type": "charge.dispute.created", "livemode": False,
             "data": {"object": {"id": "dp_early", "object": "dispute", "payment_intent": "pi_fixture1",
                                 "amount": 1000, "currency": "cny", "status": "needs_response"}}}
    assert deliver(world, early).status_code == 500
    assert alerted_codes(world, boss)[2:] == ["amount_mismatch", "payment_order_mismatch"]
    # A processed or ignored event raises nothing.
    assert deliver(world, event).json()["status"] == "processed"
    assert deliver(world, session_event(order, paid=False, session_id=session, event_id="evt_unpaid")).json()["status"] == "ignored"
    assert len(alerted_codes(world, boss)) == 4
    # The replay of inbox rows left failed (process_pending) alerts too, under the same rules.
    topups = world["api"].state.topups
    world["store"].execute("UPDATE payment_webhook_inbox SET status='failed' WHERE event_id='evt_dispute_early'")
    monkeypatch.setattr(topups, "_process", lambda *a: (_ for _ in ()).throw(RuntimeError("fixture: ledger unavailable")))
    assert topups.process_pending() == 0
    assert alerted_codes(world, boss)[4:] == ["processing_error"]
    assert topups.process_pending() == 0
    assert len(alerted_codes(world, boss)) == 5, "同一天同一代码不再重复"
    # The next day the same failure is reported again, once.
    import synwevo_core.billing.risk as risk
    tomorrow = time.gmtime(time.time() + 86400)
    monkeypatch.setattr(risk.time, "gmtime", lambda *a: tomorrow)
    topups.process_pending()
    topups.process_pending()
    assert alerted_codes(world, boss)[5:] == ["processing_error"]
    # Only the provider name and a stable code travel: no payload, signature, event or customer detail.
    for row in webhook_alerts(world, boss):
        assert json.loads(row["params"]).keys() == {"provider", "code"} and json.loads(row["params"])["provider"] == "stripe"
        text = json.dumps(row, ensure_ascii=False)
        for secret in (FIXTURE_WEBHOOK_SECRET, "whsec_", "evt_", "pi_fixture1", "dp_early", order, session, "Private Name",
                       "payer@example.test", "v1="):
            assert secret not in text, secret


# ---------------------------------------------------------------- organization payer
def test_only_a_billing_manager_tops_up_an_organization_and_the_money_lands_there(world):
    world["configure"]()
    users, store = world["users"], world["store"]
    org = Organizations(store, world["ident"]).create(users["alice"]["id"], "Fixture Org", "Fixture WS", "fixture-org-topup")
    oid = org["organization"]["id"]
    store.insert("organization_memberships", {"organization_id": oid, "user_id": users["bob"]["id"], "role": "admin",
                                              "created_at": now(), "updated_at": now()})
    bob = world["login"]("bob")
    r = bob.get(f"/api/auth/wallet/topup-options?payer_kind=organization&payer_id={oid}")
    assert r.status_code == 403 and r.json()["error"] == "payer_forbidden", "组织管理员默认不能为组织钱包充值"
    assert post(bob, "/api/auth/wallet/topups", {"payer_kind": "organization", "payer_id": oid,
                                                 "package_id": "cny-10"}).status_code == 403
    alice = world["login"]("alice")
    assert alice.get(f"/api/auth/wallet/topup-options?payer_kind=organization&payer_id={oid}").json()["available"] is True
    r = post(alice, "/api/auth/wallet/topups", {"payer_kind": "organization", "payer_id": oid, "package_id": "cny-50"})
    assert r.status_code == 201, r.text
    order = r.json()["order_id"]
    # The billing manager comes back to the organization's payments, not to their own wallet.
    form = parse_qs(world["stripe"].requests[-1].content.decode())
    assert form["success_url"] == [f"http://accounts.testserver/organizations/{oid}?topup={order}&result=success"]
    assert form["cancel_url"] == [f"http://accounts.testserver/organizations/{oid}?topup={order}&result=cancel"]
    session = store.one("SELECT checkout_session_id FROM payment_topups WHERE order_id=?", (order,))["checkout_session_id"]
    deliver(world, session_event(order, amount=5000, session_id=session, pi="pi_org"))
    assert cny_booked(world, "alice") == 0, "组织充值不进入个人钱包"
    org_wallet = store.one("SELECT b.amount FROM billing_accounts a JOIN billing_balances b ON b.account_id=a.id "
                           "WHERE a.kind='org_wallet' AND a.owner=?", (oid,))
    assert org_wallet["amount"] == 5000 * 10**4
    # someone outside the organization cannot even name it as a payer
    assert world["login"]("carol").get(f"/api/auth/wallet/topup-options?payer_kind=organization&payer_id={oid}").status_code == 403
