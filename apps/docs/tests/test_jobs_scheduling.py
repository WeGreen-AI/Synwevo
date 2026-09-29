"""Scheduling: planning large batches outside the request, fairness across workspaces, and
per-workspace admission.

Most tests drive a JobService with ``start=False`` by hand (claim, slice, slice …) so the order
of work is observable and nothing depends on timing. The fixture app's own runner is stopped
first, so it cannot take the jobs these tests are stepping through.
"""
from __future__ import annotations

import threading
import time

import pytest

from synwevo_core.errors import ServiceError
from synwevo_docs.engine import records as R
from synwevo_docs.engine import tabular
from synwevo_docs.engine import values as V
from synwevo_docs.engine.plan import build_plan
from synwevo_docs.engine.resolve import SourceDoc
from synwevo_docs import jobs as J
from synwevo_docs.jobs import JobService


def _project(api, fixtures, rows=12, bad=False, name="调度"):
    pid = api.project(name)
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": rows}})
    header = "不存在的列" if bad else "姓名"
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": header}], "target": {"binding": s["binding"]}}])
    return pid


def _one_step_slices(monkeypatch):
    """TEST FIXTURE: every slice does the smallest unit of work (one plan batch or one sample),
    so progress between slices is observable."""
    monkeypatch.setattr(J, "slice_seconds", lambda: 0.0)


def _manual(api) -> JobService:
    """Stop the fixture's runner and return a service stepped by hand (TEST FIXTURE)."""
    api.c.app_state.jobs.stop()
    time.sleep(0.2)
    return JobService(api.c.app_state.app, start=False)


def _drain(svc: JobService, jid: str, limit: int = 500) -> None:
    for _ in range(limit):
        if svc.get(jid, "default")["status"] not in ("queued", "running", "cancelling"):
            return
        svc._fill()
        lease = svc._next_lease()
        if lease is None:
            continue
        if svc._slice(lease):
            svc._held.pop(lease.jid, None)
    raise AssertionError("job did not finish")


# ------------------------------------------------------------------ planning
def test_large_batches_are_planned_by_the_runner_with_progress(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_SYNC_PLAN_MAX", "5")
    monkeypatch.setenv("SYNWEVO_DOCS_PLAN_BATCH", "4")
    _one_step_slices(monkeypatch)
    pid = _project(api, fixtures, rows=12)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"], "naming": "合同"})
    # the request returned without planning a single sample
    assert j["phase"] == "planning" and j["status"] == "queued"
    assert j["counts"]["total"] == 12 and j["counts"]["planned"] == 0 and svc.count_items(j["id"]) == 0
    assert svc._claim(j["id"]) == j["id"]
    lease = svc._held[j["id"]]
    seen = []
    while svc.get(j["id"])["phase"] == "planning":
        assert svc._slice(lease) is False
        seen.append(svc.get(j["id"])["counts"]["planned"])
    assert seen == [4, 8, 12], seen                                     # real progress, batch by batch
    got = svc.get(j["id"])
    assert got["status"] == "running" and got["phase"] == "generating"          # no flapping back to queued
    _drain(svc, j["id"])
    done = svc.get(j["id"])
    assert done["status"] == "completed" and done["counts"]["success"] == 12 and done["counts"]["total"] == 12
    names = sorted(i["file_base"] for i in svc.items(j["id"], limit=100))
    assert names == sorted(["合同"] + [f"合同 ({k})" for k in range(2, 13)])      # de-duplicated across batches
    types = [e["type"] for e in svc.events(j["id"])]
    assert types.count("job.started") == 1 and "job.planned" in types and types[-1] == "job.completed"


def test_planning_resumes_after_a_crash_without_duplicating_samples(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_SYNC_PLAN_MAX", "3")
    monkeypatch.setenv("SYNWEVO_DOCS_PLAN_BATCH", "4")
    _one_step_slices(monkeypatch)
    pid = _project(api, fixtures, rows=12)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"]})
    svc._claim(j["id"])
    svc._slice(svc._held[j["id"]])
    assert svc.count_items(j["id"]) == 4
    # the worker dies: its lease is left behind, a new worker recovers and continues
    store = api.c.app_state.app.store
    store.execute("UPDATE jobs SET lease_owner='deadhost:999999:abc', lease_until=? WHERE id=?", (time.time() - 60, j["id"]))
    other = JobService(api.c.app_state.app, start=False)
    assert svc.get(j["id"])["status"] == "queued"
    _drain(other, j["id"])
    done = other.get(j["id"])
    assert done["status"] == "completed" and done["counts"]["success"] == 12
    seqs = [r["seq"] for r in store.all("SELECT seq FROM job_items WHERE job_id=? ORDER BY seq", (j["id"],))]
    assert seqs == list(range(12))                                          # every sample exactly once


def test_async_planning_with_block_policy_fails_with_located_issues(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_SYNC_PLAN_MAX", "2")
    pid = _project(api, fixtures, rows=6, bad=True)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"], "error_policy": "block"})     # accepted: planning is deferred
    _drain(svc, j["id"])
    done = svc.get(j["id"])
    assert done["status"] == "failed" and done["error_code"] == "has_errors" and "6 个样本存在错误" in done["error"]
    skipped = svc.items(j["id"], status="skipped")
    assert len(skipped) == 6 and all(i["issues"][0]["code"] == "missing_column" for i in skipped)
    assert not api.c.app_state.app.store.one("SELECT 1 AS x FROM artifacts WHERE job_id=?", (j["id"],))
    failed = [e for e in svc.events(j["id"]) if e["type"] == "job.failed"][0]["data"]
    assert failed["error_code"] == "has_errors"
    issues = svc.preflight_issues(pid, level="error")
    assert issues["total"] == 6 and {r["code"] for r in issues["items"]} == {"missing_column"}
    # a has_errors job cannot be retried into generating anyway
    with pytest.raises(ServiceError) as e:
        svc.retry(j["id"])
    assert e.value.code == "has_errors"


def test_cancel_during_planning(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_SYNC_PLAN_MAX", "2")
    monkeypatch.setenv("SYNWEVO_DOCS_PLAN_BATCH", "3")
    _one_step_slices(monkeypatch)
    pid = _project(api, fixtures, rows=12)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"]})
    svc._claim(j["id"])
    svc._slice(svc._held[j["id"]])
    assert svc.cancel(j["id"])["status"] == "cancelling"
    assert svc._slice(svc._held[j["id"]]) is True
    done = svc.get(j["id"])
    assert done["status"] == "cancelled" and done["counts"].get("success", 0) == 0
    assert done["counts"]["total"] == 12 and done["counts"]["planned"] == 3 and done["counts"]["unplanned"] == 9
    assert done["counts"]["cancelled"] == 3


# ------------------------------------------------------------------ fairness
def test_one_workspace_s_large_batch_does_not_starve_another(api, fixtures, monkeypatch):
    _one_step_slices(monkeypatch)
    big = _project(api, fixtures, rows=30, name="大批量")
    small = _project(api, fixtures, rows=3, name="小批量")
    svc = _manual(api)
    store = api.c.app_state.app.store
    a = svc.create(big, {"formats": ["docx"]})
    b = svc.create(small, {"formats": ["docx"]})
    # the small job belongs to another workspace (only the job's tenant matters to the scheduler)
    store.execute("UPDATE jobs SET tenant='ws_other' WHERE id=?", (b["id"],))
    order = []
    for _ in range(400):
        if svc.get(b["id"], "ws_other")["status"] not in ("queued", "running"):
            break
        svc._fill()
        lease = svc._next_lease()
        order.append(lease.jid)
        if svc._slice(lease):
            svc._held.pop(lease.jid, None)
    assert svc.get(b["id"], "ws_other")["status"] == "completed"
    assert svc.get(a["id"])["status"] == "running", "大任务应当仍在进行，而不是先跑完"
    # workspaces take turns: the big job never gets two slices in a row while the small one waits
    first_b = order.index(b["id"])
    assert first_b <= 1 and all(x != y for x, y in zip(order, order[1:])), order
    _drain(svc, a["id"])
    assert svc.get(a["id"])["counts"]["success"] == 30
    for jid, t in ((a["id"], "default"), (b["id"], "ws_other")):
        types = [e["type"] for e in svc.events(jid)]
        assert types.count("job.started") == 1 and "job.recovered" not in types      # no status flapping


def test_the_real_runner_interleaves_workspaces(api, fixtures, monkeypatch):
    """The same property with the background runner thread, measured by completion order."""
    monkeypatch.setenv("SYNWEVO_DOCS_JOB_SLICE_S", "0.05")
    big = _project(api, fixtures, rows=40, name="大批量")
    small = _project(api, fixtures, rows=2, name="小批量")
    svc = _manual(api)
    store = api.c.app_state.app.store
    a = svc.create(big, {"formats": ["docx"]})
    b = svc.create(small, {"formats": ["docx"]})
    store.execute("UPDATE jobs SET tenant='ws_other' WHERE id=?", (b["id"],))
    runner = JobService(api.c.app_state.app, start=True)
    try:
        for _ in range(600):
            if svc.get(b["id"], "ws_other")["status"] == "completed":
                break
            time.sleep(0.05)
        assert svc.get(b["id"], "ws_other")["status"] == "completed"
        big_now = svc.get(a["id"])
        assert big_now["status"] == "running" and big_now["counts"].get("success", 0) < 40
    finally:
        runner.stop()


# ------------------------------------------------------------------ admission
def test_workspace_active_job_limit(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_MAX_ACTIVE_JOBS", "2")
    pid = _project(api, fixtures, rows=2)
    _manual(api)                                                       # nothing runs: jobs stay queued
    one = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]})
    assert r.status_code == 429 and r.json()["error"] == "queue_full" and r.json()["detail"]["retry_after"] > 0
    use = api.c.app_state.jobs.usage("default")
    assert use["active_jobs"] == 2 and use["queued_jobs"] == 2 and use["limits"]["max_active_jobs"] == 2 and use["pending_items"] == 4
    # another workspace is not affected by this one's queue
    api.c.app_state.app.store.execute("UPDATE jobs SET tenant='ws_other' WHERE id=?", (one["id"],))
    api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    api.c.app_state.app.store.execute("UPDATE jobs SET tenant='default' WHERE id=?", (one["id"],))
    assert api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}).status_code == 429


def test_a_workspace_policy_replaces_the_deployment_limit(api, fixtures, monkeypatch):
    """API-1: the capacity an administrator sets for a workspace is what admission enforces, on the
    browser route as on the machine API, and the refusal says which layer decided it."""
    from synwevo_core.api_quotas import Policies
    monkeypatch.setenv("SYNWEVO_DOCS_MAX_ACTIVE_JOBS", "5")
    store = api.c.app_state.app.store
    if not store.one("SELECT 1 FROM workspaces WHERE id='default'"):
        store.insert("workspaces", {"id": "default", "name": "fixture workspace", "kind": "personal",   # fixture row
                                    "status": "active", "created_at": 0, "updated_at": 0})
    Policies(store).change("workspace", "default", {"active_jobs": 1, "base_version": 0},
                           actor_id="u_fixture", reason="fixture: one job at a time")
    pid = _project(api, fixtures, rows=2)
    _manual(api)
    api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]})
    assert r.status_code == 429 and r.json()["error"] == "queue_full"
    assert r.json()["detail"]["limit"] == 1 and r.json()["detail"]["policy_source"] == "workspace"
    assert api.c.app_state.jobs.usage("default")["limits"]["max_active_jobs"] == 1
    # Clearing the override falls back to the deployment default (5): the next job is admitted.
    Policies(store).change("workspace", "default", {"active_jobs": None, "base_version": 1},
                           actor_id="u_fixture", reason="fixture: back to the default")
    api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))


def test_the_limit_cannot_be_raced(api, fixtures, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_MAX_ACTIVE_JOBS", "3")
    pid = _project(api, fixtures, rows=2)
    _manual(api)
    codes = []

    def submit():
        codes.append(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}).status_code)

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert codes.count(200) == 3 and codes.count(429) == 5, codes
    assert api.c.app_state.app.store.one("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'")["n"] == 3


def test_retry_counts_against_the_limit(api, fixtures, monkeypatch):
    pid = _project(api, fixtures, rows=2)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"]})
    svc.cancel(j["id"])
    monkeypatch.setenv("SYNWEVO_DOCS_MAX_ACTIVE_JOBS", "1")
    svc.create(pid, {"formats": ["docx"]})
    with pytest.raises(ServiceError) as e:
        svc.retry(j["id"])
    assert e.value.status == 429 and e.value.code == "queue_full"


# ------------------------------------------------------------------ plan cost
def test_planning_is_linear_in_the_number_of_samples():
    """F156: every record rebuilt the whole table view, so planning was quadratic (measured 98 s
    for 20,000 samples). The view is now built once per source document."""
    def src_with(n):
        rows = [[V.text(f"H{c}") for c in range(10)]] + [[V.text(f"r{r}c{c}") for c in range(10)] for r in range(n)]
        src = SourceDoc("f1", "a.xlsx", "xlsx", "table", workbook_json={"sheets": []})
        src._wb = tabular.Workbook([tabular.Sheet("S", 0, rows)])
        return src

    rule = {"mode": "per_row", "table": {"sheet": "S", "header_row": 0}}
    maps = [{"id": "m1", "no": 1, "type": "single", "enabled": True, "sources": [{"kind": "column", "header": "H3"}],
             "target": {"binding": "s1"}}]
    slots = [{"id": "s1", "name": "x", "kind": "text"}]
    src = src_with(20000)
    recs = R.build_records([src], rule)["records"]
    t = time.perf_counter()
    plans = [build_plan(rec, rule, {"f1": src}, maps, slots) for rec in recs]
    took = time.perf_counter() - t
    assert len(plans) == 20000 and not any(p.blocking for p in plans)
    assert plans[19999].values["s1"]["value"].text == "r19999c3"
    assert took < 10, f"20,000 个样本的计划耗时 {took:.1f} 秒"
