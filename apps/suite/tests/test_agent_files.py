"""Personal attachment lifecycle on real Store/ACL/admission/ledger. The tiny reader and model
are explicit TEST DOUBLES; real document formats are tested in Docs and integration tests."""
import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from synwevo_core import agent_records, quarantine
from synwevo_core.capabilities import canonical
from synwevo_core.errors import ServiceError
from synwevo_core.store import now, Store
from synwevo_suite.agent.files import Files, MAX_BYTES
from .test_agent import agent, op, say, CSRF  # noqa: F401
from .test_agent_governance_boundaries import governed, advance  # noqa: F401
from .test_agent_provenance import shown
from .test_agent_metering import gateway  # noqa: F401
from .test_agent_run_quotes import paid  # noqa: F401


class Reader:
    """Small test parser, independent of the product's injected Docs reader."""
    def describe(self):
        return {'context_formats': ['txt'], 'scanner': {'status': 'not_configured'}}

    def extract_context(self, data, filename):
        return {'status': 'ready', 'format': 'txt', 'tables': [],
                'lines': [{'text': data.decode('utf-8'), 'source': {'line': 1}}]}


class RecognizingReader(Reader):
    """TEST DOUBLE of a reader whose deployment recognizes images: it reports so and expects the
    worker to ask for recognition explicitly (the real reader is synwevo_docs.ingest)."""
    def describe(self):
        return {'context_formats': ['txt', 'png'], 'context_ocr': True, 'context_ocr_languages': ['eng'],
                'context_limits': {'ocr_pages': 5}, 'scanner': {'status': 'not_configured'}}

    def extract_context(self, data, filename, ocr=False):
        assert ocr is True, 'the worker must ask for recognition when the reader offers it'
        return {'status': 'ready', 'format': 'png', 'tables': [],
                'ocr': {'available': True, 'applied': True, 'engine': 'fixture', 'pages': 1, 'words': 2, 'low_confidence_words': 1},
                'lines': [{'text': 'SYNTHETIC RECOGNIZED', 'source': {'page': 1, 'line': 1, 'recognized': True, 'confidence': 61}}]}


#: a one-pixel PNG (test fixture bytes; the double above never reads them)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


def ready(files, uid, key='fixture-file', data=b'PRIVATE SYNTHETIC INPUT'):
    files.reader = Reader()
    item = files.upload(uid, 'fixture.txt', data, key)
    assert item['status'] == 'queued'
    files.tick()
    item = files.get(uid,item['id'])
    assert item['status'] == 'ready', item
    return item


def turn(g, ref, **extra):
    cid = op(g.client,'agent.conversation.create',{'scope':[]})['output']['id']
    t = op(g.client,'agent.turn.start',{'conversation_id':cid,'text':'Read my synthetic file','file_refs':[ref],**extra})['output']
    return cid,t


def test_real_http_upload_ownership_download_csrf_and_idempotency(governed):
    g=governed; f=g.engine.files;f.reader=Reader()
    r=g.client.post('/api/suite/agent/files',files={'file':('fixture.txt',b'fixture content')},headers=CSRF|{'Idempotency-Key':'u1'})
    assert r.status_code==200,r.text
    item=r.json();assert item['status']=='queued' and item['scan_status']=='not_configured'
    r2=g.client.post('/api/suite/agent/files',files={'file':('fixture.txt',b'fixture content')},headers=CSRF|{'Idempotency-Key':'u1'})
    assert r2.json()['id']==item['id']
    f.tick();item=f.get(g.people.uid('alice'),item['id'])
    response=g.client.get(item['download_path'])
    assert response.content==b'fixture content' and response.headers['content-disposition'].startswith('attachment;')
    assert response.headers['content-type']=='application/octet-stream'
    g.client.sign_in('oscar')
    assert g.client.get(item['download_path']).status_code==404
    op(g.client,'agent.file.get',{'file_id':item['id']},expect=404)
    from synwevo_core.platform_access import PlatformAccess
    PlatformAccess(g.store).host_grant(g.people.uid('out'), 'super_admin', source='fixture:agent-files', reason='isolated ownership check')
    g.client.sign_in('out')
    assert g.client.get(item['download_path']).status_code==404
    g.client.sign_in('alice')
    assert g.client.post('/api/suite/agent/files',files={'file':('fixture.txt',b'x')},headers={'Idempotency-Key':'no-csrf'}).status_code==403
    assert g.store.one('SELECT COUNT(*) AS n FROM agent_files')['n']==1


def test_same_key_conflicts_and_concurrent_upload_keeps_one_record(governed):
    g=governed;f=g.engine.files;f.reader=Reader();uid=g.people.uid('alice')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:f.upload(uid,'fixture.txt',b'same','same'),range(2)))
    assert len({r['id'] for r in results})==1
    with pytest.raises(ServiceError) as exc:f.upload(uid,'fixture.txt',b'changed','same')
    assert exc.value.code=='idempotency_conflict'
    assert g.store.one('SELECT COUNT(*) AS n FROM agent_files')['n']==1


def test_personal_context_reaches_model_and_deletion_hides_history_stream_and_cached_run(governed):
    g=governed;uid=g.people.uid('alice');f=g.engine.files;item=ready(f,uid)
    g.model.steps=[say('PRIVATE SYNTHETIC ANSWER')]
    cid,t=turn(g,item['reference'])
    assert advance(g,t)['status']=='succeeded'
    assert 'PRIVATE SYNTHETIC INPUT' in canonical(g.model.requests[0]['messages'])
    saved=shown(g,cid);assert not any(m['hidden'] for m in saved['messages'])
    source=json.loads(g.store.one("SELECT sources_json FROM agent_messages WHERE role='user'")['sources_json'])[0]
    assert source['workspace_id'] is None and source['file_id']==item['id']
    assert g.engine._source_workspaces({'sources_json':canonical([source])})==set()
    assert saved['messages'][0]['contexts'][0]['kind']=='file'
    old=op(g.client,'agent.file.get',{'file_id':item['id']})
    op(g.client,'agent.file.delete',{'file_id':item['id']})
    gone=shown(g,cid);assert gone['sealed']=='access_changed' and all(m['hidden'] for m in gone['messages'])
    assert all(not m['contexts'] and not m['body'] for m in gone['messages'])
    assert all(not e['data']['delta'] for e in g.engine.events_after(uid,cid,0) if e['type']=='text')
    assert g.engine.message_hidden(uid,cid,g.store.one('SELECT * FROM agent_messages WHERE id=?',(t['assistant_message_id'],)))
    assert 'PRIVATE SYNTHETIC INPUT' not in g.store.one("SELECT api_json FROM agent_messages WHERE role='user'")['api_json']
    with pytest.raises(ServiceError):g.engine.caps._check_result(g.store.one('SELECT * FROM op_runs WHERE id=?',(old['run_id'],)))
    assert g.client.get(item['download_path']).status_code==409
    assert g.store.one('SELECT content FROM agent_files WHERE id=?',(item['id'],))['content'] is None
    assert not list((g.store.root/'blobs').glob('*/*')), 'personal uploads create no shared-storage orphan'


@pytest.mark.parametrize('change',['delete','quarantine','missing'])
def test_dependency_loss_after_enqueue_stops_dispatch_without_model(governed,change):
    g=governed;uid=g.people.uid('alice');f=g.engine.files;item=ready(f,uid)
    g.model.steps=[say('should not run')];cid,t=turn(g,item['reference'])
    row=f.row(uid,item['id'])
    if change=='delete':f.delete(uid,item['id'])
    elif change=='quarantine':quarantine.hold(g.store,row['content_hash'],b'PRIVATE SYNTHETIC INPUT',signature='fixture',engine='test',rows=[],by='test')
    else:g.store.execute('UPDATE agent_files SET content=NULL WHERE id=?',(item['id'],))
    result=advance(g,t)
    assert result['status']=='failed' and not g.model.requests
    assert shown(g,cid)['sealed']=='access_changed'


def test_expiry_is_atomic_and_only_unreferenced_files_expire(governed):
    g=governed;uid=g.people.uid('alice');f=g.engine.files
    used=ready(f,uid);other=ready(f,uid,'other')
    cid,_=turn(g,used['reference'])
    g.store.execute('UPDATE agent_files SET expires_at=0')
    f.sweep()
    assert f.get(uid,used['id'])['status']=='ready'
    assert f.get(uid,other['id'])['status']=='deleted'
    assert not shown(g,cid)['sealed']


def test_worker_restart_lease_and_cancel_discard_late_parse(governed,monkeypatch):
    from synwevo_suite.agent import files as mod
    g=governed;uid=g.people.uid('alice');f=g.engine.files;f.reader=Reader()
    item=f.upload(uid,'fixture.txt',b'input','restart')
    g.store.execute("UPDATE agent_files SET status='parsing',lease_token='old',lease_until=0,attempts=1 WHERE id=?",(item['id'],))
    recovered=Files(g.engine,Reader());recovered.tick()
    assert recovered.get(uid,item['id'])['status']=='ready' and recovered.get(uid,item['id'])['attempts']==2
    second=f.upload(uid,'fixture.txt',b'input','cancel-during')
    def cancel(fn,args,check):
        f.cancel(uid,second['id'])
        check()
        raise AssertionError('cancel must interrupt')
    monkeypatch.setattr(mod,'run_bounded',cancel)
    f.tick()
    assert f.get(uid,second['id'])['status']=='cancelled'
    assert f.row(uid,second['id'])['parsed_json'] is None
    monkeypatch.undo()
    f.retry(uid,second['id']);f.tick()
    assert f.get(uid,second['id'])['status']=='ready'


def test_limits_scan_failure_no_blob_retry_limit_and_stale_upload(governed,monkeypatch):
    from synwevo_suite.agent import files as mod
    g=governed;f=g.engine.files;f.reader=Reader();uid=g.people.uid('alice')
    with pytest.raises(ServiceError) as e:f.upload(uid,'fixture.txt',b'x'*(MAX_BYTES+1),'large')
    assert e.value.code=='agent_file_limit'
    def reject(*args):raise ServiceError(503,'scan_unavailable','fixture unavailable')
    monkeypatch.setattr(mod,'accept_upload',reject)
    with pytest.raises(ServiceError):f.upload(uid,'fixture.txt',b'input','scan-down')
    row=g.store.one('SELECT * FROM agent_files');assert row['status']=='failed' and row['content'] is None
    with pytest.raises(ServiceError):f.retry(uid,row['id'])
    monkeypatch.undo()
    item=ready(f,uid,'parse-max');g.store.execute("UPDATE agent_files SET status='parsing',attempts=3,lease_until=0 WHERE id=?",(item['id'],))
    f.tick();assert f.get(uid,item['id'])['error_code']=='parse_interrupted'
    g.store.execute("UPDATE agent_files SET status='receiving',lease_until=0 WHERE id=?",(row['id'],))
    f.sweep();assert f.get(uid,row['id'])['error_code']=='upload_interrupted'


def test_account_erasure_and_idempotent_migration_include_files(governed):
    g=governed;uid=g.people.uid('alice');f=g.engine.files;item=ready(f,uid)
    op(g.client,'agent.file.get',{'file_id':item['id']})
    Store(g.store.root)
    assert f.get(uid,item['id'])['status']=='ready'
    with g.store.tx():agent_records.erase(g.store,uid)
    assert not g.store.all('SELECT * FROM agent_files')
    assert all(r['output'] is None for r in g.store.all("SELECT output FROM op_runs WHERE user_id=? AND app='agent'",(uid,)))


def test_quoted_file_binding_cannot_be_dropped_and_consent_carries_exact_reference(paid):
    g=paid;f=g.agent.files;item=ready(f,g.user['id']);ref=item['reference']
    quote=g.issue(file_refs=[ref])
    with pytest.raises(ServiceError) as exc:
        g.agent.start_turn(g.user,g.conversation['id'],'fixture question',parent_id=None,regenerate_of=None,quote_id=quote['id'])
    assert exc.value.code=='billing_quote_context_changed'
    assert not g.seen and not g.store.all('SELECT * FROM agent_messages')
    with g.store.tx():consent=g.quotes.consents.create(g.user['id'],quote['id'],max_runs=2,expires_at=None,until_revoked=True)
    t=g.agent.start_turn(g.user,g.conversation['id'],'fixture question',parent_id=None,regenerate_of=None,consent_id=consent['id'],file_refs=[ref])
    g.agent._run(t['turn_id'])
    assert len(g.seen)==1 and g.balance().booked==958
    assert g.store.one('SELECT binding_digest FROM agent_model_quotes WHERE accepted_turn_id=?',(t['turn_id'],))['binding_digest']==g.quotes._row(g.user['id'],quote['id'])['binding_digest']


@pytest.mark.parametrize('bad', ['duplicate','snapshot','combined_limit'])
def test_invalid_file_context_is_refused_before_admission(governed,bad):
    g=governed;item=ready(g.engine.files,g.people.uid('alice'));ref=item['reference']
    cid=op(g.client,'agent.conversation.create')['output']['id']
    refs=[ref,ref] if bad=='duplicate' else [dict(ref,snapshot='0'*64)] if bad=='snapshot' else [ref]*6
    r=op(g.client,'agent.turn.start',{'conversation_id':cid,'text':'fixture','file_refs':refs},expect=409 if bad=='snapshot' else 422)
    assert r['error'] in ('invalid_context','agent_file_changed','invalid_input')
    assert not g.store.all('SELECT * FROM agent_messages') and not g.model.requests


def test_file_delete_invalidates_waiting_write_confirmation(governed):
    from .test_agent import call
    from .test_agent_governance_boundaries import policy
    g=governed;uid=g.people.uid('alice');item=ready(g.engine.files,uid)
    policy(g,g.people.a,'platform')
    cid=op(g.client,'agent.conversation.create')['output']['id']
    g.model.steps=[call('run_operation',{'operation':'forms.form.create','workspace_id':g.people.a,'input':{'title':'Created from personal fixture'}})]
    t=op(g.client,'agent.turn.start',{'conversation_id':cid,'text':'Create a form from my file','file_refs':[item['reference']]})['output']
    assert advance(g,t)['status']=='waiting_confirmation'
    card=g.store.one('SELECT * FROM agent_tool_calls WHERE turn_id=?',(t['turn_id'],))
    g.engine.files.delete(uid,item['id'])
    result=op(g.client,'agent.tool_call.confirm',{'tool_call_id':card['id'],'confirmation':card['input_hash']},expect=409)
    assert result['error']=='source_access_changed'
    assert not g.store.all('SELECT * FROM forms')


def test_suspended_agent_cannot_upload_retry_or_finish_parse_but_can_delete(governed):
    from synwevo_core.agent_safety import AgentAccess
    g=governed;uid=g.people.uid('alice');f=g.engine.files;f.reader=Reader()
    item=f.upload(uid,'fixture.txt',b'fixture','before-suspend')
    AgentAccess(g.store).suspend(uid,g.people.uid('out'),reason='isolated fixture access suspension')
    with pytest.raises(ServiceError) as e:f.upload(uid,'fixture.txt',b'fixture','after-suspend')
    assert e.value.code=='agent_access_suspended'
    f.tick();assert f.get(uid,item['id'])['status']=='failed'
    with pytest.raises(ServiceError):f.retry(uid,item['id'])
    assert f.delete(uid,item['id'])['status']=='deleted'


def test_expired_unattached_file_cannot_be_read_or_attached_before_sweeper(governed):
    from synwevo_suite.agent.context import Contexts
    g=governed;uid=g.people.uid('alice');f=g.engine.files;item=ready(f,uid)
    g.store.execute('UPDATE agent_files SET expires_at=0 WHERE id=?',(item['id'],))
    assert f.get(uid,item['id'])['error_code']=='agent_file_expired'
    with pytest.raises(ServiceError) as e:f.download(uid,item['id'])
    assert e.value.code=='agent_file_expired'
    conv=g.engine.create_conversation(uid,'Expired fixture',None)
    with pytest.raises(ServiceError) as e:
        Contexts(g.engine).prepare(uid,g.engine.conversation(uid,conv['id']),[],file_refs=[item['reference']])
    assert e.value.code=='agent_file_expired'
    assert not g.store.all('SELECT * FROM agent_messages')


def test_expiry_during_parse_discards_result(governed,monkeypatch):
    from synwevo_suite.agent import files as mod
    g=governed;uid=g.people.uid('alice');f=g.engine.files;f.reader=Reader()
    item=f.upload(uid,'fixture.txt',b'input','expires-during')
    def expire(fn,args,check):
        parsed=fn(*args)
        g.store.execute('UPDATE agent_files SET expires_at=0 WHERE id=?',(item['id'],))
        return parsed
    monkeypatch.setattr(mod,'run_bounded',expire)
    f.tick();row=f.row(uid,item['id'])
    assert row['status']=='failed' and row['error_code']=='agent_file_expired' and row['parsed_json'] is None


def test_legacy_account_erase_and_rollback_still_clear_new_file_rows(governed):
    g=governed;uid=g.people.uid('alice');item=ready(g.engine.files,uid)
    with g.store.tx():
        g.store.execute("UPDATE users SET status='pending_delete' WHERE id=?",(uid,))
    assert g.store.one('SELECT parsed_json FROM agent_files WHERE id=?',(item['id'],))['parsed_json']
    with g.store.tx():
        # The old account process finalizes with this existing user contract and has no
        # agent_files cleanup. The DB must erase new rows without requiring a new worker.
        g.store.execute("UPDATE users SET status='deleted' WHERE id=?",(uid,))
    assert not g.store.all('SELECT * FROM agent_files WHERE user_id=?',(uid,))


def test_recognized_files_are_asked_for_explicitly_and_marked_to_model_and_person(governed):
    g=governed;uid=g.people.uid('alice');f=g.engine.files;f.reader=RecognizingReader()
    caps=f.capabilities();assert caps['ocr'] is True and caps['ocr_pages']==5 and caps['ocr_languages']==['eng'] and 'png' in caps['formats']
    item=f.upload(uid,'fixture.png',PNG,'fixture-image');assert item['status']=='queued'
    f.tick();item=f.get(uid,item['id']);assert item['status']=='ready',item
    assert item['extraction']['ocr']['applied'] is True and item['extraction']['lines'][0]['source']['recognized'] is True
    g.model.steps=[say('SYNTHETIC ANSWER')];cid,t=turn(g,item['reference']);assert advance(g,t)['status']=='succeeded'
    payload=json.loads(g.store.one("SELECT api_json FROM agent_messages WHERE role='user'")['api_json'])
    assert payload['items'][0]['kind']=='file' and payload['items'][0]['recognized'] is True
    assert shown(g,cid)['messages'][0]['contexts'][0]=={**shown(g,cid)['messages'][0]['contexts'][0],'kind':'file','recognized':True}
    # A reader without the engine is never asked (it may not know the flag) and marks nothing.
    plain=ready(f,uid,'fixture-text');assert f.capabilities()['ocr'] is False and plain['extraction'].get('ocr') is None
