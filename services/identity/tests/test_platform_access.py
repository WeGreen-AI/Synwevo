"""Delegated platform permissions (synwevo_core.platform_access), through the account service.

Each built-in role against every `/api/admin` route and the API console; the 404 that disguises
administration from everyone without a platform permission (organization owners and workspace
administrators included); the audited, rate-limited 403 for a permission a caller lacks; what an
account must be to receive a role; no self-escalation or self-demotion; the last usable super
administrator under revocation, suspension, self-deletion and two administrators revoking each
other at once; the v26 → v27 migration and the rollback mirror; session tightening on grant;
revocation on the next request; expiry.

Isolated data directory and file mail only; the grants in fixtures go through the service
(`platform_fixture`), and the grants under test go through the real routes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .platform_fixture import legacy_platform_role_row, make_platform_admin
from synwevo_core import platform_access
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import (PERMISSIONS, ROLE_ORDER, ROLES, SUPER_ADMIN, PlatformAccess,
                                          catalogue)
from synwevo_core.sessions import ADMIN_IDLE_SECONDS, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

PW = "isolated-platform-fixture-passphrase-31"
REASON = "Isolated fixture: platform role change"

#: Every /api/admin route outside the API console: (method, path, permission, dangerous).
#: `None` = any platform permission (the caller describing themselves).
ROUTES = [
    ("GET", "/api/admin/overview", "platform.overview.view", False),
    ("GET", "/api/admin/session", None, False),
    ("GET", "/api/admin/users", "platform.users.view", False),
    ("POST", "/api/admin/users/{uid}/status", "platform.users.manage", True),
    ("POST", "/api/admin/users/{uid}/recovery", "platform.users.support", True),
    ("POST", "/api/admin/users/{uid}/revoke-sessions", "platform.users.support", True),
    ("GET", "/api/admin/workspaces", "platform.workspaces.view", False),
    ("GET", "/api/admin/workspaces/{wid}/members", "platform.workspaces.view", False),
    ("POST", "/api/admin/workspaces/{wid}/claim", "platform.workspaces.manage", True),
    ("POST", "/api/admin/workspaces/{wid}/freeze", "platform.workspaces.manage", True),
    ("POST", "/api/admin/workspaces/{wid}/unfreeze", "platform.workspaces.manage", True),
    ("GET", "/api/admin/organizations", "platform.orgs.view", False),
    ("POST", "/api/admin/organizations/{oid}/{action}", "platform.orgs.manage", True),
    ("GET", "/api/admin/registration", "platform.config.view", False),
    ("POST", "/api/admin/registration", "platform.config.manage", True),
    ("GET", "/api/admin/identity-providers", "platform.config.view", False),
    ("GET", "/api/admin/mail", "platform.mail.view", False),
    ("POST", "/api/admin/mail/{mid}/retry", "platform.mail.manage", True),
    ("GET", "/api/admin/audit", "platform.audit.view", False),
    ("GET", "/api/admin/platform/roles", "platform.roles.view", False),
    ("GET", "/api/admin/users/{uid}/platform-access", "platform.roles.view", False),
    ("POST", "/api/admin/users/{uid}/platform-grants", "platform.roles.assign", True),
    ("POST", "/api/admin/platform-grants/{gid}/revoke", "platform.roles.assign", True),
    ("POST", "/api/admin/users/{uid}/platform-role", "platform.roles.assign", True),
]
CONSOLE_GET = "/api/admin/api-platform/overview"
CONSOLE_POST = "/api/admin/api-platform/approvals"


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {},
                       headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    # Explicit shell fixture (the account page's own index.html): the tests run before or after a
    # front-end build, and the /admin gate is decided before the shell is served.
    dist = tmp_path / "fixture-build"
    dist.mkdir()
    dist.joinpath("index.html").write_text(
        (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver", "ACCOUNTS_UI_DIR": str(dist)}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    root = make_platform_admin(store, ident.create_user("root@example.test", PW, status="active"))
    plain = verified(store, ident, "plain@example.test")
    clients: list[TestClient] = []

    def login(user, *, remember=False):
        c = TestClient(api)
        r = post(c, "/api/auth/login", {"email": user["email"], "password": PW, "remember": remember})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    w = {"api": api, "store": store, "ident": ident, "root": root, "plain": plain, "login": login,
         "wid": ident.workspaces_for(plain["id"])[0]["id"]}
    yield w
    for c in clients:
        c.close()


def verified(store, ident, email, password=PW, **kw):
    u = ident.create_user(email, password, status="active", **kw)
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    return ident.get_user(u["id"])


def reauth(client):
    r = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text


def holder(w, role, email=None):
    u = verified(w["store"], w["ident"], email or f"{role}@example.test")
    return make_platform_admin(w["store"], u, role=role)


def grant_of(w, user, role):
    return next(g for g in PlatformAccess(w["store"]).live_grants(user["id"]) if g["role"] == role)


def _path(template, w):
    return template.format(uid=w["plain"]["id"], wid=w["wid"], mid="mail_fixture_missing",
                           oid="org_fixture_missing", action="freeze", gid="pg_fixture_missing")


def _call(client, method, path):
    if method == "GET":
        return client.get(path)
    return post(client, path, {"reason": REASON, "status": "suspended", "mode": "open", "role": "auditor",
                               "user_id": "u_fixture_missing"})


# ---------------------------------------------------------------- the catalogue and the matrix
def test_catalogue_roles_are_sets_of_catalogued_permissions_and_super_admin_is_all_of_them():
    assert ROLES[SUPER_ADMIN] == frozenset(PERMISSIONS)
    for role, perms in ROLES.items():
        assert perms <= set(PERMISSIONS), role
        assert not any(p.startswith(("workspace.", "docs.", "org.", "document")) for p in perms), \
            "平台角色不带任何业务内容权限"
    # only the super administrator can grant roles; API management is its own role's, never implied
    assert [r for r in ROLES if ROLES[r] & {"platform.roles.assign", "platform.roles.assign_super"}] == [SUPER_ADMIN]
    assert [r for r in ROLES if "api_platform.manage" in ROLES[r]] == [SUPER_ADMIN, "api_platform_admin"]
    assert ROLES["api_platform_admin"] == {"platform.overview.view", "api_platform.manage"}
    assert [r["id"] for r in catalogue()["roles"]] == list(ROLE_ORDER)


def test_every_admin_route_is_in_the_matrix(world):
    mounted = {(m.upper(), p) for p, ops in world["api"].openapi()["paths"].items()
               if p.startswith("/api/admin/") and not p.startswith("/api/admin/api-platform") for m in ops}
    assert mounted == {(m, p) for m, p, _, _ in ROUTES}, "新的管理路由必须在这里写明它需要的平台权限"


@pytest.mark.parametrize("role", ROLE_ORDER)
def test_each_built_in_role_is_allowed_exactly_its_permissions(world, role):
    c = world["login"](holder(world, role))
    for method, template, permission, dangerous in ROUTES:
        path = _path(template, world)
        r = _call(c, method, path)
        if permission is not None and permission not in ROLES[role]:
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (role, method, path, r.text)
            assert r.json()["detail"]["permission"] == permission
        elif dangerous:
            # past the permission check, stopped by the strict re-authentication: nothing changed
            assert r.status_code == 403 and r.json()["error"] == "reauth_required", (role, method, path, r.text)
        else:
            assert r.status_code == 200, (role, method, path, r.text)
    overview, mutation = c.get(CONSOLE_GET), post(c, CONSOLE_POST, {"reason": REASON})
    if "api_platform.manage" in ROLES[role]:
        assert overview.status_code == 200 and mutation.json()["error"] == "reauth_required"
    else:
        for r in (overview, mutation):
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (role, r.text)
    me = c.get("/api/auth/me").json()
    assert me["platform_roles"] == [role] and set(me["platform_permissions"]) == ROLES[role]
    assert me["permissions"] == (["api_platform.manage"] if "api_platform.manage" in ROLES[role] else [])
    assert me["platform_role"] == ("admin" if role == SUPER_ADMIN else "none"), "委派角色的回滚镜像保持 none"
    # nothing was written by any of the refused or unconfirmed calls
    assert world["ident"].get_user(world["plain"]["id"])["status"] == "active"
    assert world["ident"].get_workspace(world["wid"])["status"] == "active"


def test_no_platform_permission_sees_404_everywhere_including_owners_and_workspace_admins(world):
    store, ident = world["store"], world["ident"]
    owner = verified(store, ident, "org-owner@example.test")
    org = Organizations(store, ident).create(owner["id"], "Fixture organization", "Fixture workspace", "fixture-org-pa1")
    ws_admin = verified(store, ident, "ws-admin@example.test")
    ident.add_member(org["workspace"]["id"], ws_admin["id"], "admin", actor_id=owner["id"])
    for who in (world["plain"], owner, ws_admin):
        c = world["login"](who)
        for method, template, _, _ in ROUTES:
            r = _call(c, method, _path(template, world))
            assert r.status_code == 404 and r.json()["error"] == "not_found", (who["email"], template, r.text)
        assert c.get(CONSOLE_GET).status_code == 404
        assert post(c, CONSOLE_POST, {"reason": REASON}).status_code == 404
        assert c.get("/admin").status_code == 404 and c.get("/admin/api-platform").status_code == 404
        me = c.get("/api/auth/me").json()
        assert me["platform_permissions"] == [] and me["platform_roles"] == [] and me["permissions"] == []
        assert me["admin_locked"] is False
    assert not store.one("SELECT 1 AS x FROM audit_events WHERE action='admin.access.denied'"), \
        "没有平台权限的人得到的是 404，不留下「被拒绝的管理操作」"


def test_the_admin_shell_opens_for_any_platform_permission(world):
    """The /admin shell is served to anyone a grant gives a platform permission — an auditor or an
    API platform administrator as much as a super administrator — and to nobody else; what each
    of them may use inside is decided per request by the routes above."""
    for role in ("auditor", "api_platform_admin", SUPER_ADMIN):
        c = world["login"](world["root"] if role == SUPER_ADMIN else holder(world, role))
        for path in ("/admin", "/admin/api-platform"):
            r = c.get(path)
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), (role, path, r.status_code)
    plain = world["login"](world["plain"])
    assert plain.get("/admin").status_code == 404 and plain.get("/admin").json()["error"] == "not_found"
    assert TestClient(world["api"]).get("/admin").status_code == 404


def test_a_denied_attempt_is_audited_once_per_window(world):
    auditor = holder(world, "auditor")
    c = world["login"](auditor)
    path = f"/api/admin/users/{world['plain']['id']}/status"
    for _ in range(3):
        r = post(c, path, {"reason": REASON, "status": "suspended"})
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied"
    assert c.get(CONSOLE_GET).status_code == 403
    rows = world["store"].all("SELECT * FROM audit_events WHERE action='admin.access.denied' AND actor_id=? ORDER BY seq",
                              (auditor["id"],))
    assert [r["object_id"] for r in rows] == ["platform.users.manage", "api_platform.manage"]
    assert all(r["result"] == "denied" for r in rows)
    assert json.loads(rows[0]["meta"])["path"] == path


# ---------------------------------------------------------------- granting
def test_a_super_administrator_grants_and_revokes_through_the_routes(world):
    store = world["store"]
    root = world["login"](world["root"])
    target = world["plain"]
    path = f"/api/admin/users/{target['id']}/platform-grants"
    assert post(root, path, {"role": "support", "reason": REASON}).json()["error"] == "reauth_required"
    reauth(root)
    assert post(root, path, {"role": "support", "reason": "短"}).json()["error"] == "missing_reason"
    assert post(root, path, {"role": "operator", "reason": REASON}).json()["error"] == "bad_platform_role"
    r = post(root, path, {"role": "support", "reason": REASON})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"] == [] and body["after"] == ["support"]
    assert post(root, path, {"role": "support", "reason": REASON}).json()["error"] == "already_granted"
    # the audit record is part of the grant: before/after, reason, request id
    ev = store.one("SELECT * FROM audit_events WHERE action='admin.platform.grant' AND object_id=?", (target["id"],))
    meta = json.loads(ev["meta"])
    assert ev["actor_id"] == world["root"]["id"] and ev["request_id"] and meta["reason"] == REASON
    assert meta["before"] == [] and meta["after"] == ["support"] and meta["role"] == "support"
    explained = root.get(f"/api/admin/users/{target['id']}/platform-access").json()
    assert explained["roles"] == ["support"] and explained["grants"][0]["granted_by_email"] == "root@example.test"
    assert {p["permission"] for p in explained["permissions"]} == ROLES["support"]
    assert all(p["sources"][0]["role"] == "support" for p in explained["permissions"])
    assert SUPER_ADMIN in explained["assignable_roles"] and "support" not in explained["assignable_roles"]
    r = post(root, f"/api/admin/platform-grants/{body['grant']['id']}/revoke", {"reason": REASON})
    assert r.status_code == 200 and r.json()["after"] == []
    ev = store.one("SELECT meta FROM audit_events WHERE action='admin.platform.revoke' AND object_id=?", (target["id"],))
    assert json.loads(ev["meta"])["before"] == ["support"]
    assert post(root, f"/api/admin/platform-grants/{body['grant']['id']}/revoke", {"reason": REASON}).json()["error"] == "grant_revoked"


def test_grant_preconditions_close_d2(world):
    from synwevo_core.apps import DOCS_APP_ID
    from synwevo_core.messaging_bridge import ensure_app_account
    store, ident = world["store"], world["ident"]
    root = world["login"](world["root"])
    reauth(root)
    pending = ident.create_user("pending@example.test", PW)
    suspended = verified(store, ident, "suspended@example.test")
    ident.set_status(suspended["id"], "suspended")
    unverified = ident.create_user("unverified@example.test", PW, status="active")
    no_factor = verified(store, ident, "no-factor@example.test", password=None)
    app_identity = ensure_app_account(store, ident, DOCS_APP_ID)
    for user_id, code in ((pending["id"], "target_pending"), (suspended["id"], "target_unavailable"),
                          (unverified["id"], "target_unverified"), (no_factor["id"], "target_cannot_step_up"),
                          (app_identity, "application_identity"), ("u_fixture_missing", "no_user")):
        r = post(root, f"/api/admin/users/{user_id}/platform-grants", {"role": "auditor", "reason": REASON})
        assert r.json()["error"] == code, (code, r.text)
        assert r.status_code == (404 if code == "no_user" else 409)
    assert not store.one("SELECT 1 AS x FROM platform_role_grants WHERE user_id<>?", (world["root"]["id"],))
    # the compatibility route applies the same rules
    r = post(root, f"/api/admin/users/{pending['id']}/platform-role", {"role": "admin", "reason": REASON})
    assert r.json()["error"] == "target_pending"
    assert post(root, f"/api/admin/users/{world['plain']['id']}/platform-role",
                {"role": "operator", "reason": REASON}).json()["error"] == "operator_retired"


def test_holding_is_not_granting_and_nobody_changes_their_own_authority_to_assign(world):
    store = world["store"]
    root = world["login"](world["root"])
    reauth(root)
    me = world["root"]["id"]
    # self-escalation and self-demotion of a role that can assign roles are refused
    assert post(root, f"/api/admin/users/{me}/platform-grants", {"role": SUPER_ADMIN, "reason": REASON}).json()["error"] == "self_assignment"
    own = grant_of(world, world["root"], SUPER_ADMIN)
    r = post(root, f"/api/admin/platform-grants/{own['id']}/revoke", {"reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "self_assignment"
    r = post(root, f"/api/admin/users/{me}/platform-role", {"role": "none", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "self_assignment"
    # a role without assigning permissions may be added to oneself (it adds nothing to a super admin)
    assert post(root, f"/api/admin/users/{me}/platform-grants", {"role": "auditor", "reason": REASON}).status_code == 200
    # an access administrator holds users.manage but cannot grant anything
    access_admin = world["login"](holder(world, "access_admin"))
    reauth(access_admin)
    r = post(access_admin, f"/api/admin/users/{world['plain']['id']}/platform-grants", {"role": "support", "reason": REASON})
    assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied"
    # the service refuses a role wider than the actor's own permissions, and super_admin without assign_super
    access = PlatformAccess(store)
    with pytest.raises(ServiceError) as e:
        access.grant(world["plain"]["id"], world["plain"]["id"], "auditor", REASON)
    assert e.value.code == "platform_permission_denied"
    assert PlatformAccess(store).is_usable_super_admin(me)


# ---------------------------------------------------------------- the last usable super administrator
def test_last_super_admin_under_revocation_suspension_and_self_deletion(world):
    store, ident = world["store"], world["ident"]
    access = PlatformAccess(store)
    root_grant = grant_of(world, world["root"], SUPER_ADMIN)
    # a super administrator who cannot complete the step-up does not count as usable ...
    no_factor = make_platform_admin(store, verified(store, ident, "no-factor-super@example.test", password=None))
    assert not access.is_usable_super_admin(no_factor["id"]) and len(ident.platform_admins()) == 1
    # ... so it cannot remove the last one who can
    with pytest.raises(ServiceError) as e:
        access.revoke(no_factor["id"], root_grant["id"], REASON)
    assert e.value.code == "last_super_admin"
    # suspension: an access administrator may suspend accounts, never the last usable super admin
    access_admin = world["login"](holder(world, "access_admin"))
    reauth(access_admin)
    r = post(access_admin, f"/api/admin/users/{world['root']['id']}/status", {"status": "suspended", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "last_super_admin"
    support = holder(world, "support")
    r = post(access_admin, f"/api/admin/users/{support['id']}/status", {"status": "suspended", "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "demote_first"
    assert ident.get_user(world["root"]["id"])["status"] == "active" and ident.get_user(support["id"])["status"] == "active"
    # self-deletion
    root = world["login"](world["root"])
    r = post(root, "/api/auth/deletion", {"password": PW})
    assert r.status_code == 409 and r.json()["error"] == "last_super_admin"
    assert len(ident.platform_admins()) == 1


def test_two_super_admins_revoking_each_other_at_once_leave_exactly_one(world, monkeypatch):
    store, ident = world["store"], world["ident"]
    second = make_platform_admin(store, verified(store, ident, "second-super@example.test"))
    a, b = world["login"](world["root"]), world["login"](second)
    reauth(a)
    reauth(b)
    grant_a, grant_b = grant_of(world, world["root"], SUPER_ADMIN), grant_of(world, second, SUPER_ADMIN)
    # Both requests pass the route's permission check before either reaches the service, so the
    # outcome is decided by the re-check inside the write transaction (the pause substitutes no
    # authorization result; it only orders the two real calls).
    admitted, results = threading.Barrier(2, timeout=10), {}
    original = PlatformAccess.revoke

    def after_admission(self, *args, **kwargs):
        admitted.wait()
        return original(self, *args, **kwargs)

    monkeypatch.setattr(PlatformAccess, "revoke", after_admission)

    def revoke(name, client, gid):
        results[name] = post(client, f"/api/admin/platform-grants/{gid}/revoke", {"reason": REASON})

    threads = [threading.Thread(target=revoke, args=("a", a, grant_b["id"])),
               threading.Thread(target=revoke, args=("b", b, grant_a["id"]))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = sorted(r.status_code for r in results.values())
    assert codes[0] == 200 and codes[1] in (403, 409), {k: v.text for k, v in results.items()}
    loser = next(r for r in results.values() if r.status_code != 200)
    assert loser.json()["error"] in ("platform_permission_denied", "last_super_admin")
    assert len(ident.platform_admins()) == 1
    assert store.one("SELECT COUNT(*) AS n FROM platform_role_grants WHERE role='super_admin' AND revoked_at IS NULL")["n"] == 1
    assert store.one("SELECT COUNT(*) AS n FROM users WHERE platform_role='admin'")["n"] == 1, "回滚镜像同步"


# ---------------------------------------------------------------- sessions: tightening and revocation
def test_a_grant_tightens_the_grantees_sessions_and_a_revocation_applies_on_the_next_request(world):
    store = world["store"]
    target = world["plain"]
    remembered = world["login"](target, remember=True)
    row = store.one("SELECT * FROM sessions WHERE user_id=? AND revoked_at IS NULL", (target["id"],))
    assert row["persistent"] == 1 and row["idle_expires_at"] - row["last_seen_at"] > ADMIN_IDLE_SECONDS
    assert remembered.get("/api/admin/overview").status_code == 404
    root = world["login"](world["root"])
    reauth(root)
    granted = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": "support", "reason": REASON})
    assert granted.status_code == 200 and granted.json()["sessions_tightened"] == 1
    row = store.one("SELECT * FROM sessions WHERE id=?", (row["id"],))
    assert row["persistent"] == 0 and row["idle_expires_at"] <= row["last_seen_at"] + ADMIN_IDLE_SECONDS
    # the same browser session now has the permission, from the next request on
    assert remembered.get("/api/admin/overview").status_code == 200
    assert remembered.get("/api/admin/users").status_code == 200
    r = post(root, f"/api/admin/platform-grants/{granted.json()['grant']['id']}/revoke", {"reason": REASON})
    assert r.status_code == 200
    assert remembered.get("/api/admin/overview").status_code == 404, "撤权在下一个请求生效，不等重新登录"
    assert remembered.get("/api/auth/me").json()["platform_permissions"] == []


def test_expired_grants_are_ignored_and_can_be_granted_again(world, monkeypatch):
    target = world["plain"]
    root = world["login"](world["root"])
    reauth(root)
    assert post(root, f"/api/admin/users/{target['id']}/platform-grants",
                {"role": SUPER_ADMIN, "reason": REASON, "expires_at": now() + 3600}).json()["error"] == "super_admin_no_expiry"
    assert post(root, f"/api/admin/users/{target['id']}/platform-grants",
                {"role": "auditor", "reason": REASON, "expires_at": now() + 10}).json()["error"] == "bad_expiry"
    r = post(root, f"/api/admin/users/{target['id']}/platform-grants",
             {"role": "auditor", "reason": REASON, "expires_at": now() + 600})
    assert r.status_code == 200
    c = world["login"](target)
    assert c.get("/api/admin/audit").status_code == 200
    later = now() + 700
    monkeypatch.setattr(platform_access, "_now", lambda: later)     # the clock, not the data, moves
    assert c.get("/api/admin/audit").status_code == 404
    assert c.get("/api/auth/me").json()["platform_roles"] == []
    assert target["id"] not in {row["user"]["id"] for row in root.get("/api/admin/platform/roles").json()["roster"]}
    r = post(root, f"/api/admin/users/{target['id']}/platform-grants", {"role": "auditor", "reason": REASON})
    assert r.status_code == 200 and r.json()["after"] == ["auditor"]
    assert c.get("/api/admin/audit").status_code == 200


# ---------------------------------------------------------------- explanation, roster, session
def test_session_roster_and_explanation(world):
    store, ident = world["store"], world["ident"]
    root = world["login"](world["root"])
    s = root.get("/api/admin/session").json()
    assert s["roles"] == [SUPER_ADMIN] and s["step_up"]["required"] is False
    assert {p["permission"] for p in s["permissions"]} == set(PERMISSIONS)
    assert s["reauth"]["seconds_left"] == 0 and s["environment"]["instance"] == store.instance_kind()
    reauth(root)
    assert 0 < root.get("/api/admin/session").json()["reauth"]["seconds_left"] <= s["reauth"]["window_s"]
    holder(world, "auditor")
    roster = root.get("/api/admin/platform/roles").json()
    assert [r["id"] for r in roster["roles"]] == list(ROLE_ORDER) and roster["super_admins"] == 1
    by_email = {row["user"]["email"]: row for row in roster["roster"]}
    assert by_email["root@example.test"]["roles"] == [SUPER_ADMIN]
    assert by_email["root@example.test"]["step_up"] == {"capable": True, "password": True, "passkey_uv": False,
                                                         "totp": False, "reauth": True}
    assert by_email["root@example.test"]["last_sign_in_at"]
    assert by_email["auditor@example.test"]["roles"] == ["auditor"]
    assert "plain@example.test" not in by_email
    assert "secret" not in json.dumps(roster) and "phc" not in json.dumps(roster)


# ---------------------------------------------------------------- migration and the rollback mirror
def _as_v26(store: Store, rows: dict[str, str]) -> None:
    """Put a v27 database back into the shape v26 wrote: no grants table, no migration mark,
    schema 26, and `platform_role` holding the role itself."""
    with store.tx() as t:
        t.execute("DROP TABLE platform_role_grants")
        t.execute("DELETE FROM meta WHERE key='platform_grants.migrated'")
        t.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
        for uid, role in rows.items():
            t.execute("UPDATE users SET platform_role=? WHERE id=?", (role, uid))


def test_migration_26_to_27_makes_admins_super_admins_and_clears_operators(tmp_path):
    store = Store(tmp_path / "d")
    ident = Identity(store)
    # the production shape: an administrator created by bootstrap, its address never verified
    admin = ident.create_user("owner@example.test", PW, status="active")
    operator = ident.create_user("operator@example.test", PW, status="active")
    plain = ident.create_user("member@example.test", PW, status="active")
    _as_v26(store, {admin["id"]: "admin", operator["id"]: "operator"})
    migrated = Store(tmp_path / "d")
    assert migrated.schema_version() == 27
    grants = migrated.all("SELECT * FROM platform_role_grants")
    assert [(g["user_id"], g["role"], g["granted_by"]) for g in grants] == [(admin["id"], SUPER_ADMIN, "migration:v27")]
    access = PlatformAccess(migrated)
    assert access.is_usable_super_admin(admin["id"]), "迁移授予的最高管理员不因邮箱未验证而被锁在外面"
    assert Identity(migrated).platform_admins()[0]["id"] == admin["id"] and not Identity(migrated).bootstrap_needed()
    users = {u["id"]: u["platform_role"] for u in migrated.all("SELECT id, platform_role FROM users")}
    assert users == {admin["id"]: "admin", operator["id"]: "none", plain["id"]: "none"}
    assert access.effective_permissions(operator["id"]) == frozenset()
    audit = {r["action"]: r for r in migrated.all("SELECT * FROM audit_events WHERE actor_kind='system'")}
    assert json.loads(audit["admin.platform.operator_cleared"]["meta"])["before"] == "operator"
    assert json.loads(audit["admin.platform.grant"]["meta"])["source"] == "migration:v27"
    # opening again changes nothing
    Store(tmp_path / "d")
    assert migrated.one("SELECT COUNT(*) AS n FROM platform_role_grants")["n"] == 1
    assert migrated.one("SELECT COUNT(*) AS n FROM audit_events WHERE actor_kind='system'")["n"] == 2


def test_the_rollback_mirror_follows_every_grant_and_carries_a_rollbacks_changes_forward(tmp_path):
    store = Store(tmp_path / "d")
    ident = Identity(store)
    a = make_platform_admin(store, verified(store, ident, "a@example.test"))
    b = make_platform_admin(store, verified(store, ident, "b@example.test"))
    delegated = make_platform_admin(store, verified(store, ident, "d@example.test"), role="access_admin")
    role_of = lambda u: ident.get_user(u["id"])["platform_role"]  # noqa: E731
    assert (role_of(a), role_of(b), role_of(delegated)) == ("admin", "admin", "none"), \
        "委派管理员的镜像是 none：回到上一版本时它失去权限，而不是变宽"
    PlatformAccess(store).revoke(a["id"], grant_of({"store": store}, b, SUPER_ADMIN)["id"], REASON)
    assert role_of(b) == "none"
    # the previous release, during a rollback, demoted `a` and promoted `delegated` in the column
    PlatformAccess(store).grant(a["id"], b["id"], SUPER_ADMIN, REASON)
    legacy_platform_role_row(store, a, "none")
    legacy_platform_role_row(store, delegated, "admin")
    reopened = Store(tmp_path / "d")
    access = PlatformAccess(reopened)
    assert SUPER_ADMIN not in access.roles(a["id"]), "回滚期间的降级在升级后仍然有效"
    assert access.roles(delegated["id"]) == [SUPER_ADMIN, "access_admin"]
    carried = grant_of({"store": reopened}, delegated, SUPER_ADMIN)
    assert carried["granted_by"] == "reconcile:mirror"
    assert (role_of(a), role_of(b), role_of(delegated)) == ("none", "admin", "admin")
