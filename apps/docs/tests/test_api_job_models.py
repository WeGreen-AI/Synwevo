"""Job response contracts over HTTP, including old stored shapes and sparse wire fields."""
from __future__ import annotations

import json
import time

from synwevo_core.store import new_id

from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401


def _stop_runner(api):
    service = api.c.app_state.jobs
    service.stop()
    for thread in (service._thread, service._supervisor):
        if thread:
            thread.join(timeout=3)
            assert not thread.is_alive()
    return service


def test_job_lifecycle_preserves_replay_cancel_and_paging_contract(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    service = _stop_runner(api)
    headers = {**h, "Idempotency-Key": "job-response-create"}
    created = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=headers)
    assert created.status_code == 202, created.text
    job = created.json()
    jid = job["id"]
    assert job["status"] == "queued" and job["finished_at"] is None
    assert "idempotent_replay" not in job and "cancel" not in job
    replay = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=headers)
    assert replay.status_code == 202 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json() == {**job, "idempotent_replay": True}
    assert "Deprecation" not in replay.headers
    assert _ok(c.get(f"/api/v1/jobs/{jid}", headers=h)) == job

    cancelled = _ok(c.post(f"/api/v1/jobs/{jid}/cancel", headers=h))
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancel"] == {"accepted": True, "reason": None}
    terminal = _ok(c.post(f"/api/v1/jobs/{jid}/cancel", headers=h))
    assert terminal["cancel"] == {"accepted": False, "reason": "already_terminal"}
    assert "cancel" not in _ok(c.get(f"/api/v1/jobs/{jid}", headers=h))

    retry_headers = {**h, "Idempotency-Key": "job-response-retry"}
    retry = c.post(f"/api/v1/jobs/{jid}/retry", json={}, headers=retry_headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()["run_no"] == 2 and retry.json()["status"] == "queued"
    assert "cancel" not in retry.json() and "idempotent_replay" not in retry.json()
    repeated = c.post(f"/api/v1/jobs/{jid}/retry", json={}, headers=retry_headers)
    assert repeated.headers["Idempotent-Replayed"] == "true"
    assert repeated.json() == {**retry.json(), "idempotent_replay": True}
    assert service._claim(jid) == jid
    assert _ok(c.post(f"/api/v1/jobs/{jid}/cancel", headers=h))["cancel"] == {"accepted": True, "reason": None}
    requested = _ok(c.post(f"/api/v1/jobs/{jid}/cancel", headers=h))
    assert requested["status"] == "cancelling"
    assert requested["cancel"] == {"accepted": True, "reason": "already_requested"}

    first = c.get(f"/api/v1/jobs/{jid}/runs?limit=1", headers=h)
    page = _ok(first)
    assert page["total"] == 2 and page["offset"] == 0 and page["next_offset"] == 1
    assert 'rel="next"' in first.headers["Link"]
    last = _ok(c.get(f"/api/v1/jobs/{jid}/runs", params={"cursor": page["next_cursor"], "limit": 1}, headers=h))
    assert last["offset"] is None and last["next_offset"] is None and last["next_cursor"] is None
    assert [page["items"][0]["run_no"], last["items"][0]["run_no"]] == [1, 2]
    for row in page["items"] + last["items"]:
        assert isinstance(row["requested_by_principal"], str) and "principal" not in row

    events = _ok(c.get(f"/api/v1/jobs/{jid}/events", headers=h))
    assert events["items"][-1]["type"] == "job.cancel_requested"
    assert _ok(c.get(f"/api/v1/jobs/{jid}/events", params={"after": events["next_after"]}, headers=h)) == {
        "items": [], "next_after": events["next_after"]}
    assert c.get(f"/api/v1/jobs/{jid}", headers=keys["other"]).status_code == 404
    assert c.post(f"/api/v1/jobs/{jid}/cancel", headers=keys["ro"]).status_code == 403


def test_historical_job_rows_keep_nulls_sparse_counts_and_open_json(api, keys):
    c, h = api.c, keys["rw"]
    _stop_runner(api)
    store = c.app_state.app.store
    pid = api.project("Test fixture: historical job response")
    old, sparse = new_id("j"), new_id("j")
    # TEST FIXTURE: pre-planning snapshots, null metadata and sparse persisted counters.
    for jid, created, counts, options in ((old, 1.0, None, None), (sparse, 2.0, "{}", '{"keep_fields":false,"extension":{"v":1}}')):
        store.execute("INSERT INTO jobs (id, project_id, status, created_at, snapshot, counts, options, tenant) "
                      "VALUES (?,?,?,?,?,?,?,?)", (jid, pid, "completed", created, "{}", counts, options, "default"))
    store.execute("UPDATE jobs SET status='failed', error=?, summary=?, halt_reason=? WHERE id=?",
                  ("fixture failure", '{"error_code":"has_errors"}', "authorization_revoked:credential_revoked", old))
    detail = _ok(c.get(f"/api/v1/jobs/{old}", headers=h))
    assert detail["template"] == {"id": None, "name": None, "format": None, "version_no": None}
    assert detail["options"] is None and detail["phase"] == "generating" and detail["principal"] is None
    assert detail["started_at"] is None and detail["finished_at"] is None and detail["request_id"] is None
    assert detail["error"] == "fixture failure" and detail["error_code"] == "has_errors"
    assert detail["halt_reason"] == "authorization_revoked:credential_revoked"
    assert set(detail["counts"]) == {"samples", "documents", "files", "failed", "skipped", "cancelled", "pending", "running", "planned", "unplanned"}
    assert set(detail["counts"].values()) == {0}
    assert "idempotent_replay" not in detail and "cancel" not in detail
    first = _ok(c.get(f"/api/v1/projects/{pid}/jobs?limit=1", headers=h))
    assert first["items"][0]["counts"] == {}
    assert first["items"][0]["options"] == {"keep_fields": False, "extension": {"v": 1}}
    last = _ok(c.get(f"/api/v1/projects/{pid}/jobs", params={"cursor": first["next_cursor"], "limit": 1}, headers=h))
    assert last["offset"] is None and last["next_offset"] is None
    assert last["items"][0]["counts"] is None and last["items"][0]["options"] is None
    assert last["items"][0]["error_code"] == "has_errors"
    assert last["items"][0]["halt_reason"] == detail["halt_reason"]

    # An explicit null counter is distinct from an absent counter in the old public view.
    store.execute("UPDATE jobs SET counts=? WHERE id=?", ('{"success":null,"failed":null}', sparse))
    sparse_detail = _ok(c.get(f"/api/v1/jobs/{sparse}", headers=h))
    assert sparse_detail["counts"]["documents"] is None and sparse_detail["counts"]["failed"] is None
    assert sparse_detail["counts"]["samples"] == 0
    assert _ok(c.get(f"/api/v1/projects/{pid}/jobs", headers=h))["items"][0]["counts"] == {
        "success": None, "failed": None}

    store.execute("INSERT INTO job_runs (job_id, run_no, trigger) VALUES (?,1,'create')", (old,))
    run = _ok(c.get(f"/api/v1/jobs/{old}/runs", headers=h))["items"][0]
    assert run["items"] is None and run["requested_by_principal"] is None
    assert run["created_at"] is None and run["status"] is None
    issues = [None, "legacy issue", [{"code": "legacy", "trace": "private-test-trace", "extension": [False, None]}, 7]]
    for i, value in enumerate(issues):
        store.execute("INSERT INTO job_items (id, job_id, seq, status, issues) VALUES (?,?,?,?,?)",
                      (new_id("i"), old, i, "failed", json.dumps(value)))
    items = _ok(c.get(f"/api/v1/jobs/{old}/items", headers=h))["items"]
    assert [row["issues"] for row in items] == [None, "legacy issue", [{"code": "legacy", "extension": [False, None]}, 7]]
    assert all(row["record_key"] is None and row["artifacts"] == [] for row in items)
    # Nullable columns have always been returned as null by both artifact queries.
    aid = new_id("a")
    store.execute("INSERT INTO artifacts (id, job_id, item_id) VALUES (?,?,?)", (aid, old, items[0]["id"]))
    nested = _ok(c.get(f"/api/v1/jobs/{old}/items", headers=h))["items"][0]["artifacts"][0]
    assert nested == {"id": aid, "item_id": items[0]["id"], "format": None, "filename": None,
                      "size": None, "sha256": None, "verification": None, "run_no": 1}
    flat = _ok(c.get(f"/api/v1/jobs/{old}/artifacts", headers=h))["items"][0]
    assert flat == {"id": aid, "item_id": items[0]["id"], "format": None, "filename": None,
                    "size": None, "sha256": None, "created_at": None, "seq": 0, "label": None, "run_no": 1}
    event_data = {"skipped": None, "previous_worker": False, "extension": [1, None, {"x": True}]}
    store.execute("INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data) VALUES (?,?,?,?,?,?,?)",
                  (new_id("ev"), old, "default", "job.recovered", 1, time.time(), json.dumps(event_data)))
    event = _ok(c.get(f"/api/v1/jobs/{old}/events", headers=h))["items"][0]
    assert event["data"] == event_data
    for value in (None, ["legacy", 2, False], "legacy detail"):
        store.execute("UPDATE job_events SET data=? WHERE id=?", (json.dumps(value), event["id"]))
        assert _ok(c.get(f"/api/v1/jobs/{old}/events", headers=h))["items"][0]["data"] == value


def test_completed_job_artifact_views_keep_distinct_fields_and_filter_private_data(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    submitted = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))
    job = _wait(api, keys, submitted["id"])
    assert job["status"] == "completed"
    _stop_runner(api)
    store = c.app_state.app.store
    jid = job["id"]
    first = store.one("SELECT id FROM job_items WHERE job_id=? ORDER BY seq", (jid,))["id"]
    # TEST FIXTURE: older issue details must lose only their private traceback.
    store.execute("UPDATE job_items SET issues=? WHERE id=?", (json.dumps([
        {"code": "legacy", "trace": "private-test-trace", "where": {"row": 3}, "extension": [None, False]}]), first))
    verification = {"ok": True, "extension": {"rows": [1, None, False]}}
    store.execute("UPDATE artifacts SET verification=?, trace=? WHERE job_id=?", (json.dumps(verification), '"private-artifact-trace"', jid))
    descriptor = store.one("SELECT principal FROM jobs WHERE id=?", (jid,))["principal"]
    assert descriptor and "lineage" in descriptor
    urls = [f"/api/v1/jobs/{jid}", f"/api/v1/projects/{pid}/jobs", f"/api/v1/jobs/{jid}/runs",
            f"/api/v1/jobs/{jid}/items", f"/api/v1/jobs/{jid}/artifacts"]
    for url in urls:
        response = c.get(url, headers=h)
        assert response.status_code == 200, response.text
        assert "lineage" not in response.text and "private-test-trace" not in response.text
        assert "private-artifact-trace" not in response.text
    items = _ok(c.get(f"/api/v1/jobs/{jid}/items?limit=1", headers=h))
    item = items["items"][0]
    assert item["issues"] == [{"code": "legacy", "where": {"row": 3}, "extension": [None, False]}]
    nested = item["artifacts"][0]
    assert nested["verification"] == verification
    assert "created_at" not in nested and "seq" not in nested and "trace" not in nested
    artifacts = _ok(c.get(f"/api/v1/jobs/{jid}/artifacts?limit=1", headers=h))
    artifact = artifacts["items"][0]
    assert {"created_at", "seq", "label"} <= set(artifact) and "verification" not in artifact
    assert artifact["id"] == nested["id"] and artifact["sha256"] == nested["sha256"]
    for path, page in (("items", items), ("artifacts", artifacts)):
        tail = _ok(c.get(f"/api/v1/jobs/{jid}/{path}", params={"cursor": page["next_cursor"], "limit": 100}, headers=h))
        assert tail["offset"] is None and tail["next_offset"] is None and tail["next_cursor"] is None
        assert len(tail["items"]) + 1 == page["total"]


def test_nine_job_operations_declare_success_models_without_changing_http_contract(client):
    from synwevo_docs.api.v1 import SCOPE_NAMES, openapi_subset

    spec = openapi_subset(client.app)
    operations = {
        ("post", "/api/v1/projects/{pid}/jobs"): ("202", "JobView", "write"),
        ("get", "/api/v1/projects/{pid}/jobs"): ("200", "JobPage_JobListItem_", "read"),
        ("get", "/api/v1/jobs/{jid}"): ("200", "JobView", "read"),
        ("get", "/api/v1/jobs/{jid}/items"): ("200", "JobPage_JobItem_", "read"),
        ("get", "/api/v1/jobs/{jid}/runs"): ("200", "JobPage_JobRun_", "read"),
        ("get", "/api/v1/jobs/{jid}/events"): ("200", "JobEventsPage", "read"),
        ("post", "/api/v1/jobs/{jid}/cancel"): ("200", "JobCancelledView", "write"),
        ("post", "/api/v1/jobs/{jid}/retry"): ("200", "JobView", "write"),
        ("get", "/api/v1/jobs/{jid}/artifacts"): ("200", "JobPage_JobArtifact_", "read"),
    }
    for (method, path), (status, model, scope) in operations.items():
        op = spec["paths"][path][method]
        assert op["responses"][status]["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{model}"}
        assert op["security"] == [{"apiKey": [scope]}, {"platformToken": [SCOPE_NAMES[scope]]}]
        assert {"400", "401", "403", "404", "429", "5XX"} <= set(op["responses"])
        assert "422" not in op["responses"]
        assert "X-Request-ID" in op["responses"][status]["headers"]
        if method == "post" and not path.endswith("/cancel"):
            assert "409" in op["responses"]
            assert "Idempotent-Replayed" in op["responses"][status]["headers"]
            assert "Deprecation" in op["responses"][status]["headers"]
    schemas = spec["components"]["schemas"]
    assert "idempotent_replay" not in schemas["JobView"]["required"]
    assert "cancel" in schemas["JobCancelledView"]["required"]
    assert "required" not in schemas["JobRawCounts"]
    assert schemas["JobOptions"]["additionalProperties"] is True
    for model in ("JobView", "JobListItem", "JobRun"):
        assert not {"tenant", "origin", "snapshot", "lineage", "grant_family", "requested_authority"} & set(schemas[model]["properties"])
