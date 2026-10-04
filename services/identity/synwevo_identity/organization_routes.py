"""Organization governance HTTP ceremonies; never document access.

Mounted by the identity app with build_router(store, ident, sessions, audit,
invites, mailer). Invitations keep using the existing workspace invitation
service/routes; its acceptance transaction calls the core organization hook.

All sensitive writes use the existing strict ``admin`` reauthentication scope.
Re-read the server session and authority inside the same BEGIN IMMEDIATE as the
write, so revocation/demotion racing the request cannot use stale authority.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import Response

from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations, audit_reason
from synwevo_core.sessions import Sessions
from synwevo_core.store import now
from .profile import canonical_avatar


def build_router(store, ident, sessions, audit, invites, mailer) -> APIRouter:
    r = APIRouter(prefix="/api/auth/organizations", tags=["organization-governance"])

    def service(request):
        return Organizations(store, ident, audit=lambda action, **kw: audit(request, action, **kw))

    def session(request):
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if (not current or current["revoked_at"] is not None
                or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current

    def write(request, operation, *, sensitive=False):
        with store.tx():
            s = session(request)
            if sensitive and not Sessions.reauth_fresh(s, "admin", strict=True):
                raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": "admin"})
            return operation(service(request), s["user_id"])

    @r.get("")
    def list_organizations(request: Request):
        items = service(request).list_for_user(session(request)["user_id"])
        return {"items": items, "total": len(items)}

    @r.post("", status_code=201)
    def create_organization(request: Request, body: dict = Body(...)):
        return write(request, lambda orgs, uid: orgs.create(uid, body.get("name"), body.get("workspace_name"),
                                                           request.headers.get("Idempotency-Key")))

    @r.get("/{oid}")
    def get_organization(request: Request, oid: str):
        return {"organization": service(request).get(oid, session(request)["user_id"])}

    @r.patch("/{oid}")
    def update_organization(request: Request, oid: str, body: dict = Body(...)):
        return write(request, lambda orgs, uid: {"organization": orgs.update(oid, uid, name=body.get("name"),
                     invitation_policy=body.get("invitation_policy"), version=body.get("version"),
                     reason=body.get("reason"))}, sensitive=True)

    @r.get("/{oid}/workspaces")
    def list_workspaces(request: Request, oid: str):
        items = service(request).workspaces(oid, session(request)["user_id"])
        return {"items": items, "total": len(items)}

    @r.get("/{oid}/avatar")
    def avatar(request: Request, oid: str):
        service(request).require(oid, session(request)["user_id"])
        row = store.one("SELECT image FROM organization_avatars WHERE organization_id=?", (oid,))
        if not row:
            raise ServiceError(404, "not_found", "该组织尚未设置头像")
        return Response(bytes(row["image"]), media_type="image/png",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def change_avatar(request, oid, body, remove=False):
        allowed = {"reason"} if remove else {"reason", "image_base64"}
        if set(body) - allowed:
            raise ServiceError(400, "invalid_field", "头像请求包含不支持的字段")
        reason = audit_reason(body.get("reason"))
        def operation(orgs, uid):
            orgs.require(oid, uid, permission="org.settings.manage", active=True)
            if remove:
                store.execute("DELETE FROM organization_avatars WHERE organization_id=?", (oid,))
            else:
                image = canonical_avatar(body.get("image_base64"))
                store.execute("INSERT INTO organization_avatars (organization_id,image,updated_at) VALUES (?,?,?) "
                              "ON CONFLICT(organization_id) DO UPDATE SET image=excluded.image,updated_at=excluded.updated_at",
                              (oid, image, now()))
            audit(request, "organization.avatar.remove" if remove else "organization.avatar.update",
                  actor_id=uid, object_kind="organization", object_id=oid, result="ok", meta={"reason": reason.strip()})
            return {"organization": orgs.get(oid, uid)}
        return write(request, operation, sensitive=True)

    @r.put("/{oid}/avatar")
    def put_avatar(request: Request, oid: str, body: dict = Body(...)):
        return change_avatar(request, oid, body)

    @r.delete("/{oid}/avatar")
    def delete_avatar(request: Request, oid: str, body: dict = Body(...)):
        return change_avatar(request, oid, body, remove=True)

    @r.post("/{oid}/workspaces", status_code=201)
    def create_workspace(request: Request, oid: str, body: dict = Body(...)):
        return write(request, lambda orgs, uid: {"workspace": orgs.create_workspace(oid, uid, body.get("name"))})

    @r.post("/{oid}/workspaces/{wid}/adopt")
    def adopt_workspace(request: Request, oid: str, wid: str, body: dict = Body(...)):
        return write(request, lambda orgs, uid: {"workspace": orgs.adopt_workspace(oid, uid, wid,
                     reason=body.get("reason"))}, sensitive=True)

    @r.get("/{oid}/audit")
    def organization_audit(request: Request, oid: str, before: int | None = Query(None, ge=1, le=2**63 - 1), limit: int = Query(50, ge=1, le=100),
                           category: str = Query("", pattern="^(|organization|members|invitations|roles|imports|approvals)$")):
        """The organization's governance log (CI-101): `org.audit.read`, readable while the organization
        is frozen too. Governance metadata only — synwevo_core.organization_audit says what is in it."""
        uid = session(request)["user_id"]
        service(request).require(oid, uid, permission="org.audit.read")
        from synwevo_core.organization_audit import read as read_audit
        return read_audit(store, oid, before=before, limit=limit, category=category or None)

    @r.get("/{oid}/members")
    def list_members(request: Request, oid: str, q: str = Query("", max_length=100),
                     role: str = Query("", max_length=20), offset: int = Query(0, ge=0),
                     limit: int = Query(50, ge=1, le=200)):
        return service(request).members(oid, session(request)["user_id"], q=q, role=role, offset=offset, limit=limit)

    def _inbox(request: Request, oid: str, actor_id: str, member_id: str, kind: str, **params) -> None:
        """An inbox row for the affected member; the change stands whether or not the row lands."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None or member_id == actor_id:
            return
        try:
            org = store.one("SELECT name FROM organizations WHERE id=?", (oid,)) or {}
            actor = store.one("SELECT display_name, email FROM users WHERE id=?", (actor_id,)) or {}
            notifier.emit(member_id, kind, params={"organization": org.get("name"), "actor": actor.get("display_name") or actor.get("email") or "—", **params},
                          organization_id=oid, object_kind="organization_membership", object_id=member_id,
                          target={"origin": "accounts", "path": "/organizations"})
        except Exception as e:  # noqa: BLE001
            print(f"[synwevo] notification not recorded ({kind}): {type(e).__name__}", flush=True)

    @r.patch("/{oid}/members/{member_id}")
    def change_member(request: Request, oid: str, member_id: str, body: dict = Body(...)):
        def op(orgs, uid):
            member = orgs.change_role(oid, uid, member_id, body.get("role"), reason=body.get("reason"))
            _inbox(request, oid, uid, member_id, "organization.role_changed", role=(member or {}).get("role") or body.get("role"))
            return {"member": member}
        return write(request, op, sensitive=True)

    @r.delete("/{oid}/members/{member_id}")
    def remove_member(request: Request, oid: str, member_id: str, body: dict = Body(...)):
        def op(orgs, uid):
            out = orgs.remove_member(oid, uid, member_id, reason=body.get("reason"))
            _inbox(request, oid, uid, member_id, "member.removed", workspace=(store.one("SELECT name FROM organizations WHERE id=?", (oid,)) or {}).get("name"))
            return out
        return write(request, op, sensitive=True)

    # ------------------------------------------------------------------ custom roles
    from synwevo_core.org_roles import OrgRoles, catalogue as permission_catalogue

    def _locale(request: Request) -> str:
        lang = request.query_params.get("lang")
        if lang in ("zh-Hans", "zh-Hant", "en"):
            return lang
        u = store.one("SELECT locale FROM users WHERE id=?", (session(request)["user_id"],)) or {}
        return u.get("locale") or "zh-Hans"

    @r.get("/{oid}/permissions")
    def list_permissions(request: Request, oid: str):
        """The catalogue in the reader's language, plus what the caller holds here."""
        uid = session(request)["user_id"]
        service(request).require(oid, uid)
        eff = OrgRoles(store).effective(oid, uid)
        return {"catalogue": permission_catalogue(_locale(request)), "mine": eff["permissions"], "base_role": eff["base_role"]}

    @r.get("/{oid}/roles")
    def list_roles(request: Request, oid: str):
        uid = session(request)["user_id"]
        service(request).require(oid, uid, permission="org.members.view")
        return {"items": OrgRoles(store).list(oid)}

    @r.post("/{oid}/roles", status_code=201)
    def create_role(request: Request, oid: str, body: dict = Body(...)):
        if set(body) - {"name", "description", "permissions", "reason"}:
            raise ServiceError(400, "invalid_field", "角色请求包含不支持的字段")
        def op(orgs, uid):
            reason = audit_reason(body.get("reason"))
            orgs.require(oid, uid, active=True)
            role = OrgRoles(store).create(oid, uid, name=body.get("name"), description=body.get("description"), permissions=body.get("permissions"))
            audit(request, "organization.role.create", actor_id=uid, object_kind="organization_role", object_id=role["id"], result="ok",
                  meta={"organization_id": oid, "permissions": role["permissions"], "reason": reason})
            return {"role": role}
        return write(request, op, sensitive=True)

    @r.patch("/{oid}/roles/{rid}")
    def update_role(request: Request, oid: str, rid: str, body: dict = Body(...)):
        if set(body) - {"name", "description", "permissions", "version", "reason"}:
            raise ServiceError(400, "invalid_field", "角色请求包含不支持的字段")
        def op(orgs, uid):
            reason = audit_reason(body.get("reason"))
            orgs.require(oid, uid, active=True)
            role = OrgRoles(store).update(oid, uid, rid, name=body.get("name"), description=body.get("description"),
                                          permissions=body.get("permissions"), version=body.get("version"))
            audit(request, "organization.role.update", actor_id=uid, object_kind="organization_role", object_id=rid, result="ok",
                  meta={"organization_id": oid, "permissions": role["permissions"], "reason": reason})
            return {"role": role}
        return write(request, op, sensitive=True)

    @r.post("/{oid}/roles/{rid}/copy", status_code=201)
    def copy_role(request: Request, oid: str, rid: str, body: dict = Body(default={})):
        """A copy of a role, to adjust from (CI-94): the same rules as creating one."""
        if set(body) - {"name", "reason"}:
            raise ServiceError(400, "invalid_field", "角色请求包含不支持的字段")
        def op(orgs, uid):
            reason = audit_reason(body.get("reason"))
            orgs.require(oid, uid, active=True)
            role = OrgRoles(store).copy(oid, uid, rid, name=body.get("name"))
            audit(request, "organization.role.create", actor_id=uid, object_kind="organization_role", object_id=role["id"], result="ok",
                  meta={"organization_id": oid, "permissions": role["permissions"], "copied_from": rid, "reason": reason})
            return {"role": role}
        return write(request, op, sensitive=True)

    @r.delete("/{oid}/roles/{rid}")
    def delete_role(request: Request, oid: str, rid: str, body: dict = Body(default={})):
        if set(body) - {"unassign", "reason"}:
            raise ServiceError(400, "invalid_field", "角色请求包含不支持的字段")
        def op(orgs, uid):
            reason = audit_reason(body.get("reason"))
            orgs.require(oid, uid, active=True)
            out = OrgRoles(store).delete(oid, uid, rid, unassign=bool(body.get("unassign")))
            audit(request, "organization.role.delete", actor_id=uid, object_kind="organization_role", object_id=rid, result="ok",
                  meta={"organization_id": oid, "unassigned": out["unassigned"], "reason": reason})
            return out
        return write(request, op, sensitive=True)

    @r.put("/{oid}/members/{member_id}/roles")
    def assign_roles(request: Request, oid: str, member_id: str, body: dict = Body(...)):
        if set(body) - {"role_ids", "reason"}:
            raise ServiceError(400, "invalid_field", "角色请求包含不支持的字段")
        def op(orgs, uid):
            reason = audit_reason(body.get("reason"))
            orgs.require(oid, uid, active=True)
            out = OrgRoles(store).assign(oid, uid, member_id, body.get("role_ids"))
            audit(request, "organization.role.assign", actor_id=uid, object_kind="organization_membership", object_id=member_id, result="ok",
                  meta={"organization_id": oid, "added": out["added"], "removed": out["removed"], "reason": reason})
            if (out["added"] or out["removed"]) and member_id != uid:
                _inbox(request, oid, uid, member_id, "organization.roles_assigned",
                       roles="、".join(r["name"] for r in out["roles"]) or "—")
            return out
        return write(request, op, sensitive=True)

    @r.get("/{oid}/members/{member_id}/permissions")
    def member_permissions(request: Request, oid: str, member_id: str):
        """What a member can do here and where each permission comes from — computed from the
        rows, never by acting as that member."""
        uid = session(request)["user_id"]
        service(request).require(oid, uid, permission="org.members.view")
        return OrgRoles(store).effective(oid, member_id)

    @r.post("/{oid}/leave")
    def leave(request: Request, oid: str, body: dict = Body(...)):
        return write(request, lambda orgs, uid: orgs.remove_member(oid, uid, uid, reason=body.get("reason"), leave=True),
                     sensitive=True)

    @r.post("/{oid}/transfer")
    def transfer(request: Request, oid: str, body: dict = Body(...)):
        target = body.get("user_id")
        if not isinstance(target, str) or not target or len(target) > 100:
            raise ServiceError(400, "invalid_field", "请选择要接收所有权的组织成员", {"field": "user_id"})
        return write(request, lambda orgs, uid: {"organization": orgs.transfer(oid, uid, target,
                     reason=body.get("reason"))}, sensitive=True)

    def lifecycle_handler(action):
        def handler(request: Request, oid: str, body: dict = Body(...)):
            return write(request, lambda orgs, uid: orgs.transition(oid, uid, action, reason=body.get("reason")),
                         sensitive=True)
        handler.__name__ = action + "_organization"
        return handler

    for action in ("freeze", "unfreeze", "delete", "recover"):
        r.add_api_route("/{oid}/" + action, lifecycle_handler(action), methods=["POST"])

    return r
