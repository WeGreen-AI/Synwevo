"""Format capability matrix shown in the UI (and mirrored in docs/FORMATS.md).

Status values:
  verified    – implemented and covered by automated tests in tests/
  conversion  – works through a LibreOffice conversion (lossy; stated to the user)
  partial     – works with the stated limits
  unavailable – implemented but the required local tool is not installed
  no          – not supported (with the reason / alternative)
"""
from __future__ import annotations

from .engine import pdf_engine
from .engine.converters import soffice_path, soffice_version
from .engine.ocr import available_langs, tesseract_path


def _pymupdf_installed() -> bool:
    from .licensing import pymupdf_installed
    return pymupdf_installed()


def _engine_license(engine: str) -> str:
    if engine == "pymupdf":
        return "AGPL-3.0 或 Artifex 商业许可"
    if _pymupdf_installed():
        return "引擎 Apache-2.0 / BSD-3-Clause；但本部署仍安装了 PyMuPDF（AGPL-3.0），义务仍适用"
    return "Apache-2.0 / BSD-3-Clause"


def capability_report() -> dict:
    lo = bool(soffice_path())
    tess = bool(tesseract_path())
    langs = available_langs() if tess else []
    chi = "chi_sim" in langs
    conv = "conversion" if lo else "unavailable"
    ocr = ("verified" if chi else "partial") if tess else "unavailable"

    def row(fmt, source, preview, template, fill, native, pdf, fidelity, where, batch):
        return {"format": fmt, "source": source, "preview": preview, "template": template, "fill": fill, "native_export": native,
                "pdf_export": pdf, "fidelity": fidelity, "processing": where, "batch": batch}

    rows = _rows(lo, conv, ocr, row)
    if not pdf_engine.pdf_templates_available():
        why = pdf_engine.TEMPLATES_UNAVAILABLE
        images_ok = pdf_engine.image_templates_available()
        # One row covers scanned PDFs together with PNG/JPEG, but they no longer share a
        # fate: image templates are filled in pixels and work here, scanned-PDF templates
        # still need a PDF engine that can rewrite page content.
        mixed = ("partial", "图片模板（PNG/JPEG）可用：在像素上直接清除与写入，支持中文、换行与缩放；"
                           "扫描型 PDF 模板仍需 PyMuPDF。" + why)
        for r in rows:
            if not r["format"].startswith("PDF"):
                continue
            is_mixed_row = "PNG" in r["format"] or "JPEG" in r["format"]
            for k in ("template", "fill"):
                r[k] = (mixed if (is_mixed_row and images_ok) else ("unavailable", why))
            if r["native_export"][0] != "no":
                r["native_export"] = ((("partial", "图片模板输出同尺寸 PNG/JPEG；扫描型 PDF 无原生输出"))
                                      if (is_mixed_row and images_ok)
                                      else ("unavailable", "PDF 模板不可用，因此没有原生输出"))
            r["processing"] = "服务端本地（pypdfium2，Apache-2.0 / BSD-3）" + ("" if "Tesseract" not in r["processing"] else " + Tesseract")
    return {"rows": rows, "environment": {"libreoffice": soffice_version() if lo else None, "tesseract": tesseract_path(),
                                          "ocr_languages": langs, "pdf_engine": pdf_engine.ENGINE,
                                          # The engine says which code runs; it says nothing about what is
                                          # installed. Reporting "Apache-2.0 / BSD-3-Clause" while the AGPL
                                          # MuPDF binary ships in the same image is a false licensing claim,
                                          # so the AGPL component is reported separately. See licensing.about().
                                          "pdf_engine_license": _engine_license(pdf_engine.ENGINE),
                                          "agpl_component_installed": _pymupdf_installed()},
            "legend": {"verified": "已实现并有自动化测试", "conversion": "经 LibreOffice 转换（可能有损，已提示）", "partial": "有限支持（见说明）",
                       "unavailable": "功能已实现但本机缺少依赖或当前部署未启用", "no": "不支持"}}


def _rows(lo, conv, ocr, row):
    return [
        row("XLSX / XLSM", ("verified", "读取单元格值、类型、数字格式显示、合并单元格；公式读取文件内计算结果；没有结果的公式由内置公式引擎计算"),
            ("verified", "完整表格网格（分页加载全部行），工作表切换，单元格/区域框选"),
            ("verified", "单元格、固定区域、重复明细行；工作副本以隐藏定义名称标记字段"),
            ("verified", "单值、固定区域、重复明细行（插入行时平移公式、合并、条件格式、数据验证、图表范围）"),
            ("verified", "直接改写 XML，未修改部分原样保留（图表、图片、透视表等部件不被重写）"),
            (("verified" if lo else "unavailable"), "经 LibreOffice 转换"),
            "内置公式引擎（90+ 常用函数）计算并写入结果，其他软件读取也能看到正确值；不支持的函数自动改用 LibreOffice 计算，Excel 打开时仍会重算。XLSM 输出扩展名为 .xlsx，宏不执行",
            "服务端本地", "支持批量、失败重试、重新打开校验"),
        row("CSV / TSV", ("verified", "RFC 4180 解析；自动识别 UTF-8/GB18030/UTF-16 编码与分隔符，可手动修正；单元格内换行不拆行；全部按文本保留（前导零、长数字不变）"),
            ("verified", "表格网格"), ("no", "CSV 无样式/合并/多工作表，不能作为排版模板；需要排版请用 XLSX 或文档模板"),
            ("no", "—"), ("partial", "可由表格型模板导出 CSV（仅第一个工作表的值）"), ("no", "—"),
            "只保存值；导出 CSV 时写出公式的计算结果", "服务端本地", "支持"),
        row("XLS（97-2003）", (("conversion" if lo else "partial"), "有 LibreOffice 时转换为 XLSX 读取；否则直接读取值与数字格式（xlrd）"),
            ("verified", "表格网格"), (conv, "转换为 XLSX 后作为模板，输出为 XLSX"), (conv, "同 XLSX"), (conv, "输出 XLSX（不回写 XLS）"),
            (conv, "经 LibreOffice"), "转换可能改变部分格式；宏不执行", "服务端本地 + LibreOffice", "支持"),
        row("ODS", (conv, "经 LibreOffice 转换为 XLSX 读取"), (conv, "表格网格"), (conv, "转换为 XLSX 模板"), (conv, "同 XLSX"),
            (conv, "输出 XLSX"), (conv, "经 LibreOffice"), "转换可能损失部分格式", "服务端本地 + LibreOffice", "支持"),
        row("DOCX / DOCM", ("verified", "按段落/表格结构读取；取值使用结构路径 + 前后文锚点；表格单元格使用标签锚点"),
            ("verified", "正文、表格（含合并）、图片、编号、页眉页脚；文字可划选"),
            ("verified", "划选文字（可跨多个 run）替换或插入、表格单元格、表格区域、重复明细行、重复内容块（划选连续段落，「标签：值」自动成为明细列）；字段为内容控件"),
            ("verified", "单值（沿用字段起始文字样式）、固定区域、重复表格行（表头跨页重复、行不跨页拆分、0/1/多行）、重复内容块（每条明细复制一组段落，保留格式/编号/图片，可空行或分页分隔）"),
            ("verified", "未修改部件原样保留；只改写包含字段的段落"),
            (("verified" if lo else "unavailable"), "经 LibreOffice 转换"),
            "新内容更长时分页可能变化；不承诺像素级不变。DOCM 输出为 .docx",
            "服务端本地", "支持"),
        row("DOC / ODT / RTF", (conv, "经 LibreOffice 转换为 DOCX 读取"), (conv, "同 DOCX"), (conv, "转换为 DOCX 模板，输出 DOCX"), (conv, "同 DOCX"),
            (conv, "输出 DOCX"), (conv, "经 LibreOffice"), "转换可能改变版式、丢失宏与嵌入对象", "服务端本地 + LibreOffice", "支持"),
        row("PDF（文本型）", ("verified", "按页面坐标框选，读取框内文字；定位标签随版式移动校验，页面尺寸/旋转不同则报错"),
            ("verified", "页面图像 + 文字层框选，缩放/旋转后坐标正确换算"),
            ("verified", "框选区域设为单值字段或重复明细区域：真正删除区域内原文字（可再次提取验证），保留表格线；明细区域按表格线/表头/样例行自动识别列与行高，可拖动调整"),
            ("verified", "单值文本框：自动换行、从设定字号缩小到最小字号，仍放不下则报错不截断；重复明细逐行写入，超出区域时按设置报错、复制本页续写或压缩行高"),
            ("verified", "PDF"), ("verified", "原生"),
            "只替换框内文字；不能恢复为可编辑 Word；明细区域行高固定（单元格内可换行缩字，不会自动加高行）", "服务端本地（PyMuPDF，AGPL/商业许可）", "支持"),
        row("PDF（表单型 AcroForm）", ("partial", "按文本型读取页面文字"), ("verified", "显示表单字段"),
            ("verified", "已定义的表单字段可直接设为待填字段"),
            ("verified", "填写后扁平化为页面文字（嵌入中文字体子集）"), ("verified", "PDF"), ("verified", "原生"),
            "输出表单为扁平化文字，不保留可编辑字段", "服务端本地", "支持"),
        row("PDF（扫描型）/ PNG / JPEG / TIFF / BMP / WEBP", (ocr, "有文字层优先直接提取；无文字层时本地 Tesseract OCR（chi_sim+eng），识别词含置信度，低置信度需人工确认，可逐项修正"),
            ("verified", "原始图像预览与识别文字分开保存"),
            ("verified", "扫描 PDF 与 PNG/JPEG 图片都可作为模板：框选区域按周围背景重建像素后写入（跟随渐变与光照），文字颜色与字号取自原内容；像素绘制的表格可设为重复明细区域（表格线从图像识别、列名本地 OCR 建议）"),
            ("verified", "单值文本框与重复明细（逐格写入，保留表格线）"), ("verified", "图片模板输出同尺寸 PNG/JPEG（保留 DPI 与 ICC 颜色配置），也可导出 PDF；扫描 PDF 输出 PDF"), ("verified", "PDF"),
            "OCR 结果用于取值，不会用 OCR 重建整份文件；JPEG 输出重新压缩（质量 95）；图片模板只有一页，明细超出时只能报错或压缩行高",
            "服务端本地（Tesseract）", "支持"),
        row("PPTX / PPTM", ("verified", "按幻灯片→形状→段落→字符偏移定位，表格单元格定位；形状编号变化时按唯一形状名称回退"),
            ("verified", "按幻灯片展示文本框、表格、图片"),
            ("verified", "文本框内划选文字、表格单元格、表格区域、重复表格行"),
            ("verified", "单值、区域、重复表格行（超出幻灯片底部会警告，不自动分页）"),
            ("verified", "未修改部件原样保留"), (("verified" if lo else "unavailable"), "经 LibreOffice 转换"),
            "不套用 Word 分页规则；动画/母版不受影响", "服务端本地", "支持"),
        row("PPT / ODP", (conv, "经 LibreOffice 转换为 PPTX"), (conv, "同 PPTX"), (conv, "转换为 PPTX 模板"), (conv, "同 PPTX"),
            (conv, "输出 PPTX"), (conv, "经 LibreOffice"), "转换可能丢失动画与嵌入对象", "服务端本地 + LibreOffice", "支持"),
        row("TXT / Markdown", ("verified", "按行 + 前后文锚点取值"), ("verified", "逐行显示，可划选"), ("no", "纯文本不作为模板（无排版能力）"),
            ("no", "—"), ("no", "—"), ("no", "—"), "—", "服务端本地", "—"),
        row("HTML", ("verified", "转为纯文本读取（不执行脚本、不加载外部资源）；<table> 识别为表格"), ("verified", "文本/表格视图（不渲染原始 HTML）"),
            ("no", "不作为模板"), ("no", "—"), ("no", "—"), ("no", "—"), "—", "服务端本地（隔离：不渲染原始 HTML）", "—"),
    ]
