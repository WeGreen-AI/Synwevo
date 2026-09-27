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
    # The account role scans member-import uploads with the same policy, so it reports the same
    # component in the same shape — and stays ready while the engine is down (imports wait).
    with TestClient(create_app(str(tmp_path / "d3"), role="accounts", start_jobs=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        assert r.json()["checks"]["malware_scanner"].startswith("down:")
    # The messages role scans attachments through the same acceptance chain (CI-66): same
    # component, same shape, same non-gating behaviour.
    with TestClient(create_app(str(tmp_path / "d5"), role="messages", start_jobs=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        assert r.json()["checks"]["malware_scanner"].startswith("down:")
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    with TestClient(create_app(str(tmp_path / "d4"), role="accounts", start_jobs=False)) as c:
        assert c.get("/readyz").json()["checks"]["malware_scanner"] == "off"
    with TestClient(create_app(str(tmp_path / "d6"), role="messages", start_jobs=False)) as c:
        assert c.get("/readyz").json()["checks"]["malware_scanner"] == "off"


def test_every_role_serves_the_inbox_routes_to_its_sessions(tmp_path, monkeypatch):
    """The inbox is one list read from every origin: an application role answers the inbox
    routes with its own session check (401 here, no session) instead of the role refusal (404)."""
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    from synwevo_server.app import create_app
    for role in ("app", "portal", "accounts", "messages"):
        with TestClient(create_app(str(tmp_path / role), role=role, start_jobs=False)) as c:
            for path in ("/api/auth/notifications", "/api/auth/notifications/unread-count", "/api/auth/notifications/preferences"):
                r = c.get(path)
                assert r.status_code == 401 and r.json()["error"] == "unauthorized", (role, path, r.status_code, r.text[:120])
            r = c.post("/api/auth/notifications/mark", json={"ids": ["x"], "read": True})
            assert r.status_code in (401, 403), (role, r.status_code)
