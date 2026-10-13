"""A34: the platform's read-only organization detail, through the real account service.

Isolated data directory and file mail; no worker runs. The organization, its members, workspaces,
custom roles and lifecycle go through core services; where a state cannot be reached by a route
within a test (a suspended co-owner, a member import with planned rows, a project copy — the Docs
application owns copy requests — and the bulk rows of the cap tests) the rows are written by hand,
and each such write is marked FIXTURE. The canary strings in them stand for content the platform
must never be shown: a project's name, a copy's manifest, an imported address, an import's file name.

Held: a platform administrator who is no member reads the detail, the workspaces, the members and a
member's effective permissions — and still is no member: the tenant route answers 404, no
membership, entitlement or session appears, Synwevo Docs stays closed to them, nothing is audited;
addresses only for a reader of accounts, and then the member search does not match them either;
caps with totals and a statement count that does not grow with the rows; a deleted organization is
returned with its recovery deadline; the member page is the tenant's own page; the effective
permissions are `OrgRoles.effective`; the reads take no write lock; and the organization list
counts by grouped queries, with `%` and `_` searched as literal text.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.member_import import MemberImports
from synwevo_core.org_roles import OrgRoles, catalogue
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now

from .platform_fixture import make_platform_admin

PW = "isolated-organization-detail-fixture-34"
REASON = "Isolated fixture: organization detail verification"
BASE = "/api/admin/organizations"
CANARIES = ("CANARY-PROJECT", "CANARY-MANIFEST", "canary.row", "CANARY-ROSTER")
DETAIL_KEYS = {"organization", "settings", "counts", "owners", "admins", "owners_total", "admins_total",
               "workspaces", "custom_roles", "pending", "viewer", "permission_catalogue"}
PERSON_KEYS = {"user_id", "display_name", "email", "account_status", "joined_at", "usable"}
WORKSPACE_KEYS = {"id", "name", "kind", "status", "created_at", "members"}
#: What the platform keeps of the tenant's member rows, and what the tenant adds for its reader.
TENANT_ONLY = ("is_self", "manageable")


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "data"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "data"), start_jobs=False)
    store = Store(tmp_path / "data")
    ident = Identity(store)
    clients: list[TestClient] = []

    def verified(email, **kw):
        user = ident.create_user(email, PW, status="active", **kw)
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        return ident.get_user(user["id"])

    def login(user):
        client = TestClient(api, raise_server_exceptions=False)
        clients.append(client)
        r = post(client, "/api/auth/login", {"email": user["email"], "password": PW})
        assert r.status_code == 200, r.text
        return client

    root = make_platform_admin(store, verified("root@example.test", display_name="Root"))
    owner = verified("olive@example.test", display_name="Olive Owner")
    co_owner = verified("cody@example.test", display_name="Cody Coowner")
    ada = verified("ada@example.test", display_name="Ada Admin")
    mo = verified("mo@example.test", display_name="Mo Member")
    pat = verified("pat.secret@example.org", display_name="Pat Person")
    orgs = Organizations(store, ident)
    created = orgs.create(owner["id"], "Atlas fixture organization", "Atlas fixture workspace", "fixture-org-a34")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    for user, role in ((co_owner, "owner"), (ada, "admin"), (mo, "member"), (pat, "member")):
        # FIXTURE: memberships as an accepted invitation (or a promotion) leaves them.
        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) "
                      "VALUES (?,?,?,?,?,?)", (oid, user["id"], role, now(), now(), owner["id"]))
    ident.add_member(wid, ada["id"], "admin", actor_id=owner["id"])
    # FIXTURE: the co-owner's account is suspended — an owner row that cannot act.
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (co_owner["id"],))
    roles = OrgRoles(store)
    builder = roles.create(oid, owner["id"], name="Workspace builder",
                           permissions=["org.workspaces.create", "org.members.view"])
    roles.assign(oid, owner["id"], mo["id"], [builder["id"]])
    roles.create(oid, owner["id"], name="Auditors", permissions=["org.audit.read"])
    # An import in progress (core create), and one finished, which is not pending.
    imports = MemberImports(store, ident)
    open_import = imports.create(oid, owner["id"], filename="CANARY-ROSTER.csv", sha256="0" * 64, size=10,
                                 extraction={"format": "csv", "family": "table"}, scan={}, workspace_id=None)
    done_import = imports.create(oid, owner["id"], filename="CANARY-ROSTER-done.csv", sha256="1" * 64, size=10,
                                 extraction={"format": "csv", "family": "table"}, scan={}, workspace_id=None)
    for seq in range(2):
        # FIXTURE: planned rows, as `MemberImports.plan` writes them (addresses are row payload).
        store.insert("member_import_rows", {"id": new_id("mir"), "import_id": open_import["id"], "seq": seq,
                                            "email_key": f"canary.row{seq}@example.org", "action": "invite",
                                            "status": "planned", "updated_at": now()})
    store.execute("UPDATE member_imports SET status='planned' WHERE id=?", (open_import["id"],))   # FIXTURE
    store.execute("UPDATE member_imports SET status='finished' WHERE id=?", (done_import["id"],))  # FIXTURE
    personal = ident.workspaces_for(mo["id"])[0]["id"]

    def copy(status, *, created_at, approval_expires_at=None):
        # FIXTURE: a copy request as Synwevo Docs files it (`ProjectCopies.create`), its name and
        # manifest the content the platform must not see.
        cid = new_id("pc")
        store.insert("project_copies", {
            "id": cid, "source_workspace_id": personal, "source_project_id": "p_canary", "requester_id": mo["id"],
            "target_organization_id": oid, "target_workspace_id": wid, "name": "CANARY-PROJECT copy",
            "purpose": None, "scope": "{}", "manifest": '{"files": ["CANARY-MANIFEST.docx"]}', "digest": "d",
            "status": status, "approval_expires_at": approval_expires_at, "created_at": created_at,
            "updated_at": created_at, "expires_at": created_at + 7 * 86400})
        return cid

    t0 = now()
    copies = {"pending": copy("pending", created_at=t0 - 30), "approved": copy("approved", created_at=t0 - 20,
                                                                               approval_expires_at=t0 + 3600),
              "done": copy("done", created_at=t0 - 10)}
    admin = login(root)
    yield {"api": api, "store": store, "ident": ident, "root": root, "owner": owner, "co_owner": co_owner,
           "ada": ada, "mo": mo, "pat": pat, "oid": oid, "wid": wid, "admin": admin, "login": login,
           "verified": verified, "builder": builder, "open_import": open_import, "copies": copies, "t0": t0}
    for client in clients:
        client.close()


def get(w, path, client=None, **params):
    return (client or w["admin"]).get(path, params=params)


def ok(w, path, client=None, **params):
    r = get(w, path, client, **params)
    assert r.status_code == 200, r.text
    return r.json()


def table_counts(store):
    return {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("organization_memberships", "memberships", "app_entitlements", "sessions", "audit_events",
                      "oauth_tokens")}


def without_accounts(monkeypatch):
    from synwevo_core.platform_access import PlatformAccess
    original = PlatformAccess.effective_permissions

    def reduced(self, user):
        # FIXTURE: a role that reads organizations but not accounts (no built-in role is one).
        return frozenset(original(self, user)) - {"platform.users.view"}

    monkeypatch.setattr(PlatformAccess, "effective_permissions", reduced)


# ---------------------------------------------------------------- the detail
def test_a_non_member_administrator_reads_the_detail_and_gains_nothing(world):
    from synwevo_core.apps import Apps
    w = world
    store, oid = w["store"], w["oid"]
    before = table_counts(store)
    r = get(w, f"{BASE}/{oid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == DETAIL_KEYS
    assert set(d["organization"]) == {"id", "name", "status", "created_at", "deleted_at", "recover_until",
                                      "previous_status", "version", "has_avatar"}
    assert d["organization"]["name"] == "Atlas fixture organization" and d["organization"]["status"] == "active"
    assert d["organization"]["recover_until"] is None and d["organization"]["has_avatar"] is False
    assert d["settings"] == {"invitation_policy": "admins", "transfer_policy": "organization"}
    assert d["counts"] == {"members": 5, "owners": 2, "admins": 1, "usable_owners": 1, "workspaces": 1,
                           "workspaces_frozen": 0, "custom_roles": 2, "pending_imports": 1, "pending_copies": 2}
    assert [(p["user_id"], p["usable"], p["account_status"]) for p in d["owners"]] == [
        (w["owner"]["id"], True, "active"), (w["co_owner"]["id"], False, "suspended")]
    assert all(set(p) == PERSON_KEYS for p in d["owners"] + d["admins"])
    assert d["owners"][0]["email"] == "olive@example.test" and d["owners"][0]["display_name"] == "Olive Owner"
    assert [p["user_id"] for p in d["admins"]] == [w["ada"]["id"]] and d["owners_total"] == 2 and d["admins_total"] == 1
    assert d["workspaces"]["total"] == 1 and [set(x) for x in d["workspaces"]["items"]] == [WORKSPACE_KEYS]
    assert d["workspaces"]["items"][0]["id"] == w["wid"] and d["workspaces"]["items"][0]["members"] == 2
    assert d["custom_roles"] == [
        {"id": r["id"], "name": r["name"], "permissions": r["permissions"], "assigned": r["assigned_count"]}
        for r in OrgRoles(store).list(oid)]
    assert {r["name"]: r["assigned"] for r in d["custom_roles"]} == {"Auditors": 0, "Workspace builder": 1}
    assert d["pending"]["imports"] == [{"id": w["open_import"]["id"], "state": "planned", "rows": 2,
                                        "created_at": w["open_import"]["created_at"],
                                        "created_by": {"user_id": w["owner"]["id"], "display_name": "Olive Owner"}}]
    copies = d["pending"]["copies"]
    assert [c["id"] for c in copies] == [w["copies"]["approved"], w["copies"]["pending"]], "newest first, open only"
    assert set(copies[0]) == {"id", "status", "source_workspace_id", "target_workspace_id", "created_at", "expires_at",
                              "requested_by"}
    assert copies[0]["status"] == "approved" and copies[0]["expires_at"] == pytest.approx(w["t0"] + 3600)
    assert copies[1]["status"] == "pending" and copies[1]["expires_at"] == pytest.approx(w["t0"] - 30 + 7 * 86400)
    assert copies[0]["requested_by"] == {"user_id": w["mo"]["id"], "display_name": "Mo Member"}
    assert copies[0]["target_workspace_id"] == w["wid"]
    assert d["viewer"] == {"is_member": False, "role": None}
    for canary in CANARIES:
        assert canary not in r.text, canary
    # The other reads, then: the administrator is still no member and holds nothing new.
    assert get(w, f"{BASE}/{oid}/workspaces").status_code == 200
    assert get(w, f"{BASE}/{oid}/members").status_code == 200
    assert get(w, f"{BASE}/{oid}/members/{w['mo']['id']}/permissions").status_code == 200
    assert table_counts(store) == before, "a read creates no membership, entitlement, session, token or audit row"
    assert w["admin"].get(f"/api/auth/organizations/{oid}").status_code == 404
    assert w["admin"].get(f"/api/auth/organizations/{oid}/members").status_code == 404
    assert not store.one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?",
                         (oid, w["root"]["id"]))
    assert w["ident"].membership(w["wid"], w["root"]["id"]) is None
    assert not Apps(store, w["ident"]).check_access(w["root"]["id"], "docweave", w["wid"])[0]


def test_a_member_administrator_is_told_so(world):
    w = world
    # FIXTURE: the administrator also belongs to the organization, as an admin.
    w["store"].execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at) "
                       "VALUES (?,?,'admin',?,?)", (w["oid"], w["root"]["id"], now(), now()))
    assert ok(w, f"{BASE}/{w['oid']}")["viewer"] == {"is_member": True, "role": "admin"}


def test_the_detail_labels_permissions_in_the_readers_language(world):
    """The tenant roles page's catalogue, which only members reach, comes with the detail: by
    `?lang=`, else the administrator's own locale, else Simplified Chinese."""
    w = world
    assert ok(w, f"{BASE}/{w['oid']}", lang="en")["permission_catalogue"] == catalogue("en")
    assert ok(w, f"{BASE}/{w['oid']}", lang="zh-Hant")["permission_catalogue"] == catalogue("zh-Hant")
    assert ok(w, f"{BASE}/{w['oid']}", lang="xx")["permission_catalogue"] == catalogue("zh-Hans")
    w["store"].execute("UPDATE users SET locale='en' WHERE id=?", (w["root"]["id"],))
    assert ok(w, f"{BASE}/{w['oid']}")["permission_catalogue"] == catalogue("en")
    ids = {p["id"] for p in catalogue("en")}
    for role in ok(w, f"{BASE}/{w['oid']}")["custom_roles"]:
        assert set(role["permissions"]) <= ids


def test_a_deleted_organization_is_returned_with_its_recovery_deadline_and_an_unknown_one_is_404(world):
    w = world
    Organizations(w["store"], w["ident"]).transition(w["oid"], w["owner"]["id"], "delete", reason=REASON)
    d = ok(w, f"{BASE}/{w['oid']}")
    org = d["organization"]
    assert org["status"] == "deleted" and org["previous_status"] == "active"
    assert org["deleted_at"] and org["recover_until"] == pytest.approx(org["deleted_at"] + 7 * 24 * 3600)
    for path in ("", "/workspaces", "/members", f"/members/{w['mo']['id']}/permissions"):
        r = get(w, f"{BASE}/org_fixture_missing{path}")
        assert r.status_code == 404 and r.json()["error"] == "no_organization", (path, r.text)


# ---------------------------------------------------------------- members and permissions
def test_the_member_page_is_the_tenants_page(world):
    w = world
    tenant = w["login"](w["owner"])
    cases = [{}, {"role": "member"}, {"role": "owner"}, {"q": "mo"}, {"q": "EXAMPLE.TEST"}, {"q": "Pat Person"},
             {"offset": 1, "limit": 2}, {"q": "%"}, {"q": "_"}]
    for params in cases:
        mine = ok(w, f"/api/auth/organizations/{w['oid']}/members", tenant, **params)
        theirs = ok(w, f"{BASE}/{w['oid']}/members", **params)
        assert set(theirs) == {"items", "total", "offset", "limit", "active_owner_count"}, params
        for key in ("total", "offset", "limit", "active_owner_count"):
            assert theirs[key] == mine[key], (params, key)
        assert [{k: v for k, v in row.items() if k not in TENANT_ONLY} for row in mine["items"]] == \
               [{k: v for k, v in row.items() if k != "usable"} for row in theirs["items"]], params
    page = ok(w, f"{BASE}/{w['oid']}/members")
    assert [row["user_id"] for row in page["items"]] == [w["owner"]["id"], w["co_owner"]["id"], w["ada"]["id"],
                                                         w["mo"]["id"], w["pat"]["id"]]
    assert {row["user_id"]: row["usable"] for row in page["items"]}[w["co_owner"]["id"]] is False
    assert all(set(row) == PERSON_KEYS | {"role", "custom_roles"} for row in page["items"])
    assert {row["user_id"]: row["custom_roles"] for row in page["items"]}[w["mo"]["id"]] == [
        {"id": w["builder"]["id"], "name": "Workspace builder"}]
    assert page["active_owner_count"] == 1
    assert ok(w, f"{BASE}/{w['oid']}/members", q="%")["total"] == 0, "% is literal"


def test_member_list_refusals_are_the_tenants(world):
    w = world
    for params, code in (({"role": "bogus"}, "bad_role"), ({"limit": 0}, "invalid_field"),
                         ({"limit": 201}, "invalid_field"), ({"offset": -1}, "invalid_field"),
                         ({"q": "x" * 101}, "invalid_field")):
        r = get(w, f"{BASE}/{w['oid']}/members", **params)
        assert r.status_code == 400 and r.json()["error"] == code, (params, r.text)
    assert get(w, f"{BASE}/{w['oid']}/workspaces", limit=201).status_code == 422
    assert get(w, f"{BASE}/{w['oid']}/workspaces", offset=-1).status_code == 422


def test_effective_permissions_are_org_roles_effective(world):
    w = world
    tenant = w["login"](w["owner"])
    roles = OrgRoles(w["store"])
    for key in ("owner", "co_owner", "ada", "mo", "pat"):
        uid = w[key]["id"]
        theirs = ok(w, f"{BASE}/{w['oid']}/members/{uid}/permissions")
        effective = roles.effective(w["oid"], uid)
        assert theirs == {"base_role": effective["base_role"], "permissions": effective["permissions"],
                          "sources": effective["sources"],
                          "roles": [{"id": r["id"], "name": r["name"], "permissions": r["permissions"]}
                                    for r in effective["roles"]]}, key
        mine = ok(w, f"/api/auth/organizations/{w['oid']}/members/{uid}/permissions", tenant)
        assert {k: mine[k] for k in ("base_role", "permissions", "sources")} == \
               {k: theirs[k] for k in ("base_role", "permissions", "sources")}, key
    mo = ok(w, f"{BASE}/{w['oid']}/members/{w['mo']['id']}/permissions")
    assert mo["base_role"] == "member" and mo["permissions"] == ["org.members.view", "org.workspaces.create"]
    assert mo["sources"]["org.workspaces.create"] == [f"role:{w['builder']['id']}"]
    assert ok(w, f"{BASE}/{w['oid']}/members/{w['co_owner']['id']}/permissions")["base_role"] is None, \
        "a suspended owner holds nothing"
    r = get(w, f"{BASE}/{w['oid']}/members/{w['root']['id']}/permissions")
    assert r.status_code == 404 and r.json()["error"] == "not_member"


def test_addresses_are_for_readers_of_accounts_and_are_not_searched_without_them(world, monkeypatch):
    w = world
    assert ok(w, f"{BASE}/{w['oid']}/members", q="pat.secret")["total"] == 1
    without_accounts(monkeypatch)
    d = ok(w, f"{BASE}/{w['oid']}")
    assert {p["email"] for p in d["owners"] + d["admins"]} == {None}
    assert d["owners"][0]["display_name"] == "Olive Owner"
    page = ok(w, f"{BASE}/{w['oid']}/members")
    assert page["total"] == 5 and {row["email"] for row in page["items"]} == {None}
    for probe in ("pat.secret", "example.org", "olive@"):
        assert ok(w, f"{BASE}/{w['oid']}/members", q=probe)["total"] == 0, probe
    assert [row["user_id"] for row in ok(w, f"{BASE}/{w['oid']}/members", q="pat person")["items"]] == [w["pat"]["id"]]
    assert "pat.secret" not in w["admin"].get(f"{BASE}/{w['oid']}/members").text


# ---------------------------------------------------------------- caps, totals, statements
def _statements(monkeypatch):
    """Every Store.one/Store.all call the next requests make (FIXTURE: a counting wrapper)."""
    calls: list[str] = []
    for name in ("one", "all"):
        original = getattr(Store, name)

        def counted(self, sql, args=(), _original=original):
            calls.append(sql)
            return _original(self, sql, args)

        monkeypatch.setattr(Store, name, counted)
    return calls


def test_an_erased_address_is_not_found_by_searching(world):
    """Deleting an account erases its address but keeps the key reserved; neither the platform's
    member search nor the tenant's may link that key back to the account."""
    from synwevo_identity.profile import finalize_deletions
    w = world
    assert ok(w, f"{BASE}/{w['oid']}/members", q="pat.secret@example.org")["total"] == 1
    # FIXTURE: Pat asked for deletion and it is due now; the finalizer erases the account.
    w["store"].execute("UPDATE users SET status='pending_delete', deletion_due_at=? WHERE id=?", (now() - 1, w["pat"]["id"]))
    finalize_deletions(w["store"])
    assert w["store"].one("SELECT email FROM users WHERE id=?", (w["pat"]["id"],))["email"] == ""
    assert ok(w, f"{BASE}/{w['oid']}/members", q="pat.secret@example.org")["total"] == 0
    assert ok(w, f"{BASE}/{w['oid']}/members", q="pat.secret")["total"] == 0
    tenant = w["login"](w["owner"])
    assert ok(w, f"/api/auth/organizations/{w['oid']}/members", tenant, q="pat.secret@example.org")["total"] == 0
    # still listed as a (deleted) member without a search
    listed = [i for i in ok(w, f"{BASE}/{w['oid']}/members")["items"] if i["user_id"] == w["pat"]["id"]]
    assert listed and listed[0]["account_status"] == "deleted" and not listed[0]["email"]


def test_a_copy_past_its_deadline_is_not_open(world):
    """Only Docs' sweep marks a copy expired; until then the read leaves out a pending request or
    an approval whose deadline has passed — neither can run any more."""
    w = world
    t0 = now()
    before = ok(w, f"{BASE}/{w['oid']}")
    open_ids = {c["id"] for c in before["pending"]["copies"]}
    assert {w["copies"]["pending"], w["copies"]["approved"]} <= open_ids
    # FIXTURE: both deadlines pass without the sweep running.
    w["store"].execute("UPDATE project_copies SET expires_at=? WHERE id=?", (t0 - 60, w["copies"]["pending"]))
    w["store"].execute("UPDATE project_copies SET approval_expires_at=? WHERE id=?", (t0 - 60, w["copies"]["approved"]))
    after = ok(w, f"{BASE}/{w['oid']}")
    assert after["counts"]["pending_copies"] == before["counts"]["pending_copies"] - 2
    assert not {w["copies"]["pending"], w["copies"]["approved"]} & {c["id"] for c in after["pending"]["copies"]}
    # the read wrote nothing: the rows keep their statuses for the sweep
    assert {r["status"] for r in w["store"].all("SELECT status FROM project_copies WHERE id IN (?,?)",
                                                 (w["copies"]["pending"], w["copies"]["approved"]))} == {"pending", "approved"}


def test_an_organizations_workspaces_are_read_by_index(world):
    w = world
    plan = " ".join(r["detail"] for r in w["store"].all(
        "EXPLAIN QUERY PLAN SELECT id FROM workspaces WHERE organization_id=? AND deleted_at IS NULL "
        "ORDER BY created_at,id LIMIT 50", (w["oid"],)))
    assert "ix_ws_org" in plan and "SCAN workspaces" not in plan, plan


def test_caps_totals_and_no_query_per_row(world, monkeypatch):
    w = world
    store, oid, owner = w["store"], w["oid"], w["owner"]["id"]
    calls = _statements(monkeypatch)
    ok(w, f"{BASE}/{oid}")
    small = len(calls)
    base_ts = now()
    for n in range(55):
        # FIXTURE: many admins (accounts without a password or personal workspace) and workspaces.
        u = w["ident"].create_user(f"bulk{n:02d}@example.test", None, status="active", display_name=f"Bulk {n:02d}",
                                   with_personal_workspace=False)
        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at) "
                      "VALUES (?,?,'admin',?,?)", (oid, u["id"], base_ts + n, base_ts + n))
    for n in range(52):
        ws = w["ident"].create_workspace(f"Bulk workspace {n:02d}", owner_id=owner, kind="org")
        store.execute("UPDATE workspaces SET organization_id=?, created_at=? WHERE id=?", (oid, base_ts + n, ws["id"]))
    for n in range(22):
        # FIXTURE: open imports beyond the detail's cap.
        store.insert("member_imports", {"id": new_id("mi"), "organization_id": oid, "created_by": owner,
                                        "filename": "CANARY-ROSTER-bulk.csv", "sha256": "2" * 64, "size": 1,
                                        "status": "extracted", "created_at": base_ts + n, "updated_at": base_ts + n,
                                        "expires_at": base_ts + 86400})
    store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (w["wid"],))  # FIXTURE: as a freeze leaves it
    gone = [x for x in w["ident"].workspaces_for(owner) if x["name"] == "Bulk workspace 51"][0]["id"]
    store.execute("UPDATE workspaces SET deleted_at=? WHERE id=?", (now(), gone))  # FIXTURE: a deleted workspace
    del calls[:]
    d = ok(w, f"{BASE}/{oid}")
    assert len(calls) == small, "the detail's statements do not grow with its rows"
    assert d["admins_total"] == 56 and len(d["admins"]) == 50 and d["counts"]["admins"] == 56
    assert [p["user_id"] for p in d["admins"]][0] == w["ada"]["id"], "by joining time"
    assert d["workspaces"]["total"] == d["counts"]["workspaces"] == 52 and len(d["workspaces"]["items"]) == 50
    assert d["counts"]["workspaces_frozen"] == 1
    assert d["counts"]["pending_imports"] == 23 and len(d["pending"]["imports"]) == 20
    page = ok(w, f"{BASE}/{oid}/workspaces", offset=50, limit=50)
    assert page["total"] == 52 and page["offset"] == 50 and page["limit"] == 50 and len(page["items"]) == 2
    everything = ok(w, f"{BASE}/{oid}/workspaces", limit=200)["items"]
    assert [x["id"] for x in everything][:50] == [x["id"] for x in d["workspaces"]["items"]]
    assert gone not in {x["id"] for x in everything}
    assert [(x["created_at"], x["id"]) for x in everything] == sorted((x["created_at"], x["id"]) for x in everything)
    members = ok(w, f"{BASE}/{oid}/members", limit=200)
    assert members["total"] == 60 and len(members["items"]) == 60
    assert ok(w, f"{BASE}/{oid}/members", role="admin", limit=10)["total"] == 56
    for canary in CANARIES:
        assert canary not in w["admin"].get(f"{BASE}/{oid}").text


def test_the_reads_take_no_write_lock(world):
    w = world
    hold, held, out = threading.Event(), threading.Event(), {}

    def writer():
        # FIXTURE: a business write in progress (BEGIN IMMEDIATE and the writer lock held).
        with w["store"].tx():
            held.set()
            hold.wait(30)

    def reader():
        out["detail"] = w["admin"].get(f"{BASE}/{w['oid']}")
        out["members"] = w["admin"].get(f"{BASE}/{w['oid']}/members")

    holder = threading.Thread(target=writer)
    holder.start()
    try:
        assert held.wait(10)
        page = threading.Thread(target=reader)
        page.start()
        page.join(10)
        finished = not page.is_alive()
    finally:
        hold.set()
        holder.join()
    page.join()
    assert finished, "the detail waited for a writer"
    assert out["detail"].status_code == 200 and out["members"].status_code == 200


# ---------------------------------------------------------------- route order beside A35 and A37
def test_the_detail_routes_sit_beside_the_lifecycle_and_recovery_routes(world):
    w = world
    assert ok(w, f"{BASE}/{w['oid']}/workspaces")["total"] == 1
    preview = post(w["admin"], f"{BASE}/{w['oid']}/preview", {"action": "freeze"})
    assert preview.status_code == 200 and preview.json()["transition"] == "freeze", preview.text
    for path in (f"{BASE}/{w['oid']}/freeze", f"{BASE}/{w['oid']}/recover-owner"):
        r = post(w["admin"], path, {"reason": REASON})
        assert r.status_code == 403 and r.json()["error"] == "reauth_required", (path, r.text)
    paths = list(w["api"].openapi()["paths"])
    assert paths.index(f"{BASE}/{{oid}}/recover-owner") < paths.index(f"{BASE}/{{oid}}/{{action}}") \
        < paths.index(f"{BASE}/{{oid}}") < paths.index(f"{BASE}/{{oid}}/members")
    assert set(w["api"].openapi()["paths"][f"{BASE}/{{oid}}/members"]) == {"get"}


# ---------------------------------------------------------------- the organization list (A34 fixes)
def test_the_list_counts_by_grouped_queries_and_searches_literally(world, monkeypatch):
    w = world
    store, ident = w["store"], w["ident"]
    orgs = Organizations(store, ident)
    named = {}
    for n, name in enumerate(("Fixture x_y org", "Fixture xzy org", "Fixture 50% org", "Fixture 50 org")):
        named[name] = orgs.create(w["pat"]["id"], name, f"{name} space", f"fixture-org-a34-like-{n}")["organization"]["id"]
    extra = ident.create_workspace("Second space", owner_id=w["pat"]["id"], kind="org")
    store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (named["Fixture xzy org"], extra["id"]))   # FIXTURE
    gone = ident.create_workspace("Deleted space", owner_id=w["pat"]["id"], kind="org")
    store.execute("UPDATE workspaces SET organization_id=?, deleted_at=? WHERE id=?",
                  (named["Fixture xzy org"], now(), gone["id"]))                                              # FIXTURE

    def names(q):
        return {o["name"] for o in ok(w, BASE, q=q, limit=200)["items"]}

    assert names("x_y") == {"Fixture x_y org"}, "_ is literal"
    assert names("50%") == {"Fixture 50% org"} and names("%") == {"Fixture 50% org"}, "% is literal"
    assert names("Fixture 50") == {"Fixture 50% org", "Fixture 50 org"}
    items = ok(w, BASE, limit=200)["items"]
    assert len(items) == 5
    for org in items:
        assert org["members"] == store.one("SELECT COUNT(*) AS n FROM organization_memberships WHERE organization_id=?",
                                           (org["id"],))["n"], org
        assert org["workspaces"] == store.one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=? "
                                              "AND deleted_at IS NULL", (org["id"],))["n"], org
    assert {o["name"]: (o["members"], o["workspaces"]) for o in items}["Fixture xzy org"] == (1, 2)
    assert {o["name"]: o["members"] for o in items}["Atlas fixture organization"] == 5
    calls = _statements(monkeypatch)
    ok(w, BASE, limit=1)
    one = len(calls)
    del calls[:]
    ok(w, BASE, limit=5)
    assert len(calls) == one, "the list's statements do not grow with its page"
