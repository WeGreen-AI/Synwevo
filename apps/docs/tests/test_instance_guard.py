"""A data directory that already holds real projects can never be re-labelled as a test
instance, so destructive test tooling cannot be pointed at the user's data.

Background: the browser/headless scripts create projects, templates and jobs.  Run against
the everyday server they polluted the user's real project list.  scripts/_testenv.py now
refuses any server whose /healthz does not report instance=test; this file locks the
server-side half of that contract.
"""
from __future__ import annotations

import pytest

from synwevo_core.store import Store


def _store(tmp_path, name="d"):
    return Store(tmp_path / name)


def test_fresh_dir_without_the_env_var_is_normal(tmp_path, monkeypatch):
    monkeypatch.delenv("SYNWEVO_TEST_INSTANCE", raising=False)
    assert _store(tmp_path).instance_kind() == "normal"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_fresh_dir_with_the_env_var_is_test(tmp_path, monkeypatch, val):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", val)
    assert _store(tmp_path, val).instance_kind() == "test"


def test_dir_holding_real_projects_stays_normal(tmp_path, monkeypatch):
    """The important one: setting the env var later must NOT unlock someone's real data."""
    monkeypatch.delenv("SYNWEVO_TEST_INSTANCE", raising=False)
    s = _store(tmp_path)
    s.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p_real','真实项目',1,1)")
    assert s.instance_kind() == "normal"

    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    assert Store(tmp_path / "d").instance_kind() == "normal", "已有真实项目的数据目录不能被重新标记为测试实例"


def test_kind_is_stable_across_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    assert _store(tmp_path).instance_kind() == "test"
    monkeypatch.delenv("SYNWEVO_TEST_INSTANCE", raising=False)
    assert _store(tmp_path).instance_kind() == "test", "标记一旦写入就不应随环境变量变化"


def test_healthz_marks_a_test_instance_and_stays_quiet_otherwise(tmp_path, monkeypatch):
    """The marker exists so destructive tooling can prove it is not pointed at real data.

    It is emitted **only** on a test instance. On a normal one the key is absent, and both call
    sites in scripts/_testenv.py compare `!= "test"` — so a missing key refuses, which is the
    direction that has to fail safe. The version string is gone entirely: this is the one
    anonymous endpoint, and an exact build string is what someone matching a service against a
    vulnerability list is looking for.
    """
    import importlib
    from fastapi.testclient import TestClient

    def body_for(marked: bool):
        d = tmp_path / ("t" if marked else "n")
        monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
        monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
        monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
        if marked:
            monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
        else:
            monkeypatch.delenv("SYNWEVO_TEST_INSTANCE", raising=False)
        import synwevo_docs.api.app as app_mod
        importlib.reload(app_mod)
        return TestClient(app_mod.create_app(str(d), start_jobs=False)).get("/healthz").json()

    test_body = body_for(True)
    assert test_body["status"] == "ok" and test_body["instance"] == "test"

    normal_body = body_for(False)
    assert normal_body == {"status": "ok"}, "正式实例不应泄露构建版本或实例类型"
    assert normal_body.get("instance") != "test", "缺少标记必须被判定为「不是测试实例」"


def test_readyz_neither_writes_a_row_nor_leaves_a_file_in_the_blob_tree(tmp_path, monkeypatch):
    """It used to INSERT a row per probe — an unauthenticated write anyone who could reach the
    port could drive — and to write its storage probe inside `blobs/`, where a crash between
    write and unlink leaves a non-blob file that a mark-and-sweep must special-case."""
    import importlib
    from fastapi.testclient import TestClient
    d = tmp_path / "r"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    import synwevo_docs.api.app as app_mod
    importlib.reload(app_mod)
    c = TestClient(app_mod.create_app(str(d), start_jobs=False))

    c.get("/readyz")
    wal = d / "docweave.sqlite3-wal"
    before = wal.stat().st_size if wal.exists() else 0
    for _ in range(50):
        c.get("/readyz")
    after = wal.stat().st_size if wal.exists() else 0
    assert after == before, f"50 次探针让 WAL 增长了 {after - before} 字节"

    from synwevo_core.store import Store
    assert Store(d).one("SELECT COUNT(*) AS n FROM meta WHERE key='readyz'")["n"] == 0
    assert not list((d / "blobs").glob(".*")), "探针文件不能留在内容寻址目录里"
    assert c.get("/readyz").json().keys() == {"status", "checks"}, "匿名响应不带诊断信息"
