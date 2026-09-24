"""Licensing claims must match what the deployment actually ships.

Three statements get conflated routinely — "does not use PyMuPDF", "does not contain MuPDF"
and "has no AGPL dependency" — and the service used to report only the first while wording it
as the third: with SYNWEVO_DOCS_PDF_ENGINE=pdfium and PyMuPDF still installed, /api/about said
「未加载 AGPL 组件」while the AGPL MuPDF binary was still in the image and two fallbacks still
called into it.

Separately, the Corresponding Source archive skipped every path missing from the running
directory in silence, so the deployed container served 90 entries of backend-only while the
bundled README claimed it also held the frontend, scripts and docs. That is precisely the
failure AGPL-3.0 §13 exists to prevent.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from synwevo_docs import licensing

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()


def test_agpl_obligation_follows_the_binary_not_the_engine_setting(monkeypatch):
    """Selecting pdfium changes which engine runs; it removes nothing from the image."""
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] is False       # the engine really is pdfium
    assert a["pymupdf_installed"] is True
    assert a["agpl_component_active"] is True, "装着 AGPL 组件就不能说没有 AGPL 依赖"
    assert "仍然安装了 PyMuPDF" in a["notice"]
    assert "未加载 AGPL 组件" not in a["notice"]


def test_no_agpl_claim_only_when_pymupdf_is_absent(monkeypatch):
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: False)
    a = licensing.about()
    assert a["agpl_component_active"] is False
    assert "无 AGPL 依赖" in a["notice"] and "不含 MuPDF" in a["notice"]


def test_pymupdf_engine_keeps_the_section_13_offer(monkeypatch):
    from synwevo_docs.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pymupdf")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] and a["agpl_component_active"]
    assert "第 13 条" in a["notice"]


def test_archive_says_so_when_it_is_incomplete(monkeypatch, tmp_path):
    """Simulate a partial image: the business package present, everything else absent."""
    (tmp_path / "apps" / "docs" / "synwevo_docs").mkdir(parents=True)
    (tmp_path / "apps" / "docs" / "synwevo_docs" / "x.py").write_text("# code\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", tmp_path)

    status = licensing.source_status()
    assert not status["complete"]
    # The shared packages and the account service are part of the running program after the
    # platform restructure, so their absence has to be reported like any other gap.
    assert "packages/core" in status["missing"] and "docs" in status["missing"]
    assert "services/identity" in status["missing"]

    blob, name = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert "并不完整" in offer, "缺内容时必须说出来，不能继续声称完整"
    assert "packages/core" in offer, "必须逐条列出缺了什么"
    assert "包含所列全部内容" not in offer


def test_archive_reports_complete_when_everything_is_present(monkeypatch, tmp_path):
    for rel in licensing.INCLUDE:
        p = tmp_path / rel
        if "." in Path(rel).name:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        else:
            p.mkdir(parents=True, exist_ok=True)
            (p / "f.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    assert licensing.source_status()["complete"]
    blob, _ = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert "包含所列全部内容" in offer and "并不完整" not in offer


def _dockerfile_copy_sources(text: str) -> list[str]:
    """The source paths of every COPY from the build context (not from another stage), exactly.

    Parsed, not substring-matched: `"docs/" in copied` was satisfied by `COPY apps/docs/ …`, so
    removing `COPY docs/ docs/` would not have failed the test that exists to catch it.
    """
    logical, buf = [], ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and buf:
            continue                                # comment inside a continued instruction
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        logical.append(buf + stripped)
        buf = ""
    sources = []
    for instr in logical:
        tokens = instr.split()
        if not tokens or tokens[0].upper() != "COPY":
            continue
        args = [t for t in tokens[1:] if not t.startswith("--")]
        if any(t.startswith("--from=") for t in tokens[1:]):
            continue                                # built output from another stage
        sources += [a.rstrip("/") for a in args[:-1]]
    return sources


def _covered(rel: str, sources: list[str]) -> bool:
    return any(rel == src or rel.startswith(src + "/") for src in sources)


def test_docker_image_carries_every_path_the_offer_promises():
    """The archive is built from the files on disk, so the image must contain them — every entry
    of licensing.INCLUDE, not a hand-picked list that can drift from it.

    The deployed container served a backend-only archive because the Dockerfile COPYed only
    backend/ and the compiled frontend/dist. An entry that does not exist in the checkout (LICENSE,
    until one is chosen) cannot be copied and is reported as missing by the offer itself.
    """
    sources = _dockerfile_copy_sources((ROOT / "infra" / "Dockerfile").read_text(encoding="utf-8"))
    assert sources, "没有解析出任何 COPY"
    for rel in licensing.INCLUDE:
        if not (ROOT / rel).exists():
            continue
        assert _covered(rel, sources), f"Dockerfile 没有把 {rel} 放进镜像，对应源代码包会缺这一块"


def test_the_copy_parser_is_not_fooled_by_a_similar_path():
    """The regression the parser exists for: apps/docs/ is not docs/."""
    sources = _dockerfile_copy_sources("COPY apps/docs/ apps/docs/\nCOPY --from=ui /ui/docs docs\n")
    assert _covered("apps/docs/synwevo_docs", sources)
    assert not _covered("docs", sources)


def test_notice_lists_every_third_party_component():
    """NOTICE is generated from THIRD_PARTY; this stops the two drifting apart."""
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    for name, lic, _role in licensing.THIRD_PARTY:
        assert name in notice, f"NOTICE 缺少组件 {name}"
        assert lic in notice, f"NOTICE 中 {name} 的许可与 THIRD_PARTY 不一致"
    assert "AGPL-3.0" in notice


@pytest.mark.skipif(not (ROOT / "LICENSE").exists(), reason="本项目尚未选定 LICENSE")
def test_license_file_is_shipped():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").strip()
