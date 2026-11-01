"""FIXTURE, not product code: the cost counters and the velocity scan as they were at 5db93f86.

Verbatim copies of ``CostAdmission._counters`` (packages/core/synwevo_core/billing/admission.py)
and of ``RiskControls.guard`` (packages/core/synwevo_core/billing/risk.py) from commit 5db93f86,
before the exact cost rollup replaced their row-by-row scans, 10,000-row caps included. Only
test_cost_rollup_equivalence.py uses them, as the "legacy" implementation the rollup and its
oracle are compared with (contract §11, "Equivalence property test"). Do not import them from
product code and do not "fix" them: they are evidence of what the earlier code computed.

The only edits are the scaffolding that lets the copies run outside their classes:

* ``LegacyCostCounters`` stands in for ``CostAdmission``: ``_counters`` reads nothing from its
  instance but ``self.ledger.environment``, which the constructor provides.
* ``LegacyRiskControls`` subclasses today's ``RiskControls`` for the breaker, allowlist,
  suspension and payer readers, which this round did not change; ``guard`` is the 5db93f86 body.
* ``velocity_scan`` is the velocity part of that same ``guard`` body (from ``windows = []`` to the
  end of its row loop), returning the two totals instead of comparing them with the thresholds,
  so the totals themselves can be compared.

The module-level constants and helpers below are copied from the same files at 5db93f86.

Cost-capacity phase 2 adds ``LegacyPayerBudgets._exposure``: a verbatim copy of
``PayerBudgets._exposure`` (packages/core/synwevo_core/billing/payer_budgets.py) at 0bcce176, before
the per-wallet spend rollup replaced its row-by-row read, 10,000-row cap included. Its scaffolding is
the class and its ``CAPACITY`` (the only attribute the copy reads), as at 0bcce176.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from fractions import Fraction
from types import SimpleNamespace

from synwevo_core.billing.admission import CostPolicy  # noqa: F401 - the copied signature names it
from synwevo_core.billing.models import BillingError, identifier, integer
from synwevo_core.billing.risk import BrakeTripped, RiskControls, RiskPolicy  # noqa: F401

# --- admission.py at 5db93f86 -------------------------------------------------------------------
_MAX_RECORDS = 10_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
#: The longest a customer hold can outlive its dispatch (Ledger.max_hold_seconds' ceiling), and so
#: the longest an admission can stay an open unknown or in flight. Older ones are never scanned.
_OPEN_S = 86_400


def _windows(now: int) -> dict[str, int]:
    """Fixed UTC periods; epoch day zero is Thursday, weeks start on Monday."""
    try:
        date = _EPOCH + timedelta(seconds=now)
    except OverflowError:
        raise BillingError("invalid_cost_time") from None
    day = now // 86400 * 86400
    month = (date.replace(day=1, hour=0, minute=0, second=0) - _EPOCH).days * 86400
    return {"daily": day, "weekly": day - ((now // 86400 + 3) % 7) * 86400, "monthly": month}


class LegacyCostCounters:
    """FIXTURE stand-in for the 5db93f86 ``CostAdmission``: only ``_counters``."""

    def __init__(self, environment: str):
        self.ledger = SimpleNamespace(environment=environment)

    def _counters(self, t, policy: CostPolicy, now: int, *, adding: int = 0) -> dict:
        windows = _windows(now)
        unit = t.execute("SELECT scale FROM billing_units WHERE code=?", (policy.unit.code,)).fetchone()
        if unit is not None and unit[0] != policy.unit.scale:
            raise BillingError("cost_unit_mismatch")
        # Do not scope by namespace, policy revision, payer, key, or unit. A differing unit is a
        # conflict, never permission to hide existing exposure.
        # Two disjoint indexed ranges: admissions in the cost windows, and pending ones before
        # them that can still be open (admitted within _OPEN_S). A join predicate 'usage IS NULL'
        # over all admissions would scan all settled history; an unknown whose hold ended before
        # the windows is final at its bound and never scanned again.
        # Bound candidate lookup itself before joining immutable cost facts.
        candidates = (
            "SELECT attempt_key FROM model_cost_admissions INDEXED BY model_cost_admissions_scope"
            " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at>=?"
            " UNION ALL SELECT attempt_key FROM model_cost_pending INDEXED BY model_cost_pending_scope"
            " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at<? AND admitted_at>=? LIMIT ?"
        )
        earliest = min(windows.values())
        scope = (self.ledger.environment, policy.provider, policy.account_ref, earliest)
        args = (*scope, *scope, now - _OPEN_S, _MAX_RECORDS + 1)
        source = (" FROM (" + candidates + ") selected"
                  " JOIN model_cost_admissions a ON a.attempt_key=selected.attempt_key"
                  " JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key"
                  " LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key"
                  " LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key")
        # A capped cardinality probe detects overflow BEFORE materializing/summing facts.
        # At most 10,000 records enter Python; the extra SQL count is only an overflow sentinel.
        # Both branches use scope indexes; historical unknowns come from the pending
        # projection, so settled history is not scanned for this count.
        count = t.execute("SELECT COUNT(*) FROM (" + candidates + ")", args).fetchone()[0]
        if count + adding > _MAX_RECORDS:
            raise BillingError("cost_accounting_capacity")
        totals = {name: Fraction(0) for name in windows}
        unknown = unknown_count = inflight = absorbed = absorbed_count = 0
        rows = t.execute(
            "SELECT a.unit,a.reserved_units,a.admitted_at,a.expires_at,c.status AS call_status,"
            "c.deadline AS call_deadline,e.ending,"
            "u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit" + source + " LIMIT ?",
            (*args, _MAX_RECORDS),
        )
        for row in rows:
            if row["unit"] != policy.unit.code:
                raise BillingError("cost_unit_mismatch")
            if row["admitted_at"] > now:
                raise BillingError("cost_clock_regressed")
            if row["usage_key"] is None:
                cost = integer(row["reserved_units"])
                # Unknown usage is open exposure while the upstream may still be working (its
                # in-flight window) and, unless the platform stopped the stream itself, while the
                # payer's hold is open and evidence could still settle it. After that it is final:
                # its conservative bound stays in the cost windows below (and in the payer's and
                # route's velocity windows, billing/risk.py), but no longer in the unknown limits.
                in_flight = row["expires_at"] > now
                hold_open = row["call_status"] == "reserved" and row["call_deadline"] > now
                if in_flight or (hold_open and row["ending"] is None):
                    unknown += cost
                    unknown_count += 1
                elif row["admitted_at"] >= earliest:
                    absorbed += cost
                    absorbed_count += 1
                inflight += int(in_flight)
            else:
                if row["cost_unit"] != policy.unit.code:
                    raise BillingError("cost_unit_mismatch")
                try:
                    numerator, denominator = int(row["cost_n"]), int(row["cost_d"])
                    if numerator < 0 or denominator <= 0:
                        raise ValueError
                    cost = Fraction(numerator, denominator)
                except (ValueError, TypeError):
                    raise BillingError("invalid_cost_fact") from None
            for name, start in windows.items():
                if row["admitted_at"] >= start:
                    totals[name] += cost
        return {"windows": windows, "totals": totals, "unknown_reserved_units": unknown,
                "unknown_count": unknown_count, "inflight": inflight, "records": count,
                "absorbed_unknown_units": absorbed, "absorbed_unknown_count": absorbed_count}


# --- risk.py at 5db93f86 ------------------------------------------------------------------------
# The same bound as the cost admission counters: never materialize unbounded history in a send path.
MAX_RECORDS = 10_000


def allowlist_required(environment: str, risk: RiskPolicy | None) -> bool:
    return risk.allowlist_only if risk is not None else environment == "live"


def _ceil(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


class LegacyRiskControls(RiskControls):
    """FIXTURE: today's RiskControls with the 5db93f86 ``guard`` (its row-by-row velocity scan)."""

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

    def velocity_scan(self, t, *, policy, risk, payer, route: str, now: int) -> tuple[Fraction, Fraction]:
        """The velocity part of the 5db93f86 ``guard`` above, verbatim, returning its totals.

        ``payer`` is what ``payer_of`` returns (only ``payer["owner"]`` is read). A total whose
        threshold is None is whatever the scan of the other window happened to add: compare only
        the totals of windows with a threshold."""
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
        return payer_total, route_total


# --- payer_budgets.py at 0bcce176 ----------------------------------------------------------------
class LegacyPayerBudgets:
    """FIXTURE stand-in for the 0bcce176 ``PayerBudgets``: only ``_exposure`` (verbatim) and its CAPACITY."""

    CAPACITY = 10_000

    def _exposure(self, t, account, windows):
        if account is None:
            return {name: {"start": start, "end": end, "spent": 0, "held": 0}
                    for name, (start, end) in windows.items()}
        # Disjoint indexed ranges, bounded before the settlement join. No all-history scan.
        rows = t.execute("WITH candidates AS MATERIALIZED ("
            "SELECT id,status,hold_units,created_at FROM billing_calls WHERE account_id=? AND status='reserved' "
            "UNION ALL SELECT id,status,hold_units,created_at FROM billing_calls "
            "WHERE account_id=? AND status='settled' AND created_at>=? LIMIT ?) "
            "SELECT c.*,j.amount FROM candidates c LEFT JOIN billing_settlements s ON s.call_key=c.id "
            "LEFT JOIN billing_journal j ON j.id=s.journal_id",
            (account, account, min(start for start, _ in windows.values()), self.CAPACITY + 1)).fetchall()
        if len(rows) > self.CAPACITY:
            raise BillingError("payer_budget_capacity")
        if any(row["status"] == "settled" and row["amount"] is None for row in rows):
            raise BillingError("payer_budget_inconsistent")
        held = sum(row["hold_units"] for row in rows if row["status"] == "reserved")
        return {name: {"start": start, "end": end, "held": held,
                "spent": sum(row["amount"] for row in rows if row["status"] == "settled" and row["created_at"] >= start)}
                for name, (start, end) in windows.items()}
