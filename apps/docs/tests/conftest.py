import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from synwevo_core.http import repo_root

ROOT = repo_root()
APP = Path(__file__).resolve().parents[1]          # apps/docs
DATA = ROOT / "testdata"
FIXTURE_MARKERS = ("测试_客户100.xlsx", "测试_发货单模板.pdf", "测试_发货单图片模板.png", "测试_订单段落模板.docx", "测试_多表单登记表.pdf")


GENERATOR = APP / "scripts" / "make_fixtures.py"
STAMP = ".generated-by"          # holds the SHA-256 of the generator that produced testdata/


def _generator_digest() -> str:
    import hashlib
    return hashlib.sha256(GENERATOR.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def fixtures() -> Path:
    """TEST-ONLY fixture files (generated, synthetic).

    Regenerated when any marker file is missing **or when the generator changed** since the
    files were made: checking only for existence meant a changed make_fixtures.py never reached
    an existing testdata/, and tests kept passing (or failing) against content the generator no
    longer produces. New files are generated into a scratch directory and moved in one file at a
    time with an atomic rename, so a concurrent test run never reads a half-written fixture.
    """
    import shutil
    import tempfile
    want = _generator_digest()
    stamp = DATA / STAMP
    fresh = stamp.exists() and stamp.read_text(encoding="utf-8").strip() == want
    if fresh and all((DATA / n).exists() for n in FIXTURE_MARKERS):
        return DATA
    DATA.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".testdata-", dir=DATA.parent))
    try:
        subprocess.run([sys.executable, str(GENERATOR), str(scratch)], check=True)
        for src in sorted(scratch.rglob("*")):
            if src.is_file():
                dst = DATA / src.relative_to(scratch)
                dst.parent.mkdir(parents=True, exist_ok=True)
                src.replace(dst)
        (DATA / STAMP).write_text(want + "\n", encoding="utf-8")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return DATA


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from synwevo_docs.api.app import create_app
    app = create_app(str(tmp_path / "data"))
    with TestClient(app) as c:
        c.app_state = app.state
        yield c
    if app.state.jobs:
        app.state.jobs.stop()


from .helpers import Api  # noqa: F401  (re-exported for the fixtures below)


@pytest.fixture()
def api(client):
    return Api(client)
