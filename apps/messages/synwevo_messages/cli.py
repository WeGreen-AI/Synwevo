"""Synwevo Message 运维 CLI：补扫扫描上线前存入的附件，以及隔离区的复核操作。

    python -m synwevo_messages.cli rescan [--apply] [--limit N]         # 不加 --apply 只预览
    python -m synwevo_messages.cli quarantine list
    python -m synwevo_messages.cli quarantine release <sha256>      # 复核为误报、签名撤回之后
    python -m synwevo_messages.cli quarantine purge <sha256> --yes  # 复核确认后永久删除

逻辑在 `synwevo_core.messaging_rescan`（附件表属于 `synwevo_core.messaging`）；这里只接线。
生产上在 messages 容器里运行，扫描配置取容器自己的环境：

    docker compose -f /opt/synwevo/config/compose.yaml exec -T messages python -m synwevo_messages.cli rescan --apply </dev/null

退出码：0 全部干净或无事可做；1 有感染、出错、摘要不符或内容缺失；2 用法或配置错误；3 扫描器不可用。
策略与复核流程见 docs/security/file-acceptance.md §8。
"""
from __future__ import annotations

import argparse
import sys

from synwevo_core import env
from synwevo_core import messaging_rescan as MR
from synwevo_core import rescan as RS
from synwevo_core.http import data_dir_default


def data_dir(args) -> str:
    return args.data or env.get("DATA_DIR") or str(data_dir_default())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m synwevo_messages.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="数据目录（默认 $SYNWEVO_DATA_DIR 或仓库根目录 data/）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    RS.add_arguments(sub, workspace=False, kinds=("attachments",))
    args = ap.parse_args(argv)
    if args.cmd == "rescan":
        return MR.main_rescan(args, data_dir(args))
    return MR.main_quarantine(args, data_dir(args))


if __name__ == "__main__":
    sys.exit(main())
