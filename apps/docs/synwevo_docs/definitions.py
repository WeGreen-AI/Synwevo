"""Declarative definitions: template fields and a project's rule + mappings as JSON.

The UI stores fields by structural position (paragraph path, cell reference, page
rectangle).  Automation needs definitions that can be written by hand or generated, so a
field may also be selected by the text it replaces (``find``) or by ordinals (n-th table,
n-th slide).  Both forms end in the *same* service call the UI uses – there is no second
implementation of field creation.

Every document carries ``schema`` and ``version``; unknown keys and unknown versions are
rejected with the offending field named, never ignored.

Imports are **all or nothing**. A field list creates every field or none, as one template
version; a project definition is validated completely (template, pinned version, fields, rule,
output, mapping shapes) before anything is written, and then written in one transaction. A
400 used to leave a project rebound to a new template with its old rule, or a template with
half of its fields and a version per field.
"""
from __future__ import annotations

import re

from synwevo_core.errors import ServiceError
from synwevo_core.store import now

from .services import App

TEMPLATE_FIELDS_SCHEMA = "docweave.template-fields"
PROJECT_SCHEMA = "docweave.project"
VERSION = 1
# One field-definition call creates its fields inside one store write transaction (see
# import_template_fields), so every other writer in the process waits for it. The bound keeps
# that wait short and predictable; a longer list is sent as several calls, each atomic.
MAX_FIELDS_PER_IMPORT = 100

# selector keys accepted per field kind (besides the shared ones)
SHARED = {"kind", "name", "required", "data_type", "format", "default", "share_binding_with"}
KIND_KEYS = {
    "text": {"find", "occurrence", "part", "path", "start", "end", "mode", "slide", "shape", "para"},
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


_MISSING = object()


def _int(spec: dict, key: str, default=_MISSING, minimum: int | None = 0) -> int:
    """An integer selector value, or 400 naming the key (never a KeyError/TypeError 500)."""
    v = spec.get(key, default)
    if v is _MISSING or v is None:
        if default is _MISSING:
            _bad(f"缺少 {key}", key)
        v = default
    if isinstance(v, bool) or not isinstance(v, (int, str)) or (isinstance(v, str) and not re.fullmatch(r"-?\d+", v.strip())):
        _bad(f"{key} 必须是整数", key)
    n = int(v)
    if minimum is not None and n < minimum:
        _bad(f"{key} 不能小于 {minimum}", key)
    return n


def _need(spec: dict, key: str):
    if spec.get(key) is None:
        _bad(f"缺少 {key}", key)
    return spec[key]


def _rect(spec: dict, key: str) -> list[float]:
    v = spec.get(key)
    if not isinstance(v, list) or len(v) != 4 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        _bad(f"{key} 必须是 4 个数字 [x0, y0, x1, y1]", key)
    return [float(x) for x in v]


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
    if not isinstance(spec.get("name"), str) or not spec["name"].strip():
        _bad("字段定义缺少 name", "name")
    fmt = app.get_template(tid)["format"]
    req: dict = {k: spec[k] for k in SHARED if k in spec}
    req["kind"] = kind
    occ = _int(spec, "occurrence", 1, minimum=1)
    if kind == "text":
        if fmt == "docx":
            if "find" in spec:
                req.update(_docx_find(app, tid, str(spec["find"]), occ, spec.get("part")))
            else:
                for k in ("path", "start", "end"):
                    if k not in spec:
                        _bad(f"docx 文字字段需要 find，或 path/start/end（缺少 {k}）", k)
                req.update({"part": spec.get("part"), "path": spec["path"], "start": _int(spec, "start"), "end": _int(spec, "end")})
            req["mode"] = spec.get("mode", "replace")
        elif fmt == "pptx":
            if "find" in spec:
                req.update(_pptx_find(app, tid, str(spec["find"]), occ))
            else:
                # Documented form: slide/shape/para/start/end. These keys used to be rejected as
                # unknown, and a spec without `find` crashed with a KeyError (a 500).
                for k in ("slide", "shape", "para", "start", "end"):
                    if spec.get(k) is None:
                        _bad(f"pptx 文字字段需要 find，或 slide/shape/para/start/end（缺少 {k}）", k)
                req.update({"slide": _int(spec, "slide"), "shape": spec["shape"], "para": _int(spec, "para"),
                            "start": _int(spec, "start"), "end": _int(spec, "end")})
        else:
            _bad(f"{fmt.upper()} 模板没有「文字字段」，请用 cell / pdf_box 等类型", "kind")
    elif kind == "cell":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "cell": _need(spec, "cell"), "mode": spec.get("mode", "replace")})
        elif fmt == "docx":
            req.update(_docx_table_path(app, tid, _int(spec, "table", 0), spec.get("part")))
            req.update({"row": _int(spec, "row"), "col": _int(spec, "col"), "mode": spec.get("mode", "replace")})
        elif fmt == "pptx":
            req.update({"slide": _int(spec, "slide"), "shape": _need(spec, "shape"), "row": _int(spec, "row"), "col": _int(spec, "col")})
        else:
            _bad(f"{fmt.upper()} 模板不支持 cell 字段", "kind")
    elif kind == "region":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "range": _need(spec, "range")})
        elif fmt == "docx":
            req.update(_docx_table_path(app, tid, _int(spec, "table", 0), spec.get("part")))
            req.update({k: _int(spec, k) for k in ("r1", "c1", "r2", "c2")})
        else:
            # Everything that was not XLSX used to be parsed as DOCX, so a PDF or PPTX template
            # got a docx-renderer error (or a 500) instead of this sentence.
            _bad(f"{fmt.upper()} 模板不支持 region 字段（仅 XLSX 与 DOCX 表格）", "kind")
    elif kind == "repeat":
        if fmt == "xlsx":
            req.update({"sheet": spec.get("sheet"), "row": _int(spec, "row"), "c1": _int(spec, "c1", 0), "c2": _int(spec, "c2", 0)})
            for k in ("header_row", "empty_policy", "placeholder"):
                if k in spec:
                    req[k] = spec[k]
        elif fmt == "docx":
            req.update(_docx_table_path(app, tid, _int(spec, "table", 0), spec.get("part")))
            req["row"] = _int(spec, "row")
            for k in ("header_rows", "empty_policy", "placeholder", "repeat_header"):
                if k in spec:
                    req[k] = spec[k]
        elif fmt == "pptx":
            req.update({"slide": _int(spec, "slide"), "shape": _need(spec, "shape"), "row": _int(spec, "row")})
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
            {"part": spec.get("part"), "path": _need(spec, "path"), "start": _int(spec, "start"), "end": _int(spec, "end")}
        req.update({"block_id": blk["id"], **loc})
    elif kind in ("pdf_box", "pdf_repeat"):
        if fmt not in ("pdf", "png", "jpeg"):
            _bad(f"{kind} 只用于 PDF / 图片模板", "kind")
        if "find" in spec:
            req.update(_pdf_find(app, tid, str(spec["find"]), occ, _int(spec, "page") if spec.get("page") is not None else None))
        elif "rect" in spec:
            req.update({"page": _int(spec, "page", 0), "rect": _rect(spec, "rect")})
        elif "nrect" in spec:
            req.update({"page": _int(spec, "page", 0), "nrect": _rect(spec, "nrect")})
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
    """Create every field of the document, or none of them, as **one** new template version.

    Each field is still created by the same service call the UI uses (`add_slot`), in order, so
    a later `find` sees the working copy the earlier fields produced. All of it runs in one
    store transaction: field N failing rolls back fields 1..N-1, and on success the
    intermediate versions — which nobody outside this transaction could have seen — are folded
    into a single version whose parent is the head the caller started from.

    The trade-off, stated: the transaction (and the store's in-process write lock) is held while
    each field is located and its working copy rendered, so other writers — the job runner
    publishing results included — wait for the whole call. Rendering outside the transaction
    would need `add_slot` to compute a field without writing a version, which it does not offer;
    until it does, the number of fields per call is capped (`MAX_FIELDS_PER_IMPORT`).
    """
    check_envelope(doc, TEMPLATE_FIELDS_SCHEMA)
    specs = doc.get("fields")
    if not isinstance(specs, list) or not specs:
        _bad("fields 必须是非空数组", "fields")
    if len(specs) > MAX_FIELDS_PER_IMPORT:
        _bad(f"一次最多定义 {MAX_FIELDS_PER_IMPORT} 个字段（收到 {len(specs)} 个）；请分几次提交，每次仍是全部成功或全部不变",
             "fields")
    unknown = set(doc) - {"schema", "version", "template", "fields"}
    if unknown:
        _bad(f"定义中有本服务不认识的键：{', '.join(sorted(unknown))}")
    app.get_template(tid)                                            # 404 before any work
    created = []
    with app.store.tx() as c:
        head0 = c.execute("SELECT head FROM templates WHERE id=?", (tid,)).fetchone()["head"]
        max0 = c.execute("SELECT MAX(version_no) AS m FROM template_versions WHERE template_id=?", (tid,)).fetchone()["m"] or 0
        for i, spec in enumerate(specs):
            label = spec.get("name", "") if isinstance(spec, dict) else ""
            try:
                req = resolve_field(app, tid, spec)
                detail = app.add_slot(tid, req)
            except ServiceError as e:
                raise ServiceError(e.status, e.code, f"第 {i + 1} 个字段「{label}」：{e.message}", e.detail) from e
            created.append(detail["created_slot"])
        if len(created) > 1:
            head = c.execute("SELECT head FROM templates WHERE id=?", (tid,)).fetchone()["head"]
            final = c.execute("SELECT working_blob, slots, editor FROM template_versions WHERE template_id=? AND version_no=?",
                              (tid, head)).fetchone()
            c.execute("DELETE FROM template_versions WHERE template_id=? AND version_no>?", (tid, max0))
            names = "、".join(x["name"] for x in created[:5]) + ("…" if len(created) > 5 else "")
            c.execute("INSERT INTO template_versions (template_id, version_no, parent_no, working_blob, slots, editor, note, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (tid, max0 + 1, head0, final["working_blob"], final["slots"], final["editor"],
                                                   f"按定义添加 {len(created)} 个字段：{names}", now()))
            c.execute("UPDATE templates SET head=?, updated_at=? WHERE id=?", (max0 + 1, now(), tid))
    return {"template": app.template_detail(tid)["version"]["no"],
            "created": [{"id": x["id"], "name": x["name"], "kind": x["kind"]} for x in created]}


# ------------------------------------------------------------------ project definition
def export_project(app: App, pid: str) -> dict:
    p = app.get_project(pid)
    out = {"schema": PROJECT_SCHEMA, "version": VERSION, "project": {"id": p["id"], "name": p["name"]},
           "rule": p.get("record_rule") or {}, "mappings": app.get_mappings(pid)["mappings"],
           "output": p.get("output") or {}}
    if p.get("template_id"):
        d = app.template_detail(p["template_id"])
        pinned = p.get("template_version")
        # The fields listed are those of the version the project actually uses: a pinned
        # project's mappings refer to the pinned version, not to whatever head is now.
        slots = app.get_version(d["id"], pinned)["slots"] if pinned else d["version"]["slots"]
        out["template"] = {"id": d["id"], "name": d["name"], "format": d["format"],
                           "version": pinned or d["version"]["no"], "pinned": bool(pinned),
                           "fields": [{"id": s["id"], "name": s["name"], "kind": s["kind"], "binding": s.get("binding")}
                                      for s in slots or []]}
    return out


def import_project(app: App, pid: str, doc: dict, base_rev: int | None = None) -> dict:
    """Validate the whole definition, then apply it in one transaction.

    `template.version` pins the project to that version when `template.pinned` is true, or
    when `pinned` is absent (definitions exported before the flag existed always meant the
    version they carried). `base_rev` is the caller's view of the mapping revision; a different
    current revision is `409 stale`, as for PUT /mappings.
    """
    check_envelope(doc, PROJECT_SCHEMA)
    unknown = set(doc) - {"schema", "version", "project", "template", "rule", "mappings", "output"}
    if unknown:
        _bad(f"定义中有本服务不认识的键：{', '.join(sorted(unknown))}")
    p = app.get_project(pid)

    # ---- validate everything before writing anything
    tpl = doc.get("template")
    if tpl is not None and not isinstance(tpl, dict):
        _bad("template 必须是对象", "template")
    tpl = tpl or {}
    binding = None
    if tpl.get("id"):
        t = app.get_template(tpl["id"])                               # 404 when it does not exist
        version = tpl.get("version")
        pin = tpl.get("pinned", version is not None)
        if not isinstance(pin, bool):
            _bad("template.pinned 必须是 true 或 false", "template.pinned")
        if pin:
            if version is None:
                _bad("固定模板版本（pinned）时必须给出 template.version", "template.version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                _bad("template.version 必须是正整数", "template.version")
            if not app.store.one("SELECT 1 AS x FROM template_versions WHERE template_id=? AND version_no=?", (t["id"], version)):
                _bad(f"模板 {t['id']} 没有版本 {version}", "template.version")
        use = version if pin else t["head"]
        have = {s["id"] for s in (app.get_version(t["id"], use)["slots"] or [])}
        fields = tpl.get("fields", [])
        if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
            _bad("template.fields 必须是对象数组", "template.fields")
        missing = [str(f.get("id")) for f in fields if f.get("id") not in have]
        if missing:
            _bad(f"模板 {t['id']} 的{'版本 ' + str(use) if pin else '当前版本'}缺少定义中的字段：{', '.join(missing[:5])}；"
                 "请先导入字段定义", "template.fields")
        binding = {"template_id": t["id"], "template_version": version if pin else None}
    rule = None
    if "rule" in doc:
        rule = doc["rule"] or {}
        app._validate_rule(rule)
    output = None
    if "output" in doc:
        if not isinstance(doc["output"], dict):
            _bad("output 必须是对象", "output")
        output = doc["output"]
    maps = None
    if "mappings" in doc:
        maps = doc["mappings"]
        if not isinstance(maps, list):
            _bad("mappings 必须是数组", "mappings")
        for i, m in enumerate(maps):
            if not isinstance(m, dict) or not isinstance(m.get("sources"), list):
                _bad(f"第 {i + 1} 个映射必须是带 sources 数组的对象", "mappings")
    if base_rev is not None and base_rev != p["mapping_rev"]:
        raise ServiceError(409, "stale", "映射已被修改（mapping_rev 不一致），请重新读取后再导入", {"mapping_rev": p["mapping_rev"]})

    # ---- apply atomically
    with app.store.tx():
        if binding:
            app.update_project(pid, binding)
        if rule is not None:
            app.update_project(pid, {"record_rule": rule})
        if output is not None:
            app.update_project(pid, {"output": output})
        if maps is not None:
            app.save_mappings(pid, maps, "导入定义", base_rev)
    return export_project(app, pid)
