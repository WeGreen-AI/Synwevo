"""Holds sized from the actual request (a UTF-8 upper bound on prompt tokens), not the route's
whole context ceiling. Real Store/Ledger; FIXTURE SSE and synthetic prices.
"""
from dataclasses import replace
import json

import httpx
import pytest

from synwevo_suite.agent.gateway import TEMPLATE_TOKENS, TOOLS_TEMPLATE_TOKENS, MeteredDeepSeek

from .test_agent_metering import STAMP, Wire, complete, gateway  # noqa: F401 - fixture import

CEILING = 1_000_000  # A 1M-token context: the old hold for one call was 2,000,097 units.


def wide(g, name='fixture-wide-run'):
    context = replace(g.context, run_id=name)
    route = replace(g.route, input_token_ceiling=CEILING)
    g.model.open_run(context, route=route, budget=3_000_000, max_calls=3, deadline=STAMP + 500)
    return route, g.args | {'context': context}


def test_a_short_request_holds_its_bound_not_the_whole_context(gateway):
    g = gateway
    route, args = wide(g)
    held = []
    async def respond(request):
        g.seen.append(request)
        held.append(g.balance().held)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=Wire(complete()))
    g.model.transport = httpx.MockTransport(respond)
    bound = MeteredDeepSeek.input_bound(route, system=args['system'], tools=args['tools'], messages=args['messages'])
    expected = MeteredDeepSeek.upper_bound(route, g.sale, input_tokens=bound)
    assert bound < 300 and expected < 1000  # The wallet (1000 units) could never cover the ceiling hold.
    assert MeteredDeepSeek.upper_bound(route, g.sale) == 2_000_097
    g.model.step(**args)
    assert held == [expected]
    assert g.balance().booked == 958 and g.balance().held == 0  # Settled at 42, the rest released.
    evidence = json.loads(g.store.one('SELECT evidence FROM agent_model_observations')['evidence'])
    assert evidence['input_bound'] == bound and evidence['raw_usage']['prompt_tokens'] <= bound


def test_the_bound_covers_every_utf8_byte_and_the_template_and_is_capped():
    route = type('R', (), {'input_token_ceiling': CEILING})()
    for text in ('ascii prompt', '中文提示与工具结果', 'emoji 🙂🙂 and \u0000 control', 'x' * 10_000):
        messages = [{'role': 'user', 'content': text}]
        bound = MeteredDeepSeek.input_bound(route, system=[], tools=[], messages=messages)
        # A byte-level BPE text never has more tokens than UTF-8 bytes.
        assert bound >= len(text.encode('utf-8')) + TEMPLATE_TOKENS
    tools = [{'name': 'run_operation', 'description': '描述' * 50, 'input_schema': {'type': 'object'}}]
    plain = MeteredDeepSeek.input_bound(route, system=[], tools=[], messages=[{'role': 'user', 'content': 'hi'}])
    with_tools = MeteredDeepSeek.input_bound(route, system=[], tools=tools, messages=[{'role': 'user', 'content': 'hi'}])
    assert with_tools >= plain + TOOLS_TEMPLATE_TOKENS + len('描述'.encode()) * 50
    # Replayed private reasoning is sent, so it counts too.
    replay = [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a', 'reasoning_content': 'r' * 5000},
              {'role': 'user', 'content': 'q2'}]
    assert MeteredDeepSeek.input_bound(route, system=[], tools=[], messages=replay) > 5000
    small = type('R', (), {'input_token_ceiling': 100})()
    assert MeteredDeepSeek.input_bound(small, system=[], tools=[], messages=[{'role': 'user', 'content': 'x' * 1000}]) == 100


def test_the_hold_never_exceeds_the_ceiling_hold(gateway):
    g = gateway
    route = replace(g.route, input_token_ceiling=50)
    assert MeteredDeepSeek.upper_bound(route, g.sale, input_tokens=10_000) == MeteredDeepSeek.upper_bound(route, g.sale)
    with pytest.raises(Exception):
        MeteredDeepSeek.input_bound(route, system=[], tools=[], messages=[{'role': 'user', 'content': float('nan')}])
