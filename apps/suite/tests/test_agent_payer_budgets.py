"""Actual paid factory/HTTP, real budgets and ledger; synthetic funds/model protocol only."""
import json
import uuid

import pytest

from synwevo_core.capabilities import Call
from synwevo_core.errors import ServiceError
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE
from .test_agent_governed_factory import governed, configure, fund, start, UNIT


def body(revision=0, **changes):
    return dict(unit_code=UNIT.code, expected_revision=revision, enabled=True,
                daily_budget='1000000', weekly_budget='1000000', monthly_budget='1000000', **changes)


def update(g, payload=None, key=None, **kwargs):
    return g.suite.caps.invoke('agent.billing.budget.set', payload or body(), user_id=g.user['id'],
                              idempotency_key=key or uuid.uuid4().hex, **kwargs)


def test_factory_refuses_unconfigured_budget_without_a_network_attempt(governed):
    # Legacy explicit-quote path: admitted, refused at the first call (unchanged).
    g=governed;configure(g,payer_budget=False);fund(g)
    tid=start(g);g.agent._run(tid)
    assert g.store.one('SELECT error_code FROM agent_turns WHERE id=?',(tid,))['error_code']=='billing_payer_budget_not_configured'
    assert g.seen==[] and g.store.all('SELECT * FROM billing_attempts')==[]
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


@pytest.mark.parametrize('state,code',[('missing','billing_payer_budget_not_configured'),('disabled','billing_payer_budget_disabled')])
def test_an_automatic_send_reads_the_personal_limit_policy_at_admission_and_stores_nothing(governed,state,code):
    """Send-and-go (D-4): a personal payer without an enabled policy is refused before the message,
    turn or authorization is stored — not admitted and then refused at the first call."""
    from synwevo_core.terms import TermsAcceptances
    g=governed;configure(g,payer_budget=(state=='disabled'));fund(g)
    TermsAcceptances(g.store).record_paid_use(g.user['id'],context='fixture:agent_setup')   # FIXTURE: the start step was done
    if state=='disabled':
        update(g,{**body(1),'enabled':False})
    from .test_agent_automatic_runs import displayed
    with pytest.raises(ServiceError) as caught:   # the page sends the price it displays (addendum-2 S1)
        g.suite.caps.invoke('agent.turn.start',{'conversation_id':g.conversation['id'],'text':'Read my forms',**displayed(g)},
                            user_id=g.user['id'],via='ui',idempotency_key='auto-no-limit')
    assert (caught.value.status,caught.value.code)==(409,code)
    assert caught.value.message=='本次回答未能开始，消息没有发送，也没有扣费'
    for table in ('agent_turns','agent_messages','agent_model_quotes','billing_calls','billing_attempts','billing_runs'):
        assert g.store.all(f'SELECT * FROM {table}')==[],table
    assert g.seen==[]


@pytest.mark.parametrize('field',['daily_budget','weekly_budget','monthly_budget','enabled'])
def test_current_payer_budget_blocks_actual_transport_and_releases_held_funds(governed,monkeypatch,field):
    g=governed;configure(g);fund(g);tid=start(g)
    ledger=g.provider.gateway.ledger;original=ledger.prepare_attempt
    def change(*args,**kwargs):
        ticket=original(*args,**kwargs)
        payload=body(1);payload[field]=False if field=='enabled' else '0'
        update(g,payload)
        return ticket
    monkeypatch.setattr(ledger,'prepare_attempt',change)
    g.agent._run(tid)
    turn=g.store.one('SELECT * FROM agent_turns WHERE id=?',(tid,))
    expected='billing_payer_budget_disabled' if field=='enabled' else 'billing_payer_'+field.replace('_budget','')+'_budget_exceeded'
    assert turn['error_code']==expected and turn['status']=='failed'
    assert g.seen==[] and not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")
    assert g.store.all('SELECT * FROM billing_settlements')==[]
    assert all(row['dispatched_at'] is None for row in g.store.all('SELECT * FROM billing_attempts'))


def test_shared_contract_idempotency_audit_event_and_person_only(governed):
    g=governed;configure(g,payer_budget=False)
    result=update(g,key='same')
    replay=update(g,key='same')
    assert replay['replayed'] and replay['output']==result['output']
    assert result['output']['daily_budget']=='1000000'
    assert len(g.store.all('SELECT * FROM billing_payer_budget_versions'))==1
    events=g.store.all("SELECT * FROM app_events WHERE type='agent.payer_budget.changed'")
    assert len(events)==1 and events[0]['workspace_id'] is None
    assert len(g.store.all("SELECT * FROM audit_events WHERE action='agent.billing.budget.set'"))==1
    with pytest.raises(ServiceError) as exc:update(g,{**body(),'daily_budget':'1'},key='same')
    assert exc.value.code=='idempotency_conflict'
    for via in ('agent','flow','automation'):
        with pytest.raises(ServiceError) as exc:update(g,body(1),via=via)
        assert exc.value.code in ('not_for_agent','person_required')
        with pytest.raises(ServiceError) as exc:
            g.suite.caps.invoke('agent.billing.budget.get',{},user_id=g.user['id'],via=via)
        assert exc.value.code in ('not_for_agent','person_required')
    read=g.suite.caps.inspect('agent.billing.budget.get',{},user_id=g.user['id'])
    assert read['budgets'][0]==result['output'] and read['timezone']=='UTC'
    other=g.suite.people.users['bob']['id']
    other_result=g.suite.caps.inspect('agent.billing.budget.get',{},user_id=other)
    assert not other_result['budgets'][0]['configured']
    assert g.user['id'] not in json.dumps(other_result)
    g.store.execute("UPDATE users SET status='suspended' WHERE id=?",(g.user['id'],))
    with pytest.raises(ServiceError):update(g,key='same')


def test_event_failure_rolls_back_policy_and_success_receipt(governed,monkeypatch):
    g=governed;configure(g,payer_budget=False)
    def fail(*args,**kwargs):raise RuntimeError('fixture event failure')
    monkeypatch.setattr(Call,'emit',fail)
    with pytest.raises(ServiceError):update(g)
    assert g.store.all('SELECT * FROM billing_payer_budget_versions')==[]
    assert g.store.all("SELECT * FROM app_events WHERE type='agent.payer_budget.changed'")==[]
    assert not g.store.one("SELECT 1 FROM op_runs WHERE op='agent.billing.budget.set' AND status='succeeded'")


def test_actual_http_csrf_exact_amounts_and_cannot_choose_another_payer(governed):
    g=governed;configure(g,payer_budget=False)
    client=g.suite
    base='/api/suite/ops/agent.billing.budget.'
    client.cookies.clear()
    assert client.post(base+'get',json={'input':{}}).status_code==401
    raw,_=client.app.state.sessions.create(g.user['id'],workspace_id=None,amr='password')
    client.cookies.set(COOKIE_NAME,raw);client.cookies.set(CSRF_COOKIE,'payer-fixture')
    payload={**body(),'daily_budget':'9007199254740993'}
    assert client.post(base+'set',json={'input':payload}).status_code==403
    headers={'X-Synwevo-CSRF':'payer-fixture'}
    result=client.post(base+'set',json={'input':payload,'idempotency_key':'http-payer-fixture'},headers=headers)
    assert result.status_code==200,result.text
    assert result.json()['output']['daily_budget']=='9007199254740993'
    for changes in ({'payer_user_id':g.suite.people.users['bob']['id']},{'daily_budget':True},{'timezone':'Pacific/Kiritimati'}):
        response=client.post(base+'set',json={'input':{**body(1),**changes},'idempotency_key':uuid.uuid4().hex},headers=headers)
        assert response.status_code==422
    conflict=client.post(base+'set',json={'input':body(),'idempotency_key':uuid.uuid4().hex},headers=headers)
    assert conflict.status_code==409 and conflict.json()['error']=='billing_payer_budget_conflict'
    assert g.seen==[] and g.store.all('SELECT * FROM billing_journal')==[]
