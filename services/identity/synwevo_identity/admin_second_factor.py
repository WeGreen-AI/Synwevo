"""The platform-controlled reset of a person's lost second factor (admin coverage A26).

A person who lost both their authenticator app and every recovery code cannot sign in with the
password any more (`Mfa.enabled_for` is the TOTP enrolment) and cannot turn the factor off
(that takes a current code). This is the platform's way back for them — a recovery act, never a
change made *as* the person, and never a look at their secrets:

* **state** (`GET /api/admin/users/{uid}/second-factor`, `platform.users.view`): whether TOTP is
  on, how many recovery codes are unused, how many passkeys are live, the pending reset (when it
  falls due, whether its announcement was delivered and so when it can really take effect, how
  the identity was confirmed, who asked) and the last ten resets. Counts and states only: no
  seed, no code, no token, no credential id.
* **preview** (`POST …/second-factor-reset/preview`, `platform.users.recover`, not dangerous):
  the refusals below, the factors and what the reset would remove — the authenticator, the
  recovery codes, the passkeys when `include_passkeys`, every session and token — with the time
  it would take effect (now + `SECOND_FACTOR_RESET_DELAY_S`) and a signed, five-minute plan
  (`SignedPreview`) over actor, target, origin, `include_passkeys` and a digest of the account's
  address, verification, status, factors (`factor_fingerprint`) and pending state.
* **request** (`POST …/second-factor-reset`, dangerous: strict re-authentication before anything
  in the body is read): how the identity was confirmed (`METHODS`), a written reason, the preview
  token and an idempotency key, as `AccountOperations.execute` takes them. In ONE write
  transaction: re-admission, the stored result of the same key (replayed verbatim), the refusals
  again, the plan against the account as it is now (`admin_preview_stale`), then the pending row
  — with a fresh cancel token (only its SHA-256 is stored), the announcement's mail id, the
  address it goes to and the factor fingerprint — the announcement in the outbox, the audit row
  and the durable result. **The mail and the row commit together**: `Mailer.enqueue` is an
  INSERT into `mail_outbox` on the same connection, so a refused enqueue (mail not configured, no
  public origin) rolls the request back and nothing waits unannounced; the worker sends it after
  the commit. The in-app notice is written after the commit, best effort, as every security
  notice is. Nothing about the account changes yet: the person still signs in with the
  authenticator until the reset takes effect. A due time sent by the client is never read.
* **cancel** — by the person, through the mailed link (`/api/auth/second-factor-reset/preview` and
  `/cancel` in account_routes.py, anonymous, the token is the authority, audited as the account:
  `auth.second_factor_reset.cancelled`; the link works for as long as the reset is pending, past
  its nominal due time too), or by a platform administrator (`POST …/second-factor-reset/{rid}/cancel`,
  `platform.users.recover`, dangerous, a reason). Either way nothing changed, and the account is told.
* **completion** (`complete_due_second_factor_resets`, run by the account service's sweeper): each
  reset past its due time in its own write transaction, re-read there.

  **The delay runs from delivery.** The person must have had the full delay to cancel *after the
  announcement reached them*: a reset completes only once its announcement mail is `sent` and
  `max(due_at, sent_at + SECOND_FACTOR_RESET_DELAY_S)` has passed (a mail delivered 60 hours late
  still leaves 72 hours). A mail that ended `failed` (any terminal failure, `delivery_unknown`
  included) or `dropped` (a restore), or that is still queued `ANNOUNCEMENT_GRACE_S` after the due
  time, voids the reset (`announcement_undelivered`); one still queued within that grace, or
  delivered less than the delay ago, waits for the next sweep.

  **Everything the request relied on is judged again** and, if it no longer holds, the reset is
  `void` with the reason (`VOID_REASONS`) and nothing about the account is touched: the account is
  deleted, not active, unverified, or its address is not the one announced to (`email_changed`);
  it holds a live platform grant or is an application identity; the administrator who asked no
  longer holds `platform.users.recover` effectively (`authority_changed` — a suspended, deleted
  or demoted requester's request does not outlive their authority, as a super administrator offer
  does not, admin_handover.py); nothing is left to remove (`nothing_to_reset`); or the factor rows
  changed — enrolled again, regenerated, or *used* to sign in, which proves the factor is not lost
  (`factors_changed`: the TOTP enrolment's times and its last accepted step, the unused recovery
  codes' ids, the included passkeys' ids, last use and signature counter, as an opaque digest).
  A suspension during the window voids the reset at once (`account_suspended`, in the suspension's
  own transaction, AccountOperations._apply). A void is audited as the system (below), the person
  is told — in words, never the code — unless the reason is about their own mailbox
  (`MAILBOX_REASONS`), and the administrator who asked gets an in-app notice (not when the reason
  is that they lost the authority).

  Otherwise the TOTP enrolment and every recovery code are deleted — never read: the seed is not
  decrypted, the code hashes are not selected — the passkeys are marked removed when asked for,
  every session, token and pending credential ends (`admin_operations.withdraw_account_access`),
  the account is told (mail in the same transaction, best effort; the notice after the commit),
  and — last — the security journal: a `passkey_removed` entry per removed passkey (as the
  person's own removal writes one) and the `sessions_revoked` entry, so a restore from an older
  backup removes those passkeys again and ends the restored sessions. Removing the TOTP enrolment
  and the recovery codes has no journal kind: a restore brings the old factor back, which is the
  safe direction — the person is again protected by the factor they had. A journal that cannot be
  written rolls the completion back; the reset stays pending and the next sweep tries again.

  Completion and voids are audited as the system (D7: `actor_kind=system`,
  `actor_id=SYSTEM_ACTOR`, action `admin.user.second_factor_reset.complete`, `result=denied` with
  `meta.code` for a void) — the administrator who asked is `meta.requested_by` and acted 72 hours
  earlier; a void at suspension also names `meta.suspended_by` and the suspension's
  `operation_id`. A second sweep finds nothing pending.

Refusals (preview, request): `no_user` (404); `self_reset` — an administrator manages their own
factors on the account page; `target_is_platform_admin` — an account holding a live platform
grant regains access through the super administrator handover, never through a reset another
administrator can start; `application_identity`; `account_state_conflict` — only an active
account; `email_unverified`; `recent_email_change` — the sign-in address changed less than
`RECENT_EMAIL_CHANGE_S` ago, so the announcement would go to an address the person may not hold
(the authoritative record is the `auth.email.changed` audit row: the one route that changes an
account's address writes it in the same transaction as the change); `nothing_to_reset`;
`reset_pending` — one pending reset per account (the partial unique index on
`second_factor_resets` backs this check).

A person without a password whose passkeys are removed is not locked out: the mailed password
reset (`/forgot`) proves the same mailbox this reset was announced to.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from fastapi import APIRouter, Body, Request

from synwevo_core.audit_change import change
from synwevo_core.auth import audit_event
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess, require_platform
from synwevo_core.store import Store, new_id

from .admin_operations import (OPERATIONS, PREVIEW_SECONDS, SignedPreview, canonical,
                               journal_sessions_revoked, withdraw_account_access)

#: How long a requested reset waits before it takes effect. Three days in which the account's own
#: address holds the announcement and its cancel link: long enough for a person who did not ask
#: for it to notice over a weekend, short enough that a person locked out is not locked out for a
#: week. Fixed, because the delay is the protection: no request, role or setting shortens it.
SECOND_FACTOR_RESET_DELAY_S = 72 * 3600

REQUEST = "admin.user.second_factor_reset.request"
CANCEL = "admin.user.second_factor_reset.cancel"
COMPLETE = "admin.user.second_factor_reset.complete"
#: `platform.users.recover` (platform_access.PERMISSIONS): the super administrator's only.
RECOVER = OPERATIONS[REQUEST].permission
VIEW = "platform.users.view"
#: How the administrator confirmed the person's identity outside the platform.
METHODS = ("in_person", "video_call", "verified_document", "other")
#: Who completes a reset (D7: the system, named by this label).
SYSTEM_ACTOR = "second-factor-reset"
#: `cancelled_by` when the person cancelled through the mailed link (otherwise the administrator's id).
HOLDER = "account_holder"
HISTORY_LIMIT = 10
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
#: The page the cancel link opens (the account shell, signed out).
LINK_PATH = "/account/second-factor-reset"
#: The revocation reason on every session and token a completed reset ends.
REVOKE_REASON = "second_factor_reset"
#: How long past its due time an announcement may still be waiting in the outbox before the reset
#: is voided: the mail worker retries for about two hours; a day is a stuck queue, not a slow one.
ANNOUNCEMENT_GRACE_S = 24 * 3600
#: An address changed this recently is not one a reset is announced to (`recent_email_change`).
RECENT_EMAIL_CHANGE_S = 7 * 86400
#: The audit row the one address-changing route (account_routes.email_confirm) writes in the same
#: transaction as the change: the authoritative record of when an account's address changed.
EMAIL_CHANGED_ACTION = "auth.email.changed"
#: Why a reset past its due time was not carried out (`second_factor_resets.void_reason`).
VOID_REASONS = ("account_deleted", "account_state_conflict", "account_suspended", "email_unverified",
                "email_changed", "target_is_platform_admin", "application_identity", "authority_changed",
                "announcement_undelivered", "nothing_to_reset", "factors_changed",
                # written by a restore's fence (apps/docs/scripts/snapshot.py), never by this module
                "restored_invalidated")
#: Void reasons about the account's own mailbox: the person is neither mailed nor notified.
MAILBOX_REASONS = frozenset({"account_deleted", "email_unverified", "email_changed", "announcement_undelivered"})
#: Where the requester's notice of a void points.
ADMIN_TARGET = {"origin": "accounts", "path": "/admin?tab=users"}
ACCOUNT_TARGET = {"origin": "accounts", "path": "/account#security"}


def factors(store: Store, uid: str) -> dict:
    """The account's second factors as counts: a confirmed TOTP enrolment, unused recovery codes,
    live passkeys. Read from the rows' existence only — never a seed or a code hash."""
    totp = store.one("SELECT 1 AS x FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL", (uid,)) is not None
    recovery = store.one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (uid,))["n"]
    passkeys = store.one("SELECT COUNT(*) AS n FROM passkeys WHERE user_id=? AND deleted_at IS NULL", (uid,))["n"]
    return {"totp": totp, "recovery_remaining": recovery, "passkeys": passkeys}


def factor_fingerprint(store: Store, uid: str, include_passkeys: bool) -> str:
    """An opaque digest of the factor rows a reset would remove, recorded at the request and
    compared at completion: the TOTP enrolment's times and its last accepted step (a sign-in with
    the authenticator moves it), the unused recovery codes' ids (spending or regenerating one
    changes the set) and — when they are included — the passkeys' ids, last use and signature
    counter. Changed means enrolled again or used: the factor is not lost. Never a seed, a code
    hash or a public key."""
    totp = store.one("SELECT created_at,confirmed_at,last_step FROM totp_secrets WHERE user_id=?", (uid,))
    recovery = [r["id"] for r in store.all(
        "SELECT id FROM recovery_codes WHERE user_id=? AND used_at IS NULL ORDER BY id", (uid,))]
    passkeys = [[r["id"], r["last_used_at"], r["sign_count"]] for r in store.all(
        "SELECT id,last_used_at,sign_count FROM passkeys WHERE user_id=? AND deleted_at IS NULL ORDER BY id",
        (uid,))] if include_passkeys else None
    return hashlib.sha256(canonical({"totp": totp, "recovery": recovery, "passkeys": passkeys}).encode()).hexdigest()


def nothing_to_reset(f: dict, include_passkeys: bool) -> bool:
    return not f["totp"] and not f["recovery_remaining"] and not (include_passkeys and f["passkeys"])


def recent_email_change(store: Store, uid: str) -> float | None:
    """When the account's address last changed, if less than `RECENT_EMAIL_CHANGE_S` ago."""
    at = store.one("SELECT MAX(at) AS at FROM audit_events WHERE action=? AND actor_id=? AND result='ok'",
                   (EMAIL_CHANGED_ACTION, uid))["at"]
    return at if at and at > time.time() - RECENT_EMAIL_CHANGE_S else None


def pending_reset(store: Store, uid: str) -> dict | None:
    return store.one("SELECT * FROM second_factor_resets WHERE user_id=? AND status='pending'", (uid,))


def announcement(store: Store, row: dict) -> dict:
    """The announcement mail's state, and — once it was sent — when the reset may really take
    effect: the full delay after delivery, never before the due time."""
    mail = store.one("SELECT status,sent_at FROM mail_outbox WHERE id=?",
                     (row["announcement_mail_id"],)) if row.get("announcement_mail_id") else None
    status = mail["status"] if mail else "missing"
    sent = mail["sent_at"] if mail and status == "sent" else None
    return {"status": status, "sent_at": sent,
            "effective_due_at": max(row["due_at"], sent + SECOND_FACTOR_RESET_DELAY_S) if sent else None}


def holder_due_at(store: Store, row: dict) -> float:
    """The time the cancel page shows: when the reset can take effect, as far as is known."""
    return announcement(store, row)["effective_due_at"] or row["due_at"]


def _void_code(store: Store, row: dict, user: dict | None) -> str | None:
    """Why a reset past its due time must not touch the account (everything the request relied
    on, judged again), or None."""
    if not user or user.get("deleted_at") or user.get("status") == "deleted":
        return "account_deleted"
    if user["status"] != "active":
        return "account_state_conflict"
    if not user.get("email_verified_at"):
        return "email_unverified"
    if user["email_key"] != row.get("announced_email_key"):
        return "email_changed"
    access = PlatformAccess(store)
    if access.holds_any_grant(user["id"]):
        return "target_is_platform_admin"
    if store.one("SELECT 1 AS x FROM app_accounts WHERE user_id=?", (user["id"],)):
        return "application_identity"
    if RECOVER not in access.effective_permissions(row["requested_by"]):
        return "authority_changed"
    include = bool(row["include_passkeys"])
    if nothing_to_reset(factors(store, user["id"]), include):
        return "nothing_to_reset"
    if factor_fingerprint(store, user["id"], include) != row.get("factor_digest"):
        return "factors_changed"
    return None


def notify(notifier, uid: str, what: str, **params) -> bool:
    """The in-app security notice (`security.change`, in the reader's language), best effort after
    the commit: the reset stands, or stays cancelled, whether or not the row lands."""
    return _emit(notifier, uid, "security.change", {"what": what, **params}, ACCOUNT_TARGET)


def _emit(notifier, uid: str, kind: str, params: dict, target: dict) -> bool:
    if notifier is None:
        return False
    try:
        notifier.emit(uid, kind, params=params, target=target)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[synwevo] notification not recorded ({kind}): {type(e).__name__}", flush=True)
        return False


def close_pending(store: Store, row: dict, *, by: str, mailer) -> bool:
    """Cancel a pending reset inside the caller's write transaction and queue the account's notice
    in the same transaction. `by` is the administrator's id or `HOLDER`. Returns whether the notice
    was queued: a cancellation stands even when mail cannot be sent (nothing about the account
    changed, and the audit row is the record)."""
    from .mail import second_factor_reset_cancelled_mail
    n = store.execute("UPDATE second_factor_resets SET status='cancelled',cancelled_at=?,cancelled_by=? "
                      "WHERE id=? AND status='pending'", (time.time(), by, row["id"]))
    if not n:
        raise RuntimeError("the reset is no longer pending")
    user = store.one("SELECT email,locale FROM users WHERE id=?", (row["user_id"],))
    if mailer is None or not user or not user["email"]:
        return False
    try:
        subject, text, html = second_factor_reset_cancelled_mail(user["locale"], by_holder=by == HOLDER)
        mailer.enqueue(user["email"], subject, text, html=html, kind="second_factor_reset_cancelled", ref_id=row["user_id"])
        return True
    except ServiceError:
        return False


def void_reset(store: Store, row: dict, code: str, *, mailer=None, notifier=None, extra: dict | None = None) -> None:
    """Void a pending reset inside the caller's write transaction: the row, the person's mail (best
    effort, not for a reason about their own mailbox), the system's audit row; after the commit
    the person's in-app notice and the requester's (not when they lost the authority)."""
    from synwevo_core.notifications import Notifier
    from .mail import second_factor_reset_void_mail
    uid = row["user_id"]
    if not store.execute("UPDATE second_factor_resets SET status='void',void_reason=? WHERE id=? AND status='pending'",
                         (code, row["id"])):
        raise RuntimeError("the reset is no longer pending")
    user = store.one("SELECT email,locale,deleted_at FROM users WHERE id=?", (uid,))
    tell = code not in MAILBOX_REASONS and bool(user and user["email"] and not user["deleted_at"])
    mailed = False
    if tell and mailer is not None:
        try:
            subject, text, html = second_factor_reset_void_mail(user["locale"], code)
            mailer.enqueue(user["email"], subject, text, html=html, kind="second_factor_reset_void", ref_id=uid)
            mailed = True
        except ServiceError:
            pass
    audit_event(store, COMPLETE, actor_kind="system", actor_id=SYSTEM_ACTOR, object_kind="user", object_id=uid,
                result="denied", meta={"code": code, "reset_id": row["id"], "requested_by": row["requested_by"],
                                       "mailed": mailed, **(extra or {}),
                                       **change({"status": "pending"}, {"status": "void"})})
    email = (user or {}).get("email") or ""

    def after():
        reader = notifier if notifier is not None else Notifier(store)
        if tell:
            notify(reader, uid, "second_factor_reset_void", void_reason=code)
        if code != "authority_changed":
            _emit(reader, row["requested_by"], "platform.second_factor_reset_void",
                  {"email": email, "void_reason": code}, ADMIN_TARGET)
    store.after_commit(after)


def void_for_suspension(store: Store, uid: str, *, request, suspended_by: str, operation_id: str) -> bool:
    """A26: an account suspended while a reset of its second factor waits — the reset is void at once,
    in the suspension's own transaction (AccountOperations._apply), audited as every void is (the
    system), naming the suspending administrator and the suspension's operation. Returns whether
    there was one."""
    row = pending_reset(store, uid)
    if not row:
        return False
    state = getattr(getattr(request, "app", None), "state", None)
    mailer = getattr(state, "mailer", None) or _default_mailer(store)
    void_reset(store, row, "account_suspended", mailer=mailer, notifier=getattr(state, "notifier", None),
               extra={"suspended_by": suspended_by, "operation_id": operation_id})
    return True


# ---------------------------------------------------------------------- completion (the sweeper)
def complete_due_second_factor_resets(store: Store, *, sessions=None, mailer=None, notifier=None) -> dict:
    """Carry out every pending reset past its due time whose announcement has been delivered for the
    full delay, each in its own write transaction; idempotent (a completed or void reset is never
    pending again). Returns the counts: `completed`; `void` (something the request relied on no
    longer holds; nothing touched); `waiting` (the announcement is still on its way, or was
    delivered less than the delay ago); `failed` (an error — the transaction rolled back and the
    next sweep tries again)."""
    from synwevo_core.notifications import Notifier
    from synwevo_core.sessions import Sessions
    out = {"completed": 0, "void": 0, "waiting": 0, "failed": 0}
    due = [r["id"] for r in store.all("SELECT id FROM second_factor_resets WHERE status='pending' AND due_at<=? "
                                       "ORDER BY due_at, id", (time.time(),))]
    if not due:
        return out
    sessions = sessions if sessions is not None else Sessions(store)
    mailer = mailer if mailer is not None else _default_mailer(store)
    notifier = notifier if notifier is not None else Notifier(store)
    for rid in due:
        try:
            outcome = _complete_one(store, rid, sessions=sessions, mailer=mailer, notifier=notifier)
        except Exception as e:  # noqa: BLE001 - one reset's failure must not stop the others
            print(f"[synwevo] second-factor reset {rid} not completed: {type(e).__name__}", flush=True)
            out["failed"] += 1
            continue
        if outcome:
            out[outcome] += 1
    return out


def _default_mailer(store: Store):
    from .mail import Mailer
    try:
        return Mailer(store, start=False)
    except ServiceError:
        return None


def _complete_one(store: Store, rid: str, *, sessions, mailer, notifier) -> str | None:
    from synwevo_core import journal
    from .mail import second_factor_reset_completed_mail
    with store.tx():
        ts = time.time()
        row = store.one("SELECT * FROM second_factor_resets WHERE id=? AND status='pending' AND due_at<=?", (rid, ts))
        if not row:
            return None
        uid = row["user_id"]
        user = store.one("SELECT * FROM users WHERE id=?", (uid,))
        code = _void_code(store, row, user)
        if code is None:
            mail = announcement(store, row)
            if mail["sent_at"] is not None:
                if ts < mail["effective_due_at"]:
                    return "waiting"
            elif mail["status"] == "queued" and ts <= row["due_at"] + ANNOUNCEMENT_GRACE_S:
                return "waiting"
            else:
                code = "announcement_undelivered"
        if code:
            void_reset(store, row, code, mailer=mailer, notifier=notifier)
            return "void"
        include = bool(row["include_passkeys"])
        before = factors(store, uid)
        # Deleted, never read: no seed is decrypted and no code hash is selected.
        store.execute("DELETE FROM totp_secrets WHERE user_id=?", (uid,))
        store.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
        removed_passkeys = [r["id"] for r in store.all(
            "SELECT id FROM passkeys WHERE user_id=? AND deleted_at IS NULL ORDER BY id", (uid,))] if include else []
        for pid in removed_passkeys:
            # Marked removed as `Passkeys.remove` marks one (deleted_at), so the account's own list,
            # sign-in and re-authentication stop offering it; the row stays for the record.
            store.execute("UPDATE passkeys SET deleted_at=? WHERE id=? AND deleted_at IS NULL", (ts, pid))
        n, tokens = withdraw_account_access(store, sessions, uid, REVOKE_REASON)
        store.execute("UPDATE second_factor_resets SET status='completed',completed_at=? WHERE id=? AND status='pending'",
                      (ts, rid))
        mailed = False
        if mailer is not None and user["email"]:
            try:
                subject, text, html = second_factor_reset_completed_mail(user["locale"], passkeys_removed=bool(removed_passkeys))
                mailer.enqueue(user["email"], subject, text, html=html, kind="second_factor_reset_completed", ref_id=uid)
                mailed = True
            except ServiceError:
                pass        # the reset was announced at least 72 hours ago; this notice is best effort
        after = factors(store, uid)
        removed = {"totp": before["totp"], "recovery_codes": before["recovery_remaining"], "passkeys": len(removed_passkeys)}
        audit_event(store, COMPLETE, actor_kind="system", actor_id=SYSTEM_ACTOR, object_kind="user", object_id=uid,
                    result="ok", meta={"reset_id": rid, "requested_by": row["requested_by"], "removed": removed,
                                       "sessions": n, "tokens": tokens, "mailed": mailed, "journaled": True,
                                       **change({"status": "pending", **before, "sessions": n, "tokens": tokens},
                                                {"status": "completed", **after, "sessions": 0, "tokens": 0})})
        # Last, as every withdrawal: a journal that cannot be written rolls all of this back, and
        # the next sweep tries again. Each removed passkey as the person's own removal journals
        # it, then the sessions; the TOTP enrolment and recovery codes have no kind (a restore
        # bringing the old factor back leaves the person protected by it).
        for pid in removed_passkeys:
            journal.record(store, "passkey_removed", credential_id=pid)
        journal_sessions_revoked(store, uid, rid)
        store.after_commit(lambda: notify(notifier, uid, "second_factor_reset_completed"))
    return "completed"


# ---------------------------------------------------------------------- administration
class SecondFactorResets(SignedPreview):
    action = REQUEST

    def __init__(self, store: Store, ident: Identity, audit, mailer=None):
        self.store, self.ident, self.audit, self._mailer = store, ident, audit, mailer

    def _mail(self, request):
        sender = self._mailer if self._mailer is not None else getattr(request.app.state, "mailer", None)
        if sender is None:
            raise ServiceError(503, "mail_not_configured", "邮件服务尚未配置")
        return sender

    def _actor(self, request, *, execute=False):
        return require_platform(request, RECOVER, store=self.store, audit=self.audit, dangerous=execute)

    def _target(self, actor: dict, uid: str, include: bool) -> tuple[dict, dict]:
        """The account and its factors if a reset may be requested for it now; the refusal otherwise.
        Under the writer lock the caller holds."""
        target = self.ident.get_user(uid)
        if not target:
            raise ServiceError(404, "no_user", "找不到该账户")
        if uid == actor["id"]:
            raise ServiceError(409, "self_reset", "不能重置自己的第二因素；请在「账户与安全」里管理自己的登录方式")
        if PlatformAccess(self.store).holds_any_grant(uid):
            raise ServiceError(409, "target_is_platform_admin",
                               "该账户持有平台权限：管理员找回访问走最高管理员交接流程，不能在这里重置第二因素")
        if self.store.one("SELECT 1 AS x FROM app_accounts WHERE user_id=?", (uid,)):
            raise ServiceError(409, "application_identity", "请通过应用身份管理操作该账户")
        if target["status"] != "active":
            raise ServiceError(409, "account_state_conflict", "只有正常使用中的账户可以重置第二因素，请刷新后重试")
        if not target["email_verified_at"]:
            raise ServiceError(409, "email_unverified", "该账户的邮箱尚未验证：重置的通知与取消链接只能发往已验证的邮箱")
        if recent_email_change(self.store, uid):
            days = RECENT_EMAIL_CHANGE_S // 86400
            raise ServiceError(409, "recent_email_change",
                               f"该账户的登录邮箱在 {days} 天内更改过：通知可能发往不属于本人的邮箱，请等满 {days} 天后再申请",
                               {"days": days})
        f = factors(self.store, uid)
        if nothing_to_reset(f, include):
            raise ServiceError(409, "nothing_to_reset", "该账户没有可以重置的第二因素")
        if pending_reset(self.store, uid):
            raise ServiceError(409, "reset_pending", "该账户已有一项等待生效的第二因素重置")
        return target, f

    def _version(self, target: dict, f: dict, include: bool) -> str:
        """What the plan binds: the account's address, verification and status, the factor counts
        and rows (`factor_fingerprint`: a regenerated set of recovery codes or a use of the
        authenticator is a change even at the same count), its pending reset and the passkey
        choice. Ids enter only this opaque digest; no seed or code hash is read."""
        uid = target["id"]
        pending = pending_reset(self.store, uid)
        version = {"id": uid, "status": target["status"], "email": target["email"], "email_key": target["email_key"],
                   "verified": target["email_verified_at"], "factors": f,
                   "fingerprint": factor_fingerprint(self.store, uid, include),
                   "passkeys": [r["id"] for r in self.store.all(
                       "SELECT id FROM passkeys WHERE user_id=? AND deleted_at IS NULL ORDER BY id", (uid,))],
                   "pending": pending["id"] if pending else None, "include_passkeys": include}
        return hashlib.sha256(canonical(version).encode()).hexdigest()

    def _effect(self, uid: str, f: dict, include: bool) -> dict:
        count = lambda table: self.store.one(  # noqa: E731
            f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=? AND revoked_at IS NULL", (uid,))["n"]
        return {"removes_totp": f["totp"], "removes_recovery_codes": f["recovery_remaining"] > 0,
                "removes_passkeys": f["passkeys"] if include else 0,
                "sessions": count("sessions"), "tokens": count("oauth_tokens")}

    # ------------------------------------------------------------------ reading
    def state(self, request: Request, uid: str) -> dict:
        require_platform(request, VIEW, store=self.store, audit=self.audit)
        if not self.ident.get_user(uid):
            raise ServiceError(404, "no_user", "找不到该账户")
        f = factors(self.store, uid)
        row = pending_reset(self.store, uid)
        pending = None
        if row:
            mail = announcement(self.store, row)
            requester = self.store.one("SELECT email FROM users WHERE id=?", (row["requested_by"],))
            # `effective_due_at`: when it can take effect — the full delay after the announcement
            # was delivered, never before `due_at`; null until it is delivered.
            pending = {"id": row["id"], "created_at": row["created_at"], "due_at": row["due_at"], "method": row["method"],
                       "requested_by_email": requester["email"] if requester else None,
                       "include_passkeys": bool(row["include_passkeys"]),
                       "announcement_status": mail["status"], "announcement_sent_at": mail["sent_at"],
                       "effective_due_at": mail["effective_due_at"]}
        history = self.store.all(
            "SELECT id,status,created_at,due_at,completed_at,cancelled_at,method,void_reason FROM second_factor_resets "
            "WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ?", (uid, HISTORY_LIMIT))
        return {**f, "pending_reset": pending, "history": history}

    # ------------------------------------------------------------------ preview
    def preview(self, request: Request, uid: str, body: dict) -> dict:
        actor = self._actor(request)
        # A boolean, or nothing: anything else is the less destructive choice.
        include = body.get("include_passkeys") is True
        try:
            with self.store.tx():
                actor = self._actor(request)
                target, f = self._target(actor, uid, include)
                plan = {"v": 1, "actor": actor["id"], "action": self.action, "target": uid,
                        "include_passkeys": include, "origin": self._environment(request),
                        "version": self._version(target, f, include),
                        "expires_at": time.time() + PREVIEW_SECONDS, "nonce": new_id("plan")}
                effect = self._effect(uid, f, include)
        except Exception as exc:
            self._failure(request, actor, uid, exc, phase="preview", action=REQUEST)
            raise
        return {"action": self.action, "scope": "platform", "risk": OPERATIONS[self.action].risk,
                "target": {"id": uid, "email": target["email"], "status": target["status"]},
                "factors": f, "effect": effect, "due_at": time.time() + SECOND_FACTOR_RESET_DELAY_S,
                "expires_at": plan["expires_at"], "preview_token": self._sign(plan)}

    # ------------------------------------------------------------------ request
    def request(self, request: Request, uid: str, body: dict) -> dict:
        actor = self._actor(request, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        method = body.get("method")
        include = body.get("include_passkeys") is True
        key, token = body.get("idempotency_key"), body.get("preview_token")
        origin = self._environment(request)
        replayed = False
        try:
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            if method not in METHODS:
                raise ServiceError(400, "bad_method", "请选择你核实对方身份的方式", {"field": "method"})
            request_hash = hashlib.sha256(canonical({"action": self.action, "method": method, "reason": reason,
                                                    "include_passkeys": include, "preview_token": token,
                                                    "target": uid, "origin": origin}).encode()).hexdigest()
            with self.store.tx() as tx:
                actor = self._actor(request, execute=True)
                if isinstance(key, str):
                    previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                              (actor["id"], key))
                    if previous:
                        if previous["action"] != self.action or previous["request_hash"] != request_hash:
                            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                        replayed = True
                        result = json.loads(previous["result_json"])
                if not replayed:
                    target, f = self._target(actor, uid, include)
                    if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key) or not isinstance(token, str):
                        raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                    plan = self._verify(token)
                    expected = {"actor": actor["id"], "action": self.action, "target": uid,
                                "include_passkeys": include, "origin": origin}
                    if any(plan.get(k) != v for k, v in expected.items()):
                        raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                    if plan.get("expires_at", 0) <= time.time():
                        raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                    if plan.get("version") != self._version(target, f, include):
                        raise ServiceError(409, "admin_preview_stale", "该账户的第二因素或状态已变化，请重新预览")
                    result = self._open(tx, request, actor, target, f, method=method, reason=reason, include=include,
                                        key=key, request_hash=request_hash, origin=origin)
        except Exception as exc:
            self._failure(request, actor, uid, exc, phase="execute", action=REQUEST, reason=reason)
            raise
        if not replayed:
            notify(getattr(request.app.state, "notifier", None), uid, "second_factor_reset_requested")
        return result

    def _open(self, tx, request, actor, target, f, *, method, reason, include, key, request_hash, origin) -> dict:
        from .mail import second_factor_reset_requested_mail
        from .profile import digest, public_link
        uid = target["id"]
        raw = secrets.token_urlsafe(32)
        # Refuses an unconfigured public origin before anything is written.
        url = public_link(LINK_PATH, raw, target.get("locale"))
        ts = time.time()
        due = ts + SECOND_FACTOR_RESET_DELAY_S
        rid, operation_id = new_id("sfr"), new_id("ao")
        subject, text, html = second_factor_reset_requested_mail(target.get("locale"), url, due_at=due,
                                                                 timezone=target.get("timezone"), include_passkeys=include)
        # The announcement joins this transaction: refused here, the request is not committed.
        delivery = self._mail(request).enqueue(target["email"], subject, text, html=html,
                                               kind="second_factor_reset_requested", ref_id=uid)
        # What completion re-checks: the delivery of this mail, the address it went to, the factor rows.
        tx.execute("INSERT INTO second_factor_resets (id,user_id,requested_by,method,reason,include_passkeys,"
                   "cancel_token_hash,created_at,due_at,status,announcement_mail_id,announced_email_key,factor_digest) "
                   "VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?,?)",
                   (rid, uid, actor["id"], method, reason, int(include), digest(raw), ts, due, delivery["id"],
                    target["email_key"], factor_fingerprint(self.store, uid, include)))
        result = {"ok": True, "operation_id": operation_id, "reset": {"id": rid, "due_at": due, "status": "pending"}}
        self.audit(request, REQUEST, actor_id=actor["id"], object_kind="user", object_id=uid, workspace_given=True,
                   result="ok", meta={"operation_id": operation_id, "reset_id": rid, "method": method, "reason": reason,
                                      "include_passkeys": include, "due_at": due, "factors": f,
                                      "mail_id": delivery["id"], "environment": origin,
                                      **change({"reset": None}, {"reset": "pending"})})
        tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                   "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                   (operation_id, actor["id"], REQUEST, uid, key, request_hash, canonical(result), ts))
        return result

    # ------------------------------------------------------------------ cancel
    def cancel(self, request: Request, uid: str, rid: str, body: dict) -> dict:
        actor = self._actor(request, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        try:
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            with self.store.tx():
                actor = self._actor(request, execute=True)
                row = self.store.one("SELECT * FROM second_factor_resets WHERE id=? AND user_id=?", (rid, uid))
                if not row:
                    raise ServiceError(404, "no_reset", "找不到该账户的这项第二因素重置")
                if row["status"] != "pending":
                    raise ServiceError(409, "reset_not_pending", "这项第二因素重置已经结束（已生效、已取消或已作废）")
                mailed = close_pending(self.store, row, by=actor["id"], mailer=self._mailer
                                       if self._mailer is not None else getattr(request.app.state, "mailer", None))
                cancelled = self.store.one("SELECT cancelled_at FROM second_factor_resets WHERE id=?", (rid,))["cancelled_at"]
                self.audit(request, CANCEL, actor_id=actor["id"], object_kind="user", object_id=uid, workspace_given=True,
                           result="ok", meta={"reset_id": rid, "reason": reason, "due_at": row["due_at"], "mailed": mailed,
                                              "environment": self._environment(request),
                                              **change({"status": "pending"}, {"status": "cancelled"})})
        except Exception as exc:
            self._failure(request, actor, uid, exc, phase="execute", action=CANCEL, reason=reason, reset_id=rid)
            raise
        notify(getattr(request.app.state, "notifier", None), uid, "second_factor_reset_cancelled")
        return {"ok": True, "reset": {"id": rid, "status": "cancelled", "cancelled_at": cancelled}}

    def _failure(self, request, actor, uid, exc, *, phase, action, reason="", reset_id=None):
        # Outside the failed transaction. Inputs and exception messages can carry arbitrary data;
        # audit only a stable code, the phase and a validated reason (never a token).
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed", "phase": phase,
                "environment": self._environment(request)}
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        if reset_id and isinstance(reset_id, str) and len(reset_id) <= 64:
            meta["reset_id"] = reset_id
        self.audit(request, action, actor_id=actor["id"], object_kind="user", object_id=uid, workspace_given=True,
                   result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error", meta=meta)


def build_router(store: Store, ident: Identity, audit, mailer=None) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin, ahead of its own
    `/users/{uid}…` routes (as the bulk router is)."""
    r = APIRouter(tags=["admin"])
    resets = SecondFactorResets(store, ident, audit, mailer)

    @r.get("/users/{uid}/second-factor")
    def second_factor_state(request: Request, uid: str):
        """The account's second factors as counts, its pending reset and the last ten resets."""
        return resets.state(request, uid)

    @r.post("/users/{uid}/second-factor-reset/preview")
    def preview_second_factor_reset(request: Request, uid: str, body: dict = Body(...)):
        """`{"include_passkeys": bool}` → the factors, what the reset removes, when, and the signed plan."""
        return resets.preview(request, uid, body if isinstance(body, dict) else {})

    @r.post("/users/{uid}/second-factor-reset")
    def request_second_factor_reset(request: Request, uid: str, body: dict = Body(...)):
        """`{"method", "reason", "include_passkeys", "preview_token", "idempotency_key"}` → the pending reset, once."""
        return resets.request(request, uid, body if isinstance(body, dict) else {})

    @r.post("/users/{uid}/second-factor-reset/{rid}/cancel")
    def cancel_second_factor_reset(request: Request, uid: str, rid: str, body: dict = Body(...)):
        """`{"reason"}` → the pending reset cancelled; the account is told."""
        return resets.cancel(request, uid, rid, body if isinstance(body, dict) else {})

    return r
