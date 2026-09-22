"""Generation jobs: snapshot inputs, run items in a background thread, verify
outputs, keep state in SQLite so a page refresh or server restart can resume."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import secrets
import socket
import sqlite3
import tempfile
import threading
import time
import traceback
import zipfile
from pathlib import Path

from .engine import generate as G
from .engine import records as R
from .engine.plan import build_plan, static_check
from .engine.resolve import SourceDoc
from .context import tenant as current_tenant
from .services import App, ServiceError, not_found, safe_output_name
from .store import new_id, now

TOKEN_RE = re.compile(r"\{([^{}]{1,40})\}")


# ------------------------------------------------------------------ naming
def naming_tokens(slots: list[dict], columns: list[str] | None = None) -> list[str]:
    names = ["序号", "样本标识", "来源文件名", "工作表", "块号"]
    for s in slots:
        if s["kind"] not in ("repeat", "region") and s["name"] not in names:
            names.append(s["name"])
    for c in columns or []:
        if c and c not in names:
            names.append(c)
    return names


def render_name(pattern: str, record: dict, plan, slots: list[dict], index: int, width: int,
                column_value=None) -> tuple[str, list[str]]:
    """Build an output file name.  Tokens: built-ins, template field names, then source column
    headers (value of the current sample's row; first row of a group)."""
    warns = []
    by_name = {}
    for s in slots:
        fv = plan.values.get(s["id"]) if plan else None
        if fv and fv.get("kind") == "scalar":
            by_name.setdefault(s["name"], fv["value"].text)

    def sub(m):
        key = m.group(1).strip()
        if key == "序号":
            return str(index + 1).zfill(width)
        if key == "样本标识":
            return record.get("ident") or record.get("label", "")
        if key == "来源文件名":
            return (record.get("file_name") or "").rsplit(".", 1)[0]
        if key == "工作表":
            return record.get("sheet", "")
        if key == "块号":
            return str(record["block"] + 1) if record.get("block") is not None else ""
        if key in by_name:
            v = by_name[key]
            if not v:
                warns.append(f"文件名字段「{key}」为空")
            return v
        if column_value is not None:
            v = column_value(record, key)
            if v is not None:
                return v
        warns.append(f"文件名中的「{{{key}}}」不是可用字段")
        return ""
    name = TOKEN_RE.sub(sub, pattern or "{序号}")
    return safe_output_name(name.replace("\n", " ")), warns


def dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        k = n.lower()
        if k not in seen:
            seen[k] = 1
            out.append(n)
            continue
        i = seen[k] + 1
        while f"{n} ({i})".lower() in seen:
            i += 1
        seen[k] = i
        cand = f"{n} ({i})"
        seen[cand.lower()] = 1
        out.append(cand)
    return out


# ------------------------------------------------------------------ project context
class ProjectContext:
    """Everything needed to build plans for a project at a given moment."""

    def __init__(self, app: App, pid: str):
        self.app = app
        self.project = app.get_project(pid)
        self.rule = self.project.get("record_rule") or {}
        tid = self.project.get("template_id")
        self.template = app.get_template(tid) if tid else None
        self.version = app.get_version(tid, self.template["head"]) if self.template else None
        self.slots = (self.version or {}).get("slots") or []
        self.mappings = app.get_mappings(pid)["mappings"]
        self.source_rows = app.project_sources(pid)
        self.all_source_rows = app.project_sources(pid, ready_only=False)
        self.srcs: dict[str, SourceDoc] = {r["id"]: app._doc_from_row(r) for r in self.source_rows}
        self._records = None

    def signature(self) -> str:
        parts = [self.project["rev"], self.project["mapping_rev"], (self.template or {}).get("id"), (self.version or {}).get("version_no"),
                 json.dumps(self.rule, sort_keys=True), [(r["id"], r["version"], r["blob"]) for r in self.source_rows]]
        return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]

    @property
    def records(self) -> dict:
        if self._records is None:
            if not self.rule.get("mode"):
                raise ServiceError(400, "no_rule", "请先设置样本模式")
            try:
                self._records = R.build_records([self.srcs[r["id"]] for r in self.source_rows], self.rule)
            except ValueError as e:
                raise ServiceError(400, "bad_rule", str(e)) from e
            names = {r["id"]: r["filename"] for r in self.source_rows}
            for rec in self._records["records"]:
                rec["file_name"] = names.get(rec["file_ids"][0] if rec.get("file_ids") else rec["file_id"], "")
        return self._records

    def plan(self, record: dict):
        return build_plan(record, self.rule, self.srcs, self.mappings, self.slots)

    def column_headers(self) -> list[str]:
        return column_headers(self.rule, self.srcs, self.source_rows)

    def column_value(self, record: dict, header: str):
        return record_column_value(self.rule, self.srcs, record, header)

    def working(self) -> bytes:
        return self.app.store.get_blob(self.version["working_blob"])


def column_headers(rule: dict, srcs: dict, rows: list[dict]) -> list[str]:
    if rule.get("mode") not in ("per_row", "per_group") or not rule.get("table"):
        return []
    from .engine.resolve import ResolveError, table_view
    for r in rows:
        src = srcs.get(r["id"])
        if src is None or src.workbook_json is None:
            continue
        try:
            return [h for h in table_view(src, rule["table"]).headers if h]
        except ResolveError:
            continue
    return []


_TV_CACHE: dict = {}


def record_column_value(rule: dict, srcs: dict, record: dict, header: str):
    if rule.get("mode") not in ("per_row", "per_group"):
        return None
    from .engine import tabular
    from .engine.resolve import ResolveError, table_view
    fid = record.get("file_id")
    src = srcs.get(fid)
    if src is None or src.workbook_json is None:
        return None
    key = (src.file_id, repr(rule.get("table")))
    hit = _TV_CACHE.get(key)
    tv = hit[1] if hit and hit[0] is src else None      # identity check: never reuse a view of another file version
    if tv is None:
        try:
            tv = table_view(src, rule["table"])
        except ResolveError:
            return None
        if len(_TV_CACHE) > 64:
            _TV_CACHE.clear()
        _TV_CACHE[key] = (src, tv)
    idx = tv.header_index().get(tabular.normalize_header(header))
    if not idx or len(idx) > 1:
        return None
    row = record.get("row")
    if row is None and record.get("rows"):
        row = record["rows"][0][2]
    if row is None:
        return None
    return tv.sheet.cell(row, idx[0]).text


# ------------------------------------------------------------------ verification
def verify_output(fmt: str, data: bytes, slots: list[dict], plan) -> dict:
    """Reopen the produced file and check that filled values are present."""
    res = {"reopened": False, "checks": 0, "missing": [], "engine": None}
    try:
        if fmt == "docx":
            import docx
            d = docx.Document(io.BytesIO(data))
            from .engine.docx_render import render_docx
            rend = render_docx(data)
            text = "\n".join(_blocks_text(p["blocks"]) for p in rend["parts"])
            res.update(reopened=True, engine="python-docx " + getattr(docx, "__version__", ""), paragraphs=len(d.paragraphs))
        elif fmt == "xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data))
            text = "\n".join(str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value is not None)
            res.update(reopened=True, engine="openpyxl", sheets=len(wb.worksheets))
        elif fmt == "pdf":
            from .engine import pdf_engine as pe
            text = pe.pdf_text(data)
            res.update(reopened=True, engine={"pymupdf": "PyMuPDF", "pdfium": "PDFium"}[pe.ENGINE], pages=pe.page_count(data))
        elif fmt == "pptx":
            import pptx
            prs = pptx.Presentation(io.BytesIO(data))
            from .engine.pptx_engine import render_pptx
            rend = render_pptx(data)
            text = "\n".join(p["text"] for s in rend["slides"] for sh in s["shapes"] for p in sh.get("paras", [])) + "\n" + \
                "\n".join(p["text"] for s in rend["slides"] for sh in s["shapes"] for row in sh.get("rows", []) for c in row for p in c["paras"])
            res.update(reopened=True, engine="python-pptx", slides=len(prs.slides))
        elif fmt == "csv":
            text = data.decode("utf-8-sig")
            res.update(reopened=True, engine="csv")
        elif fmt in ("png", "jpg"):
            from PIL import Image
            im = Image.open(io.BytesIO(data))
            im.verify()
            im = Image.open(io.BytesIO(data))
            res.update(reopened=True, engine="Pillow", size=list(im.size),
                       note="图片中的文字无法直接读取：已在栅格化前的页面中逐值核对（未写入则该样本失败）")
            return res
        else:
            return res
    except Exception as e:  # noqa: BLE001
        res["error"] = f"输出文件无法重新打开：{e}"
        return res
    norm = _squash(text)
    for s in slots:
        fv = plan.values.get(s["id"]) if plan else None
        if not fv:
            continue
        vals = []
        if fv["kind"] == "scalar":
            vals = [fv["value"].text]
        elif fv["kind"] == "rows":
            vals = [v.text for row in fv["rows"][:50] for v in row.values()]
        elif fv["kind"] == "matrix":
            vals = [c.text for row in fv["rows"] for c in row]
        for v in vals:
            if not v or (fmt == "xlsx" and _numeric(v)):
                continue
            res["checks"] += 1
            if _squash(v) not in norm:
                res["missing"].append({"slot": s["name"], "value": v[:80]})
    return res


def _numeric(s: str) -> bool:
    return bool(re.fullmatch(r"-?[\d,]+(\.\d+)?", s.strip()))


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _blocks_text(blocks) -> str:
    out = []
    for b in blocks:
        if b["t"] == "p":
            out.append(b["text"])
        else:
            for row in b["rows"]:
                for c in row["cells"]:
                    out.append(_blocks_text(c["blocks"]))
    return "\n".join(out)


# ------------------------------------------------------------------ job service
LEASE_S = float(os.environ.get("DOCWEAVE_JOB_LEASE_S", "30"))
IDEMPOTENCY_TTL_S = float(os.environ.get("DOCWEAVE_IDEMPOTENCY_TTL_HOURS", "24")) * 3600
TERMINAL = ("completed", "completed_with_errors", "failed", "cancelled")
ACTIVE = ("queued", "running", "cancelling")
EVENT_VERSION = 1


def request_hash(obj) -> str:
    """Stable hash of a normalised request (key order and whitespace do not matter)."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class JobService:
    """Runs generation jobs in a background thread.

    Reliability model (see docs/integration/api.md「任务语义」):

    * a job is *claimed* with an atomic ``UPDATE … WHERE status='queued'`` and held with a
      lease that a heartbeat renews; a job whose lease expired (worker gone) is recovered:
      unfinished items go back to ``pending`` and the job to ``queued`` (or ``cancelled``);
    * an item's output files and its ``success`` status are published in one transaction –
      an interruption leaves either nothing or a complete result, never a half result;
    * the terminal status is written with a conditional update inside a transaction that also
      reads the cancel flag, so a cancel and the completion of the last item cannot both win;
    * every state change appends an event (``job_events``) that pollers and webhooks read.
    """

    def __init__(self, app: App, start: bool = True):
        self.app = app
        self.store = app.store
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None
        self.last_heartbeat = 0.0
        self.tmp_dir = Path(self.store.root) / "tmp"
        self.tmp_dir.mkdir(exist_ok=True)
        self.recover()
        if start:
            self._start_runner()
            self._supervisor = threading.Thread(target=self._supervise, name="job-supervisor", daemon=True)
            self._supervisor.start()

    # -------------------------------------------------------------- lifecycle
    def _start_runner(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def runner_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self):
        self._stop = True
        self._wake.set()

    def _supervise(self) -> None:
        """Heartbeat for held leases, recovery of jobs whose worker is gone, restart of a dead
        runner thread, expiry of idempotency keys and old temporary files."""
        tick = max(0.5, min(5.0, LEASE_S / 3))
        while not self._stop:
            try:
                t = now()
                self.store.execute("UPDATE jobs SET lease_until=? WHERE lease_owner=? AND status IN ('running','cancelling')",
                                   (t + LEASE_S, self.worker_id))
                self.last_heartbeat = t
                self.recover()
                if not self.runner_alive() and not self._stop:
                    self._start_runner()
                self.store.execute("DELETE FROM idempotency WHERE created_at < ?", (t - IDEMPOTENCY_TTL_S,))
                for f in self.tmp_dir.glob("*"):
                    if f.is_file() and f.stat().st_mtime < t - 3600:
                        f.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 - the supervisor must keep running; errors surface through /readyz
                traceback.print_exc()
            time.sleep(tick)

    def _lease_expired(self, j: dict, t: float) -> bool:
        owner = j.get("lease_owner")
        if not owner:
            return True                                     # pre-lease job or released lease
        if owner == self.worker_id:
            return False
        host, _, rest = owner.partition(":")
        pid = rest.partition(":")[0]
        if host == socket.gethostname() and pid.isdigit() and not _pid_alive(int(pid)):
            return True                                     # its process is gone: no need to wait for the lease
        return (j.get("lease_until") or 0) < t

    def recover(self) -> list[str]:
        """Resume jobs whose worker stopped: running items back to pending, the job back to
        the queue (or to ``cancelled`` when a cancel had been accepted)."""
        t = now()
        recovered = []
        rows = self.store.all("SELECT id, status, lease_owner, lease_until FROM jobs WHERE status IN ('running','cancelling')")
        for j in rows:
            if not self._lease_expired(j, t):
                continue
            with self.store.tx() as c:
                cur = c.execute("SELECT status, lease_owner, tenant, run_no FROM jobs WHERE id=?", (j["id"],)).fetchone()
                if cur is None or cur["status"] not in ("running", "cancelling") or cur["lease_owner"] != j["lease_owner"]:
                    continue                                # changed meanwhile
                c.execute("UPDATE job_items SET status='pending' WHERE job_id=? AND status='running'", (j["id"],))
                if cur["status"] == "cancelling":
                    self._finalize_tx(c, j["id"], expect_owner=cur["lease_owner"])
                else:
                    c.execute("UPDATE jobs SET status='queued', lease_owner=NULL, lease_until=NULL WHERE id=?", (j["id"],))
                    self._emit(c, j["id"], cur["tenant"], "job.recovered", {"status": "queued", "run_no": cur["run_no"],
                                                                             "previous_worker": bool(cur["lease_owner"])})
            recovered.append(j["id"])
        if recovered:
            self._wake.set()
        return recovered

    # -------------------------------------------------------------- events
    def _emit(self, c: sqlite3.Connection, jid: str, tenant: str | None, etype: str, data: dict) -> None:
        c.execute("INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data) VALUES (?,?,?,?,?,?,?)",
                  (new_id("ev"), jid, tenant or "default", etype, EVENT_VERSION, now(), json.dumps(data, ensure_ascii=False)))
        hook = getattr(self.app, "on_event", None)
        if hook:
            hook()

    def events(self, jid: str, after: int = 0, limit: int = 200) -> list[dict]:
        rows = self.store.all("SELECT seq, id, job_id, type, version, occurred_at, data FROM job_events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (jid, after, limit))
        return [{"seq": r["seq"], "id": r["id"], "type": r["type"], "version": r["version"], "job_id": r["job_id"],
                 "occurred_at": r["occurred_at"], "data": r["data"]} for r in rows]

    # -------------------------------------------------------------- summary before generation
    def summary(self, pid: str, formats: list[str] | None = None, naming: str | None = None) -> dict:
        ctx = ProjectContext(self.app, pid)
        if not ctx.template:
            raise ServiceError(400, "no_template", "请先选择或创建模板")
        recs = ctx.records
        valid, invalid, warnings = [], [], 0
        issues_by_code: dict[str, int] = {}
        sample_issues = []
        for rec in recs["records"]:
            pl = ctx.plan(rec)
            warnings += sum(1 for i in pl.issues if i["level"] == "warning")
            if pl.blocking:
                invalid.append(rec)
                for i in pl.blocking:
                    issues_by_code[i["code"]] = issues_by_code.get(i["code"], 0) + 1
                if len(sample_issues) < 50:
                    sample_issues.append({"record": rec["label"], "index": rec["index"], "key": rec["key"], "issues": pl.blocking[:3]})
            else:
                valid.append(rec)
        exports = self.app._exports_for(ctx.template["format"])
        fmts = formats or [G.native_format(ctx.template["format"])]
        fmt_info = []
        for f in fmts:
            ex = next((e for e in exports if e["format"] == f), None)
            if ex is None:
                fmt_info.append({"format": f, "available": False, "reason": f"不支持导出 {f.upper()}"})
            else:
                fmt_info.append(ex)
        static = static_check(ctx.mappings, ctx.slots, ctx.rule)
        pattern = naming or (ctx.project.get("output") or {}).get("naming") or "{序号}"
        names = []
        width = max(3, len(str(len(recs["records"]))))
        for rec in valid[:5]:
            nm, _ = render_name(pattern, rec, ctx.plan(rec), ctx.slots, rec["index"], width, ctx.column_value)
            names.append(nm)
        n_ok_formats = len([f for f in fmt_info if f.get("available")])
        return {"input_files": len(ctx.all_source_rows), "ready_files": len(ctx.source_rows),
                "file_errors": recs["file_errors"], "records": len(recs["records"]), "valid": len(valid), "invalid": len(invalid),
                "excluded": recs["excluded"], "warnings": warnings, "record_warnings": recs["warnings"],
                "issues_by_code": issues_by_code, "invalid_samples": sample_issues, "formats": fmt_info,
                "expected_outputs": len(valid) * n_ok_formats, "expected_if_block": len(recs["records"]) * n_ok_formats if not invalid else 0,
                "naming_examples": names, "static_issues": static, "tokens": naming_tokens(ctx.slots, ctx.column_headers()),
                "signature": ctx.signature(), "template_version": ctx.version["version_no"]}

    # -------------------------------------------------------------- idempotency
    def _idem_lookup(self, tenant: str, scope: str, key: str, h: str) -> str | None:
        row = self.store.one("SELECT request_hash, job_id, created_at FROM idempotency WHERE tenant=? AND scope=? AND key=?", (tenant, scope, key))
        if not row:
            return None
        if row["created_at"] < now() - IDEMPOTENCY_TTL_S:
            self.store.execute("DELETE FROM idempotency WHERE tenant=? AND scope=? AND key=?", (tenant, scope, key))
            return None
        if row["request_hash"] != h:
            raise ServiceError(409, "idempotency_conflict", "该幂等键已用于另一个不同内容的请求；请换一个新的幂等键，或发送与原请求完全相同的内容",
                               {"job_id": row["job_id"]})
        return row["job_id"]

    # -------------------------------------------------------------- create
    def create(self, pid: str, options: dict, tenant: str | None = None, idem_key: str | None = None, origin: str = "ui") -> dict:
        tenant = tenant or current_tenant()
        idem_key = idem_key or options.get("request_id")
        ctx = ProjectContext(self.app, pid)
        if not ctx.template:
            raise ServiceError(400, "no_template", "请先选择或创建模板")
        formats = options.get("formats") or [G.native_format(ctx.template["format"])]
        policy = options.get("error_policy", "block")
        if policy not in ("block", "skip"):
            raise ServiceError(400, "bad_error_policy", "error_policy 只能是 block 或 skip", {"field": "error_policy"})
        layout = options.get("layout") if options.get("layout") in ("off", "warn", "fit") else ((ctx.project.get("output") or {}).get("layout") or "off")
        pattern = options.get("naming") or (ctx.project.get("output") or {}).get("naming") or "{序号}"
        normalized = {"project": pid, "formats": list(formats), "error_policy": policy, "naming": pattern,
                      "keep_fields": bool(options.get("keep_fields")), "layout": layout}
        h = request_hash(normalized)
        scope = f"job:{pid}"
        if idem_key:
            existing = self._idem_lookup(tenant, scope, idem_key, h)
            if existing:
                return {**self.get(existing), "idempotent_replay": True}
        exports = {e["format"]: e for e in self.app._exports_for(ctx.template["format"])}
        for f in formats:
            if f not in exports or not exports[f].get("available"):
                reason = (exports.get(f) or {}).get("reason") or f"不支持导出 {f.upper()}"
                raise ServiceError(400, "format_unavailable", f"无法导出 {f.upper()}：{reason}", {"field": "formats"})
        recs = ctx.records
        if not recs["records"]:
            raise ServiceError(400, "no_records", "没有识别到任何样本，无法生成")
        from .engine.limits import LIMITS
        if len(recs["records"]) > LIMITS.max_records_per_job:
            raise ServiceError(400, "too_many", f"样本数 {len(recs['records'])} 超过单次任务上限 {LIMITS.max_records_per_job}")
        plans = [(rec, ctx.plan(rec)) for rec in recs["records"]]
        invalid = [(rec, pl) for rec, pl in plans if pl.blocking]
        if invalid and policy == "block":
            raise ServiceError(409, "has_errors", f"{len(invalid)} 个样本存在错误，已按「有错误则不开始」停止。请修正或选择「跳过错误样本」",
                               {"invalid": [{"record": r["label"], "key": r["key"], "issues": p.blocking[:3]} for r, p in invalid[:50]]})
        width = max(3, len(str(len(plans))))
        raw_names = [render_name(pattern, rec, pl, ctx.slots, rec["index"], width, ctx.column_value)[0] for rec, pl in plans]
        names = dedupe(raw_names)
        jid = new_id("j")
        snapshot = {
            "project_id": pid, "signature": ctx.signature(), "rule": ctx.rule, "mappings": ctx.mappings,
            "mapping_rev": ctx.project.get("mapping_rev"),
            "template": {"id": ctx.template["id"], "name": ctx.template["name"], "format": ctx.template["format"],
                         "version_no": ctx.version["version_no"], "working_blob": ctx.version["working_blob"], "slots": ctx.slots,
                         "fill_options": self.app.fill_options(ctx.template)},
            "sources": [{k: r.get(k) for k in ("id", "filename", "version", "blob", "working_blob", "parsed_blob", "format", "family",
                                              "ocr", "corrections")} for r in ctx.source_rows],
        }
        t = now()
        try:
            with self.store.tx() as c:
                if idem_key:
                    c.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, job_id, created_at) VALUES (?,?,?,?,?,?)",
                              (tenant, scope, idem_key, h, jid, t))
                c.execute("INSERT INTO jobs (id, project_id, status, created_at, snapshot, options, counts, request_id, tenant, run_no, origin) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (jid, pid, "queued", t, json.dumps(snapshot, ensure_ascii=False),
                           json.dumps({"formats": formats, "error_policy": policy, "naming": pattern,
                                       "keep_fields": bool(options.get("keep_fields")), "layout": layout}, ensure_ascii=False),
                           "{}", None, tenant, 1, origin))
                for (rec, pl), name in zip(plans, names):
                    status = "skipped" if pl.blocking else "pending"
                    c.execute("INSERT INTO job_items (id, job_id, seq, record_key, record, status, issues, file_base, label, run_no) VALUES (?,?,?,?,?,?,?,?,?,1)",
                              (new_id("i"), jid, rec["index"], rec["key"], json.dumps(rec, ensure_ascii=False, default=str), status,
                               json.dumps(pl.blocking if pl.blocking else [], ensure_ascii=False), name, rec["label"]))
                c.execute("INSERT INTO job_runs (job_id, run_no, trigger, requested_by, items, created_at, status) VALUES (?,?,?,?,?,?,?)",
                          (jid, 1, "create", origin, len(plans) - len(invalid), t, "queued"))
                self._update_counts(jid, c)
                self._emit(c, jid, tenant, "job.created", {"status": "queued", "run_no": 1, "samples": len(plans),
                                                            "skipped": len(invalid), "formats": formats})
        except sqlite3.IntegrityError:
            # a concurrent request with the same idempotency key won the race
            existing = self._idem_lookup(tenant, scope, idem_key or "", h)
            if existing:
                return {**self.get(existing), "idempotent_replay": True}
            raise
        self._wake.set()
        return self.get(jid)

    # -------------------------------------------------------------- queries
    def get(self, jid: str, tenant: str | None = None) -> dict:
        j = self.store.one("SELECT id, project_id, status, created_at, started_at, finished_at, options, counts, error, cancel_requested, "
                           "cancel_requested_at, request_id, tenant, run_no, origin FROM jobs WHERE id=?", (jid,))
        tenant = tenant or current_tenant()
        if not j or (j["tenant"] or "default") != tenant:
            not_found("任务")
        snap = self.store.one("SELECT snapshot FROM jobs WHERE id=?", (jid,))["snapshot"]
        j["template"] = {k: snap["template"][k] for k in ("id", "name", "format", "version_no")}
        j["source_count"] = len(snap["sources"])
        j["cancel_requested"] = bool(j["cancel_requested"])
        return j

    def items(self, jid: str, status: str | None = None, offset: int = 0, limit: int = 500) -> list[dict]:
        sql = "SELECT id, seq, record_key, label, status, issues, attempts, file_base, started_at, finished_at, run_no FROM job_items WHERE job_id=?"
        args: tuple = (jid,)
        if status:
            sql += " AND status=?"
            args += (status,)
        sql += " ORDER BY seq LIMIT ? OFFSET ?"
        args += (limit, offset)
        rows = self.store.all(sql, args)
        ids = [r["id"] for r in rows]
        by_item: dict[str, list] = {}
        if ids:
            arts = self.store.all(f"SELECT id, item_id, format, filename, blob, size, verification, run_no FROM artifacts "
                                  f"WHERE job_id=? AND deleted_at IS NULL AND item_id IN ({','.join('?' * len(ids))})", (jid, *ids))
            for a in arts:
                a["sha256"] = a.pop("blob")
                by_item.setdefault(a["item_id"], []).append(a)
        for r in rows:
            r["artifacts"] = by_item.get(r["id"], [])
        return rows

    def runs(self, jid: str) -> list[dict]:
        return self.store.all("SELECT run_no, trigger, requested_by, items, created_at, started_at, finished_at, status FROM job_runs WHERE job_id=? ORDER BY run_no", (jid,))

    def list_for_project(self, pid: str) -> list[dict]:
        return self.store.all("SELECT id, status, created_at, started_at, finished_at, counts, options, run_no FROM jobs "
                              "WHERE project_id=? AND tenant=? ORDER BY created_at DESC LIMIT 50", (pid, current_tenant()))

    # -------------------------------------------------------------- control
    def cancel(self, jid: str, tenant: str | None = None) -> dict:
        """Cancel.  queued → cancelled at once; running → cancelling (no new item starts; the item
        in progress finishes and keeps its result); terminal → unchanged, reported as such."""
        self.get(jid, tenant)
        accepted, reason = True, None
        with self.store.tx() as c:
            j = c.execute("SELECT status, tenant, run_no FROM jobs WHERE id=?", (jid,)).fetchone()
            st = j["status"]
            if st in TERMINAL:
                accepted, reason = False, "already_terminal"
            elif st == "queued":
                c.execute("UPDATE job_items SET status='cancelled' WHERE job_id=? AND status IN ('pending','running')", (jid,))
                c.execute("UPDATE jobs SET status='cancelled', cancel_requested=1, cancel_requested_at=?, finished_at=? WHERE id=?", (now(), now(), jid))
                c.execute("UPDATE job_runs SET status='cancelled', finished_at=? WHERE job_id=? AND run_no=?", (now(), jid, j["run_no"]))
                counts = self._update_counts(jid, c)
                self._emit(c, jid, j["tenant"], "job.cancelled", {"status": "cancelled", "run_no": j["run_no"], "counts": counts})
            elif st == "running":
                c.execute("UPDATE jobs SET status='cancelling', cancel_requested=1, cancel_requested_at=? WHERE id=?", (now(), jid))
                self._emit(c, jid, j["tenant"], "job.cancel_requested", {"status": "cancelling", "run_no": j["run_no"]})
            else:
                reason = "already_requested"                  # cancelling
        out = self.get(jid)
        out["cancel"] = {"accepted": accepted, "reason": reason}
        return out

    def retry(self, jid: str, include_skipped: bool = False, tenant: str | None = None, idem_key: str | None = None,
              requested_by: str = "ui") -> dict:
        j0 = self.get(jid, tenant)
        h = request_hash({"job": jid, "include_skipped": bool(include_skipped)})
        scope = f"retry:{jid}"
        if idem_key:
            existing = self._idem_lookup(j0["tenant"], scope, idem_key, h)
            if existing:
                return {**self.get(jid), "idempotent_replay": True}
        statuses = ("failed", "cancelled") + (("skipped",) if include_skipped else ())
        try:
            with self.store.tx() as c:
                j = c.execute("SELECT status, tenant, run_no FROM jobs WHERE id=?", (jid,)).fetchone()
                if j["status"] in ACTIVE:
                    raise ServiceError(409, "job_active", "任务仍在进行中；请等它结束（或取消）后再重试失败项")
                run_no = (j["run_no"] or 1) + 1
                n = c.execute(f"UPDATE job_items SET status='pending', run_no=? WHERE job_id=? AND status IN ({','.join('?' * len(statuses))})",
                              (run_no, jid) + statuses).rowcount
                if not n:
                    raise ServiceError(409, "nothing_to_retry", "没有失败或已取消的样本可重试")
                if idem_key:
                    c.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, job_id, created_at) VALUES (?,?,?,?,?,?)",
                              (j["tenant"], scope, idem_key, h, jid, now()))
                c.execute("UPDATE jobs SET status='queued', run_no=?, cancel_requested=0, cancel_requested_at=NULL, finished_at=NULL, error=NULL WHERE id=?",
                          (run_no, jid))
                c.execute("INSERT INTO job_runs (job_id, run_no, trigger, requested_by, items, created_at, status) VALUES (?,?,?,?,?,?,?)",
                          (jid, run_no, "retry", requested_by, n, now(), "queued"))
                counts = self._update_counts(jid, c)
                self._emit(c, jid, j["tenant"], "job.retry_requested", {"status": "queued", "run_no": run_no, "items": n, "counts": counts})
        except sqlite3.IntegrityError:
            existing = self._idem_lookup(j0["tenant"], scope, idem_key or "", h)
            if existing:
                return {**self.get(jid), "idempotent_replay": True}
            raise
        self._wake.set()
        return self.get(jid)

    def _update_counts(self, jid: str, c: sqlite3.Connection | None = None) -> dict:
        def run(c):
            rows = c.execute("SELECT status, COUNT(*) AS n FROM job_items WHERE job_id=? GROUP BY status", (jid,)).fetchall()
            cnt = {r["status"]: r["n"] for r in rows}
            cnt["total"] = sum(cnt.values())
            cnt["files"] = c.execute("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND deleted_at IS NULL", (jid,)).fetchone()["n"]
            cnt["documents"] = cnt.get("success", 0)
            c.execute("UPDATE jobs SET counts=? WHERE id=?", (json.dumps(cnt), jid))
            return cnt
        if c is not None:
            return run(c)
        with self.store.tx() as c2:
            return run(c2)

    # -------------------------------------------------------------- runner
    def _claim(self) -> str | None:
        row = self.store.one("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1")
        if not row:
            return None
        t = now()
        with self.store.tx() as c:
            n = c.execute("UPDATE jobs SET status='running', lease_owner=?, lease_until=?, started_at=COALESCE(started_at, ?) "
                          "WHERE id=? AND status='queued'", (self.worker_id, t + LEASE_S, t, row["id"])).rowcount
            if not n:
                return None                                  # another worker claimed it first
            j = c.execute("SELECT tenant, run_no FROM jobs WHERE id=?", (row["id"],)).fetchone()
            c.execute("UPDATE job_runs SET status='running', started_at=? WHERE job_id=? AND run_no=?", (t, row["id"], j["run_no"]))
            self._emit(c, row["id"], j["tenant"], "job.started", {"status": "running", "run_no": j["run_no"]})
        return row["id"]

    def _loop(self) -> None:
        while not self._stop:
            jid = self._claim()
            if not jid:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            try:
                self._run(jid)
            except Exception as e:  # noqa: BLE001 - job-level failure (inputs unreadable…): recorded, runner continues
                self._fail_job(jid, e)

    def _fail_job(self, jid: str, e: Exception) -> None:
        with self.store.tx() as c:
            j = c.execute("SELECT status, tenant, run_no, lease_owner FROM jobs WHERE id=?", (jid,)).fetchone()
            if not j or j["lease_owner"] != self.worker_id or j["status"] not in ("running", "cancelling"):
                return
            c.execute("UPDATE job_items SET status='failed', finished_at=?, issues=? WHERE job_id=? AND status IN ('pending','running')",
                      (now(), json.dumps([{"level": "error", "code": "job_error", "message": f"任务无法继续：{type(e).__name__}: {e}"}], ensure_ascii=False), jid))
            counts = self._update_counts(jid, c)
            c.execute("UPDATE jobs SET status='failed', error=?, finished_at=?, lease_owner=NULL, lease_until=NULL WHERE id=?",
                      (f"{type(e).__name__}: {e}", now(), jid))
            c.execute("UPDATE job_runs SET status='failed', finished_at=? WHERE job_id=? AND run_no=?", (now(), jid, j["run_no"]))
            self._emit(c, jid, j["tenant"], "job.failed", {"status": "failed", "run_no": j["run_no"], "counts": counts,
                                                            "error": f"{type(e).__name__}: {e}"})

    def _holds_lease(self, jid: str) -> bool:
        row = self.store.one("SELECT lease_owner FROM jobs WHERE id=?", (jid,))
        return bool(row) and row["lease_owner"] == self.worker_id

    def _finalize_tx(self, c: sqlite3.Connection, jid: str, expect_owner: str | None) -> str | None:
        """Decide the terminal status inside a transaction.  A cancel that was accepted cancels the
        items that have not started; if none were left, the job completes normally."""
        j = c.execute("SELECT status, tenant, run_no, cancel_requested, lease_owner FROM jobs WHERE id=?", (jid,)).fetchone()
        if not j or j["status"] not in ("running", "cancelling") or j["lease_owner"] != expect_owner:
            return None
        cancelled_now = 0
        if j["cancel_requested"]:
            cancelled_now = c.execute("UPDATE job_items SET status='cancelled' WHERE job_id=? AND status IN ('pending','running')", (jid,)).rowcount
        counts = self._update_counts(jid, c)
        if cancelled_now or (j["cancel_requested"] and counts.get("cancelled")):
            status = "cancelled"
        elif counts.get("success", 0) == 0:
            status = "failed"
        elif counts.get("failed") or counts.get("skipped") or counts.get("cancelled"):
            status = "completed_with_errors"
        else:
            status = "completed"
        t = now()
        c.execute("UPDATE jobs SET status=?, finished_at=?, lease_owner=NULL, lease_until=NULL WHERE id=?", (status, t, jid))
        c.execute("UPDATE job_runs SET status=?, finished_at=? WHERE job_id=? AND run_no=?", (status, t, jid, j["run_no"]))
        self._emit(c, jid, j["tenant"], f"job.{status}", {"status": status, "run_no": j["run_no"], "counts": counts})
        return status

    def _run(self, jid: str) -> None:
        j = self.store.one("SELECT * FROM jobs WHERE id=?", (jid,))
        snap, opts = j["snapshot"], j["options"]
        srcs = {}
        for s in snap["sources"]:
            srcs[s["id"]] = self.app._doc_from_row({**s, "family": s["family"]})
        tpl = snap["template"]
        work = self.store.get_blob(tpl["working_blob"])
        slots = tpl["slots"]
        native = G.native_format(tpl["format"])
        layout_mode = opts.get("layout") or "off"
        tpl_pages = G.template_pages(tpl["format"], work) if layout_mode != "off" else None
        run_no = j.get("run_no") or 1
        last_count = time.time()
        while not self._stop:
            ctl = self.store.one("SELECT cancel_requested, lease_owner FROM jobs WHERE id=?", (jid,))
            if ctl["lease_owner"] != self.worker_id:
                return                                        # lease lost (e.g. paused too long): another worker resumes
            if ctl["cancel_requested"]:
                break
            item = self.store.one("SELECT * FROM job_items WHERE job_id=? AND status='pending' ORDER BY seq LIMIT 1", (jid,))
            if item is None:
                break
            with self.store.tx() as c:
                n = c.execute("UPDATE job_items SET status='running', started_at=?, attempts=attempts+1, run_no=? WHERE id=? AND status='pending'",
                              (now(), run_no, item["id"])).rowcount
            if not n:
                continue
            attempt = item["attempts"] + 1
            try:
                self._process_item(jid, item, attempt, run_no, snap, opts, srcs, tpl, work, slots, native, layout_mode, tpl_pages)
            except Exception as e:  # noqa: BLE001 - item-level failure: recorded on the item
                self._item_done(item["id"], "failed", [{"level": "error", "code": "exception", "stage": "generate",
                                                         "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-800:]}])
            if time.time() - last_count > 0.3:
                self._update_counts(jid)
                last_count = time.time()
        if self._stop:
            return
        with self.store.tx() as c:
            self._finalize_tx(c, jid, self.worker_id)

    def _item_done(self, item_id: str, status: str, issues: list[dict]) -> None:
        self.store.execute("UPDATE job_items SET status=?, issues=?, finished_at=? WHERE id=? AND status='running'",
                           (status, json.dumps(issues, ensure_ascii=False), now(), item_id))

    def _process_item(self, jid, item, attempt, run_no, snap, opts, srcs, tpl, work, slots, native, layout_mode, tpl_pages) -> None:
        plan = build_plan(item["record"], snap["rule"], srcs, snap["mappings"], slots)
        if plan.blocking:
            self._item_done(item["id"], "failed", [{**i, "stage": i.get("stage", "resolve")} for i in plan.blocking])
            return
        out, issues, lay = G.fill_checked(tpl["format"], work, slots, plan, {**(tpl.get("fill_options") or {}), "keep_fields": opts.get("keep_fields")},
                                          layout_mode, tpl_pages)
        blocking = [i for i in issues if i.get("level") == "error"]
        if blocking or not out:
            self._item_done(item["id"], "failed", [{**i, "stage": i.get("stage", "fill")} for i in (blocking or issues)])
            return
        produced = []
        for f in opts["formats"]:
            try:
                data, conv_issues = G.convert_output(out, native, f)
                issues.extend(conv_issues)
            except Exception as e:  # noqa: BLE001
                self._item_done(item["id"], "failed", issues + [{"level": "error", "code": "export_failed", "stage": "export",
                                                                  "message": f"{f.upper()}：{e}"}])
                return
            ver = verify_output(f, data, slots if f == native else [], plan)
            if lay and f == native:
                ver["layout"] = lay
            problem = None if ver.get("reopened") else (ver.get("error") or "输出校验失败")
            if not problem and ver["missing"]:
                problem = "输出文件中找不到已填写的值：" + "、".join(f"{m['slot']}={m['value']}" for m in ver["missing"][:3])
            if problem:
                self._item_done(item["id"], "failed", issues + [{"level": "error", "code": "export_failed", "stage": "verify",
                                                                  "message": f"{f.upper()}：{problem}"}])
                return
            produced.append((f, self.store.put_blob(data), len(data), ver))  # content-addressed, written atomically
        # publish: output records and the success status become visible together, or not at all
        with self.store.tx() as c:
            live = c.execute("SELECT i.status, j.lease_owner FROM job_items i JOIN jobs j ON j.id=i.job_id WHERE i.id=?", (item["id"],)).fetchone()
            if not live or live["status"] != "running" or live["lease_owner"] != self.worker_id:
                return                                        # recovered by another worker meanwhile: do not publish twice
            for f, blob, size, ver in produced:
                c.execute("INSERT INTO artifacts (id, job_id, item_id, format, filename, blob, size, trace, verification, created_at, attempt, run_no) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (new_id("a"), jid, item["id"], f, f"{item['file_base']}.{f}", blob, size,
                           json.dumps({"record": {k: item["record"].get(k) for k in ("key", "label", "file_id", "file_name", "where", "rows")},
                                       "slots": plan.trace, "template_version": tpl["version_no"]}, ensure_ascii=False, default=str),
                           json.dumps(ver, ensure_ascii=False, default=str), now(), attempt, run_no))
            c.execute("UPDATE job_items SET status='success', issues=?, finished_at=? WHERE id=?",
                      (json.dumps([i for i in issues if i.get("level") != "info"][:20], ensure_ascii=False), now(), item["id"]))

    # -------------------------------------------------------------- downloads
    def artifact(self, aid: str, tenant: str | None = None) -> tuple[dict, bytes]:
        a = self.store.one("SELECT a.*, j.tenant FROM artifacts a JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (aid,))
        tenant = tenant or current_tenant()
        if not a or (a["tenant"] or "default") != tenant:
            not_found("输出文件")
        if a.get("deleted_at"):
            raise ServiceError(410, "artifact_deleted", "该输出文件已被删除，无法下载；可对原任务重新生成")
        if not self.store.has_blob(a["blob"]):
            raise ServiceError(410, "artifact_missing", "该输出文件的内容已不在存储中（可能已按保留期清理）")
        return a, self.store.get_blob(a["blob"])

    def zip_file(self, jid: str, fmt: str | None = None) -> Path:
        """Write the job's published outputs (+ manifest) to a temporary ZIP file and return its path;
        streaming from disk keeps memory flat for large jobs and supports range requests."""
        self.get(jid)
        arts = self.store.all("SELECT a.*, i.seq FROM artifacts a JOIN job_items i ON a.item_id=i.id "
                              "WHERE a.job_id=? AND a.deleted_at IS NULL AND i.status='success' ORDER BY i.seq, a.format", (jid,))
        fd, path = tempfile.mkstemp(prefix=f"{jid}-", suffix=".zip", dir=self.tmp_dir)
        os.close(fd)
        used = set()
        multi = len({x["format"] for x in arts}) > 1
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for a in arts:
                if fmt and a["format"] != fmt:
                    continue
                name = a["filename"]
                folder = f"{a['format']}/" if multi else ""
                full = folder + name
                k = 2
                while full.lower() in used:
                    base, _, ext = name.rpartition(".")
                    full = f"{folder}{base} ({k}).{ext}"
                    k += 1
                used.add(full.lower())
                z.write(self.store.blob_path(a["blob"]), full)
            z.writestr("生成清单.csv", self.report_csv(jid))
        return Path(path)

    def zip(self, jid: str, fmt: str | None = None) -> bytes:
        p = self.zip_file(jid, fmt)
        try:
            return p.read_bytes()
        finally:
            p.unlink(missing_ok=True)

    def report_csv(self, jid: str) -> bytes:
        items = self.items(jid, limit=1_000_000)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["序号", "样本", "状态", "输出文件", "来源文件", "来源位置", "问题说明"])
        status_cn = {"success": "成功", "failed": "失败", "skipped": "已跳过（生成前校验错误）", "cancelled": "已取消", "pending": "等待中", "running": "进行中"}
        for it in items:
            files = "；".join(a["filename"] for a in it["artifacts"])
            issues = "；".join(i.get("message", "") for i in (it["issues"] or []) if i.get("level") in ("error", "warning"))
            rec = self.store.one("SELECT record FROM job_items WHERE id=?", (it["id"],))["record"]
            w.writerow([it["seq"] + 1, it["label"], status_cn.get(it["status"], it["status"]), files, rec.get("file_name", ""),
                        rec.get("where", ""), issues])
        return ("﻿" + out.getvalue()).encode("utf-8")
