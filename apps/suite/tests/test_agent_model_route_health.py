"""Governed model-id checks and the upstream-402 closure on the real Suite factory.

FIXTURE transports only (httpx.MockTransport); synthetic prices, funds and keys. No real
DeepSeek endpoint is contacted and no real key exists anywhere in these tests.
"""
import json
import sys
import time
import types

import httpx
import pytest

from synwevo_core.notifications import CATALOGUE
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.store import Store
from synwevo_suite.agent.deepseek import BASE_URL, DeepSeekError, list_models
from synwevo_suite.agent.gateway import UNAVAILABLE, _open_platform_breaker
from synwevo_suite.agent.model_check import ModelCheck
from synwevo_suite.agent.provider import Credential

from .test_agent_governed_factory import KEY, UNIT, configure, fund, governed, publish, start  # noqa: F401 - fixture import
from .test_agent_metering import Wire, complete

FIXTURE_KEY = 'fixture-models-key-not-real'


def body(status, content, content_type='application/json'):
    # A streamed body, like the network's (httpx pre-reads json=/content= responses).
    return httpx.Response(status, headers={'content-type': content_type}, stream=Wire(content))


def listing(*ids):
    return body(200, json.dumps({'object': 'list', 'data': [
        {'id': i, 'object': 'model', 'owned_by': 'fixture'} for i in ids]}).encode())


def models_transport(respond, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        assert request.method == 'GET' and request.url.path == '/models'
        return respond(request)
    return httpx.MockTransport(handle)


def admin(g):
    """A fixture platform administrator who can reopen a route (platform.config.manage)."""
    bob = g.suite.people.users['bob']
    g.store.execute('UPDATE users SET email_verified_at=? WHERE id=?', (time.time(), bob['id']))
    PlatformAccess(g.store).host_grant(bob['id'], 'super_admin', source='fixture:tests', reason='test fixture: platform role')
    return bob['id']


def check(g, respond, seen=None):
    return ModelCheck(g.provider.settings, base_url=g.provider.base_url,
                      transport=lambda: models_transport(respond, seen)).run_once()


def balance(g):
    uid = g.user['id']
    return g.provider.gateway.ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=UNIT)


def alerts(g, uid):
    return g.store.all('SELECT kind,params,target,object_id FROM notifications WHERE user_id=? ORDER BY created_at', (uid,))


# ---------------------------------------------------------------- GET /models transport

def test_models_listing_sends_the_key_only_to_the_official_origin_and_maps_errors():
    seen = []
    ids = list_models(Credential('platform', FIXTURE_KEY, 'fixture-model', provider='deepseek'),
                      transport=models_transport(lambda r: listing('fixture-a', 'fixture-b'), seen))
    assert ids == frozenset({'fixture-a', 'fixture-b'})
    assert str(seen[0].url) == BASE_URL + '/models'
    assert seen[0].headers['authorization'] == 'Bearer ' + FIXTURE_KEY
    credential = Credential('platform', FIXTURE_KEY, 'fixture-model', provider='deepseek')
    for status, code in ((401, 'provider_auth'), (402, 'provider_balance'), (429, 'provider_rate_limited'),
                         (503, 'provider_overloaded'), (302, 'provider_redirect')):
        with pytest.raises(DeepSeekError) as err:
            list_models(credential, transport=models_transport(lambda r: httpx.Response(status, json={'error': {}})))
        assert err.value.code == code
    for content in (b'not json', b'{"data": [{"id": "has space"}]}', b'{"data": {}}'):
        with pytest.raises(DeepSeekError) as err:
            list_models(credential, transport=models_transport(lambda r: body(200, content)))
        assert err.value.code == 'provider_bad_output'
    # The key is never sent anywhere but the official origin or a loopback FIXTURE double.
    for base in ('https://api.deepseek.com.evil.test', 'http://example.test:8080', 'http://127.0.0.1.nip.io:9'):
        with pytest.raises(ValueError):
            list_models(credential, transport=models_transport(lambda r: listing()), base_url=base)


# ---------------------------------------------------------------- model id check (item 3)

def test_missing_model_id_closes_the_route_alerts_admins_and_refuses_unsent_until_republished(governed):
    g = governed; configure(g); fund(g); boss = admin(g)
    assert check(g, lambda r: listing('fixture-model', 'other'))['state'] == 'model_listed'
    assert g.provider.settings.status()['health'] == {'closure': None, 'last_models_check': {
        'state': 'model_listed', 'at': g.store.one('SELECT created_at FROM model_provider_health')['created_at']}}
    assert alerts(g, boss) == []

    observed = check(g, lambda r: listing('other-model'))
    assert observed['state'] == 'model_missing' and observed['closure']['state'] == 'model_missing'
    audit = g.store.all("SELECT * FROM audit_events WHERE action='admin.model_provider.closed'")
    assert len(audit) == 1 and json.loads(audit[0]['meta'])['state'] == 'model_missing'
    [alert] = alerts(g, boss)
    assert alert['kind'] == 'billing.model.route_missing' and json.loads(alert['params'])['model'] == 'fixture-model'
    assert KEY not in json.dumps([audit, alert, g.store.all('SELECT * FROM model_provider_health')])

    # A person's next turn is refused before any reservation or send, and is not charged.
    tid = start(g); g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    assert turn['status'] == 'failed' and turn['error_code'] == 'billing_provider_model_missing'
    assert turn['usage_state'] == 'known'
    assert g.seen == [] and g.store.all('SELECT * FROM billing_calls') == []

    # Sticky: a later listing does not silently reopen it (an id can be reused for another model).
    check(g, lambda r: listing('fixture-model'))
    assert g.provider.settings.closure()['state'] == 'model_missing'
    assert len(alerts(g, boss)) == 1  # One closure, one alert.

    # An administrator reopens it by publishing (the same settings may be published again).
    publish(g, {'action': 'keep'})
    assert g.provider.settings.closure() is None
    tid = start(g); g.agent._run(tid)
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] == 'succeeded'
    # An identical publication is refused again while the route is open.
    with pytest.raises(Exception) as err:
        publish(g, {'action': 'keep'})
    assert getattr(err.value, 'code', None) == 'model_no_change'


def test_a_failed_check_closes_nothing_and_a_stale_revision_is_ignored(governed):
    g = governed; configure(g); fund(g)
    observed = check(g, lambda r: httpx.Response(500, json={'error': {'message': 'fixture'}}))
    assert observed['state'] == 'models_check_failed' and observed['reason_code'] == 'provider_error'
    assert observed['closure'] is None
    # An observation of an older revision describes a route no longer served.
    revision = g.provider.settings.read()['revision']
    publish(g, {'action': 'keep'}, {**g.spec, 'route': {**g.spec['route'], 'output_token_ceiling': 63}})
    assert g.provider.settings.record_health(state='model_missing', requested_model='fixture-model',
                                             reason_code='models_listing', revision=revision) is None
    assert g.provider.settings.closure() is None
    tid = start(g); g.agent._run(tid)
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] == 'succeeded'


def test_a_closure_committed_after_reservation_stops_the_send(governed, monkeypatch):
    g = governed; configure(g); fund(g); tid = start(g)
    ledger = g.provider.gateway.ledger
    original = ledger.prepare_attempt
    def before(*args, **kwargs):
        ticket = original(*args, **kwargs)
        g.provider.settings.record_health(state='model_missing', requested_model='fixture-model',
                                          reason_code='models_listing')
        return ticket
    monkeypatch.setattr(ledger, 'prepare_attempt', before)
    g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    assert g.seen == [] and turn['status'] == 'failed' and turn['error_code'] == 'billing_provider_model_missing'
    assert g.store.all('SELECT * FROM billing_settlements') == []
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


def test_the_worker_checks_a_new_revision_promptly_and_otherwise_waits_for_its_interval(governed):
    g = governed; configure(g)
    ticks, seen = [1000.0], []
    worker = ModelCheck(g.provider.settings, base_url=g.provider.base_url, interval_seconds=3600,
                        transport=lambda: models_transport(lambda r: listing('fixture-model'), seen), clock=lambda: ticks[0])
    assert worker.due()
    worker.run_once()
    assert not worker.due()
    ticks[0] += 3599
    assert not worker.due()
    publish(g, {'action': 'keep'}, {**g.spec, 'route': {**g.spec['route'], 'output_token_ceiling': 63}})
    assert worker.due()  # A new revision is checked at the next poll, not an hour later.
    worker.run_once()
    ticks[0] += 3600
    assert worker.due() and len(seen) == 2
    assert g.provider.model_check().interval_seconds == 3600
    with pytest.raises(ValueError):
        ModelCheck(g.provider.settings, base_url=BASE_URL, interval_seconds=5)


def test_health_schema_is_additive_and_append_only(tmp_path):
    store = Store(tmp_path / 'health-schema')
    store.execute('DROP TABLE model_provider_health')  # An existing database from before this table.
    store = Store(tmp_path / 'health-schema')
    store.execute('INSERT INTO model_provider_health(environment,seq,revision,state,requested_model,reason_code,created_at) '
                  "VALUES ('test',1,1,'model_missing','fixture-model','models_listing',1)")
    for statement in ("UPDATE model_provider_health SET state='model_listed'", 'DELETE FROM model_provider_health'):
        with pytest.raises(Exception, match='immutable_model_provider_health'):
            store.execute(statement)
    with pytest.raises(Exception):
        store.execute('INSERT INTO model_provider_health(environment,seq,revision,state,requested_model,reason_code,created_at) '
                      "VALUES ('test',2,1,'reopened','fixture-model','x',1)")


# ---------------------------------------------------------------- upstream 402 (item 4)

def test_upstream_402_closes_paid_dispatch_says_unavailable_and_charges_nothing(governed):
    g = governed; configure(g); fund(g); boss = admin(g)
    gateway = g.provider.gateway
    async def refuse(request):
        g.seen.append(request)
        return httpx.Response(402, json={'error': {'message': 'Insufficient Balance', 'type': 'fixture'}})
    gateway.transport = httpx.MockTransport(refuse)
    before = balance(g)
    tid = start(g); g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    assert turn['status'] == 'failed' and turn['error_code'] == 'provider_balance'
    assert turn['error_message'] == UNAVAILABLE and '余额不足' not in turn['error_message']
    assert len(g.seen) == 1
    # The operator's account is empty, not the person's: paid dispatch closes and admins hear of it.
    assert g.provider.settings.closure()['state'] == 'upstream_balance'
    [alert] = alerts(g, boss)
    assert alert['kind'] == 'billing.model.upstream_balance'
    for language in ('zh-Hans', 'zh-Hant', 'en'):
        assert CATALOGUE['billing.model.upstream_balance']['title'][language]

    # The next turn is refused unsent and unreserved, with the same honest sentence.
    tid = start(g); g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    # Same code as the platform breaker (billing/risk.py) so the chat reads it the same way.
    assert turn['error_code'] == 'billing_risk_breaker_open' and turn['error_message'] == UNAVAILABLE
    assert len(g.seen) == 1 and g.store.one('SELECT COUNT(*) AS n FROM billing_calls')['n'] == 1

    # Not charged: no sale was ever posted; the uncertain hold ends at its deadline unsold.
    assert g.store.all('SELECT * FROM billing_settlements') == []
    gateway.ledger.clock = lambda: int(time.time()) + 100_000
    gateway.recover()
    assert g.store.one("SELECT status FROM billing_calls")['status'] == 'released'
    assert g.store.all("SELECT * FROM billing_journal WHERE kind='sale'") == []
    assert balance(g).booked == before.booked and balance(g).held == 0


def test_the_402_seam_opens_the_platform_breaker_when_it_is_installed(monkeypatch):
    calls, fallback = [], []
    risk = types.ModuleType('synwevo_core.billing.risk')
    risk.trip_platform_breaker = lambda store, **kwargs: calls.append((store, kwargs)) or {'state': 'open'}
    monkeypatch.setitem(sys.modules, 'synwevo_core.billing.risk', risk)
    assert _open_platform_breaker('store', environment='test', reason_code='http_402',
                                  fallback=lambda: fallback.append(1)) == 'platform_breaker'
    assert calls == [('store', {'environment': 'test', 'source': 'upstream_402', 'reason_code': 'http_402'})]
    assert fallback == []

    # Not installed in this build: the package's own minimal state closes the provider revision.
    monkeypatch.setitem(sys.modules, 'synwevo_core.billing.risk', None)
    assert _open_platform_breaker('store', environment='test', reason_code='http_402',
                                  fallback=lambda: fallback.append(1)) == 'provider_revision'
    assert fallback == [1]


def test_a_failing_closure_still_tells_the_person_and_charges_nothing(governed, monkeypatch):
    g = governed; configure(g); fund(g)
    async def refuse(request):
        g.seen.append(request)
        return httpx.Response(402, json={'error': {'message': 'fixture'}})
    g.provider.gateway.transport = httpx.MockTransport(refuse)
    def broken(route):
        raise RuntimeError('fixture: closure storage unavailable')
    monkeypatch.setattr(g.provider.gateway, 'on_upstream_balance', broken)
    tid = start(g); g.agent._run(tid)
    turn = g.store.one('SELECT * FROM agent_turns WHERE id=?', (tid,))
    assert turn['error_code'] == 'provider_balance' and turn['error_message'] == UNAVAILABLE
    assert g.store.all('SELECT * FROM billing_settlements') == []
