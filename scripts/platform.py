#!/usr/bin/env python3
"""Synwevo 根目录的运行入口：安装、启动、停止、状态、日志、验证。

    python scripts/platform.py setup                 # 建立 .venv、装依赖、构建四个前端
    python scripts/platform.py up                    # 启动全部四个服务，登记客户端，打印地址
    python scripts/platform.py up accounts           # 只启动其中一个（可多个：accounts portal docs messages）
    python scripts/platform.py down [accounts …]
    python scripts/platform.py restart [accounts …]
    python scripts/platform.py status
    python scripts/platform.py logs accounts|portal|docs|messages [-n 200]
    python scripts/platform.py test [docs|identity|messages|integration|all]
    python scripts/platform.py backup [--out DIR]    # 一致性备份（SQLite 备份 API + blob 清单）
    python scripts/platform.py verify-restore FILE   # 在隔离副本上核验备份能恢复
    python scripts/platform.py restore FILE --into DIR [--force]
                                                     # 恢复并补上备份之后的撤销（安全日志）

`setup` runs with any Python ≥ 3.12 on a clean checkout: it imports nothing from this repository,
because nothing from this repository is installed until it has run. Every other command needs
the workspace virtualenv and re-executes itself inside it when started with another interpreter.

Why four hostnames and not four ports: **different ports do not isolate cookies.** A cookie
set by `localhost:8770` is sent to `localhost:8765` as well, because the cookie protocol scopes
by host and ignores the port entirely. Running all four on `localhost` would therefore produce
a demonstration in which the account service's session cookie is handed to Synwevo Docs on every
request — and a "cross-origin single sign-on" that is really one shared cookie wearing four
hats. `*.localhost` names all resolve to the loopback address without any hosts-file edit (RFC
6761), so this gets genuinely separate cookie jars with no system configuration at all.

One database. The account service and Synwevo Docs are the same binary in two roles, and they share
the store, because there must be exactly one account authority — two databases would mean two
answers to "is this person suspended".

Local only. Nothing here touches DNS, opens a tunnel, or listens on anything but loopback.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / ".local" / "platform"
PY = ROOT / ".venv" / "bin" / "python"

# Overridable, because a developer very often already has one of these on 8765 and stopping
# someone else's running server to demonstrate a login flow is not an acceptable trade.
ACCOUNTS_PORT = int(os.environ.get("SYNWEVO_ACCOUNTS_PORT", "8770"))
PORTAL_PORT = int(os.environ.get("SYNWEVO_PORTAL_PORT", "8769"))
DOCS_PORT = int(os.environ.get("SYNWEVO_DOCS_PORT")
                or os.environ.get("PLATFORM_DOCWEAVE_PORT") or "8765")
ACCOUNTS = f"http://accounts.localhost:{ACCOUNTS_PORT}"
PORTAL = f"http://portal.localhost:{PORTAL_PORT}"
# The hostname stays `docweave.localhost`. It is a registered OIDC callback, a Passkey origin
# and a cookie scope — renaming it would invalidate every credential bound to it, in exchange
# for a tidier URL bar on one developer machine.
DOCS = f"http://docweave.localhost:{DOCS_PORT}"
# Synwevo Message (CI-66): conversations on their own origin. Production is message.synwevo.com
# by decision; locally `message.localhost`, singular like the product name.
MESSAGES_PORT = int(os.environ.get("SYNWEVO_MESSAGES_PORT", "8768"))
MESSAGES = f"http://message.localhost:{MESSAGES_PORT}"

SERVICES = {
    # name: (role, port, public URL, OIDC client id, app id)
    #
    # The portal's app id is `platform`, which is deliberately *not* a row in the application
    # registry: the registry lists business applications you can be granted or denied, and the
    # portal is where you go to see which of those you have.
    "accounts": ("accounts", ACCOUNTS_PORT, ACCOUNTS, None, None),
    "portal": ("portal", PORTAL_PORT, PORTAL, "portal-web", "platform"),
    "docs": ("app", DOCS_PORT, DOCS, "docweave-web", "docweave"),
    "messages": ("messages", MESSAGES_PORT, MESSAGES, "messages-web", "messages"),
}


def data_dir() -> str:
    """Where the one database lives, as an absolute path.

    Read through `synwevo_core.env` so a conflicting `SYNWEVO_DATA_DIR` / `DOCWEAVE_DATA_DIR`
    pair is refused rather than guessed. Imported here, not at the top of the file: `setup` must
    run before `synwevo_core` is installed anywhere. A relative value is taken relative to the
    repository root — the same rule `snapshot.py --data` uses — so the three services and the
    backup tool resolve one directory whichever directory they were started from.
    """
    from synwevo_core import env
    raw = env.get("DATA_DIR")
    if not raw:
        return str(ROOT / "data")
    p = Path(raw).expanduser()
    return str(p if p.is_absolute() else (ROOT / p).resolve())


def env_for(name: str) -> dict:
    role, port, url, client, app = SERVICES[name]
    child_env = dict(os.environ)
    if name != "accounts":
        # Only the identity service sends account mail; sibling applications never need
        # the mailbox password, even when the parent shell has loaded an SMTP env file.
        for key in list(child_env):
            if key.startswith(("SYNWEVO_SMTP_", "SYNWEVO_MAIL_", "DOCWEAVE_SMTP_", "DOCWEAVE_MAIL_")):
                del child_env[key]
    else:
        child_env.setdefault("SYNWEVO_PUBLIC_URL", url)
    child_env.update({
        "SYNWEVO_ROLE": role,
        "SYNWEVO_PORT": str(port),
        "SYNWEVO_HOST": bind_addr(urllib.parse.urlparse(url).hostname),
        "SYNWEVO_LOCAL_DUAL_STACK": "1",
        "SYNWEVO_DATA_DIR": data_dir(),
        "SYNWEVO_UI_AUTH": "session",
        "SYNWEVO_ISSUER": ACCOUNTS,
        "SYNWEVO_APP_URL": url,
        # Plain HTTP on loopback, so the Secure attribute would stop the cookie being stored at
        # all. This is the one local-only concession, and it is why these hostnames must never
        # be used for anything real.
        "SYNWEVO_COOKIE_SECURE": "0",
        # Where the account pages' "back" link goes. Server-controlled, never a query parameter.
        "SYNWEVO_PORTAL_URL": PORTAL,
    })
    if name == "portal":
        # The business applications on the workplace origin (synwevo_suite). A local instance
        # releases all of them unless the shell says otherwise (docs/agent-work/DECISIONS.md D4).
        child_env.setdefault("SYNWEVO_SUITE_APPS", "all")
    if client:
        child_env["SYNWEVO_OIDC_CLIENT_ID"] = client
    if app:
        child_env["SYNWEVO_OIDC_APP_ID"] = app
    return child_env


def bind_addr(host: str) -> str:
    """Preferred loopback for display and startup guards.

    The local runner binds both loopback families through LOCAL_DUAL_STACK. Browsers and
    server-side HTTP clients may resolve *.localhost differently; neither may lose SSO.
    This helper never selects a wildcard or changes a production bind address.
    """
    try:
        for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            addr = info[4][0]
            if addr in ("127.0.0.1", "::1"):
                return addr
    except socket.gaierror:
        pass
    return "127.0.0.1"


def _port_busy(port: int, host: str = "accounts.localhost") -> bool:
    for fam, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        if fam == socket.AF_INET6 and not socket.has_ipv6:
            continue
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex((addr, port)) == 0:
                return True
    return False


def _pidfile(name: str) -> Path:
    return RUN / f"{name}.pid"


def _is_ours(pid: int) -> bool:
    """Is this pid still the server we started, rather than whatever reused the number?

    A pid file outlives a crash or a reboot, and pids are reused. Signalling on the strength of
    "some process has this number" is how `down` would terminate an unrelated program, so the
    command line has to say it is `synwevo_server.run` before anything is sent to it.
    """
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        command = shlex.split(out.strip())
    except ValueError:
        return False
    if len(command) != 3 or command[1:] != ["-m", "synwevo_server.run"]:
        return False
    # macOS replaces the venv executable in ps output with the framework interpreter.
    # Check the working directory instead: up always sets cwd=ROOT, and a different
    # checkout running the same module must never be stopped through our stale pid file.
    try:
        if sys.platform.startswith("linux"):
            cwd = Path(f"/proc/{pid}/cwd").resolve(strict=True)
        else:
            result = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                    capture_output=True, text=True, timeout=5, check=True)
            paths = [line[1:] for line in result.stdout.splitlines() if line.startswith("n")]
            if len(paths) != 1:
                return False
            cwd = Path(paths[0]).resolve()
        return cwd == ROOT.resolve()
    except (OSError, subprocess.SubprocessError):
        return False


def _alive(name: str) -> int | None:
    f = _pidfile(name)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, ProcessLookupError, PermissionError):
        return None
    return pid if _is_ours(pid) else None


def check_hostnames() -> bool:
    """Confirm the four names resolve to loopback before claiming isolation.

    If a name resolved somewhere else, requests would leave this machine — so this is checked
    rather than assumed, and the answer is printed rather than swallowed.
    """
    ok = True
    for host in ("accounts.localhost", "portal.localhost", "docweave.localhost", "message.localhost"):
        try:
            addrs = {a[4][0] for a in socket.getaddrinfo(host, None)}
        except socket.gaierror as e:
            print(f"  ✗ {host}: 无法解析（{e}）")
            ok = False
            continue
        loop = all(a in ("127.0.0.1", "::1") for a in addrs)
        print(f"  {'✓' if loop else '✗'} {host} → {', '.join(sorted(addrs))}")
        ok = ok and loop
    return ok


def register_clients() -> bool:
    """Register the three relying parties. Idempotent, and by CLI on purpose — there is no
    dynamic client registration endpoint and no UI for this."""
    for client_id, app_id, name, base in (
            ("portal-web", "platform", "平台门户", PORTAL),
            ("docweave-web", "docweave", "Synwevo Docs", DOCS),
            ("messages-web", "messages", "Synwevo Message", MESSAGES)):
        cmd = [str(PY), "-m", "synwevo_identity.cli", "--data", data_dir(),
               "client", "register", "--client-id", client_id, "--app", app_id, "--name", name,
               "--redirect", f"{base}/auth/callback",
               "--post-logout", f"{base}/signed-out",
               "--backchannel", f"{base}/auth/backchannel-logout"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ✗ {client_id}: {p.stderr.strip()[:200]}")
            return False
        else:
            print(f"  ✓ {client_id} → {base}/auth/callback")
    return True


def _service(value: str) -> str:
    if value not in SERVICES:
        raise argparse.ArgumentTypeError(f"未知服务 {value!r}；可选：{', '.join(SERVICES)}")
    return value


def _selected(args) -> list[str]:
    names = list(dict.fromkeys(_service(n) for n in getattr(args, "services", []) or []))
    return names or list(SERVICES)


def _ready(name: str) -> bool:
    if not _alive(name):
        return False
    # Local readiness must not travel through an ambient HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"{SERVICES[name][2]}/readyz", timeout=1) as response:
            return response.status == 200 and json.load(response).get("status") == "ready"
    except (OSError, ValueError):
        return False


def up(args) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    if not PY.exists():
        print(f"没有 {PY}。先运行：python scripts/platform.py setup", file=sys.stderr)
        return 2
    _require_data_dir(args)
    print("检查本机域名解析：")
    if not check_hostnames():
        print("\n这些名字必须解析到回环地址，否则请求会离开本机。中止。", file=sys.stderr)
        return 2

    selected = _selected(args)
    occupied = [name for name in selected if not _alive(name)
                and _port_busy(SERVICES[name][1], urllib.parse.urlparse(SERVICES[name][2]).hostname)]
    if occupied:
        for name in occupied:
            print(f"  ✗ {name}: 端口 {SERVICES[name][1]} 已被其他进程占用；未启动", file=sys.stderr)
        return 1

    print(f"\n数据目录：{data_dir()}（四个服务共用一个账户权威）")
    print("登记 OIDC 客户端：")
    if not register_clients():
        return 1

    print("\n启动服务：")
    for name in selected:
        role, port, url, _, _app = SERVICES[name]
        if _alive(name):
            print(f"  · {name} 已在运行（pid {_alive(name)}）")
            continue
        if _port_busy(port, urllib.parse.urlparse(url).hostname):
            print(f"  ✗ {name}: 端口 {port} 已被占用", file=sys.stderr)
            return 1
        child_env = env_for(name)
        with (RUN / f"{name}.log").open("a") as log:
            log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start role={role} =====\n")
            log.flush()
            p = subprocess.Popen([str(PY), "-m", "synwevo_server.run"],
                                 env=child_env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        _pidfile(name).write_text(str(p.pid))
        print(f"  ✓ {name:<9} role={role:<9} {url}  绑定 {env_for(name)['SYNWEVO_HOST']}:{port}"
              f"  (pid {p.pid})")

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if all(_ready(n) for n in selected):
            break
        time.sleep(0.3)
    failed = [n for n in selected if not _ready(n)]
    if failed:
        # A start script whose only output is "OK" is worse than no script: the next thing that
        # fails looks unrelated. Say which one, and where to read why.
        for n in failed:
            print(f"  ✗ {n} 没有在 20 秒内就绪；日志：{RUN / (n + '.log')}", file=sys.stderr)

    print("\n打开：")
    print(f"  Synwevo Workspace   {PORTAL}")
    print(f"  Synwevo Docs        {DOCS}")
    print(f"  Synwevo Message     {MESSAGES}")
    print(f"  Synwevo Account     {ACCOUNTS}/login")
    print(f"\n日志：{RUN}/<服务>.log      停止：python scripts/platform.py down")
    return 1 if failed else 0


def down(args) -> int:
    for name in _selected(args):
        pid = _alive(name)
        if not pid:
            stale = _pidfile(name).exists()
            print(f"  · {name} 未在运行" + ("（pid 文件已过期：那个编号现在不是我们的服务，未发送信号）"
                                         if stale else ""))
            _pidfile(name).unlink(missing_ok=True)
            continue
        if getattr(args, "kill", False):
            # a crash rehearsal: no graceful shutdown, so nothing is written on the way out
            os.kill(pid, signal.SIGKILL)
            for _ in range(40):
                if not _alive(name):
                    break
                time.sleep(0.1)
            print(f"  ✓ {name} 已强制结束（pid {pid}，未走优雅关闭）")
            _pidfile(name).unlink(missing_ok=True)
            continue
        os.kill(pid, signal.SIGTERM)
        for _ in range(40):
            if not _alive(name):
                break
            time.sleep(0.1)
        if _alive(name):
            os.kill(pid, signal.SIGKILL)
            print(f"  ✓ {name} 已强制停止（pid {pid}）")
        else:
            print(f"  ✓ {name} 已停止（pid {pid}）")
        _pidfile(name).unlink(missing_ok=True)
    return 0


def status(args) -> int:
    print(f"数据目录：{data_dir()}")
    for name, (role, port, url, client, _app) in SERVICES.items():
        pid = _alive(name)
        listening = _port_busy(port, urllib.parse.urlparse(url).hostname)
        state = f"运行中 pid {pid}" if pid else ("端口被占用但不是我们启动的" if listening else "已停止")
        print(f"  {name:<9} role={role:<9} {url:<34} {state}")
    if any(_alive(n) for n in SERVICES):
        try:
            with urllib.request.urlopen(f"{ACCOUNTS}/.well-known/openid-configuration", timeout=3) as r:
                d = json.load(r)
            print(f"\n发行者：{d['issuer']}")
            print(f"支持的范围：{' '.join(d['scopes_supported'])}")
        except Exception as e:
            print(f"\n账户服务发现文档不可读：{e}")
    return 0


def logs(args) -> int:
    f = RUN / f"{args.service}.log"
    if not f.exists():
        print(f"没有 {f}", file=sys.stderr)
        return 2
    lines = f.read_text(errors="replace").splitlines()
    print("\n".join(lines[-args.n:]))
    return 0



# ---------------------------------------------------------------- setup / verification


def _run(cmd: list[str], cwd: Path | None = None) -> int:
    print("  $ " + " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=str(cwd or ROOT)).returncode


def _require_data_dir(args) -> None:
    """Refuse to start against a directory that is not the initialised instance.

    Starting with a wrong or empty path does not fail: SQLite creates the file, the schema is
    written, and the platform comes up looking like a fresh install with nobody's data in it.
    That is the failure that reads as "the data is gone", so it is turned into a refusal — and
    `--init` is how you say you meant it.
    """
    d = Path(data_dir())
    db = d / "docweave.sqlite3"
    if db.exists() or getattr(args, "init", False):
        return
    print(f"数据目录 {d} 里没有 docweave.sqlite3。\n"
          f"如果这是新装，用 --init 明确表示；如果不是，先检查 SYNWEVO_DATA_DIR 指到哪里了。\n"
          f"这里不会替你建一个空库：一个空库会让服务正常启动，而那正是「数据不见了」的样子。",
          file=sys.stderr)
    raise SystemExit(2)


# Development checkout packages. The SDK is needed by catalogue/acceptance tests;
# production services do not depend on it (their packages are installed in infra/Dockerfile).
MODULES = ["packages/core", "packages/auth-client/python", "services/identity",
           "apps/portal", "apps/docs", "apps/messages", "apps/suite", "infra/server", "apps/docs/sdk/python"]
IMPORTS = ["synwevo_core", "synwevo_auth_client", "synwevo_identity", "synwevo_portal",
           "synwevo_docs", "synwevo_messages", "synwevo_suite", "synwevo_server", "synwevo_docs_sdk"]


def setup(args) -> int:
    """Create the workspace virtualenv, install every module, build the four shells.

    Reproducible means: third-party versions come from the requirements files, the front end
    from package-lock.json (`npm ci`, never `npm install`, which may rewrite the lock), and the
    result is checked — `pip check` proves every module's declared dependencies are installed,
    and each module is imported once, from the venv, before this reports success.
    """
    if sys.version_info < (3, 12):
        print(f"需要 Python 3.12 或更高（当前 {sys.version.split()[0]}，{sys.executable}）。\n"
              f"换一个解释器运行：python3.12 scripts/platform.py setup", file=sys.stderr)
        return 2
    missing = [m for m in MODULES if not (ROOT / m / "pyproject.toml").is_file()]
    if missing:
        print(f"找不到这些模块：{', '.join(missing)}", file=sys.stderr)
        return 2
    if not PY.exists():
        print(f"建立 {ROOT / '.venv'}：")
        if _run([sys.executable, "-m", "venv", str(ROOT / ".venv")]):
            return 1
    print("安装 Python 依赖（按各模块固定的版本）：")
    if _run([str(PY), "-m", "pip", "install", "-q", "-r", "infra/server/requirements-build.txt"]):
        return 1
    if _run([str(PY), "-m", "pip", "install", "-q", "-r", "requirements-dev.txt"]):
        return 1
    print("以可编辑方式安装各模块（真实的包边界，不是 sys.path 拼接）：")
    if _run([str(PY), "-m", "pip", "install", "-q", "--no-deps", "--no-build-isolation"]
            + [a for m in MODULES for a in ("-e", m)]):
        return 1
    print("核对：每个模块声明的依赖都已安装，并且都能导入：")
    if _run([str(PY), "-m", "pip", "check"]):
        return 1
    if _run([str(PY), "-c", "import " + ", ".join(IMPORTS)]):
        return 1
    if shutil.which("npm") is None:
        print("找不到 npm：构建四个前端外壳需要 Node.js（容器镜像用的是 Node 22）。",
              file=sys.stderr)
        return 1
    print("按锁文件安装前端依赖并构建四个外壳：")
    if _run(["npm", "ci"]) or _run(["npm", "run", "build"]):
        return 1
    print("\n完成。启动：python scripts/platform.py up --init（新装）或 python scripts/platform.py up")
    return 0


SUITES = {"docs": "apps/docs/tests", "identity": "services/identity/tests",
          "messages": "apps/messages/tests", "suite": "apps/suite/tests",
          "integration": "tests/integration"}


def test(args) -> int:
    # `args.suite` is one name. Iterating the string itself walked its letters ('d','o',…) and
    # raised KeyError for every suite except "all".
    keys = list(SUITES) if args.suite == "all" else [args.suite]
    return _run([str(PY), "-m", "pytest", *(SUITES[k] for k in keys)])


def deps(args) -> int:
    """Check the dependency boundaries the restructure exists to create."""
    return _run([str(PY), str(ROOT / "scripts" / "check_boundaries.py")])


SNAPSHOT = ROOT / "apps" / "docs" / "scripts" / "snapshot.py"


def _here(path: str) -> str:
    """A path the person typed, made absolute against *their* working directory.

    The snapshot tool runs with the repository root as its working directory and resolves a
    relative `--into` / `--out` against that root, so `platform.py verify-restore ./b.tar.gz`
    typed anywhere else looked for the archive in the wrong place. Resolving here, before the
    hand-over, is the only point that still knows where the person was standing.
    """
    return str(Path(path).expanduser().resolve())


def backup(args) -> int:
    """A consistent snapshot: SQLite's own backup API plus the blob tree, in one archive.

    Delegates to the tool that already does this correctly rather than growing a second one.
    Copying the `.sqlite3` file is not a backup while a write-ahead log exists — it is a backup
    missing whatever had not been checkpointed.
    """
    out = _here(args.out) if args.out else str(ROOT / ".local" / "backups")
    return _run([str(PY), str(SNAPSHOT), "create", "--data", data_dir(), "--out", out])


def verify_restore(args) -> int:
    """Restore into a throwaway directory and compare, row by row, against what was captured.

    A backup that has never been restored is a file, not a backup. The live security journal is
    read (never written) so the rehearsal also shows which withdrawals a real restore would
    re-apply.
    """
    return _run([str(PY), str(SNAPSHOT), "verify", "--archive", _here(args.backup),
                 "--journal-from", data_dir()])


def restore(args) -> int:
    """Restore an archive, then re-apply every withdrawal of access recorded after it was taken.

    The services must be stopped first when restoring over the live data directory; the tool
    checks, and refuses while the database is open.
    """
    cmd = [str(PY), str(SNAPSHOT), "restore", "--archive", _here(args.backup), "--into", _here(args.into),
           "--journal-from", data_dir()]
    if args.force:
        cmd.append("--force")
    if args.no_journal:
        cmd.append("--no-journal")
    return _run(cmd)


def restart(args) -> int:
    down(args)
    return up(args)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("setup", help="建立 .venv、安装各模块、构建前端").set_defaults(func=setup)

    u = sub.add_parser("up", help="启动服务（不指定则全部）")
    u.add_argument("services", nargs="*", type=_service, default=[])
    u.add_argument("--init", action="store_true", help="允许在空数据目录上初始化一个新实例")
    u.set_defaults(func=up)

    d = sub.add_parser("down", help="停止服务（不指定则全部）")
    d.add_argument("services", nargs="*", type=_service, default=[])
    d.add_argument("--kill", action="store_true", help="不走优雅关闭，直接结束进程（演练进程崩溃）")
    d.set_defaults(func=down)

    rs = sub.add_parser("restart", help="重启服务")
    rs.add_argument("services", nargs="*", type=_service, default=[])
    rs.add_argument("--init", action="store_true")
    rs.set_defaults(func=restart)

    sub.add_parser("status", help="端口、进程与发行者").set_defaults(func=status)

    lg = sub.add_parser("logs", help="查看某个服务的日志")
    lg.add_argument("service", choices=list(SERVICES))
    lg.add_argument("-n", type=int, default=120)
    lg.set_defaults(func=logs)

    t = sub.add_parser("test", help="运行模块级或平台集成验证")
    t.add_argument("suite", nargs="?", choices=list(SUITES) + ["all"], default="all")
    t.set_defaults(func=test)

    sub.add_parser("deps", help="检查模块依赖边界").set_defaults(func=deps)

    bk = sub.add_parser("backup", help="一致性备份（SQLite 备份 API + blob 清单）")
    bk.add_argument("--out")
    bk.set_defaults(func=backup)

    vr = sub.add_parser("verify-restore", help="在隔离副本上核验备份能恢复")
    vr.add_argument("backup", help="备份归档文件（python scripts/platform.py backup 的产物）")
    vr.set_defaults(func=verify_restore)

    rt = sub.add_parser("restore", help="恢复备份，并重放备份之后的撤销记录")
    rt.add_argument("backup", help="备份归档文件")
    rt.add_argument("--into", required=True, help="恢复到的目录；覆盖正在使用的数据目录需要 --force")
    rt.add_argument("--force", action="store_true",
                    help="目录里已有数据库时，先把它移到隔离区再恢复（服务必须已停止）")
    rt.add_argument("--no-journal", action="store_true",
                    help="原撤销日志丢失时使用：会话、令牌、旧 API key 全部作废，机密/服务客户端停用；"
                         "保留公开交互客户端。无法补回备份后的成员与账户撤权历史，恢复后需人工核对")
    rt.set_defaults(func=restore)

    args = ap.parse_args()
    if args.cmd != "setup" and _needs_venv():
        return _reexec_in_venv()
    # Handed over successfully: the mark is not for the services or test runs started from here.
    os.environ.pop(REEXEC_MARK, None)
    return args.func(args)


def _needs_venv() -> bool:
    """Everything but `setup` reads configuration through `synwevo_core`, which lives in the
    workspace virtualenv. Started with another interpreter, hand over to that one instead of
    failing on an import — or, worse, carrying a second copy of the configuration rules here."""
    try:
        import synwevo_core  # noqa: F401
        return False
    except ImportError:
        return True


REEXEC_MARK = "SYNWEVO_PLATFORM_REEXEC"


def _running_in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == (ROOT / ".venv").resolve()
    except OSError:
        return False


def _reexec_in_venv() -> int:
    """Hand over to the workspace interpreter — once.

    A `.venv` that exists but cannot import `synwevo_core` (setup failed after creating it, or
    the package itself raises on import) used to hand over to itself forever: every command but
    `setup` spun in an exec loop and never printed anything. So the hand-over is marked, and an
    interpreter that already is the venv, or was already handed over to, says what is wrong.
    """
    if not PY.exists():
        print(f"没有 {PY}。先运行：python scripts/platform.py setup", file=sys.stderr)
        return 2
    if os.environ.get(REEXEC_MARK) or _running_in_venv():
        try:
            import synwevo_core  # noqa: F401
            why = ""
        except ImportError as e:
            why = f"（{e}）"
        print(f"{ROOT / '.venv'} 里导入不了 synwevo_core{why}。\n"
              f"安装没有完成，或者包本身导入失败。重新运行：python3.12 scripts/platform.py setup",
              file=sys.stderr)
        return 2
    os.environ[REEXEC_MARK] = "1"
    os.execv(str(PY), [str(PY), str(Path(__file__).resolve()), *sys.argv[1:]])
    return 2  # not reached


if __name__ == "__main__":
    sys.exit(main())
