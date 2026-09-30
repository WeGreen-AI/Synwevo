"""Real isolated HTTP settings edits, signatures, history, authorization and competing instances."""
from __future__ import annotations

import concurrent.futures
import json
import threading
import types
import uuid

import pytest
from fastapi.testclient import TestClient

from synwevo_core.platform_settings import DAILY_CAP_KEY, INTERVAL_KEY, PlatformSettings
from synwevo_core.sessions import COOKIE_NAME, Sessions
from synwevo_core.store import Store
from synwevo_identity import admin_settings

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, post, world

REASON = "Fixture: confirm the conversation digest configuration"


def preview(w, body=None, client=None):
    response = post(client or w["admin"], "/api/admin/settings/preview",
                    body or {"expected_revision": 0, "changes": {INTERVAL_KEY: 60}})
    assert response.status_code == 200, response.text
    return response.json()


def command(plan, *, key=None):
    selection = {"rollback_revision": plan["rollback_revision"]} if plan["rollback_revision"] is not None else {"changes": plan["changes"]}
    return {**selection, "expected_revision": plan["expected_revision"], "preview_token": plan["preview_token"],
            "idempotency_key": key or uuid.uuid4().hex, "reason": REASON}


def publish(w, body, *, client=None, **kwargs):
    return post(client or w["admin"], "/api/admin/settings/publish", body, **kwargs)


def audits(w, result=None):
    return w["store"].all("SELECT * FROM audit_events WHERE action='admin.settings.publish'" +
                          (" AND result=?" if result else "") + " ORDER BY seq", (result,) if result else ())


def test_read_and_preview_are_real_read_only_configuration_without_worker_claims(world):
    response = world["admin"].get("/api/admin/settings")
    assert response.status_code == 200
    read = response.json()
    assert {row["key"] for row in read["catalogue"]} == {INTERVAL_KEY, DAILY_CAP_KEY}
    assert read["state"]["revision"] == 0 and read["state"]["values"][INTERVAL_KEY] == 1800
    counts = {table: world["store"].one(f"SELECT COUNT(*) n FROM {table}")["n"]
              for table in ("platform_settings_versions", "admin_operations", "audit_events", "mail_outbox", "notifications")}
    plan = preview(world, {"expected_revision": 0, "changes": {INTERVAL_KEY: 60, DAILY_CAP_KEY: None}})
    assert plan["action"] == "admin.settings.publish" and plan["scope"] == "platform" and plan["risk"] == "high"
    assert plan["expected_revision"] == 0 and plan["before"] == read["state"]
    assert plan["after"]["values"] == {INTERVAL_KEY: 60, DAILY_CAP_KEY: 8}
    assert plan["changes"] == {INTERVAL_KEY: 60, DAILY_CAP_KEY: None}
    assert "revision" not in plan["after"] and "applied" not in plan
    assert {table: world["store"].one(f"SELECT COUNT(*) n FROM {table}")["n"] for table in counts} == counts
    assert world["admin"].get("/api/admin/settings/history").json() == {"items": [], "next_before_revision": None}
    assert publish(world, command(plan)).status_code == 200


def test_publish_commits_one_result_snapshot_and_audit_and_retries_return_it(world):
    body = command(preview(world))
    first = publish(world, body)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "succeeded" and result["revision"] == 1
    assert result["state"] == PlatformSettings(world["store"]).read()
    assert publish(world, body).json() == result
    history = world["admin"].get("/api/admin/settings/history").json()["items"]
    assert len(history) == 1 and history[0]["operation_id"] == result["operation_id"]
    assert history[0]["actor_id"] == world["root"]["id"] and history[0]["reason"] == REASON
    success = audits(world, "ok")
    assert len(success) == 1 and json.loads(success[0]["meta"])["operation_id"] == result["operation_id"]
    assert world["store"].one("SELECT COUNT(*) n FROM admin_operations WHERE action='admin.settings.publish'")["n"] == 1
    for field, changed in (("reason", REASON + " changed"), ("changes", {INTERVAL_KEY: 120})):
        response = publish(world, {**body, field: changed})
        assert response.status_code == 409 and response.json()["error"] == "idempotency_conflict"
    assert PlatformSettings(world["store"]).read()["revision"] == 1


def test_current_permission_and_strict_admin_confirmation_protect_new_writes_and_retries(world):
    actor = make_platform_admin(world["store"], world["ident"].create_user("config@example.test", PW, status="active"))
    client = world["login"](actor)
    body = command(preview(world, client=client))
    denied = publish(world, body, client=client)
    assert denied.status_code == 403 and denied.json()["error"] == "reauth_required"
    world["reauth"](client)
    assert publish(world, body, client=client).status_code == 200
    end_platform_grants(world["store"], actor)
    denied = publish(world, body, client=client)
    assert denied.status_code == 404
    assert len(audits(world, "ok")) == 1


@pytest.mark.parametrize("alteration", ["actor", "origin", "signature", "expired", "changes", "revision", "rollback", "action", "scope", "target"])
def test_preview_cannot_be_rebound_to_another_actor_origin_input_or_operation(world, monkeypatch, alteration):
    plan = preview(world)
    body, kwargs = command(plan), {}
    if alteration == "actor":
        other = make_platform_admin(world["store"], world["ident"].create_user("other@example.test", PW, status="active"))
        kwargs["client"] = world["login"](other)
        world["reauth"](kwargs["client"])
    elif alteration == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif alteration == "signature":
        body["preview_token"] += "tampered"
    elif alteration == "expired":
        monkeypatch.setattr(admin_settings, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    elif alteration == "changes":
        body["changes"] = {INTERVAL_KEY: 120}
    elif alteration == "revision":
        body["expected_revision"] = 1
    elif alteration == "rollback":
        del body["changes"]
        body["rollback_revision"] = 0
    else:
        # A valid server signature from a different binding must still fail, not just a bad HMAC.
        signer = admin_settings.SettingsOperations(world["store"], None)
        value = signer._verify(body["preview_token"])
        value[alteration] = "admin.user.status" if alteration == "action" else "another-scope"
        body["preview_token"] = signer._sign(value)
    response = publish(world, body, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == ("admin_preview_expired" if alteration == "expired" else "admin_preview_invalid")
    assert PlatformSettings(world["store"]).read()["revision"] == 0
    assert not world["store"].all("SELECT * FROM admin_operations")


def test_stale_preview_conflict_contains_current_state_even_after_a_b_a(world):
    stale = command(preview(world))
    assert publish(world, command(preview(world, {"expected_revision": 0, "changes": {DAILY_CAP_KEY: 9}}))).status_code == 200
    assert publish(world, command(preview(world, {"expected_revision": 1, "rollback_revision": 0}))).status_code == 200
    response = publish(world, stale)
    assert response.status_code == 409 and response.json()["error"] == "settings_revision_conflict"
    current = response.json()["detail"]["current"]
    assert current["revision"] == 2 and current["values"] == {INTERVAL_KEY: 1800, DAILY_CAP_KEY: 8}
    response = post(world["admin"], "/api/admin/settings/preview", {"expected_revision": 0, "changes": {DAILY_CAP_KEY: 10}})
    assert response.status_code == 409 and response.json()["detail"]["current"] == current
    assert len(audits(world, "ok")) == 2


def test_reset_rollback_and_history_pagination_reference_real_versions(world):
    for revision, changes in ((0, {INTERVAL_KEY: 60}), (1, {DAILY_CAP_KEY: 2}), (2, {INTERVAL_KEY: None})):
        assert publish(world, command(preview(world, {"expected_revision": revision, "changes": changes}))).status_code == 200
    rollback = command(preview(world, {"expected_revision": 3, "rollback_revision": 1}))
    response = publish(world, rollback)
    assert response.status_code == 200 and response.json()["revision"] == 4
    assert response.json()["state"]["overrides"] == {INTERVAL_KEY: 60}
    first = world["admin"].get("/api/admin/settings/history?limit=2").json()
    assert [entry["revision"] for entry in first["items"]] == [4, 3]
    assert first["items"][0]["rollback_revision"] == 1
    second = world["admin"].get(f"/api/admin/settings/history?limit=2&before_revision={first['next_before_revision']}").json()
    assert [entry["revision"] for entry in second["items"]] == [2, 1] and second["next_before_revision"] is None


def test_invalid_inputs_leave_only_fixed_failure_codes_not_arbitrary_payloads(world):
    cases = [(True, {INTERVAL_KEY: 60}, "settings_bad_revision"),
             (0, {}, "settings_bad_changes"), (0, {INTERVAL_KEY: "fixture-secret"}, "settings_invalid_value"),
             (0, {"fixture-secret-key": 1}, "settings_unknown_key"), (0, {INTERVAL_KEY: None}, "settings_no_change")]
    for revision, changes, code in cases:
        response = post(world["admin"], "/api/admin/settings/preview", {"expected_revision": revision, "changes": changes})
        assert response.json()["error"] == code, response.text
    body = command(preview(world))
    response = publish(world, {**body, "reason": "secret"})
    assert response.status_code == 400 and response.json()["error"] == "missing_reason"
    text = json.dumps(audits(world))
    assert "fixture-secret" not in text and '"reason": "secret"' not in text
    assert not world["store"].all("SELECT * FROM platform_settings_versions")


def test_success_audit_failure_rolls_back_configuration_result_and_partial_audit(world, monkeypatch):
    body = command(preview(world))
    original = Store.execute
    def fail(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and "admin.settings.publish" in params and "ok" in params:
            raise OSError("fixture private failure detail")
        return result
    monkeypatch.setattr(Store, "execute", fail)
    response = publish(world, body)
    assert response.status_code == 500
    assert PlatformSettings(world["store"]).read()["revision"] == 0
    assert not world["store"].all("SELECT * FROM admin_operations") and not audits(world, "ok")
    failures = audits(world, "error")
    assert len(failures) == 1 and json.loads(failures[0]["meta"])["code"] == "admin_operation_failed"
    assert "fixture private failure detail" not in json.dumps(failures)
    monkeypatch.setattr(Store, "execute", original)
    assert publish(world, body).status_code == 200


@pytest.mark.parametrize("withdrawal", ["permission", "session", "freshness"])
def test_writer_lock_recheck_rejects_authority_withdrawn_after_initial_admission(world, monkeypatch, withdrawal):
    body = command(preview(world))
    original = admin_settings.require_platform
    happened = False
    def admitted(request, *args, **kwargs):
        nonlocal happened
        actor = original(request, *args, **kwargs)
        if request.url.path.endswith("/publish") and not happened:
            happened = True
            independent = Store(world["store"].root)
            if withdrawal == "permission":
                end_platform_grants(independent, world["root"])
            else:
                row = Sessions(independent).load(world["admin"].cookies.get(COOKIE_NAME), touch=False)
                if withdrawal == "session":
                    Sessions(independent).revoke(row["id"], "fixture_logout")
                else:
                    independent.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE id=?", (row["id"],))
        return actor
    monkeypatch.setattr(admin_settings, "require_platform", admitted)
    response = publish(world, body)
    assert response.status_code == {"permission": 404, "session": 401, "freshness": 403}[withdrawal], response.text
    assert PlatformSettings(world["store"]).read()["revision"] == 0
    assert not world["store"].all("SELECT * FROM admin_operations")


@pytest.mark.parametrize("same_key", [False, True])
def test_two_application_instances_serialize_same_revision_and_idempotency(world, same_key):
    from synwevo_identity.app import create_app
    other_api = create_app(str(world["store"].root), start_jobs=False)
    second = TestClient(other_api, raise_server_exceptions=False)
    second.cookies.update(world["admin"].cookies)
    first_body = command(preview(world))
    other_body = dict(first_body) if same_key else command(preview(world, client=second))
    barrier = threading.Barrier(2, timeout=10)
    def write(pair):
        client, body = pair
        barrier.wait()
        return publish(world, body, client=client)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [(world["admin"], first_body), (second, other_body)]))
    second.close()
    assert sorted(response.status_code for response in results) == ([200, 200] if same_key else [200, 409]), [r.text for r in results]
    if same_key:
        assert results[0].json() == results[1].json()
    else:
        denied = next(response for response in results if response.status_code == 409)
        assert denied.json()["error"] == "settings_revision_conflict" and denied.json()["detail"]["current"]["revision"] == 1
    assert PlatformSettings(world["store"]).read()["revision"] == 1
    assert len(audits(world, "ok")) == 1
    assert world["store"].one("SELECT COUNT(*) n FROM admin_operations WHERE action='admin.settings.publish'")["n"] == 1
