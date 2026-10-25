"""Exact, trigger-maintained rollups of the platform cost facts; installed only by Store.

docs/billing-ledger.md ("Cost rollup") and docs/platform/agent-run-budgets.md.

The facts stay where they are: ``model_cost_admissions``, ``billing_usage``, ``model_cost_endings``
and ``billing_calls`` (all insert-only or one-way). Everything here is DERIVED from them by SQL
triggers that run inside the writer's own transaction, so the rollup commits or rolls back with
the fact that changed it and no Python ever runs on a fact write path:

* ``model_cost_buckets`` — per scope (environment, provider, account_ref) the admitted bounds,
  resolved bounds and exact resolved cost of every admission, per UTC day (dim ``account``) and,
  for the velocity brake, per payer wallet owner and per requested model in 1, 60 and 3,600 s
  buckets (dims ``payer`` / ``route``). Integers are two int64 limbs (base ``B``); a cost on the
  10^-15 grid (class R) keeps its fractional part on that grid; any other canonical rational
  (class Q) is summed per denominator in its own row (``den``); everything else (class X) is one
  row in ``model_cost_exceptions`` and is read back and parsed exactly as before.
* ``model_cost_inflight`` / ``model_cost_hold`` — the open set: an admission without usage while
  its in-flight window lasts, and while its call is still reserved without a recorded local stop.
* ``model_cost_rollup_faults`` — per-scope latches: a malformed or orphan admission (found by the
  same triggers), a verifier mismatch, or a read-path inconsistency. An open fault refuses the
  scope's paid dispatch until an audited rebuild (``platform.py cost-rollup rebuild``).
* ``model_cost_rollup_accepted`` — malformed or orphan admissions an operator accepted in a rebuild
  (``--accept``): facts cannot be edited, so such an admission stays a fact but is left out of the
  rollup, the read path's clock, parity and records counts and the verifier's oracle from then on,
  and out of the scope's newest admission M (the velocity horizon, prune and the verifier's horizon).
  Usage that arrives for it after the accept latches the scope (fault ``usage:<attempt_key>``).

The GATE: derived tables refuse every direct write (``cost_rollup_derived``) unless a row exists in
the transient ``model_cost_rollup_deltas``, which is empty at rest; a fact trigger inserts its
deltas, the apply triggers on that table write the derived rows, and the fact trigger empties it.
Derived tables carry no CHECK and no NOT NULL and are maintained only with COALESCE and upserts,
so nothing here can make a fact INSERT raise. Each derived table holds a SENTINEL row in scope
``''``: dropping or emptying a table loses it, and the next start replays the facts (``migrate``).

Rollback: older code ignores these tables; the triggers keep them current under it, so a later
roll-forward finds a consistent rollup and replays nothing. The producer triggers carry a version
(meta ``model_cost_rollup_triggers``): a start that finds another one recreates them and replays
the facts once, inside the migration's BEGIN IMMEDIATE (``migrate``).
"""
from __future__ import annotations

import json
import secrets
import time

B = 10**15                 # limb base for integer sums
G = 10**15                 # fixed grid for the fractional part of class-R costs
DAY = 86_400
HORIZON_S = 172_800        # velocity buckets older than (newest admission - this) are never read
SPANS = (1, 60, 3600)
MARKER = "model_cost_rollup_v1"
#: Version of the versioned triggers' SQL (VERSIONED). 2 (2026-10-26): canonical() refuses an embedded
#: NUL (SQLite's GLOB, length() and CAST stop at the first NUL, Python does not), and accepted admissions
#: are left out. 3 (2026-10-26): accepted admissions are left out of the velocity horizon too, and
#: usage that arrives for an accepted admission latches its scope (``usage:<attempt_key>``).
TRIGGERS_KEY, TRIGGERS_VERSION = "model_cost_rollup_triggers", "3"
DERIVED = ("model_cost_buckets", "model_cost_inflight", "model_cost_hold", "model_cost_exceptions")
INT64_MAX = "9223372036854775807"

_BUCKET_KEY = ("environment", "provider", "account_ref", "dim", "member", "span", "start", "unit", "den")
COUNTS = ("n_adm", "n_res", "n_cross", "n_exc")
LIMBS = ("bound", "rbound", "cost")
BUCKET_VALUES = ("n_adm", "n_res", "n_cross", "n_exc", "bound_hi", "bound_lo", "rbound_hi", "rbound_lo",
                 "cost_hi", "cost_lo", "cost_carry", "cost_frac")
DELTA_COLUMNS = ("kind", "attempt_key", "call_key", "environment", "provider", "account_ref", "unit", "owner",
                 "route", "admitted_at", "horizon", "reserved", "cls", "c_hi", "c_lo", "frac", "den", "xunit", "code")

#: One sentinel per derived table, in the empty scope; (table, columns, values).
SENTINELS = (
    ("model_cost_buckets", _BUCKET_KEY + BUCKET_VALUES, ("", "", "", "sentinel", "", 0, 0, "", 0) + (0,) * 12),
    ("model_cost_inflight", ("attempt_key", "environment", "provider", "account_ref", "expires_at"), ("", "", "", "", 0)),
    ("model_cost_hold", ("attempt_key", "call_key", "environment", "provider", "account_ref", "deadline"),
     ("", "", "", "", "", 0)),
    ("model_cost_exceptions", ("attempt_key", "environment", "provider", "account_ref", "admitted_at"), ("", "", "", "", 0)),
)


# ---------------------------------------------------------------- cost classes
def canonical(x: str) -> str:
    """SQL: digits only, no leading zero (except '0'), at most 2^63-1.

    GLOB, length() and CAST all stop at an embedded NUL ('\\x0099' globs, measures and casts as ''),
    so a NUL anywhere makes the text non-canonical first (instr() compares bytes, NUL included)."""
    return (f"(typeof({x})='text' AND instr({x},char(0))=0 AND {x}<>'' AND {x} NOT GLOB '*[^0-9]*'"
            f" AND ({x}='0' OR {x} NOT GLOB '0*') AND (length({x})<19 OR (length({x})=19 AND {x}<='{INT64_MAX}')))")


def class_q(n: str, d: str) -> str:
    return f"({canonical(n)} AND {canonical(d)} AND {d}<>'0')"


def class_r(n: str, d: str) -> str:
    return f"({class_q(n, d)} AND length({d})<=16 AND {G} % CAST({d} AS INTEGER)=0)"


def _whole(n, d):
    return f"(CAST({n} AS INTEGER)/CAST({d} AS INTEGER))"


# ---------------------------------------------------------------- the producer template
_ORPHAN = "(att.id IS NULL OR c.id IS NULL OR acc.id IS NULL)"
_ACCEPTED = "EXISTS(SELECT 1 FROM model_cost_rollup_accepted x WHERE x.attempt_key=a.attempt_key)"
_MALFORMED = ("(typeof(a.admitted_at)<>'integer' OR typeof(a.expires_at)<>'integer' OR a.admitted_at<0"
              f" OR a.expires_at>a.admitted_at+{DAY})")
#: The scope's newest admission M, minus HORIZON_S: the oldest velocity bucket a read can still use.
#: An accepted admission is not M (its admitted_at may be garbage far ahead, which would put every
#: real admission's velocity buckets before the horizon); billing/cost_rollup.py's prune and verifier
#: take M the same way (``cost_rollup.newest``).
NOT_ACCEPTED = "NOT EXISTS(SELECT 1 FROM model_cost_rollup_accepted x WHERE x.attempt_key={}.attempt_key)"
_HORIZON = ("(SELECT h.admitted_at FROM model_cost_admissions h INDEXED BY model_cost_admissions_scope"
            " WHERE h.environment=a.environment AND h.provider=a.provider AND h.account_ref=a.account_ref"
            f" AND typeof(h.admitted_at)='integer' AND {NOT_ACCEPTED.format('h')}"
            f" ORDER BY h.admitted_at DESC LIMIT 1)-{HORIZON_S}")
_JOINS = (" FROM model_cost_admissions a"
          " LEFT JOIN billing_attempts att ON att.id=a.attempt_key"
          " LEFT JOIN billing_calls c ON c.id=att.call_key"
          " LEFT JOIN billing_accounts acc ON acc.id=c.account_id")


def produce(kind: str, where: str, *, order: str = "", arriving: bool = False) -> str:
    """The one producer: admitted/resolved (or fault) deltas for the admissions matching ``where``.

    It reads the admission (by primary key in the triggers, in rowid order in a replay) and LEFT
    JOINs its attempt, call and wallet account; ``resolved`` also joins the usage fact. A malformed
    or orphan admission, or one an operator accepted as such, yields a ``fault`` delta instead,
    never a bucket (the fault row it names already exists, resolved, for an accepted one).

    ``arriving`` (only the usage trigger): the usage fact is being written now. For an accepted
    admission that is cost the operator did not see when accepting it and that no window will
    count, so its fault delta says ``accepted_usage``: the fault ``usage:<attempt_key>`` latches
    the scope until a rebuild. A replay never passes it, so a rebuild or a start never re-latches."""
    bad = f"({_ORPHAN} OR {_MALFORMED} OR {_ACCEPTED})"
    first = f"WHEN {_ACCEPTED} THEN 'accepted_usage' " if arriving else ""
    code = (f"CASE {first}WHEN {_ORPHAN} THEN 'orphan_admission' WHEN {_MALFORMED} THEN 'malformed_admission'"
            f" WHEN {_ACCEPTED} THEN 'accepted_admission' END")
    if kind == "admitted":
        extra, usage = "NULL,0,0,0,0,0", ""
    elif kind == "resolved":
        n, d = "u.cost_n", "u.cost_d"
        r, q = class_r(n, d), class_q(n, d)
        whole = _whole(n, d)
        extra = (f"CASE WHEN {r} THEN 'R' WHEN {q} THEN 'Q' ELSE 'X' END,"
                 f"CASE WHEN {r} THEN {whole}/{B} WHEN {q} THEN CAST({n} AS INTEGER)/{B} ELSE 0 END,"
                 f"CASE WHEN {r} THEN {whole}%{B} WHEN {q} THEN CAST({n} AS INTEGER)%{B} ELSE 0 END,"
                 f"CASE WHEN {r} THEN (CAST({n} AS INTEGER)%CAST({d} AS INTEGER))*({G}/CAST({d} AS INTEGER)) ELSE 0 END,"
                 f"CASE WHEN {r} THEN 0 WHEN {q} THEN CAST({d} AS INTEGER) ELSE 0 END,"
                 "CASE WHEN u.cost_unit IS a.unit THEN 0 ELSE 1 END")
        usage = " JOIN billing_usage u ON u.attempt_key=a.attempt_key"
    else:
        raise ValueError(kind)
    return (f"INSERT INTO model_cost_rollup_deltas({','.join(DELTA_COLUMNS)}) SELECT"
            f" CASE WHEN {bad} THEN 'fault' ELSE '{kind}' END,a.attempt_key,att.call_key,a.environment,a.provider,"
            f"a.account_ref,a.unit,acc.owner,att.requested_model,a.admitted_at,{_HORIZON},a.reserved_units,{extra},{code}"
            f"{_JOINS}{usage} WHERE {where}{order}")


# ---------------------------------------------------------------- the apply triggers
def _limb_add(prefix: str) -> str:
    lo, hi = prefix + "_lo", prefix + "_hi"
    return (f"{lo}=(COALESCE({lo},0)+excluded.{lo})%{B},"
            f"{hi}=COALESCE({hi},0)+excluded.{hi}+(COALESCE({lo},0)+excluded.{lo})/{B}")


_ON_CONFLICT = (f" ON CONFLICT({','.join(_BUCKET_KEY)}) DO UPDATE SET "
                + ",".join(f"{c}=COALESCE({c},0)+excluded.{c}" for c in COUNTS) + ","
                + ",".join(_limb_add(p) for p in LIMBS) + ","
                f"cost_frac=(COALESCE(cost_frac,0)+excluded.cost_frac)%{G},"
                f"cost_carry=COALESCE(cost_carry,0)+excluded.cost_carry+(COALESCE(cost_frac,0)+excluded.cost_frac)/{G}")
_TARGETS = (("account", "''", DAY),) + tuple((dim, member, span) for dim, member in
                                             (("payer", "NEW.owner"), ("route", "NEW.route")) for span in SPANS)


def _upserts(values: dict, *, den: str = "0", when: str = "1") -> str:
    """One upsert per target bucket (1 day + 3 payer + 3 route spans): add ``values``."""
    out = []
    for dim, member, span in _TARGETS:
        start = f"(NEW.admitted_at-NEW.admitted_at%{span})"
        guard = when if dim == "account" else f"{when} AND {start}>=NEW.horizon"
        row = [f"COALESCE(NEW.environment,'')", "COALESCE(NEW.provider,'')", "COALESCE(NEW.account_ref,'')",
               f"'{dim}'", f"COALESCE({member},'')", str(span), start, "COALESCE(NEW.unit,'')", den]
        row += [values.get(c, "0") for c in BUCKET_VALUES]
        out.append(f"INSERT INTO model_cost_buckets({','.join(_BUCKET_KEY + BUCKET_VALUES)}) SELECT {','.join(row)}"
                   f" WHERE {guard}{_ON_CONFLICT};")
    return "\n ".join(out)


def _limbs(value: str, prefix: str) -> dict:
    return {prefix + "_hi": f"({value})/{B}", prefix + "_lo": f"({value})%{B}"}


_ADMITTED = _upserts({"n_adm": "1", **_limbs("COALESCE(NEW.reserved,0)", "bound")})
_RESOLVED = _upserts({"n_res": "1", "n_cross": "COALESCE(NEW.xunit,1)",
                      "n_exc": "CASE WHEN NEW.cls='X' THEN 1 ELSE 0 END",
                      **_limbs("COALESCE(NEW.reserved,0)", "rbound"),
                      "cost_hi": "CASE WHEN NEW.cls='R' THEN COALESCE(NEW.c_hi,0) ELSE 0 END",
                      "cost_lo": "CASE WHEN NEW.cls='R' THEN COALESCE(NEW.c_lo,0) ELSE 0 END",
                      "cost_frac": "CASE WHEN NEW.cls='R' THEN COALESCE(NEW.frac,0) ELSE 0 END"})
_RESOLVED_Q = _upserts({"cost_hi": "COALESCE(NEW.c_hi,0)", "cost_lo": "COALESCE(NEW.c_lo,0)"},
                       den="COALESCE(NEW.den,0)", when="NEW.cls='Q'")


def _gate(table: str) -> str:
    return "".join(f"""
CREATE TRIGGER IF NOT EXISTS {table}_gate_{op.lower()} BEFORE {op} ON {table}
WHEN NOT EXISTS(SELECT 1 FROM model_cost_rollup_deltas)
BEGIN SELECT RAISE(ABORT,'cost_rollup_derived'); END;""" for op in ("INSERT", "UPDATE", "DELETE"))


#: The producer triggers whose SQL holds the cost classification; ``migrate`` recreates them, with
#: the rest of VERSIONED, when the database records another TRIGGERS_VERSION (``CREATE ... IF NOT
#: EXISTS`` never would).
PRODUCERS = {
    "model_cost_admissions_rollup": f"""
CREATE TRIGGER IF NOT EXISTS model_cost_admissions_rollup AFTER INSERT ON model_cost_admissions BEGIN
 {produce('admitted', 'a.attempt_key=NEW.attempt_key')};
 {produce('resolved', 'a.attempt_key=NEW.attempt_key')};
 DELETE FROM model_cost_rollup_deltas;
END;""",
    "model_cost_usage_rollup": f"""
CREATE TRIGGER IF NOT EXISTS model_cost_usage_rollup AFTER INSERT ON billing_usage
WHEN EXISTS(SELECT 1 FROM model_cost_admissions WHERE attempt_key=NEW.attempt_key) BEGIN
 {produce('resolved', 'a.attempt_key=NEW.attempt_key', arriving=True)};
 DELETE FROM model_cost_rollup_deltas;
END;""",
}
#: Every trigger whose SQL a TRIGGERS_VERSION covers: the producers and the fault mapping.
VERSIONED = {**PRODUCERS, "model_cost_rollup_apply_fault": """
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_apply_fault AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='fault' BEGIN
 INSERT INTO model_cost_rollup_faults(id,environment,provider,account_ref,code,detail,detected_at)
  VALUES (CASE WHEN NEW.code='accepted_usage' THEN 'usage:' ELSE 'fact:' END||COALESCE(NEW.attempt_key,'(null)'),
   COALESCE(NEW.environment,''),COALESCE(NEW.provider,''),
   COALESCE(NEW.account_ref,''),'fact_'||COALESCE(NEW.code,'invalid'),COALESCE(NEW.code,'invalid'),
   COALESCE(CAST(strftime('%s','now') AS INTEGER),0))
  ON CONFLICT(id) DO NOTHING;
END;"""}

SCHEMA = f"""
-- Transient: empty at rest; a row exists only while its apply triggers run (the GATE).
CREATE TABLE IF NOT EXISTS model_cost_rollup_deltas (
    kind TEXT, attempt_key TEXT, call_key TEXT, environment TEXT, provider TEXT, account_ref TEXT,
    unit TEXT, owner TEXT, route TEXT, admitted_at INTEGER, horizon INTEGER, reserved INTEGER,
    cls TEXT, c_hi INTEGER, c_lo INTEGER, frac INTEGER, den INTEGER, xunit INTEGER, code TEXT
);
-- dim 'account': member '', span 86400 | 'payer' / 'route': wallet owner / requested model, span 1|60|3600.
-- den 0: counts, bounds and class-R cost; den d: class-Q numerators over d (cost only).
CREATE TABLE IF NOT EXISTS model_cost_buckets (
    environment TEXT, provider TEXT, account_ref TEXT, dim TEXT, member TEXT, span INTEGER, start INTEGER,
    unit TEXT, den INTEGER,
    n_adm INTEGER, n_res INTEGER, n_cross INTEGER, n_exc INTEGER,
    bound_hi INTEGER, bound_lo INTEGER, rbound_hi INTEGER, rbound_lo INTEGER,
    cost_hi INTEGER, cost_lo INTEGER, cost_carry INTEGER, cost_frac INTEGER,
    PRIMARY KEY(environment,provider,account_ref,dim,member,span,start,unit,den)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS model_cost_inflight (
    attempt_key TEXT PRIMARY KEY, environment TEXT, provider TEXT, account_ref TEXT, expires_at INTEGER
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS model_cost_inflight_open ON model_cost_inflight(environment,provider,account_ref,expires_at);
CREATE TABLE IF NOT EXISTS model_cost_hold (
    attempt_key TEXT PRIMARY KEY, call_key TEXT, environment TEXT, provider TEXT, account_ref TEXT, deadline INTEGER
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS model_cost_hold_open ON model_cost_hold(environment,provider,account_ref,deadline);
CREATE INDEX IF NOT EXISTS model_cost_hold_call ON model_cost_hold(call_key);
CREATE TABLE IF NOT EXISTS model_cost_exceptions (
    attempt_key TEXT PRIMARY KEY, environment TEXT, provider TEXT, account_ref TEXT, admitted_at INTEGER
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS model_cost_exceptions_scope ON model_cost_exceptions(environment,provider,account_ref,admitted_at);

-- Not derived: latches, rebuilds and verifier results. Faults are resolved once, never deleted.
CREATE TABLE IF NOT EXISTS model_cost_rollup_faults (
    id TEXT PRIMARY KEY NOT NULL, environment TEXT NOT NULL, provider TEXT NOT NULL, account_ref TEXT NOT NULL,
    code TEXT NOT NULL, detail TEXT NOT NULL, detected_at INTEGER NOT NULL,
    resolved_at INTEGER, resolved_by TEXT, rebuild_id TEXT
);
CREATE INDEX IF NOT EXISTS model_cost_rollup_faults_open ON model_cost_rollup_faults(environment,provider,account_ref)
    WHERE resolved_at IS NULL;
CREATE TABLE IF NOT EXISTS model_cost_rollup_rebuilds (
    seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, environment TEXT NOT NULL, provider TEXT NOT NULL,
    account_ref TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, finished_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_cost_rollup_rebuilds_scope
    ON model_cost_rollup_rebuilds(environment,provider,account_ref,seq);
CREATE TABLE IF NOT EXISTS model_cost_rollup_audits (
    id TEXT PRIMARY KEY NOT NULL, environment TEXT NOT NULL, provider TEXT NOT NULL, account_ref TEXT NOT NULL,
    mode TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('ok','mismatch','superseded')),
    since INTEGER NOT NULL, facts INTEGER NOT NULL, differences TEXT NOT NULL, rebuild_seq INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_cost_rollup_audits_recent ON model_cost_rollup_audits(environment,mode,created_at);
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_faults_no_delete BEFORE DELETE ON model_cost_rollup_faults
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_fault'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_faults_resolve_once BEFORE UPDATE ON model_cost_rollup_faults
WHEN OLD.resolved_at IS NOT NULL OR NEW.resolved_at IS NULL OR NEW.resolved_by IS NULL
  OR NEW.id IS NOT OLD.id OR NEW.environment IS NOT OLD.environment OR NEW.provider IS NOT OLD.provider
  OR NEW.account_ref IS NOT OLD.account_ref OR NEW.code IS NOT OLD.code OR NEW.detail IS NOT OLD.detail
  OR NEW.detected_at IS NOT OLD.detected_at
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_fault'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_rebuilds_no_update BEFORE UPDATE ON model_cost_rollup_rebuilds
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_rebuild'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_rebuilds_no_delete BEFORE DELETE ON model_cost_rollup_rebuilds
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_rebuild'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_audits_no_update BEFORE UPDATE ON model_cost_rollup_audits
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_audit'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_audits_no_delete BEFORE DELETE ON model_cost_rollup_audits
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_audit'); END;
-- Operator decisions, insert-only: a malformed or orphan admission accepted in a rebuild (--accept).
CREATE TABLE IF NOT EXISTS model_cost_rollup_accepted (
    attempt_key TEXT PRIMARY KEY NOT NULL, environment TEXT NOT NULL, provider TEXT NOT NULL,
    account_ref TEXT NOT NULL, code TEXT NOT NULL, rebuild_id TEXT NOT NULL, actor TEXT NOT NULL,
    reason TEXT NOT NULL, accepted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_cost_rollup_accepted_scope ON model_cost_rollup_accepted(environment,provider,account_ref);
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_accepted_no_update BEFORE UPDATE ON model_cost_rollup_accepted
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_accepted'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_accepted_no_delete BEFORE DELETE ON model_cost_rollup_accepted
BEGIN SELECT RAISE(ABORT,'immutable_cost_rollup_accepted'); END;
{''.join(_gate(t) for t in DERIVED)}

-- Apply: one trigger per delta kind. Totality: COALESCE and upserts only.
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_apply_admitted AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='admitted' BEGIN
 {_ADMITTED}
 INSERT INTO model_cost_inflight(attempt_key,environment,provider,account_ref,expires_at)
  SELECT NEW.attempt_key,NEW.environment,NEW.provider,NEW.account_ref,a.expires_at FROM model_cost_admissions a
  WHERE a.attempt_key=NEW.attempt_key AND NOT EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=NEW.attempt_key)
  ON CONFLICT(attempt_key) DO UPDATE SET environment=excluded.environment,provider=excluded.provider,
   account_ref=excluded.account_ref,expires_at=excluded.expires_at;
 INSERT INTO model_cost_hold(attempt_key,call_key,environment,provider,account_ref,deadline)
  SELECT NEW.attempt_key,NEW.call_key,NEW.environment,NEW.provider,NEW.account_ref,c.deadline FROM billing_calls c
  WHERE c.id=NEW.call_key AND c.status='reserved'
   AND NOT EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=NEW.attempt_key)
   AND NOT EXISTS(SELECT 1 FROM model_cost_endings WHERE attempt_key=NEW.attempt_key)
  ON CONFLICT(attempt_key) DO UPDATE SET call_key=excluded.call_key,environment=excluded.environment,
   provider=excluded.provider,account_ref=excluded.account_ref,deadline=excluded.deadline;
END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_apply_resolved AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='resolved' BEGIN
 {_RESOLVED}
 {_RESOLVED_Q}
 INSERT INTO model_cost_exceptions(attempt_key,environment,provider,account_ref,admitted_at)
  SELECT NEW.attempt_key,NEW.environment,NEW.provider,NEW.account_ref,NEW.admitted_at WHERE NEW.cls='X'
  ON CONFLICT(attempt_key) DO UPDATE SET environment=excluded.environment,provider=excluded.provider,
   account_ref=excluded.account_ref,admitted_at=excluded.admitted_at;
 DELETE FROM model_cost_inflight WHERE attempt_key=NEW.attempt_key;
 DELETE FROM model_cost_hold WHERE attempt_key=NEW.attempt_key;
END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_apply_ended AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='ended' BEGIN
 DELETE FROM model_cost_hold WHERE attempt_key=NEW.attempt_key;
END;
CREATE TRIGGER IF NOT EXISTS model_cost_rollup_apply_released AFTER INSERT ON model_cost_rollup_deltas
WHEN NEW.kind='released' BEGIN
 DELETE FROM model_cost_hold WHERE call_key=NEW.call_key;
END;

-- Producers: every fact INSERT (from admit, fixtures, old code or plain SQL) and the two
-- one-way changes that close a hold. The first two carry the cost classification (PRODUCERS);
-- they and the fault mapping (apply_fault) are VERSIONED.
{''.join(VERSIONED.values())}
CREATE TRIGGER IF NOT EXISTS model_cost_endings_rollup AFTER INSERT ON model_cost_endings
WHEN EXISTS(SELECT 1 FROM model_cost_hold WHERE attempt_key=NEW.attempt_key) BEGIN
 INSERT INTO model_cost_rollup_deltas(kind,attempt_key) VALUES ('ended',NEW.attempt_key);
 DELETE FROM model_cost_rollup_deltas;
END;
CREATE TRIGGER IF NOT EXISTS model_cost_calls_rollup AFTER UPDATE OF status ON billing_calls
WHEN OLD.status='reserved' AND NEW.status IS NOT 'reserved'
 AND EXISTS(SELECT 1 FROM model_cost_hold INDEXED BY model_cost_hold_call WHERE call_key=NEW.id) BEGIN
 INSERT INTO model_cost_rollup_deltas(kind,call_key) VALUES ('released',NEW.id);
 DELETE FROM model_cost_rollup_deltas;
END;
"""


# ---------------------------------------------------------------- replay (backfill / rebuild)
def _scope_clause(alias: str, scope) -> tuple[str, tuple]:
    if scope is None:
        return "1", ()
    return f"{alias}.environment=? AND {alias}.provider=? AND {alias}.account_ref=?", tuple(scope)


def sentinels_present(c) -> bool:
    for table, cols, values in SENTINELS:
        key = cols[:9] if table == "model_cost_buckets" else cols[:1]
        where = " AND ".join(f"{col}=?" for col in key)
        if c.execute(f"SELECT 1 FROM {table} WHERE {where}", values[:len(key)]).fetchone() is None:
            return False
    return True


def _write_sentinels(c) -> None:
    for table, cols, values in SENTINELS:
        key = cols[:9] if table == "model_cost_buckets" else cols[:1]
        c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
                  f"ON CONFLICT({','.join(key)}) DO NOTHING", values)


def replay(c, scope=None) -> dict:
    """Recompute the derived rows of ``scope`` (all scopes when None) from the facts, through the
    same producer and apply triggers as a live write. The caller owns the BEGIN IMMEDIATE.

    Returns {"facts": admissions replayed, "faults": [attempt keys of malformed/orphan facts],
    "accepted": [attempt keys an operator accepted, left out as before]}."""
    c.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('gate')")
    for table in DERIVED:
        where, args = _scope_clause(table, scope)
        c.execute(f"DELETE FROM {table} WHERE {where}", args)
    where, args = _scope_clause("a", scope)
    facts = c.execute(produce("admitted", where, order=" ORDER BY a.rowid"), args).rowcount
    c.execute(produce("resolved", where, order=" ORDER BY a.rowid"), args)
    rows = c.execute("SELECT DISTINCT d.attempt_key,x.attempt_key IS NOT NULL FROM model_cost_rollup_deltas d "
                     "LEFT JOIN model_cost_rollup_accepted x ON x.attempt_key=d.attempt_key WHERE d.kind='fault'").fetchall()
    _write_sentinels(c)
    c.execute("DELETE FROM model_cost_rollup_deltas")
    return {"facts": facts, "faults": sorted({r[0] for r in rows if not r[1]}, key=str),
            "accepted": sorted({r[0] for r in rows if r[1]}, key=str)}


def triggers_current(c) -> bool:
    row = c.execute("SELECT value FROM meta WHERE key=?", (TRIGGERS_KEY,)).fetchone()
    return row is not None and row[0] == TRIGGERS_VERSION


def migrate(c) -> None:
    """Backfill once, inside apply()'s BEGIN IMMEDIATE; a routine start issues no fact query.

    A database whose versioned triggers carry another TRIGGERS_VERSION gets them recreated (DROP and
    CREATE in this same transaction, so no fact is written between) and replays once, since its rows
    were derived by other SQL. Store open never refuses: a malformed or orphan admission becomes a
    per-scope fault. The replay holds the writer lock for its whole length (about 48 s per million
    admissions measured locally); other processes opening the Store meanwhile wait for it (Store's
    migration wait), a running writer waits its busy_timeout. A deploy gives the first role only its
    health budget (about two minutes) for it: a store past about two million admissions is migrated
    with the services stopped before such a release (docs/billing-ledger.md, "Backfill")."""
    current = triggers_current(c)
    if current and c.execute("SELECT 1 FROM meta WHERE key=?", (MARKER,)).fetchone() and sentinels_present(c):
        return
    if not current:
        for name, sql in VERSIONED.items():
            c.execute(f"DROP TRIGGER IF EXISTS {name}")
            c.execute(sql)
    result = replay(c)
    now = int(time.time())
    c.execute("INSERT INTO model_cost_rollup_rebuilds(id,environment,provider,account_ref,actor,reason,finished_at) "
              "VALUES (?,'','','','system:backfill','backfill',?)", ("crb_" + secrets.token_hex(8), now))
    seq = c.execute("SELECT MAX(seq) FROM model_cost_rollup_rebuilds").fetchone()[0]
    c.execute("INSERT INTO model_cost_rollup_audits(id,environment,provider,account_ref,mode,status,since,facts,"
              "differences,rebuild_seq,created_at) VALUES (?,'','','','backfill',?,0,?,?,?,?)",
              ("cra_" + secrets.token_hex(8), "mismatch" if result["faults"] else "ok", result["facts"],
               json.dumps({"faults": result["faults"][:20], "fault_count": len(result["faults"])}), seq, now))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (MARKER, "1"))
    c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (TRIGGERS_KEY, TRIGGERS_VERSION))
