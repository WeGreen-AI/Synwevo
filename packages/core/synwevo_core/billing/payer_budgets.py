"""Payer-wide calendar limits over the existing ledger, not another funds reservation.

Current UTC periods count settled charges by original reservation time plus every still-held
call, including earlier periods. A new namespace, price or policy version cannot reset spend.
All money admission runs under the ledger writer transaction; views create no money facts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import BillingError, identifier, integer


class PayerBudgets:
    CAPACITY = 10_000

    def __init__(self, ledger):
        self.ledger, self.store, self.environment = ledger, ledger.store, ledger.environment

    def _transaction(self, t):
        with self.store.conn() as current:
            if t is not current or not t.in_transaction or not getattr(self.store._local, "depth", 0):
                raise BillingError("payer_budget_requires_transaction")

    def _unit(self, t, code):
        identifier(code)
        row = t.execute("SELECT u.code,u.scale FROM billing_units u WHERE u.code=? AND ("
            "EXISTS(SELECT 1 FROM billing_prices p WHERE p.environment=? "
            "AND json_extract(p.snapshot,'$.purpose')='sale' AND json_extract(p.snapshot,'$.unit.code')=u.code))",
            (code, self.environment)).fetchone()
        if not row:
            raise BillingError("unknown_sale_unit")
        return dict(row)

    def _policy(self, t, user_id, code):
        return t.execute("SELECT * FROM billing_payer_budget_versions WHERE environment=? AND user_id=? "
            "AND unit=? ORDER BY revision DESC LIMIT 1", (self.environment, user_id, code)).fetchone()

    def _windows(self, at):
        today = datetime.fromtimestamp(at, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        week = today - timedelta(days=today.weekday())
        month = today.replace(day=1)
        following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        return {name: (int(start.timestamp()), int(end.timestamp())) for name, start, end in (
            ("daily", today, today + timedelta(days=1)), ("weekly", week, week + timedelta(days=7)),
            ("monthly", month, following))}

    def _exposure(self, t, account, windows):
        if account is None:
            return {name: {"start": start, "end": end, "spent": 0, "held": 0}
                    for name, (start, end) in windows.items()}
        # Disjoint indexed ranges, bounded before the settlement join. No all-history scan.
        rows = t.execute("WITH candidates AS MATERIALIZED ("
            "SELECT id,status,hold_units,created_at FROM billing_calls WHERE account_id=? AND status='reserved' "
            "UNION ALL SELECT id,status,hold_units,created_at FROM billing_calls "
            "WHERE account_id=? AND status='settled' AND created_at>=? LIMIT ?) "
            "SELECT c.*,j.amount FROM candidates c LEFT JOIN billing_settlements s ON s.call_key=c.id "
            "LEFT JOIN billing_journal j ON j.id=s.journal_id",
            (account, account, min(start for start, _ in windows.values()), self.CAPACITY + 1)).fetchall()
        if len(rows) > self.CAPACITY:
            raise BillingError("payer_budget_capacity")
        if any(row["status"] == "settled" and row["amount"] is None for row in rows):
            raise BillingError("payer_budget_inconsistent")
        held = sum(row["hold_units"] for row in rows if row["status"] == "reserved")
        return {name: {"start": start, "end": end, "held": held,
                "spent": sum(row["amount"] for row in rows if row["status"] == "settled" and row["created_at"] >= start)}
                for name, (start, end) in windows.items()}

    def _view(self, t, user_id, code):
        unit = self._unit(t, code)
        policy = self._policy(t, user_id, code)
        account = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind='wallet'",
                            (self.environment, code, user_id)).fetchone()
        usage_state = "available"
        try:
            periods = self._exposure(t, account[0] if account else None, self._windows(self.ledger._now()))
        except BillingError as exc:
            if exc.code not in ("payer_budget_capacity", "payer_budget_inconsistent"):
                raise
            usage_state = exc.code.removeprefix("payer_budget_")
            periods = None
        limits = {name + "_budget": policy[name + "_budget"] if policy else 0 for name in ("daily", "weekly", "monthly")}
        for name, value in (periods or {}).items():
            value["remaining"] = str(max(0, limits[name + "_budget"] - value["spent"] - value["held"]))
            for field in ("spent", "held"):
                value[field] = str(value[field])
        history = t.execute("SELECT revision,enabled,daily_budget,weekly_budget,monthly_budget,created_at "
            "FROM billing_payer_budget_versions WHERE environment=? AND user_id=? AND unit=? ORDER BY revision DESC LIMIT 20",
            (self.environment, user_id, code)).fetchall()
        return {"unit": unit, "revision": policy["revision"] if policy else 0, "configured": policy is not None,
            "enabled": bool(policy["enabled"]) if policy else False,
            **{name: str(value) for name, value in limits.items()}, "periods": periods, "usage_state": usage_state,
            "history": [{**dict(row), "enabled": bool(row["enabled"]),
                         **{name: str(row[name]) for name in limits}} for row in history]}

    def read(self, user_id):
        with self.store.tx() as t:
            self.ledger._active_user(t, user_id)
            units = t.execute("SELECT DISTINCT json_extract(snapshot,'$.unit.code') AS code FROM billing_prices "
                "WHERE environment=? AND json_extract(snapshot,'$.purpose')='sale' ORDER BY code LIMIT 101",
                (self.environment,)).fetchall()
            if len(units) > 100:
                raise BillingError("payer_budget_capacity")
            return {"environment": self.environment, "timezone": "UTC",
                    "budgets": [self._view(t, user_id, row["code"]) for row in units]}

    def set(self, user_id, *, unit_code, expected_revision, enabled, daily_budget, weekly_budget, monthly_budget):
        """Caller owns authorization/audit/idempotency and the encompassing Store transaction."""
        integer(expected_revision)
        if type(enabled) is not bool:
            raise BillingError("invalid_payer_budget")
        for amount in (daily_budget, weekly_budget, monthly_budget):
            integer(amount)
        with self.store.conn() as t:
            self._transaction(t)
            self.ledger._active_user(t, user_id)
            self._unit(t, unit_code)
            old = self._policy(t, user_id, unit_code)
            if (old["revision"] if old else 0) != expected_revision:
                raise BillingError("payer_budget_conflict")
            t.execute("INSERT INTO billing_payer_budget_versions VALUES (?,?,?,?,?,?,?,?,?)",
                (self.environment, user_id, unit_code, expected_revision + 1, int(enabled), daily_budget,
                 weekly_budget, monthly_budget, self.ledger._now()))
            return self._view(t, user_id, unit_code)

    def check(self, t, account, *, amount, at):
        self._transaction(t)
        integer(amount)
        integer(at)
        owner = t.execute("SELECT owner,unit FROM billing_accounts WHERE id=? AND environment=? AND kind='wallet'",
                          (account, self.environment)).fetchone()
        if not owner:
            raise BillingError("payer_budget_inconsistent")
        policy = self._policy(t, owner["owner"], owner["unit"])
        if policy is None:
            raise BillingError("payer_budget_not_configured")
        if not policy["enabled"]:
            raise BillingError("payer_budget_disabled")
        periods = self._exposure(t, account, self._windows(at))
        for name, value in periods.items():
            if value["spent"] + value["held"] + amount > policy[name + "_budget"]:
                raise BillingError("payer_" + name + "_budget_exceeded")
        return policy

    def record(self, t, call, policy):
        self._transaction(t)
        t.execute("INSERT INTO billing_payer_budget_admissions VALUES (?,?,?,?,?)",
                  (call, self.environment, policy["user_id"], policy["unit"], policy["revision"]))
