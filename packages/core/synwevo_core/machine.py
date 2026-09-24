"""Who is calling the machine API.

Two kinds of caller reach `/api/v1`, and they are different in a way that matters:

* a **service account** — a machine acting as itself, bound to one workspace, with its own
  lifecycle. A person signing out of a browser must not stop its work.
* a **user delegation** — a client acting *on behalf of* someone who authorised it. Its
  authority is the intersection of three things: what the client is allowed to ask for, what
  that person can currently do, and what this particular grant covered. Never the union, and
  never what the token said when it was minted.

Both arrive as an opaque bearer token issued by the platform. Legacy `dw_<id>_<secret>` keys
keep working — they predate the platform and revoking them all at once would break running
automation — but they are resolved through the same function, so every caller comes out as the
same shape and every call site re-checks the same way.

**Everything is re-derived per request.** A token minted an hour ago says nothing about whether
the account is still active, whether the person is still a member of that workspace, whether
the workspace still has the application, or whether the client is still registered for the
scopes it carries. Freezing that into the token is how a revoked person keeps working until it
expires.

**A token must have been meant for this API** (RFC 9700 §2.3, RFC 8707). Its audience, when
set, must name the Docs resource, and the client that obtained it must be registered for the
Docs application. The application a client belongs to is the question — not which application
the *person* can reach: a portal client's token used to be accepted here and checked against
the portal's "any member" rule, which let anyone with an account write to Docs through the
public portal client.
"""
from __future__ import annotations

import sys
import time

from . import env
from .apps import DOCS_APP_ID
from .identity import Identity
from .errors import ServiceError
from .store import Store

# What a machine caller may do, and which legacy key scope each corresponds to. Kept explicit
# rather than derived, so adding an API scope is a decision rather than a side effect.
SCOPE_READ = "docweave.read"
SCOPE_WRITE = "docweave.write"
# Governance: webhook subscriptions and the caller's own credentials. The legacy `admin` key
# scope is its equivalent, so the routes that already ask for `admin` accept either.
SCOPE_MANAGE = "docweave.manage"
LEGACY_EQUIVALENT = {SCOPE_READ: "read", SCOPE_WRITE: "write", SCOPE_MANAGE: "admin"}

# The audience Docs API tokens carried before resource indicators were configurable. It is
# written into already-issued tokens and into client registrations made from the documentation
# (docs/architecture/platform.md), so it keeps naming this API — refusing it would log every
# existing integration out on upgrade. It names *this* API and nothing else, which is what the
# audience check is for.
LEGACY_DOCS_AUDIENCE = "docweave-api"

WRITE_ROLES = frozenset({"owner", "admin", "editor"})
MANAGE_ROLES = frozenset({"owner", "admin"})


def docs_resource() -> str:
    """The Docs API's resource identifier (`SYNWEVO_DOCS_RESOURCE`). One definition, in oidc."""
    from .oidc import docs_resource as _r
    return _r()


def docs_audiences() -> frozenset:
    """Every audience value that names this API."""
    return frozenset({docs_resource(), LEGACY_DOCS_AUDIENCE})


def legacy_audience_accepted() -> bool:
    """Are tokens with **no** audience still accepted? (R37)

    `SYNWEVO_DOCS_ACCEPT_LEGACY_AUDIENCE`, on by default for one release: tokens issued before
    audiences were stored have none, and they are short-lived — refusing them outright on
    upgrade would be an outage with nothing gained after fifteen minutes. Every acceptance is
    logged (`_warn_null_audience`) so an operator can see which clients still get such tokens
    before turning it off.

    `SYNWEVO_DOCS_REQUIRE_AUDIENCE=1`, the name this setting had during development, is still
    honoured. The two only ever combine towards refusing: either one saying so is enough.
    """
    if env.flag("REQUIRE_AUDIENCE", scope="docs"):
        return False
    return env.flag("ACCEPT_LEGACY_AUDIENCE", default=True, scope="docs")


def audience_required() -> bool:
    """The inverse of `legacy_audience_accepted`, for callers that ask it this way round."""
    return not legacy_audience_accepted()


# (database, client_id) -> when the last warning was printed. A client with a stale
# registration presents such a token on every call; one line per client per interval says
# the same thing without burying the log.
_NULL_AUDIENCE_WARNED: dict[tuple[str, str], float] = {}
NULL_AUDIENCE_WARN_EVERY_S = 3600


def _warn_null_audience(store: Store, client_id: str) -> None:
    key = (str(getattr(store, "db_path", "")), client_id or "?")
    t = time.monotonic()
    last = _NULL_AUDIENCE_WARNED.get(key)
    if last is not None and t - last < NULL_AUDIENCE_WARN_EVERY_S:
        return
    _NULL_AUDIENCE_WARNED[key] = t
    print(f"[synwevo] 警告：接受了未指明受众的访问令牌 client_id={client_id}；"
          f"请让该客户端申请令牌时带 resource={docs_resource()}。"
          f"设置 {env.name_for('ACCEPT_LEGACY_AUDIENCE', scope='docs')}=0 后这类令牌将被拒绝。",
          file=sys.stderr, flush=True)


class MachineAuth:
    def __init__(self, store: Store, ident: Identity, legacy):
        self.store = store
        self.ident = ident
        self.legacy = legacy                 # synwevo_core.auth.Auth — the existing dw_ key checker

    def verify(self, header: str | None) -> dict:
        """Authorization header → principal, or raise 401/403.

        The principal is the same shape for every kind of caller:

            {kind, key_id, tenant, scopes, user_id, client_id, name,
             lineage, grant_family, role, can_export, expires_at}

        `tenant` is the workspace the call acts in. For a service account it comes from the
        registration; for a delegation it is re-resolved from live membership, not read from the
        token — a token carrying a workspace someone has since left would otherwise keep working.
        `lineage` (api keys) and `grant_family` (delegations) are what deferred work is
        authorised against later (`synwevo_core.authority`): a rotation keeps them, a revocation
        ends them.
        """
        token = _bearer(header)
        from .api_platform import enforce_principal
        if token.startswith("dw_"):
            principal = self._legacy_key(header)
        else:
            principal = self._platform_token(token)
        return enforce_principal(self.store, principal)

    # ------------------------------------------------------------------ legacy dw_ keys
    def _legacy_key(self, header: str | None) -> dict:
        """The pre-platform credential, with the platform's workspace and application checks.

        A key names its tenant, and the tenant is a workspace. Freezing a workspace or turning
        Synwevo Docs off for it stopped people and service accounts but not these keys, which
        kept reading and generating that workspace's documents. Two cases stay open on purpose:

        * the unclaimed legacy workspace (`pending_claim`): nobody reaches it through
          membership, and operator-issued keys are the documented way to keep automating against
          the historical data until it is claimed;
        * a tenant with no workspace row: the free-form tenants of the pre-identity model. No
          member can ever reach such a tenant, so a key there isolates only itself.
        """
        p = self.legacy.verify(header)
        why = self._legacy_block(p["tenant"])
        if why:
            code, message = why
            raise ServiceError(403, "forbidden", message, {"reason": code})
        return {**p, "kind": "api_key", "user_id": None, "client_id": None, "grant_family": None,
                "role": None, "can_export": None}

    def _legacy_block(self, tenant: str) -> tuple[str, str] | None:
        from .organizations import workspace_access_failure
        if workspace_access_failure(self.store, tenant):
            return "organization_unavailable", "该组织当前不可用"
        app = self.store.one("SELECT status FROM apps WHERE id=?", (DOCS_APP_ID,))
        if app and app["status"] != "active":
            return "app_disabled", "Synwevo Docs 已在平台范围停用"
        ws = self.store.one("SELECT id, status, deleted_at FROM workspaces WHERE id=?", (tenant,))
        if not ws or ws["status"] == "pending_claim":
            return None
        if ws["deleted_at"] or ws["status"] == "deleted":
            return "workspace_deleted", "该密钥所属的工作空间已删除"
        if ws["status"] != "active":
            return "workspace_frozen", "该密钥所属的工作空间已冻结"
        if not self.store.one("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                              (tenant, DOCS_APP_ID)):
            return "app_not_enabled_for_workspace", "该密钥所属的工作空间已停用 Synwevo Docs"
        return None

    # ------------------------------------------------------------------ platform tokens
    def _platform_token(self, token: str) -> dict:
        from .apps import Apps
        from .oidc import Clients, Grants
        if not token:
            raise ServiceError(401, "unauthorized",
                               "缺少凭据（Authorization: Bearer <平台访问令牌> 或 dw_<id>_<secret>）")
        row = Grants(self.store).load_access(token)
        if not row:
            raise ServiceError(401, "unauthorized", "凭据无效、已过期或已撤销")

        # Audience first: a token for another API is not "a token with too few permissions",
        # it is a token for somebody else, and saying so is what lets the caller fix it.
        aud = row.get("audience")
        if aud:
            if aud not in docs_audiences():
                raise ServiceError(401, "invalid_audience",
                                   f"该访问令牌是签发给「{aud}」的，不是 Synwevo Docs 接口"
                                   f"（{docs_resource()}）；请申请令牌时带 resource={docs_resource()}",
                                   {"audience": aud, "expected": docs_resource()})
        elif not legacy_audience_accepted():
            raise ServiceError(401, "invalid_audience",
                               f"该访问令牌没有指明受众；本部署要求令牌明确签发给 Synwevo Docs 接口"
                               f"（申请时带 resource={docs_resource()}）", {"expected": docs_resource()})
        else:
            _warn_null_audience(self.store, row.get("client_id"))

        clients = Clients(self.store)
        client = clients.get(row["client_id"])
        if not client:
            raise ServiceError(401, "client_unavailable", "签发该凭据的客户端已停用")
        if client.get("app_id") != DOCS_APP_ID:
            # Whose application the client is, not whose the person can reach. A delegation
            # obtained through another application's client was never approved for this one.
            raise ServiceError(403, "wrong_audience",
                               f"该凭据由属于其它应用（{client.get('app_id')}）的客户端取得，不能调用 Synwevo Docs 接口")

        scopes = tuple(s for s in (row["scope"] or "").split() if s in LEGACY_EQUIVALENT)
        if not scopes:
            # An identity token (openid/profile/email) reaching the business API. It proves who
            # signed in; it is not permission to run anything, and treating it as one is exactly
            # the "ID Token as a universal API credential" mistake.
            raise ServiceError(403, "wrong_credential",
                               "这是身份凭据，不能用于调用业务接口；请申请带 docweave.* 权限的访问令牌")
        # What the client is registered for *now*. Narrowing a registration takes effect on the
        # next call, not when the tokens it already holds expire. A service account's ceiling is
        # its `api_scopes`; a user client's is everything it was registered to ask a person for.
        if row["user_id"]:
            registered = clients.allowed_scopes(client)
        else:
            registered = set((client.get("api_scopes") or "").split())
        granted = tuple(s for s in scopes if s in registered)
        if not granted:
            raise ServiceError(403, "forbidden",
                               "签发该凭据的客户端已不再登记这些接口权限（" + " ".join(scopes) + "）",
                               {"reason": "scope_withdrawn"})

        apps = Apps(self.store, self.ident)
        if row["user_id"]:
            return self._delegated(row, client, granted, apps)
        return self._service(row, client, granted, apps)

    # ------------------------------------------------------------------ service account
    def _service(self, row: dict, client: dict, scopes: tuple, apps) -> dict:
        if client.get("kind") != "service":
            raise ServiceError(403, "forbidden", "该凭据不属于服务账户")
        ws = client.get("workspace_id")
        if row.get("workspace_id") and row["workspace_id"] != ws:
            # Re-registered into another workspace after this token was issued. The token was
            # minted for the old one; acting in the new one would be a tenant it never had.
            raise ServiceError(401, "unauthorized", "服务账户的工作空间已变更，请重新获取访问令牌")
        ok, _, reason = apps.check_access_for_workspace(ws or "", DOCS_APP_ID)
        if not ok:
            raise ServiceError(403, "forbidden", reason)
        return {"kind": "service_account", "key_id": client["client_id"], "tenant": ws,
                "scopes": scopes, "user_id": None, "client_id": client["client_id"],
                "name": client["name"], "lineage": None, "grant_family": None,
                "role": None, "can_export": None, "expires_at": row.get("access_expires_at")}

    # ------------------------------------------------------------------ user delegation
    def _delegated(self, row: dict, client: dict, scopes: tuple, apps) -> dict:
        """The intersection, recomputed now.

        The token records what was granted. Whether it still *means* anything is a question
        about the present: is the account live, is that person still a member of that workspace,
        does the workspace still give them Synwevo Docs. All three are asked here, on every call.
        """
        user = self.ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "该凭据背后的账户不可用")

        wanted = row["workspace_id"]
        entitled, wid, reason = apps.check_access(user["id"], DOCS_APP_ID, wanted)
        if not entitled:
            raise ServiceError(403, "forbidden", reason)

        member = self.ident.membership(wid, user["id"])
        if not member:
            raise ServiceError(403, "forbidden", "你已不是该工作空间的成员")
        # The person's current role caps the delegation. A token granted write while they were
        # an editor must not still write after they were moved to read-only, and governance
        # (docweave.manage) follows the workspace admin roles.
        allowed = _role_scopes(member)
        effective = tuple(s for s in scopes if s in allowed)
        if not effective:
            raise ServiceError(403, "forbidden",
                               f"你在该工作空间的权限（{member['role']}）不包含这个凭据的操作范围")
        return {"kind": "delegated", "key_id": f"{client['client_id']}:{user['id']}",
                "tenant": wid, "scopes": effective, "user_id": user["id"],
                "client_id": client["client_id"],
                "name": f"{client['name']}（代表 {user['email']}）",
                "lineage": None, "grant_family": row.get("family_id") or row["id"],
                "role": member["role"], "can_export": bool(member.get("can_export")),
                "expires_at": row.get("access_expires_at")}

    # ------------------------------------------------------------------ signed links
    # Delegated explicitly rather than through __getattr__: two named methods say which parts of
    # the older Auth this wrapper actually stands in for, and a typo still fails loudly.
    def sign_link(self, kind: str, ident: str, tenant: str, ttl_s: int = 900,
                  principal: dict | None = None) -> tuple[str, float]:
        return self.legacy.sign_link(kind, ident, tenant, ttl_s, principal=principal)

    def open_link(self, token: str) -> dict:
        return self.legacy.open_link(token)

    # ------------------------------------------------------------------ authorisation
    @staticmethod
    def require(principal: dict, scope: str) -> None:
        """Check one permission, accepting either vocabulary.

        Legacy keys carry `read`/`write`/`admin`; platform tokens carry `docweave.read` /
        `docweave.write` / `docweave.manage`. Callers ask in the legacy vocabulary because that
        is what every existing route already passes.

        `export` is not a scope a credential carries. It is read access plus, for a person, the
        right to take files off the platform: a viewer without `can_export` may look at results
        but not download them (permissions.md §4.2), and delegating to a client does not give
        the client more than the person has. Keys and service accounts are not people; for them
        export is read.
        """
        if scope == "export":
            MachineAuth.require(principal, "read")
            if principal.get("kind") == "delegated" and principal.get("role") not in WRITE_ROLES \
                    and not principal.get("can_export"):
                raise ServiceError(403, "export_not_allowed",
                                   "你在该工作空间没有导出权限：可以查看结果，但不能下载文件")
            return
        have = set(principal.get("scopes") or ())
        if "admin" in have or scope in have:
            return
        for api_scope, legacy in LEGACY_EQUIVALENT.items():
            if legacy == scope and api_scope in have:
                return
        raise ServiceError(403, "forbidden",
                           f"该凭据没有 {scope} 权限（当前：{'/'.join(sorted(have)) or '无'}）")


def _role_scopes(member: dict) -> set:
    """What a workspace role can do through the machine API."""
    role = member["role"]
    if role in MANAGE_ROLES:
        return {SCOPE_READ, SCOPE_WRITE, SCOPE_MANAGE}
    if role in WRITE_ROLES:
        return {SCOPE_READ, SCOPE_WRITE}
    return {SCOPE_READ}                      # viewer, and anything unrecognised: read only


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return header.strip()
