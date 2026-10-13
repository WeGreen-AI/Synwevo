"""Read Stripe's account capabilities and default payment-method configuration without creating a payment.

Stripe defines a configuration's ``available`` as "display preference on AND the method's capability
active". Our Checkout (pinned pre-endive API version, payments.STRIPE_API_VERSION) lists explicit
``payment_method_types``, which do not consult the display preference: ``available=true`` is no proof
that Checkout will accept a method, and ``available=false`` alone does not mean the method is not
activated — the Dashboard display may simply be off (显示关闭不必然表示渠道没有获批). So each method's
``activated`` combines the evidence:

* ``True`` — the account's capability for it is ``active``, or the default configuration reports it
  available (which implies an active capability);
* ``False`` — definitely not activated: the capability is reported in another state (inactive,
  pending), or the account's capability list omits it and the default configuration reports it
  unavailable, or (capabilities not reported) the configuration reports it unavailable with its
  display preference on;
* ``None`` — no evidence either way, or the two sources contradict each other.

The publish route (admin_payments.py) refuses an enabled revision that selects a ``False`` method
and asks for an explicit acknowledgement when a selected method is ``None``.

A key Stripe rejects (HTTP 401 with its error object) is a definite answer, not an outage:
``payment_credentials_rejected``. A key Stripe forbids to read the account (403 with its error object,
e.g. a restricted key) is ``payment_credentials_forbidden``. Transport failures, timeouts, other HTTP
answers, malformed bodies and a busy checker are ``payment_readiness_unavailable`` /
``payment_readiness_busy``. No account identity, provider body, credential or upstream message
leaves this adapter. Every request pins ``Stripe-Version`` (payments.STRIPE_API_VERSION); the result
reports the version Stripe says it applied (``api_version``, from its response header).
"""
from __future__ import annotations

import json
import re
import threading
import time

import httpx

from synwevo_core.billing.payment_settings import PAYMENT_METHODS
from synwevo_core.errors import ServiceError

from .payments import STRIPE_API, STRIPE_API_VERSION

MAX_RESPONSE_BYTES = 512 * 1024
MAX_PAGES = 5
_API_VERSION = re.compile(r"\d{4}-\d{2}-\d{2}(\.[a-z]{1,20})?")
#: The account capability that activates each payment method at Stripe.
CAPABILITIES = {"card": "card_payments", "wechat_pay": "wechat_pay_payments", "alipay": "alipay_payments"}
_STATE = re.compile(r"[a-z_]{1,40}")


def unavailable():
    return ServiceError(503, "payment_readiness_unavailable", "暂时无法检查 Stripe 支付渠道，请稍后重试；这不会更改支付设置")


def rejected():
    return ServiceError(409, "payment_credentials_rejected",
                        "Stripe 拒绝了已保存的密钥（密钥无效、已撤销或已过期）。请在 Stripe 核对后，通过“检查并发布”设置新的密钥")


def forbidden():
    return ServiceError(409, "payment_credentials_forbidden",
                        "Stripe 不允许已保存的密钥读取账户信息（可能是权限受限的密钥），因此无法检查支付渠道")


def _activated(state, available, display):
    """See the module docstring. ``state``: the capability's state, "unrequested" when the account's
    capability list omits it, None when the account reported no capability list."""
    positive = state == "active" or available is True
    negative = ((state is not None and state not in ("active", "unrequested"))
                or (state == "unrequested" and available is False)
                or (state is None and available is False and display == "on"))
    if positive == negative:
        return None
    return positive


class StripeReadiness:
    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout: float = 5):
        self._transport, self._timeout = transport, timeout
        # A manual administrator diagnostic must not consume every account-service worker.
        self._slots = threading.BoundedSemaphore(2)

    @staticmethod
    def _get(client, path, params=None):
        """(body, the API version Stripe applied or None)."""
        with client.stream("GET", path, params=params) as response:
            status = response.status_code
            if status not in (200, 401, 403):
                raise unavailable()
            applied = response.headers.get("stripe-version")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise unavailable()
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            # RecursionError: a body nested too deeply to decode is malformed, not a crash.
            raise unavailable() from None
        if status != 200:
            # Only Stripe's documented error object makes a 401/403 a definite answer about the
            # key; a proxy page or an empty body proves nothing. Its message is never read.
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict) and isinstance(error.get("type"), str):
                raise rejected() if status == 401 else forbidden()
            raise unavailable()
        if not isinstance(body, dict):
            raise unavailable()
        return body, applied if isinstance(applied, str) and _API_VERSION.fullmatch(applied) else None

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
                          headers={"Authorization": "Bearer " + secret_key,
                                   "Stripe-Version": STRIPE_API_VERSION}) as client:
            account, applied = self._get(client, "/v1/account")
            if account.get("object") != "account" or type(account.get("charges_enabled")) is not bool:
                raise unavailable()
            charges_enabled = account["charges_enabled"]
            listed = account.get("capabilities")
            states = {}
            for method, capability in CAPABILITIES.items():
                if not isinstance(listed, dict):
                    states[method] = None
                else:
                    value = listed.get(capability)
                    states[method] = ("unrequested" if value is None
                                      else value if isinstance(value, str) and _STATE.fullmatch(value) else None)
            # Drop the account object before reading configurations: only these flags are needed.
            del account, listed
            defaults, seen = [], set()
            params = {"limit": 100}
            for _ in range(MAX_PAGES):
                page, _ = self._get(client, "/v1/payment_method_configurations", params)
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
                return self._result(charges_enabled, "incomplete", states=states, api_version=applied)
        if not defaults:
            return self._result(charges_enabled, "missing", states=states, api_version=applied)
        if len(defaults) != 1:
            return self._result(charges_enabled, "ambiguous", states=states, api_version=applied)
        config = defaults[0]
        if type(config.get("active")) is not bool:
            raise unavailable()
        if not config["active"]:
            return self._result(charges_enabled, "inactive", states=states, api_version=applied)
        methods, displays = {}, {}
        for method in PAYMENT_METHODS:
            value = config.get(method)
            available = value.get("available") if isinstance(value, dict) else None
            methods[method] = available if type(available) is bool else None
            preference = value.get("display_preference") if isinstance(value, dict) else None
            display = preference.get("value") if isinstance(preference, dict) else None
            displays[method] = display if display in ("on", "off") else None
        return self._result(charges_enabled, "available", methods, states=states, displays=displays,
                            api_version=applied)

    @staticmethod
    def _result(charges_enabled, status, methods=None, *, states, displays=None, api_version=None):
        methods = methods if methods is not None else {method: None for method in PAYMENT_METHODS}
        displays = displays or {}
        return {"provider": "stripe", "checked_at": int(time.time()), "charges_enabled": charges_enabled,
                "configuration_status": status, "methods": methods,
                "activated": {method: _activated(states.get(method), methods.get(method), displays.get(method))
                              for method in PAYMENT_METHODS},
                "api_version": api_version, "api_version_requested": STRIPE_API_VERSION}
