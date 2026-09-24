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


def normalise_email(email: str) -> str:
    """The key uniqueness and lookup use. Casefold + NFKC, and nothing clever.

    Deliberately does NOT strip dots or +tags: those rules are specific to a few providers, and
    applying them everywhere silently merges addresses that are genuinely different elsewhere.
    """
    return unicodedata.normalize("NFKC", (email or "").strip()).casefold()


def valid_email(email: str) -> bool:
    e = (email or "").strip()
    if not e or len(e) > 254 or e.count("@") != 1:
        return False
    local, _, domain = e.partition("@")
    return bool(local) and bool(domain) and "." in domain and " " not in e


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
                    with_personal_workspace: bool = True) -> dict:
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
            if phc:
                t.execute("INSERT INTO user_passwords (user_id,phc,updated_at) VALUES (?,?,?)", (uid, phc, ts))
            if with_personal_workspace:
                self._create_workspace(t, name=_personal_name(display_name, email), kind="personal",
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
        n = self.store.one("SELECT (SELECT COUNT(*) FROM webauthn_credentials WHERE user_id=?) "
                           "+ (SELECT COUNT(*) FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL) AS n",
                           (uid, uid))["n"]
        return bool(n)

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
            return {"ok": False, "user": None, "reason": "address_blocked"}
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
            self._record_failure(addr_bucket, acct_bucket)
            return {"ok": False, "user": None, "reason": "no_account"}

        if not passwords.verify(row["phc"], password):
            self._record_failure(addr_bucket, acct_bucket)
            # The user is returned so the audit log can record which account was targeted —
            # that is how credential stuffing becomes visible. Callers must not render it.
            return {"ok": False, "user": user, "reason": "bad_password"}

        # Correct password, but the account may still not be usable. Checked after verification
        # so an attacker cannot learn an account is suspended without knowing its password.
        if user["status"] in ("suspended", "deleted", "pending_delete"):
            self._record_failure(addr_bucket, acct_bucket)
            return {"ok": False, "user": user, "reason": f"status_{user['status']}"}

        self._clear(acct_bucket)
        if passwords.needs_rehash(row["phc"]):
            # The only moment the plaintext exists, so this is where parameters get upgraded.
            self.store.execute("UPDATE user_passwords SET phc=?, updated_at=? WHERE user_id=?",
                               (passwords.hash_password(password), now(), user["id"]))
        return {"ok": True, "user": user, "reason": "ok"}

    # ------------------------------------------------------------------ rate limiting
    def _bucket(self, name: str) -> dict | None:
        return self.store.one("SELECT * FROM auth_attempts WHERE bucket=?", (name,))

    def _blocked(self, name: str) -> bool:
        b = self._bucket(name)
        if not b:
            return False
        if b.get("blocked_until") and now() < b["blocked_until"]:
            return True
        if now() - b["first_at"] > ADDRESS_WINDOW_S:
            return False
        return b["count"] >= ADDRESS_FAIL_LIMIT

    def _backoff_remaining(self, name: str) -> float:
        b = self._bucket(name)
        if not b:
            return 0.0
        idx = min(int(b["count"]), len(_BACKOFF) - 1)
        delay = min(_BACKOFF[idx], MAX_BACKOFF)
        return max(0.0, (b["last_at"] + delay) - now())

    def _record_failure(self, *buckets: str) -> None:
        ts = now()
        with self.store.tx() as t:
            for name in buckets:
                t.execute(
                    "INSERT INTO auth_attempts (bucket,count,first_at,last_at) VALUES (?,1,?,?) "
                    "ON CONFLICT(bucket) DO UPDATE SET count=count+1, last_at=excluded.last_at",
                    (name, ts, ts))

    def _clear(self, name: str) -> None:
        self.store.execute("DELETE FROM auth_attempts WHERE bucket=?", (name,))

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
        for app_id in DEFAULT_WORKSPACE_APPS:
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
            "SELECT w.id, w.name, w.kind, w.status, m.role, m.can_export "
            "FROM memberships m JOIN workspaces w ON w.id=m.workspace_id "
            "WHERE m.user_id=? AND w.deleted_at IS NULL ORDER BY w.created_at", (uid,))

    def add_member(self, wid: str, uid: str, role: str, *, can_export: bool | None = None,
                   actor_id: str | None = None) -> dict:
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        ts = now()
        export = 1 if (can_export if can_export is not None else role != "viewer") else 0
        self.store.execute(
            "INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id,user_id) DO UPDATE SET "
            "role=excluded.role, can_export=excluded.can_export, updated_at=excluded.updated_at",
            (wid, uid, role, export, ts, ts, actor_id))
        return self.membership(wid, uid)

    def remove_member(self, wid: str, uid: str) -> None:
        """Removing the last owner is refused inside one transaction.

        Checking and deleting separately would let two concurrent removals each see a second
        owner and both proceed, leaving a workspace nobody can administer.
        """
        with self.store.tx() as t:
            row = t.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid)).fetchone()
            if not row:
                return
            if row["role"] == "owner":
                owners = t.execute("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role='owner'",
                                   (wid,)).fetchone()["n"]
                if owners <= 1:
                    raise ServiceError(409, "last_owner", "这是该空间最后一名所有者，请先指定另一位所有者")
            t.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid))

    def change_role(self, wid: str, uid: str, role: str) -> dict:
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        with self.store.tx() as t:
            row = t.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?", (wid, uid)).fetchone()
            if not row:
                raise ServiceError(404, "no_member", "该用户不是这个空间的成员")
            if row["role"] == "owner" and role != "owner":
                owners = t.execute("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role='owner'",
                                   (wid,)).fetchone()["n"]
                if owners <= 1:
                    raise ServiceError(409, "last_owner", "不能降级最后一名所有者")
            t.execute("UPDATE memberships SET role=?, can_export=?, updated_at=? WHERE workspace_id=? AND user_id=?",
                      (role, 0 if role == "viewer" else 1, now(), wid, uid))
        return self.membership(wid, uid)

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


def _personal_name(display_name: str | None, email: str) -> str:
    who = (display_name or "").strip() or (email or "").split("@")[0]
    return f"{who} 的空间"


# A real Argon2id hash of a random value, used to spend the same time on an unknown account as on
# a known one. Generated once at import so the cost matches the configured parameters exactly.
_DUMMY_PHC = passwords.hash_password(secrets.token_urlsafe(32))


REGISTRATION_MODES = ("open", "invite", "closed")


def registration_mode() -> str:
    """Default is invite-only.

    A first deployment that is reachable before anyone configures it must not let a passer-by
    open an account, so the safe mode is the default and opening it up is a deliberate act.
    """
    mode = (env.get("REGISTRATION") or "invite").strip().lower()
    return mode if mode in REGISTRATION_MODES else "invite"
