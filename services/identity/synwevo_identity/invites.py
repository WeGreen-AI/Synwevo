"""Invitations, email verification and password reset — the token-carrying flows.

Shared shape for every token here:

* high entropy, generated server-side, **stored only as a SHA-256**, so a database read cannot
  be turned into an accepted invitation or a password reset;
* short-lived and single-use, with consumption done by an UPDATE whose WHERE clause *is* the
  check, so two concurrent requests cannot both win;
* opening the emailed link never completes anything. The link leads to a page; the action needs
  a deliberate POST. Mail scanners and link-preview bots fetch URLs routinely, and a flow that
  acts on GET hands them the ability to accept invitations and burn reset links.

An invitation binds four things — workspace, email, role, and who issued it — and each of them
can independently make it invalid later.
"""
from __future__ import annotations

import hashlib
import secrets

from synwevo_core import journal
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, ROLE_RANK, ROLES, normalise_email, valid_email
from synwevo_core.store import Store, new_id, now

INVITE_TTL_S = 7 * 24 * 3600
INVITE_TTL_MIN_S = 24 * 3600
INVITE_TTL_MAX_S = 30 * 24 * 3600
# Invitations a workspace may issue in any rolling 24 hours (GitHub: 50 for a new organisation).
# Every invitation is a mail sent from this service in the workspace's name; without a ceiling a
# compromised admin account turns it into a spam relay with a trusted From line.
INVITES_PER_DAY = 50
VERIFY_TTL_S = 24 * 3600
RESET_TTL_S = 2 * 3600
STATES = ("pending", "expired", "revoked", "accepted", "declined")

ROLE_LABEL = {"viewer": "只读成员", "editor": "编辑成员", "admin": "空间管理员", "owner": "空间所有者"}


def _digest(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


# ================================================================ invitees as administration sees them
# A platform administrator governs invitations without learning whom they were sent to (A47): every
# administration surface that shows an invitation or its mail — the invitation oversight and the
# mail tab — masks the invitee with this one function and searches invitees with this one rule.

def masked_address(address: str) -> tuple[str, str]:
    """(masked address, domain): the local part's first character, `***`, then `@domain`
    (`a***@example.com`). Never the local part beyond its first character."""
    local, _, domain = (address or "").rpartition("@")
    return f"{local[:1]}***@{domain}", domain


def like_escape(text: str) -> str:
    """`text` as a literal inside a LIKE pattern written with ESCAPE '\\'."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def invitee_search(q: str) -> tuple[str | None, str | None]:
    """How an invitee may be searched for: only whole. `(exact key, None)` for an address,
    `(None, LIKE suffix pattern)` for `@domain` (match with ESCAPE '\\'), `(None, None)` for
    anything else — a fragment of an address finds no invitee, so a search cannot spell one out."""
    q = (q or "").strip()
    if q.startswith("@") and len(q) > 1:
        return None, "%" + like_escape(normalise_email(q))
    if "@" in q:
        return normalise_email(q), None
    return None, None


class Invites:
    def __init__(self, store: Store, ident: Identity):
        self.store = store
        self.ident = ident

    # ================================================================ invitations
    def may_invite(self, actor_id: str, workspace_id: str, role: str) -> None:
        """Can this person issue this invitation, right now?

        Two rules that are easy to miss:
        * you cannot grant a role above your own — otherwise an admin invites someone as owner
          and has effectively promoted themselves by proxy;
        * platform role grants nothing here. A platform admin manages accounts and service
          state; it does not make them a member of a workspace they were never added to.
        """
        from synwevo_core.organizations import require_invitation_permission
        require_invitation_permission(self.store, workspace_id, actor_id)
        m = self.ident.membership(workspace_id, actor_id)
        if not m or m["role"] not in ("admin", "owner"):
            raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以邀请成员")
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        if ROLE_RANK[role] > ROLE_RANK[m["role"]]:
            raise ServiceError(403, "role_too_high", "不能邀请比自己权限更高的角色")

    def create(self, **kwargs) -> tuple[dict, str]:
        # Authority, limits, superseding and issuance share one write transaction.
        with self.store.tx():
            return self._create(**kwargs)

    def _create(self, *, workspace_id: str, email: str, role: str, actor_id: str,
               can_export: bool | None = None, ttl_s: int = INVITE_TTL_S,
               invited_via: str | None = None, invited_approval_id: str | None = None) -> tuple[dict, str]:
        if not INVITE_TTL_MIN_S <= ttl_s <= INVITE_TTL_MAX_S:
            raise ServiceError(400, "bad_expiry", "邀请有效期必须在 1 到 30 天之间")
        if not valid_email(email):
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        self.may_invite(actor_id, workspace_id, role)
        if invited_via:
            from .workspace_actor import require_role, service_approval
            require_role(role)
            approval = service_approval(self.store, invited_via, workspace_id, approval_id=invited_approval_id)
            if not invited_approval_id or approval["approved_by"] != actor_id:
                raise ServiceError(403, "approval_inactive", "邀请必须绑定当前批准人与原审批")
        ws = self.ident.get_workspace(workspace_id)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        if ws["status"] != "active":
            # The caller is an admin or owner of it (may_invite passed), so the workspace's
            # existence is no secret from them: say what is actually wrong, with the same code
            # every other governance route uses for a workspace that gains no access.
            if ws["status"] == "frozen":
                raise ServiceError(409, "workspace_frozen", "该工作空间已被冻结，暂时不能增加成员或权限")
            raise ServiceError(409, "workspace_unavailable", "该工作空间当前不可用")

        key = normalise_email(email)
        if invited_via and self.store.one(
                "SELECT 1 FROM invitations WHERE workspace_id=? AND email_key=? "
                "AND role IN ('admin','owner') AND accepted_at IS NULL AND revoked_at IS NULL", (workspace_id, key)):
            # Superseding an administrator invitation is itself a privileged role change.
            require_role("admin")
        existing_user = self.ident.by_email(key)
        if existing_user and self.ident.membership(workspace_id, existing_user["id"]):
            raise ServiceError(409, "already_member", "该邮箱已经是这个空间的成员")

        latest = self.store.one("SELECT created_at FROM invitations WHERE workspace_id=? AND email_key=? ORDER BY created_at DESC LIMIT 1", (workspace_id, key))
        if latest and now() - latest["created_at"] < 60:
            raise ServiceError(429, "invite_cooldown", "请稍后再发送邀请", {"retry_after": max(1, int(60 - now() + latest["created_at"]))})
        raw = _token()
        row = {"id": new_id("iv"), "email_key": key, "workspace_id": workspace_id, "role": role,
               "can_export": 1 if (can_export if can_export is not None else role != "viewer") else 0,
               "token_hash": _digest(raw), "invited_by": actor_id,
               "invited_via": invited_via, "invited_approval_id": invited_approval_id,
               "created_at": now(), "expires_at": now() + ttl_s}
        with self.store.tx() as t:
            # Counted inside the same transaction as the insert, so concurrent requests cannot
            # each see 49 and all proceed.
            window = t.execute("SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM invitations "
                               "WHERE workspace_id=? AND created_at>?",
                               (workspace_id, now() - 24 * 3600)).fetchone()
            if window["n"] >= INVITES_PER_DAY:
                retry = int((window["oldest"] or now()) + 24 * 3600 - now()) + 1
                raise ServiceError(429, "invite_rate_limited",
                                   f"这个空间 24 小时内最多发出 {INVITES_PER_DAY} 个邀请，请稍后再试",
                                   {"retry_after": max(1, retry), "limit": INVITES_PER_DAY})
            # One live invitation per (workspace, email). Re-inviting supersedes rather than
            # accumulating, so revoking "the" invitation cannot leave an older one usable.
            superseded = [r["id"] for r in t.execute(
                "SELECT id FROM invitations WHERE workspace_id=? AND email_key=? "
                "AND accepted_at IS NULL AND revoked_at IS NULL", (workspace_id, key)).fetchall()]
            if superseded:
                t.execute(f"UPDATE invitations SET revoked_at=? WHERE id IN ({','.join('?' * len(superseded))})",
                          (now(), *superseded))
            t.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,"
                      "invited_by,invited_via,invited_approval_id,created_at,expires_at) VALUES (:id,:email_key,:workspace_id,:role,"
                      ":can_export,:token_hash,:invited_by,:invited_via,:invited_approval_id,:created_at,:expires_at)", row)
        # Named so the caller can journal each withdrawn link: a restore from before this moment
        # must not bring back the link a resend was meant to kill.
        row["superseded_ids"] = superseded
        return row, raw

    def list_for_workspace(self, workspace_id: str, actor_id: str, *, state: str | None = None) -> list[dict]:
        m = self.ident.membership(workspace_id, actor_id)
        if not m or m["role"] not in ("admin", "owner"):
            raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以查看邀请")
        if state and state not in STATES:
            raise ServiceError(400, "bad_state", f"未知的邀请状态 {state!r}", {"field": "state"})
        rows = self.store.all(
            "SELECT i.id,i.email_key,i.role,i.can_export,i.invited_by,i.invited_via,i.created_at,i.expires_at,"
            "i.accepted_at,i.revoked_at,i.declined_at,u.email AS invited_by_email, "
            "u.display_name AS inviter_name,c.name AS integration_name "
            "FROM invitations i LEFT JOIN users u ON u.id=i.invited_by "
            "LEFT JOIN oauth_clients c ON c.client_id=i.invited_via "
            "WHERE i.workspace_id=? ORDER BY i.created_at DESC", (workspace_id,))
        out = [{**r, "state": self._state(r)} for r in rows]
        viewer = self.ident.get_user(actor_id) or {}
        from .workspace_actor import format_actor_label
        for item in out:
            name = item.pop("inviter_name") or item["invited_by_email"] or item["invited_by"]
            app = item.pop("integration_name") or item["invited_via"]
            item["inviter"] = (format_actor_label(app, name, viewer.get("locale") or "zh-Hans")
                               if item.get("invited_via") else item["invited_by_email"])
        return [r for r in out if not state or r["state"] == state]

    def get(self, invite_id: str) -> dict | None:
        return self.store.one("SELECT * FROM invitations WHERE id=?", (invite_id,))

    def update(self, invite_id: str, actor_id: str, *, role: str | None = None,
               can_export: bool | None = None) -> dict:
        """Edit a pending invitation's role or export flag. The token is unchanged.

        Only while pending: an accepted invitation is a membership now (change that instead), and
        a revoked or expired one is not coming back by being edited. The role ceiling is checked
        against both the old and the new role, so an admin can neither raise an invitation above
        their own role nor rewrite an invitation for a role above it.
        """
        r = self.get(invite_id)
        if not r:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        self.may_invite(actor_id, r["workspace_id"], r["role"])
        new_role = role or r["role"]
        self.may_invite(actor_id, r["workspace_id"], new_role)
        if r.get("invited_via") and new_role in ("admin", "owner"):
            raise ServiceError(403, "role_needs_person",
                               "集成发出的邀请只能赋予普通成员角色；请撤回这份邀请，再以本人身份重新发送")
        if self._state(r) != "pending":
            raise ServiceError(409, "invite_not_pending", "只能修改尚未接受、未撤销、未过期的邀请")
        export = r["can_export"] if can_export is None else (1 if can_export else 0)
        if role and can_export is None and new_role == "viewer" and r["role"] != "viewer":
            export = 0                       # a downgrade does not keep the wider right
        n = self.store.execute("UPDATE invitations SET role=?, can_export=? WHERE id=? AND accepted_at IS NULL "
                               "AND revoked_at IS NULL AND expires_at>?", (new_role, export, invite_id, now()))
        if not n:
            raise ServiceError(409, "invite_not_pending", "只能修改尚未接受、未撤销、未过期的邀请")
        out = self.get(invite_id)
        return {**out, "state": self._state(out), "before": {"role": r["role"], "can_export": bool(r["can_export"])}}

    def resend(self, invite_id: str, actor_id: str, *, ttl_s: int = INVITE_TTL_S) -> tuple[dict, str]:
        """Issue a fresh token for the same address and role; the old link stops working.

        For a pending, expired or mail-failed invitation. An accepted one is a member already,
        and a revoked one was withdrawn on purpose — bringing it back is a new invitation.
        """
        r = self.get(invite_id)
        if not r:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        state = self._state(r)
        if state not in ("pending", "expired"):
            raise ServiceError(409, f"invite_{state}", "已接受、已拒绝或已撤销的邀请不能重发")
        return self.create(workspace_id=r["workspace_id"], email=r["email_key"], role=r["role"],
                           actor_id=actor_id, can_export=bool(r["can_export"]), ttl_s=ttl_s)

    @staticmethod
    def _state(r: dict) -> str:
        # `is not None`, as the SQL that writes and filters these columns says IS NULL: a timestamp
        # of 0 is a set timestamp (A47 — the oversight list computes the same state in SQL).
        if r.get("accepted_at") is not None:
            return "accepted"
        if r.get("declined_at") is not None:
            return "declined"
        if r.get("revoked_at") is not None:
            return "revoked"
        return "expired" if r["expires_at"] <= now() else "pending"

    def revoke(self, invite_id: str, actor_id: str) -> bool:
        """Withdraw an invitation. True when it is now revoked (by this call or an earlier one);
        False when it had already been accepted, which a revocation cannot undo."""
        r = self.store.one("SELECT * FROM invitations WHERE id=?", (invite_id,))
        if not r:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        self.may_invite(actor_id, r["workspace_id"], r["role"])
        return bool(self.store.execute("UPDATE invitations SET revoked_at=COALESCE(revoked_at, ?) "
                                       "WHERE id=? AND accepted_at IS NULL", (now(), invite_id)))

    def platform_revoke(self, invite_id: str) -> dict:
        """Withdraw an invitation under platform governance (A47): the UPDATE `revoke` makes,
        without `may_invite` — the platform administrator is no member of the workspace, and the
        authority is the caller's `platform.workspaces.manage` (admin_invitations) — for the
        caller's write transaction.

        An accepted invitation is a membership now and a declined one was answered: both are 409
        `invite_not_pending`, nothing written. An invitation already revoked (by its workspace, a
        resend that superseded it, or an earlier platform revoke) writes nothing and comes back
        with `already`. A pending or expired one is revoked: an expired link is dead already, but
        a revoked one can no longer be resent by its inviter. Returns
        `{"before": row, "state_before", "already", "revoked_at"}`."""
        r = self.get(invite_id)
        if not r:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        state = self._state(r)
        if state in ("accepted", "declined"):
            raise ServiceError(409, "invite_not_pending", "已接受或已拒绝的邀请不能撤销")
        if state == "revoked":
            return {"before": r, "state_before": state, "already": True, "revoked_at": r["revoked_at"]}
        ts = now()
        if not self.store.execute("UPDATE invitations SET revoked_at=? WHERE id=? AND accepted_at IS NULL "
                                  "AND declined_at IS NULL AND revoked_at IS NULL", (ts, invite_id)):
            # The row changed between the read and the write (unreachable under the writer lock the
            # caller holds): refused, never retried.
            raise ServiceError(409, "invite_not_pending", "邀请状态已变化，请刷新后重试")
        return {"before": r, "state_before": state, "already": False, "revoked_at": ts}

    def revoked_by_platform(self, workspace_id: str) -> set[str]:
        """The workspace's invitations the platform revoked (A47), for the workspace's own list.

        Read from the record the platform revoke writes in its own transaction — `invite.revoke`
        under the invitation's workspace with `by: platform` — rather than from a new column: the
        row exists exactly when that revoke committed, the audit table is append-only (nothing
        prunes it), `ix_audit_ws` serves the read, and no schema change is needed. A copy restored
        from before the revoke has the revocation back from the security journal but not this
        record, so there the invitation shows as revoked without saying by whom."""
        return {row["object_id"] for row in self.store.all(
            "SELECT DISTINCT object_id FROM audit_events WHERE workspace_id=? AND action='invite.revoke' "
            "AND object_kind='invitation' AND result='ok' AND json_extract(meta, '$.by')='platform'",
            (workspace_id,))}

    def preview(self, raw_token: str) -> dict:
        """What the landing page shows before anyone commits to anything.

        Reads only; the token is not consumed here, which is what makes the flow safe against a
        scanner opening the link. Deliberately does not reveal whether an account already exists
        for the invited address — that would make invitation links an account-existence oracle.
        """
        r = self.store.one("SELECT * FROM invitations WHERE token_hash=?", (_digest(raw_token),))
        if not r:
            raise ServiceError(404, "bad_invite", "邀请链接无效")
        state = self._state(r)
        ws = self.ident.get_workspace(r["workspace_id"])
        inviter = self.ident.get_user(r["invited_by"])
        name = (inviter or {}).get("display_name") or (inviter or {}).get("email")
        if r.get("invited_via"):
            from .workspace_actor import actor_label
            name = actor_label(self.store, r["invited_via"], r["invited_by"], r.get("locale") or "zh-Hans")
        return {"state": state, "email": r["email_key"], "role": r["role"],
                "role_label": ROLE_LABEL.get(r["role"], r["role"]),
                "workspace": (ws or {}).get("name"),
                "workspace_status": (ws or {}).get("status"),
                "inviter": name,
                "expires_at": r["expires_at"],
                # Whether the visitor needs to choose a password, decided without saying whether
                # the address is registered: the client is told only what it must render.
                "needs_account": not self.ident.by_email(r["email_key"])}

    def decline(self, raw_token: str, user_id: str) -> dict:
        with self.store.tx():
            row = self.store.one("SELECT * FROM invitations WHERE token_hash=?", (_digest(raw_token),))
            user = self.ident.get_user(user_id)
            if not row or not user or user["status"] != "active" or user["email_key"] != row["email_key"]:
                raise ServiceError(404, "bad_invite", "找不到与你的账户相符的邀请")
            if row.get("declined_at"):
                return {"id": row["id"], "workspace_id": row["workspace_id"], "state": "declined", "invited_by": row["invited_by"]}
            if self._state(row) != "pending":
                raise ServiceError(409, "invite_not_pending", "邀请已经处理或失效")
            # Written ahead, in this transaction (journal._intent): a fresh attempt_id and a receipt
            # that commits with the decline, so a backup copied between the line and its COMMIT is
            # given the decline on restore. Strict: a line that cannot be written refuses the
            # decline (503). A later failure (the caller's audit, the COMMIT) rolls back and voids it.
            try:
                journal._intent(self.store, "invitation_revoked", invitation_id=row["id"])
            except (OSError, ValueError):
                raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
            self.store.execute("UPDATE invitations SET revoked_at=?,declined_at=? WHERE id=?",
                               (now(), now(), row["id"]))
            return {"id": row["id"], "workspace_id": row["workspace_id"], "state": "declined", "invited_by": row["invited_by"]}

    def accept(self, raw_token: str, **kwargs) -> dict:
        # Includes new-account creation and every authority check: a lost accept/revoke race
        # cannot leave an orphaned account, membership or default workspace.
        with self.store.tx():
            return self._accept(raw_token, **kwargs)

    def _accept(self, raw_token: str, *, password: str | None, display_name: str | None,
               as_user_id: str | None = None) -> dict:
        """Consume an invitation. Returns {"user": row, "workspace_id": ...}.

        `as_user_id` is the already-signed-in account, if any. Accepting while signed in as a
        *different* person is refused rather than silently binding the invitation to whoever
        happened to be logged in — that is how someone ends up joining a workspace with an
        account their colleague left open in the browser.
        """
        digest = _digest(raw_token)
        r = self.store.one("SELECT * FROM invitations WHERE token_hash=?", (digest,))
        if not r:
            raise ServiceError(404, "bad_invite", "邀请链接无效")
        state = self._state(r)
        if state != "pending":
            raise ServiceError(409, f"invite_{state}", {
                "accepted": "这个邀请已经被使用过了",
                "revoked": "这个邀请已被撤销",
                "expired": "这个邀请已过期，请让邀请人重新发送",
                "declined": "这个邀请已经被拒绝，请联系邀请人重新发送",
            }[state])

        ws = self.ident.get_workspace(r["workspace_id"])
        if not ws or ws["status"] != "active":
            raise ServiceError(409, "workspace_unavailable", "该工作空间已不可用")

        if r.get("invited_via"):
            from .workspace_actor import require_role, service_approval
            try:
                require_role(r["role"])
                approval = service_approval(self.store, r["invited_via"], r["workspace_id"],
                                            approval_id=r.get("invited_approval_id"))
                if not r.get("invited_approval_id") or approval["approved_by"] != r["invited_by"]:
                    raise ServiceError(403, "approval_inactive", "邀请的原审批已失效")
            except ServiceError:
                raise ServiceError(409, "inviter_lost_permission", "发出邀请的集成已不再具备权限，请联系空间管理员重新邀请") from None

        # The inviter's authority is re-checked at acceptance, not only at issue. An invitation
        # written by someone who has since been removed or demoted must not still let them add
        # members days later.
        inviter_m = self.ident.membership(r["workspace_id"], r["invited_by"])
        inviter_u = self.ident.get_user(r["invited_by"])
        if (not inviter_m or inviter_m["role"] not in ("admin", "owner")
                or not inviter_u or inviter_u["status"] != "active"
                or ROLE_RANK[r["role"]] > ROLE_RANK[inviter_m["role"]]):
            raise ServiceError(409, "inviter_lost_permission",
                               "邀请人已不再具备邀请权限，这个邀请失效了。请联系空间管理员重新邀请。")

        existing = self.ident.by_email(r["email_key"])
        if as_user_id:
            signed_in = self.ident.get_user(as_user_id)
            if not signed_in or normalise_email(signed_in["email"]) != r["email_key"]:
                raise ServiceError(409, "wrong_account",
                                   f"这个邀请是发给 {r['email_key']} 的，而你当前登录的是"
                                   f"{(signed_in or {}).get('email', '另一个账户')}。请先退出再接受邀请。")
            if signed_in["status"] not in ("active", "pending"):
                raise ServiceError(403, "account_unavailable", "账户不可用")
            # Explicit token consumption proves this exact mailbox, not an arbitrary session.
            self.store.execute("UPDATE users SET email_verified_at=COALESCE(email_verified_at,?),status='active' WHERE id=?", (now(), signed_in["id"]))
            user = self.ident.get_user(signed_in["id"])
        elif existing:
            # The address already has an account. Joining it must prove control of that account,
            # so this path requires signing in first rather than accepting a password here —
            # otherwise the invitation would be a way to set a stranger's password.
            raise ServiceError(409, "sign_in_required", "该邮箱已有账户，请先登录后再打开邀请链接")
        else:
            if not password:
                raise ServiceError(400, "password_required", "请设置口令", {"field": "password"})
            user = self.ident.create_user(r["email_key"], password, display_name=display_name,
                                          status="active", with_personal_workspace=False)
            # Arriving through an invitation proves control of the mailbox, so the address is
            # verified; asking for a second confirmation mail here proves nothing extra.
            self.store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))

        # Consume and join in one transaction. What actually serialises concurrent accepts is
        # Store.tx()'s BEGIN IMMEDIATE plus its process-level write lock — verified by swapping
        # this for a naive check-then-act, which still produced exactly one membership. The
        # conditional UPDATE is kept anyway because it makes the claim self-checking: it stays
        # correct if this ever runs outside that transaction discipline, and it is the same
        # pattern the job runner uses to claim work.
        with self.store.tx() as t:
            from synwevo_core.organizations import accept_workspace_invitation
            accept_workspace_invitation(self.store, r["workspace_id"], user["id"], r["invited_by"])
            claimed = t.execute(
                "UPDATE invitations SET accepted_at=?, accepted_by=? "
                "WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?",
                (now(), user["id"], digest, now())).rowcount
            if not claimed:
                raise ServiceError(409, "invite_accepted", "这个邀请已经被使用过了")
            ts = now()
            t.execute("UPDATE users SET onboarding_completed_at=COALESCE(onboarding_completed_at,?),updated_at=? WHERE id=?", (ts, ts, user["id"]))
            joined = t.execute(
                "INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id,user_id) DO NOTHING",
                (r["workspace_id"], user["id"], r["role"], r["can_export"], ts, ts, r["invited_by"])).rowcount
            if joined:
                # A new membership starts fresh. Removing someone writes explicit revoked
                # entitlement rows (so the removal holds under the `members` policy); left in
                # place, a former member invited back would be a member who silently cannot
                # open the application. Restoring their old grants is not implied either — the
                # workspace's defaults apply, as for anyone else joining.
                t.execute("DELETE FROM app_entitlements WHERE workspace_id=? AND user_id=? "
                          "AND revoked_at IS NOT NULL", (r["workspace_id"], user["id"]))
        # A bulk import may have named organization roles for this person. They are applied now,
        # as the importer and under the rules that hold now (not when the file was uploaded): if
        # the importer may no longer assign them, nothing is assigned and the import row says so.
        import_roles = None
        try:
            from synwevo_core.member_import import MemberImports
            import_roles = MemberImports(self.store, self.ident).on_invitation_accepted(r["id"], user["id"])
        except Exception:  # noqa: BLE001 - roles are an extra on top of the membership, never a reason to fail the accept
            import_roles = None
        return {"user": self.ident.get_user(user["id"]), "workspace_id": r["workspace_id"], "invitation_id": r["id"], "invited_by": r["invited_by"],
                "role": r["role"], "import_roles": import_roles}

    # ================================================================ email tokens
    def issue_email_token(self, user_id: str, kind: str, *, ttl_s: int,
                          new_email: str | None = None) -> str:
        if kind not in ("verify", "reset", "email_change"):
            raise ServiceError(400, "bad_kind", f"未知的邮件令牌类型 {kind!r}")
        raw = _token()
        with self.store.tx() as t:
            # Issuing a new one invalidates any outstanding token of the same kind, so an old
            # reset link in an inbox stops working the moment a new one is requested.
            t.execute("UPDATE email_tokens SET used_at=? WHERE user_id=? AND kind=? AND used_at IS NULL",
                      (now(), user_id, kind))
            t.execute("INSERT INTO email_tokens (id,user_id,kind,token_hash,new_email,new_email_key,"
                      "created_at,expires_at) VALUES (?,?,?,?,?,?,?,?)",
                      (new_id("et"), user_id, kind, _digest(raw), new_email,
                       normalise_email(new_email) if new_email else None, now(), now() + ttl_s))
        return raw

    def peek_email_token(self, raw: str, kind: str) -> dict | None:
        """Look without consuming — for the landing page. See the module docstring."""
        r = self.store.one("SELECT * FROM email_tokens WHERE token_hash=? AND kind=?", (_digest(raw), kind))
        if not r or r.get("used_at") or r["expires_at"] <= now():
            return None
        return r

    def consume_email_token(self, raw: str, kind: str) -> dict:
        """Atomically spend the token and return its row."""
        digest = _digest(raw)
        with self.store.tx() as t:
            n = t.execute("UPDATE email_tokens SET used_at=? WHERE token_hash=? AND kind=? "
                          "AND used_at IS NULL AND expires_at>?", (now(), digest, kind, now())).rowcount
            if not n:
                raise ServiceError(409, "bad_token", "链接无效或已过期，请重新申请")
            return t.execute("SELECT * FROM email_tokens WHERE token_hash=?", (digest,)).fetchone()
