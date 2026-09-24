"""/api/v1 contract: bounded, predictable pagination on every list (R32).

Every list has a default and a maximum page size enforced server side, pages in SQL, and offers
an opaque cursor (`next_cursor` + `Link: <…>; rel="next"`) that neither skips nor repeats rows
when earlier rows are deleted between requests. `offset`/`limit`/`total`/`next_offset` stay
for existing clients.
"""
import re
import time
import urllib.parse

from synwevo_core.context import as_tenant
from synwevo_core.store import new_id

from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401  (keys is a fixture)


def _next_link(r) -> str | None:
    link = r.headers.get("Link")
    if not link:
        return None
    m = re.match(r'<([^>]+)>;\s*rel="next"', link)
    assert m, link
    u = urllib.parse.urlsplit(m.group(1))
    return f"{u.path}?{u.query}"


def _fixture_projects(api, n: int, tenant: str = "default") -> list[str]:
    """TEST FIXTURE rows: n projects created through the service (origin marks them as test data)."""
    app = api.c.app_state.app
    with as_tenant(tenant):
        return [app.create_project(f"分页夹具 {i:03d}", origin="test:pagination")["id"] for i in range(n)]


def test_follow_link_next_through_250_projects(api, keys):
    c, h = api.c, keys["rw"]
    made = set(_fixture_projects(api, 250))
    seen, requests, url = [], 0, "/api/v1/projects?limit=100"
    while url:
        r = c.get(url, headers=h)
        assert r.status_code == 200, r.text
        requests += 1
        body = r.json()
        seen += [p["id"] for p in body["items"]]
        url = _next_link(r)
        if url:
            assert body["next_cursor"] and "cursor=" in url
        else:
            assert body["next_cursor"] is None
    assert requests == 3
    assert len(seen) == len(set(seen)) == 250 and set(seen) == made


def test_cursor_pages_do_not_skip_or_repeat_after_a_delete(api, keys):
    c, h = api.c, keys["rw"]
    ids = _fixture_projects(api, 30)
    p1 = c.get("/api/v1/projects?limit=10", headers=h)
    first = [p["id"] for p in p1.json()["items"]]
    # delete (soft, as DELETE /projects does) a project that was already returned on page 1
    store = c.app_state.app.store
    store.execute("UPDATE projects SET deleted=1 WHERE id=?", (first[3],))
    rest, url = [], _next_link(p1)
    while url:
        r = c.get(url, headers=h)
        rest += [p["id"] for p in r.json()["items"]]
        url = _next_link(r)
    assert not set(first) & set(rest), "游标分页不能重复已返回的行"
    assert set(first) | set(rest) == set(ids), "游标分页不能跳过任何行"
    # offset paging, for comparison, skips a row in exactly this situation — which is why the
    # cursor exists (offset is kept only for existing clients)
    store.execute("UPDATE projects SET deleted=0 WHERE id=?", (first[3],))
    order = [p["id"] for p in _ok(c.get("/api/v1/projects?limit=30", headers=h))["items"]]
    o1 = [p["id"] for p in _ok(c.get("/api/v1/projects?limit=10&offset=0", headers=h))["items"]]
    store.execute("UPDATE projects SET deleted=1 WHERE id=?", (o1[3],))
    o2 = [p["id"] for p in _ok(c.get("/api/v1/projects?limit=10&offset=10", headers=h))["items"]]
    assert order[10] not in o1 + o2


def test_offset_fields_are_kept_and_counts_come_from_sql(api, keys):
    c, h = api.c, keys["rw"]
    _fixture_projects(api, 7)
    body = _ok(c.get("/api/v1/projects?limit=3&offset=3", headers=h))
    assert body["total"] == 7 and body["offset"] == 3 and body["limit"] == 3 and body["next_offset"] == 6
    assert len(body["items"]) == 3 and {"sources", "last_job", "template_name"} <= set(body["items"][0])
    last = _ok(c.get("/api/v1/projects?limit=3&offset=6", headers=h))
    assert len(last["items"]) == 1 and last["next_offset"] is None and last["next_cursor"] is None
    # the default page is bounded
    assert _ok(c.get("/api/v1/projects", headers=h))["limit"] == 50


def test_unbounded_lists_are_now_bounded(api, keys):
    c, h = api.c, keys["rw"]
    store = c.app_state.app.store
    # TEST FIXTURE rows: 300 template rows (no files needed to list them)
    t = time.time()
    for i in range(300):
        tid = new_id("t")
        store.execute("INSERT INTO templates (id, name, kind, format, head, created_at, updated_at, deleted, tenant) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (tid, f"模板夹具{i}", "uploaded", "docx", 1, t + i, t + i, 0, "default"))
        store.execute("INSERT INTO template_versions (template_id, version_no, slots, created_at) VALUES (?,?,?,?)",
                      (tid, 1, "[]", t))
    r = c.get("/api/v1/templates", headers=h)
    body = r.json()
    assert len(body["items"]) == 50 and body["total"] == 300 and body["next_cursor"]
    assert _next_link(r)
    assert body["items"][0]["name"] == "模板夹具299", "按创建时间倒序"
    # all pages via cursor
    seen, url = [x["id"] for x in body["items"]], _next_link(r)
    while url:
        r = c.get(url, headers=h)
        seen += [x["id"] for x in r.json()["items"]]
        url = _next_link(r)
    assert len(seen) == len(set(seen)) == 300


def test_page_size_and_cursor_are_validated(api, keys):
    c, h = api.c, keys["rw"]
    for q, field in (("limit=5000", "limit"), ("limit=0", "limit"), ("offset=-1", "offset"),
                     ("cursor=not-a-cursor", "cursor")):
        r = c.get(f"/api/v1/projects?{q}", headers=h)
        assert r.status_code == 400, q
        assert r.json()["error"]["field"] == field, q
    # a cursor from one list is not accepted by another
    _fixture_projects(api, 3)
    cur = _ok(c.get("/api/v1/projects?limit=1", headers=h))["next_cursor"]
    r = c.get(f"/api/v1/templates?cursor={cur}", headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"
    # cursor and offset together are ambiguous
    r = c.get(f"/api/v1/projects?cursor={cur}&offset=2", headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "cursor"


def test_job_lists_page_beyond_the_old_hard_cap(api, keys, fixtures):
    """Project jobs used to stop at 50 while reporting total=50 and next_offset=null."""
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    real = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    store = c.app_state.app.store
    snap = store.one("SELECT snapshot, options FROM jobs WHERE id=?", (real["id"],))
    import json as _json
    t = time.time()
    for i in range(60):          # TEST FIXTURE rows: finished jobs of the same project
        store.execute("INSERT INTO jobs (id, project_id, status, created_at, snapshot, options, counts, tenant, run_no, origin) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (new_id("j"), pid, "completed", t - 1000 - i, _json.dumps(snap["snapshot"]), _json.dumps(snap["options"]),
                       "{}", "default", 1, "test:pagination"))
    body = _ok(c.get(f"/api/v1/projects/{pid}/jobs", headers=h))
    assert body["total"] == 61 and len(body["items"]) == 50 and body["next_offset"] == 50
    assert body["items"][0]["id"] == real["id"], "最新的任务在前"
    assert {"phase", "halt_reason", "principal"} <= set(body["items"][0])
    assert body["items"][0]["principal"] == f"api:{store.one('SELECT id FROM api_keys WHERE name=?', ('rw',))['id']}"
    rest = _ok(c.get(f"/api/v1/projects/{pid}/jobs?cursor={body['next_cursor']}", headers=h))
    assert len(rest["items"]) == 11 and not set(x["id"] for x in rest["items"]) & set(x["id"] for x in body["items"])

    # items, artifacts and runs: cursor pages cover everything exactly once
    for path, key in ((f"/api/v1/jobs/{real['id']}/items?limit=1", "id"),
                      (f"/api/v1/jobs/{real['id']}/artifacts?limit=1", "id"),
                      (f"/api/v1/jobs/{real['id']}/runs?limit=1", "run_no")):
        r = c.get(path, headers=h)
        seen, url = [x[key] for x in r.json()["items"]], _next_link(r)
        total = r.json()["total"]
        while url:
            r = c.get(url, headers=h)
            seen += [x[key] for x in r.json()["items"]]
            url = _next_link(r)
        assert len(seen) == len(set(seen)) == total, path
    # the status filter travels inside the cursor and cannot be swapped mid-way
    r = c.get(f"/api/v1/jobs/{real['id']}/items?limit=1&status=success", headers=h)
    cur = r.json()["next_cursor"]
    assert cur and "status=success" in r.headers["Link"]
    swapped = c.get(f"/api/v1/jobs/{real['id']}/items?cursor={cur}&status=failed", headers=h)
    assert swapped.status_code == 400 and swapped.json()["error"]["code"] == "invalid_cursor"
    assert c.get(f"/api/v1/jobs/{real['id']}/items?status=bogus", headers=h).json()["error"]["field"] == "status"


def test_files_list_is_paged(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _ok(c.post("/api/v1/projects", json={"name": "文件分页"}, headers=h))["id"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    ids = [_ok(c.post(f"/api/v1/projects/{pid}/files", files={"file": (f"f{i}.xlsx", data)}, headers=h))["id"] for i in range(3)]
    r = c.get(f"/api/v1/projects/{pid}/files?limit=2", headers=h)
    body = r.json()
    assert body["total"] == 3 and [x["id"] for x in body["items"]] == ids[:2]
    nxt = _ok(c.get(_next_link(r), headers=h))
    assert [x["id"] for x in nxt["items"]] == ids[2:] and nxt["next_cursor"] is None
