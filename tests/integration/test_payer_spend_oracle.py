"""Independent oracle for the personal limits' exposure read (cost-capacity phase 2).

``PayerBudgets._exposure`` used to read a wallet's money facts row by row (0bcce176); phase 2 reads
trigger-maintained per-wallet day rows instead. This file was written from the phase-2 addendum and
the 0bcce176 code only, without reading the rollup's read path or the equivalence test's own oracle,
so that two independent derivations of "what a wallet's exposure is" meet the product. Three
implementations are compared on seeded random histories written to real, isolated Stores (never
``data/``; no keys, no provider traffic):

* **oracle** — ``oracle_exposure`` / ``oracle_check`` below: plain Python over every ``billing_calls``
  row of the wallet, joined to its settlement and journal, no caps. Held is the hold of every
  ``reserved`` call whatever its date; spent per period is the journal amount of every ``settled``
  call reserved at or after the period's start, with no upper bound. The addendum's refusals in its
  order: a settled call with a non-integer or negative ``created_at`` latches the wallet
  (``payer_budget_inconsistent``, cleared only by a backup restore); more than 10,000 reserved calls
  refuse ``payer_budget_capacity``; a settled call from the earliest period start without a journal
  amount refuses ``payer_budget_inconsistent`` until the fact arrives. ``check`` adds the policy
  (not configured, disabled) and the period order daily, weekly, monthly;
* **legacy** — tests/integration/legacy_cost_counters.py ``LegacyPayerBudgets._exposure``, the verbatim
  0bcce176 read (verified against that commit by test_legacy_fixture_is_verbatim): it must agree with
  the oracle while it has at most 10,000 candidates (reserved calls plus settled calls from the
  earliest period start) and no settled call with a malformed ``created_at`` (the addendum's
  deliberate deviations: legacy raised TypeError on TEXT, counted REAL and ignored negatives), and
  must refuse ``payer_budget_capacity`` above its cap where the rollup still equals the oracle;
* **rollup** — ``PayerBudgets._exposure`` and ``PayerBudgets.check``, plus the real ``Ledger`` paths
  that call ``check`` (``reserve_call`` with the hold, ``dispatch`` with 0): their outcome must be
  what the oracle's numbers decide, unless an earlier check of the same path refused first
  (insufficient funds, the run's own budget, a fence). The product's own uncapped
  ``spend_rollup.reference_exposure`` (what the verifier compares the day rows with) must equal the
  oracle without its hold cap as well.

A history: two environments x two owners, so four wallets; the clock starts near one of the
calendar anchors (Sunday->Monday 2034-01-02, month end->2034-02-01 where the week start precedes the
month start, Monday the 1st 2034-05-01, 2036-02-29->03-01, 2035-01-01) and crosses it. Operations:
real reserves with holds 0, 1, 2**40, 2**62 and random; prepare+dispatch+settle through the product
(completed and platform_fault, settled at once or later, late after an expiry); calls written with SQL
(reserved, or inserted already settled, dated at the anchor +-1 s, a day or a week before it, far in
the past or days ahead); settlements written with SQL in the product order (journal, settlement,
status) and in the other arrival orders the addendum names (status first, status last, journal last
with foreign_keys=OFF), each with the remaining facts arriving in the same transaction, in a later
step (a late settlement) or never (an unpaid settled call); amounts 0, 1, 7, around the 10**15 limb
boundary, 2**40, 2**62; release by cancel and by SQL; expiry by the clock and ``expire_due``; clock
steps forward across day, week and month edges and back; policies set at the oracle's totals +-1 and
disabled; reopening the Store, plain, without the version marker and with the day table dropped;
committed wallet rebuilds. Fault histories add one settled call with a TEXT, REAL, BLOB or negative
``created_at`` (directly, or reserved first and settled or released later).

After every step: ``CostRollup.check`` over every day is clean (a wallet with a malformed settled
call is reported as exactly that, ``fact_fault``); a rebuild of the touched wallet, rolled back,
leaves its day rows identical (or is refused when the wallet has a malformed settled call); and at
several instants (the clock, every day, week and month edge within 35 days +-1 s, random ones) the
rollup equals the oracle on every field of every period, legacy equals both under its cap, and
``check`` returns exactly the oracle's code with the wallet's current policy. The capacity tests
hold 12,000 settled calls (legacy refuses, rollup equals the oracle) and 10,000 then 10,001
reserved calls.

Defaults: ``SYNWEVO_PAYER_ORACLE_SEEDS`` (300) seeds x ``SYNWEVO_PAYER_ORACLE_STEPS`` (12) steps and
``SYNWEVO_PAYER_ORACLE_FAULT_SEEDS`` (60) fault histories (about a tenth of a second each). A disagreement reports its seed, step,
operation and the trace, so it can become a deterministic test.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from synwevo_core.billing import BillingError, ChargeContext, Ledger, Usage
from synwevo_core.billing import spend_rollup
from synwevo_core.billing.cost_rollup import CostRollup
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.identity import Identity
from synwevo_core.store import Store
from tests.integration.legacy_cost_counters import LegacyPayerBudgets
from tests.integration.test_billing_ledger import (COST_UNIT, START, UNIT, cost_price, evidence, fixture_authorize,
                                                    fixture_verify, fund, sale_price)

SEEDS = list(range(1, int(os.environ.get("SYNWEVO_PAYER_ORACLE_SEEDS") or 300) + 1))
FAULT_SEEDS = int(os.environ.get("SYNWEVO_PAYER_ORACLE_FAULT_SEEDS") or 60)
STEPS = int(os.environ.get("SYNWEVO_PAYER_ORACLE_STEPS") or 12)

DAY = 86_400
CAP = 10_000                      # the 0bcce176 candidate cap and the addendum's reserved-call cap
INT64 = 2**63 - 1
B = 10**15                        # the limb base of the day rows (addendum §3)
ENVIRONMENTS = ("test", "live")
API_NAMESPACE = "fixture-payer-oracle"
SQL_NAMESPACE = "fixture-payer-oracle-sql"
PERIODS = ("daily", "weekly", "monthly")
FIELDS = ("start", "end", "held", "spent")
BASE_COMMIT = "0bcce176"
STATS = Counter()


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())


ANCHORS = (
    ("sunday_monday", utc("2034-01-02 00:00:00")),   # Sunday 2034-01-01 -> Monday, the day after a month start
    ("month_end", utc("2034-02-01 00:00:00")),       # Wednesday the 1st: the week starts 2034-01-30, before the month
    ("monday_first", utc("2034-05-01 00:00:00")),    # Monday the 1st: day, week and month start together
    ("leap_day", utc("2036-03-01 00:00:00")),        # the day after 2036-02-29
    ("new_year", utc("2035-01-01 00:00:00")),        # Monday 2035-01-01
)


# ================================================================================= the oracle
def oracle_windows(at: int) -> dict:
    """The UTC day, Monday-based week and month containing ``at``, each (start, end), end exclusive.
    Day and week by epoch arithmetic (1970-01-01 was a Thursday), the month by the calendar."""
    day0 = (at // DAY) * DAY
    week0 = day0 - (((at // DAY) + 3) % 7) * DAY
    d = datetime.fromtimestamp(day0, timezone.utc)
    month0 = utc(f"{d.year:04d}-{d.month:02d}-01 00:00:00")
    year, month = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    month1 = utc(f"{year:04d}-{month:02d}-01 00:00:00")
    return {"daily": (day0, day0 + DAY), "weekly": (week0, week0 + 7 * DAY), "monthly": (month0, month1)}


@dataclass(frozen=True)
class Fact:
    """One billing_calls row of the wallet with the journal amount reached through its settlement."""
    id: str
    status: str
    hold: int
    created_at: object
    ctype: str          # typeof(created_at) in SQLite: the column has no type CHECK (addendum §2)
    amount: object      # None without a settlement or without its journal


FACTS = ("SELECT c.id,c.status,c.hold_units,c.created_at,typeof(c.created_at),j.amount FROM billing_calls c "
         "LEFT JOIN billing_settlements s ON s.call_key=c.id LEFT JOIN billing_journal j ON j.id=s.journal_id "
         "WHERE c.account_id=?")


def wallet_facts(t, account: str) -> list[Fact]:
    return [Fact(*row) for row in t.execute(FACTS, (account,)).fetchall()]


def malformed(f: Fact) -> bool:
    return f.ctype != "integer" or f.created_at < 0


@dataclass
class Expect:
    code: str | None = None
    why: str = ""
    periods: dict | None = None
    candidates: int = 0       # what the 0bcce176 read selects: reserved calls plus settled calls from E
    comparable: bool = True   # legacy computed the same thing (no settled call with a malformed created_at)


def oracle_exposure(facts: list[Fact], at: int, cap: int | None = CAP) -> Expect:
    """Addendum §1 (semantics) and §4 (the refusal order) over the wallet's facts; ``cap=None`` is the
    uncapped read (what ``reference_exposure`` computes)."""
    windows = oracle_windows(at)
    earliest = min(start for start, _ in windows.values())
    settled = [f for f in facts if f.status == "settled"]
    reserved = [f for f in facts if f.status == "reserved"]
    broken = [f for f in settled if malformed(f)]
    candidates = len(reserved) + sum(1 for f in settled if not malformed(f) and f.created_at >= earliest)
    if broken:
        return Expect("payer_budget_inconsistent", "fault", candidates=candidates, comparable=False)
    if cap is not None and len(reserved) > cap:
        return Expect("payer_budget_capacity", "holds", candidates=candidates)
    if any(f.amount is None and f.created_at >= earliest for f in settled):
        return Expect("payer_budget_inconsistent", "unpaid", candidates=candidates)
    held = sum(f.hold for f in reserved)
    periods = {name: {"start": start, "end": end, "held": held,
                      "spent": sum(f.amount for f in settled if f.created_at >= start)}
               for name, (start, end) in windows.items()}
    return Expect(None, "", periods, candidates)


def oracle_check(expect: Expect, policy, amount: int) -> tuple:
    """``PayerBudgets.check`` at 0bcce176, unchanged by the addendum: policy, exposure, period order."""
    if policy is None:
        return ("refused", "payer_budget_not_configured")
    if not policy["enabled"]:
        return ("refused", "payer_budget_disabled")
    if expect.code:
        return ("refused", expect.code)
    for name in PERIODS:
        period = expect.periods[name]
        if period["spent"] + period["held"] + amount > policy[name + "_budget"]:
            return ("refused", "payer_" + name + "_budget_exceeded")
    return ("ok", None)


# ================================================================================= comparing
class Mismatch(AssertionError):
    def __init__(self, signature: str, detail: dict, spec: dict | None = None):
        self.signature, self.detail, self.spec = signature, detail, spec or {}
        super().__init__(signature + " " + json.dumps({"detail": detail, "spec": self.spec}, default=str,
                                                      sort_keys=True)[:3500])


def outcome(fn):
    try:
        return ("ok", fn())
    except BillingError as exc:
        return ("refused", exc.code)
    except Exception as exc:  # noqa: BLE001 - a crash is an outcome to compare, not a test error
        return ("error", f"{type(exc).__name__}: {str(exc)[:200]}")


def _short(value):
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value]
    if isinstance(value, sqlite3.Row):
        return dict(value)
    return value if isinstance(value, (int, str, type(None), float, bool)) else repr(value)[:200]


def compare_exposure(who: str, spec: dict, expect: Expect, got) -> None:
    STATS[f"{who}:{expect.code or 'ok'}:{expect.why}"] += 1
    if expect.code:
        if got[:2] != ("refused", expect.code):
            raise Mismatch(f"{who}:outcome", {"expected": expect.code, "got": _short(got)}, spec)
        return
    if got[0] != "ok":
        raise Mismatch(f"{who}:outcome", {"expected": _short(expect.periods), "got": _short(got)}, spec)
    periods = got[1]
    if not isinstance(periods, dict) or sorted(periods) != sorted(PERIODS):
        raise Mismatch(f"{who}:periods", {"got": _short(periods)}, spec)
    for name in PERIODS:
        for key in FIELDS:
            if periods[name].get(key) != expect.periods[name][key] or type(periods[name].get(key)) is not int:
                raise Mismatch(f"{who}:{name}.{key}", {"expected": expect.periods[name], "got": _short(periods[name])},
                               spec)


def compare_legacy(spec: dict, expect: Expect, got) -> None:
    if not expect.comparable:
        STATS["legacy:deviation"] += 1
        return
    if expect.candidates > CAP:
        STATS["legacy:capacity"] += 1
        if got[:2] != ("refused", "payer_budget_capacity"):
            raise Mismatch("legacy:capacity", {"candidates": expect.candidates, "got": _short(got)}, spec)
        return
    compare_exposure("legacy", spec, expect, got)


# ================================================================================= the world
@dataclass
class World:
    root: Path
    store: Store
    users: list
    clock: list
    run_deadline: int
    ledgers: dict = field(default_factory=dict)     # environment -> Ledger (API namespace)
    wallets: dict = field(default_factory=dict)     # (environment, k) -> wallet account id
    revenue: dict = field(default_factory=dict)     # environment -> the platform revenue account
    sql_runs: dict = field(default_factory=dict)    # environment -> run key of the SQL-written calls
    calls: dict = field(default_factory=dict)       # call id -> {"env", "k", "kind", "ticket", "bad"}
    pending: list = field(default_factory=list)     # settlement facts that have not arrived yet
    seq: int = 0
    trace: list = field(default_factory=list)

    @property
    def now(self) -> int:
        return self.clock[0]

    def next(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{self.seq}"

    def budgets(self, env: str) -> PayerBudgets:
        return self.ledgers[env].payer_budgets

    def attach_ledgers(self) -> None:
        for env in ENVIRONMENTS:
            ledger = Ledger(self.store, environment=env, namespace=API_NAMESPACE, authorize=fixture_authorize,
                            verify_payment=fixture_verify, clock=lambda: self.clock[0], max_hold_seconds=DAY)
            ledger.payer_budgets = PayerBudgets(ledger)
            self.ledgers[env] = ledger


FUNDS = {("test", 0): 2**62, ("test", 1): 2**60, ("live", 0): 2**61, ("live", 1): 2**60}


def make_world(root: Path, anchor: int) -> World:
    """FIXTURE: an isolated Store, two users, a funded wallet per user and environment (one of 2**62
    for the large holds; the platform's paid account must stay above -2**63), fixture prices in both
    environments, a revenue account each, a run each for SQL-written calls, and unlimited policies."""
    store = Store(root)
    with store.conn() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS fixture_payment_inbox(event_id TEXT PRIMARY KEY,receipt TEXT NOT NULL);"
                        "CREATE TABLE IF NOT EXISTS fixture_billing_authority(payer TEXT,actor TEXT,authority TEXT);")
    identity = Identity(store)
    users = [identity.create_user(f"payer-oracle-{i}@example.test", None, status="active",
                                  with_personal_workspace=False)["id"] for i in range(2)]
    with store.tx() as t:
        t.executemany("INSERT INTO fixture_billing_authority VALUES (?,?,?)", [(u, u, "fixture-approval") for u in users])
    w = World(root, store, users, [START], anchor + 400 * DAY)
    w.attach_ledgers()
    for env in ENVIRONMENTS:
        w.ledgers[env].publish_price(sale_price())
        w.ledgers[env].publish_price(cost_price())
    w.clock[0] = anchor - 10 * DAY
    for (env, k), amount in FUNDS.items():
        fund((store, w.ledgers[env], users, w.clock), amount, order=f"fixture-payer-oracle-{env}-{k}", user=k,
             ledger=w.ledgers[env])
    with store.tx() as t:
        for env in ENVIRONMENTS:
            for k, user in enumerate(users):
                w.wallets[(env, k)] = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? "
                                                "AND kind='wallet' AND owner=?", (env, UNIT.code, user)).fetchone()[0]
            w.revenue[env] = w.ledgers[env]._account(t, UNIT.code, "platform", "revenue")
            run = f"fixture-payer-oracle-sql-{env}"
            t.execute("INSERT INTO billing_runs(id,environment,namespace,run_id,context,unit,budget,max_calls,"
                      "max_attempts,deadline,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (run, env, SQL_NAMESPACE, "sql-run", "{}", UNIT.code, 0, 10_000, 10_000, w.run_deadline, START))
            w.sql_runs[env] = run
    for env in ENVIRONMENTS:
        for k in range(2):
            set_policy(w, env, k)
    return w


def set_policy(w: World, env: str, k: int, *, enabled=True, daily=INT64, weekly=INT64, monthly=INT64) -> None:
    pb = w.budgets(env)
    with w.store.tx() as t:
        current = pb._policy(t, w.users[k], UNIT.code)
        pb.set(w.users[k], unit_code=UNIT.code, expected_revision=current["revision"] if current else 0,
               enabled=enabled, daily_budget=daily, weekly_budget=weekly, monthly_budget=monthly)


def balance(t, account: str) -> int:
    return t.execute("SELECT amount FROM billing_balances WHERE account_id=?", (account,)).fetchone()[0]


def reserved_calls(w: World) -> list[dict]:
    with w.store.tx() as t:
        rows = t.execute("SELECT c.id FROM billing_calls c WHERE c.status='reserved' AND c.id IN (%s) ORDER BY c.rowid"
                         % ",".join("?" * len(w.calls)) if w.calls else "SELECT NULL WHERE 0",
                         tuple(w.calls)).fetchall()
    return [{"id": r[0], **w.calls[r[0]]} for r in rows]


def expect_check(w: World, t, env: str, k: int, amount: int, at: int | None = None) -> tuple:
    facts = wallet_facts(t, w.wallets[(env, k)])
    policy = w.budgets(env)._policy(t, w.users[k], UNIT.code)
    return oracle_check(oracle_exposure(facts, w.now if at is None else at), policy, amount)


#: Refusals a Ledger path raises before it asks the payer budgets (reserve_call / dispatch at 0bcce176).
PRE_PAYER = {"insufficient_funds", "balance_cache_inconsistent", "run_budget_exceeded", "wallet_frozen", "invalid_hold_deadline", "idempotency_conflict",
             "run_expired", "dispatch_fenced", "run_time_too_short", "usage_pending", "call_not_found"}


def agree(what: str, op: dict, expected: tuple, got) -> None:
    """The product path decided what the oracle's numbers decide, unless an earlier check refused."""
    if got[0] == "refused" and got[1] in PRE_PAYER:
        STATS[f"{what}:before_payer:{got[1]}"] += 1
        return
    normalized = ("ok", None) if got[0] == "ok" else got[:2]
    STATS[f"{what}:{expected[1] or 'ok'}"] += 1
    if normalized != expected:
        raise Mismatch(what, {"expected": expected, "got": _short(got)}, {"op": op})


# ================================================================================= SQL-written facts
def call_context(w: World, k: int) -> str:
    return json.dumps({"payer_user_id": w.users[k], "actor_user_id": w.users[k], "run_id": "sql-run",
                       "authorization_ref": "fixture-approval"})


def insert_call(t, w: World, call: str, env: str, k: int, *, hold: int, created_at, deadline: int, status: str) -> None:
    t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,"
              "status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (call, w.sql_runs[env], call, call_context(w, k), w.wallets[(env, k)], "fixture-sale-v1", hold, deadline,
               status, created_at))


def settlement_facts(w: World, call: str, env: str, k: int, amount: int, at: int) -> dict:
    """The facts of one SQL-written settlement; which of them exist is decided part by part."""
    return {"call": call, "env": env, "k": k, "amount": amount, "attempt": w.next("sql-attempt"),
            "journal": w.next("sql-journal"), "at": at}


def write_part(t, w: World, f: dict, part: str) -> None:
    """T: the call's status becomes settled. S: attempt, usage and the settlement row (naming the journal
    whether or not it exists yet). J: the journal row, bounded by the wallet's balance at that moment
    (the journal trigger refuses a negative wallet), so a late journal may carry less than planned."""
    account = w.wallets[(f["env"], f["k"])]
    if part == "T":
        status = t.execute("SELECT status FROM billing_calls WHERE id=?", (f["call"],)).fetchone()[0]
        if status == "reserved":
            t.execute("UPDATE billing_calls SET status='settled' WHERE id=?", (f["call"],))
        else:
            STATS["sql:status_no_longer_reserved"] += 1
    elif part == "J":
        f["amount"] = min(f["amount"], balance(t, account))
        t.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,credit_account,amount,"
                  "kind,reference,created_at) VALUES (?,?,?,?,?,?,?,?,'sale',?,?)",
                  (f["journal"], f["env"], SQL_NAMESPACE, "sale:" + f["call"], UNIT.code, account, w.revenue[f["env"]],
                   f["amount"], f["call"], f["at"]))
    elif part == "S":
        t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                  "requested_model,cost_price_id,status,created_at,dispatched_at) VALUES (?,?,?,?,?,?,?,?,?,?,'settled',?,?)",
                  (f["attempt"], f["call"], f["attempt"], "fixture-payer-oracle", f["attempt"] + "-fence", f["at"],
                   f["env"], "fixture-provider", "fixture-model", "fixture-cost-v1", f["at"], f["at"]))
        t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                  "absorbed_d,late,created_at) VALUES (?,'{}','completed','0','1',?,?,'0','1',0,?)",
                  (f["attempt"], COST_UNIT.code, f["amount"], f["at"]))
        t.execute("INSERT INTO billing_settlements VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (f["call"], f["attempt"], f["journal"], str(f["amount"]), "1", "0", "1", "0", "1", f["at"]))
    else:
        raise ValueError(part)


def write_parts(w: World, f: dict, parts: str, *, foreign_keys: bool = True) -> None:
    if foreign_keys:
        with w.store.tx() as t:
            for part in parts:
                write_part(t, w, f, part)
        return
    # The journal arrives after the settlement that names it: only possible with the checks off.
    c = w.store._local.c if getattr(w.store._local, "c", None) is not None else None
    if c is not None:
        c.close()
        w.store._local.c = None
    c = sqlite3.connect(w.store.db_path, isolation_level=None, timeout=30)
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("BEGIN IMMEDIATE")
        for part in parts:
            write_part(c, w, f, part)
        c.execute("COMMIT")
    finally:
        c.close()


#: How a reserved call's settlement facts arrive (addendum §2, §8 test 3): parts written now, then later.
ORDERS = {"product": ("JST", ""), "status_first": ("T", "JS"), "status_last": ("JS", "T"), "journal_last": ("TS", "J")}


def pick_amount(rng: random.Random, cap: int) -> int:
    choice = rng.choice((0, 1, 7, B - 1, B, B + 1, 3 * B + 5, 2**40, 2**62, rng.randint(0, 10**6)))
    return max(0, min(choice, cap))


# ================================================================================= operations
def op_reserve(w: World, op: dict) -> None:
    env, k = op["env"], op["k"]
    ledger = w.ledgers[env]
    ledger.expire_due()
    with w.store.tx() as t:
        expected = expect_check(w, t, env, k, op["hold"])
    run = f"fixture-payer-oracle-run-{env}-{k}"
    ctx = ChargeContext(w.users[k], w.users[k], run, "fixture-approval", "workspace-A")
    ledger.open_run(ctx, unit=UNIT, budget=INT64, max_calls=10_000, max_attempts=10_000, deadline=w.run_deadline)
    got = outcome(lambda: ledger.reserve_call(ctx, logical_call_id=w.next("call"), amount=op["hold"],
                                              price_id="fixture-sale-v1", deadline=w.now + op["ttl"]))
    agree("reserve", op, expected, got)
    if got[0] == "ok":
        w.calls[got[1]] = {"env": env, "k": k, "kind": "api", "ticket": None, "bad": False}


def op_attempt(w: World, op: dict) -> None:
    """prepare + dispatch (the payer budgets are checked again with 0), then maybe settle at once."""
    info = w.calls[op["call"]]
    ledger = w.ledgers[info["env"]]
    ledger.expire_due()
    with w.store.tx() as t:
        expected = expect_check(w, t, info["env"], info["k"], 0)
    got = outcome(lambda: ledger.prepare_attempt(op["call"], attempt_id=w.next("attempt"), owner="fixture-worker",
                                                 cost_price_id="fixture-cost-v1", requested_model="fixture-request-alias"))
    if got[0] != "ok":
        STATS["attempt:not_prepared:" + str(got[1])] += 1
        return
    ticket = got[1]
    got = outcome(lambda: ledger.dispatch(ticket))
    agree("dispatch", op, expected, got)
    if got[0] != "ok":
        return
    info["ticket"] = ticket
    if op.get("settle"):
        op_settle(w, {"kind": "settle", "call": op["call"], "charge": op["charge"], "outcome": op["outcome"]})


def op_settle(w: World, op: dict) -> None:
    info = w.calls[op["call"]]
    ledger = w.ledgers[info["env"]]
    ticket = info["ticket"]
    if ticket is None:
        return
    if op.get("late"):
        with w.store.tx() as t:
            row = t.execute("SELECT status,deadline FROM billing_calls WHERE id=?", (op["call"],)).fetchone()
        if row[0] == "reserved":
            w.clock[0] = max(w.now, row[1] + 1)
    ledger.expire_due()
    got = outcome(lambda: ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=Usage(op["charge"], 0, 0)),
                                                      outcome=op["outcome"]))
    if got[0] != "ok":
        raise Mismatch("settle:" + got[0], {"got": _short(got)}, {"op": op})
    STATS["settle:" + op["outcome"] + (":late" if got[1].late else "")] += 1
    info["ticket"] = None


def op_sql_call(w: World, op: dict) -> None:
    env, k = op["env"], op["k"]
    call = w.next("sql-call")
    w.calls[call] = {"env": env, "k": k, "kind": "sql", "ticket": None, "bad": op.get("bad", False)}
    with w.store.tx() as t:
        insert_call(t, w, call, env, k, hold=op["hold"], created_at=op["created_at"], deadline=op["deadline"],
                    status=op["status"])
        if op["status"] == "settled":
            f = settlement_facts(w, call, env, k, min(op["amount"], balance(t, w.wallets[(env, k)])), w.now)
            if op["arrival"] == "now":
                write_part(t, w, f, "J")
                write_part(t, w, f, "S")
            elif op["arrival"] == "later":
                w.pending.append((f, "JS", True))
            else:
                STATS["sql:unpaid_forever"] += 1


def op_sql_settle(w: World, op: dict) -> None:
    info = w.calls[op["call"]]
    now_parts, later = ORDERS[op["order"]]
    with w.store.tx() as t:
        f = settlement_facts(w, op["call"], info["env"], info["k"], min(op["amount"], balance(t, w.wallets[(info["env"], info["k"])])),
                             w.now)
    info["settling"] = True
    write_parts(w, f, now_parts, foreign_keys=op["order"] != "journal_last")
    STATS["sql_settle:" + op["order"]] += 1
    if later:
        if op["arrival"] == "later":
            w.pending.append((f, later, True))
        elif op["arrival"] == "never":
            STATS["sql:unpaid_forever"] += 1
        else:
            write_parts(w, f, later)


def op_drain(w: World, op: dict) -> None:
    """A late settlement: the rest of a settlement's facts arrive."""
    if not w.pending:
        return
    f, parts, _ = w.pending.pop(op["index"] % len(w.pending))
    write_parts(w, f, parts)
    STATS["late_settlement:" + parts] += 1


def op_release(w: World, op: dict) -> None:
    info = w.calls[op["call"]]
    if info["kind"] == "api":
        ledger = w.ledgers[info["env"]]
        ledger.expire_due()
        got = outcome(lambda: ledger.cancel_call(op["call"]))
        STATS["release:cancel:" + str(got[1] if got[0] == "ok" else got)] += 1
    else:
        with w.store.tx() as t:
            t.execute("UPDATE billing_calls SET status='released' WHERE id=? AND status='reserved'", (op["call"],))
        STATS["release:sql"] += 1


def op_expire(w: World, op: dict) -> None:
    w.clock[0] = op["at"]
    for env in ENVIRONMENTS:
        STATS["expired"] += w.ledgers[env].expire_due()


def op_clock(w: World, op: dict) -> None:
    w.clock[0] = op["at"]
    STATS["clock:back" if op.get("back") else "clock:forward"] += 1


def op_policy(w: World, op: dict) -> None:
    set_policy(w, op["env"], op["k"], enabled=op["enabled"], daily=op["daily"], weekly=op["weekly"], monthly=op["monthly"])


def op_reopen(w: World, op: dict) -> None:
    c = getattr(w.store._local, "c", None)
    if c is not None:
        c.close()
        w.store._local.c = None
    damage = op.get("damage")
    if damage:
        c = sqlite3.connect(w.store.db_path, isolation_level=None, timeout=30)
        try:
            if damage == "marker":
                c.execute("DELETE FROM meta WHERE key='payer_spend_rollup_v1'")
            elif damage == "drop":
                c.execute("DROP TABLE payer_spend_days")
        finally:
            c.close()
    w.store = Store(w.root)
    w.attach_ledgers()
    STATS["reopen:" + str(damage)] += 1


def rebuild_wallet(w: World, env: str, account: str) -> dict:
    return CostRollup.rebuild_wallet(w.store, environment=env, account_id=account, actor="fixture-payer-oracle",
                                     reason="independent payer-spend oracle", clock=lambda: w.now)


def op_rebuild(w: World, op: dict) -> None:
    env, k = op["env"], op["k"]
    with w.store.tx() as t:
        faulted = any(malformed(f) and f.status == "settled" for f in wallet_facts(t, w.wallets[(env, k)]))
    got = outcome(lambda: rebuild_wallet(w, env, w.wallets[(env, k)]))
    if faulted:
        if got[:2] != ("refused", "cost_rollup_rebuild_refused"):
            raise Mismatch("rebuild:not_refused", {"got": _short(got)}, {"op": op})
    elif got[0] != "ok":
        raise Mismatch("rebuild:refused", {"got": _short(got)}, {"op": op})
    STATS["rebuild:" + ("refused" if faulted else "ok")] += 1


def op_malformed(w: World, op: dict) -> None:
    """A settled (or reserved) call whose created_at is not an integer >= 0: only a write outside the
    product can produce one (billing_calls.created_at has no type CHECK)."""
    env, k = op["env"], op["k"]
    created = {"text": "soon", "real": float(w.now) + 0.5, "negative": -5, "blob": sqlite3.Binary(b"\x01\x02")}[op["shape"]]
    call = w.next("sql-bad")
    w.calls[call] = {"env": env, "k": k, "kind": "sql", "ticket": None, "bad": True}
    with w.store.tx() as t:
        insert_call(t, w, call, env, k, hold=op["hold"], created_at=created, deadline=w.now + DAY, status=op["status"])
        if op["status"] == "settled":
            f = settlement_facts(w, call, env, k, min(op["amount"], balance(t, w.wallets[(env, k)])), w.now)
            write_part(t, w, f, "J")
            write_part(t, w, f, "S")
    STATS["malformed:" + op["shape"] + ":" + op["status"]] += 1


OPS = {"reserve": op_reserve, "attempt": op_attempt, "settle": op_settle, "sql_call": op_sql_call,
       "sql_settle": op_sql_settle, "drain": op_drain, "release": op_release, "expire": op_expire, "clock": op_clock,
       "policy": op_policy, "reopen": op_reopen, "rebuild": op_rebuild, "malformed": op_malformed}


def run_op(w: World, op: dict) -> None:
    OPS[op["kind"]](w, op)


# ================================================================================= choosing operations
KINDS = (["reserve"] * 6 + ["attempt"] * 5 + ["settle"] * 2 + ["sql_call"] * 4 + ["sql_settle"] * 4 + ["drain"] * 3
         + ["release"] * 2 + ["expire"] * 2 + ["clock"] * 4 + ["policy"] * 2 + ["reopen"] * 1 + ["rebuild"] * 1)


def choose_op(w: World, rng: random.Random, anchor: int, kind: str | None = None) -> dict:
    env, k = rng.choice(ENVIRONMENTS), rng.randrange(2)
    kind = kind or rng.choice(KINDS)
    reserved = reserved_calls(w)
    if kind == "reserve":
        hold = rng.choice((0, 0, 1, 1, 2**40, 2**62, rng.randint(0, 10**6), rng.randint(0, 10**12)))
        return {"kind": kind, "env": env, "k": k, "hold": hold, "ttl": rng.choice((1, 60, 3600, DAY - 1, DAY, rng.randint(1, DAY)))}
    if kind in ("attempt", "settle"):
        candidates = [c for c in reserved if c["kind"] == "api" and not c.get("settling")
                      and (c["ticket"] is None) == (kind == "attempt")]
        if not candidates and kind == "settle":
            candidates = [c for c, i in w.calls.items() if i["ticket"] is not None]
            candidates = [{"id": c} for c in candidates]
        if not candidates:
            return choose_op(w, rng, anchor, "reserve")
        call = rng.choice(candidates)["id"]
        charge = rng.choice((0, 1, 7, 10**12, rng.randint(0, 10**6), rng.randint(0, 10**12)))
        op = {"kind": kind, "call": call, "charge": charge, "outcome": rng.choice(("completed",) * 5 + ("platform_fault",))}
        if kind == "attempt":
            op["settle"] = rng.random() < 0.6
        else:
            op["late"] = rng.random() < 0.4
        return op
    if kind == "sql_call":
        created = rng.choice((w.now, w.now - 1, w.now + 1, anchor - 1, anchor, anchor + 1, anchor - DAY, anchor - 7 * DAY,
                              anchor - 100 * DAY, w.now + 10 * DAY, rng.randint(anchor - 40 * DAY, anchor + 40 * DAY)))
        status = rng.choice(("reserved", "settled", "settled"))
        return {"kind": kind, "env": env, "k": k, "hold": rng.choice((0, 1, 2**40, rng.randint(0, 10**6))),
                "created_at": created, "deadline": created + rng.randint(1, DAY), "status": status,
                "amount": pick_amount(rng, INT64), "arrival": rng.choice(("now", "now", "now", "later", "never"))}
    if kind == "sql_settle":
        candidates = [c for c in reserved if c["ticket"] is None and not c.get("settling")]
        if not candidates:
            return choose_op(w, rng, anchor, "sql_call")
        return {"kind": kind, "call": rng.choice(candidates)["id"], "order": rng.choice(sorted(ORDERS)),
                "amount": pick_amount(rng, INT64), "arrival": rng.choice(("now", "now", "later", "later", "never"))}
    if kind == "drain":
        return {"kind": kind, "index": rng.randrange(1000)}
    if kind == "release":
        if not reserved:
            return choose_op(w, rng, anchor, "reserve")
        return {"kind": kind, "call": rng.choice(reserved)["id"]}
    if kind == "expire":
        with w.store.tx() as t:
            row = t.execute("SELECT MIN(c.deadline) FROM billing_calls c JOIN billing_runs r ON r.id=c.run_key "
                            "WHERE c.status='reserved' AND r.namespace=? AND typeof(c.deadline)='integer'",
                            (API_NAMESPACE,)).fetchone()
        at = max(w.now, (row[0] if row and row[0] is not None else w.now)) + rng.choice((0, 1, 3600))
        return {"kind": kind, "at": at}
    if kind == "clock":
        back = rng.random() < 0.25
        delta = rng.choice((1, 60, 3600, DAY - 1, DAY, DAY + 1, rng.randint(0, 3 * DAY)))
        at = max(anchor - 9 * DAY, w.now - delta) if back else w.now + delta
        return {"kind": kind, "at": at, "back": back}
    if kind == "policy":
        with w.store.tx() as t:
            expect = oracle_exposure(wallet_facts(t, w.wallets[(env, k)]), w.now)
        budgets = {}
        for name in PERIODS:
            total = (expect.periods[name]["spent"] + expect.periods[name]["held"]) if expect.periods else 0
            budgets[name] = min(INT64, max(0, rng.choice((total, total + 1, total - 1, INT64, rng.randint(0, 2 * total + 10)))))
        return {"kind": kind, "env": env, "k": k, "enabled": rng.random() < 0.85, **budgets}
    if kind == "reopen":
        return {"kind": kind, "damage": rng.choice((None, None, "marker", "drop"))}
    if kind == "rebuild":
        return {"kind": kind, "env": env, "k": k}
    if kind == "malformed":
        return {"kind": kind, "env": env, "k": k, "shape": rng.choice(("text", "real", "negative", "blob")),
                "status": rng.choice(("settled", "settled", "reserved")), "hold": rng.choice((0, 1, 5)),
                "amount": pick_amount(rng, INT64)}
    raise ValueError(kind)


# ================================================================================= after every step
class _Discard(Exception):
    pass


def broken_calls(t, w: World) -> dict:
    """Per wallet scope, the ids of its settled calls with a malformed created_at."""
    out = {}
    for (env, k), account in w.wallets.items():
        ids = {f.id for f in wallet_facts(t, account) if f.status == "settled" and malformed(f)}
        if ids:
            out[(env, "", account)] = ids
    return out


def check_verify(w: World, spec: dict) -> None:
    """CostRollup.check over every day is clean, except that a wallet with a malformed settled call
    is reported as exactly that (and only it)."""
    report = CostRollup.check(w.store, now=w.now, all=True)
    with w.store.tx() as t:
        broken = broken_calls(t, w)
    if report.issues:
        raise Mismatch("verify:issues", report.summary(), spec)
    for scope in report.mismatched:
        result = report.all_scopes[scope]
        ids = broken.get(tuple(scope))
        if not ids or result.count != len(ids) or any(
                d.get("check") != "fact_fault" or not any(i in json.dumps(d, default=str) for i in ids)
                for d in result.differences):
            raise Mismatch("verify", {"scope": list(scope), "summary": report.summary()}, spec)
        STATS["verify:fact_fault"] += 1
    STATS["verify:ok"] += 1


def day_rows(t, account: str) -> list:
    return [tuple(r) for r in t.execute("SELECT * FROM payer_spend_days WHERE account_id=? ORDER BY start", (account,))]


def check_rebuild(w: World, env: str, k: int, spec: dict) -> None:
    """A rebuild of the wallet, rolled back, leaves its day rows identical; a malformed settled call
    makes it refuse without a change."""
    account = w.wallets[(env, k)]
    seen = {}
    try:
        with w.store.tx() as t:
            seen["faulted"] = any(malformed(f) and f.status == "settled" for f in wallet_facts(t, account))
            seen["before"] = day_rows(t, account)
            seen["got"] = outcome(lambda: rebuild_wallet(w, env, account))
            seen["after"] = day_rows(t, account)
            raise _Discard
    except _Discard:
        pass
    got = seen["got"]
    STATS["rebuild_rolled_back:" + ("refused" if seen["faulted"] else "ok")] += 1
    if seen["faulted"]:
        if got[:2] != ("refused", "cost_rollup_rebuild_refused"):
            raise Mismatch("rebuild:not_refused", {"got": _short(got)}, spec)
        return
    if got[0] != "ok":
        raise Mismatch("rebuild:refused", {"got": _short(got)}, spec)
    if seen["before"] != seen["after"]:
        raise Mismatch("rebuild:rows", {"live": _short(seen["before"][:8]), "rebuilt": _short(seen["after"][:8])}, spec)


def instants(rng: random.Random, now: int) -> list[int]:
    marks = {now, now - 1, now + 1, (now // DAY) * DAY, (now // DAY) * DAY - 1, (now // DAY + 1) * DAY, (now // DAY + 1) * DAY - 1}
    day = ((now - 35 * DAY) // DAY) * DAY
    while day <= now + 35 * DAY:
        w = oracle_windows(day)
        if day in (w["weekly"][0], w["monthly"][0]):
            marks.update((day - 1, day, day + 1))
        day += DAY
    chosen = rng.sample(sorted(marks), min(5, len(marks)))
    chosen.append(rng.randint(now - 35 * DAY, now + 35 * DAY))
    return [now] + [at for at in chosen if at != now]


def compare_all(w: World, rng: random.Random, spec: dict) -> None:
    legacy = LegacyPayerBudgets()
    with w.store.tx() as t:
        for (env, k), account in w.wallets.items():
            facts = wallet_facts(t, account)
            pb = w.budgets(env)
            policy = pb._policy(t, w.users[k], UNIT.code)
            for at in instants(rng, w.now):
                here = {**spec, "env": env, "k": k, "at": at}
                expect = oracle_exposure(facts, at)
                windows = pb._windows(at)
                if windows != oracle_windows(at):
                    raise Mismatch("windows", {"product": windows, "oracle": oracle_windows(at)}, here)
                compare_exposure("rollup", here, expect, outcome(lambda: pb._exposure(t, account, windows)))
                compare_legacy(here, expect, outcome(lambda: legacy._exposure(t, account, windows)))
                compare_exposure("reference", here, oracle_exposure(facts, at, cap=None),
                                 outcome(lambda: spend_rollup.reference_exposure(t, account=account, windows=windows)))
                amount = rng.choice((0, 1, rng.randint(0, 10**6)))
                expected = oracle_check(expect, policy, amount)
                got = outcome(lambda: pb.check(t, account, amount=amount, at=at))
                got = ("ok", None) if got[0] == "ok" else got[:2]
                STATS["check:" + (expected[1] or "ok")] += 1
                if got != expected:
                    raise Mismatch("check", {"amount": amount, "expected": expected, "got": _short(got)}, here)


def run_history(root: Path, seed: int, *, steps: int, fault: bool) -> World:
    rng = random.Random(seed)
    name, anchor = ANCHORS[seed % len(ANCHORS)]
    w = make_world(root, anchor)
    w.clock[0] = anchor - rng.randint(0, 2 * DAY)
    fault_step = rng.randrange(0, max(1, steps - 2)) if fault else None
    bad_call = None
    try:
        for step in range(steps):
            forced = None
            if step == fault_step:
                forced = "malformed"
            op = choose_op(w, rng, anchor, forced)
            if forced == "malformed" and op["status"] == "reserved":
                bad_call = (step, w.seq + 1)           # the call id op_malformed will give it
            if bad_call and step == bad_call[0] + 2:
                call = f"sql-bad-{bad_call[1]}"
                if w.calls.get(call) and reserved_calls(w) and any(c["id"] == call for c in reserved_calls(w)):
                    op = ({"kind": "sql_settle", "call": call, "order": "product", "amount": 3, "arrival": "now"}
                          if rng.random() < 0.7 else {"kind": "release", "call": call})
            spec = {"seed": seed, "anchor": name, "step": step, "op": op}
            run_op(w, op)
            w.trace.append(op)
            check_verify(w, spec)
            if "env" in op:
                check_rebuild(w, op["env"], op["k"], spec)
            elif "call" in op and op["call"] in w.calls:
                check_rebuild(w, w.calls[op["call"]]["env"], w.calls[op["call"]]["k"], spec)
            compare_all(w, rng, spec)
    except Mismatch as exc:
        raise Mismatch(exc.signature, {**exc.detail, "trace": w.trace[-12:]}, {**exc.spec, "seed": seed, "anchor": name}) from exc
    return w


# ================================================================================= tests
def test_legacy_fixture_is_verbatim():
    """The fixture's ``_exposure`` is the 0bcce176 method, byte for byte (its CAPACITY too)."""
    root = Path(__file__).resolve().parents[2]
    shown = subprocess.run(["git", "-C", str(root), "show", f"{BASE_COMMIT}:packages/core/synwevo_core/billing/payer_budgets.py"],
                           capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip(f"{BASE_COMMIT} is not in this checkout")

    def body(src):
        return re.search(r"    def _exposure\(self, t, account, windows\):\n(.*?)(?=\n    def |\Z)", src, re.S).group(1).rstrip()

    copy = (root / "tests/integration/legacy_cost_counters.py").read_text()
    assert body(copy.split("class LegacyPayerBudgets")[1]) == body(shown.stdout)
    assert LegacyPayerBudgets.CAPACITY == PayerBudgets.CAPACITY == CAP


@pytest.mark.parametrize("at", sorted({a + d for _, a in ANCHORS for d in (-1, 0, 1)}
                                      | {utc("2036-02-29 00:00:00") + d for d in (-1, 0, 1, DAY - 1, DAY)}
                                      | {utc("2034-12-31 23:59:59"), utc("2034-05-07 23:59:59"), utc("2034-05-08 00:00:00"),
                                         START, 0, 86_399, 86_400, 3 * DAY, 4 * DAY}))
def test_windows_agree_with_the_oracle_at_calendar_edges(at):
    product = PayerBudgets(SimpleNamespace(store=None, environment="test"))._windows(at)
    assert product == oracle_windows(at)
    for name, (start, end) in product.items():
        assert start <= at < end and start % DAY == 0 and end % DAY == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_histories_agree(tmp_path, seed):
    run_history(tmp_path / "payer-oracle", seed, steps=STEPS, fault=False)


@pytest.mark.parametrize("seed", range(10_001, 10_001 + FAULT_SEEDS))
def test_a_malformed_settled_call_latches_only_its_wallet(tmp_path, seed):
    w = run_history(tmp_path / "payer-oracle-fault", seed, steps=max(STEPS, 4), fault=True)
    with w.store.tx() as t:
        broken = broken_calls(t, w)
    if not broken:            # reserved and then released: no fault, as the addendum says
        STATS["fault_history:no_fault"] += 1
        return
    # Reopening keeps the fault; the wallet keeps refusing; a rebuild keeps refusing.
    op_reopen(w, {"kind": "reopen", "damage": None})
    for (env, _, account), ids in broken.items():
        pb = w.budgets(env)
        with w.store.tx() as t:
            got = outcome(lambda: pb._exposure(t, account, pb._windows(w.now)))
        assert got[:2] == ("refused", "payer_budget_inconsistent"), got
        got = outcome(lambda: rebuild_wallet(w, env, account))
        assert got[:2] == ("refused", "cost_rollup_rebuild_refused"), got
    STATS["fault_history:latched"] += 1


def bulk_calls(w: World, env: str, k: int, n: int, *, status: str, created_at: int, amount: int = 1, hold: int = 0) -> None:
    """n calls of one wallet in one statement each; settled ones with journal and settlement rows."""
    account, run, p = w.wallets[(env, k)], w.sql_runs[env], w.next("bulk")
    with w.store.tx() as t:
        t.execute("CREATE TEMP TABLE IF NOT EXISTS fixture_oracle_i(i INTEGER PRIMARY KEY)")
        t.execute("DELETE FROM fixture_oracle_i")
        t.executemany("INSERT INTO fixture_oracle_i VALUES (?)", [(i,) for i in range(n)])
        t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,"
                  "status,created_at) SELECT ?||'-c-'||i,?,?||'-c-'||i,?,?,'fixture-sale-v1',?,?,?,? FROM fixture_oracle_i",
                  (p, run, p, call_context(w, k), account, hold, created_at + DAY, status, created_at))
        if status != "settled":
            return
        t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                  "requested_model,cost_price_id,status,created_at,dispatched_at) SELECT ?||'-a-'||i,?||'-c-'||i,?||'-a-'||i,"
                  "'fixture-payer-oracle',?||'-f-'||i,?,?,'fixture-provider','fixture-model','fixture-cost-v1','settled',?,? "
                  "FROM fixture_oracle_i", (p, p, p, p, created_at, env, created_at, created_at))
        t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,absorbed_d,"
                  "late,created_at) SELECT ?||'-a-'||i,'{}','completed','0','1',?,?,'0','1',0,? FROM fixture_oracle_i",
                  (p, COST_UNIT.code, amount, created_at))
        t.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,credit_account,amount,"
                  "kind,reference,created_at) SELECT ?||'-j-'||i,?,?,?||'-v-'||i,?,?,?,?,'sale',?||'-c-'||i,? FROM fixture_oracle_i",
                  (p, env, SQL_NAMESPACE, p, UNIT.code, account, w.revenue[env], amount, p, created_at))
        t.execute("INSERT INTO billing_settlements SELECT ?||'-c-'||i,?||'-a-'||i,?||'-j-'||i,?,'1','0','1','0','1',? "
                  "FROM fixture_oracle_i", (p, p, p, str(amount), created_at))


def exposure_of(w: World, env: str, k: int, at: int) -> tuple:
    """(expect, rollup outcome, legacy outcome) at ``at`` for one wallet."""
    account, pb = w.wallets[(env, k)], w.budgets(env)
    with w.store.tx() as t:
        expect = oracle_exposure(wallet_facts(t, account), at)
        windows = pb._windows(at)
        assert windows == oracle_windows(at)
        rollup = outcome(lambda: pb._exposure(t, account, windows))
        legacy = outcome(lambda: LegacyPayerBudgets()._exposure(t, account, windows))
        compare_exposure("reference", {"env": env, "k": k, "at": at}, oracle_exposure(wallet_facts(t, account), at, cap=None),
                         outcome(lambda: spend_rollup.reference_exposure(t, account=account, windows=windows)))
        check = outcome(lambda: pb.check(t, account, amount=1, at=at))
    return expect, rollup, legacy, ("ok", None) if check[0] == "ok" else check[:2]


def test_12000_settled_calls_this_month_legacy_refuses_capacity_and_the_rollup_equals_the_oracle(tmp_path):
    anchor = ANCHORS[2][1]
    w = make_world(tmp_path / "payer-oracle-capacity", anchor)
    w.clock[0] = anchor + 3 * DAY + 5
    env, k = "test", 0
    bulk_calls(w, env, k, 12_000, status="settled", created_at=anchor + DAY, amount=7)
    bulk_calls(w, env, k, 3, status="reserved", created_at=anchor + 2 * DAY, hold=11)
    run_op(w, {"kind": "reserve", "env": env, "k": k, "hold": 2**40, "ttl": 3600})     # the product admits (no cap on volume)
    spec = {"test": "capacity", "step": 0}
    check_verify(w, spec)
    check_rebuild(w, env, k, spec)
    for at in (w.now, anchor - 1, anchor + DAY - 1, anchor + DAY, anchor + 40 * DAY):
        expect, rollup, legacy, check = exposure_of(w, env, k, at)
        here = {**spec, "at": at}
        compare_exposure("rollup", here, expect, rollup)
        compare_legacy(here, expect, legacy)
        assert check == oracle_check(expect, w.budgets(env)._policy(w.store._c(), w.users[k], UNIT.code), 1)
        if at < anchor + 35 * DAY:    # the calls are in or ahead of the periods: no upper bound (addendum §1)
            assert expect.candidates == 12_004 > CAP and legacy[:2] == ("refused", "payer_budget_capacity")
            assert rollup[1]["monthly"]["spent"] == 12_000 * 7 and rollup[1]["monthly"]["held"] == 33 + 2**40
        else:                         # June: the calls are before every period start, only the holds remain
            assert expect.candidates == 4 and legacy[0] == "ok" and rollup[1]["monthly"]["spent"] == 0
    STATS["capacity:settled"] += 1


def test_10000_reserved_calls_admit_and_10001_refuse_capacity_everywhere(tmp_path):
    anchor = ANCHORS[0][1]
    w = make_world(tmp_path / "payer-oracle-holds", anchor)
    w.clock[0] = anchor + 5
    env, k = "live", 1
    bulk_calls(w, env, k, 9_999, status="reserved", created_at=anchor - 20 * DAY, hold=3)
    run_op(w, {"kind": "reserve", "env": env, "k": k, "hold": 1, "ttl": DAY})            # the 10,000th hold admits
    spec = {"test": "holds", "step": 0}
    check_verify(w, spec)
    expect, rollup, legacy, check = exposure_of(w, env, k, w.now)
    assert expect.code is None and expect.candidates == 10_000 and expect.periods["daily"]["held"] == 9_999 * 3 + 1
    compare_exposure("rollup", spec, expect, rollup)
    compare_legacy(spec, expect, legacy)
    assert check == ("ok", None)
    bulk_calls(w, env, k, 1, status="reserved", created_at=anchor - 20 * DAY, hold=3)
    check_verify(w, spec)
    check_rebuild(w, env, k, spec)
    expect, rollup, legacy, check = exposure_of(w, env, k, w.now)
    assert expect.code == "payer_budget_capacity" and expect.candidates == 10_001
    compare_exposure("rollup", spec, expect, rollup)
    compare_legacy(spec, expect, legacy)
    assert check == ("refused", "payer_budget_capacity")
    run_op(w, {"kind": "reserve", "env": env, "k": k, "hold": 1, "ttl": DAY})            # the product refuses too (agree)
    assert STATS["reserve:payer_budget_capacity"] >= 1
    # Another wallet of the same owner in the other environment is unaffected.
    expect, rollup, legacy, check = exposure_of(w, "test", k, w.now)
    assert expect.code is None and rollup[0] == "ok" and check == ("ok", None)
    STATS["capacity:holds"] += 1


def test_check_refuses_a_wallet_of_another_environment_before_reading_it(tmp_path):
    w = make_world(tmp_path / "payer-oracle-scope", ANCHORS[1][1])
    with w.store.tx() as t:
        got = outcome(lambda: w.budgets("live").check(t, w.wallets[("test", 0)], amount=0, at=w.now))
        assert got[:2] == ("refused", "payer_budget_inconsistent")
        got = outcome(lambda: w.budgets("test").check(t, w.wallets[("test", 0)], amount=0, at=w.now))
        assert got[0] == "ok"


def test_zz_the_generated_histories_covered_the_cases():
    """Runs last (file order): the random histories must have exercised what the contract names."""
    if not STATS["verify:ok"]:
        pytest.skip("no history ran in this session")
    needed = ("rollup:payer_budget_inconsistent:unpaid", "rollup:payer_budget_inconsistent:fault", "legacy:capacity",
              "legacy:deviation", "sql_settle:journal_last", "sql_settle:status_first", "sql_settle:status_last",
              "sql_settle:product", "late_settlement:JS", "late_settlement:J", "late_settlement:T", "settle:completed",
              "settle:completed:late", "settle:platform_fault", "clock:back", "reopen:marker", "reopen:drop", "rebuild:ok",
              "rebuild_rolled_back:ok", "rebuild_rolled_back:refused", "verify:fact_fault", "expired",
              "malformed:text:settled", "malformed:real:settled", "malformed:negative:settled", "malformed:blob:settled",
              "fault_history:latched", "capacity:settled", "capacity:holds")
    missing = [key for key in needed if not STATS[key]]
    exceeded = sum(v for key, v in STATS.items() if key.endswith("_budget_exceeded"))
    print("payer-spend oracle coverage:", json.dumps(dict(sorted(STATS.items()))))
    assert not missing and exceeded, {"missing": missing, "budget_refusals": exceeded, "stats": dict(sorted(STATS.items()))}
