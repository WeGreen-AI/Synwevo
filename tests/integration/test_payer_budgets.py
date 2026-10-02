"""Personal period budgets against real Store/Ledger; isolated money fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
import sqlite3

import pytest

from synwevo_core.billing import BillingError, Ledger, Usage
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.store import Store
from .test_billing_ledger import (money, UNIT, COST_UNIT, context, fund, balance, fixture_authorize,
    fixture_verify, reserve, dispatch, settle, sale_price)


def configure(money, *, ledger=None, user=0, revision=0, **changes):
    ledger = ledger or money[1]
    if ledger.payer_budgets is None: ledger.payer_budgets = PayerBudgets(ledger)
    params = dict(unit_code=UNIT.code, expected_revision=revision, enabled=True,
                  daily_budget=100, weekly_budget=200, monthly_budget=300)
    params.update(changes)
    with ledger.store.tx():
        return ledger.payer_budgets.set(money[2][user], **params)


def view(money, user=0):
    return money[1].payer_budgets.read(money[2][user])['budgets'][0]


def test_missing_limits_never_create_funds_or_grants_and_default_factory_is_closed(money):
    store, ledger, users, _ = money
    ledger.payer_budgets = PayerBudgets(ledger)
    before = view(money)
    assert not before['configured'] and not before['enabled'] and before['revision'] == 0
    assert before['periods']['daily']['spent'] == before['periods']['daily']['held'] == '0'
    assert store.all('SELECT * FROM billing_payer_budget_versions') == []
    assert store.all('SELECT * FROM billing_journal') == []
    fund(money)
    with pytest.raises(BillingError, match='payer_budget_not_configured'): reserve(money)
    assert balance(money).held == 0
    assert store.all('SELECT * FROM billing_calls') == []


@pytest.mark.parametrize('period', ['daily','weekly','monthly'])
def test_independent_limits_are_atomic_and_no_double_reserve_on_replay(money, period):
    fund(money, 1000); configure(money, **{period+'_budget': 20})
    call = reserve(money, amount=20)
    assert reserve(money, amount=20) == call
    with pytest.raises(BillingError, match='payer_'+period+'_budget_exceeded'):
        reserve(money, call='another', amount=1)
    assert balance(money).held == 20
    assert len(money[0].all('SELECT * FROM billing_payer_budget_admissions')) == 1
    assert view(money)['periods'][period]['remaining'] == '0'


def test_current_revision_rechecked_at_send_and_rollback_preserves_prepared_attempt(money):
    fund(money); configure(money)
    call = reserve(money, amount=20)
    ledger = money[1]
    ticket = ledger.prepare_attempt(call, attempt_id='a', owner='fixture', cost_price_id='fixture-cost-v1', requested_model='fixture')
    configure(money, revision=1, daily_budget=19)
    with pytest.raises(BillingError, match='payer_daily_budget_exceeded'): ledger.dispatch(ticket)
    row = money[0].one('SELECT * FROM billing_attempts')
    assert row['status'] == 'prepared' and row['dispatched_at'] is None
    assert not money[0].one("SELECT 1 FROM billing_events WHERE kind='payer_budget_checked'")
    configure(money, revision=2, daily_budget=20)
    ledger.dispatch(ticket)
    assert money[0].one("SELECT reason FROM billing_events WHERE kind='payer_budget_checked'")['reason'] == 'revision:3'


def test_stop_new_work_does_not_prevent_known_settlement_or_rewrite_usage(money):
    fund(money); configure(money)
    ticket = dispatch(money, reserve(money, amount=20))
    configure(money, revision=1, enabled=False)
    assert settle(money, ticket).charged == 9
    daily = view(money)['periods']['daily']
    assert (daily['spent'], daily['held'], daily['remaining']) == ('9', '0', '91')
    with pytest.raises(BillingError, match='payer_budget_disabled'): reserve(money, call='next')
    configure(money, revision=2, daily_budget=10)
    with pytest.raises(BillingError, match='payer_daily_budget_exceeded'): reserve(money, call='next', amount=2)
    reserve(money, call='last', amount=1)
    assert balance(money).booked == 91 and balance(money).held == 1


@pytest.mark.parametrize('day', ['2034-01-02', '2034-02-01', '2034-01-01'])
def test_utc_period_boundary_carries_old_holds_and_late_charge_stays_in_original_period(money, day):
    store, ledger, _, ticks = money
    boundary = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    # Price fixture effective date is earlier than these calendar cases.
    ticks[0] = boundary - 30
    fund(money); configure(money, daily_budget=20, weekly_budget=20, monthly_budget=20)
    ctx = context(money)
    ledger.open_run(ctx, unit=UNIT, budget=100, max_calls=5, max_attempts=5, deadline=boundary+200)
    call = ledger.reserve_call(ctx, logical_call_id='old', amount=20, price_id='fixture-sale-v1', deadline=boundary+100)
    ticket = dispatch(money, call)
    ticks[0] = boundary + 1
    current = view(money)
    assert current['periods']['daily']['held'] == '20'
    with pytest.raises(BillingError, match='payer_daily_budget_exceeded'):
        ledger.reserve_call(ctx, logical_call_id='new', amount=1, price_id='fixture-sale-v1', deadline=boundary+100)
    assert settle(money,ticket).charged == 9
    current = view(money)
    assert current['periods']['daily']['spent'] == '0' and current['periods']['daily']['held'] == '0'
    assert current['periods']['weekly']['spent'] == ('0' if day == '2034-01-02' else '9')
    assert current['periods']['monthly']['spent'] == ('9' if day == '2034-01-02' else '0')
    assert current['periods']['weekly']['start'] % 86400 == 0
    ledger.reserve_call(ctx, logical_call_id='new', amount=1, price_id='fixture-sale-v1', deadline=boundary+100)


def test_two_users_units_environments_and_namespaces_have_correct_scope(money):
    fund(money, 1000); fund(money, 1000, user=1, order='other-user')
    configure(money, daily_budget=20); configure(money, user=1, daily_budget=50)
    reserve(money, amount=20)
    other = Ledger(Store(money[0].root), environment='test', namespace='different', authorize=fixture_authorize,
                   verify_payment=fixture_verify, clock=lambda:money[3][0])
    other.payer_budgets = PayerBudgets(other)
    ctx=context(money,run='new-space')
    other.open_run(ctx,unit=UNIT,budget=100,max_calls=5,max_attempts=5,deadline=money[3][0]+100)
    with pytest.raises(BillingError, match='payer_daily_budget_exceeded'):
        other.reserve_call(ctx,logical_call_id='one',amount=1,price_id='fixture-sale-v1',deadline=money[3][0]+100)
    reserve(money,ctx=context(money,user=1,run='other-user-run'),amount=40)
    assert view(money,1)['periods']['daily']['held'] == '40'
    live=Ledger(money[0],environment='live',namespace='different',clock=lambda:money[3][0])
    live.publish_price(sale_price())
    assert not PayerBudgets(live).read(money[2][0])['budgets'][0]['configured']
    with pytest.raises(BillingError,match='unknown_sale_unit'):configure(money, unit_code=COST_UNIT.code)


def test_independent_store_workers_compete_for_last_period_allowance(money):
    fund(money, 1000); configure(money, daily_budget=20)
    barrier=Barrier(6)
    def one(i):
        ledger=Ledger(Store(money[0].root),environment='test',namespace='parallel-'+str(i),
                      authorize=fixture_authorize,clock=lambda:money[3][0])
        ledger.payer_budgets=PayerBudgets(ledger)
        ctx=context(money,run='parallel-'+str(i))
        ledger.open_run(ctx,unit=UNIT,budget=100,max_calls=5,max_attempts=5,deadline=money[3][0]+100)
        barrier.wait()
        try:
            return ledger.reserve_call(ctx,logical_call_id='one',amount=20,price_id='fixture-sale-v1',deadline=money[3][0]+100)
        except BillingError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(one,range(6)))
    assert results.count('payer_daily_budget_exceeded') == 5
    assert balance(money).held == 20
    assert len(money[0].all('SELECT * FROM billing_payer_budget_admissions')) == 1


def test_revision_change_does_not_reset_history_and_invalid_settings_rollback(money):
    configure(money, daily_budget=2**63-1)
    assert view(money)['daily_budget'] == str(2**63-1)
    with pytest.raises(BillingError,match='payer_budget_conflict'):configure(money, revision=0)
    for change in ({'daily_budget': True},{'monthly_budget':-1},{'weekly_budget':2**63},{'enabled':1}):
        with pytest.raises(BillingError): configure(money,revision=1,**change)
    assert view(money)['revision'] == 1
    with pytest.raises(RuntimeError):
        with money[0].tx():
            configure(money,revision=1,enabled=False)
            raise RuntimeError('fixture audit failure')
    assert view(money)['revision'] == 1 and len(view(money)['history']) == 1
    reopened=Store(money[0].root)
    assert len(reopened.all('SELECT * FROM billing_payer_budget_versions')) == 1
    for sql in ('DELETE FROM billing_payer_budget_versions','UPDATE billing_payer_budget_versions SET enabled=0',
                'INSERT OR REPLACE INTO billing_payer_budget_versions SELECT * FROM billing_payer_budget_versions'):
        with pytest.raises(sqlite3.IntegrityError):money[0].execute(sql)


def test_accounting_bound_refuses_dispatch_but_can_still_disable_and_reports_unknown(money, monkeypatch):
    fund(money); configure(money)
    reserve(money, call='one', amount=1)
    monkeypatch.setattr(money[1].payer_budgets,'CAPACITY',0)
    state=view(money)
    assert state['usage_state'] == 'capacity' and state['periods'] is None
    with pytest.raises(BillingError,match='payer_budget_capacity'):reserve(money,call='two',amount=1)
    changed=configure(money,revision=1,enabled=False)
    assert not changed['enabled'] and changed['periods'] is None


def test_reservation_admission_failure_rolls_back_hold_and_event(money, monkeypatch):
    fund(money); configure(money)
    def fail(*args):raise RuntimeError('fixture evidence failure')
    monkeypatch.setattr(money[1].payer_budgets,'record',fail)
    with pytest.raises(RuntimeError):reserve(money)
    assert balance(money).held == 0
    assert money[0].all('SELECT * FROM billing_calls') == money[0].all('SELECT * FROM billing_events') == []


def test_only_current_month_and_pending_ranges_are_queried(money):
    fund(money); configure(money); reserve(money)
    sql=[]
    with money[0].conn() as c:
        c.set_trace_callback(sql.append)
        try:view(money)
        finally:c.set_trace_callback(None)
    query=next(s for s in sql if s.startswith('WITH candidates'))
    plan=money[0].all('EXPLAIN QUERY PLAN '+query)
    detail=' '.join(row['detail'] for row in plan)
    assert 'billing_calls_holds' in detail and 'billing_calls_payer_period' in detail
    assert 'MATERIALIZE candidates' in detail


def test_final_release_frees_period_budget_but_late_usage_cannot_secretly_charge(money):
    fund(money); configure(money,daily_budget=20)
    call=reserve(money,amount=20);ticket=dispatch(money,call)
    money[3][0]+=501
    assert money[1].expire_due()==1
    assert view(money)['periods']['daily']['held']=='0'
    assert settle(money,ticket).charged==0
    assert balance(money).booked==100
    assert view(money)['periods']['daily']['spent']=='0'


def test_zero_limit_is_an_explicit_stop_and_read_requires_active_account(money):
    fund(money);configure(money,daily_budget=0)
    with pytest.raises(BillingError,match='payer_daily_budget_exceeded'):reserve(money,amount=1)
    money[0].execute("UPDATE users SET status='suspended' WHERE id=?",(money[2][0],))
    with pytest.raises(BillingError,match='account_unavailable'):view(money)


@pytest.mark.parametrize('day,period', [('2034-01-03','daily'),('2034-01-02','weekly'),('2034-02-01','monthly')])
def test_clock_rollover_inside_reservation_cannot_use_next_period_for_old_attribution(money,monkeypatch,day,period):
    ledger=money[1];ticks=money[3]
    boundary=int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    ticks[0]=boundary-2
    fund(money)
    configure(money,**{period+'_budget':9})
    ctx=context(money)
    ledger.open_run(ctx,unit=UNIT,budget=100,max_calls=5,max_attempts=5,deadline=boundary+100)
    call=ledger.reserve_call(ctx,logical_call_id='before',amount=9,price_id='fixture-sale-v1',deadline=boundary+100)
    ticket=dispatch(money,call)
    assert settle(money,ticket).charged==9
    original=ledger._balance
    def rollover(*args):
        result=original(*args)
        ticks[0]=boundary
        return result
    monkeypatch.setattr(ledger,'_balance',rollover)
    with pytest.raises(BillingError,match='payer_'+period+'_budget_exceeded'):
        ledger.reserve_call(ctx,logical_call_id='rollover',amount=9,price_id='fixture-sale-v1',deadline=boundary+100)
    assert not money[0].one("SELECT 1 FROM billing_calls WHERE logical_call_id='rollover'")
    assert balance(money).held==0
