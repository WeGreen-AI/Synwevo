"""Synthetic source lifecycle: deletion and racing parser versions cannot revive data."""
import threading
import time

import pytest

from .test_api_v1 import _ok, keys  # noqa: F401


def test_deleted_project_sources_are_unreachable_everywhere(api, keys):
    c = api.c
    pid = api.project("合成删除验收")
    src = _ok(c.post(f"/api/projects/{pid}/sources", files={"files": ("synthetic.csv", b"name,value\nexample,1\n")}))[0]
    sid = src["id"]
    for _ in range(100):
        if _ok(c.get(f"/api/v1/files/{sid}", headers=keys["rw"]))["status"] != "parsing": break
        time.sleep(.01)
    for path in (f"/api/sources/{sid}/view", f"/api/sources/{sid}/download"):
        assert c.get(path).status_code == 200
    _ok(c.delete(f"/api/projects/{pid}"))
    for path in (f"/api/sources/{sid}/view", f"/api/sources/{sid}/download", f"/api/sources/{sid}/search?q=example",
                 f"/api/sources/{sid}/blocks", f"/api/sources/{sid}/words/0", f"/api/sources/{sid}/page/0.png",
                 f"/api/v1/files/{sid}"):
        response = c.get(path, headers=keys["rw"])
        assert response.status_code == 404 and "example" not in response.text, (path, response.text)
    assert c.put(f"/api/sources/{sid}/options", json={"csv": {"delimiter": ";"}}).status_code == 404


@pytest.mark.parametrize("withdrawal", ["project", "source", "new_options"])
def test_parser_does_not_publish_after_deletion_or_over_newer_options(api, monkeypatch, withdrawal):
    from synwevo_docs.engine import sources
    app = api.c.app_state.app
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    original_parse, original_worker = sources.parse_source, app._parse_source
    def delayed(data, name, options):
        if "csv" not in options:
            started.set()
            assert release.wait(5)
        return original_parse(data, name, options)
    def worker(sid):
        try: original_worker(sid)
        finally: done.set()
    monkeypatch.setattr(sources, "parse_source", delayed)
    monkeypatch.setattr(app, "_parse_source", worker)
    pid = api.project("合成解析竞态")
    src = app.add_source(pid, "synthetic.csv", b"name;value\nexample;1\n")
    sid = src["id"]
    assert started.wait(5)
    try:
        if withdrawal == "project": app.delete_project(pid)
        elif withdrawal == "source": app.remove_source(sid)
        else: app.set_source_options(sid, {"csv": {"delimiter": ","}})
        after_change = app.store.one("SELECT * FROM source_files WHERE id=?", (sid,))
        revision = app.store.one("SELECT rev FROM projects WHERE id=?", (pid,))["rev"]
    finally:
        done.clear()
        release.set()
    assert done.wait(5)
    after_parser = app.store.one("SELECT * FROM source_files WHERE id=?", (sid,))
    assert after_parser == after_change
    assert app.store.one("SELECT rev FROM projects WHERE id=?", (pid,))["rev"] == revision
    if withdrawal == "new_options":
        assert after_parser["status"] == "ready" and after_parser["version"] == 2
        assert after_parser["structure"]["sheets"][0]["cols"] == 1
    else:
        assert after_parser["parsed_blob"] is None and after_parser["working_blob"] is None


def test_parser_storage_failure_is_reported_and_can_be_retried(api, monkeypatch):
    app = api.c.app_state.app
    pid = api.project("合成存储故障")
    # Intercept this fixture's scheduling only; run the actual parser deterministically.
    monkeypatch.setattr(app.parse_pool, "submit", lambda *args: None)
    sid = app.add_source(pid, "synthetic.csv", b"name,value\nexample,1\n")["id"]
    original = app.store.put_json_blob

    def disk_full(_):
        raise OSError("synthetic disk full /private/sensitive-path")

    monkeypatch.setattr(app.store, "put_json_blob", disk_full)
    app._parse_source(sid)
    failed = app.get_source(sid, raw=True)
    assert failed["status"] == "error" and failed["parsed_blob"] is None
    assert "存储空间" in failed["error"] and "sensitive-path" not in failed["error"]
    monkeypatch.setattr(app.store, "put_json_blob", original)
    retried = app.set_source_options(sid, {"csv": {"delimiter": ","}})
    assert retried["status"] == "ready" and retried["version"] == 2
    assert retried["structure"]["sheets"][0]["cols"] == 2
