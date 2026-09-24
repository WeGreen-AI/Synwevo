"""门户进程：`SYNWEVO_ROLE=portal` 时运行的那一个。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from synwevo_core import env
from synwevo_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)

ASSETS_DIR = "portal-assets"


def web_dist() -> Path:
    return Path(os.environ.get("SYNWEVO_PORTAL_UI_DIR")
                or Path(__file__).resolve().parents[1] / "web" / "dist")


def install(api: FastAPI, plat: Platform) -> None:
    from .routes import build_router
    api.include_router(build_router(plat.store, plat.identity, plat.sessions, plat.audit))


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    """The portal on its own: its API, its single-sign-on client, and its shell.

    It starts and answers with DocWeave stopped — the application cards report status from the
    registry, which is the point of having a portal rather than a link list.
    """
    from synwevo_auth_client.routes import (build_account_redirect_router,
                                             build_router as build_rp_router)
    from synwevo_auth_client.session_routes import build_router as build_session_router
    plat = build_platform(data_dir)
    api = new_api("Synwevo Workspace")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=env.get("ROLE") or "portal")
    install(api, plat)
    install_probes(api, plat.store)
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
    api.include_router(build_account_redirect_router())

    dist = web_dist()
    if mount_web(api, dist, assets_dir=ASSETS_DIR):
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)

    return api
