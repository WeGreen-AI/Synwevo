"""Actual deployed composition and PDF bytes; no model or OCR substitute."""
from contextlib import contextmanager

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


def _invoice_pdf(fitz) -> bytes:
    """Fixture invoice (fictional): one content stream per line, the way the acceptance file was made."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    lines = ["INVOICE", "Invoice No: INV-2026-0417", "Invoice Date: 2026-09-28", "Supplier: Northwind Trading Co.",
             "Item: Widget A, 2 x 12.50 = 25.00", "Item: Gadget B, 1 x 99.90 = 99.90",
             "Item: Service fee, 3 x 40.00 = 120.00", "Subtotal: 244.90", "Tax: 24.49", "Total: 269.39"]
    for n, text in enumerate(lines):
        page.insert_text((72, 72 + 20 * n), text, fontsize=11, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


@contextmanager
def _extract(tmp_path, monkeypatch, definition):
    """The portal composition with the real Docs reader, one signed-in owner and a published
    template; yields `run(filename, bytes, ocr)` → the task after the real bounded parser child."""
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
    from synwevo_server.app import create_app

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "extract")
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://workplace.testserver")
    api = create_app(str(tmp_path / "data"), role="portal", start_jobs=False)
    ident = api.state.identity
    user = ident.create_user("extract-reader@example.test", "harbor lights guide the evening ferry home", status="active")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    ws = ident.create_workspace("Extract reader fixture", owner_id=user["id"])["id"]
    raw, _ = Sessions(ident.store).create(user["id"], workspace_id=ws, amr="password")
    csrf = {"X-Synwevo-CSRF": "fixture-csrf"}
    with TestClient(api, base_url="http://workplace.testserver") as client:
        client.cookies.set(COOKIE_NAME, raw)
        client.cookies.set(CSRF_COOKIE, "fixture-csrf")

        def op(name, body):
            response = client.post(f"/api/suite/ops/extract.{name}", headers=csrf, json={"workspace_id": ws, "input": body})
            assert response.status_code == 200, response.text
            return response.json()["output"]

        template = op("template.create", {"title": "Reader fixture", "definition": definition})["id"]
        op("template.publish", {"template_id": template, "draft_rev": 1})

        def run(name, data, ocr=False):
            uploaded = client.post("/api/suite/extract/files", params={"workspace_id": ws},
                                   headers={**csrf, "Idempotency-Key": name}, files={"file": (name, data, "application/pdf")})
            assert uploaded.status_code == 200, uploaded.text
            tid = op("task.create", {"template_id": template, "version": 1, "file_id": uploaded.json()["id"], "ocr": ocr})["id"]
            api.state.extract.tick()  # the real bounded parser child
            return op("task.get", {"task_id": tid})

        run.api = api
        yield run


INVOICE_FIELDS = {"fields": [
    {"id": "number", "label": "Number", "rule": {"prefix": "Invoice No:"}},
    {"id": "total", "label": "Total", "type": "decimal", "rule": {"prefix": "Total:"}},
    {"id": "items", "label": "Items", "many": True, "rule": {"prefix": "Item:"}},
]}


def test_damaged_pdf_is_refused_and_harmless_repair_still_reads(tmp_path, monkeypatch):
    """Acceptance defect: a truncated PDF (the first 45 % of a valid invoice) became a normal
    partial result for review, its later lines silently missing. The parser repairs the
    cross-reference table, then finds the later content streams missing; that is a damaged file,
    refused with its own reason. A PDF whose only fault is a cross-reference table the engine
    rebuilds without losing anything is still read."""
    import fitz

    good = _invoice_pdf(fitz)
    # Cut points where the engine still opens the file and reads its first lines (the 45 % cut
    # of this smaller fixture lands where it cannot open the file at all).
    truncated = [good[: int(len(good) * share)] for share in (0.4, 0.55, 0.7)]
    # Harmless repair: every byte offset shifted by a comment after the header, and no xref table.
    shifted = good.replace(b"\n", b"\n% fixture: shifts every offset\n", 1)
    shifted = shifted[: shifted.rfind(b"xref")] + b"%%EOF\n"
    with _extract(tmp_path, monkeypatch, INVOICE_FIELDS) as run:
        for n, data in enumerate(truncated):
            damaged = run(f"invoice-truncated-{n}.pdf", data)
            assert damaged["status"] == "failed" and damaged["error_code"] == "damaged_file", (n, damaged)
            assert damaged["values"] is None and damaged["machine"] is None

        repaired = run("invoice-repaired.pdf", shifted)
        assert repaired["status"] == "waiting_input", repaired["error_code"]
        assert repaired["values"]["number"]["value"] == "INV-2026-0417"
        assert repaired["values"]["total"]["value"] == "269.39"
        assert len(repaired["values"]["items"]["value"]) == 3

        intact = run("invoice.pdf", good)
        assert intact["status"] == "waiting_input" and intact["values"]["total"]["value"] == "269.39"


def _scanned_pdf(fitz, pages) -> bytes:
    """Fixture scan (fictional): each page rendered to a greyscale JPEG and placed as the only
    content of a new page, so the file has no text layer and needs text recognition."""
    out = fitz.open()
    for lines in pages:
        src = fitz.open()
        page = src.new_page(width=595, height=842)
        for n, text in enumerate(lines):
            page.insert_text((60, 70 + 27 * n), text, fontsize=14, fontname="helv")
        image = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY).tobytes("jpeg", jpg_quality=75)
        src.close()
        scan = out.new_page(width=595, height=842)
        scan.insert_image(scan.rect, stream=image)
    data = out.tobytes()
    out.close()
    return data


def test_recognised_scan_accounts_for_every_labelled_item(tmp_path, monkeypatch):
    """Acceptance defect: on a recognised multi-page scan the stacked "Item:" labels were read
    apart from their values; each empty match was skipped without a word and the field read
    "located" with 2 of 38 items. Whatever line order recognition produces, every labelled item
    is either a located value or an unresolved occurrence, and a field short of values is never
    reported as fully located."""
    import fitz
    from synwevo_docs.ingest import DocumentIngest

    if not DocumentIngest().describe()["ocr"]:
        pytest.skip("text recognition is not installed on this machine")
    items = [f"Item: Spare part SP-1{j:02d}, 1 x 2.50 = 2.50" for j in range(12)]
    pages = [["INVOICE", "Invoice No: INV-2026-0533", "Item: Paper A4, 10 x 4.20 = 42.00",
              "Item: Toner cartridge, 2 x 65.00 = 130.00", "Total: 172.00"], ["Continued, page 2", *items]]
    with _extract(tmp_path, monkeypatch, INVOICE_FIELDS) as run:
        task = run("scan-2p.pdf", _scanned_pdf(fitz, pages), ocr=True)
        assert task["status"] == "waiting_input", task["error_code"]
        field = task["values"]["items"]
        counts = field["occurrences"]
        assert counts["labelled"] == 14, counts
        assert counts["located"] + len(field["unresolved"]) == counts["labelled"]
        assert counts["located"] == len(field["value"])
        if counts["located"] < counts["labelled"]:
            assert field["status"] == "incomplete" and field["requires_review"], field["status"]
        else:
            assert len(field["value"]) == 14
        assert task["values"]["number"]["value"] == "INV-2026-0533"
