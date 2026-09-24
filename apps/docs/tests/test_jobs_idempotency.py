"""Idempotency of job creation and retry: concurrent retries, what the fingerprint covers,
and whose namespace a key lives in (F298, F308/F153)."""
from __future__ import annotations

import threading
import time

from synwevo_core import authority
from synwevo_docs.engine import generate as G
from synwevo_docs.jobs import JobService, request_hash


def _project(api, fixtures, rows=3):
    pid = api.project("幂等")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": rows}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid


def _wait(api, jid):
    for _ in range(600):
        j = api.ok(api.c.get(f"/api/jobs/{jid}"))
        if j["status"] not in ("queued", "running", "cancelling"):
            return j
        time.sleep(0.05)
    raise AssertionError("job timeout")


def _key(kid, lineage=None):
    return authority.describe({"kind": "api_key", "key_id": kid, "lineage": lineage or kid, "tenant": "default"})


def test_concurrent_retries_with_one_key_replay_instead_of_409(api, fixtures, monkeypatch):
    pid = _project(api, fixtures)
    real = G.fill_checked

    def flaky(fmt, work, slots, plan, options, mode, pages):
        if plan.record["index"] == 1:
            raise RuntimeError("模拟的临时故障")
        return real(fmt, work, slots, plan, options, mode, pages)

    monkeypatch.setattr(G, "fill_checked", flaky)
    job = api.job(pid)
    assert job["status"] == "completed_with_errors"
    out = []

    def retry():
        r = api.c.post(f"/api/jobs/{job['id']}/retry", json={"idempotency_key": "same-retry"})
        out.append((r.status_code, r.json()))

    threads = [threading.Thread(target=retry) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert [c for c, _ in out] == [200] * 5, out
    assert sum(1 for _, b in out if not b.get("idempotent_replay")) == 1
    assert {b["run_no"] for _, b in out} == {2}
    _wait(api, job["id"])
    assert [r["trigger"] for r in api.ok(api.c.get(f"/api/jobs/{job['id']}/runs"))] == ["create", "retry"]


def test_a_replay_is_not_refused_after_the_project_defaults_change(api, fixtures):
    pid = _project(api, fixtures)
    api.c.app_state.jobs.stop()
    body = {"formats": ["docx"], "request_id": "k-defaults"}
    a = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json=body))
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"output": {"naming": "{客户名称}", "layout": "warn"}}))
    b = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json=body))
    assert b["id"] == a["id"] and b["idempotent_replay"]
    r = api.c.post(f"/api/projects/{pid}/jobs", json={**body, "naming": "{序号}-x"})
    assert r.status_code == 409 and r.json()["error"] == "idempotency_conflict"


def test_keys_belong_to_the_caller_not_to_the_whole_workspace(api, fixtures):
    pid = _project(api, fixtures)
    api.c.app_state.jobs.stop()
    svc = JobService(api.c.app_state.app, start=False)
    one = svc.create(pid, {"formats": ["docx"]}, tenant="default", idem_key="shared", principal=_key("k1"))
    two = svc.create(pid, {"formats": ["docx"]}, tenant="default", idem_key="shared", principal=_key("k2"))
    assert one["id"] != two["id"]                                         # two integrations, one key string
    again = svc.create(pid, {"formats": ["docx"]}, tenant="default", idem_key="shared", principal=_key("k1"))
    assert again["id"] == one["id"] and again["idempotent_replay"]
    rotated = svc.create(pid, {"formats": ["docx"]}, tenant="default", idem_key="shared", principal=_key("k1b", lineage="k1"))
    assert rotated["id"] == one["id"]                                     # a rotated key replays its predecessor's work


def test_a_key_stored_before_the_upgrade_still_replays(api, fixtures):
    """A row written by the previous version (workspace-wide scope, resolved-defaults hash, no
    principal) must replay, not create a second job, when the client retries across the upgrade."""
    pid = _project(api, fixtures)
    api.c.app_state.jobs.stop()
    svc = JobService(api.c.app_state.app, start=False)
    existing = svc.create(pid, {"formats": ["docx"]}, tenant="default")
    old_hash = request_hash({"project": pid, "formats": ["docx"], "error_policy": "block", "naming": "{序号}",
                             "keep_fields": False, "layout": "off"})
    api.c.app_state.app.store.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, job_id, created_at) VALUES (?,?,?,?,?,?)",
                                      ("default", f"job:{pid}", "legacy-key", old_hash, existing["id"], time.time()))
    replay = svc.create(pid, {"formats": ["docx"]}, tenant="default", idem_key="legacy-key", principal=_key("k9"))
    assert replay["id"] == existing["id"] and replay["idempotent_replay"]
