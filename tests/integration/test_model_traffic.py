"""Model-call traffic: rates, in-flight calls, bounded fair waiting (docs/platform/model-traffic.md).

Real Store/Ledger; fixture limits and a fixture clock. Expectations are independent arithmetic,
not values computed by the implementation. No provider, key or payment is contacted.
"""
from dataclasses import replace

import pytest

from synwevo_core.billing import BillingError, Ledger
from synwevo_core.billing.admission import CostPolicy
from synwevo_core.billing.traffic import GRANT_TTL_S, RETAIN_S, ModelTraffic, TrafficCancelled, TrafficLimits
from synwevo_core.store import Store, new_id
from tests.integration.test_billing_ledger import COST_UNIT, fixture_authorize, fixture_verify, money  # noqa: F401

LIMITS = TrafficLimits(platform_rpm=100, platform_tpm=100_000, payer_rpm=100, payer_tpm=100_000,
                       payer_inflight=10, wait_seconds=300, max_waiting=50, max_waiting_per_payer=20)


def policy(traffic=LIMITS, **changes):
    return replace(CostPolicy("fixture-policy-v1", "fixture-provider-account", "fixture-provider",
                              COST_UNIT, True, 1000, 2000, 3000, 1000, 100, 100, traffic), **changes)


class World:
    def __init__(self, money, **limits):
        self.store, self.ledger, self.users, self.ticks = money
        self.policy = policy(replace(LIMITS, **limits)) if limits else policy()
        self.traffic = self.make(self.ledger)

    def make(self, ledger):
        def sleep(_):  # Each poll lets one fixture second pass; nothing really sleeps.
            self.ticks[0] += 1
        return ModelTraffic(ledger, current_policy=lambda: self.policy, sleep=sleep)

    def acquire(self, payer, tokens=10, *, deadline=None, should_stop=lambda: False, traffic=None):
        return (traffic or self.traffic).acquire(payer, est_tokens=tokens,
                                                 deadline=deadline or self.ticks[0] + 10_000, should_stop=should_stop)

    def admit(self, claim, *, inflight=120):
        with self.store.tx() as t:
            self.traffic.admit(t, claim, attempt_key=new_id("attempt"), inflight_until=self.ticks[0] + inflight)

    def waiting(self, payer, tokens=10):
        """A queued call by another worker: the state acquire() writes before it polls."""
        claim, now = new_id("traffic"), self.ticks[0]
        self.store.execute("INSERT INTO model_traffic_claims(id,environment,account_ref,payer_user_id,est_tokens,"
                           "state,enqueued_at,wait_until) VALUES (?,?,?,?,?,'waiting',?,?)",
                           (claim, "test", "fixture-provider-account", payer, tokens, now, now + 300))
        self.ticks[0] += 1  # Distinct arrival seconds keep the expected order unambiguous.
        return claim

    def grant(self):
        with self.store.tx() as t:
            self.traffic._grant(t, self.policy, self.policy.traffic, self.ticks[0])

    def state(self, claim):
        return self.store.one("SELECT state FROM model_traffic_claims WHERE id=?", (claim,))["state"]


def test_without_traffic_limits_nothing_waits_or_is_recorded(money):
    w = World(money)
    w.policy = policy(None)
    assert w.acquire("payer-a") is None and w.traffic.queue_state("payer-a") is None
    assert w.store.all("SELECT * FROM model_traffic_claims") == []


@pytest.mark.parametrize("changes", [dict(payer_rpm=101), dict(payer_tpm=100_001), dict(max_waiting_per_payer=51),
                                     dict(wait_seconds=901), dict(platform_rpm=0), dict(max_waiting=10_001),
                                     dict(payer_inflight=True)])
def test_limits_reject_a_payer_above_the_platform_and_unbounded_waiting(changes):
    with pytest.raises(BillingError):
        replace(LIMITS, **changes)


def test_a_payer_may_not_hold_more_calls_in_flight_than_the_platform():
    with pytest.raises(BillingError):
        policy(replace(LIMITS, payer_inflight=11), max_inflight=10)


def test_payer_requests_per_minute_wait_for_the_window_then_proceed(money):
    w = World(money, payer_rpm=2)
    start = w.ticks[0]
    for _ in range(2):
        w.admit(w.acquire("payer-a"))
    assert w.ticks[0] == start  # Within the limit nothing waits.
    third = w.acquire("payer-a")
    # The first two were admitted at `start`; the third fits once they leave the 60 s window.
    assert w.state(third) == "granted" and w.ticks[0] - start == 60
    other = w.acquire("payer-b")  # Another payer is not held back by payer-a's limit.
    assert w.state(other) == "granted"


def test_tokens_count_the_estimate_until_trusted_usage_replaces_it(money):
    w = World(money, payer_tpm=100, platform_tpm=100)
    first = w.acquire("payer-a", tokens=80)
    w.admit(first)
    w.traffic.finish(first, tokens=15)       # Actual usage frees 65 of the 80 estimated.
    second = w.acquire("payer-a", tokens=80)  # 15 + 80 <= 100: no wait.
    assert w.state(second) == "granted"
    w.admit(second)
    w.traffic.finish(second, tokens=None)    # Unknown usage keeps the full estimate.
    start = w.ticks[0]
    third = w.acquire("payer-a", tokens=10)  # 15 + 80 + 10 > 100 until the window passes.
    assert w.state(third) == "granted" and w.ticks[0] > start


def test_a_call_larger_than_any_window_is_refused_instead_of_blocking_the_queue(money):
    w = World(money, payer_tpm=100, platform_tpm=1000)
    with pytest.raises(BillingError) as err:
        w.acquire("payer-a", tokens=101)
    assert err.value.code == "traffic_request_too_large"
    assert w.store.all("SELECT * FROM model_traffic_claims") == []


def test_in_flight_calls_free_on_known_usage_and_hold_until_expiry_when_unknown(money):
    w = World(money, payer_inflight=1)
    first = w.acquire("payer-a")
    w.admit(first, inflight=30)
    waiting = w.waiting("payer-a")
    w.grant()
    assert w.state(waiting) == "waiting"
    w.traffic.finish(first, tokens=None)     # Unknown: the slot stays taken until its expiry.
    w.grant()
    assert w.state(waiting) == "waiting"
    w.ticks[0] += 30
    w.grant()
    assert w.state(waiting) == "granted"


def test_payers_take_turns_and_one_payers_limit_does_not_block_others(money):
    w = World(money, platform_rpm=3, payer_rpm=3)
    a = [w.waiting("payer-a") for _ in range(3)]
    b = w.waiting("payer-b")
    c = w.waiting("payer-c")
    w.grant()
    # Round robin in arrival order: a1, b1, c1 -- not a1, a2, a3.
    assert [w.state(x) for x in (*a, b, c)] == ["granted", "waiting", "waiting", "granted", "granted"]

    w2 = World(money, payer_inflight=1)
    w2.store.execute("DELETE FROM model_traffic_claims")
    held = w2.acquire("payer-a")
    w2.admit(held)
    blocked, free = w2.waiting("payer-a"), w2.waiting("payer-b")
    w2.grant()
    assert (w2.state(blocked), w2.state(free)) == ("waiting", "granted")


def test_the_least_recently_served_payer_goes_first(money):
    w = World(money, platform_rpm=1, payer_rpm=1)
    served = w.acquire("payer-a")
    w.admit(served)
    w.ticks[0] += 61
    again, newcomer = w.waiting("payer-a"), w.waiting("payer-b")
    w.grant()
    assert (w.state(again), w.state(newcomer)) == ("waiting", "granted")


def test_platform_capacity_keeps_the_order_instead_of_letting_small_calls_overtake(money):
    w = World(money, platform_tpm=100, payer_tpm=100)
    admitted = w.acquire("payer-c", tokens=60)
    w.admit(admitted)
    big, small = w.waiting("payer-a", tokens=50), w.waiting("payer-b", tokens=10)
    w.grant()
    # 60 + 50 > 100: the big call is next in order, so the small one does not jump ahead of it.
    assert (w.state(big), w.state(small)) == ("waiting", "waiting")


def test_waiting_ends_at_its_allowance_or_the_run_deadline_and_frees_nothing_it_never_had(money):
    w = World(money, payer_rpm=1, wait_seconds=5)
    w.admit(w.acquire("payer-a"))
    start = w.ticks[0]
    with pytest.raises(BillingError) as err:
        w.acquire("payer-a")
    assert err.value.code == "traffic_wait_timeout" and w.ticks[0] - start == 5
    with pytest.raises(BillingError):
        w.acquire("payer-a", deadline=w.ticks[0] + 2)
    states = [r["state"] for r in w.store.all("SELECT state FROM model_traffic_claims ORDER BY rowid")]
    assert states[0] == "admitted" and all(s in ("expired", "abandoned") for s in states[1:])


def test_no_waiting_allowance_still_gets_one_chance(money):
    w = World(money, wait_seconds=0)
    assert w.state(w.acquire("payer-a")) == "granted"


def test_stopping_while_waiting_abandons_the_place(money):
    w = World(money, payer_rpm=1)
    w.admit(w.acquire("payer-a"))
    polls = []
    with pytest.raises(TrafficCancelled):
        w.acquire("payer-a", should_stop=lambda: polls.append(1) or len(polls) > 2)
    assert w.store.one("SELECT COUNT(*) AS n FROM model_traffic_claims WHERE state='waiting'")["n"] == 0


def test_full_queues_refuse_new_work_at_once(money):
    w = World(money, max_waiting=3, max_waiting_per_payer=2, payer_rpm=1, platform_rpm=1)
    w.admit(w.acquire("payer-z"))
    w.waiting("payer-a"), w.waiting("payer-a")
    state = w.traffic.queue_state("payer-a")
    assert state["full"] and state["payer_waiting"] == 2 and state["retry_after"] >= 1
    with pytest.raises(BillingError) as err:
        w.acquire("payer-a")
    assert err.value.code == "traffic_payer_queue_full"
    w.waiting("payer-b")
    with pytest.raises(BillingError) as err:
        w.acquire("payer-c")
    assert err.value.code == "traffic_queue_full"
    assert w.traffic.queue_state("payer-q")["full"]  # The whole queue is full for everyone.


def test_an_unused_grant_expires_and_cannot_be_admitted_later(money):
    w = World(money, payer_rpm=1)
    claim = w.acquire("payer-a")
    w.ticks[0] += GRANT_TTL_S
    with pytest.raises(BillingError) as err:
        w.admit(claim)
    assert err.value.code == "traffic_grant_expired"
    nxt = w.acquire("payer-a")  # The expired grant no longer counts against the minute.
    assert w.state(nxt) == "granted"


def test_unreadable_policy_refuses_instead_of_admitting(money):
    w = World(money)

    def broken():
        raise BillingError("cost_not_configured")
    w.traffic.current_policy = broken
    with pytest.raises(BillingError):
        w.acquire("payer-a")


def test_two_processes_share_one_set_of_counters(money):
    w = World(money, payer_rpm=1)
    other_store = Store(w.store.root)
    other = w.make(Ledger(other_store, environment="test", namespace="fixture-other-process",
                          authorize=fixture_authorize, verify_payment=fixture_verify, clock=lambda: w.ticks[0]))
    w.admit(w.acquire("payer-a"))
    start = w.ticks[0]
    claim = w.acquire("payer-a", traffic=other)
    assert w.state(claim) == "granted" and w.ticks[0] - start == 60


def test_ended_rows_are_pruned_after_a_day_and_live_ones_never(money):
    w = World(money)
    done = w.acquire("payer-a")
    w.admit(done)
    w.traffic.finish(done, tokens=5)
    unknown = w.acquire("payer-a")
    w.admit(unknown, inflight=2 * RETAIN_S)  # Usage never arrived and its slot is still held.
    w.traffic.finish(unknown, tokens=None)
    w.ticks[0] += RETAIN_S + 1
    waiting = w.waiting("payer-b")
    w.grant()
    left = {r["id"] for r in w.store.all("SELECT id FROM model_traffic_claims")}
    assert done not in left and {unknown, waiting} <= left
