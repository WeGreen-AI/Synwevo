"""Machine API (`/api/v1`).

Same engine, same services and the same job runner as the web UI – only the transport and
the access control differ.  Every route needs an API key (`Authorization: Bearer dw_…`),
works inside that key's tenant, and returns the error envelope defined in
`docs/integration/api.md`.  Nothing here is exposed in the product UI.
"""
from __future__ import annotations

import json
import os
from fastapi import APIRouter, Body, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from platform_core.context import as_tenant
from platform_core.errors import ServiceError
from platform_core.store import now

from .. import definitions as D
from ..capabilities import capability_report
from ..engine.limits import LIMITS

API_VERSION = "1.0"
IMPORT_ROOTS = [p for p in (os.environ.get("DOCWEAVE_IMPORT_ROOTS") or "").split(os.pathsep) if p.strip()]
LINK_TTL_S = int(os.environ.get("DOCWEAVE_DOWNLOAD_LINK_TTL_S", "900"))
PUBLIC_URL = (os.environ.get("DOCWEAVE_PUBLIC_URL") or "").strip().rstrip("/")


def public_base(request: Request) -> str:
    """The origin to put in a download link.

    ``request.base_url`` is built from what the *immediate* client said, which behind a reverse
    proxy is wrong in two ways.  The scheme is the proxy→origin scheme, so a service reached
    over HTTPS hands out ``http://`` links — and a signed link is a credential, so that is not
    merely untidy.  And with a proxy that does not preserve ``Host`` (nginx's default is
    ``$proxy_host``) the host becomes the origin's own address, so the caller is handed
    ``http://127.0.0.1:8765/...``, which resolves to their own machine.

    ``DOCWEAVE_PUBLIC_URL`` is the deterministic answer and is what a deployment behind a proxy
    should set.  Failing that, honour the forwarded headers — uvicorn does not apply them unless
    it is started with proxy headers enabled, and enabling those globally would also change the
    client address the rate limiter sees.  Spoofing them only changes the URL handed back to the
    spoofer; the token itself is signed and verified independently of how it was addressed.
    """
    if PUBLIC_URL:
        return PUBLIC_URL
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if not host:
        return str(request.base_url).rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{host}"


def _principal(request: Request) -> dict:
    p = getattr(request.state, "principal", None)
    if not p:
        raise ServiceError(401, "unauthorized", "缺少 API 密钥")
    return p


def _page(rows: list, offset: int, limit: int, total: int) -> dict:
    nxt = offset + len(rows)
    return {"items": rows, "total": total, "offset": offset, "limit": limit, "next_offset": nxt if nxt < total else None}


def build_router(app_, views, jobs, auth, attachment) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["v1"])

    def scope(request: Request, needed: str) -> dict:
        p = _principal(request)
        auth.require(p, needed)
        return p

    # ------------------------------------------------------------ system
    @r.get("/capabilities", summary="本部署真实可用的格式与依赖")
    def capabilities(request: Request):
        scope(request, "read")
        rep = capability_report()
        return {"api_version": API_VERSION, **rep, "limits": {
            "max_file_mb": LIMITS.max_file_bytes // (1024 * 1024), "max_pages": LIMITS.max_pages,
            "max_cells": LIMITS.max_cells, "max_records_per_job": LIMITS.max_records_per_job}}

    @r.get("/whoami", summary="当前密钥的租户与权限")
    def whoami(request: Request):
        p = _principal(request)
        return {"key_id": p["key_id"], "name": p["name"], "tenant": p["tenant"], "scopes": list(p["scopes"])}

    # ------------------------------------------------------------ projects
    @r.post("/projects", status_code=201, summary="创建项目")
    def create_project(request: Request, body: dict = Body(default={})):
        p = scope(request, "write")
        return app_.create_project(body.get("name") or "自动化项目", origin=f"api:{p['key_id']}")

    @r.get("/projects", summary="项目列表")
    def list_projects(request: Request, offset: int = 0, limit: int = Query(50, le=200)):
        scope(request, "read")
        rows = app_.list_projects()
        return _page(rows[offset:offset + limit], offset, limit, len(rows))

    @r.get("/projects/{pid}", summary="项目详情（来源、模板、规则、映射）")
    def get_project(request: Request, pid: str):
        scope(request, "read")
        p = app_.get_project(pid)
        tpl = app_.template_detail(p["template_id"]) if p.get("template_id") else None
        return {"id": p["id"], "name": p["name"], "created_at": p["created_at"], "updated_at": p["updated_at"],
                "rule": p.get("record_rule") or {}, "output": p.get("output") or {}, "mapping_rev": p["mapping_rev"],
                "template": None if not tpl else {"id": tpl["id"], "name": tpl["name"], "format": tpl["format"],
                                                  "version": p.get("template_version") or tpl["version"]["no"],
                                                  "pinned": bool(p.get("template_version")), "fields": len(tpl["version"]["slots"])},
                "files": app_.list_sources(pid), "mappings": len(app_.get_mappings(pid)["mappings"])}

    @r.delete("/projects/{pid}", summary="删除项目")
    def delete_project(request: Request, pid: str):
        scope(request, "admin")
        app_.delete_project(pid)
        return {"deleted": pid}

    @r.put("/projects/{pid}/rule", summary="保存样本规则")
    def put_rule(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        rule = body.get("rule", body)
        app_.update_project(pid, {"record_rule": rule})
        preview = views.rule_preview(pid, rule)
        return {"rule": rule, "records": preview["total"], "file_errors": preview["file_errors"], "warnings": preview.get("warnings", [])}

    @r.put("/projects/{pid}/template", summary="绑定模板（可固定版本）")
    def put_template(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        tid = body.get("template_id")
        app_.get_template(tid)
        values = {"template_id": tid}
        if body.get("version") is not None:
            values["template_version"] = int(body["version"])
        app_.update_project(pid, values)
        return get_project(request, pid)

    @r.get("/projects/{pid}/mappings", summary="读取映射")
    def get_mappings(request: Request, pid: str):
        scope(request, "read")
        m = app_.get_mappings(pid)
        return {"rev": m["rev"], "mappings": m["mappings"]}

    @r.put("/projects/{pid}/mappings", summary="保存映射（带并发校验）")
    def put_mappings(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        maps = body.get("mappings")
        if not isinstance(maps, list):
            raise ServiceError(400, "invalid", "mappings 必须是数组", {"field": "mappings"})
        out = app_.save_mappings(pid, maps, body.get("note") or "API 保存映射", body.get("base_rev"))
        return {"rev": out["rev"], "mappings": out["mappings"]}

    @r.get("/projects/{pid}/definition", summary="导出项目定义（模板绑定 + 规则 + 映射）")
    def get_definition(request: Request, pid: str):
        scope(request, "read")
        return D.export_project(app_, pid)

    @r.put("/projects/{pid}/definition", summary="导入项目定义")
    def put_definition(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        return D.import_project(app_, pid, body)

    @r.post("/projects/{pid}/preflight", summary="预检：样本数、有效数、预计输出、错误与警告")
    def preflight(request: Request, pid: str, body: dict = Body(default={})):
        scope(request, "read")
        return jobs.summary(pid, body.get("formats"), body.get("naming"))

    # ------------------------------------------------------------ files
    @r.post("/projects/{pid}/files", status_code=201, summary="上传来源文件（multipart 或授权目录下的路径）")
    async def upload_file(request: Request, pid: str, file: UploadFile | None = File(default=None)):
        scope(request, "write")
        app_.get_project(pid)
        if file is not None:
            from .app import _read_limited
            data = await _read_limited(file)
            return app_.add_source(pid, file.filename or "upload.bin", data)
        body = {}
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
        path = (body or {}).get("path")
        if not path:
            raise ServiceError(400, "invalid", "请以 multipart 上传文件，或提供 path（需配置 DOCWEAVE_IMPORT_ROOTS）", {"field": "file"})
        data, name = _read_from_roots(path)
        return app_.add_source(pid, name, data)

    @r.get("/projects/{pid}/files", summary="项目的来源文件")
    def list_files(request: Request, pid: str):
        scope(request, "read")
        app_.get_project(pid)
        rows = app_.list_sources(pid)
        return _page(rows, 0, len(rows), len(rows))

    @r.get("/files/{fid}", summary="来源文件状态与结构")
    def get_file(request: Request, fid: str):
        scope(request, "read")
        r_ = app_.get_source(fid, raw=True)
        app_.get_project(r_["project_id"])                      # tenant check
        return app_._source_out(r_)

    @r.delete("/files/{fid}", summary="从项目移除来源文件")
    def delete_file(request: Request, fid: str):
        scope(request, "write")
        r_ = app_.get_source(fid, raw=True)
        app_.get_project(r_["project_id"])
        app_.remove_source(fid)
        return {"deleted": fid}

    # ------------------------------------------------------------ templates
    @r.post("/templates", status_code=201, summary="上传模板")
    async def upload_template(request: Request, file: UploadFile = File(...), name: str | None = None):
        scope(request, "write")
        from .app import _read_limited
        data = await _read_limited(file)
        return app_.upload_template(file.filename or "template.docx", data, name)

    @r.get("/templates", summary="模板列表")
    def list_templates(request: Request):
        scope(request, "read")
        rows = app_.list_templates()
        return _page(rows, 0, len(rows), len(rows))

    @r.get("/templates/{tid}", summary="模板详情与版本")
    def get_template(request: Request, tid: str):
        scope(request, "read")
        d = app_.template_detail(tid)
        return {"id": d["id"], "name": d["name"], "format": d["format"], "kind": d["kind"], "head": d["head"],
                "version": d["version"]["no"], "fields": d["version"]["slots"], "versions": d["versions"],
                "exports": d["exports"], "info": d.get("info")}

    @r.get("/templates/{tid}/fields", summary="导出字段定义")
    def get_fields(request: Request, tid: str):
        scope(request, "read")
        return D.export_template_fields(app_, tid)

    @r.post("/templates/{tid}/fields", summary="按声明式定义创建字段")
    def post_fields(request: Request, tid: str, body: dict = Body(...)):
        scope(request, "write")
        return D.import_template_fields(app_, tid, body)

    @r.get("/templates/{tid}/content", summary="下载模板文件（原始或工作副本）")
    def template_content(request: Request, tid: str, which: str = "original"):
        scope(request, "read")
        t = app_.get_template(tid)
        v = app_.get_version(tid, t["head"])
        blob = t["original_blob"] if which == "original" else v["working_blob"]
        ext = {"builtin_doc": "docx", "builtin_sheet": "xlsx"}.get(t["format"], t["format"])
        return attachment(app_.store.get_blob(blob), f"{t['name']}.{ext}", ext)

    @r.delete("/templates/{tid}", summary="删除模板")
    def delete_template(request: Request, tid: str):
        scope(request, "admin")
        app_.delete_template(tid)
        return {"deleted": tid}

    # ------------------------------------------------------------ jobs
    @r.post("/projects/{pid}/jobs", status_code=202, summary="提交生成任务（支持 Idempotency-Key）")
    def create_job(request: Request, pid: str, body: dict = Body(default={})):
        p = scope(request, "write")
        key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
        job = jobs.create(pid, body, tenant=p["tenant"], idem_key=key, origin=f"api:{p['key_id']}")
        return _job_out(job)

    @r.get("/projects/{pid}/jobs", summary="项目的任务列表")
    def project_jobs(request: Request, pid: str):
        scope(request, "read")
        app_.get_project(pid)
        rows = jobs.list_for_project(pid)
        return _page(rows, 0, len(rows), len(rows))

    @r.get("/jobs/{jid}", summary="任务状态与计数")
    def get_job(request: Request, jid: str):
        scope(request, "read")
        return _job_out(jobs.get(jid))

    @r.get("/jobs/{jid}/items", summary="逐样本结果（可按状态过滤、分页）")
    def job_items(request: Request, jid: str, status: str | None = None, offset: int = 0, limit: int = Query(200, le=1000)):
        scope(request, "read")
        jobs.get(jid)
        total = app_.store.one("SELECT COUNT(*) AS n FROM job_items WHERE job_id=?" + (" AND status=?" if status else ""),
                               (jid, status) if status else (jid,))["n"]
        return _page(jobs.items(jid, status, offset, limit), offset, limit, total)

    @r.get("/jobs/{jid}/runs", summary="任务的运行记录（初次与重试）")
    def job_runs(request: Request, jid: str):
        scope(request, "read")
        jobs.get(jid)
        return {"items": jobs.runs(jid)}

    @r.get("/jobs/{jid}/events", summary="任务事件（轮询用，seq 递增）")
    def job_events(request: Request, jid: str, after: int = 0, limit: int = Query(200, le=1000)):
        scope(request, "read")
        jobs.get(jid)
        evs = jobs.events(jid, after, limit)
        return {"items": evs, "next_after": evs[-1]["seq"] if evs else after}

    @r.post("/jobs/{jid}/cancel", summary="取消任务")
    def cancel_job(request: Request, jid: str):
        scope(request, "write")
        return _job_out(jobs.cancel(jid))

    @r.post("/jobs/{jid}/retry", summary="重试失败项（沿用原快照，可带 Idempotency-Key）")
    def retry_job(request: Request, jid: str, body: dict = Body(default={})):
        p = scope(request, "write")
        key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
        return _job_out(jobs.retry(jid, bool(body.get("include_skipped")), idem_key=key, requested_by=f"api:{p['key_id']}"))

    @r.get("/jobs/{jid}/artifacts", summary="产物清单")
    def job_artifacts(request: Request, jid: str, offset: int = 0, limit: int = Query(500, le=2000)):
        scope(request, "read")
        jobs.get(jid)
        total = app_.store.one("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND deleted_at IS NULL", (jid,))["n"]
        rows = app_.store.all("SELECT a.id, a.item_id, a.format, a.filename, a.size, a.blob, a.created_at, a.run_no, i.seq, i.label "
                              "FROM artifacts a JOIN job_items i ON i.id=a.item_id WHERE a.job_id=? AND a.deleted_at IS NULL "
                              "ORDER BY i.seq, a.format LIMIT ? OFFSET ?", (jid, limit, offset))
        for a in rows:
            a["sha256"] = a.pop("blob")
        return _page(rows, offset, limit, total)

    @r.get("/artifacts/{aid}/content", summary="下载一个产物")
    def artifact_content(request: Request, aid: str):
        scope(request, "read")
        a, data = jobs.artifact(aid)
        resp = attachment(data, a["filename"], a["format"])
        resp.headers["X-Content-SHA256"] = a["blob"]
        return resp

    @r.post("/artifacts/{aid}/link", summary="生成有时效的下载链接（无需密钥即可下载）")
    def artifact_link(request: Request, aid: str):
        p = scope(request, "read")
        a, _ = jobs.artifact(aid)
        token, exp = auth.sign_link("artifact", aid, p["tenant"], LINK_TTL_S)
        return {"url": public_base(request) + f"/api/v1/download/{token}", "expires_at": exp,
                "filename": a["filename"], "sha256": a["blob"], "size": a["size"]}

    @r.get("/jobs/{jid}/archive", summary="下载全部产物（ZIP，含生成清单）")
    def job_archive(request: Request, jid: str, format: str | None = None):
        scope(request, "read")
        j = jobs.get(jid)
        return _zip_response(jobs, jid, format, j)

    @r.post("/jobs/{jid}/archive-link", summary="生成 ZIP 的有时效下载链接")
    def archive_link(request: Request, jid: str, body: dict = Body(default={})):
        p = scope(request, "read")
        jobs.get(jid)
        ident = jid if not body.get("format") else f"{jid}|{body['format']}"
        token, exp = auth.sign_link("archive", ident, p["tenant"], LINK_TTL_S)
        return {"url": public_base(request) + f"/api/v1/download/{token}", "expires_at": exp}

    @r.get("/jobs/{jid}/report.csv", summary="生成清单（CSV）")
    def job_report(request: Request, jid: str):
        scope(request, "read")
        jobs.get(jid)
        return attachment(jobs.report_csv(jid), f"report_{jid}.csv", "csv")

    @r.get("/download/{token}", summary="按签名链接下载（链接本身即凭据）")
    def signed_download(token: str):
        data = auth.open_link(token)
        with as_tenant(data["t"]):
            if data["k"] == "artifact":
                a, blob = jobs.artifact(data["i"])
                resp = attachment(blob, a["filename"], a["format"])
                resp.headers["X-Content-SHA256"] = a["blob"]
                return resp
            jid, _, fmt = data["i"].partition("|")
            j = jobs.get(jid)
            return _zip_response(jobs, jid, fmt or None, j)

    return r


def _job_out(j: dict) -> dict:
    counts = j.get("counts") or {}
    out = {"id": j["id"], "project_id": j["project_id"], "status": j["status"], "run_no": j.get("run_no", 1),
           "created_at": j["created_at"], "started_at": j.get("started_at"), "finished_at": j.get("finished_at"),
           "template": j.get("template"), "options": j.get("options"), "error": j.get("error"),
           "cancel_requested": bool(j.get("cancel_requested")),
           "counts": {"samples": counts.get("total", 0), "documents": counts.get("success", 0), "files": counts.get("files", 0),
                      "failed": counts.get("failed", 0), "skipped": counts.get("skipped", 0), "cancelled": counts.get("cancelled", 0),
                      "pending": counts.get("pending", 0), "running": counts.get("running", 0)}}
    for k in ("cancel", "idempotent_replay"):
        if k in j:
            out[k] = j[k]
    return out


def _zip_response(jobs, jid: str, fmt: str | None, j: dict) -> Response:
    from starlette.background import BackgroundTask
    path = jobs.zip_file(jid, fmt)
    name = f"{j['template']['name']}_{jid}.zip"
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _read_from_roots(path: str) -> tuple[bytes, str]:
    """Read a file the caller names by path – only inside an explicitly configured root."""
    from pathlib import Path
    if not IMPORT_ROOTS:
        raise ServiceError(400, "path_not_allowed", "本部署未开放按路径读取文件；请用 multipart 上传，或配置 DOCWEAVE_IMPORT_ROOTS", {"field": "path"})
    target = Path(path).expanduser()
    try:
        real = target.resolve(strict=True)
    except OSError as e:
        raise ServiceError(404, "file_not_found", f"找不到文件：{path}", {"field": "path"}) from e
    for root in IMPORT_ROOTS:
        rp = Path(root).expanduser().resolve()
        if real == rp or rp in real.parents:
            if real.stat().st_size > LIMITS.max_file_bytes:
                raise ServiceError(413, "too_large", "文件超过大小上限")
            return real.read_bytes(), real.name
    raise ServiceError(403, "path_not_allowed", f"路径不在授权目录内（DOCWEAVE_IMPORT_ROOTS）：{path}", {"field": "path"})


def _reachable_schemas(spec: dict) -> dict:
    """The component schemas referenced, directly or transitively, by the kept paths."""
    schemas = (spec.get("components") or {}).get("schemas") or {}

    def refs(node, out):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                out.add(ref.rsplit("/", 1)[1])
            for v in node.values():
                refs(v, out)
        elif isinstance(node, list):
            for v in node:
                refs(v, out)
        return out

    wanted, frontier = set(), refs(spec["paths"], set())
    while frontier:
        name = frontier.pop()
        if name in wanted or name not in schemas:
            continue
        wanted.add(name)
        frontier |= refs(schemas[name], set())
    return {k: v for k, v in schemas.items() if k in wanted}


def openapi_subset(api) -> dict:
    """The OpenAPI document of the machine API (v1 routes and the health probes)."""
    spec = api.openapi()
    keep = {p: v for p, v in spec["paths"].items() if p.startswith("/api/v1") or p in ("/healthz", "/readyz")}
    spec = json.loads(json.dumps(spec))
    spec["paths"] = keep
    spec["info"] = {"title": "DocWeave 机器接口", "version": API_VERSION,
                    "description": "文档批量生成引擎的自动化接口。所有 /api/v1 路由都需要 API 密钥（Authorization: Bearer dw_…）。"
                                   "详见 docs/integration/api.md。"}
    spec["components"] = spec.get("components", {})
    spec["components"]["securitySchemes"] = {"apiKey": {"type": "http", "scheme": "bearer", "bearerFormat": "dw_<id>_<secret>"}}
    spec["security"] = [{"apiKey": []}]
    for path, ops in spec["paths"].items():
        for op in ops.values():
            if isinstance(op, dict) and path in ("/healthz", "/readyz"):
                op["security"] = []
    # Drop schemas no kept path can reach. FastAPI generates a component for every route in the
    # app, so without this the machine-API document also carried request bodies belonging to the
    # UI and the OIDC endpoints — routes this file deliberately does not describe.
    spec["components"]["schemas"] = _reachable_schemas(spec)
    spec["x-generated-at"] = None                       # stable file: no timestamp
    spec.pop("x-generated-at")
    return spec
