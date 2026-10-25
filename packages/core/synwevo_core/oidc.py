"""OIDC 的数据模型与令牌存储：签名密钥、客户端、授权码与令牌。

在共享层而不是在账户服务里，是因为**验证令牌的一方不是签发它的一方**：Synwevo Docs 的机器接口
要在每次调用时判断一个访问令牌是否仍然有效、属于谁、还剩哪些权限。如果这段代码住在账户服务
里，业务应用就得 import 账户服务才能拒绝一个过期令牌——那条 import 一旦存在，"账户服务可以
独立于业务应用运行"就不再是可检查的事实。协议端点（authorize/token/userinfo/logout）仍然
只在账户服务上，见 `synwevo_identity.provider_routes`。

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
from authlib.jose import JsonWebKey, JsonWebToken, jwt
from pydantic import AnyHttpUrl

from . import env
from .errors import ServiceError
from .store import Store, new_id, now

CODE_TTL_S = 60                      # short: a code is exchanged immediately or not at all
ACCESS_TTL_S = int(env.get("ACCESS_TTL_S", "900"))
REFRESH_TTL_S = int(env.get("REFRESH_TTL_S", str(14 * 24 * 3600)))
ID_TOKEN_TTL_S = 300
LOGOUT_TOKEN_TTL_S = 120
LOGOUT_TOKEN_SKEW_S = 60             # what verify_logout_token tolerates past `exp`
# How long a token signed with a key can still be verified after that key stopped signing:
# the longest signed-token lifetime plus the clock skew a verifier allows. Access and refresh
# tokens are opaque and never depend on the signing key.
SIGNED_TOKEN_MAX_S = max(ID_TOKEN_TTL_S, LOGOUT_TOKEN_TTL_S) + LOGOUT_TOKEN_SKEW_S
ALG = "RS256"
# The longest an old client secret may keep working after a rotation. Bounded because a grace
# period is a second valid credential: it exists so a running automation can be redeployed with
# the new secret without an outage, not so a leaked one can linger.
MAX_SECRET_GRACE_S = 24 * 3600

IDENTITY_SCOPES = ("openid", "profile", "email", "offline_access")
# API scopes. Separate vocabulary from the identity scopes on purpose: a token that proves who
# signed in is not a token that may run a job, and keeping them in one list is how the two
# quietly become the same thing. `docweave.manage` is governance (webhook subscriptions, the
# caller's own credentials) and corresponds to the legacy key scope `admin`.
API_SCOPES = ("docweave.read", "docweave.write", "docweave.manage")
# The workspace directory API (API-5, accounts origin, /api/workspace/v1): a workspace's members,
# roles and invitations, for service accounts its owner approved. Its own resource and audience, so
# a Docs credential cannot reach it and a directory credential cannot reach Docs.
WORKSPACE_API_SCOPES = ("workspace.read", "workspace.members.write")
# The business applications' operations API (workplace origin, /api/suite/v1): Forms, Tables,
# DataPrep, Approvals and Extract, for a client a person authorised. One resource and audience for
# the five, a read and a write scope per application, and a separate `decide` scope for the
# operations that are a person's own decision (an approval decision, confirming an extraction):
# consenting to "write" must not quietly include "decide as me". Written out rather than derived,
# so a new scope is a decision; `capabilities.machine_scope` names the scope each operation needs
# and a test holds the two together. The personal Agent has no scope here on purpose.
SUITE_API_SCOPES = ("forms.read", "forms.write", "tables.read", "tables.write",
                    "dataprep.read", "dataprep.write", "extract.read", "extract.write",
                    "extract.decide", "approvals.read", "approvals.write", "approvals.decide")
# The Synwevo MCP server (workplace origin, /api/suite/v1/mcp) exposes the same operations as the
# business applications' operations API to MCP clients, so a token for it carries the same
# scopes; only the audience differs (`mcp_resource`), because an MCP client names the server
# by URL and the server must refuse a token minted for anything else.
MCP_SCOPES = SUITE_API_SCOPES
# What an MCP client is asked to hold when nothing names a scope: the metadata's `scopes_supported`
# (MCP authorization, "Scope Selection Strategy": the minimal set for basic use) and a step-up from
# a token with no business scope left. Reading and changing the applications' records; a decision
# taken as the person (`*.decide`) is never asked for by default — a client that needs it names it,
# and the person sees it named on the consent page.
MCP_DEFAULT_SCOPES = tuple(s for s in MCP_SCOPES if not s.endswith(".decide"))
MCP_PATH = "/api/suite/v1/mcp"
ALL_API_SCOPES = API_SCOPES + WORKSPACE_API_SCOPES + SUITE_API_SCOPES
SUPPORTED_SCOPES = IDENTITY_SCOPES + ALL_API_SCOPES

# Only these algorithms are ever accepted on a token this platform verifies. Authlib's module
# level `jwt` accepts every registered JWS algorithm — including `none`, which is an unsigned
# token that "verifies" against any key set.
_RS256 = JsonWebToken([ALG])


def decode_signed(raw: str, jwks: dict):
    """Verify a JWT's RS256 signature against a published key set and return its claims.

    The one place a signed token from this issuer is decoded. Pinning the algorithm here is the
    whole defence against `alg: none` and against algorithm confusion; claim checks are the
    caller's, because what "valid" means differs between an id_token and a logout token.
    """
    return _RS256.decode(raw, JsonWebKey.import_key_set(jwks))


def docs_resource() -> str:
    """The resource indicator (RFC 8707) that names the Synwevo Docs API.

    Tokens meant for the Docs API carry this as their audience, and the Docs side refuses a
    token whose audience is set to anything else. Built from the app id rather than written out,
    so the one stable identifier stays in one place.
    """
    return (env.get("DOCS_RESOURCE") or f"urn:synwevo:app:{_docs_app_id()}").strip()


def api_scope_app(scope: str) -> str | None:
    """The application an API scope belongs to (`docweave.read` -> `docweave`,
    `workspace.read` and `forms.write` -> the platform), or None."""
    if scope in API_SCOPES:
        return scope.split(".", 1)[0]
    if scope in WORKSPACE_API_SCOPES or scope in SUITE_API_SCOPES:
        # Platform clients: the directory is the platform's own, and one business-application
        # client may span several applications — each call is checked against the application it
        # names, live, by the capability layer.
        from .apps import PLATFORM_APP_ID
        return PLATFORM_APP_ID
    return None


def workspace_resource() -> str:
    """The resource indicator (RFC 8707) of the workspace directory API."""
    return (env.get("WORKSPACE_RESOURCE") or "urn:synwevo:platform:workspace").strip()


def suite_resource() -> str:
    """The resource indicator (RFC 8707) of the business applications' operations API."""
    return (env.get("SUITE_RESOURCE") or "urn:synwevo:platform:suite").strip()


def canonical_resource_uri(value: str) -> str:
    """An http(s) resource URI in canonical form (RFC 8707 §2; MCP authorization, "Canonical
    Server URI"): lowercase scheme and host, no default port, no fragment, no trailing slash after
    a path (an empty path is written `/`, as every parser writes it) — exactly what a client's URL
    parser makes of the same address (the SDK and the metadata document go through pydantic's
    `AnyHttpUrl`, a browser through `URL`), so the audience a token is minted for, the `resource`
    the metadata advertises and the value a client sends back are one string. Anything that is not
    an http(s) URL is returned as written, less surrounding space, a fragment and trailing slashes."""
    raw = (value or "").strip().split("#", 1)[0].rstrip("/")
    try:
        return str(AnyHttpUrl(raw))
    except ValueError:
        return raw


def mcp_resource() -> str:
    """The canonical URI of the Synwevo MCP server (RFC 8707 `resource`, RFC 9728 `resource`).

    MCP clients name the server they want a token for by its own URL, so unlike the other APIs
    this audience is an https URL: the MCP endpoint on the workplace origin. Configured through
    the workplace origin (`SYNWEVO_APP_URL`) or explicitly (`SYNWEVO_MCP_RESOURCE`), and always
    canonicalised (`canonical_resource_uri`): an operator's `https://Workplace.Example.com:443`
    must not become an audience no client can name. Without either setting there is no MCP server
    to name, and a URN that no client would send stands in so the audience check cannot
    accidentally pass.
    """
    explicit = (env.get("MCP_RESOURCE") or "").strip()
    if explicit:
        return canonical_resource_uri(explicit)
    base = (env.get("APP_URL") or "").strip().rstrip("/")
    return canonical_resource_uri(f"{base}{MCP_PATH}") if base else "urn:synwevo:platform:mcp:unconfigured"


def registered_audiences(audiences: list[str]) -> list[str]:
    """A client registration's audiences as they are kept and compared: an audience that names
    this deployment's MCP server becomes `mcp_resource()` however the operator wrote it — the
    documented `--audience {SYNWEVO_APP_URL}/api/suite/v1/mcp` with an APP_URL of
    `HTTP://Workplace.Example.com:443` is the same server, and a client asks for it by the
    metadata's canonical `resource` — and every other audience stays exactly as written: the other
    resource servers compare their own setting verbatim, and a URN has no canonical form."""
    mcp = mcp_resource()
    return list(dict.fromkeys(mcp if canonical_resource_uri(a) == mcp else a for a in audiences))


def mcp_grantable_scopes(client: dict | None) -> set[str]:
    """The MCP scopes that authorizing `client` again can grant it for this deployment's MCP server:
    what a step-up can reach, and so the only scopes an `insufficient_scope` challenge may name. The
    account service narrows an MCP client's request to its registration (`Clients.allowed_scopes`;
    the rest is dropped, not refused) and issues a token for the MCP server only to a client
    registered for that resource, so for any other scope — or any other client, or a disabled one —
    asking again ends with the same token. Empty when there is nothing to gain."""
    if not client or mcp_resource() not in (client.get("audiences") or []):
        return set()
    return Clients.allowed_scopes(client) & set(MCP_SCOPES)


def _docs_app_id() -> str:
    from .apps import DOCS_APP_ID               # lazy: apps imports identity, which is heavier
    return DOCS_APP_ID


def issuer() -> str:
    """The issuer URL. Configured, never derived from the request.

    Deriving it from Host would let anyone who can reach the service mint tokens claiming to
    come from the real issuer, and clients validate `iss` against exactly this string.
    """
    return (env.get("ISSUER") or "http://accounts.localhost:8770").rstrip("/")


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

    def rotate(self) -> tuple[str, int]:
        """Start signing with a new key. Returns (new kid, how many keys moved to `retiring`).

        The count is returned so a caller can say what actually happened: on a service that has
        never signed anything there is no previous key, and reporting "the old key is still
        published" would be describing a state that does not exist.
        """
        with self.store.tx() as t:
            n = t.execute("UPDATE oidc_keys SET status='retiring' WHERE status='active'").rowcount
        return self.active()["kid"], n

    def retirable_at(self, kid: str) -> float | None:
        """When a retiring key can be withdrawn without failing a token it signed: the moment it
        stopped signing plus SIGNED_TOKEN_MAX_S. `rotate()` flips the old key to retiring and then
        mints its successor, so the earliest key created after this one marks that moment. None
        when no later key exists (nothing proves the key stopped signing)."""
        row = self.store.one("SELECT k.created_at FROM oidc_keys k, oidc_keys o WHERE o.kid=? "
                             "AND k.created_at > o.created_at ORDER BY k.created_at LIMIT 1", (kid,))
        return row["created_at"] + SIGNED_TOKEN_MAX_S if row else None

    def retire(self, kid: str, *, force: bool = False) -> bool:
        """Stop publishing a retiring key. Returns False when it was already retired.

        The active key is never retired (rotate first): the next request would silently mint a
        replacement and every token signed seconds earlier would stop verifying. A retiring key
        waits until the tokens it signed have expired; `force` skips the wait for a compromised
        key, and only for a retiring one."""
        with self.store.tx() as t:
            row = t.execute("SELECT status FROM oidc_keys WHERE kid=?", (kid,)).fetchone()
            if not row:
                raise ServiceError(404, "no_key", f"没有签名密钥 {kid}")
            if row["status"] == "retired":
                return False
            if row["status"] != "retiring":
                raise ServiceError(409, "key_in_use", "这是当前签名密钥，不能退役；先执行 keys rotate")
            if not force:
                at = self.retirable_at(kid)
                if at is None or now() < at:
                    raise ServiceError(409, "key_still_verifying", "用这把密钥签发的令牌可能仍在有效期内，暂不能退役",
                                       {"retirable_at": at})
            t.execute("UPDATE oidc_keys SET status='retired', retired_at=? WHERE kid=?", (now(), kid))
        return True


# ---------------------------------------------------------------------- clients
class Clients:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def _decode(r: dict | None) -> dict | None:
        if r:
            r["redirect_uris"] = json.loads(r["redirect_uris"])
            r["post_logout_redirect_uris"] = json.loads(r["post_logout_redirect_uris"] or "[]")
            # a registration kept before audiences were canonicalised still names the MCP server
            r["audiences"] = registered_audiences(json.loads(r["audiences"] or "[]"))
        return r

    def get(self, client_id: str) -> dict | None:
        return self._decode(self.store.one(
            "SELECT * FROM oauth_clients WHERE client_id=? AND disabled_at IS NULL", (client_id,)))

    def get_any(self, client_id: str) -> dict | None:
        """Including a disabled registration — for the operator's CLI, never for a grant."""
        return self._decode(self.store.one("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,)))

    @staticmethod
    def allowed_scopes(client: dict) -> set[str]:
        """Every scope this client may ever be granted: what it was registered for.

        `scopes` holds the identity scopes (and, for a user client, the API scopes it may ask a
        person to delegate); `api_scopes` is the service-account ceiling. A scope in neither is
        not the client's to ask for, however valid it is on the platform.
        """
        return set((client.get("scopes") or "").split()) | set((client.get("api_scopes") or "").split())

    def register(self, *, client_id: str, app_id: str, name: str, redirect_uris: list[str],
                 scopes: str = "openid profile email", audiences: list[str] | None = None,
                 confidential: bool = False, post_logout_redirect_uris: list[str] | None = None,
                 backchannel_logout_uri: str | None = None, kind: str = "user",
                 workspace_id: str | None = None, api_scopes: str = "") -> tuple[dict, str | None]:
        """Register a client, or update an existing registration's metadata.

        Returns (row, secret). `secret` is not None **only when a new secret was actually
        stored** — on first registration of a confidential client, or when a public client is
        re-registered as confidential. Re-registering an existing confidential client keeps its
        secret: this used to print a freshly generated secret that was never stored, so an
        operator "rotating" a leaked secret got one that failed while the leaked one kept
        working. Rotation is `rotate_secret`, which is explicit about what it replaces.

        Registration is a CLI/configuration action by design. Dynamic client registration is not
        exposed: anything that lets an unauthenticated caller create a client with its own
        redirect URI is a way to have tokens delivered somewhere else.
        """
        for uri in redirect_uris:
            _check_redirect(uri)
        if kind not in ("user", "service"):
            raise ServiceError(400, "bad_kind", f"未知的客户端类型 {kind!r}")
        if kind == "service":
            if not workspace_id:
                raise ServiceError(400, "no_workspace",
                                   "服务账户必须绑定一个工作空间——没有人在背后，租户不能由成员关系推出来")
            if not confidential:
                # Its credential is all it has; a public service account is a published one.
                raise ServiceError(400, "must_be_confidential", "服务账户必须是机密客户端")
        bad = [s for s in api_scopes.split() if s not in ALL_API_SCOPES]
        if bad:
            raise ServiceError(400, "bad_scope", f"未知的接口权限：{' '.join(bad)}")
        own = [s for s in api_scopes.split() if s in SUITE_API_SCOPES]
        if own:
            # `api_scopes` is what a service account may hold with nobody behind it. Every business
            # application operation is recorded and authorized for a person today, so these scopes
            # exist only as something a person delegates (`scopes`).
            raise ServiceError(400, "suite_delegation_only",
                               f"业务应用操作接口的权限（{' '.join(sorted(own))}）目前只能由本人授权给客户端，"
                               "不能登记为服务账户权限")
        unknown = [s for s in (scopes or "").split() if s not in SUPPORTED_SCOPES]
        if unknown:
            raise ServiceError(400, "bad_scope", f"未知的权限：{' '.join(unknown)}")
        # An API scope names the application it opens. A client registered for another
        # application (the portal, say) asking for `docweave.write` would be a way to reach the
        # Docs API that the Docs app registry never approved.
        foreign = [s for s in set(api_scopes.split()) | set((scopes or "").split())
                   if api_scope_app(s) and api_scope_app(s) != app_id]
        if foreign:
            raise ServiceError(400, "scope_wrong_app",
                               f"接口权限 {' '.join(sorted(foreign))} 不属于应用 {app_id}")

        existing = self.store.one("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,))
        if existing and existing["disabled_at"]:
            # Re-registering used to report success while the client stayed disabled (and the
            # CLI then crashed on the missing row). Re-enabling is its own, audited decision.
            raise ServiceError(409, "client_disabled",
                               f"客户端 {client_id} 已停用；如确需恢复，请用 client enable")
        was_confidential = bool(existing) and existing["token_endpoint_auth_method"] != "none"
        if was_confidential and not confidential:
            # Downgrading silently would publish the credential's replacement: a confidential
            # client's existing secret would be dropped and anyone could act as it with PKCE.
            raise ServiceError(409, "confidentiality_change",
                               "机密客户端不能改登记为公共客户端；请停用它并换一个 client_id 登记")
        secret = secrets.token_urlsafe(40) if confidential and not was_confidential else None

        auds = registered_audiences(list(audiences or []))
        has_api = bool(api_scopes.split()) or any(api_scope_app(s) for s in (scopes or "").split())
        if not auds and app_id == _docs_app_id() and has_api:
            # RFC 8707 / RFC 9700 §2.3: a token meant for an API names that API. With no explicit
            # audience a token for this client would be accepted by any resource server that
            # trusts this issuer, so a Docs API client defaults to the Docs resource.
            auds = [docs_resource()]
        elif not auds and has_api and any(s in WORKSPACE_API_SCOPES for s in api_scopes.split()):
            auds = [workspace_resource()]
        elif not auds and any(s in SUITE_API_SCOPES for s in (scopes or "").split()):
            auds = [suite_resource()]
        if existing and "service" in (existing["kind"], kind):
            # A service account's secret already acts in its workspace. Re-registering it renames
            # or narrows it; moving it to another workspace or application, turning it into another
            # kind — either way: a user client's secret, held by a relying party, must not become a
            # workspace credential — or widening its scopes, audiences, callbacks or delegation
            # scopes would hand whoever holds that secret more than anyone approved (admin defect
            # D5). The identity scopes are harmless on a client no person signs in to.
            had_scopes = set((existing["api_scopes"] or "").split())
            had_auds = set(registered_audiences(json.loads(existing["audiences"] or "[]")))
            had_redirects = set(json.loads(existing["redirect_uris"] or "[]"))
            had_delegation = set((existing["scopes"] or "").split())
            if (existing["kind"] != "service" or kind != "service" or existing["app_id"] != app_id
                    or (existing["workspace_id"] or None) != (workspace_id or None)
                    or not set(api_scopes.split()) <= had_scopes or not set(auds) <= had_auds
                    or not set(redirect_uris) <= had_redirects
                    or not set((scopes or "").split()) - {"openid", "profile", "email"} <= had_delegation):
                raise ServiceError(409, "service_account_change",
                                   f"服务账户 {client_id} 不能改换工作空间、应用或类型，也不能扩大权限；"
                                   "请通过工作空间所有者批准的新凭据申请")
        ts = now()
        self.store.execute(
            "INSERT INTO oauth_clients (client_id,app_id,name,secret_hash,redirect_uris,"
            "post_logout_redirect_uris,backchannel_logout_uri,scopes,audiences,"
            "token_endpoint_auth_method,require_pkce,kind,workspace_id,api_scopes,"
            "created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?) "
            "ON CONFLICT(client_id) DO UPDATE SET app_id=excluded.app_id, name=excluded.name, "
            "redirect_uris=excluded.redirect_uris, post_logout_redirect_uris=excluded.post_logout_redirect_uris, "
            "backchannel_logout_uri=excluded.backchannel_logout_uri, scopes=excluded.scopes, "
            "audiences=excluded.audiences, kind=excluded.kind, workspace_id=excluded.workspace_id, "
            "api_scopes=excluded.api_scopes, "
            "secret_hash=COALESCE(excluded.secret_hash, secret_hash), "
            "token_endpoint_auth_method=excluded.token_endpoint_auth_method, "
            "updated_at=excluded.updated_at",
            (client_id, app_id, name, _sha(secret) if secret else None,
             json.dumps(redirect_uris), json.dumps(post_logout_redirect_uris or []),
             backchannel_logout_uri, scopes, json.dumps(auds),
             "client_secret_post" if confidential else "none", kind, workspace_id, api_scopes,
             ts, ts))
        return self.get(client_id), secret

    # -------------------------------------------------------------- secret lifecycle
    @staticmethod
    def _grace_key(client_id: str) -> str:
        return f"client_secret_grace:{client_id}"

    def rotate_secret(self, client_id: str, *, grace_s: float = 0) -> dict:
        """Replace a confidential client's secret. Returns {secret, secret_hash, grace_until}.

        The new secret is returned once and only its hash is stored. With `grace_s` the previous
        secret keeps working until `grace_until` (at most MAX_SECRET_GRACE_S) so a running
        automation can be redeployed without an outage. The grace entry is bound to the hash it
        was created alongside: if the current secret changes again — another rotation, or a
        restore that re-applies a later rotation from the security journal — the old secret
        stops working at once rather than surviving under a stale entry.
        """
        row = self.store.one("SELECT * FROM oauth_clients WHERE client_id=?", (client_id,))
        if not row:
            raise ServiceError(404, "no_client", f"找不到客户端 {client_id}")
        if row["disabled_at"]:
            raise ServiceError(409, "client_disabled", f"客户端 {client_id} 已停用，停用的客户端没有可轮换的密钥")
        if row["token_endpoint_auth_method"] == "none" or not row["secret_hash"]:
            raise ServiceError(409, "public_client", f"{client_id} 是公共客户端，没有密钥可以轮换")
        grace_s = max(0.0, min(float(grace_s or 0), MAX_SECRET_GRACE_S))
        secret = secrets.token_urlsafe(40)
        digest = _sha(secret)
        ts = now()
        until = ts + grace_s if grace_s else None
        with self.store.tx() as t:
            n = t.execute("UPDATE oauth_clients SET secret_hash=?, updated_at=? "
                          "WHERE client_id=? AND secret_hash=? AND disabled_at IS NULL",
                          (digest, ts, client_id, row["secret_hash"])).rowcount
            if not n:
                raise ServiceError(409, "concurrent_change", "客户端在轮换过程中被修改了，请重试")
            if until:
                t.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                          (self._grace_key(client_id),
                           json.dumps({"hash": row["secret_hash"], "until": until, "current": digest})))
            else:
                t.execute("DELETE FROM meta WHERE key=?", (self._grace_key(client_id),))
        return {"secret": secret, "secret_hash": digest, "grace_until": until}

    def disable(self, client_id: str) -> bool:
        """Stop the client authenticating, and end its grace period. Tokens: see Grants."""
        with self.store.tx() as t:
            n = t.execute("UPDATE oauth_clients SET disabled_at=?, updated_at=? "
                          "WHERE client_id=? AND disabled_at IS NULL", (now(), now(), client_id)).rowcount
            t.execute("DELETE FROM meta WHERE key=?", (self._grace_key(client_id),))
        return bool(n)

    def enable(self, client_id: str) -> bool:
        return bool(self.store.execute("UPDATE oauth_clients SET disabled_at=NULL, updated_at=? "
                                       "WHERE client_id=? AND disabled_at IS NOT NULL", (now(), client_id)))

    def grace_until(self, client_id: str) -> float | None:
        """When the previous secret stops working, or None when there is no live grace period."""
        row = self.store.one("SELECT secret_hash FROM oauth_clients WHERE client_id=?", (client_id,))
        g = self._grace(client_id)
        if not row or not g or g.get("current") != row["secret_hash"] or g.get("until", 0) <= now():
            return None
        return g["until"]

    def _grace(self, client_id: str) -> dict | None:
        m = self.store.one("SELECT value FROM meta WHERE key=?", (self._grace_key(client_id),))
        if not m:
            return None
        try:
            return json.loads(m["value"])
        except (TypeError, ValueError):
            return None

    def check_secret(self, client_id: str, secret: str) -> bool:
        r = self.store.one("SELECT secret_hash FROM oauth_clients WHERE client_id=? AND disabled_at IS NULL",
                           (client_id,))
        if not r or not r["secret_hash"]:
            return False
        digest = _sha(secret)
        if secrets.compare_digest(r["secret_hash"], digest):
            return True
        g = self._grace(client_id)
        # The previous secret counts only inside its window *and* only while the secret it was
        # rotated to is still the current one.
        return bool(g and g.get("current") == r["secret_hash"] and float(g.get("until") or 0) > now()
                    and isinstance(g.get("hash"), str) and secrets.compare_digest(g["hash"], digest))

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
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1") \
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
    @staticmethod
    def default_audience(client: dict) -> str | None:
        return (client.get("audiences") or [None])[0]

    def issue_tokens(self, *, client: dict, code_row: dict, with_refresh: bool,
                     family_id: str | None = None, parent_id: str | None = None,
                     audience: str | None = None) -> dict:
        """`audience` is the RFC 8707 resource the token is for, already checked against the
        client's registered audiences by the caller; None means the client's default."""
        access = secrets.token_urlsafe(40)
        refresh = secrets.token_urlsafe(40) if with_refresh else None
        tid = new_id("tok")
        audience = audience or self.default_audience(client)
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

    def rotate_refresh(self, raw: str, *, client: dict, audience: str | None = None) -> tuple[dict, dict]:
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
            if row.get("revoked_reason") == "rotated":
                # Spent by an earlier refresh: whoever presents it now holds a leaked copy.
                raise ServiceError(400, "invalid_grant",
                                   f"刷新令牌已被使用过，该授权的全部令牌已撤销（{n} 个）")
            # Revoked for another reason (the person withdrew consent, signed out, an administrator
            # acted): say so, rather than calling the holder's own token a replay.
            raise ServiceError(400, "invalid_grant", "该授权已被撤销，请重新授权")
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

            # Consumption and replacement share one transaction. Otherwise a concurrent
            # security revocation can see no live row, finish successfully, and miss the
            # successor inserted afterwards. Failed issuance must also leave the original
            # grant usable rather than consuming its only refresh token.
            out = self.issue_tokens(
                client=client,
                code_row={"user_id": row["user_id"], "scope": row["scope"],
                          "central_session_id": row["central_session_id"],
                          "workspace_id": row["workspace_id"]},
                with_refresh=True, family_id=row["family_id"] or row["id"], parent_id=row["id"],
                # A refresh keeps the audience the grant was issued for unless the client narrows it
                # to another registered resource; falling back to the client default here would let
                # a refresh silently widen a token that was minted for one API.
                audience=audience or row.get("audience"))
        return out, row

    def issue_for_service(self, *, client: dict, scope: str, audience: str | None = None) -> dict:
        """The client credentials grant: a machine acting as itself.

        No user, no refresh token, no `openid`. A service account has its own lifecycle on
        purpose — a person closing their browser must not stop its work, and it must not inherit
        anyone's permissions. Its tenant comes from the registration, because with nobody behind
        it there is no membership to resolve one from.
        """
        from .api_platform import approved_service_scopes
        with self.store.tx():
            current = Clients(self.store).get(client["client_id"])
            if not current or current.get("disabled_at") or current.get("kind") != "service":
                raise ServiceError(403, "invalid_client", "服务身份当前不可用")
            if not set(scope.split()).issubset(set((current.get("api_scopes") or "").split())):
                raise ServiceError(403, "invalid_scope", "请求超出了服务身份的授权范围")
            scope = approved_service_scopes(self.store, current, scope)
            audience = audience or self.default_audience(current)
            if not audience or audience not in (current.get("audiences") or []):
                raise ServiceError(403, "invalid_target", "请求的接口受众已不在服务身份授权范围内")
            from .apps import Apps, PLATFORM_APP_ID
            from .identity import Identity
            if current["app_id"] == PLATFORM_APP_ID:
                # The workspace directory (API-5) is the platform's own: a live workspace is the question.
                from .machine import directory_workspace_live
                entitled, _ = directory_workspace_live(self.store, Identity(self.store), current.get("workspace_id"))
            else:
                entitled, _, _ = Apps(self.store, Identity(self.store)).check_access_for_workspace(
                    current.get("workspace_id"), current["app_id"])
            if not entitled:
                raise ServiceError(403, "invalid_client", "服务身份的工作空间或应用授权当前不可用")
            access = secrets.token_urlsafe(40)
            tid = new_id("tok")
            self.store.execute(
                "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,audience,"
                "central_session_id,workspace_id,issued_at,access_expires_at,refresh_expires_at,"
                "family_id,parent_id) VALUES (?,?,NULL,?,NULL,?,?,NULL,?,?,?,NULL,?,NULL)",
                (tid, current["client_id"], _sha(access), scope, audience,
                 current.get("workspace_id"), now(), now() + ACCESS_TTL_S, tid))
        return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL_S,
                "scope": scope}

    def revoke_for_client(self, client_id: str, reason: str) -> int:
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE client_id=? AND revoked_at IS NULL", (now(), reason, client_id))

    def revoke_family(self, family_id: str, reason: str) -> int:
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE family_id=? AND revoked_at IS NULL", (now(), reason, family_id))

    def load_access(self, raw: str) -> dict | None:
        r = self.store.one("SELECT * FROM oauth_tokens WHERE access_hash=?", (_sha(raw),))
        if not r or r.get("revoked_at") or (r["access_expires_at"] or 0) <= now():
            return None
        # Last use, written at most once a minute (the api_keys rule): enough to answer "when did
        # this application last act for me" on the connected-apps page, cheap enough for every call.
        ts = now()
        if (r.get("last_used_at") or 0) < ts - 60:
            self.store.execute("UPDATE oauth_tokens SET last_used_at=? WHERE id=?", (ts, r["id"]))
            r["last_used_at"] = ts
        return r

    def revoke_token(self, raw: str, reason: str = "client_revoked", *, client_id: str | None = None) -> bool:
        """RFC 7009 §2.1: a client may revoke only tokens issued to it. `client_id=None` is the
        platform's own revocation (logout, admin action), which is not a client request."""
        d = _sha(raw)
        sql = ("UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
               "WHERE (access_hash=? OR refresh_hash=?) AND revoked_at IS NULL")
        args: list = [now(), reason, d, d]
        if client_id is not None:
            sql += " AND client_id=?"
            args.append(client_id)
        return bool(self.store.execute(sql, tuple(args)))

    def revoke_for_user_in_workspace(self, user_id: str, workspace_id: str, reason: str) -> int:
        """Every token this person holds that acts in this workspace — used when they leave it.

        Scoped to the workspace on purpose: being removed from one workspace must not sign the
        person out of the others they still belong to.
        """
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? "
            "WHERE user_id=? AND workspace_id=? AND revoked_at IS NULL",
            (now(), reason, user_id, workspace_id))

    def revoke_for_workspace(self, workspace_id: str, reason: str) -> int:
        """Every token acting in this workspace, people's and service accounts' alike (freeze)."""
        return self.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? WHERE workspace_id=? AND revoked_at IS NULL",
            (now(), reason, workspace_id))

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
            "iat": iat, "exp": iat + LOGOUT_TOKEN_TTL_S, "jti": new_id("lt"), "sid": sid,
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
    is still validated. What the validation does, precisely: plain http only to loopback names
    (local development); and no request to an address that no relying party can legitimately
    live on — link-local (which includes cloud metadata at 169.254.169.254), multicast,
    unspecified or reserved ranges. Private ranges over https stay allowed, because a relying
    party on the same private network as the account service is an ordinary deployment.
    """
    try:
        _check_redirect(url)
    except ServiceError as e:
        return False, e.message
    why = _unroutable_target(url)
    if why:
        return False, why
    import httpx
    try:
        # Same reasoning as the relying party's client: no ambient proxy. A logout notification
        # routed through an unrelated proxy either fails silently or hands a signed token
        # naming a user and a session to a third party.
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
                          proxy=env.get("OIDC_PROXY") or None) as c:
            r = c.post(url, data={"logout_token": token},
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        if 200 <= r.status_code < 300:
            return True, None
        return False, f"HTTP {r.status_code}"
    except Exception as e:                                   # network, DNS, TLS, timeout
        return False, f"{type(e).__name__}: {e}"[:200]


def _unroutable_target(url: str) -> str | None:
    """Why this URL must not be posted to, or None. Resolves the host the way the request will."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None                           # unresolvable: the POST fails on its own and says so
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            continue
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            return f"拒绝向 {host} 投递：它解析到不可能是应用的地址 {ip}"
    return None


def verify_logout_token(raw: str, *, jwks: dict, client_id: str, expected_issuer: str | None = None) -> dict:
    """Client side: validate a logout token before acting on it.

    Used by Synwevo Docs as a relying party. Every check here is one that, if skipped, lets someone
    log other people out at will: signature against the issuer's published keys, issuer and
    audience, freshness, the logout event claim, and the absence of `nonce`.
    """
    iss = expected_issuer or issuer()
    try:
        # RS256 only. Authlib's module-level decoder also accepts `alg: none`, which made an
        # unsigned token naming any sid or sub "verify" — an anonymous way to log anyone out.
        claims = decode_signed(raw, jwks)
    except ServiceError:
        raise
    except Exception as e:
        # Authlib raises its own exception types for a malformed or badly signed token. Letting
        # those escape turned a junk POST into a 500, which tells the provider to keep retrying
        # and tells an operator nothing. It is a rejected token, which is a 400.
        raise ServiceError(400, "bad_logout_token", f"登出令牌无法验证：{type(e).__name__}") from e
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
    if not isinstance(claims.get("jti"), str) or not claims.get("jti"):
        # The relying party de-duplicates retries by jti; without one a replay is not detectable.
        raise ServiceError(400, "bad_logout_token", "登出令牌缺少 jti")
    ts = int(time.time())
    if abs(ts - int(claims.get("iat") or 0)) > 300:
        raise ServiceError(400, "bad_logout_token", "登出令牌不在有效时间窗内")
    try:
        exp = int(claims.get("exp"))
    except (TypeError, ValueError):
        raise ServiceError(400, "bad_logout_token", "登出令牌缺少有效的 exp") from None
    if exp + LOGOUT_TOKEN_SKEW_S < ts:                # a minute of clock skew, no more
        raise ServiceError(400, "bad_logout_token", "登出令牌已过期")
    return dict(claims)


def discovery(*, client_id_metadata_documents: bool = False) -> dict:
    """Only the endpoints that actually exist. Advertising more would be a lie a client acts on.

    Served both as OpenID Connect Discovery and as OAuth 2.0 Authorization Server Metadata
    (RFC 8414): MCP clients try either. `client_id_metadata_documents` says whether a client may
    present an https URL as its client id (the platform MCP policy decides); the `iss`
    authorization-response parameter (RFC 9207) is always sent.
    """
    iss = issuer()
    return {
        "authorization_response_iss_parameter_supported": True,
        "client_id_metadata_document_supported": bool(client_id_metadata_documents),
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oidc/authorize",
        "token_endpoint": f"{iss}/oidc/token",
        "userinfo_endpoint": f"{iss}/oidc/userinfo",
        "jwks_uri": f"{iss}/oidc/jwks.json",
        "revocation_endpoint": f"{iss}/oidc/revoke",
        "introspection_endpoint": f"{iss}/oidc/introspect",
        "introspection_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "revocation_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
        "end_session_endpoint": f"{iss}/oidc/logout",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token", "client_credentials"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": [ALG],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "auth_time", "amr", "sid",
                             "email", "email_verified", "name", "nonce", "at_hash"],
        "backchannel_logout_supported": True,
        "backchannel_logout_session_supported": True,
    }
