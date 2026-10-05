"""The one-time welcome allowance (owner decision §6.1: CNY 1 per account), off by default.

Granted at the person's first Agent use, through ``Ledger.grant(kind='welcome')``, and only when
every abuse limit below holds. Each limit is a property of the data, not of a caller's care:

* **Configured.** ``SYNWEVO_BILLING_WELCOME_MINOR`` (fen, 1..10000) together with a configured
  CNY wallet unit and billing environment. Unset means no allowance and nothing is said about one.
* **A verified address.** ``users.email_verified_at`` is set; pending, suspended, deleted and
  application accounts get nothing.
* **Once per account** (``grant_id = welcome:<user id>``, idempotent) **and once per address**:
  the grant carries ``abuse_key`` = SHA-256 of the normalized verified address (lower case, a
  ``+tag`` in the local part removed), unique per environment, so deleting the account and
  registering again with the same mailbox does not pay twice. The database's unique indexes
  enforce both even under concurrent first uses.
* **A platform-wide daily ceiling**: ``SYNWEVO_BILLING_WELCOME_DAILY_CAP`` grants per rolling
  24 hours (default 200). A burst of sign-ups beyond it gets the allowance refused with the
  honest reason ``capacity``, not a silent zero.
* Registration itself is already behind Turnstile when configured (synwevo_core.challenge).
  Device and payment-fingerprint limits from the brief are not implemented: the platform holds
  no device fingerprint, and the allowance precedes any payment (docs/personal-wallet.md).
"""
from __future__ import annotations

import hashlib
import os

from .ledger import Ledger
from .models import BillingError, Unit
from .wallet_config import cny_unit, fen_to_units

REASON = "welcome allowance (one per account)"
ACTOR = "system:welcome"
DEFAULT_DAILY_CAP = 200


def normalized_address(email: str) -> str:
    local, _, domain = (email or "").strip().lower().partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def abuse_key(email: str) -> str:
    return hashlib.sha256(("synwevo/welcome-allowance/v1\0" + normalized_address(email)).encode()).hexdigest()


def _int_env(name: str, *, low: int, high: int) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw or not raw.isascii() or not raw.isdecimal() or (len(raw) > 1 and raw[0] == "0"):
        return None
    value = int(raw)
    return value if low <= value <= high else None


class WelcomeAllowance:
    def __init__(self, ledger: Ledger, *, unit: Unit, amount_minor: int, daily_cap: int = DEFAULT_DAILY_CAP):
        self.ledger, self.unit = ledger, unit
        self.amount = fen_to_units(amount_minor, unit)
        self.amount_minor = amount_minor
        self.daily_cap = daily_cap

    @classmethod
    def from_host(cls, ledger: Ledger | None, units) -> "WelcomeAllowance | None":
        amount = _int_env("SYNWEVO_BILLING_WELCOME_MINOR", low=1, high=10000)
        unit = cny_unit(units)
        if ledger is None or amount is None or unit is None:
            return None
        cap = _int_env("SYNWEVO_BILLING_WELCOME_DAILY_CAP", low=0, high=100000)
        return cls(ledger, unit=unit, amount_minor=amount, daily_cap=DEFAULT_DAILY_CAP if cap is None else cap)

    def ensure(self, user_id: str) -> dict:
        """Grant the allowance if this account is eligible and has not had it. Never raises for
        an ineligible account; returns ``{"state": granted|already|email_unverified|ineligible|capacity}``."""
        store = self.ledger.store
        with store.conn() as connection:
            if connection.in_transaction:
                # The ledger owns its transaction; a caller inside one (e.g. a consent run)
                # already used the Agent before, so nothing is lost by not granting here.
                return {"state": "deferred"}
        user = store.one("SELECT id,email,status,deleted_at,email_verified_at FROM users WHERE id=?", (user_id,))
        if not user or user["status"] != "active" or user["deleted_at"] is not None:
            return {"state": "ineligible"}
        try:
            if store.one("SELECT 1 AS x FROM app_accounts WHERE user_id=?", (user_id,)):
                return {"state": "ineligible"}
        except Exception:  # noqa: BLE001 - a store without the application-account table has none
            pass
        if not user["email_verified_at"] or not user["email"]:
            return {"state": "email_unverified"}
        key = abuse_key(user["email"])
        if self.ledger.has_welcome(payer_id=user_id, abuse_key=key):
            return {"state": "already"}
        try:
            row = self.ledger.grant(grant_id="welcome:" + user_id, kind="welcome", payer_kind="user",
                                    payer_id=user_id, unit=self.unit, amount=self.amount, reason=REASON,
                                    actor_id=ACTOR, abuse_key=key, welcome_daily_cap=self.daily_cap)
        except BillingError as exc:
            if exc.code == "welcome_capacity":
                return {"state": "capacity"}
            if exc.code in ("welcome_already_granted", "idempotency_conflict"):
                return {"state": "already"}
            raise
        except Exception as exc:  # a concurrent first use won the unique index
            if "UNIQUE" in str(exc) or "immutable_billing_fact" in str(exc):
                return {"state": "already"}
            raise
        if not row["created"]:
            return {"state": "already"}
        return {"state": "granted", "grant_id": row["id"], "amount_minor": self.amount_minor}
