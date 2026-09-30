"""Isolated quarantine/restore faults must not expose bytes or keep unwanted work alive."""
import hashlib

import pytest
from synwevo_core import quarantine
from .test_dataprep import op, recipe, start, finish

DATA = b'id,amount\n000127,123456789.01\n'


def imported(c):
    c.sign_in('bob')
    args = {'params': {'workspace_id': c.people.a},
            'headers': {'X-Synwevo-CSRF': 'fixture-csrf', 'Idempotency-Key': 'hold-upload'},
            'files': {'file': ('isolated.csv', DATA, 'text/csv')}}
    upload = c.post('/api/suite/dataprep/files', **args)
    assert upload.status_code == 200, upload.text
    fid = upload.json()['id']
    payload = {'file_id': fid, 'name': 'Private source title'}
    d = op(c, 'dataset.import', payload, key='hold-import')
    return args, fid, payload, d


def hold(c):
    digest = hashlib.sha256(DATA).hexdigest()
    quarantine.hold(c.people.store, digest, DATA, signature='isolated-verdict',
                    engine='test-injection', rows=[], by='test')
    assert c.people.store.get_blob(digest) == DATA  # restored bytes do not withdraw a verdict


def test_held_source_blocks_reads_derived_exports_replay_upload_and_worker(suite):
    args, fid, payload, d = imported(suite)
    r = recipe(suite)
    done = finish(suite, start(suite, r, d))
    queued = start(suite, r, {'id': done['output_dataset_id']})
    hold(suite)
    for did in (d['id'], done['output_dataset_id']):
        assert op(suite, 'dataset.get', {'dataset_id': did}, status=409)['error'] == 'file_quarantined'
        out = suite.get(f'/api/suite/dataprep/datasets/{did}/export', params={'workspace_id': suite.people.a})
        assert out.status_code == 409 and out.json()['error'] == 'file_quarantined'
    assert op(suite, 'file.inspect', {'file_id': fid}, status=409)['error'] == 'file_quarantined'
    assert op(suite, 'dataset.import', payload, key='hold-import', status=409)['error'] == 'run_result_unavailable'
    assert op(suite, 'dataset.import', payload, key='new-held-import', status=409)['error'] == 'file_quarantined'
    reupload = suite.post('/api/suite/dataprep/files', **args)
    assert reupload.status_code == 409 and reupload.json()['error'] == 'file_quarantined'
    assert suite.api.state.dataprep.tick()
    row = suite.people.store.one('SELECT status,error_code,output_dataset_id FROM dataprep_runs WHERE id=?', (queued['id'],))
    assert row == {'status': 'failed', 'error_code': 'file_quarantined', 'output_dataset_id': None}
    assert suite.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 2


@pytest.mark.parametrize('change', ['quarantine', 'lost_source'])
def test_cancel_after_source_loss_is_authorized_and_never_returns_source_content(suite, change):
    _, _, _, d = imported(suite)
    queued = start(suite, recipe(suite), d)
    if change == 'quarantine':
        hold(suite)
    else:
        # Dataset metadata survives an isolated source-storage failure.
        suite.people.store.execute('DELETE FROM dataprep_files WHERE id=?', (d['file_id'],))
    suite.sign_in('vera')
    op(suite, 'run.cancel', {'run_id': queued['id']}, status=403)
    suite.sign_in('bob')
    args = {'run_id': queued['id']}
    stopped = op(suite, 'run.cancel', args, key='stop-without-source')
    assert stopped == {'id': queued['id'], 'status': 'cancelled', 'cancel_requested': 1}
    assert op(suite, 'run.cancel', args, key='stop-without-source') == stopped
    receipt = suite.people.store.one("SELECT id FROM op_runs WHERE op='dataprep.run.cancel' AND status='succeeded'")
    history = suite.get('/api/suite/runs/' + receipt['id']).json()
    assert history['output'] == stopped and not history.get('snapshot_hidden')
    assert not suite.api.state.dataprep.tick()
    assert suite.people.store.one('SELECT COUNT(*) AS n FROM dataprep_datasets')['n'] == 1
