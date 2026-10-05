"""Upstream balance monitor and reconciliation for the platform model-provider account.

docs/platform/model-risk.md. The monitor reads DeepSeek's `GET /user/balance` with the sealed
platform key (ProviderSettings.resolve) from the same governed origin as the chat gateway
(`upstream_base`: official, or a loopback FIXTURE double in the test environment only), keeps
the key inside the request header and records
only status codes, the availability flag and the balance in the cost policy's unit. Below the
hard threshold, or when the provider reports the account unavailable, it closes paid dispatch
through `trip_platform_breaker`; below the warning threshold it alerts.

Daily reconciliation compares the upstream balance change (plus administrator-recorded upstream
top-ups) with the ledger's cost for calls admitted in the same window (a range: calls whose usage
stayed unknown cost between nothing and their conservative bound), and Ledger.audit_balances
runs on the same schedule. Results are stored (amounts and codes only, never conversation
content) and shown in Admin; a difference beyond the configured tolerance alerts.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from fractions import Fraction
import json
import re
import time

import httpx

from ..errors import ServiceError
from ..store import new_id
from .cost_settings import CostSettings
from .ledger import Ledger
from .models import BillingError, identifier, integer
from .risk import _reason, notify_admins, trip_platform_breaker

#: The provider's official API origin. The platform key is only ever sent here, or, in the test
#: billing environment only, to a loopback FIXTURE double (``upstream_base``).
OFFICIAL_ORIGIN = "https://api.deepseek.com"
BALANCE_PATH = "/user/balance"
BALANCE_URL = OFFICIAL_ORIGIN + BALANCE_PATH
_LOOPBACK = ("127.0.0.1", "::1", "localhost")
MAX_BODY = 64 * 1024
_DECIMAL = re.compile(r"^-?(0|[1-9][0-9]{0,14})(\.[0-9]{1,12})?$")
DAY = 86_400
# How far from a window boundary a balance reading may be and still bound that window.
MIN_READING_GAP_S = 900
# Daily reconciliation waits this long after midnight UTC for the boundary reading to exist.
SETTLE_DELAY_S = 600
MAX_LEDGER_ROWS = 100_000


def loopback_origin(value: object) -> bool:
    """An ``http(s)://127.0.0.1|[::1]|localhost:<port>`` origin: no credentials, path or query."""
    try:
        url = httpx.URL(value) if isinstance(value, str) else None
    except Exception:  # noqa: BLE001 - any unparsable value is simply not loopback
        return False
    return bool(url) and url.scheme in ("http", "https") and url.host in _LOOPBACK and url.port is not None \
        and not url.userinfo and url.path in ("", "/") and not url.query and not url.fragment


def upstream_base(environment: str | None, override: str | None) -> str:
    """The upstream origin the platform key may be sent to, for every request that carries it
    (chat, GET /models in the Suite gateway, GET /user/balance here).

    Always the official origin, except that end-to-end tests may point at a labelled FIXTURE
    double (apps/suite/scripts/deepseek_test_double.py) when the billing environment is
    ``test`` AND the target is a loopback host. Anything else is refused at startup, so a
    configuration mistake can never send the platform key to another host.
    """
    if override in (None, "", OFFICIAL_ORIGIN):
        return OFFICIAL_ORIGIN
    if environment != "test":
        raise ValueError("DeepSeek upstream override is refused outside SYNWEVO_BILLING_ENVIRONMENT=test")
    if not loopback_origin(override):
        raise ValueError("DeepSeek upstream override must be a loopback origin: http(s)://127.0.0.1|[::1]|localhost:<port>")
    return override.rstrip("/")


def utc_day(at: int) -> int:
    return at // DAY * DAY


def day_label(start: int) -> str:
    return datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d")


def units_to_decimal(units: int, scale: int) -> str:
    value = Decimal(units).scaleb(-scale)
    return f"{value:.{scale}f}" if scale else str(units)


class UpstreamBalance:
    def __init__(self, store, *, environment: str, transport: httpx.BaseTransport | None = None,
                 clock=None, timeout_s: float = 10.0, base_url: str | None = None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store, self.environment = store, environment
        self.transport = transport  # Trusted Python fixture injection only; never a configurable URL.
        # The gateway's origin rule (official, or a loopback FIXTURE double in test); raises otherwise.
        self.base_url = upstream_base(environment, base_url)
        self.clock = clock or (lambda: int(time.time()))
        self.timeout_s = timeout_s

    def _policy(self):
        try:
            return CostSettings(self.store, environment=self.environment, clock=self.clock).policy()
        except (BillingError, ServiceError):
            return None

    def _record(self, status, code, *, unit=None, is_available=None, total=None) -> dict:
        row = {"id": new_id("rbal"), "environment": self.environment, "observed_at": integer(self.clock()),
               "status": status, "code": code, "is_available": None if is_available is None else int(is_available),
               "unit": unit.code if unit else None, "scale": unit.scale if unit else None, "total_units": total}
        self.store.execute("INSERT INTO model_risk_balance_readings(id,environment,observed_at,status,code,is_available,"
                           "unit,scale,total_units) VALUES (?,?,?,?,?,?,?,?,?)", tuple(row.values()))
        return row

    def _fetch(self, api_key: str):
        """Return (code, data). Exceptions are reduced to codes: their text may echo a header."""
        transport = self.transport if self.transport is not None else httpx.HTTPTransport(retries=0)
        try:
            with httpx.Client(transport=transport, timeout=self.timeout_s, trust_env=False,
                              follow_redirects=False) as client:
                with client.stream("GET", self.base_url + BALANCE_PATH, headers={"Authorization": "Bearer " + api_key,
                                                                 "Accept": "application/json"}) as response:
                    if response.status_code != 200:
                        return f"upstream_http_{response.status_code}", None
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) > MAX_BODY:
                            return "upstream_bad_response", None
        except httpx.TimeoutException:
            return "upstream_timeout", None
        except httpx.HTTPError:
            return "upstream_unreachable", None
        try:
            return "ok", json.loads(body)
        except ValueError:
            return "upstream_bad_response", None

    def poll(self) -> dict:
        from .provider_settings import ProviderSettings
        policy = self._policy()
        if policy is None:
            return self._record("not_configured", "cost_policy_missing")
        try:
            resolved = ProviderSettings(self.store, environment=self.environment, clock=self.clock).resolve()
        except (BillingError, ServiceError):
            return self._record("not_configured", "credential_unavailable", unit=policy.unit)
        if resolved is None:
            return self._record("not_configured", "provider_disabled", unit=policy.unit)
        code, data = self._fetch(resolved.api_key)
        resolved = None  # The key is not kept past the request.
        if code != "ok":
            return self._record("error", code, unit=policy.unit)
        infos = data.get("balance_infos") if isinstance(data, dict) else None
        available = data.get("is_available") if isinstance(data, dict) else None
        if type(available) is not bool or not isinstance(infos, list) or len(infos) > 20:
            return self._record("error", "upstream_bad_response", unit=policy.unit)
        info = next((item for item in infos if isinstance(item, dict) and item.get("currency") == policy.unit.code), None)
        if info is None:
            return self._record("error", "currency_mismatch", unit=policy.unit, is_available=available)
        raw = info.get("total_balance")
        if not isinstance(raw, str) or not _DECIMAL.match(raw):
            return self._record("error", "upstream_bad_response", unit=policy.unit, is_available=available)
        try:
            # Floor: a fraction of the smallest unit never makes the balance look healthier.
            total = int((Decimal(raw).scaleb(policy.unit.scale)).to_integral_value(rounding=ROUND_FLOOR))
        except InvalidOperation:
            return self._record("error", "upstream_bad_response", unit=policy.unit, is_available=available)
        reading = self._record("ok", "ok", unit=policy.unit, is_available=available, total=total)
        self._evaluate(policy, reading)
        return reading

    def _evaluate(self, policy, reading) -> None:
        risk = policy.risk
        if not reading["is_available"]:
            trip_platform_breaker(self.store, environment=self.environment, source="balance",
                                  reason_code="upstream_unavailable")
        elif risk is not None and risk.balance_close is not None and reading["total_units"] < risk.balance_close:
            trip_platform_breaker(self.store, environment=self.environment, source="balance",
                                  reason_code="upstream_balance_below_close")
        elif risk is not None and risk.balance_warn is not None and reading["total_units"] < risk.balance_warn:
            day = day_label(utc_day(reading["observed_at"]))
            notify_admins(self.store, "billing.risk.low_balance",
                          params={"balance": units_to_decimal(reading["total_units"], policy.unit.scale),
                                  "unit": policy.unit.code},
                          dedupe_key=f"risk:low_balance:{self.environment}:{day}", object_id=reading["id"])

    def readings(self, *, limit: int = 20) -> list[dict]:
        integer(limit, positive=True, maximum=100)
        return self.store.all("SELECT * FROM model_risk_balance_readings WHERE environment=? "
                              "ORDER BY observed_at DESC, id DESC LIMIT ?", (self.environment, limit))


class Reconciliation:
    def __init__(self, store, *, environment: str, clock=None):
        if environment not in ("test", "live"):
            raise BillingError("invalid_environment")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _store_result(self, **fields) -> dict:
        row = {"id": new_id("rrec"), "environment": self.environment, "created_at": integer(self.clock()), **fields}
        columns = ",".join(row)
        self.store.execute(f"INSERT INTO model_risk_reconciliations({columns}) VALUES ({','.join('?' * len(row))})",
                           tuple(row.values()))
        return self.store.one("SELECT * FROM model_risk_reconciliations WHERE id=?", (row["id"],))

    def _reading_near(self, unit, at: int, gap: int):
        return self.store.one("SELECT * FROM model_risk_balance_readings WHERE environment=? AND status='ok' AND unit=? "
                              "AND observed_at BETWEEN ? AND ? ORDER BY ABS(observed_at-?), observed_at LIMIT 1",
                              (self.environment, unit.code, at - gap, at + gap, at))

    def daily(self, day_start: int, *, trigger: str = "schedule", actor_id: str | None = None) -> dict:
        integer(day_start)
        if day_start % DAY or trigger not in ("schedule", "admin"):
            raise BillingError("invalid_reconciliation_window")
        base = {"kind": "daily", "window_start": day_start, "window_end": day_start + DAY, "trigger": trigger,
                "actor_id": actor_id}
        if day_start + DAY > self.clock():
            raise ServiceError(409, "risk_reconcile_window_open", "只能对已经结束的 UTC 日进行对账")
        try:
            policy = CostSettings(self.store, environment=self.environment, clock=self.clock).policy()
        except (BillingError, ServiceError):
            return self._finish(self._store_result(**base, status="incomplete", code="cost_policy_missing"))
        unit, risk = policy.unit, policy.risk
        base.update(unit=unit.code, scale=unit.scale)
        if risk is None:
            return self._finish(self._store_result(**base, status="incomplete", code="risk_not_configured"))
        base["tolerance_units"] = risk.reconcile_tolerance
        gap = max(MIN_READING_GAP_S, 2 * risk.balance_poll_s)
        start = self._reading_near(unit, day_start, gap)
        end = self._reading_near(unit, day_start + DAY, gap)
        if not start or not end or start["observed_at"] >= end["observed_at"]:
            return self._finish(self._store_result(**base, status="incomplete", code="balance_reading_missing"))
        a, b = start["observed_at"], end["observed_at"]
        base.update(reading_start_at=a, reading_end_at=b)
        topups = self.store.one("SELECT COALESCE(SUM(amount_units),0) AS n FROM model_risk_upstream_topups "
                                "WHERE environment=? AND unit=? AND topped_up_at>? AND topped_up_at<=?",
                                (self.environment, unit.code, a, b))["n"]
        rows = self.store.all(
            "SELECT a.reserved_units,a.unit,u.attempt_key AS usage_key,u.cost_n,u.cost_d,u.cost_unit "
            "FROM model_cost_admissions a INDEXED BY model_cost_admissions_scope "
            "LEFT JOIN billing_usage u ON u.attempt_key=a.attempt_key "
            "WHERE a.environment=? AND a.provider=? AND a.account_ref=? AND a.admitted_at>=? AND a.admitted_at<? LIMIT ?",
            (self.environment, policy.provider, policy.account_ref, a, b, MAX_LEDGER_ROWS + 1))
        if len(rows) > MAX_LEDGER_ROWS:
            return self._finish(self._store_result(**base, status="error", code="ledger_capacity"))
        cost, pending = Fraction(0), 0
        for row in rows:
            if row["unit"] != unit.code or (row["usage_key"] is not None and row["cost_unit"] != unit.code):
                return self._finish(self._store_result(**base, status="error", code="cost_unit_mismatch"))
            if row["usage_key"] is None:
                pending += row["reserved_units"]
            else:
                cost += Fraction(int(row["cost_n"]), int(row["cost_d"]))
        upstream = start["total_units"] - end["total_units"] + topups
        # Calls whose usage stayed unknown (a stopped or broken stream) cost somewhere between
        # nothing and their conservative bound, and only the provider knows where. So the
        # recorded cost is a range, [known cost, known cost + those bounds]; drift is how far
        # the upstream spend lies outside it. Spend above the range means the provider billed
        # more than any bound allows (an HTTP error answer billed after all, a wrong price).
        if upstream > cost + pending:
            drift = Fraction(upstream) - cost - pending
        elif upstream < cost:
            drift = Fraction(upstream) - cost
        else:
            drift = Fraction(0)
        status = "drift" if abs(drift) > risk.reconcile_tolerance else "ok"
        return self._finish(self._store_result(
            **base, status=status, code="beyond_tolerance" if status == "drift" else "within_tolerance",
            upstream_units=upstream, topup_units=topups, ledger_cost_n=str(cost.numerator),
            ledger_cost_d=str(cost.denominator), pending_units=pending,
            drift_n=str(drift.numerator), drift_d=str(drift.denominator)))

    def _finish(self, row: dict) -> dict:
        if row["status"] != "ok":
            notify_admins(self.store, "billing.risk.reconciliation",
                          params={"day": day_label(row["window_start"]), "risk": row["status"]},
                          dedupe_key="risk:reconciliation:" + row["id"], object_id=row["id"])
        return row

    def ledger_audit(self, *, trigger: str = "schedule", actor_id: str | None = None) -> dict:
        if trigger not in ("schedule", "admin"):
            raise BillingError("invalid_reconciliation_window")
        now = integer(self.clock())
        base = {"kind": "ledger_audit", "window_start": now, "window_end": now, "trigger": trigger, "actor_id": actor_id}
        try:
            # Compare only; a rebuild is a separate, deliberate maintenance decision.
            differences = Ledger(self.store, environment=self.environment, namespace="platform-risk",
                                 clock=self.clock).audit_balances(rebuild=False)
        except BillingError as exc:
            row = self._store_result(**base, status="error", code=exc.code)
            notify_admins(self.store, "billing.risk.reconciliation", params={"day": day_label(utc_day(now)), "risk": "error"},
                          dedupe_key="risk:audit:" + row["id"], object_id=row["id"])
            return row
        row = self._store_result(**base, status="drift" if differences else "ok",
                                 code="differences" if differences else "consistent", differences=len(differences))
        if differences:
            notify_admins(self.store, "billing.risk.ledger_audit", params={"count": str(len(differences))},
                          dedupe_key="risk:audit:" + row["id"], object_id=row["id"])
        return row

    def history(self, *, kind: str | None = None, before: int | None = None, limit: int = 30) -> dict:
        integer(limit, positive=True, maximum=100)
        clause, args = "", []
        if kind is not None:
            if kind not in ("daily", "ledger_audit"):
                raise BillingError("invalid_reconciliation_kind")
            clause, args = " AND kind=?", [kind]
        if before is not None:
            clause += " AND created_at<?"
            args.append(integer(before))
        rows = self.store.all("SELECT * FROM model_risk_reconciliations WHERE environment=?" + clause +
                              " ORDER BY created_at DESC, id DESC LIMIT ?", (self.environment, *args, limit + 1))
        items = rows[:limit]
        return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit else None}

    def record_topup(self, *, amount_units: int, topped_up_at: int, actor_id: str, reason: str) -> dict:
        """An administrator records money added to the upstream account, so a top-up is not
        mistaken for negative spend. It moves no ledger balance."""
        integer(amount_units, positive=True)
        integer(topped_up_at)
        identifier(actor_id)
        reason = _reason(reason)
        if topped_up_at > self.clock():
            raise ServiceError(400, "risk_topup_future", "充值时间不能晚于现在")
        try:
            unit = CostSettings(self.store, environment=self.environment, clock=self.clock).policy().unit
        except (BillingError, ServiceError):
            raise ServiceError(409, "model_cost_policy_required", "请先发布有效的成本策略") from None
        entry = new_id("rtop")
        self.store.execute("INSERT INTO model_risk_upstream_topups(id,environment,unit,amount_units,topped_up_at,actor_id,"
                           "reason,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (entry, self.environment, unit.code, amount_units, topped_up_at, actor_id, reason,
                            integer(self.clock())))
        return self.store.one("SELECT * FROM model_risk_upstream_topups WHERE id=?", (entry,))

    def topups(self, *, limit: int = 20) -> list[dict]:
        integer(limit, positive=True, maximum=100)
        return self.store.all("SELECT * FROM model_risk_upstream_topups WHERE environment=? "
                              "ORDER BY topped_up_at DESC, id DESC LIMIT ?", (self.environment, limit))
