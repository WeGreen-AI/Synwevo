"""The account system over HTTP, in session mode.

The assertions that matter are the negative ones: a signed-in account cannot see the legacy
workspace, a suspended account stops working on its next request rather than at session expiry,
and a state-changing request without the CSRF header is refused.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")       # TestClient speaks http
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(d), start_jobs=False)
    store = Store(d)
    return {"client": TestClient(api), "store": store, "ident": Identity(store)}


def _login(c: TestClient, email: str, password: str = PW):
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    return r


def _csrf(c: TestClient) -> dict:
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


# ---------------------------------------------------------------- signed out
def test_everything_but_the_login_surface_needs_a_session(env):
    c = env["client"]
    assert c.get("/api/auth/state").status_code == 200
    for path in ("/api/projects", "/api/templates", "/api/auth/me", "/api/auth/sessions"):
        assert c.get(path).status_code == 401, path


def test_login_failures_look_identical(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    unknown = _login(c, "nobody@example.com")
    wrong = _login(c, "alice@example.com", "the-wrong-passphrase-x")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"] == wrong.json()["error"] == "bad_credentials"
    assert unknown.json()["message"] == wrong.json()["message"]


def test_suspended_account_cannot_log_in_and_says_nothing_extra(env):
    c, ident = env["client"], env["ident"]
    u = ident.create_user("alice@example.com", PW, status="active")
    ident.set_status(u["id"], "suspended")
    r = _login(c, "alice@example.com")
    assert r.status_code == 401 and r.json()["error"] == "bad_credentials"
    assert "suspend" not in r.text and "停用" not in r.text


# ---------------------------------------------------------------- signed in
def test_login_issues_cookies_and_me_reports_the_account(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    r = _login(c, "alice@example.com")
    assert r.status_code == 200
    assert c.cookies.get(COOKIE_NAME) and c.cookies.get(CSRF_COOKIE)

    me = c.get("/api/auth/me").json()
    assert me["email"] == "alice@example.com" and me["platform_role"] == "none"
    assert len(me["workspaces"]) == 1 and me["workspaces"][0]["role"] == "owner"


def test_signed_in_user_sees_only_their_own_workspace_data(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    assert c.get("/api/projects").status_code == 200
    assert c.get("/api/projects").json() == []          # a brand new personal workspace is empty


def test_legacy_workspace_is_invisible_to_a_normal_account(env):
    """The migration's core promise, over HTTP: historical projects do not appear for anyone."""
    c, ident, store = env["client"], env["ident"], env["store"]
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    store.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant) "
                  "VALUES ('p_legacy','历史项目',?,?,'default')", (ts, ts))

    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    me = c.get("/api/auth/me").json()
    assert "default" not in [w["id"] for w in me["workspaces"]]
    assert c.get("/api/projects").json() == [], "历史项目不应出现"
    assert c.get("/api/projects/p_legacy").status_code == 404


def test_a_user_with_no_workspace_sees_nothing_rather_than_the_legacy_data(env):
    """The dangerous fallback: an unset workspace must not resolve to tenant 'default'."""
    c, ident, store = env["client"], env["ident"], env["store"]
    ts = now()
    store.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant) "
                  "VALUES ('p_legacy','历史项目',?,?,'default')", (ts, ts))
    u = ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    # drop them out of every workspace, as a removal would
    store.execute("DELETE FROM memberships WHERE user_id=?", (u["id"],))
    assert c.get("/api/projects").json() == []
    assert c.get("/api/projects/p_legacy").status_code == 404


# ---------------------------------------------------------------- CSRF
def test_state_changing_request_without_the_csrf_header_is_refused(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    bare = c.post("/api/projects", json={"name": "无 CSRF"})
    assert bare.status_code == 403 and bare.json()["error"] == "csrf_failed"
    ok = c.post("/api/projects", json={"name": "有 CSRF"}, headers=_csrf(c))
    assert ok.status_code == 200


def test_csrf_header_must_match_the_cookie(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    r = c.post("/api/projects", json={"name": "x"}, headers={CSRF_HEADER: "not-the-token"})
    assert r.status_code == 403


def test_reads_do_not_require_csrf(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    assert c.get("/api/projects").status_code == 200


# ---------------------------------------------------------------- revocation
def test_suspension_takes_effect_on_the_next_request(env):
    c, ident = env["client"], env["ident"]
    u = ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    assert c.get("/api/projects").status_code == 200
    ident.set_status(u["id"], "suspended")
    assert c.get("/api/projects").status_code == 401, "停用必须立刻生效，而不是等会话过期"


def test_logout_ends_the_session_immediately(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    assert c.post("/api/auth/logout", headers=_csrf(c)).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_sessions_can_be_listed_and_others_revoked(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com")
    other = TestClient(c.app)
    _login(other, "alice@example.com")
    assert len(c.get("/api/auth/sessions").json()["items"]) == 2

    assert c.post("/api/auth/sessions/revoke-others", headers=_csrf(c)).json()["revoked"] == 1
    assert c.get("/api/auth/me").status_code == 200          # mine survives
    assert other.get("/api/auth/me").status_code == 401      # the other one is gone


def test_a_user_cannot_revoke_someone_elses_session(env):
    c, ident = env["client"], env["ident"]
    ident.create_user("alice@example.com", PW, status="active")
    ident.create_user("bob@example.com", PW, status="active")
    _login(c, "alice@example.com")
    bob = TestClient(c.app)
    _login(bob, "bob@example.com")
    bob_sid = bob.get("/api/auth/sessions").json()["items"][0]["id"]

    r = c.delete(f"/api/auth/sessions/{bob_sid}", headers=_csrf(c))
    assert r.status_code == 404, "不能撤销别人的会话"
    assert bob.get("/api/auth/me").status_code == 200


# ---------------------------------------------------------------- bootstrap
def test_bootstrap_requires_a_valid_token_and_runs_once(env):
    c, ident = env["client"], env["ident"]
    assert c.get("/api/auth/state").json()["bootstrap_required"] is True

    bad = c.post("/api/auth/bootstrap", json={"token": "made-up", "email": "root@example.com",
                                              "password": PW})
    assert bad.status_code == 403
    # Person accounts only: the composed server creates Synwevo Docs' application identity at
    # start-up (CI-75, `app_accounts`), which is not something a bootstrap could have left behind.
    assert ident.store.one("SELECT COUNT(*) AS n FROM users WHERE id NOT IN (SELECT user_id FROM app_accounts)")["n"] == 0, "失败的引导不能留下账户"

    raw = ident.create_bootstrap_token()
    ok = c.post("/api/auth/bootstrap", json={"token": raw, "email": "root@example.com",
                                             "password": PW, "display_name": "运维"})
    assert ok.status_code == 200 and ok.json()["user"]["platform_role"] == "admin"
    assert c.get("/api/auth/state").json()["bootstrap_required"] is False

    again = c.post("/api/auth/bootstrap", json={"token": raw, "email": "other@example.com",
                                                "password": PW})
    assert again.status_code == 409


def test_bootstrap_rejects_a_weak_password_without_consuming_the_token(env):
    c, ident = env["client"], env["ident"]
    raw = ident.create_bootstrap_token()
    weak = c.post("/api/auth/bootstrap", json={"token": raw, "email": "root@example.com",
                                               "password": "short"})
    assert weak.status_code == 400 and weak.json()["error"] == "weak_password"
    # the token survives a rejected attempt, so an operator is not sent back to the CLI
    good = c.post("/api/auth/bootstrap", json={"token": raw, "email": "root@example.com",
                                               "password": PW})
    assert good.status_code == 200


# ---------------------------------------------------------------- audit
def test_login_success_and_failure_are_both_audited_without_secrets(env):
    c, ident, store = env["client"], env["ident"], env["store"]
    ident.create_user("alice@example.com", PW, status="active")
    _login(c, "alice@example.com", "the-wrong-passphrase-x")
    _login(c, "alice@example.com")
    rows = store.all("SELECT * FROM audit_events WHERE action='auth.login' ORDER BY seq")
    assert [r["result"] for r in rows] == ["denied", "ok"]
    blob = " ".join(str(r) for r in rows)
    assert PW not in blob and "the-wrong-passphrase-x" not in blob, "审计里不能出现口令"
    assert all(r["request_id"] for r in rows)


# ---------------------------------------------------------------- backwards compatibility
def test_basic_mode_still_works_untouched(tmp_path, monkeypatch):
    """The deployed instance runs SYNWEVO_UI_AUTH=basic; adding accounts must not break it."""
    import base64
    d = tmp_path / "b"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "basic")
    from synwevo_core.auth import Auth
    Auth(Store(d)).add_ui_user("probe", "probe-pass-123")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    c = TestClient(app_mod.create_app(str(d), start_jobs=False))
    assert c.get("/api/projects").status_code == 401
    h = {"Authorization": "Basic " + base64.b64encode(b"probe:probe-pass-123").decode()}
    assert c.get("/api/projects", headers=h).status_code == 200


@pytest.mark.parametrize("mode", ["none", "basic", "proxy", "session"])
def test_auth_state_reports_the_mode_so_the_ui_does_not_guess(tmp_path, monkeypatch, mode):
    """A deployment that does not use accounts must say so.

    These routes are mounted in every auth mode, so /api/auth/state answers 200 on a `basic`
    deployment too. The UI inferred "accounts are in use" from that success and redirected
    people who had already signed in with the shared password to a first-run bootstrap screen —
    on the live instance. Whether the account system is active is the server's answer.
    """
    import base64
    d = tmp_path / f"m-{mode}"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    from synwevo_core.auth import Auth
    from synwevo_core.store import Store
    Auth(Store(d)).add_ui_user("probe", "probe-pass-123")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    c = TestClient(app_mod.create_app(str(d), start_jobs=False))
    h = {"Authorization": "Basic " + base64.b64encode(b"probe:probe-pass-123").decode()} if mode == "basic" else {}
    body = c.get("/api/auth/state", headers=h).json()
    assert body["mode"] == mode
