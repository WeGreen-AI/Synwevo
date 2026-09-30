"""Actual deployed composition and PDF bytes; no model or OCR substitute."""
import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("role", ["portal", "all"])
def test_real_pdf_reader_evidence_and_review(tmp_path, monkeypatch, role):
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
    from synwevo_server.app import create_app
    from synwevo_docs.ingest import DocumentIngest
    import fitz

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "extract")
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://workplace.testserver")
    api = create_app(str(tmp_path / "data"), role=role, start_jobs=False)
    assert isinstance(api.state.extract.reader, DocumentIngest)
    ident = api.state.identity
    user = ident.create_user("extract-fixture@example.test", "harbor lights guide the evening ferry home", status="active")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    ws = ident.create_workspace("Extract PDF fixture", owner_id=user["id"])["id"]
    raw, _ = Sessions(ident.store).create(user["id"], workspace_id=ws, amr="password")
    csrf = {"X-Synwevo-CSRF": "fixture-csrf"}
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((60, 80), "Invoice: 000127\nAmount: 123456789.01\nIssued: 2026-09-30")
    pdf = doc.tobytes()
    doc.close()
    definition = {"fields": [
        {"id": "code", "label": "Code", "rule": {"prefix": "Invoice:"}},
        {"id": "amount", "label": "Amount", "type": "decimal", "rule": {"prefix": "Amount:"}},
        {"id": "optional", "label": "Missing", "required": False, "rule": {"prefix": "Not present:"}},
    ]}
    with TestClient(api, base_url="http://workplace.testserver") as client:
        client.cookies.set(COOKIE_NAME, raw)
        client.cookies.set(CSRF_COOKIE, "fixture-csrf")

        def op(name, body):
            response = client.post(f"/api/suite/ops/extract.{name}", headers=csrf,
                                   json={"workspace_id": ws, "input": body})
            assert response.status_code == 200, response.text
            return response.json()

        template = op("template.create", {"title": "PDF fixture", "definition": definition})["output"]["id"]
        op("template.publish", {"template_id": template, "draft_rev": 1})
        uploaded = client.post("/api/suite/extract/files", params={"workspace_id": ws},
                               headers={**csrf, "Idempotency-Key": "pdf-fixture"},
                               files={"file": ("invoice.pdf", pdf, "application/pdf")})
        assert uploaded.status_code == 200, uploaded.text
        created = op("task.create", {"template_id": template, "version": 1, "file_id": uploaded.json()["id"]})
        assert created["status"] == "queued"
        tid = created["output"]["id"]
        api.state.extract.tick()  # actual bounded parser child, not a fake result
        task = op("task.get", {"task_id": tid})["output"]
        assert task["status"] == "waiting_input", task["error_code"]
        assert task["values"]["code"]["value"] == "000127"
        assert task["values"]["amount"]["value"] == "123456789.01"
        assert task["values"]["optional"]["status"] == "missing"
        segments = op("task.segments", {"task_id": tid})["output"]["items"]
        candidate = task["values"]["amount"]["candidates"][0]
        evidence = candidate["evidence"]
        assert candidate["verified"] and evidence["source"]["page"] == 1
        segment = next(s for s in segments if s["id"] == evidence["segment_id"])
        assert segment["text"][evidence["start"]:evidence["end"]] == "123456789.01"
        op("task.confirm", {"task_id": tid, "revision": 1, "note": "Compared with authorized PDF fixture"})
        result = op("task.result", {"task_id": tid, "revision": 2})["output"]
        assert result["machine"] == task["machine"]
        assert result["review"]["actor_id"] == user["id"]
        receipt = client.get(f"/api/suite/runs/{created['run_id']}").json()
        assert receipt["status"] == "succeeded"
        assert receipt["output"]["confirmed_revision"] == 2
