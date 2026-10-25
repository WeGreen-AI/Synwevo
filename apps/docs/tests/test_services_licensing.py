"""The Corresponding Source archive (F122): built once per state of the installed files, and
without the server's absolute installation path in it."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile

import pytest

from synwevo_docs import licensing, source_manifest


def _tree(root):
    """TEST FIXTURE: a minimal installation tree, as an image build leaves it (with its manifest)."""
    d = root / "apps" / "docs" / "synwevo_docs"
    d.mkdir(parents=True)
    (d / "x.py").write_text("# code\n", encoding="utf-8")
    source_manifest.write(root, commit="0123456789abcdef0123456789abcdef01234567", commit_time=1767225600)
    return d / "x.py"


def test_the_image_archive_is_built_once(monkeypatch, tmp_path):
    """The image's files cannot change (read-only root filesystem): one build per process."""
    _tree(tmp_path)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    built = []
    real = licensing._build_archive
    monkeypatch.setattr(licensing, "_build_archive", lambda listing: built.append(1) or real(listing))
    a, _ = licensing.source_archive()
    b, _ = licensing.source_archive()
    assert a is b and len(built) == 1                                     # not re-tarred per request


def test_a_checkout_archive_is_reused_until_a_file_changes(monkeypatch, tmp_path):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    d = tmp_path / "apps" / "docs" / "synwevo_docs"
    d.mkdir(parents=True)
    f = d / "x.py"
    f.write_text("# code\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    monkeypatch.setattr(licensing, "ROOT", tmp_path)
    monkeypatch.setattr(licensing, "CHECKOUT_TTL_S", 0.0)                 # list again on every call
    built = []
    real = licensing._build_archive
    monkeypatch.setattr(licensing, "_build_archive", lambda listing: built.append(1) or real(listing))
    a, _ = licensing.source_archive()
    b, _ = licensing.source_archive()
    assert a is b and len(built) == 1                                     # unchanged: not re-tarred
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
