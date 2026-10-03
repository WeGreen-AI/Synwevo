"""The business applications of the workplace origin, as one installable module.

`synwevo_server` mounts this in the `portal` role (https://workplace.synwevo.com) and in `all`
(docs/agent-work/DECISIONS.md D1). Each application is a subpackage with its own records, routes,
operations and pages; they never import one another and meet only in the capability registry
that :func:`install` builds for this process.

Which applications are live is the deployment's decision: ``SYNWEVO_SUITE_APPS`` lists them
(``all`` for every one). An application that is not listed is not registered — its operations do
not exist here, it has no card, no switcher entry and no Agent tools — and its registry row is
marked disabled (DECISIONS.md D4).
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from synwevo_core import env
from synwevo_core.capabilities import Capabilities, Registry

ASSETS_DIR = "suite-assets"

#: Application subpackages, in the order they are installed. The personal Agent comes last so its
#: tool discovery sees every other application's operations.
APP_MODULES = ("forms", "tables", "dataprep", "approvals", "extract", "agent")

#: The shell owns these paths on the workplace origin (and on `all`'s single origin).
SHELL_PATHS = ("/forms", "/tables", "/dataprep", "/approvals", "/extract", "/agent")


def web_dist() -> Path:
    return Path(os.environ.get("SYNWEVO_SUITE_UI_DIR")
                or Path(__file__).resolve().parents[1] / "web" / "dist")


def released_apps() -> frozenset[str]:
    """``SYNWEVO_SUITE_APPS``: comma-separated application ids, or ``all``. Unset means none —
    a deployment that has not decided releases nothing."""
    raw = (env.get("SUITE_APPS") or "").strip().lower()
    if raw in ("all", "*"):
        return frozenset(APP_MODULES)
    return frozenset(a.strip() for a in raw.split(",") if a.strip()) & frozenset(APP_MODULES)


@dataclass
class SuiteContext:
    """What an application subpackage gets at install time."""
    api: FastAPI
    plat: Any
    capabilities: Capabilities
    registry: Registry
    start_jobs: bool
    #: Injected by the composition root: Synwevo Docs' reading and generation capabilities
    #: (DECISIONS.md D3). None when this process was built without Docs.
    docs: Any = None

    @property
    def store(self):
        return self.plat.store

    @property
    def identity(self):
        return self.plat.identity


def install(api: FastAPI, plat, *, docs: Any = None, start_jobs: bool = True,
            released: frozenset[str] | None = None) -> SuiteContext:
    from synwevo_core.apps import bootstrap_suite_app
    registry = Registry()
    caps = Capabilities(plat.store, plat.identity, registry)
    api.state.capabilities = caps
    ctx = SuiteContext(api=api, plat=plat, capabilities=caps, registry=registry,
                       start_jobs=start_jobs, docs=docs)
    api.state.suite = ctx
    live = released_apps() if released is None else released
    base = (env.get("APP_URL") or "").rstrip("/")
    for name in APP_MODULES:
        try:
            module = importlib.import_module(f"synwevo_suite.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"synwevo_suite.{name}":
                # Not built yet in this tree: never registered, whatever the switch says.
                bootstrap_suite_app(plat.store, plat.identity, app_id=name, name=name, icon=name,
                                    launch_url=None, released=False)
                continue
            raise
        manifest = module.MANIFEST
        on = manifest.id in live
        bootstrap_suite_app(plat.store, plat.identity, app_id=manifest.id, name=manifest.name,
                            icon=manifest.icon, launch_url=(base + manifest.path) if base else None,
                            released=on)
        if on:
            registry.register_app(manifest)
            module.install(ctx)
    if docs is not None and hasattr(docs, "register"):
        docs.register(ctx)
    if registry.app("agent") is not None:
        from .flows import install as install_flows
        install_flows(ctx)
    from .common.machine import build_machine_router
    from .common.routes import build_router
    # After every application (and the injected adapters) registered: the machine API gets one
    # route per operation that exists in this process, and none for one that does not.
    api.include_router(build_machine_router(ctx))
    api.include_router(build_router(ctx))
    return ctx


def create_app(data_dir: str | None = None, start_jobs: bool = True,
               released: frozenset[str] | None = None) -> FastAPI:
    """The business applications on their own, as the `portal` role serves them: the platform
    gate, the session routes, the relying party, the applications and their shell — without the
    portal's own pages. For this module's tests and for running the applications alone."""
    from synwevo_auth_client.routes import (build_account_redirect_router,
                                             build_router as build_rp_router)
    from synwevo_auth_client.session_routes import build_router as build_session_router
    from synwevo_core.http import (build_platform, install as install_platform, install_probes,
                                    mount_web, new_api, shell_response)
    plat = build_platform(data_dir)
    api = new_api("Synwevo Workspace")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=env.get("ROLE") or "portal")
    install_probes(api, plat.store)
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    install(api, plat, start_jobs=start_jobs, released=released)
    api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
    api.include_router(build_account_redirect_router())
    dist = web_dist()
    if mount_web(api, dist, assets_dir=ASSETS_DIR):
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)
    return api


def owns_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in SHELL_PATHS)
