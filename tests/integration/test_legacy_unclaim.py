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
from tests.integration.platform_fixture import make_platform_admin

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
    admin = make_platform_admin(store, ident.create_user("root@example.com", PW, status="active"))
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


def test_a_frozen_wrong_assignment_is_undone_without_the_assignee_getting_it_back(world):
    """The administrator who spots a wrong assignment freezes it first (the stop switch). The undo
    used to be refused until it was unfrozen, and the unfreeze gave the wrong account the data back
    — its browser could select the workspace and read the project again — until the undo ran."""
    alice_c = world["c"]["alice"]
    _claim(world)
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
    assert alice_c.get("/api/projects/p_legacy").json()["name"] == CANARY
    _admin(world, "freeze", {"reason": "指派错了，先停止访问"})
    assert alice_c.get("/api/projects/p_legacy", headers={"X-Synwevo-Workspace": LEGACY}).status_code == 403

    out = _unclaim(world)
    assert out["previous_status"] == "frozen" and out["status"] == "pending_claim"
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code in (403, 404)
    assert alice_c.get("/api/projects/p_legacy").status_code == 404
    assert CANARY not in str(alice_c.get("/api/projects").json())
    assert not world["store"].all("SELECT * FROM audit_events WHERE action='admin.workspace.unfreeze'")
    # assigned again, the normal way
    _claim(world)
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
    assert alice_c.get("/api/projects/p_legacy").status_code == 200


class _Tab:
    """Fixture: the part of a browser tab's live-editing client this test needs — Yjs sync over the
    room's socket (`synwevo_docs.collab`), a local copy of the document, and the server's frames."""

    def __init__(self, client, tid):
        from pycrdt import Doc
        self.cm = client.websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"})
        self.ws = self.cm.__enter__()
        self.doc, self.meta, self.synced = Doc(), [], False

    def frame(self):
        import json
        from pycrdt import YMessageType, YSyncMessageType, handle_sync_message, read_message
        from synwevo_docs.collab import MESSAGE_META
        data = self.ws.receive_bytes()
        if data[0] == MESSAGE_META:
            self.meta.append(json.loads(read_message(data[1:])))
            return "meta"
        if data[0] == YMessageType.SYNC:
            reply = handle_sync_message(data[1:], self.doc)
            if data[1] == YSyncMessageType.SYNC_STEP1 and reply is not None:
                self.ws.send_bytes(reply)
            if data[1] == YSyncMessageType.SYNC_STEP2:
                self.synced = True
                return "step2"
        return "other"

    def sync(self):
        from pycrdt import create_sync_message
        self.synced = False
        self.ws.send_bytes(create_sync_message(self.doc))
        for _ in range(20):
            if self.frame() == "step2":
                return
            if self.meta and self.meta[-1].get("type") == "closed":
                return
        raise AssertionError(f"no answer; meta: {self.meta}")

    def text(self) -> str:
        from pycrdt import XmlFragment
        return str(self.doc.get("body", type=XmlFragment))

    def type(self, text: str):
        """Append to the first paragraph's text and send the update, like a keystroke."""
        from pycrdt import XmlFragment, create_update_message
        updates = []
        sub = self.doc.observe(lambda ev: updates.append(ev.update))
        with self.doc.transaction():
            node = self.doc.get("body", type=XmlFragment).children[0].children[0]
            node.insert(len(node), text)
        self.doc.unobserve(sub)
        assert updates, "the edit produced no update"
        for u in updates:
            self.ws.send_bytes(create_update_message(u))

    def closed(self):
        from starlette.websockets import WebSocketDisconnect
        for _ in range(20):
            try:
                self.frame()
            except WebSocketDisconnect as e:
                return e.code, [m for m in self.meta if m.get("type") == "closed"]
        raise AssertionError(f"still open; meta: {self.meta}")

    def close(self):
        """Idempotent: also after the server closed the socket (its portal thread ends only here)."""
        cm, self.cm = self.cm, None
        if cm is not None:
            cm.__exit__(None, None, None)


def test_an_open_live_editing_tab_can_neither_read_nor_write_after_the_undo(world, monkeypatch):
    """The former owner's template tab is open when the administrator undoes the assignment. Their
    HTTP requests were refused at once, but the socket was re-checked only every 30 s: it still
    answered a fresh sync with the whole document and saved an edit into the draft the next
    assignee would inherit (review finding). Each reading or writing frame is now re-checked."""
    # the periodic re-check plays no part here (and bounds the test, rather than hanging it, without the fix)
    monkeypatch.setenv("SYNWEVO_COLLAB_RECHECK_S", "5")
    alice_c, store = world["c"]["alice"], world["store"]
    doc = {"page": {"size": "A4", "orientation": "portrait", "margins": {"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 31.8}},
           "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "客户："}]}]},
           "header": {"type": "doc", "content": [{"type": "paragraph"}]}, "footer": {"type": "doc", "content": [{"type": "paragraph"}]}}
    with TestClient(world["api"]):
        _claim(world)
        assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
        r = _post(alice_c, "/api/templates/builtin", {"kind": "builtin_doc", "editor": doc, "name": "历史合同模板"})
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        tabs = []  # closed even when an assertion below fails: an open tab keeps re-reading this database
        try:
            reader = _Tab(alice_c, tid)
            tabs.append(reader)
            writer = _Tab(alice_c, tid)
            tabs.append(writer)
            for tab in (reader, writer):
                tab.sync()
                assert "客户：" in tab.text()

            _unclaim(world)
            assert alice_c.get(f"/api/templates/{tid}").status_code in (403, 404)
            # a fresh sync asks for the whole document: refused, nothing sent
            from pycrdt import Doc
            reader.doc = Doc()
            reader.sync()
            assert reader.synced is False and "客户：" not in reader.text()
            assert reader.closed() == (4403, [{"type": "closed", "reason": "membership_ended"}])
            # an edit: refused, never applied
            writer.type("（撤销之后写入）")
            assert writer.closed() == (4403, [{"type": "closed", "reason": "membership_ended"}])
        finally:
            for tab in tabs:
                tab.close()
    # nothing of it reached the draft the next assignee inherits
    for row in store.all("SELECT state FROM collab_docs WHERE template_id=?", (tid,)):
        from pycrdt import Doc, XmlFragment
        d = Doc()
        d.apply_update(row["state"])
        assert "撤销之后" not in str(d.get("body", type=XmlFragment))
    _claim(world)
    assert _post(alice_c, "/api/auth/workspace", {"workspace_id": LEGACY}).status_code == 200
    assert alice_c.get(f"/api/templates/{tid}").status_code == 200
