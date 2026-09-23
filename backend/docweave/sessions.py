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

from .store import Store, new_id, now

COOKIE_NAME = "dw_session"
CSRF_COOKIE = "dw_csrf"
CSRF_HEADER = "X-DocWeave-CSRF"

# Defaults chosen for a document tool people keep open while they work; overridable per deployment.
IDLE_SECONDS = int(os.environ.get("DOCWEAVE_SESSION_IDLE_S", str(12 * 3600)))
ABSOLUTE_SECONDS = int(os.environ.get("DOCWEAVE_SESSION_ABSOLUTE_S", str(30 * 24 * 3600)))
# Sudo-style window. Long enough to finish a settings page, short enough that walking away ends it.
REAUTH_SECONDS = int(os.environ.get("DOCWEAVE_REAUTH_S", str(10 * 60)))
# Admin sessions get a shorter leash than ordinary ones — §4.2 of docs/security/permissions.md.
ADMIN_IDLE_SECONDS = int(os.environ.get("DOCWEAVE_ADMIN_IDLE_S", str(60 * 60)))


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Sessions:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ lifecycle
    def create(self, user_id: str, *, workspace_id: str | None, amr: str,
               user_agent: str | None = None, ip: str | None = None,
               admin: bool = False, oidc_sid: str | None = None,
               central_session_id: str | None = None, auth_time: float | None = None) -> tuple[str, dict]:
        """Return (cookie value, row). The caller sets the cookie; the raw value is never stored.

        Always called *after* authentication succeeds and never reuses an id the client supplied,
        so a session fixed in advance by an attacker cannot be promoted into an authenticated one.
        """
        raw = secrets.token_urlsafe(32)
        sid = _digest(raw)
        ts = now()
        idle = ADMIN_IDLE_SECONDS if admin else IDLE_SECONDS
        row = {
            "id": sid, "user_id": user_id, "workspace_id": workspace_id,
            "created_at": ts, "last_seen_at": ts,
            "idle_expires_at": ts + idle, "absolute_expires_at": ts + ABSOLUTE_SECONDS,
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
                "absolute_expires_at,reauth_at,reauth_scope,amr,user_agent,ip,oidc_sid,central_session_id) "
                "VALUES (:id,:user_id,:workspace_id,:created_at,:last_seen_at,:idle_expires_at,"
                ":absolute_expires_at,:reauth_at,:reauth_scope,:amr,:user_agent,:ip,:oidc_sid,"
                ":central_session_id)", row)
        return raw, row

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
            # Extend the idle clock but never the absolute one.
            self.store.execute("UPDATE sessions SET last_seen_at=?, idle_expires_at=? WHERE id=?",
                               (ts, min(ts + IDLE_SECONDS, row["absolute_expires_at"]), row["id"]))
        return row

    def revoke(self, session_id: str, reason: str) -> bool:
        return self.store.execute("UPDATE sessions SET revoked_at=?, revoked_reason=? "
                                  "WHERE id=? AND revoked_at IS NULL", (now(), reason, session_id)) > 0

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
            "SELECT id,created_at,last_seen_at,idle_expires_at,absolute_expires_at,amr,user_agent,ip "
            "FROM sessions WHERE user_id=? AND revoked_at IS NULL "
            "AND idle_expires_at>? AND absolute_expires_at>? ORDER BY last_seen_at DESC",
            (user_id, ts, ts))
        out = []
        for r in rows:
            out.append({"id": r["id"], "current": r["id"] == current_id,
                        "created_at": r["created_at"], "last_seen_at": r["last_seen_at"],
                        "expires_at": min(r["idle_expires_at"], r["absolute_expires_at"]),
                        "amr": r["amr"], "client": describe_client(r["user_agent"]),
                        "ip": r["ip"] or None})
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

    @staticmethod
    def reauth_fresh(session: dict, scope: str) -> bool:
        """Did this session re-authenticate recently, for this purpose?

        The scope check is the point. Proving yourself to add a passkey should not leave a window
        in which deleting the workspace is also unlocked, so a stored scope only satisfies the
        same scope — or an empty stored scope, which is what a fresh login grants.
        """
        at = session.get("reauth_at")
        if not at or now() - at > REAUTH_SECONDS:
            return False
        stored = session.get("reauth_scope") or ""
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
    return secrets.compare_digest(cookie_value, header_value)


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


def describe_client(user_agent: str | None) -> str:
    """A short, honest description. Returns "未知设备" rather than inventing specificity."""
    ua = user_agent or ""
    if not ua:
        return "未知设备"
    browser = next((n for k, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Chrome/", "Chrome"),
                                   ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in ua), None)
    # Chrome's UA contains "Safari/"; check order above already handles that.
    platform = next((n for k, n in (("Windows NT", "Windows"), ("Macintosh", "macOS"),
                                    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                    ("Linux", "Linux")) if k in ua), None)
    if browser and platform:
        return f"{platform} 上的 {browser}"
    return browser or platform or "未知设备"


def new_session_id() -> str:
    return new_id("s")
