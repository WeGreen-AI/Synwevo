"""The shared acceptance kit: synthetic inputs and the outputs every client must produce.

    python apps/docs/sdk/acceptance/make_kit.py <out-dir>

Writes, deterministically (same bytes of meaning on every run):

* ``customers.xlsx`` — sheet 「客户」, a header row and 12 **synthetic** customers. Every value is
  made up for testing; none of it is anyone's data.
* ``template.docx`` — a one-page confirmation letter with placeholder text where fields go.
* ``fields.json`` — the template's fields as a ``docweave.template-fields`` v1 definition, so a
  client creates them **through the API**, not by clicking in a browser.
* ``project.json`` — rule and mappings as a ``docweave.project`` v1 definition; mappings name
  their target field by ``field`` (the field's name), so no client has to know internal ids.
* ``expected.json`` — for each sample, in order, the values that must appear in its output and
  the placeholder text that must **not** remain.

The Python SDK, the CLI, the TypeScript client, the n8n workflow and the browser check all use
this one kit, so "the same input produced the same documents" is something that can be compared
rather than asserted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CUSTOMERS = [
    ("C-100001", "合成测试客户甲", "杭州", "12800.50", "2026-01-05"),
    ("C-100002", "合成测试客户乙", "上海", "560.00", "2026-01-12"),
    ("C-100003", "合成测试客户丙", "深圳", "99999.99", "2026-02-01"),
    ("C-100004", "合成测试客户丁", "北京", "0.01", "2026-02-14"),
    ("C-100005", "合成测试客户戊", "成都", "3200.00", "2026-03-03"),
    ("C-100006", "合成测试客户己", "武汉", "47100.25", "2026-03-18"),
    ("C-100007", "合成测试客户庚", "西安", "8800.80", "2026-04-09"),
    ("C-100008", "合成测试客户辛", "南京", "15.00", "2026-04-30"),
    ("C-100009", "合成测试客户壬", "广州", "7300.00", "2026-05-21"),
    ("C-100010", "合成测试客户癸", "重庆", "610.40", "2026-06-06"),
    ("C-100011", "合成测试客户子", "苏州", "25000.00", "2026-06-28"),
    ("C-100012", "合成测试客户丑", "天津", "1.99", "2026-07-15"),
]
HEADERS = ("客户编号", "客户名称", "城市", "金额", "签约日期")
PLACEHOLDERS = {"客户名称": "示例客户名称", "客户编号": "C-000000", "城市": "示例城市",
                "金额": "0.00", "签约日期": "2020-01-01"}


def make(out: Path) -> dict:
    import docx
    import openpyxl

    out.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户"
    ws.append(list(HEADERS))
    for row in CUSTOMERS:
        ws.append(list(row))
    # Stable metadata so re-runs do not differ only by a timestamp.
    wb.properties.creator = "synwevo acceptance kit (synthetic)"
    wb.save(out / "customers.xlsx")

    d = docx.Document()
    d.core_properties.author = "synwevo acceptance kit (synthetic)"
    d.add_heading("客户确认函（合成测试模板）", level=1)
    d.add_paragraph(f"客户名称：{PLACEHOLDERS['客户名称']}")
    d.add_paragraph(f"客户编号：{PLACEHOLDERS['客户编号']}")
    d.add_paragraph(f"所在城市：{PLACEHOLDERS['城市']}")
    d.add_paragraph(f"合同金额：{PLACEHOLDERS['金额']} 元")
    d.add_paragraph(f"签约日期：{PLACEHOLDERS['签约日期']}")
    d.add_paragraph("本文件由验收套件生成，内容全部为合成测试数据。")
    d.save(out / "template.docx")

    fields = {"schema": "docweave.template-fields", "version": 1,
              "fields": [{"kind": "text", "name": name, "find": text} for name, text in PLACEHOLDERS.items()]}
    project = {
        "schema": "docweave.project", "version": 1,
        "rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}},
        "mappings": [{"field": name, "type": "single",
                      "sources": [{"kind": "column", "sheet": "客户", "header": name}]}
                     for name in PLACEHOLDERS],
        "output": {"naming": "{客户编号}_{客户名称}"},
    }
    expected = {
        "samples": len(CUSTOMERS),
        "naming": "{客户编号}_{客户名称}",
        "forbidden_text": sorted(set(PLACEHOLDERS.values()) - {"0.00"}),
        "items": [{"file_base": f"{c[0]}_{c[1]}", "must_contain": list(c)} for c in CUSTOMERS],
    }
    for name, obj in (("fields.json", fields), ("project.json", project), ("expected.json", expected)):
        (out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return expected


def check_docx(data: bytes, item: dict, forbidden: list[str]) -> list[str]:
    """Problems with one generated DOCX against its expected item; empty list means it passes."""
    import io

    import docx
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    problems = [f"缺少「{v}」" for v in item["must_contain"] if v not in text]
    problems += [f"仍有模板占位文字「{v}」" for v in forbidden if v in text]
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    exp = make(Path(sys.argv[1]))
    print(json.dumps({"out": sys.argv[1], "samples": exp["samples"]}, ensure_ascii=False))
