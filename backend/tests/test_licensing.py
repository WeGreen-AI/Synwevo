"""Licensing claims must match what the deployment actually ships.

Three statements get conflated routinely — "does not use PyMuPDF", "does not contain MuPDF"
and "has no AGPL dependency" — and the service used to report only the first while wording it
as the third: with DOCWEAVE_PDF_ENGINE=pdfium and PyMuPDF still installed, /api/about said
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

from docweave import licensing

ROOT = Path(__file__).resolve().parents[2]


def test_agpl_obligation_follows_the_binary_not_the_engine_setting(monkeypatch):
    """Selecting pdfium changes which engine runs; it removes nothing from the image."""
    from docweave.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] is False       # the engine really is pdfium
    assert a["pymupdf_installed"] is True
    assert a["agpl_component_active"] is True, "装着 AGPL 组件就不能说没有 AGPL 依赖"
    assert "仍然安装了 PyMuPDF" in a["notice"]
    assert "未加载 AGPL 组件" not in a["notice"]


def test_no_agpl_claim_only_when_pymupdf_is_absent(monkeypatch):
    from docweave.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pdfium")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: False)
    a = licensing.about()
    assert a["agpl_component_active"] is False
    assert "无 AGPL 依赖" in a["notice"] and "不含 MuPDF" in a["notice"]


def test_pymupdf_engine_keeps_the_section_13_offer(monkeypatch):
    from docweave.engine import pdf_engine
    monkeypatch.setattr(pdf_engine, "ENGINE", "pymupdf")
    monkeypatch.setattr(licensing, "pymupdf_installed", lambda: True)
    a = licensing.about()
    assert a["pymupdf_used_by_engine"] and a["agpl_component_active"]
    assert "第 13 条" in a["notice"]


def test_archive_says_so_when_it_is_incomplete(monkeypatch, tmp_path):
    """Simulate the container layout: backend present, everything else absent."""
    (tmp_path / "backend" / "docweave").mkdir(parents=True)
    (tmp_path / "backend" / "docweave" / "x.py").write_text("# code\n", encoding="utf-8")
    monkeypatch.setattr(licensing, "ROOT", tmp_path)

    status = licensing.source_status()
    assert not status["complete"]
    assert "frontend/src" in status["missing"] and "docs" in status["missing"]

    blob, name = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert "并不完整" in offer, "缺内容时必须说出来，不能继续声称完整"
    assert "frontend/src" in offer, "必须逐条列出缺了什么"
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


def test_docker_image_carries_every_path_the_offer_promises():
    """The archive is built from the files on disk, so the image must contain them.

    The deployed container served a backend-only archive because the Dockerfile COPYed only
    backend/ and the compiled frontend/dist.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = "\n".join(l for l in dockerfile.splitlines() if l.startswith("COPY"))
    for rel in ("frontend/src", "scripts", "docs", "README.md", "NOTICE"):
        assert rel in copied, f"Dockerfile 没有把 {rel} 放进镜像，对应源代码包会缺这一块"


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
