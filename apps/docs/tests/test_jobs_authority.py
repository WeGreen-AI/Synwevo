"""A job stops when whoever asked for it loses the right to have it done (F131/F337).

The submitting principal is stored with the job and with each run; the runner re-checks it
before a run starts, between samples and immediately before publishing a sample's files. A key
revoked while its batch is queued or running must not keep delivering documents.

Driven by hand with ``start=False`` so the moment of revocation is exact.
"""
from __future__ import annotations

import time

from synwevo_core import authority
from synwevo_core.auth import Auth
from synwevo_docs.engine import generate as G
from synwevo_docs.jobs import JobService


def _project(api, fixtures, rows=5):
    pid = api.project("权限复核")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": rows}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid


def _setup(api, fixtures, rows=5):
    """TEST FIXTURE: a project, an API key of the default workspace and a hand-driven service."""
    pid = _project(api, fixtures, rows)
    api.c.app_state.jobs.stop()
    time.sleep(0.2)
    store = api.c.app_state.app.store
    auth = Auth(store)
    _, meta = auth.create_key("automation", "default", ("read", "write"))
    desc = authority.describe({"kind": "api_key", "key_id": meta["id"], "lineage": meta["id"], "tenant": "default"})
    return pid, store, auth, meta["id"], desc, JobService(api.c.app_state.app, start=False)


def _artifacts(store, jid):
    return store.one("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=?", (jid,))["n"]


def test_a_job_queued_by_a_revoked_key_never_starts(api, fixtures):
    pid, store, auth, kid, desc, svc = _setup(api, fixtures)
    j = svc.create(pid, {"formats": ["docx"]}, tenant="default", origin=f"api:{kid}", principal=desc)
    assert j["submitted_by"] == f"api:{kid}"
    auth.revoke_key(kid)
    assert svc._claim(j["id"]) is None
    done = svc.get(j["id"])
    assert done["status"] == "cancelled" and done["halt_reason"] == "authorization_revoked:credential_revoked"
    assert _artifacts(store, j["id"]) == 0
    items = svc.items(j["id"])
    assert {i["status"] for i in items} == {"cancelled"} and {i["issues"][0]["code"] for i in items} == {"authorization_revoked"}
    events = svc.events(j["id"])
    assert "job.started" not in [e["type"] for e in events]
    last = events[-1]
    assert last["type"] == "job.cancelled" and last["data"]["halt_reason"] == "authorization_revoked:credential_revoked"


def test_revocation_while_a_sample_renders_publishes_nothing_more(api, fixtures, monkeypatch):
    pid, store, auth, kid, desc, svc = _setup(api, fixtures)
    real = G.fill_checked

    def revoke_during_third(fmt, work, slots, plan, options, mode, pages):
        if plan.record["index"] == 2:
            auth.revoke_key(kid)                          # the key leaks and is revoked mid-render
        return real(fmt, work, slots, plan, options, mode, pages)

    monkeypatch.setattr(G, "fill_checked", revoke_during_third)
    j = svc.create(pid, {"formats": ["docx"]}, tenant="default", principal=desc)
    assert svc._claim(j["id"]) == j["id"]
    svc._run(j["id"])
    done = svc.get(j["id"])
    assert done["status"] == "cancelled" and done["halt_reason"].startswith("authorization_revoked:")
    by_seq = {i["seq"]: i for i in svc.items(j["id"])}
    assert [by_seq[k]["status"] for k in range(5)] == ["success", "success", "cancelled", "cancelled", "cancelled"]
    # the sample being rendered when the key was revoked got no file: the check runs right before publishing
    assert _artifacts(store, j["id"]) == 2 and not by_seq[2]["artifacts"]
    assert by_seq[2]["issues"][0]["code"] == "authorization_revoked"


def test_a_retry_is_authorised_by_whoever_retries(api, fixtures):
    pid, store, auth, kid, desc, svc = _setup(api, fixtures, rows=3)
    j = svc.create(pid, {"formats": ["docx"]}, tenant="default", principal=desc)
    auth.revoke_key(kid)
    svc._claim(j["id"])
    assert svc.get(j["id"])["status"] == "cancelled"
    # the revoked key cannot bring it back …
    again = svc.retry(j["id"], tenant="default", requested_by=f"api:{kid}", principal=desc)
    assert again["status"] == "queued" and again["halt_reason"] is None
    assert svc._claim(j["id"]) is None and svc.get(j["id"])["status"] == "cancelled"
    # … a live key of the workspace can, and its run records who asked
    _, meta2 = auth.create_key("second", "default", ("read", "write"))
    desc2 = authority.describe({"kind": "api_key", "key_id": meta2["id"], "lineage": meta2["id"], "tenant": "default"})
    svc.retry(j["id"], tenant="default", requested_by=f"api:{meta2['id']}", principal=desc2)
    assert svc._claim(j["id"]) == j["id"]
    svc._run(j["id"])
    done = svc.get(j["id"])
    assert done["status"] == "completed" and done["counts"]["success"] == 3
    runs = svc.runs(j["id"])
    assert [r["requested_by_principal"] for r in runs] == [f"api:{kid}", f"api:{kid}", f"api:{meta2['id']}"]


def test_a_rotated_key_keeps_its_lineage_s_jobs(api, fixtures):
    pid, store, auth, kid, desc, svc = _setup(api, fixtures, rows=2)
    j = svc.create(pid, {"formats": ["docx"]}, tenant="default", principal=desc)
    auth.rotate_key(kid)                                   # routine hygiene, not a revocation
    assert svc._claim(j["id"]) == j["id"]
    svc._run(j["id"])
    assert svc.get(j["id"])["status"] == "completed"


def test_deleting_the_project_stops_its_queued_job(api, fixtures):
    pid, store, auth, kid, desc, svc = _setup(api, fixtures, rows=2)
    j = svc.create(pid, {"formats": ["docx"]})
    api.ok(api.c.delete(f"/api/projects/{pid}"))
    assert svc._claim(j["id"]) is None
    done = store.one("SELECT status, halt_reason FROM jobs WHERE id=?", (j["id"],))
    assert done["status"] == "cancelled" and done["halt_reason"] == "project_deleted" and _artifacts(store, j["id"]) == 0
    # and the job itself goes with its project (Q16): not found, like its sources
    assert api.c.get(f"/api/jobs/{j['id']}").status_code == 404


def test_operator_jobs_and_jobs_from_before_descriptors_keep_running(api, fixtures):
    """Browser operator modes and jobs created before v5 carry no descriptor: they were
    authorised under the old rules and must not be stranded by the upgrade."""
    pid, store, auth, kid, desc, svc = _setup(api, fixtures, rows=2)
    j = svc.create(pid, {"formats": ["docx"]})
    store.execute("UPDATE jobs SET principal=NULL WHERE id=?", (j["id"],))
    store.execute("UPDATE job_runs SET principal=NULL WHERE job_id=?", (j["id"],))
    assert svc._claim(j["id"]) == j["id"]
    svc._run(j["id"])
    assert svc.get(j["id"])["status"] == "completed"


def test_the_ui_route_records_who_submitted(api, fixtures):
    pid = _project(api, fixtures, rows=2)
    j = api.ok(api.c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
    row = api.c.app_state.app.store.one("SELECT principal FROM jobs WHERE id=?", (j["id"],))
    assert authority.loads(row["principal"])["kind"] == "operator"          # loopback, no sign-in: operator mode


# ------------------------------------------------------------------ people (session mode)
def test_a_member_s_job_runs_and_stops_when_their_role_no_longer_allows_it(tmp_path, monkeypatch, fixtures):
    """The descriptor of a signed-in person is re-checked against live membership: an editor
    demoted to viewer (or removed) while their batch waits does not get it generated."""
    from fastapi.testclient import TestClient

    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
    from synwevo_docs.api.app import create_app

    from .helpers import Api

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    app = create_app(str(tmp_path / "data"))
    store = app.state.app.store
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    owner = ident.create_user("owner@example.com", "a-sufficiently-long-passphrase", status="active")
    wid = ident.workspaces_for(owner["id"])[0]["id"]
    editor = ident.create_user("editor@example.com", "a-sufficiently-long-passphrase", status="active")
    ident.add_member(wid, editor["id"], "editor")
    raw, _ = Sessions(store).create(editor["id"], workspace_id=wid, amr="password")
    c = TestClient(app)
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "t")
    c.headers[CSRF_HEADER] = "t"
    api = Api(c)
    try:
        pid = api.project("成员的任务")
        api.upload(pid, fixtures / "测试_客户100.xlsx")
        t = api.template(pid, fixtures / "测试_合同模板.docx")
        s = api.text_slot(t["id"], "示例客户名称", "客户名称")
        api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
        api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
        done = api.job(pid)
        assert done["status"] == "completed" and done["submitted_by"] == f"user:{editor['id']}"
        app.state.jobs.stop()
        time.sleep(0.2)
        svc = JobService(app.state.app, start=False)
        queued = api.ok(c.post(f"/api/projects/{pid}/jobs", json={"formats": ["docx"]}))
        ident.change_role(wid, editor["id"], "viewer")
        assert svc._claim(queued["id"]) is None
        from synwevo_core.context import as_tenant
        with as_tenant(wid):
            halted = svc.get(queued["id"])
        assert halted["status"] == "cancelled" and halted["halt_reason"] == "authorization_revoked:role_insufficient"
    finally:
        if app.state.jobs:
            app.state.jobs.stop()
