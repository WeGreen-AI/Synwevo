"""A22: application identities (`app_accounts`, e.g. the Docs card sender) are not people, so person
governance leaves them out: they are not listed or counted with the accounts, and they cannot
receive recovery mail, platform roles, a suspension, a second-factor reset or the historical data.

The application identity is the real one the product creates (`ensure_app_account`), in the
isolated identity fixture of test_admin_operations; no row is hand-written.
"""
from __future__ import annotations

from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.messaging_bridge import ensure_app_account

from .test_admin_operations import post, world  # noqa: F401

REASON = "Isolated fixture: application identity governance"


def app_identity(w):
    return ensure_app_account(w["store"], w["ident"], DOCS_APP_ID)


def test_the_account_list_and_the_overview_count_people_only(world):
    w = world
    uid = app_identity(w)
    listed = w["admin"].get("/api/admin/users?limit=200").json()
    assert uid not in {u["id"] for u in listed["items"]}
    assert listed["total"] == len(listed["items"])
    found = w["admin"].get("/api/admin/users", params={"q": "@"}).json()["items"]
    assert uid not in {u["id"] for u in found}, "a search does not bring it back either"
    people = w["store"].one("SELECT COUNT(*) AS n FROM users WHERE deleted_at IS NULL "
                            "AND id NOT IN (SELECT user_id FROM app_accounts)")["n"]
    assert w["admin"].get("/api/admin/overview").json()["users"] == people
    # still readable by id, marked as what it is
    detail = w["admin"].get(f"/api/admin/users/{uid}")
    assert detail.status_code == 200 and detail.json()["application_identity"] is True


def test_no_recovery_or_verification_mail_goes_to_an_application_identity(world):
    w = world
    uid = app_identity(w)
    before = w["store"].one("SELECT COUNT(*) AS n FROM mail_outbox")["n"]
    r = post(w["admin"], f"/api/admin/users/{uid}/recovery", {"reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "application_identity", r.text
    assert w["store"].one("SELECT COUNT(*) AS n FROM mail_outbox")["n"] == before
    assert not w["store"].one("SELECT 1 FROM email_tokens WHERE user_id=?", (uid,))


def test_an_application_identity_cannot_hold_a_platform_role_or_be_suspended(world):
    w = world
    uid = app_identity(w)
    role = post(w["admin"], f"/api/admin/users/{uid}/platform-grants", {"role": "support", "reason": REASON})
    assert role.status_code == 409 and role.json()["error"] == "application_identity", role.text
    status = post(w["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"})
    assert status.status_code == 409 and status.json()["error"] == "application_identity", status.text


def test_the_historical_data_cannot_be_claimed_for_an_application_identity(world):
    w = world
    uid = app_identity(w)
    ts = w["store"].one("SELECT strftime('%s','now') AS t")["t"]
    # FIXTURE: the pre-identity workspace as the v1 migration leaves it (no members, pending_claim)
    w["store"].execute("INSERT OR IGNORE INTO workspaces (id,name,kind,status,created_at,updated_at) "
                       "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    r = post(w["admin"], "/api/admin/workspaces/default/claim", {"user_id": uid, "reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "application_identity", r.text
    assert not w["store"].one("SELECT 1 FROM memberships WHERE workspace_id='default'")
    assert w["store"].one("SELECT status FROM workspaces WHERE id='default'")["status"] == "pending_claim"
