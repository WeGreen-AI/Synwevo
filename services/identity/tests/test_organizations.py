"""Isolated organization fixtures; real Store, identity factory and login sessions.

No production data, external mail, document app factory, or alternate migrator.
The factory/Store wiring belongs to the parent task and is deliberately exercised
here: tests must fail if the new router/schema are not installed in real startup.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import pytest

from .platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core import authority
from synwevo_core.apps import Apps, DOCS_APP_ID
from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.machine import MachineAuth
from synwevo_core.organizations import (
    Organizations, RECOVERY_SECONDS, accept_workspace_invitation,
    current_membership, organization_for_workspace, require_invitation_permission,
    workspace_access_failure,
)
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, new_id, now
from synwevo_identity.app import create_app
from synwevo_identity.invites import Invites

PW = "isolated-organization-test-passphrase"
REASON = "测试夹具：已完成组织治理复核"
BASE = "/api/auth/organizations"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    root = tmp_path / "isolated"
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(root))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    api = create_app(str(root), start_jobs=False)
    store, ident = Store(root), Identity(Store(root))
    users = {name: ident.create_user(name + "@example.test", PW, display_name=name, status="active")
             for name in ("alice", "bob", "carol", "outsider")}
    with TestClient(api) as client:
        assert client.post("/api/auth/login", json={"email": users["alice"]["email"], "password": PW}).status_code == 200
        yield {"api": api, "store": store, "ident": ident, "users": users,
               "client": client, "orgs": Organizations(store), "root": root}


def request(env, method, path, body=None, **headers):
    c = env["client"]
    return c.request(method, path, json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or "", **headers})


def reauth(env):
    result = request(env, "POST", "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert result.status_code == 200, result.text


def create(env, key="test-create-unique-01"):
    response = request(env, "POST", BASE, {"name": "隔离组织", "workspace_name": "隔离工作空间"}, **{"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def member_fixture(env, oid, name, role="member"):
    """Explicit fixture for org-only membership; deliberately no workspace grant."""
    uid, ts = env["users"][name]["id"], now()
    env["store"].insert("organization_memberships", {"organization_id": oid, "user_id": uid, "role": role,
                         "created_at": ts, "updated_at": ts, "created_by": env["users"]["alice"]["id"]})
    return uid


def expect_error(code, fn):
    with pytest.raises(ServiceError) as exc:
        fn()
    assert exc.value.code == code


def test_create_is_persisted_idempotent_and_does_not_replace_personal_workspace(env):
    actor = env["users"]["alice"]["id"]
    personal = env["ident"].workspaces_for(actor)
    first = create(env)
    assert create(env) == first
    assert Organizations(Store(env["root"])).create(actor, "隔离组织", "隔离工作空间", "test-create-unique-01") == first
    assert env["store"].one("SELECT COUNT(*) AS n FROM organizations")["n"] == 1
    assert env["store"].one("SELECT COUNT(*) AS n FROM audit_events WHERE action='organization.create'")["n"] == 1
    assert env["ident"].get_workspace(personal[0]["id"])["organization_id"] is None
    assert first["organization"]["usage"] == {"members": 1, "workspaces": 1}
    assert first["organization"]["quota"] is None
    different = request(env, "POST", BASE, {"name": "different", "workspace_name": "隔离工作空间"},
                        **{"Idempotency-Key": "test-create-unique-01"})
    assert different.status_code == 409 and different.json()["error"] == "idempotency_conflict"
    assert request(env, "POST", BASE, {"name": "missing key", "workspace_name": "space"}).status_code == 400


def test_creation_failure_rolls_back_org_workspace_grants_idempotency_and_audit(env, monkeypatch):
    orgs, store, actor = env["orgs"], env["store"], env["users"]["alice"]["id"]
    before = {table: store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] for table in
              ("organizations", "organization_memberships", "organization_idempotency", "workspaces", "memberships", "app_entitlements", "audit_events")}
    def fail_audit(*args, **kwargs):
        raise RuntimeError("isolated fixture: audit storage failed")
    monkeypatch.setattr(orgs, "_record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage"):
        orgs.create(actor, "rollback", "workspace", "rollback-create-key")
    assert before == {table: store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] for table in before}


def test_authority_is_checked_again_after_waiting_for_writer(env):
    alice, bob = env["users"]["alice"]["id"], env["users"]["bob"]["id"]
    created = env["orgs"].create(alice, "race fixture", "workspace", "authority-race-key")
    oid = created["organization"]["id"]
    member_fixture(env, oid, "bob", "admin")
    other = Organizations(Store(env["root"]))
    attempting = threading.Event()
    def queued_write():
        attempting.set()
        try:
            other.create_workspace(oid, bob, "should not be created")
            return "ok"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with env["store"].tx():
            future = pool.submit(queued_write)
            assert attempting.wait(5)
            env["orgs"].change_role(oid, alice, bob, "member", reason=REASON)
        assert future.result(timeout=5) == "forbidden"
    assert env["store"].one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=?", (oid,))["n"] == 1


def test_invitation_hook_joins_parent_transaction_and_never_upgrades_org_role(env):
    alice, bob = env["users"]["alice"]["id"], env["users"]["bob"]["id"]
    created = env["orgs"].create(alice, "atomic invitation fixture", "workspace", "atomic-invitation-key")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    with pytest.raises(RuntimeError, match="consumption failed"):
        with env["store"].tx():
            accept_workspace_invitation(env["store"], wid, bob, alice)
            raise RuntimeError("isolated fixture: consumption failed")
    assert current_membership(env["store"], oid, bob) is None
    assert env["ident"].membership(wid, bob) is None


def test_real_invitation_acceptance_adds_only_org_member_and_selected_workspace(env):
    alice, bob = env["users"]["alice"]["id"], env["users"]["bob"]["id"]
    created = env["orgs"].create(alice, "real invitation fixture", "first", "real-invitation-key")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    other = env["orgs"].create_workspace(oid, alice, "not invited")["id"]
    invites = Invites(env["store"], Identity(env["store"]))
    invitation, raw = invites.create(workspace_id=wid, email=env["users"]["bob"]["email"], role="owner", actor_id=alice)
    invites.accept(raw, password=None, display_name=None, as_user_id=bob)
    assert current_membership(env["store"], oid, bob)["role"] == "member"
    assert env["ident"].membership(wid, bob)["role"] == "owner"
    assert env["ident"].membership(other, bob) is None
    assert env["store"].one("SELECT accepted_by FROM invitations WHERE id=?", (invitation["id"],))["accepted_by"] == bob


def test_real_invitation_rechecks_workspace_inviter_after_concurrent_demotion(env):
    """The demotion holds the writer lock first; the queued accept sees its result."""
    store, alice = env["store"], env["users"]["alice"]["id"]
    created = env["orgs"].create(alice, "invitation race fixture", "workspace", "invitation-race-key")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    bob, carol = member_fixture(env, oid, "bob", "admin"), env["users"]["carol"]["id"]
    Identity(store).add_member(wid, bob, "admin")
    invites = Invites(store, Identity(store))
    invitation, raw = invites.create(workspace_id=wid, email=env["users"]["carol"]["email"], role="editor", actor_id=bob)
    attempting = threading.Event()
    worker_store = Store(env["root"])
    def accept():
        attempting.set()
        try:
            Invites(worker_store, Identity(worker_store)).accept(raw, password=None, display_name=None, as_user_id=carol)
            return "accepted"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.tx():
            pending = pool.submit(accept)
            assert attempting.wait(5)
            Identity(store).change_role(wid, bob, "viewer", actor_id=alice)
        assert pending.result(timeout=5) in ("inviter_lost_permission", "forbidden", "role_too_high")
    assert current_membership(store, oid, carol) is None
    assert env["ident"].membership(wid, carol) is None
    assert store.one("SELECT accepted_at FROM invitations WHERE id=?", (invitation["id"],))["accepted_at"] is None


def test_frozen_org_lifecycle_and_authority_rollback_on_audit_failure(env, monkeypatch):
    alice, orgs = env["users"]["alice"]["id"], env["orgs"]
    created = orgs.create(alice, "atomic freeze fixture", "workspace", "atomic-freeze-key")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    scoped = _authority_fixture(env, wid, alice, "atomic-freeze")
    def fail_audit(*args, **kwargs):
        raise RuntimeError("isolated fixture: audit storage failed")
    monkeypatch.setattr(orgs, "_record", fail_audit)
    with pytest.raises(RuntimeError, match="audit storage"):
        orgs.transition(oid, alice, "freeze", reason=REASON)
    assert orgs.get(oid, alice)["status"] == "active"
    for table, key in (("sessions", "session"), ("oauth_tokens", "token"), ("invitations", "invite"), ("api_platform_approvals", "approval")):
        assert env["store"].one(f"SELECT revoked_at FROM {table} WHERE id=?", (scoped[key],))["revoked_at"] is None


def test_concurrent_create_replays_once_across_store_connections(env):
    actor, barrier = env["users"]["alice"]["id"], threading.Barrier(2)
    stores = [Store(env["root"]), Store(env["root"])]
    def run(store):
        barrier.wait()
        return Organizations(store).create(actor, "并发夹具", "space", "same-concurrent-create-key")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, stores))
    assert results[0] == results[1]
    assert env["store"].one("SELECT COUNT(*) AS n FROM organization_idempotency")["n"] == 1


def test_platform_admin_outsider_has_no_org_membership_or_content_authority(env):
    created = create(env)
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    uid = env["users"]["outsider"]["id"]
    make_platform_admin(env["ident"].store, uid)
    expect_error("no_organization", lambda: env["orgs"].get(oid, uid))
    assert env["orgs"].list_for_user(uid) == []
    assert workspace_access_failure(env["store"], wid, uid) == "not_an_organization_member"
    assert env["ident"].membership(wid, uid) is None
    bob = member_fixture(env, oid, "bob", "admin")
    assert env["orgs"].workspaces(oid, bob)[0]["workspace_role"] is None
    assert workspace_access_failure(env["store"], wid, bob) is None
    assert env["ident"].membership(wid, bob) is None
    assert env["store"].one("SELECT 1 FROM app_entitlements WHERE workspace_id=? AND user_id=?", (wid, bob)) is None
    assert env["orgs"].create_workspace(oid, bob, "Bob explicitly created")["created_by"] == bob
    assert env["ident"].membership(wid, bob) is None


def test_integrated_apps_deferred_authority_and_machine_gates_are_live(env):
    created = create(env)
    oid, wid, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    store, orgs = env["store"], env["orgs"]
    apps = Apps(store, Identity(store))
    bob = member_fixture(env, oid, "bob", "admin")
    assert not apps.check_access(bob, DOCS_APP_ID, wid)[0]
    bob_desc = {"kind": "user", "user_id": bob, "tenant": wid}
    assert not authority.check(store, bob_desc, need="read")[0]
    Identity(store).add_member(wid, bob, "viewer")
    assert apps.check_access(bob, DOCS_APP_ID, wid)[0]
    assert authority.check(store, bob_desc, need="read")[0]
    # Isolated fixture for a lost org membership with a stale workspace row:
    # business gates must independently refuse it, not rely on removal cleanup.
    store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, bob))
    assert not apps.check_access(bob, DOCS_APP_ID, wid)[0]
    assert authority.check(store, bob_desc, need="read") == (False, "not_an_organization_member")
    auth = Auth(store)
    raw, key = auth.create_key("isolated org gate fixture", wid, scopes=("read",))
    machine = MachineAuth(store, Identity(store), auth)
    principal = machine.verify("Bearer " + raw)
    descriptor = authority.describe(principal)
    assert authority.check(store, descriptor, need="read")[0]
    alice_desc = {"kind": "user", "user_id": alice, "tenant": wid}
    assert apps.check_access(alice, DOCS_APP_ID, wid)[0]
    orgs.transition(oid, alice, "freeze", reason=REASON)
    assert not apps.check_access(alice, DOCS_APP_ID, wid)[0]
    assert not apps.check_access_for_workspace(wid, DOCS_APP_ID)[0]
    assert authority.check(store, alice_desc, need="read") == (False, "organization_unavailable")
    assert authority.check(store, descriptor, need="read") == (False, "organization_unavailable")
    with pytest.raises(ServiceError) as exc:
        machine.verify("Bearer " + raw)
    assert exc.value.detail["reason"] == "organization_unavailable"
    assert auth.get_key(key["id"])["revoked_at"] is None
    orgs.transition(oid, alice, "unfreeze", reason=REASON)
    assert machine.verify("Bearer " + raw)["key_id"] == key["id"]
    assert apps.check_access(alice, DOCS_APP_ID, wid)[0]


def test_member_search_pagination_and_role_permissions(env):
    oid = create(env)["organization"]["id"]
    actor = env["users"]["alice"]["id"]
    bob, carol = member_fixture(env, oid, "bob", "admin"), member_fixture(env, oid, "carol")
    page = env["client"].get(f"{BASE}/{oid}/members?offset=1&limit=1").json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["items"][0]["user_id"] == bob
    result = env["client"].get(f"{BASE}/{oid}/members?q=CAROL&role=member").json()
    assert result["total"] == 1 and result["items"][0]["user_id"] == carol
    assert env["orgs"].members(oid, actor, q="%")["total"] == 0
    expect_error("forbidden", lambda: env["orgs"].members(oid, carol))
    expect_error("role_too_high", lambda: env["orgs"].change_role(oid, bob, carol, "admin", reason=REASON))
    assert env["client"].get(f"{BASE}/{oid}/members?limit=201").status_code == 422


def test_sensitive_operations_need_scoped_reauth_reason_and_csrf(env):
    oid = create(env)["organization"]["id"]
    bob = member_fixture(env, oid, "bob")
    path = f"{BASE}/{oid}/members/{bob}"
    body = {"role": "admin", "reason": REASON}
    assert request(env, "PATCH", path, body).json()["error"] == "reauth_required"
    assert request(env, "POST", "/api/auth/reauth", {"password": PW, "scope": "mfa"}).status_code == 200
    assert request(env, "PATCH", path, body).json()["error"] == "reauth_required"
    reauth(env)
    assert env["client"].patch(path, json=body).status_code == 403
    assert request(env, "PATCH", path, {"role": "admin", "reason": "short"}).json()["error"] == "missing_reason"
    result = request(env, "PATCH", path, body)
    assert result.status_code == 200, result.text
    audit = env["store"].one("SELECT * FROM audit_events WHERE action='organization.member.role'")
    assert json.loads(audit["meta"])["reason"] == REASON and audit["request_id"]


def test_last_active_owner_and_concurrent_demotions(env):
    oid = create(env)["organization"]["id"]
    alice, bob = env["users"]["alice"]["id"], member_fixture(env, oid, "bob", "owner")
    env["ident"].set_status(bob, "suspended")
    expect_error("last_owner", lambda: env["orgs"].change_role(oid, alice, alice, "admin", reason=REASON))
    env["ident"].set_status(bob, "active")
    barrier = threading.Barrier(2)
    pairs = [(Organizations(Store(env["root"])), uid) for uid in (alice, bob)]
    def demote(pair):
        orgs, uid = pair
        barrier.wait()
        try:
            orgs.change_role(oid, uid, uid, "admin", reason=REASON)
            return "ok"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(demote, pairs))
    assert sorted(outcomes) == ["last_owner", "ok"]
    assert env["orgs"].members(oid, alice)["active_owner_count"] == 1


def test_transfer_is_org_only_and_rejects_inactive_target(env):
    created = create(env)
    oid, wid, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    bob = member_fixture(env, oid, "bob")
    env["ident"].set_status(bob, "suspended")
    expect_error("unauthorized", lambda: env["orgs"].transfer(oid, alice, bob, reason=REASON))
    env["ident"].set_status(bob, "active")
    reauth(env)
    assert request(env, "POST", f"{BASE}/{oid}/transfer", {"user_id": bob, "reason": REASON}).status_code == 200
    assert current_membership(env["store"], oid, alice)["role"] == "admin"
    assert current_membership(env["store"], oid, bob)["role"] == "owner"
    assert env["ident"].membership(wid, alice)["role"] == "owner"
    assert env["ident"].membership(wid, bob) is None


def test_adoption_is_explicit_reauthenticated_and_preserves_existing_resources(env):
    alice, bob = env["users"]["alice"]["id"], env["users"]["bob"]["id"]
    historical = env["ident"].create_workspace("historical fixture", owner_id=alice)
    env["ident"].add_member(historical["id"], bob, "viewer")
    env["store"].insert("projects", {"id": "fixture-project", "name": "existing", "tenant": historical["id"]})
    before = env["store"].one("SELECT * FROM projects WHERE id='fixture-project'")
    oid = create(env)["organization"]["id"]
    assert organization_for_workspace(env["store"], historical["id"]) is None
    path = f"{BASE}/{oid}/workspaces/{historical['id']}/adopt"
    assert request(env, "POST", path, {"reason": REASON}).json()["error"] == "reauth_required"
    reauth(env)
    adopted = request(env, "POST", path, {"reason": REASON})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["workspace"]["id"] == historical["id"]
    assert before == env["store"].one("SELECT * FROM projects WHERE id='fixture-project'")
    assert env["ident"].membership(historical["id"], bob)["role"] == "viewer"
    assert current_membership(env["store"], oid, bob)["role"] == "member"
    assert request(env, "POST", path, {"reason": REASON}).json()["error"] == "workspace_already_associated"
    personal = next(w for w in env["ident"].workspaces_for(alice) if w["kind"] == "personal")
    assert request(env, "POST", f"{BASE}/{oid}/workspaces/{personal['id']}/adopt", {"reason": REASON}).json()["error"] == "workspace_not_adoptable"
    strangers_ws = env["ident"].create_workspace("not Alice's", owner_id=bob)
    expect_error("no_workspace", lambda: env["orgs"].adopt_workspace(oid, alice, strangers_ws["id"], reason=REASON))


def test_two_orgs_cannot_concurrently_adopt_same_workspace(env):
    alice = env["users"]["alice"]["id"]
    orgs = [create(env, key)["organization"]["id"] for key in ("adopt-org-first", "adopt-org-second")]
    wid = env["ident"].create_workspace("contended workspace", owner_id=alice)["id"]
    stores = [Store(env["root"]), Store(env["root"])]
    barrier = threading.Barrier(2)
    def adopt(pair):
        oid, store = pair
        barrier.wait()
        try:
            Organizations(store).adopt_workspace(oid, alice, wid, reason=REASON)
            return "ok"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(adopt, zip(orgs, stores))) == ["ok", "workspace_already_associated"]


def test_invitation_membership_hook_enforces_policy_without_role_or_workspace_expansion(env):
    created = create(env)
    oid, wid, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    bob, carol = env["users"]["bob"]["id"], env["users"]["carol"]["id"]
    with env["store"].tx():
        joined = accept_workspace_invitation(env["store"], wid, bob, alice)
    assert joined["role"] == "member" and env["ident"].membership(wid, bob) is None
    env["orgs"].change_role(oid, alice, bob, "admin", reason=REASON)
    assert accept_workspace_invitation(env["store"], wid, bob, alice)["role"] == "admin"
    require_invitation_permission(env["store"], wid, bob)
    env["orgs"].update(oid, alice, invitation_policy="owners", reason=REASON)
    expect_error("organization_invitation_forbidden", lambda: accept_workspace_invitation(env["store"], wid, carol, bob))
    assert current_membership(env["store"], oid, carol) is None
    env["orgs"].transition(oid, alice, "freeze", reason=REASON)
    expect_error("organization_unavailable", lambda: accept_workspace_invitation(env["store"], wid, carol, alice))


def _authority_fixture(env, wid, uid, suffix):
    store, ts = env["store"], now()
    raw, session = Sessions(store).create(uid, workspace_id=wid, amr="password")
    tid, iid, aid = "token-" + suffix, "invite-" + suffix, "approval-" + suffix
    store.insert("oauth_tokens", {"id": tid, "client_id": "fixture-client", "user_id": uid,
                 "workspace_id": wid, "issued_at": ts, "access_expires_at": ts + 900,
                 "access_hash": hashlib.sha256(tid.encode()).hexdigest(), "scope": "openid"})
    store.insert("invitations", {"id": iid, "workspace_id": wid, "email_key": "invited@example.test",
                 "role": "viewer", "token_hash": hashlib.sha256(iid.encode()).hexdigest(), "invited_by": uid,
                 "created_at": ts, "expires_at": ts + 900})
    store.insert("api_platform_approvals", {"id": aid, "workspace_id": wid, "requested_by": uid,
                 "name": "approval fixture", "credential_kind": "api_key", "requested_scopes": "[]",
                 "requested_resources": "[]", "status": "approved", "reason": REASON,
                 "created_at": ts, "expires_at": ts + 900})
    return {"session": session["id"], "token": tid, "invite": iid, "approval": aid}


def test_freeze_delete_recover_revoke_scoped_authority_preserve_data_and_keys(env):
    created = create(env)
    oid, wid, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    personal = next(w for w in env["ident"].workspaces_for(alice) if w["kind"] == "personal")["id"]
    scoped, untouched = _authority_fixture(env, wid, alice, "org"), _authority_fixture(env, personal, alice, "personal")
    env["store"].insert("api_keys", {"id": "fixture-key", "name": "fixture", "tenant": wid,
        "scopes": "[]", "secret_hash": "not-a-real-secret", "created_at": now()})
    key_before = env["store"].one("SELECT * FROM api_keys WHERE id='fixture-key'")
    env["store"].insert("projects", {"id": "surviving-project", "name": "fixture", "tenant": wid})
    reauth(env)
    frozen = request(env, "POST", f"{BASE}/{oid}/freeze", {"reason": REASON})
    assert frozen.status_code == 200, frozen.text
    for table, key in (("sessions", "session"), ("oauth_tokens", "token"), ("invitations", "invite"), ("api_platform_approvals", "approval")):
        assert env["store"].one(f"SELECT revoked_at FROM {table} WHERE id=?", (scoped[key],))["revoked_at"]
        assert env["store"].one(f"SELECT revoked_at FROM {table} WHERE id=?", (untouched[key],))["revoked_at"] is None
    assert workspace_access_failure(env["store"], wid) == "organization_unavailable"
    assert workspace_access_failure(env["store"], personal, alice) is None
    assert request(env, "POST", f"{BASE}/{oid}/workspaces", {"name": "denied"}).status_code == 409
    deleted = request(env, "POST", f"{BASE}/{oid}/delete", {"reason": REASON}).json()["organization"]
    assert deleted["recover_until"] - deleted["deleted_at"] == RECOVERY_SECONDS
    restored = request(env, "POST", f"{BASE}/{oid}/recover", {"reason": REASON}).json()["organization"]
    assert restored["status"] == "frozen"
    assert request(env, "POST", f"{BASE}/{oid}/unfreeze", {"reason": REASON}).status_code == 200
    assert env["store"].one("SELECT * FROM projects WHERE id='surviving-project'")["tenant"] == wid
    assert env["store"].one("SELECT * FROM api_keys WHERE id='fixture-key'") == key_before
    assert env["store"].one("SELECT revoked_at FROM oauth_tokens WHERE id=?", (scoped["token"],))["revoked_at"]
    assert workspace_access_failure(env["store"], wid, alice) is None


def test_expired_recovery_never_deletes_retained_data_or_extends_window(env):
    created = create(env)
    oid, wid, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    env["orgs"].transition(oid, alice, "delete", reason=REASON)
    env["store"].execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() - 1, oid))
    expect_error("organization_recovery_expired", lambda: env["orgs"].transition(oid, alice, "recover", reason=REASON))
    expect_error("organization_state_conflict", lambda: env["orgs"].transition(oid, alice, "delete", reason=REASON))
    assert env["ident"].get_workspace(wid)["id"] == wid
    assert env["orgs"].get(oid, alice)["status"] == "deleted"


def test_removal_is_scoped_and_reinvite_cannot_restore_other_workspace_grants(env):
    created = create(env)
    oid, first, alice = created["organization"]["id"], created["workspace"]["id"], env["users"]["alice"]["id"]
    second = env["orgs"].create_workspace(oid, alice, "second")["id"]
    bob = member_fixture(env, oid, "bob")
    for wid in (first, second):
        env["ident"].add_member(wid, bob, "editor")
    scoped = _authority_fixture(env, first, bob, "removing-bob")
    personal = next(w for w in env["ident"].workspaces_for(bob) if w["kind"] == "personal")["id"]
    other = _authority_fixture(env, personal, bob, "bob-personal")
    reauth(env)
    removed = request(env, "DELETE", f"{BASE}/{oid}/members/{bob}", {"reason": REASON})
    assert removed.status_code == 200, removed.text
    assert env["ident"].membership(personal, bob)["role"] == "owner"
    assert env["store"].one("SELECT revoked_at FROM sessions WHERE id=?", (scoped["session"],))["revoked_at"]
    assert env["store"].one("SELECT revoked_at FROM sessions WHERE id=?", (other["session"],))["revoked_at"] is None
    accept_workspace_invitation(env["store"], first, bob, alice)
    env["ident"].add_member(first, bob, "viewer")
    assert env["ident"].membership(second, bob) is None
    expect_error("last_owner", lambda: env["orgs"].remove_member(oid, alice, alice, reason=REASON, leave=True))
    env["orgs"].change_role(oid, alice, bob, "owner", reason=REASON)
    expect_error("last_workspace_owner", lambda: env["orgs"].remove_member(oid, alice, alice, reason=REASON, leave=True))


def test_stale_organization_version_cannot_overwrite_updated_policy(env):
    org = create(env)["organization"]
    reauth(env)
    path = f"{BASE}/{org['id']}"
    first = request(env, "PATCH", path, {"name": "new name", "version": org["version"], "reason": REASON})
    assert first.status_code == 200
    stale = request(env, "PATCH", path, {"invitation_policy": "owners", "version": org["version"], "reason": REASON})
    assert stale.status_code == 409 and stale.json()["error"] == "organization_changed"


def test_v5_upgrade_is_additive_without_historical_adoption(tmp_path, monkeypatch):
    """Build a v5-era fixture through Store, then upgrade it through real Store."""
    import synwevo_core.organizations as org_module
    import synwevo_core.store as store_module
    root = tmp_path / "migration-fixture"
    with monkeypatch.context() as old:
        old.setattr(org_module, "SCHEMA_SQL", "")
        old.setattr(org_module, "V6_COLUMNS", [])
        old.setattr(store_module, "V6_COLUMNS", [])
        old.setattr(store_module, "SCHEMA_VERSION", 5)
        legacy = Store(root)
        actor = Identity(legacy).create_user("legacy@example.test", PW, status="active")["id"]
        existing = Identity(legacy).create_workspace("pre-org fixture", owner_id=actor)["id"]
        legacy.insert("projects", {"id": "legacy-project", "name": "retained", "tenant": "default"})
        # Exercise the existing legacy workspace seed on the actual v5 upgrade.
        legacy.insert("api_keys", {"id": "legacy-key", "name": "retained", "tenant": existing,
                      "scopes": "[]", "secret_hash": "fixture-hash", "created_at": now()})
        legacy.insert("meta", {"key": "fixture-root-key", "value": "fixture-do-not-rotate"})
        before = {table: legacy.all(f"SELECT * FROM {table}") for table in ("projects", "api_keys", "memberships")}
    upgraded = Store(root)
    assert upgraded.schema_version() >= 6
    assert upgraded.one("SELECT COUNT(*) AS n FROM organizations")["n"] == 0
    assert upgraded.one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id IS NOT NULL")["n"] == 0
    assert upgraded.one("SELECT * FROM workspaces WHERE id='default'")["status"] == "pending_claim"
    assert before == {table: upgraded.all(f"SELECT * FROM {table}") for table in before}
    assert upgraded.one("SELECT value FROM meta WHERE key='fixture-root-key'")["value"] == "fixture-do-not-rotate"
    reopened = Store(root)
    assert reopened.one("SELECT COUNT(*) AS n FROM organizations")["n"] == 0
