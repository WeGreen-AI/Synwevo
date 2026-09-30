"""Real Extract -> confirmed revision -> DataPrep, using isolated, declared CSV fixtures.

The reader below is the existing test-only CSV reader, not a production fallback or model.
All templates, uploads, extraction, corrections, confirmations and processing use real suite
services/operations and tmp_path storage. Fault injection is identified at its point of use.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from synwevo_core.capabilities import fingerprint
from .test_extract import CsvReader

CSRF = {'X-Synwevo-CSRF': 'fixture-csrf'}
CSV = b'code,amount\n000127,12345678901234567890.12\n000128,0.20\n'
FIELDS = ['code', 'amount', 'invoice', 'optional']
DEFINITION = {'fields': [
    {'id': 'code', 'label': 'Fixture identifier', 'many': True,
     'rule': {'kind': 'column', 'header': 'code'}},
    {'id': 'amount', 'label': 'Fixture amount', 'type': 'decimal', 'many': True,
     'rule': {'kind': 'column', 'header': 'amount'}},
    {'id': 'invoice', 'label': 'Fixture header copied to each detail',
     'rule': {'kind': 'cell', 'row': 2, 'column': 1}},
    {'id': 'optional', 'label': 'Absent optional source field', 'required': False,
     'rule': {'kind': 'cell', 'row': 2, 'column': 3}},
]}


def op(c, operation, payload=None, *, key=None, ws=None, status=200):
    response = c.post('/api/suite/ops/' + operation, headers=CSRF,
        json={'workspace_id': ws or c.people.a, 'input': payload or {},
              **({'idempotency_key': key} if key else {})})
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def ex(suite):
    suite.sign_in('alice')
    suite.api.state.extract.reader = CsvReader()
    return suite


def extracted(c, *, data=CSV, definition=None, confirm=True):
    tpl = op(c, 'extract.template.create', {'title': 'Isolated DataPrep source fixture',
                 'definition': definition or DEFINITION})['output']['id']
    op(c, 'extract.template.publish', {'template_id': tpl, 'draft_rev': 1})
    upload = c.post('/api/suite/extract/files', params={'workspace_id': c.people.a},
        headers={**CSRF, 'Idempotency-Key': tpl}, files={'file': ('fixture.csv', data, 'text/csv')})
    assert upload.status_code == 200, upload.text
    tid = op(c, 'extract.task.create', {'template_id': tpl, 'version': 1,
             'file_id': upload.json()['id']})['output']['id']
    c.api.state.extract.tick()
    task = op(c, 'extract.task.get', {'task_id': tid})['output']
    assert task['status'] == 'waiting_input', task
    revision = task['revision']
    if confirm:
        revision = op(c, 'extract.task.confirm', {'task_id': tid, 'revision': revision,
                        'note': 'Human review of the isolated CSV fixture'})['output']['revision']
    return {'task_id': tid, 'revision': revision}


def import_args(*refs, field_ids=None):
    return {'name': 'Confirmed fixture import', 'tasks': list(refs),
            'field_ids': FIELDS if field_ids is None else field_ids, 'detail_policy': 'zip_strict'}


def recipe(c, steps=None):
    created = op(c, 'dataprep.recipe.create', {'name': 'Fixture validation',
                 'definition': {'steps': steps or []}})['output']
    op(c, 'dataprep.recipe.publish', {'recipe_id': created['id'], 'draft_rev': 1})
    return created['id']


def start(c, did, *, steps=None, key=None):
    return op(c, 'dataprep.run.start', {'recipe_id': recipe(c, steps), 'version': 1,
               'inputs': {'main': did}}, key=key)


def get_dataset(c, did):
    return op(c, 'dataprep.dataset.get', {'dataset_id': did})['output']


def export(c, did, format='json'):
    return c.get(f'/api/suite/dataprep/datasets/{did}/export',
                 params={'workspace_id': c.people.a, 'format': format})


def test_confirmed_csv_import_process_precision_provenance_and_idempotency(ex):
    ref = extracted(ex)
    payload = import_args(ref)
    imported = op(ex, 'dataprep.dataset.from_extract', payload, key='confirmed-import')
    d = imported['output']
    assert set(d) == {'id', 'workspace_id', 'name', 'origin', 'source', 'columns',
                      'row_count', 'column_count', 'content_hash'}
    assert d['row_count'] == 2 and d['column_count'] == 4 and d['columns'] == FIELDS
    assert d['source']['kind'] == 'extract' and d['source']['workspace_id'] == ex.people.a
    result = op(ex, 'extract.task.result', ref)['output']
    source = d['source']['tasks'][0]
    assert source == {**ref, 'template_id': result['template_id'], 'template_version': 1,
        'definition_digest': result['definition_digest'], 'file_id': result['file']['id'],
        'content_digest': result['file']['content_digest'], 'result_fingerprint': fingerprint(result)}
    data = get_dataset(ex, d['id'])
    assert data['rows'] == [
        {'values': {'code': '000127', 'amount': '12345678901234567890.12', 'invoice': '000127'},
         'origins': [[ref['task_id'], 1, ref['revision']]]},
        {'values': {'code': '000128', 'amount': '0.20', 'invoice': '000127'},
         'origins': [[ref['task_id'], 2, ref['revision']]]},
    ]
    replay = op(ex, 'dataprep.dataset.from_extract', payload, key='confirmed-import')
    assert replay['replayed'] and replay['run_id'] == imported['run_id'] and replay['output'] == d
    assert op(ex, 'dataprep.dataset.from_extract', {**payload, 'field_ids': ['amount']},
              key='confirmed-import', status=409)['error'] == 'idempotency_conflict'
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 1
    assert ex.people.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='dataprep.dataset.created'")['n'] == 1
    run = start(ex, d['id'], steps=[
        {'id': 'money', 'type': 'cast', 'column': 'amount', 'to': 'decimal', 'scale': 2},
        {'id': 'threshold', 'type': 'validate', 'condition': {'column': 'amount', 'op': 'gt',
          'numeric': True, 'value': '1.00'}, 'message': 'Fixture amount is too small', 'on_error': 'keep'},
    ])
    assert ex.api.state.dataprep.tick()
    done = op(ex, 'dataprep.run.get', {'run_id': run['output']['id']})['output']
    assert done['status'] == 'partial' and done['counts'] == {'output_rows': 2, 'issues': 1}
    issues = op(ex, 'dataprep.run.issues', {'run_id': done['id']})['output']['items']
    assert issues[0]['input_name'] == ref['task_id'] and issues[0]['source_row'] == 2
    assert issues[0]['params']['origins'] == [[ref['task_id'], 2, ref['revision']]]
    output = get_dataset(ex, done['output_dataset_id'])
    assert output['rows'] == data['rows']
    assert output['source']['inputs']['main']['dataset_id'] == d['id']
    assert export(ex, output['id']).json()['rows'] == data['rows']
    csv = export(ex, output['id'], 'csv')
    assert csv.status_code == 200 and '12345678901234567890.12' in csv.text and '000127' in csv.text
    receipt = ex.get('/api/suite/runs/' + run['run_id']).json()
    assert not receipt['snapshot_hidden'] and receipt['output']['output_dataset_id'] == output['id']


def test_fixed_ids_scalar_empty_and_missing_are_preserved(ex):
    ref = extracted(ex)
    correction = op(ex, 'extract.task.correct', {**ref, 'values': {'optional': ''},
                    'note': 'Reviewed empty optional fixture value'})['output']
    confirmation = op(ex, 'extract.task.confirm', {'task_id': ref['task_id'],
        'revision': correction['revision'], 'note': 'Confirmed the empty value',
        'acknowledge_unverified': True})['output']
    current = {**ref, 'revision': confirmation['revision']}
    # A single selected scalar yields one row, independent of unselected repeated fields.
    d = op(ex, 'dataprep.dataset.from_extract', import_args(current, field_ids=['optional', 'invoice']))['output']
    assert d['columns'] == ['optional', 'invoice'] and d['row_count'] == 1
    assert get_dataset(ex, d['id'])['rows'][0]['values'] == {'optional': '', 'invoice': '000127'}
    # Renaming a draft label does not affect fixed field IDs or the task's published template.
    tid = d['source']['tasks'][0]['template_id']
    renamed = copy.deepcopy(DEFINITION)
    renamed['fields'][2]['label'] = 'Changed draft label'
    op(ex, 'extract.template.save', {'template_id': tid, 'title': 'Renamed fixture',
        'draft_rev': 1, 'definition': renamed})
    assert get_dataset(ex, d['id'])['rows'][0]['values']['invoice'] == '000127'


def test_multiple_tasks_and_repeated_length_policy_are_atomic(ex):
    good = extracted(ex)
    bad = extracted(ex, data=b'code,amount\n000127,1.00\n000128,\n')
    assert op(ex, 'dataprep.dataset.from_extract', import_args(good, bad),
              status=422)['error'] == 'extract_detail_length'
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 0
    # Explicit human correction restores equal lengths, retaining machine evidence/history.
    corrected = op(ex, 'extract.task.correct', {**bad, 'values': {'amount': ['1.00', '0.00']},
                   'note': 'Fixture second line amount checked'})['output']
    confirmed = op(ex, 'extract.task.confirm', {'task_id': bad['task_id'], 'revision': corrected['revision'],
                   'note': 'Fixture details reviewed', 'acknowledge_unverified': True})['output']
    d = op(ex, 'dataprep.dataset.from_extract', import_args(good, {**bad, 'revision': confirmed['revision']}))['output']
    rows = get_dataset(ex, d['id'])['rows']
    assert d['row_count'] == 4
    assert [r['values']['amount'] for r in rows] == ['12345678901234567890.12', '0.20', '1.00', '0.00']
    assert [r['origins'][0][0] for r in rows] == [good['task_id']] * 2 + [bad['task_id']] * 2
    assert rows[-1]['origins'][0][2] == confirmed['revision']


def test_empty_detail_is_zero_rows_and_never_padded(ex):
    definition = copy.deepcopy(DEFINITION)
    definition['fields'][1]['required'] = False
    ref = extracted(ex, data=b'code,amount\n000127,\n', definition=definition)
    d = op(ex, 'dataprep.dataset.from_extract', import_args(ref, field_ids=['amount', 'invoice']))['output']
    assert d['row_count'] == 0 and get_dataset(ex, d['id'])['rows'] == []
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref), status=422)['error'] == 'extract_detail_length'


def test_missing_empty_and_explicit_null_survive_real_multi_source_processing(ex):
    ref = extracted(ex)
    columns = ['invoice', 'optional']
    missing = op(ex, 'dataprep.dataset.from_extract', import_args(ref, field_ids=columns))['output']['id']
    other = extracted(ex)
    correction = op(ex, 'extract.task.correct', {**other, 'values': {'optional': ''},
                    'note': 'Explicitly empty fixture value'})['output']
    confirmation = op(ex, 'extract.task.confirm', {'task_id': other['task_id'],
        'revision': correction['revision'], 'note': 'Reviewed empty value', 'acknowledge_unverified': True})['output']
    empty = op(ex, 'dataprep.dataset.from_extract', import_args(
        {**other, 'revision': confirmation['revision']}, field_ids=columns))['output']['id']
    # Extract currently only emits null for status=missing. A real independent record supplies
    # explicit null so we can distinguish all three states after shared engine processing.
    nullable = op(ex, 'dataprep.dataset.from_records', {'name': 'Isolated explicit null record',
        'columns': columns, 'records': [{'invoice': '000127', 'optional': None}]})['output']['id']
    rid = recipe(ex, [{'id': 'empty', 'type': 'append', 'source': 'empty'},
                      {'id': 'null', 'type': 'append', 'source': 'nullable'}])
    run = op(ex, 'dataprep.run.start', {'recipe_id': rid, 'version': 1,
        'inputs': {'main': missing, 'empty': empty, 'nullable': nullable}})['output']
    assert ex.api.state.dataprep.tick()
    done = op(ex, 'dataprep.run.get', {'run_id': run['id']})['output']
    assert done['status'] == 'succeeded'
    rows = export(ex, done['output_dataset_id']).json()['rows']
    assert [r['values'] for r in rows] == [
        {'invoice': '000127'}, {'invoice': '000127', 'optional': ''},
        {'invoice': '000127', 'optional': None}]
    assert rows[0]['origins'][0][0] == ref['task_id']
    assert rows[1]['origins'][0][0] == other['task_id']


def test_unconfirmed_unknown_field_wrong_revision_and_workspace_refused(ex):
    ref = extracted(ex, confirm=False)
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref), status=409)['error'] == 'result_not_confirmed'
    confirmed = op(ex, 'extract.task.confirm', {**ref, 'note': 'Checked fixture'})['output']
    ref['revision'] = confirmed['revision']
    assert op(ex, 'dataprep.dataset.from_extract', import_args({**ref, 'revision': 1}),
              status=409)['error'] == 'result_not_confirmed'
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref, field_ids=['renamed_label']),
              status=422)['error'] == 'extract_field_missing'
    ex.sign_in('oscar')
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref), ws=ex.people.b,
              status=404)['error'] == 'extract_not_found'
    ex.sign_in('vera')
    op(ex, 'dataprep.dataset.from_extract', import_args(ref), status=403)
    ex.sign_in('out')
    op(ex, 'dataprep.dataset.from_extract', import_args(ref), status=403)
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 0


@pytest.mark.parametrize('change', ['withdraw', 'reconfirm', 'access', 'fingerprint'])
def test_source_changes_block_all_read_export_worker_rerun_history_and_replay(ex, change):
    ref = extracted(ex)
    payload = import_args(ref)
    imported = op(ex, 'dataprep.dataset.from_extract', payload, key='retained-source')
    did = imported['output']['id']
    finished = start(ex, did)
    assert ex.api.state.dataprep.tick()
    done = op(ex, 'dataprep.run.get', {'run_id': finished['output']['id']})['output']
    derived = done['output_dataset_id']
    queued = start(ex, derived)
    cancellable = start(ex, derived)
    # Save a real authorized read receipt as well as both write receipts.
    read = op(ex, 'dataprep.dataset.get', {'dataset_id': derived})
    if change in ('withdraw', 'reconfirm'):
        corrected = op(ex, 'extract.task.correct', {**ref, 'values': {'amount': ['1.00', '0.20']},
                       'note': 'Actual later human correction'})['output']
        if change == 'reconfirm':
            op(ex, 'extract.task.confirm', {'task_id': ref['task_id'], 'revision': corrected['revision'],
                'note': 'Accept new fixture revision', 'acknowledge_unverified': True})
    elif change == 'access':
        # Revoke Extract only; DataPrep workspace access stays valid.
        ex.people.store.execute("UPDATE apps SET status='disabled' WHERE id='extract'")
    else:
        # Explicit storage-integrity fault injection: same revision but changed source digest.
        ex.people.store.execute('UPDATE extract_files SET content_digest=? WHERE id=?',
            ('0' * 64, imported['output']['source']['tasks'][0]['file_id']))
    denial = {'withdraw': 409, 'reconfirm': 409, 'access': 404, 'fingerprint': 403}[change]
    cancel_args = {'run_id': cancellable['output']['id']}
    stopped = op(ex, 'dataprep.run.cancel', cancel_args, key='stop-after-source-change')['output']
    assert stopped == {'id': cancel_args['run_id'], 'status': 'cancelled', 'cancel_requested': 1}
    assert op(ex, 'dataprep.run.cancel', cancel_args, key='stop-after-source-change')['output'] == stopped
    for target in (did, derived):
        op(ex, 'dataprep.dataset.get', {'dataset_id': target}, status=denial)
        op(ex, 'dataprep.dataset.issues', {'dataset_id': target}, status=denial)
        assert export(ex, target).status_code == denial
    for operation, args in [
        ('dataprep.dataset.list', {}), ('dataprep.run.list', {}),
        ('dataprep.recipe.preview', {'definition': {}, 'inputs': {'main': did}}),
        ('dataprep.run.get', {'run_id': done['id']}), ('dataprep.run.issues', {'run_id': done['id']}),
        ('dataprep.run.retry', {'run_id': done['id']}),
    ]:
        op(ex, operation, args, status=denial)
    assert ex.get(f"/api/suite/dataprep/runs/{done['id']}/issues",
                  params={'workspace_id': ex.people.a}).status_code == denial
    before = ex.people.store.one('SELECT COUNT(*) AS n FROM op_runs')['n']
    for receipt in (imported, finished, queued, read):
        historic = ex.get('/api/suite/runs/' + receipt['run_id']).json()
        assert historic['snapshot_hidden'] and historic['input'] is None
        assert historic['output'] is None and historic['object'] is None
    assert op(ex, 'dataprep.dataset.from_extract', payload, key='retained-source',
              status=409)['error'] == 'run_result_unavailable'
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM op_runs')['n'] == before
    assert ex.api.state.dataprep.tick()
    persisted = ex.people.store.one('SELECT status,output_dataset_id,error_code FROM dataprep_runs WHERE id=?',
                                   (queued['output']['id'],))
    assert persisted['status'] == 'failed' and persisted['output_dataset_id'] is None
    assert persisted['error_code'] in ('result_not_confirmed', 'app_unavailable', 'result_unavailable')
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 2


def test_import_is_a_versioned_flow_and_agent_capability_with_declared_limits(ex):
    ref = extracted(ex)
    operation = ex.caps.registry.get('dataprep.dataset.from_extract')
    description = operation.describe()
    assert description['version'] == 1 and description['agent'] == 'confirm'
    assert set(description['title']) == {'zh-Hans', 'zh-Hant', 'en'}
    assert operation.output_schema()['properties']['source']['$ref'].endswith('/ExtractSource')
    for via in ('flow', 'agent'):
        result = ex.caps.invoke(operation.id, import_args(ref), via=via, user_id=ex.people.uid('alice'),
                                workspace_id=ex.people.a, idempotency_key=via + '-fixture')
        assert get_dataset(ex, result['output']['id'])['row_count'] == 2
    # Input boundaries reject before any source resolution or dataset write.
    for payload in [
        import_args(ref, ref), import_args(ref, field_ids=['amount', 'amount']),
        import_args(ref, field_ids=[f'field_{n}' for n in range(65)]),
        import_args(*[{'task_id': f'extask_{n:012x}', 'revision': 2} for n in range(101)]),
        import_args({**ref, 'workspace_id': ex.people.b}),
        import_args({**ref, 'revision': 2.0}),
        {**import_args(ref), 'detail_policy': 'pad'},
    ]:
        assert op(ex, operation.id, payload, status=422)['error'] == 'invalid_input'


def test_unexpected_numeric_wire_values_are_not_coerced(ex, monkeypatch):
    ref = extracted(ex)
    inspect = ex.caps.inspect

    def faulty_wire(operation, payload, **context):
        result = inspect(operation, payload, **context)
        if operation == 'extract.task.result':
            # Protocol fault injection after a real confirmed read, never a success fixture.
            result['values']['amount']['value'] = [1.25, 2]
            result['review']['values'] = copy.deepcopy(result['values'])
        return result

    monkeypatch.setattr(ex.caps, 'inspect', faulty_wire)
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref),
              status=422)['error'] == 'extract_value_invalid'
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 0


@pytest.mark.parametrize(('budget', 'limit', 'error'), [
    ('MAX_CELLS', 7, 'table_limit'), ('MAX_ROWS', 1, 'table_limit'),
    ('MAX_TABLE_BYTES', 200, 'extract_source_limit'),
])
def test_resource_budget_failure_is_atomic_without_truncation(ex, monkeypatch, budget, limit, error):
    from synwevo_suite.dataprep import service
    ref = extracted(ex)
    # Scale budgets down for a real two-row import instead of allocating pathological fixtures.
    monkeypatch.setattr(service, budget, limit)
    assert op(ex, 'dataprep.dataset.from_extract', import_args(ref), status=422)['error'] == error
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 0
    assert ex.people.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='dataprep.dataset.created'")['n'] == 0


def test_recovered_worker_rechecks_confirmation_at_result_commit(ex, monkeypatch):
    from synwevo_suite.dataprep import service
    ref = extracted(ex)
    imported = op(ex, 'dataprep.dataset.from_extract', import_args(ref))['output']
    queued = start(ex, imported['id'])['output']
    execute = service.execute

    def correct_after_processing(*args, **kwargs):
        result = execute(*args, **kwargs)
        # A real human correction at the deterministic compute/commit seam models revocation
        # while the job is in flight; no mocked engine result or hand-written business record.
        ex.caps.invoke('extract.task.correct', {**ref, 'values': {'amount': ['1.00', '0.20']},
            'note': 'Correction while the fixture worker is executing'}, via='api',
            workspace_id=ex.people.a, user_id=ex.people.uid('alice'))
        return result

    monkeypatch.setattr(service, 'execute', correct_after_processing)
    recovered = service.DataPrep(ex.people.store, ex.people.ident, ex.caps)
    assert recovered.tick()
    persisted = ex.people.store.one('SELECT status,output_dataset_id,error_code FROM dataprep_runs WHERE id=?',
                                   (queued['id'],))
    assert persisted == {'status': 'failed', 'output_dataset_id': None, 'error_code': 'result_not_confirmed'}
    assert ex.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 1
    assert ex.people.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='dataprep.run.succeeded'")['n'] == 0


def test_suite_and_sdk_imports_are_from_this_worktree():
    import synwevo_core
    import synwevo_suite
    import synwevo_docs_sdk
    import synwevo_auth_client
    root = Path(__file__).resolve().parents[3]
    for module in (synwevo_core, synwevo_suite, synwevo_docs_sdk, synwevo_auth_client):
        assert Path(module.__file__).resolve().is_relative_to(root), module.__file__
