"""Webhook operations for platform administrators (API-6): delivery health across workspaces as
metadata only, and a protective pause the workspace can undo. No payloads, receiver paths or secrets."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.machine import SCOPE_MANAGE, SCOPE_READ, SCOPE_WRITE
from tests.integration.test_api_platform import (BASE, REASON, approve, issue, plane, post, reauth,  # noqa: F401
                                                  request_approval)


@pytest.fixture()
def hooked(plane, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    row = request_approval(plane, kind="api_key", scopes=[SCOPE_READ, SCOPE_WRITE, SCOPE_MANAGE])
    approve(plane, row)
    key = issue(plane, row)["key"]
    m = TestClient(plane["api"])
    h = {"Authorization": f"Bearer {key}"}
    sub = m.post("/api/v1/webhooks/subscriptions", headers=h,
                 json={"url": "http://127.0.0.1:9/hooks/receiver-token-abc?sig=private", "events": ["job.*", "file.*"]})
    assert sub.status_code == 201, sub.text
    return {**plane, "m": m, "h": h, "sub": sub.json()}


def test_only_platform_admins_see_the_operations_view(hooked):
    for role in ("owner", "stranger", "operator"):
        c = hooked["clients"][role]
        assert c.get(f"{BASE}/webhooks").status_code == 404
        reauth(c)
        assert post(c, f"{BASE}/webhooks/{hooked['sub']['id']}/pause", {"reason": REASON}).status_code == 404


def test_health_is_metadata_only(hooked):
    view = hooked["admin"].get(f"{BASE}/webhooks?workspace_id={hooked['ws']}").json()
    assert view["total"] == 1
    row = view["items"][0]
    assert row["id"] == hooked["sub"]["id"] and row["tenant"] == hooked["ws"] and row["status"] == "active"
    assert row["receiver"] == "http://127.0.0.1:9", "the receiver's path and query are withheld"
    assert row["events"] == ["job.*", "file.*"] and row["pending"] == 0
    text = str(view)
    assert "receiver-token-abc" not in text and "sig=private" not in text and "whsec_" not in text
    assert set(view["totals"]) == {"pending", "failing", "disabled", "next_attempt_at"}
    assert hooked["admin"].get(f"{BASE}/webhooks?workspace_id={hooked['other_ws']}").json()["total"] == 0
    assert hooked["admin"].get(f"{BASE}/webhooks?status=bogus").status_code == 422
    overview = hooked["admin"].get(f"{BASE}/overview").json()
    assert overview["webhooks"]["pending"] == 0 and overview["webhooks"]["failing"] == 0


def test_a_protective_pause_is_audited_and_the_workspace_can_undo_it(hooked):
    sid, admin = hooked["sub"]["id"], hooked["admin"]
    assert post(admin, f"{BASE}/webhooks/{sid}/pause", {"reason": "short"}).status_code in (400, 403)
    reauth(admin)
    assert post(admin, f"{BASE}/webhooks/{sid}/pause", {"reason": "short"}).status_code == 400
    r = post(admin, f"{BASE}/webhooks/{sid}/pause", {"reason": REASON})
    assert r.status_code == 200, r.text
    assert r.json() == {"id": sid, "tenant": hooked["ws"], "status": "paused"}
    own = hooked["m"].get(f"/api/v1/webhooks/subscriptions/{sid}", headers=hooked["h"]).json()
    assert own["status"] == "paused"
    assert hooked["m"].post(f"/api/v1/webhooks/subscriptions/{sid}/resume", headers=hooked["h"]).json()["status"] == "active"
    rows = hooked["store"].all("SELECT result, workspace_id, object_id FROM audit_events WHERE action='api_platform.webhook.pause' ORDER BY seq")
    assert [r["result"] for r in rows][-1] == "ok" and rows[-1]["workspace_id"] == hooked["ws"] and rows[-1]["object_id"] == sid
    assert post(admin, f"{BASE}/webhooks/wh_missing/pause", {"reason": REASON}).status_code == 404


# ------------------------------------------------------------------ jobs and the API audit trail
def _fixture_job(e, status="queued"):
    """FIXTURE: a job row in the owner's workspace, as the job service would have queued it."""
    from synwevo_core.store import new_id, now
    jid = new_id("j")
    e["store"].insert("jobs", {"id": jid, "project_id": e["project"]["id"], "status": status, "created_at": now(),
                              "snapshot": '{"template": {}, "sources": []}', "options": "{}", "counts": "{}",
                              "tenant": e["ws"], "run_no": 1, "origin": "api", "phase": "generating",
                              "request_id": "fixture-" + jid})
    return jid


def test_jobs_are_listed_as_metadata_and_a_runaway_can_be_stopped(hooked):
    jid = _fixture_job(hooked)
    admin = hooked["admin"]
    view = admin.get(f"{BASE}/jobs?status=active&workspace_id={hooked['ws']}").json()
    row = next(r for r in view["items"] if r["id"] == jid)
    assert row["status"] == "queued" and row["tenant"] == hooked["ws"] and row["request_id"] == "fixture-" + jid
    assert set(row["counts"]) == {"samples", "documents", "failed", "pending", "running"}
    assert view["totals"]["queued"] >= 1 and view["totals"]["oldest_queued_at"]
    assert "Private approval fixture document" not in str(view), "no project names in the platform view"
    assert hooked["clients"]["owner"].get(f"{BASE}/jobs").status_code == 404
    reauth(admin)
    r = post(admin, f"{BASE}/jobs/{jid}/cancel", {"reason": REASON})
    assert r.status_code == 200 and r.json()["status"] == "cancelled", r.text
    events = hooked["store"].all("SELECT type FROM job_events WHERE job_id=?", (jid,))
    assert [e["type"] for e in events] == ["job.cancelled"], "the workspace hears it like its own cancel"
    assert post(admin, f"{BASE}/jobs/j_missing/cancel", {"reason": REASON}).status_code == 404


def test_the_api_audit_trail_is_scoped_and_filterable(hooked):
    admin = hooked["admin"]
    reauth(admin)
    post(admin, f"{BASE}/webhooks/{hooked['sub']['id']}/pause", {"reason": REASON})
    trail = admin.get(f"{BASE}/audit?action=api_platform.webhook.pause").json()
    assert trail["total"] == 1 and trail["items"][0]["result"] == "ok"
    assert trail["items"][0]["actor_email"] == hooked["users"]["administrator"]["email"]
    assert trail["items"][0]["meta"]["reason"] == REASON
    everything = admin.get(f"{BASE}/audit?limit=100").json()["items"]
    assert everything and all(r["action"].startswith(("api_platform.", "oidc.", "api.credential.", "cli.client.")) for r in everything)
    assert admin.get(f"{BASE}/audit?action=admin.user.suspend").status_code == 400
    assert hooked["clients"]["owner"].get(f"{BASE}/audit").status_code == 404


def test_console_audit_names_the_target_workspace_not_the_administrators_own(plane):
    """Console audit rows used to fall back to the administrator's session workspace: an approval
    for workspace A was recorded under the admin's personal workspace, with no approval id."""
    row = request_approval(plane, kind="service_account")
    approve(plane, row)
    issue(plane, row)
    rows = {r["action"]: r for r in plane["store"].all(
        "SELECT action, workspace_id, object_id FROM audit_events WHERE action IN "
        "('api_platform.approval.request', 'api_platform.approval.approve', 'api_platform.credential.issue')")}
    assert rows["api_platform.approval.request"]["workspace_id"] == plane["ws"]
    assert rows["api_platform.approval.request"]["object_id"] == row["id"]
    assert rows["api_platform.approval.approve"]["workspace_id"] == plane["ws"]
    assert rows["api_platform.credential.issue"]["workspace_id"] == plane["ws"]
    # A platform-wide change has no workspace at all.
    reauth(plane["admin"])
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    r = plane["admin"].put(f"{BASE}/quotas/platform", json={"reason": REASON, "base_version": 0, "workspace_rpm": 900},
                           headers={CSRF_HEADER: plane["admin"].cookies.get(CSRF_COOKIE) or ""})
    assert r.status_code == 200, r.text
    quota = plane["store"].one("SELECT workspace_id FROM audit_events WHERE action='api_platform.quota.change' ORDER BY seq DESC LIMIT 1")
    assert quota["workspace_id"] is None
