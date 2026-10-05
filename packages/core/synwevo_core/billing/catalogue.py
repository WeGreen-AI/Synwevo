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
from .bands import SCHEDULE_ID, current_schedule
from .models import BANDS, RATE_KEYS, BandRates, BillingError, MarkupBasis, PriceSnapshot, Rate, Unit, Usage

DECIMAL = r"^(0|[1-9][0-9]{0,8})(\.[0-9]{1,9})?$"


class PriceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,15}$")
    scale: int = Field(ge=0, le=12)


def _plain_name(value):
    if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid name")
    return value.strip()


class BandRateSpec(BaseModel):
    """Off-peak rates, in major units per million tokens; never above the peak rates."""
    model_config = ConfigDict(extra="forbid", strict=True)
    input_uncached: str = Field(pattern=DECIMAL)
    input_cached: str = Field(pattern=DECIMAL)
    output: str = Field(pattern=DECIMAL)


class PriceSpec(BaseModel):
    """A flat tariff, or (with ``off_peak``) a time-banded one whose three base rates are PEAK."""
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    purpose: Literal["sale", "cost"]
    unit: PriceUnit
    provider: Literal["deepseek"]
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    input_uncached: str = Field(pattern=DECIMAL)
    input_cached: str = Field(pattern=DECIMAL)
    output: str = Field(pattern=DECIMAL)
    terms_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    effective_from: int = Field(ge=0, le=253402300799)
    previous_price_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    off_peak: BandRateSpec | None = None

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return _plain_name(value)

    def dump(self):
        """Stored form. A flat spec keeps exactly the pre-band keys, so an idempotent create
        replayed across an upgrade still matches and a release without bands can read it."""
        data = self.model_dump()
        if data["off_peak"] is None:
            del data["off_peak"]
        return data

    def snapshot(self, price_id):
        def rate(value):
            exact = Fraction(Decimal(value)) * 10 ** self.unit.scale
            return Rate(exact.numerator, exact.denominator)
        off_peak = (BandRates(*(rate(getattr(self.off_peak, key)) for key in RATE_KEYS))
                    if self.off_peak is not None else None)
        return PriceSnapshot(price_id, self.purpose, Unit(**self.unit.model_dump()), self.provider, self.model,
            rate(self.input_uncached), rate(self.input_cached), rate(self.output), self.terms_ref, self.effective_from,
            off_peak=off_peak, band_schedule=SCHEDULE_ID if off_peak is not None else None)


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
    except (ValidationError, ValueError, TypeError):
        # No submitted values (which might include secrets) escape through validation detail.
        raise ServiceError(400, "price_invalid", "价格字段无效，请检查单位、费率和生效时间") from None
    if spec.off_peak is not None and any(Decimal(getattr(spec.off_peak, key)) > Decimal(getattr(spec, key))
                                         for key in RATE_KEYS):
        raise ServiceError(400, "price_off_peak_above_peak", "低谷费率不能高于高峰费率")
    try:
        spec.snapshot("validation")  # Also enforce ledger integer/rational bounds.
    except (BillingError, ValueError, TypeError):
        raise ServiceError(400, "price_invalid", "价格字段无效，请检查单位、费率和生效时间") from None
    return spec


def _display(amount: Fraction, scale: int) -> str:
    """Exact decimal string of smallest units in major units (decimal inputs always terminate)."""
    major = amount / 10 ** scale
    with localcontext() as ctx:
        ctx.prec = 80
        value = (Decimal(major.numerator) / Decimal(major.denominator)).normalize()
        return format(value, "f")


def _amount(price: PriceSnapshot, usage: Usage, band=None) -> dict:
    exact = price.exact(usage, band)
    return {"exact": {"numerator": str(exact.numerator), "denominator": str(exact.denominator)},
            "display_amount": _display(exact, price.unit.scale) if exact else "0",
            "charge_units_if_no_prior_remainder": str(exact.numerator // exact.denominator)}


def rate_table(price: PriceSnapshot) -> dict:
    """Major-unit rates per million tokens, by band ('flat' for a single-rate tariff)."""
    bands = BANDS if price.banded else ("flat",)
    return {band: {key: _display(rate.fraction, price.unit.scale) if rate.numerator else "0"
                   for key, rate in price.rates(None if band == "flat" else band).items()} for band in bands}


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
        # A flat spec is stored without ``off_peak`` (PriceSpec.dump); every reader sees it as null.
        return {key: row[key] for key in ("id", "revision", "status", "price_id", "actor_id", "created_at", "updated_at")} | {
            "spec": {"off_peak": None, **json.loads(row["spec_json"])}}

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
        encoded, key = _json(spec.dump()), _key(idempotency_key)
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
                  (_json(spec.dump()), actor_id, self.clock(), draft_id))
        return self.get(draft_id), True

    def validate_publication(self, draft_id, revision):
        draft = self.get(draft_id, expected_revision=_revision(revision))
        if draft["status"] != "draft":
            raise ServiceError(409, "price_published", "已发布价格不可修改，请复制为新版本")
        spec = validate_spec(draft["spec"])
        self._references(spec)
        if spec.off_peak is not None:
            with self.store.conn() as c:
                if current_schedule(c, self.environment, SCHEDULE_ID) is None:
                    raise ServiceError(409, "price_band_schedule_required", "请先发布高峰时段与节假日日历，再发布分时价格")
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
                  (self.environment, price_id, _json(snapshot.to_dict()), ts))
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
        usage = Usage(input_uncached, input_cached, output)
        exact = price.exact(usage, "peak")
        major = exact / 10 ** price.unit.scale
        with localcontext() as ctx:
            ctx.prec = 80
            display = format(Decimal(major.numerator) / Decimal(major.denominator), "f")
        # The top-level amount is the flat or peak (highest) amount; a banded draft adds both bands.
        return {"unit": asdict(price.unit), "exact": {"numerator": str(exact.numerator), "denominator": str(exact.denominator)},
                "display_amount": display, "charge_units_if_no_prior_remainder": str(exact.numerator // exact.denominator),
                "per_tokens": price.per_tokens,
                "bands": {band: _amount(price, usage, band) for band in BANDS} if price.banded else None}


MARKUP = r"^(100|[1-9][0-9]?)(\.[0-9]{1,4})?$"
EXAMPLE_TOKENS = 1000


class MarkupSpec(BaseModel):
    """Sale price of one route = a published cost price x an administrator's markup.

    ``markup`` applies to flat and peak rates; ``off_peak_markup`` (optional, banded costs only)
    applies to off-peak rates, otherwise the same markup does. Markups are 1-100 with at most
    four decimals: selling below the provider's cost is refused, not a typo away.
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    cost_price_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
    markup: str = Field(pattern=MARKUP)
    off_peak_markup: str | None = Field(default=None, pattern=MARKUP)
    terms_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
    effective_from: int = Field(ge=0, le=253402300799)

    @field_validator("name")
    @classmethod
    def name_text(cls, value):
        return _plain_name(value)

    @field_validator("markup", "off_peak_markup")
    @classmethod
    def bounded_markup(cls, value):
        if value is not None and not 1 <= Decimal(value) <= 100:
            raise ValueError("invalid markup")
        return value


def validate_markup(value) -> MarkupSpec:
    try:
        return MarkupSpec.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise ServiceError(400, "price_markup_invalid", "加价字段无效：倍率须为 1 至 100、最多四位小数，并填写条款引用与生效时间") from None


class PriceMarkups:
    """Publish sale snapshots derived from cost snapshots; the Admin layer authorizes and audits.

    Each publication is an immutable sale price version (in ``billing_prices``) plus its
    provenance. It changes no route: a route adopts the new sale price through model
    configuration, which in turn invalidates quotes and consents bound to the old route.
    """

    def __init__(self, store, *, environment, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment = store, environment
        self.clock = clock or (lambda: int(time.time()))
        self.catalogue = PriceCatalogue(store, environment=environment, clock=self.clock)

    def derive(self, spec, price_id="preview"):
        spec = validate_markup(spec)
        cost = self.catalogue.published(spec.cost_price_id)
        if cost.purpose != "cost":
            raise ServiceError(409, "price_markup_needs_cost", "请选择已发布的成本价作为加价基础")
        if spec.off_peak_markup is not None and not cost.banded:
            raise ServiceError(409, "price_markup_band_unavailable", "该成本价没有分时费率，不能单独设置低谷倍率")
        peak, off_peak = Fraction(Decimal(spec.markup)), Fraction(Decimal(spec.off_peak_markup or spec.markup))

        def times(rate, factor):
            exact = rate.fraction * factor
            if exact.denominator > 10**9 or exact.numerator > 2**63 - 1:
                raise ServiceError(400, "price_markup_precision", "加价后的费率超出可精确记账的范围，请减少倍率小数位")
            return Rate(exact.numerator, exact.denominator)
        try:
            return spec, cost, PriceSnapshot(
                price_id, "sale", cost.unit, cost.provider, cost.model,
                *(times(getattr(cost, key), peak) for key in RATE_KEYS), spec.terms_ref, spec.effective_from,
                per_tokens=cost.per_tokens,
                off_peak=BandRates(*(times(getattr(cost.off_peak, key), off_peak) for key in RATE_KEYS)) if cost.banded else None,
                band_schedule=cost.band_schedule, basis=MarkupBasis(spec.cost_price_id, spec.markup, spec.off_peak_markup))
        except BillingError:
            raise ServiceError(400, "price_markup_off_peak_above_peak", "低谷售价不能高于高峰售价，请调整低谷倍率") from None

    def latest(self, model):
        row = self.store.one("SELECT price_id FROM model_price_markups WHERE environment=? AND model=? "
                             "ORDER BY created_at DESC, rowid DESC LIMIT 1", (self.environment, model))
        return row["price_id"] if row else None

    def preview(self, spec):
        spec, cost, sale = self.derive(spec)
        examples = {}
        for band in (BANDS if cost.banded else ("flat",)):
            pinned = None if band == "flat" else band
            examples[band] = {key: {"cost": _display(getattr(cost.rates(pinned)[key], "fraction") * EXAMPLE_TOKENS / cost.per_tokens, cost.unit.scale),
                                    "sale": _display(getattr(sale.rates(pinned)[key], "fraction") * EXAMPLE_TOKENS / sale.per_tokens, sale.unit.scale)}
                              for key in RATE_KEYS}
        return {"spec": spec.model_dump(), "model": cost.model, "unit": asdict(cost.unit),
                "cost_rates": rate_table(cost), "sale_rates": rate_table(sale), "example_tokens": EXAMPLE_TOKENS,
                "examples": examples, "replaces_price_id": self.latest(cost.model)}

    def publish(self, spec, *, actor_id, operation_id, reason):
        t = self.catalogue._writer()  # Requires the caller's Store.tx() (audit + operation result).
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        price_id, ts = new_id("price"), self.clock()
        spec, cost, sale = self.derive(spec, price_id)
        t.execute("INSERT INTO billing_prices(environment,price_id,snapshot,created_at) VALUES (?,?,?,?)",
                  (self.environment, price_id, _json(sale.to_dict()), ts))
        t.execute("INSERT INTO model_price_markups(environment,price_id,model,cost_price_id,spec_json,actor_id,"
                  "operation_id,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (self.environment, price_id, cost.model, spec.cost_price_id, _json(spec.model_dump()), actor_id,
                   operation_id, reason.strip(), ts))
        return self.get(price_id)

    def _entry(self, row):
        sale = self.catalogue.published(row["price_id"])
        return {"price_id": row["price_id"], "model": row["model"], "spec": json.loads(row["spec_json"]),
                "unit": asdict(sale.unit), "sale_rates": rate_table(sale), "actor_id": row["actor_id"],
                "reason": row["reason"], "operation_id": row["operation_id"], "created_at": row["created_at"]}

    def get(self, price_id):
        row = self.store.one("SELECT * FROM model_price_markups WHERE environment=? AND price_id=?",
                             (self.environment, price_id))
        if not row:
            raise ServiceError(404, "price_not_found", "找不到该价格版本")
        return self._entry(row)

    def list(self, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError(400, "price_bad_page", "价格列表分页参数无效")
        rows = self.store.all("SELECT * FROM model_price_markups WHERE environment=? ORDER BY created_at DESC, rowid DESC "
                              "LIMIT ?", (self.environment, limit))
        return {"environment": self.environment, "items": [self._entry(row) for row in rows]}
