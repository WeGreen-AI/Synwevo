"""Controlled synthetic Account fixture for the TS/n8n acceptance runner.

Only identity/credential setup and operator governance live here, outside the external SDK.
Business projects, template fields and mappings are created by the clients over HTTP.
"""
from contextlib import contextmanager
import json
import subprocess
import sys


@contextmanager
def account_fixture(testenv, instance, evidence, base):
    """Controlled synthetic identity setup; every access token comes from real HTTP OAuth."""
    from synwevo_core.store import Store
    from synwevo_core.identity import Identity
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.oidc import Clients
    store = Store(instance.data_dir)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    user = ident.create_user('sdk-fixture@example.test', 'synthetic-only-long-password', status='active')
    workspace = ident.workspaces_for(user['id'])[0]['id']
    client_id = 'synthetic-ts-n8n'
    _, secret = Clients(store).register(client_id=client_id, app_id='docweave', name='Synthetic SDK acceptance',
        redirect_uris=[], confidential=True, kind='service', workspace_id=workspace,
        api_scopes='docweave.read docweave.write', audiences=['urn:synwevo:app:docweave'])
    child = testenv._child_env(instance.data_dir.parent, int(base.rsplit(':', 1)[1]))
    child['SYNWEVO_ROLE'] = 'accounts'
    def set_enabled(enabled, child):
        argv = [sys.executable, '-m', 'synwevo_identity.cli', '--data', str(instance.data_dir),
                'client', 'enable' if enabled else 'disable', client_id]
        result = subprocess.run(argv, env=child, capture_output=True, text=True, check=True)
        with (evidence / 'commands.jsonl').open('a') as log:
            log.write(json.dumps({'argv': argv, 'exit_code': result.returncode}) + '\n')
        return True
    with (evidence / 'account.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, '-m', 'synwevo_server.run'], env=child,
                                   cwd=instance.data_dir.parent, stdout=log, stderr=log)
    try:
        testenv._wait(base, process, evidence / 'account.log')
        assert testenv.health(base)['instance'] == 'test'
        yield {'tokenUrl': base + '/oidc/token', 'clientId': client_id, 'clientSecret': secret,
               'scope': 'docweave.read docweave.write', 'resource': 'urn:synwevo:app:docweave',
               'workspace': workspace, 'dataDir': instance.data_dir, 'child': child,
               'deliveries': lambda sid: store.one('SELECT event_id,status,last_code FROM webhook_deliveries WHERE subscription_id=? ORDER BY rowid DESC LIMIT 1', (sid,)),
               'set_enabled': lambda enabled: set_enabled(enabled, child)}
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
