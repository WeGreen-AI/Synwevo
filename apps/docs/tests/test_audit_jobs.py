"""Audit of Synwevo Docs generation jobs (admin coverage G26, decision D7, work package WP6).

`JobService.create`, `.cancel` and `.retry` write their ``docs.job.*`` row inside the transaction
of the change, attributed to the authority descriptor the route passed as ``principal``:

* the browser route under the test ``client`` fixture runs without sign-in (``SYNWEVO_UI_AUTH``
  unset → ``none``), so `authority.describe_request` yields the operator descriptor and the row
  names ``("system", "operator")`` — that is what the implementation produces here, not a
  person; `test_jobs_authority.test_the_ui_route_records_who_submitted` pins the same fact for
  the job record itself;
* a legacy ``dw_`` API key on /api/v1 is ``("machine", "api:<key id>")``;
* a delegated principal is the person, with the client named in ``meta.delegation``.

Every job here stays queued: the runner is stopped (and joined) before a job is submitted, as
`test_retry_failed_only._manual_runner` does, so "cancel changed the job" is exact and nothing
renders in the background. Rows are read back by ``seq`` (the table's autoincrement) above a
mark taken before the call, never by the text ``id``.
"""
from __future__ import annotations

import json

from synwevo_core import authority
from synwevo_core.auth import Auth

from .test_jobs_idempotency import _project
from .test_retry_failed_only import _manual_runner

CREATE_META_KEYS = {"project_id", "run_no", "origin", "records", "phase", "formats", "idempotency_key", "skipped"}


# ------------------------------------------------------------------ helpers
def _mark(store) -> int:
    return store.one("SELECT COALESCE(MAX(seq), 0) AS m FROM audit_events")["m"]


def _rows(store, mark: int, prefix: str = "docs.job.") -> list[dict]:
    rows = [dict(r) for r in store.all("SELECT * FROM audit_events WHERE seq>? AND action LIKE ? ORDER BY seq",
                                       (mark, prefix + "%"))]
    for r in rows:
        r["meta_text"] = r["meta"] or ""
        r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
    return rows


def _one(store, mark: int, action: str, *, object_id: str, actor: tuple[str, str], workspace: str = "default") -> dict:
    """Exactly one new ``docs.job.*`` row since ``mark``: this action on this job by this actor."""
    rows = _rows(store, mark)
    assert [(r["action"], r["object_kind"], r["object_id"]) for r in rows] == [(action, "job", object_id)], \
        [(r["action"], r["object_id"], r["actor_kind"], r["actor_id"]) for r in rows]
    row = rows[0]
    assert (row["actor_kind"], row["actor_id"]) == actor, row
    assert row["workspace_id"] == workspace and row["result"] == "ok", row
    return row


def _count(store, action: str, jid: str) -> int:
    return store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action=? AND object_id=?", (action, jid))["n"]


def _job_events(store, jid: str) -> list[str]:
    return [r["type"] for r in store.all("SELECT type FROM job_events WHERE job_id=? ORDER BY seq", (jid,))]


def _ok(r, status: int | None = None):
    assert r.status_code < 400, (r.status_code, r.text[:800])
    if status is not None:
        assert r.status_code == status, (r.status_code, r.text[:800])
    return r.json()


def _setup(api, fixtures):
    """TEST FIXTURE: a browser-made project (template, rule, mappings; three records), the
    runner stopped and joined, and one machine credential of the default workspace."""
    pid = _project(api, fixtures)
    jobs = _manual_runner(api)
    store = api.c.app_state.app.store
    token, row = Auth(store).create_key("audit-jobs", "default", ("read", "write"))
    return pid, store, jobs, {"Authorization": f"Bearer {token}"}, row["id"]


def _queued_browser_job(api, pid: str) -> dict:
    j = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    assert j["status"] == "queued" and j["run_no"] == 1, j
    return j


# ------------------------------------------------------------------ create
def test_creating_a_job_on_each_surface_leaves_one_row(api, fixtures):
    c = api.c
    pid, store, jobs, h, kid = _setup(api, fixtures)

    # ---- browser POST /api/projects/{pid}/jobs: the operator descriptor of the no-sign-in gate
    mark = _mark(store)
    j = _queued_browser_job(api, pid)
    row = _one(store, mark, "docs.job.create", object_id=j["id"], actor=("system", "operator"))
    assert row["meta"] == {"project_id": pid, "run_no": 1, "origin": "ui", "records": 3, "phase": "generating",
                           "formats": ["docx"], "idempotency_key": False, "skipped": 0}
    assert row["request_id"] and row["request_id"] == j["request_id"], (row["request_id"], j["request_id"])
    assert "幂等" not in row["meta_text"] and "合同模板" not in row["meta_text"]          # identifiers and counts only

    # ---- v1 POST /api/v1/projects/{pid}/jobs with a key and an Idempotency-Key header
    mark = _mark(store)
    k = {**h, "Idempotency-Key": "audit-job-create-1"}
    first = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=k)
    assert first.status_code == 202, (first.status_code, first.text[:300])
    jv = first.json()
    # the v1 view leaves unset fields out: a first response carries no `idempotent_replay` at all
    assert jv["status"] == "queued" and jv["run_no"] == 1 and not jv.get("idempotent_replay"), jv
    assert "Idempotent-Replayed" not in first.headers
    row = _one(store, mark, "docs.job.create", object_id=jv["id"], actor=("machine", f"api:{kid}"))
    assert row["meta"] == {"project_id": pid, "run_no": 1, "origin": f"api:{kid}", "records": 3, "phase": "generating",
                           "formats": ["docx"], "idempotency_key": True, "skipped": 0}
    assert row["request_id"] and row["request_id"] == jv["request_id"]

    # ---- the same Idempotency-Key again: the same job, no second row
    mark = _mark(store)
    again = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=k)
    assert again.status_code == 202, (again.status_code, again.text[:300])
    assert again.json()["id"] == jv["id"] and again.json()["idempotent_replay"] is True
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert _rows(store, mark) == []
    assert _count(store, "docs.job.create", jv["id"]) == 1

    # ---- a delegated principal (an OAuth client acting for a person) through the service, as
    # `test_jobs_authority` drives descriptors: the person is the actor, the client is named beside
    mark = _mark(store)
    desc = authority.describe({"kind": "delegated", "client_id": "client-audit", "user_id": "u-audit",
                               "grant_family": "fam-audit", "tenant": "default"})
    jd = jobs.create(pid, {"formats": ["docx"]}, tenant="default", principal=desc)
    assert jd["status"] == "queued" and jd["submitted_by"] == "delegated:client-audit:u-audit"
    row = _one(store, mark, "docs.job.create", object_id=jd["id"], actor=("user", "u-audit"))
    assert row["meta"]["delegation"] == {"kind": "oauth_client", "client_id": "client-audit", "grant": "fam-audit"}
    assert set(row["meta"]) == CREATE_META_KEYS | {"delegation"}
    assert row["meta"]["project_id"] == pid and row["meta"]["run_no"] == 1 and row["meta"]["idempotency_key"] is False

    # three jobs, three rows, one each
    assert [_count(store, "docs.job.create", x) for x in (j["id"], jv["id"], jd["id"])] == [1, 1, 1]
    assert all(x["status"] == "queued" for x in (jobs.get(j["id"], "default"), jobs.get(jd["id"], "default")))


# ------------------------------------------------------------------ cancel
def test_cancel_writes_one_row_only_when_it_changes_the_job(api, fixtures):
    c = api.c
    pid, store, jobs, h, kid = _setup(api, fixtures)
    j = _queued_browser_job(api, pid)

    # ---- queued → cancelled through the browser route: one row with the change
    mark = _mark(store)
    out = api.ok(c.post(f"/api/jobs/{j['id']}/cancel"))
    assert out["status"] == "cancelled" and out["cancel"] == {"accepted": True, "reason": None}, out
    row = _one(store, mark, "docs.job.cancel", object_id=j["id"], actor=("system", "operator"))
    assert row["meta"] == {"run_no": 1, "before": {"status": "queued"}, "after": {"status": "cancelled"}, "changed": ["status"]}
    assert row["request_id"]

    # ---- the same cancel again: reported, not recorded — nothing changed
    mark = _mark(store)
    again = c.post(f"/api/jobs/{j['id']}/cancel")
    assert again.status_code == 200, again.text[:300]
    assert again.json()["cancel"] == {"accepted": False, "reason": "already_terminal"}, again.json()["cancel"]
    assert _rows(store, mark) == []
    assert _count(store, "docs.job.cancel", j["id"]) == 1

    # ---- another queued job cancelled through /api/v1 with a key: the machine is the actor
    jv = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h), 202)
    assert jv["status"] == "queued"
    mark = _mark(store)
    out = _ok(c.post(f"/api/v1/jobs/{jv['id']}/cancel", headers=h), 200)
    assert out["status"] == "cancelled" and out["cancel"] == {"accepted": True, "reason": None}, out
    row = _one(store, mark, "docs.job.cancel", object_id=jv["id"], actor=("machine", f"api:{kid}"))
    assert row["meta"] == {"run_no": 1, "before": {"status": "queued"}, "after": {"status": "cancelled"}, "changed": ["status"]}
    assert row["request_id"]
    mark = _mark(store)
    assert _ok(c.post(f"/api/v1/jobs/{jv['id']}/cancel", headers=h), 200)["cancel"]["accepted"] is False
    assert _rows(store, mark) == []
    assert _job_events(store, jv["id"]).count("job.cancelled") == 1


# ------------------------------------------------------------------ retry
def test_retry_writes_one_row_and_a_replay_none(api, fixtures):
    c = api.c
    pid, store, jobs, h, kid = _setup(api, fixtures)
    jv = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h), 202)
    cancelled = _ok(c.post(f"/api/v1/jobs/{jv['id']}/cancel", headers=h), 200)
    assert cancelled["status"] == "cancelled" and cancelled["counts"]["cancelled"] == 3, cancelled["counts"]

    # ---- POST /api/v1/jobs/{jid}/retry with an Idempotency-Key: one row for run 2
    mark = _mark(store)
    k = {**h, "Idempotency-Key": "audit-job-retry-1"}
    retried = c.post(f"/api/v1/jobs/{jv['id']}/retry", json={}, headers=k)
    assert retried.status_code == 200, (retried.status_code, retried.text[:300])
    body = retried.json()
    assert body["status"] == "queued" and body["run_no"] == 2 and not body.get("idempotent_replay"), body
    assert body["counts"]["pending"] == 3, body["counts"]
    row = _one(store, mark, "docs.job.retry", object_id=jv["id"], actor=("machine", f"api:{kid}"))
    assert row["meta"] == {"project_id": pid, "run_no": 2, "items": 3, "include_skipped": False, "failed_only": False,
                           "requested_by": f"api:{kid}", "idempotency_key": True}
    assert row["meta"]["items"] >= 1 and row["request_id"]

    # ---- the same key again: a replay, no second row
    mark = _mark(store)
    replay = c.post(f"/api/v1/jobs/{jv['id']}/retry", json={}, headers=k)
    assert replay.status_code == 200, (replay.status_code, replay.text[:300])
    assert replay.json()["idempotent_replay"] is True and replay.json()["run_no"] == 2
    assert replay.headers.get("Idempotent-Replayed") == "true"
    assert _rows(store, mark) == []
    assert _count(store, "docs.job.retry", jv["id"]) == 1
    assert [r["trigger"] for r in jobs.runs(jv["id"])] == ["create", "retry"]

    # ---- the browser route's body key behaves the same for a second job (operator actor)
    j = _queued_browser_job(api, pid)
    api.ok(c.post(f"/api/jobs/{j['id']}/cancel"))
    mark = _mark(store)
    first = api.ok(c.post(f"/api/jobs/{j['id']}/retry", json={"idempotency_key": "audit-ui-retry"}))
    assert first["run_no"] == 2 and not first.get("idempotent_replay")
    row = _one(store, mark, "docs.job.retry", object_id=j["id"], actor=("system", "operator"))
    assert row["meta"] == {"project_id": pid, "run_no": 2, "items": 3, "include_skipped": False, "failed_only": False,
                           "requested_by": "ui", "idempotency_key": True}
    mark = _mark(store)
    assert api.ok(c.post(f"/api/jobs/{j['id']}/retry", json={"idempotency_key": "audit-ui-retry"}))["idempotent_replay"] is True
    assert _rows(store, mark) == []


# ------------------------------------------------------------------ atomicity
def test_a_failed_audit_undoes_a_cancel(api, fixtures, monkeypatch):
    from synwevo_core import auth

    c = api.c
    pid, store, jobs, h, kid = _setup(api, fixtures)
    j = _queued_browser_job(api, pid)
    events_before = _job_events(store, j["id"])
    assert events_before == ["job.created"], events_before

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")

    original = auth.audit_event
    mark = _mark(store)
    monkeypatch.setattr(auth, "audit_event", fail)
    r = c.post(f"/api/jobs/{j['id']}/cancel")
    assert r.status_code >= 500 and "synthetic" not in r.text, (r.status_code, r.text[:300])
    assert r.json()["error"] == "internal_error" and r.json().get("request_id"), r.text[:300]
    monkeypatch.setattr(auth, "audit_event", original)

    # the cancel was undone with its audit row: still queued, nothing requested, no event, no row
    live = api.ok(c.get(f"/api/jobs/{j['id']}"))
    assert live["status"] == "queued" and live["cancel_requested"] is False and live["finished_at"] is None, live
    assert _rows(store, mark) == [] and _count(store, "docs.job.cancel", j["id"]) == 0
    assert _job_events(store, j["id"]) == events_before
    assert {i["status"] for i in jobs.items(j["id"])} == {"pending"}
    assert store.one("SELECT status FROM job_runs WHERE job_id=? AND run_no=1", (j["id"],))["status"] == "queued"

    # with the writer back, the same cancel goes through once, with its row
    out = api.ok(c.post(f"/api/jobs/{j['id']}/cancel"))
    assert out["status"] == "cancelled" and out["cancel"]["accepted"] is True
    row = _one(store, mark, "docs.job.cancel", object_id=j["id"], actor=("system", "operator"))
    assert row["meta"]["changed"] == ["status"]
    assert _job_events(store, j["id"]) == ["job.created", "job.cancelled"]


# ------------------------------------------------------------------ idempotency conflict
def test_an_idempotent_create_conflict_writes_no_row(api, fixtures):
    c = api.c
    pid, store, jobs, h, kid = _setup(api, fixtures)

    # ---- v1: one key, a different body → 409 idempotency_conflict and no job, no row
    k = {**h, "Idempotency-Key": "audit-job-conflict-1"}
    jv = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=k), 202)
    assert _count(store, "docs.job.create", jv["id"]) == 1
    mark = _mark(store)
    r = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"], "naming": "{序号}-x"}, headers=k)
    assert r.status_code == 409, (r.status_code, r.text[:300])
    err = r.json()["error"]
    assert err["code"] == "idempotency_conflict" and err["details"] == {"job_id": jv["id"]}, err
    assert _rows(store, mark) == []
    assert store.one("SELECT COUNT(*) AS n FROM jobs WHERE project_id=?", (pid,))["n"] == 1

    # ---- browser: the body `request_id` key, same rule
    j = api.ok(c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"], "request_id": "audit-ui-conflict"}))
    assert _count(store, "docs.job.create", j["id"]) == 1
    mark = _mark(store)
    r = c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"], "request_id": "audit-ui-conflict", "naming": "{序号}-x"})
    assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict", r.text[:300]
    assert _rows(store, mark) == []
    assert store.one("SELECT COUNT(*) AS n FROM jobs WHERE project_id=?", (pid,))["n"] == 2
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.job.create' AND workspace_id='default'")["n"] == 2
