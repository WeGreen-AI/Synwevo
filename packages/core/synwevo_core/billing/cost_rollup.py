"""Readers, verifier, rebuild, prune and latch of the exact cost rollup.

The rollup itself (tables, gate, triggers, replay) is ``synwevo_core.suite_schema.model_cost_rollup``;
docs/billing-ledger.md ("Cost rollup") describes both. This module:

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
* ``CostRollup`` — ``check`` (independent recomputation from the facts in one read snapshot,
  streamed: its memory does not grow with the facts it reads; one scope's error is that scope's
  difference, never the end of the run), ``record`` (per-scope faults/audits under BEGIN IMMEDIATE;
  superseded by a newer rebuild), ``verify`` (both), ``rebuild`` (audited, never automatic; it
  repairs derived rows and refuses, changing nothing, while any fact of the scope is broken),
  ``prune`` and ``latch``.

Broken facts fail closed. Only a write outside the product (an import with foreign keys off, hand
SQL) can leave a malformed or orphan admission, or a value Python cannot decode (a non-UTF-8 TEXT):
its scope refuses with ``cost_rollup_inconsistent`` and no rebuild clears it, since facts cannot be
edited; the database is restored from a backup taken before that write (docs/platform/model-risk.md
§13). Attempt keys are read as SQLite type and bytes wherever Python handles them (``key_label``,
``fact_fault_id``), so every broken one can be named.

Refusals: ``cost_rollup_inconsistent`` (an open fault, or a failed structure/stale/parity/absorbed
check, or a value the read cannot decode: ``RollupInconsistent``), ``cost_accounting_capacity`` (more than 10,000 open rows or
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
import sqlite3
import time

from ..suite_schema import model_cost_rollup as schema
from .models import BillingError, Unit, identifier, integer

log = logging.getLogger("synwevo.billing.cost_rollup")

B, G, DAY, HORIZON_S, SPANS = schema.B, schema.G, schema.DAY, schema.HORIZON_S, schema.SPANS
key_label, fact_fault_id = schema.key_label, schema.fact_fault_id
#: Open set and class-X caps: the only remaining row limits on the admission path.
MAX_OPEN = 10_000
#: Limb base for summing up to MAX_OPEN+1 reserved int64 bounds in SQL without overflow.
_LB = 10**14
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


#: Verifier differences that name a broken fact (``attempt_key``), which no rebuild repairs.
FACT_CHECKS = ("fact_fault", "undecodable_key")
#: The store-wide issue for rows in a scope no read can name (a part that is a BLOB, a NULL or a
#: non-UTF-8 TEXT): reported, audited and alerted, never read (``_scopes``).
UNNAMED_SCOPE = "undecodable_scope"


class RollupInconsistent(BillingError):
    """A read-path detection (structure, stale open row, parity, absorbed, or ``undecodable``: a value
    the read cannot decode). Like BrakeTripped it is latched by ``CostAdmission.after_refusal`` once
    the refusing transaction has rolled back, unless a rebuild newer than ``rebuild_seq`` has happened
    since."""

    def __init__(self, scope: tuple, check: str, rebuild_seq: int):
        super().__init__("cost_rollup_inconsistent")
        self.scope, self.check, self.rebuild_seq = tuple(scope), check, rebuild_seq


class RebuildRefused(BillingError):
    """A rebuild that would not leave a consistent rollup; nothing was changed. ``attempt_keys`` names
    the broken facts (``key_label``), which no rebuild repairs; ``differences`` what else disagrees."""

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


def scope_key(scope) -> str:
    """An unambiguous text for a scope (fault ids, alert dedupe keys, summaries): its parts as a JSON
    array, so ('test','a:b','c') and ('test','a','b:c') never share one (identifier() allows ':')."""
    return json.dumps([str(part) for part in scope], separators=(",", ":"))


_NEWEST = ("SELECT a.admitted_at AS m,a.unit FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope WHERE "
           "a.environment=? AND a.provider=? AND a.account_ref=? AND typeof(a.admitted_at)='integer' "
           "ORDER BY a.admitted_at DESC LIMIT 1")


def newest(t, scope):
    """The scope's newest admission M (its admitted_at and unit), or None: the time the velocity
    horizon (schema._HORIZON), prune and the verifier count from. Facts are insert-only, so M never
    decreases: a value read earlier is at most the current one."""
    return t.execute(_NEWEST, tuple(scope)).fetchone()


def _undecodable(exc: BaseException) -> bool:
    """Python's sqlite3 could not decode a TEXT value as UTF-8 (SQLite stores TEXT unchecked: only a
    write outside the product leaves such a value). Raised by the module, not by SQLite: no error code."""
    return (isinstance(exc, sqlite3.OperationalError) and getattr(exc, "sqlite_errorcode", None) is None
            and str(exc).startswith("Could not decode"))


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
    plus pending ones in the Monday-the-1st tail). ``rows_read`` counts rows fetched. A value the
    read cannot decode refuses as ``RollupInconsistent`` (check ``undecodable``), latched after the
    refusal like the other read-path checks."""
    try:
        return _counters(t, environment, provider, account_ref, unit, now, records)
    except sqlite3.OperationalError as exc:
        if not _undecodable(exc):
            raise
        scope = (environment, provider, account_ref)
        raise RollupInconsistent(scope, "undecodable", rebuild_seq(t, scope)) from None


def _counters(t, environment, provider, account_ref, unit, now, records) -> dict:
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
    xs = t.execute("SELECT i.admitted_at,a.unit,u.attempt_key IS NOT NULL AS has_usage,u.cost_n,u.cost_d,u.cost_unit "
                   "FROM model_cost_exceptions i INDEXED BY model_cost_exceptions_scope "
                   "LEFT JOIN model_cost_admissions a ON a.attempt_key=i.attempt_key "
                   "LEFT JOIN billing_usage u ON u.attempt_key=i.attempt_key "
                   "WHERE i.environment=? AND i.provider=? AND i.account_ref=? AND i.admitted_at>=? LIMIT ?",
                   (*scope, E, MAX_EXCEPTIONS + 1)).fetchall()
    read += len(xs)
    if len(xs) > MAX_EXCEPTIONS:
        raise BillingError("cost_accounting_capacity")
    # A time that is not an integer (only a write past the gate: a TEXT or BLOB sorts above every integer,
    # so the windows still select it) is refused like a malformed day row, never compared in Python.
    if len(xs) != exceptions_expected or any(not r["has_usage"] or r["unit"] is None
                                             or type(r["admitted_at"]) is not int for r in xs):
        raise inconsistent("exceptions")
    for r in xs:
        if r["unit"] != P or r["cost_unit"] != P:
            raise BillingError("cost_unit_mismatch")
        cost = _admission_cost(r["cost_n"], r["cost_d"])
        for name, start in win.items():
            if r["admitted_at"] >= start:
                totals[name] += cost
    # 6. The open set: still in flight, or a reserved hold without a recorded local stop. Joined to
    # the admission, the call (status, deadline; through the hold row's call key), the ending and the
    # usage, and classified with the earlier predicate verbatim (a row selected only as in flight is
    # open whatever its call), aggregated in SQL; reserved bounds are summed in base-10^14 limbs, so
    # a sum of 10,001 int64 values cannot overflow. Stale: usage, a missing or foreign admission, an
    # in-flight row whose expiry differs, or a selected hold row with an ending, a call no longer
    # reserved or another deadline.
    row = t.execute(
        "WITH o AS MATERIALIZED (SELECT attempt_key,MAX(fe) AS fe,MAX(hd) AS hd,MAX(hc) AS hc,MAX(in_f) AS in_f,"
        "MAX(in_h) AS in_h FROM (SELECT attempt_key,expires_at AS fe,NULL AS hd,NULL AS hc,1 AS in_f,0 AS in_h "
        "FROM model_cost_inflight INDEXED BY model_cost_inflight_open WHERE " + _SCOPE + " AND expires_at>? "
        "UNION ALL SELECT attempt_key,NULL,deadline,call_key,0,1 FROM model_cost_hold INDEXED BY model_cost_hold_open "
        "WHERE " + _SCOPE + " AND deadline>?) GROUP BY attempt_key LIMIT ?), "
        "x AS MATERIALIZED (SELECT o.*,a.attempt_key AS ak,a.environment AS ae,a.provider AS ap,a.account_ref AS aa,"
        "a.unit AS au,a.reserved_units AS r,a.admitted_at AS at,a.expires_at AS ex,c.status AS cs,c.deadline AS cd,"
        "e.attempt_key AS ek,u.attempt_key AS uk FROM o "
        "LEFT JOIN model_cost_admissions a ON a.attempt_key=o.attempt_key "
        "LEFT JOIN billing_calls c ON c.id=o.hc "
        "LEFT JOIN model_cost_endings e ON e.attempt_key=o.attempt_key "
        "LEFT JOIN billing_usage u ON u.attempt_key=o.attempt_key), "
        "y AS MATERIALIZED (SELECT *,(at>=? AND (ex>? OR (cs='reserved' AND cd>? AND ek IS NULL))) AS op FROM x) "
        "SELECT COUNT(*) AS n,"
        "SUM(CASE WHEN ak IS NULL OR uk IS NOT NULL OR ae IS NOT ? OR ap IS NOT ? OR aa IS NOT ?"
        " OR (in_f AND fe IS NOT ex) OR (in_h AND (ek IS NOT NULL OR cs IS NOT 'reserved' OR hd IS NOT cd))"
        " THEN 1 ELSE 0 END) AS stale,"
        "SUM(at>=? AND au IS NOT ?) AS foreign_unit, SUM(at>=? AND ex>?) AS inflight, SUM(op) AS unknown_count,"
        f"SUM(CASE WHEN op THEN r/{_LB} ELSE 0 END) AS unknown_hi, SUM(CASE WHEN op THEN r%{_LB} ELSE 0 END) AS unknown_lo,"
        f"SUM(op AND at>=?) AS open_count, SUM(CASE WHEN op AND at>=? THEN r/{_LB} ELSE 0 END) AS open_hi,"
        f"SUM(CASE WHEN op AND at>=? THEN r%{_LB} ELSE 0 END) AS open_lo FROM y",
        (*scope, now, *scope, now, MAX_OPEN + 1, L, now, now, *scope, L, P, L, now, E, E, E)).fetchone()
    read += row["n"]
    if row["n"] > MAX_OPEN:
        raise BillingError("cost_accounting_capacity")
    if row["stale"]:
        raise inconsistent("stale")
    if row["foreign_unit"]:
        raise BillingError("cost_unit_mismatch")
    unknown = (row["unknown_hi"] or 0) * _LB + (row["unknown_lo"] or 0)
    unknown_count, inflight = row["unknown_count"] or 0, row["inflight"] or 0
    open_units = (row["open_hi"] or 0) * _LB + (row["open_lo"] or 0)
    open_count = row["open_count"] or 0
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
                                     "WHERE " + _SCOPE + " AND admitted_at>=? AND admitted_at<?",
                                     (*scope, L, E)).fetchone()[0]
            read += 1
        out["records"] = admitted + tail_pending
    out["rows_read"] = read
    return out


# ---------------------------------------------------------------- RiskControls.guard velocity
#: ``_structure`` per row, in SQL: integer types, limbs in range, counts ordered, rbound <= bound,
#: class-Q rows (den > 0) hold cost only. A velocity read sums rows, so it checks each one here.
_ROW_OK = ("typeof(den)='integer' AND den>=0 AND " + " AND ".join(f"typeof({c})='integer'" for c in schema.BUCKET_VALUES)
           + f" AND bound_lo BETWEEN 0 AND {B - 1} AND rbound_lo BETWEEN 0 AND {B - 1} AND cost_lo BETWEEN 0 AND {B - 1}"
           f" AND cost_frac BETWEEN 0 AND {G - 1} AND bound_hi>=0 AND rbound_hi>=0 AND cost_hi>=0 AND cost_carry>=0"
           " AND n_exc>=0 AND n_exc<=n_res AND n_res<=n_adm AND n_cross>=0 AND n_cross<=n_res"
           " AND (rbound_hi<bound_hi OR (rbound_hi=bound_hi AND rbound_lo<=bound_lo))"
           " AND (den=0 OR (" + " AND ".join(f"{c}=0" for c in schema.BUCKET_VALUES if c not in ("cost_hi", "cost_lo"))
           + "))")


def _bucket_sums(t, scope, dim, member, now, w):
    """Disjoint, exact: span 1 over [a,a1), span 60 over [a1,a2), span 3600 from a2 (<= 143 rows)."""
    a = now - w + 1
    a1 = -(-a // 60) * 60
    a2 = -(-a // 3600) * 3600
    part = "SELECT * FROM model_cost_buckets WHERE " + _SCOPE + " AND dim=? AND member=? AND span=? AND start>=?"
    return t.execute(
        "SELECT unit,den,COUNT(*) AS n," + _SUMS + ",SUM(CASE WHEN " + _ROW_OK + " THEN 0 ELSE 1 END) AS broken FROM ("
        + part + " AND start<? UNION ALL " + part + " AND start<? UNION ALL " + part + ") GROUP BY unit,den",
        (*scope, dim, member, 1, a, a1, *scope, dim, member, 60, a1, a2, *scope, dim, member, 3600, a2)).fetchall()


def velocity(t, *, environment: str, provider: str, account_ref: str, unit: Unit, owner: str, route: str,
             now: int, risk) -> tuple[Fraction, Fraction]:
    """(payer total, route total) of the velocity brake, from buckets plus class-X facts.

    A total is computed only for a window whose cost threshold is set (the other is 0). Runs only
    after ``counters`` (I5: no admission after ``now``). A value it cannot decode refuses as
    ``RollupInconsistent`` (check ``undecodable``), as in ``counters``."""
    try:
        return _velocity(t, environment, provider, account_ref, unit, owner, route, now, risk)
    except sqlite3.OperationalError as exc:
        if not _undecodable(exc):
            raise
        scope = (environment, provider, account_ref)
        raise RollupInconsistent(scope, "undecodable", rebuild_seq(t, scope)) from None


def _velocity(t, environment, provider, account_ref, unit, owner, route, now, risk) -> tuple[Fraction, Fraction]:
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
        "SELECT a.unit,a.admitted_at,a.reserved_units,u.attempt_key IS NOT NULL AS has_usage,u.cost_n,u.cost_d,"
        "u.cost_unit,att.requested_model AS route,acc.owner AS owner FROM model_cost_exceptions i INDEXED BY model_cost_exceptions_scope "
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
        if r["unit"] != P or (r["has_usage"] and r["cost_unit"] != P):
            raise BillingError("cost_unit_mismatch")
        parsed.append((r, _risk_cost(r["cost_n"], r["cost_d"]) if r["has_usage"]
                       else Fraction(integer(r["reserved_units"]))))
    # 3. Each total: its own window's buckets plus its own class-X facts.
    out = {}
    for dim, (member, w, cost) in sides.items():
        if cost is None:
            out[dim] = Fraction(0)
            continue
        groups = sums[(dim, w)] if (dim, w) in sums else _bucket_sums(t, scope, dim, member, now, w)
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
        "c.deadline AS call_deadline,e.ending,u.attempt_key IS NOT NULL AS has_usage,u.cost_n,u.cost_d,u.cost_unit"
        " FROM (" + candidates + ") selected JOIN model_cost_admissions a ON a.attempt_key=selected.attempt_key"
        " JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key"
        " LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key"
        " LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key", args)        # streamed, never materialized
    count = t.execute("SELECT COUNT(*) FROM (" + candidates + ")", args).fetchone()[0]
    totals = {name: Fraction(0) for name in win}
    unknown = unknown_count = inflight = absorbed = absorbed_count = 0
    for r in rows:
        if r["unit"] != unit.code:
            raise BillingError("cost_unit_mismatch")
        if r["admitted_at"] > now:
            raise BillingError("cost_clock_regressed")
        if not r["has_usage"]:
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
        "SELECT a.reserved_units,a.admitted_at,a.unit,u.attempt_key IS NOT NULL AS has_usage,u.cost_n,u.cost_d,"
        "u.cost_unit,att.requested_model AS route,acc.owner AS owner "
        "FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope "
        "JOIN billing_attempts att ON att.id=a.attempt_key JOIN billing_calls c ON c.id=att.call_key "
        "JOIN billing_accounts acc ON acc.id=c.account_id "
        "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
        "WHERE a.environment=? AND a.provider=? AND a.account_ref=? AND a.admitted_at>? "
        "AND (acc.owner=? OR att.requested_model=?)",
        (environment, provider, account_ref, now - max(windows_set), owner, route))
    payer_total = route_total = Fraction(0)
    for r in rows:
        if r["unit"] != unit.code or (r["has_usage"] and r["cost_unit"] != unit.code):
            raise BillingError("cost_unit_mismatch")
        cost = Fraction(integer(r["reserved_units"])) if not r["has_usage"] else _risk_cost(r["cost_n"], r["cost_d"])
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
    return not (r["has_att"] and r["has_call"] and r["has_acc"])


def _key(r, prefix="key"):
    """An attempt key as the verifier compares it: (SQLite type, bytes), never decoded."""
    return r[prefix + "_t"], r[prefix + "_b"] or b""


def _label(key) -> str:
    return key_label(*key)


def _decodes(key) -> bool:
    if key[0] != "text":
        return True
    try:
        key[1].decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


class _Expected:
    """The expected buckets of one scope, built from facts streamed in admitted_at order and flushed
    to a temporary table as soon as each bucket is complete: at most one open bucket per member and
    span stays in Python, whatever the number of facts (the risk monitor runs this in the portal)."""

    TABLE = "model_cost_rollup_expected"
    _SPANS = SPANS + (DAY,)

    def __init__(self, c):
        self.c = c
        self.open = {span: [None, {}] for span in self._SPANS}       # span -> [start, {key: sums}]
        self.ready = []                                               # complete rows not yet inserted
        c.execute(f"DROP TABLE IF EXISTS temp.{self.TABLE}")
        # The bucket table's affinities, so a value reaches both tables coerced the same way.
        c.execute(f"CREATE TABLE temp.{self.TABLE}(dim TEXT,member TEXT,span INTEGER,start INTEGER,unit TEXT,den INTEGER,"
                  + ",".join(f"{v} INTEGER" for v in schema.BUCKET_VALUES) + ")")
        c.execute(f"CREATE INDEX temp.{self.TABLE}_key ON {self.TABLE}(dim,member,span,start,unit,den)")

    def add(self, key, **values):
        span, start = key[2], key[3]
        slot = self.open[span]
        if slot[0] != start:
            if slot[0] is not None and start < slot[0]:
                raise RuntimeError("cost rollup verifier: facts out of admitted_at order")
            self._flush(slot)
            slot[0] = start
        row = slot[1].get(key)
        if row is None:
            row = slot[1][key] = {"n_adm": 0, "n_res": 0, "n_cross": 0, "n_exc": 0, "bound": 0, "rbound": 0,
                                  "whole": 0, "frac": 0, "q": 0}
        for name, v in values.items():
            row[name] += v

    def finish(self):
        for slot in self.open.values():
            self._flush(slot)
        self._write()

    def drop(self):
        self.c.execute(f"DROP TABLE IF EXISTS temp.{self.TABLE}")

    def _flush(self, slot):
        if slot[1]:
            for key, e in slot[1].items():
                row = self.canonical(key, e)
                self.ready.append(key + tuple(row[v] for v in schema.BUCKET_VALUES))
            slot[1] = {}
            if len(self.ready) >= 5_000:
                self._write()

    def _write(self):
        if self.ready:
            self.c.executemany(f"INSERT INTO temp.{self.TABLE} VALUES ({','.join('?' * (6 + len(schema.BUCKET_VALUES)))})",
                               self.ready)
            self.ready = []

    @staticmethod
    def canonical(key, e) -> dict:
        if key[-1]:
            row = dict.fromkeys(schema.BUCKET_VALUES, 0)
            row["cost_hi"], row["cost_lo"] = divmod(e["q"], B)
            return row
        row = {"n_adm": e["n_adm"], "n_res": e["n_res"], "n_cross": e["n_cross"], "n_exc": e["n_exc"]}
        row["bound_hi"], row["bound_lo"] = divmod(e["bound"], B)
        row["rbound_hi"], row["rbound_lo"] = divmod(e["rbound"], B)
        row["cost_hi"], row["cost_lo"] = divmod(e["whole"], B)
        row["cost_carry"], row["cost_frac"] = divmod(e["frac"], G)
        return row


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
    #: Store-wide problems (a delta row at rest, a missing sentinel): reported even with no scope.
    issues: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and all(not s.count for s in self.scopes.values())

    @property
    def mismatched(self) -> list[tuple]:
        return [scope for scope, s in self.scopes.items() if s.count]

    @property
    def facts(self) -> int:
        return sum(s.facts for s in self.scopes.values())

    def summary(self) -> dict:
        return {"mode": self.mode, "now": self.now, "since": self.since, "facts": self.facts, "issues": list(self.issues),
                "mismatched": {scope_key(scope): {"count": s.count, "first": s.differences[:5]}
                               for scope, s in self.scopes.items() if s.count}}


_SCOPE_COLUMNS = ("environment", "provider", "account_ref")
#: The tables whose rows name a scope, each with the index that leads with the scope columns and whether
#: those columns can hold a NULL (the facts' are NOT NULL; the buckets' are a WITHOUT ROWID primary key).
_SCOPE_SOURCES = (("model_cost_admissions INDEXED BY model_cost_admissions_scope", False),
                  ("model_cost_buckets", False),
                  ("model_cost_inflight INDEXED BY model_cost_inflight_open", True),
                  ("model_cost_hold INDEXED BY model_cost_hold_open", True),
                  ("model_cost_exceptions INDEXED BY model_cost_exceptions_scope", True))


def _name(value) -> str | None:
    """A scope part read as (SQLite type, bytes) as the str every read binds, or None: a part no read can
    name (a BLOB or NULL, or a TEXT Python cannot decode), since a str never equals any of them."""
    kind, raw = value
    return raw.decode("utf-8") if kind == "text" and _decodes(value) else None


def _after(column: str, value) -> tuple[str, tuple]:
    """``column`` sorts after ``value`` (SQLite type, bytes), as one comparison the index seeks on. The scope
    columns have TEXT affinity, so they hold only NULL, TEXT and BLOB, in that order (a number is stored
    as text): past a NULL (or a number) comes the empty text; a TEXT Python cannot decode is bound as its
    bytes cast to TEXT."""
    kind, raw = value
    if kind == "text":
        text = _name(value)
        return (f"{column}>?", (text,)) if text is not None else (f"{column}>CAST(? AS TEXT)", (raw,))
    if kind == "blob":
        return f"{column}>?", (raw,)
    return f"{column}>=''", ()


def _distinct(c, source: str, nullable: bool, prefix: tuple) -> list:
    """The distinct values of the scope column after ``prefix`` (the str values of the ones before it), in
    index order, each found by one seek past the previous one: the work grows with the distinct values,
    never with the rows behind them. (Not a row-value comparison over all three columns: SQLite seeks
    that on its first column only and filters the entries after it one by one.)"""
    column = _SCOPE_COLUMNS[len(prefix)]
    fixed = [f"{name}=?" for name in _SCOPE_COLUMNS[:len(prefix)]]
    out, last = [], ("null", b"")                       # NULL sorts first: looked for on its own, by a seek
    if nullable and c.execute(f"SELECT 1 FROM {source} WHERE " + " AND ".join(fixed + [f"{column} IS NULL"])
                              + " LIMIT 1", prefix).fetchone():
        out.append(last)
    while True:
        after, args = _after(column, last)
        row = c.execute(f"SELECT typeof({column}),CAST({column} AS BLOB) FROM {source} WHERE "
                        + " AND ".join(fixed + [after]) + f" ORDER BY {column} LIMIT 1", (*prefix, *args)).fetchone()
        if row is None:
            return out
        value = (row[0], row[1] if row[1] is not None else b"")
        if value == last:
            raise RuntimeError("cost rollup scope scan did not advance")    # never loop on a value
        out.append(value)
        last = value


def _scopes(c, environment, undecodable=None) -> list[tuple]:
    """Every scope with facts or derived rows, each part a str: a loose index scan (per table, one seek
    per distinct environment, provider and account), never a table scan.

    Parts are read as type and bytes. A part no read can name (a BLOB or NULL, or a non-UTF-8 TEXT, which
    only a write outside the product leaves: every read binds UTF-8 text) never stops the scan: its scope
    is left out and added to ``undecodable`` (the scan goes past it), so the scopes returned can always
    be bound, recorded and audited. The sentinels' environment '' is skipped."""
    found = set()

    def unnamed(*parts):
        if undecodable is not None:
            undecodable.add(tuple(key_label(*part) for part in parts))

    for source, nullable in _SCOPE_SOURCES:
        envs = ([("text", environment.encode("utf-8"))] if environment is not None
                else _distinct(c, source, nullable, ()))
        for env in envs:
            e = _name(env)
            if e is None:
                unnamed(env)
                continue
            if e == "":
                continue                                 # the sentinels' empty scope
            for provider in _distinct(c, source, nullable, (e,)):
                p = _name(provider)
                if p is None:
                    unnamed(env, provider)
                    continue
                for account in _distinct(c, source, nullable, (e, p)):
                    a = _name(account)
                    if a is None:
                        unnamed(env, provider, account)
                    else:
                        found.add((e, p, a))
    return sorted(found)


def _previous_month(now: int) -> int:
    date = _EPOCH + timedelta(seconds=now)
    first = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (first - timedelta(days=1)).replace(day=1)
    return int((previous - _EPOCH).total_seconds())


#: A fact's columns for the verifier. Keys (the attempt's and its call's) come as SQLite type and bytes
#: and the times only when they are integers, so a broken import's undecodable key or text time
#: cannot stop the scan; every other text is what a well-formed fact holds.
_OPEN_FACT = ("typeof(a.attempt_key) AS key_t,CAST(a.attempt_key AS BLOB) AS key_b,"
              "CASE WHEN typeof(a.admitted_at)='integer' THEN a.admitted_at END AS admitted_at,"
              "CASE WHEN typeof(a.expires_at)='integer' THEN a.expires_at END AS expires_at,"
              "att.id IS NOT NULL AS has_att,typeof(att.call_key) AS call_t,CAST(att.call_key AS BLOB) AS call_b,"
              "c.id IS NOT NULL AS has_call,c.status AS call_status,c.deadline AS call_deadline,"
              "acc.id IS NOT NULL AS has_acc,u.attempt_key IS NOT NULL AS has_usage,"
              "e.attempt_key IS NOT NULL AS has_ending")
_FACT = (_OPEN_FACT + ",a.unit,a.reserved_units,att.requested_model,acc.owner,u.cost_n,u.cost_d,u.cost_unit")
_FACT_JOINS = ("LEFT JOIN billing_attempts att ON att.id=a.attempt_key LEFT JOIN billing_calls c ON c.id=att.call_key "
               "LEFT JOIN billing_accounts acc ON acc.id=c.account_id LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
               "LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key")
_KEY = ("dim", "member", "span", "start", "unit", "den")
#: Let a dispatch thread of the same process take the GIL this often (facts) during a long check.
_YIELD_EVERY = 2_000


def _check_scope(c, scope, *, since, now, mode) -> ScopeReport:
    env, provider, account = scope
    last = newest(c, scope)
    M = last["m"] if last else None
    H = M - HORIZON_S if M is not None else None
    at_now = max(now, M) if M is not None else now
    lo = None if since is None else since - since % DAY
    report = ScopeReport(scope, 0, rebuild_seq(c, scope))
    report.open_faults = c.execute("SELECT COUNT(*) FROM model_cost_rollup_faults WHERE " + _SCOPE
                                   + " AND resolved_at IS NULL", scope).fetchone()[0]
    floor = None if lo is None else (lo if H is None else min(lo, H))
    # The open set first (keys only: bounded by the unpruned open rows), for the "missing" direction.
    typed = "SELECT typeof(attempt_key) AS key_t,CAST(attempt_key AS BLOB) AS key_b FROM {} WHERE " + _SCOPE
    inflight = {_key(r) for r in c.execute(typed.format("model_cost_inflight"), scope)}
    holds = {_key(r) for r in c.execute(typed.format("model_cost_hold"), scope)}
    expected = _Expected(c)
    try:
        exceptions: dict[tuple, int] = {}
        fact_faults = []
        # Facts, streamed in admitted_at order (the scope index): nothing is kept per fact.
        where = "a.environment=? AND a.provider=? AND a.account_ref=?" + ("" if floor is None else " AND a.admitted_at>=?")
        for r in c.execute("SELECT " + _FACT + " FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope "
                           + _FACT_JOINS + " WHERE " + where + " ORDER BY a.admitted_at",
                           scope if floor is None else (*scope, floor)):
            report.facts += 1
            if report.facts % _YIELD_EVERY == 0:
                time.sleep(0)
            key = _key(r)
            if _malformed(r) or _orphan(r):
                fact_faults.append(key)
                continue
            if not _decodes(key):
                # Well-formed for SQL, but no product writer can make such a key (Python writes UTF-8):
                # a write outside the product, which fails closed like a broken fact.
                report.differ("undecodable_key", attempt_key=_label(key))
            at = r["admitted_at"]
            resolved = bool(r["has_usage"])
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
                    exceptions[key] = at
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
            if M is not None and not resolved:
                if r["expires_at"] > M and key not in inflight:
                    report.differ("inflight_missing", attempt_key=_label(key))
                if (not r["has_ending"] and r["call_status"] == "reserved" and r["call_deadline"] > M
                        and key not in holds):
                    report.differ("hold_missing", attempt_key=_label(key))
        expected.finish()
        for key in fact_faults:
            report.differ("fact_fault", attempt_key=_label(key))
            if c.execute("SELECT 1 FROM model_cost_rollup_faults WHERE id=?", (fact_fault_id(key, scope),)).fetchone() is None:
                report.differ("fact_fault_missing", attempt_key=_label(key))
        # Buckets, both directions: day rows from ``lo`` (all when None); velocity rows from H, or every
        # one when the scope has no admission (no fact can justify any).
        clauses, args = ["(b.dim='account'" + ("" if lo is None else " AND b.start>=?") + ")"], [] if lo is None else [lo]
        if H is not None:
            clauses.append("(b.dim<>'account' AND b.start>=?)")
            args.append(H)
        else:
            clauses.append("b.dim<>'account'")
        on = " AND ".join(f"e.{k}=b.{k}" for k in _KEY)
        for r in c.execute("SELECT " + ",".join(f"b.{k}" for k in _KEY + schema.BUCKET_VALUES) + ",e.rowid AS e_row,"
                           + ",".join(f"e.{v} AS e_{v}" for v in schema.BUCKET_VALUES)
                           + f" FROM model_cost_buckets b LEFT JOIN temp.{_Expected.TABLE} e ON {on}"
                           " WHERE b.environment=? AND b.provider=? AND b.account_ref=? AND (" + " OR ".join(clauses) + ")",
                           (*scope, *args)):
            key = [r[k] for k in _KEY]
            if not (type(r["den"]) is int and r["den"] >= 0 and _structure(r, main=r["den"] == 0)):
                report.differ("structure", key=key)
            found = {v: r[v] for v in schema.BUCKET_VALUES}
            want = None if r["e_row"] is None else {v: r["e_" + v] for v in schema.BUCKET_VALUES}
            if want != found:
                report.differ("bucket", key=key, expected=want, found=found)
        for r in c.execute(f"SELECT * FROM temp.{_Expected.TABLE} e WHERE NOT EXISTS(SELECT 1 FROM model_cost_buckets b "
                           f"WHERE b.environment=? AND b.provider=? AND b.account_ref=? AND {on})", scope):
            report.differ("bucket", key=[r[k] for k in _KEY], expected={v: r[v] for v in schema.BUCKET_VALUES}, found=None)
    finally:
        expected.drop()
    # Class-X exceptions, both directions.
    have = {_key(r): r["admitted_at"] for r in c.execute(
        "SELECT typeof(attempt_key) AS key_t,CAST(attempt_key AS BLOB) AS key_b,admitted_at FROM model_cost_exceptions "
        "WHERE " + _SCOPE + ("" if lo is None else " AND admitted_at>=?"), scope if lo is None else (*scope, lo))}
    if have != exceptions:
        report.differ("exceptions", missing=[_label(k) for k in sorted(set(exceptions) - set(have))[:5]],
                      extra=[_label(k) for k in sorted(set(have) - set(exceptions))[:5]])

    def valid(row):
        return row["key_t"] != "null" and row["same_scope"] and not _malformed(row) and not _orphan(row)

    # The open set, the other direction: every row is justified by its facts (joined, streamed).
    for table, own in (("model_cost_inflight", "i.expires_at AS own_expires"),
                       ("model_cost_hold", "typeof(i.call_key) AS own_call_t,CAST(i.call_key AS BLOB) AS own_call_b,"
                                           "i.deadline AS own_deadline")):
        for row in c.execute(f"SELECT typeof(i.attempt_key) AS own_t,CAST(i.attempt_key AS BLOB) AS own_b,{own},"
                             "(a.environment IS ? AND a.provider IS ? AND a.account_ref IS ?) AS same_scope,"
                             + _OPEN_FACT + f" FROM {table} i LEFT JOIN model_cost_admissions a ON a.attempt_key=i.attempt_key "
                             + _FACT_JOINS + " WHERE i.environment=? AND i.provider=? AND i.account_ref=?", (*scope, *scope)):
            if table == "model_cost_inflight":
                if not valid(row) or row["has_usage"] or row["expires_at"] != row["own_expires"]:
                    report.differ("inflight_stale", attempt_key=_label(_key(row, "own")))
            elif (not valid(row) or row["has_usage"] or row["has_ending"]
                  or row["call_status"] != "reserved" or row["call_deadline"] != row["own_deadline"]
                  or _key(row, "call") != _key(row, "own_call")):
                report.differ("hold_stale", attempt_key=_label(_key(row, "own")))
    # The read path against the uncapped oracle (not in the bounded hourly run).
    if mode != "recent" and M is not None and not report.open_faults and not fact_faults:
        newest_unit = last["unit"]
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


#: SQLite errors of the environment (contention, I/O, memory), not of one scope's facts: re-raised.
_TRANSIENT = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_INTERRUPT, sqlite3.SQLITE_IOERR,
                        sqlite3.SQLITE_NOMEM, sqlite3.SQLITE_FULL, sqlite3.SQLITE_CANTOPEN})


def _scope_error(exc: BaseException) -> bool:
    """Whether checking one scope failed because of what its rows hold (a value Python cannot decode
    or parse, a broken invariant): recorded as that scope's difference, so the run goes on."""
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", None)
        return code is None or code & 0xFF not in _TRANSIENT
    return isinstance(exc, (BillingError, ValueError, TypeError, ArithmeticError, RuntimeError))


def _check(c, *, environment, since, now, mode, scopes=None) -> VerifyReport:
    report = VerifyReport(mode, now, since, environment)
    if c.execute("SELECT 1 FROM model_cost_rollup_deltas LIMIT 1").fetchone():
        report.issues.append("deltas_not_empty")
    if not schema.sentinels_present(c):
        report.issues.append("sentinel_missing")
    damage = list(report.issues)            # store-wide: every scope's rollup may be affected
    if scopes is None:
        undecodable = set()
        scopes = _scopes(c, environment, undecodable)
        if undecodable:
            # Rows in a scope no reader can name (every read binds UTF-8 text): told, never read.
            report.issues.append(UNNAMED_SCOPE)
    for scope in scopes:
        try:
            result = _check_scope(c, scope, since=since, now=now, mode=mode)
        except Exception as exc:  # noqa: BLE001 - one scope's facts never stop the others' check
            if not _scope_error(exc):
                raise
            result = ScopeReport(scope, 0, rebuild_seq(c, scope))
            result.differ("verify_error", error=type(exc).__name__, detail=str(exc)[:200])
        for issue in damage:
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
                        ("verify:" + scope_key(scope) + ":" + str(result.rebuild_seq), *scope, detail, at)).rowcount
                    if added:
                        _system_audit(t, "billing.cost.rollup_inconsistent", "model_cost_rollup", scope_key(scope),
                                      {"billing_environment": scope[0], "provider": scope[1], "account_ref": scope[2],
                                       "source": report.mode, "differences": result.count})
                        alerts.append(scope)
                t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,"
                          "facts,differences,rebuild_seq,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (_id("cra"), *scope, report.mode, status, report.since or 0, result.facts, detail,
                           result.rebuild_seq, at))
            status = ("mismatch" if report.issues or "mismatch" in statuses.values()
                      else "superseded" if statuses else "ok")
            if report.issues and (not statuses or UNNAMED_SCOPE in report.issues):
                # A store-wide problem no scope's latch carries: any when no scope differs (a delta row at
                # rest before any fact; the first fact write empties the deltas), and rows in scopes no
                # read can name whatever else differs (no scope's fault covers them). Reported and alerted
                # all the same.
                _system_audit(t, "billing.cost.rollup_inconsistent", "model_cost_rollup", "*",
                              {"billing_environment": report.environment or "", "source": report.mode,
                               "issues": list(report.issues)})
                alerts.append(("*", "*", "*"))
            environments = {s[0] for s in report.scopes} | ({report.environment} if report.environment else set())
            for environment in sorted(environments or {""}):
                t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,"
                          "facts,differences,rebuild_seq,created_at) VALUES (?,?,'','',?,?,?,?,?,?,?)",
                          (_id("cra"), environment, report.mode, status, report.since or 0,
                           sum(s.facts for k, s in report.scopes.items() if k[0] == environment),
                           json.dumps({"scopes": len(report.scopes), "mismatched": len(statuses),
                                       **({"issues": list(report.issues)} if report.issues else {})}),
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

        Never automatic (platform.py cost-rollup rebuild). It repairs the DERIVED rows (a write past
        the gate, a dropped trigger, a defect) and refuses, changing nothing, when any cost fact of
        the scope is broken (malformed, orphan, or with a key Python cannot decode:
        ``RebuildRefused.attempt_keys``, named by ``key_label``) or when the result does not verify.
        Facts cannot be edited (immutable_cost_admission) and only a write outside the product can
        break one, so such a scope stays refused until the database is restored from a backup taken
        before that write (docs/platform/model-risk.md §13)."""
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
                differences = report.scopes[scope].differences
                raise RebuildRefused(attempt_keys=[d["attempt_key"] for d in differences if d["check"] in FACT_CHECKS],
                                     differences=differences)
            t.execute("INSERT INTO model_cost_rollup_rebuilds(id,environment,provider,account_ref,actor,reason,"
                      "finished_at) VALUES (?,?,?,?,?,?,?)", (rebuild_id, *scope, actor, reason, at))
            seq = t.execute("SELECT seq FROM model_cost_rollup_rebuilds WHERE id=?", (rebuild_id,)).fetchone()[0]
            t.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,facts,"
                      "differences,rebuild_seq,created_at) VALUES (?,?,?,?,'rebuild','ok',0,?,'{}',?,?)",
                      (_id("cra"), *scope, replayed["facts"], seq, at))
            _system_audit(t, "billing.cost.rollup_rebuilt", "model_cost_rollup", scope_key(scope),
                          {"billing_environment": environment, "provider": provider, "account_ref": account_ref,
                           "actor": actor, "reason": reason, "facts": replayed["facts"], "resolved_faults": resolved,
                           "rebuild_seq": seq})
        return {"id": rebuild_id, "seq": seq, "scope": list(scope), "facts": replayed["facts"],
                "resolved_faults": resolved}

    @staticmethod
    def prune(store, *, environment: str | None = None, batch: int = 5_000) -> dict:
        """Delete what can never be read again, per scope, in batches under the gate: velocity
        buckets older than (newest admission - 2 days), inflight rows expired by the newest
        admission, hold rows past their deadline by it. Day buckets are never pruned. A read path's
        ``now`` is at least M (the clock check refuses an admission after it), and M never decreases
        (facts are insert-only), so M read before the batches can only delete less than the current one."""
        integer(batch, positive=True, maximum=50_000)
        counts = {"buckets": 0, "inflight": 0, "hold": 0}
        with store.conn() as c:
            scopes = _scopes(c, environment)
        for scope in scopes:
            with store.conn() as c:
                last = newest(c, scope)
            if last is None:
                continue
            M = last["m"]
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
                (f"read:{exc.check}:" + scope_key(scope) + f":{exc.rebuild_seq}", *scope, "read_" + exc.check,
                 exc.check, at)).rowcount
            if added:
                _system_audit(t, "billing.cost.rollup_inconsistent", "model_cost_rollup", scope_key(scope),
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
                  dedupe_key=f"cost:rollup:{scope_key(scope)}:{day}", object_id=scope_key(scope))


def assert_consistent(store, **kwargs) -> VerifyReport:
    """Test helper (autouse teardowns): the whole rollup verifies against the facts."""
    report = CostRollup.check(store, all=True, **kwargs)
    if not report.ok:
        raise AssertionError(json.dumps(report.summary(), default=str)[:4000])
    return report


def _operator(explicit) -> str:
    if explicit:
        return identifier(explicit)
    import getpass
    try:
        return "operator:" + getpass.getuser()
    except Exception:  # noqa: BLE001 - a container user without a passwd entry is still named
        import os
        return f"operator:uid-{os.getuid()}"


def main(argv=None) -> int:
    """``python -m synwevo_core.billing.cost_rollup verify|rebuild [--data DIR]`` (docs/platform/model-risk.md §13).

    In production run it in a role container, which sets SYNWEVO_DATA_DIR (the default for --data):
    ``docker compose -f /opt/synwevo/config/compose.yaml exec -T portal python -m synwevo_core.billing.cost_rollup
    verify --all --no-record </dev/null``. Locally ``python scripts/platform.py cost-rollup …`` runs the same."""
    import argparse
    import sys
    from pathlib import Path

    from .. import env
    from ..store import Store

    ap = argparse.ArgumentParser(prog="cost-rollup", description=main.__doc__)
    ap.add_argument("action", choices=["verify", "rebuild"])
    ap.add_argument("--data", default=env.get("DATA_DIR"), help="the data directory (default: SYNWEVO_DATA_DIR)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--environment", choices=["test", "live"])
    ap.add_argument("--provider")
    ap.add_argument("--account-ref")
    ap.add_argument("--reason")
    ap.add_argument("--actor", help="rebuild: who runs it, for the audit (default: operator:<system user>)")
    args = ap.parse_args(argv)
    if not args.data or not (Path(args.data) / "docweave.sqlite3").is_file():
        # Never open (and so create) an empty store: a mistyped path is reported, not initialized.
        print(f"没有数据库：{args.data or '（未指定 --data，也没有 SYNWEVO_DATA_DIR）'}", file=sys.stderr)
        return 2
    store = Store(args.data)
    if args.action == "verify":
        if args.no_record:
            report = CostRollup.check(store, args.environment, all=args.all, mode="cli")
        else:
            report = CostRollup.verify(store, args.environment, all=args.all, mode="cli")
        print(json.dumps(report.summary(), ensure_ascii=False, indent=2, default=str))
        print(f"{len(report.scopes)} 个提供方账户，{report.facts} 条成本事实："
              + ("一致。" if report.ok else f"{len(report.mismatched)} 个不一致"
                 + (f"，全库问题 {report.issues}" if report.issues else "")
                 + ("（未记录）。" if args.no_record else "，已锁定；按 docs/platform/model-risk.md §13 处理。")),
              file=sys.stderr)
        return 0 if report.ok else 1
    missing = [flag for flag, value in (("--environment", args.environment), ("--provider", args.provider),
                                        ("--account-ref", args.account_ref), ("--reason", args.reason)) if not value]
    if missing:
        print("重建需要：" + " ".join(missing), file=sys.stderr)
        return 2
    try:
        result = CostRollup.rebuild(store, environment=args.environment, provider=args.provider,
                                    account_ref=args.account_ref, actor=_operator(args.actor),
                                    reason=args.reason)
    except RebuildRefused as exc:
        if exc.attempt_keys:
            print("没有重建：这些成本准入本身有问题（时间无效、到期超过准入后一天、找不到调用/钱包，或键不是 UTF-8 文本），"
                  "只能来自产品之外的写入（关掉外键的导入、手写 SQL）。事实不可修改，重建不会解除；该账户保持拒绝，"
                  "直到把数据库恢复到这次写入之前的备份（docs/platform/model-risk.md §13 第 3 步）：", file=sys.stderr)
            for key in exc.attempt_keys:
                print("  " + key, file=sys.stderr)
            if exc.differences:
                print(json.dumps(exc.differences, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        else:
            print("没有重建：重放后仍与事实不一致（什么也没改）：", file=sys.stderr)
            print(json.dumps(exc.differences, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - a refusal (reason, identifiers) is printed, not a traceback
        print(f"没有重建：{getattr(exc, 'code', None) or type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
