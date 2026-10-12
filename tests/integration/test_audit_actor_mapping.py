"""The one audit writer (admin coverage G26, decision D7): who a row names for each kind of
caller, how a row written deep in a service still names the request, and that an explicit
actor keeps winning. Isolated stores under tmp_path only."""
from __future__ import annotations

import json

from synwevo_core import context
from synwevo_core.auth import audit_event
from synwevo_core.authority import describe, label
from synwevo_core.store import Store


def rows(store):
    return {r["action"]: dict(r) for r in store.all("SELECT * FROM audit_events")}


def test_a_descriptor_names_the_actor_the_same_way_for_every_kind(tmp_path):
    store = Store(str(tmp_path / "d"))
    delegated = {"kind": "delegated", "client_id": "cl_fixture", "user_id": "u_person", "grant_family": "fam_fixture"}
    service = {"kind": "service_account", "client_id": "svc_fixture"}
    key = {"kind": "api_key", "key_id": "key_fixture"}
    audit_event(store, "fixture.user", actor=describe(user={"id": "u_person"}), object_kind="thing", object_id="t1")
    audit_event(store, "fixture.delegated", actor=describe(delegated), meta={"note": "kept"})
    audit_event(store, "fixture.service", actor=describe(service))
    audit_event(store, "fixture.key", actor=describe(key))
    audit_event(store, "fixture.cli", actor={"kind": "operator", "label": "cli:fixture"})
    audit_event(store, "fixture.operator", actor=describe())
    audit_event(store, "fixture.nobody")
    got = rows(store)
    assert (got["fixture.user"]["actor_kind"], got["fixture.user"]["actor_id"]) == ("user", "u_person")
    assert (got["fixture.user"]["object_kind"], got["fixture.user"]["object_id"]) == ("thing", "t1")
    d = got["fixture.delegated"]
    assert (d["actor_kind"], d["actor_id"]) == ("user", "u_person")
    assert json.loads(d["meta"]) == {"note": "kept", "delegation": {"kind": "oauth_client", "client_id": "cl_fixture", "grant": "fam_fixture"}}
    assert (got["fixture.service"]["actor_kind"], got["fixture.service"]["actor_id"]) == ("machine", label(describe(service)))
    assert got["fixture.service"]["actor_id"].startswith("service:")
    assert (got["fixture.key"]["actor_kind"], got["fixture.key"]["actor_id"]) == ("machine", "api:key_fixture")
    assert (got["fixture.cli"]["actor_kind"], got["fixture.cli"]["actor_id"]) == ("system", "cli:fixture")
    assert (got["fixture.operator"]["actor_kind"], got["fixture.operator"]["actor_id"]) == ("system", "operator")
    assert (got["fixture.nobody"]["actor_kind"], got["fixture.nobody"]["actor_id"]) == ("system", None)
    assert all(r["result"] == "ok" and r["request_id"] is None and r["ip"] is None for r in got.values())


def test_the_requests_principal_and_id_reach_a_row_written_deep_in_a_service(tmp_path):
    """What the gate records for a request (context.principal / context.request_id) is what a
    service's row says, without the request in hand; an explicit actor still wins, and an id
    alone names a person."""
    store = Store(str(tmp_path / "d"))
    p = context.set_principal(describe({"kind": "service_account", "client_id": "svc_deep"}))
    r = context.set_request_id("req_fixture_deep")
    try:
        audit_event(store, "fixture.deep", object_kind="thing", object_id="t2")
        audit_event(store, "fixture.explicit", actor_kind="user", actor_id="u_admin")
        audit_event(store, "fixture.id_only", actor_id="u_admin")
        audit_event(store, "fixture.given_id", request_id="req_given")
    finally:
        context.reset_request_id(r)
        context.reset_principal(p)
    audit_event(store, "fixture.after")
    got = rows(store)
    assert (got["fixture.deep"]["actor_kind"], got["fixture.deep"]["actor_id"]) == ("machine", "service:svc_deep")
    assert got["fixture.deep"]["request_id"] == "req_fixture_deep"
    assert (got["fixture.explicit"]["actor_kind"], got["fixture.explicit"]["actor_id"]) == ("user", "u_admin")
    assert got["fixture.explicit"]["request_id"] == "req_fixture_deep"
    assert (got["fixture.id_only"]["actor_kind"], got["fixture.id_only"]["actor_id"]) == ("user", "u_admin")
    assert got["fixture.given_id"]["request_id"] == "req_given", "a caller that names the request outranks the ambient context"
    assert (got["fixture.after"]["actor_kind"], got["fixture.after"]["actor_id"], got["fixture.after"]["request_id"]) == ("system", None, None)


def test_a_row_shares_the_transaction_of_the_write_it_belongs_to(tmp_path):
    """Inside a Store transaction the row commits or rolls back with the write."""
    store = Store(str(tmp_path / "d"))
    store.execute("CREATE TABLE IF NOT EXISTS fixture_things (id TEXT PRIMARY KEY)")
    try:
        with store.tx():
            store.execute("INSERT INTO fixture_things (id) VALUES ('t1')")
            audit_event(store, "fixture.write", actor_id="u_person", object_kind="thing", object_id="t1")
            raise RuntimeError("fixture: the write fails after its audit")
    except RuntimeError:
        pass
    assert store.one("SELECT COUNT(*) AS n FROM fixture_things")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='fixture.write'")["n"] == 0
    with store.tx():
        store.execute("INSERT INTO fixture_things (id) VALUES ('t2')")
        audit_event(store, "fixture.write", actor_id="u_person", object_kind="thing", object_id="t2")
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='fixture.write' AND object_id='t2'")["n"] == 1
