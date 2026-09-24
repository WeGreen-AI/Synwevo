"""PDFium is not thread-safe and does not fail gracefully — it aborts the process.

DW-052: the AGPL-free (pdfium) engine made unserialised PDFium calls.  FastAPI runs sync
route handlers on a threadpool and the job runner is another thread, so concurrent PDF
reads are normal operation; before the fix, 200 mixed reads on 8 threads killed the server
with SIGBUS (exit 138) — no traceback, every in-flight request and job lost with it.

The repro runs in a SUBPROCESS on purpose: if the lock regresses, this test fails with a
signal instead of taking the whole pytest run down with it.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()

SCRIPT = textwrap.dedent("""
    import concurrent.futures as cf
    from synwevo_docs.engine import pdf_engine as pe
    pe.use_engine("pdfium")
    data = open({pdf!r}, "rb").read()

    def work(i):
        try:
            pe.page_words(data, i % 2)
            pe.pdf_text(data)
            return "ok"
        except BaseException as e:
            return f"{{type(e).__name__}}: {{e}}"

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(work, range({n})))
    bad = [r for r in res if r != "ok"]
    print(f"ok={{len(res) - len(bad)}}/{{len(res)}}")
    if bad:
        print("FAILURES:", bad[:3])
        sys.exit(2)
""")


def test_concurrent_pdfium_reads_do_not_crash_the_process():
    pdf = ROOT / "testdata" / "测试_多表单登记表.pdf"
    if not pdf.is_file():
        pytest.skip(f"缺少测试文件 {pdf}")
    src = SCRIPT.format(pdf=str(pdf), n=200)
    r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=300)

    assert r.returncode != -10 and r.returncode != 138, (
        f"PDFium 并发调用使进程崩溃（returncode={r.returncode}）——说明 pdfium_shim.LOCK 失效了。"
        f"\nstdout={r.stdout[-500:]}\nstderr={r.stderr[-500:]}")
    assert r.returncode == 0, f"并发读取失败：rc={r.returncode}\n{r.stdout[-800:]}\n{r.stderr[-800:]}"
    assert "ok=200/200" in r.stdout, r.stdout


def test_lock_is_reentrant_so_nested_page_access_cannot_deadlock():
    """Page work nests (a page is opened while a document-level call already holds it)."""
    from synwevo_docs.engine import pdfium_shim
    assert pdfium_shim.LOCK.__class__.__name__ == "RLock"
    with pdfium_shim.LOCK:
        with pdfium_shim.LOCK:
            pass
