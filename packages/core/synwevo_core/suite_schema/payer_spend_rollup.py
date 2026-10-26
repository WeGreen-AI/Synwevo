"""Exact, trigger-maintained per-wallet spend of the personal spending limits; installed only by Store.

docs/billing-ledger.md ("Cost rollup", "Per-wallet spend") and docs/platform/agent-run-budgets.md.
Phase 2 of the cost-capacity work (decision D18): ``PayerBudgets._exposure`` reads these day rows
instead of summing up to 10,000 calls inside the writer lock.

The facts stay where they are: ``billing_calls`` (``account_id``, ``hold_units``, ``created_at`` and
``deadline`` immutable, ``status`` leaves ``reserved`` once, no delete or replace),
``billing_settlements`` and ``billing_journal`` (insert-only; ``call_key`` is the settlement's
primary key, ``journal_id`` UNIQUE, ``amount`` an integer >= 0). Derived from them, per wallet and
UTC day of the call's ``created_at``:

* ``payer_spend_days`` — ``n_set`` settled calls, ``n_paid`` of them with their settlement AND its
  journal entry, and the exact sum of those journal amounts in two int64 limbs (base ``B``).

Whichever arrival completes a call books it, so each call counts once in any order of the three
facts (the product writes journal, settlement, then ``status='settled'`` in one transaction; a
recovery fixture inserts a call already settled; an import with foreign keys off may write the
settlement before its journal):

* a call becoming or inserted ``settled`` books ``n_set`` 1, plus ``n_paid`` 1 and the amount when its
  settlement and journal exist;
* a settlement of a settled call whose journal exists books ``n_paid`` 1 and the amount;
* a journal entry that a settlement of a settled call names books ``n_paid`` 1 and the amount.

Everything reuses the cost rollup's machinery (``model_cost_rollup``): one producer template feeds
``model_cost_rollup_deltas`` with ``kind='spend'`` (no added column: a trigger naming a missing NEW
column would make every delta INSERT, and so every fact write, raise), the apply trigger upserts the
day row with COALESCE and limb arithmetic only (the totality rule: no CHECK, no NOT NULL, nothing
that can make a fact write raise), the GATE refuses any other write to the day rows
(``cost_rollup_derived``), and a settled call whose ``created_at`` is not a non-negative integer
becomes a fault (``kind='fault'``, ``malformed_settled_call``) of its WALLET SCOPE
``(environment, '', account_id)`` through the cost rollup's own fault mapping; ``identifier()`` refuses
'' as a provider, so no provider scope shares it.

The sentinel ``('', 0, 0, 0, 0, 0)`` is lost when the table is dropped or emptied; the next start
replays every settled call once (``migrate``), as does another trigger version (meta
``payer_spend_rollup_triggers``) or a missing marker (``payer_spend_rollup_v1``). Rollback: older code
ignores the table and its triggers keep it current, so a later roll-forward replays nothing.
"""
from __future__ import annotations

import json
import secrets
import time

from .model_cost_rollup import B, _gate, _limb_add, key_label

DAY = 86_400
TABLE = "payer_spend_days"
MARKER = "payer_spend_rollup_v1"
#: Version of the versioned triggers' SQL (VERSIONED).
TRIGGERS_KEY, TRIGGERS_VERSION = "payer_spend_rollup_triggers", "1"
#: The day row's value columns (after the key ``account_id``, ``start``).
VALUES = ("n_set", "n_paid", "amt_hi", "amt_lo")
SENTINEL = ("", 0, 0, 0, 0, 0)
#: Where the wallets' backfill is recorded in ``model_cost_rollup_rebuilds`` / ``_audits``: wallet scopes
#: read their newest global rebuild here, provider scopes at ('', '', ''), so neither family's backfill
#: supersedes the other's verify results.
BACKFILL_SCOPE = ("", "@wallets", "")
MALFORMED_CODE = "malformed_settled_call"

# ---------------------------------------------------------------- the producer template
_MALFORMED = "(typeof(c.created_at)<>'integer' OR c.created_at<0)"
_PAID = "(j.id IS NOT NULL)"            # the settlement exists and so does the journal entry it names
_SPEND_COLUMNS = ("kind", "attempt_key", "call_key", "environment", "provider", "account_ref", "admitted_at",
                  "reserved", "xunit", "c_hi", "c_lo", "code")


def produce_spend(n_set: int, where: str, *, order: str = "") -> str:
    """The one producer: a ``spend`` (or ``fault``) delta per settled call matching ``where``.

    Reads the call (by primary key in the triggers, in rowid order in a replay) and LEFT JOINs its
    settlement, the journal entry the settlement names and the wallet account. Mapping onto the
    cost rollup's delta columns: ``attempt_key``/``call_key`` = call id, ``environment`` = the
    account's (or ''), ``provider`` = '', ``account_ref`` = wallet id, ``admitted_at`` = created_at,
    ``reserved`` = n_set, ``xunit`` = n_paid, ``c_hi``/``c_lo`` = amount in limbs."""
    if n_set not in (0, 1):
        raise ValueError(n_set)
    return (f"INSERT INTO model_cost_rollup_deltas({','.join(_SPEND_COLUMNS)}) SELECT"
            f" CASE WHEN {_MALFORMED} THEN 'fault' ELSE 'spend' END,c.id,c.id,COALESCE(acc.environment,''),'',"
            f"c.account_id,c.created_at,{n_set},CASE WHEN {_PAID} THEN 1 ELSE 0 END,"
            f"CASE WHEN {_PAID} THEN j.amount/{B} ELSE 0 END,CASE WHEN {_PAID} THEN j.amount%{B} ELSE 0 END,"
            f"CASE WHEN {_MALFORMED} THEN '{MALFORMED_CODE}' END"
            " FROM billing_calls c LEFT JOIN billing_settlements s ON s.call_key=c.id"
            " LEFT JOIN billing_journal j ON j.id=s.journal_id"
            " LEFT JOIN billing_accounts acc ON acc.id=c.account_id"
            f" WHERE {where}{order}")


_APPLY = f"""
CREATE TRIGGER IF NOT EXISTS payer_spend_rollup_apply AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='spend' BEGIN
 INSERT INTO {TABLE}(account_id,start,n_set,n_paid,amt_hi,amt_lo)
  VALUES (COALESCE(NEW.account_ref,''),COALESCE(NEW.admitted_at-NEW.admitted_at%{DAY},0),COALESCE(NEW.reserved,0),
   COALESCE(NEW.xunit,0),COALESCE(NEW.c_hi,0),COALESCE(NEW.c_lo,0))
  ON CONFLICT(account_id,start) DO UPDATE SET n_set=COALESCE(n_set,0)+excluded.n_set,
   n_paid=COALESCE(n_paid,0)+excluded.n_paid,{_limb_add('amt')};
END;"""

#: Every trigger whose SQL TRIGGERS_VERSION covers: the four producers and the apply trigger.
VERSIONED = {
    # The call becomes settled (the product's last write; a one-way change: I2).
    "payer_spend_calls_settled": f"""
CREATE TRIGGER IF NOT EXISTS payer_spend_calls_settled AFTER UPDATE OF status ON billing_calls
WHEN NEW.status='settled' AND OLD.status IS NOT 'settled' BEGIN
 {produce_spend(1, 'c.rowid=NEW.rowid')};
 DELETE FROM model_cost_rollup_deltas;
END;""",
    # A call inserted already settled (recovery fixtures, an import, an older release's repair).
    "payer_spend_calls_inserted": f"""
CREATE TRIGGER IF NOT EXISTS payer_spend_calls_inserted AFTER INSERT ON billing_calls
WHEN NEW.status='settled' BEGIN
 {produce_spend(1, 'c.rowid=NEW.rowid')};
 DELETE FROM model_cost_rollup_deltas;
END;""",
    # The settlement arrives last (its call already settled, its journal entry already written).
    "payer_spend_settlements": f"""
CREATE TRIGGER IF NOT EXISTS payer_spend_settlements AFTER INSERT ON billing_settlements
WHEN EXISTS(SELECT 1 FROM billing_calls WHERE id=NEW.call_key AND status='settled')
 AND EXISTS(SELECT 1 FROM billing_journal WHERE id=NEW.journal_id) BEGIN
 {produce_spend(0, 'c.id=NEW.call_key')};
 DELETE FROM model_cost_rollup_deltas;
END;""",
    # The journal entry arrives last (written after its settlement: only with foreign keys off).
    # One UNIQUE probe (billing_settlements.journal_id) per journal insert.
    "payer_spend_journal": f"""
CREATE TRIGGER IF NOT EXISTS payer_spend_journal AFTER INSERT ON billing_journal
WHEN EXISTS(SELECT 1 FROM billing_settlements WHERE journal_id=NEW.id) BEGIN
 {produce_spend(0, "c.id=(SELECT call_key FROM billing_settlements WHERE journal_id=NEW.id) AND c.status='settled'")};
 DELETE FROM model_cost_rollup_deltas;
END;""",
    "payer_spend_rollup_apply": _APPLY,
}

SCHEMA = f"""
-- Derived, per wallet and UTC day of the call's created_at. No CHECK, no NOT NULL (the totality rule).
CREATE TABLE IF NOT EXISTS {TABLE} (
    account_id TEXT, start INTEGER, n_set INTEGER, n_paid INTEGER, amt_hi INTEGER, amt_lo INTEGER,
    PRIMARY KEY(account_id,start)
) WITHOUT ROWID;
{_gate(TABLE)}
{''.join(VERSIONED.values())}
"""


# ---------------------------------------------------------------- replay (backfill / rebuild)
def sentinel_present(c) -> bool:
    return c.execute(f"SELECT 1 FROM {TABLE} WHERE account_id=? AND start=?", SENTINEL[:2]).fetchone() is not None


def _write_sentinel(c) -> None:
    c.execute(f"INSERT INTO {TABLE}(account_id,start,{','.join(VALUES)}) VALUES (?,?,?,?,?,?) "
              "ON CONFLICT(account_id,start) DO NOTHING", SENTINEL)


def replay(c, account: str | None = None) -> dict:
    """Recompute the day rows of ``account`` (every wallet when None) from the settled calls, through
    the same producer and apply triggers as a live write. The caller owns the BEGIN IMMEDIATE.

    Returns {"facts": settled calls replayed, "faults": [the malformed calls' ids, each read as its
    SQLite type and bytes and named by ``key_label``]}."""
    c.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('gate')")
    if account is None:
        c.execute(f"DELETE FROM {TABLE}")
        where, args = "c.status='settled'", ()
    else:
        c.execute(f"DELETE FROM {TABLE} WHERE account_id=?", (account,))
        where, args = "c.account_id=? AND c.status='settled'", (account,)
    facts = c.execute(produce_spend(1, where, order=" ORDER BY c.rowid"), args).rowcount
    rows = c.execute("SELECT DISTINCT typeof(attempt_key),CAST(attempt_key AS BLOB) FROM model_cost_rollup_deltas "
                     "WHERE kind='fault'").fetchall()
    _write_sentinel(c)
    c.execute("DELETE FROM model_cost_rollup_deltas")
    return {"facts": facts, "faults": sorted(key_label(kind, raw) for kind, raw in rows)}


def triggers_current(c) -> bool:
    row = c.execute("SELECT value FROM meta WHERE key=?", (TRIGGERS_KEY,)).fetchone()
    return row is not None and row[0] == TRIGGERS_VERSION


def migrate(c) -> None:
    """Backfill once, inside apply()'s BEGIN IMMEDIATE; a routine start issues no fact query.

    Otherwise (another trigger version, a missing marker or a lost sentinel): recreate the versioned
    triggers (DROP and CREATE in this transaction, so no fact is written between), replay every settled
    call in rowid order, and record the run as a rebuild and a ``backfill`` audit in BACKFILL_SCOPE.
    Store open never refuses: a malformed settled call becomes a fault of its wallet."""
    if (triggers_current(c) and c.execute("SELECT 1 FROM meta WHERE key=?", (MARKER,)).fetchone()
            and sentinel_present(c)):
        return
    for name, sql in VERSIONED.items():
        c.execute(f"DROP TRIGGER IF EXISTS {name}")
        c.execute(sql)
    result = replay(c)
    now = int(time.time())
    c.execute("INSERT INTO model_cost_rollup_rebuilds(id,environment,provider,account_ref,actor,reason,finished_at) "
              "VALUES (?,?,?,?,'system:backfill','backfill',?)", ("crb_" + secrets.token_hex(8), *BACKFILL_SCOPE, now))
    seq = c.execute("SELECT MAX(seq) FROM model_cost_rollup_rebuilds").fetchone()[0]
    c.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,facts,"
              "differences,rebuild_seq,created_at) VALUES (?,?,?,?,'backfill',?,0,?,?,?,?)",
              ("cra_" + secrets.token_hex(8), *BACKFILL_SCOPE, "mismatch" if result["faults"] else "ok", result["facts"],
               json.dumps({"faults": result["faults"][:20], "fault_count": len(result["faults"])}), seq, now))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (MARKER, "1"))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (TRIGGERS_KEY, TRIGGERS_VERSION))
