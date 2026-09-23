"""启动平台的一个角色：

    DOCWEAVE_ROLE=accounts python -m platform_server.run
    DOCWEAVE_ROLE=portal   python -m platform_server.run
    DOCWEAVE_ROLE=app      python -m platform_server.run
    python -m platform_server.run              # 默认 all：单进程部署（容器镜像用的就是这个）

不再 `os.chdir`。它原本是为了让相对路径解析到源码目录；现在数据目录、构建产物与 OCR 数据都从各自
模块的位置推出来，改变进程工作目录只会让"从哪个目录启动"变成一个有后果的问题——而那正是要消除的。
"""
from __future__ import annotations

import os
import sys

import uvicorn

from platform_core.auth import check_listen_config


def main() -> int:
    host = os.environ.get("DOCWEAVE_HOST", "127.0.0.1")
    # Never serve the UI to the network without deciding how it is protected.
    check_listen_config(host)
    uvicorn.run("platform_server.main:app", host=host,
                port=int(os.environ.get("DOCWEAVE_PORT", "8765")), workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
