"""The server must not serve the UI to the network without deciding how it is protected.

Guards DW-047: check_listen_config() existed and was documented, but nothing called it —
`SYNWEVO_HOST=0.0.0.0` started and served with SYNWEVO_UI_AUTH unset (verified by
actually starting it before the fix).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from synwevo_core import auth

ROOT = __import__("synwevo_core.http", fromlist=["repo_root"]).repo_root()


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", ""])
def test_loopback_is_allowed_without_ui_auth(host, monkeypatch):
    monkeypatch.delenv("SYNWEVO_UI_AUTH", raising=False)
    monkeypatch.setattr(auth, "in_container", lambda: False)
    auth.check_listen_config(host)          # no exception


@pytest.mark.parametrize("mode", ["basic", "proxy"])
def test_public_bind_is_allowed_once_ui_auth_is_chosen(mode, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", mode)
    monkeypatch.setattr(auth, "in_container", lambda: False)
    auth.check_listen_config("0.0.0.0")      # no exception


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_public_bind_without_ui_auth_refuses_to_start(host, monkeypatch):
    monkeypatch.delenv("SYNWEVO_UI_AUTH", raising=False)
    monkeypatch.setattr(auth, "in_container", lambda: False)
    with pytest.raises(SystemExit) as e:
        auth.check_listen_config(host)
    msg = str(e.value)
    assert "拒绝启动" in msg and "SYNWEVO_UI_AUTH=basic" in msg and "SYNWEVO_UI_AUTH=proxy" in msg


def test_container_warns_instead_of_refusing(monkeypatch):
    """In a container the bind address says nothing about exposure – warn, do not block."""
    monkeypatch.delenv("SYNWEVO_UI_AUTH", raising=False)
    monkeypatch.setattr(auth, "in_container", lambda: True)
    said: list[str] = []
    auth.check_listen_config("0.0.0.0", warn=said.append)
    assert said and "端口映射" in said[0]


def test_in_container_env_flag(monkeypatch):
    monkeypatch.setenv("SYNWEVO_IN_CONTAINER", "1")
    assert auth.in_container()
    monkeypatch.setenv("SYNWEVO_IN_CONTAINER", "0")
    monkeypatch.setattr("os.path.exists", lambda p: True)
    assert not auth.in_container()          # explicit no overrides /.dockerenv too
    monkeypatch.setattr("os.path.exists", lambda p: False)
    assert not auth.in_container()


def test_run_py_actually_calls_the_guard(tmp_path):
    """The end-to-end guarantee: starting the documented way must refuse, not serve."""
    env = {"PATH": "/usr/bin:/bin", "SYNWEVO_HOST": "0.0.0.0", "SYNWEVO_PORT": "8791",
           "SYNWEVO_IN_CONTAINER": "0",  # exercise the strict host policy even inside Docker
           "SYNWEVO_DATA_DIR": str(tmp_path), "HOME": str(tmp_path)}
    r = subprocess.run([sys.executable, str(ROOT / "infra" / "server" / "synwevo_server" / "run.py")], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0, r.stdout[-2000:]
    assert "拒绝启动" in (r.stderr + r.stdout)
