import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from platform_core.http import repo_root

ROOT = repo_root()
APP = Path(__file__).resolve().parents[1]          # apps/docweave
DATA = ROOT / "testdata"
FIXTURE_MARKERS = ("测试_客户100.xlsx", "测试_发货单模板.pdf", "测试_发货单图片模板.png", "测试_订单段落模板.docx", "测试_多表单登记表.pdf")


@pytest.fixture(scope="session")
def fixtures() -> Path:
    """TEST-ONLY fixture files (generated, synthetic)."""
    if not all((DATA / n).exists() for n in FIXTURE_MARKERS):
        subprocess.run([sys.executable, str(APP / "scripts" / "make_fixtures.py"), str(DATA)], check=True)
    return DATA


@pytest.fixture()
def client(tmp_path):
    from fastapi.testclient import TestClient

    from docweave.api.app import create_app
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
