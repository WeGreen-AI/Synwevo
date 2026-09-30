"""Price drafting shared by Account governance and the model gateway.

Amounts at this boundary are decimal strings in major units per million tokens. They
become rational smallest-unit rates; neither parsing nor quotes use binary floating point.
This is a data service, like PlatformSettings: callers authorize and own the writer
transaction, including their durable operation result and audit. Publication changes no
active route, wallet balance or historical settlement.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, localcontext
from fractions import Fraction
import json
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..errors import ServiceError
from ..store import new_id
from .models import BillingError, PriceSnapshot, Rate, Unit, Usage


class PriceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,15}$")
    scale: int = Field(ge=0, le=12)


class PriceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    purpose: Literal["sale", "cost"]
    unit: PriceUnit
    provider: Literal["deepseek"]
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    input_uncached: str = Field(pattern=r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$")
    input_cached: str = Field(pattern=r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$")
    output: str = Field(pattern=r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$")
    terms_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    effective_from: int = Field(ge=0, le=253402300799)
    previous_price_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid name")
        return value.strip()

    def snapshot(self, price_id):
        def rate(value):
            exact = Fraction(Decimal(value)) * 10 ** self.unit.scale
            return Rate(exact.numerator, exact.denominator)
        return PriceSnapshot(price_id, self.purpose, Unit(**self.unit.model_dump()), self.provider, self.model,
            rate(self.input_uncached), rate(self.input_cached), rate(self.output), self.terms_ref, self.effective_from)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _revision(value):
    if type(value) is not int or not 1 <= value < 2**63 - 1:
        raise ServiceError(400, "price_bad_revision", "价格版本无效，请重新读取草稿")
    return value


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise ServiceError(400, "price_bad_key", "操作编号无效，请重新开始")
    return value


def validate_spec(value):
    try:
        spec = PriceSpec.model_validate(value)
        spec.snapshot("validation")  # Also enforce ledger integer/rational bounds.
        return spec
    except (ValidationError, BillingError, ValueError, TypeError):
        # No submitted values (which might include secrets) escape through validation detail.
        raise ServiceError(400, "price_invalid", "价格字段无效，请检查单位、费率和生效时间") from None


class PriceCatalogue:
    def __init__(self, store, *, environment, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))

    def _writer(self):
        with self.store.conn() as c:
            if not c.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("price mutation requires an existing Store.tx()")
            return c

    @staticmethod
    def _entry(row):
        return {key: row[key] for key in ("id", "revision", "status", "price_id", "actor_id", "created_at", "updated_at")} | {
            "spec": json.loads(row["spec_json"])}

    def get(self, draft_id, *, expected_revision=None):
        row = self.store.one("SELECT * FROM model_price_drafts WHERE environment=? AND id=?",
                             (self.environment, draft_id))
        if not row:
            raise ServiceError(404, "price_not_found", "找不到该价格版本")
        item = self._entry(row)
        if expected_revision is not None and item["revision"] != _revision(expected_revision):
            raise ServiceError(409, "price_revision_conflict", "价格已变化，请保留输入并重新读取", {"current": item})
        return item

    def list(self, *, before=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100 or (before is not None and (
                not isinstance(before, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", before) or int(before) >= 2**63)):
            raise ServiceError(400, "price_bad_page", "价格列表分页参数无效")
        clause, args = (" AND seq<?", (int(before),)) if before else ("", ())
        rows = self.store.all("SELECT * FROM model_price_drafts WHERE environment=?" + clause + " ORDER BY seq DESC LIMIT ?",
                              (self.environment, *args, limit+1))
        return {"environment": self.environment, "items": [self._entry(row) for row in rows[:limit]],
                "next_before": str(rows[limit-1]["seq"]) if len(rows) > limit else None}

    def _references(self, spec):
        unit = self.store.one("SELECT scale FROM billing_units WHERE code=?", (spec.unit.code,))
        if unit and unit["scale"] != spec.unit.scale:
            raise ServiceError(409, "price_unit_conflict", "该计费单位已使用不同精度，请选择新的单位名称")
        if spec.previous_price_id:
            previous = self.published(spec.previous_price_id)
            if previous.purpose != spec.purpose or previous.provider != spec.provider or previous.unit != Unit(**spec.unit.model_dump()):
                raise ServiceError(409, "price_reference_conflict", "历史价格的用途、提供方或单位不匹配")

    def create(self, *, spec, actor_id, idempotency_key):
        t = self._writer()
        spec = validate_spec(spec)
        encoded, key = _json(spec.model_dump()), _key(idempotency_key)
        previous = self.store.one("SELECT * FROM model_price_drafts WHERE environment=? AND creator_id=? AND create_key=?",
                                  (self.environment, actor_id, key))
        if previous:
            if previous["creation_json"] != encoded:
                raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
            return self._entry(previous), False
        self._references(spec)
        draft_id, ts = new_id("mpd"), self.clock()
        t.execute("INSERT INTO model_price_drafts(id,environment,revision,status,spec_json,creation_json,create_key,"
                  "creator_id,actor_id,created_at,updated_at) VALUES (?,?,1,'draft',?,?,?,?,?,?,?)",
                  (draft_id, self.environment, encoded, encoded, key, actor_id, actor_id, ts, ts))
        return self.get(draft_id), True

    def update(self, draft_id, *, spec, expected_revision, actor_id):
        t = self._writer()
        current = self.get(draft_id, expected_revision=_revision(expected_revision))
        if current["status"] != "draft":
            raise ServiceError(409, "price_published", "已发布价格不可修改，请复制为新版本")
        spec = validate_spec(spec)
        self._references(spec)
        if current["spec"] == spec.model_dump():
            return current, False
        t.execute("UPDATE model_price_drafts SET spec_json=?,revision=revision+1,actor_id=?,updated_at=? WHERE id=?",
                  (_json(spec.model_dump()), actor_id, self.clock(), draft_id))
        return self.get(draft_id), True

    def validate_publication(self, draft_id, revision):
        draft = self.get(draft_id, expected_revision=_revision(revision))
        if draft["status"] != "draft":
            raise ServiceError(409, "price_published", "已发布价格不可修改，请复制为新版本")
        spec = validate_spec(draft["spec"])
        self._references(spec)
        # Past effective dates are allowed as metadata, never as authority to reprice usage.
        return draft

    def publish(self, draft_id, *, expected_revision, actor_id, operation_id, reason):
        t = self._writer()
        draft = self.validate_publication(draft_id, expected_revision)
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        price_id, ts = new_id("price"), self.clock()
        snapshot = validate_spec(draft["spec"]).snapshot(price_id)
        # Exact same immutable billing_prices table and format as Ledger.publish_price.
        # This transaction also commits governance result/audit; it never sends a model call.
        unit = self.store.one("SELECT scale FROM billing_units WHERE code=?", (snapshot.unit.code,))
        if not unit:
            t.execute("INSERT INTO billing_units(code,scale) VALUES (?,?)", (snapshot.unit.code, snapshot.unit.scale))
        t.execute("INSERT INTO billing_prices(environment,price_id,snapshot,created_at) VALUES (?,?,?,?)",
                  (self.environment, price_id, _json(asdict(snapshot)), ts))
        t.execute("UPDATE model_price_drafts SET status='published',price_id=?,revision=revision+1,actor_id=?,updated_at=? WHERE id=?",
                  (price_id, actor_id, ts, draft_id))
        t.execute("INSERT INTO model_price_publications VALUES (?,?,?,?,?,?,?,?)",
                  (self.environment, price_id, draft_id, expected_revision, actor_id, operation_id, reason.strip(), ts))
        return self.get(draft_id)

    def published(self, price_id):
        row = self.store.one("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?", (self.environment, price_id))
        if not row:
            raise ServiceError(404, "price_not_found", "找不到该价格版本")
        return PriceSnapshot.from_dict(row["snapshot"] if isinstance(row["snapshot"], dict) else json.loads(row["snapshot"]))

    def calculate(self, draft_id, *, expected_revision, input_uncached, input_cached, output):
        draft = self.get(draft_id, expected_revision=_revision(expected_revision))
        if any(type(x) is not int or not 0 <= x <= 10**9 for x in (input_uncached, input_cached, output)):
            raise ServiceError(400, "price_invalid_usage", "试算用量必须是零至十亿之间的整数")
        spec = validate_spec(draft["spec"])
        price = spec.snapshot("calculation")
        exact = price.exact(Usage(input_uncached, input_cached, output))
        major = exact / 10 ** price.unit.scale
        with localcontext() as ctx:
            ctx.prec = 80
            display = format(Decimal(major.numerator) / Decimal(major.denominator), "f")
        return {"unit": asdict(price.unit), "exact": {"numerator": str(exact.numerator), "denominator": str(exact.denominator)},
                "display_amount": display, "charge_units_if_no_prior_remainder": str(exact.numerator // exact.denominator),
                "per_tokens": price.per_tokens}
