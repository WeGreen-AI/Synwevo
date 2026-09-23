"""The OIDC provider core: keys, clients, codes, tokens, id_token claims.

Authlib implements the protocol; what is tested here is the part we wrote — storage, single-use
semantics, exact redirect matching, PKCE enforcement, and that the id_token carries how and when
the person actually authenticated rather than what their account happens to own.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest
from authlib.jose import JsonWebKey, jwt

from platform_core.identity import Identity
from platform_core import oidc
from platform_core.errors import ServiceError
from platform_core.store import Store, now

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_ISSUER", "http://accounts.localhost:8770")
    store = Store(tmp_path / "d")
    ident = Identity(store)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    clients = oidc.Clients(store)
    client, _ = clients.register(
        client_id="docweave-web", app_id="docweave", name="DocWeave",
        redirect_uris=["http://docweave.localhost:8765/auth/callback"],
        audiences=["docweave-api"])
    return {"store": store, "ident": ident, "user": user, "clients": clients,
            "client": client, "grants": oidc.Grants(store), "keys": oidc.Keys(store)}


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"v" * 48).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _code(env, **over):
    verifier, challenge = _pkce()
    kw = dict(client_id="docweave-web", user_id=env["user"]["id"],
              redirect_uri="http://docweave.localhost:8765/auth/callback",
              scope="openid profile email", nonce="n-123",
              code_challenge=challenge, code_challenge_method="S256",
              auth_time=now(), amr="password+totp", central_session_id="cs_1",
              workspace_id="w_1")
    kw.update(over)
    return env["grants"].issue_code(**kw), verifier


# ---------------------------------------------------------------- keys
def test_signing_key_persists_across_instances(env, tmp_path):
    """A key regenerated at startup would invalidate every token — a mass logout with no cause."""
    first = env["keys"].active()["kid"]
    again = oidc.Keys(Store(tmp_path / "d")).active()["kid"]
    assert first == again


def test_jwks_publishes_only_public_material(env):
    jwks = env["keys"].jwks()
    assert len(jwks["keys"]) == 1
    k = jwks["keys"][0]
    assert k["kty"] == "RSA" and k["use"] == "sig" and k["alg"] == "RS256" and k["kid"]
    assert "d" not in k and "p" not in k and "q" not in k, "私钥材料不能出现在 JWKS 里"


def test_rotation_keeps_the_previous_key_verifiable(env):
    old = env["keys"].active()["kid"]
    new, retiring = env["keys"].rotate()
    assert new != old and retiring == 1
    kids = {k["kid"] for k in env["keys"].jwks()["keys"]}
    assert old in kids and new in kids, "轮换期间旧密钥必须仍可验签，否则轮换看起来像故障"
    env["keys"].retire(old)
    assert old not in {k["kid"] for k in env["keys"].jwks()["keys"]}


def test_the_first_rotation_on_a_fresh_service_retires_nothing(tmp_path):
    """Reported honestly, so an operator is not told a previous key is still published when
    there never was one."""
    from platform_core.store import Store
    keys = oidc.Keys(Store(tmp_path / "fresh"))
    kid, retiring = keys.rotate()
    assert kid and retiring == 0
    assert len(keys.jwks()["keys"]) == 1


# ---------------------------------------------------------------- clients
def test_redirect_uris_match_exactly(env):
    c = env["client"]
    assert oidc.Clients.redirect_allowed(c, "http://docweave.localhost:8765/auth/callback")
    for bad in ("http://docweave.localhost:8765/auth/callback/",
                "http://docweave.localhost:8765/auth/callback?x=1",
                "http://docweave.localhost:8765/auth",
                "http://evil.localhost:8765/auth/callback",
                "http://docweave.localhost:8766/auth/callback"):
        assert not oidc.Clients.redirect_allowed(c, bad), bad


def test_registration_refuses_unsafe_redirects(env):
    for bad in ("not-a-url", "javascript:alert(1)", "https://x.example.com/cb#frag",
                "http://example.com/cb"):
        with pytest.raises(ServiceError):
            env["clients"].register(client_id="x", app_id="docweave", name="X", redirect_uris=[bad])


def test_a_confidential_client_stores_only_a_hash(env):
    row, secret = env["clients"].register(
        client_id="svc", app_id="docweave", name="S",
        redirect_uris=["http://docweave.localhost:8765/auth/callback"], confidential=True)
    assert secret
    stored = env["store"].one("SELECT secret_hash FROM oauth_clients WHERE client_id='svc'")["secret_hash"]
    assert stored != secret and len(stored) == 64
    assert env["clients"].check_secret("svc", secret) is True
    assert env["clients"].check_secret("svc", secret + "x") is False


def test_a_public_client_has_no_secret(env):
    assert env["client"]["secret_hash"] is None
    assert env["clients"].check_secret("docweave-web", "anything") is False


# ---------------------------------------------------------------- codes
def test_a_code_is_single_use(env):
    raw, verifier = _code(env)
    kw = dict(client_id="docweave-web", redirect_uri="http://docweave.localhost:8765/auth/callback",
              code_verifier=verifier)
    assert env["grants"].consume_code(raw, **kw)["user_id"] == env["user"]["id"]
    with pytest.raises(ServiceError) as e:
        env["grants"].consume_code(raw, **kw)
    assert e.value.code == "invalid_grant"


def test_replaying_a_code_revokes_what_it_already_produced(env):
    """A second presentation of a spent code means it leaked; the grant it made goes with it."""
    raw, verifier = _code(env)
    kw = dict(client_id="docweave-web", redirect_uri="http://docweave.localhost:8765/auth/callback",
              code_verifier=verifier)
    row = env["grants"].consume_code(raw, **kw)
    tokens = env["grants"].issue_tokens(client=env["client"], code_row=row, with_refresh=True)
    assert env["grants"].load_access(tokens["access_token"])

    with pytest.raises(ServiceError):
        env["grants"].consume_code(raw, **kw)
    assert env["grants"].load_access(tokens["access_token"]) is None, "重放授权码后旧令牌必须失效"


def test_a_code_cannot_be_redeemed_by_another_client(env):
    env["clients"].register(client_id="portal-web", app_id="portal", name="Portal",
                            redirect_uris=["http://portal.localhost:8771/auth/callback"])
    raw, verifier = _code(env)
    with pytest.raises(ServiceError) as e:
        env["grants"].consume_code(raw, client_id="portal-web",
                                   redirect_uri="http://docweave.localhost:8765/auth/callback",
                                   code_verifier=verifier)
    assert "不属于该客户端" in e.value.message


def test_redirect_uri_must_match_the_one_used_at_authorization(env):
    raw, verifier = _code(env)
    with pytest.raises(ServiceError):
        env["grants"].consume_code(raw, client_id="docweave-web",
                                   redirect_uri="http://docweave.localhost:8765/other",
                                   code_verifier=verifier)


def test_an_expired_code_is_refused(env):
    raw, verifier = _code(env)
    env["store"].execute("UPDATE oauth_codes SET expires_at=?", (now() - 1,))
    with pytest.raises(ServiceError):
        env["grants"].consume_code(raw, client_id="docweave-web",
                                   redirect_uri="http://docweave.localhost:8765/auth/callback",
                                   code_verifier=verifier)


# ---------------------------------------------------------------- PKCE
def test_pkce_is_verified(env):
    raw, _ = _code(env)
    kw = dict(client_id="docweave-web", redirect_uri="http://docweave.localhost:8765/auth/callback")
    with pytest.raises(ServiceError) as e:
        env["grants"].consume_code(raw, code_verifier="the-wrong-verifier", **kw)
    assert "PKCE" in e.value.message


def test_a_missing_verifier_is_refused(env):
    raw, _ = _code(env)
    with pytest.raises(ServiceError) as e:
        env["grants"].consume_code(raw, client_id="docweave-web",
                                   redirect_uri="http://docweave.localhost:8765/auth/callback",
                                   code_verifier=None)
    assert "code_verifier" in e.value.message


def test_plain_pkce_is_refused_rather_than_silently_skipped(env):
    raw, verifier = _code(env, code_challenge="whatever", code_challenge_method="plain")
    with pytest.raises(ServiceError) as e:
        env["grants"].consume_code(raw, client_id="docweave-web",
                                   redirect_uri="http://docweave.localhost:8765/auth/callback",
                                   code_verifier=verifier)
    assert "S256" in e.value.message


# ---------------------------------------------------------------- id_token
def test_id_token_verifies_against_the_published_jwks(env):
    raw, verifier = _code(env)
    row = env["grants"].consume_code(raw, client_id="docweave-web",
                                     redirect_uri="http://docweave.localhost:8765/auth/callback",
                                     code_verifier=verifier)
    tokens = env["grants"].issue_tokens(client=env["client"], code_row=row, with_refresh=False)
    tok = env["grants"].id_token(client=env["client"], user=env["user"], code_row=row,
                                 sid="sid-1", access_token=tokens["access_token"])

    jwks = JsonWebKey.import_key_set(env["keys"].jwks())
    claims = jwt.decode(tok, jwks)
    assert claims["iss"] == "http://accounts.localhost:8770"
    assert claims["sub"] == env["user"]["id"] and claims["aud"] == "docweave-web"
    assert claims["nonce"] == "n-123" and claims["sid"] == "sid-1"
    # how and when, carried from the actual sign-in
    assert claims["amr"] == ["password", "totp"] and claims["auth_time"] > 0
    assert claims["email"] == "alice@example.com" and claims["name"] == "Alice"
    assert claims["at_hash"]


def test_id_token_omits_claims_the_scope_did_not_ask_for(env):
    raw, verifier = _code(env, scope="openid")
    row = env["grants"].consume_code(raw, client_id="docweave-web",
                                     redirect_uri="http://docweave.localhost:8765/auth/callback",
                                     code_verifier=verifier)
    claims = jwt.decode(env["grants"].id_token(client=env["client"], user=env["user"],
                                               code_row=row, sid="s"),
                        JsonWebKey.import_key_set(env["keys"].jwks()))
    assert "email" not in claims and "name" not in claims


def test_a_token_signed_by_a_foreign_key_does_not_verify(env):
    """The check a relying party makes; asserted here so the fixture itself is trustworthy."""
    raw, verifier = _code(env)
    row = env["grants"].consume_code(raw, client_id="docweave-web",
                                     redirect_uri="http://docweave.localhost:8765/auth/callback",
                                     code_verifier=verifier)
    rogue = JsonWebKey.generate_key("RSA", 2048, is_private=True).as_dict(is_private=True)
    rogue["kid"] = json.loads(env["keys"].active()["public_jwk"])["kid"]      # same kid, wrong key
    forged = jwt.encode({"alg": "RS256", "kid": rogue["kid"]},
                        {"iss": oidc.issuer(), "sub": row["user_id"], "aud": "docweave-web"},
                        rogue).decode()
    with pytest.raises(Exception):
        jwt.decode(forged, JsonWebKey.import_key_set(env["keys"].jwks())).validate()


def test_issuer_comes_from_configuration_not_a_request(env, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_ISSUER", "https://id.example.org/")
    assert oidc.issuer() == "https://id.example.org"


# ---------------------------------------------------------------- tokens
def test_access_token_is_stored_as_a_hash_and_revocable(env):
    raw, verifier = _code(env)
    row = env["grants"].consume_code(raw, client_id="docweave-web",
                                     redirect_uri="http://docweave.localhost:8765/auth/callback",
                                     code_verifier=verifier)
    tokens = env["grants"].issue_tokens(client=env["client"], code_row=row, with_refresh=True)
    stored = env["store"].one("SELECT access_hash, refresh_hash, audience FROM oauth_tokens LIMIT 1")
    assert tokens["access_token"] not in json.dumps(dict(stored))
    assert stored["audience"] == "docweave-api"

    assert env["grants"].load_access(tokens["access_token"])
    assert env["grants"].revoke_token(tokens["access_token"]) is True
    assert env["grants"].load_access(tokens["access_token"]) is None


def test_revoking_a_central_session_kills_every_token_it_produced(env):
    """This is what makes platform-wide logout more than clearing one cookie."""
    for _ in range(3):
        raw, verifier = _code(env)
        row = env["grants"].consume_code(raw, client_id="docweave-web",
                                         redirect_uri="http://docweave.localhost:8765/auth/callback",
                                         code_verifier=verifier)
        env["grants"].issue_tokens(client=env["client"], code_row=row, with_refresh=True)
    assert env["grants"].revoke_for_central_session("cs_1", "platform_logout") == 3
    assert env["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE revoked_at IS NULL")["n"] == 0


def test_an_expired_access_token_stops_loading(env):
    raw, verifier = _code(env)
    row = env["grants"].consume_code(raw, client_id="docweave-web",
                                     redirect_uri="http://docweave.localhost:8765/auth/callback",
                                     code_verifier=verifier)
    tokens = env["grants"].issue_tokens(client=env["client"], code_row=row, with_refresh=False)
    env["store"].execute("UPDATE oauth_tokens SET access_expires_at=?", (now() - 1,))
    assert env["grants"].load_access(tokens["access_token"]) is None


# ---------------------------------------------------------------- discovery
def test_discovery_advertises_only_what_exists(env):
    d = oidc.discovery()
    assert d["issuer"] == "http://accounts.localhost:8770"
    assert d["response_types_supported"] == ["code"], "只实现授权码流程就只声明它"
    assert d["code_challenge_methods_supported"] == ["S256"]
    assert "implicit" not in d["grant_types_supported"]
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri",
                "revocation_endpoint", "end_session_endpoint"):
        assert d[key].startswith("http://accounts.localhost:8770/oidc/")
