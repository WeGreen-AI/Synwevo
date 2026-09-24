"""/api/v1 contract: another workspace's objects are invisible through every object route.

`test_api_v1.test_tenant_isolation` covered projects only. A regression that dropped the tenant
check in any other handler — files, templates, jobs, items, artifacts, links, uploads — would
have leaked another workspace's generated documents with the suite still green. This walks every
route that takes an object id, with a credential of another workspace that has *every* scope,
and requires 404 with nothing of the object in the answer.
"""
from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401  (keys is a fixture)


def test_every_object_route_refuses_another_workspace(api, keys, fixtures):
    c, h, o = api.c, keys["rw"], keys["other"]
    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    jid = job["id"]
    fid = _ok(c.get(f"/api/v1/projects/{pid}/files", headers=h))["items"][0]["id"]
    tid = _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["template"]["id"]
    art = _ok(c.get(f"/api/v1/jobs/{jid}/artifacts", headers=h))["items"][0]
    aid = art["id"]
    up = _ok(c.post("/api/v1/uploads", json={"filename": "a.xlsx", "size": 3}, headers=h))["id"]
    own_pid = _ok(c.post("/api/v1/projects", json={"name": "B 的项目"}, headers=o))["id"]
    fields = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "x", "find": "示例客户名称"}]}

    routes = [
        ("GET", f"/api/v1/projects/{pid}", None), ("DELETE", f"/api/v1/projects/{pid}", None),
        ("PUT", f"/api/v1/projects/{pid}/rule", {"rule": {"mode": "per_file"}}),
        ("PUT", f"/api/v1/projects/{pid}/template", {"template_id": tid}),
        ("GET", f"/api/v1/projects/{pid}/mappings", None), ("PUT", f"/api/v1/projects/{pid}/mappings", {"mappings": []}),
        ("GET", f"/api/v1/projects/{pid}/definition", None),
        ("PUT", f"/api/v1/projects/{pid}/definition", {"schema": "docweave.project", "version": 1, "rule": {}}),
        ("POST", f"/api/v1/projects/{pid}/preflight", {}), ("GET", f"/api/v1/projects/{pid}/preflight/issues", None),
        ("GET", f"/api/v1/projects/{pid}/files", None), ("POST", f"/api/v1/projects/{pid}/files", {"upload_id": up}),
        ("GET", f"/api/v1/files/{fid}", None), ("DELETE", f"/api/v1/files/{fid}", None),
        ("GET", f"/api/v1/templates/{tid}", None), ("DELETE", f"/api/v1/templates/{tid}", None),
        ("GET", f"/api/v1/templates/{tid}/fields", None), ("POST", f"/api/v1/templates/{tid}/fields", fields),
        ("GET", f"/api/v1/templates/{tid}/content", None), ("GET", f"/api/v1/templates/{tid}/content?which=working", None),
        ("POST", f"/api/v1/projects/{pid}/jobs", {}), ("GET", f"/api/v1/projects/{pid}/jobs", None),
        ("GET", f"/api/v1/jobs/{jid}", None), ("GET", f"/api/v1/jobs/{jid}/items", None),
        ("GET", f"/api/v1/jobs/{jid}/runs", None), ("GET", f"/api/v1/jobs/{jid}/events", None),
        ("POST", f"/api/v1/jobs/{jid}/cancel", None), ("POST", f"/api/v1/jobs/{jid}/retry", {}),
        ("GET", f"/api/v1/jobs/{jid}/artifacts", None), ("GET", f"/api/v1/artifacts/{aid}/content", None),
        ("POST", f"/api/v1/artifacts/{aid}/link", None), ("GET", f"/api/v1/jobs/{jid}/archive", None),
        ("POST", f"/api/v1/jobs/{jid}/archive-link", {}), ("GET", f"/api/v1/jobs/{jid}/report.csv", None),
        ("GET", f"/api/v1/uploads/{up}", None), ("POST", f"/api/v1/uploads/{up}/complete", {}),
        ("POST", f"/api/v1/uploads/{up}/abort", None),
        # binding someone else's template to one's own project
        ("PUT", f"/api/v1/projects/{own_pid}/template", {"template_id": tid}),
    ]
    leaks = []
    for method, path, body in routes:
        r = c.request(method, path, json=body, headers=o)
        text = r.content
        if r.status_code != 404:
            leaks.append(f"{method} {path} → {r.status_code} {text[:120]!r}")
            continue
        for secret in (art["sha256"], art["filename"], tid if "/projects/" in path else None):
            if secret and secret.encode() in text:
                leaks.append(f"{method} {path} 响应里出现了对方的 {secret}")
        if text.startswith(b"PK"):
            leaks.append(f"{method} {path} 返回了文件内容")
    put = c.put(f"/api/v1/uploads/{up}", content=b"abc", headers={**o, "Content-Range": "bytes 0-2/3"})
    if put.status_code != 404:
        leaks.append(f"PUT /uploads/{up} → {put.status_code}")
    assert not leaks, "\n".join(leaks)

    # and the owner still has everything, untouched
    assert _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["template"]["id"] == tid
    assert _ok(c.get(f"/api/v1/jobs/{jid}", headers=h))["status"] == "completed"
    assert c.get(f"/api/v1/artifacts/{aid}/content", headers=h).status_code == 200
    assert _ok(c.get(f"/api/v1/uploads/{up}", headers=h))["status"] == "open"
    assert _ok(c.get(f"/api/v1/projects/{own_pid}", headers=o))["template"] is None
