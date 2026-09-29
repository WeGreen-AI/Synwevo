"""One trace id from the call to the job and its first event (API-2): the X-Request-ID of the request
that submitted a job is kept on the job and in job.created, so an integrator can follow it from
the call log to the job and its webhooks."""
from synwevo_core.auth import Auth

from .test_api_v1 import _ok
from .test_jobs_scheduling import _manual, _project


def test_the_submitting_request_id_follows_the_job(api, fixtures):
    pid = _project(api, fixtures, rows=2)
    _manual(api)                                            # nothing runs: the job stays queued
    token, _ = Auth(api.c.app_state.app.store).create_key("trace", "default", ("read", "write"))
    h = {"Authorization": f"Bearer {token}"}
    job = _ok(api.c.post(f"/api/v1/projects/{pid}/jobs", headers={**h, "X-Request-ID": "trace-job-7"},
                         json={"formats": ["docx"]}))
    assert job["request_id"] == "trace-job-7"
    assert _ok(api.c.get(f"/api/v1/jobs/{job['id']}", headers=h))["request_id"] == "trace-job-7"
    created = [e for e in _ok(api.c.get(f"/api/v1/jobs/{job['id']}/events", headers=h))["items"] if e["type"] == "job.created"]
    assert created and created[0]["data"]["request_id"] == "trace-job-7"
    feed = _ok(api.c.get("/api/v1/events?types=job.created", headers=h))["items"]
    assert any(e["job_id"] == job["id"] and e["data"]["request_id"] == "trace-job-7" for e in feed)
    # A job submitted in the browser carries the gate's id for that request, not a stale one.
    ui = _ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}, headers={"X-Request-ID": "trace-ui-8"}))
    assert _ok(api.c.get(f"/api/v1/jobs/{ui['id']}", headers=h))["request_id"] == "trace-ui-8"
