"""Job semantics: idempotent submission, atomic publication, lease recovery, cancel/complete
arbitration, retry runs and the event stream.  These pin DW-001…DW-008."""
import threading
import time

import pytest

from docweave.jobs import JobService
from docweave.services import ServiceError


def _project(api, fixtures, rows=6, bad=False):
    pid = api.project("任务语义")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": rows}})
    header = "不存在的列" if bad else "姓名"
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": header}], "target": {"binding": s["binding"]}}])
    return pid


def test_idempotent_submission(api, fixtures):
    pid = _project(api, fixtures)
    body = {"formats": ["docx"], "naming": "{序号}"}
    a = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={**body, "request_id": "k1"}))
    b = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={**body, "request_id": "k1"}))
    assert a["id"] == b["id"] and b.get("idempotent_replay")
    assert len(api.ok(api.c.get(f"/api/projects/{pid}/jobs"))) == 1
    r = api.c.post(f"/api/projects/{pid}/jobs", json={**body, "naming": "{姓名}", "request_id": "k1"})
    assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict"
    # the key survives a restart of the service (it is stored, not kept in memory)
    api.c.app_state.jobs.stop()
    fresh = JobService(api.c.app_state.app, start=False)
    same = fresh.create(pid, {**body, "request_id": "k1"})
    assert same["id"] == a["id"] and same.get("idempotent_replay")


def test_concurrent_same_key_creates_one_job(api, fixtures):
    pid = _project(api, fixtures)
    out, errs = [], []

    def submit():
        try:
            r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"], "request_id": "race"})
            (out if r.status_code < 400 else errs).append(r.json())
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errs, errs
    assert len({j["id"] for j in out}) == 1
    assert len(api.ok(api.c.get(f"/api/projects/{pid}/jobs"))) == 1


def test_all_items_failed_is_reported_as_failed(api, fixtures):
    pid = _project(api, fixtures, bad=True)
    job = api.job(pid, error_policy="skip")
    assert job["status"] == "failed" and job["counts"]["skipped"] == 6 and not job["counts"].get("success")
    types = [e["type"] for e in api.ok(api.c.get(f"/api/jobs/{job['id']}/events"))]
    assert types[0] == "job.created" and types[-1] == "job.failed" and "job.started" in types


def test_cancel_is_arbitrated_against_completion(api, fixtures):
    pid = _project(api, fixtures, rows=40)
    j = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    c1 = api.ok(api.c.post(f"/api/jobs/{j['id']}/cancel"))
    assert c1["cancel"]["accepted"] and c1["status"] in ("cancelling", "cancelled")
    c2 = api.ok(api.c.post(f"/api/jobs/{j['id']}/cancel"))          # repeating is stable
    assert c2["cancel"]["accepted"] in (True, False)
    for _ in range(600):
        j = api.ok(api.c.get(f"/api/jobs/{j['id']}"))
        if j["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(0.05)
    assert j["status"] in ("cancelled", "completed")               # completed only if it had finished before the cancel
    counts = j["counts"]
    assert counts.get("success", 0) + counts.get("cancelled", 0) + counts.get("failed", 0) == counts["total"]
    assert counts["files"] == counts.get("success", 0)             # every finished sample has exactly its output
    after = api.ok(api.c.post(f"/api/jobs/{j['id']}/cancel"))      # cancelling a terminal job changes nothing
    assert after["cancel"] == {"accepted": False, "reason": "already_terminal"} and after["status"] == j["status"]


def test_retry_is_a_recorded_run_and_is_idempotent(api, fixtures, monkeypatch):
    pid = _project(api, fixtures, rows=4)
    from docweave.engine import generate as G
    real = G.fill_checked
    state = {"fail": True}

    def flaky(fmt, work, slots, plan, options, mode, pages):
        if state["fail"] and plan.record["index"] == 2:
            raise RuntimeError("模拟的临时故障")
        return real(fmt, work, slots, plan, options, mode, pages)

    monkeypatch.setattr(G, "fill_checked", flaky)
    job = api.job(pid)
    assert job["status"] == "completed_with_errors" and job["counts"]["failed"] == 1
    state["fail"] = False
    r1 = api.ok(api.c.post(f"/api/jobs/{job['id']}/retry", json={"idempotency_key": "r1"}))
    assert r1["run_no"] == 2
    r2 = api.ok(api.c.post(f"/api/jobs/{job['id']}/retry", json={"idempotency_key": "r1"}))
    assert r2.get("idempotent_replay") and r2["run_no"] == 2       # same key: no second retry run
    for _ in range(600):
        j = api.ok(api.c.get(f"/api/jobs/{job['id']}"))
        if j["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(0.05)
    assert j["status"] == "completed" and j["counts"]["success"] == 4
    runs = api.ok(api.c.get(f"/api/jobs/{job['id']}/runs"))
    assert [r["trigger"] for r in runs] == ["create", "retry"] and runs[1]["items"] == 1 and runs[1]["status"] == "completed"
    items = api.items(job["id"])
    assert all(len(i["artifacts"]) == 1 for i in items)            # retried item did not duplicate outputs
    assert api.c.post(f"/api/jobs/{job['id']}/retry", json={}).status_code == 409   # nothing left to retry


def test_lease_recovery_after_a_dead_worker(api, fixtures):
    pid = _project(api, fixtures, rows=5)
    jobs = api.c.app_state.jobs
    jobs.stop()                                                    # simulate: this worker is gone
    time.sleep(0.2)
    j = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    store = api.c.app_state.app.store
    store.execute("UPDATE jobs SET status='running', lease_owner=?, lease_until=? WHERE id=?",
                  ("deadhost:999999:abc", time.time() - 120, j["id"]))
    store.execute("UPDATE job_items SET status='running' WHERE job_id=? AND seq=0", (j["id"],))
    fresh = JobService(api.c.app_state.app)                        # a new worker takes over
    try:
        for _ in range(600):
            cur = fresh.get(j["id"])
            if cur["status"] not in ("queued", "running", "cancelling"):
                break
            time.sleep(0.05)
        assert cur["status"] == "completed" and cur["counts"]["success"] == 5
        assert cur["counts"]["files"] == 5                          # the interrupted item ran once, not twice
        assert any(e["type"] == "job.recovered" for e in fresh.events(j["id"]))
    finally:
        fresh.stop()


def test_publication_is_all_or_nothing(api, fixtures):
    """An item's outputs and its success status are written in one transaction: a second
    publication attempt for an item that is no longer 'running' writes nothing."""
    pid = _project(api, fixtures, rows=2)
    job = api.job(pid)
    jobs = api.c.app_state.jobs
    item = api.items(job["id"])[0]
    assert item["status"] == "success" and len(item["artifacts"]) == 1
    snap = api.c.app_state.app.store.one("SELECT snapshot, options FROM jobs WHERE id=?", (job["id"],))
    tpl = snap["snapshot"]["template"]
    srcs = {s["id"]: api.c.app_state.app._doc_from_row({**s, "family": s["family"]}) for s in snap["snapshot"]["sources"]}
    full = api.c.app_state.app.store.one("SELECT * FROM job_items WHERE id=?", (item["id"],))
    jobs._process_item(job["id"], full, 2, 1, snap["snapshot"], snap["options"], srcs, tpl,
                       api.c.app_state.app.store.get_blob(tpl["working_blob"]), tpl["slots"], "docx", "off", None)
    assert len(api.items(job["id"])[0]["artifacts"]) == 1          # still one file, no duplicate


def test_job_errors_carry_stage_and_sample(api, fixtures):
    pid = _project(api, fixtures, bad=True)
    job = api.job(pid, error_policy="skip")
    it = api.items(job["id"])[0]
    assert it["status"] == "skipped" and it["issues"][0]["code"] == "missing_column"
    assert it["label"] and "不存在的列" in it["issues"][0]["message"]


def test_zip_contains_only_published_outputs(api, fixtures):
    pid = _project(api, fixtures, rows=3)
    job = api.job(pid)
    import io
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(api.ok_bytes(api.c.get(f"/api/jobs/{job['id']}/zip"))))
    names = [n for n in z.namelist() if n.endswith(".docx")]
    assert len(names) == 3 and len(set(names)) == 3 and "生成清单.csv" in z.namelist()


def test_service_error_shape(api, fixtures):
    r = api.c.get("/api/jobs/j_doesnotexist")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    with pytest.raises(ServiceError):
        api.c.app_state.jobs.get("j_nope", tenant="default")
