"""The composed server's readiness reports Synwevo Docs' scanner as a non-gating component.

Production runs the composer, not the standalone Docs factory, so the component has to be wired
there too — the first CI-20 deployment proved it by omission (`checks` had no `malware_scanner`).
"""
from fastapi.testclient import TestClient


def test_app_role_reports_the_scanner_without_gating_on_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    monkeypatch.delenv("SYNWEVO_MALWARE_SCAN", raising=False)
    from synwevo_server.app import create_app
    with TestClient(create_app(str(tmp_path / "d"), role="app", start_jobs=False)) as c:
        body = c.get("/readyz").json()
        assert body["checks"]["malware_scanner"] == "off"
    # A configured but unreachable engine is visible, and the role stays ready: accepted files
    # keep working while new uploads wait in quarantine.
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", "tcp://127.0.0.1:1")
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "1")
    with TestClient(create_app(str(tmp_path / "d2"), role="app", start_jobs=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        assert r.json()["checks"]["malware_scanner"].startswith("down:")
    with TestClient(create_app(str(tmp_path / "d3"), role="accounts", start_jobs=False)) as c:
        assert "malware_scanner" not in c.get("/readyz").json()["checks"]
