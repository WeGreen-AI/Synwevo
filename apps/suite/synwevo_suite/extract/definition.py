"""Bounded deterministic selectors over real parser segments, never a model or executable code."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError
from synwevo_core.errors import ServiceError


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Refusals are raised on the input they concern, with their own type, so the template editor can
# word each one next to that input (the operation layer returns `loc` and `type` per error).
class Rule(Strict):
    kind: Literal["prefix", "cell", "column"] = "prefix"
    prefix: str = Field(default="", max_length=120, validate_default=True)
    table: str = Field(default="", max_length=120)
    row: int = Field(default=1, ge=1, le=2000)
    column: int = Field(default=1, ge=1, le=100)
    header: str = Field(default="", max_length=120, validate_default=True)

    @field_validator("prefix")
    @classmethod
    def prefix_given(cls, value: str, info: ValidationInfo):
        if info.data.get("kind", "prefix") == "prefix" and not value.strip():
            raise PydanticCustomError("prefix_required", "prefix must be nonempty")
        return value

    @field_validator("header")
    @classmethod
    def header_given(cls, value: str, info: ValidationInfo):
        if info.data.get("kind") == "column" and not value.strip():
            raise PydanticCustomError("header_required", "column header must be nonempty")
        return value


class FieldSpec(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    type: Literal["text", "decimal", "date", "boolean"] = "text"
    required: bool = True
    many: bool = False
    review_required: bool = True
    rule: Rule
    max_length: int = Field(default=2000, ge=1, le=2000)
    minimum: str | None = Field(default=None, max_length=50, pattern=r"^-?[0-9]{1,30}(\.[0-9]{1,12})?$")
    maximum: str | None = Field(default=None, max_length=50, pattern=r"^-?[0-9]{1,30}(\.[0-9]{1,12})?$")

    @field_validator("maximum")
    @classmethod
    def bounds(cls, value: str | None, info: ValidationInfo):
        minimum = info.data.get("minimum")       # absent when the minimum itself was refused
        if value is not None and minimum is not None and Decimal(minimum) > Decimal(value):
            raise PydanticCustomError("bounds_order", "maximum is below minimum")
        return value


class Definition(Strict):
    fields: list[FieldSpec] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique(self):
        if len({f.id for f in self.fields}) != len(self.fields):
            raise ValueError("duplicate field id")
        return self


def value_error(field: FieldSpec, value: str) -> str | None:
    if len(value) > field.max_length:
        return "too_long"
    if not value:
        return "required" if field.required else None
    if field.type == "decimal":
        # Fixed decimals only; preserve exact input, including leading zeroes. No exponents/NaN.
        import re
        if not re.fullmatch(r"-?[0-9]{1,30}(\.[0-9]{1,12})?", value):
            return "invalid_decimal"
        try:
            number = Decimal(value)
            if field.minimum is not None and number < Decimal(field.minimum):
                return "below_minimum"
            if field.maximum is not None and number > Decimal(field.maximum):
                return "above_maximum"
        except InvalidOperation:
            return "invalid_decimal"
    if field.type == "date":
        try:
            if date.fromisoformat(value).isoformat() != value:
                return "invalid_date"
        except ValueError:
            return "invalid_date"
    if field.type == "boolean" and value not in ("true", "false"):
        return "invalid_boolean"
    return None


def normalize(raw: dict, description: dict, *, ocr: bool) -> dict:
    """DocumentIngest protocol. Conservatively reject its known truncation ceilings.

    Its text adapter caps a line at 500 characters without a flag. A line at that ceiling is
    therefore rejected; accepting it could turn a cut amount into a plausible smaller amount.
    Page/line and table/cell locations are supplied by the parser; no boxes are fabricated.
    """
    status = raw.get("status")
    # The reader found a structurally damaged file (truncated, objects missing). Whatever lines it
    # still read are not the document; reviewing them would pass a partial file off as whole.
    if raw.get("damaged"):
        raise ServiceError(422, "damaged_file", "文件已损坏或不完整，请用生成它的程序重新导出后上传")
    if status == "needs_ocr":
        raise ServiceError(422, "ocr_required", "该文件需要文字识别，请启用已配置的识别服务")
    if status != "ready":
        detail = str(raw.get("error", "")).lower()
        code = "encrypted_file" if any(x in detail for x in ("password", "encrypted", "密码", "加密")) else "parse_failed"
        raise ServiceError(422, code, "文件无法解析，请检查格式、加密状态或重新导出")
    lines, tables = raw.get("lines", []), raw.get("tables", [])
    limits = description.get("limits", {})
    pages = raw.get("pages") or 0
    if (raw.get("lines_truncated") or len(lines) >= 5000 or len(tables) >= 50
            or pages > min(200, limits.get("text_pages", 200))
            or (ocr and pages > min(20, limits.get("ocr_pages", 20)))
            or any(t.get("truncated") for t in tables)
            or any(len(l.get("text", "")) >= 500 for l in lines)):
        raise ServiceError(413, "parse_limit", "解析结果不完整，请拆分文件后重试")
    # This adapter reports scanned pages in notes but cannot prove all were read on partial OCR.
    scan_notes = any("没有文字层" in n for n in raw.get("notes", []))
    if scan_notes and not ocr:
        raise ServiceError(422, "ocr_required", "文件含扫描页，需要完整识别后才能提取")
    if ocr and not raw.get("ocr", {}).get("applied") and (raw.get("family") == "image" or scan_notes):
        raise ServiceError(422, "ocr_incomplete", "文字识别未完整完成")
    segments = []
    for line in lines:
        source = line.get("source") or {}
        text = line.get("text", "")
        located = isinstance(source.get("page"), int) and source["page"] > 0 and isinstance(source.get("line"), int) and source["line"] > 0
        segments.append({"id": f"s{len(segments)}", "text": text, "source": source, "located": located, "kind": "line"})
    for table in tables:
        rows = table.get("rows", [])
        nums = table.get("row_numbers", [])
        if len(rows) > 2000 or any(len(r) > 100 for r in rows):
            raise ServiceError(413, "parse_limit", "表格超过提取上限")
        for ri, row in enumerate(rows):
            for ci, text in enumerate(row):
                if len(str(text)) > 2000:
                    raise ServiceError(413, "parse_limit", "单元格超过提取上限")
                source = {**(table.get("source") or {}), "table": table.get("name", ""),
                          "row": nums[ri] if ri < len(nums) else None, "column": ci + 1}
                segments.append({"id": f"s{len(segments)}", "text": str(text), "source": source,
                                 "located": bool(source["table"]) and isinstance(source["row"], int),
                                 "kind": "cell", "table_row": ri + 1})
    if len(segments) > 10000 or sum(len(s["text"]) for s in segments) > 150000:
        raise ServiceError(413, "parse_limit", "解析内容超过提取上限")
    return {"engine": str(raw.get("engine", "unknown"))[:80], "algorithm": "literal-selectors-v1",
            "format": raw.get("format"), "pages": pages, "ocr_applied": bool(raw.get("ocr", {}).get("applied")),
            "segments": segments}


#: Matches one field may hold. Beyond it the field is left unread with its counts (`over_limit`);
#: the other fields are still read and the task still reaches review.
MAX_MATCHES = 100


def extract(definition: Definition, parsed: dict) -> dict:
    """Every match of a rule is accounted for. A line that starts with the label but has nothing
    after it is an unresolved occurrence (text recognition often reads a column of stacked labels
    apart from their values), never silently skipped: the field is `incomplete` and needs a
    person. The value is not taken from the next line — the line order recognition produces does
    not prove which value belongs to which label. `occurrences` counts labelled matches against
    values located, so a reviewer sees 2 of 38 rather than a confident partial list."""
    results = {}
    segments = parsed["segments"]
    for field in definition.fields:
        rule, found, unresolved, labelled, located = field.rule, [], [], 0, 0
        headers = [s for s in segments if s["kind"] == "cell" and s["table_row"] == 1
                   and s["text"] == rule.header and (not rule.table or s["source"]["table"] == rule.table)]
        for seg in segments:
            start = 0
            if rule.kind == "prefix":
                if seg["kind"] != "line" or not seg["text"].startswith(rule.prefix):
                    continue
                start = len(rule.prefix)
            else:
                src = seg["source"]
                if seg["kind"] != "cell" or (rule.table and src["table"] != rule.table):
                    continue
                if rule.kind == "cell" and (seg["table_row"] != rule.row or src["column"] != rule.column):
                    continue
                if rule.kind == "column" and not any(src["table"] == h["source"]["table"] and
                        src["column"] == h["source"]["column"] and seg["table_row"] > 1 for h in headers):
                    continue
            text = seg["text"][start:]
            start += len(text) - len(text.lstrip())
            value = text.strip()
            labelled += 1
            located += bool(value)
            if labelled > MAX_MATCHES:
                continue                    # only counted: the field is left unread below
            if not value:
                # An empty cell is a cell read as empty; a label with nothing after it is not a reading.
                if rule.kind == "prefix":
                    unresolved.append({"reason": "no_value_after_label", "ocr": bool(seg["source"].get("ocr")),
                                       "evidence": {"segment_id": seg["id"], "start": start, "end": start,
                                                    "source": seg["source"]}})
                continue
            found.append({"value": value, "evidence": {"segment_id": seg["id"], "start": start,
                          "end": start + len(value), "source": seg["source"]},
                          "verified": seg["located"], "validation": value_error(field, value),
                          "ocr": bool(seg["source"].get("ocr"))})
        occurrences = {"labelled": labelled, "located": located}
        if labelled > MAX_MATCHES:
            results[field.id] = {"value": [] if field.many else None, "status": "over_limit", "candidates": [],
                                 "unresolved": [], "occurrences": occurrences, "limit": MAX_MATCHES,
                                 "requires_review": True, "origin": "deterministic_rule"}
            continue
        status = "extracted"
        if unresolved:
            status = "incomplete"
        elif not found:
            status = "missing"
        elif not field.many and len(found) > 1:
            status = "ambiguous" if len({x["value"] for x in found}) == 1 else "conflict"
        elif any(x["validation"] for x in found):
            status = "invalid"
        elif not all(x["verified"] for x in found):
            status = "unverified"
        value = [x["value"] for x in found] if field.many else (found[0]["value"] if len(found) == 1 else None)
        results[field.id] = {"value": value, "status": status, "candidates": found, "unresolved": unresolved,
                             "occurrences": occurrences,
                             "requires_review": field.review_required or status != "extracted" or any(x["ocr"] for x in found),
                             "origin": "deterministic_rule"}
    import json
    if len(json.dumps(results, ensure_ascii=False).encode()) > 200000:
        raise ServiceError(413, "result_limit", "提取结果过大，请缩小规则范围")
    return results
