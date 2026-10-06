"""D11: ordinary suspension must preserve actual organization/workspace stewards.

Synthetic identities and governance rows only, in the existing isolated identity fixture.
The scheduling hook commits a real withdrawal on another Store before the writer lock;
it never replaces authorization, continuity checks or the operation's result.
"""
from __future__ import annotations

import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier

import pytest

from synwevo_core import journal
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, now
from synwevo_identity.profile import check_deletion_constraints

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, command, execute, post, preview, world  # noqa: F401


@pytest.fixture()
def governed(world):
    # Exercise the ordinary delegated administrator, not an emergency/root bypass.
    actor = make_platform_admin(world["store"], world["ident"].create_user(
        "governance-admin@example.test", PW, status="active"), role="access_admin")
    world["admin"] = world["login"](actor)
    world["reauth"](world["admin"])
    return world


def org_member(w, oid, uid, role):
    """Explicit fixture membership; no invitation or external recipient is involved."""
    w["store"].insert("organization_memberships", {
        "organization_id": oid, "user_id": uid, "role": role,
        "created_by": w["target"]["id"], "created_at": now(), "updated_at": now()})


def stewardship(w, scope, *, role="owner", successor_role=None):
    uid = w["target"]["id"]
    successor = w["ident"].create_user("successor@example.test", PW, status="active")["id"]
    if scope == "organization":
        created = Organizations(w["store"]).create(uid, "Fixture organization", "Fixture space", "d11-fixture-org")
        resource = created["organization"]["id"]
        # Organization continuity is independent of ownership of its initial workspace.
        w["store"].execute("DELETE FROM memberships WHERE workspace_id=?", (created["workspace"]["id"],))
        w["store"].execute("UPDATE organization_memberships SET role=? WHERE organization_id=? AND user_id=?",
                           (role, resource, uid))
        if successor_role:
            org_member(w, resource, successor, successor_role)
        table, column, error = "organization_memberships", "organization_id", "organization_ownership_required"
    else:
        resource = w["ident"].create_workspace("Fixture shared space", owner_id=uid, kind="org")["id"]
        w["store"].execute("UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?", (role, resource, uid))
        if successor_role:
            w["ident"].add_member(resource, successor, successor_role)
        table, column, error = "memberships", "workspace_id", "ownership_required"
    return {"id": resource, "successor": successor, "table": table, "column": column, "error": error}


def preview_response(w):
    return post(w["admin"], f"/api/admin/users/{w['target']['id']}/status-preview", {"status": "suspended"})


def assert_refused(w, response, error, phase):
    assert response.status_code == 409 and response.json()["error"] == error, response.text
    uid, store = w["target"]["id"], w["store"]
    assert w["ident"].get_user(uid)["status"] == "active"
    assert w["ordinary"].get("/api/auth/me").status_code == 200
    assert not store.one("SELECT 1 FROM admin_operations WHERE target_id=?", (uid,))
    assert not store.one("SELECT 1 FROM audit_events WHERE action='admin.user.status' AND object_id=? AND result='ok'", (uid,))
    denied = store.one("SELECT meta FROM audit_events WHERE action='admin.user.status' AND object_id=? "
                       "AND result='denied' ORDER BY seq DESC", (uid,))
    assert json.loads(denied["meta"])["code"] == error
    assert json.loads(denied["meta"])["phase"] == phase
    assert not [event for event in journal.read(store.root) if event["kind"] == "user_suspended"]


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("role,successor_role", [("owner", None), ("admin", None), ("owner", "admin")])
def test_preview_refuses_loss_of_owner_or_last_manager(governed, scope, role, successor_role):
    s = stewardship(governed, scope, role=role, successor_role=successor_role)
    assert_refused(governed, preview_response(governed), s["error"], "preview")
    # Deletion and ordinary suspension have one continuity contract.
    with governed["store"].tx(), pytest.raises(ServiceError) as caught:
        check_deletion_constraints(governed["store"], governed["target"])
    assert caught.value.code == s["error"]


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("loss", ["pending", "suspended", "pending_delete", "deleted_at", "membership", "role"])
def test_execute_rechecks_successor_after_preview(governed, scope, loss):
    s = stewardship(governed, scope, successor_role="owner")
    prepared = command(preview(governed))
    store, successor = governed["store"], s["successor"]
    if loss == "deleted_at":
        store.execute("UPDATE users SET deleted_at=? WHERE id=?", (now(), successor))
    elif loss == "membership":
        store.execute(f"DELETE FROM {s['table']} WHERE {s['column']}=? AND user_id=?", (s["id"], successor))
    elif loss == "role":
        store.execute(f"UPDATE {s['table']} SET role='admin' WHERE {s['column']}=? AND user_id=?", (s["id"], successor))
    else:
        store.execute("UPDATE users SET status=? WHERE id=?", (loss, successor))
    assert_refused(governed, execute(governed, prepared), s["error"], "execute")
    assert_refused(governed, preview_response(governed), s["error"], "preview")


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("phase", ["preview", "execute"])
def test_continuity_is_checked_after_acquiring_writer_lock(governed, monkeypatch, scope, phase):
    s = stewardship(governed, scope, successor_role="owner")
    prepared = command(preview(governed)) if phase == "execute" else None
    independent, original, scheduled = Store(governed["store"].root), Store.tx, []

    @contextmanager
    def delayed(store):
        if (not scheduled and store.root == independent.root and any(
                frame.function == phase and frame.frame.f_globals.get("__name__") == "synwevo_identity.admin_operations"
                for frame in inspect.stack())):
            scheduled.append(True)
            independent.execute("UPDATE users SET status='suspended' WHERE id=?", (s["successor"],))
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", delayed)
    response = execute(governed, prepared) if prepared else preview_response(governed)
    assert scheduled == [True]
    assert_refused(governed, response, s["error"], phase)


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("change", ["successor_role", "frozen", "membership_added"])
def test_preview_version_binds_governance_even_if_still_safe(governed, scope, change):
    s = stewardship(governed, scope, role="admin", successor_role="owner")
    prepared = command(preview(governed))
    store = governed["store"]
    if change == "successor_role":
        store.execute(f"UPDATE {s['table']} SET role='admin' WHERE {s['column']}=? AND user_id=?", (s["id"], s["successor"]))
    elif change == "membership_added":
        if scope == "organization":
            org_member(governed, s["id"], governed["root"]["id"], "owner")
        else:
            governed["ident"].add_member(s["id"], governed["root"]["id"], "owner")
    else:
        table = "organizations" if scope == "organization" else "workspaces"
        store.execute(f"UPDATE {table} SET status='frozen' WHERE id=?", (s["id"],))
    assert_refused(governed, execute(governed, prepared), "admin_preview_stale", "execute")
    assert execute(governed, command(preview(governed))).status_code == 200


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("role,successor_role", [("owner", "owner"), ("admin", "admin")])
def test_effective_successor_allows_suspension_and_idempotent_retry(governed, scope, role, successor_role):
    s = stewardship(governed, scope, role=role, successor_role=successor_role)
    prepared = command(preview(governed))
    result = execute(governed, prepared)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "suspended"
    assert execute(governed, prepared).json() == result.json()
    assert governed["ident"].get_user(s["successor"])["status"] == "active"
    assert governed["store"].one(f"SELECT role FROM {s['table']} WHERE {s['column']}=? AND user_id=?",
                                (s["id"], s["successor"]))["role"] == successor_role
    assert governed["ordinary"].get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("scope", ["organization", "workspace"])
def test_restoring_a_previously_suspended_sole_owner_is_still_possible(governed, scope):
    stewardship(governed, scope)
    # Fixture for an account stranded before D11; ordinary restore must remain available.
    governed["store"].execute("UPDATE users SET status='suspended' WHERE id=?", (governed["target"]["id"],))
    result = execute(governed, command(preview(governed, status="active"), status="active"))
    assert result.status_code == 200 and result.json()["status"] == "active", result.text
    assert governed["login"](governed["target"]).get("/api/auth/me").status_code == 200


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("state,blocked", [("frozen", True), ("recoverable", True), ("deleted", False)])
def test_recoverable_governance_is_preserved_but_deleted_resources_do_not_block(governed, scope, state, blocked):
    s = stewardship(governed, scope)
    store = governed["store"]
    if scope == "organization":
        store.execute("UPDATE organizations SET status=?,recover_until=? WHERE id=?",
                      ("frozen" if state == "frozen" else "deleted", now() + (600 if blocked else -600), s["id"]))
    elif state == "frozen":
        store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (s["id"],))
    elif state == "deleted":
        store.execute("UPDATE workspaces SET deleted_at=? WHERE id=?", (now(), s["id"]))
    else:
        org = Organizations(store).create(governed["root"]["id"], "Fixture recovering org", "Fixture space", "d11-recoverable")
        oid = org["organization"]["id"]
        org_member(governed, oid, governed["target"]["id"], "member")
        store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (oid, s["id"]))
        store.execute("UPDATE organizations SET status='deleted',recover_until=? WHERE id=?", (now()+600, oid))
    if blocked:
        assert_refused(governed, preview_response(governed), s["error"], "preview")
    else:
        assert execute(governed, command(preview(governed))).status_code == 200


def test_workspace_successor_needs_current_organization_membership(governed):
    s = stewardship(governed, "workspace", successor_role="owner")
    store = governed["store"]
    org = Organizations(store).create(governed["root"]["id"], "Fixture org", "Fixture org space", "d11-attached")
    oid = org["organization"]["id"]
    for uid in (governed["target"]["id"], s["successor"]):
        org_member(governed, oid, uid, "member")
    store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (oid, s["id"]))
    prepared = command(preview(governed))
    # Historical inconsistent membership: a workspace row alone cannot grant current access.
    store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, s["successor"]))
    assert_refused(governed, execute(governed, prepared), "ownership_required", "execute")
    with store.tx(), pytest.raises(ServiceError) as caught:
        check_deletion_constraints(store, governed["target"])
    assert caught.value.code == "ownership_required"


@pytest.mark.parametrize("shared", [False, True])
def test_personal_workspace_exception_is_only_for_a_solo_workspace(governed, shared):
    wid = governed["ident"].workspaces_for(governed["target"]["id"])[0]["id"]
    if shared:
        governed["ident"].add_member(wid, governed["root"]["id"], "viewer")
        assert_refused(governed, preview_response(governed), "ownership_required", "preview")
    else:
        assert execute(governed, command(preview(governed))).status_code == 200


@pytest.mark.parametrize("scope", ["organization", "workspace"])
def test_two_previews_cannot_suspend_both_remaining_owners(governed, scope):
    s = stewardship(governed, scope, successor_role="owner")
    first = command(preview(governed))
    second = command(preview(governed, target=s["successor"]))
    barrier = Barrier(2)

    def suspend(item):
        uid, body = item
        barrier.wait(timeout=10)
        return execute(governed, body, target=uid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(suspend, [(governed["target"]["id"], first), (s["successor"], second)]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 409).json()["error"] == s["error"]
    assert sorted(governed["ident"].get_user(uid)["status"] for uid in
                  (governed["target"]["id"], s["successor"])) == ["active", "suspended"]
