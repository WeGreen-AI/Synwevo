"""Export the business applications' machine API contract for this deployment.

    python -m synwevo_server.suite_contract --out docs/integration/suite-openapi.json

The contract is built from a registry (`synwevo_suite.common.machine_contract`), and the registry
an integration actually meets is the composed one: the applications plus the operations only the
composition root can register because they span two of them (importing a form attachment into
Extract). Exporting from the applications alone left those out of the published document while
the server served them. So the exporter lives here, where the composition does.

It builds the composition over an empty database in a temporary directory with every application
released; it never opens the deployment's data, starts no worker and calls no handler.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def build() -> dict:
    from synwevo_suite.common.machine_contract import contract
    from .app import create_app
    saved = {k: os.environ.get(k) for k in ("SYNWEVO_SUITE_APPS", "SYNWEVO_DATA_DIR", "SYNWEVO_UI_AUTH")}
    with tempfile.TemporaryDirectory(prefix="synwevo-suite-contract-") as scratch:
        os.environ.update({"SYNWEVO_SUITE_APPS": "all", "SYNWEVO_DATA_DIR": scratch, "SYNWEVO_UI_AUTH": "session"})
        try:
            api = create_app(scratch, role="all", start_jobs=False)
            return contract(api.state.capabilities.registry)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="JSON 输出文件")
    args = ap.parse_args()
    spec = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写出 {out}（{len(spec['paths'])} 个路径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
