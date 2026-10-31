"""The per-wallet spend rollup (suite_schema/payer_spend_rollup.py, billing/spend_rollup.py) on real Stores.

Cost-capacity phase 2 (binding addendum to the cost-rollup contract, decision D18): the personal spending
limits read exact per-wallet day rows that triggers keep, instead of up to 10,000 calls inside the writer
lock. Isolated money fixtures only (tests/integration/test_billing_ledger.py): no keys, no provider
traffic, never ``data/``. Bulk history is cloned from one real Ledger settlement with plain SQL (FIXTURE),
so the triggers see exactly what a bulk writer, an older release or a hand-written repair would write, in
each order the three money facts can arrive. Expected amounts are independent arithmetic over the cloned
rows, never values read back from the implementation under test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3

import pytest

from synwevo_core.billing import BillingError, Ledger, Usage
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.store import Store
from tests.integration.test_billing_ledger import (  # noqa: F401
    UNIT, context, fixture_authorize, fixture_verify, fund, money, reserve,
)
from tests.integration.test_model_cost_admission import policy
from tests.integration.test_payer_budgets import configure, view
from tests.integration.platform_fixture import make_platform_admin
from tests.integration.test_payer_budgets_recovery import _call, _prepare
from tests.integration.test_billing_ledger import dispatch, settle

B = 10**15
DAY = 86_400
INT64 = 2**63 - 1
TOP = 2**63 - 1


def spend():
    from synwevo_core.billing import spend_rollup
    return spend_rollup


def rollup():
    from synwevo_core.billing import cost_rollup
    return cost_rollup


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text + "+00:00").timestamp())


def independent_windows(at: int) -> dict:
    """The UTC day, ISO week (from Monday) and month containing ``at``, computed here (not by the product)."""
    moment = datetime.fromtimestamp(at, timezone.utc)
    day = datetime(moment.year, moment.month, moment.day, tzinfo=timezone.utc)
    week = day - timedelta(days=moment.weekday())
    month = datetime(moment.year, moment.month, 1, tzinfo=timezone.utc)
    return {"daily": int(day.timestamp()), "weekly": int(week.timestamp()), "monthly": int(month.timestamp())}


def wallet(money, user=0) -> str:
    return money[0].one("SELECT id FROM billing_accounts WHERE kind='wallet' AND owner=? AND unit=?",
                        (money[2][user], UNIT.code))["id"]


def scope(money, user=0) -> tuple:
    return ("test", "", wallet(money, user))


def days(store, account) -> list[dict]:
    return store.all("SELECT start,n_set,n_paid,amt_hi,amt_lo FROM payer_spend_days WHERE account_id=? ORDER BY start",
                     (account,))


def expected_days(rows) -> list[dict]:
    """Independent: per UTC day of created_at, (settled, paid, Σ amount in limbs) of (created_at, amount|None)."""
    out = {}
    for at, amount in rows:
        start = at - at % DAY
        e = out.setdefault(start, [0, 0, 0])
        e[0] += 1
        if amount is not None:
            e[1] += 1
            e[2] += amount
    return [{"start": s, "n_set": n, "n_paid": p, "amt_hi": a // B, "amt_lo": a % B} for s, (n, p, a) in sorted(out.items())]


def tamper(store, sql, args=()):
    """FIXTURE: a derived row edited past the gate (a hand-written delta opens it: the residual risk)."""
    with store.tx() as t:
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        changed = t.execute(sql, args).rowcount
        t.execute("DELETE FROM model_cost_rollup_deltas")
    return changed


# ---------------------------------------------------------------- FIXTURE: cloned money facts
def settled_template(money, name="fixture-spend-template") -> str:
    """One real reservation, dispatch and settlement (charged 1) to clone from; returns its call id."""
    call = _call(money, name)
    ticket = dispatch(money, call)
    assert settle(money, ticket, usage=Usage(1, 0, 0)).charged == 1
    return call


#: The three money facts of a settlement in each order they can arrive (contract phase 2 §2). The last one
#: writes the settlement before its journal entry, which only an import with foreign keys off can do.
ORDERS = {
    "product": ("call:reserved", "journal", "settlement", "status"),
    "status_first": ("call:reserved", "status", "journal", "settlement"),
    "inserted_settled": ("call:settled", "journal", "settlement"),
    "journal_after_settlement": ("call:reserved", "status", "settlement", "journal"),
}


def _columns(c, table):
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _clone(c, table, key_column, key, replace):
    cols = _columns(c, table)
    c.execute(f"INSERT INTO {table}({','.join(cols)}) SELECT {','.join(replace.get(col, col) for col in cols)} "
              f"FROM temp.fixture_rows f, {table} WHERE {key_column}=? ORDER BY f.i", (key,))


def history(store, template, rows, *, prefix, steps=ORDERS["product"], accounts=None, foreign_keys=True,
            first=0):
    """FIXTURE: one call per (created_at, amount) of ``rows``, cloned from the real settled call ``template``
    with plain SQL, each with its own attempt, usage, journal entry (debiting ``accounts[i]`` = (debit,
    credit) when given, else the template's wallet and revenue) and settlement, written in ``steps`` (an
    ORDERS value, or a part of one: an unpaid call is "call:settled" alone)."""
    p = prefix
    with store.conn() as c:
        if not foreign_keys:
            c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("CREATE TEMP TABLE IF NOT EXISTS fixture_rows(i INTEGER PRIMARY KEY, at, amount, debit, credit)")
                c.execute("DELETE FROM temp.fixture_rows")
                c.executemany("INSERT INTO temp.fixture_rows VALUES (?,?,?,?,?)",
                              [(first + k, at, amount, *(accounts[k] if accounts else (None, None)))
                               for k, (at, amount) in enumerate(rows)])
                settled = c.execute("SELECT attempt_key,journal_id FROM billing_settlements WHERE call_key=?",
                                    (template,)).fetchone()
                attempt, journal = settled if settled else (None, None)
                for step in steps:
                    if step.startswith("call:"):
                        _clone(c, "billing_calls", "id", template, {
                            "id": f"'{p}-call-'||f.i", "logical_call_id": f"'{p}-'||f.i", "created_at": "f.at",
                            "status": "'settled'" if step == "call:settled" else "'reserved'"})
                        if attempt is not None:     # a settlement's attempt and usage (a hold needs neither)
                            _clone(c, "billing_attempts", "id", attempt, {
                                "id": f"'{p}-attempt-'||f.i", "call_key": f"'{p}-call-'||f.i",
                                "fence": f"'{p}-fence-'||f.i", "request_id": f"'{p}-request-'||f.i"})
                            _clone(c, "billing_usage", "attempt_key", attempt, {"attempt_key": f"'{p}-attempt-'||f.i"})
                    elif step == "status":
                        c.execute("UPDATE billing_calls SET status='settled' WHERE id IN "
                                  f"(SELECT '{p}-call-'||i FROM temp.fixture_rows)")
                    elif step == "journal":
                        _clone(c, "billing_journal", "id", journal, {
                            "id": f"'{p}-journal-'||f.i", "voucher": f"'{p}-voucher-'||f.i",
                            "reference": f"'{p}-call-'||f.i", "amount": "f.amount",
                            "debit_account": "COALESCE(f.debit,debit_account)",
                            "credit_account": "COALESCE(f.credit,credit_account)"})
                    elif step == "settlement":
                        _clone(c, "billing_settlements", "call_key", template, {
                            "call_key": f"'{p}-call-'||f.i", "attempt_key": f"'{p}-attempt-'||f.i",
                            "journal_id": f"'{p}-journal-'||f.i"})
                    else:
                        raise ValueError(step)
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        finally:
            if not foreign_keys:
                c.execute("PRAGMA foreign_keys=ON")


def held(store, template, count, *, prefix):
    """FIXTURE: ``count`` reserved calls (hold 1 each, the template's) in the template's run."""
    history(store, template, [(store.one("SELECT created_at FROM billing_calls WHERE id=?", (template,))["created_at"],
                               None)] * count, prefix=prefix, steps=("call:reserved",))


def fixture_accounts(money, count, *, name):
    """FIXTURE: ``count`` (debit, credit) pairs of fresh platform accounts with balance rows, so a journal
    entry of up to 2^63-1 never touches a wallet's balance (the rollup sums any journal kind)."""
    ledger = money[1]
    with money[0].tx() as t:
        return [(ledger._account(t, UNIT.code, f"{name}-source-{k}", "paid"),
                 ledger._account(t, UNIT.code, f"{name}-sink-{k}", "revenue")) for k in range(count)]


def periods_spent(view_):
    return {name: int(value["spent"]) for name, value in view_["periods"].items()}


# ---------------------------------------------------------------- 1-2: settled volume never refuses
def test_20000_settled_calls_this_month_admit_and_spent_is_exact(money):
    store, ledger, _, ticks = money
    now = utc("2034-05-20T12:00:00")                    # a Saturday: week from 05-15, month from 05-01
    ticks[0] = now - 7200
    fund(money, 100_000)
    configure(money, daily_budget=10**12, weekly_budget=10**12, monthly_budget=10**12)
    template = settled_template(money)
    month = utc("2034-05-01T00:00:00")
    rows = [(month + (i * 997) % (now - 3600 - month), 1 + i % 5) for i in range(19_999)]   # + the template
    history(store, template, rows, prefix="fixture-month")
    assert store.one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='settled'")["n"] == 20_000
    ticks[0] = now
    every = rows + [(now - 7200, 1)]
    want = {name: sum(a for at, a in every if at >= start) for name, start in independent_windows(now).items()}
    current = view(money)
    assert current["usage_state"] == "available"
    assert periods_spent(current) == want and current["periods"]["daily"]["held"] == "0"
    call = _call(money, "after-the-month", amount=5)    # reserve admits
    ledger.dispatch(_prepare(money, call))              # and so does dispatch
    after = view(money)
    assert periods_spent(after) == want and after["periods"]["monthly"]["held"] == "5"
    with store.tx() as t:
        assert spend().reference_exposure(t, account=wallet(money), windows=spend().periods(now)) == \
            ledger.payer_budgets._exposure(t, wallet(money), spend().periods(now))


def test_10000_reserved_calls_admit_and_the_10001st_refuses_without_partial_writes(money):
    store, ledger, _, ticks = money
    fund(money, 100_000)
    configure(money, daily_budget=10**9, weekly_budget=10**9, monthly_budget=10**9)
    target = _call(money, "capacity-target")
    ticket = _prepare(money, target)
    held(store, target, 9_999, prefix="fixture-held")
    assert store.one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='reserved'")["n"] == 10_000
    assert view(money)["usage_state"] == "available"
    ledger.dispatch(ticket)                             # 10,000 held: admitted
    extra = _call(money, "capacity-10001")              # 10,000 counted before its own row: admitted
    pending = _prepare(money, extra)
    assert view(money)["usage_state"] == "capacity"
    before = (store.all("SELECT * FROM billing_calls ORDER BY rowid"), store.all("SELECT * FROM billing_events ORDER BY seq"),
              store.all("SELECT * FROM billing_payer_budget_admissions ORDER BY call_key"))
    with pytest.raises(BillingError, match="^payer_budget_capacity$"):
        _call(money, "capacity-refused")
    with pytest.raises(BillingError, match="^payer_budget_capacity$"):
        ledger.dispatch(pending)
    assert (store.all("SELECT * FROM billing_calls ORDER BY rowid"), store.all("SELECT * FROM billing_events ORDER BY seq"),
            store.all("SELECT * FROM billing_payer_budget_admissions ORDER BY call_key")) == before
    assert store.one("SELECT status,dispatched_at FROM billing_attempts WHERE id=?", (pending.attempt_key,)) == {
        "status": "prepared", "dispatched_at": None}


# ---------------------------------------------------------------- 3: every arrival order, unpaid calls
@pytest.mark.parametrize("order", list(ORDERS))
def test_every_arrival_order_of_the_money_facts_books_each_call_once(money, order):
    store, ledger, _, ticks = money
    fund(money, 10_000)
    configure(money)
    template = settled_template(money)
    base = ticks[0]
    rows = [(base - 3 * DAY + k * 7_777, 2 + 3 * k) for k in range(12)]
    history(store, template, rows, prefix="fixture-" + order, steps=ORDERS[order],
            foreign_keys=order != "journal_after_settlement")
    account = wallet(money)
    assert days(store, account) == expected_days(rows + [(base, 1)])
    assert store.all("SELECT * FROM model_cost_rollup_deltas") == []
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    # A later settlement of a real call adds once more, whatever came before.
    ticket = dispatch(money, _call(money, "after-" + order))
    settle(money, ticket, usage=Usage(1, 0, 0))
    assert days(store, account) == expected_days(rows + [(base, 1), (base, 1)])
    rollup().assert_consistent(store)


def test_an_unpaid_settled_call_refuses_in_the_periods_not_before_and_a_late_settlement_clears_it(money):
    store, ledger, _, ticks = money
    now = utc("2034-05-17T12:00:00")                    # a Wednesday; month from 05-01
    ticks[0] = now - 600
    fund(money, 10_000)
    configure(money)
    template = settled_template(money)
    ticks[0] = now
    old = [(utc("2034-04-30T23:59:59"), 5)]             # before every period: never read
    history(store, template, old, prefix="fixture-old-unpaid", steps=("call:settled",))
    assert view(money)["usage_state"] == "available"
    _call(money, "while-old-unpaid")
    current = [(now - 60, 7)]
    history(store, template, current, prefix="fixture-unpaid", steps=("call:reserved", "status"))
    state = view(money)
    assert state["usage_state"] == "inconsistent" and state["periods"] is None
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
        _call(money, "while-unpaid")
    # Not latched: the missing facts arriving clear it (journal, then settlement).
    history(store, template, current, prefix="fixture-unpaid", steps=("journal", "settlement"))
    state = view(money)
    assert state["usage_state"] == "available"
    assert periods_spent(state) == {"daily": 1 + 7, "weekly": 1 + 7, "monthly": 1 + 7}
    assert state["periods"]["daily"]["held"] == "1"
    _call(money, "after-late-settlement")
    rollup().assert_consistent(store)


# ---------------------------------------------------------------- 4: a malformed settled call
@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("created_at", ["soon", 1.5, -DAY], ids=["text", "real", "negative"])
def test_a_malformed_settled_call_is_a_fault_that_refuses_only_its_wallet(money, created_at):
    store, ledger, users, ticks = money
    fund(money, 10_000)
    fund(money, 1_000, user=1, order="other-user")
    configure(money)
    configure(money, user=1)
    template = settled_template(money)
    ticket = _prepare(money, _call(money, "prepared-before"))
    key = "fixture-malformed-call-0"
    history(store, template, [(created_at, None)], prefix="fixture-malformed", steps=("call:settled",))
    target = scope(money)
    fault = store.one("SELECT * FROM model_cost_rollup_faults WHERE id=?", (rollup().fact_fault_id(key, target),))
    assert fault is not None and fault["resolved_at"] is None
    assert (fault["code"], fault["environment"], fault["provider"], fault["account_ref"]) == (
        "fact_malformed_settled_call", *target)
    assert view(money)["usage_state"] == "inconsistent"
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
        _call(money, "after-malformed")
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
        ledger.dispatch(ticket)
    with pytest.raises(BillingError, match="cost_rollup_rebuild_refused") as caught:
        rollup().CostRollup.rebuild_wallet(store, account_id=target[2], actor="fixture-operator",
                                           reason="fixture rebuild over a malformed call")
    assert caught.value.attempt_keys == [key]
    assert store.one("SELECT resolved_at FROM model_cost_rollup_faults WHERE id=?",
                     (rollup().fact_fault_id(key, target),))["resolved_at"] is None
    # The other wallet is unaffected.
    other = reserve(money, ctx=context(money, user=1, run="other-wallet"), amount=1)
    assert other and view(money, 1)["usage_state"] == "available"
    report = rollup().CostRollup.check(store, all=True)
    assert set(report.mismatched) == {target}
    assert {d["check"] for d in report.wallets[target].differences} == {"fact_fault"}
    reopened = Store(store.root)                        # opening never refuses, and keeps the fault
    again = Ledger(reopened, environment="test", namespace=ledger.namespace, authorize=fixture_authorize,
                   verify_payment=fixture_verify, clock=lambda: ticks[0], max_hold_seconds=1000)
    again.payer_budgets = PayerBudgets(again)
    assert reopened.one("SELECT resolved_at FROM model_cost_rollup_faults WHERE id=?",
                        (rollup().fact_fault_id(key, target),))["resolved_at"] is None
    assert view((reopened, again, users, ticks))["usage_state"] == "inconsistent"


# ---------------------------------------------------------------- 5-6: the gate, tampering, latch, rebuild
@pytest.mark.cost_rollup_unchecked
def test_day_rows_refuse_direct_writes_and_a_tampered_row_never_blocks_a_settlement(money):
    store, ledger, _, _ = money
    fund(money, 1_000)
    configure(money)
    settled_template(money)
    call = _call(money, "settled-after-tamper")
    ticket = dispatch(money, call)
    for sql in ("UPDATE payer_spend_days SET n_set=0", "DELETE FROM payer_spend_days",
                "INSERT INTO payer_spend_days(account_id,start,n_set,n_paid,amt_hi,amt_lo) VALUES ('w',0,1,1,0,1)"):
        with pytest.raises(sqlite3.IntegrityError, match="cost_rollup_derived"):
            store.execute(sql)
    assert tamper(store, "UPDATE payer_spend_days SET n_set='x',amt_lo=NULL,amt_hi=-1 WHERE account_id=?",
                  (wallet(money),)) == 1
    assert settle(money, ticket, usage=Usage(1, 0, 0)).charged == 1          # the fact still commits
    assert store.one("SELECT status FROM billing_calls WHERE id=?", (call,))["status"] == "settled"
    assert view(money)["usage_state"] == "inconsistent"
    assert not rollup().CostRollup.check(store).ok


@pytest.mark.cost_rollup_unchecked
def test_a_tampered_day_row_latches_only_its_wallet_and_a_rebuild_restores_identical_rows(money):
    store, ledger, _, ticks = money
    fund(money, 1_000)
    fund(money, 1_000, user=1, order="other-user")
    configure(money)
    configure(money, user=1)
    settled_template(money)
    settle(money, dispatch(money, reserve(money, ctx=context(money, user=1, run="other-settled"), amount=3),
                           attempt="other"), usage=Usage(1, 0, 0))
    ticket = _prepare(money, _call(money, "latched-dispatch"))
    target, other = scope(money), scope(money, 1)
    original = store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start")
    tamper(store, f"UPDATE payer_spend_days SET amt_lo={B} WHERE account_id=?", (target[2],))
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$") as caught:
        ledger.dispatch(ticket)
    error = caught.value
    assert isinstance(error, spend().SpendRollupInconsistent) and isinstance(error, rollup().RollupInconsistent)
    assert (error.scope, error.check) == (target, "structure")
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []        # rolled back with the dispatch
    # The gateway hands a payer_* refusal to after_refusal once the dispatch rolled back: it latches.
    CostAdmission(ledger, current_policy=policy).after_refusal(error)
    faults = store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL")
    assert [(f["code"], f["environment"], f["provider"], f["account_ref"]) for f in faults] == [("read_structure", *target)]
    assert faults[0]["id"].startswith("read:structure:")
    audits = store.all("SELECT object_id,meta FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'")
    assert [a["object_id"] for a in audits] == [rollup().scope_key(target)]
    assert json.loads(audits[0]["meta"])["wallet"] == target[2]
    # The verifier finds that wallet only; the other one still reserves.
    report = rollup().CostRollup.verify(store)
    assert set(report.mismatched) == {target}
    assert {d["check"] for d in report.wallets[target].differences} >= {"structure", "day"}
    reserve(money, ctx=context(money, user=1, run="other-still-works"), amount=1)
    result = rollup().CostRollup.rebuild_wallet(store, account_id=target[2], actor="fixture-operator",
                                                reason="fixture tamper repaired")
    assert result["resolved_faults"] == 2 and result["scope"] == list(target)
    assert store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start") == original
    assert store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL") == []
    assert store.all("SELECT action FROM audit_events WHERE action='billing.payer_spend.rollup_rebuilt'")
    ledger.dispatch(ticket)
    # A verify that read its snapshot before a newer rebuild is recorded as superseded, not latched.
    tamper(store, "UPDATE payer_spend_days SET amt_lo=amt_lo+1 WHERE account_id=?", (target[2],))
    stale = rollup().CostRollup.check(store)
    assert set(stale.mismatched) == {target}
    rollup().CostRollup.rebuild_wallet(store, account_id=target[2], actor="fixture-operator",
                                       reason="fixture second repair")
    assert rollup().CostRollup.record(store, stale) == {target: "superseded"}
    assert store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL") == []
    rollup().assert_consistent(store)


# ---------------------------------------------------------------- 7: replay on the next start
@pytest.mark.parametrize("damage", ["drop_table", "sentinel", "trigger_version", "marker"])
def test_a_lost_table_sentinel_or_trigger_version_replays_on_the_next_start(money, damage):
    store, ledger, _, ticks = money
    fund(money, 1_000)
    configure(money)
    template = settled_template(money)
    history(store, template, [(ticks[0] - k * 50_000, k + 1) for k in range(6)], prefix="fixture-replay",
            steps=ORDERS["status_first"])
    history(store, template, [(ticks[0] - 10, None)], prefix="fixture-replay-unpaid", steps=("call:settled",))
    original = store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start")
    if damage == "drop_table":
        store.execute("DROP TABLE payer_spend_days")
    elif damage == "sentinel":
        tamper(store, "DELETE FROM payer_spend_days WHERE account_id=''")
    elif damage == "trigger_version":
        with store.tx() as t:                           # an older release's producer, missing one trigger
            t.execute("DROP TRIGGER payer_spend_journal")
            t.execute("UPDATE meta SET value='0' WHERE key='payer_spend_rollup_triggers'")
    else:
        store.execute("DELETE FROM meta WHERE key='payer_spend_rollup_v1'")
    backfills = store.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"]
    reopened = Store(store.root)
    assert reopened.all("SELECT * FROM payer_spend_days ORDER BY account_id,start") == original
    assert reopened.one("SELECT 1 AS x FROM sqlite_master WHERE type='trigger' AND name='payer_spend_journal'")
    audit = reopened.one("SELECT * FROM model_cost_rollup_audits WHERE provider='@wallets' ORDER BY rowid DESC LIMIT 1")
    assert (audit["environment"], audit["account_ref"], audit["mode"], audit["status"]) == ("", "", "backfill", "ok")
    assert reopened.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"] == backfills + 1
    assert reopened.one("SELECT actor FROM model_cost_rollup_rebuilds WHERE seq=?", (audit["rebuild_seq"],)) == {
        "actor": "system:backfill"}
    again = Store(store.root)                           # the second start replays nothing
    assert again.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"] == backfills + 1
    rollup().assert_consistent(again)


# ---------------------------------------------------------------- 8: exactness and calendar edges
def test_three_int64_amounts_in_one_day_sum_exactly(money):
    store, ledger, _, ticks = money
    fund(money, 1_000)
    configure(money, daily_budget=TOP, weekly_budget=TOP, monthly_budget=TOP)
    template = settled_template(money)
    rows = [(ticks[0] - 30 + k, INT64) for k in range(3)]
    history(store, template, rows, prefix="fixture-int64", accounts=fixture_accounts(money, 3, name="int64"))
    total = 3 * INT64 + 1
    row = next(r for r in days(store, wallet(money)) if r["start"] == ticks[0] - ticks[0] % DAY)
    assert (row["n_set"], row["n_paid"], row["amt_hi"] * B + row["amt_lo"]) == (4, 4, total)
    assert 0 <= row["amt_lo"] < B
    current = view(money)
    assert periods_spent(current) == {"daily": total, "weekly": total, "monthly": total}
    assert current["periods"]["daily"]["remaining"] == "0"
    rollup().assert_consistent(store)


def test_a_budget_of_exactly_spent_plus_held_plus_amount_admits_and_one_less_refuses(money):
    store, ledger, _, ticks = money
    fund(money, 1_000)
    configure(money, daily_budget=TOP, weekly_budget=TOP, monthly_budget=TOP)
    template = settled_template(money)
    big = 2**62 + 12_345
    history(store, template, [(ticks[0] - 5, big)], prefix="fixture-big", accounts=fixture_accounts(money, 1, name="big"))
    _call(money, "held-seven", amount=7)
    spent, held_, amount = big + 1, 7, 5
    configure(money, revision=1, daily_budget=spent + held_ + amount - 1, weekly_budget=TOP, monthly_budget=TOP)
    with pytest.raises(BillingError, match="^payer_daily_budget_exceeded$"):
        _call(money, "one-too-many", amount=amount)
    configure(money, revision=2, daily_budget=spent + held_ + amount, weekly_budget=TOP, monthly_budget=TOP)
    _call(money, "exactly-at-the-budget", amount=amount)
    current = view(money)
    assert periods_spent(current)["daily"] == spent and current["periods"]["daily"]["held"] == str(held_ + amount)
    assert current["periods"]["daily"]["remaining"] == "0"


@pytest.mark.parametrize("edge", ["2034-05-08T00:00:00", "2034-06-01T00:00:00", "2036-02-29T00:00:00",
                                  "2036-03-01T00:00:00"], ids=["sunday-monday", "month-end", "leap-day", "leap-month-end"])
def test_calendar_edges_count_each_call_in_the_periods_of_its_creation_time(money, edge):
    store, ledger, _, ticks = money
    b = utc(edge)
    ticks[0] = b - 3 * DAY
    fund(money, 100_000)
    configure(money, daily_budget=TOP, weekly_budget=TOP, monthly_budget=TOP)
    template = settled_template(money)
    rows = [(b - 1, 1), (b, 10), (b + 1, 100), (b - DAY, 1_000), (b + DAY - 1, 10_000)]
    history(store, template, rows, prefix="fixture-edge")
    every = rows + [(b - 3 * DAY, 1)]
    account = wallet(money)
    for at in (b - 1, b, b + 1):
        win = spend().periods(at)
        assert {name: start for name, (start, _) in win.items()} == independent_windows(at)
        want = {name: sum(a for t, a in every if t >= start) for name, start in independent_windows(at).items()}
        with store.tx() as t:
            got = spend().exposure(t, environment="test", account=account, windows=win)
            ref = spend().reference_exposure(t, account=account, windows=win)
        assert {name: value["spent"] for name, value in got.items()} == want, at
        assert got == ref
    rollup().assert_consistent(store)


# ---------------------------------------------------------------- 9: plans and flatness
#: The facts the legacy read joined, and the index it scanned for settled history: never read now.
NOT_READ = ("billing_settlements", "billing_journal", "billing_calls_payer_period")


def test_the_check_reads_only_the_day_rows_key_and_the_holds_index(money):
    store, ledger, _, ticks = money
    fund(money, 1_000)
    configure(money)
    template = settled_template(money)
    history(store, template, [(ticks[0] - k * 40_000, 1) for k in range(30)], prefix="fixture-plan")
    _call(money, "held-for-the-plan")
    account = wallet(money)
    statements = []
    with store.tx() as t:
        t.set_trace_callback(statements.append)
        try:
            ledger.payer_budgets.check(t, account, amount=1, at=ticks[0])
        finally:
            t.set_trace_callback(None)
        reads = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert reads
        searched = set()
        for sql in reads:
            assert not any(name in sql for name in NOT_READ), sql
            details = [row[3] for row in t.execute("EXPLAIN QUERY PLAN " + sql)]
            assert not any(re.match(r"SCAN (?!\(?subquery|\w+ VIRTUAL)", d) for d in details), (sql, details)
            for d in details:
                found = re.match(r"SEARCH (\w+)(?: AS \w+)? USING (?:COVERING )?(?:INDEX (\w+)|(PRIMARY KEY))", d)
                if found:
                    searched.add((found[1], found[2] or "PRIMARY KEY"))
    facts = {(table, index) for table, index in searched if table in ("payer_spend_days", "billing_calls")}
    assert facts == {("payer_spend_days", "PRIMARY KEY"), ("billing_calls", "billing_calls_holds")}, searched


def test_the_check_does_the_same_work_with_1000_and_with_50000_settled_calls(money):
    """VM steps of PayerBudgets.check for two wallets of one store, measured identically: 1,000 and 50,000
    settled calls spread over the same days of this month, three holds each."""
    store, ledger, users, ticks = money
    now = utc("2034-05-20T12:00:00")
    month = utc("2034-05-01T00:00:00")
    ticks[0] = now - 7200
    steps = {}
    for user, total in ((0, 1_000), (1, 50_000)):
        fund(money, 100_000, user=user, order=f"flat-{user}")
        configure(money, user=user, daily_budget=TOP, weekly_budget=TOP, monthly_budget=TOP)
        ctx = context(money, user=user, run=f"flat-template-{user}")
        ledger.open_run(ctx, unit=UNIT, budget=100_000, max_calls=10_000, max_attempts=10_000, deadline=ticks[0] + 900)
        call = ledger.reserve_call(ctx, logical_call_id="one", amount=1, price_id="fixture-sale-v1",
                                   deadline=ticks[0] + 500)
        assert settle(money, dispatch(money, call), usage=Usage(1, 0, 0)).charged == 1
        rows = [(month + (i * 7919) % (now - 7200 - month), 1) for i in range(total - 1)]
        history(store, call, rows, prefix=f"fixture-flat-{user}")
        held(store, call, 3, prefix=f"fixture-flat-held-{user}")
    ticks[0] = now
    for user in (0, 1):
        account = wallet(money, user)
        counted = [0]

        def count():
            counted[0] += 1
            return 0

        with store.tx() as t:
            t.set_progress_handler(count, 1)
            try:
                ledger.payer_budgets.check(t, account, amount=1, at=now)
            finally:
                t.set_progress_handler(None, 0)
        steps[user] = counted[0]
    assert abs(steps[1] - steps[0]) < 0.1 * steps[0], steps


# ---------------------------------------------------------------- operator tools
def test_the_command_line_verifies_and_rebuilds_one_wallet(money):
    import os
    import subprocess
    import sys

    store, ledger, _, _ = money
    fund(money, 1_000)
    configure(money)
    settled_template(money)
    env = {**os.environ, "SYNWEVO_DATA_DIR": str(store.root), "SYNWEVO_PLATFORM_REEXEC": ""}
    script = str(Path(__file__).resolve().parents[2] / "scripts" / "platform.py")

    def run(*args):
        return subprocess.run([sys.executable, script, "cost-rollup", *args], env=env, capture_output=True,
                              text=True, timeout=120)

    verified = run("verify", "--all")
    assert verified.returncode == 0, verified.stderr
    for flags in (("--wallet", wallet(money)), ("--provider", "fixture-provider"), ("--account-ref", "fixture-account"),
                  ("--reason", "fixture operator reason"), ("--actor", "fixture-operator")):
        ignored = run("verify", "--all", *flags)      # a rebuild-only flag on verify is refused, never ignored
        assert ignored.returncode == 2 and flags[0] in ignored.stderr and "{" not in ignored.stdout, (flags, ignored.stderr)
    refused = run("rebuild", "--wallet", wallet(money))
    assert refused.returncode == 2 and "--reason" in refused.stderr
    unknown = run("rebuild", "--wallet", "fixture-no-such-wallet", "--reason", "fixture operator rebuild rehearsal")
    assert unknown.returncode == 2, unknown.stderr
    rebuilt = run("rebuild", "--wallet", wallet(money), "--reason", "fixture operator rebuild rehearsal")
    assert rebuilt.returncode == 0, rebuilt.stderr
    result = json.loads(rebuilt.stdout[rebuilt.stdout.index("{"):])
    assert result["facts"] == 1 and result["scope"] == list(scope(money))
    assert store.one("SELECT actor FROM model_cost_rollup_rebuilds WHERE account_ref=?",
                     (wallet(money),))["actor"].startswith("operator:")
    rollup().assert_consistent(store)


def test_the_wallet_alert_names_the_wallet_only_in_three_languages():
    from synwevo_core.notifications import CATALOGUE, LOCALES
    spec = CATALOGUE["billing.payer_spend.rollup_inconsistent"]
    assert spec["category"] == "platform" and spec["severity"] == "action"
    for part in ("title", "body"):
        for locale in LOCALES:
            text = spec[part][locale]
            assert text and set(re.findall(r"\{(\w+)\}", text)) <= {"wallet"}, (part, locale)
    assert all("{wallet}" in spec["body"][locale] for locale in LOCALES)


# ---------------------------------------------------------------- the benchmark measures what it says
def test_the_benchmark_measures_the_check_the_live_settlement_triggers_and_the_verify(tmp_path, monkeypatch):
    """A tiny run of the personal-limits benchmark (scripts/bench_cost_admission.py --wallet-sizes): one
    wallet's settled history and holds, the check with its plans, the trigger cost a live settlement pays
    (A/B against a copy without the spend triggers), the backfill and the daily verify in a child process."""
    import importlib.util
    monkeypatch.delenv("SYNWEVO_DATA_DIR", raising=False)
    monkeypatch.delenv("DOCWEAVE_DATA_DIR", raising=False)
    path = Path(__file__).resolve().parents[2] / "scripts" / "bench_cost_admission.py"
    spec = importlib.util.spec_from_file_location("fixture_bench_cost_admission_wallet", path)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    result = bench.run_wallet(3_000, samples=3, seed=7, work_parent=tmp_path, holds=30, live_pairs=40)
    counts = result["counts"]
    assert (counts["settled"], counts["held"]) == (3_000, 30) and 0 < counts["day_rows"] <= 20
    assert result["check_ms"]["n"] == 3 and result["exposure"]["monthly"]["held"] != "0"
    plans = " ".join(" ".join(p["plan"]) for p in result["query_plans"])
    assert "payer_spend_days USING PRIMARY KEY" in plans and "billing_calls_holds" in plans
    assert not any(name in p["sql"] for p in result["query_plans"] for name in NOT_READ)
    live = result["triggers_live"]
    assert live["pairs"] == 40 and live["per_transaction"] is True and "extra_stmt_us_p50" in live
    daily = result["verify_daily"]
    assert daily["ok"] and daily["wallet_facts"] >= 3_000 and daily["peak_rss_mb"] > 0
    assert result["verify_all"]["ok"] and result["backfill_facts"] == 3_000


# ---------------------------------------------------------------- review fixes: the bounded verifies, a lost sentinel
@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("created_at", ["soon", 1.5, -DAY], ids=["text", "real", "negative"])
def test_the_bounded_verifies_name_a_malformed_settled_call_outside_their_window_and_alert_once(money, created_at):
    """The hourly run streams the settled calls of two days, the daily one from the previous month's first day. A
    settled call whose created_at is a negative integer or a small REAL sorts below that bound (a TEXT or BLOB
    sorts above every integer); the trigger latched it when it was written, and the bounded runs still name it
    (through that fault, re-read from the call) and alert the administrators once, like the unbounded run."""
    store, ledger, _, ticks = money
    fund(money, 1_000)
    configure(money)
    template = settled_template(money)
    key = "fixture-below-call-0"
    history(store, template, [(created_at, 3)], prefix="fixture-below")
    target = scope(money)
    assert store.one("SELECT code FROM model_cost_rollup_faults WHERE id=?",
                     (rollup().fact_fault_id(key, target),))["code"] == "fact_malformed_settled_call"
    now = ticks[0]
    for kwargs in (dict(since=now - 2 * DAY, now=now, mode="recent"), dict(now=now, mode="daily"),
                   dict(all=True, now=now)):
        report = rollup().CostRollup.check(store, **kwargs)
        assert set(report.mismatched) == {target}, kwargs
        assert report.wallets[target].differences == [{"check": "fact_fault", "call": key}], kwargs
    recorded = rollup().CostRollup.verify(store, since=now - 2 * DAY, now=now, mode="recent", clock=lambda: now)
    assert recorded.mismatched == [target]
    faults = store.all("SELECT code FROM model_cost_rollup_faults WHERE resolved_at IS NULL ORDER BY code")
    assert [f["code"] for f in faults] == ["fact_malformed_settled_call", "verify_mismatch"]
    audits = store.all("SELECT object_id,meta FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'")
    assert [a["object_id"] for a in audits] == [rollup().scope_key(target)]
    assert json.loads(audits[0]["meta"])["source"] == "recent"
    # The daily run after it finds the same, already latched: no second fault, audit or alert.
    rollup().CostRollup.verify(store, now=now, mode="daily", clock=lambda: now)
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_rollup_faults WHERE resolved_at IS NULL")["n"] == 2
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'")["n"] == 1
    with pytest.raises(BillingError, match="cost_rollup_rebuild_refused") as caught:
        rollup().CostRollup.rebuild_wallet(store, account_id=target[2], actor="fixture-operator",
                                           reason="fixture rebuild over a malformed call")
    assert caught.value.attempt_keys == [key]


@pytest.mark.cost_rollup_unchecked
def test_a_lost_sentinel_refuses_every_wallet_unlatched_and_the_next_start_restores_service_by_itself(money):
    """The sentinel row goes only with a write past the gate (or with the whole table). Until the next start
    replays, every wallet's read refuses payer_budget_inconsistent (fail closed: the day rows may be gone with it)
    without latching, the verifier reports it once store-wide and latches no wallet, and the start that replays
    restores service with no rebuild to run (docs/platform/model-risk.md §13, wallets)."""
    store, ledger, users, ticks = money
    fund(money, 1_000)
    fund(money, 1_000, user=1, order="other-user")
    configure(money)
    configure(money, user=1)
    settled_template(money)
    settle(money, dispatch(money, reserve(money, ctx=context(money, user=1, run="other-settled"), amount=3),
                           attempt="other"), usage=Usage(1, 0, 0))
    ticket = _prepare(money, _call(money, "prepared-before"))
    original = store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start")
    assert tamper(store, "DELETE FROM payer_spend_days WHERE account_id='' AND start=0") == 1
    targets = {scope(money), scope(money, 1)}
    for user in (0, 1):
        assert view(money, user)["usage_state"] == "inconsistent"
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$") as refused:
        _call(money, "while-the-sentinel-is-lost")
    assert not isinstance(refused.value, rollup().RollupInconsistent)        # refused, not a latched read
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$") as refused:
        ledger.dispatch(ticket)
    CostAdmission(ledger, current_policy=policy).after_refusal(refused.value)  # what the gateway does: no latch
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    now = ticks[0]
    for kwargs in (dict(since=now - 2 * DAY, now=now, mode="recent"), dict(now=now, mode="daily"),
                   dict(all=True, now=now)):
        report = rollup().CostRollup.check(store, **kwargs)
        assert (report.issues, report.mismatched, report.ok) == (["spend_sentinel_missing"], [], False), kwargs
        assert set(report.wallets) == targets
    rollup().CostRollup.verify(store, since=now - 2 * DAY, now=now, mode="recent", clock=lambda: now)
    rollup().CostRollup.verify(store, now=now, mode="daily", clock=lambda: now)
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    assert store.all("SELECT * FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'") == []
    audits = store.all("SELECT object_id,meta FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")
    assert [a["object_id"] for a in audits] == ["*", "*"]
    assert all(json.loads(a["meta"])["issues"] == ["spend_sentinel_missing"] for a in audits)
    assert [r["status"] for r in store.all("SELECT status FROM model_cost_rollup_audits WHERE mode IN ('recent','daily')")] == [
        "mismatch", "mismatch"]
    backfills = store.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"]
    reopened = Store(store.root)                        # the next start replays: the sentinel and the rows are back
    again = Ledger(reopened, environment="test", namespace=ledger.namespace, authorize=fixture_authorize,
                   verify_payment=fixture_verify, clock=lambda: ticks[0], max_hold_seconds=1000)
    again.payer_budgets = PayerBudgets(again)
    restarted = (reopened, again, users, ticks)
    assert reopened.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"] == backfills + 1
    assert reopened.all("SELECT * FROM payer_spend_days ORDER BY account_id,start") == original
    for user in (0, 1):
        assert view(restarted, user)["usage_state"] == "available"
    assert _call(restarted, "after-the-restart")       # admits again: nothing was latched, nothing to rebuild
    again.dispatch(ticket)
    assert reopened.all("SELECT * FROM model_cost_rollup_faults") == []
    assert reopened.all("SELECT * FROM model_cost_rollup_rebuilds WHERE actor<>'system:backfill'") == []
    report = rollup().CostRollup.verify(reopened, now=now, mode="daily", clock=lambda: now)
    assert report.ok and report.issues == []
    rollup().assert_consistent(reopened)


# ---------------------------------------------------------------- the sentinel lost WITH day rows (2026-11-01)
def restart(money):
    """The next start: a fresh Store (its migration replays the wallets when the sentinel is lost) and a
    Ledger on it, as the same money tuple."""
    store, ledger, users, ticks = money
    reopened = Store(store.root)
    again = Ledger(reopened, environment="test", namespace=ledger.namespace, authorize=fixture_authorize,
                   verify_payment=fixture_verify, clock=lambda: ticks[0], max_hold_seconds=1000)
    again.payer_budgets = PayerBudgets(again)
    return reopened, again, users, ticks


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("damage", ["emptied", "sentinel_plus_one_wallet"])
def test_the_table_emptied_past_the_gate_before_the_verify_latches_no_wallet_and_the_next_start_restores_every_one(
        money, damage):
    """The sentinel goes with the rows: the table emptied past the gate (the schema's "dropped or emptied"), or
    one wallet's rows deleted with it. The hourly and daily verifies that run before the restart report the
    store-wide issue only (one audit and one alert per run, object `*`, deduplicated per day): no wallet gets a
    day-row difference, a fault, an audit or an alert of its own, since the next start replays every wallet's
    rows. Until that start every wallet's read and reservation refuse payer_budget_inconsistent (unlatched);
    after it every wallet is available, 0 faults are open and nothing is left to rebuild (D18 item 7,
    docs/platform/model-risk.md §13 wallets, docs/billing-ledger.md "Per-wallet spend")."""
    store, ledger, users, ticks = money
    recipient = users[2]
    make_platform_admin(store, recipient)             # FIXTURE: the alert recipient (platform_fixture)
    fund(money, 1_000)
    fund(money, 1_000, user=1, order="other-user")
    configure(money)
    configure(money, user=1)
    template = settled_template(money)
    settle(money, dispatch(money, reserve(money, ctx=context(money, user=1, run="other-settled"), amount=3),
                           attempt="other"), usage=Usage(1, 0, 0))
    history(store, template, [(ticks[0] - k * 50_000, k + 1) for k in range(6)], prefix="fixture-emptied",
            steps=ORDERS["status_first"])
    ticket = _prepare(money, _call(money, "prepared-before"))
    original = store.all("SELECT * FROM payer_spend_days ORDER BY account_id,start")
    targets = {scope(money), scope(money, 1)}
    if damage == "emptied":
        assert tamper(store, "DELETE FROM payer_spend_days") == len(original) > 3
    else:
        gone = tamper(store, "DELETE FROM payer_spend_days WHERE account_id IN ('',?)", (wallet(money),))
        assert 1 < gone < len(original)
        assert days(store, wallet(money, 1))          # the other wallet's rows are still there
    assert store.all("SELECT * FROM payer_spend_days WHERE account_id=''") == []
    # Until the restart: every wallet's read refuses, unlatched (what the gateway hands to after_refusal is
    # a plain refusal, not a read-path detection).
    for user in (0, 1):
        assert view(money, user)["usage_state"] == "inconsistent"
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$") as refused:
        _call(money, "while-the-rows-are-lost")
    assert not isinstance(refused.value, rollup().RollupInconsistent)
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$") as refused:
        ledger.dispatch(ticket)
    CostAdmission(ledger, current_policy=policy).after_refusal(refused.value)
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    now = ticks[0]
    for kwargs in (dict(since=now - 2 * DAY, now=now, mode="recent"), dict(now=now, mode="daily"),
                   dict(all=True, now=now)):
        report = rollup().CostRollup.check(store, **kwargs)
        assert (report.issues, report.mismatched, report.ok) == (["spend_sentinel_missing"], [], False), kwargs
        assert set(report.wallets) == targets and all(s.differences == [] for s in report.wallets.values()), kwargs
        assert report.wallet_facts > 0, kwargs         # the settled calls are still streamed (malformed ones)
    assert report.wallet_facts == 8                    # all=True: the template, the other wallet's, the six cloned
    rollup().CostRollup.verify(store, since=now - 2 * DAY, now=now, mode="recent", clock=lambda: now)
    rollup().CostRollup.verify(store, now=now, mode="daily", clock=lambda: now)
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    assert store.all("SELECT * FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'") == []
    audits = store.all("SELECT object_id,meta FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")
    assert [a["object_id"] for a in audits] == ["*", "*"]
    assert [json.loads(a["meta"])["issues"] for a in audits] == [["spend_sentinel_missing"]] * 2
    assert [r["status"] for r in store.all("SELECT status FROM model_cost_rollup_audits WHERE mode IN ('recent','daily')")] == [
        "mismatch", "mismatch"]
    alerts = store.all("SELECT kind,object_id FROM notifications WHERE user_id=? ORDER BY seq", (recipient,))
    assert [(a["kind"], a["object_id"]) for a in alerts] == [("billing.cost.rollup_inconsistent", rollup().scope_key(("*",) * 3))]
    backfills = store.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"]
    # The next start replays every wallet's rows: service is back, nothing was latched, nothing to rebuild.
    restarted = restart(money)
    reopened, again = restarted[0], restarted[1]
    assert reopened.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE provider='@wallets'")["n"] == backfills + 1
    assert reopened.all("SELECT * FROM payer_spend_days ORDER BY account_id,start") == original
    for user in (0, 1):
        assert view(restarted, user)["usage_state"] == "available"
    assert _call(restarted, "after-the-restart")
    assert reserve(restarted, ctx=context(restarted, user=1, run="other-after-the-restart"), amount=1)
    again.dispatch(ticket)
    assert reopened.all("SELECT * FROM model_cost_rollup_faults") == []
    assert reopened.all("SELECT * FROM model_cost_rollup_rebuilds WHERE actor<>'system:backfill'") == []
    report = rollup().CostRollup.verify(reopened, now=now, mode="daily", clock=lambda: now)
    assert report.ok and report.issues == [] and set(report.wallets) == targets
    assert reopened.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=?", (recipient,))["n"] == 1
    rollup().assert_consistent(reopened)


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("created_at", ["soon", 1.5, -DAY], ids=["text", "real", "negative"])
def test_a_malformed_settled_call_is_still_named_while_the_sentinel_and_the_rows_are_lost(money, created_at):
    """With the table emptied past the gate, the verifier still names a malformed settled call (the facts are
    intact and the trigger latched its fault when it was written): that is its wallet's only difference, never
    a day row's, and the other wallet gets none. After the restart that wallet stays refused on its fact (a
    backup restore clears it, no rebuild does) and the other wallet is available."""
    store, ledger, users, ticks = money
    fund(money, 1_000)
    fund(money, 1_000, user=1, order="other-user")
    configure(money)
    configure(money, user=1)
    template = settled_template(money)
    settle(money, dispatch(money, reserve(money, ctx=context(money, user=1, run="other-settled"), amount=3),
                           attempt="other"), usage=Usage(1, 0, 0))
    key = "fixture-malformed-lost-call-0"
    history(store, template, [(created_at, 3)], prefix="fixture-malformed-lost")
    target, other = scope(money), scope(money, 1)
    assert store.one("SELECT code FROM model_cost_rollup_faults WHERE id=?",
                     (rollup().fact_fault_id(key, target),))["code"] == "fact_malformed_settled_call"
    assert tamper(store, "DELETE FROM payer_spend_days") >= 3
    now = ticks[0]
    for kwargs in (dict(since=now - 2 * DAY, now=now, mode="recent"), dict(now=now, mode="daily"),
                   dict(all=True, now=now)):
        report = rollup().CostRollup.check(store, **kwargs)
        assert (report.issues, report.mismatched) == (["spend_sentinel_missing"], [target]), kwargs
        assert report.wallets[target].differences == [{"check": "fact_fault", "call": key}], kwargs
        assert report.wallets[other].differences == [], kwargs
    recorded = rollup().CostRollup.verify(store, since=now - 2 * DAY, now=now, mode="recent", clock=lambda: now)
    assert recorded.mismatched == [target]
    faults = store.all("SELECT code,account_ref FROM model_cost_rollup_faults WHERE resolved_at IS NULL ORDER BY code")
    assert [(f["code"], f["account_ref"]) for f in faults] == [("fact_malformed_settled_call", target[2]),
                                                               ("verify_mismatch", target[2])]
    audits = store.all("SELECT object_id FROM audit_events WHERE action='billing.payer_spend.rollup_inconsistent'")
    assert [a["object_id"] for a in audits] == [rollup().scope_key(target)]
    assert view(money, 1)["usage_state"] == "inconsistent"             # the sentinel: every wallet, until the restart
    restarted = restart(money)
    reopened = restarted[0]
    assert (view(restarted, 0)["usage_state"], view(restarted, 1)["usage_state"]) == ("inconsistent", "available")
    with pytest.raises(BillingError, match="^payer_budget_inconsistent$"):
        _call(restarted, "after-the-restart")
    assert reserve(restarted, ctx=context(restarted, user=1, run="other-after-the-restart"), amount=1)
    report = rollup().CostRollup.check(reopened, all=True, now=now)
    assert (report.issues, report.mismatched) == ([], [target])
    assert report.wallets[target].differences == [{"check": "fact_fault", "call": key}]
    with pytest.raises(BillingError, match="cost_rollup_rebuild_refused") as caught:
        rollup().CostRollup.rebuild_wallet(reopened, account_id=target[2], actor="fixture-operator",
                                           reason="fixture rebuild over a malformed call")
    assert caught.value.attempt_keys == [key]
