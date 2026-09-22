"""PDF template repeat-detail areas: detection, filling, overflow policies, rotation."""

import openpyxl
import pymupdf
import pytest

from docweave.engine import pdf_repeat as pr

AREA = pymupdf.Rect(72, 172, 523, 304)          # 6 body rows below the header (unrotated points)
NO_BOX = pymupdf.Rect(112, 108, 290, 125)        # "DD-示例"


def _nrect(page: pymupdf.Page, r: pymupdf.Rect) -> list[float]:
    v = r * page.rotation_matrix
    v.normalize()
    return [v.x0 / page.rect.width, v.y0 / page.rect.height, v.x1 / page.rect.width, v.y1 / page.rect.height]


def _setup(api, fixtures, overflow="error", template="测试_发货单模板.pdf", page_no=0):
    pid = api.project(f"PDF明细-{overflow}")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    t = api.template(pid, fixtures / template)
    tid = t["id"]
    doc = pymupdf.open(fixtures / template)
    page = doc[page_no]
    det = api.ok(api.c.post(f"/api/templates/{tid}/pdf-repeat-detect", json={"page": page_no, "nrect": _nrect(page, AREA)}))
    assert [c["name"] for c in det["cols"]] == ["序号", "品名", "数量", "单价", "金额"]
    assert det["rows_fit"] == 6 and abs(det["row_height"] - 22) < 0.6
    no = api.slot(tid, kind="pdf_box", page=page_no, nrect=_nrect(page, NO_BOX), name="单号")
    rep = api.slot(tid, kind="pdf_repeat", page=page_no, nrect=_nrect(page, AREA), name="明细", overflow=overflow)
    assert rep["kind"] == "repeat" and rep["capacity"] == 6 and [c["name"] for c in rep["columns"]][1] == "品名"
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0},
                   "group": {"keys": [{"header": "订单号"}], "sort": [{"header": "行号", "dir": "asc", "numeric": True}]}})
    api.maps(pid, [
        {"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": no["binding"]}},
        {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
         "columns": [{"slot_col": "c0", "source": {"header": "行号"}}, {"slot_col": "c1", "source": {"header": "品名"}},
                     {"slot_col": "c2", "source": {"header": "数量"}}, {"slot_col": "c3", "source": {"header": "单价"}}]},
    ])
    return pid, tid, rep


def _expected(fixtures):
    ws = openpyxl.load_workbook(fixtures / "测试_订单明细100.xlsx")["明细"]
    out: dict[str, list] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        out.setdefault(r[0], []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r[2])
    return out


def _row_texts(page: pymupdf.Page, rect: pymupdf.Rect, row_h: float, n: int) -> list[str]:
    out = []
    for i in range(n):
        r = pymupdf.Rect(rect.x0, rect.y0 + i * row_h, rect.x1, rect.y0 + (i + 1) * row_h)
        ws = sorted([w for w in page.get_text("words") if r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))], key=lambda w: w[0])
        out.append(" ".join(w[4] for w in ws))
    return out


def test_overflow_error_is_reported_before_generation(api, fixtures):
    pid, tid, rep = _setup(api, fixtures, "error")
    exp = _expected(fixtures)
    big = [k for k, v in exp.items() if len(v) > 6]
    assert big, "fixture must contain orders with more rows than the area holds"
    r = api.c.post(f"/api/projects/{pid}/jobs", json={})
    assert r.status_code == 409 and "只能容纳 6 行" in r.text
    job = api.job(pid, naming="{订单号}", error_policy="skip")
    assert job["counts"]["success"] == len(exp) - len(big)
    outs = api.outputs(job["id"])
    small = next(k for k, v in exp.items() if len(v) <= 6)
    doc = pymupdf.open(stream=outs[f"{small}.pdf"], filetype="pdf")
    assert doc.page_count == 1
    page = doc[0]
    rows = _row_texts(page, AREA, 22, 6)
    for i, src in enumerate(exp[small]):
        assert rows[i].split()[:2] == [str(src[2]), src[3]], rows[i]
        assert f"{src[5]:.2f}".rstrip("0").rstrip(".") in rows[i] or str(src[5]) in rows[i]
    assert "示例商品" not in page.get_text() and "DD-示例" not in page.get_text()     # sample text truly removed
    assert small in page.get_text() and "合计" in page.get_text()
    # numbers are right-aligned inside their column
    qty = [w for w in page.get_text("words") if w[4] == str(exp[small][0][4]) and 292 <= w[0] <= 362 and 172 <= w[1] <= 194]
    assert qty and qty[0][2] > 350


def test_overflow_continue_copies_page(api, fixtures):
    pid, tid, rep = _setup(api, fixtures, "continue")
    exp = _expected(fixtures)
    job = api.job(pid, naming="{订单号}")
    assert job["status"] == "completed" and job["counts"]["success"] == len(exp)
    outs = api.outputs(job["id"])
    big = next(k for k, v in exp.items() if len(v) > 6)
    doc = pymupdf.open(stream=outs[f"{big}.pdf"], filetype="pdf")
    assert doc.page_count == 2
    got = _row_texts(doc[0], AREA, 22, 6) + _row_texts(doc[1], AREA, 22, 6)
    got = [g for g in got if g]
    assert [g.split()[1] for g in got] == [r[3] for r in exp[big]]
    for p in doc:                                             # header field repeated on the continuation page
        assert big in p.get_text() and "测试发货单" in p.get_text()
    items = api.items(job["id"])
    it = next(i for i in items if i["record_key"].endswith(big) or big in (i.get("record") or {}).get("label", ""))
    assert any(x.get("code") == "continued_pages" for x in it["issues"])


def test_overflow_shrink_keeps_one_page(api, fixtures):
    pid, tid, rep = _setup(api, fixtures, "shrink")
    exp = _expected(fixtures)
    job = api.job(pid, naming="{订单号}")
    assert job["counts"]["success"] == len(exp)
    big = next(k for k, v in exp.items() if len(v) > 6)
    doc = pymupdf.open(stream=api.outputs(job["id"])[f"{big}.pdf"], filetype="pdf")
    n = len(exp[big])
    assert doc.page_count == 1
    rows = _row_texts(doc[0], AREA, 132 / n, n)
    assert [r.split()[1] for r in rows] == [r[3] for r in exp[big]]
    # nothing spills below the area
    below = [w for w in doc[0].get_text("words") if 305 <= w[1] <= 320 and w[4].startswith("测试商品")]
    assert not below


def test_rotated_page_and_header_only_table(api, fixtures):
    for page_no in (0, 1):     # page 0: no rules (header-based columns); page 1: /Rotate 90
        pid, tid, rep = _setup(api, fixtures, "error", template="测试_发货单模板_无框线与旋转.pdf", page_no=page_no)
        exp = _expected(fixtures)
        small = next(k for k, v in exp.items() if len(v) <= 6)
        pv = api.ok(api.c.get(f"/api/projects/{pid}/preview", params={"search": small}))
        assert not [i for i in pv["issues"] if i["level"] == "error"], pv["issues"]
        job = api.job(pid, naming="{订单号}", error_policy="skip")
        doc = pymupdf.open(stream=api.outputs(job["id"])[f"{small}.pdf"], filetype="pdf")
        page = doc[page_no]
        rows = _row_texts(page, AREA, 22, len(exp[small]))
        assert [r.split()[1] for r in rows] == [r[3] for r in exp[small]]
        from docweave.engine.pdf_engine import text_rotation_in
        assert text_rotation_in(page, AREA) == 0          # written in the same direction as the template text


def test_slot_grid_and_edit(api, fixtures):
    pid, tid, rep = _setup(api, fixtures, "error")
    detail = api.ok(api.c.get(f"/api/templates/{tid}"))
    s = next(x for x in detail["version"]["slots"] if x["id"] == rep["id"])
    assert len([g for g in s["ngrid"] if g["kind"] == "col"]) == 4 and len([g for g in s["ngrid"] if g["kind"] == "row"]) == 5
    upd = api.ok(api.c.patch(f"/api/templates/{tid}/slots/{rep['id']}", json={"overflow": "continue", "columns": [{"key": "c2", "name": "件数", "align": "center"}]}))
    s = next(x for x in upd["version"]["slots"] if x["id"] == rep["id"])
    assert s["overflow"] == "continue" and s["columns"][2]["name"] == "件数" and s["layout"]["cols"][2]["align"] == "center"
    r = api.c.patch(f"/api/templates/{tid}/slots/{rep['id']}", json={"overflow": "bogus"})
    assert r.status_code == 400


def test_layout_validation():
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    lay = {"trot": 0, "trect": [10, 10, 200, 100], "row_height": 20, "cols": [{"key": "c0", "name": "a", "x0": 10, "x1": 12}]}
    with pytest.raises(Exception, match="太窄"):
        pr.validate_layout(lay, page)
    lay["cols"] = [{"key": "c0", "name": "a", "x0": 10, "x1": 200}]
    lay["row_height"] = 500
    with pytest.raises(Exception, match="行高"):
        pr.validate_layout(lay, page)
    lay["row_height"] = 20
    lay["trect"] = [10, 10, 400, 100]
    lay["cols"] = [{"key": "c0", "name": "a", "x0": 10, "x1": 400}]
    with pytest.raises(Exception, match="超出页面"):
        pr.validate_layout(lay, page)
    assert pr.plan_pages({"layout": {"trect": [0, 0, 100, 100], "row_height": 20}, "overflow": "error"}, 5)["mode"] == "fit"
    assert pr.plan_pages({"layout": {"trect": [0, 0, 100, 100], "row_height": 20}, "overflow": "continue"}, 11)["pages"] == 3
    assert pr.plan_pages({"layout": {"trect": [0, 0, 100, 100], "row_height": 20}, "overflow": "shrink"}, 11)["mode"] == "error"
