"""Browser authentication endpoints (`/api/auth/*`).

These are UI endpoints, not part of the `/api/v1` automation contract — nothing here appears in
the published OpenAPI subset, and the standing rule that automation surfaces get no product UI
is untouched: this is the ordinary account surface a person needs to sign in and manage their
own sessions.

Responses are written so that an unauthenticated caller learns nothing about which addresses
have accounts. Login answers with one shape for "no such account", "wrong password" and
"account suspended"; only the audit record distinguishes them.
"""
from __future__ import annotations

import os
from fastapi import APIRouter, Body, Request, Response

from synwevo_auth_client.session_routes import describe_user
from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, registration_mode
from synwevo_core.passwords import PasswordError
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions, cookie_kwargs, new_csrf_token
from synwevo_core.store import now as now_ts

from .mfa import TICKET_MAX_ATTEMPTS
from .mail import reset_mail as mail_reset, security_notice as mail_notice, verify_mail as mail_verify

# Same shape for every failed sign-in attempt, whatever actually went wrong.
_LOGIN_FAILED = ("bad_credentials", "邮箱或口令不正确")


def cookies_secure() -> bool:
    """Whether to mark cookies Secure.

    Not guessed from the request: behind a TLS-terminating proxy the origin only ever sees plain
    HTTP, so inferring it would disable the flag exactly where it matters most. Defaults to on,
    and is turned off deliberately for local development over http://127.0.0.1.
    """
    raw = (env.get("COOKIE_SECURE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def _trusted_hops() -> int:
    raw = (env.get("TRUST_PROXY") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return 1
    return int(raw) if raw.isdigit() else 0


def client_ip(request: Request) -> str:
    """The address used for rate limiting and audit.

    X-Forwarded-For is honoured only when a trusted proxy is configured, because otherwise any
    caller could rotate a header to escape their own rate-limit bucket. And then only the entry
    our own proxy wrote: each proxy *appends* the address it received the request from, so the
    left end is whatever the client chose to send. Taking the leftmost entry — as this used to —
    let anyone rotate that value per request and never fill an address bucket. `TRUST_PROXY` is
    the number of trusted proxies in front of the service (`1`/`true` for one); the address is
    the entry that many places from the right.
    """
    hops = _trusted_hops()
    if hops:
        parts = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        if parts:
            return (parts[-hops] if len(parts) >= hops else parts[0])[:64]
    return (request.client.host if request.client else "") or "unknown"


# Second-factor guesses are limited per account across tickets, not only per ticket: every
# correct password buys a fresh ticket, so a per-ticket limit alone let someone who knew the
# password keep guessing codes indefinitely.
MFA_BUCKET = "mfa:{uid}"
# One ticket's worth of mistakes is free; beyond that each further wrong code across tickets
# waits longer, up to 15 minutes. At the cap that is under a hundred guesses a day against a
# million codes, where the per-ticket limit alone allowed as many as sign-ins could be made.
MFA_BACKOFF = (0, 0, 0, 0, 0, 2, 5, 15, 30, 60, 120, 300, 600, 900)
# A session whose holder keeps failing re-authentication is ended: the cookie is the only thing
# they have, and continuing to accept guesses through it is an online password oracle.
REAUTH_MAX_FAILURES = 5
# Anonymous endpoints that do work or send mail per call: counted per source address.
FORGOT_PER_ADDRESS = (10, 900)            # 10 requests per 15 minutes per source address
FORGOT_PER_ACCOUNT = (3, 3600)            # at most 3 reset mails an hour to one account...
FORGOT_MIN_GAP_S = 60                     # ...and not two within a minute
PASSKEY_BEGIN_PER_ADDRESS = (30, 60)      # 30 challenges per minute per source address
BOOTSTRAP_PER_ADDRESS = (10, 900)


LOGIN_CHALLENGE_AFTER = 3                 # failed sign-ins from one source address before a challenge


def _challenge(request: Request):
    """The process's human-verification policy; a disabled one is a no-op on every call."""
    ch = getattr(request.app.state, "challenge", None)
    if ch is None:
        from synwevo_core.challenge import Policy, Turnstile
        ch = request.app.state.challenge = Turnstile(Policy(None, None, frozenset(), 5))
    return ch


def _too_many(retry_after: float) -> ServiceError:
    return ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试",
                        {"retry_after": max(1, int(retry_after or 60))})


def notify_security_change(request: Request, user: dict | None, what: str) -> None:
    """Tell the account holder a factor or credential changed. Best effort, never blocking.

    The notice is what lets someone notice an attacker's change from their inbox; failing to
    queue it must not undo the change they (or the attacker) just made, so errors are logged.
    """
    mailer = getattr(request.app.state, "mailer", None)
    if not mailer or not user:
        return
    try:
        subject, text = mail_notice(what=what, when_text="刚刚", locale=user.get("locale") or "zh-Hans")
        mailer.enqueue(user["email"], subject, text, kind="security", ref_id=user["id"])
    except Exception as e:  # noqa: BLE001 - mail may be deliberately unconfigured
        print(f"[synwevo] security notice not queued ({what}): {type(e).__name__}", flush=True)


def check_reauth_password(ident: Identity, sessions: Sessions, request: Request, s: dict,
                          password: str, audit, scope: str) -> None:
    """Re-verify the password for a sensitive action, under the login limits. Raises on failure.

    Shared by both re-authentication routes so neither can drift back to an unthrottled check.
    """
    res = ident.check_password(s["user_id"], password or "", ip=client_ip(request))
    if res["ok"]:
        ident.backoff_clear(f"reauth:{s['id']}")
        return
    audit(request, "auth.reauth", actor_id=s["user_id"], result="denied",
          meta={"scope": scope, "reason": res["reason"]})
    if res["reason"] in ("backoff", "address_blocked"):
        raise _too_many(res.get("retry_after"))
    bucket = f"reauth:{s['id']}"
    ident.backoff_fail(bucket)
    if ident.attempt_count(bucket) >= REAUTH_MAX_FAILURES:
        sessions.revoke(s["id"], "reauth_failures")
        from .provider_routes import end_central_session
        end_central_session(ident.store, s["id"], "reauth_failures")
        ident.backoff_clear(bucket)
        audit(request, "auth.session.ended", actor_id=s["user_id"], object_kind="session",
              object_id=s["id"], result="ok", meta={"reason": "reauth_failures"})
        raise ServiceError(401, "session_ended", "口令错误次数过多，这个登录已结束，请重新登录")
    raise ServiceError(401, "bad_credentials", "口令不正确")


def build_router(ident: Identity, sessions: Sessions, audit, mfa=None, tickets=None) -> APIRouter:
    r = APIRouter(prefix="/api/auth", tags=["auth"])

    def _issue(response: Response, user: dict, *, amr: str, request: Request,
               persistent: bool = False, auth_provider: str | None = None) -> dict:
        """Start a session and set both cookies. Always called after authentication succeeds,
        never reusing anything the client supplied, so a pre-set session id cannot be promoted.

        `persistent` is the "keep me signed in" choice made on the form that authenticated. It
        changes the cookie's lifetime and the idle window (sessions.py); it never lowers what a
        later sensitive action asks for — re-authentication and second factors are unchanged.
        """
        return issue_session(ident, sessions, response, user, amr=amr, request=request,
                             persistent=persistent, auth_provider=auth_provider, describe=_me)

    def _me(user: dict, spaces: list[dict], session: dict | None = None) -> dict:
        return describe_user(user, spaces, session)

    @r.post("/bootstrap")
    def bootstrap(request: Request, response: Response, body: dict = Body(...)):
        """Create the first platform administrator with a token issued by `manage.py bootstrap`.

        The token is the authority here; the request is otherwise unauthenticated. It is
        single-use and expires, so this cannot be left open as a way in.
        """
        token = (body.get("token") or "").strip()
        email, password = (body.get("email") or "").strip(), body.get("password") or ""
        if not token or not email or not password:
            raise ServiceError(400, "missing_fields", "请填写引导令牌、邮箱与口令")
        if not ident.bootstrap_needed():
            raise ServiceError(409, "already_bootstrapped", "本服务已经有平台管理员了")
        # The token is checked before anything is created. Creating the account first (so a
        # failed creation would not burn the token) let any anonymous caller trigger an Argon2
        # hash and leave an orphaned workspace behind on every attempt with a made-up token.
        limit, window = BOOTSTRAP_PER_ADDRESS
        allowed, wait = ident.window_allows(f"bootstrap:{client_ip(request)}", limit=limit, window_s=window)
        if not allowed:
            raise _too_many(wait)
        if not ident.peek_bootstrap_token(token):
            audit(request, "auth.bootstrap", result="denied", meta={"reason": "bad_token"})
            raise ServiceError(403, "bad_token", "引导令牌无效或已过期，请重新生成")
        try:
            user = ident.create_user(email, password, display_name=body.get("display_name"),
                                     status="active", platform_role="admin")
        except PasswordError as e:
            raise ServiceError(400, "weak_password", str(e), {"field": "password"}) from None
        # Consumed after the account exists so a failed creation does not burn the token; a
        # concurrent bootstrap that spent it first leaves nothing of this attempt behind.
        if not ident.consume_bootstrap_token(token, user_id=user["id"]):
            ident.purge_new_account(user["id"])
            raise ServiceError(403, "bad_token", "引导令牌无效或已过期，请重新生成")
        audit(request, "auth.bootstrap", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok")
        return _issue(response, user, amr="password", request=request)

    # ------------------------------------------------------------------ login / logout
    @r.post("/login")
    def login(request: Request, response: Response, body: dict = Body(...)):
        email, password = (body.get("email") or "").strip(), body.get("password") or ""
        remember = remember_choice(body)
        if not email or not password:
            raise ServiceError(400, *_LOGIN_FAILED)
        # Risk-triggered: an ordinary sign-in never sees a challenge. Once this source address
        # has failed LOGIN_CHALLENGE_AFTER times in the address window, every further attempt
        # must carry a solved token *before* the password is checked — so a guesser without one
        # burns no attempt, learns nothing from timing, and cannot use the failure counters to
        # lock the account holder out (the holder solves the challenge and continues).
        ch = _challenge(request)
        if ch.enabled and ident.address_failures(client_ip(request)) >= LOGIN_CHALLENGE_AFTER:
            ch.require(request, body, "login", audit=audit)
        # Password verification and issuance must serialize with activation/credential
        # withdrawal. Otherwise an old-password request can finish hashing after every
        # existing session was revoked and create a fresh, unrevoked session or MFA ticket.
        with ident.store.tx():
            res = ident.authenticate(email, password, ip=client_ip(request))
            if res["ok"]:
                user = res["user"]
                if mfa and mfa.enabled_for(user["id"]):
                    # The ticket proves only the password step, never a complete sign-in.
                    ticket = tickets.issue(user["id"], user_agent=request.headers.get("user-agent"),
                                           ip=client_ip(request))
                    f = mfa.factors(user["id"])
                    audit(request, "auth.login", actor_id=user["id"], object_kind="user",
                          object_id=user["id"], result="ok", meta={"stage": "password", "mfa": True})
                    return {"mfa_required": True, "ticket": ticket,
                            "methods": {"totp": f["totp"], "recovery": f["recovery_remaining"] > 0}}
                audit(request, "auth.login", actor_id=user["id"], object_kind="user",
                      object_id=user["id"], result="ok", meta={"remember": remember})
                return _issue(response, user, amr="password", request=request, persistent=remember)
            # Commit failed-attempt counters and audit before raising the HTTP error.
            # Raising inside this transaction would roll back brute-force protection.
            audit(request, "auth.login", actor_id=(res.get("user") or {}).get("id"),
                  object_kind="user", object_id=(res.get("user") or {}).get("id"),
                  result="denied", meta={"reason": res["reason"]})
        if res["reason"] in ("backoff", "address_blocked"):
            raise ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试",
                               {"retry_after": int(res.get("retry_after") or 60)})
        raise ServiceError(401, *_LOGIN_FAILED)

    @r.post("/login/passkey")
    def login_passkey(request: Request, response: Response, body: dict = Body(...)):
        """Sign in with a passkey alone — no password step before it.

        `amr` records what actually happened. `passkey_uv` means the authenticator verified the
        person in *this* ceremony (a PIN, a fingerprint); `passkey` alone means it proved
        possession only. A policy that needs a phishing-resistant *and* verified sign-in checks
        the first; treating "the account owns a passkey" as either is the mistake this
        distinction exists to prevent.
        """
        pk = request.app.state.passkeys
        remember = remember_choice(body)
        out = pk.verify(body.get("credential") or {})
        user = ident.get_user(out["user_id"])
        if not user or user["status"] != "active":
            # Same shape as every other failed sign-in: no account state is disclosed here.
            audit(request, "auth.login.passkey", actor_id=out["user_id"], result="denied",
                  meta={"reason": "user_unavailable"})
            raise ServiceError(401, *_LOGIN_FAILED)
        amr = "passkey_uv" if out["user_verified"] else "passkey"
        # A passkey used *without* user verification proves possession of a device and nothing
        # else — one factor. An account whose owner turned on a second factor, and a platform
        # administrator, must not be entered on one factor, so for them the authenticator has to
        # verify the person in this ceremony (a PIN or biometric: possession plus knowledge or
        # inherence). Everyone else may still sign in with possession alone, as before; `amr`
        # records which it was.
        if not out["user_verified"] and (user["platform_role"] == "admin"
                                         or (mfa and mfa.enabled_for(user["id"]))):
            audit(request, "auth.login.passkey", actor_id=user["id"], object_kind="passkey",
                  object_id=out["passkey_id"], result="denied", meta={"reason": "uv_required"})
            raise ServiceError(401, "uv_required",
                               "这个账户要求两步验证：使用安全密钥登录时需要验证 PIN、指纹或面容；"
                               "也可以用口令加动态验证码登录。")
        audit(request, "auth.login.passkey", actor_id=user["id"], object_kind="passkey",
              object_id=out["passkey_id"], result="ok",
              meta={"amr": amr, "sign_count_warning": out["sign_count_warning"]})
        issued = _issue(response, user, amr=amr, request=request, persistent=remember)
        if out["sign_count_warning"]:
            # Surfaced rather than swallowed. It is the protocol's only cloning signal, and it
            # is also routinely zero on synced passkeys — so it is a note, not a refusal.
            issued["sign_count_warning"] = True
        return issued

    @r.post("/login/mfa")
    def login_mfa(request: Request, response: Response, body: dict = Body(...)):
        """Second half of a sign-in. Requires the ticket from /login plus one factor."""
        raw = (body.get("ticket") or "").strip()
        code = (body.get("code") or "").strip()
        recovery = (body.get("recovery_code") or "").strip()
        remember = remember_choice(body)
        t = tickets.load(raw)
        if not t:
            # Expired, spent, or out of attempts — all indistinguishable, all mean start again.
            raise ServiceError(401, "ticket_invalid", "验证已超时或失败次数过多，请重新登录")
        user = ident.get_user(t["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "ticket_invalid", "验证已超时或失败次数过多，请重新登录")
        bucket = MFA_BUCKET.format(uid=user["id"])
        wait = ident.backoff_wait(bucket, MFA_BACKOFF)
        if wait > 0:
            # Checked before the attempt is spent, so waiting out the backoff costs nothing.
            raise _too_many(wait)
        used_attempts = tickets.claim_attempt(raw)
        if used_attempts is None:
            raise ServiceError(401, "ticket_invalid", "验证已超时或失败次数过多，请重新登录")

        used_recovery = False
        # The ticket says which first factor it proved (password, or a code mailed to the
        # account's address); the session's amr records the pair that actually happened.
        first = t.get("first_factor") or "password"
        if code and mfa.verify_totp(user["id"], code):
            amr = f"{first}+totp"
        elif recovery and mfa.use_recovery_code(user["id"], recovery):
            amr, used_recovery = f"{first}+recovery", True
        else:
            ident.backoff_fail(bucket)
            left = TICKET_MAX_ATTEMPTS - used_attempts
            audit(request, "auth.login.mfa", actor_id=user["id"], object_kind="user",
                  object_id=user["id"], result="denied",
                  meta={"remaining": max(0, left), "account_failures": ident.attempt_count(bucket)})
            raise ServiceError(401, "bad_code", "验证码不正确" + (f"，还可尝试 {left} 次" if left > 0 else "，请重新登录"))
        ident.backoff_clear(bucket)

        if not tickets.redeem(raw):
            raise ServiceError(401, "ticket_invalid", "验证已超时，请重新登录")
        audit(request, "auth.login.mfa", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok", meta={"amr": amr})
        out = _issue(response, user, amr=amr, request=request, persistent=remember)
        if used_recovery:
            # Says it plainly: a spent recovery code is one fewer way back in, and the moment
            # someone notices is when they are already in the account and can act on it.
            out["recovery_used"] = True
            out["recovery_remaining"] = mfa.recovery_remaining(user["id"])
        return out


    @r.post("/reauth")
    def reauth(request: Request, body: dict = Body(...)):
        """Re-prove the password for one named purpose, for a few minutes.

        The scope comes from a closed set. A client-chosen string would let a page ask for
        `scope=""`, which satisfies every check — the one value that must not be requestable.
        """
        from synwevo_core.sessions import REAUTH_SECONDS, REAUTH_SCOPES
        s = _require_session(request)
        scope = (body.get("scope") or "").strip()
        if scope not in REAUTH_SCOPES:
            raise ServiceError(400, "bad_scope", f"未知的重认证用途 {scope!r}")
        check_reauth_password(ident, sessions, request, s, body.get("password") or "", audit, scope)
        sessions.mark_reauth(s["id"], scope)
        # The password is a second, distinct factor for a session that began with a mailed code
        # or an external provider; recording it is what unlocks platform administration.
        amr = sessions.add_factor(s["id"], "password")
        audit(request, "auth.reauth", actor_id=s["user_id"], result="ok", meta={"scope": scope, "amr": amr})
        return {"ok": True, "scope": scope, "valid_for_s": REAUTH_SECONDS, "amr": amr}

    # ------------------------------------------------------------------ sessions
    @r.get("/sessions")
    def list_sessions(request: Request):
        s = _require_session(request)
        return {"items": sessions.list_for_user(s["user_id"], current_id=s["id"])}

    @r.delete("/sessions/{sid}")
    def revoke_session(request: Request, sid: str):
        s = _require_session(request)
        # Scoped to the caller's own sessions: passing someone else's id must not end their day.
        owned = ident.store.one("SELECT user_id FROM sessions WHERE id=?", (sid,))
        if not owned or owned["user_id"] != s["user_id"]:
            raise ServiceError(404, "no_session", "找不到该会话")
        # Ending a device's sign-in ends what it authorised: the platform tokens minted from it
        # and the application sessions it opened. Revoking only the row left stolen delegated
        # refresh tokens working for up to two weeks after "sign out this device".
        from .provider_routes import end_central_session
        out = end_central_session(ident.store, sid, "user_revoked")
        sessions.revoke(sid, "user_revoked")
        # Broader than the action on purpose: the journal has no kind that re-revokes one session
        # row, and a restore that brought this device's sign-in back would undo exactly what the
        # person asked for. Replaying ends every session and token of the account instead —
        # fail-safe, at the cost of signing them in again after a restore.
        from .withdrawals import record as journal
        journaled = journal(ident.store, "sessions_revoked", user_id=s["user_id"])
        audit(request, "auth.session.revoke", actor_id=s["user_id"], object_kind="session",
              object_id=sid, result="ok", meta={**out, "journaled": journaled})
        return {"ok": True, "was_current": sid == s["id"], "tokens_revoked": out["tokens_revoked"]}

    @r.post("/sessions/revoke-others")
    def revoke_others(request: Request):
        s = _require_session(request)
        from .provider_routes import end_central_session
        current_root = sessions.device_root_id(s["id"]) or s["id"]
        others = ident.store.all("SELECT id FROM sessions WHERE user_id=? AND id<>? AND revoked_at IS NULL",
                                 (s["user_id"], s["id"]))
        tokens = n = 0
        ended_roots = {current_root}
        for row in others:
            root = sessions.device_root_id(row["id"])
            if root is None or root in ended_roots:
                continue
            ended_roots.add(root)
            # A root and its RP rows are one device. End it once and count the actual
            # changes; a second blanket revocation both lost this count and could end
            # the current device through one of its relying-party sessions.
            outcome = end_central_session(ident.store, row["id"], "user_revoked_others")
            tokens += outcome["tokens_revoked"]
            n += outcome["sessions_revoked"]
        # Tokens not tied to any central session this account still has open — minted from a
        # session since swept, say — cannot be vouched for by the one being kept, so they go too.
        tokens += ident.store.execute(
            "UPDATE oauth_tokens SET revoked_at=?, revoked_reason='user_revoked_others' "
            "WHERE user_id=? AND revoked_at IS NULL AND (central_session_id IS NULL OR central_session_id<>?)",
            (now_ts(), s["user_id"], current_root))
        from .withdrawals import record as journal
        journaled = journal(ident.store, "sessions_revoked", user_id=s["user_id"])
        audit(request, "auth.session.revoke_others", actor_id=s["user_id"], result="ok",
              meta={"count": n, "tokens_revoked": tokens, "journaled": journaled})
        return {"ok": True, "revoked": n, "tokens_revoked": tokens}


    def _require_session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    return r


def build_invite_router(ident: Identity, sessions: Sessions, invites, mailer, audit) -> APIRouter:
    """Invitation and email-token endpoints.

    Split from the auth router only because it needs the invite and mail services; it is mounted
    on the same `/api/auth` prefix and the anonymous set in app.py lists exactly which of these
    may be reached without a session.
    """
    from .members import build_router as build_members_router, error_response

    r = APIRouter(prefix="/api/auth", tags=["auth"])
    # Member, role, entitlement, ownership and invitation-lifecycle routes live in members.py and
    # share this router's prefix; including them here keeps the account service's composition
    # (app.py) unchanged.
    governance = build_members_router(ident, sessions, invites, mailer, audit)

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    # ------------------------------------------------------------------ issuing
    @r.post("/workspaces/{wid}/invitations")
    def invite(request: Request, wid: str, body: dict = Body(...)):
        """`{"email", "role", "can_export"?, "expires_in_days"? (1–30, default 7)}`.

        429 `invite_rate_limited` with `Retry-After` once the workspace has sent 50 in 24 hours.
        """
        s = _session(request)
        email, role = (body.get("email") or "").strip(), (body.get("role") or "viewer").strip()
        try:
            return governance.issue_invitation(request, s, wid, email, role, body.get("can_export"),
                                               governance.invite_ttl(body))
        except ServiceError as e:
            if e.status == 429:
                return error_response(request, e)
            raise

    @r.get("/workspaces/{wid}/invitations")
    def list_invites(request: Request, wid: str, state: str = ""):
        """Every invitation with its real state and mail state; `state=` filters. `groups` counts
        them the way the page shows them (a mail that failed is its own group)."""
        s = _session(request)
        items = invites.list_for_workspace(wid, s["user_id"], state=state.strip() or None)
        groups = {"pending": 0, "expired": 0, "revoked": 0, "accepted": 0, "declined": 0, "mail_failed": 0}
        for it in items:
            st = mailer.status_for(it["id"])
            it["mail"] = (st or {}).get("status") or "unknown"
            it["mail_error"] = (st or {}).get("last_error")
            it["can_export"] = bool(it["can_export"])
            groups[it["state"]] += 1
            if it["state"] == "pending" and it["mail"] == "failed":
                groups["mail_failed"] += 1
        return {"items": items, "groups": groups}

    @r.delete("/invitations/{iid}")
    def revoke_invite(request: Request, iid: str):
        s = _session(request)
        journaled = None
        if invites.revoke(iid, s["user_id"]):
            # A withdrawn link stays withdrawn when an older database is restored.
            from .withdrawals import record as journal
            journaled = journal(ident.store, "invitation_revoked", invitation_id=iid)
        audit(request, "invite.revoke", actor_id=s["user_id"], object_kind="invitation",
              object_id=iid, result="ok", meta={"journaled": journaled})
        return {"ok": True, "journaled": journaled}

    # ------------------------------------------------------------------ accepting
    @r.get("/invitations/preview")
    def preview_invite(request: Request, token: str):
        """Read-only. Opening the emailed link lands here and changes nothing, so a mail scanner
        following the URL cannot accept on the recipient's behalf."""
        out = invites.preview(token)
        s = getattr(request.state, "session", None)
        if s:
            u = ident.get_user(s["user_id"])
            out["signed_in_as"] = (u or {}).get("email")
        return out

    @r.post("/invitations/preview")
    def preview_invite_body(request: Request, body: dict = Body(...)):
        token = body.get("token")
        if not isinstance(token, str) or len(token) > 256:
            raise ServiceError(400, "bad_invite", "邀请链接无效")
        return preview_invite(request, token)

    @r.post("/invitations/accept")
    def accept_invite(request: Request, response: Response, body: dict = Body(...)):
        token = (body.get("token") or "").strip()
        s = getattr(request.state, "session", None)
        out = invites.accept(token, password=body.get("password") or None,
                             display_name=body.get("display_name"),
                             as_user_id=s["user_id"] if s else None)
        audit(request, "invite.accept", actor_id=out["user"]["id"], workspace_id=out["workspace_id"],
              object_kind="membership", object_id=out["user"]["id"], result="ok",
              meta={"role": out["role"]})
        if s:
            sessions.set_workspace(s["id"], out["workspace_id"])
            return {"user": _public_user(ident, out["user"]), "workspace_id": out["workspace_id"]}
        return _issue_session(ident, sessions, response, out["user"], amr="password", request=request)

    @r.post("/invitations/decline")
    def decline_invite(request: Request, body: dict = Body(...)):
        token = body.get("token")
        if set(body) != {"token"} or not isinstance(token, str) or not 1 <= len(token) <= 256:
            raise ServiceError(400, "bad_invite", "邀请链接无效")
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        with ident.store.tx():
            out = invites.decline(token, state["user_id"])
            audit(request, "invite.decline", actor_id=state["user_id"], workspace_id=out["workspace_id"],
                  object_kind="invitation", object_id=out["id"], result="ok")
        return {"state": "declined"}

    r.include_router(governance)
    return r


def _public_user(ident: Identity, user: dict) -> dict:
    spaces = ident.workspaces_for(user["id"])
    return {"id": user["id"], "email": user["email"], "display_name": user.get("display_name"),
            "status": user["status"], "platform_role": user["platform_role"],
            "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"],
                            "role": w["role"], "can_export": bool(w["can_export"])} for w in spaces]}


def remember_choice(body: dict) -> bool:
    """The "keep me signed in" checkbox. Only a JSON true counts; a string "false" is false."""
    return body.get("remember") is True


def issue_session(ident: Identity, sessions: Sessions, response: Response, user: dict, *,
                  amr: str, request: Request, persistent: bool = False,
                  auth_provider: str | None = None, describe=None) -> dict:
    """Start a central session and set both cookies.

    A remembered session gets cookies with an expiry (bounded by the session's absolute
    deadline); otherwise they are browser-session cookies. The server-side clocks apply in both
    cases, so a cookie that outlives its row is simply refused.
    """
    spaces = ident.workspaces_for(user["id"])
    raw, row = sessions.create(user["id"], workspace_id=spaces[0]["id"] if spaces else None, amr=amr,
                               user_agent=request.headers.get("user-agent"), ip=client_ip(request),
                               admin=user["platform_role"] == "admin", persistent=persistent,
                               auth_provider=auth_provider)
    secure = cookies_secure()
    max_age = sessions.cookie_max_age(row)
    response.set_cookie(COOKIE_NAME, raw, max_age=max_age, **cookie_kwargs(secure))
    # Readable by script on purpose: the page has to echo it in a header. It is not a
    # credential on its own — it only proves the request came from our own page.
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=max_age, httponly=False, secure=secure,
                        samesite="lax", path="/")
    user_view = describe(user, spaces, row) if describe else _public_user(ident, user)
    return {"user": user_view, "session": {"id": row["id"], "amr": amr, "persistent": bool(row["persistent"])}}


def _issue_session(ident: Identity, sessions: Sessions, response: Response, user: dict, *,
                   amr: str, request: Request) -> dict:
    return issue_session(ident, sessions, response, user, amr=amr, request=request)


RESET_TTL_H = 2
VERIFY_TTL_H = 24


def build_recovery_router(ident: Identity, sessions: Sessions, invites, mailer, mfa,
                          audit) -> APIRouter:
    """Forgetting a password, and proving an address.

    Both halves of each flow are deliberately shaped:

    * **Requesting a reset always answers the same way.** Whether or not the address has an
      account, the response is identical — otherwise this endpoint is an account-existence
      oracle that needs no authentication at all.
    * **The link is not the action.** Opening it only *reads* the token; spending it takes a
      second, deliberate POST. Mail security scanners follow links in messages, and a token
      consumed by a scanner's GET is a reset the person never asked for and can no longer do.
    * **A reset is not a sign-in.** It sets the password and ends every session; the person then
      signs in normally, which means any second factor they have still applies. Signing them in
      here would make "I forgot my password" a way around their own MFA.
    """
    from synwevo_core.passwords import PasswordError
    r = APIRouter(prefix="/api/auth", tags=["auth"])

    @r.post("/forgot")
    def forgot(request: Request, body: dict = Body(...)):
        email = (body.get("email") or "").strip()
        same = {"ok": True, "message": "如果这个邮箱有可用账户且邮件服务正常，重置请求将进入发送队列，请稍后检查邮箱。"}
        if not email:
            return same
        # The challenge is the one thing here that is *not* the same answer: a request without
        # a solved token is refused outright, because sending nothing silently would teach a
        # person that the form is broken while a bot learns nothing either way.
        _challenge(request).require(request, body, "forgot", audit=audit)
        # Throttled without changing the answer: the response shape is the security property
        # here, so a throttled request still gets `same` — it just does not send anything or
        # invalidate anything. Unthrottled, anyone who knew an address could flood it with mail
        # and keep killing the owner's real reset link, since each request supersedes the last.
        # Serialize admission, per-account gap, token replacement and outbox enqueue.
        # A concurrent request must see the first committed reset before deciding.
        with ident.store.tx():
            limit, window = FORGOT_PER_ADDRESS
            allowed, _ = ident.window_allows(f"forgot:{client_ip(request)}", limit=limit, window_s=window)
            if not allowed:
                audit(request, "auth.reset.request", result="denied", meta={"reason": "address_throttled"})
                return same
            user = ident.by_email(email)
            # Recorded either way, so the audit log shows attempts against addresses with no
            # account — which is what a scan of this endpoint looks like.
            if not user or user["status"] not in ("active", "pending"):
                audit(request, "auth.reset.request", result="denied",
                      meta={"reason": "no_such_account"})
                return same
            n_max, per = FORGOT_PER_ACCOUNT
            recent = ident.store.one(
                "SELECT COUNT(*) AS n, MAX(created_at) AS last FROM email_tokens "
                "WHERE user_id=? AND kind='reset' AND created_at>?", (user["id"], now_ts() - per))
            if recent["n"] >= n_max or (recent["last"] and now_ts() - recent["last"] < FORGOT_MIN_GAP_S):
                audit(request, "auth.reset.request", actor_id=user["id"], object_kind="user",
                      object_id=user["id"], result="denied", meta={"reason": "account_throttled"})
                return same
            token = invites.issue_email_token(user["id"], "reset", ttl_s=RESET_TTL_H * 3600)
            subject, text = mail_reset(token=token, expires_h=RESET_TTL_H, locale=user.get("locale") or "zh-Hans")
            try:
                mailer.enqueue(user["email"], subject, text, kind="reset", ref_id=user["id"])
            except ServiceError as e:
                # Swallowed *here specifically*, and nowhere else. `enqueue` raises on purpose so an
                # invitation nobody can receive is not reported as sent — but on this endpoint the
                # response shape is itself the security property. Letting the refusal through would
                # answer 503 for an address that has an account and 200 for one that does not,
                # turning the one endpoint built to resist enumeration into a perfect oracle for it.
                # The failure is recorded loudly instead of being lost.
                audit(request, "auth.reset.request", actor_id=user["id"], object_kind="user",
                      object_id=user["id"], result="error", meta={"mail_error": e.code})
                return same
            audit(request, "auth.reset.request", actor_id=user["id"], object_kind="user",
                  object_id=user["id"], result="ok")
            return same

    @r.get("/reset/preview")
    def reset_preview(request: Request, token: str = ""):
        """Read-only, for the landing page. Spending happens on the POST below."""
        row = invites.peek_email_token(token.strip(), "reset")
        if not row:
            raise ServiceError(410, "bad_token", "链接无效或已过期，请重新申请")
        user = ident.get_user(row["user_id"])
        if not user:
            raise ServiceError(410, "bad_token", "链接无效或已过期，请重新申请")
        return {"email": user["email"], "expires_at": row["expires_at"]}

    @r.post("/reset/preview")
    def reset_preview_body(request: Request, body: dict = Body(...)):
        token = body.get("token")
        if not isinstance(token, str) or len(token) > 256:
            raise ServiceError(410, "bad_token", "链接无效或已过期，请重新申请")
        return reset_preview(request, token)

    @r.post("/reset")
    def reset(request: Request, body: dict = Body(...)):
        token = (body.get("token") or "").strip()
        password = body.get("password") or ""
        if not token or not password:
            raise ServiceError(400, "missing_fields", "请提供链接令牌与新口令")
        row = invites.consume_email_token(token, "reset")
        user = ident.get_user(row["user_id"])
        if not user or user["status"] == "suspended":
            raise ServiceError(403, "account_unavailable", "账户不可用，请联系管理员")
        try:
            ident.set_password(user["id"], password)
        except PasswordError as e:
            # The token is already spent — it had to be, to make it single use under
            # concurrency. Say so plainly rather than let someone retype into a dead link.
            raise ServiceError(400, "weak_password",
                               f"{e}（这个链接已失效，请重新申请一封邮件）", {"field": "password"}) from None

        # A credential change ends everything that was authorised by the old one.
        n = sessions.revoke_all_for_user(user["id"], "password_reset")
        from synwevo_core.oidc import Grants
        tokens = Grants(ident.store).revoke_for_user(user["id"], "password_reset")
        from .withdrawals import record as journal
        journaled = journal(ident.store, "password_changed", user_id=user["id"])
        audit(request, "auth.reset.complete", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok",
              meta={"sessions": n, "tokens": tokens, "journaled": journaled})
        try:
            subject, text = mail_notice(what="口令已重置", when_text="刚刚", locale=user.get("locale") or "zh-Hans")
            mailer.enqueue(user["email"], subject, text, kind="security", ref_id=user["id"])
        except Exception:                      # a notice that fails must not undo the reset
            pass
        # Deliberately not signed in: the next sign-in goes through the normal path, so a second
        # factor still applies. Otherwise a reset would be a way around the person's own MFA.
        return {"ok": True, "sessions_revoked": n,
                "mfa_required": mfa.enabled_for(user["id"]) if mfa else False}

    @r.post("/verify-email/send")
    def verify_send(request: Request):
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        user = ident.get_user(s["user_id"])
        if not user:
            raise ServiceError(401, "unauthorized", "请先登录")
        if user.get("email_verified_at"):
            return {"ok": True, "already_verified": True}
        with ident.store.tx():
            recent = ident.store.one("SELECT COUNT(*) AS n, MAX(created_at) AS last FROM email_tokens "
                                     "WHERE user_id=? AND kind='verify' AND created_at>?", (user["id"], now_ts() - 3600))
            if recent["n"] >= 3 or (recent["last"] and now_ts() - recent["last"] < 60):
                raise _too_many(60)
            token = invites.issue_email_token(user["id"], "verify", ttl_s=VERIFY_TTL_H * 3600, new_email=user["email"])
            subject, text = mail_verify(token=token, expires_h=VERIFY_TTL_H, locale=user.get("locale") or "zh-Hans")
            mailer.enqueue(user["email"], subject, text, kind="verify", ref_id=user["id"])
        audit(request, "auth.email.verify_sent", actor_id=user["id"], result="ok")
        return {"ok": True, "expires_h": VERIFY_TTL_H}

    @r.post("/verify-email")
    @r.post("/email/verify")
    def verify_email(request: Request, body: dict = Body(...)):
        """The mailbox owner chooses the credential that activates a pending account.

        A pre-registrant can know its old password and hold pending sessions. Merely
        verifying a link must never promote those credentials into active access.
        Active users only verify their current address; this is not a password reset.
        """
        from synwevo_core import passwords
        from .profile import invalidate_access
        token = body.get("token")
        if (set(body) - {"token", "new_password"} or not isinstance(token, str)
                or not 20 <= len(token) <= 256):
            raise ServiceError(409, "bad_token", "链接无效或已过期，请重新申请")
        with ident.store.tx():
            row = invites.peek_email_token(token, "verify")
            if not row:
                raise ServiceError(409, "bad_token", "链接无效或已过期，请重新申请")
            user = ident.get_user(row["user_id"])
            if (not user or user["status"] not in ("pending", "active") or user.get("deleted_at")
                    or row["new_email_key"] != user["email_key"]):
                raise ServiceError(409, "bad_token", "链接无效或已过期，请重新申请")
            pending = user["status"] == "pending"
            phc = None
            if pending:
                replacement = body.get("new_password")
                if not isinstance(replacement, str) or not replacement:
                    raise ServiceError(400, "new_password_required", "请设置由你掌控的新口令以完成账户激活",
                                       {"field": "new_password"})
                try:
                    passwords.check_policy(replacement, email=user["email"],
                                           display_name=user.get("display_name"), single_factor=True)
                    phc = passwords.hash_password(replacement)
                except PasswordError as exc:
                    raise ServiceError(400, "weak_password", str(exc), {"field": "new_password"}) from None
            elif "new_password" in body:
                raise ServiceError(400, "invalid_field", "已激活账户的邮箱验证不会修改口令",
                                   {"field": "new_password"})
            invites.consume_email_token(token, "verify")
            if pending:
                ident.store.execute("INSERT INTO user_passwords (user_id,phc,updated_at) VALUES (?,?,?) "
                                    "ON CONFLICT(user_id) DO UPDATE SET phc=excluded.phc,updated_at=excluded.updated_at",
                                    (user["id"], phc, now_ts()))
                invalidate_access(ident.store, user["id"], "account_verified")
                # Historical pending users may carry factors enrolled before the pending gate
                # existed. Those factors belong to the pre-registrant, not the mailbox owner.
                for table in ("passkeys", "webauthn_credentials", "totp_secrets", "recovery_codes"):
                    ident.store.execute(f"DELETE FROM {table} WHERE user_id=?", (user["id"],))
            ident.store.execute("UPDATE users SET email_verified_at=?, status='active', updated_at=? WHERE id=?",
                                (now_ts(), now_ts(), row["user_id"]))
        audit(request, "auth.email.verified", actor_id=row["user_id"], object_kind="user",
              object_id=row["user_id"], result="ok")
        if pending:
            from .withdrawals import record as journal
            journal(ident.store, "password_changed", user_id=row["user_id"])
        return {"ok": True, "sign_in_required": pending}

    return r


def build_passkey_router(ident: Identity, sessions: Sessions, passkeys, audit) -> APIRouter:
    """Passkey management for one's own account, plus the sign-in ceremony.

    Enrolment and removal need a recent, scoped re-authentication; the sign-in half is
    necessarily anonymous, because you cannot prove who you are before signing in.
    """
    r = APIRouter(prefix="/api/auth/passkeys", tags=["auth"])
    SCOPE = "mfa"

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def _fresh(request: Request) -> dict:
        # strict=True: adding or removing a way into the account is exactly where the prompt is
        # the point. Without it, someone who signed in a minute ago would never be asked — and
        # a confirmation that can be silently skipped is a confirmation in name only.
        s = _session(request)
        if not Sessions.reauth_fresh(s, SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这是敏感操作，请重新输入口令确认",
                               {"scope": SCOPE})
        return s

    @r.get("")
    def listing(request: Request):
        from .passkeys import allowed_origins, rp_id
        s = _session(request)
        return {"items": passkeys.list_for(s["user_id"]),
                "reauth_fresh": Sessions.reauth_fresh(s, SCOPE, strict=True),
                # Shown in the UI so a domain change is visible as a fact rather than as
                # credentials that mysteriously stop being offered.
                "rp_id": rp_id(), "origins": allowed_origins()}

    @r.post("/register/begin")
    def register_begin(request: Request):
        s = _fresh(request)
        user = ident.get_user(s["user_id"])
        if not user:
            raise ServiceError(401, "unauthorized", "请先登录")
        return passkeys.begin_registration(user)

    @r.post("/register/finish")
    def register_finish(request: Request, body: dict = Body(...)):
        s = _fresh(request)
        user = ident.get_user(s["user_id"])
        out = passkeys.finish_registration(user, body.get("credential") or {},
                                           name=body.get("name"))
        audit(request, "auth.passkey.register", actor_id=user["id"], object_kind="passkey",
              object_id=out["id"], result="ok")
        notify_security_change(request, user, "新增了一个安全密钥（Passkey）")
        return out

    @r.post("/authenticate/begin")
    def auth_begin(request: Request, body: dict = Body(default={})):
        """Anonymous by necessity. No credential list is returned for an address, because that
        would answer "does this address have an account" — which the rest of sign-in avoids.

        Limited per source address: each call writes a challenge row, and an unlimited anonymous
        writer is a way to grow the one database everything shares."""
        limit, window = PASSKEY_BEGIN_PER_ADDRESS
        allowed, wait = ident.window_allows(f"pkbegin:{client_ip(request)}", limit=limit, window_s=window)
        if not allowed:
            raise _too_many(wait)
        return passkeys.begin_authentication(None)

    @r.patch("/{pid}")
    def rename(request: Request, pid: str, body: dict = Body(...)):
        s = _session(request)
        passkeys.rename(s["user_id"], pid, body.get("name") or "")
        audit(request, "auth.passkey.rename", actor_id=s["user_id"], object_kind="passkey",
              object_id=pid, result="ok")
        return {"ok": True}

    @r.delete("/{pid}")
    def remove(request: Request, pid: str):
        """Removing the last way in is refused, not confirmed away.

        Someone with a passkey and no password would lock themselves out permanently, and the
        dialog they clicked through would be the only record of how.
        """
        s = _fresh(request)
        has_password = ident.store.one("SELECT 1 FROM user_passwords WHERE user_id=?",
                                       (s["user_id"],))
        if not has_password and passkeys.count(s["user_id"]) <= 1:
            raise ServiceError(409, "last_factor",
                               "这是你唯一的登录方式，删除后将无法登录。请先设置口令或再登记一个安全密钥。")
        passkeys.remove(s["user_id"], pid)
        # A key removed because it was lost must stay removed after a restore from backup.
        from .withdrawals import record as journal
        journaled = journal(ident.store, "passkey_removed", credential_id=pid)
        audit(request, "auth.passkey.remove", actor_id=s["user_id"], object_kind="passkey",
              object_id=pid, result="ok", meta={"journaled": journaled})
        notify_security_change(request, ident.get_user(s["user_id"]), "删除了一个安全密钥（Passkey）")
        return {"ok": True}

    return r


def build_mfa_router(ident: Identity, sessions: Sessions, mfa, audit) -> APIRouter:
    """Second-factor management for one's own account.

    Every change here is gated on a *recent* re-authentication recorded server-side, scoped to
    this purpose. Proving yourself to add a factor must not also unlock removing one, and a
    front-end flag would unlock nothing at all.
    """
    from synwevo_core.sessions import REAUTH_SECONDS

    r = APIRouter(prefix="/api/auth/mfa", tags=["auth"])
    SCOPE = "mfa"

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def _fresh(request: Request, *, strict: bool = False) -> dict:
        s = _session(request)
        if not Sessions.reauth_fresh(s, SCOPE, strict=strict):
            raise ServiceError(403, "reauth_required",
                               "这是敏感操作，请重新输入口令确认", {"scope": SCOPE})
        return s

    @r.post("/reauth")
    def reauth(request: Request, body: dict = Body(...)):
        """Re-prove the password, for this purpose only and for a few minutes."""
        s = _session(request)
        check_reauth_password(ident, sessions, request, s, body.get("password") or "", audit, SCOPE)
        sessions.mark_reauth(s["id"], SCOPE)
        amr = sessions.add_factor(s["id"], "password")
        audit(request, "auth.reauth", actor_id=s["user_id"], result="ok", meta={"scope": SCOPE, "amr": amr})
        return {"ok": True, "valid_for_s": REAUTH_SECONDS, "amr": amr}

    @r.get("")
    def state(request: Request):
        s = _session(request)
        f = mfa.factors(s["user_id"])
        return {**f, "reauth_fresh": Sessions.reauth_fresh(s, SCOPE)}

    # ------------------------------------------------------------------ TOTP
    @r.post("/totp/begin")
    def totp_begin(request: Request):
        s = _fresh(request)
        user = ident.get_user(s["user_id"])
        out = mfa.begin_totp(s["user_id"], account_label=user["email"])
        audit(request, "mfa.totp.begin", actor_id=s["user_id"], result="ok")
        return out

    @r.post("/totp/confirm")
    def totp_confirm(request: Request, body: dict = Body(...)):
        s = _fresh(request)
        mfa.confirm_totp(s["user_id"], body.get("code") or "")
        audit(request, "mfa.totp.enable", actor_id=s["user_id"], result="ok")
        # Recovery codes are issued with the first factor, not left for later: the moment MFA
        # starts mattering is the moment losing the phone starts mattering.
        codes = mfa.generate_recovery_codes(s["user_id"])
        notify_security_change(request, ident.get_user(s["user_id"]), "启用了动态验证码（两步验证）")
        return {"ok": True, "recovery_codes": codes}

    @r.post("/totp/disable")
    def totp_disable(request: Request, body: dict = Body(...)):
        s = _fresh(request)
        # Removing a factor is guarded at least as strongly as adding one, and it also takes a
        # current code: someone who has your unlocked laptop should not be able to strip MFA
        # without the thing MFA is made of.
        if not mfa.verify_totp(s["user_id"], body.get("code") or ""):
            raise ServiceError(400, "bad_code", "请输入当前的动态验证码以确认关闭", {"field": "code"})
        mfa.disable_totp(s["user_id"])
        audit(request, "mfa.totp.disable", actor_id=s["user_id"], result="ok")
        notify_security_change(request, ident.get_user(s["user_id"]), "关闭了动态验证码（两步验证）")
        return {"ok": True, **mfa.factors(s["user_id"])}

    # ------------------------------------------------------------------ recovery codes
    @r.post("/recovery/regenerate")
    def recovery_regen(request: Request):
        # strict: regenerating replaces every code the person wrote down, so "signed in a
        # moment ago" is not enough — the password has to be re-entered for this purpose.
        # Otherwise anyone at an unlocked, freshly signed-in browser could invalidate the
        # owner's way back in and walk away with a working set.
        s = _fresh(request, strict=True)
        if not mfa.enabled_for(s["user_id"]):
            raise ServiceError(409, "no_mfa", "还没有启用任何第二因素，恢复码暂时没有用处")
        codes = mfa.generate_recovery_codes(s["user_id"])
        audit(request, "mfa.recovery.regenerate", actor_id=s["user_id"], result="ok",
              meta={"count": len(codes)})
        notify_security_change(request, ident.get_user(s["user_id"]), "重新生成了恢复码，旧的恢复码已全部失效")
        return {"recovery_codes": codes}

    return r
