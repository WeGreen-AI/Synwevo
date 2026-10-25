"""The licence and the Corresponding Source offer reach every remote user (AGPL-3.0 §13).

Measured before this: with `SYNWEVO_UI_AUTH=session` (production) both `/api/about` and
`/api/source-code` answered 401 「请先登录」, and only the Docs role served them at all — so a
person who used only the sign-in page, the public site, the workspace or the conversations of the
same program was never offered its source. Every role now serves both paths, without a session,
in every authentication mode; nothing else becomes anonymous with them.
"""
from __future__ import annotations

import importlib
import io
import tarfile

import pytest
from fastapi.testclient import TestClient

ROLES = ["all", "accounts", "portal", "app", "messages"]


def _client(data_dir, monkeypatch, *, mode: str, role: str) -> TestClient:
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    monkeypatch.setenv("SYNWEVO_ROLE", role)
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.create_app(str(data_dir), start_jobs=False))


@pytest.fixture()
def small_tree(tmp_path, monkeypatch):
    """TEST FIXTURE: a two-file installation, so each role's archive is built in milliseconds."""
    from synwevo_docs import licensing
    root = tmp_path / "installed"
    (root / "apps" / "docs").mkdir(parents=True)
    (root / "apps" / "docs" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "LICENSE").write_text("licence text\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", root)
    return root


@pytest.mark.parametrize("mode", ["session", "basic"])
@pytest.mark.parametrize("role", ROLES)
def test_every_role_offers_the_source_without_a_sign_in(tmp_path, monkeypatch, small_tree, mode, role):
    c = _client(tmp_path / f"{mode}-{role}", monkeypatch, mode=mode, role=role)
    about = c.get("/api/about")
    assert about.status_code == 200, (role, mode, about.text)
    body = about.json()
    assert body["license"] == "AGPL-3.0-or-later" and body["source_offer"] is True
    assert "AGPL-3.0-or-later" in body["license_notice"]
    archive = c.get("/api/source-code")
    assert archive.status_code == 200, (role, mode, archive.text)
    assert archive.headers["content-type"] == "application/gzip"
    names = tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz").getnames()
    assert any(n.endswith("/apps/docs/x.py") for n in names) and any(n.endswith("/SOURCE_OFFER.txt") for n in names)


@pytest.mark.parametrize("role", ROLES)
def test_the_offer_opens_no_other_path(tmp_path, monkeypatch, small_tree, role):
    """Exact paths: a neighbour of the offer, and the business API beside it, still need a session."""
    c = _client(tmp_path / role, monkeypatch, mode="session", role=role)
    for path in ("/api/about/", "/api/aboutx", "/api/source-code/x", "/api/projects"):
        assert c.get(path).status_code in (401, 404), path


def test_the_archive_is_rate_limited_per_address(tmp_path, monkeypatch, small_tree):
    from synwevo_docs import licensing
    monkeypatch.setattr(licensing, "DOWNLOADS_PER_MIN", 2)
    c = _client(tmp_path / "limited", monkeypatch, mode="session", role="accounts")
    assert [c.get("/api/source-code").status_code for _ in range(3)] == [200, 200, 429]
    refused = c.get("/api/source-code").json()
    assert refused["error"] == "too_many_attempts" and refused["detail"]["retry_after"] >= 1


def test_switching_the_offer_off_is_said_on_every_role(tmp_path, monkeypatch, small_tree):
    monkeypatch.setenv("SYNWEVO_DOCS_SOURCE_OFFER", "off")
    for role in ROLES:
        c = _client(tmp_path / f"off-{role}", monkeypatch, mode="session", role=role)
        r = c.get("/api/source-code")
        assert r.status_code == 404 and r.json()["error"] == "source_offer_off", role
        assert c.get("/api/about").json()["source_offer"] is False


def test_every_shell_links_to_the_offer():
    """§13 asks for a *prominent* offer. The archive is linked where people arrive: the footer of the
    public site and the workspace (one portal shell), the footer of every sign-in, sign-up and
    recovery page (the account frame), and the Docs page about licences, which also shows the
    licence itself."""
    from synwevo_core.http import repo_root
    root = repo_root()
    for rel in ("apps/portal/web/src/Portal.tsx", "services/identity/web/src/pages/org/AuthFlow.tsx",
                "apps/docs/web/src/pages/About.tsx"):
        text = (root / rel).read_text(encoding="utf-8")
        assert 'href="/api/source-code" download' in text, f"{rel} 没有链接到对应源代码"
    about = (root / "apps/docs/web/src/pages/About.tsx").read_text(encoding="utf-8")
    assert "a.license_notice" in about and "a.notice" in about, "关于页要显示本程序的许可与 AGPL 组件说明"
