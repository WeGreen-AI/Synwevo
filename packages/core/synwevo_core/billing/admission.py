"""Opt-in platform cost admission, independent of customer wallet holds.

Only a trusted Ledger.dispatch callback may use admit; returning from admit is not send
permission. The enclosing FULL-durability dispatch transaction must first commit. Policy
comes from trusted local configuration, with no default prices, budgets or enablement.
account_ref must identify the actual provider account across key/namespace rotations;
this service cannot establish that identity from a credential or a caller-supplied alias.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import json

from .ledger import Ledger
from .models import AttemptTicket, BillingError, Unit, identifier, integer
from .traffic import TrafficLimits

_MAX_RECORDS = 10_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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

    def __post_init__(self):
        for value in (self.revision, self.account_ref, self.provider):
            identifier(value)
        if not isinstance(self.unit, Unit) or type(self.enabled) is not bool:
            raise BillingError("invalid_cost_policy")
        for value in (self.daily_budget, self.weekly_budget, self.monthly_budget, self.unknown_budget):
            integer(value)
        for value in (self.max_inflight, self.max_unknown):
            integer(value, positive=True, maximum=_MAX_RECORDS)
        if self.traffic is not None and (not isinstance(self.traffic, TrafficLimits)
                                         or self.traffic.payer_inflight > self.max_inflight):
            raise BillingError("invalid_cost_policy")


def _windows(now: int) -> dict[str, int]:
    """Fixed UTC periods; epoch day zero is Thursday, weeks start on Monday."""
    try:
        date = _EPOCH + timedelta(seconds=now)
    except OverflowError:
        raise BillingError("invalid_cost_time") from None
    day = now // 86400 * 86400
    month = (date.replace(day=1, hour=0, minute=0, second=0) - _EPOCH).days * 86400
    return {"daily": day, "weekly": day - ((now // 86400 + 3) % 7) * 86400, "monthly": month}


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

    def _counters(self, t, policy: CostPolicy, now: int, *, adding: int = 0) -> dict:
        windows = _windows(now)
        unit = t.execute("SELECT scale FROM billing_units WHERE code=?", (policy.unit.code,)).fetchone()
        if unit is not None and unit[0] != policy.unit.scale:
            raise BillingError("cost_unit_mismatch")
        # Do not scope by namespace, policy revision, payer, key, wallet status, or unit.
        # A differing unit is a conflict, never permission to hide existing exposure.
        # Two disjoint indexed ranges: recent admissions and older pending ones. A join
        # predicate 'usage IS NULL' over all admissions would scan all settled history.
        # Bound candidate lookup itself before joining immutable cost facts.
        candidates = (
            "SELECT attempt_key FROM model_cost_admissions INDEXED BY model_cost_admissions_scope"
            " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at>=?"
            " UNION ALL SELECT attempt_key FROM model_cost_pending INDEXED BY model_cost_pending_scope"
            " WHERE environment=? AND provider=? AND account_ref=? AND admitted_at<? LIMIT ?"
        )
        scope = (self.ledger.environment, policy.provider, policy.account_ref, min(windows.values()))
        args = (*scope, *scope, _MAX_RECORDS + 1)
        source = (" FROM (" + candidates + ") selected"
                  " JOIN model_cost_admissions a ON a.attempt_key=selected.attempt_key"
                  " LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key")
        # A capped cardinality probe detects overflow BEFORE materializing/summing facts.
        # At most 10,000 records enter Python; the extra SQL count is only an overflow sentinel.
        # Both branches use scope indexes; historical unknowns come from the pending
        # projection, so settled history is not scanned for this count.
        count = t.execute("SELECT COUNT(*) FROM (" + candidates + ")", args).fetchone()[0]
        if count + adding > _MAX_RECORDS:
            raise BillingError("cost_accounting_capacity")
        totals = {name: Fraction(0) for name in windows}
        unknown = unknown_count = inflight = 0
        rows = t.execute(
            "SELECT a.unit,a.reserved_units,a.admitted_at,a.expires_at,"
            "u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit" + source + " LIMIT ?",
            (*args, _MAX_RECORDS),
        )
        for row in rows:
            if row["unit"] != policy.unit.code:
                raise BillingError("cost_unit_mismatch")
            if row["admitted_at"] > now:
                raise BillingError("cost_clock_regressed")
            if row["usage_key"] is None:
                cost = integer(row["reserved_units"])
                unknown += cost
                unknown_count += 1
                inflight += int(row["expires_at"] > now)
            else:
                if row["cost_unit"] != policy.unit.code:
                    raise BillingError("cost_unit_mismatch")
                try:
                    numerator, denominator = int(row["cost_n"]), int(row["cost_d"])
                    if numerator < 0 or denominator <= 0:
                        raise ValueError
                    cost = Fraction(numerator, denominator)
                except (ValueError, TypeError):
                    raise BillingError("invalid_cost_fact") from None
            for name, start in windows.items():
                if row["admitted_at"] >= start:
                    totals[name] += cost
        return {"windows": windows, "totals": totals, "unknown_reserved_units": unknown,
                "unknown_count": unknown_count, "inflight": inflight, "records": count}

    def admit(self, transaction, ticket: AttemptTicket, *, amount: int, expires_at: int) -> None:
        """Reserve a conservative cost bound atomically with Ledger's one-shot send fence.

        Expiry only releases the concurrency slot. Unknown count/amount survive indefinitely
        until trusted usage exists, including after wallet release. Actual costs replace the
        bound without capping, even for late/platform_fault usage, in the ORIGINAL UTC periods.
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
        if expires_at <= now:
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
        if t.execute("SELECT 1 FROM model_cost_admissions WHERE attempt_key=?", (ticket.attempt_key,)).fetchone():
            raise BillingError("cost_attempt_already_admitted")
        counters = self._counters(t, policy, now, adding=1)
        for period, total in counters["totals"].items():
            if total + amount > getattr(policy, period + "_budget"):
                raise BillingError("cost_" + period + "_budget_exceeded")
        if counters["unknown_reserved_units"] + amount > policy.unknown_budget:
            raise BillingError("cost_unknown_budget_exceeded")
        if counters["unknown_count"] + 1 > policy.max_unknown:
            raise BillingError("cost_unknown_limit")
        if counters["inflight"] + 1 > policy.max_inflight:
            raise BillingError("cost_inflight_limit")
        # No writes precede all checks. Store/schema own persistence and immutability.
        t.execute(
            "INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,"
            "reserved_units,policy_revision,limits_json,admitted_at,expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ticket.attempt_key, ledger.environment, policy.provider, policy.account_ref, policy.unit.code,
             amount, policy.revision, json.dumps(asdict(policy), sort_keys=True, separators=(",", ":")), now, expires_at),
        )

    def snapshot(self) -> dict:
        """Internal, consistent counters; neither a public projection nor send permission."""
        with self.ledger.store.tx() as t:
            policy, now = self._policy(), self.ledger._now()
            counters = self._counters(t, policy, now)
            totals = counters.pop("totals")
            return {"environment": self.ledger.environment, "provider": policy.provider,
                    "account_ref": policy.account_ref, "policy_revision": policy.revision,
                    "unit": asdict(policy.unit), "as_of": now, **counters,
                    **{name: {"numerator": value.numerator, "denominator": value.denominator}
                       for name, value in totals.items()}}
