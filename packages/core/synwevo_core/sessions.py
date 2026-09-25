"""Server-side sessions.

A signed cookie carrying claims would need no table, but this product has to revoke access the
moment a member is removed, an account is suspended or a password changes — and a self-contained
token stays valid until it expires no matter what the database says. So the cookie is an opaque
random value and the state lives in a row that can be deleted.

Only the SHA-256 of the cookie is stored. A dump of `sessions` therefore cannot be replayed as a
login, which matters because backups and support exports touch that table.

Two clocks, both enforced on every request:
  * idle — expires if unused, so an abandoned browser stops being a way in;
  * absolute — expires regardless of use, so a stolen cookie has a bounded life no matter how
    busy the attacker keeps it.

`reauth_at` records that the person re-proved who they are *recently*, on the server. Sensitive
actions check it. It is never inferred from a front-end flag, and it is scoped: re-authenticating
to change an email does not silently unlock deleting a workspace.
"""
from __future__ import annotations

import hashlib
import os
import secrets

from . import env
from .store import Store, new_id, now

COOKIE_NAME = "dw_session"
CSRF_COOKIE = "dw_csrf"
CSRF_HEADER = "X-Synwevo-CSRF"
#: The pre-rebrand header name, still accepted. A page loaded before the rename keeps sending
#: it, and a deploy is not atomic across every open tab — refusing it would log those people
#: out of every state-changing action until they reloaded, which reads as "the app is broken"
#: rather than as "there is a new brand".
LEGACY_CSRF_HEADER = "X-DocWeave-CSRF"

# What a re-authentication can be *for*. A closed set on purpose: the empty scope satisfies
# every check, so it must never be something a request can ask for.
REAUTH_SCOPES = ("mfa", "admin")

# Defaults chosen for a document tool people keep open while they work; overridable per deployment.
IDLE_SECONDS = int(env.get("SESSION_IDLE_S", str(12 * 3600)))
ABSOLUTE_SECONDS = int(env.get("SESSION_ABSOLUTE_S", str(30 * 24 * 3600)))
# "Keep me signed in": a longer idle window and a persistent cookie. The absolute clock is the
# same — remembering a device never turns into a session that cannot expire. Platform
# administrators keep their short leash: the option is accepted and ignored for them.
PERSISTENT_IDLE_SECONDS = int(env.get("SESSION_PERSISTENT_IDLE_S", str(14 * 24 * 3600)))
# Sudo-style window. Long enough to finish a settings page, short enough that walking away ends it.
REAUTH_SECONDS = int(env.get("REAUTH_S", str(10 * 60)))
# Admin sessions get a shorter leash than ordinary ones — §4.2 of docs/security/permissions.md.
ADMIN_IDLE_SECONDS = int(env.get("ADMIN_IDLE_S", str(60 * 60)))
# How stale the stored idle deadline may be before a request rewrites it. See load().
TOUCH_SLACK_SECONDS = 60


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def factor_kinds(amr: str) -> set[str]:
    """The distinct kinds of factor an `amr` value records ("password+totp" -> {password, totp})."""
    kinds: set[str] = set()
    for part in (amr or "").split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("idp:"):
            kinds.add("idp")
        elif part == "passkey_uv":
            kinds.update({"passkey", "user_verification"})
        else:
            kinds.add(part)
    return kinds


def admin_capable(amr: str) -> bool:
    """May a session authenticated this way exercise platform administration?

    The rule keeps every existing administrator path (a password sign-in, a verified passkey,
    any password + second factor pair) and restricts only the newer single-factor paths: a
    mailed code or an external provider proves one thing, so such a session may manage the
    person's own account but not the platform until a second, distinct factor is proved — a
    TOTP code at sign-in, or the password re-entered on the account page (`Sessions.add_factor`).
    Nothing here grants a role; it only says whether the role may be *used* in this session.
    """
    kinds = factor_kinds(amr)
    if not kinds:
        return False
    if "user_verification" in kinds:
        return True
    if kinds == {"password"}:
        return True
    return len(kinds - {"user_verification"}) >= 2


class Sessions:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ lifecycle
    def create(self, user_id: str, *, workspace_id: str | None, amr: str,
               user_agent: str | None = None, ip: str | None = None,
               admin: bool = False, oidc_sid: str | None = None,
               central_session_id: str | None = None, auth_time: float | None = None,
               persistent: bool = False, auth_provider: str | None = None) -> tuple[str, dict]:
        """Return (cookie value, row). The caller sets the cookie; the raw value is never stored.

        Always called *after* authentication succeeds and never reuses an id the client supplied,
        so a session fixed in advance by an attacker cannot be promoted into an authenticated one.

        `persistent` is the person's "keep me signed in" choice: a longer idle window and, at the
        caller, a cookie with an expiry instead of a browser-session cookie. It is ignored for
        platform administrators, whose idle limit stays short by policy.
        """
        raw = secrets.token_urlsafe(32)
        sid = _digest(raw)
        ts = now()
        persistent = bool(persistent) and not admin
        idle = ADMIN_IDLE_SECONDS if admin else (PERSISTENT_IDLE_SECONDS if persistent else IDLE_SECONDS)
        row = {
            "id": sid, "user_id": user_id, "workspace_id": workspace_id,
            "created_at": ts, "last_seen_at": ts,
            "idle_expires_at": ts + min(idle, ABSOLUTE_SECONDS), "absolute_expires_at": ts + ABSOLUTE_SECONDS,
            "persistent": 1 if persistent else 0, "auth_provider": auth_provider,
            # A fresh login is itself a recent authentication, scoped to nothing in particular.
            # For a session created by SSO, `auth_time` is when the *central* authentication
            # actually happened. Taking "now" instead would let a sign-in that only replayed an
            # hours-old central session claim to be a fresh re-authentication.
            "reauth_at": auth_time if auth_time is not None else ts, "reauth_scope": "",
            "amr": amr, "user_agent": (user_agent or "")[:300], "ip": (ip or "")[:64],
            "oidc_sid": oidc_sid, "central_session_id": central_session_id,
        }
        with self.store.tx() as t:
            t.execute(
                "INSERT INTO sessions (id,user_id,workspace_id,created_at,last_seen_at,idle_expires_at,"
                "absolute_expires_at,reauth_at,reauth_scope,amr,user_agent,ip,oidc_sid,central_session_id,"
                "persistent,auth_provider) "
                "VALUES (:id,:user_id,:workspace_id,:created_at,:last_seen_at,:idle_expires_at,"
                ":absolute_expires_at,:reauth_at,:reauth_scope,:amr,:user_agent,:ip,:oidc_sid,"
                ":central_session_id,:persistent,:auth_provider)", row)
        return raw, row

    @staticmethod
    def cookie_max_age(row: dict) -> int | None:
        """How long the cookie for this session may live in the browser.

        A remembered session gets a cookie that survives closing the browser, bounded by the
        session's own absolute deadline so the two can never disagree. Everything else is a
        browser-session cookie (no Max-Age): the server-side clocks still apply either way.
        """
        if not row.get("persistent"):
            return None
        return max(60, int(row["absolute_expires_at"] - now()))

    def load(self, raw_cookie: str | None, *, touch: bool = True) -> dict | None:
        """The live session for this cookie, or None. Expiry is enforced here, on read.

        Checking both clocks at read time means an expired row cannot be used even if the sweeper
        has not run — the sweeper only reclaims space.
        """
        if not raw_cookie:
            return None
        row = self.store.one("SELECT * FROM sessions WHERE id=?", (_digest(raw_cookie),))
        if not row or row.get("revoked_at"):
            return None
        ts = now()
        if ts >= row["idle_expires_at"] or ts >= row["absolute_expires_at"]:
            return None
        if touch:
            # Extend the idle clock by *this session's own* window, never the absolute one.
            #
            # The window used to be IDLE_SECONDS for everybody, so an admin session created with
            # a 1-hour idle limit got 12 hours after its first request. The row has no column
            # for the window, but it does not need one: create() writes
            # `idle_expires_at = last_seen_at + window` and every touch below preserves that, so
            # the window is the difference. Once the absolute deadline caps it the difference
            # shrinks, and it stays capped from then on — which is the correct behaviour anyway.
            window = max(0.0, row["idle_expires_at"] - row["last_seen_at"])
            deadline = min(ts + window, row["absolute_expires_at"])
            # A write per request (every asset, every poll) is a write lock taken for nothing
            # when the stored deadline is already within a minute of the right one. Skipping
            # it costs at most a minute of idle time and never extends anything.
            if deadline - row["idle_expires_at"] > TOUCH_SLACK_SECONDS or deadline < row["idle_expires_at"]:
                self.store.execute("UPDATE sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
                                   (ts, deadline, row["id"]))
                row = {**row, "last_seen_at": ts, "idle_expires_at": deadline}
        return row

    def revoke(self, session_id: str, reason: str) -> bool:
        return self.store.execute("UPDATE sessions SET revoked_at=?, revoked_reason=? "
                                  "WHERE id=? AND revoked_at IS NULL", (now(), reason, session_id)) > 0

    def device_root_id(self, session_id: str) -> str | None:
        """Resolve a local/RP session to its original sign-in, including older RP rows.

        Callers performing revocation use this inside their write transaction. The legacy
        linkage is scoped to the same user; a client session from another account never wins.
        """
        row = self.store.one("SELECT user_id,central_session_id,oidc_sid FROM sessions WHERE id=?",
                             (session_id,))
        if not row:
            return None
        if row["central_session_id"]:
            return row["central_session_id"]
        if row["oidc_sid"]:
            found = self.store.one("SELECT central_session_id FROM client_sessions WHERE sid=? AND user_id=?",
                                    (row["oidc_sid"], row["user_id"]))
            if found:
                return found["central_session_id"]
        return session_id

    def revoke_device(self, session_id: str, reason: str = "device_logout", *,
                      rp_reason: str | None = None) -> dict:
        """End this central sign-in and its relying-party sessions, never another device/service.

        All hosted applications share Store, so enforcement is transactional and does not wait
        for a network callback. The next browser request with any old cookie is refused.
        """
        ts = now()
        with self.store.tx() as t:
            row = t.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not row:
                return {"sessions_revoked": 0, "tokens_revoked": 0}
            central = self.device_root_id(session_id)
            n = t.execute("UPDATE sessions SET revoked_at=?,revoked_reason=CASE WHEN id=? THEN ? ELSE ? END WHERE user_id=? "
                          "AND revoked_at IS NULL AND (id=? OR id=? OR central_session_id=? OR oidc_sid IN "
                          "(SELECT sid FROM client_sessions WHERE central_session_id=? AND user_id=?))",
                          (ts, central, reason, rp_reason or reason, row["user_id"], session_id,
                           central, central, central, row["user_id"])).rowcount
            tokens = t.execute("UPDATE oauth_tokens SET revoked_at=?,revoked_reason=? "
                               "WHERE central_session_id=? AND user_id=? AND revoked_at IS NULL",
                               (ts, reason, central, row["user_id"])).rowcount
            t.execute("UPDATE client_sessions SET logged_out_at=? WHERE central_session_id=? AND user_id=?",
                      (ts, central, row["user_id"]))
            return {"sessions_revoked": n, "tokens_revoked": tokens}

    def revoke_all_for_user(self, user_id: str, reason: str, *, except_session: str | None = None) -> int:
        """Used by password change, MFA change, suspension and "sign out everywhere".

        `except_session` keeps the session that just re-authenticated, so changing your own
        password does not log you out of the tab you are typing in while still ending every
        other one.
        """
        sql = "UPDATE sessions SET revoked_at=?, revoked_reason=? WHERE user_id=? AND revoked_at IS NULL"
        args: list = [now(), reason, user_id]
        if except_session:
            sql += " AND id<>?"
            args.append(except_session)
        return self.store.execute(sql, tuple(args))

    def list_for_user(self, user_id: str, *, current_id: str | None = None) -> list[dict]:
        """What the account's session page shows.

        Device and browser are reported only as far as the User-Agent honestly supports, and no
        location is claimed at all: a coarse IP-to-city guess presented as fact is worse than
        nothing when someone is trying to decide whether a session is theirs.
        """
        ts = now()
        rows = self.store.all(
            "SELECT id,created_at,last_seen_at,idle_expires_at,absolute_expires_at,amr,user_agent,ip,"
            "persistent,auth_provider FROM sessions WHERE user_id=? AND revoked_at IS NULL "
            "AND idle_expires_at>? AND absolute_expires_at>? ORDER BY last_seen_at DESC",
            (user_id, ts, ts))
        out = []
        for r in rows:
            out.append({"id": r["id"], "current": r["id"] == current_id,
                        "created_at": r["created_at"], "last_seen_at": r["last_seen_at"],
                        "expires_at": min(r["idle_expires_at"], r["absolute_expires_at"]),
                        "amr": r["amr"], "client": describe_client(r["user_agent"]),
                        "client_parts": client_parts(r["user_agent"]),
                        "ip": r["ip"] or None, "persistent": bool(r.get("persistent")),
                        "auth_provider": r.get("auth_provider")})
        return out

    def sweep(self, older_than_s: float = 7 * 24 * 3600) -> int:
        """Delete rows that have been dead a while. Purely reclamation — nothing depends on it."""
        cutoff = now() - older_than_s
        return self.store.execute(
            "DELETE FROM sessions WHERE (revoked_at IS NOT NULL AND revoked_at<?) "
            "OR absolute_expires_at<?", (cutoff, cutoff))

    # ------------------------------------------------------------------ recent re-authentication
    def mark_reauth(self, session_id: str, scope: str) -> None:
        self.store.execute("UPDATE sessions SET reauth_at=?, reauth_scope=? WHERE id=?",
                           (now(), scope, session_id))

    def add_factor(self, session_id: str, factor: str) -> str:
        """Record that this session proved one more factor (the password, re-entered on the
        account page). `amr` then names every factor actually proved, and a session that began
        with a mailed code becomes `email+password`: two distinct factors, admin-capable."""
        row = self.store.one("SELECT amr FROM sessions WHERE id=?", (session_id,))
        if not row:
            return ""
        amr = row["amr"] or ""
        if factor in factor_kinds(amr) or factor in amr.split("+"):
            return amr
        amr = f"{amr}+{factor}" if amr else factor
        self.store.execute("UPDATE sessions SET amr=? WHERE id=?", (amr, session_id))
        return amr

    @staticmethod
    def reauth_fresh(session: dict, scope: str, *, strict: bool = False) -> bool:
        """Did this session re-authenticate recently, for this purpose?

        The scope check is the point. Proving yourself to add a passkey should not leave a window
        in which deleting the workspace is also unlocked, so a stored scope only satisfies the
        same scope — or an empty stored scope, which is what a fresh login grants.

        `strict` drops that last allowance: the person must have deliberately re-entered their
        password *for this purpose*, not merely have signed in a moment ago. Used for the
        operations where the prompt is the point — reassigning who owns a body of documents,
        suspending an account, changing a platform role. Without it the confirmation an
        administrator sees would be one they could silently never be shown, which is a
        confirmation in name only.
        """
        at = session.get("reauth_at")
        if not at or now() - at > REAUTH_SECONDS:
            return False
        stored = session.get("reauth_scope") or ""
        if strict:
            return stored == scope
        return stored in ("", scope)

    def set_workspace(self, session_id: str, workspace_id: str | None) -> None:
        self.store.execute("UPDATE sessions SET workspace_id=? WHERE id=?", (workspace_id, session_id))


# ---------------------------------------------------------------------- CSRF
def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(cookie_value: str | None, header_value: str | None) -> bool:
    """Double-submit: the header has to echo the cookie.

    Independent of CORS, which governs who may *read* a response and does nothing to stop a
    cross-site form from being *sent*. Compared in constant time so the token cannot be guessed
    byte by byte.
    """
    if not cookie_value or not header_value:
        return False
    # Compared as bytes: compare_digest raises TypeError on a str with non-ASCII characters, and
    # both values arrive from the client, so a header of "é" was an unhandled 500 instead of 403.
    return secrets.compare_digest(cookie_value.encode("utf-8", "surrogateescape"),
                                  header_value.encode("utf-8", "surrogateescape"))


def cookie_kwargs(secure: bool) -> dict:
    """Cookie attributes for the session cookie.

    SameSite=Lax rather than Strict: Strict would drop the cookie on the first request after any
    external link into the app — including the links in verification and invitation email — and
    the person would land on a login page having just clicked "confirm". Lax still withholds the
    cookie from cross-site POSTs, and the CSRF check above covers the rest.

    `secure` is passed in rather than guessed, because getting it wrong in either direction is
    bad: forced on, the cookie silently never sets over plain HTTP on localhost; forced off in
    production, it travels in the clear.
    """
    return {"httponly": True, "secure": secure, "samesite": "lax", "path": "/"}


def client_parts(user_agent: str | None) -> dict:
    """What can honestly be read from a User-Agent: the browser family and the platform, or
    nothing. Product names only — the *sentence* ("macOS 上的 Chrome", "Chrome on macOS") is the
    reader's language and is composed by the page, so the device list translates like the rest
    of the account page instead of being the one Chinese string on an English screen."""
    ua = user_agent or ""
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                                   ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), None)
    # Chrome's UA contains "Safari/"; the order above already handles that.
    platform = next((n for k, n in (("Windows NT", "Windows"), ("Macintosh", "macOS"),
                                    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                    ("Linux", "Linux")) if k in ua), None)
    return {"browser": browser, "platform": platform}


def describe_client(user_agent: str | None) -> str:
    """The legacy one-line description (zh-Hans), kept for clients that still read `client`.
    Returns "未知设备" rather than inventing specificity."""
    parts = client_parts(user_agent)
    browser, platform = parts["browser"], parts["platform"]
    if browser and platform:
        return f"{platform} 上的 {browser}"
    return browser or platform or "未知设备"


def new_session_id() -> str:
    return new_id("s")
