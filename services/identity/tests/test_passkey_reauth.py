"""Scoped reauthentication with real WebAuthn signatures in disposable account services.

The software authenticator is explicitly test equipment. It cannot establish real-browser
interoperability; separate browser evidence covers that. No verifier or policy is mocked.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import secrets
import threading

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from synwevo_core import journal
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, now
from synwevo_identity.passkeys import Passkeys

from .platform_fixture import make_platform_admin
from .test_passkeys import ORIGIN, RP_ID, PW, SoftAuthenticator, b64


class Authenticator(SoftAuthenticator):
    def __init__(self):
        super().__init__()
        self.cred_id = b"fixture-credential-" + secrets.token_bytes(16)

    def assertion(self, challenge, *, origin=ORIGIN, uv=True, up=True, handle=None, cross_origin=False):
        self.sign_count += 1
        self.user_verified = uv
        client = json.dumps({"type": "webauthn.get", "challenge": challenge,
                             "origin": origin, "crossOrigin": cross_origin}, separators=(",", ":")).encode()
        data = self._auth_data(attested=False)
        if not up:
            data = data[:32] + bytes([data[32] & ~1]) + data[33:]
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64(self.cred_id), "rawId": b64(self.cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64(client), "authenticatorData": b64(data),
                             "signature": b64(signature), "userHandle": b64(handle.encode()) if handle is not None else None},
                "clientExtensionResults": {}}


def post(client, path, body=None):
    return client.post(path, json=body or {}, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "fixture"), "PUBLIC_URL": ORIGIN, "ISSUER": ORIGIN,
                       "RP_ID": RP_ID, "UI_AUTH": "session", "COOKIE_SECURE": "0", "MAIL_MODE": "file",
                       "MAIL_DIR": str(tmp_path / "mail")}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "fixture"), start_jobs=False)
    store, clients = api.state.identity.store, []
    identity = Identity(store)
    user = identity.create_user("owner@example.test", PW, status="active")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))

    def login(who=user):
        client = TestClient(api, base_url=ORIGIN, raise_server_exceptions=False)
        clients.append(client)
        response = post(client, "/api/auth/login", {"email": who["email"], "password": PW})
        assert response.status_code == 200, response.text
        return client

    client = login()
    assert post(client, "/api/auth/reauth", {"scope": "mfa", "password": PW}).status_code == 200
    auth = Authenticator()
    opts = post(client, "/api/auth/passkeys/register/begin").json()
    registered = post(client, "/api/auth/passkeys/register/finish", {"credential": auth.register(opts["challenge"])})
    assert registered.status_code == 200, registered.text
    sid = Sessions(store).load(client.cookies.get(COOKIE_NAME), touch=False)["id"]
    store.execute("UPDATE sessions SET reauth_scope='',reauth_at=0 WHERE id=?", (sid,))
    yield {"api": api, "store": store, "identity": identity, "user": user, "client": client,
           "sid": sid, "key_id": registered.json()["id"], "auth": auth, "login": login}
    for c in clients:
        c.close()


def begin(w, scope="admin", client=None):
    response = post(client or w["client"], "/api/auth/reauth/passkey/begin", {"scope": scope})
    assert response.status_code == 200, response.text
    options = response.json()
    assert options["userVerification"] == "required" and options["rpId"] == RP_ID
    return options


def finish(w, options, scope="admin", client=None, assertion=None):
    return post(client or w["client"], "/api/auth/reauth/passkey/finish",
                {"scope": scope, "credential": assertion or w["auth"].assertion(options["challenge"])})


def session(w):
    return w["store"].one("SELECT * FROM sessions WHERE id=?", (w["sid"],))


def test_methods_only_list_current_strict_proofs_and_require_a_session(world):
    c, store = world["client"], world["store"]
    assert c.get("/api/auth/reauth/options?scope=admin").json() == {"scope": "admin", "methods": ["password", "passkey_uv"]}
    assert c.get("/api/auth/reauth/options?scope=").json()["error"] == "bad_scope"
    assert post(c, "/api/auth/reauth/passkey/begin", {"scope": {}}).json()["error"] == "bad_scope"
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (world["user"]["id"],))
    assert c.get("/api/auth/reauth/options?scope=mfa").json()["methods"] == ["passkey_uv"]
    store.execute("UPDATE passkeys SET rp_id='old.example.test' WHERE id=?", (world["key_id"],))
    assert c.get("/api/auth/reauth/options?scope=admin").json()["methods"] == []
    assert post(c, "/api/auth/reauth/passkey/begin", {"scope": "admin"}).json()["error"] == "no_usable_passkey"
    anonymous = TestClient(world["api"], base_url=ORIGIN)
    assert anonymous.get("/api/auth/reauth/options?scope=admin").status_code == 401
    assert post(anonymous, "/api/auth/reauth/passkey/begin", {"scope": "admin"}).status_code == 401
    assert c.post("/api/auth/reauth/passkey/begin", json={"scope": "admin"}, headers={CSRF_HEADER: "wrong"}).status_code == 403


def test_passkey_only_admin_confirms_existing_session_without_extending_it(world):
    store, user, c = world["store"], world["user"], world["client"]
    make_platform_admin(store, user)
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    store.execute("UPDATE sessions SET amr='email' WHERE id=?", (world["sid"],))
    before, cookie = session(world), c.cookies.get(COOKIE_NAME)
    count = store.one("SELECT COUNT(*) AS n FROM sessions")["n"]
    assert PlatformAccess(store).is_usable_super_admin(user["id"])
    opts = begin(world)
    result = finish(world, opts, assertion=world["auth"].assertion(opts["challenge"], handle=user["id"]))
    assert result.status_code == 200 and result.json()["amr"] == "email+passkey_uv", result.text
    assert result.json()["scope"] == "admin" and result.json()["valid_for_s"] > 0
    after = session(world)
    for field in ("id", "user_id", "created_at", "idle_expires_at", "absolute_expires_at", "persistent"):
        assert after[field] == before[field], field
    assert c.cookies.get(COOKIE_NAME) == cookie and "set-cookie" not in result.headers
    assert store.one("SELECT COUNT(*) AS n FROM sessions")["n"] == count
    assert Sessions.reauth_fresh(after, "admin", strict=True)
    assert not Sessions.reauth_fresh(after, "mfa", strict=True)
    changed = post(c, "/api/admin/registration", {"mode": "invite", "reason": "Fixture: confirmed admin policy"})
    assert changed.status_code == 200, changed.text
    assert store.one("SELECT sign_count FROM passkeys WHERE id=?", (world["key_id"],))["sign_count"] == 1
    event = store.one("SELECT meta FROM audit_events WHERE action='auth.reauth' AND object_kind='passkey'")
    assert json.loads(event["meta"])["method"] == "passkey_uv"


@pytest.mark.parametrize("mismatch", ["scope", "session", "user", "expired", "login", "registration"])
def test_challenge_is_bound_to_its_owner_session_scope_purpose_and_lifetime(world, mismatch):
    options = begin(world)
    body = world["auth"].assertion(options["challenge"])
    client, scope = world["client"], "admin"
    if mismatch == "scope":
        scope = "mfa"
    elif mismatch == "session":
        client = world["login"]()
    elif mismatch == "user":
        other = world["identity"].create_user("other@example.test", PW, status="active")
        client = world["login"](other)
    elif mismatch == "expired":
        world["store"].execute("UPDATE reauth_challenges SET expires_at=?", (now()-1,))
    elif mismatch == "login":
        options = post(client, "/api/auth/passkeys/authenticate/begin").json()
        body = world["auth"].assertion(options["challenge"])
    else:
        assert post(client, "/api/auth/reauth", {"scope": "mfa", "password": PW}).status_code == 200
        options = post(client, "/api/auth/passkeys/register/begin").json()
        body = world["auth"].assertion(options["challenge"])
    response = finish(world, options, scope, client, body)
    assert response.status_code == 400 and response.json()["error"] == "no_challenge", response.text
    assert not Sessions.reauth_fresh(session(world), "admin", strict=True)


def test_reauth_challenge_cannot_create_a_login_and_only_one_finish_succeeds(world):
    options = begin(world)
    assertion = world["auth"].assertion(options["challenge"])
    before = world["store"].one("SELECT COUNT(*) AS n FROM sessions")["n"]
    wrong = post(world["client"], "/api/auth/login/passkey", {"credential": assertion})
    assert wrong.status_code == 400 and wrong.json()["error"] == "no_challenge"
    assert world["store"].one("SELECT COUNT(*) AS n FROM sessions")["n"] == before
    barrier = threading.Barrier(2)
    def submit(_):
        barrier.wait(timeout=5)
        return finish(world, options, assertion=assertion)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in replies) == [200, 400]
    assert len(world["store"].all("SELECT * FROM audit_events WHERE action='auth.reauth' AND object_kind='passkey' AND result='ok'")) == 1
    assert finish(world, options, assertion=assertion).json()["error"] == "no_challenge"


@pytest.mark.parametrize("case", ["uv", "up", "origin", "rp", "handle", "cross_origin", "signature", "other_key"])
def test_signed_invalid_proofs_are_consumed_without_promoting_the_session(world, case):
    options = begin(world)
    kwargs = {"uv": False} if case == "uv" else {"up": False} if case == "up" else {}
    if case == "origin": kwargs["origin"] = "https://other.example.test"
    if case == "handle": kwargs["handle"] = "another-user"
    if case == "cross_origin": kwargs["cross_origin"] = True
    if case == "rp": world["auth"].rp_id = "other.example.test"
    auth = Authenticator() if case == "other_key" else world["auth"]
    assertion = auth.assertion(options["challenge"], **kwargs)
    if case == "signature": assertion["response"]["signature"] = b64(b"fixture-invalid-signature")
    before = session(world)
    response = finish(world, options, assertion=assertion)
    assert response.status_code == 401 and response.json()["error"] == "bad_assertion", response.text
    assert session(world)["reauth_at"] == before["reauth_at"]
    assert session(world)["reauth_scope"] == before["reauth_scope"]
    assert session(world)["amr"] == before["amr"]
    assert world["store"].one("SELECT sign_count FROM passkeys WHERE id=?", (world["key_id"],))["sign_count"] == 0
    assert world["store"].one("SELECT used_at FROM reauth_challenges WHERE challenge=?", (options["challenge"],))["used_at"]
    audited = json.dumps(world["store"].all("SELECT meta FROM audit_events WHERE action='auth.reauth'"))
    assert assertion["response"]["signature"] not in audited
    assert assertion["response"]["clientDataJSON"] not in audited
    assert finish(world, options, assertion=assertion).json()["error"] == "no_challenge"


@pytest.mark.parametrize("withdrawal", ["session", "user", "passkey"])
def test_withdrawal_after_challenge_claim_wins_before_the_success_transaction(world, monkeypatch, withdrawal):
    options = begin(world)
    original = Passkeys.spend_reauthentication
    def interleave(self, *args, **kwargs):
        row = original(self, *args, **kwargs)
        independent = Store(self.store.root)
        if withdrawal == "session":
            Sessions(independent).revoke(world["sid"], "fixture_logout")
        elif withdrawal == "user":
            Identity(independent).set_status(world["user"]["id"], "suspended")
        else:
            Passkeys(independent).remove(world["user"]["id"], world["key_id"])
        return row
    monkeypatch.setattr(Passkeys, "spend_reauthentication", interleave)
    response = finish(world, options)
    assert response.status_code == 401, response.text
    assert session(world)["reauth_scope"] == "" and session(world)["reauth_at"] == 0


@pytest.mark.parametrize("method,path", [("passkey_uv", "/api/auth/reauth/passkey/finish"),
                                         ("password", "/api/auth/reauth"), ("password", "/api/auth/mfa/reauth")])
def test_success_audit_failure_cannot_promote_the_session(world, monkeypatch, method, path):
    options = begin(world) if method == "passkey_uv" else None
    original = Store.execute
    def fail_after_audit(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and "auth.reauth" in params and "ok" in params:
            raise OSError("fixture audit failure; do not expose detail")
        return result
    monkeypatch.setattr(Store, "execute", fail_after_audit)
    body = {"scope": "admin", "credential": world["auth"].assertion(options["challenge"])} if options else {"scope": "admin", "password": PW}
    response = post(world["client"], path, body)
    assert response.status_code == 500
    assert session(world)["reauth_at"] == 0 and session(world)["reauth_scope"] == ""
    if options:
        assert world["store"].one("SELECT used_at FROM reauth_challenges WHERE challenge=?", (options["challenge"],))["used_at"]
        assert world["store"].one("SELECT sign_count FROM passkeys WHERE id=?", (world["key_id"],))["sign_count"] == 0


def test_bounded_begin_rate_and_cleanup(world):
    for _ in range(10):
        begin(world)
    response = post(world["client"], "/api/auth/reauth/passkey/begin", {"scope": "admin"})
    assert response.status_code == 429 and response.json()["error"] == "too_many_attempts"
    assert world["store"].one("SELECT COUNT(*) AS n FROM reauth_challenges")["n"] == 10
    world["store"].execute("UPDATE reauth_challenges SET expires_at=?", (now()-3601,))
    assert world["api"].state.passkeys.sweep() >= 10
    assert not world["store"].all("SELECT * FROM reauth_challenges")


def test_last_passkey_only_super_admin_cannot_remove_the_only_usable_factor(world):
    store, user = world["store"], world["user"]
    make_platform_admin(store, user)
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    response = finish(world, begin(world, "mfa"), "mfa")
    assert response.status_code == 200, response.text
    result = world["client"].delete(f"/api/auth/passkeys/{world['key_id']}", headers={CSRF_HEADER: world["client"].cookies.get(CSRF_COOKIE)})
    assert result.status_code == 409 and result.json()["error"] == "last_super_admin", result.text
    assert PlatformAccess(store).is_usable_super_admin(user["id"])
    assert not store.one("SELECT deleted_at FROM passkeys WHERE id=?", (world["key_id"],))["deleted_at"]
    assert not journal.read(store.root)
    # A stale-domain spare is not a way to administer this deployment.
    spare = Authenticator()
    options = world["api"].state.passkeys.begin_registration(user)
    made = world["api"].state.passkeys.finish_registration(user, spare.register(options["challenge"]))
    store.execute("UPDATE passkeys SET rp_id='old.example.test' WHERE id=?", (made["id"],))
    result = world["client"].delete(f"/api/auth/passkeys/{world['key_id']}", headers={CSRF_HEADER: world["client"].cookies.get(CSRF_COOKIE)})
    assert result.status_code == 409 and result.json()["error"] == "last_super_admin"


def test_usable_spare_preserves_admin_and_withdrawn_key_stays_removed_after_restore(world, tmp_path):
    store, user = world["store"], world["user"]
    make_platform_admin(store, user)
    spare = Authenticator()
    options = world["api"].state.passkeys.begin_registration(user)
    made = world["api"].state.passkeys.finish_registration(user, spare.register(options["challenge"]))
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    assert finish(world, begin(world, "mfa"), "mfa").status_code == 200
    from .test_platform_grant_restore import backup
    restored = Store(backup(store, tmp_path, "before-key-withdrawal"))
    result = world["client"].delete(f"/api/auth/passkeys/{world['key_id']}", headers={CSRF_HEADER: world["client"].cookies.get(CSRF_COOKIE)})
    assert result.status_code == 200 and result.json()["journaled"] is True, result.text
    journal.apply(restored, journal.read(store.root))
    assert not restored.one("SELECT 1 FROM passkeys WHERE id=?", (world["key_id"],))
    assert restored.one("SELECT 1 FROM passkeys WHERE id=? AND deleted_at IS NULL", (made["id"],))
    assert PlatformAccess(restored).is_usable_super_admin(user["id"])


def test_account_invalidation_and_restore_clear_pending_reauth(world, tmp_path):
    options = begin(world)
    from .test_platform_grant_restore import backup
    restored = Store(backup(world["store"], tmp_path, "before-account-withdrawal"))
    from synwevo_identity.profile import invalidate_access
    invalidate_access(world["store"], world["user"]["id"], "fixture_revoked")
    assert not world["store"].all("SELECT * FROM reauth_challenges")
    journal.apply(restored, [{"kind": "sessions_revoked", "at": now(), "user_id": world["user"]["id"]}])
    assert not restored.all("SELECT * FROM reauth_challenges")
    assert finish(world, options).status_code == 401


def add_spare(w):
    auth = Authenticator()
    options = w["api"].state.passkeys.begin_registration(w["user"])
    key = w["api"].state.passkeys.finish_registration(w["user"], auth.register(options["challenge"]))
    return key["id"]


def delete_key(w, key_id):
    return w["client"].delete(f"/api/auth/passkeys/{key_id}",
                              headers={CSRF_HEADER: w["client"].cookies.get(CSRF_COOKIE)})


def test_concurrent_key_deletions_cannot_remove_the_last_passkey_only_super(world):
    store, user = world["store"], world["user"]
    make_platform_admin(store, user)
    spare_id = add_spare(world)
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    assert finish(world, begin(world, "mfa"), "mfa").status_code == 200
    admitted = threading.Barrier(2, timeout=5)
    def remove(key_id):
        admitted.wait()
        return delete_key(world, key_id)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(remove, [world["key_id"], spare_id]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 409).json()["error"] == "last_super_admin"
    assert len(PlatformAccess(store).usable_super_admins()) == 1
    assert len(Passkeys(store).reauth_keys(user["id"])) == 1
    assert len(journal.read(store.root)) == 1
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='auth.passkey.remove' AND result='ok'")["n"] == 1


def test_passkey_only_super_and_password_super_racing_to_revoke_each_other_leave_one(world, monkeypatch):
    from synwevo_identity import admin_routes
    store, user = world["store"], world["user"]
    make_platform_admin(store, user)
    other = world["identity"].create_user("second-super@example.test", PW, status="active")
    make_platform_admin(store, other)
    second = world["login"](other)
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    assert finish(world, begin(world)).status_code == 200
    assert post(second, "/api/auth/reauth", {"scope": "admin", "password": PW}).status_code == 200
    grants = {uid: PlatformAccess(store).live_grants(uid)[0]["id"] for uid in (user["id"], other["id"])}
    admitted = threading.Barrier(2, timeout=10)
    original = admin_routes.require_platform
    def after_admission(request, *args, **kwargs):
        actor = original(request, *args, **kwargs)
        if not getattr(request.state, "fixture_admission_synchronized", False):
            request.state.fixture_admission_synchronized = True
            admitted.wait()
        return actor
    monkeypatch.setattr(admin_routes, "require_platform", after_admission)
    def revoke(pair):
        client, target = pair
        return post(client, f"/api/admin/platform-grants/{grants[target]}/revoke",
                    {"reason": "Fixture: simultaneous administrator handover"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(revoke, [(world["client"], other["id"]), (second, user["id"])]))
    assert sorted(r.status_code for r in responses) == [200, 404], [r.text for r in responses]
    assert len(PlatformAccess(store).usable_super_admins()) == 1
    assert store.one("SELECT COUNT(*) AS n FROM platform_role_grants WHERE role='super_admin' AND revoked_at IS NULL")["n"] == 1


@pytest.mark.parametrize("failure", ["journal", "audit"])
def test_failed_key_removal_preserves_the_key_and_records_no_success(world, monkeypatch, failure):
    assert finish(world, begin(world, "mfa"), "mfa").status_code == 200
    if failure == "journal":
        def fail(*args, **kwargs):
            raise OSError("fixture unavailable journal")
        monkeypatch.setattr(journal, "record", fail)
    else:
        original = Store.execute
        def fail(store, sql, params=()):
            result = original(store, sql, params)
            if sql.startswith("INSERT INTO audit_events") and "auth.passkey.remove" in params and "ok" in params:
                raise OSError("fixture unavailable audit")
            return result
        monkeypatch.setattr(Store, "execute", fail)
    response = delete_key(world, world["key_id"])
    assert response.status_code == (503 if failure == "journal" else 500), response.text
    assert not world["store"].one("SELECT deleted_at FROM passkeys WHERE id=?", (world["key_id"],))["deleted_at"]
    assert not world["store"].all("SELECT * FROM audit_events WHERE action='auth.passkey.remove' AND result='ok'")
    assert not journal.read(world["store"].root)


def test_restore_without_the_successor_key_refuses_to_remove_the_only_admin(world, tmp_path):
    from .test_platform_grant_restore import backup
    store, user = world["store"], world["user"]
    make_platform_admin(store, user)
    store.execute("DELETE FROM user_passwords WHERE user_id=?", (user["id"],))
    restored = Store(backup(store, tmp_path, "before-successor-key"))
    add_spare(world)
    assert finish(world, begin(world, "mfa"), "mfa").status_code == 200
    assert delete_key(world, world["key_id"]).status_code == 200
    with pytest.raises(ValueError, match="no usable super administrator"):
        journal.apply(restored, journal.read(store.root))
    # Replay rolls back and refuses startup; a newer backup containing the successor is required.
    assert restored.one("SELECT 1 FROM passkeys WHERE id=?", (world["key_id"],))
    assert len(PlatformAccess(store).usable_super_admins()) == 1


@pytest.mark.parametrize("path", ["/api/auth/reauth", "/api/auth/mfa/reauth"])
def test_failed_denial_audit_cannot_erase_password_attempts_or_terminal_revocation(world, monkeypatch, caplog, path):
    from .test_account_security import _age
    original = Store.execute
    def failed_audit(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and any(x in params for x in ("auth.reauth", "auth.session.ended")):
            raise OSError("fixture-private-audit-error")
        return result
    monkeypatch.setattr(Store, "execute", failed_audit)
    store, before = world["store"], world["store"].all("SELECT * FROM audit_events ORDER BY id")
    for index in range(5):
        _age(store, "user:%")
        response = post(world["client"], path, {"scope": "admin", "password": "fixture-wrong-secret"})
        assert response.status_code == 401, response.text
        if index < 4:
            assert response.json()["error"] == "bad_credentials"
            assert world["identity"].attempt_count("reauth:" + world["sid"]) == index + 1
            assert store.one("SELECT count FROM auth_attempts WHERE bucket LIKE 'user:%'")["count"] >= 1
        else:
            assert response.json()["error"] == "session_ended"
    assert session(world)["revoked_reason"] == "reauth_failures"
    assert session(world)["reauth_scope"] == "" and session(world)["reauth_at"] == 0
    assert world["client"].get("/api/auth/me").status_code == 401
    assert store.all("SELECT * FROM audit_events ORDER BY id") == before, "partial failure audit INSERT must be rolled back"
    assert caplog.text.count("reauth_failure_audit_unavailable") == 5
    assert "fixture-private-audit-error" not in caplog.text and "fixture-wrong-secret" not in caplog.text
