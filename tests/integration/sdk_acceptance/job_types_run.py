"""Real Jobs HTTP and installed-SDK acceptance with a synthetic recoverable blob outage.

Always creates an empty temporary Store; accepts no existing data directory or server URL.
Only the fixture's job worker lifecycle and one content-addressed template blob are controlled.
The production engine, HTTP routes, authorization and frozen job snapshot are unchanged.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-python", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    sdk_python = args.sdk_python.absolute()  # retain a venv symlink's environment
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error("evidence directory must be empty")
    repo = Path(__file__).resolve().parents[3]
    acceptance = repo / "apps" / "docs" / "sdk" / "acceptance"
    spec = importlib.util.spec_from_file_location("synthetic_job_kit", acceptance / "make_kit.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    kit.make(out / "kit")
    # One invalid sample remains skipped across a strict failed-only retry. This is real
    # input validation, not a changed job row or renderer stub.
    import openpyxl
    workbook = openpyxl.load_workbook(out / "kit" / "customers.xlsx")
    workbook.active.cell(13, 3).value = None
    workbook.save(out / "kit" / "customers.xlsx")
    fields_path = out / "kit" / "fields.json"
    fields = json.loads(fields_path.read_text())
    next(f for f in fields["fields"] if f["name"] == "城市")["required"] = True
    fields_path.write_text(json.dumps(fields, ensure_ascii=False))

    # Scrub before importing server configuration; no caller settings can select real data.
    for key in list(os.environ):
        if key.startswith(("SYNWEVO_", "DOCWEAVE_")):
            del os.environ[key]
    os.environ.update(SYNWEVO_TEST_INSTANCE="1", SYNWEVO_UI_AUTH="none", SYNWEVO_ROLE="app",
                      SYNWEVO_MALWARE_SCAN="off", NO_PROXY="127.0.0.1,localhost")
    with tempfile.TemporaryDirectory(prefix="synwevo-jobs-sdk-") as tmp:
        root = Path(tmp)
        control = root / "control"
        control.mkdir()
        os.environ["SYNWEVO_DATA_DIR"] = str(root / "data")
        os.environ["SYNWEVO_DOCS_REGRESSION_DIR"] = str(root / "regression")
        import uvicorn
        from synwevo_core import workers
        from synwevo_core.auth import Auth
        from synwevo_docs.api.app import create_app
        from synwevo_docs.jobs import JobService
        from python_service import Evidence

        app = create_app(str(root / "data"))
        store = app.state.app.store
        idle = app.state.jobs
        idle.stop()
        for thread in (idle._thread, idle._supervisor):
            thread.join(timeout=10)
            assert not thread.is_alive(), "empty fixture worker did not stop"
        token, _ = Auth(store).create_key("synthetic-job-contract", "default", ("read", "write"))
        secret_file = root / "api-key"
        with open(secret_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as fh:
            fh.write(token)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(Evidence(app, out / "http.jsonl"), log_level="warning", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        process = None
        displaced = original = None
        try:
            deadline = time.monotonic() + 20
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "test server startup failed"
                time.sleep(.02)
            env = {**os.environ, "SYNWEVO_BASE_URL": base, "SYNWEVO_API_KEY_FILE": str(secret_file)}
            with (out / "worker.log").open("w") as log:
                process = subprocess.Popen([str(sdk_python), str(acceptance / "python_jobs.py"),
                    "--kit", str(out / "kit"), "--control", str(control), "--out", str(out / "sdk")],
                    cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)

            def wait_checkpoint(name):
                deadline = time.monotonic() + 120
                path = control / (name + ".json")
                while not path.exists():
                    assert process.poll() is None, f"SDK worker exited; see {out / 'worker.log'}"
                    assert time.monotonic() < deadline, f"SDK checkpoint {name} timed out"
                    time.sleep(.02)
                return json.loads(path.read_text())

            created = wait_checkpoint("created")
            job_id = created["job_id"]
            snapshot = store.one("SELECT snapshot FROM jobs WHERE id=?", (job_id,))["snapshot"]
            digest = snapshot["template"]["working_blob"]
            original = store.blob_path(digest)
            assert original.is_relative_to(root) and hashlib.sha256(original.read_bytes()).hexdigest() == digest
            displaced = root / "synthetic-template-temporarily-unavailable"
            original.replace(displaced)
            JobService(app.state.app)  # actual production runner, now reading the missing fixture blob
            failed = wait_checkpoint("failed")
            displaced.replace(original)
            assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
            (control / "restored.json").write_text("{}")
            assert process.wait(timeout=120) == 0, f"SDK worker failed; see {out / 'worker.log'}"
            assert store.one("SELECT snapshot FROM jobs WHERE id=?", (job_id,))["snapshot"] == snapshot
            result = json.loads((out / "sdk" / "acceptance.json").read_text())
            result.update(fixture="fresh temporary Store; synthetic missing template blob restored byte-for-byte",
                          snapshot_sha256=hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest(),
                          backend_snapshot_unchanged=True, failed_checkpoint=failed)
            (out / "acceptance.json").write_text(json.dumps(result, indent=2))
            print(json.dumps(result))
        finally:
            if displaced is not None and displaced.exists():
                displaced.replace(original)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.should_exit = True
            thread.join(timeout=15)
            workers.stop_all(wait=5)
            sock.close()


if __name__ == "__main__":
    main()
