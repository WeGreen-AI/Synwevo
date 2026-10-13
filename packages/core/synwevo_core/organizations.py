"""Organization governance over existing workspace tenant IDs.

Only Store executes SCHEMA_SQL and V6_COLUMNS. No constructor performs DDL,
backfills historical workspaces, grants document access, or creates credentials.
The identity service owns HTTP ceremonies; Apps/authority consume the live checks
below. An organization membership is necessary for an attached workspace, but is
never sufficient: the existing workspace membership and app checks still apply.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone

from .audit_change import change
from .errors import ServiceError
from .journal import record_organization
from .store import Store, new_id, now

ROLES = ("member", "admin", "owner")
RECOVERY_SECONDS = 7 * 24 * 3600
#: The lifecycle acts, in the owner's tenant route and the platform's console path alike.
LIFECYCLE_ACTIONS = ("freeze", "unfreeze", "delete", "recover")
#: The organization as platform governance sees it (A35): lifecycle columns only — no role,
#: usage, policies or avatar, which are the members' view (`Organizations.get`).
PLATFORM_VIEW_COLUMNS = ("id", "name", "status", "deleted_at", "recover_until", "previous_status", "version")
#: A member whose account can act (`current_membership`'s rule): active and undeleted. Written
#: against the alias `u` (users).
USABLE_ACCOUNT_CONDITION = "u.status='active' AND u.deleted_at IS NULL"
#: An organization owner who can act (`_keep_owner`'s rule, shared with the platform's orphan list
#: and `recover_owner`): the membership's role is owner and the account is active and undeleted.
#: Written against the aliases `m` (organization_memberships) and `u` (users).
USABLE_OWNER_CONDITION = "m.role='owner' AND " + USABLE_ACCOUNT_CONDITION

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS organizations (
 id TEXT PRIMARY KEY, name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','frozen','deleted')),
 invitation_policy TEXT NOT NULL DEFAULT 'admins' CHECK(invitation_policy IN ('admins','owners')),
 created_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 deleted_at REAL, recover_until REAL, previous_status TEXT,
 version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS organization_memberships (
 organization_id TEXT NOT NULL REFERENCES organizations(id), user_id TEXT NOT NULL REFERENCES users(id),
 role TEXT NOT NULL CHECK(role IN ('owner','admin','member')),
 created_at REAL NOT NULL, updated_at REAL NOT NULL, created_by TEXT,
 PRIMARY KEY(organization_id,user_id)
);
CREATE INDEX IF NOT EXISTS ix_org_members_user ON organization_memberships(user_id,organization_id);
CREATE TABLE IF NOT EXISTS organization_avatars (
 organization_id TEXT PRIMARY KEY REFERENCES organizations(id), image BLOB NOT NULL,
 updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS organization_idempotency (
 actor_id TEXT NOT NULL, key_hash TEXT NOT NULL, request_hash TEXT NOT NULL,
 organization_id TEXT NOT NULL REFERENCES organizations(id), response_body TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(actor_id,key_hash)
);
"""

# Import this into Store's additive V6 column loop; do not rewrite tenant values.
V6_COLUMNS = [("workspaces", "organization_id", "TEXT REFERENCES organizations(id)")]


def deleted_workspace_owner(ws: dict, role: str = "owner") -> ServiceError:
    """The refusal of a departure — leaving or being removed from the organization, deleting or
    suspending the account — that would leave a workspace the platform deleted, and can still
    recover, without its only usable owner (or, for a departing admin, without anybody to manage
    it): its memberships come back with a recovery (A44). Unlike a live workspace it cannot be
    handed over now — it is hidden until it is recovered, and the platform's ownership recovery
    does not reach a deleted workspace — so the refusal says what can be done: a platform recovery
    and then a handover, or waiting until the recovery window (`recover_until`) has ended, after
    which the departure is allowed (A44-L1). `ws` carries `id`, `name` and `recover_until`; the
    name and the time are in `detail` too, so every shell words it in its reader's language."""
    until = ws["recover_until"]
    when = datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if role == "owner":
        message = (f"此账户是已删除、仍在恢复期内的工作空间「{ws['name']}」唯一可用的拥有者；恢复期限（{when}）结束前不能执行此操作。"
                   "可请平台管理员恢复该工作空间后移交所有权，或等待恢复期限结束。")
    else:
        message = (f"此账户是已删除、仍在恢复期内的工作空间「{ws['name']}」唯一可用的管理者；恢复期限（{when}）结束前不能执行此操作。"
                   "可请平台管理员恢复该工作空间并另设拥有者或管理者，或等待恢复期限结束。")
    return ServiceError(409, "owner_of_deleted_workspace", message,
                        {"workspace_id": ws["id"], "name": ws["name"], "role": "owner" if role == "owner" else "admin",
                         "recover_until": until})


def organization_for_workspace(store: Store, workspace_id: str) -> dict | None:
    return store.one("SELECT o.* FROM organizations o JOIN workspaces w ON w.organization_id=o.id "
                     "WHERE w.id=?", (workspace_id,))


def current_membership(store: Store, organization_id: str, user_id: str) -> dict | None:
    """A current membership of an active account, including frozen/deleted orgs.

    Keeping lifecycle state separate permits authorized recovery without granting
    business access during the recovery window.
    """
    return store.one("SELECT m.* FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                     "WHERE m.organization_id=? AND m.user_id=? AND u.status='active' "
                     "AND u.deleted_at IS NULL", (organization_id, user_id))


def workspace_access_failure(store: Store, workspace_id: str, user_id: str | None = None) -> str | None:
    """Additive gate; None does not grant any workspace or application permission.

    Machine principals pass no user ID: workspace/org lifecycle still applies.
    Personal and historical unassociated workspaces retain their old contract.
    """
    ws = store.one("SELECT organization_id FROM workspaces WHERE id=?", (workspace_id,))
    if not ws or not ws["organization_id"]:
        return None
    org = organization_for_workspace(store, workspace_id)
    if not org or org["status"] != "active":
        return "organization_unavailable"
    if user_id is not None and not current_membership(store, org["id"], user_id):
        return "not_an_organization_member"
    return None


def require_invitation_permission(store: Store, workspace_id: str, actor_id: str) -> None:
    """Called by the existing invitation service, in its write transaction.

    Workspace invitation role ceilings remain that service's responsibility.
    """
    org = organization_for_workspace(store, workspace_id)
    if not org:
        return
    if org["status"] != "active":
        raise ServiceError(409, "organization_unavailable", "该组织当前不可用")
    member = current_membership(store, org["id"], actor_id)
    if not member:
        raise ServiceError(403, "organization_invitation_forbidden", "当前组织邀请策略不允许此操作")
    if org["invitation_policy"] == "owners":
        ok = member["role"] == "owner"
    else:
        from .org_roles import OrgRoles
        ok = OrgRoles(store).has(org["id"], actor_id, "org.members.invite")
    if not ok:
        raise ServiceError(403, "organization_invitation_forbidden", "当前组织邀请策略不允许此操作")


def accept_workspace_invitation(store: Store, workspace_id: str, user_id: str, actor_id: str) -> dict | None:
    """Parent calls inside the existing invitation-consumption transaction.

    Joins only the explicitly invited workspace's organization, always as member;
    a workspace owner invitation cannot promote an organization owner.
    """
    with store.tx() as t:
        require_invitation_permission(store, workspace_id, actor_id)
        org = organization_for_workspace(store, workspace_id)
        if not org:
            return None
        user = store.one("SELECT status,deleted_at FROM users WHERE id=?", (user_id,))
        if not user or user["status"] != "active" or user["deleted_at"] is not None:
            raise ServiceError(409, "account_unavailable", "该账户当前不可用")
        ts = now()
        t.execute("INSERT INTO organization_memberships "
                  "(organization_id,user_id,role,created_at,updated_at,created_by) "
                  "VALUES (?,?,'member',?,?,?) ON CONFLICT(organization_id,user_id) DO NOTHING",
                  (org["id"], user_id, ts, ts, actor_id))
        return current_membership(store, org["id"], user_id)


def _name(value, field: str = "name") -> str:
    if not isinstance(value, str):
        raise ServiceError(400, "invalid_field", "请填写有效名称", {"field": field})
    value = value.strip()
    if not 1 <= len(value) <= 100 or any(unicodedata.category(c).startswith("C") for c in value):
        raise ServiceError(400, "invalid_field", "名称须为 1 至 100 个字符且不能包含控制字符", {"field": field})
    return value


def audit_reason(value) -> str:
    """The reason an organization change is made, written to its audit record: 8 to 500 characters."""
    if not isinstance(value, str) or not 8 <= len(value.strip()) <= 500:
        raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字），它会写入审计记录",
                           {"field": "reason"})
    return value.strip()


def platform_view(store: Store, oid: str) -> dict | None:
    """The PLATFORM_VIEW_COLUMNS of one organization, or None when there is no such row."""
    return store.one(f"SELECT {','.join(PLATFORM_VIEW_COLUMNS)} FROM organizations WHERE id=?", (oid,))


def member_page(store: Store, oid: str, *, q="", role="", offset=0, limit=50, match_email: bool = True,
                usable: bool = False) -> dict:
    """One page of an organization's members as `Organizations.members` lists them — owners, then
    admins, then members, each by joining time — with no authority check: the caller decides who
    may read it (the tenant's `org.members.view`, the platform's `platform.orgs.view`, A34).

    `q` (≤100) matches the address key or the display name, `role` one base role; 400 `bad_role`,
    400 `invalid_field`. `match_email=False` matches display names only: a reader who is not shown
    addresses must not find one by probing. `usable=True` adds whether each account can act
    (USABLE_ACCOUNT_CONDITION). Rows: `{user_id, role, joined_at, email, display_name,
    account_status, custom_roles: [{id, name}]}`; the page: `{items, total, offset, limit,
    active_owner_count}`. Joins an enclosing transaction or read snapshot."""
    if role and role not in ROLES:
        raise ServiceError(400, "bad_role", "请选择有效的组织角色", {"field": "role"})
    if not isinstance(q, str) or len(q) > 100 or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
        raise ServiceError(400, "invalid_field", "搜索或分页参数无效")
    where, args = "m.organization_id=?", [oid]
    if q.strip():
        escaped = q.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if match_email:
            # A deleted account keeps its address key reserved but erased: never matched by it.
            where += " AND ((u.deleted_at IS NULL AND u.email_key LIKE ? ESCAPE '\\') OR lower(u.display_name) LIKE ? ESCAPE '\\')"
            args += [f"%{escaped}%"] * 2
        else:
            where += " AND lower(u.display_name) LIKE ? ESCAPE '\\'"
            args.append(f"%{escaped}%")
    if role:
        where += " AND m.role=?"
        args.append(role)
    base = " FROM organization_memberships m JOIN users u ON u.id=m.user_id WHERE " + where
    extra = f", ({USABLE_ACCOUNT_CONDITION}) AS usable" if usable else ""
    rows = store.all("SELECT m.user_id,m.role,m.created_at AS joined_at,u.email,u.display_name, "
                     "u.status AS account_status" + extra + base + " ORDER BY CASE m.role WHEN 'owner' THEN 0 "
                     "WHEN 'admin' THEN 1 ELSE 2 END,m.created_at,m.user_id LIMIT ? OFFSET ?", tuple(args + [limit, offset]))
    names = {}
    if rows:
        marks = ",".join("?" for _ in rows)
        for a in store.all("SELECT a.user_id, r.id, r.name FROM organization_role_assignments a JOIN organization_roles r ON r.id=a.role_id "
                           f"WHERE a.organization_id=? AND a.user_id IN ({marks}) ORDER BY r.name", (oid, *[r["user_id"] for r in rows])):
            names.setdefault(a["user_id"], []).append({"id": a["id"], "name": a["name"]})
    for row in rows:
        if usable:
            row["usable"] = bool(row["usable"])
        row["custom_roles"] = names.get(row["user_id"], [])
    owners = store.one("SELECT COUNT(*) AS n FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                       f"WHERE m.organization_id=? AND {USABLE_OWNER_CONDITION}", (oid,))["n"]
    return {"items": rows, "total": store.one("SELECT COUNT(*) AS n" + base, tuple(args))["n"],
            "offset": offset, "limit": limit, "active_owner_count": owners}


def lifecycle_values(org: dict, action: str, ts: float) -> dict:
    """The column values `action` writes from the organization's current row — or the refusal:
    409 `organization_recovery_expired` for a recovery past `recover_until`, 409
    `organization_state_conflict` for every other act the status does not allow (an unknown
    action included). The one state machine behind `Organizations.transition` and
    `Organizations.platform_transition`; the platform's preview asks it the same question
    without writing anything."""
    if action == "freeze" and org["status"] == "active":
        return {"status": "frozen"}
    if action == "unfreeze" and org["status"] == "frozen":
        return {"status": "active"}
    if action == "delete" and org["status"] in ("active", "frozen"):
        return {"status": "deleted", "deleted_at": ts, "recover_until": ts + RECOVERY_SECONDS,
                "previous_status": org["status"]}
    if action == "recover" and org["status"] == "deleted":
        if not org["recover_until"] or ts >= org["recover_until"]:
            raise ServiceError(409, "organization_recovery_expired", "该组织的七天恢复期限已过")
        return {"status": org["previous_status"] or "frozen", "deleted_at": None,
                "recover_until": None, "previous_status": None}
    raise ServiceError(409, "organization_state_conflict", "组织当前状态不允许此操作")


class Organizations:
    def __init__(self, store: Store, ident=None, audit=None):
        from .identity import Identity
        self.store = store
        self.ident = ident or Identity(store)
        self.audit = audit

    def _record(self, actor_id, oid, action, meta):
        # Always called inside the mutation transaction. Audit failure rolls it back.
        if self.audit:
            self.audit(action, actor_id=actor_id, object_kind="organization", object_id=oid,
                       result="ok", meta=meta)
        else:
            self.store.insert("audit_events", {"id": new_id("au"), "at": now(), "actor_kind": "user",
                              "actor_id": actor_id, "action": action, "object_kind": "organization",
                              "object_id": oid, "result": "ok", "meta": meta})

    def _user(self, uid):
        user = self.ident.get_user(uid)
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        return user

    def require(self, oid, actor_id, *, manage=False, owner=False, active=False, permission=None):
        """Membership plus authority. `permission` names a catalogue id (org_roles) and is the
        rule going forward: the base role and any custom roles decide. `owner` stays for the
        owner-only acts; `manage` is the legacy shorthand for the administrative set."""
        self._user(actor_id)
        org = self.store.one("SELECT * FROM organizations WHERE id=?", (oid,))
        member = current_membership(self.store, oid, actor_id)
        if not org or not member:
            raise ServiceError(404, "no_organization", "找不到该组织")
        if owner and member["role"] != "owner":
            raise ServiceError(403, "forbidden", "当前组织角色无权执行此操作")
        if permission is not None:
            from .org_roles import OrgRoles
            if not OrgRoles(self.store).has(oid, actor_id, permission):
                raise ServiceError(403, "forbidden", "当前组织角色无权执行此操作", {"permission": permission})
        elif manage and member["role"] == "member":
            raise ServiceError(403, "forbidden", "当前组织角色无权执行此操作")
        if active and org["status"] != "active":
            raise ServiceError(409, "organization_unavailable", "该组织当前不可用")
        return org, member

    def list_for_user(self, actor_id):
        self._user(actor_id)
        return self.store.all("SELECT o.*,m.role FROM organizations o JOIN organization_memberships m "
                              "ON m.organization_id=o.id WHERE m.user_id=? ORDER BY o.created_at,o.id", (actor_id,))

    def get(self, oid, actor_id):
        from .data_flow import organization_outbound
        org, member = self.require(oid, actor_id)
        # Governance counts only. Never inspect documents or invent a storage quota.
        return {**org, "role": member["role"], "transfer_policy": organization_outbound(self.store, oid)["outbound"],
                "has_avatar": bool(self.store.one(
            "SELECT 1 FROM organization_avatars WHERE organization_id=?", (oid,))), "usage": {
            "workspaces": self.store.one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=? "
                                         "AND deleted_at IS NULL", (oid,))["n"],
            "members": self.store.one("SELECT COUNT(*) AS n FROM organization_memberships "
                                      "WHERE organization_id=?", (oid,))["n"]}, "quota": None}

    def create(self, actor_id, name, workspace_name, idempotency_key):
        name, workspace_name = _name(name), _name(workspace_name, "workspace_name")
        if (not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200
                or any(ord(c) < 33 or ord(c) > 126 for c in idempotency_key)):
            raise ServiceError(400, "idempotency_key_required", "请提供有效的幂等请求标识",
                               {"field": "Idempotency-Key"})
        kh = hashlib.sha256(idempotency_key.encode()).hexdigest()
        rh = hashlib.sha256(json.dumps([name, workspace_name], ensure_ascii=False).encode()).hexdigest()
        with self.store.tx():
            self._user(actor_id)
            replay = self.store.one("SELECT * FROM organization_idempotency WHERE actor_id=? AND key_hash=?",
                                    (actor_id, kh))
            if replay:
                if replay["request_hash"] != rh:
                    raise ServiceError(409, "idempotency_conflict", "该请求标识已用于不同的创建内容")
                # Replaying creation must not disclose a now-inaccessible organization.
                self.require(replay["organization_id"], actor_id)
                return json.loads(replay["response_body"])
            self._creation_budget(actor_id, "organization", 10)
            oid, ts = new_id("org"), now()
            self.store.insert("organizations", {"id": oid, "name": name, "created_by": actor_id,
                              "created_at": ts, "updated_at": ts})
            self.store.insert("organization_memberships", {"organization_id": oid, "user_id": actor_id,
                              "role": "owner", "created_by": actor_id, "created_at": ts, "updated_at": ts})
            ws = self._new_workspace(oid, actor_id, workspace_name)
            result = {"organization": self.get(oid, actor_id), "workspace": ws}
            self.store.insert("organization_idempotency", {"actor_id": actor_id, "key_hash": kh,
                              "request_hash": rh, "organization_id": oid,
                              "response_body": json.dumps(result, ensure_ascii=False), "created_at": ts})
            self._record(actor_id, oid, "organization.create", {"workspace_id": ws["id"]})
            return result

    def _new_workspace(self, oid, actor_id, name):
        # Explicitly creating a workspace makes its creator its owner. It grants
        # no membership or entitlement to other organization owners/admins.
        ws = self.ident.create_workspace(name, owner_id=actor_id, kind="org")
        self.store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (oid, ws["id"]))
        return {**ws, "organization_id": oid}

    def update(self, oid, actor_id, *, name=None, invitation_policy=None, transfer_policy=None, reason, version=None):
        """`transfer_policy` caps where the organization's workspace content may be carried
        (synwevo_core.data_flow): "organization" keeps it inside, "any" leaves it to each
        workspace's own policy. Like the invitation policy, only an owner changes it."""
        from .data_flow import ORGANIZATION_OUTBOUND, organization_outbound, set_organization_outbound
        reason = audit_reason(reason)
        if name is None and invitation_policy is None and transfer_policy is None:
            raise ServiceError(400, "invalid_field", "请提供要修改的组织设置")
        if name is not None:
            name = _name(name)
        if invitation_policy is not None and invitation_policy not in ("admins", "owners"):
            raise ServiceError(400, "invalid_field", "请选择有效的邀请策略", {"field": "invitation_policy"})
        if transfer_policy is not None and transfer_policy not in ORGANIZATION_OUTBOUND:
            raise ServiceError(400, "invalid_field", "请选择有效的数据外发上限", {"field": "transfer_policy"})
        with self.store.tx():
            org, _ = self.require(oid, actor_id, permission="org.settings.manage",
                                  owner=invitation_policy is not None or transfer_policy is not None, active=True)
            if transfer_policy is not None and version is None:
                # The cap decides where the organization's content may go: it is only changed
                # against the settings the owner was looking at, never on top of a newer change.
                raise ServiceError(400, "invalid_field", "更改内容外发上限需要提供当前的组织设置版本", {"field": "version"})
            if version is not None and (type(version) is not int or version != org["version"]):
                raise ServiceError(409, "organization_changed", "组织设置已更新，请刷新后重试")
            transfer_before = organization_outbound(self.store, oid)["outbound"]
            after = {"name": name if name is not None else org["name"],
                     "invitation_policy": invitation_policy or org["invitation_policy"],
                     "updated_at": now(), "version": org["version"] + 1}
            self._record(actor_id, oid, "organization.update", {"reason": reason, "before": {
                "name": org["name"], "invitation_policy": org["invitation_policy"], "transfer_policy": transfer_before},
                "after": {**after, "transfer_policy": transfer_policy or transfer_before}})
            if transfer_policy is not None and transfer_policy != transfer_before:
                set_organization_outbound(self.store, oid, transfer_policy, actor_id=actor_id)
            if invitation_policy == "owners" and org["invitation_policy"] != "owners":
                record_organization(self.store, "organization_policy_restricted", organization_id=oid)
            self.store.update("organizations", {"id": oid}, after)
            if invitation_policy == "owners" and org["invitation_policy"] != "owners":
                self.store.execute("UPDATE invitations SET revoked_at=? WHERE accepted_at IS NULL "
                                   "AND revoked_at IS NULL AND workspace_id IN "
                                   "(SELECT id FROM workspaces WHERE organization_id=?) AND invited_by NOT IN "
                                   "(SELECT user_id FROM organization_memberships WHERE organization_id=? AND role='owner')",
                                   (now(), oid, oid))
            return self.get(oid, actor_id)

    def workspaces(self, oid, actor_id):
        self.require(oid, actor_id)
        return self.store.all("SELECT w.id,w.name,w.kind,w.status,w.organization_id,w.created_at, "
                              "m.role AS workspace_role FROM workspaces w LEFT JOIN memberships m "
                              "ON m.workspace_id=w.id AND m.user_id=? WHERE w.organization_id=? "
                              "AND w.deleted_at IS NULL ORDER BY w.created_at,w.id", (actor_id, oid))

    def create_workspace(self, oid, actor_id, name):
        name = _name(name)
        with self.store.tx():
            self.require(oid, actor_id, permission="org.workspaces.create", active=True)
            self._creation_budget(actor_id, "workspace", 30)
            ws = self._new_workspace(oid, actor_id, name)
            self._record(actor_id, oid, "organization.workspace.create", {"workspace_id": ws["id"]})
            return ws

    def _creation_budget(self, actor_id, kind, maximum):
        # The caller holds BEGIN IMMEDIATE; every process sees one admission count.
        table = "organizations" if kind == "organization" else "workspaces"
        row = self.store.one(f"SELECT COUNT(*) AS n,MIN(created_at) AS oldest FROM {table} "
                             "WHERE created_by=? AND created_at>?", (actor_id, now() - 86400))
        if row["n"] >= maximum:
            raise ServiceError(429, "creation_rate_limited", "今天创建的组织或工作空间较多，请稍后再试",
                               {"retry_after": max(1, int(row["oldest"] + 86400 - now()) + 1)})

    def adopt_workspace(self, oid, actor_id, wid, *, reason):
        reason = audit_reason(reason)
        with self.store.tx():
            self.require(oid, actor_id, permission="org.workspaces.adopt", active=True)
            ws = self.ident.get_workspace(wid)
            member = self.ident.membership(wid, actor_id)
            if not ws or not member:
                raise ServiceError(404, "no_workspace", "找不到该工作空间")
            if member["role"] != "owner":
                raise ServiceError(403, "workspace_owner_required", "只有当前工作空间所有者可以将其纳入组织")
            if ws["kind"] != "org" or wid == "default":
                raise ServiceError(409, "workspace_not_adoptable", "个人空间与历史默认空间不能纳入组织")
            if ws["status"] != "active":
                raise ServiceError(409, "workspace_unavailable", "该工作空间当前不可用")
            if ws.get("organization_id"):
                raise ServiceError(409, "workspace_already_associated", "该工作空间已属于一个组织")
            ts = now()
            # Preserve existing workspace members' effective access. This adds only
            # organization membership, never access to any other workspace.
            self.store.execute("INSERT INTO organization_memberships "
                               "(organization_id,user_id,role,created_at,updated_at,created_by) "
                               "SELECT ?,m.user_id,'member',?,?,? FROM memberships m JOIN users u ON u.id=m.user_id "
                               "WHERE m.workspace_id=? ON CONFLICT(organization_id,user_id) DO NOTHING",
                               (oid, ts, ts, actor_id, wid))
            self.store.execute("UPDATE workspaces SET organization_id=?,updated_at=? WHERE id=?", (oid, ts, wid))
            self._record(actor_id, oid, "organization.workspace.adopt", {"workspace_id": wid, "reason": reason})
            return self.ident.get_workspace(wid)

    def members(self, oid, actor_id, *, q="", role="", offset=0, limit=50):
        _, caller = self.require(oid, actor_id, permission="org.members.view")
        page = member_page(self.store, oid, q=q, role=role, offset=offset, limit=limit)
        for row in page["items"]:
            row["is_self"] = row["user_id"] == actor_id
            row["manageable"] = not row["is_self"] and (caller["role"] == "owner" or row["role"] == "member")
        return {**page, "single_owner": page["active_owner_count"] <= 1,
                "caller": {"user_id": actor_id, "role": caller["role"]}}

    def _target(self, oid, uid):
        member = self.store.one("SELECT * FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, uid))
        if not member:
            raise ServiceError(404, "not_a_member", "该账户不是组织成员")
        return member

    def _keep_owner(self, oid, uid):
        n = self.store.one("SELECT COUNT(*) AS n FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                           "WHERE m.organization_id=? AND m.role='owner' AND m.user_id<>? "
                           "AND u.status='active' AND u.deleted_at IS NULL", (oid, uid))["n"]
        if not n:
            raise ServiceError(409, "last_owner", "不能移除或降级最后一名可用的组织所有者")

    def change_role(self, oid, actor_id, uid, role, *, reason):
        reason = audit_reason(reason)
        if role not in ROLES:
            raise ServiceError(400, "bad_role", "请选择有效的组织角色", {"field": "role"})
        with self.store.tx():
            org, actor = self.require(oid, actor_id, permission="org.members.manage")
            target = self._target(oid, uid)
            if actor["role"] != "owner" and (target["role"] != "member" or role != "member"):
                raise ServiceError(403, "role_too_high", "只有组织所有者可以管理管理员和所有者")
            if ROLES.index(role) > ROLES.index(target["role"]):
                if org["status"] != "active":
                    raise ServiceError(409, "organization_unavailable", "该组织当前不可增加权限")
                self._user(uid)
            if target["role"] == "owner" and role != "owner":
                self._keep_owner(oid, uid)
            self._record(actor_id, oid, "organization.member.role", {"user_id": uid, "before": target["role"],
                         "after": role, "reason": reason})
            if ROLES.index(role) < ROLES.index(target["role"]):
                record_organization(self.store, "organization_role_changed", organization_id=oid, user_id=uid, role=role)
            self.store.execute("UPDATE organization_memberships SET role=?,updated_at=? "
                               "WHERE organization_id=? AND user_id=?", (role, now(), oid, uid))
            if ROLES.index(role) < ROLES.index(target["role"]):
                self._revoke_invites(oid, uid)
            return self._target(oid, uid)

    def _revoke_invites(self, oid, uid):
        return self.store.execute("UPDATE invitations SET revoked_at=? WHERE invited_by=? "
                                  "AND accepted_at IS NULL AND revoked_at IS NULL AND workspace_id IN "
                                  "(SELECT id FROM workspaces WHERE organization_id=?)", (now(), uid, oid))

    def remove_member(self, oid, actor_id, uid, *, reason, leave=False):
        reason = audit_reason(reason)
        with self.store.tx():
            _, actor = self.require(oid, actor_id, permission=None if leave else "org.members.manage")
            target = self._target(oid, uid)
            if leave and actor_id != uid:
                raise ServiceError(403, "forbidden", "只能退出自己的组织成员身份")
            if not leave and actor["role"] != "owner" and target["role"] != "member":
                raise ServiceError(403, "role_too_high", "只有组织所有者可以管理管理员和所有者")
            if target["role"] == "owner":
                self._keep_owner(oid, uid)
            # Removing org membership must not strand an attached workspace — nor one the platform
            # deleted that can still be recovered (A44): its memberships come back with it, and a
            # sole owner removed meanwhile would leave it ownerless, with nobody to name if it had
            # no other member. Hand over first (after a recovery), or wait for its window to close.
            # A live workspace is named first: its handover is possible now. A deleted one is
            # refused with its own reason, since it cannot be handed over until it is recovered.
            stranded = self.store.one("SELECT w.id, w.name, w.deleted_at, w.recover_until FROM workspaces w "
                "JOIN memberships m ON m.workspace_id=w.id "
                "WHERE w.organization_id=? AND (w.deleted_at IS NULL OR (w.status='deleted' AND w.recover_until>?)) "
                "AND m.user_id=? AND m.role='owner' "
                "AND NOT EXISTS (SELECT 1 FROM memberships other JOIN users u ON u.id=other.user_id "
                "JOIN organization_memberships om ON om.user_id=u.id AND om.organization_id=w.organization_id "
                "WHERE other.workspace_id=w.id AND other.role='owner' AND other.user_id<>? "
                "AND u.status='active' AND u.deleted_at IS NULL) ORDER BY w.deleted_at IS NOT NULL, w.id LIMIT 1",
                (oid, now(), uid, uid))
            if stranded and stranded["deleted_at"] is not None:
                raise deleted_workspace_owner(stranded)
            if stranded:
                raise ServiceError(409, "last_workspace_owner", "请先交接仍由此账户独自拥有的工作空间",
                                   {"workspace_id": stranded["id"]})
            self._record(actor_id, oid, "organization.member.leave" if leave else "organization.member.remove",
                         {"user_id": uid, "reason": reason})
            record_organization(self.store, "organization_member_removed", organization_id=oid, user_id=uid)
            revoked = self._revoke_workspace_authority(oid, uid=uid)
            self.store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, uid))
            from .org_roles import OrgRoles
            OrgRoles(self.store).unassign_all(oid, uid)       # custom roles go with the membership
            from .messaging import Messaging
            Messaging(self.store, self.ident).withdraw_from_organization(oid, uid)   # and so do the conversations
            # Withdraw explicit workspace grants so a later invitation into one
            # workspace cannot resurrect previous access to every other workspace.
            self.store.execute("UPDATE app_entitlements SET revoked_at=COALESCE(revoked_at,?) WHERE user_id=? "
                               "AND workspace_id IN (SELECT id FROM workspaces WHERE organization_id=?)", (now(), uid, oid))
            self.store.execute("DELETE FROM memberships WHERE user_id=? AND workspace_id IN "
                               "(SELECT id FROM workspaces WHERE organization_id=?)", (uid, oid))
            return {"removed": True, **revoked}

    def transfer(self, oid, actor_id, uid, *, reason):
        reason = audit_reason(reason)
        with self.store.tx():
            self.require(oid, actor_id, owner=True, active=True)
            self._target(oid, uid)
            self._user(uid)
            if uid == actor_id:
                raise ServiceError(400, "invalid_field", "请选择另一名组织成员", {"field": "user_id"})
            self._record(actor_id, oid, "organization.transfer", {"user_id": uid, "reason": reason})
            record_organization(self.store, "organization_role_changed", organization_id=oid, user_id=actor_id, role="admin")
            self.store.execute("UPDATE organization_memberships SET role='owner',updated_at=? "
                               "WHERE organization_id=? AND user_id=?", (now(), oid, uid))
            self.store.execute("UPDATE organization_memberships SET role='admin',updated_at=? "
                               "WHERE organization_id=? AND user_id=?", (now(), oid, actor_id))
            self._revoke_invites(oid, actor_id)
            return self.get(oid, actor_id)

    def usable_owners(self, oid):
        """The owners of `oid` who can act (USABLE_OWNER_CONDITION): `{user_id, email, status}` rows,
        empty for an orphaned organization. Joins an enclosing `store.tx()`."""
        return self.store.all(
            "SELECT m.user_id, u.email, u.status FROM organization_memberships m JOIN users u ON u.id=m.user_id "
            f"WHERE m.organization_id=? AND {USABLE_OWNER_CONDITION} ORDER BY m.created_at, m.user_id", (oid,))

    def recover_owner(self, oid, uid, *, actor_id, reason):
        """Name an existing member owner of an organization that has no usable owner (A37).

        `transfer` is an owner's act; this is the platform's, run by `actor_id` (a platform
        administrator who is not a member and gains no membership, workspace access or
        entitlement here). Frozen and recoverable organizations still need stewards; one deleted
        past `recover_until` is out of scope. In one transaction, in this order: 404
        `no_organization`, 409 `not_recoverable`, 409 `owner_present` (checked here under the
        write lock, so two concurrent recoveries end with one owner), 409 `target_unavailable`
        (not a member, or the account is not active or is deleted), 409
        `successor_second_factor_required` (`PlatformAccess.has_second_factor`: a confirmed TOTP
        authenticator or a user-verifying passkey bound to the current RP ID — the product's
        second-factor rule, the same the workspace recovery applies). Then the organization's
        own audit record (`organization.owner.recover`,
        as `transfer` records itself) and the role write. Returns
        `{"organization_id", "to", "previous_role", "unusable_owners"}`.
        """
        reason = audit_reason(reason)
        with self.store.tx():
            org = self.store.one("SELECT id,status,recover_until FROM organizations WHERE id=?", (oid,))
            if not org:
                raise ServiceError(404, "no_organization", "找不到该组织")
            if org["status"] == "deleted" and (org["recover_until"] is None or org["recover_until"] <= now()):
                raise ServiceError(409, "not_recoverable", "该组织已删除且恢复期限已过")
            if self.usable_owners(oid):
                raise ServiceError(409, "owner_present", "该组织已有可用的所有者，无需平台恢复")
            unusable = self.store.one("SELECT COUNT(*) AS n FROM organization_memberships "
                                      "WHERE organization_id=? AND role='owner'", (oid,))["n"]
            target = self.store.one(
                "SELECT m.role, u.status, u.deleted_at FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND m.user_id=?", (oid, uid))
            if not target or target["status"] != "active" or target["deleted_at"]:
                raise ServiceError(409, "target_unavailable", "接收人必须是这个组织里账户状态正常的成员")
            from .platform_access import PlatformAccess
            if not PlatformAccess(self.store).has_second_factor(uid):
                raise ServiceError(409, "successor_second_factor_required",
                                   "接收人需要先登记第二因素（动态验证码或安全密钥），所有者的操作需要它来确认身份")
            self._record(actor_id, oid, "organization.owner.recover",
                         {"user_id": uid, "before": target["role"], "after": "owner", "reason": reason})
            self.store.execute("UPDATE organization_memberships SET role='owner',updated_at=? "
                               "WHERE organization_id=? AND user_id=?", (now(), oid, uid))
            return {"organization_id": oid, "to": uid, "previous_role": target["role"], "unusable_owners": unusable}

    def _revoke_workspace_authority(self, oid, uid=None):
        ts = now()
        scope = "workspace_id IN (SELECT id FROM workspaces WHERE organization_id=?)"
        args = (oid,)
        user_scope = " AND user_id=?" if uid is not None else ""
        user_args = (uid,) if uid is not None else ()
        out = {}
        out["sessions_revoked"] = self.store.execute("UPDATE sessions SET revoked_at=?,revoked_reason=? WHERE "
            + scope + user_scope + " AND revoked_at IS NULL", (ts, "organization_access_withdrawn", *args, *user_args))
        out["tokens_revoked"] = self.store.execute("UPDATE oauth_tokens SET revoked_at=?,revoked_reason=? WHERE "
            + scope + user_scope + " AND revoked_at IS NULL", (ts, "organization_access_withdrawn", *args, *user_args))
        self.store.execute("UPDATE oauth_codes SET used_at=? WHERE " + scope + user_scope + " AND used_at IS NULL",
                           (ts, *args, *user_args))
        out["invitations_revoked"] = self.store.execute("UPDATE invitations SET revoked_at=? WHERE " + scope
            + (" AND invited_by=?" if uid is not None else "") + " AND revoked_at IS NULL AND accepted_at IS NULL",
            (ts, *args, *user_args))
        # API governance is a separately composed core capability. Never create
        # its schema here or silently skip an existing but incompatible table.
        if self.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='api_platform_approvals'"):
            out["approvals_revoked"] = self.store.execute("UPDATE api_platform_approvals SET status='revoked', "
                "revoked_at=? WHERE " + scope + (" AND (requested_by=? OR approved_by=?)" if uid is not None else "")
                + " AND status IN ('pending','approved')", (ts, oid, *((uid, uid) if uid is not None else ())))
        # Long-lived machine keys stay stored and are denied by the live org gate;
        # recovery cannot un-revoke sessions/tokens/approvals, or rotate old keys.
        return out

    def _transition(self, org, actor_id, action, reason, *, meta=None):
        """The lifecycle write shared by `transition` (an owner's act) and `platform_transition`
        (the platform's, A35), so the two cannot drift: called inside the mutation transaction
        with the organization's current row, after the caller's own authority check. `meta`
        adds to the organization's audit record. Returns the revocation counts (empty for
        unfreeze and recover)."""
        oid, ts, revoked = org["id"], now(), {}
        values = lifecycle_values(org, action, ts)
        # Audit is transactional and precedes the durable intent. No security
        # SQL can commit without fsync; later failure leaves a fail-closed
        # restore intent rather than allowing an old archive to revive access.
        self._record(actor_id, oid, "organization." + action,
                     {"reason": reason, **change({"status": org["status"]}, {"status": values["status"]}), **(meta or {})})
        if action in ("freeze", "delete"):
            lifecycle = {k: values[k] for k in ("deleted_at", "recover_until", "previous_status")} if action == "delete" else {}
            record_organization(self.store, "organization_" + ("frozen" if action == "freeze" else "deleted"),
                                organization_id=oid, **lifecycle)
            revoked = self._revoke_workspace_authority(oid)
        values.update(updated_at=ts, version=org["version"] + 1)
        self.store.update("organizations", {"id": oid}, values)
        return revoked

    def transition(self, oid, actor_id, action, *, reason):
        reason = audit_reason(reason)
        with self.store.tx():
            org, _ = self.require(oid, actor_id, owner=True)
            revoked = self._transition(org, actor_id, action, reason)
            return {"organization": self.get(oid, actor_id), **revoked}

    def platform_transition(self, oid, actor_id, action, *, reason):
        """The platform's lifecycle act on any organization (A35): `actor_id` is a platform
        administrator who need not be a member and gains no membership, workspace access or
        entitlement here. The same state machine, journal entry and revocations as `transition`
        (`_transition`); only the authority differs — the organization has to exist (404
        `no_organization`), nothing else — and the organization's own audit record carries
        `"by": "platform"`, so its governance log shows platform governance. 400 `bad_action`
        outside LIFECYCLE_ACTIONS. Returns the platform view (PLATFORM_VIEW_COLUMNS) and the
        revocation counts, never the members' view."""
        reason = audit_reason(reason)
        if action not in LIFECYCLE_ACTIONS:
            raise ServiceError(400, "bad_action", "请选择有效的组织生命周期操作", {"field": "action"})
        with self.store.tx():
            org = self.store.one("SELECT * FROM organizations WHERE id=?", (oid,))
            if not org:
                raise ServiceError(404, "no_organization", "找不到该组织")
            revoked = self._transition(org, actor_id, action, reason, meta={"by": "platform"})
            return {"organization": platform_view(self.store, oid), **revoked}
