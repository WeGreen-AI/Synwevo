"""Real OIDC workspace handoff across isolated ASGI origins and cookie jars.

The provider transport is the actual identity HTTP app, never a token/login mock.
Each test uses a new local data directory. No network, external mail, or real data.
Browser sessionStorage consumption is owned/tested by the shared frontend; here
we verify its server marker and the authorized /me header contract.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient

from synwevo_core.apps import Apps, DOCS_APP_ID, PLATFORM_APP_ID
from synwevo_core.identity import Identity
from synwevo_core.oidc import Clients
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_server.app import create_app

PW = "isolated-workspace-launch-test-passphrase"
ACCOUNTS = "http://accounts.localhost:8770"
DOCS = "http://docweave.localhost:8765"
PORTAL = "http://portal.localhost:8769"
CLIENT = DOCS_APP_ID + "-web"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    root = tmp_path / "isolated-sso"
    for key, value in {"SYNWEVO_DATA_DIR": str(root), "SYNWEVO_UI_AUTH": "session",
                       "SYNWEVO_TEST_INSTANCE": "1", "SYNWEVO_COOKIE_SECURE": "0",
                       "SYNWEVO_ISSUER": ACCOUNTS, "SYNWEVO_APP_URL": DOCS,
                       "SYNWEVO_OIDC_APP_ID": DOCS_APP_ID, "SYNWEVO_OIDC_CLIENT_ID": CLIENT}.items():
        monkeypatch.setenv(key, value)
    accounts_api = create_app(str(root), role="accounts", start_jobs=False)
    accounts = TestClient(accounts_api, base_url=ACCOUNTS, follow_redirects=False)
    transport = TestClient(accounts_api, base_url=ACCOUNTS, follow_redirects=False)
    from synwevo_auth_client.rp import Rp
    # A transport substitution only: real HTTP endpoints issue and verify every
    # authorization code, PKCE verifier, nonce, signature and account session.
    monkeypatch.setattr(Rp, "http", property(lambda self: transport))
    docs = TestClient(create_app(str(root), role="app", start_jobs=False), base_url=DOCS, follow_redirects=False)
    portal = TestClient(create_app(str(root), role="portal", start_jobs=False), base_url=PORTAL, follow_redirects=False)
    store = Store(root)
    ident = Identity(store)
    alice = ident.create_user("alice@example.test", PW, status="active")
    outsider = ident.create_user("outsider@example.test", PW, status="active")
    personal = ident.workspaces_for(alice["id"])[0]["id"]
    other = ident.workspaces_for(outsider["id"])[0]["id"]
    organization = Organizations(store).create(alice["id"], "SSO fixture organization", "Selected workspace", "sso-fixture-create")
    selected = organization["workspace"]["id"]
    for cid, aid, origin in ((CLIENT, DOCS_APP_ID, DOCS), ("portal-web", PLATFORM_APP_ID, PORTAL)):
        Clients(store).register(client_id=cid, app_id=aid, name="SSO test fixture",
                               redirect_uris=[origin + "/auth/callback"], scopes="openid profile email")
    result = accounts.post("/api/auth/login", json={"email": alice["email"], "password": PW})
    assert result.status_code == 200, result.text
    data = {"store": store, "ident": ident, "apps": Apps(store, ident), "alice": alice,
            "personal": personal, "selected": selected, "other": other,
            "oid": organization["organization"]["id"], "accounts": accounts, "docs": docs, "portal": portal}
    # Complete real portal SSO with that service's per-process configuration.
    # The test shares a process, so scope its environment for this round trip.
    with monkeypatch.context() as config:
        config.setenv("SYNWEVO_APP_URL", PORTAL)
        config.setenv("SYNWEVO_OIDC_APP_ID", PLATFORM_APP_ID)
        config.setenv("SYNWEVO_OIDC_CLIENT_ID", "portal-web")
        start = portal.get("/auth/start?next=/")
        authorized = accounts.get(start.headers["location"])
        callback = portal.get(authorized.headers["location"])
        assert callback.status_code == 303 and callback.headers["location"] == "/", callback.text
    try:
        yield data
    finally:
        for client in (accounts, transport, docs, portal):
            client.close()


def begin(world, wid=None, *, next_path="/"):
    params = {"next": next_path}
    if wid is not None:
        params["workspace_id"] = wid
    response = world["docs"].get("/auth/start?" + urlencode(params))
    assert response.status_code == 303, response.text
    return response.headers["location"]


def authorize(world, url):
    response = world["accounts"].get(url)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def finish(world, wid=None, *, next_path="/"):
    return world["docs"].get(authorize(world, begin(world, wid, next_path=next_path)))


def post(client, path, body):
    return client.post(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def replace_query(url, **changes):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query.update(changes)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def test_portal_launch_hands_selected_workspace_to_existing_docs_session(world):
    docs, portal, selected = world["docs"], world["portal"], world["selected"]
    assert finish(world, world["personal"]).status_code == 303
    previous = docs.cookies.get(COOKIE_NAME)
    assert docs.get("/api/auth/me").json()["workspace_id"] == world["personal"]
    assert post(portal, "/api/portal/workspace", {"workspace_id": selected}).status_code == 200
    home = portal.get("/api/portal/home").json()
    card = next(app for app in home["apps"] if app["id"] == DOCS_APP_ID)
    launch = urlsplit(card["launch_url"])
    assert launch.path == "/auth/start" and dict(parse_qsl(launch.query))["workspace_id"] == selected
    start = docs.get(card["launch_url"])
    auth_url = start.headers["location"]
    assert dict(parse_qsl(urlsplit(auth_url).query))["workspace_id"] == selected
    rid = docs.cookies.get("dw_authreq")
    assert world["store"].one("SELECT workspace_id FROM rp_auth_requests WHERE id=?", (rid,))["workspace_id"] == selected
    callback = docs.get(authorize(world, auth_url))
    assert callback.status_code == 303
    assert dict(parse_qsl(urlsplit(callback.headers["location"]).query)) == {"synwevo_workspace": selected}
    assert docs.cookies.get(COOKIE_NAME) != previous
    assert docs.cookies.get(COOKIE_NAME) != world["accounts"].cookies.get(COOKIE_NAME)
    assert docs.get("/api/auth/me").json()["workspace_id"] == selected
    assert world["store"].one("SELECT workspace_id FROM oauth_tokens WHERE client_id=? ORDER BY issued_at DESC LIMIT 1",
                              (CLIENT,))["workspace_id"] == selected
    # The frontend must override its stale personal-tab selection with this hint
    # on the first authorized refresh. A query hint cannot bypass this header gate.
    me = docs.get("/api/auth/me", headers={"X-Synwevo-Workspace": selected})
    assert me.status_code == 200 and me.json()["workspace_id"] == selected
    forged = docs.get("/api/auth/me", headers={"X-Synwevo-Workspace": world["other"]})
    assert forged.status_code == 403


def test_provider_refuses_unowned_explicit_workspace_without_default_fallback(world):
    callback_url = authorize(world, begin(world, world["other"]))
    assert dict(parse_qsl(urlsplit(callback_url).query))["error"] == "access_denied"
    response = world["docs"].get(callback_url)
    assert response.status_code == 303 and response.headers["location"].startswith("/sign-in-failed?")
    assert world["docs"].cookies.get(COOKIE_NAME) is None


def test_forged_provider_workspace_cannot_replace_server_bound_request(world):
    # The request is bound to a workspace Alice cannot enter. Altering the provider
    # hint to her own personal space must not make its callback fall back there.
    bound = begin(world, world["other"])
    altered = replace_query(bound, workspace_id=world["personal"])
    response = world["docs"].get(authorize(world, altered))
    assert response.status_code == 303 and response.headers["location"].startswith("/no-access?")
    assert world["docs"].cookies.get(COOKIE_NAME) is None
    assert world["store"].one("SELECT COUNT(*) AS n FROM sessions WHERE oidc_sid IS NOT NULL")["n"] == 1  # portal only


def test_callback_workspace_parameter_cannot_override_bound_selection(world):
    callback = authorize(world, begin(world, world["selected"]))
    forged = replace_query(callback, workspace_id=world["other"], synwevo_workspace=world["other"])
    result = world["docs"].get(forged)
    assert result.status_code == 303
    assert dict(parse_qsl(urlsplit(result.headers["location"]).query))["synwevo_workspace"] == world["selected"]
    assert world["docs"].get("/api/auth/me").json()["workspace_id"] == world["selected"]


def test_callback_rechecks_withdrawn_entitlement_without_selecting_another_workspace(world):
    assert finish(world, world["personal"]).status_code == 303
    previous_cookie = world["docs"].cookies.get(COOKIE_NAME)
    before = world["store"].one("SELECT COUNT(*) AS n FROM sessions WHERE oidc_sid IS NOT NULL")["n"]
    callback = authorize(world, begin(world, world["selected"]))
    world["apps"].revoke(world["selected"], DOCS_APP_ID, world["alice"]["id"])
    response = world["docs"].get(callback)
    assert response.status_code == 303 and response.headers["location"].startswith("/no-access?")
    assert world["docs"].cookies.get(COOKIE_NAME) == previous_cookie
    assert world["store"].one("SELECT COUNT(*) AS n FROM sessions WHERE oidc_sid IS NOT NULL")["n"] == before
    assert world["apps"].check_access(world["alice"]["id"], DOCS_APP_ID, world["personal"])[0]
    # The successful code exchange and used request cannot be replayed into a new session.
    assert world["docs"].get(callback).status_code == 400


def test_deep_link_without_explicit_workspace_retains_legacy_selection(world):
    target = "/projects/fixture?filter=a%20b&flag#section"
    response = finish(world, next_path=target)
    assert response.status_code == 303 and response.headers["location"] == target
    assert world["docs"].get("/api/auth/me").json()["workspace_id"] == world["personal"]


def test_landing_marker_is_bound_and_preserves_other_deep_link_components(world):
    target = "/projects/fixture?view=table&synwevo_workspace=forged&synwevo_workspace=another#section"
    response = finish(world, world["selected"], next_path=target)
    landing = urlsplit(response.headers["location"])
    assert landing.path == "/projects/fixture" and landing.fragment == "section"
    assert parse_qsl(landing.query) == [("view", "table"), ("synwevo_workspace", world["selected"])]


@pytest.mark.parametrize("query", ["workspace_id=", "workspace_id=a&workspace_id=b", "workspace_id=has%20space"])
def test_invalid_or_ambiguous_workspace_selector_never_starts_a_request(world, query):
    before = world["store"].one("SELECT COUNT(*) AS n FROM rp_auth_requests")["n"]
    response = world["docs"].get("/auth/start?" + query)
    assert response.status_code == 400 and response.json()["error"] == "invalid_field"
    assert world["store"].one("SELECT COUNT(*) AS n FROM rp_auth_requests")["n"] == before


@pytest.mark.parametrize("ambiguous", [False, True])
def test_provider_independently_validates_selector_before_issuing_code(world, ambiguous):
    url = begin(world, world["selected"])
    url = (url + "&workspace_id=" + world["personal"]) if ambiguous else replace_query(url, workspace_id="")
    before = world["store"].one("SELECT COUNT(*) AS n FROM oauth_codes")["n"]
    response = world["accounts"].get(url)
    assert response.status_code == 303
    assert dict(parse_qsl(urlsplit(response.headers["location"]).query))["error"] == "invalid_request"
    assert world["store"].one("SELECT COUNT(*) AS n FROM oauth_codes")["n"] == before


def test_provider_cannot_launch_workspace_in_frozen_organization(world):
    Organizations(world["store"]).transition(world["oid"], world["alice"]["id"], "freeze",
                                              reason="隔离测试夹具：组织已冻结")
    response = world["accounts"].get(begin(world, world["selected"]))
    assert response.status_code == 303
    assert dict(parse_qsl(urlsplit(response.headers["location"]).query))["error"] == "access_denied"
    assert world["docs"].cookies.get(COOKIE_NAME) is None


def test_workspace_switch_checks_live_organization_membership(world):
    alice, selected = world["alice"]["id"], world["selected"]
    # Isolated stale workspace membership fixture: removes only the org row so
    # endpoint authorization, rather than removal cleanup, must stop the switch.
    world["store"].execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?",
                           (world["oid"], alice))
    assert world["ident"].membership(selected, alice)["role"] == "owner"
    for client, endpoint in ((world["accounts"], "/api/auth/workspace"), (world["portal"], "/api/portal/workspace")):
        before = client.get("/api/auth/me").json()["workspace_id"]
        response = post(client, endpoint, {"workspace_id": selected})
        assert response.status_code == 403 and response.json()["error"] == "not_an_organization_member"
        assert client.get("/api/auth/me").json()["workspace_id"] == before
    assert world["accounts"].get("/api/auth/me", headers={"X-Synwevo-Workspace": selected}).status_code == 403


@pytest.mark.parametrize("locale", ["en", "zh-Hant", "zh-Hans"])
def test_anonymous_locale_intent_reaches_login_and_preserves_original_next(world, locale):
    world["accounts"].cookies.clear()
    next_path = "/projects/fixture?filter=a%20b&flag#section"
    response = world["docs"].get("/auth/start?" + urlencode({"lang": locale, "next": next_path}))
    auth_url = response.headers["location"]
    assert dict(parse_qsl(urlsplit(auth_url).query))["ui_locales"] == locale
    login = world["accounts"].get(auth_url)
    assert login.status_code == 303
    parts = urlsplit(login.headers["location"])
    query = dict(parse_qsl(parts.query))
    assert parts.path == "/login" and query["lang"] == locale
    assert query["next"] == urlsplit(auth_url).path + "?" + urlsplit(auth_url).query
    # The existing user's saved choice wins after the anonymous ceremony.
    world["store"].execute("UPDATE users SET locale='zh-Hant' WHERE id=?", (world["alice"]["id"],))
    assert world["accounts"].post("/api/auth/login", json={"email": world["alice"]["email"], "password": PW}).status_code == 200
    authorized = world["accounts"].get(query["next"])
    callback = world["docs"].get(authorized.headers["location"])
    assert callback.status_code == 303 and callback.headers["location"] == next_path
    assert world["docs"].get("/api/auth/me").json()["locale"] == "zh-Hant"


@pytest.mark.parametrize("query", ["lang=fr", "lang=", "lang=en&lang=zh-Hant"])
def test_unsupported_or_ambiguous_locale_never_creates_sso_request(world, query):
    before = world["store"].one("SELECT COUNT(*) AS n FROM rp_auth_requests")["n"]
    result = world["docs"].get("/auth/start?" + query)
    assert result.status_code == 400 and result.json()["detail"]["field"] == "lang"
    assert world["store"].one("SELECT COUNT(*) AS n FROM rp_auth_requests")["n"] == before


def test_provider_does_not_forward_unvalidated_locale_to_login(world):
    world["accounts"].cookies.clear()
    auth_url = replace_query(begin(world), ui_locales="en&next=//outside.invalid")
    response = world["accounts"].get(auth_url)
    assert response.status_code == 303
    assert dict(parse_qsl(urlsplit(response.headers["location"]).query))["error"] == "invalid_request"


@pytest.mark.parametrize('withdrawal', ['logout', 'reset', 'expiry'])
def test_late_callback_cannot_recreate_a_withdrawn_device(world, monkeypatch, withdrawal):
    """Real OIDC exchange, with only the post-exchange scheduling gap injected."""
    from synwevo_auth_client.rp import Rp
    from synwevo_core.sessions import Sessions
    from synwevo_core.store import now
    callback = authorize(world, begin(world, world['selected']))
    original = Rp.complete
    root_id = world['accounts'].get('/api/auth/me').json()['session']['id']
    def delayed(self, **kwargs):
        result = original(self, **kwargs)
        if withdrawal == 'logout':
            Sessions(world['store']).revoke_device(root_id)
        elif withdrawal == 'reset':
            Sessions(world['store']).revoke_all_for_user(world['alice']['id'], 'password_reset')
        else:
            world['store'].execute('UPDATE sessions SET idle_expires_at=? WHERE id=?', (now()-1, root_id))
        return result
    monkeypatch.setattr(Rp, 'complete', delayed)
    response = world['docs'].get(callback)
    assert response.status_code == 403 and response.json()['error'] == 'session_revoked'
    assert not world['docs'].cookies.get(COOKIE_NAME)
    assert world['docs'].get('/api/auth/me').status_code == 401
    assert world['docs'].get('/api/projects').status_code == 401
    assert not world['store'].one('SELECT 1 FROM sessions s JOIN client_sessions c ON c.sid=s.oidc_sid '
                                 'WHERE s.user_id=? AND c.client_id=? AND s.revoked_at IS NULL',
                                 (world['alice']['id'], CLIENT))


def test_callback_records_the_live_central_device(world):
    response = finish(world, world['selected'])
    assert response.status_code == 303
    me = world['docs'].get('/api/auth/me').json()
    row = world['store'].one('SELECT central_session_id FROM sessions WHERE id=?', (me['session']['id'],))
    root_id = world['accounts'].get('/api/auth/me').json()['session']['id']
    assert row['central_session_id'] == root_id
