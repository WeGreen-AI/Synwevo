"""Actual Suite factory and governed DeepSeek wiring; synthetic prices/funds and SSE only.

No provider injection replaces the factory, and no network/payment service is contacted.
The transport fixture is installed only after the real factory assembled every gate.
"""
from dataclasses import asdict
import json
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

from synwevo_core.billing import Ledger, PaymentReceipt, Unit
from synwevo_core.billing.catalogue import PriceCatalogue
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.provider_settings import ProviderSettings
from synwevo_core.errors import ServiceError
from synwevo_suite.agent.configuration import GovernedDeepSeek, configured_provider
from .test_agent_metering import Wire, complete, event, USAGE

KEY = 'fixture-governed-provider-not-real-key'
UNIT = Unit('FIXFACT', 6)


@pytest.fixture
def governed(request, monkeypatch):
    monkeypatch.setenv('SYNWEVO_AGENT_PROVIDER', 'deepseek')
    monkeypatch.setenv('SYNWEVO_BILLING_ENVIRONMENT', 'test')
    # These must never become the DeepSeek credential, including when governance is absent.
    monkeypatch.setenv('SYNWEVO_AGENT_ANTHROPIC_API_KEY', 'fixture-wrong-provider')
    monkeypatch.setenv('SYNWEVO_AGENT_MODEL', 'fixture-wrong-model')
    async def no_network(*args, **kwargs):
        pytest.fail('Factory regression attempted real network access')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    suite = request.getfixturevalue('suite')
    agent = suite.api.state.agent
    monkeypatch.setattr(agent, '_submit', lambda turn: None)
    assert isinstance(agent.provider, GovernedDeepSeek)
    assert agent.provider.agent is agent
    g = SimpleNamespace(suite=suite, agent=agent, provider=agent.provider, store=agent.store,
                        user=suite.people.users['alice'], seen=[], replies=[complete()])
    g.wid = next(w['id'] for w in suite.people.ident.workspaces_for(g.user['id']) if w['kind'] == 'personal')
    async def respond(req):
        g.seen.append(req)
        assert req.headers['authorization'] == 'Bearer ' + KEY
        assert g.store.one("SELECT COUNT(*) AS n FROM billing_attempts WHERE status='dispatch_unknown'")['n'] == 1
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(g.replies.pop(0)))
    g.provider.gateway.transport = httpx.MockTransport(respond)
    g.settings = ProviderSettings(g.store, environment='test')
    return g


def configure(g):
    cat = PriceCatalogue(g.store, environment='test')
    ids = {}
    with g.store.tx():
        for purpose in ('sale', 'cost'):
            draft, _ = cat.create(spec={'name': 'Factory fixture', 'purpose': purpose, 'provider': 'deepseek',
                'model': 'fixture-route', 'unit': asdict(UNIT), 'input_uncached': '1', 'input_cached': '0.1',
                'output': '2', 'terms_ref': 'fixture:factory', 'effective_from': 0},
                actor_id=g.user['id'], idempotency_key=uuid.uuid4().hex)
            published = cat.publish(draft['id'], expected_revision=1, actor_id=g.user['id'],
                operation_id=uuid.uuid4().hex, reason='Fixture price publication')
            ids[purpose] = published['price_id']
        CostSettings(g.store, environment='test').append({'enabled': True, 'unit': asdict(UNIT),
            'daily_budget': '1000000', 'weekly_budget': '1000000', 'monthly_budget': '1000000',
            'unknown_budget': '1000000', 'max_inflight': 4, 'max_unknown': 4},
            expected_revision=0, actor_id=g.user['id'], reason='Fixture bounded costs', operation_id=uuid.uuid4().hex)
        g.spec = {'enabled': True, 'route': {'requested_model': 'fixture-model', 'confirmed_route': 'fixture-route',
            'confirmation_ref': 'fixture:route', 'account_confirmation_ref': 'fixture:account',
            'cost_basis_ref': 'fixture:cost', 'sale_price_id': ids['sale'], 'cost_price_id': ids['cost'],
            'input_token_ceiling': 300, 'output_token_ceiling': 64, 'valid_until': int(time.time())+3600}}
        publish(g, {'action': 'replace', 'api_key': KEY})
    g.conversation = g.agent.create_conversation(g.user['id'], 'Factory integration fixture', [g.wid])


def publish(g, credential, spec=None):
    with g.store.tx():
        return g.settings.append(spec or g.spec, credential, expected_revision=g.settings.read()['revision'],
            actor_id=g.user['id'], reason='Fixture provider governance', operation_id=uuid.uuid4().hex)


def fund(g):
    # Explicit trusted fixture verifier, never installed on the production factory ledger.
    receipt = PaymentReceipt('test', 'fixture-payment', 'fixture-merchant', 'fixture-transaction', 'fixture-event',
        'fixture-order', 1000, 'XTS', 'fixture-inbox', 'a'*64)
    fixture_ledger = Ledger(g.store, environment='test', namespace='agent', verify_payment=lambda t, r: r == receipt)
    fixture_ledger.create_payment_order(order_id='fixture-order', payer_user_id=g.user['id'],
        provider='fixture-payment', merchant_id='fixture-merchant', amount_minor=1000, currency='XTS',
        unit=UNIT, credit_units=1000000, exchange_version='fixture-exchange')
    fixture_ledger.credit_verified_payment(receipt)


def start(g):
    q = g.agent.quote_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None, regenerate_of=None,
                          budget=5000, max_calls=3, run_seconds=300)
    return g.agent.start_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None,
                             regenerate_of=None, quote_id=q['id'])['turn_id']


def test_explicit_factory_is_closed_without_governance_and_never_borrows_anthropic(governed):
    g = governed
    assert g.agent._credential(g.user['id']) is None
    assert g.provider.route() is None
    assert g.suite.api.state.agent_metering_recovery.gateway is g.provider.gateway
    for table in ('billing_prices', 'billing_journal', 'model_provider_versions', 'model_provider_credentials'):
        assert g.store.all('SELECT * FROM '+table) == []
    assert g.seen == []


def test_configured_factory_requires_real_quote_and_balance_before_transport(governed):
    g = governed; configure(g)
    with pytest.raises(ServiceError) as err:
        g.agent.start_turn(g.user, g.conversation['id'], 'No consent', parent_id=None, regenerate_of=None)
    assert err.value.code == 'billing_authorization_required'
    tid = start(g); g.agent._run(tid)
    assert g.store.one('SELECT error_code FROM agent_turns WHERE id=?', (tid,))['error_code'] == 'billing_insufficient_funds'
    assert g.store.all('SELECT * FROM billing_attempts') == [] and g.seen == []


def test_real_factory_reads_real_forms_and_settles_each_model_step_once(governed):
    g = governed; configure(g); fund(g)
    created = g.suite.caps.invoke('forms.form.create', {'title': 'Factory business form'}, user_id=g.user['id'],
                                 workspace_id=g.wid, idempotency_key='factory-form-create')
    assert created['status'] == 'succeeded'
    arguments = {'operation': 'forms.form.list', 'workspace_id': g.wid, 'input': {}}
    g.replies = [event({'tool_calls': [{'index': 0, 'id': 'fixture-tool', 'type': 'function',
        'function': {'name': 'run_operation', 'arguments': json.dumps(arguments)}}]}) +
        event(finish='tool_calls', usage=USAGE)+b'data: [DONE]\n\n', complete().replace(b'fixture-upstream-1', b'fixture-upstream-2')]
    tid = start(g); g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    assert turn['status'] == 'succeeded', turn['error_code']
    assert len(g.seen) == 2
    messages = json.loads(g.seen[1].content)['messages']
    tool = next(m for m in messages if m['role'] == 'tool')
    assert 'Factory business form' in tool['content']
    assert len(g.store.all('SELECT * FROM billing_settlements')) == 2
    assert len(g.store.all('SELECT * FROM model_cost_admissions')) == 2
    public = g.agent.conversation_json(g.user['id'], g.agent.conversation(g.user['id'], g.conversation['id']), full=True)
    assert KEY not in json.dumps(public) and 'credential_binding' not in json.dumps(public)
    g.agent.recover()
    assert len(g.seen) == 2 and len(g.store.all('SELECT * FROM billing_settlements')) == 2


@pytest.mark.parametrize('change', ['rotate', 'disable', 'cancel', 'user', 'application', 'costs', 'archive'])
def test_actual_send_rechecks_governance_after_reservation(governed, monkeypatch, change):
    g = governed; configure(g); fund(g); tid = start(g)
    ledger = g.provider.gateway.ledger
    original = ledger.prepare_attempt
    def before(*args, **kwargs):
        ticket = original(*args, **kwargs)
        if change == 'rotate': publish(g, {'action': 'replace', 'api_key': KEY+'rotated'})
        elif change == 'disable': publish(g, {'action': 'keep'}, {**g.spec, 'enabled': False})
        elif change == 'cancel': g.store.execute('UPDATE agent_turns SET cancel_requested=1 WHERE id=?', (tid,))
        elif change == 'user': g.store.execute("UPDATE users SET status='suspended' WHERE id=?", (g.user['id'],))
        elif change == 'application': g.store.execute("UPDATE apps SET status='disabled' WHERE id='agent'")
        elif change == 'archive': g.store.execute("UPDATE agent_conversations SET status='archived' WHERE id=?", (g.conversation['id'],))
        elif change == 'costs':
            costs = CostSettings(g.store, environment='test'); state = costs.read()
            with g.store.tx():
                costs.append({**state['spec'], 'enabled': False}, expected_revision=state['revision'],
                    actor_id=g.user['id'], reason='Fixture stop costs', operation_id=uuid.uuid4().hex)
        return ticket
    monkeypatch.setattr(ledger, 'prepare_attempt', before)
    g.agent._run(tid)
    assert g.seen == []
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] in ('failed', 'cancelled')
    assert g.store.all('SELECT * FROM billing_settlements') == []
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


def test_factory_rejects_unknown_selection_instead_of_falling_back(governed, monkeypatch):
    monkeypatch.setenv('SYNWEVO_AGENT_PROVIDER', 'unsupported')
    with pytest.raises(ValueError, match='unsupported'): configured_provider(governed.store)
