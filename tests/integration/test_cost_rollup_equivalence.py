"""Equivalence property test of the exact cost rollup (cost-capacity contract §11).

Three implementations of the cost-admission counters and of the velocity totals are compared on
seeded random histories written to real, isolated Stores (never ``data/``; no keys, no provider
traffic):

* **legacy** — tests/integration/legacy_cost_counters.py: a verbatim FIXTURE copy of the 5db93f86
  row-by-row ``_counters`` and velocity scan, 10,000-row caps included;
* **oracle** — ``oracle_counters`` / ``oracle_velocity`` below, written for this test from the
  contract and the 5db93f86 code, not from the new implementation: plain Python over every fact of
  the provider account, no caps, plus the contract's deliberate deviations (§9: an orphan or
  malformed admission refuses its scope; with several refusals the contract's check order
  decides). The product's own uncapped ``reference_counters`` / ``reference_velocity`` must agree
  with it too;
* **rollup** — ``cost_rollup.counters`` / ``cost_rollup.velocity``, and through them
  ``CostAdmission.admit`` (real Ledger dispatches) and ``RiskControls.guard``.

A history: 2 environments × 2 provider accounts, a rare second unit, 4 wallet owners (one an
organization), 2 routes; instants clustered at Sunday→Monday, month end→the 1st, Monday the 1st
(2034-05-01, 2035-01-01) and 2036-02-29. Operations: admissions with bounds 0/1/7/2**63-1 written
with SQL and through real ``Ledger.dispatch`` + ``CostAdmission.admit`` (with budgets, unknown
limits and velocity thresholds set at the oracle's total ± 1, and retries after a metered
platform fault); completed, late and platform_fault usage in cost classes R, Q and X, before or
after its admission; local stops; cancel, expiry and release; clock steps back; bulk SQL
(clustered on window edges); reopen (plain, after a lost marker, a dropped or emptied derived
table); rebuild; prune; recorded verify; rarely an orphan or malformed admission, also one dated
days ahead (its scope must then refuse, and only it, until the end of the history: no rebuild,
reopen, prune or verify clears it). ``test_a_broken_admission_*`` runs histories that always have one.

After every step: ``CostRollup.check`` over every day is clean (and, alternately, as the risk
monitor's daily and recent verifies run it), except that each orphan or malformed fact is
reported as itself; a rebuild of the touched provider account (rolled back afterwards) leaves
every row a read can still use identical; and at six instants (window, expiry, hold deadline,
one-day-open and velocity window edges ±1 s, plus random ones) rollup == oracle on every field,
legacy == both while it has at most 10,000 candidates, and the refusal codes agree when exactly
one refusal applies (when several apply, the rollup refuses with the first in the contract's
order, the others with one of them). Real admissions and ``RiskControls.guard`` (today's and the
5db93f86 one) decide exactly what the oracle's numbers decide. Every third step covers all four
provider accounts. ``test_12000_records_*`` is the capacity case.

Kept out of the random traces, each with a test of its own below: admissions violating I1's lower
bound (``expires_at <= admitted_at``: found here as a disagreement between the write-time producer
and the verifier; the verifier no longer reports it, since rollup and oracle agree on such an
admission) and cost encodings no trace can carry as JSON (BLOBs, text with an embedded NUL).

A disagreement is minimised (delta debugging over the recorded operation trace, replayed on fresh
Stores, then halving bulk writes) and reported with its seed, step, fields and minimal trace, so
it can become a deterministic test.

Cost-capacity phase 2 adds the personal limits' per-wallet spend rollup (section "phase 2" below):
``oracle_exposure`` (written here from the phase-2 addendum and the 0bcce176 code) against the rollup's
``spend_rollup.exposure``, the product's uncapped ``reference_exposure`` and the verbatim 0bcce176
``LegacyPayerBudgets._exposure``, on seeded wallet histories (reserve with holds 0, 1, 2^62; settle in
the four orders the money facts can arrive; unpaid and late-completed settlements; release and expiry;
malformed settled calls; clock steps back; bulk; reopen, also after a lost table, sentinel, marker or
trigger version; rebuild; real Ledger reservations decided at the oracle's budget boundary).

Default: 300 fixed seeds × ``STEPS`` (12) steps after a short prelude, plus 60 histories with a broken
admission; 100 wallet histories and 20 with a malformed settled call. ``SYNWEVO_COST_EQUIVALENCE_SEEDS=N`` runs seeds 1..N instead,
``SYNWEVO_COST_EQUIVALENCE_FAULT_SEEDS=N`` N broken-admission histories, ``SYNWEVO_COST_EQUIVALENCE_STEPS=K``
changes the steps per history.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import sqlite3

import pytest

from synwevo_core.billing import BillingError, ChargeContext, Ledger, Rate, Unit, Usage
from synwevo_core.billing.admission import CostAdmission, CostPolicy
from synwevo_core.billing.cost_rollup import (
    CostRollup, RebuildRefused, counters as rollup_counters, reference_counters, reference_velocity,
    velocity as rollup_velocity,
)
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.billing.risk import BrakeTripped, RiskControls, RiskPolicy
from synwevo_core.billing.spend_rollup import exposure as rollup_exposure, reference_exposure
from synwevo_core.identity import Identity
from synwevo_core.store import Store
from tests.integration.legacy_cost_counters import LegacyCostCounters, LegacyPayerBudgets, LegacyRiskControls
from tests.integration.test_billing_ledger import (
    COST_UNIT, START, UNIT, cost_price, evidence, fixture_authorize, fixture_verify, fund, sale_price,
)

SEEDS = list(range(1, int(os.environ.get("SYNWEVO_COST_EQUIVALENCE_SEEDS") or 300) + 1))
#: Histories of test_a_broken_admission_keeps_its_scope_refused_on_random_histories.
FAULT_SEEDS = int(os.environ.get("SYNWEVO_COST_EQUIVALENCE_FAULT_SEEDS") or 60)
STEPS = int(os.environ.get("SYNWEVO_COST_EQUIVALENCE_STEPS") or 12)

PROVIDER = "fixture-provider"
ENVIRONMENTS = ("test", "live")
ACCOUNT_REFS = ("fixture-provider-account", "fixture-provider-account-b")
SCOPES = tuple((environment, PROVIDER, account) for environment in ENVIRONMENTS for account in ACCOUNT_REFS)
MAIN = COST_UNIT
SECOND = Unit("FIXTURE-COST-B", 8)             # the rare second unit
WRONG_SCALE = Unit(COST_UNIT.code, COST_UNIT.scale + 1)
UNPUBLISHED = Unit("FIXTURE-COST-NONE", 8)       # a policy unit with no billing_units row
ROUTES = ("fixture-route-a", "fixture-route-b")
ORG = "fixture-equivalence-org"
INT64 = 2**63 - 1
BOUNDS = (0, 1, 7, INT64)
DAY = 86_400
OPEN_S = 86_400
CAP = 10_000
SQL_NAMESPACE = "fixture-equivalence-sql"
API_NAMESPACE = "fixture-equivalence"
Q_PRICE = "fixture-cost-q"
#: What the comparisons covered (how many of each outcome); read it to see a run was not vacuous.
STATS: Counter = Counter()


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text + "+00:00").timestamp())


#: Where histories cluster: Sunday→Monday, month end→the 1st, Monday the 1st, the leap day.
ANCHORS = (
    utc("2034-05-01T00:00:00"),   # Monday the 1st (week and month start together)
    utc("2034-05-08T00:00:00"),   # Sunday -> Monday
    utc("2035-01-01T00:00:00"),   # a year end that is also Monday the 1st
    utc("2036-01-01T00:00:00"),   # month (and year) end -> Wednesday the 1st
    utc("2036-02-29T00:00:00"),   # the leap day
    utc("2036-03-01T00:00:00"),   # leap-February end -> Saturday the 1st
)

# Cost facts (cost_n, cost_d) of the three classes of contract §4 (B = G = 10**15).
R_COSTS = (("0", "1"), ("5", "1"), ("7", "8"), ("3", "125"), ("10", "8"), ("1", "32768"),
           ("999999999999999", "1000000000000000"), (str(INT64), "1"), (str(INT64), "8"),
           ("123456789012345678", "1000"))
Q_COSTS = (("1", "3"), ("1", "7"), (str(3 * 2**53 + 1), "3"), ("2", "6"), ("1", "10000000000000000"),
           (str(INT64), str(INT64)), ("5", str(INT64)), (str(INT64), "3"), ("12345678901234567", "9"))
X_COSTS = (("007", "1"), ("-5", "1"), ("x", "1"), ("1", "0"), ("12345678901234567890", "1"),
           ("1", "12345678901234567890"), (" 7", "1"), ("+7", "1"), ("1_000", "1"), ("\u0663", "1"),
           ("", "1"), ("1.5", "1"), ("0", "-3"), (str(INT64 + 1), "3"), ("5", "00"), ("4" * 4301, "1"),
           ("99999999999999999999999999", "7"), ("-0", "1"), ("00", "1"), ("0000000000000001", "1"),
           ("1", "0000000000000008"), ("7", "-0"), ("1e3", "1"), ("\uff17", "1"), ("1\n", "1"), ("1", " 8"))


# ================================================================ the oracle (independent)
def oracle_windows(now: int) -> dict:
    """The UTC day, the ISO week (Monday) and the month containing ``now``."""
    try:
        moment = datetime.fromtimestamp(now, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise BillingError("invalid_cost_time") from None
    day = now - now % DAY
    return {"daily": day, "weekly": day - moment.weekday() * DAY,
            "monthly": int(datetime(moment.year, moment.month, 1, tzinfo=timezone.utc).timestamp())}


#: Every fact of a scope.
FACTS = ("SELECT a.attempt_key,a.unit,a.reserved_units,a.admitted_at,a.expires_at,att.id AS attempt,"
         "att.requested_model AS route,c.id AS call,c.status AS call_status,c.deadline AS call_deadline,"
         "acc.id AS account,acc.owner AS owner,e.attempt_key AS ending,u.attempt_key AS usage,u.cost_n,u.cost_d,"
         "u.cost_unit FROM model_cost_admissions a LEFT JOIN billing_attempts att ON att.id=a.attempt_key "
         "LEFT JOIN billing_calls c ON c.id=att.call_key LEFT JOIN billing_accounts acc ON acc.id=c.account_id "
         "LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key "
         "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
         "WHERE a.environment=? AND a.provider=? AND a.account_ref=?")


def scope_facts(t, scope) -> list[dict]:
    return [dict(row) for row in t.execute(FACTS, scope).fetchall()]


def broken(f) -> bool:
    """Contract §5: malformed (time types, a negative admission time, expiry beyond a day) or orphan."""
    if f["attempt"] is None or f["call"] is None or f["account"] is None:
        return True
    a, x = f["admitted_at"], f["expires_at"]
    return type(a) is not int or type(x) is not int or a < 0 or x > a + OPEN_S


def admission_cost(f):
    """5db93f86 _counters: n<0 or d<=0, or unparsable -> invalid (None)."""
    try:
        n, d = int(f["cost_n"]), int(f["cost_d"])
        if n < 0 or d <= 0:
            return None
        return Fraction(n, d)
    except (TypeError, ValueError):
        return None


def risk_cost(f):
    """5db93f86 guard: only TypeError/ValueError/ZeroDivisionError make a cost invalid (None)."""
    try:
        return Fraction(int(f["cost_n"]), int(f["cost_d"]))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def newest(facts):
    times = [f["admitted_at"] for f in facts if type(f["admitted_at"]) is int]
    return max(times) if times else None


@dataclass
class Expect:
    codes: list                 # every refusal that applies, unique, in the contract's check order
    value: dict | None          # the result when no refusal applies
    candidates: int = 0         # what the 5db93f86 code counted against its 10,000 cap
    scale: bool = False         # the unit's scale differs (5db93f86 checked it before its cap)


def _unique(codes):
    return list(dict.fromkeys(codes))


def oracle_counters(t, facts, unit: Unit, now: int) -> Expect:
    """Contract §6 _counters, from the facts: every check evaluated, none short-circuited."""
    win = oracle_windows(now)
    E = min(win.values())
    L = min(E, now - OPEN_S)
    codes = []
    scale = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
    if scale is not None and scale[0] != unit.scale:
        codes.append("cost_unit_mismatch")                               # 0. scale
    good = [f for f in facts if not broken(f)]
    if len(good) != len(facts):
        codes.append("cost_rollup_inconsistent")                         # 1. fault (§9 deviation)
    if any(f["admitted_at"] > now for f in good):
        codes.append("cost_clock_regressed")                             # 2. clock
    window = [f for f in good if f["admitted_at"] >= E]
    tail = [f for f in good if L <= f["admitted_at"] < E and f["usage"] is None]
    if any(f["unit"] != unit.code or (f["usage"] is not None and f["cost_unit"] != unit.code) for f in window):
        codes.append("cost_unit_mismatch")                               # 3. day rows
    if any(f["unit"] != unit.code for f in tail):
        codes.append("cost_unit_mismatch")                               # 4. Monday-the-1st tail
        STATS["oracle:tail_foreign_unit"] += 1
        STATS["oracle:tail_foreign_unit_only"] += not any(
            f["unit"] != unit.code or (f["usage"] is not None and f["cost_unit"] != unit.code) for f in window)
    if any(f["usage"] is not None and admission_cost(f) is None for f in window):
        codes.append("invalid_cost_fact")                                # 5. class X
    codes = _unique(codes)
    candidates = len(window) + len(tail)
    scaled = scale is not None and scale[0] != unit.scale
    if codes:
        return Expect(codes, None, candidates, scaled)
    totals = {name: Fraction(0) for name in win}
    unknown = unknown_count = inflight = absorbed = absorbed_count = 0
    for f in good:
        if f["usage"] is None:
            cost = f["reserved_units"]
            if f["admitted_at"] >= L:
                in_flight = f["expires_at"] > now
                hold_open = f["call_status"] == "reserved" and f["call_deadline"] > now and f["ending"] is None
                if in_flight or hold_open:
                    unknown += cost
                    unknown_count += 1
                elif f["admitted_at"] >= E:
                    absorbed += cost
                    absorbed_count += 1
                inflight += in_flight
        elif f["admitted_at"] >= E:
            cost = admission_cost(f)
        else:
            continue
        for name, start in win.items():
            if f["admitted_at"] >= start:
                totals[name] += cost
    STATS["oracle:tail"] += bool(tail)
    STATS["oracle:unknown"] += bool(unknown_count)
    STATS["oracle:absorbed"] += bool(absorbed_count)
    STATS["oracle:inflight"] += bool(inflight)
    STATS["oracle:fractional"] += any(v.denominator != 1 for v in totals.values())
    return Expect([], {"windows": win, "totals": totals, "unknown_reserved_units": unknown,
                       "unknown_count": unknown_count, "inflight": inflight, "records": candidates,
                       "absorbed_unknown_units": absorbed, "absorbed_unknown_count": absorbed_count}, candidates)


def oracle_velocity(facts, unit: Unit, owner: str, route: str, now: int, risk) -> Expect:
    """Contract §6 velocity, from the facts: the 5db93f86 owner-OR-route set over the largest
    window with a threshold; a side without a threshold is 0."""
    sides = {"payer": (risk.payer_window_s, risk.payer_window_cost),
             "route": (risk.route_window_s, risk.route_window_cost)}
    enabled = [w for w, cost in sides.values() if cost is not None]
    if not enabled:
        return Expect([], {"payer": Fraction(0), "route": Fraction(0)})
    W = max(enabled)
    rows = [f for f in facts if not broken(f) and f["admitted_at"] > now - W
            and (f["owner"] == owner or f["route"] == route)]
    codes = []
    if any(f["unit"] != unit.code or (f["usage"] is not None and f["cost_unit"] != unit.code) for f in rows):
        codes.append("cost_unit_mismatch")
    if any(f["usage"] is not None and risk_cost(f) is None for f in rows):
        codes.append("invalid_cost_fact")
    if codes:
        return Expect(codes, None, len(rows))

    def total(member, key, window):
        return sum((Fraction(f["reserved_units"]) if f["usage"] is None else risk_cost(f) for f in rows
                    if f[key] == member and f["admitted_at"] > now - window), Fraction(0))

    payer = total(owner, "owner", risk.payer_window_s) if sides["payer"][1] is not None else Fraction(0)
    by_route = total(route, "route", risk.route_window_s) if sides["route"][1] is not None else Fraction(0)
    return Expect([], {"payer": payer, "route": by_route},
                  len(rows))


def _ceil(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def oracle_guard(vel: Expect, risk, *, amount: int, policy: CostPolicy, payer: dict, route: str):
    """What RiskControls.guard decides with a closed breaker, no allowlist and no suspension."""
    if risk is None or (risk.payer_window_cost is None and risk.route_window_cost is None):
        return ("ok", None)
    if vel.codes:
        return ("refused", vel.codes[0])
    base = {"unit": policy.unit.code, "policy_revision": policy.revision, "actor_user_id": payer["actor"]}
    if risk.payer_window_cost is not None and vel.value["payer"] + amount > risk.payer_window_cost:
        return ("refused", "risk_payer_brake", {**base, "scope_kind": payer["kind"], "scope_id": payer["id"],
                "reason_code": "payer_velocity", "window_s": risk.payer_window_s,
                "threshold_units": risk.payer_window_cost, "observed_units": _ceil(vel.value["payer"] + amount)})
    if risk.route_window_cost is not None and vel.value["route"] + amount > risk.route_window_cost:
        return ("refused", "risk_route_brake", {**base, "scope_kind": "route", "scope_id": route,
                "reason_code": "route_velocity", "window_s": risk.route_window_s,
                "threshold_units": risk.route_window_cost, "observed_units": _ceil(vel.value["route"] + amount)})
    return ("ok", None)


def oracle_admit(counters: Expect, vel: Expect | None, policy: CostPolicy, *, amount: int, payer: dict,
                 route: str):
    """CostAdmission.admit's decision from the oracle's numbers (5db93f86 order, contract §6)."""
    if counters.codes:
        return ("refused", counters.codes[0])
    value = counters.value
    for period, total in value["totals"].items():
        if total + amount > getattr(policy, period + "_budget"):
            return ("refused", "cost_" + period + "_budget_exceeded")
    if value["unknown_reserved_units"] + amount > policy.unknown_budget:
        return ("refused", "cost_unknown_budget_exceeded")
    if value["unknown_count"] + 1 > policy.max_unknown:
        return ("refused", "cost_unknown_limit")
    if value["inflight"] + 1 > policy.max_inflight:
        return ("refused", "cost_inflight_limit")
    if vel is None:
        return ("ok", None)
    return oracle_guard(vel, policy.risk, amount=amount, policy=policy, payer=payer, route=route)


# ================================================================ comparison
class Mismatch(AssertionError):
    """A disagreement. ``signature`` identifies it across replays; ``spec`` re-runs the check."""

    def __init__(self, signature: str, detail: dict, spec: dict):
        self.signature, self.detail, self.spec = signature, detail, spec
        super().__init__(signature + " " + json.dumps(detail, default=str, sort_keys=True)[:3000])


def outcome(fn):
    try:
        return ("ok", fn())
    except BrakeTripped as exc:
        return ("refused", exc.code, exc.trip)
    except BillingError as exc:
        return ("refused", exc.code)
    except Exception as exc:  # noqa: BLE001 - a crash is an outcome to compare, not a test error
        return ("error", f"{type(exc).__name__}: {str(exc)[:200]}")


COUNTER_FIELDS = ("windows", "totals", "unknown_reserved_units", "unknown_count", "inflight",
                  "absorbed_unknown_units", "absorbed_unknown_count")


def _short(value):
    if isinstance(value, Fraction):
        return f"{value.numerator}/{value.denominator}"
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value]
    return value


def _fields(expected: dict, got, fields) -> list:
    if got[0] != "ok":
        return ["outcome"]
    return [name for name in fields if got[1].get(name) != expected.get(name)]


def compare_counters(spec, expect: Expect, rollup, reference, legacy):
    """rollup == oracle on every field; reference == oracle; legacy == both within its cap."""
    fields = COUNTER_FIELDS + (("records",) if spec.get("records") else ())
    STATS["counters:" + ("ok" if not expect.codes else "+".join(expect.codes))] += 1
    STATS["counters:legacy_compared"] += legacy is not None and expect.candidates <= CAP

    def fail(who, got, extra=None):
        raise Mismatch(f"counters:{who}:" + ",".join(extra or []),
                       {"spec": spec, "expected": {"codes": expect.codes, "value": _short(expect.value)},
                        who: _short(got)}, spec)

    if not expect.codes:
        bad = _fields(expect.value, rollup, fields)
        if bad:
            fail("rollup", rollup, bad)
        if reference is not None:
            bad = _fields(expect.value, reference, COUNTER_FIELDS + ("records",))
            if bad:
                fail("reference", reference, bad)
        if legacy is not None:
            if expect.candidates <= CAP:
                bad = _fields(expect.value, legacy, COUNTER_FIELDS + ("records",))
                if bad:
                    fail("legacy", legacy, bad)
            elif legacy[:2] != ("refused", "cost_accounting_capacity"):
                fail("legacy", legacy, ["capacity"])
        return
    if rollup[:2] != ("refused", expect.codes[0]):
        fail("rollup", rollup, ["code"])
    if reference is not None and (reference[0] != "refused" or reference[1] not in expect.codes
                                  or (len(expect.codes) == 1 and reference[1] != expect.codes[0])):
        fail("reference", reference, ["code"])
    if legacy is not None:
        if expect.candidates > CAP:
            if legacy[:2] != ("refused", "cost_unit_mismatch" if expect.scale else "cost_accounting_capacity"):
                fail("legacy", legacy, ["capacity"])
        elif legacy[0] != "refused" or legacy[1] not in expect.codes:
            fail("legacy", legacy, ["code"])


def compare_velocity(spec, expect: Expect, rollup, reference, legacy, risk):
    enabled = [side for side, cost in (("payer", risk.payer_window_cost), ("route", risk.route_window_cost))
               if cost is not None]

    def fail(who, got, extra):
        raise Mismatch(f"velocity:{who}:" + ",".join(extra),
                       {"spec": spec, "expected": {"codes": expect.codes, "value": _short(expect.value)},
                        who: _short(got)}, spec)

    def totals(got):
        return {"payer": got[1][0], "route": got[1][1]}

    STATS["velocity:" + ("ok" if not expect.codes else "+".join(expect.codes))] += 1
    STATS["velocity:sides:" + "+".join(enabled)] += 1

    if not expect.codes:
        for who, got, sides in (("rollup", rollup, ("payer", "route")), ("reference", reference, ("payer", "route")),
                                ("legacy", legacy, enabled)):
            if got is None:
                continue
            if who == "legacy" and expect.candidates > CAP:
                if got[:2] != ("refused", "risk_accounting_capacity"):
                    fail(who, got, ["capacity"])
                continue
            if got[0] != "ok":
                fail(who, got, ["outcome"])
            bad = [side for side in sides if totals(got)[side] != expect.value[side]]
            if bad:
                fail(who, got, bad)
        return
    if rollup[:2] != ("refused", expect.codes[0]):
        fail("rollup", rollup, ["code"])
    for who, got in (("reference", reference), ("legacy", legacy)):
        if got is None or (who == "legacy" and expect.candidates > CAP):
            continue
        if got[0] != "refused" or got[1] not in expect.codes or (len(expect.codes) == 1 and got[1] != expect.codes[0]):
            fail(who, got, ["code"])


# ================================================================ the world
@dataclass
class World:
    root: Path
    store: Store
    users: list
    owners: list
    wallets: dict
    runs: dict
    clock: list
    ledger: Ledger
    aliases: dict = field(default_factory=dict)      # alias of a real (Ledger) object -> its keys
    reverse: dict = field(default_factory=dict)      # real attempt key -> alias

    @property
    def now(self) -> int:
        return self.clock[0]

    def context(self, owner: int) -> str:
        if self.owners[owner] == ORG:
            return json.dumps({"payer_kind": "organization", "payer_id": ORG, "payer_user_id": self.users[0],
                               "actor_user_id": self.users[0]})
        return json.dumps({"payer_user_id": self.owners[owner], "actor_user_id": self.owners[owner]})

    def payer(self, owner: int) -> dict:
        if self.owners[owner] == ORG:
            return {"kind": "organization", "id": ORG, "owner": ORG, "actor": self.users[0]}
        return {"kind": "user", "id": self.owners[owner], "owner": self.owners[owner], "actor": self.owners[owner]}

    def new_ledger(self) -> Ledger:
        return Ledger(self.store, environment="test", namespace=API_NAMESPACE, authorize=fixture_authorize,
                      verify_payment=fixture_verify, clock=lambda: self.clock[0], max_hold_seconds=86_400)


def make_world(root: Path, start: int) -> World:
    """FIXTURE: an isolated Store with three users, a funded wallet, published fixture prices, a
    second cost unit, one wallet per owner and environment, and a run per environment for SQL calls."""
    store = Store(root)
    with store.conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS fixture_payment_inbox(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);"
                        "CREATE TABLE IF NOT EXISTS fixture_billing_authority(payer TEXT,actor TEXT,authority TEXT);")
    identity = Identity(store)
    users = [identity.create_user(f"cost-equivalence-{i}@example.test", None, status="active",
                                  with_personal_workspace=False)["id"] for i in range(3)]
    with store.tx() as t:
        t.executemany("INSERT INTO fixture_billing_authority VALUES (?,?,?)", [(u, u, "fixture-approval") for u in users])
    clock = [START]
    world = World(root, store, users, users + [ORG], {}, {}, clock, None)
    world.ledger = world.new_ledger()
    world.ledger.publish_price(sale_price())
    world.ledger.publish_price(cost_price())
    world.ledger.publish_price(cost_price(price_id=Q_PRICE, input_uncached=Rate(1, 7), input_cached=Rate(2, 3),
                                          output=Rate(5, 11)))
    clock[0] = start
    fund((store, world.ledger, users, clock), 10**15, order="fixture-equivalence", ledger=world.ledger)
    with store.tx() as t:
        t.execute("INSERT INTO billing_units(code,scale) VALUES (?,?)", (SECOND.code, SECOND.scale))
        for environment in ENVIRONMENTS:
            run = "fixture-equivalence-run-" + environment
            t.execute("INSERT INTO billing_runs(id,environment,namespace,run_id,context,unit,budget,max_calls,"
                      "max_attempts,deadline,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (run, environment, SQL_NAMESPACE, "sql-run", "{}", UNIT.code, 0, 10_000, 10_000,
                       start + 400 * DAY, START))
            world.runs[environment] = run
            for k, owner in enumerate(world.owners):
                row = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND kind='wallet' "
                                "AND owner=?", (environment, UNIT.code, owner)).fetchone()
                if row is None:
                    key = f"fixture-equivalence-wallet-{environment}-{k}"
                    t.execute("INSERT INTO billing_accounts(id,environment,unit,owner,kind) VALUES (?,?,?,?,'wallet')",
                              (key, environment, UNIT.code, owner))
                    world.wallets[(environment, k)] = key
                else:
                    world.wallets[(environment, k)] = row[0]
    return world


# ================================================================ operations (concrete, replayable)
def _usage(t, attempt: str, u: dict) -> None:
    t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
              "absorbed_d,late,created_at) VALUES (?,'{}',?,?,?,?,0,'0','1',?,?)",
              (attempt, u["outcome"], u["n"], u["d"], u["unit"], u["late"], u["at"]))


def _admit_sql(w: World, op: dict) -> None:
    with w.store.tx() as t:
        call = op.get("new_call")
        if call:
            t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,"
                      "deadline,status,created_at) VALUES (?,?,?,?,?,'fixture-sale-v1',0,?,?,?)",
                      (call["key"], w.runs[call["env"]], call["key"], w.context(call["owner"]),
                       w.wallets[(call["env"], call["owner"])], call["deadline"], call["status"], call["created_at"]))
        t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                  "requested_model,cost_price_id,status,created_at,dispatched_at) VALUES "
                  "(?,?,?,'fixture-equivalence-sql',?,?,?,?,?,'fixture-cost-v1','dispatch_unknown',?,?)",
                  (op["attempt"], op["call"], op["attempt"], op["attempt"] + "-fence", op["created_at"], op["env"],
                   PROVIDER, op["route"], op["created_at"], op["created_at"]))
        if op.get("usage_first"):
            _usage(t, op["attempt"], op["usage_first"])
        t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,reserved_units,"
                  "policy_revision,limits_json,admitted_at,expires_at) VALUES (?,?,?,?,?,?,'fixture-equivalence-sql',"
                  "'{}',?,?)", (op["attempt"], op["env"], PROVIDER, op["acct"], op["unit"], op["reserved"],
                                op["admitted_at"], op["expires_at"]))


def _orphan(w: World, op: dict) -> None:
    """FIXTURE: an admission whose attempt does not exist, written with foreign keys off."""
    c = w.store._connect()
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,reserved_units,"
                  "policy_revision,limits_json,admitted_at,expires_at) VALUES (?,?,?,?,?,?,'fixture-orphan','{}',?,?)",
                  (op["attempt"], op["env"], PROVIDER, op["acct"], MAIN.code, op["reserved"], op["admitted_at"],
                   op["expires_at"]))
        c.execute("COMMIT")
    finally:
        c.close()


def _bulk(w: World, op: dict) -> None:
    p = op["prefix"]
    with w.store.tx() as t:
        t.execute("CREATE TEMP TABLE IF NOT EXISTS fixture_equivalence_times(i INTEGER PRIMARY KEY, at INTEGER NOT NULL)")
        t.execute("DELETE FROM fixture_equivalence_times")
        t.executemany("INSERT INTO fixture_equivalence_times VALUES (?,?)", list(enumerate(op["times"])))
        t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,"
                  "status,created_at) SELECT ?||'-call-'||i,?,?||'-'||i,?,?,'fixture-sale-v1',0,at-5+?,?,at-5 "
                  "FROM fixture_equivalence_times",
                  (p, w.runs[op["env"]], p, w.context(op["owner"]), w.wallets[(op["env"], op["owner"])], op["hold"],
                   op["call_status"]))
        t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                  "requested_model,cost_price_id,status,created_at,dispatched_at) SELECT ?||'-attempt-'||i,?||'-call-'||i,"
                  "?||'-'||i,'fixture-equivalence-bulk',?||'-fence-'||i,at,?,?,?,'fixture-cost-v1','dispatch_unknown',"
                  "at,at FROM fixture_equivalence_times", (p, p, p, p, op["env"], PROVIDER, op["route"]))
        usage = op.get("usage")

        def usage_rows():
            t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                      "absorbed_d,late,created_at) SELECT ?||'-attempt-'||i,'{}',?,?,?,?,0,'0','1',?,at+1 "
                      "FROM fixture_equivalence_times",
                      (p, usage["outcome"], usage["n"], usage["d"], usage["unit"], usage["late"]))

        if usage and op.get("usage_first"):
            usage_rows()
        t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,reserved_units,"
                  "policy_revision,limits_json,admitted_at,expires_at) SELECT ?||'-attempt-'||i,?,?,?,?,?,"
                  "'fixture-equivalence-bulk','{}',at,at+? FROM fixture_equivalence_times",
                  (p, op["env"], PROVIDER, op["acct"], op["unit"], op["reserved"], op["expires_after"]))
        if usage and not op.get("usage_first"):
            usage_rows()
        if op.get("stop_at") is not None and not usage:
            t.execute("INSERT INTO model_cost_endings(attempt_key,ending,recorded_at) SELECT ?||'-attempt-'||i,"
                      "'stopped_locally',? FROM fixture_equivalence_times", (p, op["stop_at"]))


def _reopen(w: World, op: dict) -> None:
    connection = getattr(w.store._local, "c", None)
    if connection is not None:
        connection.close()
        w.store._local.c = None
    damage = op.get("damage")
    if damage:
        c = sqlite3.connect(w.store.db_path, isolation_level=None)
        try:
            what, _, table = damage.partition(":")
            if what == "drop":
                c.execute(f"DROP TABLE {table}")
            elif what == "empty":
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('gate')")
                c.execute(f"DELETE FROM {table}")
                c.execute("DELETE FROM model_cost_rollup_deltas")
                c.execute("COMMIT")
            elif what == "marker":
                c.execute("DELETE FROM meta WHERE key='model_cost_rollup_v1'")
        finally:
            c.close()
    w.store = Store(w.root)
    w.ledger = w.new_ledger()


class _Policy:
    """The current policy handed to CostAdmission (a real admission's own policy)."""

    def __init__(self, policy):
        self.policy = policy

    def __call__(self):
        return self.policy


def make_risk(spec):
    if spec is None:
        return None
    return RiskPolicy(allowlist_only=False, payer_window_s=spec["payer_s"], payer_window_cost=spec["payer_cost"],
                      route_window_s=spec["route_s"], route_window_cost=spec["route_cost"], balance_warn=None,
                      balance_close=None, balance_poll_s=60, reconcile_tolerance=0, pending_alert=None)


def make_policy(spec, *, unit=MAIN) -> CostPolicy:
    return CostPolicy("fixture-equivalence-v1", spec["acct"], PROVIDER, unit, True, spec["daily"], spec["weekly"],
                      spec["monthly"], spec["unknown"], spec["max_inflight"], spec["max_unknown"],
                      risk=make_risk(spec.get("risk")))


def _admit_api(w: World, op: dict, *, checking: bool) -> dict:
    """A real Ledger reservation, attempt and dispatch whose admission runs CostAdmission.admit."""
    now = w.now
    alias = op["alias"]
    if op.get("retry_of"):             # a retry after a metered platform fault, on the same call
        call = w.aliases[op["retry_of"]]["call"]
        ticket = w.ledger.prepare_attempt(call, attempt_id=alias, owner="fixture-equivalence-worker",
                                          cost_price_id=op["price"], requested_model=op["route"],
                                          retry_reason="platform_fault")
    else:
        ctx = ChargeContext(w.users[0], w.users[0], alias, "fixture-approval", "workspace-A")
        w.ledger.open_run(ctx, unit=UNIT, budget=10**12, max_calls=10, max_attempts=10,
                          deadline=now + op["hold_s"] + 1)
        call = w.ledger.reserve_call(ctx, logical_call_id=alias, amount=1, price_id="fixture-sale-v1",
                                     deadline=now + op["hold_s"])
        ticket = w.ledger.prepare_attempt(call, attempt_id=alias, owner="fixture-equivalence-worker",
                                          cost_price_id=op["price"], requested_model=op["route"])
    w.aliases[alias] = {"call": call, "ticket": ticket}
    w.reverse[ticket.attempt_key] = alias
    policy = make_policy(op["policy"])
    scope = ("test", PROVIDER, op["policy"]["acct"])
    expected = None
    if checking:
        with w.store.tx() as t:
            facts = scope_facts(t, scope)
            counters = oracle_counters(t, facts, MAIN, now)
            vel = (oracle_velocity(facts, MAIN, w.users[0], op["route"], now, policy.risk)
                   if policy.risk is not None else None)
        expected = oracle_admit(counters, vel, policy, amount=op["amount"], payer=w.payer(0), route=op["route"])
    admission = CostAdmission(w.ledger, current_policy=_Policy(policy))
    got = outcome(lambda: w.ledger.dispatch(ticket, admission=lambda t, a: admission.admit(
        t, a, amount=op["amount"], expires_at=now + op["expires_in"])))
    got = ("ok", None) if got[0] == "ok" else got
    STATS["admit:" + str(got[1] if got[0] == "refused" else "ok")] += checking
    if checking and got != expected:
        raise Mismatch("admit:" + str(expected[:2]) + "!=" + str(got[:2]),
                       {"op": op, "expected": expected, "got": got}, {"check": "admit", "alias": alias})
    return {"admitted": got[0] == "ok"}


def run_op(w: World, op: dict, *, checking: bool = True) -> dict:
    """Apply one recorded operation. Under replay (minimisation) a reference to something a removed
    operation would have created is skipped, never an error."""
    kind = op["op"]
    STATS["op:" + kind] += checking
    try:
        if kind == "clock":
            w.clock[0] = op["now"]
        elif kind == "admit_sql":
            _admit_sql(w, op)
        elif kind == "admit_api":
            return _admit_api(w, op, checking=checking)
        elif kind == "usage_sql":
            with w.store.tx() as t:
                _usage(t, op["attempt"], op["usage"])
                if op.get("settle_call"):
                    t.execute("UPDATE billing_calls SET status='settled' WHERE id=? AND status='reserved'",
                              (op["settle_call"],))
        elif kind == "usage_api":
            ticket = w.aliases[op["alias"]]["ticket"]
            w.ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=Usage(*op["tokens"])),
                                          outcome=op["outcome"])
        elif kind == "ending":
            key = w.aliases[op["alias"]]["ticket"].attempt_key if op.get("alias") else op["attempt"]
            if op["via"] == "api":
                assert CostAdmission(w.ledger, current_policy=_Policy(None)).record_local_stop(key) in (True, False)
            else:
                with w.store.tx() as t:
                    t.execute("INSERT INTO model_cost_endings(attempt_key,ending,recorded_at) "
                              "VALUES (?,'stopped_locally',?)", (key, w.now))
        elif kind == "cancel":
            if op.get("alias"):
                w.ledger.cancel_call(w.aliases[op["alias"]]["call"])
            else:
                with w.store.tx() as t:
                    t.execute("UPDATE billing_calls SET cancel_requested=1 WHERE id=?", (op["call"],))
        elif kind == "release":
            with w.store.tx() as t:
                t.execute("UPDATE billing_calls SET status=? WHERE id=? AND status='reserved'",
                          (op["status"], op["call"]))
        elif kind == "expire":
            w.ledger.expire_due()
            with w.store.tx() as t:
                t.execute("UPDATE billing_calls SET status='released' WHERE status='reserved' AND deadline<=? "
                          "AND run_key IN (?,?)", (w.now, *w.runs.values()))
        elif kind == "bulk":
            _bulk(w, op)
        elif kind == "reopen":
            _reopen(w, op)
        elif kind == "rebuild":
            CostRollup.rebuild(w.store, environment=op["scope"][0], provider=op["scope"][1], account_ref=op["scope"][2],
                               actor="fixture-equivalence", reason="equivalence property test rebuild",
                               clock=lambda: w.now)
        elif kind == "prune":
            CostRollup.prune(w.store)
        elif kind == "verify":
            report = CostRollup.verify(w.store, all=True, now=w.now, clock=lambda: w.now)
            if checking:
                clean(w, report)
        elif kind == "fault":
            if op["fault"] in ("orphan", "future_orphan"):
                _orphan(w, op)
            else:
                _admit_sql(w, op)
        else:
            raise AssertionError(kind)
    except Mismatch:
        raise
    except (KeyError, sqlite3.IntegrityError, BillingError, RebuildRefused):
        if checking:
            raise
    return {}


# ================================================================ checks after every step
def usable_rows(t, scope) -> dict:
    """Every derived row a read can still use (contract §8 prune): velocity buckets from the
    horizon, inflight/hold rows not yet passed by the newest admission, all day buckets and X rows."""
    row = t.execute("SELECT MAX(admitted_at) FROM model_cost_admissions WHERE environment=? AND provider=? "
                    "AND account_ref=? AND typeof(admitted_at)='integer'", scope).fetchone()
    M = row[0]
    clauses = {"model_cost_buckets": ("dim='account' OR start>=?", (M - 172_800,) if M is not None else None),
               "model_cost_inflight": ("expires_at>?", (M,) if M is not None else None),
               "model_cost_hold": ("deadline>?", (M,) if M is not None else None),
               "model_cost_exceptions": ("1", ())}
    out = {}
    for table, (clause, args) in clauses.items():
        if args is None:
            clause, args = "1", ()
        rows = t.execute(f"SELECT * FROM {table} WHERE environment=? AND provider=? AND account_ref=? AND ({clause})",
                         (*scope, *args)).fetchall()
        out[table] = sorted(json.dumps(dict(r), sort_keys=True) for r in rows)
    return out


class _Discard(Exception):
    pass


def clean(w: World, report, signature="verify") -> None:
    """Clean, except that each orphan or malformed admission is reported as itself (fact_fault)."""
    for scope in report.mismatched:
        with w.store.tx() as t:
            broken_keys = {f["attempt_key"] for f in scope_facts(t, scope) if broken(f)}
        result = report.scopes[scope]
        if (not broken_keys or result.count != len(broken_keys)
                or any(d.get("check") != "fact_fault" or d.get("attempt_key") not in broken_keys
                       for d in result.differences)):
            raise Mismatch(signature, report.summary(), {"check": "verify"})


def check_verify(w: World, **kwargs):
    """CostRollup.check over every day (default), or as the risk monitor runs it (daily, recent)."""
    kwargs = kwargs or {"all": True}
    clean(w, CostRollup.check(w.store, now=w.now, **kwargs))


def check_rebuild(w: World, scope, faulted: bool):
    """A rebuild of ``scope`` (rolled back) leaves every usable row identical."""
    seen = {}
    try:
        with w.store.tx() as t:
            seen["before"] = usable_rows(t, scope)
            seen["outcome"] = outcome(lambda: CostRollup.rebuild(
                w.store, environment=scope[0], provider=scope[1], account_ref=scope[2], actor="fixture-equivalence",
                reason="equivalence property test (rolled back)", clock=lambda: w.now))
            seen["after"] = usable_rows(t, scope)
            raise _Discard
    except _Discard:
        pass
    spec = {"check": "rebuild", "scope": scope}
    got = seen["outcome"]
    if faulted:
        if got[:2] != ("refused", "cost_rollup_rebuild_refused"):
            raise Mismatch("rebuild:not_refused", {"scope": scope, "got": _short(got)}, spec)
        return
    if got[0] != "ok":
        raise Mismatch("rebuild:refused", {"scope": scope, "got": _short(got)}, spec)
    if seen["before"] != seen["after"]:
        diff = {table: {"live_only": sorted(set(seen["before"][table]) - set(seen["after"][table]))[:5],
                        "rebuilt_only": sorted(set(seen["after"][table]) - set(seen["before"][table]))[:5]}
                for table in seen["before"] if seen["before"][table] != seen["after"][table]}
        raise Mismatch("rebuild:rows:" + ",".join(sorted(diff)), {"scope": scope, "diff": diff}, spec)


def read_counters(w: World, t, facts, spec):
    scope, unit, now = tuple(spec["scope"]), Unit(*spec["unit"]), spec["now"]
    expect = oracle_counters(t, facts, unit, now)
    faulted = "cost_rollup_inconsistent" in expect.codes
    rollup = outcome(lambda: rollup_counters(t, environment=scope[0], provider=scope[1], account_ref=scope[2],
                                             unit=unit, now=now, records=spec["records"]))
    reference = None if faulted else outcome(lambda: reference_counters(
        t, environment=scope[0], provider=scope[1], account_ref=scope[2], unit=unit, now=now))
    legacy = None
    if not faulted:
        policy = CostPolicy("fixture-legacy", scope[2], scope[1], unit, True, 0, 0, 0, 0, 1, 1)
        legacy = outcome(lambda: LegacyCostCounters(scope[0])._counters(t, policy, now))
    compare_counters(spec, expect, rollup, reference, legacy)
    return expect


def read_velocity(w: World, t, facts, spec):
    scope, unit, now = tuple(spec["scope"]), Unit(*spec["unit"]), spec["now"]
    risk = make_risk(spec["risk"])
    owner, route = w.owners[spec["owner"]], spec["route"]
    expect = oracle_velocity(facts, unit, owner, route, now, risk)
    kwargs = dict(environment=scope[0], provider=scope[1], account_ref=scope[2], unit=unit, owner=owner, route=route,
                  now=now, risk=risk)
    rollup = outcome(lambda: rollup_velocity(t, **kwargs))
    reference = outcome(lambda: reference_velocity(t, **kwargs))
    legacy = None
    if risk.payer_window_cost is not None or risk.route_window_cost is not None:
        policy = CostPolicy("fixture-legacy", scope[2], scope[1], unit, True, 0, 0, 0, 0, 1, 1, risk=risk)
        legacy = outcome(lambda: LegacyRiskControls(w.store, environment=scope[0]).velocity_scan(
            t, policy=policy, risk=risk, payer={"owner": owner}, route=route, now=now))
    compare_velocity(spec, expect, rollup, reference, legacy, risk)
    if spec.get("amount") is not None:
        policy = CostPolicy("fixture-equivalence-guard", scope[2], scope[1], unit, True, 0, 0, 0, 0, 1, 1, risk=risk)
        call = {"context": w.context(spec["owner"]), "account_id": w.wallets[(scope[0], spec["owner"])]}
        attempt = {"requested_model": route}
        expected = oracle_guard(expect, risk, amount=spec["amount"], policy=policy, payer=w.payer(spec["owner"]),
                                route=route)
        for who, controls in (("rollup", RiskControls(w.store, environment=scope[0], clock=lambda: now)),
                              ("legacy", LegacyRiskControls(w.store, environment=scope[0], clock=lambda: now))):
            if who == "legacy" and expect.candidates > CAP:
                continue
            got = outcome(lambda c=controls: c.guard(t, policy=policy, call=call, attempt=attempt,
                                                     amount=spec["amount"], now=now))
            got = ("ok", None) if got[0] == "ok" else got
            # Several refusals: the rollup follows the contract's order, legacy its row order.
            same = got == expected or (who == "legacy" and len(expect.codes) > 1 and len(got) == 2
                                       and got[0] == "refused" and got[1] in expect.codes)
            STATS[f"guard:{who}:" + str(expected[1] if expected[0] == "refused" else "ok")] += 1
            if not same:
                raise Mismatch(f"guard:{who}", {"spec": spec, "expected": _short(expected), "got": _short(got)}, spec)


def run_check(w: World, spec: dict):
    """Re-run one recorded check (used by the minimiser)."""
    if spec["check"] == "verify":
        return check_verify(w)
    if spec["check"] == "rebuild":
        with w.store.tx() as t:
            faulted = any(broken(f) for f in scope_facts(t, tuple(spec["scope"])))
        return check_rebuild(w, tuple(spec["scope"]), faulted)
    with w.store.tx() as t:
        facts = scope_facts(t, tuple(spec["scope"]))
        if spec["check"] == "counters":
            read_counters(w, t, facts, spec)
        elif spec["check"] == "velocity":
            read_velocity(w, t, facts, spec)


# ================================================================ histories
WEIGHTS = {"admit_sql": 26, "admit_api": 12, "usage_sql": 18, "usage_api": 9, "ending": 6, "cancel": 3,
           "release": 4, "expire": 3, "clock": 13, "clock_back": 3, "bulk": 6, "reopen": 2, "rebuild": 2,
           "prune": 2, "verify": 1}
FORWARD = (1, 2, 59, 60, 61, 3_599, 3_600, 3_601, 86_399, 86_400, 86_401)
WINDOW_CHOICES = (60, 61, 119, 120, 3_599, 3_600, 3_601, 7_200, 86_399, 86_400)


class History:
    def __init__(self, root: Path, seed: int, *, steps: int = STEPS, fault_rate: float = 0.05,
                 fault_by: int | None = None, weights: dict = WEIGHTS):
        self.seed, self.steps = seed, steps
        self.rng = random.Random(seed)
        anchor = self.rng.choice(ANCHORS)
        start = anchor - self.rng.choice((1, 2, 60, 3_600, DAY - 1, DAY, DAY + 1, 2 * DAY, 6 * DAY,
                                          self.rng.randint(1, 8 * DAY)))
        self.anchor = anchor
        self.root = root
        self.w = make_world(root / "store", start)
        self.trace = [{"op": "clock", "now": start}]
        self.n = 0
        self.faulted = set()
        self.fault_step = (self.rng.randint(1, fault_by or steps) if self.rng.random() < fault_rate else None)
        self.weights = weights

    # ---------------------------------------------------------------- helpers
    def key(self, kind: str) -> str:
        self.n += 1
        return f"fx-{kind}-{self.n}"

    def scope(self):
        return self.rng.choice(SCOPES)

    def boundary(self, now: int) -> int:
        """A nearby instant where some window or the anchor changes."""
        win = oracle_windows(now)
        moment = datetime.fromtimestamp(now, timezone.utc)
        month = datetime(moment.year + (moment.month == 12), moment.month % 12 + 1, 1, tzinfo=timezone.utc)
        choices = [win["daily"] + DAY, win["weekly"] + 7 * DAY, int(month.timestamp())]
        if self.anchor > now:
            choices.append(self.anchor)
        return self.rng.choice(choices) + self.rng.choice((-1, 0, 0, 1))

    def cost(self):
        r = self.rng.random()
        pool = R_COSTS if r < 0.55 else Q_COSTS if r < 0.88 else X_COSTS
        n, d = self.rng.choice(pool)
        if pool is R_COSTS and self.rng.random() < 0.3:
            n = str(self.rng.randint(0, 10**6))
        return n, d

    def usage_spec(self, *, late: int):
        n, d = self.cost()
        return {"n": n, "d": d, "unit": SECOND.code if self.rng.random() < 0.03 else MAIN.code,
                "outcome": "platform_fault" if self.rng.random() < 0.2 else "completed", "late": late, "at": self.w.now}

    def unit(self) -> str:
        return SECOND.code if self.rng.random() < 0.04 else MAIN.code

    def reserved(self) -> int:
        """Bounds 0, 1, 7 and 2**63-1 (rarer: one saturates every budget of its windows) or random."""
        if self.rng.random() < 0.05:
            return INT64
        return self.rng.choice((0, 1, 7, self.rng.randint(0, 1_000), self.rng.randint(0, 10**12)))

    def rows(self, sql, args=()):
        with self.w.store.conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # ---------------------------------------------------------------- generators
    def gen(self, kind):
        w, rng, now = self.w, self.rng, self.w.now
        if kind == "clock":
            target = self.boundary(now) if rng.random() < 0.4 else now + rng.choice(FORWARD + (rng.randint(1, 3 * DAY),))
            return {"op": "clock", "now": target}
        if kind == "clock_back":
            return {"op": "clock", "now": now - rng.choice((1, 60, 3_600, DAY, rng.randint(1, 3 * DAY)))}
        if kind == "admit_sql":
            env = rng.choice(ENVIRONMENTS)
            reuse = self.rows("SELECT c.id FROM billing_calls c WHERE c.status='reserved' AND c.run_key=? "
                              "ORDER BY c.id", (w.runs[env],))
            admitted_at = now - (0 if rng.random() < 0.8 else rng.randint(1, 600))
            op = {"op": "admit_sql", "env": env, "acct": rng.choice(ACCOUNT_REFS), "route": rng.choice(ROUTES),
                  "unit": self.unit(), "reserved": self.reserved(), "admitted_at": admitted_at,
                  "created_at": admitted_at, "attempt": self.key("attempt")}
            # I1 holds: admitted_at < expires_at <= admitted_at + 1 day (its lower bound: module docstring).
            op["expires_at"] = admitted_at + rng.choice((1, 2, 60, 120, 900, OPEN_S - 1, OPEN_S, rng.randint(1, OPEN_S)))
            if reuse and rng.random() < 0.25:
                op["call"] = rng.choice(reuse)["id"]
            else:
                created = admitted_at - rng.choice((0, 1, 5, 300))
                op["call"] = self.key("call")
                hold = rng.choice((1, 6, 60, 600, 3_600, DAY - 1, DAY, rng.randint(1, DAY)))
                if rng.random() < 0.03:            # beyond I7 (deadline <= created_at + 1 day): still exact
                    hold = rng.randint(DAY + 1, 3 * DAY)
                op["new_call"] = {"key": op["call"], "env": env, "owner": rng.randrange(4), "created_at": created,
                                  "deadline": created + hold,
                                  "status": "reserved" if rng.random() < 0.9 else rng.choice(("settled", "released"))}
            if rng.random() < 0.08:
                op["usage_first"] = self.usage_spec(late=0)
            return op
        if kind == "admit_api":
            acct = rng.choice(ACCOUNT_REFS)
            amount = rng.choice(BOUNDS + (rng.randint(0, 50), rng.randint(0, 10**9)))
            route = rng.choice(ROUTES)
            with w.store.tx() as t:
                facts = scope_facts(t, ("test", PROVIDER, acct))
                counters = oracle_counters(t, facts, MAIN, now)
            totals = counters.value["totals"] if counters.value else {}

            def budget(name):
                # Mostly unlimited; else exactly total + amount (admits), just below it, or zero.
                total = totals.get(name, Fraction(0)) + amount
                if rng.random() < 0.85:
                    return INT64
                options = [_ceil(total), total.numerator // total.denominator, _ceil(total) - 1, 0]
                return max(0, min(INT64, rng.choice(options)))

            unknown = counters.value["unknown_count"] if counters.value else 0
            inflight = counters.value["inflight"] if counters.value else 0
            unknown_units = counters.value["unknown_reserved_units"] if counters.value else 0
            spec = {"acct": acct, "daily": budget("daily"), "weekly": budget("weekly"), "monthly": budget("monthly"),
                    "unknown": max(0, min(INT64, rng.choice((INT64,) * 5 + (unknown_units + amount,
                                                                         unknown_units + amount - 1)))),
                    "max_unknown": max(1, min(CAP, rng.choice((CAP,) * 5 + (unknown + 1, unknown)))),
                    "max_inflight": max(1, min(CAP, rng.choice((CAP,) * 5 + (inflight + 1, inflight)))), "risk": None}
            if rng.random() < 0.6:
                spec["risk"] = self.risk_spec(("test", PROVIDER, acct), 0, route, now, amount)
            op = {"op": "admit_api", "alias": self.key("real"), "amount": amount, "route": route,
                  "expires_in": rng.choice((1, 60, 120, OPEN_S, rng.randint(1, OPEN_S))),
                  "hold_s": rng.choice((1, 30, 600, 3_600, OPEN_S, rng.randint(1, OPEN_S))),
                  "price": rng.choice(("fixture-cost-v1", Q_PRICE)), "policy": spec}
            retry = self.rows(
                "SELECT att.id FROM billing_attempts att JOIN billing_usage u ON u.attempt_key=att.id "
                "JOIN billing_calls c ON c.id=att.call_key WHERE att.owner='fixture-equivalence-worker' "
                "AND u.outcome='platform_fault' AND c.status='reserved' AND c.deadline>? AND c.cancel_requested=0 "
                "AND NOT EXISTS(SELECT 1 FROM billing_attempts later WHERE later.call_key=att.call_key "
                "AND later.rowid>att.rowid) ORDER BY att.id", (now,))
            if retry and rng.random() < 0.7:
                op["retry_of"] = w.reverse[rng.choice(retry)["id"]]
            return op
        if kind in ("usage_sql", "usage_api", "ending"):
            open_attempts = self.rows(
                "SELECT a.attempt_key,att.call_key,c.status,att.owner AS writer,e.attempt_key AS ending,"
                "u.attempt_key AS usage FROM model_cost_admissions a JOIN billing_attempts att ON att.id=a.attempt_key "
                "JOIN billing_calls c ON c.id=att.call_key LEFT JOIN model_cost_endings e ON e.attempt_key=a.attempt_key "
                "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key ORDER BY a.rowid")
            real = [r for r in open_attempts if r["writer"] == "fixture-equivalence-worker" and r["usage"] is None]
            sql = [r for r in open_attempts if r["writer"] != "fixture-equivalence-worker" and r["usage"] is None]
            if kind == "usage_api" and real:
                r = rng.choice(real)
                return {"op": "usage_api", "alias": w.reverse[r["attempt_key"]],
                        "tokens": (rng.randint(0, 10**6), rng.randint(0, 10**6), rng.randint(0, 10**5), 0),
                        "outcome": "platform_fault" if rng.random() < 0.35 else "completed"}
            if kind == "ending":
                candidates = [r for r in open_attempts
                              if r["ending"] is None and (r["usage"] is None or rng.random() < 0.05)]
                if not candidates:
                    return None
                r = rng.choice(candidates)
                op = {"op": "ending", "via": "api" if rng.random() < 0.5 else "sql"}
                if r["attempt_key"] in w.reverse:
                    op["alias"] = w.reverse[r["attempt_key"]]
                else:
                    op["attempt"] = r["attempt_key"]
                return op
            if not sql:
                return None
            r = rng.choice(sql)
            late = int(r["status"] != "reserved")
            usage = self.usage_spec(late=late)
            settle = r["call_key"] if (not late and usage["outcome"] == "completed") else None
            return {"op": "usage_sql", "attempt": r["attempt_key"], "usage": usage, "settle_call": settle}
        if kind in ("cancel", "release"):
            calls = self.rows("SELECT id,run_key FROM billing_calls WHERE status='reserved' AND run_key IN "
                              "(SELECT id FROM billing_runs WHERE namespace IN (?,?)) ORDER BY id",
                              (SQL_NAMESPACE, API_NAMESPACE))
            if not calls:
                return None
            c = rng.choice(calls)
            real = {v["call"]: k for k, v in w.aliases.items()}
            if kind == "cancel":
                return {"op": "cancel", "alias": real[c["id"]]} if c["id"] in real else {"op": "cancel", "call": c["id"]}
            if c["id"] in real:
                return None
            return {"op": "release", "call": c["id"], "status": "released" if rng.random() < 0.8 else "settled"}
        if kind == "expire":
            return {"op": "expire"}
        if kind == "bulk":
            env, count = rng.choice(ENVIRONMENTS), rng.choice((5, 20, 60, 150))
            spread = rng.choice((60, 3_600, DAY, 7 * DAY, 40 * DAY))
            base = now - spread
            times = sorted(rng.randint(base, now) for _ in range(count))
            if rng.random() < 0.3:     # clustered on a window boundary
                b = oracle_windows(now)[rng.choice(("daily", "weekly", "monthly"))]
                times = sorted(min(now, b + rng.randint(-3, 3)) for _ in range(count))
            status = rng.choice(("reserved", "reserved", "settled", "released"))
            usage = None
            if rng.random() < 0.6:
                usage = self.usage_spec(late=int(status != "reserved"))
            return {"op": "bulk", "prefix": self.key("bulk"), "env": env, "acct": rng.choice(ACCOUNT_REFS),
                    "owner": rng.randrange(4), "route": rng.choice(ROUTES), "unit": self.unit(),
                    "reserved": self.reserved(), "times": times, "hold": rng.choice((1, 600, DAY)),
                    "expires_after": rng.choice((1, 120, OPEN_S)), "call_status": status, "usage": usage,
                    "usage_first": rng.random() < 0.3, "stop_at": now if (usage is None and rng.random() < 0.3) else None}
        if kind == "reopen":
            damage = rng.choice((None, None, "marker", "drop:model_cost_buckets", "drop:model_cost_hold",
                                 "drop:model_cost_inflight", "drop:model_cost_exceptions", "empty:model_cost_buckets",
                                 "empty:model_cost_hold"))
            return {"op": "reopen", "damage": damage}
        if kind == "rebuild":
            healthy = [s for s in SCOPES if s not in self.faulted]
            return {"op": "rebuild", "scope": rng.choice(healthy)} if healthy else None
        if kind in ("prune", "verify", "expire"):
            return {"op": kind}
        raise AssertionError(kind)

    def gen_fault(self):
        scope = self.scope()
        admitted_at = self.w.now
        kind = self.rng.choice(("orphan", "text_time", "negative_time", "long_expiry", "future_orphan",
                                "future_long_expiry"))
        op = {"op": "fault", "fault": kind, "env": scope[0], "acct": scope[2], "reserved": 7,
              "attempt": self.key("fault"), "route": ROUTES[0], "unit": MAIN.code, "created_at": admitted_at,
              "admitted_at": admitted_at, "expires_at": admitted_at + 60}
        if kind == "text_time":
            op["admitted_at"] = "not-a-time"
        elif kind == "negative_time":
            op["admitted_at"], op["expires_at"] = -5, 10
        elif kind == "long_expiry":
            op["expires_at"] = admitted_at + OPEN_S + 1
        elif kind in ("future_orphan", "future_long_expiry"):   # a broken import's date days ahead
            op["admitted_at"] = admitted_at + self.rng.choice((3 * DAY, 10 * DAY, 400 * DAY))
            op["expires_at"] = op["admitted_at"] + (60 if kind == "future_orphan" else OPEN_S + 1)
        if kind not in ("orphan", "future_orphan"):
            op["call"] = self.key("call")
            op["new_call"] = {"key": op["call"], "env": scope[0], "owner": 0, "created_at": admitted_at,
                              "deadline": admitted_at + 600, "status": "reserved"}
        self.faulted.add(scope)
        return op

    def risk_spec(self, scope, owner: int, route: str, now: int, amount: int):
        """Windows from 60 s to a day, thresholds at the oracle's total + amount, or one below."""
        rng = self.rng
        with self.w.store.tx() as t:
            facts = scope_facts(t, scope)
        spec = {}
        for side in ("payer", "route"):
            window = rng.choice(WINDOW_CHOICES + (rng.randint(60, 86_400),))
            mine = [f for f in facts if type(f["admitted_at"]) is int and f["admitted_at"] <= now]
            r = rng.random()
            if mine and r < 0.35:                # put the window edge exactly on an admission
                f = rng.choice(mine)
                window = min(86_400, max(60, now - f["admitted_at"] + rng.choice((0, 1))))
            elif r < 0.5:                        # window start (now - w + 1) on a minute or hour edge, or next to one
                span = rng.choice((60, 3_600))
                window = (now + 1) % span + span * rng.randint(1, 86_400 // span - 1) + rng.choice((0, 0, -1, 1))
                window = min(86_400, max(60, window))
            spec[side + "_s"] = window
            spec[side + "_cost"] = None if rng.random() < 0.25 else 0
        risk = make_risk(spec)
        member = self.w.owners[owner]
        vel = oracle_velocity(facts, MAIN, member, route, now, risk) if now >= (newest(facts) or now) else None
        for side in ("payer", "route"):
            if spec[side + "_cost"] is None:
                continue
            total = vel.value[side] + amount if vel is not None and vel.value is not None else Fraction(amount)
            spec[side + "_cost"] = max(0, min(INT64, rng.choice((INT64, _ceil(total), _ceil(total) - 1,
                                                                 total.numerator // total.denominator))))
        return spec

    # ---------------------------------------------------------------- running
    def apply(self, op):
        self.trace.append(op)
        run_op(self.w, op)

    def step(self, index: int):
        if self.fault_step == index:
            op = self.gen_fault()
        else:
            op = None
            while op is None:
                kind = self.rng.choices(list(self.weights), weights=list(self.weights.values()))[0]
                op = self.gen(kind)
        self.apply(op)
        return op

    def prelude(self):
        """A short initial history so the first checks already see open, settled and old facts."""
        for _ in range(self.rng.choice((1, 2, 3))):
            self.apply(self.gen("bulk"))
        for _ in range(self.rng.randint(2, 6)):
            self.apply(self.gen("admit_sql" if self.rng.random() < 0.7 else "admit_api"))

    def touched(self, op) -> list:
        if op.get("env") and op.get("acct"):
            return [(op["env"], PROVIDER, op["acct"])]
        if op["op"] == "admit_api":
            return [("test", PROVIDER, op["policy"]["acct"])]
        if op["op"] == "rebuild":
            return [tuple(op["scope"])]
        alias = self.w.aliases.get(op.get("alias"))
        key = alias["ticket"].attempt_key if alias else op.get("attempt")
        if key:
            row = self.rows("SELECT environment,provider,account_ref FROM model_cost_admissions WHERE attempt_key=?",
                            (key,))
            if row:
                return [(row[0]["environment"], row[0]["provider"], row[0]["account_ref"])]
        return [self.scope()]

    def check(self, index: int, op):
        w, rng = self.w, self.rng
        check_verify(w)
        scopes = self.touched(op)
        if index % 3 == 1:          # the risk monitor's daily verify (from the previous month) ...
            check_verify(w, environment=scopes[0][0], mode="daily")
        elif index % 3 == 2:        # ... and its hourly two-day one
            check_verify(w, environment=scopes[0][0], since=w.now - 2 * DAY, mode="recent")
        if index % 3 == 0:
            scopes = list(SCOPES)
        for scope in scopes[:1] if index % 3 else scopes:
            check_rebuild(w, scope, scope in self.faulted)
        for scope in scopes:
            with w.store.tx() as t:
                facts = scope_facts(t, scope)
                for now in self.instants(facts):
                    unit = rng.choices((MAIN, SECOND, WRONG_SCALE, UNPUBLISHED), weights=(82, 10, 4, 4))[0]
                    spec = {"check": "counters", "scope": scope, "now": now, "unit": [unit.code, unit.scale],
                            "records": rng.random() < 0.5}
                    expect = read_counters(w, t, facts, spec)
                    M = newest(facts)
                    if (scope in self.faulted or "cost_rollup_inconsistent" in expect.codes or unit is WRONG_SCALE
                            or (M is not None and now < M)):
                        continue
                    owner, route = rng.randrange(4), rng.choice(ROUTES)
                    vspec = {"check": "velocity", "scope": scope, "now": now, "unit": [unit.code, unit.scale],
                             "owner": owner, "route": route, "risk": self.risk_spec(scope, owner, route, now, 0)}
                    if rng.random() < 0.4:
                        vspec["amount"] = rng.choice(BOUNDS + (rng.randint(0, 100),))
                    read_velocity(w, t, facts, vspec)

    def instants(self, facts) -> list:
        """Six instants: boundaries ±1 s at or after the newest admission (where a window starts, an
        expiry, a hold deadline or the one-day open limit passes), plus random ones; now and then
        one before it (a clock that stepped back)."""
        rng, now = self.rng, self.w.now
        M = newest(facts)
        base = max(now, M) if M is not None else now
        pool = {base, base + 1}
        for start in oracle_windows(base).values():
            pool |= {start - 1, start}
        for _ in range(2):
            b = self.boundary(base)
            pool |= {b - 1, b, b + 1}
        live = [f for f in facts if not broken(f) and f["usage"] is None]
        for f in rng.sample(live, min(4, len(live))):
            pool |= {f["expires_at"] - 1, f["expires_at"], f["admitted_at"] + OPEN_S - 1, f["admitted_at"] + OPEN_S}
            if f["call_deadline"] is not None:
                pool |= {f["call_deadline"] - 1, f["call_deadline"]}
        pool = sorted(x for x in pool if M is None or x >= M)
        chosen = rng.sample(pool, min(4, len(pool)))
        chosen.append(base + rng.choice((rng.randint(0, 2 * DAY), rng.randint(0, 3_600))))
        chosen.append(rng.choice((now, M - 1)) if M is not None and rng.random() < 0.3 else base + rng.randint(0, DAY))
        return chosen

    def run(self):
        self.prelude()
        self.check(0, {"op": "prelude"})
        for index in range(1, self.steps + 1):
            op = self.step(index)
            self.check(index, op)


# ================================================================ minimisation
def replay(root: Path, trace: list, spec: dict) -> Mismatch | None:
    """Run ``trace`` on a fresh Store and then only the recorded failing check."""
    import shutil
    shutil.rmtree(root, ignore_errors=True)
    w = make_world(root, trace[0]["now"])
    try:
        for op in trace:
            if spec.get("check") == "admit" and op.get("alias") == spec.get("alias") and op["op"] == "admit_api":
                run_op(w, op, checking=True)
                continue
            if op["op"] == "verify":
                run_op(w, op, checking=False)
                continue
            run_op(w, op, checking=False)
        if spec.get("check") != "admit":
            run_check(w, spec)
    except Mismatch as exc:
        return exc
    except Exception:  # noqa: BLE001 - a different failure is not a reproduction
        return None
    return None


def minimize(root: Path, trace: list, failure: Mismatch, *, budget: int = 400) -> list:
    """Delta debugging over the operation trace (the first clock op is always kept)."""
    head, ops = trace[:1], trace[1:]
    tries = [0]

    def fails(candidate):
        tries[0] += 1
        if tries[0] > budget:
            return False
        got = replay(root / f"replay-{tries[0] % 2}", head + candidate, failure.spec)
        return got is not None and got.signature == failure.signature

    if not fails(ops):
        return trace
    n = 2
    while len(ops) >= 2 and tries[0] < budget:
        chunk = max(1, len(ops) // n)
        reduced = False
        for start in range(0, len(ops), chunk):
            candidate = ops[:start] + ops[start + chunk:]
            if candidate and fails(candidate):
                ops, n, reduced = candidate, max(n - 1, 2), True
                break
        if not reduced:
            if chunk == 1:
                break
            n = min(len(ops), n * 2)
    for index, op in enumerate(ops):          # then halve the bulk writes that are left
        while op["op"] == "bulk" and len(op["times"]) > 1 and tries[0] < budget:
            for half in (op["times"][: len(op["times"]) // 2], op["times"][len(op["times"]) // 2:]):
                candidate = ops[:index] + [{**op, "times": half}] + ops[index + 1:]
                if fails(candidate):
                    ops, op = candidate, candidate[index]
                    break
            else:
                break
    return head + ops


def run_history(root: Path, seed: int, steps: int = STEPS, **options):
    history = History(root, seed, steps=steps, **options)
    try:
        history.run()
    except Mismatch as failure:
        minimal = minimize(root / "minimize", history.trace, failure)
        pytest.fail(f"seed {seed}, step {len(history.trace) - 1}: {failure}\n"
                    f"minimal trace ({len(minimal)} ops): {json.dumps(minimal, default=str)}\n"
                    f"failing check: {json.dumps(failure.spec, default=str)}", pytrace=False)
    except Exception as error:  # an operation itself failed (a fact write refused, a crash): no minimisation
        pytest.fail(f"seed {seed}, after {len(history.trace)} ops: {type(error).__name__}: {error}\n"
                    f"last op: {json.dumps(history.trace[-1], default=str)[:2000]}")
    return history


# ================================================================ tests
@pytest.mark.parametrize("seed", SEEDS)
def test_rollup_equals_oracle_and_legacy_on_random_histories(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    run_history(tmp_path, seed)


@pytest.mark.parametrize("seed", range(1, FAULT_SEEDS + 1))
def test_a_broken_admission_keeps_its_scope_refused_on_random_histories(seed, tmp_path, monkeypatch):
    """Every history has a broken admission in its first half (a third of them dated days ahead, so
    also the scope's newest admission); prunes, clock steps, rebuilds and reopens are more frequent.
    Fail closed: from the fault on, every read of that scope refuses cost_rollup_inconsistent and every
    rebuild of it refuses, to the end of the history (no operation clears it: only restoring a backup
    from before the write does), while the other scopes still equal the oracle."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    history = run_history(tmp_path, 10_000 + seed, fault_rate=1.0, fault_by=STEPS // 2,
                          weights={**WEIGHTS, "prune": 10, "clock": 20, "rebuild": 6, "reopen": 4})
    assert history.faulted
    with history.w.store.tx() as t:
        for scope in history.faulted:
            assert t.execute("SELECT 1 FROM model_cost_rollup_faults WHERE environment=? AND provider=? AND account_ref=? "
                             "AND resolved_at IS NULL", scope).fetchone(), scope
            with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
                rollup_counters(t, environment=scope[0], provider=scope[1], account_ref=scope[2], unit=MAIN,
                                now=history.w.now)
    STATS["fault:histories"] += 1


def test_12000_records_legacy_refuses_on_its_cap_and_the_rollup_still_equals_the_oracle(tmp_path, monkeypatch):
    """12,000 admissions in the month, 10,500 of them on one route within the last day: the 5db93f86
    counters and velocity scan refuse on their caps; the rollup's counters, velocity, guard and a real
    admission still equal the oracle (and the product's reference), and the rollup verifies."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    now = utc("2036-02-28T12:00:00")
    w = make_world(tmp_path / "store", now)
    scope = ("test", PROVIDER, ACCOUNT_REFS[0])
    rng = random.Random(12_000)

    def bulk(prefix, count, low, high, *, owner, route, usage=None, expires_after=120, hold=600, status="settled"):
        run_op(w, {"op": "bulk", "prefix": prefix, "env": "test", "acct": scope[2], "owner": owner, "route": route,
                   "unit": MAIN.code, "reserved": 7, "times": sorted(rng.randint(low, high) for _ in range(count)),
                   "hold": hold, "expires_after": expires_after, "call_status": status, "usage": usage,
                   "usage_first": False, "stop_at": None})

    settled = {"outcome": "completed", "unit": MAIN.code, "late": 0, "at": now}
    bulk("fx-cap-r", 6_000, now - 86_000, now, owner=0, route=ROUTES[0], usage={**settled, "n": "7", "d": "8"})
    bulk("fx-cap-q", 4_500, now - 86_000, now, owner=1, route=ROUTES[0], usage={**settled, "n": "1", "d": "3"})
    bulk("fx-cap-closed", 1_000, utc("2036-02-01T00:00:00"), now - 2 * DAY, owner=2, route=ROUTES[1],
         status="reserved")
    bulk("fx-cap-open", 400, now - 3_600, now, owner=0, route=ROUTES[1], expires_after=OPEN_S, hold=DAY,
         status="reserved")
    bulk("fx-cap-x", 100, now - 7 * DAY, now, owner=3, route=ROUTES[1], usage={**settled, "n": "007", "d": "1"})
    check_verify(w)
    check_rebuild(w, scope, False)
    risk = {"payer_s": 86_400, "payer_cost": INT64, "route_s": 86_400, "route_cost": INT64}
    with w.store.tx() as t:
        facts = scope_facts(t, scope)
        assert len(facts) == 12_000
        for instant in (now, now + 1, utc("2036-02-29T00:00:00") - 1, utc("2036-02-29T00:00:00"), now + 3_600):
            expect = oracle_counters(t, facts, MAIN, instant)
            assert not expect.codes and expect.candidates > CAP, expect.codes
            legacy_policy = CostPolicy("fixture-legacy", scope[2], PROVIDER, MAIN, True, 0, 0, 0, 0, 1, 1)
            assert outcome(lambda p=legacy_policy, at=instant: LegacyCostCounters("test")._counters(t, p, at)) == (
                "refused", "cost_accounting_capacity")
            for records in (True, False):        # rollup == oracle, reference == oracle (legacy: capacity)
                read_counters(w, t, facts, {"check": "counters", "scope": scope, "now": instant,
                                            "unit": [MAIN.code, MAIN.scale], "records": records})
            for owner, route in ((0, ROUTES[0]), (3, ROUTES[0]), (2, ROUTES[1])):
                spec = {"check": "velocity", "scope": scope, "now": instant, "unit": [MAIN.code, MAIN.scale],
                        "owner": owner, "route": route, "risk": risk, "amount": 7}
                velocity = oracle_velocity(facts, MAIN, w.owners[owner], route, instant, make_risk(risk))
                read_velocity(w, t, facts, spec)
                if velocity.candidates > CAP:
                    policy = CostPolicy("fixture-legacy", scope[2], PROVIDER, MAIN, True, 0, 0, 0, 0, 1, 1,
                                        risk=make_risk(risk))
                    call = {"context": w.context(owner), "account_id": w.wallets[("test", owner)]}
                    assert outcome(lambda p=policy, c=call, r=route, at=instant: LegacyRiskControls(
                        w.store, environment="test").guard(t, policy=p, call=c, attempt={"requested_model": r},
                                                           amount=7, now=at)) == (
                        "refused", "risk_accounting_capacity")
    w.clock[0] = now + 1
    policy = {"acct": scope[2], "daily": INT64, "weekly": INT64, "monthly": INT64, "unknown": INT64,
              "max_unknown": CAP, "max_inflight": CAP, "risk": risk}
    assert run_op(w, {"op": "admit_api", "alias": "fx-cap-real", "amount": 7, "route": ROUTES[0], "expires_in": 120,
                      "hold_s": 600, "price": "fixture-cost-v1", "policy": policy}) == {"admitted": True}
    check_verify(w)
    check_rebuild(w, scope, False)


#: Cost encodings no Ledger writes but a bulk writer or an operator could: BLOBs (bytes), text
#: that only Python's int() reads, and text with an embedded NUL (SQLite's GLOB, length() and CAST
#: stop at it; Python's parsers do not). Kept out of the random traces, which are JSON for replay.
ODD_COSTS = ((b"7", b"1"), (b"007", b"8"), (b"-5", b"1"), (b"", b"1"), ("-0", "1"), ("0000000000000001", "1"),
             ("1", "0000000000000008"), ("7", "-0"), (str(INT64), "1000000000000000"), ("18446744073709551616", "2"),
             ("1e3", "1"), ("\u0663", "\u0668"), ("\uff17", "1"), ("1\n", "1"), ("1", " 8"),
             ("\x0099999999", "1"), ("7\x00junk", "1"), ("5", "1\x00"), ("123", "3\x007"), ("7\x00", "4"),
             ("5\x00x", "1"), ("12", "3\x00junk"))


@pytest.mark.parametrize("cost", ODD_COSTS, ids=[repr(c)[:40] for c in ODD_COSTS])
def test_odd_cost_encodings_agree_across_rollup_oracle_and_legacy(cost, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    at = ANCHORS[2] + 600
    w = make_world(tmp_path / "store", at)
    scope = ("test", PROVIDER, ACCOUNT_REFS[1])
    run_op(w, {"op": "admit_sql", "env": "test", "acct": scope[2], "route": ROUTES[0], "unit": MAIN.code,
               "reserved": 5, "admitted_at": at, "created_at": at, "attempt": "fx-attempt-1", "expires_at": at + 60,
               "call": "fx-call-1", "new_call": {"key": "fx-call-1", "env": "test", "owner": 0, "created_at": at,
                                                 "deadline": at + 600, "status": "reserved"}})
    run_op(w, {"op": "usage_sql", "attempt": "fx-attempt-1", "settle_call": "fx-call-1",
               "usage": {"n": cost[0], "d": cost[1], "unit": MAIN.code, "outcome": "completed", "late": 0, "at": at}})
    check_verify(w)
    check_rebuild(w, scope, False)
    risk = {"payer_s": 3_600, "payer_cost": 10**12, "route_s": 86_400, "route_cost": 10**12}
    with w.store.tx() as t:
        facts = scope_facts(t, scope)
        for now in (at, at + 1, at + DAY):
            read_counters(w, t, facts, {"check": "counters", "scope": scope, "now": now,
                                        "unit": [MAIN.code, MAIN.scale], "records": True})
            read_velocity(w, t, facts, {"check": "velocity", "scope": scope, "now": now,
                                        "unit": [MAIN.code, MAIN.scale], "owner": 0, "route": ROUTES[0],
                                        "risk": risk, "amount": 3})


# ================================================================ disagreements found (regressions)
def test_an_admission_expiring_when_it_is_admitted_is_a_fault_or_verifies_clean(tmp_path, monkeypatch):
    """Found by this test (seed 13 before the generator excluded it): an admission with expires_at <=
    admitted_at violates I1, the producer does not count it as malformed (contract §5 bounds expires_at
    only from above) and CostRollup.check reported it (fact_i1), so a verify latched the scope and a
    rebuild then refused. Such an admission is never in flight for the rollup nor for the oracle, which
    agree on it; the verifier no longer reports it, so producer and verifier agree."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    at = ANCHORS[2] - 60
    w = make_world(tmp_path / "store", at)
    run_op(w, {"op": "admit_sql", "env": "test", "acct": ACCOUNT_REFS[1], "route": ROUTES[0], "unit": MAIN.code,
               "reserved": 871, "admitted_at": at, "created_at": at, "attempt": "fx-attempt-1", "expires_at": at,
               "call": "fx-call-1", "new_call": {"key": "fx-call-1", "env": "test", "owner": 0, "created_at": at,
                                                 "deadline": at + 600, "status": "reserved"}})
    report = CostRollup.check(w.store, all=True, now=at)
    faults = w.store.one("SELECT COUNT(*) AS n FROM model_cost_rollup_faults WHERE resolved_at IS NULL")["n"]
    assert report.ok or faults, report.summary()


# ================================================================ phase 2: the per-wallet spend rollup
# Three implementations of PayerBudgets._exposure on seeded wallet histories (cost-capacity phase 2,
# binding addendum §8): legacy (the verbatim 0bcce176 copy, cap included), the oracle below (plain Python
# over every call of the wallet, from the addendum's semantics: held = every reserved call's hold; spent[p]
# = the journal amounts of the settled calls created at or after p's start, reached through their
# settlements; refusals in the addendum's order: a malformed settled call (the wallet's fault), more than
# 10,000 holds, a settled call in the periods without its money facts), and the rollup
# (spend_rollup.exposure, and through it Ledger.reserve_call / dispatch). The product's uncapped
# reference_exposure must agree with the oracle too.
WALLET_SEEDS = list(range(1, int(os.environ.get("SYNWEVO_COST_EQUIVALENCE_SEEDS") or 100) + 1))
#: Histories of test_a_malformed_settled_call_keeps_its_wallet_refused_on_random_histories.
WALLET_FAULT_SEEDS = max(1, FAULT_SEEDS // 3)
SPEND_CAP = 10_000
#: A: the test wallet that also takes real Ledger reservations (small holds: its balance covers them);
#: B: another test wallet and L: a live one, both written with SQL only (holds up to 2^62).
WALLET_ENVS = {"A": "test", "B": "test", "L": "live"}
SPEND_HOLDS = {"A": (0, 1, 7), "B": (0, 1, 2**62), "L": (0, 1, 2**62)}
SPEND_AMOUNTS = (0, 1, 7, 2**62, INT64)
SPEND_ORDERS = ("product", "status_first", "inserted_settled", "journal_after_settlement")
MALFORMED_TIMES = ("soon", 1.5, -5)
SPEND_NAMESPACE = "fixture-spend-equivalence"
SPEND_WEIGHTS = {"reserve": 14, "settle": 22, "unpaid": 4, "complete": 8, "release": 6, "expire": 3, "clock": 6,
                 "bulk": 4, "reopen": 3, "rebuild": 4, "real": 10}


def spend_periods(at: int) -> dict:
    """The UTC day, ISO week (from Monday) and month containing ``at``: {name: (start, exclusive end)}."""
    moment = datetime.fromtimestamp(at, timezone.utc)
    day = datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)
    week = day - timedelta(days=moment.weekday())
    month = datetime(moment.year, moment.month, 1, tzinfo=timezone.utc)
    following = datetime(moment.year + moment.month // 12, moment.month % 12 + 1, 1, tzinfo=timezone.utc)
    stamp = lambda d: int(d.timestamp())  # noqa: E731
    return {"daily": (stamp(day), stamp(day) + DAY), "weekly": (stamp(week), stamp(week) + 7 * DAY),
            "monthly": (stamp(month), stamp(following))}


#: Every call of a wallet with the money facts its settlement reached.
WALLET_FACTS = ("SELECT c.id,c.status,c.hold_units AS hold,c.created_at,j.amount,j.id IS NOT NULL AS paid "
                "FROM billing_calls c LEFT JOIN billing_settlements s ON s.call_key=c.id "
                "LEFT JOIN billing_journal j ON j.id=s.journal_id WHERE c.account_id=?")


def wallet_facts(t, account) -> list[dict]:
    return [dict(row) for row in t.execute(WALLET_FACTS, (account,)).fetchall()]


def malformed_call(f) -> bool:
    return f["status"] == "settled" and (type(f["created_at"]) is not int or f["created_at"] < 0)


def oracle_exposure(facts, at: int) -> Expect:
    """Every check evaluated, none short-circuited; ``value`` is the uncapped answer (for the reference when
    only the holds' bound applies); ``candidates`` what the 0bcce176 read counted against its cap."""
    win = spend_periods(at)
    E = min(start for start, _ in win.values())
    codes = []
    if any(malformed_call(f) for f in facts):
        codes.append("payer_budget_inconsistent")                          # 1. the wallet's fault
    reserved = [f for f in facts if f["status"] == "reserved"]
    if len(reserved) > SPEND_CAP:
        codes.append("payer_budget_capacity")                              # 3. holds
    settled = [f for f in facts if f["status"] == "settled" and not malformed_call(f)]
    if any(not f["paid"] and f["created_at"] >= E for f in settled):
        codes.append("payer_budget_inconsistent")                          # 4. unpaid in the periods
    held = sum(f["hold"] for f in reserved)
    value = {name: {"start": start, "end": end, "held": held,
                    "spent": sum(f["amount"] for f in settled if f["paid"] and f["created_at"] >= start)}
             for name, (start, end) in win.items()}
    candidates = len(reserved) + sum(1 for f in settled if f["created_at"] >= E)
    STATS["spend:unpaid_before_E"] += any(not f["paid"] and f["created_at"] < E for f in settled)
    STATS["spend:future"] += any(f["created_at"] > at for f in settled)
    STATS["spend:big"] += any(f["paid"] and f["amount"] >= 2**62 for f in settled) or held >= 2**62
    return Expect(_unique(codes), value, candidates)


def compare_exposure(spec, expect: Expect, rollup, reference, legacy):
    """rollup == oracle (its first code when refusing); reference == oracle (it has no cap); legacy == both
    within its cap (it refuses payer_budget_capacity beyond it)."""
    def fail(who, got, what):
        raise Mismatch(f"exposure:{who}:{what}", {"spec": spec, "expected": {"codes": expect.codes,
                       "value": expect.value, "candidates": expect.candidates}, who: got}, spec)

    STATS["exposure:" + ("ok" if not expect.codes else "+".join(expect.codes))] += 1
    STATS["exposure:legacy_compared"] += legacy is not None and expect.candidates <= SPEND_CAP
    STATS["exposure:legacy_capped"] += legacy is not None and expect.candidates > SPEND_CAP
    if not expect.codes:
        if rollup != ("ok", expect.value):
            fail("rollup", rollup, "value")
        if reference != ("ok", expect.value):
            fail("reference", reference, "value")
    else:
        if rollup[:2] != ("refused", expect.codes[0]):
            fail("rollup", rollup, "code")
        others = [code for code in expect.codes if code != "payer_budget_capacity"]
        if not others and reference != ("ok", expect.value):
            fail("reference", reference, "value")
        if others and reference[:2] != ("refused", others[0]):
            fail("reference", reference, "code")
    if legacy is not None:
        if expect.candidates > SPEND_CAP:
            if legacy[:2] != ("refused", "payer_budget_capacity"):
                fail("legacy", legacy, "capacity")
        elif not expect.codes and legacy != ("ok", expect.value):
            fail("legacy", legacy, "value")
        elif expect.codes and (legacy[0] != "refused" or legacy[1] not in expect.codes):
            fail("legacy", legacy, "code")


@dataclass
class WalletWorld:
    root: Path
    store: Store
    users: list
    clock: list
    wallets: dict                   # "A" / "B" / "L" -> billing account id
    template: dict                  # the real settled call cloned from: call, attempt, journal ids
    ledger: Ledger | None = None
    pair: tuple = ()                # the shared fixture (debit, credit) accounts of small SQL journal entries
    malformed: set = field(default_factory=set)
    revision: int = 0
    n: int = 0

    @property
    def now(self) -> int:
        return self.clock[0]

    def new_ledger(self) -> Ledger:
        ledger = Ledger(self.store, environment="test", namespace=SPEND_NAMESPACE, authorize=fixture_authorize,
                        verify_payment=fixture_verify, clock=lambda: self.clock[0], max_hold_seconds=86_400)
        ledger.payer_budgets = PayerBudgets(ledger)
        return ledger

    def key(self, kind: str) -> str:
        self.n += 1
        return f"fx-spend-{kind}-{self.n}"


def _copy(t, table, key_column, key, changes: dict) -> None:
    """FIXTURE: one row of ``table`` copied from the row ``key_column=key`` with ``changes`` (plain SQL)."""
    cols = [r[1] for r in t.execute(f"PRAGMA table_info({table})")]
    exprs, args = [], []
    for col in cols:
        if col in changes:
            exprs.append("?")
            args.append(changes[col])
        else:
            exprs.append(col)
    t.execute(f"INSERT INTO {table}({','.join(cols)}) SELECT {','.join(exprs)} FROM {table} WHERE {key_column}=?",
              (*args, key))


def _set_budgets(w: WalletWorld, budgets: dict) -> None:
    with w.store.tx():
        w.ledger.payer_budgets.set(w.users[0], unit_code=UNIT.code, expected_revision=w.revision, enabled=True,
                                   daily_budget=budgets["daily"], weekly_budget=budgets["weekly"],
                                   monthly_budget=budgets["monthly"])
    w.revision += 1


def make_wallet_world(root: Path, start: int) -> WalletWorld:
    """FIXTURE: an isolated Store with three users; wallet A (user 0, test) funded, with enabled limits and one
    real settled call to clone from; wallet B (user 1, test) and wallet L (user 2, live) for SQL facts; a
    shared pair of platform accounts that small SQL journal entries move between."""
    store = Store(root)
    with store.conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS fixture_payment_inbox(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);"
                        "CREATE TABLE IF NOT EXISTS fixture_billing_authority(payer TEXT,actor TEXT,authority TEXT);")
    identity = Identity(store)
    users = [identity.create_user(f"spend-equivalence-{i}@example.test", None, status="active",
                                  with_personal_workspace=False)["id"] for i in range(3)]
    with store.tx() as t:
        t.executemany("INSERT INTO fixture_billing_authority VALUES (?,?,?)", [(u, u, "fixture-approval") for u in users])
    clock = [start - DAY]
    w = WalletWorld(root, store, users, clock, {}, {})
    w.ledger = w.new_ledger()
    w.ledger.publish_price(sale_price())
    w.ledger.publish_price(cost_price())
    fund((store, w.ledger, users, clock), 10**12, order="fixture-spend-equivalence", ledger=w.ledger)
    _set_budgets(w, {"daily": INT64, "weekly": INT64, "monthly": INT64})
    ctx = ChargeContext(users[0], users[0], "fx-spend-template", "fixture-approval", "workspace-A")
    w.ledger.open_run(ctx, unit=UNIT, budget=10**12, max_calls=10_000, max_attempts=10_000, deadline=w.now + 900)
    call = w.ledger.reserve_call(ctx, logical_call_id="template", amount=1, price_id="fixture-sale-v1",
                                 deadline=w.now + 500)
    ticket = w.ledger.prepare_attempt(call, attempt_id="template", owner="fixture-spend", cost_price_id="fixture-cost-v1",
                                      requested_model="fixture-route-a")
    w.ledger.dispatch(ticket)
    w.ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=Usage(1, 0, 0)))
    journal = store.one("SELECT journal_id FROM billing_settlements WHERE call_key=?", (call,))["journal_id"]
    w.template = {"call": call, "attempt": ticket.attempt_key, "journal": journal}
    with store.tx() as t:
        w.wallets["A"] = t.execute("SELECT account_id FROM billing_calls WHERE id=?", (call,)).fetchone()[0]
        w.wallets["B"] = w.ledger._account(t, UNIT.code, users[1])
        w.wallets["L"] = "fixture-spend-wallet-live"
        t.execute("INSERT INTO billing_accounts(id,environment,unit,owner,kind) VALUES (?,'live',?,?,'wallet')",
                  (w.wallets["L"], UNIT.code, users[2]))
        t.execute("INSERT INTO billing_balances(account_id,amount) VALUES (?,0)", (w.wallets["L"],))
        w.pair = (w.ledger._account(t, UNIT.code, "fixture-spend-source", "paid"),
                  w.ledger._account(t, UNIT.code, "fixture-spend-sink", "revenue"))
    clock[0] = start
    return w


# ---------------------------------------------------------------- operations (concrete, replayable)
def _spend_call(t, w, key, wallet, created_at, status, hold, deadline):
    _copy(t, "billing_calls", "id", w.template["call"], {
        "id": key, "logical_call_id": key, "account_id": w.wallets[wallet], "created_at": created_at,
        "status": status, "hold_units": hold, "deadline": deadline})


def _spend_money(t, w, call, amount, steps, pair):
    """The attempt and usage a settlement names (once), then ``steps`` of: journal, settlement, status."""
    attempt = call + "-attempt"
    journal = call + "-journal"
    if not t.execute("SELECT 1 FROM billing_attempts WHERE id=?", (attempt,)).fetchone():
        _copy(t, "billing_attempts", "id", w.template["attempt"], {
            "id": attempt, "call_key": call, "attempt_id": attempt, "fence": attempt + "-fence", "request_id": attempt})
        _copy(t, "billing_usage", "attempt_key", w.template["attempt"], {"attempt_key": attempt})
    for step in steps:
        if step == "journal":
            _copy(t, "billing_journal", "id", w.template["journal"], {
                "id": journal, "voucher": journal, "reference": call, "amount": amount,
                "debit_account": pair[0], "credit_account": pair[1]})
        elif step == "settlement":
            _copy(t, "billing_settlements", "call_key", w.template["call"], {
                "call_key": call, "attempt_key": attempt, "journal_id": journal})
        elif step == "status":
            t.execute("UPDATE billing_calls SET status='settled' WHERE id=? AND status='reserved'", (call,))
        else:
            raise ValueError(step)


#: After the call row: the money facts of each order (contract phase 2 §2).
_ORDER_STEPS = {"product": ("journal", "settlement", "status"), "status_first": ("status", "journal", "settlement"),
                "inserted_settled": ("journal", "settlement"), "journal_after_settlement": ("status", "settlement", "journal")}


def _spend_tx(w, *, foreign_keys=True):
    """A write transaction on the store's connection, or (foreign keys off) on a connection of its own."""
    class _Tx:
        def __enter__(self):
            if foreign_keys:
                self.cm = w.store.tx()
                return self.cm.__enter__()
            self.c = w.store._connect()
            self.c.execute("PRAGMA foreign_keys=OFF")
            self.c.execute("BEGIN IMMEDIATE")
            return self.c

        def __exit__(self, kind, value, tb):
            if foreign_keys:
                return self.cm.__exit__(kind, value, tb)
            try:
                self.c.execute("ROLLBACK" if kind else "COMMIT")
            finally:
                self.c.close()
            return False
    return _Tx()


def _pair(t, w, op):
    if op["amount"] < 2**62:
        return w.pair
    return (w.ledger._account(t, UNIT.code, op["call"] + "-source", "paid"),
            w.ledger._account(t, UNIT.code, op["call"] + "-sink", "revenue"))


def run_spend_op(w: WalletWorld, op: dict) -> dict:
    kind = op["op"]
    if kind == "reserve":
        with w.store.tx() as t:
            _spend_call(t, w, op["call"], op["wallet"], op["at"], "reserved", op["hold"], op["at"] + op["deadline_in"])
    elif kind == "settle":
        order = op["order"]
        with _spend_tx(w, foreign_keys=order != "journal_after_settlement") as t:
            if op.get("existing"):
                steps = _ORDER_STEPS["status_first" if order == "inserted_settled" else order]
            else:
                _spend_call(t, w, op["call"], op["wallet"], op["at"], "settled" if order == "inserted_settled" else "reserved",
                            op["hold"], op["at"] + 600)
                steps = _ORDER_STEPS[order]
            _spend_money(t, w, op["call"], op["amount"], steps, _pair(t, w, op))
    elif kind == "unpaid":
        with w.store.tx() as t:
            if op.get("existing"):
                t.execute("UPDATE billing_calls SET status='settled' WHERE id=? AND status='reserved'", (op["call"],))
            else:
                _spend_call(t, w, op["call"], op["wallet"], op["at"], "settled", 0, op["at"] + 600)
    elif kind == "complete":
        with _spend_tx(w, foreign_keys=not op["settlement_first"]) as t:
            steps = ("settlement", "journal") if op["settlement_first"] else ("journal", "settlement")
            _spend_money(t, w, op["call"], op["amount"], steps, _pair(t, w, op))
    elif kind == "release":
        with w.store.tx() as t:
            t.execute("UPDATE billing_calls SET status='released' WHERE id=? AND status='reserved'", (op["call"],))
    elif kind == "expire":
        w.clock[0] += op["advance"]
        return {"expired": w.ledger.expire_due()}
    elif kind == "clock":
        w.clock[0] += op["delta"]
    elif kind == "malformed":
        with w.store.tx() as t:
            _spend_call(t, w, op["call"], op["wallet"], op["at"], "settled", 0, w.now + 600)
            if op["paid"]:
                _spend_money(t, w, op["call"], 3, ("journal", "settlement"), w.pair)
        w.malformed.add(op["wallet"])
    elif kind == "bulk":
        with w.store.tx() as t:
            t.execute("CREATE TEMP TABLE IF NOT EXISTS fixture_spend_rows(i INTEGER PRIMARY KEY, at INTEGER, amount INTEGER)")
            t.execute("DELETE FROM temp.fixture_spend_rows")
            t.executemany("INSERT INTO temp.fixture_spend_rows VALUES (?,?,?)",
                          [(i, at, amount) for i, (at, amount) in enumerate(op["rows"])])
            p = op["call"]
            cols = {table: [r[1] for r in t.execute(f"PRAGMA table_info({table})")]
                    for table in ("billing_calls", "billing_attempts", "billing_usage", "billing_journal", "billing_settlements")}

            def clone(table, key_column, key, changes):
                exprs = [changes.get(col, col) for col in cols[table]]
                t.execute(f"INSERT INTO {table}({','.join(cols[table])}) SELECT {','.join(exprs)} FROM temp.fixture_spend_rows f, "
                          f"{table} WHERE {key_column}=? ORDER BY f.i", (key,))

            clone("billing_calls", "id", w.template["call"], {
                "id": f"'{p}-'||f.i", "logical_call_id": f"'{p}-'||f.i", "account_id": f"'{w.wallets[op['wallet']]}'",
                "created_at": "f.at", "status": "'reserved'", "hold_units": "0", "deadline": "f.at+600"})
            clone("billing_attempts", "id", w.template["attempt"], {
                "id": f"'{p}-a'||f.i", "call_key": f"'{p}-'||f.i", "attempt_id": f"'{p}-a'||f.i",
                "fence": f"'{p}-f'||f.i", "request_id": f"'{p}-a'||f.i"})
            clone("billing_usage", "attempt_key", w.template["attempt"], {"attempt_key": f"'{p}-a'||f.i"})
            clone("billing_journal", "id", w.template["journal"], {
                "id": f"'{p}-j'||f.i", "voucher": f"'{p}-j'||f.i", "reference": f"'{p}-'||f.i", "amount": "f.amount",
                "debit_account": f"'{w.pair[0]}'", "credit_account": f"'{w.pair[1]}'"})
            clone("billing_settlements", "call_key", w.template["call"], {
                "call_key": f"'{p}-'||f.i", "attempt_key": f"'{p}-a'||f.i", "journal_id": f"'{p}-j'||f.i"})
            t.execute(f"UPDATE billing_calls SET status='settled' WHERE id IN (SELECT '{p}-'||i FROM temp.fixture_spend_rows)")
    elif kind == "reopen":
        _spend_reopen(w, op.get("damage"))
    elif kind == "rebuild":
        return _spend_rebuild(w, op)
    elif kind == "real":
        return _spend_real(w, op)
    else:
        raise ValueError(kind)
    return {}


def _day_rows(store) -> list:
    return store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start")


def _spend_reopen(w: WalletWorld, damage) -> None:
    before = _day_rows(w.store)
    connection = getattr(w.store._local, "c", None)
    if connection is not None:
        connection.close()
        w.store._local.c = None
    if damage:
        c = sqlite3.connect(w.store.db_path, isolation_level=None)
        try:
            if damage == "drop":
                c.execute("DROP TABLE payer_spend_days")
            elif damage == "sentinel":
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('gate')")
                c.execute("DELETE FROM payer_spend_days WHERE account_id=''")
                c.execute("DELETE FROM model_cost_rollup_deltas")
                c.execute("COMMIT")
            elif damage == "version":
                c.execute("UPDATE meta SET value='0' WHERE key='payer_spend_rollup_triggers'")
            elif damage == "marker":
                c.execute("DELETE FROM meta WHERE key='payer_spend_rollup_v1'")
        finally:
            c.close()
    w.store = Store(w.root)
    w.ledger = w.new_ledger()
    after = _day_rows(w.store)
    if after != before:
        raise Mismatch("reopen:rows", {"damage": damage, "before": before[:5], "after": after[:5]}, {"check": "reopen"})


def _spend_rebuild(w: WalletWorld, op: dict) -> dict:
    before = _day_rows(w.store)
    got = outcome(lambda: CostRollup.rebuild_wallet(w.store, account_id=w.wallets[op["wallet"]],
                                                    actor="fixture-equivalence", reason="equivalence property test rebuild",
                                                    clock=lambda: w.now))
    spec = {"check": "rebuild", "wallet": op["wallet"]}
    if op["wallet"] in w.malformed:
        if got[:2] != ("refused", "cost_rollup_rebuild_refused"):
            raise Mismatch("rebuild:not_refused", {"got": got}, spec)
    elif got[0] != "ok":
        raise Mismatch("rebuild:refused", {"got": got}, spec)
    if _day_rows(w.store) != before:
        raise Mismatch("rebuild:rows", {"before": before[:5], "after": _day_rows(w.store)[:5]}, spec)
    STATS["spend:rebuild:" + got[0]] += 1
    return {"outcome": got[0]}


def _spend_real(w: WalletWorld, op: dict) -> dict:
    """A real Ledger reservation of wallet A, its limit set at the oracle's boundary (spent + held + amount,
    plus op["delta"]) for one period: the reservation must decide what the oracle's numbers decide; an
    admitted one is then dispatched and settled through the product path."""
    w.ledger.expire_due()                               # reserve_call expires due holds first: so does the oracle
    with w.store.tx() as t:
        expect = oracle_exposure(wallet_facts(t, w.wallets["A"]), w.now)
    amount = op["amount"]
    budgets = {name: INT64 for name in ("daily", "weekly", "monthly")}
    if not expect.codes:
        value = expect.value[op["period"]]
        boundary = value["spent"] + value["held"] + amount + op["delta"]
        if 0 <= boundary <= INT64:
            budgets[op["period"]] = boundary
    _set_budgets(w, budgets)
    want = ("refused", expect.codes[0]) if expect.codes else ("ok",)
    if not expect.codes:
        for name, value in expect.value.items():
            if value["spent"] + value["held"] + amount > budgets[name]:
                want = ("refused", f"payer_{name}_budget_exceeded")
                break
    ctx = ChargeContext(w.users[0], w.users[0], op["call"], "fixture-approval", "workspace-A")
    w.ledger.open_run(ctx, unit=UNIT, budget=10**12, max_calls=10, max_attempts=10, deadline=w.now + 900)
    got = outcome(lambda: w.ledger.reserve_call(ctx, logical_call_id="real", amount=amount, price_id="fixture-sale-v1",
                                                deadline=w.now + 500))
    STATS["spend:real:" + ("ok" if got[0] == "ok" else got[1])] += 1
    if got[0] != want[0] or (got[0] == "refused" and got[1] != want[1]):
        raise Mismatch("real:reserve", {"want": want, "got": got, "budgets": budgets, "expected": expect.value,
                                        "codes": expect.codes}, {"check": "real", "op": op})
    if got[0] == "ok" and op["settle"] is not None:
        ticket = w.ledger.prepare_attempt(got[1], attempt_id="real", owner="fixture-spend", cost_price_id="fixture-cost-v1",
                                          requested_model="fixture-route-a")
        dispatched = outcome(lambda: w.ledger.dispatch(ticket))
        if dispatched[0] != "ok":
            raise Mismatch("real:dispatch", {"got": dispatched}, {"check": "real", "op": op})
        w.ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=Usage(op["settle"], 0, 0)))
    return {"outcome": got[:2]}


# ---------------------------------------------------------------- checks after every step
def check_spend_verify(w: WalletWorld, *, strict=True, **kwargs):
    """CostRollup.check is clean, except that a wallet with a malformed settled call reports each as itself
    (fact_fault). ``strict``: every day is compared, so every such wallet must report."""
    report = CostRollup.check(w.store, now=w.now, **(kwargs or {"all": True}))
    malformed = {("test" if name != "L" else "live", "", w.wallets[name]) for name in w.malformed}
    if report.issues or report.scopes:
        raise Mismatch("verify:issues", report.summary(), {"check": "verify"})
    for scope in report.mismatched:
        if scope not in malformed or any(d["check"] != "fact_fault" for d in report.wallets[scope].differences):
            raise Mismatch("verify:wallet", report.summary(), {"check": "verify"})
    if strict and malformed - set(report.mismatched):
        raise Mismatch("verify:fault_not_reported", report.summary(), {"check": "verify"})


def read_exposure(w: WalletWorld, t, name: str, at: int):
    account = w.wallets[name]
    expect = oracle_exposure(wallet_facts(t, account), at)
    windows = {p: (start, end) for p, (start, end) in spend_periods(at).items()}
    rollup = outcome(lambda: rollup_exposure(t, environment=WALLET_ENVS[name], account=account, windows=windows))
    reference = outcome(lambda: reference_exposure(t, account=account, windows=windows))
    legacy = None if name in w.malformed else outcome(lambda: LegacyPayerBudgets()._exposure(t, account, windows))
    compare_exposure({"check": "exposure", "wallet": name, "at": at}, expect, rollup, reference, legacy)


def spend_instants(rng: random.Random, now: int) -> list:
    """Six instants: each period's start ±1 s around ``now``, now, and random ones."""
    edges = {now, now + rng.randint(1, 3 * DAY), now - rng.randint(1, 3 * DAY)}
    for start, end in spend_periods(now).values():
        edges |= {start - 1, start, start + 1, end - 1, end}
    return rng.sample(sorted(edges), 6)


# ---------------------------------------------------------------- histories
class WalletHistory:
    def __init__(self, root: Path, seed: int, *, steps: int = STEPS, fault_rate: float = 0.01, fault_by=None):
        self.rng = random.Random(seed)
        self.seed, self.steps, self.fault_rate, self.fault_by = seed, steps, fault_rate, fault_by
        self.w = make_wallet_world(root / "store", self.rng.choice(ANCHORS) + self.rng.randint(-2 * DAY, 2 * DAY))
        self.trace = []

    def at(self) -> int:
        rng, now = self.rng, self.w.now
        pick = rng.random()
        if pick < 0.4:
            return now - rng.choice(FORWARD)
        if pick < 0.8:
            start, end = rng.choice(list(spend_periods(now).values()))
            return rng.choice((start, end)) + rng.choice((-1, 0, 1))
        return now + rng.choice((1, 60, 3_600, DAY))   # in the future: counted, no upper bound

    def calls(self, wallet: str, status: str, *, paid=None) -> list:
        sql = ("SELECT c.id FROM billing_calls c LEFT JOIN billing_settlements s ON s.call_key=c.id "
               "WHERE c.account_id=? AND c.status=?" + ("" if paid is None else " AND (s.call_key IS NOT NULL)=?")
               + " ORDER BY c.rowid")
        args = (self.w.wallets[wallet], status) if paid is None else (self.w.wallets[wallet], status, int(paid))
        return [r["id"] for r in self.w.store.all(sql, args)]

    def generate(self, *, fault: bool) -> dict:
        rng, w = self.rng, self.w
        wallet = rng.choice(("A", "A", "B", "L"))
        if fault:
            return {"op": "malformed", "wallet": wallet, "call": w.key("bad"), "at": rng.choice(MALFORMED_TIMES),
                    "paid": rng.random() < 0.5}
        kind = rng.choices(list(SPEND_WEIGHTS), weights=list(SPEND_WEIGHTS.values()))[0]
        if kind == "reserve":
            return {"op": kind, "wallet": wallet, "call": w.key("held"), "at": self.at(),
                    "hold": rng.choice(SPEND_HOLDS[wallet]), "deadline_in": rng.choice((60, 600, DAY))}
        if kind == "settle":
            held = self.calls(wallet, "reserved")
            existing = held and rng.random() < 0.5
            amount = rng.choice(SPEND_AMOUNTS) if wallet != "A" or rng.random() < 0.5 else rng.randint(0, 9)
            return {"op": kind, "wallet": wallet, "order": rng.choice(SPEND_ORDERS), "amount": amount,
                    "call": rng.choice(held) if existing else w.key("sold"), "existing": bool(existing),
                    "at": self.at(), "hold": rng.choice(SPEND_HOLDS[wallet])}
        if kind == "unpaid":
            held = self.calls(wallet, "reserved")
            existing = held and rng.random() < 0.5
            return {"op": kind, "wallet": wallet, "call": rng.choice(held) if existing else w.key("unpaid"),
                    "existing": bool(existing), "at": self.at()}
        if kind == "complete":
            unpaid = self.calls(wallet, "settled", paid=False)
            if not unpaid:
                return self.generate(fault=False)
            return {"op": kind, "wallet": wallet, "call": rng.choice(unpaid), "amount": rng.choice(SPEND_AMOUNTS),
                    "settlement_first": rng.random() < 0.3}
        if kind == "release":
            held = self.calls(wallet, "reserved")
            if not held:
                return self.generate(fault=False)
            return {"op": kind, "wallet": wallet, "call": rng.choice(held)}
        if kind == "expire":
            return {"op": kind, "advance": rng.choice((61, 601, DAY + 1))}
        if kind == "clock":
            return {"op": kind, "delta": rng.choice((-DAY, -3_600, -1, 1, 3_600, DAY, 3 * DAY))}
        if kind == "bulk":
            low = min(start for start, _ in spend_periods(w.now).values())
            rows = [(rng.choice((low - 1, low, low + 1, w.now - rng.randint(0, 3 * DAY))), rng.randint(0, 7))
                    for _ in range(rng.choice((20, 200)))]
            return {"op": kind, "wallet": wallet, "call": w.key("bulk"), "rows": rows}
        if kind == "reopen":
            return {"op": kind, "damage": rng.choice((None, None, "drop", "sentinel", "version", "marker"))}
        if kind == "rebuild":
            return {"op": kind, "wallet": wallet}
        return {"op": "real", "call": w.key("real"), "amount": rng.choice((0, 1, 2, 5)),
                "period": rng.choice(("daily", "weekly", "monthly")), "delta": rng.choice((-1, 0, 0, 5)),
                "settle": rng.choice((None, 1, 3))}

    def check(self, step: int) -> None:
        w = self.w
        if step % 3 == 2:
            check_spend_verify(w, strict=False, mode="daily")
        else:
            check_spend_verify(w)
        with w.store.tx() as t:
            for at in spend_instants(self.rng, w.now):
                for name in WALLET_ENVS:
                    read_exposure(w, t, name, at)

    def run(self) -> None:
        fault_step = self.rng.randint(0, self.fault_by) if self.fault_by is not None else None
        for step in range(self.steps):
            for _ in range(self.rng.randint(1, 4)):
                fault = (step == fault_step) or (fault_step is None and self.rng.random() < self.fault_rate)
                op = self.generate(fault=fault)
                if fault:
                    fault_step = -1
                self.trace.append(op)
                run_spend_op(self.w, op)
            self.check(step)


def run_wallet_history(root: Path, seed: int, **options) -> WalletHistory:
    history = WalletHistory(root, seed, **options)
    try:
        history.run()
    except Mismatch as failure:
        pytest.fail(f"wallet seed {seed}, after {len(history.trace)} ops: {failure}\n"
                    f"trace: {json.dumps(history.trace, default=str)[:6000]}", pytrace=False)
    except Exception as error:
        pytest.fail(f"wallet seed {seed}, after {len(history.trace)} ops: {type(error).__name__}: {error}\n"
                    f"last op: {json.dumps(history.trace[-1:], default=str)[:2000]}")
    return history


@pytest.mark.parametrize("seed", WALLET_SEEDS)
def test_spend_rollup_equals_oracle_reference_and_legacy_on_random_wallet_histories(seed, tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    run_wallet_history(tmp_path, 20_000 + seed)


@pytest.mark.parametrize("seed", range(1, WALLET_FAULT_SEEDS + 1))
def test_a_malformed_settled_call_keeps_its_wallet_refused_on_random_histories(seed, tmp_path, monkeypatch):
    """Every history has a malformed settled call in its first half: from then on that wallet refuses
    payer_budget_inconsistent at every instant, every rebuild of it refuses and reopening keeps it (only
    restoring a backup from before the write clears it), while the other wallets still equal the oracle."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    history = run_wallet_history(tmp_path, 30_000 + seed, fault_by=STEPS // 2)
    w = history.w
    assert w.malformed
    with w.store.tx() as t:
        for name in w.malformed:
            scope = (WALLET_ENVS[name], "", w.wallets[name])
            assert t.execute("SELECT 1 FROM model_cost_rollup_faults WHERE environment=? AND provider=? AND account_ref=? "
                             "AND resolved_at IS NULL", scope).fetchone(), scope
            with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
                rollup_exposure(t, environment=scope[0], account=scope[2], windows=spend_periods(w.now))
    STATS["spend:fault_histories"] += 1


def test_12000_settled_calls_legacy_refuses_on_its_cap_and_the_spend_rollup_still_equals_the_oracle(tmp_path,
                                                                                                    monkeypatch):
    """12,000 settled calls of wallet A this month (and three holds): the 0bcce176 read refuses
    payer_budget_capacity; the rollup equals the oracle and the product's reference at the period edges,
    verifies, and a real reservation at exactly the limit is admitted while one unit less refuses."""
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    now = utc("2036-02-28T12:00:00")
    w = make_wallet_world(tmp_path / "store", now)
    rng = random.Random(12_000)
    month = utc("2036-02-01T00:00:00")
    run_spend_op(w, {"op": "bulk", "wallet": "A", "call": "fx-cap",
                     "rows": [(rng.randint(month, now), rng.randint(0, 7)) for _ in range(12_000)]})
    for k in range(3):
        run_spend_op(w, {"op": "reserve", "wallet": "A", "call": f"fx-cap-held-{k}", "at": now - k, "hold": 7,
                         "deadline_in": DAY})
    check_spend_verify(w)
    with w.store.tx() as t:
        for at in (now, now + 1, utc("2036-02-29T00:00:00") - 1, utc("2036-02-29T00:00:00"), utc("2036-03-01T00:00:00") - 1):
            expect = oracle_exposure(wallet_facts(t, w.wallets["A"]), at)
            assert not expect.codes and expect.candidates > SPEND_CAP, (at, expect.codes)
            windows = spend_periods(at)
            assert outcome(lambda: LegacyPayerBudgets()._exposure(t, w.wallets["A"], windows)) == (
                "refused", "payer_budget_capacity")
            read_exposure(w, t, "A", at)
    for delta, admitted in ((-1, False), (0, True)):
        got = run_spend_op(w, {"op": "real", "call": f"fx-cap-real{delta}", "amount": 5, "period": "monthly",
                               "delta": delta, "settle": None})
        assert (got["outcome"][0] == "ok") is admitted, got
    check_spend_verify(w)
