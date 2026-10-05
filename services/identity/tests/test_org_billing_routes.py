"""An organization's payment settings for its members, on the real accounts application.

Fixture accounts, fixture CNY unit, isolated temporary data directory; no payment or model.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core.org_roles import OrgRoles
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import now

PW = "org-billing-fixture-passphrase-12"


@pytest.fixture
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "disabled", "PUBLIC_URL": "http://testserver", "BILLING_ENVIRONMENT": "test",
                       "BILLING_WALLET_UNITS": json.dumps([{"code": "CNY", "scale": 6}]), "TEST_INSTANCE": "1",
                       "SECRET": "synthetic-org-billing-fixture-key-123456789"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store, ident = api.state.identity.store, api.state.identity
    people = {}
    for key in ("owner", "admin", "member", "outsider"):
        u = ident.create_user(f"{key}@example.test", PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        people[key] = u["id"]
    created = Organizations(store, ident).create(people["owner"], "Fixture Org", "Fixture WS", "fixture-org-billing")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    for key, role in (("admin", "admin"), ("member", "member")):
        store.insert("organization_memberships", {"organization_id": oid, "user_id": people[key], "role": role,
                                                  "created_at": now(), "updated_at": now()})
    clients = []

    def login(key):
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW}).status_code == 200
        clients.append(c)
        return c

    yield {"api": api, "store": store, "people": people, "oid": oid, "wid": wid, "login": login}
    for c in clients:
        c.close()


def put(c, path, body):
    return c.put(path, json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def test_only_a_billing_manager_sets_who_may_charge_the_organization(world):
    base = f"/api/auth/organizations/{world['oid']}/billing"
    for key in ("admin", "member", "outsider"):
        r = world["login"](key).get(base)
        assert r.status_code == 403 and r.json()["error"] == "org_billing_forbidden", key
    owner = world["login"]("owner")
    view = owner.get(base).json()
    assert view["currency"] == "CNY" and view["wallet"] == {"booked": "0", "held": "0", "available": "0", "frozen": False}
    member = next(m for m in view["members"] if m["user_id"] == world["people"]["member"])
    assert member["enabled"] is False and member["revision"] == 0
    r = put(owner, f"{base}/members/{world['people']['member']}",
            {"enabled": True, "monthly_limit_minor": 5000, "expected_revision": 0})
    assert r.status_code == 200, r.text
    member = next(m for m in r.json()["members"] if m["user_id"] == world["people"]["member"])
    assert member["enabled"] and member["monthly_limit"] == str(5000 * 10**4) and member["revision"] == 1
    # a stale revision does not silently overwrite someone else's change
    r = put(owner, f"{base}/members/{world['people']['member']}",
            {"enabled": False, "monthly_limit_minor": 0, "expected_revision": 0})
    assert r.status_code == 409 and r.json()["error"] == "org_billing_conflict"
    # nobody outside the organization can be granted
    r = put(owner, f"{base}/members/{world['people']['outsider']}",
            {"enabled": True, "monthly_limit_minor": 100, "expected_revision": 0})
    assert r.status_code == 409 and r.json()["error"] == "org_billing_not_member"
    audit = world["store"].one("SELECT meta FROM audit_events WHERE action='org.billing.member_grant'")
    assert json.loads(audit["meta"])["monthly_limit_minor"] == 5000


def test_a_custom_role_can_delegate_billing_and_no_workspace_limit_is_offered(world):
    """A per-workspace monthly limit binds nothing until organization-paid Agent runs are attributed
    to a workspace (the ledger applies one only to a call that carries its workspace, and no run
    does). So none is offered: no route sets one, the view lists none, nothing is stored."""
    roles, store = OrgRoles(world["store"]), world["store"]
    role = roles.create(world["oid"], world["people"]["owner"], name="Treasurer", permissions=["org.billing.manage"])
    roles.assign(world["oid"], world["people"]["owner"], world["people"]["admin"], [role["id"]])
    admin = world["login"]("admin")
    base = f"/api/auth/organizations/{world['oid']}/billing"
    view = admin.get(base)
    assert view.status_code == 200 and "workspaces" not in view.json()
    r = put(admin, f"{base}/members/{world['people']['member']}",
            {"enabled": True, "monthly_limit_minor": 100, "expected_revision": 0})
    assert r.status_code == 200 and "workspaces" not in r.json()
    r = put(admin, f"{base}/workspaces/{world['wid']}", {"monthly_limit_minor": 2000, "expected_revision": 0})
    assert r.status_code in (404, 405), r.text
    assert store.one("SELECT COUNT(*) AS n FROM billing_org_workspace_limits")["n"] == 0
    statement = admin.get(f"{base}/statement").json()
    assert statement["items"] == [] and statement["unit"] == {"code": "CNY", "scale": 6}
