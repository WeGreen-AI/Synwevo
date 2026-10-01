"""Real turn admission and ledger, isolated provider/payment protocol fixtures only."""
from dataclasses import replace
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from synwevo_core.billing import BillingError
from synwevo_core.billing.admission import CostAdmission, CostPolicy
from synwevo_core.errors import ServiceError
from synwevo_suite.agent.gateway import MeteredTurnProvider
from synwevo_suite.agent.provider import Credential
from synwevo_suite.agent.quotes import RunQuotes
from .test_agent_metering import gateway  # explicit isolated Store/Ledger/transport fixture


@pytest.fixture
def paid(gateway, monkeypatch):
    from synwevo_suite.app import create_app
    g=gateway
    monkeypatch.setenv('SYNWEVO_SUITE_APPS','forms,agent')
    monkeypatch.setenv('SYNWEVO_UI_AUTH','session')
    monkeypatch.setenv('SYNWEVO_COOKIE_SECURE','0')
    monkeypatch.setenv('SYNWEVO_ROLE','portal')
    monkeypatch.setenv('SYNWEVO_APP_URL','http://workplace.testserver')
    api=create_app(str(g.store.root),start_jobs=False)
    agent=api.state.agent
    monkeypatch.setattr(agent,'_submit',lambda turn_id:None)
    user=api.state.identity.get_user(g.context.actor_user_id)
    g.store.execute('UPDATE users SET email_verified_at=1 WHERE id=?',(user['id'],))
    # Admission and its grant share the exact Store transaction, as deployment composition must.
    g.store=agent.store;g.ledger.store=agent.store;g.model.store=agent.store
    from synwevo_core.billing import Unit
    policy=CostPolicy('fixture-policy-v1','fixture-provider-account','deepseek',Unit('FIXTURE-GATEWAY-COST',8),
                      True,10000,10000,10000,10000,10,10)
    g.model.cost_admission=CostAdmission(g.ledger,current_policy=lambda:policy)
    route=[g.route]
    quotes=RunQuotes(g.model,current_route=lambda:route[0],current_authority=lambda context:g.authority[0])
    g.ledger.authorize=quotes.authorize
    agent.provider=MeteredTurnProvider(g.model,quotes=quotes,
        credential_resolver=lambda store,uid:Credential('platform','fixture-only',route[0].requested_model))
    conversation=agent.create_conversation(user['id'],'Budget fixture',[])
    def issue(**kwargs):
        args=dict(parent_id=None,regenerate_of=None,budget=1000,max_calls=3,run_seconds=500)
        args.update(kwargs)
        return agent.quote_turn(user,conversation['id'],'fixture question',**args)
    g.api=api
    g.agent,g.quotes,g.user,g.conversation,g.issue,g.route_holder=agent,quotes,user,conversation,issue,route
    yield g
    agent.close()


def start(g,q,text='fixture question'):
    return g.agent.start_turn(g.user,g.conversation['id'],text,parent_id=None,regenerate_of=None,quote_id=q['id'])


def test_quote_discloses_fixed_terms_without_freezing_or_network_and_accepts_once(paid):
    g=paid;q=g.issue()
    assert q['budget']=='1000' and q['per_call_hold']=='297'
    assert q['payer_user_id']==g.user['id'] and q['environment']=='test'
    assert q['price_id']=='fixture-sale' and q['unit']=={'code':'FIXTURE-GATEWAY','scale':6}
    assert q['rates']['input_uncached']=={'numerator':'2','denominator':'1'}
    assert q['status']=='quoted' and g.balance().held==0 and g.seen==[]
    assert 'fixture question' not in json.dumps(g.store.one('SELECT * FROM agent_model_quotes'))
    turn=start(g,q)
    assert g.quotes.get(g.user['id'],q['id'])['turn_id']==turn['turn_id']
    g.agent._run(turn['turn_id'])
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?',(turn['turn_id'],))['status']=='succeeded'
    assert g.balance().booked==958 and len(g.seen)==1
    with pytest.raises(ServiceError) as exc:start(g,q)
    assert exc.value.code=='billing_quote_already_used'
    assert len(g.store.all('SELECT * FROM agent_turns'))==1
    assert g.balance().booked==958 and len(g.seen)==1


@pytest.mark.parametrize('change',['text','scope','head','route','expiry','revoke'])
def test_quote_context_and_lifecycle_cannot_be_reused_for_changed_action(paid,change):
    g=paid;q=g.issue();text='fixture question'
    if change=='text':text='different request'
    elif change=='scope':g.store.execute("UPDATE agent_conversations SET scope_json=NULL WHERE id=?",(g.conversation['id'],))
    elif change=='head':g.store.execute("UPDATE agent_conversations SET updated_at=updated_at+1 WHERE id=?",(g.conversation['id'],))
    elif change=='route':g.route_holder[0]=replace(g.route,version='new-route')
    elif change=='expiry':g.ticks[0]+=300
    elif change=='revoke':g.quotes.revoke(g.user['id'],q['id'])
    with pytest.raises(ServiceError):start(g,q,text)
    assert g.store.all('SELECT * FROM agent_turns')==[]
    assert g.store.all('SELECT * FROM agent_messages')==[]
    assert not g.store.one('SELECT accepted_turn_id FROM agent_model_quotes')['accepted_turn_id']
    assert g.seen==[] and g.balance().held==0


def test_missing_quote_fails_before_worker_or_messages(paid):
    g=paid
    with pytest.raises(ServiceError,match='价格与预算'):
        g.agent.start_turn(g.user,g.conversation['id'],'no consent',parent_id=None,regenerate_of=None)
    assert g.store.all('SELECT * FROM agent_turns')==[]
    assert g.store.all('SELECT * FROM agent_messages')==[]


def test_revoke_after_admission_stops_unsent_paid_turn(paid):
    g=paid;q=g.issue();turn=start(g,q)
    g.quotes.revoke(g.user['id'],q['id'])
    g.agent._run(turn['turn_id'])
    assert g.store.one('SELECT status,error_code FROM agent_turns')['status']=='failed'
    assert g.seen==[] and g.balance().booked==1000


def test_quote_is_private_and_environment_scoped(paid):
    g=paid;q=g.issue()
    with pytest.raises(BillingError,match='quote_not_found'):g.quotes.get('someone-else',q['id'])
    old=g.ledger.environment;g.ledger.environment='live'
    try:
        with pytest.raises(BillingError,match='quote_not_found'):g.quotes.get(g.user['id'],q['id'])
    finally:g.ledger.environment=old


def test_accept_rolls_back_if_turn_insert_fails(paid,monkeypatch):
    g=paid;q=g.issue();original=g.store.after_commit
    def fail(callback):raise RuntimeError('fixture admission failure')
    monkeypatch.setattr(g.store,'after_commit',fail)
    with pytest.raises(RuntimeError):start(g,q)
    assert g.store.all('SELECT * FROM agent_turns')==[]
    assert g.quotes.get(g.user['id'],q['id'])['status']=='quoted'
    monkeypatch.setattr(g.store,'after_commit',original)
    assert start(g,q)['turn_id']


def test_concurrent_acceptance_only_creates_one_turn(paid):
    g=paid;q=g.issue()
    def accept(_):
        try:return start(g,q)
        except ServiceError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,range(2)))
    assert sum(isinstance(x,dict) for x in results)==1
    assert len(g.store.all('SELECT * FROM agent_turns'))==1


def test_fixed_budget_reaches_ledger_and_model_dispatch_rechecks_grant(paid,monkeypatch):
    g=paid;q=g.issue(budget=297,max_calls=1);turn=start(g,q)
    original=g.ledger.prepare_attempt
    def revoke(*a,**kw):
        ticket=original(*a,**kw);g.quotes.revoke(g.user['id'],q['id']);return ticket
    monkeypatch.setattr(g.ledger,'prepare_attempt',revoke)
    g.agent._run(turn['turn_id'])
    run=g.store.one('SELECT * FROM billing_runs WHERE run_id=?',(turn['turn_id'],))
    assert run['budget']==297 and run['max_calls']==run['max_attempts']==1
    assert g.seen==[] and g.balance().held==0


@pytest.mark.parametrize('args',[{'budget':296},{'budget':True},{'budget':2**63},{'max_calls':17},{'max_calls':False},{'run_seconds':0}])
def test_invalid_budget_rejected_without_quote(paid,args):
    with pytest.raises(ServiceError):paid.issue(**args)
    assert paid.store.all('SELECT * FROM agent_model_quotes')==[]


def test_quote_terms_immutable_and_revoke_irreversible(paid):
    import sqlite3
    g=paid;q=g.issue();g.quotes.revoke(g.user['id'],q['id'])
    for assignment in ("terms_json='{}'","revoked_at=NULL","user_id='another'"):
        with pytest.raises(sqlite3.IntegrityError):g.store.execute('UPDATE agent_model_quotes SET '+assignment)
    with pytest.raises(sqlite3.IntegrityError):g.store.execute('DELETE FROM agent_model_quotes')


def test_shared_operations_quote_start_replay_and_revoke(paid):
    g=paid;caps=g.agent.caps;uid=g.user['id']
    assert caps.store is g.store
    offer=caps.inspect('agent.billing.offer',{},user_id=uid)
    assert offer['max_calls_limit']==12 and offer['per_call_hold']=='297'
    args={'conversation_id':g.conversation['id'],'text':'fixture question','budget':'1000','max_calls':3,'run_seconds':500}
    result=caps.invoke('agent.turn.quote',args,user_id=uid,idempotency_key='fixture-quote-1')
    again=caps.invoke('agent.turn.quote',args,user_id=uid,idempotency_key='fixture-quote-1')
    assert again['replayed'] and result['output']==again['output']
    assert len(g.store.all('SELECT * FROM agent_model_quotes'))==1
    q=result['output'];body={'conversation_id':g.conversation['id'],'text':'fixture question','quote_id':q['id']}
    first=caps.invoke('agent.turn.start',body,user_id=uid,idempotency_key='fixture-start-1')
    second=caps.invoke('agent.turn.start',body,user_id=uid,idempotency_key='fixture-start-1')
    assert second['replayed'] and second['output']==first['output']
    out=caps.invoke('agent.quote.revoke',{'conversation_id':g.conversation['id'],'quote_id':q['id']},
                    user_id=uid,idempotency_key='fixture-revoke-1')['output']
    assert out['status']=='revoked'
    g.agent._run(first['output']['turn_id'])
    assert g.seen==[] and g.balance().booked==1000


def test_http_session_csrf_privacy_and_exact_large_budget(paid):
    from fastapi.testclient import TestClient
    from synwevo_core.sessions import COOKIE_NAME,CSRF_COOKIE
    g=paid
    with TestClient(g.api,base_url='http://workplace.testserver') as client:
        base='/api/suite/ops/'
        assert client.post(base+'agent.billing.offer',json={'input':{}}).status_code==401
        raw,_=g.api.state.sessions.create(g.user['id'],workspace_id=None,amr='password')
        client.cookies.set(COOKIE_NAME,raw);client.cookies.set(CSRF_COOKIE,'quote-fixture-csrf')
        args={'conversation_id':g.conversation['id'],'text':'fixture question','budget':'9007199254740993','max_calls':1,'run_seconds':60}
        assert client.post(base+'agent.turn.quote',json={'input':args}).status_code==403
        headers={'X-Synwevo-CSRF':'quote-fixture-csrf'}
        result=client.post(base+'agent.turn.quote',json={'input':args,'idempotency_key':'http-quote-fixture'},headers=headers)
        assert result.status_code==200,result.text
        quote=result.json()['output'];assert quote['budget']=='9007199254740993'
        assert 'cost_price' not in result.text and 'fixture-only' not in result.text
        other=g.api.state.identity.create_user('other-budget@example.test',None,status='active',with_personal_workspace=False)
        g.store.execute('UPDATE users SET email_verified_at=1 WHERE id=?',(other['id'],))
        raw,_=g.api.state.sessions.create(other['id'],workspace_id=None,amr='password')
        client.cookies.set(COOKIE_NAME,raw)
        result=client.post(base+'agent.quote.get',json={'input':{'conversation_id':g.conversation['id'],'quote_id':quote['id']}},headers=headers)
        assert result.status_code==404
    assert g.seen==[] and g.balance().held==0
