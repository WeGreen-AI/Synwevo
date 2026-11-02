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
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator, field_validator

from .. import sealed
from ..errors import ServiceError
from ..store import new_id
from .bands import schedule_revision_for
from .cost_settings import ACCOUNT_REF, PROVIDER, CostSettings
from .models import BillingError, PriceSnapshot, identifier

KEY_PURPOSE = "platform-model-key"
log = logging.getLogger("synwevo.billing")
#: Health observations of a published revision; the closing ones stop paid dispatch on it.
HEALTH_STATES = ("model_listed", "model_missing", "models_check_failed", "upstream_balance")
CLOSING = ("model_missing", "upstream_balance")
ALERT_PERMISSION = "platform.models.credential"  # Those who can reopen a closed route by publishing (authz-6).
ADMIN_TARGET = {"origin": "accounts", "path": "/admin?tab=model-provider"}
#: DeepSeek reasoning_effort values; "none" is thinking off (docs/deepseek-provider.md).
REASONING_EFFORTS = ("none", "low", "high", "max")
Effort = Literal["none", "low", "high", "max"]
#: What follows ``deepseek:<env>:`` in a resolved route version (ASCII digits only).
_ROUTE_VERSION = re.compile(r"([1-9][0-9]{0,17})(?::bands:[1-9][0-9]{0,17})?")


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


#: Kinds of incomplete call a charging policy can decide on (shared contract, docs/deepseek-provider.md).
INCOMPLETE_KINDS = ("length", "content_filter", "provider_interrupted", "client_cancelled")


class ChargingPolicy(BaseModel):
    """Whether a payer is charged for the tokens an incomplete call consumed, per kind of ending.

    true charges the trusted terminal usage (never beyond the call's hold); false keeps the
    platform absorbing it as recorded cost, which is what every version before this field
    meant. Anything else that fails (invalid tool output, a rejected step, untrusted or missing
    usage) is never charged. Part of the published revision, so a change is versioned, needs a
    reason, is audited, and invalidates existing quotes and consents through the route version;
    a price term (`price_terms`), so the pricing permission decides it, not the key's.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    length: bool = False                # finish_reason=length (output limit reached)
    content_filter: bool = False        # finish_reason=content_filter
    provider_interrupted: bool = False  # finish_reason=aborted / insufficient_system_resource
    client_cancelled: bool = False      # the person stopped after the final usage had arrived

    def charged(self) -> list[str]:
        return [kind for kind in INCOMPLETE_KINDS if getattr(self, kind)]


class ProviderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    route: RouteSpec | None
    incomplete_charging: ChargingPolicy = Field(default_factory=ChargingPolicy)

    @model_validator(mode="after")
    def enabled_route(self):
        if self.enabled and self.route is None:
            raise ValueError("enabled route required")
        return self


#: What one publication can change (authz-6, docs/agent-work/DECISIONS.md D20). They share a version
#: because a route names its prices and a published price reaches nobody until one does, so a
#: publication asks for the permission of what it changes, not of its address.
#:
#: Fail closed: CUSTODY is this short list and nothing else, and PRICE_TERMS everything a version holds
#: besides, now and in future, whether or not anyone thought about the field. The list has what moves
#: no money: the key (`CredentialChange`, not a field), whether the route is on, the evidence for it and
#: how long that evidence stands. Whoever adds a field gets the pricing permission's by default and has
#: to argue it onto the list. The opposite default filed these under the key, and each moves what payers
#: pay: the ceilings (`gateway.upper_bound` is one call's hold and twelve of them the most an answer
#: costs, and they are what the cost policy admits and `max_tokens` sends), the thinking levels (the
#: tokens one request spends, silently: no price key moves), the model id (the upstream model under the
#: same prices, and what route suspensions and the velocity window are keyed on, `risk.py`). The
#: withdrawal of a route and the reopening of a closed one are also custody (`change_parts`).
CUSTODY, PRICE_TERMS = "custody", "price_terms"
_CUSTODY_ROUTE_FIELDS = frozenset({"confirmation_ref", "account_confirmation_ref", "valid_until"})
#: "route" is read field by field (the line above), "enabled" is the version's own switch.
_NOT_VERSION_TERMS = frozenset({"enabled", "route"})


def _route_terms(spec: dict | None) -> dict:
    route = (spec or {}).get("route") or {}
    return {key: value for key, value in route.items() if key not in _CUSTODY_ROUTE_FIELDS}


def _version_terms(spec: dict | None) -> dict:
    # A version from before the field existed reads as the default it has always meant.
    return {"incomplete_charging": ChargingPolicy().model_dump(),
            **{key: value for key, value in (spec or {}).items() if key not in _NOT_VERSION_TERMS}}


def price_terms(spec: dict | None) -> dict:
    """Everything of a published spec that custody does not keep: every route field off the short
    list, and whatever the version holds besides. A version without a route has only the latter."""
    return {**_route_terms(spec), **_version_terms(spec)}


def _custody_terms(spec: dict | None) -> dict | None:
    if spec is None:
        return None
    route = spec["route"]
    return {"enabled": spec["enabled"], "route": route and {key: route[key] for key in sorted(_CUSTODY_ROUTE_FIELDS)}}


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

        A model id missing from GET /models closes the revision until an administrator publishes
        again (the same settings may be republished for that). Closure is sticky: a later listing
        of the id never reopens it silently, because a provider can reuse an id for a different
        model. A failed check never closes anything. Safe inside an open Store.tx() (same connection).

        An upstream 402 (the operator's provider balance is empty) writes a closure here only where
        the risk module is not installed (`gateway._open_platform_breaker`, the `upstream_balance`
        state). With it, which is every deployment of this repository, a 402 opens the platform
        breaker instead: no closure, no provider alert, and publishing does not reopen it. Its alert
        goes to the holders of the pricing permission, and they close the breaker (`risk.py`).
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
        """The provider revision a resolved route version names: ``deepseek:<env>:<revision>``, or
        ``deepseek:<env>:<revision>:bands:<schedule revision>`` when a price is banded (resolve).
        None for anything else; callers must treat that as closed, never as revision 0."""
        prefix = f"{PROVIDER}:{self.environment}:"
        if not isinstance(version, str) or not version.startswith(prefix):
            return None
        match = _ROUTE_VERSION.fullmatch(version[len(prefix):])
        return int(match.group(1)) if match else None

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
        current administrator who can reopen the route by publishing, the holders of
        ALERT_PERMISSION (inbox and mail by their preferences). An observation of an older
        revision is ignored: it describes a route no longer served.
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
        # A sale price published as cost x markup is only that for its own cost basis.
        if prices[0].basis is not None and prices[0].basis.cost_price_id != route.cost_price_id:
            raise ServiceError(409, "model_price_markup_basis", "销售价由另一成本价加价生成，请选择与之对应的成本价")
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

    def change_parts(self, spec, credential, *, expected_revision) -> tuple[str, ...]:
        """Which parts of the version this publication changes, against the version it names; which
        permission each needs is the caller's to ask (admin_model_provider).

        * Custody, a closed list: a key set, replaced or revoked; whether the route is on; its two
          confirmation references and its validity; withdrawing it; and reopening. Every publication
          reopens a route a closure stopped (`closure`), which is for those who are alerted and can
          see to the cause (ALERT_PERMISSION), so it is custody whatever else it changes. The same
          settings published again are that and nothing else.
        * Price terms, everything else (`price_terms`): any other field of a route, a field added
          later included, so a version's first route and one put back after a withdrawal, even with
          the same prices; the charging of incomplete calls. Withdrawing a route sets none of them.

        So what a closed route needs depends on the repair. Publishing it as it was is custody's;
        fixing a model_missing closure by naming another model also changes a price term, so one
        publication doing it needs both permissions. Separate holders publish three times, each under
        their own permission: custody turns the route off (the new version has no closure), pricing
        names the model while it is off, custody turns it on. A first route, or one put back after a
        withdrawal, has no such path and needs one person holding both.

        Empty against a version that has moved on: `preview` refuses it (`model_revision_conflict`)
        and a replay of a finished request returns its result; neither changes anything."""
        spec, action = _valid(ProviderSpec, spec).model_dump(), _valid(CredentialChange, credential).action
        before = self.read()
        if before["revision"] != expected_revision:
            return ()
        parts = []
        if (action != "keep" or _custody_terms(before["spec"]) != _custody_terms(spec)
                or self.closure(before["revision"]) is not None):
            parts.append(CUSTODY)
        if (spec["route"] is not None and _route_terms(before["spec"]) != _route_terms(spec)
                or _version_terms(before["spec"]) != _version_terms(spec)):
            parts.append(PRICE_TERMS)
        return tuple(parts) or (CUSTODY,)

    def append(self, spec, credential, *, expected_revision, actor_id, reason, operation_id):
        with self.store.conn() as transaction:
            if not transaction.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("model settings append requires an existing Store.tx()")
            identifier(actor_id); identifier(operation_id)
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            credential = _valid(CredentialChange, credential)
            plan = self.preview(spec, credential, expected_revision=expected_revision)
            # Withdrawals are journalled ahead of the change (journal.py), in the caller's
            # transaction and with a receipt that commits with it (journal._intent), so restoring
            # an older backup can bring back neither a revoked/replaced key nor a provider switched
            # off, and a publication that rolled back is voided rather than replayed. A journal
            # failure raises and rolls the publication back.
            from .. import journal
            before = plan["before"]
            if credential.action != "keep" and before["credential_configured"] and before["credential_revision"] > 0:
                journal._intent(self.store, "model_credential_withdrawn", event_id=identifier(operation_id),
                                environment=self.environment, credential_revision=before["credential_revision"])
            if (before["spec"] or {}).get("enabled") and not plan["after"]["enabled"]:
                journal._intent(self.store, "model_provider_disabled", event_id=identifier(operation_id),
                                environment=self.environment)
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
            prices = self._prices(spec.route)
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
            # Quotes and consents bind this version. A banded price's effective rate also
            # depends on the governed calendar, so its revision invalidates them as well.
            schedule = schedule_revision_for(self.store, self.environment, prices)
            if schedule is not None:
                route["version"] += f":bands:{schedule}"
            route["charge_incomplete"] = spec.incomplete_charging.charged()
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
