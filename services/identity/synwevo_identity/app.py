"""账户服务进程：`SYNWEVO_ROLE=accounts` 时运行的那一个。

登录、账户与安全、平台管理、OIDC 授权服务器，以及账户外壳。没有工作台，没有门户首页——
它们在各自的源站上；一个共用外壳会让账户服务渲染出一份它自己拒绝加载的项目列表。
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from synwevo_auth_client.session_routes import build_router as build_session_router
from synwevo_core import env
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.http import (Platform, build_platform, install as install_platform,
                                install_probes, mount_web, new_api, shell_response)

ASSETS_DIR = "account-assets"

SWEEP_INTERVAL_S = 3600


class Sweeper:
    """Reclaims expired sign-in state on a schedule.

    Each of these tables already had a `sweep()` and nothing called any of them, so rows written
    by anonymous traffic — sign-in attempts started and abandoned, MFA tickets, passkey
    challenges — and every dead session accumulated forever. Expiry was always enforced on read;
    this only reclaims the space, so a sweep that fails is logged and retried next time rather
    than stopping the service.
    """

    def __init__(self, store, sessions, tickets, passkeys, *, start: bool = True):
        import threading
        self.store, self.sessions, self.tickets, self.passkeys = store, sessions, tickets, passkeys
        self._stop = threading.Event()
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, name="auth-sweeper", daemon=True)
            self._thread.start()

    def run_once(self) -> dict:
        from synwevo_auth_client.rp import Rp
        return {"sessions": self.sessions.sweep(),
                "mfa_tickets": self.tickets.sweep(),
                "webauthn_challenges": self.passkeys.sweep(),
                "rp_auth_requests": Rp(self.store).sweep()}

    def _run(self) -> None:
        while not self._stop.wait(SWEEP_INTERVAL_S):
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001 - reclamation must never take the service down
                print(f"[synwevo] auth sweep failed: {type(e).__name__}: {e}", flush=True)

    def stop(self) -> None:
        self._stop.set()


def web_dist() -> Path:
    """The account shell's built output, beside its own source."""
    return Path(os.environ.get("SYNWEVO_ACCOUNTS_UI_DIR")
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
    api.state.auth_sweeper = Sweeper(store, sessions, tickets, passkeys, start=start_jobs)
    api.state.mfa = mfa
    api.state.passkeys = passkeys

    api.include_router(build_auth_router(identity, sessions, audit, mfa, tickets))
    api.include_router(build_invite_router(identity, sessions, invites, mailer, audit))
    api.include_router(build_mfa_router(identity, sessions, mfa, audit))
    api.include_router(build_recovery_router(identity, sessions, invites, mailer, mfa, audit))
    api.include_router(build_passkey_router(identity, sessions, passkeys, audit))

    # Registered before any SPA catch-all so /oidc/* and /.well-known/* reach real handlers
    # instead of being served the shell.
    bootstrap_docs_app(store, identity)
    # Once per database, not once per start: the adoption grants every member an entitlement
    # and enables the application in every workspace with members, so running it on every start
    # silently undid each `app revoke` and `app disable --workspace` at the next restart.
    Apps(store, identity).adopt_once()
    api.include_router(build_oidc_router(store, identity, sessions, audit))
    api.include_router(build_admin_router(store, identity, sessions, audit))


def create_app(data_dir: str | None = None, start_jobs: bool = True) -> FastAPI:
    plat = build_platform(data_dir)
    api = new_api("Synwevo Account")
    api.state.auth = plat.auth
    api.state.identity = plat.identity
    api.state.sessions = plat.sessions
    install_platform(api, plat, role=env.get("ROLE") or "accounts")
    api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
    install(api, plat, start_jobs=start_jobs)
    install_probes(api, plat.store, worker=lambda: bool(api.state.mailer and api.state.mailer.alive()))

    dist = web_dist()
    if mount_web(api, dist, assets_dir=ASSETS_DIR):
        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)

    return api
