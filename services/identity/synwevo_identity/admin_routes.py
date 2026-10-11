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
from contextlib import contextmanager
import re
from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, REGISTRATION_MODES, registration_mode
from synwevo_core.platform_access import (ASSIGN, ASSIGN_SUPER, PlatformAccess, ROLES, SUPER_ADMIN, catalogue, clock,
                                          require_outranking, require_platform, sorted_permissions, sorted_roles)
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import admin_capable, Sessions
from synwevo_core.store import LEGACY_WORKSPACE_NAME, Store, now

REAUTH_SCOPE = "admin"
#: `_admin` called without naming a permission. A route merged from a branch written before the
#: permissions existed fails loudly (500, fail-closed) instead of guessing one. Historical-data
#: routes (claim, and unclaim when it lands) take "platform.workspaces.claim"; see
#: synwevo_core.platform_access.PERMISSIONS for the rest.
_UNNAMED = object()
UNFREEZE_RESTORE_NOTE = ("解冻不会写入安全日志（它只记录收回访问的操作）：如果之后从早于此刻的备份恢复，"
                         "冻结会被重新应用，需要再解冻一次。")
#: What a claim renames the generated 「历史数据（待认领）」 label to when no name is given — and
#: the overview never gives one.
LEGACY_CLAIMED_NAME = "历史数据"
UNCLAIM_TOKEN_REASON = "workspace_unclaimed"
#: The account detail's security timeline: the newest events about one account (A13).
TIMELINE_LIMIT = 50


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, mailer=None) -> APIRouter:
    r = APIRouter(prefix="/api/admin", tags=["admin"])
    from .admin_notification_recovery import build_router as build_notification_recovery_router
    r.include_router(build_notification_recovery_router(store, audit))

    def _admin(request: Request, permission=_UNNAMED, *, dangerous: bool = False,
               step_up: bool = True) -> dict:
        """The caller, if a platform grant gives them `permission`. Raises otherwise.

        Every route names the permission it needs (synwevo_core.platform_access): no platform
        permission at all is the 404 of a missing page, another platform permission is 403
        `platform_permission_denied` (audited), then the session step-up and, for the dangerous
        operations, the strict re-authentication. Asking for a password on every page view
        trains people to type it without reading the page, which is the opposite of what a
        re-authentication prompt is for.
        """
        if permission is _UNNAMED:
            raise RuntimeError(f"{request.method} {request.url.path}: an /api/admin route must name the platform "
                               "permission it needs (platform_access.PERMISSIONS); the historical-data routes "
                               "use 'platform.workspaces.claim'")
        return require_platform(request, permission, store=store, audit=audit,
                                dangerous=dangerous, step_up=step_up)

    def _not_outranked(request: Request, actor: dict, target_id: str | None) -> None:
        """A lower administrator does not act on a higher one (platform_access.require_outranking)."""
        if target_id:
            require_outranking(request, actor, target_id, store=store, audit=audit)

    @contextmanager
    def _write(request, permission, *, object_kind="platform_permission", object_id=None):
        """Revalidate under the writer lock; retain denials after rollback."""
        admitted = _admin(request, permission, dangerous=True)
        try:
            with store.tx():
                yield _admin(request, permission, dangerous=True)
        except ServiceError as exc:
            from synwevo_core.platform_access import audit_denied
            if exc.status < 500:
                audit_denied(store, audit, request, admitted["id"], object_id or permission,
                             object_kind=object_kind, code=exc.code)
            raise

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
        _admin(request, "platform.config.view")
        return _policy()

    @r.post("/registration")
    def set_registration_policy(request: Request, body: dict = Body(...)):
        actor = _admin(request, "platform.config.manage", dangerous=True)
        reason = _reason(body)
        mode = body.get("mode")
        if mode not in REGISTRATION_MODES:
            raise ServiceError(400, "bad_registration_mode", "请选择有效的注册策略")
        with _write(request, "platform.config.manage", object_kind="setting", object_id="registration.mode"):
            actor = _admin(request, "platform.config.manage", dangerous=True)
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
        _admin(request, "platform.mail.view")
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
        actor = _admin(request, "platform.users.support", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        with _write(request, "platform.users.support", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.support", dangerous=True)
            _not_outranked(request, actor, uid)
            delivery = _dispatch(request, uid, "reset")
            audit(request, "admin.user.recovery", actor_id=actor["id"], object_kind="user",
                  object_id=uid, result="ok", meta={"reason": reason, "mail_id": delivery["id"]})
        return {"ok": True, "delivery": delivery}

    @r.post("/mail/{mid}/retry")
    def retry_mail(request: Request, mid: str, body: dict = Body(...)):
        actor = _admin(request, "platform.mail.manage", dangerous=True)
        reason = _reason(body)
        # Reset and verify mail (the only retryable kinds) name the account in ref_id, which never
        # changes; ranked before the write transaction so the denied attempt's record is kept.
        named = store.one("SELECT ref_id FROM mail_outbox WHERE id=?", (mid,))
        _not_outranked(request, actor, named and named["ref_id"])
        with _write(request, "platform.mail.manage", object_kind="mail", object_id=mid):
            actor = _admin(request, "platform.mail.manage", dangerous=True)
            _not_outranked(request, actor, named and named["ref_id"])
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
        actor = _admin(request, "platform.orgs.view")
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
        actor = _admin(request, "platform.orgs.manage", dangerous=True)
        reason = _reason(body)
        # Use the core lifecycle and the real actor. Platform governance does not add an
        # organization membership, impersonate its owner, or bypass its ownership checks.
        with _write(request, "platform.orgs.manage", object_kind="organization", object_id=oid) as actor:
            return Organizations(store, ident, lambda event, **kw: audit(request, event, **kw)).transition(
                oid, actor["id"], action, reason=reason)

    # ------------------------------------------------------------------ overview
    @r.get("/identity-providers")
    def identity_providers(request: Request, probe: int = Query(0, ge=0, le=1)):
        """Masked configuration state of the external sign-in providers (see
        ExternalIdentities.status). Reading it is ordinary administration; it discloses
        nothing an administrator could not read from the deployment's own environment file,
        and it never returns the client secret."""
        _admin(request, "platform.config.view")
        external = getattr(request.app.state, "external", None)
        return {"items": external.status(probe=bool(probe)) if external is not None else [],
                "enabled": external is not None}

    @r.get("/overview")
    def overview(request: Request):
        _admin(request, "platform.overview.view")
        legacy = store.one("SELECT * FROM workspaces WHERE kind='legacy' AND deleted_at IS NULL")
        out = {
            "users": store.one("SELECT COUNT(*) AS n FROM users WHERE deleted_at IS NULL")["n"],
            "suspended": store.one("SELECT COUNT(*) AS n FROM users WHERE status='suspended' "
                                   "AND deleted_at IS NULL")["n"],
            "workspaces": store.one("SELECT COUNT(*) AS n FROM workspaces WHERE deleted_at IS NULL")["n"],
            # Accounts whose platform grants count, and among them the usable super
            # administrators (active, verified, able to complete the step-up).
            "admins": _platform_holders(),
            "super_admins": len(ident.platform_admins()),
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
        _admin(request, "platform.workspaces.view")
        page = _page("workspaces", where="deleted_at IS NULL AND (?='' OR name LIKE ?)",
                     params=(q, f"%{q}%"), order="kind='legacy' DESC, created_at, id",
                     limit=None if limit is None else max(1, min(limit, 200)),
                     offset=offset, snapshot=snapshot)
        page["items"] = [_workspace_row(w) for w in page["items"]]
        return page

    @r.get("/workspaces/{wid}/members")
    def members(request: Request, wid: str):
        _admin(request, "platform.workspaces.view")
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
        actor = _admin(request, "platform.workspaces.claim", dangerous=True)
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
        with (
            _write(request, "platform.workspaces.claim", object_kind="workspace", object_id=wid) as actor,
            store.tx() as t,
        ):
            # A target suspended or deleted while this request waited cannot receive data.
            target = ident.get_user(target_id)
            if not target or target["status"] != "active":
                raise ServiceError(400, "bad_target", "目标账户不存在或不可用")
            # Taken under the write lock, not before waiting for it: an undo's security-journal
            # entry is replayed "as of" its own time (journal.UNCLAIM_KIND), and a claim that
            # queued behind that undo must not carry a time from before it.
            ts = now()
            # Re-checked inside the transaction. Checked only before it, two concurrent claims
            # for different accounts both passed and the "unclaimed" data got two owners.
            live = t.execute("SELECT status, name FROM workspaces WHERE id=? AND kind='legacy'",
                             (wid,)).fetchone()
            if not live or live["status"] != "pending_claim":
                raise ServiceError(409, "not_claimable", "该工作空间不是待认领的历史数据空间")
            if t.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?",
                         (wid, target_id)).fetchone():
                raise ServiceError(409, "already_member", "该账户已经是这个空间的成员了")
            # A platform role is never the way to a document: not for the administrator acting,
            # and not for anyone else holding a platform grant (platform_access.PERMISSIONS,
            # `platform.workspaces.claim`). Checked here, with the write.
            if target_id == actor["id"]:
                raise ServiceError(409, "self_claim",
                                   "不能把历史数据指派给自己：平台管理权限不带来任何文档的访问。请指派给数据实际所有者的普通账户")
            if PlatformAccess(store).live_grants(target_id):
                raise ServiceError(409, "target_is_platform_admin",
                                   "该账户持有平台权限，不能接收历史数据：平台管理与业务数据须由不同账户持有。请指派给没有平台权限的账户")
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
            # A row left by an earlier assignment (an undo writes an explicit revoked row per
            # former member) is granted anew: when and by whom is this claim's, as `Apps.grant`
            # records it — not the undo's time and the administrator who ran the undo.
            t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
                      "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                      "revoked_at=NULL, granted_at=excluded.granted_at, granted_by=excluded.granted_by",
                      (wid, DOCS_APP_ID, target_id, ts, actor["id"]))
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

        A frozen assignment is still an assignment and is undone as it is. Freezing is the stop
        switch an administrator reaches for first when they see a wrong assignment; asking them to
        unfreeze before undoing handed the data straight back to the wrong account for as long as
        the two steps took. The undo ends the freeze with the assignment — the data goes to
        `pending_claim`, which nobody can open either.
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
        if w["status"] not in ("active", "frozen"):
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

    def _table(name: str) -> bool:
        return bool(store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    def _operator_keys(wid: str, ts: float) -> int:
        """Operator-issued `dw_` keys of this tenant that no owner's approval binds. The undo does
        not stop them: the documented `pending_claim` rule (`MachineAuth._legacy_key`) keeps them
        working with the historical data, as before the claim. Counted per lineage — a rotation's
        grace period has two keys of one lineage alive."""
        unbound = ("AND NOT EXISTS (SELECT 1 FROM api_platform_bindings b WHERE b.kind='api_key' "
                   "AND b.credential_id=COALESCE(k.lineage, k.id))") if _table("api_platform_bindings") else ""
        return store.one("SELECT COUNT(DISTINCT COALESCE(k.lineage, k.id)) AS n FROM api_keys k "
                         "WHERE k.tenant=? AND k.revoked_at IS NULL AND (k.expires_at IS NULL OR k.expires_at>?) "
                         + unbound, (wid, ts))["n"]

    def _jobs_stopping(wid: str) -> int:
        """Queued or running generation jobs here that stop once nobody holds the data.

        Work is re-authorised against its submitter (`synwevo_core.authority.check`): a person's,
        a delegation's and a service account's need a member or an active workspace, and an API
        key bound to an owner's approval needs that owner — the undo takes all of these away, and
        the runner cancels what is left of such a job (`authorization_revoked`; finished results
        stay, a retry is needed after a new assignment). Work of an operator's `dw_` key or of the
        pre-identity operator modes keeps running, as before the claim."""
        from synwevo_core import authority
        bindings = _table("api_platform_bindings")
        n = 0
        for row in store.all("SELECT j.principal AS job_p, r.principal AS run_p FROM jobs j LEFT JOIN job_runs r "
                             "ON r.job_id=j.id AND r.run_no=COALESCE(j.run_no, 1) "
                             "WHERE j.tenant=? AND j.status IN ('queued','running')", (wid,)):
            desc = authority.loads(row["run_p"] if row["run_p"] not in (None, "") else row["job_p"]) or {}
            kind = desc.get("kind")
            if kind in ("user", "delegated", "service_account"):
                n += 1
            elif kind == "api_key" and bindings and store.one(
                    "SELECT 1 FROM api_platform_bindings WHERE kind='api_key' AND credential_id=?",
                    (desc.get("lineage") or desc.get("key_id"),)):
                n += 1
        return n

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
        if _table("api_platform_approvals"):
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
            # what the undo does NOT stop, said before it is confirmed rather than discovered after
            "operator_keys": _operator_keys(wid, ts),
            "jobs_stopping": _jobs_stopping(wid),
            "projects": store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0", (wid,))["n"],
            "templates": store.one("SELECT COUNT(*) AS n FROM templates WHERE tenant=? AND deleted=0", (wid,))["n"],
            "assigned": None if assignment is None else {
                k: assignment[k] for k in ("at", "by_id", "by_email", "by_name", "to_id", "to_email", "role", "reason")},
        }

    @r.get("/workspaces/{wid}/unclaim-preview")
    def unclaim_preview(request: Request, wid: str):
        """What undoing the assignment would do, without doing it: who loses access, how many
        invitations and sign-in tokens are withdrawn, what keeps working (operator keys) and what
        stops (jobs), what the data amounts to (numbers only), the name it gets back, and when and
        by whom it was assigned. Reads only; looking needs no re-authentication — the undo
        itself does. It names the people in the workspace, so it is the claim's own permission,
        not a view one."""
        _admin(request, "platform.workspaces.claim")
        return _unclaim_plan(_assigned_legacy(wid))

    def _notify_removed(request: Request, uid: str, workspace_name: str, wid: str) -> bool:
        """The inbox item a member gets when they are removed from a workspace (`member.removed`,
        worded in their language), best effort as in `members._notify`: access is already gone
        whether or not the row lands.

        The actor is the role, not the person (`actor_kind`): a platform administrator's email or
        name has never been handed to workspace members, and the account the data was assigned
        to by mistake may be a stranger."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None:
            return False
        try:
            notifier.emit(uid, "member.removed",
                          params={"workspace": workspace_name, "actor_kind": "platform_admin"},
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
        as the claim; a workspace or organization role never opens it. An assignment that has
        been frozen is undone as it is (see `_assigned_legacy`). One transaction:

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

        The security journal gets one `workspace_unclaimed` entry **before** any of it, inside the
        transaction (write-ahead, like the organization withdrawals): a restore from an older
        backup then re-applies the undo whether the process stopped after the commit or the
        backup was taken before it, and the entry's own time keeps it from touching a later
        assignment. When the journal cannot be written the undo is not performed at all
        (`journal_unavailable`, nothing changed): an undo a restore would silently reverse is not
        reported as done. Freezing remains available as the immediate stop.

        Machine credentials follow the rules already in force, nothing new: a service account
        needs an active workspace with the application on (`Apps.check_access_for_workspace`), and
        an API approval counts only while its approver is a current owner
        (`Approvals.require_owner`) — both stop here without being deleted, exactly as when a
        workspace is frozen or loses its owners, and an approval counts again if the data is
        assigned back to the owner who gave it. Operator-issued `dw_` keys follow the documented
        `pending_claim` rule (`MachineAuth._legacy_key`) and keep working, as before the claim;
        the preview counts them (`operator_keys`), and the queued work that stops (`jobs_stopping`).

        Sessions are left alone: membership is checked on every request, and a session whose
        workspace the person no longer belongs to is cleared then (`http._session_gate`). A tab
        that still names this workspace in `X-Synwevo-Workspace` is refused
        (`workspace_context_invalid`) until it chooses again — the overview drops that context
        itself when its own tab named the workspace.

        Nothing is deleted: projects, templates, sources, jobs, results and files stay, and no
        account can open them — platform administrators included — until the data is assigned
        again.

        `expected_members` (the user ids the confirmation showed) makes the undo refuse, rather
        than remove someone the administrator never saw, when the members changed after they
        looked.
        """
        _admin(request, "platform.workspaces.claim")
        _assigned_legacy(wid)
        actor = _admin(request, "platform.workspaces.claim", dangerous=True)
        reason = _reason(body)
        expected = body.get("expected_members")
        if expected is not None and (not isinstance(expected, list) or len(expected) > 10_000
                                     or not all(isinstance(x, str) for x in expected)):
            raise ServiceError(400, "bad_expected_members", "expected_members 必须是账户 ID 列表")
        from synwevo_core import journal as security_journal
        from synwevo_core.apps import DOCS_APP_ID
        from synwevo_core.oidc import Grants
        journal_error: Exception | None = None
        try:
            with (
                _write(request, "platform.workspaces.claim", object_kind="workspace", object_id=wid) as actor,
                store.tx() as t,
            ):
                ts = now()
                w = _assigned_legacy(wid)
                plan = _unclaim_plan(w)
                members = plan["members"]
                if expected is not None and set(expected) != {m["user_id"] for m in members}:
                    raise ServiceError(409, "assignment_changed",
                                       "你确认之后，这个空间的成员发生了变化。请重新查看将失去访问的人，再确认撤销")
                # Every refusal is decided above; from here on the undo happens. The journal line
                # goes first, so there is no moment at which the undo is committed and not journalled.
                try:
                    security_journal.record(store, security_journal.UNCLAIM_KIND, workspace_id=wid,
                                            label_restored=bool(plan["name_restored"]))
                except (OSError, ValueError) as e:
                    journal_error = e
                    raise ServiceError(503, "journal_unavailable",
                                       "安全日志暂时无法写入，撤销指派没有执行，什么都没有改变。请检查安全日志；"
                                       "需要立即停止访问时，可以先冻结这个空间") from e
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
                           "operator_keys": plan["operator_keys"], "jobs_stopping": plan["jobs_stopping"],
                           "projects": plan["projects"], "templates": plan["templates"]}
                audit(request, "admin.workspace.unclaim", actor_id=actor["id"], workspace_id=wid,
                      object_kind="workspace", object_id=wid, result="ok",
                      meta={**outcome, "reason": reason, "journaled": True,
                            "assigned_at": (plan["assigned"] or {}).get("at"),
                            "assigned_by": (plan["assigned"] or {}).get("by_id")})
        except ServiceError:
            if journal_error is not None:
                print(f"[synwevo] security journal write failed for {security_journal.UNCLAIM_KIND}: "
                      f"{type(journal_error).__name__}: {journal_error}", flush=True)
                audit(request, "admin.workspace.unclaim", actor_id=actor["id"], workspace_id=wid,
                      object_kind="workspace", object_id=wid, result="error",
                      meta={"code": "journal_unavailable", "reason": reason})
            raise

        notified = sum(_notify_removed(request, m["user_id"], w["name"], wid)
                       for m in removed if m["user_id"] != actor["id"])
        return {"ok": True, **outcome, "journaled": True, "notified": notified,
                "workspace": _workspace_row(ident.get_workspace(wid))}

    # ------------------------------------------------------------------ users
    @r.get("/users")
    def list_users(request: Request, q: str = Query("", max_length=100), limit: int = 50,
                   offset: int = Query(0, ge=0, le=2**63 - 1),
                   snapshot: int | None = Query(None, ge=0, le=2**63 - 1),
                   status: str = Query("", pattern="^(|pending|active|suspended|pending_delete|deleted)$"),
                   role: str = Query("", pattern="^(|none|any|admin|super_admin|access_admin|support|auditor|api_platform_admin|finance)$")):
        """`role`: '' every account, `none` no live platform grant, `any` at least one, a built-in
        role id its holders, `admin` the super administrators by the rollback mirror (older links).
        The retired `operator` is not a filter any more (422)."""
        actor = _admin(request, "platform.users.view")
        ts = clock()
        live = ("SELECT 1 FROM platform_role_grants g WHERE g.user_id=users.id AND g.revoked_at IS NULL "
                "AND (g.expires_at IS NULL OR g.expires_at>?)")
        page = _page("users", columns="id,email,display_name,status,platform_role,created_at",
                     where="deleted_at IS NULL AND (?='' OR email LIKE ? OR display_name LIKE ?) "
                           f"AND (?='' OR status=?) AND (?='' OR (?='none' AND NOT EXISTS ({live})) "
                           f"OR (?='any' AND EXISTS ({live})) OR (?='admin' AND platform_role='admin') "
                           f"OR EXISTS ({live} AND g.role=?))",
                     params=(q, f"%{q}%", f"%{q}%", status, status, role, role, ts, role, ts, role, ts, role),
                     order="created_at DESC, id DESC", limit=max(1, min(limit, 200)),
                     offset=offset, snapshot=snapshot)
        rows = page["items"]
        access = PlatformAccess(store)
        held = actor["platform_permissions"]
        for u in rows:
            u["platform_roles"] = sorted_roles({g["role"] for g in access.live_grants(u["id"], at=ts)})
            # the page greys out what the server would refuse with target_outranks_actor
            u["outranks_caller"] = u["id"] != actor["id"] and bool(access.outranking(held, u["id"]))
            u["workspaces"] = store.one(
                "SELECT COUNT(*) AS n FROM memberships WHERE user_id=?", (u["id"],))["n"]
            u["sessions"] = store.one(
                "SELECT COUNT(*) AS n FROM sessions WHERE user_id=? AND revoked_at IS NULL "
                "AND idle_expires_at > ? AND absolute_expires_at > ?",
                (u["id"], now(), now()))["n"]
        return page

    @r.get("/users/{uid}")
    def account_detail(request: Request, uid: str):
        """One account, as metadata (admin coverage A13): profile and verification state, the ways
        it can sign in, its live platform roles, workspace and organization memberships, active
        sessions, the sign-in backoff it is under and — for a caller who may read the platform
        audit log — its recent security events.

        Never a password hash, TOTP seed, recovery code, token, session cookie or document: the
        facts the account's own settings page shows its owner, read with `platform.users.view`.
        Workspace and organization names follow the caller's permission to view those lists, so
        this page names nothing the lists would not. The timeline is the audit log's slice about
        this account and needs `platform.audit.view`, as the log does. Reading changes nothing
        and, like the lists, is not audited.
        """
        actor = _admin(request, "platform.users.view")
        user = ident.get_user(uid)
        if not user:
            raise ServiceError(404, "no_user", "找不到该账户")
        from .external import ExternalIdentities, load_providers
        from .mfa import Mfa
        held = actor["platform_permissions"]
        ts = clock()
        access = PlatformAccess(store)
        blocked = access.blocked_by(user)
        grants = access.live_grants(uid, at=ts)
        factors = Mfa(store).factors(uid)
        passkeys = store.all("SELECT id,name,created_at,last_used_at,backed_up,uv_capable FROM passkeys "
                             "WHERE user_id=? AND deleted_at IS NULL ORDER BY created_at, id", (uid,))
        external = ExternalIdentities(store, load_providers()).list_for_user(uid)
        see_workspaces, see_orgs = "platform.workspaces.view" in held, "platform.orgs.view" in held
        workspaces = store.all(
            "SELECT m.workspace_id AS id, m.role, m.can_export, m.created_at AS joined_at, w.name, w.kind, w.status "
            "FROM memberships m JOIN workspaces w ON w.id=m.workspace_id "
            "WHERE m.user_id=? AND w.deleted_at IS NULL ORDER BY m.created_at, m.workspace_id", (uid,))
        organizations = store.all(
            "SELECT o.id, o.name, o.status, m.role, m.created_at AS joined_at FROM organization_memberships m "
            "JOIN organizations o ON o.id=m.organization_id WHERE m.user_id=? AND o.deleted_at IS NULL "
            "ORDER BY m.created_at, o.id", (uid,))
        timeline = None
        if "platform.audit.view" in held:
            timeline = store.all(
                "SELECT audit_events.id, audit_events.at, audit_events.action, audit_events.actor_kind, "
                "u.email AS actor_email, audit_events.object_kind, audit_events.object_id, audit_events.result, "
                "audit_events.meta FROM audit_events LEFT JOIN users u ON u.id=audit_events.actor_id "
                "WHERE audit_events.actor_id=? OR (audit_events.object_kind='user' AND audit_events.object_id=?) "
                "ORDER BY audit_events.at DESC, audit_events.seq DESC LIMIT ?", (uid, uid, TIMELINE_LIMIT))
            for row in timeline:
                try:
                    meta = json.loads(row.pop("meta") or "{}")
                except ValueError:
                    meta = {}
                reason = meta.get("reason")
                row["reason"] = reason if isinstance(reason, str) else None
        return {
            "user": {k: user[k] for k in ("id", "email", "display_name", "status", "platform_role",
                                          "email_verified_at", "created_at", "updated_at")},
            "application_identity": "application_identity" in blocked,
            "blocked_by": blocked,
            "outranks_caller": user["id"] != actor["id"] and bool(access.outranking(held, uid)),
            "sign_in": {
                "password_set": bool(store.one("SELECT 1 AS n FROM user_passwords WHERE user_id=?", (uid,))),
                "totp": bool(factors["totp"]), "recovery_remaining": factors["recovery_remaining"],
                "passkeys": [{**k, "backed_up": bool(k["backed_up"]), "uv_capable": bool(k["uv_capable"])} for k in passkeys],
                "external_identities": [{k: row[k] for k in ("id", "provider", "label", "available", "email",
                                                             "email_verified", "created_at", "last_used_at")}
                                        for row in external]},
            "platform_roles": sorted_roles({g["role"] for g in grants}),
            "platform_grants": [{"role": g["role"], "granted_at": g["granted_at"], "expires_at": g["expires_at"]}
                                for g in grants],
            "workspaces": [{**w, "can_export": bool(w["can_export"]), "name": w["name"] if see_workspaces else None}
                           for w in workspaces],
            "organizations": [{**o, "name": o["name"] if see_orgs else None} for o in organizations],
            "sessions": [{k: s[k] for k in ("id", "created_at", "last_seen_at", "expires_at", "amr", "client",
                                            "client_parts", "ip", "persistent", "auth_provider")}
                         for s in sessions.list_for_user(uid)],
            "backoff": ident.login_backoff(uid),
            "audit": {"permitted": timeline is not None, "limit": TIMELINE_LIMIT, "items": timeline or []},
        }

    @r.post("/users/{uid}/status-preview")
    def preview_status(request: Request, uid: str, body: dict = Body(...)):
        from .admin_operations import AccountOperations
        return AccountOperations(store, audit).preview(request, uid, body.get("status"))

    @r.post("/users/{uid}/status")
    def set_status(request: Request, uid: str, body: dict = Body(...)):
        from .admin_operations import AccountOperations
        return AccountOperations(store, audit).execute(request, uid, body)

    @r.post("/users/{uid}/revoke-sessions")
    def revoke_sessions(request: Request, uid: str, body: dict = Body(...)):
        actor = _admin(request, "platform.users.support", dangerous=True)
        reason = _reason(body)
        from synwevo_core import journal
        from synwevo_core.oidc import Grants
        from synwevo_core.store import new_id
        from .profile import invalidate_access
        with _write(request, "platform.users.support", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.support", dangerous=True)
            if not ident.get_user(uid):
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            n = sessions.revoke_all_for_user(uid, "admin_revoked")
            tokens = Grants(store).revoke_for_user(uid, "admin_revoked")
            invalidate_access(store, uid, "admin_revoked")
            audit(request, "admin.user.revoke_sessions", actor_id=actor["id"], object_kind="user",
                  object_id=uid, workspace_given=True, result="ok",
                  meta={"reason": reason, "sessions": n, "tokens": tokens, "journaled": True})
            try:
                journal.record_account(store, "sessions_revoked", user_id=uid, event_id=new_id("ao"))
            except (OSError, ValueError):
                raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
        return {"ok": True, "sessions_revoked": n, "tokens_revoked": tokens, "journaled": True}

    def _security_notice(request: Request, uid: str, what: str) -> bool:
        """The inbox item the person gets about a sign-in method or device the platform acted on
        (`security.change`, in their language), best effort: the change holds whether or not the
        row lands."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None:
            return False
        try:
            notifier.security_change(uid, what)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[synwevo] notification not recorded (security.change): {type(e).__name__}", flush=True)
            return False

    @r.post("/users/{uid}/sessions/{sid}/revoke")
    def revoke_session(request: Request, uid: str, sid: str, body: dict = Body(...)):
        """End one device's sign-in and what it minted (admin coverage A17): `{"reason"}` with
        strict re-authentication; the other devices stay signed in. The person is told as for a
        device they signed out themselves. Unlike the whole-account revocation this is not
        journalled: a restore from an older backup could revive this one device until its own
        expiry, as it could a device the person signed out."""
        actor = _admin(request, "platform.users.manage", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        with _write(request, "platform.users.manage", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.manage", dangerous=True)
            if not ident.get_user(uid):
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            ts = now()
            if not store.one("SELECT 1 AS n FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL "
                             "AND idle_expires_at>? AND absolute_expires_at>?", (sid, uid, ts, ts)):
                raise ServiceError(404, "no_session", "找不到该账户的这个在线会话")
            out = sessions.revoke_device(sid, "admin_revoked")
            audit(request, "admin.user.revoke_session", actor_id=actor["id"], object_kind="user",
                  object_id=uid, workspace_given=True, result="ok",
                  meta={"reason": reason, "sessions": out["sessions_revoked"], "tokens": out["tokens_revoked"]})
        notified = _security_notice(request, uid, "sessions_revoked")
        return {"ok": True, **out, "notified": notified}

    @r.post("/users/{uid}/passkeys/{pid}/remove")
    def remove_passkey(request: Request, uid: str, pid: str, body: dict = Body(...)):
        """Remove one of the account's passkeys (A17): `{"reason"}` with strict re-authentication.
        The person's own rules hold — never their only way in (`last_factor`), never the last
        usable super administrator's identity confirmation — so an administrator who means to
        lock an account out suspends it instead."""
        from .passkeys import Passkeys
        actor = _admin(request, "platform.users.manage", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        with _write(request, "platform.users.manage", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.manage", dangerous=True)
            if not ident.get_user(uid):
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            Passkeys(store).remove(uid, pid)
            audit(request, "admin.user.passkey_remove", actor_id=actor["id"], object_kind="user",
                  object_id=uid, workspace_given=True, result="ok", meta={"reason": reason, "passkey_id": pid})
        notified = _security_notice(request, uid, "passkey_removed")
        return {"ok": True, "notified": notified}

    @r.post("/users/{uid}/external-identities/{iid}/unlink")
    def unlink_external_identity(request: Request, uid: str, iid: str, body: dict = Body(...)):
        """Detach one external identity from the account (A17): `{"reason"}` with strict
        re-authentication; refused when it is the account's only way in (`last_sign_in_method`),
        as the person's own unlink is. The provider's next sign-in no longer reaches this account."""
        from .external import ExternalIdentities, load_providers, other_sign_in_methods
        actor = _admin(request, "platform.users.manage", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        with _write(request, "platform.users.manage", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.manage", dangerous=True)
            if not ident.get_user(uid):
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            row = store.one("SELECT id,provider FROM external_identities WHERE id=? AND user_id=?", (iid, uid))
            if not row:
                raise ServiceError(404, "no_external_identity", "找不到该账户的这个外部身份")
            if other_sign_in_methods(store, uid, except_identity=iid) == 0:
                raise ServiceError(409, "last_sign_in_method", "这是该账户唯一的登录方式，不能解除关联；需要停止访问请停用账户")
            store.execute("DELETE FROM external_identities WHERE id=? AND user_id=?", (iid, uid))
            audit(request, "admin.user.external_unlink", actor_id=actor["id"], object_kind="user",
                  object_id=uid, workspace_given=True, result="ok", meta={"reason": reason, "provider": row["provider"]})
        providers = ExternalIdentities(store, load_providers()).providers
        label = providers[row["provider"]].label if row["provider"] in providers else row["provider"]
        notified = _security_notice(request, uid, f"解除了外部登录方式（{label}）")
        return {"ok": True, "notified": notified}

    # ------------------------------------------------------------------ freezing a workspace
    def _freeze(request: Request, wid: str, body: dict, frozen: bool) -> dict:
        actor = _admin(request, "platform.workspaces.manage", dangerous=True)
        reason = (body.get("reason") or "").strip()
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        with _write(request, "platform.workspaces.manage", object_kind="workspace", object_id=wid):
            actor = _admin(request, "platform.workspaces.manage", dangerous=True)
            out = ident.set_workspace_frozen(wid, frozen)
            journaled = True if frozen else None
            extra = {} if frozen else {"restore_note": UNFREEZE_RESTORE_NOTE}
            audit(request, "admin.workspace.freeze" if frozen else "admin.workspace.unfreeze",
                  actor_id=actor["id"], workspace_id=wid, object_kind="workspace", object_id=wid,
                  result="ok", meta={**out, "reason": reason[:500], "journaled": journaled})
            if frozen:
                from synwevo_core import journal
                try:
                    journal.record(store, "workspace_frozen", workspace_id=wid)
                except (OSError, ValueError):
                    raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
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
        """Compatibility with older clients, through the grant service and all its rules:
        `admin` now requires the dedicated recipient acceptance flow; `none` revokes every
        live grant atomically. `operator` is refused — it never carried a capability (D17)."""
        actor = _admin(request, ASSIGN, dangerous=True)
        role = (body.get("role") or "").strip()
        if role == "operator":
            raise ServiceError(400, "operator_retired",
                               "「平台运维」角色已停用：它从未带有任何权限。请改为授予一个内置平台角色")
        if role not in ("none", "admin"):
            raise ServiceError(400, "bad_role", "平台角色只能是 none 或 admin")
        reason = _reason(body)
        with _write(request, "platform.roles.assign", object_kind="user", object_id=uid):
            actor = _admin(request, ASSIGN, dangerous=True)
            access = _access(request)
            if role == "admin":
                access._require_actor(actor["id"], SUPER_ADMIN)
                raise ServiceError(409, "super_admin_acceptance_required", "最高管理员须由本人通过安全密钥接受提议")
            else:
                access.revoke_all(actor["id"], uid, reason)
            return {"ok": True, "role": role, "platform_roles": access.roles(uid)}

    # ------------------------------------------------------------------ platform roles
    def _access(request: Request) -> PlatformAccess:
        """The grant service, auditing as this request (same transaction as each change)."""
        return PlatformAccess(store, audit=lambda action, **kw: audit(request, action, **kw))

    def _platform_holders() -> int:
        access = PlatformAccess(store)
        return sum(1 for row in store.all(
            "SELECT DISTINCT user_id FROM platform_role_grants WHERE revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at>?)", (clock(),)) if access.counted_grants(row["user_id"]))

    def _last_sign_in(uid: str) -> float | None:
        # Central sign-ins only (a relying-party session is a hand-off of one); the rows are
        # swept a week after they end, so this reads "within the retained sessions".
        row = store.one("SELECT MAX(created_at) AS at FROM sessions WHERE user_id=? "
                        "AND oidc_sid IS NULL AND central_session_id IS NULL", (uid,))
        return row["at"] if row else None

    def _assign_refusal(held: frozenset, actor_id: str, uid: str, role: str) -> str | None:
        """Why the caller could not grant or revoke `role` on this account — the code the service
        would refuse with (PlatformAccess._require_actor, the self rule) — or None."""
        needed = {ASSIGN} | ({ASSIGN_SUPER} if role == SUPER_ADMIN else set())
        if not needed <= held:
            return "platform_permission_denied"
        if not ROLES[role] <= held:
            return "role_exceeds_own"
        if uid == actor_id and ROLES[role] & {ASSIGN, ASSIGN_SUPER}:
            return "self_assignment"
        return None

    @r.get("/session")
    def admin_session(request: Request):
        """What the caller may do here and why: effective permissions with the grants they come
        from, whether this session still needs the step-up, how long the strict re-authentication
        lasts, and which instance this is (the `instance_kind` that /healthz reports). Any platform
        permission reads it, a locked session included — it is how the page learns what is
        missing."""
        from synwevo_core.sessions import REAUTH_SECONDS
        user = _admin(request, None, step_up=False)
        s = request.state.session
        explained = PlatformAccess(store).explain(user["id"])
        fresh = s.get("reauth_scope") == REAUTH_SCOPE and s.get("reauth_at")
        left = max(0, int(REAUTH_SECONDS - (now() - s["reauth_at"]))) if fresh else 0
        return {"user": {"id": user["id"], "email": user["email"], "display_name": user.get("display_name")},
                "roles": explained["roles"], "permissions": explained["permissions"],
                "step_up": {"required": not admin_capable(s.get("amr") or ""), "amr": s.get("amr") or ""},
                "reauth": {"scope": REAUTH_SCOPE, "seconds_left": left, "window_s": REAUTH_SECONDS},
                "environment": {"instance": store.instance_kind()},
                "super_admins": len(ident.platform_admins())}

    @r.get("/platform/roles")
    def platform_roles(request: Request):
        """The catalogue (built-in roles and their permissions) and the roster: every account
        holding a live grant, its grants, whether it can complete the administrator step-up and
        the strict re-authentication, and its last sign-in."""
        _admin(request, "platform.roles.view")
        access = PlatformAccess(store)
        ids = [row["user_id"] for row in store.all(
            "SELECT user_id, MIN(granted_at) AS first FROM platform_role_grants WHERE revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at>?) GROUP BY user_id ORDER BY first, user_id", (clock(),))]
        roster = []
        for uid in ids:
            explained = access.explain(uid)
            roster.append({"user": {"id": uid, "email": explained["email"], "status": explained["status"],
                                    "email_verified": explained["email_verified"],
                                    "display_name": (ident.get_user(uid) or {}).get("display_name")},
                           "roles": explained["roles"], "grants": explained["grants"],
                           "blocked_by": explained["blocked_by"], "step_up": explained["step_up"],
                           "super_admin_usable": explained["super_admin_usable"],
                           "last_sign_in_at": _last_sign_in(uid)})
        return {**catalogue(), "roster": roster, "super_admins": len(ident.platform_admins()),
                "recommended_super_admins": 2}

    @r.get("/users/{uid}/platform-access")
    def platform_access(request: Request, uid: str):
        """Why this account may or may not do what: each permission with its source grants, every
        live grant and whether it counts, and — for the caller — which roles they could grant it
        and which grants they could revoke (the server re-checks both when asked)."""
        actor = _admin(request, "platform.roles.view")
        explained = PlatformAccess(store).explain(uid)
        held = actor["platform_permissions"]
        live = {g["role"] for g in explained["grants"]}
        open_roles = [r for r in sorted_roles(ROLES) if r not in live and r != SUPER_ADMIN]
        refusals = {r: _assign_refusal(held, actor["id"], uid, r) for r in open_roles}
        explained["assignable_roles"] = [r for r in open_roles if refusals[r] is None]
        # When nothing can be granted, the reason the page shows (the first role's; for a caller
        # without assign it is the same for every role).
        explained["assign_refusal"] = None if explained["assignable_roles"] or not open_roles \
            else refusals[open_roles[0]]
        for g in explained["grants"]:
            g["revoke_refusal"] = _assign_refusal(held, actor["id"], uid, g["role"])
            g["revocable"] = g["revoke_refusal"] is None
        from .admin_handover import offer_eligibility
        refusal = offer_eligibility(store, actor["id"], uid)
        explained["can_offer_super_admin"] = refusal is None
        explained["offer_refusal"] = refusal
        explained["caller_permissions"] = sorted_permissions(held)
        explained["catalogue"] = catalogue()["roles"]
        return explained

    @r.post("/users/{uid}/platform-grants")
    def grant_platform_role(request: Request, uid: str, body: dict = Body(...)):
        """Grant a built-in role: `{role, reason, expires_at?}` (a UNIX time; never for
        `super_admin`). Strict re-authentication; every rule of `PlatformAccess.grant` applies."""
        actor = _admin(request, ASSIGN, dangerous=True)
        role = body.get("role")
        if not isinstance(role, str) or role not in ROLES:
            raise ServiceError(400, "bad_platform_role", "请选择一个内置平台角色")
        with _write(request, "platform.roles.assign", object_kind="user", object_id=uid):
            actor = _admin(request, ASSIGN, dangerous=True)
            return {"ok": True, **_access(request).grant(actor["id"], uid, role, _reason(body), body.get("expires_at"))}

    @r.post("/platform-grants/{gid}/revoke")
    def revoke_platform_grant(request: Request, gid: str, body: dict = Body(...)):
        """End one grant: `{reason}`. Strict re-authentication; refuses the last usable super
        administrator (409 `last_super_admin`) and one's own authority to assign."""
        actor = _admin(request, ASSIGN, dangerous=True)
        with _write(request, "platform.roles.assign", object_kind="platform_grant", object_id=gid):
            actor = _admin(request, ASSIGN, dangerous=True)
            return {"ok": True, **_access(request).revoke(actor["id"], gid, _reason(body))}

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
        _admin(request, "platform.audit.view")
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
