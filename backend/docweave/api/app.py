"""HTTP API (FastAPI).  The browser UI talks only to these endpoints; the engine
package has no web dependency so a desktop shell can reuse it later."""
from __future__ import annotations

import os
import secrets
import time
import traceback
import urllib.parse
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..auth import Auth, rate_limiter, ui_auth_mode
from ..context import UI_TENANT, set_tenant
from ..identity import Identity
from ..invites import Invites
from ..mail import Mailer
from ..mfa import Mfa, Tickets
from ..sessions import Sessions
from ..engine.limits import LIMITS
from ..jobs import JobService, naming_tokens
from ..services import App, ServiceError, safe_output_name
from ..store import Store, new_id
from ..views import Views

API_RATE_PER_MIN = int(os.environ.get("DOCWEAVE_API_RATE_PER_MIN", "300"))
PUBLIC_V1 = ("/api/v1/download/",)          # signed links carry their own proof

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


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    data_dir = data_dir or os.environ.get("DOCWEAVE_DATA_DIR") or str(Path(__file__).resolve().parents[3] / "data")
    store = Store(data_dir)
    app_ = App(store)
    views = Views(app_)
    jobs = JobService(app_) if start_jobs else None
    auth = Auth(store)
    identity = Identity(store)
    from ..machine import MachineAuth
    # One resolver for every machine caller: legacy API keys, service accounts and user
    # delegations all come out as the same principal shape, so no call site has to know which
    # kind it got — and none of them can forget to re-check one of the three.
    machine = MachineAuth(store, identity, auth)
    sessions = Sessions(store)
    invites = Invites(store, identity)
    mailer = Mailer(store, start=start_jobs)
    mfa = Mfa(store)
    tickets = Tickets(store)
    from ..webhooks import WebhookDispatcher
    hooks = WebhookDispatcher(store, jobs, start=start_jobs)
    app_.on_event = hooks.notify                      # job state changes wake the dispatcher
    show_docs = (os.environ.get("DOCWEAVE_API_DOCS") or "").strip().lower() in ("1", "true", "on", "yes")
    api = FastAPI(title="DocWeave", version="1.0.0",
                  docs_url="/api/docs" if show_docs else None,
                  redoc_url=None,
                  openapi_url="/api/openapi.json" if show_docs else None)
    api.state.app = app_
    api.state.views = views
    api.state.jobs = jobs
    api.state.auth = auth
    api.state.identity = identity
    api.state.sessions = sessions
    api.state.invites = invites
    api.state.mailer = mailer
    api.state.mfa = mfa
    api.state.webhooks = hooks
    limiter = rate_limiter(API_RATE_PER_MIN)

    def _error_body(request: Request, code: str, message: str, detail: dict | None):
        rid = getattr(request.state, "request_id", "")
        if request.url.path.startswith("/api/v1"):
            body = {"error": {"code": code, "message": message, "request_id": rid}}
            if isinstance(detail, dict) and detail.get("field"):
                body["error"]["field"] = detail["field"]
            if detail:
                body["error"]["details"] = {k: v for k, v in detail.items() if k != "field"} or None
                if not body["error"]["details"]:
                    body["error"].pop("details")
            return body
        return {"error": code, "message": message, "detail": detail, "request_id": rid}

    @api.exception_handler(ServiceError)
    async def _svc_err(request: Request, exc: ServiceError):
        resp = JSONResponse(_error_body(request, exc.code, exc.message, exc.detail), status_code=exc.status)
        if exc.code == "rate_limited" and (exc.detail or {}).get("retry_after"):
            resp.headers["Retry-After"] = str(int((exc.detail or {})["retry_after"]) + 1)
        return resp

    @api.exception_handler(ValueError)
    async def _val_err(request: Request, exc: ValueError):
        return JSONResponse(_error_body(request, getattr(exc, "code", "invalid"), str(exc), None), status_code=400)

    @api.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        rid = getattr(request.state, "request_id", "")
        print(f"[docweave] unhandled error request_id={rid} path={request.url.path}\n{traceback.format_exc()}", flush=True)
        return JSONResponse(_error_body(request, "internal_error", "服务器内部错误；请提供 request_id 以便排查", None), status_code=500)

    # Reachable without a session, because you cannot sign in from behind a sign-in wall. Kept
    # to an exact set rather than a prefix: "/api/auth" as a prefix would also open anything
    # added under it later, and this list is the whole anonymous surface of the account system.
    ANON_AUTH_PATHS = {"/api/auth/state", "/api/auth/login", "/api/auth/bootstrap", "/api/auth/logout",
                       # An invitation is for someone who has no account yet, so reading the
                       # landing page and accepting it cannot require being signed in. Preview
                       # is read-only and accept consumes a token that is itself the authority.
                       "/api/auth/invitations/preview", "/api/auth/invitations/accept",
                       # the second half of a sign-in: the ticket is the authority here
                       "/api/auth/login/mfa",
                       # A passkey sign-in has no password step before it, so it cannot require
                       # a session; the signed assertion over a server-issued challenge is the
                       # authority. Its challenge endpoint is anonymous for the same reason.
                       "/api/auth/login/passkey", "/api/auth/passkeys/authenticate/begin"}
    # The SPA shell and its assets must load so the login screen can render; every /api/ route
    # behind them still requires the session, so no data rides along with the HTML.
    SPA_PREFIXES = ("/assets/",)
    # OIDC protocol surface. Not a product feature and not UI: these are the endpoints a relying
    # party talks to, and each one carries its own authentication.
    PLATFORM_PREFIXES = ("/oidc/", "/.well-known/")
    # The relying-party side. `/auth/start` and `/auth/callback` are mid-redirect-chain and
    # `/auth/backchannel-logout` is authenticated by a signed token, so none of them can be put
    # behind the legacy Basic prompt.
    SSO_PREFIXES = ("/auth/",)
    # One build, three shells. The account service must not serve the document workbench: its
    # API refuses those calls by role, so a shared shell renders a project list that then fills
    # with "this endpoint is not on the account service" — a correct refusal presented as a
    # broken page.
    SHELLS = {"portal": "portal.html", "accounts": "accounts.html"}
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def _session_gate(request: Request, path: str, finish):
        """Resolve the session cookie into an identity, or refuse.

        Returns the tenant-context token on success, or a JSONResponse to return as-is. The
        workspace comes from the session row and is re-checked against live membership on every
        request, so a workspace someone has been removed from stops working immediately rather
        than at the end of their session.
        """
        from ..sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, csrf_ok
        session = sessions.load(request.cookies.get(COOKIE_NAME))
        request.state.session = session

        if session and request.method not in SAFE_METHODS and path.startswith("/api/"):
            # Double-submit CSRF on every state-changing request. Login is exempt because it has
            # no session yet; it is protected by being useless without valid credentials.
            if path not in ANON_AUTH_PATHS and not csrf_ok(request.cookies.get(CSRF_COOKIE),
                                                           request.headers.get(CSRF_HEADER)):
                return finish(JSONResponse(_error_body(request, "csrf_failed",
                                                       "请求校验失败，请刷新页面后重试", None), status_code=403))

        if path in ANON_AUTH_PATHS or any(path.startswith(p) for p in SPA_PREFIXES) or not path.startswith("/api/"):
            # The SPA shell renders for anyone; it fetches nothing until a session exists. The
            # fallback is deliberately an id no row carries rather than UI_TENANT — see below.
            return set_tenant((session or {}).get("workspace_id") or "__anonymous__")

        if not session:
            return finish(JSONResponse(_error_body(request, "unauthorized", "请先登录", None), status_code=401))

        user = identity.get_user(session["user_id"])
        if not user or user["status"] != "active":
            # Suspension and deletion take effect on the next request, not at session expiry.
            sessions.revoke(session["id"], f"user_{user['status'] if user else 'deleted'}")
            request.state.session = None
            return finish(JSONResponse(_error_body(request, "unauthorized", "账户不可用，请重新登录", None), status_code=401))
        request.state.user = user

        wid = session.get("workspace_id")
        if wid:
            # Membership is authoritative every time; the session only remembers a preference.
            if not identity.membership(wid, user["id"]):
                sessions.set_workspace(session["id"], None)
                wid = None
        request.state.workspace_id = wid
        # With no workspace selected the tenant becomes an id no row can ever carry, so the
        # person sees nothing. Falling back to UI_TENANT here would be a disaster: UI_TENANT is
        # "default", which is exactly the legacy workspace holding every pre-identity file — so
        # the fallback would hand the historical data to every signed-in account.
        return set_tenant(wid or f"__no_workspace__{user['id']}")

    # Paths the account service owns. Everything else under /api/ is DocWeave's own workbench.
    ACCOUNT_PATHS = ("/api/auth/", "/api/admin/", "/oidc/", "/.well-known/")
    SHARED_PATHS = ("/api/health", "/healthz", "/readyz", "/assets/")
    # The account routes an application still needs about its *own* session: who is signed in,
    # which workspace is selected, and how to leave. Credentials, MFA enrolment, invitations and
    # device management stay on the account service, so there is one place to change a password
    # and one place that knows about authenticators.
    APP_ACCOUNT_PATHS = ("/api/auth/state", "/api/auth/logout", "/api/auth/me",
                         "/api/auth/workspace", "/api/auth/sign-out")

    def _role_blocks(path: str) -> str | None:
        """Why this role refuses this path, or None.

        `all` is the default and keeps the single-process behaviour the existing deployment has.
        `accounts` and `app` are what make two origins mean two services rather than two names
        for the same one.
        """
        role = (os.environ.get("DOCWEAVE_ROLE") or "all").strip().lower()
        if role in ("all", ""):
            return None
        api_path = path.startswith("/api/") or path.startswith("/oidc/") \
            or path.startswith("/.well-known/") or path.startswith("/auth/") \
            or path in ("/healthz", "/readyz")
        if not api_path or any(path.startswith(p) for p in SHARED_PATHS):
            return None                                      # the SPA shell and probes are shared
        is_account = any(path.startswith(p) for p in ACCOUNT_PATHS)
        if role == "accounts" and not is_account:
            return "该接口不在账户服务上，请访问应用自身的地址"
        if role in ("app", "portal"):
            # Everything the account service owns is refused here, except the handful of routes
            # an application still needs about its own session. Expressed as "account paths
            # minus the allow-list" rather than as a list of prefixes to block, so a route added
            # to the account service later is refused by default instead of quietly reachable —
            # which is how `/api/admin/` first slipped through on a business role.
            if any(path.startswith(p) for p in ACCOUNT_PATHS) and path not in APP_ACCOUNT_PATHS:
                if path.startswith(("/oidc/", "/.well-known/")):
                    return "该接口在账户服务上，请访问账户服务地址"
                return "账户、安全与平台管理在账户服务上统一管理，请访问账户服务地址"
        if role == "app" and path.startswith("/api/portal/"):
            return "门户接口在平台门户上，不在业务应用上"
        if role == "portal" and not is_account and not path.startswith("/auth/") \
                and not path.startswith("/api/portal/"):
            return "该接口不在平台门户上"
        return None

    @api.middleware("http")
    async def _gate(request: Request, call_next):
        rid = (request.headers.get("X-Request-ID") or "")[:64] or secrets.token_hex(8)
        request.state.request_id = "".join(ch for ch in rid if ch.isalnum() or ch in "-_") or secrets.token_hex(8)
        path = request.url.path
        token = None

        def finish(resp):
            """Every response – including the ones returned before the route runs – carries
            the trace id and the standard headers (docs/integration/api.md)."""
            resp.headers["X-Request-ID"] = request.state.request_id
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
            if path.startswith("/api/"):
                resp.headers.setdefault("Cache-Control", "no-store")
            return resp

        try:
            off = _role_blocks(path)
            if off:
                # One binary, three roles, decided by configuration. Enforced here rather than by
                # conditionally mounting routers: a filter in one place cannot be half-applied,
                # and it means the account service genuinely cannot serve a document — an
                # accounts-origin cookie reaching DocWeave's data would defeat the whole point
                # of giving them separate origins.
                return finish(JSONResponse(_error_body(request, "not_in_this_role", off, None),
                                           status_code=404))
            if path.startswith("/api/v1") and not any(path.startswith(p) for p in PUBLIC_V1):
                principal = machine.verify(request.headers.get("Authorization"))
                request.state.principal = principal
                ok, wait = limiter(principal["key_id"])
                if not ok:
                    raise ServiceError(429, "rate_limited", f"请求过于频繁（限制 {API_RATE_PER_MIN} 次/分钟）", {"retry_after": wait})
                token = set_tenant(principal["tenant"])
            else:
                # Everything that is not a key-authenticated /api/v1 route goes through the UI
                # gate — including signed download links, which skip only the Basic check below.
                #
                # This used to read `elif path.startswith("/api/") or not path.startswith("/api")`,
                # and a path beginning with "/api" but lacking the slash satisfied neither
                # disjunct: the gate was skipped outright and set_tenant never ran. Under
                # DOCWEAVE_UI_AUTH=basic, "/" returned 401 while "/api" and "/apifoo" returned
                # 200. Only the SPA shell was reachable that way, but an authentication branch
                # that some paths fall past is not a shape to leave in place.
                mode = ui_auth_mode()
                if any(path.startswith(p) for p in PLATFORM_PREFIXES + SSO_PREFIXES):
                    # Protocol endpoints authenticate themselves: /oidc/authorize reads the
                    # central session cookie and redirects to /login when there is none, and the
                    # token/userinfo/revoke half authenticates by client credentials and bearer
                    # token. Putting the legacy Basic prompt in front of them would make the
                    # account service unusable as an identity provider in `basic` mode, and
                    # would put an HTTP auth dialog in the middle of a redirect chain.
                    token = set_tenant("__platform__")
                elif mode == "session" and not path.startswith("/api/v1"):
                    token = _session_gate(request, path, finish)
                    if isinstance(token, JSONResponse):
                        return token
                elif mode == "basic" and not path.startswith("/api/v1"):
                    if not auth.check_ui_basic(request.headers.get("Authorization")):
                        return finish(JSONResponse(_error_body(request, "unauthorized", "需要登录", None), status_code=401,
                                                   headers={"WWW-Authenticate": 'Basic realm="DocWeave"'}))
                    token = set_tenant(UI_TENANT)
                else:
                    token = set_tenant(UI_TENANT)
        except ServiceError as e:
            return finish(await _svc_err(request, e))
        try:
            resp = await call_next(request)
        finally:
            if token is not None:
                try:
                    from ..context import _tenant
                    _tenant.reset(token)
                except (ValueError, LookupError):
                    pass
        return finish(resp)

    # ------------------------------------------------------------ system
    @api.get("/api/health")
    def health():
        return {"ok": True}

    @api.get("/healthz", tags=["system"], summary="存活检查（进程是否在运行）")
    def healthz():
        # `instance` lets destructive test tooling prove it is not pointed at real data.
        return {"status": "ok", "version": api.version, "instance": app_.store.instance_kind()}

    @api.get("/readyz", tags=["system"], summary="就绪检查（能否真正处理任务）")
    def readyz():
        checks = {}
        try:
            store.one("SELECT 1 AS x")
            store.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('readyz', ?)", (str(time.time()),))
            checks["database"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["database"] = f"error: {type(e).__name__}"
        try:
            p = Path(store.root) / "blobs" / ".readyz"
            p.write_text("ok")
            p.unlink()
            checks["storage"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["storage"] = f"error: {type(e).__name__}"
        checks["worker"] = "ok" if (jobs and jobs.runner_alive()) else "stopped"
        ready = all(v == "ok" for v in checks.values())
        out = {"status": "ready" if ready else "not_ready", "checks": checks, "schema_version": store.schema_version()}
        if hooks.enabled:
            out["webhooks"] = "enabled"
        return JSONResponse(out, status_code=200 if ready else 503)

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
    def source_page(sid: str, page: int, zoom: float = 1.5, block: int | None = None):
        return Response(views.source_page_png(sid, page, zoom, block), media_type="image/png", headers={"Cache-Control": "private, max-age=600"})

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
    def create_builtin(body: dict = Body(...)):
        from ..builtin_service import create_builtin as cb
        t = cb(app_, body)
        if body.get("project_id"):
            app_.update_project(body["project_id"], {"template_id": t["id"]})
        return t

    @api.put("/api/templates/{tid}/builtin")
    def save_builtin(tid: str, body: dict = Body(...)):
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
        j = jobs.get(jid)
        return attachment(jobs.zip(jid, fmt), f"{j['template']['name']}_批量结果_{jid[-6:]}.zip", "zip")

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

    def _audit(request: Request, action: str, *, actor_id: str | None = None, actor_kind: str = "user",
               workspace_id: str | None = None, object_kind: str | None = None,
               object_id: str | None = None, result: str = "ok", meta: dict | None = None) -> None:
        """Append a security event.

        Metadata only — never a password, token, TOTP seed, recovery code or document content.
        A failure to write must not be mistaken for the action not happening, so it is raised
        rather than swallowed; callers that must not fail closed catch it deliberately.
        """
        import json as _json
        store.execute(
            "INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,"
            "object_id,result,request_id,ip,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("ae"), time.time(), actor_kind, actor_id,
             workspace_id or getattr(request.state, "workspace_id", None), action, object_kind, object_id,
             result, getattr(request.state, "request_id", None),
             (request.client.host if request.client else None), _json.dumps(meta or {}, ensure_ascii=False)))

    from .auth_routes import build_invite_router, build_router as build_auth_router
    api.include_router(build_auth_router(identity, sessions, _audit, mfa, tickets))
    api.include_router(build_invite_router(identity, sessions, invites, mailer, _audit))
    from .auth_routes import build_mfa_router, build_passkey_router
    api.include_router(build_mfa_router(identity, sessions, mfa, _audit))
    from ..passkeys import Passkeys
    passkeys = Passkeys(store)
    api.state.passkeys = passkeys
    api.include_router(build_passkey_router(identity, sessions, passkeys, _audit))

    # ------------------------------------------------------------ platform identity (OIDC provider)
    # Registered before the SPA catch-all so /oidc/* and /.well-known/* reach real handlers
    # instead of being served index.html.
    from ..platform.apps import Apps, bootstrap_docweave
    from ..platform.routes import build_router as build_oidc_router
    bootstrap_docweave(store, identity)
    Apps(store, identity).adopt_existing_members()
    api.include_router(build_oidc_router(store, identity, sessions, _audit))

    # ------------------------------------------------------------ this app as a relying party
    from .rp_routes import build_router as build_rp_router
    api.include_router(build_rp_router(store, identity, sessions, _audit))

    # ------------------------------------------------------------ platform portal
    from .portal_routes import build_router as build_portal_router
    api.include_router(build_portal_router(store, identity, sessions, _audit))

    # ------------------------------------------------------------ platform administration
    from .admin_routes import build_router as build_admin_router
    api.include_router(build_admin_router(store, identity, sessions, _audit))

    # ------------------------------------------------------------ static UI
    dist = Path(os.environ.get("DOCWEAVE_UI_DIR", Path(__file__).resolve().parents[3] / "frontend" / "dist"))
    if dist.exists():
        api.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @api.get("/{full_path:path}")
        def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"error": "not_found", "message": "接口不存在"}, status_code=404)
            target = (dist / full_path).resolve()
            if full_path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            # Which shell this role serves. The portal is a separate build output, not a route
            # inside DocWeave's SPA: one shell owning both would mean the portal's router also
            # owned every DocWeave path, which is how a new entry point swallows the application
            # it was meant to sit beside.
            shell = SHELLS.get((os.environ.get("DOCWEAVE_ROLE") or "").lower(), "index.html")
            if not (dist / shell).exists():
                shell = "index.html"
            return FileResponse(dist / shell, headers={"Cache-Control": "no-cache"})

    return api


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
