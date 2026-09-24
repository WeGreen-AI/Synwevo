"""What a job hands back: the report, the archive, item pages and error details."""
from __future__ import annotations

import csv
import io
import zipfile

import openpyxl

from synwevo_docs.engine import generate as G
from synwevo_docs.engine import records as R
from synwevo_docs.engine import tabular
from synwevo_docs.engine import values as V
from synwevo_docs.engine.resolve import SourceDoc


def _xlsx(rows):
    """TEST FIXTURE: a synthetic workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户"
    for r in rows:
        ws.append(list(r))
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def _project(api, fixtures, data: bytes, filename="客户.xlsx"):
    pid = api.project("输出")
    api.upload_bytes(pid, filename, data)
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}, "id_column": {"header": "编号"}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid


def test_the_report_neutralises_spreadsheet_formulas(api, fixtures):
    """F106/F137/F348: a label is a cell value from someone else's file; "+HYPERLINK(…)" or a
    DDE payload in it would execute when the operator opens the report in Excel."""
    rows = [("编号", "姓名"), ("+HYPERLINK(\"http://x\",\"点我\")", "甲"), ("@SUM(1+1)*cmd|' /C calc'!A0", "乙"), ("-2+3", "丙"), ("正常", "丁")]
    pid = _project(api, fixtures, _xlsx(rows), filename="=cmd.xlsx")
    job = api.job(pid)
    assert job["status"] == "completed"
    text = api.ok_bytes(api.c.get(f"/api/jobs/{job['id']}/report.csv")).decode("utf-8-sig")
    table = list(csv.reader(io.StringIO(text)))
    for row in table[1:]:
        for cell in row[1:]:
            assert not cell or cell[0] not in "=+-@\t\r", row
    labels = [r[1] for r in table[1:]]
    assert labels[0].startswith("'+HYPERLINK") and labels[1].startswith("'@SUM") and labels[3].startswith("正常")
    assert all(r[4] == "'=cmd.xlsx" for r in table[1:])
    # the same report inside the archive
    z = zipfile.ZipFile(io.BytesIO(api.ok_bytes(api.c.get(f"/api/jobs/{job['id']}/zip"))))
    assert "'+HYPERLINK" in z.read("生成清单.csv").decode("utf-8-sig")


def test_item_failures_do_not_hand_out_tracebacks(api, fixtures, monkeypatch):
    """F120/F150: the stored issue carried the last 800 characters of a traceback — server paths,
    library versions — and was returned to every reader of the job."""
    pid = _project(api, fixtures, _xlsx([("编号", "姓名"), ("1", "甲"), ("2", "乙")]))

    def boom(*a, **k):
        raise RuntimeError("模拟的渲染故障")

    monkeypatch.setattr(G, "fill_checked", boom)
    job = api.job(pid)
    items = api.items(job["id"])
    assert {i["status"] for i in items} == {"failed"}
    for i in items:
        assert i["issues"][0]["message"] == "RuntimeError: 模拟的渲染故障" and "trace" not in i["issues"][0]
    # rows stored by earlier versions still carry a trace: it is removed on the way out
    store = api.c.app_state.app.store
    store.execute("UPDATE job_items SET issues=? WHERE id=?",
                  ('[{"level":"error","code":"exception","message":"x","trace":"File \\"/srv/app/secret.py\\""}]', items[0]["id"]))
    assert "trace" not in api.items(job["id"])[0]["issues"][0]


def test_an_unchanged_job_s_archive_is_byte_identical(api, fixtures):
    """F309: the manifest used to be stamped with the time of the download, so a resumed range
    request could splice two different builds."""
    pid = _project(api, fixtures, _xlsx([("编号", "姓名"), ("1", "甲"), ("2", "乙")]))
    job = api.job(pid)
    a = api.ok_bytes(api.c.get(f"/api/jobs/{job['id']}/zip"))
    b = api.ok_bytes(api.c.get(f"/api/jobs/{job['id']}/zip"))
    assert a == b


def test_items_can_be_paged_and_filtered_on_the_server(api, fixtures):
    rows = [("编号", "姓名")] + [(str(i), f"客户{i}" if i % 3 else None) for i in range(1, 13)]
    pid = _project(api, fixtures, _xlsx(rows))
    t = api.ok(api.c.get(f"/api/projects/{pid}"))["template"]
    slot = t["version"]["slots"][0]
    api.ok(api.c.patch(f"/api/templates/{t['id']}/slots/{slot['id']}", json={"required": True}))
    job = api.job(pid, error_policy="skip")
    assert job["counts"]["skipped"] == 4 and job["counts"]["success"] == 8
    page = api.ok(api.c.get(f"/api/jobs/{job['id']}/items", params={"status": "success", "paged": "true", "limit": 3}))
    assert page["total"] == 8 and len(page["items"]) == 3 and page["next_offset"] == 3
    assert {i["status"] for i in page["items"]} == {"success"}
    last = api.ok(api.c.get(f"/api/jobs/{job['id']}/items", params={"status": "success", "paged": "true", "offset": 6, "limit": 3}))
    assert len(last["items"]) == 2 and last["next_offset"] is None
    assert isinstance(api.ok(api.c.get(f"/api/jobs/{job['id']}/items")), list)          # the unpaged shape is unchanged


def test_group_keys_are_unambiguous():
    """F170: ("a / b", "c") and ("a", "b / c") produced the same record key."""
    rows = [[V.text("甲"), V.text("乙"), V.text("值")], [V.text("a / b"), V.text("c"), V.text("1")],
            [V.text("a"), V.text("b / c"), V.text("2")], [V.text("DD-1"), V.text("x"), V.text("3")]]
    src = SourceDoc("f1", "a.xlsx", "xlsx", "table", workbook_json={"sheets": []})
    src._wb = tabular.Workbook([tabular.Sheet("S", 0, rows)])
    recs = R.build_records([src], {"mode": "per_group", "table": {"sheet": "S", "header_row": 0},
                                   "group": {"keys": [{"header": "甲"}, {"header": "乙"}]}})["records"]
    keys = [r["key"] for r in recs]
    assert len(recs) == 3 and len(set(keys)) == 3
    assert keys[2] == "f1|DD-1 / x"                                         # plain values keep the readable key
    assert all(r["label"].startswith("a / b / c") for r in recs[:2])     # labels are for people and unchanged
