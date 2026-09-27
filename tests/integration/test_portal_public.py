"""Portal public documents and real identity visibility, using isolated test fixtures only."""
from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, now
from synwevo_identity.invites import Invites
from synwevo_server.app import create_app


@pytest.fixture()
def portal(tmp_path, monkeypatch):
    data = tmp_path / "fixture-data"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ROLE", "portal")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    monkeypatch.setenv("SYNWEVO_PORTAL_URL", "https://portal.example.test")
    # Explicit shell fixture: tests run before or after a frontend build, with no production data.
    dist = tmp_path / "fixture-build"
    dist.mkdir()
    source = Path(__file__).resolve().parents[2] / "apps/portal/web/index.html"
    dist.joinpath("index.html").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("SYNWEVO_PORTAL_UI_DIR", str(dist))
    api = create_app(str(data), role="portal", start_jobs=False)
    store = Store(data)
    ident = Identity(store)
    bootstrap_docs_app(store, ident, launch_url="http://docs.localhost:8765")
    return api, store, ident


def user(portal, name="member", *, admin=False, personal=False):
    _, store, ident = portal
    u = ident.create_user(f"{name}@example.test", None, status="active",
                          platform_role="admin" if admin else "none",
                          with_personal_workspace=personal)
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    return ident.get_user(u["id"])


def signed_in(portal, u, wid=None):
    api, store, _ = portal
    client = TestClient(api)
    # The session stands in for a completed password sign-in: `amr` decides what an
    # administrator may do (sessions.admin_capable), so a made-up value would leave the role
    # locked and test a policy the fixture never meant to exercise.
    token, _ = Sessions(store).create(u["id"], workspace_id=wid, amr="password")
    client.cookies.set(COOKIE_NAME, token)
    client.cookies.set(CSRF_COOKIE, "portal-fixture-csrf")
    return client


def switch(client, wid, *, csrf=True):
    return client.post("/api/portal/workspace", json={"workspace_id": wid},
                       headers={CSRF_HEADER: "portal-fixture-csrf"} if csrf else {})


@pytest.mark.parametrize("path,language,title,description,heading", [
    ("/", "zh-Hans", "Synwevo · 工作流程自动化平台",
     "文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。", "让工作，自然相连。"),
    ("/zh-Hans", "zh-Hans", "Synwevo · 工作流程自动化平台",
     "文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。", "让工作，自然相连。"),
    ("/zh-Hant", "zh-Hant", "Synwevo · 工作流程自動化平台",
     "文件自動化、團隊協作與訊息溝通，匯聚於同一個工作空間。", "讓工作，自然相連。"),
    ("/en", "en", "Synwevo · Workflow automation platform",
     "Document automation, team collaboration and messaging, brought together in one workspace.",
     "Work that comes together."),
    ("/privacy", "zh-Hans", "数据与隐私 · Synwevo",
     "Synwevo 如何处理你的账户、文档与消息数据：存储位置、Cookie、保留期限、导出与删除。", None),
    ("/zh-Hant/privacy", "zh-Hant", "資料與私隱 · Synwevo",
     "Synwevo 如何處理你的帳戶、文件與訊息資料：儲存位置、Cookie、保留期限、匯出與刪除。", None),
    ("/en/privacy", "en", None, None, "Your data, plainly stated."),
    ("/en/service", "en", None, None, "What this service is, and how it works."),
    ("/service", "zh-Hans", None, None, None),
    ("/zh-Hant/service", "zh-Hant", None, None, None),
], ids=["root", "zh-Hans", "zh-Hant", "en", "privacy", "privacy-zh-Hant", "privacy-en", "service-en", "service", "service-zh-Hant"])
def test_public_documents_have_localized_content_and_only_public_canonicals(
    portal, platform_root, path, language, title, description, heading,
):
    from synwevo_portal.public import COPY, PUBLIC_PAGES
    copy = COPY[language]["pages"][PUBLIC_PAGES[path]]
    # A row may pin the wording (the brand lines are a product decision) or leave it to the
    # catalogue; either way the served document must carry exactly the catalogue's text.
    title, description, heading = title or copy["title"], description or copy["description"], heading or copy["heading"]
    assert (copy["title"], copy["description"], copy["heading"]) == (title, description, heading)
    import html as _html
    title, description, heading = (_html.escape(x) for x in (title, description, heading))
    if path == "/":
        # SSR replaces these tags, so also guard the defaults it could otherwise conceal.
        source = (platform_root / "apps/portal/web/index.html").read_text(encoding="utf-8")
        assert f'<title>{title}</title>' in source
        assert f'<meta name="description" content="{description}" />' in source
        assert '<html lang="zh-Hans">' in source
        assert 'name="robots" content="noindex, nofollow"' in source
    response = TestClient(portal[0]).get(path)
    assert response.status_code == 200
    assert f'lang="{language}"' in response.text
    assert f'<title>{title}</title>' in response.text
    assert f'<meta name="description" content="{description}" />' in response.text
    assert f'<h1>{heading}</h1>' in response.text
    assert f'<p>{description}</p>' in response.text
    for paragraph in copy["paragraphs"]:
        assert f"<p>{_html.escape(paragraph)}</p>" in response.text, "the no-script fallback carries the page's own summary"
    assert response.text.count('<title>') == 1
    assert response.text.count('<meta name="description"') == 1
    assert response.text.count('<meta name="robots"') == 1
    assert response.headers["X-Robots-Tag"] == "index, follow"
    assert 'name="robots" content="index, follow"' in response.text
    assert "noindex" not in response.text
    assert f'href="/register?lang={language}"' in response.text
    assert f'href="/auth/start?next=%2Fworkspace&amp;lang={language}"' in response.text
    from synwevo_portal.public import public_path
    page = PUBLIC_PAGES[path]
    canonical_path = public_path(page, language)
    assert f'<link rel="canonical" href="https://portal.example.test{canonical_path}" />' in response.text
    assert response.text.count('rel="canonical"') == 1
    for alternate in ("zh-Hans", "zh-Hant", "en", "x-default"):
        suffix = public_path(page, "zh-Hans" if alternate == "x-default" else alternate)
        assert f'<link rel="alternate" hreflang="{alternate}" href="https://portal.example.test{suffix}" />' in response.text
    assert '/workspace"' not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_robots_sitemap_and_private_shells(portal):
    client = TestClient(portal[0])
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /\n" in robots.text
    assert "Allow: /$" in robots.text and "Allow: /en$" in robots.text
    assert "Allow: /privacy$" in robots.text and "Allow: /en/service$" in robots.text and "Allow: /zh-Hant/privacy$" in robots.text
    xml = client.get("/sitemap.xml")
    locations = [node.text for node in ET.fromstring(xml.text).iter() if node.tag.endswith("loc")]
    assert locations == ["https://portal.example.test" + p for p in
                         ("/", "/zh-Hant", "/en", "/privacy", "/zh-Hant/privacy", "/en/privacy", "/service", "/zh-Hant/service", "/en/service")]
    facts = client.get("/public/deployment.json")
    assert facts.status_code == 200 and facts.headers["Cache-Control"] == "no-store"
    assert set(facts.json()) == {"checked_at", "accounts", "docs"}, "runtime facts only: nothing about the request or a session"
    for path in ("/workspace", "/index.html", "/unknown", "/login", "/account", "/admin/api"):
        result = client.get(path, follow_redirects=False)
        assert result.headers["X-Robots-Tag"] == "noindex, nofollow"
        if result.status_code == 200:
            assert 'name="robots" content="noindex, nofollow"' in result.text
            assert 'rel="canonical"' not in result.text
            assert 'hreflang=' not in result.text
    assert client.get("/api/portal/home").status_code == 401
    assert client.get("/api/projects").status_code == 404


@pytest.mark.parametrize("configured", ["", "https://user:pass@example.test", "https://portal.example.test/?secret=x", "javascript:alert(1)"])
def test_no_unconfigured_or_host_injected_canonical(portal, monkeypatch, configured):
    monkeypatch.setenv("SYNWEVO_PORTAL_URL", configured)
    client = TestClient(portal[0], base_url="http://untrusted.example.test")
    page = client.get("/")
    assert 'rel="canonical"' not in page.text
    assert "untrusted.example.test" not in client.get("/sitemap.xml").text
    assert "<url>" not in client.get("/sitemap.xml").text


def test_empty_account_and_platform_admin_do_not_inherit_business_access(portal):
    for name, admin in (("unaffiliated", False), ("platform-admin", True)):
        u = user(portal, name, admin=admin)
        client = signed_in(portal, u)
        assert client.get("/api/auth/me").json()["platform_role"] == ("admin" if admin else "none")
        home = client.get("/api/portal/home").json()
        assert home["workspaces"] == [] and home["apps"] == []
        assert home["pending_invitations"] == []
        assert home["current_workspace_id"] is None
        assert client.get("/api/projects").status_code == 404
        assert client.get("/api/admin/users").status_code == 404


def test_workspace_switch_rechecks_csrf_membership_freezing_and_application_access(portal):
    _, store, ident = portal
    alice = user(portal, "alice")
    owner = user(portal, "owner")
    first = ident.create_workspace("Fixture A", owner_id=alice["id"])
    second = ident.create_workspace("Fixture B", owner_id=alice["id"])
    private = ident.create_workspace("PRIVATE-CANARY", owner_id=owner["id"])
    client = signed_in(portal, alice, first["id"])
    home = client.get("/api/portal/home").json()
    assert {w["id"] for w in home["workspaces"]} == {first["id"], second["id"]}
    assert "PRIVATE-CANARY" not in str(home)
    assert home["apps"] and home["apps"][0]["workspace_id"] == first["id"]
    assert switch(client, second["id"], csrf=False).status_code == 403
    assert switch(client, private["id"]).status_code == 403
    assert switch(client, [first["id"]]).status_code == 400
    assert switch(client, second["id"]).status_code == 200
    assert client.get("/api/auth/me").json()["workspace_id"] == second["id"]
    assert client.get("/api/portal/home").json()["apps"][0]["workspace_id"] == second["id"]
    Apps(store, ident).revoke(second["id"], "docweave", alice["id"])
    assert client.get("/api/portal/home").json()["apps"] == []
    store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (first["id"],))
    assert switch(client, first["id"]).status_code == 403
    store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (second["id"], alice["id"]))
    assert switch(client, second["id"]).status_code == 403
    assert client.get("/api/portal/home").json()["current_workspace_id"] is None


def test_pending_invites_are_real_verified_address_scoped_and_never_tokens(portal):
    _, store, ident = portal
    owner, invitee, other = [user(portal, name) for name in ("inviter", "invitee", "other")]
    invites = Invites(store, ident)
    rows = []
    for name in ("pending", "expired", "accepted", "revoked", "frozen", "deleted", "other-email"):
        ws = ident.create_workspace(f"Fixture {name}", owner_id=owner["id"])
        row, raw = invites.create(workspace_id=ws["id"], email=(other if name == "other-email" else invitee)["email"], role="editor", actor_id=owner["id"])
        rows.append((row, raw))
        if name == "expired": store.execute("UPDATE invitations SET expires_at=? WHERE id=?", (now() - 1, row["id"]))
        if name == "accepted": store.execute("UPDATE invitations SET accepted_at=? WHERE id=?", (now(), row["id"]))
        if name == "revoked": store.execute("UPDATE invitations SET revoked_at=? WHERE id=?", (now(), row["id"]))
        if name == "frozen": store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (ws["id"],))
        if name == "deleted": store.execute("UPDATE workspaces SET deleted_at=? WHERE id=?", (now(), ws["id"]))
    client = signed_in(portal, invitee)
    home = client.get("/api/portal/home").json()
    assert home["workspaces"] == [] and home["apps"] == []
    assert [i["id"] for i in home["pending_invitations"]] == [rows[0][0]["id"]]
    assert set(home["pending_invitations"][0]) == {"id", "workspace_id", "workspace_name", "role", "expires_at"}
    assert all(raw not in str(home) for _, raw in rows)
    store.execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (invitee["id"],))
    assert client.get("/api/portal/home").json()["pending_invitations"] == []
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (invitee["id"],))
    assert client.get("/api/portal/home").status_code == 401


def test_organization_visibility_never_implies_workspace_or_app_access(portal):
    from synwevo_core.organizations import Organizations
    _, store, ident = portal
    owner = user(portal, "org-owner")
    member = user(portal, "org-member")
    outsider = user(portal, "org-outsider", admin=True)
    created = Organizations(store, ident).create(owner["id"], "Fixture Organization", "Fixture Org Space", "portal-org-fixture")
    org, ws = created["organization"], created["workspace"]
    store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at) VALUES (?,?,'admin',?,?)",
                  (org["id"], member["id"], now(), now()))
    member_client = signed_in(portal, member)
    home = member_client.get("/api/portal/home").json()
    assert home["organizations"] == [{"id": org["id"], "name": org["name"], "status": "active", "role": "admin"}]
    assert home["workspaces"] == [] and home["apps"] == []
    assert member_client.get("/api/auth/me").json()["permissions"] == []
    assert switch(member_client, ws["id"]).status_code == 403
    assert signed_in(portal, outsider).get("/api/portal/home").json()["organizations"] == []
    owner_client = signed_in(portal, owner, ws["id"])
    owner_home = owner_client.get("/api/portal/home").json()
    assert owner_home["workspaces"][0]["organization_id"] == org["id"]
    assert owner_home["workspaces"][0]["organization_name"] == org["name"]
    store.execute("UPDATE organizations SET status='frozen' WHERE id=?", (org["id"],))
    assert switch(owner_client, ws["id"]).status_code == 403
    assert owner_client.get("/api/portal/home").json()["apps"] == []


def test_tab_workspace_headers_survive_other_tab_switches(portal):
    _, store, ident = portal
    person = user(portal, "tab-owner")
    one = ident.create_workspace("Fixture tab one", owner_id=person["id"])
    two = ident.create_workspace("Fixture tab two", owner_id=person["id"])
    client = signed_in(portal, person, one["id"])
    selected = client.post("/api/auth/workspace", json={"workspace_id": two["id"]},
        headers={CSRF_HEADER: "portal-fixture-csrf", "X-Synwevo-Workspace": one["id"]})
    assert selected.status_code == 200
    first = client.get("/api/portal/home", headers={"X-Synwevo-Workspace": one["id"]}).json()
    second = client.get("/api/portal/home", headers={"X-Synwevo-Workspace": two["id"]}).json()
    assert first["current_workspace_id"] == first["apps"][0]["workspace_id"] == one["id"]
    assert second["current_workspace_id"] == second["apps"][0]["workspace_id"] == two["id"]
    assert client.get("/api/portal/home", headers={"X-Synwevo-Workspace": "not-a-member"}).status_code == 403


def test_portal_exposes_account_locale_and_only_platform_admin_api_permission(portal):
    member, admin = user(portal, "locale-member"), user(portal, "locale-admin", admin=True)
    for person, permissions in ((member, []), (admin, ["api_platform.manage"])):
        client = signed_in(portal, person)
        assert client.get("/api/auth/me").json()["permissions"] == permissions
        response = client.patch("/api/auth/locale", json={"locale": "en"}, headers={CSRF_HEADER: "portal-fixture-csrf"})
        assert response.status_code == 200
        assert client.get("/api/auth/me").json()["locale"] == "en"


def test_the_deployment_facts_reader_identifies_itself_and_reads_503_bodies():
    """Production reaches the other services through the public edge, which refuses the default
    library User-Agent; the reader must name itself, and a 503 `/readyz` body (a component down)
    is still an answer."""
    import http.server
    import json
    import threading
    from synwevo_portal.public import FACTS_USER_AGENT, _read_json
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.headers.get("User-Agent"))
            if self.headers.get("User-Agent", "").startswith("Python-urllib"):
                self.send_response(403); self.end_headers(); return
            body = json.dumps({"status": "degraded" if self.path == "/down" else "ready", "checks": {"malware_scanner": "down"}}).encode()
            self.send_response(503 if self.path == "/down" else 200)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        assert _read_json(base + "/readyz") == {"status": "ready", "checks": {"malware_scanner": "down"}}
        assert _read_json(base + "/down") == {"status": "degraded", "checks": {"malware_scanner": "down"}}, "a 503 body is read, not dropped"
        assert seen and all(ua == FACTS_USER_AGENT for ua in seen) and FACTS_USER_AGENT.startswith("Synwevo")
    finally:
        server.shutdown(); server.server_close()

