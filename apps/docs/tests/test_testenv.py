"""The isolated test-server harness the browser E2E scripts run on (apps/docs/scripts/_testenv.py).

Its default mode — no base URL — is the only way those scripts can run without writing into
someone's real instance. It was broken for a whole restructure (a module passed as the process
environment, an interpreter path that no longer existed) and nothing noticed, because nothing ran
it. This does: it starts a real server process on a free port and checks what it promises.
"""
from __future__ import annotations

import importlib.util
import json
import urllib.request

from synwevo_core.http import repo_root

SCRIPTS = repo_root() / "apps" / "docs" / "scripts"


def _testenv():
    spec = importlib.util.spec_from_file_location("_synwevo_testenv", SCRIPTS / "_testenv.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_default_mode_starts_an_isolated_marked_instance_and_removes_it(tmp_path, monkeypatch):
    # A hostile developer shell: session auth, a data directory, a webhook receiver. None of it
    # may reach the throwaway instance.
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "must-not-be-used"))
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://hooks.example.test/should-not-be-called")
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    # TEST DOUBLE: an unusable system proxy must not intercept loopback health checks.
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:1"})
    te = _testenv()
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    with te.isolated() as inst:
        assert inst.base.startswith("http://127.0.0.1:")
        port = int(inst.base.rsplit(":", 1)[1])
        assert port not in (8765, 8769, 8770), "隔离实例必须用空闲端口，不能撞上日常服务"
        assert te.health(inst.base) == {"status": "ok", "instance": "test"}
        # UI auth is off in the child although the parent shell said "session"
        with direct.open(inst.base + "/api/projects", timeout=10) as r:
            assert json.loads(r.read()) == []
        req = urllib.request.Request(inst.base + "/api/projects", method="POST",
                                     data=json.dumps({"name": "E2E 夹具项目"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with direct.open(req, timeout=10) as r:
            assert r.status < 400
        assert (inst.data_dir / "docweave.sqlite3").exists()
        assert not (tmp_path / "must-not-be-used").exists(), "父进程的 SYNWEVO_DATA_DIR 不能泄漏进隔离实例"
        data_dir = inst.data_dir
    assert not data_dir.exists(), "结束后临时数据目录要被清掉"


def test_an_explicit_base_url_is_refused_unless_it_is_a_marked_test_instance(monkeypatch):
    import pytest
    te = _testenv()
    monkeypatch.delenv("SYNWEVO_DOCS_ALLOW_LIVE_TESTS", raising=False)
    # TEST DOUBLE: stands in for the /healthz body of an unmarked (normal) and a marked instance.
    monkeypatch.setattr(te, "health", lambda base, timeout=3.0: {"status": "ok"})
    with pytest.raises(te.NotATestInstance):
        te.require_test_instance("http://127.0.0.1:1")
    monkeypatch.setattr(te, "health", lambda base, timeout=3.0: {"status": "ok", "instance": "test"})
    assert te.require_test_instance("http://127.0.0.1:1") == "http://127.0.0.1:1"
