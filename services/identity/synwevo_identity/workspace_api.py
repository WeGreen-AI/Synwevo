"""The workspace directory API (API-5): who belongs to a workspace, in which role, and who has been
invited — for a service account the workspace's owner approved (`workspace.read`).

The gate (`synwevo_core.http`, prefix /api/workspace/v1) has already verified the caller with
`MachineAuth.verify_workspace`: a platform service account bound to this workspace, a token for
the directory resource, a live owner approval. Nothing here reads a workspace id from the request —
the principal's workspace is the only one there is. Responses carry directory metadata (addresses,
names, roles, dates) and nothing a member made.

Writes use an explicit application actor and the same member and invitation services as the
account pages. Only viewer/editor roles can be managed by an owner-approved integration.

Withdrawal journal entries are fsynced outside SQLite before the surrounding transaction commits,
as in the account governance flows. If a later database write fails, rollback cannot erase that
security intent: a restore may conservatively withdraw access even after the request returned a
failure. This prevents restoring revoked rights; it is not a cross-storage atomicity guarantee.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, ROLE_RANK, normalise_email
from synwevo_core.idempotency import Idempotency
from synwevo_core.machine import MachineAuth
from synwevo_core.store import Store, now

from .invites import INVITE_TTL_S, ROLE_LABEL
from .mail import invitation_mail
from .withdrawals import record as journal
from .workspace_actor import WRITE, actor_label, require_role, service_approval

PREFIX = "/api/workspace/v1"
READ = "workspace.read"
INVITATION_STATES = ("pending", "accepted", "declined", "revoked", "expired")


class DirectoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DirectoryInvitationCreate(DirectoryBody):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(description="viewer or editor; admin/owner require a person and return role_needs_person")
    can_export: bool | None = None
    expires_in_days: int = Field(default=7, ge=1, le=30)


class DirectoryMemberChange(DirectoryBody):
    role: str = Field(description="New role: viewer or editor")
    expected_role: str = Field(description="Last observed role; compared inside the write transaction")


class DirectoryMemberRemove(DirectoryBody):
    reason: str = Field(min_length=4, max_length=500)
    expected_role: str = Field(description="Last observed role; compared inside the removal transaction")


class DirectoryInvitationView(BaseModel):
    id: str
    email: str
    role: str
    can_export: bool
    state: str
    created_at: float
    expires_at: float
    invited_via: str | None = None
    mail: str


class DirectoryMemberChangeResult(BaseModel):
    user_id: str
    role: str
    can_export: bool
    previous_role: str
    journaled: bool | None


class DirectoryMemberRemovalResult(BaseModel):
    user_id: str
    removed: bool
    journaled: bool


class DirectoryInvitationRevocationResult(BaseModel):
    id: str
    state: str
    journaled: bool


class DirectoryWorkspace(BaseModel):
    id: str
    name: str | None
    kind: str | None


class DirectoryWhoami(BaseModel):
    workspace: DirectoryWorkspace
    client_id: str
    name: str | None
    scopes: list[str]


class DirectoryMember(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: str
    account_status: str
    joined_at: float


class DirectoryPage(BaseModel):
    total: int
    offset: int
    limit: int
    next_offset: int | None


class DirectoryMemberPage(DirectoryPage):
    items: list[DirectoryMember]
    owner_count: int


class DirectoryInvitation(BaseModel):
    id: str
    email: str
    role: str
    state: str
    created_at: float
    expires_at: float
    invited_via: str | None


class DirectoryInvitationPage(DirectoryPage):
    items: list[DirectoryInvitation]


def build_router(store: Store, ident: Identity, invites, mailer, audit) -> APIRouter:
    r = APIRouter(tags=["workspace-directory"])
    idem = Idempotency(store)
    machine = MachineAuth(store, ident, None)

    def caller(request: Request, scope: str = READ) -> dict:
        p = getattr(request.state, "principal", None)
        if not p or p.get("kind") != "service_account" or not p.get("tenant"):
            raise ServiceError(401, "unauthorized", "缺少工作空间目录凭据")
        if scope not in (p.get("scopes") or ()):
            raise ServiceError(403, "forbidden", f"该凭据没有 {scope} 权限", {"required": scope})
        return p

    def writer(request: Request) -> tuple[dict, dict]:
        caller(request, WRITE)
        # Re-read the token, client and approval under the write transaction, so revocation,
        # role changes and expiry between the gateway and the write cannot leave stale power.
        p = machine.verify_workspace(request.headers.get("authorization"))
        if WRITE not in p["scopes"]:
            raise ServiceError(403, "forbidden", f"该凭据没有 {WRITE} 权限", {"required": WRITE})
        approval = service_approval(store, p["client_id"], p["tenant"], approval_id=p.get("approval_id"))
        return p, approval

    def invitation_in(p: dict, iid: str) -> dict:
        row = invites.get(iid)
        if not row or row["workspace_id"] != p["tenant"]:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        require_role(row["role"])
        return row

    def log(request: Request, p: dict, approval: dict, action: str, kind: str, oid: str, **meta) -> None:
        audit(request, action, actor_kind="machine", actor_id=p["client_id"], workspace_id=p["tenant"],
              object_kind=kind, object_id=oid, result="ok",
              meta={"via": "api", "actor_kind": "service_account", "approved_by": approval["approved_by"],
                    "approval_id": approval["id"], **meta})

    def notify(request: Request, p: dict, approval: dict, uid: str, kind: str, **kw) -> None:
        notifier = getattr(request.app.state, "notifier", None)
        if notifier:
            user = ident.get_user(uid) or {}
            label = actor_label(store, p["client_id"], approval["approved_by"], user.get("locale") or "zh-Hans")
            params = {"workspace": (ident.get_workspace(p["tenant"]) or {}).get("name"),
                      "actor": label, "inviter": label, **kw.pop("params", {})}
            notifier.emit(uid, kind, params=params, workspace_id=p["tenant"], **kw)

    def issue(request: Request, p: dict, approval: dict, body: DirectoryInvitationCreate, resend_of: str | None = None) -> dict:
        require_role(body.role)
        row, raw = invites.create(workspace_id=p["tenant"], email=body.email, role=body.role,
                                  actor_id=approval["approved_by"], can_export=body.can_export,
                                  ttl_s=body.expires_in_days * 86400, invited_via=p["client_id"],
                                  invited_approval_id=approval["id"])
        ws = ident.get_workspace(p["tenant"])
        approver = ident.get_user(approval["approved_by"]) or {}
        recipient = ident.by_email(row["email_key"])
        locale = (recipient or {}).get("locale") or approver.get("locale") or "zh-Hans"
        store.execute("UPDATE invitations SET locale=? WHERE id=?", (locale, row["id"]))
        subject, text, html = invitation_mail(workspace=ws["name"],
            inviter=actor_label(store, p["client_id"], approval["approved_by"], locale),
            role_label=ROLE_LABEL[row["role"]], token=raw, expires_h=body.expires_in_days * 24, locale=locale)
        # The real mail outbox shares the invitation/idempotency transaction. A mail configuration
        # failure leaves neither an invitation nor a cached success. Delivery happens after commit.
        mailer.enqueue(row["email_key"], subject, text, kind="invite", ref_id=row["id"], html=html)
        for old in row.get("superseded_ids") or []:
            journal(store, "invitation_revoked", invitation_id=old)
            request.app.state.notifier.set_action_state("invitation", old, "void")
        log(request, p, approval, "invite.resend" if resend_of else "invite.create", "invitation", row["id"],
            role=row["role"], resend_of=resend_of)
        if recipient:
            notify(request, p, approval, recipient["id"], "invitation.received", params={"role": row["role"]},
                   object_kind="invitation", object_id=row["id"], dedupe_key=f"invite:{row['id']}",
                   actionable=True, target={"origin": "accounts", "path": "/onboarding"})
        return DirectoryInvitationView.model_validate({"id": row["id"], "email": row["email_key"], "role": row["role"],
                "can_export": bool(row["can_export"]), "state": "pending", "created_at": row["created_at"],
                "expires_at": row["expires_at"], "invited_via": p["client_id"], "mail": "queued"}).model_dump()

    def replay_invitation(request: Request, out: dict) -> None:
        p, _ = writer(request)
        invitation_in(p, out["id"])

    @r.post(PREFIX + "/invitations", status_code=201, response_model=DirectoryInvitationView,
            summary="由获批集成发送真实成员邀请（只限 viewer/editor；支持 Idempotency-Key）")
    def create_invitation(request: Request, body: DirectoryInvitationCreate):
        caller(request, WRITE)
        data = body.model_copy(update={"email": normalise_email(body.email)})
        require_role(data.role)
        def work():
            with store.tx():
                p, approval = writer(request)
                return issue(request, p, approval, data)
        return idem.run(request, PREFIX + "/invitations", data.model_dump(), work,
                        replay_check=lambda out: replay_invitation(request, out))

    @r.post(PREFIX + "/invitations/{iid}/resend", status_code=201, response_model=DirectoryInvitationView,
            summary="重发待接受或过期邀请，新链接取代旧链接（支持 Idempotency-Key）")
    def resend_invitation(request: Request, iid: str, body: DirectoryBody = DirectoryBody()):
        caller(request, WRITE)
        def work():
            with store.tx():
                p, approval = writer(request)
                old = invitation_in(p, iid)
                state = invites._state(old)
                if state not in ("pending", "expired"):
                    raise ServiceError(409, f"invite_{state}", "已接受、已拒绝或已撤销的邀请不能重发")
                data = DirectoryInvitationCreate(email=old["email_key"], role=old["role"],
                                        can_export=bool(old["can_export"]), expires_in_days=INVITE_TTL_S // 86400)
                return issue(request, p, approval, data, iid)
        return idem.run(request, PREFIX + "/invitations/{iid}/resend", {"id": iid}, work,
                        replay_check=lambda out: replay_invitation(request, out))

    @r.post(PREFIX + "/invitations/{iid}/revoke", response_model=DirectoryInvitationRevocationResult,
            summary="撤回本空间的普通成员邀请")
    def revoke_invitation(request: Request, iid: str, body: DirectoryBody = DirectoryBody()):
        with store.tx():
            p, approval = writer(request)
            row = invitation_in(p, iid)
            if row["accepted_at"]:
                raise ServiceError(409, "invite_accepted", "已经接受的邀请不能撤回")
            if not row["revoked_at"]:
                invites.revoke(iid, approval["approved_by"])
                journaled = journal(store, "invitation_revoked", invitation_id=iid)
                request.app.state.notifier.set_action_state("invitation", iid, "void")
                log(request, p, approval, "invite.revoke", "invitation", iid, journaled=journaled)
                recipient = ident.by_email(row["email_key"])
                if recipient:
                    notify(request, p, approval, recipient["id"], "invitation.revoked", object_kind="invitation",
                           object_id=iid, target={"origin": "accounts", "path": "/onboarding"})
            else:
                journaled = journal(store, "invitation_revoked", invitation_id=iid)
        return {"id": iid, "state": "revoked", "journaled": journaled}

    def authority(request: Request, expected_role: str, role: str | None = None):
        def check(target: dict, actor: dict | None):
            p, _ = writer(request)
            require_role(target["role"])
            if target["role"] != expected_role:
                raise ServiceError(409, "role_changed", "成员角色已改变，请重新读取后再操作", {"field": "expected_role"})
            if role:
                require_role(role)
                if ROLE_RANK[role] > ROLE_RANK[target["role"]]:
                    ws = ident.get_workspace(p["tenant"]) or {}
                    if ws.get("status") != "active":
                        raise ServiceError(409, "workspace_frozen", "冻结空间不能增加成员权限")
        return check

    @r.patch(PREFIX + "/members/{user_id}", response_model=DirectoryMemberChangeResult,
             summary="更改普通成员角色（expected_role 必填，冲突时返回 role_changed）")
    def change_member(request: Request, user_id: str, body: DirectoryMemberChange):
        caller(request, WRITE)
        require_role(body.role)
        require_role(body.expected_role)
        with store.tx():
            p, approval = writer(request)
            row = ident.change_role(p["tenant"], user_id, body.role,
                authorize=authority(request, body.expected_role, body.role))
            before = row["before"]
            journaled = None
            if ROLE_RANK[body.role] < ROLE_RANK[before["role"]]:
                journaled = journal(store, "role_changed", workspace_id=p["tenant"], user_id=user_id, role=body.role)
            if body.role != before["role"]:
                log(request, p, approval, "member.role", "membership", user_id,
                    before=before, after={"role": row["role"], "can_export": bool(row["can_export"])}, journaled=journaled)
                notify(request, p, approval, user_id, "member.role_changed", params={"role": row["role"]},
                       object_kind="membership", object_id=user_id, target={"origin": "accounts", "path": "/account#workspaces"})
        return {"user_id": user_id, "role": row["role"], "can_export": bool(row["can_export"]),
                "previous_role": before["role"], "journaled": journaled}

    @r.post(PREFIX + "/members/{user_id}/remove", response_model=DirectoryMemberRemovalResult,
            summary="移除普通成员并撤回其工作空间权限（expected_role 与原因必填）")
    def remove_member(request: Request, user_id: str, body: DirectoryMemberRemove):
        caller(request, WRITE)
        require_role(body.expected_role)
        reason = body.reason.strip()
        if len(reason) < 4:
            raise ServiceError(400, "missing_reason", "移除理由至少需要 4 个字", {"field": "reason"})
        with store.tx():
            p, approval = writer(request)
            removed = ident.remove_member(p["tenant"], user_id, authorize=authority(request, body.expected_role))
            if removed is None:
                raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
            journaled = journal(store, "member_removed", workspace_id=p["tenant"], user_id=user_id)
            for iid in removed["cancelled_invitation_ids"]:
                journaled = journal(store, "invitation_revoked", invitation_id=iid) and journaled
                request.app.state.notifier.set_action_state("invitation", iid, "void")
            log(request, p, approval, "member.remove", "membership", user_id, reason=reason,
                before={"role": removed["role"], "can_export": removed["can_export"]}, journaled=journaled)
            notify(request, p, approval, user_id, "member.removed", object_kind="membership", object_id=user_id,
                   target={"origin": "accounts", "path": "/account#workspaces"})
        return {"user_id": user_id, "removed": True, "journaled": journaled}

    def paged(items: list, total: int, offset: int, limit: int) -> dict:
        nxt = offset + len(items)
        return {"items": items, "total": total, "offset": offset, "limit": limit, "next_offset": nxt if nxt < total else None}

    @r.get(PREFIX + "/whoami", response_model=DirectoryWhoami, summary="当前凭据与它所属的工作空间")
    def whoami(request: Request):
        p = caller(request)
        ws = ident.get_workspace(p["tenant"]) or {}
        return {"workspace": {"id": p["tenant"], "name": ws.get("name"), "kind": ws.get("kind")},
                "client_id": p["client_id"], "name": p.get("name"), "scopes": list(p["scopes"])}

    @r.get(PREFIX + "/members", response_model=DirectoryMemberPage, summary="工作空间成员（地址、名称、角色、加入时间；分页）")
    def members(request: Request, role: str = Query("", pattern="^(|owner|admin|editor|viewer)$"),
                q: str = Query("", max_length=100), limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0, le=2**31)):
        p = caller(request)
        out = ident.list_members(p["tenant"], q=q, role=role or None, offset=offset, limit=limit)
        items = [{"user_id": m["user_id"], "email": m["email"], "display_name": m["display_name"], "role": m["role"],
                  "account_status": m["account_status"], "joined_at": m["joined_at"]} for m in out["items"]]
        return {**paged(items, out["total"], offset, limit), "owner_count": out["owner_count"]}

    @r.get(PREFIX + "/invitations", response_model=DirectoryInvitationPage, summary="工作空间邀请及其状态（分页，最新在前）")
    def invitations(request: Request, state: str = Query("", pattern="^(|pending|accepted|declined|revoked|expired)$"),
                    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**31)):
        p = caller(request)
        t = now()
        state_sql = {
            "accepted": "i.accepted_at IS NOT NULL",
            "declined": "i.accepted_at IS NULL AND i.declined_at IS NOT NULL",
            "revoked": "i.accepted_at IS NULL AND i.declined_at IS NULL AND i.revoked_at IS NOT NULL",
            "expired": "i.accepted_at IS NULL AND i.declined_at IS NULL AND i.revoked_at IS NULL AND i.expires_at<=?",
            "pending": "i.accepted_at IS NULL AND i.declined_at IS NULL AND i.revoked_at IS NULL AND i.expires_at>?",
        }
        where, args = ["i.workspace_id=?"], [p["tenant"]]
        if state:
            where.append(state_sql[state])
            if state in ("expired", "pending"):
                args.append(t)
        sql = " AND ".join(where)
        total = store.one(f"SELECT COUNT(*) AS n FROM invitations i WHERE {sql}", tuple(args))["n"]
        rows = store.all(f"SELECT i.id, i.email_key, i.role, i.created_at, i.expires_at, i.accepted_at, i.revoked_at, "
                         f"i.declined_at, i.invited_via FROM invitations i WHERE {sql} ORDER BY i.created_at DESC, i.id DESC LIMIT ? OFFSET ?",
                         (*args, limit, offset))
        items = []
        for row in rows:
            st = ("accepted" if row["accepted_at"] else "declined" if row["declined_at"] else "revoked" if row["revoked_at"]
                  else "expired" if row["expires_at"] <= t else "pending")
            items.append({"id": row["id"], "email": row["email_key"], "role": row["role"], "state": st,
                          "created_at": row["created_at"], "expires_at": row["expires_at"], "invited_via": row["invited_via"]})
        return paged(items, total, offset, limit)

    return r
