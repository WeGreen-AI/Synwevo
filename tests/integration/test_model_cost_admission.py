"""Isolated platform-cost fixtures: real Store/Ledger, no keys or external calls.

The payment fixture uses its durable verified inbox. Budget expectations below are
independent constants/arithmetic, not results calculated by admission implementation.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime
from fractions import Fraction
import json
import re
import sqlite3
from threading import Barrier

import pytest

from synwevo_core.billing import AttemptTicket, BillingError, Ledger, Rate, Unit, Usage
from synwevo_core.billing.admission import CostAdmission, CostPolicy
from synwevo_core.billing.risk import RiskControls
from synwevo_core.store import Store
from tests.integration.test_billing_ledger import (
    COST_UNIT, START, UNIT, balance, context, cost_price, evidence, fixture_authorize,
    fixture_verify, fund, money, sale_price,
)


def policy(**changes):
    """Explicit test-only limits and stable provider-account identity."""
    return replace(CostPolicy("fixture-policy-v1", "fixture-provider-account", "fixture-provider",
                              COST_UNIT, True, 1000, 2000, 3000, 1000, 100, 100), **changes)


@pytest.fixture
def funded(money):
    fund(money, 10_000)
    return money


def prepare(money, name, *, ledger=None, cost_id="fixture-cost-v1", sale_id="fixture-sale-v1",
            hold=1, deadline_seconds=500):
    ledger = ledger or money[1]
    now = money[3][0]
    ctx = context(money, run="cost-run-" + name)
    ledger.open_run(ctx, unit=UNIT, budget=10_000, max_calls=10_000, max_attempts=10_000, deadline=now+900)
    call = ledger.reserve_call(ctx, logical_call_id=name, amount=hold, price_id=sale_id,
                               deadline=now+deadline_seconds)
    ticket = ledger.prepare_attempt(call, attempt_id=name, owner="fixture-cost-worker",
                                     cost_price_id=cost_id, requested_model="fixture-model-alias")
    return call, ticket


def send(admission, ticket, *, amount, expires_at=None):
    expires_at = admission.ledger._now() + 120 if expires_at is None else expires_at
    admission.ledger.dispatch(ticket, admission=lambda t, a: admission.admit(
        t, a, amount=amount, expires_at=expires_at))


def exact(snapshot, period):
    return Fraction(snapshot[period]["numerator"], snapshot[period]["denominator"])


def utc(value):
    return int(datetime.fromisoformat(value + "+00:00").timestamp())


def sibling(money, *, namespace="fixture-other", environment="test", independent=False):
    store = Store(money[0].root) if independent else money[0]
    return Ledger(store, environment=environment, namespace=namespace, authorize=fixture_authorize,
                  verify_payment=fixture_verify, clock=lambda: money[3][0])


@pytest.mark.parametrize("field", ["daily_budget", "weekly_budget", "monthly_budget", "unknown_budget"])
@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None, 2**63])
def test_policy_money_is_strict_nonnegative_int64(field, value):
    with pytest.raises(BillingError):
        policy(**{field: value})


@pytest.mark.parametrize("field", ["max_inflight", "max_unknown"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.0, "1", None, 10_001])
def test_policy_slots_are_strict_positive_bounded_integers(field, value):
    with pytest.raises(BillingError):
        policy(**{field: value})


@pytest.mark.parametrize("changes", [{"revision": ""}, {"account_ref": " "}, {"provider": "x\n"},
                                      {"unit": "USD"}, {"enabled": 1}, {"enabled": None}])
def test_policy_requires_identity_unit_and_boolean_enabled(changes):
    with pytest.raises(BillingError):
        policy(**changes)


def test_policy_is_frozen_has_no_defaults_and_allows_exact_limits():
    with pytest.raises(TypeError):
        CostPolicy()
    p = policy(daily_budget=0, weekly_budget=2**63-1, max_inflight=10_000, max_unknown=1)
    with pytest.raises(FrozenInstanceError):
        p.enabled = False
    with pytest.raises(FrozenInstanceError):
        p.unit.scale = 1


@pytest.mark.parametrize("value", [None, {}, True, asdict(policy())])
def test_current_policy_must_return_cost_policy(funded, value):
    _, ticket = prepare(funded, "bad-policy")
    with pytest.raises(BillingError, match="invalid_cost_policy"):
        send(CostAdmission(funded[1], current_policy=lambda: value), ticket, amount=1)
    assert funded[0].all("SELECT * FROM model_cost_admissions") == []


def test_zero_budgets_permit_only_explicit_zero_bound_and_still_consume_slots(funded):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(
        daily_budget=0, weekly_budget=0, monthly_budget=0, unknown_budget=0, max_unknown=1))
    _, first = prepare(funded, "zero")
    send(admission, first, amount=0)
    snapshot = admission.snapshot()
    assert exact(snapshot, "daily") == snapshot["unknown_reserved_units"] == 0
    assert snapshot["unknown_count"] == snapshot["inflight"] == 1
    _, second = prepare(funded, "zero-again")
    with pytest.raises(BillingError, match="cost_unknown_limit"):
        send(admission, second, amount=0)


def test_current_policy_rechecks_revision_and_disable_does_not_block_settlement(funded):
    _, ticket = prepare(funded, "before-disable")
    current = [policy()]
    admission = CostAdmission(funded[1], current_policy=lambda: current[0])
    send(admission, ticket, amount=20)
    current[0] = replace(current[0], revision="fixture-policy-v2", enabled=False)
    _, refused = prepare(funded, "after-disable")
    with pytest.raises(BillingError, match="cost_admission_disabled"):
        send(admission, refused, amount=1)
    result = funded[1].settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    assert result.cost == 19
    assert exact(admission.snapshot(), "daily") == 19
    row = funded[0].one("SELECT * FROM model_cost_admissions")
    assert row["policy_revision"] == "fixture-policy-v1"
    assert json.loads(row["limits_json"]) == asdict(policy())


@pytest.mark.parametrize("field", ["amount", "expires_at"])
@pytest.mark.parametrize("value", [True, -1, 1.0, "1", None, 2**63])
def test_admission_amount_and_expiry_are_strict(funded, field, value):
    _, ticket = prepare(funded, "bad-amount")
    admission = CostAdmission(funded[1], current_policy=policy)
    kwargs = {"amount": 1, "expires_at": START+120, field: value}
    with funded[1]._tx() as t, pytest.raises(BillingError):
        admission.admit(t, ticket, **kwargs)
    assert funded[0].all("SELECT * FROM model_cost_admissions") == []


@pytest.mark.parametrize("offset", [-1, 0])
def test_expiry_must_be_in_future(funded, offset):
    _, ticket = prepare(funded, "expired")
    with pytest.raises(BillingError, match="invalid_cost_expiry"):
        send(CostAdmission(funded[1], current_policy=policy), ticket, amount=1, expires_at=START+offset)


def test_transaction_is_same_store_managed_writer_with_full_durability(funded):
    store, ledger, _, _ = funded
    _, ticket = prepare(funded, "transaction")
    admission = CostAdmission(ledger, current_policy=policy)

    def admit(t):
        admission.admit(t, ticket, amount=1, expires_at=START+120)

    with pytest.raises(BillingError, match="cost_admission_requires_transaction"):
        admit(None)
    with store.conn() as c:
        with pytest.raises(BillingError, match="cost_admission_requires_transaction"):
            admit(c)
        c.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(BillingError, match="cost_admission_requires_transaction"):
                admit(c)
        finally:
            c.execute("ROLLBACK")
        c.execute("PRAGMA synchronous=NORMAL")
    with store.tx() as t, pytest.raises(BillingError, match="cost_admission_requires_full_durability"):
        admit(t)
    other = Store(store.root)
    with other.tx() as t, pytest.raises(BillingError, match="cost_admission_requires_transaction"):
        admit(t)

    def callback(t, a):
        with store.conn() as c:
            assert t is c and t.in_transaction
        assert t.execute("PRAGMA synchronous").fetchone()[0] == 2
        admit(t)

    ledger.dispatch(ticket, admission=callback)
    with store.conn() as c:
        assert not c.in_transaction
    assert store.one("SELECT status FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["status"] == "dispatch_unknown"


@pytest.mark.parametrize("field", ["owner", "fence", "attempt_key"])
def test_ticket_tampering_is_refused_inside_admission(funded, field):
    _, ticket = prepare(funded, "tampered")
    admission = CostAdmission(funded[1], current_policy=policy)
    with funded[1]._tx() as t, pytest.raises(BillingError):
        admission.admit(t, replace(ticket, **{field: "fixture-forged"}), amount=1, expires_at=START+120)
    assert funded[0].all("SELECT * FROM model_cost_admissions") == []


@pytest.mark.parametrize("ticket", [None, {}, AttemptTicket("", "owner", "fence")])
def test_invalid_ticket_type_and_identifiers_are_refused(funded, ticket):
    admission = CostAdmission(funded[1], current_policy=policy)
    with funded[1]._tx() as t, pytest.raises(BillingError):
        admission.admit(t, ticket, amount=1, expires_at=START+120)


@pytest.mark.parametrize("kind", ["namespace", "environment"])
def test_foreign_ledger_ticket_is_refused(funded, kind):
    _, ticket = prepare(funded, "foreign")
    other = sibling(funded, namespace="fixture-other" if kind == "namespace" else funded[1].namespace,
                    environment="test" if kind == "namespace" else "live")
    admission = CostAdmission(other, current_policy=policy)
    with other._tx() as t, pytest.raises(BillingError, match="call_not_found"):
        admission.admit(t, ticket, amount=1, expires_at=START+120)


@pytest.mark.parametrize("state", ["expired-lease", "expired-call", "cancelled", "dispatched", "settled"])
def test_ticket_requires_live_prepared_attempt_and_call(funded, state):
    store, ledger, _, ticks = funded
    call, ticket = prepare(funded, state)
    admission = CostAdmission(ledger, current_policy=policy)
    if state == "expired-lease":
        ticks[0] += 60
    elif state == "expired-call":
        ticks[0] += 500
    elif state == "cancelled":
        ledger.cancel_call(call)
    else:
        ledger.dispatch(ticket)
        if state == "settled":
            ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    with ledger._tx() as t, pytest.raises(BillingError, match="dispatch_fenced"):
        admission.admit(t, ticket, amount=1, expires_at=ticks[0]+120)
    assert store.all("SELECT * FROM model_cost_admissions") == []


def test_reclaimed_attempt_requires_new_fence(funded):
    _, old = prepare(funded, "reclaim")
    funded[3][0] += 60
    new = funded[1].claim_prepared(old.attempt_key, owner="fixture-new-worker")
    admission = CostAdmission(funded[1], current_policy=policy)
    with funded[1]._tx() as t, pytest.raises(BillingError, match="dispatch_fenced"):
        admission.admit(t, old, amount=1, expires_at=START+120)
    send(admission, new, amount=1)


@pytest.mark.parametrize("changes,code", [({"provider": "fixture-other-provider"}, "cost_provider_mismatch"),
    ({"unit": Unit("FIXTURE-OTHER-COST", 8)}, "cost_unit_mismatch"),
    ({"unit": Unit(COST_UNIT.code, 7)}, "cost_unit_mismatch")])
def test_policy_matches_immutable_cost_price(funded, changes, code):
    _, ticket = prepare(funded, "price")
    with pytest.raises(BillingError, match=code):
        send(CostAdmission(funded[1], current_policy=lambda: policy(**changes)), ticket, amount=1)


def test_concurrent_independent_stores_compete_for_last_account_budget(funded):
    count = 8
    ledgers = [sibling(funded, namespace=f"fixture-key-rotation-{i}", independent=True) for i in range(count)]
    tickets = [prepare(funded, str(i), ledger=ledger)[1] for i, ledger in enumerate(ledgers)]
    barrier = Barrier(count)

    def compete(i):
        admission = CostAdmission(ledgers[i], current_policy=lambda: policy(daily_budget=1))
        barrier.wait()
        try:
            send(admission, tickets[i], amount=1)
            return "admitted"
        except BillingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(compete, range(count)))
    assert results.count("admitted") == 1
    assert results.count("cost_daily_budget_exceeded") == 7
    assert len(funded[0].all("SELECT * FROM model_cost_admissions")) == 1
    assert len(funded[0].all("SELECT * FROM billing_attempts WHERE status='prepared'")) == 7


def test_namespace_and_policy_revision_cannot_reset_account_cost(funded):
    _, first = prepare(funded, "first")
    send(CostAdmission(funded[1], current_policy=lambda: policy(daily_budget=5)), first, amount=5)
    other = sibling(funded)
    _, second = prepare(funded, "second", ledger=other)
    admission = CostAdmission(other, current_policy=lambda: policy(revision="fixture-key-rotated", daily_budget=5))
    assert exact(admission.snapshot(), "daily") == 5
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, second, amount=1)
    assert funded[0].one("SELECT status FROM billing_attempts WHERE id=?", (second.attempt_key,))["status"] == "prepared"


def test_changing_cost_unit_cannot_hide_same_account_exposure(funded):
    _, first = prepare(funded, "old-unit")
    send(CostAdmission(funded[1], current_policy=policy), first, amount=5)
    new_unit = Unit("FIXTURE-OTHER-COST", 8)
    new_price = cost_price(price_id="fixture-new-cost-unit", unit=new_unit)
    funded[1].publish_price(new_price)
    _, second = prepare(funded, "new-unit", cost_id=new_price.price_id)
    changed = CostAdmission(funded[1], current_policy=lambda: policy(unit=new_unit))
    with pytest.raises(BillingError, match="cost_unit_mismatch"):
        send(changed, second, amount=1)
    changed = CostAdmission(funded[1], current_policy=lambda: policy(unit=Unit(COST_UNIT.code, 7)))
    with pytest.raises(BillingError, match="cost_unit_mismatch"):
        changed.snapshot()


def test_environment_and_real_provider_accounts_have_separate_budgets(funded):
    p = policy(daily_budget=1)
    _, first = prepare(funded, "test")
    send(CostAdmission(funded[1], current_policy=lambda: p), first, amount=1)
    live = sibling(funded, environment="live")  # Isolated local fixture, never a real provider account.
    live.publish_price(sale_price())
    live.publish_price(cost_price())
    fund(funded, 100, order="fixture-live-order", ledger=live)
    _, second = prepare(funded, "live", ledger=live)
    # Live paid dispatch admits only allowlisted payers until a published risk section says otherwise.
    RiskControls(funded[0], environment="live").allow("user", funded[2][0], actor_id=funded[2][1],
                                                      reason="isolated live fixture cohort")
    live_admission = CostAdmission(live, current_policy=lambda: p)
    send(live_admission, second, amount=1)
    _, third = prepare(funded, "different-account")
    other = CostAdmission(funded[1], current_policy=lambda: replace(p, account_ref="fixture-distinct-real-account"))
    send(other, third, amount=1)
    assert exact(live_admission.snapshot(), "daily") == exact(other.snapshot(), "daily") == 1


@pytest.mark.parametrize("period,before,after", [
    ("daily", "2033-05-18T23:59:59", "2033-05-19T00:00:00"),
    ("weekly", "2033-05-22T23:59:59", "2033-05-23T00:00:00"),
    ("monthly", "2033-05-31T23:59:59", "2033-06-01T00:00:00"),
    ("monthly", "2036-02-29T23:59:59", "2036-03-01T00:00:00"),
])
def test_fixed_utc_boundaries_reset_only_the_matching_period(funded, period, before, after):
    funded[3][0] = utc(before)
    p = policy(**{period + "_budget": 1})
    admission = CostAdmission(funded[1], current_policy=lambda: p)
    _, first = prepare(funded, "before")
    send(admission, first, amount=1)
    _, second = prepare(funded, "boundary")
    with pytest.raises(BillingError, match="cost_" + period + "_budget_exceeded"):
        send(admission, second, amount=1)
    funded[3][0] = utc(after)
    assert exact(admission.snapshot(), period) == 0
    assert admission.snapshot()["unknown_reserved_units"] == 1
    send(admission, second, amount=1)
    assert exact(admission.snapshot(), period) == 1


def test_week_can_start_in_previous_month_and_usage_stays_in_admitted_period(funded):
    ledger, ticks = funded[1], funded[3]
    ticks[0] = utc("2033-05-31T23:59:59")
    admission = CostAdmission(ledger, current_policy=policy)
    _, ticket = prepare(funded, "may")
    send(admission, ticket, amount=20)
    ticks[0] = utc("2033-06-01T12:00:00")
    result = ledger.settle_trusted_usage(ticket.attempt_key, replace(evidence(ticket), metered_at=ticks[0]))
    assert result.late and result.cost == 19
    snapshot = admission.snapshot()
    assert snapshot["windows"] == {"daily": utc("2033-06-01T00:00:00"),
                                  "weekly": utc("2033-05-30T00:00:00"),
                                  "monthly": utc("2033-06-01T00:00:00")}
    assert exact(snapshot, "daily") == exact(snapshot, "monthly") == 0
    assert exact(snapshot, "weekly") == 19
    assert snapshot["unknown_count"] == snapshot["inflight"] == 0


def test_wallet_release_and_expiry_never_clear_unknown_exposure(funded):
    store, ledger, _, ticks = funded
    current = [policy(unknown_budget=20)]
    admission = CostAdmission(ledger, current_policy=lambda: current[0])
    call, first = prepare(funded, "unknown", hold=20)
    send(admission, first, amount=20, expires_at=START+600)
    pending = store.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,))
    assert pending == {"attempt_key": first.attempt_key, "environment": "test", "provider": "fixture-provider",
                       "account_ref": "fixture-provider-account", "admitted_at": START}
    ledger.mark_usage_pending(first, request_id=evidence(first).request_id)
    ledger.cancel_call(call)
    ticks[0] = START+500
    assert ledger.expire_due() == 1 and balance(funded).held == 0
    assert store.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,)) == pending
    snapshot = admission.snapshot()
    assert (snapshot["unknown_reserved_units"], snapshot["unknown_count"], snapshot["inflight"]) == (20, 1, 1)
    ticks[0] = START+600
    assert admission.snapshot()["inflight"] == 0
    assert store.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,)) == pending
    ticks[0] = utc("2034-01-01T00:00:00")
    snapshot = admission.snapshot()
    assert [exact(snapshot, p) for p in ("daily", "weekly", "monthly")] == [0, 0, 0]
    assert snapshot["unknown_reserved_units"] == 20
    assert store.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,)) == pending
    _, second = prepare(funded, "next-year")
    with pytest.raises(BillingError, match="cost_unknown_budget_exceeded"):
        send(admission, second, amount=1)
    current[0] = policy(unknown_budget=100, max_unknown=1)
    with pytest.raises(BillingError, match="cost_unknown_limit"):
        send(admission, second, amount=0)
    result = ledger.settle_trusted_usage(first.attempt_key, evidence(first))
    assert result.late and result.charged == 0 and result.cost == 19
    assert store.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,)) is None
    assert admission.snapshot()["unknown_count"] == 0
    send(admission, second, amount=1)
    assert store.one("SELECT reserved_units FROM model_cost_admissions WHERE attempt_key=?", (first.attempt_key,))["reserved_units"] == 20


def test_expiry_releases_only_inflight_slot_at_exact_second(funded):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(max_inflight=1))
    _, first = prepare(funded, "slot-1")
    _, second = prepare(funded, "slot-2")
    send(admission, first, amount=3, expires_at=START+10)
    with pytest.raises(BillingError, match="cost_inflight_limit"):
        send(admission, second, amount=3)
    funded[3][0] += 10
    send(admission, second, amount=3)
    snapshot = admission.snapshot()
    assert (snapshot["inflight"], snapshot["unknown_count"], snapshot["unknown_reserved_units"]) == (1, 2, 6)


def test_trusted_usage_replaces_reservation_and_releases_both_slots(funded):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(unknown_budget=3, max_inflight=1, max_unknown=1))
    _, first = prepare(funded, "known")
    send(admission, first, amount=3)
    result = funded[1].settle_trusted_usage(first.attempt_key, evidence(first, usage=Usage(0, 1, 0)))
    assert result.cost == 1
    _, second = prepare(funded, "after-known")
    send(admission, second, amount=3)
    snapshot = admission.snapshot()
    assert exact(snapshot, "daily") == 4
    assert (snapshot["unknown_reserved_units"], snapshot["unknown_count"], snapshot["inflight"]) == (3, 1, 1)


@pytest.mark.parametrize("outcome", ["completed", "platform_fault"])
@pytest.mark.parametrize("late", [False, True])
def test_actual_cost_over_bound_is_never_capped_even_for_fault_or_late_usage(funded, outcome, late):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(daily_budget=10))
    _, first = prepare(funded, "over-bound")
    send(admission, first, amount=2)
    assert funded[0].one("SELECT attempt_key FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,))
    if late:
        funded[3][0] += 500
    result = funded[1].settle_trusted_usage(first.attempt_key, evidence(first), outcome=outcome)
    assert result.cost == 19 and result.late == late
    assert result.charged == (1 if outcome == "completed" and not late else 0)
    assert funded[0].one("SELECT attempt_key FROM model_cost_pending WHERE attempt_key=?", (first.attempt_key,)) is None
    snapshot = admission.snapshot()
    assert exact(snapshot, "daily") == 19
    assert snapshot["unknown_count"] == snapshot["inflight"] == 0
    _, second = prepare(funded, "after-over-bound")
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, second, amount=0)


def test_fractional_cost_above_large_integer_budget_cannot_round_down(funded):
    limit = 2**53
    price = cost_price(price_id="fixture-rational", input_cached=Rate(3*limit+1, 3))
    funded[1].publish_price(price)
    admission = CostAdmission(funded[1], current_policy=lambda: policy(
        daily_budget=limit, weekly_budget=2**63-1, monthly_budget=2**63-1, unknown_budget=2**63-1))
    _, first = prepare(funded, "rational", cost_id=price.price_id)
    send(admission, first, amount=limit)
    funded[1].settle_trusted_usage(first.attempt_key, evidence(first, usage=Usage(0, 1, 0)))
    assert exact(admission.snapshot(), "daily") == Fraction(27_021_597_764_222_977, 3)
    _, second = prepare(funded, "fraction-overflow")
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, second, amount=0)


def test_multiple_usage_fractions_and_unknown_reservations_sum_exactly(funded):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(daily_budget=3))
    for i, denominator in enumerate((3, 7)):
        price = cost_price(price_id=f"fixture-fraction-{i}", input_cached=Rate(1, denominator))
        funded[1].publish_price(price)
        _, ticket = prepare(funded, f"fraction-{i}", cost_id=price.price_id)
        send(admission, ticket, amount=1)
        funded[1].settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=Usage(0, 1, 0)))
    _, pending = prepare(funded, "mixed-pending")
    send(admission, pending, amount=2)
    assert exact(admission.snapshot(), "daily") == Fraction(52, 21)  # 1/3 + 1/7 + 2.
    _, refused = prepare(funded, "mixed-overflow")
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, refused, amount=1)


def test_old_known_usage_drops_out_but_not_until_every_utc_window_ends(funded):
    admission = CostAdmission(funded[1], current_policy=policy)
    _, ticket = prepare(funded, "old-known")
    send(admission, ticket, amount=20)
    funded[1].settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    funded[3][0] = utc("2034-01-01T00:00:00")
    snapshot = admission.snapshot()
    assert snapshot["records"] == snapshot["unknown_count"] == 0
    assert [exact(snapshot, p) for p in ("daily", "weekly", "monthly")] == [0, 0, 0]


def test_duplicate_admission_and_dispatch_never_grant_second_send(funded):
    admission = CostAdmission(funded[1], current_policy=policy)
    _, ticket = prepare(funded, "duplicate")

    def twice(t, a):
        admission.admit(t, a, amount=1, expires_at=START+120)
        admission.admit(t, a, amount=1, expires_at=START+120)

    with pytest.raises(BillingError, match="cost_attempt_already_admitted"):
        funded[1].dispatch(ticket, admission=twice)
    assert funded[0].all("SELECT * FROM model_cost_admissions") == []
    assert funded[0].all("SELECT * FROM model_cost_pending") == []
    send(admission, ticket, amount=1)
    with pytest.raises(BillingError, match="dispatch_fenced"):
        send(admission, ticket, amount=1)
    assert len(funded[0].all("SELECT * FROM model_cost_admissions")) == 1


def test_failure_after_admission_rolls_back_reservation_and_dispatch(funded):
    store, ledger, _, _ = funded
    admission = CostAdmission(ledger, current_policy=policy)
    _, ticket = prepare(funded, "rollback")
    # Fixture-only fault at Ledger's event write, AFTER admission and attempt UPDATE.
    store.execute("CREATE TEMP TRIGGER fixture_dispatch_failure BEFORE INSERT ON billing_events "
                  "WHEN NEW.kind='dispatch_unknown' BEGIN SELECT RAISE(ABORT,'fixture_dispatch_failed'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture_dispatch_failed"):
            send(admission, ticket, amount=7)
    finally:
        store.execute("DROP TRIGGER fixture_dispatch_failure")
    assert store.all("SELECT * FROM model_cost_admissions") == []
    assert store.all("SELECT * FROM model_cost_pending") == []
    row = store.one("SELECT status,dispatched_at FROM billing_attempts WHERE id=?", (ticket.attempt_key,))
    assert row == {"status": "prepared", "dispatched_at": None}
    send(admission, ticket, amount=7)
    assert len(store.all("SELECT * FROM model_cost_admissions")) == 1


def test_policy_refusal_rolls_back_earlier_writes_in_the_dispatch_callback(funded):
    store, ledger, _, _ = funded
    _, ticket = prepare(funded, "refusal-rollback")
    admission = CostAdmission(ledger, current_policy=lambda: policy(daily_budget=0))

    def callback(t, a):
        t.execute("INSERT INTO billing_events(call_key,attempt_key,kind,reason,created_at) "
                  "SELECT call_key,id,'fixture_before_admission','fixture_rollback',? FROM billing_attempts WHERE id=?",
                  (START, a.attempt_key))
        admission.admit(t, a, amount=1, expires_at=START+120)

    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        ledger.dispatch(ticket, admission=callback)
    assert store.all("SELECT * FROM billing_events WHERE kind='fixture_before_admission'") == []
    assert store.all("SELECT * FROM model_cost_admissions") == []
    assert store.one("SELECT status FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["status"] == "prepared"


def test_admission_fact_is_immutable_including_replace(funded):
    store = funded[0]
    admission = CostAdmission(funded[1], current_policy=policy)
    _, ticket = prepare(funded, "immutable")
    send(admission, ticket, amount=7)
    original = store.one("SELECT * FROM model_cost_admissions")
    for sql in ("UPDATE model_cost_admissions SET reserved_units=0",
                "DELETE FROM model_cost_admissions",
                "INSERT OR REPLACE INTO model_cost_admissions SELECT * FROM model_cost_admissions"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable_cost_admission"):
            store.execute(sql)
    assert store.one("SELECT * FROM model_cost_admissions") == original


def seed_capacity_facts(money, *, count, admitted_at):
    """Fixture-only bulk history, cloned from a real funded Ledger dispatch.

    Each clone has a distinct call/attempt and valid foreign keys. Bulk SQL keeps this
    10,000-row boundary test focused on scanning, without 40,000 redundant fsync commits.
    """
    store, ledger, _, ticks = money
    current = ticks[0]
    ticks[0] = admitted_at
    call, ticket = prepare(money, "capacity-template", hold=0)
    admission = CostAdmission(ledger, current_policy=lambda: policy(max_unknown=10_000, max_inflight=10_000))
    send(admission, ticket, amount=0)
    ticks[0] = current
    with store.tx() as t:
        t.executemany(
            "INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,status,created_at) "
            "SELECT ?,run_key,?,context,account_id,price_id,0,deadline,status,created_at FROM billing_calls WHERE id=?",
            [(f"fixture-capacity-call-{i}", f"fixture-capacity-{i}", call) for i in range(count-1)])
        t.executemany(
            "INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
            "requested_model,cost_price_id,status,created_at,dispatched_at) "
            "SELECT ?,?,?,owner,?,lease_until,environment,provider,requested_model,cost_price_id,status,created_at,dispatched_at "
            "FROM billing_attempts WHERE id=?",
            [(f"fixture-capacity-attempt-{i}", f"fixture-capacity-call-{i}", f"fixture-capacity-{i}",
              f"fixture-capacity-fence-{i}", ticket.attempt_key) for i in range(count-1)])
        t.executemany(
            "INSERT INTO model_cost_admissions SELECT ?,environment,provider,account_ref,unit,reserved_units,"
            "policy_revision,limits_json,admitted_at,expires_at FROM model_cost_admissions WHERE attempt_key=?",
            [(f"fixture-capacity-attempt-{i}", ticket.attempt_key) for i in range(count-1)])
    return ticket


@pytest.mark.parametrize("old_unknown", [False, True])
def test_accounting_capacity_counts_current_windows_and_all_old_unknown(funded, old_unknown):
    admission = CostAdmission(funded[1], current_policy=lambda: policy(max_unknown=10_000, max_inflight=10_000))
    now = utc("2034-01-01T00:00:00")
    funded[3][0] = now
    seed_capacity_facts(funded, count=9_999, admitted_at=START if old_unknown else now)
    _, ticket = prepare(funded, "at-capacity")
    send(admission, ticket, amount=0)
    snapshot = admission.snapshot()
    assert snapshot["records"] == snapshot["unknown_count"] == 10_000
    _, refused = prepare(funded, "over-capacity")
    with pytest.raises(BillingError, match="cost_accounting_capacity"):
        send(admission, refused, amount=0)
    assert len(funded[0].all("SELECT attempt_key FROM model_cost_admissions")) == 10_000
    assert funded[0].all("PRAGMA foreign_key_check") == []


def test_over_capacity_snapshot_refuses_partial_totals(funded):
    seed_capacity_facts(funded, count=10_000, admitted_at=START)
    # A separate zero-cost historical fact models a deployment already beyond the cap.
    _, ticket = prepare(funded, "legacy-extra", hold=0)
    funded[1].dispatch(ticket)
    with funded[0].tx() as t:
        t.execute("INSERT INTO model_cost_admissions SELECT ?,environment,provider,account_ref,unit,reserved_units,"
                  "policy_revision,limits_json,admitted_at,expires_at FROM model_cost_admissions LIMIT 1", (ticket.attempt_key,))
    admission = CostAdmission(funded[1], current_policy=policy)
    with pytest.raises(BillingError, match="cost_accounting_capacity"):
        admission.snapshot()


def test_pending_creation_commits_with_dispatch_and_is_not_visible_before_commit(funded):
    store, ledger, _, _ = funded
    observer = Store(store.root)
    admission = CostAdmission(ledger, current_policy=policy)
    _, ticket = prepare(funded, "pending-transaction")

    def callback(t, a):
        admission.admit(t, a, amount=7, expires_at=START+120)
        assert t.execute("SELECT 1 FROM model_cost_pending WHERE attempt_key=?", (a.attempt_key,)).fetchone()
        assert t.execute("SELECT status FROM billing_attempts WHERE id=?", (a.attempt_key,)).fetchone()[0] == "prepared"
        assert observer.one("SELECT * FROM model_cost_pending WHERE attempt_key=?", (a.attempt_key,)) is None
        assert observer.one("SELECT * FROM model_cost_admissions WHERE attempt_key=?", (a.attempt_key,)) is None

    ledger.dispatch(ticket, admission=callback)
    assert observer.one("SELECT * FROM model_cost_pending") == {
        "attempt_key": ticket.attempt_key, "environment": "test", "provider": "fixture-provider",
        "account_ref": "fixture-provider-account", "admitted_at": START,
    }
    assert observer.one("SELECT status FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["status"] == "dispatch_unknown"


@pytest.mark.parametrize("outcome", ["completed", "platform_fault"])
@pytest.mark.parametrize("late", [False, True])
def test_usage_failure_restores_pending_and_retry_removes_it_atomically(funded, outcome, late):
    store, ledger, _, ticks = funded
    admission = CostAdmission(ledger, current_policy=policy)
    call, ticket = prepare(funded, "pending-usage-rollback")
    send(admission, ticket, amount=20)
    pending = store.one("SELECT * FROM model_cost_pending")
    if late:
        ticks[0] += 500
        ledger.expire_due()
    before_balance = balance(funded)
    before_call = ledger.call_status(call)
    before_events = store.all("SELECT * FROM billing_events ORDER BY seq")
    before_journal = store.all("SELECT * FROM billing_journal ORDER BY rowid")
    # Fixture-only failure after usage INSERT has already removed pending. If removal
    # does not happen in this transaction, this fault never fires and the test fails.
    store.execute(
        "CREATE TEMP TRIGGER fixture_pending_settlement_failure BEFORE INSERT ON billing_events "
        "WHEN NEW.kind IN ('settled','cost_only') "
        "AND EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=NEW.attempt_key) "
        "AND NOT EXISTS(SELECT 1 FROM model_cost_pending WHERE attempt_key=NEW.attempt_key) "
        "BEGIN SELECT RAISE(ABORT,'fixture_pending_settlement_failed'); END")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture_pending_settlement_failed"):
            ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket), outcome=outcome)
    finally:
        store.execute("DROP TRIGGER fixture_pending_settlement_failure")
    assert store.all("SELECT * FROM billing_usage") == []
    assert store.one("SELECT * FROM model_cost_pending") == pending
    assert store.one("SELECT status FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["status"] == "dispatch_unknown"
    assert balance(funded) == before_balance and ledger.call_status(call) == before_call
    assert store.all("SELECT * FROM billing_events ORDER BY seq") == before_events
    assert store.all("SELECT * FROM billing_journal ORDER BY rowid") == before_journal
    result = ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket), outcome=outcome)
    assert result.cost == 19 and result.late == late
    assert store.all("SELECT * FROM model_cost_pending") == []
    assert admission.snapshot()["unknown_count"] == 0
    assert exact(admission.snapshot(), "daily") == 19


def test_pending_unknown_cannot_be_deleted_updated_or_replaced(funded):
    store = funded[0]
    _, ticket = prepare(funded, "pending-integrity")
    send(CostAdmission(funded[1], current_policy=policy), ticket, amount=7)
    original = store.one("SELECT * FROM model_cost_pending")
    for statement in (
        "DELETE FROM model_cost_pending",
        "UPDATE model_cost_pending SET account_ref='fixture-forged-account'",
        "INSERT OR REPLACE INTO model_cost_pending "
        "SELECT attempt_key,environment,provider,'fixture-forged-account',admitted_at FROM model_cost_pending",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute(statement)
        assert store.one("SELECT * FROM model_cost_pending") == original


@pytest.mark.parametrize("rebuild_projection", [False, True])
def test_reopening_store_preserves_or_rebuilds_only_unknown_pending(funded, rebuild_projection):
    store, ledger, _, ticks = funded
    admission = CostAdmission(ledger, current_policy=policy)
    _, unknown = prepare(funded, "persist-unknown")
    _, known = prepare(funded, "persist-known")
    send(admission, unknown, amount=20)
    send(admission, known, amount=20)
    ledger.settle_trusted_usage(known.attempt_key, evidence(known))
    ticks[0] = utc("2034-01-01T00:00:00")
    ledger.expire_due()
    facts = store.all("SELECT * FROM model_cost_admissions ORDER BY attempt_key")
    usage = store.all("SELECT * FROM billing_usage ORDER BY attempt_key")
    pending = [{"attempt_key": unknown.attempt_key, "environment": "test", "provider": "fixture-provider",
                "account_ref": "fixture-provider-account", "admitted_at": START}]
    assert store.all("SELECT * FROM model_cost_pending") == pending
    if rebuild_projection:
        # Isolated fixture only: simulate the database before this derived projection
        # and its one-time migration marker existed. Immutable facts stay untouched.
        with store.tx() as t:
            t.execute("DROP TABLE model_cost_pending")
            t.execute("DELETE FROM meta WHERE key='model_cost_pending_projection_v1'")
    statements = []

    class ObservedStore(Store):
        """Real Store migration/connection, with SQL observation only."""
        def _connect(self):
            connection = super()._connect()
            connection.set_trace_callback(statements.append)
            return connection

    for reopening in range(2):
        statements.clear()
        reopened = ObservedStore(store.root)
        with reopened.conn() as c:
            c.set_trace_callback(None)
        history_queries = [sql for sql in statements
                           if sql.lstrip().upper().startswith(("SELECT", "WITH", "INSERT"))
                           and "model_cost_admissions" in sql.lower()]
        assert bool(history_queries) == (rebuild_projection and reopening == 0)
        assert reopened.all("SELECT * FROM model_cost_pending") == pending
        assert reopened.all("SELECT * FROM model_cost_admissions ORDER BY attempt_key") == facts
        assert reopened.all("SELECT * FROM billing_usage ORDER BY attempt_key") == usage
        assert reopened.one("SELECT value FROM meta WHERE key='model_cost_pending_projection_v1'") == {"value": "1"}
        assert reopened.all("PRAGMA foreign_key_check") == []
        reopened_ledger = Ledger(reopened, environment="test", namespace=ledger.namespace, clock=lambda: ticks[0])
        snapshot = CostAdmission(reopened_ledger, current_policy=policy).snapshot()
        assert snapshot["records"] == snapshot["unknown_count"] == 1
        assert snapshot["unknown_reserved_units"] == 20 and snapshot["inflight"] == 0


def test_admission_insert_with_existing_usage_never_creates_pending(funded):
    store, ledger, _, _ = funded
    _, ticket = prepare(funded, "already-metered")
    ledger.dispatch(ticket)
    ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    # Fixture-only legacy admission backfill AFTER the authentic Ledger usage fact.
    with store.tx() as t:
        t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
                  "reserved_units,policy_revision,limits_json,admitted_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ticket.attempt_key, "test", "fixture-provider", "fixture-provider-account", COST_UNIT.code,
                   20, "fixture-policy-v1", json.dumps(asdict(policy())), START, START+120))
    assert store.all("SELECT * FROM model_cost_pending") == []
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("INSERT INTO model_cost_pending SELECT attempt_key,environment,provider,account_ref,admitted_at "
                      "FROM model_cost_admissions WHERE attempt_key=?", (ticket.attempt_key,))
    assert exact(CostAdmission(ledger, current_policy=policy).snapshot(), "daily") == 19
    reopened = Store(store.root)
    assert reopened.all("SELECT * FROM model_cost_pending") == []


def test_historical_pending_query_uses_scope_index_without_scanning_settled_history(funded):
    store, ledger, _, ticks = funded
    template = seed_capacity_facts(funded, count=10_000, admitted_at=START)
    ledger.settle_trusted_usage(template.attempt_key, evidence(template, usage=Usage(0, 0, 0)))
    # Fixture-only bulk settled history: copy a real zero-cost usage fact to the
    # distinct capacity attempts. Real schema triggers maintain every pending row.
    with store.tx() as t:
        t.execute("UPDATE billing_attempts SET status='settled' WHERE id LIKE 'fixture-capacity-attempt-%'")
        t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,"
                  "absorbed_n,absorbed_d,late,created_at) "
                  "SELECT a.id,u.facts,u.outcome,u.cost_n,u.cost_d,u.cost_unit,u.charge,"
                  "u.absorbed_n,u.absorbed_d,u.late,u.created_at "
                  "FROM billing_attempts a JOIN billing_usage u ON u.attempt_key=? "
                  "WHERE a.id LIKE 'fixture-capacity-attempt-%'", (template.attempt_key,))
    assert store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 10_000
    assert store.all("SELECT * FROM model_cost_pending") == []
    admission = CostAdmission(ledger, current_policy=policy)
    ticks[0] = utc("2033-07-01T00:00:00")
    _, old_unknown = prepare(funded, "old-indexed-unknown")
    send(admission, old_unknown, amount=7)
    ticks[0] = utc("2034-01-01T00:00:00")
    _, current = prepare(funded, "current-indexed-unknown")
    send(admission, current, amount=3)
    statements = []
    with store.conn() as c:
        c.set_trace_callback(statements.append)
        try:
            snapshot = admission.snapshot()
        finally:
            c.set_trace_callback(None)
        # Inspect the SQL actually used by the service, not a parallel test query.
        cost_queries = {sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))
                        and "model_cost_admissions" in sql.lower()}
        assert cost_queries
        for sql in cost_queries:
            assert "UNION ALL" in sql.upper() and "model_cost_pending" in sql.lower()
            details = [row[3].lower() for row in c.execute("EXPLAIN QUERY PLAN " + sql)]
            assert any("search " in detail and "model_cost_pending_scope" in detail
                       and all(term in detail for term in ("environment=?", "provider=?", "account_ref=?", "admitted_at<?"))
                       for detail in details), details
            assert any("search " in detail and "model_cost_admissions_scope" in detail
                       and "admitted_at>?" in detail for detail in details), details
            sources = {"model_cost_admissions", "model_cost_pending", "billing_usage"}
            for table, alias in re.findall(
                r"\b(?:FROM|JOIN)\s+(model_cost_admissions|model_cost_pending|billing_usage)"
                r"(?:\s+(?:AS\s+)?([a-z_]\w*))?", sql, re.IGNORECASE,
            ):
                sources.add(table.lower())
                if alias.lower() not in ("", "where", "join", "left", "inner", "on", "indexed", "union"):
                    sources.add(alias.lower())
            assert not any(re.match(r"scan\s+(?:" + "|".join(map(re.escape, sources)) + r")\b", detail)
                           for detail in details), details
    assert snapshot["records"] == snapshot["unknown_count"] == 2
    assert snapshot["unknown_reserved_units"] == 10 and snapshot["inflight"] == 1
    assert exact(snapshot, "daily") == exact(snapshot, "weekly") == exact(snapshot, "monthly") == 3
