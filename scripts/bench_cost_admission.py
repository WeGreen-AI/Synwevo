#!/usr/bin/env python3
"""Benchmark the platform cost admission on the exact cost rollup (docs/billing-ledger.md).

    python scripts/bench_cost_admission.py                       # 200k and 1M admissions, JSON to stdout
    python scripts/bench_cost_admission.py --sizes 200000 --out FILE

Everything happens in a NEW, isolated temporary Store, made fresh inside ``--work`` (default: the
system temporary directory) and removed afterwards; the script refuses the repository's ``data/``,
the configured ``SYNWEVO_DATA_DIR`` and any directory that already holds a store. No network, no keys, no real prices: synthetic fixture tariffs, users and
wallets, written through the real Ledger and CostAdmission where timing matters, and cloned with
plain bulk SQL (the triggers see exactly what a bulk writer would write) for the history.

Workload per size N: N admissions between 2034-05-01T00:00 (a Monday, MONTH) and 2034-05-20T12:00
(a Saturday, NOW), all in the month, 95 % settled at random on-grid costs; 2,000 local stops in the
last 24 h (holds still reserved); 300 open unknowns in flight; the rest closed unknowns; 2,000 payer
wallets; 21 routes, one of them hot (30 %), with payer and route velocity windows on. The result's
``history`` gives the first and last admission and how many fall in the month. Then 500 real
dispatches (reserve, prepare, dispatch with admission under FULL durability, settle).

Measured: ``_counters`` + ``RiskControls.guard`` inside BEGIN IMMEDIATE (p50/p95/max), the real
dispatch, settlement with and without the usage trigger, the trigger cost per admission+usage pair
on the live write path (``triggers_live``: one admission, then its usage, each in its own FULL
transaction, for distinct payers, routes and seconds, interleaved with a copy of the store whose
producer triggers are dropped) and amortized over one bulk statement (``triggers``), the backfill
(replay), and the verifier as the risk monitor runs it (``verify_daily``: from the previous month's
first day, here the whole history; ``verify_recent_2d``) and over every day (``verify_all``), each
daily/recent one in a child process with its peak RSS; the EXPLAIN QUERY PLAN of every cost
statement on the admission path and its rows_read. Targets (contract §12): counters+guard p95 <= 3 ms
and the 1M run within 1.5x of 200k; dispatch p95 < 20 ms; triggers <= 300 us per pair (live path);
settlement <= 1 ms extra; backfill and daily verify <= 15 s.

A month-end daily verify reads two months of facts (at this density about 3x the 19.5-day history
here): its time grows linearly with the facts it reads; its memory does not (it streams).

    python scripts/bench_cost_admission.py --sizes '' --wallet-sizes 200000,1000000   # the personal limits only

The personal limits (cost-capacity phase 2, billing/spend_rollup.py), per size N in ``--wallet-sizes``: ONE
wallet with N settled calls between MONTH and NOW, written in the product's order (journal entry,
settlement, then the call's status) with plain bulk SQL, at random amounts, plus 300 still-reserved holds.
Measured: ``PayerBudgets.check`` inside BEGIN IMMEDIATE (p50/p95/max) with the EXPLAIN QUERY PLAN of its
statements, the trigger cost per settlement on the live write path (``triggers_live``: one settlement's
journal, settlement and status writes per FULL transaction, A/B against a copy without the spend
triggers), the backfill (replay of every settled call) and the verifier as the risk monitor runs it.
Targets (phase-2 addendum §9): check p95 <= 1 ms and the 1M run within 1.5x of 200k; triggers <= 150 us
per settlement; backfill and verify times recorded.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__" and not sys.flags.safe_path:
    # Started as `python scripts/bench_cost_admission.py`, this directory would come first on the
    # import path and scripts/platform.py would shadow the standard library's `platform` for every
    # import below. -P (Python 3.11+) leaves the script's directory out instead.
    os.execv(sys.executable, [sys.executable, "-P", str(Path(__file__).resolve()), *sys.argv[1:]])
START = 2_000_000_000
MONTH = 2_030_054_400          # 2034-05-01T00:00:00Z (a Monday)
NOW = 2_031_739_200            # 2034-05-20T12:00:00Z (a Saturday)
DAY = 86_400
ROUTES = ["bench-hot"] + [f"bench-route-{i}" for i in range(20)]


def refuse_real_data(work: Path) -> None:
    """Never the repository's data/ nor the configured data directory, never an existing store."""
    resolved = work.resolve()
    forbidden = [(ROOT / "data").resolve()]
    for key in ("SYNWEVO_DATA_DIR", "DOCWEAVE_DATA_DIR"):
        if os.environ.get(key):
            forbidden.append(Path(os.environ[key]).expanduser().resolve())
    for bad in forbidden:
        if resolved == bad or bad in resolved.parents:
            raise SystemExit(f"refusing {resolved}: the benchmark never touches {bad}")
    if (resolved / "docweave.sqlite3").exists():
        raise SystemExit(f"refusing {resolved}: it already holds a store; the benchmark builds its own")


def stats(samples_ms):
    out = sorted(samples_ms)
    return {"n": len(out), "p50": round(statistics.median(out), 3),
            "p95": round(out[max(0, int(0.95 * len(out)) - 1)], 3), "max": round(out[-1], 3)}


class Bench:
    def __init__(self, work: Path, size: int, seed: int):
        from synwevo_core.billing import (ChargeContext, Ledger, PaymentReceipt, PriceSnapshot, Rate, Unit, Usage,
                                         UsageEvidence)
        from synwevo_core.billing.admission import CostAdmission, CostPolicy
        from synwevo_core.billing.risk import RiskControls, RiskPolicy
        from synwevo_core.identity import Identity
        from synwevo_core.store import Store

        self.mods = dict(ChargeContext=ChargeContext, Usage=Usage, UsageEvidence=UsageEvidence, RiskControls=RiskControls)
        self.size, self.rng, self.work = size, random.Random(seed), work
        self.store = Store(work)
        self.ticks = [MONTH - DAY]
        self.unit, self.cost_unit = Unit("BENCH-CREDIT", 6), Unit("BENCH-COST", 8)
        self.ledger = Ledger(self.store, environment="test", namespace="bench", authorize=lambda t, c, a: True,
                             verify_payment=lambda t, r: True, clock=lambda: self.ticks[0], max_hold_seconds=DAY)
        sale = PriceSnapshot("bench-sale", "sale", self.unit, "bench-provider", "bench-model", Rate(1), Rate(1),
                             Rate(2), "bench-terms", MONTH - 2 * DAY, per_tokens=1)
        from dataclasses import replace
        self.ledger.publish_price(sale)
        self.ledger.publish_price(replace(sale, price_id="bench-cost", purpose="cost", unit=self.cost_unit,
                                          input_uncached=Rate(3), input_cached=Rate(1), output=Rate(4)))
        identity = Identity(self.store)
        self.user = identity.create_user("bench-payer@example.test", None, status="active",
                                         with_personal_workspace=False)["id"]
        self.ledger.create_payment_order(order_id="bench-order", payer_user_id=self.user, provider="bench-payments",
                                         merchant_id="bench-merchant", amount_minor=100, currency="XTS",
                                         unit=self.unit, credit_units=10**12, exchange_version="bench-exchange")
        self.ledger.credit_verified_payment(PaymentReceipt("test", "bench-payments", "bench-merchant", "bench-payment",
                                                           "bench-event", "bench-order", 100, "XTS", "bench-inbox",
                                                           "b" * 64))
        # One run for the template of the cloned history, one for the real calls (a run caps its calls).
        self.history = ChargeContext(self.user, self.user, "bench-history", "bench-approval", "bench-workspace")
        self.ctx = ChargeContext(self.user, self.user, "bench-run", "bench-approval", "bench-workspace")
        for ctx in (self.history, self.ctx):
            self.ledger.open_run(ctx, unit=self.unit, budget=10**12, max_calls=10_000, max_attempts=10_000,
                                 deadline=NOW + 30 * DAY)
        top = 2**62
        risk = RiskPolicy(allowlist_only=False, payer_window_s=3600, payer_window_cost=top, route_window_s=DAY,
                          route_window_cost=top, balance_warn=None, balance_close=None, balance_poll_s=300,
                          reconcile_tolerance=0, pending_alert=None)
        self.policy = CostPolicy("bench-policy", "bench-account", "bench-provider", self.cost_unit, True, top, top, top,
                                 top, 10_000, 10_000, risk=risk)
        self.admission = CostAdmission(self.ledger, current_policy=lambda: self.policy)
        self.calls = 0

    # ------------------------------------------------------------ real calls
    def prepare(self, amount=1, *, deadline_s=3600, ctx=None):
        self.calls += 1
        name = f"real-{self.calls}"
        call = self.ledger.reserve_call(ctx or self.ctx, logical_call_id=name, amount=amount, price_id="bench-sale",
                                        deadline=self.ticks[0] + deadline_s)
        return call, self.ledger.prepare_attempt(call, attempt_id=name, owner="bench-worker", cost_price_id="bench-cost",
                                                 requested_model="bench-hot")

    def dispatch(self, ticket, amount=50):
        self.ledger.dispatch(ticket, admission=lambda t, a: self.admission.admit(
            t, a, amount=amount, expires_at=self.ticks[0] + 120))

    def settle(self, ticket):
        Usage, UsageEvidence = self.mods["Usage"], self.mods["UsageEvidence"]
        return self.ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence(
            "bench-provider", "req-" + ticket.attempt_key, "final-" + ticket.attempt_key, Usage(2, 1, 3, 1),
            "bench-model", "bench-capability", self.ticks[0]))

    # ------------------------------------------------------------ bulk history
    def build(self):
        store, rng = self.store, self.rng
        self.ticks[0] = MONTH
        call, ticket = self.prepare(amount=0, ctx=self.history)
        self.ledger.dispatch(ticket)
        self.template = (call, ticket.attempt_key)
        account = store.one("SELECT * FROM billing_accounts WHERE id=(SELECT account_id FROM billing_calls WHERE id=?)",
                            (call,))
        owners = [f"bench-payer-{i}" for i in range(2000)]
        with store.tx() as t:
            t.executemany("INSERT INTO billing_accounts(id,environment,unit,owner,kind) VALUES (?,?,?,?,?)",
                          [(f"bench-wallet-{o}", account["environment"], account["unit"], o, account["kind"])
                           for o in owners])
            t.execute("CREATE TEMP TABLE bench_rows(i INTEGER PRIMARY KEY, at INTEGER, account TEXT, route TEXT, "
                      "kind TEXT, reserved INTEGER, expires INTEGER, deadline INTEGER, n TEXT, d TEXT)")
        n = self.size
        stops, opens = 2000, 300
        kinds = (["stop"] * stops + ["open"] * opens + ["settled"] * int(0.95 * n))
        kinds += ["closed"] * (n - len(kinds))
        rng.shuffle(kinds)
        started = time.perf_counter()
        batch = 20_000
        for first in range(0, n, batch):
            rows = []
            for i in range(first, min(n, first + batch)):
                kind = kinds[i]
                if kind == "stop":
                    at = rng.randint(NOW - DAY + 600, NOW - 600)
                elif kind == "open":
                    at = rng.randint(NOW - 600, NOW - 1)
                else:
                    at = rng.randint(MONTH, NOW - 1)
                expires = NOW + 300 if kind == "open" else at + 120
                deadline = min(at + DAY, NOW + 3600) if kind in ("stop", "open") else at + 900
                cost = Fraction(rng.randint(1, 10**11), 10**6)
                # The real payer's own wallet is a hot payer (5 %), so its velocity reads are dense too.
                wallet = account["id"] if rng.random() < 0.05 else f"bench-wallet-{rng.choice(owners)}"
                rows.append((i, at, wallet,
                             "bench-hot" if rng.random() < 0.3 else rng.choice(ROUTES[1:]), kind,
                             rng.randint(1000, 200_000), expires, deadline, str(cost.numerator), str(cost.denominator)))
            with store.tx() as t:
                t.execute("DELETE FROM bench_rows")
                t.executemany("INSERT INTO bench_rows VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
                t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,"
                          "deadline,status,created_at) SELECT 'c'||b.i,c.run_key,'l'||b.i,c.context,b.account,c.price_id,0,"
                          "b.deadline,CASE b.kind WHEN 'settled' THEN 'settled' WHEN 'closed' THEN 'released' "
                          "ELSE 'reserved' END,b.at FROM bench_rows b, billing_calls c WHERE c.id=?", (call,))
                t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                          "requested_model,cost_price_id,status,created_at,dispatched_at) SELECT 'a'||b.i,'c'||b.i,"
                          "'l'||b.i,a.owner,'f'||b.i,a.lease_until,a.environment,a.provider,b.route,a.cost_price_id,"
                          "CASE b.kind WHEN 'settled' THEN 'settled' ELSE 'usage_pending' END,b.at,b.at "
                          "FROM bench_rows b, billing_attempts a WHERE a.id=?", (ticket.attempt_key,))
                t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
                          "reserved_units,policy_revision,limits_json,admitted_at,expires_at) SELECT 'a'||b.i,'test',"
                          "'bench-provider','bench-account',?,b.reserved,'bench-policy','{}',b.at,b.expires "
                          "FROM bench_rows b ORDER BY b.at", (self.cost_unit.code,))
                t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                          "absorbed_d,late,created_at) SELECT 'a'||b.i,'{}','completed',b.n,b.d,?,0,'0','1',0,b.at "
                          "FROM bench_rows b WHERE b.kind='settled'", (self.cost_unit.code,))
                t.execute("INSERT INTO model_cost_endings(attempt_key,ending,recorded_at) SELECT 'a'||b.i,"
                          "'stopped_locally',b.at+60 FROM bench_rows b WHERE b.kind='stop'")
        self.build_s = time.perf_counter() - started
        self.ticks[0] = NOW

    def counts(self):
        one = lambda sql: self.store.one(sql.replace("{real}", "environment<>''"))["n"]  # noqa: E731  (no sentinels)
        return {"admissions": one("SELECT COUNT(*) AS n FROM model_cost_admissions"),
                "usage": one("SELECT COUNT(*) AS n FROM billing_usage"),
                "pending": one("SELECT COUNT(*) AS n FROM model_cost_pending"),
                "endings": one("SELECT COUNT(*) AS n FROM model_cost_endings"),
                "buckets": one("SELECT COUNT(*) AS n FROM model_cost_buckets WHERE {real}"),
                "day_buckets": one("SELECT COUNT(*) AS n FROM model_cost_buckets WHERE dim='account'"),
                "inflight": one("SELECT COUNT(*) AS n FROM model_cost_inflight WHERE {real}"),
                "hold": one("SELECT COUNT(*) AS n FROM model_cost_hold WHERE {real}"),
                "exceptions": one("SELECT COUNT(*) AS n FROM model_cost_exceptions WHERE {real}")}

    # ------------------------------------------------------------ measurements
    def counters_and_guard(self, samples):
        RiskControls = self.mods["RiskControls"]
        call, ticket = self.prepare()
        out, read, plans = [], None, []
        guard = RiskControls(self.store, environment="test", clock=lambda: self.ticks[0])
        for k in range(samples + 1):
            with self.store.tx() as t:
                attempt = self.ledger._attempt(t, ticket.attempt_key)
                row = self.ledger._call(t, call)
                traced = []
                if k == 0:
                    t.set_trace_callback(traced.append)
                s = time.perf_counter()
                counters = self.admission._counters(t, self.policy, self.ticks[0])
                guard.guard(t, policy=self.policy, call=row, attempt=attempt, amount=1, now=self.ticks[0])
                elapsed = (time.perf_counter() - s) * 1000
                if k == 0:
                    t.set_trace_callback(None)
                    read = counters["rows_read"]
                    for sql in traced:
                        if "model_cost_" in sql and sql.lstrip().upper().startswith(("SELECT", "WITH")):
                            plans.append({"sql": sql[:240], "plan": [r[3] for r in t.execute("EXPLAIN QUERY PLAN " + sql)]})
                else:
                    out.append(elapsed)
        from synwevo_core.billing import cost_rollup
        with self.store.tx() as t:
            velocity_rows = {f"{dim}@{w}": sum(g["n"] for g in cost_rollup._bucket_sums(
                t, ("test", "bench-provider", "bench-account"), dim, member, self.ticks[0], w))
                for dim, member, w in (("payer", self.user, 3600), ("route", "bench-hot", DAY))}
        return stats(out), read, velocity_rows, plans, {k: v for k, v in counters.items() if k != "windows"}

    def dispatches(self, count):
        times, settles = [], []
        for _ in range(count):
            self.ticks[0] += 1
            _, ticket = self.prepare()
            s = time.perf_counter()
            self.dispatch(ticket)
            times.append((time.perf_counter() - s) * 1000)
            s = time.perf_counter()
            self.settle(ticket)
            settles.append((time.perf_counter() - s) * 1000)
        return stats(times), stats(settles)

    def history_span(self):
        row = self.store.one("SELECT MIN(admitted_at) AS first, MAX(admitted_at) AS last, "
                             "SUM(admitted_at>=?) AS in_month, COUNT(*) AS n FROM model_cost_admissions "
                             "WHERE environment='test' AND provider='bench-provider'", (MONTH,))
        return {"first": row["first"], "last": row["last"], "in_month": row["in_month"], "admissions": row["n"]}

    def verify_times(self):
        """As the risk monitor calls it (daily, recent), each in a child process for its peak RSS,
        and over every day (snapshot.py verify) in this process."""
        from synwevo_core.billing.cost_rollup import CostRollup
        out = {}
        for name, mode in (("verify_daily", "daily"), ("verify_recent_2d", "recent")):
            out[name] = _child_verify(self.work, mode, self.ticks[0])
        s = time.perf_counter()
        report = CostRollup.check(self.store, now=self.ticks[0], all=True)
        out["verify_all"] = {"s": round(time.perf_counter() - s, 3), "ok": report.ok, "facts": report.facts}
        return out

    def backfill_s(self):
        from synwevo_core.suite_schema.model_cost_rollup import replay
        c = self.store._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            s = time.perf_counter()
            result = replay(c)
            elapsed = time.perf_counter() - s
            c.execute("COMMIT")
        finally:
            c.close()
        return round(elapsed, 3), result["facts"]

    def trigger_overhead_live(self, pairs=2_000):
        """The trigger cost a live writer pays: per pair, one admission INSERT in its own BEGIN
        IMMEDIATE .. COMMIT and then its usage INSERT in another (as Ledger.dispatch and settle write
        them, under synchronous=FULL), each pair for its own payer wallet, route and second; on this
        store (A) and on a copy whose producer triggers are dropped (B), interleaved in random order.
        ``extra_stmt_us`` times the two statements alone, ``extra_locked_us`` the writer-lock hold."""
        call, attempt = self.template
        copy = self.work.parent / (self.work.name + "-ab")
        refuse_real_data(copy)
        copy.mkdir()
        source = self.store._connect()
        target = sqlite3.connect(copy / "docweave.sqlite3")
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        rng = random.Random(3)
        wallets = [r["id"] for r in self.store.all("SELECT id FROM billing_accounts WHERE id LIKE 'bench-wallet-%'")]
        plan = [(i, rng.choice(wallets), rng.choice(ROUTES), self.ticks[0] - 7200 + i) for i in range(pairs)]
        sides = {}
        try:
            for name, path in (("A", self.work), ("B", copy)):
                c = sqlite3.connect(path / "docweave.sqlite3", isolation_level=None, timeout=30)
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA foreign_keys=ON")
                c.execute("PRAGMA synchronous=FULL")
                if name == "B":
                    c.execute("DROP TRIGGER model_cost_admissions_rollup")
                    c.execute("DROP TRIGGER model_cost_usage_rollup")
                c.execute("BEGIN IMMEDIATE")        # each call and attempt exists first, as in a real dispatch
                for i, wallet, route, at in plan:
                    c.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,"
                              "deadline,status,created_at) SELECT ?,run_key,?,context,?,price_id,0,?,'reserved',? "
                              "FROM billing_calls WHERE id=?", (f"live-c{i}", f"live-l{i}", wallet, at + 3600, at, call))
                    c.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,"
                              "provider,requested_model,cost_price_id,status,created_at,dispatched_at) SELECT ?,?,?,owner,?,"
                              "lease_until,environment,provider,?,cost_price_id,'usage_pending',?,? FROM billing_attempts "
                              "WHERE id=?", (f"live-a{i}", f"live-c{i}", f"live-l{i}", f"live-f{i}", route, at, at, attempt))
                c.execute("COMMIT")
                sides[name] = (c, {"stmt": [], "locked": [], "admission": [], "usage": []})
            for i, _, _, at in plan:
                order = list(sides.items())
                rng.shuffle(order)
                for _, (c, samples) in order:
                    held = time.perf_counter()
                    c.execute("BEGIN IMMEDIATE")
                    s = time.perf_counter()
                    c.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
                              "reserved_units,policy_revision,limits_json,admitted_at,expires_at) VALUES (?,'test',"
                              "'bench-provider','bench-account',?,5000,'bench-policy','{}',?,?)",
                              (f"live-a{i}", self.cost_unit.code, at, at + 120))
                    admission = time.perf_counter() - s
                    c.execute("COMMIT")
                    c.execute("BEGIN IMMEDIATE")
                    s = time.perf_counter()
                    c.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,"
                              "absorbed_n,absorbed_d,late,created_at) VALUES (?,'{}','completed','12345','1000',?,0,'0',"
                              "'1',0,?)", (f"live-a{i}", self.cost_unit.code, at + 5))
                    usage = time.perf_counter() - s
                    c.execute("COMMIT")
                    samples["admission"].append(admission * 1e6)
                    samples["usage"].append(usage * 1e6)
                    samples["stmt"].append((admission + usage) * 1e6)
                    samples["locked"].append((time.perf_counter() - held) * 1e6)
        finally:
            for c, _ in sides.values():
                c.close()
            shutil.rmtree(copy, ignore_errors=True)

        def q(values, p):
            values = sorted(values)
            return round(values[max(0, int(p * len(values)) - 1)], 1)

        out = {"pairs": pairs, "per_transaction": True, "synchronous": "FULL",
               "with": {k: {"p50": q(v, .5), "p95": q(v, .95)} for k, v in sides["A"][1].items()},
               "without": {k: {"p50": q(v, .5), "p95": q(v, .95)} for k, v in sides["B"][1].items()}}
        out["extra_stmt_us_p50"] = round(out["with"]["stmt"]["p50"] - out["without"]["stmt"]["p50"], 1)
        out["extra_stmt_us_p95"] = round(out["with"]["stmt"]["p95"] - out["without"]["stmt"]["p95"], 1)
        out["extra_locked_us_p50"] = round(out["with"]["locked"]["p50"] - out["without"]["locked"]["p50"], 1)
        out["extra_locked_us_p95"] = round(out["with"]["locked"]["p95"] - out["without"]["locked"]["p95"], 1)
        return out

    def trigger_and_settlement_overhead(self, pairs=10_000, settles=200):
        """Last: drops the producer triggers in this throwaway store to measure them (A/B). The bulk
        number is the cost AMORTIZED over one INSERT..SELECT of ``pairs`` rows sharing hot buckets:
        a lower bound, not what a live writer pays (``trigger_overhead_live``)."""
        def bulk(prefix, n):
            call, attempt = self.template
            rows = [(i, self.ticks[0] - 1000 - i % 500) for i in range(n)]
            s = time.perf_counter()
            with self.store.tx() as t:
                t.execute("CREATE TEMP TABLE IF NOT EXISTS bench_ab(i INTEGER PRIMARY KEY, at INTEGER)")
                t.execute("DELETE FROM bench_ab")
                t.executemany("INSERT INTO bench_ab VALUES (?,?)", rows)
                t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,"
                          "deadline,status,created_at) SELECT ?||'c'||i,run_key,?||i,context,account_id,price_id,0,"
                          "deadline,'settled',created_at FROM bench_ab, billing_calls WHERE id=?", (prefix, prefix, call))
                t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,"
                          "provider,requested_model,cost_price_id,status,created_at,dispatched_at) SELECT ?||'a'||i,"
                          "?||'c'||i,?||i,owner,?||'f'||i,lease_until,environment,provider,'bench-hot',cost_price_id,"
                          "'settled',created_at,dispatched_at FROM bench_ab, billing_attempts WHERE id=?",
                          (prefix, prefix, prefix, prefix, attempt))
                start = time.perf_counter()
                t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
                          "reserved_units,policy_revision,limits_json,admitted_at,expires_at) SELECT ?||'a'||i,'test',"
                          "'bench-provider','bench-account',?,100,'bench-policy','{}',at,at+120 FROM bench_ab",
                          (prefix, self.cost_unit.code))
                t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,"
                          "absorbed_n,absorbed_d,late,created_at) SELECT ?||'a'||i,'{}','completed','12345','1000',?,"
                          "0,'0','1',0,at FROM bench_ab", (prefix, self.cost_unit.code))
                elapsed = time.perf_counter() - start
            return elapsed, time.perf_counter() - s

        with_triggers, _ = bulk("ab1-", pairs)
        _, with_settle = self.dispatches(settles)
        with self.store.tx() as t:
            t.execute("DROP TRIGGER model_cost_admissions_rollup")
            t.execute("DROP TRIGGER model_cost_usage_rollup")
        without_triggers, _ = bulk("ab2-", pairs)
        _, without_settle = self.dispatches(settles)
        return {"pairs": pairs, "bulk": True, "with_s": round(with_triggers, 3), "without_s": round(without_triggers, 3),
                "us_per_pair": round((with_triggers - without_triggers) / pairs * 1e6, 1)}, \
               {"with": with_settle, "without": without_settle,
                "extra_ms_p50": round(with_settle["p50"] - without_settle["p50"], 3)}


class WalletBench:
    """FIXTURE world of the personal-limits benchmark: one funded wallet with enabled limits, ``size`` settled
    calls this month and ``holds`` reserved ones (see the module docstring)."""

    SPEND_TRIGGERS = ("payer_spend_calls_settled", "payer_spend_calls_inserted", "payer_spend_settlements",
                      "payer_spend_journal")

    def __init__(self, work: Path, size: int, seed: int, holds: int = 300):
        from dataclasses import replace

        from synwevo_core.billing import ChargeContext, Ledger, PaymentReceipt, PriceSnapshot, Rate, Unit, Usage, UsageEvidence
        from synwevo_core.billing.payer_budgets import PayerBudgets
        from synwevo_core.identity import Identity
        from synwevo_core.store import Store

        self.mods = dict(Usage=Usage, UsageEvidence=UsageEvidence)
        self.size, self.holds, self.rng, self.work = size, holds, random.Random(seed), work
        self.store = Store(work)
        self.ticks = [MONTH]
        self.unit = Unit("BENCH-CREDIT", 6)
        self.ledger = Ledger(self.store, environment="test", namespace="bench-wallet", authorize=lambda t, c, a: True,
                             verify_payment=lambda t, r: True, clock=lambda: self.ticks[0], max_hold_seconds=DAY)
        sale = PriceSnapshot("bench-sale", "sale", self.unit, "bench-provider", "bench-model", Rate(1), Rate(1),
                             Rate(2), "bench-terms", MONTH - 2 * DAY, per_tokens=1)
        self.ledger.publish_price(sale)
        self.ledger.publish_price(replace(sale, price_id="bench-cost", purpose="cost", unit=Unit("BENCH-COST", 8)))
        self.user = Identity(self.store).create_user("bench-wallet@example.test", None, status="active",
                                                     with_personal_workspace=False)["id"]
        self.ledger.create_payment_order(order_id="bench-order", payer_user_id=self.user, provider="bench-payments",
                                         merchant_id="bench-merchant", amount_minor=100, currency="XTS",
                                         unit=self.unit, credit_units=10**15, exchange_version="bench-exchange")
        self.ledger.credit_verified_payment(PaymentReceipt("test", "bench-payments", "bench-merchant", "bench-payment",
                                                           "bench-event", "bench-order", 100, "XTS", "bench-inbox",
                                                           "b" * 64))
        self.ledger.payer_budgets = PayerBudgets(self.ledger)
        with self.store.tx():
            self.ledger.payer_budgets.set(self.user, unit_code=self.unit.code, expected_revision=0, enabled=True,
                                          daily_budget=2**62, weekly_budget=2**62, monthly_budget=2**62)
        self.ctx = ChargeContext(self.user, self.user, "bench-wallet-history", "bench-approval", "bench-workspace")
        self.ledger.open_run(self.ctx, unit=self.unit, budget=10**12, max_calls=10_000, max_attempts=10_000,
                             deadline=NOW + 30 * DAY)

    def build(self):
        store, rng = self.store, self.rng
        call = self.ledger.reserve_call(self.ctx, logical_call_id="template", amount=10, price_id="bench-sale",
                                        deadline=self.ticks[0] + 600)
        ticket = self.ledger.prepare_attempt(call, attempt_id="template", owner="bench-worker", cost_price_id="bench-cost",
                                             requested_model="bench-model")
        self.ledger.dispatch(ticket)
        Usage, UsageEvidence = self.mods["Usage"], self.mods["UsageEvidence"]
        self.ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence(
            "bench-provider", "req-template", "final-template", Usage(2, 1, 3, 1), "bench-model", "bench-capability",
            self.ticks[0]))
        journal = store.one("SELECT journal_id FROM billing_settlements WHERE call_key=?", (call,))["journal_id"]
        self.template = {"call": call, "attempt": ticket.attempt_key, "journal": journal}
        self.account = store.one("SELECT account_id FROM billing_calls WHERE id=?", (call,))["account_id"]
        started = time.perf_counter()
        for first in range(0, self.size - 1, 20_000):
            rows = [(i, rng.randint(MONTH, NOW - 1), rng.randint(1, 1000)) for i in range(first, min(self.size - 1, first + 20_000))]
            self._settled(rows, prefix="w")
        self._held([(i, NOW - rng.randint(60, DAY - 60), rng.randint(1, 1000)) for i in range(self.holds)], prefix="h")
        self.build_s = time.perf_counter() - started
        self.ticks[0] = NOW

    def _copy_sql(self, t, table, key_column, key, changes, source="temp.bench_wallet_rows f"):
        cols = [r[1] for r in t.execute(f"PRAGMA table_info({table})")]
        t.execute(f"INSERT INTO {table}({','.join(cols)}) SELECT {','.join(changes.get(c, c) for c in cols)} "
                  f"FROM {source}, {table} WHERE {key_column}=? ORDER BY f.i", (key,))

    def _rows(self, t, rows):
        t.execute("CREATE TEMP TABLE IF NOT EXISTS bench_wallet_rows(i INTEGER PRIMARY KEY, at INTEGER, amount INTEGER)")
        t.execute("DELETE FROM temp.bench_wallet_rows")
        t.executemany("INSERT INTO temp.bench_wallet_rows VALUES (?,?,?)", rows)

    def _calls(self, t, p, *, status="'reserved'", hold="0", deadline="f.at+600"):
        tpl = self.template
        self._copy_sql(t, "billing_calls", "id", tpl["call"], {
            "id": f"'{p}c'||f.i", "logical_call_id": f"'{p}c'||f.i", "created_at": "f.at", "status": status,
            "hold_units": hold, "deadline": deadline})

    def _attempts(self, t, p):
        tpl = self.template
        self._copy_sql(t, "billing_attempts", "id", tpl["attempt"], {
            "id": f"'{p}a'||f.i", "call_key": f"'{p}c'||f.i", "attempt_id": f"'{p}a'||f.i", "fence": f"'{p}f'||f.i",
            "request_id": f"'{p}a'||f.i"})
        self._copy_sql(t, "billing_usage", "attempt_key", tpl["attempt"], {"attempt_key": f"'{p}a'||f.i"})

    def _settled(self, rows, *, prefix):
        """Bulk FIXTURE: settled calls in the product's order (journal, settlement, then the status)."""
        tpl, p = self.template, prefix
        with self.store.tx() as t:
            self._rows(t, rows)
            self._calls(t, p)
            self._attempts(t, p)
            self._copy_sql(t, "billing_journal", "id", tpl["journal"], {
                "id": f"'{p}j'||f.i", "voucher": f"'{p}j'||f.i", "reference": f"'{p}c'||f.i", "amount": "f.amount"})
            self._copy_sql(t, "billing_settlements", "call_key", tpl["call"], {
                "call_key": f"'{p}c'||f.i", "attempt_key": f"'{p}a'||f.i", "journal_id": f"'{p}j'||f.i"})
            t.execute(f"UPDATE billing_calls SET status='settled' WHERE id IN (SELECT '{p}c'||i FROM temp.bench_wallet_rows)")

    def _held(self, rows, *, prefix):
        with self.store.tx() as t:
            self._rows(t, rows)
            self._calls(t, prefix, hold="f.amount", deadline=str(NOW + 3600))

    def counts(self):
        one = lambda sql, *a: self.store.one(sql, a)["n"]  # noqa: E731
        return {"settled": one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='settled' AND account_id=?", self.account),
                "held": one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='reserved' AND account_id=?", self.account),
                "day_rows": one("SELECT COUNT(*) AS n FROM payer_spend_days WHERE account_id=?", self.account)}

    def check_times(self, samples):
        out, plans, result = [], [], None
        for k in range(samples + 1):
            with self.store.tx() as t:
                traced = []
                if k == 0:
                    t.set_trace_callback(traced.append)
                s = time.perf_counter()
                result = self.ledger.payer_budgets.check(t, self.account, amount=1, at=self.ticks[0])
                elapsed = (time.perf_counter() - s) * 1000
                if k == 0:
                    t.set_trace_callback(None)
                    for sql in traced:
                        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                            plans.append({"sql": sql[:240], "plan": [r[3] for r in t.execute("EXPLAIN QUERY PLAN " + sql)]})
                else:
                    out.append(elapsed)
        with self.store.tx() as t:
            exposure = self.ledger.payer_budgets._exposure(t, self.account, self.ledger.payer_budgets._windows(self.ticks[0]))
        return stats(out), plans, {"revision": result["revision"],
                                   "monthly": {k: str(v) for k, v in exposure["monthly"].items()}}

    def trigger_overhead_live(self, pairs=2_000):
        """What a live settlement pays for the spend triggers: per pair, the product's three settlement writes
        (journal entry, settlement, the call's status) in one BEGIN IMMEDIATE .. COMMIT under synchronous=FULL,
        on this store (A) and on a copy whose spend triggers are dropped (B), interleaved in random order.
        ``extra_stmt_us`` times the three statements alone, ``extra_locked_us`` the writer-lock hold."""
        rng = random.Random(5)
        rows = [(i, NOW - 7200 + i % 3600, rng.randint(1, 1000)) for i in range(pairs)]
        with self.store.tx() as t:            # each call, attempt and usage exists first, as in a real settlement
            self._rows(t, rows)
            self._calls(t, "live", deadline=str(NOW + DAY))
            self._attempts(t, "live")
        copy = self.work.parent / (self.work.name + "-ab")
        refuse_real_data(copy)
        copy.mkdir()
        source = self.store._connect()
        target = sqlite3.connect(copy / "docweave.sqlite3")
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        tpl = self.store.one("SELECT * FROM billing_journal WHERE id=?", (self.template["journal"],))
        settlement = self.store.one("SELECT * FROM billing_settlements WHERE call_key=?", (self.template["call"],))
        sides = {}
        try:
            for name, path in (("A", self.work), ("B", copy)):
                c = sqlite3.connect(path / "docweave.sqlite3", isolation_level=None, timeout=30)
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA foreign_keys=ON")
                c.execute("PRAGMA synchronous=FULL")
                if name == "B":
                    for trigger in self.SPEND_TRIGGERS:
                        c.execute(f"DROP TRIGGER {trigger}")
                sides[name] = (c, {"stmt": [], "locked": []})
            for i, at, amount in rows:
                order = list(sides.items())
                rng.shuffle(order)
                for _, (c, samples) in order:
                    held = time.perf_counter()
                    c.execute("BEGIN IMMEDIATE")
                    s = time.perf_counter()
                    c.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,credit_account,"
                              "amount,kind,reference,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                              (f"livej{i}", tpl["environment"], tpl["namespace"], f"livej{i}", tpl["unit"],
                               tpl["debit_account"], tpl["credit_account"], amount, tpl["kind"], f"livec{i}", at))
                    c.execute("INSERT INTO billing_settlements(call_key,attempt_key,journal_id,exact_n,exact_d,"
                              "remainder_before_n,remainder_before_d,remainder_after_n,remainder_after_d,created_at) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (f"livec{i}", f"livea{i}", f"livej{i}", settlement["exact_n"], settlement["exact_d"], "0", "1",
                               "0", "1", at))
                    c.execute("UPDATE billing_calls SET status='settled' WHERE id=?", (f"livec{i}",))
                    elapsed = time.perf_counter() - s
                    c.execute("COMMIT")
                    samples["stmt"].append(elapsed * 1e6)
                    samples["locked"].append((time.perf_counter() - held) * 1e6)
        finally:
            for c, _ in sides.values():
                c.close()
            shutil.rmtree(copy, ignore_errors=True)

        def q(values, p):
            values = sorted(values)
            return round(values[max(0, int(p * len(values)) - 1)], 1)

        out = {"pairs": pairs, "per_transaction": True, "synchronous": "FULL",
               "with": {k: {"p50": q(v, .5), "p95": q(v, .95)} for k, v in sides["A"][1].items()},
               "without": {k: {"p50": q(v, .5), "p95": q(v, .95)} for k, v in sides["B"][1].items()}}
        for k in ("stmt", "locked"):
            for p in ("p50", "p95"):
                out[f"extra_{k}_us_{p}"] = round(out["with"][k][p] - out["without"][k][p], 1)
        return out

    def backfill_s(self):
        from synwevo_core.suite_schema.payer_spend_rollup import replay
        c = self.store._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            s = time.perf_counter()
            result = replay(c)
            elapsed = time.perf_counter() - s
            c.execute("COMMIT")
        finally:
            c.close()
        return round(elapsed, 3), result["facts"]

    def verify_times(self):
        from synwevo_core.billing.cost_rollup import CostRollup
        out = {name: _child_verify(self.work, mode, self.ticks[0])
               for name, mode in (("verify_daily", "daily"), ("verify_recent_2d", "recent"))}
        s = time.perf_counter()
        report = CostRollup.check(self.store, now=self.ticks[0], all=True)
        out["verify_all"] = {"s": round(time.perf_counter() - s, 3), "ok": report.ok, "wallet_facts": report.wallet_facts}
        return out


def run_wallet(size, *, samples, seed, work_parent, holds=300, live_pairs=2_000):
    work = Path(tempfile.mkdtemp(prefix=f"wallet-bench-{size}-", dir=work_parent))
    refuse_real_data(work)
    try:
        bench = WalletBench(work, size, seed, holds=holds)
        bench.build()
        result = {"size": size, "build_s": round(bench.build_s, 2), "counts": bench.counts()}
        check, plans, exposure = bench.check_times(samples)
        result.update(check_ms=check, query_plans=plans, exposure=exposure)
        result.update(bench.verify_times())
        result["backfill_s"], result["backfill_facts"] = bench.backfill_s()
        result["triggers_live"] = bench.trigger_overhead_live(live_pairs)
        result["db_mb"] = round(sum(p.stat().st_size for p in work.glob("docweave.sqlite3*")) / 1e6, 1)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _child_verify(work: Path, mode: str, now: int) -> dict:
    """One CostRollup.check as the risk monitor runs it, in a fresh child process: time and peak RSS."""
    import subprocess
    code = ("import json,resource,sys,time\n"
            "from synwevo_core.store import Store\n"
            "from synwevo_core.billing.cost_rollup import CostRollup\n"
            "unit = 1 if sys.platform == 'darwin' else 1024\n"
            "store, now, mode = Store(sys.argv[1]), int(sys.argv[2]), sys.argv[3]\n"
            "base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit\n"
            "s = time.perf_counter()\n"
            "if mode == 'daily':\n"
            "    r = CostRollup.check(store, environment='test', now=now, mode='daily')\n"
            "else:\n"
            "    r = CostRollup.check(store, environment='test', since=now - 2 * 86400, now=now, mode='recent')\n"
            "el = time.perf_counter() - s\n"
            "peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit\n"
            "print(json.dumps({'s': round(el, 3), 'ok': r.ok, 'facts': r.facts, 'wallet_facts': r.wallet_facts,"
            " 'rss_before_mb': round(base / 2**20, 1), 'peak_rss_mb': round(peak / 2**20, 1)}))\n")
    done = subprocess.run([sys.executable, "-P", "-c", code, str(work), str(now), mode], capture_output=True, text=True,
                          check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def run(size, *, samples, dispatches, seed, work_parent, pairs=10_000, live_pairs=2_000):
    work = Path(tempfile.mkdtemp(prefix=f"cost-bench-{size}-", dir=work_parent))
    refuse_real_data(work)
    try:
        bench = Bench(work, size, seed)
        bench.build()
        result = {"size": size, "build_s": round(bench.build_s, 2), "counts": bench.counts(),
                  "history": bench.history_span()}
        cg, read, velocity_rows, plans, counters = bench.counters_and_guard(samples)
        result.update(counters_guard_ms=cg, rows_read=read, velocity_bucket_rows=velocity_rows, query_plans=plans,
                      counters={k: str(v) if not isinstance(v, (int, str)) else v for k, v in counters.items()})
        dispatch, settle = bench.dispatches(dispatches)
        result.update(dispatch_ms=dispatch, settle_ms=settle)
        result.update(bench.verify_times())
        result["backfill_s"], result["backfill_facts"] = bench.backfill_s()
        result["triggers_live"] = bench.trigger_overhead_live(live_pairs)
        result["triggers"], result["settlement_overhead"] = bench.trigger_and_settlement_overhead(pairs)
        result["db_mb"] = round(sum(p.stat().st_size for p in work.glob("docweave.sqlite3*")) / 1e6, 1)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="200000,1000000", help="cost admissions per run ('' for none)")
    ap.add_argument("--wallet-sizes", default="200000,1000000",
                    help="settled calls of the one wallet per personal-limits run ('' for none)")
    ap.add_argument("--samples", type=int, default=200)
    ap.add_argument("--dispatches", type=int, default=500)
    ap.add_argument("--seed", type=int, default=20261025)
    ap.add_argument("--work", help="parent directory for the temporary stores (default: the system temp)")
    ap.add_argument("--out", help="write the JSON evidence here")
    args = ap.parse_args(argv)
    parent = Path(args.work) if args.work else Path(tempfile.gettempdir())
    parent.mkdir(parents=True, exist_ok=True)
    refuse_real_data(parent)
    runs = [run(int(size), samples=args.samples, dispatches=args.dispatches, seed=args.seed, work_parent=parent)
            for size in args.sizes.split(",") if size]
    wallet_runs = [run_wallet(int(size), samples=args.samples, seed=args.seed, work_parent=parent)
                   for size in args.wallet_sizes.split(",") if size]
    by_size = {r["size"]: r for r in runs}
    base = by_size.get(200_000)
    met = {}
    for r in runs:
        met[r["size"]] = {
            "counters_guard_p95_le_3ms": r["counters_guard_ms"]["p95"] <= 3,
            "dispatch_p95_lt_20ms": r["dispatch_ms"]["p95"] < 20,
            "triggers_le_300us_per_pair": r["triggers_live"]["extra_stmt_us_p50"] <= 300,
            "settlement_extra_le_1ms": r["settlement_overhead"]["extra_ms_p50"] <= 1,
            "backfill_le_15s": r["backfill_s"] <= 15, "verify_daily_le_15s": r["verify_daily"]["s"] <= 15}
        if base and r is not base:
            met[r["size"]]["counters_guard_within_1_5x_of_200k"] = (
                r["counters_guard_ms"]["p95"] <= 1.5 * base["counters_guard_ms"]["p95"])
    wallet_base = next((r for r in wallet_runs if r["size"] == 200_000), None)
    wallet_met = {}
    for r in wallet_runs:
        wallet_met[r["size"]] = {"check_p95_le_1ms": r["check_ms"]["p95"] <= 1,
                                 "triggers_le_150us_per_settlement": r["triggers_live"]["extra_stmt_us_p50"] <= 150,
                                 "verify_daily_ok": r["verify_daily"]["ok"]}
        if wallet_base and r is not wallet_base:
            wallet_met[r["size"]]["check_within_1_5x_of_200k"] = r["check_ms"]["p95"] <= 1.5 * wallet_base["check_ms"]["p95"]
    evidence = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "machine": _machine(),
                "scope": "isolated temporary Store, synthetic fixture tariffs and wallets; no network, keys or data/",
                "runs": runs, "targets_met": met, "wallet_runs": wallet_runs, "wallet_targets_met": wallet_met}
    text = json.dumps(evidence, indent=2, ensure_ascii=False, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _machine() -> str:
    import platform
    return f"{platform.system()}-{platform.release()}-{platform.machine()}"


if __name__ == "__main__":
    sys.exit(main())
