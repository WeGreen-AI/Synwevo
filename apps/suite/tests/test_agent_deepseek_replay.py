"""DeepSeek thinking-mode history contract on the real Suite factory (fixture transport only).

The official thinking-mode guide (re-read 2026-10-05) states that when a request carries
tools, every earlier assistant turn must send its `reasoning_content` back, including turns
without a tool call, or the API answers HTTP 400. The FIXTURE double below enforces exactly
that rule and answers with a DeepSeek-shaped error body, so a history that drops reasoning of
a failed or cancelled turn reproduces the upstream refusal without any real model call.
"""
import json

import httpx
import pytest

from .test_agent_governed_factory import KEY, configure, fund, governed, start  # noqa: F401 - fixture import
from .test_agent_metering import USAGE, Wire, event

FIXTURE_400 = {"error": {"message": "FIXTURE: The reasoning_content in the thinking mode must be passed back to the API.",
                         "type": "invalid_request_error", "param": None, "code": "invalid_request_error"}}


def contract_refusal(body: dict) -> bool:
    """FIXTURE copy of the documented thinking-mode replay rule (tools present, thinking on)."""
    thinking = (body.get("thinking") or {}).get("type") != "disabled"
    return bool(body.get("tools")) and thinking and any(
        m.get("role") == "assistant" and not isinstance(m.get("reasoning_content"), str) for m in body["messages"])


def reply(request_id, *chunks):
    return b"".join(chunks).replace(b"fixture-upstream-1", request_id.encode())


def done(request_id, text="fixture answer"):
    return reply(request_id, event({"reasoning_content": "fixture-reasoning-" + request_id, "content": text}),
                 event(finish="stop", usage=USAGE), b"data: [DONE]\n\n")


@pytest.fixture
def wire(governed):
    g = governed
    g.bodies = []

    async def respond(request):
        body = json.loads(request.content)
        g.bodies.append(body)
        if contract_refusal(body):
            return httpx.Response(400, json=FIXTURE_400)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(g.replies.pop(0)))
    g.provider.gateway.transport = httpx.MockTransport(respond)
    configure(g)
    fund(g)
    return g


def turn(g, tid):
    return g.store.one("SELECT * FROM agent_turns WHERE id=?", (tid,))


def assistant_messages(body):
    return [m for m in body["messages"] if m["role"] == "assistant"]


def test_cancelled_confirmation_turn_replays_reasoning_and_closes_the_unrun_call(wire):
    g = wire
    arguments = {"operation": "forms.form.create", "workspace_id": g.wid, "input": {"title": "Fixture form"}}
    g.replies = [reply("fixture-call-1",
        event({"reasoning_content": "fixture-private-plan", "content": "I will create the form."}),
        event({"tool_calls": [{"index": 0, "id": "fixture-tool-1", "type": "function",
                               "function": {"name": "run_operation", "arguments": json.dumps(arguments)}}]}),
        event(finish="tool_calls", usage=USAGE), b"data: [DONE]\n\n")]
    first = start(g)
    g.agent._run(first)
    assert turn(g, first)["status"] == "waiting_confirmation"
    g.agent.cancel(g.user["id"], first)
    assert turn(g, first)["status"] == "cancelled"

    g.replies = [done("fixture-call-2")]
    second = start(g)
    g.agent._run(second)
    assert turn(g, second)["status"] == "succeeded", turn(g, second)["error_code"]
    body = g.bodies[-1]
    replayed = assistant_messages(body)
    assert replayed and all(isinstance(m.get("reasoning_content"), str) for m in replayed)
    assert replayed[0]["reasoning_content"] == "fixture-private-plan"
    assert replayed[0]["tool_calls"][0]["id"] == "fixture-tool-1"
    closed = [m for m in body["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in closed] == ["fixture-tool-1"]
    assert "not run" in closed[0]["content"].lower() or "stopped" in closed[0]["content"].lower()
    # The person's two messages stay in order after the closed call.
    assert body["messages"][-1] == {"role": "user", "content": "Read my forms"}
    public = json.dumps(g.agent.conversation_json(g.user["id"], g.agent.conversation(g.user["id"], g.conversation["id"]), full=True))
    assert "fixture-private-plan" not in public and KEY not in public


def test_interrupted_stream_keeps_its_partial_reasoning_for_the_next_request(wire):
    g = wire
    # Reasoning and visible text arrive, then the stream ends without a terminal chunk.
    g.replies = [reply("fixture-call-1", event({"reasoning_content": "fixture-partial-thought", "content": "Partial answer"}))]
    first = start(g)
    g.agent._run(first)
    row = turn(g, first)
    assert row["status"] == "failed" and row["error_code"] == "provider_stream_incomplete"

    g.replies = [done("fixture-call-2")]
    second = start(g)
    g.agent._run(second)
    assert turn(g, second)["status"] == "succeeded", turn(g, second)["error_code"]
    replayed = assistant_messages(g.bodies[-1])
    assert replayed == [{"role": "assistant", "content": "Partial answer", "reasoning_content": "fixture-partial-thought"}]


def test_failed_second_step_keeps_the_completed_tool_step_reasoning(wire):
    g = wire
    created = g.suite.caps.invoke("forms.form.create", {"title": "Replay fixture form"}, user_id=g.user["id"],
                                 workspace_id=g.wid, idempotency_key="replay-fixture-form")
    assert created["status"] == "succeeded"
    arguments = {"operation": "forms.form.list", "workspace_id": g.wid, "input": {}}
    g.replies = [reply("fixture-call-1",
        event({"reasoning_content": "fixture-step-one-thought", "content": "Looking up forms."}),
        event({"tool_calls": [{"index": 0, "id": "fixture-tool-read", "type": "function",
                               "function": {"name": "run_operation", "arguments": json.dumps(arguments)}}]}),
        event(finish="tool_calls", usage=USAGE), b"data: [DONE]\n\n"),
        # Step two is cut off by the upstream after some reasoning and no visible text.
        reply("fixture-call-2", event({"reasoning_content": "fixture-step-two-thought"}))]
    first = start(g)
    g.agent._run(first)
    assert turn(g, first)["status"] == "failed"

    g.replies = [done("fixture-call-3")]
    second = start(g)
    g.agent._run(second)
    assert turn(g, second)["status"] == "succeeded", turn(g, second)["error_code"]
    body = g.bodies[-1]
    replayed = assistant_messages(body)
    assert [m["reasoning_content"] for m in replayed] == ["fixture-step-one-thought", "fixture-step-two-thought"]
    tool = next(m for m in body["messages"] if m["role"] == "tool")
    assert tool["tool_call_id"] == "fixture-tool-read" and "Replay fixture form" in tool["content"]


def test_truncated_step_reasoning_is_replayed_and_its_unaccepted_text_kept(wire):
    g = wire
    # finish_reason=length is refused as provider_truncated after metering; its reasoning stays.
    g.replies = [reply("fixture-call-1", event({"reasoning_content": "fixture-long-thought", "content": "Cut"}),
                       event(finish="length", usage=USAGE), b"data: [DONE]\n\n")]
    first = start(g)
    g.agent._run(first)
    assert turn(g, first)["error_code"] == "provider_truncated"

    g.replies = [done("fixture-call-2")]
    second = start(g)
    g.agent._run(second)
    assert turn(g, second)["status"] == "succeeded", turn(g, second)["error_code"]
    assert assistant_messages(g.bodies[-1]) == [
        {"role": "assistant", "content": "Cut", "reasoning_content": "fixture-long-thought"}]
