"""External converters run as isolated subprocesses with time limits.

* LibreOffice (``soffice --headless``) for legacy/ODF formats and PDF export.
  A private user profile is used; LibreOffice's default macro security does not
  run document macros during headless conversion.
* Tesseract for OCR (see ocr.py).
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .limits import LIMITS

_LO_LOCK = threading.Lock()
# one private profile per server process: LibreOffice profiles must not be shared by concurrent instances
# A LibreOffice user profile must not be shared by two concurrent soffice processes.
# _LO_LOCK only serialises threads INSIDE one process, so the directory is always made
# per-PID: DOCWEAVE_LO_PROFILE names the parent to put profiles under, not the profile
# itself (pointing several workers at one fixed path used to corrupt it).
_PROFILE_BASE = Path(os.environ.get("DOCWEAVE_LO_PROFILE") or (Path(tempfile.gettempdir()) / "docweave-lo"))
_PROFILE_DIR = _PROFILE_BASE / f"profile-{os.getpid()}"


def soffice_path() -> str | None:
    env = os.environ.get("DOCWEAVE_SOFFICE")
    if env and Path(env).exists():
        return env
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
    env = os.environ.get("DOCWEAVE_CJK_FONTS")
    cands = [p for p in (env.split(os.pathsep) if env else []) if p] + list(CJK_FONT_CANDIDATES)
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
    marker = fd / ".docweave-fonts"
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


def _limit_resources():  # pragma: no cover - runs in child
    try:
        cpu = LIMITS.converter_timeout_s + 30
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except Exception:
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
        raise ConversionError("未安装 LibreOffice，无法进行此格式转换（安装说明见 docs/DEPLOY.md）")
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
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _ensure_profile_fonts()
        cmd = [exe, f"-env:UserInstallation={_PROFILE_DIR.as_uri()}", "--headless", "--norestore", "--nolockcheck",
               "--nodefault", "--nofirststartwizard", "--convert-to", FILTERS.get(dst_ext, dst_ext),
               "--outdir", str(outdir), str(src)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, preexec_fn=_limit_resources)
        except subprocess.TimeoutExpired as e:
            raise ConversionError(f"LibreOffice 转换超时（{timeout} 秒）") from e
        outs = list(outdir.glob(f"*.{dst_ext}"))
        if not outs:
            msg = (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip()[-400:]
            raise ConversionError(f"LibreOffice 未生成 {dst_ext.upper()} 文件（退出码 {proc.returncode}）：{msg or '无输出'}")
        return outs[0].read_bytes()


def recalc_xlsx(data: bytes) -> bytes:
    """Round-trip through LibreOffice Calc so formula cached values are computed."""
    return convert(data, "xlsx", "xlsx")
