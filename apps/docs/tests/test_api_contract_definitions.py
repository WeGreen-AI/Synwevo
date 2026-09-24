"""/api/v1 contract: declarative field definitions are all-or-nothing, the documented selector
forms work, malformed selectors are named 400s (never 500s), and the capability report does not
fork LibreOffice / Tesseract on every call."""
from .test_api_v1 import _ok, keys  # noqa: F401  (keys is a fixture)

FIELDS = "docweave.template-fields"


def _upload(c, h, path, name=None):
    return _ok(c.post("/api/v1/templates", files={"file": (name or path.name, path.read_bytes())}, headers=h))


def _versions(api, tid):
    return api.c.app_state.app.store.one("SELECT COUNT(*) AS n FROM template_versions WHERE template_id=?", (tid,))["n"]


def test_field_import_is_all_or_nothing(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    t = _upload(c, h, fixtures / "测试_合同模板.docx")
    tid = t["id"]
    head, n = t["head"], _versions(api, tid)
    doc = {"schema": FIELDS, "version": 1, "fields": [
        {"kind": "text", "name": "客户名称", "find": "示例客户名称"},
        {"kind": "text", "name": "编号", "find": "000000"},
        {"kind": "text", "name": "不存在", "find": "这段文字不在模板里"}]}
    r = c.post(f"/api/v1/templates/{tid}/fields", json=doc, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "find"
    assert "第 3 个字段" in r.json()["error"]["message"]
    after = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))
    assert after["head"] == head and after["fields"] == [], "失败的导入不能留下前两个字段"
    assert _versions(api, tid) == n, "失败的导入不能留下中间版本"


def test_field_import_creates_one_version_per_call(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    t = _upload(c, h, fixtures / "测试_合同模板.docx")
    tid, head = t["id"], t["head"]
    out = _ok(c.post(f"/api/v1/templates/{tid}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [
        {"kind": "text", "name": "客户名称", "find": "示例客户名称"},
        {"kind": "text", "name": "编号", "find": "000000"},
        {"kind": "text", "name": "金额", "find": "0.00"}]}))
    d = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))
    assert out["template"] == d["head"] == head + 1
    assert [f["name"] for f in d["fields"]] == ["客户名称", "编号", "金额"]
    newest = d["versions"][-1]
    assert newest["version_no"] == head + 1 and newest["parent_no"] == head and "3 个字段" in newest["note"]
    assert _versions(api, tid) == 2
    # the combined version is a normal version: it can be undone in one step in the UI
    undo = _ok(c.post(f"/api/templates/{tid}/undo"))
    assert undo["version"]["no"] == head and undo["version"]["slots"] == []


def test_pptx_text_field_by_position(api, keys, fixtures):
    """The documented PPTX form slide/shape/para/start/end used to be rejected as unknown keys."""
    c, h = api.c, keys["rw"]
    pptx = fixtures / "测试_汇报模板.pptx"
    by_find = _upload(c, h, pptx, "a.pptx")
    _ok(c.post(f"/api/v1/templates/{by_find['id']}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [
        {"kind": "text", "name": "客户", "find": "示例客户"}]}))
    ref = _ok(c.get(f"/api/v1/templates/{by_find['id']}", headers=h))["fields"][0]
    loc = ref["locator"]
    by_pos = _upload(c, h, pptx, "b.pptx")
    spec = {"kind": "text", "name": "客户", "slide": loc["slide"], "shape": loc["shape"], "para": loc["para"],
            "start": loc["start"], "end": loc["end"]}
    made = _ok(c.post(f"/api/v1/templates/{by_pos['id']}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [spec]}))
    assert made["created"][0]["kind"] == ref["kind"]          # the same slot the find form creates
    got = _ok(c.get(f"/api/v1/templates/{by_pos['id']}", headers=h))["fields"][0]["locator"]
    assert {k: got[k] for k in ("slide", "shape", "para", "start", "end")} == \
        {k: loc[k] for k in ("slide", "shape", "para", "start", "end")}
    # a position missing one key is a named 400, not a KeyError 500
    partial = {k: v for k, v in spec.items() if k != "para"}
    r = c.post(f"/api/v1/templates/{by_pos['id']}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [partial]})
    assert r.status_code == 400 and r.json()["error"]["field"] == "para"


def test_malformed_selectors_are_named_400s(api, keys, fixtures):
    c, h = api.c, keys["rw"]
    xlsx = _upload(c, h, fixtures / "测试_订单模板.xlsx")
    docx_t = _upload(c, h, fixtures / "测试_合同模板.docx")
    pptx = _upload(c, h, fixtures / "测试_汇报模板.pptx")
    pdf = _upload(c, h, fixtures / "测试_通知书模板.pdf")
    cases = [
        (xlsx, {"kind": "cell", "name": "x", "sheet": "订单"}, "cell"),
        (xlsx, {"kind": "region", "name": "x", "sheet": "订单"}, "range"),
        (xlsx, {"kind": "repeat", "name": "x", "sheet": "订单", "row": "abc"}, "row"),
        (docx_t, {"kind": "cell", "name": "x", "table": 0, "row": 1}, "col"),
        (docx_t, {"kind": "text", "name": "x", "find": "示例客户名称", "occurrence": 0}, "occurrence"),
        (docx_t, {"kind": "text", "name": "x", "path": [0], "start": "a", "end": 3}, "start"),
        (docx_t, {"kind": "text", "name": 5, "find": "示例客户名称"}, "name"),
        (pptx, {"kind": "region", "name": "x", "r1": 0, "c1": 0, "r2": 1, "c2": 1}, "kind"),
        (pdf, {"kind": "region", "name": "x", "r1": 0, "c1": 0, "r2": 1, "c2": 1}, "kind"),
        (pdf, {"kind": "pdf_box", "name": "x", "rect": "10,10,50,50"}, "rect"),
        (pdf, {"kind": "pdf_box", "name": "x", "rect": [10, 10, 50]}, "rect"),
        (pptx, {"kind": "cell", "name": "x", "slide": 0, "row": 0, "col": 0}, "shape"),
    ]
    for t, spec, field in cases:
        r = c.post(f"/api/v1/templates/{t['id']}/fields", headers=h, json={"schema": FIELDS, "version": 1, "fields": [spec]})
        assert r.status_code == 400, (spec, r.status_code, r.text[:200])
        assert r.json()["error"]["code"] == "invalid_definition" and r.json()["error"]["field"] == field, (spec, r.json())


def test_capability_probes_are_cached(api, keys, monkeypatch):
    import synwevo_docs.capabilities as caps
    calls = {"soffice": 0, "langs": 0}

    def fake_version():
        calls["soffice"] += 1
        return "LibreOffice 24.2 (test fixture)"

    def fake_langs():
        calls["langs"] += 1
        return ["chi_sim", "eng"]

    caps.clear_probe_cache()
    monkeypatch.setattr(caps, "soffice_path", lambda: "/fixture/soffice")
    monkeypatch.setattr(caps, "soffice_version", fake_version)
    monkeypatch.setattr(caps, "tesseract_path", lambda: "/fixture/tesseract")
    monkeypatch.setattr(caps, "available_langs", fake_langs)
    try:
        for _ in range(5):
            env = _ok(api.c.get("/api/v1/capabilities", headers=keys["ro"]))["environment"]
            assert env["libreoffice"] == "LibreOffice 24.2 (test fixture)" and "chi_sim" in env["ocr_languages"]
        assert calls == {"soffice": 1, "langs": 1}, "探测子进程不应每次调用都启动"
        # a different executable is a different probe
        monkeypatch.setattr(caps, "soffice_path", lambda: "/fixture/other-soffice")
        _ok(api.c.get("/api/capabilities"))
        assert calls["soffice"] == 2
        # and the cache expires
        monkeypatch.setattr(caps, "PROBE_TTL_S", 0.0)
        _ok(api.c.get("/api/v1/capabilities", headers=keys["ro"]))
        assert calls["soffice"] == 3
    finally:
        caps.clear_probe_cache()
