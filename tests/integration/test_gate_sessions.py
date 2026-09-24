"""Session clocks, request hygiene and startup refusals at the platform gate."""
from __future__ import annotations

import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import sessions as sessions_mod
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_HEADER, Sessions, csrf_ok
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "s")


def _row(store, sid):
    return store.one("SELECT last_seen_at, idle_expires_at, absolute_expires_at FROM sessions WHERE id=?", (sid,))


# ---------------------------------------------------------------- idle clock
def test_an_admin_session_keeps_its_one_hour_idle_limit_after_being_used(store):
    """create() gave admins 1 hour, and the first request extended it to the 12-hour default."""
    u = Identity(store).create_user("root@example.com", PW, status="active")
    raw, row = Sessions(store).create(u["id"], workspace_id=None, amr="password", admin=True)
    # A request two minutes later.
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-120, idle_expires_at=idle_expires_at-120 "
                  "WHERE id=?", (row["id"],))
    Sessions(store).load(raw)
    after = _row(store, row["id"])
    assert after["idle_expires_at"] <= time.time() + sessions_mod.ADMIN_IDLE_SECONDS + 1, \
        "管理员会话被第一次请求延长到了普通会话的空闲期"
    assert after["idle_expires_at"] >= time.time() + sessions_mod.ADMIN_IDLE_SECONDS - 5


def test_an_ordinary_session_is_still_extended_by_its_own_window(store):
    u = Identity(store).create_user("alice@example.com", PW, status="active")
    raw, row = Sessions(store).create(u["id"], workspace_id=None, amr="password")
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-600, idle_expires_at=idle_expires_at-600 "
                  "WHERE id=?", (row["id"],))
    Sessions(store).load(raw)
    after = _row(store, row["id"])
    assert abs(after["idle_expires_at"] - (time.time() + sessions_mod.IDLE_SECONDS)) < 5


def test_back_to_back_requests_do_not_each_write(store):
    """A write lock per request (every asset, every poll) bought nothing: the deadline only
    needs refreshing when it has drifted."""
    u = Identity(store).create_user("alice@example.com", PW, status="active")
    raw, row = Sessions(store).create(u["id"], workspace_id=None, amr="password")
    before = _row(store, row["id"])
    for _ in range(5):
        assert Sessions(store).load(raw) is not None
    assert _row(store, row["id"]) == before


def test_the_absolute_deadline_still_caps_the_idle_one(store):
    u = Identity(store).create_user("alice@example.com", PW, status="active")
    raw, row = Sessions(store).create(u["id"], workspace_id=None, amr="password")
    store.execute("UPDATE sessions SET absolute_expires_at=? WHERE id=?", (time.time() + 30, row["id"]))
    Sessions(store).load(raw)
    after = _row(store, row["id"])
    assert after["idle_expires_at"] <= after["absolute_expires_at"]


# ---------------------------------------------------------------- CSRF with hostile input
def test_a_non_ascii_csrf_header_is_refused_not_a_crash():
    assert csrf_ok("token", "tökén") is False
    assert csrf_ok("tökén", "tökén") is True


@pytest.fixture()
def session_app(tmp_path, monkeypatch):
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="all", start_jobs=False)
    store = Store(d)
    Identity(store).create_user("alice@example.com", PW, status="active")
    c = TestClient(api, raise_server_exceptions=False)
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return {"c": c, "store": store}


def test_a_non_ascii_csrf_header_over_http_is_a_403(session_app):
    c = session_app["c"]
    r = c.post("/api/projects", json={"name": "x"}, headers={CSRF_HEADER: "é".encode("utf-8")})
    assert r.status_code == 403 and r.json()["error"] == "csrf_failed"


def test_static_assets_do_not_touch_the_session(session_app):
    c, store = session_app["c"], session_app["store"]
    sid = store.one("SELECT id FROM sessions")["id"]
    store.execute("UPDATE sessions SET last_seen_at=last_seen_at-3600, idle_expires_at=idle_expires_at-3600")
    before = _row(store, sid)
    c.get("/assets/does-not-matter.js")
    assert _row(store, sid) == before


# ---------------------------------------------------------------- audit address
def test_the_audit_log_records_the_forwarded_address_only_behind_a_trusted_proxy(tmp_path, monkeypatch):
    d = tmp_path / "a"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="all", start_jobs=False)
    store = Store(d)
    Identity(store).create_user("alice@example.com", PW, status="active")
    c = TestClient(api)
    fwd = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}

    monkeypatch.delenv("SYNWEVO_TRUST_PROXY", raising=False)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}, headers=fwd)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}, headers=fwd)
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "2")
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}, headers=fwd)
    ips = [r["ip"] for r in store.all("SELECT ip FROM audit_events WHERE action='auth.login' ORDER BY seq")]
    assert ips[0] != "203.0.113.9", "未声明可信代理时不能相信 X-Forwarded-For"
    assert ips[1] == "10.0.0.1", "只信任一跳时不能把调用者插入的最左侧地址当作来源"
    assert ips[-1] == "203.0.113.9"


# ---------------------------------------------------------------- startup refusals
@pytest.mark.parametrize("bad", ["docs", "app,accounts", "gateway"])
def test_an_unknown_role_refuses_to_start(tmp_path, monkeypatch, bad):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "r"))
    monkeypatch.setenv("SYNWEVO_ROLE", bad)
    from synwevo_server.app import create_app
    with pytest.raises(RuntimeError) as e:
        create_app(str(tmp_path / "r"), start_jobs=False)
    assert "SYNWEVO_ROLE" in str(e.value)
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path / "r"), role=bad, start_jobs=False)


@pytest.mark.parametrize("argv,refused", [
    (["uvicorn", "synwevo_docs.api.main:app", "--host", "0.0.0.0"], True),
    (["uvicorn", "synwevo_docs.api.main:app", "--host=192.168.1.20"], True),
    (["gunicorn", "-b", "0.0.0.0:8765", "synwevo_server.main:app"], True),
    (["uvicorn", "synwevo_docs.api.main:app", "--host", "127.0.0.1"], False),
    (["uvicorn", "synwevo_docs.api.main:app"], False),
])
def test_the_standalone_entry_points_check_where_they_listen(monkeypatch, argv, refused):
    """`uvicorn synwevo_docs.api.main:app --host 0.0.0.0` used to start with no refusal and
    serve the legacy workspace to the network under the default `none` mode."""
    from synwevo_core import auth
    from synwevo_core.http import check_standalone_listen
    monkeypatch.delenv("SYNWEVO_UI_AUTH", raising=False)
    monkeypatch.delenv("SYNWEVO_HOST", raising=False)
    monkeypatch.setattr(auth, "in_container", lambda: False)
    if refused:
        with pytest.raises(SystemExit):
            check_standalone_listen(argv)
    else:
        check_standalone_listen(argv)


def test_the_standalone_docs_entry_point_runs_the_check(monkeypatch):
    import ast
    import inspect
    import synwevo_docs.api as pkg
    from pathlib import Path
    src = (Path(inspect.getfile(pkg)).parent / "main.py").read_text("utf-8")
    calls = [n.func.id for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert calls.index("check_standalone_listen") < calls.index("create_app")


# ---------------------------------------------------------------- relying-party redirects
def test_the_login_redirect_keeps_the_deep_link_as_one_parameter(tmp_path, monkeypatch):
    """`&` was left unescaped, so `/login?next=/p?a=1&prompt=none` became an /auth/start request
    with its own `prompt=none` — injected into the authorize request."""
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "l"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    c = TestClient(create_app(str(tmp_path / "l"), role="app", start_jobs=False))
    nxt = "/projects/p1?a=1&prompt=none"
    r = c.get("/login?" + urllib.parse.urlencode({"next": nxt}), follow_redirects=False)
    assert r.status_code == 303
    loc = urllib.parse.urlparse(r.headers["location"])
    assert loc.path == "/auth/start"
    assert urllib.parse.parse_qs(loc.query) == {"next": [nxt]}


def test_an_oversized_return_target_is_not_stored():
    from synwevo_auth_client.rp import MAX_NEXT_LEN, safe_next
    assert safe_next("/" + "a" * (MAX_NEXT_LEN + 1)) == "/"
    assert safe_next("/projects/p1") == "/projects/p1"


def test_a_non_ascii_state_on_the_callback_is_a_400(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "cb"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    c = TestClient(create_app(str(tmp_path / "cb"), role="app", start_jobs=False),
                   raise_server_exceptions=False)
    start = c.get("/auth/start?next=/", follow_redirects=False)
    assert start.status_code == 303
    r = c.get("/auth/callback?" + urllib.parse.urlencode({"code": "x", "state": "é"}),
              follow_redirects=False)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------- reclamation
def test_the_account_service_reclaims_dead_sign_in_state(tmp_path, monkeypatch):
    """Every sweeper existed and none was ever called."""
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "sw"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "sw"), role="accounts", start_jobs=False)
    store = Store(tmp_path / "sw")
    u = Identity(store).create_user("old@example.com", PW, status="active")
    _, row = Sessions(store).create(u["id"], workspace_id=None, amr="password")
    long_ago = time.time() - 60 * 24 * 3600
    store.execute("UPDATE sessions SET absolute_expires_at=?, revoked_at=? WHERE id=?", (long_ago, long_ago, row["id"]))
    store.execute("INSERT INTO rp_auth_requests (id,state,nonce,code_verifier,next_path,created_at,expires_at) "
                  "VALUES ('ar_old','s','n','v','/',?,?)", (long_ago, long_ago))
    out = api.state.auth_sweeper.run_once()
    assert out["sessions"] == 1 and out["rp_auth_requests"] == 1
    assert store.one("SELECT COUNT(*) AS n FROM sessions")["n"] == 0
