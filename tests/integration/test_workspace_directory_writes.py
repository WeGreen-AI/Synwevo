"""Real isolated owner approval → service token → invite acceptance → membership lifecycle."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re

import pytest
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.store import now
from tests.integration.test_api_platform import OWNER, PW, REASON, approve, plane, post, reauth, request_approval  # noqa: F401
from tests.integration.test_workspace_directory_api import D, directory_credential, token

WRITE = "workspace.members.write"


def write(e, tok, path, body=None, *, method="POST", key=None):
    headers = {"Authorization": f"Bearer {tok}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    with TestClient(e["api"]) as client:
        return client.request(method, D + path, json={} if body is None else body, headers=headers)


def credential(e, scopes=("workspace.read", WRITE)):
    row, cred = directory_credential(e, scopes)
    return row, cred, token(e, cred)["access_token"]


def raw_invite(e, iid):
    mail = e["store"].one("SELECT body FROM mail_outbox WHERE ref_id=? AND kind='invite'", (iid,))
    assert mail
    return re.search(r"#token=([A-Za-z0-9_-]+)", mail["body"]).group(1)


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text


def test_application_invite_accept_change_and_remove_have_real_effects_and_attribution(plane):
    e = plane
    approval, cred, tok = credential(e)
    email = e["users"]["stranger"]["email"]
    uid = e["users"]["stranger"]["id"]
    r = write(e, tok, "/invitations", {"email": email, "role": "viewer"}, key="onboard-1")
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    row = e["store"].one("SELECT * FROM invitations WHERE id=?", (iid,))
    assert row["invited_via"] == cred["client_id"] and row["invited_approval_id"] == approval["id"]
    assert e["ident"].membership(e["ws"], uid) is None
    raw = raw_invite(e, iid)
    preview = e["api"].state.invites.preview(raw)
    assert "集成 Fixture automation" in preview["inviter"] and "批准" in preview["inviter"]
    mail = e["store"].one("SELECT body FROM mail_outbox WHERE ref_id=?", (iid,))
    assert "集成 Fixture automation" in mail["body"] and "批准" in mail["body"]
    human_list = e["api"].state.invites.list_for_workspace(e["ws"], e["users"]["owner"]["id"])
    assert "集成 Fixture automation" in next(i["inviter"] for i in human_list if i["id"] == iid)
    received = e["store"].one("SELECT params FROM notifications WHERE user_id=? AND object_id=?", (uid, iid))
    assert "集成 Fixture automation" in json.loads(received["params"])["inviter"]
    assert post(e["clients"]["stranger"], "/api/auth/invitations/accept", {"token": raw}).status_code == 200
    assert e["ident"].membership(e["ws"], uid)["role"] == "viewer"
    promote = write(e, tok, f"/members/{uid}", {"role": "editor", "expected_role": "viewer"}, method="PATCH")
    assert promote.status_code == 200 and promote.json()["role"] == "editor", promote.text
    assert_error(write(e, tok, f"/members/{uid}", {"role": "viewer", "expected_role": "viewer"}, method="PATCH"), 409, "role_changed")
    removed = write(e, tok, f"/members/{uid}/remove", {"reason": "Employee left the fixture team", "expected_role": "editor"})
    assert removed.status_code == 200 and removed.json()["removed"], removed.text
    assert e["ident"].membership(e["ws"], uid) is None
    assert_error(write(e, tok, f"/members/{uid}/remove", {"reason": "Retry completed removal", "expected_role": "editor"}), 404, "no_member")
    actions = e["store"].all("SELECT * FROM audit_events WHERE actor_id=? AND "
                             "(action LIKE 'invite.%' OR action LIKE 'member.%')", (cred["client_id"],))
    assert {a["action"] for a in actions} >= {"invite.create", "member.role", "member.remove"}
    for event in actions:
        assert event["actor_kind"] == "machine"
        meta = json.loads(event["meta"])
        assert meta["actor_kind"] == "service_account"
        assert meta["approved_by"] == e["users"]["owner"]["id"] and meta["approval_id"] == approval["id"]
        assert meta["via"] == "api" and event["request_id"]
    notice = e["store"].one("SELECT params FROM notifications WHERE user_id=? AND kind='member.removed'", (uid,))
    assert "集成 Fixture automation" in json.loads(notice["params"])["actor"]
    # The API console can find its application actor while ordinary browser invitations stay
    # in the account governance audit surface rather than being swept into API diagnostics.
    human = post(e["owner"], f"/api/auth/workspaces/{e['ws']}/invitations",
                 {"email": "human-invite@example.test", "role": "viewer"})
    assert human.status_code == 200, human.text
    console = e["admin"].get(f"/api/admin/api-platform/audit?action=invite.create&workspace_id={e['ws']}")
    assert console.status_code == 200, console.text
    assert [event["object_id"] for event in console.json()["items"]] == [iid]
    assert console.json()["items"][0]["meta"]["actor_kind"] == "service_account"


def test_invitation_idempotency_is_atomic_concurrent_and_keeps_client_namespace(plane):
    e = plane
    _, cred, tok = credential(e)
    body = {"email": "new-member@example.test", "role": "viewer"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(e, tok, "/invitations", body, key="simultaneous"), range(2)))
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert results[0].json() == results[1].json()
    assert sum(r.headers.get("Idempotent-Replayed") == "true" for r in results) == 1
    iid = results[0].json()["id"]
    assert e["store"].one("SELECT COUNT(*) AS n FROM mail_outbox WHERE ref_id=?", (iid,))["n"] == 1
    assert e["store"].one("SELECT COUNT(*) AS n FROM invitations WHERE email_key=?", (body["email"],))["n"] == 1
    assert_error(write(e, tok, "/invitations", {**body, "role": "editor"}, key="simultaneous"), 409, "idempotency_conflict")
    # A new access token after rotation remains in the same service-client namespace.
    from synwevo_core.oidc import Clients
    secret = Clients(e["store"]).rotate_secret(cred["client_id"])["secret"]
    new_tok = token(e, {**cred, "client_secret": secret})["access_token"]
    replay = write(e, new_tok, "/invitations", body, key="simultaneous")
    assert replay.status_code == 201 and replay.headers.get("Idempotent-Replayed") == "true", replay.text
    # The response cache stores metadata only, never an acceptance link.
    cached = e["store"].one("SELECT response_body FROM idempotency WHERE key='simultaneous'")["response_body"]
    assert raw_invite(e, iid) not in cached and "token" not in cached


def test_resend_is_idempotent_withdraws_old_link_and_revoke_is_safe(plane):
    e = plane
    _, _, tok = credential(e)
    r = write(e, tok, "/invitations", {"email": "resend@example.test", "role": "viewer"})
    assert r.status_code == 201, r.text
    iid = r.json()["id"]
    old_raw = raw_invite(e, iid)
    assert_error(write(e, tok, f"/invitations/{iid}/resend"), 429, "invite_cooldown")
    e["store"].execute("UPDATE invitations SET created_at=? WHERE id=?", (now() - 61, iid))
    resend = write(e, tok, f"/invitations/{iid}/resend", key="resend-once")
    assert resend.status_code == 201, resend.text
    next_id = resend.json()["id"]
    assert next_id != iid and e["api"].state.invites.preview(old_raw)["state"] == "revoked"
    replay = write(e, tok, f"/invitations/{iid}/resend", key="resend-once")
    assert replay.status_code == 201 and replay.json() == resend.json()
    assert e["store"].one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='invite'")["n"] == 2
    assert write(e, tok, f"/invitations/{next_id}/revoke").status_code == 200
    assert write(e, tok, f"/invitations/{next_id}/revoke").status_code == 200
    assert_error(write(e, tok, f"/invitations/{next_id}/resend"), 409, "invite_revoked")


def test_withdrawn_approval_blocks_replay_and_pending_invitation_acceptance(plane):
    e = plane
    approval, _, tok = credential(e)
    body = {"email": "withdrawn@example.test", "role": "viewer"}
    created = write(e, tok, "/invitations", body, key="replay-after-withdrawal")
    assert created.status_code == 201, created.text
    raw = raw_invite(e, created.json()["id"])
    reauth(e["owner"])
    assert post(e["owner"], f"{OWNER}/{approval['id']}/revoke", {"reason": REASON}).status_code == 200
    assert write(e, tok, "/invitations", body, key="replay-after-withdrawal").status_code in (401, 403)
    with pytest.raises(ServiceError) as exc:
        e["api"].state.invites.accept(raw, password=PW, display_name="Fixture invitee")
    assert exc.value.code == "inviter_lost_permission"
    assert e["ident"].by_email(body["email"]) is None


@pytest.mark.parametrize("mutation", ["disable", "scope", "rebind", "approver_demoted"])
def test_invitation_acceptance_rechecks_original_application_authority(plane, mutation):
    e = plane
    approval, cred, tok = credential(e)
    out = write(e, tok, "/invitations", {"email": "authority@example.test", "role": "viewer"})
    assert out.status_code == 201, out.text
    raw = raw_invite(e, out.json()["id"])
    if mutation == "disable":
        e["store"].execute("UPDATE oauth_clients SET disabled_at=? WHERE client_id=?", (now(), cred["client_id"]))
    elif mutation == "scope":
        e["store"].execute("UPDATE oauth_clients SET api_scopes='workspace.read' WHERE client_id=?", (cred["client_id"],))
    elif mutation == "rebind":
        from synwevo_core.oidc import workspace_resource
        replacement = request_approval(e, scopes=["workspace.read", WRITE], resources=[workspace_resource()])
        approve(e, replacement)
        e["store"].execute("UPDATE api_platform_bindings SET approval_id=? WHERE credential_id=?", (replacement["id"], cred["client_id"]))
    else:
        e["ident"].add_member(e["ws"], e["users"]["stranger"]["id"], "owner")
        e["ident"].change_role(e["ws"], e["users"]["owner"]["id"], "admin")
    with pytest.raises(ServiceError) as exc:
        e["api"].state.invites.accept(raw, password=PW, display_name="Fixture invitee")
    assert exc.value.code == "inviter_lost_permission"
    assert e["ident"].by_email("authority@example.test") is None


def test_scope_role_field_and_workspace_boundaries(plane):
    e = plane
    _, _, read_tok = credential(e, ("workspace.read",))
    assert_error(write(e, read_tok, "/invitations", {"email": "read-only@example.test", "role": "viewer"}), 403, "forbidden")
    _, _, tok = credential(e)
    for role in ("admin", "owner"):
        assert_error(write(e, tok, "/invitations", {"email": "privileged@example.test", "role": role}), 403, "role_needs_person")
    uid = e["users"]["owner"]["id"]
    assert_error(write(e, tok, f"/members/{uid}", {"role": "viewer", "expected_role": "viewer"}, method="PATCH"), 403, "role_needs_person")
    assert_error(write(e, tok, f"/members/{uid}/remove", {"reason": "Forbidden owner removal", "expected_role": "viewer"}), 403, "role_needs_person")
    for extra in ({"actor_id": uid}, {"workspace_id": e["other_ws"]}, {"can_export": "true"}, {"expires_in_days": True}):
        assert_error(write(e, tok, "/invitations", {"email": "fields@example.test", "role": "viewer", **extra}), 400, "invalid")
    assert_error(write(e, tok, f"/members/{uid}", {"role": "editor"}, method="PATCH"), 400, "invalid")
    assert_error(write(e, tok, f"/members/{uid}/remove", {"reason": "Removal needs version"}), 400, "invalid")
    outsider = e["users"]["stranger"]["id"]
    assert_error(write(e, tok, f"/members/{outsider}", {"role": "editor", "expected_role": "viewer"}, method="PATCH"), 404, "no_member")
    other, _ = e["api"].state.invites.create(workspace_id=e["other_ws"], email="other@example.test", role="viewer", actor_id=outsider)
    assert_error(write(e, tok, f"/invitations/{other['id']}/resend"), 404, "no_invite")
    assert_error(write(e, tok, f"/invitations/{other['id']}/revoke"), 404, "no_invite")


def test_machine_cannot_supersede_a_human_administrator_invitation(plane):
    e = plane
    _, _, tok = credential(e)
    row, _ = e["api"].state.invites.create(workspace_id=e["ws"], email="administrator-invite@example.test",
        role="admin", actor_id=e["users"]["owner"]["id"])
    e["store"].execute("UPDATE invitations SET created_at=? WHERE id=?", (now() - 61, row["id"]))
    assert_error(write(e, tok, "/invitations", {"email": row["email_key"], "role": "viewer"}), 403, "role_needs_person")
    assert_error(write(e, tok, f"/invitations/{row['id']}/resend"), 403, "role_needs_person")
    assert_error(write(e, tok, f"/invitations/{row['id']}/revoke"), 403, "role_needs_person")
    assert e["api"].state.invites.get(row["id"])["revoked_at"] is None


def test_mail_failure_rolls_back_the_invitation_and_same_key_can_retry(plane, monkeypatch):
    e = plane
    _, _, tok = credential(e)
    body = {"email": "mail-retry@example.test", "role": "viewer"}
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    failed = write(e, tok, "/invitations", body, key="mail-failed")
    assert failed.status_code == 503, failed.text
    assert e["store"].one("SELECT COUNT(*) AS n FROM invitations WHERE email_key=?", (body["email"],))["n"] == 0
    assert e["store"].one("SELECT COUNT(*) AS n FROM idempotency WHERE key='mail-failed'")["n"] == 0
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    assert write(e, tok, "/invitations", body, key="mail-failed").status_code == 201


def test_role_compare_is_inside_mutation_and_prevents_lost_updates(plane, monkeypatch):
    e = plane
    _, _, tok = credential(e)
    uid = e["users"]["stranger"]["id"]
    e["ident"].add_member(e["ws"], uid, "viewer")
    ident = e["api"].state.identity
    original = ident.change_role
    def concurrent_change(wid, user_id, role, **kwargs):
        ident.store.execute("UPDATE memberships SET role='editor' WHERE workspace_id=? AND user_id=?", (wid, user_id))
        return original(wid, user_id, role, **kwargs)
    monkeypatch.setattr(ident, "change_role", concurrent_change)
    assert_error(write(e, tok, f"/members/{uid}", {"role": "viewer", "expected_role": "viewer"}, method="PATCH"), 409, "role_changed")
    # A stale remove is also a conflict, never a way to delete a changed target.
    e["ident"].change_role(e["ws"], uid, "editor")
    assert_error(write(e, tok, f"/members/{uid}/remove", {"reason": "Stale employee removal", "expected_role": "viewer"}), 409, "role_changed")
    assert e["ident"].membership(e["ws"], uid)["role"] == "editor"


def test_freeze_and_daily_invitation_limit_apply_to_directory_writes(plane, monkeypatch):
    e = plane
    _, _, tok = credential(e)
    monkeypatch.setattr("synwevo_identity.invites.INVITES_PER_DAY", 1)
    assert write(e, tok, "/invitations", {"email": "first@example.test", "role": "viewer"}).status_code == 201
    limited = write(e, tok, "/invitations", {"email": "second@example.test", "role": "viewer"})
    assert_error(limited, 429, "invite_rate_limited")
    assert int(limited.headers["Retry-After"]) > 0
    e["ident"].set_workspace_frozen(e["ws"], True)
    assert write(e, tok, "/invitations", {"email": "frozen@example.test", "role": "viewer"}).status_code in (401, 403)


def test_unapproved_cli_service_cannot_write_members(plane):
    from synwevo_core.apps import PLATFORM_APP_ID
    from synwevo_core.oidc import Clients
    e = plane
    client, secret = Clients(e["store"]).register(client_id="unapproved-directory-fixture", app_id=PLATFORM_APP_ID,
        name="Unapproved fixture", redirect_uris=[], kind="service", confidential=True,
        workspace_id=e["ws"], api_scopes=WRITE)
    tok = token(e, {"client_id": client["client_id"], "client_secret": secret})["access_token"]
    assert_error(write(e, tok, "/invitations", {"email": "not-approved@example.test", "role": "viewer"}), 403, "approval_inactive")
    assert e["store"].one("SELECT COUNT(*) AS n FROM invitations WHERE email_key='not-approved@example.test'")["n"] == 0


def test_removal_withdraws_sessions_and_pending_invitations(plane):
    e = plane
    _, _, tok = credential(e)
    uid = e["users"]["stranger"]["id"]
    e["ident"].add_member(e["ws"], uid, "admin")
    pending, _ = e["api"].state.invites.create(workspace_id=e["ws"], email="pending-by-former-admin@example.test",
        role="viewer", actor_id=uid)
    e["ident"].change_role(e["ws"], uid, "editor")
    e["store"].execute("UPDATE sessions SET workspace_id=? WHERE user_id=? AND revoked_at IS NULL", (e["ws"], uid))
    result = write(e, tok, f"/members/{uid}/remove", {"reason": "Former administrator leaving", "expected_role": "editor"})
    assert result.status_code == 200, result.text
    assert e["api"].state.invites.get(pending["id"])["revoked_at"]
    assert e["store"].one("SELECT COUNT(*) AS n FROM sessions WHERE user_id=? AND workspace_id=?", (uid, e["ws"]))["n"] == 0


@pytest.mark.parametrize("locale,mark", [("zh-Hans", "集成 Fixture automation（由"),
                                         ("zh-Hant", "整合 Fixture automation（由"),
                                         ("en", "Integration Fixture automation (approved by")])
def test_invitation_application_identity_uses_recipient_locale(plane, locale, mark):
    e = plane
    _, _, tok = credential(e)
    user = e["users"]["stranger"]
    e["store"].execute("UPDATE users SET locale=? WHERE id=?", (locale, user["id"]))
    created = write(e, tok, "/invitations", {"email": user["email"], "role": "editor"})
    assert created.status_code == 201, created.text
    iid = created.json()["id"]
    assert mark in e["store"].one("SELECT body FROM mail_outbox WHERE ref_id=?", (iid,))["body"]
    assert mark in e["api"].state.invites.preview(raw_invite(e, iid))["inviter"]


def test_browser_cannot_turn_machine_invitation_into_unacceptable_admin_invitation(plane):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    e = plane
    _, _, tok = credential(e)
    created = write(e, tok, "/invitations", {"email": "no-hybrid-invite@example.test", "role": "viewer"})
    assert created.status_code == 201, created.text
    iid = created.json()["id"]
    denied = e["owner"].patch(f"/api/auth/invitations/{iid}", json={"role": "admin"},
        headers={CSRF_HEADER: e["owner"].cookies.get(CSRF_COOKIE) or ""})
    assert denied.status_code == 403 and denied.json()["error"] == "role_needs_person", denied.text
    assert e["api"].state.invites.get(iid)["role"] == "viewer"
    accepted = e["api"].state.invites.accept(raw_invite(e, iid), password=PW, display_name="Safe invitation fixture")
    assert e["ident"].membership(e["ws"], accepted["user"]["id"])["role"] == "viewer"


def test_withdrawal_journal_survives_a_later_transaction_failure_conservatively(plane, monkeypatch):
    """The external fsynced security intent cannot be rolled back with the database writes."""
    from synwevo_core import journal
    e = plane
    _, cred, tok = credential(e)
    uid = e["users"]["stranger"]["id"]
    e["ident"].add_member(e["ws"], uid, "editor")
    def unavailable_notification(*args, **kwargs):
        raise RuntimeError("isolated notification write failure fixture")
    monkeypatch.setattr(e["api"].state.notifier, "emit", unavailable_notification)
    failed = write(e, tok, f"/members/{uid}/remove", {"reason": "Fixture departure before storage failure", "expected_role": "editor"})
    assert_error(failed, 500, "internal_error")
    assert e["ident"].membership(e["ws"], uid)["role"] == "editor"
    assert not e["store"].one("SELECT 1 FROM audit_events WHERE actor_id=? AND action='member.remove'", (cred["client_id"],))
    entries = [entry for entry in journal.read(e["store"].root)
               if entry["kind"] == "member_removed" and entry.get("user_id") == uid]
    assert len(entries) == 1 and entries[0]["workspace_id"] == e["ws"]
    # An isolated restore rehearsal applies that fail-closed intent instead of re-granting
    # access based on a database backup that did not contain the completed withdrawal.
    journal.apply(e["store"], entries)
    assert e["ident"].membership(e["ws"], uid) is None
