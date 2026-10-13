"""A44: what a platform deletion does to the workspace's machine credentials, through the real API
platform flow (request → owner approval → console issue) and the real Docs API — and what the
deletion and recovery previews say about them.

A credential issued through an approval is bound to it; the deletion revokes the approval for good,
so the credential ends with it (as the API console's revoke ends it) and is never counted as
"paused, comes back". A historical key bound to no approval is paused and comes back. Work queued by
an ended credential stops for good after the recovery too, and the recovery preview says so.

Isolated data directory, file mail and accounts of `tests/integration/test_api_platform.plane`.
"""
from __future__ import annotations

from synwevo_core import authority
from synwevo_core.api_platform import SCOPES
from synwevo_core.auth import Auth
from synwevo_core.machine import SCOPE_READ
from synwevo_core.store import now
from tests.integration.test_api_platform import (approve, call, issue, plane, post, reauth,  # noqa: F401
                                                 request_approval, service_token)

REASON = "Isolated fixture: platform deletion of a workspace"


def _team(e, name):
    team = e["ident"].create_workspace(name, owner_id=e["users"]["owner"]["id"])["id"]
    e["ws"] = team
    return team


def _bound(e, kind, scopes):
    row = request_approval(e, kind, scopes)
    approve(e, row)
    return issue(e, row)


def _preview(e, wid, action):
    r = post(e["admin"], f"/api/admin/workspaces/{wid}/lifecycle/preview", {"action": action})
    assert r.status_code == 200, r.text
    return r.json()


def _act(e, wid, action, key):
    p = _preview(e, wid, action)
    reauth(e["admin"])
    r = post(e["admin"], f"/api/admin/workspaces/{wid}/lifecycle/{action}",
             {"reason": REASON, "preview_token": p["preview_token"], "idempotency_key": key})
    assert r.status_code == 200, r.text
    return r.json()


def test_only_unbound_credentials_are_paused_and_come_back(plane):
    """A44-ACC-1 / A44-1 / A44-WEB-1, end to end: one historical key, one console-issued key and one
    console-issued service account in an organization workspace."""
    e = plane
    team = _team(e, "Deletion credentials team")
    historical, _ = Auth(e["store"]).create_key("Historical fixture key", team, ("read",))
    key = _bound(e, "api_key", list(SCOPES))
    service = _bound(e, "service_account", [SCOPE_READ])
    assert call(e, historical).status_code == 200 and call(e, key["key"]).status_code == 200
    assert service_token(e, service).status_code == 200

    p = _preview(e, team, "delete")
    assert (p["impact"]["approvals"], p["impact"]["api_keys"], p["impact"]["service_accounts"]) == (2, 1, 0)
    assert {"approvals_revoked_for_good", "credentials_paused"} <= set(p["notes"])
    deleted = _act(e, team, "delete", "a44_credentials_delete_01")
    assert deleted["withdrawn"]["approvals"] == 2
    # ended with their approvals, as the console's revoke ends them — not left stored as live
    lineage = key["credential"]["lineage"]
    assert e["store"].one("SELECT revoke_reason FROM api_keys WHERE id=?", (lineage,))["revoke_reason"] == "approval_revoked"
    assert e["store"].one("SELECT disabled_at FROM oauth_clients WHERE client_id=?", (service["client_id"],))["disabled_at"]
    assert call(e, historical).status_code != 200, "paused while deleted"

    r = _preview(e, team, "recover")
    assert (r["impact"]["approvals"], r["impact"]["api_keys"], r["impact"]["service_accounts"]) == (0, 1, 0)
    assert {"approvals_revoked_for_good", "credentials_paused"} <= set(r["notes"])
    assert "recover_within_7_days" not in r["notes"]
    _act(e, team, "recover", "a44_credentials_recover_1")
    assert call(e, historical).status_code == 200, "the historical key comes back"
    assert call(e, key["key"]).status_code in (401, 403)
    assert service_token(e, service).status_code != 200
    again = _preview(e, team, "delete")
    assert (again["impact"]["approvals"], again["impact"]["api_keys"], again["impact"]["service_accounts"]) == (0, 1, 0)


def test_work_of_an_ended_credential_is_announced_as_ending_on_recovery(plane):
    """A44-WEB-2 (a): a Docs subscription and a queued Docs job created by the console-issued service
    account (FIXTURE rows, `created_by` / `principal` as Docs stores them). The deletion ends the
    service account with its approval, so after the recovery both are refused for good — and the
    recovery preview says so before the runner or the dispatcher has touched them."""
    e = plane
    team = _team(e, "Deletion dependents team")
    service = _bound(e, "service_account", [SCOPE_READ])
    desc = authority.describe({"kind": "service_account", "client_id": service["client_id"], "tenant": team})
    ts = now()
    e["store"].insert("webhook_subscriptions", {"id": "a44-dependent-hook", "tenant": team, "created_by": authority.dumps(desc),
                                                "url": "https://receiver.example.test/hook", "events": '["*"]',
                                                "secret_enc": "fixture-sealed", "created_at": ts, "updated_at": ts})
    e["store"].insert("jobs", {"id": "a44-dependent-job", "status": "queued", "created_at": ts, "tenant": team,
                               "principal": authority.dumps(desc)})
    assert authority.check(e["store"], desc, need="read", tenant=team) == (True, None)
    p = _preview(e, team, "delete")
    assert p["impact"]["running_jobs"] == 1 and p["impact"]["webhooks"] == 1
    _act(e, team, "delete", "a44_dependents_delete_001")
    r = _preview(e, team, "recover")
    assert {"jobs_halt_for_good", "webhooks_disabled_for_good"} <= set(r["notes"]), r["notes"]
    _act(e, team, "recover", "a44_dependents_recover_01")
    assert authority.check(e["store"], desc, need="read", tenant=team)[0] is False
