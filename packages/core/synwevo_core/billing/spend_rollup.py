"""Reader, uncapped oracle and verifier of the per-wallet spend rollup (cost-capacity phase 2, D18).

The rollup itself (table, gate, triggers, replay) is ``synwevo_core.suite_schema.payer_spend_rollup``;
docs/billing-ledger.md ("Per-wallet spend") describes both. The faults, rebuild rows, audits, latch
and alerts are the cost rollup's (``billing/cost_rollup.py``), in the WALLET SCOPE
``(environment, '', account_id)``. This module:

* ``exposure`` — what ``PayerBudgets._exposure`` returns, in a fixed order: (1) an open fault of the
  wallet refuses ``payer_budget_inconsistent``; (2) the day rows: the table's sentinel must be present
  (else ``payer_budget_inconsistent``, not latched: it goes only with a write past the gate or with the
  table, and only a start's full replay restores it, with every wallet's rows; ``CostRollup.rebuild_wallet``
  refuses meanwhile and a one-wallet replay never writes it, so the sentinel never stands over incomplete
  rows, which every read would count as no spend), and the rows from the earliest
  period start (at most 31, plus any future days) must have their structure, else
  ``SpendRollupInconsistent`` (code ``payer_budget_inconsistent``, latched after the refusal like the
  cost rollup's read-path checks);
  (3) the still-reserved holds, through ``billing_calls_holds``, more than ``CAPACITY`` of them refuse
  ``payer_budget_capacity``; (4) a settled call in the periods without its settlement or journal entry
  refuses ``payer_budget_inconsistent`` (not latched: it clears when the fact arrives); (5) spent per
  period from the day rows, in Python integers. It never reads the settlements, the journal or
  ``billing_calls_payer_period``, and nothing it reads grows with settled volume.
* ``reference_exposure`` — the uncapped oracle: the earlier row-by-row read over the facts, without
  its 10,000-row cap, plus the contract's deviation (a settled call whose ``created_at`` is not a
  non-negative integer refuses its wallet). The verifier compares against it; so does the
  equivalence property test.
* ``wallet_scopes`` / ``check_wallet`` — the verifier's part for wallets (``CostRollup.check``). The
  bounded runs (hourly, daily) stream a window of settled calls; a malformed one outside it (a negative
  or REAL ``created_at`` sorts below every window) is still named, through the fault its write latched
  and re-read from the call, so every such wallet is reported and alerted by the next scheduled run.
  While the table's sentinel is lost (alone, or with the day rows: the table emptied past the gate, or a
  wallet's rows deleted with it) the run reports that once, store-wide (``spend_sentinel_missing``; read
  once per run by ``cost_rollup._check`` and passed down), and compares no wallet's day rows or read:
  every wallet's read already refuses on it and the next start's replay rewrites every row, so a
  difference here would only latch wallets the restart then leaves refused. The malformed settled calls
  are still named: the facts are intact and their faults the triggers'. That start is the only repair:
  the verify command's own open is one, and the command says so (``spend_rollup_replayed_on_open``, exit
  1) instead of calling rows it just wrote consistent; the store-wide issues are alerted with their own
  wording (``billing.cost.rollup_issues``, the issue names), never as one account's.

Semantics kept from the earlier read (``PayerBudgets`` before phase 2): held = every reserved call's
hold, whatever its creation time, namespace or deadline, the same in every period; spent[p] = the
journal amounts reached through the settlements of the settled calls created at or after the start
of p (no upper bound; any journal kind).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

from ..suite_schema import payer_spend_rollup as schema
from ..suite_schema.model_cost_rollup import fact_fault_key
from . import cost_rollup
from .cost_rollup import RollupInconsistent, ScopeReport, key_label, rebuild_seq
from .models import BillingError

B, DAY = schema.B, schema.DAY
#: The only remaining row bound of the personal limits: more reserved calls than this refuse.
CAPACITY = 10_000
#: Limb base for summing up to CAPACITY+1 int64 holds in SQL without overflow.
_LB = 10**14


def periods(at: int) -> dict[str, tuple[int, int]]:
    """The personal limits' UTC periods containing ``at``: {name: (start, exclusive end)}, weeks from Monday."""
    today = datetime.fromtimestamp(at, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return {name: (int(start.timestamp()), int(end.timestamp())) for name, start, end in (
        ("daily", today, today + timedelta(days=1)), ("weekly", week, week + timedelta(days=7)),
        ("monthly", month, following))}


def wallet_scope(environment: str, account: str) -> tuple:
    """A wallet's scope in the cost rollup's faults, rebuilds and audits: no provider ('')."""
    return (environment, "", account)


def is_wallet(scope) -> bool:
    return scope[1] == ""


class SpendRollupInconsistent(RollupInconsistent):
    """A read-path detection on a wallet's day rows (``structure``, or ``undecodable``: a value the read
    cannot decode). Its code is the personal limits' ``payer_budget_inconsistent``: the gateway hands
    the refusal of a reservation (``Ledger.reserve_call``, the first read of a turn) and of a dispatch
    alike to ``after_refusal`` once that transaction rolled back, which latches it as a fault of the
    wallet scope (``CostRollup.latch``) unless a newer rebuild of that wallet has happened since."""

    def __init__(self, scope: tuple, check: str, rebuild_seq: int):
        BillingError.__init__(self, "payer_budget_inconsistent")
        self.scope, self.check, self.rebuild_seq = tuple(scope), check, rebuild_seq


def _structure(row) -> bool:
    """Integer types, a day-aligned start, the amount's low limb in range, 0 <= n_paid <= n_set."""
    values = (row["start"], *(row[c] for c in schema.VALUES))
    if any(type(v) is not int for v in values):
        return False
    return (row["start"] % DAY == 0 and 0 <= row["amt_lo"] < B and row["amt_hi"] >= 0
            and 0 <= row["n_paid"] <= row["n_set"])


def _amount(row) -> int:
    return row["amt_hi"] * B + row["amt_lo"]


# ---------------------------------------------------------------- PayerBudgets._exposure
def exposure(t, *, environment: str, account: str, windows: dict, capacity: int = CAPACITY) -> dict:
    """{period: {"start", "end", "spent", "held"}} of one wallet, in the fixed check order above."""
    scope = wallet_scope(environment, account)
    try:
        return _exposure(t, scope, account, windows, capacity)
    except sqlite3.OperationalError as exc:
        if not cost_rollup._undecodable(exc):
            raise
        raise SpendRollupInconsistent(scope, "undecodable", rebuild_seq(t, scope)) from None


def _exposure(t, scope, account, windows, capacity) -> dict:
    E = min(start for start, _ in windows.values())
    # 1. Faults: a malformed settled call, a verifier mismatch or a latched read of this wallet.
    if cost_rollup._open_fault(t, scope):
        raise BillingError("payer_budget_inconsistent")
    # 2. Day rows. The sentinel first: it goes only with a write past the gate or with the table, and
    # the rows may have gone with it, so every wallet refuses until the next start replays them (not
    # latched: that start clears it, with no rebuild to run; a one-wallet rebuild refuses meanwhile, as
    # it must never bring the sentinel back over incomplete rows). Then the rows from the earliest period
    # start (a TEXT or BLOB start sorts after every integer, so a row written past the gate with one is
    # selected and refused here, never compared in Python).
    if not schema.sentinel_present(t):
        raise BillingError("payer_budget_inconsistent")
    rows = t.execute("SELECT start," + ",".join(schema.VALUES) + " FROM payer_spend_days "
                     "WHERE account_id=? AND start>=?", (account, E)).fetchall()
    if not all(_structure(r) for r in rows):
        raise SpendRollupInconsistent(scope, "structure", rebuild_seq(t, scope))
    # 3. Held: every reserved call, summed in base-10^14 limbs (no overflow at CAPACITY+1 int64 holds).
    held = t.execute(f"SELECT COUNT(*) AS n,SUM(h/{_LB}) AS hi,SUM(h%{_LB}) AS lo FROM (SELECT hold_units AS h "
                     "FROM billing_calls INDEXED BY billing_calls_holds WHERE account_id=? AND status='reserved' "
                     "LIMIT ?)", (account, capacity + 1)).fetchone()
    if held["n"] > capacity:
        raise BillingError("payer_budget_capacity")
    held_units = (held["hi"] or 0) * _LB + (held["lo"] or 0)
    # 4. A settled call in the periods without its settlement or journal entry.
    if any(r["n_paid"] < r["n_set"] for r in rows):
        raise BillingError("payer_budget_inconsistent")
    # 5. Spent per period.
    return {name: {"start": start, "end": end, "held": held_units,
                   "spent": sum(_amount(r) for r in rows if r["start"] >= start)}
            for name, (start, end) in windows.items()}


# ---------------------------------------------------------------- the uncapped oracle
def reference_exposure(t, *, account: str, windows: dict) -> dict:
    """The earlier ``PayerBudgets._exposure`` over the facts, without its 10,000-row cap and with the
    contract's deviation: a settled call of the wallet whose ``created_at`` is not a non-negative
    integer refuses (``payer_budget_inconsistent``) before anything else. Same result dict as
    ``exposure``; refusals ``payer_budget_inconsistent`` only (it has no cap)."""
    E = min(start for start, _ in windows.values())
    if t.execute("SELECT 1 FROM billing_calls INDEXED BY billing_calls_payer_period WHERE account_id=? "
                 "AND status='settled' AND (typeof(created_at)<>'integer' OR created_at<0) LIMIT 1",
                 (account,)).fetchone():
        raise BillingError("payer_budget_inconsistent")
    held = sum(r[0] for r in t.execute("SELECT hold_units FROM billing_calls INDEXED BY billing_calls_holds "
                                       "WHERE account_id=? AND status='reserved'", (account,)))
    spent = {name: 0 for name in windows}
    for r in t.execute("SELECT c.created_at,j.amount FROM billing_calls c INDEXED BY billing_calls_payer_period "
                       "LEFT JOIN billing_settlements s ON s.call_key=c.id LEFT JOIN billing_journal j ON j.id=s.journal_id "
                       "WHERE c.account_id=? AND c.status='settled' AND c.created_at>=?", (account, E)):
        if r["amount"] is None:
            raise BillingError("payer_budget_inconsistent")
        for name, (start, _) in windows.items():
            if r["created_at"] >= start:
                spent[name] += r["amount"]
    return {name: {"start": start, "end": end, "held": held, "spent": spent[name]}
            for name, (start, end) in windows.items()}


# ---------------------------------------------------------------- the verifier's part
#: Where a wallet's settled calls or day rows name it: a loose index scan of each (one seek per wallet).
_WALLET_SOURCES = (("payer_spend_days", ""),
                   ("billing_calls INDEXED BY billing_calls_payer_period", "status='settled' AND "))


def wallet_scopes(c, environment, undecodable=None) -> list[tuple]:
    """Every wallet with day rows or settled calls, as its scope ``(environment, '', account_id)``, each
    part a str: one seek per distinct account id in the day rows' key and in the settled-call index, never
    a table scan. An id no read can name (a BLOB, or a TEXT that is not UTF-8: only a write outside the
    product leaves one) is added to ``undecodable`` and skipped. The sentinel's '' and an account id with
    no account row (only a write with foreign keys off: no read reaches it, ``check`` needs the account)
    are skipped too. The environment is the account's; ``environment`` keeps only that one."""
    found = set()
    for source, extra in _WALLET_SOURCES:
        last = ("null", b"")
        while True:
            after, args = cost_rollup._after("account_id", last)
            row = c.execute(f"SELECT typeof(account_id),CAST(account_id AS BLOB) FROM {source} WHERE {extra}{after} "
                            "ORDER BY account_id LIMIT 1", args).fetchone()
            if row is None:
                break
            value = (row[0], row[1] if row[1] is not None else b"")
            if value == last:
                raise RuntimeError("payer spend scope scan did not advance")
            last = value
            name = cost_rollup._name(value)
            if name is None:
                if undecodable is not None:
                    undecodable.add((key_label(*value),))
                continue
            if name == "":
                continue
            account = c.execute("SELECT environment FROM billing_accounts WHERE id=?", (name,)).fetchone()
            if account is None or (environment is not None and account[0] != environment):
                continue
            found.add(wallet_scope(account[0], name))
    return sorted(found)


def _expected_row(n_set, n_paid, amount) -> dict:
    return {"n_set": n_set, "n_paid": n_paid, "amt_hi": amount // B, "amt_lo": amount % B}


def _malformed_settled_call(c, key) -> bool:
    """Whether the call ``key`` (SQLite type, bytes) is settled with a malformed ``created_at``: one primary
    key seek, whatever the key's type (a TEXT Python cannot decode is bound as its bytes cast to TEXT)."""
    kind, raw = key
    where, args = {"text": ("id=CAST(? AS TEXT)", (raw,)), "blob": ("id=?", (raw,))}.get(kind, ("id IS NULL", ()))
    return c.execute(f"SELECT 1 FROM billing_calls WHERE {where} AND status='settled' AND "
                     "(typeof(created_at)<>'integer' OR created_at<0)", args).fetchone() is not None


def check_wallet(c, scope, *, since, now, mode, sentinel: bool) -> ScopeReport:
    """Recompute one wallet's day rows from its settled calls (streamed, from ``since``'s UTC day; all
    days when None) and compare both directions; name each malformed settled call (``fact_fault``, and
    ``fact_fault_missing`` when its fault row is missing), in a bounded run also those outside the
    streamed days, found through the faults their writes latched (one index seek, bounded by those
    faults, which only a write outside the product leaves) and each re-read from its call. Outside the
    hourly ``recent`` mode, also compare ``exposure`` with the uncapped ``reference_exposure`` at ``now``
    (not when the rollup refused with ``payer_budget_capacity``, its documented bound, nor while the
    wallet has an open fault).

    ``sentinel``: whether the table's sentinel is present, read ONCE per run by ``cost_rollup._check`` in
    the run's read snapshot (where it cannot change between wallets) and passed down, never re-read per
    wallet. While it is lost the day rows are neither compared nor checked for structure and the read is
    not compared with the reference: every wallet's read already refuses on it, the run reports it
    store-wide (``spend_sentinel_missing``) and the next start's replay rewrites every wallet's rows, so a
    difference here would only latch a wallet that the restart then leaves refused (an emptied table, or
    one wallet's rows gone with the sentinel, once latched every such wallet). The settled calls are still
    streamed and the malformed ones named: their facts are intact and their faults the triggers'."""
    environment, _, account = scope
    lo = None if since is None else since - since % DAY
    report = ScopeReport(scope, 0, rebuild_seq(c, scope))
    report.open_faults = c.execute("SELECT COUNT(*) FROM model_cost_rollup_faults WHERE environment=? AND provider=? "
                                   "AND account_ref=? AND resolved_at IS NULL", scope).fetchone()[0]
    expected: dict = {}
    fact_faults = []
    for r in c.execute("SELECT typeof(c.id) AS key_t,CAST(c.id AS BLOB) AS key_b,"
                       "CASE WHEN typeof(c.created_at)='integer' AND c.created_at>=0 THEN c.created_at END AS at,"
                       "j.id IS NOT NULL AS paid,j.amount FROM billing_calls c INDEXED BY billing_calls_payer_period "
                       "LEFT JOIN billing_settlements s ON s.call_key=c.id LEFT JOIN billing_journal j ON j.id=s.journal_id "
                       "WHERE c.account_id=? AND c.status='settled'" + ("" if lo is None else " AND c.created_at>=?"),
                       (account,) if lo is None else (account, lo)):
        report.facts += 1
        key = (r["key_t"], r["key_b"] or b"")
        if r["at"] is None:
            fact_faults.append(key)
            continue
        day = expected.setdefault(r["at"] - r["at"] % DAY, [0, 0, 0])
        day[0] += 1
        if r["paid"]:
            day[1] += 1
            day[2] += r["amount"]
    if lo is not None:
        streamed = set(fact_faults)
        for r in c.execute("SELECT id FROM model_cost_rollup_faults INDEXED BY model_cost_rollup_faults_open WHERE "
                           "environment=? AND provider=? AND account_ref=? AND resolved_at IS NULL AND code=?",
                           (*scope, "fact_" + schema.MALFORMED_CODE)):
            key = fact_fault_key(r["id"])
            if key is not None and key not in streamed and _malformed_settled_call(c, key):
                fact_faults.append(key)
    for key in fact_faults:
        report.differ("fact_fault", call=key_label(*key))
        if c.execute("SELECT 1 FROM model_cost_rollup_faults WHERE id=?",
                     (cost_rollup.fact_fault_id(key, scope),)).fetchone() is None:
            report.differ("fact_fault_missing", call=key_label(*key))
    if sentinel:
        # The day rows, both directions; not while the sentinel is lost (the docstring): ``expected`` is
        # then only what the next start replays.
        for r in c.execute("SELECT start," + ",".join(schema.VALUES) + " FROM payer_spend_days WHERE account_id=?"
                           + ("" if lo is None else " AND start>=?"), (account,) if lo is None else (account, lo)):
            if not _structure(r):
                report.differ("structure", start=r["start"])
            found = {v: r[v] for v in schema.VALUES}
            want = expected.pop(r["start"], None)
            want = None if want is None else _expected_row(*want)
            if want != found or not all(type(v) is int for v in found.values()):
                report.differ("day", start=r["start"], expected=want, found=found)
        for start, want in sorted(expected.items()):
            report.differ("day", start=start, expected=_expected_row(*want), found=None)
    if sentinel and mode != "recent" and not report.open_faults and not fact_faults:
        win = periods(now)
        outcome = {}
        for name, fn, kwargs in (("rollup", exposure, {"environment": environment}), ("reference", reference_exposure, {})):
            try:
                outcome[name] = fn(c, account=account, windows=win, **kwargs)
            except BillingError as exc:
                outcome[name] = {"refused": exc.code, "check": getattr(exc, "check", None)}
        mine, theirs = outcome["rollup"].get("refused"), outcome["reference"].get("refused")
        if mine == "payer_budget_capacity":
            differs = False             # over the holds' bound the read path refuses by design
        elif outcome["rollup"].get("check") is not None:
            differs = True              # a read-path detection (structure, undecodable)
        else:
            differs = outcome["rollup"] != outcome["reference"]
        if differs:
            report.differ("exposure", now=now, rollup=outcome["rollup"], reference=outcome["reference"])
    return report
