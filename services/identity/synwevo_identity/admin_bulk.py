"""Bulk account administration (A18): suspend accounts, or end every session of accounts, from the
platform console — up to 100 accounts under ONE preview and ONE confirmation.

A batch is a set of single-account acts, never a weaker path around them:

* **preview** (`POST /api/admin/users/bulk-preview`, not dangerous): for each submitted account,
  in the submitted order (duplicates collapsed), whether the single act would be admitted now and,
  if not, exactly the refusal the single route would answer — for a suspension the
  `AccountOperations._target` codes and the D11 continuity codes of `_snapshot`, for a revocation
  `no_user` / `target_outranks_actor` — with its unrevoked sessions and tokens. A suspension's
  accounts are judged in order as execution will meet them, each after the earlier eligible ones
  are suspended (simulated in a savepoint that is rolled back), so the second of the last two
  owners shows `ownership_required` here, and co-owners leaving together are not stale. One signed,
  five-minute plan (`admin.user.bulk`) binds actor, operation, origin, expiry, a nonce and the
  ordered accounts: `[uid, version digest, None]` for an eligible one (a suspension's digest is
  `AccountOperations._snapshot`'s, a revocation's covers the unrevoked session and token ids and
  the live platform grants), `[uid, None, code]` for one refused at preview, which is reported
  with that code and never acted on, even if it became eligible since.
* **execute** (`POST /api/admin/users/bulk`, dangerous: strict re-authentication before anything
  else in the body is read): the written reason, the preview token and an idempotency key. The
  first transaction reserves the key (the batch row, marked running). Then each
  eligible account in its own write transaction: re-admission, its receipt, the single act's own
  re-check, its digest against the plan (`admin_preview_stale` for that account only), then the
  single act's shared write — `AccountOperations._apply` (audit `admin.user.status`, journal
  `user_suspended`) or `admin_operations.revoke_account_sessions` (audit
  `admin.user.revoke_sessions`, journal `sessions_revoked`) — with `batch_id` in its audit row. A
  refused account writes the denied row its single route writes, and the others go on. One final
  transaction writes the batch's audit row and replaces the running mark with the durable result.

**Permission, per operation.** `OPERATIONS["admin.user.bulk"].permission_for(operation)`: a
suspension needs `admin.user.status`'s `platform.users.manage`, a revocation the single route's
`platform.users.support`. The `operation` field is the one thing read before the admission; an
operation outside the two is admitted under `platform.users.support` (the lesser, and the route's
own danger level) and then refused `bad_operation`, so a caller without either permission meets the
platform's 404 or 403, never the request's validation. A preview, which lists each account's
address and state, also needs `platform.users.view` (checked after the operation's permission).

**Receipts and retries.** Each account's transaction writes its receipt in `admin_operations`
under `(actor, "<batch key>:<uid>")` — `admin.user.status`'s own durable result for a suspension, a
receipt row (action `admin.user.revoke_sessions`) for a revocation — with the batch's request hash.
The column has no length limit; a batch key is `[A-Za-z0-9_-]{16,128}`, so it never contains `:`,
and no single-account or batch key can equal a receipt key. The batch row itself is written under
the batch key in the first transaction, before any account is touched, with a running mark: the
same key with another request is `idempotency_conflict` there, having done nothing, whether the
first request is still running or was interrupted. The same request finding the running mark
resumes it under its batch id: the accounts already done are skipped (`already_applied`, no
second journal entry), the rest are applied — or, once the preview has expired, reported
`admin_preview_expired` and never applied — and the batch is finished. A finished batch's result
replaces the mark, and the same request then returns it verbatim, applying and recording nothing.

No background job: the batch is bounded and synchronous. Cancelling is closing the preview;
retrying the failed accounts is a new preview of them — a confirmed batch never runs twice.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter

from fastapi import APIRouter, Body, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.platform_access import PlatformAccess, audit_denied, require_outranking, require_platform
from synwevo_core.store import Store, new_id

from .admin_operations import (OPERATIONS, PREVIEW_SECONDS, AccountOperations, SignedPreview, canonical,
                               revoke_account_sessions)

BULK_MAX = 100
#: What a submitted account id may look like. Ids are `u_` + 12 hex characters; the bound keeps the
#: signed plan (which carries every submitted id) small, and the alphabet keeps anything that
#: cannot be an id — a lone surrogate SQLite cannot even bind among them — out of the queries.
ACCOUNT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
SUSPEND, REVOKE_SESSIONS = "suspend", "revoke_sessions"
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9_-]{16,128}")
#: The batch row's `result_json` from the first transaction until the last: the key is reserved.
RUNNING = "running"
#: Reading the accounts a preview lists (email, status) is account search, as the users list is.
VIEW_PERMISSION = "platform.users.view"
#: The single act each operation is: its audit action, which is also its receipt's action.
SINGLE_ACTION = {SUSPEND: AccountOperations.action, REVOKE_SESSIONS: "admin.user.revoke_sessions"}
#: The server's sentence for an account refused at preview, reported again at execution from the
#: code the plan carries. The same sentences the single routes answer (AccountOperations._target,
#: ownership._require_successor, platform_access.require_outranking); the bulk tests compare them.
REFUSAL_MESSAGES = {
    "no_user": "找不到该账户",
    "application_identity": "请通过应用身份管理操作该账户",
    "self_suspend": "不能停用自己的账户",
    "last_super_admin": "不能停用最后一名可用的最高管理员",
    "demote_first": "请先撤销该账户的全部平台权限，再停用它",
    "account_state_conflict": "账户状态已变化，请刷新后重试",
    "ownership_required": "请先移交共享工作空间的所有权和管理权限",
    "organization_ownership_required": "请先移交组织的所有权和管理权限",
    # organizations.deleted_workspace_owner names the workspace and its recovery deadline; a
    # preview item carries the code alone, so this is that sentence without the two (the same
    # code-only wording the account shell catalogues in lib/refusals.ts REFUSAL_SOURCES).
    "owner_of_deleted_workspace": ("此账户是一个已删除、仍在恢复期内的工作空间唯一可用的拥有者或管理者；恢复期限结束前不能执行此操作。"
                                   "可请平台管理员恢复该工作空间后移交，或等待恢复期限结束。"),
    "target_outranks_actor": "该账户持有你没有的平台权限：只有权限不低于它的管理员才能对它执行这项操作",
}
REFUSED_MESSAGE = "该账户不符合这项操作的条件"
DONE_MESSAGES = {SUSPEND: "账户已停用", REVOKE_SESSIONS: "已撤销该账户的全部会话"}
ALREADY_APPLIED_MESSAGE = "这一项已在本批次此前的尝试中完成"


@contextlib.contextmanager
def _discarded(connection, name: str = "bulk_preview"):
    """A savepoint inside the caller's write transaction whose changes never leave it: rolled back
    and released on the way out, whatever happened inside. Raw SAVEPOINT statements on the
    transaction's own connection (`Store.tx` yields it; the store opens it with
    `isolation_level=None`, so the driver adds no statements of its own). Never outside a
    transaction: a SAVEPOINT there would open one without the writer lock."""
    if not connection.in_transaction:
        raise RuntimeError("a discarded savepoint needs the caller's write transaction")
    connection.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        # If the savepoint itself is gone the caller's ROLLBACK discards everything anyway.
        with contextlib.suppress(sqlite3.Error):
            connection.execute(f"ROLLBACK TO {name}")
            connection.execute(f"RELEASE {name}")
        raise
    connection.execute(f"ROLLBACK TO {name}")
    connection.execute(f"RELEASE {name}")


class _AdmissionLost(Exception):
    """The actor's own admission failed inside an account's transaction (session ended, grant
    revoked, re-authentication expired): the batch stops there, and every account not reached yet
    is reported with that refusal."""

    def __init__(self, error: ServiceError):
        super().__init__(error.code)
        self.error = error


class BulkAccountOperations(SignedPreview):
    action = "admin.user.bulk"
    # Up to 100 accounts of at most 128 ASCII characters, each with a 64-character digest or a
    # code: about 21 KB of plan, under 30 KB once encoded.
    token_limit = 65536

    def __init__(self, store: Store, ident: Identity, sessions, audit):
        self.store, self.ident, self.sessions, self.audit = store, ident, sessions, audit
        self.accounts = AccountOperations(store, audit)

    # ------------------------------------------------------------------ admission and input
    def _actor(self, request, operation, *, execute=False, view=False):
        """The operation's permission first (its 403 names it), then — for a preview, which lists
        addresses and states — `platform.users.view` as well."""
        actor = require_platform(request, OPERATIONS[self.action].permission_for(operation), store=self.store,
                                 audit=self.audit, dangerous=execute)
        if view:
            actor = require_platform(request, VIEW_PERMISSION, store=self.store, audit=self.audit)
        return actor

    @staticmethod
    def _operation(operation) -> str:
        if operation not in (SUSPEND, REVOKE_SESSIONS):
            raise ServiceError(400, "bad_operation", "请选择有效的批量操作（suspend 或 revoke_sessions）",
                               {"field": "operation"})
        return operation

    @staticmethod
    def _ids(raw) -> list[str]:
        if not isinstance(raw, list) or not raw:
            raise ServiceError(400, "missing_users", "请提供要处理的账户（非空列表）", {"field": "user_ids"})
        if len(raw) > BULK_MAX:
            raise ServiceError(400, "too_many", f"一次最多处理 {BULK_MAX} 个账户", {"field": "user_ids", "limit": BULK_MAX})
        if not all(isinstance(v, str) and ACCOUNT_ID.fullmatch(v) for v in raw):
            raise ServiceError(400, "missing_users", "账户编号格式无效", {"field": "user_ids"})
        return list(dict.fromkeys(raw))

    # ------------------------------------------------------------------ one account, as the single act judges it
    def _counts(self, uid: str) -> dict:
        """Unrevoked sessions and tokens: what the act ends (the predicates `_snapshot` counts and
        the revocations update by)."""
        return {"sessions": self.store.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=? AND revoked_at IS NULL",
                                           (uid,))["n"],
                "tokens": self.store.one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE user_id=? AND revoked_at IS NULL",
                                         (uid,))["n"]}

    def _sessions_version(self, uid: str) -> str:
        """A revocation's version: the account's unrevoked session and token ids and its live
        platform grants. Ids enter only this opaque digest, never a response or an audit row."""
        version = {"id": uid,
                   "sessions": [r["id"] for r in self.store.all(
                       "SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY id", (uid,))],
                   "tokens": [r["id"] for r in self.store.all(
                       "SELECT id FROM oauth_tokens WHERE user_id=? AND revoked_at IS NULL ORDER BY id", (uid,))],
                   "grants": PlatformAccess(self.store).live_grants(uid)}
        return hashlib.sha256(canonical(version).encode()).hexdigest()

    def _judge(self, request, actor, operation, uid) -> str:
        """The account's version digest if the single act would be admitted now; raises the single
        act's refusal otherwise. Under the writer lock (the D11 continuity check needs it). Nothing
        is audited here: a preview over a selection reports, it does not attempt."""
        if operation == SUSPEND:
            target = self.accounts._target(request, actor, uid, "suspended")
            return self.accounts._snapshot(target, "suspended")[0]
        if not self.ident.get_user(uid):
            raise ServiceError(404, "no_user", "找不到该账户")
        require_outranking(request, actor, uid, store=self.store, audit=None)
        return self._sessions_version(uid)

    # ------------------------------------------------------------------ preview
    def preview(self, request, body: dict):
        operation = body.get("operation")
        actor = self._actor(request, operation, view=True)
        try:
            self._operation(operation)
            ids = self._ids(body.get("user_ids"))
            with self.store.tx() as tx:
                actor = self._actor(request, operation, view=True)
                items, plan_items = [], []
                impact = {"sessions": 0, "tokens": 0}
                # Execution suspends the accounts in order, each in its own transaction, so a later
                # account is judged there with the earlier ones already suspended — which is what
                # its D11 continuity state (a co-owner's status) and so its digest depend on. The
                # preview judges it the same way: in a savepoint of this transaction, each eligible
                # account's `users.status` becomes 'suspended' before the next is judged, and the
                # savepoint is rolled back before anything is returned or signed. Only the status is
                # simulated: of what a suspension writes, nothing else enters another account's
                # `_target` or `_snapshot` (its own `updated_at`, sessions, tokens and pending
                # credentials are read for that account alone). A revocation changes nothing that
                # another account's checks read, so it simulates nothing.
                with _discarded(tx):
                    for uid in ids:
                        user = self.ident.get_user(uid)
                        try:
                            digest, code = self._judge(request, actor, operation, uid), None
                        except ServiceError as exc:
                            digest, code = None, exc.code
                        counts = self._counts(uid) if user else {"sessions": 0, "tokens": 0}
                        if code is None:
                            impact = {k: impact[k] + counts[k] for k in impact}
                            if operation == SUSPEND:
                                tx.execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
                        items.append({"id": uid, "email": user["email"] if user else None,
                                      "status": user["status"] if user else None, "eligible": code is None,
                                      "code": code, **counts})
                        plan_items.append([uid, digest, code])
                expires = time.time() + PREVIEW_SECONDS
                plan = {"v": 1, "actor": actor["id"], "action": self.action, "operation": operation,
                        "origin": self._environment(request), "items": plan_items,
                        "expires_at": expires, "nonce": new_id("plan")}
        except Exception as exc:
            self._failure(request, actor, exc, phase="preview", operation=operation)
            raise
        return {"operation": operation, "scope": "platform", "risk": OPERATIONS[self.action].risk,
                "expires_at": expires, "preview_token": self._sign(plan), "total": len(items),
                "eligible": sum(1 for item in items if item["eligible"]), "impact": impact, "items": items}

    # ------------------------------------------------------------------ execute
    def _batch(self, actor_id: str, key: str, request_hash: str):
        """The batch row under this key, if there is one, as `(result, running)` — or the conflict
        when the key was used for another request."""
        previous = self.store.one("SELECT action,request_hash,result_json FROM admin_operations "
                                  "WHERE actor_id=? AND idempotency_key=?", (actor_id, key))
        if not previous:
            return None
        if previous["action"] != self.action or previous["request_hash"] != request_hash:
            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
        result = json.loads(previous["result_json"])
        return result, result.get("state") == RUNNING

    @staticmethod
    def _plan_items(plan) -> list[list]:
        items = plan.get("items")
        valid = isinstance(items, list) and 0 < len(items) <= BULK_MAX and all(
            isinstance(item, list) and len(item) == 3 and isinstance(item[0], str)
            and (isinstance(item[1], str) != isinstance(item[2], str)) for item in items)
        if not valid:
            raise ServiceError(409, "admin_preview_invalid", "请重新预览操作影响后再确认")
        return items

    def execute(self, request, body: dict):
        operation = body.get("operation")
        actor = self._actor(request, operation, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        key, token = body.get("idempotency_key"), body.get("preview_token")
        origin = self._environment(request)
        request_hash = hashlib.sha256(canonical({"action": self.action, "operation": operation, "reason": reason,
                                                "preview_token": token, "origin": origin}).encode()).hexdigest()
        batch_id = None
        try:
            self._operation(operation)
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            with self.store.tx() as tx:
                actor = self._actor(request, operation, execute=True)
                previous = self._batch(actor["id"], key, request_hash) if isinstance(key, str) else None
                if previous and not previous[1]:
                    return previous[0]
                if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key) or not isinstance(token, str):
                    raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "operation": operation, "origin": origin}
                if any(plan.get(field) != value for field, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                items = self._plan_items(plan)
                expired = plan.get("expires_at", 0) <= time.time()
                if previous:
                    # The same request, still running or interrupted: resume it under its batch id.
                    # Its confirmation was accepted in time; once the preview has expired the
                    # accounts not yet done are reported expired, never applied (`_apply_one`).
                    batch_id = previous[0]["operation_id"]
                else:
                    if expired:
                        raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                    # The key is reserved before any account is touched: a concurrent or retried
                    # request with another body meets `idempotency_conflict` here, having done nothing.
                    batch_id = new_id("ao")
                    tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,"
                               "request_hash,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (batch_id, actor["id"], self.action, "users", key, request_hash,
                                canonical({"state": RUNNING, "operation_id": batch_id, "operation": operation}),
                                time.time()))
            outcome = self._run(request, actor, operation, items, reason=reason, key=key,
                                request_hash=request_hash, batch_id=batch_id, expired=expired)
            results = []
            for uid, _, code in items:
                if code is not None:
                    results.append({"id": uid, "ok": False, "code": code,
                                    "message": REFUSAL_MESSAGES.get(code, REFUSED_MESSAGE)})
                else:
                    results.append({"id": uid, **outcome[uid]})
            succeeded = sum(1 for item in results if item["ok"])
            result = {"ok": succeeded == len(results), "operation_id": batch_id, "operation": operation,
                      "succeeded": succeeded, "failed": len(results) - succeeded, "results": results}
            with self.store.tx() as tx:
                # A concurrent resumption of the same request may have finished first: its result
                # is the batch's.
                row = self.store.one("SELECT result_json FROM admin_operations WHERE id=?", (batch_id,))
                stored = json.loads(row["result_json"])
                if stored.get("state") != RUNNING:
                    return stored
                self.audit(request, self.action, actor_id=actor["id"], object_kind="admin_operation",
                           object_id=batch_id, workspace_given=True, result="ok",
                           meta={"operation": operation, "total": len(results), "succeeded": succeeded,
                                 "failed": len(results) - succeeded,
                                 "codes": dict(sorted(Counter(item["code"] for item in results).items())),
                                 "reason": reason, "operation_id": batch_id, "environment": origin})
                tx.execute("UPDATE admin_operations SET result_json=? WHERE id=?", (canonical(result), batch_id))
            return result
        except Exception as exc:
            self._failure(request, actor, exc, phase="execute", operation=operation, reason=reason,
                          batch_id=batch_id)
            raise

    def _run(self, request, actor, operation, items, *, reason, key, request_hash, batch_id, expired=False) -> dict:
        """Every eligible account, each in its own transaction; {uid: its result}. The actor's own
        account (a revocation may include it) goes last, so that ending the actor's own session
        cannot cut the batch short; results are still reported in the submitted order."""
        eligible = [(uid, digest) for uid, digest, code in items if code is None]
        eligible.sort(key=lambda item: item[0] == actor["id"])
        outcome, halted = {}, None
        for uid, digest in eligible:
            if halted is not None:
                outcome[uid] = {"ok": False, "code": halted.code, "message": halted.message}
                continue
            try:
                done = self._apply_one(request, operation, uid, digest, reason=reason, key=f"{key}:{uid}",
                                       request_hash=request_hash, batch_id=batch_id, expired=expired)
                outcome[uid] = {"ok": True, **done}
            except _AdmissionLost as lost:
                halted = lost.error
                self._lost(request, actor, operation, uid, halted, reason=reason, batch_id=batch_id)
                outcome[uid] = {"ok": False, "code": halted.code, "message": halted.message}
            except ServiceError as exc:
                # The account's own refusal, recorded as its single route records it.
                self._refused(request, actor, operation, uid, exc, reason=reason, batch_id=batch_id)
                outcome[uid] = {"ok": False, "code": exc.code, "message": exc.message}
            except Exception as exc:
                # Not a refusal: the batch stops here with the error. The accounts already done keep
                # their receipts, and a retry with the same key carries on from this one.
                if operation == SUSPEND:
                    self.accounts._failure(request, actor, uid, exc, phase="execute", reason=reason, batch_id=batch_id)
                raise
        return outcome

    def _apply_one(self, request, operation, uid, digest, *, reason, key, request_hash, batch_id,
                   expired=False) -> dict:
        """One account of the batch in its own write transaction: re-admission, the account's
        receipt, then the single act's re-check and shared write (receipt included). In a batch
        resumed after its preview expired, an account not yet done is refused as expired."""
        with self.store.tx() as tx:
            try:
                actor = self._actor(request, operation, execute=True)
            except ServiceError as exc:
                raise _AdmissionLost(exc) from None
            receipt = self.store.one("SELECT request_hash,result_json FROM admin_operations "
                                     "WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
            if receipt:
                if receipt["request_hash"] != request_hash:
                    raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                earlier = json.loads(receipt["result_json"])
                return {"code": "already_applied", "message": ALREADY_APPLIED_MESSAGE,
                        "sessions_revoked": earlier.get("sessions_revoked", 0),
                        "tokens_revoked": earlier.get("tokens_revoked", 0)}
            if expired:
                raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
            if operation == SUSPEND:
                target = self.accounts._target(request, actor, uid, "suspended")
                out = self.accounts._apply(tx, request, actor, target, "suspended", version=digest, reason=reason,
                                           key=key, request_hash=request_hash, batch_id=batch_id)
            else:
                item_id = new_id("ao")

                def receipt_row(result):
                    tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,"
                               "request_hash,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                               (item_id, actor["id"], SINGLE_ACTION[REVOKE_SESSIONS], uid, key, request_hash,
                                canonical({"ok": True, "operation_id": item_id, **result, "batch_id": batch_id}),
                                time.time()))

                out = revoke_account_sessions(self.store, self.sessions, self.audit, request, actor, uid, reason,
                                              version=digest, version_of=self._sessions_version, batch_id=batch_id,
                                              event_id=item_id, before_journal=receipt_row)
            return {"code": "ok", "message": DONE_MESSAGES[operation],
                    "sessions_revoked": out["sessions_revoked"], "tokens_revoked": out["tokens_revoked"]}

    def _refused(self, request, actor, operation, uid, exc: ServiceError, *, reason, batch_id) -> None:
        """Outside the account's rolled-back transaction, the row its single route writes: a
        suspension's `admin.user.status` denied (or error) row, with the batch id; a revocation's
        `admin.access.denied` row (`admin_routes._write`), for a refusal below 500."""
        if operation == SUSPEND:
            self.accounts._failure(request, actor, uid, exc, phase="execute", reason=reason, batch_id=batch_id)
        elif exc.status < 500:
            audit_denied(self.store, self.audit, request, actor["id"], uid, object_kind="user", code=exc.code)

    def _lost(self, request, actor, operation, uid, exc: ServiceError, *, reason, batch_id) -> None:
        """The record of an admission lost at this account: the row its single route writes for
        the refusal, and — for a missing permission — the `admin.access.denied` row
        `require_platform` wrote inside the account's transaction, which rolled back with it."""
        self._refused(request, actor, operation, uid, exc, reason=reason, batch_id=batch_id)
        if exc.code == "platform_permission_denied":
            audit_denied(self.store, self.audit, request, actor["id"], OPERATIONS[self.action].permission_for(operation))

    def _failure(self, request, actor, exc, *, phase, operation=None, reason="", batch_id=None):
        # Outside any failed transaction. Inputs and exception messages can carry arbitrary data;
        # audit only a stable code, the phase, a catalogued operation and a validated reason.
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "environment": self._environment(request)}
        if operation in (SUSPEND, REVOKE_SESSIONS):
            meta["operation"] = operation
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        if batch_id:
            meta["operation_id"] = batch_id
        self.audit(request, self.action, actor_id=actor["id"], object_kind="admin_operation", object_id=batch_id,
                   workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta=meta)


def build_router(store: Store, ident: Identity, sessions, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin, and included before the
    parent declares its own `/users/{uid}…` routes (test_admin_bulk_accounts proves both paths reach
    these handlers)."""
    r = APIRouter(tags=["admin"])
    bulk = BulkAccountOperations(store, ident, sessions, audit)

    @r.post("/users/bulk-preview")
    def preview_bulk_accounts(request: Request, body: dict = Body(...)):
        """`{"operation": "suspend"|"revoke_sessions", "user_ids": [...]}` → every account's
        eligibility and the signed plan."""
        return bulk.preview(request, body if isinstance(body, dict) else {})

    @r.post("/users/bulk")
    def run_bulk_accounts(request: Request, body: dict = Body(...)):
        """`{"operation", "preview_token", "idempotency_key", "reason"}` → each account's result, once."""
        return bulk.execute(request, body if isinstance(body, dict) else {})

    return r
