"""Real engine and persistent jobs; faults/cancellation injected only into isolated fixtures."""
import pytest

from .test_api_v1 import _ok, _prepared, keys  # noqa: F401


def _manual_runner(api):
    jobs = api.c.app_state.jobs
    jobs.stop()
    for thread in (jobs._thread, jobs._supervisor):
        if thread:
            thread.join(timeout=10)
            assert not thread.is_alive()
    jobs._stop = False       # deterministic test-only scheduling; no background thread
    return jobs


def test_failed_only_retry_keeps_success_and_cancelled_items(api, keys, fixtures, monkeypatch):
    from synwevo_docs.engine import generate as G
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    jobs = _manual_runner(api)
    jid = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={}, headers=h))["id"]
    original = G.fill_checked
    calls = 0
    def fail_second_and_cancel(*a, **kw):
        nonlocal calls
        calls += 1
        if calls == 2:
            jobs.cancel(jid)
            raise RuntimeError("synthetic transient render failure")
        return original(*a, **kw)
    monkeypatch.setattr(G, "fill_checked", fail_second_and_cancel)
    assert jobs._claim(jid) == jid
    jobs._run(jid)
    before = _ok(c.get(f"/api/v1/jobs/{jid}/items", headers=h))["items"]
    assert [i["status"] for i in before] == ["success", "failed", "cancelled"]
    monkeypatch.setattr(G, "fill_checked", original)
    hdr = {**h, "Idempotency-Key": "synthetic-failed-only"}
    retried = _ok(c.post(f"/api/v1/jobs/{jid}/retry", json={"failed_only": True}, headers=hdr))
    assert retried["counts"]["pending"] == 1
    assert _ok(c.post(f"/api/v1/jobs/{jid}/retry", json={"failed_only": True}, headers=hdr))["idempotent_replay"]
    changed = c.post(f"/api/v1/jobs/{jid}/retry", json={}, headers=hdr)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    assert jobs._claim(jid) == jid
    jobs._run(jid)
    after = _ok(c.get(f"/api/v1/jobs/{jid}/items", headers=h))["items"]
    assert [i["status"] for i in after] == ["success", "success", "cancelled"]
    assert [i["attempts"] for i in after] == [1, 2, 0]
    assert after[0]["artifacts"] == before[0]["artifacts"]
    assert _ok(c.get(f"/api/v1/jobs/{jid}", headers=h))["status"] == "completed_with_errors"
    no_failed = c.post(f"/api/v1/jobs/{jid}/retry", json={"failed_only": True}, headers=h)
    assert no_failed.status_code == 409 and no_failed.json()["error"]["code"] == "nothing_to_retry"
    # Compatibility: an ordinary retry can explicitly resume the cancelled remainder.
    assert _ok(c.post(f"/api/v1/jobs/{jid}/retry", json={}, headers=h))["counts"]["pending"] == 1
    assert jobs._claim(jid) == jid
    jobs._run(jid)
    completed = _ok(c.get(f"/api/v1/jobs/{jid}", headers=h))
    assert completed["status"] == "completed"
    assert completed["counts"]["documents"] == 3
    final_items = _ok(c.get(f"/api/v1/jobs/{jid}/items", headers=h))["items"]
    assert [i["status"] for i in final_items] == ["success", "success", "success"]
    assert [i["attempts"] for i in final_items] == [1, 2, 1]
    assert final_items[0]["artifacts"] == before[0]["artifacts"]


@pytest.mark.parametrize("body", [{"failed_only":"false"}, {"failed_only":1}, {"include_skipped":"false"},
                                  {"failed_only":True,"include_skipped":True}])
def test_retry_rejects_silent_boolean_coercion(api, keys, body):
    r = api.c.post("/api/v1/jobs/missing/retry", json=body, headers=keys["rw"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid"


def test_revocation_between_blob_write_and_publication_wins(api, keys, fixtures, monkeypatch):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    jobs = _manual_runner(api)
    jid = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={}, headers=h))["id"]
    store = c.app_state.app.store
    original = store.put_blob
    def revoke_at_storage(data):
        digest = original(data)
        kid = store.one("SELECT id FROM api_keys WHERE name='rw'")["id"]
        keys["auth"].revoke_key(kid)
        return digest
    monkeypatch.setattr(store, "put_blob", revoke_at_storage)
    assert jobs._claim(jid) == jid
    jobs._run(jid)
    assert store.one("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=?", (jid,))["n"] == 0
    assert store.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "cancelled"


def test_empty_run_descriptor_is_not_replaced_by_operator_authority(api, keys, fixtures):
    c = api.c
    pid = _prepared(api, keys, fixtures)
    jobs = _manual_runner(api)
    jid = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={}, headers=keys["rw"]))["id"]
    jobs.store.execute("UPDATE job_runs SET principal='{}' WHERE job_id=?", (jid,))
    assert jobs._claim(jid) is None
    assert jobs.store.one("SELECT halt_reason FROM jobs WHERE id=?", (jid,))["halt_reason"] == "authorization_revoked:unknown_principal"
