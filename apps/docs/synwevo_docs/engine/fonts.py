"""Fonts.

Two jobs:

* availability check — fonts used by a template but not installed on the server only
  matter for server-side PDF export (LibreOffice substitutes them); the DOCX/XLSX itself
  is rendered by the user's own Office on their computer.
* locating a real CJK font FILE.  Drawing Chinese with Pillow needs a path on disk, not a
  family name.  The only CJK font the product had was ``pymupdf.Font("cjk")``, embedded in
  the PyMuPDF binary — so without PyMuPDF there was no way to draw Chinese at all.
  ``cjk_font_file()`` resolves one from configuration, fontconfig or the platform's usual
  locations, and only accepts a candidate whose cmap really covers Chinese.
"""
from __future__ import annotations

import functools
import os

from synwevo_core import env
import shutil
import subprocess
from pathlib import Path

from .ooxml import NS, Package

# A font that merely exists is not enough: it must actually have the glyphs, or Chinese
# renders as tofu boxes.  These cover Simplified, Traditional, punctuation and digits.
_COVERAGE_PROBE = "中文测试字体汉字壹贰叁订单号金额（）：０"

# Usual locations per platform, tried when fontconfig is unavailable or unhelpful.
_WELL_KNOWN = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)


class NoCJKFont(RuntimeError):
    pass


def _covers_chinese(path: str, index: int = 0) -> bool:
    """True when the font file really maps the probe characters."""
    try:
        from fontTools.ttLib import TTCollection, TTFont

        def covers(font) -> bool:
            cmap = font.getBestCmap() or {}
            return all(ord(ch) in cmap for ch in _COVERAGE_PROBE)

        if path.lower().endswith(".ttc"):
            with TTCollection(path, lazy=True) as coll:
                fonts = [coll.fonts[index]] if index < len(coll.fonts) else list(coll.fonts)
                # Lazy cmap tables must be read before the collection closes its file.
                return any(covers(font) for font in fonts)
        with TTFont(path, lazy=True) as font:
            return covers(font)
    except Exception:  # noqa: BLE001 - an unreadable candidate is simply not usable
        return False
    return False


def _fc_match() -> list[str]:
    exe = shutil.which("fc-match")
    if not exe:
        return []
    out = []
    for lang in ("zh-cn", "zh-tw"):
        try:
            r = subprocess.run([exe, "-f", "%{file}", f":lang={lang}"], capture_output=True, text=True, timeout=20)
            if r.returncode == 0 and r.stdout.strip():
                out.append(r.stdout.strip())
        except Exception:  # noqa: BLE001
            continue
    return out


@functools.lru_cache(maxsize=1)
def cjk_font_file() -> str | None:
    """Path to a font file that can draw Chinese, or None.

    Order: explicit configuration, fontconfig, well-known platform paths.  Every candidate
    is checked for real glyph coverage before it is accepted.
    """
    configured = (env.docs("CJK_FONT_FILE") or "").strip()
    candidates = ([configured] if configured else []) + _fc_match() + list(_WELL_KNOWN)
    for c in candidates:
        if c and Path(c).is_file() and _covers_chinese(c):
            return c
    return None


def require_cjk_font() -> str:
    path = cjk_font_file()
    if not path:
        raise NoCJKFont(
            "找不到可用于绘制中文的字体文件。请安装中文字体（Debian/Ubuntu：apt install fonts-noto-cjk；"
            "镜像内已预装），或用 SYNWEVO_DOCS_CJK_FONT_FILE 指定一个 .ttf/.ttc/.otf 路径。"
            "注意：必须是字体**文件路径**，不是字体族名称。")
    return path


@functools.lru_cache(maxsize=1)
def installed_families() -> frozenset[str] | None:
    exe = shutil.which("fc-list")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, ":", "family"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    fams = set()
    for line in out.splitlines():
        for name in line.split(","):
            name = name.replace("\\-", "-").strip()
            if name:
                fams.add(name.lower())
    return frozenset(fams)


def docx_fonts(pkg: Package) -> set[str]:
    W = NS["w"]
    fonts: set[str] = set()
    for name in pkg.names:
        if not (name.startswith("word/") and name.endswith(".xml")) or "/_rels/" in name:
            continue
        if not any(k in name for k in ("document", "styles", "header", "footer", "numbering")):
            continue
        try:
            root = pkg.xml(name)
        except Exception:
            continue
        for el in root.iter("{%s}rFonts" % W):
            for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
                v = el.get("{%s}%s" % (W, attr))
                if v:
                    fonts.add(v)
    return fonts


def missing_fonts(fonts: set[str]) -> list[str] | None:
    have = installed_families()
    if have is None:
        return None
    return sorted(f for f in fonts if f.lower() not in have)
