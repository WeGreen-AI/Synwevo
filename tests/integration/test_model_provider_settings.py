"""Governed route/key facts in isolated real Stores; no model or payment traffic."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3

import pytest

from synwevo_core import sealed
from synwevo_core.auth import Auth
from synwevo_core.billing import BillingError, Ledger, PriceSnapshot, Rate, Unit
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.provider_settings import CredentialChange, KEY_PURPOSE, ProviderSettings
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.store import Store

KEY = "fixture-platform-credential-not-a-real-key"


@pytest.fixture
def model_config(tmp_path):
    store = Store(tmp_path / "isolated-provider")
    user = Identity(store).create_user("provider-owner@example.test", None, status="active", with_personal_workspace=False)
    ticks = [2_000_000_000]
    ledger = Ledger(store, environment="test", namespace="provider-fixture", clock=lambda: ticks[0])
    sale = PriceSnapshot("fixture-sale", "sale", Unit("FIXTURECREDITS", 6), "deepseek", "fixture-route",
                         Rate(2), Rate(1), Rate(3), "fixture-published-terms", ticks[0])
    ledger.publish_price(sale)
    ledger.publish_price(replace(sale, price_id="fixture-cost", purpose="cost", unit=Unit("FIXTURECOST", 8)))
    costs = CostSettings(store, environment="test", clock=lambda: ticks[0])
    with store.tx():
        costs.append({"enabled": True, "unit": {"code": "FIXTURECOST", "scale": 8},
            "daily_budget": "1000", "weekly_budget": "1000", "monthly_budget": "1000", "unknown_budget": "1000",
            "max_inflight": 2, "max_unknown": 2}, expected_revision=0, actor_id=user["id"],
            reason="Isolated fixture budget", operation_id="fixture-cost-setup")
    settings = ProviderSettings(store, environment="test", clock=lambda: ticks[0])
    route = {"requested_model": "fixture-request", "confirmed_route": "fixture-route",
             "confirmation_ref": "fixture-provider-confirmation", "cost_basis_ref": "fixture-cost-evidence",
             "account_confirmation_ref": "fixture-account-binding", "sale_price_id": "fixture-sale",
             "cost_price_id": "fixture-cost", "input_token_ceiling": 1000, "output_token_ceiling": 32,
             "valid_until": ticks[0]+3600}
    return store, settings, user["id"], ticks, {"enabled": True, "route": route}


def publish(fixture, spec=None, credential=None, *, revision=0, operation=None):
    store, settings, actor, _, default = fixture
    with store.tx():
        return settings.append(spec if spec is not None else default,
            credential if credential is not None else {"action": "replace", "api_key": KEY},
            expected_revision=revision, actor_id=actor, reason="Isolated provider policy change",
            operation_id=operation or f"fixture-provider-{revision}")


def test_unconfigured_reader_does_not_enable_model_or_create_credentials(model_config):
    store, settings, *_ = model_config
    assert settings.read()["spec"] is None and settings.resolve() is None
    assert store.all("SELECT * FROM model_provider_versions") == []
    assert store.all("SELECT * FROM model_provider_credentials") == []
    assert store.all("SELECT * FROM billing_journal") == []


def test_public_metadata_never_contains_key_and_real_resolver_reads_current_revision(model_config):
    store, settings, _, _, spec = model_config
    plan = settings.preview(spec, {"action": "replace", "api_key": KEY}, expected_revision=0)
    assert KEY not in json.dumps(plan)
    assert store.all("SELECT * FROM model_provider_credentials") == []
    result = publish(model_config)
    assert KEY not in json.dumps(result) + json.dumps(settings.history())
    cipher = store.one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    assert KEY not in cipher and sealed.unseal(store, KEY_PURPOSE, cipher) == KEY
    resolved = settings.resolve()
    assert resolved.api_key == KEY and KEY not in repr(resolved)
    assert resolved.route["version"] == "deepseek:test:1"
    assert resolved.provider == "deepseek" and resolved.account_ref == "platform-deepseek"
    assert "account_confirmation_ref" not in resolved.route


def test_key_rotation_and_revocation_keep_account_and_history_without_old_secrets(model_config):
    store, settings, _, _, spec = model_config
    first = publish(model_config)
    rotated = publish(model_config, credential={"action": "replace", "api_key": KEY+"-rotation"}, revision=1)
    assert rotated["account_ref"] == first["account_ref"] and rotated["credential_revision"] == 2
    assert settings.resolve().api_key == KEY+"-rotation"
    revoked = publish(model_config, spec={**spec, "enabled": False}, credential={"action": "revoke"}, revision=2)
    assert revoked["credential_revision"] == 3 and not revoked["credential_configured"]
    assert settings.resolve() is None
    assert store.one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"] is None
    assert [x["revision"] for x in settings.history(limit=2)["items"]] == [3, 2]
    assert settings.history(limit=2)["next_before_revision"] == 2
    assert settings.history(before_revision=2)["items"] == [first]
    assert KEY not in json.dumps(settings.history())


def test_version_and_cipher_rollback_together_when_audit_or_operation_fails(model_config):
    store, settings, actor, _, spec = model_config
    with pytest.raises(RuntimeError, match="Store.tx"):
        settings.append(spec, {"action": "replace", "api_key": KEY}, expected_revision=0,
                        actor_id=actor, reason="Isolated no transaction", operation_id="fixture-invalid")
    with pytest.raises(RuntimeError, match="fixture audit failure"):
        with store.tx():
            publish(model_config)
            raise RuntimeError("fixture audit failure")
    assert settings.read()["revision"] == 0 and store.all("SELECT * FROM model_provider_credentials") == []
    publish(model_config)
    with pytest.raises(sqlite3.IntegrityError):
        publish(model_config, credential={"action": "replace", "api_key": KEY+"-discard"}, revision=1,
                operation="fixture-provider-0")
    assert settings.resolve().api_key == KEY and settings.read()["revision"] == 1


def test_root_rekey_includes_active_platform_secret(model_config):
    store, *_ = model_config
    publish(model_config)
    old, new = Auth(store).secret(), b"isolated-rekey-root-material-only"
    result = sealed.rekey(store, old, new)
    assert result["model_provider_credentials.secret_enc"] == 1
    token = store.one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    assert sealed.unseal_with(new, KEY_PURPOSE, token) == KEY
    assert KEY not in token


def test_two_processes_cannot_rotate_over_same_revision(model_config):
    store, settings, actor, ticks, spec = model_config
    publish(model_config)
    others = [ProviderSettings(Store(store.root), environment="test", clock=lambda: ticks[0]) for _ in range(2)]
    def change(i):
        try:
            with others[i].store.tx():
                others[i].append(spec, {"action": "replace", "api_key": KEY+str(i)}, expected_revision=1,
                    actor_id=actor, reason="Concurrent fixture rotation", operation_id=f"fixture-concurrent-{i}")
            return "published"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(change, range(2))) == ["model_revision_conflict", "published"]
    assert settings.read()["revision"] == 2
    assert settings.resolve().api_key in (KEY+"0", KEY+"1")


@pytest.mark.parametrize("changes,code", [
    ({"valid_until": 1}, "model_route_expired"),
    ({"valid_until": 253402300800}, "model_config_invalid"),
    ({"requested_model": "invalid/model"}, "model_config_invalid"),
    ({"confirmed_route": "unconfirmed"}, "model_price_mismatch"),
    ({"sale_price_id": "fixture-cost"}, "model_price_mismatch"),
    ({"cost_price_id": "missing"}, "model_price_unavailable"),
    ({"output_token_ceiling": True}, "model_config_invalid"),
    ({"input_token_ceiling": "100"}, "model_config_invalid"),
    ({"confirmation_ref": "bad\nreference"}, "model_config_invalid"),
])
def test_enabled_route_is_bound_to_current_confirmed_prices(model_config, changes, code):
    store, settings, _, _, spec = model_config
    with pytest.raises(ServiceError) as refused:
        settings.preview({**spec, "route": {**spec["route"], **changes}},
                         {"action": "replace", "api_key": KEY}, expected_revision=0)
    assert refused.value.code == code and KEY not in str(refused.value)
    assert store.all("SELECT * FROM model_provider_credentials") == []


def test_enablement_requires_credential_cost_policy_and_same_environment(model_config):
    store, settings, actor, ticks, spec = model_config
    with pytest.raises(ServiceError) as no_key:
        settings.preview(spec, {"action": "keep"}, expected_revision=0)
    assert no_key.value.code == "model_credential_required"
    cost = CostSettings(store, environment="test", clock=lambda: ticks[0])
    with store.tx():
        cost.append({**cost.read()["spec"], "enabled": False}, expected_revision=1, actor_id=actor,
                    reason="Isolated cost breaker", operation_id="fixture-disable-cost")
    with pytest.raises(ServiceError) as stopped:
        publish(model_config)
    assert stopped.value.code == "model_cost_policy_required"
    live = ProviderSettings(store, environment="live", clock=lambda: ticks[0])
    with pytest.raises(ServiceError) as no_live_price:
        live.preview(spec, {"action": "replace", "api_key": KEY}, expected_revision=0)
    assert no_live_price.value.code == "model_price_unavailable"
    assert live.resolve() is None
    # Emergency disable/revoke stays possible even after route evidence expires.
    publish(model_config, spec={**spec, "enabled": False})
    ticks[0] += 7200
    publish(model_config, spec={"enabled": False, "route": None}, credential={"action": "revoke"}, revision=1)
    assert settings.resolve() is None


@pytest.mark.parametrize("credential", [
    {"action": "replace", "api_key": "short"}, {"action": "replace", "api_key": KEY+"\n"},
    {"action": "keep", "api_key": KEY}, {"action": "revoke", "api_key": KEY},
    {"action": "replace", "api_key": {"not": "string"}},
])
def test_invalid_secret_never_leaks_in_domain_error(model_config, credential):
    store, settings, _, _, spec = model_config
    with pytest.raises(ServiceError) as refused:
        settings.preview(spec, credential, expected_revision=0)
    assert refused.value.code == "model_config_invalid" and KEY not in str(refused.value)
    assert store.all("SELECT * FROM model_provider_credentials") == []


def test_snapshot_is_immutable_and_reopen_keeps_cipher_and_version(model_config):
    store, settings, _, ticks, _ = model_config
    publish(model_config)
    for sql in ("DELETE FROM model_provider_versions", "UPDATE model_provider_versions SET reason='tampered'",
                "INSERT OR REPLACE INTO model_provider_versions SELECT * FROM model_provider_versions"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable_model_provider"):
            store.execute(sql)
    reopened = ProviderSettings(Store(store.root), environment="test", clock=lambda: ticks[0])
    assert reopened.read() == settings.read() and reopened.resolve().api_key == KEY
    store.execute("UPDATE model_provider_credentials SET secret_enc='corrupt-fixture'")
    with pytest.raises(BillingError, match="model_credential_unavailable"):
        reopened.resolve()
    # A broken key never prevents disabling a provider.
    publish(model_config, spec={"enabled": False, "route": None}, credential={"action": "revoke"}, revision=1)
    assert reopened.resolve() is None
