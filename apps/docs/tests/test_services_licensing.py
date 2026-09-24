"""The Corresponding Source archive (F122): built once per state of the installed files, and
without the server's absolute installation path in it."""
from __future__ import annotations

import io
import os
import tarfile

from synwevo_docs import licensing


def _tree(root):
    """TEST FIXTURE: a minimal installation tree."""
    d = root / "apps" / "docs" / "synwevo_docs"
    d.mkdir(parents=True)
    (d / "x.py").write_text("# code\n", encoding="utf-8")
    return d / "x.py"


def test_the_archive_is_reused_until_a_file_changes(monkeypatch, tmp_path):
    f = _tree(tmp_path)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    built = []
    real = licensing._build_archive
    monkeypatch.setattr(licensing, "_build_archive", lambda files, stamp: built.append(1) or real(files, stamp))
    a, _ = licensing.source_archive()
    b, _ = licensing.source_archive()
    assert a is b and len(built) == 1                                     # not re-tarred per request
    f.write_text("# changed\n", encoding="utf-8")
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    c, _ = licensing.source_archive()
    assert len(built) == 2 and c != a


def test_the_offer_does_not_disclose_the_installation_path(monkeypatch, tmp_path):
    _tree(tmp_path)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    blob, _ = licensing.source_archive()
    tar = tarfile.open(fileobj=io.BytesIO(blob))
    offer = tar.extractfile([m for m in tar.getmembers() if m.name.endswith("SOURCE_OFFER.txt")][0]).read().decode()
    assert str(tmp_path) not in offer and "生成位置" not in offer
