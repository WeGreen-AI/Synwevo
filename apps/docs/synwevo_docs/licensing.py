"""Licences of the running deployment and the Corresponding Source offer.

Synwevo itself is licensed AGPL-3.0-or-later (``LICENSE``, ``NOTICE``; the SDKs under
``apps/docs/sdk/`` are Apache-2.0), and every default build also installs PyMuPDF (AGPL-3.0).
When the service is used by others over a network, AGPL-3.0 §13 requires offering those users the
complete source of the running program. ``source_archive`` builds that archive from the files
actually installed (backend code, frontend source, scripts, docs, dependency pins, the licence
files) – not from a separate copy that could drift – and only from the files of the source release:
the list the image build wrote from its ``git archive`` context (``source_manifest``), or ``git
ls-files`` in a checkout. Without either it refuses; it never walks the directories, where anything
else an operator left there would go out with it. ``SYNWEVO_DOCS_SOURCE_OFFER=off`` switches the
download off; it is for an operator who offers the Corresponding Source another way, or who runs
an unmodified Synwevo without PyMuPDF (or with an Artifex commercial licence for it).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import math
import os
import stat
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from fastapi import Request   # module level: the route below is annotated with it (postponed annotations)

from synwevo_core import env
from synwevo_core.http import repo_root

from . import source_manifest

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
    ".gitattributes": "which evidence files `git archive` leaves out of a release, and which file it stamps with the commit",
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
    # xlrd's LICENSE has two parts: BSD-3-Clause for the bulk, and for some earlier work David
    # Giffin's 4-clause BSD-style licence, whose clause 4 asks every redistribution to keep an
    # acknowledgment (NOTICE carries it). Its metadata says only "BSD"; the file says more.
    ("xlrd", "BSD-3-Clause；早期部分代码为 David Giffin 的 BSD 式许可（含广告与致谢条款，见 NOTICE）", "XLS 读取"),
    ("olefile", "BSD-2-Clause", "旧格式检测"),
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


class SourceUnavailable(Exception):
    """This installation has no list of the release's files, so it builds no archive."""


UNAVAILABLE = ("本安装没有源代码发布清单：它既不是从 git archive 构建的镜像（构建时写入 SOURCE_MANIFEST.json），"
               "也不是 Git 检出（git ls-files）。分不清哪些文件属于源代码时，为免把备份、密钥或本地配置发给任何人，"
               "这里不提供下载。请从 git archive 构建镜像：git archive --format=tar HEAD | "
               "docker build -f infra/Dockerfile -t synwevo -（见 docs/operations.md 第 1 节）。")


def _is_environment_file(name: str) -> bool:
    """A filled-in configuration file (`.env`, `.env.production`, `runtime.env`, `smtp.env`) holds an
    operator's secrets and is never source, wherever it sits — the archive is anonymous. Only the
    tracked templates (`*.example`) are offered. Kept behind the release list, not instead of it."""
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _include_of(name: str) -> str | None:
    """The INCLUDE entry a release path falls under, or None when it is not offered."""
    parts = name.split("/")
    if not name or name.startswith("/") or ".." in parts or "" in parts:
        return None                                   # never trust a list entry to stay inside ROOT
    rel = next((r for r in INCLUDE if name == r or name.startswith(r + "/")), None)
    # Suffix match as well as exact: `synwevo_core.egg-info` is one path component, so a set
    # membership test never saw it and build metadata rode along.
    if (rel is None or set(parts) & EXCLUDE_PARTS or any(part.endswith(".egg-info") for part in parts)
            or PurePosixPath(name).suffix in EXCLUDE_SUFFIXES or _is_environment_file(parts[-1])):
        return None
    return rel


def _git_tracked(root: Path) -> list[str] | None:
    """`git ls-files` of a checkout, less what `git archive` would not export (``export-ignore``),
    so a checkout offers what an image built from it would. None when git cannot answer."""
    import shutil
    import subprocess
    git = shutil.which("git")
    if git is None:
        return None
    # The server's own environment must not point git at another repository or work tree.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        listed = subprocess.run([git, "-C", str(root), "ls-files", "-z", "--cached"], check=True,
                                capture_output=True, timeout=30, env=clean).stdout
        names = [n for n in listed.decode("utf-8", "surrogateescape").split("\0") if n]
        attrs = subprocess.run([git, "-C", str(root), "check-attr", "-z", "--stdin", "export-ignore"],
                               input="\0".join(names).encode("utf-8", "surrogateescape"), check=True,
                               capture_output=True, timeout=30, env=clean).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    fields = attrs.decode("utf-8", "surrogateescape").split("\0")
    ignored = {fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "set"}
    return [n for n in names if n not in ignored] if names else None


def _release_list(root: Path) -> tuple[list[str], str, str | None, int | None]:
    """(paths, where the list came from, commit, commit time) of the files that are the release.

    A checkout answers with git; the image with the manifest its build wrote from the build
    context (``source_manifest``). Neither: refuse. The archive used to walk INCLUDE and leave out
    a list of names, so any other file under an offered directory — runtime.env.bak, a swap file,
    origin.key, .pgpass — went to anyone who asked.
    """
    if (root / ".git").exists():
        names = _git_tracked(root)
        if names is not None:
            return names, "git", None, None
    manifest = source_manifest.read(root)
    if manifest is not None:
        return manifest["files"], "manifest", manifest["commit"], manifest["commit_time"]
    raise SourceUnavailable(UNAVAILABLE)


@dataclass(frozen=True)
class _Listing:
    source: str                                   # "manifest" (an image) or "git" (a checkout)
    commit: str | None
    commit_time: int | None
    files: tuple[tuple[Path, str, bool], ...]     # (path, archive name, executable), sorted, ≤ MAX_FILE
    oversized: tuple[str, ...]
    missing: tuple[str, ...]                      # INCLUDE entries the release holds nothing of
    fingerprint: str


def _make_listing(root: Path) -> _Listing:
    names, source, commit, commit_time = _release_list(root)
    files, big, present = [], [], set()
    digest = hashlib.sha256(f"{root}\0{source}\0{commit}\0{commit_time}\0{MAX_FILE}\n".encode())
    for name in sorted(set(names)):
        rel = _include_of(name)
        if rel is None:
            continue
        path = root / name
        try:
            st = os.lstat(path)
        except OSError:
            continue                                  # listed, but not here (deleted in a checkout)
        if not stat.S_ISREG(st.st_mode):
            continue                                  # never a link out of the release
        executable = bool(st.st_mode & 0o111)
        digest.update(f"{name}\0{st.st_size}\0{st.st_mtime_ns}\0{executable}\n".encode())
        present.add(rel)
        if st.st_size > MAX_FILE:
            big.append(name)
        else:
            files.append((path, name, executable))
    return _Listing(source, commit, commit_time, tuple(files), tuple(big),
                    tuple(rel for rel in INCLUDE if rel not in present), digest.hexdigest())


#: How long a checkout's list is trusted before git is asked again. The image's list is never
#: re-read: it was written at build time and the production root filesystem is read-only.
CHECKOUT_TTL_S = 30.0
_now = time.monotonic
_LOCK = threading.Lock()
_CACHE: dict = {}


def _current_listing() -> _Listing:
    """The release list, computed once per process for an image and once per CHECKOUT_TTL_S for a
    checkout. Each request used to walk and stat the whole tree (~2,200 files, twice) only to find
    the archive already built: ~120 ms of CPU per anonymous request."""
    key = (str(ROOT), MAX_FILE, INCLUDE)
    with _LOCK:
        hit = _CACHE.get("listing")
        if hit is not None and hit[0] == key:
            _key, listing, at = hit
            if (listing is not None and listing.source == "manifest") or _now() - at < CHECKOUT_TTL_S:
                if listing is None:
                    raise SourceUnavailable(UNAVAILABLE)
                return listing
        try:
            listing = _make_listing(Path(ROOT))
        except SourceUnavailable:
            _CACHE["listing"] = (key, None, _now())
            raise
        _CACHE["listing"] = (key, listing, _now())
        return listing


def source_status() -> dict:
    """Which of the promised paths the release actually holds where the service is running.

    The container image once COPYed only ``backend/`` and the *compiled* ``frontend/dist``, so on a
    real deployment most of INCLUDE was absent — and the builder skipped every missing path in
    silence while the bundled README went on claiming the archive held "后端、前端源码、脚本、
    文档与依赖版本清单". An incomplete Corresponding Source that says it is complete is the failure
    mode AGPL-3.0 §13 is about, so the gap is reported instead of hidden — and an installation with
    no release list at all says so rather than guessing.
    """
    try:
        listing = _current_listing()
    except SourceUnavailable as refused:
        return {"available": False, "reason": str(refused), "missing": list(INCLUDE), "complete": False}
    return {"available": True, "source": listing.source, "commit": listing.commit,
            "missing": list(listing.missing), "complete": not listing.missing}


def oversized() -> list[str]:
    """Source files left out of the archive for size. Named in SOURCE_OFFER.txt, never dropped quietly."""
    return list(_current_listing().oversized)


def _archive_files() -> list[tuple[Path, str]]:
    """(path, archive-relative name) of every file the Corresponding Source contains."""
    return [(path, name) for path, name, _x in _current_listing().files]


class Archive(NamedTuple):
    data: bytes
    name: str                                     # the download's file name
    etag: str                                     # strong: the SHA-256 of ``data``, quoted


def current_archive() -> Archive:
    """The archive of the current release list: built once per list, then the same object."""
    listing = _current_listing()
    with _LOCK:
        hit = _CACHE.get("archive")
        if hit is not None and hit[0] == listing.fingerprint:
            return hit[1]
        archive = _build_archive(listing)
        _CACHE["archive"] = (listing.fingerprint, archive)
        return archive


def source_archive() -> tuple[bytes, str]:
    archive = current_archive()
    return archive.data, archive.name


#: Members' time when the release has no commit time (a checkout): fixed, so the same files give
#: the same bytes. 2026-01-01T00:00:00Z.
CHECKOUT_MTIME = 1767225600


def _member(name: str, size: int, mode: int, mtime: int) -> tarfile.TarInfo:
    """A member that says nothing about the server: owner 0/0 with no names (they used to be the
    account that owns the files — on a checkout deployment, the operator's login name), a git-style
    mode, and the release's time rather than the files' own."""
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, mode, mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _build_archive(listing: _Listing) -> Archive:
    tag = listing.commit[:12] if listing.commit else ""
    top = f"synwevo-source-{tag}" if tag else "synwevo-source"
    mtime = listing.commit_time if listing.commit_time is not None else CHECKOUT_MTIME
    raw = io.BytesIO()
    # gzip without a name or a timestamp in its header, PAX tar, sorted members: reproducible, so
    # every process and every role serving the same release answers with the same bytes and ETag.
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, name, executable in listing.files:
            try:
                data = path.read_bytes()
            except OSError:
                continue                              # removed from a checkout since it was listed
            tar.addfile(_member(f"{top}/{name}", len(data), 0o755 if executable else 0o644, mtime), io.BytesIO(data))
        # Where it was generated is described, not spelled out: the absolute path of the
        # installation is nobody's business outside the server and was handed to every user.
        lines = ["Synwevo 对应源代码（Corresponding Source）", ""]
        if listing.source == "manifest":
            lines += [f"本压缩包由正在运行的服务生成，是提交 {listing.commit} 的发布：文件清单是构建镜像时从构建上下文"
                      "（该提交的 git archive）记录的 SOURCE_MANIFEST.json，文件取自运行目录。"]
        else:
            lines += ["本压缩包由正在运行的服务生成：文件清单是运行目录（Git 检出）的 git ls-files（git archive 不导出的文件除外），"
                      "文件取自运行目录，包括尚未提交的修改。"]
        lines += ["清单之外的文件——未受版本控制的本地文件、备份、编辑器临时文件、密钥——一律不进包。"
                  "成员的属主、权限与时间已统一（uid/gid 0、无用户名、0644/0755、发布时间），同一发布生成的包逐字节相同。",
                  "许可：Synwevo 以 AGPL-3.0-or-later 许可（见 LICENSE 与 NOTICE）；apps/docs/sdk/ 下的 SDK 为 Apache-2.0；"
                  "Synwevo 名称、Logo 与图标不在许可之内（见 TRADEMARKS.md）。", ""]
        if not listing.missing and not listing.oversized:
            lines += ["本包包含所列全部内容：Synwevo Docs、门户、Message、业务应用与个人 Agent（apps/suite）、平台共享包与前端包、"
                      "账户服务、部署组装（infra/server）、镜像与生产部署的构建和运行文件（infra/Dockerfile、"
                      "infra/docker-compose.yml、infra/production、.github/workflows、.dockerignore、.env.example）、"
                      "脚本、测试与测试配置、文档、依赖版本清单，以及 LICENSE、NOTICE 与 TRADEMARKS.md。"]
        if listing.missing:
            lines += ["⚠ 本包并不完整。以下内容不在本安装的发布清单里（或其文件在运行目录中不存在），因此未能包含：", ""]
            lines += [f"  - {rel}" for rel in listing.missing]
            lines += ["", "这说明构建运行目录（容器镜像）时漏掉了它们。完整源代码请见项目仓库，",
                      "或用包含这些路径的镜像重新构建（见 infra/Dockerfile 中的 COPY 行）。"]
        if listing.oversized:
            lines += ["", "⚠ 以下文件单个超过 5 MB，未放入本包，请从项目仓库获取：", ""]
            lines += [f"  - {name}" for name in listing.oversized]
        lines += ["", "未包含：用户数据、填写过的配置文件（.env、runtime.env 等，只含 *.example 模板）、虚拟环境与 node_modules、"
                      "编译产物，以及 apps/docs/tessdata 中的第三方 OCR 模型文件（tessdata_fast，Apache-2.0，未经修改，"
                      "来源见该目录的 README）。仓库中只供协作者与编码代理使用的工作规则（AGENTS.md、CLAUDE.md）"
                      "与版本控制设置不属于对应源代码。",
                  "第三方组件的源代码可从其官方发布获取（版本见各模块的 requirements.txt、infra/server/requirements.lock "
                  "与仓库根目录的 package-lock.json）。", ""]
        readme = "\n".join(lines).encode("utf-8")
        tar.addfile(_member(f"{top}/SOURCE_OFFER.txt", len(readme), 0o644, mtime), io.BytesIO(readme))
    data = raw.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    name = f"{top}.tar.gz" if tag else f"synwevo-source-{digest[:12]}.tar.gz"
    return Archive(data, name, f'"{digest}"')


#: Archive downloads per minute per client — an IPv4 address, or an IPv6 /64
#: (``synwevo_core.http.address_bucket``). The offer is anonymous and the archive is tens of
#: megabytes, so one client cannot turn it into a bandwidth pump; a person fetching the source is
#: never near this. A revalidation answered 304 is not a download and is not counted.
DOWNLOADS_PER_MIN = 6
#: The archive changes only with a deployment, so an edge cache may keep it — for ten minutes, after
#: which it revalidates (a 304 while the ETag still matches). A deployment's new source is
#: therefore served everywhere within ten minutes. Error responses stay ``no-store`` (the gate).
ARCHIVE_CACHE_CONTROL = "public, max-age=600"


def _etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match, with the weak comparison RFC 9110 §13.1.2 prescribes for it."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    return any(tag.strip().removeprefix("W/") == etag for tag in header.split(","))


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
    from synwevo_core.http import RateBuckets, address_bucket, client_ip

    r = APIRouter()
    downloads = RateBuckets(DOWNLOADS_PER_MIN, name="source-code")

    @r.get("/api/about")
    def about_route():
        return about()

    @r.get("/api/source-code")
    def source_code(request: Request):
        if not source_offer_enabled():
            raise ServiceError(404, "source_offer_off", "本部署已关闭源代码下载（运营方以其他方式提供对应源代码，或运行未经修改、不含第三方 AGPL 组件的版本）")
        try:
            archive = current_archive()
        except SourceUnavailable as refused:
            raise ServiceError(503, "source_unavailable", str(refused)) from None
        cache = {"ETag": archive.etag, "Cache-Control": ARCHIVE_CACHE_CONTROL}
        if _etag_matches(request.headers.get("if-none-match"), archive.etag):
            return Response(status_code=304, headers=cache)
        allowed, _left, wait = downloads.take(address_bucket(client_ip(request) or "unknown"))
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, math.ceil(wait))})
        return Response(archive.data, media_type="application/gzip",
                        headers={**cache, "Content-Disposition": f"attachment; filename=\"{archive.name}\""})

    return r
