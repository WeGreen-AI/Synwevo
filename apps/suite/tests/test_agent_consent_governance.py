"""Continuing consent through the deployed composition; isolated SSE/funds fixtures only."""
import json

import pytest

from .test_agent_governed_factory import accept_terms, governed, configure, fund, publish, KEY, UNIT
from .test_agent_metering import complete


def approve(g, max_runs=2):
    quote = g.agent.quote_turn(g.user, g.conversation['id'], 'Fixture review', parent_id=None,
        regenerate_of=None, budget=5000, max_calls=3, run_seconds=300)
    return g.suite.caps.invoke('agent.consent.create', {
        'conversation_id': g.conversation['id'], 'quote_id': quote['id'], 'max_runs': max_runs,
        'expires_at': None, 'until_revoked': True,
    }, user_id=g.user['id'], idempotency_key='fixture-consent')['output']


def send(g, consent_id, key):
    return g.suite.caps.invoke('agent.turn.start', {
        'conversation_id': g.conversation['id'], 'text': 'Fixture manually sent message',
        'consent_id': consent_id,
    }, user_id=g.user['id'], idempotency_key=key)


def test_factory_consumes_two_runs_with_fixed_terms_without_duplicate_charges(governed):
    g = governed; configure(g); fund(g); accept_terms(g)
    consent = approve(g)
    assert consent['total_maximum'] == '10000'
    assert g.store.all('SELECT * FROM billing_calls') == []
    for index in range(2):
        g.replies = [complete().replace(b'fixture-upstream-1', f'fixture-upstream-{index+1}'.encode())]
        accepted = send(g, consent['id'], f'fixture-send-{index}')
        replay = send(g, consent['id'], f'fixture-send-{index}')
        assert replay['replayed'] and replay['output'] == accepted['output']
        g.agent._run(accepted['output']['turn_id'])
        assert g.store.one('SELECT status FROM agent_turns WHERE id=?',
            (accepted['output']['turn_id'],))['status'] == 'succeeded'
    current = g.provider.quotes.consents.get(g.user['id'], consent['id'])
    assert current['status'] == 'exhausted' and current['used_runs'] == 2
    assert len(g.seen) == len(g.store.all('SELECT * FROM billing_settlements')) == 2
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")
    runs = g.store.all('SELECT * FROM billing_runs')
    assert len(runs) == 2 and all(row['budget'] == 5000 for row in runs)
    for row in g.store.all('SELECT q.terms_json FROM agent_model_quotes q JOIN agent_quote_consents c ON c.quote_id=q.id'):
        assert json.loads(row['terms_json'])['sale']['price_id'] == consent['price_id']
    g.agent.recover()
    assert len(g.seen) == 2


@pytest.mark.parametrize('change', ['consent', 'payer_budget', 'credential', 'scope'])
def test_continuing_consent_does_not_bypass_current_dispatch_gates(governed, monkeypatch, change):
    g = governed; configure(g); fund(g); accept_terms(g)
    consent = approve(g)
    tid = send(g, consent['id'], 'fixture-gated-send')['output']['turn_id']
    ledger = g.provider.gateway.ledger
    original = ledger.prepare_attempt
    reached = []

    def before(*args, **kwargs):
        ticket = original(*args, **kwargs)
        reached.append(ticket)
        if change == 'consent':
            g.suite.caps.invoke('agent.consent.revoke', {'consent_id': consent['id']},
                user_id=g.user['id'], idempotency_key='fixture-revoke-before-send')
        elif change == 'payer_budget':
            g.suite.caps.invoke('agent.billing.budget.set', {
                'unit_code': UNIT.code, 'expected_revision': 1, 'enabled': False,
                'daily_budget': '1000000', 'weekly_budget': '1000000', 'monthly_budget': '1000000',
            }, user_id=g.user['id'], idempotency_key='fixture-pause-before-send')
        elif change == 'credential':
            publish(g, {'action': 'replace', 'api_key': KEY+'rotated'})
        elif change == 'scope':
            g.agent.update_conversation(g.user['id'], g.conversation['id'], title=None, scope=[],
                status=None, head_id=None)
        return ticket

    monkeypatch.setattr(ledger, 'prepare_attempt', before)
    g.agent._run(tid)
    assert len(reached) == 1  # The real composed ledger reached dispatch after reservation.
    assert g.seen == [] and g.store.all('SELECT * FROM billing_settlements') == []
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] == 'failed'
    assert g.provider.quotes.consents.get(g.user['id'], consent['id'])['used_runs'] == 1
