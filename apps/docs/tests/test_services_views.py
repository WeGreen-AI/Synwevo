"""Read models: preview uses the job's generation options, OCR page lists are validated, and the
parsed-document cache keeps one version per file."""
from __future__ import annotations

from synwevo_docs.engine import generate as G


def test_preview_fills_with_the_options_a_job_would_use(api, fixtures, monkeypatch):
    """F146: preview turned layout 'warn' into 'off' and never passed keep_fields."""
    pid = api.project("预览选项")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"output": {"naming": "{序号}", "layout": "warn", "keep_fields": True}}))
    seen = {}
    real = G.fill_checked

    def spy(fmt, work, slots, plan, options, mode, pages):
        seen.update(mode=mode, keep=options.get("keep_fields"))
        return real(fmt, work, slots, plan, options, "off", None)       # no LibreOffice needed for the check

    monkeypatch.setattr(G, "fill_checked", spy)
    monkeypatch.setattr(G, "template_pages", lambda fmt, work: 1)
    api.ok(api.c.get(f"/api/projects/{pid}/preview"))
    assert seen == {"mode": "warn", "keep": True}


def test_ocr_page_lists_are_validated_before_any_work(api, fixtures, monkeypatch):
    """F154: page numbers came straight from the request — repeated or out-of-range pages kept a
    worker busy for minutes."""
    from synwevo_docs.engine import ocr
    pid = api.project("OCR 页码")
    api.upload(pid, fixtures / "测试_申请表.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    calls = []

    def fake_ocr(png):          # TEST FIXTURE: count recognitions instead of running Tesseract
        calls.append(len(png))
        return {"words": [], "w": 1, "h": 1, "engine": "test-fixture"}

    monkeypatch.setattr(ocr, "ocr_png", fake_ocr)
    for pages in ([999], [-1], ["1"], [True]):
        r = api.c.post(f"/api/sources/{sid}/ocr", json={"pages": pages})
        assert r.status_code == 400 and r.json()["error"] == "bad_pages", (pages, r.text)
    assert not calls
    out = api.ok(api.c.post(f"/api/sources/{sid}/ocr", json={"pages": [0, 0, 0, 1, 1]}))
    assert len(calls) == 2 and out["pages"] == [0, 1]                         # duplicates recognised once


def test_the_parsed_document_cache_keeps_one_version_per_file(api, fixtures):
    """F143: every correction or OCR run created a new cache entry and the old ones stayed until
    the cache passed 64 entries, each holding the raw bytes and the parsed workbook."""
    pid = api.project("缓存")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    app = api.c.app_state.app
    row = app.project_sources(pid)[0]
    app._doc_from_row(row)
    app._doc_from_row({**row, "version": row["version"] + 1, "corrections": {"m1": {"value": "x", "confirmed": True}}})
    keys = [k for k in app._src_cache if k[0] == row["id"]]
    assert len(keys) == 1 and keys[0][1] == row["version"] + 1
