"""Atomic bootstrap through real HTTP and CLI, using synthetic tmp_path identities only."""
from __future__ import annotations

import concurrent.futures
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store
from synwevo_identity import cli

PW = "synthetic-bootstrap-transaction-passphrase-47"
ACCOUNT_TABLES = ("users", "user_passwords", "workspaces", "memberships", "workspace_apps",
                  "app_entitlements", "platform_role_grants", "sessions")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "isolated-bootstrap"
    for key, value in {"DATA_DIR": str(data), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "PUBLIC_URL": "http://testserver", "MAIL_MODE": "file",
                       "MAIL_DIR": str(tmp_path / "fixture-mail")}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    app = create_app(str(data), start_jobs=False)
    store = Store(data)
    return {"app": app, "store": store, "ident": Identity(store), "data": str(data)}


def rows(store):
    return {table: store.all(f"SELECT * FROM {table} ORDER BY rowid")
            for table in (*ACCOUNT_TABLES, "bootstrap_tokens", "audit_events")}


def assert_failure_audit(store, reason, *, result="denied", secrets=()):
    events = store.all("SELECT * FROM audit_events WHERE action='auth.bootstrap' AND result<>'ok' ORDER BY seq")
    assert len(events) == 1
    event = events[0]
    assert event["result"] == result and event["request_id"]
    assert event["actor_id"] is None and event["object_id"] is None and event["workspace_id"] is None
    assert json.loads(event["meta"]) == {"reason": reason}
    assert all(secret not in json.dumps(event) for secret in (PW, *secrets))
    return event


def claim(world, token, email="first@example.test"):
    with TestClient(world["app"], raise_server_exceptions=False) as client:
        return client.post("/api/auth/bootstrap", json={
            "token": token, "email": email, "password": PW, "display_name": "Fixture operator"})


def args(world, *, action="admin", email="cli-first@example.test", password=PW, force=False, ttl=900):
    return SimpleNamespace(data=world["data"], action=action, email=email, password=password,
                           name="Fixture CLI operator", force=force, ttl=ttl)


def inject_failure(monkeypatch, kind, audit_action):
    """Fail after the real grant or audit write, when partial persistence would be visible."""
    if kind == "grant":
        original = PlatformAccess.host_grant

        def grant_then_fail(self, *a, **kw):
            original(self, *a, **kw)
            raise OSError("synthetic post-grant failure")

        monkeypatch.setattr(PlatformAccess, "host_grant", grant_then_fail)
    else:
        original = Store.execute

        def audit_then_fail(self, sql, params=()):
            result = original(self, sql, params)
            if sql.startswith("INSERT INTO audit_events") and audit_action in params:
                raise OSError("synthetic post-audit failure")
            return result

        monkeypatch.setattr(Store, "execute", audit_then_fail)


@pytest.mark.parametrize("ttl", [None, True, 900.0, "900", -1, 0, 59, 3601])
def test_invalid_ttl_leaves_existing_token_usable_and_unchanged(world, ttl):
    ident, store = world["ident"], world["store"]
    current = ident.create_bootstrap_token()
    before = rows(store)
    with pytest.raises(ServiceError) as rejected:
        ident.create_bootstrap_token(ttl_s=ttl)
    assert rejected.value.status == 400 and rejected.value.code == "bad_bootstrap_ttl"
    assert rows(store) == before
    assert ident.peek_bootstrap_token(current)


@pytest.mark.parametrize("ttl", [60, 900, 3600, None], ids=["minimum", "default-explicit", "maximum", "default"])
def test_supported_ttl_and_default_are_bounded_single_use_hashed_tokens(world, ttl):
    ident, store = world["ident"], world["store"]
    superseded = ident.create_bootstrap_token()
    raw = ident.create_bootstrap_token(**({} if ttl is None else {"ttl_s": ttl}))
    row = store.one("SELECT * FROM bootstrap_tokens WHERE used_at IS NULL")
    assert row["expires_at"] - row["created_at"] == (900 if ttl is None else ttl)
    assert raw not in json.dumps(store.all("SELECT * FROM bootstrap_tokens"))
    assert not ident.peek_bootstrap_token(superseded)
    assert ident.consume_bootstrap_token(raw, user_id="fixture-consumer")
    assert not ident.consume_bootstrap_token(raw, user_id="fixture-other-consumer")


def test_two_http_claims_that_pass_precheck_create_exactly_one_account_and_workspace(world, monkeypatch):
    token = world["ident"].create_bootstrap_token()
    before = {table: {json.dumps(row, sort_keys=True) for row in world["store"].all(f"SELECT * FROM {table}")}
              for table in ACCOUNT_TABLES}
    rendezvous, local = threading.Barrier(2), threading.local()
    original = Identity.peek_bootstrap_token

    def both_pass_precheck(self, raw):
        accepted = original(self, raw)
        if not getattr(local, "arrived", False):
            local.arrived = True
            assert accepted
            rendezvous.wait(timeout=10)
        return accepted

    monkeypatch.setattr(Identity, "peek_bootstrap_token", both_pass_precheck)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim, world, token, email)
                   for email in ("first-a@example.test", "first-b@example.test")]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    denied = next(r for r in responses if r.status_code == 409)
    assert denied.json()["error"] == "already_bootstrapped"
    winner = next(r for r in responses if r.status_code == 200).json()["user"]["id"]
    store = world["store"]
    for table in ("users", "user_passwords", "workspaces", "memberships", "platform_role_grants", "sessions"):
        added = [row for row in store.all(f"SELECT * FROM {table}")
                 if json.dumps(row, sort_keys=True) not in before[table]]
        assert len(added) == 1, (table, added)
    assert len(world["ident"].platform_admins()) == 1
    assert store.one("SELECT used_by FROM bootstrap_tokens WHERE used_by=?", (winner,))
    assert store.one("SELECT COUNT(*) n FROM audit_events WHERE action='auth.bootstrap' AND result='ok'")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM audit_events WHERE action='admin.platform.grant'")["n"] == 1
    assert_failure_audit(store, "already_bootstrapped", secrets=(token, "first-a@example.test", "first-b@example.test"))
    assert not store.one("SELECT 1 FROM memberships m LEFT JOIN users u ON u.id=m.user_id WHERE u.id IS NULL")
    assert not store.one("SELECT 1 FROM workspaces w LEFT JOIN users u ON u.id=w.created_by "
                         "WHERE w.kind='personal' AND u.id IS NULL")
    for table in ("workspace_apps", "app_entitlements"):
        assert not store.one(f"SELECT 1 FROM {table} a LEFT JOIN workspaces w ON w.id=a.workspace_id WHERE w.id IS NULL")


def test_http_rechecks_first_admin_after_an_independent_cli_bootstrap(world, monkeypatch, capsys):
    token = world["ident"].create_bootstrap_token()
    original = Identity.peek_bootstrap_token
    created = False

    def host_bootstraps_after_precheck(self, raw):
        nonlocal created
        accepted = original(self, raw)
        if not created:
            created = True
            assert accepted
            assert cli.cmd_bootstrap(args(world)) == 0
        return accepted

    monkeypatch.setattr(Identity, "peek_bootstrap_token", host_bootstraps_after_precheck)
    response = claim(world, token)
    assert response.status_code == 409 and response.json()["error"] == "already_bootstrapped", response.text
    assert world["ident"].by_email("first@example.test") is None
    assert [u["email"] for u in world["ident"].platform_admins()] == ["cli-first@example.test"]
    assert world["store"].one("SELECT COUNT(*) n FROM workspaces WHERE kind='personal'")["n"] == 1
    assert original(world["ident"], token)  # CLI did not consume the web token; authority is the rejected condition.
    assert_failure_audit(world["store"], "already_bootstrapped", secrets=(token, "first@example.test"))
    assert PW not in capsys.readouterr().out


@pytest.mark.parametrize("kind", ["grant", "audit"])
def test_http_grant_or_audit_failure_rolls_back_every_identity_row_and_token(world, monkeypatch, kind):
    token = world["ident"].create_bootstrap_token()
    before = rows(world["store"])
    with monkeypatch.context() as fault:
        inject_failure(fault, kind, "auth.bootstrap")
        response = claim(world, token)
    assert response.status_code == 500
    after = rows(world["store"])
    if kind == "grant":
        failure = assert_failure_audit(world["store"], "bootstrap_failed", result="error",
                                       secrets=(token, "first@example.test", "synthetic post-grant failure"))
        assert after["audit_events"] == [*before["audit_events"], failure]
        after["audit_events"] = before["audit_events"]
    # When audit storage itself fails, even its attempted failure record rolls back.
    assert after == before
    assert world["ident"].peek_bootstrap_token(token)
    # Retrying the same email and token works: no orphan account blocks recovery.
    retry = claim(world, token)
    assert retry.status_code == 200, retry.text


def test_token_expiring_during_account_creation_rolls_back_the_account(world, monkeypatch):
    from synwevo_core import identity as identity_module
    token = world["ident"].create_bootstrap_token()
    expiry = world["store"].one("SELECT expires_at FROM bootstrap_tokens WHERE used_at IS NULL")["expires_at"]
    before = rows(world["store"])
    original = Identity.create_user

    def create_then_expire(self, *a, **kw):
        user = original(self, *a, **kw)
        monkeypatch.setattr(identity_module, "now", lambda: expiry + 1)
        return user

    monkeypatch.setattr(Identity, "create_user", create_then_expire)
    response = claim(world, token)
    assert response.status_code == 403 and response.json()["error"] == "bad_token", response.text
    failure = assert_failure_audit(world["store"], "bad_token", secrets=(token, "first@example.test"))
    after = rows(world["store"])
    assert after["audit_events"] == [*before["audit_events"], failure]
    after["audit_events"] = before["audit_events"]
    assert after == before


def test_session_failure_after_commit_keeps_the_audited_admin_and_allows_fresh_login(world, monkeypatch):
    token = world["ident"].create_bootstrap_token()

    def unavailable(*a, **kw):
        raise OSError("synthetic session delivery failure")

    with monkeypatch.context() as fault:
        fault.setattr(Sessions, "create", unavailable)
        response = claim(world, token)
    assert response.status_code == 500
    ident, store = world["ident"], world["store"]
    user = ident.by_email("first@example.test")
    assert user and PlatformAccess(store).is_usable_super_admin(user)
    assert not ident.peek_bootstrap_token(token)
    assert store.one("SELECT COUNT(*) n FROM audit_events WHERE action='auth.bootstrap' AND result='ok'")["n"] == 1
    assert not store.all("SELECT * FROM sessions")
    with TestClient(world["app"]) as client:
        login = client.post("/api/auth/login", json={"email": user["email"], "password": PW})
        assert login.status_code == 200 and client.get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("kind", ["grant", "audit"])
def test_cli_grant_or_audit_failure_leaves_no_account_workspace_or_success_record(world, monkeypatch, capsys, kind):
    before = rows(world["store"])
    with monkeypatch.context() as fault:
        inject_failure(fault, kind, "cli.bootstrap.admin")
        with pytest.raises(OSError, match="synthetic post-"):
            cli.cmd_bootstrap(args(world))
    assert rows(world["store"]) == before
    assert "已创建最高管理员" not in capsys.readouterr().out
    assert cli.cmd_bootstrap(args(world)) == 0
    output = capsys.readouterr().out
    assert "已创建最高管理员" in output and PW not in output


def test_cli_rechecks_after_hidden_password_prompt_and_retains_explicit_force(world, monkeypatch, capsys):
    prompts = []

    def hidden_prompt(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            assert cli.cmd_bootstrap(args(world, email="concurrent@example.test")) == 0
        return PW

    monkeypatch.setattr(cli.getpass, "getpass", hidden_prompt)
    assert cli.cmd_bootstrap(args(world, password=None)) == 1
    assert len(prompts) == 2 and all(PW not in prompt for prompt in prompts)
    assert world["ident"].by_email("cli-first@example.test") is None
    assert [u["email"] for u in world["ident"].platform_admins()] == ["concurrent@example.test"]
    assert cli.cmd_bootstrap(args(world, force=True)) == 0
    assert len(world["ident"].platform_admins()) == 2
    assert PW not in capsys.readouterr().out


def test_cli_token_audit_failure_keeps_previous_token_and_prints_no_new_secret(world, monkeypatch, capsys):
    previous = world["ident"].create_bootstrap_token()
    before = rows(world["store"])
    inject_failure(monkeypatch, "audit", "cli.bootstrap.token")
    with pytest.raises(OSError, match="synthetic post-audit"):
        cli.cmd_bootstrap(args(world, action="token"))
    assert rows(world["store"]) == before
    assert world["ident"].peek_bootstrap_token(previous)
    output = capsys.readouterr()
    assert not output.out and previous not in output.err


def test_cli_rejects_invalid_ttl_without_replacing_an_existing_token(world, capsys):
    token = world["ident"].create_bootstrap_token()
    before = rows(world["store"])
    assert cli.cmd_bootstrap(args(world, action="token", ttl=3601)) == 1
    output = capsys.readouterr()
    assert "60" in output.err and "3600" in output.err and not output.out
    assert rows(world["store"]) == before and world["ident"].peek_bootstrap_token(token)
