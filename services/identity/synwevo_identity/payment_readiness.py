"""Read Stripe's default payment-method configuration without creating a payment.

`available` combines capability and display preference. Our Checkout uses explicit method
types, so this is a diagnostic, not a substitute for Checkout validation or an eligibility
gate. No account identity, provider body, credential or upstream message leaves this adapter.
"""
from __future__ import annotations

import json
import re
import threading
import time

import httpx

from synwevo_core.billing.payment_settings import PAYMENT_METHODS
from synwevo_core.errors import ServiceError

STRIPE_API = "https://api.stripe.com"
MAX_RESPONSE_BYTES = 512 * 1024
MAX_PAGES = 5


def unavailable():
    return ServiceError(503, "payment_readiness_unavailable", "暂时无法检查 Stripe 支付渠道，请稍后重试；这不会更改支付设置")


class StripeReadiness:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = 5):
        self._transport, self._timeout = transport, timeout
        # A manual administrator diagnostic must not consume every account-service worker.
        self._slots = threading.BoundedSemaphore(2)

    @staticmethod
    def _get(client, path, params=None):
        with client.stream("GET", path, params=params) as response:
            if response.status_code != 200:
                raise unavailable()
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise unavailable()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise unavailable() from None
        if not isinstance(body, dict):
            raise unavailable()
        return body

    def inspect(self, secret_key: str, *, environment: str) -> dict:
        if not self._slots.acquire(blocking=False):
            raise ServiceError(503, "payment_readiness_busy", "支付渠道检查正在进行，请稍后重试")
        try:
            return self._inspect(secret_key, environment=environment)
        except httpx.HTTPError:
            raise unavailable() from None
        finally:
            self._slots.release()

    def _inspect(self, secret_key: str, *, environment: str) -> dict:
        with httpx.Client(base_url=STRIPE_API, transport=self._transport, timeout=self._timeout,
                          follow_redirects=False, trust_env=False,
                          headers={"Authorization": "Bearer " + secret_key}) as client:
            account = self._get(client, "/v1/account")
            if account.get("object") != "account" or type(account.get("charges_enabled")) is not bool:
                raise unavailable()
            charges_enabled = account["charges_enabled"]
            # Drop the account object before reading configurations: only this flag is needed.
            del account
            defaults, seen = [], set()
            params = {"limit": 100}
            for _ in range(MAX_PAGES):
                page = self._get(client, "/v1/payment_method_configurations", params)
                rows = page.get("data")
                if (page.get("object") != "list" or not isinstance(rows, list) or len(rows) > 100
                        or type(page.get("has_more")) is not bool):
                    raise unavailable()
                for row in rows:
                    if not isinstance(row, dict) or row.get("object") != "payment_method_configuration":
                        raise unavailable()
                    # Connect child/application configurations are not this direct merchant's default.
                    if row.get("is_default") is True and not row.get("parent") and not row.get("application"):
                        if type(row.get("livemode")) is not bool or row["livemode"] != (environment == "live"):
                            raise unavailable()
                        defaults.append(row)
                if not page["has_more"]:
                    break
                last = rows[-1].get("id") if rows else None
                if not isinstance(last, str) or not re.fullmatch(r"pmc_[A-Za-z0-9]{1,200}", last) or last in seen:
                    raise unavailable()
                seen.add(last)
                params = {"limit": 100, "starting_after": last}
            else:
                return self._result(charges_enabled, "incomplete")
        if not defaults:
            return self._result(charges_enabled, "missing")
        if len(defaults) != 1:
            return self._result(charges_enabled, "ambiguous")
        config = defaults[0]
        if type(config.get("active")) is not bool:
            raise unavailable()
        if not config["active"]:
            return self._result(charges_enabled, "inactive")
        methods = {}
        for method in PAYMENT_METHODS:
            value = config.get(method)
            available = value.get("available") if isinstance(value, dict) else None
            methods[method] = available if type(available) is bool else None
        return self._result(charges_enabled, "available", methods)

    @staticmethod
    def _result(charges_enabled, status, methods=None):
        return {"provider": "stripe", "checked_at": int(time.time()), "charges_enabled": charges_enabled,
                "configuration_status": status,
                "methods": methods if methods is not None else {method: None for method in PAYMENT_METHODS}}
