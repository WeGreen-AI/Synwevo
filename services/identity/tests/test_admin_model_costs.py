"""Real isolated HTTP cost governance; synthetic units, no funds or provider traffic."""
from __future__ import annotations

import concurrent.futures
import copy
import json
import threading
import time
import types
import uuid

import pytest
from fastapi.testclient import TestClient

from synwevo_core.billing import BillingError, ChargeContext, Ledger, Unit
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.catalogue import PriceCatalogue
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.sessions import COOKIE_NAME, Sessions
from synwevo_core.store import Store
from synwevo_identity import admin_model_costs

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_model_prices import SPEC as PRICE_SPEC
from .test_admin_operations import PW, post, world

BASE = "/api/admin/billing/cost-policy"
ACTION = "admin.model_cost.publish"
REASON = "Fixture: confirm bounded platform account costs"
SPEC = {"enabled": True, "unit": {"code": "FIX_COST", "scale": 6},
        "daily_budget": "9007199254740993", "weekly_budget": "9223372036854775807",
        "monthly_budget": "9223372036854775807", "unknown_budget": "0",
        "max_inflight": 1, "max_unknown": 10000}


@pytest.fixture(autouse=True)
def billing_environment(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")


def seed_price(w, *, environment="test", purpose="cost", unit=None):
    """A real immutable catalogue price using explicitly synthetic fixture values."""
    catalogue = PriceCatalogue(w["store"], environment=environment)
    with w["store"].tx():
        draft, _ = catalogue.create(spec={**PRICE_SPEC, "purpose": purpose, "unit": unit or SPEC["unit"]},
            actor_id=w["root"]["id"], idempotency_key=uuid.uuid4().hex)
        return catalogue.publish(draft["id"], expected_revision=draft["revision"],
            actor_id=w["root"]["id"], operation_id="fixture_" + uuid.uuid4().hex, reason=REASON)


@pytest.fixture()
def cost_world(world):
    seed_price(world)
    return world


def preview(w, *, revision=0, spec=None, client=None):
    r = post(client or w["admin"], BASE + "/preview", {"expected_revision": revision, "spec": spec or SPEC})
    assert r.status_code == 200, r.text
    return r.json()


def command(plan):
    return {"expected_revision": plan["before"]["revision"], "spec": plan["after"],
            "preview_token": plan["preview_token"], "idempotency_key": uuid.uuid4().hex, "reason": REASON}


def publish(w, body, *, client=None, **kwargs):
    return post(client or w["admin"], BASE + "/publish", body, **kwargs)


def audits(w, result=None):
    return w["store"].all("SELECT * FROM audit_events WHERE action=?" +
        (" AND result=?" if result else "") + " ORDER BY seq", (ACTION, result) if result else (ACTION,))


def test_no_implicit_policy_or_units_and_only_published_cost_units_are_available(world):
    w = world
    initial = w["admin"].get(BASE)
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"environment": "test", "provider": "deepseek", "account_ref": "platform-deepseek",
        "revision": 0, "spec": None, "actor_id": None, "created_at": None, "reason": None,
        "operation_id": None, "units": []}
    assert w["admin"].get(BASE + "/history").json() == {"items": [], "next_before_revision": None}
    seed_price(w, purpose="sale")
    seed_price(w, environment="live")
    assert w["admin"].get(BASE).json() == initial.json()
    refused = post(w["admin"], BASE + "/preview", {"expected_revision": 0, "spec": SPEC})
    assert refused.status_code == 409 and refused.json()["error"] == "cost_unit_unavailable"
    seed_price(w)
    assert w["admin"].get(BASE).json() == {**initial.json(), "units": [SPEC["unit"]]}
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")


def test_preview_is_read_only_and_publish_preserves_exact_limits_without_business_or_money_access(cost_world):
    w = cost_world
    private = w["ident"].create_workspace("Fixture private workspace", owner_id=w["target"]["id"])
    untouched = ("billing_accounts", "billing_journal", "billing_attempts", "billing_runs", "billing_orders",
                 "memberships", "app_entitlements", "mail_outbox", "notifications")
    before = {table: w["store"].all(f"SELECT * FROM {table}") for table in untouched}
    plan = preview(w)
    assert plan["before"] == w["admin"].get(BASE).json()
    assert plan["after"] == SPEC and plan["risk"] == "high" and plan["environment"] == "test"
    assert not audits(w) and not w["store"].all("SELECT * FROM admin_operations")
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")
    response = publish(w, command(plan))
    assert response.status_code == 200, response.text
    result = response.json()
    state = result["state"]
    assert result["status"] == "succeeded" and state["revision"] == 1 and state["spec"] == SPEC
    assert state == CostSettings(w["store"], environment="test").read()
    assert state == w["admin"].get(BASE).json()
    assert state["actor_id"] == w["root"]["id"] and state["reason"] == REASON
    assert state["operation_id"] == result["operation_id"]
    assert CostSettings(w["store"], environment="test").policy().daily_budget == 9007199254740993
    success = audits(w, "ok")
    assert len(success) == 1 and success[0]["workspace_id"] is None
    metadata = json.loads(success[0]["meta"])
    assert metadata["after"] == SPEC and metadata["before"] is None
    assert metadata["operation_id"] == result["operation_id"]
    assert w["ident"].membership(private["id"], w["root"]["id"]) is None
    assert private["id"] not in {x["id"] for x in w["ident"].workspaces_for(w["root"]["id"])}
    assert {table: w["store"].all(f"SELECT * FROM {table}") for table in untouched} == before


def test_replay_returns_original_result_without_new_version_even_after_later_publication(cost_world, monkeypatch):
    w = cost_world
    plan = preview(w)
    body = command(plan)
    first = publish(w, body).json()
    second = publish(w, command(preview(w, revision=1, spec={**SPEC, "enabled": False})))
    assert second.status_code == 200, second.text
    monkeypatch.setattr(admin_model_costs, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    assert publish(w, body).json() == first
    assert w["admin"].get(BASE).json()["revision"] == 2
    assert len(audits(w, "ok")) == 2
    for changed in ({"reason": REASON + " changed"}, {"spec": {**SPEC, "daily_budget": "10"}},
                    {"expected_revision": 1}, {"preview_token": body["preview_token"] + "tamper"}):
        r = publish(w, {**body, **changed})
        assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict"


def test_http_publication_updates_existing_dispatch_consumer_without_restart_or_funds(cost_world):
    w = cost_world
    settings = CostSettings(w["store"], environment="test")
    uid = w["target"]["id"]
    context = ChargeContext(uid, uid, "fixture-cost-http-run", "fixture-cost-http-authorization")
    # Explicit isolated authorization for this fixture's zero-value hold only. No payment
    # is credited and dispatch records a fence; there is no transport or provider call.
    ledger = Ledger(w["store"], environment="test", namespace="fixture-cost-http",
                    authorize=lambda _t, ctx, _action: ctx == context)
    admission = CostAdmission(ledger, current_policy=settings.policy)
    with pytest.raises(BillingError, match="cost_not_configured"):
        admission.snapshot()
    cost = seed_price(w)
    sale = seed_price(w, purpose="sale")
    deadline = int(time.time()) + 120
    ledger.open_run(context, unit=Unit(**SPEC["unit"]), budget=0, max_calls=1, max_attempts=1, deadline=deadline)
    call = ledger.reserve_call(context, logical_call_id="fixture-cost-http-call", amount=0,
                               price_id=sale["price_id"], deadline=deadline)
    ticket = ledger.prepare_attempt(call, attempt_id="fixture-cost-http-attempt", owner="fixture-worker",
                                    cost_price_id=cost["price_id"], requested_model=PRICE_SPEC["model"])
    def dispatch():
        ledger.dispatch(ticket, admission=lambda tx, attempt: admission.admit(tx, attempt, amount=1, expires_at=deadline))
    limits = {**SPEC, "enabled": False, "daily_budget": "0", "unknown_budget": "1"}
    assert publish(w, command(preview(w, spec=limits))).status_code == 200
    with pytest.raises(BillingError, match="cost_admission_disabled"):
        dispatch()
    limits = {**limits, "enabled": True}
    assert publish(w, command(preview(w, revision=1, spec=limits))).status_code == 200
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        dispatch()
    limits = {**limits, "daily_budget": "1"}
    assert publish(w, command(preview(w, revision=2, spec=limits))).status_code == 200
    dispatch()
    row = w["store"].one("SELECT * FROM model_cost_admissions WHERE attempt_key=?", (ticket.attempt_key,))
    assert row["policy_revision"] == "model-cost:test:3" and row["account_ref"] == "platform-deepseek"
    assert row["reserved_units"] == 1 and admission.snapshot()["unknown_reserved_units"] == 1
    assert not w["store"].all("SELECT * FROM billing_journal")


@pytest.mark.parametrize("change", ["actor", "origin", "signature", "expired", "environment", "action", "scope",
                                    "target", "provider", "revision", "spec", "before_digest"])
def test_signed_preview_binds_all_authority_and_change_fields(cost_world, monkeypatch, change):
    w = cost_world
    plan = preview(w)
    body, kwargs = command(plan), {}
    if change == "actor":
        actor = make_platform_admin(w["store"], w["ident"].create_user("other-cost@example.test", PW, status="active"))
        kwargs["client"] = w["login"](actor)
        w["reauth"](kwargs["client"])
    elif change == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif change == "signature":
        body["preview_token"] += "tamper"
    elif change == "expired":
        monkeypatch.setattr(admin_model_costs, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    elif change == "revision":
        body["expected_revision"] = 1
    elif change == "spec":
        body["spec"] = {**SPEC, "daily_budget": "10"}
    else:
        signer = admin_model_costs.CostOperations(w["store"], None, "test")
        data = signer._verify(body["preview_token"])
        data[change] = "live" if change == "environment" else "different-fixture-binding"
        body["preview_token"] = signer._sign(data)
    response = publish(w, body, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == ("admin_preview_expired" if change == "expired" else "admin_preview_invalid")
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")
    assert not w["store"].all("SELECT * FROM admin_operations")


def test_stale_revision_and_changes_in_available_units_require_a_new_preview(cost_world):
    w = cost_world
    stale = command(preview(w))
    seed_price(w, unit={"code": "FIX_OTHER", "scale": 4})
    r = publish(w, stale)
    assert r.status_code == 409 and r.json()["error"] == "admin_preview_invalid"
    stale = command(preview(w))
    first = publish(w, command(preview(w, spec={**SPEC, "enabled": False})))
    assert first.status_code == 200, first.text
    r = publish(w, stale)
    assert r.status_code == 409 and r.json()["error"] == "cost_revision_conflict"
    assert r.json()["detail"]["current"] == first.json()["state"]
    r = post(w["admin"], BASE + "/preview", {"expected_revision": 1, "spec": {**SPEC, "enabled": False}})
    assert r.status_code == 409 and r.json()["error"] == "cost_no_change"


def test_history_paginates_real_versions_and_restoration_is_a_new_version(cost_world):
    w = cost_world
    for revision, spec in enumerate((SPEC, {**SPEC, "enabled": False}, SPEC)):
        r = publish(w, command(preview(w, revision=revision, spec=spec)))
        assert r.status_code == 200, r.text
    first = w["admin"].get(BASE + "/history?limit=2").json()
    assert [item["revision"] for item in first["items"]] == [3, 2]
    assert first["next_before_revision"] == 2 and all("units" not in item for item in first["items"])
    last = w["admin"].get(BASE + "/history?limit=2&before_revision=2").json()
    assert [item["revision"] for item in last["items"]] == [1] and last["next_before_revision"] is None
    assert first["items"][0]["spec"] == last["items"][0]["spec"] == SPEC
    assert first["items"][0]["operation_id"] != last["items"][0]["operation_id"]


def test_permissions_csrf_freshness_and_revoked_replay(cost_world):
    w = cost_world
    w["ident"].create_workspace("Fixture owner has no platform authority", owner_id=w["target"]["id"])
    for suffix in ("", "/history"):
        assert w["ordinary"].get(BASE + suffix).status_code == 404
    for suffix, body in (("/preview", {"expected_revision": 0, "spec": SPEC}),
                         ("/publish", command(preview(w)))):
        assert post(w["ordinary"], BASE + suffix, body).status_code == 404
    viewer = make_platform_admin(w["store"], w["ident"].create_user("cost-viewer@example.test", PW, status="active"), "auditor")
    client = w["login"](viewer)
    assert client.get(BASE).status_code == 200 and client.get(BASE + "/history").status_code == 200
    assert post(client, BASE + "/preview", {"expected_revision": 0, "spec": SPEC}).status_code == 403
    assert publish(w, command(preview(w)), client=client).status_code == 403
    manager = w["login"](w["root"])
    body = command(preview(w, client=manager))
    assert publish(w, body, client=manager).json()["error"] == "reauth_required"
    w["reauth"](manager)
    assert manager.post(BASE + "/publish", json=body).status_code == 403
    assert publish(w, body, client=manager).status_code == 200
    end_platform_grants(w["store"], w["root"])
    assert publish(w, body, client=manager).status_code == 404


@pytest.mark.parametrize("withdrawal", ["permission", "session", "freshness"])
@pytest.mark.parametrize("replay", [False, True])
def test_publish_rechecks_authority_under_writer_lock_for_new_writes_and_replay(cost_world, monkeypatch, withdrawal, replay):
    w = cost_world
    body = command(preview(w))
    if replay:
        assert publish(w, body).status_code == 200
    original, happened = admin_model_costs.require_platform, False
    def withdraw(request, *args, **kwargs):
        nonlocal happened
        actor = original(request, *args, **kwargs)
        if request.url.path.endswith("/publish") and not happened:
            happened = True
            separate = Store(w["store"].root)
            if withdrawal == "permission":
                end_platform_grants(separate, w["root"])
            else:
                session = Sessions(separate).load(w["admin"].cookies.get(COOKIE_NAME), touch=False)
                if withdrawal == "session":
                    Sessions(separate).revoke(session["id"], "fixture_logout")
                else:
                    separate.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE id=?", (session["id"],))
        return actor
    monkeypatch.setattr(admin_model_costs, "require_platform", withdraw)
    r = publish(w, body)
    assert r.status_code == {"permission": 404, "session": 401, "freshness": 403}[withdrawal], r.text
    assert len(w["store"].all("SELECT * FROM model_cost_policy_versions")) == int(replay)
    assert len(audits(w, "ok")) == int(replay)


@pytest.mark.parametrize("surface", ["read", "history", "preview"])
def test_read_and_preview_recheck_after_resolving_state(cost_world, monkeypatch, surface):
    w = cost_world
    original = getattr(CostSettings, surface)
    def revoke(settings, *args, **kwargs):
        value = original(settings, *args, **kwargs)
        # Same-transaction injection models authorization becoming invalid before return.
        settings.store.execute("UPDATE sessions SET revoked_at=1 WHERE user_id=?", (w["root"]["id"],))
        return value
    monkeypatch.setattr(CostSettings, surface, revoke)
    r = (post(w["admin"], BASE + "/preview", {"expected_revision": 0, "spec": SPEC}) if surface == "preview"
         else w["admin"].get(BASE + ("/history" if surface == "history" else "")))
    assert r.status_code == 401 and "spec" not in r.json()
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")


def test_fresh_auth_is_rechecked_after_preview_before_append(cost_world, monkeypatch):
    w = cost_world
    body = command(preview(w))
    original = CostSettings.preview
    def expire(settings, *args, **kwargs):
        value = original(settings, *args, **kwargs)
        settings.store.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE user_id=?", (w["root"]["id"],))
        return value
    monkeypatch.setattr(CostSettings, "preview", expire)
    r = publish(w, body)
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")
    assert not audits(w, "ok")


def test_audit_failure_rolls_back_policy_result_and_partial_audit_without_logging_exception(cost_world, monkeypatch, capsys, caplog):
    w = cost_world
    body = command(preview(w))
    original = Store.execute
    def fail(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and ACTION in params and "ok" in params:
            raise OSError("fixture-sensitive-exception")
        return result
    monkeypatch.setattr(Store, "execute", fail)
    response = publish(w, body)
    assert response.status_code == 503 and response.json()["error"] == "cost_settings_unavailable"
    assert not w["store"].all("SELECT * FROM model_cost_policy_versions")
    assert not w["store"].all("SELECT * FROM admin_operations")
    assert not audits(w, "ok")
    captured = capsys.readouterr()
    assert "fixture-sensitive-exception" not in response.text + captured.out + captured.err + caplog.text + json.dumps(audits(w))
    assert json.loads(audits(w)[0]["meta"])["code"] == "admin_operation_failed"
    monkeypatch.setattr(Store, "execute", original)
    assert publish(w, body).status_code == 200


@pytest.mark.parametrize("same_key", [False, True])
def test_two_instances_publish_one_policy_and_idempotent_result(cost_world, same_key):
    w = cost_world
    from synwevo_identity.app import create_app
    with TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False) as client:
        client.cookies.update(w["admin"].cookies)
        first = command(preview(w))
        second = dict(first) if same_key else command(preview(w))
        barrier = threading.Barrier(2, timeout=10)
        def run(args):
            peer, body = args
            barrier.wait()
            return publish(w, body, client=peer)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, [(w["admin"], first), (client, second)]))
    assert sorted(r.status_code for r in results) == ([200, 200] if same_key else [200, 409]), [r.text for r in results]
    assert len(w["store"].all("SELECT * FROM model_cost_policy_versions")) == 1
    assert len(audits(w, "ok")) == 1
    assert len(w["store"].all("SELECT * FROM admin_operations WHERE action=?", (ACTION,))) == 1
    if same_key:
        assert results[0].json() == results[1].json()


@pytest.mark.parametrize("body", [None, ["fixture-secret"], "fixture-secret", {"spec": "fixture-secret"}])
@pytest.mark.parametrize("suffix", ["preview", "publish"])
def test_malformed_body_never_echoes_or_logs_payload(world, body, suffix, capsys, caplog):
    r = post(world["admin"], BASE + "/" + suffix, body)
    assert r.status_code == 400 and r.json()["error"] == "cost_invalid", r.text
    captured = capsys.readouterr()
    assert "fixture-secret" not in r.text + captured.out + captured.err + caplog.text + json.dumps(audits(world))
    assert not world["store"].all("SELECT * FROM model_cost_policy_versions")


@pytest.mark.parametrize("field,value", [("enabled", "true"), ("enabled", 1), ("daily_budget", 1),
    ("daily_budget", "01"), ("daily_budget", "-1"), ("daily_budget", "1e3"), ("daily_budget", "1.0"),
    ("daily_budget", "9223372036854775808"), ("unknown_budget", True), ("max_inflight", True),
    ("max_inflight", "1"), ("max_unknown", 0), ("max_unknown", 10001),
    ("unit", {"code": "FIX_COST", "scale": True}), ("account_ref", "fixture-secret"),
    ("provider", "fixture-secret"), ("credentials", "fixture-secret")])
def test_strict_spec_rejects_coercion_unknown_fields_and_unsafe_amounts(world, field, value):
    spec = copy.deepcopy(SPEC)
    spec[field] = value
    r = post(world["admin"], BASE + "/preview", {"expected_revision": 0, "spec": spec})
    assert r.status_code == 400 and r.json()["error"] == "cost_invalid", r.text
    assert "fixture-secret" not in r.text + json.dumps(audits(world))
    assert not world["store"].all("SELECT * FROM model_cost_policy_versions")


@pytest.mark.parametrize("revision", [True, "0", -1, 0.0, 2**63-1])
def test_revision_is_strict_and_bounded(world, revision):
    r = post(world["admin"], BASE + "/preview", {"expected_revision": revision, "spec": SPEC})
    assert r.status_code == 400 and r.json()["error"] == "cost_invalid"


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=fixture-secret", "before_revision=-1",
                                 "before_revision=9223372036854775807", "before_revision=fixture-secret"])
def test_history_query_is_bounded_and_sanitized(world, query):
    r = world["admin"].get(BASE + "/history?" + query)
    assert r.status_code == 400 and "fixture-secret" not in r.text


@pytest.mark.parametrize("environment", [None, "invalid", "live"])
def test_host_environment_is_fixed_and_missing_configuration_fails_closed(cost_world, monkeypatch, environment):
    w = cost_world
    plan = preview(w)
    if environment is None:
        monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT")
    else:
        monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", environment)
    # Existing routers retain the host environment captured when they were built.
    assert w["admin"].get(BASE).json()["environment"] == "test"
    from synwevo_identity.app import create_app
    with TestClient(create_app(str(w["store"].root), start_jobs=False), raise_server_exceptions=False) as client:
        client.cookies.update(w["admin"].cookies)
        state = client.get(BASE).json()
        assert state == {"environment": "live" if environment == "live" else None,
            "provider": "deepseek", "account_ref": "platform-deepseek", "revision": 0,
            "spec": None, "actor_id": None, "created_at": None, "reason": None, "operation_id": None, "units": []}
        assert client.get(BASE + "/history").json() == {"items": [], "next_before_revision": None}
        r = publish(w, command(plan), client=client)
        assert r.status_code == (409 if environment == "live" else 503), r.text
        if environment != "live":
            assert r.json()["error"] == "billing_not_configured"
            r = post(client, BASE + "/preview", {"expected_revision": 0, "spec": SPEC})
            assert r.status_code == 503 and r.json()["error"] == "billing_not_configured"


def test_openapi_and_runtime_responses_are_typed_with_no_implicit_policy_defaults(cost_world):
    w = cost_world
    schema = w["api"].openapi()
    paths, models = schema["paths"], schema["components"]["schemas"]
    assert {path for path in paths if path.startswith(BASE)} == {BASE, BASE + "/history", BASE + "/traffic",
                                                             BASE + "/preview", BASE + "/publish"}
    for path, methods in paths.items():
        if not path.startswith(BASE):
            continue
        for method, operation in methods.items():
            assert "422" not in operation["responses"]
            assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            if method == "post":
                name = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
                assert models[name]["additionalProperties"] is False
    spec = models["CostPolicySpec"]
    assert set(spec["required"]) == set(SPEC)
    for field in ("daily_budget", "weekly_budget", "monthly_budget", "unknown_budget"):
        assert spec["properties"][field]["type"] == "string"
    plan = preview(w)
    result = publish(w, command(plan)).json()
    history = w["admin"].get(BASE + "/history").json()
    for contract, data in ((admin_model_costs.CostPreview, plan), (admin_model_costs.CostPublication, result),
                           (admin_model_costs.CostHistory, history), (admin_model_costs.CostState, result["state"])):
        assert contract.model_validate(data).model_dump() == data
    # The final int64 revision can be read even though it cannot be advanced again.
    terminal = {**result["state"], "revision": 2**63-1}
    assert admin_model_costs.CostState.model_validate(terminal).model_dump() == terminal


TRAFFIC = {"platform_rpm": 600, "platform_tpm": 2_000_000, "payer_rpm": 60, "payer_tpm": 200_000,
           "payer_inflight": 1, "wait_seconds": 300, "max_waiting": 500, "max_waiting_per_payer": 20}


def test_traffic_limits_publish_exactly_and_invalid_combinations_are_refused(cost_world):
    w = cost_world
    spec = {**SPEC, "traffic": TRAFFIC}
    plan = preview(w, spec=spec)
    assert plan["after"] == spec
    saved = publish(w, command(plan))
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"]["spec"]["traffic"] == TRAFFIC
    for change in ({"payer_rpm": 601}, {"payer_tpm": 2_000_001}, {"max_waiting_per_payer": 501},
                   {"payer_inflight": 2}, {"wait_seconds": 901}, {"platform_rpm": 0}, {"extra": 1}):
        r = post(w["admin"], BASE + "/preview", {"expected_revision": 1, "spec": {**SPEC, "traffic": {**TRAFFIC, **change}}})
        assert r.status_code == 400 and r.json()["error"] in ("cost_invalid", "cost_traffic_invalid"), change
        # Submitted values are not echoed. The request id is random hex and can contain the same digits by
        # chance (CI run 37124810853: "1833f60141cff808"; locally "14a9601046e2e401" under `request_id`),
        # so it is left out of the comparison under either key.
        said = json.dumps({k: v for k, v in r.json().items() if k not in ("id", "request_id")})
        assert "601" not in said and "2000001" not in said
    # A policy without traffic keeps its exact former shape: no "traffic": null appears.
    plan = preview(w, revision=1, spec=SPEC)
    assert "traffic" not in plan["after"] and plan["before"]["spec"]["traffic"] == TRAFFIC


def test_traffic_usage_is_platform_wide_counters_for_viewers_only(cost_world):
    w = cost_world
    assert w["ordinary"].get(BASE + "/traffic").status_code == 404
    viewer = make_platform_admin(w["store"], w["ident"].create_user("traffic-viewer@example.test", PW, status="active"), "auditor")
    client = w["login"](viewer)
    empty = client.get(BASE + "/traffic")
    assert empty.status_code == 200 and empty.json()["configured"] is False and empty.json()["requests"] == 0
    assert publish(w, command(preview(w, spec={**SPEC, "traffic": TRAFFIC}))).status_code == 200
    now = int(time.time())
    for state, admitted in (("admitted", now), ("finished", now - 10), ("waiting", None), ("finished", now - 3600)):
        w["store"].execute("INSERT INTO model_traffic_claims(id,environment,account_ref,payer_user_id,est_tokens,state,"
                           "enqueued_at,wait_until,admitted_at,inflight_until,actual_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           ("traffic_" + uuid.uuid4().hex[:12], "test", "platform-deepseek", w["target"]["id"], 100, state,
                            now - 5, now + 300, admitted, now + 60 if state == "admitted" else None,
                            40 if state == "finished" else None))
    usage = client.get(BASE + "/traffic").json()
    # Two admitted within the minute (100 estimated + 40 actual); the hour-old call is outside it.
    assert (usage["configured"], usage["requests"], usage["tokens"], usage["inflight"], usage["waiting"]) == (True, 2, 140, 1, 1)
    assert usage["oldest_wait_s"] >= 5 and w["target"]["id"] not in json.dumps(usage)
