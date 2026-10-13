"""Real admin routes/Store with isolated credentials; Stripe HTTP responses are test fixtures.

No payment, customer, session or model call may be created by this diagnostic.
"""
from __future__ import annotations

import httpx
import pytest

from synwevo_core.billing.payment_settings import PaymentSettings
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import PlatformAccess
from synwevo_identity.payment_readiness import MAX_RESPONSE_BYTES, StripeReadiness

from .test_admin_payments import (FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET, SPEC, publish,
                                  world)  # noqa: F401 - shared isolated app fixture

PATH = "/api/admin/billing/payments/readiness"


def configuration(**changes):
    return {"object": "payment_method_configuration", "id": "pmc_fixture", "active": True,
            "is_default": True, "livemode": False, "card": {"available": True},
            "wechat_pay": {"available": False}, "alipay": {"available": False}, **changes}


def transport_for(configs=None, *, after_account=None, charges=True):
    calls = []

    def handler(request):
        assert request.method == "GET" and request.url.host == "api.stripe.com"
        assert request.headers["authorization"] == "Bearer " + FIXTURE_SECRET_KEY
        calls.append(request)
        if request.url.path == "/v1/account":
            if after_account:
                after_account()
            return httpx.Response(200, json={"object": "account", "charges_enabled": charges,
                                           "email": "private-merchant@example.test", "id": "acct_private"})
        assert request.url.path == "/v1/payment_method_configurations"
        return httpx.Response(200, json={"object": "list", "data": [configuration()] if configs is None else configs,
                                       "has_more": False})

    return httpx.MockTransport(handler), calls


def test_saved_disabled_configuration_can_be_checked_without_payment_or_mutation(world):
    client, store = world["login"]("fin"), world["store"]
    assert publish(client, spec={**SPEC, "enabled": False}).status_code == 200
    before = {table: store.all(f"SELECT * FROM {table}") for table in
              ("payment_settings_versions", "admin_operations", "payment_topups", "billing_journal")}
    transport, calls = transport_for()
    world["api"].state.payment_readiness = StripeReadiness(transport=transport)
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revision"] == result["credential_revision"] == 1
    assert result["environment"] == "test" and result["charges_enabled"] is True
    assert result["methods"] == {"card": True, "wechat_pay": False, "alipay": False}
    assert result["configuration_status"] == "available" and result["checked_at"] > 0
    assert response.headers["cache-control"] == "no-store"
    for private in (FIXTURE_SECRET_KEY, FIXTURE_WEBHOOK_SECRET, "private-merchant", "acct_private", "pmc_fixture"):
        assert private not in response.text
    for table, rows in before.items():
        assert store.all(f"SELECT * FROM {table}") == rows
    assert len(calls) == 2
    assert PaymentSettings(store, environment="test").resolve() is None, "inspection must not open top-ups"


def test_readiness_requires_current_billing_permission_before_contacting_stripe(world):
    transport, calls = transport_for()
    world["api"].state.payment_readiness = StripeReadiness(transport=transport)
    assert world["login"]("plain").get(PATH).status_code == 404
    assert world["login"]("aud").get(PATH).status_code == 403
    response = world["login"]("fin", reauth=False).get(PATH)
    assert response.status_code == 409 and response.json()["error"] == "payment_readiness_no_keys"
    assert calls == []


@pytest.mark.parametrize("change", ["settings", "permission"])
def test_configuration_and_permission_are_rechecked_after_provider_read(world, change):
    client = world["login"]("fin")
    assert publish(client).status_code == 200

    def changed():
        if change == "settings":
            settings = PaymentSettings(world["store"], environment="test")
            with world["store"].tx():
                settings.append({**SPEC, "enabled": False}, {"action": "keep"}, expected_revision=1,
                                actor_id=world["people"]["fin"]["id"], reason="isolated concurrent change",
                                operation_id="ao_readiness_fixture")
        else:
            PlatformAccess(world["store"]).revoke_role(world["people"]["root"]["id"],
                world["people"]["fin"]["id"], "finance", "isolated concurrent revocation")

    transport, _ = transport_for(after_account=changed)
    world["api"].state.payment_readiness = StripeReadiness(transport=transport)
    response = client.get(PATH)
    assert response.status_code == (409 if change == "settings" else 404)
    assert "methods" not in response.json()


@pytest.mark.parametrize(("configs", "status"), [
    ([], "missing"), ([configuration(active=False)], "inactive"),
    ([configuration(), configuration(id="pmc_second")], "ambiguous"),
    ([configuration(parent="pmc_connect")], "missing"),
])
def test_unknown_or_inactive_defaults_never_look_verified(configs, status):
    transport, _ = transport_for(configs)
    result = StripeReadiness(transport=transport).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert result["configuration_status"] == status
    assert result["methods"] == {"card": None, "wechat_pay": None, "alipay": None}


def test_unknown_method_flags_are_not_coerced_and_closed_account_is_reported():
    transport, _ = transport_for([configuration(card={"available": "true"}, alipay=None)], charges=False)
    result = StripeReadiness(transport=transport).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert result["charges_enabled"] is False
    assert result["methods"] == {"card": None, "wechat_pay": False, "alipay": None}


def test_default_on_later_page_is_read_and_page_limit_is_explicit():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True})
        page = len(calls) - 1
        assert request.url.params.get("starting_after") == (f"pmc_page{page - 1}" if page > 1 else None)
        return httpx.Response(200, json={"object": "list", "has_more": page < 3,
              "data": [configuration(id=f"pmc_page{page}", is_default=page == 3)]})

    result = StripeReadiness(transport=httpx.MockTransport(handler)).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert result["methods"]["card"] is True and len(calls) == 4

    def endless(request):
        calls.append(request)
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True})
        return httpx.Response(200, json={"object": "list", "has_more": True,
                     "data": [configuration(id=f"pmc_page{len(calls)}", is_default=False)]})

    calls.clear()
    result = StripeReadiness(transport=httpx.MockTransport(endless)).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert result["configuration_status"] == "incomplete" and len(calls) == 6
    assert all(value is None for value in result["methods"].values())


@pytest.mark.parametrize("fault", ["network", "redirect", "denied", "invalid", "oversized", "wrong-mode", "empty-more"])
def test_failed_provider_reads_never_echo_secrets_or_follow_redirects(fault):
    calls = []

    def handler(request):
        calls.append(request)
        if fault == "network":
            raise httpx.ReadTimeout(FIXTURE_SECRET_KEY)
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.example/"})
        if fault == "denied":
            return httpx.Response(403, json={"error": {"message": FIXTURE_SECRET_KEY}})
        if fault == "invalid":
            return httpx.Response(200, content=b"not json")
        if fault == "oversized":
            return httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1))
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True})
        return httpx.Response(200, json={"object": "list", "has_more": fault == "empty-more",
                          "data": [configuration(livemode=True)] if fault == "wrong-mode" else []})

    reader = StripeReadiness(transport=httpx.MockTransport(handler))
    with pytest.raises(ServiceError) as exc:
        reader.inspect(FIXTURE_SECRET_KEY, environment="test")
    assert exc.value.code == "payment_readiness_unavailable"
    assert FIXTURE_SECRET_KEY not in str(exc.value)
    assert all(request.url.host == "api.stripe.com" for request in calls)


def test_parallel_reads_are_bounded_and_busy_does_not_contact_provider():
    transport, calls = transport_for()
    reader = StripeReadiness(transport=transport)
    assert reader._slots.acquire(blocking=False) and reader._slots.acquire(blocking=False)
    with pytest.raises(ServiceError) as exc:
        reader.inspect(FIXTURE_SECRET_KEY, environment="test")
    assert exc.value.code == "payment_readiness_busy" and calls == []
    reader._slots.release()
    assert reader.inspect(FIXTURE_SECRET_KEY, environment="test")["methods"]["card"] is True


# ---------------------------------------------------------------- activation evidence (PS-1)
def inspect_with(capabilities, method_entry, *, absent=False):
    """Inspect a FIXTURE account whose capability list is ``capabilities`` (omitted when ``absent``)
    and whose default configuration describes WeChat Pay as ``method_entry``."""
    def handler(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True,
                                             **({} if absent else {"capabilities": capabilities})})
        return httpx.Response(200, json={"object": "list", "has_more": False,
                                         "data": [configuration(wechat_pay=method_entry)]})
    return StripeReadiness(transport=httpx.MockTransport(handler)).inspect(FIXTURE_SECRET_KEY, environment="test")


OFF = {"available": False, "display_preference": {"value": "off"}}
ON_UNAVAILABLE = {"available": False, "display_preference": {"value": "on"}}


@pytest.mark.parametrize(("capabilities", "entry", "absent", "expected"), [
    ({"card_payments": "active"}, OFF, False, False),                               # production 2026-10-13
    ({"card_payments": "active", "wechat_pay_payments": "active"}, OFF, False, True),  # display off only
    ({"wechat_pay_payments": "pending"}, OFF, False, False),
    ({"wechat_pay_payments": "inactive"}, None, False, False),
    ({"wechat_pay_payments": "inactive"}, {"available": True}, False, None),        # contradiction: no evidence
    ({"card_payments": "active"}, {"available": True}, False, True),                 # e.g. an older account
    ({"card_payments": "active"}, None, False, None),
    (None, ON_UNAVAILABLE, True, False),                                             # Stripe's definition of available
    (None, OFF, True, None),
    (None, {"available": False}, True, None),
    ({"wechat_pay_payments": {"status": "active"}}, OFF, False, None),               # an unexpected shape is no evidence
])
def test_activation_needs_definite_evidence(capabilities, entry, absent, expected):
    result = inspect_with(capabilities, entry, absent=absent)
    assert result["activated"]["wechat_pay"] is expected
    assert result["activated"]["card"] is True
    available = (entry or {}).get("available")
    assert result["methods"]["wechat_pay"] == (available if isinstance(available, bool) else None), "the diagnostic flag is unchanged"


def test_activation_is_read_from_capabilities_even_without_a_usable_default_configuration():
    def handler(request):
        if request.url.path == "/v1/account":
            return httpx.Response(200, json={"object": "account", "charges_enabled": True,
                                             "capabilities": {"card_payments": "active"}})
        return httpx.Response(200, json={"object": "list", "has_more": False, "data": []})
    result = StripeReadiness(transport=httpx.MockTransport(handler)).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert result["configuration_status"] == "missing"
    assert result["activated"] == {"card": True, "wechat_pay": None, "alipay": None}


# ---------------------------------------------------------------- Stripe's answer about the key (UX-2)
@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {"error": {"type": "invalid_request_error", "code": "api_key_expired", "message": FIXTURE_SECRET_KEY}},
     "payment_credentials_rejected"),
    (401, {"error": {"type": "invalid_request_error", "message": "Invalid API Key provided"}}, "payment_credentials_rejected"),
    (403, {"error": {"type": "invalid_request_error", "message": FIXTURE_SECRET_KEY}}, "payment_credentials_forbidden"),
    (401, {"error": {"message": FIXTURE_SECRET_KEY}}, "payment_readiness_unavailable"),   # no Stripe error object
    (401, ["not", "an", "object"], "payment_readiness_unavailable"),
    (402, {"error": {"type": "invalid_request_error"}}, "payment_readiness_unavailable"),
])
def test_a_definite_answer_about_the_key_is_not_reported_as_an_outage(status, body, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body)
    with pytest.raises(ServiceError) as exc:
        StripeReadiness(transport=httpx.MockTransport(handler)).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert exc.value.code == code
    assert FIXTURE_SECRET_KEY not in str(exc.value) and "Invalid API Key" not in str(exc.value)
    assert len(calls) == 1


def test_a_body_nested_too_deeply_is_malformed_not_a_crash():
    def handler(request):
        return httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000)   # too deep for the JSON decoder
    with pytest.raises(ServiceError) as exc:
        StripeReadiness(transport=httpx.MockTransport(handler)).inspect(FIXTURE_SECRET_KEY, environment="test")
    assert exc.value.code == "payment_readiness_unavailable"
