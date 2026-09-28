"""Sign in with a one-time code sent to the account's e-mail address.

A second way in for people who do not have their password to hand and do not use a passkey on
this device. Three things it deliberately is not:

* **Not a link.** Mail security scanners follow links; a GET that signed someone in would sign
  the scanner in. A six-digit code has to be typed into the page the person already has open,
  on the device they are actually using, so nothing a scanner does can complete a sign-in.
* **Not a way around a second factor.** The code proves control of the mailbox — one factor.
  An account with TOTP enabled still goes through the second step (`/api/auth/login/mfa`) and
  the session records `email+totp`. A platform administrator without TOTP gets an ordinary
  one-factor session that cannot use the role (`sessions.admin_capable`) until the password is
  re-entered on the account page or a second factor is enrolled and used.
* **Not an account-existence oracle.** Requesting a code answers the same way for every address,
  and the limiter counts unknown addresses too. Only the audit log knows the difference.

Codes live in `login_codes`, hashed with the row id as salt, expire after ten minutes, are single
use, and die after five wrong guesses; the per-account backoff bucket shared with TOTP applies on
top, so a burst of parallel guesses cannot beat the counter.
"""
from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Body, Request, Response

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, normalise_email, valid_email
from synwevo_core.sessions import Sessions
from synwevo_core.store import new_id, now

from .auth_routes import MFA_BACKOFF, _challenge, _too_many, client_ip, issue_session, remember_choice
from .mail import login_code_mail, mode as mail_mode

CODE_TTL_S = 10 * 60
CODE_MAX_ATTEMPTS = 5
CODE_DIGITS = 6
#: Requests per source address per 15 minutes; a sender that ignores the limit just gets the
#: same acknowledgement without a mail.
START_PER_ADDRESS = (10, 900)
#: At most three codes an hour to one account, and never two within a minute.
START_PER_ACCOUNT = (3, 3600)
START_MIN_GAP_S = 60
VERIFY_BUCKET = "login-code:{uid}"


def _hash(row_id: str, code: str) -> str:
    return hashlib.sha256(f"{row_id}:{code}".encode("utf-8")).hexdigest()


def _acknowledged() -> dict:
    """Identical for known and unknown addresses; says a mail was *requested*, never delivered."""
    return {"status": "requested", "delivery": "unavailable" if mail_mode() == "disabled" else "requested",
            "expires_min": CODE_TTL_S // 60}


def build_router(ident: Identity, sessions: Sessions, mailer, mfa, tickets, audit,
                 describe=None) -> APIRouter:
    r = APIRouter(prefix="/api/auth/login/email", tags=["auth"])

    def eligible(user: dict | None) -> tuple[dict | None, str | None]:
        """Whether this account may sign in with a mailed code. Returns (user, denial reason)."""
        if not user or user["status"] != "active" or not user.get("email_verified_at"):
            return None, "no_such_account"
        # Platform administrators may sign in this way too. Mailbox control is one factor, so
        # the session they get cannot use the role (sessions.admin_capable) until a second
        # factor is proved: TOTP at sign-in when enrolled, or the password on the account page.
        return user, None

    @r.post("/start", status_code=202)
    def start(request: Request, body: dict = Body(...)):
        email = body.get("email")
        if not isinstance(email, str) or len(email) > 254 or not valid_email(email):
            raise ServiceError(400, "bad_email", "请输入有效的邮箱地址", {"field": "email"})
        if set(body) - {"email", "challenge_token"}:
            raise ServiceError(400, "invalid_field", "请求包含不支持的字段")
        # A mailed code is a mail per request; the challenge is what makes the request cost a
        # real browser interaction (docs/security/human-verification.md).
        _challenge(request).require(request, body, "login_email", audit=audit)
        with ident.store.tx():
            limit, window = START_PER_ADDRESS
            allowed, _ = ident.window_allows(f"login-code:{client_ip(request)}", limit=limit, window_s=window)
            if not allowed:
                audit(request, "auth.login.code.request", result="denied", meta={"reason": "address_throttled"})
                return _acknowledged()
            # Unknown addresses count too, under a hash so the limiter never stores the address.
            key = hashlib.sha256(normalise_email(email).encode()).hexdigest()
            n_max, per = START_PER_ACCOUNT
            allowed, _ = ident.window_allows(f"login-code-email:{key}", limit=n_max, window_s=per)
            if not allowed:
                audit(request, "auth.login.code.request", result="denied", meta={"reason": "account_throttled"})
                return _acknowledged()
            user, why = eligible(ident.by_email(email))
            if not user:
                audit(request, "auth.login.code.request", result="denied", meta={"reason": why})
                return _acknowledged()
            recent = ident.store.one("SELECT MAX(created_at) AS last FROM login_codes WHERE user_id=?", (user["id"],))
            if recent and recent["last"] and now() - recent["last"] < START_MIN_GAP_S:
                audit(request, "auth.login.code.request", actor_id=user["id"], result="denied",
                      meta={"reason": "account_throttled"})
                return _acknowledged()
            # A new code retires any outstanding one, so an older mail cannot be used after a
            # newer request — and the person only ever has one live code to type.
            ident.store.execute("UPDATE login_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
                                (now(), user["id"]))
            row_id = new_id("lc")
            code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
            ident.store.execute(
                "INSERT INTO login_codes (id,user_id,code_hash,created_at,expires_at,ip,user_agent) "
                "VALUES (?,?,?,?,?,?,?)",
                (row_id, user["id"], _hash(row_id, code), now(), now() + CODE_TTL_S,
                 client_ip(request)[:64], (request.headers.get("user-agent") or "")[:300]))
            subject, text, html = login_code_mail(code=code, expires_min=CODE_TTL_S // 60,
                                            locale=user.get("locale") or "zh-Hans")
            try:
                mailer.enqueue(user["email"], subject, text, kind="login_code", ref_id=user["id"], html=html)
            except ServiceError as e:
                # Same shape whatever happened: the response must not say which addresses have
                # accounts. The failure is loud in the audit log instead.
                audit(request, "auth.login.code.request", actor_id=user["id"], object_kind="user",
                      object_id=user["id"], result="error", meta={"mail_error": e.code})
                return _acknowledged()
            audit(request, "auth.login.code.request", actor_id=user["id"], object_kind="user",
                  object_id=user["id"], result="ok")
            return _acknowledged()

    @r.post("/verify")
    def verify(request: Request, response: Response, body: dict = Body(...)):
        email = body.get("email")
        code = body.get("code")
        remember = remember_choice(body)
        if (not isinstance(email, str) or not isinstance(code, str) or len(email) > 254
                or set(body) - {"email", "code", "remember"}):
            raise ServiceError(400, "bad_code", "验证码不正确")
        code = "".join(ch for ch in code if ch.isdigit())
        if len(code) != CODE_DIGITS:
            raise ServiceError(401, "bad_code", "验证码不正确")
        user, _ = eligible(ident.by_email(email))
        if not user:
            # No row to count against; the address limiter above already bounds this path.
            audit(request, "auth.login.code", result="denied", meta={"reason": "no_such_account"})
            raise ServiceError(401, "bad_code", "验证码不正确")
        bucket = VERIFY_BUCKET.format(uid=user["id"])
        wait = ident.backoff_wait(bucket, MFA_BACKOFF)
        if wait > 0:
            raise _too_many(wait)
        # Everything that must survive a refusal — the spent attempt, the dead code — is written
        # and committed *before* the error is raised. Raising inside the transaction would roll
        # the counter back, and five attempts would really be "as many as fit".
        refusal: tuple[str, str] | None = None
        result: dict | None = None
        with ident.store.tx() as t:
            row = t.execute("SELECT * FROM login_codes WHERE user_id=? AND used_at IS NULL AND expires_at>? "
                            "ORDER BY created_at DESC LIMIT 1", (user["id"], now())).fetchone()
            # The attempt is spent before the comparison, in the same statement that checks the
            # limit, so parallel guesses cannot all pass the check before any of them counted.
            claimed = row and t.execute(
                "UPDATE login_codes SET attempts=attempts+1 WHERE id=? AND used_at IS NULL AND attempts<?",
                (row["id"], CODE_MAX_ATTEMPTS)).rowcount
            if not claimed:
                refusal = ("no_live_code", "code_expired")
            elif not secrets.compare_digest(row["code_hash"], _hash(row["id"], code)):
                left = CODE_MAX_ATTEMPTS - (row["attempts"] + 1)
                if left <= 0:
                    t.execute("UPDATE login_codes SET used_at=? WHERE id=?", (now(), row["id"]))
                refusal = ("bad_code", f"bad_code:{max(0, left)}")
            elif not t.execute("UPDATE login_codes SET used_at=? WHERE id=? AND used_at IS NULL",
                               (now(), row["id"])).rowcount:
                refusal = ("no_live_code", "code_expired")
            else:
                # Re-read inside the transaction: activation, suspension or credential
                # withdrawal racing with this request must win.
                user, _ = eligible(ident.get_user(user["id"]))
                if not user:
                    refusal = ("account_unavailable", "bad_code")
                elif mfa and mfa.enabled_for(user["id"]):
                    ticket = tickets.issue(user["id"], user_agent=request.headers.get("user-agent"),
                                           ip=client_ip(request), first_factor="email")
                    f = mfa.factors(user["id"])
                    audit(request, "auth.login.code", actor_id=user["id"], object_kind="user",
                          object_id=user["id"], result="ok", meta={"stage": "email", "mfa": True})
                    result = {"mfa_required": True, "ticket": ticket,
                              "methods": {"totp": f["totp"], "recovery": f["recovery_remaining"] > 0}}
                else:
                    audit(request, "auth.login.code", actor_id=user["id"], object_kind="user",
                          object_id=user["id"], result="ok", meta={"remember": remember})
                    result = issue_session(ident, sessions, response, user, amr="email", request=request,
                                           persistent=remember, describe=describe)
        if refusal is None:
            ident.backoff_clear(bucket)
            return result
        reason, code_name = refusal
        ident.backoff_fail(bucket)
        audit(request, "auth.login.code", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="denied", meta={"reason": reason})
        if code_name.startswith("bad_code:"):
            left = int(code_name.split(":")[1])
            raise ServiceError(401, "bad_code", "验证码不正确" + (f"，还可尝试 {left} 次" if left > 0 else "，请重新获取"),
                               {"remaining": left, "exhausted": "resend"})
        if code_name == "code_expired":
            raise ServiceError(401, "code_expired", "验证码已失效，请重新获取")
        raise ServiceError(401, "bad_code", "验证码不正确")

    return r
