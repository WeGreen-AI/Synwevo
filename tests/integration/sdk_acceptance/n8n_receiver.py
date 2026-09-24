"""Synthetic local n8n receiver acceptance. No existing server, credentials, or data accepted."""
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with DIRECT.open(req, timeout=55) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def wait_ready(url, process):
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Local receiver process exited; inspect logs')
        try:
            if request(url)[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.2)
    raise RuntimeError('Local receiver startup timed out')


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def running(node, n8n, evidence, base, oauth, env, command, free_port):
    root = Path(__file__).parents[3] / 'apps/docs/sdk/n8n'
    receiver_base = f"http://127.0.0.1:{env['N8N_PORT']}"
    webhook_url = receiver_base + '/webhook/synwevo-events'
    verifier_port = free_port()
    verifier_base = f'http://127.0.0.1:{verifier_port}'
    output = evidence / 'receiver-output'
    output.mkdir()
    state = evidence / 'receiver-state'
    token = secrets.token_hex(32)
    auth_file = evidence / '.receiver-auth'
    auth_file.touch(mode=0o600)
    auth_file.write_text(token)
    # Operator CLI creates only a synthetic subscription; service credentials remain read/write.
    argv = [sys.executable, '-m', 'synwevo_docs.cli', '--data', str(oauth['dataDir']),
            'webhooks', 'add', '--tenant', oauth['workspace'], '--url', webhook_url,
            '--events', 'job.completed', '--json']
    registered = subprocess.run(argv, env=oauth['child'], capture_output=True, text=True, check=True)
    subscription = json.loads(registered.stdout)
    secret = subscription['secret']
    with (evidence / 'commands.jsonl').open('a') as log:
        log.write(json.dumps({'argv': argv, 'exit_code': 0, 'stdout': 'one-time signing secret not retained'}) + '\n')
    credentials = evidence / '.receiver-credential.json'
    credentials.touch(mode=0o600)
    credentials.write_text(json.dumps([{'id': 'synwevo-synthetic-verifier', 'name': 'Synthetic verifier only',
        'type': 'httpCustomAuth', 'data': {'json': json.dumps({'headers': {
            'Authorization': 'Bearer ' + token, 'X-Synwevo-Signing-Secret': secret}})}}]))
    try:
        command('receiver-credential', 'import:credentials', '--input', credentials)
    finally:
        credentials.unlink(missing_ok=True)
    workflow = json.loads((root / 'receive-documents.json').read_text())
    for n in workflow['nodes']:
        if n['name'] == 'Receiver config':
            code = n['parameters']['jsCode']
            for old, new in [("'http://127.0.0.1:1'", base), ("'http://127.0.0.1:2'", verifier_base),
                             ("'/replace-with-receiver-output'", str(output)), ("'replace-with-workspace'", oauth['workspace'])]:
                code = code.replace(old, json.dumps(new))
            n['parameters']['jsCode'] = code
        auth = n['parameters'].get('genericAuthType')
        if auth:
            ident, name = ('synwevo-synthetic-oauth', 'Synthetic Synwevo only') if auth == 'oAuth2Api' else ('synwevo-synthetic-verifier', 'Synthetic verifier only')
            n['credentials'] = {auth: {'id': ident, 'name': name}}
    imported = evidence / 'receiver-import.json'
    imported.write_text(json.dumps(workflow, indent=2))
    command('receiver-import', 'import:workflow', '--input', imported)
    command('receiver-activate', 'publish:workflow', '--id', workflow['id'])
    processes = []
    def start_verifier():
        args = [str(node), str(root / 'verify-receiver.mjs'), str(auth_file), str(state), oauth['workspace'], str(verifier_port)]
        with (evidence / 'verifier.log').open('ab') as log:
            p = subprocess.Popen(args, env=env, stdout=log, stderr=log)
        processes.append(p)
        wait_ready(verifier_base + '/healthz', p)
        return p
    try:
        verifier = start_verifier()
        with (evidence / 'n8n-server.log').open('wb') as log:
            server = subprocess.Popen([str(node), str(n8n), 'start'], env=env, stdout=log, stderr=log)
        processes.append(server)
        wait_ready(receiver_base + '/healthz', server)
        # Health precedes workflow activation. A rejected unsigned delivery proves the route is ready.
        deadline = time.monotonic() + 30
        while request(webhook_url, b'{}', {'Content-Type': 'application/json'})[0] != 401:
            if time.monotonic() > deadline or server.poll() is not None:
                raise RuntimeError('n8n webhook activation failed; inspect log')
            time.sleep(.2)
        def verify(result):
            nonlocal verifier
            deadline = time.monotonic() + 60
            delivery = None
            while time.monotonic() < deadline:
                delivery = oauth['deliveries'](subscription['id'])
                if delivery and delivery['status'] == 'delivered':
                    break
                time.sleep(.3)
            assert delivery and delivery['status'] == 'delivered', delivery
            assert delivery['last_code'] == 204, delivery
            real = json.loads((output / (delivery['event_id'] + '.json')).read_text())
            assert real['job_id'] == result['job']['id']
            def probe(ident, *, offset=0, alter=False, wrong=False, tenant=None):
                event = {'id': ident, 'type': 'job.completed', 'version': 1, 'tenant': tenant or oauth['workspace'],
                         'job': {'id': result['job']['id']}, 'data': {}}
                raw = json.dumps(event, ensure_ascii=False, indent=1).encode()
                timestamp = str(int(time.time()) + offset)
                key = base64.b64decode(secret[6:])
                signature = base64.b64encode(hmac.new(key, ident.encode() + b'.' + timestamp.encode() + b'.' + raw, hashlib.sha256).digest()).decode()
                headers = {'Content-Type': 'application/json', 'webhook-id': ident,
                           'webhook-timestamp': timestamp, 'webhook-signature': 'v1,' + ('AAAA' if wrong else signature)}
                return request(webhook_url, raw + (b' ' if alter else b''), headers)[0]
            checks = {'dispatcher_event': delivery['event_id'], 'dispatcher_status': delivery['last_code']}
            for name, kwargs in [('tampered', {'alter': True}), ('wrong_signature', {'wrong': True}),
                                 ('stale', {'offset': -400}), ('future', {'offset': 400}), ('wrong_workspace', {'tenant': 'synthetic-other-workspace'})]:
                checks[name] = probe('probe-' + name, **kwargs)
                assert checks[name] == 401, checks
            checks['valid'] = probe('probe-valid')
            assert checks['valid'] == 204, checks
            before = {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
            checks['duplicate'] = probe('probe-valid')
            assert checks['duplicate'] == 204
            assert before == {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
            stop(verifier)
            verifier = start_verifier()
            checks['restart_duplicate'] = probe('probe-valid')
            assert checks['restart_duplicate'] == 204
            assert before == {p.name: p.stat().st_mtime_ns for p in output.iterdir()}
            assert oauth['set_enabled'](False)
            try:
                checks['disabled_service'] = probe('probe-retry')
                assert checks['disabled_service'] == 503, checks
                assert not (output / 'probe-retry.json').exists()
            finally:
                oauth['set_enabled'](True)
            checks['reenabled_service_retry'] = probe('probe-retry')
            assert checks['reenabled_service_retry'] == 204, checks
            (evidence / 'receiver-verification.json').write_text(json.dumps(checks, indent=2) + '\n')
            # No workflow or execution log may contain imported secret material.
            for path in [evidence / 'n8n-import.json', imported, *evidence.glob('*.stdout'), *evidence.glob('*.stderr'), evidence / 'n8n-server.log']:
                data = path.read_text()
                assert secret not in data and token not in data and oauth['clientSecret'] not in data, path
            return real
        yield verify
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                stop(p)
        auth_file.unlink(missing_ok=True)
