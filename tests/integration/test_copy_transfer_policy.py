"""CR-045: browser, delegated API and delayed publication respect source policy.

Synthetic accounts use real composed services. Only the claim-boundary test injects
an interleaving; it changes policy through the existing versioned policy service.
"""
import pytest

from synwevo_core import data_flow
from tests.integration.test_project_copy import acting, reauth, world  # noqa: F401
from tests.integration.test_api_copy_requests import api, delegated_token, _approved_execution_fixture


def policy(w, value):
    store, source = w['ident'].store, w['personal']['alice']
    data_flow.set_workspace_outbound(store, source, value, actor_id=w['users']['alice']['id'],
                                    revision=data_flow.workspace_outbound(store, source)['revision'])


@pytest.mark.parametrize('via', ['browser', 'api'])
@pytest.mark.parametrize('outbound,code', [('none', 'transfer_source_closed'),
                                         ('audience', 'transfer_audience_denied'),
                                         ('organization', 'transfer_organization_denied')])
def test_request_refused_before_manifest_is_sent_to_target(world, via, outbound, code):
    w = world
    alice = acting(w['c']['alice'], w['personal']['alice'])
    pid = alice.post('/api/projects', json={'name': 'Private synthetic draft'}).json()['id']
    headers = delegated_token(w, 'alice', w['personal']['alice']) if via == 'api' else {}
    client, prefix = (api(w), '/api/v1') if via == 'api' else (alice, '/api')
    policy(w, outbound)
    response = client.post(f'{prefix}/projects/{pid}/copy-requests',
                           json={'target_workspace_id': w['wid']}, headers=headers)
    assert response.status_code == 403, response.text
    error = response.json()['error']
    assert (error['code'] if via == 'api' else error) == code
    store = w['ident'].store
    assert store.one('SELECT COUNT(*) AS n FROM project_copies')['n'] == 0
    assert store.one("SELECT COUNT(*) AS n FROM job_events WHERE type='copy.requested'")['n'] == 0
    assert not w['api'].state.notifier.list(w['users']['bob']['id'])['items']


@pytest.mark.parametrize('boundary', ['before_run', 'after_claim'])
def test_approved_copy_rechecks_policy_in_publication_transaction(world, monkeypatch, boundary):
    w = world
    runner, pid, cid, _ = _approved_execution_fixture(w)
    if boundary == 'before_run':
        policy(w, 'none')
    else:
        original = runner.copies.claim_approved
        def claim_then_close(copy_id):
            claim = original(copy_id)
            policy(w, 'none')
            return claim
        monkeypatch.setattr(runner.copies, 'claim_approved', claim_then_close)
    result = runner.executor.run(cid)
    assert result['status'] == 'stale' and result['error'] == 'transfer:transfer_source_closed', result
    assert result['result'] is None
    store = w['ident'].store
    assert store.one('SELECT COUNT(*) AS n FROM projects WHERE origin=?', ('copy:'+cid,))['n'] == 0
    assert [e['type'] for e in store.all("SELECT type FROM job_events WHERE type LIKE 'copy.%' ORDER BY rowid")] == [
        'copy.requested', 'copy.approved', 'copy.stale']
    assert runner.executor.run(cid)['status'] == 'stale'
    assert store.one("SELECT COUNT(*) AS n FROM job_events WHERE type='copy.stale'")['n'] == 1


def test_pending_approval_does_not_override_policy_but_rejection_remains_available(world):
    w = world
    alice = acting(w['c']['alice'], w['personal']['alice'])
    pid = alice.post('/api/projects', json={'name': 'Private synthetic draft'}).json()['id']
    request = alice.post(f'/api/projects/{pid}/copy-requests', json={'target_workspace_id': w['wid']}).json()
    policy(w, 'none')
    bob = w['c']['bob']; reauth(bob)
    path = f"/api/auth/organizations/{w['oid']}/approvals/{request['id']}/decide"
    response = bob.post(path, json={'action': 'approve', 'reason': 'Target still wants it'})
    assert response.status_code == 403, response.text
    assert response.json()['error'] == 'transfer_source_closed'
    assert w['api'].state.copy_runner.copies.get(request['id'])['status'] == 'pending'
    assert bob.post(path, json={'action': 'reject', 'reason': 'Source policy prohibits it'}).json()['status'] == 'rejected'


def test_completed_copy_remains_independent_after_source_policy_closes(world):
    w = world
    runner, pid, cid, _ = _approved_execution_fixture(w)
    done = runner.executor.run(cid)
    assert done['status'] == 'done', done
    policy(w, 'none')
    copy_id = done['result']['project_id']
    response = acting(w['c']['bob'], w['wid']).get(f'/api/projects/{copy_id}')
    assert response.status_code == 200
    assert runner.executor.run(cid)['result']['project_id'] == copy_id
    assert w['ident'].store.one('SELECT COUNT(*) AS n FROM projects WHERE origin=?', ('copy:'+cid,))['n'] == 1


@pytest.mark.parametrize('loss', ['membership', 'workspace_inactive', 'workspace_deleted'])
def test_copy_review_rechecks_target_access_for_list_and_detail(world, loss):
    w = world
    alice = acting(w['c']['alice'], w['personal']['alice'])
    pid = alice.post('/api/projects', json={'name': 'Private manifest fixture'}).json()['id']
    request = alice.post(f'/api/projects/{pid}/copy-requests', json={'target_workspace_id': w['wid']}).json()
    path = f"/api/auth/organizations/{w['oid']}/approvals"
    reviewer = w['c']['carol']  # Organization admin and target viewer: can read, cannot decide.
    assert reviewer.get(f"{path}/{request['id']}").json()['can_decide'] is False
    store = w['ident'].store
    if loss == 'membership':
        store.execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?', (w['wid'], w['users']['carol']['id']))
    elif loss == 'workspace_inactive':
        store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (w['wid'],))
    else:
        store.execute('UPDATE workspaces SET deleted_at=1 WHERE id=?', (w['wid'],))
    assert reviewer.get(path).json()['items'] == []
    response = reviewer.get(f"{path}/{request['id']}")
    assert response.status_code == 404, response.text
    assert 'Private manifest fixture' not in response.text


def test_audience_policy_does_not_expose_manifest_to_organization_only_reviewers(world):
    w = world
    store = w['ident'].store
    # The target and source contain only Alice. Bob remains organization owner, Carol admin.
    store.execute('DELETE FROM memberships WHERE workspace_id=? AND user_id<>?', (w['wid'], w['users']['alice']['id']))
    policy(w, 'audience')
    alice = acting(w['c']['alice'], w['personal']['alice'])
    pid = alice.post('/api/projects', json={'name': 'Private audience fixture'}).json()['id']
    response = alice.post(f'/api/projects/{pid}/copy-requests', json={'target_workspace_id': w['wid']})
    assert response.status_code == 201, response.text
    cid = response.json()['id']
    path = f"/api/auth/organizations/{w['oid']}/approvals"
    for user in ('bob', 'carol'):
        assert w['c'][user].get(path).json()['items'] == []
        assert w['c'][user].get(f'{path}/{cid}').status_code == 404


def test_copy_review_filters_before_limit(world):
    w = world
    store = w['ident'].store
    alice = acting(w['c']['alice'], w['personal']['alice'])
    pid = alice.post('/api/projects', json={'name': 'Visible fixture'}).json()['id']
    visible = alice.post(f'/api/projects/{pid}/copy-requests', json={'target_workspace_id': w['wid']}).json()
    other = w['ident'].create_workspace('Other private target', owner_id=w['users']['alice']['id'])
    store.execute('UPDATE workspaces SET organization_id=? WHERE id=?', (w['oid'], other['id']))
    second = alice.post(f'/api/projects/{pid}/copy-requests', json={'target_workspace_id': other['id']})
    assert second.status_code == 201, second.text
    path = f"/api/auth/organizations/{w['oid']}/approvals?limit=1"
    assert [r['id'] for r in w['c']['carol'].get(path).json()['items']] == [visible['id']]
