"""Acceptance scenarios 5–8."""
import io

import docx
import openpyxl
import pymupdf

from conftest import docx_text
from docweave.engine.converters import convert


def _xlsx(rows, sheet="Sheet1", extra_sheets=()):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    for name in extra_sheets:
        wb.create_sheet(name)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def test_s5_region_fill_and_size_mismatch(api, fixtures):
    pid = api.project("S5")
    api.upload(pid, fixtures / "测试_区域来源3x4.xlsx")
    t = api.template(pid, fixtures / "测试_区域模板.xlsx")
    region = api.slot(t["id"], kind="region", sheet="报表", range="C5:F7", name="季度区域")
    assert region["rows"] == 3 and region["cols"] == 4
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "region", "sources": [{"kind": "range", "sheet": "数据", "range": "B3:E5"}], "target": {"binding": region["binding"]}}])
    job = api.job(pid)
    assert job["status"] == "completed", api.items(job["id"])
    out = openpyxl.load_workbook(io.BytesIO(next(iter(api.outputs(job["id"]).values()))))["报表"]
    for r in range(3):
        for c in range(4):
            cell = out.cell(5 + r, 3 + c)
            assert cell.value == f"R{r + 1}C{c + 1}"                  # row/column correspondence
            assert cell.fill.fgColor.rgb.endswith("FFF2CC") and cell.font.bold  # target style kept
    assert out["B5"].value is None and out["G5"].value is None and out["C8"].value is None  # nothing spilled around
    assert out["A10"].value == "下方内容不应被覆盖"
    # a 3×3 target with the 3×4 source: must be reported, never truncated
    t2 = api.template(pid, fixtures / "测试_区域模板.xlsx")
    small = api.slot(t2["id"], kind="region", sheet="报表", range="C5:E7", name="小区域")
    api.maps(pid, [{"type": "region", "sources": [{"kind": "range", "sheet": "数据", "range": "B3:E5"}], "target": {"binding": small["binding"]}}])
    pv = api.ok(api.c.get(f"/api/projects/{pid}/preview"))
    assert pv.get("blocked") and any(i["code"] == "region_size" for i in pv["issues"])
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"error_policy": "block"})
    assert r.status_code == 409 and "错误" in r.json()["message"]


def _detail_file(n):
    return _xlsx([["行号", "品名", "数量", "单价"]] + [[i + 1, f"测试商品{i + 1}", 1, 9.5] for i in range(n)], "明细")


def test_s6_repeat_detail_0_1_many_and_pagination(api, fixtures):
    pid = api.project("S6")
    for n in (0, 1, 60):
        api.upload_bytes(pid, f"明细_{n:02d}.xlsx", _detail_file(n))
    t = api.template(pid, fixtures / "测试_订单模板.docx")
    v = api.view(t["id"])
    tbl = [b for b in v["doc"]["parts"][0]["blocks"] if b["t"] == "tbl"][0]
    rep = api.slot(t["id"], kind="repeat", part=tbl["part"], table_path=tbl["path"], row=1, name="明细", header_rows=1, empty_policy="placeholder")
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
                    "columns": [{"slot_col": f"c{i}", "source": {"header": h}} for i, h in enumerate(["行号", "品名", "数量", "单价"])]}])
    job = api.job(pid, naming="{来源文件名}", formats=["docx", "pdf"])
    assert job["status"] == "completed", api.items(job["id"])
    assert job["counts"]["success"] == 3 and job["counts"]["files"] == 6       # 3 samples × 2 formats
    outs = api.outputs(job["id"])
    d0 = docx.Document(io.BytesIO(outs["明细_00.docx"]))
    assert [c.text for c in d0.tables[0].rows[1].cells][0] == "（无）" and len(d0.tables[0].rows) == 2
    d1 = docx.Document(io.BytesIO(outs["明细_01.docx"]))
    assert len(d1.tables[0].rows) == 2 and d1.tables[0].rows[1].cells[1].text == "测试商品1"
    d60 = docx.Document(io.BytesIO(outs["明细_60.docx"]))
    assert len(d60.tables[0].rows) == 61 and d60.tables[0].rows[60].cells[1].text == "测试商品60"
    assert d60.tables[0].rows[0]._tr.trPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader") is not None
    assert "（明细结束）" in docx_text(outs["明细_60.docx"])                   # text after the table not overwritten
    pdf = pymupdf.open(stream=outs["明细_60.pdf"], filetype="pdf")
    assert pdf.page_count >= 2
    for page in pdf:                                                         # header row repeated on every page
        assert "品名" in page.get_text()
    all_text = "".join(p.get_text() for p in pdf)
    assert all(f"测试商品{i}" in all_text for i in range(1, 61))            # nothing truncated
    # policy remove_row + XLSX template shrinking
    api.ok(api.c.patch(f"/api/templates/{t['id']}/slots/{rep['id']}", json={"empty_policy": "remove_row"}))
    job = api.job(pid, naming="{来源文件名}")
    d0 = docx.Document(io.BytesIO(api.outputs(job["id"])["明细_00.docx"]))
    assert len(d0.tables[0].rows) == 1 and d0.tables[0].rows[0].cells[1].text == "品名"


def test_s6b_xlsx_zero_rows_removes_template_row(api, fixtures):
    pid = api.project("S6b")
    api.upload_bytes(pid, "空明细.xlsx", _detail_file(0))
    t = api.template(pid, fixtures / "测试_订单模板.xlsx")
    rep = api.slot(t["id"], kind="repeat", sheet="订单", row=4, c1=0, c2=3, name="明细", empty_policy="remove_row")
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
                    "columns": [{"slot_col": "c0", "source": {"header": "行号"}}, {"slot_col": "c1", "source": {"header": "品名"}}]}])
    job = api.job(pid)
    ws = openpyxl.load_workbook(io.BytesIO(next(iter(api.outputs(job["id"]).values()))))["订单"]
    assert ws["D5"].value == "合计" and ws["A7"].value == "备注：测试模板"        # rows below moved up by one
    assert ws["E5"].value == "=SUM(#REF!)" or "#REF!" in str(ws["E5"].value)     # total over removed rows is reported as #REF! (documented)


def test_s7_one_to_many_and_conflicts(api, fixtures):
    pid = api.project("S7")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s_name = api.text_slot(t["id"], "示例客户名称", "客户名称")
    s_hdr = api.text_slot(t["id"], "HT-000000", "合同编号", part_kind="header")
    s_id = api.text_slot(t["id"], "000000", "客户编号", ctx="客户编号 000000")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 3}})
    col = lambda h: {"kind": "column", "sheet": "客户", "header": h}
    # one source → two targets
    api.maps(pid, [{"type": "single", "sources": [col("客户编号")], "target": {"binding": s_id["binding"]}},
                   {"type": "single", "sources": [col("客户编号")], "target": {"binding": s_hdr["binding"]}},
                   {"type": "single", "sources": [col("姓名"), col("客户编号")], "combine": {"op": "concat", "sep": "/"}, "target": {"binding": s_name["binding"]}}])
    job = api.job(pid, naming="{序号}")
    d = docx.Document(io.BytesIO(api.outputs(job["id"])["001.docx"]))
    assert d.sections[0].header.paragraphs[0].text.endswith("000001") and "客户编号 000001" in d.paragraphs[1].text
    assert "测试钱磊敏/000001" in d.paragraphs[1].text                                   # explicit combine rule
    # two mappings writing to one target without a rule → blocked
    api.maps(pid, [{"type": "single", "sources": [col("姓名")], "target": {"binding": s_name["binding"]}},
                   {"type": "single", "sources": [col("备注")], "target": {"binding": s_name["binding"]}}])
    summ = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    assert any(i["code"] == "target_conflict" for i in summ["static_issues"])
    r = api.c.post(f"/api/projects/{pid}/jobs", json={})
    assert r.status_code == 409
    # multi-source without a combine rule is rejected as well
    api.maps(pid, [{"type": "single", "sources": [col("姓名"), col("备注")], "target": {"binding": s_name["binding"]}}])
    assert any(i["code"] == "combine_missing" for i in api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))["static_issues"])


def test_s8_structure_changes(api, fixtures):
    pid = api.project("S8")
    api.upload(pid, fixtures / "测试_客户100.xlsx", fixtures / "测试_客户_列顺序变化.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s_name = api.text_slot(t["id"], "示例客户名称", "客户名称")
    s_id = api.text_slot(t["id"], "000000", "客户编号", ctx="客户编号 000000")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}})
    col = lambda h, **kw: {"kind": "column", "sheet": "客户", "header": h, **kw}
    api.maps(pid, [{"type": "single", "sources": [col("姓名")], "target": {"binding": s_name["binding"]}},
                   {"type": "single", "sources": [col("客户编号")], "target": {"binding": s_id["binding"]}}])
    job = api.job(pid, naming="{来源文件名}_{客户编号}")
    assert job["counts"]["success"] == 105
    reordered = docx_text(api.outputs(job["id"])["测试_客户_列顺序变化_000003.docx"])
    assert "测试李桂洋" in reordered and "客户编号 000003" in reordered            # bound by header name, not position
    # duplicate header → must be disambiguated
    api.upload(pid, fixtures / "测试_客户_重名列.xlsx")
    summ = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    assert summ["issues_by_code"].get("ambiguous_column") == 1
    r = api.c.post(f"/api/projects/{pid}/jobs", json={"error_policy": "skip"})
    j = api.job(pid, error_policy="skip")
    assert j["counts"]["skipped"] == 1 and j["counts"]["success"] == 105            # not silently dropped: reported as skipped
    skipped = [i for i in api.items(j["id"]) if i["status"] == "skipped"][0]
    assert "同名列" in skipped["issues"][0]["message"]
    # explicit disambiguation fixes it
    api.maps(pid, [{"type": "single", "sources": [col("姓名", occurrence=2)], "target": {"binding": s_name["binding"]}},
                   {"type": "single", "sources": [col("客户编号")], "target": {"binding": s_id["binding"]}}])
    j = api.job(pid, naming="{来源文件名}_{序号}", error_policy="skip")
    names = api.outputs(j["id"])
    dup = [k for k in names if k.startswith("测试_客户_重名列")][0]
    assert "测试乙" in docx_text(names[dup])
    # missing column & missing/ambiguous sheet
    pid2 = api.project("S8b")
    api.upload_bytes(pid2, "缺列.xlsx", _xlsx([["客户编号", "金额"], ["000009", 1]], "客户"))
    api.upload_bytes(pid2, "工作表名不同.xlsx", _xlsx([["客户编号", "姓名"], ["1", "x"]], "Customers"))
    api.upload_bytes(pid2, "工作表歧义.xlsx", _xlsx([["客户编号", "姓名"]], "客户 ", extra_sheets=[" 客户"]))
    t2 = api.template(pid2, fixtures / "测试_合同模板.docx")
    s2 = api.text_slot(t2["id"], "示例客户名称", "客户名称")
    api.rule(pid2, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}})
    api.maps(pid2, [{"type": "single", "sources": [col("姓名")], "target": {"binding": s2["binding"]}}])
    recs = api.ok(api.c.get(f"/api/projects/{pid2}/records"))
    codes = {e["file"]: e["code"] for e in recs["file_errors"]}
    assert codes == {"工作表名不同.xlsx": "missing_sheet", "工作表歧义.xlsx": "ambiguous_sheet"}, codes
    summ = api.ok(api.c.get(f"/api/projects/{pid2}/generation-summary"))
    assert summ["issues_by_code"] == {"missing_column": 1}
    # DOCX anchor missing in one of the per-file sources
    pid3 = api.project("S8c")
    good = fixtures / "检测表20" / "测试_检测表_01.docx"
    d = docx.Document(good)
    d.paragraphs[1].text = "编号（字段名改了）：YP-9999"
    b = io.BytesIO()
    d.save(b)
    api.upload(pid3, good)
    api.upload_bytes(pid3, "结构变化.docx", b.getvalue())
    t3 = api.template(pid3, fixtures / "测试_检测报告模板.docx")
    s3 = api.text_slot(t3["id"], "XXXX", "样品编号")
    api.rule(pid3, {"mode": "per_file"})
    api.maps(pid3, [{"type": "single", "sources": [{"kind": "docx_text", "part": "word/document.xml", "path": "1", "prefix": "样品编号：", "suffix": ""}],
                     "target": {"binding": s3["binding"]}}])
    val = api.ok(api.c.get(f"/api/projects/{pid3}/validate"))
    res = {f["filename"]: list(f["results"].values())[0] for f in val["files"]}
    assert res["测试_检测表_01.docx"]["ok"] and res["测试_检测表_01.docx"]["value"] == "YP-0001"
    assert res["结构变化.docx"]["ok"] is False and res["结构变化.docx"]["code"] == "anchor_missing"
