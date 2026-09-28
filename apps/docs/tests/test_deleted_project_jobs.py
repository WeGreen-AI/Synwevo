"""Q16: a deleted project's jobs and outputs go with it, as its sources already did. Before this,
a job id kept anywhere — an inbox notification ("批量生成已完成"), a Synwevo Message card, a
bookmark — still opened the job after its project was deleted: its items, runs, events, report
and ZIP, every output file (also by blob id), and a re-run request (which then halted).
TEST FIXTURES: the synthetic workbook and template of test_docx_nested."""
from __future__ import annotations

from .test_docx_nested import _map, _setup


def test_a_deleted_projects_jobs_and_outputs_are_not_found(api, tmp_path):
    pid, tid, blk, rep, cust = _setup(api, tmp_path)
    _map(api, pid, blk, rep, cust)
    job = api.job(pid, naming="{客户}")
    assert job["status"] == "completed"
    jid = job["id"]
    art = api.items(jid)[0]["artifacts"][0]
    c = api.c
    paths = [f"/api/jobs/{jid}", f"/api/jobs/{jid}/items", f"/api/jobs/{jid}/runs", f"/api/jobs/{jid}/events",
             f"/api/jobs/{jid}/zip", f"/api/jobs/{jid}/report.csv", f"/api/artifacts/{art['id']}"]
    assert all(c.get(p).status_code == 200 for p in paths)
    from synwevo_docs import authz
    store = c.app_state.app.store
    assert authz.blob_readable(store, art["sha256"], "default")

    api.ok(c.delete(f"/api/projects/{pid}"))

    for p in paths:
        r = c.get(p)
        assert r.status_code == 404 and r.json()["error"] == "not_found", (p, r.status_code, r.text[:200])
    assert c.post(f"/api/jobs/{jid}/retry", json={}).status_code == 404
    assert c.post(f"/api/jobs/{jid}/cancel", json={}).status_code == 404
    assert not authz.blob_readable(store, art["sha256"], "default"), "the output's bytes are not served by id either"
    # the rows stay (deletion is a mark; reclaiming bytes is docs/platform/blob-reclamation.md)
    assert store.one("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=?", (jid,))["n"] >= 1
