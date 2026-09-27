"""消息应用进程：`SYNWEVO_ROLE=messages` 时运行的那一个。

Synwevo Message 是平台上的一个应用，有自己的源站，是账户服务的依赖方：它自己不登录任何人，
`/auth/start` 把浏览器送去账户服务，回来时铸造本源自己的会话 Cookie。会话数据（表、规则、
授权）在 `synwevo_core.messaging`；这里只有 HTTP 与外壳。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, FastAPI

from synwevo_core import env
from synwevo_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)
from synwevo_core.notifications import Notifier

ASSETS_DIR = "messages-assets"
#: The paths the messages shell owns when it shares an origin with the other shells (role `all`):
#: the conversation list and the thread deep links. On its own origin it serves everything.
SHELL_PATHS = ("/messages", "/c")


def web_dist() -> Path:
    return Path(os.environ.get("SYNWEVO_MESSAGES_UI_DIR")
                or Path(__file__).resolve().parents[1] / "web" / "dist")


def install(api: FastAPI, plat: Platform, *, notifier: Notifier | None = None) -> APIRouter:
    """Mount the messaging API under `/api/messaging` on an application the caller scaffolded.

    Returns the router: its `hub` (the in-process event fan-out) and `messaging` (the service)
    are what the composer and the tests reach for. `api.state.notifier` is created here when the
    caller did not bring one (the account service brings its own, with the mail dispatcher).
    """
    from .routes import build_router
    if notifier is None:
        notifier = getattr(api.state, "notifier", None) or Notifier(plat.store)
        api.state.notifier = notifier
    router = build_router(plat.store, plat.identity, plat.sessions, plat.audit, notifier=notifier)
    api.state.message_hub = router.hub
    api.state.messaging = router.messaging
    api.include_router(router)
    return router


def install_legacy(api: FastAPI, plat: Platform, *, notifier: Notifier | None = None) -> APIRouter:
    """The account origin's former `/api/auth/messaging/…` routes, for a deployment **without** the
    messages application.

    Mounted by the composer on the `accounts` role only (this package does not know the account
    service, and the account service does not know this package). Every route carries a guard:
    the moment the registry says Synwevo Message is active, they answer 410 naming its address,
    so conversations never have two write sources — two processes would each hold their own
    event fan-out, and a message sent through one would not reach tabs streaming from the other.
    """
    from .routes import LEGACY_PREFIX, build_router
    if notifier is None:
        notifier = getattr(api.state, "notifier", None) or Notifier(plat.store)
        api.state.notifier = notifier
    router = build_router(plat.store, plat.identity, plat.sessions, plat.audit, notifier=notifier,
                          prefix=LEGACY_PREFIX, legacy_guard=True)
    api.state.message_hub = router.hub
    api.include_router(router)
    return router


def scanner_health() -> str:
    """Attachments go through the same acceptance chain as Docs uploads (`accept_upload`), so the
    readiness probe reports the scanner the same way: `ok` / `off` / `down: <why>`, never gating."""
    from synwevo_core import malware as M
    h = M.health(M.policy())
    return h["status"] if h["status"] in ("ok", "off") else "down: " + (h.get("error") or "no PONG")[:120]


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    """Synwevo Message on its own: its API, its single-sign-on client, and its shell.

    It starts and answers with the portal and Synwevo Docs stopped. The process that *is* the
    application registers it (`bootstrap_messages_app`), which is how the other origins learn
    its address; nothing else writes that row.
    """
    from synwevo_auth_client.routes import (build_account_redirect_router,
                                             build_router as build_rp_router)
    from synwevo_auth_client.session_routes import build_router as build_session_router
    from synwevo_core.apps import bootstrap_messages_app
    plat = build_platform(data_dir)
    api = new_api("Synwevo Message")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    role = env.get("ROLE") or "messages"
    install_platform(api, plat, role=role)
    install_probes(api, plat.store, degradable={"malware_scanner": scanner_health})
    install(api, plat)
    bootstrap_messages_app(plat.store, plat.identity, role=role)
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
    api.include_router(build_account_redirect_router())

    dist = web_dist()
    if mount_web(api, dist, assets_dir=ASSETS_DIR):
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)

    return api
