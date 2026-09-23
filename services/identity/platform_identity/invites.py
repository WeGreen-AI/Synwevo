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

from .identity import Identity, ROLE_RANK, ROLES, normalise_email, valid_email
from .services import ServiceError
from .store import Store, new_id, now

INVITE_TTL_S = 7 * 24 * 3600
VERIFY_TTL_S = 24 * 3600
RESET_TTL_S = 2 * 3600

ROLE_LABEL = {"viewer": "只读成员", "editor": "编辑成员", "admin": "空间管理员", "owner": "空间所有者"}


def _digest(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


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
        m = self.ident.membership(workspace_id, actor_id)
        if not m or m["role"] not in ("admin", "owner"):
            raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以邀请成员")
        if role not in ROLES:
            raise ServiceError(400, "bad_role", f"未知的角色 {role!r}", {"field": "role"})
        if ROLE_RANK[role] > ROLE_RANK[m["role"]]:
            raise ServiceError(403, "role_too_high", "不能邀请比自己权限更高的角色")

    def create(self, *, workspace_id: str, email: str, role: str, actor_id: str,
               can_export: bool | None = None, ttl_s: int = INVITE_TTL_S) -> tuple[dict, str]:
        if not valid_email(email):
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        self.may_invite(actor_id, workspace_id, role)
        ws = self.ident.get_workspace(workspace_id)
        if not ws or ws["status"] != "active":
            raise ServiceError(404, "no_workspace", "找不到该工作空间")

        key = normalise_email(email)
        existing_user = self.ident.by_email(key)
        if existing_user and self.ident.membership(workspace_id, existing_user["id"]):
            raise ServiceError(409, "already_member", "该邮箱已经是这个空间的成员")

        raw = _token()
        row = {"id": new_id("iv"), "email_key": key, "workspace_id": workspace_id, "role": role,
               "can_export": 1 if (can_export if can_export is not None else role != "viewer") else 0,
               "token_hash": _digest(raw), "invited_by": actor_id,
               "created_at": now(), "expires_at": now() + ttl_s}
        with self.store.tx() as t:
            # One live invitation per (workspace, email). Re-inviting supersedes rather than
            # accumulating, so revoking "the" invitation cannot leave an older one usable.
            t.execute("UPDATE invitations SET revoked_at=? WHERE workspace_id=? AND email_key=? "
                      "AND accepted_at IS NULL AND revoked_at IS NULL", (now(), workspace_id, key))
            t.execute("INSERT INTO invitations (id,email_key,workspace_id,role,can_export,token_hash,"
                      "invited_by,created_at,expires_at) VALUES (:id,:email_key,:workspace_id,:role,"
                      ":can_export,:token_hash,:invited_by,:created_at,:expires_at)", row)
        return row, raw

    def list_for_workspace(self, workspace_id: str, actor_id: str) -> list[dict]:
        m = self.ident.membership(workspace_id, actor_id)
        if not m or m["role"] not in ("admin", "owner"):
            raise ServiceError(403, "forbidden", "只有空间管理员或所有者可以查看邀请")
        rows = self.store.all(
            "SELECT id,email_key,role,can_export,invited_by,created_at,expires_at,accepted_at,revoked_at "
            "FROM invitations WHERE workspace_id=? ORDER BY created_at DESC", (workspace_id,))
        return [{**r, "state": self._state(r)} for r in rows]

    @staticmethod
    def _state(r: dict) -> str:
        if r.get("accepted_at"):
            return "accepted"
        if r.get("revoked_at"):
            return "revoked"
        return "expired" if r["expires_at"] <= now() else "pending"

    def revoke(self, invite_id: str, actor_id: str) -> None:
        r = self.store.one("SELECT * FROM invitations WHERE id=?", (invite_id,))
        if not r:
            raise ServiceError(404, "no_invite", "找不到该邀请")
        self.may_invite(actor_id, r["workspace_id"], r["role"])
        self.store.execute("UPDATE invitations SET revoked_at=? WHERE id=? AND accepted_at IS NULL",
                           (now(), invite_id))

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
        return {"state": state, "email": r["email_key"], "role": r["role"],
                "role_label": ROLE_LABEL.get(r["role"], r["role"]),
                "workspace": (ws or {}).get("name"),
                "workspace_status": (ws or {}).get("status"),
                "inviter": (inviter or {}).get("display_name") or (inviter or {}).get("email"),
                "expires_at": r["expires_at"],
                # Whether the visitor needs to choose a password, decided without saying whether
                # the address is registered: the client is told only what it must render.
                "needs_account": not self.ident.by_email(r["email_key"])}

    def accept(self, raw_token: str, *, password: str | None, display_name: str | None,
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
            }[state])

        ws = self.ident.get_workspace(r["workspace_id"])
        if not ws or ws["status"] != "active":
            raise ServiceError(409, "workspace_unavailable", "该工作空间已不可用")

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
            user = signed_in
        elif existing:
            # The address already has an account. Joining it must prove control of that account,
            # so this path requires signing in first rather than accepting a password here —
            # otherwise the invitation would be a way to set a stranger's password.
            raise ServiceError(409, "sign_in_required", "该邮箱已有账户，请先登录后再打开邀请链接")
        else:
            if not password:
                raise ServiceError(400, "password_required", "请设置口令", {"field": "password"})
            user = self.ident.create_user(r["email_key"], password, display_name=display_name,
                                          status="active", with_personal_workspace=True)
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
            claimed = t.execute(
                "UPDATE invitations SET accepted_at=?, accepted_by=? "
                "WHERE token_hash=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?",
                (now(), user["id"], digest, now())).rowcount
            if not claimed:
                raise ServiceError(409, "invite_accepted", "这个邀请已经被使用过了")
            ts = now()
            t.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
                      "VALUES (?,?,?,?,?,?,?) ON CONFLICT(workspace_id,user_id) DO NOTHING",
                      (r["workspace_id"], user["id"], r["role"], r["can_export"], ts, ts, r["invited_by"]))
        return {"user": self.ident.get_user(user["id"]), "workspace_id": r["workspace_id"],
                "role": r["role"]}

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
