"""Calls that reach DeepSeek but end without usage, on the real Suite factory (review runtime-1).

FIXTURE transports and synthetic prices only; no real provider. The fixture cost policy allows
max_unknown=4 and max_inflight=4, so five such endings are enough to show whether unknown usage
accumulates into a platform-wide refusal for everybody.

Business expectations:
* An HTTP error answer (any status but 200, before any stream) consumed nothing billable: it is
  settled at once as known zero usage, never sold, and its hold is released immediately.
* A stream the platform stopped itself (the person's Stop, the output guard, a shutdown) stays
  unknown and unsold within its hold, but after its in-flight window it is the payer's exposure,
  not a platform-wide unknown: other turns keep dispatching.
* Any other unknown ending (a cut stream, a timeout) fails closed platform-wide only until the
  call's hold deadline; then it remains in the cost windows at its conservative bound.
"""
import httpx
import pytest

from .test_agent_governed_factory import UNIT, configure, fund, governed  # noqa: F401 - fixture import
from .test_agent_incomplete_charging import Then
from .test_agent_metering import Wire, complete, event


def turn(g, *, run_seconds=900):
    q = g.agent.quote_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None, regenerate_of=None,
                          budget=5000, max_calls=3, run_seconds=run_seconds)
    tid = g.agent.start_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None,
                             regenerate_of=None, quote_id=q['id'])['turn_id']
    g.agent._run(tid)
    return g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))


def clock(g):
    """The ledger's clock (holds, admission windows, route validity), advanced by the test."""
    import time
    state = {'now': int(time.time())}
    g.provider.gateway.ledger.clock = lambda: state['now']
    return state


def answer(g, respond):
    async def handle(request):
        g.seen.append(request)
        return respond(len(g.seen))
    g.provider.gateway.transport = httpx.MockTransport(handle)


def healthy(n):
    body = complete().replace(b'fixture-upstream-1', f'fixture-upstream-{n}'.encode())
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(body))


def balance(g):
    uid = g.user['id']
    return g.provider.gateway.ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=UNIT)


def snapshot(g):
    return g.provider.gateway.cost_admission.snapshot()


@pytest.mark.parametrize('statuses', [(400, 401, 404, 422, 500)])
def test_error_answers_are_known_zero_usage_and_release_their_hold_at_once(governed, statuses):
    g = governed; configure(g); fund(g)
    before = balance(g)
    pending = list(statuses)
    answer(g, lambda n: httpx.Response(pending.pop(0), json={'error': {'message': 'fixture refusal'}})
           if pending else healthy(n))
    for status in statuses:  # More refusals than max_unknown (4) and max_inflight (4) allow.
        refused = turn(g)
        assert refused['status'] == 'failed' and refused['usage_state'] == 'known', refused['error_code']
    rows = g.store.all('SELECT a.status AS attempt, a.request_id, c.status AS call, u.outcome, u.cost_n, u.charge '
                       'FROM billing_attempts a JOIN billing_calls c ON c.id=a.call_key '
                       'JOIN billing_usage u ON u.attempt_key=a.id ORDER BY a.rowid')
    assert len(rows) == len(statuses)
    for row, status in zip(rows, statuses):
        assert (row['attempt'], row['call'], row['outcome'], row['cost_n'], row['charge']) == (
            'settled', 'released', 'platform_fault', '0', 0)
        assert row['request_id'].startswith(f'refused:http-{status}:')
    assert g.store.all('SELECT * FROM model_cost_pending') == []
    assert g.store.all('SELECT * FROM billing_settlements') == []
    assert (snapshot(g)['unknown_count'], snapshot(g)['inflight']) == (0, 0)
    assert balance(g).booked == before.booked and balance(g).held == 0  # Released now, not at the deadline.
    # Paid dispatch continues for everyone: the next turn is sent and settled normally.
    assert turn(g)['status'] == 'succeeded'
    assert len(g.seen) == len(statuses) + 1


def test_stopped_streams_are_the_payers_exposure_and_never_close_dispatch_for_everyone(governed):
    g = governed; configure(g); fund(g)
    state = clock(g)
    stop = lambda: g.store.execute("UPDATE agent_turns SET cancel_requested=1 WHERE status='running'")  # noqa: E731
    answer(g, lambda n: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Then(
        event({'content': 'partial'}).replace(b'fixture-upstream-1', f'fixture-upstream-{n}'.encode()),
        event(finish='stop').replace(b'fixture-upstream-1', f'fixture-upstream-{n}'.encode()), stop))
        if n <= 5 else healthy(n))
    for _ in range(5):
        stopped = turn(g)
        assert stopped['status'] == 'cancelled' and stopped['usage_state'] == 'pending'
        state['now'] += 130  # Past this call's in-flight window (timeout + 1 s), within its hold.
    # Unknown and unsold within the hold: never charged beyond it, the hold stays until its deadline.
    assert g.store.all('SELECT * FROM billing_usage') == [] and g.store.all('SELECT * FROM billing_settlements') == []
    assert [r['status'] for r in g.store.all('SELECT status FROM billing_calls')] == ['reserved'] * 5
    assert balance(g).held > 0
    assert [r['ending'] for r in g.store.all('SELECT ending FROM model_cost_endings')] == ['stopped_locally'] * 5
    counters = snapshot(g)
    assert (counters['unknown_count'], counters['inflight'], counters['absorbed_unknown_count']) == (0, 0, 5)
    # Still counted where it belongs: at its conservative bound in the platform's cost windows.
    reserved = sum(r['reserved_units'] for r in g.store.all('SELECT reserved_units FROM model_cost_admissions'))
    assert counters['absorbed_unknown_units'] == reserved
    assert counters['daily']['numerator'] == reserved * counters['daily']['denominator']
    # Five stops (more than max_unknown 4) do not stop anybody's next paid call.
    assert turn(g)['status'] == 'succeeded'


def test_other_unknown_endings_fail_closed_only_until_their_hold_deadline(governed):
    g = governed; configure(g); fund(g)
    state = clock(g)
    cut = lambda n: httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(  # noqa: E731
        event({'content': 'partial'}).replace(b'fixture-upstream-1', f'fixture-upstream-{n}'.encode())))
    answer(g, lambda n: cut(n) if n <= 4 else healthy(n))
    start = state['now']
    for _ in range(4):
        assert turn(g)['error_code'] == 'provider_stream_incomplete'
        state['now'] += 130
    # Unexplained unknown usage: platform-wide exposure while the holds are open (max_unknown 4).
    assert (snapshot(g)['unknown_count'], snapshot(g)['inflight']) == (4, 0)
    refused = turn(g)
    assert refused['status'] == 'failed' and refused['error_code'] == 'billing_cost_unknown_limit'
    assert len(g.seen) == 4  # Refused before sending.
    # Every hold ends at its deadline (dispatch + 900 s): the exposure is released platform-wide.
    state['now'] = start + 4 * 130 + 901
    counters = snapshot(g)
    assert (counters['unknown_count'], counters['absorbed_unknown_count']) == (0, 4)
    assert turn(g)['status'] == 'succeeded'


def test_recovery_settles_an_error_answer_whose_settlement_was_interrupted(governed, monkeypatch):
    g = governed; configure(g); fund(g)
    answer(g, lambda n: httpx.Response(500, json={'error': {'message': 'fixture server error'}}))
    gateway = g.provider.gateway
    original = gateway._settle_refused
    def crash(*args, **kwargs):
        raise RuntimeError('fixture: process stopped before settlement')
    monkeypatch.setattr(gateway, '_settle_refused', crash)
    q = g.agent.quote_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None, regenerate_of=None,
                          budget=5000, max_calls=3, run_seconds=900)
    tid = g.agent.start_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None,
                             regenerate_of=None, quote_id=q['id'])['turn_id']
    g.agent._run_safely(tid)  # The worker's entry point: an unexpected failure ends the turn.
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] == 'failed'
    assert g.store.one('SELECT status FROM billing_attempts')['status'] == 'dispatch_unknown'
    monkeypatch.setattr(gateway, '_settle_refused', original)
    result = g.suite.api.state.agent_metering_recovery.run_once()
    assert [(c.state, c.charge, c.outcome) for c in result.calls] == [('settled', 0, 'platform_fault')]
    assert g.store.one('SELECT status FROM billing_calls')['status'] == 'released'
    assert g.store.all('SELECT * FROM model_cost_pending') == [] and balance(g).held == 0
    assert len(g.seen) == 1  # Recovery never resends.
