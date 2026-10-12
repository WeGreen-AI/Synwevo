"""The audit trail of the capability path (admin coverage G26, decision D7 §2, §4 and §5).

Every row here is written by `Capabilities` through the one writer, `synwevo_core.auth.audit_event`:

* the actor is the call's authority descriptor — a person, a person behind a delegated client
  (named in ``meta.delegation``), or a machine credential (``service:…`` / ``api:…``);
* a run that fails leaves one ``result=error`` row, written after its own rollback;
* a machine caller refused before a run exists leaves one ``result=denied`` row per (actor,
  operation) per minute, and a browser session's refusal leaves none;
* an asynchronous operation leaves an acceptance row and, with the terminal `update_run`, a
  completion row under the same ``run_id``.

The fixture application of test_capabilities.py (``fixture.note.*``) carries the page calls and the
handlers written for these cases; the machine paths go through the real ``/api/suite/v1`` route
with the delegated tokens of test_machine_api.py, on a real application's operation
(``forms.form.create``), because the fixture application has no machine API and no token can be
minted for a scope outside the platform's own.
"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from synwevo_core import capabilities
from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError

from .test_capabilities import TEXT, _setup
from .test_machine_api import bearer, register

OP = "forms.form.create"            # a real operation with a machine door; needs forms.write


class Empty(BaseModel):
    pass


def _meta(row: dict) -> dict:
    return json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]


def _audit(store, action: str) -> list[dict]:
    return store.all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (action,))


def _denied(store) -> list[dict]:
    return store.all("SELECT * FROM audit_events WHERE result='denied' ORDER BY seq")


def _op(suite, name: str, handler, *, need="write", side_effect="write", agent="confirm", **kw) -> str:
    """One more operation of the fixture application, registered the way `_setup` registers its own."""
    op = Operation(id=f"fixture.{name}", app="fixture", title=TEXT, summary=f"Audit fixture {name}", input=Empty,
                   handler=handler, need=need, side_effect=side_effect, agent=agent, **kw)
    suite.caps.registry.register(op)
    return op.id


def _machine(suite):
    """The delegated client of test_machine_api, and a client with no cookies in the way."""
    register(suite)
    suite.cookies.clear()


# --------------------------------------------------------------------------------- D7 §2: the actor
def test_the_actor_follows_the_authority_descriptor(suite):
    store = _setup(suite)
    p = suite.people

    # A person in the page: ("user", id); no delegation, no request, and a synchronous run has one
    # row with no phase.
    out = suite.caps.invoke("fixture.note.create", {"text": "hello"}, user_id=p.uid("bob"), workspace_id=p.a, via="ui")
    rows = _audit(store, "fixture.note.create")
    assert len(rows) == 1
    page = rows[0]
    assert (page["actor_kind"], page["actor_id"]) == ("user", p.uid("bob"))
    assert page["result"] == "ok" and page["workspace_id"] == p.a
    assert (page["object_kind"], page["object_id"]) == ("note", "n1")
    assert _meta(page) == {"run_id": out["run_id"], "via": "ui", "operation": "fixture.note.create"}
    assert page["ip"] is None and page["request_id"] is None

    # A delegated client through the machine API: the person acts, the client is named beside
    # them, and the row carries the request's id and address.
    _machine(suite)
    auth = bearer(suite, "bob", "forms.read forms.write")
    r = suite.post(f"/api/suite/v1/ops/{OP}", json={"input": {"title": "Audited"}},
                   headers={**auth, "X-Request-ID": "fixture-audit-1"})
    assert r.status_code == 200, r.text
    rows = _audit(store, OP)
    assert len(rows) == 1
    delegated = rows[0]
    assert (delegated["actor_kind"], delegated["actor_id"]) == ("user", p.uid("bob"))
    assert delegated["result"] == "ok" and delegated["workspace_id"] == p.a
    assert delegated["request_id"] == "fixture-audit-1" and delegated["ip"]
    meta = _meta(delegated)
    assert meta["via"] == "api" and meta["run_id"] == r.json()["run_id"] and meta["operation"] == OP
    assert meta["delegation"]["kind"] == "oauth_client" and meta["delegation"]["client_id"] == "crm-sync"
    assert meta["delegation"]["grant"]
    assert "phase" not in meta
    token = auth["Authorization"].split()[1]
    assert token not in json.dumps(delegated, default=str)

    # A machine credential: ("machine", "service:<client>") / ("machine", "api:<key>"). No service
    # account is admitted to the Suite API (test_machine_api: `service_account_not_supported`,
    # `suite_delegation_only`), so the mapping is checked at the seam the gate uses — the
    # descriptor handed to `invoke` — and read back from the run's stored principal.
    for desc, expected in (({"kind": "service_account", "client_id": "fixture-robot", "tenant": p.a},
                            ("machine", "service:fixture-robot")),
                           ({"kind": "api_key", "key_id": "fixturekey01", "lineage": "fixturekey01", "tenant": p.a},
                            ("machine", "api:fixturekey01"))):
        out = suite.caps.invoke(OP, {"title": f"By {expected[1]}"}, user_id=p.uid("alice"), workspace_id=p.a,
                                via="api", scopes=frozenset({"forms.write"}), principal=desc)
        row = store.one("SELECT * FROM audit_events WHERE action=? AND json_extract(meta, '$.run_id')=?",
                        (OP, out["run_id"]))
        assert (row["actor_kind"], row["actor_id"]) == expected, desc
        meta = _meta(row)
        assert meta["via"] == "api" and "delegation" not in meta
        stored = store.one("SELECT principal FROM op_runs WHERE id=?", (out["run_id"],))["principal"]
        assert json.loads(stored) == desc
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action=?", (OP,))["n"] == 3


# --------------------------------------------------------------------------------- D7 §5: failures
def test_a_failed_run_leaves_one_error_row_after_the_rollback(suite):
    store = _setup(suite)
    p = suite.people

    def conflict(call, inp):
        # a write that must go with the rollback, then the business refusal
        store.execute("INSERT INTO fixture_notes VALUES ('half', ?, 'half')", (call.workspace_id,))
        raise ServiceError(409, "fixture_conflict", "夹具冲突")

    def crash(call, inp):
        raise RuntimeError("fixture crash")

    def upstream(call, inp):
        raise ServiceError(502, "fixture_upstream", "夹具上游失败")

    cases = [
        # (operation, the error the caller sees, its HTTP status)
        (_op(suite, "fail.conflict", conflict), "fixture_conflict", 409),
        (_op(suite, "fail.crash", crash), "operation_failed", 500),
        # two-phase (non-transactional) operations: the failure is recorded on the existing run
        (_op(suite, "fail.external", upstream, side_effect="external", transactional=False), "fixture_upstream", 502),
        (_op(suite, "fail.external_crash", crash, side_effect="external", transactional=False), "operation_failed", 500),
    ]
    for op_id, code, status in cases:
        with pytest.raises(ServiceError) as e:
            suite.caps.invoke(op_id, {}, user_id=p.uid("bob"), workspace_id=p.a)
        assert (e.value.status, e.value.code) == (status, code), op_id
        run = store.one("SELECT * FROM op_runs WHERE op=?", (op_id,))
        assert run["status"] == "failed" and run["error_code"] == code, op_id
        if code == "operation_failed":
            assert e.value.detail["run_id"] == run["id"]
        rows = _audit(store, op_id)
        assert [r["result"] for r in rows] == ["error"], (op_id, rows)
        assert _meta(rows[0]) == {"run_id": run["id"], "via": "ui", "operation": op_id, "error": code, "status": status}
        assert (rows[0]["actor_kind"], rows[0]["actor_id"], rows[0]["workspace_id"]) == ("user", p.uid("bob"), p.a)
    # the transactional handler's own write went with the rollback; the error row did not
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE result='error'")["n"] == len(cases)
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE result='ok'")["n"] == 0

    # A read-only page operation is not audited when it succeeds, and not when it fails either.
    quiet = _op(suite, "fail.read", upstream, need="read", side_effect="none", agent="auto")
    with pytest.raises(ServiceError):
        suite.caps.invoke(quiet, {}, user_id=p.uid("vera"), workspace_id=p.a)
    assert store.one("SELECT status FROM op_runs WHERE op=?", (quiet,))["status"] == "failed"
    assert _audit(store, quiet) == []


# --------------------------------------------------------------------------------- D7 §5: refusals
def test_a_refused_machine_call_leaves_one_denied_row_per_minute(suite, monkeypatch):
    store = _setup(suite)
    p = suite.people
    # The window is process-wide: this test starts it empty and ends with it restored.
    monkeypatch.setattr(capabilities, "_DENIED_RECENT", {})

    def refuse(who="vera", op_id=OP, **kw) -> ServiceError:
        with pytest.raises(ServiceError) as e:
            suite.caps.invoke(op_id, {"title": "No"}, user_id=p.uid(who), workspace_id=p.a, **kw)
        return e.value

    held = frozenset({"forms.write", "tables.write"})
    # Vera is a viewer: the credential holds the scope, the person may not write. Three refusals
    # within the window, one row — with no run, no object, and the refusal's reason.
    for _ in range(3):
        err = refuse(via="api", scopes=held)
        assert (err.status, err.code) == (403, "read_only_role")
    rows = _denied(store)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == OP and (row["actor_kind"], row["actor_id"]) == ("user", p.uid("vera"))
    assert row["workspace_id"] == p.a and row["object_kind"] is None and row["object_id"] is None
    assert _meta(row) == {"via": "api", "operation": OP, "error": "read_only_role", "status": 403}
    assert store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM forms")["n"] == 0

    # Another operation is another key in the window.
    err = refuse(op_id="tables.table.create", via="api", scopes=held)
    assert err.code in ("read_only_role", "manage_required")
    assert [r["action"] for r in _denied(store)] == [OP, "tables.table.create"]

    # The window elapses: the same refusal is recorded again.
    monkeypatch.setattr(capabilities, "DENIED_ROW_INTERVAL_S", 0)
    assert refuse(via="api", scopes=held).code == "read_only_role"
    assert [r["action"] for r in _denied(store)] == [OP, "tables.table.create", OP]

    # A browser session's refusal writes nothing, as before — whatever the reason.
    assert refuse(via="ui").code == "read_only_role"
    assert refuse(who="out", via="ui").code == "app_access_denied"
    assert len(_denied(store)) == 3

    # The same refusal through the machine API: the person as actor, the client named beside
    # them, the request's id and address, and still no run.
    _machine(suite)
    auth = bearer(suite, "vera", "forms.read forms.write")
    r = suite.post(f"/api/suite/v1/ops/{OP}", json={"input": {"title": "No"}},
                   headers={**auth, "X-Request-ID": "fixture-denied-1"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "read_only_role", r.text
    rows = _denied(store)
    assert len(rows) == 4
    row = rows[-1]
    assert (row["actor_kind"], row["actor_id"]) == ("user", p.uid("vera"))
    assert row["request_id"] == "fixture-denied-1" and row["ip"]
    meta = _meta(row)
    assert meta["delegation"]["kind"] == "oauth_client" and meta["delegation"]["client_id"] == "crm-sync"
    assert (meta["via"], meta["operation"], meta["error"], meta["status"]) == ("api", OP, "read_only_role", 403)
    assert "run_id" not in meta
    assert auth["Authorization"].split()[1] not in json.dumps(row, default=str)

    # The other refusals `authorize` makes before a run exists, each with its own reason.
    assert refuse(who="out", via="api", scopes=held).code == "app_access_denied"
    assert _meta(_denied(store)[-1])["error"] == "app_access_denied"
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (p.uid("bob"),))
    err = refuse(who="bob", via="api", scopes=held)
    assert (err.status, err.code) == (401, "account_unavailable")
    last = _denied(store)[-1]
    assert (last["actor_kind"], last["actor_id"]) == ("user", p.uid("bob"))
    assert _meta(last) == {"via": "api", "operation": OP, "error": "account_unavailable", "status": 401}
    assert store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE result<>'denied'")["n"] == 0


# --------------------------------------------------------------------------------- D7 §4: two rows
def test_an_async_operation_writes_an_acceptance_row_then_a_completion_row(suite):
    store = _setup(suite)
    p = suite.people

    def start(call, inp):
        call.touches("job", f"job-{call.run_id}")
        return {"__run_status__": "running", "job": "fixture"}

    def peek(call, inp):
        return {"__run_status__": "running"}

    op_id = _op(suite, "job.start", start, mode="async")
    quiet = _op(suite, "job.peek", peek, need="read", side_effect="none", agent="auto", mode="async")

    # Accepted: one row, result ok, phase accepted, under the run.
    out = suite.caps.invoke(op_id, {}, user_id=p.uid("bob"), workspace_id=p.a)
    assert out["status"] == "running" and out["output"] == {"job": "fixture"}
    rid = out["run_id"]
    rows = _audit(store, op_id)
    assert len(rows) == 1
    accepted = rows[0]
    assert accepted["result"] == "ok"
    assert _meta(accepted) == {"run_id": rid, "via": "ui", "operation": op_id, "phase": "accepted"}
    assert (accepted["actor_kind"], accepted["actor_id"], accepted["workspace_id"]) == ("user", p.uid("bob"), p.a)
    assert (accepted["object_kind"], accepted["object_id"]) == ("job", f"job-{rid}")

    # Progress is not an audit event.
    suite.caps.update_run(rid, "running")
    suite.caps.update_run(rid, "waiting_input")
    assert len(_audit(store, op_id)) == 1

    # Completed: the second row, same run and same actor, result ok.
    suite.caps.update_run(rid, "succeeded", output={"job": "done"})
    rows = _audit(store, op_id)
    assert len(rows) == 2
    done = rows[1]
    assert done["result"] == "ok"
    assert _meta(done) == {"run_id": rid, "via": "ui", "operation": op_id, "phase": "completion", "status": "succeeded"}
    assert (done["actor_kind"], done["actor_id"], done["workspace_id"]) == \
        (accepted["actor_kind"], accepted["actor_id"], accepted["workspace_id"])
    assert (done["object_kind"], done["object_id"]) == ("job", f"job-{rid}")
    run = store.one("SELECT status, output FROM op_runs WHERE id=?", (rid,))
    assert run["status"] == "succeeded" and run["output"] == {"job": "done"}

    # A second run whose worker fails: the completion row says error and names the code.
    second = suite.caps.invoke(op_id, {}, user_id=p.uid("alice"), workspace_id=p.a)
    suite.caps.update_run(second["run_id"], "failed", error=ServiceError(422, "fixture_job_failed", "fixture"))
    rows = [r for r in _audit(store, op_id) if _meta(r)["run_id"] == second["run_id"]]
    assert [r["result"] for r in rows] == ["ok", "error"]
    assert _meta(rows[1]) == {"run_id": second["run_id"], "via": "ui", "operation": op_id, "phase": "completion",
                              "status": "failed", "error": "fixture_job_failed"}
    assert {(r["actor_kind"], r["actor_id"]) for r in rows} == {("user", p.uid("alice"))}
    assert store.one("SELECT status, error_code FROM op_runs WHERE id=?", (second["run_id"],)) == \
        {"status": "failed", "error_code": "fixture_job_failed"}

    # A run started on someone's behalf (a flow): the delegation named on the acceptance row is
    # named on the completion row too, and the person stays the actor.
    flow = {"kind": "flow_run", "flow_run_id": "flowrun_fixture0001"}
    third = suite.caps.invoke(op_id, {}, user_id=p.uid("bob"), workspace_id=p.a, via="flow", delegation=flow,
                              correlation_id="flowrun_fixture0001")
    suite.caps.update_run(third["run_id"], "partial", output={"job": "half"})
    rows = [r for r in _audit(store, op_id) if _meta(r)["run_id"] == third["run_id"]]
    assert [r["result"] for r in rows] == ["ok", "ok"]
    assert [_meta(r)["phase"] for r in rows] == ["accepted", "completion"]
    for r in rows:
        meta = _meta(r)
        assert meta["via"] == "flow" and meta["delegation"] == flow and meta["correlation_id"] == "flowrun_fixture0001"
        assert (r["actor_kind"], r["actor_id"]) == ("user", p.uid("bob"))
    assert _meta(rows[1])["status"] == "partial"

    # A read-only page operation that happens to be asynchronous: neither row, as before.
    fourth = suite.caps.invoke(quiet, {}, user_id=p.uid("vera"), workspace_id=p.a)
    assert fourth["status"] == "running"
    suite.caps.update_run(fourth["run_id"], "succeeded", output={})
    assert _audit(store, quiet) == []
    assert store.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == 6


# ------------------------------------------------------------------ D7 §5: refused at the door
def test_a_refusal_before_authorization_is_recorded_too(suite, monkeypatch):
    """A machine caller turned away before `authorize()` runs — a credential without the operation's
    scope, a credential with no scopes at all, an agent asking for an operation marked never for it —
    is a refused machine call like any other: one `denied` row, no run."""
    store = _setup(suite)
    p = suite.people
    monkeypatch.setattr(capabilities, "_DENIED_RECENT", {})
    monkeypatch.setattr(capabilities, "DENIED_ROW_INTERVAL_S", 0)      # each reason on its own row
    guarded = _op(suite, "note.guarded", lambda call, i: {"ok": True}, agent="never")

    def refuse(op_id=OP, **kw) -> ServiceError:
        with pytest.raises(ServiceError) as e:
            suite.caps.invoke(op_id, {"title": "No"} if op_id == OP else {}, user_id=p.uid("bob"), workspace_id=p.a, **kw)
        return e.value

    scope = refuse(via="api", scopes=frozenset({"forms.read"}))
    none = refuse(via="api", scopes=None)
    agent = refuse(op_id=guarded, via="agent")
    assert scope.code == "insufficient_scope" and agent.code == "not_for_agent" and none.status == 403
    rows = _denied(store)
    assert [(r["action"], _meta(r)["error"], _meta(r)["via"], _meta(r)["status"]) for r in rows] == [
        (OP, "insufficient_scope", "api", 403), (OP, none.code, "api", 403), (guarded, "not_for_agent", "agent", 403)]
    for r in rows:
        assert (r["actor_kind"], r["actor_id"], r["workspace_id"]) == ("user", p.uid("bob"), p.a)
        assert "run_id" not in _meta(r)
    assert store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == 0
    # A browser session's refusal is still not recorded: nothing a person does in the page is a scan.
    with pytest.raises(ServiceError) as e:
        suite.caps.invoke(OP, {"title": "No"}, user_id=p.uid("vera"), workspace_id=p.a, via="ui")
    assert e.value.code == "read_only_role" and len(_denied(store)) == 3
