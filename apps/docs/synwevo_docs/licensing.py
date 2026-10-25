"""Licences of the running deployment and the Corresponding Source offer.

Synwevo itself is licensed AGPL-3.0-or-later (``LICENSE``, ``NOTICE``; the SDKs under
``apps/docs/sdk/`` are Apache-2.0), and every default build also installs PyMuPDF (AGPL-3.0).
When the service is used by others over a network, AGPL-3.0 §13 requires offering those users the
complete source of the running program. ``source_archive`` builds that archive from the files
actually installed (backend code, frontend source, scripts, docs, dependency pins, the licence
files) – not from a separate copy that could drift. ``SYNWEVO_DOCS_SOURCE_OFFER=off`` switches the
download off; it is for an operator who offers the Corresponding Source another way, or who runs
an unmodified Synwevo without PyMuPDF (or with an Artifex commercial licence for it).
"""
from __future__ import annotations

import hashlib
import io
import os
import tarfile
import threading
import time
from pathlib import Path

from synwevo_core import env
from synwevo_core.http import repo_root

ROOT = repo_root() or Path(__file__).resolve().parents[3]
# The Corresponding Source of the *running program*. After the platform restructure that is more
# than Synwevo Docs' own directory: the process a network user reaches is composed from the shared
# packages, the account service, Synwevo Message, the business applications and the personal Agent
# (apps/suite), and the role composer, and an archive holding only `apps/docs` would be a partial
# source shipped under a complete-source claim — which is the exact failure AGPL-3.0 §13 is about.
# Whole directories, matching the Dockerfile's COPY lines one for one: apps/suite and apps/messages
# were copied into the image and installed with `pip install -e` for months while this list left
# them out, because only INCLUDE ⊆ COPY was tested. Both directions are tested now
# (apps/docs/tests/test_licensing.py).
INCLUDE = (
    # the applications
    "apps/docs", "apps/portal", "apps/messages", "apps/suite",
    # the platform they run on
    "packages", "services/identity", "infra/server",
    # how it is built, run and documented
    "infra/Dockerfile", "infra/docker-compose.yml", "scripts", "tests", "docs",
    "package.json", "package-lock.json", "pyproject.toml", "requirements-dev.txt",
    "README.md", "README.en.md", "LICENSE", "NOTICE", "TRADEMARKS.md",
)
EXCLUDE_PARTS = {"__pycache__", "node_modules", ".venv", "dist", ".pytest_cache", "screenshots",
                 "data", "testdata", ".local"}
# `.traineddata`: the tessdata_fast OCR models in apps/docs/tessdata are unmodified third-party data
# (Apache-2.0, available upstream), not source; their README and LICENSE stay in the archive.
EXCLUDE_SUFFIXES = (".pyc", ".log", ".traineddata")
MAX_FILE = 5 * 1024 * 1024

# The program's own licence (LICENSE); the SDKs under apps/docs/sdk/ are Apache-2.0 (their own LICENSE).
PROGRAM_LICENSE = "AGPL-3.0-or-later"
SDK_LICENSE = "Apache-2.0"

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
     "口令哈希（Argon2id；参数经部署机实测，见 synwevo_core/passwords.py）"),
    ("PyOTP", "MIT", "TOTP 第二因素"),
    ("cryptography", "Apache-2.0 或 BSD-3-Clause", "TOTP 种子落地加密（AES-256-GCM）、WebAuthn 签名校验"),
    ("webauthn（Duo）", "BSD-3-Clause", "Passkey / WebAuthn 注册与登录仪式（协议实现）"),
    ("cbor2 / pyOpenSSL / pyasn1", "MIT / Apache-2.0 / BSD-2-Clause", "WebAuthn 的 COSE 与证书解析依赖"),
    ("Authlib", "BSD-3-Clause", "OAuth 2.0 / OpenID Connect 授权服务器与客户端（协议实现）"),
    ("charset-normalizer", "MIT", "CSV 编码识别"),
    ("xlrd / olefile", "BSD-3-Clause / BSD-2-Clause", "XLS 读取、旧格式检测"),
    ("React / React Router / Vite / TypeScript", "MIT / Apache-2.0", "界面"),
    ("TipTap（开源部分）", "MIT", "内置文档编辑器"),
    ("Droid Sans Fallback（随 PyMuPDF 分发的字体）", "Apache-2.0", "PDF 中文字体"),
]


def source_offer_enabled() -> bool:
    return (env.docs("SOURCE_OFFER") or "on").strip().lower() not in ("off", "0", "false", "no")


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
    ``SYNWEVO_DOCS_PDF_ENGINE=pdfium`` and PyMuPDF still installed it said 「未加载 AGPL 组件」while
    ~53 MB of AGPL-3.0 MuPDF binary sat in the image and the fallbacks above still called into
    it.  A service asserting a false licensing fact about itself is worse than saying nothing.
    """
    from .engine import pdf_engine
    engine_uses = pdf_engine.ENGINE == "pymupdf"
    present = pymupdf_installed()
    own = ("Synwevo 以 AGPL-3.0-or-later 许可（见 LICENSE 与 NOTICE；apps/docs/sdk/ 下的 SDK 为 Apache-2.0）。"
           "向网络用户提供修改过的 Synwevo 时，第 13 条要求向他们提供对应源代码。")
    if engine_uses:
        notice = ("当前部署启用了 PyMuPDF（AGPL-3.0）。按 AGPL-3.0 第 13 条，通过网络向他人提供本服务时，应向其提供本程序完整的对应源代码；"
                  "本页提供的源代码包即由正在运行的文件生成。关闭此下载（SYNWEVO_DOCS_SOURCE_OFFER=off）的前提是："
                  "PyMuPDF 已取得 Artifex 商业许可，或已改用 pdfium 引擎（SYNWEVO_DOCS_PDF_ENGINE=pdfium）并从镜像中移除 pymupdf；"
                  "并且运行的是未经修改的 Synwevo，或已通过其他途径向用户提供对应源代码。")
    elif present:
        notice = ("当前 PDF 引擎是 pdfium（pypdfium2，Apache-2.0 / BSD-3-Clause），**但本部署仍然安装了 PyMuPDF**："
                  "AGPL-3.0 的 MuPDF 二进制仍随镜像分发，且图片模板包装页与中文后备字体两处仍会调用它。"
                  "因此 AGPL-3.0 第 13 条的源代码提供义务依然适用。要去掉这一 AGPL 依赖，镜像中就不能安装 pymupdf；"
                  "目前没有经过验证的这种平台构建（apps/docs/requirements-pdfium.txt 只覆盖 Synwevo Docs，且缺少 pycrdt）。")
    else:
        notice = "本部署未安装 PyMuPDF，镜像中不含 MuPDF 二进制，无 AGPL 依赖；PDF / 图片模板的部分能力在此模式下受限。"
    notice = own + notice
    return {
        "license": PROGRAM_LICENSE,
        "sdk_license": SDK_LICENSE,
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


def _walk():
    """(path, archive-relative name) of every source file under INCLUDE, before the size limit."""
    for rel in INCLUDE:
        p = ROOT / rel
        if not p.exists():
            continue
        files = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file())
        for f in files:
            parts = f.relative_to(ROOT).parts
            # Suffix match as well as exact: `synwevo_core.egg-info` is one path component,
            # so a set membership test never saw it and build metadata rode along.
            if (set(parts) & EXCLUDE_PARTS
                    or any(part.endswith(".egg-info") for part in parts)
                    or f.suffix in EXCLUDE_SUFFIXES):
                continue
            yield f, f.relative_to(ROOT).as_posix()


def oversized() -> list[str]:
    """Source files left out of the archive for size. Named in SOURCE_OFFER.txt, never dropped quietly."""
    return [name for f, name in _walk() if f.stat().st_size > MAX_FILE]


def _archive_files():
    """(path, archive-relative name) of every file the Corresponding Source contains."""
    for f, name in _walk():
        if f.stat().st_size <= MAX_FILE:
            yield f, name


# One archive per state of the installed files. It used to be rebuilt — the whole tree read, tarred
# and gzipped in memory — on every request, which any signed-in user could repeat at will. The key
# is a stat-only fingerprint (names, sizes, modification times) plus the day stamped into the name.
_ARCHIVE_LOCK = threading.Lock()
_ARCHIVE: dict = {}


def _fingerprint(files: list) -> str:
    h = hashlib.sha256(str(ROOT).encode())
    for f, name in files:
        st = f.stat()
        h.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def source_archive() -> tuple[bytes, str]:
    files = list(_archive_files())
    stamp = time.strftime("%Y%m%d")
    key = (_fingerprint(files), stamp)
    with _ARCHIVE_LOCK:
        if _ARCHIVE.get("key") == key:
            return _ARCHIVE["data"], _ARCHIVE["name"]
        data, name = _build_archive(files, stamp)
        _ARCHIVE.clear()
        _ARCHIVE.update(key=key, data=data, name=name)
        return data, name


def _build_archive(files: list, stamp: str) -> tuple[bytes, str]:
    buf = io.BytesIO()
    top = f"synwevo-source-{stamp}"
    status = source_status()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f, name in files:
            tar.add(f, arcname=f"{top}/{name}", recursive=False)
        # Where it was generated is described, not spelled out: the absolute path of the
        # installation is nobody's business outside the server and was handed to every user.
        lines = ["Synwevo 对应源代码（Corresponding Source）", "",
                 "本压缩包由正在运行的服务按其安装目录中的文件生成。",
                 "许可：Synwevo 以 AGPL-3.0-or-later 许可（见 LICENSE 与 NOTICE）；apps/docs/sdk/ 下的 SDK 为 Apache-2.0；"
                 "Synwevo 名称、Logo 与图标不在许可之内（见 TRADEMARKS.md）。", ""]
        big = oversized()
        if status["complete"] and not big:
            lines += ["本包包含所列全部内容：Synwevo Docs、门户、Message、业务应用与个人 Agent（apps/suite）、平台共享包与前端包、"
                      "账户服务、部署组装、构建与运行脚本、测试、文档、依赖版本清单，以及 LICENSE、NOTICE 与 TRADEMARKS.md。"]
        if not status["complete"]:
            lines += ["⚠ 本包并不完整。以下内容在运行本服务的目录中不存在，因此未能包含：", ""]
            lines += [f"  - {rel}" for rel in status["missing"]]
            lines += ["", "这说明构建运行目录（容器镜像）时漏掉了它们。完整源代码请见项目仓库，",
                      "或用包含这些路径的镜像重新构建（见 infra/Dockerfile 中的 COPY 行）。"]
        if big:
            lines += ["", "⚠ 以下文件单个超过 5 MB，未放入本包，请从项目仓库获取：", ""]
            lines += [f"  - {name}" for name in big]
        lines += ["", "未包含：用户数据、虚拟环境与 node_modules、编译产物，以及 apps/docs/tessdata 中的第三方 OCR 模型文件"
                      "（tessdata_fast，Apache-2.0，未经修改，来源见该目录的 README）。",
                  "第三方组件的源代码可从其官方发布获取（版本见各模块的 requirements.txt、infra/server/requirements.lock "
                  "与仓库根目录的 package-lock.json）。", ""]
        readme = "\n".join(lines).encode("utf-8")
        info = tarfile.TarInfo(f"{top}/SOURCE_OFFER.txt")
        info.size = len(readme)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(readme))
    return buf.getvalue(), f"{top}.tar.gz"
