"""Versioned platform-account cost policy, shared by Account governance and dispatch.

Authorization, signed confirmation, audit and idempotency belong to the existing Admin
operation layer. This data service writes only inside its caller's Store transaction.
No key, provider route, user budget, wallet balance or model enablement lives here.
"""
from __future__ import annotations

import json
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..errors import ServiceError
from .admission import CostAdmission, CostPolicy
from .catalogue import PriceUnit
from .ledger import Ledger
from .models import BillingError, Unit, identifier

PROVIDER = "deepseek"
# This installation currently uses one platform provider account. All keys, namespaces
# and routes share this identity. A credential rotation cannot choose a new budget scope.
ACCOUNT_REF = "platform-deepseek"


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

    @field_validator("daily_budget", "weekly_budget", "monthly_budget", "unknown_budget")
    @classmethod
    def bounded_amount(cls, value):
        if int(value) > 2**63-1:
            raise ValueError("invalid budget")
        return value

    def policy(self, revision: str) -> CostPolicy:
        values = self.model_dump()
        values["unit"] = Unit(**values["unit"])
        for name in ("daily_budget", "weekly_budget", "monthly_budget", "unknown_budget"):
            values[name] = int(values[name])
        return CostPolicy(revision=revision, account_ref=ACCOUNT_REF, provider=PROVIDER, **values)


def _revision(value):
    if type(value) is not int or not 0 <= value < 2**63-1:
        raise ServiceError(400, "cost_bad_revision", "成本策略版本无效，请重新读取")
    return value


def validate_spec(value):
    try:
        return CostPolicySpec.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise ServiceError(400, "cost_invalid", "成本策略字段无效，请检查金额、单位及调用上限") from None


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
