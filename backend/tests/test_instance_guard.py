"""A data directory that already holds real projects can never be re-labelled as a test
instance, so destructive test tooling cannot be pointed at the user's data.

Background: the browser/headless scripts create projects, templates and jobs.  Run against
the everyday server they polluted the user's real project list.  scripts/_testenv.py now
refuses any server whose /healthz does not report instance=test; this file locks the
server-side half of that contract.
"""
from __future__ import annotations

import pytest

from docweave.store import Store


def _store(tmp_path, name="d"):
    return Store(tmp_path / name)


def test_fresh_dir_without_the_env_var_is_normal(tmp_path, monkeypatch):
    monkeypatch.delenv("DOCWEAVE_TEST_INSTANCE", raising=False)
    assert _store(tmp_path).instance_kind() == "normal"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_fresh_dir_with_the_env_var_is_test(tmp_path, monkeypatch, val):
    monkeypatch.setenv("DOCWEAVE_TEST_INSTANCE", val)
    assert _store(tmp_path, val).instance_kind() == "test"


def test_dir_holding_real_projects_stays_normal(tmp_path, monkeypatch):
    """The important one: setting the env var later must NOT unlock someone's real data."""
    monkeypatch.delenv("DOCWEAVE_TEST_INSTANCE", raising=False)
    s = _store(tmp_path)
    s.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p_real','真实项目',1,1)")
    assert s.instance_kind() == "normal"

    monkeypatch.setenv("DOCWEAVE_TEST_INSTANCE", "1")
    assert Store(tmp_path / "d").instance_kind() == "normal", "已有真实项目的数据目录不能被重新标记为测试实例"


def test_kind_is_stable_across_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_TEST_INSTANCE", "1")
    assert _store(tmp_path).instance_kind() == "test"
    monkeypatch.delenv("DOCWEAVE_TEST_INSTANCE", raising=False)
    assert _store(tmp_path).instance_kind() == "test", "标记一旦写入就不应随环境变量变化"


def test_healthz_exposes_the_marker(api):
    body = api.ok(api.c.get("/healthz"))
    assert body["status"] == "ok" and "instance" in body
    assert body["instance"] in ("test", "normal")
