"""Isolated server for the browser/headless test scripts.

Why: these scripts CREATE projects, templates and jobs.  Pointed at the everyday
server they write into the user's real project space — which is exactly what
happened before this module existed, leaving dozens of "UI验收…" projects behind.

Two guarantees:

* ``server()`` starts a throwaway server on a free port with its own data directory,
  marked ``instance_kind=test`` at creation.  Nothing it does can reach real data.
* ``require_test_instance(base_url)`` refuses to run against a server that does not
  report ``instance=test`` from /healthz.  Being on localhost, or on port 8765, is
  NOT accepted as proof — only the marker the server itself recorded when its data
  directory was created.

    from _testenv import server
    with server() as base:          # isolated; cleaned up on exit
        ...

    .venv/bin/python apps/docs/scripts/ui_e2e.py                        # isolated (default)
    .venv/bin/python apps/docs/scripts/ui_e2e.py http://127.0.0.1:8765  # refused unless marked test

The isolated server is `python -m synwevo_server.run` from the same interpreter that runs the
script (the workspace venv), started in its own temporary directory as working directory — so a
path that only resolves from the repository root fails here instead of passing by accident.
Its environment starts from the caller's with every Synwevo setting that could point it at real
data, a real identity provider or a real webhook receiver removed; only the settings that locate
local tools (LibreOffice, Tesseract, fonts, PDF engine) are kept.
"""
from __future__ import annotations

import json
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from synwevo_core import env
from synwevo_core.http import repo_root

APP = Path(__file__).resolve().parents[1]              # apps/docs
REPO = repo_root() or APP.parents[1]
# Fixtures and screenshots live at the repository root, whichever module's script uses them.
TESTDATA = REPO / "testdata"
SHOTS = REPO / "docs" / "screenshots"
PYTHON = sys.executable

# Suffixes (after SYNWEVO_DOCS_ / SYNWEVO_ / DOCWEAVE_) of settings the isolated server may
# inherit: where local tools are. Everything else Synwevo-prefixed is dropped, because a
# developer shell with SYNWEVO_UI_AUTH=session, an ISSUER, WEBHOOK_URLS or a SECRET would
# otherwise turn "isolated" into "talks to real things".
INHERITED = {"SOFFICE", "TESSERACT", "TESSDATA", "CJK_FONTS", "CJK_FONT_FILE", "LO_PROFILE",
             "PDF_ENGINE"}


class NotATestInstance(SystemExit):
    pass


def run_id() -> str:
    """Identifies everything one test run creates, so leftovers can be traced."""
    return f"t{uuid.uuid4().hex[:10]}"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def health(base_url: str, timeout: float = 3.0) -> dict:
    # macOS urllib also inherits system proxies, even with no proxy environment variables.
    # A throwaway loopback server must be checked directly; never change the user's proxy
    # configuration or install a process-global opener to make this one check work.
    host = urllib.parse.urlsplit(base_url).hostname or ""
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    opener = (urllib.request.build_opener(urllib.request.ProxyHandler({})) if loopback
              else urllib.request.build_opener())
    with opener.open(f"{base_url.rstrip('/')}/healthz", timeout=timeout) as r:
        return json.loads(r.read())


def require_test_instance(base_url: str) -> str:
    """Refuse to run destructive tooling against anything but a marked test instance."""
    if (env.docs("ALLOW_LIVE_TESTS") or "").strip().lower() in ("1", "true", "yes", "on"):
        print(f"!! SYNWEVO_DOCS_ALLOW_LIVE_TESTS is set — writing test data into {base_url}", file=sys.stderr)
        return base_url
    try:
        h = health(base_url)
    except (urllib.error.URLError, OSError) as e:
        raise NotATestInstance(f"无法连接 {base_url}：{e}") from e
    kind = h.get("instance")
    if kind != "test":
        raise NotATestInstance(
            f"拒绝在 {base_url} 上运行测试：该实例的数据目录标记为 {kind or '未知（旧版本或正式实例）'}，不是测试实例。\n"
            "这些脚本会创建项目、模板和任务，写进正式实例会污染用户的真实项目。\n"
            "请任选其一：\n"
            "  1) 不传 base_url，脚本会自动启动一个隔离实例（推荐）；\n"
            "  2) 用 SYNWEVO_DATA_DIR=<空目录> SYNWEVO_TEST_INSTANCE=1 另起一个服务，再指向它；\n"
            "  3) 确需写入该实例时显式设置 SYNWEVO_DOCS_ALLOW_LIVE_TESTS=1（会打印警告）。")
    return base_url


def _wait(base_url: str, proc: subprocess.Popen, log: Path, timeout: float = 60.0) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if proc.poll() is not None:
            raise SystemExit(f"测试服务启动失败（退出码 {proc.returncode}）：\n{log.read_text(errors='replace')[-2000:]}")
        try:
            if health(base_url, timeout=2.0).get("status") == "ok":
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise SystemExit(f"测试服务 {base_url} 启动超时：\n{log.read_text(errors='replace')[-2000:]}")


def _child_env(tmp: Path, port: int) -> dict:
    out = {}
    for k, v in os.environ.items():
        for prefix in ("SYNWEVO_DOCS_", "SYNWEVO_", "DOCWEAVE_"):
            if k.startswith(prefix):
                if k[len(prefix):] in INHERITED:
                    out[k] = v
                break
        else:
            out[k] = v
    out.update({
        "SYNWEVO_DATA_DIR": str(tmp / "data"),
        "SYNWEVO_PORT": str(port),
        "SYNWEVO_HOST": "127.0.0.1",
        "SYNWEVO_ROLE": "app",
        # Loopback only, so no UI authentication is the honest setting for a throwaway instance.
        "SYNWEVO_UI_AUTH": "none",
        "SYNWEVO_TEST_INSTANCE": "1",
        "SYNWEVO_DOCS_REGRESSION_DIR": str(tmp / "regression"),   # never touch the repo's cases
    })
    return out


class Instance(NamedTuple):
    base: str
    data_dir: Path


@contextmanager
def server(keep: bool = False):
    """A throwaway Synwevo Docs on a free port with its own data dir, marked as a test instance."""
    with isolated(keep=keep) as inst:
        yield inst.base


@contextmanager
def isolated(keep: bool = False):
    """Like server(), and also says where the throwaway data directory is — for tooling that
    has to prepare it out of band (an API key made with the CLI, for instance)."""
    tmp = Path(tempfile.mkdtemp(prefix="synwevo-test-"))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = tmp / "server.log"
    with log.open("wb") as fh:
        proc = subprocess.Popen([PYTHON, "-m", "synwevo_server.run"], cwd=str(tmp),
                                env=_child_env(tmp, port), stdout=fh, stderr=fh)
    try:
        _wait(base, proc, log)
        kind = health(base).get("instance")
        if kind != "test":
            raise SystemExit(f"隔离实例未被标记为 test（得到 {kind!r}），拒绝继续")
        print(f"[testenv] 隔离实例 {base}  数据目录 {tmp/'data'}")
        yield Instance(base, tmp / "data")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        if keep or (env.docs("KEEP_TEST_DATA") or "").lower() in ("1", "true", "yes", "on"):
            print(f"[testenv] 保留测试数据：{tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def target(argv_base: str | None, keep: bool = False):
    """Either the caller's explicit (test-marked) server, or a fresh isolated one."""
    if argv_base:
        yield require_test_instance(argv_base)
    else:
        with server(keep=keep) as base:
            yield base
