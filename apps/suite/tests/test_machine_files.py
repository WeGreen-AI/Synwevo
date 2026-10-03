"""The routes that move bytes, for a credential: uploads, downloads and exports under `/api/suite/v1`.

They are the applications' own handlers served a second time, so what is checked here is what a
credential changes — the scope each route names, the one workspace, the per-client retry key, the
credential's reach into other applications — and that everything behind the handler is the page's:
the same upload check and verdict, the same export right of the person, the same private tickets.
Three of the tests are whole flows an integration runs with no browser anywhere in them.
Real store, real upload check and workers; fixture accounts, files and tokens.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location("suite_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_api = _sibling("test_machine_api")
register, bearer, m, refused = _api.register, _api.bearer, _api.m, _api.refused
CSRF = {"X-Synwevo-CSRF": "fixture-csrf"}
CSV = b"code,amount\n00127,123456789.01\n"
CLIENT = "api:" + hashlib.sha256(b"crm-sync").hexdigest()[:16] + ":"


class CsvReader:
    """Test fixture reader: real CSV parsing only, no simulated OCR. At module level because the
    Extract worker hands the reader to a spawned parsing process, which has to import it."""
    def describe(self):
        return {"ocr": False}

    def extract(self, data, filename, *, ocr=False):
        from synwevo_core.member_import import csv_extraction
        return csv_extraction("csv-test-fixture", "csv", data, ",")


@pytest.fixture()
def api(suite):
    register(suite)
    suite.cookies.clear()
    return suite


def put(api, auth, path, name, data, *, key=None, ws=None, expect=200, kind="text/csv"):
    headers = dict(auth)
    if key:
        headers["Idempotency-Key"] = key
    r = api.post("/api/suite/v1" + path, params={"workspace_id": ws or api.people.a}, headers=headers,
                 files={"file": (name, data, kind)})
    assert r.status_code == expect, (path, r.status_code, r.text)
    return r


def take(api, auth, path, *, ws=None, expect=200, **query):
    r = api.get("/api/suite/v1" + path, params={"workspace_id": ws or api.people.a, **query}, headers=auth)
    assert r.status_code == expect, (path, r.status_code, r.text)
    return r


# --------------------------------------------------------------------------------- who may move bytes
def test_every_file_route_needs_a_credential_its_scope_and_the_credentials_workspace(api):
    from synwevo_suite.common.machine_contract import FILE_ROUTES
    p = api.people
    ids = {"file_id": "exfile_000000000000", "table_id": "tbl_000000000000", "export_id": "exp_000000000000",
           "dataset_id": "dset_000000000000", "run_id": "dprun_000000000000", "task_id": "extask_000000000000"}
    every = "forms.read forms.write tables.read tables.write dataprep.read dataprep.write extract.read extract.write"
    for app, method, path, _, scope, _, kind in FILE_ROUTES:
        url = "/api/suite/v1" + path.format(**ids)
        extra = {"revision": 1} if path.endswith("/export") and app == "extract" else {}

        def ask(auth, ws=p.a):
            kw = {"params": {"workspace_id": ws, **extra}, "headers": {**auth, "Idempotency-Key": "k1"}}
            if method == "post":
                kw["files"] = {"file": ("fixture.csv", CSV, "text/csv")}
            return api.request(method.upper(), url, **kw)

        refused(ask({}), 401, "unauthorized")
        api.sign_in("alice")                               # a browser session is not a credential here
        refused(ask(CSRF), 401, "unauthorized")
        api.cookies.clear()
        others = " ".join(s for s in every.split() if s != scope)
        err = refused(ask(bearer(api, "alice", others)), 403, "insufficient_scope")
        assert err["details"]["required_scope"] == scope, path
        refused(ask(bearer(api, "alice", every), ws=p.b), 403, "workspace_mismatch")
        # with the scope, in the workspace, the call reaches the application (which has no such object)
        reached = ask(bearer(api, "alice", every))
        assert reached.status_code not in (401, 500) and reached.json().get("error", {}).get("code") not in (
            "insufficient_scope", "workspace_mismatch", "unauthorized"), (path, reached.text)
    assert {k: v for k, v in _api.counts(p.store, "extract_files", "dataprep_files", "tb_imports").items() if v} == {
        "dataprep_files": 1, "extract_files": 1}           # the two uploads that had everything they needed


def test_a_person_cannot_name_a_clients_upload_key_and_clients_keys_are_their_own(api):
    p = api.people
    register(api, client_id="other-tool")
    auth = bearer(api, "alice", "extract.read extract.write dataprep.read dataprep.write")
    first = put(api, auth, "/extract/files", "invoice.csv", CSV, key="upload-1").json()
    again = put(api, auth, "/extract/files", "invoice.csv", CSV, key="upload-1").json()
    assert again["id"] == first["id"]
    row = p.store.one("SELECT created_by, idempotency_key FROM extract_files WHERE id=?", (first["id"],))
    assert row == {"created_by": p.uid("alice"), "idempotency_key": CLIENT + "upload-1"}
    # same key, other bytes: a conflict, not a second file and not a silent replacement
    clash = put(api, auth, "/extract/files", "invoice.csv", CSV + b"00128,1\n", key="upload-1", expect=409)
    assert clash.json()["error"]["code"] == "idempotency_conflict"
    # another client for the same person, the same key: its own upload
    other = put(api, bearer(api, "alice", "extract.write", client_id="other-tool"), "/extract/files", "invoice.csv",
                CSV, key="upload-1").json()
    assert other["id"] != first["id"]
    # the page may not write into the client's namespace, in either application
    api.sign_in("alice")
    for path in ("/api/suite/extract/files", "/api/suite/dataprep/files"):
        seeded = api.post(path, params={"workspace_id": p.a}, headers={**CSRF, "Idempotency-Key": CLIENT + "upload-2"},
                          files={"file": ("invoice.csv", CSV, "text/csv")})
        assert seeded.status_code == 400 and seeded.json()["error"] == "bad_idempotency_key", (path, seeded.text)
    # and its own keys are untouched by any of this
    page = api.post("/api/suite/extract/files", params={"workspace_id": p.a}, headers={**CSRF, "Idempotency-Key": "upload-1"},
                    files={"file": ("invoice.csv", CSV, "text/csv")})
    assert page.status_code == 200 and page.json()["id"] not in (first["id"], other["id"])
    api.cookies.clear()
    for bad in ("with space", "x" * 129):
        refused(api.post("/api/suite/v1/extract/files", params={"workspace_id": p.a},
                         headers={**auth, "Idempotency-Key": bad}, files={"file": ("invoice.csv", CSV, "text/csv")}),
                400, "bad_idempotency_key")
    assert p.store.one("SELECT COUNT(*) AS n FROM extract_files")["n"] == 3
    assert p.store.one("SELECT COUNT(*) AS n FROM dataprep_files")["n"] == 0


def test_an_upload_gets_the_same_check_and_verdict_as_the_pages(api):
    p = api.people
    auth = bearer(api, "alice", "extract.read extract.write dataprep.read dataprep.write")
    api.sign_in("alice")
    page_extract = api.post("/api/suite/extract/files", params={"workspace_id": p.a}, headers={**CSRF, "Idempotency-Key": "p"},
                            files={"file": ("invoice.csv", CSV, "text/csv")}).json()
    page_prep = api.post("/api/suite/dataprep/files", params={"workspace_id": p.a}, headers={**CSRF, "Idempotency-Key": "p"},
                         files={"file": ("rows.csv", CSV, "text/csv")}).json()
    api.cookies.clear()
    mine_extract = put(api, auth, "/extract/files", "invoice.csv", CSV, key="m").json()
    mine_prep = put(api, auth, "/dataprep/files", "rows.csv", CSV, key="m").json()
    for page, mine in ((page_extract, mine_extract), (page_prep, mine_prep)):
        assert set(page) == set(mine)
        same = {k for k in page if k not in ("id", "created_at", "uploaded_at", "upload_key")}
        assert {k: page[k] for k in same} == {k: mine[k] for k in same}, same
    # each caller is told the key it sent; how a client's key is stored is not its concern
    assert (page_prep["upload_key"], mine_prep["upload_key"]) == ("p", "m")
    assert p.store.one("SELECT upload_key FROM dataprep_files WHERE id=?", (mine_prep["id"],))["upload_key"] == CLIENT + "m"
    verdicts = {r["id"]: r["scan_json"] for r in p.store.all("SELECT id, scan_json FROM extract_files")}
    assert verdicts[page_extract["id"]] == verdicts[mine_extract["id"]] and verdicts[mine_extract["id"]]
    # what the page's route refuses, this one refuses, with the same reason
    for name, data, kind in (("run.exe", b"MZ", "application/octet-stream"), ("empty.csv", b"", "text/csv")):
        api.sign_in("alice")
        page = api.post("/api/suite/extract/files", params={"workspace_id": p.a}, headers={**CSRF, "Idempotency-Key": name},
                        files={"file": (name, data, kind)})
        api.cookies.clear()
        mine = put(api, auth, "/extract/files", name, data, key=name, kind=kind, expect=page.status_code)
        assert page.status_code >= 400 and mine.json()["error"]["code"] == page.json()["error"], name
    assert p.store.one("SELECT COUNT(*) AS n FROM extract_files")["n"] == 2


# --------------------------------------------------------------------------------- whole flows, no browser
def test_extract_from_upload_to_confirmed_export_without_a_session(api):
    p = api.people
    api.api.state.extract.reader = CsvReader()
    auth = bearer(api, "alice", "extract.read extract.write extract.decide")
    template = m(api, auth, "extract.template.create", {"title": "Fixture invoice", "definition": {"fields": [
        {"id": "code", "label": "Invoice code", "rule": {"kind": "cell", "row": 2, "column": 1}},
        {"id": "amount", "label": "Amount", "type": "decimal", "rule": {"kind": "cell", "row": 2, "column": 2},
         "minimum": "0"}]}}, key="tpl")["output"]["id"]
    m(api, auth, "extract.template.publish", {"template_id": template, "draft_rev": 1}, key="tpl-publish")
    file = put(api, auth, "/extract/files", "invoice.csv", CSV, key="invoice-00127").json()
    assert take(api, auth, f"/extract/files/{file['id']}").content == CSV
    task = m(api, auth, "extract.task.create", {"template_id": template, "version": 1, "file_id": file["id"], "ocr": False},
             key="task", expect=202)["output"]["id"]
    api.api.state.extract.tick()
    ready = m(api, auth, "extract.task.get", {"task_id": task})["output"]
    assert ready["status"] == "waiting_input" and ready["values"]["code"]["value"] == "00127"
    # nothing to export until a person's confirmation exists; the credential holds extract.decide for it
    take(api, auth, f"/extract/tasks/{task}/export", revision=1, expect=409)
    m(api, auth, "extract.task.confirm", {"task_id": task, "revision": 1, "note": "Checked the original"}, key="confirm")
    exported = take(api, auth, f"/extract/tasks/{task}/export", revision=2)
    assert exported.headers["cache-control"] == "no-store"
    values = exported.json()["values"]
    assert (values["code"]["value"], values["amount"]["value"]) == ("00127", "123456789.01")
    review = p.store.one("SELECT kind, actor_id FROM extract_reviews WHERE task_id=?", (task,))
    assert review == {"kind": "confirmation", "actor_id": p.uid("alice")}
    # the export read is the credential's own run, attributed to the client
    run = p.store.one("SELECT via, principal FROM op_runs WHERE op='extract.task.result' ORDER BY created_at DESC LIMIT 1")
    assert run["via"] == "api" and json.loads(run["principal"])["client_id"] == "crm-sync"


def test_dataprep_from_upload_to_exported_result_without_a_session(api):
    auth = bearer(api, "bob", "dataprep.read dataprep.write")
    file = put(api, auth, "/dataprep/files", "amounts.csv", b"id,amount\n001,0.10\n002,0.20\n002,0.20\n", key="amounts").json()
    data = m(api, auth, "dataprep.dataset.import", {"file_id": file["id"], "name": "Imported amounts"}, key="import")["output"]
    recipe = m(api, auth, "dataprep.recipe.create", {"name": "Deduplicate", "definition": {"steps": [
        {"id": "dedupe", "type": "dedupe", "columns": ["id"]}]}}, key="recipe")["output"]
    m(api, auth, "dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1}, key="publish")
    started = m(api, auth, "dataprep.run.start", {"recipe_id": recipe["id"], "version": 1, "inputs": {"main": data["id"]}},
                key="run", expect=202)["output"]["id"]
    assert api.api.state.dataprep.tick()
    done = m(api, auth, "dataprep.run.get", {"run_id": started})["output"]
    assert done["status"] in ("succeeded", "partial") and done["output_dataset_id"]
    out = take(api, auth, f"/dataprep/datasets/{done['output_dataset_id']}/export", format="csv")
    import csv
    import io
    rows = list(csv.reader(io.StringIO(out.content.decode("utf-8-sig"))))
    assert rows[0] == ["id", "amount"] and sorted(rows[1:]) == [["001", "0.10"], ["002", "0.20"]]   # zeros kept, one 002
    assert out.headers["content-disposition"].endswith('.csv"') and out.headers["cache-control"] == "no-store"
    issues = take(api, auth, f"/dataprep/runs/{started}/issues")
    assert isinstance(issues.json(), list)


def test_tables_from_import_file_to_export_file_without_a_session(api):
    p = api.people
    auth = bearer(api, "alice", "tables.read tables.write")
    table = m(api, auth, "tables.table.create", {"name": "Fixture contacts"}, key="table")["output"]["id"]
    rev = m(api, auth, "tables.table.get", {"table_id": table})["output"]["schema_rev"]
    field = m(api, auth, "tables.field.save", {"table_id": table, "schema_rev": rev,
              "field": {"name": "Code", "type": "text"}}, key="field")["output"]["field_id"]
    ticket = put(api, auth, f"/tables/{table}/imports", "codes.csv", b"Code\n00042\n00043\n").json()
    assert ticket["row_count"] == 2
    applied = m(api, auth, "tables.import.apply", {"table_id": table, "import_id": ticket["id"],
                "mapping": {"Code": field}, "atomic": False, "preview": False}, key="apply")
    assert applied["output"]["succeeded"] == 2
    export = m(api, auth, "tables.export.create", {"table_id": table}, key="export")["output"]
    data = take(api, auth, f"/tables/exports/{export['id']}")
    assert sorted(data.content.decode("utf-8-sig").split()) == ["00042", "00043", "Code"]
    # a ticket is the person's who made it: Bob's credential, in the same workspace, cannot take it
    take(api, bearer(api, "bob", "tables.read tables.write"), f"/tables/exports/{export['id']}", expect=404)
    assert p.store.one("SELECT user_id FROM tb_exports WHERE id=?", (export["id"],))["user_id"] == p.uid("alice")


# --------------------------------------------------------------------------------- what stays the person's
def test_the_persons_export_right_and_the_credentials_reach_both_apply_to_downloads(api):
    p = api.people
    writer = bearer(api, "bob", "forms.read forms.write dataprep.read dataprep.write extract.read extract.write")
    file = put(api, writer, "/extract/files", "invoice.csv", CSV, key="f").json()
    form = m(api, writer, "forms.form.create", {"title": "Fixture source", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}}, key="form")["output"]
    m(api, writer, "forms.form.publish", {"form_id": form["id"], "draft_rev": 1}, key="form-publish")
    link = m(api, writer, "forms.request.create", {"form_id": form["id"]}, key="request")["output"]["url"]
    sent = api.post(f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}/submit",
                    json={"version": 1, "answers": {"f_amount01": "0001"}, "idempotency_key": "fixture-submit"})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]
    snapshot = m(api, writer, "dataprep.dataset.from_forms", {"name": "Form snapshot", "submission_ids": [sub]},
                 key="snapshot")["output"]["id"]
    assert take(api, writer, f"/dataprep/datasets/{snapshot}/export", format="json").status_code == 200

    # Vera is a viewer without the export right: holding the scope does not let her client take files out
    viewer = bearer(api, "vera", "extract.read dataprep.read forms.read")
    for path, query in ((f"/extract/files/{file['id']}", {}), (f"/dataprep/datasets/{snapshot}/export", {"format": "csv"})):
        denied = take(api, viewer, path, expect=403, **query)
        assert denied.json()["error"]["code"] == "export_not_allowed", path
    p.store.execute("UPDATE memberships SET can_export=1 WHERE workspace_id=? AND user_id=?", (p.a, p.uid("vera")))
    assert take(api, viewer, f"/extract/files/{file['id']}").content == CSV

    # Bob may export, but a credential of his without forms.read may not export what came from Forms
    narrow = bearer(api, "bob", "dataprep.read dataprep.write")
    denied = take(api, narrow, f"/dataprep/datasets/{snapshot}/export", format="csv", expect=403)
    assert denied.json()["error"]["code"] == "insufficient_scope"
    assert denied.json()["error"]["details"] == {"required_scope": "forms.read"}
    assert b"0001" not in denied.content


def test_a_submitted_attachment_is_downloaded_by_a_credential_as_the_person_could(api):
    p = api.people
    auth = bearer(api, "bob", "forms.read forms.write")
    form = m(api, auth, "forms.form.create", {"title": "Receipts", "definition": {"fields": [
        {"id": "f_name0001", "type": "text", "label": "Full name", "required": True, "max_length": 100},
        {"id": "f_file0001", "type": "file", "label": "Receipt", "accept": [".pdf"], "max_files": 1, "max_size_mb": 1},
    ]}}, key="form")["output"]
    m(api, auth, "forms.form.publish", {"form_id": form["id"], "draft_rev": form["draft_rev"]}, key="publish")
    link = m(api, auth, "forms.request.create", {"form_id": form["id"]}, key="request")["output"]["url"]
    base = f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}"
    receipt = b"%PDF-1.4 fixture receipt"
    up = api.post(base + "/files", data={"field_id": "f_file0001"}, files={"file": ("receipt.pdf", receipt, "application/pdf")})
    assert up.status_code == 200, up.text
    sent = api.post(base + "/submit", json={"version": 1, "idempotency_key": "fixture-submit",
                                            "answers": {"f_name0001": "Ann Filler", "f_file0001": [up.json()["id"]]}})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]
    listed = m(api, auth, "forms.submission.get", {"submission_id": sub})["output"]["files"]
    assert [f["filename"] for f in listed] == ["receipt.pdf"]
    got = take(api, auth, f"/forms/files/{listed[0]['id']}")
    assert got.content == receipt and "no-store" in got.headers["cache-control"]
    assert "attachment" in got.headers["content-disposition"]
    # a viewer's client holds forms.read and still may not take the file out
    denied = take(api, bearer(api, "vera", "forms.read"), f"/forms/files/{listed[0]['id']}", expect=403)
    assert denied.json()["error"]["code"] == "export_not_allowed" and receipt not in denied.content
    # and it is this workspace's file only: the owner of another workspace, with every right there,
    # does not find it through a credential for that workspace
    elsewhere = take(api, bearer(api, "oscar", "forms.read", ws=p.b), f"/forms/files/{listed[0]['id']}", ws=p.b, expect=404)
    assert elsewhere.json()["error"]["code"] == "file_not_found"
