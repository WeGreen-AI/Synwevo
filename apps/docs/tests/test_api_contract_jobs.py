"""/api/v1 contract: job output, template version pinning, preflight (simulate + issue list),
the rule endpoint's save order, and template downloads for image templates."""
import io

import docx
import pytest

from .test_api_v1 import _ok, _prepared, _wait, keys  # noqa: F401  (keys is a fixture)

FIELDS = "docweave.template-fields"


def _tid(api, keys, pid):
    return _ok(api.c.get(f"/api/v1/projects/{pid}", headers=keys["rw"]))["template"]["id"]


def test_job_output_has_phase_halt_reason_and_only_a_principal_label(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    created = c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h)
    assert created.status_code == 202
    job = _wait(api, keys, created.json()["id"])
    kid = c.app_state.app.store.one("SELECT id FROM api_keys WHERE name='rw'")["id"]
    for body in (created.json(), job):
        assert {"phase", "halt_reason", "principal"} <= set(body)
        assert body["principal"] == f"api:{kid}"
    # the stored descriptor stays internal: no lineage, no descriptor object on the wire
    raw = c.get(f"/api/v1/jobs/{job['id']}", headers=h).text
    assert "lineage" not in raw and '"kind"' not in raw
    # what the runner records when it halts a job is reported as is
    c.app_state.app.store.execute("UPDATE jobs SET halt_reason=? WHERE id=?",
                                  ("authorization_revoked:credential_revoked", job["id"]))
    assert _ok(c.get(f"/api/v1/jobs/{job['id']}", headers=h))["halt_reason"] == "authorization_revoked:credential_revoked"


def test_pinned_template_version_survives_template_edits(api, keys, fixtures):
    """PUT /projects/{pid}/template {version} must really pin: the answer, preflight and the job
    all use version H after the template has moved on to H+1."""
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    tid = _tid(api, keys, pid)
    head = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["head"]
    pinned = _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid, "version": head}, headers=h))
    assert pinned["template"]["pinned"] is True and pinned["template"]["version"] == head
    # the template is edited afterwards (as the UI would): a new field, a new head
    _ok(c.post(f"/api/v1/templates/{tid}/fields", headers=h, json={
        "schema": FIELDS, "version": 1, "fields": [{"kind": "text", "name": "金额", "find": "0.00"}]}))
    now = _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["template"]
    assert now["head"] == head + 1 and now["version"] == head and now["pinned"] is True and now["fields"] == 1
    pre = _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={"formats": ["docx"]}, headers=h))
    assert pre["template_version"] == head
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    assert job["status"] == "completed" and job["template"]["version_no"] == head
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(c.get(f"/api/v1/artifacts/{art['id']}/content", headers=h).content)).paragraphs)
    assert "0.00" in text, "固定版本没有「金额」字段，模板里的 0.00 应原样保留"
    # the definition says which version, and that it is pinned; its fields are that version's
    d = _ok(c.get(f"/api/v1/projects/{pid}/definition", headers=h))
    assert d["template"]["version"] == head and d["template"]["pinned"] is True and len(d["template"]["fields"]) == 1
    # unknown versions are refused, naming the field
    r = c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid, "version": head + 50}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "version"
    for bad in (0, "2", True):
        assert c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid, "version": bad}, headers=h).status_code == 400
    # rebinding without a version follows head again
    free = _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid}, headers=h))["template"]
    assert free["pinned"] is False and free["version"] == head + 1


def test_definition_import_pins_and_is_atomic(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    tid = _tid(api, keys, pid)
    head = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["head"]
    _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid, "version": head}, headers=h))
    definition = _ok(c.get(f"/api/v1/projects/{pid}/definition", headers=h))
    target = _ok(c.post("/api/v1/projects", json={"name": "导入目标"}, headers=h))["id"]
    before = _ok(c.get(f"/api/v1/projects/{target}", headers=h))
    # a definition naming a field the version lacks is refused, and nothing is half-applied
    broken = {**definition, "template": {**definition["template"], "fields": [{"id": "s_missing", "name": "x", "kind": "text"}]}}
    r = c.put(f"/api/v1/projects/{target}/definition", json=broken, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "template.fields"
    after = _ok(c.get(f"/api/v1/projects/{target}", headers=h))
    assert after["template"] is None and after["rule"] == before["rule"] and after["mapping_rev"] == before["mapping_rev"]
    # a bad mapping (not an object) also leaves the project untouched
    r = c.put(f"/api/v1/projects/{target}/definition", json={**definition, "mappings": ["x"]}, headers=h)
    assert r.status_code == 400 and _ok(c.get(f"/api/v1/projects/{target}", headers=h))["template"] is None
    # stale base_rev → 409, untouched
    r = c.put(f"/api/v1/projects/{target}/definition?base_rev=99", json=definition, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale"
    ok = _ok(c.put(f"/api/v1/projects/{target}/definition?base_rev={before['mapping_rev']}", json=definition, headers=h))
    assert ok["template"]["pinned"] is True and ok["template"]["version"] == head
    got = _ok(c.get(f"/api/v1/projects/{target}", headers=h))["template"]
    assert got["pinned"] is True and got["version"] == head


def test_preflight_simulate_and_issue_list(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    plain = _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={"formats": ["docx"]}, headers=h))
    assert "simulation" not in plain
    sim = _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={"formats": ["docx"], "simulate": True}, headers=h))
    assert sim["simulation"]["checked"] == 3 and sim["valid"] == 3
    assert _ok(c.post(f"/api/v1/projects/{pid}/preflight", json={"simulate": 1}, headers=h))["simulation"]["checked"] == 1
    for bad in ("yes", 0, -2):
        r = c.post(f"/api/v1/projects/{pid}/preflight", json={"simulate": bad}, headers=h)
        assert r.status_code == 400 and r.json()["error"]["field"] == "simulate", bad
    # simulation stores nothing
    store = c.app_state.app.store
    assert store.one("SELECT COUNT(*) AS n FROM artifacts")["n"] == 0

    # every located issue, paged: map a *required* field to an empty column
    tid = _tid(api, keys, pid)
    _ok(c.post(f"/api/v1/templates/{tid}/fields", headers=h, json={
        "schema": FIELDS, "version": 1, "fields": [{"kind": "text", "name": "备注", "find": "0.00", "required": True}]}))
    slots = {f["name"]: f for f in _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["fields"]}
    maps = _ok(c.get(f"/api/v1/projects/{pid}/mappings", headers=h))["mappings"]
    maps.append({"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "备注"}],
                 "target": {"binding": slots["备注"]["binding"]}})
    _ok(c.put(f"/api/v1/projects/{pid}/mappings", json={"mappings": maps}, headers=h))
    r = c.get(f"/api/v1/projects/{pid}/preflight/issues?level=error&limit=2", headers=h)
    page = r.json()
    assert r.status_code == 200 and page["total"] == 3 and len(page["items"]) == 2 and page["next_offset"] == 2
    assert 'rel="next"' in r.headers["Link"] and "offset=2" in r.headers["Link"]
    assert all(i["level"] == "error" and i["where"] for i in page["items"])
    rest = _ok(c.get(f"/api/v1/projects/{pid}/preflight/issues?level=error&offset=2", headers=h))
    assert len(rest["items"]) == 1 and rest["next_offset"] is None
    for q, field in (("level=fatal", "level"), ("limit=5000", "limit"), ("offset=-1", "offset")):
        bad = c.get(f"/api/v1/projects/{pid}/preflight/issues?{q}", headers=h)
        assert bad.status_code == 400 and bad.json()["error"]["field"] == field, q


def test_rule_is_not_saved_when_its_preview_fails(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    pid = _prepared(api, keys, fixtures)
    before = _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["rule"]
    r = c.put(f"/api/v1/projects/{pid}/rule", json={"rule": {"mode": "per_group", "table": {"sheet": "客户", "header_row": 0}}},
              headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_rule"
    assert _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["rule"] == before


def test_image_template_download_is_the_image_not_the_wrapper(api, keys, fixtures):
    from synwevo_docs.engine import pdf_engine as pe
    if not pe.image_templates_available():
        pytest.skip("本环境没有可用的图片模板引擎")
    c, h = api.c, keys["rw"]
    png = (fixtures / "测试_发货单图片模板.png").read_bytes()
    t = _ok(c.post("/api/v1/templates", files={"file": ("发货单.png", png)}, headers=h))
    orig = c.get(f"/api/v1/templates/{t['id']}/content?which=original", headers=h)
    assert orig.status_code == 200 and orig.content == png
    assert orig.headers["content-type"] == "image/png" and ".png" in orig.headers["content-disposition"]
    work = c.get(f"/api/v1/templates/{t['id']}/content?which=working", headers=h)
    assert work.content.startswith(b"%PDF") and work.headers["content-type"] == "application/pdf"
    bad = c.get(f"/api/v1/templates/{t['id']}/content?which=both", headers=h)
    assert bad.status_code == 400 and bad.json()["error"]["field"] == "which"
