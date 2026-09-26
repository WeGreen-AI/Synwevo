"""A process stops what it started.

Leaving the application — uvicorn's graceful shutdown, a test client leaving its ``with`` block —
stops the job runner and its supervisor, the webhook dispatcher, the copy runner, the mailer, the
notification dispatcher, the session sweeper and the member-import runner; no thread of theirs
outlives it. Before this nothing was ever stopped: every application built in these suites kept
its workers, and through them its store, alive, and the verify container ran out of memory a third
of the way through the suite (CI-48).
"""
import threading
import time

from fastapi.testclient import TestClient

WORKER_THREADS = {"job-runner", "job-supervisor", "webhooks", "project-copies",
                  "mail", "notify", "auth-sweeper", "member-import"}


def _workers_alive() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name in WORKER_THREADS)


def _settle(seconds: float = 5.0) -> list[str]:
    deadline = time.monotonic() + seconds
    while _workers_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    return _workers_alive()


def test_leaving_the_application_stops_every_worker_thread(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    from synwevo_server.app import create_app
    assert _settle() == [], "another test left workers behind"
    api = create_app(str(tmp_path / "d"), start_jobs=True)
    with TestClient(api) as c:
        assert c.get("/healthz").status_code == 200
        started = set(_workers_alive())
        assert WORKER_THREADS <= started, started
    # The lifespan's shutdown hooks ran; every worker leaves within its own wake interval.
    assert _settle() == []


def test_stop_all_is_the_net_for_an_application_that_never_shut_down(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    from synwevo_server.app import create_app
    from synwevo_core import workers
    api = create_app(str(tmp_path / "d"), start_jobs=True)
    TestClient(api).get("/healthz")                         # no context: no shutdown hook runs
    assert WORKER_THREADS <= set(_workers_alive())
    left = workers.stop_all(wait=5.0)
    assert left == [], left
    assert _workers_alive() == []
    assert workers.stop_all(wait=0.1) == []                # idempotent, nothing left to stop
