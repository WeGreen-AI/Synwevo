#!/usr/bin/env python3
"""Synwevo's own product surfaces name capabilities, not the components underneath them.

The user-visible text of the three shells, the shared front-end packages, the account service,
the Docs service and the core package must not name the third-party components they are built
on (the layout engine, the PDF engines, the OCR engine, the malware scanner, the human
verification vendor, the readers, the HTTP stack). "Visible" means: string literals in the
front-end message catalogues (`*messages*.ts`), natural-language string literals in the
front-end sources (`tr("…")`, `t("…")`, JSX text is always a literal too) and natural-language
string literals in the Python packages (error messages, notes, issue texts, capability rows, mail).

What this check deliberately leaves alone, because it is either not a product surface or is a
statement that must stay true:

* identifiers, configuration values, URLs, import paths, package names in requirements/locks;
* comments and docstrings (the code should keep saying what it really uses);
* the licence attribution page (`packages/core/synwevo_core/source_offer.py` and the shell strings that
  translate its rows): the AGPL/MPL/Apache obligations name the components, and pretending
  otherwise would be a false attribution;
* tests and documentation;
* external sign-in providers (Google, Microsoft, …): they are the identity the person is
  choosing, not a component of ours, so they are not in the list at all.

Every exception outside those rules is listed in ``STABLE`` with its reason, like
``check_brand.py``. Adding a name to a product string means renaming the capability instead
(see docs/platform/component-names.md for the vocabulary), not adding an exception.

    python scripts/check_component_names.py          # exit 1 on any unlisted match
    python scripts/check_component_names.py --list   # also print the listed exceptions
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Component names as they would appear in prose. Matched case-insensitively on word boundaries.
NAMES = [
    "LibreOffice", "soffice", "PyMuPDF", "MuPDF", "pdfium", "pypdfium2", "Tesseract", "Cloudflare",
    "Turnstile", "ClamAV", "clamd", "xlrd", "olefile", "openpyxl", "python-docx", "python-pptx", "Pillow",
    "uvicorn", "Starlette", "FastAPI", "SQLite", "Playwright", "pdfplumber", "pikepdf", "Ghostscript",
]
NAME_RE = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in NAMES) + r")(?![A-Za-z0-9_])", re.I)
CJK = re.compile(r"[㐀-鿿]")

FRONTEND_ROOTS = ["apps/docs/web/src", "apps/portal/web/src", "apps/messages/web/src", "apps/suite/web/src",
                  "services/identity/web/src",
                  "packages/auth-client/web/src", "packages/ui/src"]
PYTHON_ROOTS = ["apps/docs/synwevo_docs", "apps/portal/synwevo_portal", "apps/messages/synwevo_messages",
                "apps/suite/synwevo_suite",
                "services/identity/synwevo_identity",
                "packages/core/synwevo_core", "packages/auth-client/python/synwevo_auth_client", "infra/server/synwevo_server"]

# Files whose every literal is an attribution or an operator-facing licence statement.
ATTRIBUTION_FILES = {
    "packages/core/synwevo_core/source_offer.py": "关于与许可页：AGPL-3.0 §13 / MPL-2.0 / Apache-2.0 的归属声明必须写明组件名",
}

# Individual literals allowed with a reason. Keys are exact literals.
STABLE: dict[str, str] = {
    "引擎 Apache-2.0 / BSD-3-Clause；但本部署仍安装了 PyMuPDF（AGPL-3.0），义务仍适用":
        "能力矩阵 environment.pdf_engine_license 是许可声明（与关于与许可页一致），运营读取，外壳不显示",
}


def is_prose(s: str) -> bool:
    s = s.strip()
    if not s or s.startswith(("http://", "https://", "/", "#", "SYNWEVO_", "--")):
        return False
    return bool(CJK.search(s)) or len(s.split()) >= 3


def attribution_literals() -> set[str]:
    out: set[str] = set()
    for rel in ATTRIBUTION_FILES:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.add(node.value)
    return out


def scan_python(rel: str, attributions: set[str]) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    if rel in ATTRIBUTION_FILES:
        return hits
    src = (ROOT / rel).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [(rel, e.lineno or 0, f"cannot parse: {e}")]
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in doc_nodes:
            s = node.value
            if NAME_RE.search(s) and is_prose(s) and s not in attributions:
                hits.append((rel, node.lineno, s))
    return hits


TS_STRING = re.compile(r"""("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)""")
TS_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"'`])//[^\n]*", re.S)


def scan_ts(rel: str, attributions: set[str]) -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    src = (ROOT / rel).read_text(encoding="utf-8")
    # Strip comments while keeping line numbers.
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    code = TS_COMMENT.sub(blank, src)
    catalogue = "messages" in Path(rel).name
    for m in TS_STRING.finditer(code):
        raw = m.group(1)
        s = raw[1:-1]
        if not NAME_RE.search(s):
            continue
        if not (catalogue or is_prose(s)):
            continue
        if not is_prose(s) and catalogue and len(s.split()) < 2 and not CJK.search(s):
            continue                       # a bare identifier value such as "turnstile"
        if s in attributions:
            continue
        line = code.count("\n", 0, m.start()) + 1
        hits.append((rel, line, s))
    return hits


def catalogue_attribution_entries(attributions: set[str]) -> set[str]:
    """Translations of attribution rows live in the Docs shell's server catalogue; the whole
    entry (key and both translations) is attribution too."""
    out = set(attributions)
    rel = "apps/docs/web/src/messages.server.ts"
    src = (ROOT / rel).read_text(encoding="utf-8")
    for key in attributions:
        k = re.escape(key)
        for m in re.finditer(r"""["']""" + k + r"""["']\s*:\s*\{(.*?)\}""", src, re.S):
            for v in TS_STRING.findall(m.group(1)):
                out.add(v[1:-1])
    return out


def main() -> int:
    show_listed = "--list" in sys.argv
    attributions = catalogue_attribution_entries(attribution_literals())
    hits: list[tuple[str, int, str]] = []
    for root in PYTHON_ROOTS:
        for p in sorted((ROOT / root).rglob("*.py")):
            if "tests" in p.parts:
                continue
            hits += scan_python(str(p.relative_to(ROOT)), attributions)
    for root in FRONTEND_ROOTS:
        for p in sorted((ROOT / root).rglob("*.ts*")):
            if "tests" in p.parts or p.name.endswith(".d.ts") or ".test." in p.name:
                continue
            hits += scan_ts(str(p.relative_to(ROOT)), attributions)
    # A listed literal's translations in the Docs shell's server catalogue are listed with it.
    stable_all = catalogue_attribution_entries(set(STABLE))
    reason = {s: STABLE[k] for k in STABLE for s in catalogue_attribution_entries({k})}
    listed = [(r, l, s) for r, l, s in hits if s in stable_all]
    bad = [(r, l, s) for r, l, s in hits if s not in stable_all]
    if show_listed:
        for r, l, s in listed:
            print(f"listed  {r}:{l}: {s[:100]!r} — {reason[s]}")
    for r, l, s in bad:
        print(f"{r}:{l}: {s[:140]!r}")
    if bad:
        print(f"\n{len(bad)} product string(s) name an underlying component. Rename the capability "
              f"(docs/platform/component-names.md) or, for a required attribution, list it in STABLE with the reason.", file=sys.stderr)
        return 1
    print(f"component names: ok ({len(listed)} listed exception(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
