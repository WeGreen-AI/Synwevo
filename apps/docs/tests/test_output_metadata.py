"""A delivered PDF names the product in Producer/Creator and keeps the person's own metadata."""
from __future__ import annotations

import pytest

from synwevo_docs.engine import pdf_engine as pe


@pytest.mark.skipif(pe.ENGINE != "pymupdf", reason="basic-mode engine cannot rewrite the trailer")
def test_converted_pdf_names_the_product_and_keeps_the_authors_fields():
    import pymupdf
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "x")
    doc.set_metadata({"producer": "LibreOffice 26.8 (test fixture)", "creator": "Writer", "author": "客户 A", "title": "合同", "keywords": "k1"})
    data = doc.tobytes()
    out = pe.stamp_output_metadata(data)
    meta = pymupdf.open(stream=out, filetype="pdf").metadata
    assert meta["producer"] == "Synwevo Docs" and meta["creator"] == "Synwevo Docs"
    assert meta["author"] == "客户 A" and meta["title"] == "合同" and meta["keywords"] == "k1"
    assert pe.stamp_output_metadata(out) == out, "already stamped: returned unchanged"
    assert pe.stamp_output_metadata(b"not a pdf") == b"not a pdf"


def test_basic_mode_returns_the_bytes_untouched(monkeypatch):
    prev = pe.use_engine("pdfium")
    try:
        assert pe.stamp_output_metadata(b"%PDF-1.4 fixture") == b"%PDF-1.4 fixture"
    finally:
        pe.use_engine(prev)
