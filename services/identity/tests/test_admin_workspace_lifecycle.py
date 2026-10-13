"""A44: the platform's workspace lifecycle — a platform administrator who is not a member renames,
soft-deletes (seven-day recovery) and recovers a workspace from the console through the
confirmed-operation protocol (impact preview → reason → strict re-authentication → one
transaction → audit naming the platform operator), and gains nothing in the workspace.

Synthetic identities and governance rows only, in the isolated identity fixture of
test_admin_operations (`world`) with the ordinary delegated administrator of test_admin_ownership
(`governed`, role access_admin). Fixture rows that the product's routes would not write here (a
session parked in the workspace, a token, an authorization code, an API approval, an API key, a
service account, a Docs job, project and webhook, a Forms link, an organization template) are
inserted directly and marked as such.
"""
from __future__ import annotations

import hashlib
import json
import time
import types
import uuid

import pytest

from synwevo_core import authority, journal, workspace_lifecycle
from synwevo_core.apps import DOCS_APP_ID, Apps
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import ROLE_ORDER, ROLES
from synwevo_core.store import now
from synwevo_identity import admin_workspaces
from synwevo_identity.invites import Invites

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401
from .test_admin_ownership import governed, org_member  # noqa: F401

REASON = "Isolated fixture: platform governance of a workspace"
BASE = "/api/admin/workspaces"
ACTION = "admin.workspace.lifecycle"
ADMIN_EMAIL = "governance-admin@example.test"
ROW = {"id", "name", "kind", "status", "organization_id", "organization", "created_at", "deleted_at",
       "recover_until", "previous_status", "members"}
IMPACT = {"members", "usable_owners", "sessions", "tokens", "invitations", "approvals", "api_keys",
          "service_accounts", "running_jobs", "webhooks", "form_links", "projects", "org_templates"}


# ---------------------------------------------------------------- fixtures and helpers
def admin_id(w):
    return w["ident"].by_email(ADMIN_EMAIL)["id"]


@pytest.fixture()
def space(governed):
    """An organization workspace owned by `target`, with a second member signed in to it, a pending
    invitation, an unrevoked token, an unused authorization code, an approved API credential
    request, an API key and a service account bound to no approval, one running Docs job the member
    submitted, an active Docs webhook, a project, an open Forms link and an organization template
    published from it — everything a deletion reaches or keeps, each exactly once. Beside them, an
    operator's running job (no descriptor), which a deletion does not halt and no preview counts."""
    w = governed
    store, ident, owner = w["store"], w["ident"], w["target"]
    created = Organizations(store, ident).create(owner["id"], "Governed organization", "Governed workspace",
                                                 "a44-fixture-org")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    member = ident.create_user("member@example.test", PW, status="active")
    org_member(w, oid, member["id"], "member")
    ident.add_member(wid, member["id"], "editor", actor_id=owner["id"])
    client = w["login"](member)
    # FIXTURE: the member's session parked in the workspace (applications select one per request).
    store.execute("UPDATE sessions SET workspace_id=? WHERE user_id=? AND revoked_at IS NULL", (wid, member["id"]))
    invitation, _ = Invites(store, ident).create(workspace_id=wid, email="invited@example.test", role="viewer",
                                                 actor_id=owner["id"])
    ts = now()
    # FIXTURE rows in the workspace, plus a finished job that no preview may count.
    store.insert("oauth_tokens", {"id": "a44-token", "client_id": "fixture-client", "user_id": member["id"],
                                  "workspace_id": wid, "issued_at": ts, "access_expires_at": ts + 900,
                                  "access_hash": hashlib.sha256(b"a44-token").hexdigest(), "scope": "openid"})
    store.insert("oauth_codes", {"code_hash": "a44-code", "client_id": "fixture-client", "user_id": member["id"],
                                 "redirect_uri": "http://localhost/callback", "scope": "openid", "auth_time": ts,
                                 "workspace_id": wid, "created_at": ts, "expires_at": ts + 60})
    store.insert("api_platform_approvals", {"id": "a44-approval", "workspace_id": wid, "requested_by": owner["id"],
                                            "name": "approval fixture", "credential_kind": "api_key",
                                            "requested_scopes": "[]", "requested_resources": "[]", "status": "approved",
                                            "reason": REASON, "created_at": ts, "expires_at": ts + 900})
    store.insert("api_keys", {"id": "a44-key", "name": "key fixture", "tenant": wid, "scopes": "read",
                              "secret_hash": hashlib.sha256(b"a44-key").hexdigest(), "created_at": ts})
    store.insert("oauth_clients", {"client_id": "a44-service", "app_id": DOCS_APP_ID, "name": "service fixture",
                                   "redirect_uris": "[]", "kind": "service", "workspace_id": wid,
                                   "created_at": ts, "updated_at": ts})
    store.insert("jobs", {"id": "a44-job-running", "status": "running", "created_at": ts, "tenant": wid,
                          "principal": authority.dumps(authority.describe(user=member, tenant=wid))})
    store.insert("jobs", {"id": "a44-job-operator", "status": "queued", "created_at": ts, "tenant": wid})
    store.insert("jobs", {"id": "a44-job-done", "status": "success", "created_at": ts, "tenant": wid})
    store.insert("webhook_subscriptions", {"id": "a44-webhook", "tenant": wid, "url": "https://receiver.example.test/hook",
                                           "events": '["*"]', "secret_enc": "fixture-sealed", "created_at": ts,
                                           "updated_at": ts})
    store.insert("projects", {"id": "a44-project", "name": "project fixture", "tenant": wid, "deleted": 0})
    store.insert("form_requests", {"id": "a44-form-link", "workspace_id": wid, "form_id": "fixture-form",
                                   "form_version": 1, "kind": "public", "submission_policy": "multiple",
                                   "status": "open", "created_by": owner["id"], "created_at": ts, "updated_at": ts})
    store.insert("organization_templates", {"id": "a44-org-template", "organization_id": oid, "source_tenant": wid,
                                            "template_id": "fixture-template", "name": "template fixture",
                                            "format": "docx", "version_no": 1, "working_blob": "fixture",
                                            "slots": "[]", "published_by": owner["id"], "published_at": ts,
                                            "updated_at": ts})
    return {**w, "oid": oid, "wid": wid, "owner": owner, "member": member, "member_client": client,
            "invitation": invitation["id"]}


def preview(s, action, *, client=None, wid=None, name=None):
    body = {"action": action} if name is None else {"action": action, "name": name}
    return post(client or s["admin"], f"{BASE}/{wid or s['wid']}/lifecycle/preview", body)


def plan(s, action, **kwargs):
    response = preview(s, action, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def command(p, *, reason=REASON, key=None):
    body = {"reason": reason, "preview_token": p["preview_token"], "idempotency_key": key or uuid.uuid4().hex}
    if p.get("name") is not None:
        body["name"] = p["name"]
    return body


def execute(s, action, body, *, client=None, wid=None):
    return post(client or s["admin"], f"{BASE}/{wid or s['wid']}/lifecycle/{action}", body)


def act(s, action, *, wid=None, name=None):
    """Preview then confirm, as the console does."""
    response = execute(s, action, command(plan(s, action, wid=wid, name=name)), wid=wid)
    assert response.status_code == 200, response.text
    return response.json()


def ws_row(s, wid=None):
    return s["store"].one("SELECT * FROM workspaces WHERE id=?", (wid or s["wid"],))


def platform_rows(s, result=None, *, wid=None):
    where = " AND result=?" if result else ""
    return s["store"].all(f"SELECT * FROM audit_events WHERE action=? AND object_id=?{where} ORDER BY seq",
                          (ACTION, wid or s["wid"], *((result,) if result else ())))


def denied_rows(s):
    return s["store"].all("SELECT * FROM audit_events WHERE action=? AND result='denied' ORDER BY seq", (ACTION,))


def org_rows(s, action):
    return s["store"].all("SELECT * FROM audit_events WHERE action=? AND object_id=? ORDER BY seq",
                          ("organization.workspace." + action, s["oid"]))


def operations(s):
    return s["store"].all("SELECT * FROM admin_operations WHERE action=? ORDER BY created_at", (ACTION,))


def events(s, wid=None):
    return [e for e in journal.read(s["store"].root) if e.get("workspace_id") == (wid or s["wid"])
            and e["kind"] == journal.WORKSPACE_DELETED_KIND]


def durable(s, wid=None):
    """What a confirmed act leaves behind: platform records, operations, journal entries, the row."""
    row = ws_row(s, wid)
    return (len(platform_rows(s, "ok", wid=wid)), len(operations(s)), len(events(s, wid)),
            {k: row[k] for k in ("name", "status", "deleted_at", "recover_until", "previous_status", "updated_at")})


def assert_nothing_gained(s):
    """The administrator governs from outside: no membership, entitlement or token."""
    store, uid = s["store"], admin_id(s)
    assert s["ident"].membership(s["wid"], uid) is None
    assert not store.one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (s["oid"], uid))
    assert not store.one("SELECT 1 FROM app_entitlements WHERE user_id=? AND workspace_id=?", (uid, s["wid"]))
    assert not store.one("SELECT 1 FROM oauth_tokens WHERE user_id=?", (uid,))


def refused(s, call, status, code, *, phase="execute", transition="delete", wid=None):
    """One refusal: the answer, the workspace untouched, nothing durable added, and the denied
    record the operation protocol writes once past the permission check."""
    before = durable(s, wid)
    response = call()
    assert response.status_code == status and response.json()["error"] == code, response.text
    assert durable(s, wid) == before
    assert_nothing_gained(s)
    denied = denied_rows(s)[-1]
    assert denied["actor_id"] == admin_id(s)
    meta = json.loads(denied["meta"])
    assert meta["code"] == code and meta["phase"] == phase
    assert meta.get("transition") == (transition if transition in workspace_lifecycle.ACTIONS else None)
    return meta


def set_state(s, state, wid=None):
    """FIXTURE state written directly (both lifecycle columns together, as the one writer does)."""
    wid = wid or s["wid"]
    deleted = state in ("deleted", "expired")
    s["store"].execute("UPDATE workspaces SET status=?, deleted_at=?, recover_until=?, previous_status=? WHERE id=?",
                       ("deleted" if deleted else state, now() if deleted else None,
                        (now() - 1 if state == "expired" else now() + 3600) if deleted else None,
                        "active" if deleted else None, wid))


# ---------------------------------------------------------------- the preview
def test_delete_preview_reports_the_real_impact_and_changes_nothing(space):
    s = space
    before = ws_row(s)
    started = time.time()
    p = plan(s, "delete")
    assert p["action"] == "delete" and p["risk"] == "high" and p["name"] is None
    assert p["transition"] == {"from": "active", "to": "deleted"}
    assert set(p["target"]) == ROW and p["target"]["id"] == s["wid"] and p["target"]["kind"] == "org"
    assert p["target"]["organization"] == {"id": s["oid"], "name": "Governed organization", "status": "active"}
    assert p["target"]["members"] == 2 and p["target"]["deleted_at"] is None
    assert p["impact"] == {"members": 2, "usable_owners": 1, "sessions": 1, "tokens": 1, "invitations": 1,
                           "approvals": 1, "api_keys": 1, "service_accounts": 1, "running_jobs": 1, "webhooks": 1,
                           "form_links": 1, "projects": 1, "org_templates": 1}
    assert p["notes"] == ["data_kept", "sessions_moved", "tokens_revoked", "invitations_revoked",
                          "approvals_revoked_for_good", "jobs_halt_for_good", "webhooks_disabled_for_good",
                          "credentials_paused", "forms_links_paused", "org_templates_kept", "recover_within_7_days"]
    assert started + 299 <= p["expires_at"] <= time.time() + 301
    assert ws_row(s) == before
    assert not platform_rows(s) and not operations(s) and not events(s)
    assert_nothing_gained(s)
    # Preview is POST-only: prefetch and link scanning cannot execute or confirm it.
    assert s["admin"].get(f"{BASE}/{s['wid']}/lifecycle/preview").status_code in (404, 405)


def test_notes_follow_the_data(space):
    """Only the codes true for this act and these rows: an empty workspace's deletion says what is
    kept and how long it can be recovered, nothing about withdrawals it would not make."""
    s = space
    empty = Organizations(s["store"], s["ident"]).create_workspace(s["oid"], s["owner"]["id"], "Empty workspace")["id"]
    p = plan(s, "delete", wid=empty)
    assert p["notes"] == ["data_kept", "recover_within_7_days"]
    assert p["impact"]["members"] == 1 and p["impact"]["sessions"] == 0 and p["impact"]["projects"] == 0


def test_rename_preview_normalises_the_name(space):
    s = space
    p = plan(s, "rename", name="  Renamed workspace  ")
    assert p["action"] == "rename" and p["name"] == "Renamed workspace" and p["risk"] == "medium"
    assert p["transition"] == {"from": "active", "to": "active"}
    assert p["notes"] == ["rename_visible_everywhere", "rename_not_journalled"]
    assert set(p["impact"]) == IMPACT and p["impact"]["members"] == 2
    assert ws_row(s)["name"] == "Governed workspace"


@pytest.mark.parametrize("state, action, name, status, code", [
    ("missing", "delete", None, 404, "no_workspace"),
    ("active", "explode", None, 400, "bad_action"),
    ("active", None, None, 400, "bad_action"),
    ("active", "rename", None, 400, "invalid_field"),
    ("active", "rename", "   ", 400, "invalid_field"),
    ("active", "rename", "x" * 101, 400, "invalid_field"),
    ("active", "rename", "bad\u0007name", 400, "invalid_field"),
    ("active", "rename", " Governed workspace ", 409, "workspace_name_unchanged"),
    ("active", "recover", None, 409, "workspace_state_conflict"),
    ("frozen", "recover", None, 409, "workspace_state_conflict"),
    ("deleted", "delete", None, 409, "workspace_state_conflict"),
    ("deleted", "rename", "Another name", 409, "workspace_state_conflict"),
    ("expired", "recover", None, 409, "workspace_recovery_expired"),
])
def test_preview_refusals_are_the_state_machines_and_are_recorded(space, state, action, name, status, code):
    s = space
    wid = "w_fixture_missing" if state == "missing" else s["wid"]
    if state not in ("missing", "active"):
        set_state(s, state)
    r = preview(s, action, wid=wid, name=name)
    assert r.status_code == status and r.json()["error"] == code, r.text
    (denied,) = denied_rows(s)
    meta = json.loads(denied["meta"])
    assert meta["code"] == code and meta["phase"] == "preview" and denied["actor_id"] == admin_id(s)
    assert meta.get("transition") == (action if action in workspace_lifecycle.ACTIONS else None)
    assert denied["object_id"] == (None if state == "missing" else wid) == denied["workspace_id"]
    assert set(meta) <= {"code", "phase", "environment", "transition"}, "a refused name is not recorded"
    assert not operations(s) and not events(s)


@pytest.mark.parametrize("action", ["delete", "recover"])
def test_a_personal_workspace_is_renamed_but_never_deleted_or_recovered(space, action):
    s = space
    personal = s["ident"].workspaces_for(s["owner"]["id"])[0]
    assert personal["kind"] == "personal"
    r = preview(s, action, wid=personal["id"])
    assert r.status_code == 409 and r.json()["error"] == "workspace_personal", r.text
    refused(s, lambda: execute(s, action, {"reason": REASON}, wid=personal["id"]), 409, "workspace_personal",
            transition=action, wid=personal["id"])
    # FIXTURE: even a personal row written as deleted is not recovered here (a second personal
    # workspace would follow).
    set_state(s, "deleted", personal["id"])
    assert preview(s, "recover", wid=personal["id"]).json()["error"] == "workspace_personal"
    set_state(s, "active", personal["id"])
    renamed = act(s, "rename", wid=personal["id"], name="Owner's own space")
    assert renamed["workspace"]["name"] == "Owner's own space" and renamed["workspace"]["kind"] == "personal"
    assert renamed["workspace"]["organization"] is None and renamed["restore_note"] == "rename_not_journalled"


@pytest.mark.parametrize("legacy_status", ["pending_claim", "active"])
@pytest.mark.parametrize("action", ["rename", "delete", "recover"])
def test_the_historical_workspace_refuses_every_act(space, legacy_status, action):
    """Pending its claim or claimed, the legacy workspace's lifecycle is claim/unclaim."""
    s = space
    # FIXTURE: the historical workspace, as Store seeds it (pending) or after a claim (active).
    s["store"].execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                       "VALUES ('default','历史数据（待认领）','legacy',?,?,?)", (legacy_status, now(), now()))
    r = preview(s, action, wid="default", name="Renamed history" if action == "rename" else None)
    assert r.status_code == 409 and r.json()["error"] == "workspace_legacy", r.text
    body = {"reason": REASON, **({"name": "Renamed history"} if action == "rename" else {})}
    refused(s, lambda: execute(s, action, body, wid="default"), 409, "workspace_legacy", transition=action, wid="default")


# ---------------------------------------------------------------- the acts
def test_a_non_member_administrator_deletes_a_workspace_from_outside(space):
    s = space
    store = s["store"]
    p = plan(s, "delete")
    started = time.time()
    r = execute(s, "delete", command(p))
    assert r.status_code == 200, r.text
    result = r.json()
    assert set(result) == {"ok", "operation_id", "state", "action", "workspace", "withdrawn", "restore_note"}
    assert result["ok"] is True and result["state"] == "succeeded" and result["action"] == "delete"
    assert result["restore_note"] is None
    assert result["withdrawn"] == {"sessions": 1, "tokens": 1, "codes": 1, "invitations": 1, "approvals": 1}
    view = result["workspace"]
    assert set(view) == ROW and view["status"] == "deleted" and view["previous_status"] == "active"
    assert started <= view["deleted_at"] <= time.time()
    assert view["recover_until"] - view["deleted_at"] == workspace_lifecycle.RECOVERY_SECONDS
    row = ws_row(s)
    assert (row["status"], row["deleted_at"], row["recover_until"], row["previous_status"]) == \
        ("deleted", view["deleted_at"], view["recover_until"], "active")
    # the withdrawals: moved, revoked, spent — and what stays stored, gated live
    assert store.one("SELECT workspace_id, revoked_at FROM sessions WHERE user_id=?", (s["member"]["id"],)) == \
        {"workspace_id": None, "revoked_at": None}
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (s["invitation"],))["revoked_at"]
    assert store.one("SELECT revoked_reason FROM oauth_tokens WHERE id='a44-token'")["revoked_reason"] == "workspace_deleted"
    assert store.one("SELECT used_at FROM oauth_codes WHERE code_hash='a44-code'")["used_at"]
    assert store.one("SELECT status FROM api_platform_approvals WHERE id='a44-approval'")["status"] == "revoked"
    assert store.one("SELECT revoked_at FROM api_keys WHERE id='a44-key'")["revoked_at"] is None
    assert store.one("SELECT disabled_at FROM oauth_clients WHERE client_id='a44-service'")["disabled_at"] is None
    assert store.one("SELECT status FROM jobs WHERE id='a44-job-running'")["status"] == "running", "halted at its next check"
    # ... which refuses the member's job (counted) and lets the operator's run (not counted)
    for jid, verdict in (("a44-job-running", (False, "workspace_deleted")), ("a44-job-operator", (True, None))):
        principal = store.one("SELECT principal FROM jobs WHERE id=?", (jid,))["principal"]
        assert authority.check(store, authority.loads(principal), need="write", tenant=s["wid"]) == verdict
    assert len(store.all("SELECT 1 FROM memberships WHERE workspace_id=?", (s["wid"],))) == 2, "data kept"
    # hidden: the switcher, the organization's list, the platform's default list
    assert s["wid"] not in [w["id"] for w in s["ident"].workspaces_for(s["member"]["id"])]
    assert s["ident"].get_workspace(s["wid"]) is None
    assert not Apps(store, s["ident"]).check_access(s["member"]["id"], DOCS_APP_ID, s["wid"])[0]
    assert s["wid"] not in [w["id"] for w in Organizations(store, s["ident"]).workspaces(s["oid"], s["owner"]["id"])]
    assert s["wid"] not in [w["id"] for w in s["admin"].get(BASE).json()["items"]]
    # the journal entry, written ahead with its receipt
    (event,) = events(s)
    assert set(event) == {"at", "kind", "event_id", "workspace_id", "deleted_at", "recover_until", "previous_status"}
    assert (event["deleted_at"], event["recover_until"], event["previous_status"]) == \
        (view["deleted_at"], view["recover_until"], "active")
    assert store.one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + event["event_id"],))
    # the records: the platform's, then the organization's own (by: platform), then the operation
    (platform,) = platform_rows(s, "ok")
    meta = json.loads(platform["meta"])
    assert platform["actor_id"] == admin_id(s) and platform["object_kind"] == "workspace"
    assert platform["workspace_id"] == s["wid"]
    assert meta["before"] == {"status": "active"} and meta["after"] == {"status": "deleted"} and meta["changed"] == ["status"]
    assert meta["transition"] == "delete" and meta["reason"] == REASON and meta["impact"] == p["impact"]
    assert meta["operation_id"] == result["operation_id"] and meta["organization_id"] == s["oid"]
    assert meta["environment"] == "http://testserver"
    (own,) = org_rows(s, "delete")
    own_meta = json.loads(own["meta"])
    assert own["actor_id"] == admin_id(s) and own["object_kind"] == "organization" and own["workspace_id"] == s["wid"]
    assert own_meta["by"] == "platform" and own_meta["workspace_id"] == s["wid"] and own_meta["reason"] == REASON
    assert own_meta["before"] == {"status": "active"} and own_meta["after"] == {"status": "deleted"}
    assert platform["seq"] < own["seq"]
    (operation,) = operations(s)
    assert operation["id"] == result["operation_id"] and operation["target_id"] == s["wid"]
    assert json.loads(operation["result_json"]) == result
    assert_nothing_gained(s)
    # the organization's own log shows the act, not the platform's records
    logged = s["ordinary"].get(f"/api/auth/organizations/{s['oid']}/audit").json()["items"]
    assert not [i for i in logged if i["action"].startswith("admin.")]
    (entry,) = [i for i in logged if i["action"] == "organization.workspace.delete"]
    assert entry["detail"]["by"] == "platform" and entry["actor"] == {"kind": "platform_admin"}
    assert entry["workspace"] == {"id": s["wid"], "name": "Governed workspace"}


def test_the_platform_list_finds_deleted_workspaces_with_their_deadline(space):
    s = space
    deleted = act(s, "delete")["workspace"]
    page = s["admin"].get(f"{BASE}?status=deleted")
    assert page.status_code == 200, page.text
    (item,) = page.json()["items"]
    assert {k: item[k] for k in ROW} == deleted and item["projects"] == 1
    assert page.json()["total"] == 1
    frozen = Organizations(s["store"], s["ident"]).create_workspace(s["oid"], s["owner"]["id"], "To be frozen")["id"]
    set_state(s, "frozen", frozen)
    assert [w["id"] for w in s["admin"].get(f"{BASE}?status=frozen").json()["items"]] == [frozen]
    assert s["wid"] not in [w["id"] for w in s["admin"].get(f"{BASE}?status=active").json()["items"]]
    every = s["admin"].get(BASE).json()["items"]
    assert frozen in [w["id"] for w in every] and all(set(w) >= ROW | {"projects", "apps"} for w in every)
    bad = s["admin"].get(f"{BASE}?status=archived")
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_field", bad.text


def test_recover_returns_the_previous_status_without_a_journal_entry(space):
    s = space
    deleted = act(s, "delete")["workspace"]
    p = plan(s, "recover")
    assert p["transition"] == {"from": "deleted", "to": "active"} and p["risk"] == "medium"
    # what the deletion ended for good (the approval), what comes back (the unbound key and service
    # account, the Forms link), what was never gone (the organization template) — no deadline note
    assert p["notes"] == ["data_kept", "approvals_revoked_for_good", "credentials_paused", "forms_links_paused",
                          "org_templates_kept", "recover_not_journalled"]
    assert (p["impact"]["approvals"], p["impact"]["api_keys"], p["impact"]["service_accounts"]) == (0, 1, 1)
    assert p["target"]["recover_until"] == deleted["recover_until"]
    r = execute(s, "recover", command(p))
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["withdrawn"] is None and result["restore_note"] == "recover_not_journalled"
    assert result["workspace"] == {**deleted, "status": "active", "deleted_at": None, "recover_until": None,
                                   "previous_status": None}
    assert len(events(s)) == 1, "a recovery writes no journal entry"
    assert json.loads(org_rows(s, "recover")[0]["meta"])["by"] == "platform"
    # what was gated live comes back; what was withdrawn does not
    assert s["wid"] in [w["id"] for w in s["ident"].workspaces_for(s["member"]["id"])]
    assert Apps(s["store"], s["ident"]).check_access(s["member"]["id"], DOCS_APP_ID, s["wid"])[0]
    assert s["store"].one("SELECT status FROM api_platform_approvals WHERE id='a44-approval'")["status"] == "revoked"
    assert s["store"].one("SELECT revoked_at FROM oauth_tokens WHERE id='a44-token'")["revoked_at"]
    # a frozen workspace deleted and recovered comes back frozen
    set_state(s, "frozen")
    assert act(s, "delete")["workspace"]["previous_status"] == "frozen"
    assert act(s, "recover")["workspace"]["status"] == "frozen"
    assert_nothing_gained(s)


def test_recover_previews_warn_about_the_organization_and_the_owners(space):
    s = space
    act(s, "delete")
    # FIXTURE: the organization frozen and the only owner suspended while the workspace was deleted.
    s["store"].execute("UPDATE organizations SET status='frozen' WHERE id=?", (s["oid"],))
    s["store"].execute("UPDATE users SET status='suspended' WHERE id=?", (s["owner"]["id"],))
    p = plan(s, "recover")
    assert p["notes"] == ["data_kept", "approvals_revoked_for_good", "credentials_paused", "forms_links_paused",
                          "org_templates_kept", "recover_not_journalled", "recover_blocked_by_organization",
                          "recover_without_usable_owner"]
    assert p["impact"]["usable_owners"] == 0 and p["target"]["organization"]["status"] == "frozen"


def bound_credentials(s):
    """FIXTURE rows as the API console's issue leaves them (approved, consumed, then bound): an API
    key whose lineage holds a rotated successor, and a service account with a secret grace period."""
    store, wid, ts = s["store"], s["wid"], now()
    for aid, kind in (("a44-key-approval", "api_key"), ("a44-service-approval", "service_account")):
        store.insert("api_platform_approvals", {"id": aid, "workspace_id": wid, "requested_by": s["owner"]["id"],
                                                "name": "bound fixture", "credential_kind": kind,
                                                "requested_scopes": "[]", "requested_resources": "[]",
                                                "status": "approved", "reason": REASON, "created_at": ts,
                                                "expires_at": ts + 900, "consumed_at": ts})
    for kid in ("a44-bound-key", "a44-bound-key-next"):
        store.insert("api_keys", {"id": kid, "name": "bound key fixture", "tenant": wid, "scopes": "read",
                                  "secret_hash": hashlib.sha256(kid.encode()).hexdigest(), "created_at": ts,
                                  "lineage": "a44-bound-key"})
    store.insert("oauth_clients", {"client_id": "a44-bound-service", "app_id": DOCS_APP_ID, "name": "bound service fixture",
                                   "redirect_uris": "[]", "kind": "service", "workspace_id": wid,
                                   "created_at": ts, "updated_at": ts})
    store.execute("INSERT INTO meta (key,value) VALUES ('client_secret_grace:a44-bound-service', '{}')")
    for kind, credential, aid in (("api_key", "a44-bound-key", "a44-key-approval"),
                                  ("service_account", "a44-bound-service", "a44-service-approval")):
        store.insert("api_platform_bindings", {"kind": kind, "credential_id": credential, "approval_id": aid,
                                               "created_at": ts})


def test_credentials_bound_to_an_approval_end_with_it_and_are_never_counted_as_paused(space):
    """A44-ACC-1 / A44-1 / A44-WEB-1: a credential bound to an approval can never work again once
    the deletion revoked the approval, so it is not counted as paused (`api_keys`,
    `service_accounts`, `credentials_paused` cover unbound ones only, keys by lineage) and is ended
    with its approval as `api_platform.revoke_approval` ends it — not left stored as if live, for
    the next preview to promise again."""
    s = space
    store = s["store"]
    bound_credentials(s)
    # FIXTURE: the unbound key rotated — its successor shares the lineage and is the same credential
    store.insert("api_keys", {"id": "a44-key-next", "name": "key fixture", "tenant": s["wid"], "scopes": "read",
                              "secret_hash": hashlib.sha256(b"a44-key-next").hexdigest(), "created_at": now(),
                              "lineage": "a44-key"})
    p = plan(s, "delete")
    assert (p["impact"]["approvals"], p["impact"]["api_keys"], p["impact"]["service_accounts"]) == (3, 1, 1)
    assert {"approvals_revoked_for_good", "credentials_paused"} <= set(p["notes"])
    result = execute(s, "delete", command(p))
    assert result.status_code == 200, result.text
    assert result.json()["withdrawn"]["approvals"] == 3
    keys = {r["id"]: (bool(r["revoked_at"]), r["revoke_reason"])
            for r in store.all("SELECT * FROM api_keys WHERE tenant=?", (s["wid"],))}
    assert keys == {"a44-key": (False, None), "a44-key-next": (False, None),
                    "a44-bound-key": (True, "approval_revoked"), "a44-bound-key-next": (True, "approval_revoked")}
    clients = {r["client_id"]: bool(r["disabled_at"]) for r in store.all(
        "SELECT client_id, disabled_at FROM oauth_clients WHERE kind='service' AND workspace_id=?", (s["wid"],))}
    assert clients == {"a44-service": False, "a44-bound-service": True}
    assert not store.one("SELECT 1 FROM meta WHERE key='client_secret_grace:a44-bound-service'")
    # the recovery says the approvals stay revoked and only the unbound credentials come back
    p = plan(s, "recover")
    assert (p["impact"]["approvals"], p["impact"]["api_keys"], p["impact"]["service_accounts"]) == (0, 1, 1)
    assert {"approvals_revoked_for_good", "credentials_paused"} <= set(p["notes"])
    act(s, "recover")
    assert store.one("SELECT COUNT(*) AS n FROM api_keys WHERE tenant=? AND revoked_at IS NULL", (s["wid"],))["n"] == 2
    assert store.one("SELECT disabled_at FROM oauth_clients WHERE client_id='a44-bound-service'")["disabled_at"]
    # a second deletion promises nothing about them either
    again = plan(s, "delete")
    assert (again["impact"]["approvals"], again["impact"]["api_keys"], again["impact"]["service_accounts"]) == (0, 1, 1)
    assert "approvals_revoked_for_good" not in again["notes"]


def test_recover_notes_name_what_the_deletion_halted_and_disabled(space):
    """A44-ACC-4: a recovery's notes say what stays ended, judged by the rows now — the approvals
    the deletion revoked, the Docs jobs halted and the webhook subscriptions disabled since
    `deleted_at` (FIXTURE: written as Docs' JobRunner._halt and WebhookDispatcher
    .disable_subscription write them) — and nothing that ended before the deletion."""
    s = space
    store = s["store"]
    quiet = Organizations(store, s["ident"]).create_workspace(s["oid"], s["owner"]["id"], "Quiet workspace")["id"]
    before = now() - 60
    # FIXTURE: an approval revoked, a job halted and a subscription disabled before the deletion
    store.insert("api_platform_approvals", {"id": "a44-revoked-before", "workspace_id": quiet,
                                            "requested_by": s["owner"]["id"], "name": "approval fixture",
                                            "credential_kind": "api_key", "requested_scopes": "[]",
                                            "requested_resources": "[]", "status": "revoked", "reason": REASON,
                                            "created_at": before, "expires_at": before + 900, "revoked_at": before})
    store.insert("jobs", {"id": "a44-halted-before", "status": "cancelled", "created_at": before, "tenant": quiet,
                          "halt_reason": "authorization_revoked:credential_revoked", "finished_at": before})
    store.insert("webhook_subscriptions", {"id": "a44-disabled-before", "tenant": quiet, "status": "disabled",
                                           "url": "https://receiver.example.test/quiet", "events": '["*"]',
                                           "disabled_reason": "authorization_revoked:credential_revoked",
                                           "secret_enc": "fixture-sealed", "created_at": before, "updated_at": before})
    act(s, "delete", wid=quiet)
    assert plan(s, "recover", wid=quiet)["notes"] == ["data_kept", "recover_not_journalled"]
    # the populated workspace: the member's job and the subscription are still waiting for their check
    deleted = act(s, "delete")["workspace"]
    expected = ["data_kept", "approvals_revoked_for_good", "credentials_paused", "forms_links_paused",
                "org_templates_kept", "recover_not_journalled"]
    assert plan(s, "recover")["notes"] == expected
    later = deleted["deleted_at"] + 1
    store.execute("UPDATE jobs SET status='cancelled', halt_reason='authorization_revoked:workspace_deleted', "
                  "finished_at=? WHERE id='a44-job-running'", (later,))
    store.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason='workspace_deleted', "
                  "updated_at=? WHERE id='a44-webhook'", (later,))
    p = plan(s, "recover")
    assert p["notes"] == ["data_kept", "approvals_revoked_for_good", "jobs_halt_for_good", "webhooks_disabled_for_good",
                          "credentials_paused", "forms_links_paused", "org_templates_kept", "recover_not_journalled"]
    assert p["impact"]["running_jobs"] == 0 and p["impact"]["webhooks"] == 0


def test_recover_notes_count_the_dependents_of_grants_the_deletion_ended(space):
    """A44-WEB-2: work still waiting for its check whose descriptor the deletion ended — a Docs job
    and a Docs subscription created through a delegated grant (its tokens were revoked), a Docs job
    of an approval-bound service account (disabled with its approval), and a business application's
    subscription of that grant — stops for good after the recovery too; the recovery says so before
    any of them is halted or disabled. A member's own job and an operator's subscription do not."""
    s = space
    store, wid, ts = s["store"], s["wid"], now()
    bound_credentials(s)
    # FIXTURE: the delegated client behind the fixture token, and the rows its grant created
    store.insert("oauth_clients", {"client_id": "fixture-client", "app_id": DOCS_APP_ID, "name": "delegated fixture",
                                   "redirect_uris": "[]", "created_at": ts, "updated_at": ts})
    delegated = authority.dumps({"kind": "delegated", "client_id": "fixture-client", "user_id": s["member"]["id"],
                                 "family_id": "a44-token", "tenant": wid})
    service = authority.dumps(authority.describe({"kind": "service_account", "client_id": "a44-bound-service",
                                                  "tenant": wid}))
    plan_before = plan(s, "delete")
    for jid, principal in (("a44-job-delegated", delegated), ("a44-job-service", service)):
        store.insert("jobs", {"id": jid, "status": "queued", "created_at": ts, "tenant": wid, "principal": principal})
    assert plan(s, "delete")["impact"]["running_jobs"] == plan_before["impact"]["running_jobs"] + 2
    act(s, "delete")
    base = ["data_kept", "approvals_revoked_for_good", "credentials_paused", "forms_links_paused",
            "org_templates_kept", "recover_not_journalled"]
    p = plan(s, "recover")
    assert p["notes"] == base[:2] + ["jobs_halt_for_good"] + base[2:]
    store.insert("webhook_subscriptions", {"id": "a44-webhook-delegated", "tenant": wid, "created_by": delegated,
                                           "url": "https://receiver.example.test/delegated", "events": '["*"]',
                                           "secret_enc": "fixture-sealed", "created_at": ts, "updated_at": ts})
    assert plan(s, "recover")["notes"] == base[:2] + ["jobs_halt_for_good", "webhooks_disabled_for_good"] + base[2:]
    store.execute("DELETE FROM webhook_subscriptions WHERE id='a44-webhook-delegated'")
    store.execute("DELETE FROM jobs WHERE id IN ('a44-job-delegated', 'a44-job-service')")
    assert plan(s, "recover")["notes"] == base
    store.insert("suite_webhook_subscriptions", {
        "id": "a44-suite-webhook", "workspace_id": wid, "client_id": "fixture-client", "user_id": s["member"]["id"],
        "created_by": delegated, "authorized_at": ts, "url": "https://receiver.example.test/suite",
        "events": '["*"]', "status_changed_at": ts, "secret_enc": "fixture-sealed", "cursor": 0, "cursor_at": ts,
        "created_at": ts, "updated_at": ts})
    assert plan(s, "recover")["notes"] == base[:2] + ["webhooks_disabled_for_good"] + base[2:]


def test_a_sole_owner_cannot_strand_a_deleted_workspace_that_can_still_be_recovered(space, monkeypatch):
    """A44-3: inside the recovery window a deleted workspace's memberships come back with it, so its
    sole owner can neither leave the organization, be removed from it, nor delete the account
    (ownership.check_ownership_continuity) — the recovered workspace would have nobody to manage
    it. Each says so in its own words (A44-L1, below). Once the window has closed it no longer
    holds them back."""
    from synwevo_core.errors import ServiceError
    from synwevo_identity import ownership

    s = space
    store, orgs = s["store"], Organizations(s["store"], s["ident"])
    second = s["ident"].create_user("second-org-owner@example.test", PW, status="active")
    org_member(s, s["oid"], second["id"], "owner")
    deleted = act(s, "delete")["workspace"]
    held = {"workspace_id": s["wid"], "name": "Governed workspace", "role": "owner",
            "recover_until": deleted["recover_until"]}
    with pytest.raises(ServiceError) as refused_leave:
        orgs.remove_member(s["oid"], s["owner"]["id"], s["owner"]["id"], reason=REASON, leave=True)
    assert (refused_leave.value.status, refused_leave.value.code) == (409, "owner_of_deleted_workspace")
    assert refused_leave.value.detail == held
    with pytest.raises(ServiceError) as refused_removal:
        orgs.remove_member(s["oid"], second["id"], s["owner"]["id"], reason=REASON)
    assert refused_removal.value.code == "owner_of_deleted_workspace" and refused_removal.value.detail == held
    with pytest.raises(ServiceError) as refused_account:
        ownership.check_ownership_continuity(store, s["owner"]["id"])
    assert refused_account.value.code == "owner_of_deleted_workspace" and refused_account.value.detail == held
    assert s["ident"].membership(s["wid"], s["owner"]["id"])["role"] == "owner"
    # the window closed: nothing left to come back
    closed = deleted["recover_until"] + 1
    import synwevo_core.organizations as organizations_module
    original_core, original_ownership = organizations_module.now, ownership.now
    monkeypatch.setattr(organizations_module, "now", lambda: closed)
    monkeypatch.setattr(ownership, "now", lambda: closed)
    try:
        ownership.check_ownership_continuity(store, s["owner"]["id"])
        assert orgs.remove_member(s["oid"], s["owner"]["id"], s["owner"]["id"], reason=REASON, leave=True)["removed"]
    finally:
        monkeypatch.setattr(organizations_module, "now", original_core)
        monkeypatch.setattr(ownership, "now", original_ownership)
    assert s["ident"].membership(s["wid"], s["owner"]["id"]) is None


def test_a_departure_held_back_by_a_deleted_workspace_names_it_its_window_and_the_way_out(space):
    """A44-L1: the generic refusals told the person to hand over a workspace they cannot see or hand
    over (it is hidden until recovered, and the platform's ownership recovery does not reach a
    deleted workspace). Now the refusal is its own: `owner_of_deleted_workspace`, naming the
    workspace and the end of its recovery window — in the sentence and in `detail`, so every shell
    words it — and the two ways out: a platform recovery followed by a handover, or the end of the
    window. The same answer reaches the account (self-service deletion), the organization (leave,
    removal) and the console (suspension preview, the account's deletion blockers). A live
    workspace the person alone owns is named first: its handover is possible now."""
    from datetime import datetime, timezone

    from synwevo_core.errors import ServiceError
    from synwevo_identity import ownership

    s = space
    store, orgs, uid = s["store"], Organizations(s["store"], s["ident"]), s["owner"]["id"]
    second = s["ident"].create_user("second-org-owner@example.test", PW, status="active")
    org_member(s, s["oid"], second["id"], "owner")
    until = act(s, "delete")["workspace"]["recover_until"]
    held = {"workspace_id": s["wid"], "name": "Governed workspace", "role": "owner", "recover_until": until}
    with pytest.raises(ServiceError) as refused:
        ownership.check_ownership_continuity(store, uid)
    when = datetime.fromtimestamp(until, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    assert refused.value.message == (
        f"此账户是已删除、仍在恢复期内的工作空间「Governed workspace」唯一可用的拥有者；恢复期限（{when}）结束前不能执行此操作。"
        "可请平台管理员恢复该工作空间后移交所有权，或等待恢复期限结束。")
    # the console: the suspension preview, and the account's deletion blockers
    r = post(s["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"})
    assert r.status_code == 409 and r.json()["error"] == "owner_of_deleted_workspace", r.text
    assert r.json()["detail"] == held and r.json()["message"] == refused.value.message
    assert s["admin"].get(f"/api/admin/users/{uid}").json()["deletion"]["blockers"] == ["owner_of_deleted_workspace"]
    # a live workspace this account alone owns comes first, with the refusal a handover answers
    live = orgs.create_workspace(s["oid"], uid, "Live sole workspace")["id"]
    s["ident"].add_member(live, s["member"]["id"], "editor", actor_id=uid)
    with pytest.raises(ServiceError) as first:
        orgs.remove_member(s["oid"], second["id"], uid, reason=REASON)
    assert (first.value.code, first.value.detail) == ("last_workspace_owner", {"workspace_id": live})
    with pytest.raises(ServiceError) as first:
        ownership.check_ownership_continuity(store, uid)
    assert first.value.code == "ownership_required"
    s["ident"].add_member(live, second["id"], "owner", actor_id=uid)
    with pytest.raises(ServiceError) as refused:
        orgs.remove_member(s["oid"], second["id"], uid, reason=REASON)
    assert (refused.value.code, refused.value.detail) == ("owner_of_deleted_workspace", held)
    # a departing admin of a deleted workspace nobody else can manage: the same code, its own words
    store.execute("UPDATE memberships SET role='admin' WHERE workspace_id=? AND user_id=?", (s["wid"], uid))
    with pytest.raises(ServiceError) as refused:
        ownership.check_ownership_continuity(store, uid)
    assert (refused.value.code, refused.value.detail) == ("owner_of_deleted_workspace", {**held, "role": "admin"})
    assert "唯一可用的管理者" in refused.value.message and "另设拥有者或管理者" in refused.value.message


def test_a_bulk_suspension_reports_a_deleted_workspace_owner_with_its_own_server_sentence(space):
    """A44-L1 in the bulk console (admin_bulk): the preview item carries `owner_of_deleted_workspace`
    by code alone, and the batch reports it again from that code with the server's own sentence —
    the code-only form of organizations.deleted_workspace_owner (no workspace name, no deadline),
    never the generic "该账户不符合这项操作的条件". The single route still names both."""
    from synwevo_identity.admin_bulk import REFUSAL_MESSAGES, REFUSED_MESSAGE

    s = space
    uid = s["owner"]["id"]
    second = s["ident"].create_user("second-org-owner@example.test", PW, status="active")
    org_member(s, s["oid"], second["id"], "owner")
    act(s, "delete")
    sentence = REFUSAL_MESSAGES["owner_of_deleted_workspace"]
    assert sentence != REFUSED_MESSAGE and "Governed workspace" not in sentence
    assert sentence.startswith("此账户是一个已删除、仍在恢复期内的工作空间唯一可用的拥有者或管理者")
    r = post(s["admin"], "/api/admin/users/bulk-preview", {"operation": "suspend", "user_ids": [uid, s["member"]["id"]]})
    assert r.status_code == 200, r.text
    bulk = r.json()
    assert [(item["id"], item["code"]) for item in bulk["items"]] == [(uid, "owner_of_deleted_workspace"), (s["member"]["id"], None)]
    r = post(s["admin"], "/api/admin/users/bulk", {"operation": "suspend", "reason": REASON, "preview_token": bulk["preview_token"],
                                                   "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    results = {item["id"]: item for item in r.json()["results"]}
    assert results[uid] == {"id": uid, "ok": False, "code": "owner_of_deleted_workspace", "message": sentence}
    assert results[s["member"]["id"]]["ok"] is True
    assert s["ident"].get_user(uid)["status"] == "active"
    single = post(s["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"}).json()
    assert single["error"] == "owner_of_deleted_workspace" and "「Governed workspace」" in single["message"]


def test_the_recovery_window_is_judged_by_the_clock_at_the_moment_of_the_act(space, monkeypatch):
    s = space
    deleted = act(s, "delete")["workspace"]
    p = plan(s, "recover")
    closed = deleted["recover_until"] + 1
    original_core, original_route = workspace_lifecycle.now, admin_workspaces.now
    monkeypatch.setattr(workspace_lifecycle, "now", lambda: closed)
    monkeypatch.setattr(admin_workspaces, "now", lambda: closed)
    try:
        refused(s, lambda: execute(s, "recover", command(p)), 409, "workspace_recovery_expired", transition="recover")
        r = preview(s, "recover")
        assert r.status_code == 409 and r.json()["error"] == "workspace_recovery_expired"
    finally:
        monkeypatch.setattr(workspace_lifecycle, "now", original_core)
        monkeypatch.setattr(admin_workspaces, "now", original_route)
    # one second before the deadline it is still open
    just_open = deleted["recover_until"] - 1
    monkeypatch.setattr(workspace_lifecycle, "now", lambda: just_open)
    monkeypatch.setattr(admin_workspaces, "now", lambda: just_open)
    try:
        assert act(s, "recover")["workspace"]["status"] == "active"
    finally:
        monkeypatch.setattr(workspace_lifecycle, "now", original_core)
        monkeypatch.setattr(admin_workspaces, "now", original_route)
    assert len(operations(s)) == 2 and len(events(s)) == 1


def test_rename_is_visible_in_the_switcher_and_in_invitation_mail(space):
    s = space
    result = act(s, "rename", name="  Renamed by the platform ")
    assert result["action"] == "rename" and result["withdrawn"] is None
    assert result["restore_note"] == "rename_not_journalled"
    assert result["workspace"]["name"] == "Renamed by the platform" and result["workspace"]["status"] == "active"
    assert ws_row(s)["name"] == "Renamed by the platform" and not events(s)
    # the member's switcher
    me = s["member_client"].get("/api/auth/me").json()
    assert {w["id"]: w["name"] for w in me["workspaces"]}[s["wid"]] == "Renamed by the platform"
    # the next invitation mail names it
    r = post(s["ordinary"], f"/api/auth/workspaces/{s['wid']}/invitations", {"email": "next@example.test", "role": "viewer"})
    assert r.status_code == 200, r.text
    mail = s["store"].one("SELECT subject, body FROM mail_outbox WHERE ref_id=?", (r.json()["id"],))
    assert "Renamed by the platform" in mail["subject"] and "Renamed by the platform" in mail["body"]
    # the records
    (own,) = org_rows(s, "rename")
    own_meta = json.loads(own["meta"])
    assert own_meta["before"] == {"name": "Governed workspace"} and own_meta["after"] == {"name": "Renamed by the platform"}
    assert own_meta["by"] == "platform"
    (platform,) = platform_rows(s, "ok")
    assert json.loads(platform["meta"])["changed"] == ["name"]
    # a frozen workspace can be renamed too, and stays frozen
    set_state(s, "frozen")
    assert act(s, "rename", name="Frozen and renamed")["workspace"]["status"] == "frozen"


def test_an_unattached_organization_workspace_writes_no_organization_record(space):
    """A historical `org` workspace with no organization: deleted like any other, with the platform's
    record only."""
    s = space
    loose = s["ident"].create_workspace("Loose workspace", owner_id=s["owner"]["id"], kind="org")["id"]
    result = act(s, "delete", wid=loose)
    assert result["workspace"]["organization_id"] is None and result["workspace"]["organization"] is None
    assert not s["store"].one("SELECT 1 FROM audit_events WHERE action LIKE 'organization.workspace.%' "
                              "AND workspace_id=?", (loose,))
    assert len(platform_rows(s, "ok", wid=loose)) == 1 and len(events(s, loose)) == 1


# ---------------------------------------------------------------- the refusals
def test_reauthentication_is_checked_before_the_body_is_read(space):
    s = space
    fresh = s["login"](s["ident"].by_email(ADMIN_EMAIL))
    for body in ({}, {"reason": REASON}, command(plan(s, "delete"))):
        r = execute(s, "delete", body, client=fresh)
        assert r.status_code == 403 and r.json()["error"] == "reauth_required", r.text
    assert ws_row(s)["status"] == "active" and not platform_rows(s) and not operations(s)
    # a preview needs no re-authentication: it changes nothing
    assert preview(s, "delete", client=fresh).status_code == 200


@pytest.mark.parametrize("role", ROLE_ORDER)
def test_the_role_matrix(space, role):
    s = space
    holder = make_platform_admin(s["store"], s["ident"].create_user(f"{role}-holder@example.test", PW, status="active"),
                                 role=role)
    client = s["login"](holder)
    s["reauth"](client)
    allowed = admin_workspaces.PERMISSION in ROLES[role]
    p = plan(s, "delete")
    for r in (preview(s, "delete", client=client), execute(s, "delete", command(p), client=client)):
        if allowed:
            assert r.status_code in (200, 409), r.text   # the act: the plan was signed for another administrator
        else:
            assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", r.text
            assert r.json()["detail"]["permission"] == "platform.workspaces.manage"
    assert ws_row(s)["status"] == "active" and not operations(s)
    assert [r for r in ROLE_ORDER if admin_workspaces.PERMISSION in ROLES[r]] == ["super_admin", "access_admin"]


def test_owners_and_workspace_administrators_without_a_platform_role_meet_404(space):
    s = space
    for client in (s["ordinary"], s["member_client"]):
        assert preview(s, "delete", client=client).status_code == 404
        assert execute(s, "delete", {"reason": REASON}, client=client).status_code == 404
    assert ws_row(s)["status"] == "active" and not platform_rows(s)


def test_the_reason_the_key_and_the_token_are_required(space):
    s = space
    p = plan(s, "delete")
    meta = refused(s, lambda: execute(s, "delete", {**command(p), "reason": "short"}), 400, "missing_reason")
    assert "reason" not in meta
    refused(s, lambda: execute(s, "delete", {**command(p), "reason": "x" * 501}), 400, "missing_reason")
    refused(s, lambda: execute(s, "delete", {"reason": REASON}), 400, "admin_confirmation_required")
    refused(s, lambda: execute(s, "delete", {**command(p), "idempotency_key": "short"}), 400, "admin_confirmation_required")
    meta = refused(s, lambda: execute(s, "delete", {**command(p), "preview_token": None}), 400, "admin_confirmation_required")
    assert meta["reason"] == REASON


@pytest.mark.parametrize("alteration", ["workspace", "transition", "name", "actor", "origin", "signature", "expired"])
def test_a_preview_binds_workspace_act_name_actor_origin_and_time(space, monkeypatch, alteration):
    s = space
    action, wid, headers = "rename", s["wid"], {}
    p = plan(s, "rename", name="Bound name")
    body = command(p)
    if alteration == "workspace":
        wid = Organizations(s["store"], s["ident"]).create_workspace(s["oid"], s["owner"]["id"], "Other workspace")["id"]
    elif alteration == "transition":
        action, body = "delete", {k: v for k, v in body.items() if k != "name"}
    elif alteration == "name":
        body["name"] = "Another name"
    elif alteration == "actor":
        second = make_platform_admin(s["store"], s["ident"].create_user("second-admin@example.test", PW, status="active"),
                                     role="access_admin")
        client = s["login"](second)
        s["reauth"](client)
        body = command(plan(s, "rename", client=client, name="Bound name"))
    elif alteration == "origin":
        headers = {"host": "localhost"}
    elif alteration == "signature":
        body["preview_token"] += "tampered"
    else:
        monkeypatch.setattr(admin_workspaces, "time", types.SimpleNamespace(time=lambda: p["expires_at"] + 1))
    expected = "admin_preview_expired" if alteration == "expired" else "admin_preview_invalid"
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER

    def call():
        return s["admin"].post(f"{BASE}/{wid}/lifecycle/{action}", json=body,
                               headers={CSRF_HEADER: s["admin"].cookies.get(CSRF_COOKIE) or "", **headers})

    refused(s, call, 409, expected, transition=action, wid=wid)
    assert ws_row(s)["name"] == "Governed workspace"


@pytest.mark.parametrize("change", ["member_joins", "member_swap", "renamed_meanwhile", "frozen_meanwhile", "org_frozen"])
def test_a_changed_workspace_makes_the_preview_stale(space, change):
    s = space
    p = plan(s, "delete")
    if change == "member_joins":
        s["ident"].add_member(s["wid"], s["ident"].create_user("latecomer@example.test", PW, status="active")["id"], "viewer")
    elif change == "member_swap":
        # FIXTURE: one member leaves while another joins — the count stays, the identities do not.
        s["store"].execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (s["wid"], s["member"]["id"]))
        s["ident"].add_member(s["wid"], s["ident"].create_user("newcomer@example.test", PW, status="active")["id"], "editor")
    elif change == "renamed_meanwhile":
        act(s, "rename", name="Renamed meanwhile")
    elif change == "frozen_meanwhile":
        r = post(s["admin"], f"{BASE}/{s['wid']}/freeze", {"reason": REASON})
        assert r.status_code == 200, r.text
    else:
        s["store"].execute("UPDATE organizations SET status='frozen' WHERE id=?", (s["oid"],))
    refused(s, lambda: execute(s, "delete", command(p)), 409, "admin_preview_stale")


def test_an_act_the_state_does_not_allow_is_refused_before_confirmation(space):
    s = space
    refused(s, lambda: execute(s, "recover", {"reason": REASON}), 409, "workspace_state_conflict", transition="recover")
    refused(s, lambda: execute(s, "recover", command(plan(s, "delete"))), 409, "workspace_state_conflict",
            transition="recover")
    refused(s, lambda: execute(s, "explode", {"reason": REASON}), 400, "bad_action", transition="explode")
    refused(s, lambda: execute(s, "rename", {"reason": REASON, "name": "Governed workspace"}), 409,
            "workspace_name_unchanged", transition="rename")
    meta = refused(s, lambda: execute(s, "rename", {"reason": REASON, "name": ""}), 400, "invalid_field",
                   transition="rename")
    assert set(meta) == {"code", "phase", "environment", "transition", "reason"}
    before = len(denied_rows(s))
    r = execute(s, "delete", {"reason": REASON}, wid="w_fixture_missing")
    assert r.status_code == 404 and r.json()["error"] == "no_workspace"
    assert len(denied_rows(s)) == before + 1 and denied_rows(s)[-1]["object_id"] is None


# ---------------------------------------------------------------- idempotency
def test_identical_retries_return_the_stored_result_and_write_nothing_twice(space, monkeypatch):
    s = space
    body = command(plan(s, "delete"))
    first = execute(s, "delete", body)
    assert first.status_code == 200, first.text
    monkeypatch.setattr(admin_workspaces, "time", types.SimpleNamespace(time=lambda: time.time() + 3600))
    retry = execute(s, "delete", {**body, "reason": "  " + REASON + "  "})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(platform_rows(s)) == len(operations(s)) == len(events(s)) == len(org_rows(s, "delete")) == 1
    conflict = execute(s, "delete", {**body, "reason": REASON + " changed"})
    assert conflict.status_code == 409 and conflict.json()["error"] == "idempotency_conflict", conflict.text
    other = execute(s, "recover", {**body, "preview_token": plan(s, "recover")["preview_token"]})
    assert other.status_code == 409 and other.json()["error"] == "idempotency_conflict", other.text
    assert ws_row(s)["status"] == "deleted" and len(operations(s)) == 1 and len(platform_rows(s, "ok")) == 1


# ---------------------------------------------------------------- failures roll back
def test_the_journal_failing_commits_nothing_and_is_recorded_as_an_error(space, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("fixture: security journal unavailable")

    s = space
    p = plan(s, "delete")
    before = durable(s)
    original = workspace_lifecycle.record_workspace
    monkeypatch.setattr(workspace_lifecycle, "record_workspace", unavailable)
    r = execute(s, "delete", command(p))
    # Restore only this function: undo() would also revert the fixture's environment.
    monkeypatch.setattr(workspace_lifecycle, "record_workspace", original)
    assert r.status_code == 503 and r.json()["error"] == "journal_unavailable", r.text
    assert "fixture: security journal unavailable" not in r.text
    assert r.json()["message"] == admin_workspaces.JOURNAL_UNAVAILABLE and "未提交" not in r.json()["message"]
    assert durable(s) == before and not org_rows(s, "delete")
    assert s["store"].one("SELECT workspace_id FROM sessions WHERE user_id=?", (s["member"]["id"],))["workspace_id"] == s["wid"]
    assert s["store"].one("SELECT revoked_at FROM invitations WHERE id=?", (s["invitation"],))["revoked_at"] is None
    error = platform_rows(s, "error")[-1]
    assert json.loads(error["meta"])["code"] == "journal_unavailable"


def test_a_journal_failure_after_the_line_was_written_is_not_called_void(space, monkeypatch):
    """A44-J2: the directory fsync fails after the entry reached the journal. The transaction rolls
    back — the workspace stays active, nothing durable is added in the database — but the entry has
    no receipt, so a later restore applies it (write-ahead, fail-closed; replayed in
    tests/integration/test_workspace_restore). The 503 says so instead of 操作未提交."""
    s = space
    p = plan(s, "delete")
    before = durable(s)[:2] + durable(s)[3:]
    original = journal.record

    def appended_then_failed(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("fixture: directory fsync failed after the append")
    monkeypatch.setattr(journal, "record", appended_then_failed)
    r = execute(s, "delete", command(p))
    monkeypatch.setattr(journal, "record", original)
    assert r.status_code == 503 and r.json()["error"] == "journal_unavailable", r.text
    message = r.json()["message"]
    assert message == admin_workspaces.JOURNAL_UNAVAILABLE and "备份" in message and "未提交" not in message
    after = durable(s)
    assert after[:2] + after[3:] == before and ws_row(s)["status"] == "active"
    (event,) = events(s)
    assert not s["store"].one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + event["event_id"],))


def test_the_platform_record_failing_commits_nothing(space, monkeypatch):
    from .test_admin import _audit_insert_fails

    s = space
    p = plan(s, "delete")
    before = durable(s)
    restore = _audit_insert_fails(monkeypatch, ACTION)
    r = execute(s, "delete", command(p))
    restore()
    assert r.status_code >= 500, r.text
    assert "fixture: audit storage unavailable" not in r.text
    assert durable(s) == before and not events(s) and not org_rows(s, "delete")


def test_a_write_ahead_entry_whose_transaction_failed_has_no_receipt(space, monkeypatch):
    """The entry is fsynced before the SQL; a failure after it rolls the deletion back and leaves an
    intent without a receipt — which a restore replays (tests/integration/test_workspace_restore)."""
    s = space
    p = plan(s, "delete")
    original = workspace_lifecycle.withdraw

    def failing(*args, **kwargs):
        raise RuntimeError("fixture: failure after the journal entry")
    monkeypatch.setattr(workspace_lifecycle, "withdraw", failing)
    r = execute(s, "delete", command(p))
    monkeypatch.setattr(workspace_lifecycle, "withdraw", original)
    assert r.status_code == 500
    (event,) = events(s)
    assert not s["store"].one("SELECT 1 FROM meta WHERE key=?", ("workspace_withdrawal:" + event["event_id"],))
    assert ws_row(s)["status"] == "active" and not operations(s)


# ---------------------------------------------------------------- routes and neighbours
def test_the_lifecycle_routes_do_not_collide_with_the_other_workspace_routes(space):
    s = space
    paths = list(s["api"].openapi()["paths"])
    preview_path, generic = f"{BASE}/{{wid}}/lifecycle/preview", f"{BASE}/{{wid}}/lifecycle/{{action}}"
    assert paths.index(preview_path) < paths.index(generic)
    for neighbour in ("freeze", "unfreeze", "claim", "unclaim", "recover-owner"):
        assert f"{BASE}/{{wid}}/{neighbour}" in paths
    # a preview is a preview, not the act "preview": a session without the strict re-authentication
    # reaches it, and a body without `action` is its own refusal
    fresh = s["login"](s["ident"].by_email(ADMIN_EMAIL))
    assert preview(s, "delete", client=fresh).json()["transition"]["to"] == "deleted"
    r = post(fresh, f"{BASE}/{s['wid']}/lifecycle/preview", {"reason": REASON})
    assert r.status_code == 400 and r.json()["error"] == "bad_action", r.text
    # the freeze route is the freeze route, and the lifecycle does not take its words
    r = execute(s, "freeze", {"reason": REASON})
    assert r.status_code == 400 and r.json()["error"] == "bad_action", r.text
    r = post(s["admin"], f"{BASE}/{s['wid']}/freeze", {"reason": REASON})
    assert r.status_code == 200 and r.json()["workspace"]["status"] == "frozen", r.text
    assert set(r.json()["workspace"]) >= ROW
    r = post(s["admin"], f"{BASE}/{s['wid']}/recover-owner", {"reason": REASON})
    assert r.json()["error"] != "bad_action", r.text
    assert ws_row(s)["status"] == "frozen" and not operations(s)


def test_a_deleted_workspace_cannot_be_frozen_unfrozen_or_reassigned(space):
    s = space
    act(s, "delete")
    for path in ("freeze", "unfreeze"):
        r = post(s["admin"], f"{BASE}/{s['wid']}/{path}", {"reason": REASON})
        assert r.status_code == 404 and r.json()["error"] == "no_workspace", r.text
    assert ws_row(s)["status"] == "deleted"


def test_an_organization_lifecycle_preview_goes_stale_after_a_workspace_deletion(space):
    s = space
    org_plan = post(s["admin"], f"/api/admin/organizations/{s['oid']}/preview", {"action": "freeze"}).json()
    act(s, "delete")
    r = post(s["admin"], f"/api/admin/organizations/{s['oid']}/freeze",
             {"reason": REASON, "preview_token": org_plan["preview_token"], "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and r.json()["error"] == "admin_preview_stale", r.text
    assert s["store"].one("SELECT status FROM organizations WHERE id=?", (s["oid"],))["status"] == "active"
    # and the organization's counts no longer include it
    fresh = post(s["admin"], f"/api/admin/organizations/{s['oid']}/preview", {"action": "freeze"}).json()
    assert fresh["impact"]["workspaces"] == org_plan["impact"]["workspaces"] - 1


def test_the_member_can_no_longer_reach_the_deleted_workspace(space):
    """The account service's own refusals; the applications' are in their suites."""
    s = space
    act(s, "delete")
    c = s["member_client"]
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    headers = {CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""}
    me = c.get("/api/auth/me").json()
    assert s["wid"] not in [w["id"] for w in me["workspaces"]] and me.get("workspace_id") != s["wid"]
    switch = c.post("/api/auth/workspace", json={"workspace_id": s["wid"]}, headers=headers)
    assert switch.status_code == 404 and switch.json()["error"] == "no_workspace", switch.text
    pinned = c.get("/api/auth/me", headers={"X-Synwevo-Workspace": s["wid"]})
    assert pinned.status_code == 403 and pinned.json()["error"] == "workspace_context_invalid", pinned.text
    invite = post(s["ordinary"], f"/api/auth/workspaces/{s['wid']}/invitations", {"email": "late@example.test"})
    assert invite.status_code in (403, 404, 409) and not s["store"].one(
        "SELECT 1 FROM invitations WHERE workspace_id=? AND email_key='late@example.test'", (s["wid"],))
