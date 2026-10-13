"""A35: the platform-scope organization lifecycle — a platform administrator who is not a member
freezes, unfreezes, soft-deletes and recovers an organization from the console through the
confirmed-operation protocol (impact preview → reason → strict re-authentication → one
transaction → audit naming the platform operator), and gains nothing in the organization.

Synthetic identities and governance rows only, in the isolated identity fixture of
test_admin_operations (`world`) with the ordinary delegated administrator of test_admin_ownership
(`governed`, role access_admin; never the root super administrator). Fixture rows that the
product's routes would not write (a session parked in an organization workspace, a token, an API
approval, a Docs job) are inserted directly and marked as such.
"""
from __future__ import annotations

import hashlib
import json
import time
import types
import uuid

import pytest

from synwevo_core import journal
from synwevo_core.apps import DOCS_APP_ID, Apps
from synwevo_core.organizations import LIFECYCLE_ACTIONS, RECOVERY_SECONDS, Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import now
from synwevo_identity import admin_organizations
from synwevo_identity.invites import Invites

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401
from .test_admin_ownership import governed, org_member  # noqa: F401

REASON = "Isolated fixture: platform governance of an organization"
BASE = "/api/admin/organizations"
TENANT = "/api/auth/organizations"
ACTION = "admin.organization.lifecycle"
ADMIN_EMAIL = "governance-admin@example.test"
VIEW = {"id", "name", "status", "deleted_at", "recover_until", "previous_status", "version"}


# ---------------------------------------------------------------- fixtures and helpers
def admin_id(w):
    return w["ident"].by_email(ADMIN_EMAIL)["id"]


@pytest.fixture()
def org(governed):
    """An organization owned by `target` with two workspaces, a second member signed in to one of
    them, a pending invitation, an unrevoked token, an approved API credential request and one
    running Docs job — everything a freeze reaches, each exactly once."""
    w = governed
    store, ident, owner = w["store"], w["ident"], w["target"]
    orgs = Organizations(store, ident)
    created = orgs.create(owner["id"], "Governed organization", "First workspace", "a35-fixture-org")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    second = orgs.create_workspace(oid, owner["id"], "Second workspace")["id"]
    member = ident.create_user("member@example.test", PW, status="active")
    org_member(w, oid, member["id"], "member")
    ident.add_member(wid, member["id"], "editor", actor_id=owner["id"])
    client = w["login"](member)
    # FIXTURE: the member's session parked in the organization workspace (a sign-in lands in the
    # account's first workspace; the applications select another per request, not on the row).
    store.execute("UPDATE sessions SET workspace_id=? WHERE user_id=? AND revoked_at IS NULL", (wid, member["id"]))
    invitation, _ = Invites(store, ident).create(workspace_id=wid, email="invited@example.test", role="viewer",
                                                 actor_id=owner["id"])
    ts = now()
    # FIXTURE rows: an issued token, an approved API credential request and a running Docs job in
    # the organization's workspaces, plus a finished job that no preview may count.
    store.insert("oauth_tokens", {"id": "a35-token", "client_id": "fixture-client", "user_id": member["id"],
                                  "workspace_id": second, "issued_at": ts, "access_expires_at": ts + 900,
                                  "access_hash": hashlib.sha256(b"a35-token").hexdigest(), "scope": "openid"})
    store.insert("api_platform_approvals", {"id": "a35-approval", "workspace_id": wid, "requested_by": owner["id"],
                                            "name": "approval fixture", "credential_kind": "api_key",
                                            "requested_scopes": "[]", "requested_resources": "[]", "status": "approved",
                                            "reason": REASON, "created_at": ts, "expires_at": ts + 900})
    store.insert("jobs", {"id": "a35-job-running", "status": "running", "created_at": ts, "tenant": wid})
    store.insert("jobs", {"id": "a35-job-done", "status": "success", "created_at": ts, "tenant": wid})
    return {**w, "oid": oid, "wid": wid, "second": second, "owner": owner, "member": member,
            "member_client": client, "invitation": invitation["id"]}


def preview(o, action, *, client=None, oid=None):
    return post(client or o["admin"], f"{BASE}/{oid or o['oid']}/preview", {"action": action})


def plan(o, action, **kwargs):
    response = preview(o, action, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def command(p, *, reason=REASON, key=None):
    return {"reason": reason, "preview_token": p["preview_token"], "idempotency_key": key or uuid.uuid4().hex}


def execute(o, action, body, *, client=None, oid=None):
    return post(client or o["admin"], f"{BASE}/{oid or o['oid']}/{action}", body)


def act(o, action, **kwargs):
    """Preview then confirm, as the console does."""
    response = execute(o, action, command(plan(o, action)), **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def status_of(o, oid=None):
    return o["store"].one("SELECT status FROM organizations WHERE id=?", (oid or o["oid"],))["status"]


def platform_rows(o, result=None, *, oid=None):
    where = " AND result=?" if result else ""
    return o["store"].all(f"SELECT * FROM audit_events WHERE action=? AND object_id=?{where} ORDER BY seq",
                          (ACTION, oid or o["oid"], *((result,) if result else ())))


def org_rows(o, action):
    return o["store"].all("SELECT * FROM audit_events WHERE action=? AND object_id=? ORDER BY seq",
                          ("organization." + action, o["oid"]))


def operations(o):
    return o["store"].all("SELECT * FROM admin_operations WHERE action=? ORDER BY created_at", (ACTION,))


def events(o, kind=None):
    return [e for e in journal.read(o["store"].root)
            if e.get("organization_id") == o["oid"] and (kind is None or e["kind"] == kind)]


def durable(o):
    """What a confirmed act leaves behind: platform records, operations, journal entries, the version."""
    return (len(platform_rows(o, "ok")), len(operations(o)), len(events(o)),
            o["store"].one("SELECT version FROM organizations WHERE id=?", (o["oid"],))["version"])


def assert_nothing_gained(o):
    """The administrator governs from outside: no membership, workspace access, entitlement or token."""
    store, uid = o["store"], admin_id(o)
    assert not store.one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (o["oid"], uid))
    for wid in (o["wid"], o["second"]):
        assert o["ident"].membership(wid, uid) is None
        assert not Apps(store, o["ident"]).check_access(uid, DOCS_APP_ID, wid)[0]
    assert not store.one("SELECT 1 FROM app_entitlements WHERE user_id=? AND workspace_id IN "
                         "(SELECT id FROM workspaces WHERE organization_id=?)", (uid, o["oid"]))
    assert not store.one("SELECT 1 FROM oauth_tokens WHERE user_id=?", (uid,))


def refused(o, call, status, code, *, phase="execute", transition="freeze", expected_status="active", oid=None):
    """One refusal: the answer, the organization untouched, nothing durable added, and the denied
    record the operation protocol writes once past the permission check."""
    before = durable(o)
    response = call()
    assert response.status_code == status and response.json()["error"] == code, response.text
    assert status_of(o) == expected_status and durable(o) == before
    assert_nothing_gained(o)
    denied = platform_rows(o, "denied", oid=oid)[-1]
    assert denied["actor_id"] == admin_id(o) and denied["workspace_id"] is None
    meta = json.loads(denied["meta"])
    assert meta["code"] == code and meta["phase"] == phase
    assert meta.get("transition") == (transition if transition in LIFECYCLE_ACTIONS else None)
    return meta


# ---------------------------------------------------------------- the preview
def test_preview_reports_the_real_impact_and_changes_nothing(org):
    o = org
    before = o["store"].one("SELECT * FROM organizations WHERE id=?", (o["oid"],))
    started = time.time()
    p = plan(o, "freeze")
    assert p["action"] == ACTION and p["scope"] == "platform" and p["risk"] == "high" and p["transition"] == "freeze"
    assert p["target"] == {"id": o["oid"], "name": "Governed organization", "status": "active", "deleted_at": None,
                           "recover_until": None, "previous_status": None, "version": before["version"]}
    assert p["result_status"] == "frozen"
    assert p["impact"] == {"members": 2, "workspaces": 2, "sessions": 1, "tokens": 1, "invitations": 1,
                           "approvals": 1, "running_jobs": 1}
    assert started + 299 <= p["expires_at"] <= time.time() + 301
    assert o["store"].one("SELECT * FROM organizations WHERE id=?", (o["oid"],)) == before
    assert o["member_client"].get("/api/auth/me").status_code == 200
    assert not platform_rows(o) and not operations(o) and not events(o)
    assert_nothing_gained(o)
    # Preview is POST-only: prefetch and link scanning cannot execute or confirm it.
    assert o["admin"].get(f"{BASE}/{o['oid']}/preview").status_code in (404, 405)
    # What each act would leave behind, from the state it needs.
    assert plan(o, "delete")["result_status"] == "deleted"
    o["store"].execute("UPDATE organizations SET status='frozen' WHERE id=?", (o["oid"],))      # FIXTURE state
    assert plan(o, "unfreeze")["result_status"] == "active" and plan(o, "delete")["result_status"] == "deleted"


@pytest.mark.parametrize("state, action, status, code", [
    ("missing", "freeze", 404, "no_organization"),
    ("active", "explode", 400, "bad_action"),
    ("active", None, 400, "bad_action"),
    ("active", "unfreeze", 409, "organization_state_conflict"),
    ("active", "recover", 409, "organization_state_conflict"),
    ("frozen", "freeze", 409, "organization_state_conflict"),
    ("deleted", "delete", 409, "organization_state_conflict"),
    ("expired", "recover", 409, "organization_recovery_expired"),
])
def test_preview_refusals_are_the_state_machines_and_are_recorded(org, state, action, status, code):
    o = org
    oid = "org_fixture_missing" if state == "missing" else o["oid"]
    if state in ("frozen", "deleted", "expired"):
        # FIXTURE state: written directly, so each refusal is judged from the status it names.
        deleted = state != "frozen"
        o["store"].execute("UPDATE organizations SET status=?,deleted_at=?,recover_until=?,previous_status=? WHERE id=?",
                           ("deleted" if deleted else "frozen", now() if deleted else None,
                            (now() - 1 if state == "expired" else now() + 3600) if deleted else None,
                            "active" if deleted else None, o["oid"]))
    r = preview(o, action, oid=oid)
    assert r.status_code == status and r.json()["error"] == code, r.text
    (denied,) = platform_rows(o, "denied", oid=oid)
    meta = json.loads(denied["meta"])
    assert meta["code"] == code and meta["phase"] == "preview" and denied["actor_id"] == admin_id(o)
    assert meta.get("transition") == (action if action in LIFECYCLE_ACTIONS else None)
    assert not operations(o) and not events(o)


# ---------------------------------------------------------------- the acts
def test_a_non_member_administrator_freezes_an_organization_from_outside(org):
    o = org
    store = o["store"]
    p = plan(o, "freeze")
    r = execute(o, "freeze", command(p))
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ok"] is True and result["state"] == "succeeded" and result["transition"] == "freeze"
    assert result["organization"]["status"] == "frozen" and result["organization"]["id"] == o["oid"]
    assert set(result["organization"]) == VIEW and result["organization"]["version"] == p["target"]["version"] + 1
    assert result["sessions_revoked"] == 1 and result["tokens_revoked"] == 1
    assert result["invitations_revoked"] == 1 and result["approvals_revoked"] == 1
    # the effect: the member's next request is refused, the invitation, token and approval are withdrawn
    assert status_of(o) == "frozen"
    assert o["member_client"].get("/api/auth/me").status_code == 401
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (o["invitation"],))["revoked_at"]
    assert store.one("SELECT revoked_at FROM oauth_tokens WHERE id='a35-token'")["revoked_at"]
    assert store.one("SELECT status FROM api_platform_approvals WHERE id='a35-approval'")["status"] == "revoked"
    assert store.one("SELECT status FROM jobs WHERE id='a35-job-running'")["status"] == "running", "a freeze stops access, not the queue"
    # the organization's own record names the platform; the platform's record names the operator
    (own,) = org_rows(o, "freeze")
    own_meta = json.loads(own["meta"])
    assert own["actor_id"] == admin_id(o) and own["workspace_id"] is None
    assert own_meta["by"] == "platform" and own_meta["reason"] == REASON
    assert own_meta["before"] == {"status": "active"} and own_meta["after"] == {"status": "frozen"} and own_meta["changed"] == ["status"]
    (row,) = platform_rows(o, "ok")
    meta = json.loads(row["meta"])
    assert row["actor_id"] == admin_id(o) and row["object_kind"] == "organization" and row["workspace_id"] is None
    assert meta["before"] == {"status": "active"} and meta["after"] == {"status": "frozen"} and meta["changed"] == ["status"]
    assert meta["transition"] == "freeze" and meta["reason"] == REASON and meta["impact"] == p["impact"]
    assert meta["operation_id"] == result["operation_id"] and meta["environment"] == "http://testserver"
    assert row["seq"] < own["seq"], "the platform's record precedes the organization's (and the journal), inside one transaction"
    (operation,) = operations(o)
    assert operation["id"] == result["operation_id"] and operation["target_id"] == o["oid"] and operation["actor_id"] == admin_id(o)
    assert json.loads(operation["result_json"]) == result
    (event,) = events(o)
    assert event["kind"] == "organization_frozen"
    assert_nothing_gained(o)
    # the owner sees what the platform did; the organization's own governance log says who
    owner_view = o["ordinary"].get(f"{TENANT}/{o['oid']}")
    assert owner_view.status_code == 200 and owner_view.json()["organization"]["status"] == "frozen"
    logged = next(x for x in o["ordinary"].get(f"{TENANT}/{o['oid']}/audit").json()["items"] if x["action"] == "organization.freeze")
    assert logged["detail"]["by"] == "platform" and logged["actor"] == {"kind": "platform_admin"}


def test_unfreeze_restores_without_a_journal_entry_or_revocations(org):
    o = org
    act(o, "freeze")
    frozen_events = len(events(o))
    frozen_sessions = o["store"].all("SELECT id FROM sessions WHERE revoked_at IS NOT NULL")
    raw, _ = Sessions(o["store"]).create(o["member"]["id"], workspace_id=o["wid"], amr="password")
    result = act(o, "unfreeze")
    assert result["transition"] == "unfreeze" and result["organization"]["status"] == "active"
    assert not any(key in result for key in ("sessions_revoked", "tokens_revoked", "invitations_revoked", "approvals_revoked"))
    assert status_of(o) == "active" and len(events(o)) == frozen_events
    assert o["store"].all("SELECT id FROM sessions WHERE revoked_at IS NOT NULL") == frozen_sessions
    assert Sessions(o["store"]).load(raw) is not None
    (own,) = org_rows(o, "unfreeze")
    assert json.loads(own["meta"])["by"] == "platform"
    assert [json.loads(r["meta"])["transition"] for r in platform_rows(o, "ok")] == ["freeze", "unfreeze"]
    assert len(operations(o)) == 2
    assert_nothing_gained(o)


def test_delete_opens_the_recovery_window_and_recover_closes_it(org):
    o = org
    result = act(o, "delete")
    organization = result["organization"]
    assert organization["status"] == "deleted" and organization["previous_status"] == "active"
    assert organization["recover_until"] - organization["deleted_at"] == RECOVERY_SECONDS
    assert result["sessions_revoked"] == 1 and status_of(o) == "deleted"
    (event,) = events(o)
    assert event["kind"] == "organization_deleted" and event["recover_until"] == organization["recover_until"]
    assert event["deleted_at"] == organization["deleted_at"] and event["previous_status"] == "active"
    p = plan(o, "recover")
    assert p["result_status"] == "active" and p["target"]["recover_until"] == organization["recover_until"]
    recovered = execute(o, "recover", command(p))
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["organization"] == {**organization, "status": "active", "deleted_at": None, "recover_until": None,
                                                "previous_status": None, "version": organization["version"] + 1}
    assert status_of(o) == "active" and len(events(o)) == 1, "a recovery writes no journal entry"
    assert json.loads(org_rows(o, "recover")[0]["meta"])["by"] == "platform"
    # a frozen organization deleted and recovered comes back frozen
    act(o, "freeze")
    assert act(o, "delete")["organization"]["previous_status"] == "frozen"
    assert act(o, "recover")["organization"]["status"] == "frozen"
    assert_nothing_gained(o)


def test_the_recovery_window_is_judged_at_the_moment_of_the_act(org):
    o = org
    act(o, "delete")
    # FIXTURE: the window closes between the preview and the confirmation.
    # Previewed while open, then closed (fixture UPDATE) before the confirmation: the state machine
    # judges the window inside the write, before the plan's version is even compared.
    o["store"].execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() + 3600, o["oid"]))
    p = plan(o, "recover")
    o["store"].execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() - 1, o["oid"]))
    refused(o, lambda: execute(o, "recover", command(p)), 409, "organization_recovery_expired",
            transition="recover", expected_status="deleted")
    # FIXTURE: and once it has closed, there is no preview to confirm.
    o["store"].execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() - 1, o["oid"]))
    r = preview(o, "recover")
    assert r.status_code == 409 and r.json()["error"] == "organization_recovery_expired"
    assert status_of(o) == "deleted" and len(events(o)) == 1
    assert len(operations(o)) == 1 and len(platform_rows(o, "ok")) == 1, "the deletion only"


def test_an_owner_in_the_console_also_takes_the_platform_path(org):
    """An organization owner holding a platform role governs from the console as the platform:
    confirmed, with `by: platform` — and keeps exactly the membership they had."""
    o = org
    owner_admin = make_platform_admin(o["store"], o["owner"]["id"], role="access_admin")
    client = o["login"](owner_admin)
    o["reauth"](client)
    assert execute(o, "freeze", {"reason": REASON}, client=client).json()["error"] == "admin_confirmation_required"
    result = execute(o, "freeze", command(plan(o, "freeze", client=client)), client=client)
    assert result.status_code == 200 and result.json()["organization"]["status"] == "frozen", result.text
    (own,) = org_rows(o, "freeze")
    assert own["actor_id"] == o["owner"]["id"] and json.loads(own["meta"])["by"] == "platform"
    assert o["store"].one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?",
                          (o["oid"], o["owner"]["id"]))["role"] == "owner"


# ---------------------------------------------------------------- the refusals
def test_reauthentication_is_checked_before_the_body_is_read(org):
    o = org
    fresh = o["login"](o["ident"].by_email(ADMIN_EMAIL))
    for body in ({}, {"reason": REASON}, command(plan(o, "freeze"))):
        r = execute(o, "freeze", body, client=fresh)
        assert r.status_code == 403 and r.json()["error"] == "reauth_required", r.text
    assert status_of(o) == "active" and not platform_rows(o) and not operations(o)
    # a preview needs no re-authentication: it changes nothing
    assert preview(o, "freeze", client=fresh).status_code == 200


def test_a_support_role_is_refused_on_both_routes(org):
    o = org
    support = make_platform_admin(o["store"], o["ident"].create_user("support@example.test", PW, status="active"), role="support")
    client = o["login"](support)
    o["reauth"](client)
    for r in (preview(o, "freeze", client=client), execute(o, "freeze", command(plan(o, "freeze")), client=client)):
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", r.text
        assert r.json()["detail"]["permission"] == "platform.orgs.manage"
    assert status_of(o) == "active" and not platform_rows(o) and not operations(o)
    denied = o["store"].one("SELECT meta FROM audit_events WHERE action='admin.access.denied' AND actor_id=?", (support["id"],))
    assert json.loads(denied["meta"])["code"] == "platform_permission_denied"


def test_the_reason_the_key_and_the_token_are_required(org):
    o = org
    p = plan(o, "freeze")
    meta = refused(o, lambda: execute(o, "freeze", {**command(p), "reason": "short"}), 400, "missing_reason")
    assert "reason" not in meta
    refused(o, lambda: execute(o, "freeze", {**command(p), "reason": None}), 400, "missing_reason")
    refused(o, lambda: execute(o, "freeze", {"reason": REASON}), 400, "admin_confirmation_required")
    refused(o, lambda: execute(o, "freeze", {**command(p), "idempotency_key": "short"}), 400, "admin_confirmation_required")
    refused(o, lambda: execute(o, "freeze", {**command(p), "idempotency_key": "has spaces in it 1234"}), 400, "admin_confirmation_required")
    meta = refused(o, lambda: execute(o, "freeze", {**command(p), "preview_token": None}), 400, "admin_confirmation_required")
    assert meta["reason"] == REASON


@pytest.mark.parametrize("alteration", ["organization", "transition", "actor", "origin", "signature", "expired"])
def test_a_preview_binds_organization_transition_actor_origin_and_time(org, monkeypatch, alteration):
    o = org
    p, action, other_oid, headers = plan(o, "freeze"), "freeze", None, {}
    if alteration == "organization":
        other = Organizations(o["store"], o["ident"]).create(o["owner"]["id"], "Other organization", "Other space", "a35-other-org")
        other_oid = other["organization"]["id"]
    elif alteration == "transition":
        action = "delete"
    elif alteration == "actor":
        second = make_platform_admin(o["store"], o["ident"].create_user("second-admin@example.test", PW, status="active"),
                                     role="access_admin")
        client = o["login"](second)
        o["reauth"](client)
        p = plan(o, "freeze", client=client)                 # signed for the second administrator
    elif alteration == "origin":
        headers = {"host": "localhost"}
    elif alteration == "signature":
        p = {**p, "preview_token": p["preview_token"] + "tampered"}
    else:
        monkeypatch.setattr(admin_organizations, "time", types.SimpleNamespace(time=lambda: p["expires_at"] + 1))
    expected = "admin_preview_expired" if alteration == "expired" else "admin_preview_invalid"

    def call():
        return o["admin"].post(f"{BASE}/{other_oid or o['oid']}/{action}", json=command(p),
                               headers={CSRF_HEADER: o["admin"].cookies.get(CSRF_COOKIE) or "", **headers})

    refused(o, call, 409, expected, transition=action, oid=other_oid)
    assert not events(o)
    if other_oid:
        assert status_of(o, other_oid) == "active"


@pytest.mark.parametrize("change", ["member_joins", "workspace_created", "owner_froze_it"])
def test_a_changed_organization_makes_the_preview_stale(org, change):
    o = org
    action = "delete" if change == "owner_froze_it" else "freeze"
    p = plan(o, action)
    if change == "member_joins":
        org_member(o, o["oid"], o["ident"].create_user("latecomer@example.test", PW, status="active")["id"], "member")
    elif change == "workspace_created":
        Organizations(o["store"], o["ident"]).create_workspace(o["oid"], o["owner"]["id"], "Third workspace")
    else:
        # The owner acts first, through the tenant route: the plan was for an active organization.
        o["reauth"](o["ordinary"])
        owner = post(o["ordinary"], f"{TENANT}/{o['oid']}/freeze", {"reason": REASON})
        assert owner.status_code == 200, owner.text
    refused(o, lambda: execute(o, action, command(p)), 409, "admin_preview_stale", transition=action,
            expected_status="frozen" if change == "owner_froze_it" else "active")
    if change == "owner_froze_it":
        (own,) = org_rows(o, "freeze")
        assert own["actor_id"] == o["owner"]["id"] and "by" not in json.loads(own["meta"]), "the owner's own record"
        assert [e["kind"] for e in events(o)] == ["organization_frozen"], "the owner's entry, no second one"


def test_an_act_the_state_does_not_allow_is_refused_before_confirmation(org):
    o = org
    refused(o, lambda: execute(o, "unfreeze", {"reason": REASON}), 409, "organization_state_conflict", transition="unfreeze")
    refused(o, lambda: execute(o, "recover", command(plan(o, "freeze"))), 409, "organization_state_conflict", transition="recover")
    refused(o, lambda: execute(o, "explode", {"reason": REASON}), 400, "bad_action", transition="explode")
    refused(o, lambda: execute(o, "freeze", {"reason": REASON}, oid="org_fixture_missing"), 404, "no_organization",
            oid="org_fixture_missing")


# ---------------------------------------------------------------- idempotency
def test_identical_retries_return_the_stored_result_and_write_nothing_twice(org, monkeypatch):
    o = org
    body = command(plan(o, "freeze"))
    first = execute(o, "freeze", body)
    assert first.status_code == 200, first.text
    monkeypatch.setattr(admin_organizations, "time", types.SimpleNamespace(time=lambda: time.time() + 3600))
    retry = execute(o, "freeze", {**body, "reason": "  " + REASON + "  "})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(platform_rows(o)) == len(operations(o)) == len(events(o)) == len(org_rows(o, "freeze")) == 1
    assert json.loads(operations(o)[0]["result_json"]) == first.json()
    # the same key with another request is a conflict, not a second act
    conflict = execute(o, "freeze", {**body, "reason": REASON + " changed"})
    assert conflict.status_code == 409 and conflict.json()["error"] == "idempotency_conflict", conflict.text
    other = execute(o, "delete", {**body, "preview_token": plan(o, "delete")["preview_token"]})
    assert other.status_code == 409 and other.json()["error"] == "idempotency_conflict", other.text
    assert status_of(o) == "frozen" and len(operations(o)) == 1 and len(platform_rows(o, "ok")) == 1


# ---------------------------------------------------------------- the owner's route and the routes
def test_the_owners_tenant_route_is_unchanged(org):
    o = org
    o["reauth"](o["ordinary"])
    r = post(o["ordinary"], f"{TENANT}/{o['oid']}/freeze", {"reason": REASON})
    assert r.status_code == 200, r.text
    assert r.json()["organization"]["role"] == "owner" and r.json()["organization"]["status"] == "frozen"
    assert "usage" in r.json()["organization"] and r.json()["sessions_revoked"] == 1, "the members' view, as before"
    (own,) = org_rows(o, "freeze")
    assert own["actor_id"] == o["owner"]["id"] and "by" not in json.loads(own["meta"])
    assert [e["kind"] for e in events(o)] == ["organization_frozen"]
    # a member who is not an owner is refused there, and so is the platform administrator (not a member)
    member = o["login"](o["member"])
    o["reauth"](member)
    assert post(member, f"{TENANT}/{o['oid']}/unfreeze", {"reason": REASON}).status_code == 403
    assert post(o["admin"], f"{TENANT}/{o['oid']}/unfreeze", {"reason": REASON}).status_code == 404
    assert status_of(o) == "frozen" and not platform_rows(o) and not operations(o)


def test_preview_and_the_generic_action_do_not_swallow_each_other(org):
    o = org
    paths = list(o["api"].openapi()["paths"])
    preview_path, generic = f"{BASE}/{{oid}}/preview", f"{BASE}/{{oid}}/{{action}}"
    assert paths.index(preview_path) < paths.index(generic)
    assert paths.index(f"{BASE}/{{oid}}/recover-owner") < paths.index(generic), "A37's specific route still wins"
    # a preview is a preview, not the act "preview": a session without the strict re-authentication
    # reaches it (the generic act would have answered reauth_required), and a body without `action`
    # is its own refusal, not the act's
    fresh = o["login"](o["ident"].by_email(ADMIN_EMAIL))
    assert preview(o, "freeze", client=fresh).json()["result_status"] == "frozen"
    r = post(fresh, f"{BASE}/{o['oid']}/preview", {"reason": REASON})
    assert r.status_code == 400 and r.json()["error"] == "bad_action", r.text
    assert status_of(o) == "active"
    # and an act with a preview-shaped body is the act, judged as one
    r = execute(o, "freeze", {"action": "freeze"})
    assert r.status_code == 400 and r.json()["error"] == "missing_reason", r.text


# ---------------------------------------------------------------- review follow-ups
def test_a_member_swap_between_preview_and_confirmation_makes_the_preview_stale(org):
    """One member leaving while another joins keeps the counts equal; the plan binds who, not how many."""
    o = org
    p = plan(o, "freeze")
    newcomer = o["ident"].create_user("newcomer@example.test", PW, status="active")
    # FIXTURE: the swap the counts cannot see — the second member leaves, a newcomer joins.
    o["store"].execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (o["oid"], o["member"]["id"]))
    org_member(o, o["oid"], newcomer["id"], "member")
    refused(o, lambda: execute(o, "freeze", command(p)), 409, "admin_preview_stale")


def test_the_journal_failing_commits_nothing_and_is_recorded_as_an_error(org, monkeypatch):
    from synwevo_core import organizations as core_organizations

    def unavailable(*args, **kwargs):
        raise OSError("fixture: security journal unavailable")

    o = org
    p = plan(o, "freeze")
    before = durable(o)
    original = core_organizations.record_organization
    monkeypatch.setattr(core_organizations, "record_organization", unavailable)
    r = execute(o, "freeze", command(p))
    # Restore only this function: undo() would also revert the fixture's environment.
    monkeypatch.setattr(core_organizations, "record_organization", original)
    assert r.status_code == 503 and r.json()["error"] == "journal_unavailable", r.text
    assert "fixture: security journal unavailable" not in r.text
    assert status_of(o) == "active" and durable(o) == before
    assert not org_rows(o, "freeze"), "the organization's own record rolled back with the status"
    assert o["member_client"].get("/api/auth/me").status_code == 200, "nothing was revoked"
    error = platform_rows(o, "error")[-1]
    assert json.loads(error["meta"])["code"] == "journal_unavailable"


def test_the_platform_record_failing_commits_nothing(org, monkeypatch):
    from .test_admin import _audit_insert_fails

    o = org
    p = plan(o, "freeze")
    before = durable(o)
    restore = _audit_insert_fails(monkeypatch, ACTION)
    r = execute(o, "freeze", command(p))
    restore()
    assert r.status_code >= 500, r.text
    assert "fixture: audit storage unavailable" not in r.text
    assert status_of(o) == "active" and durable(o) == before and not events(o)
    assert not org_rows(o, "freeze") and not platform_rows(o, "ok")


def test_the_organizations_own_log_shows_the_act_but_not_the_platforms_records(org):
    """Its owner reads `organization.freeze` marked `by: platform`; the console's records about it
    (the platform row, a refused preview with the operator's reason) stay platform administration."""
    o = org
    refused_preview = preview(o, "recover")          # a refused preview: recorded, platform-side
    assert refused_preview.status_code == 409
    act(o, "freeze")
    act(o, "unfreeze")
    owner = o["login"](o["owner"])
    r = owner.get(f"{TENANT}/{o['oid']}/audit")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert not [i for i in items if i["action"].startswith("admin.")], items
    frozen = [i for i in items if i["action"] == "organization.freeze"]
    assert len(frozen) == 1 and frozen[0]["detail"]["by"] == "platform", frozen
    assert platform_rows(o, "denied") and len(platform_rows(o, "ok")) == 2, "the platform keeps its own records"

