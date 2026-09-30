"""Real admin routes with withdrawals committed immediately before their writer lock.

The scheduling hook only interleaves real Store writes. It never replaces authorization,
changes a production store, or fabricates a successful route result.
"""
from __future__ import annotations

import inspect
import json
from contextlib import contextmanager

import pytest

from synwevo_core import journal
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import PlatformAccess
from synwevo_core.sessions import REAUTH_SECONDS, Sessions
from synwevo_core.store import Store, now

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, REASON, post, world  # noqa: F401 -- shared isolated fixture


def prepare(w, operation):
    store = w["store"]
    if operation == "organization":
        org = Organizations(store).create(w["root"]["id"], "Fixture organization", "Fixture workspace",
                                          "fixture-delayed-admin-write")
        oid = org["organization"]["id"]
        return f"/api/admin/organizations/{oid}/freeze", {"reason": REASON}, oid
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','Fixture historical workspace','legacy','pending_claim',?,?)", (ts, ts))
    body = {"reason": REASON, "user_id": w["target"]["id"]}
    if operation == "unclaim":
        response = post(w["admin"], "/api/admin/workspaces/default/claim", body)
        assert response.status_code == 200, response.text
        body = {"reason": REASON, "expected_members": [w["target"]["id"]]}
    return f"/api/admin/workspaces/default/{operation}", body, "default"


def interleave_before_writer(monkeypatch, w, mutate):
    """Commit a separate connection's change after admission, before the route gets its lock."""
    original, scheduled = Store.tx, []
    independent = Store(w["store"].root)
    route_names = {"claim", "unclaim", "govern_organization"}

    @contextmanager
    def delayed(store):
        if (not scheduled and store.root == independent.root
                and any(frame.function in route_names for frame in inspect.stack())):
            scheduled.append(True)
            mutate(independent)
        with original(store) as connection:
            yield connection

    monkeypatch.setattr(Store, "tx", delayed)
    return scheduled


def assert_unchanged(w, operation, object_id):
    if operation == "organization":
        assert w["store"].one("SELECT status FROM organizations WHERE id=?", (object_id,))["status"] == "active"
        action = "organization.freeze"
    else:
        expected = "active" if operation == "unclaim" else "pending_claim"
        assert w["ident"].get_workspace("default")["status"] == expected
        member = w["ident"].membership("default", w["target"]["id"])
        assert bool(member) == (operation == "unclaim")
        action = "admin.workspace." + operation
    assert not w["store"].one("SELECT 1 FROM audit_events WHERE action=? AND result='ok'", (action,))
    assert not [entry for entry in journal.read(w["store"].root)
                if entry["kind"] in (journal.UNCLAIM_KIND, "organization_frozen")]


@pytest.mark.parametrize("operation", ["claim", "unclaim", "organization"])
@pytest.mark.parametrize("withdrawal,error", [("session", "unauthorized"), ("grant", "not_found"),
                                               ("reauth", "reauth_required")])
def test_withdrawn_admission_cannot_commit_an_admin_write(world, monkeypatch, operation, withdrawal, error):
    path, body, object_id = prepare(world, operation)
    root_id = world["root"]["id"]
    second = make_platform_admin(world["store"], world["ident"].create_user(
        "successor@example.test", PW, status="active"))
    grant = PlatformAccess(world["store"]).live_grants(root_id)[0]
    sid = world["store"].one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (root_id,))["id"]

    def withdraw(store):
        if withdrawal == "session":
            Sessions(store).revoke(sid, "fixture_lost_device")
        elif withdrawal == "grant":
            PlatformAccess(store).revoke(second["id"], grant["id"], REASON)
        else:
            store.execute("UPDATE sessions SET reauth_at=? WHERE id=?", (now()-REAUTH_SECONDS-1, sid))

    scheduled = interleave_before_writer(monkeypatch, world, withdraw)
    response = post(world["admin"], path, body)
    assert scheduled == [True]
    assert response.status_code in (401, 403, 404) and response.json()["error"] == error, response.text
    assert_unchanged(world, operation, object_id)
    denied = world["store"].one("SELECT meta FROM audit_events WHERE action='admin.access.denied' "
                                 "AND object_id=? ORDER BY seq DESC", (object_id,))
    assert denied and json.loads(denied["meta"])["code"] == error


@pytest.mark.parametrize("state", ["suspended", "pending_delete", "deleted"])
def test_claim_rechecks_target_availability_after_waiting_for_the_writer(world, monkeypatch, state):
    path, body, object_id = prepare(world, "claim")

    def unavailable(store):
        store.execute("UPDATE users SET status=?,deleted_at=? WHERE id=?",
                      (state, now() if state == "deleted" else None, world["target"]["id"]))

    scheduled = interleave_before_writer(monkeypatch, world, unavailable)
    response = post(world["admin"], path, body)
    assert scheduled == [True]
    assert response.status_code == 400 and response.json()["error"] == "bad_target", response.text
    assert_unchanged(world, "claim", object_id)


def test_organization_owner_is_rechecked_inside_the_same_admin_transaction(world, monkeypatch):
    path, body, oid = prepare(world, "organization")

    def demote(store):
        # Fixture: the ownership change is committed by a separate transaction before this one.
        store.execute("UPDATE organization_memberships SET role='member',updated_at=? "
                      "WHERE organization_id=? AND user_id=?", (now(), oid, world["root"]["id"]))

    scheduled = interleave_before_writer(monkeypatch, world, demote)
    response = post(world["admin"], path, body)
    assert scheduled == [True]
    assert response.status_code == 403 and response.json()["error"] == "forbidden", response.text
    assert PlatformAccess(world["store"]).is_usable_super_admin(world["root"]["id"])
    assert_unchanged(world, "organization", oid)
