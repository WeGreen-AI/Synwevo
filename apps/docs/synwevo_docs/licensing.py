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
import math
import os
import tarfile
import threading
import time
from pathlib import Path

from fastapi import Request   # module level: the route below is annotated with it (postponed annotations)

from synwevo_core import env
from synwevo_core.http import repo_root

ROOT = repo_root() or Path(__file__).resolve().parents[3]
# The Corresponding Source of the *running program*. After the platform restructure that is more
# than Synwevo Docs' own directory: the process a network user reaches is composed from the shared
# packages, the account service, Synwevo Message, the business applications and the personal Agent
# (apps/suite), and the role composer, and an archive holding only `apps/docs` would be a partial
# source shipped under a complete-source claim — which is the exact failure AGPL-3.0 §13 is about.
# Whole directories, matching the Dockerfile's COPY lines one for one: apps/suite and apps/messages
# were copied into the image and installed with `pip install -e` while this list left them out,
# because only INCLUDE ⊆ COPY was tested. Both directions are tested now
# (apps/docs/tests/test_licensing.py).
#
# "What the Dockerfile copies" is not by itself a definition of complete — it is the same list
# twice. The reference outside both is the repository: every tracked top-level entry is either here
# or in NOT_OFFERED with its reason (tested against `git ls-files`). Corresponding Source includes
# the scripts that build, install and run the program (GPLv3 §1), so the production stack
# (infra/production: compose, nginx, the deploy receiver), the CI workflow that builds and ships
# the image (.github), the image's build-context rules (.dockerignore), the documented
# configuration (.env.example) and the test configuration (conftest.py) are offered too.
INCLUDE = (
    # the applications
    "apps/docs", "apps/portal", "apps/messages", "apps/suite",
    # the platform they run on
    "packages", "services/identity", "infra/server",
    # how it is built, installed, run, tested and documented
    "infra/Dockerfile", "infra/docker-compose.yml", "infra/production", ".github", ".dockerignore",
    ".env.example", "conftest.py", "scripts", "tests", "docs",
    "package.json", "package-lock.json", "pyproject.toml", "requirements-dev.txt",
    "README.md", "README.en.md", "LICENSE", "NOTICE", "TRADEMARKS.md",
)
#: Tracked top-level entries that are not part of the program's Corresponding Source, and why.
#: Nothing here is needed to build, install, run or modify the program.
NOT_OFFERED = {
    "AGENTS.md": "the working rules for people and coding agents who change this repository",
    "CLAUDE.md": "the same rules, loaded by one coding tool",
    ".gitignore": "which local files version control ignores",
    ".gitattributes": "which evidence files `git archive` leaves out of a release",
}
EXCLUDE_PARTS = {"__pycache__", "node_modules", ".venv", "dist", ".pytest_cache", "screenshots",
                 "data", "testdata", ".local"}
# `.traineddata`: the tessdata_fast OCR models in apps/docs/tessdata are unmodified third-party data
# (Apache-2.0, available upstream), not source; their README and LICENSE stay in the archive.
EXCLUDE_SUFFIXES = (".pyc", ".log", ".traineddata")
MAX_FILE = 5 * 1024 * 1024

# The program's own licence (LICENSE); the SDKs under apps/docs/sdk/ are Apache-2.0 (their own LICENSE).
PROGRAM_LICENSE = "AGPL-3.0-or-later"
SDK_LICENSE = "Apache-2.0"
LICENSE_NOTICE = ("Synwevo 以 AGPL-3.0-or-later 许可（见 LICENSE 与 NOTICE；apps/docs/sdk/ 下的 SDK 为 Apache-2.0；"
                  "Synwevo 名称、Logo 与图标不在许可之内，见 TRADEMARKS.md）。"
                  "向网络用户提供修改过的 Synwevo 时，第 13 条要求向他们提供对应源代码。")

# Every component the running deployment uses or ships, grouped by what it does. "Every" is held by
# apps/docs/tests/test_licensing.py: each runtime pin in infra/server/requirements.lock and each
# direct front-end dependency must be named in a row, and every non-development npm package must
# carry a licence the catch-all row states. NOTICE repeats this table by hand; a test keeps the two
# in step. The production services that run beside the image (ClamAV, nginx) are listed too.
THIRD_PARTY = [
    # name, licence, role
    ("PyMuPDF", "AGPL-3.0 或 Artifex 商业许可", "PDF 模板、图片模板、PDF 读取（pymupdf 引擎）"),
    ("pypdfium2 / PDFium", "Apache-2.0 或 BSD-3-Clause（绑定）；BSD-3-Clause / Apache-2.0（PDFium）", "PDF 读取（pdfium 引擎）"),
    ("LibreOffice（外部程序）", "MPL-2.0", "旧格式转换、PDF 导出、真实分页、公式后备计算"),
    ("Tesseract OCR（外部程序）+ tessdata_fast 模型（chi_sim / eng / osd）", "Apache-2.0", "扫描件 / 图片本地 OCR"),
    ("FastAPI / Starlette / Uvicorn / Pydantic", "MIT / BSD-3-Clause", "Web 服务"),
    ("pydantic-core / annotated-types / annotated-doc / typing-inspection", "MIT", "Pydantic 与 FastAPI 的运行时依赖"),
    ("websockets / h11 / click", "BSD-3-Clause / MIT / BSD-3-Clause", "Uvicorn 的 WebSocket 与 HTTP/1.1 协议实现、命令行解析"),
    ("anyio / sniffio / idna", "MIT / MIT 或 Apache-2.0 / BSD-3-Clause", "异步运行时与国际化域名"),
    ("python-multipart", "Apache-2.0", "多文件上传"),
    ("httpx / httpcore / httpx2 / httpcore2", "BSD-3-Clause", "出站 HTTP 客户端（登出回调、MCP、模型调用）"),
    ("certifi / truststore", "MPL-2.0 / MIT", "出站 HTTPS 的根证书与系统证书库"),
    ("openpyxl", "MIT", "XLSX 读取与预览"),
    ("python-docx / python-pptx", "MIT", "DOCX / PPTX 读取与内置模板生成"),
    ("et-xmlfile / XlsxWriter", "MIT / BSD-2-Clause", "openpyxl 与 python-pptx 的依赖"),
    ("lxml", "BSD-3-Clause", "OOXML 直接编辑"),
    ("Pillow", "MIT-CMU (HPND)", "图片处理"),
    ("fontTools", "MIT", "字体子集"),
    ("argon2-cffi / argon2-cffi-bindings / cffi / pycparser", "MIT / MIT / MIT-0 / BSD-3-Clause",
     "口令哈希（Argon2id；参数经部署机实测，见 synwevo_core/passwords.py）"),
    ("PyOTP", "MIT", "TOTP 第二因素"),
    ("cryptography", "Apache-2.0 或 BSD-3-Clause", "TOTP 种子落地加密（AES-256-GCM）、WebAuthn 签名校验"),
    ("py-vapid / http-ece", "MPL-2.0 / MIT", "Web 推送（VAPID 签名与载荷加密）"),
    ("webauthn（Duo）", "BSD-3-Clause", "Passkey / WebAuthn 注册与登录仪式（协议实现）"),
    ("cbor2 / pyOpenSSL / pyasn1 / pyasn1-modules", "MIT / Apache-2.0 / BSD-2-Clause / BSD-2-Clause",
     "WebAuthn 的 COSE 与证书解析依赖"),
    ("Authlib", "BSD-3-Clause", "OAuth 2.0 / OpenID Connect 授权服务器与客户端（协议实现）"),
    ("charset-normalizer", "MIT", "CSV 编码识别"),
    ("xlrd / olefile", "BSD-3-Clause / BSD-2-Clause", "XLS 读取、旧格式检测"),
    ("pycrdt", "MIT", "文档实时协同（服务端共享文档）"),
    ("anthropic / jiter / docstring-parser", "MIT", "个人 Agent 调用所配置的模型提供方（Anthropic）"),
    ("mcp / mcp-types", "MIT", "MCP 服务端与 Agent 的 MCP 客户端"),
    ("jsonschema / jsonschema-specifications / referencing / rpds-py / attrs", "MIT", "按 JSON Schema 校验外部工具的输入"),
    ("sse-starlette", "BSD-3-Clause", "MCP 服务端的流式响应"),
    ("opentelemetry-api", "Apache-2.0", "MCP SDK 的可观测性接口（未配置导出器）"),
    ("PyJWT", "MIT", "MCP SDK 授权模块的依赖"),
    ("typing_extensions", "PSF-2.0", "类型标注的运行时支持"),
    ("react / react-dom / react-router-dom / Vite / TypeScript", "MIT / Apache-2.0", "界面"),
    ("TipTap（开源部分）", "MIT", "内置文档编辑器"),
    ("Yjs / y-websocket / y-protocols / lib0 / @tiptap/y-tiptap", "MIT", "文档实时协同（浏览器端）"),
    ("qrcode", "MIT", "TOTP 绑定二维码"),
    ("其余前端依赖（package-lock.json 中的非开发依赖）", "MIT / ISC", "随界面一同打包"),
    ("Droid Sans Fallback（随 PyMuPDF 分发的字体）", "Apache-2.0", "PDF 中文字体"),
    ("Noto Sans / Serif CJK（fonts-noto-cjk，镜像内安装）", "OFL-1.1", "LibreOffice 导出 PDF 时的中文字体"),
    ("ClamAV（生产部署的独立容器）", "GPL-2.0-only", "上传文件的恶意软件扫描（经网络调用，不与本程序链接）"),
    ("nginx（生产部署的反向代理容器）", "BSD-2-Clause", "TLS 终止与反向代理"),
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
    if engine_uses:
        notice = ("当前部署启用了 PyMuPDF（AGPL-3.0）。按 AGPL-3.0 第 13 条，通过网络向他人提供本服务时，应向其提供本程序完整的对应源代码；"
                  "本服务在 /api/source-code 提供由正在运行的文件生成的源代码包，无需登录即可下载。关闭此下载（SYNWEVO_DOCS_SOURCE_OFFER=off）的前提是："
                  "PyMuPDF 已取得 Artifex 商业许可，或已改用 pdfium 引擎（SYNWEVO_DOCS_PDF_ENGINE=pdfium）并从镜像中移除 pymupdf；"
                  "并且运行的是未经修改的 Synwevo，或已通过其他途径向用户提供对应源代码。")
    elif present:
        notice = ("当前 PDF 引擎是 pdfium（pypdfium2，Apache-2.0 / BSD-3-Clause），但本部署仍然安装了 PyMuPDF："
                  "AGPL-3.0 的 MuPDF 二进制仍随镜像分发，且图片模板包装页与中文后备字体两处仍会调用它。"
                  "因此 AGPL-3.0 第 13 条的源代码提供义务依然适用。要去掉这一 AGPL 依赖，镜像中就不能安装 pymupdf；"
                  "目前没有经过验证的这种平台构建（apps/docs/requirements-pdfium.txt 只覆盖 Synwevo Docs，且缺少 pycrdt）。")
    else:
        notice = "本部署未安装 PyMuPDF，镜像中不含 MuPDF 二进制，无第三方 AGPL 依赖；PDF / 图片模板的部分能力在此模式下受限。"
    return {
        "license": PROGRAM_LICENSE,
        "sdk_license": SDK_LICENSE,
        # The program's own licence, said once; `notice` below is about the third-party AGPL
        # component only. Two fields rather than one joined sentence, so a page can show and
        # translate each as it is.
        "license_notice": LICENSE_NOTICE,
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


def _is_environment_file(name: str) -> bool:
    """A filled-in configuration file (`.env`, `.env.production`, `runtime.env`, `smtp.env`) holds an
    operator's secrets and is never source, wherever it sits — the archive is anonymous. Only the
    tracked templates (`*.example`) are offered."""
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


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
                    or f.suffix in EXCLUDE_SUFFIXES
                    or _is_environment_file(f.name)):
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
                      "账户服务、部署组装（infra/server）、镜像与生产部署的构建和运行文件（infra/Dockerfile、"
                      "infra/docker-compose.yml、infra/production、.github/workflows、.dockerignore、.env.example）、"
                      "脚本、测试与测试配置、文档、依赖版本清单，以及 LICENSE、NOTICE 与 TRADEMARKS.md。"]
        if not status["complete"]:
            lines += ["⚠ 本包并不完整。以下内容在运行本服务的目录中不存在，因此未能包含：", ""]
            lines += [f"  - {rel}" for rel in status["missing"]]
            lines += ["", "这说明构建运行目录（容器镜像）时漏掉了它们。完整源代码请见项目仓库，",
                      "或用包含这些路径的镜像重新构建（见 infra/Dockerfile 中的 COPY 行）。"]
        if big:
            lines += ["", "⚠ 以下文件单个超过 5 MB，未放入本包，请从项目仓库获取：", ""]
            lines += [f"  - {name}" for name in big]
        lines += ["", "未包含：用户数据、填写过的配置文件（.env、runtime.env 等，只含 *.example 模板）、虚拟环境与 node_modules、"
                      "编译产物，以及 apps/docs/tessdata 中的第三方 OCR 模型文件（tessdata_fast，Apache-2.0，未经修改，"
                      "来源见该目录的 README）。仓库中只供协作者与编码代理使用的工作规则（AGENTS.md、CLAUDE.md）"
                      "与版本控制设置不属于对应源代码。",
                  "第三方组件的源代码可从其官方发布获取（版本见各模块的 requirements.txt、infra/server/requirements.lock "
                  "与仓库根目录的 package-lock.json）。", ""]
        readme = "\n".join(lines).encode("utf-8")
        info = tarfile.TarInfo(f"{top}/SOURCE_OFFER.txt")
        info.size = len(readme)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(readme))
    return buf.getvalue(), f"{top}.tar.gz"


#: Archive downloads per minute per client address. The offer is anonymous and the archive is tens
#: of megabytes, so one address cannot turn it into a bandwidth pump; a person fetching the source
#: is never near this.
DOWNLOADS_PER_MIN = 6


def router():
    """``GET /api/about`` and ``GET /api/source-code``, for every role of the deployment.

    AGPL-3.0 §13 asks for the offer to be made to *all* users who interact with the program over a
    network. Served only by Synwevo Docs and only to signed-in members, it reached nobody who used
    just the sign-in page, the public site, the workspace or the conversations — the same process
    and the same program. So the composer (synwevo_server) mounts this router on every role, the
    request gate lets exactly these two paths through without a session
    (``synwevo_core.http.SOURCE_OFFER_PATHS``), and the page footers link to the archive. Neither
    handler reads anything a workspace owns.
    """
    from fastapi import APIRouter
    from fastapi.responses import Response
    from synwevo_core.errors import ServiceError
    from synwevo_core.http import RateBuckets, client_ip

    r = APIRouter()
    downloads = RateBuckets(DOWNLOADS_PER_MIN, name="source-code")

    @r.get("/api/about")
    def about_route():
        return about()

    @r.get("/api/source-code")
    def source_code(request: Request):
        if not source_offer_enabled():
            raise ServiceError(404, "source_offer_off", "本部署已关闭源代码下载（运营方以其他方式提供对应源代码，或运行未经修改、不含第三方 AGPL 组件的版本）")
        allowed, _left, wait = downloads.take(client_ip(request) or "unknown")
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, math.ceil(wait))})
        data, name = source_archive()
        return Response(data, media_type="application/gzip",
                        headers={"Content-Disposition": f"attachment; filename=\"{name}\""})

    return r
