"""Member governance, observed from Synwevo Docs.

The account service removes a member, revokes an entitlement or freezes a workspace; these tests
check the effect where it matters — on the composed server (`synwevo_server.app.create_app`),
through the Docs browser API and the machine API — rather than in the account service's own
tables. Everything is driven through real sessions and real tokens.
"""
from __future__ import annotations

import importlib

import pytest

from tests.integration.platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from synwevo_core import authority, oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now

PW = "a-sufficiently-long-passphrase"
REASON = "项目结束，收回协作者权限"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    carol = ident.create_user("carol@example.com", PW, status="active")
    admin = make_platform_admin(store, ident.create_user("root@example.com", PW, status="active"))
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    ident.add_member(ws, carol["id"], "editor")
    Apps(store, ident).grant(ws, "docweave", carol["id"])
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                                 redirect_uris=["http://docweave.localhost:8765/auth/callback"],
                                 scopes="openid profile email docweave.read docweave.write")
    _, secret = oidc.Clients(store).register(client_id="nightly", app_id="docweave", name="夜间任务",
                                             redirect_uris=[], confidential=True, kind="service",
                                             workspace_id=ws, api_scopes="docweave.read")
    c = {n: _login(api, f"{n}@example.com") for n in ("alice", "carol", "root")}
    pid = _post(c["alice"], "/api/projects", {"name": "CANARY-PROJ-7f3"}).json()["id"]
    return {"api": api, "store": store, "ident": ident, "ws": ws, "alice": alice, "carol": carol,
            "admin": admin, "c": c, "pid": pid, "secret": secret}


def _login(api, email):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _h(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}


def _post(c, path, body=None):
    return c.post(path, json=body if body is not None else {}, headers=_h(c))


def _select(c, ws):
    assert _post(c, "/api/auth/workspace", {"workspace_id": ws}).status_code == 200


def _delegated(world, who="carol"):
    """A delegated access token as the token endpoint would store it (Docs acting for a person)."""
    raw = new_id("raw")
    world["store"].execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
        "access_expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id("tok"), "docweave-web", world[who]["id"], oidc._sha(raw), "docweave.read docweave.write",
         world["ws"], now(), now() + 900, new_id("fam")))
    return raw


def _v1(world, token, path="/api/v1/projects"):
    return TestClient(world["api"]).get(path, headers={"Authorization": f"Bearer {token}"})


def _names(resp) -> list[str]:
    body = resp.json()
    items = body["items"] if isinstance(body, dict) else body
    return [p["name"] for p in items]


# ---------------------------------------------------------------- removal
def test_a_removed_member_loses_the_workspace_on_the_next_request(world):
    carol, pid = world["c"]["carol"], world["pid"]
    _select(carol, world["ws"])
    assert carol.get(f"/api/projects/{pid}").status_code == 200
    token = _delegated(world)
    assert "CANARY-PROJ-7f3" in _names(_v1(world, token))

    alice = world["c"]["alice"]
    assert _post(alice, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    r = _post(alice, f"/api/auth/workspaces/{world['ws']}/members/{world['carol']['id']}/remove", {"reason": REASON})
    assert r.status_code == 200, r.text

    assert carol.get(f"/api/projects/{pid}").status_code == 404
    assert "CANARY-PROJ-7f3" not in str(carol.get("/api/projects").json())
    assert _v1(world, token).status_code in (401, 403)
    # and she cannot simply select it again
    assert _post(carol, "/api/auth/workspace", {"workspace_id": world["ws"]}).status_code == 404


# ---------------------------------------------------------------- entitlement
def test_a_revoked_entitlement_stops_deferred_work_and_delegated_tokens(world):
    token = _delegated(world)
    alice = world["c"]["alice"]
    r = _post(alice, f"/api/auth/workspaces/{world['ws']}/members/{world['carol']['id']}/entitlements",
              {"app_id": "docweave", "grant": False})
    assert r.status_code == 200 and r.json()["tokens_revoked"] == 1
    assert _v1(world, token).status_code == 401
    desc = {"kind": "user", "user_id": world["carol"]["id"], "tenant": world["ws"]}
    assert authority.check(world["store"], desc, need="read") == (False, "entitlement_revoked")


# ---------------------------------------------------------------- freezing
def test_a_frozen_workspace_refuses_machine_callers_and_deferred_work(world):
    c = TestClient(world["api"])
    data = {"grant_type": "client_credentials", "client_id": "nightly", "client_secret": world["secret"]}
    tok = c.post("/oidc/token", data=data).json()["access_token"]
    assert _v1(world, tok).status_code == 200

    root = world["c"]["root"]
    assert _post(root, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    r = _post(root, f"/api/admin/workspaces/{world['ws']}/freeze", {"reason": "滥用调查期间冻结"})
    assert r.status_code == 200, r.text
    assert "CANARY" not in r.text, "冻结的回应只有计数，没有内容"

    assert _v1(world, tok).status_code == 401
    again = c.post("/oidc/token", data=data)
    assert again.status_code == 400 and again.json()["error"] == "invalid_client"
    desc = {"kind": "user", "user_id": world["alice"]["id"], "tenant": world["ws"]}
    assert authority.check(world["store"], desc, need="read") == (False, "workspace_frozen")
    # nobody can select the frozen workspace through the account side either
    assert world["ident"].get_workspace(world["ws"])["status"] == "frozen"


# ---------------------------------------------------------------- deleting (A44)
def test_a_deleted_workspace_refuses_sessions_machine_callers_and_deferred_work(world):
    """The platform deletes a team workspace through the console. Every way in is refused — a
    session still naming it, a per-tab header, switching to it, its service account, a delegated
    token, deferred work — and a recovery gives back what was refused live (membership, the
    service account) but not what was withdrawn (tokens)."""
    store, ident, carol_id = world["store"], world["ident"], world["carol"]["id"]
    team = ident.create_workspace("团队空间（夹具）", owner_id=world["alice"]["id"])["id"]
    ident.add_member(team, carol_id, "editor")
    _, secret = oidc.Clients(store).register(client_id="team-nightly", app_id="docweave", name="团队夜间任务",
                                             redirect_uris=[], confidential=True, kind="service",
                                             workspace_id=team, api_scopes="docweave.read")
    c = TestClient(world["api"])
    data = {"grant_type": "client_credentials", "client_id": "team-nightly", "client_secret": secret}
    service = c.post("/oidc/token", data=data).json()["access_token"]
    raw = new_id("raw")
    # FIXTURE: a delegated token in the team workspace, as the token endpoint would store it.
    store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
                  "access_expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
                  (new_id("tok"), "docweave-web", carol_id, oidc._sha(raw), "docweave.read docweave.write",
                   team, now(), now() + 900, new_id("fam")))
    carol = world["c"]["carol"]
    _select(carol, team)
    assert carol.get("/api/projects").status_code == 200
    assert _v1(world, service).status_code == 200 and _v1(world, raw).status_code == 200

    root = world["c"]["root"]
    assert _post(root, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    preview = _post(root, f"/api/admin/workspaces/{team}/lifecycle/preview", {"action": "delete"})
    assert preview.status_code == 200 and preview.json()["impact"]["service_accounts"] == 1, preview.text
    r = _post(root, f"/api/admin/workspaces/{team}/lifecycle/delete",
              {"reason": REASON, "preview_token": preview.json()["preview_token"],
               "idempotency_key": "a44_members_access_0001"})
    assert r.status_code == 200 and r.json()["withdrawn"]["sessions"] >= 1, r.text

    # the session was moved off it; one still naming it (FIXTURE: a row the move did not see) is refused
    assert carol.get("/api/auth/me").json()["workspace_id"] != team
    store.execute("UPDATE sessions SET workspace_id=? WHERE user_id=? AND revoked_at IS NULL", (team, carol_id))
    refused = carol.get("/api/projects")
    assert refused.status_code == 403 and refused.json()["error"] == "workspace_unavailable", refused.text
    assert refused.json()["detail"]["workspace_status"] == "deleted"
    store.execute("UPDATE sessions SET workspace_id=NULL WHERE user_id=?", (carol_id,))
    pinned = carol.get("/api/projects", headers={"X-Synwevo-Workspace": team})
    assert pinned.status_code == 403 and pinned.json()["error"] == "workspace_context_invalid", pinned.text
    switch = _post(carol, "/api/auth/workspace", {"workspace_id": team})
    assert switch.status_code == 404 and switch.json()["error"] == "no_workspace", switch.text
    # machine callers: the service account's token and the delegated token are revoked, a new
    # service token is refused, and deferred work of any principal there is refused
    assert _v1(world, service).status_code == 401 and _v1(world, raw).status_code == 401
    again = c.post("/oidc/token", data=data)
    assert again.status_code in (400, 401) and again.json()["error"] == "invalid_client", again.text
    for desc in ({"kind": "user", "user_id": carol_id, "tenant": team},
                 {"kind": "delegated", "user_id": carol_id, "client_id": "docweave-web", "tenant": team},
                 {"kind": "service_account", "client_id": "team-nightly", "tenant": team}):
        assert authority.check(store, desc, need="read") == (False, "workspace_deleted"), desc

    # recovered: refused live comes back, withdrawn does not
    preview = _post(root, f"/api/admin/workspaces/{team}/lifecycle/preview", {"action": "recover"})
    r = _post(root, f"/api/admin/workspaces/{team}/lifecycle/recover",
              {"reason": REASON, "preview_token": preview.json()["preview_token"],
               "idempotency_key": "a44_members_access_0002"})
    assert r.status_code == 200 and r.json()["workspace"]["status"] == "active", r.text
    _select(carol, team)
    assert carol.get("/api/projects").status_code == 200
    fresh = c.post("/oidc/token", data=data)
    assert fresh.status_code == 200 and _v1(world, fresh.json()["access_token"]).status_code == 200
    assert _v1(world, raw).status_code == 401, "a withdrawn token stays withdrawn"


# ---------------------------------------------------------------- F324: administration is not content access
def test_a_platform_admin_cannot_read_a_workspace_they_do_not_belong_to(world):
    root, pid = world["c"]["root"], world["pid"]
    assert root.get(f"/api/projects/{pid}").status_code == 404
    assert "CANARY-PROJ-7f3" not in str(root.get("/api/projects").json())
    # nor through the member API, nor the admin console
    assert root.get(f"/api/auth/workspaces/{world['ws']}/members").status_code == 404
    for path in ("/api/admin/overview", "/api/admin/workspaces", f"/api/admin/workspaces/{world['ws']}/members",
                 "/api/admin/users", "/api/admin/audit"):
        r = root.get(path)
        assert r.status_code == 200 and "CANARY" not in r.text, path
