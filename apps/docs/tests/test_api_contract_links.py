"""/api/v1 contract: a signed download link is re-authorised at download time (contract 2).

A link is a bearer credential that outlives the request that minted it. Revoking the key that
signed it, or freezing the workspace, must end the link now — not when it expires.
"""
from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401  (keys is a fixture)


def _done_job(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]
    return job, art


def _path(url: str) -> str:
    return "/api/v1/" + url.split("/api/v1/", 1)[1]


def test_revoking_the_issuing_key_ends_its_links(api, keys, fixtures):
    c, auth = api.c, keys["auth"]
    job, art = _done_job(api, keys, fixtures)
    tok, row = auth.create_key("link-minter", "default", ("read",))
    mh = {"Authorization": f"Bearer {tok}"}
    link = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=mh))["url"]
    zlink = _ok(c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={}, headers=mh))["url"]
    assert c.get(_path(link)).status_code == 200 and c.get(_path(zlink)).status_code == 200
    # a link minted by another, still valid key is unaffected by this revocation
    other = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=keys["rw"]))["url"]

    auth.revoke_key(row["id"])
    for url in (link, zlink):
        r = c.get(_path(url))
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "link_revoked"
        assert not r.content.startswith(b"PK"), "撤销后不得再返回文件内容"
    ok = c.get(_path(other))
    assert ok.status_code == 200 and ok.headers["X-Content-SHA256"] == art["sha256"]


def test_frozen_workspace_ends_links(api, keys, fixtures):
    c, auth = api.c, keys["auth"]
    store = c.app_state.app.store
    # A real (non-legacy) workspace, as a fixture row, with a key bound to it.
    import time
    store.execute("INSERT INTO workspaces (id, name, kind, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                  ("w_linkfreeze", "测试工作空间（夹具）", "org", "active", time.time(), time.time()))
    if not store.one("SELECT 1 AS x FROM apps WHERE id='docweave'"):
        store.execute("INSERT INTO apps (id, name, launch_url, status, access_policy, created_at, updated_at) "
                      "VALUES ('docweave', 'Synwevo Docs', '/', 'active', 'members', ?, ?)", (time.time(), time.time()))
    store.execute("INSERT OR IGNORE INTO workspace_apps (workspace_id, app_id, enabled_at) VALUES (?,?,?)",
                  ("w_linkfreeze", "docweave", time.time()))
    tok, _ = auth.create_key("ws", "w_linkfreeze", ("read", "write"))
    h = {"Authorization": f"Bearer {tok}"}
    pid = _ok(c.post("/api/v1/projects", json={"name": "冻结"}, headers=h))["id"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    _ok(c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data)}, headers=h))
    tpl = _ok(c.post("/api/v1/templates", files={"file": ("t.docx", (fixtures / "测试_合同模板.docx").read_bytes())}, headers=h))
    _ok(c.post(f"/api/v1/templates/{tpl['id']}/fields", headers=h, json={
        "schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "客户名称", "find": "示例客户名称"}]}))
    _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tpl["id"]}, headers=h))
    for _ in range(100):
        if all(f["status"] != "parsing" for f in _ok(c.get(f"/api/v1/projects/{pid}/files", headers=h))["items"]):
            break
        time.sleep(0.05)
    _ok(c.put(f"/api/v1/projects/{pid}/rule", headers=h,
              json={"rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}}}))
    slot = _ok(c.get(f"/api/v1/templates/{tpl['id']}", headers=h))["fields"][0]
    _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"mappings": [
        {"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": slot["binding"]}}]}))
    jid = _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"]
    for _ in range(600):
        j = _ok(c.get(f"/api/v1/jobs/{jid}", headers=h))
        if j["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(0.05)
    assert j["status"] == "completed", j
    link = _ok(c.post(f"/api/v1/jobs/{jid}/archive-link", json={}, headers=h))["url"]
    assert c.get(_path(link)).status_code == 200
    store.execute("UPDATE workspaces SET status='frozen' WHERE id='w_linkfreeze'")
    r = c.get(_path(link))
    assert r.status_code == 403 and r.json()["error"]["code"] == "link_revoked"


def test_unbound_legacy_links_cannot_survive_credential_revocation(api, keys, fixtures):
    """Without an issuer a legacy link cannot prove current authority; require reminting."""
    c, auth = api.c, keys["auth"]
    _, art = _done_job(api, keys, fixtures)
    legacy, _ = auth.sign_link("artifact", art["id"], "default", 900)
    r = c.get(f"/api/v1/download/{legacy}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "link_revoked"
    assert not r.content.startswith(b"PK")
    # A currently authorised caller can mint a bound replacement and download normally.
    replacement = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=keys["rw"]))["url"]
    assert c.get(_path(replacement)).headers["X-Content-SHA256"] == art["sha256"]
    expired, _ = auth.sign_link("artifact", art["id"], "default", ttl_s=-5)
    assert c.get(f"/api/v1/download/{expired}").json()["error"]["code"] == "link_expired"


def test_download_origins_ignore_untrusted_forwarded_headers(api, keys, fixtures, monkeypatch):
    from synwevo_docs.api import v1
    monkeypatch.setattr(v1, "PUBLIC_URL", "")
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "0")
    c = api.c
    _, art = _done_job(api, keys, fixtures)
    header = {**keys["rw"], "X-Forwarded-Host": "untrusted.invalid", "X-Forwarded-Proto": "https"}
    link = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=header))["url"]
    assert link.startswith("http://testserver/api/v1/download/")
    monkeypatch.setenv("SYNWEVO_TRUST_PROXY", "1")
    header.update({"X-Forwarded-Host": "untrusted.invalid, docs.example.invalid", "X-Forwarded-Proto": "http, https"})
    link = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=header))["url"]
    assert link.startswith("https://docs.example.invalid/api/v1/download/")
    monkeypatch.setattr(v1, "PUBLIC_URL", "https://configured.example.invalid")
    link = _ok(c.post(f"/api/v1/artifacts/{art['id']}/link", headers=header))["url"]
    assert link.startswith("https://configured.example.invalid/api/v1/download/")
