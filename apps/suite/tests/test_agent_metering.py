"""Gateway integration on real Store/Ledger with isolated payment and SSE protocol fixtures.

No real model/checkout calls. The payment inbox and route prices are explicitly synthetic.
Test transport inspects the committed dispatch state before any simulated network response.
"""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import httpx
import pytest

from synwevo_core.billing import (BillingError, ChargeContext, Ledger, PaymentReceipt,
                                 PriceSnapshot, Rate, Unit)
from synwevo_core.identity import Identity
from synwevo_core.store import Store
from synwevo_suite.agent.deepseek import DeepSeekCancelled, DeepSeekError
from synwevo_suite.agent.gateway import MeteredDeepSeek, Route
from synwevo_suite.agent.provider import Credential

STAMP = 2_000_000_000
UNIT = Unit('FIXTURE-GATEWAY', 6)
USAGE = {'prompt_tokens': 13, 'prompt_cache_hit_tokens': 5, 'prompt_cache_miss_tokens': 8,
         'completion_tokens': 7, 'total_tokens': 20,
         'completion_tokens_details': {'reasoning_tokens': 3}}


def event(delta=None, finish=None, usage=None):
    return ('data: '+json.dumps({'id': 'fixture-upstream-1', 'model': 'fixture-returned-label', 'object':'chat.completion.chunk',
        'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': finish}], 'usage': usage})+'\n\n').encode()


def complete():
    return event({'reasoning_content': 'private-fixture-reasoning', 'content': 'actual fixture response'}) + \
        event(finish='stop', usage=USAGE) + b'data: [DONE]\n\n'


class Wire(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
    async def __aiter__(self):
        yield self.body


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    async def no_network(*a, **kw):
        pytest.fail('Gateway fixture attempted a real paid request')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    store = Store(tmp_path/'metered-fixture')
    user = Identity(store).create_user('metering@example.test', None, status='active', with_personal_workspace=False)['id']
    ticks, authority, stopped, seen = [STAMP], [True], [False], []
    store.execute('CREATE TABLE fixture_verified_payments(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
    def verify(t, receipt):
        row=t.execute('SELECT receipt FROM fixture_verified_payments WHERE event_id=?', (receipt.event_id,)).fetchone()
        return bool(row and json.loads(row[0]) == asdict(receipt))
    ledger = Ledger(store, environment='test', namespace='agent', clock=lambda:ticks[0],
                    authorize=lambda t,c,a: authority[0] and c.payer_user_id==user==c.actor_user_id,
                    verify_payment=verify, max_hold_seconds=100)
    sale = PriceSnapshot('fixture-sale', 'sale', UNIT, 'deepseek', 'fixture-confirmed-route',
                          Rate(2), Rate(1), Rate(3), 'fixture-terms', STAMP, per_tokens=1)
    ledger.publish_price(sale)
    ledger.publish_price(replace(sale, price_id='fixture-cost', purpose='cost', unit=Unit('FIXTURE-GATEWAY-COST',8)))
    ledger.create_payment_order(order_id='fixture-order', payer_user_id=user, provider='fixture-payment',
        merchant_id='fixture-merchant', amount_minor=1000, currency='XTS', unit=UNIT, credit_units=1000,
        exchange_version='fixture-exchange')
    receipt=PaymentReceipt('test','fixture-payment','fixture-merchant','fixture-transaction','fixture-event',
                           'fixture-order',1000,'XTS','fixture-inbox','a'*64)
    store.execute('INSERT INTO fixture_verified_payments VALUES (?,?)', (receipt.event_id,json.dumps(asdict(receipt))))
    ledger.credit_verified_payment(receipt)
    ctx=ChargeContext(user,user,'fixture-run','fixture-budget-consent')
    route=Route('fixture-route-v1','fixture-requested-model','fixture-confirmed-route','fixture-confirmation',
                'fixture-dispatch-time-cost-contract','fixture-sale','fixture-cost',100,32,STAMP+1000)
    holder={'body':complete()}
    async def respond(request):
        seen.append(request)
        assert store.one('SELECT status FROM billing_attempts')['status']=='dispatch_unknown'
        with store.conn() as conn:
            assert not conn.in_transaction
        assert balance().held==297
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Wire(holder['body']))
    model=MeteredDeepSeek(ledger,current_authority=lambda c: authority[0],transport=httpx.MockTransport(respond))
    model.open_run(ctx,route=route,budget=1000,max_calls=3,deadline=STAMP+500)
    def balance():
        return ledger.balance_for_user(requester_user_id=user,payer_user_id=user,unit=UNIT)
    args=dict(context=ctx,logical_call_id='step-1',credential=Credential('platform','fixture-key-not-real',route.requested_model, provider='deepseek'),
              system=[{'type':'text','text':'private-fixture-system'}],tools=[],messages=[{'role':'user','content':'fixture question'}],
              effort='high',on_text=lambda text:None,should_stop=lambda:stopped[0])
    return SimpleNamespace(store=store,ledger=ledger,model=model,route=route,context=ctx,args=args,ticks=ticks,
        authority=authority,stopped=stopped,seen=seen,balance=balance,holder=holder,sale=sale)


def test_reserved_dispatch_and_separate_cost_settle_once(gateway):
    g=gateway
    result=g.model.step(**g.args)
    assert len(g.seen)==1 and g.balance().booked==958 and g.balance().held==0
    body=json.loads(g.seen[0].content)
    assert body['max_tokens']==32 and body['user_id']!=g.context.payer_user_id
    observation=g.store.one('SELECT evidence FROM agent_model_observations')['evidence']
    assert 'private-fixture' not in observation and 'fixture-key' not in observation
    assert result.replay_message['reasoning_content']=='private-fixture-reasoning'
    parsed=json.loads(observation)
    assert parsed['returned_model']=='fixture-returned-label'
    assert parsed['confirmed_route']=='fixture-confirmed-route'
    usage=g.store.one('SELECT * FROM billing_usage')
    assert usage['charge']==42 and usage['cost_unit']=='FIXTURE-GATEWAY-COST'
    with pytest.raises(BillingError, match='model_call_already_recorded'):
        g.model.step(**g.args)
    assert len(g.seen)==1
    assert g.model.recover()==0
    assert len(g.store.all('SELECT * FROM billing_settlements'))==1


def test_unknown_usage_holds_and_stops_next_step_until_final_deadline(gateway):
    g=gateway; g.holder['body']=event({'content':'partial'})
    with pytest.raises(DeepSeekError,match='模型请求未完成'):
        g.model.step(**g.args)
    assert g.balance().held==297 and g.balance().booked==1000
    with pytest.raises(BillingError,match='usage_pending'):
        g.model.step(**(g.args|{'logical_call_id':'step-2'}))
    assert len(g.seen)==1
    g.ticks[0]+=100
    g.model.recover()
    assert g.balance().held==0 and g.balance().booked==1000
    assert g.store.one('SELECT metering_state FROM agent_metered_calls')['metering_state']=='released'


def test_cancellation_after_dispatch_before_handoff_is_not_resent(gateway,monkeypatch):
    g=gateway; dispatch=g.ledger.dispatch
    def fence_then_cancel(ticket, **kwargs):
        dispatch(ticket, **kwargs); g.stopped[0]=True
    monkeypatch.setattr(g.ledger,'dispatch',fence_then_cancel)
    with pytest.raises(DeepSeekCancelled):
        g.model.step(**g.args)
    assert g.seen==[] and g.balance().held==297
    assert g.store.one('SELECT status FROM billing_attempts')['status']=='dispatch_unknown'
    g.stopped[0]=False
    with pytest.raises(BillingError,match='usage_pending'):
        g.model.step(**g.args)


def test_revoked_authority_before_fence_sends_nothing(gateway,monkeypatch):
    g=gateway; original=g.ledger.prepare_attempt
    def revoke(*a,**kw):
        ticket=original(*a,**kw); g.authority[0]=False;return ticket
    monkeypatch.setattr(g.ledger,'prepare_attempt',revoke)
    with pytest.raises(DeepSeekError) as exc:
        g.model.step(**g.args)
    assert exc.value.code=='provider_dispatch_blocked'
    assert g.seen==[] and g.balance().held==0
    assert g.store.all('SELECT * FROM billing_usage')==[]


def test_saved_final_usage_survives_settlement_crash_and_recovery(gateway,monkeypatch):
    g=gateway; original=g.ledger.settle_trusted_usage
    def fail(*a,**kw):
        raise BillingError('fixture_database_failure')
    monkeypatch.setattr(g.ledger,'settle_trusted_usage',fail)
    with pytest.raises(BillingError,match='fixture_database_failure'):
        g.model.step(**g.args)
    assert g.balance().held==297 and len(g.seen)==1
    monkeypatch.setattr(g.ledger,'settle_trusted_usage',original)
    g.authority[0]=False  # Recording already incurred facts must survive revocation.
    assert g.model.recover()==1 and g.model.recover()==0
    assert g.balance().booked==958 and len(g.seen)==1


def test_invalid_tool_arguments_with_final_usage_records_platform_cost(gateway):
    g=gateway
    g.args['tools']=[{'name':'fixture_lookup','input_schema':{'type':'object','properties':{}}}]
    g.holder['body']=event({'tool_calls':[{'index':0,'id':'fixture-tool','type':'function',
        'function':{'name':'fixture_lookup','arguments':'not json'}}]})+event(finish='tool_calls',usage=USAGE)+b'data: [DONE]\n\n'
    with pytest.raises(DeepSeekError):
        g.model.step(**g.args)
    assert g.balance().booked==1000 and g.balance().held==0
    assert g.store.one('SELECT outcome FROM billing_usage')['outcome']=='platform_fault'
    assert g.store.all('SELECT * FROM billing_settlements')==[]


def test_fixed_route_prices_and_expiry_refuse_without_network(gateway):
    g=gateway
    with pytest.raises(BillingError,match='run_route_conflict'):
        g.model.open_run(g.context,route=replace(g.route,version='changed'),budget=1000,max_calls=3,deadline=STAMP+500)
    g.ticks[0]=g.route.valid_until
    with pytest.raises(BillingError,match='route_confirmation_expired'):
        g.model.step(**g.args)
    assert g.seen==[]


def test_source_request_change_under_same_identity_conflicts(gateway):
    g=gateway;g.model.step(**g.args)
    with pytest.raises(BillingError,match='idempotency_conflict'):
        g.model.step(**(g.args|{'messages':[{'role':'user','content':'changed'}]}))
    assert len(g.seen)==1


def test_funds_or_nested_transaction_denial_cannot_send(gateway):
    g=gateway
    with g.store.tx():
        with pytest.raises(BillingError,match='billing_requires_own_transaction'):
            g.model.step(**g.args)
    assert g.seen==[] and g.balance().held==0


def test_invalid_or_absent_raw_usage_not_stored_as_zero_or_private_text(gateway):
    g=gateway
    g.holder['body']=event(finish='stop',usage=USAGE|{'prompt_tokens':'private-fixture-secret'})+b'data: [DONE]\n\n'
    g.model.step(**g.args)
    assert g.store.all('SELECT * FROM billing_usage')==[] and g.balance().held==297
    evidence=g.store.one('SELECT evidence FROM agent_model_observations')['evidence']
    assert 'private-fixture-secret' not in evidence
    assert json.loads(evidence)['usage_reliability']=='unreliable'


def test_real_agent_tool_confirmation_preserves_private_native_history_and_bills_each_attempt(gateway,monkeypatch):
    """Real Agent and Forms services; only model/payment network boundaries are fixtures."""
    from synwevo_suite.app import create_app
    from synwevo_suite.agent.gateway import MeteredTurnProvider
    g=gateway
    monkeypatch.setenv('SYNWEVO_SUITE_APPS','forms,agent')
    monkeypatch.setenv('SYNWEVO_AGENT_MODEL',g.route.requested_model)
    api=create_app(str(g.store.root),start_jobs=False)
    agent=api.state.agent
    monkeypatch.setattr(agent,'_submit',lambda turn_id:None)  # deterministic worker scheduling only
    agent.provider=MeteredTurnProvider(g.model, credential_resolver=lambda *_: g.args['credential'])
    uid=g.context.actor_user_id
    identity=api.state.identity
    user=identity.get_user(uid)
    g.store.execute('UPDATE users SET email_verified_at=1 WHERE id=?',(uid,))
    wid=identity.create_workspace('Metered isolated workspace',owner_id=uid)['id']
    g.store.execute("INSERT INTO agent_workspace_policies VALUES (?,'platform',?,?)",(wid,uid,STAMP))
    api.state.capabilities.invoke("agent.workspace_policy.set", {"processing": "platform", "providers": ["deepseek"]},
                                  user_id=uid, workspace_id=wid, idempotency_key="metering-policy-consent")
    conversation=agent.create_conversation(uid,'Metered fixture',[wid])
    turn=agent.start_turn(user,conversation['id'],'Create the authorized test form',parent_id=None,regenerate_of=None)
    context=replace(g.context,run_id=turn['turn_id'])
    g.model.open_run(context,route=g.route,budget=1000,max_calls=3,deadline=STAMP+500)
    requests=[]
    async def respond(request):
        requests.append(json.loads(request.content))
        assert g.store.one("SELECT COUNT(*) AS n FROM billing_attempts WHERE status='dispatch_unknown'")['n']==1
        if len(requests)==1:
            body=event({'reasoning_content':'private-native-fixture','tool_calls':[{'index':0,'id':'fixture-create',
                'type':'function','function':{'name':'run_operation','arguments':json.dumps({
                    'operation':'forms.form.create','workspace_id':wid,'input':{'title':'Metered form'}})}}]})+\
                event(finish='tool_calls',usage=USAGE)+b'data: [DONE]\n\n'
        else:
            previous=[m for m in requests[-1]['messages'] if m['role']=='assistant'][-1]
            assert previous['reasoning_content']=='private-native-fixture'
            assert any(m['role']=='tool' and m['tool_call_id']=='fixture-create' for m in requests[-1]['messages'])
            body=complete().replace(b'fixture-upstream-1',b'fixture-upstream-2')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Wire(body))
    g.model.transport=httpx.MockTransport(respond)
    try:
        agent._run(turn['turn_id'])
        assert g.store.one('SELECT status FROM agent_turns WHERE id=?',(turn['turn_id'],))['status']=='waiting_confirmation'
        assert g.store.all('SELECT * FROM forms')==[]
        call=g.store.one('SELECT * FROM agent_tool_calls WHERE turn_id=?',(turn['turn_id'],))
        agent.decide(user,call['id'],confirm=True,confirmation=call['input_hash'])
        agent._run(turn['turn_id'])
        assert g.store.one('SELECT status FROM agent_turns WHERE id=?',(turn['turn_id'],))['status']=='succeeded'
        assert g.store.one('SELECT title FROM forms')['title']=='Metered form'
        public=agent.conversation_json(uid,agent.conversation(uid,conversation['id']),full=True)
        assert 'private-native-fixture' not in json.dumps(public)
        assert 'private-fixture-reasoning' not in json.dumps(public)
        assert len(g.store.all('SELECT * FROM billing_usage'))==2
        assert len(g.store.all('SELECT * FROM billing_settlements'))==2
        assert g.balance().booked==916 and g.balance().held==0
        assert public['messages'][-1]['turn']['usage']=={'input':26,'output':14,'state':'known'}
        agent.recover()
        assert len(requests)==2 and len(g.store.all('SELECT * FROM forms'))==1
        from synwevo_core.errors import ServiceError
        agent.provider=SimpleNamespace(name='different-provider', resolve_credential=lambda store, user_id: g.args['credential'])
        with pytest.raises(ServiceError) as refused:
            agent.start_turn(user,conversation['id'],'Continue',parent_id=None,regenerate_of=None)
        assert refused.value.code=='provider_history_incompatible'
        assert len(requests)==2
    finally:
        agent.close()
