"""The capability layer: one entry point, the same answer for every caller.

A fixture application (``fixture``, registered only in these tests) exercises `Capabilities.invoke`
the way every real application does: explicit workspace, live membership and role, validated
input, idempotency, run records, events in the same transaction as the business write.
"""
from __future__ import annotations

import threading

import pytest
from pydantic import BaseModel, Field

from synwevo_core.apps import Apps
from synwevo_core.capabilities import AppManifest, Operation
from synwevo_core.errors import ServiceError

TEXT = {"zh-Hans": "夹具", "zh-Hant": "夾具", "en": "Fixture"}


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=50)


class NoteOut(BaseModel):
    id: str
    text: str


def _setup(c):
    """Register the fixture application in this process and turn it on in both workspaces."""
    store, ident = c.people.store, c.people.ident
    store.execute("CREATE TABLE IF NOT EXISTS fixture_notes (id TEXT PRIMARY KEY, workspace_id TEXT, text TEXT)")
    apps = Apps(store, ident)
    apps.register(app_id="fixture", name="Fixture", icon="doc", launch_url="http://workplace.testserver/fixture")
    for wid in (c.people.a, c.people.b):
        apps.enable_for_workspace(wid, "fixture")
    reg = c.caps.registry
    reg.register_app(AppManifest(id="fixture", name="Fixture", path="/fixture", icon="doc", summary=TEXT))

    def create(call, inp: NoteIn):
        if inp.text == "boom":
            store.execute("INSERT INTO fixture_notes VALUES ('half', ?, 'half')", (call.workspace_id,))
            raise ServiceError(409, "fixture_refused", "夹具拒绝")
        nid = f"n{store.one('SELECT COUNT(*) AS n FROM fixture_notes')['n'] + 1}"
        store.execute("INSERT INTO fixture_notes VALUES (?,?,?)", (nid, call.workspace_id, inp.text))
        call.touches("note", nid)
        call.emit("fixture.note.created", object_kind="note", object_id=nid, data={"text": inp.text})
        return NoteOut(id=nid, text=inp.text)

    def listing(call, inp):
        rows = store.all("SELECT id, text FROM fixture_notes WHERE workspace_id=? ORDER BY id", (call.workspace_id,))
        return {"notes": rows}

    class Empty(BaseModel):
        pass

    def decide(call, inp):
        return {"ok": True}

    def personal(call, inp):
        return {"me": call.user_id}

    def undeclared(call, inp):
        call.emit("fixture.other")
        return {}

    reg.register(Operation(id="fixture.note.create", app="fixture", title=TEXT, summary="Create a note",
                           input=NoteIn, output=NoteOut, handler=create, need="write", side_effect="write",
                           agent="confirm", events=("fixture.note.created",), object_kind="note"))
    reg.register(Operation(id="fixture.note.list", app="fixture", title=TEXT, summary="List notes",
                           input=Empty, handler=listing))
    reg.register(Operation(id="fixture.decide", app="fixture", title=TEXT, summary="A person's decision",
                           input=Empty, handler=decide, need="write", side_effect="write", agent="never",
                           unattended=False))
    reg.register(Operation(id="fixture.me", app="fixture", title=TEXT, summary="Personal", input=Empty,
                           handler=personal, need="personal"))
    reg.register(Operation(id="fixture.bad", app="fixture", title=TEXT, summary="Undeclared event",
                           input=Empty, handler=undeclared, events=("fixture.note.created",)))
    return store


def test_member_writes_in_the_named_workspace_and_the_run_and_event_are_recorded(suite):
    store = _setup(suite)
    p = suite.people
    out = suite.caps.invoke("fixture.note.create", {"text": "hello"}, user_id=p.uid("bob"), workspace_id=p.a)
    assert out["status"] == "succeeded" and out["output"] == {"id": "n1", "text": "hello"}
    run = store.one("SELECT * FROM op_runs WHERE id=?", (out["run_id"],))
    assert run["workspace_id"] == p.a and run["user_id"] == p.uid("bob") and run["object_id"] == "n1"
    ev = store.one("SELECT * FROM app_events WHERE run_id=?", (out["run_id"],))
    assert ev["type"] == "fixture.note.created" and ev["workspace_id"] == p.a and ev["correlation_id"] == out["run_id"]
    audit = store.one("SELECT * FROM audit_events WHERE action='fixture.note.create'")
    assert audit["actor_id"] == p.uid("bob") and audit["workspace_id"] == p.a and audit["object_id"] == "n1"


def test_viewer_outsider_and_missing_workspace_are_refused_before_any_run(suite):
    store = _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.create", {"text": "x"}, user_id=p.uid("vera"), workspace_id=p.a)
    assert e.value.code == "read_only_role"
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.list", {}, user_id=p.uid("out"), workspace_id=p.a)
    assert e.value.code == "app_access_denied"
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.list", {}, user_id=p.uid("bob"), workspace_id=None)
    assert e.value.code == "workspace_required"
    # Alice reads B (viewer there) but cannot write in it, while she can write in A.
    assert suite.caps.invoke("fixture.note.list", {}, user_id=p.uid("alice"), workspace_id=p.b)["status"] == "succeeded"
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.create", {"text": "x"}, user_id=p.uid("alice"), workspace_id=p.b)
    assert e.value.code == "read_only_role"
    assert store.one("SELECT COUNT(*) AS n FROM op_runs WHERE op='fixture.note.create'")["n"] == 0


def test_invalid_input_names_the_field(suite):
    _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.create", {"text": ""}, user_id=p.uid("bob"), workspace_id=p.a)
    assert e.value.status == 422 and e.value.detail["fields"][0]["loc"] == ["text"]


def test_idempotency_replays_the_same_run_and_refuses_changed_parameters(suite):
    store = _setup(suite)
    p = suite.people
    first = suite.caps.invoke("fixture.note.create", {"text": "once"}, user_id=p.uid("bob"), workspace_id=p.a,
                              idempotency_key="k-1")
    again = suite.caps.invoke("fixture.note.create", {"text": "once"}, user_id=p.uid("bob"), workspace_id=p.a,
                              idempotency_key="k-1")
    assert again["replayed"] and again["run_id"] == first["run_id"] and again["output"] == first["output"]
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.create", {"text": "twice"}, user_id=p.uid("bob"), workspace_id=p.a,
                          idempotency_key="k-1")
    assert e.value.code == "idempotency_conflict"
    # The key is the caller's: another person with the same key is a different request.
    other = suite.caps.invoke("fixture.note.create", {"text": "once"}, user_id=p.uid("alice"), workspace_id=p.a,
                              idempotency_key="k-1")
    assert not other["replayed"]
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == 2


def test_concurrent_twins_with_one_key_create_one_record(suite):
    store = _setup(suite)
    p = suite.people
    results, errors = [], []

    def go():
        try:
            results.append(suite.caps.invoke("fixture.note.create", {"text": "race"}, user_id=p.uid("bob"),
                                             workspace_id=p.a, idempotency_key="race-1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len({r["run_id"] for r in results}) == 1
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == 1


def test_a_refused_handler_rolls_back_its_write_and_records_the_failure(suite):
    store = _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.create", {"text": "boom"}, user_id=p.uid("bob"), workspace_id=p.a)
    assert e.value.code == "fixture_refused"
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == 0
    run = store.one("SELECT * FROM op_runs WHERE op='fixture.note.create'")
    assert run["status"] == "failed" and run["error_code"] == "fixture_refused"
    assert store.one("SELECT COUNT(*) AS n FROM app_events")["n"] == 0


def test_person_only_decisions_and_agent_exclusions(suite):
    _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.decide", {}, user_id=p.uid("bob"), workspace_id=p.a, via="flow")
    assert e.value.code == "person_required"
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.decide", {}, user_id=p.uid("bob"), workspace_id=p.a, via="agent")
    assert e.value.code == "not_for_agent"
    assert suite.caps.invoke("fixture.decide", {}, user_id=p.uid("bob"), workspace_id=p.a)["status"] == "succeeded"


def test_loops_stop_at_the_depth_limit(suite):
    _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.list", {}, user_id=p.uid("bob"), workspace_id=p.a, via="flow", depth=9)
    assert e.value.code == "run_depth_exceeded"


def test_personal_operations_take_no_workspace(suite):
    _setup(suite)
    p = suite.people
    assert suite.caps.invoke("fixture.me", {}, user_id=p.uid("out"))["output"] == {"me": p.uid("out")}
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.me", {}, user_id=p.uid("out"), workspace_id=p.a)
    assert e.value.code == "workspace_not_applicable"


def test_undeclared_events_are_a_programming_error(suite):
    _setup(suite)
    p = suite.people
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.bad", {}, user_id=p.uid("bob"), workspace_id=p.a)
    assert e.value.code == "operation_failed"


def test_turning_the_application_off_for_a_workspace_takes_effect_on_the_next_call(suite):
    store = _setup(suite)
    p = suite.people
    Apps(store, p.ident).disable_for_workspace(p.a, "fixture")
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke("fixture.note.list", {}, user_id=p.uid("bob"), workspace_id=p.a)
    assert e.value.code == "app_access_denied"


def test_operation_definitions_refuse_unsafe_agent_policies():
    with pytest.raises(ValueError):
        Operation(id="x.y", app="x", title=TEXT, summary="s", input=NoteIn, handler=lambda c, i: None,
                  need="write", side_effect="write", agent="auto")
    with pytest.raises(ValueError):
        Operation(id="x.y", app="x", title=TEXT, summary="s", input=NoteIn, handler=lambda c, i: None,
                  need="read", side_effect="external", agent="confirm")
    with pytest.raises(ValueError):
        Operation(id="other.y", app="x", title=TEXT, summary="s", input=NoteIn, handler=lambda c, i: None)


def test_two_phase_runs_left_running_become_unknown_not_success(suite):
    store = _setup(suite)
    p = suite.people

    class Empty(BaseModel):
        pass

    def crash(call, inp):
        raise KeyboardInterrupt  # stands in for the process dying mid-call (not an Exception)

    suite.caps.registry.register(Operation(id="fixture.external", app="fixture", title=TEXT, summary="Send",
                                           input=Empty, handler=crash, need="write", side_effect="external",
                                           agent="confirm", transactional=False))
    with pytest.raises(KeyboardInterrupt):
        suite.caps.invoke("fixture.external", {}, user_id=p.uid("bob"), workspace_id=p.a)
    run = store.one("SELECT * FROM op_runs WHERE op='fixture.external'")
    assert run["status"] == "running"
    store.execute("UPDATE op_runs SET updated_at=0 WHERE id=?", (run["id"],))
    assert suite.caps.reconcile_stale(older_than_s=60) == 1
    assert store.one("SELECT status FROM op_runs WHERE id=?", (run["id"],))["status"] == "unknown"


def test_http_surface_uses_the_same_rules(suite):
    _setup(suite)
    p = suite.people
    assert suite.post("/api/suite/ops/fixture.note.list", json={"workspace_id": p.a}).status_code == 401
    h = suite.sign_in("vera")
    r = suite.post("/api/suite/ops/fixture.note.create", json={"workspace_id": p.a, "input": {"text": "x"}}, headers=h)
    assert r.status_code == 403 and r.json()["error"] == "read_only_role"
    # CSRF is required on state-changing calls.
    h = suite.sign_in("bob")
    r = suite.post("/api/suite/ops/fixture.note.create", json={"workspace_id": p.a, "input": {"text": "x"}})
    assert r.status_code == 403 and r.json()["error"] == "csrf_failed"
    r = suite.post("/api/suite/ops/fixture.note.create", json={"workspace_id": p.a, "input": {"text": "x"}},
                   headers={**h, "Idempotency-Key": "h-1"})
    assert r.status_code == 200 and r.json()["status"] == "succeeded"
    again = suite.post("/api/suite/ops/fixture.note.create", json={"workspace_id": p.a, "input": {"text": "x"}},
                       headers={**h, "Idempotency-Key": "h-1"})
    assert again.json()["replayed"] is True
    run_id = r.json()["run_id"]
    assert suite.get(f"/api/suite/runs/{run_id}").json()["object"] == {"kind": "note", "id": "n1"}
    assert [x["id"] for x in suite.get("/api/suite/runs").json()["runs"]] == [run_id]
    # Somebody else's run reads as missing.
    suite.sign_in("alice")
    assert suite.get(f"/api/suite/runs/{run_id}").status_code == 404
    # The per-tab workspace binding does not decide where the call writes: the body names it.
    r = suite.post("/api/suite/ops/fixture.note.list", json={"workspace_id": p.b},
                   headers={**suite.sign_in("alice"), "X-Synwevo-Workspace": p.a})
    assert r.status_code == 200 and r.json()["workspace_id"] == p.b


def test_catalogue_lists_applications_with_the_workspaces_each_can_be_used_in(suite):
    _setup(suite)
    p = suite.people
    suite.sign_in("alice")
    apps = {a["id"]: a for a in suite.get("/api/suite/catalog").json()["apps"]}
    assert {w["id"] for w in apps["fixture"]["workspaces"]} == {p.a, p.b}
    suite.sign_in("out")
    assert "fixture" not in {a["id"] for a in suite.get("/api/suite/catalog").json()["apps"]}
    ops = suite.get("/api/suite/operations", params={"app": "fixture"})
    assert ops.status_code == 404
