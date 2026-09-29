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
