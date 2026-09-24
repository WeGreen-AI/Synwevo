"""AGPL-free deployment: the pdfium engine (pypdfium2, Apache-2.0/BSD) reads PDFs the same
way PyMuPDF does; PDF / image templates are refused with an explanation."""
import pytest

from synwevo_docs.engine import pdf_engine as pe


@pytest.fixture()
def pdfium():
    prev = pe.use_engine("pdfium")
    yield
    pe.use_engine(prev)


def _centers(data, pno):
    out = {}
    for w in pe.page_words(data, pno)["words"]:
        r = w["r"]
        out.setdefault(w["t"], ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2))
    return out


@pytest.mark.parametrize("name", ["测试_申请表.pdf", "测试_通知书模板.pdf", "测试_发货单模板_无框线与旋转.pdf"])
def test_same_geometry_as_pymupdf(fixtures, name):
    data = (fixtures / name).read_bytes()
    ref_meta = pe.inspect(data)
    ref = [_centers(data, i) for i in range(ref_meta["page_count"])]
    prev = pe.use_engine("pdfium")
    try:
        meta = pe.inspect(data)
        got = [_centers(data, i) for i in range(meta["page_count"])]
        png = pe.render_page_png(data, 0, 1.0)
    finally:
        pe.use_engine(prev)
    assert [(p["w"], p["h"], p["rotation"], p["kind"]) for p in meta["pages"]] == [(p["w"], p["h"], p["rotation"], p["kind"]) for p in ref_meta["pages"]]
    assert png[:4] == b"\x89PNG"
    for a, b in zip(ref, got):
        common = set(a) & set(b)
        assert len(common) >= 0.8 * len(a), (name, sorted(set(a) - set(b))[:10])
        for t in common:                                     # same word, same place on the displayed page
            assert abs(a[t][0] - b[t][0]) < 0.02 and abs(a[t][1] - b[t][1]) < 0.02, (name, t, a[t], b[t])


def test_pdf_source_flow_and_template_refusal(api, fixtures, pdfium):
    pid = api.project("pdfium 引擎")
    api.upload(pid, fixtures / "测试_申请表.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    words = api.ok(api.c.get(f"/api/sources/{sid}/words/0"))["words"]
    label = next(w for w in words if w["t"].startswith("证件号"))
    vals = [w for w in words if abs(w["r"][1] - label["r"][1]) < 0.01 and w["r"][0] > label["r"][2]]
    nrect = [vals[0]["r"][0] - 0.005, vals[0]["r"][1] - 0.004, vals[0]["r"][2] + 0.005, vals[0]["r"][3] + 0.004]
    prop = api.ok(api.c.post(f"/api/sources/{sid}/locator", json={"project_id": pid, "selection": {"kind": "pdf_region", "page": 0, "nrect": nrect}}))["proposals"][0]
    assert prop["sample"] == "TEST00000001" and prop["locator"]["anchor"]["text"].startswith("证件号")
    r = api.c.post("/api/templates/upload", files={"file": ("t.pdf", (fixtures / "测试_通知书模板.pdf").read_bytes())}, data={"project_id": pid})
    assert r.status_code == 400 and "pdfium" in r.text and "PyMuPDF" in r.text
    # Image templates DO work without PyMuPDF: they are raster work and now use the Pillow
    # fill path (they used to be refused for a reason that had nothing to do with images).
    r = api.c.post("/api/templates/upload", files={"file": ("t.png", (fixtures / "测试_发货单图片模板.png").read_bytes())}, data={"project_id": pid})
    assert r.status_code < 400, r.text[:300]
    # a DOCX template still works end to end with the PDF source
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    slot = api.text_slot(t["id"], "示例客户名称", "客户")
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "single", "sources": [prop["locator"]], "target": {"binding": slot["binding"]}}])
    job = api.job(pid)
    assert job["status"] == "completed"
    caps = api.ok(api.c.get("/api/capabilities"))
    assert caps["environment"]["pdf_engine"] == "pdfium"
    pdf_row = next(r for r in caps["rows"] if r["format"].startswith("PDF（文本型）"))
    assert pdf_row["template"][0] == "unavailable"      # PDF templates still need PyMuPDF
    img_row = next(r for r in caps["rows"] if "PNG" in r["format"])
    assert img_row["template"][0] != "unavailable", "图片模板在 pdfium 部署下应当可用"


def test_page_level_blocks_and_scanned_ocr(api, fixtures, pdfium):
    pid = api.project("pdfium 分块")
    api.upload(pid, fixtures / "测试_申请表.pdf", fixtures / "测试_多表单登记表.pdf")
    srcs = {s["filename"]: s["id"] for s in api.ok(api.c.get(f"/api/projects/{pid}/sources"))}
    pv = api.ok(api.c.post(f"/api/projects/{pid}/rule-preview", json={"mode": "per_block", "block": {"anchor": "测试申请表"}}))
    assert pv["blocks"][srcs["测试_申请表.pdf"]] == 3                  # one form per page: page-level split works
    pv = api.ok(api.c.post(f"/api/projects/{pid}/rule-preview", json={"mode": "per_block", "block": {"anchor": "人员登记表"}}))
    assert any("只能按整页切分" in e["message"] for e in pv["file_errors"])   # in-page split needs PyMuPDF: said, not faked
    pid2 = api.project("pdfium OCR")
    api.upload(pid2, fixtures / "测试_扫描单据.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid2}/sources"))[0]["id"]
    ocr = api.ok(api.c.post(f"/api/sources/{sid}/ocr", json={}))
    assert ocr["words"] > 5


def test_about_and_source_offer(api, monkeypatch):
    import io
    import tarfile
    a = api.ok(api.c.get("/api/about"))
    assert a["pdf_engine"] in ("pymupdf", "pdfium") and any(t["name"] == "PyMuPDF" for t in a["third_party"])
    r = api.c.get("/api/source-code")
    assert r.status_code == 200
    names = tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz").getnames()
    assert any(n.endswith("apps/docs/synwevo_docs/engine/pdf_engine.py") for n in names)
    assert any(n.endswith("apps/docs/web/src/main.tsx") for n in names) and any(n.endswith("SOURCE_OFFER.txt") for n in names)
    assert not any("/data/" in n or ".venv" in n or "node_modules" in n for n in names)   # never user data
    monkeypatch.setenv("SYNWEVO_DOCS_SOURCE_OFFER", "off")
    assert api.c.get("/api/source-code").status_code == 404
