"""Send-and-go: a person's send from the page starts at once under an automatic run authorization.

Owner decision 2026-10-23 (D17): no per-message budget, quote or price dialog. The server issues
and binds the run authorization inside the admission transaction (one immutable
``agent_model_quotes`` row per paid turn, bounded by one call's hold x 12 or the person's lower
cap) and every money invariant stays enforced at reserve and dispatch. Real turn admission,
ledger, quotes and capability layer; isolated provider/payment protocol fixtures only
(``paid``: a fixture gateway; ``governed``: the real Suite factory with fixture prices). No
network: both fixtures fail on any real transport.
"""
from dataclasses import asdict
import json
import uuid

import pytest

from synwevo_core.billing import Unit
from synwevo_core.errors import ServiceError
from synwevo_core.terms import DOCUMENTS, TermsAcceptances
from synwevo_suite.agent.gateway import RUN_TIME_TOO_SHORT

from . import test_agent_governed_factory as factory
from .test_agent_governed_factory import configure, fund, governed, publish  # noqa: F401 - fixture
from .test_agent_metering import STAMP, USAGE, complete, event, gateway  # noqa: F401 - fixture
from .test_agent_payers import cny, fund_org, grant, organization  # noqa: F401 - fixture
from .test_agent_run_quotes import paid  # noqa: F401 - fixture


def accept_terms(store, uid):
    """FIXTURE: the person accepted the published terms, AI use rules and pricing versions in
    force, as they would in the Agent's one-time start step (test_agent_setup.py tests the step)."""
    TermsAcceptances(store).record_paid_use(uid, context="fixture:agent_setup")


def displayed(g, user=None):
    """What the open page displays beside the composer and repeats on a send (AD-1, addendum-2
    S1): ``seen_*`` from the ``run`` of agent.billing.status, read as the page reads it (the same
    handler, without a run record); nothing when the page has no price to show."""
    run = g.agent.caps.inspect("agent.billing.status", {}, user_id=(user or g.user)["id"]).get("run")
    return {} if not run else {"seen_price_id": run["price_id"], "seen_route_version": run["route_version"]}


def send(g, text="fixture question", *, via="ui", key=None, user=None, shown=True, **extra):
    """A send. From the page (``via=ui``) without an explicit authorization it carries the price
    the page displays, as the page does, unless ``shown=False`` or the test names ``seen_*``."""
    payload = {"conversation_id": g.conversation["id"], "text": text, **extra}
    if shown and via == "ui" and not {"quote_id", "consent_id", "seen_price_id", "seen_route_version"} & set(extra):
        payload.update(displayed(g, user))
    return g.agent.caps.invoke("agent.turn.start", payload, user_id=(user or g.user)["id"], via=via,
                               idempotency_key=key or uuid.uuid4().hex, **({"scopes": frozenset()} if via == "api" else {}))


def refused(fn, *args, **kwargs):
    with pytest.raises(ServiceError) as err:
        fn(*args, **kwargs)
    return err.value


def quotes_of(g):
    return g.store.all("SELECT * FROM agent_model_quotes ORDER BY created_at, rowid")


def nothing_stored(g):
    assert g.store.all("SELECT * FROM agent_turns") == []
    assert g.store.all("SELECT * FROM agent_messages") == []
    assert quotes_of(g) == []
    assert g.store.all("SELECT * FROM billing_calls") == []


def tool_reply(index, usage, *, workspace_id):
    """A model step that calls one tool, with the usage the fixture upstream reports for it."""
    arguments = {"operation": "forms.form.list", "workspace_id": workspace_id, "input": {}}
    body = event({"tool_calls": [{"index": 0, "id": f"fixture-tool-{index}", "type": "function",
                                  "function": {"name": "run_operation", "arguments": json.dumps(arguments)}}]})
    return (body + event(finish="tool_calls", usage=usage) + b"data: [DONE]\n\n").replace(
        b"fixture-upstream-1", f"fixture-upstream-{index}".encode())


# ------------------------------------------------------------------ 1. the send itself
def test_a_send_from_the_page_starts_at_once_under_an_automatic_bound_authorization(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    out = send(g)["output"]
    assert out["turn_id"] and g.store.one("SELECT status FROM agent_turns WHERE id=?", (out["turn_id"],))["status"] == "queued"
    rows = quotes_of(g)
    assert len(rows) == 1 and rows[0]["accepted_turn_id"] == out["turn_id"], "一条回答一份授权，准入时即绑定"
    terms = json.loads(rows[0]["terms_json"])
    hold = int(g.quotes.offer()["per_call_hold"])
    assert terms["budget"] == hold * 12 and terms["per_call_hold"] == hold and terms["max_calls"] == 12
    assert terms["deadline"] - rows[0]["created_at"] == min(3600, g.route.valid_until - STAMP) == 1000
    assert terms["payer"] == {"kind": "user", "id": g.user["id"]}
    issued = terms["issued"]
    assert issued["mode"] == "automatic" and issued["actor_user_id"] == g.user["id"] and issued["via"] == "ui"
    assert issued["source"] == "agent.turn.start" and issued["bound"] == "default"
    assert g.store.one("SELECT id FROM op_runs WHERE id=?", (issued["op_run_id"],)), "审计记录指向发起它的操作运行"
    assert g.quotes.get(g.user["id"], rows[0]["id"])["authorization"] == "automatic"
    assert "fixture question" not in rows[0]["terms_json"]
    # The turn runs and settles exactly as a confirmed one did.
    g.agent._run(out["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (out["turn_id"],))["status"] == "succeeded"
    assert len(g.seen) == 1 and g.balance().booked == 958 and g.balance().held == 0
    run = g.store.one("SELECT budget,max_calls,deadline FROM billing_runs WHERE run_id=?", (out["turn_id"],))
    assert (run["budget"], run["max_calls"], run["deadline"]) == (hold * 12, 12, terms["deadline"])


# ------------------------------------------------------------------ 2. the bound is respected
def test_the_persons_cap_bounds_the_run_and_every_charge_stays_within_its_hold(governed):
    g = governed
    configure(g)
    fund(g)
    accept_terms(g.store, g.user["id"])
    hold = int(g.suite.caps.inspect("agent.billing.offer", {}, user_id=g.user["id"])["per_call_hold"])
    cap = 2 * hold
    g.suite.caps.invoke("agent.settings.update", {"run_budget_cap": str(cap)}, user_id=g.user["id"],
                        idempotency_key="cap-fixture")
    # Each call's charge stays under its hold but above half of it: two settle inside the cap,
    # a third reservation would exceed it.
    heavy = {**USAGE, "prompt_tokens": 250, "prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 250,
             "completion_tokens": 60, "total_tokens": 310}
    g.replies = [tool_reply(1, heavy, workspace_id=g.wid), tool_reply(2, heavy, workspace_id=g.wid),
                 tool_reply(3, heavy, workspace_id=g.wid)]
    out = send(g, "Read my forms")["output"]
    terms = json.loads(quotes_of(g)[0]["terms_json"])
    assert terms["budget"] == cap and terms["issued"]["bound"] == "personal_cap"
    g.agent._run(out["turn_id"])
    turn = g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (out["turn_id"],))
    assert (turn["status"], turn["error_code"]) == ("failed", "billing_run_budget_exceeded"), turn
    assert len(g.seen) == 2, "第三次预留被拒，没有再发送"
    calls = g.store.all("SELECT c.hold_units, c.status, j.amount FROM billing_calls c "
                        "LEFT JOIN billing_settlements s ON s.call_key=c.id LEFT JOIN billing_journal j ON j.id=s.journal_id")
    assert len(calls) == 2 and all(c["status"] == "settled" for c in calls)
    assert all(0 < c["amount"] <= c["hold_units"] <= hold for c in calls), "每次扣费不超过其预留"
    assert sum(c["amount"] for c in calls) <= cap, "总扣费不超过本人上限"
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


# ------------------------------------------------------------------ 3. a cap that cannot be honoured
@pytest.mark.parametrize("problem", ["below_hold", "unit_changed"])
def test_a_cap_below_one_call_or_in_another_unit_refuses_the_send_with_nothing_stored(paid, problem):
    g = paid
    accept_terms(g.store, g.user["id"])
    hold = int(g.quotes.offer()["per_call_hold"])
    g.agent.caps.invoke("agent.settings.update", {"run_budget_cap": str(hold - 1 if problem == "below_hold" else hold)},
                        user_id=g.user["id"], idempotency_key="cap-fixture")
    if problem == "unit_changed":
        # FIXTURE: the cap was set when another sale unit was published.
        g.store.execute("UPDATE agent_settings SET run_budget_cap_unit='FIXTURE-OLD-UNIT' WHERE user_id=?", (g.user["id"],))
    err = refused(send, g)
    assert (err.status, err.code) == (409, "billing_run_cap_below_call_hold" if problem == "below_hold" else "billing_run_cap_unit_changed")
    assert "报价" not in err.message
    if problem == "below_hold":
        assert err.detail == {"per_call_hold": str(hold), "cap": str(hold - 1), "unit": {"code": "FIXTURE-GATEWAY", "scale": 6}}
    nothing_stored(g)
    assert g.seen == []
    status = g.agent.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]
    assert status["run"]["usable"] is False and status["run"]["source"] == "personal_cap"


# ------------------------------------------------------------------ 4. other callers are unchanged
@pytest.mark.parametrize("via,expected", [("flow", (409, "billing_authorization_required")),
                                          ("automation", (409, "billing_authorization_required")),
                                          ("system", (409, "billing_authorization_required")),
                                          ("api", (403, "person_required")), ("agent", (403, "not_for_agent"))])
def test_no_other_caller_starts_a_paid_turn_without_an_explicit_authorization(paid, via, expected):
    g = paid
    accept_terms(g.store, g.user["id"])
    err = refused(send, g, via=via)
    assert (err.status, err.code) == expected
    nothing_stored(g)


# ------------------------------------------------------------------ 5. the terms gate
def test_the_terms_must_be_accepted_once_before_the_first_automatic_run(paid):
    g = paid
    uid = g.user["id"]
    err = refused(send, g)
    assert (err.status, err.code) == (409, "terms_acceptance_required")
    assert {d["id"] for d in err.detail["documents"]} == set(DOCUMENTS)
    nothing_stored(g)
    assert TermsAcceptances(g.store).history(uid) == [], "发送从不推断接受"
    current = [{"id": d, "version": v} for d, (v, _) in DOCUMENTS.items()]
    stale = [{"id": d, "version": "2000-01-01"} if d == "pricing" else {"id": d, "version": v} for d, (v, _) in DOCUMENTS.items()]
    err = refused(g.agent.caps.invoke, "agent.terms.accept", {"documents": stale}, user_id=uid, idempotency_key="stale")
    assert (err.status, err.code) == (409, "terms_version_changed")
    assert err.detail["documents"] == [{"id": d, "version": v, "effective": e} for d, (v, e) in DOCUMENTS.items()]
    assert TermsAcceptances(g.store).history(uid) == []
    for via in ("flow", "automation", "system"):
        err = refused(g.agent.caps.invoke, "agent.terms.accept", {"documents": current}, user_id=uid, via=via,
                      idempotency_key=uuid.uuid4().hex)
        assert (err.status, err.code) == (403, "person_required")
    accepted = g.agent.caps.invoke("agent.terms.accept", {"documents": current}, user_id=uid, idempotency_key="accept-1")
    assert accepted["output"]["complete"] is True
    rows = TermsAcceptances(g.store).history(uid)
    assert {(r["document"], r["version"]) for r in rows} == {(d, v) for d, (v, _) in DOCUMENTS.items()}
    assert {r["context"] for r in rows} == {"agent_setup:" + accepted["run_id"]}
    assert send(g)["output"]["turn_id"]
    assert len(TermsAcceptances(g.store).history(uid)) == 3, "自动路径不再写 paid_run 记录"


def test_the_provider_consent_refusal_still_comes_before_the_terms_refusal(paid):
    from synwevo_core.agent_safety import ProviderConsents
    g = paid
    ProviderConsents(g.store).withdraw(g.user["id"], "deepseek")
    err = refused(send, g)
    assert err.code == "provider_consent_required"
    nothing_stored(g)


# ------------------------------------------------------------------ 6. personal limits at admission
def test_a_personal_payer_without_limits_is_refused_at_admission_an_organization_payer_is_not(cny, governed):
    g = governed
    configure(g, payer_budget=False)
    fund(g)
    accept_terms(g.store, g.user["id"])
    err = refused(send, g, "Read my forms")
    assert (err.status, err.code) == (409, "billing_payer_budget_not_configured")
    assert err.message == "本次回答未能开始，消息没有发送，也没有扣费"
    nothing_stored(g)
    assert g.seen == [] and g.store.all("SELECT * FROM billing_attempts") == []
    oid = organization(g)
    fund_org(g, oid)
    grant(g, oid)
    out = send(g, "Read my forms", payer_kind="organization", payer_id=oid)["output"]
    assert out["turn_id"] and json.loads(quotes_of(g)[0]["terms_json"])["payer"] == {"kind": "organization", "id": oid}


# ------------------------------------------------------------------ 7. the organization payer, inline
def test_the_organization_payer_is_chosen_on_the_send_and_needs_the_organizations_grant(cny, governed):
    g = governed
    configure(g)
    fund(g)
    accept_terms(g.store, g.user["id"])
    oid = organization(g)
    fund_org(g, oid)
    err = refused(send, g, "Read my forms", payer_kind="organization", payer_id=oid)
    assert (err.status, err.code) == (409, "billing_org_payer_not_granted")
    nothing_stored(g)
    grant(g, oid)
    out = send(g, "Read my forms", payer_kind="organization", payer_id=oid)["output"]
    terms = json.loads(quotes_of(g)[0]["terms_json"])
    assert terms["payer"] == {"kind": "organization", "id": oid}
    def personal():
        return g.provider.gateway.ledger.balance_for_user(requester_user_id=g.user["id"], payer_user_id=g.user["id"],
                                                          unit=Unit("CNY", 6)).booked
    personal_before = personal()
    g.agent._run(out["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (out["turn_id"],))["status"] == "succeeded"
    call = g.store.one("SELECT a.kind,a.owner FROM billing_calls c JOIN billing_accounts a ON a.id=c.account_id")
    assert (call["kind"], call["owner"]) == ("org_wallet", oid), "组织付款，个人钱包不动"
    assert personal() == personal_before
    # Payer fields together with an explicit authorization are a conflict, whatever the quote says.
    quote = g.agent.quote_turn(g.user, g.conversation["id"], "Read my forms", parent_id=None, regenerate_of=None,
                               budget=5000, max_calls=3, run_seconds=300)
    err = refused(send, g, "Read my forms", quote_id=quote["id"], payer_kind="user", payer_id=g.user["id"])
    assert (err.status, err.code) == (409, "billing_authorization_conflict")
    err = refused(send, g, "Read my forms", via="flow", payer_kind="organization", payer_id=oid)
    assert (err.status, err.code) == (403, "person_required")


def test_payer_fields_without_a_paid_model_are_refused(suite, monkeypatch):
    from .test_agent import ScriptedModel, say
    suite.sign_in("alice")
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "test-double-key-not-a-real-credential")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", "test-double-model")
    suite.api.state.agent.provider = ScriptedModel([say("free")])
    conv = suite.post("/api/suite/ops/agent.conversation.create", json={"input": {}},
                      headers={"X-Synwevo-CSRF": "fixture-csrf"}).json()["output"]["id"]
    r = suite.post("/api/suite/ops/agent.turn.start", json={"input": {"conversation_id": conv, "text": "hi", "payer_kind": "user"}},
                   headers={"X-Synwevo-CSRF": "fixture-csrf"})
    assert r.status_code == 409 and r.json()["error"] == "billing_not_configured", r.text
    assert suite.people.store.all("SELECT * FROM agent_turns") == []


# ------------------------------------------------------------------ 8. a price change between sends and mid-run
def new_sale_price(g, input_uncached="2"):
    from synwevo_core.billing.catalogue import PriceCatalogue
    cat = PriceCatalogue(g.store, environment="test")
    with g.store.tx():
        draft, _ = cat.create(spec={"name": "Factory fixture, changed", "purpose": "sale", "provider": "deepseek",
            "model": "fixture-route", "unit": asdict(factory.UNIT), "input_uncached": input_uncached, "input_cached": "0.1",
            "output": "2", "terms_ref": "fixture:factory-changed", "effective_from": 0},
            actor_id=g.user["id"], idempotency_key=uuid.uuid4().hex)
        published = cat.publish(draft["id"], expected_revision=1, actor_id=g.user["id"], operation_id=uuid.uuid4().hex,
                                reason="Fixture price change")
        publish(g, {"action": "keep"}, {**g.spec, "route": {**g.spec["route"], "sale_price_id": published["price_id"]}})
    return published["price_id"]


def test_a_price_change_between_sends_needs_no_step_and_is_shown_as_a_notice(governed):
    g = governed
    configure(g)
    fund(g)
    accept_terms(g.store, g.user["id"])
    old_price = g.spec["route"]["sale_price_id"]
    old_offer = g.suite.caps.invoke("agent.billing.offer", {}, user_id=g.user["id"])["output"]
    first = send(g, "Read my forms")["output"]
    g.agent._run(first["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (first["turn_id"],))["status"] == "succeeded"
    assert g.suite.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]["price_notice"] is None
    new_price = new_sale_price(g)
    new_offer = g.suite.caps.invoke("agent.billing.offer", {}, user_id=g.user["id"])["output"]
    assert new_offer["price_id"] == new_price != old_price and new_offer["rates"] != old_offer["rates"]
    status = g.suite.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]
    notice = status["price_notice"]
    assert notice["previous"]["price_id"] == old_price and notice["previous"]["rates"] == old_offer["rates"]
    assert notice["previous"]["per_call_hold"] == old_offer["per_call_hold"], "提示的是上次运行的旧价格"
    assert notice["previous_run_at"] == quotes_of(g)[0]["created_at"]
    assert status["run"]["usable"] is True and status["run"]["per_call_hold"] == new_offer["per_call_hold"], "提示不阻断发送"
    g.replies = [complete().replace(b"fixture-upstream-1", b"fixture-upstream-2")]
    second = send(g, "Read my forms again")["output"]
    terms = json.loads(quotes_of(g)[-1]["terms_json"])
    assert terms["sale"]["price_id"] == new_price, "新价格直接生效，没有额外步骤"
    assert str(terms["sale"]["input_uncached"]["numerator"]) == new_offer["rates"]["input_uncached"]["numerator"]
    g.agent._run(second["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (second["turn_id"],))["status"] == "succeeded"
    assert len(g.seen) == 2
    assert g.suite.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]["price_notice"] is None


def test_a_route_change_mid_run_stops_before_the_next_call_and_earlier_calls_settle_at_the_old_price(governed, monkeypatch):
    g = governed
    configure(g)
    fund(g)
    accept_terms(g.store, g.user["id"])
    old_price = g.spec["route"]["sale_price_id"]
    g.replies = [tool_reply(1, USAGE, workspace_id=g.wid), complete().replace(b"fixture-upstream-1", b"fixture-upstream-2")]
    original = g.agent._tool_results

    def change_price_after_the_tool(*args, **kwargs):
        new_sale_price(g)
        return original(*args, **kwargs)
    monkeypatch.setattr(g.agent, "_tool_results", change_price_after_the_tool)
    out = send(g, "Read my forms")["output"]
    g.agent._run(out["turn_id"])
    turn = g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (out["turn_id"],))
    # A route change is published as a new provider revision, so the turn's credential-binding
    # fence (engine._check_credential_binding) stops the run before the gateway's own quote check
    # (quotes.authorize → billing_model_configuration_changed) would; either way no further call.
    assert turn["status"] == "failed", turn
    assert turn["error_code"] in ("model_configuration_changed", "billing_model_configuration_changed"), turn
    assert len(g.seen) == 1, "价格变化后没有再发起模型调用"
    settled = g.store.all("SELECT c.price_id,c.status FROM billing_calls c")
    assert [(c["price_id"], c["status"]) for c in settled] == [(old_price, "settled")], "已完成的调用按原价格结算"
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


# ------------------------------------------------------------------ 9. funds and limits stay at the call
@pytest.mark.parametrize("problem", ["insufficient_funds", "wallet_frozen"])
def test_funds_and_a_frozen_wallet_still_refuse_at_the_first_call_with_the_same_words(governed, problem):
    g = governed
    configure(g)
    accept_terms(g.store, g.user["id"])
    if problem == "wallet_frozen":
        fund(g)
        account = g.store.one("SELECT id FROM billing_accounts WHERE kind='wallet' AND owner=? AND environment='test'", (g.user["id"],))
        # FIXTURE: a chargeback froze the wallet (billing_account_freezes is how the ledger records it).
        g.store.execute("INSERT INTO billing_account_freezes(id,account_id,reason,created_at) VALUES ('fixture-freeze',?,'chargeback',?)",
                        (account["id"], 1))
    out = send(g, "Read my forms")["output"]
    g.agent._run(out["turn_id"])
    turn = g.store.one("SELECT status,error_code,error_message FROM agent_turns WHERE id=?", (out["turn_id"],))
    assert (turn["status"], turn["error_code"]) == ("failed", "billing_" + problem)
    assert turn["error_message"] == "本次运行的预算或用量状态暂不允许继续，请查看费用记录"
    assert g.seen == [] and g.store.all("SELECT * FROM billing_attempts") == []
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")


# ------------------------------------------------------------------ 10. idempotent replay
def test_replaying_the_same_send_returns_the_same_turn_and_one_authorization(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    first = send(g, key="same-send")
    again = send(g, key="same-send")
    assert again["replayed"] and again["output"] == first["output"]
    assert len(quotes_of(g)) == 1 and len(g.store.all("SELECT * FROM agent_turns")) == 1


def as_recorded_by_217(g, run_id, recorded):
    """FIXTURE: rewrite a run record as release 217d66d4 wrote it: that release's whole validated
    input (its model had fewer optional fields) and a fingerprint of all of it, defaults included."""
    from synwevo_core.capabilities import canonical, fingerprint
    g.store.execute("UPDATE op_runs SET input=?, input_hash=? WHERE id=?", (canonical(recorded), fingerprint(recorded), run_id))


def test_a_send_retried_across_the_deploy_replays_the_turn_it_started(paid):
    """int-release-2: the old page keeps the key and the body of a send whose answer was lost
    while the deploy stopped the gateway; the retry reaches the new release, whose StartTurn and
    Settings have gained optional fields, and gets the started turn, not idempotency_conflict."""
    g = paid
    accept_terms(g.store, g.user["id"])
    q = g.issue()
    body = {"conversation_id": g.conversation["id"], "text": "fixture question", "quote_id": q["id"]}
    first = send(g, key="crossed-the-deploy", **body)
    as_recorded_by_217(g, first["run_id"], {"context_refs": [], "file_refs": [], "parent_message_id": None,
                                            "regenerate_of": None, "consent_id": None, **body})
    again = send(g, key="crossed-the-deploy", **body)
    assert again["replayed"] and again["output"]["turn_id"] == first["output"]["turn_id"]
    assert len(g.store.all("SELECT * FROM agent_turns")) == 1
    saved = g.agent.caps.invoke("agent.settings.update", {"effort": "high"}, user_id=g.user["id"], idempotency_key="crossed-settings")
    as_recorded_by_217(g, saved["run_id"], {"effort": "high", "prefer_personal_key": None, "memory_enabled": None,
                                            "personal_key": None, "clear_personal_key": False})
    again = g.agent.caps.invoke("agent.settings.update", {"effort": "high"}, user_id=g.user["id"], idempotency_key="crossed-settings")
    assert again["replayed"] and again["run_id"] == saved["run_id"]
    err = refused(g.agent.caps.invoke, "agent.settings.update", {"effort": "low"}, user_id=g.user["id"],
                  idempotency_key="crossed-settings")
    assert err.code == "idempotency_conflict", "不同的参数仍然冲突"


# ------------------------------------------------------------------ 11. rr-money-1 on the legacy path
def test_a_legacy_quote_bound_with_less_than_one_send_left_is_refused_unstored(paid):
    g = paid
    window = g.model.send_window
    q = g.issue(run_seconds=300)
    assert q["expires_at"] == STAMP + 300 - window, "报价有效期不晚于截止时间减一次发送窗口"
    g.ticks[0] = STAMP + 211
    err = refused(g.agent.start_turn, g.user, g.conversation["id"], "fixture question", parent_id=None, regenerate_of=None,
                  quote_id=q["id"])
    assert (err.status, err.code) == (409, "billing_run_time_too_short")
    assert g.store.all("SELECT * FROM agent_turns") == [] and g.store.all("SELECT * FROM agent_messages") == []
    assert g.quotes.get(g.user["id"], q["id"])["turn_id"] is None and g.seen == []
    # The shortest run is acceptable for one second only: never an expiry already in the past.
    g.ticks[0] = STAMP
    shortest = g.issue(run_seconds=window)
    assert shortest["expires_at"] == STAMP + 1 and shortest["status"] == "quoted"
    assert RUN_TIME_TOO_SHORT.startswith("本次回答已用完单次运行时限") and "重新确认" not in RUN_TIME_TOO_SHORT


# ------------------------------------------------------------------ 12. authz-4
def test_a_member_sees_what_they_can_still_charge_the_organization_never_its_full_balance(cny, governed):
    g = governed
    configure(g)
    oid = organization(g)
    fund_org(g, oid, units=5_000_000)
    grant(g, oid, limit=2_000_000)
    r = g.suite.get("/api/suite/agent/payers", headers=g.suite.sign_in("alice"))
    assert r.status_code == 200, r.text
    org = next(p for p in r.json()["payers"] if p["kind"] == "organization")
    assert org["available"] == "2000000" == org["remaining_this_month"]
    assert "5000000" not in r.text, "成员看不到组织钱包全额"
    grant(g, oid, limit=9_000_000)
    org = next(p for p in g.suite.get("/api/suite/agent/payers", headers=g.suite.sign_in("alice")).json()["payers"]
               if p["kind"] == "organization")
    assert org["available"] == "5000000" and org["remaining_this_month"] == "9000000"


# ------------------------------------------------------------------ 14. the welcome allowance
def test_the_payers_read_grants_the_welcome_allowance_and_the_automatic_issue_does_not(cny, governed, monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", "100")
    g = governed
    configure(g)
    accept_terms(g.store, g.user["id"])
    assert g.store.all("SELECT * FROM billing_grants") == []
    r = g.suite.get("/api/suite/agent/payers", headers=g.suite.sign_in("alice"))
    assert r.json()["welcome"] == {"state": "granted"}
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_grants WHERE kind='welcome'")["n"] == 1
    out = send(g, "Read my forms")["output"]
    assert out["turn_id"] and len(quotes_of(g)) == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_grants WHERE kind='welcome'")["n"] == 1
    g.agent._run(out["turn_id"])
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (out["turn_id"],))["status"] == "succeeded"


# ------------------------------------------------------------------ 13. the account export
def test_the_export_says_how_each_run_was_authorized_and_shows_the_persons_cap(paid):
    from synwevo_core.agent_records import export
    g = paid
    uid = g.user["id"]
    accept_terms(g.store, uid)
    g.agent.caps.invoke("agent.settings.update", {"run_budget_cap": "500"}, user_id=uid, idempotency_key="cap-export")
    automatic = send(g)["output"]
    legacy = g.issue()
    out = export(g.store, uid)
    by_turn = {q["accepted_turn_id"]: q for q in out["quotes"]}
    assert by_turn[automatic["turn_id"]]["authorization"] == "automatic"
    assert by_turn[None]["id"] == legacy["id"] and by_turn[None]["authorization"] == "confirmed"
    assert out["settings"]["run_budget_cap"] == {"amount": "500", "unit": "FIXTURE-GATEWAY"}
    assert "fixture question" not in json.dumps(out["quotes"]), "导出的授权只有固定条款，没有消息正文"
    assert {(t["document"], t["version"]) for t in out["terms_accepted"]} == {(d, v) for d, (v, _) in DOCUMENTS.items()}


# ------------------------------------------------------------------ 15. the price the page showed (AD-1)
STALE_PRICE = "价格或模型已更新，本次没有发送，也没有扣费。请查看新价格后重新发送。"


def shown_run(g):
    """What the open page displays beside the composer: the ``run`` of agent.billing.status."""
    return g.agent.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]["run"]


def seen(run):
    return {"seen_price_id": run["price_id"], "seen_route_version": run["route_version"]}


@pytest.mark.parametrize("stale", [{"seen_price_id": "fixture-price-shown-earlier"},
                                   {"seen_route_version": "fixture-route-shown-earlier"},
                                   {"seen_price_id": "fixture-price-shown-earlier", "seen_route_version": "fixture-route-v1"}])
def test_a_send_from_a_page_showing_another_price_is_refused_with_the_current_one_and_nothing_stored(paid, stale):
    g = paid
    accept_terms(g.store, g.user["id"])
    run = shown_run(g)
    hold = int(g.quotes.offer()["per_call_hold"])
    assert (run["price_id"], run["route_version"]) == ("fixture-sale", "fixture-route-v1")
    err = refused(send, g, **stale)
    assert (err.status, err.code) == (409, "billing_price_changed")
    assert err.message == STALE_PRICE and "报价" not in err.message
    assert err.detail == {"current": {"price_id": "fixture-sale", "route_version": "fixture-route-v1",
                                      "maximum": str(hold * 12), "per_call_hold": str(hold),
                                      "unit": {"code": "FIXTURE-GATEWAY", "scale": 6}}}, "带回新的最高金额"
    nothing_stored(g)
    assert g.seen == []


def test_a_send_carrying_the_price_the_page_showed_starts_at_that_price(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    run = shown_run(g)
    out = send(g, **seen(run))["output"]
    terms = json.loads(quotes_of(g)[0]["terms_json"])
    assert quotes_of(g)[0]["accepted_turn_id"] == out["turn_id"]
    assert terms["sale"]["price_id"] == run["price_id"] and terms["route"]["version"] == run["route_version"]
    assert str(terms["budget"]) == run["maximum"] and str(terms["per_call_hold"]) == run["per_call_hold"]


@pytest.mark.parametrize("partial", [{}, "price_id only", "route_version only"])
def test_a_send_without_the_shown_price_is_refused_with_the_current_one_and_nothing_stored(paid, partial):
    """addendum-2 S1: no deployed page reaches the automatic path without them (the earlier
    bundle always sent quote_id/consent_id), so a send naming no displayed price is one the page
    sent while it showed none (loading, unreadable, no run): refused, never run at a price unseen."""
    g = paid
    accept_terms(g.store, g.user["id"])
    run = shown_run(g)
    fields = {} if partial == {} else ({"seen_price_id": run["price_id"]} if partial == "price_id only"
                                       else {"seen_route_version": run["route_version"]})
    err = refused(send, g, shown=False, **fields)
    hold = int(g.quotes.offer()["per_call_hold"])
    assert (err.status, err.code, err.message) == (409, "billing_price_changed", STALE_PRICE)
    assert err.detail["current"]["maximum"] == str(hold * 12) == run["maximum"], "带回页面应显示的最高金额"
    assert (err.detail["current"]["price_id"], err.detail["current"]["per_call_hold"]) == (run["price_id"], run["per_call_hold"])
    nothing_stored(g)
    assert g.seen == []
    # The page reads the price, shows it, and the person sends again: it starts at that price.
    out = send(g, **seen(shown_run(g)))["output"]
    assert quotes_of(g)[0]["accepted_turn_id"] == out["turn_id"]


def test_a_page_that_opened_while_the_route_was_unusable_cannot_send_at_a_price_it_never_showed(paid):
    """int-money-2: the route lapsed, so billing.status has no run and the page shows no maximum;
    the owner renews it (a new version) before the page re-reads; a send then names no price."""
    from dataclasses import replace
    g = paid
    accept_terms(g.store, g.user["id"])
    g.route_holder[0] = replace(g.route, valid_until=1)
    status = g.agent.caps.invoke("agent.billing.status", {}, user_id=g.user["id"])["output"]
    assert status["available"] is False and status.get("run") is None
    assert displayed(g) == {}, "页面此时没有可显示的价格"
    g.route_holder[0] = replace(g.route, version="fixture-route-renewed")
    err = refused(send, g, shown=False)     # the page still shows no price: it names none
    assert (err.status, err.code) == (409, "billing_price_changed")
    assert err.detail["current"]["route_version"] == "fixture-route-renewed"
    nothing_stored(g)
    out = send(g)["output"]                 # after the re-read the page shows the price and sends it
    assert json.loads(quotes_of(g)[0]["terms_json"])["route"]["version"] == "fixture-route-renewed"
    assert quotes_of(g)[0]["accepted_turn_id"] == out["turn_id"]


def test_the_shown_price_is_part_of_the_automatic_path_only(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    run = shown_run(g)
    q = g.issue()
    for authorization in ({"quote_id": q["id"]}, {"consent_id": "consent_" + "0" * 12}):
        err = refused(send, g, **authorization, **seen(run))
        assert (err.status, err.code) == (409, "billing_authorization_conflict")
        err = refused(send, g, **authorization, seen_route_version=run["route_version"])
        assert (err.status, err.code) == (409, "billing_authorization_conflict")
    assert g.store.all("SELECT * FROM agent_turns") == [] and g.store.all("SELECT * FROM agent_messages") == []
    assert g.quotes.get(g.user["id"], q["id"])["turn_id"] is None
    err = refused(send, g, seen_price_id="x" * 201)
    assert (err.status, err.code) == (422, "invalid_input")


def test_a_price_adopted_while_the_page_was_open_is_refused_once_then_sent_at_the_new_price(governed):
    g = governed
    configure(g)
    fund(g)
    accept_terms(g.store, g.user["id"])
    before = shown_run(g)
    new_price = new_sale_price(g, input_uncached="5")
    err = refused(send, g, "Read my forms", **seen(before))
    assert (err.status, err.code) == (409, "billing_price_changed")
    nothing_stored(g)
    assert g.seen == []
    now_shown = shown_run(g)
    current = err.detail["current"]
    assert current == {"price_id": now_shown["price_id"], "route_version": now_shown["route_version"],
                       "maximum": now_shown["maximum"], "per_call_hold": now_shown["per_call_hold"],
                       "unit": asdict(factory.UNIT)}
    assert current["price_id"] == new_price != before["price_id"]
    assert int(current["maximum"]) > int(before["maximum"]), "页面此前显示的最高金额已不再适用"
    out = send(g, "Read my forms", **seen(now_shown))["output"]
    terms = json.loads(quotes_of(g)[0]["terms_json"])
    assert quotes_of(g)[0]["accepted_turn_id"] == out["turn_id"]
    assert terms["sale"]["price_id"] == new_price and str(terms["budget"]) == now_shown["maximum"], "按页面显示的新价格运行"


def test_the_price_refusal_names_the_maximum_this_person_would_get(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    hold = int(g.quotes.offer()["per_call_hold"])
    g.agent.caps.invoke("agent.settings.update", {"run_budget_cap": str(2 * hold)}, user_id=g.user["id"],
                        idempotency_key="cap-fixture")
    err = refused(send, g, seen_price_id="fixture-price-shown-earlier")
    assert err.code == "billing_price_changed"
    assert err.detail["current"]["maximum"] == str(2 * hold) == shown_run(g)["maximum"], "本人上限之下的最高金额"
    nothing_stored(g)


# ------------------------------------------------------------------ 16. codes a person can never meet (AD-2)
@pytest.mark.parametrize("too_large", [str(2**63), "9999999999999999999"])
def test_a_cap_above_the_ledgers_integer_range_is_refused_when_saved(paid, too_large):
    g = paid
    accept_terms(g.store, g.user["id"])
    err = refused(g.agent.caps.invoke, "agent.settings.update", {"run_budget_cap": too_large}, user_id=g.user["id"],
                  idempotency_key="cap-too-large")
    assert (err.status, err.code) == (422, "invalid_input")
    assert [f["loc"] for f in err.detail["fields"]] == [["run_budget_cap"]]
    assert g.agent.caps.invoke("agent.settings.get", {}, user_id=g.user["id"])["output"]["billing"]["run_budget_cap"] is None
    assert shown_run(g)["usable"] is True, "保存被拒，之后的发送不受影响"
    g.agent.caps.invoke("agent.settings.update", {"run_budget_cap": str(2**63 - 1)}, user_id=g.user["id"],
                        idempotency_key="cap-largest")
    assert send(g)["output"]["turn_id"], "最大可保存的上限即治理默认值"


def test_a_stored_cap_that_cannot_be_read_shows_an_unusable_bound_instead_of_no_bound(paid):
    g = paid
    accept_terms(g.store, g.user["id"])
    hold = int(g.quotes.offer()["per_call_hold"])
    # FIXTURE: a cap saved before the save-time range check existed.
    g.store.execute("INSERT INTO agent_settings (user_id, updated_at, run_budget_cap, run_budget_cap_unit) "
                    "VALUES (?, 1, '9999999999999999999', 'FIXTURE-GATEWAY')", (g.user["id"],))
    run = shown_run(g)
    assert run is not None, "状态仍给出本次回答的上限信息"
    assert (run["usable"], run["cap"], run["source"], run["maximum"]) == (False, None, "personal_cap", str(hold * 12))
    assert (run["price_id"], run["route_version"]) == ("fixture-sale", "fixture-route-v1")
    err = refused(send, g)
    assert (err.status, err.code) == (409, "billing_run_cap_invalid")
    nothing_stored(g)


def test_an_automatic_issue_for_anyone_but_the_person_is_an_internal_error_not_a_refusal(paid):
    from synwevo_core.billing import BillingError
    g = paid
    for issued in ({"actor_user_id": g.user["id"], "via": "flow"}, {"via": "ui"}, None):
        with pytest.raises(RuntimeError) as err, g.store.tx():
            g.quotes.issue_automatic(g.user["id"], g.conversation, text="fixture question", parent_id=None,
                                     regenerate_of=None, issued=issued)
        assert not isinstance(err.value, (BillingError, ServiceError)), "内部不变量，不是可以展示给本人的拒绝"
    assert quotes_of(g) == []


# ------------------------------------------------------------------ 17. the automatic authorization's expiry (AD-3)
def test_a_second_passing_between_the_automatic_issue_and_its_bind_does_not_refuse_the_send(paid, monkeypatch):
    """The ledger clock is whole seconds: issue and bind may read either side of a boundary."""
    g = paid
    accept_terms(g.store, g.user["id"])
    original = g.quotes.issue_automatic

    def issue_then_the_clock_moves(*args, **kwargs):
        quote_id = original(*args, **kwargs)
        g.ticks[0] += 1
        return quote_id
    monkeypatch.setattr(g.quotes, "issue_automatic", issue_then_the_clock_moves)
    out = send(g)["output"]
    row = quotes_of(g)[0]
    assert row["accepted_turn_id"] == out["turn_id"], "跨过整秒仍然绑定"
    deadline = json.loads(row["terms_json"])["deadline"]
    assert row["expires_at"] == max(STAMP + 1, min(STAMP + 300, deadline - g.model.send_window)) == STAMP + 300, "与旧报价同一公式"
    # A route close to its end: the authorization lapses one send window before the deadline.
    from dataclasses import replace
    g.store.execute("UPDATE agent_turns SET status='succeeded'")
    g.route_holder[0] = replace(g.route, valid_until=g.ticks[0] + 200)
    send(g, "fixture question 2")
    row = quotes_of(g)[-1]
    deadline = json.loads(row["terms_json"])["deadline"]
    assert deadline == row["created_at"] + 200 and row["accepted_turn_id"]
    assert row["expires_at"] == deadline - g.model.send_window < row["created_at"] + 300


# ------------------------------------------------------------------ 18. acceptance is never inferred, legacy path too (AD-4)
def accepted_an_earlier_version(store, uid):
    """FIXTURE: the person accepted the documents before their current versions took effect."""
    for document in DOCUMENTS:
        store.execute("INSERT INTO agent_terms_acceptances (user_id,document,version,accepted_at,context) VALUES (?,?,?,?,?)",
                      (uid, document, "2000-01-01", 1, "fixture:earlier-version"))


def test_a_legacy_authorization_sent_from_the_page_after_a_version_change_asks_for_acceptance_first(paid):
    g = paid
    uid = g.user["id"]
    accepted_an_earlier_version(g.store, uid)
    before = TermsAcceptances(g.store).history(uid)
    assert {d["id"] for d in TermsAcceptances(g.store).missing(uid)} == set(DOCUMENTS)
    q = g.issue()
    with g.store.tx():
        consent = g.quotes.consents.create(uid, g.issue()["id"], max_runs=3, expires_at=None, until_revoked=True)
    for authorization in ({"quote_id": q["id"]}, {"consent_id": consent["id"]}):
        err = refused(send, g, **authorization)
        assert (err.status, err.code) == (409, "terms_acceptance_required"), authorization
        assert {d["id"] for d in err.detail["documents"]} == set(DOCUMENTS)
        assert g.store.all("SELECT * FROM agent_turns") == [] and g.store.all("SELECT * FROM agent_messages") == []
        assert g.store.one("SELECT COUNT(*) AS n FROM agent_model_quotes WHERE accepted_turn_id IS NOT NULL")["n"] == 0
        assert len(quotes_of(g)) == 2, "持续授权没有签发新的运行授权"
        assert TermsAcceptances(g.store).history(uid) == before, "没有替本人记录新版本"
    assert g.seen == []
    # After the person accepts the current versions, the same legacy send is admitted and records nothing more.
    current = [{"id": d, "version": v} for d, (v, _) in DOCUMENTS.items()]
    g.agent.caps.invoke("agent.terms.accept", {"documents": current}, user_id=uid, idempotency_key="accept-current")
    accepted = TermsAcceptances(g.store).history(uid)
    out = send(g, quote_id=q["id"])["output"]
    assert g.quotes.get(uid, q["id"])["turn_id"] == out["turn_id"]
    assert TermsAcceptances(g.store).history(uid) == accepted


@pytest.mark.parametrize("via", ["flow", "automation"])
def test_other_callers_on_the_legacy_path_keep_recording_the_versions_at_the_paid_run(paid, via):
    g = paid
    uid = g.user["id"]
    accepted_an_earlier_version(g.store, uid)
    q = g.issue()
    out = send(g, via=via, quote_id=q["id"])["output"]
    added = [r for r in TermsAcceptances(g.store).history(uid) if r["context"] == "paid_run:" + out["turn_id"]]
    assert {(r["document"], r["version"]) for r in added} == {(d, v) for d, (v, _) in DOCUMENTS.items()}

