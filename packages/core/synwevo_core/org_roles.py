"""Organization custom roles: named sets of organization-level permissions, assigned to members.

Why here: the account service governs organizations and the portal and Synwevo Docs read the
same memberships, so the permission model has to be one module every role can consult.

Model (docs/platform/organization-roles.md):

* The three base roles stay (`owner` / `admin` / `member` on ``organization_memberships``) and
  keep their meaning: an owner holds every permission including the ones that cannot be
  delegated (ownership transfer, the organization's lifecycle); an admin holds the
  administrative set below; a member holds none by default. Workspace roles (viewer / editor /
  admin / owner on ``memberships``) and everything inside Synwevo Docs are untouched: an
  organization role never opens a document (Notion's rule, read 2026-09-26: "Admin roles don't
  change what someone can see or edit in pages or databases").
* A custom role is a name plus a subset of the *delegable* permissions; a member can hold
  several. Effective permissions = base role ∪ assigned roles.
* Whoever creates, edits or assigns a role must already hold every permission it carries and
  ``org.roles.manage`` itself: delegation cannot exceed the delegator. Owners hold everything,
  so they can delegate role management; a delegated roles manager cannot mint an owner.
* Deleting a role that is still assigned is refused unless the caller says what happens to the
  assignees (unassign); nobody is silently promoted or demoted.

Permission ids are stable and language-independent; names are shown through the catalogue's
three-language labels. Adding a permission to the catalogue never adds it to an existing
custom role.
"""
from __future__ import annotations

import json

from .errors import ServiceError
from .store import Store, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_roles (
  id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  name TEXT NOT NULL, description TEXT,
  permissions TEXT NOT NULL,
  created_by TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_org_role_name ON organization_roles(organization_id, name);
CREATE TABLE IF NOT EXISTS organization_role_assignments (
  organization_id TEXT NOT NULL REFERENCES organizations(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role_id TEXT NOT NULL REFERENCES organization_roles(id),
  created_at REAL NOT NULL, created_by TEXT,
  PRIMARY KEY (organization_id, user_id, role_id)
);
CREATE INDEX IF NOT EXISTS ix_org_role_assign_role ON organization_role_assignments(role_id);
"""

#: id → group, whether only an owner can ever hold it, three-language label and description.
CATALOGUE: dict[str, dict] = {
    "org.settings.manage": {"group": "organization", "owner_only": False,
        "label": {"zh-Hans": "管理组织设置", "zh-Hant": "管理組織設定", "en": "Manage organization settings"},
        "desc": {"zh-Hans": "修改组织名称与头像", "zh-Hant": "修改組織名稱與頭像", "en": "Rename the organization and change its avatar"}},
    "org.members.view": {"group": "members", "owner_only": False,
        "label": {"zh-Hans": "查看成员列表", "zh-Hant": "檢視成員列表", "en": "View the member list"},
        "desc": {"zh-Hans": "查看组织成员、角色与账户状态", "zh-Hant": "檢視組織成員、角色與帳戶狀態", "en": "See members, their roles and account status"}},
    "org.members.invite": {"group": "members", "owner_only": False,
        "label": {"zh-Hans": "邀请成员", "zh-Hant": "邀請成員", "en": "Invite members"},
        "desc": {"zh-Hans": "向组织内的工作空间发出邀请（受组织邀请策略约束）", "zh-Hant": "向組織內的工作空間發出邀請（受組織邀請策略約束）", "en": "Invite people into the organization's workspaces (subject to the invitation policy)"}},
    "org.members.manage": {"group": "members", "owner_only": False,
        "label": {"zh-Hans": "管理成员", "zh-Hant": "管理成員", "en": "Manage members"},
        "desc": {"zh-Hans": "更改普通成员的组织角色、移除普通成员；管理员与所有者仍只由所有者处理", "zh-Hant": "變更一般成員的組織角色、移除一般成員；管理員與擁有者仍只由擁有者處理", "en": "Change ordinary members' organization role and remove them; admins and owners remain the owner's call"}},
    "org.members.import": {"group": "members", "owner_only": False,
        "label": {"zh-Hans": "批量导入成员", "zh-Hant": "批次匯入成員", "en": "Import members in bulk"},
        "desc": {"zh-Hans": "从文件批量邀请成员并预设角色", "zh-Hant": "從檔案批次邀請成員並預設角色", "en": "Invite members from a file with preset roles"}},
    "org.roles.manage": {"group": "roles", "owner_only": False,
        "label": {"zh-Hans": "管理自定义角色", "zh-Hant": "管理自訂角色", "en": "Manage custom roles"},
        "desc": {"zh-Hans": "创建、修改、删除自定义角色并分配给成员——只能授予自己已持有的权限", "zh-Hant": "建立、修改、刪除自訂角色並指派給成員——只能授予自己已持有的權限", "en": "Create, edit, delete custom roles and assign them — only with permissions you already hold"}},
    "org.workspaces.create": {"group": "workspaces", "owner_only": False,
        "label": {"zh-Hans": "创建工作空间", "zh-Hant": "建立工作空間", "en": "Create workspaces"},
        "desc": {"zh-Hans": "在组织下创建新的工作空间", "zh-Hant": "在組織下建立新的工作空間", "en": "Create new workspaces under the organization"}},
    "org.workspaces.adopt": {"group": "workspaces", "owner_only": False,
        "label": {"zh-Hans": "纳入已有工作空间", "zh-Hant": "納入既有工作空間", "en": "Adopt existing workspaces"},
        "desc": {"zh-Hans": "把自己拥有的工作空间纳入组织", "zh-Hant": "把自己擁有的工作空間納入組織", "en": "Bring a workspace you own into the organization"}},
    "org.audit.read": {"group": "governance", "owner_only": False,
        "label": {"zh-Hans": "查看组织审计", "zh-Hant": "檢視組織稽核", "en": "Read the organization audit log"},
        "desc": {"zh-Hans": "查看组织治理操作的记录", "zh-Hant": "檢視組織治理操作的紀錄", "en": "See the record of governance actions"}},
    "org.approvals.decide": {"group": "governance", "owner_only": False,
        "label": {"zh-Hans": "审批复制申请", "zh-Hant": "審批複製申請", "en": "Decide copy approvals"},
        "desc": {"zh-Hans": "批准或拒绝把个人项目复制到组织空间的申请（需同时是目标空间的管理员）", "zh-Hant": "核准或拒絕把個人專案複製到組織空間的申請（需同時是目標空間的管理員）", "en": "Approve or decline requests to copy a personal project into an organization workspace (also needs workspace admin)"}},
    "org.billing.manage": {"group": "governance", "owner_only": False,
        "label": {"zh-Hans": "管理组织付费", "zh-Hant": "管理組織付費", "en": "Manage organization payments"},
        "desc": {"zh-Hans": "为组织钱包充值、授权成员用组织付费并设置每月额度、查看组织账单（不含对话内容）", "zh-Hant": "為組織錢包儲值、授權成員以組織付費並設定每月額度、檢視組織帳單（不含對話內容）", "en": "Top up the organization wallet, let members charge it within monthly limits, and read the organization statement (never conversation content)"}},
    "org.messaging.manage": {"group": "governance", "owner_only": False,
        "label": {"zh-Hans": "管理群聊", "zh-Hant": "管理群組對話", "en": "Manage group conversations"},
        "desc": {"zh-Hans": "创建组织级群聊、归档群聊、处理举报", "zh-Hant": "建立組織層級群組、封存群組、處理檢舉", "en": "Create organization-wide groups, archive them and handle reports"}},
    "org.ownership.transfer": {"group": "owner", "owner_only": True,
        "label": {"zh-Hans": "交接所有权", "zh-Hant": "交接擁有權", "en": "Transfer ownership"},
        "desc": {"zh-Hans": "仅所有者，不可委派", "zh-Hant": "僅擁有者，不可委派", "en": "Owners only, never delegated"}},
    "org.lifecycle.manage": {"group": "owner", "owner_only": True,
        "label": {"zh-Hans": "冻结、删除或恢复组织", "zh-Hant": "凍結、刪除或復原組織", "en": "Freeze, delete or restore the organization"},
        "desc": {"zh-Hans": "仅所有者，不可委派", "zh-Hant": "僅擁有者，不可委派", "en": "Owners only, never delegated"}},
}
GROUPS = ("organization", "members", "roles", "workspaces", "governance", "owner")
DELEGABLE = frozenset(k for k, v in CATALOGUE.items() if not v["owner_only"])
ALL = frozenset(CATALOGUE)
BASE: dict[str, frozenset[str]] = {
    "owner": ALL,
    "admin": frozenset({"org.settings.manage", "org.members.view", "org.members.invite", "org.members.manage", "org.members.import",
                        "org.workspaces.create", "org.workspaces.adopt", "org.audit.read", "org.approvals.decide", "org.messaging.manage"}),
    "member": frozenset(),
}
MAX_ROLES = 50


def catalogue(locale: str = "zh-Hans") -> list[dict]:
    loc = locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
    return [{"id": k, "group": v["group"], "owner_only": v["owner_only"], "label": v["label"][loc], "description": v["desc"][loc]}
            for k, v in CATALOGUE.items()]


def _perms(value) -> frozenset[str]:
    if isinstance(value, str):
        value = json.loads(value or "[]")
    return frozenset(value or ())


class OrgRoles:
    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------ reading
    def base_role(self, oid: str, uid: str) -> str | None:
        row = self.store.one("SELECT m.role FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                             "WHERE m.organization_id=? AND m.user_id=? AND u.status='active' AND u.deleted_at IS NULL", (oid, uid))
        return row["role"] if row else None

    def assigned(self, oid: str, uid: str) -> list[dict]:
        return [self._out(r) for r in self.store.all(
            "SELECT r.* FROM organization_role_assignments a JOIN organization_roles r ON r.id=a.role_id "
            "WHERE a.organization_id=? AND a.user_id=? ORDER BY r.name", (oid, uid))]

    def effective(self, oid: str, uid: str) -> dict:
        """What this member may do and where each permission comes from. Read-only; the answer
        is computed from the rows, never from a signed-in preview of the member."""
        base = self.base_role(oid, uid)
        sources: dict[str, list[str]] = {}
        if base:
            for p in BASE[base]:
                sources.setdefault(p, []).append(f"base:{base}")
        roles = self.assigned(oid, uid) if base else []
        for r in roles:
            for p in r["permissions"]:
                sources.setdefault(p, []).append(f"role:{r['id']}")
        return {"base_role": base, "roles": roles, "permissions": sorted(sources), "sources": sources}

    def permissions_of(self, oid: str, uid: str) -> frozenset[str]:
        base = self.base_role(oid, uid)
        if not base:
            return frozenset()
        out = set(BASE[base])
        for r in self.store.all("SELECT r.permissions FROM organization_role_assignments a JOIN organization_roles r ON r.id=a.role_id "
                                "WHERE a.organization_id=? AND a.user_id=?", (oid, uid)):
            out |= _perms(r["permissions"])
        return frozenset(out)

    def has(self, oid: str, uid: str, permission: str) -> bool:
        if permission not in CATALOGUE:
            raise ValueError(permission)
        return permission in self.permissions_of(oid, uid)

    def list(self, oid: str) -> list[dict]:
        rows = self.store.all("SELECT r.*, (SELECT COUNT(*) FROM organization_role_assignments a WHERE a.role_id=r.id) AS assigned_count "
                              "FROM organization_roles r WHERE r.organization_id=? ORDER BY r.name", (oid,))
        return [self._out(r) for r in rows]

    def get(self, oid: str, rid: str) -> dict:
        row = self.store.one("SELECT r.*, (SELECT COUNT(*) FROM organization_role_assignments a WHERE a.role_id=r.id) AS assigned_count "
                             "FROM organization_roles r WHERE r.id=? AND r.organization_id=?", (rid, oid))
        if not row:
            raise ServiceError(404, "no_role", "找不到该角色")
        return self._out(row)

    def _out(self, r: dict) -> dict:
        return {"id": r["id"], "organization_id": r["organization_id"], "name": r["name"], "description": r.get("description"),
                "permissions": sorted(_perms(r["permissions"])), "version": r["version"], "created_at": r["created_at"],
                "updated_at": r["updated_at"], "assigned_count": r.get("assigned_count")}

    # ------------------------------------------------------------------ writing
    def _manager(self, oid: str, actor_id: str, *, needs: frozenset[str] = frozenset()) -> frozenset[str]:
        held = self.permissions_of(oid, actor_id)
        if "org.roles.manage" not in held:
            raise ServiceError(403, "forbidden", "只有持有「管理自定义角色」权限的成员可以执行此操作")
        missing = sorted(needs - held)
        if missing:
            raise ServiceError(403, "permission_not_held", "不能授予自己没有的权限", {"permissions": missing})
        return held

    @staticmethod
    def _validate(name, description, permissions) -> tuple[str, str | None, frozenset[str]]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
            raise ServiceError(400, "invalid_field", "角色名称须为 1 至 60 个字符", {"field": "name"})
        if description is not None and (not isinstance(description, str) or len(description) > 500):
            raise ServiceError(400, "invalid_field", "说明最多 500 个字符", {"field": "description"})
        if not isinstance(permissions, list) or not permissions or not all(isinstance(p, str) for p in permissions):
            raise ServiceError(400, "invalid_field", "请至少选择一项权限", {"field": "permissions"})
        unknown = sorted(set(permissions) - ALL)
        if unknown:
            raise ServiceError(400, "unknown_permission", "包含未知的权限", {"permissions": unknown})
        locked = sorted(set(permissions) - DELEGABLE)
        if locked:
            raise ServiceError(400, "owner_only_permission", "这些权限仅所有者持有，不能放进自定义角色", {"permissions": locked})
        return name.strip(), (description.strip() if description else None), frozenset(permissions)

    def create(self, oid: str, actor_id: str, *, name, description=None, permissions) -> dict:
        name, description, perms = self._validate(name, description, permissions)
        with self.store.tx() as t:
            self._manager(oid, actor_id, needs=perms)
            if t.execute("SELECT COUNT(*) AS n FROM organization_roles WHERE organization_id=?", (oid,)).fetchone()[0] >= MAX_ROLES:
                raise ServiceError(409, "too_many_roles", f"一个组织最多 {MAX_ROLES} 个自定义角色", {"limit": MAX_ROLES})
            if t.execute("SELECT 1 FROM organization_roles WHERE organization_id=? AND name=?", (oid, name)).fetchone():
                raise ServiceError(409, "role_name_taken", "已有同名角色", {"field": "name"})
            rid = new_id("orole")
            ts = now()
            t.execute("INSERT INTO organization_roles (id,organization_id,name,description,permissions,created_by,created_at,updated_at,version) "
                      "VALUES (?,?,?,?,?,?,?,?,1)", (rid, oid, name, description, json.dumps(sorted(perms)), actor_id, ts, ts))
        return self.get(oid, rid)

    def update(self, oid: str, actor_id: str, rid: str, *, name=None, description=None, permissions=None, version=None) -> dict:
        current = self.get(oid, rid)
        new_name = current["name"] if name is None else name
        new_desc = current["description"] if description is None else description
        new_perms = current["permissions"] if permissions is None else permissions
        new_name, new_desc, perms = self._validate(new_name, new_desc, new_perms)
        with self.store.tx() as t:
            # Holding the role's *current* permissions too: nobody edits a role above their own reach.
            self._manager(oid, actor_id, needs=perms | frozenset(current["permissions"]))
            if version is not None and int(version) != current["version"]:
                raise ServiceError(409, "version_conflict", "角色已被他人修改，请刷新后重试", {"version": current["version"]})
            if new_name != current["name"] and t.execute("SELECT 1 FROM organization_roles WHERE organization_id=? AND name=? AND id!=?",
                                                          (oid, new_name, rid)).fetchone():
                raise ServiceError(409, "role_name_taken", "已有同名角色", {"field": "name"})
            t.execute("UPDATE organization_roles SET name=?, description=?, permissions=?, updated_at=?, version=version+1 WHERE id=?",
                      (new_name, new_desc, json.dumps(sorted(perms)), now(), rid))
        return self.get(oid, rid)

    def copy(self, oid: str, actor_id: str, rid: str, *, name=None) -> dict:
        """A new role with the source role's description and permissions (CI-94). Copying is
        creating: the actor must hold every permission in it (holding a permission is what lets a
        manager grant it), the organization's cap and name uniqueness apply, and nobody is
        assigned the copy. Without a name the copy takes the source's name with a number — a
        language-neutral suffix, since the name is data every member reads."""
        src = self.get(oid, rid)
        if name is not None:
            name, _, _ = self._validate(name, src["description"], src["permissions"])
        with self.store.tx() as t:
            self._manager(oid, actor_id, needs=frozenset(src["permissions"]))
            if t.execute("SELECT COUNT(*) AS n FROM organization_roles WHERE organization_id=?", (oid,)).fetchone()[0] >= MAX_ROLES:
                raise ServiceError(409, "too_many_roles", f"一个组织最多 {MAX_ROLES} 个自定义角色", {"limit": MAX_ROLES})
            taken = {r[0] for r in t.execute("SELECT name FROM organization_roles WHERE organization_id=?", (oid,)).fetchall()}
            if name is None:
                for n in range(2, MAX_ROLES + 2):
                    suffix = f" ({n})"
                    candidate = src["name"][:60 - len(suffix)].rstrip() + suffix
                    if candidate not in taken:
                        name = candidate
                        break
            if name is None or name in taken:
                raise ServiceError(409, "role_name_taken", "已有同名角色", {"field": "name"})
            new_rid = new_id("orole")
            ts = now()
            t.execute("INSERT INTO organization_roles (id,organization_id,name,description,permissions,created_by,created_at,updated_at,version) "
                      "VALUES (?,?,?,?,?,?,?,?,1)", (new_rid, oid, name, src["description"], json.dumps(src["permissions"]), actor_id, ts, ts))
        return self.get(oid, new_rid)

    def delete(self, oid: str, actor_id: str, rid: str, *, unassign: bool = False) -> dict:
        current = self.get(oid, rid)
        with self.store.tx() as t:
            self._manager(oid, actor_id, needs=frozenset(current["permissions"]))
            n = t.execute("SELECT COUNT(*) AS n FROM organization_role_assignments WHERE role_id=?", (rid,)).fetchone()[0]
            if n and not unassign:
                raise ServiceError(409, "role_assigned", "该角色仍分配给成员；确认后将解除这些分配", {"assigned_count": n})
            t.execute("DELETE FROM organization_role_assignments WHERE role_id=?", (rid,))
            t.execute("DELETE FROM organization_roles WHERE id=?", (rid,))
        return {"deleted": rid, "unassigned": n}

    def assign(self, oid: str, actor_id: str, uid: str, role_ids: list) -> dict:
        """Replace the member's set of custom roles; the member must be a current organization
        member. The assigner must hold every permission of each role whose assignment changes —
        added or taken away. A role the member keeps untouched needs nothing: leaving it in place
        grants nothing, while taking away a role above the assigner's reach would let a delegate
        strip the owner's own delegations (admin defect D12)."""
        if not isinstance(role_ids, list) or not all(isinstance(r, str) for r in role_ids):
            raise ServiceError(400, "invalid_field", "role_ids 必须是角色 ID 列表", {"field": "role_ids"})
        wanted = {}
        for rid in role_ids:
            wanted[rid] = self.get(oid, rid)
        with self.store.tx() as t:
            held = self._manager(oid, actor_id)
            if not self.base_role(oid, uid):
                raise ServiceError(404, "no_member", "该用户不是这个组织的成员")
            current = {r["id"]: r for r in self.assigned(oid, uid)}
            before = set(current)
            after = set(wanted)
            granting = frozenset(p for rid in after - before for p in wanted[rid]["permissions"]) - held
            if granting:
                raise ServiceError(403, "permission_not_held", "不能授予自己没有的权限", {"permissions": sorted(granting)})
            taking = frozenset(p for rid in before - after for p in current[rid]["permissions"]) - held
            if taking:
                raise ServiceError(403, "permission_not_held", "不能收回包含自己没有的权限的角色", {"permissions": sorted(taking)})
            for rid in before - after:
                t.execute("DELETE FROM organization_role_assignments WHERE organization_id=? AND user_id=? AND role_id=?", (oid, uid, rid))
            for rid in after - before:
                t.execute("INSERT INTO organization_role_assignments (organization_id,user_id,role_id,created_at,created_by) VALUES (?,?,?,?,?)",
                          (oid, uid, rid, now(), actor_id))
        return {"user_id": uid, "roles": self.assigned(oid, uid), "added": sorted(after - before), "removed": sorted(before - after)}

    def unassign_all(self, oid: str, uid: str) -> int:
        """When a member leaves or is removed: custom roles go with the membership."""
        return int(self.store.execute("DELETE FROM organization_role_assignments WHERE organization_id=? AND user_id=?", (oid, uid)) or 0)
