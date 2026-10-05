"""Time-banded tariffs: the governed schedule, the band pinned at dispatch and its settlement.

FIXTURE units, prices and calendars below are synthetic test choices, not Synwevo or DeepSeek
prices. No model call, payment or network is made. Instants are explicit UTC seconds.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3

import pytest

from synwevo_core.billing import BillingError, ChargeContext, Ledger, PriceSnapshot, Rate, Unit, Usage, UsageEvidence
from synwevo_core.billing.bands import (PUBLISHED_PEAK_WINDOWS, BandSchedules, ScheduleSpec, band_at,
                                        published_defaults, validate_schedule)
from synwevo_core.billing.catalogue import PriceCatalogue, PriceMarkups
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.models import BandRates
from synwevo_core.billing.provider_settings import ProviderSettings
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.store import Store

UNIT = Unit("FIXTURE-CNY", 2)
COST_UNIT = Unit("FIXTURE-COSTCNY", 2)


def utc(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


# Fixture calendar: DeepSeek's published windows and one synthetic holiday (a Thursday).
SPEC = {**published_defaults(), "holidays": [{"date": "2026-10-01", "name": "Fixture holiday"}],
        "source_ref": "fixture-calendar:v1"}


def snapshot(price_id, purpose, unit, peak, off_peak, *, schedule="deepseek", start=utc("2026-01-01T00:00:00")):
    return PriceSnapshot(price_id, purpose, unit, "deepseek", "fixture-model", *(Rate(x) for x in peak),
                         "fixture-terms-v1", start, per_tokens=1,
                         off_peak=BandRates(*(Rate(x) for x in off_peak)) if off_peak else None,
                         band_schedule=schedule if off_peak else None)


@pytest.mark.parametrize("instant,band", [
    ("2026-10-05T00:59:59", "off_peak"), ("2026-10-05T01:00:00", "peak"), ("2026-10-05T03:59:59", "peak"),
    ("2026-10-05T04:00:00", "off_peak"), ("2026-10-05T05:59:59", "off_peak"), ("2026-10-05T06:00:00", "peak"),
    ("2026-10-05T09:59:59", "peak"), ("2026-10-05T10:00:00", "off_peak"), ("2026-10-09T02:00:00", "peak"),
    ("2026-10-03T02:00:00", "off_peak"), ("2026-10-04T07:00:00", "off_peak"),   # Saturday, Sunday
    ("2026-10-01T02:00:00", "off_peak"), ("2026-10-01T07:00:00", "off_peak"),   # holiday (Thursday)
    ("2026-10-02T02:00:00", "peak"),                                           # the next workday
])
def test_band_boundaries_weekends_and_holidays(instant, band):
    assert band_at(ScheduleSpec.model_validate(SPEC), utc(instant)) == band


def test_holiday_is_a_china_calendar_date_while_windows_are_utc():
    # A window across UTC 15:00-17:00 shows which date a holiday covers: 2026-10-01 starts at
    # 16:00 UTC on 2026-09-30 (UTC+8), and ends at 16:00 UTC on 2026-10-01.
    spec = ScheduleSpec.model_validate({**SPEC, "peak_windows": [{"weekdays": [1, 2, 3, 4, 5], "start": "15:00", "end": "17:00"}]})
    assert band_at(spec, utc("2026-09-30T15:30:00")) == "peak"       # 23:30 on 30 Sep in China
    assert band_at(spec, utc("2026-09-30T16:30:00")) == "off_peak"   # 00:30 on the holiday
    assert band_at(spec, utc("2026-10-01T16:30:00")) == "peak"       # 00:30 on 2 Oct, a Friday


def test_published_defaults_are_deepseek_windows_and_seed_nothing(tmp_path):
    assert [dict(w) for w in PUBLISHED_PEAK_WINDOWS] == [
        {"weekdays": [1, 2, 3, 4, 5], "start": "01:00", "end": "04:00"},
        {"weekdays": [1, 2, 3, 4, 5], "start": "06:00", "end": "10:00"}]
    assert published_defaults()["holidays"] == [] and published_defaults()["holiday_utc_offset_minutes"] == 480
    store = Store(tmp_path / "isolated-bands-seed")
    assert store.all("SELECT * FROM billing_band_schedules") == []
    assert BandSchedules(store, environment="test").read()["spec"] is None


@pytest.mark.parametrize("change", [
    {"peak_windows": []}, {"peak_windows": [{"weekdays": [0], "start": "01:00", "end": "02:00"}]},
    {"peak_windows": [{"weekdays": [2, 1], "start": "01:00", "end": "02:00"}]},
    {"peak_windows": [{"weekdays": [1], "start": "04:00", "end": "01:00"}]},
    {"peak_windows": [{"weekdays": [1], "start": "1:00", "end": "02:00"}]},
    {"peak_windows": [{"weekdays": [1], "start": "01:00", "end": "02:00"}] * 2},
    {"holidays": [{"date": "2026-02-30", "name": "x"}]}, {"holidays": [{"date": "2026-10-01", "name": " "}]},
    {"holidays": [{"date": "2026-10-02", "name": "b"}, {"date": "2026-10-01", "name": "a"}]},
    {"holidays": [{"date": "2026-10-01", "name": "a"}, {"date": "2026-10-01", "name": "b"}]},
    {"holiday_utc_offset_minutes": True}, {"source_ref": "fixture?secret=1"}, {"extra": 1},
])
def test_invalid_schedules_are_refused_without_echo(change):
    with pytest.raises(ServiceError) as caught:
        validate_schedule({**SPEC, **change})
    assert caught.value.code == "band_schedule_invalid" and "secret" not in str(caught.value.message)


def test_off_peak_above_peak_or_half_banded_snapshot_is_refused():
    with pytest.raises(BillingError, match="invalid_price_bands"):
        snapshot("p", "cost", COST_UNIT, (2, 1, 4), (3, 1, 4))
    with pytest.raises(BillingError, match="invalid_price_bands"):
        PriceSnapshot("p", "cost", COST_UNIT, "deepseek", "m", Rate(1), Rate(1), Rate(1), "t", 0, band_schedule="deepseek")
    banded = snapshot("p", "cost", COST_UNIT, (2, 1, 8), (1, 1, 4))
    with pytest.raises(BillingError, match="price_band_required"):
        banded.exact(Usage(1, 1, 1))
    assert banded.exact(Usage(1, 1, 1), "peak") == 11 and banded.exact(Usage(1, 1, 1), "off_peak") == 6
    # Old single-rate snapshots stay valid as 'flat'; the band does not change them.
    old = {"price_id": "old", "purpose": "cost", "unit": {"code": "X", "scale": 2}, "provider": "deepseek", "model": "m",
           "input_uncached": {"numerator": 3, "denominator": 1}, "input_cached": {"numerator": 1, "denominator": 1},
           "output": {"numerator": 4, "denominator": 1}, "terms_ref": "t", "effective_from": 0, "per_tokens": 1}
    flat = PriceSnapshot.from_dict(old)
    assert not flat.banded and flat.exact(Usage(1, 1, 1)) == flat.exact(Usage(1, 1, 1), "peak") == 8
    assert PriceSnapshot.from_dict(json.loads(json.dumps({**old, **{"off_peak": None}}))) == flat


def authorize(t, context, action):
    """Test-only authorization: every fixture payer may spend; never a production rule."""
    return True


@pytest.fixture
def money(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-bands-money")
    identity = Identity(store)
    admin = identity.create_user("bands-admin@example.test", None, status="active", with_personal_workspace=False)["id"]
    payer = identity.create_user("bands-payer@example.test", None, status="active", with_personal_workspace=False)["id"]
    clock = [utc("2026-09-30T00:00:00")]
    ledger = Ledger(store, environment="test", namespace="fixture-bands", authorize=authorize, clock=lambda: clock[0])
    ledger.publish_price(snapshot("fixture-sale-banded", "sale", UNIT, (4, 2, 8), (2, 1, 4)))
    ledger.publish_price(snapshot("fixture-cost-banded", "cost", COST_UNIT, (2, 1, 4), (1, 1, 2)))
    ledger.publish_price(snapshot("fixture-sale-flat", "sale", UNIT, (4, 2, 8), None))
    ledger.publish_price(snapshot("fixture-cost-flat", "cost", COST_UNIT, (2, 1, 4), None))
    # Fixture wallet credit straight into the balance cache, as the ledger's own grant would.
    with ledger._tx() as t:
        wallet = ledger._account(t, UNIT.code, payer)
        source = ledger._account(t, UNIT.code, "platform", "test_payment")
        ledger._post(t, voucher="fixture-credit", unit=UNIT.code, debit=source, credit=wallet,
                     amount=10_000, kind="test_payment", reference="fixture")
    return {"store": store, "ledger": ledger, "admin": admin, "payer": payer, "clock": clock}


def publish_schedule(m, spec=SPEC, *, revision=0, operation="op-fixture-1"):
    with m["store"].tx():
        return BandSchedules(m["store"], environment="test", clock=lambda: m["clock"][0]).append(
            spec, expected_revision=revision, actor_id=m["admin"], reason="Fixture schedule publication",
            operation_id=operation)


def call(m, at, *, run, sale="fixture-sale-banded", cost="fixture-cost-banded"):
    ledger = m["ledger"]
    m["clock"][0] = at
    context = ChargeContext(m["payer"], m["payer"], run, "fixture-approval")
    ledger.open_run(context, unit=UNIT, budget=5000, max_calls=5, max_attempts=5, deadline=at + 900)
    key = ledger.reserve_call(context, logical_call_id="call", amount=500, price_id=sale, deadline=at + 600)
    ticket = ledger.prepare_attempt(key, attempt_id="attempt", owner="fixture-worker", cost_price_id=cost,
                                    requested_model="fixture-model")
    return key, ticket


def settle(m, ticket, *, at, usage=Usage(10, 10, 10)):
    m["clock"][0] = at
    return m["ledger"].settle_trusted_usage(ticket.attempt_key, UsageEvidence(
        "deepseek", "request-" + ticket.attempt_key, "fixture-final", usage, "fixture-model", "fixture-v1", at))


def attempt(m, ticket):
    return m["store"].one("SELECT * FROM billing_attempts WHERE id=?", (ticket.attempt_key,))


def test_band_is_pinned_from_dispatch_instant_and_both_timestamps_are_stored(money):
    m = money
    publish_schedule(m)
    _, peak = call(m, utc("2026-10-05T03:59:59"), run="run-peak")      # Monday, last peak second
    m["ledger"].dispatch(peak)
    # The answer completes after the window closed: still the dispatch band.
    result = settle(m, peak, at=utc("2026-10-05T04:03:00"))
    row = attempt(m, peak)
    assert (row["band"], row["band_schedule_revision"]) == ("peak", 1)
    assert (row["dispatched_at"], row["completed_at"]) == (utc("2026-10-05T03:59:59"), utc("2026-10-05T04:03:00"))
    assert result.charged == 10 * 4 + 10 * 2 + 10 * 8 and result.cost == 10 * 2 + 10 * 1 + 10 * 4

    _, off = call(m, utc("2026-10-05T04:00:00"), run="run-off")        # first off-peak second
    m["ledger"].dispatch(off)
    result = settle(m, off, at=utc("2026-10-05T04:00:30"))
    assert attempt(m, off)["band"] == "off_peak"
    assert result.charged == 10 * 2 + 10 * 1 + 10 * 4 and result.cost == 10 * 1 + 10 * 1 + 10 * 2


def test_band_follows_the_dispatch_instant_not_the_reservation(money):
    m = money
    publish_schedule(m)
    _, ticket = call(m, utc("2026-10-05T00:59:30"), run="run-cross")      # reserved off-peak
    m["clock"][0] = utc("2026-10-05T01:00:00")                            # sent at the first peak second
    m["ledger"].dispatch(ticket)
    row = attempt(m, ticket)
    assert (row["band"], row["dispatched_at"]) == ("peak", utc("2026-10-05T01:00:00"))
    assert settle(m, ticket, at=utc("2026-10-05T01:00:20")).charged == 140


@pytest.mark.parametrize("instant", ["2026-10-01T02:00:00", "2026-10-03T02:00:00"])
def test_holidays_and_weekends_are_charged_off_peak(money, instant):
    publish_schedule(money)
    _, ticket = call(money, utc(instant), run="run-" + instant)
    money["ledger"].dispatch(ticket)
    assert settle(money, ticket, at=utc(instant) + 5).charged == 70
    assert attempt(money, ticket)["band"] == "off_peak"


def test_schedule_change_after_dispatch_never_reprices_the_call(money):
    m = money
    publish_schedule(m)
    _, ticket = call(m, utc("2026-10-05T02:00:00"), run="run-change")
    m["ledger"].dispatch(ticket)
    # The administrator declares that Monday a holiday after the call was sent.
    publish_schedule(m, {**SPEC, "holidays": [*SPEC["holidays"], {"date": "2026-10-05", "name": "Fixture added"}]},
                     revision=1, operation="op-fixture-2")
    assert settle(m, ticket, at=utc("2026-10-05T02:01:00")).charged == 140
    assert (attempt(m, ticket)["band"], attempt(m, ticket)["band_schedule_revision"]) == ("peak", 1)
    # The next call uses the new revision.
    _, later = call(m, utc("2026-10-05T02:05:00"), run="run-after")
    m["ledger"].dispatch(later)
    assert (attempt(m, later)["band"], attempt(m, later)["band_schedule_revision"]) == ("off_peak", 2)


def test_banded_tariff_without_published_schedule_is_never_dispatched(money):
    m = money
    key, ticket = call(m, utc("2026-10-05T02:00:00"), run="run-closed")
    with pytest.raises(BillingError, match="price_band_schedule_unavailable"):
        m["ledger"].dispatch(ticket)
    row = attempt(m, ticket)
    assert (row["status"], row["dispatched_at"], row["band"]) == ("prepared", None, None)
    assert m["ledger"].cancel_call(key) == "released"


def test_flat_tariffs_pin_flat_and_need_no_schedule(money):
    m = money
    _, ticket = call(m, utc("2026-10-05T02:00:00"), run="run-flat", sale="fixture-sale-flat", cost="fixture-cost-flat")
    m["ledger"].dispatch(ticket)
    assert settle(m, ticket, at=utc("2026-10-05T02:00:10")).charged == 140
    row = attempt(m, ticket)
    assert (row["band"], row["band_schedule_revision"], row["completed_at"]) == ("flat", None, utc("2026-10-05T02:00:10"))


def test_unknown_usage_records_completion_and_pinned_facts_are_immutable(money):
    m = money
    publish_schedule(m)
    _, ticket = call(m, utc("2026-10-05T02:00:00"), run="run-pending")
    m["ledger"].dispatch(ticket)
    m["clock"][0] = utc("2026-10-05T02:00:40")
    m["ledger"].mark_usage_pending(ticket, reason="awaiting_final_usage")
    assert attempt(m, ticket)["completed_at"] == utc("2026-10-05T02:00:40")
    for sql in ("UPDATE billing_attempts SET band='off_peak'", "UPDATE billing_attempts SET band_schedule_revision=2",
                "UPDATE billing_attempts SET completed_at=1"):
        with pytest.raises(sqlite3.IntegrityError):
            m["store"].execute(sql)
    for sql in ("UPDATE billing_band_schedules SET spec_json='{}'", "DELETE FROM billing_band_schedules",
                "INSERT OR REPLACE INTO billing_band_schedules SELECT * FROM billing_band_schedules"):
        with pytest.raises(sqlite3.IntegrityError):
            m["store"].execute(sql)
    # Late trusted usage keeps the first completion instant.
    settle(m, ticket, at=utc("2026-10-05T02:09:00"))
    assert attempt(m, ticket)["completed_at"] == utc("2026-10-05T02:00:40")


def test_schedule_versions_are_consecutive_audited_facts(money):
    m = money
    first = publish_schedule(m)
    assert first["revision"] == 1 and first["changes"]["holidays_added"] == ["2026-10-01"]
    with pytest.raises(ServiceError) as stale:
        publish_schedule(m, {**SPEC, "holidays": []}, revision=0, operation="op-stale")
    assert stale.value.code == "band_schedule_revision_conflict"
    with pytest.raises(ServiceError) as same:
        publish_schedule(m, SPEC, revision=1, operation="op-same")
    assert same.value.code == "band_schedule_no_change"
    with pytest.raises(sqlite3.IntegrityError):
        m["store"].execute("INSERT INTO billing_band_schedules VALUES ('test','deepseek',5,'{}',?,'skip reason','op-x',0)",
                           (m["admin"],))
    second = publish_schedule(m, {**SPEC, "holidays": []}, revision=1, operation="op-fixture-2")
    assert second["changes"] == {"holidays_added": [], "holidays_removed": ["2026-10-01"], "holidays_renamed": [],
                                 "windows_changed": False}
    history = BandSchedules(m["store"], environment="test").history()
    assert [item["revision"] for item in history["items"]] == [2, 1]
    assert BandSchedules(m["store"], environment="live").read()["revision"] == 0


PRE_BAND_KEYS = {"price_id", "purpose", "unit", "provider", "model", "input_uncached", "input_cached", "output",
                 "terms_ref", "effective_from", "per_tokens"}


def on_disk(m, price_id):
    """The stored snapshot text exactly as written (Store's row reader decodes this column)."""
    with m["store"].conn() as c:
        return json.loads(c.execute("SELECT snapshot FROM billing_prices WHERE price_id=?", (price_id,)).fetchone()[0])


def test_flat_snapshots_keep_the_pre_band_serialization(money):
    # A rollback to a release without bands must still read every flat price published after
    # this change, and quote/consent terms of a flat price must not change shape on upgrade.
    m = money
    stored = on_disk(m, "fixture-sale-flat")
    assert set(stored) == PRE_BAND_KEYS and PriceSnapshot.from_dict(stored).to_dict() == stored
    with m["store"].tx():
        catalogue = PriceCatalogue(m["store"], environment="test", clock=lambda: m["clock"][0])
        draft, _ = catalogue.create(spec={"name": "Fixture flat", "purpose": "cost", "unit": {"code": "FIXFLAT", "scale": 2},
            "provider": "deepseek", "model": "fixture-model", "input_uncached": "2", "input_cached": "1", "output": "4",
            "terms_ref": "fixture:flat", "effective_from": 0}, actor_id=m["admin"], idempotency_key="fixture-flat-format-key")
        price_id = catalogue.publish(draft["id"], expected_revision=1, actor_id=m["admin"], operation_id="op-flat-format",
                                     reason="Fixture flat publication")["price_id"]
    assert set(on_disk(m, price_id)) == PRE_BAND_KEYS
    assert set(on_disk(m, "fixture-sale-banded")) == PRE_BAND_KEYS | {"off_peak", "band_schedule"}
    # A flat draft keeps its pre-band stored shape too: an idempotent create replayed across the
    # upgrade still matches its creation record, and a release without bands can read it.
    with m["store"].conn() as c:
        row = c.execute("SELECT spec_json,creation_json FROM model_price_drafts WHERE id=?", (draft["id"],)).fetchone()
    assert "off_peak" not in json.loads(row[0]) and "off_peak" not in json.loads(row[1])


ROUTE_SALE, ROUTE_COST = Unit("FIXROUTESALE", 2), Unit("FIXROUTECOST", 2)


def governed_route(m, *, banded=True):
    """Fixture provider route over FIXTURE prices; the credential is a fixture string, not a key."""
    store, clock = m["store"], (lambda: m["clock"][0])
    m["ledger"].publish_price(snapshot("fixture-route-sale", "sale", ROUTE_SALE, (4, 2, 8), (2, 1, 4) if banded else None))
    m["ledger"].publish_price(snapshot("fixture-route-cost", "cost", ROUTE_COST, (2, 1, 4), (1, 1, 2) if banded else None))
    with store.tx():
        CostSettings(store, environment="test", clock=clock).append({"enabled": True, "unit": asdict(ROUTE_COST),
            "daily_budget": "1000", "weekly_budget": "1000", "monthly_budget": "1000", "unknown_budget": "1000",
            "max_inflight": 2, "max_unknown": 2}, expected_revision=0, actor_id=m["admin"],
            reason="Fixture bounded costs", operation_id="op-fixture-costs")
        settings = ProviderSettings(store, environment="test", clock=clock)
        settings.append({"enabled": True, "route": {"requested_model": "fixture-request", "confirmed_route": "fixture-model",
            "confirmation_ref": "fixture:route", "cost_basis_ref": "fixture:cost", "account_confirmation_ref": "fixture:account",
            "sale_price_id": "fixture-route-sale", "cost_price_id": "fixture-route-cost", "input_token_ceiling": 100,
            "output_token_ceiling": 10, "valid_until": m["clock"][0] + 86400}},
            {"action": "replace", "api_key": "fixture-provider-credential-not-a-key"}, expected_revision=0,
            actor_id=m["admin"], reason="Fixture route publication", operation_id="op-fixture-route")
    return settings


def test_route_version_binds_the_schedule_revision_of_banded_prices(money):
    # Quotes and consents bind the route version; a calendar change re-bands future calls, so
    # it must invalidate them exactly like a price change does.
    m = money
    publish_schedule(m)
    settings = governed_route(m)
    assert settings.resolve().route["version"] == "deepseek:test:1:bands:1"
    publish_schedule(m, {**SPEC, "holidays": []}, revision=1, operation="op-fixture-2")
    assert settings.resolve().route["version"] == "deepseek:test:1:bands:2"


def test_route_sells_a_markup_price_only_over_its_own_cost_basis(money):
    # Sale = cost x markup holds for a route only when the route's cost price is the markup's
    # basis. A markup over another (say, superseded) cost price is refused, not adopted.
    m = money
    publish_schedule(m)
    settings = governed_route(m)
    markups = PriceMarkups(m["store"], environment="test", clock=lambda: m["clock"][0])
    with m["store"].tx():
        stale = markups.publish({"name": "Fixture over another cost", "cost_price_id": "fixture-cost-banded",
            "markup": "2", "off_peak_markup": None, "terms_ref": "fixture:retail", "effective_from": 0},
            actor_id=m["admin"], operation_id="op-fixture-markup-1", reason="Fixture markup publication")
        own = markups.publish({"name": "Fixture over route cost", "cost_price_id": "fixture-route-cost",
            "markup": "2", "off_peak_markup": None, "terms_ref": "fixture:retail", "effective_from": 0},
            actor_id=m["admin"], operation_id="op-fixture-markup-2", reason="Fixture markup publication")
    route = settings.read()["spec"]
    with m["store"].tx(), pytest.raises(ServiceError) as refused:
        settings.append({**route, "route": {**route["route"], "sale_price_id": stale["price_id"]}}, {"action": "keep"},
                        expected_revision=1, actor_id=m["admin"], reason="Fixture route change", operation_id="op-r2")
    assert (refused.value.status, refused.value.code) == (409, "model_price_markup_basis")
    with m["store"].tx():
        settings.append({**route, "route": {**route["route"], "sale_price_id": own["price_id"]}}, {"action": "keep"},
                        expected_revision=1, actor_id=m["admin"], reason="Fixture route change", operation_id="op-r3")
    assert settings.resolve().route["sale_price_id"] == own["price_id"]


def test_flat_route_version_ignores_the_schedule(money):
    m = money
    settings = governed_route(m, banded=False)
    assert settings.resolve().route["version"] == "deepseek:test:1"
    publish_schedule(m)
    assert settings.resolve().route["version"] == "deepseek:test:1"


def test_upgrade_adds_band_columns_to_an_existing_attempts_table(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-bands-upgrade")
    # Simulate a database written before this migration: drop the trigger and new columns.
    with store.conn() as c:
        c.executescript("DROP TRIGGER billing_attempts_pinned_band; DROP TRIGGER billing_attempts_preserve_contract;"
                        "ALTER TABLE billing_attempts DROP COLUMN completed_at;"
                        "ALTER TABLE billing_attempts DROP COLUMN band_schedule_revision;"
                        "ALTER TABLE billing_attempts DROP COLUMN band;")
    columns = {r["name"] for r in store.all("PRAGMA table_info(billing_attempts)")}
    assert not {"band", "band_schedule_revision", "completed_at"} & columns
    reopened = Store(store.root)
    columns = {r["name"] for r in reopened.all("PRAGMA table_info(billing_attempts)")}
    assert {"band", "band_schedule_revision", "completed_at"} <= columns
    assert reopened.one("SELECT name FROM sqlite_master WHERE type='trigger' AND name='billing_attempts_pinned_band'")
    table = reopened.one("SELECT sql FROM sqlite_master WHERE type='table' AND name='billing_attempts'")["sql"]
    assert "band IN ('flat','peak','off_peak')" in table
