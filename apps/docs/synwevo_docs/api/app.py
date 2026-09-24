"""DocWeave 的 HTTP 接口：浏览器工作台与机器接口。

engine 包不依赖任何 Web 框架，所以将来的桌面外壳可以直接复用它；这一层只做 HTTP。

会话网关、错误契约、探针和静态外壳的通用部分已经移到 `synwevo_core.http`——它们对账户服务、
门户和这里是同一套，留在这里就意味着账户服务要导入 DocWeave 才能有一个会话。这里剩下的全部是
DocWeave 自己的业务路由。
"""
from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from fastapi import Body, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)
from synwevo_core.store import Store

from ..engine.limits import LIMITS
from ..jobs import JobService, naming_tokens
from ..services import App, safe_output_name
from ..views import Views

MIME = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pdf": "application/pdf", "csv": "text/csv; charset=utf-8", "zip": "application/zip", "png": "image/png", "jpg": "image/jpeg"}


def attachment(data: bytes, filename: str, ext: str | None = None, inline: bool = False) -> Response:
    ext = ext or (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    if not ext.isascii() or not ext.isalnum():
        ext = "bin"
    safe = safe_output_name(filename, 200)
    ascii_fallback = safe.encode("ascii", "ignore").decode() or f"download.{ext}"
    disp = "inline" if inline else "attachment"
    cd = f"{disp}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{urllib.parse.quote(safe)}"
    return Response(data, media_type=MIME.get(ext, "application/octet-stream"),
                    headers={"Content-Disposition": cd, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


def web_dist() -> Path:
    """The workbench's built shell. Its own build output, beside its own source."""
    return Path(env.docs("UI_DIR") or Path(__file__).resolve().parents[2] / "web" / "dist")


def install(api: FastAPI, plat: Platform, *, start_jobs: bool = True) -> App:
    """Mount DocWeave's own routes onto an application the caller has already scaffolded.

    Returns the business service object so a composer can hold on to it. Nothing here touches
    accounts, the OIDC provider or the portal — those are mounted by their own modules.
    """
    from ..webhooks import WebhookDispatcher
    store, machine = plat.store, plat.machine
    app_ = App(store)
    views = Views(app_)
    jobs = JobService(app_) if start_jobs else None
    hooks = WebhookDispatcher(store, jobs, start=start_jobs)
    app_.on_event = hooks.notify                      # job state changes wake the dispatcher
    api.state.app = app_
    api.state.views = views
    api.state.jobs = jobs
    api.state.webhooks = hooks

    # ------------------------------------------------------------ regression cases & desensitization
    @api.get("/api/regression-cases")
    def regression_list():
        from ..regression import list_cases
        return list_cases()

    @api.post("/api/projects/{pid}/regression-cases")
    def regression_capture(pid: str, body: dict = Body(default={})):
        from ..regression import RegressionError, capture
        try:
            return capture(app_, pid, body.get("name") or "", body.get("note") or "", body.get("sample_keys"))
        except RegressionError as e:
            raise ServiceError(400, e.code, str(e)) from e

    @api.post("/api/regression-cases/{cid}/run")
    def regression_run(cid: str):
        from ..regression import RegressionError, run
        try:
            return run(cid)
        except RegressionError as e:
            raise ServiceError(404 if e.code == "not_found" else 400, e.code, str(e)) from e

    @api.delete("/api/regression-cases/{cid}")
    def regression_delete(cid: str):
        from ..regression import RegressionError, delete
        try:
            delete(cid)
        except RegressionError as e:
            raise ServiceError(404 if e.code == "not_found" else 400, e.code, str(e)) from e
        return {"ok": True}

    @api.get("/api/regression-cases/{cid}/download")
    def regression_download(cid: str):
        import io as _io
        import zipfile
        from ..regression import RegressionError, load
        try:
            _, d = load(cid)
        except RegressionError as e:
            raise ServiceError(404, e.code, str(e)) from e
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    z.write(f, f"{cid}/{f.relative_to(d).as_posix()}")
        return attachment(buf.getvalue(), f"回归用例_{cid}.zip", "zip")

    @api.post("/api/projects/{pid}/desensitize")
    def desensitize(pid: str, body: dict = Body(...)):
        return app_.desensitize_sources(pid, body)

    @api.get("/api/desensitized/{blob}")
    def desensitized_file(blob: str, name: str = "脱敏文件"):
        return attachment(views.preview_file(blob), name)

    @api.get("/api/about")
    def about():
        from ..licensing import about as _about
        return _about()

    @api.get("/api/source-code")
    def source_code():
        from ..licensing import source_archive, source_offer_enabled
        if not source_offer_enabled():
            raise ServiceError(404, "source_offer_off", "本部署已关闭源代码下载（运营方已取得相应许可或未使用 AGPL 组件）")
        data, name = source_archive()
        return Response(data, media_type="application/gzip",
                        headers={"Content-Disposition": f"attachment; filename=\"{name}\""})

    @api.get("/api/capabilities")
    def capabilities():
        from ..capabilities import capability_report
        return capability_report()

    # ------------------------------------------------------------ projects
    @api.get("/api/projects")
    def list_projects(archived: int = 0):
        return app_.list_projects(include_archived=bool(archived))

    @api.post("/api/projects")
    def create_project(body: dict = Body(...)):
        return app_.create_project(body.get("name", ""), origin="ui")

    @api.get("/api/projects/{pid}")
    def get_project(pid: str):
        p = app_.get_project(pid)
        p["sources"] = app_.list_sources(pid)
        p["template"] = app_.template_detail(p["template_id"]) if p.get("template_id") else None
        p["mappings"] = app_.get_mappings(pid)
        if jobs:
            p["jobs"] = jobs.list_for_project(pid)
        return p

    @api.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: dict = Body(...)):
        return app_.update_project(pid, body)

    @api.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        app_.delete_project(pid)
        return {"ok": True}

    # ------------------------------------------------------------ sources
    @api.post("/api/projects/{pid}/sources")
    async def upload_sources(pid: str, files: list[UploadFile] = File(...)):
        out = []
        for f in files:
            data = await _read_limited(f)
            out.append(app_.add_source(pid, f.filename or "file", data))
        return out

    @api.get("/api/projects/{pid}/sources")
    def list_sources(pid: str):
        return app_.list_sources(pid)

    @api.put("/api/projects/{pid}/sources/order")
    def reorder(pid: str, body: dict = Body(...)):
        return app_.reorder_sources(pid, body.get("ids", []))

    @api.delete("/api/sources/{sid}")
    def remove_source(sid: str):
        app_.remove_source(sid)
        return {"ok": True}

    @api.post("/api/sources/{sid}/representative")
    def representative(sid: str):
        return app_.set_representative(sid)

    @api.put("/api/sources/{sid}/options")
    def source_options(sid: str, body: dict = Body(...)):
        return app_.set_source_options(sid, body)

    @api.get("/api/sources/{sid}/view")
    def source_view(sid: str, sheet: str | None = None, offset: int = 0, limit: int = Query(200, le=2000), block: int | None = None):
        return views.source_view(sid, sheet, offset, limit, block)

    @api.get("/api/sources/{sid}/blocks")
    def source_blocks(sid: str):
        return app_.source_blocks(sid)

    @api.get("/api/sources/{sid}/search")
    def source_search(sid: str, q: str, block: int | None = None):
        return views.search_source(sid, q, block=block)

    @api.get("/api/sources/{sid}/page/{page}.png")
    def source_page(request: Request, sid: str, page: int, zoom: float = 1.5, block: int | None = None):
        """A rendered page of someone's source document.

        This used to answer `private, max-age=600`, which meant the browser served it from cache
        **without contacting the server** for ten minutes — including after the person signed
        out, was removed from the workspace, or had their session revoked. Every other path
        re-checks authorization on the next request; this one had no next request, so the
        revocation guarantees in docs/security/sessions.md §6.3 did not hold for it.

        The cache is still worth having — re-rasterizing a PDF page is expensive and the UI
        re-requests on every zoom and page change — so the bytes stay in the browser and only
        the permission to see them is re-checked: `no-cache` forces revalidation, the ETag makes
        that revalidation cheap (304, no body, no rasterizing), and the request itself goes
        through the auth gate and per-object authorization like everything else.

        The ETag is derived from the stored content, not from the rendered bytes, so computing
        it does not require doing the work it is meant to avoid.
        """
        etag = views.source_page_etag(sid, page, zoom, block)
        if etag and request.headers.get("if-none-match") == etag:
            # Authorization already ran: source_page_etag goes through the same object lookup.
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"})
        headers = {"Cache-Control": "private, no-cache"}
        if etag:
            headers["ETag"] = etag
        return Response(views.source_page_png(sid, page, zoom, block), media_type="image/png",
                        headers=headers)

    @api.get("/api/sources/{sid}/words/{page}")
    def source_words(sid: str, page: int, block: int | None = None):
        return views.source_words(sid, page, block)

    @api.post("/api/sources/{sid}/ocr")
    def run_ocr(sid: str, body: dict = Body(default={})):
        return views.run_ocr(sid, body.get("pages"))

    @api.put("/api/sources/{sid}/corrections/{mid}")
    def correction(sid: str, mid: str, body: dict = Body(...)):
        return views.set_correction(sid, mid, body.get("value"), bool(body.get("confirmed", True)))

    @api.post("/api/sources/{sid}/locator")
    def build_locator(sid: str, body: dict = Body(...)):
        from ..locators import build_locator as bl
        project = app_.get_project(body["project_id"]) if body.get("project_id") else None
        return bl(app_, sid, body.get("selection") or {}, project, body.get("block"))

    @api.get("/api/projects/{pid}/locate")
    def locate(pid: str, file: str, block: int | None = None):
        from ..locators import locate_all
        return locate_all(app_, pid, file, block)

    @api.get("/api/sources/{sid}/download")
    def source_download(sid: str):
        r = app_.get_source(sid, raw=True)
        return attachment(store.get_blob(r["blob"]), r["filename"])

    # ------------------------------------------------------------ templates
    @api.get("/api/templates")
    def list_templates():
        return app_.list_templates()

    @api.post("/api/templates/upload")
    async def upload_template(file: UploadFile = File(...), name: str | None = Form(None), project_id: str | None = Form(None)):
        data = await _read_limited(file)
        t = app_.upload_template(file.filename or "template", data, name)
        if project_id:
            app_.update_project(project_id, {"template_id": t["id"]})
        return t

    @api.post("/api/templates/builtin")
    async def create_builtin(request: Request, body: dict = Body(...)):
        _guard_editor_body(request)
        from ..builtin_service import create_builtin as cb
        t = cb(app_, body)
        if body.get("project_id"):
            app_.update_project(body["project_id"], {"template_id": t["id"]})
        return t

    @api.put("/api/templates/{tid}/builtin")
    async def save_builtin(request: Request, tid: str, body: dict = Body(...)):
        _guard_editor_body(request)
        from ..builtin_service import save_builtin as sb
        return sb(app_, tid, body)

    @api.get("/api/templates/{tid}")
    def template_detail(tid: str):
        return app_.template_detail(tid)

    @api.patch("/api/templates/{tid}")
    def rename_template(tid: str, body: dict = Body(...)):
        return app_.rename_template(tid, body.get("name", ""))

    @api.delete("/api/templates/{tid}")
    def delete_template(tid: str):
        app_.delete_template(tid)
        return {"ok": True}

    @api.post("/api/templates/{tid}/duplicate")
    def dup_template(tid: str, body: dict = Body(default={})):
        return app_.duplicate_template(tid, body.get("name"))

    @api.get("/api/templates/{tid}/view")
    def template_view(tid: str, sheet: str | None = None, offset: int = 0, limit: int = Query(200, le=2000), version: int | None = None):
        return views.template_view(tid, sheet, offset, limit, version)

    @api.get("/api/templates/{tid}/page/{page}.png")
    def template_page(tid: str, page: int, zoom: float = 1.5, which: str = "working"):
        return Response(views.template_page_png(tid, page, zoom, which), media_type="image/png")

    @api.post("/api/templates/{tid}/slots")
    def add_slot(tid: str, body: dict = Body(...)):
        return app_.add_slot(tid, body)

    @api.post("/api/templates/{tid}/pdf-repeat-detect")
    def pdf_repeat_detect(tid: str, body: dict = Body(...)):
        return app_.pdf_repeat_detect(tid, int(body.get("page", 0)), body.get("nrect") or [])

    @api.patch("/api/templates/{tid}/slots/{slot_id}")
    def update_slot(tid: str, slot_id: str, body: dict = Body(...)):
        return app_.update_slot(tid, slot_id, body)

    @api.delete("/api/templates/{tid}/slots/{slot_id}")
    def remove_slot(tid: str, slot_id: str, project_id: str | None = None, cascade: bool = True):
        return app_.remove_slot(tid, slot_id, project_id, cascade)

    @api.post("/api/templates/{tid}/undo")
    def t_undo(tid: str):
        return app_.template_undo(tid)

    @api.post("/api/templates/{tid}/redo")
    def t_redo(tid: str):
        return app_.template_redo(tid)

    @api.get("/api/templates/{tid}/download")
    def template_download(tid: str, which: str = "working"):
        t = app_.get_template(tid)
        v = app_.get_version(tid, t["head"])
        blob = t["original_blob"] if which == "original" else v["working_blob"]
        ext = {"builtin_doc": "docx", "builtin_sheet": "xlsx"}.get(t["format"], t["format"])
        img = (t.get("info") or {}).get("image")
        if img:   # image template: the upload itself, or the working copy (a one-page PDF with the fields)
            if which == "original":
                blob, ext = img["original_blob"], {"jpeg": "jpg"}.get(img.get("source_format") or "png", img.get("source_format") or "png")
            else:
                ext = "pdf"
        suffix = "（原始）" if which == "original" else f"（工作副本 v{v['version_no']}）"
        return attachment(store.get_blob(blob), f"{t['name']}{suffix}.{ext}", ext)

    # ------------------------------------------------------------ records / rules
    @api.get("/api/projects/{pid}/records")
    def records(pid: str, offset: int = 0, limit: int = Query(200, le=5000)):
        return views.records(pid, offset, limit)

    @api.post("/api/projects/{pid}/rule-preview")
    def rule_preview(pid: str, body: dict = Body(...)):
        return views.rule_preview(pid, body)

    @api.get("/api/projects/{pid}/validate")
    def validate(pid: str):
        return views.validate(pid)

    # ------------------------------------------------------------ mappings
    @api.get("/api/projects/{pid}/mappings")
    def get_mappings(pid: str):
        return app_.get_mappings(pid)

    @api.put("/api/projects/{pid}/mappings")
    def put_mappings(pid: str, body: dict = Body(...)):
        return app_.save_mappings(pid, body.get("mappings", []), body.get("note", "修改映射"), body.get("base_rev"))

    @api.post("/api/projects/{pid}/mappings/undo")
    def m_undo(pid: str):
        return app_.mappings_undo(pid)

    @api.post("/api/projects/{pid}/mappings/redo")
    def m_redo(pid: str):
        return app_.mappings_redo(pid)

    # ------------------------------------------------------------ preview
    @api.get("/api/projects/{pid}/preview")
    def preview(pid: str, index: int | None = None, key: str | None = None, search: str | None = None):
        return views.preview(pid, index, key, search)

    @api.get("/api/preview-files/{blob}")
    def preview_file(blob: str, fmt: str = "docx", name: str = "预览"):
        return attachment(views.preview_file(blob), f"{name}.{fmt}", fmt)

    @api.get("/api/projects/{pid}/preview-layout")
    def preview_layout(pid: str, blob: str):
        return views.preview_layout(pid, blob)

    @api.get("/api/preview-files/{blob}/view")
    def preview_view(blob: str, fmt: str, sheet: str | None = None, offset: int = 0, limit: int = Query(200, le=2000)):
        from ..views import render_output
        return render_output(fmt, views.preview_file(blob), sheet, offset, limit, [])

    @api.get("/api/artifacts/{aid}/view")
    def artifact_view(aid: str, sheet: str | None = None, offset: int = 0, limit: int = Query(200, le=2000)):
        from ..views import render_output
        a, data = jobs.artifact(aid)
        return render_output(a["format"], data, sheet, offset, limit, [])

    @api.get("/api/preview-files/{blob}/page/{page}.png")
    def preview_page(blob: str, page: int, zoom: float = 1.5):
        from ..engine.pdf_engine import render_page_png
        from ..engine.pdf_engine import as_pdf_bytes
        return Response(render_page_png(as_pdf_bytes(views.preview_file(blob)), page, zoom), media_type="image/png")

    # ------------------------------------------------------------ jobs
    @api.get("/api/projects/{pid}/generation-summary")
    def gen_summary(pid: str, formats: str | None = None, naming: str | None = None):
        return jobs.summary(pid, formats.split(",") if formats else None, naming)

    @api.post("/api/projects/{pid}/jobs")
    def create_job(pid: str, body: dict = Body(...)):
        return jobs.create(pid, body)

    @api.get("/api/projects/{pid}/jobs")
    def list_jobs(pid: str):
        return jobs.list_for_project(pid)

    @api.get("/api/jobs/{jid}")
    def get_job(jid: str):
        return jobs.get(jid)

    @api.get("/api/jobs/{jid}/items")
    def job_items(jid: str, status: str | None = None, offset: int = 0, limit: int = Query(500, le=5000)):
        jobs.get(jid)          # carries the workspace check, as /runs, /events and /zip already did
        return jobs.items(jid, status, offset, limit)

    @api.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: str):
        return jobs.cancel(jid)

    @api.post("/api/jobs/{jid}/retry")
    def retry_job(jid: str, body: dict = Body(default={})):
        return jobs.retry(jid, bool(body.get("include_skipped")), idem_key=body.get("idempotency_key"))

    @api.get("/api/jobs/{jid}/runs")
    def job_runs(jid: str):
        jobs.get(jid)
        return jobs.runs(jid)

    @api.get("/api/jobs/{jid}/events")
    def job_events(jid: str, after: int = 0, limit: int = Query(200, le=1000)):
        jobs.get(jid)
        return jobs.events(jid, after, limit)

    @api.get("/api/jobs/{jid}/zip")
    def job_zip(jid: str, fmt: str | None = None):
        """Stream the archive from disk rather than buffering it.

        `zip_file` already writes to a temporary file and its docstring already says streaming
        "keeps memory flat for large jobs" — and then this route called `jobs.zip()`, which read
        the whole file straight back into memory. Measured on a large job: ~+222 MB of RSS for a
        download that needs ~13 MB, on a container limited to 3 GB. The `/api/v1` twin has
        always done it correctly; only the UI route had the buffered copy.

        `FileResponse` also brings range requests, so an interrupted download resumes instead of
        starting the whole archive again.
        """
        from starlette.background import BackgroundTask
        j = jobs.get(jid)
        path = jobs.zip_file(jid, fmt)
        name = safe_output_name(f"{j['template']['name']}_批量结果_{jid[-6:]}.zip", 200)
        ascii_fallback = name.encode("ascii", "ignore").decode() or f"{jid[-6:]}.zip"
        cd = (f"attachment; filename=\"{ascii_fallback}\"; "
              f"filename*=UTF-8''{urllib.parse.quote(name)}")
        return FileResponse(path, media_type="application/zip",
                            background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
                            headers={"Content-Disposition": cd, "Cache-Control": "no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @api.get("/api/jobs/{jid}/report.csv")
    def job_report(jid: str):
        jobs.get(jid)          # same check its siblings make; without it the CSV crossed workspaces
        return attachment(jobs.report_csv(jid), f"生成清单_{jid[-6:]}.csv", "csv")

    @api.get("/api/artifacts/{aid}")
    def artifact(aid: str, inline: bool = False):
        a, data = jobs.artifact(aid)
        return attachment(data, a["filename"], a["format"], inline=inline and a["format"] == "pdf")

    @api.get("/api/artifacts/{aid}/page/{page}.png")
    def artifact_page(aid: str, page: int, zoom: float = 1.2):
        from ..engine.pdf_engine import render_page_png
        a, data = jobs.artifact(aid)
        if a["format"] not in ("pdf", "png", "jpg"):
            raise ServiceError(400, "not_pdf", "只有 PDF / 图片结果可以渲染页面")
        from ..engine.pdf_engine import as_pdf_bytes
        return Response(render_page_png(as_pdf_bytes(data), page, zoom), media_type="image/png")

    # ------------------------------------------------------------ machine API (documented in docs/integration/api.md)
    from .v1 import build_router
    api.include_router(build_router(app_, views, jobs, machine, attachment))

    return app_


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    """DocWeave on its own: the workbench, the machine API, and the single-sign-on client.

    No portal build and no account pages are served here; the account service is a separate
    origin and this process is a relying party on it. `synwevo_server` is what puts several
    roles into one process for the single-process deployment.
    """
    from synwevo_auth_client.routes import (build_account_redirect_router,
                                             build_router as build_rp_router)
    from synwevo_auth_client.session_routes import build_router as build_session_router
    plat = build_platform(data_dir)
    api = new_api("Synwevo Docs")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=env.get("ROLE") or "app")
    # Probes first, so the published OpenAPI document lists them where it always has. Route
    # order is not behaviour, but a generated file that reshuffles on every refactor is one
    # nobody reads the diff of.
    install_probes(api, plat.store, worker=lambda: bool(getattr(api.state, "jobs", None) and api.state.jobs.runner_alive()))
    app_ = install(api, plat, start_jobs=start_jobs)
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
    api.include_router(build_account_redirect_router())

    dist = web_dist()
    if mount_web(api, dist):
        @api.get("/{full_path:path}")
        def spa(full_path: str):
            return shell_response(dist, full_path)

    return api


def _guard_editor_body(request: Request) -> None:
    """Refuse an editor document too large to hold in memory, with a message someone can act on.

    Uploaded *files* go through `_read_limited` and are capped. This route does not take a file:
    it takes the editor's document model as JSON, and Starlette reads that body into memory in
    full and parses it before any handler runs. There was no limit on it at all.

    That matters because images arrive inline. The editor inserts a picked or pasted image as a
    `data:` URI in the document model (Tiptap is configured `allowBase64`), so an 11 MB photo
    becomes ~16 MB of base64 inside the JSON — and several of them, on a container limited to
    3 GB, is an out-of-memory kill rather than an error page.

    Checked against Content-Length so the refusal happens before the body is read, and phrased
    so the person knows the fix is a smaller image rather than a smaller document.
    """
    declared = request.headers.get("content-length")
    if not declared:
        return                                  # chunked: the ASGI server's own limits apply
    try:
        size = int(declared)
    except ValueError:
        return
    if size > LIMITS.max_editor_body_bytes:
        mb = LIMITS.max_editor_body_bytes // (1024 * 1024)
        raise ServiceError(413, "editor_body_too_large",
                           f"这份文档有 {size / (1024 * 1024):.1f} MB，超过 {mb} MB 上限。"
                           "通常是插入的图片太大——图片是连同文档一起提交的。"
                           "请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。")


async def _read_limited(f: UploadFile) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > LIMITS.max_file_bytes:
            raise ServiceError(413, "too_large", f"文件「{f.filename}」超过 {LIMITS.max_file_bytes // (1024 * 1024)} MB 上限")
        chunks.append(chunk)
    return b"".join(chunks)
