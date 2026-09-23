#!/usr/bin/env python3
"""Run the platform locally on three real origins.

    python scripts/platform_local.py up          # start all three, register clients, print URLs
    python scripts/platform_local.py down
    python scripts/platform_local.py status
    python scripts/platform_local.py logs accounts|portal|docweave [-n 200]

Why three hostnames and not three ports: **different ports do not isolate cookies.** A cookie
set by `localhost:8770` is sent to `localhost:8765` as well, because the cookie protocol scopes
by host and ignores the port entirely. Running all three on `localhost` would therefore produce
a demonstration in which the account service's session cookie is handed to DocWeave on every
request — and a "cross-origin single sign-on" that is really one shared cookie wearing three
hats. `*.localhost` names all resolve to the loopback address without any hosts-file edit (RFC
6761), so this gets genuinely separate cookie jars with no system configuration at all.

One database. The account service and DocWeave are the same binary in two roles, and they share
the store, because there must be exactly one account authority — two databases would mean two
answers to "is this person suspended".

Local only. Nothing here touches DNS, opens a tunnel, or listens on anything but loopback.
"""
from __future__ import annotations

import argparse
import json
import os
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
PY = ROOT / "backend" / ".venv" / "bin" / "python"

# Overridable, because a developer very often already has a DocWeave on 8765 and stopping
# someone else's running server to demonstrate a login flow is not an acceptable trade.
ACCOUNTS_PORT = int(os.environ.get("PLATFORM_ACCOUNTS_PORT", "8770"))
PORTAL_PORT = int(os.environ.get("PLATFORM_PORTAL_PORT", "8769"))
DOCWEAVE_PORT = int(os.environ.get("PLATFORM_DOCWEAVE_PORT", "8765"))
ACCOUNTS = f"http://accounts.localhost:{ACCOUNTS_PORT}"
PORTAL = f"http://portal.localhost:{PORTAL_PORT}"
DOCWEAVE = f"http://docweave.localhost:{DOCWEAVE_PORT}"

SERVICES = {
    # name: (role, port, public URL, OIDC client id, app id)
    #
    # The portal's app id is `platform`, which is deliberately *not* a row in the application
    # registry: the registry lists business applications you can be granted or denied, and the
    # portal is where you go to see which of those you have.
    "accounts": ("accounts", ACCOUNTS_PORT, ACCOUNTS, None, None),
    "portal": ("portal", PORTAL_PORT, PORTAL, "portal-web", "platform"),
    "docweave": ("app", DOCWEAVE_PORT, DOCWEAVE, "docweave-web", "docweave"),
}


def data_dir() -> str:
    return os.environ.get("DOCWEAVE_DATA_DIR") or str(ROOT / "data")


def env_for(name: str) -> dict:
    role, port, url, client, app = SERVICES[name]
    env = dict(os.environ)
    env.update({
        "DOCWEAVE_ROLE": role,
        "DOCWEAVE_PORT": str(port),
        "DOCWEAVE_HOST": bind_addr(urllib.parse.urlparse(url).hostname),
        "DOCWEAVE_DATA_DIR": data_dir(),
        "DOCWEAVE_UI_AUTH": "session",
        "DOCWEAVE_ISSUER": ACCOUNTS,
        "DOCWEAVE_APP_URL": url,
        # Plain HTTP on loopback, so the Secure attribute would stop the cookie being stored at
        # all. This is the one local-only concession, and it is why these hostnames must never
        # be used for anything real.
        "DOCWEAVE_COOKIE_SECURE": "0",
        # Where the account pages' "back" link goes. Server-controlled, never a query parameter.
        "DOCWEAVE_PORTAL_URL": PORTAL,
    })
    if client:
        env["DOCWEAVE_OIDC_CLIENT_ID"] = client
    if app:
        env["DOCWEAVE_OIDC_APP_ID"] = app
    return env


def bind_addr(host: str) -> str:
    """The loopback address this hostname actually resolves to first.

    Not a detail. `*.localhost` resolves to `::1` *and* `127.0.0.1` on this machine, and
    `getaddrinfo` returns `::1` first. uvicorn binds exactly one address, so binding
    `127.0.0.1` left `[::1]:8770` refusing connections — which the browser hops survived,
    because urllib and browsers try every address, and the server-to-server token call did not,
    because httpx does not. The result was a sign-in that got all the way to the callback and
    then failed with "Server disconnected without sending a response".

    So: the service listens where its own name points. Same rule in production, where the
    issuer hostname and the bind address must agree.
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
    addr = bind_addr(host)
    fam = socket.AF_INET6 if ":" in addr else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((addr, port)) == 0


def _pidfile(name: str) -> Path:
    return RUN / f"{name}.pid"


def _alive(name: str) -> int | None:
    f = _pidfile(name)
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def check_hostnames() -> bool:
    """Confirm the three names resolve to loopback before claiming isolation.

    If a name resolved somewhere else, requests would leave this machine — so this is checked
    rather than assumed, and the answer is printed rather than swallowed.
    """
    ok = True
    for host in ("accounts.localhost", "portal.localhost", "docweave.localhost"):
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


def register_clients() -> None:
    """Register the two relying parties. Idempotent, and by CLI on purpose — there is no
    dynamic client registration endpoint and no UI for this."""
    for client_id, app_id, name, base in (
            ("portal-web", "platform", "平台门户", PORTAL),
            ("docweave-web", "docweave", "DocWeave", DOCWEAVE)):
        cmd = [str(PY), str(ROOT / "backend" / "manage.py"), "--data", data_dir(),
               "client", "register", "--client-id", client_id, "--app", app_id, "--name", name,
               "--redirect", f"{base}/auth/callback",
               "--post-logout", f"{base}/signed-out",
               "--backchannel", f"{base}/auth/backchannel-logout"]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ✗ {client_id}: {p.stderr.strip()[:200]}")
        else:
            print(f"  ✓ {client_id} → {base}/auth/callback")


def up(args) -> int:
    RUN.mkdir(parents=True, exist_ok=True)
    print("检查本机域名解析：")
    if not check_hostnames():
        print("\n这些名字必须解析到回环地址，否则请求会离开本机。中止。", file=sys.stderr)
        return 2

    print(f"\n数据目录：{data_dir()}（三个服务共用一个账户权威）")
    print("登记 OIDC 客户端：")
    register_clients()

    print("\n启动服务：")
    for name, (role, port, url, _, _app) in SERVICES.items():
        if _alive(name):
            print(f"  · {name} 已在运行（pid {_alive(name)}）")
            continue
        if _port_busy(port, urllib.parse.urlparse(url).hostname):
            print(f"  ✗ {name}: 端口 {port} 已被占用", file=sys.stderr)
            continue
        log = (RUN / f"{name}.log").open("a")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start role={role} =====\n")
        log.flush()
        p = subprocess.Popen([str(PY), str(ROOT / "backend" / "run.py")],
                             env=env_for(name), stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        _pidfile(name).write_text(str(p.pid))
        print(f"  ✓ {name:<9} role={role:<9} {url}  绑定 {env_for(name)['DOCWEAVE_HOST']}:{port}"
              f"  (pid {p.pid})")

    deadline = time.time() + 20
    while time.time() < deadline:
        if all(_port_busy(port, urllib.parse.urlparse(u).hostname)
               for _, port, u, _, _a in SERVICES.values()):
            break
        time.sleep(0.3)

    print("\n打开：")
    print(f"  平台门户   {PORTAL}")
    print(f"  DocWeave   {DOCWEAVE}")
    print(f"  账户服务   {ACCOUNTS}/login")
    print(f"\n日志：{RUN}/<服务>.log      停止：python scripts/platform_local.py down")
    return 0


def down(args) -> int:
    for name in SERVICES:
        pid = _alive(name)
        if not pid:
            print(f"  · {name} 未在运行")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("up").set_defaults(func=up)
    sub.add_parser("down").set_defaults(func=down)
    sub.add_parser("status").set_defaults(func=status)
    lg = sub.add_parser("logs")
    lg.add_argument("service", choices=list(SERVICES))
    lg.add_argument("-n", type=int, default=120)
    lg.set_defaults(func=logs)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
