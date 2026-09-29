"""Undoing the assignment of the historical data, observed from Synwevo Docs.

The account service's own tests check the rows; these check the effect where it matters — on the
composed server (`synwevo_server.app.create_app`), through the former owner's existing browser
session and tokens, and through machine credentials — and that assigning the data again restores
access the normal way. Fixtures are isolated (tmp_path); accounts and rows are fixture data.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.api_platform import Approvals
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.machine import docs_resource
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now

PW = "a-sufficiently-long-passphrase"
LEGACY = "default"
CANARY = "CANARY-LEGACY-5a1"


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
    admin = ident.create_user("root@example.com", PW, status="active", platform_role="admin")
    # Fixture: the pre-identity data, with one project carrying tenant='default'.
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES (?,'历史数据（待认领）','legacy','pending_claim',?,?)", (LEGACY, ts, ts))
    store.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant) VALUES ('p_legacy',?,?,?,?)",
                  (CANARY, ts, ts, LEGACY))
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                                 redirect_uris=["http://docweave.localhost:8765/auth/callback"],
                                 scopes="openid profile email docweave.read docweave.write")
    _, secret = oidc.Clients(store).register(client_id="legacy-batch", app_id="docweave", name="历史数据批处理",
                                             redirect_uris=[], confidential=True, kind="service",
                                             workspace_id=LEGACY, api_scopes="docweave.read")
    c = {"alice": _login(api, "alice@example.com"), "root": _login(api, "root@example.com")}
    return {"api": api, "store": store, "ident": ident, "alice": alice, "admin": admin, "c": c, "secret": secret}


def _login(api, email):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def _post(c, path, body=None):
    return c.post(path, json=body if body is not None else {}, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def _admin(world, action, body):
    root = world["c"]["root"]
    assert _post(root, "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    r = _post(root, f"/api/admin/workspaces/{LEGACY}/{action}", body)
    assert r.status_code == 200, r.text
    return r.json()


def _claim(world):
    return _admin(world, "claim", {"user_id": world["alice"]["id"], "reason": "交给历史数据原本的所有者"})


def _unclaim(world):
    return _admin(world, "unclaim", {"reason": "指派错了账户，先撤销再重新指派"})


def _delegated(world):
    """A delegated access token as the token endpoint would store it (Docs acting for alice)."""
    raw = new_id("raw")
    world["store"].execute(
        "INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,scope,workspace_id,issued_at,"
        "access_expires_at,family_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (new_id("tok"), "docweave-web", world["alice"]["id"], oidc._sha(raw), "docweave.read docweave.write",
         LEGACY, now(), now() + 900, new_id("fam")))
    return raw


def _service_token(world):
    return TestClient(world["api"]).post("/oidc/token", data={
        "grant_type": "client_credentials", "client_id": "legacy-batch", "client_secret": world["secret"],
        "scope": "docweave.read"})


def _v1(world, token, path="/api/v1/projects"):
    return TestClient(world["api"]).get(path, headers={"Authorization": f"Bearer {token}"})


def _names(resp) -> list[str]:
    body = resp.json()
    items = body["items"] if isinstance(body, dict) else body
    return [p["name"] for p in items]


def _approved_key(world):
    """An API key issued under an owner's approval, as the console issues it (bound in the same
    transaction that creates the key)."""
    store, alice = world["store"], world["alice"]["id"]
    approvals = Approvals(store)
    row = approvals.create(world["admin"]["id"], {"workspace_id": LEGACY, "name": "历史导出", "credential_kind": "api_key",
                                                  "scopes": ["docweave.read"], "resources": [docs_resource()]}, "fixture approval")
    approvals.decide(row["id"], alice, "approve", {"scopes": ["docweave.read"], "resources": [docs_resource()]}, "同意导出")
    with store.tx():
        token, key = Auth(store).create_key("历史导出", LEGACY, ("read",))
        approvals.bind(row["id"], "api_key", key["id"])
    return token


def test_after_the_undo_the_former_owner_is_refused_and_a_new_claim_lets_them_back(world):
    store, alice_c, root = world["store"], world["c"]["alice"], world["c"]["root"]
    _claim(world)

    # assigned: the former owner works in it through the browser and through a delegation
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
    assert alice_c.get("/api/projects/p_legacy").status_code == 200
    assert CANARY in _names(alice_c.get("/api/projects"))
    token = _delegated(world)
    assert CANARY in _names(_v1(world, token))

    out = _unclaim(world)
    assert out["removed_members"][0]["email"] == "alice@example.com" and out["tokens_revoked"] >= 1

    # her existing session: the workspace it pointed at is gone from it, nothing of the data reads
    assert alice_c.get("/api/projects/p_legacy").status_code == 404
    assert CANARY not in str(alice_c.get("/api/projects").json())
    sid_ws = store.all("SELECT workspace_id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (world["alice"]["id"],))
    assert LEGACY not in {r["workspace_id"] for r in sid_ws}, "会话里过期的工作空间在下一次请求时被清除"
    tab = alice_c.get("/api/projects/p_legacy", headers={"X-Synwevo-Workspace": LEGACY})
    assert tab.status_code == 403 and tab.json()["error"] == "workspace_context_invalid"
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code in (403, 404)
    assert LEGACY not in [w["id"] for w in alice_c.get("/api/auth/me").json()["workspaces"]]
    # her existing token
    assert _v1(world, token).status_code == 401
    # and nobody else, platform administrators included
    assert root.get("/api/projects/p_legacy").status_code == 404
    assert CANARY not in str(root.get("/api/projects").json())
    # nothing was deleted
    assert store.one("SELECT name, deleted FROM projects WHERE id='p_legacy'") == {"name": CANARY, "deleted": 0}

    # assigned again: access comes back the normal way, through the same session
    _claim(world)
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
    assert alice_c.get("/api/projects/p_legacy").status_code == 200
    assert CANARY in _names(alice_c.get("/api/projects"))
    # a revoked token stays revoked; a new delegation works
    assert _v1(world, token).status_code == 401
    assert CANARY in _names(_v1(world, _delegated(world)))


def test_machine_credentials_follow_the_rules_already_in_force(world):
    """Service accounts stop because the workspace is no longer active with Docs on
    (`check_access_for_workspace`); an approved key stops because its approver is no longer an
    owner (`Approvals.require_owner`); an operator's `dw_` key follows the documented
    `pending_claim` rule, as it did before the claim. Nothing is deleted or newly revoked."""
    _claim(world)
    service = _service_token(world)
    assert service.status_code == 200, service.text
    service = service.json()["access_token"]
    approved = _approved_key(world)
    operator, _ = Auth(world["store"]).create_key("运维脚本", LEGACY, ("read",))
    for credential in (service, approved, operator):
        assert CANARY in _names(_v1(world, credential))

    _unclaim(world)
    assert _v1(world, service).status_code == 401                      # its token was revoked
    again = _service_token(world)
    assert again.status_code >= 400                                    # and no new one is issued
    r = _v1(world, approved)
    assert r.status_code == 403 and r.json()["error"]["code"] == "owner_required", r.text
    assert CANARY in _names(_v1(world, operator)), "运维密钥按待认领空间的既有规则继续可用"
    assert world["store"].one("SELECT status FROM api_platform_approvals")["status"] == "approved"

    # assigned again to the same owner: her approval counts again, by the same rule
    _claim(world)
    assert CANARY in _names(_v1(world, approved))
    assert _service_token(world).status_code == 200
