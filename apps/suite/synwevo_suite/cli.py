"""Command line for the business applications module.

    python -m synwevo_suite.cli openapi --out docs/integration/suite-openapi.json
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def cmd_openapi(args) -> int:
    """Write the machine API's contract. The operations exist only once the applications are
    installed, so this builds them over an empty database in a temporary directory — it never
    opens the deployment's data, starts no worker and calls no handler."""
    from .app import APP_MODULES, create_app
    from .common.machine_contract import contract
    with tempfile.TemporaryDirectory(prefix="synwevo-suite-contract-") as scratch:
        api = create_app(scratch, start_jobs=False, released=frozenset(APP_MODULES))
        spec = contract(api.state.capabilities.registry)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出 {out}（{len(spec['paths'])} 个路径）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    export = sub.add_parser("openapi", help="导出业务应用操作接口契约（使用临时空库，不打开部署数据）")
    export.add_argument("--out", required=True, help="JSON 输出文件")
    export.set_defaults(func=cmd_openapi)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
