"""Continuing model-spending consent: bounds, ownership, current-state checks and history.

Fixture prices, funds, routes and SSE only (see test_agent_governed_factory); no real provider,
payment service or network is contacted.
"""
import json
import sqlite3
import threading
import time
import uuid

import pytest

from synwevo_core.errors import ServiceError
from .test_agent_governed_factory import accept_terms, governed, configure, fund, publish
from .test_agent_metering import complete


@pytest.fixture(autouse=True)
def started(governed):
    """FIXTURE: every person here has done the Agent's one-time start step; a consent spent from
    the page needs the current terms accepted beforehand (test_agent_automatic_runs.py, AD-4)."""
    accept_terms(governed)


def quote(g, conversation=None, *, user=None, budget=5000, run_seconds=300):
    return g.agent.quote_turn(user or g.user, (conversation or g.conversation)['id'], 'Fixture review',
        parent_id=None, regenerate_of=None, budget=budget, max_calls=3, run_seconds=run_seconds)


def create(g, quote_id, *, user=None, conversation=None, max_runs=2, expires_at=None, until_revoked=True,
           via='ui'):
    return g.suite.caps.invoke('agent.consent.create', {
        'conversation_id': (conversation or g.conversation)['id'], 'quote_id': quote_id, 'max_runs': max_runs,
        'expires_at': expires_at, 'until_revoked': until_revoked,
    }, user_id=(user or g.user)['id'], via=via, idempotency_key=uuid.uuid4().hex)['output']


def call(g, operation, payload, *, user=None, via='ui'):
    return g.suite.caps.invoke(operation, payload, user_id=(user or g.user)['id'], via=via,
                               idempotency_key=uuid.uuid4().hex)['output']


def send(g, consent_id, *, conversation=None, via='ui', quote_id=None):
    payload = {'conversation_id': (conversation or g.conversation)['id'], 'text': 'Fixture manually sent message',
               'consent_id': consent_id}
    if quote_id:
        payload['quote_id'] = quote_id
    return call(g, 'agent.turn.start', payload, via=via)


def refused(fn, *args, **kwargs):
    with pytest.raises(ServiceError) as err:
        fn(*args, **kwargs)
    return err.value.status, err.value.code


def messages(g, conversation=None):
    return g.store.one('SELECT COUNT(*) AS n FROM agent_messages WHERE conversation_id=?',
                       ((conversation or g.conversation)['id'],))['n']


def finish(g, turn_id, index=0):
    g.replies = [complete().replace(b'fixture-upstream-1', f'fixture-upstream-c{index}'.encode())]
    g.agent._run(turn_id)
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (turn_id,))['status'] == 'succeeded'


def test_create_requires_one_bounded_lifetime_and_leaves_the_quote_on_refusal(governed):
    g = governed; configure(g)
    q = quote(g)
    now = int(time.time())
    assert refused(create, g, q['id'], expires_at=now+3600, until_revoked=True) == (409, 'billing_consent_expiry_required')
    assert refused(create, g, q['id'], expires_at=None, until_revoked=False) == (409, 'billing_consent_expiry_required')
    assert refused(create, g, q['id'], expires_at=now-1, until_revoked=False) == (409, 'billing_consent_invalid_expiry')
    assert refused(create, g, q['id'], expires_at=now+367*86400, until_revoked=False) == (409, 'billing_consent_invalid_expiry')
    for bad in (0, 1001, '2', True):
        assert refused(create, g, q['id'], max_runs=bad) == (422, 'invalid_input')
    assert g.store.one('SELECT revoked_at FROM agent_model_quotes WHERE id=?', (q['id'],))['revoked_at'] is None
    assert g.store.all('SELECT * FROM agent_model_consents') == []

    consent = create(g, q['id'], max_runs=3, expires_at=now+3600, until_revoked=False)
    assert consent['status'] == 'active' and consent['until_revoked'] is False
    assert consent['expires_at'] == now+3600 and consent['max_runs'] == consent['remaining_runs'] == 3
    assert consent['budget'] == '5000' and consent['total_maximum'] == '15000' and consent['max_calls'] == 3
    assert consent['run_seconds'] == 300 and consent['scope'] == [g.wid] and consent['used_runs'] == 0
    # No funds or ledger rows are touched by agreeing to future runs.
    assert g.store.all('SELECT * FROM billing_calls') == g.store.all('SELECT * FROM billing_journal') == []


def test_total_maximum_must_fit_and_global_discovery_needs_an_explicit_scope(governed):
    g = governed; configure(g)
    large = quote(g, budget=2**62)
    assert refused(create, g, large['id'], max_runs=2) == (409, 'billing_consent_total_too_large')
    assert create(g, large['id'], max_runs=1)['total_maximum'] == str(2**62)

    discover = g.agent.create_conversation(g.user['id'], 'Discover all', None)
    single = quote(g, discover)  # A one-run quote with global discovery stays available.
    assert refused(create, g, single['id'], conversation=discover) == (409, 'billing_consent_explicit_scope_required')
    personal = g.agent.create_conversation(g.user['id'], 'Personal only', [])
    assert create(g, quote(g, personal)['id'], conversation=personal)['scope'] == []


def test_basis_quote_is_retired_and_cannot_be_combined_or_reused(governed):
    g = governed; configure(g)
    q = quote(g)
    consent = create(g, q['id'])
    assert g.store.one('SELECT revoked_at FROM agent_model_quotes WHERE id=?', (q['id'],))['revoked_at'] is not None
    before = messages(g)
    status, code = refused(call, g, 'agent.turn.start', {'conversation_id': g.conversation['id'],
        'text': 'Fixture manually sent message', 'quote_id': q['id']})
    assert (status, code) == (409, 'billing_quote_expired_or_revoked')
    assert refused(send, g, consent['id'], quote_id=quote(g)['id']) == (409, 'billing_authorization_conflict')
    assert refused(create, g, q['id']) == (409, 'billing_quote_expired_or_revoked')
    assert messages(g) == before  # Refused sends leave no message behind.
    assert g.provider.quotes.consents.get(g.user['id'], consent['id'])['used_runs'] == 0


def test_one_active_consent_per_conversation_and_review_after_revoke(governed):
    g = governed; configure(g)
    first = create(g, quote(g)['id'])
    second_quote = quote(g)
    assert refused(create, g, second_quote['id']) == (409, 'billing_consent_already_exists')
    assert g.store.one('SELECT revoked_at FROM agent_model_quotes WHERE id=?', (second_quote['id'],))['revoked_at'] is None

    revoked = call(g, 'agent.consent.revoke', {'consent_id': first['id']})
    again = call(g, 'agent.consent.revoke', {'consent_id': first['id']})
    assert revoked['status'] == again['status'] == 'revoked'
    events = g.store.all("SELECT type FROM app_events WHERE type LIKE 'agent.consent.%' ORDER BY seq")
    assert [e['type'] for e in events] == ['agent.consent.created', 'agent.consent.revoked']
    assert refused(send, g, first['id']) == (409, 'billing_consent_revoked')

    replacement = create(g, second_quote['id'], max_runs=1)
    listed = call(g, 'agent.consent.list', {'conversation_id': g.conversation['id']})['consents']
    assert [(c['id'], c['status']) for c in listed] == [(replacement['id'], 'active'), (first['id'], 'revoked')]


def test_active_consents_per_person_are_bounded(governed):
    g = governed; configure(g)
    now = int(time.time())
    basis = quote(g)
    terms = g.store.one('SELECT terms_json FROM agent_model_quotes WHERE id=?', (basis['id'],))['terms_json']
    with g.store.tx() as t:
        for index in range(100):
            t.execute('INSERT INTO agent_model_consents (id,environment,namespace,user_id,conversation_id,'
                'basis_quote_id,scope_json,terms_json,max_runs,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (f'consent_fixture{index:05d}', 'test', 'agent', g.user['id'], f'conv_fixture{index:05d}',
                 basis['id'], '[]', terms, 1, now, None))
    assert refused(create, g, basis['id']) == (409, 'billing_consent_limit_exceeded')


def test_consents_belong_to_one_person_and_one_conversation(governed):
    g = governed; configure(g)
    bob = g.suite.people.users['bob']
    consent = create(g, quote(g)['id'])
    for operation in ('agent.consent.get', 'agent.consent.revoke'):
        assert refused(call, g, operation, {'consent_id': consent['id']}, user=bob) == (404, 'billing_consent_not_found')
    bobs = g.agent.create_conversation(bob['id'], 'Bob fixture', [])
    assert refused(create, g, quote(g)['id'], user=bob, conversation=bobs) == (404, 'billing_quote_not_found')
    assert refused(call, g, 'agent.consent.list', {'conversation_id': g.conversation['id']}, user=bob)[0] == 404

    other = g.agent.create_conversation(g.user['id'], 'Second fixture', [g.wid])
    assert refused(create, g, quote(g)['id'], conversation=other) == (404, 'quote_not_found')
    assert refused(send, g, consent['id'], conversation=other) == (409, 'billing_consent_not_found')
    assert messages(g, other) == 0
    current = g.provider.quotes.consents.get(g.user['id'], consent['id'])
    assert current['status'] == 'active' and current['used_runs'] == 0


@pytest.mark.parametrize('change,status,refusal', [
    ('scope', 'scope_changed', (409, 'billing_consent_scope_changed')),
    ('route', 'model_changed', (409, 'billing_consent_model_changed')),
    ('credential', 'model_changed', (409, 'billing_consent_model_changed')),
    ('archive', 'conversation_unavailable', (409, 'conversation_archived')),
    ('expiry', 'expired', (409, 'billing_consent_expired')),
])
def test_current_state_changes_never_carry_the_consent_forward(governed, change, status, refusal):
    g = governed; configure(g)
    ledger = g.provider.gateway.ledger
    now = int(time.time())
    consent = create(g, quote(g)['id'], expires_at=now+600, until_revoked=False)
    if change == 'scope':
        g.agent.update_conversation(g.user['id'], g.conversation['id'], title=None, scope=[], status=None, head_id=None)
    elif change == 'route':
        publish(g, {'action': 'keep'}, {**g.spec, 'route': {**g.spec['route'], 'output_token_ceiling': 32}})
    elif change == 'credential':
        publish(g, {'action': 'replace', 'api_key': 'fixture-governed-provider-rotated'})
    elif change == 'archive':
        g.agent.update_conversation(g.user['id'], g.conversation['id'], title=None, scope=..., status='archived', head_id=None)
    else:
        ledger.clock = lambda: now + 601
    assert g.provider.quotes.consents.get(g.user['id'], consent['id'])['status'] == status
    before = messages(g)
    assert refused(send, g, consent['id']) == refusal
    assert messages(g) == before and g.store.all('SELECT * FROM agent_quote_consents') == []


def test_deleted_conversation_leaves_an_unusable_history(governed):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'])
    g.agent.delete_conversation(g.user['id'], g.conversation['id'])
    assert g.provider.quotes.consents.get(g.user['id'], consent['id'])['status'] == 'conversation_unavailable'
    assert refused(send, g, consent['id'])[0] == 404


def test_used_up_or_revoked_consent_refuses_the_next_send(governed):
    g = governed; configure(g); fund(g)
    consent = create(g, quote(g)['id'], max_runs=1)
    finish(g, send(g, consent['id'])['turn_id'])
    before = messages(g)
    assert refused(send, g, consent['id']) == (409, 'billing_consent_exhausted')
    assert messages(g) == before
    current = g.provider.quotes.consents.get(g.user['id'], consent['id'])
    assert (current['status'], current['used_runs'], current['remaining_runs']) == ('exhausted', 1, 0)
    assert call(g, 'agent.consent.revoke', {'consent_id': consent['id']})['status'] == 'revoked'

    other = g.agent.create_conversation(g.user['id'], 'Revoked fixture', [g.wid])
    revoked = create(g, quote(g, other)['id'], conversation=other, max_runs=5)
    call(g, 'agent.consent.revoke', {'consent_id': revoked['id']})
    assert refused(send, g, revoked['id'], conversation=other) == (409, 'billing_consent_revoked')


def test_each_run_is_bounded_by_the_consent_and_route_lifetimes(governed):
    g = governed; configure(g)
    ledger = g.provider.gateway.ledger
    now = int(time.time())
    ledger.clock = lambda: now
    # A run is bounded by the consent's remaining lifetime, which must still fit one whole
    # model call (gateway.send_window): usage after a hold's deadline is never charged.
    window = g.provider.gateway.send_window
    consent = create(g, quote(g)['id'], max_runs=3, expires_at=now+window+10, until_revoked=False)
    assert consent['run_seconds'] == 300
    turn = send(g, consent['id'])['turn_id']
    issued = g.store.one('SELECT q.terms_json FROM agent_model_quotes q JOIN agent_quote_consents c '
                         'ON c.quote_id=q.id WHERE q.accepted_turn_id=?', (turn,))
    assert json.loads(issued['terms_json'])['deadline'] == now+window+10

    other = g.agent.create_conversation(g.user['id'], 'Route window fixture', [g.wid])
    long_lived = create(g, quote(g, other)['id'], conversation=other)
    valid_until = g.spec['route']['valid_until']
    ledger.clock = lambda: valid_until - window - 10
    turn = send(g, long_lived['id'], conversation=other)['turn_id']
    issued = g.store.one('SELECT terms_json FROM agent_model_quotes WHERE accepted_turn_id=?', (turn,))
    assert json.loads(issued['terms_json'])['deadline'] == valid_until


def test_consent_history_and_run_bindings_are_immutable(governed):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'])
    send(g, consent['id'])
    cid = consent['id']
    for sql in ("UPDATE agent_model_consents SET max_runs=1000 WHERE id=?",
                "UPDATE agent_model_consents SET terms_json='{}' WHERE id=?",
                "UPDATE agent_model_consents SET scope_json='[]' WHERE id=?",
                "UPDATE agent_model_consents SET expires_at=1 WHERE id=?",
                "DELETE FROM agent_model_consents WHERE id=?",
                "INSERT OR REPLACE INTO agent_model_consents SELECT * FROM agent_model_consents WHERE id=?",
                "UPDATE agent_quote_consents SET ordinal=7 WHERE consent_id=?",
                "DELETE FROM agent_quote_consents WHERE consent_id=?",
                "INSERT OR REPLACE INTO agent_quote_consents SELECT * FROM agent_quote_consents WHERE consent_id=?"):
        with pytest.raises(sqlite3.DatabaseError, match='immutable_model_consent'):
            g.store.execute(sql, (cid,))
    call(g, 'agent.consent.revoke', {'consent_id': cid})
    for sql in ("UPDATE agent_model_consents SET revoked_at=NULL WHERE id=?",
                "UPDATE agent_model_consents SET revoked_at=revoked_at+1 WHERE id=?"):
        with pytest.raises(sqlite3.DatabaseError, match='immutable_model_consent'):
            g.store.execute(sql, (cid,))
    current = g.provider.quotes.consents.get(g.user['id'], cid)
    assert (current['status'], current['max_runs'], current['used_runs']) == ('revoked', 2, 1)


def test_concurrent_sends_accept_exactly_the_allowed_runs(governed):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'], max_runs=1)
    gate = threading.Barrier(4)
    accepted, refusals = [], []

    def attempt():
        gate.wait()
        try:
            accepted.append(send(g, consent['id']))
        except ServiceError as exc:
            refusals.append((exc.status, exc.code))

    workers = [threading.Thread(target=attempt) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert len(accepted) == 1 and len(refusals) == 3
    assert {r for r in refusals} <= {(409, 'turn_running'), (409, 'billing_consent_exhausted')}
    assert g.store.one('SELECT COUNT(*) AS n FROM agent_quote_consents')['n'] == 1
    assert g.store.one('SELECT COUNT(*) AS n FROM agent_turns')['n'] == 1


@pytest.mark.parametrize('via,refusal', [
    ('agent', (403, 'not_for_agent')),
    ('flow', (403, 'person_required')),
    ('automation', (403, 'person_required')),
    ('system', (403, 'person_required')),
    ('api', (403, 'person_required')),
])
def test_only_the_person_can_grant_read_revoke_or_spend_a_consent(governed, via, refusal):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'])
    assert refused(create, g, quote(g)['id'], via=via) == refusal
    assert refused(call, g, 'agent.consent.get', {'consent_id': consent['id']}, via=via) == refusal
    assert refused(call, g, 'agent.consent.list', {'conversation_id': g.conversation['id']}, via=via) == refusal
    assert refused(call, g, 'agent.consent.revoke', {'consent_id': consent['id']}, via=via) == refusal
    assert refused(send, g, consent['id'], via=via) == refusal
    current = g.provider.quotes.consents.get(g.user['id'], consent['id'])
    assert (current['status'], current['used_runs']) == ('active', 0)
    assert g.store.all('SELECT * FROM agent_turns') == []
    assert send(g, consent['id'])['turn_id']  # The same consent still works for the person in the page.


def test_http_surface_is_csrf_bound_and_hides_other_peoples_consents(governed):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'])
    c = g.suite
    header = c.sign_in('alice')
    path = '/api/suite/ops/agent.consent.get'
    body = {'input': {'consent_id': consent['id']}}
    assert c.post(path, json=body).status_code == 403
    r = c.post(path, json=body, headers=header)
    assert r.status_code == 200 and r.json()['output']['status'] == 'active'
    bad = c.post('/api/suite/ops/agent.consent.create', headers=header, json={'input': {
        'conversation_id': g.conversation['id'], 'quote_id': quote(g)['id'], 'max_runs': 1001,
        'expires_at': None, 'until_revoked': True}})
    assert bad.status_code == 422
    header = c.sign_in('bob')
    for operation in ('agent.consent.get', 'agent.consent.revoke'):
        r = c.post(f'/api/suite/ops/{operation}', json=body, headers=header)
        assert r.status_code == 404 and consent['id'] not in r.text


def test_audit_trail_names_ids_and_never_message_or_terms(governed):
    g = governed; configure(g)
    consent = create(g, quote(g)['id'])
    call(g, 'agent.consent.revoke', {'consent_id': consent['id']})
    rows = g.store.all("SELECT type,data FROM app_events WHERE type LIKE 'agent.consent.%' ORDER BY seq")
    assert [r['type'] for r in rows] == ['agent.consent.created', 'agent.consent.revoked']
    for row in rows:
        assert row['data'] == {'consent_id': consent['id'], 'conversation_id': g.conversation['id']}
    for run in g.store.all("SELECT input FROM op_runs WHERE op LIKE 'agent.consent.%'"):
        assert 'Fixture review' not in run['input']
