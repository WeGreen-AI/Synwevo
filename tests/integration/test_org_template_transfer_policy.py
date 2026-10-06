"""CR-045 organization publication: real composed services, isolated synthetic fixtures.

New releases transfer current source content; frozen releases already belong to the
organization. Transaction probes below inject interleavings, not authorization results.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from synwevo_core import data_flow, oidc
from synwevo_core.apps import Apps, DOCS_APP_ID
from synwevo_core.context import as_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations, current_membership
from synwevo_core.store import Store
from synwevo_docs.org_templates import OrgTemplates
from tests.integration.test_org_templates_v1 import call, delegated, env as machine_env  # noqa: F401

PW = "synthetic publication fixture passphrase"
WS = "X-Synwevo-Workspace"


def document(text="Synthetic release one"):
    import docx
    doc = docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "data"),
                       "PUBLIC_URL": "http://testserver", "ROLE": "all", "MAIL_MODE": "file",
                       "MAIL_DIR": str(tmp_path / "mail")}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "data"), role="all", start_jobs=False)
    ident = api.state.identity
    store = ident.store
    users = {key: ident.create_user(f"{key}@example.test", PW, status="active", with_personal_workspace=True)
             for key in ("owner", "publisher", "reader", "org_only")}
    for user in users.values():
        store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    orgs = Organizations(store, ident)
    created = orgs.create(users["owner"]["id"], "Synthetic publication org", "Source", "fixture-publication-org-1")
    oid, source = created["organization"]["id"], created["workspace"]["id"]
    target = orgs.create_workspace(oid, users["owner"]["id"], "Target")["id"]
    for key in ("publisher", "reader", "org_only"):
        store.insert("organization_memberships", {"organization_id": oid, "user_id": users[key]["id"],
                                                  "role": "member", "created_at": 1, "updated_at": 1})
    ident.add_member(source, users["publisher"]["id"], "editor")
    ident.add_member(target, users["reader"]["id"], "editor")
    clients = {}
    for key, wid in (("publisher", source), ("reader", target), ("owner", target)):
        client = TestClient(api)
        response = client.post("/api/auth/login", json={"email": users[key]["email"], "password": PW})
        assert response.status_code == 200, response.text
        client.headers.update({"X-Synwevo-CSRF": client.cookies.get("dw_csrf"), WS: wid})
        clients[key] = client
    shared = OrgTemplates(api.state.app, ident)
    response = clients["publisher"].post("/api/templates/upload", files={"file": ("fixture.docx", document())})
    assert response.status_code in (200, 201), response.text
    try:
        yield {"api": api, "store": store, "ident": ident, "users": users, "clients": clients, "shared": shared,
               "source": source, "target": target, "oid": oid, "tid": response.json()["id"], "data": tmp_path / "data"}
    finally:
        for client in clients.values():
            client.close()


def policy(w, value, *, store=None):
    store = store or w["store"]
    data_flow.set_workspace_outbound(store, w["source"], value, actor_id=w["users"]["owner"]["id"],
                                    revision=data_flow.workspace_outbound(store, w["source"])["revision"])


def publish(w):
    response = w["clients"]["publisher"].post(f"/api/templates/{w['tid']}/publish", json={})
    assert response.status_code == 201, response.text
    return response.json()


def advance(w):
    with as_tenant(w["source"]):
        return w["api"].state.app.new_version(w["tid"], document("Synthetic release two"), [], "fixture next head")


def publication(w, pid):
    return w["store"].one("SELECT * FROM organization_templates WHERE id=?", (pid,))


def assert_refusal(response, code):
    assert response.status_code == 403, response.text
    assert response.json()["error"] == code, response.text


def test_none_blocks_publication_even_from_the_source_workspace(world):
    w = world
    policy(w, "none")
    response = w["clients"]["publisher"].post(f"/api/templates/{w['tid']}/publish", json={})
    assert_refusal(response, "transfer_source_closed")
    for table in ("organization_templates", "organization_template_releases"):
        assert w["store"].one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


@pytest.mark.parametrize("outbound", [None, "organization", "any"])
def test_organization_publication_does_not_require_readers_to_join_source(world, outbound):
    w = world
    if outbound:
        policy(w, outbound)
    pub = publish(w)
    assert not w["ident"].membership(w["source"], w["users"]["reader"]["id"])
    response = w["clients"]["reader"].post(f"/api/organization-templates/{pub['id']}/use", json={})
    assert response.status_code == 201, response.text
    copied = w["store"].one("SELECT * FROM templates WHERE id=?", (response.json()["id"],))
    assert copied["tenant"] == w["target"]
    assert copied["original_blob"] == publication(w, pub["id"])["working_blob"]
    assert copied["info"]["origin"]["release_no"] == 1


def test_audience_includes_organization_members_without_any_org_workspace(world):
    w = world
    w["ident"].add_member(w["source"], w["users"]["reader"]["id"], "viewer")
    policy(w, "audience")
    response = w["clients"]["publisher"].post(f"/api/templates/{w['tid']}/publish", json={})
    assert_refusal(response, "transfer_organization_audience_denied")
    assert response.json()["detail"] == {"outside": 1}
    assert w["users"]["org_only"]["id"] not in response.text


@pytest.mark.parametrize("inactive", [{"status": "suspended"}, {"deleted_at": 1}])
def test_audience_uses_current_organization_membership_and_rechecks_reactivation(world, inactive):
    w = world
    uid = w["users"]["org_only"]["id"]
    w["ident"].add_member(w["source"], w["users"]["reader"]["id"], "viewer")
    w["store"].update("users", {"id": uid}, inactive)
    assert current_membership(w["store"], w["oid"], uid) is None
    policy(w, "audience")
    pub = publish(w)
    advance(w)
    w["store"].update("users", {"id": uid}, {"status": "active", "deleted_at": None})
    assert current_membership(w["store"], w["oid"], uid)
    response = w["clients"]["publisher"].post(f"/api/organization-templates/{pub['id']}/update", json={})
    assert_refusal(response, "transfer_organization_audience_denied")
    assert publication(w, pub["id"])["release_no"] == 1


def test_policy_change_blocks_new_bytes_but_preserves_publication_and_copies(world):
    w = world
    pub = publish(w)
    path = f"/api/organization-templates/{pub['id']}"
    first = w["clients"]["reader"].post(path + "/use", json={}).json()
    frozen = publication(w, pub["id"])
    advance(w)
    policy(w, "none")
    response = w["clients"]["publisher"].post(path + "/update", json={"description": "Must roll back too"})
    assert_refusal(response, "transfer_source_closed")
    assert publication(w, pub["id"]) == frozen
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases WHERE publication_id=?", (pub["id"],))["n"] == 1
    # A use of the authorized release is not a fresh export from its source.
    second = w["clients"]["reader"].post(path + "/use", json={})
    assert second.status_code == 201, second.text
    for tid in (first["id"], second.json()["id"]):
        response = w["clients"]["reader"].get(f"/api/templates/{tid}")
        assert response.status_code == 200
        row = w["store"].one("SELECT * FROM templates WHERE id=?", (tid,))
        assert row["original_blob"] == frozen["working_blob"]
    # Reopening policy permits the actual current head; old copies still keep release 1.
    policy(w, "organization")
    response = w["clients"]["publisher"].post(path + "/update", json={})
    assert response.status_code == 200 and response.json()["version_no"] == 2, response.text
    assert publication(w, pub["id"])["working_blob"] != frozen["working_blob"]
    assert w["clients"]["reader"].get(f"/api/templates/{first['id']}").json()["info"]["origin"]["release_no"] == 1


@pytest.mark.parametrize("revocation", ["source_membership", "organization_membership", "entitlement", "source_deleted"])
def test_publisher_revocation_or_source_deletion_does_not_revoke_frozen_release(world, revocation):
    w = world
    pub = publish(w)
    path = f"/api/organization-templates/{pub['id']}"
    first = w["clients"]["reader"].post(path + "/use", json={}).json()
    uid = w["users"]["publisher"]["id"]
    if revocation == "source_membership":
        w["store"].execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (w["source"], uid))
    elif revocation == "organization_membership":
        w["store"].execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (w["oid"], uid))
    elif revocation == "entitlement":
        Apps(w["store"], w["ident"]).revoke(w["source"], DOCS_APP_ID, uid)
    else:
        assert w["clients"]["publisher"].delete(f"/api/templates/{w['tid']}").status_code == 200
    assert w["clients"]["reader"].get(path).status_code == 200
    response = w["clients"]["reader"].post(path + "/use", json={})
    assert response.status_code == 201, response.text
    assert response.json()["info"]["origin"]["release_no"] == 1
    assert w["clients"]["reader"].get(f"/api/templates/{first['id']}").status_code == 200


@pytest.mark.parametrize("restriction,code", [("membership", "transfer_source_unavailable"),
                                              ("entitlement", "transfer_source_unavailable"),
                                              ("export", "transfer_export_denied")])
def test_org_governance_does_not_grant_source_content_or_export_rights(world, restriction, code):
    w = world
    pub = publish(w)
    uid = w["users"]["owner"]["id"]
    if restriction == "membership":
        w["store"].execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (w["source"], uid))
    elif restriction == "entitlement":
        Apps(w["store"], w["ident"]).revoke(w["source"], DOCS_APP_ID, uid)
    else:
        # Synthetic governance-only fixture: org owner, source viewer without export.
        w["store"].execute("UPDATE memberships SET role='viewer',can_export=0 WHERE workspace_id=? AND user_id=?", (w["source"], uid))
    path = f"/api/organization-templates/{pub['id']}"
    owner = w["clients"]["owner"]
    # Description-only maintenance and deprecation do not touch source bytes.
    response = owner.post(path + "/update", json={"description": "Governed description"})
    assert response.status_code == 200 and response.json()["released"] is False, response.text
    advance(w)
    frozen = publication(w, pub["id"])
    response = owner.post(path + "/update", json={"description": "Would also release"})
    assert_refusal(response, code)
    assert publication(w, pub["id"]) == frozen
    assert owner.post(path + "/deprecate", json={}).status_code == 200
    assert owner.post(path + "/deprecate", json={"deprecated": False}).status_code == 200
    assert w["clients"]["reader"].post(path + "/use", json={}).status_code == 201


def test_org_admin_with_source_export_can_release_from_another_workspace(world):
    w = world
    pub = publish(w)
    advance(w)
    w["store"].execute("UPDATE memberships SET role='viewer',can_export=1 WHERE workspace_id=? AND user_id=?",
                       (w["source"], w["users"]["owner"]["id"]))
    response = w["clients"]["owner"].post(f"/api/organization-templates/{pub['id']}/update", json={})
    assert response.status_code == 200 and response.json()["release_no"] == 2, response.text


def test_initial_publication_rechecks_editor_role_after_the_route_gate(world):
    w = world
    uid = w["users"]["publisher"]["id"]
    w["store"].execute("UPDATE memberships SET role='viewer',can_export=1 WHERE workspace_id=? AND user_id=?",
                       (w["source"], uid))
    with as_tenant(w["source"]), pytest.raises(ServiceError) as error:
        w["shared"].publish(w["tid"], uid)
    assert error.value.code == "forbidden"
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_templates")["n"] == 0


@pytest.mark.parametrize("keyed", [False, True])
def test_machine_api_policy_recheck_preserves_idempotent_frozen_publication(machine_env, keyed):
    w = machine_env
    token = delegated(w, "ann@example.com", w["wa"])
    upload = call(w, token, "POST", "/api/v1/templates", files={"file": ("fixture.docx", document())})
    assert upload.status_code == 201, upload.text
    tid = upload.json()["id"]
    headers = {"Idempotency-Key": "fixture-publication"} if keyed else {}

    def set_policy(value):
        data_flow.set_workspace_outbound(w["store"], w["wa"], value, actor_id=w["ann"]["id"],
                                        revision=data_flow.workspace_outbound(w["store"], w["wa"])["revision"])

    set_policy("none")
    response = call(w, token, "POST", f"/api/v1/templates/{tid}/publish", json={}, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "transfer_source_closed", response.text
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_templates")["n"] == 0
    set_policy("organization")
    response = call(w, token, "POST", f"/api/v1/templates/{tid}/publish", json={}, headers=headers)
    assert response.status_code == 201, response.text
    pub = response.json()
    with as_tenant(w["wa"]):
        w["api"].state.app.new_version(tid, document("Synthetic machine release two"), [], "fixture new head")
    set_policy("none")
    if keyed:
        replay = call(w, token, "POST", f"/api/v1/templates/{tid}/publish", json={}, headers=headers)
        assert replay.status_code == 201 and replay.json() == pub, replay.text
    response = call(w, token, "POST", f"/api/v1/organization-templates/{pub['id']}/releases", json={})
    assert response.status_code == 403 and response.json()["error"]["code"] == "transfer_source_closed", response.text
    bob = delegated(w, "bob@example.com", w["wb"])
    copied = call(w, bob, "POST", f"/api/v1/organization-templates/{pub['id']}/use", json={})
    assert copied.status_code == 201 and copied.json()["origin"]["release_no"] == 1, copied.text
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == 1


@pytest.mark.parametrize("body", [{}, {"description": "Must not implicitly release source bytes"}])
def test_target_bound_delegation_cannot_release_fresh_source_bytes(machine_env, body):
    w = machine_env
    source_token = delegated(w, "ann@example.com", w["wa"])
    target_token = delegated(w, "ann@example.com", w["wb"])
    upload = call(w, source_token, "POST", "/api/v1/templates", files={"file": ("fixture.docx", document())})
    assert upload.status_code == 201, upload.text
    tid = upload.json()["id"]
    response = call(w, source_token, "POST", f"/api/v1/templates/{tid}/publish", json={})
    assert response.status_code == 201, response.text
    pub = response.json()
    path = f"/api/v1/organization-templates/{pub['id']}"
    # The user's governance and source access do not expand this token's workspace.
    assert call(w, target_token, "GET", f"/api/v1/templates/{tid}").status_code == 404
    metadata = call(w, target_token, "POST", path + "/releases", json={"description": "Organization-owned metadata"})
    assert metadata.status_code == 201 and metadata.json()["released"] is False, metadata.text
    with as_tenant(w["wa"]):
        w["api"].state.app.new_version(tid, document("Unreleased private source change"), [], "fixture new source bytes")
    before = w["store"].one("SELECT * FROM organization_templates WHERE id=?", (pub["id"],))
    response = call(w, target_token, "POST", path + "/releases", json=body)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "source_delegation_required"
    assert w["store"].one("SELECT * FROM organization_templates WHERE id=?", (pub["id"],)) == before
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == 1
    # The authorized frozen release remains readable and reusable with the target token.
    assert call(w, target_token, "GET", path).status_code == 200
    copied = call(w, target_token, "POST", path + "/use", json={})
    assert copied.status_code == 201 and copied.json()["origin"]["release_no"] == 1, copied.text
    assert w["store"].one("SELECT original_blob FROM templates WHERE id=?", (copied.json()["id"],))["original_blob"] == before["working_blob"]
    # A delegation actually granted for the source can publish its current head.
    response = call(w, source_token, "POST", path + "/releases", json={})
    assert response.status_code == 201 and response.json()["release_no"] == 2, response.text


@pytest.mark.parametrize("initial", [True, False])
def test_source_delegation_revoked_after_route_gate_cannot_publish(machine_env, monkeypatch, initial):
    w = machine_env
    token = delegated(w, "ann@example.com", w["wa"])
    upload = call(w, token, "POST", "/api/v1/templates", files={"file": ("fixture.docx", document())})
    assert upload.status_code == 201, upload.text
    tid = upload.json()["id"]
    path = f"/api/v1/templates/{tid}/publish"
    before = None
    if not initial:
        response = call(w, token, "POST", path, json={})
        assert response.status_code == 201, response.text
        pub = response.json()
        path = f"/api/v1/organization-templates/{pub['id']}/releases"
        before = w["store"].one("SELECT * FROM organization_templates WHERE id=?", (pub["id"],))
        with as_tenant(w["wa"]):
            w["api"].state.app.new_version(tid, document("Source changed before revocation"), [], "fixture new head")
    method = "publish" if initial else "update"
    original = getattr(OrgTemplates, method)
    revoked = False

    def revoke_before_service_transaction(self, *args, **kwargs):
        nonlocal revoked
        # Real grant revocation after person()/scope() accepted the request, before tx().
        assert oidc.Grants(self.store).revoke_token(token)
        revoked = True
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OrgTemplates, method, revoke_before_service_transaction)
    response = call(w, token, "POST", path, json={})
    assert revoked
    assert response.status_code == 403 and response.json()["error"]["code"] == "source_delegation_required", response.text
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == (0 if initial else 1)
    if initial:
        assert w["store"].one("SELECT COUNT(*) AS n FROM organization_templates")["n"] == 0
    else:
        assert w["store"].one("SELECT * FROM organization_templates WHERE id=?", (pub["id"],)) == before


@pytest.mark.parametrize("initial", [True, False])
@pytest.mark.parametrize("scope", ["workspace", "organization"])
def test_unknown_policy_rejects_new_publication_and_release(world, initial, scope):
    w = world
    pub = None if initial else publish(w)
    if not initial:
        advance(w)
    table, key, value = (("workspace_transfer_policies", "workspace_id", w["source"]) if scope == "workspace"
                         else ("organization_transfer_policies", "organization_id", w["oid"]))
    # A synthetic row from a newer schema version, not a policy the setter accepts.
    with w["store"].tx() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        try:
            w["store"].insert(table, {key: value, "outbound": "future-policy", "revision": 1,
                                     "updated_by": w["users"]["owner"]["id"], "updated_at": 1})
        finally:
            connection.execute("PRAGMA ignore_check_constraints=OFF")
    path = f"/api/templates/{w['tid']}/publish" if initial else f"/api/organization-templates/{pub['id']}/update"
    assert_refusal(w["clients"]["publisher"].post(path, json={}),
                   "transfer_source_closed" if scope == "workspace" else "transfer_publication_policy_denied")


@pytest.mark.parametrize("initial", [True, False])
@pytest.mark.parametrize("inactive", ["source_frozen", "source_deleted", "org_frozen", "org_deleted", "publisher_suspended"])
def test_service_rechecks_current_lifecycle_even_after_route_authorization(world, initial, inactive):
    w = world
    pub = None if initial else publish(w)
    if not initial:
        advance(w)
    if inactive.startswith("source_"):
        w["store"].update("workspaces", {"id": w["source"]},
                          {"status": "frozen"} if inactive == "source_frozen" else {"deleted_at": 1})
    elif inactive.startswith("org_"):
        w["store"].update("organizations", {"id": w["oid"]}, {"status": inactive.removeprefix("org_")})
    else:
        w["store"].update("users", {"id": w["users"]["publisher"]["id"]}, {"status": "suspended"})
    with as_tenant(w["source"]), pytest.raises(ServiceError):
        if initial:
            w["shared"].publish(w["tid"], w["users"]["publisher"]["id"])
        else:
            w["shared"].update(pub["id"], w["users"]["publisher"]["id"])
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == (0 if initial else 1)


@pytest.mark.parametrize("initial", [True, False])
def test_policy_check_and_release_insertion_share_a_sqlite_write_transaction(world, monkeypatch, initial):
    w = world
    pub = None if initial else publish(w)
    if not initial:
        advance(w)
    writer = Store(w["data"])
    checked, resume = threading.Event(), threading.Event()
    original = data_flow.check_organization_publication

    def pause_after_check(*args, **kwargs):
        result = original(*args, **kwargs)
        checked.set()
        assert resume.wait(5), "publication was not resumed"
        return result

    monkeypatch.setattr(data_flow, "check_organization_publication", pause_after_check)

    def release():
        with as_tenant(w["source"]):
            if initial:
                return w["shared"].publish(w["tid"], w["users"]["publisher"]["id"])
            return w["shared"].update(pub["id"], w["users"]["publisher"]["id"])

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(release)
        try:
            assert checked.wait(5), "policy was not checked"
            assert writer.one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == (0 if initial else 1)
            with writer.conn() as connection:
                connection.execute("PRAGMA busy_timeout=50")
            # A separate SQLite connection cannot tighten policy between check and insert.
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                policy(w, "none", store=writer)
        finally:
            resume.set()
        out = future.result(timeout=5)
    assert out["release_no"] == (1 if initial else 2)
    policy(w, "none", store=writer)
    assert publication(w, out["id"])["release_no"] == out["release_no"]


@pytest.mark.parametrize("initial", [True, False])
def test_source_head_is_read_after_entering_publication_transaction(world, monkeypatch, initial):
    w = world
    pub = None if initial else publish(w)
    original = w["store"].tx
    injected = False

    @contextmanager
    def concurrent_edit_before_begin():
        nonlocal injected
        if not injected:
            injected = True
            advance(w)  # Real committed source edit immediately before acquiring the write lock.
        with original() as connection:
            yield connection

    monkeypatch.setattr(w["store"], "tx", concurrent_edit_before_begin)
    with as_tenant(w["source"]):
        out = (w["shared"].publish(w["tid"], w["users"]["publisher"]["id"]) if initial else
               w["shared"].update(pub["id"], w["users"]["publisher"]["id"]))
    assert injected and out["version_no"] == 2
    assert out["release_no"] == (1 if initial else 2)


@pytest.mark.parametrize("initial", [True, False])
def test_failed_release_insert_rolls_back_the_entire_publication(world, initial):
    w = world
    pub = None if initial else publish(w)
    before = None if initial else publication(w, pub["id"])
    if not initial:
        advance(w)
    # Synthetic storage failure exercises real SQLite rollback after publication work starts.
    w["store"].execute("CREATE TEMP TRIGGER fail_fixture_release BEFORE INSERT ON organization_template_releases "
                       "BEGIN SELECT RAISE(ABORT, 'fixture release failure'); END")
    with as_tenant(w["source"]), pytest.raises(sqlite3.IntegrityError, match="fixture release failure"):
        if initial:
            w["shared"].publish(w["tid"], w["users"]["publisher"]["id"])
        else:
            w["shared"].update(pub["id"], w["users"]["publisher"]["id"], "Must not persist", set_description=True)
    if initial:
        assert w["store"].one("SELECT COUNT(*) AS n FROM organization_templates")["n"] == 0
    else:
        assert publication(w, pub["id"]) == before
    assert w["store"].one("SELECT COUNT(*) AS n FROM organization_template_releases")["n"] == (0 if initial else 1)
