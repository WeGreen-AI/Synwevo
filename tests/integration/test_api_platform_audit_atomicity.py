"""Real governance requests must not commit authority without their success audit.

The injected storage failure happens after INSERT, so both the mutation and a partial
audit row must roll back. All identities and credentials are isolated fixtures.
"""
import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core.machine import SCOPE_READ
from synwevo_core.oidc import docs_resource
from synwevo_core.store import Store
from tests.integration.test_api_platform import (
    BASE, OWNER, REASON, approve, issue, plane, post, request_approval,
)
from tests.integration.test_api_platform_races import authority_state
from tests.integration.test_api_platform_webhook_ops import _fixture_job


@pytest.mark.parametrize("action", ["request", "approve", "issue", "rotate", "disable", "enable", "revoke"])
def test_failed_success_audit_rolls_back_authority(plane, monkeypatch, action):
    e = plane
    approval = request_approval(e)
    if action != "approve":
        approve(e, approval)
    credential = issue(e, approval) if action in {"rotate", "disable", "enable"} else None
    if action == "enable":
        assert post(e["admin"], f"{BASE}/credentials/service_account/{credential['client_id']}/disable",
                    {"reason": REASON}).status_code == 200
    client = e["admin"]
    body = {"reason": REASON}
    if action == "request":
        path, audit_action = BASE + "/approvals", "approval.request"
        body.update(workspace_id=e["ws"], name="Isolated audit rollback", credential_kind="api_key",
                    scopes=[SCOPE_READ], resources=[docs_resource()], expires_in_days=1)
    elif action in {"approve", "issue", "revoke"}:
        prefix = OWNER if action == "approve" else BASE + "/approvals"
        path = f"{prefix}/{approval['id']}/{action}"
        audit_action = "credential.issue" if action == "issue" else "approval." + action
        if action == "approve":
            client = e["owner"]
            # request_approval only reauthenticates the platform administrator.
            from tests.integration.test_api_platform import reauth
            reauth(client)
            body.update(scopes=approval["requested_scopes"], resources=approval["requested_resources"])
    else:
        path = f"{BASE}/credentials/service_account/{credential['client_id']}/{action}"
        audit_action = "credential." + action
    audit_action = "api_platform." + audit_action
    before = authority_state(e)
    audit_before = e["store"].all("SELECT id FROM audit_events WHERE action=? AND result='ok'", (audit_action,))
    original = Store.execute
    failures = []

    def fail_after_success_insert(store, sql, params=()):
        result = original(store, sql, params)
        if sql.startswith("INSERT INTO audit_events") and audit_action in params and "ok" in params:
            failures.append(True)
            raise OSError("isolated audit write failure")
        return result

    monkeypatch.setattr(Store, "execute", fail_after_success_insert)
    with TestClient(e["api"], raise_server_exceptions=False) as request_client:
        request_client.cookies.update(client.cookies)
        response = post(request_client, path, body)
    assert response.status_code >= 500
    assert failures == [True]
    assert authority_state(e) == before
    assert e["store"].all("SELECT id FROM audit_events WHERE action=? AND result='ok'", (audit_action,)) == audit_before
    errors = e["store"].all("SELECT meta FROM audit_events WHERE action=? AND result='error'", (audit_action,))
    assert len(errors) == 1
    assert json.loads(errors[0]["meta"])["code"] == "journal_unavailable"
    assert response.status_code == 503
    assert "isolated audit write failure" not in response.text


@pytest.mark.parametrize("fail_audit", [False, True])
def test_job_cancellation_notifies_only_after_audited_commit(plane, monkeypatch, fail_audit):
    from tests.integration.test_api_platform import reauth
    e = plane
    reauth(e["admin"])
    jid = _fixture_job(e)
    observed = []
    app_store = e["api"].state.app.store

    def on_committed_event():
        assert not app_store._c().in_transaction
        observed.append(e["store"].one("SELECT status FROM jobs WHERE id=?", (jid,))["status"])
        assert e["store"].one("SELECT id FROM audit_events WHERE action='api_platform.job.cancel' AND result='ok'")

    monkeypatch.setattr(e["api"].state.app, "on_event", on_committed_event)
    original = Store.execute

    def fail_after_success_insert(store, sql, params=()):
        result = original(store, sql, params)
        if fail_audit and sql.startswith("INSERT INTO audit_events") and "api_platform.job.cancel" in params and "ok" in params:
            raise OSError("isolated cancellation audit failure")
        return result

    monkeypatch.setattr(Store, "execute", fail_after_success_insert)
    with TestClient(e["api"], raise_server_exceptions=False) as client:
        client.cookies.update(e["admin"].cookies)
        response = post(client, f"{BASE}/jobs/{jid}/cancel", {"reason": REASON})
    assert response.status_code == (503 if fail_audit else 200)
    assert observed == ([] if fail_audit else ["cancelled"])
    assert e["store"].one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == ("queued" if fail_audit else "cancelled")
    assert len(e["store"].all("SELECT id FROM job_events WHERE job_id=?", (jid,))) == (0 if fail_audit else 1)
