"""The host's wallet configuration, read once: environment and units. Invents nothing.

``SYNWEVO_BILLING_ENVIRONMENT`` is ``test`` or ``live``; ``SYNWEVO_BILLING_WALLET_UNITS`` is a
JSON array of 1..16 ``{code, scale}`` objects. The owner decided the wallet currency is CNY
(agent_deepseek_prompt §6.1): money paid in yuan and fen lands in the unit whose code is
``CNY``, which must have at least the two decimal places of a fen (``scale >= 2``). Amounts in
the ledger are integers of ``10**-scale`` yuan; a payment's ``amount_minor`` is fen.

Missing or invalid configuration means "not configured" (``None`` / ``()``), never a default.
"""
from __future__ import annotations

import json
import os

from .models import BillingError, Unit

CURRENCY = "CNY"
CURRENCY_SCALE = 2  # fen


def environment_from_env() -> str | None:
    value = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT")
    return value if value in ("test", "live") else None


def units_from_env() -> tuple[Unit, ...]:
    raw = os.environ.get("SYNWEVO_BILLING_WALLET_UNITS")
    if not raw or len(raw) > 16384:
        return ()

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_field")
            result[key] = value
        return result

    try:
        records = json.loads(raw, object_pairs_hook=unique_fields)
        if not isinstance(records, list) or not 1 <= len(records) <= 16:
            return ()
        if any(not isinstance(record, dict) or set(record) != {"code", "scale"} for record in records):
            return ()
        units = tuple(Unit(record["code"], record["scale"]) for record in records)
        if len({unit.code for unit in units}) != len(units):
            return ()
        return units
    except (ValueError, TypeError, RecursionError):
        # Never echo or log raw host configuration, including parser exception messages.
        return ()


def cny_unit(units) -> Unit | None:
    """The CNY wallet unit, when configured with at least fen precision."""
    for unit in units or ():
        if isinstance(unit, Unit) and unit.code == CURRENCY and unit.scale >= CURRENCY_SCALE:
            return unit
    return None


def fen_to_units(amount_minor: int, unit: Unit) -> int:
    """Exact: one fen is ``10**(scale-2)`` wallet units. No rounding, no exchange rate."""
    if type(amount_minor) is not int or amount_minor < 0 or unit.code != CURRENCY or unit.scale < CURRENCY_SCALE:
        raise BillingError("invalid_amount")
    return amount_minor * 10 ** (unit.scale - CURRENCY_SCALE)


def units_to_fen(units: int, unit: Unit) -> int:
    """Floor to whole fen (for display limits only; never used to move money)."""
    if type(units) is not int or unit.code != CURRENCY or unit.scale < CURRENCY_SCALE:
        raise BillingError("invalid_amount")
    return units // 10 ** (unit.scale - CURRENCY_SCALE)


#: The fixed identity conversion recorded on every CNY order (credit_units = fen × 10^(scale-2)).
EXCHANGE_VERSION = "cny-fen-identity-v1"
