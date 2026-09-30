"""Targeted Agent contract regressions. All identities/data/providers are isolated test fixtures.

No production credentials, provider calls, application data or background deployment are used.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from synwevo_core.errors import ServiceError
from synwevo_suite.agent.provider import AnthropicProvider, Credential, ProviderError
from .test_agent import agent, call, op, say, start, wait_turn, ScriptedModel  # noqa: F401 (fixture)


def pending(c, *, title="Fixture", operation="forms.form.create", payload=None):
    conv, turn = start(c, [call("run_operation", {"operation": operation, "workspace_id": c.people.a,
                                               "input": payload or {"title": title}}), say("done")])
    assert wait_turn(c.people.store, turn["turn_id"]) == "waiting_confirmation"
    card = c.people.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=?", (turn["turn_id"],))
    return conv, turn, card


def test_model_must_be_explicit_even_with_personal_or_platform_key(agent, monkeypatch):
    agent.sign_in("alice")
    monkeypatch.delenv("SYNWEVO_AGENT_MODEL")
    settings = op(agent, "agent.settings.update", {"personal_key": "fixture-personal-secret-1234567890",
                                                 "prefer_personal_key": True})["output"]
    assert settings["active"] is None and not settings["platform_configured"] and not settings["model_configured"]
    conv = op(agent, "agent.conversation.create")["output"]["id"]
    assert op(agent, "agent.turn.start", {"conversation_id": conv, "text": "hello"}, expect=503)["error"] == "agent_not_configured"


def test_secret_absent_from_success_failure_replay_and_logs(agent, monkeypatch, caplog):
    from synwevo_suite.agent import ops

    agent.sign_in("alice")
    p = agent.people
    secret = "fixture-personal-secret-1234567890"
    out = agent.caps.invoke("agent.settings.update", {"personal_key": secret}, user_id=p.uid("alice"),
                            idempotency_key="fixture-key")
    replay = agent.caps.invoke("agent.settings.update", {"personal_key": secret}, user_id=p.uid("alice"),
                               idempotency_key="fixture-key")
    assert replay["replayed"] and replay["run_id"] == out["run_id"]
    with pytest.raises(ServiceError, match="幂等"):
        agent.caps.invoke("agent.settings.update", {"personal_key": secret + "different"},
                          user_id=p.uid("alice"), idempotency_key="fixture-key")

    def fail_seal(*args):
        raise ServiceError(503, "fixture_sealing_unavailable", "隔离故障注入")

    monkeypatch.setattr(ops.sealed, "seal", fail_seal)
    with pytest.raises(ServiceError):
        agent.caps.invoke("agent.settings.update", {"personal_key": secret}, user_id=p.uid("alice"))
    for table in ("op_runs", "audit_events", "agent_events", "agent_settings"):
        assert secret not in json.dumps(p.store.all(f"SELECT * FROM {table}"), default=str)
    assert secret not in repr(Credential("personal", secret, "fixture-model"))
    assert secret not in caplog.text
    assert secret not in json.dumps(agent.caps.run(out["run_id"], user_id=p.uid("alice")))


def test_real_sdk_adapter_uses_configured_model_no_fallback_or_error_body_log(monkeypatch, caplog):
    """Official SDK against an isolated HTTP failure fixture, never the live provider."""
    import anthropic
    import httpx2 as httpx

    secret = "fixture-provider-secret-never-log"
    seen, clients = [], []
    original = anthropic.Anthropic

    def response(request):
        seen.append((json.loads(request.content), request.headers, str(request.url)))
        return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": secret}})

    def factory(**kwargs):
        c = original(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(response)))
        clients.append(c)
        return c

    monkeypatch.setattr(anthropic, "Anthropic", factory)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://host-fixture.invalid")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-host-token")
    monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "X-Host-Secret: fixture-host-secret\nAuthorization: Bearer fixture-host-token")
    with pytest.raises(ProviderError) as err:
        AnthropicProvider().step(credential=Credential("platform", secret, "configured-fixture-model"),
                                 system=[], tools=[], messages=[{"role": "user", "content": "fixture"}],
                                 effort="medium", on_text=lambda _: None, should_stop=lambda: False)
    assert err.value.code == "provider_bad_request"
    assert seen[0][0]["model"] == "configured-fixture-model"
    assert "fallbacks" not in seen[0][0] and "anthropic-beta" not in seen[0][1]
    assert seen[0][2].startswith("https://api.anthropic.com/")
    assert "x-host-secret" not in seen[0][1] and "authorization" not in seen[0][1]
    assert clients[0].is_closed() and secret not in caplog.text


def test_resource_deleted_hides_history_descendants_sse_and_search(agent):
    p = agent.people
    agent.sign_in("alice")
    fid = op(agent, "forms.form.create", {"title": "fixture restricted source"}, ws=p.a)["output"]["id"]
    op(agent, "agent.workspace_policy.set", {"processing": "platform"}, ws=p.a)
    conv, turn = start(agent, [call("run_operation", {"operation": "forms.form.get", "workspace_id": p.a,
                                                   "input": {"form_id": fid}}), say("restricted-needle")])
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"
    agent.agent.provider = ScriptedModel([say("derived restricted-needle")])
    next_turn = op(agent, "agent.turn.start", {"conversation_id": conv, "text": "summarize it"})["output"]
    assert wait_turn(p.store, next_turn["turn_id"]) == "succeeded"
    # Still a member with the same role. The resource itself is no longer readable.
    p.store.execute("DELETE FROM forms WHERE id=?", (fid,))
    got = op(agent, "agent.conversation.get", {"conversation_id": conv})["output"]
    assert got["sealed"] == "access_changed"
    assistants = [m for m in got["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 2 and all(m["hidden"] and not m["body"] for m in assistants)
    assert assistants[0]["tool_calls"][0]["result"] is None
    events = agent.agent.events_after(p.uid("alice"), conv, 0)
    assert all(e["data"]["hidden"] and not e["data"]["delta"] for e in events if e["type"] == "text")
    assert "restricted-needle" not in json.dumps(events)
    assert op(agent, "agent.conversation.list", {"q": "restricted-needle"})["output"]["conversations"] == []


def test_role_downgrade_rechecks_field_visibility_without_membership_loss(agent):
    p = agent.people
    agent.sign_in("bob")
    fid = op(agent, "forms.form.create", {"title": "fixture", "definition": {"fields": [
        {"id": "f_name", "type": "text", "label": "Fixture name"}]}}, ws=p.a)["output"]["id"]
    op(agent, "forms.form.publish", {"form_id": fid, "draft_rev": 1}, ws=p.a)
    op(agent, "forms.public_link.set", {"form_id": fid, "enabled": True}, ws=p.a)
    conv, turn = start(agent, [call("run_operation", {"operation": "forms.form.get", "workspace_id": p.a,
                                                   "input": {"form_id": fid}}), say("cached privileged fields")])
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"
    p.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?", (p.a, p.uid("bob")))
    got = op(agent, "agent.conversation.get", {"conversation_id": conv})["output"]
    assert got["sealed"] == "access_changed" and got["messages"][-1]["hidden"]


def test_events_reauthorize_owner_and_account_and_paginate(agent):
    p = agent.people
    agent.sign_in("alice")
    conv = op(agent, "agent.conversation.create")["output"]["id"]
    for i in range(503):
        agent.agent._event(conv, p.uid("alice"), None, "fixture", {"n": i})
    first = agent.agent.events_after(p.uid("alice"), conv, 0)
    rest = agent.agent.events_after(p.uid("alice"), conv, first[-1]["seq"])
    assert len(first) == 500 and [r["data"]["n"] for r in rest] == [500, 501, 502]
    with pytest.raises(ServiceError) as wrong_owner:
        agent.agent.events_after(p.uid("bob"), conv, 0)
    assert wrong_owner.value.code == "conversation_not_found"
    p.store.execute("UPDATE users SET status='suspended' WHERE id=?", (p.uid("alice"),))
    with pytest.raises(ServiceError) as disabled:
        agent.agent.events_after(p.uid("alice"), conv, 0)
    assert disabled.value.code == "account_unavailable"


def test_policy_revoked_stops_replay_before_next_provider_request(agent):
    p = agent.people
    agent.sign_in("alice")
    op(agent, "agent.workspace_policy.set", {"processing": "platform"}, ws=p.a)
    conv, turn = start(agent, [call("run_operation", {"operation": "forms.form.list", "workspace_id": p.a, "input": {}}), say("done")])
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"
    op(agent, "agent.workspace_policy.set", {"processing": "none"}, ws=p.a)
    agent.agent.provider = ScriptedModel([say("must never run")])
    nxt = op(agent, "agent.turn.start", {"conversation_id": conv, "text": "more"})["output"]
    assert wait_turn(p.store, nxt["turn_id"]) == "failed"
    assert not agent.agent.provider.requests
    assert p.store.one("SELECT error_code FROM agent_turns WHERE id=?", (nxt["turn_id"],))["error_code"] == "source_policy_changed"


def test_confirmation_bound_to_call_and_current_resource_revision(agent):
    p = agent.people
    agent.sign_in("alice")
    fid = op(agent, "forms.form.create", {"title": "fixture"}, ws=p.a)["output"]["id"]
    conv, turn, card = pending(agent, operation="forms.form.archive", payload={"form_id": fid, "archived": True})
    op(agent, "forms.form.update_draft", {"form_id": fid, "draft_rev": 1, "title": "changed after card"}, ws=p.a)
    assert op(agent, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]}, expect=409)["error"] == "confirmation_mismatch"
    assert p.store.one("SELECT status FROM forms WHERE id=?", (fid,))["status"] == "active"
    op(agent, "agent.turn.cancel", {"turn_id": turn["turn_id"]})
    _, _, one = pending(agent)
    _, _, two = pending(agent)
    assert one["input_hash"] != two["input_hash"]
    assert op(agent, "agent.tool_call.confirm", {"tool_call_id": two["id"], "confirmation": one["input_hash"]}, expect=409)["error"] == "confirmation_mismatch"


def test_concurrent_confirm_runs_write_once(agent):
    p = agent.people
    agent.sign_in("bob")
    _, turn, card = pending(agent)
    barrier = threading.Barrier(2)

    def decide():
        barrier.wait(timeout=3)
        try:
            return agent.agent.decide(p.users["bob"], card["id"], confirm=True, confirmation=card["input_hash"])["status"]
        except ServiceError as err:
            return err.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: decide(), range(2)))
    assert sorted(outcomes) == ["not_awaiting_confirmation", "succeeded"]
    assert wait_turn(p.store, turn["turn_id"], ("succeeded", "failed")) == "succeeded"
    assert p.store.one("SELECT COUNT(*) AS n FROM forms")["n"] == 1
    assert p.store.one("SELECT COUNT(*) AS n FROM op_runs WHERE idempotency_key=?", (f"agent:{card['id']}",))["n"] == 1


def test_cancel_during_model_response_prevents_any_tool_start(agent):
    p = agent.people
    agent.sign_in("alice")

    def cancel_then_tool(messages):
        turn = p.store.one("SELECT id FROM agent_turns WHERE status='running'")
        agent.agent.cancel(p.uid("alice"), turn["id"])
        return call("run_operation", {"operation": "forms.form.list", "workspace_id": p.a, "input": {}})

    _, turn = start(agent, [cancel_then_tool])
    assert wait_turn(p.store, turn["turn_id"]) == "cancelled"
    assert not p.store.all("SELECT * FROM agent_tool_calls")


def test_concurrent_admission_and_quota_leave_no_orphan_messages(agent, monkeypatch):
    p = agent.people
    agent.sign_in("alice")
    monkeypatch.setattr(agent.agent, "_submit", lambda _: None)  # isolated stalled-worker fixture
    conv = op(agent, "agent.conversation.create")["output"]["id"]
    barrier = threading.Barrier(2)

    def send():
        barrier.wait(timeout=3)
        try:
            return agent.agent.start_turn(p.users["alice"], conv, "fixture", parent_id=None, regenerate_of=None)["turn_id"]
        except ServiceError as err:
            return err.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: send(), range(2)))
    assert outcomes.count("turn_running") == 1
    assert p.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"] == 2
    for _ in range(2):
        cid = op(agent, "agent.conversation.create")["output"]["id"]
        op(agent, "agent.turn.start", {"conversation_id": cid, "text": "fixture"})
    cid = op(agent, "agent.conversation.create")["output"]["id"]
    assert op(agent, "agent.turn.start", {"conversation_id": cid, "text": "over quota"}, expect=429)["error"] == "agent_busy"
    assert p.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"] == 6


def test_duplicate_worker_dispatch_claims_turn_once(agent, monkeypatch):
    p = agent.people
    agent.sign_in("alice")
    monkeypatch.setattr(agent.agent, "_submit", lambda _: None)
    entered, release = threading.Event(), threading.Event()

    def slow(messages):
        entered.set()
        assert release.wait(3)
        return say("once")

    _, turn = start(agent, [slow])
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(agent.agent._run_safely, turn["turn_id"])
        assert entered.wait(3)
        duplicate = pool.submit(agent.agent._run_safely, turn["turn_id"])
        duplicate.result(timeout=3)
        release.set()
        running.result(timeout=3)
    assert len(agent.agent.provider.requests) == 1
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"


def test_token_budget_is_failure_not_fabricated_success(agent, monkeypatch):
    from synwevo_suite.agent import engine

    agent.sign_in("alice")
    monkeypatch.setattr(engine, "MAX_TURN_TOKENS", 5)
    _, turn = start(agent, [call("find_operations", {}), say("must not run")])
    assert wait_turn(agent.people.store, turn["turn_id"]) == "failed"
    assert len(agent.agent.provider.requests) == 1
    assert agent.people.store.one("SELECT error_code FROM agent_turns")["error_code"] == "budget_exceeded"


def test_restart_after_claim_reconciles_waiting_turn_without_reexecution(agent):
    p = agent.people
    agent.sign_in("bob")
    _, turn, card = pending(agent)
    agent.caps.invoke("forms.form.create", {"title": "Fixture"}, user_id=p.uid("bob"), workspace_id=p.a,
                      via="agent", idempotency_key=f"agent:{card['id']}")
    p.store.execute("UPDATE agent_tool_calls SET status='running',decided_at=1 WHERE id=?", (card["id"],))
    assert agent.agent.recover() == 1
    assert p.store.one("SELECT status FROM agent_tool_calls")["status"] == "succeeded"
    assert p.store.one("SELECT status,error_code FROM agent_turns") == {"status": "failed", "error_code": "interrupted"}
    assert p.store.one("SELECT COUNT(*) AS n FROM forms")["n"] == 1
    assert len(agent.agent.provider.requests) == 1


def test_cross_workspace_target_scope_and_business_memory_cannot_be_bypassed(agent):
    p = agent.people
    agent.sign_in("alice")
    op(agent, "agent.workspace_policy.set", {"processing": "platform"}, ws=p.a)
    conv, turn = start(agent, [call("run_operation", {"operation": "forms.form.list", "workspace_id": p.a, "input": {}}),
                              call("run_operation", {"operation": "agent.memory.add", "input": {"body": "fixture business fact"}}, tid="remember"),
                              say("declined")])
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"
    assert p.store.one("SELECT error_code FROM agent_tool_calls WHERE tool_use_id='remember'")["error_code"] == "source_transfer_unavailable"
    assert not p.store.all("SELECT * FROM agent_memories")
    # Real duplication operation's target cannot bypass the conversation's selected scope.
    from synwevo_suite.agent.tools import ToolContext
    prepared = ToolContext(agent.caps, p.users["alice"], [p.a], "platform").prepare(
        {"operation": "forms.form.duplicate", "workspace_id": p.a,
         "input": {"form_id": "frm_123456789abc", "target_workspace_id": p.b}})
    assert prepared["code"] == "outside_conversation_scope"


@pytest.mark.parametrize("cancel", [False, True])
def test_parallel_confirmation_never_resumes_while_another_action_is_running(agent, cancel):
    """A real registry/Forms write behind an explicitly isolated blocking-handler fixture."""
    from dataclasses import replace
    from synwevo_suite.agent.provider import StepResult

    p = agent.people
    agent.sign_in("bob")
    entered, release = threading.Event(), threading.Event()
    create = agent.caps.registry.get("forms.form.create")

    def slow_create(c, i):
        if i.title == "first":
            entered.set()
            assert release.wait(5)
        return create.handler(c, i)

    operation = replace(create, id="forms.fixture.slow_create", transactional=False, handler=slow_create)
    agent.caps.registry.register(operation)
    blocks = [call("run_operation", {"operation": operation.id, "workspace_id": p.a,
                                      "input": {"title": title}}, tid=title).content[0] for title in ("first", "second")]
    _, turn = start(agent, [StepResult(blocks, "tool_use"), say("both finished")])
    assert wait_turn(p.store, turn["turn_id"]) == "waiting_confirmation"
    cards = {c["tool_use_id"]: c for c in p.store.all("SELECT * FROM agent_tool_calls")}

    def confirm(card):
        return agent.agent.decide(p.users["bob"], card["id"], confirm=True, confirmation=card["input_hash"])

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(confirm, cards["first"])
        try:
            assert entered.wait(3)
            if cancel:
                agent.agent.cancel(p.uid("bob"), turn["turn_id"])
            else:
                assert confirm(cards["second"])["status"] == "succeeded"
            assert p.store.one("SELECT status FROM agent_turns")["status"] == "waiting_confirmation"
            assert len(agent.agent.provider.requests) == 1
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "succeeded"
    assert wait_turn(p.store, turn["turn_id"], ("succeeded", "failed", "cancelled")) == ("cancelled" if cancel else "succeeded")
    assert p.store.one("SELECT COUNT(*) AS n FROM forms")["n"] == (1 if cancel else 2)
    assert len(agent.agent.provider.requests) == (1 if cancel else 2)


def test_real_template_copy_keeps_explicit_cross_workspace_contract(agent):
    """C1/C2's existing template-copy contract: B viewer may copy its template into A."""
    p = agent.people
    agent.sign_in("oscar")
    fid = op(agent, "forms.form.create", {"title": "Source template"}, ws=p.b)["output"]["id"]
    agent.sign_in("alice")
    conv, turn = start(agent, [call("run_operation", {"operation": "forms.form.duplicate", "workspace_id": p.b,
                                                   "input": {"form_id": fid, "target_workspace_id": p.a}}), say("copied")])
    assert wait_turn(p.store, turn["turn_id"]) == "waiting_confirmation"
    card = p.store.one("SELECT * FROM agent_tool_calls")
    result = op(agent, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]})["output"]
    assert result["status"] == "succeeded" and result["result"]["workspace_id"] == p.a
    assert result["target_workspace_id"] == p.a and f"/w/{p.a}/" in result["link"]
    assert wait_turn(p.store, turn["turn_id"], ("succeeded", "failed")) == "succeeded"
    assert p.store.one("SELECT workspace_id FROM forms WHERE id=?", (result["result"]["form_id"],))["workspace_id"] == p.a
