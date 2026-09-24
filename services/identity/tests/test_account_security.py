"""Throttling and bootstrap hardening on the sign-in surface.

Each test is the attack a reviewer demonstrated, replayed: spraying from one address after its
first window, rotating X-Forwarded-For, grinding second-factor codes across tickets, guessing a
password through the re-authentication prompt, and flooding bootstrap with made-up tokens.
"""
from __future__ import annotations

import importlib
import threading
import time

import pyotp
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_identity.mfa import Mfa, Tickets, TICKET_MAX_ATTEMPTS

PW = "a-sufficiently-long-passphrase"
WRONG = "wrong-password-here-ok"


@pytest.fixture()
def ident(tmp_path):
    return Identity(Store(tmp_path / "d"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    user = ident.create_user("alice@example.com", PW, status="active")
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return {"api": api, "c": c, "store": store, "ident": ident, "user": user}


def _csrf(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}


def _age(store, like: str, seconds: float = 10000) -> None:
    """Move a bucket into the past, as if that much time had passed."""
    store.execute("UPDATE auth_attempts SET first_at=first_at-?, last_at=last_at-?, "
                  "blocked_until=CASE WHEN blocked_until IS NULL THEN NULL ELSE blocked_until-? END "
                  "WHERE bucket LIKE ?", (seconds, seconds, seconds, like))


# ---------------------------------------------------------------- F002: the address window
def test_the_address_limit_still_works_after_its_first_window(ident):
    """The reviewer's spray: 31 failures from one address, 15 minutes pass, keep spraying."""
    for i in range(31):
        r = ident.authenticate(f"n{i}@example.com", WRONG, ip="5.5.5.5")
    assert r["reason"] == "address_blocked" and r["retry_after"] > 0
    _age(ident.store, "ip:5.5.5.5", 1000)
    for i in range(31, 61):
        assert ident.authenticate(f"n{i}@example.com", WRONG, ip="5.5.5.5")["reason"] == "no_account"
    blocked = ident.authenticate("n99@example.com", WRONG, ip="5.5.5.5")
    assert blocked["reason"] == "address_blocked", "第二个窗口里地址限流必须照样生效"


def test_a_block_lasts_its_whole_window_from_when_it_was_hit(ident):
    for i in range(30):
        ident.authenticate(f"n{i}@example.com", WRONG, ip="6.6.6.6")
    b = ident.store.one("SELECT * FROM auth_attempts WHERE bucket='ip:6.6.6.6'")
    assert b["blocked_until"] and b["blocked_until"] > time.time() + 800


# ---------------------------------------------------------------- F059: X-Forwarded-For
def _req(xff: str | None, peer="10.0.0.1") -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                    "client": (peer, 1234), "query_string": b""})


def test_only_the_entry_our_proxy_wrote_is_trusted(monkeypatch):
    from synwevo_identity.auth_routes import client_ip
    monkeypatch.delenv("SYNWEVO_TRUST_PROXY", raising=False)
    assert client_ip(_req("1.2.3.4, 9.9.9.9")) == "10.0.0.1", "未配置代理时不读该头"
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    # the client sent "1.2.3.4"; our proxy appended the real peer 9.9.9.9
    assert client_ip(_req("1.2.3.4, 9.9.9.9")) == "9.9.9.9"
    assert client_ip(_req("spoof-1, spoof-2, 9.9.9.9")) == "9.9.9.9", "轮换伪造值不能换桶"
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "2")
    assert client_ip(_req("spoof, 1.2.3.4, 9.9.9.9")) == "1.2.3.4"


# ---------------------------------------------------------------- F060: second-factor grinding
def _enable_totp(env) -> str:
    mfa = Mfa(env["store"])
    begin = mfa.begin_totp(env["user"]["id"], "alice@example.com")
    mfa.confirm_totp(env["user"]["id"], pyotp.TOTP(begin["secret"]).now())
    return begin["secret"]


def _ticket(env) -> str:
    r = TestClient(env["api"]).post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return r.json()["ticket"]


def test_wrong_codes_are_limited_across_tickets_not_only_per_ticket(env):
    secret = _enable_totp(env)
    fresh = TestClient(env["api"])
    t1 = _ticket(env)
    for _ in range(TICKET_MAX_ATTEMPTS):
        assert fresh.post("/api/auth/login/mfa", json={"ticket": t1, "code": "000000"}).status_code == 401
    # a brand-new ticket (the password is known) no longer buys five fresh guesses at once
    t2 = _ticket(env)
    r = fresh.post("/api/auth/login/mfa", json={"ticket": t2, "code": "000000"})
    assert r.status_code == 429 and r.json()["error"] == "too_many_attempts"
    # the wait did not cost the ticket an attempt, and the right code still works afterwards
    _age(env["store"], "mfa:%", 10000)
    code = pyotp.TOTP(secret).at(int(time.time()) + 30)
    ok = fresh.post("/api/auth/login/mfa", json={"ticket": t2, "code": code})
    assert ok.status_code == 200, ok.text
    assert env["store"].one("SELECT 1 FROM auth_attempts WHERE bucket LIKE 'mfa:%'") is None


def test_parallel_guesses_cannot_exceed_the_ticket_limit(env):
    _enable_totp(env)
    raw = _ticket(env)
    tickets = Tickets(env["store"])
    got, barrier = [], threading.Barrier(12)

    def claim():
        barrier.wait()
        got.append(tickets.claim_attempt(raw))

    threads = [threading.Thread(target=claim) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(x for x in got if x is not None) == list(range(1, TICKET_MAX_ATTEMPTS + 1))
    assert got.count(None) == 12 - TICKET_MAX_ATTEMPTS


# ---------------------------------------------------------------- F061/F079: re-authentication
def test_reauth_failures_share_the_login_backoff(env):
    c = env["c"]
    for _ in range(4):
        r = c.post("/api/auth/reauth", json={"password": "nope-nope-nope", "scope": "admin"}, headers=_csrf(c))
    assert r.status_code == 429, "重认证的错误口令与登录共用退避"
    login = TestClient(env["api"]).post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    assert login.status_code == 429, "从会话里猜口令，不能绕开登录的限流"


def test_a_session_that_keeps_failing_reauth_is_ended(env):
    c, store = env["c"], env["store"]
    for i in range(5):
        _age(store, "user:%")                          # step past the backoff each time
        r = c.post("/api/auth/mfa/reauth", json={"password": f"wrong-{i}-guess-guess"}, headers=_csrf(c))
    assert r.status_code == 401 and r.json()["error"] == "session_ended"
    assert c.get("/api/auth/me").status_code == 401
    row = store.one("SELECT revoked_reason FROM sessions WHERE user_id=?", (env["user"]["id"],))
    assert row["revoked_reason"] == "reauth_failures"


def test_the_right_password_still_reauthenticates(env):
    c = env["c"]
    c.post("/api/auth/reauth", json={"password": "nope-nope-nope", "scope": "mfa"}, headers=_csrf(c))
    assert c.post("/api/auth/reauth", json={"password": PW, "scope": "mfa"}, headers=_csrf(c)).status_code == 200


# ---------------------------------------------------------------- F062: bootstrap
@pytest.fixture()
def fresh_service(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "b"), start_jobs=False)
    return api, Store(tmp_path / "b")


def _counts(store):
    return {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("users", "workspaces", "memberships", "user_passwords", "app_entitlements", "workspace_apps")}


def test_a_made_up_bootstrap_token_creates_nothing(fresh_service):
    api, store = fresh_service
    before = _counts(store)
    c = TestClient(api)
    r = c.post("/api/auth/bootstrap", json={"token": "made-up", "email": "root@example.com", "password": PW})
    assert r.status_code == 403 and r.json()["error"] == "bad_token"
    assert _counts(store) == before, "令牌无效时不能先建账户再删一半"


def test_bootstrap_is_throttled_per_address(fresh_service):
    api, _ = fresh_service
    c = TestClient(api)
    codes = [c.post("/api/auth/bootstrap", json={"token": f"t{i}", "email": "r@example.com", "password": PW}).status_code
             for i in range(11)]
    assert codes[:10] == [403] * 10 and codes[10] == 429


def test_a_real_token_still_bootstraps(fresh_service):
    api, store = fresh_service
    token = Identity(store).create_bootstrap_token()
    r = TestClient(api).post("/api/auth/bootstrap", json={"token": token, "email": "root@example.com", "password": PW})
    assert r.status_code == 200, r.text


def test_a_lost_bootstrap_race_leaves_nothing_behind(ident):
    before = _counts(ident.store)
    u = ident.create_user("late@example.com", PW, status="active", platform_role="admin")
    ident.purge_new_account(u["id"])
    assert _counts(ident.store) == before


# ---------------------------------------------------------------- F024: a saturated hasher
def test_a_saturated_hasher_turns_requests_away_instead_of_parking_workers(monkeypatch):
    from synwevo_core import passwords
    from synwevo_core.errors import ServiceError
    monkeypatch.setattr(passwords, "SLOT_WAIT_S", 0.05)
    held = [passwords._slots.acquire() for _ in range(passwords.CONCURRENCY)]
    try:
        with pytest.raises(ServiceError) as e:
            passwords.verify("not-even-a-phc-string", "x")     # refused before any comparison
        assert e.value.status == 503 and e.value.code == "busy"
    finally:
        for _ in held:
            passwords._slots.release()
    phc = passwords.hash_password("correct horse battery staple and more")
    assert passwords.verify(phc, "correct horse battery staple and more") is True


# ---------------------------------------------------------------- F096: dead tickets are reclaimed
def test_expired_tickets_are_reclaimed_when_new_ones_are_issued(ident):
    t = Tickets(ident.store)
    t.issue("u_1")
    ident.store.execute("UPDATE mfa_tickets SET expires_at=?", (time.time() - 7200,))
    t.issue("u_1")
    assert ident.store.one("SELECT COUNT(*) AS n FROM mfa_tickets")["n"] == 1
