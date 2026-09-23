"""Machine API (/api/v1): authentication, tenant isolation, the full headless flow,
declarative definitions, idempotency, signed links and error contracts."""
import io
import time
import zipfile

import docx
import pytest

from docweave.auth import Auth


@pytest.fixture()
def keys(client):
    auth = Auth(client.app_state.app.store)
    tok_rw, _ = auth.create_key("rw", "default", ("read", "write"))
    tok_ro, _ = auth.create_key("ro", "default", ("read",))
    tok_other, _ = auth.create_key("other", "tenant-b", ("read", "write", "admin"))
    return {"rw": {"Authorization": f"Bearer {tok_rw}"}, "ro": {"Authorization": f"Bearer {tok_ro}"},
            "other": {"Authorization": f"Bearer {tok_other}"}, "auth": auth}


def _ok(r):
    assert r.status_code < 400, (r.status_code, r.text[:600])
    return r.json()


def test_authentication_and_scopes(api, keys):
    c = api.c
    assert c.get("/api/v1/capabilities").status_code == 401
    assert c.get("/api/v1/capabilities", headers={"Authorization": "Bearer dw_bad_key"}).status_code == 401
    body = c.get("/api/v1/capabilities", headers={"Authorization": "Bearer nonsense"}).json()
    assert body["error"]["code"] == "unauthorized" and body["error"]["request_id"]
    caps = _ok(c.get("/api/v1/capabilities", headers=keys["rw"]))
    assert caps["api_version"] and caps["rows"] and caps["limits"]["max_records_per_job"]
    who = _ok(c.get("/api/v1/whoami", headers=keys["ro"]))
    assert who["tenant"] == "default" and who["scopes"] == ["read"]
    r = c.post("/api/v1/projects", json={"name": "x"}, headers=keys["ro"])
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    # a revoked key stops working immediately
    tok, row = keys["auth"].create_key("temp", "default", ("read",))
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/api/v1/projects", headers=h).status_code == 200
    keys["auth"].revoke_key(row["id"])
    assert c.get("/api/v1/projects", headers=h).json()["error"]["code"] == "key_revoked"


def test_tenant_isolation(api, keys, fixtures):
    c = api.c
    mine = _ok(c.post("/api/v1/projects", json={"name": "我的"}, headers=keys["rw"]))
    theirs = _ok(c.post("/api/v1/projects", json={"name": "别人的"}, headers=keys["other"]))
    assert c.get(f"/api/v1/projects/{theirs['id']}", headers=keys["rw"]).status_code == 404   # not 403: existence is not leaked
    assert c.get(f"/api/v1/projects/{mine['id']}", headers=keys["other"]).status_code == 404
    names = [p["name"] for p in _ok(c.get("/api/v1/projects", headers=keys["other"]))["items"]]
    assert names == ["别人的"]
    # a UI-created project belongs to the UI tenant (default) and is reusable by default-tenant keys
    ui_pid = api.project("界面项目")
    assert _ok(c.get(f"/api/v1/projects/{ui_pid}", headers=keys["rw"]))["name"] == "界面项目"
    assert c.get(f"/api/v1/projects/{ui_pid}", headers=keys["other"]).status_code == 404


def test_headless_end_to_end(api, keys, fixtures):
    """Upload → declarative fields → rule → mappings → preflight → job → wait → download."""
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "自动化合同"}, headers=h))["id"]
    with open(fixtures / "测试_客户100.xlsx", "rb") as f:
        src = _ok(c.post(f"/api/v1/projects/{pid}/files", files={"file": ("测试_客户100.xlsx", f.read())}, headers=h))
    for _ in range(100):
        src = _ok(c.get(f"/api/v1/files/{src['id']}", headers=h))
        if src["status"] != "parsing":
            break
        time.sleep(0.05)
    assert src["status"] == "ready" and src["structure"]["sheets"][0]["name"] == "客户"
    with open(fixtures / "测试_合同模板.docx", "rb") as f:
        tpl = _ok(c.post("/api/v1/templates", files={"file": ("测试_合同模板.docx", f.read())}, headers=h))
    fields = _ok(c.post(f"/api/v1/templates/{tpl['id']}/fields", headers=h, json={
        "schema": "docweave.template-fields", "version": 1,
        "fields": [{"kind": "text", "name": "客户名称", "find": "示例客户名称"},
                   {"kind": "text", "name": "合同编号", "find": "HT-0000"},
                   {"kind": "text", "name": "金额", "find": "0.00"}]}))
    assert [f["name"] for f in fields["created"]] == ["客户名称", "合同编号", "金额"]
    _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tpl["id"]}, headers=h))
    rule = _ok(c.put(f"/api/v1/projects/{pid}/rule", headers=h,
                     json={"rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 5}}}))
    assert rule["records"] == 5
    slots = {f["name"]: f for f in _ok(c.get(f"/api/v1/templates/{tpl['id']}", headers=h))["fields"]}
    maps = [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": col}],
             "target": {"binding": slots[name]["binding"]}} for name, col in (("客户名称", "姓名"), ("合同编号", "客户编号"), ("金额", "金额"))]
    saved = _ok(c.put(f"/api/v1/projects/{pid}/mappings", json={"mappings": maps}, headers=h))
    assert len(saved["mappings"]) == 3 and saved["rev"] >= 1
    assert c.put(f"/api/v1/projects/{pid}/mappings", json={"mappings": maps, "base_rev": 0}, headers=h).status_code == 409
    pre = _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={"formats": ["docx"], "naming": "{合同编号}"}, headers=h))
    assert pre["records"] == 5 and pre["valid"] == 5 and pre["expected_outputs"] == 5 and not pre["static_issues"]
    job = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"], "naming": "{合同编号}"},
                     headers={**h, "Idempotency-Key": "run-1"}))
    assert job["status"] in ("queued", "running") and job["counts"]["samples"] == 5
    for _ in range(600):
        job = _ok(c.get(f"/api/v1/jobs/{job['id']}", headers=h))
        if job["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed" and job["counts"]["documents"] == 5 and job["counts"]["files"] == 5
    arts = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))
    assert arts["total"] == 5 and all(a["sha256"] and a["size"] for a in arts["items"])
    one = arts["items"][0]
    content = c.get(f"/api/v1/artifacts/{one['id']}/content", headers=h)
    assert content.status_code == 200 and content.headers["X-Content-SHA256"] == one["sha256"]
    import hashlib
    assert hashlib.sha256(content.content).hexdigest() == one["sha256"]
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(content.content)).paragraphs)
    assert "000001" in one["filename"] and "测试" in text
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/v1/jobs/{job['id']}/archive", headers=h).content))
    assert len([n for n in z.namelist() if n.endswith(".docx")]) == 5
    events = _ok(c.get(f"/api/v1/jobs/{job['id']}/events", headers=h))
    assert [e["type"] for e in events["items"]][:2] == ["job.created", "job.started"] and events["items"][-1]["type"] == "job.completed"
    assert all(e["version"] == 1 and e["id"] and e["occurred_at"] for e in events["items"])
    later = _ok(c.get(f"/api/v1/jobs/{job['id']}/events", params={"after": events["next_after"]}, headers=h))
    assert later["items"] == []
    # the definition round-trips into a second project
    definition = _ok(c.get(f"/api/v1/projects/{pid}/definition", headers=h))
    pid2 = _ok(c.post("/api/v1/projects", json={"name": "复用"}, headers=h))["id"]
    with open(fixtures / "测试_客户100.xlsx", "rb") as f:
        _ok(c.post(f"/api/v1/projects/{pid2}/files", files={"file": ("测试_客户100.xlsx", f.read())}, headers=h))
    api.wait_parsed(pid2)
    _ok(c.put(f"/api/v1/projects/{pid2}/definition", json=definition, headers=h))
    pre2 = _ok(c.post(f"/api/v1/projects/{pid2}/preflight", json={"formats": ["docx"]}, headers=h))
    assert pre2["valid"] == 5 and pre2["template_version"] == definition["template"]["version"]


def test_idempotent_job_submission_over_http(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    j1 = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers={**h, "Idempotency-Key": "abc"}))
    j2 = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers={**h, "Idempotency-Key": "abc"}))
    assert j1["id"] == j2["id"] and j2["idempotent_replay"]
    conflict = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"], "naming": "{序号}-x"},
                      headers={**h, "Idempotency-Key": "abc"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"


def test_signed_links_and_expiry(api, keys, fixtures, monkeypatch):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]
    link = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=h))
    path = link["url"].split("/api/v1/", 1)[1]
    r = c.get(f"/api/v1/{path}")                      # no Authorization header: the link is the credential
    assert r.status_code == 200 and r.content[:2] == b"PK" and r.headers["X-Content-SHA256"] == art["sha256"]
    zlink = _ok(c.post(f"/api/v1/jobs/{job['id']}/archive-link", headers=h))
    assert c.get("/api/v1/" + zlink["url"].split("/api/v1/", 1)[1]).status_code == 200
    assert c.get(f"/api/v1/download/{path.split('/')[-1][:-4]}tampered").json()["error"]["code"] in ("bad_link", "link_expired")
    expired, _ = keys["auth"].sign_link("artifact", art["id"], "default", ttl_s=-5)
    assert c.get(f"/api/v1/download/{expired}").json()["error"]["code"] == "link_expired"
    # a deleted artifact reports 410, and a fresh link can always be requested again
    api.c.app_state.app.store.execute("UPDATE artifacts SET deleted_at=? WHERE id=?", (time.time(), art["id"]))
    assert c.get(f"/api/v1/artifacts/{art['id']}/content", headers=h).status_code == 410


def test_path_upload_is_restricted(api, keys, fixtures, monkeypatch, tmp_path):
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "路径"}, headers=h))["id"]
    assert c.post(f"/api/v1/projects/{pid}/files", json={"path": str(fixtures / "测试_客户100.xlsx")}, headers=h).status_code == 400
    import docweave.api.v1 as v1
    monkeypatch.setattr(v1, "IMPORT_ROOTS", [str(tmp_path)])
    assert c.post(f"/api/v1/projects/{pid}/files", json={"path": "/etc/hosts"}, headers=h).status_code == 403
    assert c.post(f"/api/v1/projects/{pid}/files", json={"path": str(tmp_path / "nope.xlsx")}, headers=h).status_code == 404
    import shutil
    shutil.copy(fixtures / "测试_客户100.xlsx", tmp_path / "ok.xlsx")
    got = _ok(c.post(f"/api/v1/projects/{pid}/files", json={"path": str(tmp_path / "ok.xlsx")}, headers=h))
    assert got["filename"] == "ok.xlsx"
    outside = tmp_path / "sub"
    outside.mkdir()
    (outside / "deep.xlsx").write_bytes((fixtures / "测试_客户100.xlsx").read_bytes())
    assert c.post(f"/api/v1/projects/{pid}/files", json={"path": str(outside / "deep.xlsx")}, headers=h).status_code == 201


def test_definition_validation_rejects_unknown_and_incompatible(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    with open(fixtures / "测试_合同模板.docx", "rb") as f:
        tpl = _ok(c.post("/api/v1/templates", files={"file": ("t.docx", f.read())}, headers=h))
    base = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "x", "find": "示例客户名称"}]}
    assert c.post(f"/api/v1/templates/{tpl['id']}/fields", json={**base, "version": 99}, headers=h).json()["error"]["field"] == "version"
    assert c.post(f"/api/v1/templates/{tpl['id']}/fields", json={**base, "schema": "other"}, headers=h).status_code == 400
    bad_key = {**base, "fields": [{"kind": "text", "name": "x", "find": "示例客户名称", "colour": "red"}]}
    err = c.post(f"/api/v1/templates/{tpl['id']}/fields", json=bad_key, headers=h).json()["error"]
    assert "colour" in err["message"]
    missing = {**base, "fields": [{"kind": "text", "name": "x", "find": "这段文字不存在"}]}
    assert c.post(f"/api/v1/templates/{tpl['id']}/fields", json=missing, headers=h).json()["error"]["field"] == "find"
    ok = _ok(c.post(f"/api/v1/templates/{tpl['id']}/fields", json=base, headers=h))
    assert ok["created"][0]["kind"] == "text"
    export = _ok(c.get(f"/api/v1/templates/{tpl['id']}/fields", headers=h))
    assert export["schema"] == "docweave.template-fields" and export["fields"][0]["name"] == "x"


def test_rate_limit_and_request_id(api, keys, monkeypatch):
    import docweave.api.app as A
    c = api.c
    rid = c.get("/api/v1/whoami", headers={**keys["rw"], "X-Request-ID": "trace-123"})
    assert rid.headers["X-Request-ID"] == "trace-123"
    limiter = A.rate_limiter(2)
    monkeypatch.setattr(api.c.app_state.app, "_test_limiter", limiter, raising=False)
    # the limiter itself: two tokens then refusal with a wait hint
    assert limiter("k")[0] and limiter("k")[0]
    ok, wait = limiter("k")
    assert not ok and wait > 0


def test_ui_endpoints_stay_anonymous_but_v1_never_is(api, keys):
    c = api.c
    assert c.get("/api/projects").status_code == 200          # local UI (loopback deployment)
    assert c.get("/api/v1/projects").status_code == 401
    assert c.get("/healthz").status_code == 200
    ready = c.get("/readyz")
    assert ready.status_code == 200 and ready.json()["checks"]["worker"] == "ok"
    assert c.get("/api/docs").status_code == 404 and c.get("/api/openapi.json").status_code == 404


def _prepared(api, keys, fixtures) -> str:
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "准备"}, headers=h))["id"]
    with open(fixtures / "测试_客户100.xlsx", "rb") as f:
        _ok(c.post(f"/api/v1/projects/{pid}/files", files={"file": ("测试_客户100.xlsx", f.read())}, headers=h))
    api.wait_parsed(pid)
    with open(fixtures / "测试_合同模板.docx", "rb") as f:
        tpl = _ok(c.post("/api/v1/templates", files={"file": ("测试_合同模板.docx", f.read())}, headers=h))
    _ok(c.post(f"/api/v1/templates/{tpl['id']}/fields", headers=h, json={
        "schema": "docweave.template-fields", "version": 1,
        "fields": [{"kind": "text", "name": "客户名称", "find": "示例客户名称"}]}))
    _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tpl["id"]}, headers=h))
    _ok(c.put(f"/api/v1/projects/{pid}/rule", headers=h,
              json={"rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 3}}}))
    slot = _ok(c.get(f"/api/v1/templates/{tpl['id']}", headers=h))["fields"][0]
    _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"mappings": [
        {"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": slot["binding"]}}]}))
    return pid


def _wait(api, keys, jid: str) -> dict:
    for _ in range(600):
        job = _ok(api.c.get(f"/api/v1/jobs/{jid}", headers=keys["rw"]))
        if job["status"] not in ("queued", "running", "cancelling"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_every_response_carries_the_trace_id(api, keys):
    """docs/integration/api.md: 每个响应都带 X-Request-ID – including the ones the
    middleware returns before the route runs (DW-048: 401 used to skip the header)."""
    c = api.c
    r = c.get("/api/v1/capabilities", headers=keys["rw"])            # authorised
    assert r.status_code == 200 and r.headers.get("X-Request-ID")

    r = c.get("/api/v1/capabilities")                                # rejected in the middleware
    assert r.status_code == 401
    assert r.headers.get("X-Request-ID"), "鉴权失败的响应也必须带追踪 ID"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"], "头与响应体的追踪 ID 必须一致"

    mine = "my-own-trace-1234"                                       # a caller-supplied id is echoed back
    r = c.get("/api/v1/capabilities", headers={"X-Request-ID": mine})
    assert r.headers.get("X-Request-ID") == mine
    assert r.json()["error"]["request_id"] == mine


def test_signed_link_origin_survives_a_reverse_proxy(api, keys, fixtures, monkeypatch):
    """A download link is a credential, so the URL handed back must be the one the caller used.

    uvicorn builds request.base_url from the immediate connection, so behind a TLS-terminating
    proxy it reports http://, and behind a proxy that rewrites Host (nginx's $proxy_host default)
    it reports the origin's own address — handing the caller a token on a plaintext URL that
    points at their own machine.
    """
    import docweave.api.v1 as v1
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]

    def link(extra):
        return _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers={**h, **extra}))["url"]

    # the scheme the user actually used, not the proxy→origin hop
    assert link({"Host": "dw.example.com", "X-Forwarded-Proto": "https"}).startswith("https://dw.example.com/api/v1/download/")
    # a proxy that rewrote Host still must not produce a loopback URL, if it forwarded the real one
    assert link({"Host": "127.0.0.1:8765", "X-Forwarded-Host": "dw.example.com",
                 "X-Forwarded-Proto": "https"}).startswith("https://dw.example.com/")
    # comma-joined values from a chain of proxies: the first entry is the client-facing one
    assert link({"X-Forwarded-Host": "dw.example.com, inner.local",
                 "X-Forwarded-Proto": "https, http"}).startswith("https://dw.example.com/")
    # an explicit public URL wins over anything a client claims, including a spoofed Host
    monkeypatch.setattr(v1, "PUBLIC_URL", "https://docs.example.org")
    assert link({"Host": "evil.example", "X-Forwarded-Proto": "http"}) \
        .startswith("https://docs.example.org/api/v1/download/")
    monkeypatch.setattr(v1, "PUBLIC_URL", "")
    # no proxy at all keeps working
    assert link({}).startswith("http://testserver/api/v1/download/")

    # whatever the URL says, the token is verified on its own and still downloads
    token = link({"Host": "dw.example.com", "X-Forwarded-Proto": "https"}).rsplit("/", 1)[-1]
    got = c.get(f"/api/v1/download/{token}")
    assert got.status_code == 200 and got.headers["X-Content-SHA256"] == art["sha256"]
