"""Machine API (`/api/v1`).

Same engine, same services and the same job runner as the web UI – only the transport and
the access control differ.  Every route needs a machine credential (a `dw_…` API key or a
platform access token), works inside that credential's workspace, and returns the error
envelope defined in `docs/integration/api.md`.  Nothing here is exposed in the product UI.

Contract rules this module keeps in one place:

* **Creating POSTs accept `Idempotency-Key`** (see `idempotency.py`); job creation and retry
  keep their transactional dedupe in `JobService` and share only the key validation.
* **Lists are bounded and pageable two ways**: `offset`/`limit` (kept for compatibility, with
  `total` and `next_offset`) and an opaque `cursor` over a stable key (`next_cursor`, plus a
  `Link: <…>; rel="next"` header). Page sizes are validated, never silently unbounded.
* **Deferred work carries who asked for it**: jobs, retries and signed links store an authority
  descriptor (`synwevo_core.authority`), and a signed link is re-authorised at download.
* **Legacy inputs are signalled, not removed** (RFC 9745 `Deprecation` + `Link rel=deprecation`).
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from synwevo_core import authority, env
from synwevo_core.context import UI_TENANT, as_tenant
from synwevo_core.context import tenant as current_tenant
from synwevo_core.errors import ServiceError, not_found

from .. import definitions as D
from ..capabilities import capability_report
from ..engine.limits import LIMITS
from ..jobs import ProjectContext
from ..services import scan_out
from . import idempotency as I
from .v1_uploads import build_router as build_uploads_router
from .v1_uploads import upload_body_schema, use_upload

API_VERSION = "1.0"
# Entries separated by os.pathsep. An entry containing `{tenant}` is a per-workspace root (the
# placeholder becomes the caller's workspace id); an entry without it is the legacy shared root
# and serves only the legacy workspace (`SYNWEVO_UI_TENANT`, default `default`).
IMPORT_ROOTS = [p for p in (env.docs("IMPORT_ROOTS") or "").split(os.pathsep) if p.strip()]
LINK_TTL_S = int(env.docs("DOWNLOAD_LINK_TTL_S", "900"))
PUBLIC_URL = (env.get("PUBLIC_URL") or "").strip().rstrip("/")
# Where docs/integration/api.md is published for integrators (an absolute http(s) URL). The
# deprecation Link points there; without it, only a URN names the section.
API_DOC_URL = (env.docs("API_DOC_URL") or "").strip()
# The token endpoint written into the exported OpenAPI file. It lives on the account service
# ($ISSUER), not on this host, so a relative tokenUrl resolved to the wrong server. The exported
# file shows the local development issuer; `openapi_subset(api, issuer=...)` names another.
DEV_ISSUER = "http://accounts.localhost:8770"
# A simulated preflight renders every sample it covers, synchronously, in the request. It is
# bounded like a page size rather than by the per-job limit, and needs write scope: it costs
# what a job costs, without the job queue's admission control.
SIMULATE_DEFAULT, SIMULATE_MAX = 50, 200

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
ITEM_LIMIT, ITEM_MAX = 200, 1000               # per-sample results: documented larger pages
ARTIFACT_LIMIT, ARTIFACT_MAX = 500, 2000       # artifact manifests: one row per output file
ISSUE_LIMIT, ISSUE_MAX = 100, 1000
ITEM_STATUSES = ("pending", "running", "success", "failed", "skipped", "cancelled")
TEMPLATE_CONTENT = ("original", "working")
SCOPE_NAMES = {"read": "docweave.read", "write": "docweave.write", "admin": "docweave.manage"}
# `export` is read access plus, for a person acting through a delegated token, the right to take
# files off the platform (can_export). A credential never carries it; OpenAPI shows read.
SCOPE_AS = {"export": "read"}
# OpenAPI scopes an operation may declare with x-scope; `any` = any valid credential.
X_SCOPES = ("read", "write", "admin", "export", "any")
_SAFE_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# RFC 9745 / RFC 8594. One entry per legacy input still accepted; `sunset` stays None until a
# removal date is decided, and must never be earlier than `since`.
DEPRECATION_URN = "urn:synwevo:doc:integration/api.md#deprecations"
DEPRECATIONS = {
    "job-body-idempotency-key": {
        "since": calendar.timegm((2026, 9, 24, 0, 0, 0)),
        "sunset": None,
        "fields": ("idempotency_key", "request_id"),
        "replacement": "Idempotency-Key 请求头",
    },
}


def deprecation_doc() -> str:
    """The deprecation Link target: the published api.md section when configured, else the URN.

    RFC 9745 means the link for a person to read. This service does not serve its developer
    documentation (no product entry point for it), so the published location is configuration.
    """
    base = API_DOC_URL
    if base.startswith(("https://", "http://")) and not any(c in base for c in '<> "'):
        return base.split("#", 1)[0] + "#deprecations"
    return DEPRECATION_URN


def deprecation_headers(name: str) -> dict:
    d = DEPRECATIONS[name]
    out = {"Deprecation": f"@{int(d['since'])}",
           "Link": f'<{deprecation_doc()}>; rel="deprecation"'}
    if d.get("sunset") is not None:
        if d["sunset"] < d["since"]:
            raise RuntimeError(f"deprecation {name}: Sunset earlier than Deprecation")
        from email.utils import formatdate
        out["Sunset"] = formatdate(d["sunset"], usegmt=True)
    return out


def public_base(request: Request) -> str:
    """Configured public origin, or the request origin with explicitly trusted proxy hops.

    A signed URL is a credential. Do not send clients to a caller-supplied forwarded host.
    Multi-proxy values use the same right-to-left hop convention as audit and rate limiting.
    """
    if PUBLIC_URL:
        return PUBLIC_URL
    from synwevo_core.http import trusted_proxy_hops
    hops = trusted_proxy_hops()
    def forwarded(name, fallback):
        values = [v.strip() for v in ",".join(request.headers.getlist(name)).split(",") if v.strip()]
        return (values[-hops] if len(values) >= hops else values[0]) if hops and values else fallback
    host = forwarded("x-forwarded-host", request.headers.get("host") or request.url.netloc)
    proto = forwarded("x-forwarded-proto", request.url.scheme)
    parsed = urllib.parse.urlsplit(f"{proto}://{host}")
    if proto not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise ServiceError(400, "invalid_origin", "服务公开地址无效；请核对代理或 SYNWEVO_PUBLIC_URL 配置")
    return f"{proto}://{host}"


def _principal(request: Request) -> dict:
    p = getattr(request.state, "principal", None)
    if not p:
        raise ServiceError(401, "unauthorized", "缺少 API 密钥")
    return p


def _ox(scope: str | None = None, *, idempotent: bool = False, binary: tuple[str, ...] = (), **extra) -> dict:
    """OpenAPI annotations for a route, read back by `openapi_subset`."""
    out = dict(extra)
    if scope:
        out["x-scope"] = scope
    if idempotent:
        out["x-idempotent"] = True
    if binary:
        out["x-binary"] = list(binary)
    return out


# --------------------------------------------------------------------- pagination
def _limit_q(default: int, maximum: int):
    # Declared without `le`: FastAPI's own validation error is not the /api/v1 error envelope,
    # and a client asking for too much must be told which parameter is wrong.
    return Query(None, description=f"每页条数，默认 {default}，最大 {maximum}",
                 json_schema_extra={"minimum": 1, "maximum": maximum, "default": default})


def _offset_q():
    return Query(0, description="跳过的条数（与 cursor 二选一）", json_schema_extra={"minimum": 0})


def _cursor_q():
    return Query(None, description="上一页返回的 next_cursor（不透明，按稳定排序续读；与 offset 二选一）")


def _check_limit(limit: int | None, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if limit < 1 or limit > maximum:
        raise ServiceError(400, "invalid", f"limit 必须在 1–{maximum} 之间", {"field": "limit"})
    return limit


def _encode_cursor(name: str, key: list, filters: dict | None) -> str:
    raw = json.dumps({"l": name, "k": key, "f": I.fingerprint(filters or {})[:16]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(raw: str, name: str, filters: dict | None, width: int) -> list:
    bad = ServiceError(400, "invalid_cursor", "cursor 无效：请使用上一页响应中的 next_cursor 原样传回", {"field": "cursor"})
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception as e:  # noqa: BLE001 - any malformed cursor is the same client error
        raise bad from e
    if not isinstance(data, dict) or data.get("l") != name or not isinstance(data.get("k"), list) \
            or len(data["k"]) != width or not all(isinstance(v, (int, float, str)) for v in data["k"]):
        raise bad
    if data.get("f") != I.fingerprint(filters or {})[:16]:
        raise ServiceError(400, "invalid_cursor", "cursor 与本次请求的筛选条件不一致；换筛选条件请从第一页开始",
                           {"field": "cursor"})
    return data["k"]


def _next_url(request: Request, **params) -> str:
    keep = [(k, v) for k, v in request.query_params.multi_items() if k not in ("offset", "cursor", "limit")]
    keep += [(k, str(v)) for k, v in params.items()]
    return f"{public_base(request)}{request.url.path}?{urllib.parse.urlencode(keep)}"


def _paged(store, request: Request, response: Response, *, name: str, select: str, frm: str, args: tuple,
           keys: tuple[str, ...], desc: bool, offset: int, limit: int | None, cursor: str | None,
           default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT, filters: dict | None = None) -> dict:
    """One page of `select … frm`, ordered by `keys`, with LIMIT/OFFSET in SQL.

    Keyset order must be total (the last key is always a unique id), so a cursor page never
    skips or repeats a row when rows before it are added or deleted — which offset pages do.
    """
    lim = _check_limit(limit, default, maximum)
    if offset < 0:
        raise ServiceError(400, "invalid", "offset 不能为负数", {"field": "offset"})
    if cursor and offset:
        raise ServiceError(400, "invalid", "cursor 与 offset 不能同时使用", {"field": "cursor"})
    direction = "DESC" if desc else "ASC"
    order = ", ".join(f"{k} {direction}" for k in keys)
    marks = ", ".join(f"{k} AS _k{i}" for i, k in enumerate(keys))
    cond, cargs = "", ()
    if cursor:
        pos = _decode_cursor(cursor, name, filters, len(keys))
        cond = f" AND ({', '.join(keys)}) {'<' if desc else '>'} ({', '.join('?' * len(keys))})"
        cargs = tuple(pos)
    rows = store.all(f"{select}, {marks} {frm}{cond} ORDER BY {order} LIMIT ? OFFSET ?",
                     args + cargs + (lim + 1, 0 if cursor else offset))
    more = len(rows) > lim
    rows = rows[:lim]
    total = store.one(f"SELECT COUNT(*) AS n {frm}", args)["n"]
    nxt = _encode_cursor(name, [rows[-1][f"_k{i}"] for i in range(len(keys))], filters) if more and rows else None
    for r in rows:
        for i in range(len(keys)):
            r.pop(f"_k{i}", None)
    if nxt:
        response.headers["Link"] = f'<{_next_url(request, limit=lim, cursor=nxt)}>; rel="next"'
    return {"items": rows, "total": total, "offset": None if cursor else offset, "limit": lim,
            "next_offset": None if (cursor or not more) else offset + len(rows), "next_cursor": nxt}


# --------------------------------------------------------------------- router
def build_router(app_, views, jobs, auth, attachment) -> APIRouter:
    r = APIRouter(prefix="/api/v1", tags=["v1"])
    store = app_.store
    idem = I.Idempotency(store)

    def scope(request: Request, needed: str) -> dict:
        p = _principal(request)
        auth.require(p, needed)
        return p

    def job_view(j: dict) -> dict:
        # `j` is what JobService.get (or create/cancel/retry, which return it) says; the
        # service owns the defaults (phase of pre-v5 jobs) and the secret-free principal label.
        return _job_out(j)

    # ------------------------------------------------------------ system
    @r.get("/capabilities", summary="本部署真实可用的格式与依赖", openapi_extra=_ox("read"))
    def capabilities(request: Request):
        scope(request, "read")
        rep = capability_report()
        rep["environment"]["malware_scan"] = app_.scan_policy.describe()
        return {"api_version": API_VERSION, **rep, "limits": {
            "max_file_mb": LIMITS.max_file_bytes // (1024 * 1024), "max_pages": LIMITS.max_pages,
            "max_cells": LIMITS.max_cells, "max_records_per_job": LIMITS.max_records_per_job}}

    @r.get("/whoami", summary="当前凭据的租户与权限", openapi_extra=_ox("any"))
    def whoami(request: Request):
        p = _principal(request)
        return {"key_id": p["key_id"], "name": p["name"], "tenant": p["tenant"], "scopes": list(p["scopes"])}

    # ------------------------------------------------------------ projects
    @r.post("/projects", status_code=201, summary="创建项目（支持 Idempotency-Key）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"name": {"type": "string", "maxLength": 100}}}}}}))
    def create_project(request: Request, body: dict = Body(default={})):
        p = scope(request, "write")
        name = body.get("name")
        if name is not None and not isinstance(name, str):
            raise ServiceError(400, "invalid", "name 必须是字符串", {"field": "name"})
        return idem.run(request, "/api/v1/projects", {"body": body},
                        lambda: app_.create_project(name or "自动化项目", origin=f"api:{p['key_id']}"),
                        replay_check=lambda out: app_.get_project(out["id"]), status=201)

    @r.get("/projects", summary="项目列表（按创建时间倒序；offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def list_projects(request: Request, response: Response, offset: int = _offset_q(),
                      limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        scope(request, "read")
        page = _paged(store, request, response, name="projects",
                      select="SELECT id, name, created_at, updated_at, template_id, record_rule, step, origin, archived_at",
                      frm="FROM projects WHERE deleted=0 AND tenant=? AND archived_at IS NULL", args=(current_tenant(),),
                      keys=("COALESCE(created_at, 0)", "id"), desc=True, offset=offset, limit=limit, cursor=cursor)
        app_._with_list_details(page["items"])
        return page

    @r.get("/projects/{pid}", summary="项目详情（来源、模板、规则、映射）", openapi_extra=_ox("read"))
    def get_project(request: Request, pid: str):
        scope(request, "read")
        p = app_.get_project(pid)
        tpl = None
        if p.get("template_id"):
            pinned = p.get("template_version")
            rejected = None
            try:
                d = app_.template_detail(p["template_id"])
            except ServiceError as e:
                if e.code == "rejected_malware":
                    # A rescan found the template's bytes infected (CI-90): say so with its scan
                    # record; the template itself is refused (410) wherever its bytes would be used.
                    rejected = app_.get_template(p["template_id"], accepted=False)
                elif e.status != 404:
                    raise
                # The bound template was deleted. The project itself still exists and must not
                # answer 404 because of it; say what is missing instead.
                d = None
            if rejected is not None:
                tpl = {"id": rejected["id"], "name": rejected["name"], "format": rejected["format"], "version": pinned,
                       "head": rejected["head"], "pinned": bool(pinned), "fields": 0, "rejected": True,
                       "scan": scan_out(rejected.get("scan"))}
            elif d is None:
                tpl = {"id": p["template_id"], "name": None, "format": None, "version": pinned, "head": None,
                       "pinned": bool(pinned), "fields": 0, "missing": True}
            else:
                slots = app_.get_version(d["id"], pinned)["slots"] if pinned else d["version"]["slots"]
                tpl = {"id": d["id"], "name": d["name"], "format": d["format"], "version": pinned or d["version"]["no"],
                       "head": d["head"], "pinned": bool(pinned), "fields": len(slots or [])}
        return {"id": p["id"], "name": p["name"], "created_at": p["created_at"], "updated_at": p["updated_at"],
                "rule": p.get("record_rule") or {}, "output": p.get("output") or {}, "mapping_rev": p["mapping_rev"],
                "settings_rev": int(p.get("settings_rev") or 0), "archived_at": p.get("archived_at"),
                "template": tpl, "files": app_.list_sources(pid), "mappings": len(app_.get_mappings(pid)["mappings"])}

    # Renaming, archiving and output defaults (API-2). The same service as the workbench's project
    # page; base_settings_rev is required here (it is optional there for old browser tabs), so a
    # machine client never overwrites a settings change it has not seen.
    @r.patch("/projects/{pid}", summary="修改项目名称、归档状态或输出默认值（base_settings_rev 必填，不符则 409 stale_project）",
             openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
                 "type": "object", "required": ["base_settings_rev"], "additionalProperties": False, "properties": {
                     "base_settings_rev": {"type": "integer", "minimum": 0, "description": "读取项目时的 settings_rev"},
                     "name": {"type": "string", "minLength": 1, "maxLength": 200},
                     "archived": {"type": "boolean", "description": "归档只从默认列表隐藏，项目照常可用、可恢复"},
                     "output": {"type": "object", "description": "输出默认值：formats、naming、error_policy、layout"}}}}}}))
    def patch_project(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        unknown = sorted(set(body) - {"base_settings_rev", "name", "archived", "output"})
        if unknown:
            raise ServiceError(400, "invalid", f"不支持修改的项目属性：{unknown[0]}", {"field": unknown[0]})
        base = body.get("base_settings_rev")
        if type(base) is not int or base < 0:
            raise ServiceError(400, "invalid", "base_settings_rev 必须是读取项目时的 settings_rev（非负整数）",
                               {"field": "base_settings_rev"})
        values: dict = {"base_settings_rev": base}
        if "name" in body:
            name = body["name"]
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
                raise ServiceError(400, "invalid", "项目名称必须是 1 至 200 个字", {"field": "name"})
            values["name"] = name.strip()
        if "archived" in body:
            if type(body["archived"]) is not bool:
                raise ServiceError(400, "invalid", "archived 必须是布尔值", {"field": "archived"})
            values["archived"] = body["archived"]
        if "output" in body:
            values["output"] = body["output"]
        if len(values) == 1:
            raise ServiceError(400, "invalid", "没有要修改的属性", {"field": "body"})
        # One write transaction: the revision check and the write are one decision, so two clients
        # that read the same revision cannot both succeed.
        with store.tx():
            app_.update_project(pid, values)
        return get_project(request, pid)

    @r.delete("/projects/{pid}", summary="删除项目", openapi_extra=_ox("admin"))
    def delete_project(request: Request, pid: str):
        scope(request, "admin")
        app_.delete_project(pid)
        return {"deleted": pid}

    @r.put("/projects/{pid}/rule", summary="保存样本规则", openapi_extra=_ox("write"))
    def put_rule(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        rule = body.get("rule", body)
        app_.get_project(pid)
        # Validate and preview before saving: a rule the preview rejects must not be stored
        # while the caller is told the request failed.
        app_._validate_rule(rule)
        preview = views.rule_preview(pid, rule)
        app_.update_project(pid, {"record_rule": rule})
        return {"rule": rule, "records": preview["total"], "file_errors": preview["file_errors"], "warnings": preview.get("warnings", [])}

    @r.put("/projects/{pid}/template", summary="绑定模板（可用 version 固定模板版本）",
           openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
               "type": "object", "required": ["template_id"], "properties": {
                   "template_id": {"type": "string"},
                   "version": {"type": ["integer", "null"], "minimum": 1,
                               "description": "固定使用的模板版本；省略或 null 表示始终使用最新版本"}}}}}}))
    def put_template(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        tid = body.get("template_id")
        if not isinstance(tid, str) or not tid:
            raise ServiceError(400, "invalid", "template_id 必须是模板 id", {"field": "template_id"})
        app_.get_project(pid)
        app_.get_template(tid)
        version = body.get("version")
        if version is not None:
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise ServiceError(400, "invalid", "version 必须是正整数", {"field": "version"})
            if not store.one("SELECT 1 AS x FROM template_versions WHERE template_id=? AND version_no=?", (tid, version)):
                raise ServiceError(400, "unknown_version", f"模板 {tid} 没有版本 {version}", {"field": "version"})
        # Always written: rebinding to another template without a version must drop an old pin,
        # which referred to a version of the previous template.
        app_.update_project(pid, {"template_id": tid, "template_version": version})
        return get_project(request, pid)

    @r.get("/projects/{pid}/mappings", summary="读取映射", openapi_extra=_ox("read"))
    def get_mappings(request: Request, pid: str):
        scope(request, "read")
        m = app_.get_mappings(pid)
        return {"rev": m["rev"], "mappings": m["mappings"]}

    @r.put("/projects/{pid}/mappings", summary="保存映射（带并发校验）", openapi_extra=_ox("write"))
    def put_mappings(request: Request, pid: str, body: dict = Body(...)):
        scope(request, "write")
        maps = body.get("mappings")
        if not isinstance(maps, list):
            raise ServiceError(400, "invalid", "mappings 必须是数组", {"field": "mappings"})
        out = app_.save_mappings(pid, maps, body.get("note") or "API 保存映射", body.get("base_rev"))
        return {"rev": out["rev"], "mappings": out["mappings"]}

    @r.get("/projects/{pid}/definition", summary="导出项目定义（模板绑定 + 规则 + 映射）", openapi_extra=_ox("read"))
    def get_definition(request: Request, pid: str):
        scope(request, "read")
        return D.export_project(app_, pid)

    @r.put("/projects/{pid}/definition", summary="导入项目定义（全部校验通过才写入；可带 base_rev 做并发校验）",
           openapi_extra=_ox("write"))
    def put_definition(request: Request, pid: str, body: dict = Body(...),
                       base_rev: int | None = Query(None, description="当前映射版本（mapping_rev）；不一致返回 409 stale")):
        scope(request, "write")
        return D.import_project(app_, pid, body, base_rev=base_rev)

    @r.post("/projects/{pid}/preflight", summary="预检：样本数、有效数、预计输出、错误与警告（simulate 时实际试生成）",
            description=f"不带 simulate 只需 read 权限。simulate 会在请求内逐样本实际渲染，需要 write 权限，"
                        f"且最多覆盖 {SIMULATE_MAX} 个样本（true 为前 {SIMULATE_DEFAULT} 个）；"
                        "要覆盖全部样本请提交任务。",
            openapi_extra=_ox("read", requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {
                    "formats": {"type": "array", "items": {"type": "string"}},
                    "naming": {"type": "string"},
                    "simulate": {"oneOf": [{"type": "boolean"}, {"type": "integer", "minimum": 1, "maximum": SIMULATE_MAX}],
                                 "default": False,
                                 "description": "在内存中逐样本试生成并丢弃结果，报告只在生成时才会出现的问题；不写入任何产物。"
                                                f"true 模拟前 {SIMULATE_DEFAULT} 个有效样本，正整数 N（最大 {SIMULATE_MAX}）"
                                                "模拟前 N 个；响应的 simulation.limited 表示是否还有未模拟的样本。需要 write 权限"}}}}}}))
    def preflight(request: Request, pid: str, body: dict = Body(default={})):
        sim = body.get("simulate", False)
        scope(request, "read" if sim is False or sim is None else "write")
        formats, naming = _formats_naming(body.get("formats"), body.get("naming"))
        n = _simulate_count(sim)
        if not n:
            # The default preflight keeps its exact old call.
            return jobs.summary(pid, formats, naming)
        return jobs.summary(pid, formats, naming, simulate=n)

    @r.get("/projects/{pid}/preflight/issues", summary="预检问题明细（逐样本、逐字段，分页）", openapi_extra=_ox("read"))
    def preflight_issues(request: Request, response: Response, pid: str,
                         level: str | None = Query(None, description="error 或 warning；省略为全部"),
                         offset: int = _offset_q(), limit: int | None = _limit_q(ISSUE_LIMIT, ISSUE_MAX),
                         formats: list[str] | None = Query(None), naming: str | None = None):
        scope(request, "read")
        if level not in (None, "error", "warning"):
            raise ServiceError(400, "invalid", "level 只能是 error 或 warning", {"field": "level"})
        lim = _check_limit(limit, ISSUE_LIMIT, ISSUE_MAX)
        if offset < 0:
            raise ServiceError(400, "invalid", "offset 不能为负数", {"field": "offset"})
        fm = [f for x in (formats or []) for f in x.split(",") if f] or None
        app_.get_project(pid)
        page = jobs.preflight_issues(pid, level=level, offset=offset, limit=lim, formats=fm, naming=naming)
        if page.get("next_offset") is not None:
            response.headers["Link"] = f'<{_next_url(request, limit=lim, offset=page["next_offset"])}>; rel="next"'
        return page

    # ------------------------------------------------------------ files
    @r.post("/projects/{pid}/preview", operation_id="render_project_preview",
            summary="生成并下载一个样本的真实原生格式预览",
            description="与界面预览共用模板版本、样本计划及渲染/读回校验。需要 write 与导出权限；"
                        "不创建批次任务或正式产物。一次仅渲染一个样本，受模拟生成并发限制。"
                        "响应是二进制附件；signature 可用预检返回值固定输入版本。",
            openapi_extra=_ox("write", binary=("application/octet-stream",), **{"x-requires-export": True},
                requestBody={"content": {"application/json": {"schema": {
                    "type": "object", "additionalProperties": False, "properties": {
                        "index": {"type": "integer", "minimum": 0, "default": 0},
                        "key": {"type": "string", "minLength": 1, "maxLength": 512},
                        "signature": {"type": "string", "minLength": 1, "maxLength": 64}}}}}}))
    def render_project_preview(request: Request, pid: str, body: dict = Body(default={})):
        scope(request, "write")
        scope(request, "export")
        unknown = set(body) - {"index", "key", "signature"}
        if unknown:
            raise ServiceError(400, "invalid", "未知预览参数", {"field": sorted(unknown)[0]})
        if "index" in body and (type(body["index"]) is not int or body["index"] < 0):
            raise ServiceError(400, "invalid", "index 必须是从 0 开始的整数", {"field": "index"})
        for name, maximum in (("key", 512), ("signature", 64)):
            if name in body and (not isinstance(body[name], str) or not 1 <= len(body[name]) <= maximum):
                raise ServiceError(400, "invalid", f"{name} 必须是非空文本（最多 {maximum} 字符）", {"field": name})
        if "index" in body and "key" in body:
            raise ServiceError(400, "invalid", "index 与 key 只能指定一个", {"field": "key"})
        # Expensive one-sample renders share the service's existing bounded render slots.
        with jobs._sim_flight(("preview", current_tenant(), pid)):
            result = views.preview(pid, body.get("index"), body.get("key"), strict=True,
                                   expected_signature=body.get("signature"))
            # Rendering can outlive a grant. Revalidate the actual incoming credential before
            # returning bytes (including cached previews); its request-start principal is stale.
            live = auth.verify(request.headers.get("authorization"))
            auth.require(live, "write")
            auth.require(live, "export")
            if ProjectContext(app_, pid).signature() != result["signature"]:
                raise ServiceError(409, "stale", "生成期间项目输入或模板已改变，请重新预检", {"field": "signature"})
            if result.get("blocked"):
                raise ServiceError(409, "preview_blocked", "该样本未通过生成校验", {
                    "issues": result.get("issues", []), "record": result["record"], "signature": result["signature"]})
            output = result["output"]
            data = views.preview_file(output["blob"])
        response = attachment(data, f"preview-{result['record']['index'] + 1}.{output['format']}", output["format"])
        response.headers.update({"X-Content-SHA256": output["blob"], "X-Project-Signature": result["signature"],
                                 "X-Template-Version": str(result["template_version"]),
                                 "X-Record-Key": urllib.parse.quote(result["record"]["key"], safe="")})
        return response

    @r.post("/projects/{pid}/files", status_code=201,
            summary="上传来源文件（multipart、已完成的可续传上传 upload_id，或授权目录下的 path）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {
                    **upload_body_schema()["properties"],
                    "path": {"type": "string", "description": "服务器上的绝对路径，须在 SYNWEVO_DOCS_IMPORT_ROOTS 授权目录内"}}}}}},
                parameters=[{"name": "X-Content-SHA256", "in": "header", "required": False,
                             "description": "multipart 文件的 SHA-256（十六进制）；与收到的内容不一致时拒绝",
                             "schema": {"type": "string"}}]))
    async def upload_file(request: Request, pid: str, file: UploadFile | None = File(default=None),
                          sha256: str | None = Form(default=None)):
        from starlette.concurrency import run_in_threadpool     # storing and reading files is not event-loop work
        scope(request, "write")
        app_.get_project(pid)
        route = "/api/v1/projects/{pid}/files"
        if file is not None:
            data, name, digest = await _read_checked(file, sha256, request)
            return await run_in_threadpool(idem.run, request, route, {"pid": pid, "file": digest, "filename": name},
                                           lambda: app_.add_source(pid, name, data),
                                           replay_check=lambda out: app_.get_source(out["id"]), status=201)
        body = await _json_body(request, ("path", "upload_id"),
                                "请以 multipart 上传文件，或提供 upload_id / path（path 需配置 SYNWEVO_DOCS_IMPORT_ROOTS）")
        if body.get("upload_id") is not None:
            return await run_in_threadpool(idem.run, request, route, {"pid": pid, "body": body},
                                           lambda: use_upload(store, body["upload_id"], lambda n, d: app_.add_source(pid, n, d), "source"),
                                           replay_check=lambda out: app_.get_source(out["id"]), status=201)
        tenant = current_tenant()

        def from_path():
            data, name = _read_from_roots(body["path"], tenant)
            return app_.add_source(pid, name, data)

        return await run_in_threadpool(idem.run, request, route, {"pid": pid, "body": body}, from_path,
                                       replay_check=lambda out: app_.get_source(out["id"]), status=201)

    @r.get("/projects/{pid}/files", summary="项目的来源文件（按上传顺序；offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def list_files(request: Request, response: Response, pid: str, offset: int = _offset_q(),
                   limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        scope(request, "read")
        app_.get_project(pid)
        page = _paged(store, request, response, name="files", select="SELECT *",
                      frm="FROM source_files WHERE project_id=? AND removed=0", args=(pid,),
                      keys=("COALESCE(ord, 0)", "id"), desc=False, offset=offset, limit=limit, cursor=cursor,
                      filters={"pid": pid})
        page["items"] = [app_._source_out(x) for x in page["items"]]
        return page

    @r.get("/files/{fid}", summary="来源文件状态与结构", openapi_extra=_ox("read"))
    def get_file(request: Request, fid: str):
        scope(request, "read")
        r_ = app_.get_source(fid, raw=True)
        app_.get_project(r_["project_id"])                      # tenant check
        return app_._source_out(r_)

    @r.delete("/files/{fid}", summary="从项目移除来源文件", openapi_extra=_ox("write"))
    def delete_file(request: Request, fid: str):
        scope(request, "write")
        r_ = app_.get_source(fid, raw=True)
        app_.get_project(r_["project_id"])
        app_.remove_source(fid)
        return {"deleted": fid}

    # ------------------------------------------------------------ templates
    @r.post("/templates", status_code=201, summary="上传模板（multipart，或已完成的可续传上传 upload_id）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {**upload_body_schema()["properties"], "name": {"type": "string"}}}}}},
                parameters=[{"name": "X-Content-SHA256", "in": "header", "required": False,
                             "description": "multipart 文件的 SHA-256（十六进制）；与收到的内容不一致时拒绝",
                             "schema": {"type": "string"}}]))
    async def upload_template(request: Request, file: UploadFile | None = File(default=None), name: str | None = None,
                              sha256: str | None = Form(default=None)):
        from starlette.concurrency import run_in_threadpool     # the security check and template analysis
        scope(request, "write")
        route = "/api/v1/templates"
        if file is not None:
            data, fname, digest = await _read_checked(file, sha256, request)
            return await run_in_threadpool(idem.run, request, route, {"file": digest, "filename": fname, "name": name},
                                           lambda: app_.upload_template(fname, data, name),
                                           replay_check=lambda out: app_.get_template(out["id"]), status=201)
        body = await _json_body(request, ("upload_id", "name"), "请以 multipart 上传模板，或提供 upload_id")
        tname = body.get("name", name)
        if tname is not None and not isinstance(tname, str):
            raise ServiceError(400, "invalid", "name 必须是字符串", {"field": "name"})
        return await run_in_threadpool(idem.run, request, route, {"body": body, "name": name},
                                       lambda: use_upload(store, body["upload_id"], lambda n, d: app_.upload_template(n, d, tname), "template"),
                                       replay_check=lambda out: app_.get_template(out["id"]), status=201)

    @r.get("/templates", summary="模板列表（按创建时间倒序；offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def list_templates(request: Request, response: Response, offset: int = _offset_q(),
                       limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        scope(request, "read")
        page = _paged(store, request, response, name="templates", select="SELECT *",
                      frm="FROM templates WHERE deleted=0 AND tenant=?", args=(current_tenant(),),
                      keys=("COALESCE(created_at, 0)", "id"), desc=True, offset=offset, limit=limit, cursor=cursor)
        rows = page["items"]
        heads = {}
        if rows:
            marks = ",".join("?" * len(rows))
            for v in store.all(f"SELECT v.template_id, v.slots FROM template_versions v JOIN templates t "
                               f"ON t.id=v.template_id AND t.head=v.version_no WHERE t.id IN ({marks})",
                               tuple(x["id"] for x in rows)):
                heads[v["template_id"]] = len(v.get("slots") or [])
        page["items"] = [{**app_._template_out(x), "slot_count": heads.get(x["id"], 0)} for x in rows]
        return page

    builtin_schema = {"type": "object", "additionalProperties": False,
                      "required": ["kind", "editor"], "properties": {
                          "kind": {"type": "string", "enum": ["builtin_doc", "builtin_sheet"]},
                          "name": {"type": "string", "minLength": 1, "maxLength": 100},
                          "editor": {"$ref": "#/components/schemas/BuiltinEditorModel"}}}
    save_builtin_schema = {"type": "object", "additionalProperties": False,
                           "required": ["editor", "base_version"], "properties": {
                               "name": builtin_schema["properties"]["name"],
                               "editor": builtin_schema["properties"]["editor"],
                               "base_version": {"type": "integer", "minimum": 1},
                               "note": {"type": "string", "maxLength": 200, "description": "这个版本的说明（命名检查点）；留空时为「在编辑器中保存」"},
                               "discard_live": {"type": "boolean", "default": False,
                                                "description": "编辑器里有尚未保存为版本的实时修改时，保存会替换并丢弃它们：未带 true 即 409 live_edits"}}}

    async def builtin_body(request, *, saving=False):
        from .app import _editor_body
        body = await _editor_body(request)  # bounded streaming JSON, including chunked bodies
        # discard_live is in the schema, the SDK sends it and api.md documents it (CI-113); it was
        # missing here, so a confirmed discard was refused as an unknown field
        allowed = {"editor", "name", "base_version", "note", "discard_live"} if saving else {"editor", "name", "kind"}
        unknown = set(body) - allowed
        if unknown:
            raise ServiceError(400, "invalid", "不认识的字段", {"field": sorted(unknown)[0]})
        if not isinstance(body.get("editor"), dict):
            raise ServiceError(400, "invalid", "editor 必须是对象", {"field": "editor"})
        if "name" in body and (not isinstance(body["name"], str) or not body["name"].strip() or len(body["name"]) > 100):
            raise ServiceError(400, "invalid", "name 必须是 1–100 个字符", {"field": "name"})
        if saving and (type(body.get("base_version")) is not int or body["base_version"] < 1):
            raise ServiceError(400, "invalid", "必须提供正整数 base_version", {"field": "base_version"})
        return body

    @r.post("/templates/builtin", status_code=201, operation_id="create_builtin_template",
            summary="用结构化编辑器模型创建 DOCX/XLSX 模板及字段",
            openapi_extra=_ox("write", idempotent=True, requestBody={"required": True,
                "content": {"application/json": {"schema": builtin_schema}}}))
    async def create_builtin_template(request: Request):
        from starlette.concurrency import run_in_threadpool
        from ..builtin_service import create_builtin
        scope(request, "write")
        body = await builtin_body(request)
        return await run_in_threadpool(idem.run, request, "/api/v1/templates/builtin", {"body": body},
                                      lambda: create_builtin(app_, body),
                                      replay_check=lambda out: app_.get_template(out["id"]), status=201)

    @r.get("/templates/{tid}/builtin", operation_id="get_builtin_template",
           summary="读取指定版本的可移植编辑器模型及字段", openapi_extra=_ox("read"))
    def get_builtin_template(request: Request, tid: str, version: int | None = Query(None, ge=1)):
        scope(request, "read")
        t = app_.get_template(tid)
        if t["kind"] not in ("builtin_doc", "builtin_sheet"):
            raise ServiceError(400, "not_builtin", "该模板没有内置编辑器模型")
        v = app_.get_version(tid, version or t["head"])
        return {"id": tid, "name": t["name"], "kind": t["kind"], "head": t["head"],
                "version": v["version_no"], "editor": app_.editor_of(tid, v, t["kind"]), "fields": v["slots"]}

    @r.put("/templates/{tid}/builtin", operation_id="save_builtin_template",
           summary="保存编辑器模型为新版本；base_version 防止覆盖并发修改",
           openapi_extra=_ox("write", idempotent=True, requestBody={"required": True,
               "content": {"application/json": {"schema": save_builtin_schema}}}))
    async def save_builtin_template(request: Request, tid: str):
        from starlette.concurrency import run_in_threadpool
        from ..builtin_service import save_builtin
        scope(request, "write")
        app_.get_template(tid)
        body = await builtin_body(request, saving=True)

        def run_save():
            with request.app.state.collab.acting(tid, (_principal(request) or {}).get("user_id")):
                return save_builtin(app_, tid, body)
        return await run_in_threadpool(idem.run, request, "/api/v1/templates/{tid}/builtin", {"tid": tid, "body": body},
                                      run_save, replay_check=lambda out: app_.get_template(out["id"]), status=200)

    # ------------------------------------------------------------ organization-shared templates
    # Membership in an organization belongs to a person, so these operations need a delegated
    # token (someone acting through a client); a service account or an API key has no person
    # behind it and is refused with a code that says so rather than a bare 403.
    from ..org_templates import OrgTemplates as _OrgTemplates
    shared = _OrgTemplates(app_, auth.ident)          # the machine verifier already holds the identity service

    def person(request: Request, needed: str) -> tuple[dict, str]:
        p = scope(request, needed)
        if not p.get("user_id"):
            raise ServiceError(403, "delegated_required", "组织共享模板只对代表某个成员的委托令牌开放；服务账户与 API 密钥没有组织成员身份")
        return p, p["user_id"]

    def shared_out(x: dict) -> dict:
        keep = ("id", "organization_id", "name", "description", "format", "kind", "slot_count", "release_no", "version_no", "source_workspace",
                "published_by", "published_at", "updated_by", "updated_at", "deprecated", "deprecated_at", "deprecation_note", "use_count",
                "can_manage", "source_ahead", "source_gone")
        return {k: x.get(k) for k in keep}

    @r.get("/organization-templates", summary="组织共享模板列表（当前空间所属组织；offset 或 cursor 分页；需委托令牌）", openapi_extra=_ox("read"))
    def list_shared_templates(request: Request, response: Response, include_deprecated: bool = False, offset: int = _offset_q(),
                              limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        _, uid = person(request, "read")
        org = shared.organization_of(current_tenant())
        if not org:
            page = {"items": [], "total": 0, "next_offset": None, "next_cursor": None}
            return {**page, "organization": None}
        member = shared._org_member(org["id"], uid)
        page = _paged(store, request, response, name="organization-templates", select="SELECT *",
                      frm="FROM organization_templates WHERE organization_id=?" + ("" if include_deprecated else " AND deprecated_at IS NULL"),
                      args=(org["id"],), keys=("COALESCE(updated_at, 0)", "id"), desc=True, offset=offset, limit=limit, cursor=cursor,
                      filters={"include_deprecated": include_deprecated})
        page["items"] = [shared_out(shared._out(x, uid, member)) for x in page["items"]]
        page["organization"] = {"id": org["id"], "name": org["name"]}
        return page

    @r.get("/organization-templates/{pid}", summary="组织共享模板详情与发布历史（需委托令牌）", openapi_extra=_ox("read"))
    def get_shared_template(request: Request, pid: str):
        _, uid = person(request, "read")
        out = shared.get(pid, uid)
        return {**shared_out(out), "releases": out["releases"]}

    @r.post("/templates/{tid}/publish", status_code=201, summary="把模板发布到组织（冻结 head 版本为第 1 次发布；需委托令牌；支持 Idempotency-Key）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"description": {"type": "string", "maxLength": 500}}}}}}))
    def publish_template(request: Request, tid: str, body: dict = Body(default={})):
        _, uid = person(request, "write")
        desc = body.get("description")
        if desc is not None and not isinstance(desc, str):
            raise ServiceError(400, "invalid", "description 必须是字符串", {"field": "description"})
        return idem.run(request, "/api/v1/templates/{tid}/publish", {"tid": tid, "body": body},
                        lambda: shared_out(shared.publish(tid, uid, desc)),
                        replay_check=lambda out: shared.get(out["id"], uid), status=201)

    @r.post("/organization-templates/{pid}/releases", status_code=201, summary="更新发布：源模板 head 有新版本时产生新一次发布，或只改说明（需委托令牌）",
            openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"description": {"type": "string", "maxLength": 500}}}}}}))
    def release_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        _, uid = person(request, "write")
        desc = body.get("description")
        if desc is not None and not isinstance(desc, str):
            raise ServiceError(400, "invalid", "description 必须是字符串", {"field": "description"})
        out = shared.update(pid, uid, desc, set_description="description" in body)
        return {**shared_out(out), "released": out["released"]}

    @r.post("/organization-templates/{pid}/deprecate", summary="弃用或恢复组织共享模板（需委托令牌）",
            openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"deprecated": {"type": "boolean", "default": True}, "note": {"type": "string", "maxLength": 500}}}}}}))
    def deprecate_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        _, uid = person(request, "write")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise ServiceError(400, "invalid", "note 必须是字符串", {"field": "note"})
        return shared_out(shared.set_deprecated(pid, uid, body.get("deprecated", True) is not False, note))

    @r.post("/organization-templates/{pid}/use", status_code=201, summary="把当前发布复制成本空间的模板，可同时绑定项目（需委托令牌；支持 Idempotency-Key）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"name": {"type": "string", "maxLength": 100}, "project_id": {"type": "string"}}}}}}))
    def use_shared_template(request: Request, pid: str, body: dict = Body(default={})):
        _, uid = person(request, "write")
        name, project_id = body.get("name"), body.get("project_id")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ServiceError(400, "invalid", "name 必须是非空字符串", {"field": "name"})
        if project_id is not None and not isinstance(project_id, str):
            raise ServiceError(400, "invalid", "project_id 必须是字符串", {"field": "project_id"})

        def work():
            t = shared.use(pid, uid, name)
            if project_id:
                app_.update_project(project_id, {"template_id": t["id"]})
            return {"id": t["id"], "name": t["name"], "format": t["format"], "kind": t["kind"], "head": t["head"], "version": t["version"]["no"],
                    "fields": t["version"]["slots"], "origin": (t.get("info") or {}).get("origin"), "project_id": project_id}
        return idem.run(request, "/api/v1/organization-templates/{pid}/use", {"pid": pid, "body": body}, work,
                        replay_check=lambda out: app_.template_detail(out["id"]), status=201)

    @r.get("/templates/{tid}", summary="模板详情与版本", openapi_extra=_ox("read"))
    def get_template(request: Request, tid: str):
        scope(request, "read")
        d = app_.template_detail(tid)
        return {"id": d["id"], "name": d["name"], "format": d["format"], "kind": d["kind"], "head": d["head"],
                "version": d["version"]["no"], "fields": d["version"]["slots"], "versions": d["versions"],
                "exports": d["exports"], "info": d.get("info")}

    @r.get("/templates/{tid}/usage", summary="使用此模板的项目：跟随当前版本的与固定了版本的", openapi_extra=_ox("read"))
    def template_usage(request: Request, tid: str):
        scope(request, "read")
        return app_.template_usage(tid)

    @r.post("/templates/{tid}/restore", status_code=201, summary="恢复某个版本：以它的内容新建当前版本（历史保留；base_head 不符则 409 stale_template；"
                                                                  "编辑器里有未保存为版本的实时修改则 409 live_edits，除非 discard_live=true）",
            openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
                "type": "object", "required": ["version"], "properties": {"version": {"type": "integer", "minimum": 1}, "base_head": {"type": "integer", "minimum": 1},
                                                                          "discard_live": {"type": "boolean", "default": False}}}}}}))
    def restore_template_version(request: Request, tid: str, body: dict = Body(...)):
        scope(request, "write")
        if type(body.get("version")) is not int or body["version"] < 1:
            raise ServiceError(400, "invalid", "version 必须是正整数", {"field": "version"})
        base = body.get("base_head")
        if base is not None and (type(base) is not int or base < 1):
            raise ServiceError(400, "invalid", "base_head 必须是正整数", {"field": "base_head"})
        discard = body.get("discard_live", False)
        if type(discard) is not bool:
            raise ServiceError(400, "invalid", "discard_live 必须是布尔值", {"field": "discard_live"})
        with request.app.state.collab.acting(tid, (_principal(request) or {}).get("user_id")):
            d = app_.restore_version(tid, body["version"], base, discard_live=discard)
        return {"id": d["id"], "name": d["name"], "format": d["format"], "kind": d["kind"], "head": d["head"],
                "version": d["version"]["no"], "fields": d["version"]["slots"], "versions": d["versions"], "exports": d["exports"], "info": d.get("info")}

    @r.get("/templates/{tid}/fields", summary="导出字段定义", openapi_extra=_ox("read"))
    def get_fields(request: Request, tid: str):
        scope(request, "read")
        return D.export_template_fields(app_, tid)

    @r.post("/templates/{tid}/fields", summary=f"按声明式定义创建字段（一次调用产生一个模板版本；全部成功或全部不变；"
                                                f"每次最多 {D.MAX_FIELDS_PER_IMPORT} 个）",
            openapi_extra=_ox("write", idempotent=True))
    def post_fields(request: Request, tid: str, body: dict = Body(...)):
        scope(request, "write")
        return idem.run(request, "/api/v1/templates/{tid}/fields", {"tid": tid, "body": body},
                        lambda: D.import_template_fields(app_, tid, body),
                        replay_check=lambda out: app_.get_template(tid), status=200)

    # Editing and removing one field (API-2). Both name the template head they read: a newer head is
    # 409 stale_template with the current head, so a retry after a lost response cannot change a
    # version twice and two editors cannot overwrite each other.
    FIELD_EDITABLE = {"name", "required", "data_type", "format", "default", "font_size", "min_font_size", "align",
                      "valign", "empty_policy", "placeholder", "repeat_header", "keep_header_when_empty", "binding",
                      "mode", "columns", "overflow", "min_row_scale", "color", "separator", "remove_column", "shrink",
                      "shrink_min"}

    def _base_head(value) -> int:
        if type(value) is not int or value < 1:
            raise ServiceError(400, "invalid", "base_head 必须是读取模板时的 head（正整数）", {"field": "base_head"})
        return value

    def _field_result(tid: str, d: dict) -> dict:
        d = app_.template_detail(tid)
        return {"id": d["id"], "name": d["name"], "format": d["format"], "kind": d["kind"], "head": d["head"],
                "version": d["version"]["no"], "fields": d["version"]["slots"]}

    @r.patch("/templates/{tid}/fields/{field_id}",
             summary="修改一个字段的属性（产生一个新模板版本；base_head 必填，不符则 409 stale_template）",
             openapi_extra=_ox("write", requestBody={"content": {"application/json": {"schema": {
                 "type": "object", "required": ["base_head"], "additionalProperties": True,
                 "properties": {"base_head": {"type": "integer", "minimum": 1},
                                **{k: {} for k in sorted(FIELD_EDITABLE)}}}}}}))
    def patch_field(request: Request, tid: str, field_id: str, body: dict = Body(...)):
        p = scope(request, "write")
        base = _base_head(body.get("base_head"))
        values = {k: v for k, v in body.items() if k != "base_head"}
        unknown = sorted(set(values) - FIELD_EDITABLE)
        if unknown:
            raise ServiceError(400, "invalid", f"不可修改的字段属性：{unknown[0]}", {"field": unknown[0]})
        if not values:
            raise ServiceError(400, "invalid", "没有要修改的属性", {"field": "body"})
        with request.app.state.collab.acting(tid, (p or {}).get("user_id")):
            d = app_.update_slot(tid, field_id, values, expect_head=base)
        return _field_result(tid, d)

    @r.delete("/templates/{tid}/fields/{field_id}",
              summary="删除一个字段（产生一个新模板版本；base_head 必填；可同时移除某项目里绑定它的映射）",
              openapi_extra=_ox("write"))
    def delete_field(request: Request, tid: str, field_id: str,
                     base_head: int = Query(..., ge=1, description="读取模板时的 head；不符则 409 stale_template"),
                     project_id: str | None = Query(None, max_length=128, description="同时移除该项目中只绑定这个字段的映射"),
                     cascade: bool = Query(True, description="project_id 给出时是否移除映射")):
        p = scope(request, "write")
        if project_id:
            app_.get_project(project_id)                     # the project is the caller's, or 404
        with request.app.state.collab.acting(tid, (p or {}).get("user_id")):
            d = app_.remove_slot(tid, field_id, project_id, cascade, expect_head=base_head)
        return {**_field_result(tid, d), "removed_mappings": d.get("removed_mappings", [])}

    @r.get("/templates/{tid}/content", summary="下载模板文件（原始或工作副本）",
           openapi_extra=_ox("export", binary=("application/octet-stream",)))
    def template_content(request: Request, tid: str,
                         which: str = Query("original", description="original（上传的原文件）或 working（含字段的工作副本）")):
        scope(request, "export")
        if which not in TEMPLATE_CONTENT:
            raise ServiceError(400, "invalid", "which 只能是 original 或 working", {"field": "which"})
        t = app_.get_template(tid)
        v = app_.get_version(tid, t["head"])
        blob = t["original_blob"] if which == "original" else v["working_blob"]
        ext = {"builtin_doc": "docx", "builtin_sheet": "xlsx"}.get(t["format"], t["format"])
        img = (t.get("info") or {}).get("image")
        if img:
            # An image template's `original_blob` is the one-page wrapper PDF the editor draws on;
            # the file the caller uploaded is kept separately. Serving the wrapper as `.png`
            # handed clients PDF bytes under an image MIME type.
            if which == "original":
                src = img.get("source_format") or "png"
                blob, ext = img["original_blob"], {"jpeg": "jpg"}.get(src, src)
            else:
                ext = "pdf"
        return attachment(store.get_blob(blob), f"{t['name']}.{ext}", ext)

    @r.delete("/templates/{tid}", summary="删除模板", openapi_extra=_ox("admin"))
    def delete_template(request: Request, tid: str):
        scope(request, "admin")
        with request.app.state.collab.acting(tid, (_principal(request) or {}).get("user_id")):
            app_.delete_template(tid)
        return {"deleted": tid}

    # ------------------------------------------------------------ jobs
    job_body = {"type": "object", "properties": {
        "formats": {"type": "array", "items": {"type": "string"}},
        "naming": {"type": "string"},
        "error_policy": {"type": "string", "enum": ["block", "skip"]},
        "layout": {"type": "string", "enum": ["off", "warn", "fit"]},
        "keep_fields": {"type": "boolean"},
        "idempotency_key": {"type": "string", "deprecated": True, "description": "已弃用：请改用 Idempotency-Key 请求头"},
        "request_id": {"type": "string", "deprecated": True, "description": "已弃用：请改用 Idempotency-Key 请求头"}}}

    @r.post("/projects/{pid}/jobs", status_code=202, summary="提交生成任务（支持 Idempotency-Key）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": job_body}}},
                              **{"x-deprecated-inputs": ["idempotency_key", "request_id"]}))
    def create_job(request: Request, response: Response, pid: str, body: dict = Body(default={})):
        p = scope(request, "write")
        key, legacy = _job_idem_key(request, body)
        job = jobs.create(pid, body, tenant=p["tenant"], idem_key=key, origin=f"api:{p['key_id']}",
                          principal=authority.describe_request(request))
        _job_headers(response, job, legacy)
        return job_view(job)

    @r.get("/projects/{pid}/jobs", summary="项目的任务列表（按创建时间倒序；offset 或 cursor 分页）",
           openapi_extra=_ox("read"))
    def project_jobs(request: Request, response: Response, pid: str, offset: int = _offset_q(),
                     limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        scope(request, "read")
        app_.get_project(pid)
        page = _paged(store, request, response, name="jobs",
                      select="SELECT id, status, created_at, started_at, finished_at, counts, options, run_no, "
                             "COALESCE(phase, 'generating') AS phase, halt_reason, principal, "
                             "json_extract(summary, '$.error_code') AS error_code",
                      frm="FROM jobs WHERE project_id=? AND tenant=?", args=(pid, current_tenant()),
                      keys=("COALESCE(created_at, 0)", "id"), desc=True, offset=offset, limit=limit, cursor=cursor,
                      filters={"pid": pid})
        for j in page["items"]:
            desc = authority.loads(j.pop("principal", None))
            j["principal"] = authority.label(desc) if desc else None
        return page

    @r.get("/jobs/{jid}", summary="任务状态与计数", openapi_extra=_ox("read"))
    def get_job(request: Request, jid: str):
        scope(request, "read")
        return job_view(jobs.get(jid))

    @r.get("/jobs/{jid}/items", summary="逐样本结果（可按状态过滤；offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def job_items(request: Request, response: Response, jid: str,
                  status: str | None = Query(None, description="、".join(ITEM_STATUSES)),
                  offset: int = _offset_q(), limit: int | None = _limit_q(ITEM_LIMIT, ITEM_MAX),
                  cursor: str | None = _cursor_q()):
        scope(request, "read")
        jobs.get(jid)
        if status is not None and status not in ITEM_STATUSES:
            raise ServiceError(400, "invalid", f"status 只能是 {' / '.join(ITEM_STATUSES)}", {"field": "status"})
        frm = "FROM job_items WHERE job_id=?" + (" AND status=?" if status else "")
        page = _paged(store, request, response, name="items",
                      select="SELECT id, seq, record_key, label, status, issues, attempts, file_base, started_at, "
                             "finished_at, run_no",
                      frm=frm, args=(jid, status) if status else (jid,), keys=("COALESCE(seq, 0)", "id"), desc=False,
                      offset=offset, limit=limit, cursor=cursor, default=ITEM_LIMIT, maximum=ITEM_MAX,
                      filters={"jid": jid, "status": status})
        _attach_artifacts(store, jid, page["items"])
        for it in page["items"]:
            # The service's rule for what a reader of results may see (tracebacks stay in the
            # server log); this page is read with its own query, so it applies it explicitly.
            it["issues"] = jobs._public_issues(it["issues"])
        return page

    @r.get("/jobs/{jid}/runs", summary="任务的运行记录（初次与重试；offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def job_runs(request: Request, response: Response, jid: str, offset: int = _offset_q(),
                 limit: int | None = _limit_q(DEFAULT_LIMIT, MAX_LIMIT), cursor: str | None = _cursor_q()):
        scope(request, "read")
        jobs.get(jid)
        page = _paged(store, request, response, name="runs",
                      select="SELECT run_no, trigger, requested_by, items, created_at, started_at, finished_at, status, "
                             "principal",
                      frm="FROM job_runs WHERE job_id=?", args=(jid,), keys=("run_no",), desc=False,
                      offset=offset, limit=limit, cursor=cursor, filters={"jid": jid})
        for run in page["items"]:
            # Same field JobService.runs adds: who asked for this run, as a label, never the descriptor.
            desc = authority.loads(run.pop("principal", None))
            run["requested_by_principal"] = authority.label(desc) if desc else None
        return page

    @r.get("/jobs/{jid}/events", summary="任务事件（轮询用，seq 递增）", openapi_extra=_ox("read"))
    def job_events(request: Request, jid: str, after: int = 0, limit: int | None = _limit_q(200, 1000)):
        scope(request, "read")
        jobs.get(jid)
        evs = jobs.events(jid, after, _check_limit(limit, 200, 1000))
        return {"items": evs, "next_after": evs[-1]["seq"] if evs else after}

    @r.post("/jobs/{jid}/cancel", summary="取消任务", openapi_extra=_ox("write"))
    def cancel_job(request: Request, jid: str):
        scope(request, "write")
        return job_view(jobs.cancel(jid))

    @r.post("/jobs/{jid}/retry", summary="重试失败项（沿用原快照，可带 Idempotency-Key）",
            openapi_extra=_ox("write", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {
                    "include_skipped": {"type": "boolean"},
                    "failed_only": {"type": "boolean", "default": False,
                                    "description": "只重试 failed；不重新执行 cancelled/skipped/未规划样本，不能同时 include_skipped。省略保持旧行为（failed 和 cancelled）"},
                    "idempotency_key": job_body["properties"]["idempotency_key"],
                    "request_id": job_body["properties"]["request_id"]}}}}},
                **{"x-deprecated-inputs": ["idempotency_key", "request_id"]}))
    def retry_job(request: Request, response: Response, jid: str, body: dict = Body(default={})):
        p = scope(request, "write")
        key, legacy = _job_idem_key(request, body)
        job = jobs.retry(jid, body.get("include_skipped", False), idem_key=key, requested_by=f"api:{p['key_id']}",
                         principal=authority.describe_request(request), failed_only=body.get("failed_only", False))
        _job_headers(response, job, legacy)
        return job_view(job)

    @r.get("/jobs/{jid}/artifacts", summary="产物清单（offset 或 cursor 分页）", openapi_extra=_ox("read"))
    def job_artifacts(request: Request, response: Response, jid: str, offset: int = _offset_q(),
                      limit: int | None = _limit_q(ARTIFACT_LIMIT, ARTIFACT_MAX), cursor: str | None = _cursor_q()):
        scope(request, "read")
        jobs.get(jid)
        page = _paged(store, request, response, name="artifacts",
                      select="SELECT a.id, a.item_id, a.format, a.filename, a.size, a.blob, a.created_at, a.run_no, "
                             "i.seq, i.label",
                      frm="FROM artifacts a JOIN job_items i ON i.id=a.item_id WHERE a.job_id=? AND a.deleted_at IS NULL",
                      args=(jid,), keys=("COALESCE(i.seq, 0)", "a.format", "a.id"), desc=False,
                      offset=offset, limit=limit, cursor=cursor, default=ARTIFACT_LIMIT, maximum=ARTIFACT_MAX,
                      filters={"jid": jid})
        for a in page["items"]:
            a["sha256"] = a.pop("blob")
        return page

    @r.get("/artifacts/{aid}/content", summary="下载一个产物", openapi_extra=_ox("export", binary=("application/octet-stream",)))
    def artifact_content(request: Request, aid: str):
        scope(request, "export")
        a, data = jobs.artifact(aid)
        resp = attachment(data, a["filename"], a["format"])
        resp.headers["X-Content-SHA256"] = a["blob"]
        return resp

    @r.post("/artifacts/{aid}/link", summary="生成有时效的下载链接（无需密钥即可下载；签发者失去权限后链接随之失效）",
            openapi_extra=_ox("export", idempotent=True))
    def artifact_link(request: Request, aid: str):
        p = scope(request, "export")

        def make():
            a = _artifact_meta(store, aid)
            token, exp = auth.sign_link("artifact", aid, p["tenant"], LINK_TTL_S,
                                        principal=authority.describe_request(request))
            return {"url": public_base(request) + f"/api/v1/download/{token}", "expires_at": exp,
                    "filename": a["filename"], "sha256": a["blob"], "size": a["size"]}

        return idem.run(request, "/api/v1/artifacts/{aid}/link", {"aid": aid}, make, replay_check=lambda out: _artifact_meta(store, aid),
                        status=200, ttl_s=LINK_TTL_S)

    @r.get("/jobs/{jid}/archive", summary="下载全部产物（ZIP，含生成清单）", openapi_extra=_ox("export", binary=("application/zip",)))
    def job_archive(request: Request, jid: str, format: str | None = None):
        scope(request, "export")
        j = jobs.get(jid)
        return _zip_response(jobs, jid, _fmt_or_none(format), j)

    @r.post("/jobs/{jid}/archive-link", summary="生成 ZIP 的有时效下载链接",
            openapi_extra=_ox("export", idempotent=True, requestBody={"content": {"application/json": {"schema": {
                "type": "object", "properties": {"format": {"type": "string"}}}}}}))
    def archive_link(request: Request, jid: str, body: dict = Body(default={})):
        p = scope(request, "export")
        fmt = _fmt_or_none(body.get("format"))

        def make():
            jobs.get(jid)
            ident = jid if not fmt else f"{jid}|{fmt}"
            token, exp = auth.sign_link("archive", ident, p["tenant"], LINK_TTL_S,
                                        principal=authority.describe_request(request))
            return {"url": public_base(request) + f"/api/v1/download/{token}", "expires_at": exp}

        return idem.run(request, "/api/v1/jobs/{jid}/archive-link", {"jid": jid, "format": fmt}, make,
                        replay_check=lambda out: jobs.get(jid), status=200, ttl_s=LINK_TTL_S)

    @r.get("/jobs/{jid}/report.csv", summary="生成清单（CSV）", openapi_extra=_ox("export", binary=("text/csv",)))
    def job_report(request: Request, jid: str):
        scope(request, "export")
        jobs.get(jid)
        return attachment(jobs.report_csv(jid), f"report_{jid}.csv", "csv")

    @r.get("/download/{token}", summary="按签名链接下载（链接本身即凭据）",
           openapi_extra=_ox(binary=("application/octet-stream", "application/zip"), security=[]))
    def signed_download(token: str):
        data = auth.open_link(token)
        # A link outlives the request that minted it. Whoever signed it must still be allowed
        # to export from this workspace *now*: a revoked key, lost export permission or a frozen workspace
        # ends every link they handed out, not 15 minutes later. An unbound legacy link cannot
        # prove current authority; require an authorised caller to mint a bound replacement.
        issuer = data.get("p")
        ok, _why = authority.check(store, issuer, need="export", tenant=data.get("t")) if issuer else (False, "missing_issuer")
        if not ok:
            raise ServiceError(403, "link_revoked", "签发该链接的凭据或账户已不再有权访问这些文件；请重新获取链接")
        with as_tenant(data["t"]):
            if data["k"] == "artifact":
                a, blob = jobs.artifact(data["i"])
                resp = attachment(blob, a["filename"], a["format"])
                resp.headers["X-Content-SHA256"] = a["blob"]
                return resp
            jid, _, fmt = data["i"].partition("|")
            j = jobs.get(jid)
            return _zip_response(jobs, jid, fmt or None, j)

    # Resumable uploads share this router's prefix, authentication and idempotency.
    r.include_router(build_uploads_router(app_, auth, idem, scope, public_base))

    # Governance (credentials, usage) and webhook subscriptions live in their own modules; they
    # share this router's prefix and the same authentication done by the platform gate.
    from . import v1_governance, v1_sources, v1_webhooks
    v1_sources.register(r, app_, views, scope, attachment, auth)
    r.include_router(v1_governance.build_router(app_, views, jobs, auth))
    r.include_router(v1_webhooks.build_router(app_, views, jobs, auth))
    return r


# --------------------------------------------------------------------- helpers
def _job_out(j: dict) -> dict:
    """The /api/v1 view of what `JobService.get` returns (create, cancel and retry return it too)."""
    counts = j.get("counts") or {}
    out = {"id": j["id"], "project_id": j["project_id"], "status": j["status"], "run_no": j.get("run_no", 1),
           "created_at": j["created_at"], "started_at": j.get("started_at"), "finished_at": j.get("finished_at"),
           "template": j.get("template"), "options": j.get("options"), "error": j.get("error"),
           # Machine-readable reason for status=failed (e.g. 'has_errors' for a batch whose
           # planning found blocking errors under error_policy=block).
           "error_code": j.get("error_code"),
           "cancel_requested": bool(j.get("cancel_requested")),
           # 'planning' while the runner builds sample plans for a large batch, then 'generating'.
           "phase": j.get("phase") or "generating",
           # Why a job was stopped by the platform (e.g. 'authorization_revoked:credential_revoked').
           "halt_reason": j.get("halt_reason"),
           # Who asked for it, as a secret-free label; the stored descriptor itself stays internal.
           "principal": j.get("submitted_by"),
           # The X-Request-ID of the request that submitted it (the call log and job.created carry it too).
           "request_id": j.get("request_id"),
           "counts": {"samples": counts.get("total", 0), "documents": counts.get("success", 0), "files": counts.get("files", 0),
                      "failed": counts.get("failed", 0), "skipped": counts.get("skipped", 0), "cancelled": counts.get("cancelled", 0),
                      "pending": counts.get("pending", 0), "running": counts.get("running", 0),
                      # While phase='planning': how many samples have their plan (and a result row) yet.
                      "planned": counts.get("planned", 0), "unplanned": counts.get("unplanned", 0)}}
    for k in ("cancel", "idempotent_replay"):
        if k in j:
            out[k] = j[k]
    return out


def _job_idem_key(request: Request, body: dict) -> tuple[str | None, bool]:
    """(key, legacy body field used). The header wins; body fields still work but are deprecated."""
    legacy = [f for f in DEPRECATIONS["job-body-idempotency-key"]["fields"] if body.get(f) is not None]
    header = request.headers.get(I.HEADER)
    if header is not None:
        return I.validate_key(header), bool(legacy)
    for f in legacy:
        try:
            return I.validate_key(body[f]), True
        except ServiceError as e:
            raise ServiceError(e.status, e.code, e.message.replace(I.HEADER, f), {"field": f}) from e
    return None, False


def _job_headers(response: Response, job: dict, legacy: bool) -> None:
    if job.get("idempotent_replay"):
        response.headers[I.REPLAY_HEADER] = "true"
    if legacy:
        for k, v in deprecation_headers("job-body-idempotency-key").items():
            response.headers[k] = v


def _formats_naming(formats, naming):
    if formats is not None and (not isinstance(formats, list) or not all(isinstance(f, str) for f in formats)):
        raise ServiceError(400, "invalid", "formats 必须是字符串数组", {"field": "formats"})
    if naming is not None and not isinstance(naming, str):
        raise ServiceError(400, "invalid", "naming 必须是字符串", {"field": "naming"})
    return formats or None, naming or None


def _simulate_count(v) -> int:
    """0 for no simulation, else how many samples to render (bounded; see SIMULATE_MAX)."""
    if v is False or v is None:
        return 0
    if v is True:
        return SIMULATE_DEFAULT
    if not isinstance(v, int) or v < 1:
        raise ServiceError(400, "invalid", "simulate 只能是 true / false，或正整数（只模拟前 N 个样本）", {"field": "simulate"})
    if v > SIMULATE_MAX:
        raise ServiceError(400, "invalid", f"simulate 最多 {SIMULATE_MAX} 个样本；要覆盖全部样本请提交任务",
                           {"field": "simulate", "maximum": SIMULATE_MAX})
    return v


def _fmt_or_none(fmt):
    if fmt is None or fmt == "":
        return None
    if not isinstance(fmt, str) or not fmt.isascii() or not fmt.isalnum() or len(fmt) > 10:
        raise ServiceError(400, "invalid", "format 必须是输出格式名（如 docx、pdf）", {"field": "format"})
    return fmt.lower()


def _attach_artifacts(store, jid: str, rows: list[dict]) -> None:
    ids = [x["id"] for x in rows]
    by_item: dict[str, list] = {}
    if ids:
        arts = store.all(f"SELECT id, item_id, format, filename, blob, size, verification, run_no FROM artifacts "
                         f"WHERE job_id=? AND deleted_at IS NULL AND item_id IN ({','.join('?' * len(ids))})", (jid, *ids))
        for a in arts:
            a["sha256"] = a.pop("blob")
            by_item.setdefault(a["item_id"], []).append(a)
    for x in rows:
        x["artifacts"] = by_item.get(x["id"], [])


def _artifact_meta(store, aid: str) -> dict:
    """Tenant-checked artifact row without reading its bytes (minting a link needs no content)."""
    a = store.one("SELECT a.id, a.job_id, a.filename, a.blob, a.size, a.deleted_at, j.tenant FROM artifacts a "
                  "JOIN jobs j ON j.id=a.job_id WHERE a.id=?", (aid,))
    if not a or (a["tenant"] or "default") != current_tenant():
        not_found("输出文件")
    if a.get("deleted_at"):
        raise ServiceError(410, "artifact_deleted", "该输出文件已被删除，无法下载；可对原任务重新生成")
    from ..jobs import snapshot_inputs, withdrawn_input
    withdrawn = withdrawn_input(store, snapshot_inputs(store, a.pop("job_id")))
    if withdrawn is not None:
        raise withdrawn                 # no link to an output of a template or source a rescan rejected (CI-90)
    if not store.has_blob(a["blob"]):
        raise ServiceError(410, "artifact_missing", "该输出文件的内容已不在存储中（可能已按保留期清理）")
    return a


async def _read_checked(file: UploadFile, form_sha: str | None, request: Request) -> tuple[bytes, str, str]:
    """Read a multipart file within the size limit and check any SHA-256 the caller declared."""
    from .app import _read_limited
    data = await _read_limited(file)
    digest = hashlib.sha256(data).hexdigest()
    declared = [s.strip().lower() for s in (form_sha, request.headers.get("X-Content-SHA256")) if s and s.strip()]
    if any(not re.fullmatch(r"[0-9a-f]{64}", s) for s in declared):
        raise ServiceError(400, "invalid", "sha256 必须是 64 位十六进制字符串", {"field": "sha256"})
    if len(set(declared)) > 1:
        raise ServiceError(400, "checksum_conflict", "sha256 表单字段与 X-Content-SHA256 请求头不一致", {"field": "sha256"})
    if declared and declared[0] != digest:
        raise ServiceError(400, "checksum_mismatch", "收到的文件内容与声明的 SHA-256 不一致（传输中损坏或选错了文件）",
                           {"field": "sha256", "expected": declared[0], "actual": digest})
    return data, file.filename or "upload.bin", digest


async def _json_body(request: Request, allowed: tuple[str, ...], hint: str) -> dict:
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise ServiceError(400, "invalid", hint, {"field": "file"})
    try:
        body = await request.json()
    except ValueError as e:
        raise ServiceError(400, "invalid", "请求体不是合法的 JSON", {"field": "body"}) from e
    if not isinstance(body, dict):
        raise ServiceError(400, "invalid", "请求体必须是 JSON 对象", {"field": "body"})
    unknown = set(body) - set(allowed)
    if unknown:
        raise ServiceError(400, "invalid", f"不认识的字段：{', '.join(sorted(unknown))}", {"field": sorted(unknown)[0]})
    given = [k for k in ("path", "upload_id") if body.get(k) is not None]
    if len(given) > 1:
        raise ServiceError(400, "invalid", "path 与 upload_id 只能二选一", {"field": given[1]})
    if not given:
        raise ServiceError(400, "invalid", hint, {"field": "file"})
    return body


def _zip_response(jobs, jid: str, fmt: str | None, j: dict) -> Response:
    from starlette.background import BackgroundTask
    path = jobs.zip_file(jid, fmt)
    name = f"{j['template']['name']}_{jid}.zip"
    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def _roots_for(tenant: str) -> list[Path]:
    """The import roots this workspace may read.

    One process-wide list used to serve every workspace, so any tenant's write key could ingest
    another tenant's drop folder. A root with `{tenant}` is per workspace; a plain root is the
    legacy single-tenant configuration and serves only the legacy workspace.
    """
    out = []
    for raw in IMPORT_ROOTS:
        raw = raw.strip()
        if "{tenant}" in raw:
            if not _SAFE_TENANT.match(tenant or ""):
                continue
            raw = raw.replace("{tenant}", tenant)
        elif tenant != UI_TENANT:
            continue
        out.append(Path(os.path.normpath(os.path.abspath(os.path.expanduser(raw)))))
    return out


def _read_from_roots(path, tenant: str) -> tuple[bytes, str]:
    """Read a file the caller names by path – only inside a root configured for its workspace.

    Containment is decided on the *lexical* path before the filesystem is touched, and every
    path outside gets the same 403 whether it exists or not: resolving first answered 404 for a
    missing outside path and 403 for an existing one, which let any write key probe the server's
    filesystem. After resolving, containment is checked again so a symlink inside a root cannot
    lead out of it.
    """
    if not IMPORT_ROOTS:
        raise ServiceError(400, "path_not_allowed", "本部署未开放按路径读取文件；请用 multipart 上传，或配置 SYNWEVO_DOCS_IMPORT_ROOTS", {"field": "path"})
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        # A NUL byte is not part of any path; the filesystem calls raise ValueError on it (a 500).
        raise ServiceError(400, "invalid", "path 必须是服务器上的绝对路径", {"field": "path"})
    raw = os.path.expanduser(path)
    if not os.path.isabs(raw):
        raise ServiceError(400, "invalid", "path 必须是服务器上的绝对路径", {"field": "path"})
    denied = ServiceError(403, "path_not_allowed", "路径不在本工作空间的授权目录内（SYNWEVO_DOCS_IMPORT_ROOTS）", {"field": "path"})
    lexical = Path(os.path.normpath(raw))
    root = next((rt for rt in _roots_for(tenant) if lexical == rt or rt in lexical.parents), None)
    if root is None:
        raise denied
    try:
        real = lexical.resolve(strict=True)
    except (OSError, ValueError) as e:
        raise ServiceError(404, "file_not_found", "授权目录内没有这个文件", {"field": "path"}) from e
    real_root = root.resolve()
    if not (real == real_root or real_root in real.parents):
        raise denied
    if not real.is_file():
        raise ServiceError(400, "invalid", "path 指向的不是文件", {"field": "path"})
    if real.stat().st_size > LIMITS.max_file_bytes:
        raise ServiceError(413, "too_large", "文件超过大小上限")
    return real.read_bytes(), real.name


# --------------------------------------------------------------------- OpenAPI
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


HTTP_METHODS = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
_BINARY = {"type": "string", "format": "binary"}


def _hdr(name: str) -> dict:
    return {"$ref": f"#/components/headers/{name}"}


def _components() -> dict:
    """Shared pieces of the contract that FastAPI cannot derive from the handlers."""
    from ..editor_contract import editor_schemas
    err = {"type": "object", "required": ["error"], "properties": {"error": {
        "type": "object", "required": ["code", "message", "request_id"], "properties": {
            "code": {"type": "string", "description": "机器可读的错误码，如 invalid、not_found、idempotency_conflict"},
            "message": {"type": "string", "description": "给人看的说明（中文）"},
            "field": {"type": "string", "description": "出错的请求字段或请求头（如 limit、Idempotency-Key）"},
            "request_id": {"type": "string", "description": "与响应头 X-Request-ID 相同"},
            "details": {"type": "object", "additionalProperties": True}}}}}
    problem = {"type": "object", "description": "RFC 9457 Problem Details（application/problem+json）", "properties": {
        "type": {"type": "string", "format": "uri-reference", "example": "urn:synwevo:problem:bad_error_policy"},
        "title": {"type": "string"}, "status": {"type": "integer"}, "detail": {"type": "string"},
        "instance": {"type": "string", "format": "uri-reference"}, "code": {"type": "string"},
        "request_id": {"type": "string"},
        "errors": {"type": "array", "items": {"type": "object", "properties": {
            "detail": {"type": "string"}, "pointer": {"type": "string", "example": "#/error_policy"}}}},
        "details": {"type": "object", "additionalProperties": True}}}
    job_ref = {"type": "object", "properties": {k: {} for k in (
        "id", "project_id", "status", "counts", "run_no", "tenant", "created_at", "finished_at")}}
    event = {"type": "object", "required": ["id", "type", "version", "occurred_at"], "properties": {
        "id": {"type": "string", "description": "事件 id，重试与重投时不变；接收端按它去重"},
        "type": {"type": "string", "example": "job.completed"},
        "version": {"type": "integer", "example": 1},
        "occurred_at": {"type": "number", "description": "Unix 秒"},
        "tenant": {"type": "string", "description": "工作空间 id"},
        "job": {**job_ref, "type": ["object", "null"], "description": "任务摘要；测试事件与文件事件为 null"},
        "data": {"type": "object", "additionalProperties": True,
                 "description": "任务事件：status、run_no、counts；被平台停止的任务带 halt_reason。"
                                "文件事件：file_id、project_id、status、reason（安全检查结论）、version"}},
        "additionalProperties": True}
    s = {"type": "string"}
    headers = {
        "X-Request-ID": {"description": "本次请求的追踪 id（每个响应都有；也会出现在错误体的 request_id）", "schema": s},
        "Idempotent-Replayed": {"description": "为 true 表示这是同一 Idempotency-Key 的重放，响应与首次相同",
                                "schema": {"type": "string", "enum": ["true"]}},
        "RateLimit": {"description": "IETF RateLimit 头（草案）：剩余配额 r 与恢复秒数 t", "schema": s},
        "RateLimit-Policy": {"description": "IETF RateLimit-Policy 头（草案）：配额 q 与窗口 w", "schema": s},
        "Retry-After": {"description": "多少秒后可重试", "schema": {"type": "integer"}},
        "Deprecation": {"description": "RFC 9745：请求用到了已弃用的输入（值为 @Unix 秒）", "schema": s},
        "Sunset": {"description": "RFC 8594：已弃用输入停止支持的时间（HTTP 日期）", "schema": s},
        "Link": {"description": "rel=\"next\"（下一页，原样请求即可）或 rel=\"deprecation\"（弃用说明）", "schema": s},
        "X-Content-SHA256": {"description": "下载内容的 SHA-256", "schema": s},
    }
    # The envelope is the default; `Accept: application/problem+json` selects RFC 9457.
    env_json = {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}},
                "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}

    def resp(desc, *extra):
        return {"description": desc, "headers": {h: _hdr(h) for h in ("X-Request-ID", *extra)}, "content": env_json}

    return {
        "schemas": {"Error": err, "Problem": problem, "WebhookEvent": event, **editor_schemas()},
        "headers": headers,
        "parameters": {
            "IdempotencyKey": {"name": "Idempotency-Key", "in": "header", "required": False,
                               "description": "1–255 个可打印 ASCII 字符（建议 UUID）。同一工作空间、同一路由 24 小时内："
                                              "内容相同 → 重放首次的成功响应（带 Idempotent-Replayed: true）；"
                                              "内容不同 → 409 idempotency_conflict；首次仍在处理 → 409 idempotency_in_progress。"
                                              "新请求的事务失败不占用键；旧版本结果不明的记录返回 idempotency_outcome_unknown，禁止自动重做。",
                               "schema": {"type": "string", "maxLength": 255}},
            "XRequestID": {"name": "X-Request-ID", "in": "header", "required": False,
                           "description": "可选：客户端自带的追踪 id（最多 64 个字母数字、- 或 _）", "schema": s},
        },
        "responses": {
            "BadRequest": resp("请求内容不合法（error.field 指出字段）"),
            "Unauthorized": resp("凭据缺失、无效、过期或已撤销"),
            "Forbidden": resp("凭据有效但权限不足"),
            "NotFound": resp("资源不存在或不属于该工作空间"),
            "Conflict": resp("幂等键冲突、版本过期或状态不允许"),
            "RateLimited": resp("超过限流", "Retry-After", "RateLimit", "RateLimit-Policy"),
            "ServerError": resp("未预期错误（带 request_id，服务端日志有同一个 id）"),
        },
    }


def _webhooks() -> dict:
    """Top-level `webhooks`: what a receiver of job events gets (OpenAPI 3.1).

    Generated from the event types `webhooks.py` actually emits. Two kinds of destination exist:
    per-workspace subscriptions (Standard Webhooks headers plus X-Synwevo-*) and deployment
    endpoints (the legacy X-DocWeave-* headers, byte for byte, plus the Standard Webhooks ones).
    """
    from .. import webhooks as W
    sub, legacy = "仅工作空间订阅", "仅部署级端点（SYNWEVO_DOCS_WEBHOOK_URLS）"
    headers = [
        ("webhook-id", True, "事件 id；重试与重新投递时不变，接收端按它去重（Standard Webhooks）"),
        ("webhook-timestamp", True, "签名时间（Unix 秒）；拒绝与当前时间相差超过 5 分钟的请求"),
        ("webhook-signature", True, "空格分隔的一个或多个 v1,<base64(HMAC-SHA256(密钥, \"<id>.<timestamp>.<body>\"))>；"
                                    "轮换密钥期间同时带新旧两个签名，任一验证通过即可。whsec_ 前缀的密钥先去前缀再 base64 解码"),
        ("X-Synwevo-Event", False, f"事件类型（{sub}）"),
        ("X-Synwevo-Delivery", False, f"本次投递 id（{sub}）"),
        ("X-DocWeave-Event", False, f"事件类型（{legacy}）"),
        ("X-DocWeave-Event-Id", False, f"事件 id（{legacy}）"),
        ("X-DocWeave-Delivery", False, f"本次投递 id（{legacy}）"),
        ("X-DocWeave-Timestamp", False, f"签名时间（Unix 秒；{legacy}）"),
        ("X-DocWeave-Signature", False, f"v1=<hex(HMAC-SHA256(密钥, \"<timestamp>.<body>\"))>（{legacy}）"),
    ]
    params = [{"name": n, "in": "header", "required": req, "description": d, "schema": {"type": "string"}}
              for n, req, d in headers]
    out = {}
    for etype in (*W.EVENT_TYPES, W.PING_EVENT):
        out[etype] = {"post": {
            "summary": f"{etype} 事件回调",
            "description": ("测试事件：订阅的 ping 或 CLI 的测试发送，job 为 null。" if etype == W.PING_EVENT else
                            ("默认投递的事件。" if etype in W.DEFAULT_EVENTS else "订阅时在 events 中列出才会投递。")
                            + ("来源文件的接纳结论，与状态变化同一事务写入；job 为 null，data 只有 file_id、project_id、"
                               "status、reason、version，文件名与内容请用凭据另行读取。" if etype.startswith("file.") else ""))
                           + "接收端先校验签名与时间戳，再按 webhook-id 去重；投递可能重复、顺序不保证。",
            "operationId": "webhook_" + etype.replace(".", "_"),
            "parameters": params,
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/WebhookEvent"}}}},
            "responses": {"2XX": {"description": "已接收。非 2xx 按退避重试（410 表示停用该订阅）"}}}}
    return out


def openapi_subset(api, issuer: str | None = None) -> dict:
    """The OpenAPI document of the machine API (v1 routes and the health probes).

    `issuer` is the account service's issuer URL for the token endpoint; the committed file is
    exported with the local development issuer so that it does not depend on who exports it.
    Every /api/v1 operation must declare the scope it needs (`x-scope`, or `security: []` for a
    link-authenticated one); the ones that do not are listed under `x-undeclared-scope` so the
    drift test names them, instead of silently inheriting "any credential".
    """
    spec = json.loads(json.dumps(api.openapi()))
    token_url = (issuer or DEV_ISSUER).rstrip("/") + "/oidc/token"
    spec["paths"] = {p: v for p, v in spec["paths"].items() if p.startswith("/api/v1") or p in ("/healthz", "/readyz")}
    spec["info"] = {"title": "Synwevo Docs 机器接口", "version": API_VERSION,
                    "description": "Synwevo Docs 的自动化接口。/api/v1 接受两种凭据：旧式 API 密钥"
                                   "（Authorization: Bearer dw_<id>_<secret>，权限 read / write / admin）与平台签发的访问令牌"
                                   "（client_credentials，权限 docweave.read / docweave.write / docweave.manage）。"
                                   "错误统一为 {error:{code,message,field,request_id,details}}。详见 docs/integration/api.md。"}
    comps = _components()
    spec["components"] = spec.get("components", {})
    spec["components"]["securitySchemes"] = {
        "apiKey": {"type": "http", "scheme": "bearer", "bearerFormat": "dw_<id>_<secret>",
                   "description": "旧式 API 密钥（CLI 签发，库中只存哈希）。权限：read、write、admin"},
        "platformToken": {"type": "oauth2",
                          "description": "平台签发的访问令牌：服务账户用 client_credentials 向账户服务的 $ISSUER/oidc/token 申请"
                                         "（tokenUrl 是本地开发平台的账户服务；部署中请换成该部署的 $ISSUER，"
                                         "或读 $ISSUER/.well-known/openid-configuration 的 token_endpoint）；"
                                         "用户委托令牌来自授权码流程。每次调用都重新核验账户、成员关系与应用资格",
                          "flows": {"clientCredentials": {"tokenUrl": token_url, "scopes": {
                              "docweave.read": "读取项目、模板、任务与产物（等价旧的 read）",
                              "docweave.write": "创建、修改、提交任务（等价旧的 write）",
                              "docweave.manage": "删除项目与模板、管理 Webhook 订阅与自身凭据（等价旧的 admin）"}}}},
    }
    spec["security"] = [{"apiKey": []}, {"platformToken": []}]
    undeclared = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            if method not in HTTP_METHODS or not isinstance(op, dict):
                continue
            if not _annotate(path, op):
                undeclared.append(f"{method.upper()} {path}")
    if undeclared:
        spec["x-undeclared-scope"] = sorted(undeclared)
    # Drop schemas no kept path can reach. FastAPI generates a component for every route in the
    # app, so without this the machine-API document also carried request bodies belonging to the
    # UI and the OIDC endpoints — routes this file deliberately does not describe.
    reachable = _reachable_schemas(spec)
    spec["components"]["schemas"] = {**reachable, **comps["schemas"]}
    for k in ("headers", "parameters", "responses"):
        spec["components"][k] = comps[k]
    spec["webhooks"] = _webhooks()
    return spec


_EXPORT_NOTE = ("下载文件：API 密钥与服务账户只需 read；用户委托令牌还要求该成员在工作空间有导出权限"
                "（查看者没有导出权限时返回 403 export_not_allowed）。")
_ANY_NOTE = "任何有效凭据都可以调用（不要求特定权限）。"


def _annotate(path: str, op: dict) -> bool:
    """Add the contract pieces to one operation. False when it declares no scope."""
    probe = path in ("/healthz", "/readyz")
    scope = op.pop("x-scope", None)
    if scope is not None and scope not in X_SCOPES:
        raise ValueError(f"{path}: unknown x-scope {scope!r} (expected one of {', '.join(X_SCOPES)})")
    idempotent = op.pop("x-idempotent", False)
    binary = op.pop("x-binary", None)
    deprecated_inputs = op.pop("x-deprecated-inputs", None)
    responses = op.setdefault("responses", {})
    declared = True
    if probe or op.get("security") == []:
        op["security"] = []
    elif scope == "any":
        op["security"] = [{"apiKey": []}, {"platformToken": []}]
        op["description"] = ((op.get("description") or "") + "\n\n" + _ANY_NOTE).strip()
    elif scope:
        need = SCOPE_AS.get(scope, scope)
        op["security"] = [{"apiKey": [need]}, {"platformToken": [SCOPE_NAMES[need]]}]
        if scope == "export":
            op["description"] = ((op.get("description") or "") + "\n\n" + _EXPORT_NOTE).strip()
    else:
        declared = False
    if not probe:
        op["parameters"] = list(op.get("parameters") or []) + [{"$ref": "#/components/parameters/XRequestID"}]
        if idempotent:
            op["parameters"].append({"$ref": "#/components/parameters/IdempotencyKey"})
    if binary:
        ok = responses.setdefault("200", {"description": "Successful Response"})
        ok["content"] = {m: {"schema": _BINARY} for m in binary}
    for code, resp in responses.items():
        if not isinstance(resp, dict) or not str(code).startswith("2"):
            continue
        hs = resp.setdefault("headers", {})
        hs["X-Request-ID"] = _hdr("X-Request-ID")
        if not probe and op.get("security") != []:
            hs["RateLimit"] = _hdr("RateLimit")
            hs["RateLimit-Policy"] = _hdr("RateLimit-Policy")
        if idempotent:
            hs["Idempotent-Replayed"] = _hdr("Idempotent-Replayed")
        if binary:
            hs["X-Content-SHA256"] = _hdr("X-Content-SHA256")
        if deprecated_inputs:
            hs["Deprecation"] = _hdr("Deprecation")
            hs["Sunset"] = _hdr("Sunset")
            hs["Link"] = _hdr("Link")
        elif not binary and any(isinstance(p, dict) and p.get("name") in ("cursor", "offset")
                                for p in op.get("parameters") or []):
            hs["Link"] = _hdr("Link")
    if probe:
        return True
    # The platform gate answers malformed parameters on /api/v1 with the 400 envelope (listing
    # every failing field), never with FastAPI's 422 shape, so the generated 422 is not the contract.
    responses.pop("422", None)
    responses.setdefault("400", {"$ref": "#/components/responses/BadRequest"})
    if op.get("security") != []:
        responses.setdefault("401", {"$ref": "#/components/responses/Unauthorized"})
        responses.setdefault("403", {"$ref": "#/components/responses/Forbidden"})
        responses.setdefault("429", {"$ref": "#/components/responses/RateLimited"})
    responses.setdefault("404", {"$ref": "#/components/responses/NotFound"})
    if idempotent:
        responses.setdefault("409", {"$ref": "#/components/responses/Conflict"})
    responses.setdefault("5XX", {"$ref": "#/components/responses/ServerError"})
    return declared
