"""Bulk member import: a file → rows → planned changes → invitations and role updates.

Why it is shaped like this (decision record in docs/platform/member-import.md):

* **A row in a file is not consent.** Nobody becomes a member because their address was in a
  spreadsheet: every new person gets the ordinary invitation, accepts it themselves, and joins
  the organization through the same code path as a hand-typed invitation
  (`organizations.accept_workspace_invitation`). Nothing here creates a sign-in account, sends
  a password or skips e-mail verification.
* **Roles come from explicit mapping, never from strings.** A cell saying "admin" maps to
  nothing until the operator maps that text to a real role in the mapping step; base roles
  (organization owner/admin, workspace owner) cannot be imported at all.
* **Whatever the importer may not do by hand, the import may not do either.** Organization
  roles are bounded by `OrgRoles.assign` (the delegator never exceeds their own permissions),
  workspace roles by the invitation service's own rule, both re-checked when a row executes and
  again when the invitation is accepted — the permissions held while the file was uploaded do
  not carry forward.
* **What a machine read, a person checks (CI-100).** A row read by OCR — an image, a scanned
  page, or a scan's invisible text layer — that would invite or update someone is blocked
  (`ocr_unchecked`) until the operator has looked at it: confirmed it, or corrected the address or
  name, in the mapping step (`mapping["reviews"]`, keyed by page and line so a correction survives
  every re-plan, with who checked it and when). Confidence is shown, and low values are flagged,
  but it is not the gate: measured on Tesseract, a line's confidence both blocks correct addresses
  and passes misread ones. The "only valid rows" choice skips unchecked rows; nobody is invited
  from an address no person looked at.
* **Additive on existing members, visible in a diff.** An existing member's workspace role is
  raised, never lowered; organization roles are added, never removed; nobody is removed because
  they are missing from the file. The preview shows before/after per row and the operator
  confirms.
* **Reading the file** is the document engine's job (injected: `apps/docs/synwevo_docs/ingest.py`).
  Without it, the standard-library readers here cover CSV/TSV/JSON/TXT/Markdown and every other
  format is reported as needing the engine — never "unsupported" in disguise.
* The uploaded bytes are scanned (same clamd policy as every other upload), read once, and only
  the extracted rows are kept — bounded, for a limited time; the file itself is not stored.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re

from . import malware as M
from .errors import ServiceError
from .identity import Identity, ROLE_RANK, normalise_email, valid_email
from .org_roles import OrgRoles
from .organizations import current_membership
from .store import Store, new_id, now

log = logging.getLogger("synwevo.member_import")

SCHEMA = """
CREATE TABLE IF NOT EXISTS member_imports (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, workspace_id TEXT, created_by TEXT NOT NULL,
  filename TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL, format TEXT, family TEXT,
  status TEXT NOT NULL,
  extraction TEXT, mapping TEXT, summary TEXT, scan TEXT, error TEXT,
  version INTEGER NOT NULL DEFAULT 1, cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, confirmed_at REAL, finished_at REAL, expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_member_imports_org ON member_imports(organization_id, created_at);
CREATE TABLE IF NOT EXISTS member_import_rows (
  id TEXT PRIMARY KEY, import_id TEXT NOT NULL, seq INTEGER NOT NULL,
  source TEXT, fields TEXT, email_key TEXT, action TEXT, reason TEXT, diff TEXT,
  status TEXT NOT NULL DEFAULT 'planned', result TEXT, invitation_id TEXT, org_role_ids TEXT,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_member_import_rows ON member_import_rows(import_id, seq);
CREATE INDEX IF NOT EXISTS ix_member_import_rows_invitation ON member_import_rows(invitation_id);
"""

#: rows an import may plan (each invitation is a mail; the bulk invitation route has the same order of magnitude)
MAX_ROWS = 500
#: Under this lowest word confidence (Tesseract, 0–100) an OCR line is flagged as one the machine
#: was unsure of — a hint for the person checking it, not the gate: every OCR row is checked.
LOW_CONFIDENCE = 85
MAX_TABLE_ROWS = 2000
MAX_LINES = 5000
MAX_TABLES = 50
MAX_FILE_BYTES = 20 * 1024 * 1024
RETENTION_S = 7 * 24 * 3600
IMPORTABLE_WORKSPACE_ROLES = ("viewer", "editor", "admin")
FIELDS = ("email", "name", "workspace_role", "org_roles")
IMPORT_STATUSES = ("extracted", "planned", "running", "finished", "partial", "failed", "cancelled")
ROW_ACTIONS = ("invite", "update", "unchanged", "skip", "blocked")
ROW_STATUSES = ("planned", "pending", "running", "done", "failed", "skipped")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
ROLE_SPLIT = re.compile(r"[,;/、|，；]+")
_ROLE_HINT = re.compile(r"role|角色|权限|權限|职务|職務|岗位|崗位", re.I)
_HEADER_HINTS = {
    "email": re.compile(r"e-?mail|邮箱|郵箱|電郵|电邮|信箱|邮件地址|郵件地址|mail", re.I),
    "name": re.compile(r"^(name|full ?name|display ?name|姓名|名字|名稱|名称|成员|成員|member)$", re.I),
    "workspace_role": re.compile(r"space|workspace|空间|空間", re.I),
    "org_roles": re.compile(r"org|组织|組織|custom|自定义|自訂", re.I),
}


def _load(v):
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _dump(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------- acceptance (shared by both engines)
def accept_upload(filename: str, data: bytes) -> dict:
    """Scan the bytes with the platform's clamd policy before any reader opens them.

    Same verdict semantics as the Docs template scan: infected → 400, password-protected → 400
    ``encrypted_file`` (nobody can check what is inside: not accepted, not malware; §8.7),
    scanner down → 503 (never a pass), over the scan limit → 413. `not_configured` is reported,
    not hidden. Synwevo Message attachments come through here too."""
    if len(data) > MAX_FILE_BYTES:
        raise ServiceError(413, "file_too_large", f"成员文件超过 {MAX_FILE_BYTES // (1024 * 1024)} MB 上限", {"limit": MAX_FILE_BYTES})
    pol = M.policy()
    sha = hashlib.sha256(data).hexdigest()
    scanner = M.scanner_for(pol)
    if scanner is None:
        return {"status": "not_configured", "sha256": sha}
    try:
        res = scanner.scan(data, sha256=sha)
    except M.ScanUnavailable as e:
        log.warning("member_import scan.unavailable sha256=%s reason=%s", sha, str(e)[:160])
        raise ServiceError(503, "scan_unavailable", "安全检查服务暂不可用，请稍后重试", {"retry_after": 30}) from e
    if res.status == "infected":
        log.warning("member_import scan.rejected sha256=%s signature=%s", sha, res.signature)
        raise ServiceError(400, "malware_detected", f"文件「{filename}」被判定为恶意内容（{res.signature}），已拒绝",
                           {"signature": res.signature, "filename": filename})
    if res.status == "encrypted":
        log.info("member_import scan.encrypted sha256=%s kind=%s signature=%s", sha, res.reason, res.signature)
        raise ServiceError(400, "encrypted_file", "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传",
                           {"filename": filename, "kind": res.reason})
    if res.status == "too_large":
        raise ServiceError(413, "scan_limit", f"文件超过安全检查的大小上限（{pol.max_bytes // (1024 * 1024)} MB），无法接纳",
                           {"limit": pol.max_bytes})
    return res.as_record()


# ---------------------------------------------------------------- readers shared by both engines
def decode_text(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "cp1252"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8?"


def empty_extraction(engine: str, fmt: str, family: str) -> dict:
    return {"engine": engine, "format": fmt, "family": family, "status": "ready", "error": None, "notes": [],
            "tables": [], "lines": [], "lines_truncated": False, "pages": None,
            "ocr": {"available": False, "applied": False, "engine": None}}


def add_table(out: dict, name: str, source: dict, rows: list[list[str]], row_numbers: list[int] | None = None) -> None:
    if len(out["tables"]) >= MAX_TABLES:
        return
    rows = [[("" if c is None else str(c)) for c in r] for r in rows]
    numbers = row_numbers or list(range(1, len(rows) + 1))
    truncated = len(rows) > MAX_TABLE_ROWS
    out["tables"].append({"name": name, "source": source, "rows": rows[:MAX_TABLE_ROWS],
                          "row_numbers": numbers[:MAX_TABLE_ROWS], "truncated": truncated})
    if truncated:
        out["notes"].append(f"「{name}」只读取前 {MAX_TABLE_ROWS} 行")


def add_lines(out: dict, lines: list[tuple[str, dict]]) -> None:
    for text, source in lines:
        text = text.strip()
        if not text:
            continue
        if len(out["lines"]) >= MAX_LINES:
            out["lines_truncated"] = True
            break
        out["lines"].append({"text": text[:500], "source": source})


def text_extraction(engine: str, fmt: str, text: str, *, note: str | None = None) -> dict:
    """TXT / Markdown / JSON as rows and lines. Markdown pipe tables and JSON arrays of objects
    become tables; everything else is a line the planner searches for an address."""
    out = empty_extraction(engine, fmt, "text")
    if note:
        out["notes"].append(note)
    if fmt == "json":
        try:
            payload = json.loads(text)
        except ValueError as e:
            out["status"], out["error"] = "error", f"JSON 无法解析：{str(e)[:120]}"
            return out
        records = _json_records(payload)
        if records is None:
            out["status"], out["error"] = "error", "JSON 里没有对象数组（例如 [{\"email\": …, \"name\": …}]）"
            return out
        keys: list[str] = []
        for r in records:
            for k in r:
                if k not in keys:
                    keys.append(k)
        rows = [keys] + [[_scalar(r.get(k)) for k in keys] for r in records]
        add_table(out, "JSON", {"json": True}, rows, [0] + list(range(1, len(records) + 1)))
        out["notes"].append("第一行是 JSON 字段名")
        return out
    lines = text.splitlines()
    if fmt == "md":
        i, n = 0, 0
        free: list[tuple[str, dict]] = []
        while i < len(lines):
            if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
                block = []
                start = i
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    block.append(lines[i]); i += 1
                cells = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in block]
                cells = [cells[0]] + cells[2:]           # drop the --- separator
                n += 1
                add_table(out, f"表格{n}", {"md_table": n, "line": start + 1}, cells,
                          [start + 1] + [start + 3 + k for k in range(len(cells) - 1)])
                continue
            free.append((lines[i], {"line": i + 1})); i += 1
        add_lines(out, free)
        return out
    add_lines(out, [(ln, {"line": i + 1}) for i, ln in enumerate(lines)])
    return out


def _json_records(payload) -> list[dict] | None:
    if isinstance(payload, list) and payload and all(isinstance(x, dict) for x in payload):
        return payload
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
    return None


def _scalar(v) -> str:
    if v is None or isinstance(v, bool):
        return "" if v is None else ("true" if v else "false")
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def csv_extraction(engine: str, fmt: str, data: bytes, delimiter: str | None = None) -> dict:
    text, enc = decode_text(data)
    if not delimiter:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = "\t" if fmt == "tsv" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    out = empty_extraction(engine, fmt, "tabular")
    out["notes"].append(f"编码 {enc}，分隔符 {'制表符' if delimiter == chr(9) else repr(delimiter)}；单元格按文本读取，不执行公式")
    add_table(out, "CSV", {"csv": True}, rows)
    return out


class BasicIngest:
    """Standard-library readers, used when the account service runs without the document engine."""
    name = "basic"
    FORMATS = ("csv", "tsv", "json", "txt", "md")

    def describe(self) -> dict:
        return {"engine": "basic", "formats": list(self.FORMATS), "ocr": False, "converter": False,
                "scanner": M.health(M.policy())}

    def accept(self, filename: str, data: bytes) -> dict:
        return accept_upload(filename, data)

    def extract(self, data: bytes, filename: str, *, ocr: bool = False) -> dict:
        ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
        ext = {"markdown": "md", "text": "txt", "log": "txt"}.get(ext, ext)
        if ext in ("csv", "tsv"):
            return csv_extraction("basic", ext, data, "\t" if ext == "tsv" else None)
        if ext in ("json", "txt", "md"):
            text, enc = decode_text(data)
            return text_extraction("basic", ext, text, note=f"编码 {enc}")
        out = empty_extraction("basic", ext or "unknown", "unknown")
        out["status"] = "unsupported"
        out["needs_engine"] = True
        out["error"] = "这个实例的账户服务没有接入文档引擎，只能读取 CSV、TSV、JSON、TXT 和 Markdown；表格、文档、PDF 与扫描件需要文档引擎"
        return out


# ---------------------------------------------------------------- mapping suggestion
def suggest_mapping(extraction: dict) -> dict:
    """A starting point the operator confirms: which table, whether row 1 is a header, and which
    columns look like e-mail / name / roles. Never applied without confirmation."""
    tables = extraction.get("tables") or []
    if not tables:
        return {"table": None, "header_row": False, "columns": {}, "mode": "lines"}
    best, best_score = 0, -1
    for i, t in enumerate(tables):
        score = sum(1 for r in t["rows"][:200] for c in r if EMAIL_RE.search(c or ""))
        if score > best_score:
            best, best_score = i, score
    t = tables[best]
    rows = t["rows"]
    header = rows[0] if rows else []
    columns: dict[str, int] = {}
    header_row = False
    for idx, h in enumerate(header):
        h = (h or "").strip()
        if not h or EMAIL_RE.fullmatch(h):
            continue
        if "email" not in columns and _HEADER_HINTS["email"].search(h):
            columns["email"] = idx; header_row = True
        elif "name" not in columns and _HEADER_HINTS["name"].search(h):
            columns["name"] = idx; header_row = True
        elif _ROLE_HINT.search(h):
            header_row = True
            if "org_roles" not in columns and _HEADER_HINTS["org_roles"].search(h):
                columns["org_roles"] = idx
            elif "workspace_role" not in columns and _HEADER_HINTS["workspace_role"].search(h):
                columns["workspace_role"] = idx
            elif "workspace_role" not in columns:
                columns["workspace_role"] = idx
            elif "org_roles" not in columns:
                columns["org_roles"] = idx
    if "email" not in columns:
        width = max((len(r) for r in rows[:200]), default=0)
        for idx in range(width):
            cells = [r[idx] for r in rows[:200] if idx < len(r) and (r[idx] or "").strip()]
            if cells and sum(1 for c in cells if EMAIL_RE.search(c)) * 2 >= len(cells):
                columns["email"] = idx
                break
    return {"table": best, "header_row": header_row, "columns": columns, "mode": "table"}


# ---------------------------------------------------------------- candidates from an extraction
def candidates(extraction: dict, mapping: dict) -> list[dict]:
    """Rows as the file states them, before any lookup: e-mail, name, role texts, source."""
    out: list[dict] = []
    if mapping.get("mode", "table" if mapping.get("table") is not None else "lines") == "table":
        idx = mapping.get("table")
        tables = extraction.get("tables") or []
        if not isinstance(idx, int) or not 0 <= idx < len(tables):
            raise ServiceError(400, "bad_mapping", "请选择要导入的表格", {"field": "table"})
        cols = mapping.get("columns") or {}
        if not isinstance(cols.get("email"), int):
            raise ServiceError(400, "bad_mapping", "请指定邮箱所在的列", {"field": "columns.email"})
        t = tables[idx]
        start = 1 if mapping.get("header_row") else 0
        for r, row in enumerate(t["rows"][start:], start=start):
            def cell(field):
                c = cols.get(field)
                return (row[c] if isinstance(c, int) and 0 <= c < len(row) else "") or ""
            email = cell("email").strip()
            if not email and not any((x or "").strip() for x in row):
                continue                                   # blank row
            m = EMAIL_RE.search(email)
            out.append({"email": (m.group(0) if m else email), "name": cell("name").strip()[:100],
                        "workspace_role_text": cell("workspace_role").strip(), "org_roles_text": cell("org_roles").strip(),
                        "source": {**t["source"], "table": t["name"], "row": t["row_numbers"][r] if r < len(t["row_numbers"]) else r + 1}})
        return out
    reviews = mapping.get("reviews") or {}
    for ln in extraction.get("lines") or []:
        m = EMAIL_RE.search(ln["text"])
        src = dict(ln["source"])
        key = review_key(src)
        rev = reviews.get(key) if key else None
        # An OCR line with an "@" but no whole address is a misread address: shown, so a person can
        # correct it, rather than dropped without a trace.
        if not m and not (rev and rev.get("email")) and not (key and "@" in ln["text"]):
            continue
        if key:
            src["text"] = ln["text"][:200]
        rest = (ln["text"][:m.start()] + " " + ln["text"][m.end():]) if m else ln["text"]
        rest = re.sub(r"[<>()\[\]\"'，,;；|\t]+", " ", rest).strip()
        rest = re.sub(r"^(mailto:|邮箱|郵箱|email)[:：]?\s*", "", rest, flags=re.I).strip()
        raw = m.group(0) if m else (re.search(r"\S*@\S*", ln["text"]).group(0) if "@" in ln["text"] else "")
        if not m and raw:
            rest = ln["text"].replace(raw, " ").strip()
        cand = {"email": raw, "name": rest[:100], "workspace_role_text": "", "org_roles_text": "", "source": src}
        if key:
            src["review_key"] = key
            if rev:
                # The operator's reading replaces the machine's; the machine's stays in the source.
                if rev.get("email") and rev["email"] != cand["email"]:
                    src["read_email"] = cand["email"]
                    cand["email"] = rev["email"]
                if rev.get("name") is not None and rev["name"] != cand["name"]:
                    src["read_name"] = cand["name"]
                    cand["name"] = rev["name"]
                src["checked"] = bool(rev.get("checked"))
                if rev.get("by"):
                    src["checked_by"], src["checked_at"] = rev.get("by"), rev.get("at")
        out.append(cand)
    return out


def review_key(source: dict) -> str | None:
    """Where an OCR line came from, stable across re-reads and re-plans: page and line."""
    if not source.get("ocr"):
        return None
    return f"p{source.get('page') or 0}:l{source.get('line') or 0}"


def needs_review(source: dict) -> bool:
    """Every OCR row until a person has checked it (confidence is a hint, not the gate)."""
    return bool(source.get("ocr")) and not source.get("checked")


def clean_reviews(raw) -> dict:
    """`mapping["reviews"]` as the operator sent it, bounded and typed: `{key: {email?, name?, checked}}`."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_ROWS:
        raise ServiceError(400, "bad_mapping", "reviews 必须是按行的对象，最多 500 行", {"field": "reviews"})
    out = {}
    for k, v in raw.items():
        # `by`/`at` come back from the stored mapping; the server sets them, never the client.
        if not isinstance(k, str) or not re.fullmatch(r"p\d{1,5}:l\d{1,6}", k) or not isinstance(v, dict) or set(v) - {"email", "name", "checked", "by", "at"}:
            raise ServiceError(400, "bad_mapping", "reviews 的某一项无效", {"field": "reviews"})
        item = {"checked": v.get("checked") is True}
        if v.get("email") is not None:
            if not isinstance(v["email"], str) or len(v["email"].strip()) > 254:
                raise ServiceError(400, "bad_mapping", "reviews 的某一项无效", {"field": "reviews"})
            item["email"] = v["email"].strip()
        if v.get("name") is not None:
            if not isinstance(v["name"], str):
                raise ServiceError(400, "bad_mapping", "reviews 的某一项无效", {"field": "reviews"})
            item["name"] = v["name"].strip()[:100]
        out[k] = item
    return out


# ---------------------------------------------------------------- the service
class MemberImports:
    def __init__(self, store: Store, ident: Identity):
        self.store, self.ident = store, ident
        self.roles = OrgRoles(store)

    # ------------------------------------------------------------- rows in, rows out
    def _out(self, r: dict | None) -> dict | None:
        if not r:
            return None
        d = dict(r)
        for k in ("extraction", "mapping", "summary", "scan"):
            d[k] = _load(d.get(k))
        d["cancel_requested"] = bool(d.get("cancel_requested"))
        return d

    def _row_out(self, r: dict) -> dict:
        d = dict(r)
        for k in ("source", "fields", "diff", "result", "org_role_ids"):
            d[k] = _load(d.get(k))
        return d

    def create(self, oid: str, actor_id: str, *, filename: str, sha256: str, size: int, extraction: dict,
               scan: dict, workspace_id: str | None) -> dict:
        iid = new_id("mi")
        ts = now()
        self.store.insert("member_imports", {
            "id": iid, "organization_id": oid, "workspace_id": workspace_id, "created_by": actor_id,
            "filename": filename[:200], "sha256": sha256, "size": size, "format": extraction.get("format"),
            "family": extraction.get("family"), "status": "extracted", "extraction": _dump(extraction),
            "mapping": _dump(suggest_mapping(extraction)), "summary": None, "scan": _dump(scan), "error": extraction.get("error"),
            "created_at": ts, "updated_at": ts, "expires_at": ts + RETENTION_S})
        return self.get(iid)

    def get(self, iid: str) -> dict | None:
        return self._out(self.store.one("SELECT * FROM member_imports WHERE id=?", (iid,)))

    def public(self, r: dict, *, with_extraction: bool = False) -> dict:
        """What the API returns: never the raw extraction unless asked (it is the file's content)."""
        d = {k: r.get(k) for k in ("id", "organization_id", "workspace_id", "created_by", "filename", "sha256", "size",
                                    "format", "family", "status", "mapping", "summary", "scan", "error", "version",
                                    "cancel_requested", "created_at", "updated_at", "confirmed_at", "finished_at", "expires_at")}
        ex = r.get("extraction") or {}
        d["extraction"] = ({k: ex.get(k) for k in ("engine", "format", "family", "status", "error", "notes", "pages", "ocr", "lines_truncated", "needs_engine")}
                           | {"tables": [{k: t.get(k) for k in ("name", "source", "truncated")} | {"rows": len(t.get("rows") or []),
                                         "preview": (t.get("rows") or [])[:8]} for t in ex.get("tables") or []],
                              "lines": len(ex.get("lines") or []), "line_preview": [l["text"] for l in (ex.get("lines") or [])[:8]]})
        if with_extraction:
            d["extraction"]["tables"] = ex.get("tables") or []
            d["extraction"]["lines_full"] = ex.get("lines") or []
        return d

    def list(self, oid: str, *, limit: int = 20) -> list[dict]:
        rows = self.store.all("SELECT * FROM member_imports WHERE organization_id=? ORDER BY created_at DESC LIMIT ?", (oid, limit))
        return [self.public(self._out(r)) for r in rows]

    def rows(self, iid: str, *, action: str | None = None, limit: int = 1000, offset: int = 0) -> list[dict]:
        where, params = "import_id=?", [iid]
        if action:
            where += " AND action=?"; params.append(action)
        params += [limit, offset]
        return [self._row_out(r) for r in self.store.all(f"SELECT * FROM member_import_rows WHERE {where} ORDER BY seq LIMIT ? OFFSET ?", tuple(params))]

    def update_extraction(self, iid: str, extraction: dict) -> dict:
        with self.store.tx() as t:
            t.execute("UPDATE member_imports SET extraction=?, mapping=?, error=?, updated_at=?, status='extracted', summary=NULL WHERE id=? AND status IN ('extracted','planned')",
                      (_dump(extraction), _dump(suggest_mapping(extraction)), extraction.get("error"), now(), iid))
            t.execute("DELETE FROM member_import_rows WHERE import_id=?", (iid,))
        return self.get(iid)

    # ------------------------------------------------------------- planning
    def plan(self, iid: str, mapping: dict, *, actor_id: str, workspace_id: str, invite_check) -> dict:
        """Turn the mapping into per-row decisions. `invite_check(role) -> reason | None` is the
        invitation service's own authority rule for this actor and workspace (identity-owned)."""
        imp = self.get(iid)
        if not imp:
            raise ServiceError(404, "no_import", "找不到该导入")
        if imp["status"] not in ("extracted", "planned"):
            raise ServiceError(409, "import_not_editable", "这个导入已经开始执行，不能再改映射")
        if not isinstance(mapping, dict):
            raise ServiceError(400, "bad_mapping", "mapping 必须是对象")
        # Reviews are kept unless the caller sends its own set; each entry records who checked it
        # and when — kept while the entry is unchanged, set to this actor when it changes.
        before = ((imp.get("mapping") or {}).get("reviews") or {}) if isinstance(imp.get("mapping"), dict) else {}
        reviews = clean_reviews(mapping["reviews"]) if "reviews" in mapping else {k: {x: y for x, y in v.items() if x not in ("by", "at")} for k, v in before.items()}
        for k, v in reviews.items():
            old = before.get(k) or {}
            same = all(old.get(f) == v.get(f) for f in ("email", "name", "checked"))
            v["by"], v["at"] = (old.get("by"), old.get("at")) if same and old.get("by") else (actor_id, now())
        mapping = {**mapping, "reviews": reviews}
        ws = self.ident.get_workspace(workspace_id)
        oid = imp["organization_id"]
        if not ws or ws.get("organization_id") != oid or ws.get("status") != "active":
            raise ServiceError(400, "bad_workspace", "请选择这个组织里的一个可用工作空间", {"field": "workspace_id"})
        defaults = mapping.get("defaults") or {}
        default_role = defaults.get("workspace_role") or "viewer"
        if default_role not in IMPORTABLE_WORKSPACE_ROLES:
            raise ServiceError(400, "bad_mapping", "默认空间角色只能是 viewer、editor 或 admin", {"field": "defaults.workspace_role"})
        default_org_roles = [str(x) for x in (defaults.get("org_role_ids") or [])]
        ws_names = {str(k).strip().casefold(): v for k, v in (mapping.get("workspace_role_names") or {}).items()}
        org_names = {str(k).strip().casefold(): v for k, v in (mapping.get("org_role_names") or {}).items()}
        role_index = {r["id"]: r for r in self.roles.list(oid)}
        for rid in default_org_roles + list(org_names.values()):
            if rid not in role_index:
                raise ServiceError(400, "unknown_org_role", f"组织里没有角色 {rid}", {"field": "org_role_names"})
        actor_perms = self.roles.permissions_of(oid, actor_id)
        can_delegate = "org.roles.manage" in actor_perms
        cands = candidates(imp["extraction"] or {}, mapping)
        actor = self.ident.get_user(actor_id) or {}
        seen: set[str] = set()
        planned: list[dict] = []
        counts = {a: 0 for a in ROW_ACTIONS}
        reasons: dict[str, int] = {}
        unmapped_ws: set[str] = set()
        unmapped_org: set[str] = set()

        def decide(c: dict, seq: int) -> dict:
            fields = {"email": c["email"], "name": c["name"], "workspace_role": default_role, "org_roles": [], "org_role_ids": []}
            row = {"seq": seq, "source": c["source"], "fields": fields, "email_key": None, "action": "blocked",
                   "reason": None, "diff": None, "invitation_id": None}
            if seq > MAX_ROWS:
                row["reason"] = "over_limit"; return row
            if not valid_email(c["email"]):
                row["reason"] = "bad_email"; return row
            key = normalise_email(c["email"])
            row["email_key"] = key
            if key in seen:
                row["action"], row["reason"] = "skip", "duplicate_in_file"; return row
            seen.add(key)
            if actor.get("email_key") == key:
                row["action"], row["reason"] = "skip", "self"; return row
            # workspace role: explicit ids pass, anything else must be mapped by the operator
            text = c["workspace_role_text"]
            if text:
                mapped = ws_names.get(text.casefold(), text.casefold() if text.casefold() in IMPORTABLE_WORKSPACE_ROLES else None)
                if mapped == "owner" or text.casefold() in ("owner", "所有者", "擁有者", "拥有者"):
                    row["reason"] = "role_not_importable"; return row
                if mapped not in IMPORTABLE_WORKSPACE_ROLES:
                    unmapped_ws.add(text); row["reason"] = "unknown_workspace_role"; return row
                fields["workspace_role"] = mapped
            # organization roles: only through the explicit name → id mapping
            ids = list(default_org_roles)
            names_out: list[str] = []
            for part in ROLE_SPLIT.split(c["org_roles_text"]):
                part = part.strip()
                if not part:
                    continue
                rid = org_names.get(part.casefold())
                if rid is None:
                    if part.casefold() in ("owner", "admin", "所有者", "拥有者", "擁有者", "管理员", "管理員"):
                        row["reason"] = "role_not_importable"; return row
                    unmapped_org.add(part); row["reason"] = "unknown_org_role"; return row
                if rid not in ids:
                    ids.append(rid)
            for rid in ids:
                role = role_index[rid]
                names_out.append(role["name"])
                if not can_delegate or not set(role["permissions"]) <= actor_perms:
                    row["reason"] = "role_not_delegable"; return row
            fields["org_roles"], fields["org_role_ids"] = names_out, ids
            # the invitation service's own rule for this actor and workspace
            why = invite_check(fields["workspace_role"])
            if why:
                row["reason"] = why; return row
            user = self.ident.by_email(c["email"])
            if user and (user.get("deleted_at") or user["status"] not in ("active", "pending")):
                row["reason"] = "account_unavailable"; return row
            pending = self.store.one("SELECT id FROM invitations WHERE workspace_id=? AND email_key=? AND accepted_at IS NULL "
                                     "AND revoked_at IS NULL AND expires_at>?", (workspace_id, key, now()))
            if user:
                org_member = current_membership(self.store, oid, user["id"])
                ws_member = self.ident.membership(workspace_id, user["id"])
                if ws_member:
                    diff: dict = {}
                    cur_rank, new_rank = ROLE_RANK.get(ws_member["role"], 0), ROLE_RANK[fields["workspace_role"]]
                    if new_rank > cur_rank and ws_member["role"] != "owner":
                        diff["workspace_role"] = {"from": ws_member["role"], "to": fields["workspace_role"]}
                    if org_member:
                        have = {r["id"] for r in self.roles.assigned(oid, user["id"])}
                        add = [rid for rid in ids if rid not in have]
                        if add:
                            diff["org_roles"] = {"add": [role_index[r]["name"] for r in add], "add_ids": add}
                    row["fields"]["existing"] = {"workspace_role": ws_member["role"], "org_member": bool(org_member)}
                    if diff:
                        row["action"], row["reason"], row["diff"] = "update", None, diff
                    else:
                        row["action"], row["reason"] = "unchanged", "already_member"
                    return row
                if pending:
                    row["action"], row["reason"], row["invitation_id"] = "skip", "already_invited", pending["id"]; return row
                row["fields"]["existing"] = {"org_member": bool(org_member), "account": True}
                row["action"], row["reason"] = "invite", ("org_member_new_workspace" if org_member else "existing_account")
                return row
            if pending:
                row["action"], row["reason"], row["invitation_id"] = "skip", "already_invited", pending["id"]; return row
            row["action"], row["reason"] = "invite", "new_person"
            return row

        for i, c in enumerate(cands, start=1):
            row = decide(c, i)
            if row["action"] in ("invite", "update") and needs_review(c["source"]):
                # What it would do stays visible (reason, diff); it waits for a person's check.
                row["fields"]["would"] = {"action": row["action"], "reason": row["reason"]}
                row["action"], row["reason"] = "blocked", "ocr_unchecked"
            planned.append(row)
            counts[row["action"]] += 1
            if row["reason"]:
                reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
        summary = {"rows": len(planned), "counts": counts, "reasons": reasons,
                   "unmapped_workspace_roles": sorted(unmapped_ws)[:50], "unmapped_org_roles": sorted(unmapped_org)[:50],
                   "workspace": {"id": ws["id"], "name": ws["name"]}, "planned_at": now(),
                   "over_limit": max(0, len(cands) - MAX_ROWS)}
        ts = now()
        with self.store.tx() as t:
            t.execute("DELETE FROM member_import_rows WHERE import_id=?", (iid,))
            for row in planned:
                t.execute("INSERT INTO member_import_rows (id,import_id,seq,source,fields,email_key,action,reason,diff,status,invitation_id,org_role_ids,updated_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (new_id("mir"), iid, row["seq"], _dump(row["source"]), _dump(row["fields"]), row["email_key"], row["action"],
                           row["reason"], _dump(row["diff"]), "planned", row["invitation_id"], _dump(row["fields"].get("org_role_ids") or []), ts))
            t.execute("UPDATE member_imports SET mapping=?, workspace_id=?, summary=?, status='planned', updated_at=?, version=version+1 WHERE id=?",
                      (_dump(mapping), workspace_id, _dump(summary), ts, iid))
        return self.get(iid)

    # ------------------------------------------------------------- execution bookkeeping
    def confirm(self, iid: str, *, version: int | None, only_valid: bool) -> dict:
        imp = self.get(iid)
        if not imp:
            raise ServiceError(404, "no_import", "找不到该导入")
        if imp["status"] != "planned":
            raise ServiceError(409, "import_not_planned", "请先完成映射并查看预览")
        if version is not None and version != imp["version"]:
            raise ServiceError(409, "version_conflict", "预览已过期，请重新查看后再确认", {"version": imp["version"]})
        counts = (imp["summary"] or {}).get("counts") or {}
        if counts.get("blocked") and not only_valid:
            raise ServiceError(409, "rows_blocked", "有被阻断的行；修正文件或映射后重试，或选择只导入有效行", {"blocked": counts["blocked"]})
        if not (counts.get("invite") or counts.get("update")):
            raise ServiceError(409, "nothing_to_do", "没有需要邀请或更新的行")
        ts = now()
        with self.store.tx() as t:
            t.execute("UPDATE member_import_rows SET status='pending', updated_at=? WHERE import_id=? AND action IN ('invite','update')", (ts, iid))
            t.execute("UPDATE member_import_rows SET status='skipped', updated_at=? WHERE import_id=? AND action NOT IN ('invite','update')", (ts, iid))
            t.execute("UPDATE member_imports SET status='running', confirmed_at=?, updated_at=?, cancel_requested=0 WHERE id=?", (ts, ts, iid))
        return self.get(iid)

    def claim_row(self, iid: str) -> dict | None:
        with self.store.tx() as t:
            r = t.execute("SELECT * FROM member_import_rows WHERE import_id=? AND status='pending' ORDER BY seq LIMIT 1", (iid,)).fetchone()
            if not r:
                return None
            t.execute("UPDATE member_import_rows SET status='running', updated_at=? WHERE id=? AND status='pending'", (now(), r["id"]))
        return self._row_out(dict(r))

    def finish_row(self, row_id: str, status: str, result: dict | None, *, invitation_id: str | None = None) -> None:
        self.store.execute("UPDATE member_import_rows SET status=?, result=?, invitation_id=COALESCE(?, invitation_id), updated_at=? WHERE id=?",
                           (status, _dump(result), invitation_id, now(), row_id))

    def request_cancel(self, iid: str) -> dict:
        imp = self.get(iid)
        if not imp:
            raise ServiceError(404, "no_import", "找不到该导入")
        if imp["status"] != "running":
            raise ServiceError(409, "import_not_running", "只有执行中的导入可以取消")
        self.store.execute("UPDATE member_imports SET cancel_requested=1, updated_at=? WHERE id=?", (now(), iid))
        return self.get(iid)

    def finish(self, iid: str, *, cancelled: bool = False) -> dict:
        if cancelled:
            self.store.execute("UPDATE member_import_rows SET status='skipped', result=?, updated_at=? WHERE import_id=? AND status IN ('pending','running')",
                               (_dump({"reason": "cancelled"}), now(), iid))
        rows = self.rows(iid, limit=100000)
        done = sum(1 for r in rows if r["status"] == "done")
        failed = sum(1 for r in rows if r["status"] == "failed")
        skipped = sum(1 for r in rows if r["status"] == "skipped")
        pending = sum(1 for r in rows if r["status"] in ("pending", "running"))
        invited = sum(1 for r in rows if r["status"] == "done" and r["action"] == "invite")
        updated = sum(1 for r in rows if r["status"] == "done" and r["action"] == "update")
        status = "cancelled" if cancelled else ("failed" if done == 0 and failed else ("partial" if failed else "finished"))
        imp = self.get(iid)
        summary = dict(imp["summary"] or {})
        summary["outcome"] = {"done": done, "failed": failed, "skipped": skipped, "pending": pending, "invited": invited, "updated": updated}
        ts = now()
        self.store.execute("UPDATE member_imports SET status=?, summary=?, finished_at=?, updated_at=? WHERE id=?", (status, _dump(summary), ts, ts, iid))
        return self.get(iid)

    def retry_failed(self, iid: str) -> dict:
        imp = self.get(iid)
        if not imp or imp["status"] not in ("partial", "failed"):
            raise ServiceError(409, "nothing_to_retry", "只有部分失败或失败的导入可以重试")
        ts = now()
        with self.store.tx() as t:
            n = t.execute("UPDATE member_import_rows SET status='pending', result=NULL, updated_at=? WHERE import_id=? AND status='failed'", (ts, iid)).rowcount
            if not n:
                raise ServiceError(409, "nothing_to_retry", "没有失败的行")
            t.execute("UPDATE member_imports SET status='running', updated_at=?, cancel_requested=0 WHERE id=?", (ts, iid))
        return self.get(iid)

    # ------------------------------------------------------------- roles on acceptance
    def roles_for_invitation(self, invitation_id: str) -> dict | None:
        r = self.store.one("SELECT * FROM member_import_rows WHERE invitation_id=? AND action='invite' AND status='done'", (invitation_id,))
        return self._row_out(r) if r else None

    def on_invitation_accepted(self, invitation_id: str, user_id: str) -> dict | None:
        """Apply the organization roles the import specified — as the importer, under the rules
        that apply *now*: if they no longer may assign those roles, nothing is assigned and the
        row says so. Returns {"assigned": [...]} or None when the invitation was not an import."""
        row = self.roles_for_invitation(invitation_id)
        if not row:
            return None
        ids = row.get("org_role_ids") or []
        imp = self.get(row["import_id"])
        if not ids or not imp:
            return {"assigned": [], "import_id": row["import_id"]}
        result = dict(row.get("result") or {})
        try:
            out = self.roles.assign(imp["organization_id"], imp["created_by"], user_id, ids)
            result["roles"] = {"assigned": out.get("added", ids), "at": now()}
        except ServiceError as e:
            result["roles"] = {"refused": e.code, "at": now()}
        self.store.execute("UPDATE member_import_rows SET result=?, updated_at=? WHERE id=?", (_dump(result), now(), row["id"]))
        return {"assigned": result["roles"].get("assigned", []), "refused": result["roles"].get("refused"),
                "import_id": row["import_id"], "organization_id": imp["organization_id"], "actor_id": imp["created_by"]}

    # ------------------------------------------------------------- report and retention
    def report_rows(self, iid: str) -> list[list[str]]:
        head = ["seq", "source", "email", "name", "workspace_role", "org_roles", "action", "reason", "status", "result"]
        out = [head]
        for r in self.rows(iid, limit=100000):
            f = r.get("fields") or {}
            res = r.get("result") or {}
            out.append([str(r["seq"]), json.dumps(r.get("source") or {}, ensure_ascii=False), f.get("email", ""), f.get("name", ""),
                        f.get("workspace_role", ""), ", ".join(f.get("org_roles") or []), r.get("action") or "", r.get("reason") or "",
                        r.get("status") or "", (res.get("error") or res.get("reason") or ("invitation " + (r.get("invitation_id") or "")) if r.get("invitation_id") else res.get("note", ""))])
        return [[csv_safe(c) for c in row] for row in out]

    def sweep_expired(self) -> list[str]:
        """Delete the records of imports past their 7 days (never a running one) and return their
        ids, so the caller can remove the uploaded files too (the account service holds them)."""
        with self.store.tx() as t:
            ids = [r["id"] for r in t.execute("SELECT id FROM member_imports WHERE expires_at<? AND status NOT IN ('running')", (now(),)).fetchall()]
            for iid in ids:
                t.execute("DELETE FROM member_import_rows WHERE import_id=?", (iid,))
                t.execute("DELETE FROM member_imports WHERE id=?", (iid,))
        return ids


def csv_safe(value: str) -> str:
    """Spreadsheet formula injection: a cell starting with = + - @ or a tab/CR is prefixed with an
    apostrophe so a downloaded report never executes in the operator's spreadsheet."""
    s = "" if value is None else str(value)
    return ("'" + s) if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s
