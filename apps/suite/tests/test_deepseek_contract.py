"""DeepSeek request-contract fixtures for the gateway package (FIXTURE transport only).

Every request goes through httpx.MockTransport or a loopback FIXTURE double; no real
DeepSeek endpoint, key or account is used. Rules mirror the official guides re-read on
2026-10-05 (thinking mode, reasoning_effort, user_id, error codes) and must still be confirmed
by the owner-approved minimal live run before charging is enabled.
"""
from __future__ import annotations

import json

import httpx
import pytest

from synwevo_suite.agent.deepseek import DeepSeekCancelled, DeepSeekError, DeepSeekProvider
from synwevo_suite.agent.provider import Credential

from .test_deepseek import KEY, TOOL, USAGE, Wire, complete, event


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("A deterministic DeepSeek test attempted a real HTTP transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def provider_for(body: bytes, seen: list, *, status=200, **options):
    async def respond(request):
        seen.append(request)
        return httpx.Response(status, headers={"content-type": "text/event-stream"}, stream=Wire(body))
    return DeepSeekProvider(transport=httpx.MockTransport(respond), **options)


def arguments(**changes):
    args = dict(credential=Credential("platform", KEY, "deepseek-flash", provider="deepseek"),
                system=[{"type": "text", "text": "fixture system"}], tools=[],
                messages=[{"role": "user", "content": "fixture request"}], effort="medium",
                on_text=lambda _: None, should_stop=lambda: False)
    args.update(changes)
    return args


# ------------------------------------------------------------------ 1. reasoning replay

def test_interrupted_stream_hands_back_its_private_reasoning_without_unrun_calls():
    partial_call = {"index": 0, "id": "fixture-call", "type": "function",
                    "function": {"name": "fixture_lookup", "arguments": '{"pla'}}
    body = (event({"reasoning_content": "fixture thought "}) + event({"reasoning_content": "continues"})
            + event({"content": "Partial"}) + event({"tool_calls": [partial_call]}))
    seen = []
    with pytest.raises(DeepSeekError) as exc:
        provider_for(body, seen).step(**arguments(tools=[TOOL]))
    assert exc.value.code == "provider_stream_incomplete"
    assert exc.value.replay_message == {"role": "assistant", "content": "Partial",
                                        "reasoning_content": "fixture thought continues"}
    assert "fixture thought" not in repr(exc.value) + str(exc.value)


def test_cancelled_stream_keeps_reasoning_and_unsent_failures_have_none():
    stop = []
    def on_text(_):
        stop.append(True)
    seen = []
    with pytest.raises(DeepSeekCancelled) as exc:
        provider_for(event({"reasoning_content": "fixture plan", "content": "Hi"}) + event({"content": " there"}), seen
                     ).step(**arguments(on_text=on_text, should_stop=lambda: bool(stop)))
    assert exc.value.replay_message == {"role": "assistant", "content": "Hi", "reasoning_content": "fixture plan"}
    with pytest.raises(DeepSeekError) as refused:
        provider_for(b"", [], status=503).step(**arguments())
    assert refused.value.replay_message is None


def test_thinking_requests_give_every_assistant_message_a_reasoning_string():
    history = [{"role": "user", "content": "first"},
               {"role": "assistant", "content": "Legacy visible text only"},
               {"role": "user", "content": "second"},
               {"role": "assistant", "content": "Native", "reasoning_content": "fixture kept"},
               {"role": "user", "content": "third"}]
    seen = []
    provider_for(complete(), seen).step(**arguments(messages=history, tools=[TOOL]))
    sent = [m for m in json.loads(seen[0].content)["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in sent] == ["", "fixture kept"]
    # With thinking switched off nothing is invented, and saved reasoning is still not dropped.
    seen.clear()
    provider_for(complete(), seen, thinking=False).step(**arguments(messages=history, tools=[TOOL]))
    sent = [m for m in json.loads(seen[0].content)["messages"] if m["role"] == "assistant"]
    assert "reasoning_content" not in sent[0] and sent[1]["reasoning_content"] == "fixture kept"
