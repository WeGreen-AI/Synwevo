"""Credentials: a `dw_` API key, a service account's client credentials, or a person's delegation.

All end up as ``Authorization: Bearer <token>``; the difference is where the token comes from.

* `ApiKey` is the operator-issued key (``python -m synwevo_docs.cli apikey create``). It is sent
  as is.
* `ClientCredentials` is a platform service account (registered with the account service's CLI).
  It exchanges ``client_id`` + ``client_secret`` for a short-lived access token at the token
  endpoint (OAuth 2.0 client_credentials, ``client_secret_post`` — the only confidential method
  the endpoint supports), caches it, and fetches a new one shortly before it expires or when the
  API answers 401 (a token can be revoked before it expires).

* `UserToken` is a person's delegation to a client they authorised (authorization code + PKCE at
  the account service). It carries the access token and, when the person granted
  ``offline_access``, the refresh token that replaces it. Refresh tokens are single use: each
  refresh hands back a successor, which `UserToken` passes to ``on_rotate`` so the caller can
  store it before anything else happens.

Secrets are read from the environment or from files, never required on a command line (where they
would land in shell history and the process list), and never appear in ``repr`` or in errors.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Mapping

import httpx

from .errors import AuthenticationError, SynwevoError, TransportError, error_from_response

__all__ = ["ApiKey", "ClientCredentials", "Credentials", "UserToken", "credentials_from_env", "read_secret"]

DEFAULT_SCOPE = "docweave.read docweave.write"


def read_secret(value: str | None = None, file: str | os.PathLike | None = None, *, what: str = "密钥") -> str:
    """A secret given directly, or read from a file (first line, surrounding whitespace removed)."""
    if value:
        return value.strip()
    if file:
        try:
            text = Path(file).expanduser().read_text(encoding="utf-8")
        except (OSError, UnicodeError) as e:
            raise SynwevoError(None, "credential_file", f"无法读取{what}文件 {file}：{type(e).__name__}") from None
        line = text.strip().splitlines()[0].strip() if text.strip() else ""
        if not line:
            raise SynwevoError(None, "credential_file", f"{what}文件 {file} 是空的")
        return line
    raise SynwevoError(None, "missing_credentials", f"没有提供{what}")


class ApiKey:
    """An operator-issued ``dw_<id>_<secret>`` key."""

    def __init__(self, key: str):
        key = (key or "").strip()
        if not key:
            raise SynwevoError(None, "missing_credentials", "API 密钥为空")
        self._key = key

    @property
    def key_id(self) -> str | None:
        parts = self._key.split("_", 2)
        return parts[1] if len(parts) == 3 and parts[0] == "dw" else None

    def authorization(self, http: httpx.Client) -> str:
        return f"Bearer {self._key}"

    def invalidate(self) -> bool:
        """Nothing to refresh: a rejected key stays rejected."""
        return False

    def __repr__(self) -> str:
        return f"ApiKey(key_id={self.key_id!r})"


class ClientCredentials:
    """A service account's ``client_id`` / ``client_secret`` exchanged for access tokens."""

    def __init__(self, token_url: str, client_id: str, client_secret: str, scope: str = DEFAULT_SCOPE,
                 resource: str | None = None, *, refresh_margin: float = 60.0, max_retries: int = 3,
                 clock=time.time, sleep=time.sleep):
        if not token_url or not client_id or not client_secret:
            raise SynwevoError(None, "missing_credentials", "客户端凭据需要 token_url、client_id 与 client_secret")
        self.token_url = token_url
        self.client_id = client_id
        self._secret = client_secret
        self.scope = scope
        self.resource = resource
        self.refresh_margin = refresh_margin
        self.max_retries = max_retries
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._expires_at = 0.0
        self._refresh_at = 0.0
        self.granted_scope: str | None = None
        self._lock = threading.Lock()

    def authorization(self, http: httpx.Client) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._refresh_at:
                self._fetch(http)
            return f"Bearer {self._token}"

    def invalidate(self) -> bool:
        with self._lock:
            had = self._token is not None
            self._token, self._expires_at, self._refresh_at = None, 0.0, 0.0
            return had

    @property
    def expires_at(self) -> float | None:
        return self._expires_at if self._token else None

    def _fetch(self, http: httpx.Client) -> None:
        form = {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self._secret}
        if self.scope:
            form["scope"] = self.scope
        if self.resource:
            form["resource"] = self.resource
        # A token request has no side effect beyond issuing a token, so it is retried on network
        # failures and 5xx like a GET.
        attempt = 0
        while True:
            try:
                resp = http.post(self.token_url, data=form, headers={"Accept": "application/json"}, follow_redirects=False)
            except httpx.TransportError as e:
                if attempt < self.max_retries:
                    self._sleep(min(8.0, 0.5 * 2 ** attempt))
                    attempt += 1
                    continue
                raise TransportError(None, "token_endpoint_unreachable",
                                     f"无法连接令牌端点 {self.token_url}：{type(e).__name__}") from None
            if resp.status_code >= 500 and attempt < self.max_retries:
                self._sleep(min(8.0, 0.5 * 2 ** attempt))
                attempt += 1
                continue
            break
        if resp.status_code != 200:
            err = error_from_response(resp.status_code, resp.headers, resp.content, method="POST", url=self.token_url)
            if resp.status_code in (400, 401):
                # OAuth errors (invalid_client, invalid_scope, unauthorized_client) all mean the
                # credential cannot be used as configured; one exception type for all of them.
                raise AuthenticationError(resp.status_code, err.code, f"令牌端点拒绝了客户端凭据：{err.message}",
                                          err.request_id, err.field, err.details, method="POST", url=self.token_url)
            raise err
        try:
            body = resp.json()
            token = body["access_token"]
            if not isinstance(token, str) or not token.strip():
                raise ValueError("empty access token")
        except (ValueError, KeyError, TypeError):
            raise AuthenticationError(resp.status_code, "invalid_token_response", "令牌端点的响应里没有 access_token",
                                      method="POST", url=self.token_url) from None
        if str(body.get("token_type", "Bearer")).lower() != "bearer":
            raise AuthenticationError(resp.status_code, "invalid_token_response",
                                      f"不支持的令牌类型：{body.get('token_type')}")
        try:
            lifetime = float(body.get("expires_in") or 300)
        except (TypeError, ValueError):
            lifetime = 300.0
        lifetime = max(1.0, lifetime)
        t = self._clock()
        self._token = token
        self._expires_at = t + lifetime
        # Refresh ahead of expiry, but never so far ahead that a short-lived token is refetched
        # on every call: at most half its lifetime early.
        self._refresh_at = t + lifetime - min(self.refresh_margin, lifetime / 2)
        self.granted_scope = body.get("scope")

    def __repr__(self) -> str:
        return f"ClientCredentials(token_url={self.token_url!r}, client_id={self.client_id!r}, scope={self.scope!r})"


class UserToken:
    """A person's delegation to this client: an access token, refreshed with a rotating refresh token.

    Give the access token, the refresh token, or both. With a refresh token the client's
    ``client_id`` and the token endpoint are needed too (and ``client_secret`` for a confidential
    client). ``on_rotate`` receives ``{"access_token", "refresh_token", "expires_at", "scope"}``
    after every refresh: persist it — the previous refresh token is spent, and presenting a spent
    one revokes the person's whole grant.

    A refresh whose answer never arrived is not known to have failed. The next call tries the same
    refresh token again; if the server had already spent it the grant ends and the person has to
    authorise the client again, which is the only safe outcome of a lost successor.

    **One holder per refresh token.** Two processes (or two `UserToken` objects) started from the
    same refresh token will both try to spend it; the second presents a spent token, the platform
    treats that as a leak and revokes the grant for both. Let one process refresh and hand the
    others the current access token, or keep the token behind a store with a lock.
    """

    def __init__(self, access_token: str | None = None, *, refresh_token: str | None = None,
                 token_url: str | None = None, client_id: str | None = None, client_secret: str | None = None,
                 resource: str | None = None, expires_at: float | None = None,
                 on_rotate: Callable[[dict], None] | None = None, refresh_margin: float = 60.0,
                 clock=time.time):
        access_token = (access_token or "").strip() or None
        refresh_token = (refresh_token or "").strip() or None
        if not access_token and not refresh_token:
            raise SynwevoError(None, "missing_credentials", "用户授权凭据需要 access_token 或 refresh_token")
        if refresh_token and not (token_url and client_id):
            raise SynwevoError(None, "missing_credentials", "使用 refresh_token 时还需要 token_url 与 client_id")
        self.token_url = token_url
        self.client_id = client_id
        self._secret = client_secret
        self.resource = resource
        self.refresh_margin = refresh_margin
        self.on_rotate = on_rotate
        self._clock = clock
        self._access = access_token
        self._refresh = refresh_token
        self._expires_at = float(expires_at) if expires_at else None
        self.granted_scope: str | None = None
        self._lock = threading.Lock()

    @property
    def expires_at(self) -> float | None:
        return self._expires_at if self._access else None

    def authorization(self, http: httpx.Client) -> str:
        with self._lock:
            stale = self._access is None or (
                self._expires_at is not None and self._clock() >= self._expires_at - self.refresh_margin)
            if stale and self._refresh:
                self._rotate(http)
            if self._access is None:
                raise AuthenticationError(401, "token_expired", "访问令牌已失效，且没有可用的刷新令牌；请让本人重新授权")
            return f"Bearer {self._access}"

    def invalidate(self) -> bool:
        """The API refused the access token. With a refresh token there is one more thing to try."""
        with self._lock:
            if not self._refresh or self._access is None:
                return False
            self._access = None
            return True

    def _rotate(self, http: httpx.Client) -> None:
        form = {"grant_type": "refresh_token", "refresh_token": self._refresh, "client_id": self.client_id}
        if self._secret:
            form["client_secret"] = self._secret
        if self.resource:
            form["resource"] = self.resource
        try:
            resp = http.post(self.token_url, data=form, headers={"Accept": "application/json"}, follow_redirects=False)
        except httpx.TransportError as e:
            raise TransportError(None, "token_endpoint_unreachable",
                                 f"无法连接令牌端点 {self.token_url}：{type(e).__name__}") from None
        if resp.status_code != 200:
            err = error_from_response(resp.status_code, resp.headers, resp.content, method="POST", url=self.token_url)
            if resp.status_code in (400, 401):
                # invalid_grant: spent, expired or withdrawn. Nothing this process holds can fix it.
                self._refresh = None
                raise AuthenticationError(resp.status_code, err.code, f"令牌端点拒绝了刷新令牌，请让本人重新授权：{err.message}",
                                          err.request_id, err.field, err.details, method="POST", url=self.token_url)
            raise err
        try:
            body = resp.json()
            access = body["access_token"]
            if not isinstance(access, str) or not access.strip():
                raise ValueError("empty access token")
        except (ValueError, KeyError, TypeError):
            raise AuthenticationError(resp.status_code, "invalid_token_response", "令牌端点的响应里没有 access_token",
                                      method="POST", url=self.token_url) from None
        if str(body.get("token_type", "Bearer")).lower() != "bearer":
            raise AuthenticationError(resp.status_code, "invalid_token_response",
                                      f"不支持的令牌类型：{body.get('token_type')}")
        try:
            lifetime = max(1.0, float(body.get("expires_in") or 300))
        except (TypeError, ValueError):
            lifetime = 300.0
        successor = body.get("refresh_token")
        self._access = access
        self._expires_at = self._clock() + lifetime
        self._refresh = successor if isinstance(successor, str) and successor.strip() else None
        self.granted_scope = body.get("scope")
        if self.on_rotate is not None:
            # The old refresh token is spent now. If storing the successor fails, say so here,
            # while this process still holds it, rather than at the next start with nothing valid.
            self.on_rotate({"access_token": self._access, "refresh_token": self._refresh,
                            "expires_at": self._expires_at, "scope": self.granted_scope})

    def __repr__(self) -> str:
        return f"UserToken(client_id={self.client_id!r}, refreshable={self._refresh is not None})"


Credentials = ApiKey | ClientCredentials | UserToken


def credentials_from_env(environ: Mapping[str, str] | None = None) -> ApiKey | ClientCredentials:
    """Credentials from the environment, in this order:

    * ``SYNWEVO_API_KEY`` or ``SYNWEVO_API_KEY_FILE`` — a ``dw_`` key;
    * ``SYNWEVO_CLIENT_ID`` + (``SYNWEVO_CLIENT_SECRET`` or ``SYNWEVO_CLIENT_SECRET_FILE``) +
      ``SYNWEVO_TOKEN_URL`` — a service account; optional ``SYNWEVO_TOKEN_SCOPE`` and
      ``SYNWEVO_TOKEN_RESOURCE``.
    """
    env = os.environ if environ is None else environ
    if env.get("SYNWEVO_API_KEY") or env.get("SYNWEVO_API_KEY_FILE"):
        return ApiKey(read_secret(env.get("SYNWEVO_API_KEY"), env.get("SYNWEVO_API_KEY_FILE"), what="API 密钥"))
    if env.get("SYNWEVO_CLIENT_ID"):
        token_url = env.get("SYNWEVO_TOKEN_URL")
        if not token_url:
            raise SynwevoError(None, "missing_credentials", "使用 SYNWEVO_CLIENT_ID 时还需要 SYNWEVO_TOKEN_URL（账户服务的 /oidc/token 地址）")
        secret = read_secret(env.get("SYNWEVO_CLIENT_SECRET"), env.get("SYNWEVO_CLIENT_SECRET_FILE"), what="client_secret")
        return ClientCredentials(token_url, env["SYNWEVO_CLIENT_ID"], secret,
                                 scope=env.get("SYNWEVO_TOKEN_SCOPE") or DEFAULT_SCOPE,
                                 resource=env.get("SYNWEVO_TOKEN_RESOURCE") or None)
    raise SynwevoError(None, "missing_credentials",
                       "没有找到凭据：请设置 SYNWEVO_API_KEY_FILE（或 SYNWEVO_API_KEY），"
                       "或 SYNWEVO_CLIENT_ID + SYNWEVO_CLIENT_SECRET_FILE + SYNWEVO_TOKEN_URL")
