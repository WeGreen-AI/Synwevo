"""Opt-in platform cost admission, independent of customer wallet holds.

Only a trusted Ledger.dispatch callback may use admit; returning from admit is not send
permission. The enclosing FULL-durability dispatch transaction must first commit. Policy
comes from trusted local configuration, with no default prices, budgets or enablement.
account_ref must identify the actual provider account across key/namespace rotations;
this service cannot establish that identity from a credential or a caller-supplied alias.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json

from . import cost_rollup
from .cost_rollup import RollupInconsistent
from .ledger import Ledger
from .models import AttemptTicket, BillingError, Unit, identifier, integer
from .risk import BrakeTripped, RiskControls, RiskPolicy
from .traffic import TrafficLimits

#: The open set's bound: max_inflight and max_unknown cannot exceed it, and admission enforces
#: unknown_count+1 <= max_unknown, so more open rows than this exist only out of band.
_MAX_OPEN = cost_rollup.MAX_OPEN
#: The longest a customer hold can outlive its dispatch (Ledger.max_hold_seconds' ceiling), and so
#: the longest an admission can stay an open unknown or in flight.
_OPEN_S = cost_rollup.OPEN_S
_windows = cost_rollup.windows


@dataclass(frozen=True)
class CostPolicy:
    revision: str
    account_ref: str
    provider: str
    unit: Unit
    enabled: bool
    daily_budget: int
    weekly_budget: int
    monthly_budget: int
    unknown_budget: int
    max_inflight: int
    max_unknown: int
    # Request/token rates, per-payer in-flight calls and waiting; None keeps the limits above only.
    traffic: TrafficLimits | None = None
    # Largest conservative cost bound one call may reserve; None sets no per-call cap.
    max_call_cost: int | None = None
    # Brake, breaker thresholds and allowlist mode (billing/risk.py); None keeps their defaults.
    risk: RiskPolicy | None = None

    def __post_init__(self):
        for value in (self.revision, self.account_ref, self.provider):
            identifier(value)
        if not isinstance(self.unit, Unit) or type(self.enabled) is not bool:
            raise BillingError("invalid_cost_policy")
        for value in (self.daily_budget, self.weekly_budget, self.monthly_budget, self.unknown_budget):
            integer(value)
        for value in (self.max_inflight, self.max_unknown):
            integer(value, positive=True, maximum=_MAX_OPEN)
        if self.traffic is not None and (not isinstance(self.traffic, TrafficLimits)
                                         or self.traffic.payer_inflight > self.max_inflight):
            raise BillingError("invalid_cost_policy")
        if self.max_call_cost is not None:
            integer(self.max_call_cost)
        if self.risk is not None and not isinstance(self.risk, RiskPolicy):
            raise BillingError("invalid_cost_policy")


class CostAdmission:
    def __init__(self, ledger: Ledger, *, current_policy):
        if not isinstance(ledger, Ledger) or not callable(current_policy):
            raise BillingError("invalid_cost_admission")
        self.ledger = ledger
        self.current_policy = current_policy

    def _policy(self) -> CostPolicy:
        policy = self.current_policy()
        if type(policy) is not CostPolicy:
            raise BillingError("invalid_cost_policy")
        return policy

    def _transaction(self, transaction):
        # in_transaction alone accepts a manually opened deferred transaction. Store's
        # depth also establishes its BEGIN IMMEDIATE writer lock, on this very connection.
        store = self.ledger.store
        local = store._local
        if (transaction is None or transaction is not getattr(local, "c", None)
                or not getattr(local, "depth", 0) or not transaction.in_transaction):
            raise BillingError("cost_admission_requires_transaction")
        if transaction.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise BillingError("cost_admission_requires_full_durability")

    def _counters(self, t, policy: CostPolicy, now: int, *, records: bool = False) -> dict:
        """Exact counters of the policy's provider account, from the trigger-maintained rollup
        (billing/cost_rollup.py). Not scoped by namespace, policy revision, payer, key or unit: a
        differing unit is a conflict, never permission to hide existing exposure. No read grows
        with settled history; the only row caps left are the open set and class-X facts."""
        return cost_rollup.counters(t, environment=self.ledger.environment, provider=policy.provider,
                                    account_ref=policy.account_ref, unit=policy.unit, now=now, records=records)

    def admit(self, transaction, ticket: AttemptTicket, *, amount: int, expires_at: int) -> None:
        """Reserve a conservative cost bound atomically with Ledger's one-shot send fence.

        Expiry releases the concurrency slot. Without usage, the bound counts as unknown while in
        flight and, unless the platform stopped the stream itself (record_local_stop), until the
        payer's hold ends; after that it is final at its bound, which stays in its UTC periods
        (docs/platform/model-risk.md §1). Actual costs replace the bound without capping, even
        for late/platform_fault usage, in the ORIGINAL UTC periods.
        Raise through dispatch so that any refusal or subsequent dispatch failure rolls back.
        """
        self._transaction(transaction)
        integer(amount)
        integer(expires_at)
        if not isinstance(ticket, AttemptTicket):
            raise BillingError("invalid_attempt_ticket")
        for value in (ticket.attempt_key, ticket.owner, ticket.fence):
            identifier(value)
        t, ledger = transaction, self.ledger
        now = ledger._now()
        if expires_at <= now or expires_at > now + _OPEN_S:
            raise BillingError("invalid_cost_expiry")
        policy = self._policy()
        if not policy.enabled:
            raise BillingError("cost_admission_disabled")
        attempt = ledger._attempt(t, ticket.attempt_key)
        call = ledger._call(t, attempt["call_key"])
        run = t.execute("SELECT deadline FROM billing_runs WHERE id=?", (call["run_key"],)).fetchone()
        if (attempt["environment"] != ledger.environment
                or attempt["owner"] != ticket.owner or attempt["fence"] != ticket.fence
                or attempt["status"] != "prepared" or attempt["lease_until"] <= now
                or call["status"] != "reserved" or call["cancel_requested"]
                or call["deadline"] <= now or run["deadline"] <= now):
            raise BillingError("dispatch_fenced")
        price = ledger._price(t, attempt["cost_price_id"], "cost")
        if price.provider != policy.provider or attempt["provider"] != policy.provider:
            raise BillingError("cost_provider_mismatch")
        if price.unit != policy.unit:
            raise BillingError("cost_unit_mismatch")
        if policy.max_call_cost is not None and amount > policy.max_call_cost:
            # The per-call cap is checked where the call's cost bound is reserved: nothing is sent.
            raise BillingError("cost_call_limit_exceeded")
        if t.execute("SELECT 1 FROM model_cost_admissions WHERE attempt_key=?", (ticket.attempt_key,)).fetchone():
            raise BillingError("cost_attempt_already_admitted")
        counters = self._counters(t, policy, now)
        for period, total in counters["totals"].items():
            if total + amount > getattr(policy, period + "_budget"):
                raise BillingError("cost_" + period + "_budget_exceeded")
        if counters["unknown_reserved_units"] + amount > policy.unknown_budget:
            raise BillingError("cost_unknown_budget_exceeded")
        if counters["unknown_count"] + 1 > policy.max_unknown:
            raise BillingError("cost_unknown_limit")
        if counters["inflight"] + 1 > policy.max_inflight:
            raise BillingError("cost_inflight_limit")
        # Breaker, allowlist, suspensions and velocity: refusals only, still before any write.
        RiskControls(ledger.store, environment=ledger.environment, clock=ledger._now).guard(
            t, policy=policy, call=call, attempt=attempt, amount=amount, now=now)
        # No writes precede all checks. Store/schema own persistence and immutability.
        t.execute(
            "INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
            "reserved_units,policy_revision,limits_json,admitted_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ticket.attempt_key, ledger.environment, policy.provider, policy.account_ref, policy.unit.code,
             amount, policy.revision, json.dumps(asdict(policy), sort_keys=True, separators=(",", ":")), now, expires_at),
        )

    def record_local_stop(self, attempt_key: str) -> bool:
        """The platform closed this admitted call's stream itself after dispatch (the person's
        Stop, the output guard, a shutdown), so its usage stays unknown for a known reason.

        Its conservative bound still counts in the cost windows and in the payer's and route's
        velocity windows, and as unknown while in flight; it no longer counts against the
        platform-wide unknown limits for the rest of its hold. One person's stops are their own
        exposure, not a reason to refuse everybody's next call. Idempotent; returns whether
        the attempt is (now) recorded. Nothing is recorded for an attempt with usage."""
        identifier(attempt_key)
        with self.ledger.store.tx() as t:
            if not t.execute("SELECT 1 FROM model_cost_admissions WHERE attempt_key=?", (attempt_key,)).fetchone():
                return False
            if t.execute("SELECT 1 FROM model_cost_endings WHERE attempt_key=?", (attempt_key,)).fetchone():
                return True
            if t.execute("SELECT 1 FROM billing_usage WHERE attempt_key=?", (attempt_key,)).fetchone():
                return False
            t.execute("INSERT INTO model_cost_endings(attempt_key,ending,recorded_at) VALUES (?,?,?)",
                      (attempt_key, "stopped_locally", self.ledger._now()))
        return True

    def after_refusal(self, exc: BaseException) -> None:
        """After the refusing dispatch transaction rolled back: latch a velocity breach, or a
        read-path rollup inconsistency as a fault of its scope (unless a newer rebuild exists).

        The gateway calls this with the refusal it re-raises. Anything else is ignored; a latch
        that fails is re-detected (the refusal is computed from facts) on the next attempt."""
        if isinstance(exc, RollupInconsistent):
            try:
                cost_rollup.CostRollup.latch(self.ledger.store, exc)
            except Exception as error:  # noqa: BLE001 - never mask the caller's refusal
                import logging
                logging.getLogger("synwevo.billing.cost_rollup").error("rollup latch failed: %s", type(error).__name__)
            return
        if isinstance(exc, BrakeTripped):
            try:
                RiskControls(self.ledger.store, environment=self.ledger.environment,
                             clock=self.ledger._now).record_trip(exc.trip)
            except Exception as error:  # noqa: BLE001 - never mask the caller's refusal
                import logging
                logging.getLogger("synwevo.billing.risk").error("brake latch failed: %s", type(error).__name__)

    def snapshot(self) -> dict:
        """Internal, consistent counters; neither a public projection nor send permission."""
        with self.ledger.store.tx() as t:
            policy, now = self._policy(), self.ledger._now()
            counters = self._counters(t, policy, now, records=True)
            totals = counters.pop("totals")
            rollup = cost_rollup.CostRollup.state(t, (self.ledger.environment, policy.provider, policy.account_ref))
            return {"environment": self.ledger.environment, "provider": policy.provider,
                    "account_ref": policy.account_ref, "policy_revision": policy.revision,
                    "unit": asdict(policy.unit), "as_of": now, **counters, "rollup": rollup,
                    **{name: {"numerator": value.numerator, "denominator": value.denominator}
                       for name, value in totals.items()}}
