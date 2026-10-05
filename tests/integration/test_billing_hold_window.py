"""A paid call is sent only while its hold can outlive the send (review finding money-1).

The ledger treats a call's hold deadline as final: usage that arrives after it is recorded as the
platform's cost and never charged ("deadline_final_no_later_charge"). A send dispatched one
second before that deadline could therefore stream for the transport's whole timeout and settle
for free. The send boundary must refuse a dispatch whose hold ends before the longest possible
send (the dispatcher's ``send_window``) does; a dispatch that fits is charged normally.

Isolated fixture stores only: fixture units/prices/receipts, no provider, no network.
"""
from __future__ import annotations

import pytest

from synwevo_core.billing import BillingError, ChargeContext, PriceSnapshot, Rate, Usage, UsageEvidence

from tests.integration.test_billing_grants_chargebacks import START, UNIT, balance, money, pay  # noqa: F401

SEND_WINDOW = 150   # a 120 s transport timeout plus 30 s to record and settle (fixture value)


def prepared(money, *, deadline, run="run-window"):
    store, ledger, users, _ = money
    uid = users[0]
    context = ChargeContext(uid, uid, run, "fixture-approval")
    ledger.open_run(context, unit=UNIT, budget=10**6, max_calls=10, max_attempts=10, deadline=deadline)
    ledger.publish_price(PriceSnapshot("fixture-cost", "cost", UNIT, "fixture-provider", "fixture-model",
                                       Rate(0), Rate(0), Rate(0), "fixture-terms", START, per_tokens=1))
    call = ledger.reserve_call(context, logical_call_id="call-" + run, amount=500, price_id="fixture-sale",
                               deadline=deadline)
    ticket = ledger.prepare_attempt(call, attempt_id="attempt-" + run, owner="worker",
                                    cost_price_id="fixture-cost", requested_model="fixture-model")
    return call, ticket


def settle(money, ticket, tokens, at):
    return money[1].settle_trusted_usage(ticket.attempt_key, UsageEvidence(
        "fixture-provider", "req-" + ticket.attempt_key, "ev", Usage(tokens, 0, 0), "fixture-model", "cap", at))


def test_a_send_that_could_outlive_its_hold_is_refused_before_any_network(money):
    store, ledger, _, ticks = money
    pay(money, "order-1")
    call, ticket = prepared(money, deadline=START + 20)
    ticks[0] = START + 19                      # the lease is still valid: only the window refuses
    with pytest.raises(BillingError, match="run_time_too_short"):
        ledger.dispatch(ticket, send_window=SEND_WINDOW)
    attempt = store.one("SELECT status,dispatched_at FROM billing_attempts WHERE id=?", (ticket.attempt_key,))
    assert attempt["status"] == "prepared" and attempt["dispatched_at"] is None, "没有越过发送边界"
    # Provably unsent, so cancelling releases the hold at once: nothing owed, nothing lost.
    assert ledger.cancel_call(call) == "released"
    assert (balance(money).booked, balance(money).held) == (1000, 0)


def test_a_send_that_fits_its_hold_is_charged_when_it_finishes_at_the_last_moment(money):
    _, ledger, _, ticks = money
    pay(money, "order-1")
    _, ticket = prepared(money, deadline=START + 10 + SEND_WINDOW)
    ticks[0] = START + 10                      # exactly the window left: allowed
    ledger.dispatch(ticket, send_window=SEND_WINDOW)
    ticks[0] = START + 10 + SEND_WINDOW - 1    # the longest send ends and settles inside the hold
    result = settle(money, ticket, 400, ticks[0])
    assert not result.late and result.charged == 400, "发出的调用在保留期内结算，按实际用量收费"
    assert (balance(money).booked, balance(money).held) == (600, 0)


def test_one_second_short_of_the_window_is_refused(money):
    _, ledger, _, ticks = money
    pay(money, "order-1")
    _, ticket = prepared(money, deadline=START + 10 + SEND_WINDOW)
    ticks[0] = START + 11
    with pytest.raises(BillingError, match="run_time_too_short"):
        ledger.dispatch(ticket, send_window=SEND_WINDOW)


@pytest.mark.parametrize("window", [0, -1, True, 1.5, 86401])
def test_a_send_window_must_be_a_positive_whole_number_of_seconds(money, window):
    pay(money, "order-1")
    _, ticket = prepared(money, deadline=START + 900)
    with pytest.raises(BillingError):
        money[1].dispatch(ticket, send_window=window)
