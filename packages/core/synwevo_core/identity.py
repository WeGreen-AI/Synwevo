"""Accounts, workspaces, memberships and the login path.

What this module is careful about, and why:

* **Account enumeration.** Registration, login and recovery must not tell an unauthenticated
  caller whether an address has an account. That means the same status, the same body and the
  same rough timing — so a login against an unknown address still pays for a password hash
  instead of returning early and being measurably faster.
* **Layered rate limiting.** Per account, per source address and per account+address. Locking on
  the account alone lets anyone lock anyone else out by failing logins on purpose, so the account
  bucket is a slow backoff while the address bucket is the one that clamps down hard.
* **Personal workspace on creation.** A person should be able to work without inventing an
  organisation, so every account gets one and it is an ordinary workspace they own.
"""
from __future__ import annotations

import os
import secrets
import time
import unicodedata

from . import env
from . import passwords
from .errors import ServiceError
from .store import Store, new_id, now

ROLES = ("viewer", "editor", "admin", "owner")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}
PLATFORM_ROLES = ("none", "operator", "admin")

# Backoff schedule for repeated failures, in seconds, indexed by failure count.
_BACKOFF = [0, 0, 0, 1, 2, 5, 15, 30, 60, 120, 300]
MAX_BACKOFF = 900
# The address bucket clamps hard; the account bucket only slows down, so nobody can lock out
# someone else's account by failing logins against it on purpose.
ADDRESS_FAIL_LIMIT = 30
ADDRESS_WINDOW_S = 900
# Rate-limit rows idle this long, and blocking nothing, are reclaimed (see `sweep_attempts`).
ATTEMPT_IDLE_S = 24 * 3600
ATTEMPT_SWEEP_EVERY_S = 60
_LAST_ATTEMPT_SWEEP = float("-inf")


def normalise_email(email: str) -> str:
    """The key uniqueness and lookup use. Casefold + NFKC, and nothing clever.

    Deliberately does NOT strip dots or +tags: those rules are specific to a few providers, and
    applying them everywhere silently merges addresses that are genuinely different elsewhere.
    """
    return unicodedata.normalize("NFKC", (email or "").strip()).casefold()


def valid_email(email: str) -> bool:
    from email.headerregistry import Address
    e = (email or "").strip()
    if not e or len(e) > 254 or e.count("@") != 1 or any(ord(c) < 33 for c in e):
        return False
    local, _, domain = e.partition("@")
    try:
        parsed = Address(addr_spec=e)
    except Exception:  # noqa: BLE001 - HeaderParseError is not a ValueError; a validator never raises
        return False
    # A trailing dot ("user@example.com.") parses as an empty last label; refuse it here so the
    # caller gets a 400, not a crash further down when the address is used.
    labels = domain.split(".")
    if any(not label or label.startswith("-") or label.endswith("-") for label in labels):
        return False
    return bool(local) and bool(domain) and "." in domain and parsed.addr_spec == e


class Identity:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ users
    def get_user(self, uid: str) -> dict | None:
        return self.store.one("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (uid,))

    def by_email(self, email: str) -> dict | None:
        return self.store.one("SELECT * FROM users WHERE email_key=? AND deleted_at IS NULL",
                              (normalise_email(email),))

    def create_user(self, email: str, password: str | None, *, display_name: str | None = None,
                    status: str = "pending", platform_role: str = "none",
                    with_personal_workspace: bool = True, locale: str | None = None) -> dict:
        if not valid_email(email):
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        if platform_role not in PLATFORM_ROLES:
            raise ServiceError(400, "bad_role", f"未知的平台角色 {platform_role!r}")
        key = normalise_email(email)
        if password is not None:
            passwords.check_policy(password, email=email, display_name=display_name, single_factor=True)

        uid = new_id("u")
        ts = now()
        phc = passwords.hash_password(password) if password is not None else None
        with self.store.tx() as t:
            if t.execute("SELECT 1 FROM users WHERE email_key=?", (key,)).fetchone():
                # Callers that must not leak existence catch this and answer as if it succeeded.
                raise ServiceError(409, "email_taken", "该邮箱已被注册", {"field": "email"})
            t.execute("INSERT INTO users (id,email,email_key,display_name,status,platform_role,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (uid, email.strip(), key, display_name or None, status, platform_role, ts, ts))
            if locale:
                t.execute("UPDATE users SET locale=? WHERE id=?", (locale, uid))
            if phc:
                t.execute("INSERT INTO user_passwords (user_id,phc,updated_at) VALUES (?,?,?)", (uid, phc, ts))
            if with_personal_workspace:
                self._create_workspace(t, name=_personal_name(display_name, email, locale), kind="personal",
                                       owner_id=uid, created_by=uid)
        return self.get_user(uid)

    def set_password(self, uid: str, password: str) -> None:
        user = self.get_user(uid)
        if not user:
            raise ServiceError(404, "no_user", "账户不存在")
        passwords.check_policy(password, email=user["email"], display_name=user.get("display_name"),
                               single_factor=not self.has_second_factor(uid))
        phc = passwords.hash_password(password)
        ts = now()
        self.store.execute("INSERT INTO user_passwords (user_id,phc,updated_at) VALUES (?,?,?) "
                           "ON CONFLICT(user_id) DO UPDATE SET phc=excluded.phc, updated_at=excluded.updated_at",
                           (uid, phc, ts))

    def has_second_factor(self, uid: str) -> bool:
        """Does a password sign-in of this account need a second factor?

        TOTP only. A passkey is a separate way *in*, not a second step after the password: the
        password path never asks for one, so for someone with a passkey and no TOTP the password
        alone opens the account and must meet the single-factor floor. (This used to count
        `webauthn_credentials`, a table nothing writes; counting the real `passkeys` table would
        have lowered the floor for exactly those accounts.)
        """
        return bool(self.store.one(
            "SELECT 1 FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL", (uid,)))

    def set_status(self, uid: str, status: str) -> None:
        if status not in ("pending", "active", "suspended", "pending_delete", "deleted"):
            raise ServiceError(400, "bad_status", f"未知的账户状态 {status!r}")
        self.store.execute("UPDATE users SET status=?, updated_at=? WHERE id=?", (status, now(), uid))

    # ------------------------------------------------------------------ authentication
    def authenticate(self, email: str, password: str, *, ip: str | None = None) -> dict:
        """Verify an email/password pair.

        Returns {"ok": bool, "user": dict|None, "reason": str}. The reason is for the audit log
        and for internal branching — it is never rendered to an unauthenticated caller, because
        "no such account" and "wrong password" have to be indistinguishable from outside.
        """
        key = normalise_email(email)
        addr_bucket = f"ip:{ip or 'unknown'}"
        if self._blocked(addr_bucket):
            return {"ok": False, "user": None, "reason": "address_blocked",
                    "retry_after": self._blocked_for(addr_bucket)}
        acct_bucket = f"user:{key}"
        wait = self._backoff_remaining(acct_bucket)
        if wait > 0:
            return {"ok": False, "user": None, "reason": "backoff", "retry_after": wait}

        user = self.store.one("SELECT * FROM users WHERE email_key=? AND deleted_at IS NULL", (key,))
        row = self.store.one("SELECT phc FROM user_passwords WHERE user_id=?", (user["id"],)) if user else None

        if not row:
            # Hash anyway. Returning early here would make "no account" measurably faster than
            # "wrong password", which is account enumeration by stopwatch.
            passwords.verify(_DUMMY_PHC, password)
            self._record_address_failure(addr_bucket)
            self._record_failure(acct_bucket)
            return {"ok": False, "user": None, "reason": "no_account"}

        if not passwords.verify(row["phc"], password):
            self._record_address_failure(addr_bucket)
            self._record_failure(acct_bucket)
            # The user is returned so the audit log can record which account was targeted —
            # that is how credential stuffing becomes visible. Callers must not render it.
            return {"ok": False, "user": user, "reason": "bad_password"}

        # Correct password, but the account may still not be usable. Checked after verification
        # so an attacker cannot learn an account is suspended without knowing its password.
        if user["status"] in ("suspended", "deleted", "pending_delete"):
            self._record_address_failure(addr_bucket)
            self._record_failure(acct_bucket)
            return {"ok": False, "user": user, "reason": f"status_{user['status']}"}

        self._clear(acct_bucket)
        if passwords.needs_rehash(row["phc"]):
            # The only moment the plaintext exists, so this is where parameters get upgraded.
            self.store.execute("UPDATE user_passwords SET phc=?, updated_at=? WHERE user_id=?",
                               (passwords.hash_password(password), now(), user["id"]))
        return {"ok": True, "user": user, "reason": "ok"}

    def check_password(self, uid: str, password: str, *, ip: str | None = None) -> dict:
        """Re-verify a signed-in person's password (re-authentication), under the login limits.

        Returns {"ok", "reason", "retry_after"?}. Shares the account bucket and the address
        bucket with `authenticate`: a re-authentication prompt that skipped them was a way for
        anyone holding a session cookie to guess the password at Argon2 speed, outside every
        limit the login form has.
        """
        user = self.get_user(uid)
        addr_bucket = f"ip:{ip or 'unknown'}"
        if self._blocked(addr_bucket):
            return {"ok": False, "reason": "address_blocked", "retry_after": self._blocked_for(addr_bucket)}
        if not user:
            return {"ok": False, "reason": "no_account"}
        acct_bucket = f"user:{user['email_key']}"
        wait = self._backoff_remaining(acct_bucket)
        if wait > 0:
            return {"ok": False, "reason": "backoff", "retry_after": wait}
        row = self.store.one("SELECT phc FROM user_passwords WHERE user_id=?", (uid,))
        if not row or not passwords.verify(row["phc"], password or ""):
            if not row:
                passwords.verify(_DUMMY_PHC, password or "")
            self._record_address_failure(addr_bucket)
            self._record_failure(acct_bucket)
            return {"ok": False, "reason": "bad_password" if row else "no_password"}
        self._clear(acct_bucket)
        return {"ok": True, "reason": "ok"}

    # ------------------------------------------------------------------ rate limiting
    def _bucket(self, name: str) -> dict | None:
        return self.store.one("SELECT * FROM auth_attempts WHERE bucket=?", (name,))

    def _blocked(self, name: str, *, limit: int = ADDRESS_FAIL_LIMIT,
                 window_s: float = ADDRESS_WINDOW_S) -> bool:
        b = self._bucket(name)
        if not b:
            return False
        if b.get("blocked_until") and now() < b["blocked_until"]:
            return True
        if now() - b["first_at"] > window_s:
            return False
        return b["count"] >= limit

    def address_failures(self, ip: str | None, *, window_s: float = ADDRESS_WINDOW_S) -> int:
        """Failed sign-ins recorded for this source address in the current window (0 when the
        window ran out). Read-only: the account service's risk-triggered challenge asks this
        before deciding whether a sign-in attempt must carry a solved token."""
        b = self._bucket(f"ip:{ip or 'unknown'}")
        if not b or now() - b["first_at"] > window_s:
            return 0
        return int(b["count"])

    def _blocked_for(self, name: str) -> int:
        b = self._bucket(name) or {}
        until = b.get("blocked_until") or ((b.get("first_at") or now()) + ADDRESS_WINDOW_S)
        return max(1, int(until - now()))

    def _backoff_remaining(self, name: str) -> float:
        b = self._bucket(name)
        if not b:
            return 0.0
        idx = min(int(b["count"]), len(_BACKOFF) - 1)
        delay = min(_BACKOFF[idx], MAX_BACKOFF)
        return max(0.0, (b["last_at"] + delay) - now())

    def _record_failure(self, *buckets: str) -> None:
        """A slow-backoff bucket: the count only resets on success (`_clear`)."""
        ts = now()
        with self.store.tx() as t:
            for name in buckets:
                t.execute(
                    "INSERT INTO auth_attempts (bucket,count,first_at,last_at) VALUES (?,1,?,?) "
                    "ON CONFLICT(bucket) DO UPDATE SET count=count+1, last_at=excluded.last_at",
                    (name, ts, ts))

    def _record_address_failure(self, name: str, *, limit: int = ADDRESS_FAIL_LIMIT,
                                window_s: float = ADDRESS_WINDOW_S) -> None:
        """A fixed-window bucket that clamps hard.

        The window restarts once it has run out, and reaching the limit sets `blocked_until`.
        Before this, `first_at` was written once and never moved, so fifteen minutes after an
        address's *first ever* failure `_blocked` answered "window over" forever and the address
        limit stopped existing — leaving password spraying from one address throttled only by
        the per-account backoff.
        """
        ts = now()
        w = float(window_s)
        with self.store.tx() as t:
            # SQLite evaluates every SET expression against the row as it was before the UPDATE,
            # so both CASEs see the old first_at.
            t.execute(
                "INSERT INTO auth_attempts (bucket,count,first_at,last_at) VALUES (?,1,?,?) "
                "ON CONFLICT(bucket) DO UPDATE SET "
                "count=CASE WHEN excluded.last_at - first_at > ? THEN 1 ELSE count+1 END, "
                "first_at=CASE WHEN excluded.last_at - first_at > ? THEN excluded.last_at ELSE first_at END, "
                "blocked_until=CASE WHEN excluded.last_at - first_at > ? THEN NULL ELSE blocked_until END, "
                "last_at=excluded.last_at",
                (name, ts, ts, w, w, w))
            t.execute("UPDATE auth_attempts SET blocked_until=? WHERE bucket=? AND count>=? "
                      "AND (blocked_until IS NULL OR blocked_until<?)", (ts + w, name, limit, ts))

    def _clear(self, name: str) -> None:
        self.store.execute("DELETE FROM auth_attempts WHERE bucket=?", (name,))

    # Public faces of the same buckets, for the other throttled endpoints (second factor,
    # recovery requests, the anonymous passkey challenge, bootstrap). One table and one set of
    # semantics, so a limit added later cannot quietly behave differently from login's.
    def window_allows(self, bucket: str, *, limit: int, window_s: float) -> tuple[bool, int]:
        """Count one attempt against a fixed-window limit. Returns (allowed, retry_after).

        Every allowed anonymous call writes (or refreshes) one row per source address, so this
        is also where idle rows are reclaimed — at most once a minute per process, the same
        "reclaim on the path that creates them" rule the challenge tables follow.
        """
        self._maybe_sweep_attempts()
        with self.store.tx():
            # Admission and increment must serialize across processes, not only within one
            # request: public registration/invitation limits are cost controls as well.
            if self._blocked(bucket, limit=limit, window_s=window_s):
                b = self._bucket(bucket) or {}
                until = b.get("blocked_until") or ((b.get("first_at") or now()) + window_s)
                return False, max(1, int(until - now()))
            self._record_address_failure(bucket, limit=limit, window_s=window_s)
            return True, 0

    def sweep_attempts(self, idle_s: float = ATTEMPT_IDLE_S) -> int:
        """Delete rate-limit rows nobody has touched for `idle_s` and that block nothing.

        Only reclamation: a fixed-window row whose window has run out already counts as empty
        (`_record_address_failure` restarts it), and a backoff row idle for a day has long since
        stopped delaying anyone. What it forgets is a slow-backoff count older than a day, which
        buys an attacker a few free guesses per account per day — against the unbounded growth of
        one row per source address that every anonymous endpoint otherwise leaves behind.
        """
        ts = now()
        return self.store.execute(
            "DELETE FROM auth_attempts WHERE last_at<? AND (blocked_until IS NULL OR blocked_until<?)",
            (ts - float(idle_s), ts))

    def _maybe_sweep_attempts(self) -> None:
        global _LAST_ATTEMPT_SWEEP
        mono = time.monotonic()
        if mono - _LAST_ATTEMPT_SWEEP < ATTEMPT_SWEEP_EVERY_S:
            return
        _LAST_ATTEMPT_SWEEP = mono
        try:
            self.sweep_attempts()
        except Exception as e:  # noqa: BLE001 - reclamation must never refuse a request
            print(f"[synwevo] auth_attempts sweep failed: {type(e).__name__}: {e}", flush=True)

    def backoff_wait(self, bucket: str, schedule: list[int] | tuple[int, ...] | None = None) -> float:
        """Seconds to wait before the next attempt. `schedule` overrides the login schedule
        (delay indexed by failure count; the last value repeats)."""
        if schedule is None:
            return self._backoff_remaining(bucket)
        b = self._bucket(bucket)
        if not b:
            return 0.0
        delay = schedule[min(int(b["count"]), len(schedule) - 1)]
        return max(0.0, (b["last_at"] + min(delay, MAX_BACKOFF)) - now())

    def backoff_fail(self, bucket: str) -> None:
        self._record_failure(bucket)

    def backoff_clear(self, bucket: str) -> None:
        self._clear(bucket)

    def attempt_count(self, bucket: str) -> int:
        return int((self._bucket(bucket) or {}).get("count") or 0)

    # ------------------------------------------------------------------ workspaces
    def _create_workspace(self, t, *, name: str, kind: str, owner_id: str | None,
                          created_by: str | None, wid: str | None = None) -> str:
        wid = wid or new_id("w")
        ts = now()
        t.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at,created_by) "
                  "VALUES (?,?,?,'active',?,?,?)", (wid, name, kind, ts, ts, created_by))
        if owner_id:
            t.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
                      "VALUES (?,?, 'owner', 1, ?,?,?)", (wid, owner_id, ts, ts, created_by))
        released = tuple(r["id"] for r in t.execute(
            f"SELECT id FROM apps WHERE status='active' AND id IN ({','.join('?' for _ in SUITE_WORKSPACE_APPS)})",
            SUITE_WORKSPACE_APPS).fetchall())
        for app_id in DEFAULT_WORKSPACE_APPS + released:
            t.execute("INSERT INTO workspace_apps (workspace_id,app_id,enabled_at,enabled_by) "
                      "VALUES (?,?,?,?) ON CONFLICT(workspace_id,app_id) DO NOTHING",
                      (wid, app_id, ts, created_by))
            if owner_id:
                t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
                          "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                          "revoked_at=NULL", (wid, app_id, owner_id, ts, created_by))
        return wid

    def create_workspace(self, name: str, *, owner_id: str, kind: str = "org") -> dict:
        if kind not in ("personal", "org"):
            raise ServiceError(400, "bad_kind", f"未知的空间类型 {kind!r}")
        with self.store.tx() as t:
            wid = self._create_workspace(t, name=name.strip() or "未命名空间", kind=kind,
                                         owner_id=owner_id, created_by=owner_id)
        return self.get_workspace(wid)

    def ensure_personal_workspace(self, uid: str) -> dict:
        """One default personal space under concurrent onboarding, preserving any older space."""
        with self.store.tx() as t:
            user = t.execute("SELECT * FROM users WHERE id=? AND status='active' AND deleted_at IS NULL",
                             (uid,)).fetchone()
            if not user:
                raise ServiceError(403, "account_unavailable", "请先验证邮箱再创建个人空间")
            row = t.execute("SELECT w.id FROM workspaces w JOIN memberships m ON m.workspace_id=w.id "
                            "WHERE w.kind='personal' AND w.created_by=? AND m.user_id=? "
                            "AND m.role='owner' AND w.deleted_at IS NULL ORDER BY w.created_at,w.id LIMIT 1",
                            (uid, uid)).fetchone()
            if row:
                wid = row["id"]
            else:
                wid = self._create_workspace(t, name=_personal_name(user["display_name"], user["email"], dict(user).get("locale")),
                                             kind="personal", owner_id=uid, created_by=uid)
            t.execute("UPDATE users SET onboarding_completed_at=COALESCE(onboarding_completed_at,?), "
                      "updated_at=? WHERE id=?", (now(), now(), uid))
        return self.get_workspace(wid)

    def get_workspace(self, wid: str) -> dict | None:
        return self.store.one("SELECT * FROM workspaces WHERE id=? AND deleted_at IS NULL", (wid,))

    def membership(self, wid: str, uid: str) -> dict | None:
        return self.store.one("SELECT * FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))

    def workspaces_for(self, uid: str) -> list[dict]:
        """Only workspaces this person is actually a member of.

        This is what makes the legacy workspace unreachable: it has no membership rows, so it can
        never appear here, for anybody, until someone is deliberately added to it.
        """
        return self.store.all(
            "SELECT w.id, w.name, w.kind, w.status, m.role, m.can_export,w.organization_id,o.name AS organization_name "
            "FROM memberships m JOIN workspaces w ON w.id=m.workspace_id "
            "LEFT JOIN organizations o ON o.id=w.organization_id "
            "WHERE m.user_id=? AND w.deleted_at IS NULL AND (w.organization_id IS NULL OR EXISTS "
            "(SELECT 1 FROM organization_memberships om WHERE om.organization_id=w.organization_id AND om.user_id=m.user_id)) "
            "ORDER BY w.created_at,w.id", (uid,))

    def add_member(self, wid: str, uid: str, role: str, *, can_export: bool | None = None,
                   actor_id: str | None = None) -> dict:
        """Make someone a member with `role`, or set an existing member's role and export flag.

        An operator and fixture primitive — the governed paths are invitations and
        `change_role` — but it still keeps the two invariants every membership write keeps, in
        one transaction:

        * **it never demotes the last usable owner.** This used to be a bare upsert, so re-adding
          a person who had just become the sole owner (a concurrent transfer) quietly made them
          an editor and left the workspace with no owner at all;
        * **a new membership starts fresh.** Removing someone writes explicit revoked
          entitlement rows so the removal holds under the `members` policy; left in place, a
          person added back here would be a member who silently cannot open the application.
          The same rule invitation acceptance applies.
        """
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        ts = now()
        export = 1 if (can_export if can_export is not None else role != "viewer") else 0
        with self.store.tx() as t:
            cur = t.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
                            (wid, uid)).fetchone()
            if cur:
                if cur["role"] == "owner" and role != "owner" and self._other_active_owners(t, wid, uid) == 0:
                    raise ServiceError(409, "last_owner", "不能降级最后一名所有者")
                t.execute("UPDATE memberships SET role=?, can_export=?, updated_at=? "
                          "WHERE workspace_id=? AND user_id=?", (role, export, ts, wid, uid))
            else:
                t.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,"
                          "updated_at,created_by) VALUES (?,?,?,?,?,?,?)",
                          (wid, uid, role, export, ts, ts, actor_id))
                t.execute("DELETE FROM app_entitlements WHERE workspace_id=? AND user_id=? "
                          "AND revoked_at IS NOT NULL", (wid, uid))
        return self.membership(wid, uid)

    @staticmethod
    def _authorize(t, wid: str, target: dict, actor_id: str | None, authorize) -> None:
        """Run a caller's authority check against rows read inside the write transaction.

        `authorize(target, actor)` receives the target's membership as it is *now* and, when
        `actor_id` is given, the actor's (None if they are no longer a member). Reading either
        before the transaction let an admin demoted or removed a moment earlier finish a change
        with the authority they had just lost, and let a target promoted a moment earlier be
        demoted without the re-authentication that promotion now calls for.
        """
        if authorize is None:
            return
        actor = None
        if actor_id is not None:
            row = t.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                            (wid, actor_id)).fetchone()
            actor = dict(row) if row else None
        authorize(dict(target), actor)

    @staticmethod
    def _other_active_owners(t, wid: str, uid: str) -> int:
        """Owners of `wid` other than `uid` who can actually act: a live, active account.

        A suspended or deleted owner is not continuity. Counting them let the last *usable*
        owner be removed or demoted while a suspended one remained, leaving a workspace nobody
        could administer.
        """
        return t.execute(
            "SELECT COUNT(*) AS n FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.workspace_id=? AND m.role='owner' AND m.user_id<>? "
            "AND u.status='active' AND u.deleted_at IS NULL", (wid, uid)).fetchone()["n"]

    def remove_member(self, wid: str, uid: str, *, only_roles: tuple[str, ...] | None = None,
                      actor_id: str | None = None, authorize=None) -> dict | None:
        """Remove someone from a workspace, and everything that membership was holding open.

        One transaction, so the effect is all-or-nothing and immediate (GitHub and Notion both
        cut access at once): the membership row, the person's application entitlements in this
        workspace, every platform token they hold that acts in it, the workspace selection of
        their live sessions, and the invitations they sent here that nobody has accepted yet —
        an invitation must not keep adding people on the authority of someone who has left.

        Returns what was withdrawn, or None when the person was not a member. Removing the last
        active owner is refused inside the same transaction: checking and deleting separately
        would let two concurrent removals each see a second owner and both proceed.
        `only_roles` is the caller's authority ceiling, checked here for the same reason — a
        role read before the transaction may have changed by the time it runs; `authorize` (with
        `actor_id`) is the fuller form of that check, see `_authorize`.

        `cancelled_invitation_ids` names the invitations withdrawn, so the caller can record each
        in the security journal.
        """
        ts = now()
        with self.store.tx() as t:
            row = t.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                            (wid, uid)).fetchone()
            if not row:
                return None
            if only_roles is not None and row["role"] not in only_roles:
                raise ServiceError(403, "role_too_high", "不能管理与自己同级或更高角色的成员")
            self._authorize(t, wid, row, actor_id, authorize)
            if row["role"] == "owner" and self._other_active_owners(t, wid, uid) == 0:
                raise ServiceError(409, "last_owner", "这是该空间最后一名所有者，请先指定另一位所有者")
            t.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))
            ents = t.execute("UPDATE app_entitlements SET revoked_at=? WHERE workspace_id=? AND user_id=? "
                             "AND revoked_at IS NULL", (ts, wid, uid)).rowcount
            toks = t.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='member_removed' "
                             "WHERE user_id=? AND workspace_id=? AND revoked_at IS NULL", (ts, uid, wid)).rowcount
            moved = t.execute("UPDATE sessions SET workspace_id=NULL WHERE user_id=? AND workspace_id=? "
                              "AND revoked_at IS NULL", (uid, wid)).rowcount
            inv_ids = [r["id"] for r in t.execute(
                "SELECT id FROM invitations WHERE workspace_id=? AND invited_by=? "
                "AND accepted_at IS NULL AND revoked_at IS NULL", (wid, uid)).fetchall()]
            if inv_ids:
                t.execute(f"UPDATE invitations SET revoked_at=? WHERE id IN ({','.join('?' * len(inv_ids))})",
                          (ts, *inv_ids))
        return {"role": row["role"], "can_export": bool(row["can_export"]),
                "entitlements_revoked": ents, "tokens_revoked": toks, "sessions_moved": moved,
                "invitations_cancelled": len(inv_ids), "cancelled_invitation_ids": inv_ids}

    def change_role(self, wid: str, uid: str, role: str | None, *, can_export: bool | None = None,
                    only_roles: tuple[str, ...] | None = None, actor_id: str | None = None,
                    authorize=None) -> dict:
        """Change a member's role. Returns the membership row plus `before` {role, can_export}.

        `role=None` keeps the role the member has *inside this transaction* — for a change of
        `can_export` alone. Passing the role read beforehand instead would write a stale role back
        over a promotion or demotion that landed in between.

        `can_export` is kept unless the caller sets it, with one exception: moving *down* to
        viewer clears it, because a downgrade must not quietly keep the wider right the old role
        carried. (It used to be overwritten on every change, discarding an explicit grant or
        denial.) `authorize`/`actor_id`: the caller's authority, re-checked against the rows as
        they are inside this transaction (see `_authorize`).
        """
        if role is not None and role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        with self.store.tx() as t:
            row = t.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                            (wid, uid)).fetchone()
            if not row:
                raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
            if only_roles is not None and row["role"] not in only_roles:
                raise ServiceError(403, "role_too_high", "不能管理与自己同级或更高角色的成员")
            self._authorize(t, wid, row, actor_id, authorize)
            role = role or row["role"]
            if row["role"] == "owner" and role != "owner" and self._other_active_owners(t, wid, uid) == 0:
                raise ServiceError(409, "last_owner", "不能降级最后一名所有者")
            if can_export is not None:
                export = 1 if can_export else 0
            elif role == "viewer" and row["role"] != "viewer":
                export = 0
            else:
                export = row["can_export"]
            t.execute("UPDATE memberships SET role=?, can_export=?, updated_at=? WHERE workspace_id=? AND user_id=?",
                      (role, export, now(), wid, uid))
        out = self.membership(wid, uid)
        out["before"] = {"role": row["role"], "can_export": bool(row["can_export"])}
        return out

    def transfer_ownership(self, wid: str, from_uid: str, to_uid: str, *,
                           demote_self_to: str | None = None) -> dict:
        """Hand ownership to another member, optionally stepping down, in one transaction.

        The target must be a member with a live, active account — ownership handed to someone
        who cannot sign in is the same as no owner. Because the promotion and the optional
        demotion commit together, there is no moment at which the workspace has no owner, however
        these interleave with concurrent removals.
        """
        if demote_self_to is not None and demote_self_to not in ("admin", "editor", "viewer"):
            raise ServiceError(400, "bad_role", "移交后自己的角色只能是 admin、editor 或 viewer",
                               {"field": "demote_self_to"})
        if from_uid == to_uid:
            raise ServiceError(400, "same_user", "不能把所有权移交给自己")
        ts = now()
        with self.store.tx() as t:
            me = t.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
                           (wid, from_uid)).fetchone()
            if not me or me["role"] != "owner":
                raise ServiceError(403, "not_owner", "只有空间所有者可以移交所有权")
            target = t.execute(
                "SELECT m.role, u.status, u.deleted_at FROM memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.workspace_id=? AND m.user_id=?", (wid, to_uid)).fetchone()
            if not target or target["status"] != "active" or target["deleted_at"]:
                raise ServiceError(409, "target_unavailable", "接收人必须是这个空间里账户状态正常的成员")
            t.execute("UPDATE memberships SET role='owner', can_export=1, updated_at=? "
                      "WHERE workspace_id=? AND user_id=?", (ts, wid, to_uid))
            if demote_self_to:
                t.execute("UPDATE memberships SET role=?, can_export=?, updated_at=? "
                          "WHERE workspace_id=? AND user_id=?",
                          (demote_self_to, 0 if demote_self_to == "viewer" else 1, ts, wid, from_uid))
        return {"from": from_uid, "to": to_uid, "target_previous_role": target["role"],
                "self_role": demote_self_to or "owner"}

    def list_members(self, wid: str, *, q: str = "", role: str | None = None, status: str | None = None,
                     app_id: str = "docweave", entitlement: str | None = None,
                     offset: int = 0, limit: int = 50) -> dict:
        """Members of one workspace with their account state and one application's entitlement.

        Metadata only — names, addresses, roles — never anything a member made. Filtering is done
        in SQL so `total` is the size of the filtered set, not of the page.
        """
        if role and role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        if status and status not in ("pending", "active", "suspended", "pending_delete", "deleted"):
            raise ServiceError(400, "bad_status", f"未知的账户状态 {status!r}", {"field": "status"})
        if entitlement and entitlement not in ("active", "revoked", "none"):
            raise ServiceError(400, "bad_entitlement", "entitlement 只能是 active、revoked 或 none",
                               {"field": "entitlement"})
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        where, args = ["m.workspace_id=?"], [app_id, wid]
        if q:
            pat = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(u.email_key LIKE ? ESCAPE '\\' OR u.display_name LIKE ? ESCAPE '\\')")
            args += [pat.casefold(), pat]
        if role:
            where.append("m.role=?")
            args.append(role)
        if status:
            where.append("u.status=?")
            args.append(status)
        if entitlement == "active":
            where.append("e.user_id IS NOT NULL AND e.revoked_at IS NULL")
        elif entitlement == "revoked":
            where.append("e.revoked_at IS NOT NULL")
        elif entitlement == "none":
            where.append("e.user_id IS NULL")
        base = ("FROM memberships m JOIN users u ON u.id=m.user_id "
                "LEFT JOIN app_entitlements e ON e.workspace_id=m.workspace_id AND e.user_id=m.user_id "
                "AND e.app_id=? WHERE " + " AND ".join(where))
        total = self.store.one(f"SELECT COUNT(*) AS n {base}", tuple(args))["n"]
        rows = self.store.all(
            "SELECT m.user_id, m.role, m.can_export, m.created_at AS joined_at, m.created_by AS added_by, "
            "u.email, u.display_name, u.status AS account_status, "
            "e.granted_at AS entitlement_granted_at, e.revoked_at AS entitlement_revoked_at, "
            "e.user_id AS entitlement_row "
            f"{base} ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END, "
            "m.created_at LIMIT ? OFFSET ?", tuple(args) + (limit, offset))
        items = []
        for r in rows:
            ent = "none" if not r.pop("entitlement_row") else ("revoked" if r["entitlement_revoked_at"] else "active")
            items.append({**r, "can_export": bool(r["can_export"]), "entitlement": ent})
        owners = self.store.one(
            "SELECT COUNT(*) AS n, SUM(CASE WHEN u.status='active' AND u.deleted_at IS NULL THEN 1 ELSE 0 END) AS live "
            "FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role='owner'", (wid,))
        nxt = offset + len(items)
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": nxt if nxt < total else None,
                "owner_count": owners["n"], "active_owner_count": int(owners["live"] or 0)}

    def set_entitlement(self, wid: str, uid: str, app_id: str, *, grant: bool,
                        actor_id: str | None = None, authorize=None) -> dict:
        """Grant or revoke one member's right to use one application in this workspace.

        A revocation writes an explicit revoked row even when none existed, because under the
        `members` policy "no row" means "allowed": without the row there would be nothing for
        the eligibility check to refuse on. Tokens the person holds for that application in this
        workspace are revoked in the same transaction, so the change is not left waiting for the
        next refresh. `authorize` (with `actor_id` as the acting member) re-checks the caller's
        authority inside this transaction, see `_authorize`.
        """
        ts = now()
        with self.store.tx() as t:
            target = t.execute("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                               (wid, uid)).fetchone()
            if not target:
                raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
            self._authorize(t, wid, target, actor_id, authorize)
            if not t.execute("SELECT 1 FROM apps WHERE id=?", (app_id,)).fetchone():
                raise ServiceError(404, "no_app", f"没有名为 {app_id} 的应用")
            if grant and not t.execute("SELECT 1 FROM workspace_apps WHERE workspace_id=? AND app_id=?",
                                       (wid, app_id)).fetchone():
                raise ServiceError(409, "app_not_enabled", "这个空间还没有启用该应用，授予使用资格没有意义")
            before = t.execute("SELECT revoked_at FROM app_entitlements WHERE workspace_id=? AND app_id=? AND user_id=?",
                               (wid, app_id, uid)).fetchone()
            state_before = "none" if not before else ("revoked" if before["revoked_at"] else "active")
            tokens = 0
            if grant:
                t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by) "
                          "VALUES (?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                          "revoked_at=NULL, granted_at=excluded.granted_at, granted_by=excluded.granted_by",
                          (wid, app_id, uid, ts, actor_id))
            else:
                t.execute("INSERT INTO app_entitlements (workspace_id,app_id,user_id,granted_at,granted_by,revoked_at) "
                          "VALUES (?,?,?,?,?,?) ON CONFLICT(workspace_id,app_id,user_id) DO UPDATE SET "
                          "revoked_at=COALESCE(app_entitlements.revoked_at, excluded.revoked_at)",
                          (wid, app_id, uid, ts, actor_id, ts))
                tokens = t.execute(
                    "UPDATE oauth_tokens SET revoked_at=?, revoked_reason='entitlement_revoked' "
                    "WHERE user_id=? AND workspace_id=? AND revoked_at IS NULL "
                    "AND client_id IN (SELECT client_id FROM oauth_clients WHERE app_id=?)",
                    (ts, uid, wid, app_id)).rowcount
        return {"app_id": app_id, "before": state_before, "after": "active" if grant else "revoked",
                "tokens_revoked": tokens}

    def set_workspace_frozen(self, wid: str, frozen: bool) -> dict:
        """Freeze (or unfreeze) a workspace: the platform's stop switch for everything in it.

        Freezing is refused for anything that is not an ordinary active workspace — the unclaimed
        legacy workspace has its own lifecycle, and freezing a deleted one means nothing. Tokens
        acting in the workspace are revoked and sessions are moved off it in the same
        transaction; every later check (`status != 'active'`) refuses it from then on.
        """
        ts = now()
        with self.store.tx() as t:
            ws = t.execute("SELECT status, kind FROM workspaces WHERE id=? AND deleted_at IS NULL", (wid,)).fetchone()
            if not ws:
                raise ServiceError(404, "no_workspace", "找不到该工作空间")
            if frozen:
                if ws["status"] != "active":
                    raise ServiceError(409, "not_active", f"只能冻结状态正常的工作空间（当前：{ws['status']}）")
                t.execute("UPDATE workspaces SET status='frozen', updated_at=? WHERE id=?", (ts, wid))
                toks = t.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='workspace_frozen' "
                                 "WHERE workspace_id=? AND revoked_at IS NULL", (ts, wid)).rowcount
                moved = t.execute("UPDATE sessions SET workspace_id=NULL WHERE workspace_id=? AND revoked_at IS NULL",
                                  (wid,)).rowcount
                return {"status": "frozen", "tokens_revoked": toks, "sessions_moved": moved}
            if ws["status"] != "frozen":
                raise ServiceError(409, "not_frozen", "该工作空间没有被冻结")
            t.execute("UPDATE workspaces SET status='active', updated_at=? WHERE id=?", (ts, wid))
            return {"status": "active"}

    def peek_bootstrap_token(self, raw: str) -> bool:
        """Would this bootstrap token be accepted right now? Reads only."""
        import hashlib
        digest = hashlib.sha256((raw or "").encode()).hexdigest()
        return bool(self.store.one("SELECT 1 FROM bootstrap_tokens WHERE token_hash=? AND used_at IS NULL "
                                   "AND expires_at>?", (digest, now())))

    def purge_new_account(self, uid: str) -> None:
        """Undo `create_user` for an account that never became usable (a lost bootstrap race).

        Only for an account created a moment ago by the caller: it removes the personal
        workspace and its application rows too, which the earlier rollback left behind.
        """
        with self.store.tx() as t:
            wids = [r["id"] for r in t.execute(
                "SELECT id FROM workspaces WHERE created_by=? AND kind='personal'", (uid,)).fetchall()]
            for wid in wids:
                t.execute("DELETE FROM app_entitlements WHERE workspace_id=?", (wid,))
                t.execute("DELETE FROM workspace_apps WHERE workspace_id=?", (wid,))
                t.execute("DELETE FROM memberships WHERE workspace_id=?", (wid,))
                t.execute("DELETE FROM workspaces WHERE id=?", (wid,))
            t.execute("DELETE FROM memberships WHERE user_id=?", (uid,))
            t.execute("DELETE FROM app_entitlements WHERE user_id=?", (uid,))
            t.execute("DELETE FROM user_passwords WHERE user_id=?", (uid,))
            t.execute("DELETE FROM users WHERE id=?", (uid,))

    # ------------------------------------------------------------------ platform admins
    def platform_admins(self) -> list[dict]:
        return self.store.all("SELECT * FROM users WHERE platform_role='admin' "
                              "AND status='active' AND deleted_at IS NULL")

    def set_platform_role(self, uid: str, role: str) -> None:
        if role not in PLATFORM_ROLES:
            raise ServiceError(400, "bad_role", f"未知的平台角色 {role!r}")
        with self.store.tx() as t:
            cur = t.execute("SELECT platform_role FROM users WHERE id=?", (uid,)).fetchone()
            if not cur:
                raise ServiceError(404, "no_user", "账户不存在")
            if cur["platform_role"] == "admin" and role != "admin":
                n = t.execute("SELECT COUNT(*) AS n FROM users WHERE platform_role='admin' "
                              "AND status='active' AND deleted_at IS NULL AND id<>?", (uid,)).fetchone()["n"]
                if n == 0:
                    raise ServiceError(409, "last_admin", "这是最后一名平台管理员，降级后将无人可管理本服务")
            t.execute("UPDATE users SET platform_role=?, updated_at=? WHERE id=?", (role, now(), uid))

    # ------------------------------------------------------------------ first-admin bootstrap
    def create_bootstrap_token(self, ttl_s: int = 900) -> str:
        """One-shot, short-lived, printed once by the CLI.

        There is no default password anywhere in this product, and this token is not one: it is
        single-use, expires, and only exists while an operator is actively using it. Any earlier
        unused token is invalidated so two concurrent bootstraps cannot both succeed.
        """
        import hashlib
        raw = secrets.token_urlsafe(32)
        ts = now()
        with self.store.tx() as t:
            t.execute("UPDATE bootstrap_tokens SET used_at=?, used_by='superseded' WHERE used_at IS NULL", (ts,))
            t.execute("INSERT INTO bootstrap_tokens (id,token_hash,created_at,expires_at) VALUES (?,?,?,?)",
                      (new_id("bt"), hashlib.sha256(raw.encode()).hexdigest(), ts, ts + ttl_s))
        return raw

    def consume_bootstrap_token(self, raw: str, *, user_id: str) -> bool:
        """Atomic: the UPDATE's WHERE clause is the check, so two callers cannot both win."""
        import hashlib
        digest = hashlib.sha256((raw or "").encode()).hexdigest()
        n = self.store.execute(
            "UPDATE bootstrap_tokens SET used_at=?, used_by=? WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
            (now(), user_id, digest, now()))
        return n > 0

    def bootstrap_needed(self) -> bool:
        return not self.platform_admins()


# Applications a newly created workspace starts with, written in the *same transaction* that
# creates the workspace. Not a stylistic choice: `adopt_existing_members` is a one-time
# migration, so without this every account created afterwards — every registration, every
# `manage.py bootstrap admin` — owned a workspace with no applications enabled and could sign in
# successfully only to be told it had no access to anything. It was found exactly that way.
#
# The entitlement *model* lives in platform/apps.py; this is only the starting state, and the
# legacy workspace does not go through here, so it keeps its zero members and zero applications.
DEFAULT_WORKSPACE_APPS = ("docweave",)

#: The business applications on the workplace origin (Forms, Tables, DataPrep, Approvals, Extract;
#: docs/agent-work/DECISIONS.md D1). A new workspace starts with each of them that the deployment
#: has released — the registry row is active — so a person can use them without an administrator
#: first switching anything on. The personal Agent is not here: it belongs to the person, not to a
#: workspace (`synwevo_core.apps.PERSONAL_APP_IDS`).
SUITE_WORKSPACE_APPS = ("forms", "tables", "dataprep", "approvals", "extract")


#: The default personal workspace name, in the account's interface language. It is data once
#: written (renaming is the owner's), so it is composed at creation in the language the person
#: registered in rather than always in Simplified Chinese.
PERSONAL_NAME = {"zh-Hans": "{who} 的空间", "zh-Hant": "{who} 的空間", "en": "{who}'s space"}


def _personal_name(display_name: str | None, email: str, locale: str | None = None) -> str:
    who = (display_name or "").strip() or (email or "").split("@")[0]
    return PERSONAL_NAME.get(locale or "zh-Hans", PERSONAL_NAME["zh-Hans"]).format(who=who)


# A real Argon2id hash of a random value, used to spend the same time on an unknown account as on
# a known one. Generated once at import so the cost matches the configured parameters exactly.
_DUMMY_PHC = passwords.hash_password(secrets.token_urlsafe(32))


REGISTRATION_MODES = ("open", "invite", "closed")


def registration_mode(store: Store | None = None) -> str:
    """Persisted registration.mode overrides the environment; default is invite-only.

    A first deployment that is reachable before anyone configures it must not let a passer-by
    open an account, so the safe mode is the default and opening it up is a deliberate act.
    """
    if store is not None:
        setting = store.one("SELECT value FROM meta WHERE key='registration.mode'")
        if setting and setting["value"] in REGISTRATION_MODES:
            return setting["value"]
    mode = (env.get("REGISTRATION") or "invite").strip().lower()
    return mode if mode in REGISTRATION_MODES else "invite"
