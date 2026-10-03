"""共享 HTTP 骨架：错误契约、请求网关、探针与静态外壳。

这一层原本长在 Synwevo Docs 的 `api/app.py` 里，于是账户服务要有一个会话网关，就得导入 Synwevo Docs 的
应用工厂。会话解析、CSRF、租户上下文、角色边界和机器密钥限流**对三个角色是同一套**——它们是平台
的运行契约，不是某个应用的实现细节，所以放在共享层，由每个角色自己的工厂装配。

关键的一条没有变：角色边界仍由这里的中间件统一判定，而不是靠"哪些路由被挂上去了"。少挂一个路由
只会得到一个没有解释的 404；而一个集中判定的网关无法被"漏挂一半"，账户服务也就真的不可能服务
一份文档。
"""
from __future__ import annotations

import contextlib

import json
import logging
import math
import os
import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import env
from .auth import Auth, rate_limiter, ui_auth_mode  # noqa: F401  (rate_limiter: re-exported for callers)
from .context import UI_TENANT, set_tenant
from .errors import ServiceError
from .identity import Identity
from .machine import MachineAuth
from .sessions import Sessions
from .store import Store, new_id

API_RATE_PER_MIN = int(env.get("API_RATE_PER_MIN", "300"))

#: HTTP security headers (OWASP ASVS 4.0.3 V14.4; OWASP HTTP Headers Cheat Sheet, read 2026-09-25).
#: The policy is one string for all three shells because they are built the same way: Vite
#: bundles under the origin (no inline scripts), React inline `style=` attributes (hence
#: 'unsafe-inline' for styles only), images from the origin plus `data:` (Vite inlines assets under
#: 4 KiB, e.g. the 1x logo mark of the loading states) and the `blob:` object URLs the Docs viewer and avatar preview create, no third-party resource at all.
#: `frame-ancestors 'self'` is the modern form of X-Frame-Options SAMEORIGIN (kept for old
#: browsers); `form-action 'self'` holds because every form submits through fetch to its own
#: origin and the OpenID hand-offs are plain navigations. `SYNWEVO_CSP` replaces the policy
#: (`off` sends none) so an operator can loosen it without a build; the default is the contract.
DEFAULT_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; "
               "frame-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'self'; "
               "base-uri 'self'; form-action 'self'; object-src 'none'")
#: Behind Cloudflare (`CF-Ray` on the request) the edge injects its Web Analytics beacon
#: (`static.cloudflareinsights.com/beacon.min.js`, posting to `cloudflareinsights.com`) into every
#: HTML page when the zone owner has enabled Web Analytics. The strict policy blocked it — one
#: console error per page view and analytics the operator had turned on silently gone. Allowing
#: exactly those two origins, only on requests that came through Cloudflare, keeps the operator's
#: choice working without loosening the policy anywhere else: the edge already controls every byte
#: it forwards, so naming its beacon adds no trust the deployment had not granted.
CLOUDFLARE_CSP = DEFAULT_CSP.replace("script-src 'self';", "script-src 'self' https://static.cloudflareinsights.com;") \
                            .replace("connect-src 'self';", "connect-src 'self' https://cloudflareinsights.com;")
HSTS = "max-age=31536000; includeSubDomains"          # one year, per host; no `preload` (not the apex)
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"
#: The origins that serve Synwevo Message record voice messages, so the microphone is allowed for
#: the page itself there (`self`, never a third-party frame); every other origin keeps it closed.
#: Roles: the messages role and the single-process `all` (which serves the messages shell).
PERMISSIONS_POLICY_MESSAGES = PERMISSIONS_POLICY.replace("microphone=()", "microphone=(self)", 1)


def content_security_policy(request: Request | None = None) -> str | None:
    """The CSP to send, or None when the operator turned it off (`SYNWEVO_CSP=off`).

    An explicit `SYNWEVO_CSP` is sent verbatim. The default is the strict policy, widened for the
    Cloudflare beacon only when the request carries `CF-Ray` (see CLOUDFLARE_CSP)."""
    raw = (env.get("CSP") or "").strip()
    if raw.lower() == "off":
        return None
    if raw:
        return raw
    if request is not None and request.headers.get("cf-ray"):
        return _with_challenge_origins(CLOUDFLARE_CSP)
    return _with_challenge_origins(DEFAULT_CSP)


def _with_challenge_origins(csp: str) -> str:
    """When human verification is configured, the Turnstile widget script and its iframe come
    from exactly one origin (Turnstile "Content Security Policy", read 2026-09-25: script-src
    and frame-src ``https://challenges.cloudflare.com``); nothing else widens."""
    from .challenge import WIDGET_ORIGIN, policy as challenge_policy
    try:
        enabled = challenge_policy().enabled
    except ValueError:            # half-configured: the account service refuses to start anyway
        enabled = False
    if not enabled:
        return csp
    return (csp.replace("script-src 'self'", f"script-src 'self' {WIDGET_ORIGIN}", 1)
               .replace("frame-src 'self' blob:", f"frame-src 'self' blob: {WIDGET_ORIGIN}", 1))


def request_is_https(request: Request) -> bool:
    """Whether the client reached us over TLS: the scheme uvicorn saw, or the proxy's word
    (`X-Forwarded-Proto`, as the production gateway sets it). A forged header from a direct
    plain-HTTP client only earns it an HSTS header a browser ignores over HTTP."""
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https"


def apply_security_headers(request: Request, resp) -> None:
    """The headers every response carries (see docs/integration/api.md)."""
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    role = getattr(getattr(request.app, "state", None), "role", "") if hasattr(request, "app") else ""
    resp.headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY_MESSAGES if role in ("messages", "all") else PERMISSIONS_POLICY)
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    csp = content_security_policy(request)
    if csp:
        resp.headers.setdefault("Content-Security-Policy", csp)
    if request_is_https(request):
        resp.headers.setdefault("Strict-Transport-Security", HSTS)
PUBLIC_V1 = ("/api/v1/download/",)          # signed links carry their own proof
#: The workspace directory API (API-5): machine-only, on the account service, its own resource.
DIRECTORY_V1 = "/api/workspace/v1"
#: The business applications' operations API: machine-only, on the workplace origin, its own
#: resource. Under the applications' prefix, so the roles that do not serve them refuse it too;
#: unlike everything else there it never reads a session cookie.
SUITE_V1 = "/api/suite/v1"
MACHINE_PREFIXES = ("/api/v1", DIRECTORY_V1, SUITE_V1)


def machine_path(path: str) -> bool:
    """A path of a machine API (Docs /api/v1, the workspace directory, the business applications'
    operations): the error envelope, RateLimit headers and call record apply to all of them."""
    return path.startswith(MACHINE_PREFIXES)

#: The roles a process can be composed as. Anything else is refused at startup: an unknown role
#: used to fall through every branch and start a server whose API answered 404 everywhere while
#: its health probes reported ready.
ROLES = ("all", "accounts", "portal", "app", "messages")

#: Workspace roles that may change content (permissions.md §4.2). Viewers read.
WRITE_ROLES = frozenset({"owner", "admin", "editor"})

#: The messages application's API (Synwevo Message, its own origin since CI-66). Served by the
#: `messages` role and by `all`; every other role refuses it by name, so a stale bookmark or a
#: shell that still points at the account origin gets an answer that says where to go.
MESSAGING_PREFIX = "/api/messaging/"

# Paths under /api/ that belong to the account system, the portal or the messages application
# rather than to Synwevo Docs. They need a live account and nothing more (conversations are
# authorized by the messaging service layer, per organization, not per workspace); everything
# else under /api/ on a role that serves Synwevo Docs is Docs' own and is checked against
# workspace and application access.
#: The business applications on the workplace origin (Forms, Tables, DataPrep, Approvals, Extract,
#: the personal Agent; docs/agent-work/DECISIONS.md D1). Served by the `portal` role and by `all`.
#: They authorize every call themselves, per application and per explicitly named workspace
#: (the capability layer, capabilities.py), so on `all` they are not Synwevo Docs routes: the Docs gate would
#: check the wrong application and read the workspace from the tab binding.
SUITE_PREFIX = "/api/suite/"
#: The part of it a person without an account reaches: a published form's public link and a
#: personal fill-in request link. The link's own token is the authority; the handlers check it.
SUITE_PUBLIC_PREFIX = "/api/suite/public/"
ACCOUNT_LEVEL_PREFIXES = ("/api/auth/", "/api/admin/", "/api/portal/", MESSAGING_PREFIX, SUITE_PREFIX)
ACCOUNT_LEVEL_EXACT = frozenset({"/api/health"})


class PrivateAccessLog(logging.Filter):
    """Uvicorn's request target must not retain OAuth codes or other query data.

    Keep method/path/status metadata. Apply at the logger (before every handler),
    covering direct uvicorn startup as well as the repository's launch command.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            target = args[2].split("?", 1)[0].split("#", 1)[0]
            target = "".join(c if c.isprintable() else "_" for c in target)
            record.args = (*args[:2], target, *args[3:])
        else:
            # Unknown access-record formats cannot be assumed safe to persist.
            return False
        return True


def install_private_access_log() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, PrivateAccessLog) for item in logger.filters):
        logger.addFilter(PrivateAccessLog())


def validate_role(value: str | None) -> str:
    """Normalise a role name, refusing anything that is not one of :data:`ROLES`."""
    r = (value or "all").strip().lower() or "all"
    if r not in ROLES:
        raise RuntimeError(
            f"SYNWEVO_ROLE={value!r} 不是可用的角色。只能是 {' / '.join(ROLES)}"
            "（accounts=账户服务，portal=门户，app=Synwevo Docs，messages=Synwevo Message，all=单进程部署）。"
            "未知角色不会被猜成其中任何一个：那样会启动一个所有接口都返回 404、健康检查却报告就绪的进程。")
    return r


def trusted_proxy_hops() -> int:
    """How many reverse proxies in front of this service are trusted: `SYNWEVO_TRUST_PROXY`.

    A count, with ``1``/``true``/``yes``/``on`` meaning one. Unset, ``0`` or anything else means
    none, and X-Forwarded-For is not read at all.
    """
    raw = (env.get("TRUST_PROXY") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return 1
    return int(raw) if raw.isdigit() else 0


def client_ip(request: Request) -> str:
    """The caller's address, for audit rows, session rows and address-based rate limits.

    X-Forwarded-For is honoured only when `SYNWEVO_TRUST_PROXY` says trusted proxies are in
    front, because otherwise any caller could write whatever address they liked. And then only
    the entry *our* proxies wrote: each proxy appends the address it received the request from,
    so the left end is whatever the client chose to send. This used to take the left-most entry,
    which let anyone put any address into `audit_events.ip` and into their session row — and it
    read ``TRUST_PROXY=2`` as "no proxy", so a two-proxy deployment recorded the inner proxy.
    With N trusted proxies the address is the entry N places from the right; repeated header
    lines are joined in order first, as HTTP allows.

    The account service's rate limiter asks the same question and must get the same answer, or
    the address that was throttled and the address that was audited are two different ones.
    """
    hops = trusted_proxy_hops()
    if hops:
        raw = ",".join(request.headers.getlist("x-forwarded-for"))
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts:
            return (parts[-hops] if len(parts) >= hops else parts[0])[:64]
    return (request.client.host if request.client else "") or ""


# --------------------------------------------------------------------- RFC 9457 problem details
PROBLEM_JSON = "application/problem+json"
PROBLEM_TITLES = {400: "请求无效", 401: "需要认证", 403: "没有权限", 404: "资源不存在",
                  405: "不支持该请求方法", 409: "状态冲突", 410: "资源已失效", 413: "内容过大",
                  415: "不支持的内容类型", 422: "无法处理的请求", 429: "请求过于频繁",
                  500: "服务器内部错误", 502: "上游服务错误", 503: "服务暂不可用"}


def wants_problem(request: Request) -> bool:
    """Did the caller ask for `application/problem+json` (with a non-zero q)?

    Opt-in through Accept, so every existing /api/v1 client keeps the envelope it was written
    against — the v1 promise does not allow removing it — while tooling that speaks RFC 9457
    gets the standard shape.
    """
    for part in (request.headers.get("accept") or "").split(","):
        media, _, params = part.strip().partition(";")
        if media.strip().lower() != PROBLEM_JSON:
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, v = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(v)
                except ValueError:
                    q = 1.0
        if q > 0:
            return True
    return False


def json_pointer(field) -> str:
    """`#/a/b` for a field name (dotted for nesting) or a validation location tuple (RFC 6901)."""
    segs = [str(s) for s in field] if isinstance(field, (list, tuple)) else str(field).split(".")
    return "#/" + "/".join(s.replace("~", "~0").replace("/", "~1") for s in segs if s != "")


def problem_body(request_id: str, status: int, code: str, message: str, detail: dict | None) -> dict:
    """The RFC 9457 rendering of one error. `code` and `request_id` are kept as extension
    members so a client can switch formats without losing what it already keys on."""
    try:
        phrase = HTTPStatus(status).phrase
    except ValueError:
        phrase = "Error"
    body = {"type": f"urn:synwevo:problem:{code}", "title": PROBLEM_TITLES.get(status, phrase),
            "status": status, "detail": message, "instance": f"urn:synwevo:request:{request_id}",
            "code": code, "request_id": request_id}
    detail = detail if isinstance(detail, dict) else {}
    errors = []
    for e in detail.get("errors") or ():
        if isinstance(e, dict) and (e.get("field") or e.get("loc")):
            errors.append({"detail": e.get("message") or message,
                           "pointer": json_pointer(e.get("loc") or e.get("field"))})
    if not errors and detail.get("field"):
        errors.append({"detail": message, "pointer": json_pointer(detail["field"])})
    if errors:
        body["errors"] = errors
    rest = {k: v for k, v in detail.items() if k not in ("field", "errors")}
    if rest:
        body["details"] = rest
    return body


def retry_after_seconds(detail) -> int | None:
    """`Retry-After` delay-seconds for an error detail carrying ``retry_after``, or None.

    Rounded up and never below 1: a client told "0" retries at once, which is what the refusal
    was about. Anything that is not a positive number is ignored rather than guessed at.
    """
    raw = detail.get("retry_after") if isinstance(detail, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if not math.isfinite(raw) or raw <= 0:
        return None
    return max(1, math.ceil(raw))


# --------------------------------------------------------------------- rate limiting
RATELIMIT_POLICY_NAME = "key"


class RateBuckets:
    """One token bucket per key, per process — for limits that are about one live connection
    (the live-editing rooms pace a socket's frames with it).

    The machine API no longer uses it: request rate there is the layered policy administrators
    edit, counted in the shared store so N processes do not mean N × the limit
    (`synwevo_core.api_quotas.RateGate`, API-1).

    The same arithmetic as `synwevo_core.auth.rate_limiter`, but it also reports how much is
    left, so every response can tell the client how to pace itself instead of only the 429 that
    tells it it went too fast.
    """

    WINDOW_S = 60
    #: Buckets idle this long are full again and carry no information; dropping them keeps the
    #: map bounded by recent callers rather than by every caller ever — which matters once a
    #: bucket is keyed by client address.
    PRUNE_EVERY = 1024

    def __init__(self, per_min: int, *, name: str = RATELIMIT_POLICY_NAME):
        self.cap = max(1, int(per_min))
        self.name = name
        self._state: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._takes = 0

    def take(self, key: str, cost: float = 1) -> tuple[bool, float, float]:
        """(allowed, tokens left after this request, seconds until a request would be allowed).

        ``cost``: what this request weighs (at most the bucket's capacity, or it could never pass).
        A refused request takes nothing, so waiting the seconds it was told is always enough."""
        cost = min(float(cost), float(self.cap))
        with self._lock:
            t = time.monotonic()
            self._takes += 1
            if self._takes % self.PRUNE_EVERY == 0:
                idle = self.WINDOW_S * 2
                for k in [k for k, v in self._state.items() if t - v[1] > idle]:
                    del self._state[k]
            b = self._state.setdefault(key, [float(self.cap), t])
            tokens = min(float(self.cap), b[0] + (t - b[1]) * self.cap / self.WINDOW_S)
            if tokens < cost:
                b[0], b[1] = tokens, t
                return False, tokens, (cost - tokens) * self.WINDOW_S / self.cap
            b[0], b[1] = tokens - cost, t
            return True, tokens - cost, 0.0

    def headers(self, tokens_left: float) -> dict[str, str]:
        """`RateLimit-Policy` and `RateLimit` (draft-ietf-httpapi-ratelimit-headers).

        Kept in one place because the fields are still an Internet-Draft: a rename is a change
        here and nowhere else. `t` is delay-seconds — how long until the next whole request is
        available — so client and server clocks never need to agree.
        """
        remaining = max(0, math.floor(tokens_left + 1e-9))
        if tokens_left >= self.cap:
            reset = 0
        else:
            reset = max(1, math.ceil((remaining + 1 - tokens_left) * self.WINDOW_S / self.cap))
        name = self.name
        return {"RateLimit-Policy": f'"{name}";q={self.cap};w={self.WINDOW_S}',
                "RateLimit": f'"{name}";r={remaining};t={reset}'}


def _route_template(request: Request) -> str:
    """The /api/v1 route template a request *would* have reached, for requests refused before
    routing (rate limit, credential checks), so their call record names the operation rather than
    "<unmatched>". Only a template is ever returned — never the request's own path, which can carry
    names a caller chose. The flattened route list is built once per application."""
    from starlette.routing import Match
    app = request.app
    routes = getattr(app.state, "v1_route_index", None)
    if routes is None:
        routes, pending = [], list(getattr(app, "routes", ()))
        while pending:
            route = pending.pop(0)
            inner = getattr(route, "original_router", None)          # an included router (FastAPI ≥ 0.140)
            if inner is not None:
                pending[:0] = list(getattr(inner, "routes", ()))
            elif machine_path(str(getattr(route, "path", ""))) and hasattr(route, "matches"):
                routes.append(route)
        app.state.v1_route_index = routes
    for route in routes:
        try:
            match, _ = route.matches(request.scope)
        except Exception:  # noqa: BLE001 - a route that cannot judge this scope simply does not match
            continue
        if match == Match.FULL:
            return route.path
    return ""


def app_access_refusal(store: Store, identity: Identity, user_id: str, workspace_id: str,
                       app_id: str | None = None) -> ServiceError | None:
    """Why this person may not use the application in this workspace right now, or None.

    Two answers, kept apart because they need different fixes: the workspace itself is not
    usable (frozen, awaiting a claim, deleted — nobody may act in it), or the workspace is fine
    but this person's access to the application is not (the application is off platform-wide or
    for this workspace, or their entitlement was revoked). Checked on every request and on every
    workspace switch — the sign-in callback checking it once is how revoked access used to
    survive for the rest of a 30-day session.
    """
    from .apps import DOCS_APP_ID, Apps
    ws = identity.get_workspace(workspace_id)
    if not ws or ws["status"] != "active":
        status = (ws or {}).get("status") or "deleted"
        message = {"frozen": "该工作空间已被冻结，暂时不能使用。请联系工作空间所有者或平台管理员。",
                   "pending_claim": "该工作空间尚未完成认领，暂时不能使用。"}.get(
                       status, "该工作空间已删除，不能再使用。")
        return ServiceError(403, "workspace_unavailable", message,
                            {"workspace_id": workspace_id, "workspace_status": status})
    ok, _, reason = Apps(store, identity).check_access(user_id, app_id or DOCS_APP_ID, workspace_id)
    if not ok:
        return ServiceError(403, "app_access_revoked", reason,
                            {"workspace_id": workspace_id, "app_id": app_id or DOCS_APP_ID})
    return None


def check_standalone_listen(argv: list[str] | None = None) -> None:
    """Refuse to serve an unprotected UI to the network, for a server started without `run.py`.

    `python -m uvicorn synwevo_docs.api.main:app --host 0.0.0.0` never went through
    `check_listen_config`, which only `synwevo_server.run` called — so the documented standalone
    start served the legacy workspace to the network with `SYNWEVO_UI_AUTH` unset. The ASGI app
    cannot see the bind address, so the uvicorn/gunicorn command line is read for it, falling
    back to `SYNWEVO_HOST`.
    """
    from .auth import check_listen_config
    args = list(sys.argv if argv is None else argv)
    binds: list[str] = []
    for i, a in enumerate(args):
        if a in ("--host", "-b", "--bind") and i + 1 < len(args):
            binds.append(args[i + 1])
        elif a.startswith("--host=") or a.startswith("--bind="):
            binds.append(a.split("=", 1)[1])
    # gunicorn takes `-b` more than once and listens on all of them, so every one is checked;
    # checking only the last let `-b 127.0.0.1:8765 -b 0.0.0.0:8766` through.
    hosts = [h for h in (bind_host(b) for b in binds) if h is not None]
    if not binds:
        hosts = [env.get("HOST") or "127.0.0.1"]
    for host in hosts:
        check_listen_config(host)


def bind_host(bind: str) -> str | None:
    """The address part of a uvicorn `--host` or gunicorn `-b` value; None for a socket.

    ``[::1]:8765`` is an IPv6 address with a port. Stripping brackets from the whole string
    turned it into ``::1]:8765``, which is not loopback, so a local IPv6 bind was refused.
    """
    b = bind.strip()
    if b.startswith(("unix:", "fd:")):
        return None                                   # a socket file or descriptor: not a network
    if b.startswith("["):
        end = b.find("]")
        return b[1:end] if end != -1 else b[1:]
    if b.count(":") == 1:
        # gunicorn's host:port. An empty host (`-b :8765`) is every interface, not "unset".
        return b.split(":", 1)[0] or "0.0.0.0"
    return b or "0.0.0.0"                             # a hostname, IPv4 or bare IPv6


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
    return validate_role(env.get("ROLE"))


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


def data_dir(explicit: str | os.PathLike | None = None) -> Path:
    """The data directory this process uses: `explicit`, else `SYNWEVO_DATA_DIR`, else the default.

    An `explicit` path (a `--data` argument, a test's temporary directory) is the caller's own
    and is taken as given. A **relative** `SYNWEVO_DATA_DIR` is resolved against the repository
    root, never against the process working directory — the rule `scripts/platform.py` and
    `snapshot.py --data` already follow. Taken relative to cwd, `SYNWEVO_DATA_DIR=data` meant
    the repository's database when started from the root and a brand-new empty one when started
    from `apps/docs/`, and an empty database starts up looking exactly like lost data. Outside a
    source checkout there is no root to resolve against, so a relative value is refused.
    """
    if explicit:
        return Path(explicit)
    raw = env.get("DATA_DIR")
    if not raw:
        return data_dir_default()
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    root = repo_root()
    if root is None:
        raise RuntimeError(
            f"SYNWEVO_DATA_DIR={raw!r} 是相对路径，而这里不是平台仓库的源码目录，没有可以相对的根。"
            "请改成绝对路径（容器镜像里是 /data）。这里不按当前工作目录去猜：从不同目录启动会落到不同的库。")
    return (root / p).resolve()


def build_platform(data_dir_arg: str | None = None) -> Platform:
    install_private_access_log()
    store = Store(str(data_dir(data_dir_arg)))
    auth = Auth(store)
    identity = Identity(store)
    sessions = Sessions(store)
    # One resolver for every machine caller: legacy API keys, service accounts and user
    # delegations all come out as the same principal shape, so no call site has to know which
    # kind it got — and none of them can forget to re-check one of the three.
    machine = MachineAuth(store, identity, auth)

    def audit(request: Request, action: str, *, actor_id: str | None = None, actor_kind: str = "user",
              workspace_id: str | None = None, object_kind: str | None = None,
              object_id: str | None = None, result: str = "ok", meta: dict | None = None,
              workspace_given: bool = False) -> None:
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
             # workspace_given: the caller named the target (possibly none); do not substitute the
             # actor's own session workspace, which would attribute a platform action to it.
             workspace_id if workspace_given else (workspace_id or getattr(getattr(request, "state", None), "workspace_id", None)),
             action, object_kind, object_id,
             result, getattr(getattr(request, "state", None), "request_id", None),
             # Background runners (mail, imports) audit with request=None: no request id, no address.
             (client_ip(request) or None) if request is not None else None,
             _json.dumps(meta or {}, ensure_ascii=False)))

    return Platform(store=store, auth=auth, identity=identity, sessions=sessions,
                    machine=machine, audit=audit)


def new_api(title: str) -> FastAPI:
    show_docs = (env.get("API_DOCS") or "").strip().lower() in ("1", "true", "on", "yes")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # The end of the process (uvicorn's graceful shutdown, a test client leaving its
        # context): every installer appended what it started — worker threads to stop, live
        # documents to write. One hook failing must not skip the others.
        for hook in list(getattr(app.state, "shutdown_hooks", ())):
            try:
                hook()
            except Exception:                                # noqa: BLE001
                logging.getLogger("synwevo.http").exception("shutdown hook %s failed", getattr(hook, "__qualname__", hook))

    api = FastAPI(title=title, version="1.0.0",
                  docs_url="/api/docs" if show_docs else None,
                  redoc_url=None,
                  openapi_url="/api/openapi.json" if show_docs else None,
                  lifespan=lifespan)
    api.state.shutdown_hooks = []
    return api


def install(api: FastAPI, plat: Platform, *, role: str | None = None) -> None:
    """Error contract + the request gate. Every role installs exactly this."""
    store, auth, identity, sessions, machine = plat.store, plat.auth, plat.identity, plat.sessions, plat.machine
    api.state.role = validate_role(role or env.get("ROLE"))
    this_role_static = api.state.role
    # Request rate for machine callers: per credential and per workspace, from the layered policy
    # administrators edit (api_quotas), shared by every process through the store. The deployment
    # defaults (SYNWEVO_API_RATE_PER_MIN, SYNWEVO_API_WORKSPACE_RATE_PER_MIN) are read per request.
    from .api_quotas import RateGate
    rate_gate = RateGate(store)
    api.state.rate_gate = rate_gate

    def _error_body(request: Request, code: str, message: str, detail: dict | None):
        rid = getattr(request.state, "request_id", "")
        if machine_path(request.url.path):
            body = {"error": {"code": code, "message": message, "request_id": rid}}
            if isinstance(detail, dict) and detail.get("field"):
                body["error"]["field"] = detail["field"]
            if detail:
                body["error"]["details"] = {k: v for k, v in detail.items() if k != "field"} or None
                if not body["error"]["details"]:
                    body["error"].pop("details")
            return body
        return {"error": code, "message": message, "detail": detail, "request_id": rid}

    def _error_response(request: Request, status: int, code: str, message: str,
                        detail: dict | None = None, headers: dict | None = None) -> JSONResponse:
        """One error, in whichever format this caller gets.

        /api/v1 callers that send `Accept: application/problem+json` get RFC 9457 problem
        details; everyone else gets the envelope they were written against, byte for byte.
        """
        # The refusal's code travels with the request so the call record can say *why* (a
        # diagnosis by request ID needs "rate_limited" or "queue_full", not only "429").
        request.state.error_code = code
        if machine_path(request.url.path) and wants_problem(request):
            return JSONResponse(problem_body(getattr(request.state, "request_id", ""), status, code,
                                             message, detail),
                                status_code=status, media_type=PROBLEM_JSON, headers=headers)
        return JSONResponse(_error_body(request, code, message, detail), status_code=status,
                            headers=headers)

    def _v1_extra_headers(request: Request) -> dict:
        """Headers every /api/v1 response carries, including ones built outside the gate."""
        out = {"X-Request-ID": getattr(request.state, "request_id", "") or ""}
        decision = getattr(request.state, "ratelimit", None)
        if decision is not None and machine_path(request.url.path):
            out.update(decision.headers())
        return out

    @api.exception_handler(ServiceError)
    async def _svc_err(request: Request, exc: ServiceError):
        resp = _error_response(request, exc.status, exc.code, exc.message, exc.detail)
        wait = retry_after_seconds(exc.detail)
        if wait is not None:
            # Any refusal that knows when to come back says so in the header too — a full job
            # queue, too many sign-in attempts, an invitation limit, sources still parsing — not
            # only the API rate limiter. Clients and proxies read the header, not our body.
            resp.headers["Retry-After"] = str(wait)
        return resp

    @api.exception_handler(ValueError)
    async def _val_err(request: Request, exc: ValueError):
        if isinstance(exc, json.JSONDecodeError):
            # A request body that is not JSON never gets here — FastAPI refuses it first — so
            # this is stored data that failed to parse: the server's fault, not the caller's.
            return await _unexpected(request, exc)
        return _error_response(request, 400, getattr(exc, "code", "invalid"), str(exc), None)

    @api.exception_handler(RequestValidationError)
    async def _validation_err(request: Request, exc: RequestValidationError):
        """Malformed parameters on /api/v1 get the documented 400 envelope (api.md §2), with
        every failing field listed — not FastAPI's bare 422 `{"detail": [...]}`. The browser
        API keeps FastAPI's own shape, which its client already handles."""
        if not machine_path(request.url.path):
            return await request_validation_exception_handler(request, exc)
        errors = []
        for e in exc.errors():
            if e.get("type") == "json_invalid":
                # The body is not JSON at all. FastAPI reports it at ("body", <character
                # offset>), and that offset was being rendered as a field called "1" with the
                # pointer "#/1" — naming a member that does not exist in a document that does
                # not parse. There is no field to point at; say what is wrong instead.
                return _error_response(request, 400, "invalid", "请求体不是合法的 JSON", None)
            # Integer segments stay: inside a parsed body they are array indices, which RFC 6901
            # writes as decimal segments (`#/mappings/0/field`).
            loc = [str(x) for x in (e.get("loc") or ()) if x not in ("body", "query", "path", "header", "cookie")]
            errors.append({"field": ".".join(loc) or None, "loc": loc or None,
                           "message": str(e.get("msg") or "取值不合法")})
        first = errors[0] if errors else {"field": None, "message": "请求参数不合法"}
        where = f"「{first['field']}」" if first.get("field") else ""
        detail = {"field": first.get("field"), "errors": errors} if first.get("field") else {"errors": errors}
        return _error_response(request, 400, "invalid", f"请求参数{where}不合法：{first['message']}", detail)

    @api.exception_handler(StarletteHTTPException)
    async def _http_err(request: Request, exc: StarletteHTTPException):
        if not machine_path(request.url.path):
            return await http_exception_handler(request, exc)
        code, message = {404: ("not_found", "接口不存在"),
                         405: ("method_not_allowed", "该接口不支持这个请求方法")}.get(
                             exc.status_code, (f"http_{exc.status_code}", str(exc.detail or "请求失败")))
        return _error_response(request, exc.status_code, code, message, None,
                               headers=getattr(exc, "headers", None))

    @api.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        """The contract holds for failures nobody anticipated too: a JSON body, the request id
        in body and header, and nothing about the internals — the traceback goes to the log
        under the same id, never to the caller.

        This handler used to print and return nothing, so Starlette answered with a bare 500
        that carried neither the envelope nor X-Request-ID. It runs outside the gate middleware,
        so the headers the gate would add are added here.
        """
        rid = getattr(request.state, "request_id", "")
        print(f"[synwevo] unhandled error request_id={rid} path={request.url.path}\n"
              + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), flush=True)
        resp = _error_response(request, 500, "internal_error",
                               f"服务器内部错误，请稍后重试；如果持续出现，请把请求编号 {rid} 提供给管理员", None)
        resp.headers.update(_v1_extra_headers(request))
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # Reachable without a session, because you cannot sign in from behind a sign-in wall. Kept
    # to an exact set rather than a prefix: "/api/auth" as a prefix would also open anything
    # added under it later, and this list is the whole anonymous surface of the account system.
    ANON_AUTH_PATHS = {"/api/auth/state", "/api/auth/login", "/api/auth/bootstrap", "/api/auth/logout",
                       # A relying party's sign-out: without a local session it revokes nothing and only
                       # hands back the account service's logout URL (public identifiers), so a page
                       # whose application session expired can still end the platform session.
                       "/api/auth/sign-out",
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
                       "/api/auth/verify-email", "/api/auth/email/verify", "/api/auth/register",
                       "/api/auth/deletion/recovery", "/api/auth/deletion/recovery/preview",
                       "/api/auth/email/resend",
                       # A mailed one-time code is the authority for this sign-in; requesting it
                       # cannot need a session, and the answer never says whether an account exists.
                       "/api/auth/login/email/start", "/api/auth/login/email/verify",
                       # Sign in / register through an external OpenID provider: the callback
                       # arrives from the provider, before any session exists.
                       "/api/auth/external/providers",
                       # A verified provider e-mail that matches an existing account: the offer
                       # token identifies the pending link, the password proves the account.
                       "/api/auth/external/offer/preview", "/api/auth/external/offer/confirm"}
    PENDING_PATHS = frozenset({"/api/auth/me", "/api/auth/profile", "/api/auth/verify-email/send"})
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

    def _refuse(request: Request, err: ServiceError) -> JSONResponse:
        return _error_response(request, err.status, err.code, err.message, err.detail)

    def _serves_app() -> bool:
        return this_role_static in ("app", "all")

    def _is_app_path(path: str) -> bool:
        """A business-application route, as opposed to the account system or the portal."""
        return (_serves_app() and path.startswith("/api/") and path not in ACCOUNT_LEVEL_EXACT
                and not path.startswith(ACCOUNT_LEVEL_PREFIXES))

    def _app_route_check(request: Request, path: str, user: dict, wid: str | None,
                         member: dict | None) -> JSONResponse | None:
        """Workspace, application and role, for one Synwevo Docs request. None means go ahead.

        Order matters for the answer someone sees: a frozen workspace is about the workspace, a
        revoked entitlement is about them, and a viewer pressing "save" is about their role —
        three different people to ask, so three different codes.
        """
        policy_fn = getattr(api.state, "route_policy", None)
        method = request.method.upper()
        policy = policy_fn(method, path) if policy_fn else (None if method in SAFE_METHODS else "write")
        if not wid:
            if policy:
                return _refuse(request, ServiceError(
                    403, "no_workspace", "你当前没有可用的工作空间，不能进行这个操作。请先选择或加入一个工作空间。"))
            return None                                  # reads see nothing; see the tenant below
        err = app_access_refusal(store, identity, user["id"], wid, getattr(api.state, "app_id", None))
        if err:
            return _refuse(request, err)
        role = member["role"]
        can_export = bool(member.get("can_export"))
        request.state.workspace_role = role
        request.state.can_export = can_export
        if policy == "write" and role not in WRITE_ROLES:
            return _refuse(request, ServiceError(
                403, "read_only_role", "你在该工作空间是查看者，只能查看，不能修改内容或运行任务。需要编辑请联系工作空间管理员。",
                {"workspace_id": wid, "role": role}))
        if policy == "export" and not (can_export or role in WRITE_ROLES):
            return _refuse(request, ServiceError(
                403, "export_not_allowed", "你在该工作空间没有导出权限，不能把文件下载到平台之外。需要导出请联系工作空间管理员开通。",
                {"workspace_id": wid, "role": role}))
        return None

    def _session_gate(request: Request, path: str):
        """Resolve the session cookie into an identity, or refuse.

        Returns the tenant this request acts in, or a JSONResponse to return as-is. Runs in a
        worker thread (it reads and sometimes writes the database), so it sets no context
        variables itself; the caller sets the tenant on the event loop.

        The workspace comes from the session row and is re-checked against live membership on
        every request, so a workspace someone has been removed from stops working immediately
        rather than at the end of their session. For Synwevo Docs' own routes the workspace's
        status, the application's availability and the person's entitlement are re-checked too,
        and the route's role requirement is enforced (`api.state.route_policy`).
        """
        from .sessions import (COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, LEGACY_CSRF_HEADER,
                               csrf_ok)
        if any(path.startswith(p) for p in SPA_PREFIXES):
            # Hashed static files carry no data and need no identity. Loading the session here
            # meant a database read and an idle-clock write for every script and stylesheet.
            request.state.session = None
            return "__anonymous__"
        session = sessions.load(request.cookies.get(COOKIE_NAME))
        request.state.session = session
        if path == "/admin" or path.startswith("/admin/"):
            user = identity.get_user(session["user_id"]) if session else None
            from .platform_access import PlatformAccess
            if not user or user["status"] != "active" or not PlatformAccess(store).effective_permissions(user):
                return _error_response(request, 404, "not_found", "页面不存在", None)
            # The shell itself is served to anyone holding a platform permission; which views
            # they may use, and whether this *session* may use them at all, is decided per
            # request by the admin routes (platform_access.require_platform).

        if session and request.method not in SAFE_METHODS and path.startswith("/api/"):
            # Double-submit CSRF on every state-changing request. Login is exempt because it has
            # no session yet; it is protected by being useless without valid credentials.
            sent = request.headers.get(CSRF_HEADER) or request.headers.get(LEGACY_CSRF_HEADER)
            session_mutations = {"/api/auth/logout", "/api/auth/sign-out", "/api/auth/invitations/accept", "/api/auth/email/verify", "/api/auth/verify-email"}
            if (path not in ANON_AUTH_PATHS or path in session_mutations) and not csrf_ok(request.cookies.get(CSRF_COOKIE), sent):
                return _error_response(request, 403, "csrf_failed", "请求校验失败，请刷新页面后重试", None)

        if path.startswith(SUITE_PUBLIC_PREFIX):
            # A public form or a fill-in link: no session needed, but someone who is signed in is
            # known to the handler (a personal request can require its addressee to sign in).
            user = identity.get_user(session["user_id"]) if session else None
            request.state.user = user if user and user["status"] == "active" else None
            return "__anonymous__"

        if path in ANON_AUTH_PATHS or not path.startswith("/api/"):
            # The SPA shell renders for anyone; it fetches nothing until a session exists. The
            # fallback is deliberately an id no row carries rather than UI_TENANT — see below.
            return (session or {}).get("workspace_id") or "__anonymous__"

        if not session:
            return _error_response(request, 401, "unauthorized", "请先登录", None)

        user = identity.get_user(session["user_id"])
        if user and user["status"] == "pending":
            request.state.user = user
            if path in PENDING_PATHS and (request.method in SAFE_METHODS or path == "/api/auth/verify-email/send"):
                return f"__pending__{user['id']}"
            return _error_response(request, 403, "email_verification_required", "请先验证邮箱再继续", None)
        if not user or user["status"] != "active":
            # Suspension and deletion take effect on the next request, not at session expiry.
            sessions.revoke(session["id"], f"user_{user['status'] if user else 'deleted'}")
            request.state.session = None
            return _error_response(request, 401, "unauthorized", "账户不可用，请重新登录", None)
        request.state.user = user
        if path == "/api/admin" or path.startswith("/api/admin/"):
            # Decided before routing, so an account without any platform permission meets the
            # same 404 everywhere under /api/admin — not a 422 from parameter or body validation
            # (which ran before the route's own check and echoed, for one, the role catalogue in
            # a pattern), and not a 405. The routes decide each permission on their own.
            from .platform_access import PlatformAccess
            if not PlatformAccess(store).effective_permissions(user):
                return _error_response(request, 404, "not_found", "页面不存在", None)

        requested_workspace = request.headers.get("X-Synwevo-Workspace")
        wid = requested_workspace or session.get("workspace_id")
        member = None
        if wid:
            # Membership is authoritative every time; the session only remembers a preference.
            member = identity.membership(wid, user["id"])
            if requested_workspace:
                from .organizations import workspace_access_failure
                target_workspace = identity.get_workspace(wid)
                if (not target_workspace or target_workspace["status"] != "active"
                        or workspace_access_failure(store, wid, user["id"])):
                    return _error_response(request, 403, "workspace_context_invalid", "此标签页的工作空间已不可用，请重新选择", None)
            if not member:
                if requested_workspace:
                    return _error_response(request, 403, "workspace_context_invalid", "此标签页的工作空间已不可用，请重新选择", None)
                sessions.set_workspace(session["id"], None)
                wid = None
                request.state.session = {**session, "workspace_id": None}
            elif requested_workspace:
                # Per-request context never changes another tab's session preference.
                request.state.session = {**session, "workspace_id": wid}
        request.state.workspace_id = wid
        request.state.membership = member

        if _is_app_path(path):
            refused = _app_route_check(request, path, user, wid, member)
            if refused is not None:
                return refused
        # With no workspace selected the tenant becomes an id no row can ever carry, so the
        # person sees nothing. Falling back to UI_TENANT here would be a disaster: UI_TENANT is
        # "default", which is exactly the legacy workspace holding every pre-identity file — so
        # the fallback would hand the historical data to every signed-in account.
        return wid or f"__no_workspace__{user['id']}"

    # Paths the account service owns. Everything else under /api/ is Synwevo Docs' own workbench.
    # `/auth/external/` is the account service's outbound sign-in through an external OpenID
    # provider — account-owned, unlike `/auth/start` and `/auth/callback`, which are the
    # relying-party side that every application serves for itself.
    ACCOUNT_PATHS = ("/api/auth/", "/api/admin/", "/api/workspace/", "/oidc/", "/.well-known/", "/auth/external/")
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
                         "/api/auth/workspace", "/api/auth/sign-out", "/api/auth/locale",
                         # The platform inbox is one list read from every origin (session_routes).
                         "/api/auth/notifications", "/api/auth/notifications/unread-count",
                         "/api/auth/notifications/mark", "/api/auth/notifications/read-all",
                         "/api/auth/notifications/preferences",
                         # The messages badge in the platform bar; the conversations themselves
                         # live on the account origin.
                         "/api/auth/messaging/unread-count",
                         # The platform bar's application switcher: which applications this
                         # person can open (the same query as the workspace's cards).
                         "/api/auth/apps")

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
        if path.startswith(MESSAGING_PREFIX) and this_role != "messages":
            # Conversations have one write source: the messages application. Named here so the
            # refusal says where the interface lives rather than only that it is not here.
            where = _messages_origin_cached()
            return f"消息接口在 Synwevo Message 上（{where}），不在本服务上" if where else "消息接口在 Synwevo Message 应用上，不在本服务上"
        if this_role == "messages":
            # Its own API, the relying-party half, and the handful of session routes every
            # application serves about its own session (the badge's unread-count among them).
            # The rest of the account surface — including the account service's former
            # /api/auth/messaging/* routes — is refused: the application must not become a second
            # place to change a password or a second write path into conversations.
            if path.startswith(MESSAGING_PREFIX) or path.startswith("/auth/") or path in APP_ACCOUNT_PATHS:
                return None
            if is_account:
                if path.startswith(("/oidc/", "/.well-known/")):
                    return "该接口在账户服务上，请访问账户服务地址"
                return "账户、安全与平台管理在账户服务上统一管理，请访问账户服务地址"
            if path.startswith("/api/portal/"):
                return "门户接口在平台门户上，不在消息应用上"
            return "该接口不在消息应用上"
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
        if path.startswith(SUITE_PREFIX) and this_role != "portal":
            return "表单、数据表、数据整理、审批、信息提取与个人 Agent 在工作空间源站上，不在本服务上"
        if this_role == "portal" and not is_account and not path.startswith("/auth/") \
                and not path.startswith(("/api/portal/", SUITE_PREFIX)):
            return "该接口不在平台门户上"
        return None

    _messages_origin_memo: list = [0.0, None]

    def _messages_origin_cached() -> str | None:
        """The messages application's origin from the registry, for the refusal text above.
        Cached for a minute: this runs inside the gate, for requests that are being refused."""
        if time.monotonic() - _messages_origin_memo[0] > 60:
            try:
                from .apps import messages_origin
                _messages_origin_memo[1] = messages_origin(store)
            except Exception:  # noqa: BLE001 - the refusal must not depend on the registry read
                _messages_origin_memo[1] = None
            _messages_origin_memo[0] = time.monotonic()
        return _messages_origin_memo[1]

    # A WebSocket handshake is not an HTTP request to the middleware below, but it is the same
    # session, workspace and role decision: the Docs live-editing route runs this on a request
    # built from the handshake scope, so a socket never gets a looser gate than a request.
    api.state.session_gate = _session_gate

    @api.middleware("http")
    async def _gate(request: Request, call_next):
        started = time.perf_counter()
        rid = (request.headers.get("X-Request-ID") or "")[:64] or secrets.token_hex(8)
        request.state.request_id = "".join(ch for ch in rid if ch.isalnum() or ch in "-_") or secrets.token_hex(8)
        request.state.ratelimit = None
        request.state.error_code = None
        path = request.url.path
        token = None

        async def finish(resp):
            """Every response – including the ones returned before the route runs – carries
            the trace id and the standard headers (docs/integration/api.md)."""
            resp.headers["X-Request-ID"] = request.state.request_id
            apply_security_headers(request, resp)
            if path.startswith("/api/"):
                resp.headers.setdefault("Cache-Control", "no-store")
            if path.startswith(("/api/", "/auth/", "/oidc/", "/oauth/", "/account", "/admin", "/organizations", "/messages", "/c/")) or path in (
                    "/login", "/register", "/invite", "/verify-email", "/forgot-password", "/reset-password", "/onboarding", "/logged-out"):
                resp.headers.setdefault("Cache-Control", "no-store")
                resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
            if request.state.ratelimit is not None and machine_path(path):
                # On every response to an authenticated /api/v1 call, success or not, so a
                # client can pace itself before it is refused rather than only after.
                for k, v in request.state.ratelimit.headers().items():
                    resp.headers[k] = v
            if machine_path(path):
                from .api_platform import record_call
                matched = getattr(request.scope.get("route"), "path", "") or _route_template(request)
                prefix = next((p for p in (DIRECTORY_V1, SUITE_V1) if path.startswith(p)), "/api/v1")
                template = matched if matched.startswith(prefix + "/") else prefix + "/<unmatched>"
                try:
                    await run_in_threadpool(record_call, store, getattr(request.state, "principal", None),
                                            request.method, template, resp.status_code,
                                            (time.perf_counter() - started) * 1000, request.state.request_id,
                                            request.state.error_code if resp.status_code >= 400 else None)
                except Exception:
                    # Never change a completed business response or expose payloads in telemetry.
                    import logging
                    logging.getLogger(__name__).warning("API call metadata unavailable request_id=%s",
                                                        request.state.request_id)
            return resp

        try:
            off = _role_blocks(path)
            if off:
                # One binary, three roles, decided by configuration. Enforced here rather than by
                # conditionally mounting routers: a filter in one place cannot be half-applied,
                # and it means the account service genuinely cannot serve a document — an
                # accounts-origin cookie reaching Synwevo Docs' data would defeat the whole point
                # of giving them separate origins.
                return await finish(_error_response(request, 404, "not_in_this_role", off, None))
            if path.startswith(DIRECTORY_V1):
                # The workspace directory (API-5): only its own service accounts, only its own
                # audience; the same rate gate and call record as the Docs API.
                principal = await run_in_threadpool(machine.verify_workspace, request.headers.get("Authorization"))
                request.state.principal = principal
                decision = await run_in_threadpool(rate_gate.take, principal)
                request.state.ratelimit = decision
                if not decision.allowed:
                    lim = decision.refused_by
                    raise ServiceError(429, "rate_limited", f"请求过于频繁（限制 {lim.cap} 次/分钟）",
                                       {"retry_after": decision.retry_after, "limit_scope": "credential" if lim.name == "key" else "workspace",
                                        "limit": lim.cap, "window_s": 60, "policy_source": lim.source})
                token = set_tenant(principal["tenant"])
            elif path.startswith(SUITE_V1):
                # The business applications' operations: a bearer token for their own audience and
                # nothing else — no session cookie is read on this branch, so a signed-in browser
                # is not a credential here and a credential is not a session anywhere else.
                principal = await run_in_threadpool(machine.verify_suite, request.headers.get("Authorization"))
                request.state.principal = principal
                decision = await run_in_threadpool(rate_gate.take, principal)
                request.state.ratelimit = decision
                if not decision.allowed:
                    lim = decision.refused_by
                    scope = "credential" if lim.name == "key" else "workspace"
                    where = "此凭据" if scope == "credential" else "本工作空间的全部机器凭据合计"
                    raise ServiceError(429, "rate_limited", f"请求过于频繁：{where}限制 {lim.cap} 次/分钟",
                                       {"retry_after": decision.retry_after, "limit_scope": scope,
                                        "limit": lim.cap, "window_s": 60, "policy_source": lim.source})
                token = set_tenant(principal["tenant"])
            elif path.startswith("/api/v1") and not any(path.startswith(p) for p in PUBLIC_V1):
                # Credential checks read (and occasionally write) the database, so they run in
                # a worker thread rather than blocking the event loop for every other request.
                principal = await run_in_threadpool(machine.verify, request.headers.get("Authorization"))
                if not principal.get("tenant"):
                    # Defence in depth: an empty tenant would otherwise become the legacy
                    # workspace through set_tenant's fallback.
                    raise ServiceError(403, "no_workspace", "该凭据没有绑定工作空间，不能调用业务接口")
                request.state.principal = principal
                decision = await run_in_threadpool(rate_gate.take, principal)
                request.state.ratelimit = decision
                if not decision.allowed:
                    lim = decision.refused_by
                    scope = "credential" if lim.name == "key" else "workspace"
                    where = "此凭据" if scope == "credential" else "本工作空间的全部机器凭据合计"
                    raise ServiceError(429, "rate_limited", f"请求过于频繁：{where}限制 {lim.cap} 次/分钟",
                                       {"retry_after": decision.retry_after, "limit_scope": scope,
                                        "limit": lim.cap, "window_s": 60, "policy_source": lim.source})
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
                    outcome = await run_in_threadpool(_session_gate, request, path)
                    if isinstance(outcome, JSONResponse):
                        return await finish(outcome)
                    token = set_tenant(outcome)
                elif mode == "basic" and not path.startswith("/api/v1"):
                    # PBKDF2 is deliberately slow; on the event loop it stalled every other
                    # request for the length of each check.
                    if not await run_in_threadpool(auth.check_ui_basic, request.headers.get("Authorization")):
                        return await finish(_error_response(request, 401, "unauthorized", "需要登录", None,
                                                      headers={"WWW-Authenticate": 'Basic realm="Synwevo Docs"'}))
                    token = set_tenant(UI_TENANT)
                else:
                    token = set_tenant(UI_TENANT)
        except ServiceError as e:
            return await finish(await _svc_err(request, e))
        # Who acts, for attribution: the session's person, or the person a delegated machine
        # token acts for; None for service accounts and anonymous paths.
        from .context import reset_actor, reset_request_id, set_actor, set_request_id
        _user = getattr(request.state, "user", None)
        _principal = getattr(request.state, "principal", None)
        actor_token = set_actor((_user or {}).get("id") or (_principal or {}).get("user_id"))
        rid_token = set_request_id(request.state.request_id)
        try:
            resp = await call_next(request)
        except Exception as exc:
            resp = await _unexpected(request, exc)
        finally:
            reset_request_id(rid_token)
            reset_actor(actor_token)
            if token is not None:
                try:
                    from .context import _tenant
                    _tenant.reset(token)
                except (ValueError, LookupError):
                    pass
        return await finish(resp)


def install_probes(api: FastAPI, store: Store, worker: Callable[[], bool] | None = None,
                   degradable: dict[str, Callable[[], str]] | None = None) -> None:
    """Liveness and readiness, identical in every role.

    ``worker`` is what this role's background work is: Synwevo Docs' job runner, the account
    service's mail sender, or nothing at all. A role with no worker reports none rather than
    reporting somebody else's.

    ``degradable`` are components whose outage must be *visible* in ``checks`` but must not take
    the whole role out of rotation: Synwevo Docs' malware scanner is one — when it is down, new
    uploads stay quarantined and are retried, while everything already accepted keeps working,
    so restarting the container (what an unhealthy readiness does under compose) would only add
    an outage. Each callable returns ``"ok"`` or a short reason; anything else is reported as is.
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
        for name, probe in (degradable or {}).items():
            try:
                checks[name] = probe()
            except Exception as e:  # noqa: BLE001
                checks[name] = f"error: {type(e).__name__}"
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
        # Raised rather than returned so the error handlers render it: the right envelope for
        # /api/v1 (or problem details when asked for), and the request id in the body.
        raise ServiceError(404, "not_found", "接口不存在")
    target = (dist / full_path).resolve()
    if full_path and full_path != "index.html" and target.is_file() and dist.resolve() in target.parents:
        # Root files of a build (favicon.ico) keep their name across releases, so a browser or
        # edge cache must revalidate them; fingerprinted assets are cached by HashedAssets.
        return FileResponse(target, headers={"Cache-Control": "no-cache"})
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache, must-revalidate"})
