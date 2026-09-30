"""Delegated platform permissions: the catalogue, the built-in roles, the grants, and the one check.

Platform administration used to be one flag, `users.platform_role='admin'`, and every route under
`/api/admin` plus the API console asked the same yes/no question of it. Support staff who only
had to resend a recovery mail therefore held everything, including the power to make more
administrators. This module replaces the flag with three things that are separately visible:

* **Permissions** (`PERMISSIONS`): stable identifiers for real operations, split into viewing,
  managing, supporting and assigning. A permission never implies access to a workspace, a document
  or a conversation — platform permissions are not a superset of workspace permissions
  (docs/security/permissions.md §4.1), and no role here carries a business-content permission.
* **Built-in roles** (`ROLES`): named sets of permissions. `super_admin` is the whole catalogue by
  construction — that is the definition of the role, so a permission added to the catalogue is
  the super administrator's without further edits. Every other role lists its permissions, so a
  new sensitive permission reaches no delegated role silently.
* **Grants** (`platform_role_grants`): who holds which role, since when, from whom, why, and until
  when. A person's effective permissions are the union of their counted grants, recomputed on every
  request — so a revocation takes effect on the caller's next request, not at their next sign-in.

Adjustments to the draft catalogue in docs/platform/admin-coverage.md §3.1 B1-2, with the reason
for each (the brief asks that each be written here):

* `platform.orgs.manage` is added. `POST /api/admin/organizations/{oid}/{action}` is a write
  (freeze, delete, recover) and mapping it to `platform.orgs.view` would let a read-only role reach
  a write path. It is held by `super_admin` and `access_admin`, the two roles that already manage
  workspaces; the route still also requires organization ownership, as before.
* `platform.config.view` also covers the read-only external sign-in provider status
  (`GET /api/admin/identity-providers`): it is configuration, and it never returns a secret.
* `platform.workspaces.claim` is split from `platform.workspaces.manage`. Assigning the historical
  workspace (and, once it lands, taking that assignment back) hands every pre-identity document to
  a named account, which is business-content access, not a stop switch. Under `.manage` an
  account and access administrator could name themselves and read it all. It is the super
  administrator's only (by construction), and the claim itself refuses the actor and any account
  holding a live platform grant (`self_claim`, `target_is_platform_admin`): a platform role is
  never the way to a document, for whoever holds it. `.manage` keeps freeze and unfreeze.

Rules that are properties of the data rather than of a caller's discipline:

* **Who counts.** A grant counts only for an account that is active, not deleted, not an
  application identity (`app_accounts`), and whose address is verified. The one exception to the
  last condition is a grant made under host authority (`HOST_SOURCES`: the v27 migration, the
  bootstrap token, the CLI, or reconciliation of a legacy admin mirror after a rollback): the first administrator is created by bootstrap, which never verifies
  the address, so requiring it there would lock the only administrator out at migration. Host
  access is a stronger proof than a mail link; a delegated grant from another administrator is not.
* **Holding is not granting.** Granting or revoking needs `platform.roles.assign`, and
  `platform.roles.assign_super` for `super_admin`; the actor must also hold every permission of
  the role concerned (a lower administrator cannot act on a higher one).
* **A lower administrator does not act on a higher one.** Ending someone's sessions, mailing
  their recovery link, retrying their mail, suspending or restoring them is refused with 403
  `target_outranks_actor` (audited) when the target's live grants carry a permission the actor
  lacks (`require_outranking`). Live, not counted: a pending-deletion super administrator's grant
  counts again the moment the account is restored, so restoring it is acting on a super
  administrator.
* **Nobody changes their own authority to assign.** No one grants to or revokes from themselves a
  role that carries an assigning permission: no self-escalation, no self-demotion. A new highest
  role requires a different administrator's proposal and the recipient's dedicated real UV
  acceptance. Ordinary `grant(super_admin)` refuses; host/bootstrap authority remains explicit.
* **At least one usable super administrator.** Existing and host grants retain their current
  capability policy. New offer-linked grants also require an exact matching, completed readiness
  record: the recipient has entered the admin surface and proved a second dedicated UV challenge.
  Acceptance is a real grant but is not yet a usable successor. Readiness does not expire by the
  clock; current account, grant and authentication capability still apply. Ordinary dangerous
  operations continue to require separate strict scoped reauthentication by password or Passkey
  UV; dedicated acceptance/readiness never refreshes that generic scope. TOTP alone is not such a
  confirmation. Revoking the last usable
  super administrator is refused with 409 `last_super_admin`, checked inside the same
  `BEGIN IMMEDIATE` transaction as the write, so two super administrators revoking each other at
  the same moment cannot both succeed. Suspension of any grant holder is refused (`demote_first`)
  and self-deletion of the last usable super administrator is refused too. Passkey removal
  rechecks the same invariant under its writer lock. TOTP alone is not a strict reauth method.
* **A super administrator grant has no expiry.** An expiring one could leave the platform with no
  super administrator by the clock, where no checked action could refuse it.
* **Rollback mirror.** `users.platform_role` stays: `'admin'` exactly when the account holds a
  live super administrator grant, else `'none'`, written in the same transaction as every grant
  change. A delegated-only administrator keeps `'none'`, so running the previous release removes
  their access instead of widening it. `reconcile_mirror` (run by `Store` on every open) repairs
  the other direction: a demotion or promotion made by the previous release during a rollback is
  carried into the grants when this release starts again, and a leftover `'operator'` (a role with
  no capability, D17) becomes `'none'` with an audit line.

Explicit grant revocations are write-ahead entries in the security journal (`platform_grant_revoked`)
with a receipt committed alongside the change. Recovery removes that exact grant, including the
migration equivalent of a pre-v27 admin mirror, and preserves later deliberate regrants. A replay
that removes the last usable super administrator is refused; an initially unbootstrapped backup
may still be restored. Older restore tools reject this
new kind; recovery must use a reader supporting these entries. An older release also lacks the
B4 acceptance/readiness and challenge-context rules: additive schema is not a promise that a code
downgrade preserves these protections (docs/platform/admin-handover.md).
"""
from __future__ import annotations

import logging
import sqlite3
import time

from .errors import ServiceError
from .store import Store, new_id

#: Permission id → what it lets its holder do. Stable identifiers: they are stored nowhere (roles
#: are stored, permissions are derived) but they travel to the pages and into audit records.
PERMISSIONS: dict[str, str] = {
    "platform.overview.view": "view the platform overview counts",
    "platform.users.view": "list and search accounts",
    "platform.users.manage": "suspend and restore accounts",
    "platform.users.support": "send recovery mail and end an account's sessions",
    "platform.workspaces.view": "list workspaces and their members (counts, never content)",
    "platform.workspaces.manage": "freeze and unfreeze workspaces",
    "platform.workspaces.claim": "assign the historical (pre-identity) workspace to an ordinary account, and take it back",
    "platform.orgs.view": "list organizations",
    "platform.orgs.manage": "run an owned organization's lifecycle from the admin console",
    "platform.config.view": "read the registration policy, sign-in provider status and non-secret dynamic platform settings",
    "platform.config.manage": "change the registration policy and non-secret dynamic platform settings",
    "platform.mail.view": "read the mail outbox metadata",
    "platform.mail.manage": "retry a failed reset or verification mail",
    "platform.audit.view": "read the platform audit log",
    "platform.roles.view": "read the role catalogue, the administrator roster and permission explanations",
    "platform.roles.assign": "grant and revoke delegated platform roles",
    "platform.roles.assign_super": "grant and revoke the super administrator role",
    "api_platform.manage": "use the API management console",
}

ASSIGN = "platform.roles.assign"
ASSIGN_SUPER = "platform.roles.assign_super"
ASSIGNING = frozenset({ASSIGN, ASSIGN_SUPER})
SUPER_ADMIN = "super_admin"

ROLES: dict[str, frozenset[str]] = {
    # The whole catalogue, by construction: that is what the role means.
    SUPER_ADMIN: frozenset(PERMISSIONS),
    "access_admin": frozenset({
        "platform.overview.view", "platform.users.view", "platform.users.manage", "platform.users.support",
        "platform.workspaces.view", "platform.workspaces.manage", "platform.orgs.view", "platform.orgs.manage",
        "platform.config.view", "platform.mail.view", "platform.mail.manage", "platform.audit.view",
        "platform.roles.view"}),
    "support": frozenset({
        "platform.overview.view", "platform.users.view", "platform.users.support", "platform.workspaces.view",
        "platform.orgs.view", "platform.mail.view"}),
    "auditor": frozenset({
        "platform.overview.view", "platform.users.view", "platform.workspaces.view", "platform.orgs.view",
        "platform.config.view", "platform.mail.view", "platform.audit.view", "platform.roles.view"}),
    "api_platform_admin": frozenset({"platform.overview.view", "api_platform.manage"}),
}
ROLE_ORDER = (SUPER_ADMIN, "access_admin", "support", "auditor", "api_platform_admin")
assert set(ROLE_ORDER) == set(ROLES) and all(r <= set(PERMISSIONS) for r in ROLES.values())

#: Grants made under host authority rather than by another administrator. Reconciliation only
#: carries an existing legacy admin mirror forward; it cannot be requested by an HTTP caller.
HOST_SOURCES = ("migration:v27", "bootstrap:web", "bootstrap:cli", "reconcile:mirror")
#: Delegated grants may expire; not later than this from now.
MAX_EXPIRY_S = 366 * 24 * 3600
MIN_EXPIRY_S = 5 * 60
#: One denied-attempt audit row per actor and permission in this window, so probing cannot flood.
DENIED_AUDIT_WINDOW_S = 60

REASON_MIN, REASON_MAX = 8, 500

#: Reasons the service writes itself (host grants, reconciliation, deletion). Fixed sentences,
#: so the pages show them in the reader's language (PlatformAccess.tsx `SYSTEM_REASONS`, held to
#: this list by services/identity/web/tests/admin-access.test.mjs). A reason an administrator
#: typed is shown as typed.
SYSTEM_REASONS: dict[str, str] = {
    "migration": "平台管理员（platform_role=admin）迁移为最高管理员",
    "rollback_grant": "上一版本在回滚期间把该账户设为平台管理员",
    "rollback_revoke": "上一版本在回滚期间撤销了该账户的平台管理员角色",
    "bootstrap_web": "首位平台管理员（一次性引导令牌）",
    "bootstrap_cli": "命令行 bootstrap admin 创建的最高管理员",
    "account_deleted": "账户已删除",
    "journal_deleted": "账户已删除（安全日志重放）",
    "operator_cleared": "「平台运维」角色没有任何能力，迁移为无平台权限",
}

# Imported and executed by Store's migration only.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS platform_role_grants (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL
    CHECK (role IN ('super_admin','access_admin','support','auditor','api_platform_admin')),
  -- a user id, or a host authority ('migration:v27', 'bootstrap:web', 'bootstrap:cli'),
  -- or 'reconcile:mirror' for a change carried over from the previous release (platform_access.py)
  granted_by TEXT NOT NULL,
  granted_at REAL NOT NULL,
  reason TEXT NOT NULL,
  expires_at REAL,
  revoked_at REAL, revoked_by TEXT, revoke_reason TEXT
);
-- one live grant per (account, role); history stays as revoked rows
CREATE UNIQUE INDEX IF NOT EXISTS ux_platform_grants_live
  ON platform_role_grants(user_id, role) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_platform_grants_role ON platform_role_grants(role, revoked_at);
"""

_log = logging.getLogger("synwevo.platform_access")


def _now() -> float:
    return time.time()


def clock() -> float:
    """The time grant expiry is judged by. Every reader of grants (the routes' listings included)
    uses this one clock, so a grant cannot count in one answer and not in the next."""
    return _now()


def current_rp_id() -> str:
    """The domain passkeys are bound to now: `SYNWEVO_RP_ID`, else the issuer's host — the rule
    the account service's `passkeys.rp_id` and the shells' `passkeys_registered_elsewhere` apply."""
    from urllib.parse import urlparse
    from . import env
    from .oidc import issuer
    return (env.get("RP_ID") or "").strip() or (urlparse(issuer()).hostname or "localhost")


def sorted_permissions(perms) -> list[str]:
    order = list(PERMISSIONS)
    return sorted(perms, key=lambda p: order.index(p) if p in order else len(order))


def sorted_roles(roles) -> list[str]:
    return sorted(roles, key=lambda r: ROLE_ORDER.index(r) if r in ROLE_ORDER else len(ROLE_ORDER))


def catalogue() -> dict:
    """The catalogue as the pages read it: every permission, and every role with its set."""
    return {"permissions": [{"id": p, "description": d} for p, d in PERMISSIONS.items()],
            "roles": [{"id": r, "permissions": sorted_permissions(ROLES[r]),
                       "assigning": bool(ROLES[r] & ASSIGNING)} for r in ROLE_ORDER]}


def _reason(reason) -> str:
    if not isinstance(reason, str) or not REASON_MIN <= len(reason.strip()) <= REASON_MAX:
        raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
    return reason.strip()


class PlatformAccess:
    """Grants, effective permissions and their explanation. Every read goes through `store.one` /
    `store.all`, which join an enclosing `store.tx()` — so the same methods re-read the live state
    inside a write transaction."""

    def __init__(self, store: Store, audit=None):
        self.store = store
        # audit(action, *, actor_id, object_kind, object_id, result, meta) — bound to a request by
        # the caller; None for system paths, which write their own audit rows.
        self.audit = audit

    # ------------------------------------------------------------------ reading
    def _user(self, user_or_id) -> dict | None:
        if isinstance(user_or_id, dict):
            return user_or_id
        return self.store.one("SELECT * FROM users WHERE id=?", (user_or_id,))

    def _is_app_account(self, uid: str) -> bool:
        try:
            return self.store.one("SELECT 1 AS x FROM app_accounts WHERE user_id=?", (uid,)) is not None
        except sqlite3.OperationalError:
            return False

    def live_grants(self, uid: str, *, at: float | None = None) -> list[dict]:
        """Unrevoked, unexpired grants of this account, whether or not they count."""
        at = _now() if at is None else at
        return self.store.all(
            "SELECT * FROM platform_role_grants WHERE user_id=? AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at>?) ORDER BY granted_at, id", (uid, at))

    def holds_any_grant(self, uid: str) -> bool:
        return bool(self.live_grants(uid))

    def blocked_by(self, user: dict | None) -> list[str]:
        """Why this account's grants do not count at all (empty when they may)."""
        if not user or user.get("deleted_at") or user.get("status") == "deleted":
            return ["deleted"]
        out = []
        if user.get("status") != "active":
            out.append(f"status_{user.get('status')}")
        if self._is_app_account(user["id"]):
            out.append("application_identity")
        return out

    def _counts(self, user: dict, grant: dict) -> bool:
        return bool(user.get("email_verified_at")) or grant["granted_by"] in HOST_SOURCES

    def counted_grants(self, user_or_id) -> list[dict]:
        user = self._user(user_or_id)
        if not user or self.blocked_by(user):
            return []
        return [g for g in self.live_grants(user["id"]) if self._counts(user, g)]

    def effective_permissions(self, user_or_id) -> frozenset[str]:
        out: set[str] = set()
        for g in self.counted_grants(user_or_id):
            out |= ROLES.get(g["role"], frozenset())
        return frozenset(out)

    def roles(self, user_or_id) -> list[str]:
        """The roles that count now (what the account may use)."""
        return sorted_roles({g["role"] for g in self.counted_grants(user_or_id)})

    def live_roles(self, uid: str) -> list[str]:
        """The roles of every live grant, counted or not — what a grant or revocation changed.
        The audit records these: a grant ended on a suspended account is still a change."""
        return sorted_roles({g["role"] for g in self.live_grants(uid)})

    def step_up(self, uid: str) -> dict:
        """What this account could use to reach an admin-capable session (sessions.admin_capable):
        a password sign-in, a user-verifying passkey, or a second factor (TOTP) after a mailed
        code or an external provider. `reauth` is the strict re-authentication the dangerous
        operations ask for: a password or a fresh, user-verified Passkey ceremony.

        A passkey counts only when it is bound to the current RP ID: one registered under an
        earlier domain is never offered by the browser again (passkeys.py), so it proves nothing
        an administrator could use."""
        password = self.store.one("SELECT 1 AS x FROM user_passwords WHERE user_id=?", (uid,)) is not None
        try:
            passkey = self.store.one("SELECT 1 AS x FROM passkeys WHERE user_id=? AND uv_capable=1 "
                                     "AND deleted_at IS NULL AND rp_id=?", (uid, current_rp_id())) is not None
        except sqlite3.OperationalError:
            passkey = False
        totp = self.store.one("SELECT 1 AS x FROM totp_secrets WHERE user_id=? AND confirmed_at IS NOT NULL",
                              (uid,)) is not None
        return {"capable": password or passkey or totp, "password": password, "passkey_uv": passkey,
                "totp": totp, "reauth": password or passkey}

    def usable_super_admins(self, *, excluding: str | None = None) -> list[dict]:
        """Currently capable highest administrators; offer-linked grants must also be ready.

        Grandfathered/host grants keep their existing capability policy. A pending readiness
        proof must not unlock removal of the last administrator who was already usable.
        """
        rows = self.store.all(
            "SELECT DISTINCT u.* FROM users u JOIN platform_role_grants g ON g.user_id=u.id "
            "WHERE g.role=? AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>?) "
            "AND u.status='active' AND u.deleted_at IS NULL ORDER BY u.created_at, u.id",
            (SUPER_ADMIN, _now()))
        return [u for u in rows if u["id"] != excluding and self.is_usable_super_admin(u)]

    def is_usable_super_admin(self, user_or_id) -> bool:
        user = self._user(user_or_id)
        if not user or not any(g["role"] == SUPER_ADMIN and (
                not g.get("offer_id") or self.store.one(
                    "SELECT 1 FROM platform_super_admin_offers WHERE id=? AND accepted_grant_id=? "
                    "AND recipient_id=? AND state='ready' AND ready_at IS NOT NULL",
                    (g["offer_id"], g["id"], user["id"]))) for g in self.counted_grants(user)):
            return False
        step = self.step_up(user["id"])
        # A super administrator who cannot re-authenticate can grant, revoke
        # and change nothing, so the platform would be left with nobody able to administer it.
        return step["capable"] and step["reauth"]

    def live_permissions(self, uid: str) -> frozenset[str]:
        """Every permission this account's live grants carry, whether or not they count now (a
        suspended or pending-deletion account's grants count again once it is restored)."""
        out: set[str] = set()
        for g in self.live_grants(uid):
            out |= ROLES.get(g["role"], frozenset())
        return frozenset(out)

    def outranking(self, held: frozenset, target_id: str) -> frozenset[str]:
        """The permissions `target_id`'s live grants carry that `held` lacks: empty when a holder
        of `held` may act on that account."""
        return self.live_permissions(target_id) - held

    def explain(self, user_or_id) -> dict:
        """Each effective permission with the grants it comes from, plus every live grant and
        whether it counts — the answer to "why may (or may not) this person do this"."""
        user = self._user(user_or_id)
        if not user:
            raise ServiceError(404, "no_user", "找不到该账户")
        blocked = self.blocked_by(user)
        grants = []
        people = {}
        for g in self.live_grants(user["id"]):
            counts = not blocked and self._counts(user, g)
            if g["granted_by"] not in people and not g["granted_by"].count(":"):
                row = self.store.one("SELECT email FROM users WHERE id=?", (g["granted_by"],))
                people[g["granted_by"]] = (row or {}).get("email")
            grants.append({"id": g["id"], "role": g["role"], "granted_by": g["granted_by"],
                           "granted_by_email": people.get(g["granted_by"]), "granted_at": g["granted_at"],
                           "expires_at": g["expires_at"], "reason": g["reason"], "counted": counts,
                           **({} if counts or blocked else {"not_counted": "email_unverified"})})
        perms: dict[str, list[dict]] = {}
        for g in grants:
            if not g["counted"]:
                continue
            for p in ROLES.get(g["role"], ()):
                perms.setdefault(p, []).append({k: g[k] for k in ("id", "role", "granted_by", "granted_by_email",
                                                                   "granted_at", "expires_at")})
        return {"user_id": user["id"], "email": user.get("email"), "status": user.get("status"),
                "email_verified": bool(user.get("email_verified_at")), "blocked_by": blocked,
                "step_up": self.step_up(user["id"]),
                "super_admin_usable": self.is_usable_super_admin(user),
                "roles": sorted_roles({g["role"] for g in grants if g["counted"]}),
                "permissions": [{"permission": p, "sources": perms[p]} for p in sorted_permissions(perms)],
                "grants": grants}

    # ------------------------------------------------------------------ writing
    def _sync_mirror(self, uid: str, ts: float) -> None:
        """users.platform_role, the rollback mirror: 'admin' exactly while a live super
        administrator grant exists. Called inside every transaction that changes grants."""
        self.store.execute(
            "UPDATE users SET platform_role=CASE WHEN EXISTS (SELECT 1 FROM platform_role_grants g "
            " WHERE g.user_id=users.id AND g.role=? AND g.revoked_at IS NULL "
            " AND (g.expires_at IS NULL OR g.expires_at>?)) THEN 'admin' ELSE 'none' END, updated_at=? "
            "WHERE id=? AND platform_role IS NOT CASE WHEN EXISTS (SELECT 1 FROM platform_role_grants g "
            " WHERE g.user_id=users.id AND g.role=? AND g.revoked_at IS NULL "
            " AND (g.expires_at IS NULL OR g.expires_at>?)) THEN 'admin' ELSE 'none' END",
            (SUPER_ADMIN, ts, ts, uid, SUPER_ADMIN, ts))

    def _tighten_sessions(self, uid: str) -> int:
        """Bring the grantee's existing central sessions under the administrator rule that
        sign-in applies (`Sessions.create(admin=True)`): the administrator idle window and no
        "keep me signed in". Sessions of relying parties are not administrator sessions and keep
        theirs. A session idle for longer than the window ends now."""
        from .sessions import ADMIN_IDLE_SECONDS
        return self.store.execute(
            "UPDATE sessions SET idle_expires_at=MIN(idle_expires_at, last_seen_at+?), persistent=0 "
            "WHERE user_id=? AND revoked_at IS NULL AND oidc_sid IS NULL AND central_session_id IS NULL "
            "AND (idle_expires_at>last_seen_at+? OR persistent=1)",
            (ADMIN_IDLE_SECONDS, uid, ADMIN_IDLE_SECONDS))

    def _require_actor(self, actor_id: str, role: str) -> frozenset[str]:
        actor = self._user(actor_id)
        held = self.effective_permissions(actor) if actor else frozenset()
        needed = {ASSIGN} | ({ASSIGN_SUPER} if role == SUPER_ADMIN else set())
        if not needed <= held:
            raise ServiceError(403, "platform_permission_denied", "你的平台角色不包含这项操作所需的权限",
                               {"permission": sorted_permissions(needed - held)[0]})
        if not ROLES[role] <= held:
            raise ServiceError(403, "role_exceeds_own", "只能授予或撤销由你自己持有的权限组成的平台角色",
                               {"role": role})
        return held

    def _audit(self, action: str, *, actor_id: str | None, target: str, meta: dict) -> None:
        if self.audit is not None:
            self.audit(action, actor_id=actor_id, object_kind="user", object_id=target, result="ok", meta=meta)
        else:
            _system_audit(self.store, action, target, meta)

    def grant(self, actor_id: str, target_id: str, role: str, reason: str, expires_at=None) -> dict:
        return self._grant(actor_id, target_id, role, reason, expires_at)

    def _grant_accepted_offer(self, offer_id: str) -> dict:
        """Internal: the UV verifier has written this exact acceptance receipt in our writer tx.

        No HTTP field may supply this receipt. The surrounding operation commits proof, role,
        current session factor, result and audit together, or rolls them all back.
        """
        with self.store.conn() as c:
            if not c.in_transaction:
                raise RuntimeError("offer acceptance requires a write transaction")
        offer = self.store.one("SELECT * FROM platform_super_admin_offers WHERE id=? AND state='pending' "
                               "AND accepted_challenge_id IS NOT NULL AND accepted_passkey_id IS NOT NULL", (offer_id,))
        if not offer:
            raise ServiceError(409, "super_admin_acceptance_required", "最高管理员须由本人通过安全密钥接受提议")
        challenge = self.store.one("SELECT 1 FROM reauth_challenges WHERE id=? AND user_id=? AND session_id=? "
                                   "AND context_kind='super_admin_accept' AND context_id=? AND used_at IS NOT NULL",
                                   (offer["accepted_challenge_id"], offer["recipient_id"], offer["accepted_session_id"], offer_id))
        original = self.grant_row(offer["proposer_grant_id"])
        if not challenge or not original or original["user_id"] != offer["proposer_id"] or original["role"] != SUPER_ADMIN \
                or original["revoked_at"] is not None or offer["expires_at"] <= _now():
            raise ServiceError(409, "super_offer_authority_changed", "提议的原授权已失效，请由最高管理员重新发起")
        return self._grant(offer["proposer_id"], offer["recipient_id"], SUPER_ADMIN, offer["reason"], offer_id=offer_id)

    def _grant(self, actor_id: str, target_id: str, role: str, reason: str, expires_at=None, *, offer_id=None) -> dict:
        if role not in ROLES:
            raise ServiceError(400, "bad_platform_role", "请选择一个内置平台角色")
        reason = _reason(reason)
        ts = _now()
        if expires_at is not None:
            if role == SUPER_ADMIN:
                raise ServiceError(400, "super_admin_no_expiry",
                                   "最高管理员不设期限：到期会在无人操作的情况下让平台失去最高管理员")
            if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) \
                    or not ts + MIN_EXPIRY_S <= expires_at <= ts + MAX_EXPIRY_S:
                raise ServiceError(400, "bad_expiry", "到期时间须在 5 分钟之后、一年之内",
                                   {"field": "expires_at"})
        with self.store.tx():
            self._require_actor(actor_id, role)
            if target_id == actor_id and ROLES[role] & ASSIGNING:
                raise ServiceError(403, "self_assignment",
                                   "不能给自己授予或撤销带授权能力的平台角色，请由另一位最高管理员操作")
            self._precondition(target_id, role)
            if role == SUPER_ADMIN and not offer_id:
                raise ServiceError(409, "super_admin_acceptance_required", "最高管理员须由本人通过安全密钥接受提议")
            live = self.store.one("SELECT * FROM platform_role_grants WHERE user_id=? AND role=? "
                                  "AND revoked_at IS NULL", (target_id, role))
            replaced = None
            if live and live["expires_at"] is not None and live["expires_at"] <= ts:
                # The expired row is closed here so the new one can take its place; the grant's
                # audit names it, so the record of that row's end is not a silent one.
                self.store.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:expired', "
                                   "revoke_reason='expired' WHERE id=?", (ts, live["id"]))
                replaced = live["id"]
            elif live:
                raise ServiceError(409, "already_granted", "该账户已经持有这个平台角色；要改期限，请先撤销再授予")
            before = self.live_roles(target_id)
            gid = new_id("pg")
            self.store.execute(
                "INSERT INTO platform_role_grants (id,user_id,role,granted_by,granted_at,reason,expires_at,offer_id) "
                "VALUES (?,?,?,?,?,?,?,?)", (gid, target_id, role, actor_id, ts, reason, expires_at, offer_id))
            self._sync_mirror(target_id, ts)
            after = self.live_roles(target_id)
            tightened = self._tighten_sessions(target_id)
            self._audit("admin.platform.grant", actor_id=target_id if offer_id else actor_id, target=target_id,
                        meta={"grant_id": gid, "role": role, "before": before, "after": after,
                              "reason": reason, "expires_at": expires_at, "sessions_tightened": tightened,
                              **({"offer_id": offer_id, "authorized_by": actor_id} if offer_id else {}),
                              **({"replaced_expired_grant": replaced} if replaced else {})})
        return {"grant": self.grant_row(gid), "before": before, "after": after, "sessions_tightened": tightened}

    def _precondition(self, target_id: str, role: str) -> dict:
        """What an account must be to receive a platform role (closes D2)."""
        target = self._user(target_id)
        if not target or target.get("deleted_at") or target.get("status") == "deleted":
            raise ServiceError(404, "no_user", "找不到该账户")
        if self._is_app_account(target_id):
            raise ServiceError(409, "application_identity", "应用身份不是人，不能持有平台权限")
        if target["status"] == "pending":
            raise ServiceError(409, "target_pending", "该账户还没有完成邮箱验证，不能获得平台权限")
        if target["status"] != "active":
            raise ServiceError(409, "target_unavailable", "该账户已停用或正在删除，不能获得平台权限")
        if not target.get("email_verified_at"):
            raise ServiceError(409, "target_unverified", "该账户的邮箱尚未验证，不能获得平台权限")
        if not self.step_up(target_id)["capable"]:
            raise ServiceError(409, "target_cannot_step_up",
                               "该账户没有口令、可验证身份的安全密钥或动态验证码，无法完成管理员身份确认；请先让本人设置")
        if role == SUPER_ADMIN and not self.step_up(target_id)["reauth"]:
            # The account must be able to deliberately confirm a dangerous operation,
            # with either its password or a usable, user-verifying Passkey.
            raise ServiceError(409, "target_cannot_reauth",
                               "该账户没有口令或可验证身份的安全密钥，无法完成危险操作要求的重新验证")
        return target

    def grant_row(self, gid: str) -> dict | None:
        return self.store.one("SELECT * FROM platform_role_grants WHERE id=?", (gid,))

    def revoke(self, actor_id: str, grant_id: str, reason: str) -> dict:
        reason = _reason(reason)
        with self.store.tx():
            g = self.grant_row(grant_id)
            if not g:
                raise ServiceError(404, "no_grant", "找不到这项平台授权")
            return self._revoke(actor_id, g, reason)

    def revoke_role(self, actor_id: str, target_id: str, role: str, reason: str) -> dict:
        reason = _reason(reason)
        with self.store.tx():
            g = self.store.one("SELECT * FROM platform_role_grants WHERE user_id=? AND role=? "
                               "AND revoked_at IS NULL", (target_id, role))
            if not g:
                raise ServiceError(404, "no_grant", "找不到这项平台授权")
            return self._revoke(actor_id, g, reason)

    def revoke_all(self, actor_id: str, target_id: str, reason: str) -> dict:
        """Every live grant of one account, in one transaction under the same rules (the
        compatibility path of `platform-role: none`)."""
        reason = _reason(reason)
        with self.store.tx():
            if not self._user(target_id):
                raise ServiceError(404, "no_user", "找不到该账户")
            rows = self.store.all("SELECT * FROM platform_role_grants WHERE user_id=? AND revoked_at IS NULL "
                                  "ORDER BY granted_at, id", (target_id,))
            # Complete every permission check and audit before any durable restore intent.
            out = [self._revoke(actor_id, g, reason, journal=False) for g in rows]
            from .journal import record_platform_revocation
            for item in out:
                record_platform_revocation(self.store, item["grant"], at=item["grant"]["revoked_at"])
        return {"revoked": [o["grant"]["id"] for o in out], "after": out[-1]["after"] if out else self.live_roles(target_id)}

    def _revoke(self, actor_id: str, g: dict, reason: str, *, journal: bool = True) -> dict:
        ts = _now()
        if g["revoked_at"] is not None:
            raise ServiceError(409, "grant_revoked", "这项授权已经撤销")
        self._require_actor(actor_id, g["role"])
        uid = g["user_id"]
        if uid == actor_id and ROLES[g["role"]] & ASSIGNING:
            raise ServiceError(403, "self_assignment",
                               "不能给自己授予或撤销带授权能力的平台角色，请由另一位最高管理员操作")
        if g["role"] == SUPER_ADMIN and self.is_usable_super_admin(uid) \
                and not self.usable_super_admins(excluding=uid):
            raise ServiceError(409, "last_super_admin",
                               "这是最后一名可用的最高管理员。请先授予另一位最高管理员，并确认对方能登录并完成身份确认")
        before = self.live_roles(uid)
        self.store.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by=?, revoke_reason=? "
                           "WHERE id=? AND revoked_at IS NULL", (ts, actor_id, reason, g["id"]))
        self._sync_mirror(uid, ts)
        after = self.live_roles(uid)
        self._audit("admin.platform.revoke", actor_id=actor_id, target=uid,
                    meta={"grant_id": g["id"], "role": g["role"], "before": before, "after": after,
                          "reason": reason})
        if journal:
            from .journal import record_platform_revocation
            record_platform_revocation(self.store, g, at=ts)
        return {"grant": self.grant_row(g["id"]), "before": before, "after": after}

    # ------------------------------------------------------------------ host authority
    def host_grant(self, uid: str, role: str, *, source: str, reason: str) -> dict:
        """A grant made by whoever controls the host — bootstrap token, CLI, migration — or by a
        test fixture that says so in `source`. No actor, no assigning check: the authority is
        access to the deployment itself. Joins an enclosing transaction."""
        if role not in ROLES or ":" not in source:
            raise ValueError("host grants name a built-in role and a 'kind:detail' source")
        ts = _now()
        with self.store.tx():
            if self.store.one("SELECT 1 AS x FROM platform_role_grants WHERE user_id=? AND role=? "
                              "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)", (uid, role, ts)):
                return {"grant": None, "unchanged": True}
            # nothing unexpired is left for (uid, role): any unrevoked row is an expired one
            replaced = [r["id"] for r in self.store.all(
                "SELECT id FROM platform_role_grants WHERE user_id=? AND role=? AND revoked_at IS NULL", (uid, role))]
            self.store.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by='system:expired', "
                               "revoke_reason='expired' WHERE user_id=? AND role=? AND revoked_at IS NULL", (ts, uid, role))
            before = self.live_roles(uid)
            gid = new_id("pg")
            self.store.execute(
                "INSERT INTO platform_role_grants (id,user_id,role,granted_by,granted_at,reason) VALUES (?,?,?,?,?,?)",
                (gid, uid, role, source, ts, reason))
            self._sync_mirror(uid, ts)
            self._tighten_sessions(uid)
            _system_audit(self.store, "admin.platform.grant", uid,
                          {"grant_id": gid, "role": role, "source": source, "reason": reason,
                           "before": before, "after": self.live_roles(uid),
                           **({"replaced_expired_grant": replaced[0]} if replaced else {})})
        return {"grant": self.grant_row(gid)}

    def system_revoke_all(self, uid: str, *, source: str, reason: str) -> int:
        """End every live grant of an account the system is deleting. No last-administrator check:
        an account that is being deleted is no longer active, so it is not counted already. Each
        ended grant gets its `admin.platform.revoke` record in the same transaction, as every
        revocation does."""
        ts = _now()
        with self.store.tx():
            rows = self.store.all("SELECT * FROM platform_role_grants WHERE user_id=? AND revoked_at IS NULL "
                                  "ORDER BY granted_at, id", (uid,))
            for g in rows:
                before = self.live_roles(uid)
                self.store.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by=?, revoke_reason=? "
                                   "WHERE id=? AND revoked_at IS NULL", (ts, source, reason, g["id"]))
                _system_audit(self.store, "admin.platform.revoke", uid,
                              {"grant_id": g["id"], "role": g["role"], "source": source, "reason": reason,
                               "before": before, "after": self.live_roles(uid)})
            self._sync_mirror(uid, ts)
        return len(rows)


def _system_audit(store: Store, action: str, target: str, meta: dict) -> None:
    import json
    store.execute(
        "INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,object_id,"
        "result,request_id,ip,meta) VALUES (?,?,'system',NULL,NULL,?,'user',?,'ok',NULL,NULL,?)",
        (new_id("ae"), _now(), action, target, json.dumps(meta, ensure_ascii=False)))


def reconcile_mirror(store: Store, *, source: str) -> int:
    """Carry `users.platform_role` into the grants where the two disagree. Runs inside the
    caller's transaction; a no-op while every grant change keeps the mirror in step.

    * `'operator'` → `'none'` (it never carried a capability, D17), with an audit line.
    * `'admin'` without a live super administrator grant → a grant from `source` (the v27
      migration of every existing administrator; later, a promotion the previous release made
      during a rollback).
    * a live super administrator grant on an account whose mirror is not `'admin'` → revoked
      (a demotion made by the previous release, or a deletion the security journal replayed).
    """
    access = PlatformAccess(store)
    ts = _now()
    changed = 0
    for row in store.all("SELECT id FROM users WHERE platform_role='operator'"):
        store.execute("UPDATE users SET platform_role='none', updated_at=? WHERE id=?", (ts, row["id"]))
        _system_audit(store, "admin.platform.operator_cleared", row["id"],
                      {"source": source, "before": "operator", "after": "none",
                       "reason": SYSTEM_REASONS["operator_cleared"]})
        changed += 1
    for row in store.all(
            "SELECT u.id FROM users u WHERE u.platform_role='admin' AND NOT EXISTS (SELECT 1 FROM "
            "platform_role_grants g WHERE g.user_id=u.id AND g.role=? AND g.revoked_at IS NULL "
            "AND (g.expires_at IS NULL OR g.expires_at>?))", (SUPER_ADMIN, ts)):
        access.host_grant(row["id"], SUPER_ADMIN, source=source,
                          reason=SYSTEM_REASONS["migration" if source == "migration:v27" else "rollback_grant"])
        changed += 1
    for row in store.all(
            "SELECT g.*, u.status, u.deleted_at FROM platform_role_grants g JOIN users u ON u.id=g.user_id "
            "WHERE g.role=? AND g.revoked_at IS NULL AND u.platform_role<>'admin'", (SUPER_ADMIN,)):
        # Say what happened: a deleted account's mirror was cleared by the deletion (a journal
        # replay, or the previous release's sweep), not by a demotion.
        deleted = row["status"] == "deleted" or row["deleted_at"] is not None
        reason = SYSTEM_REASONS["account_deleted" if deleted else "rollback_revoke"]
        store.execute("UPDATE platform_role_grants SET revoked_at=?, revoked_by=?, revoke_reason=? WHERE id=?",
                      (ts, source, reason, row["id"]))
        _system_audit(store, "admin.platform.revoke", row["user_id"],
                      {"grant_id": row["id"], "role": SUPER_ADMIN, "source": source, "reason": reason,
                       "cause": "users.platform_role 不再是 admin"})
        if not deleted:
            from .journal import record_platform_revocation
            record_platform_revocation(store, row, at=ts)
        changed += 1
    return changed


# ---------------------------------------------------------------------- the one request check
def audit_denied(store: Store, audit, request, uid: str, object_id: str, *, object_kind: str = "permission",
                 code: str = "platform_permission_denied", meta: dict | None = None) -> None:
    """A denied attempt by someone who holds other platform permissions: recorded, at most once per
    actor and object (the missing permission, or the account acted on) per window, so a page that
    keeps asking cannot flood the log. The refusal stands whether or not the record could be
    written."""
    if audit is None:
        return
    try:
        recent = store.one("SELECT 1 AS x FROM audit_events WHERE actor_id=? AND action='admin.access.denied' "
                           "AND object_kind=? AND object_id=? AND at>? LIMIT 1",
                           (uid, object_kind, object_id, _now() - DENIED_AUDIT_WINDOW_S))
        if recent:
            return
        url = getattr(request, "url", None)
        audit(request, "admin.access.denied", actor_id=uid, object_kind=object_kind, object_id=object_id,
              result="denied", meta={"code": code, "method": getattr(request, "method", None),
                                     "path": getattr(url, "path", None), **(meta or {})})
    except Exception:                                  # noqa: BLE001 - the refusal does not depend on it
        _log.exception("could not record a denied platform request")


def require_outranking(request, actor: dict, target_id: str, *, store: Store, audit=None) -> None:
    """Refuse to act on an account whose live platform grants carry a permission the actor lacks
    ("A lower administrator does not act on a higher one"): 403 `target_outranks_actor`, audited
    like any denied attempt. Acting on oneself is not ranked. `actor` is `require_platform`'s
    answer (it carries `platform_permissions`)."""
    if target_id == actor["id"]:
        return
    missing = PlatformAccess(store).outranking(actor["platform_permissions"], target_id)
    if missing:
        audit_denied(store, audit, request, actor["id"], target_id, object_kind="user",
                     code="target_outranks_actor", meta={"permissions": sorted_permissions(missing)})
        raise ServiceError(403, "target_outranks_actor",
                           "该账户持有你没有的平台权限：只有权限不低于它的管理员才能对它执行这项操作",
                           {"permissions": sorted_permissions(missing)})


def require_platform(request, permission: str | None, *, store: Store, audit=None,
                     dangerous: bool = False, step_up: bool = True) -> dict:
    """The one check every `/api/admin` route and the API console use. Returns the caller's user
    row (with `platform_permissions`), or raises, in this order:

    1. no session → 401; an account that is not active → 401;
    2. no platform permission at all → 404, the same answer as a page that does not exist (an
       ordinary account, an organization owner or a workspace administrator learns nothing);
    3. some platform permissions but not `permission` → 403 `platform_permission_denied`, audited;
    4. a session that proved one factor → 403 `step_up_required` (`sessions.admin_capable`);
    5. `dangerous` without a strict re-authentication for `admin` → 403 `reauth_required`.

    `permission=None` asks only for "any platform permission" (the caller describing themselves);
    `step_up=False` skips 4 for the same reason — a locked administrator must be able to learn
    what is missing.
    """
    from .sessions import Sessions, admin_capable
    # Admission may predate a queued write. Re-read without touching the idle clock; callers
    # performing a mutation repeat this check inside its Store.tx() so revocation and execution
    # serialize. Do not trust the request's earlier AMR or re-authentication window either.
    s = Sessions(store).current(getattr(request.state, "session", None))
    request.state.session = s
    if not s:
        raise ServiceError(401, "unauthorized", "请先登录")
    user = store.one("SELECT * FROM users WHERE id=? AND deleted_at IS NULL", (s["user_id"],))
    if not user or user["status"] != "active":
        raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
    perms = PlatformAccess(store).effective_permissions(user)
    if not perms:
        # Same shape as a missing page: an ordinary account learns nothing about what exists.
        raise ServiceError(404, "not_found", "页面不存在")
    if permission is not None and permission not in perms:
        audit_denied(store, audit, request, user["id"], permission)
        raise ServiceError(403, "platform_permission_denied", "你的平台角色不包含这项操作所需的权限",
                           {"permission": permission})
    if step_up and not admin_capable(s.get("amr") or ""):
        # The role is real; this session proved one factor (a mailed code, an external provider).
        # Platform administration waits for a second one — the password on the account page, or
        # a second-factor sign-in.
        raise ServiceError(403, "step_up_required", "当前登录只验证了一个因素：请在账户页输入口令确认身份后再使用平台管理",
                           {"scope": "admin"})
    # strict=True: signing in a moment ago is not the same as confirming *this*.
    if dangerous and not Sessions.reauth_fresh(s, "admin", strict=True):
        raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": "admin"})
    request.state.platform_permissions = perms
    return {**user, "platform_permissions": perms}
