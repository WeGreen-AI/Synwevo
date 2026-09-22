"""Licences of the running deployment and the Corresponding Source offer.

When the PyMuPDF engine (AGPL-3.0) is active and the service is used by others over a
network, AGPL-3.0 §13 requires offering those users the complete source of the running
program.  ``source_archive`` builds that archive from the files actually installed
(backend code, frontend source, scripts, docs, dependency pins) – not from a separate
copy that could drift.  Operators with an Artifex commercial licence, or running the
AGPL-free pdfium engine, can switch the offer off with ``DOCWEAVE_SOURCE_OFFER=off``.
"""
from __future__ import annotations

import io
import os
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ("backend/docweave", "backend/tests", "backend/run.py", "backend/requirements.txt", "backend/requirements-dev.txt",
           "frontend/src", "frontend/index.html", "frontend/package.json", "frontend/package-lock.json", "frontend/tsconfig.json",
           "frontend/vite.config.ts", "scripts", "docs", "README.md", "Dockerfile", "docker-compose.yml", "LICENSE", "NOTICE")
EXCLUDE_PARTS = {"__pycache__", "node_modules", ".venv", "dist", ".pytest_cache", "screenshots"}
MAX_FILE = 5 * 1024 * 1024

THIRD_PARTY = [
    # name, licence, role
    ("PyMuPDF", "AGPL-3.0 或 Artifex 商业许可", "PDF 模板、图片模板、PDF 读取（pymupdf 引擎）"),
    ("pypdfium2 / PDFium", "Apache-2.0 或 BSD-3-Clause（绑定）；BSD-3-Clause / Apache-2.0（PDFium）", "PDF 读取（pdfium 引擎）"),
    ("LibreOffice（外部程序）", "MPL-2.0", "旧格式转换、PDF 导出、真实分页、公式后备计算"),
    ("Tesseract OCR（外部程序）+ chi_sim / eng 模型", "Apache-2.0", "扫描件 / 图片本地 OCR"),
    ("FastAPI / Starlette / Uvicorn / Pydantic", "MIT / BSD-3-Clause", "Web 服务"),
    ("openpyxl", "MIT", "XLSX 读取与预览"),
    ("python-docx / python-pptx", "MIT", "DOCX / PPTX 读取与内置模板生成"),
    ("lxml", "BSD-3-Clause", "OOXML 直接编辑"),
    ("Pillow", "MIT-CMU (HPND)", "图片处理"),
    ("fontTools", "MIT", "字体子集"),
    ("charset-normalizer", "MIT", "CSV 编码识别"),
    ("xlrd / olefile", "BSD-3-Clause / BSD-2-Clause", "XLS 读取、旧格式检测"),
    ("React / React Router / Vite / TypeScript", "MIT / Apache-2.0", "界面"),
    ("TipTap（开源部分）", "MIT", "内置文档编辑器"),
    ("Droid Sans Fallback（随 PyMuPDF 分发的字体）", "Apache-2.0", "PDF 中文字体"),
]


def source_offer_enabled() -> bool:
    return (os.environ.get("DOCWEAVE_SOURCE_OFFER") or "on").strip().lower() not in ("off", "0", "false", "no")


def about() -> dict:
    from .engine import pdf_engine
    agpl = pdf_engine.ENGINE == "pymupdf"
    return {
        "pdf_engine": pdf_engine.ENGINE,
        "agpl_component_active": agpl,
        "source_offer": source_offer_enabled(),
        "third_party": [{"name": n, "license": lic, "role": role} for n, lic, role in THIRD_PARTY],
        "notice": ("当前部署启用了 PyMuPDF（AGPL-3.0）。按 AGPL-3.0 第 13 条，通过网络向他人提供本服务时，应向其提供本程序完整的对应源代码；"
                   "本页提供的源代码包即由正在运行的文件生成。如已取得 Artifex 商业许可，或改用 pdfium 引擎（DOCWEAVE_PDF_ENGINE=pdfium），可关闭此下载（DOCWEAVE_SOURCE_OFFER=off）。")
        if agpl else "当前部署使用 pdfium 引擎（pypdfium2，Apache-2.0 / BSD-3-Clause），未加载 AGPL 组件；PDF / 图片模板在此模式下不可用。",
    }


def source_archive() -> tuple[bytes, str]:
    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d")
    top = f"docweave-source-{stamp}"
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in INCLUDE:
            p = ROOT / rel
            if not p.exists():
                continue
            files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
            for f in files:
                parts = set(f.relative_to(ROOT).parts)
                if parts & EXCLUDE_PARTS or f.suffix in (".pyc", ".log") or f.stat().st_size > MAX_FILE:
                    continue
                tar.add(f, arcname=f"{top}/{f.relative_to(ROOT).as_posix()}", recursive=False)
        readme = ("DocWeave 对应源代码（Corresponding Source）\n\n"
                  "本压缩包由正在运行的服务按其安装目录中的文件生成，包含后端、前端源码、脚本、文档与依赖版本清单。\n"
                  "第三方组件的源代码可从其官方发布获取（版本见 backend/requirements.txt 与 frontend/package-lock.json）。\n").encode("utf-8")
        info = tarfile.TarInfo(f"{top}/SOURCE_OFFER.txt")
        info.size = len(readme)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(readme))
    return buf.getvalue(), f"{top}.tar.gz"
