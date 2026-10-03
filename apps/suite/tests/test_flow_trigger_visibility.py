"""A flow's event trigger fires only for records its owner may read now.

Reading an application in a workspace is not always reading every record in it: a table and a
record have a reading role, and a dataset is withheld from someone who lost its sources. A personal
flow triggered by `tables.record.changed` used to run for every such event in the workspace — and
was handed the record's id — once its owner could read Tables at all.

Real Tables, DataPrep, Forms and flow engine on the isolated fixture store; fixture accounts. For
each hidden record: no run, no delivery receipt, no budget spent, and neither the record's id nor
the event's appears in any flow table. A record that becomes readable fires from then on; what was
missed while it was hidden is not replayed.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from synwevo_core.apps import Apps
from synwevo_core.flows import Flows


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location("suite_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


Clock = _sibling("test_flows").Clock


class Watch:
    """Bob (an editor of workspace A) and Alice (its owner) each watch one event type."""

    def __init__(self, suite):
        self.suite, self.p, self.store, self.caps = suite, suite.people, suite.people.store, suite.caps
        self.engine = Flows(self.store, self.caps, clock=Clock())

    def call(self, op, payload, who="alice"):
        return self.caps.invoke(op, payload, user_id=self.p.uid(who), workspace_id=self.p.a)["output"]

    def on(self, event_type, who, look="tables.table.list"):
        """A flow of `who` that reads a list when `event_type` happens, given the object's id."""
        uid = self.p.uid(who)
        fid = self.engine.create(user_id=uid, name="Fixture watcher", definition={
            "parameters": {"source": {"type": "string"}},
            "steps": [{"id": "look", "kind": "call", "operation": look, "workspace_id": self.p.a, "input": {}}]})["flow_id"]
        return self.engine.create_trigger(
            fid, user_id=uid, version=1,
            trigger={"kind": "event", "event_type": event_type, "workspace_id": self.p.a,
                     "input": {"source": {"$ref": "event.object_id"}}},
            policy={"tools": [look], "workspaces": [self.p.a], "max_runs": 50, "until_revoked": True})["trigger_id"]

    def tick(self):
        self.engine.tick(limit=50)

    def fired(self, trigger_id):
        """The object ids this trigger's runs were started with, in order."""
        rows = self.store.all("SELECT * FROM flow_runs WHERE trigger_id=? ORDER BY rowid", (trigger_id,))   # as started
        return [json.loads(r["params_json"])["source"] for r in rows]

    def state(self, trigger_id):
        row = self.store.one("SELECT runs_used, error_code FROM flow_triggers WHERE id=?", (trigger_id,))
        receipts = self.store.all("SELECT status, error_code FROM flow_trigger_deliveries WHERE trigger_id=? "
                                  "ORDER BY rowid", (trigger_id,))
        return row["runs_used"], row["error_code"], [(r["status"], r["error_code"]) for r in receipts]

    def flow_rows(self, owner):
        """Everything the flow engine stored for `owner`, as text: what they could read back."""
        uid, out = self.p.uid(owner), []
        for t in self.store.all("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'flow_%'"):
            columns = {c["name"] for c in self.store.all(f"PRAGMA table_info({t['name']})")}
            if "user_id" in columns:
                out += self.store.all(f"SELECT * FROM {t['name']} WHERE user_id=?", (uid,))
            elif "trigger_id" in columns:
                out += self.store.all(f"SELECT * FROM {t['name']} WHERE trigger_id IN "
                                      "(SELECT id FROM flow_triggers WHERE user_id=?)", (uid,))
            elif "run_id" in columns:
                out += self.store.all(f"SELECT * FROM {t['name']} WHERE run_id IN "
                                      "(SELECT id FROM flow_runs WHERE user_id=?)", (uid,))
        return json.dumps(out, ensure_ascii=False, default=str)

    def event_id(self, object_id):
        return self.store.one("SELECT id FROM app_events WHERE object_id=? ORDER BY seq DESC LIMIT 1", (object_id,))["id"]

    def table(self, name, read_role=None):
        tid = self.call("tables.table.create", {"name": name})["id"]
        if read_role:
            self.reading_role(tid, read_role)
        return tid

    def reading_role(self, tid, role):
        rev = self.call("tables.table.get", {"table_id": tid})["schema_rev"]
        self.call("tables.table.update", {"table_id": tid, "schema_rev": rev, "read_role": role})

    def record(self, tid, **extra):
        return self.call("tables.record.create", {"table_id": tid, "cells": {}, **extra})["id"]


@pytest.fixture()
def watch(suite):
    return Watch(suite)


def test_a_table_below_the_owners_reading_role_does_not_fire_their_trigger(watch):
    w = watch
    bob, alice = w.on("tables.record.changed", "bob"), w.on("tables.record.changed", "alice")
    open_, closed = w.table("Fixture contacts"), w.table("Fixture payroll", read_role="admin")
    hidden, plain = w.record(closed), w.record(open_)
    w.tick()
    # Alice may read both tables: her trigger is the proof that both events matched
    assert w.fired(alice) == [hidden, plain]
    assert w.fired(bob) == [plain]
    assert w.state(bob) == (1, None, [("started", None)])
    for secret in (hidden, w.event_id(hidden), closed):
        assert secret not in w.flow_rows("bob"), secret
    # and Bob still cannot read the table itself — Tables is open to him, this table is not
    assert [t["id"] for t in w.caps.invoke("tables.table.list", {}, user_id=w.p.uid("bob"),
                                           workspace_id=w.p.a)["output"]["tables"]] == [open_]


def test_a_record_with_its_own_reading_role_does_not_fire_either(watch):
    w = watch
    bob = w.on("tables.record.changed", "bob")
    table = w.table("Fixture contacts")
    private, plain = w.record(table, read_role="admin"), w.record(table)
    w.tick()
    assert w.fired(bob) == [plain] and w.state(bob) == (1, None, [("started", None)])
    assert private not in w.flow_rows("bob") and w.event_id(private) not in w.flow_rows("bob")


def test_what_becomes_readable_fires_from_then_on_and_is_not_replayed(watch):
    w = watch
    bob = w.on("tables.record.changed", "bob")
    closed = w.table("Fixture payroll", read_role="admin")
    missed = [w.record(closed) for _ in range(3)]
    w.tick()
    assert w.fired(bob) == [] and w.state(bob) == (0, None, [])
    w.reading_role(closed, "editor")                       # now an editor may read it
    w.tick()
    assert w.fired(bob) == [], "what happened while it was hidden is not delivered afterwards"
    later = w.record(closed)
    w.tick()
    assert w.fired(bob) == [later] and w.state(bob) == (1, None, [("started", None)])
    assert not any(r in w.flow_rows("bob") for r in missed)
    # closed again: the next change is not his to know, and what he was told stays as it was
    w.reading_role(closed, "admin")
    again = w.record(closed)
    w.tick()
    assert w.fired(bob) == [later] and again not in w.flow_rows("bob")


def test_a_dataset_whose_source_the_owner_lost_does_not_fire(watch):
    """DataPrep stays open to Bob in the workspace; the dataset came from Forms, which he loses."""
    w, p = watch, watch.p
    apps = Apps(p.store, p.ident)
    bob = w.on("dataprep.dataset.created", "bob", look="dataprep.dataset.list")
    alice = w.on("dataprep.dataset.created", "alice", look="dataprep.dataset.list")
    form = w.call("forms.form.create", {"title": "Fixture intake", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}})
    w.call("forms.form.publish", {"form_id": form["id"], "draft_rev": 1})
    link = w.caps.invoke("forms.request.create", {"form_id": form["id"]}, user_id=p.uid("alice"), workspace_id=p.a,
                         idempotency_key="fixture-request")["output"]["url"]
    sent = w.suite.post(f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}/submit",
                        json={"version": 1, "answers": {"f_amount01": "0001"}, "idempotency_key": "fixture-submit"})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]

    def snapshot(name):
        return w.call("dataprep.dataset.from_forms", {"name": name, "submission_ids": [sub]})["id"]

    apps.revoke(p.a, "forms", p.uid("bob"))                # Bob may no longer read Forms here
    lost = snapshot("While Forms was closed to Bob")
    plain = w.call("dataprep.dataset.from_records", {"name": "Plain rows", "columns": ["id"], "records": [{"id": "1"}]})["id"]
    w.tick()
    assert w.fired(alice) == [lost, plain]
    assert w.fired(bob) == [plain] and w.state(bob) == (1, None, [("started", None)])
    assert lost not in w.flow_rows("bob") and w.event_id(lost) not in w.flow_rows("bob")
    # DataPrep itself is still his: this is not the application being closed
    assert w.caps.check_workspace(p.uid("bob"), "dataprep", p.a, "read") == "editor"

    apps.grant(p.a, "forms", p.uid("bob"))                 # Forms is his again
    w.tick()
    assert w.fired(bob) == [plain], "no replay of the dataset made while its source was closed to him"
    regained = snapshot("After Forms was his again")
    w.tick()
    assert w.fired(bob) == [plain, regained]


def test_losing_the_application_itself_is_still_refused_with_its_reason(watch):
    """Unchanged: when the owner may no longer use the application here at all, the trigger says
    so (a refusal receipt and the trigger's error), as before — that is not a hidden record."""
    w, p = watch, watch.p
    bob = w.on("tables.record.changed", "bob")
    table = w.table("Fixture contacts")
    Apps(p.store, p.ident).revoke(p.a, "tables", p.uid("bob"))
    w.record(table)
    w.tick()
    used, error, receipts = w.state(bob)
    assert w.fired(bob) == [] and used == 0
    assert error == "app_access_denied" and receipts == [("refused", "app_access_denied")]


def test_an_owner_who_is_no_longer_available_is_still_refused_not_skipped(watch):
    """Unchanged as well, for an application that decides per record: a suspended owner's trigger
    records the refusal it always did, rather than treating every event as a hidden record."""
    w, p = watch, watch.p
    bob = w.on("dataprep.dataset.created", "bob", look="dataprep.dataset.list")
    p.store.execute("UPDATE users SET status='suspended' WHERE id=?", (p.uid("bob"),))
    w.call("dataprep.dataset.from_records", {"name": "Plain rows", "columns": ["id"], "records": [{"id": "1"}]})
    w.tick()
    used, error, receipts = w.state(bob)
    assert w.fired(bob) == [] and used == 0 and error is not None
    assert [status for status, _ in receipts] == ["refused"] and receipts[0][1] == error
