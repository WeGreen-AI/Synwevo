"""Synthetic uploads through real services/HTTP: declared and measured scan bounds agree."""
import asyncio
import hashlib
import io

import pytest
from fastapi import UploadFile

from synwevo_core.errors import ServiceError
from synwevo_core.malware import Policy, ScanResult
from synwevo_docs.api.app import _read_limited
from synwevo_docs.api import v1

from .test_api_v1 import _ok, keys  # noqa: F401
from .test_api_contract_uploads import _put


def _policy(api, monkeypatch, limit=64, mode="required"):
    app = api.c.app_state.app
    monkeypatch.setattr(app, "scan_policy", Policy("tcp://test-scanner:3310", mode, 1, limit))
    return app


@pytest.mark.parametrize("surface", ["service_source", "service_template", "ui_source", "ui_template", "api_source", "api_template", "resumable"])
def test_known_oversize_is_refused_without_persisting_or_scanning(api, keys, monkeypatch, surface):
    app = _policy(api, monkeypatch)
    pid = api.project("Synthetic admission")
    data = b"x" * 65
    before = app.store.one("SELECT * FROM projects WHERE id=?", (pid,))
    # A refusal must not even reach hashing, storage or a parser/scan task.
    monkeypatch.setattr(app.store, "put_blob", lambda *a: pytest.fail("oversize blob persisted"))
    monkeypatch.setattr(app.parse_pool, "submit", lambda *a, **k: pytest.fail("oversize task queued"))
    if surface.startswith("service"):
        with pytest.raises(ServiceError) as caught:
            if surface.endswith("source"):
                app.add_source(pid, "synthetic.csv", data)
            else:
                app.upload_template("synthetic.docx", data)
        assert caught.value.status == 413 and caught.value.code == "scan_limit"
        detail = caught.value.detail
    else:
        if surface == "resumable":
            response = api.c.post("/api/v1/uploads", json={"filename": "synthetic.csv", "size": 65}, headers=keys["rw"])
        else:
            route, field = {"ui_source": (f"/api/projects/{pid}/sources", "files"),
                            "ui_template": ("/api/templates/upload", "file"),
                            "api_source": (f"/api/v1/projects/{pid}/files", "file"),
                            "api_template": ("/api/v1/templates", "file")}[surface]
            response = api.c.post(route, files={field: ("synthetic.csv", data)},
                                  headers=keys["rw"] if surface.startswith("api") else {})
        assert response.status_code == 413, response.text
        if surface.startswith("ui"):
            assert response.json()["error"] == "scan_limit"
            detail = response.json()["detail"]
        else:
            assert response.json()["error"]["code"] == "scan_limit"
            detail = response.json()["error"]["details"]
    assert detail["limit_bytes"] == 64 and detail["limit_mb"] == 64 / 1048576
    for table in ("source_files", "templates", "uploads"):
        assert app.store.one(f"SELECT COUNT(*) n FROM {table}")["n"] == 0
    assert not list((app.store.root / "tmp" / "uploads").glob("*.part"))
    assert app.store.one("SELECT * FROM projects WHERE id=?", (pid,)) == before


@pytest.mark.parametrize("mode,scan_limit,expected", [("required", 64, 64), ("off", 64, 100 * 1048576), ("required", 200 * 1048576, 100 * 1048576)])
def test_ui_machine_and_usage_advertise_the_actual_byte_limit(api, keys, monkeypatch, mode, scan_limit, expected):
    _policy(api, monkeypatch, scan_limit, mode)
    for path, headers in [("/api/capabilities", {}), ("/api/v1/capabilities", keys["ro"]), ("/api/v1/usage", keys["ro"])]:
        limits = _ok(api.c.get(path, headers=headers))["limits"]
        assert limits["max_file_bytes"] == expected
        assert limits["max_file_mb"] == expected / 1048576
        assert limits["configured_max_file_bytes"] == 100 * 1048576
        assert limits["scan_max_file_bytes"] == (scan_limit if mode == "required" else None)


def test_exact_limit_is_allowed_but_engine_ceiling_still_applies(api, monkeypatch):
    app = _policy(api, monkeypatch)
    data = b"a,b\n" + b"1,2\n" * 15
    assert len(data) == 64
    class ScannerFixture:
        def scan(self, data, sha256=None):
            return ScanResult("clean", sha256=sha256)
    monkeypatch.setattr(app, "scanner", ScannerFixture())
    pid = api.project("Synthetic exact boundary")
    response = api.c.post(f"/api/projects/{pid}/sources", files={"files": ("boundary.csv", data)})
    assert response.status_code == 200, response.text
    from .test_malware_scan import _wait
    source = _wait(api.c, pid, response.json()[0]["id"], ("ready", "error", "scan_failed"))
    assert source["status"] == "ready" and source["scan"]["status"] == "clean"
    monkeypatch.setattr(app, "scan_policy", Policy(None, "off", 1, 64))
    app.require_upload_size(65)
    with pytest.raises(ServiceError) as caught:
        app.require_upload_size(100 * 1048576 + 1)
    assert caught.value.code == "too_large"


@pytest.mark.parametrize("stage", ["open", "complete"])
def test_lowered_limit_preserves_existing_upload_and_replay_without_new_bytes(api, keys, monkeypatch, stage):
    app = api.c.app_state.app
    data = b"a,b\n" + b"1,2\n" * 20
    headers = {**keys["rw"], "Idempotency-Key": "synthetic-admission-session"}
    body = {"filename": "synthetic.csv", "size": len(data)}
    created = api.c.post("/api/v1/uploads", json=body, headers=headers)
    uid = _ok(created)["id"]
    assert _put(api.c, keys["rw"], uid, data, 0, len(data)).status_code == 200
    if stage == "complete":
        _ok(api.c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=keys["rw"]))
    _policy(api, monkeypatch)
    replay = api.c.post("/api/v1/uploads", json=body, headers=headers)
    assert replay.content == created.content and replay.headers["Idempotent-Replayed"] == "true"
    status = _ok(api.c.get(f"/api/v1/uploads/{uid}", headers=keys["ro"]))
    assert status["status"] == stage and status["received"] == len(data)
    if stage == "open":
        assert _put(api.c, keys["rw"], uid, data, 0, len(data)).status_code == 413
        complete = api.c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=keys["rw"])
        assert complete.status_code == 413
        assert not app.store.has_blob(hashlib.sha256(data).hexdigest())
    else:
        assert api.c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=keys["rw"]).status_code == 200
        pid = api.project("Synthetic complete admission")
        monkeypatch.setattr(app.store, "get_blob", lambda *a: pytest.fail("oversize staged bytes read"))
        refused = api.c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": uid}, headers=keys["rw"])
        assert refused.status_code == 413, refused.text
    assert _ok(api.c.get(f"/api/v1/uploads/{uid}", headers=keys["ro"]))["status"] == stage
    assert api.c.post(f"/api/v1/uploads/{uid}/abort", headers=keys["rw"]).status_code == 200


def test_unknown_multipart_size_is_measured_and_stops_at_the_limit(api, monkeypatch):
    app = _policy(api, monkeypatch)
    upload = UploadFile(io.BytesIO(b"x" * 65), filename="synthetic.csv")
    assert upload.size is None
    with pytest.raises(ServiceError) as caught:
        asyncio.run(_read_limited(upload, app))
    assert caught.value.code == "scan_limit"


def test_authorized_server_import_checks_before_reading(api, keys, monkeypatch, tmp_path):
    app = _policy(api, monkeypatch)
    source = tmp_path / "synthetic.csv"
    source.write_bytes(b"x" * 65)
    monkeypatch.setattr(v1, "IMPORT_ROOTS", [str(tmp_path)])
    pid = api.project("Synthetic import")
    response = api.c.post(f"/api/v1/projects/{pid}/files", json={"path": str(source)}, headers=keys["rw"])
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "scan_limit"
    assert app.store.one("SELECT COUNT(*) n FROM source_files")["n"] == 0
