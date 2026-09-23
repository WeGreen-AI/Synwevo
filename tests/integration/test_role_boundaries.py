"""角色边界：一个角色**不**提供哪些接口。

这些检查天然跨模块——要问"业务角色会不会提供账户接口"，就得同时有业务应用和账户服务，
再由部署组装按角色拼出来。所以它们在集成验证里，而不是塞进任何一个模块自己的套件。

一个进程一辈子只有一个角色。这里因此是**按角色各建一个应用**，而不是在一个已经跑起来的服务上
改环境变量——后者测的是一个部署不会发生的状态。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import platform_server.app as server

ACCOUNT_ONLY = ("/api/admin/overview", "/api/auth/passkeys", "/api/auth/mfa",
                "/api/auth/sessions")
BUSINESS_ONLY = ("/api/projects", "/api/templates", "/api/v1/projects")


@pytest.fixture()
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))

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
    for path in BUSINESS_ONLY + ("/api/admin/overview",):
        assert c.get(path).status_code == 404, path
    # ...but it does serve its own.
    assert c.get("/api/portal/home").status_code in (200, 401)


@pytest.mark.parametrize("role,expect_password", [
    ("accounts", True), ("all", True), ("app", False), ("portal", False),
])
def test_auth_state_says_whether_this_service_can_check_a_password(build, role, expect_password):
    """A relying party does not serve /api/auth/login, so it must not offer a password box.

    Found in the browser: clicking the DocWeave card landed on DocWeave's own login form, which
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
