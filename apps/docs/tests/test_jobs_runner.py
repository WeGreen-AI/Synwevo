"""Runner robustness: leases a crashed runner thread left behind, stale workers, PID
namespaces, and inputs that are not ready."""
from __future__ import annotations

import socket
import time

import pytest

from synwevo_core.errors import ServiceError
from synwevo_docs.jobs import JobService


def _project(api, fixtures, rows=3):
    pid = api.project("运行器")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": rows}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid


def _manual(api) -> JobService:
    api.c.app_state.jobs.stop()
    time.sleep(0.2)
    return JobService(api.c.app_state.app, start=False)


def test_a_dead_runner_thread_gives_back_what_it_held(api, fixtures):
    """F136: the heartbeat renewed every lease this worker id owned, so a job held by a runner
    thread that crashed stayed 'running' for as long as the process lived."""
    pid = _project(api, fixtures)
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"]})
    assert svc._claim(j["id"]) == j["id"]
    svc._release_held()                                 # what the supervisor does before restarting the runner
    assert svc.get(j["id"])["status"] == "queued" and not svc.held()
    assert "job.recovered" in [e["type"] for e in svc.events(j["id"])]
    # and a lease of ours that the runner no longer tracks is recovered once it runs out
    assert svc._claim(j["id"]) == j["id"]
    svc._held.clear()
    store = api.c.app_state.app.store
    store.execute("UPDATE jobs SET lease_until=? WHERE id=?", (time.time() - 1, j["id"]))
    assert svc.recover() == [j["id"]] and svc.get(j["id"])["status"] == "queued"


def test_a_worker_that_lost_its_lease_cannot_fail_an_item(api, fixtures):
    """F297/F144: failure writes and item claims were not tied to the lease, so a paused worker
    could mark failed an item another worker was processing, and that worker's good result was
    then refused at publication."""
    pid = _project(api, fixtures)
    a = _manual(api)
    b = JobService(api.c.app_state.app, start=False)
    j = a.create(pid, {"formats": ["docx"]})
    a._claim(j["id"])
    store = api.c.app_state.app.store
    item = store.one("SELECT id FROM job_items WHERE job_id=? ORDER BY seq LIMIT 1", (j["id"],))
    store.execute("UPDATE job_items SET status='running' WHERE id=?", (item["id"],))
    store.execute("UPDATE jobs SET lease_owner=? WHERE id=?", (b.worker_id, j["id"]))      # b took over
    a._item_done(item["id"], "failed", [{"level": "error", "code": "exception", "message": "迟到的失败"}])
    assert store.one("SELECT status FROM job_items WHERE id=?", (item["id"],))["status"] == "running"
    b._item_done(item["id"], "failed", [{"level": "error", "code": "exception", "message": "持有者的失败"}])
    assert store.one("SELECT status FROM job_items WHERE id=?", (item["id"],))["status"] == "failed"


def test_a_pid_is_only_evidence_inside_its_own_namespace(api):
    """F145: two containers sharing a hostname but not a PID namespace saw each other's live
    workers as dead and took their leases at once."""
    svc = _manual(api)
    host = socket.gethostname()
    far_future = time.time() + 600
    dead_pid = 999999
    same_ns = {"id": "j", "lease_owner": f"{host}:{dead_pid}:abc:{svc._ns}", "lease_until": far_future}
    other_ns = {"id": "j", "lease_owner": f"{host}:{dead_pid}:abc:other-namespace", "lease_until": far_future}
    legacy = {"id": "j", "lease_owner": f"{host}:{dead_pid}:abc", "lease_until": far_future}
    assert svc._lease_expired(same_ns, time.time()) is True           # provably gone
    assert svc._lease_expired(other_ns, time.time()) is False         # cannot tell: wait for the lease
    assert svc._lease_expired(legacy, time.time()) is False
    assert svc._lease_expired({**other_ns, "lease_until": time.time() - 1}, time.time()) is True


def test_a_job_is_not_created_while_files_are_still_parsing(api, fixtures):
    """F134: a job over whichever files happened to be parsed completed "successfully" with the
    others' documents silently missing."""
    pid = _project(api, fixtures)
    store = api.c.app_state.app.store
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    store.execute("UPDATE source_files SET status='parsing' WHERE id=?", (sid,))
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]})
    assert r.status_code == 409 and r.json()["error"] == "sources_not_ready" and r.json()["detail"]["files"] == ["测试_客户100.xlsx"]
    # a restart finds it and parses it again, instead of leaving it 'parsing' forever (F135)
    assert api.c.app_state.app.resume_parsing() == 1
    api.wait_parsed(pid)
    assert api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["status"] == "ready"
    assert api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}).status_code == 200


def test_unreadable_files_are_named_in_the_job(api, fixtures):
    pid = _project(api, fixtures)
    api.upload(pid, fixtures / "测试_损坏文件.xlsx")
    svc = _manual(api)
    j = svc.create(pid, {"formats": ["docx"]})
    snap = api.c.app_state.app.store.one("SELECT snapshot FROM jobs WHERE id=?", (j["id"],))["snapshot"]
    assert [f["filename"] for f in snap["excluded_files"]] == ["测试_损坏文件.xlsx"]


def test_an_invalid_stored_layout_is_refused_not_downgraded(api, fixtures):
    """F142: a project default layout 'fitt' written before validation existed became 'off'."""
    pid = _project(api, fixtures)
    svc = _manual(api)
    api.c.app_state.app.store.execute("UPDATE projects SET output=? WHERE id=?", ('{"naming": "{序号}", "layout": "fitt"}', pid))
    with pytest.raises(ServiceError) as e:
        svc.create(pid, {"formats": ["docx"]})
    assert e.value.code == "bad_layout" and e.value.detail["field"] == "output.layout"
