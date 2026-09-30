"""Source selection operations for the machine API, using the browser's services.

Integration inside ``v1.build_router``, before returning its prefixed router::

    v1_sources.register(r, app_, views, scope, attachment, auth)

``scope(request, needed)`` must be the existing machine gate (including delegated
export policy). No credentials, sessions, persistence or parsing engines live here.
Lists use bounded offset pages over the current source/rule, not frozen snapshots.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Generic, Literal, TypeVar
from urllib.parse import urlencode

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator, model_serializer
from starlette.concurrency import run_in_threadpool

from synwevo_core.errors import ServiceError, not_found

MAX_BODY_BYTES = 262144
MAX_PAGE = 200
MAX_OFFSET = 1000000
MAX_SEARCH_OFFSET = 10000
ID = Annotated[str, Field(min_length=1, max_length=128)]
FileID = Annotated[str, Path(min_length=1, max_length=128)]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
BlockIndex = Annotated[int | None, Query(ge=0, le=4999)]
Mode = Literal["per_row", "per_file", "per_group", "per_block"]
# scanning → (parsing → ready | needs_ocr | error | unsupported) | rejected | scan_failed;
# the malware check precedes parsing (docs/security/file-acceptance.md).
Status = Literal["scanning", "parsing", "ready", "needs_ocr", "error", "unsupported", "rejected", "scan_failed"]
SelectionIndex = Annotated[int, Field(ge=0, le=1048575)]
SelectionColumn = Annotated[int, Field(ge=0, le=16383)]
TextOffset = Annotated[int, Field(ge=0, le=20000000)]
DocPath = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[0-9]+(?::[0-9]+:[0-9]+:[0-9]+)*$")]
PartName = Annotated[str, Field(min_length=1, max_length=255)]
UnitCoordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CSVOptions(StrictInput):
    encoding: Literal["auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "big5",
                      "utf-16", "shift_jis", "latin-1"] = "auto"
    delimiter: Literal["auto", ",", "\t", "\\t", ";", "|"] = "auto"
    quotechar: Literal['"', "'"] = '"'


class SourceOptions(StrictInput):
    csv: CSVOptions


class SourceCorrection(StrictInput):
    value: Annotated[str, Field(max_length=20000)] | None
    confirmed: bool = True


class SourceOcr(StrictInput):
    # Zero-based pages of a PDF; omitted = every page the file's structure marks as scanned or mixed.
    # The service refuses repeats beyond the file, and more than 50 pages per call.
    pages: Annotated[list[Annotated[int, Field(ge=0, le=1000000)]], Field(max_length=1000)] | None = None


class SourceOrder(StrictInput):
    ids: Annotated[list[ID], Field(max_length=10000)]


def _ordered_cells(r1, c1, r2, c2):
    if r1 > r2 or c1 > c2:
        raise ValueError("选区终点不能在起点之前")
    if (r2 - r1 + 1) * (c2 - c1 + 1) > 100000:
        raise ValueError("一次最多选择 100000 个单元格")


class CellSelection(StrictInput):
    kind: Literal["cells"]
    sheet: Annotated[str, Field(min_length=1, max_length=255)]
    r1: SelectionIndex
    c1: SelectionColumn
    r2: SelectionIndex
    c2: SelectionColumn
    to_end: bool = False

    @model_validator(mode="after")
    def bounds(self):
        _ordered_cells(self.r1, self.c1, self.r2, self.c2)
        return self


class TextSelection(StrictInput):
    start: TextOffset
    end: TextOffset
    text: Annotated[str, Field(max_length=20000)] | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.start >= self.end:
            raise ValueError("文字选区必须非空且 start < end")
        return self


class DocTextSelection(TextSelection):
    kind: Literal["docx_text"]
    part: PartName | None = None
    path: DocPath


class DocCellSelection(StrictInput):
    kind: Literal["docx_cell"]
    part: PartName | None = None
    table_path: DocPath
    row: SelectionIndex
    col: SelectionColumn
    text: Annotated[str, Field(max_length=20000)] | None = None


class DocTableSelection(StrictInput):
    kind: Literal["docx_table"]
    part: PartName | None = None
    table_path: DocPath
    header_rows: SelectionIndex = 1
    r1: SelectionIndex | None = None
    c1: SelectionColumn | None = None
    r2: SelectionIndex | None = None
    c2: SelectionColumn | None = None

    @model_validator(mode="after")
    def bounds(self):
        present = sum(v is not None for v in (self.r1, self.c1, self.r2, self.c2))
        if present not in (0, 4):
            raise ValueError("表格固定区域必须同时指定 r1/c1/r2/c2")
        if present:
            _ordered_cells(self.r1, self.c1, self.r2, self.c2)
        return self


class PDFSelection(StrictInput):
    kind: Literal["pdf_region"]
    page: SelectionIndex
    nrect: Annotated[list[UnitCoordinate], Field(min_length=4, max_length=4)]

    @model_validator(mode="after")
    def bounds(self):
        x0, y0, x1, y1 = self.nrect
        if x0 >= x1 or y0 >= y1:
            raise ValueError("nrect 必须有正的宽度和高度")
        return self


class PlainTextSelection(TextSelection):
    kind: Literal["text_span"]
    line: SelectionIndex


class SlideTextSelection(TextSelection):
    kind: Literal["pptx_text"]
    slide: SelectionIndex
    shape: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")]
    para: SelectionIndex = 0


class SlideCellSelection(StrictInput):
    kind: Literal["pptx_cell"]
    slide: SelectionIndex
    shape: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[0-9]+$")]
    row: SelectionIndex
    col: SelectionColumn


class SourceLocatorInput(StrictInput):
    project_id: ID | None = None
    block: Annotated[int, Field(ge=0, le=4999)] | None = None
    selection: Annotated[CellSelection | DocTextSelection | DocCellSelection | DocTableSelection |
                         PDFSelection | PlainTextSelection | SlideTextSelection | SlideCellSelection,
                         Field(discriminator="kind")]


class LocatorProposal(BaseModel):
    label: str
    varies: Literal["per_file", "per_record", "fixed"]
    locator: dict[str, JsonValue]
    sample: JsonValue = None
    detail: bool = False


class SourceLocator(BaseModel):
    proposals: list[LocatorProposal]
    warnings: list[str]


class CorrectionValue(BaseModel):
    value: str
    confirmed: bool


class ScanVerdict(BaseModel):
    """What the malware check concluded about these exact bytes (`sha256`). `unscanned` marks rows
    written before scanning existed and `not_configured` deployments without a scanner — neither is
    clean. `error`/`too_large` keep the file quarantined (`scan_failed`); the server retries `error`.
    `encrypted` (password-protected — or, with `checked: false`, a structure the check could not
    finish; `reason` archive / office / pdf) is never clean: the file ends in `error` and is never
    served (410 `encrypted_file` / `unchecked_file`); a template upload is refused with 400 and the
    same codes; a file stored before scanning that a rescan finds encrypted keeps its state (the
    owner decides)."""
    status: Literal["pending", "clean", "infected", "encrypted", "error", "too_large", "not_configured", "unscanned"]
    signature: str | None = None
    engine: str | None = None
    reason: str | None = None
    checked: bool | None = None
    sha256: str | None = None
    checked_at: float | None = None
    attempts: int | None = None
    retry_at: float | None = None

    @model_serializer(mode="wrap")
    def _only_known(self, handler):
        # The verdict carries the keys it knows, exactly as the workbench API returns it;
        # a `signature: null` on a clean file would be noise in every listing.
        return {k: v for k, v in handler(self).items() if v is not None}


class SourceOcrResult(BaseModel):
    recognized: list[int]
    pages: list[int]
    words: int
    low_conf: int
    status: str
    version: int


class SourceFile(BaseModel):
    id: str
    project_id: str
    filename: str
    ord: int
    size: int
    format: str | None
    family: str | None
    status: Status
    error: str | None
    notes: list[str]
    structure: dict[str, JsonValue]
    version: int
    representative: bool
    created_at: float
    updated_at: float
    options: dict[str, JsonValue]
    ocr_pages: list[int]
    corrections: dict[str, CorrectionValue]
    sha256: str
    scan: ScanVerdict


class SourceStructure(BaseModel):
    id: str
    project_id: str
    format: str | None
    family: str | None
    status: Status
    error: str | None
    notes: list[str]
    version: int
    structure: dict[str, JsonValue] = Field(description="Existing format-specific parser summary: sheets, docx, pdf, image, pptx or text.")


class SourceView(BaseModel):
    id: str
    filename: str
    format: str | None
    family: str | None
    status: Status
    error: str | None
    notes: list[str]
    version: int
    view: Literal["pending", "error", "grid", "docx", "pdf", "image", "slides", "text"]
    block: int | None = None
    grid: dict[str, JsonValue] | None = None
    doc: dict[str, JsonValue] | None = None
    pdf: dict[str, JsonValue] | None = None
    image: dict[str, JsonValue] | None = None
    slides: dict[str, JsonValue] | None = None
    lines: list[str] | None = None
    ocr_pages: list[int] | None = None


T = TypeVar("T")


class SourcePage(BaseModel, Generic[T]):
    items: list[T]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class SourceBlock(BaseModel):
    index: int
    label: str
    where: str


class SourceBlocks(SourcePage[SourceBlock]):
    mode: Mode | None = None
    error: str | None = None
    code: str | None = None


class SourceHit(BaseModel):
    kind: Literal["cells", "docx_text", "rect", "text_span", "pptx"]
    text: str
    sheet: str | None = None
    r: int | None = None
    c: int | None = None
    part: str | None = None
    path: str | None = None
    start: int | None = None
    end: int | None = None
    page: int | None = None
    nrect: list[float] | None = None
    line: int | None = None
    slide: int | None = None
    shape: str | None = None


class SourceWord(BaseModel):
    t: str
    r: list[float]
    u: list[float] | None = None
    b: int | None = None
    l: int | None = None
    n: int | None = None
    conf: float | None = None
    line: str | None = None


class SourceSearch(SourcePage[SourceHit]):
    truncated: bool = Field(description="More matches exist but continuing would exceed the maximum search offset (10000).")


class SourceWords(SourcePage[SourceWord]):
    index: int
    source: Literal["text_layer", "ocr", "none"]
    w: float | None = None
    h: float | None = None
    rotation: int | None = None
    kind: str | None = None
    engine: str | None = None


class SourceRecord(BaseModel):
    index: int
    key: str
    label: str
    ident: str | None
    file_id: str
    where: str
    merged_from: list[str] | None


class SourceRecords(SourcePage[SourceRecord]):
    mode: Mode
    mode_text: str
    excluded: dict[str, int]
    file_errors: list[dict[str, JsonValue]]
    warnings: list[JsonValue]
    files_used: int
    blocks: JsonValue


def _input_schema(model: type[BaseModel]) -> dict:
    """Inline these small, nonrecursive request models; no dangling component refs."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].rsplit("/", 1)[-1]])
            # Inline oneOf alternatives retain their required kind constants. A discriminator
            # would refer to removed $defs (or implicitly to nonexistent named schemas).
            return {k: inline(v) for k, v in node.items() if k != "discriminator"}
        return [inline(v) for v in node] if isinstance(node, list) else node

    return inline(schema)


def _contract(scope: str, model: type[BaseModel] | None = None, *, errors: tuple[int, ...] = ()) -> dict:
    out = {"x-scope": scope}
    if model is not None:
        out["requestBody"] = {"required": True, "content": {
            "application/json": {"schema": _input_schema(model)}}}
    # Every typed body goes through _body's byte limit. Lifecycle errors are
    # declared per operation because metadata reads can still return pending rows.
    names = {409: "Conflict", 410: "Gone", 413: "PayloadTooLarge"}
    codes = set(errors) | ({413} if model is not None else set())
    if codes:
        out["responses"] = {str(code): {"$ref": f"#/components/responses/{names[code]}"}
                            for code in sorted(codes)}
    return out


async def _body(request: Request, model: type[BaseModel]):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ServiceError(400, "invalid", "请求体必须是 application/json")
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY_BYTES:
            raise ServiceError(413, "source_body_too_large", "来源操作请求体超过 256 KiB 上限")
        data.extend(chunk)
    def unique_members(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("duplicate JSON member")
        return result

    try:
        return model.model_validate(json.loads(data, object_pairs_hook=unique_members))
    except ValidationError as e:
        first = e.errors()[0]
        raise ServiceError(400, "invalid", first["msg"],
                           {"field": ".".join(str(k) for k in first["loc"]) or "body"}) from e
    except (ValueError, UnicodeError, RecursionError) as e:
        raise ServiceError(400, "invalid", "请求体不是合法的 JSON") from e


def _page(request: Request, response: Response, items: list, total: int, offset: int, limit: int) -> dict:
    nxt = offset + len(items) if items and offset + len(items) < total else None
    if nxt is not None:
        query = [(k, v) for k, v in request.query_params.multi_items() if k not in ("offset", "limit")]
        query.extend((("offset", nxt), ("limit", limit)))
        response.headers["Link"] = f'<{request.url.path}?{urlencode(query)}>; rel="next"'
    return {"items": items, "total": total, "offset": offset, "limit": limit, "next_offset": nxt}


def register(r: APIRouter, app_, views, scope, attachment, auth) -> None:
    """Register on the existing /api/v1 router using its live machine verifier."""
    def gate(request: Request, needed: str, query: tuple[str, ...] = ()):
        principal = scope(request, needed)
        seen = set()
        for key, value in request.query_params.multi_items():
            if key not in query or key in seen:
                raise ServiceError(400, "invalid", "查询参数未知或重复", {"field": key})
            if key in ("offset", "limit", "block") and not (value.isascii() and value.isdecimal()):
                raise ServiceError(400, "invalid", "参数必须是非负整数", {"field": key})
            seen.add(key)
        return principal

    def source(fid: str, *, ready: bool = False) -> dict:
        row = app_.get_source(fid, raw=True)
        if ready and row["status"] not in ("ready", "needs_ocr"):
            raise ServiceError(409, "source_not_ready", "来源文件尚不可读取", {"status": row["status"]})
        return row

    @r.get("/files/{fid}/structure", operation_id="get_source_structure", response_model=SourceStructure,
           openapi_extra=_contract("read"))
    def structure(request: Request, fid: FileID):
        gate(request, "read")
        return source(fid)

    @r.get("/files/{fid}/view", operation_id="get_source_view", response_model=SourceView,
           response_model_exclude_unset=True, openapi_extra=_contract("read", errors=(409, 410)),
           description="Browser service view. offset/limit window grid rows only; other formats return their native document view.")
    def view(request: Request, fid: FileID, sheet: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
             offset: Offset = 0, limit: Limit = 200, block: BlockIndex = None):
        gate(request, "read", ("sheet", "offset", "limit", "block"))
        row = source(fid)
        if block is not None or sheet is not None:
            source(fid, ready=True)
            doc = app_.doc_for(row, block)
            if sheet is not None and (doc.workbook_json is None or not any(s.name == sheet for s in doc.workbook.sheets)):
                raise ServiceError(400, "unknown_sheet", "来源文件没有该工作表", {"field": "sheet"})
        return views.source_view(fid, sheet, offset, limit, block)

    @r.get("/files/{fid}/blocks", operation_id="list_source_blocks", response_model=SourceBlocks,
           response_model_exclude_unset=True, openapi_extra=_contract("read", errors=(409, 410)),
           description="Current per-block rule results, paged after the shared splitter runs. No block state is stored by this endpoint.")
    def blocks(request: Request, response: Response, fid: FileID, offset: Offset = 0, limit: Limit = 50):
        gate(request, "read", ("offset", "limit"))
        source(fid, ready=True)
        out = dict(app_.source_blocks(fid))
        rows = out.pop("blocks")
        return {**out, **_page(request, response, rows[offset:offset + limit], len(rows), offset, limit)}

    @r.get("/files/{fid}/search", operation_id="search_source", response_model=SourceSearch,
           response_model_exclude_unset=True, openapi_extra=_contract("read", errors=(409, 410)),
           description="Literal text search in the shared viewer order. The service scans the source to count all matches; retains at most offset+limit hits. Offset is limited to 10000.")
    def search(request: Request, response: Response, fid: FileID,
               q: Annotated[str, Query(min_length=1, max_length=256)],
               offset: Annotated[int, Query(ge=0, le=MAX_SEARCH_OFFSET)] = 0,
               limit: Limit = 50, block: BlockIndex = None):
        gate(request, "read", ("q", "offset", "limit", "block"))
        source(fid, ready=True)
        if not q.strip():
            raise ServiceError(400, "invalid", "q 不能只有空白", {"field": "q"})
        out = views.search_source(fid, q, limit=offset + limit, block=block)
        page = _page(request, response, out["hits"][offset:offset + limit], out["total"], offset, limit)
        # Never advertise an unreachable continuation past the bounded search window.
        page["truncated"] = page["next_offset"] is not None and page["next_offset"] > MAX_SEARCH_OFFSET
        if page["truncated"]:
            page["next_offset"] = None
            del response.headers["Link"]
        return page

    @r.get("/files/{fid}/words/{page}", operation_id="list_source_page_words", response_model=SourceWords,
           response_model_exclude_unset=True, openapi_extra=_contract("read", errors=(409, 410)),
           description="Zero-based page and optional block. Pages the existing text layer or previously stored OCR words; does not execute OCR.")
    def words(request: Request, response: Response, fid: FileID, page: Annotated[int, Path(ge=0, le=1000000)],
              offset: Offset = 0, limit: Limit = 200, block: BlockIndex = None):
        gate(request, "read", ("offset", "limit", "block"))
        row = source(fid, ready=True)
        if row["family"] not in ("pdf", "image"):
            raise ServiceError(400, "no_words", "该文件没有页面文字层")
        doc = app_.doc_for(row, block)
        if row["family"] == "pdf":
            from ..engine.pdf_engine import page_count
            count = page_count(doc.data)
        else:
            count = 1
        if page >= count:
            raise ServiceError(404, "page_missing", "来源文件没有该页", {"field": "page"})
        out = dict(views.source_words(fid, page, block))
        rows = out.pop("words")
        return {**out, **_page(request, response, rows[offset:offset + limit], len(rows), offset, limit)}

    @r.get("/files/{fid}/page/{page}.png", operation_id="render_source_page", response_class=Response,
           openapi_extra={**_contract("export", errors=(409, 410)), "x-binary": ["image/png"]},
           responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}},
           description="Zero-based PDF/image page, rendered by the browser engine. PDF zoom is bounded by the pixel budget; image sources return native dimensions. No stored artifact; current export permission is required.")
    def page_png(request: Request, fid: FileID, page: Annotated[int, Path(ge=0, le=1000000)],
                 zoom: Annotated[float, Query(ge=0.2, le=4, allow_inf_nan=False)] = 1.5,
                 block: BlockIndex = None):
        gate(request, "export", ("zoom", "block"))
        row = source(fid, ready=True)
        if row["family"] not in ("pdf", "image"):
            raise ServiceError(400, "not_paged", "该文件没有页面图像")
        doc = app_.doc_for(row, block)
        from ..engine.pdf_engine import page_count
        count = page_count(doc.data) if row["family"] == "pdf" else 1
        if page >= count:
            raise ServiceError(404, "page_missing", "来源文件没有该页", {"field": "page"})
        data = views.source_page_png(fid, page, zoom, block)
        # Rendering can outlive either the grant or the source. Check both before publishing.
        principal = auth.verify(request.headers.get("authorization"))
        auth.require(principal, "export")
        current = source(fid, ready=True)
        if current["version"] != row["version"]:
            raise ServiceError(409, "source_changed", "来源在渲染期间发生变化；请重新读取")
        return Response(data, media_type="image/png", headers={"Cache-Control": "no-store",
                        "X-Content-Type-Options": "nosniff", "X-Content-SHA256": hashlib.sha256(data).hexdigest()})

    @r.post("/files/{fid}/locator", operation_id="build_source_locator", response_model=SourceLocator,
            response_model_exclude_unset=True, openapi_extra=_contract("read", SourceLocatorInput, errors=(409, 410)),
            description="Build structured locator proposals from a native source selection. Uses the browser service and current project rule; does not save a mapping or execute OCR. Coordinates and indices are zero-based; PDF/image nrect is visual normalized [x0,y0,x1,y1].")
    async def locator(request: Request, fid: FileID):
        gate(request, "read")
        body = await _body(request, SourceLocatorInput)

        def propose():
            from ..locators import build_locator
            row = source(fid, ready=True)
            pid = body.project_id or row["project_id"]
            project = app_.get_project(pid)
            if pid != row["project_id"]:
                not_found("来源文件")
            return build_locator(app_, fid, body.selection.model_dump(exclude_none=True), project, body.block)

        return await run_in_threadpool(propose)

    @r.put("/files/{fid}/options", operation_id="update_source_options", response_model=SourceFile,
           openapi_extra=_contract("write", SourceOptions, errors=(409, 410)),
           description="CSV/TSV only. Replaces the csv options group (omitted controls reset to auto/default), synchronously reparses through the shared service. Check returned status/error.")
    async def options(request: Request, fid: FileID):
        gate(request, "write")
        body = await _body(request, SourceOptions)

        def update():
            row = source(fid)
            if row["status"] == "parsing":
                raise ServiceError(409, "source_not_ready", "请等待当前解析完成")
            if row["format"] not in ("csv", "tsv"):
                raise ServiceError(400, "unsupported_options", "当前仅 CSV/TSV 有可配置的解析选项")
            # The service owns its atomic metadata update and parses after releasing
            # that transaction. Do not hold the SQLite writer across synchronous parsing.
            return app_.set_source_options(fid, body.model_dump())

        return await run_in_threadpool(update)

    @r.put("/files/{fid}/corrections/{mid}", operation_id="update_source_correction",
           response_model=dict[str, CorrectionValue], openapi_extra=_contract("write", SourceCorrection, errors=(409,)),
           description="Set or clear (value=null) a correction for a mapping in the source project's current revision. Returns all current corrections.")
    async def correction(request: Request, fid: FileID, mid: FileID):
        gate(request, "write")
        body = await _body(request, SourceCorrection)

        def update():
            with app_.store.tx():
                row = source(fid, ready=True)
                if not any(m.get("id") == mid for m in app_.get_mappings(row["project_id"])["mappings"]):
                    not_found("当前映射")
                return views.set_correction(fid, mid, body.value, body.confirmed)

        return await run_in_threadpool(update)

    @r.post("/files/{fid}/ocr", operation_id="run_source_ocr", response_model=SourceOcrResult,
            openapi_extra=_contract("write", SourceOcr, errors=(409, 410)),
            description="Recognise text on an accepted scanned PDF (by default every scanned or mixed page; at most 50 pages "
                        "per call) or image, with the workbench's engine, synchronously. Results merge into the stored OCR "
                        "(pages recognised again are replaced); a needs_ocr source becomes ready and its version advances. "
                        "Repeating a call recognises the same pages again and stores the same words. A source still "
                        "scanning, rejected or failed is refused (409 source_not_ready). 429 ocr_busy when the server's "
                        "recognition slots stay full: wait retry_after seconds. After a client timeout, list_source_page_words "
                        "reports source=ocr for pages already recognised.")
    async def ocr(request: Request, fid: FileID):
        gate(request, "write")
        body = await _body(request, SourceOcr)

        def run():
            source(fid, ready=True)
            return views.run_ocr(fid, body.pages)

        return await run_in_threadpool(run)

    @r.post("/files/{fid}/representative", operation_id="select_representative_source", response_model=SourceFile,
            openapi_extra=_contract("write", errors=(409,)), description="Select an existing live source as the project's representative; creates no object.")
    async def representative(request: Request, fid: FileID):
        gate(request, "write")
        async for chunk in request.stream():
            if chunk:
                raise ServiceError(400, "invalid", "此操作不接受请求体")

        def update():
            with app_.store.tx():
                source(fid, ready=True)
                return app_.set_representative(fid)

        return await run_in_threadpool(update)

    @r.put("/projects/{pid}/files/order", operation_id="reorder_project_sources", response_model=list[SourceFile],
           openapi_extra=_contract("write", SourceOrder),
           description="ids must be a complete permutation of this project's live source ids. Validates all ids before writing; returns sources in their new order.")
    async def order(request: Request, pid: FileID):
        gate(request, "write")
        body = await _body(request, SourceOrder)

        def update():
            with app_.store.tx():
                current = app_.list_sources(pid)
                if len(set(body.ids)) != len(body.ids):
                    raise ServiceError(400, "invalid", "ids 不能重复", {"field": "ids"})
                for fid in body.ids:
                    if source(fid)["project_id"] != pid:
                        not_found("来源文件")
                if set(body.ids) != {f["id"] for f in current}:
                    raise ServiceError(400, "invalid", "ids 必须包含项目的全部当前来源文件", {"field": "ids"})
                return app_.reorder_sources(pid, body.ids)

        return await run_in_threadpool(update)

    @r.get("/files/{fid}/content", operation_id="download_source_content", response_class=Response,
           openapi_extra={**_contract("export", errors=(409, 410)), "x-binary": ["*/*"]},
           responses={200: {"content": {"*/*": {"schema": {"type": "string", "format": "binary"}}}}},
           description="Original upload bytes, with safe attachment filename, no-store and X-Content-SHA256. Content-Type reflects the original file format.")
    def content(request: Request, fid: FileID):
        gate(request, "export")
        row = source(fid)
        app_.require_accepted(row)        # quarantined or rejected bytes are never served
        if not app_.store.has_blob(row["blob"]):
            raise ServiceError(410, "source_content_missing", "来源文件内容已不在存储中")
        result = attachment(app_.store.get_blob(row["blob"]), row["filename"])
        result.headers["X-Content-SHA256"] = row["blob"]
        return result

    @r.get("/projects/{pid}/locate", operation_id="locate_source_mappings",
           response_model=dict[str, list[dict[str, JsonValue]]], openapi_extra=_contract("read", errors=(409, 410)),
           description="Resolve current saved mappings to positions in one source belonging to this project; read only.")
    def locate(request: Request, pid: FileID, file: Annotated[str, Query(min_length=1, max_length=128)], block: BlockIndex = None):
        gate(request, "read", ("file", "block"))
        app_.get_project(pid)
        if source(file, ready=True)["project_id"] != pid:
            not_found("来源文件")
        from ..locators import locate_all
        return locate_all(app_, pid, file, block)

    @r.get("/projects/{pid}/records", operation_id="list_source_records", response_model=SourceRecords,
           openapi_extra=_contract("read"),
           description="Current sample records from the browser's rule engine. Pages the summary; the shared service computes all records first. No job or duplicate record state is created.")
    def records(request: Request, response: Response, pid: FileID, offset: Offset = 0, limit: Limit = 50):
        gate(request, "read", ("offset", "limit"))
        app_.get_project(pid)
        out = dict(views.records(pid, offset=offset, limit=limit))
        rows = out.pop("records")
        return {**out, **_page(request, response, rows, out["total"], offset, limit)}
