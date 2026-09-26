"""Workspace governance for owners and admins: members, roles, app entitlements, invitations,
ownership.

Mounted under `/api/auth` on the account service, next to the invitation routes. Four rules
shape every route here:

* **Nobody acts above their own role.** An owner manages anyone; an admin manages viewers and
  editors and may grant up to admin. Checked again inside the transaction that makes the change
  — the actor's role *and* the target's, both re-read there (`_authority`) — because a role read
  a moment earlier may already be stale: an admin demoted or removed concurrently must not finish
  a change on the authority they just lost, and a target promoted concurrently must not be
  demoted without the re-authentication that demotion now calls for.
* **A frozen workspace sheds access but gains none.** Removals and revocations work; promotions,
  grants, ownership transfers and invitations (new, upgraded or re-sent) answer 409
  `workspace_frozen`.
* **Withdrawing access is immediate and complete.** Removing someone deletes the membership and,
  in the same transaction, revokes their entitlements and platform tokens for the workspace,
  moves their sessions off it and cancels the invitations they sent that nobody has accepted.
* **Dangerous changes are deliberate.** Removing a member, demoting an admin or owner, making
  someone an owner and handing over ownership need a recent, purpose-scoped re-authentication
  (the existing `admin` scope, strict) *and* a written reason; both go into the audit record.
* **Bulk means per object.** A batch authorises every object on its own and reports each
  outcome — `{"results": [{"id", "ok", "code", "message"}]}` — so a partial failure can never be
  read as a blanket success.

Platform administration gains nothing here: a platform admin who is not a member of a workspace
gets the same 404 as anyone else. Nothing in these responses is document content.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, ROLE_RANK, ROLES
from synwevo_core.sessions import Sessions

from .withdrawals import record as journal

REAUTH_SCOPE = "admin"
REASON_MIN = 8
BULK_MAX = 100
DEFAULT_APP = "docweave"


def error_response(request: Request, exc: ServiceError) -> JSONResponse:
    """The platform's error body, plus `Retry-After` for any 429 that says when to come back.

    The shared handler adds the header only for its own `rate_limited` code; the governance
    limits have their own codes (`invite_rate_limited`) and must still tell a client how long to
    wait rather than leave it to guess.
    """
    detail = exc.detail if isinstance(exc.detail, dict) else None
    resp = JSONResponse({"error": exc.code, "message": exc.message, "detail": exc.detail,
                         "request_id": getattr(request.state, "request_id", "")},
                        status_code=exc.status)
    if exc.status == 429 and detail and detail.get("retry_after"):
        resp.headers["Retry-After"] = str(int(detail["retry_after"]))
    return resp


def build_router(ident: Identity, sessions: Sessions, invites, mailer, audit) -> APIRouter:
    # No prefix of its own: auth_routes includes this under its `/api/auth` router, and an
    # included router's routes receive the parent's prefix.
    r = APIRouter(tags=["workspace-governance"])
    store = ident.store

    # ------------------------------------------------------------------ who is asking
    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def _actor(request: Request, wid: str, *, manage: bool = True) -> tuple[dict, dict, dict]:
        """(session, membership, workspace) for a caller who may govern this workspace.

        A non-member gets 404, not 403: whether a workspace exists is not something an outsider
        — platform administrators included — learns from these routes.
        """
        s = _session(request)
        ws = ident.get_workspace(wid)
        m = ident.membership(wid, s["user_id"]) if ws else None
        if not ws or not m:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        if manage and m["role"] not in ("admin", "owner"):
            raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以管理成员")
        return s, m, ws

    def _manageable(actor_m: dict) -> tuple[str, ...]:
        """The member roles this actor may act on."""
        return ROLES if actor_m["role"] == "owner" else ("viewer", "editor")

    def _may_assign(actor_m: dict, role: str) -> None:
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        if ROLE_RANK[role] > ROLE_RANK[actor_m["role"]]:
            raise ServiceError(403, "role_too_high", "不能授予比自己更高的角色")

    def _dangerous(request: Request, s: dict, body: dict) -> str:
        """Recent re-authentication for this purpose, plus a written reason. Returns the reason."""
        if not Sessions.reauth_fresh(s, REAUTH_SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": REAUTH_SCOPE})
        reason = str(body.get("reason") or "").strip()
        if len(reason) < REASON_MIN:
            raise ServiceError(400, "missing_reason", f"请写明理由（至少 {REASON_MIN} 个字），它会写入审计记录",
                               {"field": "reason"})
        return reason[:500]

    def _dangerous_ok(s: dict, body: dict) -> str | None:
        """The reason, when this request would pass `_dangerous`; None otherwise. For a change
        that looked safe before the transaction but may turn out not to be inside it."""
        try:
            return _dangerous(None, s, body)                  # request is not used
        except ServiceError:
            return None

    def _grants_allowed(ws: dict | None) -> None:
        # A frozen workspace can still shed access (removals, revocations) but gains none.
        if not ws or ws["status"] != "active":
            frozen = bool(ws) and ws["status"] == "frozen"
            raise ServiceError(409, "workspace_frozen" if frozen else "workspace_unavailable",
                               "该工作空间已被冻结，暂时不能增加成员或权限" if frozen else "该工作空间当前不可用")

    def _authority(wid: str, *, role: str | None = None, can_export=None, grant: bool = False,
                   reauthed: bool = False):
        """The caller's authority, checked inside the write transaction against the actor's and
        the target's rows as they are there (see `Identity._authorize`).

        Everything the pre-transaction checks decided is decided again: that the actor still
        governs the workspace, that the target is within their ceiling, that the new role is not
        above their own, that a change which is dangerous *now* was re-authenticated, and that a
        change which gains access is not being made in a workspace frozen meanwhile.
        """
        def check(target: dict, actor: dict | None) -> None:
            if not actor or actor["role"] not in ("admin", "owner"):
                raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以管理成员")
            if target["role"] not in _manageable(actor):
                raise ServiceError(403, "role_too_high", "不能管理与自己同级或更高角色的成员")
            gains = grant or can_export is True
            if role:
                _may_assign(actor, role)
                if _is_dangerous_change(target["role"], role) and not reauthed:
                    raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": REAUTH_SCOPE})
                gains = gains or ROLE_RANK[role] > ROLE_RANK[target["role"]]
            if gains:
                _grants_allowed(ident.get_workspace(wid))      # read inside the same transaction
        return check

    def _ids(body: dict, key: str) -> list[str]:
        raw = body.get(key)
        if not isinstance(raw, list) or not raw:
            raise ServiceError(400, "missing_ids", f"请提供 {key}（非空列表）", {"field": key})
        if len(raw) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多处理 {BULK_MAX} 项", {"field": key})
        out, seen = [], set()
        for v in raw:
            v = str(v or "").strip()
            if v and v not in seen:
                seen.add(v)
                out.append(v)
        return out

    def _bulk(results: list[dict]) -> dict:
        ok = sum(1 for x in results if x["ok"])
        return {"ok": ok == len(results), "succeeded": ok, "failed": len(results) - ok, "results": results}

    def _result(obj_id: str, fn) -> dict:
        try:
            data = fn()
            return {"id": obj_id, "ok": True, "code": "ok", "message": "完成", **(data or {})}
        except ServiceError as e:
            result = {"id": obj_id, "ok": False, "code": e.code, "message": e.message}
            # A mixed batch stays HTTP 200, so its 429 items must carry their own cooldown.
            # Preserve the service's value without exposing unrelated error details.
            if e.status == 429 and isinstance(e.detail, dict) and "retry_after" in e.detail:
                result["retry_after"] = e.detail["retry_after"]
            return result

    def _member_view(wid: str, uid: str, me: str) -> dict | None:
        row = store.one(
            "SELECT m.user_id, m.role, m.can_export, m.created_at AS joined_at, u.email, u.display_name, "
            "u.status AS account_status FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.workspace_id=? AND m.user_id=?", (wid, uid))
        if row:
            row["can_export"] = bool(row["can_export"])
            row["is_self"] = uid == me
        return row

    # ================================================================== members
    @r.get("/workspaces/{wid}/members")
    def list_members(request: Request, wid: str, q: str = Query("", max_length=100),
                     role: str = Query(""), status: str = Query(""), entitlement: str = Query(""),
                     app: str = Query(DEFAULT_APP, max_length=64),
                     offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        s, m, ws = _actor(request, wid)
        out = ident.list_members(wid, q=q, role=role or None, status=status or None, app_id=app,
                                 entitlement=entitlement or None, offset=offset, limit=limit)
        for it in out["items"]:
            it["is_self"] = it["user_id"] == s["user_id"]
            it["manageable"] = it["user_id"] != s["user_id"] and it["role"] in _manageable(m)
        a = store.one("SELECT id, name, status, access_policy FROM apps WHERE id=?", (app,))
        return {**out,
                "workspace": {"id": ws["id"], "name": ws["name"], "kind": ws["kind"], "status": ws["status"]},
                "caller": {"user_id": s["user_id"], "role": m["role"]},
                "app": a,
                # GitHub's continuity advice as a warning, not a rule: a personal workspace has
                # one person in it and that is fine.
                "single_owner": out["active_owner_count"] <= 1}

    def _notify(request: Request, user_id: str, kind: str, **kw) -> None:
        """An inbox row for a member, best effort: the change happened whether or not the row lands."""
        notifier = getattr(request.app.state, "notifier", None)
        if notifier is None:
            return
        try:
            notifier.emit(user_id, kind, **kw)
        except Exception as e:  # noqa: BLE001
            print(f"[synwevo] notification not recorded ({kind}): {type(e).__name__}", flush=True)

    def _actor_name(s: dict) -> str:
        me = ident.get_user(s["user_id"]) or {}
        return me.get("display_name") or me.get("email") or "—"

    def _change_one(request: Request, s: dict, m: dict, wid: str, uid: str, role: str | None,
                    can_export, reason: str | None, *, reauthed: bool) -> dict:
        target = _precheck(s, m, wid, uid, role)
        new_role = role or target["role"]
        if can_export is not None and not isinstance(can_export, bool):
            raise ServiceError(400, "bad_can_export", "can_export 必须是 true 或 false", {"field": "can_export"})
        # `role` (not new_role) is what the caller asked for: with no role in the request the
        # in-transaction check must not treat "keep the current role" as an assignment.
        out = ident.change_role(wid, uid, role, can_export=can_export, actor_id=s["user_id"],
                                authorize=_authority(wid, role=role, can_export=can_export, reauthed=reauthed))
        before = out.pop("before")
        new_role = out["role"]
        downgrade = ROLE_RANK[new_role] < ROLE_RANK[before["role"]]
        journaled = None
        if downgrade:
            journaled = journal(store, "role_changed", workspace_id=wid, user_id=uid, role=new_role)
        audit(request, "member.role", actor_id=s["user_id"], workspace_id=wid, object_kind="membership",
              object_id=uid, result="ok",
              meta={"before": before, "after": {"role": out["role"], "can_export": bool(out["can_export"])},
                    "reason": reason, "journaled": journaled})
        if out["role"] != before["role"]:
            ws = ident.get_workspace(wid) or {}
            _notify(request, uid, "member.role_changed", params={"workspace": ws.get("name"), "role": out["role"], "actor": _actor_name(s)},
                    workspace_id=wid, object_kind="membership", object_id=uid,
                    target={"origin": "accounts", "path": "/account#workspaces"})
        return {"member": _member_view(wid, uid, s["user_id"]), "before": before,
                "after": {"role": out["role"], "can_export": bool(out["can_export"])},
                "journaled": journaled}

    def _is_dangerous_change(target_role: str, new_role: str) -> bool:
        return (new_role == "owner" and target_role != "owner") or \
            (target_role in ("admin", "owner") and ROLE_RANK[new_role] < ROLE_RANK[target_role])

    def _precheck(s: dict, m: dict, wid: str, uid: str, role: str | None) -> dict:
        """What can be refused without asking for a password first: self, not a member, above
        the caller's role. Prompting for re-authentication and then refusing would teach people
        to type their password into prompts that cannot help."""
        if uid == s["user_id"]:
            # GitHub's rule: nobody changes their own role. Stepping down as owner is a transfer.
            raise ServiceError(403, "self_role", "不能修改自己的角色；所有者请使用「移交所有权」")
        target = ident.membership(wid, uid)
        if not target:
            raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
        if target["role"] not in _manageable(m):
            raise ServiceError(403, "role_too_high", "不能管理与自己同级或更高角色的成员")
        if role:
            _may_assign(m, role)
        return target

    @r.patch("/workspaces/{wid}/members/{uid}")
    def change_member(request: Request, wid: str, uid: str, body: dict = Body(...)):
        """`{"role"?, "can_export"?, "reason"?}`. Demoting an admin/owner or making someone an
        owner needs recent `admin` re-authentication and a reason."""
        s, m, ws = _actor(request, wid)
        role = str(body.get("role") or "").strip() or None
        target = _precheck(s, m, wid, uid, role)
        if role and _is_dangerous_change(target["role"], role):
            reason = _dangerous(request, s, body)
        else:
            # Looks safe from here; the transaction decides again on the row as it is then, and
            # needs to know whether this request would have passed the dangerous-change gate.
            reason = _dangerous_ok(s, body)
        if role and target and ROLE_RANK.get(role, -1) > ROLE_RANK[target["role"]]:
            _grants_allowed(ws)
        if body.get("can_export") is True:
            _grants_allowed(ws)
        return {"ok": True, **_change_one(request, s, m, wid, uid, role, body.get("can_export"),
                                          reason or str(body.get("reason") or "").strip()[:500] or None,
                                          reauthed=reason is not None)}

    @r.post("/workspaces/{wid}/members/roles")
    def change_members(request: Request, wid: str, body: dict = Body(...)):
        """Bulk role change: `{"changes": [{"user_id", "role", "can_export"?}], "reason"?}`.

        If any change in the batch is a dangerous one, the batch needs the re-authentication and
        the reason up front — before anything changes — rather than failing halfway through."""
        s, m, ws = _actor(request, wid)
        changes = body.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ServiceError(400, "missing_changes", "请提供 changes（非空列表）", {"field": "changes"})
        if len(changes) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多处理 {BULK_MAX} 项", {"field": "changes"})
        reason = str(body.get("reason") or "").strip()[:500] or None
        dangerous = False
        for ch in changes:
            # A non-object item fails on its own in the loop below (missing_user); it must not
            # turn the whole batch into a 500 here.
            ch = ch if isinstance(ch, dict) else {}
            t = ident.membership(wid, str(ch.get("user_id") or ""))
            role = str(ch.get("role") or "")
            if t and role in ROLES and _is_dangerous_change(t["role"], role):
                dangerous = True
        reauthed = False
        if dangerous:
            reason = _dangerous(request, s, body)
            reauthed = True
        elif _dangerous_ok(s, body) is not None:
            reauthed = True
        results = []
        for ch in changes:
            ch = ch if isinstance(ch, dict) else {}
            uid = str(ch.get("user_id") or "").strip()

            def one(ch=ch, uid=uid):
                if not uid:
                    raise ServiceError(400, "missing_user", "缺少 user_id")
                t = ident.membership(wid, uid)
                if t and ((ch.get("role") and ROLE_RANK.get(str(ch["role"]), -1) > ROLE_RANK[t["role"]])
                          or ch.get("can_export") is True):
                    _grants_allowed(ws)
                return _change_one(request, s, m, wid, uid, str(ch.get("role") or "").strip() or None,
                                   ch.get("can_export"), reason, reauthed=reauthed)
            results.append(_result(uid, one))
        return _bulk(results)

    def _remove_one(request: Request, s: dict, m: dict, wid: str, uid: str, reason: str) -> dict:
        if uid == s["user_id"]:
            raise ServiceError(400, "use_leave", "要退出这个空间，请使用「退出空间」")
        if not ident.membership(wid, uid):
            raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
        out = ident.remove_member(wid, uid, actor_id=s["user_id"], authorize=_authority(wid))
        if out is None:
            raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
        journaled = _journal_removal(wid, uid, out)
        audit(request, "member.remove", actor_id=s["user_id"], workspace_id=wid, object_kind="membership",
              object_id=uid, result="ok", meta={**out, "reason": reason, "journaled": journaled})
        ws = ident.get_workspace(wid) or {}
        _notify(request, uid, "member.removed", params={"workspace": ws.get("name"), "actor": _actor_name(s)},
                workspace_id=wid, object_kind="membership", object_id=uid, target={"origin": "accounts", "path": "/account#workspaces"})
        return {"removed": out, "journaled": journaled}

    def _journal_removal(wid: str, uid: str, out: dict) -> bool:
        """The removal and every invitation it cancelled, so a restore brings back neither."""
        ok = journal(store, "member_removed", workspace_id=wid, user_id=uid)
        for iid in out.get("cancelled_invitation_ids") or []:
            ok = journal(store, "invitation_revoked", invitation_id=iid) and ok
        return ok

    @r.post("/workspaces/{wid}/members/{uid}/remove")
    def remove_member(request: Request, wid: str, uid: str, body: dict = Body(...)):
        s, m, _ = _actor(request, wid)
        reason = _dangerous(request, s, body)
        return {"ok": True, **_remove_one(request, s, m, wid, uid, reason)}

    @r.post("/workspaces/{wid}/members/remove")
    def remove_members(request: Request, wid: str, body: dict = Body(...)):
        """Bulk removal: `{"user_ids": [...], "reason"}`. Each person is removed (or refused) on
        their own; a sole owner in the list fails with `last_owner` and nobody else is held up."""
        s, m, _ = _actor(request, wid)
        reason = _dangerous(request, s, body)
        results = [_result(uid, lambda uid=uid: _remove_one(request, s, m, wid, uid, reason))
                   for uid in _ids(body, "user_ids")]
        return _bulk(results)

    # ------------------------------------------------------------------ entitlements
    def _entitle_one(request: Request, s: dict, m: dict, ws: dict, uid: str, app_id: str,
                     grant, reason: str | None) -> dict:
        if not isinstance(grant, bool):
            raise ServiceError(400, "bad_grant", "grant 必须是 true 或 false", {"field": "grant"})
        if uid == s["user_id"]:
            raise ServiceError(403, "self_entitlement", "不能修改自己的应用使用资格")
        target = ident.membership(ws["id"], uid)
        if not target:
            raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
        if target["role"] not in _manageable(m):
            raise ServiceError(403, "role_too_high", "不能管理与自己同级或更高角色的成员")
        if grant:
            _grants_allowed(ws)
        out = ident.set_entitlement(ws["id"], uid, app_id, grant=grant, actor_id=s["user_id"],
                                    authorize=_authority(ws["id"], grant=grant))
        journaled = None
        if not grant and out["before"] != "revoked":
            journaled = journal(store, "entitlement_revoked", workspace_id=ws["id"], app_id=app_id, user_id=uid)
        audit(request, "member.entitlement", actor_id=s["user_id"], workspace_id=ws["id"],
              object_kind="entitlement", object_id=uid, result="ok",
              meta={**out, "reason": reason, "journaled": journaled})
        return {**out, "journaled": journaled}

    @r.post("/workspaces/{wid}/members/{uid}/entitlements")
    def set_entitlement(request: Request, wid: str, uid: str, body: dict = Body(...)):
        s, m, ws = _actor(request, wid)
        app_id = str(body.get("app_id") or DEFAULT_APP).strip()
        reason = str(body.get("reason") or "").strip()[:500] or None
        return {"ok": True, **_entitle_one(request, s, m, ws, uid, app_id, body.get("grant"), reason)}

    @r.post("/workspaces/{wid}/members/entitlements")
    def set_entitlements(request: Request, wid: str, body: dict = Body(...)):
        """Bulk: `{"app_id"?, "changes": [{"user_id", "grant": bool}], "reason"?}`."""
        s, m, ws = _actor(request, wid)
        app_id = str(body.get("app_id") or DEFAULT_APP).strip()
        reason = str(body.get("reason") or "").strip()[:500] or None
        changes = body.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ServiceError(400, "missing_changes", "请提供 changes（非空列表）", {"field": "changes"})
        if len(changes) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多处理 {BULK_MAX} 项", {"field": "changes"})
        results = []
        for ch in changes:
            ch = ch if isinstance(ch, dict) else {}
            uid = str(ch.get("user_id") or "").strip()
            results.append(_result(uid, lambda ch=ch, uid=uid: _entitle_one(
                request, s, m, ws, uid, app_id, ch.get("grant"), reason)))
        return _bulk(results)

    # ------------------------------------------------------------------ ownership
    @r.post("/workspaces/{wid}/ownership/transfer")
    def transfer(request: Request, wid: str, body: dict = Body(...)):
        """`{"to_user_id", "demote_self_to": "admin"|"editor"|"viewer"|null, "reason"}`."""
        s, m, ws = _actor(request, wid)
        if m["role"] != "owner":
            raise ServiceError(403, "not_owner", "只有空间所有者可以移交所有权")
        # Making someone an owner is the largest grant there is; a frozen workspace gains none.
        _grants_allowed(ws)
        reason = _dangerous(request, s, body)
        to = str(body.get("to_user_id") or "").strip()
        if not to:
            raise ServiceError(400, "missing_user", "请选择接收所有权的成员", {"field": "to_user_id"})
        demote = body.get("demote_self_to") or None
        out = ident.transfer_ownership(wid, s["user_id"], to, demote_self_to=demote)
        # Only the caller's demotion withdraws access; the new owner's promotion is not journalled.
        # Record after the transfer commits so a refused transfer cannot leave a withdrawal.
        # As for other withdrawals, a journal failure is reported without restoring old access.
        journaled = None
        if demote:
            journaled = journal(store, "role_changed", workspace_id=wid, user_id=s["user_id"], role=demote)
        audit(request, "workspace.ownership.transfer", actor_id=s["user_id"], workspace_id=wid,
              object_kind="workspace", object_id=wid, result="ok",
              meta={"from": s["user_id"], "to": to, "self_role": out["self_role"],
                    "target_previous_role": out["target_previous_role"], "reason": reason,
                    "journaled": journaled})
        return {"ok": True, **out, "journaled": journaled}

    @r.post("/workspaces/{wid}/leave")
    def leave(request: Request, wid: str, body: dict = Body(default={})):
        """Leave a workspace yourself. A sole active owner gets 409 last_owner: hand over first."""
        s, m, _ = _actor(request, wid, manage=False)
        out = ident.remove_member(wid, s["user_id"])
        if out is None:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        journaled = _journal_removal(wid, s["user_id"], out)
        audit(request, "member.leave", actor_id=s["user_id"], workspace_id=wid, object_kind="membership",
              object_id=s["user_id"], result="ok", meta={**out, "journaled": journaled})
        ws = ident.get_workspace(wid) or {}
        for admin in store.all("SELECT user_id FROM memberships WHERE workspace_id=? AND role IN ('owner','admin')", (wid,)):
            _notify(request, admin["user_id"], "member.left", params={"member": _actor_name(s), "workspace": ws.get("name")},
                    workspace_id=wid, object_kind="membership", object_id=s["user_id"],
                    target={"origin": "accounts", "path": f"/account/workspaces/{wid}/members"})
        return {"ok": True, "removed": out, "journaled": journaled}

    # ================================================================== invitations
    from .invites import INVITE_TTL_MAX_S, INVITE_TTL_MIN_S, INVITE_TTL_S, ROLE_LABEL
    from . import mail as mail_mod

    def _ttl(body: dict) -> int:
        days = body.get("expires_in_days")
        if days is None:
            return INVITE_TTL_S
        try:
            ttl = int(float(days) * 86400)
        except (TypeError, ValueError):
            raise ServiceError(400, "bad_expiry", "expires_in_days 必须是数字", {"field": "expires_in_days"}) from None
        if not INVITE_TTL_MIN_S <= ttl <= INVITE_TTL_MAX_S:
            raise ServiceError(400, "bad_expiry", "邀请有效期必须在 1 到 30 天之间", {"field": "expires_in_days"})
        return ttl

    def issue_invitation(request: Request, s: dict, wid: str, email: str, role: str, can_export,
                         ttl_s: int, *, action: str = "invite.create", resend_of: str | None = None) -> dict:
        """Create (or re-create) an invitation and queue its mail. Raises on any refusal.

        An invitation whose mail cannot be queued is withdrawn rather than left as a link nobody
        will ever receive. Every earlier link this one supersedes (a resend, or re-inviting the
        same address) is journalled as revoked, so a restore cannot revive the link a resend was
        meant to kill. A frozen workspace answers 409 `workspace_frozen` from `invites.create`
        (for a new invitation and a resend alike), after the caller's own authority is checked."""
        if resend_of:
            row, raw = invites.resend(resend_of, s["user_id"], ttl_s=ttl_s)
        else:
            row, raw = invites.create(workspace_id=wid, email=email, role=role, actor_id=s["user_id"],
                                      can_export=can_export, ttl_s=ttl_s)
        for old in row.get("superseded_ids") or []:
            journal(store, "invitation_revoked", invitation_id=old)
        ws = ident.get_workspace(row["workspace_id"])
        me = ident.get_user(s["user_id"])
        recipient = ident.by_email(row["email_key"])
        locale = (recipient or {}).get("locale") or (me or {}).get("locale") or "zh-Hans"
        store.execute("UPDATE invitations SET locale=? WHERE id=?", (locale, row["id"]))
        subject, text, html = mail_mod.invitation_mail(
            workspace=ws["name"], inviter=me.get("display_name") or me["email"],
            role_label=ROLE_LABEL.get(row["role"], row["role"]), token=raw,
            expires_h=max(1, int(ttl_s // 3600)), locale=locale)
        try:
            mailer.enqueue(row["email_key"], subject, text, kind="invite", ref_id=row["id"], html=html)
        except ServiceError:
            invites.revoke(row["id"], s["user_id"])
            raise
        audit(request, action, actor_id=s["user_id"], workspace_id=row["workspace_id"],
              object_kind="invitation", object_id=row["id"], result="ok",
              meta={"role": row["role"], "resend_of": resend_of})
        if recipient:
            # The person already has an account: the inbox carries the invitation too. The mail
            # keeps the link; the inbox item leads to the onboarding page, which lists what is
            # waiting and says to open the mailed link — a click here never accepts anything.
            _notify(request, recipient["id"], "invitation.received",
                    params={"workspace": ws["name"], "inviter": me.get("display_name") or me["email"], "role": row["role"]},
                    workspace_id=row["workspace_id"], object_kind="invitation", object_id=row["id"],
                    dedupe_key=f"invite:{row['id']}", actionable=True, target={"origin": "accounts", "path": "/onboarding"})
        return {"id": row["id"], "email": row["email_key"], "role": row["role"],
                "can_export": bool(row["can_export"]), "expires_at": row["expires_at"], "mail": "queued"}

    r.issue_invitation = issue_invitation             # the single-invite route in auth_routes uses it
    r.invite_ttl = _ttl

    @r.post("/workspaces/{wid}/invitations/batch")
    def invite_many(request: Request, wid: str, body: dict = Body(...)):
        """`{"invitations": [{"email", "role", "can_export"?}], "expires_in_days"?}` → per-address results."""
        s, _, ws = _actor(request, wid)
        items = body.get("invitations")
        if not isinstance(items, list) or not items:
            raise ServiceError(400, "missing_invitations", "请提供 invitations（非空列表）", {"field": "invitations"})
        if len(items) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多处理 {BULK_MAX} 项", {"field": "invitations"})
        ttl = _ttl(body)
        results = []
        for it in items:
            it = it if isinstance(it, dict) else {}
            email = str(it.get("email") or "").strip()
            role = str(it.get("role") or "viewer").strip()
            results.append(_result(email, lambda email=email, role=role, it=it: {
                "invitation": issue_invitation(request, s, wid, email, role, it.get("can_export"), ttl)}))
        return _bulk(results)

    def _invite_in(iid: str, request: Request) -> tuple[dict, dict, dict]:
        """The invitation and its workspace, for a caller who governs that workspace."""
        row = invites.get(iid)
        if not row:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        s, _, ws = _actor(request, row["workspace_id"])
        return s, row, ws

    @r.patch("/invitations/{iid}")
    def edit_invitation(request: Request, iid: str, body: dict = Body(...)):
        """`{"role"?, "can_export"?}` on a pending invitation; 409 otherwise. Raising the role or
        turning export on in a frozen workspace is 409 `workspace_frozen`; lowering is allowed."""
        s, row, ws = _invite_in(iid, request)
        role = str(body.get("role") or "").strip() or None
        can_export = body.get("can_export")
        if can_export is not None and not isinstance(can_export, bool):
            raise ServiceError(400, "bad_can_export", "can_export 必须是 true 或 false", {"field": "can_export"})
        if (role and ROLE_RANK.get(role, -1) > ROLE_RANK.get(row["role"], 0)) or \
                (can_export is True and not row["can_export"]):
            _grants_allowed(ws)
        out = invites.update(iid, s["user_id"], role=role, can_export=can_export)
        audit(request, "invite.update", actor_id=s["user_id"], workspace_id=row["workspace_id"],
              object_kind="invitation", object_id=iid, result="ok",
              meta={"before": out["before"], "after": {"role": out["role"], "can_export": bool(out["can_export"])}})
        return {"ok": True, "invitation": {"id": out["id"], "email": out["email_key"], "role": out["role"],
                                           "can_export": bool(out["can_export"]), "state": out["state"],
                                           "expires_at": out["expires_at"]}}

    @r.post("/invitations/{iid}/resend")
    def resend_invitation(request: Request, iid: str, body: dict = Body(default={})):
        """A new link for the same address and role; the old link stops working. 409
        `workspace_frozen` in a frozen workspace."""
        s, row, _ = _invite_in(iid, request)
        try:
            out = issue_invitation(request, s, row["workspace_id"], row["email_key"], row["role"],
                                   bool(row["can_export"]), _ttl(body or {}), action="invite.resend",
                                   resend_of=iid)
        except ServiceError as e:
            if e.status == 429:
                return error_response(request, e)
            raise
        return {"ok": True, "invitation": out}

    @r.post("/workspaces/{wid}/invitations/bulk")
    def invitations_bulk(request: Request, wid: str, body: dict = Body(...)):
        """`{"action": "revoke"|"resend", "ids": [...]}` → per-invitation results."""
        s, _, _ = _actor(request, wid)
        action = str(body.get("action") or "").strip()
        if action not in ("revoke", "resend"):
            raise ServiceError(400, "bad_action", "action 只能是 revoke 或 resend", {"field": "action"})
        ttl = _ttl(body)
        results = []
        for iid in _ids(body, "ids"):
            def one(iid=iid):
                row = invites.get(iid)
                if not row or row["workspace_id"] != wid:
                    raise ServiceError(404, "no_invite", "找不到该邀请")
                if action == "revoke":
                    if row["accepted_at"] or not invites.revoke(iid, s["user_id"]):
                        raise ServiceError(409, "invite_accepted", "这个邀请已经被接受了")
                    journaled = journal(store, "invitation_revoked", invitation_id=iid)
                    audit(request, "invite.revoke", actor_id=s["user_id"], workspace_id=wid,
                          object_kind="invitation", object_id=iid, result="ok",
                          meta={"journaled": journaled})
                    return {"journaled": journaled}
                return {"invitation": issue_invitation(request, s, wid, row["email_key"], row["role"],
                                                       bool(row["can_export"]), ttl, action="invite.resend",
                                                       resend_of=iid)}
            results.append(_result(iid, one))
        return _bulk(results)

    return r
