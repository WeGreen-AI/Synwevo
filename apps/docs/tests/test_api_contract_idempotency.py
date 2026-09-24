"""/api/v1 contract: Idempotency-Key on every creating POST (R26) and the deprecated body keys (R35).

A network retry must not create a second project, a second source file or a second template;
a reused key with a different body must be refused rather than silently answered with the first
result; and a malformed key must be refused with the header named.
"""
import re

from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401  (keys is a fixture)


def _count(api, sql, args=()):
    return api.c.app_state.app.store.one(sql, args)["n"]


def test_create_project_replays_and_conflicts(api, keys):
    c, h = api.c, keys["rw"]
    before = _ok(c.get("/api/v1/projects", headers=h))["total"]
    k = {**h, "Idempotency-Key": "proj-k1"}
    first = c.post("/api/v1/projects", json={"name": "幂等项目"}, headers=k)
    again = c.post("/api/v1/projects", json={"name": "幂等项目"}, headers=k)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert again.json() == first.json(), "重放必须与首次响应完全相同"
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert _ok(c.get("/api/v1/projects", headers=h))["total"] == before + 1
    # same key, different body: refused, and nothing new is created
    clash = c.post("/api/v1/projects", json={"name": "另一个名字"}, headers=k)
    assert clash.status_code == 409
    err = clash.json()["error"]
    assert err["code"] == "idempotency_conflict" and err["field"] == "Idempotency-Key"
    assert err["details"]["resource_id"] == first.json()["id"]
    assert _ok(c.get("/api/v1/projects", headers=h))["total"] == before + 1
    # key order and whitespace in the body do not change the fingerprint
    a = c.post("/api/v1/projects", content=b'{"name": "x", "extra": 1}',
               headers={**h, "Idempotency-Key": "proj-k2", "Content-Type": "application/json"})
    b = c.post("/api/v1/projects", content=b'{"extra":1,"name":"x"}',
               headers={**h, "Idempotency-Key": "proj-k2", "Content-Type": "application/json"})
    assert a.json()["id"] == b.json()["id"] and b.headers["Idempotent-Replayed"] == "true"


def test_keys_are_scoped_by_workspace_and_route(api, keys):
    c = api.c
    mine = _ok(c.post("/api/v1/projects", json={"name": "同键"}, headers={**keys["rw"], "Idempotency-Key": "shared"}))
    theirs = c.post("/api/v1/projects", json={"name": "同键"}, headers={**keys["other"], "Idempotency-Key": "shared"})
    # another workspace's identical key is a different key: a new project, and not a replay of ours
    assert theirs.status_code == 201 and theirs.json()["id"] != mine["id"]
    assert "Idempotent-Replayed" not in theirs.headers
    # the same key on another route is independent too
    up = c.post("/api/v1/uploads", json={"filename": "a.xlsx", "size": 10}, headers={**keys["rw"], "Idempotency-Key": "shared"})
    assert up.status_code == 201 and up.json()["id"].startswith("up_")


def test_malformed_keys_are_refused_with_the_header_named(api, keys):
    c, h = api.c, keys["rw"]
    for bad in ("k" * 256, "tab\there", "ümlaut"):
        try:
            r = c.post("/api/v1/projects", json={"name": "x"}, headers={**h, "Idempotency-Key": bad})
        except UnicodeEncodeError:
            continue                            # the HTTP client itself refuses non-latin-1 headers
        assert r.status_code == 400, bad
        assert r.json()["error"]["code"] == "invalid_idempotency_key"
        assert r.json()["error"]["field"] == "Idempotency-Key"
    assert c.post("/api/v1/projects", json={"name": "x"}, headers={**h, "Idempotency-Key": "k" * 255}).status_code == 201
    # the same validation applies on job submission
    pid = _ok(c.post("/api/v1/projects", json={"name": "j"}, headers=h))["id"]
    r = c.post(f"/api/v1/projects/{pid}/jobs", json={}, headers={**h, "Idempotency-Key": "k" * 256})
    assert r.status_code == 400 and r.json()["error"]["field"] == "Idempotency-Key"


def test_a_failed_request_does_not_burn_the_key(api, keys):
    """Only successes are stored: a 404 did nothing, so the corrected request must run."""
    c, h = api.c, keys["rw"]
    k = {**h, "Idempotency-Key": "fields-after-fix"}
    body = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "x", "find": "示例客户名称"}]}
    assert c.post("/api/v1/templates/t_000000000000/fields", json=body, headers=k).status_code == 404
    assert _count(api, "SELECT COUNT(*) AS n FROM idempotency WHERE key=?", ("fields-after-fix",)) == 0


def test_file_upload_with_one_key_creates_one_source(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "上传幂等"}, headers=h))["id"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    k = {**h, "Idempotency-Key": "upload-1"}
    r1 = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data)}, headers=k)
    r2 = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data)}, headers=k)
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert _count(api, "SELECT COUNT(*) AS n FROM source_files WHERE project_id=? AND removed=0", (pid,)) == 1
    # a different file under the same key is a conflict, not a replay of the first file
    other = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data + b"x")}, headers=k)
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_conflict"
    # the same key for another project is a conflict as well (path parameters are fingerprinted)
    pid2 = _ok(c.post("/api/v1/projects", json={"name": "上传幂等2"}, headers=h))["id"]
    assert c.post(f"/api/v1/projects/{pid2}/files", files={"file": ("客户.xlsx", data)}, headers=k).status_code == 409
    assert _count(api, "SELECT COUNT(*) AS n FROM source_files WHERE project_id=?", (pid2,)) == 0


def test_template_upload_fields_and_links_replay(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    data = (fixtures / "测试_合同模板.docx").read_bytes()
    k = {**h, "Idempotency-Key": "tpl-1"}
    t1 = c.post("/api/v1/templates", files={"file": ("合同.docx", data)}, headers=k)
    t2 = c.post("/api/v1/templates", files={"file": ("合同.docx", data)}, headers=k)
    assert t1.status_code == t2.status_code == 201 and t1.json()["id"] == t2.json()["id"]
    assert _count(api, "SELECT COUNT(*) AS n FROM templates WHERE original_name=?", ("合同.docx",)) == 1
    tid = t1.json()["id"]
    body = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "客户", "find": "示例客户名称"}]}
    fk = {**h, "Idempotency-Key": "fields-1"}
    f1 = c.post(f"/api/v1/templates/{tid}/fields", json=body, headers=fk)
    f2 = c.post(f"/api/v1/templates/{tid}/fields", json=body, headers=fk)
    assert f1.status_code == f2.status_code == 200 and f1.json() == f2.json()
    assert len(_ok(c.get(f"/api/v1/templates/{tid}", headers=h))["fields"]) == 1, "重试不应重复创建字段"

    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]
    lk = {**h, "Idempotency-Key": "link-1"}
    l1 = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=lk))
    l2 = c.post(f"/api/v1/artifacts/{art['id']}/link", headers=lk)
    assert l2.json()["url"] == l1["url"] and l2.headers["Idempotent-Replayed"] == "true"
    z1 = _ok(c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={}, headers={**h, "Idempotency-Key": "zip-1"}))
    z2 = c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={}, headers={**h, "Idempotency-Key": "zip-1"})
    assert z2.json()["url"] == z1["url"]
    # a replayed link is only replayed while the link itself is alive
    store = c.app_state.app.store
    store.execute("UPDATE idempotency SET created_at=created_at-100000 WHERE key='link-1'")
    l3 = c.post(f"/api/v1/artifacts/{art['id']}/link", headers=lk)
    assert l3.status_code == 200 and "Idempotent-Replayed" not in l3.headers


def test_job_submission_signals_replay_and_deprecated_body_keys(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    k = {**h, "Idempotency-Key": "job-hdr"}
    j1 = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=k)
    j2 = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=k)
    assert j1.status_code == j2.status_code == 202 and j1.json()["id"] == j2.json()["id"]
    assert j2.headers["Idempotent-Replayed"] == "true" and j2.json()["idempotent_replay"] is True
    # the header alone is the supported form: no deprecation signal
    assert "Deprecation" not in j1.headers and "Deprecation" not in j2.headers
    _wait(api, keys, j1.json()["id"])
    # the body key still works, and says it is deprecated (RFC 9745 + a rel=deprecation link)
    for field in ("idempotency_key", "request_id"):
        r = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"], field: f"body-{field}"}, headers=h)
        assert r.status_code == 202, r.text
        assert re.fullmatch(r"@\d+", r.headers["Deprecation"])
        assert 'rel="deprecation"' in r.headers["Link"]
        again = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"], field: f"body-{field}"}, headers=h)
        assert again.json()["id"] == r.json()["id"] and again.headers["Idempotent-Replayed"] == "true"
        _wait(api, keys, r.json()["id"])
    bad = c.post(f"/api/v1/projects/{pid}/jobs", json={"idempotency_key": "x" * 300}, headers=h)
    assert bad.status_code == 400 and bad.json()["error"]["field"] == "idempotency_key"


def test_sunset_is_never_earlier_than_deprecation(monkeypatch):
    import synwevo_docs.api.v1 as v1
    entry = dict(v1.DEPRECATIONS["job-body-idempotency-key"])
    monkeypatch.setitem(v1.DEPRECATIONS, "job-body-idempotency-key", {**entry, "sunset": entry["since"] + 86400 * 180})
    hs = v1.deprecation_headers("job-body-idempotency-key")
    from email.utils import parsedate_to_datetime
    assert parsedate_to_datetime(hs["Sunset"]).timestamp() >= int(hs["Deprecation"][1:])
    monkeypatch.setitem(v1.DEPRECATIONS, "job-body-idempotency-key", {**entry, "sunset": entry["since"] - 1})
    import pytest
    with pytest.raises(RuntimeError):
        v1.deprecation_headers("job-body-idempotency-key")


def test_legacy_incomplete_claim_never_permits_duplicate_work(api, keys):
    """A legacy claim may have committed work already; age cannot make takeover safe."""
    import time as _t
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    from synwevo_docs.api import idempotency as I
    fp = I.fingerprint({"body": {"name": "并发"}})
    k = store.one("SELECT id, lineage FROM api_keys WHERE name='rw'")
    scope = I.scope_of("POST", "/api/v1/projects", f"api:{k['lineage'] or k['id']}")   # this caller's namespace
    before = _count(api, "SELECT COUNT(*) AS n FROM projects")
    store.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, created_at) VALUES (?,?,?,?,?)",
                  ("default", scope, "busy-1", fp, _t.time()))
    r = c.post("/api/v1/projects", json={"name": "并发"}, headers={**h, "Idempotency-Key": "busy-1"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_in_progress"
    # Neither the stale window nor response TTL proves that the original work rolled back.
    for age in (I.IN_FLIGHT_STALE_S + 5, I.TTL_S + I.IN_FLIGHT_STALE_S + 5):
        store.execute("UPDATE idempotency SET created_at=? WHERE key='busy-1'", (_t.time() - age,))
        original = store.one("SELECT * FROM idempotency WHERE key='busy-1'")
        for name in ("并发", "changed payload"):
            r = c.post("/api/v1/projects", json={"name": name}, headers={**h, "Idempotency-Key": "busy-1"})
            assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_outcome_unknown"
            assert store.one("SELECT * FROM idempotency WHERE key='busy-1'") == original
            assert _count(api, "SELECT COUNT(*) AS n FROM projects") == before


def test_purge_removes_only_expired_route_records(api, keys):
    import time as _t

    from synwevo_docs.api import idempotency as I
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    _ok(c.post("/api/v1/projects", json={"name": "旧"}, headers={**h, "Idempotency-Key": "old-1"}))
    _ok(c.post("/api/v1/projects", json={"name": "新"}, headers={**h, "Idempotency-Key": "new-1"}))
    store.execute("UPDATE idempotency SET created_at=? WHERE key='old-1'", (_t.time() - I.TTL_S - 60,))
    # a job-scoped row belongs to JobService and is left alone even when old
    store.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, created_at) VALUES (?,?,?,?,?)",
                  ("default", "job:p_x", "job-old", "h", _t.time() - I.TTL_S - 60))
    for method in ("POST", "PUT"):
        store.execute("INSERT INTO idempotency (tenant, scope, key, request_hash, created_at) VALUES (?,?,?,?,?)",
                      ("default", f"{method} /api/v1/projects", f"unknown-{method}", "h", _t.time() - I.TTL_S - 60))
    assert I.purge_expired(store) == 1
    keys_left = {r["key"] for r in store.all("SELECT key FROM idempotency")}
    assert "old-1" not in keys_left and {"new-1", "job-old", "unknown-POST", "unknown-PUT"} <= keys_left


def test_deleted_project_is_not_disclosed_by_cached_creation(api, keys):
    c, h = api.c, keys["rw"]
    k = {**h, "Idempotency-Key": "deleted-project"}
    body = {"name": "synthetic deleted project"}
    original = _ok(c.post("/api/v1/projects", json=body, headers=k))
    # Deletion is an admin operation; perform it through the real service fixture, then
    # retry as the original write-only API caller.
    c.app_state.app.delete_project(original["id"])
    denied = c.post("/api/v1/projects", json=body, headers=k)
    assert denied.status_code == 404
    assert "Idempotent-Replayed" not in denied.headers
    assert original["name"] not in denied.text and original["id"] not in denied.text
    assert _count(api, "SELECT COUNT(*) AS n FROM projects") == 1
    assert _count(api, "SELECT COUNT(*) AS n FROM idempotency WHERE key='deleted-project'") == 1


def test_changed_payload_cannot_disclose_deleted_project(api, keys):
    c, h = api.c, keys["rw"]
    k = {**h, "Idempotency-Key": "deleted-project-conflict"}
    body = {"name": "synthetic conflict visibility fixture"}
    changed = {"name": "synthetic replacement request"}
    original = _ok(c.post("/api/v1/projects", json=body, headers=k))
    visible_conflict = c.post("/api/v1/projects", json=changed, headers=k)
    assert visible_conflict.status_code == 409
    assert visible_conflict.json()["error"]["code"] == "idempotency_conflict"
    assert visible_conflict.json()["error"]["details"]["resource_id"] == original["id"]

    c.app_state.app.delete_project(original["id"])
    store = c.app_state.app.store
    cached = store.one("SELECT * FROM idempotency WHERE key=?", (k["Idempotency-Key"],))
    # Both conflict metadata and ordinary replay must first respect current visibility.
    for payload in (changed, body):
        denied = c.post("/api/v1/projects", json=payload, headers=k)
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "not_found"
        assert "resource_id" not in denied.text
        assert original["id"] not in denied.text and original["name"] not in denied.text
        assert "Idempotent-Replayed" not in denied.headers
        assert store.one("SELECT * FROM idempotency WHERE key=?", (k["Idempotency-Key"],)) == cached
        assert _count(api, "SELECT COUNT(*) AS n FROM projects") == 1
