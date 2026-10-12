"""Synthetic upload failure injection; completed bytes must survive a retry."""
import hashlib
import json
import subprocess
import sys
import pytest

from .test_api_contract_uploads import _new, _put
from .test_api_v1 import _ok, keys  # noqa: F401
from synwevo_docs.api import v1_uploads as U


def test_completion_database_failure_preserves_bytes_for_retry(api, keys, monkeypatch):
    c, h = api.c, keys["rw"]
    data = b"synthetic upload whose bytes must not turn into zeroes"
    uid = _new(c, h, data, name="synthetic.txt")["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    store = c.app_state.app.store
    execute = store.execute

    def fail_once(sql, args=()):
        if "UPDATE uploads SET status='complete'" in sql:
            raise OSError("synthetic failure before the completion row commits")
        return execute(sql, args)

    monkeypatch.setattr(store, "execute", fail_once)
    failed = c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h)
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "internal_error"
    assert "synthetic failure" not in failed.text
    assert store.one("SELECT status FROM uploads WHERE id=?", (uid,))["status"] == "open"
    assert U.part_path(store, uid).read_bytes() == data
    monkeypatch.setattr(store, "execute", execute)
    completed = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert completed["sha256"] == hashlib.sha256(data).hexdigest()
    assert store.get_blob(completed["sha256"]) == data
    assert not U.part_path(store, uid).exists()


def test_missing_staged_bytes_never_become_a_zero_filled_file(api, keys):
    c, h = api.c, keys["rw"]
    data = b"synthetic staged content"
    uid = _new(c, h, data, name="synthetic.txt")["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    store = c.app_state.app.store
    U.part_path(store, uid).unlink()  # Isolated disk-loss fixture, never real user data.
    missing = c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h)
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "upload_data_missing"
    state = _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))
    assert state["status"] == "open" and state["received"] == 0
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    completed = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert store.get_blob(completed["sha256"]) == data


def test_resume_after_staging_loss_does_not_fill_the_prefix_with_zeroes(api, keys):
    c, h = api.c, keys["rw"]
    data = b"synthetic prefix and suffix"
    uid = _new(c, h, data, name="synthetic.txt")["id"]
    midpoint = len(data) // 2
    assert _put(c, h, uid, data[:midpoint], 0, len(data)).status_code == 200
    U.part_path(c.app_state.app.store, uid).unlink()
    resumed = _put(c, h, uid, data[midpoint:], midpoint, len(data))
    assert resumed.status_code == 409 and resumed.json()["error"]["code"] == "upload_data_missing"
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["received"] == 0
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    completed = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert completed["sha256"] == hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("phase", ["copying_blob", "before_commit", "after_commit"])
def test_completion_survives_real_process_exit_and_cleans_staging(api, keys, phase):
    """Only the child created here exits; all data belongs to the pytest fixture."""
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    data = b"Synthetic process crash upload\n" * 40000
    digest = hashlib.sha256(data).hexdigest()
    uid = _new(c, h, data, name="synthetic.txt", sha256=digest)["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    child = r'''
import json, os, sys
from fastapi.testclient import TestClient
from synwevo_docs.api.app import create_app
from synwevo_docs.api import v1_uploads as U
p = json.load(sys.stdin)
app = create_app(p["root"], start_jobs=False)
store = app.state.app.store
execute, write, unlink = store.execute, U._write_all, U._unlink_part
def interrupted_execute(sql, args=()):
    if "UPDATE uploads SET status='complete'" in sql and p["phase"] == "before_commit":
        os._exit(88)
    return execute(sql, args)
def interrupted_write(fd, data):
    write(fd, data)
    if p["phase"] == "copying_blob":
        os._exit(88)
def interrupted_unlink(store_, uid_):
    # The completion and its audit row commit together; the first statement after that commit
    # is the staging file's removal, so a crash here is "committed, not yet cleaned up".
    if p["phase"] == "after_commit":
        os._exit(88)
    unlink(store_, uid_)
store.execute, U._write_all, U._unlink_part = interrupted_execute, interrupted_write, interrupted_unlink
TestClient(app).post("/api/v1/uploads/" + p["uid"] + "/complete", json={}, headers=p["headers"])
raise SystemExit("completion did not reach the injected interruption")
'''
    run = subprocess.run([sys.executable, "-c", child], input=json.dumps({
        "root": str(store.root), "uid": uid, "headers": h, "phase": phase,
    }), capture_output=True, text=True, timeout=30)
    assert run.returncode == 88, run.stderr[-1000:]
    before = _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))
    assert before["status"] == ("complete" if phase == "after_commit" else "open")
    completed = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert completed["sha256"] == digest and store.get_blob(digest) == data
    U.cleanup_expired(store)
    part = U.part_path(store, uid)
    assert not part.exists() and not part.with_suffix(".blob").exists()


@pytest.mark.parametrize("end", ["abort", "expire"])
def test_incomplete_blob_copy_is_cleaned_without_touching_other_uploads(api, keys, end):
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    uid = _new(c, h, b"synthetic abandoned upload")["id"]
    other = _new(c, h, b"synthetic active upload")["id"]
    stale, active = (U.part_path(store, item) for item in (uid, other))
    for path in (stale, active):
        path.write_bytes(b"synthetic partial bytes")
        path.with_suffix(".blob").write_bytes(b"synthetic interrupted blob copy")
    if end == "abort":
        _ok(c.post(f"/api/v1/uploads/{uid}/abort", headers=h))
    else:
        store.execute("UPDATE uploads SET expires_at=0 WHERE id=?", (uid,))
    U.cleanup_expired(store)
    assert not stale.exists() and not stale.with_suffix(".blob").exists()
    assert active.read_bytes() == b"synthetic partial bytes"
    assert active.with_suffix(".blob").read_bytes() == b"synthetic interrupted blob copy"
