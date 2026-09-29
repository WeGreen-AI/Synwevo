"""把角色变成一个 ASGI 应用。"""
from __future__ import annotations

from fastapi import FastAPI

from synwevo_core.errors import ServiceError

from synwevo_core.http import (build_platform, install as install_platform, install_probes,
                                mount_web, new_api, role as current_role, shell_response,
                                validate_role)

ACCOUNT_SHELL_PATHS = ("/login", "/register", "/verify-email", "/onboarding", "/organizations",
                       "/account", "/invite", "/forgot-password", "/reset-password",
                       "/logged-out", "/admin", "/oauth")
#: On `all` the messages shell shares the origin with the workbench and the account pages: it owns
#: the conversation list (`/messages`, the address it had on the account origin) and the thread
#: deep links (`/c/<cid>`, the address it has on its own origin) and the object entry
#: (`/objects/{app}/{kind}/{id}`, the address Docs' 「在消息中讨论」 sends people to; CI-75).
MESSAGES_SHELL_PATHS = ("/messages", "/c", "/objects")

TITLES = {"accounts": "Synwevo Account", "portal": "Synwevo Workspace", "messages": "Synwevo Message"}


def create_app(data_dir: str | None = None, *, role: str | None = None,
               start_jobs: bool = True) -> FastAPI:
    """Compose the roles this process serves.

    `all` is the single-process deployment the container image runs: the workbench, the account
    pages and the messages application on one origin. It is **not** four origins pretending —
    cookies are scoped by host, so one origin means one cookie jar, and that is exactly why the
    split deployment uses four hostnames instead of four ports.
    """
    # Validated before anything is built: `docs` or `app,accounts` used to start a process that
    # served a shell whose every API call was a 404 while /healthz and /readyz said ready.
    role = validate_role(role) if role else current_role()
    plat = build_platform(data_dir)
    api = new_api(TITLES.get(role, "Synwevo Docs"))
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=role)

    wants_app = role in ("app", "all")
    wants_accounts = role in ("accounts", "all")
    wants_portal = role == "portal"
    wants_messages = role in ("messages", "all")

    def worker_alive() -> bool:
        jobs = getattr(api.state, "jobs", None)
        mailer = getattr(api.state, "mailer", None)
        return ((jobs.runner_alive() if jobs else True)
                and (mailer.alive() if mailer else True))

    # Probes first: they are the same in every role, and the published OpenAPI document lists
    # them where it always has.
    def scanner_health() -> dict:
        # Synwevo Docs' malware scanner: reported, never gating (see synwevo_core.http.install_probes).
        # The account role scans member-import uploads and the messages role scans attachments
        # with the same policy, so they report too.
        if wants_app:
            return api.state.app.scanner_health()
        from synwevo_core import malware as M
        h = M.health(M.policy())                     # same shape as the Docs role: ok / off / down: <why>
        return h["status"] if h["status"] in ("ok", "off") else "down: " + (h.get("error") or "no PONG")[:120]
    scans = wants_app or wants_accounts or wants_messages
    degradable = {"malware_scanner": scanner_health} if scans else {}
    if wants_messages:
        # The messages process's counters (stream connections, replays, typing…), numbers only
        # (CI-74); read lazily because the messaging router is installed below.
        from synwevo_messages.app import messaging_health
        degradable["messaging"] = lambda: messaging_health(api)
    install_probes(api, plat.store, worker=worker_alive if (wants_app or wants_accounts) else None,
                   degradable=degradable or None)

    # Who is signed in, which workspace, how to leave. The same four routes in every role, from
    # one implementation — the account service does not get its own copy and neither does an app.
    from synwevo_auth_client.session_routes import build_router as build_session_router
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))

    if wants_app:
        from synwevo_docs.api import app as docs_app
        docs_app.install(api, plat, start_jobs=start_jobs)
        if role == "app":
            # Only the process that *is* Synwevo Docs knows its own address. Registration used
            # to happen only in the account service, where `SYNWEVO_APP_URL` is the account
            # service's own address and is deliberately not used — so in a split deployment
            # the portal card pointed at the built-in development URL. Idempotent; it keeps
            # the access policy an operator chose.
            from synwevo_core.apps import bootstrap_docs_app
            bootstrap_docs_app(plat.store, plat.identity)
    if wants_accounts:
        from synwevo_identity import app as identity_app
        from synwevo_docs.control_plane import DocsControlPlane
        if wants_app:
            control_api = api
        else:
            # Private ASGI adapter, never mounted or exposed as account routes. Deployment
            # composition owns this dependency; standalone identity works without Docs.
            from synwevo_docs.api import app as docs_app
            control_api = new_api("Synwevo Docs control adapter")
            control_api.state.auth = plat.auth
            control_api.state.identity = plat.identity
            control_api.state.sessions = plat.sessions
            install_platform(control_api, plat, role="app")
            docs_app.install(control_api, plat, start_jobs=False)
        # The document engine for member imports (any file → rows), injected here because only
        # the composition root may see both the account service and Synwevo Docs. Standalone,
        # the account service falls back to its standard-library readers and says so.
        from synwevo_docs.ingest import DocumentIngest
        identity_app.install(api, plat, start_jobs=start_jobs,
                             control_plane=DocsControlPlane(control_api), ingest=DocumentIngest())
    if wants_messages:
        # Synwevo Message: its API under /api/messaging, sharing the account service's notifier
        # on `all` (one dispatcher per process). The process that serves it registers it — on
        # `all` under this origin's /messages, on its own origin at / — which is how the other
        # roles learn where conversations live (`messages_launch_url`).
        from synwevo_messages import app as messages_app
        from synwevo_core.apps import bootstrap_messages_app
        messages_app.install(api, plat, notifier=getattr(api.state, "notifier", None), start_jobs=start_jobs)
        bootstrap_messages_app(plat.store, plat.identity, role=role)
    if role == "accounts":
        # A deployment without the messages application keeps the account origin's former
        # `/api/auth/messaging/…` routes as a fallback, mounted here because only the composer
        # may see both packages. Every one of them answers 410 naming the messages origin from
        # the moment that application registers itself, so conversations never have two write
        # sources (each process fans out its own stream).
        from synwevo_messages import app as messages_app
        messages_app.install_legacy(api, plat, notifier=getattr(api.state, "notifier", None))
    if wants_portal:
        from synwevo_portal import app as portal_app
        portal_app.install(api, plat)
    if role in ("portal", "all"):
        # The business applications live on the workplace origin (docs/agent-work/DECISIONS.md D1):
        # the host every deployment already routes to this role, with its single sign-on.
        from synwevo_suite import app as suite_app
        suite_app.install(api, plat, start_jobs=start_jobs)

    # The relying-party half is mounted everywhere the account service is not the whole story.
    # It stays on `all` too: a single-process install can be configured against an external
    # issuer, and the chain tests drive two origins out of one composed app. Where there is no
    # external issuer these routes simply point at this process's own provider.
    if role in ("app", "portal", "messages", "all"):
        from synwevo_auth_client.routes import (build_account_redirect_router,
                                                 build_router as build_rp_router)
        api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
        if role != "all":
            # On `all` the account service is in this same process and serves these
            # paths itself, so redirecting them would be a loop back to ourselves.
            api.include_router(build_account_redirect_router())

    _mount_shells(api, plat, role)
    return api


def _mount_shells(api: FastAPI, plat, role: str) -> None:
    """Serve the built shells this role owns.

    Each shell is a separate build with its own asset directory, so a process serving several of
    them (`all`) mounts them all without one build's `/assets` hiding another's. That separation
    is what lets the account and security pages exist exactly once: the workbench no longer
    carries a second copy of the sign-in screen for the single-process case — it navigates to
    the account shell, on the same origin, with the same cookie. The same for the conversations:
    one messages shell, served under its own paths.
    """
    from synwevo_docs.api.app import web_dist as docs_dist
    from synwevo_identity.app import ASSETS_DIR as ACCOUNT_ASSETS, web_dist as accounts_dist
    from synwevo_messages.app import ASSETS_DIR as MESSAGES_ASSETS, web_dist as messages_dist
    from synwevo_portal.app import ASSETS_DIR as PORTAL_ASSETS, web_dist as portal_dist
    from synwevo_suite.app import ASSETS_DIR as SUITE_ASSETS, owns_path as suite_owns, web_dist as suite_dist

    if role == "portal":
        dist = portal_dist()
        suite = suite_dist()
        suite_ok = mount_web(api, suite, assets_dir=SUITE_ASSETS)
        if mount_web(api, dist, assets_dir=PORTAL_ASSETS) or suite_ok:
            @api.get("/{full_path:path}")
            def portal_shell(full_path: str):
                if suite_ok and suite_owns("/" + full_path):
                    return shell_response(suite, full_path)
                return shell_response(dist, full_path)
        return

    if role == "messages":
        dist = messages_dist()
        if mount_web(api, dist, assets_dir=MESSAGES_ASSETS):
            @api.get("/{full_path:path}")
            def messages_shell(full_path: str):
                return shell_response(dist, full_path)
        return

    if role == "accounts":
        dist = accounts_dist()
        if mount_web(api, dist, assets_dir=ACCOUNT_ASSETS):
            # The former /messages pages hand over to Synwevo Message while it is registered.
            # Until then — a deployment that has not added the application, or the window between
            # this release and the day its host exists — this origin serves the messages shell
            # under those paths itself (talking to the legacy `/api/auth/messaging` routes mounted
            # above), so nobody loses their conversations to a "page not found" in between.
            from synwevo_identity.messages_redirects import build_router as build_messages_redirects
            msgs = messages_dist()
            msgs_ok = mount_web(api, msgs, assets_dir=MESSAGES_ASSETS)

            def until_registered(full_path: str):
                return shell_response(msgs if msgs_ok else dist, full_path)
            api.include_router(build_messages_redirects(plat.store, until_registered))

            @api.get("/{full_path:path}")
            def account_shell(full_path: str):
                return shell_response(dist, full_path)
        return

    # `app` and `all`
    work = docs_dist()
    work_ok = mount_web(api, work)
    acct = accounts_dist()
    acct_ok = role == "all" and mount_web(api, acct, assets_dir=ACCOUNT_ASSETS)
    msgs = messages_dist()
    msgs_ok = role == "all" and mount_web(api, msgs, assets_dir=MESSAGES_ASSETS)
    suite = suite_dist()
    suite_ok = role == "all" and mount_web(api, suite, assets_dir=SUITE_ASSETS)

    def owns(paths, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in paths)

    if work_ok or acct_ok or msgs_ok or suite_ok:
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            path = "/" + full_path
            if suite_ok and suite_owns(path):
                return shell_response(suite, full_path)
            if msgs_ok and owns(MESSAGES_SHELL_PATHS, path):
                return shell_response(msgs, full_path)
            if acct_ok and owns(ACCOUNT_SHELL_PATHS, path):
                return shell_response(acct, full_path)
            if work_ok:
                return shell_response(work, full_path)
            if acct_ok or msgs_ok:
                return shell_response(acct if acct_ok else msgs, full_path)
            # Only the business applications' shell is built: other paths have no page here.
            raise ServiceError(404, "not_found", "页面不存在")
