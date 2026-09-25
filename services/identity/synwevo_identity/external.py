"""Sign in with an external OpenID Connect provider (Google, Microsoft, or any compliant issuer).

Synwevo Account stays the authority: the provider proves *who* is at the keyboard, and this
module decides what that is worth. The decisions, in order of how much can go wrong:

* **Identities are linked by (provider, subject), never by e-mail.** The subject is the
  provider's stable identifier for the person. An e-mail string can be re-issued to someone
  else, can be unverified at the provider, and at some providers can be set by the user. So a
  first sign-in from an unlinked identity whose *verified* e-mail matches an existing account
  does not merge anything: the person proves the existing account (password and any second
  factor) and only then is the link written. An unverified e-mail is never used for matching.
* **The whole protocol is checked**, the same way the relying party in `synwevo_auth_client`
  checks the platform's own provider: server-held `state`, `nonce` and PKCE verifier, an exact
  `iss`, our `aud`, `exp`/`iat`, a signature against the provider's JWKS, and single-use of the
  request row. The browser carries an opaque request id in a host-only cookie and nothing else.
* **A second factor still applies.** An account with TOTP enabled finishes the sign-in on the
  ordinary two-step page; the session records `idp:<provider>+totp`. A platform administrator
  without TOTP gets a one-factor session that cannot use the role until the password is
  re-entered on the account page (`sessions.admin_capable`).
* **Linking and unlinking are account-security actions**: they need a recent re-authentication
  for the `mfa` scope, exactly like adding or removing a passkey, and unlinking refuses to remove
  the last way into the account.
* **Not configured means not offered.** A provider appears on the sign-in page only when its
  client id and secret are configured; a partially configured provider stops the service at
  start-up with a message naming the missing variable rather than showing a button that fails.

Configuration (all read through `synwevo_core.env`, so `SYNWEVO_` prefixed):
    SYNWEVO_IDP_PROVIDERS=google,microsoft          which providers to enable, in display order
    SYNWEVO_IDP_GOOGLE_CLIENT_ID / _CLIENT_SECRET   OAuth client registered at the provider
    SYNWEVO_IDP_GOOGLE_ISSUER                       default https://accounts.google.com
    SYNWEVO_IDP_MICROSOFT_TENANT                    default common (or organizations / a tenant id)
    SYNWEVO_IDP_<ID>_LABEL                          button label; defaults per provider
    SYNWEVO_IDP_<ID>_SCOPES                         default "openid email profile"
    SYNWEVO_IDP_PROXY                               outbound proxy for the token/JWKS calls
The redirect URI to register at the provider is `<SYNWEVO_ISSUER>/auth/external/<id>/callback`.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import RedirectResponse

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, registration_mode, valid_email
from synwevo_core.sessions import COOKIE_NAME, Sessions, cookie_kwargs
from synwevo_core.store import Store, new_id, now

from .auth_routes import client_ip, cookies_secure, issue_session
from .mail import security_notice

REQUEST_TTL_S = 600
OFFER_TTL_S = 600
JWKS_TTL_S = 3600
DISCOVERY_TTL_S = 3600
REQ_COOKIE = "dw_extreq"
MAX_NEXT_LEN = 2048
PROVIDER_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

KNOWN: dict[str, dict] = {
    "google": {"label": "Google", "issuer": "https://accounts.google.com"},
    "microsoft": {"label": "Microsoft", "issuer": "https://login.microsoftonline.com/{tenant}/v2.0"},
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def safe_next(raw: str | None) -> str:
    if not raw or not raw.startswith("/") or len(raw) > MAX_NEXT_LEN:
        return "/account"
    if raw.startswith("//") or raw.startswith("/\\") or "\\" in raw[:2] or "://" in raw:
        return "/account"
    return raw


@dataclass
class Provider:
    id: str
    label: str
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    scopes: str = "openid email profile"
    #: Microsoft's multi-tenant discovery document states the issuer with a `{tenantid}`
    #: placeholder; the token's `iss` is the tenant-specific form and must match with the
    #: token's own `tid` substituted. Any other provider must match exactly.
    issuer_template: bool = False
    _discovery: tuple[float, dict] | None = None
    _jwks: tuple[float, dict] | None = None

    @property
    def discovery_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    def expected_issuer(self, claims: dict) -> str | None:
        if not self.issuer_template:
            return self.issuer
        tid = claims.get("tid")
        if not tid or not re.fullmatch(r"[A-Za-z0-9\-]{1,64}", str(tid)):
            return None
        return self.issuer.replace("{tenantid}", str(tid))


def load_providers() -> list[Provider]:
    """Read the configured providers, refusing a half-configured one."""
    raw = (env.get("IDP_PROVIDERS") or "").strip()
    if not raw:
        return []
    out: list[Provider] = []
    for pid in [p.strip().lower() for p in raw.split(",") if p.strip()]:
        if not PROVIDER_ID.match(pid):
            raise ServiceError(500, "idp_config_invalid", f"外部身份提供商标识无效：{pid!r}")
        key = pid.upper().replace("-", "_")
        known = KNOWN.get(pid, {})
        client_id = (env.get(f"IDP_{key}_CLIENT_ID") or "").strip()
        secret = (env.get(f"IDP_{key}_CLIENT_SECRET") or "").strip()
        issuer = (env.get(f"IDP_{key}_ISSUER") or known.get("issuer") or "").strip()
        if pid == "microsoft" and not env.get(f"IDP_{key}_ISSUER"):
            tenant = (env.get("IDP_MICROSOFT_TENANT") or "common").strip()
            if not re.fullmatch(r"[A-Za-z0-9\-.]{1,80}", tenant):
                raise ServiceError(500, "idp_config_invalid", "SYNWEVO_IDP_MICROSOFT_TENANT 无效")
            issuer = issuer.replace("{tenant}", tenant)
        missing = [name for name, value in ((f"SYNWEVO_IDP_{key}_CLIENT_ID", client_id),
                                            (f"SYNWEVO_IDP_{key}_CLIENT_SECRET", secret),
                                            (f"SYNWEVO_IDP_{key}_ISSUER", issuer)) if not value]
        if missing:
            raise ServiceError(500, "idp_config_invalid",
                               f"外部身份提供商 {pid} 已启用但缺少配置：{', '.join(missing)}")
        parts = urllib.parse.urlsplit(issuer)
        if parts.scheme not in ("https", "http") or not parts.netloc or parts.query or parts.fragment:
            raise ServiceError(500, "idp_config_invalid", f"外部身份提供商 {pid} 的 issuer 不是有效地址")
        if parts.scheme != "https" and not parts.hostname.endswith(".localhost") and parts.hostname not in ("localhost", "127.0.0.1"):
            raise ServiceError(500, "idp_config_invalid", f"外部身份提供商 {pid} 的 issuer 必须使用 https")
        template = pid == "microsoft" and ("/common/" in issuer or "/organizations/" in issuer or "/consumers/" in issuer)
        out.append(Provider(
            id=pid, label=(env.get(f"IDP_{key}_LABEL") or known.get("label") or pid.capitalize()).strip(),
            issuer=issuer.rstrip("/"), client_id=client_id, client_secret=secret,
            scopes=(env.get(f"IDP_{key}_SCOPES") or "openid email profile").strip(),
            issuer_template=template))
    return out


class ExternalIdentities:
    """Protocol and persistence for the outbound sign-in. `http` is injected for tests."""

    def __init__(self, store: Store, providers: list[Provider], http=None):
        self.store = store
        self.providers = {p.id: p for p in providers}
        self._http = http
        self._swept_at = 0.0

    # ------------------------------------------------------------------ transport
    @property
    def http(self):
        if self._http is None:
            import httpx
            # trust_env=False: these calls carry the client secret and an authorization code.
            self._http = httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False,
                                      proxy=env.get("IDP_PROXY") or None)
        return self._http

    def public(self) -> list[dict]:
        return [{"id": p.id, "label": p.label} for p in self.providers.values()]

    def status(self, *, probe: bool = False) -> list[dict]:
        """What an administrator may see about each provider: never a secret.

        Configured providers come with the values the provider console must agree with — the
        exact redirect URI, the scopes, the issuer — plus whether the client id and secret are
        present (and the id's first characters, enough to tell two registrations apart). The
        known providers that are *not* configured are listed too, with the variable names that
        are missing, so "why is there no Google button" has an answer on the page rather than
        in a shell. `probe=True` fetches the discovery document now (10 s budget, cached after);
        otherwise the last cached result is reported.
        """
        out = []
        for pid in sorted(set(KNOWN) | set(self.providers)):
            key = pid.upper().replace("-", "_")
            names = {"providers": "SYNWEVO_IDP_PROVIDERS", "client_id": f"SYNWEVO_IDP_{key}_CLIENT_ID",
                     "client_secret": f"SYNWEVO_IDP_{key}_CLIENT_SECRET", "issuer": f"SYNWEVO_IDP_{key}_ISSUER",
                     "tenant": f"SYNWEVO_IDP_{key}_TENANT", "label": f"SYNWEVO_IDP_{key}_LABEL", "scopes": f"SYNWEVO_IDP_{key}_SCOPES"}
            p = self.providers.get(pid)
            if p is None:
                missing = [names["providers"], names["client_id"], names["client_secret"]]
                if pid not in KNOWN:
                    missing.append(names["issuer"])
                out.append({"id": pid, "label": KNOWN.get(pid, {}).get("label", pid), "configured": False,
                            "redirect_uri": self.redirect_uri(Provider(id=pid, label=pid, issuer="", client_id="", client_secret="")),
                            "missing": missing, "env": names})
                continue
            discovery: dict = {"checked": False}
            if probe:
                try:
                    doc = self.discovery(p, force=True)
                    discovery = {"checked": True, "ok": True, "issuer": doc.get("issuer"), "at": int(time.time())}
                except ServiceError as e:
                    discovery = {"checked": True, "ok": False, "error": e.code, "message": e.message, "at": int(time.time())}
                except Exception as e:  # network layer
                    discovery = {"checked": True, "ok": False, "error": "idp_unreachable", "message": type(e).__name__, "at": int(time.time())}
            elif p._discovery:
                discovery = {"checked": True, "ok": True, "issuer": p._discovery[1].get("issuer"), "at": int(p._discovery[0] - DISCOVERY_TTL_S)}
            out.append({"id": p.id, "label": p.label, "configured": True, "issuer": p.issuer,
                        "issuer_template": p.issuer_template, "scopes": p.scopes,
                        "redirect_uri": self.redirect_uri(p),
                        "client_id_hint": (p.client_id[:8] + "…") if len(p.client_id) > 10 else "…",
                        "client_secret_set": bool(p.client_secret), "env": names,
                        "linked_accounts": self.store.one("SELECT COUNT(*) AS n FROM external_identities WHERE provider=?", (p.id,))["n"],
                        "discovery": discovery})
        return out

    def provider(self, pid: str) -> Provider:
        p = self.providers.get(pid or "")
        if not p:
            raise ServiceError(404, "unknown_provider", "未启用这个外部身份提供商")
        return p

    def discovery(self, p: Provider, *, force: bool = False) -> dict:
        if not force and p._discovery and p._discovery[0] > time.time():
            return p._discovery[1]
        r = self.http.get(p.discovery_url)
        if r.status_code != 200:
            raise ServiceError(503, "idp_unreachable", f"无法连接 {p.label}，请稍后再试")
        doc = r.json()
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if not isinstance(doc.get(key), str) or not doc[key]:
                raise ServiceError(503, "idp_unreachable", f"{p.label} 的发现文档不完整")
        declared = doc["issuer"].rstrip("/")
        expected = p.issuer.replace("{tenantid}", "{tenantid}")
        if declared != expected and not (p.issuer_template and declared.replace("{tenantid}", "") == expected.replace("{tenantid}", "")):
            raise ServiceError(503, "idp_unreachable", f"{p.label} 的发现文档 issuer 与配置不一致")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not doc[key].startswith("https://") and not doc[key].startswith(p.issuer.split("/", 3)[0] + "//" + urllib.parse.urlsplit(p.issuer).netloc):
                raise ServiceError(503, "idp_unreachable", f"{p.label} 的端点不是 https")
        p._discovery = (time.time() + DISCOVERY_TTL_S, doc)
        return doc

    def jwks(self, p: Provider, *, force: bool = False) -> dict:
        if not force and p._jwks and p._jwks[0] > time.time():
            return p._jwks[1]
        r = self.http.get(self.discovery(p)["jwks_uri"])
        if r.status_code != 200:
            raise ServiceError(503, "idp_unreachable", f"无法取得 {p.label} 的签名密钥")
        p._jwks = (time.time() + JWKS_TTL_S, r.json())
        return p._jwks[1]

    # ------------------------------------------------------------------ start
    def redirect_uri(self, p: Provider) -> str:
        base = (env.get("ISSUER") or "http://accounts.localhost:8770").rstrip("/")
        return f"{base}/auth/external/{p.id}/callback"

    def begin(self, pid: str, *, purpose: str, next_path: str | None, locale: str | None,
              remember: bool = False, link_user_id: str | None = None,
              link_session_id: str | None = None) -> tuple[str, str]:
        """Create the request row and return (authorization URL, request id)."""
        if purpose not in ("login", "link"):
            raise ValueError(purpose)
        p = self.provider(pid)
        doc = self.discovery(p)
        self._sweep()
        rid = new_id("xr")
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        with self.store.tx() as t:
            t.execute("INSERT INTO external_auth_requests (id,provider,state_hash,nonce,code_verifier,next_path,"
                      "purpose,link_user_id,link_session_id,locale,remember,created_at,expires_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, p.id, hashlib.sha256(state.encode()).hexdigest(), nonce, verifier,
                       safe_next(next_path), purpose, link_user_id, link_session_id,
                       locale if locale in ("zh-Hans", "zh-Hant", "en") else None,
                       1 if remember else 0, now(), now() + REQUEST_TTL_S))
        params = {"response_type": "code", "client_id": p.client_id, "redirect_uri": self.redirect_uri(p),
                  "scope": p.scopes, "state": state, "nonce": nonce,
                  "code_challenge": challenge, "code_challenge_method": "S256"}
        if p.id == "google":
            # Ask Google to return the account picker even when only one account is signed in:
            # linking a *different* Google account must be a choice, not an assumption.
            params["prompt"] = "select_account"
        sep = "&" if urllib.parse.urlsplit(doc["authorization_endpoint"]).query else "?"
        return doc["authorization_endpoint"] + sep + urllib.parse.urlencode(params), rid

    # ------------------------------------------------------------------ callback
    def complete(self, *, request_id: str | None, state: str | None, code: str | None) -> dict:
        if not request_id or not state or not code:
            raise ServiceError(400, "bad_callback", "登录回调参数不完整，请重新开始")
        row = self.store.one("SELECT * FROM external_auth_requests WHERE id=?", (request_id,))
        if not row or row["used_at"] or row["expires_at"] <= now():
            raise ServiceError(400, "bad_callback", "登录请求已过期或已使用，请重新开始")
        given = hashlib.sha256(state.encode("utf-8", "surrogateescape")).hexdigest()
        if not secrets.compare_digest(row["state_hash"], given):
            raise ServiceError(400, "bad_state", "登录状态校验失败，请重新开始")
        with self.store.tx() as t:
            if not t.execute("UPDATE external_auth_requests SET used_at=? WHERE id=? AND used_at IS NULL",
                             (now(), request_id)).rowcount:
                raise ServiceError(400, "bad_callback", "登录请求已被使用")
        p = self.provider(row["provider"])
        doc = self.discovery(p)
        r = self.http.post(doc["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri(p),
            "client_id": p.client_id, "client_secret": p.client_secret, "code_verifier": row["code_verifier"]},
            headers={"Accept": "application/json"})
        if r.status_code != 200:
            raise ServiceError(400, "token_exchange_failed", f"{p.label} 未接受这次登录，请重新开始")
        body = r.json()
        claims = self.verify_id_token(p, body.get("id_token") or "", nonce=row["nonce"])
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        verified = claims.get("email_verified") is True or (p.id == "microsoft" and claims.get("xms_edov") is True)
        if email and not valid_email(email):
            email, verified = None, False
        return {"provider": p, "subject": str(claims["sub"]), "issuer": str(claims["iss"]),
                "email": email, "email_verified": bool(email and verified),
                "display_name": (claims.get("name") if isinstance(claims.get("name"), str) else None),
                "purpose": row["purpose"], "link_user_id": row["link_user_id"],
                "link_session_id": row["link_session_id"], "next_path": row["next_path"],
                "remember": bool(row["remember"]), "locale": row["locale"]}

    def verify_id_token(self, p: Provider, raw: str, *, nonce: str) -> dict:
        from authlib.jose import JsonWebKey, jwt
        if not raw:
            raise ServiceError(400, "no_id_token", f"{p.label} 未返回身份令牌")
        try:
            claims = jwt.decode(raw, JsonWebKey.import_key_set(self.jwks(p)))
        except ServiceError:
            raise
        except Exception:
            # An unknown kid is the normal shape of a key rotation, so refetch once before
            # concluding the signature is bad. A second failure is a refusal, not a crash.
            try:
                claims = jwt.decode(raw, JsonWebKey.import_key_set(self.jwks(p, force=True)))
            except ServiceError:
                raise
            except Exception:
                raise ServiceError(400, "bad_id_token", "身份令牌的签名无法验证") from None
        if not isinstance(claims.get("iss"), str) or claims["iss"].rstrip("/") != (p.expected_issuer(claims) or "").rstrip("/"):
            raise ServiceError(400, "bad_id_token", "身份令牌的发行者不匹配")
        aud = claims.get("aud")
        if p.client_id not in ([aud] if isinstance(aud, str) else (aud or [])):
            raise ServiceError(400, "bad_id_token", "身份令牌不是发给本服务的")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != p.client_id:
            raise ServiceError(400, "bad_id_token", "身份令牌的授权方不匹配")
        if claims.get("nonce") != nonce:
            raise ServiceError(400, "bad_id_token", "身份令牌的 nonce 不匹配")
        ts = int(time.time())
        if int(claims.get("exp") or 0) <= ts:
            raise ServiceError(400, "bad_id_token", "身份令牌已过期")
        if int(claims.get("iat") or 0) > ts + 120:
            raise ServiceError(400, "bad_id_token", "身份令牌的签发时间不合理")
        sub = claims.get("sub")
        if not sub or not isinstance(sub, (str, int)) or len(str(sub)) > 255:
            raise ServiceError(400, "bad_id_token", "身份令牌缺少 sub")
        return dict(claims)

    # ------------------------------------------------------------------ persistence
    def find(self, provider_id: str, subject: str) -> dict | None:
        return self.store.one("SELECT * FROM external_identities WHERE provider=? AND subject=?", (provider_id, subject))

    def list_for_user(self, user_id: str) -> list[dict]:
        rows = self.store.all("SELECT id,provider,email,email_verified,display_name,created_at,last_used_at "
                              "FROM external_identities WHERE user_id=? ORDER BY created_at", (user_id,))
        for r in rows:
            r["label"] = self.providers[r["provider"]].label if r["provider"] in self.providers else r["provider"]
            r["available"] = r["provider"] in self.providers
            r["email_verified"] = bool(r["email_verified"])
        return rows

    def link(self, user_id: str, out: dict) -> dict:
        """Attach the identity to the account. Refuses an identity already linked elsewhere."""
        p: Provider = out["provider"]
        with self.store.tx() as t:
            existing = t.execute("SELECT user_id FROM external_identities WHERE provider=? AND subject=?",
                                 (p.id, out["subject"])).fetchone()
            if existing and existing["user_id"] != user_id:
                raise ServiceError(409, "identity_linked_elsewhere", f"这个 {p.label} 账户已关联到另一个 Synwevo 账户")
            if existing:
                t.execute("UPDATE external_identities SET email=?,email_verified=?,display_name=?,last_used_at=? "
                          "WHERE provider=? AND subject=?",
                          (out["email"], 1 if out["email_verified"] else 0, out["display_name"], now(), p.id, out["subject"]))
                return t.execute("SELECT * FROM external_identities WHERE provider=? AND subject=?", (p.id, out["subject"])).fetchone()
            row_id = new_id("xi")
            t.execute("INSERT INTO external_identities (id,user_id,provider,subject,issuer,email,email_verified,"
                      "display_name,created_at,last_used_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (row_id, user_id, p.id, out["subject"], out["issuer"], out["email"],
                       1 if out["email_verified"] else 0, out["display_name"], now(), now()))
            return t.execute("SELECT * FROM external_identities WHERE id=?", (row_id,)).fetchone()

    def touch(self, identity_id: str) -> None:
        self.store.execute("UPDATE external_identities SET last_used_at=? WHERE id=?", (now(), identity_id))

    def offer(self, user_id: str, out: dict) -> str:
        """A verified-e-mail match with an unlinked account: hold the identity for a few minutes
        while the person proves the existing account. Returns the raw offer token."""
        raw = secrets.token_urlsafe(32)
        self.store.execute(
            "INSERT INTO external_link_offers (id,token_hash,user_id,provider,subject,issuer,email,email_verified,"
            "display_name,next_path,remember,created_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("xo"), hashlib.sha256(raw.encode()).hexdigest(), user_id, out["provider"].id, out["subject"],
             out["issuer"], out["email"], 1 if out["email_verified"] else 0, out["display_name"],
             out["next_path"], 1 if out["remember"] else 0, now(), now() + OFFER_TTL_S))
        return raw

    def peek_offer(self, raw: str) -> dict | None:
        if not raw or len(raw) > 256:
            return None
        row = self.store.one("SELECT * FROM external_link_offers WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))
        if not row or row["used_at"] or row["expires_at"] <= now() or row["provider"] not in self.providers:
            return None
        return row

    def consume_offer(self, raw: str) -> dict | None:
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.store.tx() as t:
            if not t.execute("UPDATE external_link_offers SET used_at=? WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                             (now(), digest, now())).rowcount:
                return None
            return t.execute("SELECT * FROM external_link_offers WHERE token_hash=?", (digest,)).fetchone()

    def _sweep(self) -> None:
        if time.time() - self._swept_at < 300:
            return
        self._swept_at = time.time()
        cutoff = now() - 3600
        self.store.execute("DELETE FROM external_auth_requests WHERE expires_at<?", (cutoff,))
        self.store.execute("DELETE FROM external_link_offers WHERE expires_at<?", (cutoff,))


def other_sign_in_methods(store: Store, user_id: str, *, except_identity: str | None = None) -> int:
    """How many ways into the account remain besides the identity being removed."""
    n = 0
    if store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (user_id,)):
        n += 1
    n += store.one("SELECT COUNT(*) AS n FROM passkeys WHERE user_id=?", (user_id,))["n"]
    n += store.one("SELECT COUNT(*) AS n FROM external_identities WHERE user_id=? AND id<>?",
                   (user_id, except_identity or ""))["n"]
    return n


def build_router(store: Store, ident: Identity, sessions: Sessions, external: ExternalIdentities,
                 mfa, tickets, mailer, audit, describe=None) -> APIRouter:
    r = APIRouter(tags=["auth"])

    def _secure() -> bool:
        return cookies_secure()

    def _failed(reason: str, locale: str | None = None) -> RedirectResponse:
        """Every failure lands on our own sign-in page with a code the page can explain."""
        q = {"ext_error": reason}
        if locale:
            q["lang"] = locale
        resp = RedirectResponse("/login?" + urllib.parse.urlencode(q), status_code=303)
        resp.delete_cookie(REQ_COOKIE, path="/")
        return resp

    @r.get("/api/auth/external/providers")
    def providers():
        return {"items": external.public()}

    @r.get("/auth/external/{pid}/start")
    def start(pid: str, request: Request):
        q = request.query_params
        if len(q.getlist("next")) > 1 or len(q.getlist("lang")) > 1:
            raise ServiceError(400, "invalid_field", "参数重复")
        url, rid = external.begin(pid, purpose="login", next_path=q.get("next"), locale=q.get("lang"),
                                  remember=q.get("remember") == "1")
        resp = RedirectResponse(url, status_code=303)
        resp.set_cookie(REQ_COOKIE, rid, max_age=REQUEST_TTL_S, **cookie_kwargs(_secure()))
        audit(request, "auth.external.start", result="ok", meta={"provider": pid, "purpose": "login"})
        return resp

    @r.post("/api/auth/external/{pid}/link")
    def link_start(pid: str, request: Request, response: Response, body: dict = Body(default={})):
        """Begin linking a provider identity to the signed-in account. Returns the URL to visit."""
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        if not sessions.reauth_fresh(s, "mfa", strict=True):
            raise ServiceError(403, "reauth_required", "关联登录方式前，请重新输入口令确认身份")
        user = ident.get_user(s["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(403, "account_unavailable", "账户不可用")
        url, rid = external.begin(pid, purpose="link", next_path="/account", locale=body.get("locale"),
                                  link_user_id=user["id"], link_session_id=s["id"])
        response.set_cookie(REQ_COOKIE, rid, max_age=REQUEST_TTL_S, **cookie_kwargs(_secure()))
        audit(request, "auth.external.start", actor_id=user["id"], result="ok", meta={"provider": pid, "purpose": "link"})
        return {"url": url}

    @r.get("/auth/external/{pid}/callback")
    def callback(pid: str, request: Request):
        q = request.query_params
        rid = request.cookies.get(REQ_COOKIE)
        pending = store.one("SELECT locale, purpose FROM external_auth_requests WHERE id=?", (rid,)) if rid else None
        locale = (pending or {}).get("locale")
        if q.get("error"):
            audit(request, "auth.external.callback", result="denied", meta={"provider": pid, "reason": "provider_error"})
            return _failed("provider_refused" if q.get("error") != "access_denied" else "cancelled", locale)
        try:
            out = external.complete(request_id=rid, state=q.get("state"), code=q.get("code"))
        except ServiceError as e:
            audit(request, "auth.external.callback", result="denied", meta={"provider": pid, "reason": e.code})
            return _failed(e.code, locale)
        if out["provider"].id != pid:
            return _failed("bad_callback", locale)
        p = out["provider"]

        # -------------------------------------------------------- linking to the current account
        if out["purpose"] == "link":
            s = sessions.load(request.cookies.get(COOKIE_NAME))
            if not s or s["id"] != out["link_session_id"] or s["user_id"] != out["link_user_id"]:
                return _failed("link_session_changed", locale)
            user = ident.get_user(s["user_id"])
            if not user or user["status"] != "active":
                return _failed("account_unavailable", locale)
            try:
                row = external.link(user["id"], out)
            except ServiceError as e:
                audit(request, "auth.external.link", actor_id=user["id"], result="denied",
                      meta={"provider": p.id, "reason": e.code})
                return _failed(e.code, locale)
            audit(request, "auth.external.link", actor_id=user["id"], object_kind="external_identity",
                  object_id=row["id"], result="ok", meta={"provider": p.id})
            _notify(user, f"关联了外部登录方式（{p.label}）")
            resp = RedirectResponse("/account?linked=" + urllib.parse.quote(p.id) + "#security", status_code=303)
            resp.delete_cookie(REQ_COOKIE, path="/")
            return resp

        # -------------------------------------------------------- signing in
        identity = external.find(p.id, out["subject"])
        if identity:
            user = ident.get_user(identity["user_id"])
            if not user or user["status"] != "active":
                audit(request, "auth.external.login", actor_id=(user or {}).get("id"), result="denied",
                      meta={"provider": p.id, "reason": "user_unavailable"})
                return _failed("account_unavailable", locale)
            # A platform administrator may sign in here too; the one-factor session it yields
            # cannot use the role until a second factor is proved (sessions.admin_capable).
            external.touch(identity["id"])
            return _signed_in(request, user, p, out, locale)

        # Unlinked. A verified address that already has an account is an *offer* to link,
        # completed only by proving that account; anything else is a fresh registration, if
        # registration is open and the provider vouched for the address.
        existing = ident.by_email(out["email"]) if out["email"] and out["email_verified"] else None
        if existing and existing["status"] in ("active", "pending"):
            token = external.offer(existing["id"], out)
            audit(request, "auth.external.login", actor_id=existing["id"], result="ok",
                  meta={"provider": p.id, "stage": "link_offer"})
            q2 = {"link": p.id}
            if locale:
                q2["lang"] = locale
            resp = RedirectResponse("/login?" + urllib.parse.urlencode(q2) + "#offer=" + urllib.parse.quote(token), status_code=303)
            resp.delete_cookie(REQ_COOKIE, path="/")
            return resp
        if not out["email"] or not out["email_verified"]:
            audit(request, "auth.external.login", result="denied", meta={"provider": p.id, "reason": "email_unverified"})
            return _failed("email_unverified", locale)
        if registration_mode(store) != "open":
            audit(request, "auth.external.login", result="denied", meta={"provider": p.id, "reason": "registration_closed"})
            return _failed("registration_closed", locale)
        with store.tx():
            try:
                user = ident.create_user(out["email"], None, display_name=(out["display_name"] or "")[:100] or None,
                                         status="active", platform_role="none", with_personal_workspace=False)
            except ServiceError as e:
                if e.code != "email_taken":
                    raise
                return _failed("email_taken", locale)
            store.execute("UPDATE users SET email_verified_at=?, locale=?, onboarding_intent='personal' WHERE id=?",
                          (now(), out["locale"] or "zh-Hans", user["id"]))
            user = ident.get_user(user["id"])
            external.link(user["id"], out)
            audit(request, "auth.registration", actor_id=user["id"], result="ok", meta={"provider": p.id})
        return _signed_in(request, user, p, out, locale, next_override="/onboarding")

    def _signed_in(request: Request, user: dict, p: Provider, out: dict, locale: str | None,
                   next_override: str | None = None) -> RedirectResponse:
        target = next_override or out["next_path"]
        if mfa and mfa.enabled_for(user["id"]):
            ticket = tickets.issue(user["id"], user_agent=request.headers.get("user-agent"),
                                   ip=client_ip(request), first_factor=f"idp:{p.id}")
            f = mfa.factors(user["id"])
            audit(request, "auth.external.login", actor_id=user["id"], object_kind="user", object_id=user["id"],
                  result="ok", meta={"provider": p.id, "stage": "first", "mfa": True})
            frag = {"ticket": ticket, "methods": ",".join(k for k, v in (("totp", f["totp"]), ("recovery", f["recovery_remaining"] > 0)) if v),
                    "next": target, "remember": "1" if out["remember"] else "0"}
            q2 = {"lang": locale} if locale else {}
            resp = RedirectResponse("/login" + ("?" + urllib.parse.urlencode(q2) if q2 else "") + "#" + urllib.parse.urlencode(frag), status_code=303)
            resp.delete_cookie(REQ_COOKIE, path="/")
            return resp
        resp = RedirectResponse(target, status_code=303)
        issue_session(ident, sessions, resp, user, amr=f"idp:{p.id}", request=request,
                      persistent=out["remember"], auth_provider=p.id, describe=describe)
        audit(request, "auth.external.login", actor_id=user["id"], object_kind="user", object_id=user["id"],
              result="ok", meta={"provider": p.id, "remember": out["remember"]})
        resp.delete_cookie(REQ_COOKIE, path="/")
        return resp

    def _notify(user: dict, what: str) -> None:
        try:
            subject, text = security_notice(what=what, when_text="刚刚", locale=user.get("locale") or "zh-Hans")
            mailer.enqueue(user["email"], subject, text, kind="security", ref_id=user["id"])
        except Exception:                      # a notice that fails must not undo the change
            pass

    # ------------------------------------------------------------------ link offers
    @r.post("/api/auth/external/offer/preview")
    def offer_preview(body: dict = Body(...)):
        row = external.peek_offer(body.get("offer") if isinstance(body.get("offer"), str) else "")
        if not row:
            raise ServiceError(410, "bad_token", "这次关联已过期，请重新开始")
        user = ident.get_user(row["user_id"])
        if not user or user["status"] not in ("active", "pending"):
            raise ServiceError(410, "bad_token", "这次关联已过期，请重新开始")
        return {"provider": row["provider"], "label": external.providers[row["provider"]].label,
                "email": user["email"], "expires_at": row["expires_at"],
                "totp": bool(mfa and mfa.enabled_for(user["id"])),
                "password": bool(store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (user["id"],)))}

    @r.post("/api/auth/external/offer/confirm")
    def offer_confirm(request: Request, response: Response, body: dict = Body(...)):
        """Prove the existing account, then link and sign in. The offer is the identity; the
        password (and TOTP or a recovery code, when enabled) is the proof of the account."""
        raw = body.get("offer") if isinstance(body.get("offer"), str) else ""
        row = external.peek_offer(raw)
        if not row:
            raise ServiceError(410, "bad_token", "这次关联已过期，请重新开始")
        user = ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(403, "account_unavailable", "请先完成邮箱验证并激活账户，再关联外部登录")
        res = ident.check_password(user["id"], body.get("password") or "", ip=client_ip(request))
        if not res["ok"]:
            audit(request, "auth.external.link", actor_id=user["id"], result="denied",
                  meta={"provider": row["provider"], "reason": res["reason"]})
            if res["reason"] in ("backoff", "address_blocked"):
                raise ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试",
                                   {"retry_after": int(res.get("retry_after") or 60)})
            raise ServiceError(401, "bad_credentials", "口令不正确")
        amr = "password"
        if mfa and mfa.enabled_for(user["id"]):
            code = (body.get("code") or "").strip()
            recovery = (body.get("recovery_code") or "").strip()
            if code and mfa.verify_totp(user["id"], code):
                amr = "password+totp"
            elif recovery and mfa.use_recovery_code(user["id"], recovery):
                amr = "password+recovery"
            else:
                raise ServiceError(401, "bad_code", "这个账户启用了两步验证，请一并输入验证码")
        consumed = external.consume_offer(raw)
        if not consumed:
            raise ServiceError(410, "bad_token", "这次关联已过期，请重新开始")
        p = external.provider(consumed["provider"])
        out = {"provider": p, "subject": consumed["subject"], "issuer": consumed["issuer"], "email": consumed["email"],
               "email_verified": bool(consumed["email_verified"]), "display_name": consumed["display_name"]}
        linked = external.link(user["id"], out)
        audit(request, "auth.external.link", actor_id=user["id"], object_kind="external_identity",
              object_id=linked["id"], result="ok", meta={"provider": p.id, "via": "offer"})
        _notify(user, f"关联了外部登录方式（{p.label}）")
        remember = bool(consumed["remember"]) and body.get("remember") is not False
        result = issue_session(ident, sessions, response, user, amr=amr, request=request,
                               persistent=remember, auth_provider=p.id, describe=describe)
        result["next"] = safe_next(consumed["next_path"])
        return result

    # ------------------------------------------------------------------ account management
    @r.get("/api/auth/external/identities")
    def identities(request: Request):
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return {"items": external.list_for_user(s["user_id"]), "providers": external.public(),
                "reauth_fresh": sessions.reauth_fresh(s, "mfa", strict=True),
                "other_methods": other_sign_in_methods(store, s["user_id"])}

    @r.delete("/api/auth/external/identities/{iid}")
    def unlink(iid: str, request: Request):
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        if not sessions.reauth_fresh(s, "mfa", strict=True):
            raise ServiceError(403, "reauth_required", "解除关联前，请重新输入口令确认身份")
        row = store.one("SELECT * FROM external_identities WHERE id=? AND user_id=?", (iid, s["user_id"]))
        if not row:
            raise ServiceError(404, "not_found", "没有这个关联")
        if other_sign_in_methods(store, s["user_id"], except_identity=iid) == 0:
            raise ServiceError(409, "last_sign_in_method",
                               "这是账户唯一的登录方式。请先设置口令或添加安全密钥，再解除关联")
        store.execute("DELETE FROM external_identities WHERE id=? AND user_id=?", (iid, s["user_id"]))
        user = ident.get_user(s["user_id"])
        label = external.providers[row["provider"]].label if row["provider"] in external.providers else row["provider"]
        audit(request, "auth.external.unlink", actor_id=s["user_id"], object_kind="external_identity",
              object_id=iid, result="ok", meta={"provider": row["provider"]})
        if user:
            _notify(user, f"解除了外部登录方式（{label}）")
        return {"ok": True}

    return r
