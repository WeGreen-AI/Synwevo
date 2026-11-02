"""Isolated real Identity HTTP governance; all keys, prices and budgets are fixtures.

The default Identity factory mounts the routes with its own Store and audit. No
production data, external network, model request, payment or credential resolver is used.
"""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import hashlib
import json
import threading
import time
import types
import uuid

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from synwevo_core.billing.catalogue import PriceCatalogue
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.provider_settings import CredentialChange, ProviderSettings
from synwevo_core.platform_access import ROLES
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store
from synwevo_identity import admin_model_provider
from synwevo_identity.admin_operations import canonical

from .platform_fixture import end_platform_grants, holding_exactly, make_platform_admin
from .test_admin_operations import PW, post, world

BASE = "/api/admin/billing/model-provider"
ACTION = "admin.model_provider.publish"
REASON = "Fixture: confirm bounded platform model configuration"
KEY = "fixture-provider-credential-never-used-on-network"
REPLACEMENT = {"action": "replace", "api_key": KEY}
NO_CHARGING = {"length": False, "content_filter": False, "provider_interrupted": False, "client_cancelled": False}
DISABLED = {"enabled": False, "route": None, "incomplete_charging": NO_CHARGING}
COST = {"enabled": True, "unit": {"code": "FIX_COST", "scale": 6},
        "daily_budget": "1000", "weekly_budget": "1000", "monthly_budget": "1000",
        "unknown_budget": "10", "max_inflight": 1, "max_unknown": 1}


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")


def seed_prices(w, *, environment="test"):
    """Publish real immutable prices with synthetic units and terms, in the temporary DB."""
    catalogue = PriceCatalogue(w["store"], environment=environment)
    result = {}
    for purpose in ("sale", "cost"):
        with w["store"].tx():
            draft, _ = catalogue.create(spec={"name": "Fixture model price", "purpose": purpose,
                "provider": "deepseek", "model": "fixture-route", "unit": COST["unit"],
                "input_uncached": "0.30", "input_cached": "0.006", "output": "1.20",
                "terms_ref": "fixture-terms:v1", "effective_from": 1000, "previous_price_id": None},
                actor_id=w["root"]["id"], idempotency_key=uuid.uuid4().hex)
            result[purpose] = catalogue.publish(draft["id"], expected_revision=draft["revision"],
                actor_id=w["root"]["id"], operation_id="fixture_" + uuid.uuid4().hex, reason=REASON)["price_id"]
    return result


def seed_cost(w, *, enabled=True, environment="test"):
    settings = CostSettings(w["store"], environment=environment)
    with w["store"].tx():
        return settings.append({**COST, "enabled": enabled}, expected_revision=settings.read()["revision"],
            actor_id=w["root"]["id"], operation_id="fixture_" + uuid.uuid4().hex, reason=REASON)


def spec_for(prices):
    return {"enabled": True, "route": {"requested_model": "fixture-request", "confirmed_route": "fixture-route",
        "confirmation_ref": "fixture-route-confirmation", "cost_basis_ref": "fixture-cost-evidence",
        "account_confirmation_ref": "fixture-account-confirmation", "sale_price_id": prices["sale"],
        "cost_price_id": prices["cost"], "input_token_ceiling": 1000, "output_token_ceiling": 32,
        "valid_until": int(time.time()) + 3600, "reasoning_efforts": ["none", "low", "high", "max"]},
        "incomplete_charging": NO_CHARGING}


@pytest.fixture()
def provider_world(world):
    world["spec"] = spec_for(seed_prices(world))
    seed_cost(world)
    return world


def preview_request(w, *, revision=0, spec=None, credential=None):
    return {"expected_revision": revision, "spec": spec if spec is not None else w.get("spec", DISABLED),
            "credential": credential if credential is not None else REPLACEMENT}


def preview(w, *, client=None, **kwargs):
    response = post(client or w["admin"], BASE + "/preview", preview_request(w, **kwargs))
    assert response.status_code == 200, response.text
    return response.json()


def command(plan, credential=None):
    return {"expected_revision": plan["before"]["revision"], "spec": plan["after"],
            "credential": credential if credential is not None else REPLACEMENT,
            "preview_token": plan["preview_token"], "idempotency_key": uuid.uuid4().hex, "reason": REASON}


def publish(w, body, *, client=None, **kwargs):
    return post(client or w["admin"], BASE + "/publish", body, **kwargs)


def audits(w, result=None):
    return w["store"].all("SELECT * FROM audit_events WHERE action=?" +
        (" AND result=?" if result else "") + " ORDER BY seq", (ACTION, result) if result else (ACTION,))


def operations(w):
    return w["store"].all("SELECT * FROM admin_operations WHERE action=?", (ACTION,))


def decoded(plan):
    data = plan["preview_token"].split(".")[0]
    return json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))


def assert_no_public_secret(w, *values, secrets=(KEY,)):
    surfaces = json.dumps([*values, w["admin"].get(BASE).json(), w["admin"].get(BASE + "/history").json(),
        w["store"].all("SELECT * FROM model_provider_versions"), w["store"].all("SELECT * FROM audit_events"),
        w["store"].all("SELECT * FROM admin_operations")])
    for secret in secrets:
        assert secret not in surfaces
    assert "api_key" not in surfaces and "secret_enc" not in surfaces


def test_unconfigured_reads_do_not_seed_prices_costs_credentials_or_enable_routes(world):
    w = world
    response = w["admin"].get(BASE)
    assert response.status_code == 200, response.text
    assert response.json() == {"environment": "test", "provider": "deepseek", "account_ref": "platform-deepseek",
        "status": "unconfigured", "revision": 0, "spec": None, "credential_revision": 0,
        "credential_configured": False, "actor_id": None, "created_at": None, "reason": None, "operation_id": None,
        "health": {"closure": None, "last_models_check": None}}
    assert w["admin"].get(BASE + "/history").json() == {"items": [], "next_before_revision": None}
    plan = preview(w, credential={"action": "keep"})
    assert plan["after"] == DISABLED and not plan["credential_configured"]
    for table in ("billing_prices", "model_cost_policy_versions", "model_provider_versions",
                  "model_provider_credentials", "billing_journal", "admin_operations"):
        assert not w["store"].all(f"SELECT * FROM {table}")


def test_preview_and_publication_never_expose_keys_or_ciphertext_or_grant_business_access(provider_world, capsys, caplog):
    w = provider_world
    private = w["ident"].create_workspace("Fixture private workspace", owner_id=w["target"]["id"])
    untouched = ("billing_accounts", "billing_journal", "billing_attempts", "billing_runs", "billing_orders",
                 "memberships", "app_entitlements", "mail_outbox", "notifications", "billing_prices", "model_cost_policy_versions")
    before = {table: w["store"].all(f"SELECT * FROM {table}") for table in untouched}
    started = time.time()
    plan = preview(w)
    # A preview binds the version, not its health: a model-id check must not invalidate it.
    assert plan["before"] == {k: v for k, v in w["admin"].get(BASE).json().items() if k != "health"}
    assert plan["after"] == w["spec"] and plan["risk"] == "high" and plan["environment"] == "test"
    assert type(plan["expires_at"]) is float and started + 299 <= plan["expires_at"] <= time.time() + 301
    binding = decoded(plan)
    commitment = CredentialChange.model_validate(REPLACEMENT).commitment()
    assert binding["credential"] == plan["credential"] == commitment
    assert binding["before_digest"] == hashlib.sha256(canonical(plan["before"]).encode()).hexdigest()
    assert not audits(w) and not operations(w)
    assert not w["store"].all("SELECT * FROM model_provider_versions")
    assert not w["store"].all("SELECT * FROM model_provider_credentials")
    body = command(plan)
    response = publish(w, body)
    assert response.status_code == 200, response.text
    result, state = response.json(), response.json()["state"]
    assert result["status"] == "succeeded" and state["revision"] == state["credential_revision"] == 1
    assert state["credential_configured"] and state["status"] == "configured" and state["spec"] == w["spec"]
    assert state["actor_id"] == w["root"]["id"] and state["reason"] == REASON
    assert state["operation_id"] == result["operation_id"]
    assert {**state, "health": {"closure": None, "last_models_check": None}} == w["admin"].get(BASE).json()
    assert {k: v for k, v in state.items() if k != "status"} == ProviderSettings(w["store"], environment="test").read()
    cipher = w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    assert cipher and KEY not in cipher
    success = audits(w, "ok")
    assert len(success) == 1 and success[0]["workspace_id"] is None
    meta = json.loads(success[0]["meta"])
    recorded = {key: meta.pop(key) for key in ("before", "after", "changed")}  # G24, the unified record
    assert meta == {"billing_environment": "test", "provider": "deepseek", "origin": "http://testserver",
        "operation_id": result["operation_id"], "reason": REASON, "before_revision": 0, "revision": 1,
        "credential_action": "replace", "before_credential_revision": 0, "credential_revision": 1}
    route = w["spec"]["route"]
    assert recorded["after"] == {**route, "enabled": True, "incomplete_charging": NO_CHARGING,
                                 "credential_configured": True, "credential_revision": 1}
    assert recorded["before"] == {**{key: None for key in route}, "enabled": None, "incomplete_charging": None,
                                  "credential_configured": False, "credential_revision": 0}
    assert set(recorded["changed"]) == set(recorded["after"]), "the first version sets every field"
    # The request hash is committed over a credential digest, never over the raw request.
    expected_hash = hashlib.sha256(canonical({"action": ACTION, "target": "platform-deepseek", "provider": "deepseek",
        "origin": "http://testserver", "environment": "test", "revision": 0, "spec": body["spec"],
        "credential": commitment, "token": body["preview_token"], "reason": REASON}).encode()).hexdigest()
    assert operations(w)[0]["request_hash"] == expected_hash
    assert json.loads(operations(w)[0]["result_json"]) == result
    assert w["ident"].membership(private["id"], w["root"]["id"]) is None
    assert {table: w["store"].all(f"SELECT * FROM {table}") for table in untouched} == before
    captured = capsys.readouterr()
    assert_no_public_secret(w, plan, binding, result, captured.out, captured.err, caplog.text, secrets=(KEY, cipher))


def test_keep_rotate_revoke_and_history_record_only_nonsecret_versions(provider_world, capsys, caplog):
    w = provider_world
    credentials = (REPLACEMENT, {"action": "keep"}, {"action": "replace", "api_key": KEY + "-rotated"}, {"action": "revoke"})
    specs = (w["spec"], {**w["spec"], "enabled": False}, w["spec"], DISABLED)
    cipher = None
    for revision, (credential, spec) in enumerate(zip(credentials, specs)):
        plan = preview(w, revision=revision, spec=spec, credential=credential)
        response = publish(w, command(plan, credential))
        assert response.status_code == 200, response.text
        state = response.json()["state"]
        assert state["credential_revision"] == (1, 1, 2, 3)[revision]
        assert state["credential_configured"] == (revision != 3)
        assert state["account_ref"] == "platform-deepseek"
        current = w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
        if revision == 1:
            assert current == cipher
        elif revision == 2:
            assert current and current != cipher
        elif revision == 3:
            assert current is None
        cipher = current
        assert_no_public_secret(w, plan, decoded(plan), response.json(), secrets=(KEY, KEY + "-rotated"))
    first = w["admin"].get(BASE + "/history?limit=2").json()
    assert [v["revision"] for v in first["items"]] == [4, 3] and first["next_before_revision"] == 3
    last = w["admin"].get(BASE + "/history?limit=2&before_revision=3").json()
    assert [v["revision"] for v in last["items"]] == [2, 1] and last["next_before_revision"] is None
    assert [json.loads(a["meta"])["credential_action"] for a in audits(w, "ok")] == ["replace", "keep", "replace", "revoke"]
    captured = capsys.readouterr()
    assert KEY not in captured.out + captured.err + caplog.text


def test_same_key_with_changed_credential_conflicts_but_exact_replay_returns_original_after_expiry_and_rotation(provider_world, monkeypatch):
    w = provider_world
    plan = preview(w)
    body = command(plan)
    response = publish(w, body)
    assert response.status_code == 200, response.text
    first = response.json()
    rotated = {"action": "replace", "api_key": KEY + "-rotated"}
    assert publish(w, command(preview(w, revision=1, credential=rotated), rotated)).status_code == 200
    monkeypatch.setattr(admin_model_provider, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    replay = publish(w, body)
    assert replay.status_code == 200 and replay.json() == first
    for changed in ({"credential": rotated}, {"credential": {"action": "keep"}},
                    {"spec": DISABLED}, {"expected_revision": 1}, {"reason": REASON + " changed"},
                    {"preview_token": body["preview_token"] + "tamper"}):
        refused = publish(w, {**body, **changed})
        assert refused.status_code == 409 and refused.json()["error"] == "idempotency_conflict", refused.text
    assert w["admin"].get(BASE).json()["revision"] == 2 and len(audits(w, "ok")) == len(operations(w)) == 2


@pytest.mark.parametrize("change", ["actor", "origin", "signature", "expired", "environment", "action", "scope",
    "target", "provider", "revision", "spec", "before_digest", "credential", "credential_action",
    "credential_digest", "credential_revision", "credential_configured", "expiry_type"])
def test_signed_preview_binds_authority_spec_and_exact_credential(provider_world, monkeypatch, change):
    w = provider_world
    plan = preview(w)
    body, kwargs = command(plan), {}
    if change == "actor":
        actor = make_platform_admin(w["store"], w["ident"].create_user("other-provider@example.test", PW, status="active"))
        kwargs["client"] = w["login"](actor)
        w["reauth"](kwargs["client"])
    elif change == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif change == "signature":
        body["preview_token"] += "tamper"
    elif change == "expired":
        monkeypatch.setattr(admin_model_provider, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    elif change == "revision":
        body["expected_revision"] = 1
    elif change == "spec":
        body["spec"] = DISABLED
    elif change == "credential":
        body["credential"] = {"action": "replace", "api_key": KEY + "-changed"}
    elif change == "credential_action":
        body["credential"] = {"action": "revoke"}
    else:
        signer = admin_model_provider.ProviderOperations(w["store"], None, "test")
        data = decoded(plan)
        if change == "credential_digest":
            data["credential"]["digest"] = "0" * 64
        elif change == "expiry_type":
            data["expires_at"] = None
        else:
            data[change] = "different-fixture-binding"
        body["preview_token"] = signer._sign(data)
    response = publish(w, body, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == ("admin_preview_expired" if change == "expired" else "admin_preview_invalid")
    assert not w["store"].all("SELECT * FROM model_provider_versions")
    assert not w["store"].all("SELECT * FROM model_provider_credentials")
    assert not operations(w)
    assert_no_public_secret(w, response.json())


def test_enablement_requires_current_prices_cost_policy_and_credential(world):
    w = world
    spec = spec_for({"sale": "missing-fixture-sale", "cost": "missing-fixture-cost"})
    for credential, code in (({"action": "keep"}, "model_credential_required"), (REPLACEMENT, "model_price_unavailable")):
        r = post(w["admin"], BASE + "/preview", preview_request(w, spec=spec, credential=credential))
        assert r.status_code == 409 and r.json()["error"] == code, r.text
    spec = spec_for(seed_prices(w, environment="live"))
    r = post(w["admin"], BASE + "/preview", preview_request(w, spec=spec))
    assert r.status_code == 409 and r.json()["error"] == "model_price_unavailable"
    spec = spec_for(seed_prices(w))
    r = post(w["admin"], BASE + "/preview", preview_request(w, spec=spec))
    assert r.status_code == 409 and r.json()["error"] == "model_cost_policy_required"
    seed_cost(w)
    plan = preview(w, spec=spec)
    seed_cost(w, enabled=False)
    r = publish(w, command(plan))
    assert r.status_code == 409 and r.json()["error"] == "model_cost_policy_required"
    assert not w["store"].all("SELECT * FROM model_provider_versions")
    # Emergency disable/revoke does not require a usable route, prices, cost policy or key.
    assert publish(w, command(preview(w, spec=DISABLED, credential={"action": "revoke"}), {"action": "revoke"})).status_code == 200


@pytest.mark.parametrize("change,code", [({"valid_until": 1}, "model_route_expired"),
    ({"confirmed_route": "fixture-different-route"}, "model_price_mismatch"),
    ({"cost_price_id": "missing-fixture-cost"}, "model_price_unavailable")])
def test_route_semantics_reject_expired_or_mismatched_prices(provider_world, change, code):
    w = provider_world
    spec = {**w["spec"], "route": {**w["spec"]["route"], **change}}
    r = post(w["admin"], BASE + "/preview", preview_request(w, spec=spec))
    assert r.status_code == 409 and r.json()["error"] == code
    assert_no_public_secret(w, r.json())


def test_the_incomplete_call_charging_policy_is_published_versioned_and_audited(provider_world):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    charging = {**NO_CHARGING, "length": True, "client_cancelled": True}
    spec = {**w["spec"], "incomplete_charging": charging}
    plan = preview(w, revision=1, spec=spec, credential={"action": "keep"})
    assert plan["before"]["spec"]["incomplete_charging"] == NO_CHARGING and plan["after"]["incomplete_charging"] == charging
    r = publish(w, command(plan, {"action": "keep"}))
    assert r.status_code == 200, r.text
    assert r.json()["state"]["revision"] == 2 and r.json()["state"]["spec"]["incomplete_charging"] == charging
    assert [json.loads(a["meta"])["revision"] for a in audits(w, "ok")] == [1, 2]
    history = w["admin"].get(BASE + "/history").json()["items"]
    assert [item["spec"]["incomplete_charging"]["length"] for item in history] == [True, False]
    for invalid in ({**charging, "everything": True}, {**charging, "length": "yes"}, {"length": 1}):
        r = post(w["admin"], BASE + "/preview", preview_request(w, revision=2, spec={**spec, "incomplete_charging": invalid},
                                                                credential={"action": "keep"}))
        assert r.status_code == 400 and r.json()["error"] == "model_config_invalid"


def test_stale_revision_and_keep_no_change_require_new_input(provider_world):
    w = provider_world
    stale = command(preview(w))
    assert publish(w, command(preview(w))).status_code == 200
    r = publish(w, stale)
    assert r.status_code == 409 and r.json()["error"] == "model_revision_conflict"
    r = post(w["admin"], BASE + "/preview", preview_request(w, revision=1, credential={"action": "keep"}))
    assert r.status_code == 409 and r.json()["error"] == "model_no_change"
    assert len(operations(w)) == len(audits(w, "ok")) == 1


def test_route_evidence_expiring_after_preview_is_rechecked_before_publication(provider_world, monkeypatch):
    from synwevo_core.billing import provider_settings
    w = provider_world
    valid_until = int(time.time()) + 60
    spec = {**w["spec"], "route": {**w["spec"]["route"], "valid_until": valid_until}}
    plan = preview(w, spec=spec)
    assert plan["expires_at"] > valid_until + 1
    monkeypatch.setattr(provider_settings, "time", types.SimpleNamespace(time=lambda: valid_until + 1))
    r = publish(w, command(plan))
    assert r.status_code == 409 and r.json()["error"] == "model_route_expired"
    assert not w["store"].all("SELECT * FROM model_provider_credentials")
    assert not operations(w)
    # An expired route must not block the emergency disable/revoke path.
    revoke = {"action": "revoke"}
    assert publish(w, command(preview(w, spec=DISABLED, credential=revoke), revoke)).status_code == 200


def test_permissions_csrf_reauthentication_and_revoked_replay(provider_world):
    w = provider_world
    w["ident"].create_workspace("Fixture owner without platform authority", owner_id=w["target"]["id"])
    with TestClient(w["api"], raise_server_exceptions=False) as anonymous:
        assert anonymous.get(BASE).status_code == 401
    for suffix in ("", "/history"):
        assert w["ordinary"].get(BASE + suffix).status_code == 404
    for suffix, body in (("/preview", preview_request(w)), ("/publish", command(preview(w)))):
        assert post(w["ordinary"], BASE + suffix, body).status_code == 404
    viewer = make_platform_admin(w["store"], w["ident"].create_user("provider-viewer@example.test", PW, status="active"), "auditor")
    client = w["login"](viewer)
    assert client.get(BASE).status_code == client.get(BASE + "/history").status_code == 200
    for refused in (post(client, BASE + "/preview", preview_request(w)), publish(w, command(preview(w)), client=client)):
        assert refused.status_code == 403 and refused.json()["error"] == "platform_permission_denied", refused.text
        assert refused.json()["detail"]["permission"] == "platform.models.credential", "authz-6: the credential is not config.manage"
    manager = w["login"](w["root"])
    body = command(preview(w, client=manager))
    assert publish(w, body, client=manager).json()["error"] == "reauth_required"
    w["reauth"](manager)
    assert manager.post(BASE + "/preview", json=preview_request(w)).status_code == 403
    assert manager.post(BASE + "/publish", json=body).status_code == 403
    assert publish(w, body, client=manager).status_code == 200
    end_platform_grants(w["store"], w["root"])
    assert publish(w, body, client=manager).status_code == 404


def test_the_sealed_credential_reaches_no_reader_and_only_its_permission_changes_it(provider_world, monkeypatch):
    """authz-6. A key a holder of `platform.models.credential` published is in no answer a holder
    of the configuration view and the pricing permission (FIXTURE: `holding_exactly`) can read: the
    status and the history say whether one is configured and its revision. That holder cannot
    replace, keep or revoke it, with a key in the request or not, and the attempt changes nothing."""
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    sealed = w["store"].one("SELECT secret_enc FROM model_provider_credentials WHERE environment='test'")["secret_enc"]
    pricing = w["login"](holding_exactly(monkeypatch, w["store"], w["ident"].create_user("provider-pricing@example.test", PW, status="active"),
                                         {"platform.config.view", "platform.models.pricing"}))
    status, history = pricing.get(BASE), pricing.get(BASE + "/history")
    assert status.status_code == history.status_code == 200
    assert status.json()["credential_configured"] is True and status.json()["credential_revision"] == 1
    for answer in (status, history):
        assert KEY not in answer.text and sealed not in answer.text and "api_key" not in answer.text, answer.text
    versions = w["store"].one("SELECT COUNT(*) AS n FROM model_provider_versions")["n"]
    for credential in (REPLACEMENT, {"action": "keep", "api_key": None}, {"action": "revoke", "api_key": None}):
        refused = post(pricing, BASE + "/preview", preview_request(w, revision=status.json()["revision"], credential=credential))
        assert refused.status_code == 403 and refused.json()["detail"]["permission"] == "platform.models.credential", refused.text
    assert w["store"].one("SELECT COUNT(*) AS n FROM model_provider_versions")["n"] == versions


# ---------------------------------------------------------------- key custody and pricing (authz-6, D20)
# One publication route serves two permissions, and asks for the one of what the request changes:
# the sale and cost prices a route uses, their cost evidence and the charging of incomplete calls
# are what payers pay and the platform spends, so they are `platform.models.pricing`'s; the key and
# the rest of the version are `platform.models.credential`'s.
MODEL_CREDENTIAL, MODEL_PRICING = "platform.models.credential", "platform.models.pricing"
CUSTODIAN = frozenset({"platform.config.view", MODEL_CREDENTIAL})
PRICER = frozenset({"platform.config.view", MODEL_PRICING})
KEEP = {"action": "keep", "api_key": None}
CHARGED = {**NO_CHARGING, "length": True, "client_cancelled": True}
PRICE_CHANGES = ("sale_price", "cost_price", "both_prices", "cost_evidence", "charging")
CUSTODY_CHANGES = ("disable", "withdraw", "requested_model", "route_evidence", "account_evidence", "ceiling", "validity", "efforts")


def holder(w, monkeypatch, permissions, name, *, carrier="support"):
    """FIXTURE (`holding_exactly`): no built-in role holds one model id without the whole catalogue.
    Re-authenticated, so what a route answers below is about the permission and not the freshness."""
    user = holding_exactly(monkeypatch, w["store"], w["ident"].create_user(f"{name}@example.test", PW, status="active"),
                           permissions, carrier=carrier)
    client = w["login"](user)
    w["reauth"](client)
    return user, client


def with_route(spec, **changes):
    return {**spec, "route": {**spec["route"], **changes}}


def priced(spec, other, change):
    return {"sale_price": with_route(spec, sale_price_id=other["sale"]),
            "cost_price": with_route(spec, cost_price_id=other["cost"]),
            "both_prices": with_route(spec, sale_price_id=other["sale"], cost_price_id=other["cost"]),
            "cost_evidence": with_route(spec, cost_basis_ref="fixture-other-cost-evidence"),
            "charging": {**spec, "incomplete_charging": CHARGED}}[change]


def custody(spec, change):
    return {"disable": {**spec, "enabled": False},
            "withdraw": {**spec, "enabled": False, "route": None},
            "requested_model": with_route(spec, requested_model="fixture-other-request"),
            "route_evidence": with_route(spec, confirmation_ref="fixture-other-confirmation"),
            "account_evidence": with_route(spec, account_confirmation_ref="fixture-other-account"),
            "ceiling": with_route(spec, output_token_ceiling=16),
            "validity": with_route(spec, valid_until=spec["route"]["valid_until"] + 60),
            "efforts": with_route(spec, reasoning_efforts=["none"])}[change]


def current(w):
    return ProviderSettings(w["store"], environment="test").read()


def charged_route(w):
    """The route as the model gateway charges from it (the Agent's quote reads the same object)."""
    return ProviderSettings(w["store"], environment="test").resolve().route


def publishes(w, client, *, spec, credential=KEEP):
    plan = post(client, BASE + "/preview", preview_request(w, revision=current(w)["revision"], spec=spec, credential=credential))
    assert plan.status_code == 200, plan.text
    done = publish(w, command(plan.json(), credential), client=client)
    assert done.status_code == 200, done.text
    return done.json()


def refused_by_name(w, client, permission, *, spec, credential=KEEP):
    """Refused at the preview and at the publication (before any token is read); nothing changes."""
    before = current(w)
    request = preview_request(w, revision=before["revision"], spec=spec, credential=credential)
    command_ = {**request, "preview_token": "not-confirmed", "idempotency_key": uuid.uuid4().hex, "reason": REASON}
    for response in (post(client, BASE + "/preview", request), publish(w, command_, client=client)):
        assert response.status_code == 403 and response.json()["error"] == "platform_permission_denied", response.text
        assert response.json()["detail"]["permission"] == permission, response.text
    assert current(w) == before


@pytest.mark.parametrize("change", [*PRICE_CHANGES, "while_disabled", "unpublished_price"])
def test_a_key_custodian_does_not_decide_what_payers_are_charged(provider_world, monkeypatch, change):
    """authz-6 review: the published price reaches payers through the provider version, so holding
    the key must not be enough to repoint the route at another price or to charge incomplete calls."""
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    route = charged_route(w)
    other = seed_prices(w)
    user, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian")
    spec = ({**with_route(w["spec"], sale_price_id=other["sale"]), "enabled": False} if change == "while_disabled"
            else with_route(w["spec"], sale_price_id="missing-fixture-sale") if change == "unpublished_price"
            else priced(w["spec"], other, change))
    # Authority first: an id that does not exist is the same refusal, not a statement about the id.
    refused_by_name(w, custodian, MODEL_PRICING, spec=spec)
    assert charged_route(w) == route
    denied = w["store"].all("SELECT object_id FROM audit_events WHERE action='admin.access.denied' AND actor_id=?", (user["id"],))
    assert [row["object_id"] for row in denied] == [MODEL_PRICING], "audited once per missing id"


@pytest.mark.parametrize("change", CUSTODY_CHANGES)
def test_a_key_custodian_keeps_the_route(provider_world, monkeypatch, change):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian")
    state = publishes(w, custodian, spec=custody(w["spec"], change))["state"]
    assert state["revision"] == 2 and state["spec"]["incomplete_charging"] == NO_CHARGING
    if state["spec"]["route"]:
        assert state["spec"]["route"]["sale_price_id"] == w["spec"]["route"]["sale_price_id"]
        assert state["spec"]["route"]["cost_price_id"] == w["spec"]["route"]["cost_price_id"]


def test_a_key_custodian_sets_replaces_and_revokes_the_key_with_the_prices_as_they_are(provider_world, monkeypatch):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    route = charged_route(w)
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian")
    rotated = {"action": "replace", "api_key": KEY + "-rotated"}
    assert publishes(w, custodian, spec=w["spec"], credential=rotated)["state"]["credential_revision"] == 2
    assert {k: v for k, v in charged_route(w).items() if k != "version"} == {k: v for k, v in route.items() if k != "version"}
    state = publishes(w, custodian, spec={**w["spec"], "enabled": False}, credential={"action": "revoke", "api_key": None})["state"]
    assert state["credential_revision"] == 3 and state["credential_configured"] is False


@pytest.mark.parametrize("change", PRICE_CHANGES)
def test_a_pricing_holder_puts_published_prices_into_effect_and_leaves_the_key_alone(provider_world, monkeypatch, change):
    """authz-6 review: a price the pricing holder published reached no route and adopting it was
    refused, while the markup page told them to adopt it. It is their decision, on the same route."""
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    sealed = w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    other = seed_prices(w)
    user, pricer = holder(w, monkeypatch, PRICER, "pricer")
    spec = priced(w["spec"], other, change)
    state = publishes(w, pricer, spec=spec)["state"]
    assert state["revision"] == 2 and state["actor_id"] == user["id"] and state["spec"] == spec
    assert state["credential_revision"] == 1 and state["credential_configured"] is True
    assert w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"] == sealed
    route = charged_route(w)
    assert route["version"] == "deepseek:test:2", "quotes and consents bind the version: the change reaches them"
    assert route["sale_price_id"] == spec["route"]["sale_price_id"] and route["cost_price_id"] == spec["route"]["cost_price_id"]
    assert route["charge_incomplete"] == (["length", "client_cancelled"] if change == "charging" else [])


@pytest.mark.parametrize("change", CUSTODY_CHANGES)
def test_a_pricing_holder_does_not_touch_the_route(provider_world, monkeypatch, change):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    _, pricer = holder(w, monkeypatch, PRICER, "pricer")
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=custody(w["spec"], change))


@pytest.mark.parametrize("credential", [REPLACEMENT, {"action": "revoke", "api_key": None}], ids=["replace", "revoke"])
def test_a_change_of_both_kinds_needs_both_permissions(provider_world, monkeypatch, credential):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    other = seed_prices(w)
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian", carrier="support")
    _, pricer = holder(w, monkeypatch, PRICER, "pricer", carrier="auditor")
    _, both = holder(w, monkeypatch, CUSTODIAN | PRICER, "both", carrier="finance")
    mixed = with_route(w["spec"], sale_price_id=other["sale"], output_token_ceiling=16)
    refused_by_name(w, custodian, MODEL_PRICING, spec=mixed)
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=mixed)
    # the key travels with a price: the pricing holder's own change does not carry a key
    adoption = with_route(w["spec"], sale_price_id=other["sale"])
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=adoption, credential=credential)
    refused_by_name(w, custodian, MODEL_PRICING, spec=adoption, credential=credential)
    assert publishes(w, both, spec=mixed)["state"]["revision"] == 2


def test_the_first_route_and_a_restored_one_need_both_permissions(provider_world, monkeypatch):
    """A route names its prices, so creating one sets them. Withdrawing a route sets nothing, and
    is no way around the rule: putting one back is a new adoption, even of the same prices."""
    w = provider_world
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian", carrier="support")
    _, pricer = holder(w, monkeypatch, PRICER, "pricer", carrier="auditor")
    _, both = holder(w, monkeypatch, CUSTODIAN | PRICER, "both", carrier="finance")
    refused_by_name(w, custodian, MODEL_PRICING, spec=w["spec"], credential=REPLACEMENT)
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=w["spec"], credential=REPLACEMENT)
    assert current(w)["revision"] == 0
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=DISABLED, credential=REPLACEMENT)
    assert publishes(w, custodian, spec=DISABLED, credential=REPLACEMENT)["state"]["revision"] == 1
    assert publishes(w, both, spec=w["spec"])["state"]["revision"] == 2
    assert publishes(w, custodian, spec=DISABLED)["state"]["revision"] == 3
    other = seed_prices(w)
    for spec in (w["spec"], with_route(w["spec"], sale_price_id=other["sale"])):
        refused_by_name(w, custodian, MODEL_PRICING, spec=spec)
        refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=spec)
    assert publishes(w, both, spec=w["spec"])["state"]["revision"] == 4
    # the charging of incomplete calls stays the pricing holder's while no route exists
    assert publishes(w, custodian, spec=DISABLED)["state"]["revision"] == 5
    refused_by_name(w, custodian, MODEL_PRICING, spec={**DISABLED, "incomplete_charging": CHARGED})
    assert publishes(w, pricer, spec={**DISABLED, "incomplete_charging": CHARGED})["state"]["revision"] == 6
    # and a withdrawal with the charging as it is stays the custodian's
    assert publishes(w, both, spec=w["spec"])["state"]["spec"]["incomplete_charging"] == NO_CHARGING
    assert publishes(w, custodian, spec=DISABLED)["state"]["revision"] == 8


def test_reopening_a_closed_route_is_the_custodians_whatever_else_the_publication_changes(provider_world, monkeypatch):
    """Any publication reopens a route an upstream 402 or a missing model id closed. The alert goes
    to the holders of the credential permission, who can see to the cause; a price does not reopen it."""
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    settings = ProviderSettings(w["store"], environment="test")
    settings.record_health(state="model_missing", requested_model="fixture-request", reason_code="model_not_listed")
    assert settings.closure() is not None
    other = seed_prices(w)
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian", carrier="support")
    _, pricer = holder(w, monkeypatch, PRICER, "pricer", carrier="auditor")
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=with_route(w["spec"], sale_price_id=other["sale"]))
    refused_by_name(w, pricer, MODEL_CREDENTIAL, spec=w["spec"])
    assert settings.closure() is not None
    assert publishes(w, custodian, spec=w["spec"])["state"]["revision"] == 2
    assert settings.closure() is None


def test_a_publication_records_what_it_changed_and_an_adoption_is_on_the_price_record(provider_world, monkeypatch):
    """authz-6 review: the row named no price or charging change, and nothing appeared under
    admin.model_price.*: the version a payer is charged by was recoverable from the history only."""
    w = provider_world
    adoptions = lambda: w["store"].all("SELECT * FROM audit_events WHERE action='admin.model_price.adopt' ORDER BY seq")  # noqa: E731
    sale, cost = w["spec"]["route"]["sale_price_id"], w["spec"]["route"]["cost_price_id"]
    assert publish(w, command(preview(w))).status_code == 200
    meta = json.loads(audits(w, "ok")[-1]["meta"])
    assert meta["before"]["sale_price_id"] is None and meta["after"]["sale_price_id"] == sale
    assert meta["after"]["cost_price_id"] == cost and meta["after"]["credential_configured"] is True
    assert {"sale_price_id", "cost_price_id", "enabled", "credential_revision"} <= set(meta["changed"])
    first = adoptions()
    assert len(first) == 1 and first[0]["actor_id"] == w["root"]["id"] and first[0]["object_kind"] == "model_price"
    assert first[0]["object_id"] == sale
    # custody names its own change and adds nothing to the price record
    user, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian", carrier="support")
    publishes(w, custodian, spec=with_route(w["spec"], output_token_ceiling=16))
    meta = json.loads(audits(w, "ok")[-1]["meta"])
    assert meta["changed"] == ["output_token_ceiling"] and (meta["before"]["output_token_ceiling"], meta["after"]["output_token_ceiling"]) == (32, 16)
    publishes(w, custodian, spec=with_route(w["spec"], output_token_ceiling=16), credential={"action": "replace", "api_key": KEY + "-rotated"})
    meta = json.loads(audits(w, "ok")[-1]["meta"])
    assert meta["changed"] == ["credential_revision"] and KEY not in audits(w, "ok")[-1]["meta"]
    assert len(adoptions()) == 1
    # a price and a charging change name both, in the publication's row and on the price record
    other = seed_prices(w)
    user, pricer = holder(w, monkeypatch, PRICER, "pricer", carrier="auditor")
    spec = {**with_route(w["spec"], output_token_ceiling=16, sale_price_id=other["sale"]), "incomplete_charging": CHARGED}
    operation = publishes(w, pricer, spec=spec)["operation_id"]
    row = audits(w, "ok")[-1]
    meta = json.loads(row["meta"])
    assert row["actor_id"] == user["id"] and meta["changed"] == ["incomplete_charging", "sale_price_id"]
    assert meta["before"]["sale_price_id"] == sale and meta["after"]["sale_price_id"] == other["sale"]
    assert meta["before"]["incomplete_charging"] == NO_CHARGING and meta["after"]["incomplete_charging"] == CHARGED
    adopted = adoptions()[-1]
    assert adopted["actor_id"] == user["id"] and adopted["object_id"] == other["sale"] and adopted["result"] == "ok"
    meta = json.loads(adopted["meta"])
    assert meta["operation_id"] == operation and meta["reason"] == REASON and meta["revision"] == 4 and meta["billing_environment"] == "test"
    assert meta["changed"] == ["incomplete_charging", "sale_price_id"]
    assert (meta["before"]["sale_price_id"], meta["after"]["sale_price_id"]) == (sale, other["sale"])
    assert meta["after"]["cost_price_id"] == cost
    assert_no_public_secret(w)


def test_an_adoption_record_that_cannot_be_written_rolls_the_publication_back(provider_world, monkeypatch):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    other = seed_prices(w)
    plan = preview(w, revision=1, spec=with_route(w["spec"], sale_price_id=other["sale"]), credential=KEEP)
    original = Store.execute

    def fail(store, sql, params=()):
        if sql.startswith("INSERT INTO audit_events") and "admin.model_price.adopt" in params:
            raise OSError("fixture audit failure")
        return original(store, sql, params)

    monkeypatch.setattr(Store, "execute", fail)
    r = publish(w, command(plan, KEEP))
    assert r.status_code == 503 and r.json()["error"] == "model_config_unavailable", r.text
    assert current(w)["revision"] == 1 and len(operations(w)) == 1 and len(audits(w, "ok")) == 1


@pytest.mark.parametrize("when", ["before_the_writer_lock", "under_the_writer_lock"])
def test_losing_the_pricing_permission_after_the_preview_refuses_the_publication(provider_world, monkeypatch, when):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    other = seed_prices(w)
    _, both = holder(w, monkeypatch, CUSTODIAN | PRICER, "both")
    plan = post(both, BASE + "/preview", preview_request(w, revision=1, spec=with_route(w["spec"], sale_price_id=other["sale"]), credential=KEEP))
    assert plan.status_code == 200, plan.text
    if when == "before_the_writer_lock":
        monkeypatch.setitem(ROLES, "support", CUSTODIAN)
    else:
        original, calls = ProviderSettings.change_parts, []

        def drop(settings, *args, **kwargs):
            calls.append(1)
            if len(calls) == 2:  # the first is the admission, the second reads under the lock
                monkeypatch.setitem(ROLES, "support", CUSTODIAN)
            return original(settings, *args, **kwargs)

        monkeypatch.setattr(ProviderSettings, "change_parts", drop)
    r = publish(w, command(plan.json(), KEEP), client=both)
    assert r.status_code == 403 and r.json()["detail"]["permission"] == MODEL_PRICING, r.text
    assert current(w)["revision"] == 1 and len(operations(w)) == 1


def test_a_preview_of_a_custody_change_does_not_carry_a_price_to_the_publication(provider_world, monkeypatch):
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    other = seed_prices(w)
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian")
    plan = post(custodian, BASE + "/preview", preview_request(w, revision=1, spec=with_route(w["spec"], output_token_ceiling=16), credential=KEEP))
    assert plan.status_code == 200, plan.text
    body = {**command(plan.json(), KEEP), "spec": with_route(w["spec"], output_token_ceiling=16, sale_price_id=other["sale"])}
    r = publish(w, body, client=custodian)
    assert r.status_code == 403 and r.json()["detail"]["permission"] == MODEL_PRICING, r.text
    assert current(w)["revision"] == 1


def test_a_stale_page_is_told_the_version_changed_and_a_replay_answers_the_original(provider_world, monkeypatch):
    """Against a version that has moved on the request changes nothing yet, so the answer is about
    the version, not a permission; the same key returns the durable result, whatever came after."""
    w = provider_world
    assert publish(w, command(preview(w))).status_code == 200
    other = seed_prices(w)
    _, custodian = holder(w, monkeypatch, CUSTODIAN, "custodian", carrier="support")
    _, pricer = holder(w, monkeypatch, PRICER, "pricer", carrier="auditor")
    plan = post(pricer, BASE + "/preview", preview_request(w, revision=1, spec=with_route(w["spec"], sale_price_id=other["sale"]), credential=KEEP))
    body = command(plan.json(), KEEP)
    first = publish(w, body, client=pricer)
    assert first.status_code == 200, first.text
    # the custodian's page still shows version 1, with the old sale price
    stale = post(custodian, BASE + "/preview", preview_request(w, revision=1, spec=with_route(w["spec"], output_token_ceiling=16), credential=KEEP))
    assert stale.status_code == 409 and stale.json()["error"] == "model_revision_conflict", stale.text
    # the pricing holder's retry of the same request is that request, even after the next version
    publishes(w, custodian, spec=with_route(body["spec"], output_token_ceiling=16))
    replay = publish(w, body, client=pricer)
    assert replay.status_code == 200 and replay.json() == first.json()
    assert current(w)["revision"] == 3


@pytest.mark.parametrize("withdrawal", ["permission", "session", "freshness", "account", "step_up"])
@pytest.mark.parametrize("replay", [False, True])
def test_publish_rechecks_current_authority_under_writer_lock(provider_world, monkeypatch, withdrawal, replay):
    w = provider_world
    body = command(preview(w))
    if replay:
        assert publish(w, body).status_code == 200
    original, happened = admin_model_provider.require_platform, False

    def withdraw(request, *args, **kwargs):
        nonlocal happened
        actor = original(request, *args, **kwargs)
        if request.url.path.endswith("/publish") and not happened:
            happened = True
            separate = Store(w["store"].root)
            if withdrawal == "permission":
                end_platform_grants(separate, w["root"])
            elif withdrawal == "account":
                separate.execute("UPDATE users SET status='suspended' WHERE id=?", (w["root"]["id"],))
            else:
                session = Sessions(separate).load(w["admin"].cookies.get(COOKIE_NAME), touch=False)
                if withdrawal == "session":
                    Sessions(separate).revoke(session["id"], "fixture_logout")
                elif withdrawal == "freshness":
                    separate.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE id=?", (session["id"],))
                else:
                    separate.execute("UPDATE sessions SET amr='email' WHERE id=?", (session["id"],))
        return actor

    monkeypatch.setattr(admin_model_provider, "require_platform", withdraw)
    r = publish(w, body)
    assert r.status_code == {"permission": 404, "session": 401, "freshness": 403, "account": 401, "step_up": 403}[withdrawal], r.text
    assert len(w["store"].all("SELECT * FROM model_provider_versions")) == int(replay)
    assert len(w["store"].all("SELECT * FROM model_provider_credentials")) == int(replay)
    assert len(audits(w, "ok")) == len(operations(w)) == int(replay)


@pytest.mark.parametrize("surface", ["read", "history", "preview"])
def test_read_and_preview_recheck_authority_after_resolving_state(provider_world, monkeypatch, surface):
    w = provider_world
    original = getattr(ProviderSettings, surface)

    def revoke(settings, *args, **kwargs):
        value = original(settings, *args, **kwargs)
        settings.store.execute("UPDATE sessions SET revoked_at=1 WHERE user_id=?", (w["root"]["id"],))
        return value

    monkeypatch.setattr(ProviderSettings, surface, revoke)
    r = (post(w["admin"], BASE + "/preview", preview_request(w)) if surface == "preview"
         else w["admin"].get(BASE + ("/history" if surface == "history" else "")))
    assert r.status_code == 401 and "spec" not in r.json()
    assert not w["store"].all("SELECT * FROM model_provider_credentials")


@pytest.mark.parametrize("stage", ["preview", "append"])
def test_authority_loss_during_publication_rolls_back_even_after_append(provider_world, monkeypatch, stage):
    w = provider_world
    body = command(preview(w))
    original = getattr(ProviderSettings, stage)

    def expire(settings, *args, **kwargs):
        value = original(settings, *args, **kwargs)
        settings.store.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE user_id=?", (w["root"]["id"],))
        return value

    monkeypatch.setattr(ProviderSettings, stage, expire)
    r = publish(w, body)
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    assert not w["store"].all("SELECT * FROM model_provider_versions")
    assert not w["store"].all("SELECT * FROM model_provider_credentials")
    assert not audits(w, "ok") and not operations(w)


@pytest.mark.parametrize("failure", ["audit", "result", "append", "all_audits"])
@pytest.mark.parametrize("rotation", [False, True])
def test_persistence_failure_rolls_back_version_cipher_audit_and_result_without_logging_secrets(provider_world, monkeypatch, capsys, caplog, failure, rotation):
    w = provider_world
    if rotation:
        assert publish(w, command(preview(w))).status_code == 200
    credential = {"action": "replace", "api_key": KEY + "-discard"}
    body = command(preview(w, revision=int(rotation), credential=credential), credential)
    before = {table: w["store"].all(f"SELECT * FROM {table}") for table in
              ("model_provider_versions", "model_provider_credentials", "admin_operations")}
    original_execute, original_append = Store.execute, ProviderSettings.append

    def fail_audit(store, sql, params=()):
        result = original_execute(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and ACTION in params and ("ok" in params or failure == "all_audits"):
            raise OSError(KEY + "-sensitive-exception")
        return result

    def fail_append(settings, *args, **kwargs):
        original_append(settings, *args, **kwargs)
        raise OSError(KEY + "-sensitive-exception")

    if failure in ("audit", "all_audits"):
        monkeypatch.setattr(Store, "execute", fail_audit)
    elif failure == "append":
        monkeypatch.setattr(ProviderSettings, "append", fail_append)
    else:
        # A database trigger fails after append and successful audit, before result storage.
        w["store"].execute("CREATE TRIGGER fixture_operation_failure BEFORE INSERT ON admin_operations "
            "BEGIN SELECT RAISE(ABORT,'fixture-provider-credential-never-used-on-network'); END")
    response = publish(w, body)
    assert response.status_code == 503 and response.json()["error"] == "model_config_unavailable", response.text
    assert {table: w["store"].all(f"SELECT * FROM {table}") for table in before} == before
    assert len(audits(w, "ok")) == int(rotation)
    captured = capsys.readouterr()
    assert_no_public_secret(w, response.json(), captured.out, captured.err, caplog.text)
    if failure != "all_audits":
        assert json.loads(audits(w)[-1]["meta"])["code"] == "admin_operation_failed"
    monkeypatch.setattr(Store, "execute", original_execute)
    monkeypatch.setattr(ProviderSettings, "append", original_append)
    if failure == "result":
        w["store"].execute("DROP TRIGGER fixture_operation_failure")
    assert publish(w, body).status_code == 200


@pytest.mark.parametrize("mode", ["same_request", "different_keys", "same_key_changed_secret"])
def test_two_real_instances_serialize_credentials_versions_and_idempotency(provider_world, mode):
    w = provider_world
    from synwevo_identity.app import create_app
    with TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False) as client:
        client.cookies.update(w["admin"].cookies)
        first = command(preview(w))
        changed = {"action": "replace", "api_key": KEY + "-peer"}
        second = dict(first) if mode == "same_request" else command(preview(w, credential=changed), changed)
        if mode == "same_key_changed_secret":
            second["idempotency_key"] = first["idempotency_key"]
        barrier = threading.Barrier(2, timeout=10)

        def run(args):
            peer, body = args
            barrier.wait()
            return publish(w, body, client=peer)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(run, [(w["admin"], first), (client, second)]))
    assert sorted(r.status_code for r in responses) == ([200, 200] if mode == "same_request" else [200, 409]), [r.text for r in responses]
    assert len(w["store"].all("SELECT * FROM model_provider_versions")) == 1
    assert len(w["store"].all("SELECT * FROM model_provider_credentials")) == 1
    assert len(audits(w, "ok")) == len(operations(w)) == 1
    if mode == "same_request":
        assert responses[0].json() == responses[1].json()
    else:
        refused = next(r for r in responses if r.status_code == 409)
        assert refused.json()["error"] == ("idempotency_conflict" if mode == "same_key_changed_secret" else "model_revision_conflict")


@pytest.mark.parametrize("body", [None, [KEY], KEY, {"spec": KEY}, {"credential": {"action": "replace", "api_key": KEY}}])
@pytest.mark.parametrize("suffix", ["preview", "publish"])
def test_malformed_body_never_echoes_or_logs_payload(world, body, suffix, capsys, caplog):
    r = post(world["admin"], BASE + "/" + suffix, body)
    assert r.status_code == 400 and r.json()["error"] == "model_config_invalid", r.text
    captured = capsys.readouterr()
    assert_no_public_secret(world, r.json(), captured.out, captured.err, caplog.text)


@pytest.mark.parametrize("credential", [{"action": "replace", "api_key": "short"}, {"action": "replace", "api_key": KEY + "\n"},
    {"action": "keep", "api_key": KEY}, {"action": "revoke", "api_key": KEY}, {"action": "replace"},
    {"action": "replace", "api_key": {"secret": KEY}}, {"action": "replace", "api_key": 123},
    {"action": "other", "api_key": KEY}, {"action": "replace", "api_key": KEY, "unexpected": KEY}])
def test_invalid_credential_is_sanitized_on_both_http_writes(world, credential, capsys, caplog):
    plan = preview(world)
    for suffix, body in (("preview", preview_request(world)), ("publish", command(plan))):
        r = post(world["admin"], BASE + "/" + suffix, {**body, "credential": credential})
        assert r.status_code == 400 and r.json()["error"] == "model_config_invalid", r.text
        captured = capsys.readouterr()
        assert_no_public_secret(world, r.json(), captured.out, captured.err, caplog.text)
    assert not world["store"].all("SELECT * FROM model_provider_credentials")


@pytest.mark.parametrize("change", ["revision_bool", "revision_string", "revision_negative", "revision_overflow", "enabled",
    "route_missing", "token_ceiling", "valid_until", "route_unknown", "spec_unknown", "top_level_environment"])
def test_strict_bounded_input_rejects_coercion_and_scope_overrides(provider_world, change):
    w = provider_world
    body = copy.deepcopy(preview_request(w))
    revisions = {"revision_bool": True, "revision_string": "0", "revision_negative": -1, "revision_overflow": 2**63-1}
    if change in revisions:
        body["expected_revision"] = revisions[change]
    elif change == "enabled":
        body["spec"]["enabled"] = "true"
    elif change == "route_missing":
        body["spec"]["route"] = None
    elif change == "token_ceiling":
        body["spec"]["route"]["output_token_ceiling"] = True
    elif change == "valid_until":
        body["spec"]["route"]["valid_until"] = 2**63
    elif change == "route_unknown":
        body["spec"]["route"]["base_url"] = KEY
    elif change == "spec_unknown":
        body["spec"]["provider"] = KEY
    else:
        body["environment"] = "live"
    r = post(w["admin"], BASE + "/preview", body)
    assert r.status_code == 400 and r.json()["error"] == "model_config_invalid", r.text
    assert_no_public_secret(w, r.json())


@pytest.mark.parametrize("reason", ["short", " " * 8, " " + "x" * 7, "x" * 501])
def test_reason_is_bounded_and_whitespace_does_not_satisfy_minimum(world, reason):
    body = {**command(preview(world)), "reason": reason}
    r = publish(world, body)
    assert r.status_code == 400 and r.json()["error"] in ("model_config_invalid", "missing_reason")
    assert not operations(world)


@pytest.mark.parametrize("reason", ["  " + "x" * 8 + "  ", "x" * 500])
def test_reason_boundaries_are_accepted_and_normalized(world, reason):
    body = {**command(preview(world)), "reason": reason}
    r = publish(world, body)
    assert r.status_code == 200, r.text
    assert r.json()["state"]["reason"] == reason.strip()
    assert json.loads(audits(world, "ok")[0]["meta"])["reason"] == reason.strip()


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=" + KEY, "before_revision=-1",
    "before_revision=9223372036854775807", "before_revision=" + KEY])
def test_history_query_is_bounded_and_sanitized(world, query, capsys, caplog):
    r = world["admin"].get(BASE + "/history?" + query)
    assert r.status_code == 400 and r.json()["error"] == "model_config_invalid"
    captured = capsys.readouterr()
    assert_no_public_secret(world, r.json(), captured.out, captured.err, caplog.text)


@pytest.mark.parametrize("environment", [None, "invalid", "live"])
def test_environment_is_captured_and_unconfigured_host_fails_closed(provider_world, monkeypatch, environment):
    w = provider_world
    plan = preview(w)
    assert publish(w, command(plan)).status_code == 200
    if environment is None:
        monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT")
    else:
        monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", environment)
    assert w["admin"].get(BASE).json()["environment"] == "test"
    from synwevo_identity.app import create_app
    with TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False) as client:
        client.cookies.update(w["admin"].cookies)
        r = client.get(BASE)
        assert r.status_code == 200, r.text
        state = r.json()
        assert state["environment"] == ("live" if environment == "live" else None)
        assert state["status"] == "unconfigured" and state["spec"] is None and state["revision"] == 0
        assert not state["credential_configured"] and state["credential_revision"] == 0
        assert client.get(BASE + "/history").json() == {"items": [], "next_before_revision": None}
        r = publish(w, command(plan), client=client)
        assert r.status_code == (409 if environment == "live" else 503), r.text
        if environment != "live":
            assert r.json()["error"] == "billing_not_configured"
            r = post(client, BASE + "/preview", preview_request(w))
            assert r.status_code == 503 and r.json()["error"] == "billing_not_configured"
        else:
            # The fixed account is still isolated by environment, including key revision.
            live = preview(w, client=client, spec=DISABLED)
            assert publish(w, command(live), client=client).status_code == 200
    assert ProviderSettings(w["store"], environment="test").read()["revision"] == 1


def test_openapi_and_runtime_contracts_are_typed_and_never_include_output_secret_fields(provider_world):
    w = provider_world
    # OpenAPI deduplicates paths, while api.routes keeps lazy included routers.
    # Inspect effective routes to detect duplicate registrations in the real factory.
    routes = [route for route in iter_route_contexts(w["api"].routes) if (route.path or "").startswith(BASE)]
    expected_routes = {(BASE, "GET"), (BASE + "/history", "GET"),
                       (BASE + "/preview", "POST"), (BASE + "/publish", "POST")}
    assert len(routes) == 4
    assert {(route.path, method) for route in routes for method in route.methods} == expected_routes
    assert all(isinstance(route.original_route, admin_model_provider.ProviderRoute) for route in routes)
    assert all(route.endpoint.__module__ == admin_model_provider.__name__ for route in routes)
    schema = w["api"].openapi()
    paths, models = schema["paths"], schema["components"]["schemas"]
    assert {path for path in paths if path.startswith(BASE)} == {BASE, BASE + "/history", BASE + "/preview", BASE + "/publish"}
    for path, methods in paths.items():
        if not path.startswith(BASE):
            continue
        for method, operation in methods.items():
            assert "422" not in operation["responses"]
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            if method == "post":
                name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                assert models[name]["additionalProperties"] is False
                assert "credential" in models[name]["required"]
    assert models["ProviderPreview"]["properties"]["expires_at"]["type"] == "number"
    assert set(models["CredentialCommitment"]["properties"]) == {"action", "digest"}
    assert set(models["ProviderSpec"]["required"]) == {"enabled", "route"}
    plan = preview(w)
    result = publish(w, command(plan)).json()
    history = w["admin"].get(BASE + "/history").json()
    for contract, data in ((admin_model_provider.ProviderPreview, plan), (admin_model_provider.ProviderPublication, result),
                          (admin_model_provider.ProviderHistory, history), (admin_model_provider.ProviderState, result["state"])):
        assert contract.model_validate(data).model_dump() == data
    terminal = {**result["state"], "revision": 2**63-1, "credential_revision": 2**63-1}
    assert admin_model_provider.ProviderState.model_validate(terminal).model_dump() == terminal


def test_invalid_json_never_echoes_raw_key(world, capsys, caplog):
    client = world["admin"]
    response = client.post(BASE + "/preview", content='{"credential":{"api_key":"' + KEY,
        headers={"Content-Type": "application/json", CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})
    assert response.status_code == 400 and response.json()["error"] == "model_config_invalid"
    captured = capsys.readouterr()
    assert_no_public_secret(world, response.json(), captured.out, captured.err, caplog.text)
