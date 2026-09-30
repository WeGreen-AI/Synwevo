"""Isolated protocol fixtures: real SSE bytes through httpx.MockTransport, never paid calls.

Fixtures follow the official 2026-09-30 wire schema; they are not captured production
traffic and do not prove live account routing, pricing, quota or service availability.
"""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import replace

import httpx
import pytest

from synwevo_suite.agent.deepseek import (
    DeepSeekCancelled, DeepSeekError, DeepSeekProvider, Limits, normalize_usage,
)
from synwevo_suite.agent.provider import Credential

KEY = "fixture-key-not-a-real-secret"
USAGE = {"prompt_tokens": 13, "prompt_cache_hit_tokens": 5, "prompt_cache_miss_tokens": 8,
         "completion_tokens": 7, "total_tokens": 20, "prompt_tokens_details": {"cached_tokens": 5},
         "completion_tokens_details": {"reasoning_tokens": 3}}
TOOL = {"name": "fixture_lookup", "description": "Isolated fixture", "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {"place": {"type": "string"}}}}


def event(delta=None, finish=None, usage=None, **changes):
    chunk = {"id": "fixture-request-1", "model": "deepseek-flash", "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}], "usage": usage}
    chunk.update(changes)
    return ("data: " + json.dumps(chunk, ensure_ascii=False) + "\r\n\r\n").encode()


def complete(usage=USAGE, finish="stop"):
    return event({"content": "你好"}) + event(finish=finish, usage=usage) + b"data: [DONE]\n\n"


class Wire(httpx.AsyncByteStream):
    def __init__(self, body, *, split=7, error=None):
        self.body, self.split, self.error, self.closed = body, split, error, False

    async def __aiter__(self):
        for offset in range(0, len(self.body), self.split):
            yield self.body[offset:offset + self.split]
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("A deterministic DeepSeek test attempted a real HTTP transport")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


def invoke(body=None, *, limits=None, status=200, headers=None, wire=None, handler=None, **kwargs):
    seen, visible = [], []
    stream = wire or Wire(complete() if body is None else body)

    async def respond(request):
        seen.append(request)
        if handler:
            return await handler(request)
        return httpx.Response(status, headers={"Content-Type": "text/event-stream", **(headers or {})}, stream=stream)

    provider = DeepSeekProvider(transport=httpx.MockTransport(respond), limits=limits)
    args = dict(credential=Credential("platform", KEY, "deepseek-v4-pro"), system=[{"type": "text", "text": "fixture system"}],
                tools=[], messages=[{"role": "user", "content": "fixture request"}], effort="medium",
                on_text=visible.append, should_stop=lambda: False)
    args.update(kwargs)
    return provider, args, seen, visible, stream


def run(body=None, **kwargs):
    provider, args, seen, visible, wire = invoke(body, **kwargs)
    result = provider.step(**args)
    return result, seen, visible, wire


def test_terminal_content_chunk_usage_and_fixed_endpoint(monkeypatch):
    for name in ("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "https://fixture-evil.invalid")
    result, seen, visible, wire = run(b": keep-alive\r\n\r\n" + complete(), user_id="opaque_fixture_user")
    assert visible == ["你好"] and wire.closed and len(seen) == 1
    assert str(seen[0].url) == "https://api.deepseek.com/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer " + KEY
    body = json.loads(seen[0].content)
    assert body["model"] == "deepseek-v4-pro"  # Never silently rewrite the configured alias.
    assert body["max_tokens"] == 8192 and body["reasoning_effort"] == "high"
    assert body["stream_options"] == {"include_usage": True} and body["thinking"] == {"type": "enabled"}
    assert body["user_id"] == "opaque_fixture_user"
    assert "base_url" not in body and "tools" not in body
    assert result.attempt.dispatch == "observed" and result.attempt.provider_request_id == "fixture-request-1"
    assert result.attempt.returned_model == "deepseek-flash" and result.attempt.effective_route is None
    assert result.attempt.usage_reliability == "trusted_terminal"
    assert result.attempt.usage.prompt_tokens == 13
    assert result.attempt.usage.completion_tokens == 7  # Reasoning is not an extra three tokens.
    assert result.attempt.usage.reasoning_tokens == 3
    assert result.step.usage == {}  # No fabricated compatibility counters; coordinator must adapt.
    assert result.step.stop_reason == "end_turn"


def test_fragmented_tools_private_reasoning_and_lossless_replay():
    first = {"index": 0, "id": "call_fixture", "type": "function", "function": {"name": "fixture_lookup", "arguments": '{ "place" : "'}}
    second = {"index": 0, "function": {"arguments": '香港" }'}}
    body = (event({"reasoning_content": "private fixture reasoning"}) + event({"content": "Checking"})
            + event({"tool_calls": [first]}) + event({"tool_calls": [second]})
            + event(finish="tool_calls", usage=USAGE) + b"data: [DONE]\n\n")
    result, _, visible, _ = run(body, tools=[TOOL])
    assert visible == ["Checking"]
    assert "private fixture reasoning" not in repr(result) + repr(result.step)
    assert result.replay_message["reasoning_content"] == "private fixture reasoning"
    assert result.step.content[1] == {"type": "tool_use", "id": "call_fixture", "name": "fixture_lookup", "input": {"place": "香港"}}
    history = [{"role": "user", "content": "fixture"}, result.replay_message,
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_fixture", "content": "fixture output", "is_error": True}]},
               {"role": "assistant", "content": "Earlier answer", "reasoning_content": "earlier private reasoning"},
               {"role": "user", "content": "next turn"}]
    original = copy.deepcopy(history)
    _, seen, _, _ = run(tools=[TOOL], messages=history)
    sent = json.loads(seen[0].content)
    assert history == original
    assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{ "place" : "香港" }'
    assert sent["messages"][2]["reasoning_content"] == "private fixture reasoning"
    assert sent["messages"][3] == {"role": "tool", "tool_call_id": "call_fixture", "content": "fixture output"}
    assert sent["messages"][4]["reasoning_content"] == "earlier private reasoning"
    assert sent["tools"] == [{"type": "function", "function": {"name": "fixture_lookup", "description": "Isolated fixture", "parameters": TOOL["input_schema"]}}]


@pytest.mark.parametrize("change", [
    {"prompt_tokens": True}, {"prompt_tokens": "13"}, {"prompt_tokens": 13.0},
    {"prompt_tokens": -1}, {"prompt_tokens": 2**63}, {"prompt_cache_hit_tokens": 6},
    {"prompt_cache_miss_tokens": None}, {"total_tokens": 21}, {"completion_tokens": False},
    {"prompt_tokens_details": {"cached_tokens": 4}}, {"prompt_tokens_details": {"cached_tokens": 5.0}},
    {"prompt_tokens_details": None}, {"completion_tokens_details": {"reasoning_tokens": 8}},
    {"completion_tokens_details": {"reasoning_tokens": -1}}, {"completion_tokens_details": {"reasoning_tokens": True}},
])
def test_faulty_terminal_usage_is_unreliable_never_zero(change):
    raw = {**USAGE, **change}
    result, _, _, _ = run(complete(raw))
    assert result.attempt.usage_reliability == "unreliable" and result.attempt.usage is None
    assert result.attempt.raw_usage == raw and result.step.usage == {}


@pytest.mark.parametrize("key", ["prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "total_tokens"])
def test_missing_usage_category_cannot_be_inferred(key):
    raw = {k: v for k, v in USAGE.items() if k != key}
    assert normalize_usage(raw) is None


def test_explicit_zero_is_valid_but_missing_usage_pending():
    zero = {k: 0 for k in ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "completion_tokens", "total_tokens")}
    assert normalize_usage(zero).total_tokens == 0
    result, _, _, _ = run(complete(None))
    assert result.attempt.usage is None and result.attempt.usage_reliability == "pending"


def test_nonterminal_usage_cannot_become_authoritative():
    result, _, _, _ = run(event({"content": "fixture"}, usage=USAGE) + event(finish="stop", usage=USAGE) + b"data: [DONE]\n\n")
    assert result.attempt.usage is None and result.attempt.usage_reason == "nonterminal_usage"


@pytest.mark.parametrize("body", [event({"content": "partial"}), event(finish="stop", usage=USAGE), b"data: [DONE]\n\n",
                                 event(finish="stop", usage=USAGE) + b"data: [DONE]", b": keep-alive\n\n"])
def test_incomplete_stream_never_settles(body):
    provider, args, seen, _, wire = invoke(body)
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert len(seen) == 1 and wire.closed and not exc.value.retryable
    assert exc.value.attempt.dispatch == "observed"
    assert exc.value.attempt.usage is None and exc.value.attempt.usage_reliability == "pending"


@pytest.mark.parametrize("status,code", [(301, "provider_redirect"), (307, "provider_redirect"), (401, "provider_auth"),
                                        (402, "provider_balance"), (429, "provider_rate_limited"), (503, "provider_overloaded")])
def test_errors_never_retry_follow_redirects_or_leak_bodies(status, code, caplog):
    provider, args, seen, _, wire = invoke(KEY.encode(), status=status,
                                         headers={"Location": "https://fixture-evil.invalid/key", "Retry-After": "12"})
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert exc.value.code == code and exc.value.retry_after == 12 and not exc.value.retryable
    assert len(seen) == 1 and wire.closed and exc.value.attempt.dispatch == "observed"
    assert KEY not in caplog.text + str(exc.value) + repr(exc.value.attempt)
    assert exc.value.__suppress_context__ or exc.value.__context__ is None


@pytest.mark.parametrize("error", [httpx.ReadTimeout(KEY), httpx.ConnectError(KEY), httpx.WriteError(KEY), httpx.RemoteProtocolError(KEY)])
def test_transport_handoff_without_response_is_unknown(error):
    async def fail(request):
        raise error
    provider, args, seen, _, _ = invoke(handler=fail)
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert len(seen) == 1 and exc.value.attempt.dispatch == "unknown" and not exc.value.retryable
    assert KEY not in str(exc.value)


def test_body_dispatch_trace_remains_observed_on_lost_response():
    async def fail(request):
        await request.extensions["trace"]("http11.send_request_body.complete", {})
        raise httpx.ReadError("fixture loss")
    provider, args, seen, _, _ = invoke(handler=fail)
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert len(seen) == 1 and exc.value.attempt.dispatch == "observed"


@pytest.mark.parametrize("kwargs", [
    {"credential": Credential("platform", KEY, "")}, {"credential": Credential("platform", KEY, "https://fixture.invalid")},
    {"credential": Credential("platform", "fixture\nkey", "deepseek-flash")}, {"effort": "unknown"},
    {"user_id": "user@example.test"}, {"messages": [{"role": "tool", "tool_call_id": "orphan", "content": "fixture"}]},
    {"messages": [{"role": "assistant", "content": [{"type": "thinking", "thinking": "foreign"}]}]},
    {"messages": [{"role": "user", "content": [{"type": "image", "source": "fixture"}]}]},
    {"tools": [TOOL, TOOL]}, {"limits": Limits(request_bytes=10)},
])
def test_invalid_request_is_definitely_not_requested(kwargs):
    provider, args, seen, _, _ = invoke(**kwargs)
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert not seen and exc.value.attempt.dispatch == "definitely_not_requested"


def test_cancel_and_failed_fence_prevent_dispatch():
    def fail():
        raise RuntimeError(KEY)
    for options, exception in (({"should_stop": lambda: True}, DeepSeekCancelled), ({"before_dispatch": fail}, DeepSeekError)):
        provider, args, seen, _, _ = invoke(**options)
        with pytest.raises(exception) as exc:
            provider.step(**args)
        assert not seen and exc.value.attempt.dispatch == "definitely_not_requested"
        assert KEY not in str(exc.value)


def test_fence_happens_before_send_and_cancel_race_is_rechecked():
    cancelled = [False]
    def fence():
        cancelled[0] = True
    provider, args, seen, _, _ = invoke(before_dispatch=fence, should_stop=lambda: cancelled[0])
    with pytest.raises(DeepSeekCancelled) as exc:
        provider.step(**args)
    assert not seen and exc.value.attempt.dispatch == "definitely_not_requested"


class BlockingWire(Wire):
    def __init__(self, started):
        super().__init__(b"")
        self.started = started

    async def __aiter__(self):
        self.started.set()
        await asyncio.Event().wait()
        yield b""  # Make an async generator; cancellation exits the wait.


@pytest.mark.parametrize("mode", ["poll", "task", "deadline"])
def test_cancellation_and_deadline_close_even_blocked_read(mode):
    async def scenario():
        started = asyncio.Event()
        wire = BlockingWire(started)
        provider, args, seen, _, _ = invoke(wire=wire, limits=Limits(timeout_s=0.05, cancel_poll_s=0.001),
                                           should_stop=lambda: mode == "poll" and started.is_set())
        task = asyncio.create_task(provider.astep(**args))
        await started.wait()
        if mode == "task":
            task.cancel()
        with pytest.raises(DeepSeekError if mode == "deadline" else DeepSeekCancelled) as exc:
            await task
        assert wire.closed and len(seen) == 1 and exc.value.attempt.dispatch == "observed"
        assert exc.value.attempt.usage is None
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    asyncio.run(scenario())


@pytest.mark.parametrize("field,limit,body", [
    ("text_bytes", 5, complete()), ("reasoning_bytes", 2, event({"reasoning_content": "secret fixture"})),
    ("event_bytes", 10, b"data: " + b"x" * 11), ("event_bytes", 10, b":" + b"x" * 11),
    ("stream_bytes", 5, b": a\n\n: b\n\n"), ("events", 1, b": a\n\n: b\n\n"),
])
def test_wire_bounds_close_stream(field, limit, body):
    provider, args, seen, _, wire = invoke(body, limits=replace(Limits(), **{field: limit}))
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert len(seen) == 1 and wire.closed


@pytest.mark.parametrize("arguments", ['{"a":', '{"a":1,"a":2}', '{"a":NaN}', '[]', '{"a":1e999}', '{"a":' + '[' * 70 + '0' + ']' * 70 + '}'])
def test_invalid_tool_json_never_becomes_executable(arguments):
    tool = {"index": 0, "id": "fixture_call", "type": "function", "function": {"name": "fixture_lookup", "arguments": arguments}}
    provider, args, seen, _, wire = invoke(event({"tool_calls": [tool]}) + event(finish="tool_calls", usage=USAGE) + b"data: [DONE]\n\n", tools=[TOOL])
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert len(seen) == 1 and wire.closed and exc.value.attempt.usage_reliability == "trusted_terminal"


@pytest.mark.parametrize("field", ["tool_argument_bytes", "all_tool_argument_bytes"])
def test_tool_argument_bounds(field):
    tool = {"index": 0, "id": "fixture_call", "type": "function", "function": {"name": "fixture_lookup", "arguments": '{"place":"fixture"}'}}
    provider, args, _, _, wire = invoke(event({"tool_calls": [tool]}), tools=[TOOL], limits=replace(Limits(), **{field: 5}))
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert wire.closed


@pytest.mark.parametrize("body", [
    b"data: {broken}\n\n", b"data: {\xff}\n\n", b"event: error\ndata: {}\n\n",
    event(choices=[]), event(choices=[{"index": True, "delta": {}, "finish_reason": None}]),
    event({"content": "a"}) + event(id="different-id"), event({"content": "a"}) + event(model="different-model"),
    event(finish="stop") + event(finish="stop"), event(finish="unknown"),
    b'data: {"id":"a","id":"b"}\n\n',
])
def test_malformed_protocol_is_not_success(body):
    provider, args, seen, _, wire = invoke(body)
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert len(seen) == 1 and wire.closed


@pytest.mark.parametrize("finish,expected", [("length", "max_tokens"), ("content_filter", "refusal")])
def test_terminal_non_success_reasons_preserved(finish, expected):
    result, _, _, _ = run(complete(finish=finish))
    assert result.step.stop_reason == expected and result.attempt.finish_reason == finish


@pytest.mark.parametrize("finish", ["aborted", "insufficient_system_resource"])
def test_interrupted_terminal_does_not_succeed_but_retains_usage(finish):
    provider, args, _, _, wire = invoke(complete(finish=finish))
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert exc.value.code == "provider_interrupted" and exc.value.attempt.usage.total_tokens == 20 and wire.closed


@pytest.mark.parametrize("headers", [{"Content-Type": "application/json"}, {"Content-Encoding": "gzip"}])
def test_unsupported_response_encoding_or_type(headers):
    provider, args, seen, _, wire = invoke(headers=headers)
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert wire.closed and len(seen) == 1


def test_native_tool_history_and_engine_tool_blocks_are_supported():
    history = [{"role": "assistant", "content": [{"type": "tool_use", "id": "fixture_call", "name": "fixture_lookup", "input": {"place": "fixture"}}]},
               {"role": "tool", "tool_call_id": "fixture_call", "content": "fixture result"}]
    _, seen, _, _ = run(messages=history, tools=[TOOL])
    sent = json.loads(seen[0].content)["messages"]
    assert sent[1]["tool_calls"][0]["function"]["arguments"] == '{"place":"fixture"}'
    assert sent[2]["role"] == "tool"


def test_multiline_sse_lone_cr_and_single_byte_utf8():
    body = complete().replace(b', "model"', b',\rdata: "model"').replace(b"\r\n", b"\r")
    result, _, visible, wire = run(wire=Wire(body, split=1))
    assert result.attempt.usage.total_tokens == 20 and visible == ["你好"] and wire.closed


@pytest.mark.parametrize("kwargs", [{"timeout_s": float("nan")}, {"read_timeout_s": 0}, {"max_tokens": True}, {"tool_calls": 21}, {"stream_bytes": 0}])
def test_invalid_limits_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


def test_two_interleaved_tools_are_assembled_by_index():
    def first(index, suffix, fragment):
        return {"index": index, "id": "fixture_" + suffix, "type": "function",
                "function": {"name": "fixture_lookup", "arguments": fragment}}
    body = (event({"tool_calls": [first(1, "second", '{"place":'), first(0, "first", '{"place":"A"}')]})
            + event({"tool_calls": [{"index": 1, "function": {"arguments": '"B"}'}}]})
            + event(finish="tool_calls", usage=USAGE) + b"data: [DONE]\n\n")
    result, _, _, _ = run(body, tools=[TOOL])
    assert [b["id"] for b in result.step.content] == ["fixture_first", "fixture_second"]
    assert [b["input"] for b in result.step.content] == [{"place": "A"}, {"place": "B"}]


@pytest.mark.parametrize("change", [{"index": -1}, {"index": True}, {"index": 20}, {"type": "unknown"},
                                    {"function": {"name": "unknown", "arguments": "{}"}}, {"id": "https://fixture.invalid"}])
def test_invalid_tool_identity_or_index_rejected(change):
    tool = {"index": 0, "id": "fixture_call", "type": "function", "function": {"name": "fixture_lookup", "arguments": "{}"}}
    tool.update(change)
    provider, args, seen, _, wire = invoke(event({"tool_calls": [tool]}), tools=[TOOL])
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert len(seen) == 1 and wire.closed


@pytest.mark.parametrize("second", [
    {"index": 1, "id": "fixture_call", "type": "function", "function": {"name": "fixture_lookup", "arguments": "{}"}},
    {"index": 0, "id": "changed", "function": {"arguments": ""}},
    {"index": 0, "function": {"name": "changed", "arguments": ""}},
])
def test_duplicate_or_mutating_tool_identity_rejected(second):
    first = {"index": 0, "id": "fixture_call", "type": "function", "function": {"name": "fixture_lookup", "arguments": "{}"}}
    provider, args, _, _, wire = invoke(event({"tool_calls": [first]}) + event({"tool_calls": [second]}), tools=[TOOL])
    with pytest.raises(DeepSeekError):
        provider.step(**args)
    assert wire.closed


def test_usage_evidence_retained_after_terminal_chunk_without_done():
    provider, args, _, _, wire = invoke(event(finish="stop", usage=USAGE))
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert exc.value.attempt.raw_usage == USAGE and exc.value.attempt.usage is None
    assert exc.value.attempt.usage_reliability == "pending" and wire.closed


def test_cancel_while_waiting_for_headers_is_unknown_and_no_detached_request():
    async def scenario():
        started, closed = asyncio.Event(), asyncio.Event()
        async def block(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        provider, args, seen, _, _ = invoke(handler=block, limits=Limits(cancel_poll_s=0.001), should_stop=started.is_set)
        with pytest.raises(DeepSeekCancelled) as exc:
            await provider.astep(**args)
        assert closed.is_set() and len(seen) == 1 and exc.value.attempt.dispatch == "unknown"
    asyncio.run(scenario())


def test_text_callback_failure_is_sanitized_and_not_retried(caplog):
    def fail(_):
        raise RuntimeError(KEY)
    provider, args, seen, _, wire = invoke(on_text=fail)
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert len(seen) == 1 and wire.closed and not exc.value.retryable
    assert KEY not in str(exc.value) + caplog.text and exc.value.__suppress_context__


def test_thinking_disabled_and_no_tools_still_preserve_private_native_history():
    history = [{"role": "assistant", "content": "fixture answer", "reasoning_content": "fixture private"},
               {"role": "user", "content": "next"}]
    provider, args, seen, _, _ = invoke(messages=history)
    provider.thinking = False
    provider.step(**args)
    body = json.loads(seen[0].content)
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body
    assert body["messages"][1]["reasoning_content"] == "fixture private"


def test_sync_entry_refuses_running_loop_before_request():
    async def scenario():
        provider, args, seen, _, _ = invoke()
        with pytest.raises(DeepSeekError) as exc:
            provider.step(**args)
        assert exc.value.code == "provider_async_context" and not seen
    asyncio.run(scenario())


def test_deadline_applies_to_continuous_keepalives(monkeypatch):
    from synwevo_suite.agent import deepseek
    now = [0.0]
    class Heartbeats(Wire):
        async def __aiter__(self):
            while True:
                now[0] += 1.0
                yield b": keep-alive\n\n"
    monkeypatch.setattr(deepseek.time, "monotonic", lambda: now[0])
    wire = Heartbeats(b"")
    provider, args, seen, _, _ = invoke(wire=wire, limits=Limits(timeout_s=3))
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert exc.value.code == "provider_timeout" and len(seen) == 1 and wire.closed


def test_transport_options_are_explicit_and_no_environment_credentials(monkeypatch):
    from synwevo_suite.agent import deepseek
    seen_options = []
    original = httpx.AsyncClient
    wire = Wire(complete())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=wire))
    def client(**kwargs):
        seen_options.append(kwargs.copy())
        kwargs["transport"] = transport
        return original(**kwargs)
    monkeypatch.setattr(deepseek.httpx, "AsyncClient", client)
    provider, args, _, _, _ = invoke()
    provider.step(**args)
    assert seen_options[0]["trust_env"] is False and seen_options[0]["follow_redirects"] is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", KEY)
    args["credential"] = Credential("platform", "", "deepseek-flash")
    with pytest.raises(DeepSeekError) as exc:
        provider.step(**args)
    assert exc.value.attempt.dispatch == "definitely_not_requested" and len(seen_options) == 1
