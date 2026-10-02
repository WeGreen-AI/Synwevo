"""Provider identity and credential rotation fences, with zero external traffic."""
from dataclasses import replace
import json

import pytest

from synwevo_core.billing import BillingError
from synwevo_suite.agent.gateway import MeteredTurnProvider
from synwevo_suite.agent.provider import AnthropicProvider, Credential, ProviderError

from .test_agent_metering import gateway


def test_identity_and_rotation_binding_are_explicit_and_secret_is_not_represented():
    secret = "fixture-identity-secret-not-real"
    credential = Credential("platform", secret, "fixture-model", provider="deepseek", revision="revision-1")
    assert credential.label == "deepseek:platform"
    assert secret not in repr(credential) and secret not in credential.binding
    assert credential.binding == replace(credential).binding
    for change in ({"api_key": secret+"-rotated"}, {"revision": "revision-2"},
                   {"provider": "anthropic"}, {"kind": "personal"}, {"model": "different"}):
        assert replace(credential, **change).binding != credential.binding


def test_deepseek_does_not_borrow_ambient_anthropic_or_personal_configuration(gateway, monkeypatch):
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", gateway.route.requested_model)
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "fixture-key-only-for-anthropic")
    provider = MeteredTurnProvider(gateway.model)
    assert provider.resolve_credential(gateway.store, gateway.context.actor_user_id) is None
    assert gateway.seen == [] and gateway.store.all("SELECT * FROM billing_calls") == []


@pytest.mark.parametrize("change", [{"provider": "anthropic"}, {"kind": "personal"}])
def test_wrong_provider_or_personal_key_cannot_reserve_or_dispatch(gateway, change):
    g = gateway
    with pytest.raises(BillingError, match="model_configuration_changed"):
        g.model.step(**{**g.args, "credential": replace(g.args["credential"], **change)})
    assert g.seen == [] and g.balance().held == 0 and g.balance().booked == 1000
    assert g.store.all("SELECT * FROM billing_calls") == []
    assert g.store.all("SELECT * FROM agent_metered_calls") == []


def test_anthropic_rejects_deepseek_before_constructing_any_client(monkeypatch):
    import anthropic
    def unexpected(*args, **kwargs):
        pytest.fail("wrong-provider credential reached SDK")
    monkeypatch.setattr(anthropic, "Anthropic", unexpected)
    with pytest.raises(ProviderError) as refused:
        AnthropicProvider().step(credential=Credential("platform", "fixture-key", "fixture-model", provider="deepseek"),
            system=[], tools=[], messages=[], effort="high", on_text=lambda _: None, should_stop=lambda: False)
    assert refused.value.code == "model_configuration_changed"
