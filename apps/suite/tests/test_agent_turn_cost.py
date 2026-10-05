"""Each answer's cost comes from the money ledger, and the composer's balance from the wallet.

Fixture prices, funds, routes and SSE only (see test_agent_governed_factory); no real provider,
payment service or network is contacted.
"""
import json
import uuid

import pytest

from synwevo_suite.agent.ops import MODEL_CAPABILITIES
from synwevo_suite.agent.turn_cost import turn_costs
from .test_agent_governed_factory import governed, configure, fund, publish, start  # noqa: F401
from .test_agent_metering import complete, event


def call(g, operation, payload=None, *, user=None):
    return g.suite.caps.invoke(operation, payload or {}, user_id=(user or g.user)['id'],
                               via='ui', idempotency_key=uuid.uuid4().hex)['output']


def answer(g, turn_id):
    conv = call(g, 'agent.conversation.get', {'conversation_id': g.conversation['id']})
    return next(m for m in conv['messages'] if m['turn'] and m['turn']['id'] == turn_id)


def test_settled_answer_carries_its_ledger_charge_and_settled_tokens(governed):
    g = governed; configure(g); fund(g)
    turn = start(g)
    g.agent._run(turn)
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (turn,))['status'] == 'succeeded'
    # The engine's own counters are not the bill: a different number there changes nothing.
    g.store.execute('UPDATE agent_turns SET input_tokens=999999, output_tokens=999999 WHERE id=?', (turn,))
    cost = answer(g, turn)['turn']['cost']
    sale = g.store.one("SELECT COALESCE(SUM(amount),0) AS n FROM billing_journal WHERE kind='sale'")['n']
    assert sale > 0
    assert cost == {'charged_minor': str(sale), 'currency': 'FIXFACT', 'scale': 6,
                    'tokens': {'input_cached': 5, 'input_uncached': 8, 'output': 7, 'reasoning': 3},
                    'model': 'fixture-model', 'band': 'flat', 'state': 'settled', 'held_minor': '0',
                    'payer': {'kind': 'user', 'id': g.user['id']}}
    text = json.dumps(cost)
    for private in ('fixture-upstream', 'fixture:cost', 'fixture-route', KEY_HINT):
        assert private not in text


KEY_HINT = 'not-real-key'


def test_answer_names_the_payer_the_ledger_charged(governed):
    """Who paid is the run's ledger context, never the page's current payer choice."""
    g = governed; configure(g); fund(g)
    turn = start(g)
    g.agent._run(turn)
    assert answer(g, turn)['turn']['cost']['payer'] == {'kind': 'user', 'id': g.user['id']}


def test_organization_paid_context_names_the_organization_and_nothing_else():
    """org-payments records payer_kind="organization" and organization_id in the run context
    (ChargeContext); ledger runs are immutable, so the reading is checked on fixture contexts."""
    from synwevo_suite.agent.turn_cost import _payer
    base = {'payer_user_id': 'usr_fixture', 'actor_user_id': 'usr_fixture', 'run_id': 'turn_fixture'}
    assert _payer(base | {'payer_kind': 'personal'}) == {'kind': 'user', 'id': 'usr_fixture'}
    assert _payer(base) == {'kind': 'user', 'id': 'usr_fixture'}  # contexts before payer_kind existed
    assert _payer(base | {'payer_kind': 'organization', 'organization_id': 'org_fixture'}) == {
        'kind': 'organization', 'id': 'org_fixture'}
    # A context that claims an organization without naming it, or an unknown kind, names nobody.
    assert _payer(base | {'payer_kind': 'organization'}) is None
    assert _payer(base | {'payer_kind': 'organization', 'organization_id': ''}) is None
    assert _payer(base | {'payer_kind': 'team'}) is None


def test_unknown_usage_is_pending_with_its_hold_and_no_guessed_charge(governed):
    g = governed; configure(g); fund(g)
    g.replies = [event({'content': 'partial fixture answer'})]  # the stream ends without usage
    turn = start(g)
    g.agent._run(turn)
    cost = answer(g, turn)['turn']['cost']
    assert cost['state'] == 'pending' and cost['charged_minor'] == '0'
    assert int(cost['held_minor']) > 0
    assert cost['tokens'] == {'input_cached': 0, 'input_uncached': 0, 'output': 0, 'reasoning': 0}


def test_turn_without_a_ledger_run_has_no_cost_and_another_person_sees_none(governed):
    g = governed; configure(g); fund(g)
    turn = start(g)  # admitted, not run: the ledger has no run yet
    assert answer(g, turn)['turn']['cost'] is None
    g.agent._run(turn)
    ledger = g.provider.gateway.ledger
    mine = turn_costs(g.store, environment='test', namespace='agent', user_id=g.user['id'], turn_ids=[turn])
    assert mine[turn]['state'] == 'settled'
    other = g.suite.people.users['bob']['id']
    assert turn_costs(g.store, environment=ledger.environment, namespace=ledger.namespace,
                      user_id=other, turn_ids=[turn]) == {}
    assert turn_costs(g.store, environment='live', namespace='agent', user_id=g.user['id'], turn_ids=[turn]) == {}


def test_in_flight_call_is_held_not_pending(governed):
    g = governed; configure(g); fund(g)
    seen = {}

    async def respond(req):
        # While the request is out, the open turn reports its hold rather than unknown usage.
        seen['cost'] = answer(g, turn)['turn']['cost']
        import httpx
        from .test_agent_metering import Wire
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(complete()))
    import httpx
    g.provider.gateway.transport = httpx.MockTransport(respond)
    turn = start(g)
    g.agent._run(turn)
    assert seen['cost']['state'] == 'held' and int(seen['cost']['held_minor']) > 0
    assert answer(g, turn)['turn']['cost']['state'] == 'settled'


def test_billing_status_reports_wallet_and_tightest_limit_in_the_offer_unit(governed):
    g = governed
    assert call(g, 'agent.billing.status') == {'available': False, 'reason': 'unavailable', 'environment': None,
        'payer': None, 'model': None, 'unit': None, 'per_call_hold': None, 'balance': None, 'limit': None}
    configure(g)
    status = call(g, 'agent.billing.status')
    assert status['balance'] == {'booked': '0', 'held': '0', 'available': '0'}
    fund(g)
    status = call(g, 'agent.billing.status')
    assert status['available'] is True and status['environment'] == 'test'
    assert status['payer'] == {'kind': 'user', 'id': g.user['id']}
    assert status['unit'] == {'code': 'FIXFACT', 'scale': 6} and status['model'] == 'fixture-model'
    assert status['balance'] == {'booked': '1000000', 'held': '0', 'available': '1000000'}
    assert int(status['per_call_hold']) > 0
    assert status['limit'] == {'configured': True, 'enabled': True, 'usage_state': 'available',
                               'remaining': '1000000', 'period': 'daily'}
    turn = start(g); g.agent._run(turn)
    spent = g.store.one("SELECT SUM(amount) AS n FROM billing_journal WHERE kind='sale'")['n']
    status = call(g, 'agent.billing.status')
    assert status['balance']['available'] == str(1000000 - spent)
    assert status['limit']['remaining'] == str(1000000 - spent)


def test_billing_status_without_payer_limits_says_not_configured(governed):
    g = governed; configure(g, payer_budget=False); fund(g)
    assert call(g, 'agent.billing.status')['limit'] == {'configured': False, 'enabled': False,
        'usage_state': 'available', 'remaining': None, 'period': None}


def test_billing_status_and_cost_are_absent_without_the_paid_provider(suite):
    alice = suite.people.users['alice']['id']
    out = suite.caps.invoke('agent.billing.status', {}, user_id=alice, via='ui', idempotency_key='status-x')['output']
    assert out['available'] is False and out['reason'] == 'not_configured'


@pytest.mark.parametrize('model,expected', [('fixture-model', None), ('deepseek-flash', MODEL_CAPABILITIES['deepseek-flash']),
                                            ('deepseek-v4-pro', MODEL_CAPABILITIES['deepseek-v4-pro'])])
def test_offer_states_documented_capabilities_only_for_known_models(governed, model, expected):
    g = governed; configure(g)
    if model != g.spec['route']['requested_model']:
        publish(g, {'action': 'keep'}, {**g.spec, 'route': {**g.spec['route'], 'requested_model': model}})
    offer = call(g, 'agent.billing.offer')
    assert offer['model'] == model and offer['capabilities'] == expected
    if expected:
        assert expected['vision'] is (model == 'deepseek-flash')

