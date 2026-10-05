"""Isolated money fixtures: no real prices, model calls, payments or network.

Store installs the additive schema. Independent Store instances/processes exercise SQLite
locking and crash recovery, not an in-memory fake.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from synwevo_core.billing import (BillingError, ChargeContext, Ledger, PaymentReceipt, PriceSnapshot,
                                 Rate, Unit, Usage, UsageEvidence, normalize_usage)
from synwevo_core.identity import Identity
from synwevo_core.store import Store

UNIT = Unit("FIXTURE-CREDIT", 6)
COST_UNIT = Unit("FIXTURE-COST", 8)
START = 2_000_000_000


def fixture_authorize(t, context, action):
    """Test-only current authorization table; not a production authorization implementation."""
    return t.execute("SELECT 1 FROM fixture_billing_authority WHERE payer=? AND actor=? AND authority=?",
                     (context.payer_user_id, context.actor_user_id, context.authorization_ref)).fetchone() is not None


def fixture_verify(t, receipt):
    """Test-only durable authenticated inbox facts, never a production 'accept paid' hook."""
    row = t.execute("SELECT receipt FROM fixture_payment_inbox WHERE event_id=?", (receipt.event_id,)).fetchone()
    return row is not None and json.loads(row[0]) == asdict(receipt)


def sale_price(**changes):
    return replace(PriceSnapshot("fixture-sale-v1", "sale", UNIT, "fixture-provider", "fixture-model",
                                 Rate(1), Rate(1), Rate(2), "fixture-terms-v1", START, per_tokens=1), **changes)


def cost_price(**changes):
    return replace(sale_price(price_id="fixture-cost-v1", purpose="cost", unit=COST_UNIT,
                              input_uncached=Rate(3), input_cached=Rate(1), output=Rate(4)), **changes)


@pytest.fixture
def money(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-money-fixture")
    with store.conn() as c:
        c.executescript("""
            CREATE TABLE fixture_payment_inbox(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);
            CREATE TABLE fixture_billing_authority(payer TEXT,actor TEXT,authority TEXT);
        """)
    identity = Identity(store)
    users = [identity.create_user(f"billing-fixture-{i}@example.test", None, status="active",
                                  with_personal_workspace=False)["id"] for i in range(3)]
    with store.tx() as t:
        t.executemany("INSERT INTO fixture_billing_authority VALUES (?,?,?)", [(u, u, "fixture-approval") for u in users])
    ticks = [START]
    ledger = Ledger(store, environment="test", namespace="fixture-agent", authorize=fixture_authorize,
                    verify_payment=fixture_verify, clock=lambda: ticks[0], max_hold_seconds=1000)
    ledger.publish_price(sale_price())
    ledger.publish_price(cost_price())
    return store, ledger, users, ticks


def context(money, *, run="run-1", user=0, workspace="workspace-A"):
    uid = money[2][user]
    return ChargeContext(uid, uid, run, "fixture-approval", workspace)


def fund(money, amount=100, *, order="order-1", user=0, ledger=None):
    store, default, users, _ = money
    ledger = ledger or default
    ledger.create_payment_order(order_id=order, payer_user_id=users[user], provider="fixture-payments",
                                 merchant_id="fixture-merchant", amount_minor=100, currency="XTS", unit=UNIT,
                                 credit_units=amount, exchange_version="fixture-exchange-v1")
    receipt = PaymentReceipt(ledger.environment, "fixture-payments", "fixture-merchant", "payment-" + order,
                             "event-" + order, order, 100, "XTS", "fixture-inbox-" + order, "a" * 64)
    trust(money, receipt)
    ledger.credit_verified_payment(receipt)
    return receipt


def trust(money, receipt):
    with money[0].tx() as t:
        t.execute("INSERT INTO fixture_payment_inbox VALUES (?,?)", (receipt.event_id, json.dumps(asdict(receipt))))


def balance(money, user=0, ledger=None):
    uid = money[2][user]
    return (ledger or money[1]).balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=UNIT)


def reserve(money, *, amount=20, call="call-1", ctx=None, price="fixture-sale-v1", budget=100, max_calls=50, max_attempts=100):
    ledger = money[1]
    ctx = ctx or context(money)
    ledger.open_run(ctx, unit=UNIT, budget=budget, max_calls=max_calls, max_attempts=max_attempts, deadline=START+900)
    return ledger.reserve_call(ctx, logical_call_id=call, amount=amount, price_id=price, deadline=START+500)


def dispatch(money, call, *, attempt="attempt-1", retry=None):
    ledger = money[1]
    ticket = ledger.prepare_attempt(call, attempt_id=attempt, owner="fixture-worker", cost_price_id="fixture-cost-v1",
                                    requested_model="fixture-request-alias", retry_reason=retry)
    ledger.dispatch(ticket)
    return ticket


def evidence(ticket, *, usage=None):
    return UsageEvidence("fixture-provider", "request-" + ticket.attempt_key, "fixture-final-" + ticket.attempt_key,
                          usage or Usage(2, 1, 3, 1), "fixture-model", "fixture-capability-v1", START)


def settle(money, ticket, **kwargs):
    return money[1].settle_trusted_usage(ticket.attempt_key, evidence(ticket, **kwargs))


def assert_balanced(store):
    assert store.all("SELECT journal_id,SUM(delta) AS total,COUNT(*) AS legs FROM billing_entries GROUP BY journal_id HAVING total<>0 OR legs<>2") == []
    assert store.all("SELECT environment,unit,SUM(delta) AS total FROM billing_entries GROUP BY environment,unit HAVING total<>0") == []
    assert store.all("PRAGMA foreign_key_check") == []


def test_store_installs_schema_idempotently_without_funding_or_prices(tmp_path):
    store = Store(tmp_path / "isolated-migration")
    assert store.one("SELECT name FROM sqlite_master WHERE name='billing_journal'")
    Ledger(store, environment="test", namespace="fixture")
    reopened = Store(store.root)
    for table in ("billing_units", "billing_prices", "billing_accounts", "billing_journal", "billing_runs"):
        assert reopened.all(f"SELECT * FROM {table}") == []
    assert reopened.all("PRAGMA foreign_key_check") == []


@pytest.mark.parametrize("bad", [-1, 1.0, True, "1", 2**63, None])
def test_money_rejects_non_integer_negative_and_overflow(money, bad):
    with pytest.raises(BillingError, match="invalid_integer"):
        reserve(money, amount=bad)
    assert balance(money).booked == 0


def test_usage_alias_and_reasoning_are_not_double_counted():
    usage = normalize_usage({"prompt_tokens": 100, "prompt_cache_hit_tokens": 30, "prompt_cache_miss_tokens": 70,
                             "prompt_tokens_details": {"cached_tokens": 30}, "completion_tokens": 40,
                             "completion_tokens_details": {"reasoning_tokens": 15}, "total_tokens": 140})
    assert usage == Usage(70, 30, 40, 15)
    assert sale_price().exact(usage) == 180  # 70+30+40*2, not 100+70+30+30+40+15.
    assert normalize_usage({"prompt_tokens": 10, "completion_tokens": 2,
                            "prompt_tokens_details": {"cached_tokens": 3}}) == Usage(7, 3, 2)


@pytest.mark.parametrize("override", [
    {"prompt_cache_hit_tokens": 8}, {"prompt_cache_miss_tokens": 8}, {"total_tokens": 123},
    {"prompt_tokens_details": {"cached_tokens": 8}}, {"completion_tokens": 1.5},
    {"prompt_tokens": True}, {"completion_tokens_details": {"reasoning_tokens": 3}},
    {"prompt_cache_hit_tokens": -1}, {"prompt_tokens_details": None},
])
def test_invalid_usage_is_refused(override):
    raw = {"prompt_tokens": 10, "completion_tokens": 2, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 7,
           "prompt_tokens_details": {"cached_tokens": 3}}
    with pytest.raises(BillingError):
        normalize_usage(raw | override)


def test_missing_cache_classification_is_unknown():
    with pytest.raises(BillingError, match="missing_cache_classification"):
        normalize_usage({"prompt_tokens": 10, "completion_tokens": 2})


def test_fixed_prices_integer_fractional_rounding_and_immutable_versions(money):
    store, ledger, _, _ = money
    fund(money, 10)
    price = sale_price(price_id="fixture-micro-v1", input_uncached=Rate(1, 3), per_tokens=1)
    ledger.publish_price(price)
    assert ledger.publish_price(price) == price.price_id
    with pytest.raises(BillingError, match="price_version_conflict"):
        ledger.publish_price(replace(price, input_uncached=Rate(1, 2)))
    with pytest.raises(BillingError, match="unit_scale_conflict"):
        ledger.publish_price(replace(price, price_id="new", unit=Unit(UNIT.code, 2)))
    charges = []
    for i in range(6):
        call = reserve(money, amount=1, call=f"micro-{i}", price=price.price_id, budget=10)
        ticket = dispatch(money, call)
        charges.append(settle(money, ticket, usage=Usage(1, 0, 0)).charged)
    assert charges == [0, 0, 1, 0, 0, 1]
    assert balance(money).booked == 8
    assert ledger.audit_balances() == []
    assert_balanced(store)


def test_exact_million_token_formula_uses_published_scale():
    price = sale_price(input_uncached=Rate(125), input_cached=Rate(25), output=Rate(300), per_tokens=1_000_000)
    # Expected exact smallest units: (70*125+30*25+40*300)/1,000,000 = 43/2000.
    assert price.exact(Usage(70, 30, 40, 15)) == Fraction(43, 2000)
    assert price.unit.scale == 6


def test_credit_hold_settle_and_user_projection(money):
    store, ledger, users, _ = money
    fund(money)
    call = reserve(money)
    assert (balance(money).booked, balance(money).held, balance(money).available) == (100, 20, 80)
    ticket = dispatch(money, call)
    with store.conn() as connection:
        assert not connection.in_transaction
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    result = settle(money, ticket)
    assert (result.charged, result.cost, result.absorbed) == (9, 19, 0)
    assert result.cost_unit == COST_UNIT
    assert (balance(money).booked, balance(money).held, balance(money).available) == (91, 0, 91)
    assert settle(money, ticket) == result
    assert ledger.call_status(call)["status"] == "settled"
    with pytest.raises(BillingError, match="usage_conflict"):
        settle(money, ticket, usage=Usage(3, 0, 0))
    assert len(store.all("SELECT * FROM billing_settlements")) == 1
    statement = ledger.statement_for_user(requester_user_id=users[0], payer_user_id=users[0])
    assert [r["delta"] for r in statement] == [100, -9]
    assert set(statement[0]) == {"seq", "id", "kind", "unit", "scale", "created_at", "delta"}
    assert len(ledger.statement_for_user(requester_user_id=users[0], payer_user_id=users[0], after_seq=statement[0]["seq"])) == 1
    for reader in users[1:]:
        with pytest.raises(BillingError, match="wallet_forbidden"):
            ledger.balance_for_user(requester_user_id=reader, payer_user_id=users[0], unit=UNIT)
        with pytest.raises(BillingError, match="wallet_forbidden"):
            ledger.statement_for_user(requester_user_id=reader, payer_user_id=users[0])
    assert_balanced(store)


def test_environment_unit_namespace_and_business_voucher_isolation(money):
    store, ledger, users, ticks = money
    receipt = fund(money)
    live = Ledger(store, environment="live", namespace="fixture-agent", authorize=fixture_authorize,
                  verify_payment=fixture_verify, clock=lambda: ticks[0])
    assert balance(money, ledger=live).booked == 0
    with pytest.raises(BillingError, match="environment_mismatch"):
        live.credit_verified_payment(receipt)
    assert ledger.balance_for_user(requester_user_id=users[0], payer_user_id=users[0], unit=COST_UNIT).booked == 0
    other = Ledger(store, environment="test", namespace="fixture-other", authorize=fixture_authorize,
                   verify_payment=fixture_verify, clock=lambda: ticks[0])
    assert balance(money, ledger=other).booked == 100  # Same wallet; namespace cannot mint another balance.
    c1 = reserve(money, amount=80)
    ctx = context(money)
    other.open_run(ctx, unit=UNIT, budget=100, max_calls=10, max_attempts=10, deadline=START+900)
    with pytest.raises(BillingError, match="insufficient_funds"):
        other.reserve_call(ctx, logical_call_id="call-1", amount=21, price_id="fixture-sale-v1", deadline=START+500)
    c2 = other.reserve_call(ctx, logical_call_id="call-1", amount=20, price_id="fixture-sale-v1", deadline=START+500)
    assert c1 != c2 and balance(money).held == 100
    with pytest.raises(BillingError, match="call_not_found"):
        other.cancel_call(c1)
    assert_balanced(store)


def test_payer_is_explicit_and_fixed_but_workspaces_are_attribution(money):
    fund(money)
    ctx = context(money)
    call_a = reserve(money, ctx=ctx, amount=30)
    call_b = money[1].reserve_call(replace(ctx, workspace_id="workspace-B"), logical_call_id="call-B", amount=30,
                                  price_id="fixture-sale-v1", deadline=START+500)
    assert balance(money).held == 60
    assert {json.loads(r["context"])["workspace_id"] for r in money[0].all("SELECT context FROM billing_calls")} == {"workspace-A", "workspace-B"}
    assert call_a != call_b
    # An organization payer names its organization and stays the acting member; without the
    # organization policy installed (org_payers) the ledger refuses it at reservation.
    with pytest.raises(BillingError, match="invalid_payer"):
        replace(ctx, payer_kind="organization")
    with pytest.raises(BillingError, match="organization_billing_not_configured"):
        reserve(money, ctx=replace(ctx, run_id="run-org", payer_kind="organization", organization_id="org-fixture"),
                call="org-call", amount=0)
    with pytest.raises(BillingError, match="payment_delegation_required"):
        replace(ctx, payer_user_id=money[2][1])
    with pytest.raises(BillingError, match="run_context_conflict"):
        money[1].reserve_call(context(money, user=1), logical_call_id="switch-payer", amount=0,
                             price_id="fixture-sale-v1", deadline=START+500)
    with pytest.raises(BillingError, match="idempotency_conflict"):
        money[1].reserve_call(replace(ctx, workspace_id="workspace-B"), logical_call_id="call-1", amount=30,
                             price_id="fixture-sale-v1", deadline=START+500)


def test_no_authorization_fallback_and_recheck_on_dispatch(money):
    store, ledger, users, _ = money
    fund(money)
    closed = Ledger(store, environment="test", namespace="fixture-agent", clock=lambda: START)
    with pytest.raises(BillingError, match="payment_authority_denied"):
        closed.open_run(context(money), unit=UNIT, budget=1, max_calls=1, max_attempts=1, deadline=START+500)
    call = reserve(money)
    ticket = ledger.prepare_attempt(call, attempt_id="first", owner="worker", requested_model="alias", cost_price_id="fixture-cost-v1")
    store.execute("DELETE FROM fixture_billing_authority WHERE payer=?", (users[0],))
    with pytest.raises(BillingError, match="payment_authority_denied"):
        ledger.dispatch(ticket)
    assert store.one("SELECT status FROM billing_attempts WHERE id=?", (ticket.attempt_key,))["status"] == "prepared"
    assert ledger.cancel_call(call) == "released"


def test_account_suspension_blocks_new_dispatch_but_not_fact_settlement(money):
    fund(money)
    ticket = dispatch(money, reserve(money))
    Identity(money[0]).set_status(money[2][0], "suspended")
    assert settle(money, ticket).charged == 9
    with pytest.raises(BillingError, match="account_unavailable"):
        reserve(money, ctx=context(money, run="new-run"))


def test_run_parent_budget_never_double_freezes_and_limits_total(money):
    fund(money, 100)
    c1 = reserve(money, amount=30, budget=40, max_calls=3, max_attempts=3)
    assert balance(money).held == 30
    ctx = context(money)
    with pytest.raises(BillingError, match="run_budget_exceeded"):
        money[1].reserve_call(ctx, logical_call_id="too-much", amount=11, price_id="fixture-sale-v1", deadline=START+500)
    settle(money, dispatch(money, c1))
    c2 = money[1].reserve_call(ctx, logical_call_id="next", amount=31, price_id="fixture-sale-v1", deadline=START+500)
    assert balance(money).held == 31
    assert balance(money).available == 60
    with pytest.raises(BillingError, match="run_budget_exceeded"):
        money[1].reserve_call(ctx, logical_call_id="excess", amount=1, price_id="fixture-sale-v1", deadline=START+500)
    money[1].cancel_call(c2)


def test_concurrent_independent_connections_compete_for_last_balance(money):
    store, _, _, ticks = money
    fund(money, 1)
    count = 12
    ledgers = [Ledger(Store(store.root), environment="test", namespace="fixture-agent", authorize=fixture_authorize,
                       clock=lambda: ticks[0]) for _ in range(count)]
    for i, ledger in enumerate(ledgers):
        ledger.open_run(context(money, run=f"parallel-{i}"), unit=UNIT, budget=1, max_calls=1, max_attempts=1, deadline=START+900)
    barrier = Barrier(count)

    def compete(i):
        barrier.wait()
        try:
            return ledgers[i].reserve_call(context(money, run=f"parallel-{i}"), logical_call_id="only", amount=1,
                                           price_id="fixture-sale-v1", deadline=START+500)
        except BillingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(compete, range(count)))
    assert results.count("insufficient_funds") == count-1
    assert balance(money).available == 0 and balance(money).held == 1
    assert len(store.all("SELECT * FROM billing_calls")) == 1


def test_concurrent_same_call_reserve_and_settle_once(money):
    store, ledger, _, ticks = money
    fund(money)
    ctx = context(money)
    ledger.open_run(ctx, unit=UNIT, budget=100, max_calls=50, max_attempts=100, deadline=START+900)
    ledgers = [Ledger(Store(store.root), environment="test", namespace="fixture-agent", authorize=fixture_authorize,
                       clock=lambda: ticks[0]) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        calls = list(pool.map(lambda l: l.reserve_call(ctx, logical_call_id="same", amount=20, price_id="fixture-sale-v1", deadline=START+500), ledgers))
    assert len(set(calls)) == 1 and balance(money).held == 20
    ticket = dispatch(money, calls[0])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda l: l.settle_trusted_usage(ticket.attempt_key, evidence(ticket)), ledgers))
    assert len(set(results)) == 1 and balance(money).booked == 91
    assert len(store.all("SELECT * FROM billing_settlements")) == 1
    assert_balanced(store)


def test_cancel_unknown_deadline_and_late_usage_never_secretly_charge(money):
    store, ledger, _, ticks = money
    fund(money)
    call = reserve(money)
    ticket = dispatch(money, call)
    ledger.mark_usage_pending(ticket, request_id=evidence(ticket).request_id, reason="fixture_stream_disconnected")
    assert ledger.cancel_call(call) == "reserved"
    assert balance(money).held == 20
    with pytest.raises(BillingError):
        dispatch(money, call, attempt="retry", retry="unknown_is_not_retry_permission")
    ticks[0] = START + 500
    assert ledger.expire_due() == 1
    assert ledger.expire_due() == 0
    assert balance(money).available == 100
    late = settle(money, ticket)
    assert (late.charged, late.cost, late.absorbed, late.late) == (0, 19, 9, True)
    assert settle(money, ticket) == late
    assert ledger.call_status(call)["status"] == "released"
    assert store.all("SELECT * FROM billing_settlements") == []
    assert balance(money).booked == 100


def test_exact_deadline_settlement_self_expires_without_sweeper(money):
    fund(money)
    ticket = dispatch(money, reserve(money))
    money[3][0] = START+500
    assert settle(money, ticket).late
    assert balance(money).available == 100


def test_unsent_cancel_and_lease_fences(money):
    _, ledger, _, ticks = money
    fund(money)
    call = reserve(money)
    first = ledger.prepare_attempt(call, attempt_id="attempt", owner="first", requested_model="alias", cost_price_id="fixture-cost-v1", lease_seconds=5)
    with pytest.raises(BillingError, match="attempt_not_claimable"):
        ledger.claim_prepared(first.attempt_key, owner="second")
    ticks[0] += 5
    second = ledger.claim_prepared(first.attempt_key, owner="second")
    assert first.fence != second.fence
    with pytest.raises(BillingError, match="dispatch_fenced"):
        ledger.dispatch(first)
    ledger.dispatch(second)
    with pytest.raises(BillingError, match="dispatch_fenced"):
        ledger.dispatch(second)
    ticks[0] += 100
    with pytest.raises(BillingError, match="attempt_not_claimable"):
        ledger.claim_prepared(second.attempt_key, owner="third")
    other = reserve(money, call="unsent")
    assert ledger.cancel_call(other) == "released"
    assert balance(money).held == 20


def test_platform_fault_retry_records_each_cost_without_double_billing(money):
    store, ledger, _, _ = money
    fund(money)
    call = reserve(money)
    first = dispatch(money, call)
    fault = ledger.settle_trusted_usage(first.attempt_key, evidence(first), outcome="platform_fault")
    assert fault.charged == 0 and fault.cost == 19
    assert balance(money).held == 20
    second = dispatch(money, call, attempt="attempt-2", retry="fixture-worker-lost-result")
    result = settle(money, second)
    assert result.charged == 9 and balance(money).booked == 91
    assert len(store.all("SELECT * FROM billing_usage")) == 2
    assert len(store.all("SELECT * FROM billing_settlements")) == 1
    assert sum(Fraction(int(r["cost_n"]), int(r["cost_d"])) for r in store.all("SELECT * FROM billing_usage")) == 38
    with pytest.raises(BillingError, match="call_not_dispatchable"):
        dispatch(money, call, attempt="attempt-3", retry="again")


def test_fractional_overage_is_absorbed_not_carried_into_next_call(money):
    _, ledger, _, _ = money
    fund(money, 10)
    price = sale_price(price_id="fixture-half", input_uncached=Rate(1, 2))
    ledger.publish_price(price)
    call = reserve(money, amount=2, price=price.price_id, budget=10)
    result = settle(money, dispatch(money, call), usage=Usage(9, 0, 0))
    assert result.charged == 2 and result.absorbed == Fraction(5, 2)
    call2 = reserve(money, amount=2, call="next", price=price.price_id, budget=10)
    assert settle(money, dispatch(money, call2), usage=Usage(1, 0, 0)).charged == 0
    assert balance(money).booked == 8


def test_trusted_payment_id_event_and_order_dedupe(money):
    store, ledger, _, _ = money
    receipt = fund(money)
    original = ledger.credit_verified_payment(receipt)
    other = replace(receipt, event_id="different-event", evidence_ref="fixture-second-inbox", payload_sha256="b"*64)
    trust(money, other)
    assert ledger.credit_verified_payment(other) == original
    assert balance(money).booked == 100
    assert len(store.all("SELECT * FROM billing_payment_events")) == 2
    assert len(store.all("SELECT * FROM billing_payments")) == 1
    with pytest.raises(BillingError, match="payment_evidence_untrusted"):
        ledger.credit_verified_payment(replace(receipt, amount_minor=101))
    for name, value in (("amount_minor", 101), ("currency", "BAD"), ("merchant_id", "other")):
        tampered = replace(receipt, **{name: value, "event_id": "tampered-"+name})
        trust(money, tampered)
        with pytest.raises(BillingError, match="payment_order_mismatch"):
            ledger.credit_verified_payment(tampered)
    double = replace(receipt, payment_id="second-payment", event_id="second-payment-event")
    trust(money, double)
    with pytest.raises(BillingError, match="order_already_funded"):
        ledger.credit_verified_payment(double)
    closed = Ledger(store, environment="test", namespace="fixture-agent")
    with pytest.raises(BillingError, match="payment_evidence_untrusted"):
        closed.credit_verified_payment(receipt)
    assert_balanced(store)


def test_payment_same_transaction_cannot_credit_another_order_or_payer(money):
    receipt = fund(money)
    ledger = money[1]
    ledger.create_payment_order(order_id="other-order", payer_user_id=money[2][1], provider=receipt.provider,
                                 merchant_id=receipt.merchant_id, amount_minor=100, currency="XTS", unit=UNIT,
                                 credit_units=500, exchange_version="fixture-exchange-v1")
    attack = replace(receipt, order_id="other-order", event_id="new-event")
    trust(money, attack)
    with pytest.raises(BillingError, match="payment_transaction_conflict"):
        ledger.credit_verified_payment(attack)
    assert balance(money, user=1).booked == 0


def test_payment_id_cannot_be_reused_by_changing_merchant(money):
    receipt = fund(money)
    ledger = money[1]
    ledger.create_payment_order(order_id="merchant-order", payer_user_id=money[2][1], provider=receipt.provider,
                                 merchant_id="different-merchant", amount_minor=100, currency="XTS", unit=UNIT,
                                 credit_units=500, exchange_version="fixture-exchange-v1")
    duplicate = replace(receipt, order_id="merchant-order", merchant_id="different-merchant", event_id="merchant-event")
    trust(money, duplicate)
    with pytest.raises(BillingError, match="payment_transaction_conflict"):
        ledger.credit_verified_payment(duplicate)
    assert balance(money, user=1).booked == 0
    assert len(money[0].all("SELECT * FROM billing_payments")) == 1


def test_event_collision_rolls_back_posted_credit_and_payment(money):
    store, ledger, users, _ = money
    receipt = fund(money)
    ledger.create_payment_order(order_id="collision-order", payer_user_id=users[1], provider=receipt.provider,
                                 merchant_id=receipt.merchant_id, amount_minor=100, currency="XTS", unit=UNIT,
                                 credit_units=50, exchange_version="fixture-exchange-v1")
    collision = replace(receipt, order_id="collision-order", payment_id="collision-payment")
    # Test inbox can authenticate this envelope; ledger must independently refuse to use a
    # previously consumed event ID for another transaction, rolling back the earlier posting.
    store.execute("UPDATE fixture_payment_inbox SET receipt=? WHERE event_id=?",
                  (json.dumps(asdict(collision)), receipt.event_id))
    with pytest.raises(BillingError, match="payment_event_conflict"):
        ledger.credit_verified_payment(collision)
    assert balance(money, user=1).booked == 0
    assert len(store.all("SELECT * FROM billing_journal")) == 1
    assert len(store.all("SELECT * FROM billing_payments")) == 1
    assert ledger.audit_balances() == []


def test_dispatch_cancel_race_preserves_known_send_boundary(money):
    store, ledger, _, ticks = money
    fund(money)
    call = reserve(money)
    ticket = ledger.prepare_attempt(call, attempt_id="racing", owner="worker", requested_model="alias", cost_price_id="fixture-cost-v1")
    peer = Ledger(Store(store.root), environment="test", namespace="fixture-agent", authorize=fixture_authorize, clock=lambda: ticks[0])
    barrier = Barrier(2)

    def send():
        barrier.wait()
        try:
            peer.dispatch(ticket)
            return "sent_boundary"
        except BillingError as exc:
            assert exc.code == "dispatch_fenced"
            return "not_sent"

    def cancel():
        barrier.wait()
        return ledger.cancel_call(call)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(send)
        cancelling = pool.submit(cancel)
        sent, state = sending.result(), cancelling.result()
    if sent == "sent_boundary":
        assert state == "reserved" and balance(money).held == 20
        assert settle(money, ticket).charged == 9
    else:
        assert state == "released" and balance(money).held == 0
        with pytest.raises(BillingError, match="usage_without_dispatch"):
            settle(money, ticket)


def test_reserve_failure_on_last_unit_has_no_partial_journal_or_hold(money):
    fund(money, 1)
    call = reserve(money, amount=1)
    assert settle(money, dispatch(money, call), usage=Usage(1, 0, 0)).charged == 1
    assert balance(money).available == 0
    with pytest.raises(BillingError, match="insufficient_funds"):
        reserve(money, call="after-last-unit", amount=1)
    assert len(money[0].all("SELECT * FROM billing_calls")) == 1
    assert len(money[0].all("SELECT * FROM billing_journal")) == 2


def test_concurrent_payment_callbacks_credit_once(money):
    store, ledger, _, ticks = money
    receipt = fund(money)
    callbacks = [replace(receipt, event_id=f"concurrent-event-{i}", evidence_ref=f"fixture-inbox-{i}") for i in range(10)]
    for callback in callbacks:
        trust(money, callback)
    ledgers = [Ledger(Store(store.root), environment="test", namespace="fixture-agent", verify_payment=fixture_verify,
                       clock=lambda: ticks[0]) for _ in callbacks]
    with ThreadPoolExecutor(max_workers=10) as pool:
        journals = list(pool.map(lambda pair: pair[0].credit_verified_payment(pair[1]), zip(ledgers, callbacks)))
    assert len(set(journals)) == 1
    assert balance(money).booked == 100
    assert len(store.all("SELECT * FROM billing_payments")) == 1


def test_journal_and_contracts_are_immutable_even_replace(money):
    store, _, _, _ = money
    fund(money)
    call = reserve(money)
    ticket = dispatch(money, call)
    settle(money, ticket)
    for table in ("billing_journal", "billing_prices", "billing_usage", "billing_settlements", "billing_payments", "billing_orders"):
        with store.conn() as c:
            col = c.execute(f"PRAGMA table_info({table})").fetchone()[1]
        for sql in (f"UPDATE {table} SET {col}={col}", f"DELETE FROM {table}", f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}"):
            with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_fact"):
                store.execute(sql)
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_contract"):
        store.execute("UPDATE billing_calls SET deadline=deadline+1 WHERE id=?", (call,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_contract"):
        store.execute("UPDATE billing_calls SET status='reserved' WHERE id=?", (call,))
    # Alternate voucher uniqueness must not let REPLACE choose a new primary key and delete facts.
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing_fact"):
        store.execute("INSERT OR REPLACE INTO billing_journal SELECT 'new-id',environment,namespace,voucher,unit,debit_account,credit_account,amount,kind,reference,created_at FROM billing_journal LIMIT 1")
    assert_balanced(store)


def test_journal_db_rejects_negative_float_cross_unit_and_unfunded_debit(money):
    store, _, _, _ = money
    fund(money, 1)
    wallet = store.one("SELECT id FROM billing_accounts WHERE kind='wallet'")["id"]
    source = store.one("SELECT id FROM billing_accounts WHERE kind='test_payment'")["id"]
    for amount in (-1, 0.5, 2):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute("INSERT INTO billing_journal VALUES ('invalid','test','fixture','invalid',?,?,?,?, 'sale','fixture',?)",
                          (UNIT.code, wallet, source, amount, START))
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("INSERT INTO billing_journal VALUES ('wrong-unit','test','fixture','wrong',?,?,?,?, 'sale','fixture',?)",
                      (COST_UNIT.code, wallet, source, 0, START))
    assert balance(money).booked == 1


def test_balance_and_fraction_cache_rebuild_from_immutable_journal(money):
    store, ledger, _, _ = money
    fund(money)
    price = sale_price(price_id="thirds", input_uncached=Rate(1, 3))
    ledger.publish_price(price)
    settle(money, dispatch(money, reserve(money, amount=5, price=price.price_id)), usage=Usage(1, 0, 0))
    reserve(money, amount=15, call="still-held")
    # Deliberate fixture corruption only. There is no product balance setter.
    store.execute("UPDATE billing_balances SET amount=0")
    store.execute("DELETE FROM billing_remainders")
    before = store.all("SELECT * FROM billing_journal")
    assert len(ledger.audit_balances()) == 3
    assert len(ledger.audit_balances(rebuild=True)) == 3
    assert ledger.audit_balances() == []
    assert (balance(money).booked, balance(money).held, balance(money).available) == (100, 15, 85)
    assert store.all("SELECT * FROM billing_journal") == before
    call = reserve(money, amount=5, call="recovered-remainder", price=price.price_id)
    assert settle(money, dispatch(money, call), usage=Usage(2, 0, 0)).charged == 1


def test_nested_transaction_refused_and_commit_failure_rolls_back(money, monkeypatch):
    store, ledger, _, _ = money
    fund(money)
    ticket = dispatch(money, reserve(money))
    with store.tx():
        with pytest.raises(BillingError, match="billing_requires_own_transaction"):
            settle(money, ticket)
    original = ledger._event

    def crash_after_inserts(t, call_key, kind, reason, attempt_key=None):
        if kind == "settled":
            raise RuntimeError("fixture failure before commit")
        return original(t, call_key, kind, reason, attempt_key)

    monkeypatch.setattr(ledger, "_event", crash_after_inserts)
    with pytest.raises(RuntimeError, match="fixture failure"):
        settle(money, ticket)
    assert store.all("SELECT * FROM billing_usage") == []
    assert store.all("SELECT * FROM billing_settlements") == []
    assert balance(money).booked == 100 and balance(money).held == 20
    monkeypatch.setattr(ledger, "_event", original)
    assert settle(money, ticket).charged == 9


CHILD = r'''
import json, os, sys
from synwevo_core.store import Store
from synwevo_core.billing import Ledger, Usage, UsageEvidence, AttemptTicket
root, phase, ticket_json, stamp = sys.argv[1:]
store = Store(root)
ledger = Ledger(store, environment="test", namespace="fixture-agent", clock=lambda: int(stamp),
                authorize=lambda t,c,a: t.execute("SELECT 1 FROM fixture_billing_authority WHERE payer=? AND actor=? AND authority=?",
                (c.payer_user_id,c.actor_user_id,c.authorization_ref)).fetchone() is not None)
ticket = AttemptTicket(**json.loads(ticket_json))
if phase == "dispatch":
    ledger.dispatch(ticket)
    os._exit(73)
if phase == "before_commit":
    original = ledger._event
    def crash(t,call,kind,reason,attempt=None):
        if kind == "settled": os._exit(73)
        return original(t,call,kind,reason,attempt)
    ledger._event = crash
usage = UsageEvidence("fixture-provider","request-"+ticket.attempt_key,"fixture-final-"+ticket.attempt_key,
                       Usage(2,1,3,1),"fixture-model","fixture-capability-v1",int(stamp))
ledger.settle_trusted_usage(ticket.attempt_key,usage)
os._exit(73)
'''


def kill_child(money, ticket, phase):
    # Use the installed core package's parent as cwd, never mutate sys.path or import from the
    # other active worktree. The child opens its DB only through Store.
    import synwevo_core
    result = subprocess.run([sys.executable, "-c", CHILD, str(money[0].root), phase, json.dumps(asdict(ticket)), str(START)],
                            cwd=Path(synwevo_core.__file__).resolve().parent.parent,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_process_death_around_settlement_commit(money, phase):
    fund(money)
    ticket = dispatch(money, reserve(money))
    kill_child(money, ticket, phase)
    expected = 100 if phase == "before_commit" else 91
    assert balance(money).booked == expected
    assert settle(money, ticket).charged == 9
    assert balance(money).booked == 91
    assert len(money[0].all("SELECT * FROM billing_settlements")) == 1
    assert_balanced(money[0])


def test_process_death_after_dispatch_never_resends(money):
    fund(money)
    call = reserve(money)
    ticket = money[1].prepare_attempt(call, attempt_id="first", owner="worker", requested_model="alias", cost_price_id="fixture-cost-v1")
    kill_child(money, ticket, "dispatch")
    with pytest.raises(BillingError, match="dispatch_fenced"):
        money[1].dispatch(ticket)
    money[3][0] += 100
    with pytest.raises(BillingError, match="attempt_not_claimable"):
        money[1].claim_prepared(ticket.attempt_key, owner="restarted-worker")
    assert money[1].cancel_call(call) == "reserved"
    money[3][0] = START+500
    assert money[1].expire_due() == 1
    assert settle(money, ticket).charged == 0
    assert balance(money).booked == 100
