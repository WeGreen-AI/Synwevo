"""Payer-budget recovery over isolated Store/Ledger fixtures, never production data.

Migration fixtures omit only the new schema module before Store creates the old database.
Capacity fixtures explicitly bulk-copy synthetic money facts inside the test database to
exercise the real 10,000-row bound without thousands of individually committed API calls.
"""
from datetime import datetime, timezone
import json

import pytest

from synwevo_core import suite_schema
from synwevo_core.billing import BillingError, Ledger, PaymentReceipt, Unit, Usage
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.store import Store

from .test_billing_ledger import (
    UNIT, assert_balanced, balance, context, dispatch, fixture_authorize,
    fixture_verify, fund, money, sale_price, settle, trust,
)
from .test_payer_budgets import configure, view


def _ledger(store, ticks, namespace):
    return Ledger(store, environment="test", namespace=namespace,
                  authorize=fixture_authorize, verify_payment=fixture_verify,
                  clock=lambda: ticks[0], max_hold_seconds=1000)


def _call(money, name, *, amount=1, unit=UNIT, price="fixture-sale-v1"):
    _, ledger, _, ticks = money
    ctx = context(money, run=name)
    ledger.open_run(ctx, unit=unit, budget=100_000, max_calls=10_000,
                    max_attempts=10_000, deadline=ticks[0] + 900)
    return ledger.reserve_call(ctx, logical_call_id="one", amount=amount,
                               price_id=price, deadline=ticks[0] + 500)


def _prepare(money, call):
    return money[1].prepare_attempt(
        call, attempt_id="fixture-prepared", owner="fixture-worker",
        cost_price_id="fixture-cost-v1", requested_model="fixture-model")


def _facts(store):
    tables = ("units", "prices", "accounts", "balances", "orders", "payments",
              "payment_events", "runs", "calls", "attempts", "usage", "settlements",
              "journal", "remainders", "events")
    return {table: store.all(f"SELECT * FROM billing_{table} ORDER BY rowid")
            for table in tables}


@pytest.fixture
def legacy_money(request, monkeypatch):
    # Store remains the migration runner. No dropping tables or disabling fact triggers.
    with monkeypatch.context() as legacy_schema:
        legacy_schema.setattr(suite_schema, "MODULES", tuple(
            name for name in suite_schema.MODULES if name != "payer_budgets"))
        fixture = request.getfixturevalue("money")
    store, ledger, users, ticks = fixture
    assert not store.one("SELECT 1 FROM sqlite_master WHERE name='billing_payer_budget_versions'")
    boundary = int(datetime(2034, 1, 2, tzinfo=timezone.utc).timestamp())
    ticks[0] = boundary - 30
    fund(fixture, 1000)
    pending = _call(fixture, "fixture-legacy-pending", amount=20)
    ticket = dispatch(fixture, pending)
    ledger.mark_usage_pending(ticket, reason="fixture_missing_terminal_usage")

    ticks[0] = boundary + 1
    other = _ledger(store, ticks, "fixture-legacy-other-app")
    other.publish_price(sale_price(price_id="fixture-sale-v2"))
    other_fixture = (store, other, users, ticks)
    sold = _call(other_fixture, "fixture-legacy-sale", amount=20, price="fixture-sale-v2")
    assert settle(other_fixture, dispatch(other_fixture, sold)).charged == 9
    return fixture


@pytest.mark.fresh_store_schema  # builds the pre-feature schema itself
def test_populated_pre_feature_migration_preserves_facts_without_grants(legacy_money):
    old_store, _, users, ticks = legacy_money
    before = _facts(old_store)
    for _ in range(2):
        reopened = Store(old_store.root)
        assert _facts(reopened) == before
        assert reopened.all("SELECT * FROM billing_payer_budget_versions") == []
        assert reopened.all("SELECT * FROM billing_payer_budget_admissions") == []
        assert reopened.one("SELECT 1 FROM sqlite_master WHERE name='billing_calls_payer_period'")
        ledger = _ledger(reopened, ticks, "fixture-new-app")
        ledger.payer_budgets = PayerBudgets(ledger)
        current = ledger.payer_budgets.read(users[0])["budgets"][0]
        assert (current["configured"], current["enabled"], current["revision"]) == (False, False, 0)
        assert current["history"] == []
        assert current["usage_state"] == "available"
        for period in current["periods"].values():
            assert (period["spent"], period["held"]) == ("9", "20")
        assert _facts(reopened) == before
        assert_balanced(reopened)


@pytest.mark.fresh_store_schema  # builds the pre-feature schema itself
def test_first_limits_include_legacy_namespaces_prices_and_old_holds_after_reopen(legacy_money):
    old_store, _, users, ticks = legacy_money
    reopened = Store(old_store.root)
    ledger = _ledger(reopened, ticks, "fixture-new-app")
    fixture = (reopened, ledger, users, ticks)
    configured = configure(fixture, daily_budget=29)
    assert configured["periods"]["daily"]["remaining"] == "0"
    with pytest.raises(BillingError, match="^payer_daily_budget_exceeded$"):
        _call(fixture, "fixture-no-reset")
    assert reopened.all("SELECT * FROM billing_payer_budget_admissions") == []

    configure(fixture, revision=1, daily_budget=30)
    call = _call(fixture, "fixture-last-unit")
    again = Store(old_store.root)
    new_ledger = _ledger(again, ticks, "fixture-yet-another-app")
    new_ledger.payer_budgets = PayerBudgets(new_ledger)
    current = new_ledger.payer_budgets.read(users[0])["budgets"][0]
    assert [item["revision"] for item in current["history"]] == [2, 1]
    assert current["revision"] == 2
    assert (current["periods"]["daily"]["spent"], current["periods"]["daily"]["held"],
            current["periods"]["daily"]["remaining"]) == ("9", "21", "0")
    assert again.all("SELECT call_key,revision FROM billing_payer_budget_admissions") == [
        {"call_key": call, "revision": 2}]
    with pytest.raises(BillingError, match="^payer_daily_budget_exceeded$"):
        _call((again, new_ledger, users, ticks), "fixture-reopen-no-reset")
    assert (balance(fixture).booked, balance(fixture).held) == (991, 21)
    assert_balanced(again)


SECOND_UNIT = Unit("FIXTURE-SECOND-RETAIL", 2)
SECOND_PRICE = "fixture-second-retail-sale"


@pytest.fixture
def two_retail_units(money):
    _, ledger, users, _ = money
    fund(money, 100)
    ledger.publish_price(sale_price(price_id=SECOND_PRICE, unit=SECOND_UNIT))
    ledger.create_payment_order(
        order_id="fixture-second-order", payer_user_id=users[0], provider="fixture-payments",
        merchant_id="fixture-merchant", amount_minor=100, currency="XTS", unit=SECOND_UNIT,
        credit_units=200, exchange_version="fixture-second-exchange")
    receipt = PaymentReceipt(
        "test", "fixture-payments", "fixture-merchant", "fixture-second-payment",
        "fixture-second-event", "fixture-second-order", 100, "XTS", "fixture-second-inbox", "b" * 64)
    trust(money, receipt)
    ledger.credit_verified_payment(receipt)
    configure(money, daily_budget=20)
    configure(money, unit_code=SECOND_UNIT.code, daily_budget=30)
    return money


def _unit_views(money):
    return {row["unit"]["code"]: row
            for row in money[1].payer_budgets.read(money[2][0])["budgets"]}


def test_funded_retail_units_reserve_independent_allowances(two_retail_units):
    fixture = two_retail_units
    _call(fixture, "fixture-first-unit-full", amount=20)
    second = _unit_views(fixture)[SECOND_UNIT.code]
    assert second["unit"] == {"code": SECOND_UNIT.code, "scale": SECOND_UNIT.scale}
    assert (second["periods"]["daily"]["held"], second["periods"]["daily"]["remaining"]) == ("0", "30")
    _call(fixture, "fixture-second-unit-full", amount=30, unit=SECOND_UNIT, price=SECOND_PRICE)
    for unit, price, expected in ((UNIT, "fixture-sale-v1", 20), (SECOND_UNIT, SECOND_PRICE, 30)):
        current = _unit_views(fixture)[unit.code]
        assert current["periods"]["daily"]["held"] == str(expected)
        assert current["periods"]["daily"]["remaining"] == "0"
        with pytest.raises(BillingError, match="^payer_daily_budget_exceeded$"):
            _call(fixture, "fixture-over-" + unit.code, unit=unit, price=price)
    assert_balanced(fixture[0])


def test_settlement_and_policy_changes_do_not_cross_retail_units(two_retail_units):
    fixture = two_retail_units
    first = _call(fixture, "fixture-first-sale", amount=20)
    assert settle(fixture, dispatch(fixture, first)).charged == 9
    second = _call(fixture, "fixture-second-sale", amount=30, unit=SECOND_UNIT, price=SECOND_PRICE)
    assert settle(fixture, dispatch(fixture, second), usage=Usage(2, 0, 0)).charged == 2
    configure(fixture, revision=1, enabled=False, daily_budget=9)
    unchanged = _unit_views(fixture)[SECOND_UNIT.code]
    assert unchanged["revision"] == 1 and unchanged["enabled"]
    assert (unchanged["periods"]["daily"]["spent"], unchanged["periods"]["daily"]["remaining"]) == ("2", "28")

    configure(fixture, unit_code=SECOND_UNIT.code, revision=1, daily_budget=3)
    _call(fixture, "fixture-second-last-unit", unit=SECOND_UNIT, price=SECOND_PRICE)
    with pytest.raises(BillingError, match="^payer_budget_disabled$"):
        _call(fixture, "fixture-first-still-disabled")
    with pytest.raises(BillingError, match="^payer_daily_budget_exceeded$"):
        _call(fixture, "fixture-second-no-reset", unit=SECOND_UNIT, price=SECOND_PRICE)
    current = _unit_views(fixture)
    assert current[UNIT.code]["periods"]["daily"]["spent"] == "9"
    assert current[SECOND_UNIT.code]["periods"]["daily"]["spent"] == "2"
    ledger, uid = fixture[1], fixture[2][0]
    for unit, booked, held in ((UNIT, 91, 0), (SECOND_UNIT, 198, 1)):
        wallet = ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=unit)
        assert (wallet.booked, wallet.held) == (booked, held)
    assert_balanced(fixture[0])


def _insert_fixture_copies(t, table, template, changes):
    """Bulk fixture data only; retain schema constraints and journal balance triggers."""
    columns = tuple(template)
    t.executemany(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        (tuple(change.get(key, template[key]) for key in columns) for change in changes))


@pytest.fixture
def capacity_money(money):
    store, ledger, _, _ = money
    assert store.instance_kind() == "test"
    fund(money, 100_000)
    # A real one-unit settlement supplies consistent FK-linked fixture templates.
    sold = _call(money, "fixture-capacity-history")
    ticket = dispatch(money, sold)
    assert settle(money, ticket, usage=Usage(1, 0, 0)).charged == 1
    with store.tx() as t:
        call = dict(t.execute("SELECT * FROM billing_calls WHERE id=?", (sold,)).fetchone())
        attempt = dict(t.execute("SELECT * FROM billing_attempts WHERE id=?", (ticket.attempt_key,)).fetchone())
        usage = dict(t.execute("SELECT * FROM billing_usage WHERE attempt_key=?", (ticket.attempt_key,)).fetchone())
        settlement = dict(t.execute("SELECT * FROM billing_settlements WHERE call_key=?", (sold,)).fetchone())
        journal = dict(t.execute("SELECT * FROM billing_journal WHERE id=?", (settlement["journal_id"],)).fetchone())
        indices = range(1, 5000)
        _insert_fixture_copies(t, "billing_calls", call, (
            {"id": f"fixture-sale-{i}", "logical_call_id": f"fixture-sale-{i}"} for i in indices))
        _insert_fixture_copies(t, "billing_attempts", attempt, (
            {"id": f"fixture-attempt-{i}", "call_key": f"fixture-sale-{i}",
             "fence": f"fixture-fence-{i}", "request_id": f"fixture-request-{i}"} for i in indices))
        evidence = json.loads(usage["facts"])
        _insert_fixture_copies(t, "billing_usage", usage, (
            {"attempt_key": f"fixture-attempt-{i}", "facts": json.dumps({
                **evidence, "request_id": f"fixture-request-{i}",
                "evidence_ref": f"fixture-terminal-usage-{i}"})} for i in indices))
        _insert_fixture_copies(t, "billing_journal", journal, (
            {"id": f"fixture-journal-{i}", "voucher": f"fixture-voucher-{i}",
             "reference": f"fixture-sale-{i}"} for i in indices))
        _insert_fixture_copies(t, "billing_settlements", settlement, (
            {"call_key": f"fixture-sale-{i}", "attempt_key": f"fixture-attempt-{i}",
             "journal_id": f"fixture-journal-{i}"} for i in indices))
        _insert_fixture_copies(t, "billing_calls", call, (
            {"id": f"fixture-held-{i}", "logical_call_id": f"fixture-held-{i}",
             "status": "reserved"} for i in indices))
    # 5,000 settled + 4,999 held historical fixture calls, then one real admission.
    configure(money, daily_budget=20_000, weekly_budget=40_000, monthly_budget=60_000)
    pending = _call(money, "fixture-capacity-target")
    prepared = _prepare(money, pending)
    assert PayerBudgets.CAPACITY == 10_000
    assert store.one("SELECT COUNT(*) AS n FROM billing_calls")["n"] == 10_000
    assert_balanced(store)
    return money, prepared, call


def _settled_copies(t, call, indices):
    """Bulk fixture data only: settled copies of the real settled ``call``, each with its own attempt,
    usage, journal entry and settlement (as capacity_money writes them)."""
    settlement = dict(t.execute("SELECT * FROM billing_settlements WHERE call_key=?", (call["id"],)).fetchone())
    attempt = dict(t.execute("SELECT * FROM billing_attempts WHERE id=?", (settlement["attempt_key"],)).fetchone())
    usage = dict(t.execute("SELECT * FROM billing_usage WHERE attempt_key=?", (attempt["id"],)).fetchone())
    journal = dict(t.execute("SELECT * FROM billing_journal WHERE id=?", (settlement["journal_id"],)).fetchone())
    _insert_fixture_copies(t, "billing_calls", call, (
        {"id": f"fixture-sale-{i}", "logical_call_id": f"fixture-sale-{i}"} for i in indices))
    _insert_fixture_copies(t, "billing_attempts", attempt, (
        {"id": f"fixture-attempt-{i}", "call_key": f"fixture-sale-{i}",
         "fence": f"fixture-fence-{i}", "request_id": f"fixture-request-{i}"} for i in indices))
    _insert_fixture_copies(t, "billing_usage", usage, ({"attempt_key": f"fixture-attempt-{i}"} for i in indices))
    _insert_fixture_copies(t, "billing_journal", journal, (
        {"id": f"fixture-journal-{i}", "voucher": f"fixture-voucher-{i}",
         "reference": f"fixture-sale-{i}"} for i in indices))
    _insert_fixture_copies(t, "billing_settlements", settlement, (
        {"call_key": f"fixture-sale-{i}", "attempt_key": f"fixture-attempt-{i}",
         "journal_id": f"fixture-journal-{i}"} for i in indices))


def _overflow_fixture(capacity_money):
    money, _, template = capacity_money
    # Only holds count against the bound (settled history is read from per-wallet day rows, cost-capacity
    # phase 2): 4,999 bulk holds and the target, plus 5,001 more holds, exceed 10,000 reserved calls.
    with money[0].tx() as t:
        _insert_fixture_copies(t, "billing_calls", template, (
            {"id": f"fixture-over-capacity-{i}", "logical_call_id": f"fixture-over-capacity-{i}",
             "status": "reserved"} for i in range(5_001)))
    assert money[0].one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='reserved'")["n"] == 10_001
    assert money[0].one("SELECT COUNT(*) AS n FROM billing_calls")["n"] == 15_001


def test_exact_fact_capacity_reports_all_exposure_and_allows_dispatch(capacity_money):
    money, ticket, template = capacity_money
    current = view(money)
    assert current["usage_state"] == "available"
    assert (current["periods"]["daily"]["spent"], current["periods"]["daily"]["held"],
            current["periods"]["daily"]["remaining"]) == ("5000", "5000", "10000")
    money[1].dispatch(ticket)
    assert settle(money, ticket, usage=Usage(1, 0, 0)).charged == 1
    after = view(money)
    assert after["usage_state"] == "available"
    assert (after["periods"]["daily"]["spent"], after["periods"]["daily"]["held"]) == ("5001", "4999")
    # Settled volume never refuses: 10,000 more settled calls and one more hold are still exact.
    with money[0].tx() as t:
        _settled_copies(t, template, range(5_000, 15_000))
        _insert_fixture_copies(t, "billing_calls", template, [{
            "id": "fixture-held-extra", "logical_call_id": "fixture-held-extra", "status": "reserved"}])
    more = view(money)
    assert more["usage_state"] == "available"
    assert (more["periods"]["daily"]["spent"], more["periods"]["daily"]["held"]) == ("15001", "5000")
    assert_balanced(money[0])


def test_over_capacity_read_and_disable_preserve_unknown_usage_and_history(capacity_money):
    money, _, _ = capacity_money
    _overflow_fixture(capacity_money)
    current = view(money)
    assert current["usage_state"] == "capacity" and current["periods"] is None
    assert current["history"][0]["revision"] == 1
    before = balance(money)
    disabled = configure(money, revision=1, enabled=False, daily_budget=20_000,
                         weekly_budget=40_000, monthly_budget=60_000)
    assert not disabled["enabled"] and disabled["revision"] == 2
    assert disabled["usage_state"] == "capacity" and disabled["periods"] is None
    assert [row["enabled"] for row in disabled["history"]] == [False, True]
    assert view(money) == disabled
    assert balance(money) == before


@pytest.mark.parametrize("action", ["reserve", "dispatch"])
def test_over_capacity_refuses_money_admission_without_partial_writes(capacity_money, action):
    money, ticket, _ = capacity_money
    _overflow_fixture(capacity_money)
    store, ledger, _, _ = money
    before = balance(money)
    events = store.all("SELECT * FROM billing_events ORDER BY seq")
    admissions = store.all("SELECT * FROM billing_payer_budget_admissions")
    with pytest.raises(BillingError, match="^payer_budget_capacity$"):
        if action == "reserve":
            _call(money, "fixture-over-capacity-reservation")
        else:
            ledger.dispatch(ticket)
    assert store.one("SELECT COUNT(*) AS n FROM billing_calls")["n"] == 15_001
    assert store.all("SELECT * FROM billing_events ORDER BY seq") == events
    assert store.all("SELECT * FROM billing_payer_budget_admissions") == admissions
    assert store.one("SELECT status,dispatched_at FROM billing_attempts WHERE id=?", (ticket.attempt_key,)) == {
        "status": "prepared", "dispatched_at": None}
    assert balance(money) == before


@pytest.fixture
def inconsistent_money(money):
    fund(money)
    configure(money)
    corrupt = _call(money, "fixture-missing-settlement", amount=20)
    pending = _call(money, "fixture-inconsistent-target")
    ticket = _prepare(money, pending)
    # Deliberately inconsistent test-only state: settled status without its money fact.
    # Do not disable immutable-fact triggers or remove an existing settlement/journal.
    money[0].execute("UPDATE billing_calls SET status='settled' WHERE id=?", (corrupt,))
    assert not money[0].one("SELECT 1 FROM billing_settlements WHERE call_key=?", (corrupt,))
    return money, ticket


def test_missing_settlement_read_and_disable_never_invent_zero_usage(inconsistent_money):
    money, _ = inconsistent_money
    current = view(money)
    assert current["usage_state"] == "inconsistent" and current["periods"] is None
    assert current["history"][0]["revision"] == 1
    before = _facts(money[0])
    disabled = configure(money, revision=1, enabled=False)
    assert not disabled["enabled"] and disabled["revision"] == 2
    assert disabled["usage_state"] == "inconsistent" and disabled["periods"] is None
    assert [row["revision"] for row in disabled["history"]] == [2, 1]
    assert view(money) == disabled
    assert _facts(money[0]) == before


@pytest.mark.parametrize("action", ["reserve", "dispatch"])
def test_missing_settlement_refuses_money_admission_without_partial_writes(inconsistent_money, action):
    money, ticket = inconsistent_money
    store, ledger, _, _ = money
    before = balance(money)
    calls = store.all("SELECT * FROM billing_calls ORDER BY id")
    events = store.all("SELECT * FROM billing_events ORDER BY seq")
    admissions = store.all("SELECT * FROM billing_payer_budget_admissions ORDER BY call_key")
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
        if action == "reserve":
            _call(money, "fixture-inconsistent-reservation")
        else:
            ledger.dispatch(ticket)
    assert store.all("SELECT * FROM billing_calls ORDER BY id") == calls
    assert store.all("SELECT * FROM billing_events ORDER BY seq") == events
    assert store.all("SELECT * FROM billing_payer_budget_admissions ORDER BY call_key") == admissions
    assert store.one("SELECT status,dispatched_at FROM billing_attempts WHERE id=?", (ticket.attempt_key,)) == {
        "status": "prepared", "dispatched_at": None}
    assert balance(money) == before
