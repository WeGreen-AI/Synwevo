"""Versioned platform-account cost policy, shared by Account governance and dispatch.

Authorization, signed confirmation, audit and idempotency belong to the existing Admin
operation layer. This data service writes only inside its caller's Store transaction.
No key, provider route, user budget, wallet balance or model enablement lives here.
"""
from __future__ import annotations

import json
import time
from typing import Annotated

from pydantic import (BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator,
                      model_serializer, model_validator)

from ..errors import ServiceError
from .admission import CostAdmission, CostPolicy
from .catalogue import PriceUnit
from .ledger import Ledger
from .models import BillingError, Unit, identifier
from .risk import POLL_MAX_S, POLL_MIN_S, WINDOW_MAX_S, WINDOW_MIN_S, RiskPolicy
from .traffic import TrafficLimits

PROVIDER = "deepseek"
# This installation currently uses one platform provider account. All keys, namespaces
# and routes share this identity. A credential rotation cannot choose a new budget scope.
ACCOUNT_REF = "platform-deepseek"


class TrafficSpec(BaseModel):
    """Optional. Platform rates must match the verified provider-account quota (several keys
    of one account are one quota). A payer cannot be allowed more than the platform."""
    model_config = ConfigDict(extra="forbid", strict=True)
    platform_rpm: int = Field(ge=1, le=1_000_000)
    platform_tpm: int = Field(ge=1, le=10**12)
    payer_rpm: int = Field(ge=1, le=1_000_000)
    payer_tpm: int = Field(ge=1, le=10**12)
    payer_inflight: int = Field(ge=1, le=10000)
    wait_seconds: int = Field(ge=0, le=900)
    max_waiting: int = Field(ge=1, le=10000)
    max_waiting_per_payer: int = Field(ge=1, le=10000)

    def limits(self) -> TrafficLimits:
        return TrafficLimits(**self.model_dump())


# Amounts are integer strings in the policy unit's smallest unit, bounded to signed 64 bits.
Amount = Annotated[str, StringConstraints(pattern=r"^(0|[1-9][0-9]{0,18})$")]


def _int64(value):
    if value is not None and int(value) > 2**63-1:
        raise ValueError("invalid amount")
    return value


class RiskSpec(BaseModel):
    """Optional (docs/platform/model-risk.md). Every field is explicit; null switches a check off.
    Velocity counts provider cost in the policy unit; balance thresholds compare the upstream
    balance in the same unit (its code must be the balance currency)."""
    model_config = ConfigDict(extra="forbid", strict=True)
    allowlist_only: bool
    payer_window_s: int = Field(ge=WINDOW_MIN_S, le=WINDOW_MAX_S)
    payer_window_cost: Amount | None
    route_window_s: int = Field(ge=WINDOW_MIN_S, le=WINDOW_MAX_S)
    route_window_cost: Amount | None
    balance_warn: Amount | None
    balance_close: Amount | None
    balance_poll_s: int = Field(ge=POLL_MIN_S, le=POLL_MAX_S)
    reconcile_tolerance: Amount
    pending_alert: Amount | None

    @field_validator("payer_window_cost", "route_window_cost", "balance_warn", "balance_close",
                     "reconcile_tolerance", "pending_alert")
    @classmethod
    def bounded_amount(cls, value):
        return _int64(value)

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if (self.balance_warn is not None and self.balance_close is not None
                and int(self.balance_close) > int(self.balance_warn)):
            raise ValueError("close threshold above warning threshold")
        return self

    def policy(self) -> RiskPolicy:
        values = self.model_dump()
        for name in ("payer_window_cost", "route_window_cost", "balance_warn", "balance_close",
                     "reconcile_tolerance", "pending_alert"):
            values[name] = int(values[name]) if values[name] is not None else None
        return RiskPolicy(**values)


class CostPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    unit: PriceUnit
    daily_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    weekly_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    monthly_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    unknown_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    max_inflight: int = Field(ge=1, le=10000)
    max_unknown: int = Field(ge=1, le=10000)
    traffic: TrafficSpec | None = None
    # Per-call cap on the reserved cost bound (same unit); absent = no cap.
    max_call_cost: Amount | None = None
    risk: RiskSpec | None = None

    @model_serializer(mode="wrap")
    def _without_unset_traffic(self, handler):
        # Revisions without these optional sections keep their exact stored and signed shape.
        data = handler(self)
        if isinstance(data, dict):
            for name in ("traffic", "max_call_cost", "risk"):
                if data.get(name) is None:
                    data.pop(name, None)
        return data

    @field_validator("daily_budget", "weekly_budget", "monthly_budget", "unknown_budget", "max_call_cost")
    @classmethod
    def bounded_amount(cls, value):
        if value is not None and int(value) > 2**63-1:
            raise ValueError("invalid budget")
        return value

    def policy(self, revision: str) -> CostPolicy:
        values = self.model_dump()
        values["unit"] = Unit(**values["unit"])
        values["traffic"] = self.traffic.limits() if self.traffic is not None else None
        values["risk"] = self.risk.policy() if self.risk is not None else None
        values["max_call_cost"] = int(self.max_call_cost) if self.max_call_cost is not None else None
        for name in ("daily_budget", "weekly_budget", "monthly_budget", "unknown_budget"):
            values[name] = int(values[name])
        return CostPolicy(revision=revision, account_ref=ACCOUNT_REF, provider=PROVIDER, **values)


def _revision(value):
    if type(value) is not int or not 0 <= value < 2**63-1:
        raise ServiceError(400, "cost_bad_revision", "成本策略版本无效，请重新读取")
    return value


def validate_spec(value):
    try:
        spec = CostPolicySpec.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise ServiceError(400, "cost_invalid", "成本策略字段无效，请检查金额、单位及调用上限") from None
    try:
        spec.policy("validation")
    except BillingError:
        raise ServiceError(400, "cost_traffic_invalid",
                           "速率与排队设置无效：付款人上限不能高于平台上限，排队上限须在总上限内") from None
    return spec


class CostSettings:
    def __init__(self, store, *, environment, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _record(self, row):
        if row is None:
            return {"environment": self.environment, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                    "revision": 0, "spec": None, "actor_id": None, "created_at": None,
                    "reason": None, "operation_id": None}
        try:
            spec = CostPolicySpec.model_validate_json(row["spec_json"])
        except (ValidationError, TypeError, ValueError):
            raise ServiceError(503, "cost_settings_unavailable", "成本策略记录暂不可用，请联系管理员检查") from None
        return {"environment": self.environment, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                "spec": spec.model_dump(), **{key: row[key] for key in (
                    "revision", "actor_id", "created_at", "reason", "operation_id")}}

    def _current(self):
        return self._record(self.store.one(
            "SELECT * FROM model_cost_policy_versions WHERE environment=? ORDER BY revision DESC LIMIT 1",
            (self.environment,)))

    def _units(self):
        rows = self.store.all(
            "SELECT DISTINCT u.code,u.scale FROM billing_units u JOIN billing_prices p "
            "ON json_extract(p.snapshot,'$.unit.code')=u.code WHERE p.environment=? "
            "AND json_extract(p.snapshot,'$.purpose')='cost' AND json_extract(p.snapshot,'$.provider')=? "
            "ORDER BY u.code LIMIT 101", (self.environment, PROVIDER))
        if len(rows) > 100:
            raise ServiceError(503, "cost_unit_capacity", "成本计费单位过多，请联系管理员整理配置")
        return rows

    def read(self):
        # The state and units belong to one snapshot, even if another process publishes.
        with self.store.tx():
            return {**self._current(), "units": self._units()}

    def policy(self) -> CostPolicy:
        """Current local consumer, including within Ledger.dispatch's writer transaction."""
        current = self._current()
        if current["spec"] is None:
            raise BillingError("cost_not_configured")
        return validate_spec(current["spec"]).policy(f"model-cost:{self.environment}:{current['revision']}")

    def preview(self, spec, *, expected_revision):
        expected_revision = _revision(expected_revision)
        spec = validate_spec(spec)
        before = self.read()
        if before["revision"] != expected_revision:
            raise ServiceError(409, "cost_revision_conflict", "成本策略已变化，请保留输入并重新读取", {"current": before})
        if before["spec"] == spec.model_dump():
            raise ServiceError(409, "cost_no_change", "成本策略未发生变化，无需提交")
        if spec.unit.model_dump() not in before["units"]:
            raise ServiceError(409, "cost_unit_unavailable", "请先发布同一环境与单位的模型成本价格")
        # Never reinterpret existing exposure in a new unit. A disabled policy can still
        # be published when accounting needs investigation; disabling must remain possible.
        if before["spec"] is None or before["spec"]["unit"] != spec.unit.model_dump():
            probe = CostAdmission(Ledger(self.store, environment=self.environment,
                                        namespace="platform-cost-governance", clock=self.clock),
                                  current_policy=lambda: spec.policy("publication-check"))
            try:
                probe.snapshot()
            except BillingError as exc:
                raise ServiceError(409, "cost_exposure_conflict", "现有成本记录与新单位不兼容，请先核实未决用量") from exc
        return {"before": before, "after": spec.model_dump()}

    def append(self, spec, *, expected_revision, actor_id, reason, operation_id):
        with self.store.conn() as transaction:
            if not transaction.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("cost settings append requires an existing Store.tx()")
            for value in (actor_id, operation_id):
                try:
                    identifier(value)
                except BillingError:
                    raise ServiceError(400, "cost_invalid_metadata", "成本策略操作信息无效") from None
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            plan = self.preview(spec, expected_revision=expected_revision)
            transaction.execute(
                "INSERT INTO model_cost_policy_versions(environment,revision,spec_json,actor_id,reason,operation_id,created_at) "
                "VALUES (?,?,?,?,?,?,?)", (self.environment, expected_revision+1,
                    json.dumps(plan["after"], sort_keys=True, separators=(",", ":")), actor_id, reason.strip(),
                    operation_id, self.clock()))
            return self.read()

    def history(self, *, before_revision=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError(400, "cost_bad_page", "成本策略历史分页参数无效")
        if before_revision is not None:
            before_revision = _revision(before_revision)
        clause, args = (" AND revision<?", (before_revision,)) if before_revision is not None else ("", ())
        rows = self.store.all("SELECT * FROM model_cost_policy_versions WHERE environment=?"+clause+
                              " ORDER BY revision DESC LIMIT ?", (self.environment, *args, limit+1))
        items = [self._record(row) for row in rows[:limit]]
        return {"items": items, "next_before_revision": items[-1]["revision"] if len(rows) > limit else None}
