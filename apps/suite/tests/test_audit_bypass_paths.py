"""The audit rows of the Suite paths that bypass the capability centre (admin coverage G26,
decision D7 §6, work package WP9).

Routes that move bytes (imports, exports, uploads, downloads) are served twice — under the page's
prefix for a session and under ``/api/suite/v1`` for a delegated credential — and the public
fill-in router, the expiry sweepers and the automation triggers have no call at all. Each writes
its own row through the one writer, `synwevo_core.auth.audit_event`:

* a session's row names the person; a credential's row names the person and the client beside
  them (``meta.delegation``); an idempotent replay of an upload leaves none;
* a filler without an account is ``anonymous`` — the filler key's hash prefix behind a public
  link, ``recipient:<request>`` behind a personal one; a signed-in filler is the person;
* what the system does on its own (an expiry, a reminder, a trigger's start) names the engine,
  whoever's look set it off;
* a row that cannot be written undoes the change it belongs to.

Every row carries identifiers and counts only: no file name, cell, answer, note or token. Real
store, real routes and workers; fixture accounts, files and tokens (the readers are explicit test
doubles over real CSV / text bytes).
"""
from __future__ import annotations

import hashlib
import json
import time

from synwevo_core import auth

from .test_approvals import request as approval_request, stage, template as approval_template
from .test_flows import flow  # noqa: F401 — the fixture
from .test_forms import CSRF, F_AMOUNT, F_FILE, F_NAME, good_answers, op as forms_op, published_form, token_of
from .test_machine_api import ALL_SCOPES, bearer, m, register
from .test_machine_files import CsvReader, put, take

#: Fixture content. Each string below must never reach an audit row.
LEDGER_CSV = b"Code\nCELL-SECRET-00042\n"
INVOICE_CSV = b"code,amount\nCELL-SECRET-00127,123.45\n"
ROWS_CSV = b"id,amount\nCELL-SECRET-001,0.10\n"
NOTE_TXT = b"PRIVATE-SECRET note text"
RECEIPT_PDF = b"%PDF-1.4 ANSWER-SECRET receipt"
SECRETS = ("secret-ledger.csv", "secret-invoice.csv", "secret-rows.csv", "secret-notes.txt", "secret-receipt.pdf",
           "CELL-SECRET", "ANSWER-SECRET", "PRIVATE-SECRET")
EXTRACT_DEFINITION = {"fields": [
    {"id": "code", "label": "Invoice code", "rule": {"kind": "cell", "row": 2, "column": 1}},
    {"id": "amount", "label": "Amount", "type": "decimal", "rule": {"kind": "cell", "row": 2, "column": 2}, "minimum": "0"},
]}


class _TextReader:
    """TEST DOUBLE of the Agent's personal-file reader: plain text only, no scanner."""

    def describe(self):
        return {"context_formats": ["txt"], "scanner": {"status": "not_configured"}}

    def extract_context(self, data, filename):
        return {"status": "ready", "format": "txt", "tables": [],
                "lines": [{"text": data.decode("utf-8"), "source": {"line": 1}}]}


# --------------------------------------------------------------------------------- row helpers
def _meta(row: dict) -> dict:
    return json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]


def _mark(store) -> int:
    return store.one("SELECT COALESCE(MAX(seq), 0) AS n FROM audit_events")["n"]


def _rows(store, mark: int, like: str = "suite.%") -> list[dict]:
    return store.all("SELECT * FROM audit_events WHERE seq>? AND action LIKE ? ORDER BY seq", (mark, like))


def _one(store, mark: int, action: str, like: str = "suite.%") -> dict:
    """Exactly one row since `mark` among the actions `like` names, and it is `action`."""
    rows = _rows(store, mark, like)
    assert [r["action"] for r in rows] == [action], [(r["action"], r["object_kind"], r["object_id"]) for r in rows]
    return rows[0]


def _clean(rows: list[dict], *more: str) -> None:
    text = json.dumps(rows, default=str)
    for secret in SECRETS + more:
        assert secret and secret not in text, secret


# ------------------------------------------------------------------ D7 §6: the routes that move bytes
def test_byte_moving_routes_leave_one_row_on_both_surfaces(suite, monkeypatch):
    p, store = suite.people, suite.people.store
    alice, ws = p.uid("alice"), p.a
    start = _mark(store)
    suite.api.state.extract.reader = CsvReader()
    from synwevo_suite.agent import files as agent_files
    monkeypatch.setattr(agent_files, "run_bounded", lambda fn, args, check, **kw: fn(*args))   # parse in-process
    files = suite.api.state.agent.files
    files.reader = _TextReader()

    def invoke(op, inp, **kw):
        return suite.caps.invoke(op, inp, user_id=alice, workspace_id=ws, **kw)["output"]

    def person(row, request_id):
        """The session path: the person, the workspace, the request — no client beside them."""
        assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", alice, ws)
        assert row["result"] == "ok" and row["request_id"] == request_id
        assert "delegation" not in _meta(row)

    # ---- Tables: an import ticket, then an export download (session)
    suite.sign_in("alice")
    table = invoke("tables.table.create", {"name": "Fixture ledger"})["id"]
    rev = invoke("tables.table.get", {"table_id": table})["schema_rev"]
    invoke("tables.field.save", {"table_id": table, "schema_rev": rev, "field": {"name": "Code", "type": "text"}})
    mark = _mark(store)
    r = suite.post(f"/api/suite/tables/{table}/imports", params={"workspace_id": ws},
                   headers={**CSRF, "X-Request-ID": "ui-tables-up"},
                   files={"file": ("secret-ledger.csv", LEDGER_CSV, "text/csv")})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.tables.upload")
    person(row, "ui-tables-up")
    assert (row["object_kind"], row["object_id"]) == ("tables_import", r.json()["id"])
    assert _meta(row) == {"table_id": table, "format": "csv", "row_count": 1, "via": "ui"}

    export = invoke("tables.export.create", {"table_id": table})
    mark = _mark(store)
    r = suite.get(export["url"], headers={"X-Request-ID": "ui-tables-down"})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.tables.download")
    person(row, "ui-tables-down")
    assert (row["object_kind"], row["object_id"]) == ("tables_export", export["id"])
    assert _meta(row) == {"format": "csv", "via": "ui"}

    # ---- Extract: upload (and its replay), download, export (session)
    mark = _mark(store)
    upload = {"params": {"workspace_id": ws}, "files": {"file": ("secret-invoice.csv", INVOICE_CSV, "text/csv")}}
    r = suite.post("/api/suite/extract/files", headers={**CSRF, "Idempotency-Key": "ui-invoice", "X-Request-ID": "ui-extract-up"}, **upload)
    assert r.status_code == 200, r.text
    ex_file = r.json()["id"]
    row = _one(store, mark, "suite.extract.upload")
    person(row, "ui-extract-up")
    assert (row["object_kind"], row["object_id"]) == ("extract_file", ex_file)
    assert _meta(row) == {"size": len(INVOICE_CSV), "format": "csv", "source": False}
    again = suite.post("/api/suite/extract/files", headers={**CSRF, "Idempotency-Key": "ui-invoice"}, **upload)
    assert again.status_code == 200 and again.json()["id"] == ex_file
    assert len(_rows(store, mark)) == 1, "a replay leaves no second row"

    mark = _mark(store)
    r = suite.get(f"/api/suite/extract/files/{ex_file}", params={"workspace_id": ws}, headers={"X-Request-ID": "ui-extract-down"})
    assert r.status_code == 200 and r.content == INVOICE_CSV
    row = _one(store, mark, "suite.extract.download")
    person(row, "ui-extract-down")
    assert (row["object_kind"], row["object_id"]) == ("extract_file", ex_file)
    assert _meta(row) == {"size": len(INVOICE_CSV)}

    tpl = invoke("extract.template.create", {"title": "Fixture invoice", "definition": EXTRACT_DEFINITION})["id"]
    invoke("extract.template.publish", {"template_id": tpl, "draft_rev": 1})
    task = invoke("extract.task.create", {"template_id": tpl, "version": 1, "file_id": ex_file, "ocr": False})["id"]
    suite.api.state.extract.tick()
    assert invoke("extract.task.get", {"task_id": task})["status"] == "waiting_input"
    invoke("extract.task.confirm", {"task_id": task, "revision": 1, "note": "Checked the fixture"}, idempotency_key="ui-confirm")
    mark = _mark(store)
    r = suite.get(f"/api/suite/extract/tasks/{task}/export", params={"workspace_id": ws, "revision": 2},
                  headers={"X-Request-ID": "ui-extract-export"})
    assert r.status_code == 200, r.text
    # the session's read operation leaves none, so the export is the only row of any action
    row = _one(store, mark, "suite.extract.export", like="%")
    person(row, "ui-extract-export")
    assert (row["object_kind"], row["object_id"]) == ("extract_task", task)
    assert _meta(row) == {"revision": 2, "via": "ui"}

    # ---- DataPrep: upload (and its replay), dataset export, run issues export (session)
    mark = _mark(store)
    upload = {"params": {"workspace_id": ws}, "files": {"file": ("secret-rows.csv", ROWS_CSV, "text/csv")}}
    r = suite.post("/api/suite/dataprep/files", headers={**CSRF, "Idempotency-Key": "ui-rows", "X-Request-ID": "ui-dataprep-up"}, **upload)
    assert r.status_code == 200, r.text
    dp_file = r.json()["id"]
    row = _one(store, mark, "suite.dataprep.upload")
    person(row, "ui-dataprep-up")
    assert (row["object_kind"], row["object_id"]) == ("dataprep_file", dp_file)
    assert _meta(row) == {"size": len(ROWS_CSV), "format": "csv"}
    again = suite.post("/api/suite/dataprep/files", headers={**CSRF, "Idempotency-Key": "ui-rows"}, **upload)
    assert again.status_code == 200 and again.json()["id"] == dp_file
    assert len(_rows(store, mark)) == 1, "a replay leaves no second row"

    dataset = invoke("dataprep.dataset.import", {"file_id": dp_file, "name": "Imported fixture"})["id"]
    mark = _mark(store)
    r = suite.get(f"/api/suite/dataprep/datasets/{dataset}/export", params={"workspace_id": ws, "format": "csv"},
                  headers={"X-Request-ID": "ui-dataprep-export"})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.dataprep.export")
    person(row, "ui-dataprep-export")
    assert (row["object_kind"], row["object_id"]) == ("dataprep_dataset", dataset)
    assert _meta(row) == {"format": "csv", "via": "ui"}

    recipe = invoke("dataprep.recipe.create", {"name": "Fixture recipe", "definition": {"steps": []}})
    invoke("dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1})
    run = invoke("dataprep.run.start", {"recipe_id": recipe["id"], "version": 1, "inputs": {"main": dataset}})
    assert suite.api.state.dataprep.tick()
    mark = _mark(store)
    r = suite.get(f"/api/suite/dataprep/runs/{run['id']}/issues", params={"workspace_id": ws},
                  headers={"X-Request-ID": "ui-dataprep-issues"})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.dataprep.export")
    person(row, "ui-dataprep-issues")
    assert (row["object_kind"], row["object_id"]) == ("dataprep_run", run["id"])
    assert _meta(row) == {"kind": "issues", "count": 0, "via": "ui"}

    # ---- Forms: a submitted attachment downloaded by a member (session)
    form = published_form(suite, ws)
    req = forms_op(suite, "forms.request.create", ws, {"form_id": form})["output"]
    link = token_of(req["url"])
    base = f"/api/suite/public/forms/r/{link}"
    suite.cookies.clear()                                  # the filler has no account
    up = suite.post(base + "/files", data={"field_id": F_FILE},
                    files={"file": ("secret-receipt.pdf", RECEIPT_PDF, "application/pdf")})
    assert up.status_code == 200, up.text
    sent = suite.post(base + "/submit", json={"version": 1, "idempotency_key": "fixture-submit-0001",
                                              "answers": good_answers(**{F_NAME: "ANSWER-SECRET Ann", F_FILE: [up.json()["id"]]})})
    assert sent.status_code == 200, sent.text
    submission = sent.json()["submission_id"]
    suite.sign_in("alice")
    attached = invoke("forms.submission.get", {"submission_id": submission})["files"]
    assert [f["id"] for f in attached] == [up.json()["id"]]
    mark = _mark(store)
    r = suite.get(f"/api/suite/forms/files/{attached[0]['id']}", params={"workspace_id": ws}, headers={"X-Request-ID": "ui-forms-down"})
    assert r.status_code == 200 and r.content == RECEIPT_PDF
    row = _one(store, mark, "suite.forms.download")
    person(row, "ui-forms-down")
    assert (row["object_kind"], row["object_id"]) == ("form_file", attached[0]["id"])
    assert _meta(row) == {"submission_id": submission, "request_id": req["request_id"], "size": len(RECEIPT_PDF)}

    # ---- Agent: a personal file, uploaded (and replayed) and downloaded — no workspace
    mark = _mark(store)
    r = suite.post("/api/suite/agent/files", files={"file": ("secret-notes.txt", NOTE_TXT)},
                   headers={**CSRF, "Idempotency-Key": "ui-notes", "X-Request-ID": "ui-agent-up"})
    assert r.status_code == 200, r.text
    personal = r.json()["id"]
    row = _one(store, mark, "suite.agent.file.upload")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", alice, None)
    assert row["request_id"] == "ui-agent-up" and row["result"] == "ok"
    assert (row["object_kind"], row["object_id"]) == ("agent_file", personal)
    assert _meta(row) == {"size": len(NOTE_TXT)}
    again = suite.post("/api/suite/agent/files", files={"file": ("secret-notes.txt", NOTE_TXT)},
                       headers={**CSRF, "Idempotency-Key": "ui-notes"})
    assert again.status_code == 200 and again.json()["id"] == personal
    assert len(_rows(store, mark)) == 1, "a replay leaves no second row"
    files.tick()
    assert files.get(alice, personal)["status"] == "ready"
    mark = _mark(store)
    r = suite.get(f"/api/suite/agent/files/{personal}/download", headers={"X-Request-ID": "ui-agent-down"})
    assert r.status_code == 200 and r.content == NOTE_TXT
    row = _one(store, mark, "suite.agent.file.download")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", alice, None)
    assert row["request_id"] == "ui-agent-down" and (row["object_kind"], row["object_id"]) == ("agent_file", personal)
    assert _meta(row) == {"size": len(NOTE_TXT)}

    # ================================================================ the same routes, for a credential
    register(suite)
    suite.cookies.clear()
    auth_ = bearer(suite, "alice", ALL_SCOPES)
    token = auth_["Authorization"].split()[1]

    def rid(name):
        return {**auth_, "X-Request-ID": name}

    def client(row, request_id, *, via: bool = True) -> dict:
        """The machine path: the person acts, the client is named beside them; `via` says whether
        this row's meta carries the surface (the upload and download rows name the client alone)."""
        assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", alice, ws)
        assert row["result"] == "ok" and row["request_id"] == request_id
        meta = _meta(row)
        assert meta["delegation"]["kind"] == "oauth_client" and meta["delegation"]["client_id"] == "crm-sync"
        assert meta["delegation"]["grant"]
        if via:
            assert meta["via"] == "api"
        return meta

    # Tables
    mark = _mark(store)
    ticket = put(suite, rid("api-tables-up"), f"/tables/{table}/imports", "secret-ledger.csv", LEDGER_CSV).json()
    row = _one(store, mark, "suite.tables.upload")
    meta = client(row, "api-tables-up")
    assert (row["object_kind"], row["object_id"]) == ("tables_import", ticket["id"])
    assert (meta["table_id"], meta["format"], meta["row_count"]) == (table, "csv", 1)
    export = m(suite, auth_, "tables.export.create", {"table_id": table}, key="api-export")["output"]
    mark = _mark(store)
    take(suite, rid("api-tables-down"), f"/tables/exports/{export['id']}")
    row = _one(store, mark, "suite.tables.download")
    meta = client(row, "api-tables-down")
    assert row["object_kind"] == "tables_export" and row["object_id"].endswith(export["id"])
    assert meta["format"] == "csv"

    # Extract: upload, replay, download — and the export, audited by the operation it invokes
    mark = _mark(store)
    mine = put(suite, rid("api-extract-up"), "/extract/files", "secret-invoice.csv", INVOICE_CSV, key="api-invoice").json()
    row = _one(store, mark, "suite.extract.upload")
    meta = client(row, "api-extract-up", via=False)
    assert (row["object_kind"], row["object_id"]) == ("extract_file", mine["id"])
    assert (meta["size"], meta["format"], meta["source"]) == (len(INVOICE_CSV), "csv", False)
    assert put(suite, auth_, "/extract/files", "secret-invoice.csv", INVOICE_CSV, key="api-invoice").json()["id"] == mine["id"]
    assert len(_rows(store, mark)) == 1, "a replay leaves no second row"
    mark = _mark(store)
    assert take(suite, rid("api-extract-down"), f"/extract/files/{mine['id']}").content == INVOICE_CSV
    row = _one(store, mark, "suite.extract.download")
    meta = client(row, "api-extract-down", via=False)
    assert (row["object_kind"], row["object_id"]) == ("extract_file", mine["id"]) and meta["size"] == len(INVOICE_CSV)
    mark = _mark(store)
    assert take(suite, rid("api-extract-export"), f"/extract/tasks/{task}/export", revision=2).json()["values"]
    rows = _rows(store, mark, like="%")
    assert [x["action"] for x in rows] == ["extract.task.result"], [(x["action"], x["object_id"]) for x in rows]
    meta = client(rows[0], "api-extract-export")
    assert meta["operation"] == "extract.task.result" and meta["run_id"]
    assert store.one("SELECT via FROM op_runs WHERE id=?", (meta["run_id"],))["via"] == "api"

    # DataPrep: upload, replay, dataset export, run issues export
    mark = _mark(store)
    mine = put(suite, rid("api-dataprep-up"), "/dataprep/files", "secret-rows.csv", ROWS_CSV, key="api-rows").json()
    row = _one(store, mark, "suite.dataprep.upload")
    meta = client(row, "api-dataprep-up", via=False)
    assert (row["object_kind"], row["object_id"]) == ("dataprep_file", mine["id"])
    assert (meta["size"], meta["format"]) == (len(ROWS_CSV), "csv")
    assert put(suite, auth_, "/dataprep/files", "secret-rows.csv", ROWS_CSV, key="api-rows").json()["id"] == mine["id"]
    assert len(_rows(store, mark)) == 1, "a replay leaves no second row"
    mark = _mark(store)
    take(suite, rid("api-dataprep-export"), f"/dataprep/datasets/{dataset}/export", format="json")
    row = _one(store, mark, "suite.dataprep.export")
    meta = client(row, "api-dataprep-export")
    assert (row["object_kind"], row["object_id"]) == ("dataprep_dataset", dataset) and meta["format"] == "json"
    mark = _mark(store)
    take(suite, rid("api-dataprep-issues"), f"/dataprep/runs/{run['id']}/issues")
    row = _one(store, mark, "suite.dataprep.export")
    meta = client(row, "api-dataprep-issues")
    assert (row["object_kind"], row["object_id"]) == ("dataprep_run", run["id"])
    assert (meta["kind"], meta["count"]) == ("issues", 0)

    # Forms: the attachment
    mark = _mark(store)
    assert take(suite, rid("api-forms-down"), f"/forms/files/{attached[0]['id']}").content == RECEIPT_PDF
    row = _one(store, mark, "suite.forms.download")
    meta = client(row, "api-forms-down", via=False)
    assert (row["object_kind"], row["object_id"]) == ("form_file", attached[0]["id"])
    assert (meta["submission_id"], meta["request_id"], meta["size"]) == (submission, req["request_id"], len(RECEIPT_PDF))

    # Nothing of the content, the link or the token in any row written here.
    _clean(_rows(store, start), token, link)


# ------------------------------------------------------------------ D7 §6: fillers without an account
def test_public_fills_are_anonymous_rows_and_drafts_are_not(suite):
    p, store = suite.people, suite.people.store
    ws = p.a
    start = _mark(store)
    suite.sign_in("bob")
    form = published_form(suite, ws)
    public = token_of(forms_op(suite, "forms.public_link.set", ws, {"form_id": form, "enabled": True})["output"]["url"])
    public_request = store.one("SELECT public_request_id AS id FROM forms WHERE id=?", (form,))["id"]
    base = f"/api/suite/public/forms/r/{public}"
    suite.cookies.clear()
    key = "a" * 32
    ann = {"X-Synwevo-Filler": key, **CSRF}
    filler = ("pub:" + hashlib.sha256(key.encode()).hexdigest())[:24]
    assert filler.startswith("pub:") and len(filler) == 24

    def anonymous(row, request_id) -> dict:
        assert (row["actor_kind"], row["actor_id"]) == ("anonymous", filler)
        assert row["workspace_id"] == ws and row["request_id"] == request_id and row["result"] == "ok"
        meta = _meta(row)
        assert (meta["request_id"], meta["form_id"], meta["kind"]) == (public_request, form, "public")
        assert "delegation" not in meta
        return meta

    # A draft is nobody's business: no row.
    mark = _mark(store)
    d = suite.put(base + "/draft", json={"answers": {F_NAME: "ANSWER-SECRET half"}, "step": 0, "rev": 0}, headers=ann)
    assert d.status_code == 200 and d.json()["rev"] == 1, d.text
    assert _rows(store, mark, like="%") == []

    # An upload: one row, the filler's hash prefix as the actor.
    mark = _mark(store)
    up = suite.post(base + "/files", data={"field_id": F_FILE}, headers={**ann, "X-Request-ID": "pub-up-1"},
                    files={"file": ("secret-receipt.pdf", RECEIPT_PDF, "application/pdf")})
    assert up.status_code == 200, up.text
    kept = up.json()["id"]
    row = _one(store, mark, "suite.forms.file.upload")
    meta = anonymous(row, "pub-up-1")
    assert (row["object_kind"], row["object_id"]) == ("form_file", kept)
    assert (meta["field_id"], meta["size"]) == (F_FILE, len(RECEIPT_PDF))

    # A second one, discarded.
    extra = suite.post(base + "/files", data={"field_id": F_FILE}, headers={**ann, "X-Request-ID": "pub-up-2"},
                       files={"file": ("secret-receipt.pdf", RECEIPT_PDF, "application/pdf")}).json()["id"]
    mark = _mark(store)
    gone = suite.delete(base + f"/files/{extra}", headers={**ann, "X-Request-ID": "pub-discard"})
    assert gone.status_code == 200 and gone.json()["state"] == "discarded", gone.text
    row = _one(store, mark, "suite.forms.file.discard")
    anonymous(row, "pub-discard")
    assert (row["object_kind"], row["object_id"]) == ("form_file", extra)

    # The submission: counts and ids, never the answers; a replay of the key leaves no second row.
    mark = _mark(store)
    body = {"version": 1, "idempotency_key": "pub-submit-0001",
            "answers": good_answers(**{F_NAME: "ANSWER-SECRET Ann", F_FILE: [kept]})}
    sent = suite.post(base + "/submit", json=body, headers={**ann, "X-Request-ID": "pub-submit"})
    assert sent.status_code == 200, sent.text
    sid = sent.json()["submission_id"]
    row = _one(store, mark, "suite.forms.submission.create")
    meta = anonymous(row, "pub-submit")
    assert (row["object_kind"], row["object_id"]) == ("form_submission", sid)
    assert meta == {"request_id": public_request, "form_id": form, "kind": "public", "version": 1, "revision": 1, "files": 1}
    assert suite.post(base + "/submit", json=body, headers=ann).json()["replayed"] is True
    assert len(_rows(store, mark, like="%")) == 1

    # Changes requested by a member, answered by the same filler: a resubmission, revision 2.
    suite.sign_in("bob")
    forms_op(suite, "forms.submission.request_changes", ws, {"submission_id": sid, "note": "Please check",
                                                            "items": [{"field_id": F_AMOUNT, "note": "Amount looks wrong"}]})
    suite.cookies.clear()
    mark = _mark(store)
    again = suite.post(base + "/submit", headers={**ann, "X-Request-ID": "pub-resubmit"},
                       json={"version": 1, "idempotency_key": "pub-submit-0002",
                             "answers": good_answers(**{F_NAME: "ANSWER-SECRET Ann", F_AMOUNT: "15", F_FILE: [kept]})})
    assert again.status_code == 200 and again.json()["revision"] == 2, again.text
    row = _one(store, mark, "suite.forms.submission.resubmit")
    meta = anonymous(row, "pub-resubmit")
    assert (row["object_kind"], row["object_id"]) == ("form_submission", again.json()["submission_id"])
    assert (meta["version"], meta["revision"], meta["files"]) == (1, 2, 1)

    # A personal link filled signed out: the request it was sent for names the filler.
    suite.sign_in("bob")
    personal = forms_op(suite, "forms.request.create", ws, {"form_id": form, "recipient": {
        "email": "carol@outside.example", "name": "Carol Fixture"}})["output"]
    suite.cookies.clear()
    mark = _mark(store)
    r = suite.post(f"/api/suite/public/forms/r/{token_of(personal['url'])}/submit", headers={**CSRF, "X-Request-ID": "personal-submit"},
                   json={"version": 1, "idempotency_key": "personal-submit-0001",
                         "answers": good_answers(**{F_NAME: "ANSWER-SECRET Carol"})})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.forms.submission.create")
    assert (row["actor_kind"], row["actor_id"]) == ("anonymous", "recipient:" + personal["request_id"])
    assert row["workspace_id"] == ws and row["request_id"] == "personal-submit"
    assert _meta(row) == {"request_id": personal["request_id"], "form_id": form, "kind": "personal",
                          "version": 1, "revision": 1, "files": 0}

    # A link that requires sign-in, filled by its addressee: the person is the actor.
    suite.sign_in("bob")
    addressed = forms_op(suite, "forms.request.create", ws, {"form_id": form, "require_sign_in": True,
                                                            "recipient": {"email": "vera@example.test"}})["output"]
    suite.sign_in("vera")
    vbase = f"/api/suite/public/forms/r/{token_of(addressed['url'])}"
    mark = _mark(store)
    up = suite.post(vbase + "/files", data={"field_id": F_FILE}, headers={**CSRF, "X-Request-ID": "vera-up"},
                    files={"file": ("secret-receipt.pdf", RECEIPT_PDF, "application/pdf")})
    assert up.status_code == 200, up.text
    row = _one(store, mark, "suite.forms.file.upload")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", p.uid("vera"), ws)
    assert row["request_id"] == "vera-up" and _meta(row)["request_id"] == addressed["request_id"]
    mark = _mark(store)
    r = suite.post(vbase + "/submit", headers={**CSRF, "X-Request-ID": "vera-submit"},
                   json={"version": 1, "idempotency_key": "vera-submit-0001",
                         "answers": good_answers(**{F_NAME: "ANSWER-SECRET Vera", F_FILE: [up.json()["id"]]})})
    assert r.status_code == 200, r.text
    row = _one(store, mark, "suite.forms.submission.create")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("user", p.uid("vera"), ws)
    assert row["request_id"] == "vera-submit" and (row["object_kind"], row["object_id"]) == ("form_submission", r.json()["submission_id"])
    meta = _meta(row)
    assert (meta["kind"], meta["revision"], meta["files"]) == ("personal", 1, 1) and "delegation" not in meta

    _clean(_rows(store, start), public, token_of(personal["url"]), token_of(addressed["url"]), key,
           "Carol Fixture", "carol@outside.example", "Please check", "Amount looks wrong", "Bob  Smith")


# ------------------------------------------------------------------ D7 §6: what the system does on its own
def test_system_transitions_name_the_engine(suite):
    p, store = suite.people, suite.people.store
    ws = p.a
    start = _mark(store)

    # Forms: a request past its due time expires when anyone looks — the row is the system's,
    # under the look's request id — and the sweeper does the same for the ones nobody looks at.
    suite.sign_in("bob")
    form = published_form(suite, ws)
    due = [forms_op(suite, "forms.request.create", ws, {"form_id": form, "due_at": time.time() + 3600})["output"] for _ in range(2)]
    for r in due:
        store.execute("UPDATE form_requests SET due_at=? WHERE id=?", (time.time() - 1, r["request_id"]))
    suite.sign_in("vera")
    mark = _mark(store)
    look = suite.get(f"/api/suite/public/forms/r/{token_of(due[0]['url'])}", headers={"X-Request-ID": "vera-look"})
    assert look.status_code == 200 and look.json()["closed"]["code"] == "link_expired", look.text
    row = _one(store, mark, "suite.forms.request.expire", like="%")
    assert (row["actor_kind"], row["actor_id"]) == ("system", "forms-expiry")
    assert row["workspace_id"] == ws and row["request_id"] == "vera-look" and row["result"] == "ok"
    assert (row["object_kind"], row["object_id"]) == ("form_request", due[0]["request_id"])
    assert _meta(row) == {"form_id": form, "before": {"status": "open"}, "after": {"status": "expired"}, "changed": ["status"]}
    assert suite.get(f"/api/suite/public/forms/r/{token_of(due[0]['url'])}").json()["closed"]["code"] == "link_expired"
    assert len(_rows(store, mark, like="%")) == 1, "already expired: no second row"
    mark = _mark(store)
    assert suite.api.state.forms.sweep_expired() == 1
    row = _one(store, mark, "suite.forms.request.expire", like="%")
    assert (row["actor_kind"], row["actor_id"], row["request_id"], row["ip"]) == ("system", "forms-expiry", None, None)
    assert (row["object_kind"], row["object_id"], row["workspace_id"]) == ("form_request", due[1]["request_id"], ws)
    assert _meta(row)["changed"] == ["status"]
    assert suite.api.state.forms.sweep_expired() == 0 and len(_rows(store, mark, like="%")) == 1

    # Approvals: the sweeper's reminder, outside any call, names the engine.
    t = approval_template(suite, [stage(suite, due={"hours": 1, "remind": True})])
    r = approval_request(suite, t)
    assert r["status"] == "in_review", r["status"]
    engine = suite.api.state.approvals
    mark = _mark(store)
    engine.sweep(r["stages"][0]["due_at"] + 1)
    row = _one(store, mark, "suite.approvals.stage.reminder")
    assert (row["actor_kind"], row["actor_id"], row["request_id"]) == ("system", "approvals-engine", None)
    assert row["workspace_id"] == ws and row["result"] == "ok"
    assert (row["object_kind"], row["object_id"]) == ("approval_request", r["id"])
    meta = _meta(row)
    assert set(meta) == {"revision", "state_version", "status"}
    assert (meta["revision"], meta["status"]) == (r["revision"], "in_review") and meta["state_version"] >= r["state_version"]
    assert store.one("SELECT COUNT(*) AS n FROM app_events WHERE object_id=? AND type='approvals.stage.reminder'", (r["id"],))["n"] == 1
    engine.sweep(r["stages"][0]["due_at"] + 2)
    assert len(_rows(store, mark)) == 1, "reminded once: no second row"

    _clean(_rows(store, start), token_of(due[0]["url"]), token_of(due[1]["url"]), "Fixture claim", "Invoice fixture")


# ------------------------------------------------------------------ D7 §6: a trigger's start
def test_an_automation_trigger_writes_a_system_row_and_a_manual_start_does_not(flow):
    f = flow
    store = f.store
    start = _mark(store)
    fid = f.create([f.write()])

    # A person's own start is the operation's to audit, not the engine's: no trigger row, and no
    # row at all under the run (nothing asked through an operation here).
    mark = _mark(store)
    rid = f.start(fid)
    assert _rows(store, mark, like="suite.agent.flow.%") == []
    assert store.all("SELECT * FROM audit_events WHERE object_id=?", (rid,)) == []

    # An event delivered to a trigger: one system row naming the owner, the trigger and the event.
    tid = f.e.create_trigger(fid, user_id=f.uid, version=1, policy=f.policy(),
                             trigger={"kind": "event", "event_type": "fixtureflow.created", "workspace_id": f.ws})["trigger_id"]
    f.invoke("fixtureflow.create", {"text": "seed"})
    event = store.one("SELECT id FROM app_events WHERE type='fixtureflow.created' ORDER BY seq DESC LIMIT 1")["id"]
    mark = _mark(store)
    f.e.trigger_tick()
    runs = store.all("SELECT id FROM flow_runs WHERE trigger_id=?", (tid,))
    assert len(runs) == 1
    row = _one(store, mark, "suite.agent.flow.trigger", like="%")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"], row["request_id"]) == ("system", "automation", None, None)
    assert (row["object_kind"], row["object_id"], row["result"]) == ("flow_run", runs[0]["id"], "ok")
    assert _meta(row) == {"flow_id": fid, "version": 1, "user_id": f.uid, "trigger_id": tid, "event_id": event, "depth": 1}
    f.e.trigger_tick()
    assert len(_rows(store, mark, like="%")) == 1, "nothing new to deliver: no second row"
    _clean(_rows(store, start), "real stored fixture", "seed")


# ------------------------------------------------------------------ D7 §6: a row that cannot be written
def test_a_failed_audit_undoes_a_public_submit(suite, monkeypatch):
    p, store = suite.people, suite.people.store
    ws = p.a
    suite.sign_in("bob")
    form = published_form(suite, ws)
    req = forms_op(suite, "forms.request.create", ws, {"form_id": form})["output"]
    link = token_of(req["url"])
    base = f"/api/suite/public/forms/r/{link}"
    suite.cookies.clear()
    up = suite.post(base + "/files", data={"field_id": F_FILE}, headers=CSRF,
                    files={"file": ("secret-receipt.pdf", RECEIPT_PDF, "application/pdf")})
    assert up.status_code == 200, up.text
    staged = up.json()["id"]
    body = {"version": 1, "idempotency_key": "fixture-submit-0001",
            "answers": good_answers(**{F_NAME: "ANSWER-SECRET Ann", F_FILE: [staged]})}
    submissions = store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"]
    drafts = store.one("SELECT COUNT(*) AS n FROM form_drafts")["n"]

    original = auth.audit_event

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(auth, "audit_event", fail)
    mark = _mark(store)
    r = suite.post(base + "/submit", json=body, headers={**CSRF, "X-Request-ID": "failed-submit"})
    assert r.status_code >= 500, r.text
    assert "synthetic" not in r.text and r.headers["X-Request-ID"] == "failed-submit"
    # the submission went with the rollback: nothing stored, the file still staged, the request open
    assert store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"] == submissions
    assert store.one("SELECT state, submission_id FROM form_files WHERE id=?", (staged,)) == {"state": "staged", "submission_id": None}
    assert store.one("SELECT status FROM form_requests WHERE id=?", (req["request_id"],))["status"] == "open"
    assert store.one("SELECT COUNT(*) AS n FROM form_drafts")["n"] == drafts
    assert store.one("SELECT COUNT(*) AS n FROM app_events WHERE type LIKE 'forms.submission.%'")["n"] == 0
    assert _rows(store, mark, like="%") == []

    # the writer back: the same submission goes through once, with its row
    monkeypatch.setattr(auth, "audit_event", original)
    mark = _mark(store)
    r = suite.post(base + "/submit", json=body, headers={**CSRF, "X-Request-ID": "retried-submit"})
    assert r.status_code == 200 and r.json()["replayed"] is False, r.text
    sid = r.json()["submission_id"]
    row = _one(store, mark, "suite.forms.submission.create", like="%")
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"]) == ("anonymous", "recipient:" + req["request_id"], ws)
    assert row["request_id"] == "retried-submit" and (row["object_kind"], row["object_id"]) == ("form_submission", sid)
    assert _meta(row) == {"request_id": req["request_id"], "form_id": form, "kind": "personal", "version": 1, "revision": 1, "files": 1}
    assert store.one("SELECT state, submission_id FROM form_files WHERE id=?", (staged,)) == {"state": "attached", "submission_id": sid}
    assert store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"] == submissions + 1
    assert store.one("SELECT status FROM form_requests WHERE id=?", (req["request_id"],))["status"] == "submitted"
    _clean([row], link)
