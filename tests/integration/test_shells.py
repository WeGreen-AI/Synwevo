"""外壳归属：哪个角色提供哪一份构建产物。

这些检查跨模块——它们同时需要工作台的构建、账户外壳的构建和角色组装——所以放在集成验证里，
而不是放在任何一个应用自己的测试里。放错地方的代价不是风格问题：一个应用的测试套件里出现
`import synwevo_server`，就是"应用不得导入另一个应用"这条规则的第一个例外。
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

WORKBENCH_DIST = Path(__file__).resolve().parents[2] / "apps" / "docs" / "web" / "dist"
ACCOUNTS_DIST = Path(__file__).resolve().parents[2] / "services" / "identity" / "web" / "dist"
PORTAL_DIST = Path(__file__).resolve().parents[2] / "apps" / "portal" / "web" / "dist"

pytestmark = pytest.mark.skipif(not (WORKBENCH_DIST.exists() and ACCOUNTS_DIST.exists() and PORTAL_DIST.exists()),
                                reason="前端未构建")


def _client(tmp_path, monkeypatch, *, role: str) -> TestClient:
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ROLE", role)
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.create_app(str(tmp_path), role=role, start_jobs=False))


@pytest.mark.parametrize("role,marker", [("all", "Synwevo Docs"), ("app", "Synwevo Docs"),
                                         ("portal", "/portal-assets/"), ("accounts", "Synwevo Account")])
def test_a_role_serves_its_own_shell_even_when_another_is_named(tmp_path, monkeypatch, role, marker):
    """Each role serves its own build and no other.

    `/portal.html` on the Synwevo Docs origin used to serve the portal's shell, because one build
    held all three and the generic-file branch matched the name before the role was consulted.
    Each shell is now a separate build, so those filenames are not files anywhere — and the
    check is that naming one still gets you the shell this role owns.
    """
    c = _client(tmp_path, monkeypatch, role=role)
    for path in ("/", "/index.html", "/portal.html", "/accounts.html"):
        response = c.get(path)
        assert response.status_code == 200
        assert marker in response.text, f"role={role} 在 {path} 上没有返回自己的外壳"
        if role == "portal":
            assert "/account-assets/" not in response.text
            assert "/assets/" not in response.text
    if role == "portal":
        assert "<title>Synwevo · 让数据成为文档</title>" in c.get("/").text
        assert '<meta name="robots" content="index, follow"' in c.get("/").text


def test_the_account_pages_exist_once_and_the_workbench_does_not_carry_a_copy(tmp_path, monkeypatch):
    """One implementation of the sign-in and account screens, reachable from every role.

    On the single-process deployment both shells sit on one origin, and the account paths must
    reach the account shell — not the workbench's "page not found", and not a second copy of
    the sign-in form compiled into the workbench bundle.
    """
    c = _client(tmp_path, monkeypatch, role="all")
    for path in ("/login", "/account", "/forgot-password"):
        assert "Synwevo Account" in c.get(path).text, f"{path} 没有返回账户外壳"
    assert "Synwevo Docs" in c.get("/").text

    # On a relying party the same paths are redirects to the account service, because a
    # password box that cannot check a password is worse than no page at all.
    rp = _client(tmp_path / "rp", monkeypatch, role="app")
    r = rp.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/auth/start")
    r = rp.get("/account", follow_redirects=False)
    assert r.status_code == 303 and "/account" in r.headers["location"]
