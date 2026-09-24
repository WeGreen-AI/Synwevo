"""把角色变成一个 ASGI 应用。"""
from __future__ import annotations

from fastapi import FastAPI

from platform_core.http import (build_platform, install as install_platform, install_probes,
                                mount_web, new_api, role as current_role, shell_response)

ACCOUNT_SHELL_PATHS = ("/login", "/account", "/invite", "/forgot-password", "/reset-password",
                       "/logged-out", "/admin")


def create_app(data_dir: str | None = None, *, role: str | None = None,
               start_jobs: bool = True) -> FastAPI:
    """Compose the roles this process serves.

    `all` is the single-process deployment the container image runs: the workbench and the
    account pages on one origin. It is **not** three origins pretending — cookies are scoped by
    host, so one origin means one cookie jar, and that is exactly why the split deployment uses
    three hostnames instead of three ports.
    """
    role = (role or current_role()).strip().lower() or "all"
    plat = build_platform(data_dir)
    api = new_api({"accounts": "Platform accounts", "portal": "Platform portal"}.get(role, "DocWeave"))
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=role)

    wants_app = role in ("app", "all")
    wants_accounts = role in ("accounts", "all")
    wants_portal = role == "portal"

    def worker_alive() -> bool:
        jobs = getattr(api.state, "jobs", None)
        mailer = getattr(api.state, "mailer", None)
        return ((jobs.runner_alive() if jobs else True)
                and (mailer.alive() if mailer else True))

    # Probes first: they are the same in every role, and the published OpenAPI document lists
    # them where it always has.
    install_probes(api, plat.store, worker=worker_alive if (wants_app or wants_accounts) else None)

    # Who is signed in, which workspace, how to leave. The same four routes in every role, from
    # one implementation — the account service does not get its own copy and neither does an app.
    from platform_auth_client.session_routes import build_router as build_session_router
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))

    if wants_app:
        from docweave.api import app as docweave_app
        docweave_app.install(api, plat, start_jobs=start_jobs)
    if wants_accounts:
        from platform_identity import app as identity_app
        identity_app.install(api, plat, start_jobs=start_jobs)
    if wants_portal:
        from platform_portal import app as portal_app
        portal_app.install(api, plat)

    # The relying-party half is mounted everywhere the account service is not the whole story.
    # It stays on `all` too: a single-process install can be configured against an external
    # issuer, and the chain tests drive two origins out of one composed app. Where there is no
    # external issuer these routes simply point at this process's own provider.
    if role in ("app", "portal", "all"):
        from platform_auth_client.routes import (build_account_redirect_router,
                                                 build_router as build_rp_router)
        api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
        if role != "all":
            # On `all` the account service is in this same process and serves these
            # paths itself, so redirecting them would be a loop back to ourselves.
            api.include_router(build_account_redirect_router())

    _mount_shells(api, role)
    return api


def _mount_shells(api: FastAPI, role: str) -> None:
    """Serve the built shells this role owns.

    Each shell is a separate build with its own asset directory, so a process serving two of
    them (`all`) mounts both without one build's `/assets` hiding the other's. That separation
    is what lets the account and security pages exist exactly once: the workbench no longer
    carries a second copy of the sign-in screen for the single-process case — it navigates to
    the account shell, on the same origin, with the same cookie.
    """
    from docweave.api.app import web_dist as docweave_dist
    from platform_identity.app import ASSETS_DIR as ACCOUNT_ASSETS, web_dist as accounts_dist
    from platform_portal.app import ASSETS_DIR as PORTAL_ASSETS, web_dist as portal_dist

    if role == "portal":
        dist = portal_dist()
        if mount_web(api, dist, assets_dir=PORTAL_ASSETS):
            @api.get("/{full_path:path}")
            def portal_shell(full_path: str):
                return shell_response(dist, full_path)
        return

    if role == "accounts":
        dist = accounts_dist()
        if mount_web(api, dist, assets_dir=ACCOUNT_ASSETS):
            @api.get("/{full_path:path}")
            def account_shell(full_path: str):
                return shell_response(dist, full_path)
        return

    # `app` and `all`
    work = docweave_dist()
    work_ok = mount_web(api, work)
    acct = accounts_dist()
    acct_ok = role == "all" and mount_web(api, acct, assets_dir=ACCOUNT_ASSETS)

    if work_ok or acct_ok:
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            path = "/" + full_path
            if acct_ok and any(path == p or path.startswith(p + "/") for p in ACCOUNT_SHELL_PATHS):
                return shell_response(acct, full_path)
            if not work_ok:
                return shell_response(acct, full_path)
            return shell_response(work, full_path)
