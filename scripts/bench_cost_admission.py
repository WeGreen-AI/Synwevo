#!/usr/bin/env python3
"""Benchmark the platform cost admission on the exact cost rollup (docs/billing-ledger.md).

    python scripts/bench_cost_admission.py                       # 200k and 1M admissions, JSON to stdout
    python scripts/bench_cost_admission.py --sizes 200000 --out FILE

Everything happens in a NEW, isolated temporary Store, made fresh inside ``--work`` (default: the
system temporary directory) and removed afterwards; the script refuses the repository's ``data/``,
the configured ``SYNWEVO_DATA_DIR`` and any directory that already holds a store. No network, no keys, no real prices: synthetic fixture tariffs, users and
wallets, written through the real Ledger and CostAdmission where timing matters, and cloned with
plain bulk SQL (the triggers see exactly what a bulk writer would write) for the history.

Workload per size N (one month, 2034-05-01 .. 2034-05-20T12:00, a Saturday): N admissions, 95 %
settled at random on-grid costs; 2,000 local stops in the last 24 h (holds still reserved); 300 open
unknowns in flight; the rest closed unknowns; 2,000 payer wallets; 21 routes, one of them hot (30 %),
with payer and route velocity windows on. Then 500 real dispatches (reserve, prepare, dispatch with
admission under FULL durability, settle).

Measured: ``_counters`` + ``RiskControls.guard`` inside BEGIN IMMEDIATE (p50/p95/max), the real
dispatch, settlement with and without the usage trigger, the trigger cost per admission+usage pair
(bulk, with and without the producer triggers), the backfill (replay), the 40-day, full and recent
verify; the EXPLAIN QUERY PLAN of every cost statement on the admission path and its rows_read.
Targets (contract §12): counters+guard p95 <= 3 ms and the 1M run within 1.5x of 200k; dispatch p95
< 20 ms; triggers <= 300 us per pair; settlement <= 1 ms extra; backfill and 40-day verify <= 15 s.
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
MONTH = 2_029_536_000          # 2034-05-01T00:00:00Z (a Monday)
NOW = 2_031_220_800            # 2034-05-20T12:00:00Z (a Saturday)
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

    def verify_times(self):
        from synwevo_core.billing.cost_rollup import CostRollup
        out = {}
        for name, kwargs in (("verify_40d", {"since": self.ticks[0] - 40 * DAY}),
                             ("verify_recent_2d", {"since": self.ticks[0] - 2 * DAY, "mode": "recent"}),
                             ("verify_all", {"all": True})):
            s = time.perf_counter()
            report = CostRollup.check(self.store, now=self.ticks[0], **kwargs)
            out[name] = {"s": round(time.perf_counter() - s, 3), "ok": report.ok, "facts": report.facts}
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

    def trigger_and_settlement_overhead(self, pairs=10_000, settles=200):
        """Last: drops the producer triggers in this throwaway store to measure them (A/B)."""
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
        return {"pairs": pairs, "with_s": round(with_triggers, 3), "without_s": round(without_triggers, 3),
                "us_per_pair": round((with_triggers - without_triggers) / pairs * 1e6, 1)}, \
               {"with": with_settle, "without": without_settle,
                "extra_ms_p50": round(with_settle["p50"] - without_settle["p50"], 3)}


def run(size, *, samples, dispatches, seed, work_parent):
    work = Path(tempfile.mkdtemp(prefix=f"cost-bench-{size}-", dir=work_parent))
    refuse_real_data(work)
    try:
        bench = Bench(work, size, seed)
        bench.build()
        result = {"size": size, "build_s": round(bench.build_s, 2), "counts": bench.counts()}
        cg, read, velocity_rows, plans, counters = bench.counters_and_guard(samples)
        result.update(counters_guard_ms=cg, rows_read=read, velocity_bucket_rows=velocity_rows, query_plans=plans,
                      counters={k: str(v) if not isinstance(v, (int, str)) else v for k, v in counters.items()})
        dispatch, settle = bench.dispatches(dispatches)
        result.update(dispatch_ms=dispatch, settle_ms=settle)
        result.update(bench.verify_times())
        result["backfill_s"], result["backfill_facts"] = bench.backfill_s()
        result["triggers"], result["settlement_overhead"] = bench.trigger_and_settlement_overhead()
        result["db_mb"] = round(sum(p.stat().st_size for p in work.glob("docweave.sqlite3*")) / 1e6, 1)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sizes", default="200000,1000000")
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
            for size in args.sizes.split(",")]
    by_size = {r["size"]: r for r in runs}
    base = by_size.get(200_000)
    met = {}
    for r in runs:
        met[r["size"]] = {
            "counters_guard_p95_le_3ms": r["counters_guard_ms"]["p95"] <= 3,
            "dispatch_p95_lt_20ms": r["dispatch_ms"]["p95"] < 20,
            "triggers_le_300us_per_pair": r["triggers"]["us_per_pair"] <= 300,
            "settlement_extra_le_1ms": r["settlement_overhead"]["extra_ms_p50"] <= 1,
            "backfill_le_15s": r["backfill_s"] <= 15, "verify_40d_le_15s": r["verify_40d"]["s"] <= 15}
        if base and r is not base:
            met[r["size"]]["counters_guard_within_1_5x_of_200k"] = (
                r["counters_guard_ms"]["p95"] <= 1.5 * base["counters_guard_ms"]["p95"])
    evidence = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "machine": _machine(),
                "scope": "isolated temporary Store, synthetic fixture tariffs and wallets; no network, keys or data/",
                "runs": runs, "targets_met": met}
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
