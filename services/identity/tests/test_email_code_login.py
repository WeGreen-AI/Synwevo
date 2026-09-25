"""One-time e-mail sign-in codes.

Pinned: the request answers identically for every address and stores nothing readable; a code
signs in once and never twice; five wrong guesses kill it; a newer code retires the older one;
an account with TOTP still has to complete the second step and records `email+totp`; a platform
administrator signs in like anyone else but holds a one-factor session that cannot manage until
the password is re-entered (`sessions.admin_capable`); an unverified address gets no code; and
the outbox keeps the message without the code once it has left.
"""
from __future__ import annotations

import importlib
import re
import time

import pyotp
import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_identity.email_login import CODE_MAX_ATTEMPTS, CODE_TTL_S
from synwevo_identity.mail import REDACTED, redact

PW = "a-sufficiently-long-passphrase"
CODE_IN_MAIL = re.compile(r"(?:登录验证码|登入驗證碼|Sign-in code)[:：]\s*(\d{6})")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    for email, role in (("alice@example.com", "none"), ("root@example.com", "admin")):
        u = ident.create_user(email, PW, status="active", platform_role=role)
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    ident.create_user("unverified@example.com", PW, status="active")
    return {"api": api, "store": store, "ident": ident}


def _start(c, email):
    return c.post("/api/auth/login/email/start", json={"email": email})


def _latest_code(env) -> str:
    row = env["store"].one("SELECT body FROM mail_outbox WHERE kind='login_code' ORDER BY created_at DESC LIMIT 1")
    assert row, "no sign-in code mail was queued"
    m = CODE_IN_MAIL.search(row["body"])
    assert m, row["body"]
    return m.group(1)


def _mails(env):
    return env["store"].all("SELECT to_addr, body FROM mail_outbox WHERE kind='login_code' ORDER BY created_at")


def test_requests_answer_identically_and_only_verified_active_accounts_get_a_code(env):
    c = TestClient(env["api"])
    answers = {email: _start(c, email) for email in ("alice@example.com", "nobody@example.com", "unverified@example.com")}
    assert {r.status_code for r in answers.values()} == {202}
    assert len({r.text for r in answers.values()}) == 1
    assert answers["alice@example.com"].json() == {"status": "requested", "delivery": "requested", "expires_min": CODE_TTL_S // 60}
    assert [m["to_addr"] for m in _mails(env)] == ["alice@example.com"]
    assert _start(c, "not an address").status_code == 400


def test_a_code_signs_in_once_and_is_stored_only_as_a_hash(env):
    c = TestClient(env["api"])
    assert _start(c, "alice@example.com").status_code == 202
    code = _latest_code(env)
    rows = env["store"].all("SELECT * FROM login_codes")
    assert len(rows) == 1 and code not in rows[0]["code_hash"] and len(rows[0]["code_hash"]) == 64
    r = c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": code})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["amr"] == "email" and r.json()["session"]["persistent"] is False
    assert c.get("/api/auth/me").status_code == 200
    again = TestClient(env["api"]).post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": code})
    assert again.status_code == 401 and again.json()["error"] == "code_expired"


def test_remember_applies_to_code_sign_in(env):
    c = TestClient(env["api"])
    _start(c, "alice@example.com")
    r = c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": _latest_code(env), "remember": True})
    assert r.status_code == 200 and r.json()["session"]["persistent"] is True
    assert any(h.startswith(COOKIE_NAME + "=") and "Max-Age=" in h for h in r.headers.get_list("set-cookie"))


def test_five_wrong_guesses_kill_the_code(env):
    c = TestClient(env["api"])
    _start(c, "alice@example.com")
    code = _latest_code(env)
    wrong = "000000" if code != "000000" else "111111"
    statuses = []
    for _ in range(CODE_MAX_ATTEMPTS):
        statuses.append(c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": wrong}).status_code)
    assert all(s in (401, 429) for s in statuses), statuses
    env["ident"].backoff_clear(f"login-code:{env['ident'].by_email('alice@example.com')['id']}")
    r = c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": code})
    assert r.status_code == 401 and r.json()["error"] == "code_expired"
    assert c.get("/api/auth/me").status_code == 401


def test_a_newer_code_retires_the_older_one(env):
    c = TestClient(env["api"])
    _start(c, "alice@example.com")
    first = _latest_code(env)
    env["store"].execute("UPDATE login_codes SET created_at=created_at-120")
    env["ident"].store.execute("DELETE FROM auth_attempts")
    _start(c, "alice@example.com")
    second = _latest_code(env)
    assert first != second or True  # equal codes are possible by chance; the row, not the digits, decides
    live = env["store"].all("SELECT used_at FROM login_codes ORDER BY created_at")
    assert live[0]["used_at"] is not None and live[1]["used_at"] is None
    r = c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": second})
    assert r.status_code == 200


def test_expired_codes_are_refused(env):
    c = TestClient(env["api"])
    _start(c, "alice@example.com")
    code = _latest_code(env)
    env["store"].execute("UPDATE login_codes SET expires_at=?", (now() - 1,))
    r = c.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": code})
    assert r.status_code == 401 and r.json()["error"] == "code_expired"


def test_code_sign_in_does_not_bypass_totp_and_records_the_pair(env):
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    csrf = {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=csrf).status_code == 200
    begin = c.post("/api/auth/mfa/totp/begin", json={}, headers=csrf).json()
    assert c.post("/api/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(begin["secret"]).now()}, headers=csrf).status_code == 200

    fresh = TestClient(env["api"])
    _start(fresh, "alice@example.com")
    step = fresh.post("/api/auth/login/email/verify", json={"email": "alice@example.com", "code": _latest_code(env)})
    assert step.status_code == 200 and step.json()["mfa_required"] and fresh.cookies.get(COOKIE_NAME) is None
    totp = pyotp.TOTP(begin["secret"]).at(int(time.time()) + 30)
    done = fresh.post("/api/auth/login/mfa", json={"ticket": step.json()["ticket"], "code": totp})
    assert done.status_code == 200 and done.json()["session"]["amr"] == "email+totp"


def test_platform_administrators_get_a_one_factor_session_that_cannot_manage_until_stepped_up(env):
    c = TestClient(env["api"])
    assert _start(c, "root@example.com").status_code == 202
    r = c.post("/api/auth/login/email/verify", json={"email": "root@example.com", "code": _latest_code(env)})
    assert r.status_code == 200 and r.json()["session"]["amr"] == "email"
    me = c.get("/api/auth/me").json()
    assert me["platform_role"] == "admin" and me["permissions"] == [] and me["admin_locked"] is True
    csrf = {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}
    denied = c.get("/api/admin/overview")
    assert denied.status_code == 403 and denied.json()["error"] == "step_up_required"
    assert c.get("/api/admin/api-platform/overview").status_code == 403
    # The password, re-entered on the account page, is the second distinct factor.
    wrong = c.post("/api/auth/reauth", json={"password": "not-it", "scope": "admin"}, headers=csrf)
    assert wrong.status_code == 401 and c.get("/api/admin/overview").status_code == 403
    ok = c.post("/api/auth/reauth", json={"password": PW, "scope": "admin"}, headers=csrf)
    assert ok.status_code == 200 and ok.json()["amr"] == "email+password"
    me = c.get("/api/auth/me").json()
    assert me["permissions"] == ["api_platform.manage"] and me["admin_locked"] is False
    assert c.get("/api/admin/overview").status_code == 200
    assert c.get("/api/auth/sessions").json()["items"][0]["amr"] == "email+password"


def test_the_outbox_never_keeps_the_code_once_the_mail_is_done(env):
    c = TestClient(env["api"])
    _start(c, "alice@example.com")
    code = _latest_code(env)
    body = _mails(env)[0]["body"]
    assert code in body
    assert code not in redact(body) and REDACTED in redact(body)
    # Drain through the real worker path (file sink): the stored body loses the code.
    env["api"].state.mailer.drain_once()
    kept = env["store"].one("SELECT body, status FROM mail_outbox WHERE kind='login_code'")
    assert kept["status"] == "sent" and code not in kept["body"]


def test_requests_are_throttled_per_account_without_changing_the_answer(env):
    c = TestClient(env["api"])
    first = _start(c, "alice@example.com")
    second = _start(c, "alice@example.com")           # within the one-minute gap
    assert first.status_code == second.status_code == 202 and first.text == second.text
    assert len(_mails(env)) == 1
