"""Synthetic localhost SMTP/TLS receiver; never sends mail to an external recipient."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import smtplib
import socketserver
import ssl
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_identity import mail


@pytest.fixture
def configured(tmp_path, monkeypatch):
    # Scrub inherited credentials; tests use only synthetic values and localhost transport.
    import os
    for key in list(os.environ):
        if key.startswith(('SYNWEVO_SMTP_', 'DOCWEAVE_SMTP_', 'SYNWEVO_MAIL_', 'DOCWEAVE_MAIL_')):
            monkeypatch.delenv(key)
    for key, value in {'MAIL_MODE': 'smtp', 'SMTP_HOST': 'localhost', 'SMTP_PORT': '587',
                       'SMTP_USER': 'sender@example.test', 'SMTP_PASSWORD': 'synthetic-secret',
                       'MAIL_FROM': 'sender@example.test', 'MAIL_FROM_NAME': 'Synwevo',
                       'SMTP_STARTTLS': '1', 'SMTP_SSL': '0', 'SMTP_TIMEOUT_S': '2',
                       'PUBLIC_URL': 'https://accounts.example.test'}.items():
        monkeypatch.setenv('SYNWEVO_' + key, value)
    return Store(tmp_path / 'data')


@pytest.fixture
def smtp_receiver(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'ca.pem', tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    key_path.chmod(0o600)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    original_context = ssl.create_default_context

    @contextmanager
    def start(*, implicit=False, trusted=True, reply=250, lose_ack=False, advertise_starttls=True):
        state = {'messages': [], 'auth_over_tls': [], 'connections': 0, 'mail_from': [], 'rcpt_to': []}
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                conn = self.request
                stream = None
                try:
                    state['connections'] += 1
                    conn.settimeout(3)
                    encrypted = implicit
                    if implicit:
                        conn = tls.wrap_socket(conn, server_side=True)
                    stream = conn.makefile('rb')
                    conn.sendall(b'220 localhost synthetic SMTP receiver\r\n')
                    while line := stream.readline():
                        command = line.split(b' ', 1)[0].strip().upper()
                        if command in (b'EHLO', b'HELO'):
                            conn.sendall(b'250-localhost\r\n250-AUTH PLAIN\r\n250 STARTTLS\r\n'
                                         if advertise_starttls else b'250-localhost\r\n250 AUTH PLAIN\r\n')
                        elif command == b'STARTTLS':
                            conn.sendall(b'220 start TLS\r\n')
                            stream.close()
                            conn = tls.wrap_socket(conn, server_side=True)
                            stream = conn.makefile('rb')
                            encrypted = True
                        elif command == b'AUTH':
                            state['auth_over_tls'].append(encrypted)
                            conn.sendall(b'235 authenticated\r\n')
                        elif command == b'MAIL':
                            state['mail_from'].append(line.decode().strip())
                            conn.sendall(b'250 sender accepted\r\n')
                        elif command == b'RCPT':
                            state['rcpt_to'].append(line.decode().strip())
                            conn.sendall(b'250 recipient accepted\r\n')
                        elif command == b'DATA':
                            conn.sendall(b'354 send message\r\n')
                            data = bytearray()
                            while (line := stream.readline()) and line != b'.\r\n':
                                data.extend(line)
                            state['messages'].append(bytes(data))
                            if lose_ack:
                                return
                            conn.sendall(str(reply).encode()+b' queued as TEST12345\r\n')
                        elif command == b'QUIT':
                            conn.sendall(b'221 bye\r\n')
                            return
                        else:
                            conn.sendall(b'500 unsupported\r\n')
                except (ssl.SSLError, OSError):
                    pass
                finally:
                    if stream:
                        stream.close()
                    conn.close()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        server = Server(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        monkeypatch.setenv('SYNWEVO_SMTP_PORT', str(server.server_address[1]))
        monkeypatch.setenv('SYNWEVO_SMTP_SSL', '1' if implicit else '0')
        monkeypatch.setenv('SYNWEVO_SMTP_STARTTLS', '0' if implicit else '1')
        monkeypatch.setattr(mail.ssl, 'create_default_context',
                            (lambda *args, **kwargs: original_context(cafile=str(cert_path))) if trusted else original_context)
        try:
            yield state
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
    return start


@pytest.mark.parametrize('implicit', [False, True])
@pytest.mark.parametrize('status', ['active', 'pending'])
def test_business_verification_route_queues_and_delivers_through_verified_tls(configured, smtp_receiver, monkeypatch, implicit, status):
    from synwevo_identity.app import create_app
    monkeypatch.setenv('SYNWEVO_UI_AUTH', 'session')
    monkeypatch.setenv('SYNWEVO_COOKIE_SECURE', '0')
    monkeypatch.setenv('SYNWEVO_ROLE', 'accounts')
    with smtp_receiver(implicit=implicit) as receiver:
        api = create_app(str(configured.root), start_jobs=False)
        Identity(configured).create_user('recipient@example.test', 'synthetic-long-password', status=status)
        with TestClient(api) as client:
            assert client.post('/api/auth/login', json={'email': 'recipient@example.test', 'password': 'synthetic-long-password'}).status_code == 200
            response = client.post('/api/auth/verify-email/send', headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE)})
            assert response.status_code == 200
            assert receiver['messages'] == [], 'Business HTTP must only enqueue, not block on SMTP'
            row = configured.one('SELECT * FROM mail_outbox')
            assert row['status'] == 'queued'
            assert api.state.mailer.drain_once() == 1
            assert api.state.mailer.drain_once() == 0
        assert receiver['auth_over_tls'] == [True]
        # SMTP command words are case-insensitive: Python 3.12 uses FROM/TO while 3.14
        # uses from/to. Normalize only command words; envelope addresses remain exact.
        assert [(line.partition(':')[0].upper(), line.partition(':')[2])
                for line in receiver['mail_from']] == [('MAIL FROM', '<sender@example.test>')]
        assert [(line.partition(':')[0].upper(), line.partition(':')[2])
                for line in receiver['rcpt_to']] == [('RCPT TO', '<recipient@example.test>')]
        message = BytesParser(policy=policy.default).parsebytes(receiver['messages'][0])
        assert message['From'] == 'Synwevo <sender@example.test>'
        assert message['Message-ID'] == f"<{row['id']}@example.test>"
        body = message.get_body(preferencelist=("plain",)).get_content()      # text part of the multipart/alternative
        html = message.get_body(preferencelist=("html",))
        assert html is not None and "<html" in html.get_content() and "#token=" in html.get_content(), "the HTML alternative carries the same link"
        assert 'https://accounts.example.test/verify-email?lang=zh-Hans#token=' in body
        assert 'verify-email?token=' not in body, 'Bearer links must not reach HTTP access logs'
        token = body.split('token=')[1].split()[0]
        with TestClient(api) as mailbox_owner:
            if status == 'pending':
                blocked = mailbox_owner.post('/api/auth/email/verify', json={'token': token})
                assert blocked.status_code == 400 and blocked.json()['error'] == 'new_password_required'
                confirmed = mailbox_owner.post('/api/auth/email/verify', json={
                    'token': token, 'new_password': 'Mailbox final synthetic passphrase 2026!'})
                assert confirmed.status_code == 200 and confirmed.json()['sign_in_required'] is True
                assert not Identity(configured).authenticate('recipient@example.test', 'synthetic-long-password')['ok']
                assert Identity(configured).authenticate('recipient@example.test', 'Mailbox final synthetic passphrase 2026!')['ok']
                assert mailbox_owner.get('/api/auth/me').status_code == 401
            else:
                blocked = mailbox_owner.post('/api/auth/email/verify', json={
                    'token': token, 'new_password': 'Forbidden replacement synthetic phrase!'})
                assert blocked.status_code == 400 and blocked.json()['error'] == 'invalid_field'
                confirmed = mailbox_owner.post('/api/auth/email/verify', json={'token': token})
                assert confirmed.status_code == 200 and confirmed.json()['sign_in_required'] is False
                assert Identity(configured).authenticate('recipient@example.test', 'synthetic-long-password')['ok']
        stored = configured.one('SELECT * FROM mail_outbox')
        assert stored['status'] == 'sent' and stored['attempts'] == 1
        assert token not in stored['body'] and mail.REDACTED in stored['body']


def test_server_without_starttls_never_gets_credentials_or_mail(configured, smtp_receiver):
    with smtp_receiver(advertise_starttls=False) as receiver:
        m = mail.Mailer(configured, start=False)
        m.enqueue('recipient@example.test', 'synthetic test', 'private body', kind='verify')
        assert m.drain_once() == m.drain_once() == 0
        assert receiver['auth_over_tls'] == receiver['messages'] == []
        assert configured.one('SELECT status,attempts FROM mail_outbox') == {'status': 'failed', 'attempts': 1}


def test_acceptance_runner_uses_real_http_two_business_flows_and_refuses_rerun(configured, smtp_receiver, monkeypatch, tmp_path):
    import runpy
    import sys
    import json
    import os
    # The CLI normally runs in a separate process. Running it in-process here must restore
    # every environment setting it changes, so later suites cannot inherit its fixture role.
    for key in ('SYNWEVO_DATA_DIR', 'SYNWEVO_ROLE', 'SYNWEVO_UI_AUTH', 'SYNWEVO_COOKIE_SECURE',
                'SYNWEVO_PUBLIC_URL', 'SYNWEVO_ISSUER'):
        monkeypatch.setenv(key, os.environ.get(key, ''))
    monkeypatch.setattr(os, 'umask', lambda _: 0o077)  # CLI filesystem is already under pytest tmp_path.
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'verify_smtp.py'
    fixture_data = tmp_path / 'smtp-acceptance'
    monkeypatch.setattr(sys, 'argv', [str(script), '--data', str(fixture_data),
                                     '--recipient', 'recipient@example.test', '--send'])
    with smtp_receiver() as receiver:
        with pytest.raises(SystemExit) as done:
            runpy.run_path(str(script), run_name='__main__')
        assert done.value.code == 0
        assert len(receiver['messages']) == 2
        report = json.loads((fixture_data / 'acceptance.json').read_text())
        assert {row['kind'] for row in report['messages']} == {'invite', 'verify'}
        assert all(row['status'] == 'sent' and row['attempts'] == 1 for row in report['messages'])
        with pytest.raises(SystemExit) as duplicate:
            runpy.run_path(str(script), run_name='__main__')
        assert duplicate.value.code == 1
        assert len(receiver['messages']) == 2


def test_invalid_certificate_fails_before_auth_and_never_retries(configured, smtp_receiver):
    with smtp_receiver(trusted=False) as receiver:
        m = mail.Mailer(configured, start=False)
        m.enqueue('recipient@example.test', 'synthetic test', 'private body', kind='verify')
        assert m.drain_once() == m.drain_once() == 0
        assert receiver['auth_over_tls'] == receiver['messages'] == []
        row = configured.one('SELECT * FROM mail_outbox')
        assert row['status'] == 'failed' and row['last_error'] == 'tls_certificate_invalid'
        assert row['attempts'] == 1


@pytest.mark.parametrize('key,value', [('SMTP_SSL','1'), ('SMTP_STARTTLS','0'), ('SMTP_STARTTLS','maybe'),
    ('SMTP_TIMEOUT_S','inf'), ('SMTP_TIMEOUT_S','0'), ('SMTP_TIMEOUT_S','61'), ('SMTP_PORT','70000'),
    ('MAIL_FROM','bad\r\nBcc: attacker@example.test'), ('MAIL_FROM_NAME','Synwevo\r\nBcc: bad'),
    ('SMTP_PASSWORD','')])
def test_invalid_config_refused_before_queueing(configured, monkeypatch, key, value):
    monkeypatch.setenv('SYNWEVO_'+key, value)
    with pytest.raises(ServiceError, match='邮件配置无效'):
        mail.Mailer(configured, start=False)
    assert not configured.all('SELECT * FROM mail_outbox')


def test_smtp_diagnostics_do_not_include_server_text_or_secrets(configured, monkeypatch, capsys):
    m = mail.Mailer(configured, start=False)
    monkeypatch.setattr(m, '_deliver', lambda row: (_ for _ in ()).throw(smtplib.SMTPAuthenticationError(
        535, b'synthetic-secret AUTH=secret private-body token=live')))
    m.enqueue('recipient@example.test', 's', 'token=live', kind='verify')
    assert m.drain_once() == 0
    row = configured.one('SELECT * FROM mail_outbox')
    assert row['last_error'] == 'smtp_authentication_failed' and row['status'] == 'failed'
    assert 'synthetic-secret' not in capsys.readouterr().out
    monkeypatch.setenv('DOCWEAVE_SMTP_PASSWORD','second-synthetic-secret')
    with pytest.raises(env.ConfigConflict) as exc:
        mail.smtp_config()
    assert 'synthetic-secret' not in str(exc.value)
    assert 'SYNWEVO_SMTP_PASSWORD' in str(exc.value)


def test_lost_data_acknowledgement_is_not_automatically_resent(configured, smtp_receiver):
    with smtp_receiver(lose_ack=True) as receiver:
        m = mail.Mailer(configured, start=False)
        m.enqueue('recipient@example.test', 's', 'token=live', kind='verify')
        assert m.drain_once() == m.drain_once() == 0
        assert len(receiver['messages']) == 1
        row = configured.one('SELECT * FROM mail_outbox')
        assert row['status'] == 'failed' and row['last_error'] == 'delivery_unknown'


def test_explicit_temporary_refusal_retries_boundedly_with_same_message_id(configured, smtp_receiver):
    with smtp_receiver(reply=451) as receiver:
        m = mail.Mailer(configured, start=False)
        m.enqueue('recipient@example.test', 's', 'b', kind='verify')
        for _ in range(mail.MAX_ATTEMPTS):
            m.drain_once()
            configured.execute("UPDATE mail_outbox SET next_attempt_at=0 WHERE status='queued'")
        assert m.drain_once() == 0
        assert len(receiver['messages']) == mail.MAX_ATTEMPTS
        assert len({BytesParser().parsebytes(b)['Message-ID'] for b in receiver['messages']}) == 1
        row = configured.one('SELECT * FROM mail_outbox')
        assert row['status'] == 'failed' and row['attempts'] == mail.MAX_ATTEMPTS


def test_two_workers_claim_once_and_crash_claim_is_never_replayed(configured, monkeypatch):
    first, second = mail.Mailer(configured, start=False), mail.Mailer(Store(configured.root), start=False)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def delivery(row):
        calls.append(row['id'])
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(first, '_deliver', delivery)
    monkeypatch.setattr(second, '_deliver', delivery)
    first.enqueue('recipient@example.test', 's', 'b', kind='verify')
    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(first.drain_once)
        assert entered.wait(3)
        assert second.drain_once() == 0
        release.set()
        assert task.result() == 1
    assert len(calls) == 1
    row = first.enqueue('recipient@example.test', 's', 'https://example.test/?token=live', kind='verify')
    configured.execute('UPDATE mail_outbox SET last_error=?, next_attempt_at=0, attempts=1 WHERE id=?', (mail.IN_PROGRESS,row['id']))
    assert second.drain_once() == 0
    saved = configured.one('SELECT * FROM mail_outbox WHERE id=?', (row['id'],))
    assert saved['status'] == 'failed' and saved['last_error'] == 'delivery_unknown'
    assert '?token=live' not in saved['body'] and len(calls) == 1


def test_database_failure_after_smtp_acceptance_does_not_resend(configured, monkeypatch):
    m = mail.Mailer(configured, start=False)
    m.enqueue('recipient@example.test', 's', 'b', kind='verify')
    calls = []
    monkeypatch.setattr(m, '_deliver', lambda row: calls.append(row['id']))
    execute = configured.execute
    def update(sql, args=()):
        if "status='sent'" in sql:
            raise RuntimeError('synthetic database write fault')
        return execute(sql,args)
    monkeypatch.setattr(configured,'execute',update)
    with pytest.raises(RuntimeError):
        m.drain_once()
    assert m.drain_once() == 0 and len(calls) == 1
    assert configured.one('SELECT last_error FROM mail_outbox')['last_error'] == mail.IN_PROGRESS
