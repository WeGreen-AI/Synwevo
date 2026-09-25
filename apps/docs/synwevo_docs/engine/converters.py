"""External converters run as isolated subprocesses with time limits.

* LibreOffice (``soffice --headless``) for legacy/ODF formats and PDF export.
  Every run uses a private user profile owned by this server user, seeded with macro
  execution disabled, active content (OLE, DDE) disabled, untrusted referer links
  blocked and link updates set to never.  The process gets a minimal environment (no
  server secrets), its own process group (the whole group is killed at the time limit —
  ``soffice`` is often a wrapper around ``soffice.bin``) and CPU / address-space limits.
  Network isolation itself is the deployment's job (a container without egress).
* Tesseract for OCR (see ocr.py).
"""
from __future__ import annotations

import atexit
import os

from synwevo_core import env
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .limits import LIMITS

_LO_LOCK = threading.Lock()
# one private profile per server process: LibreOffice profiles must not be shared by concurrent instances
# A LibreOffice user profile must not be shared by two concurrent soffice processes.
# _LO_LOCK only serialises threads INSIDE one process, so the directory is always made
# per-PID: SYNWEVO_DOCS_LO_PROFILE names the parent to put profiles under, not the profile
# itself (pointing several workers at one fixed path used to corrupt it).
_PROFILE_BASE = Path(env.docs("LO_PROFILE") or (Path(tempfile.gettempdir()) / f"synwevo-lo-{os.getuid()}"))
_PROFILE_DIR = _PROFILE_BASE / f"profile-{os.getpid()}"
_BASE_CHECKED = False


def _private_base() -> None:
    """The profile parent must be ours: a predictable path under a shared /tmp that another
    local user created first would otherwise become LibreOffice's user profile (fonts,
    settings, macros) for every conversion this server runs."""
    global _BASE_CHECKED
    if _BASE_CHECKED:
        return
    try:
        _PROFILE_BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = os.lstat(_PROFILE_BASE)
    except OSError as e:
        raise ConversionError(f"无法创建排版引擎配置目录：{e}") from e
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise ConversionError(f"排版引擎配置目录 {_PROFILE_BASE} 不属于当前用户（或是符号链接），拒绝使用；"
                              "请删除它，或用 SYNWEVO_DOCS_LO_PROFILE 指定一个私有目录")
    if st.st_mode & 0o077:
        os.chmod(_PROFILE_BASE, 0o700)
    # profiles left by earlier server processes (each holds copies of CJK fonts).  Only old
    # ones: a PID that looks dead here may be alive in another container sharing the directory.
    for old in _PROFILE_BASE.glob("profile-*"):
        try:
            pid = int(old.name.split("-", 1)[1])
            stale = time.time() - old.stat().st_mtime > 24 * 3600
        except (ValueError, OSError):
            continue
        if pid != os.getpid() and stale and not _alive(pid):
            shutil.rmtree(old, ignore_errors=True)
    _BASE_CHECKED = True


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@atexit.register
def _drop_profile():          # pragma: no cover - process exit
    shutil.rmtree(_PROFILE_DIR, ignore_errors=True)


# Seeded into the private profile before LibreOffice starts (and re-added if missing).
_REGISTRY_ITEMS = (
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableActiveContent", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),       # ScLkUpdMode: never
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "0"),     # never
)


def _seed_registry() -> None:
    user = _PROFILE_DIR / "user"
    user.mkdir(parents=True, exist_ok=True)
    reg = user / "registrymodifications.xcu"
    text = reg.read_text(encoding="utf-8") if reg.exists() else ""
    if not text.strip():
        text = ('<?xml version="1.0" encoding="UTF-8"?>\n<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
                'xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n</oor:items>\n')
    missing = []
    for path, name, value in _REGISTRY_ITEMS:
        item = (f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse"><value>{value}</value></prop></item>')
        if item not in text:
            missing.append(item)
    if missing and "</oor:items>" in text:
        text = text.replace("</oor:items>", "\n".join(missing) + "\n</oor:items>", 1)
        reg.write_text(text, encoding="utf-8")


def _child_env() -> dict:
    """What LibreOffice needs to run, and nothing else: the server's own environment holds
    secrets (SYNWEVO_SECRET, webhook secrets, database paths) a document must never see."""
    keep = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SYSTEMROOT", "FONTCONFIG_PATH", "FONTCONFIG_FILE")
    out = {k: os.environ[k] for k in keep if k in os.environ}
    out["HOME"] = str(_PROFILE_DIR)
    out["TMPDIR"] = str(_PROFILE_DIR / "tmp")
    out.setdefault("PATH", "/usr/bin:/bin")
    return out


def soffice_path() -> str | None:
    configured = env.docs("SOFFICE")
    if configured and Path(configured).exists():
        return configured
    for cand in ("soffice", "libreoffice"):
        p = shutil.which(cand)
        if p:
            return p
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    if Path(mac).exists():
        return mac
    return None


# CJK fonts offered to LibreOffice.  Headless LibreOffice does not always see the operating system's
# fonts (observed on macOS), and then lays Chinese text out with a Latin font that has no CJK glyphs:
# wrong line breaks, wrong page counts, invisible text in exported PDFs.  Fonts copied into the private
# profile's user/fonts directory are always loaded and used for glyph fallback.
CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Light.ttc", "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc", "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simsun.ttc",
)


def cjk_font_files() -> list[Path]:
    configured = env.docs("CJK_FONTS")
    cands = [p for p in (configured.split(os.pathsep) if configured else []) if p] + list(CJK_FONT_CANDIDATES)
    seen, out = set(), []
    for c in cands:
        p = Path(c)
        if p.exists() and p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out[:3]


def _ensure_profile_fonts() -> list[str]:
    """Copy CJK fonts into the private LibreOffice profile (once per profile)."""
    fd = _PROFILE_DIR / "user" / "fonts"
    marker = fd / ".synwevo-fonts"
    if marker.exists():
        return marker.read_text(encoding="utf-8").splitlines()
    fd.mkdir(parents=True, exist_ok=True)
    names = []
    for src in cjk_font_files():
        try:
            shutil.copyfile(src, fd / src.name)
            names.append(src.name)
        except OSError:
            continue
    if not names:
        # last resort: Droid Sans Fallback (Apache-2.0) that ships inside PyMuPDF
        try:
            import pymupdf
            (fd / "DroidSansFallback.ttf").write_bytes(pymupdf.Font("cjk").buffer)
            names.append("DroidSansFallback.ttf")
        except Exception:  # noqa: BLE001
            pass
    marker.write_text("\n".join(names), encoding="utf-8")
    return names


def soffice_version() -> str | None:
    exe = soffice_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else None
    except Exception:
        return None


# Limits are set by a tiny launcher that then execs LibreOffice: preexec_fn (the old way) runs
# Python code between fork and exec, which is not safe in a multi-threaded server.
_LAUNCHER = r"""
import os, resource, sys
cpu, mem = int(sys.argv[1]), int(sys.argv[2])
try:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
except (ValueError, OSError):
    pass
if mem > 0:
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except (ValueError, OSError):
        pass
os.execv(sys.argv[3], sys.argv[3:])
"""


def _limited(cmd: list[str]) -> list[str]:
    return [sys.executable, "-I", "-S", "-c", _LAUNCHER, str(LIMITS.converter_timeout_s + 30),
            str(LIMITS.converter_memory_bytes), *cmd]


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run in a new session and kill the whole process group at the time limit."""
    proc = subprocess.Popen(_limited(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_child_env(),
                            start_new_session=True, cwd=str(_PROFILE_DIR))
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise
    finally:
        if proc.poll() is None:
            _kill_group(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.communicate(timeout=5)
    except Exception:  # noqa: BLE001
        pass


class ConversionError(RuntimeError):
    pass


FILTERS = {
    "docx": "docx:MS Word 2007 XML",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "pptx": "pptx:Impress MS PowerPoint 2007 XML",
    "pdf": "pdf",
    "doc": "doc:MS Word 97",
    "xls": "xls:MS Excel 97",
    "odt": "odt", "ods": "ods", "odp": "odp", "rtf": "rtf",
}


def convert(data: bytes, src_ext: str, dst_ext: str, timeout: int | None = None) -> bytes:
    exe = soffice_path()
    if not exe:
        raise ConversionError("排版引擎未启用，无法进行此格式转换（部署说明见 docs/DEPLOY.md）")
    timeout = timeout or LIMITS.converter_timeout_s
    with _LO_LOCK:
        try:
            return _convert_once(exe, data, src_ext, dst_ext, timeout)
        except ConversionError as e:
            if "超时" in str(e):
                raise
            # a damaged/locked private profile makes soffice exit silently: reset it once and retry
            shutil.rmtree(_PROFILE_DIR, ignore_errors=True)
            return _convert_once(exe, data, src_ext, dst_ext, timeout)


def _convert_once(exe: str, data: bytes, src_ext: str, dst_ext: str, timeout: int) -> bytes:
    with tempfile.TemporaryDirectory(prefix="dwconv-") as tmp:
        src = Path(tmp) / f"input.{src_ext}"
        src.write_bytes(data)
        outdir = Path(tmp) / "out"
        outdir.mkdir()
        _private_base()
        _PROFILE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        (_PROFILE_DIR / "tmp").mkdir(exist_ok=True)
        _ensure_profile_fonts()
        _seed_registry()
        cmd = [exe, f"-env:UserInstallation={_PROFILE_DIR.as_uri()}", "--headless", "--norestore", "--nolockcheck",
               "--nodefault", "--nofirststartwizard", "--convert-to", FILTERS.get(dst_ext, dst_ext),
               "--outdir", str(outdir), str(src)]
        try:
            proc = _run(cmd, timeout)
        except subprocess.TimeoutExpired as e:
            raise ConversionError(f"格式转换超时（{timeout} 秒）") from e
        outs = list(outdir.glob(f"*.{dst_ext}"))
        if not outs:
            msg = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip()[-400:]
            raise ConversionError(f"排版引擎未生成 {dst_ext.upper()} 文件（退出码 {proc.returncode}）：{msg or '无输出'}")
        return outs[0].read_bytes()


def recalc_xlsx(data: bytes) -> bytes:
    """Round-trip through LibreOffice Calc so formula cached values are computed."""
    return convert(data, "xlsx", "xlsx")
