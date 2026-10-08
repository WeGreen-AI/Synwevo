"""Synthetic uploads must finish writing before pytest removes their temporary Store."""
from contextlib import ExitStack
import threading

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("lifespan", [False, True], ids=["no-lifespan", "completed-lifespan"])
def test_cleanup_joins_running_parsers_and_cancels_queued_uploads(
    tmp_path, monkeypatch, release_factory_resources, lifespan,
):
    from synwevo_docs.api.app import create_app
    from synwevo_docs.engine import sources

    api = create_app(str(tmp_path / "data"), start_jobs=False)
    app = api.state.app
    started = {f"fixture-{i}.csv": threading.Event() for i in range(2)}
    release = threading.Event()
    futures, joined = [], []
    original_parse = sources.parse_source
    original_submit = app.parse_pool.submit
    original_join = threading.Thread.join

    def held_parse(data, name, options):
        result = original_parse(data, name, options)
        started[name].set()
        assert release.wait(10), "synthetic parser gate timed out"
        return result

    def record_submit(*args, **kwargs):
        future = original_submit(*args, **kwargs)
        futures.append(future)
        return future

    def release_on_join(thread, *args, **kwargs):
        # Deterministic ordering: publication may resume only when teardown actually
        # waits for this pool. No sleeps, replacement parser or simulated blob write.
        if thread in app.parse_pool._threads:
            joined.append(thread)
            release.set()
        return original_join(thread, *args, **kwargs)

    monkeypatch.setattr(sources, "parse_source", held_parse)
    monkeypatch.setattr(app.parse_pool, "submit", record_submit)
    monkeypatch.setattr(threading.Thread, "join", release_on_join)
    try:
        with ExitStack() as stack:
            client = TestClient(api)
            stack.callback(client.close)
            if lifespan:
                stack.enter_context(client)
            response = client.post("/api/projects", json={"name": "Synthetic parser lifecycle"})
            assert response.status_code == 200
            pid = response.json()["id"]
            ids = []
            for i in range(3):
                response = client.post(f"/api/projects/{pid}/sources", files={
                    "files": (f"fixture-{i}.csv", f"name,value\nsynthetic,{i}\n".encode()),
                })
                assert response.status_code == 200
                ids.append(response.json()[0]["id"])
            assert all(event.wait(5) for event in started.values())
            assert all(future.running() for future in futures[:2])
            assert not futures[2].running()

        release_factory_resources()
        assert joined, "test cleanup returned without waiting for the running parsers"
        assert all(not thread.is_alive() for thread in app.parse_pool._threads)
        assert futures[2].cancelled(), "queued upload should be left for resume_parsing"
        for sid, future in zip(ids, futures[:2]):
            assert future.result(timeout=0) is None
            row = app.get_source(sid, raw=True)
            assert row["status"] == "ready" and app.store.has_blob(row["parsed_blob"])
        assert app.get_source(ids[2], raw=True)["status"] == "parsing"
    finally:
        # Also release the real workers when testing the pre-fix implementation.
        release.set()
        app.close()
        app.parse_pool.shutdown(wait=True, cancel_futures=True)
