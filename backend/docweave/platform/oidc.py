"""OIDC provider: keys, clients, codes and tokens.

Authlib owns the protocol; this module owns identity and storage. The split matters — writing
the OAuth state machine by hand is exactly the thing not to do, but *who the user is*, *how the
password was checked* and *whether MFA was satisfied* stay in the existing account code rather
than moving into a second user store.

Three things here are deliberate:

* **Signing keys are persisted.** A key generated at startup would invalidate every issued token
  on restart, which presents as a mass logout with no cause.
* **Codes and tokens are stored as hashes**, so a database read cannot be replayed as a grant.
* **`auth_time` and `amr` travel with the code**, so the id_token says *how and when* this person
  authenticated. A client that needs a phishing-resistant factor can check; "the account owns a
  passkey" is not the same claim and is never substituted for it.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time

from authlib.jose import JsonWebKey, jwt

from ..services import ServiceError
from ..store import Store, new_id, now

CODE_TTL_S = 60                      # short: a code is exchanged immediately or not at all
ACCESS_TTL_S = int(os.environ.get("DOCWEAVE_ACCESS_TTL_S", "900"))
REFRESH_TTL_S = int(os.environ.get("DOCWEAVE_REFRESH_TTL_S", str(14 * 24 * 3600)))
ID_TOKEN_TTL_S = 300
ALG = "RS256"

SUPPORTED_SCOPES = ("openid", "profile", "email", "offline_access")


def issuer() -> str:
    """The issuer URL. Configured, never derived from the request.

    Deriving it from Host would let anyone who can reach the service mint tokens claiming to
    come from the real issuer, and clients validate `iss` against exactly this string.
    """
    return (os.environ.get("DOCWEAVE_ISSUER") or "http://accounts.localhost:8770").rstrip("/")


def _sha(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------- keys
class Keys:
    def __init__(self, store: Store):
        self.store = store

    def active(self) -> dict:
        """The current signing key, creating one on first use and then reusing it forever."""
        row = self.store.one("SELECT * FROM oidc_keys WHERE status='active' ORDER BY created_at DESC LIMIT 1")
        if row:
            return row
        key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        kid = new_id("k")
        priv = key.as_dict(is_private=True)
        pub = key.as_dict(is_private=False)
        priv["kid"] = pub["kid"] = kid
        priv["alg"] = pub["alg"] = ALG
        pub["use"] = "sig"
        self.store.execute(
            "INSERT INTO oidc_keys (kid,alg,private_jwk,public_jwk,status,created_at) "
            "VALUES (?,?,?,?,'active',?)",
            (kid, ALG, json.dumps(priv), json.dumps(pub), now()))
        return self.store.one("SELECT * FROM oidc_keys WHERE kid=?", (kid,))

    def jwks(self) -> dict:
        """Public keys, including retiring ones.

        Ensures a key exists first. A relying party commonly fetches JWKS before anyone has
        signed anything — on first start, or after discovery — and an empty key set there makes
        every later verification fail for a reason that looks nothing like "no key yet".

        Retiring keys stay published so tokens signed just before a rotation still verify;
        dropping them immediately is what makes a rotation look like an outage.
        """
        self.active()
        rows = self.store.all("SELECT public_jwk FROM oidc_keys WHERE status IN ('active','retiring')")
        return {"keys": [json.loads(r["public_jwk"]) for r in rows]}

    def rotate(self) -> str:
        """Start signing with a new key; the previous one keeps verifying until retired."""
        with self.store.tx() as t:
            t.execute("UPDATE oidc_keys SET status='retiring' WHERE status='active'")
        return self.active()["kid"]

    def retire(self, kid: str) -> None:
        self.store.execute("UPDATE oidc_keys SET status='retired', retired_at=? WHERE kid=?", (now(), kid))


# ---------------------------------------------------------------------- clients
class Clients:
    def __init__(self, store: Store):
        self.store = store

    def get(self, client_id: str) -> dict | None:
        r = self.store.one("SELECT * FROM oauth_clients WHERE client_id=? AND disabled_at IS NULL",
                           (client_id,))
        if r:
            r["redirect_uris"] = json.loads(r["redirect_uris"])
            r["post_logout_redirect_uris"] = json.loads(r["post_logout_redirect_uris"] or "[]")
            r["audiences"] = json.loads(r["audiences"] or "[]")
        return r

    def register(self, *, client_id: str, app_id: str, name: str, redirect_uris: list[str],
                 scopes: str = "openid profile email", audiences: list[str] | None = None,
                 confidential: bool = False, post_logout_redirect_uris: list[str] | None = None,
                 backchannel_logout_uri: str | None = None) -> tuple[dict, str | None]:
        """Register a client. Returns (row, secret) — the secret is shown once and hashed here.

        Registration is a CLI/configuration action by design. Dynamic client registration is not
        exposed: anything that lets an unauthenticated caller create a client with its own
        redirect URI is a way to have tokens delivered somewhere else.
        """
        for uri in redirect_uris:
            _check_redirect(uri)
        secret = secrets.token_urlsafe(40) if confidential else None
        ts = now()
        self.store.execute(
            "INSERT INTO oauth_clients (client_id,app_id,name,secret_hash,redirect_uris,"
            "post_logout_redirect_uris,backchannel_logout_uri,scopes,audiences,"
            "token_endpoint_auth_method,require_pkce,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?) "
            "ON CONFLICT(client_id) DO UPDATE SET app_id=excluded.app_id, name=excluded.name, "
            "redirect_uris=excluded.redirect_uris, post_logout_redirect_uris=excluded.post_logout_redirect_uris, "
            "backchannel_logout_uri=excluded.backchannel_logout_uri, scopes=excluded.scopes, "
            "audiences=excluded.audiences, updated_at=excluded.updated_at",
            (client_id, app_id, name, _sha(secret) if secret else None,
             json.dumps(redirect_uris), json.dumps(post_logout_redirect_uris or []),
             backchannel_logout_uri, scopes, json.dumps(audiences or []),
             "client_secret_post" if confidential else "none", ts, ts))
        return self.get(client_id), secret

    def check_secret(self, client_id: str, secret: str) -> bool:
        r = self.store.one("SELECT secret_hash FROM oauth_clients WHERE client_id=? AND disabled_at IS NULL",
                           (client_id,))
        if not r or not r["secret_hash"]:
            return False
        return secrets.compare_digest(r["secret_hash"], _sha(secret))

    @staticmethod
    def redirect_allowed(client: dict, uri: str) -> bool:
        """Exact string match against the registered list.

        No wildcards, no prefix matching, no "same origin is close enough". Loose redirect
        matching is the classic way authorization codes end up at an attacker's URL.
        """
        return uri in (client.get("redirect_uris") or [])


def _check_redirect(uri: str) -> None:
    from urllib.parse import urlparse
    u = urlparse(uri)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ServiceError(400, "bad_redirect", f"回调地址必须是 http/https 绝对地址：{uri!r}")
    if u.fragment:
        raise ServiceError(400, "bad_redirect", "回调地址不能带片段（#…）")
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1") \
            and not (u.hostname or "").endswith(".localhost"):
        raise ServiceError(400, "bad_redirect", "非本机地址必须使用 https")


# ---------------------------------------------------------------------- grants
class Grants:
    """Authorization codes and tokens."""

    def __init__(self, store: Store):
        self.store = store
        self.keys = Keys(store)

    # -------------------------------------------------------------- codes
    def issue_code(self, *, client_id: str, user_id: str, redirect_uri: str, scope: str,
                   nonce: str | None, code_challenge: str | None, code_challenge_method: str | None,
                   auth_time: float, amr: str, central_session_id: str | None,
                   workspace_id: str | None) -> str:
        raw = secrets.token_urlsafe(32)
        self.store.execute(
            "INSERT INTO oauth_codes (code_hash,client_id,user_id,redirect_uri,scope,nonce,"
            "code_challenge,code_challenge_method,auth_time,amr,central_session_id,workspace_id,"
            "created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (_sha(raw), client_id, user_id, redirect_uri, scope, nonce, code_challenge,
             code_challenge_method, auth_time, amr, central_session_id, workspace_id,
             now(), now() + CODE_TTL_S))
        return raw

    def consume_code(self, raw: str, *, client_id: str, redirect_uri: str,
                     code_verifier: str | None) -> dict:
        """Spend a code, or refuse. Every check here has a reason.

        The conditional UPDATE makes the spend single-use. The client and redirect_uri must match
        the ones the code was issued for, or a code obtained by one client could be redeemed by
        another. PKCE is verified against the stored challenge.
        """
        digest = _sha(raw)
        row = self.store.one("SELECT * FROM oauth_codes WHERE code_hash=?", (digest,))
        if not row:
            raise ServiceError(400, "invalid_grant", "授权码无效")
        if row["used_at"] or row["expires_at"] <= now():
            # A replayed code is treated as a compromise signal: everything issued from it goes.
            if row["used_at"]:
                self.revoke_tokens_from_code(digest, "code_replayed")
            raise ServiceError(400, "invalid_grant", "授权码已使用或已过期")
        if row["client_id"] != client_id:
            raise ServiceError(400, "invalid_grant", "授权码不属于该客户端")
        if row["redirect_uri"] != redirect_uri:
            raise ServiceError(400, "invalid_grant", "回调地址与授权时不一致")

        if row["code_challenge"]:
            if not code_verifier:
                raise ServiceError(400, "invalid_grant", "缺少 code_verifier")
            method = (row["code_challenge_method"] or "plain").upper()
            if method == "S256":
                import base64
                calc = base64.urlsafe_b64encode(
                    hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode()
            else:
                # Registered clients require S256; plain exists only so a non-conforming client
                # fails loudly here rather than silently skipping PKCE.
                raise ServiceError(400, "invalid_grant", "只接受 S256 的 PKCE")
            if not secrets.compare_digest(calc, row["code_challenge"]):
                raise ServiceError(400, "invalid_grant", "PKCE 校验失败")

        with self.store.tx() as t:
            n = t.execute("UPDATE oauth_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
                          (now(), digest)).rowcount
            if not n:
                raise ServiceError(400, "invalid_grant", "授权码已使用")
        return row

    def revoke_tokens_from_code(self, code_hash: str, reason: str) -> int:
        row = self.store.one("SELECT central_session_id, user_id, client_id FROM oauth_codes WHERE code_hash=?",
                             (code_hash,))
        if not row:
            return 0
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE user_id=? AND client_id=? AND revoked_at IS NULL",
            (now(), reason, row["user_id"], row["client_id"]))

    # -------------------------------------------------------------- tokens
    def issue_tokens(self, *, client: dict, code_row: dict, with_refresh: bool,
                     family_id: str | None = None, parent_id: str | None = None) -> dict:
        access = secrets.token_urlsafe(40)
        refresh = secrets.token_urlsafe(40) if with_refresh else None
        tid = new_id("tok")
        audience = (client.get("audiences") or [None])[0]
        self.store.execute(
            "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,audience,"
            "central_session_id,workspace_id,issued_at,access_expires_at,refresh_expires_at,"
            "family_id,parent_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, client["client_id"], code_row["user_id"], _sha(access),
             _sha(refresh) if refresh else None, code_row["scope"], audience,
             code_row["central_session_id"], code_row["workspace_id"], now(),
             now() + ACCESS_TTL_S, (now() + REFRESH_TTL_S) if refresh else None,
             family_id or tid, parent_id))
        out = {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL_S,
               "scope": code_row["scope"]}
        if refresh:
            out["refresh_token"] = refresh
        return out

    def rotate_refresh(self, raw: str, *, client: dict) -> tuple[dict, dict]:
        """Spend a refresh token and issue its successor. Returns (token response, old row).

        Rotation with replay detection, which is the part that makes a long-lived refresh token
        defensible at all. Each refresh token is single use; presenting a *spent* one means the
        token leaked — the legitimate holder already has the successor — so the entire family
        descended from the original grant is revoked rather than just the one presented.
        """
        digest = _sha(raw)
        row = self.store.one("SELECT * FROM oauth_tokens WHERE refresh_hash=?", (digest,))
        if not row:
            raise ServiceError(400, "invalid_grant", "刷新令牌无效")
        if row["client_id"] != client["client_id"]:
            raise ServiceError(400, "invalid_grant", "刷新令牌不属于该客户端")
        if row.get("revoked_at"):
            n = self.revoke_family(row["family_id"] or row["id"], "refresh_replayed")
            raise ServiceError(400, "invalid_grant",
                               f"刷新令牌已被使用过，该授权的全部令牌已撤销（{n} 个）")
        if (row["refresh_expires_at"] or 0) <= now():
            raise ServiceError(400, "invalid_grant", "刷新令牌已过期")

        with self.store.tx() as t:
            spent = t.execute(
                "UPDATE oauth_tokens SET revoked_at=?, revoked_reason='rotated' "
                "WHERE id=? AND revoked_at IS NULL", (now(), row["id"])).rowcount
            if not spent:
                # Lost the race against a concurrent refresh; the winner's successor stands and
                # this caller must not get a second one.
                raise ServiceError(400, "invalid_grant", "刷新令牌已被使用")

        out = self.issue_tokens(
            client=client,
            code_row={"user_id": row["user_id"], "scope": row["scope"],
                      "central_session_id": row["central_session_id"],
                      "workspace_id": row["workspace_id"]},
            with_refresh=True, family_id=row["family_id"] or row["id"], parent_id=row["id"])
        return out, row

    def revoke_family(self, family_id: str, reason: str) -> int:
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE family_id=? AND revoked_at IS NULL", (now(), reason, family_id))

    def load_access(self, raw: str) -> dict | None:
        r = self.store.one("SELECT * FROM oauth_tokens WHERE access_hash=?", (_sha(raw),))
        if not r or r.get("revoked_at") or (r["access_expires_at"] or 0) <= now():
            return None
        return r

    def revoke_token(self, raw: str, reason: str = "client_revoked") -> bool:
        d = _sha(raw)
        n = self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE (access_hash=? OR refresh_hash=?) AND revoked_at IS NULL", (now(), reason, d, d))
        return bool(n)

    def revoke_for_central_session(self, central_session_id: str, reason: str) -> int:
        """Used by platform-wide logout: every token that came from this sign-in."""
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE central_session_id=? AND revoked_at IS NULL", (now(), reason, central_session_id))

    def revoke_for_user(self, user_id: str, reason: str) -> int:
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? WHERE user_id=? AND revoked_at IS NULL",
            (now(), reason, user_id))

    # -------------------------------------------------------------- id_token
    def id_token(self, *, client: dict, user: dict, code_row: dict, sid: str,
                 access_token: str | None = None) -> str:
        key = self.keys.active()
        priv = json.loads(key["private_jwk"])
        iat = int(time.time())
        claims = {
            "iss": issuer(), "sub": user["id"], "aud": client["client_id"],
            "iat": iat, "exp": iat + ID_TOKEN_TTL_S,
            # auth_time and amr are what let a client decide whether this sign-in is strong
            # enough for what it is about to do, instead of inferring from what the account owns.
            "auth_time": int(code_row["auth_time"]), "amr": (code_row["amr"] or "").split("+") or [],
            "sid": sid,
        }
        if code_row.get("nonce"):
            claims["nonce"] = code_row["nonce"]
        scope = (code_row["scope"] or "").split()
        if "email" in scope:
            claims["email"] = user["email"]
            claims["email_verified"] = bool(user.get("email_verified_at"))
        if "profile" in scope:
            claims["name"] = user.get("display_name") or user["email"].split("@")[0]
        if access_token:
            import base64
            digest = hashlib.sha256(access_token.encode("ascii")).digest()
            claims["at_hash"] = base64.urlsafe_b64encode(digest[:16]).rstrip(b"=").decode()
        return jwt.encode({"alg": ALG, "kid": key["kid"]}, claims, priv).decode("ascii")


    # -------------------------------------------------------------- logout token
    def logout_token(self, *, client: dict, user_id: str, sid: str) -> str:
        """A back-channel logout token (OIDC Back-Channel Logout 1.0, §2.4).

        Carries `sid` so the client logs out exactly the session that came from this sign-in, and
        a `jti` so a client can reject duplicates from our retries. It deliberately has **no
        nonce** — a logout token that could pass as an id_token would be an authentication
        bypass, and §2.4 makes the absence a validation rule for that reason.
        """
        key = self.keys.active()
        iat = int(time.time())
        claims = {
            "iss": issuer(), "sub": user_id, "aud": client["client_id"],
            "iat": iat, "exp": iat + 120, "jti": new_id("lt"), "sid": sid,
            "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
        }
        return jwt.encode({"alg": ALG, "kid": key["kid"], "typ": "logout+jwt"},
                          claims, json.loads(key["private_jwk"])).decode("ascii")


def deliver_logout(url: str, token: str, timeout: float = 3.0) -> tuple[bool, str | None]:
    """POST a logout token to a client's back-channel endpoint.

    Best effort on purpose. The notification is not the enforcement: the tokens are already
    revoked in our own database before this is called, so a client that never receives it still
    fails on its next token check. Treating delivery as the mechanism is how "signed out
    everywhere" quietly becomes "signed out on whichever apps happened to answer".

    The URL is not attacker-supplied — it comes from a client registration made by CLI — but it
    is still validated, because a registration mistake should not turn this into a way to make
    the server issue requests to arbitrary internal addresses.
    """
    try:
        _check_redirect(url)
    except ServiceError as e:
        return False, e.message
    import httpx
    try:
        r = httpx.post(url, data={"logout_token": token}, timeout=timeout,
                       follow_redirects=False,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}"
    except Exception as e:                                   # network, DNS, TLS, timeout
        return False, f"{type(e).__name__}: {e}"[:200]


def verify_logout_token(raw: str, *, jwks: dict, client_id: str, expected_issuer: str | None = None) -> dict:
    """Client side: validate a logout token before acting on it.

    Used by DocWeave as a relying party. Every check here is one that, if skipped, lets someone
    log other people out at will: signature against the issuer's published keys, issuer and
    audience, freshness, the logout event claim, and the absence of `nonce`.
    """
    iss = expected_issuer or issuer()
    claims = jwt.decode(raw, JsonWebKey.import_key_set(jwks))
    if claims.get("iss") != iss:
        raise ServiceError(400, "bad_logout_token", "发行者不匹配")
    aud = claims.get("aud")
    if client_id not in ([aud] if isinstance(aud, str) else (aud or [])):
        raise ServiceError(400, "bad_logout_token", "受众不匹配")
    if "nonce" in claims:
        raise ServiceError(400, "bad_logout_token", "登出令牌不得带 nonce")
    events = claims.get("events") or {}
    if "http://schemas.openid.net/event/backchannel-logout" not in events:
        raise ServiceError(400, "bad_logout_token", "缺少后通道登出事件声明")
    if not claims.get("sid") and not claims.get("sub"):
        raise ServiceError(400, "bad_logout_token", "登出令牌必须指明 sid 或 sub")
    if abs(int(time.time()) - int(claims.get("iat") or 0)) > 300:
        raise ServiceError(400, "bad_logout_token", "登出令牌不在有效时间窗内")
    return dict(claims)


def discovery() -> dict:
    """Only the endpoints that actually exist. Advertising more would be a lie a client acts on."""
    iss = issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oidc/authorize",
        "token_endpoint": f"{iss}/oidc/token",
        "userinfo_endpoint": f"{iss}/oidc/userinfo",
        "jwks_uri": f"{iss}/oidc/jwks.json",
        "revocation_endpoint": f"{iss}/oidc/revoke",
        "end_session_endpoint": f"{iss}/oidc/logout",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": [ALG],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "amr", "sid",
                             "email", "email_verified", "name", "nonce", "at_hash"],
        "backchannel_logout_supported": True,
        "backchannel_logout_session_supported": True,
    }
