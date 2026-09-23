"""DocWeave as a relying party on the platform account service.

This is the client side of the chain the provider in `platform/` serves. It is deliberately a
**back-end for front-end**: the code is exchanged server to server, the tokens never reach the
browser, and what the browser gets is an ordinary DocWeave session cookie — host-only on
DocWeave's own origin, carrying no platform credential at all.

That shape is the reason three otherwise tempting designs are not used here:

* No shared wide-domain cookie. The central session lives on the account service's host and is
  never copied; DocWeave's session is its own row, and "signed in over there" only becomes
  "signed in here" by completing the protocol.
* No access token in `localStorage`. A token the page can read is a token an injected script can
  read, and the session cookie is `HttpOnly` precisely so that is not possible.
* No id_token as an API credential. The id_token says who signed in, to this client, at this
  moment; it is verified once, here, and then discarded.

The local session is linked back to the grant by `oidc_sid`, which is what lets a back-channel
logout from the account service end exactly the right sessions and nothing else.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse

from platform_core.errors import ServiceError
from platform_core.store import Store, new_id, now

REQUEST_TTL_S = 600          # a sign-in someone walked away from should not stay startable
JWKS_TTL_S = 300


def issuer() -> str:
    return (os.environ.get("DOCWEAVE_ISSUER") or "http://accounts.localhost:8770").rstrip("/")


def client_id() -> str:
    return os.environ.get("DOCWEAVE_OIDC_CLIENT_ID") or "docweave-web"


def app_id() -> str:
    """Which application *this* service is, for the eligibility check after a sign-in.

    Configured rather than read out of the id_token: the token says who the person is, and a
    service that let a token tell it which application it was would be checking the wrong
    entitlement. The portal runs the same code with `DOCWEAVE_OIDC_APP_ID=platform`.
    """
    return os.environ.get("DOCWEAVE_OIDC_APP_ID") or "docweave"


def public_url() -> str:
    """DocWeave's own externally reachable base URL. Configured, not derived from the request:
    it has to match the redirect URI registered with the provider exactly."""
    return (os.environ.get("DOCWEAVE_APP_URL") or "http://docweave.localhost:8765").rstrip("/")


def redirect_uri() -> str:
    return public_url() + "/auth/callback"


def safe_next(raw: str | None) -> str:
    """Reduce a requested landing page to something that cannot leave this site.

    Only a path on this origin survives. `//evil.example.com` is a protocol-relative URL that
    browsers treat as absolute, and a backslash is normalised to a slash by several of them, so
    both are rejected rather than patched up.
    """
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\") or "\\" in raw[:2]:
        return "/"
    if "://" in raw:
        return "/"
    return raw


class Rp:
    """The relying-party half. `http` is injected so tests can run it against the real account
    service app over ASGI rather than a socket, and so a deployment can pin its own client."""

    def __init__(self, store: Store, http=None):
        self.store = store
        self._http = http
        self._jwks: tuple[float, dict] | None = None

    # ------------------------------------------------------------------ transport
    @property
    def http(self):
        """The connection to the identity provider.

        `trust_env=False` is the load-bearing part. httpx otherwise honours whatever
        `HTTP_PROXY` happens to be in the environment, and the requests made on this client
        carry an authorization code and, for a confidential client, the client secret. Sending
        those through an ambient proxy that nobody chose is not a thing to do by accident — and
        it is easy to do by accident, because a `NO_PROXY` of `localhost,127.0.0.1` does *not*
        match `accounts.localhost`. That is exactly how this was found: every browser hop
        worked, and the server-to-server token call was quietly routed into a proxy that dropped
        it, surfacing as "Server disconnected without sending a response" at the callback.

        A deployment that genuinely needs a proxy to reach its issuer says so, by name.
        """
        if self._http is None:
            import httpx
            self._http = httpx.Client(base_url=issuer(), timeout=10.0, follow_redirects=False,
                                      trust_env=False,
                                      proxy=os.environ.get("DOCWEAVE_OIDC_PROXY") or None)
        return self._http

    def _jwks_now(self, *, force: bool = False) -> dict:
        if not force and self._jwks and self._jwks[0] > time.time():
            return self._jwks[1]
        r = self.http.get("/oidc/jwks.json")
        if r.status_code != 200:
            # Deliberately an error, not a fallback. "Cannot reach the identity service" must
            # never resolve to "let them in"; it resolves to "sign-in is unavailable".
            raise ServiceError(503, "idp_unreachable", "账户服务暂时不可用，请稍后再试")
        keys = r.json()
        self._jwks = (time.time() + JWKS_TTL_S, keys)
        return keys

    # ------------------------------------------------------------------ start
    def begin(self, *, next_path: str | None, prompt: str | None = None) -> tuple[str, str]:
        """Return (authorize URL, request id). The request id goes in a short-lived cookie.

        `state`, `nonce` and the PKCE verifier are generated here and stored server-side. The
        browser carries only an opaque id, so none of the three can be chosen by whoever sent
        the person to this link.
        """
        rid = new_id("ar")
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        self.store.execute(
            "INSERT INTO rp_auth_requests (id,state,nonce,code_verifier,next_path,created_at,expires_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, state, nonce, verifier, safe_next(next_path), now(), now() + REQUEST_TTL_S))
        q = {"client_id": client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
             "scope": "openid profile email", "state": state, "nonce": nonce,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        if prompt:
            q["prompt"] = prompt
        return f"{issuer()}/oidc/authorize?" + urllib.parse.urlencode(q), rid

    # ------------------------------------------------------------------ callback
    def complete(self, *, request_id: str | None, state: str | None, code: str | None) -> dict:
        """Finish a sign-in. Returns {user_id, sid, amr, auth_time, next_path, claims}.

        The pending request is consumed by a conditional UPDATE, so a callback cannot be
        replayed — the second attempt finds nothing to consume.
        """
        if not request_id or not state or not code:
            raise ServiceError(400, "bad_callback", "登录回调参数不完整，请重新登录")
        row = self.store.one("SELECT * FROM rp_auth_requests WHERE id=?", (request_id,))
        if not row or row["used_at"] or row["expires_at"] <= now():
            raise ServiceError(400, "bad_callback", "登录请求已过期或已使用，请重新登录")
        if not secrets.compare_digest(row["state"], state):
            # The cookie and the query string disagree: this callback was not started here.
            raise ServiceError(400, "bad_state", "登录状态校验失败，请重新登录")
        with self.store.tx() as t:
            if not t.execute("UPDATE rp_auth_requests SET used_at=? WHERE id=? AND used_at IS NULL",
                             (now(), request_id)).rowcount:
                raise ServiceError(400, "bad_callback", "登录请求已被使用")

        r = self.http.post("/oidc/token", data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri(),
            "client_id": client_id(), "code_verifier": row["code_verifier"]})
        if r.status_code != 200:
            detail = ""
            try:
                detail = r.json().get("error_description") or r.json().get("error") or ""
            except Exception:
                pass
            raise ServiceError(400, "token_exchange_failed", f"换取令牌失败：{detail or r.status_code}")
        body = r.json()
        claims = self.verify_id_token(body.get("id_token") or "", nonce=row["nonce"])
        return {"user_id": claims["sub"], "sid": claims.get("sid"),
                "amr": "+".join(claims.get("amr") or []) or "password",
                "auth_time": claims.get("auth_time"), "next_path": row["next_path"] or "/",
                "claims": claims, "access_token": body.get("access_token")}

    def verify_id_token(self, raw: str, *, nonce: str | None) -> dict:
        """Every check here is one that, if skipped, accepts a token someone else made."""
        from authlib.jose import JsonWebKey, jwt
        if not raw:
            raise ServiceError(400, "no_id_token", "账户服务未返回身份令牌")
        try:
            claims = jwt.decode(raw, JsonWebKey.import_key_set(self._jwks_now()))
        except Exception:
            # An unknown kid is the normal shape of a key rotation, so refetch once before
            # concluding the signature is bad.
            claims = jwt.decode(raw, JsonWebKey.import_key_set(self._jwks_now(force=True)))
        if claims.get("iss") != issuer():
            raise ServiceError(400, "bad_id_token", "身份令牌的发行者不匹配")
        aud = claims.get("aud")
        if client_id() not in ([aud] if isinstance(aud, str) else (aud or [])):
            raise ServiceError(400, "bad_id_token", "身份令牌不是发给本应用的")
        if nonce is not None and claims.get("nonce") != nonce:
            raise ServiceError(400, "bad_id_token", "身份令牌的 nonce 不匹配")
        ts = int(time.time())
        if int(claims.get("exp") or 0) <= ts:
            raise ServiceError(400, "bad_id_token", "身份令牌已过期")
        if int(claims.get("iat") or 0) > ts + 120:
            raise ServiceError(400, "bad_id_token", "身份令牌的签发时间不合理")
        if not claims.get("sub"):
            raise ServiceError(400, "bad_id_token", "身份令牌缺少 sub")
        return dict(claims)

    # ------------------------------------------------------------------ back-channel logout
    def accept_logout(self, raw_token: str) -> dict:
        """Handle a back-channel logout token: verify it, then end the sessions it names.

        Duplicates are expected — the provider retries — so a repeat is a success with nothing
        left to do, not an error that would make the provider retry forever.
        """
        from platform_core import oidc as _oidc         # verification helper, not the provider itself
        claims = _oidc.verify_logout_token(raw_token, jwks=self._jwks_now(),
                                           client_id=client_id(), expected_issuer=issuer())
        jti = claims["jti"]
        if self.store.one("SELECT 1 FROM rp_logout_seen WHERE jti=?", (jti,)):
            return {"ended": 0, "duplicate": True}
        self.store.execute("INSERT INTO rp_logout_seen (jti,at) VALUES (?,?)", (jti, now()))
        sid = claims.get("sid")
        if sid:
            n = self.store.execute(
                "UPDATE sessions SET revoked_at=?, revoked_reason='backchannel_logout' "
                "WHERE oidc_sid=? AND revoked_at IS NULL", (now(), sid))
        else:
            # No sid: the token is about the person, not one sign-in. Ending everything is the
            # conservative reading and the one the spec allows.
            n = self.store.execute(
                "UPDATE sessions SET revoked_at=?, revoked_reason='backchannel_logout' "
                "WHERE user_id=? AND oidc_sid IS NOT NULL AND revoked_at IS NULL",
                (now(), claims["sub"]))
        return {"ended": n, "duplicate": False}

    def sweep(self) -> int:
        """Drop spent and expired sign-in attempts, and logout ids past any retry window."""
        n = self.store.execute("DELETE FROM rp_auth_requests WHERE expires_at < ?", (now() - 3600,))
        n += self.store.execute("DELETE FROM rp_logout_seen WHERE at < ?", (now() - 24 * 3600,))
        return n
