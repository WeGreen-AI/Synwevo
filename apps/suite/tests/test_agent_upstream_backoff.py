"""Upstream 429/503 on the real Suite factory: bounded, jittered cool-down for attempts that are
still provably unsent; the attempt the provider refused is never resent. FIXTURE transports only.
"""
import httpx
import pytest

from synwevo_suite.agent import gateway as gateway_module

from .test_agent_governed_factory import configure, fund, governed, start  # noqa: F401 - fixture import
from .test_agent_metering import Wire, complete


def throttle(g, statuses, *, retry_after=None):
    """Answer the next requests with the given HTTP statuses, then with a complete stream."""
    async def respond(request):
        g.seen.append(request)
        assert g.store.one("SELECT COUNT(*) AS n FROM billing_attempts WHERE status='dispatch_unknown'")['n'] == 1
        if statuses:
            status = statuses.pop(0)
            headers = {'retry-after': str(retry_after)} if retry_after is not None else {}
            return httpx.Response(status, headers=headers, json={'error': {'message': 'fixture throttle'}})
        body = complete().replace(b'fixture-upstream-1', f'fixture-upstream-{len(g.seen)}'.encode())
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(body))
    g.provider.gateway.transport = httpx.MockTransport(respond)


@pytest.fixture
def clock(governed):
    """A controllable monotonic clock for the cool-down; sleeping advances it."""
    gateway = governed.provider.gateway
    state = {'now': 1000.0, 'slept': [], 'random': 0.5, 'during_sleep': []}
    gateway._clock = lambda: state['now']
    gateway._random = lambda: state['random']
    def sleep(seconds):
        # Every wait happens before anything is reserved or fenced for the waiting turn.
        state['during_sleep'].append(governed.store.one("SELECT COUNT(*) AS n FROM billing_calls")['n'])
        state['slept'].append(seconds)
        state['now'] += seconds
    gateway._sleep = sleep
    return state


def turn(g):
    tid = start(g)
    g.agent._run(tid)
    return g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))


@pytest.mark.parametrize('status,code', [(429, 'provider_rate_limited'), (503, 'provider_overloaded')])
def test_a_throttled_attempt_is_not_resent_and_the_next_call_waits_unsent(governed, clock, status, code):
    g = governed; configure(g); fund(g)
    throttle(g, [status], retry_after=2)
    first = turn(g)
    assert first['status'] == 'failed' and first['error_code'] == code
    assert len(g.seen) == 1  # The refused attempt reached the provider once and is never resent.
    # The refusal consumed nothing billable: settled as known zero usage, its call released, so
    # it can never be prepared or sent again, and it leaves no unknown exposure behind.
    attempt = g.store.one('SELECT a.status, c.status AS call, u.outcome, u.cost_n FROM billing_attempts a '
                          'JOIN billing_calls c ON c.id=a.call_key JOIN billing_usage u ON u.attempt_key=a.id')
    assert attempt == {'status': 'settled', 'call': 'released', 'outcome': 'platform_fault', 'cost_n': '0'}
    assert first['usage_state'] == 'known'
    assert clock['slept'] == []

    second = turn(g)
    assert second['status'] == 'succeeded'
    assert len(g.seen) == 2
    # Waited out Retry-After (2 s) plus at most 20% jitter, before reserving anything for it.
    assert 2.0 <= sum(clock['slept']) <= 2.4 + 0.25
    assert set(clock['during_sleep']) == {1}
    assert g.store.all('SELECT * FROM billing_settlements')  # The second call settled normally.


def test_a_cool_down_beyond_the_bound_refuses_unsent_and_unreserved(governed, clock):
    g = governed; configure(g); fund(g)
    throttle(g, [429], retry_after=120)
    turn(g)
    refused = turn(g)
    assert refused['status'] == 'failed' and refused['error_code'] == 'provider_rate_limited'
    assert refused['usage_state'] == 'known'
    assert len(g.seen) == 1 and clock['slept'] == []
    assert g.store.one('SELECT COUNT(*) AS n FROM billing_calls')['n'] == 1  # Nothing reserved for it.
    clock['now'] += 150  # After the cool-down the provider is tried again.
    assert turn(g)['status'] == 'succeeded' and len(g.seen) == 2


def test_computed_backoff_is_jittered_exponential_capped_and_reset_by_success(governed, clock):
    g = governed; gateway = g.provider.gateway
    delays = []
    for random_value in (0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0, 1.0):
        clock['random'] = random_value
        before = clock['now']
        gateway._cool_until = 0.0
        gateway._throttled(429, None)
        delays.append(gateway._cool_until - before)
    # Equal jitter over 1, 2, 4, 8, 16, 30 (cap), 30, 30 seconds.
    assert delays == [0.5, 2.0, 2.0, 8.0, 16.0, 30.0, 30.0, 30.0]
    gateway._recovered()
    gateway._cool_until = 0.0
    clock['random'] = 1.0
    gateway._throttled(503, None)
    assert gateway._cool_until - clock['now'] == 1.0 and gateway._cool_code == 'provider_overloaded'


def test_stopping_while_waiting_sends_and_reserves_nothing(governed, clock):
    g = governed; configure(g); fund(g)
    throttle(g, [429], retry_after=5)
    turn(g)
    tid = start(g)
    def stop_then_sleep(seconds):
        g.store.execute('UPDATE agent_turns SET cancel_requested=1 WHERE id=?', (tid,))
        clock['now'] += seconds
    g.provider.gateway._sleep = stop_then_sleep
    g.agent._run(tid)
    stopped = g.store.one('SELECT status,usage_state FROM agent_turns WHERE id=?', (tid,))
    assert stopped['status'] == 'cancelled' and stopped['usage_state'] == 'known'
    assert len(g.seen) == 1 and g.store.one('SELECT COUNT(*) AS n FROM billing_calls')['n'] == 1


def test_bounds_are_conservative():
    assert gateway_module.MAX_WAIT_S <= 60 and gateway_module.BACKOFF_CAP_S <= gateway_module.MAX_WAIT_S
    assert set(gateway_module.THROTTLED) == {429, 503}
