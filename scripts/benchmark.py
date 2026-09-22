"""Measure representative batch sizes on the current machine (numbers go into docs/ACCEPTANCE.md).

    backend/.venv/bin/python scripts/benchmark.py
All data is synthetic.  Each case runs through the real HTTP API in-process
(parse → records → plan → fill → verify → store), i.e. what a user's job does.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

import openpyxl  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from conftest import Api  # noqa: E402
from docweave.api.app import create_app  # noqa: E402

DATA = ROOT / "testdata"


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if platform.system() == "Darwin" else r / 1024


def customers(n: int) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("客户")
    ws.append(["客户编号", "姓名", "签约日期", "金额", "备注"])
    for i in range(1, n + 1):
        ws.append([f"{i:06d}", f"测试客户{i}", dt.date(2025, 1, 1) + dt.timedelta(days=i % 365), 1000 + i * 1.25, ""])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def details(n: int, groups: int) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("明细")
    ws.append(["订单号", "客户", "行号", "品名", "数量", "单价"])
    for i in range(n):
        g = i % groups
        ws.append([f"DD-{g + 1:04d}", f"测试客户{g + 1}", i // groups + 1, f"测试商品{i % 50}", i % 5 + 1, 9.9])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def timed_job(a: Api, pid: str, **opts):
    t0 = time.time()
    j = a.job(pid, timeout=3600, **opts)
    return j, time.time() - t0


def case_rows(a: Api, n: int, formats=("docx",)):
    pid = a.project(f"bench rows {n}")
    t0 = time.time()
    a.upload_bytes(pid, f"客户{n}.xlsx", customers(n))
    parse_s = time.time() - t0
    t = a.template(pid, DATA / "测试_合同模板.docx")
    s1 = a.text_slot(t["id"], "示例客户名称", "客户名称")
    s2 = a.text_slot(t["id"], "000000", "客户编号", ctx="客户编号 000000")
    s3 = a.text_slot(t["id"], "2020-01-01", "签约日期")
    s4 = a.text_slot(t["id"], "0.00", "金额", ctx="合同金额")
    a.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}})
    col = lambda h: {"kind": "column", "sheet": "客户", "header": h}
    a.maps(pid, [{"type": "single", "sources": [col("姓名")], "target": {"binding": s1["binding"]}},
                 {"type": "single", "sources": [col("客户编号")], "target": {"binding": s2["binding"]}},
                 {"type": "single", "sources": [col("签约日期")], "target": {"binding": s3["binding"]}},
                 {"type": "single", "sources": [col("金额")], "target": {"binding": s4["binding"]}}])
    j, secs = timed_job(a, pid, formats=list(formats), naming="{客户编号}")
    assert j["counts"]["success"] == n, j
    return {"case": f"XLSX {n} 行 → DOCX（每行一份，4 个字段）" + (" + PDF" if "pdf" in formats else ""), "samples": n,
            "files": j["counts"]["files"], "parse_s": round(parse_s, 2), "job_s": round(secs, 2), "per_file_ms": round(secs * 1000 / j["counts"]["files"], 1)}


def case_groups(a: Api, n: int, groups: int):
    pid = a.project("bench groups")
    a.upload_bytes(pid, "明细.xlsx", details(n, groups))
    t = a.template(pid, DATA / "测试_订单模板.xlsx")
    s = a.slot(t["id"], kind="cell", sheet="订单", cell="B2", name="订单号")
    rep = a.slot(t["id"], kind="repeat", sheet="订单", row=4, c1=0, c2=3, name="明细")
    a.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0}, "group": {"keys": [{"header": "订单号"}], "sort": [{"header": "行号", "numeric": True}]}})
    a.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": s["binding"]}},
                 {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
                  "columns": [{"slot_col": f"c{i}", "source": {"header": h}} for i, h in enumerate(["行号", "品名", "数量", "单价"])]}])
    j, secs = timed_job(a, pid, naming="{订单号}")
    assert j["counts"]["success"] == groups, j
    return {"case": f"XLSX {n} 条明细 / {groups} 组 → XLSX（每组一份，插入明细行+公式平移）", "samples": groups, "files": groups,
            "job_s": round(secs, 2), "per_file_ms": round(secs * 1000 / groups, 1)}


def case_pdf_template(a: Api, n: int):
    import pymupdf
    pid = a.project("bench pdf")
    a.upload_bytes(pid, "客户.xlsx", customers(n))
    t = a.template(pid, DATA / "测试_通知书模板.pdf")
    doc = pymupdf.open(DATA / "测试_通知书模板.pdf")
    pg = doc[0]
    w = [x for x in pg.get_text("words") if x[4] == "旧姓名样例"][0]
    nr = [(w[0] - 2) / pg.rect.width, (w[1] - 2) / pg.rect.height, (w[2] + 60) / pg.rect.width, (w[3] + 2) / pg.rect.height]
    s = a.slot(t["id"], kind="pdf_box", page=0, nrect=nr, name="收件人")
    a.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}})
    a.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    j, secs = timed_job(a, pid)
    assert j["counts"]["success"] == n
    return {"case": f"PDF 模板 {n} 份（删除原文字 + 嵌入中文字体子集）", "samples": n, "files": n, "job_s": round(secs, 2), "per_file_ms": round(secs * 1000 / n, 1)}


def case_ocr():
    from docweave.engine.images import to_png
    from docweave.engine.ocr import ocr_png
    png = to_png((DATA / "测试_扫描单据.png").read_bytes())
    t0 = time.time()
    r = ocr_png(png)
    return {"case": "OCR 一张 1240×700 扫描图片（Tesseract chi_sim+eng）", "samples": 1, "files": 0, "job_s": round(time.time() - t0, 2), "words": len(r["words"])}


def main():
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(tmp)
        with TestClient(app) as c:
            a = Api(c)
            cases.append(case_rows(a, 100))
            cases.append(case_rows(a, 1000))
            cases.append(case_rows(a, 5000))
            cases.append(case_rows(a, 20, formats=("docx", "pdf")))
            cases.append(case_groups(a, 2000, 200))
            cases.append(case_pdf_template(a, 300))
            cases.append(case_ocr())
        app.state.jobs.stop()
    env = {"machine": platform.machine(), "system": f"{platform.system()} {platform.release()}", "python": platform.python_version(),
           "cpus": os.cpu_count(), "peak_rss_mb": round(rss_mb(), 1)}
    print(json.dumps({"env": env, "cases": cases}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
