"""Local, restartable metering maintenance; never resumes an Agent or a provider request.

Composition: construct ``MeteringRecovery(gateway=approved_gateway)``, run Agent's existing
startup interruption recovery first, then start this worker when ``start_jobs`` is true.
Register ``close`` with the application's shutdown hooks. Construction starts no threads
and neither discovers credentials nor grants model/payment authority.

Only terminal turns are projected here. Startup interruption must concern dead workers;
this worker neither declares another process dead nor fences a still-live engine writer.
The engine adds usage after a provider returns, so
projecting a running/waiting turn would race that addition. Live projection requires the
engine to replace its incremental ``_record_usage`` with the same authoritative aggregation
and to fence late writers after interruption; it must not call this projection mid-step.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import threading
import time

from synwevo_core import workers
from synwevo_core.billing import BillingError, ChargeContext, Ledger, Usage
from synwevo_core.billing.models import integer
from synwevo_core.store import now

from .gateway import MeteredDeepSeek


@dataclass(frozen=True)
class RecoveredCall:
    """Confirmed local facts, not a claim that this worker alone created a money entry."""

    call_id: str
    turn_id: str
    state: str
    charge: int | None = None
    late: bool | None = None
    outcome: str | None = None


@dataclass(frozen=True)
class RecoveredUsage:
    turn_id: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RecoveryFailure:
    phase: str
    record_id: str | None
    # Deliberately exclude exception messages, observations, prompts and credentials.
    error_type: str


@dataclass(frozen=True)
class RecoveryRound:
    calls_scanned: int = 0
    turns_scanned: int = 0
    calls: tuple[RecoveredCall, ...] = ()
    usages: tuple[RecoveredUsage, ...] = ()
    failures: tuple[RecoveryFailure, ...] = ()
    stopped: bool = False
    budget_exhausted: bool = False
    busy: bool = False


class _ReleasedThenFailed(Exception):
    """Preserve a committed release in the report when later reconciliation fails."""

    def __init__(self, fact: RecoveredCall, error: Exception):
        self.fact, self.error_type = fact, type(error).__name__
        super().__init__(self.error_type)


class _CallLedger(Ledger):
    """Reuse Ledger's money transactions with expiry restricted to one selected call.

    The public Ledger methods (including settlement/cancel) implicitly call unbounded
    ``_expire``. A separate instance avoids monkeypatching the live gateway or copying its
    money algorithm. Keep this small adapter until Ledger offers scoped expiry publicly.
    It has no payment/dispatch authorizer and shares only Store, scope and clock.
    """

    def __init__(self, source: Ledger, call_key: str):
        super().__init__(source.store, environment=source.environment, namespace=source.namespace,
                         clock=source.clock, max_hold_seconds=source.max_hold_seconds)
        self.call_key = call_key

    def _expire(self, t):
        call = self._call(t, self.call_key)
        if call["status"] == "reserved" and call["deadline"] <= self._now():
            self._release(t, call, "deadline_final_no_later_charge")
            return 1
        return 0


class MeteringRecovery:
    """One bounded pass now, or periodic passes on a registered daemon thread.

    Each pass visits at most ``batch_size`` calls and ``batch_size`` terminal turns; each
    turn reads at most MAX_TURN_CALLS + 1 physical attempts. Keyset cursors wrap, so a poison
    row cannot permanently starve later rows. Cursors are only scheduling hints: all money
    idempotency and projection compare-and-set decisions reside in Store transactions.

    ``round_seconds`` and stop are cooperative between records, not SQLite interrupts.
    A current transaction may finish after stop (Store's busy timeout still applies).
    ``close`` reports whether it joined; no further record starts after stop.
    Returned facts and ``last_round`` are internal diagnostics, not a public billing API.
    """

    MAX_TURN_CALLS = 500

    def __init__(self, *, gateway: MeteredDeepSeek, batch_size: int = 100,
                 interval_seconds: float = 30, round_seconds: float = 5):
        integer(batch_size, positive=True, maximum=500)
        for value in (interval_seconds, round_seconds):
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or value <= 0 or value > 86400):
                raise ValueError("invalid_recovery_interval")
        self.gateway = gateway
        self.store, self.ledger = gateway.store, gateway.ledger
        self.batch_size = batch_size
        self.interval_seconds, self.round_seconds = interval_seconds, round_seconds
        self._call_cursor = self._turn_cursor = 0
        self._stop = threading.Event()
        self._round_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_round: RecoveryRound | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> MeteringRecovery:
        with self._lifecycle_lock:
            if self._stop.is_set():
                raise RuntimeError("metering_recovery_closed")
            if not self.running:
                self._thread = threading.Thread(target=self._loop, name="agent-metering-recovery", daemon=True)
                workers.register(self, self._thread)
                self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def close(self, wait: float = 3) -> bool:
        if isinstance(wait, bool) or not isinstance(wait, (int, float)) or not math.isfinite(wait) or wait < 0:
            raise ValueError("invalid_recovery_wait")
        with self._lifecycle_lock:
            self.stop()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)
        return not self.running and not self._round_lock.locked()

    def _loop(self):
        while not self._stop.is_set():
            self.run_once()
            if self._stop.wait(self.interval_seconds):
                break

    def run_once(self) -> RecoveryRound:
        if not self._round_lock.acquire(blocking=False):
            return RecoveryRound(busy=True, stopped=self._stop.is_set())
        try:
            deadline = time.monotonic() + self.round_seconds
            calls, usages, failures = [], [], []
            counts = {"calls": 0, "turns": 0}
            for phase, scan, process, target, cursor in (
                ("calls", self._scan_calls, self._recover_call, calls, "_call_cursor"),
                ("turns", self._scan_turns, self._sync_usage, usages, "_turn_cursor"),
            ):
                if self._stop.is_set() or time.monotonic() >= deadline:
                    break
                try:
                    rows = scan()
                except Exception as exc:  # One failed scan must not kill the periodic worker.
                    failures.append(RecoveryFailure(phase, None, type(exc).__name__))
                    continue
                completed_page = True
                for row in rows:
                    if self._stop.is_set() or time.monotonic() >= deadline:
                        completed_page = False
                        break
                    setattr(self, cursor, row["seq"])
                    counts[phase] += 1
                    try:
                        fact = process(row)
                        if fact is not None:
                            target.append(fact)
                    except Exception as exc:  # Bad evidence/one locked row cannot starve the rest.
                        if isinstance(exc, _ReleasedThenFailed):
                            calls.append(exc.fact)
                        failures.append(RecoveryFailure(phase, row["id"],
                            exc.error_type if isinstance(exc, _ReleasedThenFailed) else type(exc).__name__))
                if completed_page and len(rows) < self.batch_size:
                    setattr(self, cursor, 0)
            result = RecoveryRound(counts["calls"], counts["turns"], tuple(calls), tuple(usages),
                                   tuple(failures), self._stop.is_set(), time.monotonic() >= deadline)
            self.last_round = result
            return result
        finally:
            self._round_lock.release()

    def _scan_calls(self):
        # Join the immutable logical identity as well: a crash can precede writing call_key
        # back to the gateway fence. Such a hold must still reach its final deadline.
        return self.store.all(
            "SELECT m.rowid AS seq,m.id,m.attempt_key,b.id AS call_key,r.run_id "
            "FROM agent_metered_calls m JOIN billing_runs r ON r.id=m.run_key "
            "JOIN billing_calls b ON b.run_key=m.run_key AND b.logical_call_id=m.logical_id "
            "WHERE r.environment=? AND r.namespace=? AND m.metering_state<>'settled' AND m.rowid>? "
            "AND (EXISTS(SELECT 1 FROM agent_model_observations o WHERE o.call_id=m.id) "
            "OR (m.metering_state='pending' AND (b.status='released' OR (b.status='reserved' AND b.deadline<=?)))) "
            "ORDER BY m.rowid LIMIT ?",
            (self.ledger.environment, self.ledger.namespace, self._call_cursor, self.ledger._now(), self.batch_size))

    def _recover_call(self, row):
        ledger = _CallLedger(self.ledger, row["call_key"])
        # Expiry has its own committed transaction even if decoding/settlement later fails.
        ledger.expire_due()
        with self.store.tx() as t:
            released = t.execute("SELECT 1 FROM billing_calls WHERE id=? AND status='released'",
                                 (row["call_key"],)).fetchone()
            changed = released and t.execute(
                "UPDATE agent_metered_calls SET metering_state='released' WHERE id=? AND metering_state='pending'",
                (row["id"],)).rowcount
        released_fact = RecoveredCall(row["id"], row["run_id"], "released") if changed else None
        try:
            observation = self.store.one("SELECT attempt_key,evidence FROM agent_model_observations WHERE call_id=?",
                                         (row["id"],))
            if observation:
                evidence = json.loads(observation["evidence"])
                if evidence.get("usage_reliability") == "trusted_terminal":
                    attempt = self.store.one("SELECT call_key FROM billing_attempts WHERE id=?", (observation["attempt_key"],))
                    if (observation["attempt_key"] != row["attempt_key"] or not attempt
                            or attempt["call_key"] != row["call_key"]):
                        raise BillingError("usage_evidence_mismatch")
                    # Reuse the gateway's exact proof normalization, late-cost and failure rules.
                    # This local instance has no transport or dispatch authority; never call step.
                    local = MeteredDeepSeek(ledger, current_authority=lambda _: False)
                    settlement = local._settle(row["id"])
                    return RecoveredCall(row["id"], row["run_id"], "settled", settlement.charged,
                                         settlement.late, settlement.outcome)
        except Exception as exc:
            if released_fact:
                raise _ReleasedThenFailed(released_fact, exc) from None
            raise
        return released_fact

    def _scan_turns(self):
        return self.store.all(
            "SELECT t.rowid AS seq,t.id,r.id AS run_key FROM agent_turns t "
            "JOIN billing_runs r ON r.run_id=t.id JOIN agent_metered_runs m ON m.run_key=r.id "
            "WHERE r.environment=? AND r.namespace=? AND t.rowid>? AND t.usage_state='pending' "
            "AND t.status IN ('succeeded','failed','cancelled') AND t.provider='deepseek' AND t.credential='platform' "
            "ORDER BY t.rowid LIMIT ?",
            (self.ledger.environment, self.ledger.namespace, self._turn_cursor, self.batch_size))

    def _sync_usage(self, row):
        with self.store.tx() as t:
            turn = t.execute("SELECT * FROM agent_turns WHERE id=? AND usage_state='pending' "
                             "AND status IN ('succeeded','failed','cancelled') "
                             "AND provider='deepseek' AND credential='platform'", (row["id"],)).fetchone()
            if not turn:
                return None
            run = t.execute("SELECT context FROM billing_runs WHERE id=?", (row["run_key"],)).fetchone()
            context = ChargeContext(**json.loads(run["context"]))
            if context.run_id != turn["id"] or context.actor_user_id != turn["user_id"] or context.payer_user_id != turn["user_id"]:
                raise BillingError("usage_owner_mismatch")
            # A mixed deployment/namespace history cannot be reconstructed from one gateway.
            if t.execute("SELECT 1 FROM billing_runs r JOIN agent_metered_runs m ON m.run_key=r.id "
                         "WHERE r.run_id=? AND r.id<>? LIMIT 1", (turn["id"], row["run_key"])).fetchone():
                return None
            if t.execute("SELECT 1 FROM billing_calls b WHERE b.run_key=? AND NOT EXISTS "
                         "(SELECT 1 FROM agent_metered_calls m WHERE m.run_key=b.run_key AND m.call_key=b.id) LIMIT 1",
                         (row["run_key"],)).fetchone():
                return None  # Every physical call in this run must belong to the projection.
            records = t.execute(
                "SELECT m.id,m.logical_id,m.metering_state,m.attempt_key,a.id AS physical_id,a.status,b.status AS call_status,u.facts "
                "FROM agent_metered_calls m LEFT JOIN billing_calls b ON b.id=m.call_key AND b.run_key=m.run_key "
                "AND b.logical_call_id=m.logical_id LEFT JOIN billing_attempts a ON a.call_key=b.id "
                "LEFT JOIN billing_usage u ON u.attempt_key=a.id WHERE m.run_key=? ORDER BY m.rowid LIMIT ?",
                (row["run_key"], self.MAX_TURN_CALLS + 1)).fetchall()
            if not records or len(records) > self.MAX_TURN_CALLS or turn["steps"] > len(records):
                return None
            # Covers earlier steps too, including the final step whose engine write was lost.
            if {r["logical_id"] for r in records} != {f"step:{i}" for i in range(1, len(records) + 1)}:
                return None
            input_tokens = output_tokens = 0
            for record in records:
                if (record["metering_state"] != "settled" or record["status"] != "settled"
                        or record["physical_id"] != record["attempt_key"] or not record["facts"]
                        or record["call_status"] not in ("settled", "released")):
                    return None  # Missing/unreliable/expired unknown usage never becomes zero.
                facts = json.loads(record["facts"])
                if facts["provider"] != "deepseek" or facts["evidence_ref"] != "model-observation:" + record["id"]:
                    raise BillingError("usage_evidence_mismatch")
                usage = Usage(**facts["usage"])
                input_tokens += usage.input_uncached + usage.input_cached
                output_tokens += usage.output  # Reasoning is already included in output.
            # Replace, never increment. SQLite serializes this CAS across Store instances.
            stamp = now()
            changed = t.execute("UPDATE agent_turns SET input_tokens=?,output_tokens=?,usage_state='known',updated_at=? "
                                "WHERE id=? AND usage_state='pending'",
                                (input_tokens, output_tokens, stamp, turn["id"])).rowcount
            if not changed:
                return None
            # The refresh event and public projection commit together, including on retry.
            # Existing SSE readers poll persisted events across processes; no engine callback
            # or synthetic turn completion is needed. The payload carries no billing evidence.
            t.execute("INSERT INTO agent_events (conversation_id,user_id,turn_id,type,body,created_at) "
                      "VALUES (?,?,?,'usage.reconciled',?,?)",
                      (turn["conversation_id"], turn["user_id"], turn["id"],
                       json.dumps({"turn_id": turn["id"]}, separators=(",", ":")), stamp))
            return RecoveredUsage(turn["id"], input_tokens, output_tokens)
