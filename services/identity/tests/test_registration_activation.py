"""Mailbox-controlled activation: real temporary HTTP/account/password services."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from fastapi.testclient import TestClient

from services.identity.tests.test_independent_identity_review import (
    review, register, post, token_for, sign_in, PW, ATTACKER_PW, VICTIM_PW,
)


@pytest.mark.parametrize('password', ['', None, [], 'short', 'x' * 1025])
def test_invalid_final_password_does_not_spend_link_or_change_pending_identity(review, password):
    app, client, ident = review
    assert register(client).status_code == 202
    user = ident.by_email('mailbox-owner@example.test')
    before = ident.store.one('SELECT phc FROM user_passwords WHERE user_id=?', (user['id'],))
    token = token_for(ident)
    response = post(client, '/email/verify', {'token': token, 'new_password': password})
    assert response.status_code == 400
    assert ident.get_user(user['id'])['status'] == 'pending'
    assert ident.store.one('SELECT phc FROM user_passwords WHERE user_id=?', (user['id'],)) == before
    assert app.state.invites.peek_email_token(token, 'verify')
    assert post(client, '/email/verify', {'token': token, 'new_password': VICTIM_PW}).status_code == 200


def test_active_verification_rejects_password_change_and_preserves_session(review):
    app, client, ident = review
    user = ident.create_user('active-verification@example.test', PW, status='active')
    sign_in(client, user['email'])
    token = app.state.invites.issue_email_token(user['id'], 'verify', ttl_s=3600, new_email=user['email'])
    response = post(client, '/email/verify', {'token': token, 'new_password': VICTIM_PW})
    assert response.status_code == 400 and response.json()['error'] == 'invalid_field'
    assert app.state.invites.peek_email_token(token, 'verify')
    assert post(client, '/email/verify', {'token': token}).json() == {'ok': True, 'sign_in_required': False}
    assert client.get('/api/auth/me').status_code == 200
    assert ident.authenticate(user['email'], PW)['ok']
    assert not ident.authenticate(user['email'], VICTIM_PW)['ok']


def test_concurrent_activation_accepts_one_final_password_and_no_session(review):
    app, client, ident = review
    assert register(client, password=ATTACKER_PW).status_code == 202
    token = token_for(ident)
    barrier = threading.Barrier(6)
    passwords = [f'Final mailbox secret phrase number {n} for local testing!' for n in range(6)]
    def submit(n):
        with TestClient(app) as caller:
            barrier.wait()
            response = post(caller, '/email/verify', {'token': token, 'new_password': passwords[n]})
            assert caller.get('/api/auth/me').status_code == 401
            return n, response.status_code
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(submit, range(6)))
    assert sorted(status for _, status in outcomes) == [200] + [409] * 5
    winner = next(n for n, status in outcomes if status == 200)
    assert not ident.authenticate('mailbox-owner@example.test', ATTACKER_PW)['ok']
    sign_in(client, 'mailbox-owner@example.test', passwords[winner])


def test_activation_invalidates_historical_pending_factors_and_grants(review):
    app, client, ident = review
    assert register(client).status_code == 202
    token, store = token_for(ident), ident.store
    uid, ts = ident.by_email('mailbox-owner@example.test')['id'], time.time()
    sign_in(client, 'mailbox-owner@example.test')
    # Historical pending-user fixture: the current gate disallows enrolling these factors.
    app.state.mfa.begin_totp(uid, 'mailbox-owner@example.test')
    app.state.mfa.generate_recovery_codes(uid)
    store.execute("INSERT INTO passkeys(id,user_id,credential_id,public_key,rp_id,created_at) VALUES ('pending-passkey',?,'fixture-id','fixture-public-key','localhost',?)", (uid, ts))
    store.execute("INSERT INTO webauthn_credentials(id,user_id,credential_id,public_key,created_at) VALUES ('legacy-key',?,'legacy-fixture-id','fixture-public-key',?)", (uid, ts))
    store.execute("INSERT INTO oauth_codes(code_hash,client_id,user_id,redirect_uri,scope,auth_time,created_at,expires_at) VALUES ('pending-code','fixture',?,'http://localhost/callback','openid',?,?,?)", (uid, ts, ts, ts+3600))
    store.execute("INSERT INTO oauth_tokens(id,client_id,user_id,scope,issued_at,access_expires_at) VALUES ('pending-grant','fixture',?,'openid',?,?)", (uid, ts, ts+3600))
    store.execute("INSERT INTO mfa_tickets(id,user_id,created_at,expires_at) VALUES ('pending-ticket',?,?,?)", (uid, ts, ts+600))
    store.execute("INSERT INTO webauthn_challenges(id,user_id,purpose,challenge,rp_id,origin,created_at,expires_at) VALUES ('pending-challenge',?,'register','fixture','localhost','http://localhost',?,?)", (uid, ts, ts+600))
    reset = app.state.invites.issue_email_token(uid, 'reset', ttl_s=3600)
    assert post(client, '/email/verify', {'token': token, 'new_password': VICTIM_PW}).status_code == 200
    assert client.get('/api/auth/me').status_code == 401
    for table in ['passkeys', 'webauthn_credentials', 'totp_secrets', 'recovery_codes', 'mfa_tickets', 'webauthn_challenges']:
        assert store.one(f'SELECT COUNT(*) AS n FROM {table} WHERE user_id=?', (uid,))['n'] == 0
    assert store.one("SELECT used_at FROM oauth_codes WHERE code_hash='pending-code'")['used_at']
    assert store.one("SELECT revoked_at FROM oauth_tokens WHERE id='pending-grant'")['revoked_at']
    assert app.state.invites.peek_email_token(reset, 'reset') is None
    sign_in(client, 'mailbox-owner@example.test', VICTIM_PW)


@pytest.mark.parametrize('mode', ['closed', 'invite'])
def test_persisted_registration_policy_overrides_open_environment(review, mode):
    _, client, ident = review
    ident.store.execute("INSERT INTO meta(key,value) VALUES ('registration.mode',?)", (mode,))
    assert register(client).status_code == 403
    assert ident.store.one('SELECT COUNT(*) AS n FROM users')['n'] == 0


def test_atomic_login_failure_keeps_rate_limit_and_audit_writes(review):
    _, client, ident = review
    user = ident.create_user('rate-counter@example.test', PW, status='active')
    assert post(client, '/login', {'email': user['email'], 'password': 'Incorrect synthetic login phrase!'}).status_code == 401
    assert ident.store.one("SELECT COUNT(*) AS n FROM auth_attempts")['n'] >= 2
    assert ident.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='auth.login' AND result='denied'")['n'] == 1


def test_h10_concurrent_old_password_login_cannot_survive_activation(review):
    app, client, ident = review
    assert register(client, password=ATTACKER_PW).status_code == 202
    token = token_for(ident)
    barrier = threading.Barrier(9)
    attackers = [TestClient(app) for _ in range(8)]
    def login(n):
        barrier.wait()
        return post(attackers[n], '/login', {'email': 'mailbox-owner@example.test', 'password': ATTACKER_PW}).status_code
    def activate():
        barrier.wait()
        return post(client, '/email/verify', {'token': token, 'new_password': VICTIM_PW}).status_code
    try:
        with ThreadPoolExecutor(max_workers=9) as pool:
            logins = [pool.submit(login, n) for n in range(8)]
            verified = pool.submit(activate)
            assert verified.result() == 200
            statuses = [result.result() for result in logins]
        usable = [caller.get('/api/auth/me').status_code for caller in attackers]
        assert all(status != 200 for status in usable), f'H10: pre-verification password raced activation: login={statuses}, subsequent me={usable}'
    finally:
        for caller in attackers:
            caller.close()


def test_malformed_address_is_a_400_not_a_crash(review):
    app, client, ident = review
    response = register(client, email='1155123456@link.cuhk.edu.hk.')
    assert response.status_code == 400 and response.json()['error'] == 'bad_email'
    assert ident.by_email('1155123456@link.cuhk.edu.hk.') is None


def test_institutional_address_registers_and_is_told_where_the_mail_comes_from(review):
    app, client, ident = review
    state = client.get('/api/auth/state').json()
    assert state['mail_sender'] == {'name': 'Synwevo', 'address': 'synwevo@localhost'}, state['mail_sender']
    response = register(client, email='1155123456@link.cuhk.edu.hk')
    assert response.status_code == 202 and response.json() == {'status': 'verification_pending', 'delivery': 'requested'}
    user = ident.by_email('1155123456@LINK.cuhk.edu.hk')
    assert user and user['status'] == 'pending'
    row = ident.store.one("SELECT to_addr,kind,status FROM mail_outbox WHERE ref_id=?", (user['id'],))
    assert row['to_addr'] == '1155123456@link.cuhk.edu.hk' and row['kind'] == 'verify' and row['status'] == 'queued'
    # the body leaves as quoted-printable text, not base64 (a spam signal on institutional gateways)
    import os, pathlib
    assert app.state.mailer.drain_once(limit=5) >= 1
    eml = next(pathlib.Path(os.environ['SYNWEVO_MAIL_DIR']).glob('*.eml')).read_text()
    assert 'Content-Transfer-Encoding: quoted-printable' in eml and 'base64' not in eml
    assert 'http://testserver/verify-email' in eml


def test_registering_an_address_that_already_has_an_active_account_tells_that_mailbox(review):
    app, client, ident = review
    user = ident.create_user('already-there@example.test', PW, status='active')
    ident.store.execute('UPDATE users SET email_verified_at=1 WHERE id=?', (user['id'],))
    fresh = register(client, email='nobody-yet@example.test').json()
    again = register(client, email='Already-There@example.test', password=ATTACKER_PW)
    # same answer as a fresh registration: nothing here says whether the address was known
    assert again.status_code == 202 and again.json() == fresh
    rows = ident.store.all("SELECT to_addr,kind FROM mail_outbox WHERE ref_id=? ORDER BY created_at", (user['id'],))
    assert [r['kind'] for r in rows] == ['account_exists'] and rows[0]['to_addr'] == 'already-there@example.test'
    body = ident.store.one("SELECT body FROM mail_outbox WHERE ref_id=?", (user['id'],))['body']
    assert 'token=' not in body and '/login' in body and '/forgot' in body
    # nothing about the account changed, and the other password does not work
    assert ident.authenticate('already-there@example.test', PW)['ok']
    assert not ident.authenticate('already-there@example.test', ATTACKER_PW)['ok']
    assert ident.get_user(user['id'])['status'] == 'active'
    # a second attempt within the hour does not send a second notice
    register(client, email='already-there@example.test')
    assert len(ident.store.all("SELECT id FROM mail_outbox WHERE ref_id=?", (user['id'],))) == 1
