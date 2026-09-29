"""Independent H regressions: real login, OIDC endpoints and business tenant isolation.

Every user, project and protocol row is an isolated temporary fixture. No callbacks are
registered, so revocation cannot send network traffic. RP cookies below are issued by
the real Sessions service from a real completed OIDC grant's client-session linkage.
"""
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.integration.platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from synwevo_core.oidc import Clients
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions

PW = 'Isolated device testing passphrase 2026!'
REDIRECT = 'http://localhost/auth/callback'
VERIFIER = 'synthetic-proof-key-for-local-session-review-only-12345678'


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / 'session-review'
    for key, value in {'DATA_DIR': str(root), 'UI_AUTH': 'session', 'COOKIE_SECURE': '0',
                       'MAIL_MODE': 'file', 'MAIL_DIR': str(tmp_path / 'mail'),
                       'ISSUER': 'http://testserver', 'TEST_INSTANCE': '1'}.items():
        monkeypatch.setenv('SYNWEVO_' + key, value)
    from synwevo_server.app import create_app
    app = create_app(str(root), role='all', start_jobs=False)
    ident = app.state.identity
    user = ident.create_user('boundary@example.test', PW, status='active')
    other = ident.create_user('outsider@example.test', PW, status='active')
    own = ident.workspaces_for(user['id'])[0]['id']
    organization = Organizations(ident.store).create(user['id'], 'Isolated review org', 'Tab B fixture', 'review-org-create')
    Clients(ident.store).register(client_id='review-web', app_id='docweave', name='Isolated fixture client',
                                 redirect_uris=[REDIRECT], scopes='openid profile email offline_access')
    clients = []
    def login():
        client = TestClient(app)
        clients.append(client)
        response = client.post('/api/auth/login', json={'email': user['email'], 'password': PW})
        assert response.status_code == 200, response.text
        return client
    def rp(central, *, legacy=False):
        grant = exchange(central, authorize(central))
        assert grant.status_code == 200, grant.text
        root_id = central.get('/api/auth/me').json()['session']['id']
        cs = ident.store.one('SELECT * FROM client_sessions WHERE central_session_id=? ORDER BY created_at DESC LIMIT 1', (root_id,))
        raw, row = Sessions(ident.store).create(user['id'], workspace_id=own, amr='password',
                    oidc_sid=cs['sid'], central_session_id=None if legacy else root_id)
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, raw)
        client.cookies.set(CSRF_COOKIE, 'fixture-csrf-proof')
        clients.append(client)
        assert client.get('/api/auth/me').status_code == 200
        return client, row, grant.json()
    yield {'app': app, 'ident': ident, 'store': ident.store, 'user': user, 'own': own,
           'selected': organization['workspace']['id'], 'oid': organization['organization']['id'],
           'outsider': ident.workspaces_for(other['id'])[0]['id'], 'login': login, 'rp': rp}
    for client in clients:
        client.close()


def post(client, path, body=None, headers=None):
    return client.post(path, json=body or {}, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or '', **(headers or {})})


def authorize(client):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
    response = client.get('/oidc/authorize', params={'client_id': 'review-web', 'redirect_uri': REDIRECT,
        'response_type': 'code', 'scope': 'openid offline_access', 'state': 'fixture', 'nonce': 'fixture',
        'code_challenge': challenge, 'code_challenge_method': 'S256'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    query = parse_qs(urlsplit(response.headers['location']).query)
    assert 'code' in query, query
    return query['code'][0]


def exchange(client, code):
    return client.post('/oidc/token', data={'grant_type': 'authorization_code', 'client_id': 'review-web',
                      'redirect_uri': REDIRECT, 'code': code, 'code_verifier': VERIFIER})


@pytest.mark.parametrize('legacy', [False, True])
def test_logout_rp_revokes_its_device_and_grant_but_preserves_other_device(world, legacy):
    central, another = world['login'](), world['login']()
    rp, _, grant = world['rp'](central, legacy=legacy)
    sibling, _, _ = world['rp'](central)
    another_rp, _, another_grant = world['rp'](another)
    assert post(rp, '/api/auth/logout').status_code == 200
    assert central.get('/api/auth/me').status_code == 401
    assert sibling.get('/api/auth/me').status_code == 401
    assert another.get('/api/auth/me').status_code == 200
    assert another_rp.get('/api/auth/me').status_code == 200
    assert another.get('/oidc/userinfo', headers={'Authorization': 'Bearer ' + grant['access_token']}).status_code == 401
    assert another.get('/oidc/userinfo', headers={'Authorization': 'Bearer ' + another_grant['access_token']}).status_code == 200


@pytest.mark.parametrize('withdrawal', ['logout', 'password_reset'])
def test_h07_credential_withdrawal_invalidates_unspent_authorization_code(world, withdrawal):
    client = world['login']()
    code = authorize(client)
    if withdrawal == 'logout':
        assert post(client, '/api/auth/logout').status_code == 200
    else:
        assert post(client, '/api/auth/forgot', {'email': world['user']['email']}).status_code == 200
        mail = world['store'].one("SELECT body FROM mail_outbox WHERE kind='reset'")
        token = re.search(r'[#?]token=([A-Za-z0-9_-]+)', mail['body'])[1]
        assert post(client, '/api/auth/reset', {'token': token, 'password': 'Replacement grant testing passphrase 2026!'}).status_code == 200
    response = exchange(client, code)
    if response.status_code == 200:
        accessible = client.get('/oidc/userinfo', headers={'Authorization': 'Bearer ' + response.json()['access_token']}).status_code
    else:
        accessible = None
    assert response.status_code == 400, f'H07: exchanging a code after {withdrawal} returned {response.status_code}; userinfo={accessible}'


def test_h08_end_other_device_selected_by_rp_session_revokes_its_root(world):
    current, other = world['login'](), world['login']()
    rp, row, grant = world['rp'](other)
    response = current.delete('/api/auth/sessions/' + row['id'], headers={CSRF_HEADER: current.cookies.get(CSRF_COOKIE)})
    assert response.status_code == 200
    assert rp.get('/api/auth/me').status_code == 401
    root_status = other.get('/api/auth/me').status_code
    token_status = current.get('/oidc/userinfo', headers={'Authorization': 'Bearer ' + grant['access_token']}).status_code
    assert (root_status, token_status) == (401, 401), f'H08: selected RP session ended, but root={root_status}, token={token_status}'
    assert current.get('/api/auth/me').status_code == 200


def test_revoke_other_sessions_leaves_current_central_session_usable(world):
    current, other = world['login'](), world['login']()
    other_rp, _, grant = world['rp'](other)
    assert post(current, '/api/auth/sessions/revoke-others').status_code == 200
    assert current.get('/api/auth/me').status_code == 200
    assert other.get('/api/auth/me').status_code == 401
    assert other_rp.get('/api/auth/me').status_code == 401
    assert current.get('/oidc/userinfo', headers={'Authorization': 'Bearer ' + grant['access_token']}).status_code == 401


def test_cookie_logout_requires_csrf(world):
    client = world['login']()
    response = client.post('/api/auth/logout', content=b'{}', headers={'Origin': 'http://untrusted.testserver', 'Sec-Fetch-Site': 'same-site'})
    assert response.status_code == 403
    assert client.get('/api/auth/me').status_code == 200


def test_workspace_headers_isolate_two_tabs_without_mutating_cookie_default(world):
    client = world['login']()
    sid = client.get('/api/auth/me').json()['session']['id']
    original = world['store'].one('SELECT workspace_id FROM sessions WHERE id=?', (sid,))['workspace_id']
    projects = {}
    for name, wid in [('Tab A isolated project', world['own']), ('Tab B isolated project', world['selected'])]:
        response = post(client, '/api/projects', {'name': name}, {'X-Synwevo-Workspace': wid})
        assert response.status_code in (200, 201), response.text
        projects[wid] = response.json()['id']
    for wid, project_id in projects.items():
        headers = {'X-Synwevo-Workspace': wid}
        assert client.get('/api/auth/me', headers=headers).json()['workspace_id'] == wid
        rows = client.get('/api/projects', headers=headers).json()
        assert [row['id'] for row in rows] == [project_id]
        other_id = next(value for key, value in projects.items() if key != wid)
        assert client.get('/api/projects/' + other_id, headers=headers).status_code == 404
    assert world['store'].one('SELECT workspace_id FROM sessions WHERE id=?', (sid,))['workspace_id'] == original
    for wid in [world['outsider'], 'missing-fixture-workspace']:
        assert client.get('/api/projects', headers={'X-Synwevo-Workspace': wid}).status_code == 403

    make_platform_admin(world['store'], world['user']['id'])
    assert client.get('/api/projects', headers={'X-Synwevo-Workspace': world['outsider']}).status_code == 403


def test_pending_cookie_workspace_hint_cannot_unlock_business_or_oidc_grant(world):
    client = world['login']()
    world['store'].execute("UPDATE users SET status='pending',email_verified_at=NULL WHERE id=?", (world['user']['id'],))
    response = client.get('/api/projects', headers={'X-Synwevo-Workspace': world['own']})
    assert response.status_code == 403 and response.json()['error'] == 'email_verification_required'
    challenge = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b'=').decode()
    response = client.get('/oidc/authorize', params={'client_id': 'review-web', 'redirect_uri': REDIRECT,
        'response_type': 'code', 'scope': 'openid', 'state': 'fixture', 'code_challenge': challenge,
        'code_challenge_method': 'S256'}, follow_redirects=False)
    assert response.status_code == 303
    assert 'code' not in parse_qs(urlsplit(response.headers['location']).query)
    assert world['store'].one('SELECT COUNT(*) AS n FROM oauth_codes')['n'] == 0


@pytest.mark.parametrize('withdrawal', ['org_membership', 'workspace_membership', 'freeze_org'])
def test_workspace_header_rechecks_live_authority(world, withdrawal):
    client = world['login']()
    wid, uid = world['selected'], world['user']['id']
    assert client.get('/api/projects', headers={'X-Synwevo-Workspace': wid}).status_code == 200
    if withdrawal == 'org_membership':
        world['store'].execute('DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?', (world['oid'], uid))
    elif withdrawal == 'workspace_membership':
        world['store'].execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?', (wid, uid))
    else:
        Organizations(world['store']).transition(world['oid'], uid, 'freeze', reason='Isolated security review fixture')
    assert client.get('/api/projects', headers={'X-Synwevo-Workspace': wid}).status_code == 403
    assert client.get('/api/projects', headers={'X-Synwevo-Workspace': world['own']}).status_code == 200
