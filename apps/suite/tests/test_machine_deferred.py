"""Work a client queued does not outlive the authorization that asked for it.

A DataPrep run, an Extract task and an approval wait are started through the machine API, the
grant behind them ends in each of the ways a grant can end, and the worker's next step is refused:
the run fails with the reason, and nothing more is written — no output dataset, no parsed result,
no decision handed to the wait. What is *not* an end of the grant (a refresh rotation, an access
token merely expiring) and what is not a credential at all (a run the person started in the page)
keeps working. Real store, real workers and real token rows; fixture accounts and data.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from synwevo_core.store import now


def _sibling(name: str):
    """A test module of this directory, by path (they are not importable by name everywhere)."""
    spec = importlib.util.spec_from_file_location("suite_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_api = _sibling("test_machine_api")
register, m, counts, CALLBACK = _api.register, _api.m, _api.counts, _api.CALLBACK
SCOPES = "dataprep.read dataprep.write forms.read forms.write extract.read extract.write approvals.read approvals.write"


@pytest.fixture()
def api(suite):
    register(suite, scopes="offline_access " + _api.ALL_SCOPES)
    suite.cookies.clear()
    return suite


def grant(api, who, scopes=SCOPES) -> dict:
    """A delegation with a refresh token, as the code exchange issues one: {auth, access, refresh}."""
    from synwevo_core.oidc import Clients, Grants
    from synwevo_core.sessions import Sessions
    p = api.people
    _, session = Sessions(p.store).create(p.uid(who), workspace_id=None, amr="password")
    out = Grants(p.store).issue_tokens(
        client=Clients(p.store).get("crm-sync"), with_refresh=True,
        code_row={"user_id": p.uid(who), "scope": scopes, "central_session_id": session["id"], "workspace_id": p.a})
    return {"auth": {"Authorization": "Bearer " + out["access_token"]}, "access": out["access_token"],
            "refresh": out["refresh_token"], "who": who}


# --------------------------------------------------------------------------------- ways a grant ends
def _token_revoked(api, g):
    from synwevo_core.oidc import Grants
    assert Grants(api.people.store).revoke_token(g["access"], "client_revoked", client_id="crm-sync")


def _consent_withdrawn(api, g):
    # what "Connected apps → withdraw" does to the tokens (oauth_consent.withdraw), without the page
    p = api.people
    n = p.store.execute("UPDATE oauth_tokens SET revoked_at=?, revoked_reason='user_withdrew' "
                        "WHERE user_id=? AND client_id='crm-sync' AND revoked_at IS NULL", (now(), p.uid(g["who"])))
    assert n >= 1


def _refresh_replayed(api, g):
    # the refresh token is spent, then presented again: the platform revokes the whole family
    from synwevo_core.errors import ServiceError
    from synwevo_core.oidc import Clients, Grants
    grants, client = Grants(api.people.store), Clients(api.people.store).get("crm-sync")
    grants.rotate_refresh(g["refresh"], client=client)
    with pytest.raises(ServiceError):
        grants.rotate_refresh(g["refresh"], client=client)


def _client_disabled(api, g):
    from synwevo_core.oidc import Clients
    assert Clients(api.people.store).disable("crm-sync")


def _registration_narrowed(api, g):
    register(api, scopes="offline_access forms.read tables.read")       # no longer registered for any write


ENDS = [(_token_revoked, "grant_revoked"), (_consent_withdrawn, "grant_revoked"),
        (_refresh_replayed, "grant_revoked"), (_client_disabled, "credential_revoked"),
        (_registration_narrowed, "scope_withdrawn")]
END_IDS = ["token revoked", "consent withdrawn", "refresh token replayed", "client disabled", "registration narrowed"]


# --------------------------------------------------------------------------------- DataPrep
def queued_run(api, g):
    data = m(api, g["auth"], "dataprep.dataset.from_records", {"name": "Fixture amounts", "columns": ["id", "amount"],
             "records": [{"id": "001", "amount": "0.10"}, {"id": "002", "amount": "0.20"}]}, key="rows")["output"]
    recipe = m(api, g["auth"], "dataprep.recipe.create", {"name": "Fixture recipe", "definition": {"steps": []}},
               key="recipe")["output"]
    m(api, g["auth"], "dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1}, key="publish")
    started = m(api, g["auth"], "dataprep.run.start", {"recipe_id": recipe["id"], "version": 1,
                "inputs": {"main": data["id"]}}, key="run", expect=202)
    assert started["status"] == "queued"
    return started["output"]["id"], started["run_id"]


def run_row(api, rid):
    return api.people.store.one("SELECT status, error_code, output_dataset_id FROM dataprep_runs WHERE id=?", (rid,))


def nothing_published(api, run_id, before):
    """No dataset beyond the input, no success event, and the receipt says why it stopped."""
    store = api.people.store
    assert counts(store, "dataprep_datasets") == before
    kinds = [e["type"] for e in store.all("SELECT type FROM app_events WHERE run_id=? ORDER BY seq", (run_id,))]
    assert "dataprep.run.succeeded" not in kinds and "dataprep.run.partial" not in kinds and "dataprep.run.failed" in kinds
    return store.one("SELECT status, error_code, output FROM op_runs WHERE id=?", (run_id,))


@pytest.mark.parametrize("end, code", ENDS, ids=END_IDS)
def test_a_queued_run_does_not_start_once_the_grant_has_ended(api, end, code):
    g = grant(api, "bob")
    rid, run_id = queued_run(api, g)
    before = counts(api.people.store, "dataprep_datasets")
    end(api, g)
    assert api.api.state.dataprep.tick()
    assert run_row(api, rid) == {"status": "failed", "error_code": code, "output_dataset_id": None}
    receipt = nothing_published(api, run_id, before)
    assert (receipt["status"], receipt["error_code"]) == ("failed", code)


@pytest.mark.parametrize("end, code", ENDS, ids=END_IDS)
def test_a_run_whose_grant_ends_while_it_computes_publishes_nothing(api, monkeypatch, end, code):
    from synwevo_suite.dataprep import service
    g = grant(api, "bob")
    rid, run_id = queued_run(api, g)
    before = counts(api.people.store, "dataprep_datasets")
    compute, seen = service.execute, []

    def compute_then_lose_the_grant(*args, **kwargs):
        result = compute(*args, **kwargs)                  # the real computation, to completion
        seen.append(len(result[0].rows))
        end(api, g)                                        # ... and only then the grant ends
        return result

    monkeypatch.setattr(service, "execute", compute_then_lose_the_grant)
    assert api.api.state.dataprep.tick()
    assert seen == [2], "the run was computing when the grant ended"
    assert run_row(api, rid) == {"status": "failed", "error_code": code, "output_dataset_id": None}
    nothing_published(api, run_id, before)
    # and it stays stopped: another worker pass has nothing to resume
    assert api.api.state.dataprep.tick() is False
    assert counts(api.people.store, "dataprep_datasets") == before


def test_what_is_not_the_end_of_a_grant_does_not_stop_a_run(api):
    from synwevo_core.oidc import Clients, Grants
    p = api.people
    g = grant(api, "bob")
    rid, _ = queued_run(api, g)
    # the client refreshed (the old token is "rotated", a successor stands) and the access token expired
    Grants(p.store).rotate_refresh(g["refresh"], client=Clients(p.store).get("crm-sync"))
    p.store.execute("UPDATE oauth_tokens SET access_expires_at=? WHERE client_id='crm-sync'", (now() - 60,))
    # another person's grant for the same client ends
    other = grant(api, "alice")
    _token_revoked(api, other)
    assert api.api.state.dataprep.tick()
    done = run_row(api, rid)
    assert done["status"] == "succeeded" and done["error_code"] is None and done["output_dataset_id"]


UNREADABLE = ["{not json", json.dumps("a string"), json.dumps({"kind": "user"}), "",
              json.dumps({"kind": "delegated", "client_id": "someone-else"})]
UNREADABLE_IDS = ["malformed", "not an object", "says a person", "empty", "names another client"]


@pytest.mark.parametrize("descriptor", UNREADABLE, ids=UNREADABLE_IDS)
@pytest.mark.parametrize("ended", [True, False], ids=["grant ended", "grant standing"])
def test_a_clients_run_with_an_unreadable_descriptor_is_stopped_not_taken_for_the_persons_own(api, descriptor, ended):
    """Who started a run is read from how it was made (via, the recorded delegation). A stored
    descriptor that cannot be read — or that says something else — must not turn a client's run
    into one the person started in the page, which no revocation would ever stop."""
    g = grant(api, "bob")
    rid, run_id = queued_run(api, g)
    store = api.people.store
    made = store.one("SELECT via, delegation FROM op_runs WHERE id=?", (run_id,))
    assert made["via"] == "api" and json.loads(made["delegation"])["kind"] == "oauth_client"
    before = counts(store, "dataprep_datasets")
    assert store.execute("UPDATE op_runs SET principal=? WHERE id=?", (descriptor, run_id)) == 1
    if ended:
        _client_disabled(api, g)
    assert api.api.state.dataprep.tick()
    assert run_row(api, rid) == {"status": "failed", "error_code": "unknown_principal", "output_dataset_id": None}
    receipt = nothing_published(api, run_id, before)
    assert (receipt["status"], receipt["error_code"]) == ("failed", "unknown_principal")


@pytest.mark.parametrize("descriptor", UNREADABLE[:4], ids=UNREADABLE_IDS[:4])
def test_standing_refuses_a_clients_run_it_cannot_attribute_whichever_mark_remains(api, descriptor):
    """Either mark of a client's run is enough to hold it to a credential: `via`, or the recorded
    delegation. Losing one of them (and the descriptor) does not make it a person's own."""
    from synwevo_core.errors import ServiceError
    g = grant(api, "bob")
    _, run_id = queued_run(api, g)
    store, caps = api.people.store, api.api.state.capabilities
    assert caps.standing(run_id)["app"] == "dataprep"            # as made: a standing credential
    store.execute("UPDATE op_runs SET principal=? WHERE id=?", (descriptor, run_id))
    for via, delegation in (("api", None), ("flow", json.dumps({"kind": "oauth_client", "client_id": "crm-sync"}))):
        store.execute("UPDATE op_runs SET via=?, delegation=? WHERE id=?", (via, delegation, run_id))
        with pytest.raises(ServiceError) as e:
            caps.standing(run_id)
        assert (e.value.status, e.value.code) == (403, "unknown_principal")


def test_a_run_the_person_started_in_the_page_is_not_a_credentials_to_stop(api):
    p = api.people
    g = grant(api, "bob")
    api.sign_in("bob")
    page = lambda name, payload: api.post(f"/api/suite/ops/dataprep.{name}", headers={"X-Synwevo-CSRF": "fixture-csrf"},  # noqa: E731
                                          json={"workspace_id": p.a, "input": payload}).json()["output"]
    data = page("dataset.from_records", {"name": "Page rows", "columns": ["id"], "records": [{"id": "1"}]})
    recipe = page("recipe.create", {"name": "Page recipe", "definition": {"steps": []}})
    page("recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1})
    started = page("run.start", {"recipe_id": recipe["id"], "version": 1, "inputs": {"main": data["id"]}})
    api.cookies.clear()
    _consent_withdrawn(api, g)                             # every grant Bob gave the client ends,
    _client_disabled(api, g)                               # and the client itself is switched off
    assert api.api.state.dataprep.tick()
    assert run_row(api, started["id"])["status"] == "succeeded"


def test_a_queued_run_reads_its_sources_only_as_far_as_the_grant_still_reaches(api):
    """The dataset came from Forms. The client keeps its DataPrep scopes but is no longer registered
    for forms.read: the run it queued may not read the Forms answers on its behalf any more."""
    p = api.people
    g = grant(api, "bob")
    form = m(api, g["auth"], "forms.form.create", {"title": "Fixture source", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}}, key="form")["output"]
    m(api, g["auth"], "forms.form.publish", {"form_id": form["id"], "draft_rev": 1}, key="form-publish")
    link = m(api, g["auth"], "forms.request.create", {"form_id": form["id"]}, key="request")["output"]["url"]
    sent = api.post(f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}/submit",
                    json={"version": 1, "answers": {"f_amount01": "0001"}, "idempotency_key": "fixture-submit"})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]
    data = m(api, g["auth"], "dataprep.dataset.from_forms", {"name": "Form snapshot", "submission_ids": [sub]},
             key="import")["output"]
    recipe = m(api, g["auth"], "dataprep.recipe.create", {"name": "Fixture recipe", "definition": {"steps": []}},
               key="recipe")["output"]
    m(api, g["auth"], "dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1}, key="publish")
    started = m(api, g["auth"], "dataprep.run.start", {"recipe_id": recipe["id"], "version": 1,
                "inputs": {"main": data["id"]}}, key="run", expect=202)
    before = counts(p.store, "dataprep_datasets")
    register(api, scopes="offline_access dataprep.read dataprep.write")           # forms.read is gone
    assert api.api.state.dataprep.tick()
    assert run_row(api, started["output"]["id"]) == {"status": "failed", "error_code": "insufficient_scope",
                                                     "output_dataset_id": None}
    assert counts(p.store, "dataprep_datasets") == before


# --------------------------------------------------------------------------------- Extract
class CsvReader:
    """Test fixture reader: real CSV parsing only, no simulated OCR. Defined here, at module level,
    because the worker hands the reader to a spawned parsing process, which has to import it."""
    def describe(self):
        return {"ocr": False}

    def extract(self, data, filename, *, ocr=False):
        from synwevo_core.member_import import csv_extraction
        return csv_extraction("csv-test-fixture", "csv", data, ",")


@pytest.fixture()
def extracting(api):
    """A published template and an uploaded file (in the page: the machine API has no file routes),
    parsed by the fixture reader above."""
    fx = _sibling("test_extract")
    api.sign_in("alice")
    api.api.state.extract.reader = CsvReader()
    tid, fid = fx.template(api), fx.upload(api)["id"]
    api.cookies.clear()
    return tid, fid


def queued_task(api, g, extracting):
    tid, fid = extracting
    made = m(api, g["auth"], "extract.task.create", {"template_id": tid, "version": 1, "file_id": fid, "ocr": False},
             key="task", expect=202)
    return made["output"]["id"], made["run_id"]


def task_row(api, task_id):
    return api.people.store.one("SELECT status, error_code, revision, parse_json, machine_json, current_json "
                                "FROM extract_tasks WHERE id=?", (task_id,))


@pytest.mark.parametrize("end, code", ENDS, ids=END_IDS)
def test_a_queued_extraction_does_not_run_once_the_grant_has_ended(api, extracting, end, code):
    g = grant(api, "alice")
    task_id, run_id = queued_task(api, g, extracting)
    end(api, g)
    api.api.state.extract.tick()
    row = task_row(api, task_id)
    assert (row["status"], row["error_code"]) == ("failed", code)
    assert row["parse_json"] is None and row["machine_json"] is None and row["current_json"] is None
    receipt = api.people.store.one("SELECT status FROM op_runs WHERE id=?", (run_id,))
    assert receipt["status"] == "failed"


def test_an_extraction_whose_grant_ends_while_it_parses_stores_nothing(api, extracting, monkeypatch):
    from synwevo_suite.extract import service
    g = grant(api, "alice")
    task_id, _ = queued_task(api, g, extracting)
    real, seen = service.extract, []

    def extract_then_lose_the_grant(definition, parsed):
        values = real(definition, parsed)                  # the real field extraction over the parsed file
        seen.append(sorted(values))
        _token_revoked(api, g)
        return values

    monkeypatch.setattr(service, "extract", extract_then_lose_the_grant)
    api.api.state.extract.tick()
    assert seen == [["amount", "code"]], "the file was parsed and its fields extracted before the grant ended"
    row = task_row(api, task_id)
    assert (row["status"], row["error_code"]) == ("failed", "grant_revoked")
    assert row["parse_json"] is None and row["machine_json"] is None and row["current_json"] is None


def test_an_extraction_under_a_standing_grant_completes(api, extracting):
    g = grant(api, "alice")
    task_id, _ = queued_task(api, g, extracting)
    api.api.state.extract.tick()
    row = task_row(api, task_id)
    assert row["status"] == "waiting_input" and row["revision"] == 1 and json.loads(row["machine_json"])["code"]


# --------------------------------------------------------------------------------- Approvals
def waiting(api, g):
    p = api.people
    definition = {"fields": [{"id": "f_amount", "label": "Amount", "type": "money", "currency": "USD", "required": True}],
                  "stages": [{"id": "s_first", "name": "Independent review", "mode": "all",
                              "approvers": {"users": [p.uid("bob")]}}]}
    t = m(api, g["auth"], "approvals.template.create", {"title": "Fixture expense", "definition": definition},
          key="template")["output"]
    m(api, g["auth"], "approvals.template.publish", {"template_id": t["id"], "draft_rev": 1}, key="publish")
    r = m(api, g["auth"], "approvals.request.create", {"template_id": t["id"], "title": "Fixture claim",
          "values": {"f_amount": "12.50"}, "basis": "Invoice fixture"}, key="claim")["output"]
    wait = m(api, g["auth"], "approvals.request.wait", {"request_id": r["id"], "revision": 1}, key="wait", expect=202)
    return r, wait["run_id"]


def bob_approves(api, r):
    p = api.people
    task = next(x for x in r["tasks"] if x["assignee_id"] == p.uid("bob"))
    out = api.caps.invoke("approvals.request.decide", {"request_id": r["id"], "state_version": r["state_version"],
                          "task_id": task["id"], "decision": "approve", "note": "Fixture review"},
                          user_id=p.uid("bob"), workspace_id=p.a)
    assert out["status"] == "succeeded"


@pytest.mark.parametrize("end, code", ENDS, ids=END_IDS)
def test_a_wait_is_not_told_the_decision_once_the_grant_has_ended(api, end, code):
    p = api.people
    g = grant(api, "alice")
    r, wait_run = waiting(api, g)
    end(api, g)
    bob_approves(api, r)                                   # the decision is real and stands
    assert p.store.one("SELECT status FROM approval_requests WHERE id=?", (r["id"],))["status"] == "approved"
    wait = p.store.one("SELECT status, error_code, output FROM op_runs WHERE id=?", (wait_run,))
    assert (wait["status"], wait["error_code"]) == ("failed", code)
    told = wait["output"] if isinstance(wait["output"], dict) else json.loads(wait["output"])
    assert told == {"request_id": r["id"], "reason": code} and "decision" not in told
    assert p.store.one("SELECT status FROM approval_waits WHERE run_id=?", (wait_run,))["status"] == "failed"


def test_a_wait_under_a_standing_grant_and_a_wait_from_the_page_are_told(api):
    p = api.people
    g = grant(api, "alice")
    r, wait_run = waiting(api, g)
    page_wait = api.caps.invoke("approvals.request.wait", {"request_id": r["id"], "revision": 1},
                                user_id=p.uid("alice"), workspace_id=p.a)
    _token_revoked(api, grant(api, "bob"))                 # someone else's grant: not this wait's
    bob_approves(api, r)
    for run in (wait_run, page_wait["run_id"]):
        wait = p.store.one("SELECT status, output FROM op_runs WHERE id=?", (run,))
        told = wait["output"] if isinstance(wait["output"], dict) else json.loads(wait["output"])
        assert wait["status"] == "succeeded" and told["decision"] == "approved", run
