"""Publishing an enabled payment version checks the provider first; readiness reports the API version;
the finance view shows why a top-up failed.

Real admin routes and Store; every Stripe answer is an httpx.MockTransport FIXTURE (never
api.stripe.com on the wire); keys are fixture values shaped like test-mode keys; temporary data.
"""
from __future__ import annotations

import json
import time

import httpx
import pytest

from synwevo_core.billing.payment_settings import PaymentSettings
from synwevo_identity.payment_readiness import StripeReadiness
from synwevo_identity.payments import CREATING_GRACE_S, STRIPE_API_VERSION

from .test_admin_payments import (FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET, REASON, SPEC, fixture_readiness, post,
                                  publish, world)  # noqa: F401 - shared isolated app fixture
from .test_payment_readiness import transport_for

ALL = ["card", "wechat_pay", "alipay"]
PUBLISH = "/api/admin/billing/payments/publish"


def install(world, readiness):
    world["api"].state.payment_readiness = readiness


def versions(world):
    return world["store"].all("SELECT revision FROM payment_settings_versions ORDER BY revision")


def publish_audits(world, result):
    return [json.loads(row["meta"]) for row in world["store"].all(
        "SELECT meta FROM audit_events WHERE action='admin.billing.payments.publish' AND result=? ORDER BY seq", (result,))]


def with_ack(c, body_extra, **kwargs):
    spec = kwargs.get("spec", SPEC)
    return post(c, PUBLISH, {"expected_revision": kwargs.get("revision", 0), "spec": spec, "reason": REASON,
                             "idempotency_key": kwargs.get("key", "fixture_publish_ack1"),
                             "credential": kwargs.get("credential") or {"action": "replace", "secret_key": FIXTURE_SECRET_KEY,
                                                                      "webhook_secret": FIXTURE_WEBHOOK_SECRET},
                             **body_extra})


# ---------------------------------------------------------------- refusal on an unavailable method
def test_an_enabled_version_with_an_unavailable_method_is_refused_per_method(world):
    calls = []
    install(world, fixture_readiness({"wechat_pay": False, "alipay": False}, calls=calls))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ALL})
    assert r.status_code == 409 and r.json()["error"] == "payment_methods_unavailable"
    assert r.json()["detail"] == {"methods": ["wechat_pay", "alipay"]}
    assert versions(world) == [] and world["store"].all("SELECT * FROM payment_credentials") == []
    # The check used the key this version would save, with the pinned API version.
    assert {request.url.path for request in calls} == {"/v1/account", "/v1/payment_method_configurations"}
    assert all(request.headers["authorization"] == "Bearer " + FIXTURE_SECRET_KEY for request in calls)
    assert all(request.headers["stripe-version"] == STRIPE_API_VERSION for request in calls)
    denied = publish_audits(world, "denied")
    assert denied[-1] == {"code": "payment_methods_unavailable", "methods": ["wechat_pay", "alipay"]}
    # An acknowledgement does not override a definite "not available".
    acked = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ALL})
    assert acked.status_code == 409 and acked.json()["error"] == "payment_methods_unavailable"
    # Deselecting them publishes, verified.
    ok = publish(c, spec={**SPEC, "payment_methods": ["card"]}, key="fixture_publish_card1")
    assert ok.status_code == 200, ok.text
    assert ok.json()["methods_check"] == "verified" and ok.json()["state"]["spec"]["payment_methods"] == ["card"]
    meta = publish_audits(world, "ok")[-1]
    assert meta["methods_check"] == "verified" and "acknowledge_unverified_methods" not in meta
    for private in (FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET):
        assert private not in r.text and private not in ok.text


# ---------------------------------------------------------------- what counts as "not activated" (PS-1)
def test_a_method_shown_off_but_activated_is_verified_not_refused(world):
    """Stripe's ``available`` is display preference AND capability; the pinned dahlia Checkout lists
    methods explicitly, so a method whose capability is active but whose Dashboard display is off is
    accepted there. Only definite evidence that the method is not activated refuses."""
    install(world, fixture_readiness({"wechat_pay": False},
                                     capabilities={"card_payments": "active", "wechat_pay_payments": "active"}))
    r = publish(world["login"]("fin"), spec={**SPEC, "payment_methods": ["card", "wechat_pay"]})
    assert r.status_code == 200, r.text
    assert r.json()["methods_check"] == "verified"


@pytest.mark.parametrize(("capabilities", "display", "refused"), [
    # Production 2026-10-13: the capability list omits WeChat Pay and the default configuration
    # reports it unavailable (display off): two sources agree, so it is definitely not activated.
    ("from_methods", None, True),
    ({"card_payments": "active", "wechat_pay_payments": "pending"}, None, True),
    ({"card_payments": "active", "wechat_pay_payments": "inactive"}, None, True),
    # No capability list: unavailable with display ON can only mean the capability is not active…
    (None, {"wechat_pay": "on"}, True),
    # …but unavailable with display OFF proves nothing about the capability.
    (None, None, False),
])
def test_only_definite_evidence_refuses_a_method(world, capabilities, display, refused):
    install(world, fixture_readiness({"wechat_pay": False}, capabilities=capabilities, display=display))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ["card", "wechat_pay"]})
    if refused:
        assert r.status_code == 409 and r.json()["error"] == "payment_methods_unavailable", r.text
        assert r.json()["detail"] == {"methods": ["wechat_pay"]}
        acked = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card", "wechat_pay"]})
        assert acked.status_code == 409 and acked.json()["error"] == "payment_methods_unavailable"
    else:
        assert r.status_code == 409 and r.json()["error"] == "payment_methods_unverified", r.text
        assert r.json()["detail"] == {"acknowledgement": "acknowledge_unverified_methods", "methods": ["wechat_pay"],
                                      "charges_enabled": True}
    assert versions(world) == []


# ---------------------------------------------------------------- an inconclusive answer is not "verified" (SEC-1)
def configurations(*rows):
    return lambda request: (
        httpx.Response(200, json={"object": "account", "charges_enabled": True})
        if request.url.path == "/v1/account" else
        httpx.Response(200, json={"object": "list", "has_more": False, "data": list(rows)}))


def default_configuration(**changes):
    return {"object": "payment_method_configuration", "id": "pmc_fixture", "active": True, "is_default": True,
            "livemode": False, "card": {"available": True}, "wechat_pay": {"available": True},
            "alipay": {"available": True}, **changes}


@pytest.mark.parametrize(("case", "readiness", "methods", "charges"), [
    ("inactive_default", lambda: fixture_readiness(answer=configurations(default_configuration(active=False)),
                                                   capabilities=None), ALL, True),
    ("missing_default", lambda: fixture_readiness(answer=configurations()), ALL, True),
    ("two_defaults", lambda: fixture_readiness(answer=configurations(
        default_configuration(), default_configuration(id="pmc_second"))), ALL, True),
    ("unknown_flag", lambda: fixture_readiness(answer=configurations(default_configuration(alipay=None))),
     ["alipay"], True),
    ("charges_disabled", lambda: fixture_readiness(charges=False), [], False),
])
def test_an_inconclusive_inspection_needs_the_acknowledgement_and_is_recorded_as_unverified(
        world, case, readiness, methods, charges):
    install(world, readiness())
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ALL})
    assert r.status_code == 409 and r.json()["error"] == "payment_methods_unverified", (case, r.text)
    assert r.json()["detail"] == {"acknowledgement": "acknowledge_unverified_methods", "methods": methods,
                                  "charges_enabled": charges}
    assert versions(world) == []
    assert publish_audits(world, "denied")[-1] == {"code": "payment_methods_unverified", "methods": methods}
    ok = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ALL},
                  key="fixture_publish_0001")
    assert ok.status_code == 200, ok.text
    assert ok.json()["methods_check"] == "unverified_acknowledged" and ok.json()["state"]["spec"]["enabled"] is True
    meta = publish_audits(world, "ok")[-1]
    assert meta["methods_check"] == "unverified_acknowledged" and meta["acknowledge_unverified_methods"] is True
    assert meta["methods_check_cause"] == "payment_methods_unverified"


def test_keep_checks_the_saved_key_and_a_concurrent_publication_wins(world):
    c = world["login"]("fin")
    assert publish(c, spec={**SPEC, "payment_methods": ["card"]}).status_code == 200

    def concurrent():
        settings = PaymentSettings(world["store"], environment="test")
        with world["store"].tx():
            settings.append({**SPEC, "enabled": False}, {"action": "keep"}, expected_revision=1,
                            actor_id=world["people"]["fin"]["id"], reason="isolated concurrent change",
                            operation_id="ao_publish_guard_fixture")

    transport, calls = transport_for([{"object": "payment_method_configuration", "id": "pmc_fixture", "active": True,
                                       "is_default": True, "livemode": False, "card": {"available": True}}],
                                     after_account=concurrent)
    install(world, StripeReadiness(transport=transport))
    r = publish(c, revision=1, spec={**SPEC, "operator_name": "Fixture renamed"}, credential={"action": "keep"},
                key="fixture_publish_keep1")
    assert r.status_code == 409 and r.json()["error"] == "payment_revision_conflict"
    assert calls and all(request.headers["authorization"] == "Bearer " + FIXTURE_SECRET_KEY for request in calls)
    assert [row["revision"] for row in versions(world)] == [1, 2], "the concurrent version stands; this one is not saved"


# ---------------------------------------------------------------- the check cannot run
def test_a_check_that_cannot_run_refuses_unless_explicitly_acknowledged(world):
    calls = []
    install(world, fixture_readiness(calls=calls, fail=True))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ["card"]})
    assert r.status_code == 503 and r.json()["error"] == "payment_readiness_unavailable"
    assert r.json()["detail"] == {"acknowledgement": "acknowledge_unverified_methods"}
    assert versions(world) == [] and len(calls) == 1
    for invalid in ("true", 1, None):
        bad = with_ack(c, {"acknowledge_unverified_methods": invalid}, spec={**SPEC, "payment_methods": ["card"]},
                       key="fixture_publish_badack")
        assert bad.status_code == 400 and bad.json()["error"] == "payment_config_invalid"
    # The same publication, retried with the acknowledgement (same idempotency key), goes through.
    ok = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card"]},
                  key="fixture_publish_0001")
    assert ok.status_code == 200, ok.text
    assert ok.json()["methods_check"] == "unverified_acknowledged" and ok.json()["state"]["revision"] == 1
    meta = publish_audits(world, "ok")[-1]
    assert meta["methods_check"] == "unverified_acknowledged" and meta["acknowledge_unverified_methods"] is True
    assert meta["methods_check_cause"] == "payment_readiness_unavailable"
    # A replay of the finished publication answers from its record, without asking the provider again.
    before = len(calls)
    replay = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card"]},
                      key="fixture_publish_0001")
    assert replay.json() == ok.json() and len(calls) == before


def test_a_busy_check_counts_as_not_run(world):
    readiness = fixture_readiness()
    assert readiness._slots.acquire(blocking=False) and readiness._slots.acquire(blocking=False)
    install(world, readiness)
    c = world["login"]("fin")
    r = publish(c)
    assert r.status_code == 503 and r.json()["error"] == "payment_readiness_unavailable"
    readiness._slots.release()
    readiness._slots.release()


# ---------------------------------------------------------------- Stripe's answer about the key (UX-2)
def key_answer(status, body=None, *, content=None):
    def answer(request):
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body)
    return answer


FIXTURE_PROVIDER_MESSAGE = "FIXTURE provider message: Expired API Key provided: sk_test_fixture***"


def test_a_key_stripe_rejects_is_refused_and_no_acknowledgement_overrides_it(world):
    calls = []
    rejected = {"error": {"type": "invalid_request_error", "code": "api_key_expired", "message": FIXTURE_PROVIDER_MESSAGE}}
    install(world, fixture_readiness(calls=calls, answer=key_answer(401, rejected)))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ["card"]})
    assert r.status_code == 409 and r.json()["error"] == "payment_credentials_rejected", r.text
    assert "暂时" not in r.json()["message"] and "稍后" not in r.json()["message"], "a rejection is not temporary"
    assert "acknowledgement" not in (r.json().get("detail") or {}), "nothing to acknowledge"
    acked = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card"]})
    assert acked.status_code == 409 and acked.json()["error"] == "payment_credentials_rejected"
    assert versions(world) == [] and world["store"].all("SELECT * FROM payment_credentials") == []
    assert publish_audits(world, "denied")[-1] == {"code": "payment_credentials_rejected"}
    assert len(calls) == 2, "the account read only; the configurations are never asked for"
    for text in (r.text, acked.text, repr(world["store"].all("SELECT meta FROM audit_events"))):
        assert FIXTURE_PROVIDER_MESSAGE not in text and "api_key_expired" not in text and FIXTURE_SECRET_KEY not in text
    # A disabled version still publishes: closing top-ups never depends on the key.
    closed = publish(c, spec={**SPEC, "enabled": False}, key="fixture_publish_closed1")
    assert closed.status_code == 200 and closed.json()["methods_check"] == "not_required"
    # The saved-key diagnostic says the same, definitely, for the saved key.
    read = c.get("/api/admin/billing/payments/readiness")
    assert read.status_code == 409 and read.json()["error"] == "payment_credentials_rejected"
    assert FIXTURE_PROVIDER_MESSAGE not in read.text


def test_a_key_forbidden_to_read_the_account_may_be_published_with_its_own_acknowledgement(world):
    forbidden = {"error": {"type": "invalid_request_error", "message": FIXTURE_PROVIDER_MESSAGE}}
    install(world, fixture_readiness(answer=key_answer(403, forbidden)))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ["card"]})
    assert r.status_code == 409 and r.json()["error"] == "payment_credentials_forbidden", r.text
    assert r.json()["detail"] == {"acknowledgement": "acknowledge_unverified_methods"}
    assert "权限" in r.json()["message"] and FIXTURE_PROVIDER_MESSAGE not in r.text
    ok = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card"]},
                  key="fixture_publish_0001")
    assert ok.status_code == 200 and ok.json()["methods_check"] == "unverified_acknowledged"
    assert publish_audits(world, "ok")[-1]["methods_check_cause"] == "payment_credentials_forbidden"


@pytest.mark.parametrize(("case", "answer"), [
    ("401_without_error_object", key_answer(401, content=b"<html>FIXTURE proxy</html>")),
    ("403_without_error_type", key_answer(403, {"error": {"message": FIXTURE_PROVIDER_MESSAGE}})),
    ("500_with_error_object", key_answer(500, {"error": {"type": "api_error"}})),
    ("429", key_answer(429, {"error": {"type": "invalid_request_error", "code": "rate_limit"}})),
    # SEC-2: a body nested too deeply to decode is malformed, an audited 503, never an unaudited 500.
    # (200 000 levels: deeper than CPython's JSON decoder accepts on 3.12 and 3.14, inside MAX_RESPONSE_BYTES.)
    ("nested_body", key_answer(200, content=b"[" * 200_000 + b"]" * 200_000)),
    ("nested_401_body", key_answer(401, content=b"[" * 200_000 + b"]" * 200_000)),
])
def test_answers_that_prove_nothing_stay_temporary_and_acknowledgeable(world, case, answer):
    install(world, fixture_readiness(answer=answer))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": ["card"]})
    assert r.status_code == 503 and r.json()["error"] == "payment_readiness_unavailable", (case, r.text)
    assert r.json()["detail"] == {"acknowledgement": "acknowledge_unverified_methods"}
    assert publish_audits(world, "error")[-1] == {"code": "payment_readiness_unavailable"}
    assert versions(world) == []
    ok = with_ack(c, {"acknowledge_unverified_methods": True}, spec={**SPEC, "payment_methods": ["card"]},
                  key="fixture_publish_0001")
    assert ok.status_code == 200 and ok.json()["methods_check"] == "unverified_acknowledged"
    assert publish_audits(world, "ok")[-1]["methods_check_cause"] == "payment_readiness_unavailable"


def test_a_disabled_version_publishes_without_contacting_the_provider(world):
    calls = []
    install(world, fixture_readiness({"wechat_pay": False}, calls=calls, fail=True))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "enabled": False, "payment_methods": ALL})
    assert r.status_code == 200, r.text
    assert r.json()["methods_check"] == "not_required" and calls == []
    assert publish_audits(world, "ok")[-1]["methods_check"] == "not_required"


def test_an_invalid_request_is_refused_before_contacting_the_provider(world):
    calls = []
    install(world, fixture_readiness(calls=calls))
    c = world["login"]("fin")
    r = publish(c, spec={**SPEC, "payment_methods": []})
    assert r.status_code == 400 and calls == []
    assert publish(c, revision=7).json()["error"] == "payment_revision_conflict" and calls == []


# ---------------------------------------------------------------- readiness reports the API version
def test_readiness_reports_the_api_version_stripe_applied(world):
    c = world["login"]("fin")
    assert publish(c, spec={**SPEC, "payment_methods": ["card"]}).status_code == 200
    calls = []
    install(world, fixture_readiness(calls=calls))
    r = c.get("/api/admin/billing/payments/readiness")
    assert r.status_code == 200, r.text
    assert r.json()["api_version"] == "2026-04-22.dahlia" and r.json()["api_version_requested"] == STRIPE_API_VERSION
    assert calls and all(request.headers["stripe-version"] == STRIPE_API_VERSION for request in calls)

    def odd(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True},
                                  headers={"Stripe-Version": "FIXTURE <not a version>"})
        return httpx.Response(200, json={"object": "list", "has_more": False, "data": []})

    install(world, StripeReadiness(transport=httpx.MockTransport(odd)))
    r = c.get("/api/admin/billing/payments/readiness")
    assert r.status_code == 200 and r.json()["api_version"] is None and r.json()["configuration_status"] == "missing"


# ---------------------------------------------------------------- the finance view shows why
def test_the_finance_view_shows_the_sanitized_reason_per_order(world):
    store, uid = world["store"], world["people"]["plain"]["id"]
    now = int(time.time())
    rows = [("top_refused", "failed", "provider_refused", "http_400;type=invalid_request_error;param=payment_method_types",
             now - 10, now + 3600),
            ("top_stuck", "creating", "provider_unknown", "http_503", now - 7200, now - CREATING_GRACE_S - 1),
            ("top_verifying", "creating", "provider_unknown", "network", now - 5, now + 3600)]
    for order_id, status, code, detail, created, expires in rows:
        store.execute("INSERT INTO payment_topups(order_id,environment,provider,payer_kind,payer_id,initiator_id,amount_minor,"
                      "currency,unit,credit_units,settings_revision,status,created_at,expires_at,failure_code,failure_detail) "
                      "VALUES (?,'test','stripe','user',?,?,1000,'CNY','CNY',10000000,1,?,?,?,?,?)",
                      (order_id, uid, uid, status, created, expires, code, detail))
    view = world["login"]("fin").get("/api/admin/billing/finance").json()
    by_id = {row["order_id"]: row for row in view["payments"]}
    assert {k: by_id["top_refused"][k] for k in ("status", "failure_code", "failure_detail", "failure_reason")} == {
        "status": "failed", "failure_code": "provider_refused",
        "failure_detail": "http_400;type=invalid_request_error;param=payment_method_types",
        "failure_reason": "payment_method_not_enabled"}
    assert {k: by_id["top_stuck"][k] for k in ("status", "failure_code", "failure_detail", "failure_reason")} == {
        "status": "expired", "failure_code": "provider_unknown", "failure_detail": "http_503", "failure_reason": None}
    assert by_id["top_verifying"]["status"] == "creating" and by_id["top_verifying"]["failure_detail"] == "network"
