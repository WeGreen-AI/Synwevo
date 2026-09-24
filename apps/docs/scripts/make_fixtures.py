"""Generate TEST-ONLY fixture files for acceptance tests and manual trials.

All names, numbers and companies are synthetic ("测试"/"TEST" prefixed) and do
not represent real people or data.

    .venv/bin/python apps/docs/scripts/make_fixtures.py [outdir]     (default: <repo>/testdata/)

The default is the repository root's testdata/, because that is where every consumer reads them
(apps/docs/tests/conftest.py, benchmark.py, the browser E2E scripts). Defaulting to this module's
own directory produced a second, unused copy while the stale one stayed in use.
"""
from __future__ import annotations

import datetime as dt
import io
import random
import sys
from pathlib import Path

import docx
import openpyxl
import pymupdf
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from PIL import Image, ImageDraw, ImageFont

from synwevo_core.http import repo_root  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (repo_root() or Path(__file__).resolve().parents[3]) / "testdata"
random.seed(20260922)
SURN = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
GIVEN = "伟芳娜敏静丽强磊军洋勇艳杰娟涛明超秀霞平刚桂英华"


def name(i: int) -> str:
    return f"测试{SURN[i % len(SURN)]}{GIVEN[(i * 7) % len(GIVEN)]}{GIVEN[(i * 3) % len(GIVEN)]}"


def cjk_font(size: int):
    for p in ["/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def customers(n=100):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户"
    ws.append(["客户编号", "姓名", "签约日期", "金额", "备注"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i in range(1, n + 1):
        ws.append([f"{i:06d}", name(i), dt.date(2025, 1, 1) + dt.timedelta(days=i * 3), round(1000 + i * 123.45, 2), "（测试数据）" if i % 10 == 0 else None])
        ws.cell(i + 1, 3).number_format = "yyyy-mm-dd"
        ws.cell(i + 1, 4).number_format = "#,##0.00"
    ws.column_dimensions["B"].width = 16
    wb.save(OUT / "测试_客户100.xlsx")
    # same data with reordered columns (column order change test)
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.title = "客户"
    ws2.append(["金额", "姓名", "备注", "客户编号", "签约日期"])
    for i in range(1, 6):
        ws2.append([round(1000 + i * 123.45, 2), name(i), None, f"{i:06d}", dt.date(2025, 1, 1) + dt.timedelta(days=i * 3)])
        ws2.cell(i + 1, 5).number_format = "yyyy-mm-dd"
    wb2.save(OUT / "测试_客户_列顺序变化.xlsx")
    wb3 = openpyxl.Workbook()
    ws3 = wb3.active
    ws3.title = "客户"
    ws3.append(["客户编号", "姓名", "姓名", "金额"])
    ws3.append(["000001", "测试甲", "测试乙", 1])
    wb3.save(OUT / "测试_客户_重名列.xlsx")


def contract_template():
    d = docx.Document()
    sec = d.sections[0]
    sec.header.paragraphs[0].text = "测试公司 · 合同编号：HT-000000"
    sec.footer.paragraphs[0].text = "本文件为测试模板，仅用于软件验收"
    h = d.add_heading("测试服务合同", 1)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = d.add_paragraph()
    p.add_run("甲方（客户）：").bold = True
    r = p.add_run("示例")
    r.font.color.rgb = RGBColor(0xC0, 0, 0)
    r = p.add_run("客户名称")
    r.italic = True
    p.add_run("，客户编号 000000。")
    p2 = d.add_paragraph("签约日期：2020-01-01；合同金额：人民币 0.00 元。")
    d.add_paragraph("乙方：测试服务有限公司（固定文字，不应被修改）。")
    t = d.add_table(rows=2, cols=2)
    t.style = d.styles["Table Grid"]
    t.cell(0, 0).text = "项目"
    t.cell(0, 1).text = "说明"
    t.cell(1, 0).text = "服务内容"
    t.cell(1, 1).text = "按合同约定提供测试服务。"
    img = Image.new("RGB", (160, 60), (37, 99, 235))
    ImageDraw.Draw(img).text((10, 20), "TEST LOGO", fill=(255, 255, 255))
    b = io.BytesIO()
    img.save(b, "PNG")
    d.add_paragraph().add_run().add_picture(io.BytesIO(b.getvalue()))
    d.add_paragraph("甲方签字：______________    乙方签字：______________")
    d.save(OUT / "测试_合同模板.docx")


def inspection_forms(n=20):
    folder = OUT / "检测表20"
    folder.mkdir(exist_ok=True)
    for i in range(1, n + 1):
        d = docx.Document()
        d.add_heading("产品检测表（测试）", 1)
        d.add_paragraph(f"样品编号：YP-{i:04d}")
        d.add_paragraph(f"送检单位：测试单位{chr(64 + (i % 26) + 1)}有限公司")
        d.add_paragraph(f"检测日期：2026-0{(i % 9) + 1}-{(i % 27) + 1:02d}")
        t = d.add_table(rows=1, cols=3)
        t.style = d.styles["Table Grid"]
        for c, h in enumerate(["检测项目", "结果", "判定"]):
            t.cell(0, c).text = h
        for k in range(1 + i % 4):
            row = t.add_row().cells
            row[0].text = f"项目{k + 1}"
            row[1].text = f"{random.randint(10, 99)}.{random.randint(0, 9)}"
            row[2].text = "合格" if (i + k) % 5 else "不合格"
        d.add_paragraph(f"结论：{'合格' if i % 5 else '需复检'}")
        d.save(folder / f"测试_检测表_{i:02d}.docx")
    # report template
    d = docx.Document()
    d.add_heading("检测报告（测试模板）", 1)
    d.add_paragraph("报告对象：样品 XXXX，送检单位 某某单位。")
    d.add_paragraph("检测日期：YYYY-MM-DD")
    t = d.add_table(rows=2, cols=3)
    t.style = d.styles["Table Grid"]
    for c, h in enumerate(["项目", "测得值", "判定"]):
        t.cell(0, c).text = h
    for c, h in enumerate(["—", "—", "—"]):
        t.cell(1, c).text = h
    d.add_paragraph("综合结论：待定")
    d.save(OUT / "测试_检测报告模板.docx")


def orders(n=100, groups=15):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    ws.append(["订单号", "客户", "行号", "品名", "数量", "单价"])
    rows = []
    for i in range(n):
        g = i % groups
        rows.append([f"DD-{g + 1:03d}", f"测试客户{g + 1}", None, f"测试商品{(i * 7) % 23 + 1}", (i % 5) + 1, round(9.9 + (i % 7) * 3.5, 2)])
    random.shuffle(rows)
    counters = {}
    for r in rows:
        counters[r[0]] = counters.get(r[0], 0) + 1
        r[2] = counters[r[0]]
    # rows appear shuffled; the group sort rule (by 行号) must restore order
    rows.sort(key=lambda r: (random.random()))
    for r in rows:
        ws.append(r)
    wb.save(OUT / "测试_订单明细100.xlsx")
    # order template: DOCX with repeat row + total
    d = docx.Document()
    d.add_heading("订单确认单（测试）", 1)
    d.add_paragraph("订单号：DD-000    客户：某客户")
    t = d.add_table(rows=2, cols=4)
    t.style = d.styles["Table Grid"]
    for c, h in enumerate(["行号", "品名", "数量", "单价"]):
        t.cell(0, c).text = h
    for c, h in enumerate(["1", "示例商品", "1", "0.00"]):
        t.cell(1, c).text = h
    d.add_paragraph("（明细结束）")
    d.save(OUT / "测试_订单模板.docx")
    # XLSX order template with total formula
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "订单"
    ws["A1"] = "订单确认单（测试）"
    ws.merge_cells("A1:E1")
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")
    ws["A2"], ws["B2"], ws["C2"], ws["D2"] = "订单号", "DD-000", "客户", "某客户"
    thin = Side(style="thin")
    for c, h in enumerate(["行号", "品名", "数量", "单价", "小计"], start=1):
        cell = ws.cell(4, c, h)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDEBF7")
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c in range(1, 6):
        ws.cell(5, c).border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ws["E5"] = "=C5*D5"
    ws["D5"].number_format = "0.00"
    ws["E5"].number_format = "0.00"
    ws["D6"] = "合计"
    ws["E6"] = "=SUM(E5:E5)"
    ws["A8"] = "备注：测试模板"
    wb.save(OUT / "测试_订单模板.xlsx")


def region_files():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws["A1"] = "季度数据（测试）"
    for r in range(3):
        for c in range(4):
            ws.cell(r + 3, c + 2, f"R{r + 1}C{c + 1}")
    wb.save(OUT / "测试_区域来源3x4.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报表"
    ws["A1"] = "报表（测试）"
    fill = PatternFill("solid", fgColor="FFF2CC")
    for r in range(3):
        for c in range(4):
            ws.cell(r + 5, c + 3).fill = fill
            ws.cell(r + 5, c + 3).font = Font(color="1F4E79", bold=True)
    ws["A10"] = "下方内容不应被覆盖"
    wb.save(OUT / "测试_区域模板.xlsx")


def pdfs():
    font = pymupdf.Font("cjk")
    # text source PDF
    doc = pymupdf.open()
    for i in range(1, 4):
        p = doc.new_page(width=595, height=842)
        p.insert_font(fontname="f", fontbuffer=font.buffer)
        p.insert_text((72, 90), "测试申请表", fontname="f", fontsize=18)
        p.insert_text((72, 140), "申请人：", fontname="f", fontsize=12)
        p.insert_text((140, 140), name(i), fontname="f", fontsize=12)
        p.insert_text((72, 170), "证件号：", fontname="f", fontsize=12)
        p.insert_text((140, 170), f"TEST{i:08d}", fontname="f", fontsize=12)
        p.insert_text((72, 200), "金额：", fontname="f", fontsize=12)
        p.insert_text((140, 200), f"{i * 1000}.00", fontname="f", fontsize=12)
    doc.subset_fonts()
    doc.save(OUT / "测试_申请表.pdf", garbage=3, deflate=True)
    # template PDF with sample values that must be truly removed, and a rotated page
    doc = pymupdf.open()
    for rot in (0, 90):
        p = doc.new_page(width=595, height=842)
        p.insert_font(fontname="f", fontbuffer=font.buffer)
        p.insert_text((72, 90), "测试通知书", fontname="f", fontsize=18)
        p.insert_text((72, 140), "收件人：", fontname="f", fontsize=12)
        p.insert_text((140, 140), "旧姓名样例", fontname="f", fontsize=12)
        p.draw_rect(pymupdf.Rect(135, 125, 360, 146), color=(0.2, 0.2, 0.8), width=0.8)
        p.insert_text((72, 170), "编号：", fontname="f", fontsize=12)
        p.insert_text((140, 170), "OLD-000001", fontname="f", fontsize=12)
        p.draw_rect(pymupdf.Rect(135, 155, 360, 176), color=(0.2, 0.2, 0.8), width=0.8)
        p.set_rotation(rot)
    doc.subset_fonts()
    doc.save(OUT / "测试_通知书模板.pdf", garbage=3, deflate=True)
    detail_pdf_templates(font)
    # AcroForm template
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_font(fontname="f", fontbuffer=font.buffer)
    p.insert_text((72, 100), "测试表单：姓名", fontname="f", fontsize=12)
    w = pymupdf.Widget()
    w.field_name = "applicant"
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.rect = pymupdf.Rect(180, 86, 400, 106)
    w.text_fontsize = 11
    p.add_widget(w)
    doc.subset_fonts()
    doc.save(OUT / "测试_表单模板.pdf", garbage=3, deflate=True)


def _shipping_page(doc, font, grid: bool, rotate: int):
    """TEST-ONLY shipping note: two single fields and a 6-row detail table with a sample row."""
    p = doc.new_page(width=595, height=842)
    p.insert_font(fontname="f", fontbuffer=font.buffer)
    p.insert_text((72, 80), "测试发货单", fontname="f", fontsize=18)
    p.insert_text((72, 120), "单号：", fontname="f", fontsize=11)
    p.insert_text((115, 120), "DD-示例", fontname="f", fontsize=11)
    p.insert_text((300, 120), "客户：", fontname="f", fontsize=11)
    p.insert_text((343, 120), "示例客户", fontname="f", fontsize=11)
    xs = [72, 112, 292, 362, 442, 523]
    heads = ["序号", "品名", "数量", "单价", "金额"]
    top, rh, n = 150, 22, 6
    for i, h in enumerate(heads):
        p.insert_text((xs[i] + 4, top + 15), h, fontname="f", fontsize=10)
    sample = ["1", "示例商品", "10", "2.50", "25.00"]
    for i, t in enumerate(sample):
        w = font.text_length(t, 10)
        x = xs[i] + 4 if i < 2 else xs[i + 1] - 4 - w
        p.insert_text((x, top + rh + 15), t, fontname="f", fontsize=10)
    if grid:
        for k in range(n + 2):
            p.draw_line((xs[0], top + k * rh), (xs[-1], top + k * rh), color=(0, 0, 0), width=0.6)
        for x in xs:
            p.draw_line((x, top), (x, top + (n + 1) * rh), color=(0, 0, 0), width=0.6)
    else:
        p.draw_line((xs[0], top + rh), (xs[-1], top + rh), color=(0, 0, 0), width=0.8)
    p.insert_text((380, top + (n + 1) * rh + 24), "合计：", fontname="f", fontsize=11)
    p.insert_text((72, 780), "本单据为测试数据", fontname="f", fontsize=9)
    p.set_rotation(rotate)


def detail_pdf_templates(font):
    doc = pymupdf.open()
    _shipping_page(doc, font, grid=True, rotate=0)
    doc.subset_fonts()
    doc.save(OUT / "测试_发货单模板.pdf", garbage=3, deflate=True)
    doc = pymupdf.open()
    _shipping_page(doc, font, grid=False, rotate=0)
    _shipping_page(doc, font, grid=True, rotate=90)
    doc.subset_fonts()
    doc.save(OUT / "测试_发货单模板_无框线与旋转.pdf", garbage=3, deflate=True)


def paragraph_block_template():
    """TEST-ONLY DOCX where each detail item is a group of paragraphs (not a table row)."""
    d = docx.Document()
    d.add_heading("订单明细（段落式，测试）", 1)
    d.add_paragraph("订单号：DD-000")
    p = d.add_paragraph()
    r = p.add_run("行号：1")
    r.bold = True
    d.add_paragraph("品名：示例商品\u3000数量：1")
    d.add_paragraph("单价（元）：0.00")
    d.add_paragraph("（明细结束）")
    t = d.add_table(rows=1, cols=1)
    t.cell(0, 0).text = "单元格内的段落"
    d.save(OUT / "测试_订单段落模板.docx")


MULTI_FORMS = [  # TEST-ONLY people for the multi-form files (block 3 has no phone, block 2 has an extra remark)
    {"姓名": "测试赵一", "电话": "13900000001", "地址": "测试市一路 1 号"},
    {"姓名": "测试钱二", "电话": "13900000002", "地址": "测试市二路 2 号", "备注": "（测试备注）"},
    {"姓名": "测试孙三", "地址": "测试市三路 3 号"},
    {"姓名": "测试李四", "电话": "13900000004", "地址": "测试市四路 4 号"},
]


def multi_form_files():
    """Several irregular forms in ONE file, in four formats (for the per-block sample mode)."""
    # XLSX: title row, then label/value rows; blocks differ in height
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "登记"
    for f in MULTI_FORMS:
        ws.append(["人员登记表（测试）"])
        ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=4)
        ws.append(["姓名", f["姓名"], "编号", f"T{MULTI_FORMS.index(f) + 1:03d}"])
        if "备注" in f:
            ws.append(["备注", f["备注"]])
        if "电话" in f:
            ws.append(["电话", f["电话"]])
        ws.append(["地址", f["地址"]])
        ws.append([])
    wb.save(OUT / "测试_多表单登记表.xlsx")
    # DOCX
    d = docx.Document()
    for f in MULTI_FORMS:
        d.add_heading("人员登记表（测试）", 2)
        d.add_paragraph(f"姓名：{f['姓名']}")
        if "备注" in f:
            d.add_paragraph(f"备注：{f['备注']}")
        if "电话" in f:
            d.add_paragraph(f"电话：{f['电话']}")
        d.add_paragraph(f"地址：{f['地址']}")
    d.save(OUT / "测试_多表单登记表.docx")
    # PDF: two forms on page 1, two on page 2 (in-page and page-level splits)
    font = pymupdf.Font("cjk")
    doc = pymupdf.open()
    for page_forms in (MULTI_FORMS[:2], MULTI_FORMS[2:]):
        p = doc.new_page(width=595, height=842)
        p.insert_font(fontname="f", fontbuffer=font.buffer)
        y = 90
        for f in page_forms:
            p.insert_text((72, y), "人员登记表（测试）", fontname="f", fontsize=14)
            y += 34
            for k in ("姓名", "备注", "电话", "地址"):
                if k in f:
                    p.insert_text((72, y), f"{k}：", fontname="f", fontsize=11)
                    p.insert_text((130, y), f[k], fontname="f", fontsize=11)
                    y += 24
            p.draw_line((72, y), (520, y), color=(0.6, 0.6, 0.6), width=0.5)
            y += 60
    doc.subset_fonts()
    doc.save(OUT / "测试_多表单登记表.pdf", garbage=3, deflate=True)
    # TXT
    lines = []
    for f in MULTI_FORMS:
        lines.append("人员登记表（测试）")
        lines += [f"{k}：{f[k]}" for k in ("姓名", "备注", "电话", "地址") if k in f]
        lines.append("")
    (OUT / "测试_多表单登记表.txt").write_text("\n".join(lines), encoding="utf-8")
    # template: one registration card
    t = docx.Document()
    t.add_heading("登记卡（测试模板）", 1)
    t.add_paragraph("姓名：某某")
    t.add_paragraph("电话：00000000")
    t.add_paragraph("地址：某地")
    t.save(OUT / "测试_登记卡模板.docx")


def image_templates():
    """TEST-ONLY image templates: a shipping note drawn in pixels on a gradient background
    (2 px per PDF point of the PDF fixture), saved as PNG (with 150 dpi) and JPEG."""
    buf = pymupdf.Font("cjk").buffer
    f = lambda size: ImageFont.truetype(io.BytesIO(buf), size)  # noqa: E731
    W, H = 1190, 900
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        t = y / (H - 1)
        c = (round(252 - 16 * t), round(248 - 4 * t), round(238 + 14 * t))
        for x in range(W):
            px[x, y] = c
    d = ImageDraw.Draw(im)
    ink, black = (31, 58, 147), (20, 20, 20)
    d.text((144, 130), "测试发货单（图片）", font=f(36), fill=black)
    d.text((144, 222), "单号：", font=f(22), fill=black)
    d.text((230, 222), "DD-示例", font=f(22), fill=ink)
    xs = [144, 224, 584, 724, 884, 1046]
    top, rh, n = 300, 44, 6
    for i, h in enumerate(["序号", "品名", "数量", "单价", "金额"]):
        d.text((xs[i] + 8, top + 10), h, font=f(20), fill=black)
    for i, t in enumerate(["1", "示例商品", "10", "2.50", "25.00"]):
        w = d.textlength(t, font=f(20))
        x = xs[i] + 8 if i < 2 else xs[i + 1] - 8 - w
        d.text((x, top + rh + 10), t, font=f(20), fill=ink)
    for k in range(n + 2):
        d.line([(xs[0], top + k * rh), (xs[-1], top + k * rh)], fill=black, width=2)
    for x in xs:
        d.line([(x, top), (x, top + (n + 1) * rh)], fill=black, width=2)
    d.text((144, 820), "本图片为测试数据", font=f(18), fill=black)
    im.save(OUT / "测试_发货单图片模板.png", dpi=(150, 150))
    im.save(OUT / "测试_发货单图片模板.jpg", quality=92, dpi=(150, 150))


def scanned():
    img = Image.new("RGB", (1240, 700), "white")
    dr = ImageDraw.Draw(img)
    f = cjk_font(40)
    dr.text((80, 60), "测试扫描单据", font=cjk_font(52), fill="black")
    dr.text((80, 200), "姓名：测试王小明", font=f, fill="black")
    dr.text((80, 290), "编号：SC-20260922", font=f, fill="black")
    dr.text((80, 380), "金额：12345.60", font=f, fill="black")
    img.save(OUT / "测试_扫描单据.png")
    # scanned PDF (image only, no text layer)
    doc = pymupdf.open()
    b = io.BytesIO()
    img.save(b, "PNG")
    p = doc.new_page(width=595, height=336)
    p.insert_image(p.rect, stream=b.getvalue())
    doc.save(OUT / "测试_扫描单据.pdf", garbage=3, deflate=True)


def pptx_template():
    from pptx import Presentation
    from pptx.util import Inches, Pt as PPt
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "测试季度汇报"
    tb = s.shapes.add_textbox(Inches(1), Inches(1.6), Inches(8), Inches(0.8))
    tb.name = "客户信息"
    tb.text_frame.text = "客户：示例客户，编号 000000"
    rows, cols = 2, 3
    gt = s.shapes.add_table(rows, cols, Inches(1), Inches(2.8), Inches(8), Inches(0.8)).table
    for c, h in enumerate(["品名", "数量", "单价"]):
        gt.cell(0, c).text = h
    for c, h in enumerate(["示例", "1", "0.00"]):
        gt.cell(1, c).text = h
    prs.save(OUT / "测试_汇报模板.pptx")


def csv_files():
    rows = ["编号,姓名,地址", '"00017",测试张三,"北京市测试区\n第二行地址"', '"00018",测试李四,"上海市, 测试路 1 号"']
    (OUT / "测试_GBK编码.csv").write_bytes(("\r\n".join(rows) + "\r\n").encode("gb18030"))
    (OUT / "测试_UTF8.csv").write_bytes(("﻿" + "\r\n".join(rows) + "\r\n").encode("utf-8"))


def broken():
    (OUT / "测试_损坏文件.xlsx").write_bytes(b"PK\x03\x04garbage-not-a-real-zip")
    (OUT / "测试_不支持.bin").write_bytes(bytes(range(256)) * 4)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "README.txt").write_text("本目录全部为自动生成的测试数据（scripts/make_fixtures.py），不包含任何真实用户数据。\n", encoding="utf-8")
    customers()
    contract_template()
    inspection_forms()
    orders()
    region_files()
    pdfs()
    scanned()
    image_templates()
    paragraph_block_template()
    multi_form_files()
    pptx_template()
    csv_files()
    broken()
    print("fixtures written to", OUT)
