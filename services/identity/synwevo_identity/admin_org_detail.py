"""Platform read-only organization detail (A34): any organization's governance metadata, for a
holder of `platform.orgs.view` who need not be — and does not become — one of its members.

Until now only a member could read an organization (`Organizations.require` answers a non-member
404), so the console's organization list linked to the tenant settings page only for an
administrator who happened to belong. This is the platform's view, held to the rules of the rest of
the console:

* **Read-only, and nothing granted.** No membership, entitlement, token or session is created; the
  tenant route `/api/auth/organizations/{oid}` keeps answering a non-member administrator 404 and
  the applications keep refusing them. Reads are not audited (no `*.view` action is).
* **Metadata, never content.** The organization's lifecycle columns and settings, counts, the names
  of its workspaces, custom roles and people (display name, account status), and its open member
  imports and project copies as ids, states, counts and times. Never a project or template name, a
  member-import row's address or payload, a copy's manifest, a document or an invitation token.
  A person's address is returned only to a reader of accounts (`platform.users.view`, as A47's
  `has_account`), else `email: null` — and then the member search does not match addresses either,
  so the list is no address oracle.
* **One committed state, no lock.** Each response is read in one deferred read transaction
  (`read_snapshot`, admin_invitations `_one_snapshot`): every statement sees the same state, and no
  writer waits for it. Lists are capped with their totals, and the number of statements a read
  issues does not grow with the rows (per-row counts are subqueries of the one statement).
* **The tenant's own readers.** The member page is core `member_page` (what `Organizations.members`
  lists), effective permissions `OrgRoles.effective`, the custom roles `OrgRoles.list`, the transfer
  cap `data_flow.organization_outbound`, the lifecycle columns `platform_view`.

Routes (all `GET`, under the parent's /api/admin): `/organizations/{oid}`, `/organizations/{oid}/workspaces`,
`/organizations/{oid}/members`, `/organizations/{oid}/members/{uid}/permissions`. None collides with
the POST `/organizations/{oid}/preview`, `/{action}` (A35) or `/recover-owner` (A37): a GET only
partially matches those, and FastAPI keeps looking for a full match.
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Query, Request

from synwevo_core.data_flow import organization_outbound
from synwevo_core.errors import ServiceError
from synwevo_core.org_roles import OrgRoles, catalogue as permission_catalogue
from synwevo_core.organizations import (USABLE_ACCOUNT_CONDITION, USABLE_OWNER_CONDITION, current_membership,
                                        member_page, platform_view)
from synwevo_core.platform_access import require_platform
from synwevo_core.store import Store, now

VIEW = "platform.orgs.view"
#: Addresses are account data, as the users list is: shown only to its readers.
ACCOUNTS_VIEW = "platform.users.view"
#: Owners and admins listed in the detail (each), and the workspaces on its first page.
PEOPLE_CAP = 50
WORKSPACE_CAP = 50
#: The newest open member imports and project copies listed in the detail (each).
PENDING_CAP = 20
#: A member import still in progress — `member_import.IMPORT_STATUSES` before its terminal
#: `finished`, `partial`, `failed` and `cancelled`.
OPEN_IMPORT_STATUSES = ("extracted", "planned", "running")
#: A project copy still open — the statuses `ProjectCopies.request` refuses a duplicate against;
#: `rejected`, `withdrawn`, `expired`, `done`, `failed` and `stale` are terminal.
OPEN_COPY_STATUSES = ("pending", "approved", "running")
PERSON_COLUMNS = ("m.user_id, u.display_name, u.email, u.status AS account_status, m.created_at AS joined_at, "
                  f"COALESCE(({USABLE_ACCOUNT_CONDITION}), 0) AS usable")


def _marks(values) -> str:
    return ",".join("?" * len(values))


class OrganizationDetail:
    """The reads behind the routes; every method runs inside the caller's `read_snapshot`."""

    def __init__(self, store: Store):
        self.store = store

    @contextmanager
    def read_snapshot(self):
        """Every statement inside sees one committed state, and nothing waits for it: a deferred
        transaction on this thread's connection (admin_invitations `_one_snapshot`), whose first
        read fixes the snapshot under WAL. Read-only by construction, always ended by a ROLLBACK."""
        with self.store.conn() as c:
            if c.in_transaction:           # inside a caller's transaction, whose view already is one
                yield
                return
            c.execute("BEGIN DEFERRED")
            try:
                yield
            finally:
                if c.in_transaction:
                    c.execute("ROLLBACK")

    def _table(self, name: str) -> bool:
        return bool(self.store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))

    def organization(self, oid: str) -> dict:
        """`platform_view` — deleted organizations included — or 404 `no_organization`."""
        org = platform_view(self.store, oid)
        if not org:
            raise ServiceError(404, "no_organization", "找不到该组织")
        return org

    @staticmethod
    def _person(row: dict, accounts: bool) -> dict:
        return {"user_id": row["user_id"], "display_name": row["display_name"],
                "email": row["email"] if accounts else None, "account_status": row["account_status"],
                "joined_at": row["joined_at"], "usable": bool(row["usable"])}

    @staticmethod
    def _ref(user_id, display_name) -> dict | None:
        return {"user_id": user_id, "display_name": display_name} if user_id else None

    def _people(self, oid: str, role: str, accounts: bool) -> list[dict]:
        rows = self.store.all(f"SELECT {PERSON_COLUMNS} FROM organization_memberships m LEFT JOIN users u ON u.id=m.user_id "
                              "WHERE m.organization_id=? AND m.role=? ORDER BY m.created_at, m.user_id LIMIT ?",
                              (oid, role, PEOPLE_CAP))
        return [self._person(row, accounts) for row in rows]

    def workspace_page(self, oid: str, *, offset: int, limit: int) -> list[dict]:
        """The organization's undeleted workspaces by creation, with each one's member count —
        counted for the page's rows only (the page is cut first, then counted)."""
        return self.store.all(
            "SELECT p.id, p.name, p.kind, p.status, p.created_at, "
            "(SELECT COUNT(*) FROM memberships x WHERE x.workspace_id=p.id) AS members "
            "FROM (SELECT id, name, kind, status, created_at FROM workspaces WHERE organization_id=? AND deleted_at IS NULL "
            "ORDER BY created_at, id LIMIT ? OFFSET ?) p ORDER BY p.created_at, p.id", (oid, limit, offset))

    def workspace_counts(self, oid: str) -> dict:
        row = self.store.one("SELECT COUNT(*) AS n, COALESCE(SUM(status='frozen'), 0) AS frozen FROM workspaces "
                             "WHERE organization_id=? AND deleted_at IS NULL", (oid,))
        return {"workspaces": row["n"], "workspaces_frozen": row["frozen"]}

    def _imports(self, oid: str) -> tuple[int | None, list[dict]]:
        """Open member imports. `rows` counts the planned rows: they are written when the import is
        planned, so an import still `extracted` reports 0 (the console words it by state)."""
        if not self._table("member_imports"):
            return None, []
        marks = _marks(OPEN_IMPORT_STATUSES)
        total = self.store.one(f"SELECT COUNT(*) AS n FROM member_imports WHERE organization_id=? AND status IN ({marks})",
                               (oid, *OPEN_IMPORT_STATUSES))["n"]
        rows = self.store.all(
            "SELECT i.id, i.status, i.created_at, u.id AS by_id, u.display_name AS by_name, "
            "(SELECT COUNT(*) FROM member_import_rows r WHERE r.import_id=i.id) AS row_count "
            f"FROM member_imports i LEFT JOIN users u ON u.id=i.created_by WHERE i.organization_id=? AND i.status IN ({marks}) "
            "ORDER BY i.created_at DESC, i.id DESC LIMIT ?", (oid, *OPEN_IMPORT_STATUSES, PENDING_CAP))
        return total, [{"id": r["id"], "state": r["status"], "rows": r["row_count"], "created_at": r["created_at"],
                        "created_by": self._ref(r["by_id"], r["by_name"])} for r in rows]

    def _copies(self, oid: str) -> tuple[int | None, list[dict]]:
        """Open copies into the organization. `expires_at` is the deadline of the copy's current
        state: the request's (`expires_at`) while pending, the approval's (`approval_expires_at`)
        once approved — the request's own has stopped applying then — and none while running.
        A pending or approved copy past that deadline is not open: it can no longer run, and only
        Docs' sweep marks it expired, so the read leaves it out without writing (as `claim_approved`
        refuses it)."""
        if not self._table("project_copies"):
            return None, []
        marks = _marks(OPEN_COPY_STATUSES)
        ts = now()
        live = ("(c.status='running' OR (c.status='pending' AND c.expires_at>?) "
                "OR (c.status='approved' AND c.approval_expires_at>?))")
        total = self.store.one(f"SELECT COUNT(*) AS n FROM project_copies c WHERE c.target_organization_id=? "
                               f"AND c.status IN ({marks}) AND {live}", (oid, *OPEN_COPY_STATUSES, ts, ts))["n"]
        rows = self.store.all(
            "SELECT c.id, c.status, c.source_workspace_id, c.target_workspace_id, c.created_at, "
            "CASE c.status WHEN 'pending' THEN c.expires_at WHEN 'approved' THEN c.approval_expires_at END AS expires_at, "
            "u.id AS by_id, u.display_name AS by_name "
            f"FROM project_copies c LEFT JOIN users u ON u.id=c.requester_id WHERE c.target_organization_id=? AND c.status IN ({marks}) "
            f"AND {live} ORDER BY c.created_at DESC, c.id DESC LIMIT ?", (oid, *OPEN_COPY_STATUSES, ts, ts, PENDING_CAP))
        return total, [{"id": r["id"], "status": r["status"], "source_workspace_id": r["source_workspace_id"],
                        "target_workspace_id": r["target_workspace_id"], "created_at": r["created_at"],
                        "expires_at": r["expires_at"], "requested_by": self._ref(r["by_id"], r["by_name"])} for r in rows]

    def detail(self, oid: str, actor_id: str, *, accounts: bool, locale: str = "zh-Hans") -> dict:
        org = self.organization(oid)
        extra = self.store.one(
            "SELECT o.created_at, o.invitation_policy, "
            "EXISTS (SELECT 1 FROM organization_avatars a WHERE a.organization_id=o.id) AS has_avatar "
            "FROM organizations o WHERE o.id=?", (oid,))
        people = self.store.one(
            "SELECT COUNT(*) AS members, COALESCE(SUM(m.role='owner'), 0) AS owners, "
            "COALESCE(SUM(m.role='admin'), 0) AS admins, "
            f"COALESCE(SUM(CASE WHEN {USABLE_OWNER_CONDITION} THEN 1 ELSE 0 END), 0) AS usable_owners "
            "FROM organization_memberships m LEFT JOIN users u ON u.id=m.user_id WHERE m.organization_id=?", (oid,))
        spaces = self.workspace_counts(oid)
        roles = OrgRoles(self.store).list(oid)
        imports_total, imports = self._imports(oid)
        copies_total, copies = self._copies(oid)
        viewer = current_membership(self.store, oid, actor_id)
        return {
            "organization": {"id": org["id"], "name": org["name"], "status": org["status"],
                             "created_at": extra["created_at"], "deleted_at": org["deleted_at"],
                             "recover_until": org["recover_until"], "previous_status": org["previous_status"],
                             "version": org["version"], "has_avatar": bool(extra["has_avatar"])},
            # As the tenant GET reports them (`Organizations.get`).
            "settings": {"invitation_policy": extra["invitation_policy"],
                         "transfer_policy": organization_outbound(self.store, oid)["outbound"]},
            "counts": {"members": people["members"], "owners": people["owners"], "admins": people["admins"],
                       "usable_owners": people["usable_owners"], **spaces, "custom_roles": len(roles),
                       "pending_imports": imports_total, "pending_copies": copies_total},
            "owners": self._people(oid, "owner", accounts), "admins": self._people(oid, "admin", accounts),
            "owners_total": people["owners"], "admins_total": people["admins"],
            "workspaces": {"items": self.workspace_page(oid, offset=0, limit=WORKSPACE_CAP), "total": spaces["workspaces"]},
            "custom_roles": [{"id": r["id"], "name": r["name"], "permissions": r["permissions"],
                              "assigned": r["assigned_count"]} for r in roles],
            "pending": {"imports": imports, "copies": copies},
            "viewer": {"is_member": viewer is not None, "role": viewer["role"] if viewer else None},
            # The labels of the permission ids above, in the reader's language — the tenant roles
            # page's catalogue (`/api/auth/organizations/{oid}/permissions`), which only members reach.
            "permission_catalogue": permission_catalogue(locale),
        }

    def members(self, oid: str, *, accounts: bool, q, role, offset, limit) -> dict:
        """core `member_page` — the tenant's list, its order and refusals — with `usable` and, for a
        reader who is not shown addresses, no address in the rows and none matched by the search."""
        self.organization(oid)
        page = member_page(self.store, oid, q=q, role=role, offset=offset, limit=limit,
                           match_email=accounts, usable=True)
        if not accounts:
            for row in page["items"]:
                row["email"] = None
        return page

    def permissions(self, oid: str, uid: str) -> dict:
        """`OrgRoles.effective` of a member (any account status: a suspended member's base role
        is none and so are its permissions, which is what they hold), each role as id, name and
        permissions. 404 `not_member` for anyone without a membership row."""
        self.organization(oid)
        if not self.store.one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, uid)):
            raise ServiceError(404, "not_member", "该账户不是这个组织的成员")
        eff = OrgRoles(self.store).effective(oid, uid)
        return {"base_role": eff["base_role"],
                "roles": [{"id": r["id"], "name": r["name"], "permissions": r["permissions"]} for r in eff["roles"]],
                "permissions": eff["permissions"], "sources": eff["sources"]}


def build_router(store: Store, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin, after the ownership
    (A37) and lifecycle (A35) routers. Only GETs: none shares a method with their POSTs."""
    r = APIRouter(tags=["admin"])
    reader = OrganizationDetail(store)

    def _admin(request: Request) -> tuple[dict, bool]:
        """admin_routes `_admin` for `platform.orgs.view` (platform_access.require_platform), and
        whether the caller also reads accounts (and so is shown addresses)."""
        actor = require_platform(request, VIEW, store=store, audit=audit)
        return actor, ACCOUNTS_VIEW in actor["platform_permissions"]

    @r.get("/organizations/{oid}")
    def organization_detail(request: Request, oid: str):
        """The organization, its settings and counts, its first owners, admins and workspaces, all
        its custom roles, its newest open imports and copies, and whether the caller is a member.
        404 `no_organization`; a deleted organization is returned (`status: deleted`, `recover_until`)."""
        actor, accounts = _admin(request)
        lang = request.query_params.get("lang")
        locale = lang if lang in ("zh-Hans", "zh-Hant", "en") else (
            (store.one("SELECT locale FROM users WHERE id=?", (actor["id"],)) or {}).get("locale") or "zh-Hans")
        with reader.read_snapshot():
            return reader.detail(oid, actor["id"], accounts=accounts, locale=locale)

    @r.get("/organizations/{oid}/workspaces")
    def organization_workspaces(request: Request, oid: str, offset: int = Query(0, ge=0, le=2**63 - 1),
                                limit: int = Query(WORKSPACE_CAP, ge=1, le=200)):
        """`{items, total, offset, limit}`: the undeleted workspaces by creation, with member counts."""
        _admin(request)
        with reader.read_snapshot():
            reader.organization(oid)
            items = reader.workspace_page(oid, offset=offset, limit=limit)
            total = reader.workspace_counts(oid)["workspaces"]
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    @r.get("/organizations/{oid}/members")
    def organization_members(request: Request, oid: str, q: str = Query(""), role: str = Query(""),
                             offset: int = Query(0, le=2**63 - 1), limit: int = Query(50)):
        """`{items, total, offset, limit, active_owner_count}` — the tenant's member list without
        the reader's own relation to it (`is_self`, `manageable`, `caller`). `q`, `role`, `offset`
        and `limit` are checked by core, as the tenant's: 400 `bad_role`, 400 `invalid_field`."""
        _, accounts = _admin(request)
        with reader.read_snapshot():
            return reader.members(oid, accounts=accounts, q=q, role=role, offset=offset, limit=limit)

    @r.get("/organizations/{oid}/members/{uid}/permissions")
    def organization_member_permissions(request: Request, oid: str, uid: str):
        """What a member may do in the organization and where each permission comes from:
        `{base_role, roles: [{id, name, permissions}], permissions, sources}`."""
        _admin(request)
        with reader.read_snapshot():
            return reader.permissions(oid, uid)

    return r
