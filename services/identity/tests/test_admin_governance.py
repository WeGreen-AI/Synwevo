"""Governance with isolated SQLite fixtures and the real local file mail transport."""
import json
import re

import pytest
from fastapi.testclient import TestClient
from synwevo_core.identity import Identity, registration_mode
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, now
from synwevo_identity.app import create_app
from synwevo_identity.invites import Invites
from services.identity.tests.oauth_consent_helper import through_consent

PW = "a-sufficiently-long-fixture-passphrase"
REASON = "Local fixture governance verification"


def post(c, path, body):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    return c.post(path, json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def reauth(c):
    r = post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    root = tmp_path / "fixture-data"
    for key, value in {"DATA_DIR": str(root), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "fixture-mail"),
                       "PUBLIC_URL": "http://testserver", "REGISTRATION": "invite"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    api = create_app(str(root), start_jobs=False)
    store = Store(root)
    ident = Identity(store)
    # This is a real persisted setting in a disposable fixture, never repository data/.
    store.execute("INSERT INTO meta(key,value) VALUES ('registration.mode','open')")
    admin = ident.create_user("admin@example.test", PW, status="active", platform_role="admin")
    user = ident.create_user("owner@example.test", PW, status="active")
    operator = ident.create_user("operator@example.test", PW, status="active", platform_role="operator")
    org = Organizations(store, ident).create(user["id"], "Fixture organization", "Fixture workspace", "fixture-org-001")
    clients = []
    for u in (admin, user, operator):
        c = TestClient(api)
        assert post(c, "/api/auth/login", {"email": u["email"], "password": PW}).status_code == 200
        clients.append(c)
    yield {"api": api, "store": store, "ident": ident, "admin": admin, "user": user, "org": org,
           "c": clients[0], "ordinary": clients[1], "operator": clients[2], "maildir": tmp_path / "fixture-mail"}
    for c in clients:
        c.close()
    api.state.mailer.stop()


def test_policy_persists_and_db_wins_over_env(fixture, monkeypatch):
    f = fixture
    assert registration_mode(f["store"]) == "open"
    assert registration_mode() == "invite"
    path = "/api/admin/registration"
    assert post(f["c"], path, {"mode": "closed", "reason": REASON}).json()["error"] == "reauth_required"
    reauth(f["c"])
    assert post(f["c"], path, {"mode": "closed", "reason": "short"}).status_code == 400
    r = post(f["c"], path, {"mode": "closed", "expected_mode": "open", "reason": REASON})
    assert r.json() == {"mode": "closed", "source": "database"}
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    assert registration_mode(Store(f["store"].root)) == "closed"
    assert post(f["c"], path, {"mode": "invite", "expected_mode": "open", "reason": REASON}).status_code == 409
    assert post(f["c"], path, {"mode": "bogus", "reason": REASON}).status_code == 400
    audit = f["store"].one("SELECT meta FROM audit_events WHERE action='admin.registration.update'")
    assert json.loads(audit["meta"])["reason"] == REASON


@pytest.mark.parametrize("who", ["ordinary", "operator"])
def test_workspace_org_owner_and_operator_cannot_enter_platform_governance(fixture, who):
    c = fixture[who]
    for path in ("registration", "mail", "organizations"):
        assert c.get("/api/admin/" + path).status_code == 404
    for path in ("registration", "mail/missing/retry", "organizations/missing/freeze",
                 "users/" + fixture["user"]["id"] + "/recovery"):
        assert post(c, "/api/admin/" + path, {"mode": "open", "reason": REASON}).status_code == 404


def test_declined_invitation_has_its_own_member_list_group(fixture):
    f = fixture
    wid = f["org"]["workspace"]["id"]
    invites = Invites(f["store"], f["ident"])
    invite, token = invites.create(workspace_id=wid, email=f["admin"]["email"],
                                   role="viewer", actor_id=f["user"]["id"])
    invites.decline(token, f["admin"]["id"])
    response = f["ordinary"].get(f"/api/auth/workspaces/{wid}/invitations")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["groups"]["declined"] == 1
    assert result["groups"]["pending"] == result["groups"]["revoked"] == 0
    assert result["items"][0]["id"] == invite["id"]
    assert result["items"][0]["state"] == "declined"
    assert token not in response.text and "token_hash" not in response.text


def dispatch(f):
    reauth(f["c"])
    r = post(f["c"], "/api/admin/users/" + f["user"]["id"] + "/recovery", {"reason": REASON})
    assert r.status_code == 200, r.text
    return r.json()["delivery"]


def test_recovery_queues_real_local_mail_with_no_credentials_in_response(fixture):
    f = fixture
    path = "/api/admin/users/" + f["user"]["id"] + "/recovery"
    assert post(f["c"], path, {"reason": REASON}).json()["error"] == "reauth_required"
    d = dispatch(f)
    assert d["status"] == "queued" and d["to_addr"] == f["user"]["email"]
    raw = f["store"].one("SELECT body FROM mail_outbox WHERE id=?", (d["id"],))["body"]
    token = re.search(r"token=([A-Za-z0-9_-]+)", raw)[1]
    assert token not in json.dumps(d) and "body" not in d and "password" not in d
    assert Invites(f["store"], f["ident"]).peek_email_token(token, "reset")["user_id"] == f["user"]["id"]
    assert f["api"].state.mailer.drain_once() == 1
    assert len(list(f["maildir"].glob("*.eml"))) == 1
    assert post(f["c"], path, {"reason": REASON}).status_code == 429
    assert token not in f["c"].get("/api/admin/mail").text
    assert token not in f["c"].get("/api/admin/audit").text


def test_terminal_file_failure_retry_regenerates_token_and_is_single_use(fixture):
    f = fixture
    d = dispatch(f)
    raw = f["store"].one("SELECT body FROM mail_outbox WHERE id=?", (d["id"],))["body"]
    token = re.search(r"token=([A-Za-z0-9_-]+)", raw)[1]
    # Real transport failure: the output directory is a file, on the final allowed attempt.
    f["maildir"].write_text("fixture prevents directory creation")
    f["store"].execute("UPDATE mail_outbox SET attempts=7 WHERE id=?", (d["id"],))
    assert f["api"].state.mailer.drain_once() == 0
    old = f["c"].get("/api/admin/mail").json()["items"][0]
    assert old["status"] == "failed" and old["retryable"]
    f["maildir"].unlink()
    f["store"].execute("UPDATE email_tokens SET created_at=?", (now()-61,))
    r = post(f["c"], f"/api/admin/mail/{d['id']}/retry", {"reason": REASON})
    assert r.status_code == 200, r.text
    assert r.json()["delivery"]["id"] != d["id"]
    assert Invites(f["store"], f["ident"]).peek_email_token(token, "reset") is None
    assert post(f["c"], f"/api/admin/mail/{d['id']}/retry", {"reason": REASON}).status_code == 409
    assert f["api"].state.mailer.drain_once() == 1


@pytest.mark.parametrize("failure", ["delivery_unknown", "delivery_in_progress", "smtp password=fixture-secret token=secret"])
def test_ambiguous_or_legacy_failure_never_replays_and_errors_are_sanitized(fixture, failure):
    f = fixture
    d = dispatch(f)
    f["store"].execute("UPDATE mail_outbox SET status='failed',last_error=? WHERE id=?", (failure, d["id"]))
    response = f["c"].get("/api/admin/mail")
    assert "fixture-secret" not in response.text and "token=" not in response.text
    assert not response.json()["items"][0]["retryable"]
    assert post(f["c"], f"/api/admin/mail/{d['id']}/retry", {"reason": REASON}).status_code == 409


def test_disabled_delivery_rolls_back_token_rotation_and_audit(fixture, monkeypatch):
    f = fixture
    token = Invites(f["store"], f["ident"]).issue_email_token(f["user"]["id"], "reset", ttl_s=3600)
    f["store"].execute("UPDATE email_tokens SET created_at=?", (now()-61,))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    reauth(f["c"])
    r = post(f["c"], "/api/admin/users/" + f["user"]["id"] + "/recovery", {"reason": REASON})
    assert r.status_code == 503
    assert Invites(f["store"], f["ident"]).peek_email_token(token, "reset")
    assert not f["store"].one("SELECT id FROM audit_events WHERE action='admin.user.recovery'")
    assert not f["store"].one("SELECT id FROM mail_outbox")


def test_org_governance_preserves_actual_ownership_and_no_document_access(fixture):
    f = fixture
    row = f["c"].get("/api/admin/organizations?q=Fixture").json()["items"][0]
    assert row["role"] is None and row["members"] == 1 and row["workspaces"] == 1
    reauth(f["c"])
    assert post(f["c"], f"/api/admin/organizations/{row['id']}/freeze", {"reason": REASON}).status_code == 404
    own = Organizations(f["store"], f["ident"]).create(f["admin"]["id"], "Admin fixture org", "Owner space", "fixture-admin-001")
    oid = own["organization"]["id"]
    assert post(f["c"], f"/api/admin/organizations/{oid}/freeze", {"reason": REASON}).status_code == 200
    assert f["store"].one("SELECT status FROM organizations WHERE id=?", (oid,))["status"] == "frozen"
    assert f["c"].get("/api/admin/organizations?status=frozen").json()["total"] == 1
    assert not f["store"].one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (row["id"], f["admin"]["id"]))


def test_mail_search_paging_is_bounded_and_keeps_insertion_snapshot(fixture):
    f = fixture
    for n in range(4):
        f["api"].state.mailer.enqueue(f"fixture{n}@example.test", "Fixture", "Fixture", kind="security")
    first = f["c"].get("/api/admin/mail?q=fixture&limit=2").json()
    f["api"].state.mailer.enqueue("fixture-new@example.test", "Fixture", "Fixture", kind="security")
    second = f["c"].get("/api/admin/mail", params={"q": "fixture", "limit": 2, "offset": 2, "snapshot": first["snapshot"]}).json()
    assert first["total"] == second["total"] == 4
    assert len({i["id"] for i in first["items"]+second["items"]}) == 4
    assert f["c"].get("/api/admin/mail?limit=201").status_code == 422
    assert f["c"].get("/api/admin/organizations?offset=-1").status_code == 422


def test_real_org_and_workspace_admin_roles_do_not_grant_platform_management(fixture):
    f = fixture
    oid = f["org"]["organization"]["id"]
    wid = f["org"]["workspace"]["id"]
    # A fixture membership is explicit authority, not a mock authorization result.
    candidate = f["ident"].create_user("governor@example.test", PW, status="active")
    f["store"].execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at) "
                       "VALUES (?,?,'admin',?,?)", (oid, candidate["id"], now(), now()))
    f["ident"].add_member(wid, candidate["id"], "admin", actor_id=f["user"]["id"])
    c = TestClient(f["api"])
    assert post(c, "/api/auth/login", {"email": candidate["email"], "password": PW}).status_code == 200
    assert c.get(f"/api/auth/workspaces/{wid}/members").status_code == 200
    for path in ("/api/admin/registration", "/api/admin/mail", "/api/admin/organizations"):
        assert c.get(path).status_code == 404
    assert post(c, "/api/admin/users/" + f["user"]["id"] + "/recovery", {"reason": REASON}).status_code == 404
    # Even a platform administrator who is an organization admin cannot impersonate its owner.
    f["ident"].set_platform_role(candidate["id"], "admin")
    reauth(c)
    assert post(c, f"/api/admin/organizations/{oid}/freeze", {"reason": REASON}).status_code == 403
    assert f["store"].one("SELECT status FROM organizations WHERE id=?", (oid,))["status"] == "active"
    c.close()


def test_org_lifecycle_checks_reauth_reason_and_never_adds_workspace_access(fixture):
    from synwevo_core.apps import Apps
    f = fixture
    oid = f["org"]["organization"]["id"]
    wid = f["org"]["workspace"]["id"]
    # Grant only organization ownership; deliberately no workspace membership.
    f["store"].execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at) "
                       "VALUES (?,?,'owner',?,?)", (oid, f["admin"]["id"], now(), now()))
    path = f"/api/admin/organizations/{oid}"
    assert post(f["c"], path + "/freeze", {"reason": REASON}).json()["error"] == "reauth_required"
    reauth(f["c"])
    assert post(f["c"], path + "/freeze", {"reason": "short"}).status_code == 400
    for action, expected in (("freeze", "frozen"), ("delete", "deleted"), ("recover", "frozen"), ("unfreeze", "active")):
        result = post(f["c"], path + "/" + action, {"reason": REASON})
        assert result.status_code == 200, result.text
        assert result.json()["organization"]["status"] == expected
        assert f["ident"].membership(wid, f["admin"]["id"]) is None
        assert not Apps(f["store"], f["ident"]).check_access(f["admin"]["id"], "docweave", wid)[0]


def test_verification_retry_regenerates_only_for_unverified_accounts(fixture):
    from synwevo_identity.mail import verify_mail
    f = fixture
    user = f["user"]
    token = Invites(f["store"], f["ident"]).issue_email_token(user["id"], "verify", ttl_s=86400, new_email=user["email"])
    subject, body, html = verify_mail(token=token, expires_h=24)
    mail = f["api"].state.mailer.enqueue(user["email"], subject, body, kind="verify", ref_id=user["id"], html=html)
    f["store"].execute("UPDATE mail_outbox SET status='failed',last_error='smtp_recipient_rejected_550' WHERE id=?", (mail["id"],))
    f["store"].execute("UPDATE email_tokens SET created_at=?", (now()-61,))
    reauth(f["c"])
    assert post(f["c"], f"/api/admin/mail/{mail['id']}/retry", {"reason": "short"}).status_code == 400
    r = post(f["c"], f"/api/admin/mail/{mail['id']}/retry", {"reason": REASON})
    assert r.status_code == 200, r.text
    assert Invites(f["store"], f["ident"]).peek_email_token(token, "verify") is None
    replacement = r.json()["delivery"]["id"]
    f["store"].execute("UPDATE mail_outbox SET status='failed',last_error='smtp_rejected_550' WHERE id=?", (replacement,))
    f["store"].execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
    assert post(f["c"], f"/api/admin/mail/{replacement}/retry", {"reason": REASON}).json()["error"] == "already_verified"


def test_suspended_account_recovery_and_sensitive_message_replay_are_refused(fixture):
    f = fixture
    reauth(f["c"])
    f["ident"].set_status(f["user"]["id"], "suspended")
    assert post(f["c"], "/api/admin/users/" + f["user"]["id"] + "/recovery", {"reason": REASON}).status_code == 409
    for kind in ("invite", "security", "email_change"):
        mail = f["api"].state.mailer.enqueue("fixture@example.test", "Fixture-only secret subject", "Fixture-only secret body", kind=kind)
        f["store"].execute("UPDATE mail_outbox SET status='failed',last_error='transport_unavailable' WHERE id=?", (mail["id"],))
        assert post(f["c"], f"/api/admin/mail/{mail['id']}/retry", {"reason": REASON}).status_code == 409
    metadata = f["c"].get("/api/admin/mail").json()
    assert "secret" not in json.dumps(metadata)
    allowed = {"id", "to_addr", "kind", "ref_id", "status", "attempts", "created_at", "sent_at", "next_attempt_at", "error_code", "retryable"}
    assert all(set(row) == allowed for row in metadata["items"])


def test_real_factory_registration_and_public_state_honor_saved_policy(fixture):
    f = fixture
    anonymous = TestClient(f["api"])
    reauth(f["c"])
    for mode in ("closed", "invite", "open"):
        assert post(f["c"], "/api/admin/registration", {"mode": mode, "reason": REASON}).status_code == 200
        state = anonymous.get("/api/auth/state")
        assert state.status_code == 200 and state.json()["registration"] == mode
        result = post(anonymous, "/api/auth/register", {
            "email": f"registration-{mode}@example.test", "password": PW, "locale": "en"})
        assert result.status_code == (202 if mode == "open" else 403), result.text
    user = f["ident"].by_email("registration-open@example.test")
    assert user["status"] == "pending" and user["platform_role"] == "none"
    assert f["store"].one("SELECT status FROM mail_outbox WHERE kind='verify' AND ref_id=?", (user["id"],))["status"] == "queued"
    anonymous.close()


def protocol_code(f, client):
    """Real authorization-code grant in the isolated account factory, no callback network."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.oidc import Clients
    bootstrap_docs_app(f["store"], f["ident"])
    Clients(f["store"]).register(client_id="governance-review", app_id="docweave", name="Fixture review",
        redirect_uris=["http://localhost/auth/callback"], scopes="openid offline_access")
    verifier = "isolated-fixture-proof-key-with-more-than-43-characters"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    result = through_consent(client, client.get("/oidc/authorize", params={"client_id": "governance-review", "response_type": "code",
        "redirect_uri": "http://localhost/auth/callback", "scope": "openid offline_access", "state": "fixture",
        "code_challenge": challenge, "code_challenge_method": "S256"}, follow_redirects=False))
    assert result.status_code == 303, result.text
    code = parse_qs(urlsplit(result.headers["location"]).query)["code"][0]
    return {"grant_type": "authorization_code", "code": code, "client_id": "governance-review",
            "redirect_uri": "http://localhost/auth/callback", "code_verifier": verifier}


def test_code_exchange_and_device_revocation_cannot_leave_a_late_live_grant(fixture, monkeypatch):
    """Only the thread schedule is injected; all protocol and persistence operations are real."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from synwevo_core.oidc import Grants
    from synwevo_core.sessions import Sessions
    f = fixture
    client = f["ordinary"]
    sid = client.get("/api/auth/me").json()["session"]["id"]
    form = protocol_code(f, client)
    issue = Grants.issue_tokens
    revocations = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def interleave(self, **kwargs):
            started = Event()
            def revoke():
                started.set()
                return Sessions(Store(f["store"].root)).revoke_device(sid, "fixture_concurrent_logout")
            future = pool.submit(revoke)
            revocations.append(future)
            assert started.wait(5)
            try:
                future.result(timeout=0.1)
            except TimeoutError:
                pass  # Exchange must hold the SQLite write transaction until issuance ends.
            return issue(self, **kwargs)
        monkeypatch.setattr(Grants, "issue_tokens", interleave)
        response = client.post("/oidc/token", data=form)
        assert response.status_code == 200, response.text
        assert revocations[0].result(timeout=5)["tokens_revoked"] == 1
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/oidc/userinfo", headers={"Authorization": "Bearer " + response.json()["access_token"]}).status_code == 401
    assert f["store"].one("SELECT COUNT(*) AS n FROM client_sessions WHERE logged_out_at IS NULL")["n"] == 0


@pytest.mark.parametrize("expiry", ["idle_expires_at", "absolute_expires_at"])
def test_unspent_code_cannot_outlive_its_signin_clock(fixture, expiry):
    f = fixture
    form = protocol_code(f, f["ordinary"])
    sid = f["ordinary"].get("/api/auth/me").json()["session"]["id"]
    f["store"].execute(f"UPDATE sessions SET {expiry}=? WHERE id=?", (now()-1, sid))
    response = f["ordinary"].post("/oidc/token", data=form)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
    assert not f["store"].one("SELECT id FROM oauth_tokens")


def test_revoking_a_legacy_rp_device_resolves_root_without_ending_the_current_device(fixture):
    from synwevo_core.sessions import Sessions
    f = fixture
    with TestClient(f["api"]) as other:
        assert post(other, "/api/auth/login", {"email": f["user"]["email"], "password": PW}).status_code == 200
        grant = other.post("/oidc/token", data=protocol_code(f, other))
        assert grant.status_code == 200, grant.text
        linked = f["store"].one("SELECT * FROM client_sessions")
        raw, rp = Sessions(f["store"]).create(f["user"]["id"], workspace_id=None, amr="password",
                                              oidc_sid=linked["sid"], central_session_id=None)
        from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
        response = f["ordinary"].delete("/api/auth/sessions/" + rp["id"],
            headers={CSRF_HEADER: f["ordinary"].cookies.get(CSRF_COOKIE)})
        assert response.status_code == 200, response.text
        assert Sessions(f["store"]).load(raw) is None
        assert other.get("/api/auth/me").status_code == 401
        assert f["ordinary"].get("/api/auth/me").status_code == 200
        assert other.get("/oidc/userinfo", headers={"Authorization": "Bearer " + grant.json()["access_token"]}).status_code == 401


def test_revoke_other_devices_counts_roots_and_rps_but_preserves_current_family(fixture):
    from synwevo_core.sessions import Sessions
    f = fixture
    current = f["ordinary"]
    current_sid = current.get("/api/auth/me").json()["session"]["id"]
    current_grant = current.post("/oidc/token", data=protocol_code(f, current))
    assert current_grant.status_code == 200, current_grant.text
    current_link = f["store"].one("SELECT sid FROM client_sessions WHERE central_session_id=?", (current_sid,))
    current_raw, _ = Sessions(f["store"]).create(f["user"]["id"], workspace_id=None, amr="password",
                                               oidc_sid=current_link["sid"])
    with TestClient(f["api"]) as other:
        assert post(other, "/api/auth/login", {"email": f["user"]["email"], "password": PW}).status_code == 200
        other_sid = other.get("/api/auth/me").json()["session"]["id"]
        other_grant = other.post("/oidc/token", data=protocol_code(f, other))
        assert other_grant.status_code == 200, other_grant.text
        other_link = f["store"].one("SELECT sid FROM client_sessions WHERE central_session_id=?", (other_sid,))
        other_raw, _ = Sessions(f["store"]).create(f["user"]["id"], workspace_id=None, amr="password",
            oidc_sid=other_link["sid"], central_session_id=other_sid)
        response = post(current, "/api/auth/sessions/revoke-others", {})
        assert response.status_code == 200, response.text
        assert response.json()["revoked"] == 2
        assert response.json()["tokens_revoked"] == 1
        assert current.get("/api/auth/me").status_code == 200
        assert Sessions(f["store"]).load(current_raw) is not None
        assert other.get("/api/auth/me").status_code == 401
        assert Sessions(f["store"]).load(other_raw) is None
        assert current.get("/oidc/userinfo", headers={"Authorization": "Bearer " + current_grant.json()["access_token"]}).status_code == 200
        assert current.get("/oidc/userinfo", headers={"Authorization": "Bearer " + other_grant.json()["access_token"]}).status_code == 401
        assert post(current, "/api/auth/sessions/revoke-others", {}).json()["revoked"] == 0
