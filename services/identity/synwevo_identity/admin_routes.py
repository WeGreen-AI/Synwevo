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
from synwevo_core.audit_change import change
from synwevo_core.platform_access import (ADMIN_SECOND_FACTOR_GRACE_KEY, ADMIN_SECOND_FACTOR_GRACE_MAX_DAYS, ADMIN_SECOND_FACTOR_KEY,
                                          ADMIN_SECOND_FACTOR_MODES, ASSIGN, ASSIGN_SUPER, PlatformAccess,
                                          ROLES, SUPER_ADMIN, admin_second_factor_grace_until, admin_second_factor_mode,
                                          admin_session_shortfall, catalogue, clock,
                                          require_outranking, require_platform, session_strong, sorted_permissions, sorted_roles)
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import admin_capable, Sessions
from synwevo_core.store import LEGACY_WORKSPACE_NAME, Store, now

from .profile import deletion_counts, deletion_state

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
    from .admin_organizations import build_router as build_organization_lifecycle_router
    from .admin_ownership import build_router as build_ownership_recovery_router
    r.include_router(build_notification_recovery_router(store, audit))
    # In this order: the ownership router's `/organizations/{oid}/recover-owner` (A37) must be
    # matched ahead of the lifecycle router's generic `/organizations/{oid}/{action}` (A35), which
    # declares its own `/organizations/{oid}/preview` first for the same reason.
    r.include_router(build_ownership_recovery_router(store, ident, audit))
    r.include_router(build_organization_lifecycle_router(store, ident, audit))
    # A18: `/users/bulk-preview` and `/users/bulk` are declared ahead of every `/users/{uid}…`
    # route below, so neither literal segment is ever read as an account id.
    from .admin_bulk import build_router as build_bulk_accounts_router
    r.include_router(build_bulk_accounts_router(store, ident, sessions, audit))
    # A26: the account's second factors and their delayed, announced reset — included here with
    # the other account sub-routers, ahead of every `/users/{uid}…` route below.
    from .admin_second_factor import build_router as build_second_factor_router
    r.include_router(build_second_factor_router(store, ident, audit, mailer=mailer))
    # A47: every workspace's invitations as metadata (masked invitees), and the platform-scope
    # revoke of an abusive one. No route below shares the `/invitations` prefix.
    from .admin_invitations import build_router as build_invitation_oversight_router
    r.include_router(build_invitation_oversight_router(store, ident, audit))

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
                  meta={**change({"mode": before}, {"mode": mode}), "mode": mode, "reason": reason})
        return _policy()

    # ------------------------------------------------------------------ administrator second factor (A11)
    def _second_factor_policy(request: Request, actor: dict) -> dict:
        access = PlatformAccess(store)
        row = store.one("SELECT value FROM meta WHERE key=?", (ADMIN_SECOND_FACTOR_KEY,))
        supers = access.usable_super_admins()
        enrolled = sum(1 for u in supers if access.step_up(u["id"])["second_factor"])
        s = request.state.session
        return {"mode": admin_second_factor_mode(store),
                "source": "database" if row and row["value"] in ADMIN_SECOND_FACTOR_MODES else "default",
                "grace_until": admin_second_factor_grace_until(store), "grace_max_days": ADMIN_SECOND_FACTOR_GRACE_MAX_DAYS,
                "usable_super_admins": len(supers), "enrolled_super_admins": enrolled,
                "caller_second_factor": access.step_up(actor["id"])["second_factor"],
                "caller_session_strong": session_strong(s.get("amr") or "")}

    @r.get("/security/second-factor")
    def second_factor_policy(request: Request):
        """Whether platform administrators must prove a second factor (A11), and whether the
        caller and the usable super administrators could meet it."""
        return _second_factor_policy(request, _admin(request, "platform.config.view"))

    @r.post("/security/second-factor")
    def set_second_factor_policy(request: Request, body: dict = Body(...)):
        """`{"mode": "off" | "required", "expected_mode", "reason"}` with strict re-authentication.
        Requiring a second factor is refused while no usable super administrator has one enrolled:
        the policy must never lock every administrator out of administration. It applies to
        platform administration only — a locked administrator keeps their own account, where
        they enrol a passkey or an authenticator and sign in again."""
        actor = _admin(request, "platform.config.manage", dangerous=True)
        reason = _reason(body)
        mode = body.get("mode")
        if mode not in ADMIN_SECOND_FACTOR_MODES:
            raise ServiceError(400, "bad_second_factor_mode", "请选择有效的第二因素策略")
        # `grace_days` (0–30, default 0): while `required`, administrators without a strong session are
        # only warned until then; 0 locks at once. Ignored for `off`.
        grace_days = body.get("grace_days", 0)
        if isinstance(grace_days, bool) or not isinstance(grace_days, int) or not 0 <= grace_days <= ADMIN_SECOND_FACTOR_GRACE_MAX_DAYS:
            raise ServiceError(400, "bad_grace_days", f"宽限期须为 0 到 {ADMIN_SECOND_FACTOR_GRACE_MAX_DAYS} 的整数天")
        with _write(request, "platform.config.manage", object_kind="setting", object_id=ADMIN_SECOND_FACTOR_KEY):
            actor = _admin(request, "platform.config.manage", dangerous=True)
            before = admin_second_factor_mode(store)
            grace_before = admin_second_factor_grace_until(store)
            if body.get("expected_mode", before) != before:
                raise ServiceError(409, "policy_changed", "第二因素策略已更改，请刷新后重试")
            access = PlatformAccess(store)
            enrolled = sum(1 for u in access.usable_super_admins() if access.step_up(u["id"])["second_factor"])
            if mode == "required" and enrolled == 0:
                raise ServiceError(409, "no_second_factor_admin",
                                   "至少需要一名可用的最高管理员已登记安全密钥或动态验证码，才能要求管理员使用第二因素")
            store.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (ADMIN_SECOND_FACTOR_KEY, mode))
            grace_until = clock() + grace_days * 86400 if mode == "required" and grace_days else None
            if grace_until:
                store.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (ADMIN_SECOND_FACTOR_GRACE_KEY, str(grace_until)))
            else:
                store.execute("DELETE FROM meta WHERE key=?", (ADMIN_SECOND_FACTOR_GRACE_KEY,))
            audit(request, "admin.security.second_factor", actor_id=actor["id"], object_kind="setting",
                  object_id=ADMIN_SECOND_FACTOR_KEY, result="ok",
                  meta={**change({"mode": before, "grace_until": grace_before}, {"mode": mode, "grace_until": grace_until}),
                        "mode": mode, "reason": reason, "enrolled_super_admins": enrolled,
                        "grace_days": grace_days if mode == "required" else 0, "grace_until": grace_until})
        return _second_factor_policy(request, actor)

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
        # return arbitrary provider strings, even to a platform administrator. One classifier
        # (mail.public_error_code), shared with the invitation oversight (A47).
        from .mail import public_error_code
        return public_error_code(value)

    def _mail_row(row):
        code = _failure_code(row.get("last_error"))
        retryable = (row["status"] == "failed" and row["kind"] in ("reset", "verify")
                     and bool(code) and code == row.get("last_error")
                     and code not in ("delivery_unknown", "delivery_in_progress", "superseded"))
        out = {k: row.get(k) for k in ("id", "to_addr", "kind", "ref_id", "status", "attempts",
                                    "created_at", "sent_at", "next_attempt_at")} | {
            "error_code": code, "retryable": retryable}
        if row.get("kind") == "invite":
            # An invitee is no account, and the invitation oversight (A47) never names one: its
            # mail is masked with the same function. Every other kind goes to an account holder.
            from .invites import masked_address
            out["to_addr"] = masked_address(row["to_addr"])[0]
        return out

    # ------------------------------------------------------------------ throttles (G19)
    from .auth_routes import MFA_BACKOFF
    # (bucket prefix, kind, backoff schedule): a schedule marks a slow-backoff bucket whose recorder
    # waits by it (`Identity.backoff_wait(bucket, schedule)`); None means the login schedule for
    # `user:`/`reauth:` and a fixed window for the rest. Longer prefixes come before their prefixes.
    THROTTLE_KINDS = (("user:", "account_login", None), ("mfa:", "account_second_factor", MFA_BACKOFF),
                      ("ip:", "address_login", None), ("reauth:", "session_reauth", None),
                      ("registration-email:", "registration_address", None), ("registration:", "registration_source", None),
                      ("login-code-email:", "login_code_address", None), ("login-code:", "login_code_account", MFA_BACKOFF),
                      ("forgot:", "recovery_source", None), ("bootstrap:", "bootstrap_source", None),
                      ("pkbegin:", "passkey_source", None), ("pkreauth:", "passkey_reauth", None),
                      ("account:", "account_action", None), ("push-", "push", None))

    def _throttle_subject(name: str, kind: str, held) -> tuple[str | None, str | None]:
        """What the row is about, as administration may see it: an account's address only for a
        reader of accounts, a session or bucket by its first characters, a source address never
        (the handle already hashes it). Returns (subject, user_id)."""
        from synwevo_core.identity import throttle_id
        if kind == "account_login":
            key = name[len("user:"):]
            user = store.one("SELECT id,email FROM users WHERE email_key=? AND deleted_at IS NULL", (key,))
            if not user:
                return None, None
            return (user["email"] if "platform.users.view" in held else None), user["id"]
        if kind in ("account_action", "push", "account_second_factor", "login_code_account"):
            uid = name.rsplit(":", 1)[-1]
            user = store.one("SELECT id,email FROM users WHERE id=? AND deleted_at IS NULL", (uid,))
            if not user:
                return None, None
            return (user["email"] if "platform.users.view" in held else None), user["id"]
        if kind == "session_reauth":
            return name[len("reauth:"):][:8], None
        return throttle_id(name)[:12], None

    def _throttle_row(b: dict, held) -> dict | None:
        from synwevo_core.identity import throttle_id
        name = b["bucket"]
        kind, schedule = next(((k, s) for prefix, k, s in THROTTLE_KINDS if name.startswith(prefix)), ("other", None))
        state = ident.throttle_state(name, schedule)
        if not state:
            return None
        subject, uid = _throttle_subject(name, kind, held)
        return {"id": throttle_id(name), "kind": kind, "subject": subject, "user_id": uid,
                "count": state["count"], "first_at": state["first_at"], "last_at": state["last_at"],
                "blocked_until": state["blocked_until"] if state["retry_after"] else None,
                "retry_after": state["retry_after"], "active": state["retry_after"] > 0}

    @r.get("/security/throttles")
    def list_throttles(request: Request, active: bool = Query(False)):
        """G19: the sign-in and request throttles the platform is holding (`auth_attempts`): an
        account's failed proofs, a source address's, a registration or recovery window — each
        with its count, window and remaining wait. Addresses never appear (the handle hashes
        them); an account is named only for a reader of accounts. Read-only."""
        actor = _admin(request, "platform.config.view")
        held = actor["platform_permissions"]
        rows = [row for row in (_throttle_row(b, held) for b in store.all(
            "SELECT bucket,count,first_at,last_at,blocked_until FROM auth_attempts ORDER BY last_at DESC LIMIT 500")) if row]
        if active:
            rows = [row for row in rows if row["active"]]
        return {"items": rows, "kinds": [k for _, k, _ in THROTTLE_KINDS]}

    @r.post("/security/throttles/lift")
    def lift_throttle(request: Request, body: dict = Body(...)):
        """G19: release one throttle (a person locked out of their own account by an attacker's
        guesses, a shared address that tripped a window) with a written reason and strict
        re-authentication. The next attempt starts from zero; nothing else changes."""
        actor = _admin(request, "platform.config.manage", dangerous=True)
        reason = _reason(body)
        handle = body.get("id")
        if not isinstance(handle, str) or not re.fullmatch(r"[0-9a-f]{24}", handle):
            raise ServiceError(400, "bad_throttle", "请选择要解除的限流记录")
        from synwevo_core.identity import throttle_id
        with _write(request, "platform.config.manage", object_kind="throttle", object_id=handle):
            actor = _admin(request, "platform.config.manage", dangerous=True)
            held = actor["platform_permissions"]
            match = next((b for b in store.all("SELECT bucket,count,first_at,last_at,blocked_until FROM auth_attempts")
                          if throttle_id(b["bucket"]) == handle), None)
            if not match:
                raise ServiceError(404, "no_throttle", "找不到该限流记录，可能已经过期或被解除")
            row = _throttle_row(match, held)
            if row and row["user_id"]:
                _not_outranked(request, actor, row["user_id"])
            ident.lift_throttle(match["bucket"])
            audit(request, "admin.security.throttle_lift", actor_id=actor["id"],
                  object_kind="user" if row and row["user_id"] else "throttle",
                  object_id=(row["user_id"] if row and row["user_id"] else handle), result="ok",
                  meta={**change({"active": bool(row and row["active"]), "count": match["count"]}, {"active": False, "count": 0}),
                        "reason": reason, "kind": row["kind"] if row else "other", "count": match["count"],
                        "was_active": bool(row and row["active"])})
        return {"ok": True, "kind": row["kind"] if row else "other", "lifted": True}

    @r.get("/mail")
    def outbox(request: Request, q: str = Query("", max_length=100),
               status: str = Query("", pattern="^(|queued|sent|failed)$"),
               limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63-1),
               snapshot: int | None = Query(None, ge=0, le=2**63-1)):
        _admin(request, "platform.mail.view")
        from .invites import invitee_search
        from .mail import mode
        # The address or the mail id by substring, as before — except for an invite mail, which (like
        # the invitation oversight, A47) only the whole address, its `@domain` or its whole mail id
        # finds: a fragment, or a LIKE wildcard, finds no invite mail.
        exact, domain = invitee_search(q)
        page = _page("mail_outbox", columns="id,to_addr,kind,ref_id,status,attempts,created_at,"
                     "sent_at,next_attempt_at,last_error",
                     where="(?='' OR (COALESCE(kind,'')<>'invite' AND (to_addr LIKE ? OR id LIKE ?)) "
                           "OR (kind='invite' AND (id=? OR to_addr=? OR to_addr LIKE ? ESCAPE '\\'))) "
                           "AND (?='' OR status=?)",
                     params=(q, f"%{q}%", f"%{q}%", q.strip(), exact or "", domain or "", status, status),
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
        if store.one("SELECT 1 FROM app_accounts WHERE user_id=?", (uid,)):
            raise ServiceError(409, "application_identity", "应用身份不是人，不能接收找回或验证邮件")
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

    # The lifecycle acts (`POST /organizations/{oid}/preview` and `/{action}`) are the platform-scope
    # path of admin_organizations.py, included above: a confirmed operation by any holder of
    # `platform.orgs.manage`, member of the organization or not (A35).

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
            "users": store.one("SELECT COUNT(*) AS n FROM users WHERE deleted_at IS NULL "
                               "AND NOT EXISTS (SELECT 1 FROM app_accounts a WHERE a.user_id=users.id)")["n"],
            "suspended": store.one("SELECT COUNT(*) AS n FROM users WHERE status='suspended' "
                                   "AND deleted_at IS NULL")["n"],
            "workspaces": store.one("SELECT COUNT(*) AS n FROM workspaces WHERE deleted_at IS NULL")["n"],
            # Accounts whose platform grants count, and among them the usable super
            # administrators (active, verified, able to complete the step-up).
            "admins": _platform_holders(),
            "super_admins": len(ident.platform_admins()),
            "unclaimed": None,
            # A20: deletion requests in their grace period, past due, or owing a withdrawal record.
            "account_deletions": deletion_counts(store),
            # A32: how many live person accounts hold a second factor — aggregates only, never a code.
            "second_factor": _second_factor_adoption(),
        }
        if legacy:
            out["unclaimed"] = _workspace_row(legacy)
        return out

    def _second_factor_adoption() -> dict:
        """Counts over live person accounts (active, not an application identity): a confirmed
        authenticator, a passkey (and one that verifies the user), either, and the accounts whose
        authenticator has no unused recovery code left. The platform administrators' own count
        comes from the same step-up rule the second-factor policy uses."""
        live = "users u WHERE u.status='active' AND u.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM app_accounts a WHERE a.user_id=u.id)"
        totp = "EXISTS (SELECT 1 FROM totp_secrets t WHERE t.user_id=u.id AND t.confirmed_at IS NOT NULL)"
        passkey = "EXISTS (SELECT 1 FROM passkeys p WHERE p.user_id=u.id AND p.deleted_at IS NULL)"
        passkey_uv = "EXISTS (SELECT 1 FROM passkeys p WHERE p.user_id=u.id AND p.deleted_at IS NULL AND p.uv_capable=1)"
        no_recovery = "NOT EXISTS (SELECT 1 FROM recovery_codes r WHERE r.user_id=u.id AND r.used_at IS NULL)"
        count = lambda where="1": store.one(f"SELECT COUNT(*) AS n FROM {live} AND ({where})")["n"]  # noqa: E731
        access = PlatformAccess(store)
        supers = access.usable_super_admins()
        return {"accounts": count(), "totp": count(totp), "passkeys": count(passkey), "passkeys_uv": count(passkey_uv),
                "any": count(f"{totp} OR {passkey}"), "totp_without_recovery": count(f"{totp} AND {no_recovery}"),
                "super_admins": {"usable": len(supers),
                                 "enrolled": sum(1 for s in supers if access.step_up(s["id"])["second_factor"])}}

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
            if t.execute("SELECT 1 FROM app_accounts WHERE user_id=?", (target_id,)).fetchone():
                raise ServiceError(409, "application_identity", "应用身份不是人，不能接收历史数据")
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
                  meta={**change({"status": live["status"], "name": name_before, "members": 0},
                                 {"status": "active", "name": name_after, "members": 1}),
                        "target_user": target_id, "target_email": target["email"], "role": role,
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
                      meta={**outcome, **change({"status": w["status"], "name": w["name"], "members": len(members)},
                                                {"status": "pending_claim", "name": plan["name_after"], "members": 0}),
                            "reason": reason, "journaled": True,
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
        # A22: application identities (`app_accounts`, e.g. the Docs card sender) are not people:
        # they cannot sign in, hold platform roles, be suspended or receive recovery mail, so they
        # are not listed with the people the administration governs. Their row stays readable by id.
        page = _page("users", columns="id,email,display_name,status,platform_role,created_at,deletion_due_at",
                     where="deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM app_accounts a WHERE a.user_id=users.id) "
                           "AND (?='' OR email LIKE ? OR display_name LIKE ?) "
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
        from synwevo_core.agent_safety import AgentAccess
        from synwevo_core.push import PushSubscriptions
        from .external import ExternalIdentities, load_providers
        from .mfa import Mfa
        held = actor["platform_permissions"]
        suspension = AgentAccess(store).active(uid)
        pushes = PushSubscriptions(store)
        active_push = {p["id"] for p in pushes.active_for(uid)}
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
        for w in workspaces:
            # The applications the workspace has switched on (ids and names, for the access explanation, A49).
            w["apps"] = store.all("SELECT a.id, a.name FROM workspace_apps x JOIN apps a ON a.id=x.app_id "
                                  "WHERE x.workspace_id=? ORDER BY a.id", (w["id"],))
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
            # A17: browser push devices — never the endpoint (a capability URL) or its keys.
            "push_subscriptions": [{"id": p["id"], "user_agent": p.get("user_agent"), "created_at": p["created_at"],
                                    "last_sent_at": p.get("last_sent_at"), "fail_count": p.get("fail_count") or 0,
                                    "active": p["id"] in active_push} for p in pushes.list_for(uid)],
            "deletion": deletion_state(store, user),
            # The personal Agent's content-safety suspension (admin_agent_safety): the administrator's
            # reason and identity, never a conversation; lifted from here through that module's route.
            "agent_access": {"suspended": suspension is not None, "suspension": None if not suspension else {
                "id": suspension["id"], "reason": suspension["reason"], "report_id": suspension.get("report_id"),
                "created_at": suspension["created_at"],
                "created_by_email": (store.one("SELECT email FROM users WHERE id=?", (suspension["created_by"],)) or {}).get("email")}},
            "audit": {"permitted": timeline is not None, "limit": TIMELINE_LIMIT, "items": timeline or []},
        }

    @r.post("/users/{uid}/deletion/cancellation-mail")
    def resend_cancellation_mail(request: Request, uid: str, body: dict = Body(...)):
        """A20: the person asked to delete their account and lost the mail that cancels it. The
        platform sends a new cancellation link to the account's own address, expiring when the
        deletion falls due (never later), and the person decides on the page. Administration
        cannot cancel a deletion on someone's behalf and never sees the link."""
        actor = _admin(request, "platform.users.support", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        import secrets
        from synwevo_core.store import new_id
        from .mail import deletion_mail
        from .profile import digest, public_link
        with _write(request, "platform.users.support", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.support", dangerous=True)
            user = ident.get_user(uid)
            if not user:
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            ts = now()
            due = user.get("deletion_due_at")
            if user["status"] != "pending_delete" or not due or due <= ts:
                raise ServiceError(409, "not_pending_delete", "该账户没有待处理的注销申请")
            # The request's own mail and earlier re-sends count: three an hour, a minute apart.
            recent = store.one("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM account_recoveries "
                               "WHERE user_id=? AND created_at>?", (uid, ts - 3600))
            if recent["n"] >= 3 or (recent["last"] and recent["last"] > ts - 60):
                retry_after = max(1, int((3600 if recent["n"] >= 3 else 60) - (ts - recent["last"])))
                raise ServiceError(429, "mail_rate_limited", "发送过于频繁，请稍后重试", {"retry_after": retry_after})
            raw = secrets.token_urlsafe(32)
            url = public_link("/account/recovery", raw, user.get("locale"))
            subject, text, html = deletion_mail(user.get("locale"), url, resend=True)
            delivery = _mail(request).enqueue(user["email"], subject, text, kind="account_recovery", ref_id=uid, html=html)
            store.execute("UPDATE account_recoveries SET used_at=? WHERE user_id=? AND used_at IS NULL", (ts, uid))
            store.execute("INSERT INTO account_recoveries (id,user_id,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)",
                          (new_id("ar"), uid, digest(raw), ts, due))
            audit(request, "admin.user.deletion_cancellation_mail", actor_id=actor["id"], object_kind="user",
                  object_id=uid, result="ok", meta={"reason": reason, "mail_id": delivery["id"], "due_at": due})
        return {"ok": True, "delivery": _mail_row(delivery), "due_at": due}

    @r.post("/account-deletions/finalize")
    def finalize_account_deletions(request: Request, body: dict = Body(...)):
        """A20: run the deletion sweep now — accounts past their grace period are finalized and
        the withdrawal records still owed are written — instead of waiting for the next hourly
        pass. The sweeper's own idempotent routine; nothing falls due early, and its failure is
        the fact reported (503) rather than a silent retry."""
        actor = _admin(request, "platform.users.manage", dangerous=True)
        reason = _reason(body)
        from .profile import finalize_deletions, record_deletion_withdrawals
        try:
            # One transaction for the sweep and its audit (G24): a failed audit undoes the sweep,
            # which the next hourly pass then repeats — never a deletion without its record. The
            # withdrawal records (the fsync'd security journal) are appended only after that
            # commit: a journal line must never describe a deletion that was rolled back.
            with store.tx():
                before = deletion_counts(store)
                finalized = finalize_deletions(store, withdrawals=False)
                after = deletion_counts(store)
                audit(request, "admin.accounts.finalize_deletions", actor_id=actor["id"], object_kind="platform",
                      object_id="account_deletions", result="ok",
                      meta={**change(before, after), "reason": reason, "finalized": finalized})
            # The withdrawal records follow the commit; the answer reports the state they leave,
            # the audit row the state the transaction committed.
            record_deletion_withdrawals(store)
            after = deletion_counts(store)
        except Exception as e:  # noqa: BLE001 - the sweep's failure is what this route reports
            audit(request, "admin.accounts.finalize_deletions", actor_id=actor["id"], object_kind="platform",
                  object_id="account_deletions", result="error", meta={"reason": reason, "code": type(e).__name__})
            raise ServiceError(503, "finalize_failed", "到期删除未能完成，已记录失败；请查看服务日志后重试") from e
        return {"ok": True, "finalized": finalized, "before": before, "after": after}

    ACCESS_GATES = ("account", "application", "membership", "workspace", "organization", "workspace_app", "entitlement")

    @r.get("/users/{uid}/access")
    def explain_access(request: Request, uid: str, workspace_id: str = Query("", max_length=100),
                       app_id: str = Query("", max_length=64)):
        """A49: why this person can or cannot open one application in one workspace, gate by gate
        — account, application, membership, workspace, organization, the workspace's application
        switch, the entitlement — and then the verdict from the very code the browser gate runs
        (`Apps.check_access`), so the explanation cannot drift from what the person meets.
        `consistent` says the two agree. Read with `platform.roles.view`; the explanation acts as
        nobody and grants nothing; names follow the list permissions, as the detail's do."""
        from synwevo_core.apps import Apps, MESSAGES_APP_ID, PERSONAL_APP_IDS, PLATFORM_APP_ID, entitlement_state
        from synwevo_core.organizations import workspace_access_failure
        actor = _admin(request, "platform.roles.view")
        if not workspace_id or not app_id:
            raise ServiceError(400, "bad_access_query", "请指定要解释的工作空间与应用")
        user = ident.get_user(uid)
        if not user:
            raise ServiceError(404, "no_user", "找不到该账户")
        apps = Apps(store, ident)
        app = apps.get(app_id)
        if not app:
            raise ServiceError(404, "no_app", "找不到该应用")
        ws = ident.get_workspace(workspace_id)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到这个工作空间")
        held = actor["platform_permissions"]
        scope = ("platform" if app_id == PLATFORM_APP_ID else "personal" if app_id in PERSONAL_APP_IDS
                 else "organization" if app_id == MESSAGES_APP_ID else "workspace")
        gates = []

        def gate(name, state, **detail):
            gates.append({"gate": name, "state": state, "detail": detail})
        gate("account", "pass" if user["status"] == "active" else "fail", status=user["status"])
        gate("application", "pass" if app["status"] == "active" else "fail", status=app["status"],
             access_policy=app.get("access_policy"), scope=scope)
        # Membership as the gate itself sees it (`workspaces_for`: the row, and for an organization's
        # workspace the organization membership too), never the memberships table alone.
        mine = next((w for w in ident.workspaces_for(uid) if w["id"] == workspace_id), None)
        gate("membership", "pass" if mine else "fail", role=mine["role"] if mine else None,
             can_export=bool(mine["can_export"]) if mine else None)
        gate("workspace", "pass" if ws["status"] == "active" else "fail", status=ws["status"], kind=ws.get("kind"))
        if scope == "workspace":
            why = workspace_access_failure(store, workspace_id, uid)
            gate("organization", "pass" if why is None else "fail", reason=why, organization_id=ws.get("organization_id"))
            enabled = store.one("SELECT enabled_at FROM workspace_apps WHERE workspace_id=? AND app_id=?", (workspace_id, app_id))
            gate("workspace_app", "pass" if enabled else "fail", enabled_at=enabled["enabled_at"] if enabled else None)
            state = entitlement_state(store, workspace_id, app_id, uid)
            policy = app.get("access_policy")
            gate("entitlement", "pass" if state == "granted" or (state == "none" and policy == "members") else "fail",
                 entitlement=state, access_policy=policy)
        else:
            for name in ("organization", "workspace_app", "entitlement"):
                gate(name, "skip", note="not_workspace_scoped")
        allowed, resolved, reason = apps.check_access(uid, app_id, workspace_id)
        return {"user": {"id": uid, "email": user["email"] if "platform.users.view" in held else None, "status": user["status"]},
                "workspace": {"id": workspace_id, "name": ws["name"] if "platform.workspaces.view" in held else None, "status": ws["status"]},
                "app": {"id": app_id, "name": app["name"], "status": app["status"], "scope": scope},
                "gates": gates,
                "verdict": {"allowed": allowed, "workspace_id": resolved, "reason": None if allowed else reason},
                "consistent": allowed == all(g["state"] != "fail" for g in gates)}

    REGISTRATION_EVENTS = ("auth.registration", "auth.registration.existing_account", "auth.registration.throttled",
                           "auth.email.verify_requested", "auth.email.verified", "auth.onboarding.personal")

    @r.get("/diagnostics/registration")
    def registration_diagnostics(request: Request, email: str = Query("", max_length=254)):
        """A27: one address's registration, stage by stage — is the address acceptable here, was
        a request throttled (and is it now), the account row, the verification tokens (never their
        values), the mails to the address and what became of them, the activation — so support can
        answer "I registered and nothing arrived" without reading logs. Metadata only. The audit
        events (and the human-verification results near them) follow `platform.audit.view`, the
        mail rows `platform.mail.view`, each explained when withheld; reading changes nothing."""
        import hashlib
        from synwevo_core.identity import normalise_email, registration_mode as current_mode, valid_email
        actor = _admin(request, "platform.users.view")
        raw = (email or "").strip()
        if not raw or not valid_email(raw):
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        key = normalise_email(raw)
        held = actor["platform_permissions"]
        ts = now()
        user = ident.by_email(raw)
        uid = user["id"] if user else None
        bucket = store.one("SELECT count,first_at,last_at,blocked_until FROM auth_attempts WHERE bucket=?",
                           ("registration-email:" + hashlib.sha256(key.encode()).hexdigest(),))
        throttle = None
        if bucket:
            in_window = ts - bucket["first_at"] <= 3600
            blocked = bool(bucket["blocked_until"] and bucket["blocked_until"] > ts) or (in_window and bucket["count"] >= 3)
            throttle = {"count": bucket["count"], "first_at": bucket["first_at"], "last_at": bucket["last_at"],
                        "blocked": blocked, "blocked_until": bucket["blocked_until"] if blocked else None,
                        "limit": 3, "window_s": 3600}
        tokens = [{**t, "expired": bool(t["used_at"] is None and t["expires_at"] <= ts)} for t in store.all(
            "SELECT kind,created_at,expires_at,used_at FROM email_tokens WHERE user_id=? AND kind='verify' "
            "ORDER BY created_at DESC LIMIT 20", (uid or "",))]
        events = challenges = None
        if "platform.audit.view" in held:
            marks = ",".join("?" * len(REGISTRATION_EVENTS))
            events = []
            for row in store.all(
                    f"SELECT id,at,action,result,ip,meta FROM audit_events WHERE (actor_id=? AND action IN ({marks})) "
                    "OR (object_kind='registration' AND object_id IN (?,?)) ORDER BY at DESC LIMIT 100",
                    (uid or "", *REGISTRATION_EVENTS, key, hashlib.sha256(key.encode()).hexdigest())):
                try:
                    meta = json.loads(row.pop("meta") or "{}")
                except ValueError:
                    meta = {}
                row["detail"] = {k: meta.get(k) for k in ("scope", "retry_after", "mail_error") if meta.get(k) is not None}
                events.append(row)
            challenges = []
            ips = sorted({e["ip"] for e in events if e["ip"]})
            if ips:
                lo, hi = min(e["at"] for e in events) - 900, max(e["at"] for e in events) + 900
                for row in store.all(
                        f"SELECT id,at,result,ip,meta FROM audit_events WHERE action='auth.challenge' AND ip IN ({','.join('?' * len(ips))}) "
                        "AND at BETWEEN ? AND ? ORDER BY at DESC LIMIT 50", (*ips, lo, hi)):
                    try:
                        meta = json.loads(row.pop("meta") or "{}")
                    except ValueError:
                        meta = {}
                    row["action"] = meta.get("action")
                    row["reason"] = meta.get("reason")
                    challenges.append(row)
        mail = None
        if "platform.mail.view" in held:
            mail = [_mail_row(m) for m in store.all(
                "SELECT id,to_addr,kind,ref_id,status,attempts,created_at,sent_at,next_attempt_at,last_error FROM mail_outbox "
                "WHERE lower(to_addr)=? OR (?!='' AND ref_id=?) ORDER BY created_at DESC LIMIT 20", (key, uid or "", uid or ""))]
        # The stages, each with the newest fact that decides it.
        latest = {}
        for e in events or []:
            latest.setdefault(e["action"], e)
        throttled = latest.get("auth.registration.throttled")
        verify_token = tokens[0] if tokens else None
        verify_mail = next((m for m in mail or [] if m["kind"] in ("verify", "account_exists")), None)
        stages = [
            {"stage": "validation", "state": "valid", "at": None},
            {"stage": "challenge", "state": ("ok" if challenges and challenges[0]["result"] == "ok" else challenges[0]["result"] if challenges
                                             else "none") if challenges is not None else "withheld",
             "at": challenges[0]["at"] if challenges else None},
            {"stage": "throttle", "state": "blocked" if throttle and throttle["blocked"] else "refused" if throttled else "clear",
             "at": throttled["at"] if throttled else (throttle or {}).get("last_at")},
            {"stage": "account", "state": user["status"] if user else "none", "at": user["created_at"] if user else None},
            {"stage": "token", "state": "none" if not verify_token else "used" if verify_token["used_at"] else "expired" if verify_token["expired"] else "issued",
             "at": verify_token["used_at"] or verify_token["created_at"] if verify_token else None},
            {"stage": "mail", "state": "withheld" if mail is None else verify_mail["status"] if verify_mail else "none",
             "at": (verify_mail["sent_at"] or verify_mail["created_at"]) if verify_mail else None},
            {"stage": "activation", "state": "active" if user and user["email_verified_at"] else "pending" if user else "none",
             "at": user["email_verified_at"] if user else None},
        ]
        return {"email": key, "valid": True, "registration_mode": current_mode(store),
                "account": {k: user[k] for k in ("id", "status", "created_at", "email_verified_at", "locale")} if user else None,
                "throttle": throttle, "tokens": tokens,
                "audit": {"permitted": events is not None, "events": events or [], "challenges": challenges or []},
                "mail": {"permitted": mail is not None, "items": mail or []},
                "stages": stages}

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
        # The re-check and the writes are shared with each account of a bulk revocation (A18).
        from .admin_operations import revoke_account_sessions
        with _write(request, "platform.users.support", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.support", dangerous=True)
            out = revoke_account_sessions(store, sessions, audit, request, actor, uid, reason)
        return {"ok": True, **out, "journaled": True}

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
                  meta={**change({"sessions": out["sessions_revoked"], "tokens": out["tokens_revoked"]}, {"sessions": 0, "tokens": 0}),
                        "reason": reason, "sessions": out["sessions_revoked"], "tokens": out["tokens_revoked"]})
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
            passkeys = Passkeys(store)
            n_before = passkeys.count(uid)
            passkeys.remove(uid, pid)
            audit(request, "admin.user.passkey_remove", actor_id=actor["id"], object_kind="user",
                  object_id=uid, workspace_given=True, result="ok",
                  meta={**change({"passkeys": n_before}, {"passkeys": passkeys.count(uid)}), "reason": reason, "passkey_id": pid})
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
                  object_id=uid, workspace_given=True, result="ok",
                  meta={**change({"linked": True}, {"linked": False}), "reason": reason, "provider": row["provider"]})
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
                  result="ok", meta={**out, **change({"frozen": not frozen}, {"frozen": frozen}),
                                     "reason": reason[:500], "journaled": journaled})
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

    @r.post("/users/{uid}/push-subscriptions/revoke")
    def revoke_push_subscriptions(request: Request, uid: str, body: dict = Body(...)):
        """A17: every browser push device of the account stops receiving notifications (a lost or
        shared device the person cannot reach themselves). Idempotent — the count says what
        changed; the person is told through their inbox and subscribes again from a device they
        hold. Sessions and sign-in methods are untouched."""
        actor = _admin(request, "platform.users.manage", dangerous=True)
        reason = _reason(body)
        _not_outranked(request, actor, uid)
        from synwevo_core.push import PushSubscriptions
        with _write(request, "platform.users.manage", object_kind="user", object_id=uid):
            actor = _admin(request, "platform.users.manage", dangerous=True)
            if not ident.get_user(uid):
                raise ServiceError(404, "no_user", "找不到该账户")
            _not_outranked(request, actor, uid)
            n = PushSubscriptions(store).revoke_all_for_user(uid, "admin_revoked")
            audit(request, "admin.user.push_revoke", actor_id=actor["id"], object_kind="user", object_id=uid,
                  result="ok", meta={**change({"subscriptions": n}, {"subscriptions": 0}), "reason": reason, "revoked": n})
        notified = _security_notice(request, uid, "push_revoked") if n else False
        return {"ok": True, "revoked": n, "notified": notified}

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
                "step_up": {"required": admin_session_shortfall(store, s.get("amr") or "") is not None, "amr": s.get("amr") or "",
                            "reason": admin_session_shortfall(store, s.get("amr") or ""),
                            "policy": admin_second_factor_mode(store), "grace_until": admin_second_factor_grace_until(store),
                            "second_factor_enrolled": PlatformAccess(store).step_up(user["id"])["second_factor"]},
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

    @r.get("/users/{uid}/platform-access/simulate")
    def simulate_platform_access(request: Request, uid: str, change: str = Query("", max_length=8),
                                 role: str = Query("", max_length=40)):
        """A5, the simulation half: what this account's effective permissions would be if one role
        were granted or no longer held — computed from the catalogue and the account's counting
        rules, never by changing anything — and whether the caller could actually make that change
        (the same refusal codes the grant and revoke routes answer with). Read with
        `platform.roles.view`; nothing is executed and no object outside the catalogue is named."""
        actor = _admin(request, "platform.roles.view")
        if change not in ("grant", "revoke") or role not in ROLES:
            raise ServiceError(400, "bad_simulation", "请选择要模拟的变化（授予或撤销）和一个内置角色")
        access = PlatformAccess(store)
        explained = access.explain(uid)
        held = actor["platform_permissions"]
        before_roles = set(explained["roles"])
        after_roles = (before_roles | {role}) if change == "grant" else (before_roles - {role})
        # A grant by a person counts only once the address is verified (PlatformAccess._counts);
        # a blocked account's grants count for nothing either way.
        counts = not explained["blocked_by"] and (change == "revoke" or explained["email_verified"])
        before = frozenset().union(*(ROLES[r] for r in before_roles)) if before_roles else frozenset()
        after = (frozenset().union(*(ROLES[r] for r in after_roles)) if after_roles else frozenset()) if counts else before
        refusal = _assign_refusal(held, actor["id"], uid, role)
        if refusal is None and change == "grant":
            # The same order as PlatformAccess._grant: what the account must be, then the super
            # administrator rule (a grant of that role only lands through the person's own
            # acceptance of a handover offer), then whether the role is already held.
            try:
                access._precondition(uid, role)
            except ServiceError as e:
                refusal = e.code
            if refusal is None and role == SUPER_ADMIN:
                refusal = "super_admin_acceptance_required"
        if refusal is None and change == "grant" and role in before_roles:
            refusal = "already_held"
        if refusal is None and change == "revoke" and role not in before_roles:
            refusal = "not_held"
        if refusal is None and change == "revoke" and role == SUPER_ADMIN and access.is_usable_super_admin(uid) \
                and not access.usable_super_admins(excluding=uid):
            refusal = "last_super_admin"
        return {"user_id": uid, "change": change, "role": role, "counts": counts,
                "before": {"roles": sorted_roles(before_roles), "permissions": sorted_permissions(before)},
                "after": {"roles": sorted_roles(after_roles) if counts else sorted_roles(before_roles),
                          "permissions": sorted_permissions(after)},
                "gained": sorted_permissions(after - before), "lost": sorted_permissions(before - after),
                "feasible": refusal is None, "refusal": refusal}

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
    @r.get("/audit/actions")
    def audit_actions(request: Request):
        """The catalogue of administration actions (G24): every `admin.*` code with its scope and
        a label in the three languages, for the audit page's filter and rows — and, beside it, the
        applications' `docs.*` / `suite.*` codes (G26, D7 §9), kept as data here because the account
        service may not import the applications. Read-only."""
        _admin(request, "platform.audit.view")
        from .admin_actions import catalogue as action_catalogue
        from .app_actions import catalogue as app_catalogue
        apps = app_catalogue()
        return {**action_catalogue(), "applications": apps["items"], "application_scopes": apps["scopes"]}

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
                     # A value names one action exactly, or a scope on a dot boundary: admin.user and
                     # admin.user. both list every admin.user.* action, while admin.user.revoke_session
                     # never lists admin.user.revoke_sessions.
                     where="(?='' OR audit_events.action=? OR audit_events.action LIKE ?) "
                           "AND (?='' OR u.email LIKE ? OR audit_events.object_id LIKE ?)",
                     params=(action, action, f"{action.rstrip('.')}.%", q, f"%{q}%", f"%{q}%"),
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
