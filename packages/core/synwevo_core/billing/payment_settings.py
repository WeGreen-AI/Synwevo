"""Governed wallet top-up settings and the sealed payment provider credentials.

A data service only (the Admin route supplies current authorization, reason, idempotency and
audit in the same Store transaction). Off by default: with no published version, or one with
``enabled=false``, or no stored credentials, top-ups are "not configured" and the product says
so. The provider is Stripe (owner decision §6.1): Checkout in CNY with card, WeChat Pay and
Alipay. Packages and amount bounds are server-side: a browser never decides an amount.

Keys are bound to the billing environment: a ``test`` deployment accepts only Stripe test-mode
keys (``sk_test_`` / ``rk_test_``), ``live`` only live-mode keys, so a test instance cannot
charge real cards and a live instance cannot silently run in test mode. The webhook signing
secret starts with ``whsec_``. Neither is ever returned, logged or put in a preview: only a
SHA-256 commitment of a submitted value enters the request hash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from .. import sealed
from ..errors import ServiceError
from .models import identifier

KEY_PURPOSE = "platform-payment-key"
PROVIDER = "stripe"
MERCHANT = "stripe-platform"
MAX_AMOUNT_MINOR = 10_000_000          # CNY 100,000 per top-up: an upper bound on any setting


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PackageSpec(Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    amount_minor: int = Field(ge=1, le=MAX_AMOUNT_MINOR)
    label: str | None = Field(default=None, min_length=1, max_length=60)

    @field_validator("label")
    @classmethod
    def plain_label(cls, value):
        if value is not None and (value != value.strip() or any(ord(c) < 32 for c in value)):
            raise ValueError("label")
        return value


class VelocitySpec(Strict):
    max_orders_per_hour: int = Field(ge=1, le=100)
    max_orders_per_day: int = Field(ge=1, le=1000)
    max_pending: int = Field(ge=1, le=20)
    max_amount_per_day_minor: int = Field(ge=1, le=100 * MAX_AMOUNT_MINOR)


class PaymentSpec(Strict):
    enabled: bool
    operator_name: str = Field(min_length=1, max_length=120)
    packages: list[PackageSpec] = Field(max_length=12)
    custom_amount: bool
    min_amount_minor: int = Field(ge=1, le=MAX_AMOUNT_MINOR)
    max_amount_minor: int = Field(ge=1, le=MAX_AMOUNT_MINOR)
    velocity: VelocitySpec

    @field_validator("operator_name")
    @classmethod
    def plain_name(cls, value):
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("operator_name")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if self.min_amount_minor > self.max_amount_minor:
            raise ValueError("bounds")
        if len({p.id for p in self.packages}) != len(self.packages):
            raise ValueError("package ids")
        if any(not self.min_amount_minor <= p.amount_minor <= self.max_amount_minor for p in self.packages):
            raise ValueError("package outside bounds")
        if self.enabled and not self.packages and not self.custom_amount:
            raise ValueError("nothing to buy")
        return self


SECRET_KEY = {"test": re.compile(r"^(sk|rk)_test_[A-Za-z0-9]{16,240}$"),
              "live": re.compile(r"^(sk|rk)_live_[A-Za-z0-9]{16,240}$")}
WEBHOOK_SECRET = re.compile(r"^whsec_[A-Za-z0-9+/=]{16,240}$")


class CredentialChange(Strict):
    action: Literal["keep", "replace", "revoke"]
    secret_key: SecretStr | None = Field(default=None, repr=False)
    webhook_secret: SecretStr | None = Field(default=None, repr=False)
    # Revoke even though top-up checkouts opened under these keys can still be paid: those
    # payments can then no longer be verified or credited automatically (review payments-1).
    # Only for revoke; an explicit, audited decision (e.g. the signing secret itself leaked).
    override_open_checkouts: bool = False

    @model_validator(mode="after")
    def present(self):
        if self.action == "replace":
            if self.secret_key is None or self.webhook_secret is None:
                raise ValueError("both secrets required")
        elif self.secret_key is not None or self.webhook_secret is not None:
            raise ValueError("unexpected secret")
        if self.override_open_checkouts and self.action != "revoke":
            raise ValueError("override only for revoke")
        return self

    def check(self, environment: str) -> None:
        if self.action != "replace":
            return
        if not SECRET_KEY[environment].fullmatch(self.secret_key.get_secret_value()):
            raise ServiceError(400, "payment_key_mode", "支付密钥与当前计费环境不符：测试环境只接受测试密钥，正式环境只接受正式密钥")
        if not WEBHOOK_SECRET.fullmatch(self.webhook_secret.get_secret_value()):
            raise ServiceError(400, "payment_config_invalid", "支付配置字段无效，请检查套餐、金额与密钥")

    def commitment(self) -> dict:
        def digest(value):
            return hashlib.sha256(value.get_secret_value().encode()).hexdigest() if value is not None else None
        return {"action": self.action, "secret_key": digest(self.secret_key), "webhook_secret": digest(self.webhook_secret),
                **({"override_open_checkouts": True} if self.override_open_checkouts else {})}


@dataclass(frozen=True)
class ResolvedPayments:
    revision: int
    spec: dict
    secret_key: str = field(repr=False)


def _valid(model, value):
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise ServiceError(400, "payment_config_invalid", "支付配置字段无效，请检查套餐、金额与密钥") from None


class PaymentSettings:
    def __init__(self, store, *, environment, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _record(self, row):
        if row is None:
            return {"environment": self.environment, "provider": PROVIDER, "revision": 0, "spec": None,
                    "credential_revision": 0, "credential_configured": False, "actor_id": None,
                    "created_at": None, "reason": None, "operation_id": None}
        try:
            spec = PaymentSpec.model_validate_json(row["spec_json"]).model_dump()
        except (ValidationError, TypeError, ValueError):
            raise ServiceError(503, "payment_config_unavailable", "支付配置记录暂不可用，请联系管理员检查") from None
        return {"environment": self.environment, "provider": PROVIDER, "spec": spec,
                **{k: row[k] for k in ("revision", "credential_revision", "actor_id", "created_at", "reason",
                                        "operation_id")},
                "credential_configured": bool(row["credential_configured"])}

    def read(self, revision: int | None = None):
        if revision is None:
            row = self.store.one("SELECT * FROM payment_settings_versions WHERE environment=? "
                                 "ORDER BY revision DESC LIMIT 1", (self.environment,))
        else:
            row = self.store.one("SELECT * FROM payment_settings_versions WHERE environment=? AND revision=?",
                                 (self.environment, revision))
        return self._record(row)

    def open_checkouts(self) -> dict:
        """Top-up checkouts that can still be paid (not yet expired): their payments are verified
        with the current webhook signing secret. ``until`` is when the last of them expires."""
        row = self.store.one("SELECT COUNT(*) AS n, MAX(expires_at) AS until FROM payment_topups WHERE environment=? "
                             "AND status IN ('creating','pending') AND expires_at>?", (self.environment, self.clock()))
        return {"count": row["n"], "until": row["until"] if row["n"] else None}

    def plan(self, spec, credential, *, expected_revision):
        if type(expected_revision) is not int or not 0 <= expected_revision < 2**63 - 1:
            raise ServiceError(400, "payment_bad_revision", "支付配置版本无效，请重新读取")
        spec, credential = _valid(PaymentSpec, spec), _valid(CredentialChange, credential)
        credential.check(self.environment)
        before = self.read()
        if before["revision"] != expected_revision:
            raise ServiceError(409, "payment_revision_conflict", "支付配置已变化，请保留输入并重新读取")
        if credential.action == "revoke" and not credential.override_open_checkouts:
            # Revoking deletes the webhook signing secret: a customer who then pays a checkout that
            # is still open is charged but can never be credited. Close top-ups (keep the keys)
            # and revoke once they expired (at most CHECKOUT_TTL_S), or override explicitly.
            still_open = self.open_checkouts()
            if still_open["count"]:
                raise ServiceError(409, "payment_revoke_open_checkouts", "仍有未到期的充值收银台：撤销密钥后，在这些收银台完成的付款将无法验证、无法入账。请先关闭充值并保留密钥，等它们到期后再撤销；确需立即撤销请明确确认",
                                   {"open_checkouts": still_open["count"], "open_until": still_open["until"]})
        present = before["credential_configured"] if credential.action == "keep" else credential.action == "replace"
        if spec.enabled and not present:
            raise ServiceError(409, "payment_credential_required", "开启充值前请先配置支付密钥与 Webhook 签名密钥")
        if before["spec"] == spec.model_dump() and credential.action == "keep":
            raise ServiceError(409, "payment_no_change", "支付配置未发生变化，无需提交")
        revision = before["credential_revision"] + int(credential.action != "keep")
        return {"before": before, "after": spec.model_dump(), "credential": credential.commitment(),
                "credential_revision": revision, "credential_configured": present}

    def append(self, spec, credential, *, expected_revision, actor_id, reason, operation_id):
        with self.store.conn() as transaction:
            if not transaction.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("payment settings append requires an existing Store.tx()")
            identifier(actor_id)
            identifier(operation_id)
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            credential = _valid(CredentialChange, credential)
            plan = self.plan(spec, credential, expected_revision=expected_revision)
            if credential.action != "keep":
                if credential.action == "replace":
                    secret = sealed.seal(self.store, KEY_PURPOSE, credential.secret_key.get_secret_value())
                    hook = sealed.seal(self.store, KEY_PURPOSE, credential.webhook_secret.get_secret_value())
                else:
                    secret = hook = None
                transaction.execute(
                    "INSERT INTO payment_credentials(environment,revision,secret_enc,webhook_secret_enc) VALUES (?,?,?,?) "
                    "ON CONFLICT(environment) DO UPDATE SET revision=excluded.revision,secret_enc=excluded.secret_enc,"
                    "webhook_secret_enc=excluded.webhook_secret_enc",
                    (self.environment, plan["credential_revision"], secret, hook))
            transaction.execute(
                "INSERT INTO payment_settings_versions(environment,revision,spec_json,credential_revision,"
                "credential_configured,actor_id,reason,operation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (self.environment, expected_revision + 1, json.dumps(plan["after"], sort_keys=True, separators=(",", ":")),
                 plan["credential_revision"], int(plan["credential_configured"]), actor_id, reason.strip(),
                 operation_id, self.clock()))
            return self.read()

    def _secrets(self, state):
        row = self.store.one("SELECT revision,secret_enc,webhook_secret_enc FROM payment_credentials WHERE environment=?",
                             (self.environment,))
        if (not row or row["revision"] != state["credential_revision"] or not row["secret_enc"]
                or not row["webhook_secret_enc"]):
            return None
        try:
            return (sealed.unseal(self.store, KEY_PURPOSE, row["secret_enc"]),
                    sealed.unseal(self.store, KEY_PURPOSE, row["webhook_secret_enc"]))
        except Exception:  # noqa: BLE001 - a sealed value that does not open is "unavailable", never echoed
            return None

    def resolve(self) -> ResolvedPayments | None:
        """For creating a checkout: None unless enabled and the credentials open."""
        state = self.read()
        if not state["spec"] or not state["spec"]["enabled"] or not state["credential_configured"]:
            return None
        secrets = self._secrets(state)
        if secrets is None:
            return None
        return ResolvedPayments(state["revision"], state["spec"], secrets[0])

    def webhook_secret(self) -> str | None:
        """For verifying deliveries: available while credentials are stored, even when new
        top-ups are switched off, so payments already started still reach the wallet."""
        state = self.read()
        if not state["credential_configured"]:
            return None
        secrets = self._secrets(state)
        return secrets[1] if secrets else None
