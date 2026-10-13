"""Paid-dispatch risk controls: automatic brake, platform breaker and staged-rollout allowlist.

docs/platform/model-risk.md. Every check that can refuse a paid model call runs inside the
ledger's dispatch writer transaction (through CostAdmission.admit), before the one-shot send
fence commits; a refusal sends nothing and the wallet hold is released by the gateway.

* Breaker: one platform-wide switch per billing environment. It opens automatically when the
  upstream balance falls below the hard threshold or reports itself unavailable, and when the
  gateway receives an upstream 402 (`trip_platform_breaker`, the one function both use). Only a
  platform administrator closes it, with a reason.
* Brake: rolling-window provider-cost velocity per payer and per route, against thresholds in
  the published cost policy. A call that would take the window past its threshold is refused
  and latches a suspension of that payer or route; only an administrator lifts it.
* Allowlist: when allowlist mode is on, only listed payers may run paid calls. Without a
  published risk section it is on in the live environment (first production enablement) and
  off in the test environment.

Velocity counts provider cost (what the platform pays), not the sale price, in the cost policy's
unit: settled exact cost, and the conservative reserved bound for usage not yet known. These
rows are state, never money facts; nothing here moves a balance.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import logging
import time

from ..errors import ServiceError
from ..store import new_id
from .models import BillingError, identifier, integer

log = logging.getLogger("synwevo.billing.risk")

PAYER_KINDS = ("user", "organization")
SCOPE_KINDS = PAYER_KINDS + ("route",)
WINDOW_MIN_S, WINDOW_MAX_S = 60, 86_400
POLL_MIN_S, POLL_MAX_S = 60, 86_400
# The same bound as the cost admission counters: never materialize unbounded history in a send path.
MAX_RECORDS = 10_000
# Admin alerts go to the people who can act on them: holders of this platform permission.
ALERT_PERMISSION = "platform.config.manage"
ADMIN_TARGET = {"origin": "accounts", "path": "/admin?tab=model-risk"}
REASON_MIN, REASON_MAX = 8, 500


@dataclass(frozen=True)
class RiskPolicy:
    """The risk section of a published cost policy. Amounts are cost-policy units."""
    allowlist_only: bool
    payer_window_s: int
    payer_window_cost: int | None
    route_window_s: int
    route_window_cost: int | None
    balance_warn: int | None
    balance_close: int | None
    balance_poll_s: int
    reconcile_tolerance: int
    pending_alert: int | None

    def __post_init__(self):
        if type(self.allowlist_only) is not bool:
            raise BillingError("invalid_risk_policy")
        for value in (self.payer_window_s, self.route_window_s):
            if integer(value, positive=True, maximum=WINDOW_MAX_S) < WINDOW_MIN_S:
                raise BillingError("invalid_risk_policy")
        if integer(self.balance_poll_s, positive=True, maximum=POLL_MAX_S) < POLL_MIN_S:
            raise BillingError("invalid_risk_policy")
        for value in (self.payer_window_cost, self.route_window_cost, self.balance_warn,
                      self.balance_close, self.pending_alert):
            if value is not None:
                integer(value)
        integer(self.reconcile_tolerance)
        if (self.balance_warn is not None and self.balance_close is not None
                and self.balance_close > self.balance_warn):
            raise BillingError("invalid_risk_policy")


class BrakeTripped(BillingError):
    """A velocity refusal. ``trip`` is what CostAdmission.after_refusal latches once the
    refusing dispatch transaction has rolled back (a latch written inside it would roll back too)."""

    def __init__(self, code: str, trip: dict):
        super().__init__(code)
        self.trip = trip


def allowlist_required(environment: str, risk: RiskPolicy | None) -> bool:
    return risk.allowlist_only if risk is not None else environment == "live"


def _reason(value) -> str:
    if not isinstance(value, str) or not REASON_MIN <= len(value.strip()) <= REASON_MAX:
        raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
    return value.strip()


def _ceil(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _system_audit(t, action: str, object_kind: str, object_id: str, meta: dict) -> None:
    t.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
              "result,request_id,ip,meta) VALUES (?,?,'system',NULL,NULL,?,?,?,'ok',NULL,NULL,?)",
              (new_id("ae"), time.time(), action, object_kind, object_id,
               json.dumps(meta, ensure_ascii=False, sort_keys=True)))


def admin_recipients(store, *, limit: int = 50) -> list[str]:
    """Accounts whose current platform grants include ALERT_PERMISSION (bounded)."""
    from ..platform_access import ROLES, PlatformAccess
    roles = sorted(role for role, permissions in ROLES.items() if ALERT_PERMISSION in permissions)
    marks = ",".join("?" * len(roles))
    rows = store.all(f"SELECT DISTINCT user_id FROM platform_role_grants WHERE revoked_at IS NULL "
                     f"AND (expires_at IS NULL OR expires_at>?) AND role IN ({marks}) ORDER BY user_id LIMIT ?",
                     (time.time(), *roles, limit))
    access = PlatformAccess(store)
    return [row["user_id"] for row in rows if ALERT_PERMISSION in access.effective_permissions(row["user_id"])]


def notify_admins(store, kind: str, *, params: dict, dedupe_key: str, object_id: str) -> int:
    """Inbox (and mail, by the reader's preferences) for every current alert recipient.
    Never raises: an alert that cannot be delivered must not undo the protective state change."""
    from ..notifications import Notifier
    sent = 0
    try:
        notifier = Notifier(store)
        for uid in admin_recipients(store):
            try:
                if notifier.emit(uid, kind, params=params, object_kind="model_risk", object_id=object_id,
                                 dedupe_key=dedupe_key, target=ADMIN_TARGET):
                    sent += 1
            except Exception as exc:  # noqa: BLE001 - one reader's inbox failure is not another's
                log.warning("risk alert to one administrator failed: %s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        log.warning("risk alert %s could not be delivered: %s", kind, type(exc).__name__)
    return sent


def notify_user(store, user_id: str, kind: str, *, params: dict, dedupe_key: str, object_id: str) -> bool:
    from ..notifications import Notifier
    try:
        return bool(Notifier(store).emit(user_id, kind, params=params, object_kind="model_risk",
                                         object_id=object_id, dedupe_key=dedupe_key,
                                         target={"origin": "accounts", "path": "/account/wallet"}))
    except Exception as exc:  # noqa: BLE001
        log.warning("risk notice to the payer failed: %s", type(exc).__name__)
        return False


def _journal(store, kind: str, **fields) -> None:
    """Write-ahead restore aid, inside the caller's write transaction (journal._intent): the line
    before the change, with a receipt that commits with it, and a void if the transaction rolls
    back — so a suspension or breaker that never committed is not re-created on every restore. A
    failed append never undoes the restrictive state (strict=False: the receipt still commits with
    it): refusing a suspension or an open breaker because a log could not be written would fail
    open."""
    from .. import journal
    try:
        if not journal._intent(store, kind, strict=False, **fields):
            log.error("security journal append for %s failed", kind)
    except Exception as exc:  # noqa: BLE001
        log.error("security journal append for %s failed: %s", kind, type(exc).__name__)


class RiskControls:
    def __init__(self, store, *, environment: str, clock=None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _now(self) -> int:
        return integer(self.clock())

    # ------------------------------------------------------------------ state
    def _breaker(self, t) -> dict:
        row = t.execute("SELECT * FROM model_risk_breaker_events WHERE environment=? ORDER BY seq DESC LIMIT 1",
                        (self.environment,)).fetchone()
        if row is None:
            return {"state": "closed", "seq": 0, "id": None, "source": None, "reason_code": None,
                    "reason": None, "actor_id": None, "created_at": None}
        return dict(row)

    def breaker(self) -> dict:
        with self.store.tx() as t:
            return self._breaker(t)

    def _active(self, t, scope_kind: str, scope_id: str):
        return t.execute("SELECT * FROM model_risk_suspensions WHERE environment=? AND scope_kind=? AND scope_id=? "
                         "AND lifted_at IS NULL", (self.environment, scope_kind, scope_id)).fetchone()

    def _allowlisted(self, t, payer_kind: str, payer_id: str) -> bool:
        return t.execute("SELECT 1 FROM model_risk_allowlist WHERE environment=? AND payer_kind=? AND payer_id=? "
                         "AND removed_at IS NULL", (self.environment, payer_kind, payer_id)).fetchone() is not None

    @staticmethod
    def payer_of(t, call) -> dict:
        """Who pays this reserved call: kind/id from its fixed context, wallet owner for velocity."""
        context = json.loads(call["context"])
        owner = t.execute("SELECT owner FROM billing_accounts WHERE id=?", (call["account_id"],)).fetchone()
        if owner is None:
            raise BillingError("risk_payer_unknown")
        if context.get("payer_kind", "personal") in ("personal", "user"):
            kind, payer = "user", context["payer_user_id"]
        else:
            kind, payer = "organization", context.get("payer_id") or context.get("organization_id") or owner[0]
        return {"kind": kind, "id": identifier(payer), "owner": owner[0], "actor": context.get("actor_user_id")}

    # ------------------------------------------------------------------ dispatch guard
    def guard(self, t, *, policy, call, attempt, amount: int, now: int) -> None:
        """Inside Ledger.dispatch's writer transaction, after the cost budgets; performs no writes."""
        integer(amount)
        integer(now)
        if self._breaker(t)["state"] == "open":
            raise BillingError("risk_breaker_open")
        risk = getattr(policy, "risk", None)
        payer = self.payer_of(t, call)
        route = identifier(attempt["requested_model"])
        if allowlist_required(self.environment, risk) and not self._allowlisted(t, payer["kind"], payer["id"]):
            raise BillingError("risk_not_allowlisted")
        if self._active(t, payer["kind"], payer["id"]):
            raise BillingError("risk_payer_suspended")
        if self._active(t, "route", route):
            raise BillingError("risk_route_suspended")
        if risk is None or (risk.payer_window_cost is None and risk.route_window_cost is None):
            return
        windows = []
        if risk.payer_window_cost is not None:
            windows.append(risk.payer_window_s)
        if risk.route_window_cost is not None:
            windows.append(risk.route_window_s)
        rows = t.execute(
            "SELECT a.reserved_units,a.admitted_at,a.unit,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit,"
            "att.requested_model AS route,acc.owner AS owner "
            "FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope "
            "JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key "
            "JOIN billing_accounts acc ON acc.id=c.account_id "
            "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
            "WHERE a.environment=? AND a.provider=? AND a.account_ref=? AND a.admitted_at>? "
            "AND (acc.owner=? OR att.requested_model=?) LIMIT ?",
            (self.environment, policy.provider, policy.account_ref, now - max(windows),
             payer["owner"], route, MAX_RECORDS + 1)).fetchall()
        if len(rows) > MAX_RECORDS:
            raise BillingError("risk_accounting_capacity")
        payer_total = route_total = Fraction(0)
        for row in rows:
            if row["unit"] != policy.unit.code or (row["usage_key"] is not None and row["cost_unit"] != policy.unit.code):
                raise BillingError("cost_unit_mismatch")
            if row["usage_key"] is None:
                cost = Fraction(integer(row["reserved_units"]))
            else:
                try:
                    cost = Fraction(int(row["cost_n"]), int(row["cost_d"]))
                except (TypeError, ValueError, ZeroDivisionError):
                    raise BillingError("invalid_cost_fact") from None
            if row["owner"] == payer["owner"] and row["admitted_at"] > now - risk.payer_window_s:
                payer_total += cost
            if row["route"] == route and row["admitted_at"] > now - risk.route_window_s:
                route_total += cost
        base = {"unit": policy.unit.code, "policy_revision": policy.revision, "actor_user_id": payer["actor"]}
        if risk.payer_window_cost is not None and payer_total + amount > risk.payer_window_cost:
            raise BrakeTripped("risk_payer_brake", {**base, "scope_kind": payer["kind"], "scope_id": payer["id"],
                "reason_code": "payer_velocity", "window_s": risk.payer_window_s,
                "threshold_units": risk.payer_window_cost, "observed_units": _ceil(payer_total + amount)})
        if risk.route_window_cost is not None and route_total + amount > risk.route_window_cost:
            raise BrakeTripped("risk_route_brake", {**base, "scope_kind": "route", "scope_id": route,
                "reason_code": "route_velocity", "window_s": risk.route_window_s,
                "threshold_units": risk.route_window_cost, "observed_units": _ceil(route_total + amount)})

    # ------------------------------------------------------------------ brake latch
    def record_trip(self, trip: dict) -> dict:
        """Latch a suspension for a refused velocity breach; idempotent per active scope."""
        scope_kind, scope_id = trip["scope_kind"], identifier(trip["scope_id"])
        if scope_kind not in SCOPE_KINDS:
            raise BillingError("invalid_risk_scope")
        with self.store.tx() as t:
            existing = self._active(t, scope_kind, scope_id)
            if existing:
                return dict(existing)
            sid, now = new_id("rsus"), self._now()
            _journal(self.store, "model_dispatch_suspended", suspension_id=sid, environment=self.environment,
                     scope_kind=scope_kind, scope_id=scope_id)
            t.execute("INSERT INTO model_risk_suspensions(id,environment,scope_kind,scope_id,source,reason_code,unit,"
                      "window_s,threshold_units,observed_units,policy_revision,actor_user_id,created_at) "
                      "VALUES (?,?,?,?,'brake',?,?,?,?,?,?,?,?)",
                      (sid, self.environment, scope_kind, scope_id, trip["reason_code"], trip.get("unit"),
                       trip.get("window_s"), trip.get("threshold_units"), trip.get("observed_units"),
                       trip.get("policy_revision"), trip.get("actor_user_id"), now))
            row = dict(t.execute("SELECT * FROM model_risk_suspensions WHERE id=?", (sid,)).fetchone())
            _system_audit(t, "billing.risk.brake_triggered", "model_risk_suspension", sid,
                          {"billing_environment": self.environment, "scope_kind": scope_kind, "scope_id": scope_id,
                           "reason_code": row["reason_code"], "unit": row["unit"], "window_s": row["window_s"],
                           "threshold_units": row["threshold_units"], "observed_units": row["observed_units"],
                           "policy_revision": row["policy_revision"]})
            self.store.after_commit(lambda: self._announce_trip(row))
        return row

    def _announce_trip(self, row: dict) -> None:
        scope = {"user": "payer", "organization": "payer", "route": "route"}[row["scope_kind"]]
        notify_admins(self.store, "billing.risk.brake_triggered",
                      params={"risk": scope, "window": str(row["window_s"] or "")},
                      dedupe_key="risk:suspension:" + row["id"], object_id=row["id"])
        actor = row.get("actor_user_id")
        if row["scope_kind"] in PAYER_KINDS and actor:
            notify_user(self.store, actor, "billing.risk.paid_paused", params={},
                        dedupe_key="risk:suspension:" + row["id"], object_id=row["id"])

    def lift(self, suspension_id: str, *, actor_id: str, reason: str) -> dict:
        """Administrator action; the caller authorizes, audits and owns the transaction."""
        reason = _reason(reason)
        identifier(actor_id)
        with self.store.tx() as t:
            row = t.execute("SELECT * FROM model_risk_suspensions WHERE id=? AND environment=?",
                            (identifier(suspension_id), self.environment)).fetchone()
            if row is None:
                raise ServiceError(404, "risk_suspension_not_found", "未找到这项付费暂停")
            if row["lifted_at"] is not None:
                raise ServiceError(409, "risk_suspension_lifted", "这项付费暂停已解除")
            t.execute("UPDATE model_risk_suspensions SET lifted_at=?,lifted_by=?,lift_reason=? WHERE id=?",
                      (self._now(), actor_id, reason, row["id"]))
            return dict(t.execute("SELECT * FROM model_risk_suspensions WHERE id=?", (row["id"],)).fetchone())

    def suspensions(self, *, active_only: bool = True, before: int | None = None, limit: int = 50) -> dict:
        integer(limit, positive=True, maximum=100)
        clause, args = (" AND lifted_at IS NULL" if active_only else ""), []
        if before is not None:
            clause += " AND created_at<?"
            args.append(integer(before))
        rows = self.store.all("SELECT * FROM model_risk_suspensions WHERE environment=?" + clause +
                              " ORDER BY created_at DESC, id DESC LIMIT ?", (self.environment, *args, limit + 1))
        items = rows[:limit]
        return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit else None}

    # ------------------------------------------------------------------ platform breaker
    def open_breaker(self, *, source: str, reason_code: str, actor_id: str | None = None,
                     reason: str | None = None) -> dict:
        """Close paid dispatch platform-wide. Idempotent: an open breaker stays as it was."""
        if source not in ("balance", "upstream_402", "admin"):
            raise BillingError("invalid_breaker_source")
        identifier(reason_code)
        if source == "admin":
            identifier(actor_id)
            reason = _reason(reason)
        with self.store.tx() as t:
            current = self._breaker(t)
            if current["state"] == "open":
                return current
            event = self._append_breaker(t, current, "open", source, reason_code, reason, actor_id)
            _journal(self.store, "model_breaker_opened", event_id=event["id"], environment=self.environment)
            if source != "admin":
                _system_audit(t, "billing.risk.breaker_opened", "model_risk_breaker", event["id"],
                              {"billing_environment": self.environment, "source": source, "reason_code": reason_code})
            self.store.after_commit(lambda: notify_admins(
                self.store, "billing.risk.breaker_opened", params={"risk": source},
                dedupe_key="risk:breaker:" + event["id"], object_id=event["id"]))
        return event

    def close_breaker(self, *, actor_id: str, reason: str) -> dict:
        identifier(actor_id)
        reason = _reason(reason)
        with self.store.tx() as t:
            current = self._breaker(t)
            if current["state"] != "open":
                raise ServiceError(409, "risk_breaker_not_open", "付费调用总闸当前没有关闭任何调用")
            return self._append_breaker(t, current, "closed", "admin", "admin_closed", reason, actor_id)

    def _append_breaker(self, t, current, state, source, reason_code, reason, actor_id):
        event = new_id("rbrk")
        t.execute("INSERT INTO model_risk_breaker_events(id,environment,seq,state,source,reason_code,reason,actor_id,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (event, self.environment, current["seq"] + 1, state, source,
                                                 reason_code, reason, actor_id, self._now()))
        return dict(t.execute("SELECT * FROM model_risk_breaker_events WHERE id=?", (event,)).fetchone())

    def breaker_history(self, *, limit: int = 20) -> list[dict]:
        integer(limit, positive=True, maximum=100)
        return self.store.all("SELECT * FROM model_risk_breaker_events WHERE environment=? ORDER BY seq DESC LIMIT ?",
                              (self.environment, limit))

    # ------------------------------------------------------------------ allowlist
    def allow(self, payer_kind: str, payer_id: str, *, actor_id: str, reason: str) -> dict:
        if payer_kind not in PAYER_KINDS:
            raise ServiceError(400, "risk_allowlist_invalid", "放行名单条目无效")
        identifier(payer_id)
        identifier(actor_id)
        reason = _reason(reason)
        with self.store.tx() as t:
            if payer_kind == "user":
                exists = t.execute("SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL AND status<>'deleted'",
                                   (payer_id,)).fetchone()
            else:
                exists = t.execute("SELECT 1 FROM organizations WHERE id=?", (payer_id,)).fetchone()
            if not exists:
                raise ServiceError(404, "risk_allowlist_payer_not_found", "未找到要放行的账户或组织")
            if self._allowlisted(t, payer_kind, payer_id):
                raise ServiceError(409, "risk_allowlist_exists", "该付款方已在放行名单中")
            entry = new_id("rallow")
            t.execute("INSERT INTO model_risk_allowlist(id,environment,payer_kind,payer_id,added_by,add_reason,added_at) "
                      "VALUES (?,?,?,?,?,?,?)", (entry, self.environment, payer_kind, payer_id, actor_id, reason, self._now()))
            return dict(t.execute("SELECT * FROM model_risk_allowlist WHERE id=?", (entry,)).fetchone())

    def disallow(self, entry_id: str, *, actor_id: str, reason: str) -> dict:
        identifier(actor_id)
        reason = _reason(reason)
        with self.store.tx() as t:
            row = t.execute("SELECT * FROM model_risk_allowlist WHERE id=? AND environment=?",
                            (identifier(entry_id), self.environment)).fetchone()
            if row is None:
                raise ServiceError(404, "risk_allowlist_not_found", "未找到这条放行名单记录")
            if row["removed_at"] is not None:
                raise ServiceError(409, "risk_allowlist_removed", "这条放行名单记录已移除")
            t.execute("UPDATE model_risk_allowlist SET removed_at=?,removed_by=?,remove_reason=? WHERE id=?",
                      (self._now(), actor_id, reason, row["id"]))
            return dict(t.execute("SELECT * FROM model_risk_allowlist WHERE id=?", (row["id"],)).fetchone())

    def allowlist(self, *, before: int | None = None, limit: int = 50) -> dict:
        integer(limit, positive=True, maximum=100)
        clause, args = "", []
        if before is not None:
            clause, args = " AND added_at<?", [integer(before)]
        rows = self.store.all("SELECT * FROM model_risk_allowlist WHERE environment=? AND removed_at IS NULL" + clause +
                              " ORDER BY added_at DESC, id DESC LIMIT ?", (self.environment, *args, limit + 1))
        items = rows[:limit]
        return {"items": items, "next_before": items[-1]["added_at"] if len(rows) > limit else None}


def trip_platform_breaker(store, *, environment: str, source: str, reason_code: str) -> dict:
    """The one way to close paid dispatch automatically: the balance monitor (below the hard
    threshold or upstream unavailable) and the gateway's upstream 402 both call this."""
    return RiskControls(store, environment=environment).open_breaker(source=source, reason_code=reason_code)


def alert_payment_webhook_failure(store, *, environment: str, provider: str, code: str) -> int:
    """Hook for the payment channel: a webhook that failed verification or processing. Only the
    provider name and a stable code travel; never a payload, signature or customer detail."""
    identifier(provider)
    identifier(code)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return notify_admins(store, "billing.risk.payment_webhook_failed", params={"provider": provider, "code": code},
                         dedupe_key=f"risk:webhook:{environment}:{provider}:{code}:{day}",
                         object_id=f"{environment}:{provider}")
