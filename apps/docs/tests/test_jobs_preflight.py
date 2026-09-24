"""Before generation: a simulated run through the real renderer (R01), every located issue
(R02), and a summary that is not recomputed for every request."""
from __future__ import annotations

import io

import openpyxl
import pymupdf

from synwevo_docs import jobs as J

NO_BOX = pymupdf.Rect(112, 108, 290, 125)        # "DD-示例" on the delivery-note template


def _xlsx(rows: list[tuple]) -> bytes:
    """TEST FIXTURE: a small synthetic workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _nrect(page, r):
    v = r * page.rotation_matrix
    v.normalize()
    return [v.x0 / page.rect.width, v.y0 / page.rect.height, v.x1 / page.rect.width, v.y1 / page.rect.height]


def test_simulate_runs_the_renderer_and_stores_nothing(api, fixtures):
    """R01 acceptance: a PDF box that a 400-character value cannot fit. The plan is fine (valid
    3); only filling finds the overflow, which used to surface as a failed item after submission."""
    pid = api.project("模拟生成")
    api.upload_bytes(pid, "三行.xlsx", _xlsx([("备注",), ("短",), ("也短",), ("很长的备注" * 80,)]))
    t = api.template(pid, fixtures / "测试_发货单模板.pdf")
    page = pymupdf.open(fixtures / "测试_发货单模板.pdf")[0]
    box = api.slot(t["id"], kind="pdf_box", page=0, nrect=_nrect(page, NO_BOX), name="备注")
    api.ok(api.c.patch(f"/api/templates/{t['id']}/slots/{box['id']}", json={"min_font_size": 6}))
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "Sheet1", "header_row": 0}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "Sheet1", "header": "备注"}], "target": {"binding": box["binding"]}}])
    store = api.c.app_state.app.store
    blobs_before = sum(1 for _ in (store.root / "blobs").rglob("*") if _.is_file())
    arts_before = store.one("SELECT COUNT(*) AS n FROM artifacts")["n"]
    events_before = store.one("SELECT COUNT(*) AS n FROM job_events")["n"]

    plain = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    assert plain["valid"] == 3 and "simulation" not in plain
    sim = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary", params={"simulate": "true"}))
    assert sim["valid"] == 2 and sim["invalid"] == 1 and sim["simulation"]["checked"] == 3
    bad = sim["invalid_samples"][0]
    assert bad["index"] == 2 and bad["issues"][0]["code"] == "overflow" and bad["issues"][0]["stage"] == "render"
    assert sim["expected_outputs"] == 2 and sim["issues_by_code"]["overflow"] == 1

    assert sum(1 for _ in (store.root / "blobs").rglob("*") if _.is_file()) == blobs_before
    assert store.one("SELECT COUNT(*) AS n FROM artifacts")["n"] == arts_before
    assert store.one("SELECT COUNT(*) AS n FROM job_events")["n"] == events_before

    limited = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary", params={"simulate": "true", "simulate_limit": 2}))
    assert limited["simulation"] == {**limited["simulation"], "checked": 2, "limited": True} and limited["valid"] == 3


def test_every_located_issue_is_listed_not_the_first_50(api, fixtures):
    """R02: 200 rows, 120 of them missing a required value. The summary keeps its 50-sample
    excerpt; the issue list has all 120, each with its file and cell."""
    rows = [("编号", "姓名")] + [(f"N{i:03d}", None if i < 120 else f"客户{i}") for i in range(200)]
    pid = api.project("逐条问题")
    api.upload_bytes(pid, "客户.xlsx", _xlsx(rows))
    t = api.template(pid, fixtures / "测试_订单模板.xlsx")
    slot = api.slot(t["id"], kind="cell", sheet="订单", cell="B2", name="姓名", required=True)
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "Sheet1", "header_row": 0}, "id_column": {"header": "编号"}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "Sheet1", "header": "姓名"}],
                    "target": {"binding": slot["binding"]}}])
    summ = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    assert summ["invalid"] == 120 and len(summ["invalid_samples"]) == 50
    errs = api.ok(api.c.get(f"/api/projects/{pid}/generation-issues", params={"level": "error", "limit": 50}))
    assert errs["total"] == 120 and errs["next_offset"] == 50 and len(errs["items"]) == 50
    first = errs["items"][0]
    assert first["seq"] == 0 and first["file_name"] == "客户.xlsx" and first["where"] == "Sheet1!2" and first["slot"] == "姓名"
    assert all(r["where"].startswith("Sheet1!") and r["level"] == "error" for r in errs["items"])
    tail = api.ok(api.c.get(f"/api/projects/{pid}/generation-issues", params={"level": "error", "offset": 100, "limit": 50}))
    assert len(tail["items"]) == 20 and tail["next_offset"] is None
    assert api.c.get(f"/api/projects/{pid}/generation-issues", params={"level": "fatal"}).status_code == 400
    job = api.job(pid, error_policy="skip")
    assert job["counts"]["success"] == 80 and job["counts"]["skipped"] == 120


def test_the_summary_is_planned_once_per_project_state(api, fixtures, monkeypatch):
    pid = api.project("预检缓存")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    calls = {"n": 0}
    real = J.ProjectContext.plan

    def counting(self, rec):
        calls["n"] += 1
        return real(self, rec)

    monkeypatch.setattr(J.ProjectContext, "plan", counting)
    first = api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))
    planned = calls["n"]
    assert first["records"] == 100 and planned >= 100
    api.ok(api.c.get(f"/api/projects/{pid}/generation-summary", params={"naming": "{客户名称}"}))
    api.ok(api.c.get(f"/api/projects/{pid}/generation-issues"))
    assert calls["n"] - planned <= 10                                      # naming examples only, no re-planning
    # any edit changes the signature and is planned again
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 10}})
    assert api.ok(api.c.get(f"/api/projects/{pid}/generation-summary"))["records"] == 10
