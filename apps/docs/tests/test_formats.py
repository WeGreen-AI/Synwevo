"""Format coverage beyond the core flow: PPTX, legacy formats through LibreOffice,
HTML/TXT/Markdown sources, CSV export, built-in editors, and safety guards."""
import base64
import io
import zipfile

import docx
import openpyxl
import pytest
from pptx import Presentation

from .helpers import docx_text
from docweave.engine.converters import convert


def test_pptx_source_and_template(api, fixtures):
    pid = api.project("PPTX")
    api.upload(pid, fixtures / "测试_汇报模板.pptx")
    src = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]
    view = api.ok(api.c.get(f"/api/sources/{src['id']}/view"))
    shape = next(sh for sh in view["slides"]["slides"][0]["shapes"] if sh["name"] == "客户信息")
    text = shape["paras"][0]["text"]
    s = text.index("示例客户")
    prop = api.ok(api.c.post(f"/api/sources/{src['id']}/locator", json={"project_id": pid, "selection": {
        "kind": "pptx_text", "slide": 0, "shape": shape["id"], "para": 0, "start": s, "end": s + 4}}))["proposals"][0]
    assert prop["sample"] == "示例客户"
    t = api.template(pid, fixtures / "测试_汇报模板.pptx")
    tv = api.ok(api.c.get(f"/api/templates/{t['id']}/view"))
    tshape = next(sh for sh in tv["slides"]["slides"][0]["shapes"] if sh["name"] == "客户信息")
    tt = tshape["paras"][0]["text"]
    slot = api.slot(t["id"], kind="text", slide=0, shape=tshape["id"], para=0, start=tt.index("000000"), end=tt.index("000000") + 6, name="编号")
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "single", "sources": [prop["locator"]], "target": {"binding": slot["binding"]}}])
    job = api.job(pid)
    assert job["status"] == "completed"
    prs = Presentation(io.BytesIO(next(iter(api.outputs(job["id"]).values()))))
    box = next(sh for sh in prs.slides[0].shapes if sh.name == "客户信息")
    assert box.text_frame.text == "客户：示例客户，编号 示例客户"          # only the field range changed, rest kept


@pytest.mark.parametrize("src_fmt,dst", [("xlsx", "xls"), ("xlsx", "ods")])
def test_legacy_spreadsheets_as_sources(api, fixtures, src_fmt, dst):
    data = convert((fixtures / "测试_客户100.xlsx").read_bytes(), src_fmt, dst)
    pid = api.project(dst)
    api.upload_bytes(pid, f"客户.{dst}", data)
    src = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]
    assert src["status"] == "ready" and src["format"] == dst
    assert any("LibreOffice" in n or "XLS" in n for n in src["notes"])            # the conversion path is disclosed
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    i = api.text_slot(t["id"], "000000", "客户编号", ctx="客户编号 000000")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 3}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}},
                   {"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "客户编号"}], "target": {"binding": i["binding"]}}])
    job = api.job(pid)
    text = docx_text(api.outputs(job["id"])["001.docx"])
    assert "测试钱磊敏" in text and "000001" in text


@pytest.mark.parametrize("dst", ["doc", "odt", "rtf"])
def test_legacy_documents_as_sources_and_templates(api, fixtures, dst):
    src_data = convert((fixtures / "检测表20" / "测试_检测表_03.docx").read_bytes(), "docx", dst)
    tpl_data = convert((fixtures / "测试_检测报告模板.docx").read_bytes(), "docx", dst)
    pid = api.project(dst)
    api.upload_bytes(pid, f"检测表.{dst}", src_data)
    src = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]
    assert src["status"] == "ready", src
    r = api.c.post("/api/templates/upload", files={"file": (f"报告模板.{dst}", tpl_data)}, data={"project_id": pid})
    t = api.ok(r)
    assert t["format"] == "docx" and any("转换" in n for n in t["info"]["notes"])
    s = api.text_slot(t["id"], "XXXX", "样品编号")
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "docx_text", "part": "word/document.xml", "path": "1", "prefix": "样品编号：", "suffix": ""}],
                    "target": {"binding": s["binding"]}}])
    job = api.job(pid)
    assert job["status"] == "completed", api.items(job["id"])
    assert "YP-0003" in docx_text(next(iter(api.outputs(job["id"]).values())))


def test_legacy_ppt_template(api, fixtures):
    data = convert((fixtures / "测试_汇报模板.pptx").read_bytes(), "pptx", "ppt")
    pid = api.project("ppt")
    t = api.ok(api.c.post("/api/templates/upload", files={"file": ("汇报.ppt", data)}, data={"project_id": pid}))
    assert t["format"] == "pptx"


def test_html_txt_md_sources(api):
    pid = api.project("text")
    html = "<html><body><script>alert('x')</script><h1>测试</h1><p>客户：测试甲</p><table><tr><th>编号</th><th>名称</th></tr><tr><td>001</td><td>甲</td></tr></table></body></html>".encode("utf-8")
    api.upload_bytes(pid, "页面.html", html)
    api.upload_bytes(pid, "说明.md", "# 标题\n客户：测试乙\n".encode())
    api.upload_bytes(pid, "说明.txt", "客户：测试丙\n编号：00012\n".encode("gb18030"))
    srcs = {s["filename"]: s for s in api.ok(api.c.get(f"/api/projects/{pid}/sources"))}
    v = api.ok(api.c.get(f"/api/sources/{srcs['页面.html']['id']}/view"))
    assert v["view"] == "text" and "alert" not in "\n".join(v["lines"])           # scripts are dropped, never rendered
    assert v["grid"]["rows"][1][0]["v"] == "001"                                   # HTML tables selectable as a grid
    t = api.ok(api.c.get(f"/api/sources/{srcs['说明.txt']['id']}/view"))
    assert t["lines"][1] == "编号：00012"                                          # GB18030 text decoded
    prop = api.ok(api.c.post(f"/api/sources/{srcs['说明.txt']['id']}/locator", json={"project_id": pid, "selection": {"kind": "text_span", "line": 1, "start": 3, "end": 8}}))
    assert prop["proposals"][0]["sample"] == "00012"


def test_csv_export_from_xlsx_template(api, fixtures):
    pid = api.project("csv-out")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    t = api.template(pid, fixtures / "测试_订单模板.xlsx")
    s = api.slot(t["id"], kind="cell", sheet="订单", cell="B2", name="订单号")
    rep = api.slot(t["id"], kind="repeat", sheet="订单", row=4, c1=0, c2=3, name="明细")
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0}, "group": {"keys": [{"header": "订单号"}], "sort": [{"header": "行号", "numeric": True}]}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": s["binding"]}},
                   {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
                    "columns": [{"slot_col": f"c{i}", "source": {"header": h}} for i, h in enumerate(["行号", "品名", "数量", "单价"])]}])
    job = api.job(pid, formats=["xlsx", "csv"], naming="{订单号}")
    assert job["status"] == "completed" and job["counts"]["files"] == 30
    csv_text = api.outputs(job["id"])["DD-001.csv"].decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    assert lines[1].startswith("订单号,DD-001") and any(l.startswith("1,测试商品") for l in lines)
    assert any("合计" in l for l in lines)                                            # formula results computed (LibreOffice)


def _png_data_url():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (40, 20), (200, 30, 30)).save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def test_builtin_document_template(api, fixtures):
    pid = api.project("builtin-doc")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    fld = lambda i, n: {"type": "field", "attrs": {"id": i, "name": n}}
    editor = {
        "page": {"size": "A4", "orientation": "landscape", "margins": {"top": 20, "bottom": 20, "left": 15, "right": 15}},
        "header": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "订单："}, fld("fh", "订单号")]}]},
        "footer": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "测试页脚"}]}]},
        "body": {"type": "doc", "content": [
            {"type": "heading", "attrs": {"level": 1, "textAlign": "center"}, "content": [{"type": "text", "text": "订单确认（内置模板）"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "客户：", "marks": [{"type": "bold"}]}, fld("fc", "客户"),
                                              {"type": "text", "text": "，订单号 "}, fld("fo", "订单号")]},
            {"type": "image", "attrs": {"src": _png_data_url(), "width": 40}},
            {"type": "table", "content": [
                {"type": "tableRow", "content": [{"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": h}]}]} for h in ["品名", "数量"]]},
                {"type": "tableRow", "attrs": {"repeat": True, "repeatId": "rep1", "repeatName": "明细"},
                 "content": [{"type": "tableCell", "content": [{"type": "paragraph", "content": [fld("c1", "品名")]}]},
                             {"type": "tableCell", "content": [{"type": "paragraph", "content": [fld("c2", "数量")]}]}]},
                {"type": "tableRow", "content": [{"type": "tableCell", "attrs": {"colspan": 2}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "（合并单元格）"}]}]}]},
            ]},
            {"type": "pageBreak"},
            {"type": "paragraph", "content": [{"type": "text", "text": "第二页"}]},
        ]},
    }
    t = api.ok(api.c.post("/api/templates/builtin", json={"kind": "builtin_doc", "name": "内置订单", "editor": editor, "project_id": pid}))
    slots = {s["name"]: s for s in t["version"]["slots"]}
    assert slots["订单号"]["binding"] == [s for s in t["version"]["slots"] if s["name"] == "订单号"][1]["binding"]   # same name → shared binding
    assert slots["明细"]["kind"] == "repeat" and [c["name"] for c in slots["明细"]["columns"]] == ["品名", "数量"]
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0}, "group": {"keys": [{"header": "订单号"}]}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": slots["订单号"]["binding"]}},
                   {"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "客户"}], "target": {"binding": slots["客户"]["binding"]}},
                   {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": slots["明细"]["binding"]},
                    "columns": [{"slot_col": "c1", "source": {"header": "品名"}}, {"slot_col": "c2", "source": {"header": "数量"}}]}])
    job = api.job(pid, naming="{订单号}")
    assert job["status"] == "completed", api.items(job["id"])[:1]
    d = docx.Document(io.BytesIO(api.outputs(job["id"])["DD-001.docx"]))
    assert d.sections[0].header.paragraphs[0].text == "订单：DD-001" and d.sections[0].footer.paragraphs[0].text == "测试页脚"
    assert d.sections[0].page_width > d.sections[0].page_height                      # landscape
    assert "客户：测试客户1，订单号 DD-001" in "\n".join(p.text for p in d.paragraphs)
    assert d.tables[0].rows[-1].cells[0].text == "（合并单元格）" and len(d.inline_shapes) == 1
    # editing the template again creates a new version
    editor["body"]["content"][1]["content"].append({"type": "text", "text": "。"})
    t2 = api.ok(api.c.put(f"/api/templates/{t['id']}/builtin", json={"editor": editor}))
    assert t2["version"]["no"] == 2 and t2["can_undo"]


def test_builtin_sheet_template(api, fixtures):
    pid = api.project("builtin-sheet")
    api.upload(pid, fixtures / "测试_区域来源3x4.xlsx")
    editor = {"sheets": [{"name": "报表", "cols": [{"w": 120}] * 6, "rows": [{"h": 28}] * 12,
                          "cells": {"A1": {"v": "内置表格模板", "s": {"b": 1, "sz": 14, "bg": "#DDEBF7"}}, "A3": {"v": "标题", "s": {"bd": 1}}, "B3": {"v": "", "s": {"bd": 1, "nf": "@"}}},
                          "merges": ["A1:F1"],
                          "fields": [{"id": "fT", "name": "标题", "kind": "cell", "ref": "B3", "required": True},
                                     {"id": "fR", "name": "区域", "kind": "region", "ref": "B5:E7"}]}]}
    t = api.ok(api.c.post("/api/templates/builtin", json={"kind": "builtin_sheet", "name": "内置报表", "editor": editor, "project_id": pid}))
    slots = {s["name"]: s for s in t["version"]["slots"]}
    assert slots["区域"]["rows"] == 3 and slots["标题"]["required"]
    api.rule(pid, {"mode": "per_file"})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "cell", "sheet": "数据", "cell": "A1"}], "target": {"binding": "fT"}},
                   {"type": "region", "sources": [{"kind": "range", "sheet": "数据", "range": "B3:E5"}], "target": {"binding": "fR"}}])
    job = api.job(pid)
    ws = openpyxl.load_workbook(io.BytesIO(next(iter(api.outputs(job["id"]).values()))))["报表"]
    assert ws["B3"].value == "季度数据（测试）" and ws["E7"].value == "R3C4" and "A1:F1" in {str(m) for m in ws.merged_cells.ranges}
    assert not any(n.startswith("_dw_") for n in ws.parent.defined_names)             # working-copy markers removed from outputs


def test_upload_guards(api, fixtures):
    pid = api.project("guards")
    out = api.upload_bytes(pid, "../../etc/passwd.xlsx", (fixtures / "测试_客户100.xlsx").read_bytes())
    assert "/" not in out[0]["filename"]
    api.upload(pid, fixtures / "测试_损坏文件.xlsx", fixtures / "测试_不支持.bin")
    srcs = {s["filename"]: s for s in api.ok(api.c.get(f"/api/projects/{pid}/sources"))}
    assert srcs["测试_损坏文件.xlsx"]["status"] == "unsupported" and "损坏" in srcs["测试_损坏文件.xlsx"]["error"]
    assert srcs["测试_不支持.bin"]["status"] == "unsupported" and srcs["测试_不支持.bin"]["error"]
    # zip bomb style archive is refused before inflating
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/huge.bin", b"\0" * (700 * 1024 * 1024))
    api.upload_bytes(pid, "炸弹.xlsx", b.getvalue())
    bomb = [s for s in api.ok(api.c.get(f"/api/projects/{pid}/sources")) if s["filename"] == "炸弹.xlsx"][0]
    assert bomb["status"] == "error" and "上限" in bomb["error"]
    # macros are detected and never executed
    r = api.c.get("/api/capabilities")
    assert r.status_code == 200


def test_legacy_odp_template(api, fixtures):
    data = convert((fixtures / "测试_汇报模板.pptx").read_bytes(), "pptx", "odp")
    pid = api.project("odp")
    t = api.ok(api.c.post("/api/templates/upload", files={"file": ("汇报.odp", data)}, data={"project_id": pid}))
    assert t["format"] == "pptx" and any("转换" in n for n in t["info"]["notes"])


def test_search_whole_file(api, fixtures):
    pid = api.project("search")
    api.upload(pid, fixtures / "测试_客户100.xlsx", fixtures / "测试_申请表.pdf", fixtures / "测试_合同模板.docx")
    srcs = {s["filename"]: s for s in api.ok(api.c.get(f"/api/projects/{pid}/sources"))}
    r = api.ok(api.c.get(f"/api/sources/{srcs['测试_客户100.xlsx']['id']}/search", params={"q": "000100"}))
    assert r["total"] == 1 and r["hits"][0]["r"] == 100                      # row far outside the first loaded window
    r = api.ok(api.c.get(f"/api/sources/{srcs['测试_申请表.pdf']['id']}/search", params={"q": "TEST00000003"}))
    assert r["hits"][0]["page"] == 2
    r = api.ok(api.c.get(f"/api/sources/{srcs['测试_合同模板.docx']['id']}/search", params={"q": "合同编号"}))
    assert {h["part"] for h in r["hits"]} >= {"word/header1.xml"}             # headers/footers searched too
