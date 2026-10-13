"""Checkout creation outcomes: a definite provider refusal, an unknown outcome, a stuck creation.

Production 2026-10-13: an enabled revision listed WeChat Pay and Alipay, which the Stripe account
had not activated; Stripe refused every Checkout Session with a 400 error object, the order stayed
'creating' and the person saw "verifying" for ever. These tests pin the fix.

Fixtures only: every Stripe answer below is an httpx.MockTransport FIXTURE (never api.stripe.com on
the wire), the keys are fixture values shaped like test-mode keys, the data directory is temporary.
"""
from __future__ import annotations

import logging
import re
import time
from urllib.parse import parse_qs

import httpx
import pytest

from synwevo_identity import payments
from synwevo_identity.payments import (CREATING_GRACE_S, STRIPE_API_VERSION, ProviderRefused, StripeCheckout,
                                       failure_reason)
from .test_wallet_topups import (FIXTURE_SECRET_KEY, SPEC, cny_booked, deliver, post,  # noqa: F401 - fixture
                                 session_event, world)

PATH = "/api/auth/wallet/topups"
#: What a provider message may carry; none of it may be stored, logged or returned.
FIXTURE_MESSAGE = "FIXTURE provider message: The payment method type provided: wechat_pay is invalid"
FIXTURE_LOG_URL = "https://dashboard.stripe.com/acct_FIXTURE0private/test/logs/req_FIXTURE0private"
DETAIL = re.compile(r"[a-z0-9_.;=]{1,200}")


class PreEndiveStripe:
    """FIXTURE double of POST /v1/checkout/sessions on the pinned pre-endive API version, for an
    account where only card is activated: a request that lists another method is refused with
    Stripe's documented 400 error object, and nothing is created."""

    def __init__(self, world):
        self.inner, self.requests = world["stripe"], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        form = parse_qs(request.content.decode())
        assert not any(key.startswith("allowed_payment_method_types") for key in form), "endive-only parameter"
        methods = [form[key][0] for key in form if key.startswith("payment_method_types[")]
        if set(methods) - {"card"}:
            return httpx.Response(400, json={"error": {
                "type": "invalid_request_error", "param": "payment_method_types", "message": FIXTURE_MESSAGE,
                "request_log_url": FIXTURE_LOG_URL}})
        return self.inner(request)


def answering(world, status, body=None, *, content=None):
    """FIXTURE double that answers every checkout creation with ``status``."""
    calls = []

    def handler(request):
        calls.append(request)
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)

    world["api"].state.topups.provider_factory = lambda secret: StripeCheckout(
        secret, transport=httpx.MockTransport(handler))
    return calls


def keyed(key, package="cny-10"):
    return {"package_id": package, "idempotency_key": key}


def warnings(caplog):
    return [r for r in caplog.records if r.name == "synwevo_identity.payments" and r.levelno == logging.WARNING]


def no_private_text(*texts):
    joined = "\n".join(texts)
    for private in (FIXTURE_MESSAGE, "wechat_pay is invalid", FIXTURE_LOG_URL, "acct_FIXTURE", "req_FIXTURE",
                    FIXTURE_SECRET_KEY, "sk_test_"):
        assert private not in joined, private


# ---------------------------------------------------------------- the pinned API version
def test_checkout_creation_pins_the_stripe_api_version_and_keeps_the_dahlia_parameter(world):
    assert STRIPE_API_VERSION == "2026-04-22.dahlia"
    world["configure"]({**SPEC, "payment_methods": ["card"]})
    r = post(world["login"]("alice"), PATH, keyed("fixture-version-pin"))
    assert r.status_code == 201, r.text
    request = world["stripe"].requests[-1]
    assert request.headers["stripe-version"] == STRIPE_API_VERSION
    form = parse_qs(request.content.decode())
    assert form["payment_method_types[0]"] == ["card"]
    assert not any(key.startswith("allowed_payment_method_types") for key in form)


# ---------------------------------------------------------------- definite refusals
def test_a_refused_payment_method_fails_the_order_with_a_reason_and_frees_its_slot(world, caplog):
    caplog.set_level(logging.WARNING, logger="synwevo_identity.payments")
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_pending": 1}})
    stripe = PreEndiveStripe(world)
    world["api"].state.topups.provider_factory = lambda secret: StripeCheckout(
        secret, transport=httpx.MockTransport(stripe))
    c = world["login"]("alice")
    first = post(c, PATH, keyed("fixture-refused-methods"))
    assert first.status_code == 201, first.text
    reply = first.json()
    assert reply["status"] == "failed" and reply["creation_status"] == "resolved"
    assert reply["checkout_url"] is None and reply["failure_reason"] == "payment_method_not_enabled"
    assert stripe.requests[0].headers["stripe-version"] == STRIPE_API_VERSION
    oid = reply["order_id"]
    row = world["store"].one("SELECT status,failure_code,failure_detail FROM payment_topups WHERE order_id=?", (oid,))
    assert row == {"status": "failed", "failure_code": "provider_refused",
                   "failure_detail": "http_400;type=invalid_request_error;param=payment_method_types"}
    assert DETAIL.fullmatch(row["failure_detail"])
    # One warning, with the order and the sanitized fields only.
    logged = warnings(caplog)
    assert len(logged) == 1
    line = logged[0].getMessage()
    assert oid in line and "http_400" in line and "invalid_request_error" in line and "payment_method_types" in line
    # The same key replays the same resolved failure; the provider is not asked again.
    replay = post(c, PATH, keyed("fixture-refused-methods"))
    assert replay.status_code == 200
    assert {k: replay.json()[k] for k in ("order_id", "status", "creation_status", "checkout_url", "failure_reason")} == {
        "order_id": oid, "status": "failed", "creation_status": "resolved", "checkout_url": None,
        "failure_reason": "payment_method_not_enabled"}
    read = c.get(f"{PATH}/{oid}")
    assert read.json()["status"] == "failed" and read.json()["failure_reason"] == "payment_method_not_enabled"
    assert len(stripe.requests) == 1 and len(warnings(caplog)) == 1
    no_private_text(first.text, replay.text, read.text, line, repr(world["store"].all("SELECT * FROM payment_topups")),
                    repr(world["store"].all("SELECT * FROM audit_events")))
    # The failed order holds no open-order slot: with max_pending=1 a card-only order now opens.
    world["configure"]({**SPEC, "payment_methods": ["card"], "velocity": {**SPEC["velocity"], "max_pending": 1}},
                       {"action": "keep"})
    second = post(c, PATH, keyed("fixture-after-refusal"))
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "pending" and second.json()["checkout_url"].startswith("https://checkout.stripe.com/")
    assert cny_booked(world) == 0


@pytest.mark.parametrize(("status", "error", "reason", "detail"), [
    (401, {"type": "invalid_request_error"}, "provider_configuration", "http_401;type=invalid_request_error"),
    (403, {"type": "permission_error", "code": "secret_key_required"}, "provider_configuration",
     "http_403;type=permission_error;code=secret_key_required"),
    (400, {"type": "invalid_request_error", "code": "api_key_expired"}, "provider_configuration",
     "http_400;type=invalid_request_error;code=api_key_expired"),
    (400, {"type": "invalid_request_error", "code": "payment_method_unactivated"}, "payment_method_not_enabled",
     "http_400;type=invalid_request_error;code=payment_method_unactivated"),
    (400, {"type": "invalid_request_error", "code": "parameter_invalid_integer",
           "param": "line_items[0][price_data][unit_amount]"}, "provider_rejected",
     "http_400;type=invalid_request_error;code=parameter_invalid_integer;param=line_items.0.price_data.unit_amount"),
    (402, {"type": "card_error"}, "provider_rejected", "http_402;type=card_error"),
    (404, {"type": "invalid_request_error", "code": "resource_missing", "param": "Not A Param[x]"}, "provider_rejected",
     "http_404;type=invalid_request_error;code=resource_missing;param=other"),
])
def test_definite_refusals_resolve_with_a_stable_reason(world, caplog, status, error, reason, detail):
    caplog.set_level(logging.WARNING, logger="synwevo_identity.payments")
    world["configure"]()
    calls = answering(world, status, {"error": {**error, "message": FIXTURE_MESSAGE, "request_log_url": FIXTURE_LOG_URL}})
    c = world["login"]("alice")
    r = post(c, PATH, keyed("fixture-refusal"))
    assert r.status_code == 201 and r.json()["status"] == "failed" and r.json()["creation_status"] == "resolved"
    assert r.json()["failure_reason"] == reason and r.json()["checkout_url"] is None
    row = world["store"].one("SELECT status,failure_code,failure_detail FROM payment_topups")
    assert row == {"status": "failed", "failure_code": "provider_refused", "failure_detail": detail}
    assert len(calls) == 1 and calls[0].headers["stripe-version"] == STRIPE_API_VERSION
    assert len(warnings(caplog)) == 1 and "refused" in warnings(caplog)[0].getMessage()
    no_private_text(r.text, warnings(caplog)[0].getMessage(), repr(world["store"].all("SELECT * FROM payment_topups")))


def test_a_method_set_that_cannot_be_sent_is_refused_before_any_request():
    calls = []
    adapter = StripeCheckout(FIXTURE_SECRET_KEY, transport=httpx.MockTransport(lambda request: calls.append(request)))
    for methods in ([], ["card", "card"], ["paypal"]):
        with pytest.raises(ProviderRefused) as exc:
            adapter.create_checkout(order_id="top_fixture", amount_minor=1000, currency="CNY", product_name="Fixture",
                                    success_url="http://fixture.test/s", cancel_url="http://fixture.test/c",
                                    environment="test", expires_at=int(time.time()) + 3600, payment_methods=methods)
        assert exc.value.detail == "local=payment_methods" and exc.value.reason == "provider_configuration"
    assert calls == []


def test_an_unsendable_configuration_fails_the_order_instead_of_verifying_for_ever(world, monkeypatch):
    world["configure"]()
    # FIXTURE: an effective method list the adapter must refuse to send (validation normally prevents it).
    monkeypatch.setattr(payments, "payment_methods", lambda spec: [])
    r = post(world["login"]("alice"), PATH, keyed("fixture-unsendable"))
    assert r.status_code == 201 and r.json()["status"] == "failed"
    assert r.json()["failure_reason"] == "provider_configuration" and not world["stripe"].requests
    assert world["store"].one("SELECT failure_detail FROM payment_topups")["failure_detail"] == "local=payment_methods"


# ---------------------------------------------------------------- unknown outcomes stay unknown
@pytest.mark.parametrize(("status", "body", "content", "detail"), [
    (500, {"error": {"type": "api_error"}}, None, "http_500;type=api_error"),
    (503, None, b"<html>FIXTURE upstream page</html>", "http_503"),
    (409, {"error": {"type": "idempotency_error"}}, None, "http_409;type=idempotency_error"),
    (424, {"error": {"type": "api_error"}}, None, "http_424;type=api_error"),
    (429, {"error": {"type": "invalid_request_error", "code": "rate_limit"}}, None,
     "http_429;type=invalid_request_error;code=rate_limit"),
    # A 4xx proves a refusal only with Stripe's error object, and never for an idempotency error.
    (400, {"error": {"type": "idempotency_error"}}, None, "http_400;type=idempotency_error"),
    (403, None, b"<html>FIXTURE proxy refusal</html>", "http_403"),
    (400, {"unexpected": True}, None, "http_400"),
    # M1/SEC-2: a body nested too deeply to decode, or larger than any Stripe error object, is no
    # error object: an unknown outcome with its warning, never an unlogged 500 with a NULL detail.
    # (Depths: 30 000 levels fit under the 64 KiB cap and exceed the 3.12 decoder's limit; 200 000
    # levels exceed the 3.14 decoder's limit too, and the cap. Either way: an unknown outcome.)
    (400, None, b"[" * 30_000 + b"]" * 30_000, "http_400"),
    (400, None, b"[" * 200_000 + b"]" * 200_000, "http_400"),
    (400, None, b'{"error":{"type":"invalid_request_error","message":"' + b"x" * (64 * 1024) + b'"}}', "http_400"),
    (200, None, b"[" * 200_000 + b"]" * 200_000, "http_200;body"),
])
def test_unknown_outcomes_keep_verifying_with_a_sanitized_detail(world, caplog, status, body, content, detail):
    caplog.set_level(logging.WARNING, logger="synwevo_identity.payments")
    world["configure"]()
    if body is not None and "error" in body:
        body = {"error": {**body["error"], "message": FIXTURE_MESSAGE}}
    calls = answering(world, status, body, content=content)
    c = world["login"]("alice")
    r = post(c, PATH, keyed("fixture-unknown"))
    assert r.status_code == 202 and r.json()["creation_status"] == "verifying" and r.json()["status"] == "pending"
    assert r.json()["checkout_url"] is None and "failure_reason" not in r.json()
    row = world["store"].one("SELECT status,failure_code,failure_detail FROM payment_topups")
    assert row == {"status": "creating", "failure_code": "provider_unknown", "failure_detail": detail}
    assert len(calls) == 1
    assert post(c, PATH, keyed("fixture-unknown")).status_code == 202 and len(calls) == 1, "never redispatched"
    logged = warnings(caplog)
    assert len(logged) == 1 and "outcome unknown" in logged[0].getMessage() and detail in logged[0].getMessage()
    no_private_text(r.text, logged[0].getMessage(), repr(world["store"].all("SELECT * FROM payment_topups")))


def test_a_network_failure_is_unknown_and_logged_without_detail_from_the_exception(world, caplog):
    caplog.set_level(logging.WARNING, logger="synwevo_identity.payments")
    world["configure"]()

    def unreachable(request):
        raise httpx.ConnectTimeout(FIXTURE_MESSAGE, request=request)

    world["api"].state.topups.provider_factory = lambda secret: StripeCheckout(
        secret, transport=httpx.MockTransport(unreachable))
    r = post(world["login"]("alice"), PATH, keyed("fixture-network"))
    assert r.status_code == 202 and r.json()["creation_status"] == "verifying"
    assert world["store"].one("SELECT failure_detail FROM payment_topups")["failure_detail"] == "network"
    assert len(warnings(caplog)) == 1
    no_private_text(r.text, warnings(caplog)[0].getMessage())


# ---------------------------------------------------------------- a stuck creation unsticks
def test_a_stuck_creation_expires_after_the_grace_frees_its_slot_and_still_credits_once(world, monkeypatch):
    world["configure"]({**SPEC, "velocity": {**SPEC["velocity"], "max_pending": 1}})
    service = world["api"].state.topups
    world["stripe"].fail = True                       # FIXTURE 500: outcome unknown
    c = world["login"]("alice")
    stuck = post(c, PATH, keyed("fixture-stuck"))
    assert stuck.status_code == 202
    oid = stuck.json()["order_id"]
    expires = world["store"].one("SELECT expires_at FROM payment_topups WHERE order_id=?", (oid,))["expires_at"]
    world["stripe"].fail = False
    original_clock = service.clock
    # While its checkout could still be open it holds the only slot.
    monkeypatch.setattr(service, "clock", lambda: expires - 1)
    blocked = post(c, PATH, keyed("fixture-while-open"))
    assert blocked.status_code == 409 and blocked.json()["error"] == "topup_pending_limit"
    # Past expiry + grace: expired/resolved on reads and replays, and the slot is free.
    later = expires + CREATING_GRACE_S
    monkeypatch.setattr(service, "clock", lambda: later)
    read = c.get(f"{PATH}/{oid}").json()
    assert read["status"] == "expired" and read["creation_status"] == "resolved" and "failure_reason" not in read
    replay = post(c, PATH, keyed("fixture-stuck"))
    assert replay.status_code == 200 and replay.json()["status"] == "expired" and replay.json()["checkout_url"] is None
    fresh = post(c, PATH, keyed("fixture-after-expiry"))
    assert fresh.status_code == 201 and fresh.json()["order_id"] != oid
    # (The FIXTURE session's own expiry is real time, behind this shifted clock; the row shows the dispatch.)
    opened = world["store"].one("SELECT status,checkout_session_id FROM payment_topups WHERE order_id=?",
                                (fresh.json()["order_id"],))
    assert opened == {"status": "pending", "checkout_session_id": "cs_test_fixture2"}
    assert len(world["stripe"].requests) == 2, "the stuck order was never dispatched again"
    # A late, signed completion for the stuck order is still credited, exactly once.
    event = session_event(oid, session_id="cs_test_fixture_late", pi="pi_fixture_late")
    assert deliver(world, event, at=later).json()["status"] == "processed"
    assert deliver(world, event, at=later).json()["status"] == "processed"
    monkeypatch.setattr(service, "clock", original_clock)
    assert c.get(f"{PATH}/{oid}").json()["status"] == "paid"
    assert cny_booked(world) == 1000 * 10**4
    credited = world["store"].one("SELECT failure_code,failure_detail FROM payment_topups WHERE order_id=?", (oid,))
    assert credited == {"failure_code": None, "failure_detail": None}, "a credited order carries no failure"


@pytest.mark.parametrize("kind", ["checkout.session.expired", "checkout.session.async_payment_failed"])
def test_a_signed_session_event_replaces_a_stale_creation_diagnosis(world, kind):
    """A signed session event proves the checkout existed: its failure code replaces the order's
    "outcome unknown" code, and the creation detail (here the FIXTURE 500) goes with it, so the
    finance view never shows ``expired`` next to a stale ``http_500``."""
    world["configure"]()
    world["stripe"].fail = True                       # FIXTURE 500: outcome unknown
    stuck = post(world["login"]("alice"), PATH, keyed("fixture-stale-detail"))
    assert stuck.status_code == 202
    oid = stuck.json()["order_id"]
    before = world["store"].one("SELECT failure_code,failure_detail FROM payment_topups WHERE order_id=?", (oid,))
    assert before["failure_code"] == "provider_unknown" and before["failure_detail"].startswith("http_500")
    assert deliver(world, session_event(oid, kind=kind, paid=False)).json()["status"] == "processed"
    after = world["store"].one("SELECT status,failure_code,failure_detail FROM payment_topups WHERE order_id=?", (oid,))
    assert after == {"status": "expired" if kind.endswith("expired") else "failed",
                     "failure_code": kind.rsplit(".", 1)[1], "failure_detail": None}


# ---------------------------------------------------------------- the stored detail
def test_stored_details_are_allowlisted_and_map_to_reasons():
    def error(**fields):
        return httpx.Response(400, json={"error": fields})

    assert payments._provider_error(error(type="invalid_request_error", code="Weird Code!", param="metadata[order_id]",
                                          message=FIXTURE_MESSAGE)) == {
        "type": "invalid_request_error", "code": "other", "param": "metadata.order_id"}
    assert payments._provider_error(error(type="acct_fixture0private", code="sk_test_fixture", param="cs_test_x")) == {
        "type": "other", "code": "other", "param": "other"}
    assert payments._provider_error(httpx.Response(400, content=b"not json")) is None

    class TooDeep(httpx.Response):
        def json(self, **kwargs):
            raise RecursionError("FIXTURE: maximum recursion depth exceeded while decoding a JSON array")
    # Whatever depth the running Python's decoder accepts, a body it cannot decode is no error object.
    assert payments._provider_error(TooDeep(400, content=b"[[]]")) is None
    assert payments._provider_error(error(code="no_type")) is None
    assert payments._param("payment_method_options[wechat_pay][client]") == "payment_method_options.wechat_pay.client"
    assert payments._param("payment_method_types[acct_private]") == "other"
    assert failure_reason("provider_refused", "http_400;type=invalid_request_error;param=payment_method_options.wechat_pay.client") \
        == "payment_method_not_enabled"
    assert failure_reason("provider_unknown", "http_400;type=invalid_request_error;param=payment_method_types") is None
    assert failure_reason("dispatch_refused", None) is None
    long = payments._detail({"http": 400, "type": "a" * 60, "code": "b" * 60, "param": "c" * 60, "cause": "d" * 60})
    assert len(long) == 200 and DETAIL.fullmatch(long)
