"""Provider consent through real Capabilities/Store; all credentials are synthetic fixtures.

No provider request is made. The paid settings fixture supplies only credential resolution.
"""
import json
from types import SimpleNamespace

import pytest

from synwevo_core.errors import ServiceError
from synwevo_suite.agent.provider import Credential
from synwevo_suite.agent.tools import ToolContext, policy_allows


def invoke(c, name, payload=None, workspace=None, user="alice", key=None):
    return c.caps.invoke(name, payload or {}, user_id=c.people.uid(user),
                         workspace_id=workspace, idempotency_key=key)


def set_policy(c, processing="platform", providers=None, **kw):
    payload = {"processing": processing}
    if providers is not None:
        payload["providers"] = providers
    return invoke(c, "agent.workspace_policy.set", payload, c.people.a, **kw)


def listed(c, workspace):
    return next(w for w in invoke(c, "agent.workspace_policy.list")["output"]["workspaces"]
                if w["workspace_id"] == workspace)


@pytest.mark.parametrize("processing", ["platform", "any"])
def test_legacy_four_column_policy_does_not_authorize_deepseek(suite, processing):
    p = suite.people
    p.store.execute("INSERT INTO agent_workspace_policies VALUES (?,?,?,?)",
                    (p.a, processing, p.uid("alice"), 1))
    assert policy_allows(p.store, p.a, "platform")
    assert not policy_allows(p.store, p.a, "platform", provider="deepseek")
    assert listed(suite, p.a)["providers"] == ["anthropic"]
    assert listed(suite, p.a)["providers_explicit"] is False
    out = set_policy(suite, processing)["output"]
    assert out["providers"] == ["anthropic"]
    assert not policy_allows(p.store, p.a, "platform", provider="deepseek")


def test_personal_without_policy_follows_choice_but_organization_stays_closed(suite):
    p = suite.people
    own = p.ident.ensure_personal_workspace(p.uid("alice"))["id"]
    assert listed(suite, own)["providers"] == ["anthropic", "deepseek"]
    for provider in ("anthropic", "deepseek"):
        assert policy_allows(p.store, own, "personal", provider=provider)
        assert not policy_allows(p.store, p.a, "platform", provider=provider)
    assert not policy_allows(p.store, own, "personal", provider="unknown")


def test_explicit_authorization_and_revocation_update_existing_tool_context(suite):
    p = suite.people
    form = invoke(suite, "forms.form.create", {"title": "Fixture private content"}, p.a)["output"]
    operation = suite.caps.registry.get("forms.form.get")
    result = invoke(suite, operation.id, {"form_id": form["id"]}, p.a)["output"]
    ctx = ToolContext(suite.caps, p.users["alice"], None, "platform", provider="deepseek")

    def allowed():
        catalogue = ctx.catalogue()
        return next(w["ai_processing_allowed"] for app in catalogue["apps"] if app["app"] == "forms"
                    for w in app["workspaces"] if w["id"] == p.a)

    set_policy(suite)
    assert not allowed() and ctx.for_model(operation, p.a, result)["withheld"]
    set_policy(suite, providers=["deepseek"])
    assert allowed() and ctx.for_model(operation, p.a, result)["result"]["id"] == form["id"]
    assert not policy_allows(p.store, p.a, "platform", provider="anthropic")
    assert not policy_allows(p.store, p.a, "personal", provider="deepseek")
    set_policy(suite, "any", ["deepseek"])
    assert policy_allows(p.store, p.a, "personal", provider="deepseek")
    set_policy(suite, "any", [])
    assert not allowed() and ctx.for_model(operation, p.a, result)["withheld"]
    assert listed(suite, p.a)["providers"] == []


@pytest.mark.parametrize("providers", [["deepseek"], []])
def test_old_client_preserves_explicit_scope_even_when_reenabling_processing(suite, providers):
    set_policy(suite, "none", providers)
    out = set_policy(suite, "any")["output"]
    assert out["providers"] == providers
    assert listed(suite, suite.people.a)["providers"] == providers
    assert not policy_allows(suite.people.store, suite.people.a, "platform")


def test_processing_none_overrides_remembered_provider_consent(suite):
    set_policy(suite, "none", ["deepseek", "anthropic"])
    row = listed(suite, suite.people.a)
    assert row["providers"] == ["anthropic", "deepseek"] and row["providers_explicit"]
    for provider in row["providers"]:
        assert not policy_allows(suite.people.store, suite.people.a, "platform", provider=provider)


@pytest.mark.parametrize("providers", [["unknown"], ["deepseek", "deepseek"], None, "deepseek"])
def test_invalid_provider_ranges_are_rejected_without_mutation(suite, providers):
    with pytest.raises(ServiceError) as caught:
        invoke(suite, "agent.workspace_policy.set", {"processing": "any", "providers": providers}, suite.people.a)
    assert caught.value.code == "invalid_input"
    assert suite.people.store.one("SELECT * FROM agent_workspace_policies WHERE workspace_id=?", (suite.people.a,)) is None


@pytest.mark.parametrize("user", ["bob", "vera", "out"])
def test_only_workspace_owner_or_admin_can_grant_provider_consent(suite, user):
    set_policy(suite, providers=[])
    with pytest.raises(ServiceError) as caught:
        set_policy(suite, providers=["deepseek"], user=user)
    assert caught.value.status in (403, 404)
    assert listed(suite, suite.people.a)["providers"] == []


def test_workspace_admin_can_grant_provider_consent(suite):
    p = suite.people
    p.ident.add_member(p.a, p.uid("bob"), "admin")
    assert set_policy(suite, providers=["deepseek"], user="bob")["output"]["providers"] == ["deepseek"]


def test_policy_event_and_audit_share_transaction_and_idempotency(suite):
    p = suite.people
    first = set_policy(suite, providers=["deepseek"], key="fixture-policy-once")
    replay = set_policy(suite, providers=["deepseek"], key="fixture-policy-once")
    assert replay["replayed"] and replay["run_id"] == first["run_id"]
    events = p.store.all("SELECT * FROM app_events WHERE run_id=?", (first["run_id"],))
    assert len(events) == 1 and events[0]["type"] == "agent.workspace_policy.changed"
    assert events[0]["data"]["providers"] == ["deepseek"]
    assert p.store.one("SELECT * FROM audit_events WHERE action='agent.workspace_policy.set' AND result='ok'")
    # A real database failure after both policy writes must roll back policy, event and success audit.
    p.store.execute("CREATE TRIGGER fixture_policy_audit_failure BEFORE INSERT ON audit_events "
                    "WHEN NEW.action='agent.workspace_policy.set' AND NEW.result='ok' "
                    "BEGIN SELECT RAISE(ABORT, 'fixture audit failure'); END")
    with pytest.raises(ServiceError):
        set_policy(suite, "any", [])
    assert listed(suite, p.a)["processing"] == "platform"
    assert listed(suite, p.a)["providers"] == ["deepseek"]
    assert len(p.store.all("SELECT * FROM app_events WHERE type='agent.workspace_policy.changed'")) == 1
    assert len(p.store.all("SELECT * FROM audit_events WHERE action='agent.workspace_policy.set' AND result='ok'")) == 1


def paid_fixture(c):
    # Credential resolution only: no model or network transport in this settings fixture.
    c.api.state.agent.provider = SimpleNamespace(quotes=object(), name="deepseek", resolve_credential=lambda store, uid:
        Credential("platform", "fixture-platform-key-not-real", "fixture-model", provider="deepseek"))


@pytest.mark.parametrize("payload", [{"personal_key": "fixture-personal-key-not-real"}, {"prefer_personal_key": True}])
def test_paid_settings_refuse_personal_credentials(suite, payload):
    paid_fixture(suite)
    with pytest.raises(ServiceError) as caught:
        invoke(suite, "agent.settings.update", payload)
    assert caught.value.code == "model_personal_key_unsupported"
    assert suite.people.store.one("SELECT * FROM agent_settings WHERE user_id=?", (suite.people.uid("alice"),)) is None
    settings = invoke(suite, "agent.settings.get")["output"]
    assert settings["active"]["provider"] == "deepseek" and settings["personal_key_supported"] is False
    runs = suite.people.store.all("SELECT input,output FROM op_runs WHERE op='agent.settings.update'")
    assert "fixture-personal-key-not-real" not in json.dumps(runs)


def test_paid_settings_keep_existing_preference_and_allow_key_removal(suite, monkeypatch):
    # Explicit non-paid resolver preserves the legacy settings contract, without reading env keys.
    suite.api.state.agent.provider = SimpleNamespace(resolve_credential=lambda store, uid: None)
    settings = invoke(suite, "agent.settings.update", {"personal_key": "fixture-personal-key-not-real", "prefer_personal_key": True})["output"]
    assert settings["personal_key_supported"] is True
    paid_fixture(suite)
    settings = invoke(suite, "agent.settings.update", {"effort": "high"})["output"]
    assert settings["prefer_personal_key"] is True and settings["personal_key"]["set"]
    assert settings["personal_key_supported"] is False
    settings = invoke(suite, "agent.settings.update", {"clear_personal_key": True})["output"]
    assert not settings["personal_key"]["set"] and settings["prefer_personal_key"] is True
    assert invoke(suite, "agent.settings.update", {"prefer_personal_key": False})["output"]["prefer_personal_key"] is False
