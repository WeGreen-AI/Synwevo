"""Read models for the UI: source viewers, template rendering, previews and
cross-file validation.  Preview uses exactly the same plan + fill code as jobs."""
from __future__ import annotations

import io

import hashlib
import json

from .engine import generate as G
from .engine import pdf_engine as pe
from .engine import records as R
from .engine.docx_render import DocxRenderer, render_docx
from .engine.ooxml import Package
from .engine.sheet_render import XlsxRenderer, workbook_window
from .jobs import ProjectContext, verify_output
from .services import App, ServiceError, not_found


class Views:
    def __init__(self, app: App):
        self.app = app
        self.store = app.store
        self._preview_cache: dict[str, dict] = {}

    # ================================================================ sources
    def source_view(self, sid: str, sheet: str | None = None, offset: int = 0, limit: int = 200, block: int | None = None) -> dict:
        r = self.app.get_source(sid, raw=True)
        fam, fmt = r["family"], r["format"]
        base = {"id": sid, "filename": r["filename"], "format": fmt, "family": fam, "status": r["status"], "error": r["error"],
                "notes": r["notes"], "version": r["version"]}
        if r["status"] in ("parsing",):
            return {**base, "view": "pending"}
        if r["status"] in ("error", "unsupported"):
            return {**base, "view": "error"}
        doc = self.app.doc_for(r, block)
        if block is not None:
            base["block"] = block
        if doc.workbook_json is not None and fam in ("table",):
            return {**base, "view": "grid", "grid": workbook_window(doc.workbook, sheet, offset, limit)}
        if fam == "document":
            return {**base, "view": "docx", "doc": render_docx(doc.data)}
        if fam == "pdf":
            meta = ((r["structure"] or {}).get("pdf") if block is None else None) or pe.inspect(doc.data)
            return {**base, "view": "pdf", "pdf": meta, "ocr_pages": sorted(int(k) for k in (doc.ocr or {}))}
        if fam == "image":
            meta = (r["structure"] or {}).get("image") or {}
            return {**base, "view": "image", "image": meta, "ocr_pages": sorted(int(k) for k in (r.get("ocr") or {}))}
        if fam == "slides":
            from .engine.pptx_engine import render_pptx
            return {**base, "view": "slides", "slides": render_pptx(doc.data)}
        if fam == "text":
            text = doc.text
            out = {**base, "view": "text", "lines": text.split("\n")}
            if doc.workbook_json is not None:
                out["grid"] = workbook_window(doc.workbook, sheet, offset, limit)
            return out
        return {**base, "view": "error", "error": "无法预览该文件"}

    def search_source(self, sid: str, q: str, limit: int = 200, block: int | None = None) -> dict:
        """Find text anywhere in a source file (all rows / pages, not only what is rendered)."""
        q = (q or "").strip()
        if not q:
            return {"hits": [], "total": 0}
        r = self.app.get_source(sid, raw=True)
        doc = self.app.doc_for(r, block)
        ql = q.lower()
        hits: list[dict] = []
        total = 0
        if doc.workbook_json is not None:
            for sh in doc.workbook.sheets:
                for ri, row in enumerate(sh.rows):
                    for ci, v in enumerate(row):
                        if v.text and ql in v.text.lower():
                            total += 1
                            if len(hits) < limit:
                                hits.append({"kind": "cells", "sheet": sh.name, "r": ri, "c": ci, "text": v.text[:80]})
        if r["family"] == "document":
            from .locators import _walk_paragraphs
            from .engine import docx_core as dc
            for info in dc.list_parts(doc.pkg):
                for path, p in _walk_paragraphs(dc.part_container(doc.pkg, info["part"])):
                    t = dc.para_text(p)
                    i = t.lower().find(ql)
                    if i >= 0:
                        total += 1
                        if len(hits) < limit:
                            hits.append({"kind": "docx_text", "part": info["part"], "path": path, "start": i, "end": i + len(q), "text": t[:80]})
        elif r["family"] in ("pdf", "image"):
            pages = {}
            if r["family"] == "pdf":
                for pno in range(pe.open_pdf(doc.data).page_count):
                    pages[pno] = pe.page_words(doc.data, pno)["words"]
            for k, v in (doc.ocr or {}).items():
                if not pages.get(int(k)):
                    pages[int(k)] = v["words"]
            for pno, words in sorted(pages.items()):
                for w in words:
                    if ql in w["t"].lower():
                        total += 1
                        if len(hits) < limit:
                            hits.append({"kind": "rect", "page": pno, "nrect": w["r"], "text": w["t"]})
        elif r["family"] == "text":
            for li, line in enumerate(doc.text.split("\n")):
                i = line.lower().find(ql)
                if i >= 0:
                    total += 1
                    if len(hits) < limit:
                        hits.append({"kind": "text_span", "line": li, "start": i, "end": i + len(q), "text": line[:80]})
        elif r["family"] == "slides":
            from .engine.pptx_engine import render_pptx
            for sl in render_pptx(doc.data)["slides"]:
                for sh in sl["shapes"]:
                    texts = [p["text"] for p in sh.get("paras", [])] + [p["text"] for row in sh.get("rows", []) for c in row for p in c["paras"]]
                    if any(ql in t.lower() for t in texts):
                        total += 1
                        if len(hits) < limit:
                            hits.append({"kind": "pptx", "slide": sl["index"], "shape": sh["id"], "text": next(t for t in texts if ql in t.lower())[:80]})
        return {"hits": hits, "total": total}

    def source_page_png(self, sid: str, page: int, zoom: float, block: int | None = None) -> bytes:
        r = self.app.get_source(sid, raw=True)
        doc = self.app.doc_for(r, block)
        if r["family"] == "pdf":
            return pe.render_page_png(doc.data, page, zoom)
        if r["family"] == "image":
            from .engine.images import to_png
            return to_png(doc.data)
        raise ServiceError(400, "not_paged", "该文件没有页面图像")

    def source_words(self, sid: str, page: int, block: int | None = None) -> dict:
        r = self.app.get_source(sid, raw=True)
        doc = self.app.doc_for(r, block)
        ocr = (doc.ocr or {}).get(str(page))
        if r["family"] == "pdf":
            out = pe.page_words(doc.data, page)
            if ocr and not out["words"]:
                out["words"] = ocr["words"]
                out["source"] = "ocr"
                out["engine"] = ocr.get("engine")
            else:
                out["source"] = "text_layer"
            return out
        if r["family"] == "image":
            if not ocr:
                return {"index": 0, "words": [], "source": "none"}
            return {"index": 0, "words": ocr["words"], "source": "ocr", "engine": ocr.get("engine"), "w": ocr["w"], "h": ocr["h"]}
        raise ServiceError(400, "no_words", "该文件没有页面文字层")

    def run_ocr(self, sid: str, pages: list[int] | None = None) -> dict:
        from .engine.images import to_png
        from .engine.ocr import OcrError, ocr_png
        r = self.app.get_source(sid, raw=True)
        doc = self.app._doc_from_row(r)
        results = dict(r.get("ocr") or {})
        try:
            if r["family"] == "image":
                results["0"] = ocr_png(to_png(doc.data))
            elif r["family"] == "pdf":
                meta = pe.inspect(doc.data)
                targets = pages if pages is not None else [p["index"] for p in meta["pages"] if p["kind"] in ("scanned", "mixed")]
                for pno in targets:
                    png = pe.render_page_png(doc.data, pno, 300 / 72)
                    results[str(pno)] = ocr_png(png)
            else:
                raise ServiceError(400, "no_ocr", "该文件类型不需要 OCR")
        except OcrError as e:
            raise ServiceError(400, "ocr_failed", str(e)) from e
        status = r["status"]
        if status == "needs_ocr":
            status = "ready"
        self.store.update("source_files", {"id": sid}, {"ocr": results, "status": status, "version": r["version"] + 1})
        self.store.bump_project(r["project_id"])
        return {"pages": sorted(int(k) for k in results), "words": sum(len(v["words"]) for v in results.values()),
                "low_conf": sum(1 for v in results.values() for w in v["words"] if w["conf"] < 60)}

    def set_correction(self, sid: str, mapping_id: str, value: str | None, confirmed: bool = True) -> dict:
        r = self.app.get_source(sid, raw=True)
        corr = dict(r.get("corrections") or {})
        if value is None:
            corr.pop(mapping_id, None)
        else:
            corr[mapping_id] = {"value": value, "confirmed": confirmed}
        self.store.update("source_files", {"id": sid}, {"corrections": corr, "version": r["version"] + 1})
        self.store.bump_project(r["project_id"])
        return corr

    # ================================================================ templates
    def template_view(self, tid: str, sheet: str | None = None, offset: int = 0, limit: int = 200, version: int | None = None) -> dict:
        t = self.app.get_template(tid)
        v = self.app.get_version(tid, version or t["head"])
        work = self.store.get_blob(v["working_blob"])
        fmt = t["format"]
        slots = [dict(s) for s in v["slots"]]
        if fmt in ("pdf", "png", "jpeg") and slots:
            from .services import pdf_slots_with_nrect
            slots = pdf_slots_with_nrect(work, slots)
        out = {"id": tid, "format": fmt, "version": v["version_no"], "slots": slots}
        out.update(render_output("pdf" if fmt in ("png", "jpeg") else fmt, work, sheet, offset, limit, slots))
        return out

    def template_page_png(self, tid: str, page: int, zoom: float, which: str = "working") -> bytes:
        t = self.app.get_template(tid)
        v = self.app.get_version(tid, t["head"])
        data = self.store.get_blob(t["original_blob"] if which == "original" else v["working_blob"])
        if t["format"] not in ("pdf", "png", "jpeg"):
            raise ServiceError(400, "not_pdf", "只有 PDF / 图片模板有页面图像")
        return pe.render_page_png(data, page, zoom)

    # ================================================================ records
    def records(self, pid: str, offset: int = 0, limit: int = 200) -> dict:
        ctx = ProjectContext(self.app, pid)
        recs = ctx.records
        out = R.summarize(recs, limit, offset)
        out["mode_text"] = R.MODES.get(ctx.rule.get("mode"), "")
        return out

    def rule_preview(self, pid: str, rule: dict) -> dict:
        """Counts for a *candidate* rule without saving it (area semantics preview)."""
        ctx = ProjectContext(self.app, pid)
        try:
            res = R.build_records([ctx.srcs[r["id"]] for r in ctx.source_rows], rule)
        except ValueError as e:
            raise ServiceError(400, "bad_rule", str(e)) from e
        return R.summarize(res, 20)

    # ================================================================ preview
    def preview(self, pid: str, index: int | None = None, key: str | None = None, search: str | None = None) -> dict:
        ctx = ProjectContext(self.app, pid)
        if not ctx.template:
            raise ServiceError(400, "no_template", "请先选择或创建模板")
        recs = ctx.records["records"]
        if not recs:
            raise ServiceError(400, "no_records", "没有识别到样本")
        rec = None
        if key:
            rec = next((r for r in recs if r["key"] == key), None)
        elif search:
            s = search.strip().lower()
            rec = next((r for r in recs if s in (r.get("ident") or "").lower() or s in r["label"].lower()), None)
            if rec is None:
                raise ServiceError(404, "not_found", f"没有找到包含「{search}」的样本")
        if rec is None:
            index = max(0, min(int(index or 0), len(recs) - 1))
            rec = recs[index]
        sig = ctx.signature()
        cache_key = hashlib.sha1(f"{sig}|{rec['key']}|{(ctx.project.get('output') or {}).get('layout')}".encode()).hexdigest()
        if cache_key in self._preview_cache:
            return self._preview_cache[cache_key]
        plan = ctx.plan(rec)
        result = {"signature": sig, "record": {k: rec.get(k) for k in ("index", "key", "label", "ident", "file_id", "file_name", "where", "merged_from")},
                  "total": len(recs), "plan": plan.to_json(), "template_version": ctx.version["version_no"]}
        if plan.blocking:
            result["blocked"] = True
            result["issues"] = plan.issues
            return result
        layout_mode = (ctx.project.get("output") or {}).get("layout") or "off"
        tpl_pages = G.template_pages(ctx.template["format"], ctx.working()) if layout_mode == "fit" else None
        out, issues, lay = G.fill_checked(ctx.template["format"], ctx.working(), ctx.slots, plan,
                                          self.app.with_image_bytes(self.app.fill_options(ctx.template)),
                                          "fit" if layout_mode == "fit" else "off", tpl_pages)
        result["issues"] = plan.issues + issues
        if not out:
            result["blocked"] = True
            return result
        native = G.native_format(ctx.template["format"])
        blob = self.store.put_blob(out)
        result["output"] = {"format": native, "blob": blob, "size": len(out)}
        result["render"] = render_output(native, out, None, 0, 300, ctx.slots, highlight=True)
        result["verification"] = verify_output(native, out, ctx.slots, plan)
        if lay:
            result["layout"] = lay
        if len(self._preview_cache) > 200:
            self._preview_cache.clear()
        self._preview_cache[cache_key] = result
        return result

    def preview_layout(self, pid: str, blob: str) -> dict:
        """Real pagination of a preview output (LibreOffice layout) against the template's."""
        from .engine import layout as L
        ctx = ProjectContext(self.app, pid)
        if not ctx.template:
            raise ServiceError(400, "no_template", "请先选择或创建模板")
        fmt = G.native_format(ctx.template["format"])
        if fmt not in G.LAYOUT_FORMATS:
            return {"available": False, "reason": "该格式的页面由模板本身固定，不会因内容而重新分页"}
        try:
            pdf = L.render_pdf(self.preview_file(blob), fmt)
            tpl_pages = L.page_count(ctx.working(), fmt)
        except L.LayoutUnavailable as e:
            return {"available": False, "reason": str(e)}
        pages = pe.page_count(pdf)
        return {"available": True, "pages": pages, "template_pages": tpl_pages, "pdf_blob": self.store.put_blob(pdf)}

    def preview_file(self, blob: str) -> bytes:
        if not self.store.has_blob(blob):
            not_found("预览文件")
        return self.store.get_blob(blob)

    # ================================================================ validation across files
    def validate(self, pid: str) -> dict:
        ctx = ProjectContext(self.app, pid)
        out = {"files": [], "mappings": [{"id": m["id"], "no": m["no"], "color": m.get("color")} for m in ctx.mappings],
               "static": [], "signature_groups": {}}
        from .engine.plan import static_check
        out["static"] = static_check(ctx.mappings, ctx.slots, ctx.rule) if ctx.template else []
        rep = next((r for r in ctx.all_source_rows if r["representative"]), None)
        recs = None
        try:
            recs = ctx.records if ctx.rule.get("mode") else None
        except ServiceError as e:
            out["rule_error"] = e.message
        for r in ctx.all_source_rows:
            f = {"id": r["id"], "filename": r["filename"], "status": r["status"], "error": r["error"], "results": {},
                 "same_structure": (rep is not None and r["signature"] == rep["signature"]),
                 "representative": bool(r["representative"])}
            if r["status"] not in ("ready", "needs_ocr") or recs is None or not ctx.template:
                out["files"].append(f)
                continue
            frecs = [x for x in recs["records"] if r["id"] in x["file_ids"]]
            ferr = [e for e in recs["file_errors"] if e["file_id"] == r["id"]]
            if ferr:
                f["record_error"] = ferr[0]["message"]
            f["records"] = len(frecs)
            if frecs:
                plan = ctx.plan(frecs[0])
                for m in ctx.mappings:
                    errs = [i for i in plan.issues if i.get("mapping") == m["id"] and i["level"] == "error"]
                    if not m.get("enabled", True):
                        f["results"][m["id"]] = {"ok": None, "message": "已禁用"}
                    elif errs:
                        f["results"][m["id"]] = {"ok": False, "message": errs[0]["message"], "code": errs[0]["code"],
                                                 "value": (errs[0].get("detail") or {}).get("value")}
                    else:
                        tr = next((t for t in plan.trace.values() if t.get("mapping") == m["id"]), None)
                        f["results"][m["id"]] = {"ok": True, "value": (tr or {}).get("value")}
            out["files"].append(f)
        return out


def render_output(fmt: str, data: bytes, sheet: str | None, offset: int, limit: int, slots: list[dict], highlight: bool = False) -> dict:
    if fmt == "docx":
        return {"view": "docx", "doc": render_docx(data)}
    if fmt == "xlsx":
        rr = XlsxRenderer(data)
        return {"view": "grid", "grid": rr.window(sheet, offset, limit)}
    if fmt == "pdf":
        return {"view": "pdf", "pdf": pe.inspect(data)}
    if fmt in ("png", "jpg"):
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        return {"view": "pdf", "pdf": pe.inspect(pe.as_pdf_bytes(data)), "image": {"width": im.width, "height": im.height, "format": fmt}}
    if fmt == "pptx":
        from .engine.pptx_engine import render_pptx
        return {"view": "slides", "slides": render_pptx(data, slots)}
    if fmt == "csv":
        return {"view": "text", "lines": data.decode("utf-8-sig").splitlines()}
    return {"view": "none"}
