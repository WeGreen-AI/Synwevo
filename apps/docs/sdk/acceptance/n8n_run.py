"""Compatibility launcher; real-service orchestration belongs to integration tests."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[4]
_implementation = runpy.run_path(str(ROOT / "tests/integration/sdk_acceptance/n8n_run.py"))
free_port = _implementation["free_port"]
execute = _implementation["execute"]
