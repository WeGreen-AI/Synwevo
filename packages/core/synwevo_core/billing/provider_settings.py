"""Governed, fixed-account DeepSeek configuration and sealed active credential.

A data service only: Admin supplies current authorization/confirmation and appends with its
operation result and audit in the same Store transaction. No network, enablement defaults,
prices or funds are created here. Account identity cannot change during key rotation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator, field_validator

from .. import sealed
from ..errors import ServiceError
from ..store import new_id
from .cost_settings import ACCOUNT_REF, PROVIDER, CostSettings
from .models import BillingError, PriceSnapshot, identifier

KEY_PURPOSE = "platform-model-key"
log = logging.getLogger("synwevo.billing")
#: Health observations of a published revision; the closing ones stop paid dispatch on it.
HEALTH_STATES = ("model_listed", "model_missing", "models_check_failed", "upstream_balance")
CLOSING = ("model_missing", "upstream_balance")
ALERT_PERMISSION = "platform.config.manage"  # Those who can reopen the route by publishing.
ADMIN_TARGET = {"origin": "accounts", "path": "/admin?tab=model-provider"}
#: DeepSeek reasoning_effort values; "none" is thinking off (docs/deepseek-provider.md).
REASONING_EFFORTS = ("none", "low", "high", "max")
Effort = Literal["none", "low", "high", "max"]


class RouteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requested_model: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")
    confirmed_route: str
    confirmation_ref: str
    cost_basis_ref: str
    account_confirmation_ref: str
    sale_price_id: str
    cost_price_id: str
    input_token_ceiling: int = Field(ge=1, le=2_000_000)
    output_token_ceiling: int = Field(ge=1, le=32768)
    valid_until: int = Field(ge=1, le=253402300799)
    # Which thinking levels this route offers. Versions published before the field existed
    # allowed every level, so the default keeps their meaning unchanged.
    reasoning_efforts: list[Effort] = Field(default_factory=lambda: list(REASONING_EFFORTS), min_length=1, max_length=4)

    @field_validator("requested_model", "confirmed_route", "confirmation_ref", "cost_basis_ref",
                     "account_confirmation_ref", "sale_price_id", "cost_price_id")
    @classmethod
    def bounded_identifier(cls, value):
        return identifier(value)

    @field_validator("reasoning_efforts")
    @classmethod
    def canonical_efforts(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate reasoning effort")
        return [effort for effort in REASONING_EFFORTS if effort in value]


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    route: RouteSpec | None

    @model_validator(mode="after")
    def enabled_route(self):
        if self.enabled and self.route is None:
            raise ValueError("enabled route required")
        return self


class CredentialChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["keep", "replace", "revoke"]
    api_key: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def valid_secret(self):
        if self.action == "replace":
            value = self.api_key.get_secret_value() if self.api_key is not None else ""
            if not 16 <= len(value) <= 512 or any(not 33 <= ord(c) <= 126 for c in value):
                raise ValueError("invalid credential")
        elif self.api_key is not None:
            raise ValueError("unexpected credential")
        return self

    def commitment(self):
        # Only the commitment enters the signed preview. Never echo the submitted key, even
        # masked. High-entropy provider keys are not user-chosen passwords.
        value = self.api_key.get_secret_value() if self.api_key is not None else ""
        return {"action": self.action, "digest": hashlib.sha256(value.encode()).hexdigest() if value else None}


@dataclass(frozen=True)
class ResolvedProvider:
    revision: int
    credential_revision: int
    route: dict
    api_key: str = field(repr=False)
    provider: str = PROVIDER
    account_ref: str = ACCOUNT_REF


def _valid(model, value):
    try:
        return model.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise ServiceError(400, "model_config_invalid", "模型配置字段无效，请检查路线、有效期和凭据") from None


class ProviderSettings:
    def __init__(self, store, *, environment, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _record(self, row):
        if row is None:
            return {"environment": self.environment, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                    "revision": 0, "spec": None, "credential_revision": 0, "credential_configured": False,
                    "actor_id": None, "created_at": None, "reason": None, "operation_id": None}
        try:
            spec = ProviderSpec.model_validate_json(row["spec_json"]).model_dump()
        except (ValidationError, TypeError, ValueError):
            raise ServiceError(503, "model_config_unavailable", "模型配置记录暂不可用，请联系管理员检查") from None
        return {"environment": self.environment, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                "spec": spec, **{key: row[key] for key in ("revision", "credential_revision", "actor_id",
                    "created_at", "reason", "operation_id")}, "credential_configured": bool(row["credential_configured"])}

    def read(self):
        return self._record(self.store.one("SELECT * FROM model_provider_versions WHERE environment=? "
                                            "ORDER BY revision DESC LIMIT 1", (self.environment,)))

    def status(self):
        """The current version plus its health (automatic closure, last model-id check)."""
        with self.store.tx():
            state = self.read()
            return {**state, "health": self.health(state["revision"])}

    # ------------------------------------------------------------------ route health
    def closure(self, revision: int | None = None) -> dict | None:
        """Why paid dispatch on this revision is closed, or None while it is open.

        An upstream 402 (the operator's provider balance is empty) or a model id missing from
        GET /models closes the revision until an administrator publishes again (the same
        settings may be republished for that). Closure is sticky: a later listing of the id
        never reopens it silently, because a provider can reuse an id for a different model.
        A failed check never closes anything. Safe inside an open Store.tx() (same connection).
        """
        if revision is None:
            row = self.store.one("SELECT MAX(revision) AS r FROM model_provider_versions WHERE environment=?",
                                 (self.environment,))
            revision = row["r"] if row and row["r"] else 0
        found = self.store.one("SELECT state,requested_model,reason_code,created_at FROM model_provider_health "
                               "WHERE environment=? AND revision=? AND state IN ('model_missing','upstream_balance') "
                               "ORDER BY seq LIMIT 1", (self.environment, revision))
        if found is None:
            return None
        return {"state": found["state"], "reason_code": found["reason_code"],
                "requested_model": found["requested_model"], "since": found["created_at"]}

    def route_revision(self, version: str) -> int | None:
        """The provider revision a resolved route version (``deepseek:<env>:<revision>``) names."""
        prefix = f"{PROVIDER}:{self.environment}:"
        if not isinstance(version, str) or not version.startswith(prefix) or not version[len(prefix):].isdigit():
            return None
        return int(version[len(prefix):])

    def health(self, revision: int) -> dict:
        last = self.store.one("SELECT state,created_at FROM model_provider_health WHERE environment=? AND revision=? "
                              "AND state IN ('model_listed','model_missing','models_check_failed') ORDER BY seq DESC LIMIT 1",
                              (self.environment, revision))
        return {"closure": self.closure(revision),
                "last_models_check": {"state": last["state"], "at": last["created_at"]} if last else None}

    def record_health(self, *, state: str, requested_model: str, reason_code: str,
                      revision: int | None = None) -> dict | None:
        """Append one observation of the current revision; returns its closure afterwards.

        Opening/closing transitions are audited as system events and closing alerts every
        current administrator who can reopen the route (inbox and mail by their preferences).
        An observation of an older revision is ignored: it describes a route no longer served.
        """
        if state not in HEALTH_STATES:
            raise BillingError("invalid_health_state")
        identifier(requested_model); identifier(reason_code)
        with self.store.tx() as t:
            current = t.execute("SELECT MAX(revision) FROM model_provider_versions WHERE environment=?",
                                (self.environment,)).fetchone()[0]
            if not current or (revision is not None and revision != current):
                return None
            before = self.closure(current)
            if state in CLOSING and before is not None:
                return before  # Already closed: one closure event, one alert per revision.
            seq = t.execute("SELECT COALESCE(MAX(seq),0)+1 FROM model_provider_health WHERE environment=?",
                            (self.environment,)).fetchone()[0]
            t.execute("INSERT INTO model_provider_health(environment,seq,revision,state,requested_model,reason_code,created_at) "
                      "VALUES (?,?,?,?,?,?,?)", (self.environment, seq, current, state, requested_model, reason_code, self.clock()))
            after = self.closure(current)
            changed = before is None and after is not None
            if changed:
                t.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
                          "result,request_id,ip,meta) VALUES (?,?,'system',NULL,NULL,?,'model_provider',?,'ok',NULL,NULL,?)",
                          (new_id("ae"), time.time(), "admin.model_provider.closed",
                           ACCOUNT_REF, json.dumps({"billing_environment": self.environment, "provider": PROVIDER,
                               "revision": current, "state": state, "reason_code": reason_code,
                               "requested_model": requested_model}, sort_keys=True)))
        if changed:
            self._alert(after, current)
        return after

    def _alert(self, closure: dict, revision: int) -> int:
        """Never raises: an alert that cannot be delivered must not reopen paid dispatch."""
        from ..notifications import Notifier
        from ..platform_access import ROLES, PlatformAccess
        kind = "billing.model.route_missing" if closure["state"] == "model_missing" else "billing.model.upstream_balance"
        sent = 0
        try:
            roles = sorted(role for role, permissions in ROLES.items() if ALERT_PERMISSION in permissions)
            rows = self.store.all("SELECT DISTINCT user_id FROM platform_role_grants WHERE revoked_at IS NULL "
                                  "AND (expires_at IS NULL OR expires_at>?) AND role IN (" + ",".join("?" * len(roles)) +
                                  ") ORDER BY user_id LIMIT 50", (time.time(), *roles))
            access, notifier = PlatformAccess(self.store), Notifier(self.store)
            for row in rows:
                try:
                    if ALERT_PERMISSION in access.effective_permissions(row["user_id"]) and notifier.emit(
                            row["user_id"], kind, params={"model": closure["requested_model"],
                                                          "environment": self.environment},
                            object_kind="model_provider", object_id=f"{self.environment}:{revision}",
                            dedupe_key=f"model-provider:{self.environment}:{revision}:{closure['state']}",
                            target=ADMIN_TARGET):
                        sent += 1
                except Exception as exc:  # noqa: BLE001 - one reader's failure is not another's
                    log.warning("model route alert to one administrator failed: %s", type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            log.warning("model route alert could not be delivered: %s", type(exc).__name__)
        return sent

    def _prices(self, route):
        prices = []
        for price_id, purpose in ((route.sale_price_id, "sale"), (route.cost_price_id, "cost")):
            row = self.store.one("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?",
                                 (self.environment, price_id))
            if row is None:
                raise ServiceError(409, "model_price_unavailable", "请先发布同一环境的销售价格与成本价格")
            try:
                raw = row["snapshot"]
                price = PriceSnapshot.from_dict(raw if isinstance(raw, dict) else json.loads(raw))
            except (BillingError, TypeError, ValueError, KeyError):
                raise ServiceError(503, "model_config_unavailable", "模型价格记录暂不可用，请联系管理员检查") from None
            if (price.purpose != purpose or price.provider != PROVIDER or price.model != route.confirmed_route
                    or price.effective_from > self.clock()):
                raise ServiceError(409, "model_price_mismatch", "模型路线与已生效价格不匹配，请重新选择")
            prices.append(price)
        return prices

    def preview(self, spec, credential, *, expected_revision):
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63-1:
            raise ServiceError(400, "model_bad_revision", "模型配置版本无效，请重新读取")
        spec, credential = _valid(ProviderSpec, spec), _valid(CredentialChange, credential)
        with self.store.tx():
            before = self.read()
            if before["revision"] != expected_revision:
                raise ServiceError(409, "model_revision_conflict", "模型配置已变化，请保留输入并重新读取")
            present = before["credential_configured"] if credential.action == "keep" else credential.action == "replace"
            if spec.enabled:
                if not present:
                    raise ServiceError(409, "model_credential_required", "启用模型前请配置平台凭据")
                if spec.route.valid_until <= self.clock():
                    raise ServiceError(409, "model_route_expired", "模型路线核实已过期，请重新核实")
                _, cost = self._prices(spec.route)
                try:
                    policy = CostSettings(self.store, environment=self.environment, clock=self.clock).policy()
                except BillingError:
                    raise ServiceError(409, "model_cost_policy_required", "启用模型前请发布有效的成本预算") from None
                if not policy.enabled or policy.unit != cost.unit:
                    raise ServiceError(409, "model_cost_policy_required", "模型成本价格与已启用的成本预算不匹配")
            # Publishing the same settings again is the audited way to reopen a route that an
            # upstream 402 or a missing model id closed; otherwise an identical version is refused.
            if (before["spec"] == spec.model_dump() and credential.action == "keep"
                    and self.closure(before["revision"]) is None):
                raise ServiceError(409, "model_no_change", "模型配置未发生变化，无需提交")
            revision = before["credential_revision"] + int(credential.action != "keep")
            return {"before": before, "after": spec.model_dump(), "credential": credential.commitment(),
                    "credential_revision": revision, "credential_configured": present}

    def append(self, spec, credential, *, expected_revision, actor_id, reason, operation_id):
        with self.store.conn() as transaction:
            if not transaction.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("model settings append requires an existing Store.tx()")
            identifier(actor_id); identifier(operation_id)
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            credential = _valid(CredentialChange, credential)
            plan = self.preview(spec, credential, expected_revision=expected_revision)
            if credential.action != "keep":
                token = (sealed.seal(self.store, KEY_PURPOSE, credential.api_key.get_secret_value())
                         if credential.action == "replace" else None)
                transaction.execute("INSERT INTO model_provider_credentials(environment,revision,secret_enc) VALUES (?,?,?) "
                    "ON CONFLICT(environment) DO UPDATE SET revision=excluded.revision,secret_enc=excluded.secret_enc",
                    (self.environment, plan["credential_revision"], token))
            transaction.execute("INSERT INTO model_provider_versions(environment,revision,spec_json,credential_revision,"
                "credential_configured,actor_id,reason,operation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (self.environment, expected_revision+1, json.dumps(plan["after"], sort_keys=True, separators=(",", ":")),
                 plan["credential_revision"], int(plan["credential_configured"]), actor_id, reason.strip(), operation_id, self.clock()))
            return self.read()

    def resolve(self):
        """Private dispatch consumer; callers must still enforce current actor/data/payment rights."""
        with self.store.tx():
            state = self.read()
            if not state["spec"] or not state["spec"]["enabled"]:
                return None
            spec = _valid(ProviderSpec, state["spec"])
            if spec.route.valid_until <= self.clock():
                raise BillingError("route_confirmation_expired")
            self._prices(spec.route)
            row = self.store.one("SELECT revision,secret_enc FROM model_provider_credentials WHERE environment=?",
                                 (self.environment,))
            if not row or row["revision"] != state["credential_revision"] or not row["secret_enc"]:
                raise BillingError("model_credential_unavailable")
            try:
                secret = sealed.unseal(self.store, KEY_PURPOSE, row["secret_enc"])
            except Exception:
                raise BillingError("model_credential_unavailable") from None
            route = spec.route.model_dump(exclude={"account_confirmation_ref"})
            route["version"] = f"deepseek:{self.environment}:{state['revision']}"
            return ResolvedProvider(state["revision"], state["credential_revision"], route, secret)

    def history(self, *, before_revision=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100 or (before_revision is not None and
                (type(before_revision) is not int or not 0 <= before_revision < 2**63-1)):
            raise ServiceError(400, "model_bad_page", "模型配置历史分页参数无效")
        clause, args = (" AND revision<?", (before_revision,)) if before_revision is not None else ("", ())
        rows = self.store.all("SELECT * FROM model_provider_versions WHERE environment=?"+clause+
            " ORDER BY revision DESC LIMIT ?", (self.environment, *args, limit+1))
        items = [self._record(row) for row in rows[:limit]]
        return {"items": items, "next_before_revision": items[-1]["revision"] if len(rows) > limit else None}
