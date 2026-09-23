"""账户服务进程：`DOCWEAVE_ROLE=accounts` 时运行的那一个。

登录、账户与安全、平台管理、OIDC 授权服务器，以及账户外壳。没有工作台，没有门户首页——
它们在各自的源站上；一个共用外壳会让账户服务渲染出一份它自己拒绝加载的项目列表。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from platform_auth_client.session_routes import build_router as build_session_router
from platform_core.apps import Apps, bootstrap_docweave
from platform_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)

ASSETS_DIR = "account-assets"


def web_dist() -> Path:
    """The account shell's built output, beside its own source."""
    return Path(os.environ.get("PLATFORM_ACCOUNTS_UI_DIR")
                or Path(__file__).resolve().parents[1] / "web" / "dist")


def install(api: FastAPI, plat: Platform, *, start_jobs: bool = True) -> None:
    """Mount the account service's routes onto an application the caller has scaffolded."""
    from .admin_routes import build_router as build_admin_router
    from .auth_routes import (build_invite_router, build_mfa_router, build_passkey_router,
                              build_recovery_router, build_router as build_auth_router)
    from .invites import Invites
    from .mail import Mailer
    from .mfa import Mfa, Tickets
    from .passkeys import Passkeys
    from .provider_routes import build_router as build_oidc_router

    store, identity, sessions, audit = plat.store, plat.identity, plat.sessions, plat.audit
    invites = Invites(store, identity)
    mailer = Mailer(store, start=start_jobs)
    mfa = Mfa(store)
    tickets = Tickets(store)
    passkeys = Passkeys(store)
    api.state.invites = invites
    api.state.mailer = mailer
    api.state.mfa = mfa
    api.state.passkeys = passkeys

    api.include_router(build_auth_router(identity, sessions, audit, mfa, tickets))
    api.include_router(build_invite_router(identity, sessions, invites, mailer, audit))
    api.include_router(build_mfa_router(identity, sessions, mfa, audit))
    api.include_router(build_recovery_router(identity, sessions, invites, mailer, mfa, audit))
    api.include_router(build_passkey_router(identity, sessions, passkeys, audit))

    # Registered before any SPA catch-all so /oidc/* and /.well-known/* reach real handlers
    # instead of being served the shell.
    bootstrap_docweave(store, identity)
    Apps(store, identity).adopt_existing_members()
    api.include_router(build_oidc_router(store, identity, sessions, audit))
    api.include_router(build_admin_router(store, identity, sessions, audit))


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    plat = build_platform(data_dir)
    api = new_api("Platform accounts")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=os.environ.get("DOCWEAVE_ROLE") or "accounts")
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    install(api, plat, start_jobs=start_jobs)
    install_probes(api, plat.store, worker=lambda: bool(api.state.mailer and api.state.mailer.alive()))

    dist = web_dist()
    if mount_web(api, dist, assets_dir=ASSETS_DIR):
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)

    return api
