"""Synthetic, real-engine previews: API authoring, output parity and current authorization."""
import hashlib
import io
import zipfile

import pytest

from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401


def _parts(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        # ZIP entry timestamps and core created/modified timestamps are not business content.
        return {n: z.read(n) for n in z.namelist() if n != "docProps/core.xml"}


def test_preview_matches_ui_and_job_output_without_creating_an_artifact(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    pre = _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={}, headers=h))
    result = c.post(f"/api/v1/projects/{pid}/preview", json={"index": 1, "signature": pre["signature"]}, headers=h)
    assert result.status_code == 200, result.text[:200]
    assert result.content.startswith(b"PK")
    assert result.headers["X-Content-SHA256"] == hashlib.sha256(result.content).hexdigest()
    assert result.headers["X-Project-Signature"] == pre["signature"]
    assert int(result.headers["X-Template-Version"]) == pre["template_version"]
    assert "attachment" in result.headers["Content-Disposition"] and result.headers["Cache-Control"] == "no-store"
    store = c.app_state.app.store
    assert store.one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 0
    ui = _ok(c.get(f"/api/projects/{pid}/preview?index=1"))
    ui_bytes = c.get(f"/api/preview-files/{ui['output']['blob']}?fmt=docx").content
    assert ui_bytes == result.content
    by_key = c.post(f"/api/v1/projects/{pid}/preview", json={"key": ui["record"]["key"]}, headers=h)
    assert by_key.status_code == 200 and by_key.content == result.content
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    assert job["status"] == "completed"
    item = _ok(c.get(f"/api/v1/jobs/{job['id']}/items?offset=1&limit=1", headers=h))["items"][0]
    art = item["artifacts"][0]
    output = c.get(f"/api/v1/artifacts/{art['id']}/content", headers=h)
    assert output.status_code == 200 and _parts(output.content) == _parts(result.content)


@pytest.mark.parametrize("body,code", [({"index": True},400), ({"index": -1},400), ({"index": 3},400),
    ({"key":"missing"},404), ({"key":""},400), ({"key":"x","index":0},400),
    ({"format":"pdf"},400), ({"signature":"old"},409)])
def test_preview_rejects_ambiguous_or_stale_selection(api, keys, fixtures, body, code):
    pid = _prepared(api, keys, fixtures)
    r = api.c.post(f"/api/v1/projects/{pid}/preview", json=body, headers=keys["rw"])
    assert r.status_code == code, r.text[:300]
    assert "error" in r.json()


def test_preview_authorization_is_not_a_cookie_or_cache_bypass(api, keys, fixtures):
    c = api.c
    pid = _prepared(api, keys, fixtures)
    url = f"/api/v1/projects/{pid}/preview"
    assert c.post(url, json={}, headers=keys["rw"]).status_code == 200
    assert c.post(url, json={}).status_code == 401
    assert c.post(url, json={}, headers=keys["ro"]).status_code == 403
    assert c.post(url, json={}, headers=keys["other"]).status_code == 404


def test_revocation_during_preview_never_returns_document_bytes(api, keys, fixtures, monkeypatch):
    c = api.c
    pid = _prepared(api, keys, fixtures)
    original = c.app_state.views.preview
    def revoke_after_render(*args, **kw):
        result = original(*args, **kw)
        row = c.app_state.app.store.one("SELECT id FROM api_keys WHERE name='rw'")
        keys["auth"].revoke_key(row["id"])
        return result
    monkeypatch.setattr(c.app_state.views, "preview", revoke_after_render)
    r = c.post(f"/api/v1/projects/{pid}/preview", json={}, headers=keys["rw"])
    assert r.status_code == 401 and r.json()["error"]["code"] == "key_revoked"
    assert "Content-Disposition" not in r.headers


def test_a_project_changed_during_preview_is_not_returned_as_current(api, keys, fixtures, monkeypatch):
    c = api.c
    pid = _prepared(api, keys, fixtures)
    original = c.app_state.views.preview
    def edit_after_render(*args, **kw):
        result = original(*args, **kw)
        c.app_state.app.store.bump_project(pid)
        return result
    monkeypatch.setattr(c.app_state.views, "preview", edit_after_render)
    r = c.post(f"/api/v1/projects/{pid}/preview", json={}, headers=keys["rw"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale"


def test_failed_readback_blocks_ui_and_api_before_blob_publication(api, keys, fixtures, monkeypatch):
    import synwevo_docs.views as views
    c = api.c
    pid = _prepared(api, keys, fixtures)
    store = c.app_state.app.store
    blobs_before = {p.name for p in (store.root / "blobs").rglob("*") if p.is_file()}
    # Explicit verifier fault fixture, never a production renderer or fake successful output.
    monkeypatch.setattr(views, "verify_output", lambda *a, **kw: {"reopened": True, "missing": [{"slot":"customer"}]})
    ui = _ok(c.get(f"/api/projects/{pid}/preview"))
    assert ui["blocked"] and "output" not in ui
    r = c.post(f"/api/v1/projects/{pid}/preview", json={}, headers=keys["rw"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "preview_blocked"
    assert {p.name for p in (store.root / "blobs").rglob("*") if p.is_file()} == blobs_before
    assert store.one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 0
