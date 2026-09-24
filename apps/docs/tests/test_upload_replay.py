"""Synthetic real-HTTP upload retries; cached metadata cannot outlive object access."""
import hashlib

import pytest

from .test_api_contract_uploads import _put
from .test_api_v1 import _ok, keys  # noqa: F401


def _consumed(api, keys, fixtures, kind):
    c, h = api.c, keys["rw"]
    name = "测试_客户100.xlsx" if kind == "source" else "测试_合同模板.docx"
    data = (fixtures / name).read_bytes()
    body = {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "purpose": kind}
    create_headers = {**h, "Idempotency-Key": "synthetic-upload-create"}
    first = c.post("/api/v1/uploads", json=body, headers=create_headers)
    uid = _ok(first)["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    pid = None
    if kind == "source":
        pid = _ok(c.post("/api/v1/projects", json={"name": "Synthetic replay"}, headers=h))["id"]
        route = f"/api/v1/projects/{pid}/files"
    else:
        route = "/api/v1/templates"
    consume_headers = {**h, "Idempotency-Key": "synthetic-upload-consume"}
    created = c.post(route, json={"upload_id": uid}, headers=consume_headers)
    obj = _ok(created)
    if pid:
        api.wait_parsed(pid)
    return body, create_headers, first, uid, pid, route, consume_headers, created, obj


@pytest.mark.parametrize("kind", ["source", "template"])
def test_consumed_upload_creation_and_use_replay_one_object(api, keys, fixtures, kind):
    c, h = api.c, keys["rw"]
    body, create_h, first, uid, pid, route, use_h, created, obj = _consumed(api, keys, fixtures, kind)
    replay = c.post("/api/v1/uploads", json=body, headers=create_h)
    assert replay.status_code == 201, replay.text
    assert replay.content == first.content and replay.headers["Idempotent-Replayed"] == "true"
    # The original response is immutable, while GET describes the current state.
    assert replay.json()["status"] == "open"
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["status"] == "consumed"
    repeated = c.post(route, json={"upload_id": replay.json()["id"]}, headers=use_h)
    assert repeated.content == created.content and repeated.headers["Idempotent-Replayed"] == "true"
    conflict = c.post("/api/v1/uploads", json={**body, "filename": "changed.xlsx"}, headers=create_h)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    # A new operation still cannot consume the same upload a second time.
    duplicate = c.post(route, json={"upload_id": uid}, headers=h)
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "upload_consumed"
    assert _put(c, h, uid, b"x", 0, body["size"]).status_code == 409
    store = c.app_state.app.store
    table = "source_files" if kind == "source" else "templates"
    assert store.one(f"SELECT COUNT(*) n FROM {table}")["n"] == 1
    assert store.one("SELECT COUNT(*) n FROM uploads")["n"] == 1


@pytest.mark.parametrize("target", ["source", "project", "template"])
def test_consumed_upload_metadata_and_cache_follow_object_deletion(api, keys, fixtures, target):
    c, h = api.c, keys["rw"]
    kind = "template" if target == "template" else "source"
    body, create_h, first, uid, pid, route, use_h, created, obj = _consumed(api, keys, fixtures, kind)
    admin, _ = keys["auth"].create_key("synthetic-cleanup", "default", ("admin",))
    resource = {"source": f"/files/{obj['id']}", "project": f"/projects/{pid}",
                "template": f"/templates/{obj['id']}"}[target]
    _ok(c.delete("/api/v1" + resource, headers={"Authorization": f"Bearer {admin}"}))
    responses = [c.get(f"/api/v1/uploads/{uid}", headers=h),
                 c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h),
                 c.post("/api/v1/uploads", json=body, headers=create_h),
                 c.post("/api/v1/uploads", json={**body, "size": body["size"] + 1}, headers=create_h),
                 c.post(route, json={"upload_id": uid}, headers=use_h)]
    for response in responses:
        assert response.status_code == 404, response.text
        assert body["filename"] not in response.text and body["sha256"] not in response.text
        assert "resource_id" not in response.text


def test_consumed_upload_cannot_replay_through_revocation_or_another_workspace(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    body, create_h, first, uid, *_ = _consumed(api, keys, fixtures, "source")
    assert c.get(f"/api/v1/uploads/{uid}", headers=keys["other"]).status_code == 404
    other = c.post("/api/v1/uploads", json=body,
                   headers={**keys["other"], "Idempotency-Key": create_h["Idempotency-Key"]})
    assert other.status_code == 201 and other.json()["id"] != uid
    assert "Idempotent-Replayed" not in other.headers
    assert c.post("/api/v1/uploads", json=body,
                  headers={**keys["ro"], "Idempotency-Key": create_h["Idempotency-Key"]}).status_code == 403
    store = c.app_state.app.store
    key = store.one("SELECT id FROM api_keys WHERE name='rw'")
    keys["auth"].revoke_key(key["id"])
    assert c.post("/api/v1/uploads", json=body, headers=create_h).status_code == 401


@pytest.mark.parametrize("terminal", ["aborted", "expired"])
def test_unused_terminal_upload_creation_still_refuses_replay(api, keys, terminal):
    c, h = api.c, {**keys["rw"], "Idempotency-Key": "synthetic-terminal-upload"}
    body = {"filename": "synthetic.txt", "size": 1}
    uid = _ok(c.post("/api/v1/uploads", json=body, headers=h))["id"]
    if terminal == "aborted":
        _ok(c.post(f"/api/v1/uploads/{uid}/abort", headers=h))
    else:
        c.app_state.app.store.execute("UPDATE uploads SET expires_at=0 WHERE id=?", (uid,))
    response = c.post("/api/v1/uploads", json=body, headers=h)
    assert response.status_code == 410 and response.json()["error"]["code"] == f"upload_{terminal}"
