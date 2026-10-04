"""The platform's read-only view of business-application runs (synwevo_identity.admin_runs).

What is held here: who may read it (the permission matrix in test_platform_access.py covers each
built-in role against each route; this file covers the fields inside the answers), that no input,
output, error message, authored name, authored error code, caller-chosen correlation text or
delegation body ever leaves, the filters, the paging boundary and its capped count, the window
counts, the stalled / overdue / parked marks, and how the lists are read (the plans of the
statements the routes themselves ran).

Isolated data directory. The run rows are fixtures written straight into the tables the
capability layer and the flow engine own — the account service does not load either, which is
the point of the module — with marker strings in every column that must stay inside. The states
and entry names the module repeats are compared with the originals.
"""
from __future__ import annotations

import ast
import inspect
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .platform_fixture import make_platform_admin
from synwevo_core import capabilities, platform_access
from synwevo_core.flows import engine as flow_engine
from synwevo_core.flows.models import Definition
from synwevo_core.identity import Identity
from synwevo_core.platform_access import ROLES
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now
from synwevo_identity import admin_runs

PW = "isolated-runs-fixture-passphrase-47"
#: Every column that must stay inside carries one of these; no answer may contain any of them.
SECRETS = ("FIXTURE-INPUT-BODY", "FIXTURE-OUTPUT-BODY", "FIXTURE-ERROR-SENTENCE", "FIXTURE-ERROR-DETAIL",
           "FIXTURE-IDEMPOTENCY-KEY", "FIXTURE-CONVERSATION", "FIXTURE-STEP-NAME", "FIXTURE-FLOW-NAME",
           "FIXTURE-FLOW-PARAMS", "FIXTURE-FLOW-EVENT", "FIXTURE-STEP-VALUE", "FIXTURE-STEP-RECEIPT",
           "FIXTURE-STEP-READER", "FIXTURE-FLOW-SPEC", "FIXTURE-GRANT-FAMILY", "FIXTURE-INPUT-HASH",
           "FIXTURE-CALLER-CORRELATION", "FIXTURE-AUTHORED-CODE")
#: A correlation id as a caller may write one (any text up to 100 characters), and what is shown
#: for it: "ext-" and the first 24 hex digits of SHA-256 over the module's label, a zero byte and
#: the text — worked out with `shasum`, not with the module.
CALLER_TEXT = "FIXTURE-CALLER-CORRELATION acme merger 2026"
CALLER_TOKEN = "ext-2b6b54ceaa180d19be45f03a"

RUN_KEYS = {"id", "op", "app", "op_version", "workspace_id", "workspace_name", "user_id", "user_email", "via",
            "principal_kind", "client_id", "delegation_kind", "flow_run_id", "correlation_id", "parent_run_id",
            "depth", "status", "error_code", "object_kind", "object_id", "created_at", "started_at",
            "finished_at", "updated_at", "stalled"}
FLOW_KEYS = {"id", "flow_id", "version", "user_id", "user_email", "status", "error_code", "correlation_id",
             "parent_run_id", "depth", "trigger_id", "calls_used", "deadline", "due_at", "created_at",
             "updated_at", "finished_at", "leased", "overdue"}
STEP_KEYS = {"position", "status", "attempt", "checks", "error_code", "op_run_id", "op", "app", "op_status",
             "started_at", "updated_at"}
HISTORY_KEYS = {"seq", "type", "status", "attempt", "op_run_id", "error_code", "fence", "created_at"}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    dist = tmp_path / "fixture-build"
    dist.mkdir()
    dist.joinpath("index.html").write_text(
        (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8"), encoding="utf-8")
    for key, value in {"DATA_DIR": str(tmp_path / "d"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver", "ACCOUNTS_UI_DIR": str(dist)}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    clients: list[TestClient] = []

    def person(email):
        u = ident.create_user(email, PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        return ident.get_user(u["id"])

    def login(user):
        c = TestClient(api)
        r = c.post("/api/auth/login", json={"email": user["email"], "password": PW},
                   headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    def holder(role):
        return login(make_platform_admin(store, person(f"{role}@example.test"), role=role))

    alice, bob = person("alice@example.test"), person("bob@example.test")
    w = {"api": api, "store": store, "ident": ident, "login": login, "holder": holder, "person": person,
         "alice": alice, "bob": bob,
         "alice_ws": ident.workspaces_for(alice["id"])[0], "bob_ws": ident.workspaces_for(bob["id"])[0]}
    yield w
    for c in clients:
        c.close()


def run(store, rid, *, user, workspace=None, op="forms.request.create", app="forms", status="succeeded",
        via="ui", at=None, updated=None, correlation=None, parent=None, error_code=None, principal=None,
        delegation=None, object_id=None):
    """A fixture row of `op_runs`, with a marker in every column the admin view must not return."""
    at = now() if at is None else at
    store.execute(
        "INSERT INTO op_runs (id,op,app,op_version,workspace_id,user_id,principal,via,delegation,correlation_id,"
        "parent_run_id,depth,idempotency_key,input_hash,input,status,output,error_code,error_message,error_detail,"
        "object_kind,object_id,created_at,started_at,finished_at,updated_at) "
        "VALUES (?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, op, app, workspace, user["id"],
         json.dumps(principal or {"kind": "user", "user_id": user["id"], "tenant": workspace}), via,
         json.dumps(delegation) if delegation else None, correlation or rid, parent, 1 if parent else 0,
         f"FIXTURE-IDEMPOTENCY-KEY-{rid}", "FIXTURE-INPUT-HASH", json.dumps({"title": "FIXTURE-INPUT-BODY"}),
         status, json.dumps({"answer": "FIXTURE-OUTPUT-BODY"}), error_code,
         "FIXTURE-ERROR-SENTENCE" if error_code else None,
         json.dumps({"field": "FIXTURE-ERROR-DETAIL"}) if error_code else None,
         "form" if object_id else None, object_id, at, at,
         at if status in capabilities.TERMINAL_STATES else None, at if updated is None else updated))
    return rid


def flow(store, fid, *, user, status="running", at=None, updated=None, deadline=None, lease_until=0,
         error_code=None, trigger=None, flow_id="flow_fixture", spec=None, step="FIXTURE-STEP-NAME",
         correlation=None):
    at = now() if at is None else at
    store.execute("INSERT OR IGNORE INTO flow_definitions (id,user_id,name,latest_version,created_at) VALUES (?,?,?,1,?)",
                  (flow_id, user["id"], "FIXTURE-FLOW-NAME", at))
    store.execute("INSERT OR IGNORE INTO flow_versions (flow_id,version,spec_json,created_at) VALUES (?,?,?,?)",
                  (flow_id, 1, spec or json.dumps({"steps": "FIXTURE-FLOW-SPEC"}), at))
    store.execute(
        "INSERT INTO flow_runs (id,flow_id,version,user_id,params_json,input_digest,idempotency_key,status,step_id,"
        "error_code,correlation_id,trigger_id,event_json,calls_used,deadline,lease_owner,lease_until,fence,due_at,"
        "created_at,updated_at,finished_at) VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fid, flow_id, user["id"], json.dumps({"p": "FIXTURE-FLOW-PARAMS"}), "FIXTURE-INPUT-HASH",
         f"FIXTURE-IDEMPOTENCY-KEY-{fid}", status, step, error_code, correlation or fid, trigger,
         json.dumps({"e": "FIXTURE-FLOW-EVENT"}), 2, at + 3600 if deadline is None else deadline,
         "worker-1" if lease_until else None, lease_until, 3, at, at, at if updated is None else updated,
         at if status in flow_engine.TERMINAL else None))
    return fid


def texts(*responses):
    return "\n".join(r.text for r in responses)


@pytest.fixture()
def statements(monkeypatch):
    """Every statement read through the store while the test runs, as (sql, args): what the
    routes themselves built, for EXPLAIN — not a copy of it written in the test."""
    seen: list[tuple[str, tuple]] = []
    for name in ("one", "all"):
        def spy(self, sql, args=(), _real=getattr(Store, name)):
            seen.append((sql, tuple(args)))
            return _real(self, sql, args)
        monkeypatch.setattr(Store, name, spy)
    return seen


def run_statements(store, seen):
    """(sql, args, plan) of the statements in `seen` that read the two run tables."""
    return [(sql, args, " | ".join(row["detail"] for row in store.all("EXPLAIN QUERY PLAN " + sql, args)))
            for sql, args in list(seen) if re.search(r"\b(op_runs|flow_runs)\b", sql) and not sql.startswith("EXPLAIN")]


def states_the_engine_gives_a_run() -> set[str]:
    """Read from the flow engine's source: every literal it passes as the new state to `_release`
    or `_control`, and every literal it assigns to the `state` it passes on."""
    def literals(node) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        if isinstance(node, ast.Tuple):
            return set().union(*(literals(e) for e in node.elts))
        return set()

    found: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(flow_engine))):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("_release", "_control") and len(node.args) >= 3:
            found |= literals(node.args[2])
        if isinstance(node, ast.Assign) and any(isinstance(n, ast.Name) and n.id == "state"
                                                for target in node.targets for n in ast.walk(target)):
            found |= literals(node.value)
    return found


# ---------------------------------------------------------------- the copies match the originals
def test_the_repeated_states_and_entries_are_the_capability_layers_and_the_flow_engines():
    assert admin_runs.RUN_STATES == capabilities.RUN_STATES
    assert admin_runs.VIA == capabilities.VIA
    assert set(admin_runs.RUN_ACTIVE) == set(capabilities.RUN_STATES) - capabilities.TERMINAL_STATES \
        - {"waiting_input", "waiting_approval", "unknown"}
    assert admin_runs.FLOW_ACTIVE == flow_engine.ACTIVE and admin_runs.FLOW_TERMINAL == flow_engine.TERMINAL
    # ...and the whole vocabulary, not only the two tuples a run is claimed or finished in: the
    # engine also parks runs, and what it exports is held to what its source gives a run
    assert admin_runs.FLOW_PARKED == flow_engine.PARKED and admin_runs.FLOW_STATES == flow_engine.STATES
    given = states_the_engine_gives_a_run()
    assert {"queued", "succeeded", "failed", "cancelled", "retrying"} <= given, sorted(given)
    assert given - set(flow_engine.ACTIVE) - set(flow_engine.TERMINAL) == set(flow_engine.PARKED) == {"paused", "blocked", "unknown"}
    assert given <= set(admin_runs.FLOW_STATES)
    assert set(admin_runs.FLOW_ATTENTION) == {"failed", "partial", "blocked", "unknown"}
    # the ids the platform makes for a correlation are returned as they are: the form is the generators'
    for prefix, module in (("run", capabilities), ("flowrun", flow_engine)):
        assert f'new_id("{prefix}")' in inspect.getsource(module)
        made = new_id(prefix)
        assert admin_runs.correlation_token(made) == made
    assert admin_runs.PERMISSION in platform_access.PERMISSIONS
    assert sorted(r for r in ROLES if admin_runs.PERMISSION in ROLES[r]) == \
        ["access_admin", "auditor", "super_admin", "support"]


# ---------------------------------------------------------------- nothing but metadata leaves
def test_no_route_returns_an_input_an_output_a_message_an_authored_name_or_a_delegation_body(world):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    flow(store, "fr_one", user=alice, status="failed", error_code="step_failed", trigger="trg_1")
    run(store, "run_plain", user=alice, workspace=ws, object_id="form_1")
    run(store, "run_failed", user=alice, workspace=ws, status="failed", error_code="invalid_input")
    run(store, "run_agent", user=alice, workspace=ws, via="agent", correlation="corr_chain",
        delegation={"kind": "agent_conversation", "id": "FIXTURE-CONVERSATION", "turn": "t1", "tool_call": "c1"})
    run(store, "run_step", user=alice, workspace=ws, via="flow", correlation="corr_chain", parent="fr_one",
        delegation={"kind": "flow", "flow_id": "flow_fixture", "version": 1, "run_id": "fr_one",
                    "step_id": "FIXTURE-STEP-NAME", "trigger_id": "trg_1"})
    run(store, "run_client", user=alice, workspace=ws, via="api",
        principal={"kind": "delegated", "client_id": "client_fixture", "tenant": ws, "scopes": ["FIXTURE-GRANT-FAMILY"]},
        delegation={"kind": "oauth_client", "client_id": "client_fixture", "grant": "FIXTURE-GRANT-FAMILY"})
    store.execute("INSERT INTO flow_steps (run_id,step_id,status,attempt,checks,op_run_id,key,receipt_json,value_json,"
                  "reader_json,error_code,started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("fr_one", "FIXTURE-STEP-NAME", "failed", 2, 1, "run_step", "FIXTURE-IDEMPOTENCY-KEY-step",
                   json.dumps({"r": "FIXTURE-STEP-RECEIPT"}), json.dumps({"v": "FIXTURE-STEP-VALUE"}),
                   json.dumps({"x": "FIXTURE-STEP-READER"}), "invalid_input", now(), now()))
    store.execute("INSERT INTO flow_history (run_id,step_id,type,status,attempt,op_run_id,error_code,fence,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", ("fr_one", "FIXTURE-STEP-NAME", "step_failed", "failed", 2, "run_step",
                                                "invalid_input", 3, now()))
    c = world["holder"]("auditor")
    answers = [c.get("/api/admin/app-runs"), c.get("/api/admin/app-runs?status=attention"),
               c.get("/api/admin/app-runs/summary"), c.get("/api/admin/app-runs/run_step"),
               c.get("/api/admin/app-runs/run_agent"), c.get("/api/admin/app-runs/run_client"),
               c.get("/api/admin/flow-runs"), c.get("/api/admin/flow-runs/fr_one")]
    assert [r.status_code for r in answers] == [200] * len(answers), texts(*answers)
    for marker in SECRETS:
        assert marker not in texts(*answers), marker
    for r in answers:
        assert r.headers["cache-control"] == "no-store"

    listed = {row["id"]: row for row in answers[0].json()["items"]}
    assert set(listed) == {"run_plain", "run_failed", "run_agent", "run_step", "run_client"}
    for row in listed.values():
        assert set(row) == RUN_KEYS, sorted(set(row) ^ RUN_KEYS)
    assert listed["run_plain"] | {"created_at": 0, "started_at": 0, "finished_at": 0, "updated_at": 0} == {
        "id": "run_plain", "op": "forms.request.create", "app": "forms", "op_version": 1, "workspace_id": ws,
        "workspace_name": world["alice_ws"]["name"], "user_id": alice["id"], "user_email": "alice@example.test",
        "via": "ui", "principal_kind": "user", "client_id": None, "delegation_kind": None, "flow_run_id": None,
        # "run_plain" is this fixture's text, not an id the platform makes (`run_` and twelve hex
        # digits), so it is returned the way a caller's text is: as its digest
        "correlation_id": "ext-a49465cb956308e07d5bc1d4", "parent_run_id": None, "depth": 0, "status": "succeeded", "error_code": None,
        "object_kind": "form", "object_id": "form_1", "stalled": False,
        "created_at": 0, "started_at": 0, "finished_at": 0, "updated_at": 0}
    assert listed["run_failed"]["error_code"] == "invalid_input"
    assert (listed["run_agent"]["delegation_kind"], listed["run_agent"]["flow_run_id"]) == ("agent_conversation", None)
    assert (listed["run_step"]["delegation_kind"], listed["run_step"]["flow_run_id"]) == ("flow", "fr_one")
    assert (listed["run_client"]["principal_kind"], listed["run_client"]["client_id"],
            listed["run_client"]["delegation_kind"]) == ("delegated", "client_fixture", "oauth_client")

    detail = answers[3].json()
    assert set(detail) == {"run", "related", "related_truncated", "stale_after_s", "generated_at"}
    assert detail["run"]["id"] == "run_step" and [x["id"] for x in detail["related"]] == ["run_agent"]
    assert all(set(x) == RUN_KEYS for x in [detail["run"], *detail["related"]])

    flows = answers[6].json()["items"]
    assert [f["id"] for f in flows] == ["fr_one"] and set(flows[0]) == FLOW_KEYS
    one = answers[7].json()
    assert set(one) == {"run", "steps", "history", "history_truncated", "generated_at"} and set(one["run"]) == FLOW_KEYS
    assert [set(s) for s in one["steps"]] == [STEP_KEYS] and [set(h) for h in one["history"]] == [HISTORY_KEYS]
    assert one["steps"][0] | {"started_at": 0, "updated_at": 0} == {
        "position": 1, "status": "failed", "attempt": 2, "checks": 1, "error_code": "invalid_input",
        "op_run_id": "run_step", "op": "forms.request.create", "app": "forms", "op_status": "succeeded",
        "started_at": 0, "updated_at": 0}
    assert (one["run"]["status"], one["run"]["error_code"], one["run"]["trigger_id"], one["run"]["calls_used"]) == \
        ("failed", "step_failed", "trg_1", 2)


def test_a_correlation_id_a_caller_chose_is_returned_as_a_digest_that_still_groups_and_finds_its_runs(world, monkeypatch):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    made = new_id("run")                       # what the platform uses when the caller names none
    run(store, made, user=alice, workspace=ws)
    run(store, "run_child", user=alice, workspace=ws, via="flow", correlation=made, parent=made)
    run(store, "run_given_1", user=alice, workspace=ws, via="api", correlation=CALLER_TEXT)
    run(store, "run_given_2", user=alice, workspace=ws, via="api", correlation=CALLER_TEXT)
    for i, text in enumerate(("AAA FIXTURE-CALLER-CORRELATION", "AAB FIXTURE-CALLER-CORRELATION", "zz FIXTURE-CALLER-CORRELATION")):
        run(store, f"run_other_{i}", user=alice, workspace=ws, via="ui", correlation=text)
    flow(store, "fr_given", user=alice, correlation=CALLER_TEXT)
    flow(store, "fr_made", user=alice, correlation=made)
    monkeypatch.setattr(admin_runs, "_TOKEN_BATCH", 2)      # the digest is found past the first batch
    c = world["holder"]("auditor")

    listing = c.get("/api/admin/app-runs")
    listed = {row["id"]: row["correlation_id"] for row in listing.json()["items"]}
    assert listed[made] == listed["run_child"] == made
    assert listed["run_given_1"] == listed["run_given_2"] == CALLER_TOKEN
    others = [listed[f"run_other_{i}"] for i in range(3)]
    assert len(set(others)) == 3 and all(re.fullmatch(r"ext-[0-9a-f]{24}", token) for token in others) and CALLER_TOKEN not in others
    detail = c.get("/api/admin/app-runs/run_given_1")
    assert detail.json()["run"]["correlation_id"] == CALLER_TOKEN
    assert [(x["id"], x["correlation_id"]) for x in detail.json()["related"]] == [("run_given_2", CALLER_TOKEN)], \
        "同一关联编号的运行仍按存储的值归在一起"
    flows = c.get("/api/admin/flow-runs")
    assert {f["id"]: f["correlation_id"] for f in flows.json()["items"]} == {"fr_given": CALLER_TOKEN, "fr_made": made}
    one = c.get("/api/admin/flow-runs/fr_given")
    assert one.json()["run"]["correlation_id"] == CALLER_TOKEN

    def found(path, **params):
        r = c.get(path, params=params)
        assert r.status_code == 200, r.text
        return r, [row["id"] for row in r.json()["items"]]

    # what the page shows finds the chain, in both lists
    by_token, ids = found("/api/admin/app-runs", q=CALLER_TOKEN)
    assert ids == ["run_given_2", "run_given_1"]
    flows_by_token, ids = found("/api/admin/flow-runs", q=CALLER_TOKEN)
    assert ids == ["fr_given"]
    # someone who was given the text may search by it; it is found, and still not echoed
    by_text, ids = found("/api/admin/app-runs", q=CALLER_TEXT)
    assert ids == ["run_given_2", "run_given_1"]
    assert found("/api/admin/app-runs", q=made)[1] == ["run_child", made]
    assert found("/api/admin/flow-runs", q=made)[1] == ["fr_made"]
    assert found("/api/admin/app-runs", q="ext-" + "0" * 24)[1] == [], "没有人见过的摘要不对应任何运行"
    answers = [listing, detail, flows, one, by_token, flows_by_token, by_text]
    for marker in SECRETS:
        assert marker not in texts(*answers), marker


def test_an_error_code_a_flows_author_chose_is_replaced_on_the_run_the_step_and_the_history(world):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    # The definition as the engine stores it: validated by its own model, in its canonical form.
    # Its `fail` step ends the run with a code the author wrote, in the grammar of a step id.
    spec = capabilities.canonical(Definition.model_validate({"steps": [
        {"id": "collect", "kind": "call", "operation": "forms.request.create", "on_error": "FIXTURE-STEP-NAME"},
        {"id": "FIXTURE-STEP-NAME", "kind": "fail", "code": "FIXTURE-AUTHORED-CODE"}]}).model_dump(mode="json"))
    authored = {"flow_id": "flow_authored", "spec": spec}
    flow(store, "fr_authored", user=alice, status="failed", error_code="FIXTURE-AUTHORED-CODE", **authored)
    # the same step, stopped by the engine instead (the run's deadline passed before it ran)
    flow(store, "fr_deadline", user=alice, status="failed", error_code="flow_deadline_exceeded", **authored)
    # stopped at the call step by the operation's own refusal
    flow(store, "fr_refused", user=alice, status="blocked", error_code="read_only_role", step="collect", **authored)
    for step, attempt, code in (("collect", 1, "invalid_input"), ("FIXTURE-STEP-NAME", 0, "FIXTURE-AUTHORED-CODE")):
        store.execute("INSERT INTO flow_steps (run_id,step_id,status,attempt,checks,error_code,started_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", ("fr_authored", step, "failed", attempt, 0, code, now(), now()))
        store.execute("INSERT INTO flow_history (run_id,step_id,type,status,attempt,error_code,fence,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", ("fr_authored", step, "step_failed", "failed", attempt, code, 3, now()))
    c = world["holder"]("support")
    listing = c.get("/api/admin/flow-runs")
    assert {f["id"]: f["error_code"] for f in listing.json()["items"]} == {
        "fr_authored": "flow_failed_by_definition", "fr_deadline": "flow_deadline_exceeded", "fr_refused": "read_only_role"}
    assert c.get("/api/admin/flow-runs?status=attention").json()["items"][0].keys() == FLOW_KEYS
    detail = c.get("/api/admin/flow-runs/fr_authored")
    one = detail.json()
    assert one["run"]["error_code"] == "flow_failed_by_definition" and set(one["run"]) == FLOW_KEYS
    assert [(s["position"], s["error_code"]) for s in one["steps"]] == [(1, "invalid_input"), (2, "flow_failed_by_definition")]
    assert [h["error_code"] for h in one["history"]] == ["flow_failed_by_definition", "invalid_input"], "最近的在前"
    assert [set(s) for s in one["steps"]] == [STEP_KEYS] * 2 and [set(h) for h in one["history"]] == [HISTORY_KEYS] * 2
    answers = [listing, detail, c.get("/api/admin/flow-runs/fr_deadline"), c.get("/api/admin/flow-runs/fr_refused"),
               c.get("/api/admin/flow-runs?status=failed"), c.get("/api/admin/app-runs/summary")]
    assert [r.status_code for r in answers] == [200] * len(answers), texts(*answers)
    for marker in SECRETS:
        assert marker not in texts(*answers), marker
    assert admin_runs.AUTHORED_FAILURE == "flow_failed_by_definition"


def test_addresses_and_workspace_names_follow_the_callers_other_permissions(world, monkeypatch):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    run(store, "run_a", user=alice, workspace=ws)
    flow(store, "fr_a", user=alice)
    # No built-in role reads runs without also reading accounts and workspaces, so the rule is
    # exercised with a role narrowed for this test: runs, and nothing about people or workspaces.
    monkeypatch.setitem(platform_access.ROLES, "api_platform_admin",
                        frozenset({"platform.overview.view", admin_runs.PERMISSION}))
    narrow = world["holder"]("api_platform_admin")
    answers = [narrow.get("/api/admin/app-runs"), narrow.get("/api/admin/app-runs/run_a"),
               narrow.get("/api/admin/flow-runs"), narrow.get("/api/admin/flow-runs/fr_a")]
    assert [r.status_code for r in answers] == [200] * 4, texts(*answers)
    assert "alice@example.test" not in texts(*answers) and world["alice_ws"]["name"] not in texts(*answers)
    row = answers[0].json()["items"][0]
    assert (row["user_id"], row["user_email"], row["workspace_id"], row["workspace_name"]) == (alice["id"], None, ws, None)
    assert answers[2].json()["items"][0]["user_email"] is None

    monkeypatch.setitem(platform_access.ROLES, "auditor", frozenset({admin_runs.PERMISSION, "platform.users.view"}))
    people_only = world["holder"]("auditor").get("/api/admin/app-runs").json()["items"][0]
    assert (people_only["user_email"], people_only["workspace_name"]) == ("alice@example.test", None)


def test_a_role_without_the_permission_and_an_account_without_any_are_refused(world):
    run(world["store"], "run_a", user=world["alice"], workspace=world["alice_ws"]["id"])
    flow(world["store"], "fr_a", user=world["alice"])
    paths = ["/api/admin/app-runs", "/api/admin/app-runs/summary", "/api/admin/app-runs/run_a",
             "/api/admin/flow-runs", "/api/admin/flow-runs/fr_a"]
    console = world["holder"]("api_platform_admin")
    for path in paths:
        r = console.get(path)
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", (path, r.text)
        assert r.json()["detail"]["permission"] == "platform.apps.view" and "run_a" not in r.text
    # the person whose runs these are has no platform permission: the same 404 as a missing page
    owner = world["login"](world["alice"])
    for path in paths:
        r = owner.get(path)
        assert r.status_code == 404 and r.json()["error"] == "not_found", (path, r.text)
    assert TestClient(world["api"]).get("/api/admin/app-runs").status_code == 401
    denied = world["store"].all("SELECT object_id FROM audit_events WHERE action='admin.access.denied'")
    assert {d["object_id"] for d in denied} == {"platform.apps.view"}, "被拒绝的管理请求留下一条审计"


# ---------------------------------------------------------------- filters and paging
def test_filters_select_by_state_entry_application_workspace_person_and_identifier(world):
    store, alice, bob = world["store"], world["alice"], world["bob"]
    a, b = world["alice_ws"]["id"], world["bob_ws"]["id"]
    ts = now()
    run(store, "run_ok", user=alice, workspace=a)
    run(store, "run_failed", user=alice, workspace=a, status="failed", error_code="boom")
    run(store, "run_unknown", user=bob, workspace=b, status="unknown", app="tables", op="tables.record.create")
    run(store, "run_stalled", user=bob, workspace=b, status="running", via="flow", at=ts - 5000, correlation="corr_x")
    run(store, "run_fresh", user=bob, workspace=b, status="running", via="agent", app="tables",
        op="tables_record%create", correlation="corr_x")
    run(store, "run_queued_old", user=alice, workspace=a, status="queued", at=ts - 700)
    run(store, "run_waiting_old", user=alice, workspace=a, status="waiting_approval", at=ts - 9000)
    c = world["holder"]("support")

    def ids(query=""):
        r = c.get("/api/admin/app-runs" + query)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == len(r.json()["items"])
        return [row["id"] for row in r.json()["items"]]

    assert ids() == ["run_waiting_old", "run_queued_old", "run_fresh", "run_stalled", "run_unknown", "run_failed", "run_ok"], \
        "最新创建的在前（按写入顺序）"
    assert ids("?status=failed") == ["run_failed"]
    assert ids("?status=running") == ["run_fresh", "run_stalled"]
    # failed, unknown, or queued/running and untouched for longer than the threshold; waiting for
    # a person is not stalled, however long it has waited
    assert ids("?status=attention") == ["run_queued_old", "run_stalled", "run_unknown", "run_failed"]
    stalled = {row["id"] for row in c.get("/api/admin/app-runs").json()["items"] if row["stalled"]}
    assert stalled == {"run_stalled", "run_queued_old"}
    assert ids("?via=flow") == ["run_stalled"] and ids("?via=agent") == ["run_fresh"]
    assert ids("?app=tables") == ["run_fresh", "run_unknown"]
    # With a workspace or a person chosen the order is creation time — the order of the index
    # that answers the filter. This fixture wrote run_stalled and run_queued_old after newer
    # runs with an earlier creation time, which a real run never has; they sort by that time.
    assert ids(f"?workspace_id={b}") == ["run_fresh", "run_unknown", "run_stalled"]
    assert ids(f"?user_id={alice['id']}&status=attention") == ["run_failed", "run_queued_old"]
    assert ids(f"?user_id={alice['id']}") == ["run_failed", "run_ok", "run_queued_old", "run_waiting_old"]
    listing = c.get("/api/admin/app-runs").json()
    assert listing["stale_after_s"] == 600, "列表自己说明停滞的阈值，页面按它写「超过多久没有更新」"
    assert (listing["total"], listing["total_capped"], listing["more"]) == (7, False, False)
    assert ids("?q=run_failed") == ["run_failed"]
    assert ids("?q=corr_x") == ["run_fresh", "run_stalled"]
    assert ids("?q=tables.") == ["run_unknown"]
    # the search text is literal: `_` and `%` are not wildcards
    assert ids("?q=tables_record%25create") == ["run_fresh"] and ids("?q=tables_record") == ["run_fresh"]
    assert ids("?q=%25") == [] and ids("?q=run_") == []
    for bad in ("?status=done", "?via=cron"):
        r = c.get("/api/admin/app-runs" + bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_run_filter", r.text
    assert c.get("/api/admin/app-runs?limit=0").status_code == 422
    assert c.get("/api/admin/app-runs?limit=201").status_code == 422
    assert c.get("/api/admin/app-runs/run_nope").json()["error"] == "run_not_found"
    assert c.get("/api/admin/app-runs/run_nope").status_code == 404


def test_pages_keep_their_boundary_when_runs_arrive_while_paging(world):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    for i in range(5):
        run(store, f"run_{i}", user=alice, workspace=ws)
    c = world["holder"]("auditor")
    first = c.get("/api/admin/app-runs?limit=2").json()
    assert [r["id"] for r in first["items"]] == ["run_4", "run_3"] and first["total"] == 5
    run(store, "run_new_a", user=alice, workspace=ws)
    run(store, "run_new_b", user=alice, workspace=ws)
    second = c.get(f"/api/admin/app-runs?limit=2&offset=2&snapshot={first['snapshot']}").json()
    assert [r["id"] for r in second["items"]] == ["run_2", "run_1"] and second["total"] == 5
    assert second["snapshot"] == first["snapshot"]
    # a new listing starts from what exists now; a boundary from the future is brought back to it
    fresh = c.get("/api/admin/app-runs?limit=2&snapshot=999999999").json()
    assert [r["id"] for r in fresh["items"]] == ["run_new_b", "run_new_a"] and fresh["total"] == 7
    assert fresh["snapshot"] > first["snapshot"]
    flows = [flow(store, f"fr_{i}", user=alice, status="succeeded") for i in range(3)]
    page = c.get("/api/admin/flow-runs?limit=2").json()
    flow(store, "fr_late", user=alice)
    rest = c.get(f"/api/admin/flow-runs?limit=2&offset=2&snapshot={page['snapshot']}").json()
    assert [f["id"] for f in page["items"]] + [f["id"] for f in rest["items"]] == flows[::-1]


# ---------------------------------------------------------------- the window counts
def test_the_summary_counts_the_window_by_state_and_application_and_what_is_stalled_now(world):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    ts = now()
    for i in range(3):
        run(store, f"run_ok_{i}", user=alice, workspace=ws)
    run(store, "run_fail", user=alice, workspace=ws, status="failed", error_code="boom")
    run(store, "run_tab_fail", user=alice, workspace=ws, status="failed", app="tables", error_code="boom")
    run(store, "run_tab_unknown", user=alice, workspace=ws, status="unknown", app="tables")
    run(store, "run_old_ok", user=alice, workspace=ws, at=ts - 2 * 86400)          # outside a day
    run(store, "run_old_touched", user=alice, workspace=ws, at=ts - 2 * 86400, updated=ts - 60)
    run(store, "run_stalled", user=alice, workspace=ws, status="running", at=ts - 3 * 86400)
    run(store, "run_queue_stalled", user=alice, workspace=ws, status="queued", at=ts - 601)
    run(store, "run_running", user=alice, workspace=ws, status="running", app="extract")
    flow(store, "fr_ok", user=alice, status="succeeded")
    flow(store, "fr_failed", user=alice, status="failed", error_code="step_failed")
    flow(store, "fr_old_failed", user=alice, status="failed", at=ts - 2 * 86400)
    flow(store, "fr_active", user=alice, status="waiting_approval")
    flow(store, "fr_overdue", user=alice, status="retrying", at=ts - 7200, deadline=ts - 3600)
    c = world["holder"]("access_admin")
    day = c.get("/api/admin/app-runs/summary").json()
    assert day["window_s"] == 86400 and day["stale_after_s"] == 600 and abs(day["since"] - (ts - 86400)) < 30
    assert day["runs"]["by_status"] == {"queued": 1, "running": 1, "waiting_input": 0, "waiting_approval": 0,
                                        "succeeded": 4, "partial": 0, "failed": 2, "cancelled": 0, "unknown": 1}
    assert day["runs"]["total"] == 9
    assert day["runs"]["stalled"] == 2, "停滞按现在算，不受窗口限制"
    assert day["runs"]["by_app"] == [{"app": "forms", "total": 6, "failed": 1, "unknown": 0},
                                     {"app": "tables", "total": 2, "failed": 1, "unknown": 1},
                                     {"app": "extract", "total": 1, "failed": 0, "unknown": 0}]
    assert day["flows"] == {"finished": {"succeeded": 1, "partial": 0, "failed": 1, "cancelled": 0},
                            "active": 2, "overdue": 1, "parked": {"paused": 0, "blocked": 0, "unknown": 0}}
    week = c.get("/api/admin/app-runs/summary?window_s=604800").json()
    assert week["runs"]["by_status"]["succeeded"] == 5 and week["runs"]["by_status"]["running"] == 2
    assert week["flows"]["finished"]["failed"] == 2
    for bad in ("?window_s=60", "?window_s=86401"):
        r = c.get("/api/admin/app-runs/summary" + bad)
        assert r.status_code == 400 and r.json()["error"] == "bad_run_filter", r.text
    plan = store.all("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM flow_runs WHERE status=? AND updated_at>=?", ("failed", 0.0))
    assert any("flow_runs_status_updated" in row["detail"] for row in plan), plan
    plan = store.all("EXPLAIN QUERY PLAN SELECT COUNT(*) FROM op_runs WHERE status=? AND updated_at>=?", ("failed", 0.0))
    assert any("op_runs_status" in row["detail"] for row in plan), plan


# ---------------------------------------------------------------- flow runs
def test_flow_runs_are_listed_by_state_and_show_what_is_leased_or_overdue(world):
    store, alice, bob = world["store"], world["alice"], world["bob"]
    ts = now()
    flow(store, "fr_done", user=alice, status="succeeded")
    flow(store, "fr_failed", user=alice, status="failed", error_code="step_failed")
    flow(store, "fr_partial", user=bob, status="partial")
    flow(store, "fr_leased", user=bob, status="running", lease_until=ts + 30)
    flow(store, "fr_waiting", user=alice, status="waiting_input")
    flow(store, "fr_overdue", user=alice, status="queued", at=ts - 9000, deadline=ts - 10)
    flow(store, "fr_failed_late", user=alice, status="failed", at=ts - 9000, deadline=ts - 10, lease_until=ts + 30)
    c = world["holder"]("support")

    def rows(query=""):
        r = c.get("/api/admin/flow-runs" + query)
        assert r.status_code == 200, r.text
        return {f["id"]: f for f in r.json()["items"]}

    everything = rows()
    assert list(everything) == ["fr_failed_late", "fr_overdue", "fr_waiting", "fr_leased", "fr_partial", "fr_failed", "fr_done"]
    assert {i for i, f in everything.items() if f["leased"]} == {"fr_leased"}
    assert {i for i, f in everything.items() if f["overdue"]} == {"fr_overdue"}, "已结束的运行不算超期，也不算被认领"
    assert everything["fr_failed"]["user_email"] == "alice@example.test"
    assert set(rows("?status=active")) == {"fr_leased", "fr_waiting", "fr_overdue"}
    assert set(rows("?status=attention")) == {"fr_failed", "fr_partial", "fr_overdue", "fr_failed_late"}
    assert set(rows("?status=failed")) == {"fr_failed", "fr_failed_late"}
    assert set(rows(f"?user_id={bob['id']}")) == {"fr_partial", "fr_leased"}
    assert set(rows("?flow_id=flow_fixture")) == set(everything) and rows("?flow_id=flow_other") == {}
    assert set(rows("?q=fr_waiting")) == {"fr_waiting"}
    r = c.get("/api/admin/flow-runs?status=stuck")
    assert r.status_code == 400 and r.json()["error"] == "bad_run_filter"
    missing = c.get("/api/admin/flow-runs/fr_nope")
    assert missing.status_code == 404 and missing.json()["error"] == "run_not_found"


def test_parked_flow_runs_are_listed_by_state_counted_now_and_need_attention_unless_paused(world):
    store, alice, bob = world["store"], world["alice"], world["bob"]
    ts = now()
    # The three states the engine leaves a run in without claiming it again (PARKED). The blocked
    # one has waited for its owner for forty days: outside every window, and still waiting.
    flow(store, "fr_blocked", user=alice, status="blocked", error_code="read_only_role", at=ts - 40 * 86400)
    flow(store, "fr_unknown", user=alice, status="unknown", error_code="flow_outcome_unknown")
    flow(store, "fr_paused", user=bob, status="paused", at=ts - 9000, deadline=ts - 10)
    flow(store, "fr_running", user=bob, status="running")
    flow(store, "fr_done", user=bob, status="succeeded")
    c = world["holder"]("support")

    def rows(query=""):
        r = c.get("/api/admin/flow-runs" + query)
        assert r.status_code == 200, r.text
        return {f["id"]: f for f in r.json()["items"]}

    for state in ("blocked", "unknown", "paused"):
        assert set(rows(f"?status={state}")) == {f"fr_{state}"}
    assert set(rows("?status=attention")) == {"fr_blocked", "fr_unknown"}, "受阻和结果不明需要关注；暂停是所有者自己的决定"
    assert set(rows("?status=active")) == {"fr_running"}
    everything = rows()
    assert set(everything) == {"fr_blocked", "fr_unknown", "fr_paused", "fr_running", "fr_done"}
    assert not any(f["overdue"] or f["leased"] for f in everything.values()), "停下的运行不算超期：执行器不会再认领它"
    assert everything["fr_blocked"]["error_code"] == "read_only_role"
    for window in ("", "?window_s=3600", "?window_s=2592000"):
        flows = c.get("/api/admin/app-runs/summary" + window).json()["flows"]
        assert flows["parked"] == {"paused": 1, "blocked": 1, "unknown": 1}, window
        assert (flows["active"], flows["overdue"]) == (1, 0)
    assert c.get("/api/admin/flow-runs/fr_blocked").json()["run"]["status"] == "blocked"


# ---------------------------------------------------------------- how the lists are read
def test_the_lists_are_read_through_indexes_by_the_statements_the_routes_build(world, statements):
    store, alice, bob = world["store"], world["alice"], world["bob"]
    a, b = world["alice_ws"]["id"], world["bob_ws"]["id"]
    root = new_id("run")
    run(store, root, user=alice, workspace=a)
    run(store, "run_child", user=alice, workspace=a, via="flow", correlation=root, parent=root)
    # a caller's correlation text that is also the start of an operation id
    run(store, "run_given", user=bob, workspace=b, via="api", correlation="tables")
    run(store, "run_tables", user=bob, workspace=b, app="tables", op="tables.record.create")
    run(store, "run_failed", user=bob, workspace=b, status="failed", error_code="boom")
    flow(store, "fr_a", user=alice, correlation=root)
    flow(store, "fr_b", user=bob, status="failed")
    c = world["holder"]("auditor")

    def read(path, **params):
        del statements[:]
        r = c.get(path, params=params)
        assert r.status_code == 200, r.text
        ran = run_statements(store, statements)
        assert ran, path
        page = [(sql, plan) for sql, _, plan in ran if " ORDER BY " in sql and " LIMIT ? OFFSET ?" in sql]
        counts = [args for sql, args, _ in ran if sql.startswith("SELECT COUNT(*) AS n FROM (SELECT 1 FROM ")]
        assert len(page) == 1 and len(counts) == 1 and counts[0][-1] == 10001, "一页一次读取；计数最多数到上限加一"
        for sql, _, plan in ran:
            assert not re.search(r"SCAN (r|f|op_runs|flow_runs)\b(?! USING COVERING INDEX)", plan), (sql, plan)
        return [row["id"] for row in r.json()["items"]], page[0][0], page[0][1], ran

    # one person, one workspace: the (…, created_at) index gives both the rows and their order
    for query, index in (({"user_id": alice["id"]}, "op_runs_user"), ({"workspace_id": a}, "op_runs_workspace"),
                         ({"user_id": alice["id"], "status": "attention"}, "op_runs_user")):
        ids, sql, plan, ran = read("/api/admin/app-runs", **query)
        assert "ORDER BY r.created_at DESC, r.rowid DESC" in sql and index in plan and "TEMP B-TREE" not in plan, (sql, plan)
        assert all(index in p for s, _, p in ran if s.startswith("SELECT COUNT(*)")), ran
    assert read("/api/admin/app-runs", user_id=alice["id"])[0] == ["run_child", root]
    ids, sql, plan, _ = read("/api/admin/flow-runs", user_id=bob["id"])
    assert ids == ["fr_b"] and "flow_runs_user_created" in plan and "TEMP B-TREE" not in plan, (sql, plan)

    # an id or a correlation id: matched exactly, through its own index, never with LIKE or OR
    ids, sql, plan, _ = read("/api/admin/app-runs", q=root)
    assert ids == ["run_child", root] and "op_runs_correlation" in plan and "TEMP B-TREE" not in plan, (sql, plan)
    assert "LIKE" not in sql and " OR " not in sql
    ids, sql, plan, _ = read("/api/admin/app-runs", q="run_child")
    assert ids == ["run_child"] and "sqlite_autoindex_op_runs_1" in plan and "LIKE" not in sql and " OR " not in sql, (sql, plan)
    ids, sql, plan, _ = read("/api/admin/app-runs", q="tables")
    assert ids == ["run_given"] and "op_runs_correlation" in plan and "LIKE" not in sql, "精确匹配在先：不再按操作名开头去找"
    # only a text that is neither is read as the start of an operation id
    ids, sql, plan, _ = read("/api/admin/app-runs", q="tables.")
    assert ids == ["run_tables"] and "r.op LIKE ? ESCAPE" in sql and "r.id=?" not in sql and "correlation_id=?" not in sql
    ids, sql, plan, _ = read("/api/admin/flow-runs", q=root)
    assert ids == ["fr_a"] and "flow_runs_correlation" in plan and "TEMP B-TREE" not in plan, (sql, plan)
    ids, sql, plan, _ = read("/api/admin/flow-runs", q="fr_a")
    assert ids == ["fr_a"] and "sqlite_autoindex_flow_runs_1" in plan, (sql, plan)
    assert read("/api/admin/flow-runs", q="fr_")[0] == [], "流程运行只按编号或关联编号精确查找"
    # a digest is looked for in the correlation index alone, never in the table
    token = admin_runs.correlation_token("tables")
    ids, sql, plan, ran = read("/api/admin/app-runs", q=token)
    assert ids == ["run_given"] and "op_runs_correlation" in plan
    scans = [p for s, _, p in ran if "SELECT DISTINCT correlation_id" in s]
    assert scans and all("COVERING INDEX op_runs_correlation" in p for p in scans), ran
    # the related runs of one run: the correlation index, in its own order
    del statements[:]
    assert [x["id"] for x in c.get(f"/api/admin/app-runs/{root}").json()["related"]] == ["run_child"]
    related = [plan for sql, _, plan in run_statements(store, statements) if "r.id<>?" in sql]
    assert len(related) == 1 and "op_runs_correlation" in related[0] and "TEMP B-TREE" not in related[0], related
    # the window counts: every statement of the summary route is answered by a state index
    del statements[:]
    assert c.get("/api/admin/app-runs/summary").status_code == 200
    summary = run_statements(store, statements)
    assert len(summary) > 20, "按状态逐个计数"
    for sql, _, plan in summary:
        assert re.search(r"USING (COVERING )?INDEX (op_runs_status|flow_runs_status_updated|flow_runs_due)\b", plan), (sql, plan)
        assert not re.search(r"SCAN (op_runs|flow_runs)\b(?! USING COVERING INDEX)", plan), (sql, plan)


def test_a_list_counts_no_further_than_the_cap_says_so_and_still_pages_past_it(world, statements):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    # fixture rows written in one statement: 10 001 succeeded runs, one more than a list counts
    store.execute(
        "INSERT INTO op_runs (id,op,app,op_version,workspace_id,user_id,principal,via,correlation_id,input_hash,input,"
        "status,created_at,updated_at) WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<10001) "
        "SELECT printf('run_bulk_%05d', i), 'forms.request.create', 'forms', 1, ?, ?, '{}', 'ui', printf('run_bulk_%05d', i), "
        "'FIXTURE-INPUT-HASH', '{}', 'succeeded', ?, ? FROM n", (ws, alice["id"], now(), now()))
    run(store, "run_failed", user=alice, workspace=ws, status="failed", error_code="boom")
    c = world["holder"]("auditor")
    first = c.get("/api/admin/app-runs?status=succeeded&limit=2").json()
    assert (first["total"], first["total_capped"], first["more"]) == (10000, True, True)
    assert [r["id"] for r in first["items"]] == ["run_bulk_10001", "run_bulk_10000"]
    ran = run_statements(store, statements)
    counts = [args for sql, args, _ in ran if sql.startswith("SELECT COUNT(*) AS n FROM (SELECT 1 FROM op_runs r")]
    assert len(counts) == 1 and counts[0][-1] == 10001
    # that many matches are read from the end of the table, not collected and sorted
    page = [plan for sql, _, plan in ran if " ORDER BY r.rowid DESC LIMIT ? OFFSET ?" in sql]
    assert len(page) == 1 and "TEMP B-TREE" not in page[0], page
    # the pages go on past what was counted, and the last one says it is the last
    last = c.get(f"/api/admin/app-runs?status=succeeded&limit=2&offset=10000&snapshot={first['snapshot']}").json()
    assert [r["id"] for r in last["items"]] == ["run_bulk_00001"]
    assert (last["total"], last["total_capped"], last["more"]) == (10000, True, False)
    everything = c.get("/api/admin/app-runs?limit=1").json()
    assert (everything["total"], everything["total_capped"], everything["more"]) == (10000, True, True)
    few = c.get("/api/admin/app-runs?status=failed").json()
    assert (few["total"], few["total_capped"], few["more"]) == (1, False, False)
    assert [r["id"] for r in few["items"]] == ["run_failed"]


def test_reading_never_waits_for_a_business_write_and_changes_nothing(world, tmp_path):
    store, alice, ws = world["store"], world["alice"], world["alice_ws"]["id"]
    run(store, "run_a", user=alice, workspace=ws, status="running", at=now() - 5000)
    flow(store, "fr_a", user=alice, status="retrying", deadline=now() - 10)
    c = world["holder"]("auditor")
    tables = ("op_runs", "flow_runs", "flow_steps", "flow_history")
    before = {t: store.all(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}
    events = store.one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    paths = ["/api/admin/app-runs?status=attention", "/api/admin/app-runs/summary", "/api/admin/app-runs/run_a",
             "/api/admin/flow-runs?status=attention", "/api/admin/flow-runs/fr_a"]
    # An application's write is open on another connection for the whole time the pages are
    # read: a route that opened a write transaction of its own would wait for it and time out.
    business = Store(tmp_path / "d")
    with business.tx():
        business.execute("UPDATE op_runs SET depth=depth WHERE id='run_a'")
        started = time.monotonic()
        answers = [c.get(path) for path in paths]
        waited = time.monotonic() - started
    assert [r.status_code for r in answers] == [200] * len(paths), texts(*answers)
    assert waited < 2.0, f"五个读取在写事务未提交时用了 {waited:.2f}s"
    after = {t: store.all(f"SELECT * FROM {t} ORDER BY rowid") for t in tables}
    assert after == before, "查看不改变任何运行：停滞的仍是 running，超期的仍在等待"
    assert store.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == events, "读取运行元数据不写审计行"
