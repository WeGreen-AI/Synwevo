"""Compatibility launcher; real-service orchestration belongs to integration tests."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[4]
_implementation = runpy.run_path(str(ROOT / "tests/integration/sdk_acceptance/n8n_receiver.py"))
request = _implementation["request"]
wait_ready = _implementation["wait_ready"]
stop = _implementation["stop"]
running = _implementation["running"]
