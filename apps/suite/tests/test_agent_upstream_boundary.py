"""Where the platform key may go, and what identifies a payer to the provider.

The upstream override is test-only and loopback-only; the labelled FIXTURE test double
(apps/suite/scripts/deepseek_test_double.py) runs on 127.0.0.1 and is never a real model.
"""
import hashlib
import importlib.util
import json
import re
from pathlib import Path

import httpx
import pytest

from synwevo_core.billing import Ledger
from synwevo_core.store import Store
from synwevo_suite.agent.configuration import UPSTREAM_ENV, configured_provider
from synwevo_suite.agent.deepseek import BASE_URL, DeepSeekError, DeepSeekProvider, list_models, upstream_base
from synwevo_suite.agent.gateway import MeteredDeepSeek
from synwevo_suite.agent.provider import Credential

from .test_agent_metering import gateway  # noqa: F401 - fixture import

FIXTURE_KEY = 'fixture-loopback-key-not-real'
DOUBLE = Path(__file__).resolve().parents[1] / 'scripts' / 'deepseek_test_double.py'


def load_double():
    spec = importlib.util.spec_from_file_location('deepseek_test_double_fixture', DOUBLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def double():
    server = load_double().serve(models=('fixture-model', 'fixture-other'))
    yield server
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------- item 8: loopback, test only

@pytest.mark.parametrize('environment,override', [
    ('live', 'http://127.0.0.1:31790'),                 # never outside the test environment
    (None, 'http://127.0.0.1:31790'),
    ('test', 'http://example.test:31790'),              # never off loopback
    ('test', 'https://api.deepseek.com.evil.test'),
    ('test', 'http://127.0.0.1.nip.io:31790'),
    ('test', 'http://10.0.0.1:31790'),
    ('test', 'http://127.0.0.1'),                       # an explicit port is required
    ('test', 'http://fixture:secret@127.0.0.1:31790'),  # no credentials in the address
    ('test', 'http://127.0.0.1:31790/proxy'),            # the origin only
    ('test', 'ftp://127.0.0.1:31790'),
    ('test', 'not a url'),
])
def test_an_upstream_override_off_loopback_or_outside_test_is_refused(environment, override):
    with pytest.raises(ValueError):
        upstream_base(environment, override)


def test_loopback_overrides_in_test_and_the_official_origin_otherwise():
    assert upstream_base('live', None) == BASE_URL and upstream_base('test', '') == BASE_URL
    for override in ('http://127.0.0.1:31790', 'http://[::1]:31790', 'http://localhost:31790/'):
        assert upstream_base('test', override) == override.rstrip('/')


def test_the_factory_refuses_a_non_loopback_upstream_at_startup(tmp_path, monkeypatch):
    store = Store(tmp_path / 'upstream-startup')
    monkeypatch.setenv('SYNWEVO_AGENT_PROVIDER', 'deepseek')
    monkeypatch.setenv(UPSTREAM_ENV, 'http://127.0.0.1:31790')
    monkeypatch.setenv('SYNWEVO_BILLING_ENVIRONMENT', 'live')
    with pytest.raises(ValueError, match='refused'):
        configured_provider(store)
    monkeypatch.setenv('SYNWEVO_BILLING_ENVIRONMENT', 'test')
    monkeypatch.setenv(UPSTREAM_ENV, 'http://example.test:31790')
    with pytest.raises(ValueError, match='loopback'):
        configured_provider(store)
    monkeypatch.setenv(UPSTREAM_ENV, 'http://127.0.0.1:31790')
    provider = configured_provider(store)
    assert provider.base_url == provider.gateway.base_url == 'http://127.0.0.1:31790'
    monkeypatch.delenv(UPSTREAM_ENV)
    assert configured_provider(store).gateway.base_url == BASE_URL
    # Defence in depth: the gateway itself refuses a loopback upstream in the live environment.
    with pytest.raises(ValueError):
        MeteredDeepSeek(Ledger(store, environment='live', namespace='agent'), current_authority=lambda c: True,
                        base_url='http://127.0.0.1:31790')
    with pytest.raises(ValueError):
        DeepSeekProvider(base_url='http://example.test:31790')


def test_the_fixture_double_streams_the_provider_contract_over_real_http(double):
    credential = Credential('platform', FIXTURE_KEY, 'fixture-model', provider='deepseek')
    assert list_models(credential, base_url=double.base_url) == frozenset({'fixture-model', 'fixture-other'})
    provider = DeepSeekProvider(base_url=double.base_url)
    seen = []
    tools = [{'name': 'run_operation', 'description': 'fixture', 'input_schema': {'type': 'object'}}]
    step = dict(credential=credential, system=[{'type': 'text', 'text': 'fixture system'}], tools=tools,
                effort='high', on_text=seen.append, should_stop=lambda: False, user_id='fixture-pseudonym')
    answer = provider.step(messages=[{'role': 'user', 'content': 'hello'}], **step)
    assert ''.join(seen) == 'This is a FIXTURE answer.'
    assert answer.attempt.usage_reliability == 'trusted_terminal' and answer.attempt.usage.reasoning_tokens > 0
    assert answer.replay_message['reasoning_content'] == 'Considering the request (FIXTURE reasoning).'
    called = provider.step(messages=[{'role': 'user', 'content': 'go [fixture:tool]'}], **step)
    assert called.step.stop_reason == 'tool_use' and called.step.content[-1]['input'] == {}
    # Thinking + tools: the earlier turn's reasoning travels back, so the double accepts it.
    history = [{'role': 'user', 'content': 'go [fixture:tool]'}, called.replay_message,
               {'role': 'tool', 'tool_call_id': called.replay_message['tool_calls'][0]['id'], 'content': 'ok'},
               {'role': 'user', 'content': 'and now?'}]
    assert provider.step(messages=history, **step).attempt.usage_reliability == 'trusted_terminal'
    for marker, code in (('402', 'provider_balance'), ('429', 'provider_rate_limited'), ('503', 'provider_overloaded')):
        with pytest.raises(DeepSeekError) as err:
            provider.step(messages=[{'role': 'user', 'content': f'[fixture:{marker}]'}], **step)
        assert err.value.code == code and err.value.attempt.http_status == int(marker)
    with pytest.raises(DeepSeekError) as err:
        provider.step(messages=[{'role': 'user', 'content': '[fixture:cut]'}], **step)
    assert err.value.code == 'provider_stream_incomplete' and err.value.attempt.usage_reliability != 'trusted_terminal'


def test_the_fixture_double_only_listens_on_loopback():
    double = load_double()
    for host in ('0.0.0.0', '192.0.2.1', 'example.test'):
        with pytest.raises(ValueError, match='loopback'):
            double.DoubleServer(host, 0)


# ---------------------------------------------------------------- item 6: pseudonymous user_id

def test_every_request_carries_a_keyed_pseudonym_not_the_account_id(gateway):
    g = gateway
    g.model.step(**g.args)
    first = json.loads(g.seen[0].content)['user_id']
    payer = g.context.payer_user_id
    # Stable per payer (provider KV-cache isolation and abuse attribution need that).
    assert first == g.model.pseudonym(payer) == g.model.pseudonym(payer)
    assert re.fullmatch(r'[a-zA-Z0-9_-]{1,512}', first)
    assert payer not in first
    # Keyed: a guessed account id cannot be turned into it without the server's root secret.
    assert first != hashlib.sha256(f'test:agent:{payer}'.encode()).hexdigest()
    assert g.model.pseudonym('fixture-other-payer') != first


def test_the_pseudonym_depends_on_the_server_secret(tmp_path, gateway):
    other = Store(tmp_path / 'other-root')
    ledger = Ledger(other, environment='test', namespace='agent')
    elsewhere = MeteredDeepSeek(ledger, current_authority=lambda c: True)
    payer = gateway.context.payer_user_id
    assert elsewhere.pseudonym(payer) != gateway.model.pseudonym(payer)
