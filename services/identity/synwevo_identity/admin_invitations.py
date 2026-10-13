"""Platform oversight of invitations across workspaces (A47): find the pending, expired and
mail-failed invitations of every workspace, and withdraw an abusive one with a written reason.

An invitation is a mail sent from this service in a workspace's name, and a link that adds whoever
holds it. A compromised or abusive workspace administrator can turn invitations into spam or into
a way in; the workspace's own page (`GET /api/auth/workspaces/{wid}/invitations`) shows them only
to that workspace's administrators. This is the platform's view and its one act, held to the rules
of the rest of the console:

* **Metadata, never the link.** The invitee's address is never returned: `invitee.masked` is the
  first character of the local part, `***` and the domain (`a***@example.com`,
  `invites.masked_address` — the mail tab masks invite mails with the same function); a search
  finds an invitee only by the exact address or by `@domain` (`invites.invitee_search`, the mail
  tab's rule for invite mails too), never by a fragment. Whether an account holds the address is
  shown only to a reader of accounts (`platform.users.view`), so the list is no account-existence
  oracle for a role without it. No token, token hash or link is selected, and a mail's stored
  error is shown as its classified code (`mail.public_error_code`, the mail tab's), never as
  transport text. The inviter is an account and is named as the workspace member list names members.
* **The tenant's withdrawal, the platform's authority.** The revoke is the UPDATE the workspace's
  own revoke makes (`Invites.platform_revoke`), authorized by `platform.workspaces.manage` instead of
  the inviter's role — a platform administrator is no member of the workspace. In one write
  transaction with the re-admission (`_write`): the UPDATE, the security-journal entry
  `invitation_revoked` (a restore does not bring the link back), the workspace's own audit record
  `invite.revoke` filed under the invitation's workspace with `by: platform` (its organization's
  log shows it as the platform's, and the workspace's own list says `revoked_by: "platform"`), and
  the platform's record `admin.invitation.revoke`. The journal entry is written before the audit
  records and the commit, so a failure after it rolls the revoke back but leaves the entry, which a
  restore applies: a restrictive intent that fails closed, as `journal.record_organization`'s does.
  After the commit the invitee's to-do closes as the tenant route closes it. The link then fails at
  preview (state `revoked`) and at acceptance (409 `invite_revoked`); the inviter may invite the
  address again — the platform withdrew one link, it did not bar an address.
* **What cannot be undone is refused.** An accepted invitation is a membership now, a declined
  one was answered: 409 `invite_not_pending`. One already revoked answers `already`, writing nothing.
* **A batch is a set of single acts.** `POST /invitations/revoke` takes up to 100 ids and runs each
  in its own transaction through the same function, reporting each in the bulk-result shape, with
  one batch record `admin.invitation.revoke_bulk` naming the counts — written however the batch ended.
* **Reading takes no lock.** The list is read in one deferred read transaction (`_one_snapshot`):
  every statement of a page sees the same committed state, and no writer waits for it.
"""
from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import audit_denied, require_platform
from synwevo_core.store import Store, new_id, now

from .invites import Invites, invitee_search, like_escape, masked_address

VIEW = "platform.workspaces.view"
MANAGE = "platform.workspaces.manage"
#: Whether an account holds an invitee's address is account search, as the users list is.
ACCOUNTS_VIEW = "platform.users.view"
BULK_MAX = 100
REASON_MIN, REASON_MAX = 8, 500
#: What a submitted invitation id may look like (ids are `iv_` + hex); anything else is not an id.
INVITATION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
STATE_FILTER = "^(pending|expired|mail_failed|revoked|accepted|declined|all)$"
GROUPS = ("pending", "expired", "mail_failed", "revoked", "accepted", "declined")
#: Outbox states of an invite mail that will not reach the invitee: the `mail_failed` group.
UNDELIVERED = ("failed", "dropped")
#: Refusals of the caller's own admission, not of an invitation: a batch stops at the first.
ADMISSION_CODES = frozenset({"unauthorized", "not_found", "platform_permission_denied",
                             "step_up_required", "reauth_required"})
FAILED, STOPPED = "admin_operation_failed", "batch_stopped"
RESULT_MESSAGES = {"ok": "邀请已撤销", "already_revoked": "该邀请此前已被撤销",
                   FAILED: "这一项未能完成，没有任何改动；批次在此停止",
                   STOPPED: "批次在前一项出错后停止，这一项没有执行"}

#: `Invites._state` in SQL (one parameter: the time), so the filter and the counts are the page's.
_STATE_SQL = ("CASE WHEN i.accepted_at IS NOT NULL THEN 'accepted' WHEN i.declined_at IS NOT NULL THEN 'declined' "
              "WHEN i.revoked_at IS NOT NULL THEN 'revoked' WHEN i.expires_at<=? THEN 'expired' ELSE 'pending' END")
#: The latest invite mail's outbox status, asked only of a pending invitation — the only state the
#: `mail_failed` group and filter read it for (`ix_mail_notification_ref` serves it).
_MAIL_SQL = ("CASE WHEN y.st='pending' THEN (SELECT m.status FROM mail_outbox m WHERE m.kind='invite' "
             "AND m.ref_id=y.id ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) END")
_UNDELIVERED_SQL = "x.st='pending' AND x.ms IN ('failed','dropped')"


def build_router(store: Store, ident: Identity, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin. No parent route
    shares the `/invitations` prefix."""
    r = APIRouter(tags=["admin"])
    invites = Invites(store, ident)

    def _admin(request: Request, permission: str, *, dangerous: bool = False) -> dict:
        """admin_routes `_admin`: the platform permission, the session step-up and, for the
        dangerous operations, the strict re-authentication (platform_access.require_platform)."""
        return require_platform(request, permission, store=store, audit=audit, dangerous=dangerous)

    @contextmanager
    def _write(request: Request, permission: str, *, object_kind: str, object_id: str):
        """admin_routes `_write`: revalidate under the writer lock; retain denials after rollback."""
        admitted = _admin(request, permission, dangerous=True)
        try:
            with store.tx():
                yield _admin(request, permission, dangerous=True)
        except ServiceError as exc:
            if exc.status < 500:
                audit_denied(store, audit, request, admitted["id"], object_id or permission,
                             object_kind=object_kind, code=exc.code)
            raise

    @contextmanager
    def _one_snapshot():
        """Every read inside sees one committed state, and nothing waits for it.

        `Store.tx()` is a write transaction (BEGIN IMMEDIATE and the process's writer lock):
        `admin_routes._page` reads under it, which makes every business write wait for a page of
        the console. `admin_runs._page` reads with plain statements instead, each its own moment.
        A page here is several statements over the same rows — the high-water mark, the groups,
        the page, its mails and labels — so it opens a deferred transaction on this thread's
        connection (`Store.conn`): under WAL its first read fixes the snapshot every later
        statement sees, it takes no write lock, and writers carry on beside it. Read-only by
        construction (nothing inside writes; a write would fail loudly rather than escalate),
        and always ended with a ROLLBACK, which for a reader only releases the snapshot."""
        with store.conn() as c:
            if c.in_transaction:           # inside a caller's transaction, whose view already is one
                yield
                return
            c.execute("BEGIN DEFERRED")
            try:
                yield
            finally:
                if c.in_transaction:
                    c.execute("ROLLBACK")

    def _reason(body: dict) -> str:
        reason = body.get("reason")
        if not isinstance(reason, str) or not REASON_MIN <= len(reason.strip()) <= REASON_MAX:
            raise ServiceError(400, "missing_reason", f"请写明理由（{REASON_MIN} 至 {REASON_MAX} 个字）")
        return reason.strip()

    def _ids(raw) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise ServiceError(400, "missing_ids", "请提供要撤销的邀请（非空列表）", {"field": "ids"})
        if len(raw) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多撤销 {BULK_MAX} 个邀请", {"field": "ids", "limit": BULK_MAX})
        if not all(isinstance(v, str) and INVITATION_ID.fullmatch(v) for v in raw):
            raise ServiceError(400, "missing_ids", "邀请编号格式无效", {"field": "ids"})
        return list(dict.fromkeys(raw))

    # ------------------------------------------------------------------ the list
    def _source(ts: float, snapshot: int, q: str, workspace_id: str) -> tuple[str, list]:
        """Every invitation up to the high-water row, with its state and (pending ones) latest mail
        status, under the search and workspace filters (never the state filter: the groups count
        every state)."""
        where, args = ["i.rowid<=?"], [snapshot]
        if workspace_id:
            where.append("i.workspace_id=?")
            args.append(workspace_id)
        if q:
            alternatives, extra = ["w.name LIKE ? ESCAPE '\\'"], [f"%{like_escape(q)}%"]
            exact, domain = invitee_search(q)
            if exact:
                alternatives.append("i.email_key=?")
                extra.append(exact)
            elif domain:
                alternatives.append("i.email_key LIKE ? ESCAPE '\\'")
                extra.append(domain)
            where.append("(" + " OR ".join(alternatives) + ")")
            args += extra
        inner = (f"SELECT i.id, i.email_key, i.workspace_id, i.role, i.invited_by, i.invited_via, i.created_at, "
                 f"i.expires_at, i.accepted_at, i.revoked_at, i.declined_at, {_STATE_SQL} AS st, "
                 "w.name AS ws_name, w.kind AS ws_kind, w.status AS ws_status, w.organization_id AS ws_org "
                 "FROM invitations i LEFT JOIN workspaces w ON w.id=i.workspace_id WHERE " + " AND ".join(where))
        return f"SELECT y.*, {_MAIL_SQL} AS ms FROM ({inner}) y", [ts, *args]

    def _latest_mail(ids: list[str]) -> dict:
        if not ids:
            return {}
        rows = store.all("SELECT ref_id, status, attempts, last_error FROM mail_outbox WHERE kind='invite' "
                         f"AND ref_id IN ({','.join('?' * len(ids))}) ORDER BY created_at, rowid", tuple(ids))
        return {row["ref_id"]: row for row in rows}           # the last row per invitation wins

    def _item(row: dict, mail: dict | None, locale: str, accounts: bool) -> dict:
        from .mail import public_error_code
        masked, domain = masked_address(row["email_key"])
        via = None
        if row["invited_via"]:
            from .workspace_actor import actor_label
            via = actor_label(store, row["invited_via"], row["invited_by"], locale)
        return {
            "id": row["id"], "state": row["st"], "role": row["role"],
            "created_at": row["created_at"], "expires_at": row["expires_at"], "revoked_at": row["revoked_at"],
            "accepted_at": row["accepted_at"], "declined_at": row["declined_at"],
            "invitee": {"masked": masked, "domain": domain,
                        "has_account": bool(row["has_account"]) if accounts else None},
            "workspace": {"id": row["workspace_id"], "name": row["ws_name"], "kind": row["ws_kind"],
                          "status": row["ws_status"], "organization_id": row["ws_org"]},
            "inviter": ({"id": row["inviter_id"], "email": row["inviter_email"], "display_name": row["inviter_name"]}
                        if row["inviter_id"] else None),
            "via": via,
            "mail": ({"status": mail["status"], "attempts": mail["attempts"],
                      "last_error_code": public_error_code(mail["last_error"])} if mail else None),
        }

    @r.get("/invitations")
    def list_invitations(request: Request, state: str = Query("pending", pattern=STATE_FILTER),
                         q: str = Query("", max_length=100), workspace_id: str = Query("", max_length=100),
                         limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=2**63 - 1),
                         snapshot: int | None = Query(None, ge=0, le=2**63 - 1)):
        """Every workspace's invitations, newest first. `state` (default `pending`) filters; `all`
        lists every state, `mail_failed` the pending ones whose latest invite mail failed or was
        dropped. `q` (≤100) is an exact invitee address, an `@domain` suffix, or a substring of the
        workspace name; `workspace_id` one workspace. `groups` counts each state under `q`,
        `workspace_id` and the snapshot, ignoring `state`; `mail_failed` is a part of `pending`
        (the five states add up to everything). Paged by `limit`/`offset` with the console's
        high-water `snapshot`: invitations issued while paging stay out of later pages.
        `invitee.has_account` is null for a caller without `platform.users.view`."""
        actor = _admin(request, VIEW)
        accounts = ACCOUNTS_VIEW in actor["platform_permissions"]
        q, workspace_id = q.strip(), workspace_id.strip()
        ts = now()
        with _one_snapshot():
            ceiling = store.one("SELECT COALESCE(MAX(rowid), 0) AS n FROM invitations")["n"]
            snapshot = ceiling if snapshot is None else min(snapshot, ceiling)
            source, args = _source(ts, snapshot, q, workspace_id)
            groups = dict.fromkeys(GROUPS, 0)
            for g in store.all("SELECT x.st AS st, COUNT(*) AS n, "
                               f"SUM(CASE WHEN {_UNDELIVERED_SQL} THEN 1 ELSE 0 END) AS undelivered "
                               f"FROM ({source}) x GROUP BY x.st", tuple(args)):
                groups[g["st"]] = g["n"]
                groups["mail_failed"] += g["undelivered"] or 0
            if state == "all":
                where, extra = "", []
                total = sum(groups[s] for s in GROUPS if s != "mail_failed")
            elif state == "mail_failed":
                where, extra = f"WHERE {_UNDELIVERED_SQL}", []
                total = groups["mail_failed"]
            else:
                where, extra = "WHERE x.st=?", [state]
                total = groups[state]
            account = ("EXISTS (SELECT 1 FROM users a WHERE a.email_key=x.email_key AND a.deleted_at IS NULL)"
                       if accounts else "NULL")
            rows = store.all(
                "SELECT x.*, u.id AS inviter_id, u.email AS inviter_email, u.display_name AS inviter_name, "
                f"{account} AS has_account FROM ({source}) x LEFT JOIN users u ON u.id=x.invited_by {where} "
                "ORDER BY x.created_at DESC, x.id DESC LIMIT ? OFFSET ?", (*args, *extra, limit, offset))
            mails = _latest_mail([row["id"] for row in rows])
            locale = actor.get("locale") or "zh-Hans"
            items = [_item(row, mails.get(row["id"]), locale, accounts) for row in rows]
        return {"items": items, "total": total, "offset": offset, "limit": limit, "snapshot": snapshot,
                "groups": groups}

    # ------------------------------------------------------------------ revoking
    def _revoke(request: Request, iid: str, reason: str, *, batch_id: str | None = None) -> dict:
        """One invitation, in its own write transaction with the re-admission (see the module)."""
        with _write(request, MANAGE, object_kind="invitation", object_id=iid) as actor:
            done = invites.platform_revoke(iid)
            before = done["before"]
            invitation = {"id": iid, "state": "revoked", "revoked_at": done["revoked_at"]}
            if done["already"]:
                return {"already": True, "invitation": invitation, "journaled": None}
            from .withdrawals import record as journal
            # A withdrawn link stays withdrawn when an older database is restored. Before the audit
            # records: a failure after this point leaves a restrictive intent (see the module).
            journaled = journal(store, "invitation_revoked", invitation_id=iid)
            # The workspace's own record, under the invitation's workspace (never the
            # administrator's session workspace): its governance log shows the platform's act.
            audit(request, "invite.revoke", actor_id=actor["id"], workspace_id=before["workspace_id"],
                  object_kind="invitation", object_id=iid, result="ok",
                  meta={"by": "platform", "reason": reason, "journaled": journaled})
            meta = {"reason": reason, "workspace_id": before["workspace_id"], "state_before": done["state_before"],
                    "journaled": journaled}
            if batch_id:
                meta["batch_id"] = batch_id
            audit(request, "admin.invitation.revoke", actor_id=actor["id"], workspace_given=True,
                  object_kind="invitation", object_id=iid, result="ok", meta=meta)
        from .auth_routes import _inbox_invitation_closed
        _inbox_invitation_closed(request, iid, before, "invitation.revoked")
        return {"already": False, "invitation": invitation, "journaled": journaled}

    @r.post("/invitations/revoke")
    def revoke_invitations(request: Request, body: dict = Body(...)):
        """`{"ids": [1..100 ids], "reason"}` → each invitation revoked in its own transaction:
        `{ok, batch_id, succeeded, failed, results: [{id, ok, code, message}]}` in the submitted
        order (duplicates collapsed). `ok`/`already_revoked` are successes; `invite_not_pending` and
        `no_invite` are not, and the others go on. A refusal of the caller's own admission (the
        re-authentication expired, the grant ended) stops the batch: the rest report it. Any other
        failure stops it too: that item is `admin_operation_failed` (its transaction rolled back)
        and the rest `batch_stopped`. The batch record is written however the batch ended."""
        actor = _admin(request, MANAGE, dangerous=True)
        body = body if isinstance(body, dict) else {}
        reason = _reason(body)
        ids = _ids(body.get("ids"))
        batch_id = new_id("ivb")
        results: list[dict] = []
        halted: ServiceError | None = None
        broken = False
        try:
            for iid in ids:
                if broken:
                    results.append({"id": iid, "ok": False, "code": STOPPED, "message": RESULT_MESSAGES[STOPPED]})
                    continue
                if halted is not None:
                    results.append({"id": iid, "ok": False, "code": halted.code, "message": halted.message})
                    continue
                try:
                    out = _revoke(request, iid, reason, batch_id=batch_id)
                except ServiceError as exc:
                    if exc.code in ADMISSION_CODES:
                        halted = exc
                    results.append({"id": iid, "ok": False, "code": exc.code, "message": exc.message})
                    continue
                except Exception as exc:  # noqa: BLE001 - reported as a code; the text never leaves the log
                    print(f"[synwevo] invitation batch {batch_id} stopped at an item: {type(exc).__name__}", flush=True)
                    broken = True
                    results.append({"id": iid, "ok": False, "code": FAILED, "message": RESULT_MESSAGES[FAILED]})
                    continue
                code = "already_revoked" if out["already"] else "ok"
                results.append({"id": iid, "ok": True, "code": code, "message": RESULT_MESSAGES[code],
                                "journaled": out["journaled"]})
        finally:
            succeeded = sum(1 for item in results if item["ok"])
            failed = len(ids) - succeeded
            with store.tx():
                audit(request, "admin.invitation.revoke_bulk", actor_id=actor["id"], workspace_given=True,
                      object_kind="invitation_batch", object_id=batch_id,
                      result="error" if broken or len(results) < len(ids) else "denied" if halted else "ok",
                      meta={"total": len(ids), "succeeded": succeeded, "failed": failed,
                            "codes": dict(sorted(Counter(item["code"] for item in results).items())),
                            "reason": reason, "batch_id": batch_id})
        return {"ok": failed == 0, "batch_id": batch_id, "succeeded": succeeded, "failed": failed,
                "results": results}

    @r.post("/invitations/{iid}/revoke")
    def revoke_invitation(request: Request, iid: str, body: dict = Body(...)):
        """`{"reason"}` (8–500) → `{ok, already, invitation: {id, state, revoked_at}, journaled}`.
        404 `no_invite`; 409 `invite_not_pending` for an accepted or declined invitation; one
        already revoked answers `already: true` and writes nothing."""
        _admin(request, MANAGE, dangerous=True)
        reason = _reason(body if isinstance(body, dict) else {})
        return {"ok": True, **_revoke(request, iid, reason)}

    return r
