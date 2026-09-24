"""Synthetic users/local mail sink; real registration, verification, engine and authorization."""
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import Parser
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_server.app import create_app

PASSWORD = "Synthetic local journey passphrase 2026!"
VERIFIED_PASSWORD = "Mailbox chosen final journey passphrase 2026!"


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key, value in {
        "UI_AUTH": "session", "COOKIE_SECURE": "0", "REGISTRATION": "open",
        "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
        "PUBLIC_URL": "http://accounts.localhost:18770", "ROLE": "all",
    }.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    api = create_app(str(tmp_path / "data"), role="all", start_jobs=False)
    return api, TestClient(api), api.state.identity, tmp_path / "mail"


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def delivered_token(api, sink, kind="verify"):
    assert api.state.mailer.drain_once() == 1
    files = sorted(sink.glob("*.eml"))
    msg = Parser(policy=policy.default).parsestr(files[-1].read_text())
    text = msg.get_body(preferencelist=("plain",)).get_content()
    link = re.search(r"http://accounts\.localhost:18770/[^\s]+", text).group()
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    return token, msg, link


@pytest.mark.parametrize("locale,subject", [("zh-Hans", "验证"), ("zh-Hant", "驗證"), ("en", "Verify")])
def test_public_registration_verification_personal_scope_and_locale(world, locale, subject):
    api, c, ident, sink = world
    body = {"email": "New.Person+demo@example.test", "password": PASSWORD,
            "display_name": "合成测试用户", "locale": locale, "intent": "personal"}
    r = post(c, "/api/auth/register", body)
    assert r.status_code == 202, r.text
    assert r.json() == {"status": "verification_pending", "delivery": "requested"}
    user = ident.by_email(body["email"])
    assert user["platform_role"] == "none" and user["status"] == "pending"
    assert user["locale"] == locale and ident.workspaces_for(user["id"]) == []
    assert ident.by_email("NewPerson+demo@example.test") is None
    assert post(c, "/api/auth/login", body).status_code == 200
    assert c.get("/api/auth/me").json()["email_verified"] is False
    assert post(c, "/api/projects", {"name": "Forbidden pending"}).status_code == 403
    assert post(c, "/api/auth/onboarding/personal").status_code == 403
    token, message, link = delivered_token(api, sink)
    assert subject in str(message["Subject"])
    assert "LOCAL FILE SINK" in str(message["X-Synwevo-Delivery"])
    # A link prefetch has no authority to consume a verification token.
    c.get(urlsplit(link).path + "?" + urlsplit(link).query)
    assert ident.get_user(user["id"])["status"] == "pending"
    refused = post(c, "/api/auth/email/verify", {"token": token})
    assert refused.status_code == 400 and refused.json()["error"] == "new_password_required"
    assert ident.get_user(user["id"])["status"] == "pending"
    verified = post(c, "/api/auth/email/verify", {"token": token, "new_password": VERIFIED_PASSWORD})
    assert verified.status_code == 200 and verified.json()["sign_in_required"] is True
    assert c.get("/api/auth/me").status_code == 401
    assert post(c, "/api/auth/email/verify", {"token": token}).status_code == 409
    assert post(c, "/api/auth/login", {"email": body["email"], "password": PASSWORD}).status_code == 401
    assert post(c, "/api/auth/login", {"email": body["email"], "password": VERIFIED_PASSWORD}).status_code == 200
    first = post(c, "/api/auth/onboarding/personal")
    assert first.status_code == 200, first.text
    wid = first.json()["workspace"]["id"]
    assert post(c, "/api/auth/onboarding/personal").json()["workspace"]["id"] == wid
    me = c.get("/api/auth/me").json()
    assert me["permissions"] == [] and me["locale"] == locale
    assert [w["id"] for w in me["workspaces"]] == [wid]
    assert wid != "default"
    made = post(c, "/api/projects", {"name": "Synthetic registration journey"})
    assert made.status_code in (200, 201), made.text
    assert c.get("/api/admin/api-platform/overview").status_code == 404
    assert post(c, "/api/auth/register", {**body, "platform_role": "admin"}).status_code == 400


def test_registration_replay_keeps_identity_password_and_single_mail(world):
    api, c, ident, _ = world
    body = {"email": "replay@example.test", "password": PASSWORD, "locale": "en"}
    assert post(c, "/api/auth/register", body).status_code == 202
    uid = ident.by_email(body["email"])["id"]
    assert post(c, "/api/auth/register", {**body, "password": "A replacement that must not be installed", "locale": "zh-Hant"}).status_code == 202
    assert ident.by_email(body["email"])["id"] == uid
    assert ident.by_email(body["email"])["locale"] == "en"
    assert ident.authenticate(body["email"], PASSWORD)["ok"]
    assert ident.store.one("SELECT COUNT(*) n FROM mail_outbox")["n"] == 1
    assert ident.workspaces_for(uid) == []


def test_concurrent_onboarding_uses_one_personal_workspace(world):
    _, _, ident, _ = world
    user = ident.create_user("concurrent@example.test", PASSWORD, status="active", with_personal_workspace=False)
    def create(_):
        isolated = Identity(Store(ident.store.root))
        return isolated.ensure_personal_workspace(user["id"])["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(create, range(8)))
    assert len(set(ids)) == 1
    assert len(ident.workspaces_for(user["id"])) == 1


def test_disabled_mail_is_not_reported_as_sent_and_pending_stays_blocked(world, monkeypatch):
    _, c, ident, _ = world
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    r = post(c, "/api/auth/register", {"email": "offline@example.test", "password": PASSWORD})
    assert r.status_code == 202 and r.json()["delivery"] == "unavailable"
    assert ident.by_email("offline@example.test")["status"] == "pending"
    assert ident.store.one("SELECT COUNT(*) n FROM mail_outbox")["n"] == 0
