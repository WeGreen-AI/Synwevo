#!/usr/bin/env python3
"""Seed an instance with three worked examples, then verify the artifacts they produce.

A fresh instance opens on an empty project list, which shows nothing about what the product
does.  This builds three samples that together cover the three sample modes and all three
template families, runs each one, and checks the delivered bytes:

    示例一  客户合同      per_row    DOCX   text fields + date/money transforms
    示例二  订单对账单     per_group  XLSX   a cell field + a repeat detail row
    示例三  发货单        per_row    PNG    image template, same pixels out

Everything is SYNTHETIC — invented companies, invented numbers, no personal data, and both
the templates and the generated documents say so on their face.  Projects are created over
the machine API, so each carries ``origin = api:<key id>`` and
``manage.py projects prune`` can find them later without touching anything a person made.

    export DOCWEAVE_API_KEY=dw_xxx_yyy
    python scripts/seed_samples.py --base-url http://127.0.0.1:8765

Exit codes: 0 every sample generated and verified, 3 a job did not complete, 4 an artifact
failed its checksum or its content check, 1 other failures.  Only the standard library is
used for transport; the sample files need openpyxl / python-docx / Pillow, which the server
image already has (run this inside the container for a containerised deployment).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

TERMINAL = ("completed", "completed_with_errors", "failed", "cancelled")


class Api:
    def __init__(self, base_url: str, key: str, timeout: float = 300.0):
        self.base, self.key, self.timeout = base_url.rstrip("/"), key, timeout

    def __call__(self, path, method="GET", body=None, files=None, headers=None, raw=False):
        h = {"Authorization": f"Bearer {self.key}", **(headers or {})}
        data = None
        if files:
            b = uuid.uuid4().hex
            parts = []
            for field, (fn, content) in files.items():
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{field}"; '
                             f'filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                             + content + b"\r\n")
            data = b"".join(parts) + f"--{b}--\r\n".encode()
            h["Content-Type"] = "multipart/form-data; boundary=" + b
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"HTTP {e.code} {method} {path}\n{e.read().decode(errors='replace')[:800]}") from None
        return payload if raw else (json.loads(payload) if payload else {})

    def wait_file(self, fid: str) -> dict:
        for _ in range(400):
            f = self(f"/api/v1/files/{fid}")
            if f["status"] != "parsing":
                if f["status"] != "ready":
                    raise SystemExit(f"来源文件解析失败：{f}")
                return f
            time.sleep(0.3)
        raise SystemExit("来源文件解析超时")

    def wait_job(self, jid: str, timeout: float = 900) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = self(f"/api/v1/jobs/{jid}")
            if j["status"] in TERMINAL:
                return j
            time.sleep(1)
        raise SystemExit("任务超时")


# ------------------------------------------------------------------ sample inputs
def cjk_font(size):
    """A CJK-capable TrueType face.  The Linux paths are what the server image has
    (fonts-noto-cjk); the macOS ones only matter when rehearsing on a developer machine."""
    from PIL import ImageFont
    for pat in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK*.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK*.ttc",
                "/usr/share/fonts/**/*CJK*.tt[cf]",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/Supplemental/Songti.ttc"):
        for p in sorted(glob.glob(pat, recursive=True)):
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001 — try the next candidate
                continue
    raise SystemExit("找不到 CJK 字体：图片样本需要一个支持中文的 TrueType 字体（容器内为 fonts-noto-cjk）")


def _book(sheet: str, header: list, rows: list, widths: dict | None = None) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(header)
    for r in rows:
        ws.append(list(r))
    for col, w in (widths or {}).items():
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def customers_xlsx() -> bytes:
    return _book("客户", ["客户编号", "客户名称", "签约日期", "合同金额", "联系人"],
                 [("HT-2026-001", "示例科技（上海）有限公司", "2026-01-08", 128600.00, "示例联系人甲"),
                  ("HT-2026-002", "示例贸易（深圳）有限公司", "2026-01-15", 86400.50, "示例联系人乙"),
                  ("HT-2026-003", "示例制造集团股份有限公司", "2026-02-03", 254000.00, "示例联系人丙"),
                  ("HT-2026-004", "示例物流服务有限公司", "2026-02-19", 39800.00, "示例联系人丁"),
                  ("HT-2026-005", "示例医疗器械有限公司", "2026-03-06", 176500.25, "示例联系人戊"),
                  ("HT-2026-006", "示例建筑工程有限公司", "2026-03-22", 512300.00, "示例联系人己")],
                 {"B": 32})


def contract_docx() -> bytes:
    import docx
    from docx.shared import Pt
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading("服务合同（示例模板）", 0)
    for line in ("合同编号：HT-0000-000", "甲方（客户）：示例客户名称", "签约日期：2020-01-01",
                 "合同金额：人民币 0.00 元", "联系人：某某某某", "",
                 "一、服务内容：乙方按本合同约定为甲方提供示例服务。",
                 "二、付款方式：甲方应于本合同生效后十个工作日内支付上述金额。",
                 "三、本合同一式两份，甲乙双方各执一份，自双方签字之日起生效。", "",
                 "甲方签字：______________        乙方签字：______________", "",
                 "（本模板与其填充数据均为演示用合成内容，不对应任何真实主体。）"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def orders_xlsx() -> bytes:
    return _book("明细", ["订单号", "行号", "品名", "数量", "单价"],
                 [("DD-20260101", 1, "示例打印纸 A4", 20, 28.50),
                  ("DD-20260101", 2, "示例签字笔（黑）", 50, 3.20),
                  ("DD-20260101", 3, "示例文件夹", 15, 12.00),
                  ("DD-20260102", 1, "示例墨盒 BK", 6, 189.00),
                  ("DD-20260102", 2, "示例复印纸 B5", 10, 24.80),
                  ("DD-20260103", 1, "示例白板笔", 30, 5.50),
                  ("DD-20260103", 2, "示例便利贴", 40, 6.80),
                  ("DD-20260103", 3, "示例订书机", 8, 35.00),
                  ("DD-20260103", 4, "示例回形针（盒装）", 25, 4.20)])


def order_template_xlsx() -> bytes:
    """Row 5 (0-based 4) is the detail row the repeat field grows from."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "订单"
    ws["A1"] = "订单对账单（示例模板）"
    ws["A2"], ws["B2"] = "订单号：", "DD-000000"
    ws["A4"], ws["B4"], ws["C4"], ws["D4"] = "行号", "品名", "数量", "单价"
    ws["A5"], ws["B5"], ws["C5"], ws["D5"] = 0, "示例品名", 0, 0.0
    ws["A7"] = "（本模板与其填充数据均为演示用合成内容。）"
    ws.column_dimensions["B"].width = 28
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


IMG_W, IMG_H = 1000, 620
BOX = {"收货方": (250, 150, 900, 200), "订单号": (250, 216, 720, 266), "发货日期": (250, 282, 720, 332)}


def shipping_png() -> bytes:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (IMG_W, IMG_H), (252, 251, 247))
    d = ImageDraw.Draw(im)
    big, mid, small = cjk_font(34), cjk_font(26), cjk_font(18)
    d.rectangle([28, 28, IMG_W - 28, IMG_H - 28], outline=(120, 130, 145), width=2)
    d.text((64, 56), "发 货 单（示例模板）", font=big, fill=(28, 48, 104))
    d.line([64, 112, IMG_W - 64, 112], fill=(150, 160, 175), width=1)
    for label, y in (("收货方：", 158), ("订单号：", 224), ("发货日期：", 290)):
        d.text((64, y), label, font=mid, fill=(96, 102, 112))
    # the placeholders sit inside the declared boxes; the engine samples their ink colour and height
    for text, y in (("示例收货方名称", 158), ("SO-0000-0000", 224), ("0000-00-00", 290)):
        d.text((256, y), text, font=mid, fill=(28, 48, 104))
    d.line([64, 380, IMG_W - 64, 380], fill=(150, 160, 175), width=1)
    d.text((64, 404), "签收：______________      日期：______________", font=mid, fill=(96, 102, 112))
    d.text((64, IMG_H - 78), "本模板与其填充数据均为演示用合成内容，不对应任何真实主体。", font=small, fill=(150, 154, 162))
    buf = io.BytesIO()
    im.save(buf, "PNG", dpi=(150, 150))
    return buf.getvalue()


def consignees_xlsx() -> bytes:
    return _book("发货", ["收货方", "订单号", "发货日期"],
                 [("示例华东仓储有限公司", "SO-2026-0117", "2026-01-17"),
                  ("示例华南贸易有限公司", "SO-2026-0118", "2026-01-18"),
                  ("示例华北物流有限公司", "SO-2026-0119", "2026-01-19"),
                  ("示例西部供应链有限公司", "SO-2026-0120", "2026-01-20")],
                 {"A": 28})


# ------------------------------------------------------------------ builders
def single(sheet, header, binding, transforms=None):
    m = {"type": "single", "sources": [{"kind": "column", "sheet": sheet, "header": header}],
         "target": {"binding": binding}}
    if transforms:
        m["transforms"] = transforms
    return m


def fields_of(api: Api, tid: str) -> dict:
    return {f["name"]: f for f in api(f"/api/v1/templates/{tid}")["fields"]}


def sample_contracts(api: Api, state: dict):
    pid = api("/api/v1/projects", "POST", {"name": "示例一 · 客户合同（每行一份 · DOCX）"})["id"]
    api.wait_file(api(f"/api/v1/projects/{pid}/files", "POST",
                      files={"file": ("示例_客户名单.xlsx", customers_xlsx())})["id"])
    tid = api("/api/v1/templates", "POST", files={"file": ("示例_服务合同模板.docx", contract_docx())})["id"]
    api(f"/api/v1/templates/{tid}/fields", "POST", {
        "schema": "docweave.template-fields", "version": 1,
        "fields": [{"kind": "text", "name": "合同编号", "find": "HT-0000-000"},
                   {"kind": "text", "name": "客户名称", "find": "示例客户名称"},
                   {"kind": "text", "name": "签约日期", "find": "2020-01-01"},
                   {"kind": "text", "name": "合同金额", "find": "0.00"},
                   {"kind": "text", "name": "联系人", "find": "某某某某"}]})
    api(f"/api/v1/projects/{pid}/template", "PUT", {"template_id": tid})
    api(f"/api/v1/projects/{pid}/rule", "PUT",
        {"rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}}})
    f = fields_of(api, tid)
    # two fields carry transforms, so the sample also demonstrates date and money formatting
    api(f"/api/v1/projects/{pid}/mappings", "PUT", {"mappings": [
        single("客户", col, f[fld]["binding"], tr) for fld, col, tr in
        (("合同编号", "客户编号", None), ("客户名称", "客户名称", None),
         ("签约日期", "签约日期", [{"op": "date_format", "pattern": "yyyy年m月d日"}]),
         ("合同金额", "合同金额", [{"op": "decimals", "n": 2, "thousands": True}]),
         ("联系人", "联系人", None))]})
    job = api(f"/api/v1/projects/{pid}/jobs", "POST",
              {"formats": ["docx"], "naming": "{客户编号}_{客户名称}", "error_policy": "block"},
              headers={"Idempotency-Key": "seed-contracts-1"})
    return pid, api.wait_job(job["id"])


def sample_orders(api: Api, state: dict):
    pid = api("/api/v1/projects", "POST", {"name": "示例二 · 订单对账单（每组一份 + 重复明细 · XLSX）"})["id"]
    api.wait_file(api(f"/api/v1/projects/{pid}/files", "POST",
                      files={"file": ("示例_订单明细.xlsx", orders_xlsx())})["id"])
    tid = api("/api/v1/templates", "POST", files={"file": ("示例_订单对账单模板.xlsx", order_template_xlsx())})["id"]
    api(f"/api/v1/templates/{tid}/fields", "POST", {
        "schema": "docweave.template-fields", "version": 1,
        "fields": [{"kind": "cell", "name": "订单号", "sheet": "订单", "cell": "B2"},
                   {"kind": "repeat", "name": "明细", "sheet": "订单", "row": 4, "c1": 0, "c2": 3}]})
    api(f"/api/v1/projects/{pid}/template", "PUT", {"template_id": tid})
    api(f"/api/v1/projects/{pid}/rule", "PUT", {"rule": {
        "mode": "per_group", "table": {"sheet": "明细", "header_row": 0},
        "group": {"keys": [{"header": "订单号"}], "sort": [{"header": "行号", "dir": "asc", "numeric": True}]}}})
    f = fields_of(api, tid)
    api(f"/api/v1/projects/{pid}/mappings", "PUT", {"mappings": [
        single("明细", "订单号", f["订单号"]["binding"]),
        {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}],
         "target": {"binding": f["明细"]["binding"]},
         "columns": [{"slot_col": f"c{i}", "source": {"header": h}}
                     for i, h in enumerate(["行号", "品名", "数量", "单价"])]}]})
    job = api(f"/api/v1/projects/{pid}/jobs", "POST",
              {"formats": ["xlsx"], "naming": "{订单号}", "error_policy": "block"},
              headers={"Idempotency-Key": "seed-orders-1"})
    return pid, api.wait_job(job["id"])


def sample_image(api: Api, state: dict):
    pid = api("/api/v1/projects", "POST", {"name": "示例三 · 发货单（图片模板 · PNG 原样输出）"})["id"]
    api.wait_file(api(f"/api/v1/projects/{pid}/files", "POST",
                      files={"file": ("示例_发货名单.xlsx", consignees_xlsx())})["id"])
    state["template_png"] = shipping_png()                      # kept for the pixel check below
    tid = api("/api/v1/templates", "POST", files={"file": ("示例_发货单模板.png", state["template_png"])})["id"]
    api(f"/api/v1/templates/{tid}/fields", "POST", {
        "schema": "docweave.template-fields", "version": 1,
        "fields": [{"kind": "pdf_box", "name": n, "page": 0, "rect": list(BOX[n])}
                   for n in ("收货方", "订单号", "发货日期")]})
    api(f"/api/v1/projects/{pid}/template", "PUT", {"template_id": tid})
    api(f"/api/v1/projects/{pid}/rule", "PUT",
        {"rule": {"mode": "per_row", "table": {"sheet": "发货", "header_row": 0}}})
    f = fields_of(api, tid)
    api(f"/api/v1/projects/{pid}/mappings", "PUT",
        {"mappings": [single("发货", n, f[n]["binding"]) for n in ("收货方", "订单号", "发货日期")]})
    job = api(f"/api/v1/projects/{pid}/jobs", "POST",
              {"formats": ["png"], "naming": "{订单号}", "error_policy": "block"},
              headers={"Idempotency-Key": "seed-image-1"})
    return pid, api.wait_job(job["id"])


# ------------------------------------------------------------------ artifact checks
def check_docx(blob: bytes, must: list, state: dict):
    import docx
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(blob)).paragraphs)
    return text, [m for m in must if m not in text]


def check_xlsx(blob: bytes, must: list, state: dict):
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(blob))["订单"]
    text = "\n".join(" ".join(str(c) for c in row if c is not None)
                     for row in ws.iter_rows(values_only=True))
    return text, [m for m in must if m not in text]


def check_png(blob: bytes, must: list, state: dict):
    """Same canvas, and the change is confined to the declared boxes.

    A job status of "completed" only says the pipeline ran.  This compares the output
    against the template pixel for pixel: every declared box must actually differ (the
    value was written) and everything outside them must be unchanged (nothing else was
    disturbed).  2px of slack around each box absorbs the rasteriser's antialiasing
    against the clip boundary; the rest of the canvas — title, rules, labels, footer —
    must be identical."""
    from PIL import Image, ImageChops
    out = Image.open(io.BytesIO(blob)).convert("RGB")
    tpl = Image.open(io.BytesIO(state["template_png"])).convert("RGB")
    if out.size != tpl.size:
        return f"尺寸改变：{out.size} != {tpl.size}", ["图片尺寸未保持原样"]
    diff = ImageChops.difference(out, tpl).convert("L")
    problems = [f"{n} 区域未写入" for n, box in BOX.items() if not diff.crop(box).getbbox()]
    outside = diff.copy()
    for x0, y0, x1, y1 in BOX.values():
        outside.paste(0, (x0 - 2, y0 - 2, x1 + 2, y1 + 2))
    if outside.getbbox():
        problems.append(f"区域之外的像素被改动 {outside.getbbox()}")
    return (f"{out.size[0]}×{out.size[1]} {out.mode}，三个区域均已写入，"
            f"区域外（留 2px 抗锯齿余量）像素未改动"), problems


SAMPLES = [
    ("示例一 客户合同", sample_contracts, check_docx,
     ["HT-2026-001", "示例科技（上海）有限公司", "2026年1月8日", "128,600.00", "示例联系人甲"]),
    ("示例二 订单对账单", sample_orders, check_xlsx,
     ["DD-20260101", "示例打印纸 A4", "示例签字笔（黑）", "示例文件夹", "28.5"]),
    ("示例三 发货单", sample_image, check_png, []),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("DOCWEAVE_BASE_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--api-key", default=os.environ.get("DOCWEAVE_API_KEY"),
                    help="默认取 $DOCWEAVE_API_KEY；用完请 manage.py apikey revoke 撤销")
    ap.add_argument("--only", action="append", choices=["contracts", "orders", "image"],
                    help="只建某个样本（可重复）")
    args = ap.parse_args()
    if not args.api_key:
        print("需要机器接口密钥：设置 $DOCWEAVE_API_KEY 或传 --api-key\n"
              "  python backend/manage.py apikey create --name seed-samples --scopes read,write", file=sys.stderr)
        return 1

    api = Api(args.base_url, args.api_key)
    wanted = {"contracts": 0, "orders": 1, "image": 2}
    chosen = SAMPLES if not args.only else [SAMPLES[wanted[k]] for k in args.only]
    state: dict = {}
    failures = 0

    for name, build, check, must in chosen:
        pid, job = build(api, state)
        arts = api(f"/api/v1/jobs/{job['id']}/artifacts")
        first = arts["items"][0] if arts["items"] else None
        print(f"{name}  project={pid}  {job['status']}  "
              f"samples={job['counts']['samples']} files={job['counts']['files']} "
              f"failed={job['counts']['failed']}  artifacts={arts['total']}  "
              f"first={first['filename'] if first else '-'}")
        if job["status"] != "completed" or not first:
            failures += 1
            for it in api(f"/api/v1/jobs/{job['id']}/items")["items"][:5]:
                print("     ", it.get("status"), str(it.get("issues"))[:400])
            continue
        blob = api(f"/api/v1/artifacts/{first['id']}/content", raw=True)
        if hashlib.sha256(blob).hexdigest() != first["sha256"]:
            print("      下载内容与登记的 sha256 不一致")
            failures += 1
            continue
        text, missing = check(blob, must, state)
        print(f"      {text.replace(chr(10), ' / ')[:160]}")
        print(f"      {'字段值已核对' if not missing else '未通过：' + '；'.join(missing)}")
        failures += bool(missing)

    if failures:
        print(f"\n{failures} 个样本未通过校验")
        return 4
    print(f"\n{len(chosen)} 个样本均已生成并核对产物内容")
    return 0


if __name__ == "__main__":
    sys.exit(main())
