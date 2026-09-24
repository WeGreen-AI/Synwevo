"""Platform administration.

The shape of this module is set by one rule from the permission contract: **platform
administration is not a superset of workspace permissions.** An administrator here governs
accounts, workspaces and application access. They do not gain the ability to read a document,
download a result, or act as someone else — there is no impersonation, no master token, and no
"reset this person's password to one I choose".

Every route checks permission on the server. Hiding a button is not access control, and a
backend whose only protection was a hidden button would be reachable by typing the URL.

The claim flow is the reason this exists now. The pre-identity data sits in workspace `default`
with zero members, deliberately unreachable, and the only way out of that state is an
administrator explicitly naming an existing account — with recent re-authentication, a written
reason, one transaction and an audit record.
"""
from __future__ import annotations

import json
from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, now

REAUTH_SCOPE = "admin"
UNFREEZE_RESTORE_NOTE = ("解冻不会写入安全日志（它只记录收回访问的操作）：如果之后从早于此刻的备份恢复，"
                         "冻结会被重新应用，需要再解冻一次。")


def build_router(store: Store, ident: Identity, sessions: Sessions, audit) -> APIRouter:
    r = APIRouter(prefix="/api/admin", tags=["admin"])

    def _admin(request: Request, *, dangerous: bool = False) -> dict:
        """The caller, if they are a platform administrator. Raises otherwise.

        Recent re-authentication is required for the dangerous operations only. Asking for a
        password on every page view trains people to type it without reading the page, which is
        the opposite of what a re-authentication prompt is for.
        """
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        user = ident.get_user(s["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        if user["platform_role"] != "admin":
            # Same shape as a missing page: an ordinary account learns nothing about what exists.
            raise ServiceError(404, "not_found", "页面不存在")
        # strict=True: signing in a moment ago is not the same as confirming *this*. Without
        # it, an administrator who had just logged in would never see the prompt at all.
        if dangerous and not Sessions.reauth_fresh(s, REAUTH_SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份",
                               {"scope": REAUTH_SCOPE})
        return user

    def _page(table: str, *, where: str, params: tuple, order: str,
              limit: int | None, offset: int, snapshot: int | None,
              columns: str = "*", join: str = "") -> dict:
        """Additive paging: old item shapes stay intact; counts follow the same filters.

        A high-water row id excludes later inserts across pages (including audit events
        generated while browsing). Ties in timestamps are always broken by a unique key.
        This is not a frozen copy of mutable account state: deletions/filter changes remain
        visible, and authorization is checked afresh on every request. All SQL fragments
        come from this module, never the caller. Omitted workspace limits keep the old full
        listing contract; new callers opt into bounded pages with limit=.
        """
        with store.tx():
            ceiling = store.one(f"SELECT COALESCE(MAX(rowid), 0) AS n FROM {table}")["n"]
            snapshot = ceiling if snapshot is None else min(snapshot, ceiling)
            source = f"{table} {join} WHERE {table}.rowid<=? AND ({where})"
            args = (snapshot, *params)
            total = store.one(f"SELECT COUNT(*) AS n FROM {source}", args)["n"]
            rows = store.all(f"SELECT {columns} FROM {source} ORDER BY {order} LIMIT ? OFFSET ?",
                             (*args, limit if limit is not None else -1, offset))
        return {"items": rows, "total": total, "limit": limit, "offset": offset,
                "snapshot": snapshot}

    # ------------------------------------------------------------------ overview
    @r.get("/overview")
    def overview(request: Request):
        _admin(request)
        legacy = store.one("SELECT * FROM workspaces WHERE kind='legacy' AND deleted_at IS NULL")
        out = {
            "users": store.one("SELECT COUNT(*) AS n FROM users WHERE deleted_at IS NULL")["n"],
            "suspended": store.one("SELECT COUNT(*) AS n FROM users WHERE status='suspended' "
                                   "AND deleted_at IS NULL")["n"],
            "workspaces": store.one("SELECT COUNT(*) AS n FROM workspaces WHERE deleted_at IS NULL")["n"],
            "admins": len(ident.platform_admins()),
            "unclaimed": None,
        }
        if legacy:
            out["unclaimed"] = _workspace_row(legacy)
        return out

    def _workspace_row(w: dict) -> dict:
        """A workspace as administration sees it: counts, never contents.

        `projects` is a number. Listing the project *names* here would hand the titles of every
        historical document to anyone who reaches this page — which is exactly the leak the
        unclaimed workspace exists to prevent.
        """
        wid = w["id"]
        return {
            "id": wid, "name": w["name"], "kind": w["kind"], "status": w["status"],
            "created_at": w["created_at"],
            "members": store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?",
                                 (wid,))["n"],
            "projects": store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0",
                                  (wid,))["n"],
            "apps": [row["app_id"] for row in store.all(
                "SELECT app_id FROM workspace_apps WHERE workspace_id=?", (wid,))],
        }

    # ------------------------------------------------------------------ workspaces
    @r.get("/workspaces")
    def list_workspaces(request: Request, q: str = Query("", max_length=100),
                        limit: int | None = None,
                        offset: int = Query(0, ge=0, le=2**63 - 1),
                        snapshot: int | None = Query(None, ge=0, le=2**63 - 1)):
        _admin(request)
        page = _page("workspaces", where="deleted_at IS NULL AND (?='' OR name LIKE ?)",
                     params=(q, f"%{q}%"), order="kind='legacy' DESC, created_at, id",
                     limit=None if limit is None else max(1, min(limit, 200)),
                     offset=offset, snapshot=snapshot)
        page["items"] = [_workspace_row(w) for w in page["items"]]
        return page

    @r.get("/workspaces/{wid}/members")
    def members(request: Request, wid: str):
        _admin(request)
        rows = store.all(
            "SELECT m.user_id, m.role, m.can_export, m.created_at, u.email, u.display_name, u.status "
            "FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY m.created_at",
            (wid,))
        return {"items": rows}

    @r.post("/workspaces/{wid}/claim")
    def claim(request: Request, wid: str, body: dict = Body(...)):
        """Hand an unclaimed workspace to an existing account.

        Deliberately narrow:

        * the target must be an account that already exists — this does not create one;
        * it acts on exactly this workspace and grants nothing anywhere else;
        * it does not make the target a platform administrator;
        * the reason is required and recorded, because in a year the audit line is the only
          thing that will explain why these files became someone's.

        The whole thing is one transaction. A half-applied claim would leave a workspace with a
        member but no application, or an owner who cannot export — states nobody would think to
        look for.
        """
        actor = _admin(request, dangerous=True)
        target_id = (body.get("user_id") or "").strip()
        reason = (body.get("reason") or "").strip()
        role = (body.get("role") or "owner").strip()
        if not target_id:
            raise ServiceError(400, "missing_user", "请选择要认领这些数据的账户")
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明认领理由（至少 8 个字），它会写入审计记录")
        if role not in ("owner", "admin", "editor", "viewer"):
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}")

        ws = ident.get_workspace(wid)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        if ws["kind"] != "legacy" or ws["status"] != "pending_claim":
            raise ServiceError(409, "not_claimable", "该工作空间不是待认领的历史数据空间")
        target = ident.get_user(target_id)
        if not target or target["status"] != "active":
            raise ServiceError(400, "bad_target", "目标账户不存在或不可用")

        n_projects = store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0",
                               (wid,))["n"]
        ts = now()
        with store.tx() as t:
            # Re-checked inside the transaction. Checked only before it, two concurrent claims
            # for different accounts both passed and the "unclaimed" data got two owners.
            live = t.execute("SELECT status FROM workspaces WHERE id=? AND kind='legacy'", (wid,)).fetchone()
            if not live or live["status"] != "pending_claim":
                raise ServiceError(409, "not_claimable", "该工作空间不是待认领的历史数据空间")
            if t.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?",
                         (wid, target_id)).fetchone():
                raise ServiceError(409, "already_member", "该账户已经是这个空间的成员了")
            t.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,"
                      "updated_at,created_by) VALUES (?,?,?,1,?,?,?)",
                      (wid, target_id, role, ts, ts, actor["id"]))
            # The generated label says "（待认领）", which stops being true the moment this
            # succeeds — and it is shown in the workspace switcher, so leaving it would have the
            # product telling people every day that their data is still unassigned. Only the
            # label we generated is replaced; a name an administrator has since chosen is kept.
            new_name = (body.get("name") or "").strip() or "历史数据"
            if "待认领" in (ws["name"] or ""):
                t.execute("UPDATE workspaces SET name=?, status='active', updated_at=? WHERE id=?",
                          (new_name[:100], ts, wid))
            else:
                t.execute("UPDATE workspaces SET status='active', updated_at=? WHERE id=?", (ts, wid))
            from synwevo_core.apps import DOCS_APP_ID
            t.execute("INSERT INTO workspace_apps (workspace_id,app_id,enabled_at,enabled_by) "
                      "VALUES (?,?,?,?) ON CONFLICT(workspace_id,app_id) DO NOTHING",
                      (wid, DOCS_APP_ID, ts, actor["id"]))
            t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
                      "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                      "revoked_at=NULL", (wid, DOCS_APP_ID, target_id, ts, actor["id"]))

        audit(request, "admin.workspace.claim", actor_id=actor["id"], workspace_id=wid,
              object_kind="workspace", object_id=wid, result="ok",
              meta={"target_user": target_id, "target_email": target["email"], "role": role,
                    "reason": reason[:500], "projects": n_projects})
        return {"ok": True, "workspace": _workspace_row(ident.get_workspace(wid)),
                "projects": n_projects}

    # ------------------------------------------------------------------ users
    @r.get("/users")
    def list_users(request: Request, q: str = Query("", max_length=100), limit: int = 50,
                   offset: int = Query(0, ge=0, le=2**63 - 1),
                   snapshot: int | None = Query(None, ge=0, le=2**63 - 1),
                   status: str = Query("", pattern="^(|pending|active|suspended|pending_delete|deleted)$"),
                   role: str = Query("", pattern="^(|none|operator|admin)$")):
        _admin(request)
        page = _page("users", columns="id,email,display_name,status,platform_role,created_at",
                     where="deleted_at IS NULL AND (?='' OR email LIKE ? OR display_name LIKE ?) "
                           "AND (?='' OR status=?) AND (?='' OR platform_role=?)",
                     params=(q, f"%{q}%", f"%{q}%", status, status, role, role),
                     order="created_at DESC, id DESC", limit=max(1, min(limit, 200)),
                     offset=offset, snapshot=snapshot)
        rows = page["items"]
        for u in rows:
            u["workspaces"] = store.one(
                "SELECT COUNT(*) AS n FROM memberships WHERE user_id=?", (u["id"],))["n"]
            u["sessions"] = store.one(
                "SELECT COUNT(*) AS n FROM sessions WHERE user_id=? AND revoked_at IS NULL "
                "AND idle_expires_at > ? AND absolute_expires_at > ?",
                (u["id"], now(), now()))["n"]
        return page

    @r.post("/users/{uid}/status")
    def set_status(request: Request, uid: str, body: dict = Body(...)):
        """Suspend or restore an account. Suspension takes effect on the next request, not at
        session expiry, and also ends the sessions and platform tokens that already exist.

        Tokens go too: they used to be left alone, so an account suspended *because* it was
        compromised got the attacker's refresh and delegated API tokens back the moment an
        administrator restored it. A platform administrator or operator is refused with
        `demote_first` (GitHub Enterprise's rule): demotion is the transactional step that
        protects the last administrator, and suspending admins directly let two of them suspend
        each other at the same moment and leave the platform with none.
        """
        actor = _admin(request, dangerous=True)
        status = (body.get("status") or "").strip()
        reason = (body.get("reason") or "").strip()
        if status not in ("active", "suspended"):
            raise ServiceError(400, "bad_status", "状态只能是 active 或 suspended")
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        target = ident.get_user(uid)
        if not target:
            raise ServiceError(404, "no_user", "找不到该账户")
        revoked = tokens = 0
        journaled = None
        if status == "suspended":
            if uid == actor["id"]:
                raise ServiceError(400, "self_suspend", "不能停用自己的账户")
            with store.tx() as t:
                row = t.execute("SELECT platform_role FROM users WHERE id=?", (uid,)).fetchone()
                if row["platform_role"] in ("admin", "operator"):
                    raise ServiceError(409, "demote_first",
                                       "请先把该账户的平台角色降为 none，再停用它")
                t.execute("UPDATE users SET status='suspended', updated_at=? WHERE id=?", (now(), uid))
            revoked = sessions.revoke_all_for_user(uid, "admin_suspended")
            from synwevo_core.oidc import Grants
            tokens = Grants(store).revoke_for_user(uid, "admin_suspended")
            from .withdrawals import record as journal
            journaled = journal(store, "user_suspended", user_id=uid)
        else:
            ident.set_status(uid, status)
        audit(request, "admin.user.status", actor_id=actor["id"], object_kind="user",
              object_id=uid, result="ok",
              meta={"status": status, "reason": reason[:500], "sessions_revoked": revoked,
                    "tokens_revoked": tokens, "journaled": journaled})
        return {"ok": True, "status": status, "sessions_revoked": revoked, "tokens_revoked": tokens,
                "journaled": journaled}

    @r.post("/users/{uid}/revoke-sessions")
    def revoke_sessions(request: Request, uid: str, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        reason = (body.get("reason") or "").strip()
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        if not ident.get_user(uid):
            raise ServiceError(404, "no_user", "找不到该账户")
        n = sessions.revoke_all_for_user(uid, "admin_revoked")
        # Tokens issued from those sessions go too; otherwise an application keeps working with
        # an access token minted by a session that no longer exists.
        from synwevo_core.oidc import Grants
        tokens = Grants(store).revoke_for_user(uid, "admin_revoked")
        from .withdrawals import record as journal
        journaled = journal(store, "sessions_revoked", user_id=uid)
        audit(request, "admin.user.revoke_sessions", actor_id=actor["id"], object_kind="user",
              object_id=uid, result="ok",
              meta={"reason": reason[:500], "sessions": n, "tokens": tokens, "journaled": journaled})
        return {"ok": True, "sessions_revoked": n, "tokens_revoked": tokens, "journaled": journaled}

    # ------------------------------------------------------------------ freezing a workspace
    def _freeze(request: Request, wid: str, body: dict, frozen: bool) -> dict:
        actor = _admin(request, dangerous=True)
        reason = (body.get("reason") or "").strip()
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        out = ident.set_workspace_frozen(wid, frozen)
        journaled = None
        extra = {}
        if frozen:
            from .withdrawals import record as journal
            journaled = journal(store, "workspace_frozen", workspace_id=wid)
        else:
            # The journal records withdrawals only. Said out loud, because the consequence is
            # otherwise a surprise: a restore from a backup taken before this moment re-applies
            # the freeze, and the workspace has to be unfrozen again.
            extra["restore_note"] = UNFREEZE_RESTORE_NOTE
        audit(request, "admin.workspace.freeze" if frozen else "admin.workspace.unfreeze",
              actor_id=actor["id"], workspace_id=wid, object_kind="workspace", object_id=wid,
              result="ok", meta={**out, "reason": reason[:500], "journaled": journaled})
        return {"ok": True, **out, "workspace": _workspace_row(ident.get_workspace(wid)),
                "journaled": journaled, **extra}

    @r.post("/workspaces/{wid}/freeze")
    def freeze(request: Request, wid: str, body: dict = Body(...)):
        """Stop everything in a workspace: people, delegations, service accounts, API keys.

        `{"reason"}` with recent `admin` re-authentication. What it gives the administrator is a
        stop switch, not a view: the response and the audit record carry counts, never content.
        """
        return _freeze(request, wid, body, True)

    @r.post("/workspaces/{wid}/unfreeze")
    def unfreeze(request: Request, wid: str, body: dict = Body(...)):
        return _freeze(request, wid, body, False)

    @r.post("/users/{uid}/platform-role")
    def set_platform_role(request: Request, uid: str, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        role = (body.get("role") or "").strip()
        reason = (body.get("reason") or "").strip()
        # The schema's own vocabulary, not an invented one: none / operator / admin.
        if role not in ("none", "operator", "admin"):
            raise ServiceError(400, "bad_role", "平台角色只能是 none、operator 或 admin")
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        if not ident.get_user(uid):
            raise ServiceError(404, "no_user", "找不到该账户")
        # set_platform_role already refuses to remove the last administrator; this is the
        # friendlier message for the case it would raise on.
        if role != "admin" and uid == actor["id"] and len(ident.platform_admins()) <= 1:
            raise ServiceError(409, "last_admin", "你是最后一个平台管理员，降级后没有人能管理平台")
        ident.set_platform_role(uid, role)
        audit(request, "admin.user.platform_role", actor_id=actor["id"], object_kind="user",
              object_id=uid, result="ok", meta={"role": role, "reason": reason[:500]})
        return {"ok": True, "role": role}

    # ------------------------------------------------------------------ audit
    @r.get("/audit")
    def audit_log(request: Request, q: str = Query("", max_length=100),
                  action: str = Query("", max_length=64), limit: int = 100,
                  offset: int = Query(0, ge=0, le=2**63 - 1),
                  snapshot: int | None = Query(None, ge=0, le=2**63 - 1)):
        """Read the security log.

        Read-only, and read-only by construction: there is no route here that edits or deletes an
        audit row. This is a single-node SQLite database, so that is tamper-*evidence* through
        the application, not tamper-proofing — anyone with the file can rewrite it, and saying
        otherwise would be a false assurance.
        """
        _admin(request)
        page = _page("audit_events", columns="audit_events.*, u.email AS actor_email",
                     join="LEFT JOIN users u ON u.id=audit_events.actor_id",
                     where="(?='' OR audit_events.action LIKE ?) "
                           "AND (?='' OR u.email LIKE ? OR audit_events.object_id LIKE ?)",
                     params=(action, f"{action}%", q, f"%{q}%", f"%{q}%"),
                     order="audit_events.at DESC, audit_events.seq DESC",
                     limit=max(1, min(limit, 500)), offset=offset, snapshot=snapshot)
        rows = page["items"]
        for row in rows:
            try:
                row["meta"] = json.loads(row["meta"] or "{}")
            except ValueError:
                row["meta"] = {}
        return page

    return r
