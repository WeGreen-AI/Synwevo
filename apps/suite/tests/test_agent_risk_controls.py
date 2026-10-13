"""Risk controls at the real gateway send boundary (docs/platform/model-risk.md).

Isolated fixtures only: the gateway fixture's transport is a recorded SSE stream, the provider
account and prices are synthetic. Expectations are independent arithmetic on the fixture: every
call reserves a 297-unit cost bound (100*2 + 32*3 + 1) and settles at an exact cost of 42.
"""
from dataclasses import replace

import httpx
import pytest

from synwevo_core import journal
from synwevo_core.billing import BillingError
from synwevo_core.billing.risk import RiskControls, RiskPolicy, allowlist_required, trip_platform_breaker
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess

from .test_agent_cost_admission import governed
from .test_agent_metering import Wire, gateway  # noqa: F401 - fixture


def several_sends(g):
    """The shared fixture's transport asserts it sees the only attempt; these tests send after a refusal."""
    async def respond(request):
        assert g.store.one("SELECT COUNT(*) AS n FROM billing_attempts WHERE status='dispatch_unknown'")['n'] == 1
        g.seen.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(g.holder['body']))
    g.model.transport = httpx.MockTransport(respond)


def risk(**changes):
    """Fixture thresholds; brakes off unless a test sets them."""
    return replace(RiskPolicy(allowlist_only=False, payer_window_s=3600, payer_window_cost=None, route_window_s=3600,
                              route_window_cost=None, balance_warn=None, balance_close=None, balance_poll_s=300,
                              reconcile_tolerance=0, pending_alert=None), **changes)


def step(g, name):
    return g.model.step(**{**g.args, 'logical_call_id': name})


def refused(g, name, code):
    with pytest.raises(BillingError) as caught:
        step(g, name)
    assert caught.value.code == code
    return caught.value


def admin(g):
    uid = Identity(g.store).create_user('risk-admin@example.test', None, status='active',
                                        with_personal_workspace=False)['id']
    g.store.execute('UPDATE users SET email_verified_at=? WHERE id=?', (1.0, uid))  # A grant counts once verified.
    PlatformAccess(g.store).host_grant(uid, 'super_admin', source='fixture:risk', reason='isolated risk alert fixture')
    return uid


def inbox(g, uid, kind):
    return g.store.all('SELECT * FROM notifications WHERE user_id=? AND kind=?', (uid, kind))


# ---------------------------------------------------------------- 1. per-call maximum cost
def test_per_call_cost_cap_refuses_before_anything_is_sent_or_held(gateway):
    g = gateway
    governed(g, max_call_cost=296)  # The call's reserved cost bound is 297.
    refused(g, 'step-1', 'cost_call_limit_exceeded')
    assert g.seen == []
    assert g.balance().booked == 1000 and g.balance().held == 0
    assert g.store.all('SELECT * FROM model_cost_admissions') == []
    assert g.store.one('SELECT status FROM billing_attempts')['status'] == 'released'


def test_per_call_cost_cap_admits_a_bound_exactly_at_the_cap(gateway):
    g = gateway
    governed(g, max_call_cost=297)
    step(g, 'step-1')
    assert len(g.seen) == 1 and g.balance().booked == 958


# ---------------------------------------------------------------- 2. automatic brake
def test_payer_velocity_breach_refuses_latches_audits_and_notifies(gateway):
    g = gateway
    boss = admin(g)
    governed(g, risk=risk(payer_window_cost=338))
    step(g, 'step-1')                       # 0 + 297 <= 338; settles at 42.
    refused(g, 'step-2', 'risk_payer_brake')  # 42 + 297 = 339 > 338.
    assert len(g.seen) == 1 and g.balance().booked == 958 and g.balance().held == 0
    row = g.store.one('SELECT * FROM model_risk_suspensions')
    assert (row['scope_kind'], row['scope_id'], row['source'], row['reason_code']) == (
        'user', g.context.payer_user_id, 'brake', 'payer_velocity')
    assert (row['window_s'], row['threshold_units'], row['observed_units'], row['unit']) == (
        3600, 338, 339, 'FIXTURE-GATEWAY-COST')
    assert row['lifted_at'] is None
    audit = g.store.one("SELECT * FROM audit_events WHERE action='billing.risk.brake_triggered'")
    assert audit['actor_kind'] == 'system' and audit['object_id'] == row['id']
    assert len(inbox(g, g.context.payer_user_id, 'billing.risk.paid_paused')) == 1
    assert len(inbox(g, boss, 'billing.risk.brake_triggered')) == 1


def test_latched_payer_stays_suspended_after_the_window_until_an_admin_lifts_it(gateway):
    g = gateway
    several_sends(g)
    boss = admin(g)
    governed(g, risk=risk(payer_window_s=60, payer_window_cost=338))
    step(g, 'step-1')
    refused(g, 'step-2', 'risk_payer_brake')
    g.ticks[0] += 61  # The window no longer holds the first call's cost.
    g.model.open_run(replace(g.context, run_id='fixture-run-2'), route=g.route, budget=1000, max_calls=3,
                     deadline=g.ticks[0] + 500)
    g.args['context'] = replace(g.context, run_id='fixture-run-2')
    # A fresh upstream id: if the suspension were not enforced, this call would be sent and succeed.
    g.holder['body'] = g.holder['body'].replace(b'fixture-upstream-1', b'fixture-upstream-2')
    refused(g, 'step-3', 'risk_payer_suspended')
    assert len(g.seen) == 1
    suspension = g.store.one('SELECT id FROM model_risk_suspensions')['id']
    controls = RiskControls(g.store, environment='test', clock=lambda: g.ticks[0])
    with pytest.raises(Exception, match='请写明理由'):
        controls.lift(suspension, actor_id=boss, reason='short')
    lifted = controls.lift(suspension, actor_id=boss, reason='reviewed the isolated fixture spend')
    assert lifted['lifted_by'] == boss and lifted['lift_reason'] == 'reviewed the isolated fixture spend'
    step(g, 'step-4')
    assert len(g.seen) == 2


def test_route_velocity_breach_suspends_the_route(gateway):
    g = gateway
    boss = admin(g)
    governed(g, risk=risk(route_window_cost=296))
    refused(g, 'step-1', 'risk_route_brake')
    assert g.seen == []
    row = g.store.one('SELECT * FROM model_risk_suspensions')
    assert (row['scope_kind'], row['scope_id'], row['observed_units']) == ('route', g.route.requested_model, 297)
    assert len(inbox(g, boss, 'billing.risk.brake_triggered')) == 1
    assert inbox(g, g.context.payer_user_id, 'billing.risk.paid_paused') == []  # A route is not the payer's doing.
    refused(g, 'step-2', 'risk_route_suspended')


def test_brake_latch_is_idempotent_and_survives_restore_through_the_security_journal(gateway, tmp_path):
    g = gateway
    governed(g, risk=risk(payer_window_cost=1))
    refused(g, 'step-1', 'risk_payer_brake')
    refused(g, 'step-2', 'risk_payer_suspended')
    assert len(g.store.all('SELECT * FROM model_risk_suspensions')) == 1
    entries = [e for e in journal.read(g.store.root) if e['kind'] == 'model_dispatch_suspended']
    assert len(entries) == 1 and set(entries[0]) == {'at', 'kind', 'attempt_id', 'suspension_id', 'environment',
                                                     'scope_kind', 'scope_id'}
    # Written ahead with a receipt that committed with the suspension (journal._intent).
    assert g.store.one('SELECT 1 FROM meta WHERE key=?', ('journal_receipt:' + entries[0]['attempt_id'],))
    from synwevo_core.store import Store
    restored = Store(tmp_path / 'restored-before-the-suspension')
    journal.apply(restored, journal.read(g.store.root, since=10**12))  # Replayed whatever the backup's time.
    again = restored.one('SELECT * FROM model_risk_suspensions')
    assert again['id'] == entries[0]['suspension_id'] and again['lifted_at'] is None and again['source'] == 'journal'
    journal.apply(restored, journal.read(g.store.root))
    assert len(restored.all('SELECT * FROM model_risk_suspensions')) == 1


# ---------------------------------------------------------------- 3. platform breaker (guard side)
def test_upstream_402_breaker_refuses_every_paid_call_until_an_admin_closes_it(gateway):
    g = gateway
    several_sends(g)
    boss = admin(g)
    governed(g)
    opened = trip_platform_breaker(g.store, environment='test', source='upstream_402', reason_code='upstream_payment_required')
    assert trip_platform_breaker(g.store, environment='test', source='upstream_402',
                                 reason_code='upstream_payment_required')['id'] == opened['id']
    refused(g, 'step-1', 'risk_breaker_open')
    assert g.seen == [] and g.balance().held == 0 and g.balance().booked == 1000
    assert len(inbox(g, boss, 'billing.risk.breaker_opened')) == 1
    assert [e['event_id'] for e in journal.read(g.store.root) if e['kind'] == 'model_breaker_opened'] == [opened['id']]
    controls = RiskControls(g.store, environment='test')
    closed = controls.close_breaker(actor_id=boss, reason='upstream account topped up and verified')
    assert closed['state'] == 'closed' and closed['seq'] == 2
    step(g, 'step-2')
    assert len(g.seen) == 1


def test_breaker_is_scoped_to_its_billing_environment(gateway):
    g = gateway
    governed(g)
    trip_platform_breaker(g.store, environment='live', source='upstream_402', reason_code='upstream_payment_required')
    step(g, 'step-1')
    assert len(g.seen) == 1


# ---------------------------------------------------------------- 4. staged rollout allowlist
def test_allowlist_mode_admits_only_listed_payers(gateway):
    g = gateway
    several_sends(g)
    boss = admin(g)
    governed(g, risk=risk(allowlist_only=True))
    refused(g, 'step-1', 'risk_not_allowlisted')
    assert g.seen == [] and g.balance().held == 0
    controls = RiskControls(g.store, environment='test')
    entry = controls.allow('user', g.context.payer_user_id, actor_id=boss, reason='first production cohort')
    step(g, 'step-2')
    assert len(g.seen) == 1
    controls.disallow(entry['id'], actor_id=boss, reason='cohort closed for review')
    refused(g, 'step-3', 'risk_not_allowlisted')


def test_allowlist_defaults_on_for_live_and_off_for_test_until_a_risk_section_is_published():
    assert allowlist_required('live', None) is True
    assert allowlist_required('test', None) is False
    assert allowlist_required('live', risk(allowlist_only=False)) is False
    assert allowlist_required('test', risk(allowlist_only=True)) is True


# ---------------------------------------------------------------- composition
@pytest.mark.parametrize('start_jobs', [True, False])
def test_metered_composition_owns_the_risk_monitor_and_its_shutdown(gateway, monkeypatch, start_jobs):
    from synwevo_core.http import build_platform, new_api
    from synwevo_suite.agent.gateway import MeteredTurnProvider
    from synwevo_suite.app import install
    monkeypatch.setenv('SYNWEVO_SUITE_APPS', 'agent')
    g = gateway
    plat = build_platform(str(g.store.root))
    api = new_api('Isolated risk monitor fixture')
    g.model.store = plat.store
    g.ledger.store = plat.store
    api.state.agent_provider = MeteredTurnProvider(g.model)
    install(api, plat, start_jobs=start_jobs)
    monitor = api.state.model_risk_monitor
    try:
        assert monitor.environment == 'test' and monitor.close in api.state.shutdown_hooks
        assert (monitor._thread is not None) is start_jobs
        assert g.seen == []
    finally:
        for hook in api.state.shutdown_hooks:
            hook()
    assert monitor._thread is None or not monitor._thread.is_alive()


def test_second_kill_switch_removing_agent_from_the_release_list_leaves_no_paid_path(gateway, monkeypatch):
    """docs/operations.md §11.1: without `agent` in SYNWEVO_SUITE_APPS nothing of the paid Agent is
    installed: no Agent route, no metering recovery and no risk monitor, even with a provider ready."""
    from synwevo_core.http import build_platform, new_api
    from synwevo_suite.agent.gateway import MeteredTurnProvider
    from synwevo_suite.app import install
    monkeypatch.setenv('SYNWEVO_SUITE_APPS', 'forms')
    g = gateway
    plat = build_platform(str(g.store.root))
    api = new_api('Isolated kill switch fixture')
    g.model.store = plat.store
    g.ledger.store = plat.store
    api.state.agent_provider = MeteredTurnProvider(g.model)
    install(api, plat, start_jobs=True)
    try:
        assert getattr(api.state, 'model_risk_monitor', None) is None
        assert getattr(api.state, 'agent_metering_recovery', None) is None
        paths = {getattr(route, 'path', '') for route in api.routes}
        assert not any(path.startswith('/api/suite/agent') for path in paths)
        assert any(path.startswith('/api/suite/') for path in paths)  # The released application is served.
        assert g.seen == []
    finally:
        for hook in getattr(api.state, 'shutdown_hooks', []):
            hook()
