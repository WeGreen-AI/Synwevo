"""/api/v1 field editing (API-2): a script can change or remove one field of an uploaded template,
naming the head it read, with the same service (and the same version history) as the workbench."""
from .test_api_v1 import _ok, keys  # noqa: F401  (keys is a fixture)

FIELDS = "docweave.template-fields"


def _template_with_fields(c, h, fixtures):
    t = _ok(c.post("/api/v1/templates", files={"file": ("contract.docx", (fixtures / "测试_合同模板.docx").read_bytes())},
                   headers=h))
    _ok(c.post(f"/api/v1/templates/{t['id']}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [
        {"kind": "text", "name": "客户名称", "find": "示例客户名称"},
        {"kind": "text", "name": "金额", "find": "0.00"}]}))
    return _ok(c.get(f"/api/v1/templates/{t['id']}", headers=h))


def _err(r, status, code):
    assert r.status_code == status, (r.status_code, r.text[:400])
    assert r.json()["error"]["code"] == code, r.text[:400]
    return r.json()["error"]


def test_a_field_is_edited_once_against_the_head_it_was_read_at(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    t = _template_with_fields(c, h, fixtures)
    tid, head, field = t["id"], t["head"], t["fields"][1]
    url = f"/api/v1/templates/{tid}/fields/{field['id']}"
    out = _ok(c.patch(url, headers=h, json={"base_head": head, "name": "合同金额", "data_type": "number", "required": True}))
    assert out["head"] == head + 1
    edited = next(f for f in out["fields"] if f["id"] == field["id"])
    assert (edited["name"], edited["data_type"], edited["required"]) == ("合同金额", "number", True)
    # The same request again (a retry after a lost response) changes nothing: the head moved.
    err = _err(c.patch(url, headers=h, json={"base_head": head, "name": "合同金额", "data_type": "number", "required": True}),
               409, "stale_template")
    assert err["details"]["head"] == head + 1
    assert _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["head"] == head + 1
    # The version history says what happened, as it does for the workbench.
    versions = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["versions"]
    assert "合同金额" in versions[-1]["note"]


def test_bad_edits_are_named_and_change_nothing(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    t = _template_with_fields(c, h, fixtures)
    tid, head, fid = t["id"], t["head"], t["fields"][0]["id"]
    url = f"/api/v1/templates/{tid}/fields/{fid}"
    assert _err(c.patch(url, headers=h, json={"name": "x"}), 400, "invalid")["field"] == "base_head"
    assert _err(c.patch(url, headers=h, json={"base_head": head}), 400, "invalid")["field"] == "body"
    assert _err(c.patch(url, headers=h, json={"base_head": head, "locator": {}}), 400, "invalid")["field"] == "locator"
    assert _err(c.patch(url, headers=h, json={"base_head": head, "align": "sideways"}), 400, "bad_value")["field"] == "align"
    _err(c.patch(f"/api/v1/templates/{tid}/fields/no-such-field", headers=h, json={"base_head": head, "name": "y"}), 404, "not_found")
    assert c.patch(url, headers=keys["ro"], json={"base_head": head, "name": "y"}).status_code == 403
    assert c.patch(url, headers=keys["other"], json={"base_head": head, "name": "y"}).status_code == 404
    assert _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["head"] == head


def test_removing_a_field_can_take_its_mappings_in_one_project_only(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    t = _template_with_fields(c, h, fixtures)
    tid, head = t["id"], t["head"]
    gone, kept = t["fields"]
    pid = _ok(c.post("/api/v1/projects", headers=h, json={"name": "field removal"}))["id"]
    _ok(c.put(f"/api/v1/projects/{pid}/template", headers=h, json={"template_id": tid}))
    maps = _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"mappings": [
        {"sources": [{"kind": "constant", "value": "甲"}], "target": {"binding": gone.get("binding") or gone["id"]}},
        {"sources": [{"kind": "constant", "value": "1"}], "target": {"binding": kept.get("binding") or kept["id"]}}]}))
    stale = c.delete(f"/api/v1/templates/{tid}/fields/{gone['id']}?base_head={head - 1}&project_id={pid}", headers=h)
    _err(stale, 409, "stale_template")
    assert len(_ok(c.get(f"/api/v1/projects/{pid}/mappings", headers=h))["mappings"]) == 2, "a refused removal keeps the mappings"
    # Another workspace's project is not a place this caller can remove mappings from.
    other_pid = _ok(c.post("/api/v1/projects", headers=keys["other"], json={"name": "not yours"}))["id"]
    assert c.delete(f"/api/v1/templates/{tid}/fields/{gone['id']}?base_head={head}&project_id={other_pid}",
                    headers=h).status_code == 404
    out = _ok(c.delete(f"/api/v1/templates/{tid}/fields/{gone['id']}?base_head={head}&project_id={pid}", headers=h))
    assert out["head"] == head + 1 and [f["id"] for f in out["fields"]] == [kept["id"]]
    removed = {m["id"] for m in maps["mappings"] if (m["target"].get("binding")) == (gone.get("binding") or gone["id"])}
    assert set(out["removed_mappings"]) == removed and len(removed) == 1
    left = _ok(c.get(f"/api/v1/projects/{pid}/mappings", headers=h))["mappings"]
    assert [m["target"]["binding"] for m in left] == [kept.get("binding") or kept["id"]]


def test_built_in_templates_keep_their_fields_in_the_editor(api, keys):
    """A built-in template's fields live in its editor model (CI-112): removing one here would be
    undone by the next editor save, so the API refuses it exactly as the workbench does."""
    c, h = api.c, keys["rw"]
    t = _ok(c.post("/api/v1/templates/builtin", headers=h, json={"kind": "builtin_doc", "name": "合成客户通知", "editor": {
        "page": {"size": "A4", "orientation": "portrait"}, "base_size": 11,
        "body": {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "客户："},
            {"type": "field", "attrs": {"id": "customer", "name": "客户", "required": True}}]}]}}}))
    detail = _ok(c.get(f"/api/v1/templates/{t['id']}", headers=h))
    assert [f["name"] for f in detail["fields"]] == ["客户"]
    fid = detail["fields"][0]["id"]
    _err(c.delete(f"/api/v1/templates/{t['id']}/fields/{fid}?base_head={detail['head']}", headers=h), 409, "builtin_fields")
    assert _ok(c.get(f"/api/v1/templates/{t['id']}", headers=h))["head"] == detail["head"]
