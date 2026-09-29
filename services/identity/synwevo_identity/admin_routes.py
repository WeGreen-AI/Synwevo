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
reason, one transaction and an audit record. Its reverse (`unclaim`) is held to the same rules:
an assignment made by mistake is undone here, not by an operator editing the database.
"""
from __future__ import annotations

import json
import re
from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, REGISTRATION_MODES, registration_mode
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import admin_capable, Sessions
from synwevo_core.store import LEGACY_WORKSPACE_NAME, Store, now

REAUTH_SCOPE = "admin"
UNFREEZE_RESTORE_NOTE = ("解冻不会写入安全日志（它只记录收回访问的操作）：如果之后从早于此刻的备份恢复，"
                         "冻结会被重新应用，需要再解冻一次。")
#: What a claim renames the generated 「历史数据（待认领）」 label to when no name is given — and
#: the overview never gives one.
LEGACY_CLAIMED_NAME = "历史数据"
UNCLAIM_TOKEN_REASON = "workspace_unclaimed"


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, mailer=None) -> APIRouter:
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
        if not admin_capable(s.get("amr") or ""):
            # The role is real; this session proved one factor (a mailed code, an external
            # provider). Platform administration waits for a second one — the password on the
            # account page, or a second-factor sign-in.
            raise ServiceError(403, "step_up_required", "当前登录只验证了一个因素：请在账户页输入口令确认身份后再使用平台管理",
                               {"scope": "admin"})
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

    def _reason(body: dict) -> str:
        reason = body.get("reason")
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        return reason.strip()

    # ------------------------------------------------------------------ registration policy
    def _policy():
        row = store.one("SELECT value FROM meta WHERE key='registration.mode'")
        return {"mode": registration_mode(store),
                "source": "database" if row and row["value"] in REGISTRATION_MODES else "environment"}

    @r.get("/registration")
    def registration_policy(request: Request):
        _admin(request)
        return _policy()

    @r.post("/registration")
    def set_registration_policy(request: Request, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        reason = _reason(body)
        mode = body.get("mode")
        if mode not in REGISTRATION_MODES:
            raise ServiceError(400, "bad_registration_mode", "请选择有效的注册策略")
        with store.tx():
            before = registration_mode(store)
            if body.get("expected_mode", before) != before:
                raise ServiceError(409, "policy_changed", "注册策略已更改，请刷新后重试")
            store.execute("INSERT INTO meta(key,value) VALUES ('registration.mode',?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))
            audit(request, "admin.registration.update", actor_id=actor["id"],
                  object_kind="setting", object_id="registration.mode", result="ok",
                  meta={"before": before, "mode": mode, "reason": reason})
        return _policy()

    # ------------------------------------------------------------------ delivery metadata / recovery
    def _mail(request):
        # The application already owns the mail worker. Optional injection keeps older
        # composers working without creating a second worker or another persistence owner.
        sender = mailer if mailer is not None else getattr(request.app.state, "mailer", None)
        if sender is None:
            raise ServiceError(503, "mail_not_configured", "邮件服务尚未配置")
        return sender

    def _failure_code(value):
        # Historical transport errors may contain credentials or the full message. Never
        # return arbitrary provider strings, even to a platform administrator.
        safe = {"transport_unavailable", "tls_certificate_invalid", "tls_failed",
                "smtp_authentication_failed", "smtp_security_not_supported", "mail_delivery_failed",
                "delivery_unknown", "delivery_in_progress", "superseded", "mail_not_configured",
                "mail_config_invalid", "public_url_missing"}
        if value in safe or (isinstance(value, str) and re.fullmatch(
                r"smtp_(?:(?:sender|recipient|data)_)?rejected_[45][0-9]{2}", value)):
            return value
        return "mail_delivery_failed" if value else None

    def _mail_row(row):
        code = _failure_code(row.get("last_error"))
        retryable = (row["status"] == "failed" and row["kind"] in ("reset", "verify")
                     and bool(code) and code == row.get("last_error")
                     and code not in ("delivery_unknown", "delivery_in_progress", "superseded"))
        return {k: row.get(k) for k in ("id", "to_addr", "kind", "ref_id", "status", "attempts",
                                     "created_at", "sent_at", "next_attempt_at")} | {
            "error_code": code, "retryable": retryable}

    @r.get("/mail")
    def outbox(request: Request, q: str = Query("", max_length=100),
               status: str = Query("", pattern="^(|queued|sent|failed)$"),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63-1),
               snapshot: int | None = Query(None, ge=0, le=2**63-1)):
        _admin(request)
        from .mail import mode
        page = _page("mail_outbox", columns="id,to_addr,kind,ref_id,status,attempts,created_at,"
                     "sent_at,next_attempt_at,last_error",
                     where="(?='' OR to_addr LIKE ? OR id LIKE ?) AND (?='' OR status=?)",
                     params=(q, f"%{q}%", f"%{q}%", status, status),
                     order="created_at DESC,id DESC", limit=limit, offset=offset, snapshot=snapshot)
        page["items"] = [_mail_row(row) for row in page["items"]]
        page["delivery_mode"] = mode()
        return page

    def _dispatch(request, uid, kind):
        from .invites import Invites, RESET_TTL_S, VERIFY_TTL_S
        from .mail import reset_mail, verify_mail
        user = ident.get_user(uid)
        if not user or user["status"] not in ("active", "pending") or user.get("deleted_at"):
            raise ServiceError(409, "bad_target", "目标账户不存在或不可用")
        if kind == "verify" and user.get("email_verified_at"):
            raise ServiceError(409, "already_verified", "该邮箱已经验证")
        recent = store.one("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM email_tokens "
                           "WHERE user_id=? AND kind=? AND created_at>?", (uid, kind, now()-3600))
        if recent["n"] >= 3 or (recent["last"] and recent["last"] > now()-60):
            retry_after = max(1, (3600 if recent["n"] >= 3 else 60) - (now()-recent["last"]))
            raise ServiceError(429, "mail_rate_limited", "发送过于频繁，请稍后重试",
                               {"retry_after": retry_after})
        ttl = (RESET_TTL_S if kind == "reset" else VERIFY_TTL_S) // 3600
        token = Invites(store, ident).issue_email_token(uid, kind, ttl_s=ttl*3600,
                                                       new_email=user["email"] if kind == "verify" else None)
        subject, body, html = (reset_mail if kind == "reset" else verify_mail)(
            token=token, expires_h=ttl, locale=user.get("locale") or "zh-Hans")
        return _mail_row(_mail(request).enqueue(user["email"], subject, body, kind=kind, ref_id=uid, html=html))

    @r.post("/users/{uid}/recovery")
    def dispatch_recovery(request: Request, uid: str, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        reason = _reason(body)
        with store.tx():
            delivery = _dispatch(request, uid, "reset")
            audit(request, "admin.user.recovery", actor_id=actor["id"], object_kind="user",
                  object_id=uid, result="ok", meta={"reason": reason, "mail_id": delivery["id"]})
        return {"ok": True, "delivery": delivery}

    @r.post("/mail/{mid}/retry")
    def retry_mail(request: Request, mid: str, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        reason = _reason(body)
        with store.tx():
            row = store.one("SELECT id,to_addr,kind,ref_id,status,attempts,created_at,sent_at,"
                            "next_attempt_at,last_error FROM mail_outbox WHERE id=?", (mid,))
            if not row:
                raise ServiceError(404, "no_mail", "找不到该邮件")
            if not _mail_row(row)["retryable"]:
                raise ServiceError(409, "mail_not_retryable", "该邮件不能安全重试")
            # Terminal bodies are deliberately redacted by the worker. Regenerate a fresh
            # token for the live account's current address; never replay a stale stored body.
            delivery = _dispatch(request, row["ref_id"], row["kind"])
            store.execute("UPDATE mail_outbox SET last_error='superseded' WHERE id=?", (mid,))
            audit(request, "admin.mail.retry", actor_id=actor["id"], object_kind="mail",
                  object_id=mid, result="ok", meta={"reason": reason, "replacement_id": delivery["id"]})
        return {"ok": True, "delivery": delivery}

    # ------------------------------------------------------------------ organizations
    @r.get("/organizations")
    def list_organizations(request: Request, q: str = Query("", max_length=100),
                           status: str = Query("", pattern="^(|active|frozen|deleted)$"),
                           limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63-1),
                           snapshot: int | None = Query(None, ge=0, le=2**63-1)):
        actor = _admin(request)
        page = _page("organizations", columns="id,name,status,created_at,recover_until",
                     where="(?='' OR name LIKE ? OR id LIKE ?) AND (?='' OR status=?)",
                     params=(q, f"%{q}%", f"%{q}%", status, status), order="created_at DESC,id DESC",
                     limit=limit, offset=offset, snapshot=snapshot)
        mine = {o["id"]: o["role"] for o in Organizations(store, ident).list_for_user(actor["id"])}
        for org in page["items"]:
            org["role"] = mine.get(org["id"])
            org["members"] = store.one("SELECT COUNT(*) AS n FROM organization_memberships "
                                       "WHERE organization_id=?", (org["id"],))["n"]
            org["workspaces"] = store.one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=? "
                                          "AND deleted_at IS NULL", (org["id"],))["n"]
        return page

    @r.post("/organizations/{oid}/{action}")
    def govern_organization(request: Request, oid: str, action: str, body: dict = Body(...)):
        actor = _admin(request, dangerous=True)
        reason = _reason(body)
        # Use the core lifecycle and the real actor. Platform governance does not add an
        # organization membership, impersonate its owner, or bypass its ownership checks.
        return Organizations(store, ident, lambda event, **kw: audit(request, event, **kw)).transition(
            oid, actor["id"], action, reason=reason)

    # ------------------------------------------------------------------ overview
    @r.get("/identity-providers")
    def identity_providers(request: Request, probe: int = Query(0, ge=0, le=1)):
        """Masked configuration state of the external sign-in providers (see
        ExternalIdentities.status). Reading it is ordinary administration; it discloses
        nothing an administrator could not read from the deployment's own environment file,
        and it never returns the client secret."""
        _admin(request)
        external = getattr(request.app.state, "external", None)
        return {"items": external.status(probe=bool(probe)) if external is not None else [],
                "enabled": external is not None}

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
        look for. The audit record is part of it and names the workspace's name before and after:
        undoing the claim (`unclaim`) reads it to tell the label this claim set from a name an
        administrator chose later, and a record written after the commit could be missing at the
        moment a concurrent undo looks for it.
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
            live = t.execute("SELECT status, name FROM workspaces WHERE id=? AND kind='legacy'",
                             (wid,)).fetchone()
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
            # Read here, inside the transaction: an undo that just put the label back must not
            # be judged by the name as it was before it.
            name_before = live["name"] or ""
            name_after = name_before
            if "待认领" in name_before:
                name_after = ((body.get("name") or "").strip() or LEGACY_CLAIMED_NAME)[:100]
                t.execute("UPDATE workspaces SET name=?, status='active', updated_at=? WHERE id=?",
                          (name_after, ts, wid))
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
                        "reason": reason[:500], "projects": n_projects,
                        "name_before": name_before, "name_after": name_after})
        return {"ok": True, "workspace": _workspace_row(ident.get_workspace(wid)),
                "projects": n_projects}

    # ------------------------------------------------------------------ undoing a claim
    def _assigned_legacy(wid: str) -> dict:
        """The legacy workspace as it is now, if its assignment can be undone; refuses otherwise.

        Called before the write transaction (so what cannot be undone is refused before anyone is
        asked for a password) and again inside it, where `store.one` joins the transaction: a
        claim, an undo or a freeze that commits in between is judged by the row as it is then,
        never by the one read a moment earlier. Two undos at once: one commits, the other finds
        `pending_claim` and is told there is nothing to undo.
        """
        w = store.one("SELECT id, name, kind, status, organization_id, deleted_at FROM workspaces "
                      "WHERE id=?", (wid,))
        if not w:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        if w["kind"] != "legacy":
            raise ServiceError(409, "not_legacy", "只有历史数据空间可以撤销指派，这个工作空间不是")
        if w["deleted_at"] or w["status"] == "deleted":
            raise ServiceError(409, "workspace_deleted", "这个工作空间已被删除，不能撤销指派")
        if w["organization_id"]:
            raise ServiceError(409, "organization_workspace", "这个工作空间属于一个组织，不能在平台管理中撤销指派")
        if w["status"] == "frozen":
            raise ServiceError(409, "workspace_frozen", "这个工作空间已被冻结：请先解冻，再撤销指派")
        if w["status"] != "active":
            raise ServiceError(409, "not_assigned", "这份历史数据目前没有指派给任何人，无需撤销")
        return w

    def _assignment(wid: str) -> dict | None:
        """The audited claim behind the current assignment, or None.

        None when the latest claim-or-undo record of this workspace is not a claim — the
        workspace was then made active some other way (an operator's hand), and there is no
        record of when or by whom to show.
        """
        row = store.one(
            "SELECT a.action, a.at, a.actor_id, a.meta, u.email AS actor_email, "
            "u.display_name AS actor_name FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id "
            "WHERE a.workspace_id=? AND a.object_id=? AND a.result='ok' "
            "AND a.action IN ('admin.workspace.claim','admin.workspace.unclaim') "
            "ORDER BY a.seq DESC LIMIT 1", (wid, wid))
        if not row or row["action"] != "admin.workspace.claim":
            return None
        try:
            meta = json.loads(row["meta"] or "{}")
        except ValueError:
            meta = {}
        meta = meta if isinstance(meta, dict) else {}
        return {"at": row["at"], "by_id": row["actor_id"], "by_email": row["actor_email"],
                "by_name": row["actor_name"], "to_id": meta.get("target_user"),
                "to_email": meta.get("target_email"), "role": meta.get("role"),
                "reason": meta.get("reason"), "meta": meta}

    def _name_after_unclaim(current: str, assignment: dict | None) -> str:
        """The generated label comes back only if the name is still exactly the one the claim set.

        A name an administrator chose since — or chose before the claim, which the claim then kept
        — stays. Claims recorded before the names were audited could only have set their default
        (the overview never sends a name), so that is what is compared for them.
        """
        meta = (assignment or {}).get("meta") or {}
        if isinstance(meta.get("name_after"), str) and isinstance(meta.get("name_before"), str):
            if meta["name_before"] != meta["name_after"] and current == meta["name_after"]:
                return meta["name_before"] if "待认领" in meta["name_before"] else LEGACY_WORKSPACE_NAME
            return current
        return LEGACY_WORKSPACE_NAME if current == LEGACY_CLAIMED_NAME else current

    def _unclaim_plan(w: dict) -> dict:
        """Everything undoing this assignment would change, read as it is now.

        Counts, never contents: the names of the projects in here are exactly what nobody may
        read until the data is assigned again (see `_workspace_row`). The people are named —
        they are the ones who lose access, and the administrator confirms that list.
        """
        wid, ts = w["id"], now()
        members = store.all(
            "SELECT m.user_id, m.role, u.email, u.display_name FROM memberships m "
            "LEFT JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? "
            "ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END, "
            "m.created_at, m.user_id", (wid,))
        assignment = _assignment(wid)
        name_after = _name_after_unclaim(w["name"], assignment)
        approvals = 0
        if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_platform_approvals'"):
            approvals = store.one("SELECT COUNT(*) AS n FROM api_platform_approvals WHERE workspace_id=? "
                                  "AND status='approved' AND revoked_at IS NULL AND expires_at>?",
                                  (wid, ts))["n"]
        return {
            "workspace": {"id": wid, "name": w["name"], "status": w["status"]},
            "name_after": name_after, "name_restored": name_after != w["name"],
            "members": [{"user_id": m["user_id"], "email": m["email"], "display_name": m["display_name"],
                         "role": m["role"]} for m in members],
            # the rows the undo withdraws: not yet accepted, declined or withdrawn
            "invitations": store.one("SELECT COUNT(*) AS n FROM invitations WHERE workspace_id=? "
                                     "AND accepted_at IS NULL AND revoked_at IS NULL", (wid,))["n"],
            # sign-in tokens someone could still use (every unrevoked row is revoked regardless)
            "tokens": store.one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE workspace_id=? AND revoked_at IS NULL "
                                "AND (access_expires_at IS NULL OR access_expires_at>? OR refresh_expires_at>?)",
                                (wid, ts, ts))["n"],
            "service_accounts": store.one("SELECT COUNT(*) AS n FROM oauth_clients WHERE kind='service' "
                                          "AND workspace_id=? AND disabled_at IS NULL", (wid,))["n"],
            "approvals": approvals,
            "projects": store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0", (wid,))["n"],
            "templates": store.one("SELECT COUNT(*) AS n FROM templates WHERE tenant=? AND deleted=0", (wid,))["n"],
            "assigned": None if assignment is None else {
                k: assignment[k] for k in ("at", "by_id", "by_email", "by_name", "to_id", "to_email", "role", "reason")},
        }

    @r.get("/workspaces/{wid}/unclaim-preview")
    def unclaim_preview(request: Request, wid: str):
        """What undoing the assignment would do, without doing it: who loses access, how many
        invitations and sign-in tokens are withdrawn, what the data amounts to (numbers only),
        the name it gets back, and when and by whom it was assigned. Reads only; looking needs
        no re-authentication — the undo itself does."""
        _admin(request)
        return _unclaim_plan(_assigned_legacy(wid))

    def _notify_removed(request: Request, actor: dict, uid: str, workspace_name: str, wid: str) -> bool:
        """The inbox item a member gets when they are removed from a workspace (`member.removed`,
        worded in their language), best effort as in `members._notify`: access is already gone
        whether or not the row lands."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None:
            return False
        try:
            notifier.emit(uid, "member.removed",
                          params={"workspace": workspace_name, "actor": actor.get("display_name") or actor["email"]},
                          workspace_id=wid, object_kind="membership", object_id=uid,
                          target={"origin": "accounts", "path": "/account#workspaces"})
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[synwevo] notification not recorded (member.removed): {type(e).__name__}", flush=True)
            return False

    @r.post("/workspaces/{wid}/unclaim")
    def unclaim(request: Request, wid: str, body: dict = Body(...)):
        """Undo `claim`: the historical data goes back to belonging to nobody.

        `{"reason", "expected_members"?}` with recent `admin` re-authentication — the same gate
        as the claim; a workspace or organization role never opens it. One transaction:

        * **every** membership goes, not only the account the claim named — anyone invited since
          would otherwise keep data the assignment was never meant to give them;
        * their application entitlements here are revoked the way removing a member revokes them,
          with an explicit revoked row per person (under the `members` policy "no row" means
          "allowed"), and any other live row in the workspace too;
        * the workspace's applications are turned off: an unassigned workspace has none;
        * invitations nobody has accepted are withdrawn;
        * every platform token acting in it — people's and service accounts' — is revoked
          (`workspace_unclaimed`), and unexchanged sign-in codes for it are spent;
        * status `pending_claim`; the generated label comes back only over the name the claim set.

        Machine credentials follow the rules already in force, nothing new: a service account
        needs an active workspace with the application on (`Apps.check_access_for_workspace`), and
        an API approval counts only while its approver is a current owner
        (`Approvals.require_owner`) — both stop here without being deleted, exactly as when a
        workspace is frozen or loses its owners. Operator-issued `dw_` keys follow the documented
        `pending_claim` rule (`MachineAuth._legacy_key`), as before the claim.

        Sessions are left alone: membership is checked on every request, and a session whose
        workspace the person no longer belongs to is cleared then (`http._session_gate`).

        Nothing is deleted: projects, templates, sources, jobs, results and files stay, readable
        by nobody — platform administrators included — until the data is assigned again.

        `expected_members` (the user ids the confirmation showed) makes the undo refuse, rather
        than remove someone the administrator never saw, when the members changed after they
        looked. The removals are journalled after the commit like every other withdrawal, so a
        restore from an older backup does not bring the access back.
        """
        _admin(request)
        _assigned_legacy(wid)
        actor = _admin(request, dangerous=True)
        reason = _reason(body)
        expected = body.get("expected_members")
        if expected is not None and (not isinstance(expected, list) or len(expected) > 10_000
                                     or not all(isinstance(x, str) for x in expected)):
            raise ServiceError(400, "bad_expected_members", "expected_members 必须是账户 ID 列表")
        from synwevo_core.apps import DOCS_APP_ID
        from synwevo_core.oidc import Grants
        from .withdrawals import record as journal
        ts = now()
        with store.tx() as t:
            w = _assigned_legacy(wid)
            plan = _unclaim_plan(w)
            members = plan["members"]
            if expected is not None and set(expected) != {m["user_id"] for m in members}:
                raise ServiceError(409, "assignment_changed",
                                   "你确认之后，这个空间的成员发生了变化。请重新查看将失去访问的人，再确认撤销")
            apps_off = [r["app_id"] for r in store.all(
                "SELECT app_id FROM workspace_apps WHERE workspace_id=? ORDER BY app_id", (wid,))]
            entitlements = t.execute("UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? "
                                     "AND revoked_at IS NULL", (ts, wid)).rowcount
            for m in members:
                for app_id in sorted(set(apps_off) | {DOCS_APP_ID}):
                    t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,"
                              "revoked_at) VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) "
                              "DO UPDATE SET revoked_at=COALESCE(app_entitlements.revoked_at, excluded.revoked_at)",
                              (wid, app_id, m["user_id"], ts, actor["id"], ts))
            t.execute("DELETE FROM memberships WHERE workspace_id=?", (wid,))
            t.execute("DELETE FROM workspace_apps WHERE workspace_id=?", (wid,))
            invitation_ids = [r["id"] for r in store.all(
                "SELECT id FROM invitations WHERE workspace_id=? AND accepted_at IS NULL AND revoked_at IS NULL "
                "ORDER BY created_at, id", (wid,))]
            if invitation_ids:
                t.execute(f"UPDATE invitations SET revoked_at=? WHERE id IN ({','.join('?' * len(invitation_ids))})",
                          (ts, *invitation_ids))
            tokens = Grants(store).revoke_for_workspace(wid, UNCLAIM_TOKEN_REASON)
            codes = t.execute("UPDATE oauth_codes SET used_at=? WHERE workspace_id=? AND used_at IS NULL",
                              (ts, wid)).rowcount
            t.execute("UPDATE workspaces SET status='pending_claim', name=?, updated_at=? WHERE id=?",
                      (plan["name_after"], ts, wid))
            removed = [{"user_id": m["user_id"], "email": m["email"], "role": m["role"]} for m in members]
            outcome = {"removed_members": removed, "invitations_revoked": len(invitation_ids),
                       "tokens_revoked": tokens, "codes_spent": codes, "entitlements_revoked": entitlements,
                       "apps_disabled": apps_off, "previous_name": w["name"], "name": plan["name_after"],
                       "previous_status": w["status"], "status": "pending_claim",
                       "service_accounts": plan["service_accounts"], "approvals": plan["approvals"],
                       "projects": plan["projects"], "templates": plan["templates"]}
            audit(request, "admin.workspace.unclaim", actor_id=actor["id"], workspace_id=wid,
                  object_kind="workspace", object_id=wid, result="ok",
                  meta={**outcome, "reason": reason,
                        "assigned_at": (plan["assigned"] or {}).get("at"),
                        "assigned_by": (plan["assigned"] or {}).get("by_id")})

        journaled = True
        for m in removed:
            journaled = journal(store, "member_removed", workspace_id=wid, user_id=m["user_id"]) and journaled
        for iid in invitation_ids:
            journaled = journal(store, "invitation_revoked", invitation_id=iid) and journaled
        for app_id in apps_off:
            journaled = journal(store, "workspace_app_disabled", workspace_id=wid, app_id=app_id) and journaled
        notified = sum(_notify_removed(request, actor, m["user_id"], w["name"], wid)
                       for m in removed if m["user_id"] != actor["id"])
        return {"ok": True, **outcome, "journaled": journaled, "notified": notified,
                "workspace": _workspace_row(ident.get_workspace(wid))}

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
