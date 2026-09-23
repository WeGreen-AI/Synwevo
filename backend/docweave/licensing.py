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
    ("argon2-cffi（含 argon2-cffi-bindings、cffi、pycparser）", "MIT",
     "口令哈希（Argon2id；参数经部署机实测，见 docweave/passwords.py）"),
    ("charset-normalizer", "MIT", "CSV 编码识别"),
    ("xlrd / olefile", "BSD-3-Clause / BSD-2-Clause", "XLS 读取、旧格式检测"),
    ("React / React Router / Vite / TypeScript", "MIT / Apache-2.0", "界面"),
    ("TipTap（开源部分）", "MIT", "内置文档编辑器"),
    ("Droid Sans Fallback（随 PyMuPDF 分发的字体）", "Apache-2.0", "PDF 中文字体"),
]


def source_offer_enabled() -> bool:
    return (os.environ.get("DOCWEAVE_SOURCE_OFFER") or "on").strip().lower() not in ("off", "0", "false", "no")


def pymupdf_installed() -> bool:
    """Is the AGPL component present in this deployment?

    Deliberately *not* the same question as "is the pymupdf engine selected".  Selecting
    pdfium changes which engine the PDF paths call; it removes nothing from the image, and
    two fallbacks import PyMuPDF whenever it imports rather than when the engine asks for it
    (``engine/image_template.py`` for the wrapper page, ``engine/converters.py`` for the CJK
    fallback font).  The AGPL obligation follows the binary that is distributed and run, not
    an environment variable, so this is what the §13 offer keys on.
    """
    import importlib.util
    return importlib.util.find_spec("pymupdf") is not None


def about() -> dict:
    """Three different claims that are routinely conflated, reported separately.

    "does not use PyMuPDF", "does not contain MuPDF" and "has no AGPL dependency" are not the
    same statement, and this used to report only the first while wording it as the third: with
    ``DOCWEAVE_PDF_ENGINE=pdfium`` and PyMuPDF still installed it said 「未加载 AGPL 组件」while
    ~53 MB of AGPL-3.0 MuPDF binary sat in the image and the fallbacks above still called into
    it.  A service asserting a false licensing fact about itself is worse than saying nothing.
    """
    from .engine import pdf_engine
    engine_uses = pdf_engine.ENGINE == "pymupdf"
    present = pymupdf_installed()
    if engine_uses:
        notice = ("当前部署启用了 PyMuPDF（AGPL-3.0）。按 AGPL-3.0 第 13 条，通过网络向他人提供本服务时，应向其提供本程序完整的对应源代码；"
                  "本页提供的源代码包即由正在运行的文件生成。如已取得 Artifex 商业许可，或改用 pdfium 引擎（DOCWEAVE_PDF_ENGINE=pdfium）"
                  "并从镜像中移除 pymupdf，可关闭此下载（DOCWEAVE_SOURCE_OFFER=off）。")
    elif present:
        notice = ("当前 PDF 引擎是 pdfium（pypdfium2，Apache-2.0 / BSD-3-Clause），**但本部署仍然安装了 PyMuPDF**："
                  "AGPL-3.0 的 MuPDF 二进制仍随镜像分发，且图片模板包装页与中文后备字体两处仍会调用它。"
                  "因此 AGPL-3.0 第 13 条的源代码提供义务依然适用。要真正去掉 AGPL 依赖，需按 "
                  "backend/requirements-pdfium.txt 构建、镜像中不安装 pymupdf。")
    else:
        notice = "本部署未安装 PyMuPDF，镜像中不含 MuPDF 二进制，无 AGPL 依赖；PDF / 图片模板的部分能力在此模式下受限。"
    return {
        "pdf_engine": pdf_engine.ENGINE,
        # the three claims, kept apart on purpose
        "pymupdf_used_by_engine": engine_uses,
        "pymupdf_installed": present,
        "agpl_component_active": present,        # distributed and callable ⇒ the obligation applies
        "source_offer": source_offer_enabled(),
        "third_party": [{"name": n, "license": lic, "role": role} for n, lic, role in THIRD_PARTY],
        "notice": notice,
    }


def source_status() -> dict:
    """Which of the promised paths actually exist where the service is running.

    The container image only COPYs ``backend/`` and the *compiled* ``frontend/dist``, so on a
    real deployment most of INCLUDE was absent — and the builder skipped every missing path in
    silence while the bundled README went on claiming the archive held "后端、前端源码、脚本、
    文档与依赖版本清单".  Measured against the deployed image that was 90 entries of backend
    only.  An incomplete Corresponding Source that says it is complete is the failure mode
    AGPL-3.0 §13 is about, so the gap is now reported instead of hidden.
    """
    missing = [rel for rel in INCLUDE if not (ROOT / rel).exists()]
    return {"root": str(ROOT), "missing": missing, "complete": not missing}


def source_archive() -> tuple[bytes, str]:
    buf = io.BytesIO()
    stamp = time.strftime("%Y%m%d")
    top = f"docweave-source-{stamp}"
    status = source_status()
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
        lines = ["DocWeave 对应源代码（Corresponding Source）", "",
                 "本压缩包由正在运行的服务按其安装目录中的文件生成。",
                 f"生成位置：{status['root']}", ""]
        if status["complete"]:
            lines += ["本包包含所列全部内容：后端、前端源码、脚本、文档与依赖版本清单。"]
        else:
            lines += ["⚠ 本包并不完整。以下内容在运行本服务的目录中不存在，因此未能包含：", ""]
            lines += [f"  - {rel}" for rel in status["missing"]]
            lines += ["", "这通常是因为容器镜像只复制了后端与编译后的前端产物。完整源代码请见项目仓库，",
                      "或用包含这些路径的镜像重新构建（见 Dockerfile 中的 COPY 行）。"]
        lines += ["", "第三方组件的源代码可从其官方发布获取（版本见 backend/requirements.txt 与 frontend/package-lock.json）。", ""]
        readme = "\n".join(lines).encode("utf-8")
        info = tarfile.TarInfo(f"{top}/SOURCE_OFFER.txt")
        info.size = len(readme)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(readme))
    return buf.getvalue(), f"{top}.tar.gz"
