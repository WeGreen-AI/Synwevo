"""Application services shared by the HTTP API and background workers."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError, not_found
from synwevo_core.store import Store, new_id, now

from . import authz
from .engine import sources as S
from .engine import templates as T
from .engine.detect import FAMILY
from .engine.limits import LIMITS
from .engine.resolve import SourceDoc

PALETTE = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed", "#0891b2", "#dc2626", "#65a30d", "#c026d3",
           "#0d9488", "#ea580c", "#4f46e5", "#be123c", "#15803d", "#a16207", "#0369a1"]


class App:
    def __init__(self, store: Store):
        self.store = store
        self.parse_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="parse")
        self._src_cache: dict[tuple, SourceDoc] = {}
        self._cache_lock = threading.Lock()
        self.on_event = None          # set by the API layer: called after a job event is stored

    # ================================================================ projects
    def create_project(self, name: str, origin: str | None = None) -> dict:
        """``origin`` records who created this project ("ui", "api:<key id>", "test:<run id>").
        It is the only evidence a cleanup tool can act on — a name prefix is not proof."""
        pid = new_id("p")
        t = now()
        self.store.insert("projects", {"id": pid, "name": (name or "未命名项目").strip()[:100], "created_at": t, "updated_at": t,
                                       "tenant": current_tenant(), "origin": (origin or "ui")[:80],
                                       "record_rule": {}, "output": {"naming": "{序号}", "formats": [], "error_policy": "block"},
                                       "mapping_rev": 0, "rev": 1, "step": "sources"})
        self.store.insert("mapping_revs", {"project_id": pid, "rev": 0, "parent_rev": None, "data": [], "note": "初始", "created_at": t})
        return self.get_project(pid)

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        """Three queries regardless of project count.

        This used to run a COUNT and a last-job SELECT per project inside a Python loop —
        81 SQLite round-trips for 40 projects, growing linearly with the list."""
        tenant = current_tenant()
        arch = "" if include_archived else " AND archived_at IS NULL"
        rows = self.store.all(
            "SELECT id, name, created_at, updated_at, template_id, record_rule, step, origin, archived_at "
            f"FROM projects WHERE deleted=0 AND tenant=?{arch} ORDER BY updated_at DESC", (tenant,))
        if not rows:
            return rows
        counts = {r["project_id"]: r["n"] for r in self.store.all(
            "SELECT project_id, COUNT(*) AS n FROM source_files WHERE removed=0 GROUP BY project_id")}
        last = {r["project_id"]: r for r in self.store.all(
            "SELECT id, project_id, status, counts, created_at FROM ("
            "  SELECT id, project_id, status, counts, created_at,"
            "         ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at DESC, id DESC) AS rn"
            "  FROM jobs) WHERE rn = 1")}
        names = {r["id"]: r["name"] for r in self.store.all(
            "SELECT id, name FROM templates WHERE deleted=0 AND tenant=?", (tenant,))}
        for r in rows:
            r["sources"] = counts.get(r["id"], 0)
            j = last.get(r["id"])
            r["last_job"] = {k: j[k] for k in ("id", "status", "counts", "created_at")} if j else None
            r["template_name"] = names.get(r.get("template_id"))
        return rows

    def get_project(self, pid: str) -> dict:
        p = self.store.one("SELECT * FROM projects WHERE id=? AND deleted=0", (pid,))
        if not p or (p.get("tenant") or "default") != current_tenant():
            not_found("项目")          # a foreign tenant gets 404, never "exists but forbidden"
        return p

    def update_project(self, pid: str, values: dict) -> dict:
        self.get_project(pid)
        allowed = {k: v for k, v in values.items() if k in ("name", "record_rule", "output", "template_id", "step")}
        if "archived" in values:
            # Archiving only hides a project from the default list; it stays fully usable and
            # reversible.  Deleting is the separate, destructive `deleted` flag.
            allowed["archived_at"] = now() if values["archived"] else None
        if "record_rule" in allowed:
            self._validate_rule(allowed["record_rule"])
        if "template_id" in allowed and allowed["template_id"]:
            self.get_template(allowed["template_id"])
        allowed["updated_at"] = now()
        self.store.update("projects", {"id": pid}, allowed)
        if set(allowed) - {"name", "step", "updated_at", "output"}:
            self.store.bump_project(pid)
        return self.get_project(pid)

    def delete_project(self, pid: str) -> None:
        self.get_project(pid)
        self.store.update("projects", {"id": pid}, {"deleted": 1, "updated_at": now()})

    @staticmethod
    def _validate_rule(rule: dict) -> None:
        if not isinstance(rule, dict):
            raise ServiceError(400, "bad_rule", "样本规则格式错误")
        mode = rule.get("mode")
        if mode and mode not in ("per_row", "per_file", "per_group", "per_block"):
            raise ServiceError(400, "bad_rule", "未知的样本模式")
        if mode == "per_block":
            spec = rule.get("block") or {}
            if spec.get("match") not in (None, "contains", "exact", "starts", "regex"):
                raise ServiceError(400, "bad_rule", "标记匹配方式无效")
            if spec.get("match") == "regex":
                import re as _re
                try:
                    _re.compile(spec.get("anchor") or "")
                except _re.error as e:
                    raise ServiceError(400, "bad_rule", f"标记的正则表达式无效：{e}") from e

    # ================================================================ sources
    def add_source(self, pid: str, filename: str, data: bytes) -> dict:
        self.get_project(pid)
        if len(data) > LIMITS.max_file_bytes:
            raise ServiceError(413, "too_large", f"文件「{filename}」超过 {LIMITS.max_file_bytes // (1024 * 1024)} MB 上限")
        filename = safe_display_name(filename)
        blob = self.store.put_blob(data)
        sid = new_id("s")
        mx = self.store.one("SELECT MAX(ord) AS m FROM source_files WHERE project_id=?", (pid,))["m"] or 0
        t = now()
        self.store.insert("source_files", {"id": sid, "project_id": pid, "filename": filename, "ord": mx + 1, "blob": blob,
                                           "size": len(data), "status": "parsing", "notes": [], "structure": {},
                                           "options": {}, "version": 1, "created_at": t, "updated_at": t,
                                           "ocr": {}, "corrections": {}})
        has_rep = self.store.one("SELECT 1 AS x FROM source_files WHERE project_id=? AND representative=1 AND removed=0", (pid,))
        if not has_rep:
            self.store.update("source_files", {"id": sid}, {"representative": 1})
        self.store.bump_project(pid)
        self.parse_pool.submit(self._parse_source, sid)
        return self.get_source(sid)

    def _parse_source(self, sid: str) -> None:
        row = self.store.one("SELECT * FROM source_files WHERE id=?", (sid,))
        if not row:
            return
        try:
            data = self.store.get_blob(row["blob"])
            ps = S.parse_source(data, row["filename"], row.get("options") or {})
            values = {"format": ps.format, "family": ps.family, "status": ps.status, "error": ps.error, "notes": ps.notes,
                      "structure": ps.structure, "signature": ps.signature, "updated_at": now()}
            if ps.workbook is not None:
                values["parsed_blob"] = self.store.put_json_blob(ps.workbook)
            if ps.working_bytes is not None:
                values["working_blob"] = self.store.put_blob(ps.working_bytes)
            self.store.update("source_files", {"id": sid}, values)
        except Exception as e:  # noqa: BLE001
            self.store.update("source_files", {"id": sid}, {"status": "error", "error": f"解析失败：{e}", "updated_at": now()})
        self.store.bump_project(row["project_id"])

    def list_sources(self, pid: str) -> list[dict]:
        # get_project carries the tenant check and 404s, so the listing cannot enumerate another
        # workspace's files — which it did, sha256 included.
        self.get_project(pid)
        rows = self.store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,))
        return [self._source_out(r) for r in rows]

    def _source_out(self, r: dict) -> dict:
        out = {k: r.get(k) for k in ("id", "project_id", "filename", "ord", "size", "format", "family", "status", "error", "notes",
                                     "structure", "version", "representative", "created_at", "updated_at", "options")}
        out["representative"] = bool(r.get("representative"))
        out["ocr_pages"] = sorted(int(k) for k in (r.get("ocr") or {}).keys())
        out["corrections"] = r.get("corrections") or {}
        out["sha256"] = r.get("blob")
        return out

    def get_source(self, sid: str, raw: bool = False) -> dict:
        """Scoped by the project the file belongs to.

        This looked the row up by id alone, and source_files has no tenant column of its own, so
        every caller — view, download, search, page image, per-word text, OCR, delete — reached
        across workspaces. Not-found rather than forbidden, so an id cannot be probed for
        existence.
        """
        r = authz.source_in_scope(self.store, sid)
        if not r:
            not_found("来源文件")
        return r if raw else self._source_out(r)

    def remove_source(self, sid: str) -> None:
        r = self.get_source(sid, raw=True)
        self.store.update("source_files", {"id": sid}, {"removed": 1, "representative": 0, "updated_at": now()})
        if r["representative"]:
            nxt = self.store.one("SELECT id FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord LIMIT 1", (r["project_id"],))
            if nxt:
                self.store.update("source_files", {"id": nxt["id"]}, {"representative": 1})
        self.store.bump_project(r["project_id"])

    def reorder_sources(self, pid: str, ids: list[str]) -> list[dict]:
        self.get_project(pid)      # the per-row WHERE pins the project but never checked it is ours
        for i, sid in enumerate(ids):
            self.store.execute("UPDATE source_files SET ord=? WHERE id=? AND project_id=?", (i + 1, sid, pid))
        self.store.bump_project(pid)
        return self.list_sources(pid)

    def set_representative(self, sid: str) -> dict:
        r = self.get_source(sid, raw=True)
        self.store.execute("UPDATE source_files SET representative=0 WHERE project_id=?", (r["project_id"],))
        self.store.update("source_files", {"id": sid}, {"representative": 1})
        return self.get_source(sid)

    def set_source_options(self, sid: str, options: dict) -> dict:
        r = self.get_source(sid, raw=True)
        opts = dict(r.get("options") or {})
        opts.update(options or {})
        self.store.update("source_files", {"id": sid}, {"options": opts, "status": "parsing", "version": r["version"] + 1,
                                                        "updated_at": now()})
        self.store.bump_project(r["project_id"])
        self._parse_source(sid)
        return self.get_source(sid)

    def source_doc(self, sid: str) -> SourceDoc:
        r = self.get_source(sid, raw=True)
        return self._doc_from_row(r)

    def doc_for(self, r: dict, block: int | None = None) -> SourceDoc:
        """The source document, or one of its form blocks when the project splits files into blocks."""
        doc = self._doc_from_row(r)
        if block is None:
            return doc
        rule = self.get_project(r["project_id"]).get("record_rule") or {}
        if rule.get("mode") != "per_block" or not rule.get("block"):
            raise ServiceError(400, "not_block_mode", "当前样本模式不是「每块一份」，没有表单块")
        from .engine import blocks as B
        from .engine.resolve import ResolveError
        try:
            bl = B.split(doc, rule["block"])
        except ResolveError as e:
            raise ServiceError(400, e.code, str(e)) from e
        if not 0 <= int(block) < len(bl):
            raise ServiceError(404, "block_missing", f"「{r['filename']}」只有 {len(bl)} 个表单块")
        return bl[int(block)].doc

    def desensitize_sources(self, pid: str, body: dict) -> dict:
        """Desensitized copies of source files (local, consistent pseudonyms across the files)."""
        from .engine import desensitize as D
        self.get_project(pid)
        ids = body.get("file_ids") or [r["id"] for r in self.project_sources(pid, ready_only=False)]
        sess = D.Session(seed=body.get("seed"), words=body.get("words") or [], patterns=body.get("patterns"))
        out, skipped = [], []
        for sid in ids:
            r = self.get_source(sid, raw=True)
            if r["project_id"] != pid:
                continue
            try:
                data, ext = D.desensitize(self.store.get_blob(r["blob"]), r["filename"], sess, body.get("columns") or {})
            except (D.DesensitizeError, ValueError) as e:
                skipped.append({"file": r["filename"], "reason": str(e)})
                continue
            stem = r["filename"].rsplit(".", 1)[0]
            name = f"脱敏_{stem}.{ext}"
            item = {"source": r["filename"], "filename": name, "blob": self.store.put_blob(data), "size": len(data)}
            if body.get("add_to_project"):
                item["source_id"] = self.add_source(pid, name, data)["id"]
            out.append(item)
        # the random seed is deliberately not returned: with it, replaced digits could be brute-forced back
        return {"files": out, "skipped": skipped, "counts": sess.counts, "mapping_size": len(sess.map)}

    def source_blocks(self, sid: str) -> dict:
        r = self.get_source(sid, raw=True)
        rule = self.get_project(r["project_id"]).get("record_rule") or {}
        if rule.get("mode") != "per_block" or not rule.get("block"):
            return {"blocks": [], "mode": rule.get("mode")}
        from .engine import blocks as B
        from .engine.resolve import ResolveError
        try:
            bl = B.split(self._doc_from_row(r), rule["block"])
        except ResolveError as e:
            return {"blocks": [], "error": str(e), "code": e.code}
        return {"blocks": [{"index": b.index, "label": b.label, "where": b.where} for b in bl], "mode": "per_block"}

    def _doc_from_row(self, r: dict) -> SourceDoc:
        key = (r["id"], r["version"], r.get("parsed_blob"), r.get("working_blob"), json.dumps(r.get("ocr") or {}, sort_keys=True)[:64],
               json.dumps(r.get("corrections") or {}, sort_keys=True))
        with self._cache_lock:
            if key in self._src_cache:
                return self._src_cache[key]
        data = self.store.get_blob(r["working_blob"] or r["blob"])
        wb = self.store.get_json_blob(r["parsed_blob"]) if r.get("parsed_blob") else None
        fmt = r["format"]
        if r.get("working_blob"):
            fmt = {"doc": "docx", "odt": "docx", "rtf": "docx", "ods": "xlsx", "ppt": "pptx", "odp": "pptx"}.get(fmt, fmt)
        doc = SourceDoc(r["id"], r["filename"], fmt, r["family"] or FAMILY.get(fmt, "unknown"), data, wb,
                        r.get("ocr") or {}, r.get("corrections") or {})
        with self._cache_lock:
            if len(self._src_cache) > 64:
                self._src_cache.clear()
            self._src_cache[key] = doc
        return doc

    def project_sources(self, pid: str, ready_only: bool = True) -> list[dict]:
        rows = self.store.all("SELECT * FROM source_files WHERE project_id=? AND removed=0 ORDER BY ord", (pid,))
        if ready_only:
            rows = [r for r in rows if r["status"] in ("ready", "needs_ocr")]
        return rows

    # ================================================================ templates
    def list_templates(self) -> list[dict]:
        rows = self.store.all("SELECT * FROM templates WHERE deleted=0 AND tenant=? ORDER BY updated_at DESC", (current_tenant(),))
        out = []
        for r in rows:
            v = self.store.one("SELECT slots FROM template_versions WHERE template_id=? AND version_no=?", (r["id"], r["head"]))
            out.append({**self._template_out(r), "slot_count": len((v or {}).get("slots") or [])})
        return out

    def _template_out(self, r: dict) -> dict:
        out = {k: r.get(k) for k in ("id", "name", "kind", "format", "original_name", "head", "created_at", "updated_at", "info")}
        img = (out.get("info") or {}).get("image")
        if img:   # the ICC profile can be large; clients only need to know it is kept
            out["info"] = {**out["info"], "image": {k: v for k, v in img.items() if k not in ("icc", "original_blob")} | {"icc": bool(img.get("icc"))}}
        return out

    @staticmethod
    def fill_options(template_row: dict) -> dict:
        """Template-specific options every fill needs (image size / DPI / ICC for image templates).

        Metadata only: this is stored verbatim in a job snapshot, so the image's pixels must
        not go in here.  Use :meth:`with_image_bytes` at fill time to attach them."""
        img = (template_row.get("info") or {}).get("image")
        return {"image": img} if img else {}

    def with_image_bytes(self, opts: dict) -> dict:
        """Attach an image template's original pixels, resolved from the blob store.

        The raster fill path edits the picture itself, so it needs the bytes; the snapshot
        only carries the blob id, which keeps job snapshots small and stable."""
        img = (opts or {}).get("image") or {}
        blob = img.get("original_blob")
        if not blob or opts.get("image_bytes"):
            return opts
        try:
            return {**opts, "image_bytes": self.store.get_blob(blob)}
        except Exception:  # noqa: BLE001 - a missing blob is reported by the fill itself
            return opts

    def get_template(self, tid: str) -> dict:
        r = self.store.one("SELECT * FROM templates WHERE id=? AND deleted=0", (tid,))
        if not r or (r.get("tenant") or "default") != current_tenant():
            not_found("模板")
        return r

    def template_detail(self, tid: str) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        if r["format"] in PDF_LIKE and v["slots"]:
            v = dict(v)
            v["slots"] = pdf_slots_with_nrect(self.store.get_blob(v["working_blob"]), v["slots"])
        versions = self.store.all("SELECT version_no, parent_no, note, created_at FROM template_versions WHERE template_id=? ORDER BY version_no", (tid,))
        redo = [x for x in versions if x["parent_no"] == r["head"]]
        parent = v.get("parent_no")
        return {**self._template_out(r), "version": {"no": v["version_no"], "slots": v["slots"], "note": v["note"],
                                                      "editor": v.get("editor"), "created_at": v["created_at"]},
                "can_undo": parent is not None, "can_redo": bool(redo), "versions": versions[-50:],
                "exports": self._exports_for(r["format"])}

    def _exports_for(self, fmt: str) -> list[dict]:
        from .engine.converters import soffice_path
        from .engine.generate import export_formats
        return export_formats(fmt, bool(soffice_path()))

    def get_version(self, tid: str, no: int) -> dict:
        v = self.store.one("SELECT * FROM template_versions WHERE template_id=? AND version_no=?", (tid, no))
        if not v:
            not_found("模板版本")
        return v

    def upload_template(self, filename: str, data: bytes, name: str | None = None) -> dict:
        from .engine.detect import detect
        filename = safe_display_name(filename)
        try:
            info = detect(data, filename)
        except ValueError as e:
            raise ServiceError(400, "bad_template", str(e)) from e
        if info["encrypted"]:
            raise ServiceError(400, "encrypted", "模板文件已加密，请移除密码后再上传")
        fmt = info["format"]
        notes = list(info["notes"])
        working = data
        if info.get("convert_via") and info["convert_via"] in ("docx", "xlsx", "pptx"):
            from .engine.converters import ConversionError, convert
            try:
                working = convert(data, fmt, info["convert_via"])
                notes.append(f"{fmt.upper()} 模板已通过 LibreOffice 转换为 {info['convert_via'].upper()}；输出为 {info['convert_via'].upper()}。转换可能改变部分排版")
                fmt = info["convert_via"]
            except ConversionError as e:
                raise ServiceError(400, "convert_failed", str(e)) from e
        elif fmt == "xls":
            from .engine.converters import ConversionError, convert
            try:
                working = convert(data, "xls", "xlsx")
                notes.append("XLS 模板已通过 LibreOffice 转换为 XLSX；输出为 XLSX")
                fmt = "xlsx"
            except ConversionError as e:
                raise ServiceError(400, "convert_failed", str(e)) from e
        fmt = {"docm": "docx", "xlsm": "xlsx", "pptm": "pptx"}.get(fmt, fmt)
        image_meta = None
        from .engine import pdf_engine as _pe
        if fmt == "pdf" and not _pe.pdf_templates_available():
            raise ServiceError(400, "engine_unavailable", _pe.TEMPLATES_UNAVAILABLE)
        if fmt in ("png", "jpeg", "tiff", "bmp", "webp") and not _pe.image_templates_available():
            raise ServiceError(400, "engine_unavailable", _pe.IMAGE_TEMPLATES_UNAVAILABLE)
        if fmt in ("png", "jpeg", "tiff", "bmp", "webp"):
            from .engine.image_template import ImageTemplateError, make_wrapper
            try:
                working, image_meta, img_notes = make_wrapper(data)
            except ImageTemplateError as e:
                raise ServiceError(400, e.code, str(e)) from e
            notes.extend(img_notes)
            image_meta["original_blob"] = self.store.put_blob(data)    # the upload itself, unchanged
            fmt = image_meta["native"]
        if fmt not in ("docx", "xlsx", "pdf", "pptx", "png", "jpeg"):
            raise ServiceError(400, "unsupported_template", f"{fmt.upper()} 不能作为模板。可作为模板的格式：DOCX、XLSX、PDF、PPTX、PNG、JPEG（以及可转换的 DOC/XLS/PPT/ODT/ODS/ODP/RTF、BMP/TIFF/WEBP）")
        try:
            check = template_inventory(fmt, working)
        except Exception as e:  # noqa: BLE001
            raise ServiceError(400, "bad_template", f"模板无法解析：{e}") from e
        notes.extend(check.get("notes", []))
        if info["macros"]:
            notes.append("模板包含宏；宏不会被执行。输出文件为不含宏的标准格式（.docx/.xlsx/.pptx），宏代码会保留在包内但扩展名不启用宏")
        tid = new_id("t")
        t = now()
        orig = self.store.put_blob(working)
        self.store.insert("templates", {"id": tid, "tenant": current_tenant(), "name": (name or filename.rsplit(".", 1)[0])[:100], "kind": "uploaded",
                                        "format": fmt, "original_blob": orig, "original_name": filename, "head": 1,
                                        "created_at": t, "updated_at": t, "info": {"notes": notes, "inventory": check.get("inventory", {}),
                                                                                    "pdf": check.get("pdf"), "image": image_meta}})
        self.store.insert("template_versions", {"template_id": tid, "version_no": 1, "parent_no": None, "working_blob": orig,
                                                "slots": [], "note": "上传原始模板", "created_at": t})
        return self.template_detail(tid)

    def new_version(self, tid: str, working: bytes, slots: list[dict], note: str, editor: dict | None = None) -> dict:
        r = self.get_template(tid)
        mx = self.store.one("SELECT MAX(version_no) AS m FROM template_versions WHERE template_id=?", (tid,))["m"] or 0
        no = mx + 1
        self.store.insert("template_versions", {"template_id": tid, "version_no": no, "parent_no": r["head"],
                                                "working_blob": self.store.put_blob(working), "slots": slots, "note": note,
                                                "editor": editor, "created_at": now()})
        self.store.update("templates", {"id": tid}, {"head": no, "updated_at": now()})
        self._bump_projects_using(tid)
        return self.template_detail(tid)

    def _bump_projects_using(self, tid: str) -> None:
        for p in self.store.all("SELECT id FROM projects WHERE template_id=? AND deleted=0", (tid,)):
            self.store.bump_project(p["id"])

    def template_undo(self, tid: str) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        if v["parent_no"] is None:
            raise ServiceError(400, "nothing_to_undo", "没有可撤销的模板修改")
        self.store.update("templates", {"id": tid}, {"head": v["parent_no"], "updated_at": now()})
        self._bump_projects_using(tid)
        return self.template_detail(tid)

    def template_redo(self, tid: str) -> dict:
        r = self.get_template(tid)
        kids = self.store.all("SELECT version_no FROM template_versions WHERE template_id=? AND parent_no=? ORDER BY version_no DESC", (tid, r["head"]))
        if not kids:
            raise ServiceError(400, "nothing_to_redo", "没有可重做的模板修改")
        self.store.update("templates", {"id": tid}, {"head": kids[0]["version_no"], "updated_at": now()})
        self._bump_projects_using(tid)
        return self.template_detail(tid)

    def duplicate_template(self, tid: str, name: str | None = None) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        nid = new_id("t")
        t = now()
        self.store.insert("templates", {"id": nid, "tenant": current_tenant(), "name": (name or r["name"] + " 副本")[:100], "kind": r["kind"], "format": r["format"],
                                        "original_blob": r["original_blob"], "original_name": r["original_name"], "head": 1,
                                        "created_at": t, "updated_at": t, "info": r.get("info")})
        self.store.insert("template_versions", {"template_id": nid, "version_no": 1, "parent_no": None, "working_blob": v["working_blob"],
                                                "slots": v["slots"], "editor": v.get("editor"), "note": f"复制自「{r['name']}」v{v['version_no']}",
                                                "created_at": t})
        return self.template_detail(nid)

    def rename_template(self, tid: str, name: str) -> dict:
        self.get_template(tid)
        self.store.update("templates", {"id": tid}, {"name": name.strip()[:100], "updated_at": now()})
        return self.template_detail(tid)

    def delete_template(self, tid: str) -> None:
        self.get_template(tid)
        self.store.update("templates", {"id": tid}, {"deleted": 1, "updated_at": now()})

    def add_slot(self, tid: str, req: dict) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        slots = list(v["slots"] or [])
        work = self.store.get_blob(v["working_blob"])
        name = (req.get("name") or "").strip() or "字段"
        fmt = r["format"]
        if fmt == "docx" and req.get("kind") == "block_column":
            try:
                work2, blk = T.docx_add_block_column(work, req, slots)
            except T.TemplateError as e:
                raise ServiceError(400, e.code, str(e)) from e
            slots = [blk if s["id"] == blk["id"] else s for s in slots]
            detail = self.new_version(tid, work2, slots, f"为重复内容块「{blk['name']}」添加列「{blk['columns'][-1]['name']}」")
            detail["created_slot"] = blk
            return detail
        try:
            if fmt == "docx":
                work2, slot = T.docx_add_slot(work, req, slots)
            elif fmt == "xlsx":
                work2, slot = T.xlsx_add_slot(work, req, slots)
            elif fmt in PDF_LIKE:
                from .engine import pdf_engine as _pe
                img_meta = (r.get("info") or {}).get("image") or {}
                if fmt != "pdf":
                    req = {**req, "image_template": True}
                if fmt != "pdf" and not _pe.pdf_templates_available():
                    # raster authoring: the wrapper PDF is left untouched (it is only the
                    # preview surface); the field is analysed straight from the pixels
                    slot = T.image_add_slot(self.store.get_blob(img_meta["original_blob"]), req, slots)
                    work2 = work
                else:
                    slot = T.pdf_add_slot(self.store.get_blob(r["original_blob"]), req, slots)
                    work2, warns = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), slots + [slot])
                    if warns:
                        slot["warning"] = "；".join(warns)
            elif fmt == "pptx":
                from .engine.pptx_engine import pptx_add_slot
                work2, slot = pptx_add_slot(work, req, slots)
            else:
                raise ServiceError(400, "unsupported", "该模板格式不支持创建字段")
        except T.TemplateError as e:
            raise ServiceError(400, e.code, str(e)) from e
        for k in ("required", "data_type", "format", "default"):
            if k in req:
                slot[k] = req[k]
        # same display name → offer shared binding (explicit flag from client)
        if req.get("share_binding_with"):
            other = next((s for s in slots if s["id"] == req["share_binding_with"]), None)
            if other:
                slot["binding"] = other.get("binding") or other["id"]
        slots.append(slot)
        detail = self.new_version(tid, work2, slots, f"添加字段「{slot['name']}」")
        detail["created_slot"] = slot
        return detail

    def pdf_repeat_detect(self, tid: str, page: int, nrect: list) -> dict:
        r = self.get_template(tid)
        if r["format"] not in PDF_LIKE:
            raise ServiceError(400, "not_pdf", "只有 PDF / 图片模板需要识别明细区域的行列")
        if len(nrect) != 4:
            raise ServiceError(400, "bad_rect", "请先在页面上框选明细区域")
        try:
            return T.pdf_detect_repeat(self.store.get_blob(r["original_blob"]), page, [float(x) for x in nrect])
        except T.TemplateError as e:
            raise ServiceError(400, e.code, str(e)) from e

    def update_slot(self, tid: str, slot_id: str, values: dict) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        slots = [dict(s) for s in v["slots"]]
        sl = next((s for s in slots if s["id"] == slot_id), None)
        if not sl:
            not_found("字段")
        editable = {"name", "required", "data_type", "format", "default", "font_size", "min_font_size", "align", "valign",
                    "empty_policy", "placeholder", "repeat_header", "keep_header_when_empty", "binding", "mode", "columns",
                    "overflow", "min_row_scale", "color", "separator", "remove_column", "shrink", "shrink_min"}
        for k, val in values.items():
            if k not in editable:
                continue
            if k == "columns":
                names = {c["key"]: c.get("name") for c in val or []}
                for c in sl.get("columns", []):
                    if c["key"] in names and names[c["key"]]:
                        c["name"] = str(names[c["key"]])[:60]
                aligns = {c["key"]: c.get("align") for c in val or [] if c.get("align") in ("left", "center", "right")}
                if sl.get("layout") and aligns:
                    sl["layout"] = {**sl["layout"], "cols": [{**c, "align": aligns.get(c["key"], c.get("align", "left"))} for c in sl["layout"]["cols"]]}
                continue
            if k == "shrink":
                val = bool(val)
            if k == "shrink_min":
                val = min(72.0, max(4.0, float(val)))
            if k == "separator" and val not in ("none", "blank", "page_break"):
                raise ServiceError(400, "bad_value", "分隔方式无效")
            if k == "remove_column":
                if not sl.get("block") or not any(c["key"] == val for c in sl.get("columns", [])):
                    raise ServiceError(400, "bad_value", "该列不存在")
                sl["columns"] = [c for c in sl["columns"] if c["key"] != val]
                sl["_remove_column"] = val
                continue
            if k == "overflow" and val not in ("error", "shrink", "continue"):
                raise ServiceError(400, "bad_value", "超出处理方式无效")
            if k == "empty_policy":
                # the valid set depends on what kind of repeat this slot is
                kindset = "block" if sl.get("block") else ("area" if sl.get("layout") else "table")
                allowed = T.EMPTY_POLICIES[kindset]
                if val not in allowed:
                    raise ServiceError(400, "bad_value", f"empty_policy 只能是 {' / '.join(allowed)}；收到「{val}」",
                                       {"field": "empty_policy", "allowed": list(allowed)})
            if k == "data_type" and val not in ("auto", "text", "number", "date"):
                raise ServiceError(400, "bad_value", "data_type 只能是 auto / text / number / date", {"field": "data_type"})
            if k in ("align", "valign") and val not in (("left", "center", "right") if k == "align" else ("top", "middle", "bottom")):
                raise ServiceError(400, "bad_value", f"{k} 取值无效", {"field": k})
            if k == "overflow" and val == "continue" and r["format"] in ("png", "jpeg"):
                raise ServiceError(400, "no_continue", "图片模板只有一页，不能复制页面续写；请选择「视为错误」或「压缩行高」")
            if k == "color":
                val = T._valid_hex(val)
                if not val:
                    raise ServiceError(400, "bad_value", "颜色格式应为 #RRGGBB")
            if k == "min_row_scale":
                val = min(1.0, max(0.3, float(val)))
            if k == "mode" and r["format"] not in ("xlsx",):
                continue  # text replace/insert mode is fixed at creation for documents
            sl[k] = val
        from .engine import transforms
        errs = transforms.validate(sl.get("format"))
        if errs:
            raise ServiceError(400, "bad_format", "；".join(errs))
        work = self.store.get_blob(v["working_blob"])
        if r["format"] in PDF_LIKE:
            work, _ = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), slots)
        note = f"修改字段「{sl['name']}」"
        if sl.get("_remove_column"):
            key = sl.pop("_remove_column")
            work = T.docx_remove_block_column(work, sl, key)
            note = f"从重复内容块「{sl['name']}」移除一列"
        return self.new_version(tid, work, slots, note)

    def remove_slot(self, tid: str, slot_id: str, project_id: str | None = None, cascade: bool = True) -> dict:
        r = self.get_template(tid)
        v = self.get_version(tid, r["head"])
        slots = list(v["slots"])
        sl = next((s for s in slots if s["id"] == slot_id), None)
        if not sl:
            not_found("字段")
        work = self.store.get_blob(v["working_blob"])
        fmt = r["format"]
        remaining = [s for s in slots if s["id"] != slot_id]
        if fmt == "docx":
            work = T.docx_remove_slot(work, sl)
        elif fmt == "xlsx":
            work = T.xlsx_remove_slot(work, sl)
        elif fmt in PDF_LIKE:
            work, _ = T.pdf_working_copy(self.store.get_blob(r["original_blob"]), remaining)
        elif fmt == "pptx":
            from .engine.pptx_engine import pptx_remove_slot
            work = pptx_remove_slot(work, sl)
        removed_maps = []
        binding = sl.get("binding") or sl["id"]
        still_bound = any((s.get("binding") or s["id"]) == binding for s in remaining)
        if cascade and project_id and not still_bound:
            ms = self.get_mappings(project_id)["mappings"]
            keep = [m for m in ms if ((m.get("target") or {}).get("binding") or (m.get("target") or {}).get("slot_id")) != binding]
            removed_maps = [m for m in ms if m not in keep]
            if removed_maps:
                self.save_mappings(project_id, keep, f"删除字段「{sl['name']}」并移除相关映射")
        detail = self.new_version(tid, work, remaining, f"删除字段「{sl['name']}」" + (f"（同时移除 {len(removed_maps)} 个映射）" if removed_maps else ""))
        detail["removed_mappings"] = [m["id"] for m in removed_maps]
        return detail

    # ================================================================ mappings
    def get_mappings(self, pid: str) -> dict:
        p = self.get_project(pid)
        row = self.store.one("SELECT * FROM mapping_revs WHERE project_id=? AND rev=?", (pid, p["mapping_rev"]))
        kids = self.store.all("SELECT rev FROM mapping_revs WHERE project_id=? AND parent_rev=?", (pid, p["mapping_rev"]))
        return {"rev": p["mapping_rev"], "mappings": (row or {}).get("data") or [], "can_undo": bool(row and row["parent_rev"] is not None),
                "can_redo": bool(kids), "note": (row or {}).get("note")}

    def save_mappings(self, pid: str, mappings: list[dict], note: str = "修改映射", base_rev: int | None = None) -> dict:
        p = self.get_project(pid)
        if base_rev is not None and base_rev != p["mapping_rev"]:
            raise ServiceError(409, "stale", "映射已在其他窗口被修改，请刷新后重试")
        clean = []
        used_no = set()
        for m in mappings:
            m = dict(m)
            m.setdefault("id", new_id("m"))
            m.setdefault("enabled", True)
            m.setdefault("type", "single")
            if not isinstance(m.get("sources"), list):
                raise ServiceError(400, "bad_mapping", "映射缺少来源")
            if not m.get("no") or m["no"] in used_no:
                m["no"] = max(used_no | {0}) + 1
            used_no.add(m["no"])
            m.setdefault("color", PALETTE[(m["no"] - 1) % len(PALETTE)])
            m["updated_at"] = now()
            clean.append(m)
        mx = self.store.one("SELECT MAX(rev) AS m FROM mapping_revs WHERE project_id=?", (pid,))["m"] or 0
        self.store.insert("mapping_revs", {"project_id": pid, "rev": mx + 1, "parent_rev": p["mapping_rev"], "data": clean,
                                           "note": note, "created_at": now()})
        self.store.update("projects", {"id": pid}, {"mapping_rev": mx + 1})
        self.store.bump_project(pid)
        return self.get_mappings(pid)

    def mappings_undo(self, pid: str) -> dict:
        p = self.get_project(pid)
        row = self.store.one("SELECT parent_rev FROM mapping_revs WHERE project_id=? AND rev=?", (pid, p["mapping_rev"]))
        if not row or row["parent_rev"] is None:
            raise ServiceError(400, "nothing_to_undo", "没有可撤销的映射修改")
        self.store.update("projects", {"id": pid}, {"mapping_rev": row["parent_rev"]})
        self.store.bump_project(pid)
        return self.get_mappings(pid)

    def mappings_redo(self, pid: str) -> dict:
        p = self.get_project(pid)
        kids = self.store.all("SELECT rev FROM mapping_revs WHERE project_id=? AND parent_rev=? ORDER BY rev DESC", (pid, p["mapping_rev"]))
        if not kids:
            raise ServiceError(400, "nothing_to_redo", "没有可重做的映射修改")
        self.store.update("projects", {"id": pid}, {"mapping_rev": kids[0]["rev"]})
        self.store.bump_project(pid)
        return self.get_mappings(pid)


# ------------------------------------------------------------------ helpers
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_display_name(name: str) -> str:
    name = unicodedata.normalize("NFC", (name or "file").replace("\\", "/").split("/")[-1])
    name = _ILLEGAL.sub("_", name).strip(" .") or "file"
    if len(name.encode("utf-8")) > 200:
        base, dot, ext = name.rpartition(".")
        base = base or name
        while len((base + dot + ext).encode("utf-8")) > 200 and base:
            base = base[:-1]
        name = base + dot + ext
    return name


def safe_output_name(name: str, max_bytes: int = 150) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("..", "_")
    name = _ILLEGAL.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "输出"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name.strip(" .") or "输出"


def template_inventory(fmt: str, data: bytes) -> dict:
    """Inspect a template for objects the fill engine does or does not preserve."""
    notes = []
    inv = {}
    if fmt in ("docx", "xlsx", "pptx"):
        from .engine.ooxml import Package
        pkg = Package(data)
        inv = pkg.inventory()
        if fmt == "docx":
            from .engine.fonts import docx_fonts, missing_fonts
            miss = missing_fonts(docx_fonts(pkg))
            if miss:
                notes.append(f"模板使用的字体在服务器上未安装：{'、'.join(miss[:8])}。DOCX 文件本身不受影响（在用户电脑上由 Word 显示）；服务器导出 PDF 时这些字体会被替换，版式可能略有变化")
            if inv.get("charts"):
                notes.append(f"含 {inv['charts']} 个图表：原样保留（不参与填充）")
            if inv.get("comments"):
                notes.append("含批注：原样保留")
            if inv.get("signatures"):
                notes.append("含数字签名：填充后签名将失效")
        if fmt == "xlsx":
            if inv.get("charts"):
                notes.append(f"含 {inv['charts']} 个图表：保留；插入明细行时引用本表的图表数据范围会随之调整")
            if inv.get("pivot"):
                notes.append("含数据透视表：保留，但透视缓存不会自动刷新（打开后需在 Excel 中刷新）")
            if inv.get("externalLinks"):
                notes.append("含外部链接：保留但不会更新，也不会访问外部文件")
            if inv.get("slicers"):
                notes.append("含切片器：保留，未验证插入行后的行为")
    if fmt in ("png", "jpeg"):
        from .engine import pdf_engine
        return {"notes": notes, "inventory": {}, "pdf": pdf_engine.inspect(data)}
    if fmt == "pdf":
        from .engine import pdf_engine
        meta = pdf_engine.inspect(data)
        if meta["signed"]:
            notes.append("PDF 含数字签名：填充后签名将失效")
        if meta["has_js"]:
            notes.append("PDF 含 JavaScript：不会执行")
        if meta["kind"] == "scanned":
            notes.append("扫描型 PDF 模板：框选区域时会真正清除该区域的图像像素（填白）再写入新值")
        return {"notes": notes, "inventory": {}, "pdf": meta}
    return {"notes": notes, "inventory": inv}


PDF_LIKE = ("pdf", "png", "jpeg")


def pdf_slots_with_nrect(pdf_bytes: bytes, slots: list[dict]) -> list[dict]:
    """Add the on-screen (rotated, normalised) rectangle of each PDF box for display."""
    from .engine import pdf_engine as pe
    out = [dict(s) for s in slots]
    if not pe.pdf_templates_available():
        # Image templates: the page IS the pixel grid (1 px = 1 pt, no rotation), so the
        # normalised rect is plain arithmetic and needs no PDF engine.
        for s in out:
            loc = s.get("locator") or {}
            size = loc.get("page_size")
            if "rect" in loc and size and float(size[0]) > 0 and float(size[1]) > 0:
                x0, y0, x1, y1 = (float(v) for v in loc["rect"])
                w, h = float(size[0]), float(size[1])
                s["nrect"] = [x0 / w, y0 / h, x1 / w, y1 / h]
        return out

    import pymupdf
    doc = pe.open_pdf(pdf_bytes)
    for s in out:
        loc = s.get("locator") or {}
        if "rect" in loc and loc.get("page", 0) < doc.page_count:
            page = doc[loc["page"]]
            s["nrect"] = pe.unrot_to_visual_norm(page, pymupdf.Rect(loc["rect"]))
            lay = s.get("layout")
            if lay:  # PDF repeat area: column dividers and row lines for display
                from .engine import pdf_repeat as pr
                s["ngrid"] = [{"kind": kind, "r": pe.unrot_to_visual_norm(page, r)} for r, kind in pr.grid_lines(lay)]
    doc.close()
    return out
