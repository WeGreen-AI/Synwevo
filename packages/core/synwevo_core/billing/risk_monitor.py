"""The scheduled side of the paid-model risk controls (docs/platform/model-risk.md).

One round: poll the upstream balance when due, alert on unconfirmed-usage exposure above its
threshold, reconcile the previous UTC day once its boundary reading can exist, and audit ledger
balances once a day. A database lease makes one process run a round at a time, whichever
process started the monitor. Nothing runs before a cost policy is published; the balance poll,
exposure alert and daily reconciliation also need its risk section. Every step is isolated:
one failure is reported by type in the round's result and never stops the others. Construction
starts no thread; ``start()`` does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
from uuid import uuid4

from .. import workers
from ..errors import ServiceError
from .admission import CostAdmission
from .cost_settings import CostSettings
from .ledger import Ledger
from .models import BillingError
from .risk import notify_admins
from .upstream import DAY, SETTLE_DELAY_S, Reconciliation, UpstreamBalance, day_label, units_to_decimal, utc_day

log = logging.getLogger("synwevo.billing.risk")
LEASE_S = 120


@dataclass
class MonitorRound:
    busy: bool = False
    configured: bool = False
    ran: list[str] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)


class RiskMonitor:
    def __init__(self, store, *, environment: str, transport=None, clock=None, interval_s: float = 60.0):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        if not 1 <= interval_s <= 3600:
            raise ValueError("invalid_risk_monitor_interval")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))
        self.transport, self.interval_s = transport, interval_s
        self.owner = uuid4().hex
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _lease(self) -> bool:
        name, now = "risk-monitor:" + self.environment, time.time()
        with self.store.tx() as t:
            t.execute("INSERT INTO model_risk_jobs(name,owner,lease_until) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE "
                      "SET owner=excluded.owner, lease_until=excluded.lease_until "
                      "WHERE model_risk_jobs.lease_until<? OR model_risk_jobs.owner=excluded.owner",
                      (name, self.owner, now + LEASE_S, now))
            row = t.execute("SELECT owner FROM model_risk_jobs WHERE name=?", (name,)).fetchone()
        return row is not None and row[0] == self.owner

    def run_once(self) -> MonitorRound:
        result = MonitorRound()
        if not self._lease():
            result.busy = True
            return result
        try:
            policy = CostSettings(self.store, environment=self.environment, clock=self.clock).policy()
        except (BillingError, ServiceError):
            return result
        result.configured = True
        now = self.clock()
        risk = policy.risk

        def step(name, fn):
            try:
                fn()
                result.ran.append(name)
            except Exception as exc:  # noqa: BLE001 - reported by type only; no message can carry a key
                result.failures.append((name, type(exc).__name__))
                log.error("risk monitor step %s failed: %s", name, type(exc).__name__)

        if risk is not None:
            last = self.store.one("SELECT MAX(observed_at) AS at FROM model_risk_balance_readings WHERE environment=?",
                                  (self.environment,))["at"]
            if last is None or now - last >= risk.balance_poll_s:
                step("balance", lambda: UpstreamBalance(self.store, environment=self.environment,
                                                        transport=self.transport, clock=self.clock).poll())
            if risk.pending_alert is not None:
                step("pending_exposure", lambda: self._pending(policy))
            yesterday = utc_day(now) - DAY
            if now >= utc_day(now) + SETTLE_DELAY_S and not self.store.one(
                    "SELECT 1 AS x FROM model_risk_reconciliations WHERE environment=? AND kind='daily' "
                    "AND window_start=? AND trigger='schedule'", (self.environment, yesterday)):
                step("daily", lambda: Reconciliation(self.store, environment=self.environment,
                                                     clock=self.clock).daily(yesterday))
        if not self.store.one("SELECT 1 AS x FROM model_risk_reconciliations WHERE environment=? AND kind='ledger_audit' "
                              "AND trigger='schedule' AND created_at>=?", (self.environment, utc_day(now))):
            step("ledger_audit", lambda: Reconciliation(self.store, environment=self.environment,
                                                        clock=self.clock).ledger_audit())
        return result

    def _pending(self, policy) -> None:
        ledger = Ledger(self.store, environment=self.environment, namespace="platform-risk", clock=self.clock)
        snapshot = CostAdmission(ledger, current_policy=lambda: policy).snapshot()
        exposure = snapshot["unknown_reserved_units"]
        if exposure > policy.risk.pending_alert:
            notify_admins(self.store, "billing.risk.pending_exposure",
                          params={"amount": units_to_decimal(exposure, policy.unit.scale), "unit": policy.unit.code},
                          dedupe_key=f"risk:pending:{self.environment}:{day_label(utc_day(self.clock()))}",
                          object_id=f"{self.environment}:pending")

    # ------------------------------------------------------------------ thread
    def start(self) -> RiskMonitor:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="model-risk-monitor", daemon=True)
                self._thread.start()
                workers.register(self, self._thread)
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                log.error("risk monitor round failed: %s", type(exc).__name__)
            if self._stop.wait(self.interval_s):
                return

    def stop(self) -> None:
        self._stop.set()

    def close(self, wait: float = 3) -> bool:
        self.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)
            return not thread.is_alive()
        return True
