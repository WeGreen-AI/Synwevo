"""Compatibility launcher; real-service orchestration belongs to integration tests."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[4]
_implementation = runpy.run_path(str(ROOT / "tests/integration/sdk_acceptance/ts_run.py"))
load = _implementation["load"]
run = _implementation["run"]
verify = _implementation["verify"]
main = _implementation["main"]

if __name__ == "__main__":
    main()
