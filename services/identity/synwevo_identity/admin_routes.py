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
import re
from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, REGISTRATION_MODES, registration_mode
from synwevo_core.platform_access import (ASSIGN, ASSIGN_SUPER, PlatformAccess, ROLES, SUPER_ADMIN, catalogue, clock,
                                          require_outranking, require_platform, sorted_permissions, sorted_roles)
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import admin_capable, Sessions
from synwevo_core.store import Store, now

REAUTH_SCOPE = "admin"
#: `_admin` called without naming a permission. A route merged from a branch written before the
#: permissions existed fails loudly (500, fail-closed) instead of guessing one. Historical-data
#: routes (claim, and unclaim when it lands) take "platform.workspaces.claim"; see
#: synwevo_core.platform_access.PERMISSIONS for the rest.
_UNNAMED = object()
UNFREEZE_RESTORE_NOTE = ("解冻不会写入安全日志（它只记录收回访问的操作）：如果之后从早于此刻的备份恢复，"
                         "冻结会被重新应用，需要再解冻一次。")


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, mailer=None) -> APIRouter:
    r = APIRouter(prefix="/api/admin", tags=["admin"])

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
        with store.tx():
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
        look for.
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
                   role: str = Query("", pattern="^(|none|any|admin|super_admin|access_admin|support|auditor|api_platform_admin)$")):
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

    @r.post("/users/{uid}/status")
    def set_status(request: Request, uid: str, body: dict = Body(...)):
        """Suspend or restore an account. Suspension takes effect on the next request, not at
        session expiry, and also ends the sessions and platform tokens that already exist.

        Tokens go too: they used to be left alone, so an account suspended *because* it was
        compromised got the attacker's refresh and delegated API tokens back the moment an
        administrator restored it. Anyone holding a platform grant is refused with
        `demote_first` (GitHub Enterprise's rule): demotion is the transactional step that
        protects the last administrator, and suspending admins directly let two of them suspend
        each other at the same moment and leave the platform with none.

        Restoring applies to a suspended account only (409 `not_suspended`), and not to one whose
        grants outrank the caller's (403 `target_outranks_actor`).
        """
        actor = _admin(request, "platform.users.manage", dangerous=True)
        status = (body.get("status") or "").strip()
        reason = (body.get("reason") or "").strip()
        if status not in ("active", "suspended"):
            raise ServiceError(400, "bad_status", "状态只能是 active 或 suspended")
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        target = ident.get_user(uid)
        if not target:
            raise ServiceError(404, "no_user", "找不到该账户")
        # Suspension needs no rank check: it refuses every grant holder (demote_first below), and
        # an account without grants outranks nobody. Restoring is ranked (below).
        revoked = tokens = 0
        journaled = None
        if status == "suspended":
            if uid == actor["id"]:
                raise ServiceError(400, "self_suspend", "不能停用自己的账户")
            with store.tx() as t:
                # Anyone holding a live platform grant is refused: revocation is the step that
                # checks the last usable super administrator inside its transaction, and
                # suspending administrators directly let two of them suspend each other at once.
                access = PlatformAccess(store)
                if access.is_usable_super_admin(uid) and not access.usable_super_admins(excluding=uid):
                    raise ServiceError(409, "last_super_admin",
                                       "这是最后一名可用的最高管理员。请先授予另一位最高管理员，并确认对方能登录并完成身份确认")
                if access.holds_any_grant(uid):
                    raise ServiceError(409, "demote_first",
                                       "请先撤销该账户的全部平台权限，再停用它")
                t.execute("UPDATE users SET status='suspended', updated_at=? WHERE id=?", (now(), uid))
            revoked = sessions.revoke_all_for_user(uid, "admin_suspended")
            from synwevo_core.oidc import Grants
            tokens = Grants(store).revoke_for_user(uid, "admin_suspended")
            from .withdrawals import record as journal
            journaled = journal(store, "user_suspended", user_id=uid)
        else:
            # Restoring undoes a suspension, and only that: an account pending verification or
            # deletion (or already deleted) is not "restored" by an administrator — that would
            # skip the verification, or bring back a deleting super administrator's grants.
            if target["status"] != "suspended":
                raise ServiceError(409, "not_suspended", "只有已停用的账户可以恢复")
            # Ranked outside the write so the denied attempt's record is kept; nobody can grant
            # a role to a suspended account meanwhile (PlatformAccess._precondition).
            _not_outranked(request, actor, uid)
            with store.tx() as t:
                if not t.execute("UPDATE users SET status='active', updated_at=? WHERE id=? AND status='suspended'",
                                 (now(), uid)).rowcount:
                    raise ServiceError(409, "not_suspended", "只有已停用的账户可以恢复")
        audit(request, "admin.user.status", actor_id=actor["id"], object_kind="user",
              object_id=uid, result="ok",
              meta={"status": status, "reason": reason[:500], "sessions_revoked": revoked,
                    "tokens_revoked": tokens, "journaled": journaled})
        return {"ok": True, "status": status, "sessions_revoked": revoked, "tokens_revoked": tokens,
                "journaled": journaled}

    @r.post("/users/{uid}/revoke-sessions")
    def revoke_sessions(request: Request, uid: str, body: dict = Body(...)):
        actor = _admin(request, "platform.users.support", dangerous=True)
        reason = (body.get("reason") or "").strip()
        if len(reason) < 8:
            raise ServiceError(400, "missing_reason", "请写明理由（至少 8 个字）")
        if not ident.get_user(uid):
            raise ServiceError(404, "no_user", "找不到该账户")
        _not_outranked(request, actor, uid)
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
        actor = _admin(request, "platform.workspaces.manage", dangerous=True)
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
        """Compatibility with older clients, through the grant service and all its rules:
        `admin` grants `super_admin`, `none` revokes every live grant of the account (one
        transaction), `operator` is refused — it never carried a capability (D17)."""
        actor = _admin(request, ASSIGN, dangerous=True)
        role = (body.get("role") or "").strip()
        if role == "operator":
            raise ServiceError(400, "operator_retired",
                               "「平台运维」角色已停用：它从未带有任何权限。请改为授予一个内置平台角色")
        if role not in ("none", "admin"):
            raise ServiceError(400, "bad_role", "平台角色只能是 none 或 admin")
        reason = _reason(body)
        access = _access(request)
        if role == "admin":
            if SUPER_ADMIN not in {g["role"] for g in access.live_grants(uid)}:
                access.grant(actor["id"], uid, SUPER_ADMIN, reason)
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
        open_roles = [r for r in sorted_roles(ROLES) if r not in live]
        refusals = {r: _assign_refusal(held, actor["id"], uid, r) for r in open_roles}
        explained["assignable_roles"] = [r for r in open_roles if refusals[r] is None]
        # When nothing can be granted, the reason the page shows (the first role's; for a caller
        # without assign it is the same for every role).
        explained["assign_refusal"] = None if explained["assignable_roles"] or not open_roles \
            else refusals[open_roles[0]]
        for g in explained["grants"]:
            g["revoke_refusal"] = _assign_refusal(held, actor["id"], uid, g["role"])
            g["revocable"] = g["revoke_refusal"] is None
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
        return {"ok": True, **_access(request).grant(actor["id"], uid, role, _reason(body), body.get("expires_at"))}

    @r.post("/platform-grants/{gid}/revoke")
    def revoke_platform_grant(request: Request, gid: str, body: dict = Body(...)):
        """End one grant: `{reason}`. Strict re-authentication; refuses the last usable super
        administrator (409 `last_super_admin`) and one's own authority to assign."""
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
