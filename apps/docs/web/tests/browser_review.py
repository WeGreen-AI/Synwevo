"""Run the repository's real Chrome workflows with evidence outside the source tree.

No request mocking, no production data, no global proxy changes. The inspected _testenv
starts a synthetic, test-marked server; its context terminates it even after a failure.
Usage: .venv/bin/python apps/docs/web/tests/browser_review.py --evidence <temporary-dir>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    args = sys.argv
    try:
        sys.argv = [str(path)]
        spec.loader.exec_module(module)
    finally:
        sys.argv = args
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--flows", nargs="+", default=["ui_e2e", "ui_e2e_keyboard", "ui_e2e_more"])
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    env = load("_testenv")
    load("ui_e2e")  # keyboard workflow imports these shared, inspected helpers
    results = []
    with env.isolated(keep=True) as instance:
        assert env.health(instance.base)["instance"] == "test"
        (args.evidence / "instance.json").write_text(json.dumps(instance._asdict(), default=str, indent=2))
        for name in args.flows:
            module = sys.modules.get(name) or load(name)
            module.ARG_BASE = instance.base
            module.SHOTS = args.evidence / "screenshots"
            t0 = time.monotonic()
            result = {"flow": name, "script_sha256": hashlib.sha256((SCRIPTS / f"{name}.py").read_bytes()).hexdigest()}
            try:
                module.main()
                result["status"] = "passed"
            except Exception:
                result.update(status="failed", error=traceback.format_exc())
                print(result["error"], flush=True)
            result["seconds"] = round(time.monotonic() - t0, 2)
            results.append(result)
            (args.evidence / "browser-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if all(r["status"] == "passed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
