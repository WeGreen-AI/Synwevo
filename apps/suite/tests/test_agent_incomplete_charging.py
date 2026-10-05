"""The incomplete-call charging policy (shared contract): governed, pinned with the quote, applied
in settlement. Real Store/Ledger; FIXTURE SSE and synthetic prices (charge 42 = 8x2 + 5x1 + 7x3).
"""
from dataclasses import asdict, replace
import json

import httpx
import pytest

from synwevo_core.billing import BillingError
from synwevo_core.billing.provider_settings import INCOMPLETE_KINDS, ProviderSpec
from synwevo_core.errors import ServiceError
from synwevo_suite.agent.deepseek import DeepSeekCancelled, DeepSeekError
from synwevo_suite.agent.gateway import Route
from synwevo_suite.agent.provider import ProviderError

from .test_agent_governed_factory import configure, fund, governed, publish, start  # noqa: F401 - fixture import
from .test_agent_metering import STAMP, USAGE, Wire, event, gateway  # noqa: F401 - fixture import

ENDINGS = {'length': ('length', ProviderError), 'content_filter': ('content_filter', ProviderError),
           'provider_interrupted': ('aborted', DeepSeekError)}


def run(g, *, charge=(), name='fixture-charged-run'):
    """A separately authorized run whose pinned route carries the given policy."""
    context = replace(g.context, run_id=name)
    g.model.open_run(context, route=replace(g.route, charge_incomplete=charge), budget=1000, max_calls=3,
                     deadline=STAMP + 500)
    return g.args | {'context': context}


def usage_row(g):
    return g.store.one('SELECT outcome,charge FROM billing_usage')


class Then(httpx.AsyncByteStream):
    """Yields the final usage chunk, then lets the person stop before [DONE] arrives."""
    def __init__(self, first, rest, between):
        self.first, self.rest, self.between = first, rest, between

    async def __aiter__(self):
        yield self.first
        self.between()
        yield self.rest


@pytest.mark.parametrize('kind', sorted(ENDINGS))
def test_the_default_policy_charges_no_incomplete_call(gateway, kind):
    g = gateway
    finish, error = ENDINGS[kind]
    g.holder['body'] = event({'content': 'partial'}) + event(finish=finish, usage=USAGE) + b'data: [DONE]\n\n'
    with pytest.raises(error):
        g.model.step(**run(g))
    assert usage_row(g) == {'outcome': 'platform_fault', 'charge': 0}
    assert g.balance().booked == 1000 and g.balance().held == 0


@pytest.mark.parametrize('kind', sorted(ENDINGS))
def test_a_charged_ending_sells_its_trusted_usage_within_the_hold(gateway, kind):
    g = gateway
    finish, error = ENDINGS[kind]
    g.holder['body'] = event({'content': 'partial'}) + event(finish=finish, usage=USAGE) + b'data: [DONE]\n\n'
    with pytest.raises(error):
        g.model.step(**run(g, charge=(kind,)))  # The turn still fails for the person.
    assert usage_row(g) == {'outcome': 'completed', 'charge': 42}
    assert g.balance().booked == 958 and g.balance().held == 0
    evidence = json.loads(g.store.one('SELECT evidence FROM agent_model_observations')['evidence'])
    assert evidence['ending'] == kind and evidence['completed'] is False


def test_a_policy_for_one_ending_does_not_charge_another(gateway):
    g = gateway
    g.holder['body'] = event(finish='content_filter', usage=USAGE) + b'data: [DONE]\n\n'
    with pytest.raises(ProviderError):
        g.model.step(**run(g, charge=('length', 'provider_interrupted', 'client_cancelled')))
    assert usage_row(g) == {'outcome': 'platform_fault', 'charge': 0}


@pytest.mark.parametrize('charge,expected,booked', [((), {'outcome': 'platform_fault', 'charge': 0}, 1000),
                                                    (('client_cancelled',), {'outcome': 'completed', 'charge': 42}, 958)])
def test_stopping_after_the_final_usage_settles_it_by_policy(gateway, charge, expected, booked):
    g = gateway
    args = run(g, charge=charge)
    first = event({'content': 'partial answer'}) + event(finish='stop', usage=USAGE)
    async def respond(request):
        g.seen.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              stream=Then(first, b'data: [DONE]\n\n', lambda: g.stopped.__setitem__(0, True)))
    g.model.transport = httpx.MockTransport(respond)
    with pytest.raises(DeepSeekCancelled) as err:
        g.model.step(**args)
    assert err.value.attempt.usage_reliability == 'trusted_terminal'
    assert err.value.attempt.usage_reason == 'terminal_usage_before_cancel'
    assert usage_row(g) == expected
    # Known usage: the hold is released (or sold) at once instead of waiting for its deadline.
    assert g.balance().booked == booked and g.balance().held == 0


def test_stopping_before_the_final_usage_still_leaves_usage_unknown_and_unsold(gateway):
    g = gateway
    args = run(g, charge=INCOMPLETE_KINDS)
    async def respond(request):
        g.seen.append(request)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Then(
            event({'content': 'partial'}), event(finish='stop', usage=USAGE) + b'data: [DONE]\n\n',
            lambda: g.stopped.__setitem__(0, True)))
    g.model.transport = httpx.MockTransport(respond)
    with pytest.raises(DeepSeekCancelled):
        g.model.step(**args)
    assert g.store.all('SELECT * FROM billing_usage') == []
    assert g.balance().held == 297 and g.balance().booked == 1000


def test_invalid_or_rejected_output_is_never_charged_even_when_every_ending_is(gateway):
    g = gateway
    args = run(g, charge=INCOMPLETE_KINDS)
    g.holder['body'] = event({'content': 'fine text'}) + event(finish='stop', usage=USAGE) + b'data: [DONE]\n\n'
    def reject(step):
        raise ProviderError('provider_bad_output', 'fixture rejection')
    with pytest.raises(ProviderError):
        g.model.step(**(args | {'validate_result': reject}))
    assert usage_row(g) == {'outcome': 'platform_fault', 'charge': 0}
    assert g.balance().booked == 1000 and g.balance().held == 0


def test_route_snapshots_keep_the_policy_canonical_and_older_ones_charge_nothing(gateway):
    g = gateway
    route = replace(g.route, charge_incomplete=['client_cancelled', 'length'])
    assert route.charge_incomplete == ('length', 'client_cancelled')
    assert Route(**json.loads(json.dumps(asdict(route)))) == route
    older = {k: v for k, v in asdict(g.route).items() if k != 'charge_incomplete'}
    assert Route(**older).charge_incomplete == ()
    for invalid in (['length', 'length'], ['everything'], 'length'):
        with pytest.raises(BillingError):
            replace(g.route, charge_incomplete=invalid)
    stored = json.dumps({'enabled': False, 'route': None})  # A version from before the field.
    assert ProviderSpec.model_validate_json(stored).incomplete_charging.charged() == []


def test_publishing_a_policy_is_a_new_version_that_invalidates_existing_quotes(governed):
    g = governed; configure(g); fund(g)
    assert g.provider.route().charge_incomplete == ()
    quote = g.agent.quote_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None, regenerate_of=None,
                               budget=5000, max_calls=3, run_seconds=300)
    before = g.settings.read()['revision']
    publish(g, {'action': 'keep'}, {**g.spec, 'incomplete_charging': {'length': True, 'content_filter': False,
                                                                      'provider_interrupted': False, 'client_cancelled': False}})
    state = g.settings.read()
    assert state['revision'] == before + 1 and state['spec']['incomplete_charging']['length'] is True
    assert g.provider.route().charge_incomplete == ('length',)
    with pytest.raises(ServiceError) as err:  # The person re-confirms under the new terms.
        g.agent.start_turn(g.user, g.conversation['id'], 'Read my forms', parent_id=None,
                           regenerate_of=None, quote_id=quote['id'])
    assert 'model_configuration_changed' in err.value.code
    with pytest.raises(ServiceError):  # Unknown kinds are refused, nothing is published.
        publish(g, {'action': 'keep'}, {**g.spec, 'incomplete_charging': {'everything': True}})
    assert g.settings.read()['revision'] == before + 1
    tid = start(g); g.agent._run(tid)
    assert g.store.one('SELECT status FROM agent_turns WHERE id=?', (tid,))['status'] == 'succeeded'
    pinned = json.loads(g.store.one('SELECT route FROM agent_metered_runs')['route'])
    assert pinned['charge_incomplete'] == ['length']
