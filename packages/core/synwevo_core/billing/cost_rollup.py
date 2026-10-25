"""Readers, verifier, rebuild, prune and latch of the exact cost rollup.

The rollup itself (tables, gate, triggers, replay) is ``synwevo_core.suite_schema.model_cost_rollup``;
docs/platform/billing-ledger.md ("Cost rollup") describes both. This module:

* ``counters`` — what ``CostAdmission._counters`` returns, read from day buckets, the class-X
  exceptions and the open set (inflight + hold), in the fixed check order of the contract
  (scale, faults, clock, day rows, Monday-the-1st tail, exceptions, open set, parity, absorbed).
  No read grows with settled volume: at most 31 day rows per unit and denominator, the open set
  (bounded by max_unknown), the exceptions and one index-only count of today's pending rows.
* ``velocity`` — the payer and route totals of ``RiskControls.guard`` from 1/60/3,600 s buckets
  (at most 143 rows per window, summed in SQL) plus the class-X exceptions.
* ``reference_counters`` / ``reference_velocity`` — the uncapped oracle: the earlier row-by-row
  algorithms over the facts, without their 10,000-row caps. The verifier compares against them;
  so does the equivalence property test.
* ``CostRollup`` — ``check`` (independent recomputation from the facts in one read snapshot),
  ``record`` (per-scope faults/audits under BEGIN IMMEDIATE; superseded by a newer rebuild),
  ``verify`` (both), ``rebuild`` (audited, never automatic), ``prune`` and ``latch``.

Refusals: ``cost_rollup_inconsistent`` (an open fault, or a failed structure/stale/parity/absorbed
check: ``RollupInconsistent``), ``cost_accounting_capacity`` (more than 10,000 open rows or
class-X facts in the windows), ``risk_accounting_capacity`` (more than 10,000 class-X facts for
the payer or route), and the earlier ``cost_unit_mismatch`` / ``cost_clock_regressed`` /
``invalid_cost_fact`` / ``invalid_cost_time``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import logging
import re
import secrets
import time

from ..suite_schema import model_cost_rollup as schema
from .models import BillingError, Unit, identifier, integer

log = logging.getLogger("synwevo.billing.cost_rollup")

B, G, DAY, HORIZON_S, SPANS = schema.B, schema.G, schema.DAY, schema.HORIZON_S, schema.SPANS
#: Open set and class-X caps: the only remaining row limits on the admission path.
MAX_OPEN = 10_000
MAX_EXCEPTIONS = 10_000
#: How long an admission can stay open (Ledger.max_hold_seconds' ceiling, I1/I7).
OPEN_S = 86_400
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SCOPE = "environment=? AND provider=? AND account_ref=?"
_SUMS = ",".join(f"SUM({c}) AS {c}" for c in schema.BUCKET_VALUES)
_VALUES = ",".join(schema.BUCKET_VALUES)


def windows(now: int) -> dict[str, int]:
    """Fixed UTC periods; epoch day zero is Thursday, weeks start on Monday."""
    try:
        date = _EPOCH + timedelta(seconds=now)
    except OverflowError:
        raise BillingError("invalid_cost_time") from None
    day = now // 86400 * 86400
    month = (date.replace(day=1, hour=0, minute=0, second=0) - _EPOCH).days * 86400
    return {"daily": day, "weekly": day - ((now // 86400 + 3) % 7) * 86400, "monthly": month}


class RollupInconsistent(BillingError):
    """A read-path detection (structure, stale open row, parity, absorbed). Like BrakeTripped it is
    latched by ``CostAdmission.after_refusal`` once the refusing transaction has rolled back,
    unless a rebuild newer than ``rebuild_seq`` has happened since."""

    def __init__(self, scope: tuple, check: str, rebuild_seq: int):
        super().__init__("cost_rollup_inconsistent")
        self.scope, self.check, self.rebuild_seq = tuple(scope), check, rebuild_seq


class RebuildRefused(BillingError):
    """A rebuild that would not leave a consistent rollup; nothing was changed."""

    def __init__(self, *, attempt_keys=(), differences=()):
        super().__init__("cost_rollup_rebuild_refused")
        self.attempt_keys, self.differences = list(attempt_keys), list(differences)


def _last_rebuild(t, scope):
    """The newest rebuild row that covered ``scope``: its own, or a global backfill (two index seeks)."""
    best = None
    for key in (tuple(scope), ("", "", "")):
        row = t.execute("SELECT seq,id,environment,provider,account_ref,actor,finished_at FROM model_cost_rollup_rebuilds "
                        "INDEXED BY model_cost_rollup_rebuilds_scope WHERE " + _SCOPE + " ORDER BY seq DESC LIMIT 1",
                        key).fetchone()
        if row is not None and (best is None or row["seq"] > best["seq"]):
            best = row
    return best


def rebuild_seq(t, scope) -> int:
    """The sequence number of the newest rebuild that covered ``scope`` (0 if none)."""
    row = _last_rebuild(t, scope)
    return row["seq"] if row is not None else 0


def _open_fault(t, scope) -> bool:
    return t.execute("SELECT 1 FROM model_cost_rollup_faults INDEXED BY model_cost_rollup_faults_open WHERE "
                     + _SCOPE + " AND resolved_at IS NULL LIMIT 1", tuple(scope)).fetchone() is not None


def _structure(row, *, main: bool) -> bool:
    """Integer types, limbs in range, counts ordered, rbound <= bound; class-Q rows cost only."""
    values = [row[c] for c in schema.BUCKET_VALUES]
    if any(type(v) is not int for v in values):
        return False
    if not (0 <= row["bound_lo"] < B and 0 <= row["rbound_lo"] < B and 0 <= row["cost_lo"] < B
            and 0 <= row["cost_frac"] < G and min(row["bound_hi"], row["rbound_hi"], row["cost_hi"], row["cost_carry"]) >= 0
            and 0 <= row["n_exc"] <= row["n_res"] <= row["n_adm"] and 0 <= row["n_cross"] <= row["n_res"]):
        return False
    if (row["rbound_hi"], row["rbound_lo"]) > (row["bound_hi"], row["bound_lo"]):
        return False
    if not main and any(row[c] for c in schema.BUCKET_VALUES if c not in ("cost_hi", "cost_lo")):
        return False
    return True


def _value(row, den) -> Fraction:
    """bound - rbound + exact resolved cost (den 0), or the class-Q numerators over ``den``."""
    if den:
        return Fraction(row["cost_hi"] * B + row["cost_lo"], den)
    return (row["bound_hi"] * B + row["bound_lo"] - row["rbound_hi"] * B - row["rbound_lo"]
            + row["cost_hi"] * B + row["cost_lo"] + row["cost_carry"] + Fraction(row["cost_frac"], G))


def _admission_cost(n, d) -> Fraction:
    """The cost admission's validity rule for a class-X fact (as before the rollup)."""
    try:
        numerator, denominator = int(n), int(d)
        if numerator < 0 or denominator <= 0:
            raise ValueError
        return Fraction(numerator, denominator)
    except (ValueError, TypeError):
        raise BillingError("invalid_cost_fact") from None


def _risk_cost(n, d) -> Fraction:
    """The velocity brake's validity rule (as before the rollup): '-5' counts as -5."""
    try:
        return Fraction(int(n), int(d))
    except (TypeError, ValueError, ZeroDivisionError):
        raise BillingError("invalid_cost_fact") from None


# ---------------------------------------------------------------- CostAdmission._counters
def counters(t, *, environment: str, provider: str, account_ref: str, unit: Unit, now: int,
             records: bool = False) -> dict:
    """Exact counters of one provider account (scope), in the contract's fixed check order.

    ``records`` (snapshot only) adds today's definition of records (admissions in the windows
    plus pending ones in the Monday-the-1st tail). ``rows_read`` counts rows fetched."""
    scope = (environment, provider, account_ref)
    win = windows(now)
    P = unit.code
    read = 0
    # 0. Scale.
    row = t.execute("SELECT scale FROM billing_units WHERE code=?", (P,)).fetchone()
    if row is not None and row[0] != unit.scale:
        raise BillingError("cost_unit_mismatch")
    # 1. Faults.
    if _open_fault(t, scope):
        raise BillingError("cost_rollup_inconsistent")
    # 2. Clock.
    if t.execute("SELECT 1 FROM model_cost_admissions INDEXED BY model_cost_admissions_scope WHERE " + _SCOPE
                 + " AND admitted_at>? LIMIT 1", (*scope, now)).fetchone():
        raise BillingError("cost_clock_regressed")
    E = min(win.values())
    L = min(E, now - OPEN_S)
    today = win["daily"]

    def inconsistent(check):
        return RollupInconsistent(scope, check, rebuild_seq(t, scope))

    # 3. Day rows.
    days = t.execute("SELECT start,unit,den," + _VALUES + " FROM model_cost_buckets WHERE " + _SCOPE
                     + " AND dim='account' AND member='' AND span=86400 AND start>=?", (*scope, E)).fetchall()
    read += len(days)
    for r in days:
        if r["unit"] != P or r["n_cross"]:
            raise BillingError("cost_unit_mismatch")
    totals = {name: Fraction(0) for name in win}
    pending_units = pending_count = admitted = exceptions_expected = today_unresolved = 0
    for r in days:
        den = r["den"]
        if type(den) is not int or den < 0 or type(r["start"]) is not int or not _structure(r, main=den == 0):
            raise inconsistent("structure")
        amount = _value(r, den)
        for name, start in win.items():
            if r["start"] >= start:
                totals[name] += amount
        if den == 0:
            pending_units += r["bound_hi"] * B + r["bound_lo"] - r["rbound_hi"] * B - r["rbound_lo"]
            pending_count += r["n_adm"] - r["n_res"]
            admitted += r["n_adm"]
            exceptions_expected += r["n_exc"]
            if r["start"] == today:
                today_unresolved += r["n_adm"] - r["n_res"]
    # 4. Monday the 1st only: a pending admission in another unit in [L, E) refuses, as before.
    if L < E:
        tail = t.execute("SELECT unit,n_adm,n_res FROM model_cost_buckets WHERE " + _SCOPE
                         + " AND dim='account' AND member='' AND span=86400 AND start=? AND den=0",
                         (*scope, E - DAY)).fetchall()
        read += len(tail)
        if any(r["unit"] != P and r["n_adm"] != r["n_res"] for r in tail):
            read += 1
            if t.execute("SELECT 1 FROM model_cost_pending p INDEXED BY model_cost_pending_scope "
                         "JOIN model_cost_admissions a ON a.attempt_key=p.attempt_key "
                         "WHERE p.environment=? AND p.provider=? AND p.account_ref=? AND p.admitted_at>=? "
                         "AND p.admitted_at<? AND a.unit<>? LIMIT 1", (*scope, L, E, P)).fetchone():
                raise BillingError("cost_unit_mismatch")
    # 5. Class-X facts in the windows, parsed exactly as before.
    xs = t.execute("SELECT i.attempt_key,i.admitted_at,a.unit,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit "
                   "FROM model_cost_exceptions i INDEXED BY model_cost_exceptions_scope "
                   "LEFT JOIN model_cost_admissions a ON a.attempt_key=i.attempt_key "
                   "LEFT JOIN billing_usage u ON u.attempt_key=i.attempt_key "
                   "WHERE i.environment=? AND i.provider=? AND i.account_ref=? AND i.admitted_at>=? LIMIT ?",
                   (*scope, E, MAX_EXCEPTIONS + 1)).fetchall()
    read += len(xs)
    if len(xs) > MAX_EXCEPTIONS:
        raise BillingError("cost_accounting_capacity")
    if len(xs) != exceptions_expected or any(r["usage_key"] is None or r["unit"] is None for r in xs):
        raise inconsistent("exceptions")
    for r in xs:
        if r["unit"] != P or r["cost_unit"] != P:
            raise BillingError("cost_unit_mismatch")
        cost = _admission_cost(r["cost_n"], r["cost_d"])
        for name, start in win.items():
            if r["admitted_at"] >= start:
                totals[name] += cost
    # 6. The open set: still in flight, or a reserved hold without a recorded local stop.
    rows = t.execute(
        "SELECT o.attempt_key AS key,a.attempt_key,a.environment,a.provider,a.account_ref,a.unit,a.reserved_units,"
        "a.admitted_at,a.expires_at,att.call_key,c.status AS call_status,c.deadline AS call_deadline,e.ending,"
        "u.attempt_key AS usage_key,f.attempt_key AS f_key,f.expires_at AS f_expires,"
        "h.attempt_key AS h_key,h.call_key AS h_call,h.deadline AS h_deadline FROM ("
        "SELECT attempt_key FROM model_cost_inflight INDEXED BY model_cost_inflight_open WHERE " + _SCOPE
        + " AND expires_at>? UNION SELECT attempt_key FROM model_cost_hold INDEXED BY model_cost_hold_open WHERE "
        + _SCOPE + " AND deadline>? LIMIT ?) o "
        "LEFT JOIN model_cost_admissions a ON a.attempt_key=o.attempt_key "
        "LEFT JOIN billing_attempts att ON att.id=a.attempt_key LEFT JOIN billing_calls c ON c.id=att.call_key "
        "LEFT JOIN model_cost_endings e ON e.attempt_key=o.attempt_key "
        "LEFT JOIN billing_usage u ON u.attempt_key=o.attempt_key "
        "LEFT JOIN model_cost_inflight f ON f.attempt_key=o.attempt_key "
        "LEFT JOIN model_cost_hold h ON h.attempt_key=o.attempt_key",
        (*scope, now, *scope, now, MAX_OPEN + 1)).fetchall()
    read += len(rows)
    if len(rows) > MAX_OPEN:
        raise BillingError("cost_accounting_capacity")
    for r in rows:
        if (r["attempt_key"] is None or r["usage_key"] is not None
                or (r["environment"], r["provider"], r["account_ref"]) != scope
                or (r["f_key"] is not None and r["f_expires"] != r["expires_at"])
                or (r["h_key"] is not None and (r["ending"] is not None or r["call_status"] != "reserved"
                                                or r["h_deadline"] != r["call_deadline"] or r["h_call"] != r["call_key"]))):
            raise inconsistent("stale")
    unknown = unknown_count = inflight = open_units = open_count = 0
    for r in rows:
        if r["admitted_at"] < L:
            continue
        if r["unit"] != P:
            raise BillingError("cost_unit_mismatch")
        cost = integer(r["reserved_units"])
        # Verbatim the earlier classification (docs/platform/model-risk.md §1).
        in_flight = r["expires_at"] > now
        hold_open = r["call_status"] == "reserved" and r["call_deadline"] > now
        if in_flight or (hold_open and r["ending"] is None):
            unknown += cost
            unknown_count += 1
            if r["admitted_at"] >= E:
                open_units += cost
                open_count += 1
        inflight += int(in_flight)
    # 7. Parity: today's pending projection agrees with today's unresolved admissions.
    pending_today = t.execute("SELECT COUNT(*) FROM model_cost_pending INDEXED BY model_cost_pending_scope WHERE "
                              + _SCOPE + " AND admitted_at>=?", (*scope, today)).fetchone()[0]
    read += 1
    if pending_today != today_unresolved:
        raise inconsistent("parity")
    # 8. Absorbed: unresolved bounds in the windows that are no longer open.
    absorbed, absorbed_count = pending_units - open_units, pending_count - open_count
    if absorbed < 0 or absorbed_count < 0:
        raise inconsistent("absorbed")
    out = {"windows": win, "totals": totals, "unknown_reserved_units": unknown, "unknown_count": unknown_count,
           "inflight": inflight, "absorbed_unknown_units": absorbed, "absorbed_unknown_count": absorbed_count}
    # 9. Snapshot only.
    if records:
        tail_pending = 0
        if L < E:
            tail_pending = t.execute("SELECT COUNT(*) FROM model_cost_pending INDEXED BY model_cost_pending_scope "
                                     "WHERE " + _SCOPE + " AND admitted_at>=? AND admitted_at<?", (*scope, L, E)).fetchone()[0]
            read += 1
        out["records"] = admitted + tail_pending
    out["rows_read"] = read
    return out


# ---------------------------------------------------------------- RiskControls.guard velocity
def _bucket_sums(t, scope, dim, member, now, w):
    """Disjoint, exact: span 1 over [a,a1), span 60 over [a1,a2), span 3600 from a2 (<= 143 rows)."""
    a = now - w + 1
    a1 = -(-a // 60) * 60
    a2 = -(-a // 3600) * 3600
    part = "SELECT * FROM model_cost_buckets WHERE " + _SCOPE + " AND dim=? AND member=? AND span=? AND start>=?"
    return t.execute(
        "SELECT unit,den,COUNT(*) AS n," + _SUMS + ",SUM(CASE WHEN typeof(den)='integer' AND den>=0 AND "
        + " AND ".join(f"typeof({c})='integer'" for c in schema.BUCKET_VALUES) + " THEN 0 ELSE 1 END) AS broken FROM ("
        + part + " AND start<? UNION ALL " + part + " AND start<? UNION ALL " + part + ") GROUP BY unit,den",
        (*scope, dim, member, 1, a, a1, *scope, dim, member, 60, a1, a2, *scope, dim, member, 3600, a2)).fetchall()


def velocity(t, *, environment: str, provider: str, account_ref: str, unit: Unit, owner: str, route: str,
             now: int, risk) -> tuple[Fraction, Fraction]:
    """(payer total, route total) of the velocity brake, from buckets plus class-X facts.

    A total is computed only for a window whose cost threshold is set (the other is 0). Runs only
    after ``counters`` (I5: no admission after ``now``)."""
    scope = (environment, provider, account_ref)
    P = unit.code
    sides = {"payer": (owner, risk.payer_window_s, risk.payer_window_cost),
             "route": (route, risk.route_window_s, risk.route_window_cost)}
    windows_set = [w for _, w, cost in sides.values() if cost is not None]
    if not windows_set:
        return Fraction(0), Fraction(0)
    W = max(windows_set)
    # 1. Units over the largest window, for the payer's wallet owner and for the route.
    sums = {}
    for dim, (member, _, _) in sides.items():
        sums[(dim, W)] = _bucket_sums(t, scope, dim, member, now, W)
        for g in sums[(dim, W)]:
            if g["unit"] != P or g["n_cross"]:
                raise BillingError("cost_unit_mismatch")
    # 2. Class-X facts within W, joined as before (owner OR route).
    xs = t.execute(
        "SELECT a.unit,a.admitted_at,a.reserved_units,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit,"
        "att.requested_model AS route,acc.owner AS owner FROM model_cost_exceptions i INDEXED BY model_cost_exceptions_scope "
        "JOIN model_cost_admissions a ON a.attempt_key=i.attempt_key "
        "JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key "
        "JOIN billing_accounts acc ON acc.id=c.account_id LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
        "WHERE i.environment=? AND i.provider=? AND i.account_ref=? AND i.admitted_at>? "
        "AND (acc.owner=? OR att.requested_model=?) LIMIT ?",
        (*scope, now - W, owner, route, MAX_EXCEPTIONS + 1)).fetchall()
    if len(xs) > MAX_EXCEPTIONS:
        raise BillingError("risk_accounting_capacity")
    parsed = []
    for r in xs:
        if r["unit"] != P or (r["usage_key"] is not None and r["cost_unit"] != P):
            raise BillingError("cost_unit_mismatch")
        parsed.append((r, _risk_cost(r["cost_n"], r["cost_d"]) if r["usage_key"] is not None
                       else Fraction(integer(r["reserved_units"]))))
    # 3. Each total: its own window's buckets plus its own class-X facts.
    out = {}
    for dim, (member, w, cost) in sides.items():
        if cost is None:
            out[dim] = Fraction(0)
            continue
        groups = sums.get((dim, w)) or _bucket_sums(t, scope, dim, member, now, w)
        total = Fraction(0)
        for g in groups:
            if g["broken"] or g["den"] < 0:
                raise RollupInconsistent(scope, "velocity_structure", rebuild_seq(t, scope))
            total += _value(g, g["den"])
        key = "owner" if dim == "payer" else "route"
        total += sum((c for r, c in parsed if r[key] == member and r["admitted_at"] > now - w), Fraction(0))
        out[dim] = total
    return out["payer"], out["route"]


# ---------------------------------------------------------------- the uncapped oracle
def reference_counters(t, *, environment: str, provider: str, account_ref: str, unit: Unit, now: int) -> dict:
    """The earlier ``CostAdmission._counters`` over the facts, without its 10,000-record cap.

    Same result dict as ``counters(..., records=True)`` minus ``rows_read``; same refusal codes
    (cost_unit_mismatch, cost_clock_regressed, invalid_cost_fact, invalid_cost_time)."""
    win = windows(now)
    row = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
    if row is not None and row[0] != unit.scale:
        raise BillingError("cost_unit_mismatch")
    earliest = min(win.values())
    scope = (environment, provider, account_ref, earliest)
    candidates = ("SELECT attempt_key FROM model_cost_admissions INDEXED BY model_cost_admissions_scope"
                  " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at>=?"
                  " UNION ALL SELECT attempt_key FROM model_cost_pending INDEXED BY model_cost_pending_scope"
                  " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at<? AND admitted_at>=?")
    args = (*scope, *scope, now - OPEN_S)
    rows = t.execute(
        "SELECT a.unit,a.reserved_units,a.admitted_at,a.expires_at,c.status AS call_status,"
        "c.deadline AS call_deadline,e.ending,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit"
        " FROM (" + candidates + ") selected JOIN model_cost_admissions a ON a.attempt_key=selected.attempt_key"
        " JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key"
        " LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key"
        " LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key", args).fetchall()
    count = t.execute("SELECT COUNT(*) FROM (" + candidates + ")", args).fetchone()[0]
    totals = {name: Fraction(0) for name in win}
    unknown = unknown_count = inflight = absorbed = absorbed_count = 0
    for r in rows:
        if r["unit"] != unit.code:
            raise BillingError("cost_unit_mismatch")
        if r["admitted_at"] > now:
            raise BillingError("cost_clock_regressed")
        if r["usage_key"] is None:
            cost = integer(r["reserved_units"])
            in_flight = r["expires_at"] > now
            hold_open = r["call_status"] == "reserved" and r["call_deadline"] > now
            if in_flight or (hold_open and r["ending"] is None):
                unknown += cost
                unknown_count += 1
            elif r["admitted_at"] >= earliest:
                absorbed += cost
                absorbed_count += 1
            inflight += int(in_flight)
        else:
            if r["cost_unit"] != unit.code:
                raise BillingError("cost_unit_mismatch")
            cost = _admission_cost(r["cost_n"], r["cost_d"])
        for name, start in win.items():
            if r["admitted_at"] >= start:
                totals[name] += cost
    return {"windows": win, "totals": totals, "unknown_reserved_units": unknown, "unknown_count": unknown_count,
            "inflight": inflight, "records": count, "absorbed_unknown_units": absorbed,
            "absorbed_unknown_count": absorbed_count}


def reference_velocity(t, *, environment: str, provider: str, account_ref: str, unit: Unit, owner: str,
                       route: str, now: int, risk) -> tuple[Fraction, Fraction]:
    """The earlier ``RiskControls.guard`` velocity loop over the facts, without its cap; a total
    is computed only for a window whose cost threshold is set (the other is 0)."""
    windows_set = [w for w, cost in ((risk.payer_window_s, risk.payer_window_cost),
                                     (risk.route_window_s, risk.route_window_cost)) if cost is not None]
    if not windows_set:
        return Fraction(0), Fraction(0)
    rows = t.execute(
        "SELECT a.reserved_units,a.admitted_at,a.unit,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit,"
        "att.requested_model AS route,acc.owner AS owner "
        "FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope "
        "JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key "
        "JOIN billing_accounts acc ON acc.id=c.account_id "
        "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
        "WHERE a.environment=? AND a.provider=? AND a.account_ref=? AND a.admitted_at>? "
        "AND (acc.owner=? OR att.requested_model=?)",
        (environment, provider, account_ref, now - max(windows_set), owner, route)).fetchall()
    payer_total = route_total = Fraction(0)
    for r in rows:
        if r["unit"] != unit.code or (r["usage_key"] is not None and r["cost_unit"] != unit.code):
            raise BillingError("cost_unit_mismatch")
        cost = Fraction(integer(r["reserved_units"])) if r["usage_key"] is None else _risk_cost(r["cost_n"], r["cost_d"])
        if r["owner"] == owner and r["admitted_at"] > now - risk.payer_window_s:
            payer_total += cost
        if r["route"] == route and r["admitted_at"] > now - risk.route_window_s:
            route_total += cost
    return (payer_total if risk.payer_window_cost is not None else Fraction(0),
            route_total if risk.route_window_cost is not None else Fraction(0))


# ---------------------------------------------------------------- independent recomputation
_DIGITS = re.compile(r"0|[1-9][0-9]*")


def _canonical(x) -> bool:
    return (isinstance(x, str) and _DIGITS.fullmatch(x) is not None
            and (len(x) < 19 or (len(x) == 19 and x <= schema.INT64_MAX)))


def cost_class(n, d) -> str:
    """R (on the 10^-15 grid), Q (another canonical rational) or X (anything else)."""
    if _canonical(n) and _canonical(d) and d != "0":
        return "R" if len(d) <= 16 and G % int(d) == 0 else "Q"
    return "X"


def _malformed(r) -> bool:
    at, expires = r["admitted_at"], r["expires_at"]
    return type(at) is not int or type(expires) is not int or at < 0 or expires > at + DAY


def _orphan(r) -> bool:
    return r["att_id"] is None or r["call_id"] is None or r["acc_id"] is None


class _Expected:
    def __init__(self):
        self.rows: dict[tuple, dict] = {}

    def add(self, key, **values):
        row = self.rows.setdefault(key, {"n_adm": 0, "n_res": 0, "n_cross": 0, "n_exc": 0, "bound": 0, "rbound": 0,
                                         "whole": 0, "frac": 0, "q": 0})
        for name, v in values.items():
            row[name] += v

    def canonical(self) -> dict[tuple, dict]:
        out = {}
        for key, e in self.rows.items():
            den = key[-1]
            if den:
                row = dict.fromkeys(schema.BUCKET_VALUES, 0)
                row["cost_hi"], row["cost_lo"] = divmod(e["q"], B)
            else:
                row = {"n_adm": e["n_adm"], "n_res": e["n_res"], "n_cross": e["n_cross"], "n_exc": e["n_exc"]}
                row["bound_hi"], row["bound_lo"] = divmod(e["bound"], B)
                row["rbound_hi"], row["rbound_lo"] = divmod(e["rbound"], B)
                row["cost_hi"], row["cost_lo"] = divmod(e["whole"], B)
                row["cost_carry"], row["cost_frac"] = divmod(e["frac"], G)
            out[key] = row
        return out


@dataclass
class ScopeReport:
    scope: tuple
    facts: int
    rebuild_seq: int
    differences: list = field(default_factory=list)
    count: int = 0
    open_faults: int = 0

    def differ(self, check: str, **detail):
        self.count += 1
        if len(self.differences) < 20:
            self.differences.append({"check": check, **detail})


@dataclass
class VerifyReport:
    mode: str
    now: int
    since: int | None
    environment: str | None = None
    scopes: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(not s.count for s in self.scopes.values())

    @property
    def mismatched(self) -> list[tuple]:
        return [scope for scope, s in self.scopes.items() if s.count]

    @property
    def facts(self) -> int:
        return sum(s.facts for s in self.scopes.values())

    def summary(self) -> dict:
        return {"mode": self.mode, "now": self.now, "since": self.since, "facts": self.facts,
                "mismatched": {":".join(scope): {"count": s.count, "first": s.differences[:5]}
                               for scope, s in self.scopes.items() if s.count}}


def _scopes(c, environment) -> list[tuple]:
    """Every scope with facts or derived rows (a loose index scan, never a table scan)."""
    found = set()
    for table, index in (("model_cost_admissions", "model_cost_admissions_scope"),
                         ("model_cost_buckets", None), ("model_cost_inflight", "model_cost_inflight_open"),
                         ("model_cost_hold", "model_cost_hold_open"),
                         ("model_cost_exceptions", "model_cost_exceptions_scope")):
        hint = f" INDEXED BY {index}" if index else ""
        last = ("", "", "") if environment is None else (environment, "", "")
        first = True
        while True:
            op = ">=" if first and environment is not None else ">"
            row = c.execute(f"SELECT environment,provider,account_ref FROM {table}{hint} WHERE "
                            f"(environment,provider,account_ref){op}(?,?,?) ORDER BY environment,provider,account_ref "
                            "LIMIT 1", last).fetchone()
            first = False
            if row is None or (environment is not None and row[0] != environment):
                break
            last = tuple(row)
            if last[0] != "":
                found.add(last)
    return sorted(found, key=lambda s: tuple(str(x) for x in s))


def _previous_month(now: int) -> int:
    date = _EPOCH + timedelta(seconds=now)
    first = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (first - timedelta(days=1)).replace(day=1)
    return int((previous - _EPOCH).total_seconds())


def _check_scope(c, scope, *, since, now, mode) -> ScopeReport:
    env, provider, account = scope
    newest = c.execute("SELECT admitted_at FROM model_cost_admissions INDEXED BY model_cost_admissions_scope WHERE "
                       + _SCOPE + " AND typeof(admitted_at)='integer' ORDER BY admitted_at DESC LIMIT 1",
                       scope).fetchone()
    M = newest[0] if newest else None
    H = M - HORIZON_S if M is not None else None
    at_now = max(now, M) if M is not None else now
    lo = None if since is None else since - since % DAY
    report = ScopeReport(scope, 0, rebuild_seq(c, scope))
    report.open_faults = c.execute("SELECT COUNT(*) FROM model_cost_rollup_faults WHERE " + _SCOPE
                                   + " AND resolved_at IS NULL", scope).fetchone()[0]
    floor = None if lo is None else (lo if H is None else min(lo, H))
    where = _SCOPE + ("" if floor is None else " AND admitted_at>=?")
    facts = c.execute(
        "SELECT a.attempt_key,a.environment,a.provider,a.account_ref,a.unit,a.reserved_units,a.admitted_at,a.expires_at,"
        "att.id AS att_id,att.call_key,att.requested_model,c.id AS call_id,c.status AS call_status,"
        "c.deadline AS call_deadline,acc.id AS acc_id,acc.owner,u.attempt_key AS usage_key,u.cost_n,u.cost_d,"
        "u.cost_unit,e.attempt_key AS ending_key FROM (SELECT * FROM model_cost_admissions "
        "INDEXED BY model_cost_admissions_scope WHERE " + where + ") a "
        "LEFT JOIN billing_attempts att ON att.id=a.attempt_key LEFT JOIN billing_calls c ON c.id=att.call_key "
        "LEFT JOIN billing_accounts acc ON acc.id=c.account_id LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
        "LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key",
        scope if floor is None else (*scope, floor)).fetchall()
    report.facts = len(facts)
    expected = _Expected()
    exceptions: dict[str, int] = {}
    by_key = {}
    fact_faults = []
    for r in facts:
        by_key[r["attempt_key"]] = r
        if _malformed(r) or _orphan(r):
            fact_faults.append(r["attempt_key"])
            continue
        at = r["admitted_at"]
        if r["expires_at"] <= at:                        # I1: admitted_at < expires_at <= admitted_at + 1 day
            report.differ("fact_i1", attempt_key=r["attempt_key"])
        resolved = r["usage_key"] is not None
        cls = cost_class(r["cost_n"], r["cost_d"]) if resolved else None
        adds = {"n_adm": 1, "bound": r["reserved_units"]}
        if resolved:
            adds.update(n_res=1, rbound=r["reserved_units"], n_cross=int(r["cost_unit"] != r["unit"]),
                        n_exc=int(cls == "X"))
            if cls == "R":
                n, d = int(r["cost_n"]), int(r["cost_d"])
                adds.update(whole=n // d, frac=(n % d) * (G // d))
        targets = []
        if lo is None or at >= lo:
            targets.append(("account", "", DAY))
            if resolved and cls == "X":
                exceptions[r["attempt_key"]] = at
        if H is not None:
            for dim, member in (("payer", r["owner"]), ("route", r["requested_model"])):
                for span in SPANS:
                    if at - at % span >= H:
                        targets.append((dim, member, span))
        for dim, member, span in targets:
            start = at - at % span
            expected.add((dim, member, span, start, r["unit"], 0), **adds)
            if cls == "Q":
                expected.add((dim, member, span, start, r["unit"], int(r["cost_d"])), q=int(r["cost_n"]))
    for key in fact_faults:
        report.differ("fact_fault", attempt_key=key)
        if c.execute("SELECT 1 FROM model_cost_rollup_faults WHERE id=?", ("fact:" + str(key),)).fetchone() is None:
            report.differ("fact_fault_missing", attempt_key=key)
    # Buckets: day rows from ``lo`` (all when None) and velocity rows from H, both directions.
    clauses, args = [], []
    clauses.append("(dim='account'" + ("" if lo is None else " AND start>=?") + ")")
    args += [] if lo is None else [lo]
    if H is not None:
        clauses.append("(dim<>'account' AND start>=?)")
        args.append(H)
    actual = {}
    for r in c.execute("SELECT dim,member,span,start,unit,den," + _VALUES + " FROM model_cost_buckets WHERE "
                       + _SCOPE + " AND (" + " OR ".join(clauses) + ")", (*scope, *args)).fetchall():
        key = (r["dim"], r["member"], r["span"], r["start"], r["unit"], r["den"])
        actual[key] = {c_: r[c_] for c_ in schema.BUCKET_VALUES}
        if not (type(r["den"]) is int and r["den"] >= 0 and _structure(r, main=r["den"] == 0)):
            report.differ("structure", key=list(key))
    canon = expected.canonical()
    for key in sorted(set(canon) | set(actual), key=lambda k: tuple(str(x) for x in k)):
        if canon.get(key) != actual.get(key):
            report.differ("bucket", key=list(key), expected=canon.get(key), found=actual.get(key))
    # Class-X exceptions, both directions.
    have = {r[0]: r[1] for r in c.execute(
        "SELECT attempt_key,admitted_at FROM model_cost_exceptions WHERE " + _SCOPE
        + ("" if lo is None else " AND admitted_at>=?"), scope if lo is None else (*scope, lo)).fetchall()}
    if have != exceptions:
        report.differ("exceptions", missing=sorted(set(exceptions) - set(have))[:5],
                      extra=sorted(set(have) - set(exceptions), key=str)[:5])

    def fact(key):
        if key in by_key:
            return by_key[key]
        row = c.execute(
            "SELECT a.attempt_key,a.environment,a.provider,a.account_ref,a.unit,a.reserved_units,a.admitted_at,"
            "a.expires_at,att.id AS att_id,att.call_key,att.requested_model,c.id AS call_id,c.status AS call_status,"
            "c.deadline AS call_deadline,acc.id AS acc_id,acc.owner,u.attempt_key AS usage_key,u.cost_n,u.cost_d,"
            "u.cost_unit,e.attempt_key AS ending_key FROM model_cost_admissions a "
            "LEFT JOIN billing_attempts att ON att.id=a.attempt_key LEFT JOIN billing_calls c ON c.id=att.call_key "
            "LEFT JOIN billing_accounts acc ON acc.id=c.account_id LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
            "LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key WHERE a.attempt_key=?", (key,)).fetchone()
        by_key[key] = row
        return row

    def valid(row):
        return row is not None and tuple(row[k] for k in ("environment", "provider", "account_ref")) == scope \
            and not _malformed(row) and not _orphan(row)

    # The open set, both directions: every row is justified by its facts; every open fact has its row.
    inflight = {r[0]: r[1] for r in c.execute("SELECT attempt_key,expires_at FROM model_cost_inflight WHERE "
                                              + _SCOPE, scope).fetchall()}
    holds = {r[0]: (r[1], r[2]) for r in c.execute("SELECT attempt_key,call_key,deadline FROM model_cost_hold WHERE "
                                                   + _SCOPE, scope).fetchall()}
    for key, expires in inflight.items():
        row = fact(key)
        if not valid(row) or row["usage_key"] is not None or row["expires_at"] != expires:
            report.differ("inflight_stale", attempt_key=key)
    for key, (call_key, deadline) in holds.items():
        row = fact(key)
        if (not valid(row) or row["usage_key"] is not None or row["ending_key"] is not None
                or row["call_status"] != "reserved" or row["call_deadline"] != deadline or row["call_key"] != call_key):
            report.differ("hold_stale", attempt_key=key)
    if M is not None:
        for r in facts:
            if not valid(r) or r["usage_key"] is not None:
                continue
            if r["expires_at"] > M and r["attempt_key"] not in inflight:
                report.differ("inflight_missing", attempt_key=r["attempt_key"])
            if (r["ending_key"] is None and r["call_status"] == "reserved" and r["call_deadline"] > M
                    and r["attempt_key"] not in holds):
                report.differ("hold_missing", attempt_key=r["attempt_key"])
    # The read path against the uncapped oracle (not in the bounded hourly run).
    if mode != "recent" and M is not None and not report.open_faults and not fact_faults:
        newest_unit = c.execute("SELECT unit FROM model_cost_admissions INDEXED BY model_cost_admissions_scope WHERE "
                                + _SCOPE + " AND admitted_at=?", (*scope, M)).fetchone()[0]
        scale = c.execute("SELECT scale FROM billing_units WHERE code=?", (newest_unit,)).fetchone()
        if scale is not None:
            unit = Unit(newest_unit, scale[0])
            outcome = {}
            for name, fn in (("rollup", counters), ("reference", reference_counters)):
                try:
                    kwargs = {"records": True} if name == "rollup" else {}
                    got = fn(c, environment=env, provider=provider, account_ref=account, unit=unit, now=at_now, **kwargs)
                    got.pop("rows_read", None)
                    outcome[name] = got
                except BillingError as exc:
                    outcome[name] = {"refused": exc.code, "check": getattr(exc, "check", None)}
            mine, theirs = outcome["rollup"].get("refused"), outcome["reference"].get("refused")
            if mine == "cost_accounting_capacity":
                differs = False         # over the open-set or class-X cap the read path refuses by design
            elif mine == "cost_rollup_inconsistent":
                differs = True
            elif mine is not None or theirs is not None:
                # Both refuse (several refusals may pick different codes, by the documented order),
                # or only one does: the latter is a difference.
                differs = (mine is None) != (theirs is None)
            else:
                differs = outcome["rollup"] != outcome["reference"]
            if differs:
                report.differ("counters", now=at_now, rollup=_jsonable(outcome["rollup"]),
                              reference=_jsonable(outcome["reference"]))
    return report


def _jsonable(value):
    if isinstance(value, Fraction):
        return [value.numerator, value.denominator]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _check(c, *, environment, since, now, mode, scopes=None) -> VerifyReport:
    report = VerifyReport(mode, now, since, environment)
    global_issues = []
    if c.execute("SELECT 1 FROM model_cost_rollup_deltas LIMIT 1").fetchone():
        global_issues.append("deltas_not_empty")
    if not schema.sentinels_present(c):
        global_issues.append("sentinel_missing")
    for scope in scopes if scopes is not None else _scopes(c, environment):
        result = _check_scope(c, scope, since=since, now=now, mode=mode)
        for issue in global_issues:
            result.differ(issue)
        report.scopes[scope] = result
    return report


class CostRollup:
    """Operations on the rollup. Every method takes the Store; none runs on a fact write path."""

    @staticmethod
    def check(store, environment=None, since=None, now=None, *, all=False, mode="verify") -> VerifyReport:
        """Recompute from the facts and compare, in ONE deferred read transaction on its own
        connection (a WAL snapshot): never the writer lock. ``since`` defaults to the start of the
        previous month; ``all=True`` (or since=0) compares every day."""
        now = int(time.time()) if now is None else integer(now)
        if all:
            since = None
        elif since is None:
            since = _previous_month(now)
        elif since == 0:
            since = None
        c = store._connect()
        try:
            c.execute("BEGIN")
            try:
                return _check(c, environment=environment, since=since, now=now, mode=mode)
            finally:
                c.execute("ROLLBACK")
        finally:
            c.close()

    @staticmethod
    def record(store, report: VerifyReport, *, clock=None) -> dict:
        """Record a check: per mismatched scope a fault (unless a rebuild newer than the snapshot
        exists: 'superseded'), the audit and an alert; plus one run row. Returns {scope: status}
        for the scopes with differences."""
        from .risk import _system_audit
        at = int((clock or time.time)())
        statuses, alerts = {}, []
        with store.tx() as t:
            for scope, result in report.scopes.items():
                if not result.count:
                    continue
                current = rebuild_seq(t, scope)
                status = "superseded" if current > result.rebuild_seq else "mismatch"
                statuses[scope] = status
                detail = json.dumps({"count": result.count, "first": result.differences[:5]}, sort_keys=True,
                                    default=str)[:4000]
                if status == "mismatch":
                    added = t.execute(
                        "INSERT INTO model_cost_rollup_faults(id,environment,provider,account_ref,code,detail,detected_at) "
                        "VALUES (?,?,?,?,'verify_mismatch',?,?) ON CONFLICT(id) DO NOTHING",
                        ("verify:" + ":".join(scope) + ":" + str(result.rebuild_seq), *scope, detail, at)).rowcount
                    if added:
                        _system_audit(t, "billing.cost.rollup_inconsistent", "model_cost_rollup", ":".join(scope),
                                      {"billing_environment": scope[0], "provider": scope[1], "account_ref": scope[2],
                                       "source": report.mode, "differences": result.count})
                        alerts.append(scope)
                t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,"
                          "facts,differences,rebuild_seq,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (_id("cra"), *scope, report.mode, status, report.since or 0, result.facts, detail,
                           result.rebuild_seq, at))
            status = ("ok" if not statuses else "mismatch" if "mismatch" in statuses.values() else "superseded")
            environments = {s[0] for s in report.scopes} | ({report.environment} if report.environment else set())
            for environment in sorted(environments or {""}):
                t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,"
                          "facts,differences,rebuild_seq,created_at) VALUES (?,?,'','',?,?,?,?,?,?,?)",
                          (_id("cra"), environment, report.mode, status, report.since or 0,
                           sum(s.facts for k, s in report.scopes.items() if k[0] == environment),
                           json.dumps({"scopes": len(report.scopes), "mismatched": len(statuses)}),
                           max([s.rebuild_seq for s in report.scopes.values()] or [0]), at))
            for scope in alerts:
                store.after_commit(lambda scope=scope: _alert(store, scope, at))
        return statuses

    @staticmethod
    def verify(store, environment=None, since=None, now=None, *, all=False, mode="verify", clock=None) -> VerifyReport:
        report = CostRollup.check(store, environment, since, now, all=all, mode=mode)
        CostRollup.record(store, report, clock=clock)
        return report

    @staticmethod
    def rebuild(store, *, environment: str, provider: str, account_ref: str, actor: str, reason: str,
                clock=None) -> dict:
        """Replay one scope from its facts, verify it and resolve its faults, in ONE BEGIN IMMEDIATE.

        Never automatic (platform.py cost-rollup rebuild). Refuses, changing nothing, when a fact is
        malformed or orphaned (repair it from a backup first) or when the result does not verify."""
        from .risk import _reason, _system_audit
        scope = (identifier(environment), identifier(provider), identifier(account_ref))
        identifier(actor)
        reason = _reason(reason)
        at = int((clock or time.time)())
        rebuild_id = _id("crb")
        with store.tx() as t:
            replayed = schema.replay(t, scope)
            if replayed["faults"]:
                raise RebuildRefused(attempt_keys=replayed["faults"])
            resolved = t.execute("UPDATE model_cost_rollup_faults SET resolved_at=?,resolved_by=?,rebuild_id=? WHERE "
                                 + _SCOPE + " AND resolved_at IS NULL", (at, actor, rebuild_id, *scope)).rowcount
            report = _check(t, environment=environment, since=None, now=at, mode="rebuild", scopes=[scope])
            if not report.ok:
                raise RebuildRefused(differences=report.scopes[scope].differences)
            t.execute("INSERT INTO model_cost_rollup_rebuilds(id,environment,provider,account_ref,actor,reason,"
                      "finished_at) VALUES (?,?,?,?,?,?,?)", (rebuild_id, *scope, actor, reason, at))
            seq = t.execute("SELECT seq FROM model_cost_rollup_rebuilds WHERE id=?", (rebuild_id,)).fetchone()[0]
            t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,facts,"
                      "differences,rebuild_seq,created_at) VALUES (?,?,?,?,'rebuild','ok',0,?,'{}',?,?)",
                      (_id("cra"), *scope, replayed["facts"], seq, at))
            _system_audit(t, "billing.cost.rollup_rebuilt", "model_cost_rollup", ":".join(scope),
                          {"billing_environment": environment, "provider": provider, "account_ref": account_ref,
                           "actor": actor, "reason": reason, "facts": replayed["facts"], "resolved_faults": resolved,
                           "rebuild_seq": seq})
        return {"id": rebuild_id, "seq": seq, "scope": list(scope), "facts": replayed["facts"],
                "resolved_faults": resolved}

    @staticmethod
    def prune(store, *, environment: str | None = None, batch: int = 5_000) -> dict:
        """Delete what can never be read again, per scope, in batches under the gate: velocity
        buckets older than (newest admission - 2 days), inflight rows expired by the newest
        admission, hold rows past their deadline by it. Day buckets are never pruned."""
        integer(batch, positive=True, maximum=50_000)
        counts = {"buckets": 0, "inflight": 0, "hold": 0}
        with store.conn() as c:
            scopes = _scopes(c, environment)
        for scope in scopes:
            newest = store.one("SELECT admitted_at AS m FROM model_cost_admissions INDEXED BY model_cost_admissions_scope "
                               "WHERE " + _SCOPE + " AND typeof(admitted_at)='integer' ORDER BY admitted_at DESC LIMIT 1",
                               scope)
            if newest is None:
                continue
            M = newest["m"]
            while True:
                with store.tx() as t:
                    t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('gate')")
                    n_b = t.execute(
                        "DELETE FROM model_cost_buckets WHERE (environment,provider,account_ref,dim,member,span,start,unit,den) "
                        "IN (SELECT environment,provider,account_ref,dim,member,span,start,unit,den FROM model_cost_buckets "
                        "WHERE " + _SCOPE + " AND dim IN ('payer','route') AND start<? LIMIT ?)",
                        (*scope, M - HORIZON_S, batch)).rowcount
                    n_i = t.execute("DELETE FROM model_cost_inflight WHERE attempt_key IN (SELECT attempt_key FROM "
                                    "model_cost_inflight INDEXED BY model_cost_inflight_open WHERE " + _SCOPE
                                    + " AND expires_at<=? LIMIT ?)", (*scope, M, batch)).rowcount
                    n_h = t.execute("DELETE FROM model_cost_hold WHERE attempt_key IN (SELECT attempt_key FROM "
                                    "model_cost_hold INDEXED BY model_cost_hold_open WHERE " + _SCOPE
                                    + " AND deadline<=? LIMIT ?)", (*scope, M, batch)).rowcount
                    t.execute("DELETE FROM model_cost_rollup_deltas")
                counts["buckets"] += n_b
                counts["inflight"] += n_i
                counts["hold"] += n_h
                if max(n_b, n_i, n_h) < batch:
                    break
        return counts

    @staticmethod
    def latch(store, exc, *, clock=None) -> str | None:
        """After a refusing dispatch rolled back: record a read-path inconsistency as a fault of its
        scope, unless a rebuild newer than the one the read saw has happened since."""
        if not isinstance(exc, RollupInconsistent):
            return None
        from .risk import _system_audit
        at = int((clock or time.time)())
        scope = exc.scope
        with store.tx() as t:
            if rebuild_seq(t, scope) > exc.rebuild_seq:
                return "superseded"
            added = t.execute(
                "INSERT INTO model_cost_rollup_faults(id,environment,provider,account_ref,code,detail,detected_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (f"read:{exc.check}:" + ":".join(scope) + f":{exc.rebuild_seq}", *scope, "read_" + exc.check,
                 exc.check, at)).rowcount
            if added:
                _system_audit(t, "billing.cost.rollup_inconsistent", "model_cost_rollup", ":".join(scope),
                              {"billing_environment": scope[0], "provider": scope[1], "account_ref": scope[2],
                               "source": "read", "check": exc.check})
                store.after_commit(lambda: _alert(store, scope, at))
        return "latched" if added else "already_latched"

    @staticmethod
    def state(t, scope) -> dict:
        """For the internal snapshot: open faults and the last rebuild of ``scope``."""
        faults = [dict(r) for r in t.execute(
            "SELECT id,code,detected_at FROM model_cost_rollup_faults INDEXED BY model_cost_rollup_faults_open WHERE " + _SCOPE
            + " AND resolved_at IS NULL ORDER BY detected_at,id LIMIT 20", tuple(scope)).fetchall()]
        last = _last_rebuild(t, scope)
        return {"open_faults": faults, "last_rebuild": dict(last) if last else None}


def _id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(10)}"


def _alert(store, scope, at) -> None:
    from .risk import notify_admins
    day = time.strftime("%Y-%m-%d", time.gmtime(at))
    notify_admins(store, "billing.cost.rollup_inconsistent", params={"provider": scope[1], "account": scope[2]},
                  dedupe_key=f"cost:rollup:{':'.join(scope)}:{day}", object_id=":".join(scope))


def assert_consistent(store, **kwargs) -> VerifyReport:
    """Test helper (autouse teardowns): the whole rollup verifies against the facts."""
    report = CostRollup.check(store, all=True, **kwargs)
    if not report.ok:
        raise AssertionError(json.dumps(report.summary(), default=str)[:4000])
    return report


def main(argv=None) -> int:
    """``python -m synwevo_core.billing.cost_rollup verify|rebuild --data DIR`` (platform.py cost-rollup)."""
    import argparse
    import getpass
    import sys

    from ..store import Store

    ap = argparse.ArgumentParser(prog="cost-rollup", description=main.__doc__)
    ap.add_argument("action", choices=["verify", "rebuild"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--environment", choices=["test", "live"])
    ap.add_argument("--provider")
    ap.add_argument("--account-ref")
    ap.add_argument("--reason")
    args = ap.parse_args(argv)
    store = Store(args.data)
    if args.action == "verify":
        if args.no_record:
            report = CostRollup.check(store, args.environment, all=args.all, mode="cli")
        else:
            report = CostRollup.verify(store, args.environment, all=args.all, mode="cli")
        print(json.dumps(report.summary(), ensure_ascii=False, indent=2, default=str))
        print(f"{len(report.scopes)} 个提供方账户，{report.facts} 条成本事实："
              + ("一致。" if report.ok else f"{len(report.mismatched)} 个不一致"
                 + ("（未记录）。" if args.no_record else "，已锁定；核实后用 cost-rollup rebuild 重建。")),
              file=sys.stderr)
        return 0 if report.ok else 1
    missing = [flag for flag, value in (("--environment", args.environment), ("--provider", args.provider),
                                        ("--account-ref", args.account_ref), ("--reason", args.reason)) if not value]
    if missing:
        print("重建需要：" + " ".join(missing), file=sys.stderr)
        return 2
    try:
        result = CostRollup.rebuild(store, environment=args.environment, provider=args.provider,
                                    account_ref=args.account_ref, actor="operator:" + getpass.getuser(),
                                    reason=args.reason)
    except RebuildRefused as exc:
        if exc.attempt_keys:
            print("没有重建：这些成本事实本身有问题（时间无效或关联缺失），先从备份修复：", file=sys.stderr)
            for key in exc.attempt_keys:
                print("  " + str(key), file=sys.stderr)
        else:
            print("没有重建：重放后仍与事实不一致：", file=sys.stderr)
            print(json.dumps(exc.differences, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a refusal (reason, identifiers) is printed, not a traceback
        print(f"没有重建：{getattr(exc, 'code', None) or type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
