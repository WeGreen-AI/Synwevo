"""Confirmed account administration through the real isolated identity application.

All identities and pending credentials below are synthetic fixtures. No mail worker runs and
no production configuration, session or data is used. Assertions describe business outcomes,
including failures after an audit write and after a durable journal intent.
"""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import types
import uuid

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, now
from synwevo_identity import admin_operations
from synwevo_identity.mfa import Tickets

from .platform_fixture import end_platform_grants, make_platform_admin

PW = "isolated-account-operation-fixture-47"
REASON = "Isolated fixture: confirmed account lifecycle change"


def post(client, path, body, **kwargs):
    headers = {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or "", **kwargs.pop("headers", {})}
    return client.post(path, json=body, headers=headers, **kwargs)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "data"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "data"), start_jobs=False)
    store, clients = Store(tmp_path / "data"), []
    ident = Identity(store)
    root = make_platform_admin(store, ident.create_user("root@example.test", PW, status="active"))
    target = ident.create_user("target@example.test", PW, status="active")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), target["id"]))

    def login(user):
        client = TestClient(api, raise_server_exceptions=False)
        clients.append(client)
        response = post(client, "/api/auth/login", {"email": user["email"], "password": PW})
        assert response.status_code == 200, response.text
        return client

    def reauth(client):
        response = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
        assert response.status_code == 200, response.text

    admin, ordinary = login(root), login(target)
    reauth(admin)
    yield {"api": api, "store": store, "ident": ident, "root": root, "target": target,
           "admin": admin, "ordinary": ordinary, "login": login, "reauth": reauth}
    for client in clients:
        client.close()


def preview(w, *, client=None, target=None, status="suspended"):
    response = post(client or w["admin"], f"/api/admin/users/{target or w['target']['id']}/status-preview",
                    {"status": status})
    assert response.status_code == 200, response.text
    return response.json()


def command(plan, *, status="suspended", key=None):
    return {"status": status, "reason": REASON, "preview_token": plan["preview_token"],
            "idempotency_key": key or uuid.uuid4().hex}


def execute(w, body, *, client=None, target=None, **kwargs):
    return post(client or w["admin"], f"/api/admin/users/{target or w['target']['id']}/status", body, **kwargs)


def operation_audits(w):
    return w["store"].all("SELECT * FROM audit_events WHERE action='admin.user.status' ORDER BY seq")


def seed_pending_credentials(w):
    """Synthetic in-flight credentials for each existing authentication channel."""
    store, uid, ts = w["store"], w["target"]["id"], now()
    raw = "fixture-access-token-never-issued-outside-this-test"
    store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,issued_at,"
                  "access_expires_at,refresh_expires_at,family_id) VALUES (?,?,?,?,?,'openid',?,?,?,?)",
                  ("fixture-token", "docweave-web", uid, oidc._sha(raw), oidc._sha(raw + "-refresh"),
                   ts, ts + 900, ts + 3600, "fixture-token"))
    code = oidc.Grants(store).issue_code(client_id="docweave-web", user_id=uid,
        redirect_uri="http://testserver/callback", scope="openid", nonce=None,
        code_challenge=None, code_challenge_method=None, auth_time=ts, amr="password",
        central_session_id=None, workspace_id=None)
    ticket = Tickets(store).issue(uid)
    store.execute("INSERT INTO email_tokens (id,user_id,kind,token_hash,created_at,expires_at) "
                  "VALUES ('fixture-email',?,'reset','fixture-email-hash',?,?)", (uid, ts, ts + 600))
    store.execute("INSERT INTO webauthn_challenges (id,user_id,purpose,challenge,rp_id,origin,created_at,expires_at) "
                  "VALUES ('fixture-challenge',?,'authenticate','fixture-challenge-value','testserver',"
                  "'http://testserver',?,?)", (uid, ts, ts + 600))
    store.execute("INSERT INTO account_recoveries (id,user_id,token_hash,created_at,expires_at) "
                  "VALUES ('fixture-recovery',?,'fixture-recovery-hash',?,?)", (uid, ts, ts + 600))
    return {"access": raw, "code": code, "ticket": ticket}


def assert_all_old_access_ended(store, uid):
    for table in ("sessions", "oauth_tokens"):
        assert not store.one(f"SELECT 1 FROM {table} WHERE user_id=? AND revoked_at IS NULL", (uid,)), table
    for table in ("oauth_codes", "email_tokens", "account_recoveries"):
        assert not store.one(f"SELECT 1 FROM {table} WHERE user_id=? AND used_at IS NULL", (uid,)), table
    for table in ("mfa_tickets", "webauthn_challenges"):
        assert not store.one(f"SELECT 1 FROM {table} WHERE user_id=?", (uid,)), table


def test_preview_reports_real_impact_without_changing_the_account_or_credentials(world):
    pending = seed_pending_credentials(world)
    started = time.time()
    plan = preview(world)
    assert plan["action"] == "admin.user.status" and plan["risk"] == "high" and plan["scope"] == "platform"
    assert plan["target"] == {"id": world["target"]["id"], "email": "target@example.test", "status": "active"}
    assert plan["result_status"] == "suspended"
    assert plan["impact"] == {"sessions": 1, "tokens": 1, "pending_authentication": 5}
    assert started + 299 <= plan["expires_at"] <= time.time() + 301
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert oidc.Grants(world["store"]).load_access(pending["access"])
    assert Tickets(world["store"]).load(pending["ticket"])
    assert not operation_audits(world)
    assert not world["store"].all("SELECT * FROM admin_operations")
    assert not journal.read(world["store"].root)
    # Preview is POST-only: prefetch and link scanning cannot execute or confirm it.
    assert world["admin"].get(f"/api/admin/users/{world['target']['id']}/status-preview").status_code in (404, 405)
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"


def test_execution_requires_confirmation_and_a_fresh_admin_reauthentication(world):
    path = f"/api/admin/users/{world['target']['id']}/status"
    body = {"status": "suspended", "reason": REASON}
    response = post(world["admin"], path, body)
    assert response.status_code == 400 and response.json()["error"] == "admin_confirmation_required"
    plan = preview(world)
    fresh_login = world["login"](world["root"])
    response = execute(world, command(plan), client=fresh_login)
    assert response.status_code == 403 and response.json()["error"] == "reauth_required"
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"


@pytest.mark.parametrize("alteration", ["actor", "target", "status", "origin", "signature", "expired"])
def test_confirmation_cannot_be_rebound_or_replayed_after_expiry(world, monkeypatch, alteration):
    plan = preview(world)
    body, kwargs = command(plan), {}
    if alteration == "actor":
        other = make_platform_admin(world["store"], world["ident"].create_user("other@example.test", PW, status="active"))
        kwargs["client"] = world["login"](other)
        world["reauth"](kwargs["client"])
    elif alteration == "target":
        kwargs["target"] = world["ident"].create_user("second-target@example.test", PW, status="active")["id"]
    elif alteration == "status":
        world["store"].execute("UPDATE users SET status='suspended' WHERE id=?", (world["target"]["id"],))
        body["status"] = "active"
    elif alteration == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif alteration == "signature":
        body["preview_token"] = plan["preview_token"] + "tampered"
    else:
        monkeypatch.setattr(admin_operations, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    response = execute(world, body, **kwargs)
    expected = "admin_preview_expired" if alteration == "expired" else "admin_preview_invalid"
    assert response.status_code == 409 and response.json()["error"] == expected, response.text
    assert world["ident"].get_user(world["target"]["id"])["status"] == ("suspended" if alteration == "status" else "active")
    assert not world["store"].all("SELECT * FROM admin_operations")
    assert not journal.read(world["store"].root)


@pytest.mark.parametrize("change", ["new_session", "verification", "account_version"])
def test_changed_resources_invalidate_the_previous_impact_confirmation(world, change):
    plan = preview(world)
    store, uid = world["store"], world["target"]["id"]
    if change == "new_session":
        Sessions(store).create(uid, workspace_id=None, amr="password")
    elif change == "verification":
        store.execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (uid,))
    else:
        store.execute("UPDATE users SET updated_at=updated_at+1 WHERE id=?", (uid,))
    response = execute(world, command(plan))
    assert response.status_code == 409 and response.json()["error"] == "admin_preview_stale", response.text
    assert world["ident"].get_user(uid)["status"] == "active"
    assert not store.all("SELECT * FROM admin_operations")


def test_revoking_authority_invalidates_a_prepared_action_and_old_idempotency_results(world):
    actor = make_platform_admin(world["store"], world["ident"].create_user("access@example.test", PW, status="active"), role="access_admin")
    client = world["login"](actor)
    world["reauth"](client)
    prepared = command(preview(world, client=client))
    end_platform_grants(world["store"], actor)
    assert execute(world, prepared, client=client).status_code == 404
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"
    make_platform_admin(world["store"], actor, role="access_admin")
    prepared = command(preview(world, client=client))
    assert execute(world, prepared, client=client).status_code == 200
    end_platform_grants(world["store"], actor)
    assert execute(world, prepared, client=client).status_code == 404


def test_suspension_withdraws_every_old_credential_and_restore_does_not_revive_them(world):
    pending = seed_pending_credentials(world)
    response = execute(world, command(preview(world)))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "succeeded" and result["status"] == "suspended"
    assert result["sessions_revoked"] == 1 and result["tokens_revoked"] == 1 and result["journaled"] is True
    uid, store = world["target"]["id"], world["store"]
    assert_all_old_access_ended(store, uid)
    assert world["ordinary"].get("/api/auth/me").status_code == 401
    restored = execute(world, command(preview(world, status="active"), status="active"))
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    assert_all_old_access_ended(store, uid)
    assert oidc.Grants(store).load_access(pending["access"]) is None
    assert Tickets(store).load(pending["ticket"]) is None
    assert world["ordinary"].get("/api/auth/me").status_code == 401
    assert world["login"](world["target"]).get("/api/auth/me").status_code == 200
    audits = operation_audits(world)
    assert [json.loads(row["meta"])["before"]["status"] for row in audits] == ["active", "suspended"]
    assert [json.loads(row["meta"])["after"]["status"] for row in audits] == ["suspended", "active"]
    assert [json.loads(row["meta"])["changed"] for row in audits] == [["status"], ["status"]], "G24: the unified change record"
    assert all(row["actor_id"] == world["root"]["id"] and row["object_id"] == uid and row["workspace_id"] is None for row in audits)
    records = store.all("SELECT * FROM admin_operations ORDER BY created_at")
    assert {row["id"] for row in records} == {json.loads(row["meta"])["operation_id"] for row in audits}
    assert all(row["request_id"] for row in audits)
    rendered = json.dumps(audits) + json.dumps(records) + journal.path(store.root).read_text()
    for secret in (PW, pending["access"], pending["code"], pending["ticket"], "fixture-email-hash", "fixture-recovery-hash"):
        assert secret not in rendered


@pytest.mark.parametrize("verified, expected", [(True, "active"), (False, "pending")])
def test_restoring_preserves_whether_email_verification_was_completed(world, verified, expected):
    store, uid = world["store"], world["target"]["id"]
    store.execute("UPDATE users SET status='suspended',email_verified_at=? WHERE id=?", (now() if verified else None, uid))
    plan = preview(world, status="active")
    assert plan["result_status"] == expected
    response = execute(world, command(plan, status="active"))
    assert response.status_code == 200 and response.json()["status"] == expected
    assert world["ident"].get_user(uid)["status"] == expected
    assert bool(world["ident"].get_user(uid)["email_verified_at"]) is verified


@pytest.mark.parametrize("status", ["active", "pending", "pending_delete"])
def test_restore_never_activates_an_account_outside_the_suspended_state(world, status):
    uid = world["target"]["id"]
    world["store"].execute("UPDATE users SET status=? WHERE id=?", (status, uid))
    response = post(world["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "active"})
    assert response.status_code == 409 and response.json()["error"] == "not_suspended"
    response = execute(world, {"status": "active", "reason": REASON})
    assert response.status_code == 409 and response.json()["error"] == "not_suspended"
    assert world["ident"].get_user(uid)["status"] == status


def test_identical_retries_return_the_original_result_without_duplicate_audit_or_journal(world, monkeypatch):
    plan, store = preview(world), world["store"]
    body = command(plan)
    first = execute(world, body)
    assert first.status_code == 200, first.text
    monkeypatch.setattr(admin_operations, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    retry = execute(world, {**body, "reason": "  " + REASON + "  "})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(store.all("SELECT * FROM admin_operations")) == len(operation_audits(world)) == len(journal.read(store.root)) == 1
    assert json.loads(store.one("SELECT result_json FROM admin_operations")["result_json"]) == first.json()


@pytest.mark.parametrize("change", ["status", "reason", "preview_token", "target"])
def test_reusing_a_key_with_another_canonical_request_is_a_conflict(world, change):
    body = command(preview(world))
    assert execute(world, body).status_code == 200
    kwargs = {}
    if change == "target":
        kwargs["target"] = world["ident"].create_user("other-target@example.test", PW, status="active")["id"]
    else:
        body[change] = {"status": "active", "reason": REASON + " changed", "preview_token": "another-preview"}[change]
    response = execute(world, body, **kwargs)
    assert response.status_code == 409 and response.json()["error"] == "idempotency_conflict", response.text
    assert len(world["store"].all("SELECT * FROM admin_operations")) == 1
    assert len([row for row in operation_audits(world) if row["result"] == "ok"]) == 1


def test_concurrent_retries_commit_exactly_one_result(world):
    clients = [world["admin"], world["login"](world["root"])]
    world["reauth"](clients[1])
    body, barrier = command(preview(world)), threading.Barrier(2)

    def submit(client):
        barrier.wait(timeout=5)
        return execute(world, body, client=client)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, clients))
    assert [response.status_code for response in replies] == [200, 200], [response.text for response in replies]
    assert replies[0].json() == replies[1].json()
    assert len(world["store"].all("SELECT * FROM admin_operations")) == len(operation_audits(world)) == 1
    assert len(journal.read(world["store"].root)) == 1


def test_audit_storage_failure_rolls_back_state_credentials_and_the_partial_audit(world, monkeypatch):
    seed_pending_credentials(world)
    body = command(preview(world))
    original = Store.execute

    def fail_after_audit_write(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and "admin.user.status" in params and "ok" in params:
            raise OSError("synthetic audit persistence failure")
        return result

    monkeypatch.setattr(Store, "execute", fail_after_audit_write)
    response = execute(world, body)
    assert response.status_code == 500
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert not world["store"].all("SELECT * FROM admin_operations")
    assert not journal.read(world["store"].root)
    assert [row["result"] for row in operation_audits(world)] == ["error"]
    assert json.loads(operation_audits(world)[0]["meta"])["code"] == "admin_operation_failed"


def test_journal_failure_blocks_the_change_and_rolls_back_its_success_audit(world, monkeypatch):
    pending = seed_pending_credentials(world)
    body = command(preview(world))

    def unavailable(*args, **kwargs):
        raise OSError("synthetic journal unavailable")

    monkeypatch.setattr(journal, "record_account", unavailable)
    response = execute(world, body)
    assert response.status_code == 503 and response.json()["error"] == "journal_unavailable"
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert oidc.Grants(world["store"]).load_access(pending["access"])
    assert Tickets(world["store"]).load(pending["ticket"])
    assert not world["store"].all("SELECT * FROM admin_operations")
    assert [row["result"] for row in operation_audits(world)] == ["error"]
    assert json.loads(operation_audits(world)[0]["meta"])["code"] == "journal_unavailable"


def test_journal_receipt_preserves_explicit_restore_but_replays_into_an_older_backup(world, tmp_path):
    seed_pending_credentials(world)
    older = Store(tmp_path / "older-isolated-backup")
    with world["store"].conn() as source, older.conn() as destination:
        source.backup(destination)
    response = execute(world, command(preview(world)))
    assert response.status_code == 200, response.text
    operation_id = response.json()["operation_id"]
    restored = execute(world, command(preview(world, status="active"), status="active"))
    assert restored.status_code == 200 and restored.json()["status"] == "active"
    entries = journal.read(world["store"].root, since=time.time() + 3600)
    assert len(entries) == 1 and entries[0]["account_event_id"] == operation_id
    receipt = "account_withdrawal:" + operation_id
    assert world["store"].one("SELECT value FROM meta WHERE key=?", (receipt,))["value"] == "1"
    assert not older.one("SELECT value FROM meta WHERE key=?", (receipt,))
    journal.apply(world["store"], entries)
    assert world["ident"].get_user(world["target"]["id"])["status"] == "active"
    journal.apply(older, entries)
    assert older.one("SELECT status FROM users WHERE id=?", (world["target"]["id"],))["status"] == "suspended"
    assert_all_old_access_ended(older, world["target"]["id"])
    assert older.one("SELECT value FROM meta WHERE key=?", (receipt,))["value"] == "1"
    assert journal.apply(older, entries) == {}


def test_durable_intent_whose_transaction_failed_is_voided_not_replayed(world, monkeypatch):
    """A failure after fsync leaves an intent, but no successful operation or receipt. The rollback
    proves the attempt never committed and voids it (journal._intent), so a replay leaves the
    account as it is."""
    seed_pending_credentials(world)
    body = command(preview(world))
    original = journal.record_account

    def fail_after_durable_intent(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("synthetic failure after journal fsync, before transaction commit")

    monkeypatch.setattr(journal, "record_account", fail_after_durable_intent)
    response = execute(world, body)
    monkeypatch.setattr(journal, "record_account", original)
    assert response.status_code == 503 and response.json()["error"] == "journal_unavailable"
    store, uid = world["store"], world["target"]["id"]
    assert world["ident"].get_user(uid)["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert not store.all("SELECT * FROM admin_operations")
    intent, void = journal.read(store.root, since=time.time() + 3600)
    assert intent["kind"] == "user_suspended"
    assert (void["kind"], void["attempt_id"], void["voided_kind"]) == (journal.VOID_KIND, intent["attempt_id"],
                                                                       "user_suspended")
    receipt = "account_withdrawal:" + intent["account_event_id"]
    assert not store.one("SELECT value FROM meta WHERE key=?", (receipt,))
    assert journal.apply(store, [intent, void]) == {journal.VOID_KIND: 1}
    assert world["ident"].get_user(uid)["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert not store.one("SELECT value FROM meta WHERE key=?", (receipt,))


def test_durable_intent_nobody_voided_is_replayed(world):
    """The process stopped between the line and its COMMIT (FIXTURE: the line as `_intent` appends
    it, with no transaction behind it, so no rollback voids it): no receipt, no void — replayed,
    fail-closed."""
    seed_pending_credentials(world)
    store, uid = world["store"], world["target"]["id"]
    journal.record(store, "user_suspended", user_id=uid, account_event_id="ao_fixture_unvoided",
                   attempt_id="ja_fixture_unvoided")
    entries = journal.read(store.root, since=time.time() + 3600)
    assert len(entries) == 1
    receipt = "account_withdrawal:ao_fixture_unvoided"
    assert not store.one("SELECT value FROM meta WHERE key=?", (receipt,))
    journal.apply(store, entries)
    assert world["ident"].get_user(uid)["status"] == "suspended"
    assert_all_old_access_ended(store, uid)
    assert store.one("SELECT value FROM meta WHERE key=?", (receipt,))["value"] == "1"
