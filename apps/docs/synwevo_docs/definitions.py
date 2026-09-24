"""Declarative definitions: template fields and a project's rule + mappings as JSON.

The UI stores fields by structural position (paragraph path, cell reference, page
rectangle).  Automation needs definitions that can be written by hand or generated, so a
field may also be selected by the text it replaces (``find``) or by ordinals (n-th table,
n-th slide).  Both forms end in the *same* service call the UI uses – there is no second
implementation of field creation.

Every document carries ``schema`` and ``version``; unknown keys and unknown versions are
rejected with the offending field named, never ignored.
"""
from __future__ import annotations

import re

from synwevo_core.errors import ServiceError

from .services import App

TEMPLATE_FIELDS_SCHEMA = "docweave.template-fields"
PROJECT_SCHEMA = "docweave.project"
VERSION = 1

# selector keys accepted per field kind (besides the shared ones)
SHARED = {"kind", "name", "required", "data_type", "format", "default", "share_binding_with"}
KIND_KEYS = {
    "text": {"find", "occurrence", "part", "path", "start", "end", "mode"},
    "cell": {"sheet", "cell", "mode", "table", "row", "col", "part", "slide", "shape"},
    "region": {"sheet", "range", "table", "r1", "c1", "r2", "c2", "part"},
    "repeat": {"sheet", "row", "c1", "c2", "table", "header_rows", "header_row", "empty_policy", "placeholder",
               "repeat_header", "slide", "shape"},
    "repeat_block": {"first_find", "last_find", "first", "last", "part", "auto_columns", "separator", "empty_policy", "placeholder"},
    "block_column": {"block", "find", "occurrence", "part", "path", "start", "end"},
    "pdf_box": {"page", "rect", "nrect", "find", "occurrence", "font_size", "min_font_size", "align", "valign", "clear", "color"},
    "pdf_field": {"field", "font_size", "min_font_size", "align"},
    "pdf_repeat": {"page", "rect", "nrect", "find", "occurrence", "layout", "overflow", "empty_policy", "placeholder",
                   "font_size", "min_font_size", "min_row_scale", "color"},
}


def _bad(msg: str, field: str | None = None):
    raise ServiceError(400, "invalid_definition", msg, {"field": field} if field else None)


def check_envelope(doc: dict, schema: str) -> None:
    if not isinstance(doc, dict):
        _bad("定义必须是 JSON 对象")
    if doc.get("schema") != schema:
        _bad(f"schema 应为 {schema!r}，收到 {doc.get('schema')!r}", "schema")
    v = doc.get("version")
    if v != VERSION:
        _bad(f"不兼容的定义版本 {v!r}；本服务支持 version={VERSION}。请用当前版本重新导出定义", "version")


# ------------------------------------------------------------------ locating by text / ordinal
def _docx_blocks(app: App, tid: str):
    from .engine.docx_render import render_docx
    t = app.get_template(tid)
    v = app.get_version(tid, t["head"])
    return render_docx(app.store.get_blob(v["working_blob"]))


def _walk(blocks):
    for b in blocks:
        yield b
        if b["t"] == "tbl":
            for row in b["rows"]:
                for c in row["cells"]:
                    yield from _walk(c["blocks"])


def _docx_find(app: App, tid: str, text: str, occurrence: int, part_kind: str | None):
    hits = []
    for part in _docx_blocks(app, tid)["parts"]:
        if part_kind and part["kind"] != part_kind:
            continue
        for b in _walk(part["blocks"]):
            if b["t"] != "p":
                continue
            start = 0
            while True:
                i = b["text"].find(text, start)
                if i < 0:
                    break
                hits.append({"part": b["part"], "path": b["path"], "start": i, "end": i + len(text)})
                start = i + 1
    if not hits:
        _bad(f"模板中找不到文字「{text}」", "find")
    if occurrence > len(hits):
        _bad(f"文字「{text}」只出现 {len(hits)} 次，找不到第 {occurrence} 次", "occurrence")
    return hits[occurrence - 1]


def _docx_table_path(app: App, tid: str, index: int, part_kind: str | None):
    n = 0
    for part in _docx_blocks(app, tid)["parts"]:
        if part_kind and part["kind"] != part_kind:
            continue
        for b in part["blocks"]:
            if b["t"] == "tbl":
                if n == index:
                    return {"part": b["part"], "table_path": b["path"]}
                n += 1
    _bad(f"模板中没有第 {index + 1} 个表格", "table")


def _pdf_find(app: App, tid: str, text: str, occurrence: int, page: int | None):
    from .engine import pdf_engine as pe
    t = app.get_template(tid)
    v = app.get_version(tid, t["head"])
    data = app.store.get_blob(v["working_blob"])
    pages = range(pe.page_count(data)) if page is None else [page]
    hits = []
    for p in pages:
        words = pe.page_words(data, p)["words"]
        joined = ""
        spans = []
        for w in words:
            spans.append((len(joined), len(joined) + len(w["t"]), w))
            joined += w["t"] + " "
        for m in re.finditer(re.escape(text.replace(" ", "")), joined.replace(" ", "")):
            covered = []
            pos = 0
            for a, b, w in spans:
                ln = len(w["t"])
                if pos + ln > m.start() and pos < m.end():
                    covered.append(w)
                pos += ln
            if covered:
                x0 = min(w["u"][0] for w in covered) - 1
                y0 = min(w["u"][1] for w in covered) - 1
                x1 = max(w["u"][2] for w in covered) + 1
                y1 = max(w["u"][3] for w in covered) + 1
                hits.append({"page": p, "rect": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]})
    if not hits:
        _bad(f"PDF 模板中找不到文字「{text}」", "find")
    if occurrence > len(hits):
        _bad(f"文字「{text}」只出现 {len(hits)} 次", "occurrence")
    return hits[occurrence - 1]


def _pptx_find(app: App, tid: str, text: str, occurrence: int):
    from .engine.pptx_engine import render_pptx
    t = app.get_template(tid)
    v = app.get_version(tid, t["head"])
    hits = []
    for s in render_pptx(app.store.get_blob(v["working_blob"]))["slides"]:
        for sh in s["shapes"]:
            for pi, p in enumerate(sh.get("paras", [])):
                i = p["text"].find(text)
                if i >= 0:
                    hits.append({"slide": s["index"], "shape": sh["id"], "para": pi, "start": i, "end": i + len(text)})
    if not hits:
        _bad(f"幻灯片模板中找不到文字「{text}」", "find")
    if occurrence > len(hits):
        _bad(f"文字「{text}」只出现 {len(hits)} 次", "occurrence")
    return hits[occurrence - 1]


# ------------------------------------------------------------------ field selector → add_slot request
def resolve_field(app: App, tid: str, spec: dict) -> dict:
    if not isinstance(spec, dict):
        _bad("每个字段定义必须是 JSON 对象", "fields")
    kind = spec.get("kind")
    if kind not in KIND_KEYS:
        _bad(f"未知的字段类型 {kind!r}；支持：{', '.join(sorted(KIND_KEYS))}", "kind")
    unknown = set(spec) - SHARED - KIND_KEYS[kind]
    if unknown:
        _bad(f"字段定义中有本服务不认识的键：{', '.join(sorted(unknown))}", "fields")
    if not str(spec.get("name") or "").strip():
        _bad("字段定义缺少 name", "name")
    fmt = app.get_template(tid)["format"]
    req: dict = {k: spec[k] for k in SHARED if k in spec}
    req["kind"] = kind
    occ = int(spec.get("occurrence", 1) or 1)
    if kind == "text":
        if fmt == "docx":
            if "find" in spec:
                req.update(_docx_find(app, tid, str(spec["find"]), occ, spec.get("part")))
            else:
                for k in ("path", "start", "end"):
                    if k not in spec:
                        _bad(f"docx 文字字段需要 find，或 path/start/end（缺少 {k}）", k)
                req.update({"part": spec.get("part"), "path": spec["path"], "start": int(spec["start"]), "end": int(spec["end"])})
            req["mode"] = spec.get("mode", "replace")
        elif fmt == "pptx":
            req.update(_pptx_find(app, tid, str(spec["find"]), occ) if "find" in spec else
                       {k: spec[k] for k in ("slide", "shape", "para", "start", "end")})
        else:
            _bad(f"{fmt.upper()} 模板没有「文字字段」，请用 cell / pdf_box 等类型", "kind")
    elif kind == "cell":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "cell": spec["cell"], "mode": spec.get("mode", "replace")})
        elif fmt == "docx":
            req.update(_docx_table_path(app, tid, int(spec.get("table", 0)), spec.get("part")))
            req.update({"row": int(spec["row"]), "col": int(spec["col"]), "mode": spec.get("mode", "replace")})
        elif fmt == "pptx":
            req.update({"slide": spec["slide"], "shape": spec["shape"], "row": int(spec["row"]), "col": int(spec["col"])})
        else:
            _bad(f"{fmt.upper()} 模板不支持 cell 字段", "kind")
    elif kind == "region":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "range": spec["range"]})
        else:
            req.update(_docx_table_path(app, tid, int(spec.get("table", 0)), spec.get("part")))
            req.update({k: int(spec[k]) for k in ("r1", "c1", "r2", "c2")})
    elif kind == "repeat":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "row": int(spec["row"]), "c1": int(spec.get("c1", 0)), "c2": int(spec.get("c2", 0))})
            for k in ("header_row", "empty_policy", "placeholder"):
                if k in spec:
                    req[k] = spec[k]
        elif fmt == "docx":
            req.update(_docx_table_path(app, tid, int(spec.get("table", 0)), spec.get("part")))
            req["row"] = int(spec["row"])
            for k in ("header_rows", "empty_policy", "placeholder", "repeat_header"):
                if k in spec:
                    req[k] = spec[k]
        elif fmt == "pptx":
            req.update({"slide": spec["slide"], "shape": spec["shape"], "row": int(spec["row"])})
        else:
            _bad(f"{fmt.upper()} 模板的重复明细请用 pdf_repeat", "kind")
    elif kind == "repeat_block":
        if fmt != "docx":
            _bad("重复内容块只用于 DOCX 模板", "kind")
        first = spec.get("first") or (_docx_find(app, tid, str(spec["first_find"]), occ, spec.get("part")) if "first_find" in spec else None)
        last = spec.get("last") or (_docx_find(app, tid, str(spec["last_find"]), 1, spec.get("part")) if "last_find" in spec else None)
        if not first or not last:
            _bad("重复内容块需要 first_find/last_find（或 first/last 路径）", "first_find")
        req.update({"part": (first or {}).get("part") if isinstance(first, dict) else spec.get("part"),
                    "first": first["path"] if isinstance(first, dict) else first,
                    "last": last["path"] if isinstance(last, dict) else last})
        for k in ("auto_columns", "separator", "empty_policy", "placeholder"):
            if k in spec:
                req[k] = spec[k]
    elif kind == "block_column":
        block = spec.get("block")
        slots = app.template_detail(tid)["version"]["slots"]
        blk = next((s for s in slots if s.get("block") and (s["id"] == block or s["name"] == block)), None)
        if not blk:
            _bad(f"找不到重复内容块「{block}」", "block")
        loc = _docx_find(app, tid, str(spec["find"]), occ, spec.get("part")) if "find" in spec else \
            {"part": spec.get("part"), "path": spec["path"], "start": int(spec["start"]), "end": int(spec["end"])}
        req.update({"block_id": blk["id"], **loc})
    elif kind in ("pdf_box", "pdf_repeat"):
        if fmt not in ("pdf", "png", "jpeg"):
            _bad(f"{kind} 只用于 PDF / 图片模板", "kind")
        if "find" in spec:
            req.update(_pdf_find(app, tid, str(spec["find"]), occ, spec.get("page")))
        elif "rect" in spec:
            req.update({"page": int(spec.get("page", 0)), "rect": [float(x) for x in spec["rect"]]})
        elif "nrect" in spec:
            req.update({"page": int(spec.get("page", 0)), "nrect": [float(x) for x in spec["nrect"]]})
        else:
            _bad("PDF 字段需要 rect（页面坐标，单位点）、nrect（页面比例）或 find（按文字定位）", "rect")
        for k in ("font_size", "min_font_size", "align", "valign", "clear", "color", "layout", "overflow",
                  "empty_policy", "placeholder", "min_row_scale"):
            if k in spec:
                req[k] = spec[k]
    elif kind == "pdf_field":
        req.update({k: spec[k] for k in ("field", "font_size", "min_font_size", "align") if k in spec})
    return req


# ------------------------------------------------------------------ template fields
def export_template_fields(app: App, tid: str) -> dict:
    d = app.template_detail(tid)
    fields = []
    for s in d["version"]["slots"]:
        fields.append({k: s[k] for k in ("id", "name", "kind", "binding", "required", "data_type", "format", "default")
                       if s.get(k) is not None} | {"locator": s.get("locator"), "columns": s.get("columns")})
    return {"schema": TEMPLATE_FIELDS_SCHEMA, "version": VERSION,
            "template": {"id": d["id"], "name": d["name"], "format": d["format"], "version": d["version"]["no"]},
            "fields": [{k: v for k, v in f.items() if v is not None} for f in fields]}


def import_template_fields(app: App, tid: str, doc: dict) -> dict:
    check_envelope(doc, TEMPLATE_FIELDS_SCHEMA)
    specs = doc.get("fields")
    if not isinstance(specs, list) or not specs:
        _bad("fields 必须是非空数组", "fields")
    unknown = set(doc) - {"schema", "version", "template", "fields"}
    if unknown:
        _bad(f"定义中有本服务不认识的键：{', '.join(sorted(unknown))}")
    created = []
    for i, spec in enumerate(specs):
        try:
            req = resolve_field(app, tid, spec)
            detail = app.add_slot(tid, req)
        except ServiceError as e:
            raise ServiceError(e.status, e.code, f"第 {i + 1} 个字段「{spec.get('name', '')}」：{e.message}", e.detail) from e
        created.append(detail["created_slot"])
    return {"template": app.template_detail(tid)["version"]["no"], "created": [{"id": c["id"], "name": c["name"], "kind": c["kind"]} for c in created]}


# ------------------------------------------------------------------ project definition
def export_project(app: App, pid: str) -> dict:
    p = app.get_project(pid)
    out = {"schema": PROJECT_SCHEMA, "version": VERSION, "project": {"id": p["id"], "name": p["name"]},
           "rule": p.get("record_rule") or {}, "mappings": app.get_mappings(pid)["mappings"],
           "output": p.get("output") or {}}
    if p.get("template_id"):
        d = app.template_detail(p["template_id"])
        out["template"] = {"id": d["id"], "name": d["name"], "format": d["format"],
                           "version": p.get("template_version") or d["version"]["no"],
                           "fields": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "binding": s.get("binding")}
                                      for s in d["version"]["slots"]]}
    return out


def import_project(app: App, pid: str, doc: dict) -> dict:
    check_envelope(doc, PROJECT_SCHEMA)
    unknown = set(doc) - {"schema", "version", "project", "template", "rule", "mappings", "output"}
    if unknown:
        _bad(f"定义中有本服务不认识的键：{', '.join(sorted(unknown))}")
    tpl = doc.get("template") or {}
    if tpl.get("id"):
        app.get_template(tpl["id"])                                  # 404 when it does not exist
        values = {"template_id": tpl["id"]}
        if tpl.get("version"):
            values["template_version"] = int(tpl["version"])
        app.update_project(pid, values)
        have = {s["id"] for s in app.template_detail(tpl["id"])["version"]["slots"]}
        missing = [f["id"] for f in tpl.get("fields", []) if f.get("id") not in have]
        if missing:
            _bad(f"模板 {tpl['id']} 当前版本缺少定义中的字段：{', '.join(missing[:5])}；请先导入字段定义", "template.fields")
    if "rule" in doc:
        app.update_project(pid, {"record_rule": doc["rule"] or {}})
    if "output" in doc and isinstance(doc["output"], dict):
        app.update_project(pid, {"output": doc["output"]})
    if "mappings" in doc:
        if not isinstance(doc["mappings"], list):
            _bad("mappings 必须是数组", "mappings")
        app.save_mappings(pid, doc["mappings"], "导入定义")
    return export_project(app, pid)
