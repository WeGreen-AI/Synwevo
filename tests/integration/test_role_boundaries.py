"""角色边界：一个角色**不**提供哪些接口。

这些检查天然跨模块——要问"业务角色会不会提供账户接口"，就得同时有业务应用和账户服务，
再由部署组装按角色拼出来。所以它们在集成验证里，而不是塞进任何一个模块自己的套件。

一个进程一辈子只有一个角色。这里因此是**按角色各建一个应用**，而不是在一个已经跑起来的服务上
改环境变量——后者测的是一个部署不会发生的状态。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import synwevo_server.app as server

ACCOUNT_ONLY = ("/api/admin/overview", "/api/auth/passkeys", "/api/auth/mfa",
                "/api/auth/sessions")
BUSINESS_ONLY = ("/api/projects", "/api/templates", "/api/v1/projects")
# Synwevo Message's API (CI-66): served by its own role (and `all`), refused by name elsewhere.
MESSAGING_ONLY = ("/api/messaging/conversations", "/api/messaging/capabilities", "/api/messaging/stream")


@pytest.fixture()
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))

    def make(role: str) -> TestClient:
        return TestClient(server.create_app(str(tmp_path / "d"), role=role, start_jobs=False))

    return make


@pytest.mark.parametrize("path", ACCOUNT_ONLY)
def test_a_business_role_refuses_the_account_service_surface(build, path):
    r = build("app").get(path)
    assert r.status_code == 404, f"{path} → {r.status_code}"
    assert r.json()["error"] == "not_in_this_role"


@pytest.mark.parametrize("path", BUSINESS_ONLY)
def test_the_account_service_refuses_business_data(build, path):
    """An accounts-origin cookie reaching a document would defeat the whole point of giving
    them separate origins."""
    assert build("accounts").get(path).status_code == 404, path


def test_the_portal_serves_neither(build):
    c = build("portal")
    for path in BUSINESS_ONLY + ("/api/admin/overview",) + MESSAGING_ONLY:
        assert c.get(path).status_code == 404, path
    # ...but it does serve its own.
    assert c.get("/api/portal/home").status_code in (200, 401)


def test_the_messages_role_serves_conversations_and_nothing_else_of_the_others(build):
    """Synwevo Message is a relying party like Docs: its own API, the shared session routes
    (the badge's unread-count among them), and no account surface, no documents, no portal —
    not even the account origin's former /api/auth/messaging routes, which would be a second
    write path into the same conversations."""
    c = build("messages")
    for path in MESSAGING_ONLY:
        r = c.get(path)
        assert r.status_code == 401 and r.json()["error"] == "unauthorized", (path, r.status_code, r.text[:120])
    assert c.get("/api/auth/messaging/unread-count").status_code == 401           # shared, needs a session
    assert c.get("/api/auth/state").status_code == 200
    for path in ACCOUNT_ONLY + BUSINESS_ONLY + ("/api/portal/home", "/api/auth/messaging/conversations",
                                                "/api/auth/messaging/reports"):
        r = c.get(path)
        body = r.json()
        # the browser API's envelope is {"error": "<code>"}; the machine API's (/api/v1) nests
        # it as {"error": {"code": "<code>", …}}
        code = body["error"]["code"] if isinstance(body.get("error"), dict) else body.get("error")
        assert r.status_code == 404 and code == "not_in_this_role", (path, r.status_code, body)
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"}).status_code == 404


@pytest.mark.parametrize("role", ["accounts", "app", "portal"])
def test_every_other_role_refuses_the_messaging_api_by_name(build, role):
    """A stale bookmark or an old shell asking the wrong origin gets told where the interface
    lives, not only that it is not here."""
    c = build(role)
    for path in MESSAGING_ONLY:
        r = c.get(path)
        assert r.status_code == 404 and r.json()["error"] == "not_in_this_role", (role, path, r.text[:120])
        assert "Synwevo Message" in r.json()["message"], r.json()["message"]


@pytest.mark.parametrize("role,expect_password", [
    ("accounts", True), ("all", True), ("app", False), ("portal", False), ("messages", False),
])
def test_auth_state_says_whether_this_service_can_check_a_password(build, role, expect_password):
    """A relying party does not serve /api/auth/login, so it must not offer a password box.

    Found in the browser: clicking the Synwevo Docs card landed on Synwevo Docs' own login form, which
    could never work — on the one page whose whole point is that the password is typed in
    exactly one place.
    """
    st = build(role).get("/api/auth/state").json()
    assert st["role"] == role
    assert st["password_login"] is expect_password
    assert st["platform_name"]


def test_a_relying_party_does_not_serve_the_login_endpoint(build):
    r = build("app").post("/api/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert r.status_code == 404 and r.json()["error"] == "not_in_this_role"
    # ...while the account service still does (401 here: the account does not exist).
    assert build("accounts").post("/api/auth/login",
                                  json={"email": "nobody@example.com", "password": "x"}).status_code == 401


# ---------------------------------------------------------------- rebrand compatibility
def test_the_pre_rebrand_csrf_header_is_still_accepted(build):
    """A tab that loaded yesterday's bundle keeps sending `X-DocWeave-CSRF`.

    A deploy is not atomic across every open browser tab, so refusing the old header would sign
    those people out of every state-changing action until they reloaded — which reads as "the
    app is broken", not as "there is a new brand".
    """
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, LEGACY_CSRF_HEADER
    assert CSRF_HEADER == "X-Synwevo-CSRF" and LEGACY_CSRF_HEADER == "X-DocWeave-CSRF"
    c = build("all")
    # No session: both header names must reach the same refusal, not two different ones.
    for header in (CSRF_HEADER, LEGACY_CSRF_HEADER):
        r = c.post("/api/auth/workspace", json={"workspace_id": "w_nope"},
                   headers={header: "x"}, cookies={CSRF_COOKIE: "x"})
        assert r.status_code == 401, f"{header} -> {r.status_code}"


@pytest.mark.parametrize("role", ["app", "messages", "portal", "accounts"])
def test_the_application_switcher_list_is_a_shared_session_route(build, role):
    """The platform bar's switcher reads /api/auth/apps on every origin (Q11): it answers there
    and needs a session, like the badge counts beside it."""
    r = build(role).get("/api/auth/apps")
    assert r.status_code == 401 and r.json()["error"] == "unauthorized", (role, r.status_code, r.text[:120])


def test_the_application_switcher_lists_what_this_person_can_open(tmp_path, monkeypatch):
    """Same query as the workspace's cards: an application appears with its own sign-in start
    only for someone entitled to it, and nothing about what is inside a workspace."""
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    api = server.create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    with TestClient(api) as c:
        ident = api.state.identity
        u = ident.create_user("switcher@example.test", "harbor lights guide the evening ferry home", status="active", display_name="Sam")
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        stranger = ident.create_user("stranger@example.test", "harbor lights guide the evening ferry home", status="active", display_name="Stan")
        from synwevo_core.apps import Apps
        from synwevo_core.sessions import COOKIE_NAME, Sessions
        wid = ident.ensure_personal_workspace(u["id"])["id"]
        Apps(ident.store, ident).adopt_existing_members()
        raw, _ = Sessions(ident.store).create(u["id"], workspace_id=wid, amr="password")
        c.cookies.set(COOKIE_NAME, raw)
        items = c.get("/api/auth/apps").json()["items"]
        assert items and all(set(a) == {"id", "name", "icon", "launch_url"} for a in items)
        assert all("/auth/start" in a["launch_url"] for a in items)
        project = lambda rows: [{k: r.get(k) for k in ("id", "name", "icon", "launch_url")} for r in rows]
        assert items == project(Apps(ident.store, ident).apps_for(u["id"], wid)), "the workspace cards' own query"
        # an entitlement taken away disappears from the switcher as from the cards
        had_docs = any(a["id"] == "docweave" for a in items)
        Apps(ident.store, ident).revoke(wid, "docweave", u["id"])
        after = c.get("/api/auth/apps").json()["items"]
        assert after == project(Apps(ident.store, ident).apps_for(u["id"], wid))
        assert not had_docs or not any(a["id"] == "docweave" for a in after), "a revoked entitlement leaves the switcher"
        raw2, _ = Sessions(ident.store).create(stranger["id"], workspace_id=None, amr="password")
        c.cookies.set(COOKIE_NAME, raw2)
        assert c.get("/api/auth/apps").json()["items"] == project(Apps(ident.store, ident).apps_for(stranger["id"], None))
