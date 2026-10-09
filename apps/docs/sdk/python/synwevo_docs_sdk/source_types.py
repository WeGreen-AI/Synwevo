"""Public static types for source-selection responses; no server/runtime model dependency.

Optional format-specific payloads remain JSON objects, matching the published API.
TypedDicts describe server responses; the server remains the validation authority.
"""
from typing import Any, Literal, TypedDict

SourceStatus = Literal["scanning", "scan_failed", "parsing", "ready", "needs_ocr", "error", "unsupported", "rejected"]
ScanStatus = Literal["pending", "clean", "infected", "encrypted", "error", "too_large", "not_configured", "unscanned"]
SourceMode = Literal["per_row", "per_file", "per_group", "per_block"]


class _ScanVerdict(TypedDict):
    status: ScanStatus


class ScanVerdict(_ScanVerdict, total=False):
    signature: str | None
    engine: str | None
    reason: str | None
    checked: bool | None
    sha256: str | None
    checked_at: float | None
    attempts: int | None  # Cumulative across manual cycles; never reset by a retry.
    retry_at: float | None
    cycle_attempts: int
    retry_exhausted: bool


class CSVOptions(TypedDict, total=False):
    encoding: Literal["auto", "utf-8", "utf-8-sig", "gb18030", "gbk", "big5", "utf-16", "shift_jis", "latin-1"]
    delimiter: Literal["auto", ",", "\t", "\\t", ";", "|"]
    quotechar: Literal['"', "'"]


class CorrectionValue(TypedDict):
    value: str
    confirmed: bool


class SourceStructure(TypedDict):
    id: str
    project_id: str
    format: str | None
    family: str | None
    status: SourceStatus
    error: str | None
    notes: list[str]
    version: int
    structure: dict[str, Any]


class SourceFile(SourceStructure):
    filename: str
    ord: int
    size: int
    representative: bool
    created_at: float
    updated_at: float
    options: dict[str, Any]
    ocr_pages: list[int]
    corrections: dict[str, CorrectionValue]
    sha256: str
    scan: ScanVerdict


class _SourceView(TypedDict):
    id: str
    filename: str
    format: str | None
    family: str | None
    status: SourceStatus
    error: str | None
    notes: list[str]
    version: int
    view: Literal["pending", "error", "grid", "docx", "pdf", "image", "slides", "text"]


class SourceView(_SourceView, total=False):
    block: int | None
    grid: dict[str, Any] | None
    doc: dict[str, Any] | None
    pdf: dict[str, Any] | None
    image: dict[str, Any] | None
    slides: dict[str, Any] | None
    lines: list[str] | None
    ocr_pages: list[int] | None


class OffsetPage(TypedDict):
    total: int
    offset: int
    limit: int
    next_offset: int | None


class SourceBlock(TypedDict):
    index: int
    label: str
    where: str


class _SourceBlocks(OffsetPage):
    items: list[SourceBlock]


class SourceBlocks(_SourceBlocks, total=False):
    mode: SourceMode | None
    error: str | None
    code: str | None


class _SourceHit(TypedDict):
    kind: Literal["cells", "docx_text", "rect", "text_span", "pptx"]
    text: str


class SourceHit(_SourceHit, total=False):
    sheet: str | None
    r: int | None
    c: int | None
    part: str | None
    path: str | None
    start: int | None
    end: int | None
    page: int | None
    nrect: list[float] | None
    line: int | None
    slide: int | None
    shape: str | None


class SourceSearch(OffsetPage):
    items: list[SourceHit]
    truncated: bool


class _SourceWord(TypedDict):
    t: str
    r: list[float]


class SourceWord(_SourceWord, total=False):
    u: list[float] | None
    b: int | None
    l: int | None
    n: int | None
    conf: float | None
    line: str | None


class _SourceWords(OffsetPage):
    items: list[SourceWord]
    index: int
    source: Literal["text_layer", "ocr", "none"]


class SourceWords(_SourceWords, total=False):
    w: float | None
    h: float | None
    rotation: int | None
    kind: str | None
    engine: str | None


class SourceRecord(TypedDict):
    index: int
    key: str
    label: str
    ident: str | None
    file_id: str
    where: str
    merged_from: list[str] | None


class SourceRecords(OffsetPage):
    items: list[SourceRecord]
    mode: SourceMode
    mode_text: str
    excluded: dict[str, int]
    file_errors: list[dict[str, Any]]
    warnings: list[Any]
    files_used: int
    blocks: Any
