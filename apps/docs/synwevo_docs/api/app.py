"""Synwevo Docs 的 HTTP 接口：浏览器工作台与机器接口。

engine 包不依赖任何 Web 框架，所以将来的桌面外壳可以直接复用它；这一层只做 HTTP。

会话网关、错误契约、探针和静态外壳的通用部分已经移到 `synwevo_core.http`——它们对账户服务、
门户和这里是同一套，留在这里就意味着账户服务要导入 Synwevo Docs 才能有一个会话。这里剩下的全部是
Synwevo Docs 自己的业务路由。
"""
from __future__ import annotations

import json
import time
import os
import urllib.parse
from pathlib import Path
from fastapi import Body, FastAPI, File, Form, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response

from synwevo_core import authority, env
from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)
from synwevo_core.store import Store
from synwevo_core.auth import ui_auth_mode

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
    """Mount Synwevo Docs' own routes onto an application the caller has already scaffolded.

    Returns the business service object so a composer can hold on to it. Nothing here touches
    accounts, the OIDC provider or the portal — those are mounted by their own modules.
    """
    from ..webhooks import WebhookDispatcher
    store, machine = plat.store, plat.machine
    app_ = App(store)
    views = Views(app_)
    # The platform inbox: job outcomes and refused files are told to the person concerned.
    from synwevo_core.notifications import Notifier as _Notifier
    notifier = _Notifier(store)
    api.state.notifier = notifier
    jobs = JobService(app_, notifier=notifier) if start_jobs else None
    hooks = WebhookDispatcher(store, jobs, start=start_jobs)
    app_.on_event = hooks.notify                      # job state changes wake the dispatcher
    api.state.app = app_
    api.state.views = views
    api.state.jobs = jobs
    api.state.webhooks = hooks
    # Which workspace role each browser route needs (viewer / write / export). Read by the
    # platform gate; the classification lives next to the other object checks in authz.py.
    from .. import authz as _authz
    api.state.route_policy = _authz.route_policy
    if start_jobs:
        # A file left 'parsing' by a previous process is parsed again, instead of forever.
        app_.resume_parsing()
    # Approved copies of personal projects into organization workspaces are executed here, the
    # only process that can read the source rows and write the target rows.
    from synwevo_core.project_copies import ProjectCopies as _ProjectCopies
    from ..copies import CopyExecutor, CopyRunner, announce_request, build_manifest
    copies = _ProjectCopies(store, plat.identity)
    api.state.copy_runner = CopyRunner(CopyExecutor(app_, copies, notifier), copies, start=start_jobs)
    api.state.copies = copies                          # the machine copy-request routes (v1_copies)
    api.state.platform_audit = plat.audit

    def _uid(request: Request) -> str:
        s = getattr(request.state, "session", None)
        if not s:
            # A deployment without accounts (SYNWEVO_UI_AUTH=none / basic) has no person to name:
            # answering 401 there sent the browser to a sign-in that does not exist (the auth
            # client reads a confirmed 401 as an expired session). Say what is true instead.
            if ui_auth_mode() != "session":
                raise ServiceError(403, "accounts_disabled", "此部署未启用账户，这个功能需要登录的成员")
            raise ServiceError(401, "unauthorized", "请先登录")
        return s["user_id"]

    # ------------------------------------------------------------------ live co-editing (built-in document templates)
    from ..collab import CollabHub as _CollabHub
    collab = _CollabHub(store, app_, plat.identity, plat.sessions, gate=lambda req, path: api.state.session_gate(req, path))
    api.state.collab = collab
    app_.on_template_change = collab.on_template_change
    app_.on_project_change = collab.on_project_change
    app_.live_template_state = collab.live_state
    # the live-table switch as this process reads it (docs-sheet-live.md §9): a trial's workspace id
    # mistyped would keep it off without a word — said once at start-up
    from ..collab import sheet_live_report as _sheet_live_report
    _sl = _sheet_live_report(store)
    if _sl["mode"] != "off":
        import logging as _logging
        _lg = _logging.getLogger("synwevo.docs.collab")
        if _sl["mode"] == "on":
            _lg.info("live tables (SYNWEVO_SHEET_LIVE): on for every workspace")
        else:
            _lg.info("live tables (SYNWEVO_SHEET_LIVE): on for %d listed workspace(s): %s", len(_sl["workspaces"]), ", ".join(_sl["workspaces"]) or "(none)")
            if _sl["unknown"]:
                _lg.warning("live tables (SYNWEVO_SHEET_LIVE): listed ids that name no workspace here (nothing is live for them): %s", ", ".join(_sl["unknown"]))

    def _shutdown() -> None:
        """The end of this process (uvicorn's graceful shutdown, a test client leaving its
        context): stop what this installer started, write the live documents, release the parse
        pool. Before this nothing was ever stopped; every application built in a test kept its
        runner, dispatcher and copy threads — and through them its store — alive."""
        if jobs is not None:
            jobs.stop()
        hooks.stop()
        api.state.copy_runner.stop()
        collab.shutdown()
        app_.close()
    api.state.shutdown_hooks.append(_shutdown)

    def _actor_id(request: Request) -> str | None:
        s = getattr(request.state, "session", None)
        return s["user_id"] if s else None

    @api.websocket("/api/collab/templates/{tid}")
    async def collab_socket(websocket: WebSocket, tid: str):
        """The live document of a built-in document template. Session, workspace, application and
        role are decided by the platform gate on the handshake (collab.CollabHub.serve)."""
        await collab.serve(websocket, tid)

    @api.get("/api/templates/{tid}/collab/changes")
    def collab_changes(tid: str, limit: int = Query(100, ge=1, le=500)):
        app_.get_template(tid)
        return collab.changes(tid, limit)

    @api.websocket("/api/collab/projects/{pid}")
    async def project_socket(websocket: WebSocket, pid: str):
        """Who has this project open and what they are on, plus live change frames for it —
        no shared document: mappings, sources and settings keep their own saved revisions."""
        await collab.serve(websocket, pid, kind="project")

    @api.get("/api/projects/{pid}/activity")
    def project_activity(pid: str, limit: int = Query(100, ge=1, le=500)):
        app_.get_project(pid)
        return collab.activity(pid, limit)

    @api.get("/api/copy-targets")
    def copy_targets(request: Request):
        """Organization workspaces this person can write to, with their organizations — the only
        targets a copy request may name. A workspace they cannot see is not listed."""
        uid = _uid(request)
        out = []
        for w in plat.identity.workspaces_for(uid):
            if w.get("kind") == "org" and w.get("status") == "active" and w.get("organization_id") and w.get("role") in ("editor", "admin", "owner"):
                org = store.one("SELECT name, status FROM organizations WHERE id=?", (w["organization_id"],)) or {}
                if org.get("status") == "active":
                    out.append({"id": w["id"], "name": w["name"], "role": w["role"], "organization_id": w["organization_id"], "organization_name": org.get("name"),
                                "can_self_approve": copies.can_decide(w["organization_id"], w["id"], uid)})
        here = store.one("SELECT kind FROM workspaces WHERE id=?", (current_tenant(),)) or {}
        return {"items": out, "source_is_personal": here.get("kind") == "personal"}

    @api.get("/api/projects/{pid}/copy-manifest")
    def copy_manifest(request: Request, pid: str, sources: int = 0, corrections: int = 0):
        _uid(request)
        manifest, digest = build_manifest(app_, pid, {"sources": bool(sources), "corrections": bool(corrections)})
        return {"manifest": manifest, "digest": digest}

    @api.get("/api/projects/{pid}/copy-requests")
    def copy_requests(request: Request, pid: str):
        uid = _uid(request)
        app_.get_project(pid)
        return {"items": [copies.public(r, for_requester=True) for r in copies.list_for_requester(uid, source_project_id=pid)]}

    @api.post("/api/projects/{pid}/copy-requests", status_code=201)
    def request_copy(request: Request, pid: str, body: dict = Body(...)):
        """`{"target_workspace_id", "name", "purpose"?, "scope": {"sources"?, "corrections"?}, "decide"?: "approve"}`.
        The manifest is built now and frozen with the request; `decide` lets a requester who
        also holds the deciding authority approve in the same step (recorded as self-approval)."""
        uid = _uid(request)
        if set(body) - {"target_workspace_id", "name", "purpose", "scope", "decide", "reason"}:
            raise ServiceError(400, "invalid_field", "复制申请包含不支持的字段")
        scope = body.get("scope") or {}
        if not isinstance(scope, dict):
            raise ServiceError(400, "invalid_field", "scope 必须是对象", {"field": "scope"})
        manifest, digest = build_manifest(app_, pid, scope)
        # The request row and its record commit together (G26); the service's own transaction
        # nests in this one.
        with store.tx():
            r = copies.create(requester_id=uid, source_workspace_id=current_tenant(), source_project_id=pid,
                              target_workspace_id=str(body.get("target_workspace_id") or ""), name=str(body.get("name") or manifest["project"]["name"]),
                              purpose=body.get("purpose"), scope=scope, manifest=manifest, digest=digest)
            plat.audit(request, "docs.copy.request", actor_id=uid, workspace_id=current_tenant(), object_kind="project_copy", object_id=r["id"], result="ok",
                       meta={"project_id": pid, "target_workspace_id": r["target_workspace_id"], "scope": r["scope"], "digest": digest})
        if body.get("decide") == "approve":
            with store.tx():
                r = copies.decide(r["id"], uid, "approve", reason=str(body.get("reason") or ""))
                plat.audit(request, "docs.copy.decide", actor_id=uid, object_kind="project_copy", object_id=r["id"], result="ok",
                           meta={"action": "approve", "self_approved": True, "organization_id": r["target_organization_id"]})
            api.state.copy_runner.wake()
        else:
            announce_request(store, plat.identity, copies, notifier, r, uid, manifest["project"]["name"])
        return copies.public(r, for_requester=True)

    # ------------------------------------------------------------------ comments and mentions
    from ..comments import Comments as _Comments
    comments = _Comments(app_, plat.identity, notifier)

    def _limited(uid: str, bucket: str, limit: int, window_s: int) -> None:
        allowed, wait = plat.identity.window_allows(f"{bucket}:{uid}", limit=limit, window_s=window_s)
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, int(wait or 60))})

    @api.get("/api/projects/{pid}/comments")
    def list_comments(request: Request, pid: str, include_resolved: bool = False):
        _uid(request)
        return {"items": comments.list(pid, include_resolved=include_resolved)}

    @api.get("/api/projects/{pid}/comment-members")
    def comment_members(request: Request, pid: str):
        _uid(request)
        return {"items": comments.members(pid)}

    @api.post("/api/projects/{pid}/comments", status_code=201)
    def create_comment(request: Request, pid: str, body: dict = Body(...)):
        uid = _uid(request)
        _limited(uid, "comment", 60, 60)
        return comments.create(pid, uid, body.get("body"), anchor=body.get("anchor"), mentions=body.get("mentions"), parent_id=body.get("parent_id"))

    @api.patch("/api/comments/{cid}")
    def edit_comment(request: Request, cid: str, body: dict = Body(...)):
        uid = _uid(request)
        return comments.edit(cid, uid, body.get("body"), mentions=body.get("mentions"))

    @api.delete("/api/comments/{cid}")
    def delete_comment(request: Request, cid: str):
        comments.delete(cid, _uid(request))          # audited by the service, in its transaction
        return {"ok": True}

    @api.post("/api/comments/{cid}/resolve")
    def resolve_comment(request: Request, cid: str):
        return comments.resolve(cid, _uid(request), True)

    @api.post("/api/comments/{cid}/reopen")
    def reopen_comment(request: Request, cid: str):
        return comments.resolve(cid, _uid(request), False)

    @api.post("/api/copy-requests/{cid}/withdraw")
    def withdraw_copy(request: Request, cid: str):
        uid = _uid(request)
        with store.tx():
            r = copies.withdraw(cid, uid)
            plat.audit(request, "docs.copy.withdraw", actor_id=uid, object_kind="project_copy", object_id=cid, result="ok")
        return copies.public(r, for_requester=True)

    # ------------------------------------------------------------ regression cases & desensitization
    # Captured cases hold the workspace's source files verbatim, so every route works inside the
    # current workspace only and they are stored under the data directory (regression.py).
    # These routes used to list, download, replay and delete every workspace's cases.
    @api.get("/api/regression-cases")
    def regression_list():
        from ..regression import list_cases
        return list_cases(current_tenant(), store.root)

    @api.post("/api/projects/{pid}/regression-cases")
    def regression_capture(pid: str, body: dict = Body(default={})):
        from ..regression import RegressionError, capture
        try:
            return capture(app_, pid, body.get("name") or "", body.get("note") or "", body.get("sample_keys"),
                           tenant=current_tenant())
        except RegressionError as e:
            raise ServiceError(400, e.code, str(e)) from e

    @api.post("/api/regression-cases/{cid}/run")
    def regression_run(cid: str):
        from ..regression import RegressionError, run
        try:
            return run(cid, current_tenant(), store.root)
        except RegressionError as e:
            raise ServiceError(404 if e.code == "not_found" else 400, e.code, str(e)) from e

    @api.delete("/api/regression-cases/{cid}")
    def regression_delete(cid: str):
        from ..regression import RegressionError, delete
        try:
            delete(cid, current_tenant(), store.root, store=store)
        except RegressionError as e:
            raise ServiceError(404 if e.code == "not_found" else 400, e.code, str(e)) from e
        return {"ok": True}

    @api.get("/api/regression-cases/{cid}/download")
    def regression_download(cid: str):
        import io as _io
        import zipfile
        from ..regression import RegressionError, load
        try:
            _, d = load(cid, current_tenant(), store.root)
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
            raise ServiceError(404, "source_offer_off", "本部署已关闭源代码下载（运营方以其他方式提供对应源代码，或运行未经修改、不含第三方 AGPL 组件的版本）")
        data, name = source_archive()
        return Response(data, media_type="application/gzip",
                        headers={"Content-Disposition": f"attachment; filename=\"{name}\""})

    @api.get("/api/capabilities")
    def capabilities():
        from ..capabilities import capability_report
        return {**capability_report(), "limits": app_.upload_limits()}

    # ------------------------------------------------------------ projects
    PAGE_PARAMS = ("q", "sort", "order", "offset", "limit", "archived")

    @api.get("/api/projects")
    def list_projects(request: Request, archived: int = 0, q: str | None = None, sort: str | None = None,
                      order: str | None = None, offset: int = 0, limit: int = 50):
        """Without parameters: the whole list, as it always was. With any of q / sort / order /
        offset / limit / archived: one page, ``{items, total, offset, limit, next_offset}``."""
        if any(k in request.query_params for k in PAGE_PARAMS):
            return app_.list_projects_page(q, sort, order, offset, limit, include_archived=bool(archived))
        return app_.list_projects()

    @api.post("/api/projects")
    def create_project(body: dict = Body(...)):
        return app_.create_project(body.get("name", ""), origin="ui")

    def _copied_from(p: dict) -> dict | None:
        """Where a copied project came from, for the people who can open it: the request, the
        source project's name, who asked and who approved. Never the source workspace or files."""
        origin = p.get("origin") or ""
        if not origin.startswith("copy:"):
            return None
        r = copies.get(origin[5:])
        if not r:
            return {"request_id": origin[5:]}
        m = r.get("manifest") or {}
        view = copies.public(r, for_requester=True)
        return {"request_id": r["id"], "source_project_name": (m.get("project") or {}).get("name") or r["name"],
                "requester": view.get("requester"), "decider": view.get("decider"), "decided_at": r.get("decided_at"),
                "executed_at": r.get("executed_at"), "self_approved": bool(r.get("self_approved")),
                "organization_id": r["target_organization_id"], "sources_included": bool((r.get("scope") or {}).get("sources"))}

    @api.get("/api/projects/{pid}")
    def get_project(pid: str):
        p = app_.get_project(pid)
        p["sources"] = app_.list_sources(pid)
        p["copied_from"] = _copied_from(p)
        p["template"] = None
        if p.get("template_id"):
            try:
                p["template"] = app_.template_detail(p["template_id"])
            except ServiceError as e:
                if e.code == "rejected_malware":
                    # A rescan found the template's bytes infected (CI-90): the project opens so
                    # someone can choose another template; the template itself is not served.
                    p["template_rejected"] = True
                elif e.status != 404:
                    raise
                else:
                    # The template was deleted while this project still used it (possible before
                    # deletion refused templates in use). The project must still open so the person
                    # can choose another template; it used to 404 as a whole.
                    p["template_missing"] = True
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
    async def upload_sources(request: Request, pid: str, files: list[UploadFile] = File(...)):
        desc = authority.describe_request(request)
        actor = desc.get("user_id") if desc.get("kind") == "user" else None
        from starlette.concurrency import run_in_threadpool
        out = []
        for f in files:
            data = await _read_limited(f, app_)
            # hashing and storing up to the upload limit is not event-loop work
            out.append(await run_in_threadpool(app_.add_source, pid, f.filename or "file", data, actor=actor))
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

    @api.post("/api/sources/{sid}/scan/retry", status_code=202)
    def retry_source_scan(request: Request, sid: str, body: dict = Body(...)):
        if set(body) != {"version"}:
            raise ServiceError(400, "invalid", "请求仅包含来源版本", {"field": "version"})
        desc = authority.describe_request(request)
        return app_.retry_source_scan(sid, body["version"], actor_kind="user" if desc.get("user_id") else "system",
                                      actor_id=desc.get("user_id") or desc.get("key_id"), request=request)

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
        app_.require_accepted(r)          # quarantined or rejected bytes are never served
        return attachment(store.get_blob(r["blob"]), r["filename"])

    # ------------------------------------------------------------ templates
    @api.get("/api/templates")
    def list_templates():
        return app_.list_templates()

    @api.post("/api/templates/upload")
    async def upload_template(file: UploadFile = File(...), name: str | None = Form(None), project_id: str | None = Form(None)):
        from starlette.concurrency import run_in_threadpool
        data = await _read_limited(file, app_)
        # the security check (clamd round trip, container check) and the template analysis run
        # off the event loop: one upload must not stall every other request
        t = await run_in_threadpool(app_.upload_template, file.filename or "template", data, name)
        if project_id:
            app_.update_project(project_id, {"template_id": t["id"]})
        return t

    @api.post("/api/templates/builtin")
    async def create_builtin(request: Request):
        body = await _editor_body(request)
        from ..builtin_service import create_builtin as cb
        t = cb(app_, body)
        if body.get("project_id"):
            app_.update_project(body["project_id"], {"template_id": t["id"]})
        return t

    @api.put("/api/templates/{tid}/builtin")
    async def save_builtin(request: Request, tid: str):
        body = await _editor_body(request)
        from ..builtin_service import save_builtin as sb
        app_.get_template(tid)                             # another workspace's template: 404 before anything about its room
        live = collab.live_sheet_room(tid)
        if live is not None and body.get("solo") is not True:
            # docs-sheet-live.md §3: while the table is edited live, a page saving its own whole copy
            # would bypass everyone's operations; it reloads into the live room. `solo`: the
            # single-person fallback, folded into the live table like any outside version.
            raise ServiceError(409, "live_room", "这张表格正在实时协同：请刷新页面进入实时协同后再编辑",
                               {"online": [p["name"] for p in live.peers() if not p.get("readonly")], "epoch": live.epoch})
        with collab.acting(tid, _actor_id(request)):
            return sb(app_, tid, body, merge=True)

    @api.post("/api/templates/{tid}/collab/save")
    async def collab_save(request: Request, tid: str, body: dict = Body(default={})):
        """Save a live table room (docs-sheet-live.md §3): the room's state at this moment becomes a
        new version with the saver's note; edits made while it is being built stay unsaved. 409
        ``not_live`` when the template has no live table room open."""
        app_.get_template(tid)
        return await collab.save_live(tid, _actor_id(request), body.get("note"))

    @api.get("/api/templates/{tid}")
    def template_detail(tid: str):
        return app_.template_detail(tid)

    # ------------------------------------------------------------------ organization-shared templates
    from ..org_templates import OrgTemplates as _OrgTemplates
    shared = _OrgTemplates(app_, plat.identity)

    @api.get("/api/organization-templates")
    def list_shared_templates(request: Request, include_deprecated: bool = False):
        if ui_auth_mode() != "session" and not getattr(request.state, "session", None):
            return {"organization": None, "items": []}          # no organizations without accounts: nothing to list, no error
        return shared.list(_uid(request), include_deprecated=include_deprecated)

    @api.get("/api/organization-templates/{pid}")
    def shared_template(request: Request, pid: str):
        return shared.get(pid, _uid(request))

    @api.post("/api/templates/{tid}/publish", status_code=201)
    def publish_template(request: Request, tid: str, body: dict = Body(default={})):
        return shared.publish(tid, _uid(request), body.get("description"))      # audited by the service, in its transaction

    @api.post("/api/organization-templates/{pid}/update")
    def update_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        return shared.update(pid, _uid(request), body.get("description"), set_description="description" in body)

    @api.post("/api/organization-templates/{pid}/deprecate")
    def deprecate_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        return shared.set_deprecated(pid, _uid(request), body.get("deprecated", True) is not False, body.get("note"))

    @api.post("/api/organization-templates/{pid}/use", status_code=201)
    def use_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        uid = _uid(request)
        t = shared.use(pid, uid, body.get("name"))
        if body.get("project_id"):
            values = {"template_id": t["id"]}
            if body.get("base_settings_rev") is not None:
                values["base_settings_rev"] = body["base_settings_rev"]
            app_.update_project(body["project_id"], values)
        return t

    @api.patch("/api/templates/{tid}")
    def rename_template(tid: str, body: dict = Body(...)):
        return app_.rename_template(tid, body.get("name", ""))

    @api.delete("/api/templates/{tid}")
    def delete_template(request: Request, tid: str):
        with collab.acting(tid, _actor_id(request)):
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

    # Undo / redo carry the state they were decided on (``head`` / ``base_rev``); when someone
    # else changed it meanwhile the step is refused with 409 instead of undoing their edit.
    @api.get("/api/templates/{tid}/usage")
    def template_usage(tid: str):
        return app_.template_usage(tid)

    @api.post("/api/templates/{tid}/restore")
    def restore_template_version(request: Request, tid: str, body: dict = Body(...)):
        try:
            no = int(body.get("version"))
        except (TypeError, ValueError):
            raise ServiceError(400, "bad_value", "version 必须是正整数", {"field": "version"}) from None
        base = body.get("base_head")
        with collab.acting(tid, _actor_id(request)):
            return app_.restore_version(tid, no, int(base) if base is not None else None, discard_live=body.get("discard_live") is True)

    @api.post("/api/templates/{tid}/undo")
    def t_undo(request: Request, tid: str, body: dict = Body(default={})):
        with collab.acting(tid, _actor_id(request)):
            return app_.template_undo(tid, _opt_int(body, "head"), discard_live=body.get("discard_live") is True)

    @api.post("/api/templates/{tid}/redo")
    def t_redo(request: Request, tid: str, body: dict = Body(default={})):
        with collab.acting(tid, _actor_id(request)):
            return app_.template_redo(tid, _opt_int(body, "head"), discard_live=body.get("discard_live") is True)

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
        force = body.get("force_ids")
        if force is not None and (not isinstance(force, list) or not all(isinstance(x, str) for x in force)):
            raise ServiceError(400, "bad_value", "force_ids 必须是映射 id 列表", {"field": "force_ids"})
        return app_.save_mappings(pid, body.get("mappings", []), body.get("note", "修改映射"), body.get("base_rev"), force_ids=force)

    @api.post("/api/projects/{pid}/mappings/undo")
    def m_undo(pid: str, body: dict = Body(default={})):
        return app_.mappings_undo(pid, _opt_int(body, "base_rev"))

    @api.post("/api/projects/{pid}/mappings/redo")
    def m_redo(pid: str, body: dict = Body(default={})):
        return app_.mappings_redo(pid, _opt_int(body, "base_rev"))

    # ------------------------------------------------------------ preview
    @api.get("/api/projects/{pid}/preview")
    def preview(request: Request, pid: str, index: int | None = None, key: str | None = None, search: str | None = None,
                tz: str | None = None):
        # TODAY()/NOW() in the viewer's time zone and at the viewer's moment (CI-95)
        from ..clock import current_instant, moment, resolve_timezone
        from ..engine.formula import as_of
        zone = resolve_timezone(tz, _actor_id(request), app_.store)
        with as_of(moment(current_instant(), zone)):
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
    def gen_summary(request: Request, pid: str, formats: str | None = None, naming: str | None = None, simulate: bool = False,
                    simulate_limit: int | None = Query(None, ge=1)):
        """``simulate=true`` also runs the renderer over the valid samples, in memory, without
        storing anything; ``simulate_limit`` caps how many (never more than the server's cap).

        The plain summary is reading; a simulation spends real rendering time, so it takes the
        same role as submitting the job it previews. The route table cannot say that (it is
        per path, not per query), so it is checked here against the role the gate resolved."""
        if simulate:
            role = getattr(request.state, "workspace_role", None)
            if role is not None and role not in authority.WRITE_ROLES:
                raise ServiceError(403, "read_only_role", "模拟生成需要编辑权限；查看者可以查看不含模拟的生成前检查结果。",
                                   {"role": role})
        sim = (simulate_limit or True) if simulate else False
        return jobs.summary(pid, formats.split(",") if formats else None, naming, simulate=sim)

    @api.get("/api/projects/{pid}/generation-issues")
    def gen_issues(pid: str, level: str | None = None, offset: int = 0, limit: int = Query(100, ge=1, le=1000)):
        """Every located issue of every sample, paged (the summary shows only the first 50)."""
        return jobs.preflight_issues(pid, level, offset, limit)

    @api.post("/api/projects/{pid}/jobs")
    def create_job(request: Request, pid: str, body: dict = Body(...)):
        # Who asked is stored with the job, so the runner can stop if they lose access.
        return jobs.create(pid, body, origin="ui", principal=authority.describe_request(request))

    @api.get("/api/projects/{pid}/jobs")
    def list_jobs(pid: str):
        return jobs.list_for_project(pid)

    @api.get("/api/jobs/{jid}")
    def get_job(jid: str):
        return jobs.get(jid)

    @api.get("/api/jobs/{jid}/items")
    def job_items(jid: str, status: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000),
                  paged: bool = False):
        """Items filtered by status in SQL. ``paged=true`` returns ``{items, total, offset, limit,
        next_offset}`` so a page of a 20,000-sample job can be shown without loading the rest."""
        jobs.get(jid)          # carries the workspace check, as /runs, /events and /zip already did
        rows = jobs.items(jid, status, offset, limit)
        if not paged:
            return rows
        total = jobs.count_items(jid, status)
        nxt = offset + len(rows)
        return {"items": rows, "total": total, "offset": offset, "limit": limit, "next_offset": nxt if nxt < total else None}

    @api.post("/api/jobs/{jid}/cancel")
    def cancel_job(request: Request, jid: str):
        return jobs.cancel(jid, principal=authority.describe_request(request))

    @api.post("/api/jobs/{jid}/retry")
    def retry_job(request: Request, jid: str, body: dict = Body(default={})):
        # A retry is a new run authorised by whoever asked for it, not by the original submitter.
        return jobs.retry(jid, body.get("include_skipped", False), idem_key=body.get("idempotency_key"),
                          principal=authority.describe_request(request), failed_only=body.get("failed_only", False))

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
    """Synwevo Docs on its own: the workbench, the machine API, and the single-sign-on client.

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
    install_probes(api, plat.store, worker=lambda: bool(getattr(api.state, "jobs", None) and api.state.jobs.runner_alive()),
                   degradable={"malware_scanner": lambda: api.state.app.scanner_health()})
    app_ = install(api, plat, start_jobs=start_jobs)
    # The process that *is* Synwevo Docs registers it, as the composer does for role=app: the
    # gate's application checks (enabled, entitlement, per-workspace) and the portal card read
    # that registration. Idempotent; it keeps the access policy an operator chose, and it decides
    # from the role whether this process's SYNWEVO_APP_URL is Synwevo Docs' own address.
    from synwevo_core.apps import bootstrap_docs_app
    bootstrap_docs_app(plat.store, plat.identity)
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
    api.include_router(build_account_redirect_router())

    dist = web_dist()
    if mount_web(api, dist):
        @api.get("/{full_path:path}")
        def spa(full_path: str):
            return shell_response(dist, full_path)

    return api


def _editor_too_large(size: int | None) -> ServiceError:
    mb = LIMITS.max_editor_body_bytes // (1024 * 1024)
    what = f"这份文档有 {size / (1024 * 1024):.1f} MB，" if size else "这份文档"
    return ServiceError(413, "editor_body_too_large",
                        f"{what}超过 {mb} MB 上限。"
                        "通常是插入的图片太大——图片是连同文档一起提交的。"
                        "请换用更小的图片，或把它裁剪、压缩之后再插入。你的其它修改还在编辑器里，没有丢失。",
                        {"limit_mb": mb, "size_mb": round(size / (1024 * 1024), 1) if size else None})


async def _editor_body(request: Request) -> dict:
    """The editor's document model, read with the size limit enforced *while it arrives*.

    Uploaded *files* go through `_read_limited` and are capped. These routes take the editor's
    document model as JSON, and images arrive inline in it as `data:` URIs (Tiptap is configured
    `allowBase64`), so an 11 MB photo becomes ~16 MB of base64 — several of them, on a container
    limited to 3 GB, is an out-of-memory kill rather than an error page.

    The handlers used to declare `body: dict = Body(...)`, which makes FastAPI read and parse the
    whole body before the handler — and its Content-Length check — ever ran, and a chunked body
    skipped the check entirely. Now a declared size over the limit is refused before anything is
    read, and an undeclared (chunked) one as soon as the bytes received pass the limit.
    """
    # FastAPI's `Body(...)` only parsed a JSON body when the request said it was JSON, and that
    # is what kept a cross-site text/plain <form> post (which a browser sends with cached Basic
    # credentials and without a preflight) from reaching these handlers in the basic/none UI
    # modes, where no double-submit CSRF token is checked. Reading the stream ourselves must
    # keep that rule.
    ctype = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    main, _, sub = ctype.partition("/")
    if main != "application" or not (sub == "json" or sub.endswith("+json")):
        raise ServiceError(415, "unsupported_media_type", "请求内容必须是 JSON（Content-Type: application/json）",
                           {"content_type": ctype or None})
    limit = LIMITS.max_editor_body_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise _editor_too_large(int(declared))
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise _editor_too_large(None)
    try:
        body = json.loads(bytes(buf) or b"{}")
    except ValueError as e:
        raise ServiceError(400, "bad_json", "请求内容不是有效的 JSON") from e
    if not isinstance(body, dict):
        raise ServiceError(400, "bad_json", "请求内容必须是 JSON 对象")
    return body


def _opt_int(body: dict, key: str) -> int | None:
    """An optional integer field of a small JSON body (a revision a client says it saw)."""
    v = (body or {}).get(key) if isinstance(body, dict) else None
    if v is None:
        return None
    if isinstance(v, bool):
        raise ServiceError(400, "bad_value", f"{key} 必须是整数", {"field": key})
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise ServiceError(400, "bad_value", f"{key} 必须是整数", {"field": key}) from e


async def _read_limited(f: UploadFile, app_: App) -> bytes:
    if f.size is not None:
        app_.require_upload_size(f.size, f.filename or "file")
    chunks = []
    total = 0
    while True:
        chunk = await f.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        app_.require_upload_size(total, f.filename or "file")
        chunks.append(chunk)
    return b"".join(chunks)
