"""Import credentials/workflow into a fresh local n8n store and execute standard HTTP nodes."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess

import runpy

running = runpy.run_path(str(Path(__file__).with_name("n8n_receiver.py")))["running"]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def execute(node, n8n, evidence, base, oauth):
    folder = evidence / 'n8n-home'
    folder.mkdir(mode=0o700)
    out = evidence / 'n8n-output'
    out.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('N8N_', 'SYNWEVO_', 'DOCWEAVE_'))}
    env.update(N8N_USER_FOLDER=str(folder), N8N_ENCRYPTION_KEY=secrets.token_hex(32),
               N8N_LISTEN_ADDRESS='127.0.0.1', N8N_HOST='127.0.0.1', N8N_PORT=str(free_port()),
               N8N_RUNNERS_BROKER_LISTEN_ADDRESS='127.0.0.1', N8N_RUNNERS_BROKER_PORT=str(free_port()),
               N8N_DIAGNOSTICS_ENABLED='false', N8N_VERSION_NOTIFICATIONS_ENABLED='false',
               N8N_TEMPLATES_ENABLED='false', N8N_COMMUNITY_PACKAGES_ENABLED='false',
               N8N_DEFAULT_BINARY_DATA_MODE='filesystem', N8N_RESTRICT_FILE_ACCESS_TO=str(evidence),
               NODE_FUNCTION_ALLOW_BUILTIN='crypto', N8N_BLOCK_ENV_ACCESS_IN_NODE='true',
               N8N_PERSONALIZATION_ENABLED='false', NO_PROXY='127.0.0.1,localhost,::1',
               no_proxy='127.0.0.1,localhost,::1', NODE_USE_ENV_PROXY='0', PYTHONDONTWRITEBYTECODE='1')
    env['PATH'] = str(Path(node).resolve().parent) + os.pathsep + env.get('PATH', '')

    def command(label, *args):
        argv = [str(node), str(n8n), *map(str, args)]
        command_env = dict(env)
        if args and args[0] == 'execute':
            command_env['N8N_RUNNERS_BROKER_PORT'] = str(free_port())
        p = subprocess.run(argv, env=command_env, capture_output=True, text=True, timeout=300)
        # These commands contain credential file paths, never literal secrets.
        (evidence / f'n8n-{label}.stdout').write_text(p.stdout)
        (evidence / f'n8n-{label}.stderr').write_text(p.stderr)
        with (evidence / 'commands.jsonl').open('a') as f:
            f.write(json.dumps({'argv': argv, 'exit_code': p.returncode}) + '\n')
        if p.returncode:
            raise RuntimeError(f'n8n {label} failed: see saved logs')
        return p.stdout

    credentials = evidence / '.n8n-credentials.json'
    credentials.touch(mode=0o600)
    credentials.write_text(json.dumps([{'id': 'synwevo-synthetic-oauth', 'name': 'Synthetic Synwevo only',
        'type': 'oAuth2Api', 'data': {'grantType': 'clientCredentials',
        'accessTokenUrl': oauth['tokenUrl'], 'clientId': oauth['clientId'], 'clientSecret': oauth['clientSecret'],
        'scope': oauth['scope'], 'authentication': 'body', 'sendAdditionalBodyProperties': True,
        'additionalBodyProperties': json.dumps({'resource': oauth['resource']})}}]))
    try:
        command('credentials', 'import:credentials', '--input', credentials)
    finally:
        credentials.unlink(missing_ok=True)
    workflow = json.loads((Path(__file__).parents[3] / 'apps/docs/sdk/n8n/generate-documents.json').read_text())
    for n in workflow['nodes']:
        if n['name'] == 'Config':
            code = n['parameters']['jsCode']
            code = code.replace("'http://127.0.0.1:1'", json.dumps(base))
            code = code.replace("'/replace-with-synthetic-kit'", json.dumps(str(evidence / 'kit')))
            code = code.replace("'/replace-with-output-directory'", json.dumps(str(out)))
            n['parameters']['jsCode'] = code
        if n['parameters'].get('genericAuthType') == 'oAuth2Api':
            n['credentials'] = {'oAuth2Api': {'id': 'synwevo-synthetic-oauth', 'name': 'Synthetic Synwevo only'}}
    imported = evidence / 'n8n-import.json'
    imported.write_text(json.dumps(workflow, indent=2))
    command('import', 'import:workflow', '--input', imported)
    with running(node, n8n, evidence, base, oauth, env, command, free_port) as verify_receiver:
        raw = command('execute', 'execute', '--id', workflow['id'], '--rawOutput')
        # n8n startup notices can precede its execution JSON.
        decoder = json.JSONDecoder()
        execution = None
        for i, c in enumerate(raw):
            if c != '{':
                continue
            try:
                value, _ = decoder.raw_decode(raw[i:])
                if isinstance(value, dict) and 'data' in value and 'resultData' in value['data']:
                    execution = value
                    break
            except (ValueError, TypeError):
                continue
        assert execution, 'Missing n8n execution result'
        result_data = execution['data']['resultData']
        assert not result_data.get('error'), result_data.get('error')
        result = result_data['runData']['Summary'][-1]['data']['main'][0][0]['json']
        (evidence / 'n8n-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        receiver_result = verify_receiver(result)
        (evidence / 'receiver-result.json').write_text(json.dumps(receiver_result, ensure_ascii=False, indent=2))
        return result
