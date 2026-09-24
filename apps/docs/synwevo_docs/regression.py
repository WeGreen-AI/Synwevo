"""Regression cases: freeze a working project together with its expected results, and
replay it with the current code.

A case is a plain directory::

    <case id>/
        case.json          rule, mappings, template slots, expectations, owning workspace
        template.<ext>     the template working copy (fields included)
        sources/NN_<name>  the uploaded source files, byte for byte — NOT desensitized

Where cases live, and who can see them:

* **Captured cases are workspace data.** They hold the workspace's own source files verbatim, so
  they are stored under the data directory (``<SYNWEVO_DATA_DIR>/regression/<workspace>/``, or
  ``$SYNWEVO_DOCS_REGRESSION_DIR/<workspace>/``) and every list / load / run / delete is scoped
  to one workspace. They used to be written into ``apps/docs/regression`` — inside the source
  tree, outside backups, one `git add` away from a commit — and listed, downloaded, replayed and
  deleted across every workspace.
* **Built-in cases** are synthetic fixtures committed under ``apps/docs/regression/``. They are
  read-only, run by the test suite and the CLI, and never offered through the HTTP API.

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

from synwevo_core import env
import re
import shutil
import time
from pathlib import Path

from .engine import generate as G
from .engine import records as R
from .engine import sources as S
from .engine.plan import build_plan
from .engine.resolve import ResolveError, SourceDoc

ROOT = Path(__file__).resolve().parents[1]   # apps/docs
CASE_VERSION = 1
MAX_SAMPLES = 50
_ID_RE = re.compile(r"[0-9A-Za-z._-]{1,80}")


def builtin_dir() -> Path:
    """Committed synthetic cases (test fixtures). Read-only; never written by capture."""
    return ROOT / "regression"


def cases_root(data_root: Path | str | None = None) -> Path:
    """Where captured (workspace) cases live: never inside the source tree."""
    configured = env.docs("REGRESSION_DIR")
    if configured:
        return Path(configured)
    if data_root is None:
        from synwevo_core.http import data_dir_default
        data_root = env.get("DATA_DIR") or data_dir_default()
    return Path(data_root) / "regression"


def _tenant_dirname(tenant: str) -> str:
    # Workspace ids are safe path components in practice; anything else is hashed so a crafted
    # tenant value can never climb out of the root.
    t = tenant or "default"
    if _ID_RE.fullmatch(t) and not t.startswith("."):
        return t
    return "t-" + hashlib.sha256(t.encode()).hexdigest()[:32]


def cases_dir(tenant: str, data_root: Path | str | None = None, create: bool = False) -> Path:
    d = cases_root(data_root) / _tenant_dirname(tenant)
    if create:
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
def capture(app, pid: str, name: str, note: str = "", sample_keys: list[str] | None = None,
            tenant: str | None = None) -> dict:
    from synwevo_core.context import tenant as current_tenant
    from .jobs import ProjectContext
    tenant = tenant or current_tenant()
    ctx = ProjectContext(app, pid)                   # 404s for another workspace's project
    if not ctx.template:
        raise RegressionError("no_template", "项目还没有模板，无法保存回归用例")
    recs = ctx.records["records"]
    if not recs:
        raise RegressionError("no_records", "没有识别到样本，无法保存回归用例")
    wanted = set(sample_keys or [])
    # A key saved before the per-group key format changed still names its sample.
    chosen = [r for r in recs if not wanted or r["key"] in wanted or R.legacy_record_key(r) in wanted][:MAX_SAMPLES]
    cid = time.strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(f"{pid}{time.time()}".encode()).hexdigest()[:6]
    d = cases_dir(tenant, app.store.root, create=True) / cid
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
            "workspace": tenant, "project_id": pid, "desensitized": False,
            "rule": ctx.rule, "mappings": ctx.mappings,
            "template": {"format": tpl_fmt, "name": ctx.template["name"], "file": tpl_file, "slots": ctx.slots, "fill_options": fill_opts},
            "sources": sources, "records_total": len(recs), "samples": samples}
    (d / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return summary(case)


def summary(case: dict) -> dict:
    # "desensitized" is only ever what the case says about itself; a captured case copies the
    # workspace's files as they are and says False.
    return {k: case.get(k) for k in ("id", "name", "note", "created_at", "project", "records_total")} | {
        "samples": len(case.get("samples", [])), "sources": [s["filename"] for s in case.get("sources", [])],
        "template": case.get("template", {}).get("name"), "format": case.get("template", {}).get("format"),
        "desensitized": bool(case.get("desensitized"))}


def _list_in(root: Path) -> list[dict]:
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*/case.json"), reverse=True):
        try:
            out.append(summary(json.loads(p.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return out


def list_cases(tenant: str, data_root: Path | str | None = None) -> list[dict]:
    """This workspace's captured cases — and only this workspace's."""
    return _list_in(cases_dir(tenant, data_root))


def list_builtin() -> list[dict]:
    return _list_in(builtin_dir())


def list_all(data_root: Path | str | None = None) -> list[dict]:
    """Operator view for the CLI on the host (it can read the data directory anyway): the
    built-in cases and every workspace's captured cases, each tagged with where it lives."""
    out = [{**c, "scope": "builtin"} for c in list_builtin()]
    root = cases_root(data_root)
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            out += [{**c, "scope": d.name} for c in _list_in(d)]
    return out


def _check_id(cid: str) -> None:
    if not _ID_RE.fullmatch(cid or "") or cid.startswith("."):
        raise RegressionError("bad_id", "用例编号无效")


def _load_from(d: Path) -> tuple[dict, Path]:
    p = d / "case.json"
    if not p.is_file():
        raise RegressionError("not_found", "回归用例不存在")
    return json.loads(p.read_text(encoding="utf-8")), d


def load(cid: str, tenant: str, data_root: Path | str | None = None) -> tuple[dict, Path]:
    """A case of this workspace. Another workspace's id is "not found", never "forbidden"."""
    _check_id(cid)
    case, d = _load_from(cases_dir(tenant, data_root) / cid)
    if case.get("workspace") not in (None, tenant):
        raise RegressionError("not_found", "回归用例不存在")
    return case, d


def load_builtin(cid: str) -> tuple[dict, Path]:
    _check_id(cid)
    return _load_from(builtin_dir() / cid)


def delete(cid: str, tenant: str, data_root: Path | str | None = None) -> None:
    _, d = load(cid, tenant, data_root)
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


def run(cid: str, tenant: str, data_root: Path | str | None = None) -> dict:
    case, d = load(cid, tenant, data_root)
    return replay(case, d)


def run_builtin(cid: str) -> dict:
    case, d = load_builtin(cid)
    return replay(case, d)


def run_any(cid: str, data_root: Path | str | None = None) -> dict:
    """CLI: a built-in case, or a captured case of any workspace (operator on the host)."""
    _check_id(cid)
    if (builtin_dir() / cid / "case.json").is_file():
        return run_builtin(cid)
    root = cases_root(data_root)
    for d in (sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []):
        if (d / cid / "case.json").is_file():
            return replay(*_load_from(d / cid))
    raise RegressionError("not_found", "回归用例不存在")


def replay(case: dict, d: Path) -> dict:
    cid = case.get("id") or d.name
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
        # Cases captured before the per-group key format changed stored the plain " / " join;
        # find_record still finds their sample, so a key-format change is not reported as a
        # behaviour change.
        rec = by_key.get(exp["key"]) or R.find_record(recs["records"], exp["key"])
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
