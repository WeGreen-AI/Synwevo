"""The workspace directory API (API-5): who belongs to a workspace, in which role, and who has been
invited — for a service account the workspace's owner approved (`workspace.read`).

The gate (`synwevo_core.http`, prefix /api/workspace/v1) has already verified the caller with
`MachineAuth.verify_workspace`: a platform service account bound to this workspace, a token for
the directory resource, a live owner approval. Nothing here reads a workspace id from the request —
the principal's workspace is the only one there is. Responses carry directory metadata (addresses,
names, roles, dates) and nothing a member made.

Read-only in this version. Inviting, changing roles and removing members by machine need an
explicit application actor in the member services (an invitation mail names who invited), which is
the next step (API-5b); until then those stay in the account pages.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.store import Store, now

PREFIX = "/api/workspace/v1"
READ = "workspace.read"
INVITATION_STATES = ("pending", "accepted", "declined", "revoked", "expired")


def build_router(store: Store, ident: Identity) -> APIRouter:
    r = APIRouter(tags=["workspace-directory"])

    def caller(request: Request, scope: str = READ) -> dict:
        p = getattr(request.state, "principal", None)
        if not p or p.get("kind") != "service_account" or not p.get("tenant"):
            raise ServiceError(401, "unauthorized", "缺少工作空间目录凭据")
        if scope not in (p.get("scopes") or ()):
            raise ServiceError(403, "forbidden", f"该凭据没有 {scope} 权限", {"required": scope})
        return p

    def paged(items: list, total: int, offset: int, limit: int) -> dict:
        nxt = offset + len(items)
        return {"items": items, "total": total, "offset": offset, "limit": limit, "next_offset": nxt if nxt < total else None}

    @r.get(PREFIX + "/whoami", summary="当前凭据与它所属的工作空间")
    def whoami(request: Request):
        p = caller(request)
        ws = ident.get_workspace(p["tenant"]) or {}
        return {"workspace": {"id": p["tenant"], "name": ws.get("name"), "kind": ws.get("kind")},
                "client_id": p["client_id"], "name": p.get("name"), "scopes": list(p["scopes"])}

    @r.get(PREFIX + "/members", summary="工作空间成员（地址、名称、角色、加入时间；分页）")
    def members(request: Request, role: str = Query("", pattern="^(|owner|admin|editor|viewer)$"),
                q: str = Query("", max_length=100), limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0, le=2**31)):
        p = caller(request)
        out = ident.list_members(p["tenant"], q=q, role=role or None, offset=offset, limit=limit)
        items = [{"user_id": m["user_id"], "email": m["email"], "display_name": m["display_name"], "role": m["role"],
                  "account_status": m["account_status"], "joined_at": m["joined_at"]} for m in out["items"]]
        return {**paged(items, out["total"], offset, limit), "owner_count": out["owner_count"]}

    @r.get(PREFIX + "/invitations", summary="工作空间邀请及其状态（分页，最新在前）")
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
                         f"i.declined_at FROM invitations i WHERE {sql} ORDER BY i.created_at DESC, i.id DESC LIMIT ? OFFSET ?",
                         (*args, limit, offset))
        items = []
        for row in rows:
            st = ("accepted" if row["accepted_at"] else "declined" if row["declined_at"] else "revoked" if row["revoked_at"]
                  else "expired" if row["expires_at"] <= t else "pending")
            items.append({"id": row["id"], "email": row["email_key"], "role": row["role"], "state": st,
                          "created_at": row["created_at"], "expires_at": row["expires_at"]})
        return paged(items, total, offset, limit)

    return r
