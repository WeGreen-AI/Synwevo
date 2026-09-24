"""Regression cases across workspaces (F098/F128/F219/F247/F293).

A captured case holds the workspace's own source files byte for byte. The routes used to be
global: a member of workspace B could list A's cases, download A's original files, replay them
and delete them. Driven here through real session cookies against Synwevo Docs' own app factory,
because the gate derives the workspace from the session — forcing a ContextVar would test
something the server never does.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions

from .helpers import Api

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def two(tmp_path, monkeypatch):
    """TEST FIXTURE: two accounts, each in its own personal workspace, each with a session."""
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    for name in ("SYNWEVO_DOCS_REGRESSION_DIR", "SYNWEVO_REGRESSION_DIR", "DOCWEAVE_REGRESSION_DIR"):
        monkeypatch.delenv(name, raising=False)
    from synwevo_docs.api.app import create_app
    api = create_app(str(tmp_path / "data"))
    store = api.state.app.store
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    sessions = Sessions(store)
    out = {"api": api, "store": store}
    for who in ("a", "b"):
        u = ident.create_user(f"{who}@example.com", PW, status="active")
        wid = ident.workspaces_for(u["id"])[0]["id"]
        raw, _ = sessions.create(u["id"], workspace_id=wid, amr="password")
        c = TestClient(api)
        c.cookies.set(COOKIE_NAME, raw)
        c.cookies.set(CSRF_COOKIE, "t")
        c.headers[CSRF_HEADER] = "t"
        out[who], out[f"ws_{who}"] = Api(c), wid
    yield out
    if api.state.jobs:
        api.state.jobs.stop()


def _project(api: Api, fixtures: Path) -> str:
    pid = api.project("回归用例（A 的机密项目）")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    t = api.template(pid, fixtures / "测试_订单模板.xlsx")
    s_order = api.slot(t["id"], kind="cell", sheet="订单", cell="B2", name="订单号")
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0}, "group": {"keys": [{"header": "订单号"}]}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}],
                    "target": {"binding": s_order["binding"]}}])
    return pid


def test_a_case_is_invisible_and_untouchable_from_another_workspace(two, fixtures):
    a, b = two["a"], two["b"]
    pid = _project(a, fixtures)
    case = a.ok(a.c.post(f"/api/projects/{pid}/regression-cases", json={"name": "A-case"}))
    cid = case["id"]
    assert [c["id"] for c in a.ok(a.c.get("/api/regression-cases"))] == [cid]

    # B sees nothing, and every id-addressed route answers "not found" (existence is not leaked)
    assert a.ok(b.c.get("/api/regression-cases")) == []
    assert b.c.get(f"/api/regression-cases/{cid}/download").status_code == 404
    assert b.c.post(f"/api/regression-cases/{cid}/run").status_code == 404
    assert b.c.delete(f"/api/regression-cases/{cid}").status_code == 404
    # B cannot capture from A's project either
    assert b.c.post(f"/api/projects/{pid}/regression-cases", json={"name": "偷"}).status_code == 404

    # A's case survived B's attempts and still replays and downloads for A
    assert a.ok(a.c.post(f"/api/regression-cases/{cid}/run"))["ok"]
    z = zipfile.ZipFile(io.BytesIO(a.ok_bytes(a.c.get(f"/api/regression-cases/{cid}/download"))))
    assert any(n.startswith(f"{cid}/sources/") for n in z.namelist())

    # stored under the data directory, in A's workspace directory — not in the source tree
    root = Path(two["store"].root) / "regression"
    assert (root / two["ws_a"] / cid / "case.json").is_file()
    assert not (root / two["ws_b"] / cid).exists()

    # B's own case does not show up for A
    pid_b = _project(b, fixtures)
    cb = b.ok(b.c.post(f"/api/projects/{pid_b}/regression-cases", json={"name": "B-case"}))
    assert [c["id"] for c in a.ok(a.c.get("/api/regression-cases"))] == [cid]
    assert [c["id"] for c in b.ok(b.c.get("/api/regression-cases"))] == [cb["id"]]
    assert a.c.delete(f"/api/regression-cases/{cb['id']}").status_code == 404
    assert a.ok(a.c.delete(f"/api/regression-cases/{cid}"))["ok"]
    assert a.ok(a.c.get("/api/regression-cases")) == [] and b.ok(b.c.get("/api/regression-cases"))


def test_a_tenant_value_cannot_climb_out_of_the_cases_root(tmp_path, monkeypatch):
    from synwevo_docs import regression as RG
    monkeypatch.setenv("SYNWEVO_DOCS_REGRESSION_DIR", str(tmp_path / "cases"))
    for evil in ("../../etc", "..", "a/b", ".hidden"):
        d = RG.cases_dir(evil, create=True)
        assert d.parent == tmp_path / "cases", (evil, d)
    with pytest.raises(RG.RegressionError):
        RG.load("../x", "ws_1")
