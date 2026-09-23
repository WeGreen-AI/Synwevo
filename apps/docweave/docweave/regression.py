"""Regression cases: freeze a working project (real, desensitized files) together with
its expected results, and replay it with the current code.

A case is a plain directory, so it can be committed next to the code and run in CI::

    regression/<case id>/
        case.json          rule, mappings, template slots, expectations
        template.<ext>     the template working copy (fields included)
        sources/NN_<name>  the uploaded source files, byte for byte

Replaying parses the stored sources again, rebuilds the samples, resolves every mapping
and fills the template – the same code path as preview and batch generation – then
compares, sample by sample: the value of every field, and the text of the produced
document.  Nothing is compared against a stored binary (dates and ids inside office files
change legitimately); what a user reads must not change.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from .engine import generate as G
from .engine import records as R
from .engine import sources as S
from .engine.plan import build_plan
from .engine.resolve import ResolveError, SourceDoc

ROOT = Path(__file__).resolve().parents[1]   # apps/docweave
CASE_VERSION = 1
MAX_SAMPLES = 50


def cases_dir() -> Path:
    d = Path(os.environ.get("DOCWEAVE_REGRESSION_DIR") or (ROOT / "regression"))
    d.mkdir(parents=True, exist_ok=True)
    return d


class RegressionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ rendering outputs as comparable text
def output_text(fmt: str, data: bytes) -> str:
    """What a reader sees, as normalised text (one line per paragraph / row / page line)."""
    import io
    if fmt == "docx":
        from .engine.docx_render import render_docx
        rend = render_docx(data)
        lines: list[str] = []

        def walk(blocks):
            for b in blocks:
                if b["t"] == "p":
                    lines.append(b["text"])
                else:
                    for row in b["rows"]:
                        cells = []
                        for c in row["cells"]:
                            sub: list[str] = []
                            for x in c["blocks"]:
                                if x["t"] == "p":
                                    sub.append(x["text"])
                            cells.append(" ".join(sub))
                        lines.append(" | ".join(cells))
        for part in rend["parts"]:
            lines.append(f"## {part['kind']}")
            walk(part["blocks"])
        return "\n".join(lines)
    if fmt == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        out = []
        for ws in wb.worksheets:
            out.append(f"## {ws.title}")
            for row in ws.iter_rows():
                vals = ["" if c.value is None else (repr(round(c.value, 9)) if isinstance(c.value, float) else str(c.value)) for c in row]
                while vals and vals[-1] == "":
                    vals.pop()
                if vals:
                    out.append(f"{row[0].row}: " + " | ".join(vals))
        return "\n".join(out)
    if fmt == "pdf":
        from .engine import pdf_engine as pe
        return pe.pdf_text(data)
    if fmt == "pptx":
        from .engine.pptx_engine import render_pptx
        rend = render_pptx(data)
        out = []
        for s in rend["slides"]:
            out.append(f"## slide {s['index'] + 1}")
            for sh in s["shapes"]:
                out.extend(p["text"] for p in sh.get("paras", []))
                for row in sh.get("rows", []):
                    out.append(" | ".join(" ".join(p["text"] for p in c["paras"]) for c in row))
        return "\n".join(out)
    if fmt in ("png", "jpg"):
        return f"<{fmt} {hashlib.sha256(data).hexdigest()[:12]}>"     # pixels: compared through the filled values
    return data.decode("utf-8", "ignore")


def _norm(text: str) -> str:
    return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines() if ln.strip())


def _values(plan, slots: list[dict]) -> dict:
    out = {}
    for s in slots:
        fv = plan.values.get(s["id"])
        if not fv:
            continue
        key = s["name"] if s["name"] not in out else f"{s['name']}#{s['id']}"
        if fv["kind"] == "scalar":
            out[key] = fv["value"].text
        elif fv["kind"] == "matrix":
            out[key] = [[c.text for c in r] for r in fv["rows"]]
        else:
            out[key] = [{k: v.text for k, v in r.items()} for r in fv["rows"]]
    return out


# ------------------------------------------------------------------ capture
def capture(app, pid: str, name: str, note: str = "", sample_keys: list[str] | None = None) -> dict:
    from .jobs import ProjectContext
    ctx = ProjectContext(app, pid)
    if not ctx.template:
        raise RegressionError("no_template", "项目还没有模板，无法保存回归用例")
    recs = ctx.records["records"]
    if not recs:
        raise RegressionError("no_records", "没有识别到样本，无法保存回归用例")
    chosen = [r for r in recs if not sample_keys or r["key"] in sample_keys][:MAX_SAMPLES]
    cid = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(f"{pid}{time.time()}".encode()).hexdigest()[:6]
    d = cases_dir() / cid
    (d / "sources").mkdir(parents=True)
    tpl_fmt = ctx.template["format"]
    native = G.native_format(tpl_fmt)
    work = ctx.working()
    tpl_file = f"template.{'pdf' if tpl_fmt in ('png', 'jpeg') else native}"
    (d / tpl_file).write_bytes(work)
    sources = []
    for i, r in enumerate(ctx.source_rows):
        safe = re.sub(r'[\\/:*?"<>|]', "_", r["filename"])
        fn = f"{i + 1:02d}_{safe}"
        (d / "sources" / fn).write_bytes(app.store.get_blob(r["blob"]))
        sources.append({"id": r["id"], "filename": r["filename"], "file": f"sources/{fn}", "options": r.get("options") or {},
                        "ocr": r.get("ocr") or {}, "corrections": r.get("corrections") or {}})
    fill_opts = app.fill_options(ctx.template)
    samples = []
    for rec in chosen:
        plan = ctx.plan(rec)
        entry = {"key": rec["key"], "label": rec["label"], "blocking": [i["code"] for i in plan.blocking]}
        if not plan.blocking:
            out, issues = G.fill_native(tpl_fmt, work, ctx.slots, plan, fill_opts)
            entry["issues"] = sorted({i.get("code", "") for i in issues if i.get("level") in ("error", "warning")})
            entry["values"] = _values(plan, ctx.slots)
            if out:
                txt = _norm(output_text(native, out))
                entry["text_sha1"] = hashlib.sha1(txt.encode()).hexdigest()
                entry["text"] = txt[:20000]
        samples.append(entry)
    case = {"version": CASE_VERSION, "id": cid, "name": name.strip()[:100] or ctx.project["name"], "note": note[:1000],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"), "project": ctx.project["name"],
            "rule": ctx.rule, "mappings": ctx.mappings,
            "template": {"format": tpl_fmt, "name": ctx.template["name"], "file": tpl_file, "slots": ctx.slots, "fill_options": fill_opts},
            "sources": sources, "records_total": len(recs), "samples": samples}
    (d / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary(case)


def summary(case: dict) -> dict:
    return {k: case.get(k) for k in ("id", "name", "note", "created_at", "project", "records_total")} | {
        "samples": len(case.get("samples", [])), "sources": [s["filename"] for s in case.get("sources", [])],
        "template": case.get("template", {}).get("name"), "format": case.get("template", {}).get("format")}


def list_cases() -> list[dict]:
    out = []
    for p in sorted(cases_dir().glob("*/case.json"), reverse=True):
        try:
            out.append(summary(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def load(cid: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"[0-9A-Za-z._-]{1,80}", cid or ""):
        raise RegressionError("bad_id", "用例编号无效")
    d = cases_dir() / cid
    p = d / "case.json"
    if not p.exists():
        raise RegressionError("not_found", "回归用例不存在")
    return json.loads(p.read_text(encoding="utf-8")), d


def delete(cid: str) -> None:
    _, d = load(cid)
    shutil.rmtree(d)


# ------------------------------------------------------------------ replay
def _source_doc(d: Path, s: dict) -> SourceDoc:
    data = (d / s["file"]).read_bytes()
    ps = S.parse_source(data, s["filename"], s.get("options") or {})
    if ps.status not in ("ready", "needs_ocr"):
        raise RegressionError("source_failed", f"来源「{s['filename']}」现在无法解析：{ps.error}")
    fmt = ps.format
    if ps.working_bytes:      # legacy formats are read through their converted OOXML, as in the app
        fmt = {"doc": "docx", "odt": "docx", "rtf": "docx", "ods": "xlsx", "ppt": "pptx", "odp": "pptx"}.get(fmt, fmt)
    return SourceDoc(s["id"], s["filename"], fmt, ps.family, ps.working_bytes or data, ps.workbook, s.get("ocr") or {}, s.get("corrections") or {})


def run(cid: str) -> dict:
    case, d = load(cid)
    t0 = time.time()
    res = {"id": cid, "name": case["name"], "ok": True, "samples": [], "errors": []}
    try:
        srcs = {s["id"]: _source_doc(d, s) for s in case["sources"]}
    except RegressionError as e:
        return {**res, "ok": False, "errors": [str(e)], "seconds": round(time.time() - t0, 2)}
    try:
        recs = R.build_records(list(srcs.values()), case["rule"])
    except (ValueError, ResolveError) as e:
        return {**res, "ok": False, "errors": [f"样本划分失败：{e}"], "seconds": round(time.time() - t0, 2)}
    if len(recs["records"]) != case.get("records_total"):
        res["errors"].append(f"样本数量变化：保存时 {case.get('records_total')}，现在 {len(recs['records'])}")
    by_key = {r["key"]: r for r in recs["records"]}
    tpl = case["template"]
    work = (d / tpl["file"]).read_bytes()
    native = G.native_format(tpl["format"])
    for exp in case["samples"]:
        item = {"key": exp["key"], "label": exp["label"], "ok": True, "diffs": []}
        rec = by_key.get(exp["key"])
        if rec is None:
            item.update(ok=False, diffs=["样本已不存在（样本标识变化）"])
            res["samples"].append(item)
            continue
        plan = build_plan(rec, case["rule"], srcs, case["mappings"], tpl["slots"])
        blocking = [i["code"] for i in plan.blocking]
        if blocking != exp.get("blocking", []):
            item["diffs"].append(f"错误状态变化：保存时 {exp.get('blocking') or '无错误'}，现在 {blocking or '无错误'}")
        if not plan.blocking and not exp.get("blocking"):
            vals = _values(plan, tpl["slots"])
            for k in sorted(set(vals) | set(exp.get("values", {}))):
                a, b = exp.get("values", {}).get(k), vals.get(k)
                if a != b:
                    item["diffs"].append(f"字段「{k}」：保存时 {json.dumps(a, ensure_ascii=False)[:200]} → 现在 {json.dumps(b, ensure_ascii=False)[:200]}")
            out, issues = G.fill_native(tpl["format"], work, tpl["slots"], plan, tpl.get("fill_options") or {})
            codes = sorted({i.get("code", "") for i in issues if i.get("level") in ("error", "warning")})
            if codes != exp.get("issues", []):
                item["diffs"].append(f"提示变化：保存时 {exp.get('issues')}，现在 {codes}")
            if out and exp.get("text_sha1"):
                txt = _norm(output_text(native, out))
                if hashlib.sha1(txt.encode()).hexdigest() != exp["text_sha1"]:
                    diff = list(difflib.unified_diff(exp.get("text", "").splitlines(), txt[:20000].splitlines(), "保存时", "现在", lineterm="", n=1))
                    item["diffs"].append("输出文字变化：\n" + "\n".join(diff[:60]))
        item["ok"] = not item["diffs"]
        res["ok"] = res["ok"] and item["ok"]
        res["samples"].append(item)
    res["ok"] = res["ok"] and not res["errors"]
    res["seconds"] = round(time.time() - t0, 2)
    res["passed"] = sum(1 for s in res["samples"] if s["ok"])
    return res
