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
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store
from synwevo_identity import admin_model_provider
from synwevo_identity.admin_operations import canonical

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, post, world

BASE = "/api/admin/billing/model-provider"
ACTION = "admin.model_provider.publish"
REASON = "Fixture: confirm bounded platform model configuration"
KEY = "fixture-provider-credential-never-used-on-network"
REPLACEMENT = {"action": "replace", "api_key": KEY}
DISABLED = {"enabled": False, "route": None}
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
        "valid_until": int(time.time()) + 3600, "reasoning_efforts": ["none", "low", "high", "max"]}}


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
        "credential_configured": False, "actor_id": None, "created_at": None, "reason": None, "operation_id": None}
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
    assert plan["before"] == w["admin"].get(BASE).json()
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
    assert state == w["admin"].get(BASE).json()
    assert {k: v for k, v in state.items() if k != "status"} == ProviderSettings(w["store"], environment="test").read()
    cipher = w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"]
    assert cipher and KEY not in cipher
    success = audits(w, "ok")
    assert len(success) == 1 and success[0]["workspace_id"] is None
    meta = json.loads(success[0]["meta"])
    assert meta == {"billing_environment": "test", "provider": "deepseek", "origin": "http://testserver",
        "operation_id": result["operation_id"], "reason": REASON, "before_revision": 0, "revision": 1,
        "credential_action": "replace", "before_credential_revision": 0, "credential_revision": 1}
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
    assert post(client, BASE + "/preview", preview_request(w)).status_code == 403
    assert publish(w, command(preview(w)), client=client).status_code == 403
    manager = w["login"](w["root"])
    body = command(preview(w, client=manager))
    assert publish(w, body, client=manager).json()["error"] == "reauth_required"
    w["reauth"](manager)
    assert manager.post(BASE + "/preview", json=preview_request(w)).status_code == 403
    assert manager.post(BASE + "/publish", json=body).status_code == 403
    assert publish(w, body, client=manager).status_code == 200
    end_platform_grants(w["store"], w["root"])
    assert publish(w, body, client=manager).status_code == 404


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
