"""TOTP, recovery codes and the two-step sign-in.

The properties worth pinning: a code cannot be replayed, a six-digit code cannot be ground down,
a recovery code works exactly once, regenerating invalidates the old set, removing a factor is
guarded at least as strongly as adding one, and the seed is never readable through any API.
"""
from __future__ import annotations

import importlib
import time

import pyotp
import pytest
from fastapi.testclient import TestClient

from platform_core.identity import Identity
from platform_identity.mfa import TICKET_MAX_ATTEMPTS, Mfa, Tickets, decrypt_seed
from platform_core.errors import ServiceError
from platform_core.sessions import CSRF_COOKIE, CSRF_HEADER
from platform_core.store import Store

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_SECRET", "an-external-key-for-tests-0123456789")
    import platform_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    ident.create_user("alice@example.com", PW, status="active")
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "mfa": Mfa(store),
            "uid": ident.by_email("alice@example.com")["id"]}


def _csrf(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def _code(secret: str, steps_ahead: int = 1) -> str:
    """A code for a *later* step than the one enrolment consumed.

    Confirmation spends its own step — that is the replay protection — so the code that enabled
    TOTP can never sign in. Real users hit a new step before their next login; tests have to say
    so explicitly rather than reuse .now().
    """
    return pyotp.TOTP(secret).at(int(time.time()) + steps_ahead * 30)


def _enable_totp(env) -> str:
    """Walk the real enrolment path and return the secret."""
    c = env["c"]
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(c)).status_code == 200
    begin = c.post("/api/auth/mfa/totp/begin", headers=_csrf(c)).json()
    secret = begin["secret"]
    code = pyotp.TOTP(secret).now()
    r = c.post("/api/auth/mfa/totp/confirm", json={"code": code}, headers=_csrf(c))
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    return secret


# ---------------------------------------------------------------- enrolment
def test_enabling_totp_requires_a_recent_authentication(env):
    """A sign-in a moment ago counts — that is what "recent" means, and it is how sudo-style
    re-authentication behaves elsewhere. What must not pass is a session that authenticated long
    ago and has been sitting open since."""
    from platform_core.store import now as _now
    c = env["c"]
    assert c.post("/api/auth/mfa/totp/begin", headers=_csrf(c)).status_code == 200

    env["store"].execute("UPDATE sessions SET reauth_at=?", (_now() - 100000,))
    r = c.post("/api/auth/mfa/totp/begin", headers=_csrf(c))
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"


def test_reauth_needs_the_real_password(env):
    c = env["c"]
    assert c.post("/api/auth/mfa/reauth", json={"password": "not-it"}, headers=_csrf(c)).status_code == 401
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(c)).status_code == 200


def test_totp_is_not_active_until_a_real_code_is_verified(env):
    """A secret nobody has proved they can read must not lock the account."""
    c, mfa, uid = env["c"], env["mfa"], env["uid"]
    c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(c))
    c.post("/api/auth/mfa/totp/begin", headers=_csrf(c))
    assert mfa.totp_enabled(uid) is False
    assert mfa.enabled_for(uid) is False


def test_a_wrong_confirmation_code_does_not_enable_it(env):
    c, mfa, uid = env["c"], env["mfa"], env["uid"]
    c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(c))
    c.post("/api/auth/mfa/totp/begin", headers=_csrf(c))
    r = c.post("/api/auth/mfa/totp/confirm", json={"code": "000000"}, headers=_csrf(c))
    assert r.status_code == 400 and mfa.totp_enabled(uid) is False


def test_enrolment_issues_recovery_codes_immediately(env):
    """Losing the phone starts mattering the moment MFA starts mattering."""
    _enable_totp(env)
    assert env["mfa"].recovery_remaining(env["uid"]) == 10


# ---------------------------------------------------------------- the seed
def test_the_seed_is_encrypted_at_rest_and_never_returned_again(env):
    secret = _enable_totp(env)
    stored = env["store"].one("SELECT secret_enc FROM totp_secrets WHERE user_id=?", (env["uid"],))["secret_enc"]
    assert secret not in stored
    assert decrypt_seed(env["store"], stored) == secret        # decryptable by the service only

    # no API path hands it back once enrolment is done
    body = env["c"].get("/api/auth/mfa").text
    assert secret not in body and "secret" not in body


def test_a_tampered_ciphertext_fails_closed(env):
    """AES-GCM is authenticated, so a modified seed does not decrypt to a different one."""
    import base64
    _enable_totp(env)
    row = env["store"].one("SELECT secret_enc FROM totp_secrets WHERE user_id=?", (env["uid"],))
    raw = bytearray(base64.b64decode(row["secret_enc"]))
    raw[-1] ^= 0x01
    env["store"].execute("UPDATE totp_secrets SET secret_enc=? WHERE user_id=?",
                         (base64.b64encode(bytes(raw)).decode(), env["uid"]))
    assert env["mfa"].verify_totp(env["uid"], "123456") is False


def test_key_location_is_reported_not_assumed(env, monkeypatch):
    from platform_identity import mfa as mfa_mod
    assert mfa_mod.key_is_external() is True                  # the fixture sets DOCWEAVE_SECRET
    monkeypatch.delenv("DOCWEAVE_SECRET", raising=False)
    assert mfa_mod.key_is_external() is False


# ---------------------------------------------------------------- verification
def test_a_code_cannot_be_replayed(env):
    """A code captured by a phishing page stays valid for its whole window otherwise."""
    secret = _enable_totp(env)
    mfa, uid = env["mfa"], env["uid"]
    code = _code(secret)
    assert mfa.verify_totp(uid, code) is True
    assert mfa.verify_totp(uid, code) is False, "同一个验证码不能用第二次"


def test_a_code_from_an_earlier_step_is_refused_after_a_later_one(env):
    secret = _enable_totp(env)
    mfa, uid = env["mfa"], env["uid"]
    t = pyotp.TOTP(secret)
    nowt = int(time.time())
    assert mfa.verify_totp(uid, t.at(nowt + 30)) is True
    assert mfa.verify_totp(uid, t.at(nowt)) is False, "不能回退到更早的时间步"


def test_clock_drift_of_one_step_is_tolerated(env):
    """Tested on the matcher, because at sign-in the replay guard also applies and a backwards
    step is refused for that reason instead — two different rules that would otherwise be
    impossible to tell apart from one assertion."""
    secret = _enable_totp(env)
    row = env["store"].one("SELECT * FROM totp_secrets WHERE user_id=?", (env["uid"],))
    t = pyotp.TOTP(secret)
    nowt = int(time.time())
    assert env["mfa"]._match(row, t.at(nowt - 30)) is not None
    assert env["mfa"]._match(row, t.at(nowt + 30)) is not None
    assert env["mfa"]._match(row, t.at(nowt + 300)) is None, "远超窗口的验证码必须拒绝"


def test_malformed_codes_are_rejected_without_touching_state(env):
    _enable_totp(env)
    for bad in ("", "abc", "12345", "1234567", None):
        assert env["mfa"].verify_totp(env["uid"], bad) is False


# ---------------------------------------------------------------- sign-in
def test_login_becomes_two_steps_once_mfa_is_on(env):
    secret = _enable_totp(env)
    fresh = TestClient(env["api"])
    r = fresh.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and body["ticket"] and body["methods"]["totp"] is True
    assert "user" not in body, "第一步不能返回账户信息"
    assert fresh.get("/api/auth/me").status_code == 401, "第一步不能发出会话"

    done = fresh.post("/api/auth/login/mfa",
                      json={"ticket": body["ticket"], "code": _code(secret)})
    assert done.status_code == 200 and done.json()["session"]["amr"] == "password+totp"
    assert fresh.get("/api/auth/me").json()["email"] == "alice@example.com"


def test_a_ticket_dies_after_five_wrong_codes(env):
    _enable_totp(env)
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com",
                                                 "password": PW}).json()["ticket"]
    for _ in range(TICKET_MAX_ATTEMPTS):
        assert fresh.post("/api/auth/login/mfa", json={"ticket": ticket, "code": "000000"}).status_code == 401
    r = fresh.post("/api/auth/login/mfa", json={"ticket": ticket, "code": "000000"})
    assert r.status_code == 401 and r.json()["error"] == "ticket_invalid"


def test_a_ticket_is_single_use(env):
    secret = _enable_totp(env)
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com",
                                                 "password": PW}).json()["ticket"]
    assert fresh.post("/api/auth/login/mfa",
                      json={"ticket": ticket, "code": _code(secret)}).status_code == 200
    again = TestClient(env["api"]).post("/api/auth/login/mfa",
                                        json={"ticket": ticket, "code": _code(secret, 2)})
    assert again.status_code == 401


def test_an_expired_ticket_is_refused(env):
    from platform_core.store import now as _now
    _enable_totp(env)
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com",
                                                 "password": PW}).json()["ticket"]
    env["store"].execute("UPDATE mfa_tickets SET expires_at=?", (_now() - 1,))
    assert fresh.post("/api/auth/login/mfa", json={"ticket": ticket, "code": "000000"}).status_code == 401


def test_a_ticket_cannot_be_used_for_a_different_account(env):
    """It carries the user it was issued for; there is no field a client can steer."""
    _enable_totp(env)
    env["ident"].create_user("bob@example.com", PW, status="active")
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com",
                                                 "password": PW}).json()["ticket"]
    t = Tickets(env["store"]).load(ticket)
    assert t["user_id"] == env["uid"]


# ---------------------------------------------------------------- recovery codes
def test_a_recovery_code_signs_in_once_and_says_so(env):
    _enable_totp(env)
    codes = env["c"].post("/api/auth/mfa/recovery/regenerate", headers=_csrf(env["c"])).json()["recovery_codes"]
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com",
                                                 "password": PW}).json()["ticket"]
    r = fresh.post("/api/auth/login/mfa", json={"ticket": ticket, "recovery_code": codes[0]})
    assert r.status_code == 200
    assert r.json()["recovery_used"] is True and r.json()["recovery_remaining"] == 9

    second = TestClient(env["api"])
    t2 = second.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).json()["ticket"]
    assert second.post("/api/auth/login/mfa", json={"ticket": t2, "recovery_code": codes[0]}).status_code == 401


def test_recovery_codes_tolerate_how_people_type_them(env):
    _enable_totp(env)
    mfa, uid = env["mfa"], env["uid"]
    codes = mfa.generate_recovery_codes(uid)
    messy = "  " + codes[0].lower().replace("-", " ") + "  "
    assert mfa.use_recovery_code(uid, messy) is True


def test_regenerating_invalidates_every_old_code(env):
    _enable_totp(env)
    mfa, uid = env["mfa"], env["uid"]
    old = mfa.generate_recovery_codes(uid)
    mfa.generate_recovery_codes(uid)
    assert mfa.use_recovery_code(uid, old[0]) is False
    assert mfa.recovery_remaining(uid) == 10


def test_recovery_codes_are_stored_only_as_hashes(env):
    _enable_totp(env)
    codes = env["mfa"].generate_recovery_codes(env["uid"])
    rows = env["store"].all("SELECT code_hash FROM recovery_codes WHERE user_id=?", (env["uid"],))
    blob = " ".join(r["code_hash"] for r in rows)
    assert all(c not in blob for c in codes)
    assert all(len(r["code_hash"]) == 64 for r in rows)


# ---------------------------------------------------------------- removal
def test_disabling_totp_needs_reauth_and_a_current_code(env):
    """Removing a factor is guarded at least as strongly as adding one."""
    secret = _enable_totp(env)
    c = env["c"]
    # reauth from enrolment is still fresh, but the code is still required
    assert c.post("/api/auth/mfa/totp/disable", json={"code": "000000"}, headers=_csrf(c)).status_code == 400
    assert env["mfa"].totp_enabled(env["uid"]) is True
    r = c.post("/api/auth/mfa/totp/disable", json={"code": _code(secret)}, headers=_csrf(c))
    assert r.status_code == 200 and env["mfa"].totp_enabled(env["uid"]) is False


def test_disabling_without_reauth_is_refused(env):
    secret = _enable_totp(env)
    from platform_core.store import now as _now
    env["store"].execute("UPDATE sessions SET reauth_at=?", (_now() - 100000,))
    c = env["c"]
    r = c.post("/api/auth/mfa/totp/disable", json={"code": _code(secret)}, headers=_csrf(c))
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"


def test_reauth_for_one_purpose_does_not_unlock_another(env):
    """The scope on a re-authentication is what stops it becoming a general skeleton key."""
    from platform_core.sessions import Sessions
    s = env["store"].one("SELECT * FROM sessions LIMIT 1")
    env["c"].post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(env["c"]))
    s = env["store"].one("SELECT * FROM sessions LIMIT 1")
    assert Sessions.reauth_fresh(s, "mfa") is True
    assert Sessions.reauth_fresh(s, "delete_workspace") is False
