"""启动平台的一个角色：

    SYNWEVO_ROLE=accounts python -m synwevo_server.run
    SYNWEVO_ROLE=portal   python -m synwevo_server.run
    SYNWEVO_ROLE=app      python -m synwevo_server.run
    python -m synwevo_server.run              # 默认 all：单进程部署（容器镜像用的就是这个）

不再 `os.chdir`。它原本是为了让相对路径解析到源码目录；现在数据目录、构建产物与 OCR 数据都从各自
模块的位置推出来，改变进程工作目录只会让"从哪个目录启动"变成一个有后果的问题——而那正是要消除的。
"""
from __future__ import annotations

import os
import sys
import socket

import uvicorn

from synwevo_core import env
from synwevo_core.auth import check_listen_config


def loopback_sockets(port: int) -> list[socket.socket]:
    """Explicit IPv4/IPv6 loopback only; never bind :: or 0.0.0.0.

    Chromium may resolve *.localhost to IPv4 while the OS and httpx choose IPv6.
    Both names must reach the same server without sharing cookies or changing issuers.
    """
    sockets = []
    try:
        families = [(socket.AF_INET, "127.0.0.1")]
        if socket.has_ipv6:
            families.append((socket.AF_INET6, "::1"))
        for family, address in families:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sockets.append(sock)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.bind((address, port))
            if not port:
                port = sock.getsockname()[1]
            sock.listen(128)
            sock.setblocking(False)
        return sockets
    except BaseException:
        for sock in sockets:
            sock.close()
        raise


def main() -> int:
    host = env.get("HOST", "127.0.0.1")
    # Never serve the UI to the network without deciding how it is protected.
    check_listen_config(host)
    if env.get("LOCAL_DUAL_STACK") == "1":
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("LOCAL_DUAL_STACK requires an explicit loopback HOST")
        sockets = loopback_sockets(int(env.get("PORT", "8765")))
        try:
            uvicorn.Server(uvicorn.Config("synwevo_server.main:app", log_level="info")).run(sockets=sockets)
        finally:
            for sock in sockets:
                sock.close()
        return 0
    uvicorn.run("synwevo_server.main:app", host=host,
                port=int(env.get("PORT", "8765")), workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
