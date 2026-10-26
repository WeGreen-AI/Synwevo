"""The account service on its own offers the program's source (AGPL-3.0 §13).

Every sign-in, sign-up and recovery page links to /api/source-code on the account origin. Run
from its own factory — no document engine mounted, and Synwevo Docs not importable by this
package — the account service answered that link with 404: only the composer and Synwevo Docs
mounted the offer. It serves it from synwevo_core now, without a session.
"""
from __future__ import annotations

import io
import tarfile

import pytest
from fastapi.testclient import TestClient

# TEST FIXTURE values: the commit and commit time an image build would have been stamped with.
FIXTURE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FIXTURE_COMMIT_TIME = 1767225600


def _client(tmp_path, monkeypatch, mode: str) -> TestClient:
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    monkeypatch.delenv("SYNWEVO_DOCS_SOURCE_OFFER", raising=False)
    from synwevo_identity.app import create_app
    return TestClient(create_app(str(tmp_path / "d"), start_jobs=False))


def _installation(root, monkeypatch, *, built: bool):
    """TEST FIXTURE: a two-file installation; `built` adds the release manifest an image build
    writes from its `git archive` context. Without it (and without a checkout) nothing says which
    files are the source. A backup left beside them afterwards is on no list."""
    from synwevo_core import source_manifest, source_offer
    (root / "services" / "identity").mkdir(parents=True)
    (root / "services" / "identity" / "x.py").write_text("# code\n", encoding="utf-8")
    (root / "LICENSE").write_text("licence text\n", encoding="utf-8")
    if built:
        source_manifest.write(root, commit=FIXTURE_COMMIT, commit_time=FIXTURE_COMMIT_TIME)
    (root / "services" / "identity" / "runtime.env.bak").write_text("SECRET=fixture\n", encoding="utf-8")
    monkeypatch.setattr(source_offer, "ROOT", root)


@pytest.mark.parametrize("mode", ["session", "basic"])
def test_the_account_service_alone_offers_the_source_without_a_sign_in(tmp_path, monkeypatch, mode):
    _installation(tmp_path / "installed", monkeypatch, built=True)
    c = _client(tmp_path, monkeypatch, mode)
    about = c.get("/api/about")
    assert about.status_code == 200, about.text
    body = about.json()
    assert body["license"] == "AGPL-3.0-or-later" and body["source_offer"] is True
    assert body["pdf_engine"] in ("pymupdf", "pdfium") and body["third_party"], body
    archive = c.get("/api/source-code")
    assert archive.status_code == 200, archive.text
    assert archive.headers["content-type"] == "application/gzip" and archive.headers["etag"]
    names = tarfile.open(fileobj=io.BytesIO(archive.content), mode="r:gz").getnames()
    top = f"synwevo-source-{FIXTURE_COMMIT[:12]}"
    assert f"{top}/services/identity/x.py" in names and f"{top}/SOURCE_OFFER.txt" in names
    assert not any(n.endswith("runtime.env.bak") for n in names), "清单之外的文件不进包"
    # exact paths: their neighbours stay behind the gate
    for path in ("/api/about/", "/api/source-code/x"):
        assert c.get(path).status_code in (401, 404), path


def test_without_a_release_list_the_account_service_refuses_rather_than_walks(tmp_path, monkeypatch):
    _installation(tmp_path / "bare", monkeypatch, built=False)
    c = _client(tmp_path, monkeypatch, "session")
    r = c.get("/api/source-code")
    assert r.status_code == 503 and r.json()["error"] == "source_unavailable", r.text
    assert "git archive" in r.json()["message"]
    assert c.get("/api/about").json()["source_offer"] is True


# Run in a fresh interpreter: the suites share one process (pyproject testpaths, Synwevo Docs'
# first), so by the time this file runs synwevo_docs is long in sys.modules and a before/after
# comparison here could never fail. The child reports what answering made it load.
ANSWER_CHILD = r"""
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from synwevo_core import source_offer
source_offer.ROOT = Path(sys.argv[1])
from synwevo_identity.app import create_app
c = TestClient(create_app(sys.argv[2], start_jobs=False))
about, archive = c.get("/api/about"), c.get("/api/source-code")
from synwevo_core.workers import stop_all
stop_all(wait=3.0)
print(json.dumps({"pdf_engine": about.json().get("pdf_engine"), "archive": archive.status_code,
                  "loaded": sorted(n for n in sys.modules if n.split(".")[0] in sys.argv[3:])}))
"""
# Synwevo Docs and the PDF libraries either of its engines would load.
NOT_LOADED = ("synwevo_docs", "pymupdf", "fitz", "pypdfium2")


def test_the_account_service_does_not_load_the_document_engine_to_answer(tmp_path, monkeypatch):
    """The statement names the PDF engine the deployment's settings select without importing
    Synwevo Docs or the PDF library: the account service starts and answers without either."""
    import json
    import os
    import subprocess
    import sys
    root = tmp_path / "installed"
    _installation(root, monkeypatch, built=True)
    child_env = {k: v for k, v in os.environ.items() if k not in ("SYNWEVO_ROLE", "SYNWEVO_DOCS_SOURCE_OFFER")}
    child_env.update(SYNWEVO_UI_AUTH="session", SYNWEVO_DATA_DIR=str(tmp_path / "d"),
                     SYNWEVO_DOCS_PDF_ENGINE="pdfium")
    run = subprocess.run([sys.executable, "-c", ANSWER_CHILD, str(root), str(tmp_path / "d"), *NOT_LOADED],
                         env=child_env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    report = json.loads(run.stdout.strip().splitlines()[-1])
    assert report["pdf_engine"] == "pdfium" and report["archive"] == 200, report
    assert report["loaded"] == [], f"账户服务为回答许可声明导入了 Synwevo Docs 或 PDF 库：{report['loaded']}"
