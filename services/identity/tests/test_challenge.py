"""Human verification on the anonymous mail-sending entrances, verified on the server.

A transport double stands in for Cloudflare's siteverify endpoint: it records every form the
verifier sends (so the tests can see the secret, the idempotency key and the token go there and
nowhere else) and answers whatever the test scripted. Nothing here proves Cloudflare's side;
the isolated browser run with Cloudflare's published test keys does that (verification.md).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core import challenge as C

SITE = "1x00000000000000000000AA"            # Cloudflare's documented test site key (always passes)
SECRET = "1x0000000000000000000000000000000AA"   # its test secret; never a production value
HOST = "accounts.example.test"


class SiteverifyDouble:
    def __init__(self):
        self.calls: list[dict] = []
        self.script: list = []          # each entry: (status, body) or an Exception instance

    def __call__(self, form: dict, timeout_s: float):
        self.calls.append(dict(form))
        if not self.script:
            return 200, {"success": True, "hostname": HOST, "action": "register", "challenge_ts": _now_iso()}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def answer(self, action: str, *, success=True, hostname=HOST, codes=(), status=200, ts=None):
        body = {"success": success, "hostname": hostname, "action": action, "challenge_ts": ts or _now_iso(),
                "error-codes": list(codes)}
        self.script.append((status, body))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@pytest.fixture()
def double():
    return SiteverifyDouble()


@pytest.fixture()
def client(tmp_path, monkeypatch, double):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_TURNSTILE_SITE_KEY", SITE)
    monkeypatch.setenv("SYNWEVO_TURNSTILE_SECRET", SECRET)
    monkeypatch.setenv("SYNWEVO_TURNSTILE_HOSTNAMES", HOST)
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    api.state.challenge.transport = double
    with TestClient(api) as c:
        c.app_state = api.state
        c.mail_dir = tmp_path / "mail"
        yield c


def _mails(c) -> int:
    """Queued messages: the outbox row is written in the request; delivery is the worker's."""
    return c.app_state.identity.store.one("SELECT COUNT(*) AS n FROM mail_outbox")["n"]


REG = {"email": "newcomer@example.test", "password": "orchard signal lantern meadow 2026", "display_name": "N", "locale": "zh-Hans"}


# ---------------------------------------------------------------- the verifier itself
def test_verifier_sends_secret_and_idempotency_key_and_checks_action_and_hostname(double):
    t = C.Turnstile(C.Policy(SITE, SECRET, frozenset({HOST}), 5), transport=double)
    double.answer("register")
    v = t.verify("XXXX.DUMMY.TOKEN.XXXX", action="register", remoteip="203.0.113.7")
    assert v.ok and v.hostname == HOST
    sent = double.calls[-1]
    assert sent["secret"] == SECRET and sent["response"] == "XXXX.DUMMY.TOKEN.XXXX" and sent["remoteip"] == "203.0.113.7"
    assert len(sent["idempotency_key"]) == 36
    double.answer("forgot")                                    # a token solved for another form
    assert t.verify("tok", action="register").reason == "action"
    double.answer("register", hostname="evil.example")         # solved on another site
    assert t.verify("tok", action="register").reason == "hostname"
    double.answer("register", success=False, codes=["timeout-or-duplicate"])
    assert t.verify("tok", action="register").reason == "spent"
    double.answer("register", success=False, codes=["invalid-input-response"])
    assert t.verify("tok", action="register").reason == "invalid"
    double.answer("register", ts="2026-09-25T13:00:00Z")      # far older than the 300 s validity
    assert t.verify("tok", action="register").reason == "expired"
    assert t.verify("", action="register").reason == "missing" and t.verify("x" * 2049, action="register").reason == "malformed"
    assert len(double.calls) == 6, "missing and malformed tokens never reach the network"


def test_verifier_retries_once_with_the_same_key_then_reports_an_outage(double, monkeypatch):
    t = C.Turnstile(C.Policy(SITE, SECRET, frozenset({HOST}), 5), transport=double)
    # Patched for this test only: the earlier module-level assignment leaked a no-op sleep into
    # every later test of the session, which spun their polling loops and, on the CI runner,
    # stretched the suite past the job's timeout.
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    double.script = [ConnectionError("boom")]; double.answer("register")
    assert t.verify("tok", action="register").ok
    assert double.calls[-1]["idempotency_key"] == double.calls[-2]["idempotency_key"]
    double.script = [ConnectionError("a"), ConnectionError("b")]
    with pytest.raises(C.ChallengeUnavailable):
        t.verify("tok", action="register")
    double.answer("register", success=False, codes=["invalid-input-secret"])
    with pytest.raises(C.ChallengeUnavailable):                # misconfiguration is an outage, not a pass
        t.verify("tok", action="register")
    double.script = [(502, {})] * 2
    with pytest.raises(C.ChallengeUnavailable):
        t.verify("tok", action="register")


def test_policy_needs_both_keys_and_derives_the_hostname(monkeypatch):
    for k in ("SYNWEVO_TURNSTILE_SITE_KEY", "SYNWEVO_TURNSTILE_SECRET", "SYNWEVO_TURNSTILE_HOSTNAMES"):
        monkeypatch.delenv(k, raising=False)
    assert not C.policy().enabled and C.policy().public() is None
    monkeypatch.setenv("SYNWEVO_TURNSTILE_SITE_KEY", SITE)
    with pytest.raises(ValueError):
        C.policy()
    monkeypatch.setenv("SYNWEVO_TURNSTILE_SECRET", SECRET)
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    p = C.policy()
    assert p.enabled and p.hostnames == frozenset({"accounts.example.test"})
    assert p.public() == {"provider": "turnstile", "site_key": SITE, "actions": C.ACTIONS}
    assert SECRET not in repr(p.public())


# ---------------------------------------------------------------- the entrances
def test_state_names_the_site_key_and_actions_but_never_the_secret(client):
    st = client.get("/api/auth/state").json()
    assert st["challenge"]["provider"] == "turnstile" and st["challenge"]["site_key"] == SITE
    assert st["challenge"]["actions"]["register"] == "always" and st["challenge"]["actions"]["login"] == "risk"
    assert SECRET not in client.get("/api/auth/state").text


def test_registration_needs_a_good_token_and_creates_nothing_without_one(client, double):
    r = client.post("/api/auth/register", json=REG)
    assert r.status_code == 400 and r.json()["error"] == "challenge_required"
    assert client.app_state.identity.by_email(REG["email"]) is None and _mails(client) == 0 and double.calls == []
    double.answer("register", success=False, codes=["invalid-input-response"])
    r = client.post("/api/auth/register", json=REG | {"challenge_token": "bad"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_failed" and r.json()["detail"]["reason"] == "invalid"
    assert client.app_state.identity.by_email(REG["email"]) is None
    double.answer("forgot")                                    # token for the recovery form
    r = client.post("/api/auth/register", json=REG | {"challenge_token": "tok"})
    assert r.status_code == 400 and r.json()["detail"]["reason"] == "action"
    double.answer("register")
    r = client.post("/api/auth/register", json=REG | {"challenge_token": "XXXX.DUMMY.TOKEN.XXXX"})
    assert r.status_code == 202 and r.json()["status"] == "verification_pending", r.text
    user = client.app_state.identity.by_email(REG["email"])
    assert user and user["status"] == "pending" and _mails(client) == 1
    assert double.calls[-1]["response"] == "XXXX.DUMMY.TOKEN.XXXX"


def test_an_outage_refuses_the_registration_instead_of_waving_it_through(client, double):
    double.script = [ConnectionError("down"), ConnectionError("still down")]
    r = client.post("/api/auth/register", json=REG | {"challenge_token": "tok"})
    assert r.status_code == 503 and r.json()["error"] == "challenge_unavailable"
    assert r.json()["detail"]["retry_after"] == 30 and r.headers.get("retry-after") == "30"
    assert client.app_state.identity.by_email(REG["email"]) is None and _mails(client) == 0


def test_recovery_and_mailed_code_requests_are_challenged_too(client, double):
    from synwevo_core.identity import Identity
    ident: Identity = client.app_state.identity
    user = ident.create_user("holder@example.test", "harbor lights guide the evening ferry home", status="active")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    r = client.post("/api/auth/forgot", json={"email": "holder@example.test"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_required" and _mails(client) == 0
    double.answer("forgot")
    r = client.post("/api/auth/forgot", json={"email": "holder@example.test", "challenge_token": "tok"})
    assert r.status_code == 200 and _mails(client) == 1
    r = client.post("/api/auth/login/email/start", json={"email": "holder@example.test"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_required" and _mails(client) == 1
    double.answer("login_email")
    r = client.post("/api/auth/login/email/start", json={"email": "holder@example.test", "challenge_token": "tok"})
    assert r.status_code == 202 and _mails(client) == 2
    r = client.post("/api/auth/email/resend", json={"email": "holder@example.test"})
    assert r.status_code == 400 and r.json()["error"] == "challenge_required"


def test_password_login_is_challenged_only_after_repeated_failures_from_the_address(client, double):
    from synwevo_core.identity import Identity
    ident: Identity = client.app_state.identity
    user = ident.create_user("holder@example.test", "harbor lights guide the evening ferry home", status="active")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    good = {"email": "holder@example.test", "password": "harbor lights guide the evening ferry home"}
    # A first sign-in is not challenged: ordinary use never sees the widget.
    assert client.post("/api/auth/login", json=good).status_code == 200 and double.calls == []
    client.post("/api/auth/logout")
    for _ in range(3):
        r = client.post("/api/auth/login", json={"email": "holder@example.test", "password": "wrong password here"})
        assert r.status_code in (401, 429)
    # Three failures from this address: the next attempt must carry a token, and the password
    # is not even checked without one (no attempt is burnt, no timing oracle is offered).
    r = client.post("/api/auth/login", json=good)
    assert r.status_code == 400 and r.json()["error"] == "challenge_required" and double.calls == []
    double.answer("login")
    r = client.post("/api/auth/login", json=good | {"challenge_token": "tok"})
    assert r.status_code in (200, 429), r.text        # 429 only if the account backoff is still running
    if r.status_code == 429:
        assert r.json()["error"] == "too_many_attempts"


def test_without_a_configured_verifier_nothing_changes(tmp_path, monkeypatch):
    for k in ("SYNWEVO_TURNSTILE_SITE_KEY", "SYNWEVO_TURNSTILE_SECRET", "SYNWEVO_TURNSTILE_HOSTNAMES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    with TestClient(api) as c:
        assert c.get("/api/auth/state").json()["challenge"] is None
        r = c.post("/api/auth/register", json=REG)
        assert r.status_code == 202, r.text
        # A token from a stale page is ignored, not refused: the policy is the server's.
        r = c.post("/api/auth/forgot", json={"email": REG["email"], "challenge_token": "stale"})
        assert r.status_code == 200
