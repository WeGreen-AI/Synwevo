"""/api/v1 contract: resumable uploads and checksummed multipart uploads.

A large source or template arrives in byte ranges, survives an interruption (the client asks how
much was received and continues), is verified by size and SHA-256 before anything can use it,
and is then used exactly once, by its own workspace.
"""
import hashlib
import time

from synwevo_docs.api import v1_uploads as U

from .test_api_v1 import _ok, keys  # noqa: F401  (keys is a fixture)


def _new(c, h, data: bytes, name="客户.xlsx", **extra):
    body = {"filename": name, "size": len(data), **extra}
    r = c.post("/api/v1/uploads", json=body, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def _put(c, h, uid, data: bytes, start: int, size: int):
    end = start + len(data) - 1
    return c.put(f"/api/v1/uploads/{uid}", content=data,
                 headers={**h, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Type": "application/octet-stream"})


def test_ranged_upload_resume_complete_and_use_as_source(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    up = _new(c, h, data, sha256=digest, purpose="source")
    assert up["status"] == "open" and up["received"] == 0 and up["upload_url"].endswith(f"/api/v1/uploads/{up['id']}")
    assert up["expires_at"] > time.time() + 23 * 3600
    uid, n = up["id"], len(data)
    half = n // 2
    # the bytes go to a private temp file under the data directory, not into the database
    part = U.part_path(c.app_state.app.store, uid)
    assert part.exists() and oct(part.stat().st_mode & 0o777) == "0o600"
    assert oct(part.parent.stat().st_mode & 0o777) == "0o700"
    r = _put(c, h, uid, data[:half], 0, n)
    assert r.status_code == 200 and r.json()["received"] == half
    # interrupted: the client asks where to resume
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["received"] == half
    # a gap is refused with the offset to continue from
    gap = _put(c, h, uid, data[half + 10:], half + 10, n)
    assert gap.status_code == 416 and gap.json()["error"]["details"]["received"] == half
    # a resend that overlaps what we have is accepted: only the new bytes are appended
    r = _put(c, h, uid, data[half - 100:], half - 100, n)
    assert r.status_code == 200 and r.json()["received"] == n
    # not usable before `complete` has verified it
    pid = _ok(c.post("/api/v1/projects", json={"name": "续传"}, headers=h))["id"]
    early = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": uid}, headers=h)
    assert early.status_code == 409 and early.json()["error"]["code"] == "upload_incomplete"
    done = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert done["status"] == "complete" and done["sha256"] == digest and not part.exists()
    assert c.app_state.app.store.has_blob(digest)
    src = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": uid}, headers=h)
    assert src.status_code == 201, src.text
    assert src.json()["sha256"] == digest and src.json()["filename"] == "客户.xlsx"
    api.wait_parsed(pid)
    assert _ok(c.get(f"/api/v1/files/{src.json()['id']}", headers=h))["status"] == "ready"
    # one upload, one use
    again = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": uid}, headers=h)
    assert again.status_code == 409 and again.json()["error"]["code"] == "upload_consumed"
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["status"] == "consumed"


def test_checksum_mismatch_is_not_stored_and_resets(api, keys):
    c, h = api.c, keys["rw"]
    data = b"PK" + b"x" * 1000
    up = _new(c, h, data, sha256=hashlib.sha256(b"something else").hexdigest())
    assert _put(c, h, up["id"], data, 0, len(data)).status_code == 200
    r = c.post(f"/api/v1/uploads/{up['id']}/complete", json={}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "checksum_mismatch"
    assert not c.app_state.app.store.has_blob(hashlib.sha256(data).hexdigest())
    state = _ok(c.get(f"/api/v1/uploads/{up['id']}", headers=h))
    assert state["status"] == "open" and state["received"] == 0
    # a sha256 given at complete must agree with the one declared at create
    r = c.post(f"/api/v1/uploads/{up['id']}/complete", json={"sha256": hashlib.sha256(data).hexdigest()}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "checksum_conflict"


def test_body_longer_or_shorter_than_the_range_is_refused(api, keys):
    c, h = api.c, keys["rw"]
    up = _new(c, h, b"a" * 100)
    uid = up["id"]
    r = c.put(f"/api/v1/uploads/{uid}", content=b"a" * 60,
              headers={**h, "Content-Range": "bytes 0-49/100"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "upload_length_mismatch"
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["received"] == 0
    assert U.part_path(c.app_state.app.store, uid).stat().st_size == 0, "拒绝的写入必须回滚"
    r = c.put(f"/api/v1/uploads/{uid}", content=b"a" * 10, headers={**h, "Content-Range": "bytes 0-49/100"})
    assert r.status_code == 400
    r = c.put(f"/api/v1/uploads/{uid}", content=b"a" * 10, headers={**h, "Content-Range": "bytes 0-9/99"})
    assert r.status_code == 400 and r.json()["error"]["field"] == "Content-Range"
    # no Content-Range means the whole file in one request
    whole = c.put(f"/api/v1/uploads/{uid}", content=b"a" * 100, headers=h)
    assert whole.status_code == 200 and whole.json()["received"] == 100


def test_size_limit_aborted_expired_and_foreign_uploads(api, keys, monkeypatch):
    c, h = api.c, keys["rw"]
    from synwevo_docs.engine.limits import LIMITS
    big = c.post("/api/v1/uploads", json={"filename": "big.xlsx", "size": LIMITS.max_file_bytes + 1}, headers=h)
    assert big.status_code == 413 and big.json()["error"]["field"] == "size"
    for body, field in (({"filename": "", "size": 1}, "filename"), ({"filename": "a", "size": 0}, "size"),
                        ({"filename": "a", "size": 1, "sha256": "zz"}, "sha256"),
                        ({"filename": "a", "size": 1, "purpose": "other"}, "purpose")):
        r = c.post("/api/v1/uploads", json=body, headers=h)
        assert r.status_code == 400 and r.json()["error"]["field"] == field, body
    pid = _ok(c.post("/api/v1/projects", json={"name": "拒绝"}, headers=h))["id"]

    aborted = _new(c, h, b"abc")
    _put(c, h, aborted["id"], b"abc", 0, 3)
    _ok(c.post(f"/api/v1/uploads/{aborted['id']}/abort", headers=h))
    assert not U.part_path(c.app_state.app.store, aborted["id"]).exists()
    r = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": aborted["id"]}, headers=h)
    assert r.status_code == 410 and r.json()["error"]["code"] == "upload_aborted"
    assert _put(c, h, aborted["id"], b"abc", 0, 3).status_code == 410

    expired = _new(c, h, b"abc")
    _put(c, h, expired["id"], b"abc", 0, 3)
    _ok(c.post(f"/api/v1/uploads/{expired['id']}/complete", json={}, headers=h))
    store = c.app_state.app.store
    store.execute("UPDATE uploads SET expires_at=? WHERE id=?", (time.time() - 1, expired["id"]))
    r = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": expired["id"]}, headers=h)
    assert r.status_code == 410 and r.json()["error"]["code"] == "upload_expired"

    # another workspace cannot see, write, complete or use this upload
    mine = _new(c, h, b"abc")
    _put(c, h, mine["id"], b"abc", 0, 3)
    _ok(c.post(f"/api/v1/uploads/{mine['id']}/complete", json={}, headers=h))
    o = keys["other"]
    assert c.get(f"/api/v1/uploads/{mine['id']}", headers=o).status_code == 404
    assert _put(c, o, mine["id"], b"abc", 0, 3).status_code == 404
    assert c.post(f"/api/v1/uploads/{mine['id']}/abort", headers=o).status_code == 404
    theirs = _ok(c.post("/api/v1/projects", json={"name": "别人的"}, headers=o))["id"]
    r = c.post(f"/api/v1/projects/{theirs}/files", json={"upload_id": mine["id"]}, headers=o)
    assert r.status_code == 404
    assert _ok(c.get(f"/api/v1/uploads/{mine['id']}", headers=h))["status"] == "complete"
    # and path and upload_id cannot be combined
    r = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": mine["id"], "path": "/tmp/x"}, headers=h)
    assert r.status_code == 400


def test_cleanup_expires_old_uploads_and_removes_temp_files(api, keys):
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    stale = _new(c, h, b"abcdef")
    _put(c, h, stale["id"], b"abc", 0, 6)
    fresh = _new(c, h, b"abcdef")
    store.execute("UPDATE uploads SET expires_at=? WHERE id=?", (time.time() - 1, stale["id"]))
    assert U.cleanup_expired(store) == 1
    assert store.one("SELECT status FROM uploads WHERE id=?", (stale["id"],))["status"] == "expired"
    assert not U.part_path(store, stale["id"]).exists()
    assert U.part_path(store, fresh["id"]).exists(), "未过期的上传不能被清理"
    # a later cleanup at "tomorrow" expires the rest
    assert U.cleanup_expired(store, time.time() + U.UPLOAD_TTL_S + 1) == 1


def test_open_upload_quota(api, keys, monkeypatch):
    c, h = api.c, keys["rw"]
    monkeypatch.setattr(U, "MAX_OPEN_UPLOADS", 2)
    _new(c, h, b"a")
    _new(c, h, b"a")
    r = c.post("/api/v1/uploads", json={"filename": "a", "size": 1}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "too_many_open_uploads"


def test_template_from_upload(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    data = (fixtures / "测试_合同模板.docx").read_bytes()
    up = _new(c, h, data, name="合同.docx", purpose="template")
    _put(c, h, up["id"], data, 0, len(data))
    _ok(c.post(f"/api/v1/uploads/{up['id']}/complete", json={"sha256": hashlib.sha256(data).hexdigest()}, headers=h))
    t = c.post("/api/v1/templates", json={"upload_id": up["id"], "name": "上传的合同"}, headers=h)
    assert t.status_code == 201, t.text
    assert t.json()["name"] == "上传的合同" and t.json()["format"] == "docx"
    # a failed use does not consume the upload
    bad = _new(c, h, b"not a template", name="x.docx")
    _put(c, h, bad["id"], b"not a template", 0, 14)
    _ok(c.post(f"/api/v1/uploads/{bad['id']}/complete", json={}, headers=h))
    r = c.post("/api/v1/templates", json={"upload_id": bad["id"]}, headers=h)
    assert r.status_code == 400
    assert _ok(c.get(f"/api/v1/uploads/{bad['id']}", headers=h))["status"] == "complete"


def test_multipart_checksum_is_verified(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "校验"}, headers=h))["id"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    good = hashlib.sha256(data).hexdigest()
    wrong = hashlib.sha256(b"other").hexdigest()
    r = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("a.xlsx", data)}, data={"sha256": wrong}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "checksum_mismatch"
    r = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("a.xlsx", data)}, headers={**h, "X-Content-SHA256": wrong})
    assert r.status_code == 400 and r.json()["error"]["field"] == "sha256"
    assert c.app_state.app.store.one("SELECT COUNT(*) AS n FROM source_files WHERE project_id=?", (pid,))["n"] == 0
    r = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("a.xlsx", data)}, data={"sha256": good},
               headers={**h, "X-Content-SHA256": good.upper()})
    assert r.status_code == 201 and r.json()["sha256"] == good
    tpl = (fixtures / "测试_合同模板.docx").read_bytes()
    r = c.post("/api/v1/templates", files={"file": ("t.docx", tpl)}, headers={**h, "X-Content-SHA256": wrong})
    assert r.status_code == 400 and r.json()["error"]["code"] == "checksum_mismatch"
