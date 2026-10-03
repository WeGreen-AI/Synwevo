"""The business applications' event feed for a credential (`GET /api/suite/v1/events`).

Fixture accounts, real tokens, the real gate, capability layer and applications; the events are
the ones the applications write while the tests use them. What each test expects is stated from the
rule — who may learn that something happened, and how much — not read back from the code:

* an item says where to look and nothing of what is there;
* only the credential's workspace, only applications whose read scope it holds, only records its
  person may read now, never the Agent's, spending or consent;
* the cursor is its holder's alone, unreadable and unforgeable, and a narrowed grant sees less
  from it;
* reading continues exactly once, in order, in bounded pages, and a cursor that has waited past
  the retained window is refused rather than continued across a gap.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

from synwevo_core.capabilities import record_event


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location("suite_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_api = _sibling("test_machine_api")
register, bearer, m, refused, ALL_SCOPES = _api.register, _api.bearer, _api.m, _api.refused, _api.ALL_SCOPES
ITEM = {"id", "type", "version", "app", "object", "refs", "occurred_at", "run_id", "correlation_id"}
DAY = 24 * 3600


@pytest.fixture()
def api(suite):
    register(suite)
    suite.cookies.clear()
    return suite


def page(api, auth, expect=200, **query):
    r = api.get("/api/suite/v1/events", params=query, headers=auth)
    assert r.status_code == expect, (r.status_code, r.text)
    return r.json()


def everything(api, auth, **query):
    """Every item from the start (or from `cursor`), page by page, and the cursor at the end."""
    items, cursor = [], query.pop("cursor", None)
    for _ in range(200):
        got = page(api, auth, **({"cursor": cursor} if cursor else {}), **query)
        assert set(got) == {"workspace_id", "items", "next_cursor", "has_more"}
        items += got["items"]
        cursor = got["next_cursor"]
        if not got["has_more"]:
            return items, cursor
    raise AssertionError("the feed never reached its end")


def kinds(items):
    return [(e["type"], e["object"]["id"]) for e in items]


def a_table(api, auth, name, key):
    return m(api, auth, "tables.table.create", {"name": name}, key=key)["output"]["id"]


def a_record(api, auth, table, key, *, correlation_id=None, **extra):
    body = {"correlation_id": correlation_id} if correlation_id else {}
    return m(api, auth, "tables.record.create", {"table_id": table, "cells": {}, **extra}, key=key, **body)


def a_form(api, auth, key="form"):
    form = m(api, auth, "forms.form.create", {"title": "Fixture intake", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}}, key=key)["output"]
    m(api, auth, "forms.form.publish", {"form_id": form["id"], "draft_rev": 1}, key=key + "-publish")
    return form["id"]


# --------------------------------------------------------------------------------- what an item is
def test_an_item_says_where_to_look_and_nothing_of_what_is_there(api):
    p = api.people
    auth = bearer(api, "alice", ALL_SCOPES)
    table = a_table(api, auth, "Fixture payroll", "table")
    made = a_record(api, auth, table, "record", correlation_id="crm-batch-7")
    # an approval, whose events carry a note, assignees and deciders in their data
    definition = {"fields": [{"id": "f_amount", "label": "Amount", "type": "money", "currency": "USD", "required": True}],
                  "stages": [{"id": "s_first", "name": "Independent review", "mode": "all",
                              "approvers": {"users": [p.uid("bob")]}}]}
    t = m(api, auth, "approvals.template.create", {"title": "Fixture expense", "definition": definition},
          key="template")["output"]
    m(api, auth, "approvals.template.publish", {"template_id": t["id"], "draft_rev": 1}, key="publish")
    r = m(api, auth, "approvals.request.create", {"template_id": t["id"], "title": "Fixture claim",
          "values": {"f_amount": "12.50"}, "basis": "Invoice fixture"}, key="claim")["output"]
    task = next(x for x in r["tasks"] if x["assignee_id"] == p.uid("bob"))
    api.caps.invoke("approvals.request.decide", {"request_id": r["id"], "state_version": r["state_version"],
                    "task_id": task["id"], "decision": "approve", "note": "Confidential fixture note"},
                    user_id=p.uid("bob"), workspace_id=p.a)

    got = page(api, auth)
    assert got["workspace_id"] == p.a and got["has_more"] is False
    items = got["items"]
    assert all(set(e) == ITEM and set(e["object"]) == {"kind", "id"} for e in items)
    seen = kinds(items)
    assert ("tables.table.changed", table) in seen and ("tables.record.changed", made["output"]["id"]) in seen
    assert ("approvals.task.decided", r["id"]) in seen and ("approvals.request.approved", r["id"]) in seen
    # in the order they happened
    assert [e["occurred_at"] for e in items] == sorted(e["occurred_at"] for e in items)
    # the identifiers needed to open the record, and only those
    record = next(e for e in items if e["type"] == "tables.record.changed")
    assert record["object"] == {"kind": "table_record", "id": made["output"]["id"]} and record["refs"] == {"table_id": table}
    assert all(e["refs"] == {} for e in items if e["app"] == "approvals")
    # its own call is recognisable; the reviewer's decision in the page is not attributed to anyone
    assert record["run_id"] == made["run_id"] and record["correlation_id"] == "crm-batch-7"
    decided = next(e for e in items if e["type"] == "approvals.task.decided")
    assert decided["run_id"] is None and decided["correlation_id"] is None
    # nothing of the business: no note, no title, no name, no person — the stored data has them
    stored = json.dumps([e["data"] for e in p.store.all("SELECT data FROM app_events WHERE workspace_id=?", (p.a,))],
                        ensure_ascii=False, default=str)
    assert "Confidential fixture note" in stored and p.uid("bob") in stored
    served = json.dumps(got, ensure_ascii=False)
    for secret in ("Confidential fixture note", "Fixture claim", "Fixture payroll", "Invoice fixture", "12.50",
                   p.uid("bob"), p.uid("alice")):
        assert secret not in served, secret


def test_a_run_or_correlation_id_is_shown_only_to_the_client_whose_it_is(api):
    register(api, client_id="bi-tool")
    mine = bearer(api, "alice", "tables.read tables.write")
    other = bearer(api, "alice", "tables.read tables.write", client_id="bi-tool")
    table = a_table(api, mine, "Fixture contacts", "table")
    theirs = a_record(api, other, table, "r1", correlation_id="acme-payroll-2026-09")   # free text of *their* choosing
    own = a_record(api, mine, table, "r1", correlation_id="crm-1")
    api.sign_in("alice")
    by_hand = api.post("/api/suite/ops/tables.record.create", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                       json={"workspace_id": api.people.a, "input": {"table_id": table, "cells": {}}}).json()
    api.cookies.clear()

    def of(auth, record_id):
        return next(e for e in everything(api, auth)[0] if e["object"]["id"] == record_id)

    assert (of(mine, own["output"]["id"])["run_id"], of(mine, own["output"]["id"])["correlation_id"]) == (own["run_id"], "crm-1")
    for foreign in (theirs["output"]["id"], by_hand["output"]["id"]):
        e = of(mine, foreign)
        assert e["run_id"] is None and e["correlation_id"] is None
    assert of(other, theirs["output"]["id"])["correlation_id"] == "acme-payroll-2026-09"
    assert "acme-payroll" not in json.dumps(everything(api, mine)[0])


# --------------------------------------------------------------------------------- whose events
def test_only_the_credentials_workspace(api):
    p = api.people
    in_b = api.caps.invoke("tables.table.create", {"name": "Fixture B ledger"}, user_id=p.uid("oscar"), workspace_id=p.b)
    here = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, here, "Fixture A contacts", "table")
    there = bearer(api, "alice", "tables.read", ws=p.b)             # Alice is a viewer in B

    a_items, a_cursor = everything(api, here)
    b_items, _ = everything(api, there)
    assert kinds(a_items) == [("tables.table.changed", table)]
    assert kinds(b_items) == [("tables.table.changed", in_b["output"]["id"])]
    assert page(api, there)["workspace_id"] == p.b
    # a position read in one workspace is not a position in another
    refused(api.get("/api/suite/v1/events", params={"cursor": a_cursor}, headers=there), 400, "invalid_cursor")


def test_an_application_is_served_only_with_its_read_scope(api):
    writer = bearer(api, "alice", "forms.read forms.write tables.read tables.write")
    form, table = a_form(api, writer), a_table(api, writer, "Fixture contacts", "table")
    everyone, _ = everything(api, writer)
    assert {e["app"] for e in everyone} == {"forms", "tables"}

    forms_only, _ = everything(api, bearer(api, "alice", "forms.read tables.write"))
    assert {e["app"] for e in forms_only} == {"forms"} and ("forms.form.published", form) in kinds(forms_only)
    assert table not in json.dumps(forms_only)
    # write is not read: holding no read scope at all opens nothing
    err = refused(api.get("/api/suite/v1/events", headers=bearer(api, "alice", "forms.write tables.write extract.decide")),
                  403, "insufficient_scope")
    assert err["details"]["any_of"] == ["approvals.read", "dataprep.read", "extract.read", "forms.read", "tables.read"]
    # asking by name for what it does not hold is refused by name; an unknown name is a mistake
    err = refused(api.get("/api/suite/v1/events", params={"types": "forms.form.published,tables.record.changed"},
                          headers=bearer(api, "alice", "forms.read")), 403, "insufficient_scope")
    assert err["details"] == {"required_scope": "tables.read"}
    err = refused(api.get("/api/suite/v1/events", params={"types": "forms.form.published,agent.consent.created"},
                          headers=writer), 400, "invalid_events")
    assert err["field"] == "types" and err["details"]["unknown"] == ["agent.consent.created"]
    only = page(api, writer, types="forms.form.published")["items"]
    assert kinds(only) == [("forms.form.published", form)]


def test_the_agents_spending_consent_and_anything_undeclared_are_never_served(api):
    """Events written straight into the log — the Agent's, Docs', Messages', and under a served
    application an event type it never declared or one about the wrong kind of object."""
    from synwevo_suite.common.machine_events import feeds
    p = api.people
    auth = bearer(api, "alice", ALL_SCOPES)
    form = a_form(api, auth)
    assert sorted(feeds(api.caps.registry)) == ["approvals", "dataprep", "extract", "forms", "tables"]
    with p.store.tx() as t:
        for app, type_, kind in (("agent", "agent.consent.created", "conversation"),
                                 ("agent", "agent.payer_budget.changed", "conversation"),
                                 ("agent", "agent.workspace_policy.changed", "conversation"),
                                 ("agent", "agent.flow.started", "conversation"),
                                 ("docweave", "docs.job.submitted", "job"),
                                 ("messages", "messages.message.recorded", "message"),
                                 ("forms", "forms.secret.internal", "form"),         # not a declared type
                                 ("forms", "forms.form.published", "form_request"),  # declared, wrong object kind
                                 ("agent", "forms.form.published", "form")):         # a served name, another application
            record_event(t, type_=type_, app=app, workspace_id=p.a, object_kind=kind, object_id="obj_fixture",
                         data={"note": "must not be served"}, user_id=p.uid("alice"))
        record_event(t, type_="forms.form.published", app="forms", workspace_id=p.a, object_kind="form", object_id=None)
    assert p.store.one("SELECT COUNT(*) AS n FROM app_events WHERE object_id='obj_fixture'")["n"] == 9
    items, _ = everything(api, auth)
    assert kinds(items) == [("forms.form.published", form)]
    assert "obj_fixture" not in json.dumps(items) and "must not be served" not in json.dumps(items)


def test_a_record_the_person_may_not_read_is_not_an_event_for_them(api):
    """Tables: a table, and a record, have a reading role. Bob is an editor."""
    owner = bearer(api, "alice", "tables.read tables.write")
    bob = bearer(api, "bob", "tables.read tables.write")
    open_ = a_table(api, owner, "Fixture contacts", "open")
    closed = a_table(api, owner, "Fixture payroll", "closed")
    rev = m(api, owner, "tables.table.get", {"table_id": closed})["output"]["schema_rev"]
    m(api, owner, "tables.table.update", {"table_id": closed, "schema_rev": rev, "read_role": "admin"}, key="lock")
    hidden = a_record(api, owner, closed, "in-closed")["output"]["id"]
    plain = a_record(api, owner, open_, "plain")["output"]["id"]
    private = a_record(api, owner, open_, "private", read_role="admin")["output"]["id"]

    mine, cursor = everything(api, bob)
    assert kinds(mine) == [("tables.table.changed", open_), ("tables.record.changed", plain)]
    for unseen in (closed, hidden, private):
        assert unseen not in json.dumps(mine)
    everyones, _ = everything(api, owner)
    assert {closed, hidden, private, open_, plain} <= {e["object"]["id"] for e in everyones}

    # the table is opened to editors: what happens from now on is Bob's to know …
    rev = m(api, owner, "tables.table.get", {"table_id": closed})["output"]["schema_rev"]
    m(api, owner, "tables.table.update", {"table_id": closed, "schema_rev": rev, "read_role": "editor"}, key="unlock")
    later = a_record(api, owner, closed, "later")["output"]["id"]
    since, _ = everything(api, bob, cursor=cursor)
    assert kinds(since) == [("tables.table.changed", closed), ("tables.record.changed", later)]
    # … and so is its history, if he reads again from the start, because he can read the table now
    again, _ = everything(api, bob)
    assert {closed, hidden, later} <= {e["object"]["id"] for e in again} and private not in json.dumps(again)


def test_a_dataset_whose_source_the_credential_may_not_read_is_not_an_event_for_it(api):
    p = api.people
    wide = bearer(api, "bob", "dataprep.read dataprep.write forms.read forms.write")
    narrow = bearer(api, "bob", "dataprep.read dataprep.write")
    form = a_form(api, wide)
    link = m(api, wide, "forms.request.create", {"form_id": form}, key="request")["output"]["url"]
    sent = api.post(f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}/submit",
                    json={"version": 1, "answers": {"f_amount01": "0001"}, "idempotency_key": "fixture-submit"})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]
    snapshot = m(api, wide, "dataprep.dataset.from_forms", {"name": "Form snapshot", "submission_ids": [sub]},
                 key="snapshot")["output"]["id"]
    plain = m(api, wide, "dataprep.dataset.from_records", {"name": "Plain rows", "columns": ["id"],
              "records": [{"id": "1"}]}, key="plain")["output"]["id"]

    both, _ = everything(api, wide, types="dataprep.dataset.created")
    assert kinds(both) == [("dataprep.dataset.created", snapshot), ("dataprep.dataset.created", plain)]
    # the same person through a credential without forms.read: the Forms-derived dataset is not there,
    # and neither are Forms' own events — it holds no forms.read
    less, _ = everything(api, narrow)
    assert kinds(less) == [("dataprep.dataset.created", plain)]
    assert snapshot not in json.dumps(less) and sub not in json.dumps(less)
    # a submission made through the public link is an event too, for those who may read Forms
    forms, _ = everything(api, wide, types="forms.submission.created")
    assert [(e["object"], e["refs"]["form_id"]) for e in forms] == [({"kind": "form_submission", "id": sub}, form)]


def test_an_application_the_person_can_no_longer_use_here_is_simply_absent(api):
    from synwevo_core.apps import Apps
    p = api.people
    auth = bearer(api, "alice", "forms.read forms.write tables.read tables.write")
    form, table = a_form(api, auth), a_table(api, auth, "Fixture contacts", "table")
    _, cursor = everything(api, auth)
    assert Apps(p.store, p.ident).disable_for_workspace(p.a, "tables")
    items, _ = everything(api, auth)
    assert kinds(items) == [("forms.form.published", form)]
    assert table not in json.dumps(items)
    assert page(api, auth, cursor=cursor)["items"] == []


# --------------------------------------------------------------------------------- the cursor
def test_the_cursor_is_its_holders_alone_and_cannot_be_read_or_made(api):
    p = api.people
    register(api, client_id="bi-tool")
    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    first = page(api, auth, limit=1)
    cursor = first["next_cursor"]
    before = _api.counts(p.store, "app_events", "op_runs")

    def bad(value, who=auth):
        err = refused(api.get("/api/suite/v1/events", params={"cursor": value}, headers=who), 400, "invalid_cursor")
        assert err["field"] == "cursor"

    # another client of the same person, another person through the same client, another workspace
    bad(cursor, bearer(api, "alice", "tables.read", client_id="bi-tool"))
    bad(cursor, bearer(api, "bob", "tables.read"))
    bad(cursor, bearer(api, "alice", "tables.read", ws=p.b))
    # altered anywhere, cut short, padded, or something else entirely
    flipped = cursor[:-3] + ("A" if cursor[-3] != "A" else "B") + cursor[-2:]
    for forged in (flipped, cursor[:-4], cursor + "AAAA", "s1.", "s1.AAAA", "nonsense", "s2." + cursor[3:],
                   base64.urlsafe_b64encode(b"e1:5").decode().rstrip("="),            # the Docs feed's form
                   "s1." + base64.urlsafe_b64encode(json.dumps({"q": 0, "t": 0}).encode()).decode().rstrip("="),
                   "s1." + "A" * 400):
        bad(forged)
    # it does not say where in the log it stands, and two cursors for one position differ
    raw = base64.urlsafe_b64decode(cursor[3:] + "=" * (-len(cursor[3:]) % 4))
    assert b'"q"' not in raw and b"{" not in raw[:1]
    assert page(api, auth, limit=1)["next_cursor"] != cursor
    # its holder continues with it — under a token issued later, too
    a_record(api, auth, table, "r1")
    again = page(api, bearer(api, "alice", "tables.read"), cursor=cursor)
    assert [e["type"] for e in again["items"]] == ["tables.record.changed"]
    assert _api.counts(p.store, "app_events", "op_runs")["app_events"] == before["app_events"] + 1


def test_a_narrowed_grant_sees_less_from_the_same_cursor_and_a_widened_one_is_not_backfilled(api):
    both = bearer(api, "alice", "forms.read forms.write tables.read tables.write")
    forms_only = bearer(api, "alice", "forms.read")
    table = a_table(api, both, "Fixture contacts", "table")
    _, cursor = everything(api, both)
    _, narrow_cursor = everything(api, forms_only)
    form = a_form(api, both)
    record = a_record(api, both, table, "r1")["output"]["id"]

    # the cursor was made while the grant included Tables; it is no authority to keep reading Tables
    less, _ = everything(api, forms_only, cursor=cursor)
    assert kinds(less) == [("forms.form.published", form)]
    # the other way round: a cursor from the narrow grant, continued by the wider one, goes on from
    # where it stands — the table made before it is not replayed
    more, _ = everything(api, both, cursor=narrow_cursor)
    assert kinds(more) == [("forms.form.published", form), ("tables.record.changed", record)]


# --------------------------------------------------------------------------------- reading on
def test_reading_continues_exactly_once_in_order_in_bounded_pages(api):
    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    records = [a_record(api, auth, table, f"r{i}")["output"]["id"] for i in range(7)]
    expected = [("tables.table.changed", table)] + [("tables.record.changed", r) for r in records]

    pages, cursor = [], None
    while True:
        got = page(api, auth, limit=3, **({"cursor": cursor} if cursor else {}))
        pages.append(got["items"])
        cursor = got["next_cursor"]
        if not got["has_more"]:
            break
    assert [len(x) for x in pages] == [3, 3, 2]
    assert kinds([e for x in pages for e in x]) == expected
    assert len({e["id"] for x in pages for e in x}) == 8
    # at the end there is nothing more, again and again, until something happens
    for _ in range(2):
        idle = page(api, auth, cursor=cursor)
        assert idle["items"] == [] and idle["has_more"] is False
    new = a_record(api, auth, table, "r-new")["output"]["id"]
    assert kinds(page(api, auth, cursor=cursor)["items"]) == [("tables.record.changed", new)]
    # a page that exactly fits is the end, not a promise of more
    exact = page(api, auth, limit=9)
    assert len(exact["items"]) == 9 and exact["has_more"] is False
    assert len(page(api, auth)["items"]) == 9                                  # the default page holds them all
    for wrong in (0, -1, 501):
        err = refused(api.get("/api/suite/v1/events", params={"limit": wrong}, headers=auth), 400, "invalid_limit")
        assert err["details"] == {"minimum": 1, "maximum": 500}


def test_starting_now_skips_what_already_happened(api):
    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    start = page(api, auth, start="now")
    assert start["items"] == [] and start["has_more"] is False
    record = a_record(api, auth, table, "r1")["output"]["id"]
    assert kinds(page(api, auth, cursor=start["next_cursor"])["items"]) == [("tables.record.changed", record)]
    assert len(page(api, auth, start="oldest")["items"]) == 2
    for query in ({"start": "yesterday"}, {"start": "now", "cursor": start["next_cursor"]}):
        err = refused(api.get("/api/suite/v1/events", params=query, headers=auth), 400, "invalid_start")
        assert err["field"] == "start"


def test_a_page_of_events_the_person_may_not_see_is_bounded_like_any_other(api, monkeypatch):
    from synwevo_suite.common import machine_events
    owner = bearer(api, "alice", "tables.read tables.write")
    bob = bearer(api, "bob", "tables.read")
    closed = a_table(api, owner, "Fixture payroll", "closed")
    rev = m(api, owner, "tables.table.get", {"table_id": closed})["output"]["schema_rev"]
    m(api, owner, "tables.table.update", {"table_id": closed, "schema_rev": rev, "read_role": "admin"}, key="lock")
    for i in range(130):
        a_record(api, owner, closed, f"hidden-{i}")
    open_ = a_table(api, owner, "Fixture contacts", "open")

    monkeypatch.setattr(machine_events, "SCAN_ROWS", 60)
    first = page(api, bob, limit=5)
    # the request read as far as it may and stopped: nothing to show yet, and it says to come back
    assert first["items"] == [] and first["has_more"] is True
    items, _ = everything(api, bob, limit=5)
    assert kinds(items) == [("tables.table.changed", open_)]


# --------------------------------------------------------------------------------- how long a cursor lasts
def test_a_cursor_that_waited_past_the_window_is_refused_not_continued_across_a_gap(api, monkeypatch):
    from synwevo_core.store import now
    from synwevo_suite.common import machine_events
    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    for i in range(3):
        a_record(api, auth, table, f"r{i}")
    at_end = everything(api, auth)[1]
    mid_way = page(api, auth, limit=2)
    assert mid_way["has_more"] is True

    def later(days):
        monkeypatch.setattr(machine_events, "_clock", lambda: now() + days * DAY)

    later(29)                                              # away for less than the window: carries on
    assert page(api, auth, cursor=at_end)["items"] == []
    assert len(page(api, auth, cursor=mid_way["next_cursor"])["items"]) == 2
    later(31)                                              # longer: what came after it may be gone
    for stale in (at_end, mid_way["next_cursor"]):
        err = refused(api.get("/api/suite/v1/events", params={"cursor": stale}, headers=auth), 410, "cursor_expired")
        assert err["details"] == {"retention_seconds": 30 * DAY} and err["field"] == "cursor"
    # starting again reads the window, and those events are now outside it
    fresh = page(api, auth)
    assert fresh["items"] == [] and fresh["has_more"] is False
    assert page(api, auth, cursor=fresh["next_cursor"])["items"] == []
    later(0)
    assert len(page(api, auth)["items"]) == 4


def test_a_cursor_ahead_of_the_log_is_refused(api):
    """The database was restored from an older backup: positions the cursor has passed will be
    used again by new events, so continuing would skip them."""
    p = api.people
    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    a_record(api, auth, table, "r1")
    _, cursor = everything(api, auth)
    last = p.store.one("SELECT MAX(seq) AS s FROM app_events")["s"]
    p.store.execute("DELETE FROM app_events WHERE seq=?", (last,))
    refused(api.get("/api/suite/v1/events", params={"cursor": cursor}, headers=auth), 410, "cursor_expired")
    assert len(page(api, auth)["items"]) == 1


# --------------------------------------------------------------------------------- what is declared
def test_every_event_an_operation_can_emit_is_declared_or_the_application_serves_none(api):
    from synwevo_suite.common.machine_events import event_types, feeds
    reg = api.caps.registry
    served = {e["type"]: e for e in event_types(reg)}
    for op in reg.operations():
        if reg.app(op.app).machine:
            assert set(op.events) <= served.keys(), op.id
        else:
            assert not set(op.events) & served.keys(), op.id
    for e in served.values():
        assert e["scope"] == f"{e['app']}.read" and e["type"].startswith(e["app"] + ".")
        assert e["object_kind"] in reg.app(e["app"]).object_kinds
    # the two applications whose records are not all readable with the application say how they decide
    assert {app for app, feed in feeds(reg).items() if feed.visible is not None} == {"dataprep", "tables"}


def test_the_contract_describes_the_feed_as_it_answers(api):
    from synwevo_suite.common.machine_contract import contract
    from synwevo_suite.common.machine_events import MAX_LIMIT, RETENTION_S, event_types
    doc = contract(api.caps.registry)
    get = doc["paths"]["/api/suite/v1/events"]["get"]
    schemas = doc["components"]["schemas"]
    assert get["x-synwevo-event-types"] == event_types(api.caps.registry)
    assert get["x-synwevo-retention-seconds"] == RETENTION_S
    # any one application's read scope opens it
    assert get["security"] == [{"suiteToken": [f"{app}.read"]} for app in
                               ("approvals", "dataprep", "extract", "forms", "tables")]
    assert {x["name"] for x in get["parameters"] if "name" in x} == {"cursor", "types", "limit", "start"}
    assert next(x for x in get["parameters"] if x.get("name") == "limit")["schema"]["maximum"] == MAX_LIMIT
    assert {"200", "400", "401", "403", "410", "429", "500"} == set(get["responses"])
    assert doc["components"]["responses"]["Gone"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SuiteError"}

    auth = bearer(api, "alice", "tables.read tables.write")
    table = a_table(api, auth, "Fixture contacts", "table")
    a_record(api, auth, table, "r1")
    got = page(api, auth)
    assert set(got) == set(schemas["EventPage"]["required"]) == set(schemas["EventPage"]["properties"])
    event = schemas["Event"]
    assert set(event["required"]) == set(event["properties"]) == ITEM and event["additionalProperties"] is False
    for e in got["items"]:
        assert set(e) == ITEM and e["type"] in event["properties"]["type"]["enum"]
        assert e["app"] in event["properties"]["app"]["enum"] and set(e["object"]) == {"kind", "id"}
        declared = next(x for x in get["x-synwevo-event-types"] if x["type"] == e["type"])
        assert e["object"]["kind"] == declared["object_kind"] and set(e["refs"]) <= set(declared["refs"])
