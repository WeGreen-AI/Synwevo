"""Platform exposure gates the real gateway send boundary, independently of user funds."""
from dataclasses import replace
import math

import pytest

from synwevo_core.billing import BillingError, Unit
from synwevo_core.billing.admission import CostAdmission, CostPolicy
from synwevo_suite.agent.deepseek import DeepSeekError
from synwevo_suite.agent.gateway import MeteredTurnProvider

from .test_agent_metering import gateway, event, USAGE
from .test_agent_run_quotes import paid, start


def governed(g, **changes):
    policy = CostPolicy(revision='cost-fixture-v1', account_ref='fixture-deepseek-account',
        provider='deepseek', unit=Unit('FIXTURE-GATEWAY-COST', 8), enabled=True,
        daily_budget=10000, weekly_budget=10000, monthly_budget=10000,
        unknown_budget=10000, max_inflight=10, max_unknown=10)
    policies = [replace(policy, **changes)]
    g.model.cost_admission = CostAdmission(g.ledger, current_policy=lambda: policies[0])
    return policies


def test_insufficient_platform_budget_never_spends_user_funds_or_sends(gateway):
    g = gateway
    governed(g, daily_budget=296)  # Independent expectation: 100*2 + 32*3 + carry = 297.
    with pytest.raises(BillingError):
        g.model.step(**g.args)
    assert g.seen == []
    assert g.balance().booked == 1000 and g.balance().held == 0
    assert g.store.all('SELECT * FROM model_cost_admissions') == []
    assert g.store.one('SELECT status FROM billing_attempts')['status'] == 'released'


def test_actual_cost_replaces_exposure_and_blocks_the_next_unaffordable_call(gateway):
    g = gateway
    policies = governed(g)
    g.model.step(**g.args)
    admission = g.store.one('SELECT * FROM model_cost_admissions')
    assert admission['reserved_units'] == 297
    assert admission['unit'] == 'FIXTURE-GATEWAY-COST'
    # In flight for at most the transport's timeout (the fixture's), plus one second.
    assert admission['expires_at'] == g.ticks[0] + math.ceil(g.model.limits.timeout_s) + 1
    assert g.store.one('SELECT cost_n,cost_d FROM billing_usage') == {'cost_n': '42', 'cost_d': '1'}
    policies[0] = replace(policies[0], revision='cost-fixture-v2', daily_budget=338)
    with pytest.raises(BillingError):  # 42 committed cost + 297 reservation = 339, not 338.
        g.model.step(**{**g.args, 'logical_call_id': 'step-2'})
    assert len(g.seen) == 1
    assert len(g.store.all('SELECT * FROM model_cost_admissions')) == 1
    assert g.balance().booked == 958 and g.balance().held == 0


def test_current_policy_is_rechecked_after_wallet_reservation(gateway, monkeypatch):
    g = gateway
    policies = governed(g)
    prepare = g.ledger.prepare_attempt
    def disable(*args, **kwargs):
        result = prepare(*args, **kwargs)
        policies[0] = replace(policies[0], enabled=False)
        return result
    monkeypatch.setattr(g.ledger, 'prepare_attempt', disable)
    with pytest.raises(BillingError):
        g.model.step(**g.args)
    assert g.seen == [] and g.balance().booked == 1000 and g.balance().held == 0
    assert g.store.all('SELECT * FROM model_cost_admissions') == []


def test_dispatch_commit_failure_rolls_back_cost_admission_together(gateway, monkeypatch):
    g = gateway
    governed(g)
    record = g.ledger._event
    def fail(t, call_key, kind, *args, **kwargs):
        if kind == 'dispatch_unknown':
            raise RuntimeError('isolated dispatch durability failure')
        return record(t, call_key, kind, *args, **kwargs)
    monkeypatch.setattr(g.ledger, '_event', fail)
    with pytest.raises(DeepSeekError) as refused:
        g.model.step(**g.args)
    assert refused.value.code == 'provider_dispatch_blocked'
    assert g.seen == [] and g.balance().held == 0
    assert g.store.all('SELECT * FROM model_cost_admissions') == []
    assert g.store.one('SELECT status FROM billing_attempts')['status'] == 'released'


def test_final_user_release_does_not_erase_unknown_platform_exposure(gateway):
    g = gateway
    governed(g, unknown_budget=297)
    g.holder['body'] = event({'content': 'isolated incomplete stream'})
    with pytest.raises(DeepSeekError):
        g.model.step(**g.args)
    g.ticks[0] += 200  # Past both the 100s wallet deadline and 121s connection budget.
    g.model.recover()
    assert g.balance().held == 0 and g.balance().booked == 1000
    with pytest.raises(BillingError):
        g.model.step(**{**g.args, 'logical_call_id': 'step-2'})
    assert len(g.seen) == 1
    assert len(g.store.all('SELECT * FROM model_cost_admissions')) == 1
    assert g.store.all('SELECT * FROM billing_usage') == []


def test_disabled_admission_does_not_block_recovery_of_incurred_cost(gateway, monkeypatch):
    g = gateway
    policies = governed(g)
    settle = g.ledger.settle_trusted_usage
    def outage(*args, **kwargs):
        raise RuntimeError('isolated settlement outage')
    monkeypatch.setattr(g.ledger, 'settle_trusted_usage', outage)
    with pytest.raises(RuntimeError, match='settlement outage'):
        g.model.step(**g.args)
    policies[0] = replace(policies[0], enabled=False)
    monkeypatch.setattr(g.ledger, 'settle_trusted_usage', settle)
    assert g.model.recover() == 1
    assert g.balance().booked == 958 and g.balance().held == 0
    assert len(g.seen) == 1
    assert g.store.one('SELECT cost_n,cost_d FROM billing_usage') == {'cost_n': '42', 'cost_d': '1'}


def test_personal_quoted_provider_requires_platform_cost_admission(gateway):
    from synwevo_suite.agent.quotes import RunQuotes
    g = gateway
    quotes = RunQuotes(g.model, current_route=lambda: g.route, current_authority=lambda _: True)
    g.ledger.authorize = quotes.authorize
    with pytest.raises(BillingError, match='model_cost_admission_required'):
        MeteredTurnProvider(g.model, quotes=quotes, credential_resolver=lambda *_: None)


@pytest.mark.parametrize('recover_projection', [False, True])
def test_cost_refusal_is_visible_in_personal_turn_without_unknown_usage(paid, recover_projection):
    from synwevo_suite.agent.recovery import MeteringRecovery
    g = paid
    governed(g, daily_budget=296)
    turn = start(g, g.issue())
    g.agent._run(turn['turn_id'])
    assert g.store.one('SELECT status,error_code,usage_state,input_tokens,output_tokens FROM agent_turns') == {
        'status': 'failed', 'error_code': 'billing_cost_daily_budget_exceeded',
        'usage_state': 'known', 'input_tokens': 0, 'output_tokens': 0}
    if recover_projection:
        # Isolated crash point after durable refusal but before the engine's projection.
        g.store.execute("UPDATE agent_turns SET usage_state='pending'")
        worker = MeteringRecovery(gateway=g.model)
        first = worker.run_once()
        second = worker.run_once()
        assert not first.failures and not second.failures
        assert g.store.one('SELECT usage_state,input_tokens,output_tokens FROM agent_turns') == {
            'usage_state': 'known', 'input_tokens': 0, 'output_tokens': 0}
        assert len(g.store.all("SELECT * FROM agent_events WHERE type='usage.reconciled'")) == 1
    assert g.seen == [] and g.balance().booked == 1000 and g.balance().held == 0
    assert g.store.all('SELECT * FROM billing_usage') == []
    assert g.store.all('SELECT * FROM model_cost_admissions') == []


def test_later_cost_refusal_preserves_completed_tool_and_known_usage(paid):
    from synwevo_suite.agent.recovery import MeteringRecovery
    g = paid
    governed(g, daily_budget=338)
    g.holder['body'] = event({'tool_calls': [{'index': 0, 'id': 'fixture-workspaces', 'type': 'function',
        'function': {'name': 'list_workspaces', 'arguments': '{}'}}]})
    g.holder['body'] += event(finish='tool_calls', usage=USAGE) + b'data: [DONE]\n\n'
    turn = start(g, g.issue())
    g.agent._run(turn['turn_id'])
    expected = {'status': 'failed', 'error_code': 'billing_cost_daily_budget_exceeded',
                'usage_state': 'known', 'input_tokens': 13, 'output_tokens': 7}
    query = 'SELECT status,error_code,usage_state,input_tokens,output_tokens FROM agent_turns'
    assert g.store.one(query) == expected
    g.store.execute("UPDATE agent_turns SET usage_state='pending'")
    assert not MeteringRecovery(gateway=g.model).run_once().failures
    assert g.store.one(query) == expected
    assert len(g.seen) == 1 and g.balance().booked == 958 and g.balance().held == 0
    assert len(g.store.all('SELECT * FROM agent_tool_calls')) == 1
    assert len(g.store.all('SELECT * FROM billing_usage')) == 1
    assert len(g.store.all('SELECT * FROM model_cost_admissions')) == 1
