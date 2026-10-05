"""Fault reproductions from independent gateway review. All external data are fixtures."""
from dataclasses import replace
import json
import sqlite3
import threading

import httpx
import pytest

from synwevo_core.billing import BillingError
from synwevo_suite.agent.deepseek import DeepSeekError
from synwevo_suite.agent.gateway import MeteredTurnProvider

from .test_agent_metering import STAMP, USAGE, Wire, event, gateway


@pytest.fixture
def worker(gateway, monkeypatch):
    from synwevo_suite.app import create_app
    g = gateway
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "forms,agent")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", g.route.requested_model)
    api = create_app(str(g.store.root), start_jobs=False)
    agent = api.state.agent
    monkeypatch.setattr(agent, "_submit", lambda turn_id: None)
    agent.provider = MeteredTurnProvider(g.model, credential_resolver=lambda *_: g.args["credential"])
    user = api.state.identity.get_user(g.context.actor_user_id)
    g.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    conv = agent.create_conversation(user["id"], "Fault fixture", [])
    turn = agent.start_turn(user, conv["id"], "Fixture question", parent_id=None, regenerate_of=None)
    context = replace(g.context, run_id=turn["turn_id"])
    g.model.open_run(context, route=g.route, budget=1000, max_calls=3, deadline=STAMP+500)
    yield agent, turn, conv
    agent.close()


# DeepSeek's own content filter has its own code (agent/safety.py); the platform still absorbs it.
@pytest.mark.parametrize("finish,code", [("content_filter", "content_filtered_output"), ("length", "provider_truncated")])
def test_refused_or_truncated_final_usage_is_cost_only(gateway, worker, finish, code):
    g, (agent, turn, _) = gateway, worker
    g.holder["body"] = event(finish=finish, usage=USAGE) + b"data: [DONE]\n\n"
    agent._run(turn["turn_id"])
    assert g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (turn["turn_id"],)) == {
        "status": "failed", "error_code": code}
    assert g.store.one("SELECT outcome,charge FROM billing_usage") == {"outcome": "platform_fault", "charge": 0}
    assert g.balance().booked == 1000 and g.balance().held == 0
    assert len(g.seen) == 1


def test_repeated_tool_id_across_steps_is_validated_before_sale(gateway, worker):
    g, (agent, turn, _) = gateway, worker
    requests = []
    async def respond(request):
        requests.append(request)
        body = event({"tool_calls": [{"index": 0, "id": "fixture-repeated-tool", "type": "function",
            "function": {"name": "list_workspaces", "arguments": "{}"}}]})
        body += event(finish="tool_calls", usage=USAGE) + b"data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Wire(
            body.replace(b"fixture-upstream-1", f"fixture-upstream-{len(requests)}".encode())))
    g.model.transport = httpx.MockTransport(respond)
    agent._run(turn["turn_id"])
    assert g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (turn["turn_id"],)) == {
        "status": "failed", "error_code": "provider_bad_output"}
    assert g.store.all("SELECT outcome,charge FROM billing_usage ORDER BY rowid") == [
        {"outcome": "completed", "charge": 42}, {"outcome": "platform_fault", "charge": 0}]
    assert len(requests) == 2 and g.balance().booked == 958 and g.balance().held == 0
    assert len(g.store.all("SELECT * FROM agent_tool_calls")) == 1


@pytest.mark.parametrize("failure", ["observation", "settlement"])
def test_database_failure_after_dispatch_never_presents_known_zero(gateway, worker, monkeypatch, failure):
    g, (agent, turn, conv) = gateway, worker
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("isolated metering fault")
    if failure == "observation":
        g.holder["body"] = event({"content": "unfinished fixture response"})
        monkeypatch.setattr(g.model, "_observe", fail)
    else:
        monkeypatch.setattr(g.ledger, "settle_trusted_usage", fail)
    agent._run_safely(turn["turn_id"])
    state = agent.conversation_json(g.context.actor_user_id, agent.conversation(g.context.actor_user_id, conv["id"]), full=True)
    usage = next(m["turn"]["usage"] for m in state["messages"] if m.get("turn"))
    assert usage == {"input": None, "output": None, "state": "pending"}
    assert g.balance().booked == 1000 and g.balance().held == 297 and len(g.seen) == 1
    assert g.store.one("SELECT status FROM billing_attempts")["status"] == "dispatch_unknown"


def test_concurrent_steps_recheck_unresolved_attempt_inside_dispatch_transaction(gateway, monkeypatch):
    g = gateway
    barrier, first_done = threading.Barrier(2, timeout=10), threading.Event()
    original = g.model._prices
    def both_admitted(route):
        result = original(route)
        barrier.wait()
        if threading.current_thread().name == "second":
            assert first_done.wait(10)
        return result
    monkeypatch.setattr(g.model, "_prices", both_admitted)
    g.holder["body"] = event({"content": "unfinished fixture"})
    errors = []
    def run(logical_id):
        try:
            g.model.step(**(g.args | {"logical_call_id": logical_id}))
        except (DeepSeekError, BillingError) as exc:
            errors.append(exc.code)
        finally:
            if logical_id == "first":
                first_done.set()
    first = threading.Thread(target=run, args=("first",), name="first")
    second = threading.Thread(target=run, args=("second",), name="second")
    first.start(); second.start()
    first.join(15); second.join(15)
    assert not first.is_alive() and not second.is_alive()
    assert len(errors) == 2 and "provider_dispatch_blocked" in errors
    assert len(g.seen) == 1 and g.balance().held == 297
    assert len(g.store.all("SELECT * FROM billing_attempts WHERE dispatched_at IS NOT NULL")) == 1
