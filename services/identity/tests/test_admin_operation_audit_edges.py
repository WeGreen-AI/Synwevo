"""Audit and confirmation edge cases against the real isolated identity factory.

Every account, session and credential is a synthetic fixture. These checks independently
assert rejection, persisted failure records and unchanged access; no production data is used.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core import journal
from synwevo_core.store import now
from synwevo_identity.mfa import Tickets

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, command, execute, post, preview, world as world


def _audit_cursor(store):
    return store.one("SELECT COALESCE(MAX(seq),0) AS seq FROM audit_events")["seq"]


def _new_audits(store, after):
    return store.all("SELECT * FROM audit_events WHERE seq>? ORDER BY seq", (after,))


@pytest.mark.parametrize("reason,private_fragment", [
    pytest.param(None, None, id="non-string-null"),
    pytest.param(418279536, "418279536", id="non-string-number"),
    pytest.param({"private": "fixture-reason-object-secret"}, "fixture-reason-object-secret",
                 id="non-string-object"),
    pytest.param("qZ9!s", "qZ9!s", id="too-short"),
    pytest.param("fixture-private-long-reason-" * 20, "fixture-private-long-reason-", id="too-long"),
])
def test_invalid_reason_is_audited_without_saving_the_reason_or_request_body(world, reason, private_fragment):
    store, uid = world["store"], world["target"]["id"]
    plan = preview(world)
    body = {**command(plan), "reason": reason, "unrelated_private_body": "fixture-body-must-never-be-logged"}
    before = world["ident"].get_user(uid)
    cursor = _audit_cursor(store)

    response = execute(world, body)

    assert response.status_code == 400 and response.json()["error"] == "missing_reason", response.text
    assert world["ident"].get_user(uid) == before
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert not store.all("SELECT * FROM admin_operations")
    assert not journal.read(store.root)
    audits = _new_audits(store, cursor)
    failures = [row for row in audits if row["action"] == "admin.user.status"]
    assert len(failures) == 1
    event = failures[0]
    assert event["result"] == "denied"
    assert event["actor_id"] == world["root"]["id"] and event["object_id"] == uid
    assert event["object_kind"] == "user" and event["workspace_id"] is None
    assert event["request_id"]
    meta = json.loads(event["meta"])
    assert meta["code"] == "missing_reason" and meta["phase"] == "execute"
    assert not meta.get("reason")
    rendered = json.dumps(audits)
    for secret in (plan["preview_token"], body["unrelated_private_body"], private_fragment):
        if secret is not None:
            assert secret not in rendered


def test_lower_admin_restore_preview_keeps_both_denial_audits_after_rollback(world):
    store, uid = world["store"], world["target"]["id"]
    make_platform_admin(store, world["target"])
    # Synthetic pre-existing suspended highest administrator, not a permitted bypass in the API.
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
    actor = make_platform_admin(store, world["ident"].create_user(
        "access-preview@example.test", PW, status="active"), role="access_admin")
    client = world["login"](actor)
    before = world["ident"].get_user(uid)
    grants = store.all("SELECT * FROM platform_role_grants WHERE user_id=? ORDER BY id", (uid,))
    cursor = _audit_cursor(store)

    response = post(client, f"/api/admin/users/{uid}/status-preview", {"status": "active"})

    assert response.status_code == 403 and response.json()["error"] == "target_outranks_actor", response.text
    assert world["ident"].get_user(uid) == before
    assert store.all("SELECT * FROM platform_role_grants WHERE user_id=? ORDER BY id", (uid,)) == grants
    assert not store.all("SELECT * FROM admin_operations")
    assert not journal.read(store.root)
    audits = _new_audits(store, cursor)
    refusals = [row for row in audits if row["action"] == "admin.access.denied"]
    operations = [row for row in audits if row["action"] == "admin.user.status"]
    assert len(refusals) == len(operations) == 1
    for event in [refusals[0], operations[0]]:
        assert event["result"] == "denied"
        assert event["actor_id"] == actor["id"] and event["object_id"] == uid
        assert event["object_kind"] == "user" and event["request_id"]
        assert json.loads(event["meta"])["code"] == "target_outranks_actor"
    assert operations[0]["request_id"] == refusals[0]["request_id"]
    assert operations[0]["workspace_id"] is None
    assert json.loads(operations[0]["meta"])["phase"] == "preview"
    assert json.loads(refusals[0]["meta"])["path"] == f"/api/admin/users/{uid}/status-preview"


def test_reclaimed_ticket_rowid_does_not_make_a_different_proof_match_old_preview(world):
    store, uid = world["store"], world["target"]["id"]
    tickets = Tickets(store)
    tickets.issue(world["root"]["id"])  # Keep the lower row so the swept maximum is reused.
    old = tickets.issue(uid)
    original = store.one("SELECT rowid AS storage_row,id FROM mfa_tickets WHERE user_id=?", (uid,))
    assert original["storage_row"] == store.one("SELECT MAX(rowid) AS last FROM mfa_tickets")["last"]
    plan = preview(world)
    assert plan["impact"]["pending_authentication"] == 1

    # Real ticket creation sweeps tickets expired over an hour ago; the highest row can be reused.
    store.execute("UPDATE mfa_tickets SET expires_at=? WHERE id=?", (now() - 3601, original["id"]))
    replacement = tickets.issue(uid)
    current = store.one("SELECT rowid AS storage_row,id FROM mfa_tickets WHERE user_id=?", (uid,))
    assert current["storage_row"] == original["storage_row"]
    assert current["id"] != original["id"] and replacement != old
    assert tickets.load(old) is None and tickets.load(replacement) is not None

    response = execute(world, command(plan))

    assert response.status_code == 409 and response.json()["error"] == "admin_preview_stale", response.text
    assert world["ident"].get_user(uid)["status"] == "active"
    assert world["ordinary"].get("/api/auth/me").status_code == 200
    assert tickets.load(replacement) is not None
    assert not store.all("SELECT * FROM admin_operations")
    assert not journal.read(store.root)
    fresh = preview(world)
    assert fresh["impact"] == plan["impact"]  # Same counts still refer to different proof identities.
    accepted = execute(world, command(fresh))
    assert accepted.status_code == 200 and accepted.json()["status"] == "suspended", accepted.text
    assert tickets.load(replacement) is None
