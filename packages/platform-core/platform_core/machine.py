"""Who is calling the machine API.

Two kinds of caller reach `/api/v1`, and they are different in a way that matters:

* a **service account** — a machine acting as itself, bound to one workspace, with its own
  lifecycle. A person signing out of a browser must not stop its work.
* a **user delegation** — a client acting *on behalf of* someone who authorised it. Its
  authority is the intersection of three things: what the client is allowed to ask for, what
  that person can currently do, and what this particular grant covered. Never the union, and
  never what the token said when it was minted.

Both arrive as an opaque bearer token issued by the platform. Legacy `dw_<id>_<secret>` keys
keep working unchanged — they predate the platform and revoking them all at once would break
running automation — but they are resolved through the same function, so every caller comes out
as the same shape and every call site re-checks the same way.

**Everything is re-derived per request.** A token minted an hour ago says nothing about whether
the account is still active, whether the person is still a member of that workspace, or whether
the workspace still has the application. Freezing that into the token is how a revoked person
keeps working until it expires.
"""
from __future__ import annotations

from .identity import Identity
from .errors import ServiceError
from .store import Store

# What a machine caller may do, and which legacy key scope each corresponds to. Kept explicit
# rather than derived, so adding an API scope is a decision rather than a side effect.
SCOPE_READ = "docweave.read"
SCOPE_WRITE = "docweave.write"
LEGACY_EQUIVALENT = {SCOPE_READ: "read", SCOPE_WRITE: "write"}


class MachineAuth:
    def __init__(self, store: Store, ident: Identity, legacy):
        self.store = store
        self.ident = ident
        self.legacy = legacy                 # platform_core.auth.Auth — the existing dw_ key checker

    def verify(self, header: str | None) -> dict:
        """Authorization header → principal, or raise 401/403.

        The principal is the same shape for every kind of caller:

            {kind, key_id, tenant, scopes, user_id, client_id, name}

        `tenant` is the workspace the call acts in. For a service account it comes from the
        registration; for a delegation it is re-resolved from live membership, not read from the
        token — a token carrying a workspace someone has since left would otherwise keep working.
        """
        token = _bearer(header)
        if token.startswith("dw_"):
            # The pre-platform credential. Unchanged, and deliberately still supported.
            p = self.legacy.verify(header)
            return {**p, "kind": "api_key", "user_id": None, "client_id": None}
        return self._platform_token(token)

    def _platform_token(self, token: str) -> dict:
        from .apps import Apps
        from .oidc import Clients, Grants
        if not token:
            raise ServiceError(401, "unauthorized",
                               "缺少凭据（Authorization: Bearer <平台访问令牌> 或 dw_<id>_<secret>）")
        row = Grants(self.store).load_access(token)
        if not row:
            raise ServiceError(401, "unauthorized", "凭据无效、已过期或已撤销")

        client = Clients(self.store).get(row["client_id"])
        if not client:
            raise ServiceError(401, "client_unavailable", "签发该凭据的客户端已停用")
        scopes = tuple(s for s in (row["scope"] or "").split() if s in LEGACY_EQUIVALENT)
        if not scopes:
            # An identity token (openid/profile/email) reaching the business API. It proves who
            # signed in; it is not permission to run anything, and treating it as one is exactly
            # the "ID Token as a universal API credential" mistake.
            raise ServiceError(403, "wrong_credential",
                               "这是身份凭据，不能用于调用业务接口；请申请带 docweave.* 权限的访问令牌")

        apps = Apps(self.store, self.ident)
        if row["user_id"]:
            return self._delegated(row, client, scopes, apps)
        return self._service(row, client, scopes, apps)

    # ------------------------------------------------------------------ service account
    def _service(self, row: dict, client: dict, scopes: tuple, apps) -> dict:
        if client.get("kind") != "service":
            raise ServiceError(403, "forbidden", "该凭据不属于服务账户")
        ws = client.get("workspace_id")
        ok, _, reason = apps.check_access_for_workspace(ws or "", client["app_id"])
        if not ok:
            raise ServiceError(403, "forbidden", reason)
        return {"kind": "service_account", "key_id": client["client_id"], "tenant": ws,
                "scopes": scopes, "user_id": None, "client_id": client["client_id"],
                "name": client["name"]}

    # ------------------------------------------------------------------ user delegation
    def _delegated(self, row: dict, client: dict, scopes: tuple, apps) -> dict:
        """The intersection, recomputed now.

        The token records what was granted. Whether it still *means* anything is a question
        about the present: is the account live, is that person still a member of that workspace,
        does the workspace still have the application. All three are asked here, on every call.
        """
        user = self.ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "该凭据背后的账户不可用")

        wanted = row["workspace_id"]
        entitled, wid, reason = apps.check_access(user["id"], client["app_id"], wanted)
        if not entitled:
            raise ServiceError(403, "forbidden", reason)

        member = self.ident.membership(wid, user["id"])
        if not member:
            raise ServiceError(403, "forbidden", "你已不是该工作空间的成员")
        # The person's current role caps the delegation. A token granted write while they were
        # an editor must not still write after they were moved to read-only.
        allowed = _role_scopes(member)
        effective = tuple(s for s in scopes if s in allowed)
        if not effective:
            raise ServiceError(403, "forbidden",
                               f"你在该工作空间的权限（{member['role']}）不包含这个凭据的操作范围")
        return {"kind": "delegated", "key_id": f"{client['client_id']}:{user['id']}",
                "tenant": wid, "scopes": effective, "user_id": user["id"],
                "client_id": client["client_id"],
                "name": f"{client['name']}（代表 {user['email']}）"}

    # ------------------------------------------------------------------ signed links
    # Delegated explicitly rather than through __getattr__: two named methods say which parts of
    # the older Auth this wrapper actually stands in for, and a typo still fails loudly.
    def sign_link(self, *a, **kw):
        return self.legacy.sign_link(*a, **kw)

    def open_link(self, *a, **kw):
        return self.legacy.open_link(*a, **kw)

    # ------------------------------------------------------------------ authorisation
    @staticmethod
    def require(principal: dict, scope: str) -> None:
        """Check one permission, accepting either vocabulary.

        Legacy keys carry `read`/`write`/`admin`; platform tokens carry `docweave.read` /
        `docweave.write`. Callers ask in the legacy vocabulary because that is what every
        existing route already passes.
        """
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
    if role in ("owner", "admin", "editor"):
        return {SCOPE_READ, SCOPE_WRITE}
    return {SCOPE_READ}                      # viewer, and anything unrecognised: read only


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return header.strip()
