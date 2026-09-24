"""Independent H review: real identity HTTP, temporary Store, file mail only.

Regression assertions describe the required boundary. A failing test is evidence to hand
back to the implementation owner, not an expected-success label or a mocked exploit.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_core import journal
from synwevo_identity.profile import finalize_deletions

PW = 'Synthetic review owner passphrase 2026!'
ATTACKER_PW = 'Synthetic prior registrant passphrase 2026!'
VICTIM_PW = 'Synthetic different registration passphrase 2026!'


@pytest.fixture
def review(tmp_path, monkeypatch):
    for name, value in {'DATA_DIR': str(tmp_path / 'fixture-data'), 'UI_AUTH': 'session',
                        'REGISTRATION': 'open', 'COOKIE_SECURE': '0', 'MAIL_MODE': 'file',
                        'MAIL_DIR': str(tmp_path / 'fixture-mail'), 'PUBLIC_URL': 'http://testserver',
                        'TRUST_PROXY': '0', 'ROLE': 'accounts'}.items():
        monkeypatch.setenv('SYNWEVO_' + name, value)
    from synwevo_identity.app import create_app
    app = create_app(str(tmp_path / 'fixture-data'), start_jobs=False)
    with TestClient(app) as client:
        yield app, client, app.state.identity


def post(client, path, body=None):
    return client.post('/api/auth' + path, json=body or {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ''})


def register(client, *, email='mailbox-owner@example.test', password=PW):
    return post(client, '/register', {'email': email, 'password': password, 'locale': 'en'})


def token_for(ident, kind='verify'):
    row = ident.store.one('SELECT body FROM mail_outbox WHERE kind=? ORDER BY created_at DESC LIMIT 1', (kind,))
    return re.search(r'[#?]token=([A-Za-z0-9_-]+)', row['body'])[1]


def sign_in(client, email, password=PW):
    response = post(client, '/login', {'email': email, 'password': password})
    assert response.status_code == 200, response.text


def test_h01_verifying_reregistered_address_does_not_keep_attacker_session(review):
    app, attacker, ident = review
    assert register(attacker, password=ATTACKER_PW).status_code == 202
    sign_in(attacker, 'mailbox-owner@example.test', ATTACKER_PW)
    assert attacker.get('/api/auth/onboarding').status_code == 403
    victim = TestClient(app)
    assert register(victim, password=VICTIM_PW).status_code == 202
    token = token_for(ident)
    blocked = post(victim, '/email/verify', {'token': token})
    assert blocked.status_code == 400 and blocked.json()['error'] == 'new_password_required'
    assert attacker.get('/api/auth/onboarding').status_code == 403
    assert post(victim, '/email/verify', {'token': token, 'new_password': VICTIM_PW}).status_code == 200
    assert victim.get('/api/auth/me').status_code == 401
    after = attacker.get('/api/auth/onboarding')
    assert after.status_code in (401, 403), 'H01: previously blocked attacker session became active after mailbox owner verified'


def test_h01_verification_does_not_make_attacker_chosen_password_active(review):
    app, attacker, ident = review
    assert register(attacker, password=ATTACKER_PW).status_code == 202
    victim = TestClient(app)
    assert register(victim, password=VICTIM_PW).status_code == 202
    assert post(victim, '/email/verify', {'token': token_for(ident), 'new_password': VICTIM_PW}).status_code == 200
    result = post(TestClient(app), '/login', {'email': 'mailbox-owner@example.test', 'password': ATTACKER_PW})
    assert result.status_code in (401, 403), 'H01: attacker-selected password still signs in after victim repeats registration and verifies'
    sign_in(victim, 'mailbox-owner@example.test', VICTIM_PW)


def test_h02_authenticated_invitation_accept_requires_csrf(review):
    app, victim, ident = review
    inviter = ident.create_user('inviter@example.test', PW, status='active')
    user = ident.create_user('invite-victim@example.test', PW, status='active')
    workspace = ident.workspaces_for(inviter['id'])[0]
    _, token = app.state.invites.create(workspace_id=workspace['id'], email=user['email'], role='editor', actor_id=inviter['id'])
    sign_in(victim, user['email'])
    # Byte-body/no Content-Type matches a simple fetch request from a same-site sibling origin.
    # No custom header/preflight is necessary; CORS would prevent reading, not sending it.
    response = victim.post('/api/auth/invitations/accept', content=json.dumps({'token': token}).encode(),
                           headers={'Origin': 'http://untrusted.testserver', 'Sec-Fetch-Site': 'same-site'})
    joined = ident.membership(workspace['id'], user['id']) is not None
    assert response.status_code == 403 and not joined, f'H02: no-CSRF invitation acceptance returned {response.status_code}; membership created={joined}'


def test_anonymous_invitation_with_password_still_works(review):
    app, client, ident = review
    inviter = ident.create_user('inviter@example.test', PW, status='active')
    workspace = ident.workspaces_for(inviter['id'])[0]
    _, token = app.state.invites.create(workspace_id=workspace['id'], email='new-invitee@example.test', role='editor', actor_id=inviter['id'])
    response = post(client, '/invitations/accept', {'token': token, 'password': PW})
    assert response.status_code == 200, response.text
    assert ident.membership(workspace['id'], ident.by_email('new-invitee@example.test')['id'])


def test_address_bound_verification_refuses_changed_email(review):
    app, client, ident = review
    assert register(client).status_code == 202
    token = token_for(ident)
    uid = ident.by_email('mailbox-owner@example.test')['id']
    # A stored-address change races an old link; the capability must prove its own address.
    ident.store.execute("UPDATE users SET email='changed@example.test',email_key='changed@example.test' WHERE id=?", (uid,))
    response = post(client, '/email/verify', {'token': token})
    assert response.status_code == 409
    assert ident.get_user(uid)['email_verified_at'] is None


def test_h03_legacy_unbound_verification_cannot_verify_another_address(review):
    app, client, ident = review
    user = ident.create_user('old@example.test', PW, status='pending', with_personal_workspace=False)
    token = app.state.invites.issue_email_token(user['id'], 'verify', ttl_s=3600)
    ident.store.execute("UPDATE users SET email='different@example.test',email_key='different@example.test' WHERE id=?", (user['id'],))
    response = post(client, '/email/verify', {'token': token})
    current = ident.get_user(user['id'])
    assert response.status_code in (409, 410) and current['email_verified_at'] is None, 'H03: unbound legacy verify token verified the replacement address'


@pytest.mark.parametrize('field,value', [('locale', []), ('locale', {}), ('intent', [])])
def test_registration_malformed_enums_are_validation_errors(review, field, value):
    _, client, _ = review
    response = post(client, '/register', {'email': 'malformed@example.test', 'password': PW, field: value})
    assert response.status_code in (400, 422), f'{field}={type(value).__name__} returned {response.status_code}'


def test_concurrent_registration_keeps_one_identity_one_verification_mail(review):
    app, _, ident = review
    barrier = threading.Barrier(6)
    def submit(_):
        client = TestClient(app)
        barrier.wait()
        return register(client, email='parallel@example.test').status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(submit, range(6))) == [202] * 6
    assert ident.store.one("SELECT COUNT(*) AS n FROM users WHERE email_key='parallel@example.test'")['n'] == 1
    assert ident.store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='verify'")['n'] == 1
    assert ident.store.one("SELECT COUNT(*) AS n FROM email_tokens WHERE kind='verify' AND used_at IS NULL")['n'] == 1


def test_registration_ip_limit_cannot_be_rotated_with_untrusted_xff(review):
    _, client, ident = review
    results = []
    for n in range(11):
        response = client.post('/api/auth/register', json={'email': f'bounded-{n}@example.test', 'password': PW},
                               headers={'X-Forwarded-For': f'198.51.100.{n+1}'})
        results.append(response.status_code)
    assert results == [202] * 10 + [429]
    assert ident.by_email('bounded-10@example.test') is None


def test_concurrent_ip_limit_admits_exactly_ten_unique_registrations(review):
    app, _, ident = review
    barrier = threading.Barrier(16)
    def submit(n):
        with TestClient(app) as client:
            barrier.wait()
            return register(client, email=f'parallel-limit-{n}@example.test').status_code
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(submit, range(16)))
    assert sorted(results) == [202] * 10 + [429] * 6
    assert ident.store.one('SELECT COUNT(*) AS n FROM users')['n'] == 10


@pytest.mark.parametrize('path,body', [('/onboarding/personal', {}), ('/email-change', {'email':'next@example.test','password':PW}),
                                     ('/deletion', {'password':PW}), ('/export', {'password':PW}),
                                     ('/workspace', {'workspace_id':'default'}), ('/mfa/totp/begin', {})])
def test_pending_cookie_cannot_reach_sensitive_routes(review, path, body):
    _, client, _ = review
    assert register(client).status_code == 202
    sign_in(client, 'mailbox-owner@example.test')
    response = post(client, path, body)
    assert response.status_code == 403 and response.json()['error'] == 'email_verification_required'


@pytest.mark.parametrize('status', ['suspended', 'pending_delete', 'deleted'])
def test_verification_never_reactivates_unavailable_users(review, status):
    _, client, ident = review
    assert register(client).status_code == 202
    token = token_for(ident)
    uid = ident.by_email('mailbox-owner@example.test')['id']
    ident.set_status(uid, status)
    assert post(client, '/email/verify', {'token': token}).status_code == 409
    assert ident.get_user(uid)['status'] == status


def test_h04_finalized_deletion_journal_cannot_restore_as_reactivatable_suspended_user(review, tmp_path):
    _, client, ident = review
    user = ident.create_user('delete-review@example.test', PW, display_name='Erased fixture name', status='active')
    restored = Store(tmp_path / 'fixture-restore')
    # SQLite's own backup API, both connections opened exclusively through Store.
    with ident.store.conn() as source, restored.conn() as destination:
        source.backup(destination)
    sign_in(client, user['email'])
    assert post(client, '/deletion', {'password': PW}).status_code == 200
    ident.store.execute('UPDATE users SET deletion_due_at=? WHERE id=?', (time.time()-1, user['id']))
    assert finalize_deletions(ident.store) == 1
    events = journal.read(ident.store.root)
    assert [event['kind'] for event in events][-2:] == ['user_suspended', 'user_deleted']
    journal.apply(restored, events)
    row = restored.one('SELECT status,display_name FROM users WHERE id=?', (user['id'],))
    assert row['status'] == 'deleted', f'H04: replayed finalized deletion became {row["status"]}, retaining profile {bool(row["display_name"])}'


def test_h05_failed_finalization_journal_is_retried_after_disk_recovers(review):
    _, _, ident = review
    user = ident.create_user('journal-retry@example.test', PW, status='pending_delete')
    ident.store.execute('UPDATE users SET deletion_due_at=? WHERE id=?', (time.time()-1, user['id']))
    blocked = journal.path(ident.store.root)
    blocked.mkdir()  # Real filesystem failure, no mocked writer or production data.
    assert finalize_deletions(ident.store) == 1
    blocked.rmdir()
    finalize_deletions(ident.store)
    assert any(event['kind'] == 'user_deleted' and event['user_id'] == user['id'] for event in journal.read(ident.store.root)), 'H05: failed deletion journal entry never retried after recovery'


@pytest.mark.parametrize('locale', ['en', 'zh-Hant'])
def test_h06_security_notices_identify_the_changed_event(locale):
    from synwevo_identity.mail import security_notice
    email_notice = security_notice(what='登录邮箱已更改', when_text='刚刚', locale=locale)
    password_notice = security_notice(what='口令已重置', when_text='刚刚', locale=locale)
    assert email_notice != password_notice, f'H06: {locale} notices erase which credential changed'


def test_concurrent_password_reset_consumes_once_and_ends_old_session(review):
    app, client, ident = review
    user = ident.create_user('reset-race@example.test', PW, status='active')
    sign_in(client, user['email'])
    with TestClient(app) as anonymous:
        assert post(anonymous, '/forgot', {'email': user['email']}).status_code == 200
        token = token_for(ident, 'reset')
        preview = post(anonymous, '/reset/preview', {'token': token})
        assert preview.status_code == 200
        assert set(preview.json()) == {'email', 'expires_at'}
    barrier = threading.Barrier(8)
    passwords = [f'Synthetic replacement secret number {index} for testing!' for index in range(8)]
    def submit(index):
        with TestClient(app) as caller:
            barrier.wait()
            response = post(caller, '/reset', {'token': token, 'password': passwords[index]})
            assert caller.get('/api/auth/me').status_code == 401
            return index, response.status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(8)))
    assert sorted(status for _, status in results) == [200] + [409] * 7
    winner = next(index for index, status in results if status == 200)
    assert client.get('/api/auth/me').status_code == 401
    with TestClient(app) as fresh:
        assert post(fresh, '/login', {'email': user['email'], 'password': PW}).status_code == 401
        sign_in(fresh, user['email'], passwords[winner])
    assert ident.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='auth.reset.complete'")['n'] == 1


def test_concurrent_forgot_requests_respect_one_mail_minimum_gap(review):
    app, _, ident = review
    user = ident.create_user('forgot-race@example.test', PW, status='active')
    barrier = threading.Barrier(8)
    def submit(_):
        with TestClient(app) as caller:
            barrier.wait()
            return post(caller, '/forgot', {'email': user['email']}).status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(submit, range(8))) == [200] * 8
    mails = ident.store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='reset'")['n']
    issued = ident.store.one("SELECT COUNT(*) AS n FROM email_tokens WHERE kind='reset'")['n']
    assert (mails, issued) == (1, 1), f'One simultaneous forgot burst queued {mails} reset emails and issued {issued} mutually superseding tokens'


def test_mailbox_password_reset_clears_preverification_attacker_access(review):
    app, attacker, ident = review
    assert register(attacker, password=ATTACKER_PW).status_code == 202
    sign_in(attacker, 'mailbox-owner@example.test', ATTACKER_PW)
    with TestClient(app) as mailbox_owner:
        assert post(mailbox_owner, '/forgot', {'email': 'mailbox-owner@example.test'}).status_code == 200
        assert post(mailbox_owner, '/reset', {'token': token_for(ident, 'reset'), 'password': VICTIM_PW}).status_code == 200
        assert mailbox_owner.get('/api/auth/me').status_code == 401
        assert attacker.get('/api/auth/me').status_code == 401
        assert post(mailbox_owner, '/email/verify', {'token': token_for(ident), 'new_password': VICTIM_PW}).status_code == 200
        assert post(attacker, '/login', {'email': 'mailbox-owner@example.test', 'password': ATTACKER_PW}).status_code == 401
        sign_in(mailbox_owner, 'mailbox-owner@example.test', VICTIM_PW)
