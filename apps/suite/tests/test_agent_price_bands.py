"""Time-banded prices through the real governed factory: pinned band, quotes and consents.

FIXTURE prices, calendars, funds and SSE only (see test_agent_governed_factory); no provider,
payment service or network is contacted. The fixture calendar makes every hour of every day a
peak hour, so the band is deterministic whatever the wall clock says, and declares holidays
around "today" (China calendar) when a test needs the off-peak band.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
import uuid

import pytest

from synwevo_core.billing.bands import BandSchedules
from synwevo_core.billing.catalogue import PriceCatalogue, PriceMarkups
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.errors import ServiceError
from .test_agent_governed_factory import KEY, UNIT, fund, governed, publish  # noqa: F401  (governed is a fixture)
from .test_agent_metering import complete
from .test_agent_model_consents import create, quote, refused, send

ALL_DAY = [{"weekdays": [1, 2, 3, 4, 5, 6, 7], "start": "00:00", "end": "24:00"}]
CALENDAR = {"peak_windows": ALL_DAY, "holiday_utc_offset_minutes": 480, "holidays": [], "source_ref": "fixture-calendar:v1"}
# Fixture rates in major units per million tokens; off-peak is half of peak.
SALE = {"input_uncached": "4", "input_cached": "0.4", "output": "8"}
SALE_OFF = {"input_uncached": "2", "input_cached": "0.2", "output": "4"}
COST = {"input_uncached": "2", "input_cached": "0.2", "output": "4"}
COST_OFF = {"input_uncached": "1", "input_cached": "0.1", "output": "2"}
# USAGE in test_agent_metering: 8 uncached, 5 cached, 7 output (3 reasoning, inside output).
PEAK_CHARGE, OFF_PEAK_CHARGE = 8 * 4 + 5 * 0.4 + 7 * 8, 8 * 2 + 5 * 0.2 + 7 * 4


def china_days_around_now():
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    return [{"date": (today + timedelta(days=d)).isoformat(), "name": "Fixture holiday"} for d in (-1, 0, 1)]


def publish_calendar(g, spec):
    schedules = BandSchedules(g.store, environment="test")
    with g.store.tx():
        return schedules.append(spec, expected_revision=schedules.read()["revision"], actor_id=g.user["id"],
                                reason="Fixture calendar publication", operation_id=uuid.uuid4().hex)


def configure_banded(g):
    publish_calendar(g, CALENDAR)
    catalogue = PriceCatalogue(g.store, environment="test")
    ids = {}
    with g.store.tx():
        for purpose, peak, off in (("sale", SALE, SALE_OFF), ("cost", COST, COST_OFF)):
            draft, _ = catalogue.create(spec={"name": "Banded fixture", "purpose": purpose, "provider": "deepseek",
                "model": "fixture-route", "unit": asdict(UNIT), **peak, "off_peak": off, "terms_ref": "fixture:bands",
                "effective_from": 0}, actor_id=g.user["id"], idempotency_key=uuid.uuid4().hex)
            ids[purpose] = catalogue.publish(draft["id"], expected_revision=1, actor_id=g.user["id"],
                operation_id=uuid.uuid4().hex, reason="Fixture price publication")["price_id"]
        CostSettings(g.store, environment="test").append({"enabled": True, "unit": asdict(UNIT),
            "daily_budget": "1000000", "weekly_budget": "1000000", "monthly_budget": "1000000",
            "unknown_budget": "1000000", "max_inflight": 4, "max_unknown": 4},
            expected_revision=0, actor_id=g.user["id"], reason="Fixture bounded costs", operation_id=uuid.uuid4().hex)
        g.spec = {"enabled": True, "route": {"requested_model": "fixture-model", "confirmed_route": "fixture-route",
            "confirmation_ref": "fixture:route", "account_confirmation_ref": "fixture:account",
            "cost_basis_ref": "fixture:cost", "sale_price_id": ids["sale"], "cost_price_id": ids["cost"],
            "input_token_ceiling": 300, "output_token_ceiling": 64, "valid_until": int(time.time()) + 3600}}
        publish(g, {"action": "replace", "api_key": KEY})
    result = g.suite.caps.invoke("agent.billing.budget.set", {"unit_code": UNIT.code, "expected_revision": 0,
        "enabled": True, "daily_budget": "1000000", "weekly_budget": "1000000", "monthly_budget": "1000000"},
        user_id=g.user["id"], idempotency_key="bands-fixture-payer-budget")
    assert result["status"] == "succeeded"
    g.conversation = g.agent.create_conversation(g.user["id"], "Banded price fixture", [g.wid])
    g.ids = ids


def run_turn(g, index):
    q = quote(g)
    turn = g.agent.start_turn(g.user, g.conversation["id"], "Fixture review", parent_id=None,
                              regenerate_of=None, quote_id=q["id"])["turn_id"]
    g.replies = [complete().replace(b"fixture-upstream-1", f"fixture-upstream-b{index}".encode())]
    g.agent._run(turn)
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (turn,))["status"] == "succeeded"
    return g.store.one("SELECT a.*,j.amount FROM billing_attempts a JOIN billing_calls c ON c.id=a.call_key "
                       "JOIN billing_settlements s ON s.call_key=c.id JOIN billing_journal j ON j.id=s.journal_id "
                       "JOIN billing_runs r ON r.id=c.run_key WHERE r.run_id=?", (turn,))


def test_real_factory_charges_the_band_pinned_at_dispatch(governed):
    g = governed
    configure_banded(g)
    fund(g)
    peak = run_turn(g, 1)
    assert (peak["band"], peak["band_schedule_revision"], peak["amount"]) == ("peak", 1, PEAK_CHARGE)
    assert peak["dispatched_at"] and peak["completed_at"] and peak["dispatched_at"] <= peak["completed_at"]
    publish_calendar(g, {**CALENDAR, "holidays": china_days_around_now()})
    off = run_turn(g, 2)
    assert (off["band"], off["band_schedule_revision"], off["amount"]) == ("off_peak", 2, OFF_PEAK_CHARGE)
    # The ledger cost side was priced in the same band: peak 8*2 + 5*0.2 + 7*4 = 45, off-peak 22.5.
    costs = {row["attempt_key"]: (int(row["cost_n"]), int(row["cost_d"])) for row in g.store.all("SELECT * FROM billing_usage")}
    assert costs == {peak["id"]: (45, 1), off["id"]: (45, 2)}


def test_quote_and_consent_offer_both_bands(governed):
    g = governed
    configure_banded(g)
    offer = g.suite.caps.invoke("agent.billing.offer", {}, user_id=g.user["id"])["output"]
    # Wallet smallest units (scale 6) per million tokens. ``rates`` stay the peak (highest,
    # held) rates every existing reader already shows; off-peak rates are added beside them.
    assert offer["rates"]["output"] == {"numerator": "8000000", "denominator": "1"}
    assert offer["off_peak_rates"]["output"] == {"numerator": "4000000", "denominator": "1"}
    q = quote(g)
    assert q["off_peak_rates"]["input_cached"] == {"numerator": "200000", "denominator": "1"}
    consent = create(g, quote(g)["id"])
    assert consent["off_peak_rates"]["input_uncached"] == {"numerator": "2000000", "denominator": "1"}


def test_calendar_change_invalidates_outstanding_quotes_and_consents(governed):
    g = governed
    configure_banded(g)
    fund(g)
    pending = quote(g)
    consent = create(g, quote(g)["id"])
    assert consent["status"] == "active"
    publish_calendar(g, {**CALENDAR, "holidays": china_days_around_now()})
    status, code = refused(g.agent.start_turn, g.user, g.conversation["id"], "Fixture review", parent_id=None,
                           regenerate_of=None, quote_id=pending["id"])
    assert (status, code) == (409, "billing_model_configuration_changed")
    current = g.suite.caps.invoke("agent.consent.get", {"consent_id": consent["id"]},
                                  user_id=g.user["id"])["output"]
    assert current["status"] == "model_changed"
    with pytest.raises(ServiceError) as err:
        send(g, consent["id"])
    assert err.value.status == 409
    assert g.store.all("SELECT * FROM billing_attempts") == [] and g.seen == []
    # A fresh quote under the new calendar works.
    assert run_turn(g, 3)["band"] == "off_peak"


def test_adopting_a_markup_sale_price_invalidates_outstanding_quotes(governed):
    g = governed
    configure_banded(g)
    fund(g)
    pending = quote(g)
    markups = PriceMarkups(g.store, environment="test")
    with g.store.tx():
        sale = markups.publish({"name": "Fixture retail", "cost_price_id": g.ids["cost"], "markup": "2.5",
            "off_peak_markup": None, "terms_ref": "fixture:retail", "effective_from": 0},
            actor_id=g.user["id"], operation_id=uuid.uuid4().hex, reason="Fixture markup publication")
    # Publishing alone changes no route: the pending quote is still acceptable until adoption.
    assert g.provider.route().sale_price_id == g.ids["sale"]
    publish(g, {"action": "keep"}, {**g.spec, "route": {**g.spec["route"], "sale_price_id": sale["price_id"]}})
    status, code = refused(g.agent.start_turn, g.user, g.conversation["id"], "Fixture review", parent_id=None,
                           regenerate_of=None, quote_id=pending["id"])
    assert (status, code) == (409, "billing_model_configuration_changed")
    adopted = run_turn(g, 4)
    # Peak sale = cost 2/0.2/4 x 2.5 = 5/0.5/10 per token: 8*5 + 5*0.5 + 7*10 = 112.5; the half
    # unit is the ledger's carried remainder, not charged on this call.
    assert (adopted["band"], adopted["amount"]) == ("peak", 112)


def test_flat_quote_terms_keep_their_pre_band_shape(governed):
    from .test_agent_governed_factory import configure
    g = governed
    configure(g)
    q = quote(g)
    terms = json.loads(g.store.one("SELECT terms_json FROM agent_model_quotes WHERE id=?", (q["id"],))["terms_json"])
    assert not {"off_peak", "band_schedule", "basis"} & set(terms["sale"])
    assert q["off_peak_rates"] is None
