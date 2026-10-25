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


def directory_workspace_live(store: Store, ident: Identity, workspace_id: str | None) -> tuple[bool, str]:
    """May a directory service account act in this workspace right now? Live workspace, live
    organization — the platform directory has no application switch to check."""
    ws = ident.get_workspace(workspace_id or "")
    if not ws or ws.get("deleted_at") or ws["status"] != "active":
        return False, "该工作空间不可用"
    from .organizations import workspace_access_failure
    if workspace_access_failure(store, workspace_id):
        return False, "该组织当前不可用"
    return True, "ok"


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

    # ------------------------------------------------------------------ workspace directory (API-5)
    def verify_workspace(self, header: str | None) -> dict:
        """The workspace directory API's caller: a platform service account whose token names the
        directory resource. Legacy dw_ keys and delegations are refused — the first predate this
        API and belong to Docs, the second would let an integration read a directory as a person
        (not offered yet). Everything re-derived per call, like the Docs gate."""
        from .api_platform import enforce_principal
        from .apps import PLATFORM_APP_ID
        from .oidc import Clients, Grants, WORKSPACE_API_SCOPES, workspace_resource
        token = _bearer(header)
        if not token:
            raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）")
        if token.startswith("dw_"):
            raise ServiceError(403, "wrong_credential", "访问密钥只能调用 Synwevo Docs 接口；工作空间目录接口需要服务账户令牌")
        row = Grants(self.store).load_access(token)
        if not row:
            raise ServiceError(401, "unauthorized", "凭据无效、已过期或已撤销")
        if row.get("audience") != workspace_resource():
            raise ServiceError(401, "invalid_audience",
                               f"该访问令牌不是签发给工作空间目录接口的；请申请令牌时带 resource={workspace_resource()}",
                               {"audience": row.get("audience"), "expected": workspace_resource()})
        client = Clients(self.store).get(row["client_id"])
        if not client:
            raise ServiceError(401, "client_unavailable", "签发该凭据的客户端已停用")
        if client.get("app_id") != PLATFORM_APP_ID or row["user_id"]:
            raise ServiceError(403, "wrong_credential", "工作空间目录接口只接受该空间的服务账户")
        if client.get("kind") != "service":
            raise ServiceError(403, "forbidden", "该凭据不属于服务账户")
        registered = set((client.get("api_scopes") or "").split())
        scopes = tuple(s for s in (row["scope"] or "").split() if s in WORKSPACE_API_SCOPES and s in registered)
        if not scopes:
            raise ServiceError(403, "forbidden", "该凭据没有工作空间目录权限", {"reason": "scope_withdrawn"})
        ws = client.get("workspace_id")
        if row.get("workspace_id") and row["workspace_id"] != ws:
            raise ServiceError(401, "unauthorized", "服务账户的工作空间已变更，请重新获取访问令牌")
        live, reason = directory_workspace_live(self.store, self.ident, ws)
        if not live:
            raise ServiceError(403, "forbidden", reason)
        principal = {"kind": "service_account", "key_id": client["client_id"], "tenant": ws, "scopes": scopes,
                     "user_id": None, "client_id": client["client_id"], "name": client["name"], "lineage": None,
                     "grant_family": None, "role": None, "can_export": None,
                     "expires_at": row.get("access_expires_at")}
        bound = enforce_principal(self.store, principal)
        if not bound.get("approval_id"):
            # The directory API shipped after owner approvals: a service account without a live
            # one (registered on the host, outside the console) reads no member list (D5).
            raise ServiceError(403, "approval_inactive", "工作空间审批无效、已撤回或已过期")
        return bound

    # ------------------------------------------------------------------ business applications
    def verify_suite(self, header: str | None) -> dict:
        """The business applications' operations API's caller: a client a person authorised, with a
        token that names that API and the workspace the person chose when they consented.

        Legacy dw_ keys belong to Docs. Service accounts are refused, not merely unregistered:
        every operation of these applications is authorized and recorded for a person (their live
        role, their name on what they create), and a machine with nobody behind it has no such
        answer yet. What comes out is a `delegated` principal; which application each call may
        reach, and with what role, is asked again per operation by the capability layer — this
        gate only establishes who is asking, for whom, where, and with which scopes still standing.
        """
        from .api_platform import enforce_principal
        from .apps import PLATFORM_APP_ID
        from .oidc import Clients, Grants, SUITE_API_SCOPES, suite_resource
        from .organizations import workspace_access_failure
        token = _bearer(header)
        if not token:
            raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）")
        if token.startswith("dw_"):
            raise ServiceError(403, "wrong_credential",
                               "访问密钥只能调用 Synwevo Docs 接口；业务应用操作接口需要本人授权的访问令牌")
        row = Grants(self.store).load_access(token)
        if not row:
            raise ServiceError(401, "unauthorized", "凭据无效、已过期或已撤销")
        if row.get("audience") != suite_resource():
            raise ServiceError(401, "invalid_audience",
                               f"该访问令牌不是签发给业务应用操作接口的；请申请令牌时带 resource={suite_resource()}",
                               {"audience": row.get("audience"), "expected": suite_resource()})
        clients = Clients(self.store)
        client = clients.get(row["client_id"])
        if not client:
            raise ServiceError(401, "client_unavailable", "签发该凭据的客户端已停用")
        _refuse_withdrawn_client(self.store, client)
        if client.get("app_id") != PLATFORM_APP_ID:
            raise ServiceError(403, "wrong_audience",
                               f"该凭据由属于其它应用（{client.get('app_id')}）的客户端取得，不能调用业务应用操作接口")
        if not row["user_id"] or client.get("kind") == "service":
            raise ServiceError(403, "wrong_credential",
                               "业务应用操作接口目前只接受本人授权的访问令牌，服务账户尚未开放",
                               {"reason": "service_account_not_supported"})
        held = tuple(s for s in (row["scope"] or "").split() if s in SUITE_API_SCOPES)
        if not held:
            raise ServiceError(403, "wrong_credential",
                               "这是身份凭据，不能用于调用业务接口；请申请带业务应用权限（如 forms.read）的访问令牌")
        registered = clients.allowed_scopes(client)
        scopes = tuple(s for s in held if s in registered)
        if not scopes:
            raise ServiceError(403, "forbidden",
                               "签发该凭据的客户端已不再登记这些接口权限（" + " ".join(held) + "）",
                               {"reason": "scope_withdrawn"})
        user = self.ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "该凭据背后的账户不可用")
        ws = row.get("workspace_id")
        if not ws:
            raise ServiceError(403, "no_workspace", "该凭据没有绑定工作空间，不能调用业务接口")
        space = self.ident.get_workspace(ws)
        if not space or space.get("deleted_at") or space["status"] != "active":
            raise ServiceError(403, "forbidden", "该工作空间不可用")
        why = workspace_access_failure(self.store, ws, user["id"])
        if why:
            raise ServiceError(403, "forbidden", "你已不是该组织的成员" if why == "not_an_organization_member"
                               else "该组织当前不可用", {"reason": why})
        member = self.ident.membership(ws, user["id"])
        if not member:
            raise ServiceError(403, "forbidden", "你已不是该工作空间的成员")
        principal = {"kind": "delegated", "key_id": f"{client['client_id']}:{user['id']}",
                     "tenant": ws, "scopes": scopes, "user_id": user["id"],
                     "client_id": client["client_id"],
                     "name": f"{client['name']}（代表 {user['email']}）",
                     "lineage": None, "grant_family": row.get("family_id") or row["id"],
                     "role": member["role"], "can_export": bool(member.get("can_export")),
                     "expires_at": row.get("access_expires_at")}
        return enforce_principal(self.store, principal)

    def verify_mcp(self, header: str | None) -> dict:
        """The Synwevo MCP server's caller: an MCP client a person authorised, with a token minted for
        the MCP server's own URL (RFC 8707 / RFC 9728: the server names itself, and refuses a token
        minted for anything else — the business API's token included, though the operations behind
        both are the same).

        Everything else is `verify_suite`'s answer: a delegation for a person in the workspace they
        chose when they consented, with the scopes still standing, re-read now. The refusals carry
        what an MCP client needs to recover (`detail["www_authenticate"]`: the bearer challenge of
        RFC 6750 §3 the gate puts on the response), and the principal says it came through MCP so
        the run records can tell the two doors apart.
        """
        from .mcp_policy import McpPolicy
        from .oidc import Grants, mcp_resource
        token = _bearer(header)
        if not token:
            raise ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）",
                               {"www_authenticate": {"error": "invalid_token"}})
        if not McpPolicy(self.store).current().inbound_enabled:
            # No token and no scope opens a server the policy keeps closed: a 403 with no error
            # code in its challenge, which no client re-authorizes on (`http.mcp_challenge`).
            raise ServiceError(403, "mcp_inbound_disabled", "本部署尚未开放 MCP 服务")
        if token.startswith("dw_"):
            raise ServiceError(401, "wrong_credential", "访问密钥只能调用 Synwevo Docs 接口；MCP 服务需要本人授权的访问令牌",
                               {"www_authenticate": {"error": "invalid_token"}})
        row = Grants(self.store).load_access(token)
        if not row:
            raise ServiceError(401, "unauthorized", "凭据无效、已过期或已撤销",
                               {"www_authenticate": {"error": "invalid_token"}})
        expected = mcp_resource()
        if row.get("audience") != expected:
            raise ServiceError(401, "invalid_audience",
                               f"该访问令牌不是签发给 Synwevo MCP 服务的；请申请令牌时带 resource={expected}",
                               {"audience": row.get("audience"), "expected": expected,
                                "www_authenticate": {"error": "invalid_token"}})
        principal = self._suite_delegation(row, api="mcp")
        principal["transport"] = "mcp"
        return principal

    def _suite_delegation(self, row: dict, *, api: str) -> dict:
        """The delegated principal behind a token for the business applications (the operations API
        or the MCP server), with every live fact re-read: shared so the two doors cannot disagree."""
        from .api_platform import enforce_principal
        from .apps import PLATFORM_APP_ID
        from .oidc import Clients, SUITE_API_SCOPES
        from .organizations import workspace_access_failure
        # For the MCP server a 401 says the token is no good — a new authorization can fix that
        # (`invalid_token`). A 403 carries a challenge hint only where more scope can fix it; every
        # other 403 here is answered with no error code (`http.mcp_challenge`).
        challenge = {"www_authenticate": {"error": "invalid_token"}} if api == "mcp" else None
        clients = Clients(self.store)
        client = clients.get(row["client_id"])
        if not client:
            raise ServiceError(401, "client_unavailable", "签发该凭据的客户端已停用", challenge)
        _refuse_withdrawn_client(self.store, client, challenge)
        if client.get("app_id") != PLATFORM_APP_ID:
            raise ServiceError(403, "wrong_audience",
                               f"该凭据由属于其它应用（{client.get('app_id')}）的客户端取得，不能调用业务应用操作接口")
        if not row["user_id"] or client.get("kind") == "service":
            raise ServiceError(403, "wrong_credential",
                               "业务应用操作接口目前只接受本人授权的访问令牌，服务账户尚未开放",
                               {"reason": "service_account_not_supported"})
        held = tuple(s for s in (row["scope"] or "").split() if s in SUITE_API_SCOPES)
        registered = clients.allowed_scopes(client)
        scopes = tuple(s for s in held if s in registered)
        # No business scope this token may still use — it never had one, or the registration has
        # withdrawn every one it had — is a scope refusal in RFC 6750 §3.1's sense. For the MCP
        # server it challenges `insufficient_scope` with the scopes to ask for wherever authorizing
        # again can grant some, and otherwise says nothing a client would re-authorize on.
        step_up = _mcp_step_up(client) if api == "mcp" and not scopes else None
        if not held:
            raise ServiceError(403, "wrong_credential",
                               "这是身份凭据，不能用于调用业务接口；请申请带业务应用权限（如 forms.read）的访问令牌",
                               step_up)
        if not scopes:
            raise ServiceError(403, "forbidden",
                               "签发该凭据的客户端已不再登记这些接口权限（" + " ".join(held) + "）",
                               {"reason": "scope_withdrawn", **(step_up or {})})
        user = self.ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "该凭据背后的账户不可用", challenge)
        ws = row.get("workspace_id")
        if not ws:
            raise ServiceError(403, "no_workspace", "该凭据没有绑定工作空间，不能调用业务接口")
        space = self.ident.get_workspace(ws)
        if not space or space.get("deleted_at") or space["status"] != "active":
            raise ServiceError(403, "forbidden", "该工作空间不可用")
        why = workspace_access_failure(self.store, ws, user["id"])
        if why:
            raise ServiceError(403, "forbidden", "你已不是该组织的成员" if why == "not_an_organization_member"
                               else "该组织当前不可用", {"reason": why})
        member = self.ident.membership(ws, user["id"])
        if not member:
            raise ServiceError(403, "forbidden", "你已不是该工作空间的成员")
        principal = {"kind": "delegated", "key_id": f"{client['client_id']}:{user['id']}",
                     "tenant": ws, "scopes": scopes, "user_id": user["id"],
                     "client_id": client["client_id"],
                     "name": f"{client['name']}（代表 {user['email']}）",
                     "lineage": None, "grant_family": row.get("family_id") or row["id"],
                     "role": member["role"], "can_export": bool(member.get("can_export")),
                     "expires_at": row.get("access_expires_at")}
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


def _refuse_withdrawn_client(store: Store, client: dict, challenge: dict | None = None) -> None:
    """A self-registered MCP client (Client ID Metadata Document) is honoured only while the MCP
    policy accepts it: turning the mechanism off or narrowing its domains ends the tokens it holds
    at their next use, not at their expiry (`cimd.withdrawn_by_policy`)."""
    from .cimd import withdrawn_by_policy
    if withdrawn_by_policy(store, client):
        raise ServiceError(401, "client_unavailable", "本平台已不再接受签发该凭据的客户端（自描述登记）",
                           {"reason": "cimd_not_allowed", **(challenge or {})})


def _mcp_step_up(client: dict) -> dict | None:
    """The scope challenge for an MCP token with no business scope left to use: the scopes that
    authorizing this client again grants it for basic use (`oidc.MCP_DEFAULT_SCOPES` its
    registration lists — what a first authorization would have asked for), or None where there
    are none, so the refusal is not an `insufficient_scope` that no authorization can satisfy."""
    from .oidc import MCP_DEFAULT_SCOPES, mcp_grantable_scopes
    grantable = mcp_grantable_scopes(client)
    ask = [s for s in MCP_DEFAULT_SCOPES if s in grantable]
    return {"www_authenticate": {"error": "insufficient_scope", "scope": " ".join(ask)}} if ask else None


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
