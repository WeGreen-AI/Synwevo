"""The Python SDK's `SuiteClient` against the real application (fixture accounts, isolated data).

The SDK's HTTP client is the test client itself, so every request goes through the real gate,
capability layer and applications; the token is minted as the token endpoint mints one. The
refresh-token exchange needs the account service and is covered in `tests/integration`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from synwevo_docs_sdk import SuiteClient, UserToken
from synwevo_docs_sdk.errors import ConflictError, NotFoundError, PermissionDeniedError

# The token and client fixtures of the machine API's own tests, loaded by path: test modules in
# this directory are not importable by name under every way the suites are run.
_spec = importlib.util.spec_from_file_location("suite_machine_fixtures", Path(__file__).with_name("test_machine_api.py"))
_fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fx)
ALL_SCOPES, bearer, register = _fx.ALL_SCOPES, _fx.bearer, _fx.register


def sdk(suite, who, scopes, **kw) -> SuiteClient:
    token = bearer(suite, who, scopes, **kw)["Authorization"].split()[1]
    return SuiteClient("http://workplace.testserver", UserToken(token), http=suite, sleep=lambda _: None)


@pytest.fixture()
def api(suite):
    register(suite)
    suite.cookies.clear()
    return suite


def test_an_integration_builds_a_table_and_reads_it_back(api):
    p = api.people
    c = sdk(api, "alice", "tables.read tables.write")
    who = c.whoami()
    assert (who["user_id"], who["workspace_id"], who["role"]) == (p.uid("alice"), p.a, "owner")
    granted = {o["id"] for o in c.operations("tables")["operations"] if o["granted"]}
    assert {"tables.table.create", "tables.record.create", "tables.record.query"} <= granted

    table = c.invoke("tables.table.create", {"name": "Fixture contacts"})["output"]
    schema = c.invoke("tables.table.get", {"table_id": table["id"]})["output"]
    field = c.invoke("tables.field.save", {"table_id": table["id"], "schema_rev": schema["schema_rev"],
                                           "field": {"name": "Code", "type": "text"}})["output"]["field_id"]
    made = c.invoke("tables.record.create", {"table_id": table["id"], "cells": {field: "00042"}},
                    idempotency_key="contact-00042")
    again = c.invoke("tables.record.create", {"table_id": table["id"], "cells": {field: "00042"}},
                     idempotency_key="contact-00042")
    assert (made.replayed, again.replayed, again["run_id"]) == (False, True, made["run_id"])
    rows = c.invoke("tables.record.query", {"table_id": table["id"]})["output"]
    assert [r["cells"][field] for r in rows["records"]] == ["00042"]            # one record, leading zeros kept
    with pytest.raises(ConflictError) as e:                                     # same key, another input
        c.invoke("tables.record.create", {"table_id": table["id"], "cells": {field: "00043"}},
                 idempotency_key="contact-00042")
    assert e.value.code == "idempotency_conflict" and e.value.request_id

    receipt = c.run(made["run_id"])
    assert receipt["status"] == "succeeded" and receipt["operation"] == "tables.record.create"
    assert c.wait(made["run_id"], timeout=1)["id"] == made["run_id"]
    with pytest.raises(NotFoundError):
        sdk(api, "bob", "tables.read tables.write").run(made["run_id"])
    assert p.store.one("SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?", (table["id"],))["n"] == 1


def test_refusals_arrive_as_the_typed_errors_the_sdk_documents(api):
    reader = sdk(api, "bob", "forms.read")
    with pytest.raises(PermissionDeniedError) as e:
        reader.invoke("forms.form.create", {"title": "No"})
    assert e.value.code == "insufficient_scope" and e.value.details["required_scope"] == "forms.write"
    with pytest.raises(PermissionDeniedError) as e:
        sdk(api, "alice", ALL_SCOPES).invoke("agent.turn.start", {})
    assert e.value.code == "person_required"
    with pytest.raises(NotFoundError) as e:
        reader.invoke("forms.form.nothing")
    assert e.value.code == "unknown_operation"
    assert reader.invoke("forms.form.list", idempotency_key="reads-ignore-keys")["output"]["forms"] == []
    assert reader.invoke("forms.form.list", idempotency_key="reads-ignore-keys").replayed is False


def test_an_integration_moves_files_through_the_sdk(api, tmp_path):
    """Upload, process and take the result out, with the SDK alone: a file on disk in, bytes out."""
    import csv
    import io
    source = tmp_path / "amounts.csv"
    source.write_bytes(b"id,amount\n001,0.10\n002,0.20\n002,0.20\n")
    c = sdk(api, "bob", "dataprep.read dataprep.write tables.read tables.write")
    uploaded = c.dataprep_upload(source, idempotency_key="amounts-2026-10")
    assert uploaded["filename"] == "amounts.csv" and uploaded["upload_key"] == "amounts-2026-10"
    assert c.dataprep_upload(source, idempotency_key="amounts-2026-10")["id"] == uploaded["id"]     # a repeat, not a second file
    data = c.invoke("dataprep.dataset.import", {"file_id": uploaded["id"], "name": "Imported amounts"})["output"]
    recipe = c.invoke("dataprep.recipe.create", {"name": "Deduplicate", "definition": {"steps": [
        {"id": "dedupe", "type": "dedupe", "columns": ["id"]}]}})["output"]
    c.invoke("dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1})
    started = c.invoke("dataprep.run.start", {"recipe_id": recipe["id"], "version": 1, "inputs": {"main": data["id"]}})
    assert started["status"] == "queued" and started.status_code == 202
    assert api.api.state.dataprep.tick()
    done = c.wait(started["run_id"], timeout=5)
    assert done["status"] in ("succeeded", "partial")
    result = c.invoke("dataprep.run.get", {"run_id": started["output"]["id"]})["output"]["output_dataset_id"]
    rows = list(csv.reader(io.StringIO(c.dataprep_export(result).decode("utf-8-sig"))))
    assert rows[0] == ["id", "amount"] and sorted(rows[1:]) == [["001", "0.10"], ["002", "0.20"]]
    assert isinstance(c.dataprep_run_issues(started["output"]["id"]), list)

    # the same result into a table, and out again as the table's own export
    owner = sdk(api, "alice", "tables.read tables.write")
    table = owner.invoke("tables.table.create", {"name": "Amounts"})["output"]["id"]
    rev = owner.invoke("tables.table.get", {"table_id": table})["output"]["schema_rev"]
    field = owner.invoke("tables.field.save", {"table_id": table, "schema_rev": rev,
                                               "field": {"name": "id", "type": "text"}})["output"]["field_id"]
    ticket = owner.tables_import_upload(table, c.dataprep_export(result), filename="result.csv")
    assert ticket["row_count"] == 2
    owner.invoke("tables.import.apply", {"table_id": table, "import_id": ticket["id"], "mapping": {"id": field},
                                         "atomic": False, "preview": False})
    export = owner.invoke("tables.export.create", {"table_id": table})["output"]["id"]
    assert sorted(owner.tables_export_download(export).decode("utf-8-sig").split()) == ["001", "002", "id"]
    with pytest.raises(PermissionDeniedError) as e:        # a client without the scope is told which one
        sdk(api, "alice", "tables.write").tables_export_download(export)
    assert e.value.code == "insufficient_scope" and e.value.details["required_scope"] == "tables.read"
