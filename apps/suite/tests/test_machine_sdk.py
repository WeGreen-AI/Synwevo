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
def _load(name: str):
    spec = importlib.util.spec_from_file_location("suite_sdk_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_fx = _load("test_machine_api")
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


def test_an_integration_follows_what_changed_and_opens_it_through_the_operations(api, monkeypatch):
    from synwevo_docs_sdk.errors import BadRequestError, GoneError
    from synwevo_core.store import now
    from synwevo_suite.common import machine_events
    c = sdk(api, "alice", "tables.read tables.write")
    table = c.invoke("tables.table.create", {"name": "Fixture contacts"})["output"]["id"]
    start = c.events_page(start="now")
    assert start["items"] == [] and start["has_more"] is False
    made = [c.invoke("tables.record.create", {"table_id": table, "cells": {}}, correlation_id="sync-1") for _ in range(3)]

    pages = list(c.iter_event_pages(cursor=start["next_cursor"], page_size=2))
    events = [e for page in pages for e in page["items"]]
    assert [len(page["items"]) for page in pages] == [2, 1] and pages[-1]["has_more"] is False
    assert [e["object"]["id"] for e in events] == [m["output"]["id"] for m in made]
    assert {e["run_id"] for e in events} == {m["run_id"] for m in made} and {e["correlation_id"] for e in events} == {"sync-1"}
    # the event says where; the record is read through its operation, with the identifiers it gave
    first = events[0]
    record = c.invoke("tables.record.get", {"table_id": first["refs"]["table_id"], "record_id": first["object"]["id"]})
    assert record["output"]["id"] == made[0]["output"]["id"]
    assert list(c.iter_events(cursor=pages[-1]["next_cursor"])) == []
    assert [e["type"] for e in c.iter_events(types=["tables.table.changed"])] == ["tables.table.changed"]

    # another credential's cursor, and one kept too long, arrive as the typed errors
    with pytest.raises(BadRequestError) as e:
        sdk(api, "bob", "tables.read").events_page(cursor=start["next_cursor"])
    assert e.value.code == "invalid_cursor" and e.value.request_id
    with pytest.raises(PermissionDeniedError) as e:
        sdk(api, "alice", "tables.write").events_page()
    assert e.value.code == "insufficient_scope"
    monkeypatch.setattr(machine_events, "_clock", lambda: now() + 32 * 24 * 3600)
    with pytest.raises(GoneError) as e:
        c.events_page(cursor=start["next_cursor"])
    assert e.value.code == "cursor_expired" and e.value.details["retention_seconds"] == 30 * 24 * 3600


def test_an_integration_subscribes_and_verifies_what_it_is_pushed(api, monkeypatch):
    """`SuiteClient`'s webhook calls against the real service, deliveries made by the real dispatcher
    to a receiver on 127.0.0.1 (a fixture; allowed for this test only), verified with the SDK's own
    `verify_webhook`."""
    from synwevo_docs_sdk import verify_webhook
    from synwevo_docs_sdk.errors import RateLimitError
    _wh = _load("test_machine_webhooks")
    register(api, client_id="bi-tool", scopes="offline_access " + ALL_SCOPES)
    api.cookies.clear()
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_SUITE_WEBHOOK_ALLOW_HTTP", "1")
    service = api.app.state.suite_webhooks
    service.concurrency = 1
    rx = _wh.Receiver(lambda n: 503 if n == 1 else 200)
    try:
        renewable = _wh.grant(api, "alice", client_id="bi-tool")
        c = SuiteClient("http://workplace.testserver", UserToken(renewable["access"]), http=api, sleep=lambda _: None)
        # a subscription outlives an access token: one that cannot be renewed may not ask for it
        with pytest.raises(PermissionDeniedError) as e:
            sdk(api, "alice", ALL_SCOPES).create_webhook(rx.url, ["tables.*"])
        assert e.value.code == "grant_not_renewable"

        made = c.create_webhook(rx.url, ["tables.*"], description="Fixture sync")
        secret, sid = made["secret"], made["id"]
        assert made["authorized_by_caller"] is True and made["missed_events_cursor"] is None
        with pytest.raises(ConflictError) as e:                                 # a second try after a lost answer
            c.create_webhook(rx.url, ["tables.*"])
        assert e.value.code == "subscription_exists" and e.value.details["id"] == sid
        assert [s["id"] for s in c.list_webhooks()["items"]] == [sid] and "secret" not in c.get_webhook(sid)

        table = c.invoke("tables.table.create", {"name": "Fixture contacts"})["output"]["id"]
        service.tick()                                                          # refused once (503): retried
        row = api.people.store.one("SELECT id FROM suite_webhook_deliveries WHERE subscription_id=?", (sid,))
        api.people.store.execute("UPDATE suite_webhook_deliveries SET next_attempt_at=0 WHERE id=?", (row["id"],))
        service.tick()
        first, second = rx.received
        for request in (first, second):
            event = verify_webhook(request["headers"], request["body"], secret)
            assert (event["type"], event["object"]["id"], event["subscription_id"]) == ("tables.table.changed", table, sid)
        # the same event under the same id; each attempt signed anew
        assert first["headers"]["webhook-id"] == second["headers"]["webhook-id"] == event["id"]
        assert first["headers"]["webhook-signature"] != second["headers"]["webhook-signature"] or \
            first["headers"]["webhook-timestamp"] == second["headers"]["webhook-timestamp"]
        assert [e["id"] for e in c.iter_events(types=["tables.table.changed"])] == [event["id"]], "the feed's id"

        ping = c.ping_webhook(sid)
        assert (ping["status"], ping["last_code"]) == ("delivered", 200)
        assert verify_webhook(rx.received[-1]["headers"], rx.received[-1]["body"], secret)["type"] == "webhook.ping"
        with pytest.raises(RateLimitError) as e:
            c.ping_webhook(sid)
        assert e.value.code == "ping_rate_limited" and e.value.details["retry_after"] > 0

        log = c.webhook_deliveries(sid, status="delivered")
        assert [d["attempts"] for d in log["items"]] == [1, 2] and log["has_more"] is False
        assert c.webhook_delivery(sid, log["items"][-1]["id"])["webhook_id"] == event["id"]
        assert c.redeliver_webhook(sid, log["items"][-1]["id"])["redelivery"] is True

        # what happens while it is stopped is pulled from where it stopped
        paused = c.pause_webhook(sid)
        record = c.invoke("tables.record.create", {"table_id": table, "cells": {}})["output"]["id"]
        resumed = c.resume_webhook(sid)
        assert (paused["status"], resumed["status"]) == ("paused", "active")
        assert [e["object"]["id"] for e in c.iter_events(cursor=resumed["missed_events_cursor"])] == [record]

        rotated = c.rotate_webhook_secret(sid, overlap_s=0)
        assert rotated["secret"] != secret and c.update_webhook(sid, events=["tables.record.changed"],
                                                               version=rotated["version"])["version"] == rotated["version"] + 1
        assert c.delete_webhook(sid) == {"deleted": sid} and c.list_webhooks()["items"] == []
    finally:
        rx.stop()
