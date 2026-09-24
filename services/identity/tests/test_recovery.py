"""Forgetting a password, and proving an address.

The tests that matter are the ones about what the flow must *not* be: an oracle for which
addresses have accounts, a link that a mail scanner can spend, or a way around the account
owner's own second factor.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_identity.invites import Invites
from synwevo_identity.mfa import Mfa
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
NEW_PW = "an-entirely-different-long-phrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    # `file` is the mode meant for exactly this: messages are queued and written to a
    # directory instead of being sent. A local receiver is test equipment, never evidence that
    # real mail is deliverable.
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://accounts.localhost:8770")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    user = ident.create_user("alice@example.com", PW, status="active")
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "user": user,
            "invites": Invites(store, ident), "mfa": Mfa(store)}


def post(c, path, body=None):
    return c.post(path, json=body if body is not None else {},
                  headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def outbox(env, kind=None):
    rows = env["store"].all("SELECT * FROM mail_outbox ORDER BY created_at")
    return [r for r in rows if not kind or r["kind"] == kind]


def reset_token(env, email="alice@example.com") -> str:
    """The token as it appears in the delivered mail — not read out of the database, because
    only its hash is stored and the link is what the person actually receives."""
    fresh = TestClient(env["api"])
    assert post(fresh, "/api/auth/forgot", {"email": email}).status_code == 200
    body = outbox(env, "reset")[-1]["body"]
    marker = "token="
    return body[body.index(marker) + len(marker):].split()[0].strip()


# ---------------------------------------------------------------- enumeration
def test_requesting_a_reset_answers_the_same_for_any_address(env):
    """Otherwise this is an account-existence oracle that needs no authentication."""
    fresh = TestClient(env["api"])
    real = post(fresh, "/api/auth/forgot", {"email": "alice@example.com"})
    fake = post(fresh, "/api/auth/forgot", {"email": "nobody@example.com"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()
    # ...and only the real one produced mail
    assert len(outbox(env, "reset")) == 1


def test_an_attempt_against_an_unknown_address_is_still_audited(env):
    post(TestClient(env["api"]), "/api/auth/forgot", {"email": "nobody@example.com"})
    row = env["store"].one("SELECT * FROM audit_events WHERE action='auth.reset.request'")
    assert row["result"] == "denied" and row["actor_id"] is None


def test_an_empty_address_is_not_an_error(env):
    assert post(TestClient(env["api"]), "/api/auth/forgot", {"email": ""}).status_code == 200


def test_a_mail_failure_does_not_become_an_enumeration_oracle(env, monkeypatch):
    """Found by this test: `enqueue` raises 503 when mail is unconfigured, which made the
    endpoint answer 503 for an address that has an account and 200 for one that does not —
    turning the one endpoint built to resist enumeration into a perfect oracle for it."""
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    fresh = TestClient(env["api"])
    real = post(fresh, "/api/auth/forgot", {"email": "alice@example.com"})
    fake = post(fresh, "/api/auth/forgot", {"email": "nobody@example.com"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()
    # ...and the failure is recorded rather than lost
    row = env["store"].one("SELECT * FROM audit_events WHERE action='auth.reset.request' "
                           "AND result='error'")
    assert row and "mail" in str(row["meta"])


# ---------------------------------------------------------------- the link is not the action
def test_reading_the_link_does_not_spend_the_token(env):
    """Mail security scanners follow links. A token a scanner's GET consumes is a reset the
    person never asked for and can no longer do."""
    token = reset_token(env)
    fresh = TestClient(env["api"])
    for _ in range(3):
        r = fresh.get(f"/api/auth/reset/preview?token={token}")
        assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    assert post(fresh, "/api/auth/reset", {"token": token, "password": NEW_PW}).status_code == 200


def test_a_token_can_only_be_spent_once(env):
    token = reset_token(env)
    fresh = TestClient(env["api"])
    assert post(fresh, "/api/auth/reset", {"token": token, "password": NEW_PW}).status_code == 200
    again = post(fresh, "/api/auth/reset", {"token": token, "password": "yet-another-long-phrase"})
    assert again.status_code == 409


def test_asking_again_invalidates_the_previous_link(env):
    """An old reset link sitting in an inbox stops working the moment a new one is requested.

    The first request is moved two minutes into the past: a second request within a minute is
    now throttled (see the next test), and this one is about a genuine second request."""
    first = reset_token(env)
    env["store"].execute("UPDATE email_tokens SET created_at=created_at-120 WHERE kind='reset'")
    second = reset_token(env)
    assert first != second
    fresh = TestClient(env["api"])
    assert fresh.get(f"/api/auth/reset/preview?token={first}").status_code == 410
    assert fresh.get(f"/api/auth/reset/preview?token={second}").status_code == 200


def test_rapid_requests_neither_flood_the_mailbox_nor_kill_the_real_link(env):
    """F081: every request used to send a mail and supersede the previous link, so anyone who
    knew an address could bomb it and keep the owner's real reset link dead. Throttled requests
    answer exactly the same, send nothing and invalidate nothing."""
    first = reset_token(env)
    fresh = TestClient(env["api"])
    answers = {post(fresh, "/api/auth/forgot", {"email": "alice@example.com"}).text for _ in range(5)}
    assert len(answers) == 1, "被限流的请求与正常请求的回答必须一模一样"
    assert len(outbox(env, "reset")) == 1, "一分钟内不能再发第二封重置邮件"
    assert fresh.get(f"/api/auth/reset/preview?token={first}").status_code == 200, "真正的链接不能被刷掉"


def test_reset_requests_from_one_address_are_capped(env):
    """Spread across many addresses so the per-account limit is not what stops it."""
    for i in range(12):
        env["ident"].create_user(f"u{i}@example.com", PW, status="active")
    fresh = TestClient(env["api"])
    for i in range(12):
        assert post(fresh, "/api/auth/forgot", {"email": f"u{i}@example.com"}).status_code == 200
    assert len(outbox(env, "reset")) == 10, "同一来源地址 15 分钟内最多触发 10 次"


def test_a_made_up_token_is_refused(env):
    fresh = TestClient(env["api"])
    assert fresh.get("/api/auth/reset/preview?token=nonsense").status_code == 410
    assert post(fresh, "/api/auth/reset",
                {"token": "nonsense", "password": NEW_PW}).status_code == 409


def test_an_expired_token_is_refused(env):
    token = reset_token(env)
    env["store"].execute("UPDATE email_tokens SET expires_at=1 WHERE kind='reset'")
    assert TestClient(env["api"]).get(f"/api/auth/reset/preview?token={token}").status_code == 410


# ---------------------------------------------------------------- what a reset does
def test_a_reset_changes_the_password_and_ends_every_session(env):
    token = reset_token(env)
    assert env["c"].get("/api/auth/me").status_code == 200

    fresh = TestClient(env["api"])
    out = post(fresh, "/api/auth/reset", {"token": token, "password": NEW_PW})
    assert out.status_code == 200 and out.json()["sessions_revoked"] >= 1
    assert env["c"].get("/api/auth/me").status_code == 401, "改凭据要结束旧凭据授权的一切"

    signin = TestClient(env["api"])
    assert signin.post("/api/auth/login",
                       json={"email": "alice@example.com", "password": PW}).status_code == 401
    assert signin.post("/api/auth/login",
                       json={"email": "alice@example.com", "password": NEW_PW}).status_code == 200


def test_a_reset_also_revokes_platform_tokens(env):
    from synwevo_core import oidc
    store = env["store"]
    store.execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,issued_at,"
        "access_expires_at,family_id) VALUES ('t1','c',?,?,'docweave.read',0,?,'t1')",
        (env["user"]["id"], oidc._sha("live"), 9e9))
    token = reset_token(env)
    post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": NEW_PW})
    assert oidc.Grants(store).load_access("live") is None


def test_a_reset_does_not_sign_anyone_in(env):
    """Otherwise "I forgot my password" becomes a way around the account owner's own MFA."""
    fresh = TestClient(env["api"])
    token = reset_token(env)
    out = post(fresh, "/api/auth/reset", {"token": token, "password": NEW_PW})
    assert out.status_code == 200
    assert "user" not in out.json()
    assert fresh.get("/api/auth/me").status_code == 401, "重置之后仍然需要正常登录"


def test_a_reset_does_not_disable_a_second_factor(env):
    """恢复流程不得成为绕过 MFA 的后门。"""
    mfa, uid = env["mfa"], env["user"]["id"]
    secret = mfa.begin_totp(uid, "alice@example.com")["secret"]
    import pyotp, time
    mfa.confirm_totp(uid, pyotp.TOTP(secret).at(time.time() + 30))
    assert mfa.enabled_for(uid)

    token = reset_token(env)
    out = post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": NEW_PW})
    assert out.json()["mfa_required"] is True
    assert mfa.enabled_for(uid), "重置口令不应关掉第二因素"

    # signing in with the new password still stops at the second step
    signin = TestClient(env["api"])
    r = signin.post("/api/auth/login", json={"email": "alice@example.com", "password": NEW_PW})
    assert r.status_code == 200 and r.json().get("mfa_required")
    assert signin.get("/api/auth/me").status_code == 401


def test_a_weak_new_password_is_refused_and_says_the_link_is_gone(env):
    """The token had to be spent to be single-use under concurrency, so the message says so
    rather than letting someone retype into a dead link."""
    token = reset_token(env)
    out = post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": "short"})
    assert out.status_code == 400 and "失效" in out.json()["message"]


def test_a_suspended_account_cannot_be_reset_into(env):
    token = reset_token(env)
    env["ident"].set_status(env["user"]["id"], "suspended")
    out = post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": NEW_PW})
    assert out.status_code == 403


def test_the_person_is_told_their_password_changed(env):
    token = reset_token(env)
    post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": NEW_PW})
    notices = outbox(env, "security")
    assert notices and "口令已重置" in notices[-1]["subject"]


def test_no_secret_is_written_to_the_audit_log(env):
    token = reset_token(env)
    post(TestClient(env["api"]), "/api/auth/reset", {"token": token, "password": NEW_PW})
    rows = env["store"].all("SELECT * FROM audit_events")
    blob = " ".join(str(r["meta"]) + str(r["object_id"]) for r in rows)
    assert token not in blob and NEW_PW not in blob


# ---------------------------------------------------------------- email verification
def test_verifying_an_address(env):
    c = env["c"]
    assert env["ident"].get_user(env["user"]["id"])["email_verified_at"] is None
    assert post(c, "/api/auth/verify-email/send").status_code == 200
    body = outbox(env, "verify")[-1]["body"]
    token = body[body.index("token=") + 6:].split()[0].strip()

    fresh = TestClient(env["api"])
    assert post(fresh, "/api/auth/verify-email", {"token": token}).status_code == 200
    assert env["ident"].get_user(env["user"]["id"])["email_verified_at"]
    # single use
    assert post(fresh, "/api/auth/verify-email", {"token": token}).status_code == 409


def test_sending_a_verification_needs_a_session(env):
    assert post(TestClient(env["api"]), "/api/auth/verify-email/send").status_code == 401


def test_a_reset_token_cannot_verify_an_address(env):
    """Two kinds of token; one must never do the other's job."""
    token = reset_token(env)
    assert post(TestClient(env["api"]), "/api/auth/verify-email",
                {"token": token}).status_code == 409
