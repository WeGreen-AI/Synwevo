"""The OIDC provider's credential lifecycle and token hardening.

Each test here pins a defect that was real: an unsigned logout token that verified, a client
secret that could not be rotated, a scope the client was never registered for, a token that
outlived the sign-in it came from. They are written as the attack or the operator mistake, then
the refusal.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import time
import urllib.parse

import pytest
from authlib.jose import JsonWebKey
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from services.identity.tests.oauth_consent_helper import through_consent

PW = "a-sufficiently-long-passphrase"
REDIRECT = "http://docweave.localhost:8765/auth/callback"
LOGOUT_BACK = "http://docweave.localhost:8765/auth/backchannel-logout"
POST_LOGOUT = "http://docweave.localhost:8765/signed-out"
ISS = "http://accounts.localhost:8770"


class Deliveries(list):
    """TEST DOUBLE for the back-channel POST: records what would be sent, can be told to fail."""

    fail = False

    def __call__(self, url, token, timeout=3.0):
        self.append({"url": url, "token": token})
        return (False, "HTTP 503") if self.fail else (True, None)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", ISS)
    monkeypatch.delenv("SYNWEVO_DOCS_RESOURCE", raising=False)
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    deliveries = Deliveries()
    import synwevo_identity.provider_routes as routes_mod
    real_build = routes_mod.build_router
    monkeypatch.setattr(routes_mod, "build_router",
                        lambda *a, **k: real_build(*a, **{**k, "deliver": deliveries}))
    # The auth routes end sessions through the module-level function, which looks the real
    # deliverer up at call time; point it at the same double.
    monkeypatch.setattr(oidc, "deliver_logout", deliveries)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    bootstrap_docs_app(store, ident)
    apps.enable_for_workspace(ws, "docweave")
    clients = oidc.Clients(store)
    clients.register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                     redirect_uris=[REDIRECT], post_logout_redirect_uris=[POST_LOGOUT],
                     backchannel_logout_uri=LOGOUT_BACK,
                     scopes="openid profile email offline_access docweave.read")
    _, secret = clients.register(client_id="nightly", app_id="docweave", name="夜间任务", redirect_uris=[],
                                 confidential=True, kind="service", workspace_id=ws,
                                 api_scopes="docweave.read docweave.write",
                                 audiences=[oidc.docs_resource(), "urn:example:other-api"])
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "user": user, "ws": ws,
            "clients": clients, "secret": secret, "deliveries": deliveries}


def _csrf(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}


def _grant(c, scope="openid offline_access"):
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": scope, "state": "s", "nonce": "n", "code_challenge": ch, "code_challenge_method": "S256"}
    resp = through_consent(c, c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False))
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(resp.headers["location"]).query))["code"]
    out = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": REDIRECT, "client_id": "docweave-web",
                                      "code_verifier": v})
    assert out.status_code == 200, out.text
    return out.json()


def _service_token(env, secret=None, **extra):
    data = {"grant_type": "client_credentials", "client_id": "nightly",
            "client_secret": secret if secret is not None else env["secret"], **extra}
    return env["c"].post("/oidc/token", data=data)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


# ---------------------------------------------------------------- F001: logout token signature
def _logout_claims(env, **over):
    claims = {"iss": ISS, "aud": "docweave-web", "iat": int(time.time()), "exp": int(time.time()) + 120,
              "jti": "lt_forged", "sub": env["user"]["id"], "sid": "cs_x",
              "events": {"http://schemas.openid.net/event/backchannel-logout": {}}}
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def test_an_unsigned_logout_token_is_rejected(env):
    """alg=none used to verify against any key set: anyone could log anyone out."""
    jwks = env["c"].get("/oidc/jwks.json").json()
    kid = jwks["keys"][0]["kid"]
    forged = _b64({"alg": "none", "kid": kid}) + "." + _b64(_logout_claims(env)) + "."
    with pytest.raises(ServiceError) as e:
        oidc.verify_logout_token(forged, jwks=jwks, client_id="docweave-web", expected_issuer=ISS)
    assert e.value.code == "bad_logout_token" and e.value.status == 400


def test_a_foreign_key_fails_on_the_signature_not_on_freshness(env):
    """Forged with a *valid* iat/exp so the only thing wrong is the signature."""
    from authlib.jose import jwt as _jwt
    jwks = env["c"].get("/oidc/jwks.json").json()
    other = JsonWebKey.generate_key("RSA", 2048, is_private=True).as_dict(is_private=True)
    other["kid"] = jwks["keys"][0]["kid"]
    forged = _jwt.encode({"alg": "RS256", "kid": other["kid"]}, _logout_claims(env), other).decode()
    with pytest.raises(ServiceError) as e:
        oidc.verify_logout_token(forged, jwks=jwks, client_id="docweave-web", expected_issuer=ISS)
    assert e.value.code == "bad_logout_token" and "无法验证" in e.value.message


def test_a_logout_token_needs_a_jti_and_a_live_exp(env):
    keys = oidc.Keys(env["store"])
    key = keys.active()
    priv = json.loads(key["private_jwk"])
    from authlib.jose import jwt as _jwt
    jwks = keys.jwks()
    for over, why in (({"jti": None}, "jti"), ({"exp": int(time.time()) - 600}, "过期"), ({"exp": None}, "exp")):
        tok = _jwt.encode({"alg": "RS256", "kid": key["kid"]}, _logout_claims(env, **over), priv).decode()
        with pytest.raises(ServiceError) as e:
            oidc.verify_logout_token(tok, jwks=jwks, client_id="docweave-web", expected_issuer=ISS)
        assert why in e.value.message, (over, e.value.message)
    good = _jwt.encode({"alg": "RS256", "kid": key["kid"]}, _logout_claims(env), priv).decode()
    assert oidc.verify_logout_token(good, jwks=jwks, client_id="docweave-web", expected_issuer=ISS)["jti"]


def test_decode_signed_refuses_alg_none_for_any_caller(env):
    jwks = oidc.Keys(env["store"]).jwks()
    with pytest.raises(Exception):
        oidc.decode_signed(_b64({"alg": "none"}) + "." + _b64({"sub": "x"}) + ".", jwks)


# ---------------------------------------------------------------- RFC 8707 resource indicators
def test_resource_sets_the_audience_and_must_be_registered(env):
    store = env["store"]
    ok = _service_token(env, resource=oidc.docs_resource())
    assert ok.status_code == 200, ok.text
    row = store.one("SELECT audience FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")
    assert row["audience"] == oidc.docs_resource()

    other = _service_token(env, resource="urn:example:other-api")
    assert other.status_code == 200
    assert store.one("SELECT audience FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")["audience"] == \
        "urn:example:other-api"

    bad = _service_token(env, resource="https://unknown.example/api")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_target"


def test_one_token_names_one_resource(env):
    base = {"grant_type": "client_credentials", "client_id": "nightly", "client_secret": env["secret"]}
    two = env["c"].post("/oidc/token", data={**base, "resource": [oidc.docs_resource(), "urn:example:other-api"]})
    assert two.status_code == 400 and two.json()["error"] == "invalid_target"
    same_twice = env["c"].post("/oidc/token", data={**base, "resource": [oidc.docs_resource()] * 2})
    assert same_twice.status_code == 200


def test_without_resource_the_first_registered_audience_is_used(env):
    assert _service_token(env).status_code == 200
    assert env["store"].one("SELECT audience FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")["audience"] \
        == oidc.docs_resource()


def test_a_docs_service_account_defaults_to_the_docs_resource(env, monkeypatch):
    c, _ = env["clients"].register(client_id="batch2", app_id="docweave", name="b", redirect_uris=[],
                                   confidential=True, kind="service", workspace_id=env["ws"],
                                   api_scopes="docweave.read")
    assert c["audiences"] == ["urn:synwevo:app:docweave"]
    monkeypatch.setenv("SYNWEVO_DOCS_RESOURCE", "https://docs.example.org/api")
    c, _ = env["clients"].register(client_id="batch3", app_id="docweave", name="b", redirect_uris=[],
                                   confidential=True, kind="service", workspace_id=env["ws"],
                                   api_scopes="docweave.read")
    assert c["audiences"] == ["https://docs.example.org/api"]


def test_a_refresh_keeps_the_audience_it_was_granted_with(env):
    store = env["store"]
    store.execute("UPDATE oauth_clients SET audiences=? WHERE client_id='docweave-web'",
                  (json.dumps(["urn:first", "urn:second"]),))
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": "openid offline_access", "state": "s", "code_challenge": ch, "code_challenge_method": "S256"}
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(through_consent(env["c"], env["c"].get(
        "/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)).headers["location"]).query))["code"]
    tok = env["c"].post("/oidc/token", data={"grant_type": "authorization_code", "code": code,
                                             "redirect_uri": REDIRECT, "client_id": "docweave-web",
                                             "code_verifier": v, "resource": "urn:second"}).json()
    r = env["c"].post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "docweave-web",
                                           "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    assert {row["audience"] for row in store.all("SELECT audience FROM oauth_tokens")} == {"urn:second"}


def test_failed_refresh_issuance_does_not_consume_the_only_live_grant(env, monkeypatch):
    """Fault injection at persistence: a failed successor must roll back its predecessor."""
    token = _grant(env["c"])
    grants = oidc.Grants(env["store"])
    client = env["clients"].get("docweave-web")

    def failed_issue(**kwargs):
        raise RuntimeError("synthetic issuance failure")

    with monkeypatch.context() as patch:
        patch.setattr(grants, "issue_tokens", failed_issue)
        with pytest.raises(RuntimeError, match="synthetic issuance failure"):
            grants.rotate_refresh(token["refresh_token"], client=client)
    assert grants.load_access(token["access_token"]) is not None
    successor, _ = grants.rotate_refresh(token["refresh_token"], client=client)
    assert grants.load_access(successor["access_token"]) is not None


def test_concurrent_revocation_cannot_fall_between_refresh_and_successor(env, monkeypatch):
    """Real independent SQLite connections; the only injected behavior is the schedule."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    token = _grant(env["c"])
    grants = oidc.Grants(env["store"])
    revoker = oidc.Grants(Store(env["store"].root))
    family = grants.load_access(token["access_token"])["family_id"]
    issue = grants.issue_tokens
    revocations = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        def interleave(**kwargs):
            future = pool.submit(revoker.revoke_family, family, "synthetic_security_revocation")
            revocations.append(future)
            try:
                future.result(timeout=0.5)
            except TimeoutError:
                pass  # Correct behavior: the refresh transaction holds the database write lock.
            return issue(**kwargs)

        monkeypatch.setattr(grants, "issue_tokens", interleave)
        successor, _ = grants.rotate_refresh(token["refresh_token"], client=env["clients"].get("docweave-web"))
        assert revocations[0].result(timeout=5) >= 1
    assert grants.load_access(successor["access_token"]) is None
    assert env["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE family_id=? AND revoked_at IS NULL",
                            (family,))["n"] == 0


# ---------------------------------------------------------------- scopes
def test_manage_is_granted_only_to_a_client_registered_for_it(env):
    r = _service_token(env, scope="docweave.manage")
    assert r.status_code == 400 and r.json()["error"] == "invalid_scope"
    env["store"].execute("UPDATE oauth_clients SET api_scopes='docweave.read docweave.manage' "
                         "WHERE client_id='nightly'")
    r = _service_token(env, scope="docweave.manage")
    assert r.status_code == 200 and r.json()["scope"] == "docweave.manage"


def test_registration_rejects_unknown_scopes(env):
    with pytest.raises(ServiceError) as e:
        env["clients"].register(client_id="x", app_id="docweave", name="x", redirect_uris=[REDIRECT],
                                scopes="openid admin:everything")
    assert e.value.code == "bad_scope"


# ---------------------------------------------------------------- F006/F315/F054/F346: secrets
def test_re_registering_does_not_print_a_secret_it_never_stored(env):
    kw = dict(client_id="nightly", app_id="docweave", name="改个名字", redirect_uris=[], confidential=True,
              kind="service", workspace_id=env["ws"], api_scopes="docweave.read docweave.write")
    row, secret = env["clients"].register(**kw)
    assert secret is None, "已有机密客户端再次登记不能给出一个根本没保存的新密钥"
    assert row["name"] == "改个名字"
    assert env["clients"].check_secret("nightly", env["secret"]) is True


def test_rotation_replaces_the_secret_and_ends_what_the_old_one_obtained(env):
    tok = _service_token(env).json()["access_token"]
    out = env["clients"].rotate_secret("nightly")
    oidc.Grants(env["store"]).revoke_for_client("nightly", "secret_rotated")     # what the CLI does
    assert env["clients"].check_secret("nightly", env["secret"]) is False, "泄露的旧密钥必须立即失效"
    assert env["clients"].check_secret("nightly", out["secret"]) is True
    assert _service_token(env).status_code == 401
    assert _service_token(env, out["secret"]).status_code == 200
    assert oidc.Grants(env["store"]).load_access(tok) is None


def test_a_grace_period_is_bounded_and_bound_to_the_secret_it_came_with(env):
    clients = env["clients"]
    first = clients.rotate_secret("nightly", grace_s=600)
    assert clients.check_secret("nightly", env["secret"]) is True, "宽限期内旧密钥仍可用"
    assert clients.check_secret("nightly", first["secret"]) is True
    assert first["grace_until"] and first["grace_until"] <= now() + 600 + 1
    # the window closes
    key = f"client_secret_grace:nightly"
    g = json.loads(env["store"].one("SELECT value FROM meta WHERE key=?", (key,))["value"])
    env["store"].execute("UPDATE meta SET value=? WHERE key=?", (json.dumps({**g, "until": now() - 1}), key))
    assert clients.check_secret("nightly", env["secret"]) is False
    # a later rotation (e.g. re-applied from the security journal after a restore) kills the
    # grace entry even if its window were still open
    env["store"].execute("UPDATE meta SET value=? WHERE key=?", (json.dumps({**g, "until": now() + 600}), key))
    env["store"].execute("UPDATE oauth_clients SET secret_hash='somethingelse' WHERE client_id='nightly'")
    assert clients.check_secret("nightly", env["secret"]) is False
    # and the ceiling holds
    env["store"].execute("UPDATE oauth_clients SET secret_hash=? WHERE client_id='nightly'",
                         (oidc._sha(first["secret"]),))
    capped = clients.rotate_secret("nightly", grace_s=10 * 24 * 3600)
    assert capped["grace_until"] <= now() + oidc.MAX_SECRET_GRACE_S + 1


def test_a_disabled_client_is_not_quietly_re_registered(env):
    env["clients"].disable("nightly")
    with pytest.raises(ServiceError) as e:
        env["clients"].register(client_id="nightly", app_id="docweave", name="x", redirect_uris=[],
                                confidential=True, kind="service", workspace_id=env["ws"])
    assert e.value.code == "client_disabled"
    assert env["clients"].get("nightly") is None and env["clients"].get_any("nightly")["disabled_at"]


def test_confidentiality_changes_are_explicit(env):
    # public -> confidential: the new secret is actually stored and the method changes with it
    row, secret = env["clients"].register(client_id="docweave-web", app_id="docweave", name="Docs",
                                          redirect_uris=[REDIRECT], confidential=True)
    assert secret and row["token_endpoint_auth_method"] == "client_secret_post"
    assert env["clients"].check_secret("docweave-web", secret) is True
    # confidential -> public is refused
    with pytest.raises(ServiceError) as e:
        env["clients"].register(client_id="docweave-web", app_id="docweave", name="Docs",
                                redirect_uris=[REDIRECT], confidential=False)
    assert e.value.code == "confidentiality_change"


# ---------------------------------------------------------------- F072: revocation by the owner only
def test_a_client_can_revoke_only_its_own_tokens(env):
    tok = _service_token(env).json()["access_token"]
    r = env["c"].post("/oidc/revoke", data={"token": tok, "client_id": "docweave-web"})
    assert r.status_code == 200, "RFC 7009：结果一律 200"
    assert oidc.Grants(env["store"]).load_access(tok), "别的客户端不能撤销不属于它的令牌"
    env["c"].post("/oidc/revoke", data={"token": tok, "client_id": "nightly", "client_secret": env["secret"]})
    assert oidc.Grants(env["store"]).load_access(tok) is None


# ---------------------------------------------------------------- F058: ending a sign-in ends its grants
def test_signing_a_device_out_revokes_the_tokens_it_minted(env):
    c = env["c"]
    tok = _grant(c)
    sid = env["store"].one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL",
                           (env["user"]["id"],))["id"]
    other = TestClient(env["api"])
    other.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    r = other.request("DELETE", f"/api/auth/sessions/{sid}", headers=_csrf(other))
    assert r.status_code == 200 and r.json()["tokens_revoked"] >= 1
    assert oidc.Grants(env["store"]).load_access(tok["access_token"]) is None
    refresh = other.post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "docweave-web",
                                              "refresh_token": tok["refresh_token"]})
    assert refresh.status_code == 400 and refresh.json()["error"] == "invalid_grant"


def test_signing_out_other_devices_keeps_only_this_one(env):
    old = TestClient(env["api"])
    old.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    stolen = _grant(old)
    mine = _grant(env["c"])
    r = env["c"].post("/api/auth/sessions/revoke-others", headers=_csrf(env["c"]))
    assert r.status_code == 200 and r.json()["tokens_revoked"] >= 1
    grants = oidc.Grants(env["store"])
    assert grants.load_access(stolen["access_token"]) is None
    assert grants.load_access(mine["access_token"]) is not None


def test_a_refresh_from_an_explicitly_ended_sign_in_is_refused(env):
    """Even when the token row itself was not revoked (a path that only ended the session)."""
    tok = _grant(env["c"])
    env["store"].execute("UPDATE sessions SET revoked_at=?, revoked_reason='logout' WHERE user_id=?",
                         (now(), env["user"]["id"]))
    r = TestClient(env["api"]).post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "docweave-web",
                                                         "refresh_token": tok["refresh_token"]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_an_idle_expired_sign_in_does_not_kill_an_offline_grant(env):
    tok = _grant(env["c"])
    env["store"].execute("UPDATE sessions SET idle_expires_at=? WHERE user_id=?", (now() - 1, env["user"]["id"]))
    r = TestClient(env["api"]).post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "docweave-web",
                                                         "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- F057 / F071: logout
def test_logout_ends_the_app_session_even_when_the_notification_fails(env):
    """The Docs session is a row in the same database; a failed POST no longer leaves it alive."""
    from synwevo_core.sessions import Sessions
    store = env["store"]
    _grant(env["c"])
    cs = store.one("SELECT sid FROM client_sessions")
    raw, rp_row = Sessions(store).create(env["user"]["id"], workspace_id=env["ws"], amr="password",
                                         oidc_sid=cs["sid"])
    env["deliveries"].fail = True
    env["c"].get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    assert store.one("SELECT revoked_at FROM sessions WHERE id=?", (rp_row["id"],))["revoked_at"]
    assert store.one("SELECT logout_error FROM client_sessions")["logout_error"] == "HTTP 503"


def test_an_invalid_logout_target_does_not_sign_anyone_out(env):
    c = env["c"]
    r = c.get("/oidc/logout?client_id=docweave-web&post_logout_redirect_uri="
              + urllib.parse.quote("https://evil.example/"), follow_redirects=False)
    assert r.status_code == 400
    assert c.get("/api/auth/me").status_code == 200, "参数不合法时不能先把人登出"


def test_logout_delivery_refuses_link_local_targets():
    ok, err = oidc.deliver_logout("https://169.254.169.254/latest/meta-data", "tok")
    assert ok is False and "169.254.169.254" in err
