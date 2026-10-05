"""Synwevo Agent: content safety and the separate consent to send content to the model provider.

TEST DOUBLE: `DisclosedModel` is `ScriptedModel` (test_agent.py) whose credential names DeepSeek as
the provider, the way the governed DeepSeek route does. It never contacts a model. The DeepSeek
wire case at the end uses the isolated SSE fixtures of test_deepseek.py (httpx.MockTransport).

Business expectations, written before the implementation:
* No model call is made for a person who has not accepted the current provider consent version;
  an older accepted version asks again; withdrawing stops further calls, even inside a running
  answer, and is journalled.
* A governed blocked pattern in the person's message refuses it before anything is stored or sent.
* A provider content filter (DeepSeek ``finish_reason: content_filter``) and a blocked pattern in
  the model's output end the turn with distinct codes, and what was shown of it is withdrawn.
* Safety records keep the rule, direction and length — never the message text.
* An administrator's suspension stops the person's Agent; a report carries the answer only by choice.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core import journal
from synwevo_core.agent_safety import AgentAccess, SafetyRules, normalize, validate_rules
from synwevo_core.errors import ServiceError
from synwevo_suite.agent.provider import Credential, StepResult
from synwevo_suite.agent.safety import refused
from .test_agent import ScriptedModel, call, op, say, wait_turn

CSRF = {"X-Synwevo-CSRF": "fixture-csrf"}


class DisclosedModel(ScriptedModel):
    """TEST DOUBLE: a scripted model whose platform credential names DeepSeek as the provider."""

    def resolve_credential(self, store, user_id):
        return Credential("platform", "fixture-key-not-a-real-secret", "deepseek-flash", provider="deepseek")


@pytest.fixture()
def agent(suite, monkeypatch):
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "test-double-key-not-a-real-credential")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", "test-double-model")
    suite.agent = suite.api.state.agent
    # The fixture people start with the consent accepted (conftest); here it is the subject.
    suite.people.store.execute("DELETE FROM agent_provider_consents")
    suite.people.store.execute("DELETE FROM agent_provider_consent_log")
    return suite


def _start(c, steps, text="hello"):
    c.agent.provider = DisclosedModel(steps)
    conv = op(c, "agent.conversation.create", {})["output"]["id"]
    return conv, c.post("/api/suite/ops/agent.turn.start",
                        json={"input": {"conversation_id": conv, "text": text}}, headers=CSRF)


def _consent(c):
    c.agent.provider = DisclosedModel([])
    op(c, "agent.provider_consent.accept", {"version": op(c, "agent.provider_consent.get")["output"]["version"]})


def _rules(c, rules):
    SafetyRules(c.people.store).publish(c.people.uid("alice"), rules, reason="fixture rule list")


def _journal(c):
    return [e for e in journal.read(c.people.store.root) if e["kind"].startswith("agent_")]


# ------------------------------------------------------------------ consent
def test_no_model_call_without_provider_consent(agent):
    agent.sign_in("alice")
    _, r = _start(agent, [say("never sent")])
    assert r.status_code == 409 and r.json()["error"] == "provider_consent_required"
    assert r.json()["detail"]["provider"] == "deepseek" and r.json()["detail"]["version"]
    assert agent.agent.provider.requests == []
    assert agent.people.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"] == 0


def test_consent_names_the_processor_and_then_withdrawal_stops_calls(agent):
    agent.sign_in("alice")
    agent.agent.provider = DisclosedModel([])
    info = op(agent, "agent.provider_consent.get")["output"]
    assert info["provider"] == "deepseek" and info["accepted"] is False and info["required"] is True
    assert "DeepSeek" in info["processor"]["en"] and info["data_location"] == "CN"
    op(agent, "agent.provider_consent.accept", {"version": info["version"]})
    _, r = _start(agent, [say("你好")])
    assert r.status_code == 200, r.text
    assert wait_turn(agent.people.store, r.json()["output"]["turn_id"]) == "succeeded"
    out = op(agent, "agent.provider_consent.withdraw", {})["output"]
    assert out["accepted"] is False and [h["action"] for h in out["history"]] == ["accepted", "withdrawn"]
    _, r = _start(agent, [say("never sent")])
    assert r.json()["error"] == "provider_consent_required"
    assert agent.agent.provider.requests == []
    assert [(e["kind"], e["provider"]) for e in _journal(agent)] == [("agent_provider_consent_withdrawn", "deepseek")]


def test_stale_or_superseded_consent_version_is_refused(agent):
    agent.sign_in("alice")
    agent.agent.provider = DisclosedModel([])
    r = agent.post("/api/suite/ops/agent.provider_consent.accept",
                   json={"input": {"version": "0-outdated"}}, headers=CSRF)
    assert r.status_code == 409 and r.json()["error"] == "provider_consent_version_changed"
    _consent(agent)
    # A person who accepted an older wording is asked again before the next model call.
    agent.people.store.execute("UPDATE agent_provider_consents SET version='2000-01-01'")
    _, r = _start(agent, [say("never sent")])
    assert r.status_code == 409 and r.json()["error"] == "provider_consent_version_changed"
    assert agent.agent.provider.requests == []


def test_withdrawing_inside_a_running_answer_stops_the_next_model_call(agent):
    agent.sign_in("alice")
    _consent(agent)
    uid = agent.people.uid("alice")

    def withdraw_then_call_a_tool(messages):
        from synwevo_core.agent_safety import ProviderConsents
        ProviderConsents(agent.people.store).withdraw(uid, "deepseek")
        return call("list_workspaces", {})

    _, r = _start(agent, [withdraw_then_call_a_tool, say("never requested")])
    tid = r.json()["output"]["turn_id"]
    assert wait_turn(agent.people.store, tid) == "failed"
    assert agent.people.store.one("SELECT error_code FROM agent_turns WHERE id=?", (tid,))["error_code"] == "provider_consent_required"
    assert len(agent.agent.provider.requests) == 1


def test_a_provider_that_is_not_a_named_processor_needs_no_consent(agent):
    from .test_agent import start
    agent.sign_in("alice")
    _, out = start(agent, [say("hi")])
    assert wait_turn(agent.people.store, out["turn_id"]) == "succeeded"


# ------------------------------------------------------------------ input and output rules
def test_blocked_input_is_refused_before_anything_is_stored_and_logged_without_text(agent):
    agent.sign_in("alice")
    _consent(agent)
    _rules(agent, [{"kind": "keyword", "pattern": "forbidden-fixture-term", "applies_to": "input", "category": "illegal"}])
    _, r = _start(agent, [say("never sent")], text="please do FORBIDDEN-fixture-term now")
    assert r.status_code == 422 and r.json()["error"] == "content_blocked_input"
    assert r.json()["detail"] == {"category": "illegal"}
    assert agent.agent.provider.requests == []
    assert agent.people.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"] == 0
    log = agent.people.store.one("SELECT * FROM agent_safety_events")
    assert log["direction"] == "input" and log["rule_id"] == "r1" and log["rule_version"] == 1
    assert log["characters"] == len("please do FORBIDDEN-fixture-term now")
    assert "forbidden" not in json.dumps(log).lower()


def test_an_output_only_rule_does_not_refuse_the_question(agent):
    agent.sign_in("alice")
    _consent(agent)
    _rules(agent, [{"kind": "keyword", "pattern": "fixture-word", "applies_to": "output"}])
    _, r = _start(agent, [say("fine")], text="what does fixture-word mean?")
    assert r.status_code == 200
    assert wait_turn(agent.people.store, r.json()["output"]["turn_id"]) == "succeeded"


def test_provider_content_filter_has_its_own_code_and_is_withheld(agent):
    agent.sign_in("alice")
    _consent(agent)
    _, r = _start(agent, [StepResult(content=[{"type": "text", "text": "partial"}], stop_reason="refusal",
                                     usage={"output_tokens": 3}, stop_details={"category": "content_filter"})])
    tid = r.json()["output"]["turn_id"]
    assert wait_turn(agent.people.store, tid) == "failed"
    row = agent.people.store.one("SELECT t.error_code, m.body FROM agent_turns t JOIN agent_messages m "
                                 "ON m.id=t.assistant_message_id WHERE t.id=?", (tid,))
    assert row["error_code"] == "content_filtered_output" and row["body"] == ""
    ev = agent.people.store.one("SELECT direction, category FROM agent_safety_events")
    assert ev == {"direction": "provider", "category": "content_filter"}


def test_other_refusals_stay_provider_refused():
    step = StepResult(content=[], stop_reason="refusal", stop_details={"category": "cyber"})
    assert refused(step).code == "provider_refused"
    assert refused(StepResult(content=[], stop_reason="refusal")).code == "provider_refused"


def test_blocked_output_is_withheld_from_the_message_and_the_stream(agent):
    agent.sign_in("alice")
    _consent(agent)
    _rules(agent, [{"kind": "keyword", "pattern": "leaked-fixture-term", "applies_to": "output"}])
    # The term arrives split over two streamed pieces, after an innocent one.
    step = StepResult(content=[{"type": "text", "text": "Here it is: "}, {"type": "text", "text": "leaked-fix"},
                               {"type": "text", "text": "ture-term and more"}], stop_reason="end_turn",
                      usage={"output_tokens": 9})
    conv, r = _start(agent, [step])
    tid = r.json()["output"]["turn_id"]
    assert wait_turn(agent.people.store, tid) == "failed"
    row = agent.people.store.one("SELECT m.body, m.api_json, t.error_code FROM agent_turns t JOIN agent_messages m "
                                 "ON m.id=t.assistant_message_id WHERE t.id=?", (tid,))
    assert row["error_code"] == "content_blocked_output" and row["body"] == ""
    assert "leaked" not in (row["api_json"] or "")            # never replayed to the model either
    events = agent.agent.events_after(agent.people.uid("alice"), conv, 0)
    assert "leaked" not in json.dumps(events) and "Here it is" not in json.dumps(events)
    got = op(agent, "agent.conversation.get", {"conversation_id": conv})["output"]
    answer = [m for m in got["messages"] if m["role"] == "assistant"][0]
    assert answer["error"]["code"] == "content_blocked_output"


class StreamingModel(DisclosedModel):
    """TEST DOUBLE: streams its pieces slower than the engine's flush interval and honours
    should_stop between pieces, the way the DeepSeek adapter does."""

    def __init__(self, pieces):
        super().__init__([])
        self.pieces, self.sent = pieces, []

    def step(self, *, credential, system, tools, messages, effort, on_text, should_stop):
        import time as _time
        from synwevo_suite.agent.provider import Cancelled
        self.requests.append({"messages": messages})
        for piece in self.pieces:
            if should_stop():
                raise Cancelled()
            self.sent.append(piece)
            on_text(piece)
            _time.sleep(0.2)
        return StepResult(content=[{"type": "text", "text": "".join(self.pieces)}], stop_reason="end_turn")


def test_a_blocked_piece_is_never_shown_and_the_stream_is_stopped(agent, monkeypatch):
    agent.sign_in("alice")
    _consent(agent)
    _rules(agent, [{"kind": "keyword", "pattern": "leaked-fixture-term", "applies_to": "output"}])
    shown = []
    original = agent.agent._append_text
    monkeypatch.setattr(agent.agent, "_append_text", lambda t, text: (shown.append(text), original(t, text)))
    model = StreamingModel(["Here it is: ", "leaked-fix", "ture-term", " and the rest", " never streamed"])
    agent.agent.provider = model
    conv = op(agent, "agent.conversation.create", {})["output"]["id"]
    tid = op(agent, "agent.turn.start", {"conversation_id": conv, "text": "go"})["output"]["turn_id"]
    assert wait_turn(agent.people.store, tid) == "failed"
    assert agent.people.store.one("SELECT error_code FROM agent_turns WHERE id=?", (tid,))["error_code"] == "content_blocked_output"
    assert "ture-term" not in "".join(shown)                 # the completing piece was never shown
    assert model.sent == ["Here it is: ", "leaked-fix", "ture-term"]   # and the model call stopped there


# ------------------------------------------------------------------ the governed lists
def test_rule_lists_are_versioned_validated_and_normalised(agent):
    store = agent.people.store
    rules = SafetyRules(store)
    with pytest.raises(ServiceError) as e:
        rules.publish("admin", [{"kind": "pattern", "pattern": "(a+)+$"}], reason="catastrophic")
    assert e.value.code == "safety_rule_invalid" and e.value.detail == {"index": 0, "field": "pattern"}
    with pytest.raises(ServiceError):
        validate_rules([{"kind": "keyword", "pattern": "x", "applies_to": "sideways"}])
    rules.publish("admin", [{"kind": "keyword", "pattern": "Ｂａｄ Word"}], reason="first list")
    with pytest.raises(ServiceError) as e:
        rules.publish("admin", [], reason="stale edit", expected_version=0)
    assert e.value.code == "safety_rules_changed"
    compiled = rules.compiled()
    assert compiled.version == 1 and compiled.match("a b​ad   WORD here", "input") is not None
    assert normalize("Ｂａｄ  Word") == "bad word"
    with pytest.raises(Exception):
        store.execute("UPDATE agent_safety_rule_versions SET reason='rewritten'")


# ------------------------------------------------------------------ suspension and reports
def test_a_suspended_person_cannot_use_the_agent_and_the_suspension_is_journalled(agent):
    agent.sign_in("alice")
    _consent(agent)
    uid = agent.people.uid("alice")
    access = AgentAccess(agent.people.store)
    access.suspend(uid, "admin-fixture", reason="repeated abuse")
    _, r = _start(agent, [say("never sent")])
    assert r.status_code == 403 and r.json()["error"] == "agent_access_suspended"
    assert agent.agent.provider.requests == []
    assert [e["kind"] for e in _journal(agent)] == ["agent_access_suspended"]
    access.lift(uid, "admin-fixture", reason="appeal accepted")
    _, r = _start(agent, [say("back")])
    assert r.status_code == 200


def test_reporting_an_answer_shares_its_text_only_by_choice(agent):
    agent.sign_in("alice")
    _consent(agent)
    conv, r = _start(agent, [say("a wrong answer")])
    assert wait_turn(agent.people.store, r.json()["output"]["turn_id"]) == "succeeded"
    mid = r.json()["output"]["assistant_message_id"]
    out = op(agent, "agent.message.report", {"conversation_id": conv, "message_id": mid, "category": "wrong",
                                              "note": "made up a figure"})["output"]
    assert out["included_excerpt"] is False
    row = agent.people.store.one("SELECT excerpt, note, status FROM agent_message_reports WHERE id=?", (out["id"],))
    assert row == {"excerpt": None, "note": "made up a figure", "status": "open"}
    again = agent.post("/api/suite/ops/agent.message.report", headers=CSRF,
                       json={"input": {"conversation_id": conv, "message_id": mid, "category": "harmful"}})
    assert again.status_code == 409 and again.json()["error"] == "report_exists"
    # Another person cannot report (or learn about) alice's conversation.
    agent.sign_in("bob")
    other = agent.post("/api/suite/ops/agent.message.report", headers=CSRF,
                       json={"input": {"conversation_id": conv, "message_id": mid, "category": "wrong",
                                       "include_excerpt": True}})
    assert other.status_code == 404


def test_an_answer_hidden_after_an_access_change_cannot_be_copied_into_a_report(agent):
    """The Agent hides an answer once the person lost access to a workspace it read; a report
    must not carry that text to the platform's reviewers either (it may still be filed bare)."""
    p = agent.people
    agent.sign_in("alice")
    _consent(agent)
    agent.agent.provider = DisclosedModel([
        call("run_operation", {"operation": "forms.form.list", "workspace_id": p.b, "input": {}}),
        lambda msgs: say("fixture answer built from workspace B"),
    ])
    conv = op(agent, "agent.conversation.create", {})["output"]["id"]
    r = agent.post("/api/suite/ops/agent.turn.start", json={"input": {"conversation_id": conv, "text": "B?"}}, headers=CSRF)
    assert wait_turn(p.store, r.json()["output"]["turn_id"]) == "succeeded"
    mid = r.json()["output"]["assistant_message_id"]
    p.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (p.b, p.uid("alice")))
    shown = [m for m in op(agent, "agent.conversation.get", {"conversation_id": conv})["output"]["messages"] if m["id"] == mid]
    assert shown[0]["hidden"] is True
    refused = agent.post("/api/suite/ops/agent.message.report", headers=CSRF, json={"input": {
        "conversation_id": conv, "message_id": mid, "category": "wrong", "include_excerpt": True}})
    assert refused.status_code == 409 and refused.json()["error"] == "report_excerpt_hidden"
    assert p.store.one("SELECT COUNT(*) AS n FROM agent_message_reports")["n"] == 0
    out = op(agent, "agent.message.report", {"conversation_id": conv, "message_id": mid, "category": "wrong"})["output"]
    assert out["included_excerpt"] is False
    assert "workspace B" not in json.dumps(p.store.all("SELECT * FROM agent_message_reports"))


# ------------------------------------------------------------------ DeepSeek wire
def test_deepseek_content_filter_finish_is_named_on_the_step():
    from .test_deepseek import complete, run
    result, _, _, _ = run(complete(finish="content_filter"))
    assert result.step.stop_reason == "refusal" and result.step.stop_details == {"category": "content_filter"}
    assert refused(result.step).code == "content_filtered_output"
    plain, _, _, _ = run(complete(finish="stop"))
    assert plain.step.stop_details is None


# ------------------------------------------------------------------ restore (security journal)
def test_a_restore_cannot_bring_back_a_withdrawn_consent_but_keeps_a_later_one(agent):
    from synwevo_core.agent_safety import ProviderConsents
    store, uid = agent.people.store, agent.people.uid("alice")
    agent.sign_in("alice")
    _consent(agent)
    op(agent, "agent.provider_consent.withdraw", {})
    entries = _journal(agent)
    # An older backup still holds the consent as given.
    store.execute("UPDATE agent_provider_consents SET withdrawn_at=NULL")
    assert journal.apply(store, entries)["agent_provider_consent_withdrawn"] == 1
    assert ProviderConsents(store).status(uid, "deepseek")["accepted"] is False
    assert journal.apply(store, entries)["agent_provider_consent_withdrawn"] == 0
    # A consent the person gave again after the withdrawal (and the backup holds) stands.
    _consent(agent)
    store.execute("UPDATE agent_provider_consents SET accepted_at=accepted_at+5")
    journal.apply(store, entries)
    assert ProviderConsents(store).status(uid, "deepseek")["accepted"] is True


def test_a_restore_cannot_lift_an_agent_suspension(agent):
    store, uid = agent.people.store, agent.people.uid("alice")
    AgentAccess(store).suspend(uid, "admin-fixture", reason="fixture abuse")
    entries = _journal(agent)
    store.execute("DELETE FROM agent_access_suspensions")            # a backup from before it
    assert journal.apply(store, entries)["agent_access_suspended"] == 1
    assert AgentAccess(store).active(uid) is not None
    assert journal.apply(store, entries)["agent_access_suspended"] == 0


def test_a_restore_cannot_reopen_a_workspace_to_the_model(agent):
    from synwevo_suite.agent.tools import workspace_policy
    store, a = agent.people.store, agent.people.a
    agent.sign_in("alice")
    op(agent, "agent.workspace_policy.set", {"processing": "any", "providers": ["anthropic", "deepseek"]}, ws=a)
    narrowed = lambda: [e for e in journal.read(store.root) if e["kind"] == "ai_policy_narrowed"]  # noqa: E731
    assert narrowed() == []                                                  # widening: nothing
    op(agent, "agent.workspace_policy.set", {"processing": "platform", "providers": ["anthropic"]}, ws=a)
    entries = narrowed()
    assert [(e["processing"], e["providers"]) for e in entries] == [("platform", ["anthropic"])]
    # A backup from before the narrowing: neither the narrower policy nor the receipt that committed with it.
    store.execute("DELETE FROM meta WHERE key=?", ("ai_policy_narrowed:" + entries[0]["event_id"],))
    store.execute("UPDATE agent_workspace_policies SET processing='any' WHERE workspace_id=?", (a,))
    store.execute("UPDATE agent_workspace_model_providers SET providers_json='[\"anthropic\",\"deepseek\"]' WHERE workspace_id=?", (a,))
    assert journal.apply(store, entries)["ai_policy_narrowed"] == 1
    policy = workspace_policy(store, a)
    assert policy["processing"] == "platform" and policy["providers"] == ["anthropic"]
    assert journal.apply(store, entries)["ai_policy_narrowed"] == 0
    # A policy narrowed further later (and in the backup) is never widened by the replay.
    store.execute("UPDATE agent_workspace_policies SET processing='none' WHERE workspace_id=?", (a,))
    journal.apply(store, entries)
    assert workspace_policy(store, a)["processing"] == "none"


# ------------------------------------------------------------------ terms at first paid use
from .test_agent_metering import gateway  # noqa: E402,F401 — fixture (isolated paid gateway)
from .test_agent_run_quotes import paid  # noqa: E402,F401 — fixture


def test_the_first_paid_run_records_the_published_terms_versions_once(paid):  # noqa: F811
    from synwevo_core.terms import DOCUMENTS, TermsAcceptances
    g = paid
    caps, uid = g.agent.caps, g.user["id"]
    accepted = TermsAcceptances(g.store)
    assert accepted.history(uid) == []
    args = {"conversation_id": g.conversation["id"], "text": "fixture question", "budget": "1000", "max_calls": 3,
            "run_seconds": 500}
    quote = caps.invoke("agent.turn.quote", args, user_id=uid, idempotency_key="terms-quote-1")["output"]
    assert accepted.history(uid) == []                        # a quote is not yet a paid use
    turn = caps.invoke("agent.turn.start", {"conversation_id": g.conversation["id"], "text": "fixture question",
                                            "quote_id": quote["id"]}, user_id=uid, idempotency_key="terms-start-1")
    rows = accepted.history(uid)
    assert {(r["document"], r["version"]) for r in rows} == {(d, v) for d, (v, _) in DOCUMENTS.items()}
    assert {r["context"] for r in rows} == {"paid_run:" + turn["output"]["turn_id"]}
    assert accepted.record_paid_use(uid, context="paid_run:later") == []      # once per version


def test_an_unpaid_run_records_no_terms(agent):
    from synwevo_core.terms import TermsAcceptances
    agent.sign_in("alice")
    _consent(agent)
    _, r = _start(agent, [say("free")])
    assert wait_turn(agent.people.store, r.json()["output"]["turn_id"]) == "succeeded"
    assert TermsAcceptances(agent.people.store).history(agent.people.uid("alice")) == []
