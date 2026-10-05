"""Per-person low-balance reminders: a threshold the person sets, and one notice per crossing.

The setting is the person's own (``billing_wallet_reminders``, one row per environment, user and
wallet unit, optimistic ``revision``). It never changes money, never blocks a call and grants
nothing; spending limits that do block calls are ``PayerBudgets``.

``sweep`` compares each enabled reminder with the wallet's current available amount (posted
balance minus live holds, the ledger's own definition) and calls ``notify`` once when the amount
is below the threshold; ``notified_at`` then holds the reminder until the amount is back at or
above the threshold, which re-arms it. The claim and the notice commit in one transaction, so a
crash cannot send two notices for one crossing or mark one sent that was not recorded.
"""
from __future__ import annotations

import time

from ..store import Store
from .models import BillingError, identifier, integer

MAX_THRESHOLD = 10**15


def plain_amount(units: int, scale: int) -> str:
    """Integer smallest units as a short decimal ("12.5", not "12.500000"); no float on the way."""
    sign = "-" if units < 0 else ""
    digits = str(abs(units)).rjust(scale + 1, "0")
    whole, fraction = (digits[:-scale], digits[-scale:].rstrip("0")) if scale else (digits, "")
    return sign + whole + ("." + fraction if fraction else "")


def notice(notifier):
    """The ``notify`` for ``sweep``: one in-app notice (and mail, per the person's preferences)
    through the shared notification catalogue, joined to the claiming transaction — the claim
    (``notified_at``) and the notice commit together, so no dedupe key is needed (one keyed on
    the claim time would merge two crossings within the same second)."""
    def send(t, row, available):
        notifier.emit(row["user_id"], "wallet.low_balance",
                      params={"available": plain_amount(available, row["scale"]),
                              "threshold": plain_amount(row["threshold"], row["scale"]), "unit": row["unit"]},
                      object_kind="wallet", object_id=row["user_id"],
                      target={"origin": "accounts", "path": "/account/wallet"})
    return send


class LowBalanceReminders:
    def __init__(self, store: Store, *, environment: str, clock=None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _available(self, t, user_id: str, unit: str, now: int) -> int:
        account = t.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? "
                            "AND kind='wallet'", (self.environment, unit, user_id)).fetchone()
        if not account:
            return 0
        booked = t.execute("SELECT amount FROM billing_balances WHERE account_id=?", (account[0],)).fetchone()
        held = t.execute("SELECT COALESCE(SUM(hold_units),0) FROM billing_calls WHERE account_id=? "
                         "AND status='reserved' AND deadline>?", (account[0], now)).fetchone()[0]
        return (booked[0] if booked else 0) - held

    def _view(self, t, user_id: str, unit) -> dict:
        row = t.execute("SELECT * FROM billing_wallet_reminders WHERE environment=? AND user_id=? AND unit=?",
                        (self.environment, user_id, unit.code)).fetchone()
        available = self._available(t, user_id, unit.code, self.clock())
        enabled = bool(row["enabled"]) if row else False
        threshold = row["threshold"] if row else 0
        return {"unit": unit.code, "scale": unit.scale, "configured": row is not None,
                "revision": row["revision"] if row else 0, "enabled": enabled, "threshold": str(threshold),
                "available": str(available), "below": enabled and available < threshold,
                "updated_at": row["updated_at"] if row else None}

    def _active(self, t, user_id: str) -> None:
        identifier(user_id)
        if not t.execute("SELECT 1 FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                         (user_id,)).fetchone():
            raise BillingError("account_unavailable")

    @staticmethod
    def _known(t, unit) -> None:
        row = t.execute("SELECT scale FROM billing_units WHERE code=?", (unit.code,)).fetchone()
        if not row or row[0] != unit.scale:
            raise BillingError("unit_scale_conflict")

    def read(self, user_id: str, units) -> list[dict]:
        with self.store.tx() as t:
            self._active(t, user_id)
            out = []
            for unit in units:
                self._known(t, unit)
                out.append(self._view(t, user_id, unit))
            return out

    def set(self, user_id: str, unit, *, expected_revision: int, enabled: bool, threshold: int) -> dict:
        """Caller owns authorization and audit; joins the caller's Store transaction if any."""
        integer(expected_revision)
        integer(threshold, maximum=MAX_THRESHOLD)
        if type(enabled) is not bool:
            raise BillingError("invalid_reminder")
        with self.store.tx() as t:
            self._active(t, user_id)
            self._known(t, unit)
            row = t.execute("SELECT revision FROM billing_wallet_reminders WHERE environment=? AND user_id=? "
                            "AND unit=?", (self.environment, user_id, unit.code)).fetchone()
            if (row[0] if row else 0) != expected_revision:
                raise BillingError("reminder_conflict")
            now = self.clock()
            # A changed threshold re-arms the reminder; the next sweep decides afresh.
            t.execute("INSERT INTO billing_wallet_reminders(environment,user_id,unit,revision,enabled,threshold,"
                      "updated_at,notified_at) VALUES (?,?,?,?,?,?,?,NULL) ON CONFLICT(environment,user_id,unit) "
                      "DO UPDATE SET revision=excluded.revision,enabled=excluded.enabled,threshold=excluded.threshold,"
                      "updated_at=excluded.updated_at,notified_at=NULL",
                      (self.environment, user_id, unit.code, expected_revision + 1, int(enabled), threshold, now))
            return self._view(t, user_id, unit)

    def sweep(self, notify, *, batch: int = 500, limit: int = 50_000) -> dict:
        """Visit every enabled reminder once, in key order, ``batch`` rows per read.

        notify(transaction, row, available) records the notice inside the claiming transaction.
        Keyset paging (not a fixed ``LIMIT`` from the start) so reminder number 501 is checked too."""
        integer(batch, positive=True, maximum=5000)
        integer(limit, positive=True, maximum=1_000_000)
        now = self.clock()
        sent = rearmed = checked = 0
        after = ("", "")
        while checked < limit:
            rows = self.store.all(
                "SELECT r.user_id,r.unit,u.scale FROM billing_wallet_reminders r "
                "JOIN billing_units u ON u.code=r.unit JOIN users p ON p.id=r.user_id "
                "WHERE r.environment=? AND r.enabled=1 AND p.status='active' AND p.deleted_at IS NULL "
                "AND (r.user_id,r.unit)>(?,?) ORDER BY r.user_id,r.unit LIMIT ?",
                (self.environment, *after, min(batch, limit - checked)))
            for row in rows:
                checked += 1
                with self.store.tx() as t:
                    current = t.execute("SELECT threshold,enabled,notified_at FROM billing_wallet_reminders "
                                        "WHERE environment=? AND user_id=? AND unit=?",
                                        (self.environment, row["user_id"], row["unit"])).fetchone()
                    if not current or not current["enabled"]:
                        continue
                    available = self._available(t, row["user_id"], row["unit"], now)
                    if available < current["threshold"] and current["notified_at"] is None:
                        t.execute("UPDATE billing_wallet_reminders SET notified_at=? WHERE environment=? "
                                  "AND user_id=? AND unit=?", (now, self.environment, row["user_id"], row["unit"]))
                        notify(t, {**row, "threshold": current["threshold"], "notified_at": now}, available)
                        sent += 1
                    elif available >= current["threshold"] and current["notified_at"] is not None:
                        t.execute("UPDATE billing_wallet_reminders SET notified_at=NULL WHERE environment=? "
                                  "AND user_id=? AND unit=?", (self.environment, row["user_id"], row["unit"]))
                        rearmed += 1
            if len(rows) < batch or not rows:
                break
            after = (rows[-1]["user_id"], rows[-1]["unit"])
        return {"checked": checked, "sent": sent, "rearmed": rearmed}
