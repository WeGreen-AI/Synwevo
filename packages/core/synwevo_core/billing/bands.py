"""Governed peak/off-peak schedule and the band a model call is charged in.

DeepSeek publishes two tariffs: peak (Monday-Friday, 01:00-04:00 and 06:00-10:00 UTC) and
off-peak (all other time, weekends and Chinese public holidays). The peak windows and the
holiday calendar are data here: versioned, immutable once published, appended only inside
the caller's Store transaction together with its audit and operation result (the Admin layer
authorizes). Nothing is seeded into the database; ``PUBLISHED_PEAK_WINDOWS`` is the
provider's published starting point that an administrator reviews and publishes.

The band of a call is decided once, from its dispatch instant (UTC), by the ledger's dispatch
transaction (``pin_band``), and stored on the attempt with the schedule revision used. A later
schedule change never re-prices a dispatched call.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ..errors import ServiceError
from .models import BillingError, PriceSnapshot, identifier

#: The schedule a time-banded DeepSeek snapshot refers to.
SCHEDULE_ID = "deepseek"
#: China observes no daylight saving time: public holidays are dates in UTC+8.
CHINA_OFFSET_MINUTES = 480
#: DeepSeek's published peak windows (api-docs.deepseek.com pricing, checked 2026-10-05).
#: Only an editor's starting values: a band applies after an administrator publishes it.
PUBLISHED_PEAK_WINDOWS = (
    {"weekdays": [1, 2, 3, 4, 5], "start": "01:00", "end": "04:00"},
    {"weekdays": [1, 2, 3, 4, 5], "start": "06:00", "end": "10:00"},
)
PUBLISHED_SOURCE_REF = "api-docs.deepseek.com/quick_start/pricing:2026-10-05"
MAX_HOLIDAYS = 1000


def _minutes(value: str) -> int:
    hours, minutes = int(value[:2]), int(value[3:])
    return hours * 60 + minutes


class PeakWindow(BaseModel):
    """A UTC interval [start, end) on the listed ISO weekdays (1 = Monday ... 7 = Sunday)."""
    model_config = ConfigDict(extra="forbid", strict=True)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    end: str = Field(pattern=r"^(([01][0-9]|2[0-3]):[0-5][0-9]|24:00)$")

    @field_validator("weekdays")
    @classmethod
    def ordered_days(cls, value):
        if any(type(day) is not int or not 1 <= day <= 7 for day in value) or value != sorted(set(value)):
            raise ValueError("weekdays must be distinct ascending ISO days")
        return value

    @model_validator(mode="after")
    def forward(self):
        if _minutes(self.start) >= _minutes(self.end):
            raise ValueError("a window ends after it starts, within one UTC day")
        return self


class Holiday(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    name: str = Field(min_length=1, max_length=40)

    @field_validator("date")
    @classmethod
    def real_date(cls, value):
        date.fromisoformat(value)  # 2026-02-30 is refused, not normalised.
        return value

    @field_validator("name")
    @classmethod
    def plain_name(cls, value):
        if not value.strip() or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid holiday name")
        return value


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    peak_windows: list[PeakWindow] = Field(min_length=1, max_length=14)
    holiday_utc_offset_minutes: int = Field(ge=-720, le=840)
    holidays: list[Holiday] = Field(max_length=MAX_HOLIDAYS)
    source_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")

    @model_validator(mode="after")
    def distinct(self):
        windows = [(tuple(w.weekdays), w.start, w.end) for w in self.peak_windows]
        if len(set(windows)) != len(windows):
            raise ValueError("duplicate window")
        dates = [h.date for h in self.holidays]
        if dates != sorted(set(dates)):
            raise ValueError("holidays must be distinct ascending dates")
        return self


def published_defaults() -> dict:
    """The provider's published windows for the editor; holidays are entered by an administrator."""
    return {"peak_windows": [dict(w, weekdays=list(w["weekdays"])) for w in PUBLISHED_PEAK_WINDOWS],
            "holiday_utc_offset_minutes": CHINA_OFFSET_MINUTES, "holidays": [], "source_ref": PUBLISHED_SOURCE_REF}


def validate_schedule(value) -> ScheduleSpec:
    try:
        return ScheduleSpec.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        # Submitted values are not echoed.
        raise ServiceError(400, "band_schedule_invalid", "高峰时段或节假日字段无效，请检查星期、时间与日期") from None


def band_at(spec: ScheduleSpec, at: int) -> str:
    """'peak' or 'off_peak' for an instant in Unix seconds. Holidays win over peak windows."""
    if type(at) is not int or at < 0:
        raise BillingError("invalid_integer")
    moment = datetime.fromtimestamp(at, timezone.utc)
    local = (moment + timedelta(minutes=spec.holiday_utc_offset_minutes)).date().isoformat()
    if any(holiday.date == local for holiday in spec.holidays):
        return "off_peak"
    minute, weekday = moment.hour * 60 + moment.minute, moment.isoweekday()
    for window in spec.peak_windows:
        if weekday in window.weekdays and _minutes(window.start) <= minute < _minutes(window.end):
            return "peak"
    return "off_peak"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _revision(value):
    if type(value) is not int or not 0 <= value < 2**63 - 1:
        raise ServiceError(400, "band_schedule_bad_revision", "时段日历版本无效，请重新读取")
    return value


class BandSchedules:
    """Data service for one environment's schedule; the Admin layer authorizes and audits."""

    def __init__(self, store, *, environment, schedule_id=SCHEDULE_ID, clock=None):
        if environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")
        self.store, self.environment, self.schedule_id = store, environment, identifier(schedule_id)
        self.clock = clock or (lambda: int(time.time()))

    def _record(self, row):
        empty = {"environment": self.environment, "schedule_id": self.schedule_id, "revision": 0, "spec": None,
                 "actor_id": None, "created_at": None, "reason": None, "operation_id": None}
        if row is None:
            return empty
        try:
            spec = ScheduleSpec.model_validate_json(row["spec_json"]).model_dump()
        except (ValidationError, TypeError, ValueError):
            raise ServiceError(503, "band_schedule_unavailable", "时段日历记录暂不可用，请联系管理员检查") from None
        return {**empty, "spec": spec, **{key: row[key] for key in
                ("revision", "actor_id", "created_at", "reason", "operation_id")}}

    def read(self):
        return self._record(self.store.one(
            "SELECT * FROM billing_band_schedules WHERE environment=? AND schedule_id=? ORDER BY revision DESC LIMIT 1",
            (self.environment, self.schedule_id)))

    def history(self, *, before_revision=None, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError(400, "band_schedule_bad_page", "时段日历历史分页参数无效")
        clause, args = (" AND revision<?", (_revision(before_revision),)) if before_revision is not None else ("", ())
        rows = self.store.all("SELECT * FROM billing_band_schedules WHERE environment=? AND schedule_id=?" + clause +
                              " ORDER BY revision DESC LIMIT ?", (self.environment, self.schedule_id, *args, limit + 1))
        items = [self._record(row) for row in rows[:limit]]
        return {"items": items, "next_before_revision": items[-1]["revision"] if len(rows) > limit else None}

    def preview(self, spec, *, expected_revision):
        expected_revision = _revision(expected_revision)
        spec = validate_schedule(spec)
        before = self.read()
        if before["revision"] != expected_revision:
            raise ServiceError(409, "band_schedule_revision_conflict", "时段日历已变化，请保留输入并重新读取", {"current": before})
        after = spec.model_dump()
        if before["spec"] == after:
            raise ServiceError(409, "band_schedule_no_change", "时段日历未发生变化，无需提交")
        old = {h["date"]: h["name"] for h in (before["spec"] or {}).get("holidays", [])}
        new = {h["date"]: h["name"] for h in after["holidays"]}
        changes = {"holidays_added": sorted(set(new) - set(old)), "holidays_removed": sorted(set(old) - set(new)),
                   "holidays_renamed": sorted(d for d in set(old) & set(new) if old[d] != new[d]),
                   "windows_changed": (before["spec"] or {}).get("peak_windows") != after["peak_windows"]
                                      or (before["spec"] or {}).get("holiday_utc_offset_minutes") != after["holiday_utc_offset_minutes"]}
        return {"before": before, "after": after, "changes": changes}

    def append(self, spec, *, expected_revision, actor_id, reason, operation_id):
        with self.store.conn() as transaction:
            if not transaction.in_transaction or not getattr(self.store._local, "depth", 0):
                raise RuntimeError("band schedule append requires an existing Store.tx()")
            for value in (actor_id, operation_id):
                try:
                    identifier(value)
                except BillingError:
                    raise ServiceError(400, "band_schedule_invalid_metadata", "时段日历操作信息无效") from None
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            plan = self.preview(spec, expected_revision=expected_revision)
            transaction.execute(
                "INSERT INTO billing_band_schedules(environment,schedule_id,revision,spec_json,actor_id,reason,operation_id,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)", (self.environment, self.schedule_id, expected_revision + 1, _json(plan["after"]),
                                             actor_id, reason.strip(), operation_id, self.clock()))
            return {**self.read(), "changes": plan["changes"]}


def current_schedule(t, environment, schedule_id):
    """(revision, spec) currently published, read on the caller's connection; None if none."""
    row = t.execute("SELECT revision,spec_json FROM billing_band_schedules WHERE environment=? AND schedule_id=? "
                    "ORDER BY revision DESC LIMIT 1", (environment, schedule_id)).fetchone()
    if row is None:
        return None
    try:
        return row[0], ScheduleSpec.model_validate_json(row[1])
    except (ValidationError, TypeError, ValueError):
        raise BillingError("price_band_schedule_unavailable") from None


def _snapshot(t, environment, price_id):
    row = t.execute("SELECT snapshot FROM billing_prices WHERE environment=? AND price_id=?",
                    (environment, price_id)).fetchone()
    if row is None:
        raise BillingError("price_not_published")
    return PriceSnapshot.from_dict(json.loads(row[0]))


def pin_band(t, environment, cost_price_id, sale_price_id, at):
    """Band and schedule revision for a dispatch at ``at``, inside the dispatch transaction.

    Flat cost and sale tariffs pin ('flat', None). A time-banded tariff without a readable,
    published schedule refuses the dispatch: guessing a band would misstate cost or charge.
    Both tariffs of one call must refer to the same schedule; one band applies to both.
    """
    schedules = {p.band_schedule for p in (_snapshot(t, environment, cost_price_id),
                                           _snapshot(t, environment, sale_price_id)) if p.banded}
    if not schedules:
        return "flat", None
    if len(schedules) != 1:
        raise BillingError("price_band_schedule_mismatch")
    current = current_schedule(t, environment, schedules.pop())
    if current is None:
        raise BillingError("price_band_schedule_unavailable")
    revision, spec = current
    return band_at(spec, at), revision


def schedule_revision_for(store, environment, prices):
    """Revision a route's banded prices currently depend on (None when all are flat)."""
    schedules = {p.band_schedule for p in prices if p.banded}
    if not schedules:
        return None
    if len(schedules) != 1:
        raise BillingError("price_band_schedule_mismatch")
    with store.conn() as c:
        current = current_schedule(c, environment, schedules.pop())
    if current is None:
        raise BillingError("price_band_schedule_unavailable")
    return current[0]


def valid_identifier(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", value) is not None
