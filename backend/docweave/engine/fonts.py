"""Font availability check: fonts used by a template but not installed on the
server only matter for server-side PDF export (LibreOffice substitutes them);
the DOCX/XLSX itself is rendered by the user's own Office on their computer."""
from __future__ import annotations

import functools
import shutil
import subprocess

from .ooxml import NS, Package


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
