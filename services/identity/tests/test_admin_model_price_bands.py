"""Time-banded cost prices, the governed holiday calendar and markup sale prices over HTTP.

FIXTURE units, rates and calendars are synthetic test choices (the rate pairs mirror the
shape of DeepSeek's CNY table but are not Synwevo prices). No payment or model call is made.
"""
from __future__ import annotations

import json
import types
import uuid

import pytest

from synwevo_core.billing import Usage
from synwevo_core.billing.bands import published_defaults
from synwevo_core.billing.catalogue import PriceCatalogue
from synwevo_identity import admin_model_prices

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401  (world is a fixture)

BASE = "/api/admin/billing/prices"
REASON = "Fixture: publish an independently checked tariff"
COST = {"name": "Fixture flash cost", "purpose": "cost", "provider": "deepseek", "model": "fixture-flash",
        "unit": {"code": "FIX_CNY", "scale": 2}, "input_uncached": "2", "input_cached": "0.04", "output": "8",
        "terms_ref": "fixture-terms:v1", "effective_from": 1000, "previous_price_id": None,
        "off_peak": {"input_uncached": "1", "input_cached": "0.02", "output": "4"}}
CALENDAR = {**published_defaults(), "holidays": [{"date": "2026-10-01", "name": "Fixture holiday"}],
            "source_ref": "fixture-calendar:v1"}


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def key():
    return uuid.uuid4().hex


def publish_schedule(w, spec=CALENDAR, revision=0, client=None):
    client = client or w["admin"]
    plan = ok(post(client, BASE + "/schedule/preview", {"expected_revision": revision, "spec": spec}))
    return plan, post(client, BASE + "/schedule/publish", {"expected_revision": revision, "spec": spec,
        "preview_token": plan["preview_token"], "idempotency_key": key(), "reason": REASON})


def publish_draft(w, spec):
    draft = ok(post(w["admin"], BASE + "/drafts", {"spec": spec, "idempotency_key": key()}))
    plan = post(w["admin"], BASE + f"/drafts/{draft['id']}/preview", {"expected_revision": draft["revision"]})
    if plan.status_code != 200:
        return draft, plan
    return draft, post(w["admin"], BASE + f"/drafts/{draft['id']}/publish", {"expected_revision": draft["revision"],
        "preview_token": plan.json()["preview_token"], "idempotency_key": key(), "reason": REASON})


def markup_spec(cost_price_id, **changes):
    return {"name": "Fixture flash retail", "cost_price_id": cost_price_id, "markup": "1.5", "off_peak_markup": None,
            "terms_ref": "fixture-retail:v1", "effective_from": 2000, **changes}


def published_cost(w):
    ok(publish_schedule(w)[1])
    _, result = publish_draft(w, COST)
    return ok(result)["draft"]["price_id"]


def test_calendar_is_not_configured_until_published_and_offers_published_windows(world):
    state = ok(world["admin"].get(BASE + "/schedule"))
    assert state["revision"] == 0 and state["spec"] is None and state["environment"] == "test"
    assert state["defaults"]["peak_windows"] == [
        {"weekdays": [1, 2, 3, 4, 5], "start": "01:00", "end": "04:00"},
        {"weekdays": [1, 2, 3, 4, 5], "start": "06:00", "end": "10:00"}]
    assert state["defaults"]["holidays"] == []
    assert not world["store"].all("SELECT * FROM billing_band_schedules")


def test_calendar_publication_is_confirmed_audited_idempotent_and_versioned(world):
    w = world
    plan, response = publish_schedule(w)
    assert plan["changes"]["holidays_added"] == ["2026-10-01"] and plan["before"]["revision"] == 0
    first = ok(response)
    assert first["state"]["revision"] == 1 and first["state"]["spec"]["holidays"] == CALENDAR["holidays"]
    audit = w["store"].one("SELECT * FROM audit_events WHERE action='admin.model_price.schedule.publish' AND result='ok'")
    meta = json.loads(audit["meta"])
    assert meta["reason"] == REASON and meta["changes"]["holidays_added"] == ["2026-10-01"]
    # The same request replays the committed result; a stale revision is refused.
    stale_plan, _ = None, None
    added = {**CALENDAR, "holidays": [*CALENDAR["holidays"], {"date": "2026-10-02", "name": "Fixture bridge"}]}
    plan2 = ok(post(w["admin"], BASE + "/schedule/preview", {"expected_revision": 1, "spec": added}))
    body = {"expected_revision": 1, "spec": added, "preview_token": plan2["preview_token"], "idempotency_key": key(),
            "reason": REASON}
    second = ok(post(w["admin"], BASE + "/schedule/publish", body))
    assert ok(post(w["admin"], BASE + "/schedule/publish", body)) == second
    assert second["changes"]["holidays_added"] == ["2026-10-02"]
    refused = post(w["admin"], BASE + "/schedule/preview", {"expected_revision": 1, "spec": CALENDAR})
    assert refused.status_code == 409 and refused.json()["error"] == "band_schedule_revision_conflict"
    history = ok(w["admin"].get(BASE + "/schedule/history"))
    assert [item["revision"] for item in history["items"]] == [2, 1]


@pytest.mark.parametrize("change", ["actor", "expired", "spec", "revision", "signature"])
def test_calendar_preview_binds_actor_spec_revision_and_expiry(world, monkeypatch, change):
    w = world
    plan = ok(post(w["admin"], BASE + "/schedule/preview", {"expected_revision": 0, "spec": CALENDAR}))
    body = {"expected_revision": 0, "spec": CALENDAR, "preview_token": plan["preview_token"], "idempotency_key": key(),
            "reason": REASON}
    client = w["admin"]
    if change == "actor":
        client = w["login"](make_platform_admin(w["store"], w["ident"].create_user("cal@example.test", PW, status="active")))
        w["reauth"](client)
    elif change == "expired":
        monkeypatch.setattr(admin_model_prices, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    elif change == "spec":
        body["spec"] = {**CALENDAR, "holidays": []}
    elif change == "revision":
        body["expected_revision"] = 1
    else:
        body["preview_token"] += "x"
    response = post(client, BASE + "/schedule/publish", body)
    assert response.status_code in (409, 400), response.text
    assert not w["store"].all("SELECT * FROM billing_band_schedules")


def test_calendar_needs_platform_config_manage_and_fresh_auth(world):
    w = world
    assert w["ordinary"].get(BASE + "/schedule").status_code == 404
    assert post(w["ordinary"], BASE + "/schedule/preview", {"expected_revision": 0, "spec": CALENDAR}).status_code == 404
    actor = make_platform_admin(w["store"], w["ident"].create_user("cal2@example.test", PW, status="active"))
    client = w["login"](actor)
    plan, response = publish_schedule(w, client=client)
    assert response.json()["error"] == "reauth_required"
    w["reauth"](client)
    end_platform_grants(w["store"], actor)
    assert publish_schedule.__name__ and post(client, BASE + "/schedule/publish", {"expected_revision": 0, "spec": CALENDAR,
        "preview_token": plan["preview_token"], "idempotency_key": key(), "reason": REASON}).status_code == 404
    assert not w["store"].all("SELECT * FROM billing_band_schedules")


@pytest.mark.parametrize("body", [None, {"expected_revision": 0, "spec": {"secret": "fixture-secret"}},
                                  {"expected_revision": 0, "spec": {**CALENDAR, "holidays": [{"date": "fixture-secret", "name": "x"}]}}])
def test_invalid_calendar_is_refused_without_echo(world, body):
    response = post(world["admin"], BASE + "/schedule/preview", body)
    assert response.status_code == 400 and response.json()["error"] == "band_schedule_invalid"
    assert "fixture-secret" not in response.text


def test_banded_cost_price_needs_a_published_calendar_and_quotes_both_bands(world):
    w = world
    draft, refused = publish_draft(w, COST)
    assert refused.status_code == 409 and refused.json()["error"] == "price_band_schedule_required"
    quote = ok(post(w["admin"], BASE + f"/drafts/{draft['id']}/calculate",
                    {"expected_revision": 1, "input_uncached": 1000, "input_cached": 1000, "output": 1000}))
    # Fen per million tokens: peak 200 + 4 + 800 = 1004 / 1000 per 1k, off-peak half.
    assert quote["display_amount"] == "0.01004"
    assert quote["bands"]["peak"]["exact"] == {"numerator": "251", "denominator": "250"}
    assert quote["bands"]["off_peak"]["display_amount"] == "0.00502"
    ok(publish_schedule(w)[1])
    plan = ok(post(w["admin"], BASE + f"/drafts/{draft['id']}/preview", {"expected_revision": 1}))
    saved = ok(post(w["admin"], BASE + f"/drafts/{draft['id']}/publish", {"expected_revision": 1,
        "preview_token": plan["preview_token"], "idempotency_key": key(), "reason": REASON}))["draft"]
    price = PriceCatalogue(w["store"], environment="test").published(saved["price_id"])
    assert price.banded and price.band_schedule == "deepseek"
    assert price.exact(Usage(10**6, 0, 0), "peak") == 200 and price.exact(Usage(10**6, 0, 0), "off_peak") == 100


@pytest.mark.parametrize("off_peak,error", [({"input_uncached": "3", "input_cached": "0.02", "output": "4"}, "price_off_peak_above_peak"),
                                            ({"input_uncached": "1", "output": "4"}, "price_invalid")])
def test_off_peak_rates_must_be_complete_and_not_above_peak(world, off_peak, error):
    response = post(world["admin"], BASE + "/drafts", {"spec": {**COST, "off_peak": off_peak}, "idempotency_key": key()})
    assert response.status_code == 400 and response.json()["error"] == error
    assert not world["store"].all("SELECT * FROM model_price_drafts")


def test_flat_drafts_keep_their_contract(world):
    flat = {k: v for k, v in COST.items() if k != "off_peak"}
    draft = ok(post(world["admin"], BASE + "/drafts", {"spec": flat, "idempotency_key": key()}))
    assert draft["spec"]["off_peak"] is None
    quote = ok(post(world["admin"], BASE + f"/drafts/{draft['id']}/calculate",
                    {"expected_revision": 1, "input_uncached": 1000, "input_cached": 0, "output": 0}))
    assert quote["bands"] is None and quote["display_amount"] == "0.002"


def test_markup_preview_shows_examples_per_1k_tokens_and_publishes_a_derived_sale_price(world):
    w = world
    cost_id = published_cost(w)
    spec = markup_spec(cost_id, off_peak_markup="1.2")
    plan = ok(post(w["admin"], BASE + "/markups/preview", {"spec": spec}))
    assert plan["example_tokens"] == 1000 and plan["unit"] == {"code": "FIX_CNY", "scale": 2}
    assert plan["cost_rates"] == {"peak": {"input_uncached": "2", "input_cached": "0.04", "output": "8"},
                                  "off_peak": {"input_uncached": "1", "input_cached": "0.02", "output": "4"}}
    assert plan["sale_rates"] == {"peak": {"input_uncached": "3", "input_cached": "0.06", "output": "12"},
                                  "off_peak": {"input_uncached": "1.2", "input_cached": "0.024", "output": "4.8"}}
    assert plan["examples"]["peak"]["output"] == {"cost": "0.008", "sale": "0.012"}
    assert plan["examples"]["off_peak"]["input_cached"] == {"cost": "0.00002", "sale": "0.000024"}
    assert plan["replaces_price_id"] is None
    assert not w["store"].all("SELECT * FROM model_price_markups")
    body = {"spec": spec, "preview_token": plan["preview_token"], "idempotency_key": key(), "reason": REASON}
    result = ok(post(w["admin"], BASE + "/markups/publish", body))
    assert ok(post(w["admin"], BASE + "/markups/publish", body)) == result
    sale = PriceCatalogue(w["store"], environment="test").published(result["markup"]["price_id"])
    assert (sale.purpose, sale.model, sale.unit.code, sale.band_schedule) == ("sale", "fixture-flash", "FIX_CNY", "deepseek")
    assert sale.basis.cost_price_id == cost_id and sale.basis.markup == "1.5" and sale.basis.off_peak_markup == "1.2"
    assert sale.exact(Usage(10**6, 0, 10**6), "peak") == 300 + 1200
    assert sale.exact(Usage(10**6, 0, 10**6), "off_peak") == 120 + 480
    audit = json.loads(w["store"].one("SELECT meta FROM audit_events WHERE action='admin.model_price.markup.publish' "
                                      "AND result='ok'")["meta"])
    assert audit["price_id"] == sale.price_id and audit["reason"] == REASON
    listing = ok(w["admin"].get(BASE + "/markups"))
    assert [item["price_id"] for item in listing["items"]] == [sale.price_id]
    # A second version names the one it replaces; both stay immutable.
    again = ok(post(w["admin"], BASE + "/markups/preview", {"spec": markup_spec(cost_id, markup="2")}))
    assert again["replaces_price_id"] == sale.price_id


@pytest.mark.parametrize("change,error", [({"markup": "0.9"}, "price_markup_invalid"), ({"markup": "100.5"}, "price_markup_invalid"),
    ({"markup": "1.23456"}, "price_markup_invalid"), ({"markup": 2}, "price_markup_invalid"),
    # Off-peak cost is half of peak: x3 equals the x1.5 peak sale (allowed), x3.1 exceeds it.
    ({"off_peak_markup": "3.1"}, "price_markup_off_peak_above_peak"), ({"cost_price_id": "fixture-missing"}, "price_not_found")])
def test_markup_refusals(world, change, error):
    spec = {**markup_spec(published_cost(world)), **change}
    response = post(world["admin"], BASE + "/markups/preview", {"spec": spec})
    assert response.status_code in (400, 404, 409) and response.json()["error"] == error, response.text


def test_markup_on_a_sale_price_or_off_peak_markup_on_a_flat_cost_is_refused(world):
    w = world
    flat = {k: v for k, v in COST.items() if k != "off_peak"}
    _, result = publish_draft(w, flat)
    flat_id = ok(result)["draft"]["price_id"]
    refused = post(w["admin"], BASE + "/markups/preview", {"spec": markup_spec(flat_id, off_peak_markup="1.2")})
    assert refused.json()["error"] == "price_markup_band_unavailable"
    plan = ok(post(w["admin"], BASE + "/markups/preview", {"spec": markup_spec(flat_id)}))
    assert set(plan["sale_rates"]) == {"flat"} and plan["sale_rates"]["flat"]["output"] == "12"
    sale_id = ok(post(w["admin"], BASE + "/markups/publish", {"spec": markup_spec(flat_id), "preview_token": plan["preview_token"],
        "idempotency_key": key(), "reason": REASON}))["markup"]["price_id"]
    refused = post(w["admin"], BASE + "/markups/preview", {"spec": markup_spec(sale_id)})
    assert refused.json()["error"] == "price_markup_needs_cost"


def test_markup_preview_is_bound_to_the_cost_price_state(world):
    w = world
    cost_id = published_cost(w)
    plan = ok(post(w["admin"], BASE + "/markups/preview", {"spec": markup_spec(cost_id)}))
    changed = {"spec": markup_spec(cost_id, markup="1.6"), "preview_token": plan["preview_token"], "idempotency_key": key(),
               "reason": REASON}
    response = post(w["admin"], BASE + "/markups/publish", changed)
    assert response.status_code == 409 and response.json()["error"] == "admin_preview_invalid"
    assert not w["store"].all("SELECT * FROM model_price_markups")


def test_unconfigured_environment_is_honest(world, monkeypatch):
    from fastapi.testclient import TestClient
    from synwevo_identity.app import create_app
    monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT")
    client = TestClient(create_app(str(world["store"].root), start_jobs=False), raise_server_exceptions=False)
    client.cookies.update(world["admin"].cookies)
    assert client.get(BASE + "/schedule").json()["environment"] is None
    assert client.get(BASE + "/markups").json() == {"environment": None, "items": []}
    refused = post(client, BASE + "/schedule/preview", {"expected_revision": 0, "spec": CALENDAR})
    assert refused.status_code == 503 and refused.json()["error"] == "billing_not_configured"
    client.close()


def test_band_openapi_contracts_are_typed(world):
    schema = world["api"].openapi()
    paths, models = schema["paths"], schema["components"]["schemas"]
    for path in (BASE + "/schedule/preview", BASE + "/schedule/publish", BASE + "/markups/preview", BASE + "/markups/publish"):
        operation = paths[path]["post"]
        assert "422" not in operation["responses"]
        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
        assert models[ref]["additionalProperties"] is False and models[ref]["required"]
    assert "off_peak" in models["PriceSpec"]["properties"]
