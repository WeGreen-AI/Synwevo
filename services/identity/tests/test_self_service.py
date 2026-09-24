"""Self-service against the real identity app; isolated Store and file mail fixtures only."""
from __future__ import annotations

import base64
import binascii
import re
import struct
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_identity.profile import canonical_avatar, finalize_deletions

PW = 'isolated-account-fixture-passphrase-123'


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)


def png(*, width=1, height=1, pixels=b'\x00\xff\x00\x00', meta=b''):
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + meta + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


@pytest.fixture()
def account(tmp_path, monkeypatch):
    monkeypatch.setenv('SYNWEVO_DATA_DIR', str(tmp_path / 'fixture-data'))
    monkeypatch.setenv('SYNWEVO_UI_AUTH', 'session')
    monkeypatch.setenv('SYNWEVO_COOKIE_SECURE', '0')
    monkeypatch.setenv('SYNWEVO_MAIL_MODE', 'file')
    monkeypatch.setenv('SYNWEVO_MAIL_DIR', str(tmp_path / 'fixture-mail'))
    monkeypatch.setenv('SYNWEVO_PUBLIC_URL', 'http://testserver')
    from synwevo_identity.app import create_app
    app = create_app(str(tmp_path / 'fixture-data'), start_jobs=False)
    ident = app.state.identity
    user = ident.create_user('selfservice@example.test', PW, display_name='Test fixture', status='active')
    ident.store.execute('UPDATE users SET email_verified_at=? WHERE id=?', (time.time(), user['id']))
    with TestClient(app) as client:
        assert client.post('/api/auth/login', json={'email': user['email'], 'password': PW}).status_code == 200
        yield {'app': app, 'c': client, 'ident': ident, 'store': ident.store, 'user': user}


def send(account, method, path, body=None, client=None):
    c = client or account['c']
    kwargs = {'headers': {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ''}}
    if body is not None:
        kwargs['json'] = body
    return c.request(method, '/api/auth' + path, **kwargs)


def mail_token(account, kind):
    row = account['store'].one('SELECT body FROM mail_outbox WHERE kind=? ORDER BY created_at DESC LIMIT 1', (kind,))
    assert row
    return re.search(r'[#?]token=([A-Za-z0-9_-]+)', row['body'])[1]


def login(account, email=None):
    c = TestClient(account['app'])
    response = c.post('/api/auth/login', json={'email': email or account['user']['email'], 'password': PW})
    assert response.status_code == 200, response.text
    return c


def test_png_is_canonical_and_metadata_removed():
    data = png(meta=chunk(b'tEXt', b'Comment\x00private metadata'))
    result = canonical_avatar(base64.b64encode(data).decode())
    assert b'private metadata' not in result and b'tEXt' not in result
    assert result == png()


@pytest.mark.parametrize('data', [b'<svg onload="alert(1)"/>', png(width=513), png(height=0),
                                 png(pixels=b'\x00' * 2000000), png(pixels=b'\x05\xff\x00\x00'),
                                 png() + b'trailing', png()[:-1], png(meta=chunk(b'acTL', b'animation'))])
def test_bad_avatars_are_refused_without_image_dependency(data):
    with pytest.raises(ServiceError) as exc:
        canonical_avatar(base64.b64encode(data).decode())
    assert exc.value.code == 'bad_avatar'


def test_profile_cannot_edit_identity_or_another_user(account):
    other = account['ident'].create_user('other@example.test', PW, status='active')
    response = send(account, 'PATCH', '/profile', {'display_name': 'New name', 'locale': 'en', 'timezone': 'Asia/Hong_Kong'})
    assert response.status_code == 200, response.text
    assert response.json()['locale'] == 'en' and response.json()['timezone'] == 'Asia/Hong_Kong'
    assert send(account, 'PATCH', '/profile', {'id': other['id'], 'platform_role': 'admin'}).status_code == 400
    assert account['ident'].get_user(other['id'])['display_name'] is None
    assert 'avatar_blob' not in account['c'].get('/api/auth/profile').json()
    assert TestClient(account['app']).get('/api/auth/profile').status_code == 401
    assert account['c'].patch('/api/auth/profile', json={'display_name': 'CSRF'}).status_code == 403


@pytest.mark.parametrize('body', [{'locale': 'xx'}, {'timezone': '../../etc/passwd'}, {'display_name': ''},
                                  {'display_name': 'a' * 81}, {'display_name': '\x00bad'}, {'locale': []}, {'email': 'evil@example.test'}])
def test_profile_validation(account, body):
    assert send(account, 'PATCH', '/profile', body).status_code == 400


def test_avatar_is_private_and_removable(account):
    response = send(account, 'PUT', '/profile/avatar', {'data': base64.b64encode(png()).decode()})
    assert response.status_code == 200, response.text
    avatar = account['c'].get('/api/auth/profile/avatar')
    assert avatar.content == png() and avatar.headers['content-type'] == 'image/png'
    assert 'no-store' in avatar.headers['cache-control']
    assert TestClient(account['app']).get('/api/auth/profile/avatar').status_code == 401
    assert send(account, 'DELETE', '/profile/avatar').status_code == 200
    assert account['c'].get('/api/auth/profile/avatar').status_code == 404


def test_email_change_requires_password_and_verification(account):
    assert send(account, 'POST', '/email-change', {'email': 'new@example.test', 'password': 'wrong'}).status_code == 401
    response = send(account, 'POST', '/email-change', {'email': 'new@example.test', 'password': PW})
    assert response.status_code == 200, response.text
    token = mail_token(account, 'email_change')
    assert account['ident'].get_user(account['user']['id'])['email'] == account['user']['email']
    # GET is a scanner-safe page load, never an address mutation.
    assert send(account, 'GET', '/email-change/confirm').status_code in (404, 405)
    other = account['ident'].create_user('other@example.test', PW, status='active')
    other_client = login(account, other['email'])
    assert send(account, 'POST', '/email-change/confirm', {'token': token, 'password': PW}, other_client).status_code == 410
    response = send(account, 'POST', '/email-change/confirm', {'token': token, 'password': PW})
    assert response.status_code == 200, response.text
    assert account['c'].get('/api/auth/profile').status_code == 401
    assert account['ident'].get_user(account['user']['id'])['email'] == 'new@example.test'
    notice = account['store'].one("SELECT to_addr FROM mail_outbox WHERE kind='security'")
    assert notice['to_addr'] == account['user']['email']
    assert send(account, 'POST', '/email-change/confirm', {'token': token, 'password': PW}, login(account, 'new@example.test')).status_code == 410


@pytest.mark.parametrize('status', ['active', 'pending', 'suspended', 'pending_delete', 'deleted'])
def test_email_change_does_not_steal_existing_identity(account, status):
    account['ident'].create_user('reserved@example.test', PW, status=status)
    response = send(account, 'POST', '/email-change', {'email': 'RESERVED@example.test', 'password': PW})
    assert response.status_code == 409


def test_email_confirmation_rechecks_uniqueness_and_rolls_back_mail_failure(account, monkeypatch):
    assert send(account, 'POST', '/email-change', {'email': 'next@example.test', 'password': PW}).status_code == 200
    token = mail_token(account, 'email_change')
    monkeypatch.setenv('SYNWEVO_MAIL_MODE', 'disabled')
    assert send(account, 'POST', '/email-change/confirm', {'token': token, 'password': PW}).status_code == 503
    assert account['ident'].get_user(account['user']['id'])['email'] == account['user']['email']
    monkeypatch.setenv('SYNWEVO_MAIL_MODE', 'file')
    account['ident'].create_user('next@example.test', PW, status='pending')
    assert send(account, 'POST', '/email-change/confirm', {'token': token, 'password': PW}).status_code == 409


def test_email_change_revokes_address_bound_invites_and_reset_tokens(account):
    store, user = account['store'], account['user']
    for n, email in enumerate([user['email'], 'new@example.test']):
        store.execute('INSERT INTO invitations (id,email_key,workspace_id,role,token_hash,invited_by,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                      (f'fixture-invite-{n}', email, 'fixture-workspace', 'editor', f'fixture-hash-{n}', user['id'], time.time(), time.time()+1000))
    reset = account['app'].state.invites.issue_email_token(user['id'], 'reset', ttl_s=3600)
    assert send(account, 'POST', '/email-change', {'email': 'new@example.test', 'password': PW}).status_code == 200
    assert send(account, 'POST', '/email-change/confirm', {'token': mail_token(account, 'email_change'), 'password': PW}).status_code == 200
    assert all(row['revoked_at'] for row in store.all('SELECT revoked_at FROM invitations'))
    assert account['app'].state.invites.peek_email_token(reset, 'reset') is None


def test_export_and_activity_exclude_other_users_and_credentials(account):
    store, user = account['store'], account['user']
    other = account['ident'].create_user('other@example.test', PW, status='active')
    for uid, marker in [(user['id'], 'own-secret'), (other['id'], 'other-secret')]:
        store.execute('INSERT INTO audit_events (id,at,actor_kind,actor_id,action,result,meta) VALUES (?,?,?,?,?,?,?)',
                      (marker, time.time(), 'user', uid, 'auth.login', 'ok', marker))
    result = send(account, 'POST', '/export', {'password': PW})
    assert result.status_code == 200, result.text
    assert all(s not in result.text for s in ['own-secret', 'other-secret', 'other@example.test', 'phc', 'token_hash', 'secret_enc'])
    assert result.json()['profile']['id'] == user['id']
    assert 'no-store' in result.headers['cache-control']
    assert 'attachment' in result.headers['content-disposition']
    activity = account['c'].get('/api/auth/security-activity')
    assert activity.status_code == 200 and 'other-secret' not in activity.text and 'own-secret' not in activity.text


def test_delete_recovery_is_narrow_single_use_and_does_not_restore_sessions(account):
    old_session = account['c'].cookies.get(COOKIE_NAME)
    response = send(account, 'POST', '/deletion', {'password': PW})
    assert response.status_code == 200, response.text
    assert response.json()['deletion_due_at'] > time.time() + 6 * 86400
    token = mail_token(account, 'account_recovery')
    assert account['c'].get('/api/auth/profile').status_code == 401
    assert account['c'].post('/api/auth/login', json={'email': account['user']['email'], 'password': PW}).status_code == 401
    anonymous = TestClient(account['app'])
    preview = anonymous.post('/api/auth/deletion/recovery/preview', json={'token': token})
    assert preview.status_code == 200 and account['user']['email'] not in preview.text
    assert account['ident'].get_user(account['user']['id'])['status'] == 'pending_delete'
    response = anonymous.post('/api/auth/deletion/recovery', json={'token': token})
    assert response.status_code == 200, response.text
    assert COOKIE_NAME not in response.cookies
    assert anonymous.post('/api/auth/deletion/recovery', json={'token': token}).status_code == 410
    anonymous.cookies.set(COOKIE_NAME, old_session)
    assert anonymous.get('/api/auth/profile').status_code == 401
    assert login(account).get('/api/auth/profile').status_code == 200


def test_recovery_expired_or_suspended_never_reactivates(account):
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
    token = mail_token(account, 'account_recovery')
    account['ident'].set_status(account['user']['id'], 'suspended')
    assert TestClient(account['app']).post('/api/auth/deletion/recovery', json={'token': token}).status_code == 410
    account['ident'].set_status(account['user']['id'], 'pending_delete')
    account['store'].execute('UPDATE users SET deletion_due_at=? WHERE id=?', (time.time()-1, account['user']['id']))
    assert TestClient(account['app']).post('/api/auth/deletion/recovery', json={'token': token}).status_code == 410
    assert finalize_deletions(account['store']) == 1
    assert finalize_deletions(account['store']) == 0
    assert account['store'].one('SELECT status,avatar_blob FROM users WHERE id=?', (account['user']['id'],)) == {'status': 'deleted', 'avatar_blob': None}
    assert not account['store'].one('SELECT 1 FROM user_passwords WHERE user_id=?', (account['user']['id'],))


def test_last_platform_admin_cannot_delete(account):
    account['store'].execute("UPDATE users SET platform_role='admin' WHERE id=?", (account['user']['id'],))
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 409
    assert account['ident'].get_user(account['user']['id'])['status'] == 'active'


def test_last_shared_workspace_owner_cannot_delete(account):
    account['ident'].create_workspace('Fixture organization', kind='org', owner_id=account['user']['id'])
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 409


def test_mail_disabled_never_strands_deleting_account(account, monkeypatch):
    monkeypatch.setenv('SYNWEVO_MAIL_MODE', 'disabled')
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 503
    assert account['ident'].get_user(account['user']['id'])['status'] == 'active'
    assert account['c'].get('/api/auth/profile').status_code == 200


def test_last_organization_owner_cannot_delete_even_without_workspace_ownership(account):
    from synwevo_core.organizations import Organizations
    org = Organizations(account['store'], account['ident']).create(account['user']['id'], 'Fixture org', 'Fixture workspace', 'fixture-delete-guard')
    # Org stewardship is an independent condition, not inferred from workspace ownership.
    account['store'].execute('DELETE FROM memberships WHERE workspace_id=? AND user_id=?', (org['workspace']['id'], account['user']['id']))
    response = send(account, 'POST', '/deletion', {'password': PW})
    assert response.status_code == 409 and response.json()['error'] == 'organization_ownership_required'


def test_concurrent_recovery_only_one_request_can_consume_token(account):
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
    token = mail_token(account, 'account_recovery')
    def recover(_):
        with TestClient(account['app']) as client:
            return client.post('/api/auth/deletion/recovery', json={'token': token}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(recover, range(2))) == [200, 410]


def test_finalization_preserves_contributions_and_reserves_identity(account):
    account['store'].execute("INSERT INTO projects (id,name) VALUES ('fixture-contribution','Fixture organization document')")
    before = account['store'].one("SELECT * FROM projects WHERE id='fixture-contribution'")
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
    account['store'].execute('UPDATE users SET deletion_due_at=? WHERE id=?', (time.time()-1, account['user']['id']))
    assert finalize_deletions(account['store']) == 1
    assert account['store'].one("SELECT * FROM projects WHERE id='fixture-contribution'") == before
    with pytest.raises(ServiceError):
        account['ident'].create_user(account['user']['email'], PW, status='active')


@pytest.mark.parametrize('status', ['suspended', 'pending_delete', 'deleted'])
def test_unavailable_users_have_no_profile_even_with_existing_cookie(account, status):
    account['ident'].set_status(account['user']['id'], status)
    assert account['c'].get('/api/auth/profile').status_code == 401
    assert send(account, 'PATCH', '/profile', {'display_name': 'blocked'}).status_code == 401


def test_pending_profile_read_never_grants_mutation(account):
    account['ident'].set_status(account['user']['id'], 'pending')
    response = account['c'].get('/api/auth/profile')
    assert response.status_code == 200 and response.json()['status'] == 'pending'
    assert send(account, 'PATCH', '/profile', {'display_name': 'blocked'}).status_code in (401, 403)


def test_actual_file_delivery_uses_the_injected_mailer(account):
    assert send(account, 'POST', '/email-change', {'email': 'delivered@example.test', 'password': PW}).status_code == 200
    assert account['app'].state.mailer.drain_once() == 1
    row = account['store'].one("SELECT status FROM mail_outbox WHERE kind='email_change'")
    assert row['status'] == 'sent'


def test_deletion_revokes_codes_tokens_tickets_and_every_session(account):
    store, uid, ts = account['store'], account['user']['id'], time.time()
    extra_cookie, _ = account['app'].state.sessions.create(uid, workspace_id=None, amr='password')
    store.execute('INSERT INTO oauth_codes (code_hash,client_id,user_id,redirect_uri,scope,auth_time,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)',
                  ('fixture-code', 'fixture-client', uid, 'http://testserver/callback', 'openid', ts, ts, ts+3600))
    store.execute('INSERT INTO oauth_tokens (id,client_id,user_id,scope,issued_at,access_expires_at,refresh_expires_at) VALUES (?,?,?,?,?,?,?)',
                  ('fixture-grant', 'fixture-client', uid, 'openid', ts, ts+3600, ts+86400))
    store.execute('INSERT INTO mfa_tickets (id,user_id,created_at,expires_at) VALUES (?,?,?,?)', ('fixture-ticket', uid, ts, ts+600))
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
    assert store.one("SELECT used_at FROM oauth_codes WHERE code_hash='fixture-code'")['used_at']
    assert store.one("SELECT revoked_at FROM oauth_tokens WHERE id='fixture-grant'")['revoked_at']
    assert store.one("SELECT 1 FROM mfa_tickets WHERE id='fixture-ticket'") is None
    assert account['app'].state.sessions.load(extra_cookie) is None


def test_concurrent_last_admin_deletions_cannot_remove_both(account):
    account['store'].execute("UPDATE users SET platform_role='admin' WHERE id=?", (account['user']['id'],))
    other = account['ident'].create_user('second-admin@example.test', PW, status='active', platform_role='admin')
    second = login(account, other['email'])
    def remove(client):
        return send(account, 'POST', '/deletion', {'password': PW}, client).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(remove, [account['c'], second])) == [200, 409]
    assert account['store'].one("SELECT COUNT(*) AS n FROM users WHERE platform_role='admin' AND status='active'")['n'] == 1


def test_expired_deleted_organization_does_not_trap_account(account):
    from synwevo_core.organizations import Organizations
    org = Organizations(account['store'], account['ident']).create(account['user']['id'], 'Fixture expired org', 'Fixture workspace', 'fixture-expired-org')
    account['store'].execute("UPDATE organizations SET status='deleted',deleted_at=?,recover_until=? WHERE id=?", (time.time()-10, time.time()-1, org['organization']['id']))
    assert send(account, 'POST', '/deletion', {'password': PW}).status_code == 200
