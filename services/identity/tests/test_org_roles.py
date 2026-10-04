"""Organization custom roles: named permission sets that never exceed the delegator.

Real store, real routes, real sessions (cookie-secure off so the test client keeps them). The
owner and a delegated "roles manager" are separate accounts; escalation attempts are the point.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core.org_roles import BASE, CATALOGUE, DELEGABLE, OrgRoles

WHY = "fixture: reason recorded with a role change"   # role writes need 8+ characters (D12)

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("delegate", "Dee Delegate"), ("member", "Mia Member"), ("other", "Oz Other")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    orgs = Organizations(ident.store, ident)
    created = orgs.create(users["owner"]["id"], "Roles Org", "Roles Team", "fixture-idem-roles-org-0001")
    oid = created["organization"]["id"]
    for key in ("delegate", "member", "other"):
        ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (oid, users[key]["id"], users["owner"]["id"]))
    with TestClient(api) as c:
        c.api, c.ident, c.users, c.oid = api, ident, users, oid
        c.wid = created["workspace"]["id"]
        yield c


def login(c: TestClient, key: str) -> dict:
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW})
    assert r.status_code == 200, r.text
    h = {CSRF: c.cookies.get("dw_csrf")}
    # role writes are sensitive: the strict re-authentication the organization routes demand
    assert c.post("/api/auth/reauth", json={"password": PW, "scope": "admin"}, headers=h).status_code == 200
    return h


def base(c, oid):
    return f"/api/auth/organizations/{oid}"


# ---------------------------------------------------------------- the catalogue and the base roles
def test_catalogue_and_base_sets_are_consistent():
    assert set(BASE["owner"]) == set(CATALOGUE) and BASE["member"] == frozenset()
    assert BASE["admin"] <= DELEGABLE and "org.roles.manage" not in BASE["admin"], "an admin does not manage roles unless delegated"
    assert {"org.ownership.transfer", "org.lifecycle.manage"} == set(CATALOGUE) - DELEGABLE


def test_owner_creates_a_role_assigns_it_and_the_member_gains_exactly_those_permissions(org):
    c = org
    h = login(c, "owner")
    r = c.get(f"{base(c, c.oid)}/permissions?lang=en")
    assert r.status_code == 200 and r.json()["base_role"] == "owner" and any(p["label"] == "Manage custom roles" for p in r.json()["catalogue"])
    r = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Workspace builder", "description": "Creates spaces",
                                                          "permissions": ["org.workspaces.create", "org.members.view"], "reason": WHY})
    assert r.status_code == 201, r.text
    rid = r.json()["role"]["id"]
    # the member cannot create a workspace yet
    hm = login(c, "member")
    r = c.post(f"{base(c, c.oid)}/workspaces", headers=hm, json={"name": "Nope"})
    assert r.status_code == 403
    assert c.get(f"{base(c, c.oid)}/members").status_code == 403, "members cannot list members by default"
    h = login(c, "owner")
    r = c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=h, json={"role_ids": [rid], "reason": WHY})
    assert r.status_code == 200 and r.json()["added"] == [rid], r.text
    eff = c.get(f"{base(c, c.oid)}/members/{c.users['member']['id']}/permissions").json()
    assert eff["base_role"] == "member" and eff["permissions"] == ["org.members.view", "org.workspaces.create"]
    assert eff["sources"]["org.workspaces.create"] == [f"role:{rid}"]
    hm = login(c, "member")
    r = c.post(f"{base(c, c.oid)}/workspaces", headers=hm, json={"name": "Built by role"})
    assert r.status_code == 201, r.text
    assert c.get(f"{base(c, c.oid)}/members").status_code == 200
    # but still not settings, not roles, not member management
    assert c.patch(f"{base(c, c.oid)}", headers=hm, json={"name": "Renamed", "reason": "trying my luck here"}).status_code == 403
    assert c.post(f"{base(c, c.oid)}/roles", headers=hm, json={"name": "x", "permissions": ["org.members.view"], "reason": WHY}).status_code == 403
    # the member was told
    n = c.api.state.notifier
    kinds = [x["kind"] for x in n.list(c.users["member"]["id"])["items"]]
    assert "organization.roles_assigned" in kinds


def test_a_delegated_roles_manager_cannot_exceed_what_they_hold(org):
    c = org
    h = login(c, "owner")
    r = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Roles manager", "permissions": ["org.roles.manage", "org.members.view", "org.members.invite"], "reason": WHY})
    manager_role = r.json()["role"]["id"]
    assert c.put(f"{base(c, c.oid)}/members/{c.users['delegate']['id']}/roles", headers=h, json={"role_ids": [manager_role], "reason": WHY}).status_code == 200
    hd = login(c, "delegate")
    # can create a role within their own permissions
    r = c.post(f"{base(c, c.oid)}/roles", headers=hd, json={"name": "Inviter", "permissions": ["org.members.invite"], "reason": WHY})
    assert r.status_code == 201, r.text
    inviter = r.json()["role"]["id"]
    # cannot mint permissions they lack
    r = c.post(f"{base(c, c.oid)}/roles", headers=hd, json={"name": "Escalate", "permissions": ["org.settings.manage"], "reason": WHY})
    assert r.status_code == 403 and r.json()["error"] == "permission_not_held" and r.json()["detail"]["permissions"] == ["org.settings.manage"]
    # cannot put an owner-only permission into any role, even an owner cannot
    r = c.post(f"{base(c, c.oid)}/roles", headers=login(c, "owner"), json={"name": "Owner-ish", "permissions": ["org.ownership.transfer"], "reason": WHY})
    assert r.status_code == 400 and r.json()["error"] == "owner_only_permission"
    hd = login(c, "delegate")
    # cannot edit a role that carries more than they hold (the owner's "Roles manager" includes roles.manage — which they do hold —
    # so make one they don't hold)
    h = login(c, "owner")
    r = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Settings", "permissions": ["org.settings.manage"], "reason": WHY})
    settings_role = r.json()["role"]["id"]
    hd = login(c, "delegate")
    r = c.patch(f"{base(c, c.oid)}/roles/{settings_role}", headers=hd, json={"name": "Settings renamed", "reason": WHY})
    assert r.status_code == 403 and r.json()["error"] == "permission_not_held"
    # cannot assign it either; can assign the one they made
    r = c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=hd, json={"role_ids": [settings_role], "reason": WHY})
    assert r.status_code == 403
    r = c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=hd, json={"role_ids": [inviter], "reason": WHY})
    assert r.status_code == 200 and r.json()["added"] == [inviter]
    # the inviter can now invite into the organization's workspace through the ordinary invitation service
    hm = login(c, "member")
    from synwevo_core.organizations import require_invitation_permission
    require_invitation_permission(c.ident.store, c.wid, c.users["member"]["id"])          # no raise
    with pytest.raises(Exception):
        require_invitation_permission(c.ident.store, c.wid, c.users["other"]["id"])
    # an "owners only" invitation policy overrides any custom role
    c.ident.store.execute("UPDATE organizations SET invitation_policy='owners' WHERE id=?", (c.oid,))
    with pytest.raises(Exception):
        require_invitation_permission(c.ident.store, c.wid, c.users["member"]["id"])


def test_deleting_an_assigned_role_needs_an_explicit_unassign_and_removal_drops_roles(org):
    c = org
    h = login(c, "owner")
    rid = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Temp", "permissions": ["org.members.view"], "reason": WHY}).json()["role"]["id"]
    c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=h, json={"role_ids": [rid], "reason": WHY})
    r = c.request("DELETE", f"{base(c, c.oid)}/roles/{rid}", headers=h, json={"reason": WHY})
    assert r.status_code == 409 and r.json()["error"] == "role_assigned" and r.json()["detail"]["assigned_count"] == 1
    assert c.get(f"{base(c, c.oid)}/roles").json()["items"][0]["assigned_count"] == 1
    # duplicate names and unknown permissions are refused; version conflicts are visible
    assert c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Temp", "permissions": ["org.members.view"], "reason": WHY}).status_code == 409
    assert c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Bad", "permissions": ["org.nope"], "reason": WHY}).json()["error"] == "unknown_permission"
    assert c.patch(f"{base(c, c.oid)}/roles/{rid}", headers=h, json={"name": "Temp 2", "version": 99, "reason": WHY}).json()["error"] == "version_conflict"
    r = c.request("DELETE", f"{base(c, c.oid)}/roles/{rid}", headers=h, json={"unassign": True, "reason": WHY})
    assert r.status_code == 200 and r.json()["unassigned"] == 1
    assert OrgRoles(c.ident.store).assigned(c.oid, c.users["member"]["id"]) == []
    # a removed member loses their custom roles with the membership
    rid2 = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Viewer", "permissions": ["org.members.view"], "reason": WHY}).json()["role"]["id"]
    c.put(f"{base(c, c.oid)}/members/{c.users['other']['id']}/roles", headers=h, json={"role_ids": [rid2], "reason": WHY})
    r = c.request("DELETE", f"{base(c, c.oid)}/members/{c.users['other']['id']}", headers=h, json={"reason": "leaving the fixture organization"})
    assert r.status_code == 200, r.text
    assert OrgRoles(c.ident.store).assigned(c.oid, c.users["other"]["id"]) == []
    assert OrgRoles(c.ident.store).permissions_of(c.oid, c.users["other"]["id"]) == frozenset()


def test_the_admin_base_role_still_works_as_before(org):
    c = org
    c.ident.store.execute("UPDATE organization_memberships SET role='admin' WHERE organization_id=? AND user_id=?", (c.oid, c.users["delegate"]["id"]))
    hd = login(c, "delegate")
    assert c.get(f"{base(c, c.oid)}/members").status_code == 200
    assert c.post(f"{base(c, c.oid)}/workspaces", headers=hd, json={"name": "Admin made"}).status_code == 201
    assert c.post(f"{base(c, c.oid)}/roles", headers=hd, json={"name": "x", "permissions": ["org.members.view"], "reason": WHY}).status_code == 403, \
        "an admin does not manage roles until an owner delegates it"


def test_copying_a_role_follows_the_rules_for_creating_one(org):
    """CI-94: a copy has the source's description and permissions, is assigned to nobody, takes a
    numbered name when none is given, and needs every permission in it — a delegated manager
    cannot copy a role above their reach, and the copy counts toward the cap and name uniqueness."""
    c = org
    h = login(c, "owner")
    src = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Reviewer", "description": "Reads members",
                                                                "permissions": ["org.members.view", "org.members.invite"], "reason": WHY}).json()["role"]
    c.put(f"{base(c, c.oid)}/members/{c.users['member']['id']}/roles", headers=h, json={"role_ids": [src["id"]], "reason": WHY})
    r = c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=h, json={"reason": WHY})
    assert r.status_code == 201, r.text
    copy = r.json()["role"]
    assert copy["id"] != src["id"] and copy["name"] == "Reviewer (2)" and copy["description"] == "Reads members"
    assert copy["permissions"] == src["permissions"] and copy["assigned_count"] == 0 and copy["version"] == 1
    assert c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=h, json={"reason": WHY}).json()["role"]["name"] == "Reviewer (3)"
    named = c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=h, json={"name": "Reviewer for Finance", "reason": WHY}).json()["role"]
    assert named["name"] == "Reviewer for Finance"
    assert c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=h, json={"name": "Reviewer", "reason": WHY}).json()["error"] == "role_name_taken"
    assert c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=h, json={"name": "x", "permissions": ["org.settings.manage"], "reason": WHY}).status_code == 400
    assert c.post(f"{base(c, c.oid)}/roles/nope/copy", headers=h, json={"reason": WHY}).status_code == 404
    # the source's members are untouched; the copies are assigned to nobody
    eff = c.get(f"{base(c, c.oid)}/members/{c.users['member']['id']}/permissions", headers=h).json()
    assert [r["id"] for r in eff["roles"]] == [src["id"]]
    # a delegated roles manager who lacks one of the source's permissions cannot copy it
    settings = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Settings", "permissions": ["org.settings.manage"], "reason": WHY}).json()["role"]
    mgr = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Roles manager", "permissions": ["org.roles.manage", "org.members.view"], "reason": WHY}).json()["role"]
    c.put(f"{base(c, c.oid)}/members/{c.users['delegate']['id']}/roles", headers=h, json={"role_ids": [mgr["id"]], "reason": WHY})
    hd = login(c, "delegate")
    r = c.post(f"{base(c, c.oid)}/roles/{settings['id']}/copy", headers=hd, json={"reason": WHY})
    assert r.status_code == 403 and r.json()["error"] == "permission_not_held"
    assert c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=hd, json={"reason": WHY}).json()["error"] == "permission_not_held", "invite is not held"
    ok = c.post(f"{base(c, c.oid)}/roles/{mgr['id']}/copy", headers=hd, json={"name": "Roles manager B", "reason": WHY})
    assert ok.status_code == 201
    # an ordinary member cannot copy at all
    assert c.post(f"{base(c, c.oid)}/roles/{src['id']}/copy", headers=login(c, "member"), json={"reason": WHY}).status_code == 403


# ---------------------------------------------------------------- D12: reasons and taking roles away
def _role_audit(c):
    return c.ident.store.all("SELECT action, meta FROM audit_events WHERE action LIKE 'organization.role.%' ORDER BY at")


def test_every_role_write_needs_a_reason_and_records_it(org):
    """Admin defect D12: the dialog demanded a reason and said it went into the audit record; the
    five role routes refused a `reason` field and the record carried none."""
    c = org
    h = login(c, "owner")
    src = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Viewer", "permissions": ["org.members.view"], "reason": WHY}).json()["role"]
    member = c.users["member"]["id"]
    writes = [("POST", f"{base(c, c.oid)}/roles", {"name": "Reader", "permissions": ["org.members.view"]}, 201),
              ("PATCH", f"{base(c, c.oid)}/roles/{src['id']}", {"name": "Viewer 2", "version": src["version"]}, 200),
              ("POST", f"{base(c, c.oid)}/roles/{src['id']}/copy", {"name": "Viewer copy"}, 201),
              ("PUT", f"{base(c, c.oid)}/members/{member}/roles", {"role_ids": [src["id"]]}, 200),
              ("DELETE", f"{base(c, c.oid)}/roles/{src['id']}", {"unassign": True}, 200)]
    for method, url, body, ok in writes:
        before = (c.ident.store.one("SELECT COUNT(*) n FROM organization_roles")["n"], len(_role_audit(c)),
                  c.ident.store.one("SELECT COUNT(*) n FROM organization_role_assignments")["n"])
        for reason in (None, "  7chars", ):
            r = c.request(method, url, headers=h, json={**body, **({"reason": reason} if reason else {})})
            assert r.status_code == 400 and r.json()["error"] == "missing_reason", (method, url, r.text)
        assert (c.ident.store.one("SELECT COUNT(*) n FROM organization_roles")["n"], len(_role_audit(c)),
                c.ident.store.one("SELECT COUNT(*) n FROM organization_role_assignments")["n"]) == before, "a refused write changes nothing"
        r = c.request(method, url, headers=h, json={**body, "reason": "  因为团队调整需要这个角色  "})
        assert r.status_code == ok, (method, url, r.text)
        assert json.loads(_role_audit(c)[-1]["meta"])["reason"] == "因为团队调整需要这个角色"
        assert c.request(method, url, headers=h, json={**body, "reason": WHY, "extra": 1}).json()["error"] == "invalid_field"
    shown = c.get(f"{base(c, c.oid)}/audit?category=roles").json()["items"]
    assert sum(1 for item in shown if "因为团队调整需要这个角色" in json.dumps(item, ensure_ascii=False)) >= 5


def test_a_delegate_cannot_take_away_a_role_above_their_reach_but_can_add_beside_it(org):
    """Admin defect D12: assignment checked only the roles being given, so a delegate holding
    org.roles.manage could strip a member of the owner's own delegation (here org.settings.manage)."""
    c = org
    h = login(c, "owner")
    settings = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Settings", "permissions": ["org.settings.manage"], "reason": WHY}).json()["role"]
    mgr = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Roles manager", "permissions": ["org.roles.manage", "org.members.view"], "reason": WHY}).json()["role"]
    own = c.post(f"{base(c, c.oid)}/roles", headers=h, json={"name": "Viewer", "permissions": ["org.members.view"], "reason": WHY}).json()["role"]
    member = c.users["member"]["id"]
    assert c.put(f"{base(c, c.oid)}/members/{c.users['delegate']['id']}/roles", headers=h, json={"role_ids": [mgr["id"]], "reason": WHY}).status_code == 200
    assert c.put(f"{base(c, c.oid)}/members/{member}/roles", headers=h, json={"role_ids": [settings["id"]], "reason": WHY}).status_code == 200
    inbox_before = len(c.api.state.notifier.list(member)["items"])
    assigns_before = len([a for a in _role_audit(c) if a["action"] == "organization.role.assign"])
    hd = login(c, "delegate")
    for role_ids in ([], [own["id"]]):
        r = c.put(f"{base(c, c.oid)}/members/{member}/roles", headers=hd, json={"role_ids": role_ids, "reason": WHY})
        assert r.status_code == 403 and r.json()["error"] == "permission_not_held", r.text
        assert r.json()["detail"]["permissions"] == ["org.settings.manage"]
    assert [x["id"] for x in OrgRoles(c.ident.store).assigned(c.oid, member)] == [settings["id"]]
    assert len([a for a in _role_audit(c) if a["action"] == "organization.role.assign"]) == assigns_before
    assert len(c.api.state.notifier.list(member)["items"]) == inbox_before
    # adding a role they hold beside the one above their reach is fine: the untouched one needs nothing
    r = c.put(f"{base(c, c.oid)}/members/{member}/roles", headers=hd, json={"role_ids": [settings["id"], own["id"]], "reason": WHY})
    assert r.status_code == 200 and r.json()["added"] == [own["id"]] and r.json()["removed"] == [], r.text
    # the owner, who holds everything, may take it away
    r = c.put(f"{base(c, c.oid)}/members/{member}/roles", headers=login(c, "owner"), json={"role_ids": [own["id"]], "reason": WHY})
    assert r.status_code == 200 and r.json()["removed"] == [settings["id"]]
