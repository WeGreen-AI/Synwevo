"""Risk-triggered human verification (Cloudflare Turnstile), verified on the server.

Why this exists: registration, password recovery and mailed sign-in codes are anonymous
endpoints whose cost is a mail and a database row. Address and per-account windows already
bound them (10 per source address per 15 minutes, 3 per address per hour), but a bot with many
addresses stays under every window. A challenge token makes each of those requests cost a real
browser interaction, and the *server* decides whether the token is good — a checkbox or a
``verified: true`` field from the client proves nothing.

Contract (Cloudflare Turnstile "Server-side validation", developers.cloudflare.com, read
2026-09-25): ``POST https://challenges.cloudflare.com/turnstile/v0/siteverify`` with ``secret``,
``response`` (≤ 2048 characters), optional ``remoteip`` and ``idempotency_key`` (a UUID that
lets a retry return the first result instead of ``timeout-or-duplicate``); the answer carries
``success``, ``challenge_ts``, ``hostname``, ``action``, ``cdata`` and ``error-codes``. A token
is valid for 300 seconds and can be validated once. The action and hostname the widget was
solved for are checked here, so a token minted for the recovery form cannot pay for a
registration and a token solved on another site cannot pay for anything.

Policy (see docs/security/human-verification.md):

* ``SYNWEVO_TURNSTILE_SITE_KEY`` / ``SYNWEVO_TURNSTILE_SECRET`` — both or neither. The secret
  never leaves this process: it is not in ``/api/auth/state``, not in logs, not in audit rows.
* ``SYNWEVO_TURNSTILE_HOSTNAMES`` — comma-separated hostnames the widget may be solved on.
  Default: the host of ``SYNWEVO_ISSUER`` (the account service's own origin).
* ``SYNWEVO_TURNSTILE_TIMEOUT_S`` — per siteverify call (default 5); one retry with the same
  idempotency key on a network error or ``internal-error``.

Failure policy: when the verification service cannot be reached, the actions that need a
challenge are refused with 503 ``challenge_unavailable`` and a ``retry_after`` — an outage never
waves a high-risk action through. Everything that does not need a challenge is untouched.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import env
from .errors import ServiceError

SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
WIDGET_ORIGIN = "https://challenges.cloudflare.com"
TOKEN_MAX = 2048
TOKEN_TTL_S = 300
RETRY_AFTER_S = 30
#: The actions this platform challenges. ``always`` = every request; ``risk`` = once the
#: server's own signals say so (for ``login``: repeated failures from the source address).
ACTIONS = {"register": "always", "resend": "always", "forgot": "always", "login_email": "always", "login": "risk"}


class ChallengeUnavailable(Exception):
    """siteverify could not answer (network, timeout, 5xx, internal-error, bad secret)."""


@dataclass
class Verdict:
    ok: bool
    #: ok | missing | malformed | invalid | spent | hostname | action | expired | failed
    reason: str = "ok"
    hostname: str | None = None
    action: str | None = None
    error_codes: list[str] = field(default_factory=list)


@dataclass
class Policy:
    site_key: str | None
    secret: str | None
    hostnames: frozenset[str]
    timeout_s: float

    @property
    def enabled(self) -> bool:
        return bool(self.site_key and self.secret)

    def public(self) -> dict | None:
        """What a sign-in screen may know: the site key and which actions are challenged."""
        if not self.enabled:
            return None
        return {"provider": "turnstile", "site_key": self.site_key, "actions": dict(ACTIONS)}


def policy() -> Policy:
    site_key = (env.get("TURNSTILE_SITE_KEY") or "").strip() or None
    secret = (env.get("TURNSTILE_SECRET") or "").strip() or None
    if bool(site_key) != bool(secret):
        raise ValueError("SYNWEVO_TURNSTILE_SITE_KEY and SYNWEVO_TURNSTILE_SECRET must be set together")
    raw = env.get("TURNSTILE_HOSTNAMES") or ""
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    if not hosts:
        issuer = urlsplit(env.get("ISSUER") or "")
        if issuer.hostname:
            hosts = {issuer.hostname.lower()}
    return Policy(site_key, secret, frozenset(hosts), float(env.get("TURNSTILE_TIMEOUT_S") or 5))


def _http_transport(form: dict, timeout_s: float) -> tuple[int, dict]:
    """POST the form to siteverify; returns (status, json). Raises on transport failure."""
    import httpx
    with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False) as c:
        r = c.post(SITEVERIFY, data=form)
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    return r.status_code, body if isinstance(body, dict) else {}


class Turnstile:
    """One verifier per process. ``transport`` is injectable for tests; the default speaks HTTPS."""

    def __init__(self, pol: Policy, transport=None, clock=None):
        self.policy = pol
        self.transport = transport or _http_transport
        self.clock = clock or time.time

    @property
    def enabled(self) -> bool:
        return self.policy.enabled

    def public(self) -> dict | None:
        return self.policy.public()

    # ------------------------------------------------------------------ verification
    def verify(self, token, *, action: str, remoteip: str | None = None) -> Verdict:
        if token is None or token == "":
            return Verdict(False, "missing")
        if not isinstance(token, str) or len(token) > TOKEN_MAX or any(ord(ch) < 32 for ch in token):
            return Verdict(False, "malformed")
        form = {"secret": self.policy.secret, "response": token, "idempotency_key": str(uuid.uuid4())}
        if remoteip:
            form["remoteip"] = remoteip
        body = self._call(form)
        codes = [str(c) for c in (body.get("error-codes") or [])]
        if body.get("success") is not True:
            if "timeout-or-duplicate" in codes:
                return Verdict(False, "spent", error_codes=codes)
            if "invalid-input-secret" in codes or "missing-input-secret" in codes:
                # An operator problem, not the visitor's: refusing with "try again" would be a
                # lie, and letting it through would be worse. Surface it as an outage.
                raise ChallengeUnavailable("siteverify rejected the configured secret")
            if "internal-error" in codes:
                raise ChallengeUnavailable("siteverify internal-error")
            return Verdict(False, "invalid", error_codes=codes)
        hostname = (body.get("hostname") or "").lower() or None
        got_action = body.get("action") or None
        if self.policy.hostnames and hostname not in self.policy.hostnames:
            return Verdict(False, "hostname", hostname=hostname, action=got_action)
        if got_action != action:
            return Verdict(False, "action", hostname=hostname, action=got_action)
        ts = _parse_ts(body.get("challenge_ts"))
        if ts is not None and self.clock() - ts > TOKEN_TTL_S:
            return Verdict(False, "expired", hostname=hostname, action=got_action)
        return Verdict(True, "ok", hostname=hostname, action=got_action)

    def _call(self, form: dict) -> dict:
        """One retry, same idempotency key, so a retried token is not reported as spent."""
        last: Exception | None = None
        for attempt in range(2):
            try:
                status, body = self.transport(form, self.policy.timeout_s)
            except Exception as e:  # noqa: BLE001 - network, DNS, TLS, timeout
                last = e
            else:
                if status >= 500:
                    last = ChallengeUnavailable(f"siteverify HTTP {status}")
                elif "internal-error" in [str(c) for c in (body.get("error-codes") or [])]:
                    last = ChallengeUnavailable("siteverify internal-error")
                else:
                    return body
            if attempt == 0:
                time.sleep(0.5)
        raise ChallengeUnavailable(f"{type(last).__name__}: {last}"[:200])

    # ------------------------------------------------------------------ route helper
    def require(self, request, body: dict, action: str, *, audit=None) -> None:
        """Enforce the challenge for one request, or return when this policy is off.

        Raises 400 ``challenge_required`` (no token), 400 ``challenge_failed`` (bad token; the
        ``reason`` names why) or 503 ``challenge_unavailable`` (the verifier is down). The token
        itself is never echoed and never audited.
        """
        if not self.enabled:
            return
        from .http import client_ip
        token = body.get("challenge_token") if isinstance(body, dict) else None
        ip = client_ip(request)
        try:
            v = self.verify(token, action=action, remoteip=ip)
        except ChallengeUnavailable as e:
            if audit:
                audit(request, "auth.challenge", result="error", meta={"action": action, "error": str(e)[:120]})
            raise ServiceError(503, "challenge_unavailable", "人机验证服务暂不可用，请稍后重试",
                               {"retry_after": RETRY_AFTER_S, "action": action}) from None
        if v.ok:
            if audit:
                audit(request, "auth.challenge", result="ok", meta={"action": action, "hostname": v.hostname})
            return
        if audit:
            audit(request, "auth.challenge", result="denied",
                  meta={"action": action, "reason": v.reason, "hostname": v.hostname, "token_action": v.action,
                        "codes": v.error_codes[:4]})
        if v.reason == "missing":
            raise ServiceError(400, "challenge_required", "请先完成人机验证", {"action": action})
        raise ServiceError(400, "challenge_failed", "人机验证未通过，请重试", {"action": action, "reason": v.reason})


def _parse_ts(value) -> float | None:
    """``challenge_ts`` is ISO 8601 (``2026-09-25T14:20:11.123Z``); None when absent or odd."""
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime, timezone
    try:
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None
