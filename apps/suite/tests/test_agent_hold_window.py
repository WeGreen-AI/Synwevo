"""The gateway sends a paid call only when the run still has time for the whole send (money-1).

A call's hold ends at the run deadline; usage that arrives after it is the platform's cost and
never charged. The transport may stream for its whole timeout, so a step dispatched shortly
before the deadline used to settle as "late" and be free. These tests state the business rule
independently of the implementation: a call that cannot finish inside its hold is refused
unsent and unreserved; one that can is sent and charged; and a quote cannot ask for a run too
short for even one call. Real Store/Ledger, isolated SSE fixture transport, no network.
"""
import math

import pytest

from synwevo_core.billing import BillingError
from synwevo_core.errors import ServiceError

from .test_agent_metering import STAMP, gateway  # noqa: F401 - explicit isolated fixture
from .test_agent_run_quotes import paid  # noqa: F401 - explicit isolated fixture

RUN_DEADLINE = STAMP + 500          # the gateway fixture's run


def window(g):
    # Independent bound: the transport may stream for its whole timeout before usage arrives.
    assert g.model.send_window > math.ceil(g.model.limits.timeout_s)
    return g.model.send_window


def test_a_step_with_less_run_time_left_than_one_send_is_refused_unsent_and_unreserved(gateway):
    g = gateway
    g.ticks[0] = RUN_DEADLINE - window(g) + 1
    with pytest.raises(BillingError, match="run_time_too_short") as caught:
        g.model.step(**g.args)
    assert getattr(caught.value, "not_dispatched", False), "明确未发送、未预留"
    assert g.seen == [] and g.store.all("SELECT * FROM billing_calls") == []
    assert (g.balance().booked, g.balance().held) == (1000, 0)


def test_a_step_with_exactly_one_send_left_is_sent_and_charged(gateway):
    g = gateway
    g.ticks[0] = RUN_DEADLINE - window(g)
    g.model.step(**g.args)
    assert len(g.seen) == 1 and (g.balance().booked, g.balance().held) == (958, 0)
    assert g.store.one("SELECT late,charge FROM billing_usage")["charge"] == 42


def test_time_spent_after_reserving_cannot_push_a_send_past_its_hold(gateway, monkeypatch):
    """The ledger's own dispatch transaction rechecks the window: whatever delayed the step
    between reserving and sending, a send that no longer fits is not made."""
    g = gateway
    g.ticks[0] = RUN_DEADLINE - window(g)
    prepare = g.ledger.prepare_attempt

    def slow_prepare(*args, **kwargs):
        ticket = prepare(*args, **kwargs)
        g.ticks[0] += 5                 # e.g. a slow writer lock before the send boundary
        return ticket
    monkeypatch.setattr(g.ledger, "prepare_attempt", slow_prepare)
    with pytest.raises(BillingError, match="run_time_too_short") as caught:
        g.model.step(**g.args)
    assert getattr(caught.value, "not_dispatched", False)
    assert g.seen == [], "未越过发送边界"
    assert (g.balance().booked, g.balance().held) == (1000, 0)
    assert g.store.one("SELECT status FROM billing_calls")["status"] == "released"


def test_the_turn_reports_the_refusal_as_unsent_and_free(paid):
    g = paid
    from synwevo_suite.agent.gateway import MeteredTurnProvider
    q = g.issue()
    turn = g.agent.start_turn(g.user, g.conversation["id"], "fixture question", parent_id=None,
                              regenerate_of=None, quote_id=q["id"])
    g.ticks[0] = q["deadline"] - window(g) + 1
    g.agent._run(turn["turn_id"])
    row = g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (turn["turn_id"],))
    assert row["status"] == "failed" and row["error_code"] == "billing_run_time_too_short"
    assert g.seen == [] and (g.balance().booked, g.balance().held) == (1000, 0)
    assert isinstance(g.agent.provider, MeteredTurnProvider)


def test_a_quote_cannot_ask_for_a_run_too_short_for_one_call(paid):
    g = paid
    offer = g.quotes.offer()
    assert offer["run_seconds_min"] == window(g) <= offer["run_seconds_limit"]
    with pytest.raises(ServiceError) as caught:
        g.issue(run_seconds=window(g) - 1)
    assert caught.value.code == "billing_run_time_too_short"
    assert g.store.all("SELECT * FROM agent_model_quotes") == []
    assert g.issue(run_seconds=window(g))["status"] == "quoted"
