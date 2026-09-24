#!/usr/bin/env python3
"""Explicit external acceptance: isolated HTTP business routes -> outbox -> real SMTP.

Never run as part of the normal test suite. Requires an authorized recipient and --send.
Creates new, clearly labelled fixture accounts in a NEW directory, never a production copy.
At most two messages are attempted, once each. An existing directory refuses a rerun, including
after an uncertain send. All links point to the temporary local service, not production accounts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import threading
import time

import httpx
import uvicorn

from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_identity.app import create_app
from synwevo_identity.mail import mode, smtp_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True, help='New isolated fixture directory; never reuse')
    parser.add_argument('--recipient', required=True, help='Explicitly authorized acceptance recipient')
    parser.add_argument('--send', action='store_true', help='Authorize up to two external messages')
    args = parser.parse_args()
    if not args.send or mode() != 'smtp':
        parser.error('Requires --send and real SMTP configuration')
    smtp_config()
    os.umask(0o077)
    args.data.mkdir(parents=False, exist_ok=False, mode=0o700)  # Durable one-run guard.
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    os.environ.update(SYNWEVO_DATA_DIR=str(args.data), SYNWEVO_ROLE='accounts',
                      SYNWEVO_UI_AUTH='session', SYNWEVO_COOKIE_SECURE='0',
                      SYNWEVO_PUBLIC_URL=origin, SYNWEVO_ISSUER=origin)
    api = create_app(str(args.data), start_jobs=False)  # No automatic retries or background sends.
    identity, mailer = api.state.identity, api.state.mailer
    password = secrets.token_urlsafe(32)
    owner = identity.create_user('smtp-fixture@example.invalid', password, status='active',
                                 display_name='SMTP 验收（隔离测试，无需操作）')
    identity.create_user(args.recipient, password, status='active', display_name='SMTP 验收收件人')
    workspace = identity.create_workspace('SMTP 验收（隔离测试，无需操作）', owner_id=owner['id'])
    server = uvicorn.Server(uvicorn.Config(api, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()

    def post(client, path, body=None):
        response = client.post(path, json=body or {},
                               headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ''})
        if response.status_code != 200:
            raise RuntimeError(f'Business route failed: {path} HTTP {response.status_code}')
        return response

    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError('Isolated HTTP server did not start')
            time.sleep(.05)
        with httpx.Client(base_url=origin, timeout=15, trust_env=False) as client:
            post(client, '/api/auth/login', {'email': owner['email'], 'password': password})
            response = post(client, f"/api/auth/workspaces/{workspace['id']}/invitations",
                            {'email': args.recipient, 'role': 'viewer', 'can_export': False})
            if response.json().get('mail') != 'queued':
                raise RuntimeError('Invitation did not enter mail queue')
        with httpx.Client(base_url=origin, timeout=15, trust_env=False) as client:
            post(client, '/api/auth/login', {'email': args.recipient, 'password': password})
            post(client, '/api/auth/verify-email/send')
        rows = identity.store.all('SELECT id,to_addr,kind,status,attempts FROM mail_outbox')
        if (len(rows) != 2 or {row['kind'] for row in rows} != {'invite', 'verify'}
                or any(row['to_addr'] != args.recipient or row['status'] != 'queued'
                       or row['attempts'] != 0 for row in rows)):
            raise RuntimeError('Recipient/count/queue guard refused sending')
        queued_at = time.time()
        mailer.drain_once(limit=2)  # Exactly one pass; never poll retries in external acceptance.
        result = {'fixture': True, 'transport': 'real SMTP', 'business_http': 'real loopback HTTP',
                  'recipient': args.recipient, 'queued_at': queued_at,
                  'messages': identity.store.all('SELECT id,kind,status,attempts,last_error,sent_at FROM mail_outbox'),
                  'inbox_visibility': 'not observed', 'maximum_messages': 2}
        (args.data / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if all(row['status'] == 'sent' and row['attempts'] == 1 for row in result['messages']) else 1
    finally:
        mailer.stop()
        server.should_exit = True
        thread.join(10)
        listener.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # A third-party exception may contain secrets or token-bearing responses.
        print(json.dumps({'acceptance_failed': type(exc).__name__, 'retry': 'forbidden until reviewed'}))
        raise SystemExit(1) from None
