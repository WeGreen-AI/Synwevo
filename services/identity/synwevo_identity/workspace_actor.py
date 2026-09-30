"""Application attribution and live authority for directory writes and invite acceptance."""
from __future__ import annotations

from synwevo_core.api_platform import Approvals
from synwevo_core.apps import PLATFORM_APP_ID
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.oidc import Clients, workspace_resource
from synwevo_core.store import Store

WRITE = "workspace.members.write"
MACHINE_ROLES = ("viewer", "editor")


def require_role(role: str) -> None:
    if role in ("admin", "owner"):
        raise ServiceError(403, "role_needs_person", "管理员与所有者的成员操作需要本人在账户页面完成")
    if role not in MACHINE_ROLES:
        raise ServiceError(400, "bad_role", "角色必须为 viewer 或 editor", {"field": "role"})


def service_approval(store: Store, client_id: str, workspace_id: str, *, approval_id: str | None = None) -> dict:
    """Recheck the exact approval lineage, including at eventual invitation acceptance.

    Replacing a client registration or binding must never revive an old invitation. A directory
    write always needs an explicit owner approval, including clients registered by the CLI.
    """
    client = Clients(store).get(client_id)
    approvals = Approvals(store)
    binding = approvals.binding("service_account", client_id)
    if (not client or client.get("kind") != "service" or client.get("app_id") != PLATFORM_APP_ID
            or client.get("workspace_id") != workspace_id or not binding
            or (approval_id is not None and binding["approval_id"] != approval_id)
            or WRITE not in (client.get("api_scopes") or "").split()
            or workspace_resource() not in client.get("audiences", [])):
        raise ServiceError(403, "approval_inactive", "服务账户的目录写入授权已失效")
    row = approvals.active(binding["approval_id"], tenant=workspace_id)
    if row["resource"] != workspace_resource() or WRITE not in row["effective_scopes"]:
        raise ServiceError(403, "approval_scope_withdrawn", "服务账户没有获批的成员写入权限")
    return row


def actor_label(store: Store, client_id: str, approved_by: str, locale: str = "zh-Hans") -> str:
    client = Clients(store).get_any(client_id) or {}
    person = Identity(store).get_user(approved_by) or {}
    app = client.get("name") or client_id
    name = person.get("display_name") or person.get("email") or approved_by
    return format_actor_label(app, name, locale)


def format_actor_label(app: str, name: str, locale: str = "zh-Hans") -> str:
    if locale == "en":
        return f"Integration {app} (approved by {name})"
    if locale == "zh-Hant":
        return f"整合 {app}（由 {name} 核准）"
    return f"集成 {app}（由 {name} 批准）"
