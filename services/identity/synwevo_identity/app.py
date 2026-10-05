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
from synwevo_core.challenge import Turnstile, policy as challenge_policy
from synwevo_core.notifications import Notifier
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
        #: (LowBalanceReminders, notify) once install() has a configured wallet; None means off.
        self.wallet_reminders = None
        self._stop = threading.Event()
        self._thread = None
        if start:
            self._thread = threading.Thread(target=self._run, name="auth-sweeper", daemon=True)
            self._thread.start()
            from synwevo_core import workers
            workers.register(self, self._thread)

    def run_once(self) -> dict:
        from synwevo_auth_client.rp import Rp
        from .profile import finalize_deletions
        return {"sessions": self.sessions.sweep(),
                "mfa_tickets": self.tickets.sweep(),
                "webauthn_challenges": self.passkeys.sweep(),
                "rp_auth_requests": Rp(self.store).sweep(),
                "account_deletions": finalize_deletions(self.store),
                "member_imports": self._member_imports(),
                "wallet_reminders": self._wallet_reminders()}

    def _wallet_reminders(self) -> dict:
        if self.wallet_reminders is None:
            return {"enabled": False}
        reminders, notify = self.wallet_reminders
        return {"enabled": True, **reminders.sweep(notify)}

    def _member_imports(self) -> dict:
        from synwevo_core.identity import Identity
        from .member_import_routes import cleanup_enabled, sweep_member_imports
        if not cleanup_enabled():
            return {"enabled": False}
        return {"enabled": True, **sweep_member_imports(self.store, Identity(self.store))}

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


def install(api: FastAPI, plat: Platform, *, start_jobs: bool = True, control_plane=None, ingest=None) -> None:
    """Mount the account service's routes onto an application the caller has scaffolded.

    `ingest` is the document engine used to turn a member-import upload into rows (injected by
    the composition root); without one, the standard-library readers in ``synwevo_core`` handle
    CSV/TSV/JSON/TXT/Markdown and every other format is reported as needing the engine."""
    from .admin_routes import build_router as build_admin_router
    from .auth_routes import (build_invite_router, build_mfa_router, build_passkey_router,
                              build_recovery_router, build_router as build_auth_router)
    from .invites import Invites
    from .mail import Mailer
    from .notify import NotificationDispatcher
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
    # The address account mail comes from, for the "check your inbox" screen: it is in every
    # mail we send, so publishing it costs nothing and lets people search or allow-list it.
    from .mail import from_address, mode as mail_mode
    api.state.mail_sender = dict(zip(("name", "address"), from_address())) if mail_mode() != "disabled" else None
    # Human verification (Cloudflare Turnstile) for the anonymous mail-sending entrances and
    # for sign-ins after repeated failures. Built here so a half-configured pair of keys fails
    # the start-up rather than one request in a thousand; disabled when neither key is set.
    api.state.challenge = Turnstile(challenge_policy())
    # The platform inbox: every role writes rows; this role alone turns e-mail deliveries into
    # outbox mail (it is the one with a mailer). start=False in tests keeps the worker quiet.
    api.state.notifier = Notifier(store)
    api.state.notify_dispatcher = NotificationDispatcher(store, plat.identity, mailer, start=start_jobs)
    api.state.auth_sweeper = Sweeper(store, sessions, tickets, passkeys, start=start_jobs)
    from .member_import_routes import cleanup_enabled as _import_cleanup
    api.state.member_import_cleanup = _import_cleanup()        # shown on the privacy page (public state)
    api.state.mfa = mfa
    api.state.passkeys = passkeys

    api.include_router(build_auth_router(identity, sessions, audit, mfa, tickets))
    api.include_router(build_invite_router(identity, sessions, invites, mailer, audit))
    api.include_router(build_mfa_router(identity, sessions, mfa, audit))
    from .email_login import build_router as build_email_login_router
    from functools import partial
    from synwevo_auth_client.session_routes import describe_user as _describe_user
    describe_user = partial(_describe_user, store=store)
    api.include_router(build_email_login_router(identity, sessions, mailer, mfa, tickets, audit,
                                                describe=describe_user))
    api.include_router(build_recovery_router(identity, sessions, invites, mailer, mfa, audit))
    from .registration import build_router as build_registration_router
    api.include_router(build_registration_router(identity, sessions, invites, mailer, audit))
    from .account_routes import build_router as build_account_router
    from .organization_routes import build_router as build_organization_router
    from .api_platform import build_router as build_api_platform_router
    api.include_router(build_account_router(identity, sessions, mailer, audit))
    api.include_router(build_organization_router(store, identity, sessions, audit, invites, mailer))
    from synwevo_core.member_import import BasicIngest
    from .member_import_routes import build_router as build_member_import_router
    api.state.ingest = ingest or BasicIngest()
    member_imports = build_member_import_router(store, identity, sessions, audit, invites, mailer,
                                                ingest=api.state.ingest, start_jobs=start_jobs)
    api.state.member_import_runner = member_imports.runner
    api.include_router(member_imports)

    def _shutdown() -> None:
        """The end of this process: stop the account service's workers (mail, notifications,
        session sweeping, member imports). Queued mail and imports wait in the store for the next
        process; nothing is sent twice because sending is claimed per row."""
        for worker in (mailer, api.state.notify_dispatcher, api.state.auth_sweeper, api.state.member_import_runner):
            if worker is not None:
                worker.stop()
    api.state.shutdown_hooks.append(_shutdown)
    # Conversations themselves are Synwevo Message's (apps/messages, its own origin since CI-66).
    # What stays here is the moderation half: listing and reviewing reports needs the
    # organization permission and a strict re-authentication on the *account* session.
    from .messaging_reports_routes import build_router as build_messaging_reports_router
    api.include_router(build_messaging_reports_router(store, identity, sessions, audit, notifier=api.state.notifier))
    from .approval_routes import build_router as build_approval_router
    api.include_router(build_approval_router(store, identity, sessions, audit, notifier=api.state.notifier))
    from .push_routes import build_router as build_push_router, static_routes as push_static_routes
    api.include_router(build_push_router(store, identity, sessions, audit, notifier=api.state.notifier))
    api.include_router(push_static_routes())
    api.include_router(build_api_platform_router(store, identity, sessions, audit, adapter=control_plane))
    # The workspace directory API (API-5): machine-only, verified at the gate.
    from .workspace_api import build_router as build_workspace_api_router
    api.include_router(build_workspace_api_router(store, identity, invites, mailer, audit))
    api.include_router(build_passkey_router(identity, sessions, passkeys, audit))
    from .reauth_routes import build_router as build_reauth_router
    api.include_router(build_reauth_router(identity, sessions, passkeys, audit))
    # External OpenID providers: configured ones are validated here, at start-up, so a half
    # configured provider is a refusal to start rather than a button that fails at sign-in.
    from .external import ExternalIdentities, build_router as build_external_router, load_providers
    external = ExternalIdentities(store, load_providers())
    api.state.external = external
    api.include_router(build_external_router(store, identity, sessions, external, mfa, tickets, mailer, audit,
                                             describe=describe_user))

    # Registered before any SPA catch-all so /oidc/* and /.well-known/* reach real handlers
    # instead of being served the shell.
    bootstrap_docs_app(store, identity)
    # Once per database, not once per start: the adoption grants every member an entitlement
    # and enables the application in every workspace with members, so running it on every start
    # silently undid each `app revoke` and `app disable --workspace` at the next restart.
    Apps(store, identity).adopt_once()
    api.include_router(build_oidc_router(store, identity, sessions, audit))
    api.include_router(build_admin_router(store, identity, sessions, audit, mailer=mailer))
    from .admin_settings import build_router as build_settings_router
    api.include_router(build_settings_router(store, audit))
    from .admin_model_prices import build_router as build_model_prices_router
    api.include_router(build_model_prices_router(store, audit))
    from .admin_model_costs import build_router as build_model_costs_router
    api.include_router(build_model_costs_router(store, audit))
    from .admin_model_provider import build_router as build_model_provider_router
    api.include_router(build_model_provider_router(store, audit))
    from .admin_runs import build_router as build_runs_router
    api.include_router(build_runs_router(store, audit))
    from .wallet_routes import build_router as build_wallet_router, from_host as wallet_from_host
    wallet_ledger, wallet_units = wallet_from_host(store)
    api.include_router(build_wallet_router(identity, sessions, mailer, audit,
                                          ledger=wallet_ledger, units=wallet_units))
    if wallet_ledger is not None:
        # Each person's own low-balance reminder, checked on the sweeper's schedule.
        from synwevo_core.billing.reminders import LowBalanceReminders, notice
        api.state.auth_sweeper.wallet_reminders = (
            LowBalanceReminders(store, environment=wallet_ledger.environment), notice(api.state.notifier))
    from .admin_handover import build_router as build_handover_router
    api.include_router(build_handover_router(identity, sessions, passkeys, audit))


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
        # The former /messages pages hand over to Synwevo Message (303 to its sign-in start) while
        # it is registered; registered before the catch-all so they reach a real handler.
        from .messages_redirects import build_router as build_messages_redirects
        api.include_router(build_messages_redirects(plat.store, lambda full_path: shell_response(dist, full_path)))

        @api.get("/{full_path:path}")
        def shell(full_path: str):
            return shell_response(dist, full_path)

    return api
