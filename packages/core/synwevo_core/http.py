"""共享 HTTP 骨架：错误契约、请求网关、探针与静态外壳。

这一层原本长在 Synwevo Docs 的 `api/app.py` 里，于是账户服务要有一个会话网关，就得导入 Synwevo Docs 的
应用工厂。会话解析、CSRF、租户上下文、角色边界和机器密钥限流**对三个角色是同一套**——它们是平台
的运行契约，不是某个应用的实现细节，所以放在共享层，由每个角色自己的工厂装配。

关键的一条没有变：角色边界仍由这里的中间件统一判定，而不是靠"哪些路由被挂上去了"。少挂一个路由
只会得到一个没有解释的 404；而一个集中判定的网关无法被"漏挂一半"，账户服务也就真的不可能服务
一份文档。
"""
from __future__ import annotations

import os
import secrets
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import env
from .auth import Auth, rate_limiter, ui_auth_mode
from .context import UI_TENANT, set_tenant
from .errors import ServiceError
from .identity import Identity
from .machine import MachineAuth
from .sessions import Sessions
from .store import Store, new_id

API_RATE_PER_MIN = int(env.get("API_RATE_PER_MIN", "300"))
PUBLIC_V1 = ("/api/v1/download/",)          # signed links carry their own proof


def role() -> str:
    """Which service this process is, from the environment: ``accounts``, ``portal``, ``app``
    or ``all``.

    Used where there is no application to ask — a CLI, a helper called before the app exists.
    Anything that has an application should use :func:`app_role`, because a composer may have
    been told the role outright, and two answers that can disagree is worse than one that is
    sometimes coarse. A module-level override was the other option and it was wrong: it made
    building one app change the behaviour of every app built afterwards in the same process,
    which is exactly what a test suite does.
    """
    return (env.get("ROLE") or "all").strip().lower() or "all"


def app_role(request_or_app) -> str:
    """The role this particular application was composed for."""
    state = getattr(request_or_app, "app", request_or_app)
    return getattr(getattr(state, "state", None), "role", None) or role()


@dataclass
class Platform:
    """The objects every role needs, built once and shared by whatever is mounted.

    One `Store` per process, and therefore one account authority: the account service and an
    application running in the same process must not end up with two connections' worth of
    opinions about who is suspended.
    """
    store: Store
    auth: Auth
    identity: Identity
    sessions: Sessions
    machine: MachineAuth
    audit: Callable[..., None]


MARKERS = ("packages/core", "apps/docs", "services/identity")


def repo_root() -> Path | None:
    """The platform repository this module is running from, or None when installed elsewhere.

    Found by walking up from this file and looking for the workspace's own directories, not by
    counting `..` — a fixed number of parents is right in a source checkout and silently wrong
    in a site-packages install, and "silently wrong" here means a different data directory.
    """
    for parent in Path(__file__).resolve().parents:
        if all((parent / m).is_dir() for m in MARKERS):
            return parent
    return None


def data_dir_default() -> Path:
    """The data directory when nothing says otherwise: the repository root's ``data/``.

    Resolved from this file's own location, never from the process working directory — starting
    the same service from the platform root and from ``apps/docs/`` has to load the same
    database, and a cwd-relative default is exactly how it would not.

    Outside a source checkout there is no defensible default, so this raises instead of guessing.
    A guess that lands somewhere writable does not fail: it creates an empty database and the
    service comes up looking like a fresh install, which is the failure mode that reads as
    "the data is gone".
    """
    root = repo_root()
    if root is None:
        raise RuntimeError(
            "找不到平台仓库根目录，无法推断数据目录。请显式设置 SYNWEVO_DATA_DIR 指向你的数据目录"
            "（容器镜像里是 /data）。这里不猜：猜错的结果是建出一个空库，服务照常启动，看起来就像数据没了。")
    return root / "data"


def build_platform(data_dir: str | None = None) -> Platform:
    store = Store(data_dir or env.get("DATA_DIR") or str(data_dir_default()))
    auth = Auth(store)
    identity = Identity(store)
    sessions = Sessions(store)
    # One resolver for every machine caller: legacy API keys, service accounts and user
    # delegations all come out as the same principal shape, so no call site has to know which
    # kind it got — and none of them can forget to re-check one of the three.
    machine = MachineAuth(store, identity, auth)

    def audit(request: Request, action: str, *, actor_id: str | None = None, actor_kind: str = "user",
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

    return Platform(store=store, auth=auth, identity=identity, sessions=sessions,
                    machine=machine, audit=audit)


def new_api(title: str) -> FastAPI:
    show_docs = (env.get("API_DOCS") or "").strip().lower() in ("1", "true", "on", "yes")
    return FastAPI(title=title, version="1.0.0",
                   docs_url="/api/docs" if show_docs else None,
                   redoc_url=None,
                   openapi_url="/api/openapi.json" if show_docs else None)


def install(api: FastAPI, plat: Platform, *, role: str | None = None) -> None:
    """Error contract + the request gate. Every role installs exactly this."""
    store, auth, identity, sessions, machine = plat.store, plat.auth, plat.identity, plat.sessions, plat.machine
    api.state.role = (role or globals()["role"]()).strip().lower()
    this_role_static = api.state.role

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
        print(f"[synwevo] unhandled error request_id={rid} path={request.url.path}\n{traceback.format_exc()}", flush=True)

    limiter = rate_limiter(API_RATE_PER_MIN)

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
                       "/api/auth/login/passkey", "/api/auth/passkeys/authenticate/begin",
                       # Someone who has forgotten their password cannot be signed in to ask
                       # for a way back; the emailed token is the authority for the reset.
                       "/api/auth/forgot", "/api/auth/reset", "/api/auth/reset/preview",
                       "/api/auth/verify-email"}
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
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def _session_gate(request: Request, path: str, finish):
        """Resolve the session cookie into an identity, or refuse.

        Returns the tenant-context token on success, or a JSONResponse to return as-is. The
        workspace comes from the session row and is re-checked against live membership on every
        request, so a workspace someone has been removed from stops working immediately rather
        than at the end of their session.
        """
        from .sessions import (COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, LEGACY_CSRF_HEADER,
                               csrf_ok)
        session = sessions.load(request.cookies.get(COOKIE_NAME))
        request.state.session = session

        if session and request.method not in SAFE_METHODS and path.startswith("/api/"):
            # Double-submit CSRF on every state-changing request. Login is exempt because it has
            # no session yet; it is protected by being useless without valid credentials.
            sent = request.headers.get(CSRF_HEADER) or request.headers.get(LEGACY_CSRF_HEADER)
            if path not in ANON_AUTH_PATHS and not csrf_ok(request.cookies.get(CSRF_COOKIE), sent):
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

    # Paths the account service owns. Everything else under /api/ is Synwevo Docs' own workbench.
    ACCOUNT_PATHS = ("/api/auth/", "/api/admin/", "/oidc/", "/.well-known/")
    SHARED_PATHS = ("/api/health", "/healthz", "/readyz", "/assets/")
    # Liveness and readiness. An **exact set**, not a prefix: SHARED_PATHS above matches by
    # prefix, so a route added later called /healthz-debug would inherit anonymity by accident.
    # These two are the only paths a load balancer or container runtime must reach without a
    # credential, and neither handler reads tenant-scoped data.
    PROBE_PATHS = frozenset({"/healthz", "/readyz"})
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
        this_role = this_role_static
        if this_role in ("all", ""):
            return None
        api_path = path.startswith("/api/") or path.startswith("/oidc/") \
            or path.startswith("/.well-known/") or path.startswith("/auth/") \
            or path in ("/healthz", "/readyz")
        if not api_path or any(path.startswith(p) for p in SHARED_PATHS):
            return None                                      # the SPA shell and probes are shared
        is_account = any(path.startswith(p) for p in ACCOUNT_PATHS)
        if this_role == "accounts" and not is_account:
            return "该接口不在账户服务上，请访问应用自身的地址"
        if this_role in ("app", "portal"):
            # Everything the account service owns is refused here, except the handful of routes
            # an application still needs about its own session. Expressed as "account paths
            # minus the allow-list" rather than as a list of prefixes to block, so a route added
            # to the account service later is refused by default instead of quietly reachable —
            # which is how `/api/admin/` first slipped through on a business role.
            if any(path.startswith(p) for p in ACCOUNT_PATHS) and path not in APP_ACCOUNT_PATHS:
                if path.startswith(("/oidc/", "/.well-known/")):
                    return "该接口在账户服务上，请访问账户服务地址"
                return "账户、安全与平台管理在账户服务上统一管理，请访问账户服务地址"
        if this_role == "app" and path.startswith("/api/portal/"):
            return "门户接口在平台门户上，不在业务应用上"
        if this_role == "portal" and not is_account and not path.startswith("/auth/") \
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
                # accounts-origin cookie reaching Synwevo Docs' data would defeat the whole point
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
                # SYNWEVO_UI_AUTH=basic, "/" returned 401 while "/api" and "/apifoo" returned
                # 200. Only the SPA shell was reachable that way, but an authentication branch
                # that some paths fall past is not a shape to leave in place.
                mode = ui_auth_mode()
                if path in PROBE_PATHS:
                    # Anonymous in every mode. Under `basic` these previously answered 401,
                    # which contradicted ports.md, the committed OpenAPI (their security is [])
                    # and permissions.md §5.3 — and it makes a container's own restart policy
                    # depend on a shared password. The bodies are kept to the minimum that the
                    # contract promises; see the handlers.
                    token = set_tenant("__probe__")
                elif any(path.startswith(p) for p in PLATFORM_PREFIXES + SSO_PREFIXES):
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
                                                   headers={"WWW-Authenticate": 'Basic realm="Synwevo Docs"'}))
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
                    from .context import _tenant
                    _tenant.reset(token)
                except (ValueError, LookupError):
                    pass
        return finish(resp)


def install_probes(api: FastAPI, store: Store, worker: Callable[[], bool] | None = None) -> None:
    """Liveness and readiness, identical in every role.

    ``worker`` is what this role's background work is: Synwevo Docs' job runner, the account
    service's mail sender, or nothing at all. A role with no worker reports none rather than
    reporting somebody else's.
    """

    @api.get("/api/health")
    def health():
        return {"ok": True}

    @api.get("/healthz", tags=["system"], summary="存活检查（进程是否在运行）",
             description="无需鉴权。返回 `{\"status\":\"ok\"}`；仅测试实例额外返回 `instance`。")
    def healthz():
        """Alive. Deliberately says almost nothing.

        `version` used to be here and is gone: this is the one anonymous endpoint, and the exact
        build string is what someone matching a service against a vulnerability list wants.
        An authenticated caller still gets `api_version` from /api/v1/capabilities.

        `instance` is emitted **only on a test instance**, where it is the marker destructive
        tooling uses to prove it is not pointed at real data. On a normal instance the key is
        absent, and the test helper compares `!= "test"` — so a missing key refuses, which is
        the direction that has to fail safe.
        """
        out = {"status": "ok"}
        kind = store.instance_kind()
        if kind == "test":
            out["instance"] = kind
        return out

    @api.get("/readyz", tags=["system"], summary="就绪检查（能否真正处理任务）",
             description="无需鉴权。200 `ready` / 503 `not_ready`，带 `checks` 组件级状态；不含版本、"
                         "schema 版本等诊断信息。")
    def readyz():
        """Ready to actually do work: database writable, storage writable, worker alive.

        **It does not write a row.** It used to `INSERT OR REPLACE INTO meta`, so every
        unauthenticated probe produced a WAL commit — a container on a 5-second readiness check
        wrote ~17k rows a day, and anyone who could reach the port could drive that. Taking and
        releasing the write lock says the same thing for free: measured, 200 probes now grow the
        WAL by 0 bytes.

        What this check does **not** prove, measured rather than assumed: neither the lock nor
        the old INSERT detects a filesystem turned read-only *under an already-open handle* —
        SQLite writes into the WAL it already holds. The `storage` check below is what actually
        catches that case, and it is why the endpoint still returns 503.

        **The storage probe does not write inside `blobs/`.** A crash between write and unlink
        left a non-blob file in the content-addressed tree, which a future mark-and-sweep would
        have to special-case — and special cases in a delete path are how the wrong file gets
        deleted.
        """
        checks = {}
        try:
            with store.tx():
                pass                       # BEGIN IMMEDIATE + rollback: writable, no commit
            checks["database"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["database"] = f"error: {type(e).__name__}"
        try:
            p = Path(store.root) / ".readyz-probe"
            p.write_text("ok")
            p.unlink()
            checks["storage"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["storage"] = f"error: {type(e).__name__}"
        if worker is not None:
            checks["worker"] = "ok" if worker() else "stopped"
        ready = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ready" if ready else "not_ready", "checks": checks},
                            status_code=200 if ready else 503)


class HashedAssets(StaticFiles):
    """Vite's build output, whose filenames already contain a content hash.

    `main-Fb_P6FP5.js` changes its *name* when its bytes change, so the bytes under any one name
    never change and the browser never needs to ask again. `immutable` is what says that: it
    stops even a manual reload from revalidating, which is the difference between a reload
    costing one request and costing one per bundle.

    Applied only to files that actually carry a hash. A build output without one would be a file
    whose contents *can* change under a fixed name, and telling a browser to keep that for a year
    is how a deploy silently fails to reach people.

    The mount sits behind the same gate as everything else — this only adds a header to a
    response the gate has already allowed.
    """

    _HASHED = __import__("re").compile(r"-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$")

    def file_response(self, full_path, stat_result, scope, status_code=200):
        resp = super().file_response(full_path, stat_result, scope, status_code)
        name = os.path.basename(str(full_path))
        if self._HASHED.search(name):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # No hash in the name ⇒ the bytes can change under this name. Revalidate.
            resp.headers["Cache-Control"] = "no-cache"
        return resp




def mount_web(api: FastAPI, dist: Path, *, assets_dir: str = "assets") -> bool:
    """Serve one built single-page shell and its hashed assets.

    Each shell is now its own build with its own asset directory name, so a process serving
    several of them (the single-process deployment serves the workbench and the account pages)
    mounts several without one build's `/assets` hiding another's.
    """
    dist = Path(dist)
    if not dist.exists():
        return False
    assets = dist / assets_dir
    if assets.exists():
        api.mount(f"/{assets_dir}", HashedAssets(directory=assets), name=f"assets-{assets_dir}")
    return True


def shell_response(dist: Path, full_path: str) -> FileResponse | JSONResponse:
    """A real file when the build has one, otherwise the shell itself.

    Asking for the shell **by filename** used to be served as a generic file — with no
    Cache-Control at all. A cached entry document is the worst thing to hold onto: it names
    asset hashes, and after a deploy those files are gone, so a stale shell is a blank page that
    a reload does not fix.
    """
    dist = Path(dist)
    if full_path.startswith("api/"):
        return JSONResponse({"error": "not_found", "message": "接口不存在"}, status_code=404)
    target = (dist / full_path).resolve()
    if full_path and full_path != "index.html" and target.is_file() and dist.resolve() in target.parents:
        return FileResponse(target)
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})
