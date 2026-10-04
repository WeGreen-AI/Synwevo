"""The business applications' machine API (`/api/suite/v1`), with fixture accounts and real tokens.

The tokens are minted the way the token endpoint mints them (`oidc.Clients.register`,
`oidc.Grants.issue_tokens`) against the isolated fixture store; the HTTP stack, the gate, the
capability layer and the applications are the real ones. What each test expects is stated from the
rules — who may do what — not read back from the implementation.

Covers acceptance A4 (the page and the API are one operation), the identity rules of the API
platform (audience, scope, live role, one workspace per credential), and the rule that a
credential never reaches a person's own Agent, spending or consent records.
"""
from __future__ import annotations

import pytest

from synwevo_core.errors import ServiceError

SUITE = "urn:synwevo:platform:suite"
ALL_SCOPES = ("forms.read forms.write tables.read tables.write dataprep.read dataprep.write "
              "extract.read extract.write extract.decide approvals.read approvals.write approvals.decide")
CALLBACK = "https://crm.example.test/callback"


# --------------------------------------------------------------------------------- fixtures
def register(suite, client_id="crm-sync", scopes=ALL_SCOPES, **kw):
    from synwevo_core.oidc import Clients
    row, _ = Clients(suite.people.store).register(
        client_id=client_id, app_id="platform", name=f"Fixture {client_id}", redirect_uris=[CALLBACK],
        scopes="openid " + scopes, confidential=True, **kw)
    return row


def bearer(suite, who, scopes, *, client_id="crm-sync", ws=None, audience=None) -> dict:
    """An access token for `who`, as the authorization-code exchange would issue it."""
    from synwevo_core.oidc import Clients, Grants
    from synwevo_core.sessions import Sessions
    p = suite.people
    client = Clients(p.store).get(client_id)
    _, session = Sessions(p.store).create(p.uid(who), workspace_id=None, amr="password")
    out = Grants(p.store).issue_tokens(
        client=client, with_refresh=False, audience=audience,
        code_row={"user_id": p.uid(who), "scope": scopes, "central_session_id": session["id"],
                  "workspace_id": ws if ws is not None else p.a})
    return {"Authorization": "Bearer " + out["access_token"]}


def m(suite, auth, name, input=None, *, key=None, expect=200, **body):
    headers = dict(auth)
    if key:
        headers["Idempotency-Key"] = key
    r = suite.post(f"/api/suite/v1/ops/{name}", json={"input": input or {}, **body}, headers=headers)
    assert r.status_code == expect, (name, r.status_code, r.text)
    return r.json()


def refused(response, status, code):
    assert response.status_code == status, (response.status_code, response.text)
    body = response.json()
    assert body["error"]["code"] == code, body
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]
    return body["error"]


def counts(store, *patterns) -> dict:
    """Row counts of every table whose name matches, so "nothing was created" is checked on the
    tables that exist rather than on a list that goes stale."""
    names = [r["name"] for r in store.all("SELECT name FROM sqlite_master WHERE type='table'")
             if any(r["name"].startswith(p) for p in patterns)]
    return {n: store.one(f"SELECT COUNT(*) AS n FROM {n}")["n"] for n in sorted(names)}


@pytest.fixture()
def api(suite):
    register(suite)
    suite.cookies.clear()
    return suite


# --------------------------------------------------------------------------------- who is calling
def test_a_session_cookie_is_not_a_credential_and_refusals_use_the_machine_envelope(api):
    refused(api.get("/api/suite/v1/whoami"), 401, "unauthorized")
    api.sign_in("alice")                                   # a live browser session on this origin
    refused(api.get("/api/suite/v1/whoami"), 401, "unauthorized")
    refused(api.post("/api/suite/v1/ops/forms.form.list", json={"input": {}},
                     headers={"X-Synwevo-CSRF": "fixture-csrf"}), 401, "unauthorized")
    # and the other way round: a bearer token does not open the session routes
    api.cookies.clear()
    auth = bearer(api, "alice", ALL_SCOPES)
    assert api.post("/api/suite/ops/forms.form.list", json={"workspace_id": api.people.a, "input": {}},
                    headers=auth).status_code == 401


def test_whoami_says_who_for_whom_where_and_what(api):
    p = api.people
    who = api.get("/api/suite/v1/whoami", headers=bearer(api, "bob", "forms.read tables.write")).json()
    assert who["kind"] == "delegated" and who["client_id"] == "crm-sync"
    assert who["user_id"] == p.uid("bob") and who["workspace_id"] == p.a and who["role"] == "editor"
    assert who["scopes"] == ["forms.read", "tables.write"] and who["resource"] == SUITE
    assert who["apps"] == ["approvals", "dataprep", "extract", "forms", "tables"]


def test_tokens_meant_for_something_else_are_refused(api):
    from synwevo_core.oidc import Clients, Grants, docs_resource, workspace_resource
    p = api.people
    store = p.store
    # a legacy Docs access key
    refused(api.get("/api/suite/v1/whoami", headers={"Authorization": "Bearer dw_abc123_notasecret"}),
            403, "wrong_credential")
    # a token that is not a token
    refused(api.get("/api/suite/v1/whoami", headers={"Authorization": "Bearer nonsense"}), 401, "unauthorized")
    # a delegation issued for the Docs API
    Clients(store).register(client_id="docs-cli", app_id="docweave", name="Fixture Docs CLI",
                            redirect_uris=[CALLBACK], scopes="openid docweave.read docweave.write")
    docs = bearer(api, "alice", "docweave.read docweave.write", client_id="docs-cli")
    err = refused(api.get("/api/suite/v1/whoami", headers=docs), 401, "invalid_audience")
    assert err["details"] == {"audience": docs_resource(), "expected": SUITE}
    # a directory service account
    Clients(store).register(client_id="dir-svc", app_id="platform", name="Fixture directory", redirect_uris=[],
                            kind="service", workspace_id=p.a, confidential=True, api_scopes="workspace.read")
    svc = Grants(store).issue_for_service(client=Clients(store).get("dir-svc"), scope="workspace.read")
    err = refused(api.get("/api/suite/v1/whoami", headers={"Authorization": "Bearer " + svc["access_token"]}),
                  401, "invalid_audience")
    assert err["details"]["audience"] == workspace_resource()
    # the same service account, mis-registered so its token names this API: still not a person
    Clients(store).register(client_id="dir-svc", app_id="platform", name="Fixture directory", redirect_uris=[],
                            kind="service", workspace_id=p.a, confidential=True, api_scopes="workspace.read",
                            audiences=[SUITE, workspace_resource()])
    svc = Grants(store).issue_for_service(client=Clients(store).get("dir-svc"), scope="workspace.read",
                                          audience=SUITE)
    err = refused(api.get("/api/suite/v1/whoami", headers={"Authorization": "Bearer " + svc["access_token"]}),
                  403, "wrong_credential")
    assert err["details"] == {"reason": "service_account_not_supported"}
    # a sign-in token for this client: it proves who signed in, it is not permission to run anything
    refused(api.get("/api/suite/v1/whoami", headers=bearer(api, "alice", "openid", audience=SUITE)),
            403, "wrong_credential")


def test_a_service_account_cannot_be_registered_for_these_scopes(api):
    from synwevo_core.oidc import Clients
    with pytest.raises(ServiceError) as e:
        Clients(api.people.store).register(
            client_id="robot", app_id="platform", name="Fixture robot", redirect_uris=[], kind="service",
            workspace_id=api.people.a, confidential=True, api_scopes="forms.write")
    assert e.value.code == "suite_delegation_only"
    with pytest.raises(ServiceError) as e:                # and not under another application's client
        Clients(api.people.store).register(client_id="docs-x", app_id="docweave", name="Fixture",
                                           redirect_uris=[CALLBACK], scopes="openid forms.read")
    assert e.value.code == "scope_wrong_app"


def test_everything_about_the_person_is_asked_again_on_every_call(api):
    from synwevo_core.oidc import Clients, Grants
    p = api.people
    auth = bearer(api, "bob", "forms.read forms.write")
    form = m(api, auth, "forms.form.create", {"title": "While an editor"})["output"]

    # moved to read-only: the token still says forms.write, the person no longer can
    p.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?", (p.a, p.uid("bob")))
    err = m(api, auth, "forms.form.create", {"title": "As a viewer"}, expect=403)["error"]
    assert err["code"] == "read_only_role"
    assert m(api, auth, "forms.form.get", {"form_id": form["id"]})["output"]["id"] == form["id"]

    # the client's registration is narrowed: effective at once, not when the token expires
    register(api, scopes="forms.read")
    err = refused(api.post("/api/suite/v1/ops/forms.form.get", json={"input": {"form_id": form["id"]}},
                           headers=bearer(api, "bob", "forms.write")), 403, "forbidden")
    assert err["details"] == {"reason": "scope_withdrawn"}
    assert api.get("/api/suite/v1/whoami", headers=auth).json()["scopes"] == ["forms.read"]

    # removed from the workspace
    p.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (p.a, p.uid("bob")))
    refused(api.get("/api/suite/v1/whoami", headers=auth), 403, "forbidden")
    p.ident.add_member(p.a, p.uid("bob"), "editor")
    assert api.get("/api/suite/v1/whoami", headers=auth).status_code == 200

    # the workspace is frozen
    p.store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (p.a,))
    refused(api.get("/api/suite/v1/whoami", headers=auth), 403, "forbidden")
    p.store.execute("UPDATE workspaces SET status='active' WHERE id=?", (p.a,))

    # the account is suspended
    p.store.execute("UPDATE users SET status='suspended' WHERE id=?", (p.uid("bob"),))
    refused(api.get("/api/suite/v1/whoami", headers=auth), 401, "unauthorized")
    p.store.execute("UPDATE users SET status='active' WHERE id=?", (p.uid("bob"),))

    # the token is revoked; the client is disabled
    assert Grants(p.store).revoke_token(auth["Authorization"].split()[1], "fixture")
    refused(api.get("/api/suite/v1/whoami", headers=auth), 401, "unauthorized")
    again = bearer(api, "bob", "forms.read")
    assert api.get("/api/suite/v1/whoami", headers=again).status_code == 200
    Clients(p.store).disable("crm-sync")
    refused(api.get("/api/suite/v1/whoami", headers=again), 401, "client_unavailable")


# --------------------------------------------------------------------------------- what it may ask
def test_a_scope_is_needed_and_is_not_enough(api):
    p = api.people
    reader = bearer(api, "bob", "forms.read")
    err = m(api, reader, "forms.form.create", {"title": "No"}, expect=403)["error"]
    assert err["code"] == "insufficient_scope" and err["details"]["required_scope"] == "forms.write"
    err = m(api, reader, "tables.table.list", expect=403)["error"]
    assert err["details"]["required_scope"] == "tables.read"
    assert m(api, reader, "forms.form.list")["output"]["forms"] == []

    # a viewer who granted "write": the grant cannot give what the person does not have
    viewer = bearer(api, "vera", "forms.read forms.write")
    assert m(api, viewer, "forms.form.create", {"title": "No"}, expect=403)["error"]["code"] == "read_only_role"
    # an editor asking for a manage-level operation
    editor = bearer(api, "bob", "tables.read tables.write")
    assert m(api, editor, "tables.table.create", {"name": "No"}, expect=403)["error"]["code"] == "manage_required"
    # someone who is not in the workspace the token names cannot even be given a principal
    outsider = bearer(api, "out", "forms.read")
    refused(api.get("/api/suite/v1/whoami", headers=outsider), 403, "forbidden")
    assert p.store.one("SELECT COUNT(*) AS n FROM forms")["n"] == 0


def test_one_credential_one_workspace(api):
    p = api.people
    auth = bearer(api, "alice", "forms.read forms.write")            # consented for workspace A
    made = m(api, auth, "forms.form.create", {"title": "In A"})
    assert made["workspace_id"] == p.a
    # Alice is a member of B too; this credential is not for B
    err = m(api, auth, "forms.form.list", workspace_id=p.b, expect=403)["error"]
    assert err["code"] == "workspace_mismatch"
    assert m(api, auth, "forms.form.list", workspace_id=p.a)["output"]["forms"][0]["title"] == "In A"
    # a credential for B (where she is a viewer) reads B and writes nothing
    in_b = bearer(api, "alice", "forms.read forms.write", ws=p.b)
    assert m(api, in_b, "forms.form.list")["output"]["forms"] == []
    assert m(api, in_b, "forms.form.create", {"title": "In B"}, expect=403)["error"]["code"] == "read_only_role"
    rows = p.store.all("SELECT workspace_id FROM forms")
    assert [r["workspace_id"] for r in rows] == [p.a]


def test_operations_that_are_a_persons_own_have_no_machine_door(api):
    """The Agent, spending and consent: refused for every credential, and nothing is written."""
    p = api.people
    reg = api.caps.registry
    from synwevo_core.capabilities import machine_scope
    closed = sorted(op.id for op in reg.operations() if machine_scope(op, reg.app(op.app)) is None)
    # named, so opting the Agent in — or losing one of these — fails here rather than passing quietly
    for must in ("agent.turn.start", "agent.turn.quote", "agent.quote.get", "agent.quote.revoke",
                 "agent.billing.budget.get", "agent.billing.budget.set", "agent.billing.offer",
                 "agent.conversation.list", "agent.memory.list", "agent.flow.start",
                 "agent.workspace_policy.set", "forms.form.duplicate"):
        assert must in closed, must
    assert all(op.app != "agent" or op.id in closed for op in reg.operations())
    assert all(op.need != "personal" or op.id in closed for op in reg.operations())

    before = counts(p.store, "agent_", "billing_", "op_runs", "app_events")
    auth = bearer(api, "alice", ALL_SCOPES)
    for op_id in closed:
        r = api.post(f"/api/suite/v1/ops/{op_id}", json={"input": {}}, headers={**auth, "Idempotency-Key": "k1"})
        refused(r, 403, "person_required")
        # The capability layer refuses on its own, whatever HTTP entry passed the call along —
        # asked with every scope there is, so it is the operation that is closed, not the caller
        # that came without scopes (that refusal is a different branch, checked just after).
        target = None if reg.get(op_id).need == "personal" else p.a
        with pytest.raises(ServiceError) as e:
            api.caps.invoke(op_id, {}, user_id=p.uid("alice"), via="api", workspace_id=target,
                            scopes=frozenset(ALL_SCOPES.split()) | {f"{reg.get(op_id).app}.{verb}"
                                                                    for verb in ("read", "write", "decide")})
        assert (e.value.status, e.value.code) == (403, "person_required"), op_id
        assert e.value.detail == {"operation": op_id}
        with pytest.raises(ServiceError) as e:
            api.caps.invoke(op_id, {}, user_id=p.uid("alice"), via="api", workspace_id=target)
        assert (e.value.status, e.value.code) == (403, "person_required"), op_id
    assert counts(p.store, "agent_", "billing_", "op_runs", "app_events") == before
    refused(api.post("/api/suite/v1/ops/agent.turn.launch", json={"input": {}}, headers=auth), 404, "unknown_operation")


def test_the_scope_each_operation_needs_is_derived_and_complete(api):
    from synwevo_core.capabilities import machine_scope
    from synwevo_core.oidc import SUITE_API_SCOPES
    reg = api.caps.registry
    scopes = {op.id: machine_scope(op, reg.app(op.app)) for op in reg.operations()}
    open_ = {k: v for k, v in scopes.items() if v is not None}
    # every scope that can be granted opens something, and nothing needs a scope that cannot be granted
    assert set(open_.values()) == set(SUITE_API_SCOPES)
    # a person's decisions, by name
    assert sorted(k for k, v in open_.items() if v.endswith(".decide")) == [
        "approvals.request.decide", "approvals.request.repair", "approvals.request.transfer",
        "extract.task.confirm", "extract.task.correct"]
    # a read scope never opens something that changes anything
    for op in reg.operations():
        s = scopes[op.id]
        if s and s.endswith(".read"):
            assert op.side_effect == "none" and op.need == "read", op.id
        if s:
            assert s.split(".")[0] == op.app and reg.app(op.app).machine
    listed = api.get("/api/suite/v1/operations", headers=bearer(api, "alice", "forms.read")).json()
    assert {o["id"]: o["scope"] for o in listed["operations"]} == open_
    assert {o["id"] for o in listed["operations"] if o["granted"]} == {k for k, v in open_.items() if v == "forms.read"}
    only = api.get("/api/suite/v1/operations?app=tables", headers=bearer(api, "alice", "forms.read")).json()
    assert {o["app"] for o in only["operations"]} == {"tables"}
    create = next(o for o in listed["operations"] if o["id"] == "forms.request.create")
    assert create["idempotency"] == "required" and create["path"] == "/api/suite/v1/ops/forms.request.create"
    assert "title" in create["input_schema"]["properties"] or "form_id" in create["input_schema"]["properties"]


def test_a_decision_needs_the_decide_scope_and_the_person_whose_decision_it_is(api):
    p = api.people
    owner = bearer(api, "alice", "approvals.read approvals.write")
    definition = {"fields": [{"id": "f_amount", "label": "Amount", "type": "money", "currency": "USD", "required": True}],
                  "stages": [{"id": "s_first", "name": "Independent review", "mode": "all",
                              "approvers": {"users": [p.uid("bob")]}}]}
    t = m(api, owner, "approvals.template.create", {"title": "Fixture expense", "definition": definition})["output"]
    m(api, owner, "approvals.template.publish", {"template_id": t["id"], "draft_rev": 1})
    r = m(api, owner, "approvals.request.create", {"template_id": t["id"], "title": "Fixture claim",
          "values": {"f_amount": "12.50"}, "basis": "Invoice fixture"}, key="claim-1")["output"]
    task = next(x for x in r["tasks"] if x["assignee_id"] == p.uid("bob"))
    decision = {"request_id": r["id"], "state_version": r["state_version"], "task_id": task["id"],
                "decision": "approve", "note": "Fixture review"}

    # waiting is accepted, not done
    waiting = m(api, owner, "approvals.request.wait", {"request_id": r["id"], "revision": 1}, key="wait-1", expect=202)
    assert waiting["status"] == "waiting_approval"

    # Bob granted "write": that is not "decide as me"
    err = m(api, bearer(api, "bob", "approvals.read approvals.write"), "approvals.request.decide", decision,
            expect=403)["error"]
    assert err["code"] == "insufficient_scope" and err["details"]["required_scope"] == "approvals.decide"
    # the requester holds the decide scope, but the task is Bob's
    not_hers = m(api, bearer(api, "alice", ALL_SCOPES), "approvals.request.decide", decision, expect=403)
    assert not_hers["error"]["code"] != "insufficient_scope"
    state = m(api, owner, "approvals.request.get", {"request_id": r["id"]})["output"]
    assert state["status"] == r["status"] and state["state_version"] == r["state_version"]

    done = m(api, bearer(api, "bob", "approvals.decide"), "approvals.request.decide", decision, key="decide-1")
    assert done["status"] == "succeeded"
    assert m(api, owner, "approvals.request.get", {"request_id": r["id"]})["output"]["status"] == "approved"
    row = p.store.one("SELECT decider_id, via FROM approval_decisions WHERE request_id=?", (r["id"],))
    assert row == {"decider_id": p.uid("bob"), "via": "api"}


# --------------------------------------------------------------------------------- reliable writes
def test_idempotency_is_per_client_and_never_runs_a_call_twice(api):
    p = api.people
    register(api, client_id="other-tool")
    auth = bearer(api, "alice", "tables.read tables.write")
    table = m(api, auth, "tables.table.create", {"name": "Fixture contacts"}, key="t-1")["output"]
    first = m(api, auth, "tables.record.create", {"table_id": table["id"], "cells": {}}, key="rec-1")
    again = m(api, auth, "tables.record.create", {"table_id": table["id"], "cells": {}}, key="rec-1")
    assert first["replayed"] is False and again["replayed"] is True and again["run_id"] == first["run_id"]
    # the header and the body are the same key
    body = m(api, auth, "tables.record.create", {"table_id": table["id"], "cells": {}}, idempotency_key="rec-1")
    assert body["run_id"] == first["run_id"]
    clash = api.post("/api/suite/v1/ops/tables.record.create", headers={**auth, "Idempotency-Key": "a"},
                     json={"input": {"table_id": table["id"], "cells": {}}, "idempotency_key": "b"})
    refused(clash, 400, "bad_idempotency_key")
    # same key, other parameters
    other_table = m(api, auth, "tables.table.create", {"name": "Second"}, key="t-2")["output"]
    conflict = m(api, auth, "tables.record.create", {"table_id": other_table["id"], "cells": {}}, key="rec-1",
                 expect=409)
    assert conflict["error"]["code"] == "idempotency_conflict"
    # another integration acting for the same person, using the same key, is its own call
    elsewhere = m(api, bearer(api, "alice", "tables.write", client_id="other-tool"),
                  "tables.record.create", {"table_id": table["id"], "cells": {}}, key="rec-1")
    assert elsewhere["replayed"] is False and elsewhere["run_id"] != first["run_id"]
    # and so is the page
    api.sign_in("alice")
    page = api.post("/api/suite/ops/tables.record.create", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                    json={"workspace_id": p.a, "input": {"table_id": table["id"], "cells": {}},
                          "idempotency_key": "rec-1"}).json()
    api.cookies.clear()
    assert page["replayed"] is False
    assert p.store.one("SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?", (table["id"],))["n"] == 3
    # a rotated token is the same client: the same call, not a second one
    rotated = m(api, bearer(api, "alice", "tables.write"), "tables.record.create",
                {"table_id": table["id"], "cells": {}}, key="rec-1")
    assert rotated["replayed"] is True and rotated["run_id"] == first["run_id"]
    for bad in ("with space", "x" * 129):
        refused(api.post("/api/suite/v1/ops/tables.record.create", headers={**auth, "Idempotency-Key": bad},
                         json={"input": {"table_id": table["id"], "cells": {}}}), 400, "bad_idempotency_key")
    refused(api.post("/api/suite/v1/ops/tables.record.create", headers=auth,
                     json={"input": {"table_id": table["id"], "cells": {}}, "idempotency_key": "换行\n"}),
            400, "bad_idempotency_key")
    assert p.store.one("SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?", (table["id"],))["n"] == 3


def test_a_caller_cannot_name_a_correlation_in_the_form_of_the_platforms_run_ids(api):
    """R3-AR-3: a correlation of the form the platform gives its runs (`run_` or `flowrun_` and
    twelve hex digits) is read as the platform's own — platform administration shows it as it is
    and takes every run under it for one chain. Bob, an editor of A, has learnt the id of one of
    Alice's runs. Naming it as the correlation of his own calls, from the page or from an
    integration, would put his runs in her chain; a constant of that form would link two
    integrations that never met. Both entries refuse such a value and run nothing. A label of
    his own is still taken, including one that merely starts with run_."""
    p = api.people
    owner = bearer(api, "alice", "tables.read tables.write")
    table = m(api, owner, "tables.table.create", {"name": "Fixture contacts"}, key="t-1")["output"]
    hers = m(api, owner, "tables.record.create", {"table_id": table["id"], "cells": {}}, key="rec-1")["run_id"]
    before = p.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"]
    bob = bearer(api, "bob", "tables.read tables.write")
    for forged in (hers, "run_00000000c0de", "flowrun_0123456789ab"):
        refused(api.post("/api/suite/v1/ops/tables.record.create", headers=bob,
                         json={"input": {"table_id": table["id"], "cells": {}}, "correlation_id": forged}),
                400, "bad_correlation_id")
        api.sign_in("bob")
        page = api.post("/api/suite/ops/tables.record.create", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                        json={"workspace_id": p.a, "input": {"table_id": table["id"], "cells": {}},
                              "correlation_id": forged})
        api.cookies.clear()
        assert page.status_code == 400 and page.json()["error"] == "bad_correlation_id", page.text
    assert p.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == before
    # Alice's chain is hers alone
    assert {r["user_id"] for r in p.store.all("SELECT user_id FROM op_runs WHERE correlation_id=?", (hers,))} \
        == {p.uid("alice")}
    # his own labels are his: kept as he wrote them
    for label in ("sync-7", "run_2026-10-04", "RUN_00000000C0DE", hers + "-retry"):
        own = m(api, bob, "tables.record.create", {"table_id": table["id"], "cells": {}}, correlation_id=label)
        assert p.store.one("SELECT correlation_id FROM op_runs WHERE id=?", (own["run_id"],))["correlation_id"] == label


def test_reaching_someone_outside_needs_a_key(api):
    p = api.people
    auth = bearer(api, "bob", "forms.read forms.write")
    form = m(api, auth, "forms.form.create", {"title": "Expense claim", "definition": {"fields": [
        {"id": "f_name0001", "type": "text", "label": "Full name", "required": True, "max_length": 100}]}})["output"]
    m(api, auth, "forms.form.publish", {"form_id": form["id"], "draft_rev": form["draft_rev"]})
    err = m(api, auth, "forms.request.create", {"form_id": form["id"]}, expect=400)["error"]
    assert err["code"] == "idempotency_key_required"
    assert p.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 0
    one = m(api, auth, "forms.request.create", {"form_id": form["id"]}, key="req-1")
    two = m(api, auth, "forms.request.create", {"form_id": form["id"]}, key="req-1")
    assert two["replayed"] is True and two["output"]["request_id"] == one["output"]["request_id"]
    assert p.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 1


def test_a_run_is_read_back_only_by_the_client_that_started_it(api):
    p = api.people
    register(api, client_id="other-tool")
    auth = bearer(api, "alice", "forms.read forms.write")
    made = m(api, auth, "forms.form.create", {"title": "Receipt"}, key="f-1")
    seen = api.get(f"/api/suite/v1/runs/{made['run_id']}", headers=auth).json()
    assert seen["id"] == made["run_id"] and seen["operation"] == "forms.form.create" and seen["via"] == "api"
    assert seen["input"] is None and seen["status"] == "succeeded"
    assert seen["delegation"]["client_id"] == "crm-sync"
    for other in (bearer(api, "alice", "forms.read forms.write", client_id="other-tool"),   # another client
                  bearer(api, "bob", "forms.read forms.write"),                              # another person
                  bearer(api, "alice", "forms.read forms.write", ws=p.b)):                   # another workspace
        refused(api.get(f"/api/suite/v1/runs/{made['run_id']}", headers=other), 404, "run_not_found")
    # the scope that made it is the scope that reads it
    err = refused(api.get(f"/api/suite/v1/runs/{made['run_id']}", headers=bearer(api, "alice", "forms.read")),
                  403, "insufficient_scope")
    assert err["details"]["required_scope"] == "forms.write"
    # a run made in the page is not this API's to show
    page = api.caps.invoke("forms.form.create", {"title": "From the page"}, user_id=p.uid("alice"), workspace_id=p.a)
    refused(api.get(f"/api/suite/v1/runs/{page['run_id']}", headers=auth), 404, "run_not_found")
    refused(api.get("/api/suite/v1/runs/run_missing", headers=auth), 404, "run_not_found")


def test_the_call_is_recorded_attributed_and_rate_limited_like_any_machine_call(api):
    p = api.people
    auth = bearer(api, "bob", "forms.read forms.write")
    r = api.post("/api/suite/v1/ops/forms.form.create", json={"input": {"title": "Audited"}},
                 headers={**auth, "X-Request-ID": "fixture-req-1"})
    assert r.status_code == 200 and r.headers["X-Request-ID"] == "fixture-req-1"
    assert r.headers["Cache-Control"] == "no-store" and "RateLimit-Policy" in r.headers and "RateLimit" in r.headers
    call = p.store.one("SELECT * FROM api_platform_calls WHERE request_id='fixture-req-1'")
    assert call["route"] == "/api/suite/v1/ops/forms.form.create" and call["status"] == 200
    assert call["client_id"] == "crm-sync" and call["workspace_id"] == p.a and call["credential_kind"] == "delegated"
    run = p.store.one("SELECT * FROM op_runs WHERE id=?", (r.json()["run_id"],))
    assert run["via"] == "api" and run["user_id"] == p.uid("bob")
    import json
    principal = json.loads(run["principal"])
    assert principal["kind"] == "delegated" and principal["client_id"] == "crm-sync"
    assert principal["delegation"]["client_id"] == "crm-sync" and "scopes" not in principal
    audit = p.store.one("SELECT * FROM audit_events WHERE action='forms.form.create' AND request_id='fixture-req-1'")
    assert audit["actor_id"] == p.uid("bob") and audit["workspace_id"] == p.a
    meta = audit["meta"] if isinstance(audit["meta"], dict) else json.loads(audit["meta"])
    assert meta["via"] == "api" and meta["delegation"]["client_id"] == "crm-sync"
    # a refusal is recorded with its reason, under the operation's own route
    api.post("/api/suite/v1/ops/tables.table.list", json={"input": {}}, headers={**auth, "X-Request-ID": "fixture-req-2"})
    call = p.store.one("SELECT * FROM api_platform_calls WHERE request_id='fixture-req-2'")
    assert (call["route"], call["status"], call["error_code"]) == ("/api/suite/v1/ops/tables.table.list", 403, "insufficient_scope")
    # nothing of the token is in either record
    token = auth["Authorization"].split()[1]
    for row in (call, run, audit):
        assert token not in json.dumps(row, default=str)


def test_malformed_calls_are_refused_before_anything_runs(api):
    p = api.people
    auth = bearer(api, "bob", "forms.read forms.write")
    r = api.post("/api/suite/v1/ops/forms.form.create", json={"inputs": {"title": "Typo"}}, headers=auth)
    refused(r, 400, "invalid")
    r = api.post("/api/suite/v1/ops/forms.form.create", content=b"{not json", headers={**auth, "Content-Type": "application/json"})
    refused(r, 400, "invalid")
    bad = m(api, auth, "forms.form.create", {"title": ""}, expect=422)["error"]
    assert bad["code"] == "invalid_input" and bad["details"]["fields"]
    # The wrong method: refused in the machine envelope either way. Which refusal depends on
    # whether this process also serves a built page shell (its catch-all answers GET first), as it
    # does in a deployment; what must never happen is a page, or the operation running.
    wrong = api.get("/api/suite/v1/ops/forms.form.create", headers=auth)
    assert (wrong.status_code, wrong.json()["error"]["code"]) in ((405, "method_not_allowed"), (404, "not_found"))
    assert wrong.headers["content-type"].startswith("application/json")
    refused(api.get("/api/suite/v1/nothing-here", headers=auth), 404, "not_found")
    assert p.store.one("SELECT COUNT(*) AS n FROM forms")["n"] == 0
    assert p.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == 0


def test_an_application_that_is_off_has_no_machine_route(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("suite_fixtures", Path(__file__).with_name("conftest.py"))
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)                            # this suite's fixtures, not the repository root's
    app = fx.make_client(tmp_path, monkeypatch, released="forms")
    people = fx.People(app)
    with TestClient(app, base_url=fx.WORKPLACE) as c:
        c.people, c.caps = people, app.state.capabilities
        register(c)
        auth = bearer(c, "alice", ALL_SCOPES)
        assert c.get("/api/suite/v1/whoami", headers=auth).json()["apps"] == ["forms"]
        assert m(c, auth, "forms.form.list")["output"]["forms"] == []
        refused(c.post("/api/suite/v1/ops/tables.table.list", json={"input": {}}, headers=auth), 404, "unknown_operation")
        refused(c.get("/api/suite/v1/operations?app=tables", headers=auth), 404, "app_unavailable")


def test_reaching_into_another_application_needs_that_applications_scope(api):
    """DataPrep imports Forms answers and re-reads them whenever the dataset is opened. The person
    may read both; the credential must hold both, or the import would be a way round `forms.read`."""
    p = api.people
    forms = bearer(api, "bob", "forms.read forms.write")
    form = m(api, forms, "forms.form.create", {"title": "Fixture source", "definition": {
        "fields": [{"id": "f_amount01", "type": "text", "label": "ID"}]}})["output"]
    m(api, forms, "forms.form.publish", {"form_id": form["id"], "draft_rev": 1})
    link = m(api, forms, "forms.request.create", {"form_id": form["id"]}, key="req")["output"]["url"]
    sent = api.post(f"/api/suite/public/forms/r/{link.rsplit('/', 1)[1]}/submit",
                    json={"version": 1, "answers": {"f_amount01": "0001"}, "idempotency_key": "fixture-submit"})
    assert sent.status_code == 200, sent.text
    sub = p.store.one("SELECT id FROM form_submissions WHERE workspace_id=?", (p.a,))["id"]

    narrow = bearer(api, "bob", "dataprep.read dataprep.write")
    wide = bearer(api, "bob", "dataprep.read dataprep.write forms.read")
    ask = {"name": "Form snapshot", "submission_ids": [sub]}
    err = m(api, narrow, "dataprep.dataset.from_forms", ask, key="import-1", expect=403)["error"]
    assert err["code"] == "insufficient_scope" and err["details"]["required_scope"] == "forms.read"
    assert p.store.one("SELECT COUNT(*) AS n FROM dataprep_datasets")["n"] == 0

    made = m(api, wide, "dataprep.dataset.from_forms", ask, key="import-2")
    dataset = made["output"]["id"]
    assert m(api, wide, "dataprep.dataset.get", {"dataset_id": dataset})["output"]["id"] == dataset
    # the dataset exists now; a credential without forms.read still cannot open what came from Forms
    # — and the refusal does not say where it came from: no scope, no application named
    err = m(api, narrow, "dataprep.dataset.get", {"dataset_id": dataset}, expect=403)["error"]
    assert err["code"] == "insufficient_scope" and "details" not in err and "forms" not in str(err).lower()
    # ... but one such dataset does not close the list for it: the row is withheld and counted, the
    # rest is there, and nothing in the answer says which application the withheld row came from
    plain = m(api, narrow, "dataprep.dataset.from_records", {"name": "Plain rows", "columns": ["id"],
              "records": [{"id": "1"}]}, key="plain")["output"]["id"]
    listed = api.post("/api/suite/v1/ops/dataprep.dataset.list", json={"input": {}}, headers=narrow)
    assert listed.status_code == 200, listed.text
    assert [d["id"] for d in listed.json()["output"]["items"]] == [plain] and listed.json()["output"]["hidden"] == 1
    assert "forms" not in listed.text and dataset not in listed.text
    everything = m(api, wide, "dataprep.dataset.list")["output"]
    assert {d["id"] for d in everything["items"]} == {plain, dataset} and everything["hidden"] == 0
    # the same for runs: one that read the Forms-sourced dataset is withheld from the narrow credential
    recipe = m(api, wide, "dataprep.recipe.create", {"name": "Fixture recipe", "definition": {"steps": []}},
               key="recipe")["output"]
    m(api, wide, "dataprep.recipe.publish", {"recipe_id": recipe["id"], "draft_rev": 1}, key="publish")
    for name, source in (("run-forms", dataset), ("run-plain", plain)):
        m(api, wide, "dataprep.run.start", {"recipe_id": recipe["id"], "version": 1, "inputs": {"main": source}},
          key=name, expect=202)
    runs = m(api, narrow, "dataprep.run.list")["output"]
    assert len(runs["items"]) == 1 and runs["hidden"] == 1
    assert len(m(api, wide, "dataprep.run.list")["output"]["items"]) == 2
    # nor read the import's receipt, nor have it replayed
    receipt = api.get(f"/api/suite/v1/runs/{made['run_id']}", headers=narrow).json()
    assert receipt["snapshot_hidden"] is True and receipt["output"] is None
    again = m(api, narrow, "dataprep.dataset.from_forms", ask, key="import-2", expect=403)["error"]
    assert again["code"] == "insufficient_scope" and again["details"]["required_scope"] == "forms.read"
    assert api.get(f"/api/suite/v1/runs/{made['run_id']}", headers=wide).json()["snapshot_hidden"] is False
    assert p.store.one("SELECT COUNT(*) AS n FROM dataprep_datasets")["n"] == 2
    # the page is the person, not a credential: nothing above changed what Bob himself can do
    api.sign_in("bob")
    page = api.post("/api/suite/ops/dataprep.dataset.get", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                    json={"workspace_id": p.a, "input": {"dataset_id": dataset}})
    assert page.status_code == 200 and page.json()["output"]["id"] == dataset


def test_there_is_no_unscoped_machine_caller(api):
    p = api.people
    who = {"user_id": p.uid("alice"), "workspace_id": p.a}
    with pytest.raises(ServiceError) as e:                 # via="api" with nothing said about scopes
        api.caps.invoke("forms.form.list", {}, via="api", **who)
    assert (e.value.status, e.value.code) == (403, "person_required")
    with pytest.raises(ServiceError) as e:
        api.caps.invoke("forms.form.list", {}, via="api", scopes=frozenset({"tables.read"}), **who)
    assert e.value.code == "insufficient_scope" and e.value.detail["required_scope"] == "forms.read"
    assert api.caps.invoke("forms.form.list", {}, via="api", scopes=frozenset({"forms.read"}), **who)["status"] == "succeeded"
    with pytest.raises(ValueError):                        # scopes are a credential's, not a person's
        api.caps.invoke("forms.form.list", {}, scopes=frozenset({"forms.read"}), **who)
    # and a credential's limits end with its call: the next caller on this thread is not bound by them
    assert api.caps.invoke("tables.table.list", {}, **who)["status"] == "succeeded"


def test_a_handler_cannot_widen_the_credential_it_runs_under(api):
    """Two fixture applications registered in this process. `alpha.pull` reaches into `beta` and
    claims a scope of its own while doing so; the outer credential stays the ceiling."""
    from pydantic import BaseModel
    from synwevo_core.apps import Apps
    from synwevo_core.capabilities import AppManifest, Operation
    p, reg = api.people, api.caps.registry
    text = {"zh-Hans": "夹具", "zh-Hant": "夾具", "en": "Fixture"}
    apps = Apps(p.store, p.ident)
    for name in ("alpha", "beta"):
        apps.register(app_id=name, name=f"Fixture {name}", icon="doc", launch_url=f"http://workplace.testserver/{name}")
        apps.enable_for_workspace(p.a, name)
        reg.register_app(AppManifest(id=name, name=f"Fixture {name}", path=f"/{name}", icon="doc", summary=text,
                                     machine=True))

    class Nothing(BaseModel):
        pass

    def pull(call, _):
        inner = call.capabilities.invoke("beta.peek", {}, user_id=call.user_id, workspace_id=call.workspace_id,
                                         via="api", scopes=frozenset({"beta.read"}))      # claims it for itself
        return {"seen": inner["output"]}

    reg.register(Operation(id="beta.peek", app="beta", title=text, summary="Fixture read", input=Nothing,
                           handler=lambda call, _: {"secret": "beta business"}))
    reg.register(Operation(id="alpha.pull", app="alpha", title=text, summary="Fixture write that reads beta",
                           input=Nothing, handler=pull, need="write", side_effect="write", agent="confirm"))
    who = {"user_id": p.uid("alice"), "workspace_id": p.a, "via": "api"}

    with pytest.raises(ServiceError) as e:
        api.caps.invoke("alpha.pull", {}, scopes=frozenset({"alpha.write"}), **who)
    # refused, without naming the application the handler reached into or the scope it wanted
    assert e.value.code == "insufficient_scope" and not e.value.detail and "beta" not in e.value.message
    allowed = api.caps.invoke("alpha.pull", {}, scopes=frozenset({"alpha.write", "beta.read"}), **who)
    assert allowed["output"] == {"seen": {"secret": "beta business"}}
    # the person in the page is not a credential: the same handler's inner claim is all there is
    page = api.caps.invoke("alpha.pull", {}, user_id=p.uid("alice"), workspace_id=p.a)
    assert page["output"] == {"seen": {"secret": "beta business"}}


def test_an_inner_operation_only_a_person_may_run_is_refused_without_being_named(api):
    """A handler serving a credential reaches an operation no credential can ask for — one of an
    application without a machine API. It is refused and does not run, and the refusal is the same
    one a missing scope gets: it does not name the inner operation, or say which kind it was."""
    from pydantic import BaseModel
    from synwevo_core.apps import Apps
    from synwevo_core.capabilities import AppManifest, Operation
    p, reg = api.people, api.caps.registry
    text = {"zh-Hans": "夹具", "zh-Hant": "夾具", "en": "Fixture"}
    apps = Apps(p.store, p.ident)
    for name, machine in (("alpha", True), ("vault", False)):
        apps.register(app_id=name, name=f"Fixture {name}", icon="doc", launch_url=f"http://workplace.testserver/{name}")
        apps.enable_for_workspace(p.a, name)
        reg.register_app(AppManifest(id=name, name=f"Fixture {name}", path=f"/{name}", icon="doc", summary=text,
                                     machine=machine))

    class Nothing(BaseModel):
        pass

    class How(BaseModel):
        as_credential: bool = False

    ran = []

    def open_vault(call, _):
        ran.append(call.via)
        return {"secret": "vault business"}

    def pull(call, how):
        claim = {"via": "api", "scopes": frozenset({"alpha.read", "vault.read"})} if how.as_credential else {"via": "ui"}
        inner = call.capabilities.invoke("vault.open", {}, user_id=call.user_id, workspace_id=call.workspace_id, **claim)
        return {"seen": inner["output"]}

    reg.register(Operation(id="vault.open", app="vault", title=text, summary="Fixture person-only read",
                           input=Nothing, handler=open_vault))
    reg.register(Operation(id="alpha.pull", app="alpha", title=text, summary="Fixture read that opens the vault",
                           input=How, handler=pull))
    who = {"user_id": p.uid("alice"), "workspace_id": p.a, "via": "api", "scopes": frozenset({"alpha.read"})}

    for as_credential in (False, True):                    # however the handler makes the inner call
        with pytest.raises(ServiceError) as e:
            api.caps.invoke("alpha.pull", {"as_credential": as_credential}, **who)
        assert (e.value.status, e.value.code) == (403, "insufficient_scope") and not e.value.detail
        assert "vault" not in e.value.message and "本人" not in e.value.message
    assert ran == [], "the inner operation did not run"
    # asked for directly it is named: that is the caller's own request, not something it reached
    with pytest.raises(ServiceError) as e:
        api.caps.invoke("vault.open", {}, **who)
    assert e.value.code == "person_required" and e.value.detail == {"operation": "vault.open"}
    # and the person in the page is not a credential
    page = api.caps.invoke("alpha.pull", {}, user_id=p.uid("alice"), workspace_id=p.a)
    assert page["output"] == {"seen": {"secret": "vault business"}} and ran == ["ui"]


def test_no_kind_of_credential_reaches_spending_or_consent(api):
    """The operations that spend a person's funds or record their consent to spend, asked for by
    each kind of credential the platform issues. All refused; no turn, quote, consent, budget,
    ledger or run row appears."""
    from synwevo_core.oidc import Clients, Grants
    p = api.people
    spending = sorted(op.id for op in api.caps.registry.operations() if op.id.startswith(
        ("agent.consent.", "agent.turn.", "agent.quote.", "agent.billing.")))
    for must in ("agent.consent.create", "agent.consent.get", "agent.consent.list", "agent.consent.revoke",
                 "agent.turn.start", "agent.turn.quote", "agent.quote.get", "agent.billing.budget.set"):
        assert must in spending, must
    Clients(p.store).register(client_id="dir-svc", app_id="platform", name="Fixture directory", redirect_uris=[],
                              kind="service", workspace_id=p.a, confidential=True, api_scopes="workspace.read",
                              audiences=[SUITE])
    service = Grants(p.store).issue_for_service(client=Clients(p.store).get("dir-svc"), scope="workspace.read",
                                                audience=SUITE)["access_token"]
    credentials = {
        "delegated user token": (bearer(api, "alice", ALL_SCOPES), 403, "person_required"),
        "service credential": ({"Authorization": "Bearer " + service}, 403, "wrong_credential"),
        "access key": ({"Authorization": "Bearer dw_abc123_notasecret"}, 403, "wrong_credential"),
    }
    watched = ("agent_", "billing_", "op_runs", "app_events")
    before = counts(p.store, *watched)
    assert {"agent_turns", "agent_model_consents"} <= set(before) and any(t.startswith("billing_") for t in before)
    for kind, (auth, status, code) in credentials.items():
        for op_id in spending:
            body = {"input": {"conversation_id": "c_fixture", "text": "spend", "consent_id": "mc_fixture"}}
            r = api.post(f"/api/suite/v1/ops/{op_id}", json=body, headers={**auth, "Idempotency-Key": "k"})
            assert (r.status_code, r.json()["error"]["code"]) == (status, code), (kind, op_id, r.text)
    assert counts(p.store, *watched) == before


def test_a_handler_cannot_widen_its_caller_inside_its_own_application_either(api):
    """`alpha.peek` is a read whose handler runs a write of the same application and hard-codes
    ``via="ui"`` for it — the shape of a future mistake. Under a credential the inner operation
    still needs its own scope. The allowance the other way is deliberate: a write's handler reading
    its own application's records is covered by the write scope."""
    from pydantic import BaseModel
    from synwevo_core.apps import Apps
    from synwevo_core.capabilities import AppManifest, Operation
    p, reg = api.people, api.caps.registry
    text = {"zh-Hans": "夹具", "zh-Hant": "夾具", "en": "Fixture"}
    Apps(p.store, p.ident).register(app_id="alpha", name="Fixture alpha", icon="doc",
                                    launch_url="http://workplace.testserver/alpha")
    Apps(p.store, p.ident).enable_for_workspace(p.a, "alpha")
    reg.register_app(AppManifest(id="alpha", name="Fixture alpha", path="/alpha", icon="doc", summary=text, machine=True))
    written = []

    class Nothing(BaseModel):
        pass

    def change(call, _):
        written.append(call.via)
        seen = call.capabilities.inspect("alpha.look", {}, user_id=call.user_id, workspace_id=call.workspace_id)
        return {"changed": True, "saw": seen}

    def peek(call, _):
        inner = call.capabilities.invoke("alpha.change", {}, user_id=call.user_id, workspace_id=call.workspace_id,
                                         via="ui")
        return inner["output"]

    reg.register(Operation(id="alpha.look", app="alpha", title=text, summary="Fixture read", input=Nothing,
                           handler=lambda call, _: {"rows": 1}))
    reg.register(Operation(id="alpha.change", app="alpha", title=text, summary="Fixture write", input=Nothing,
                           handler=change, need="write", side_effect="write", agent="confirm"))
    reg.register(Operation(id="alpha.peek", app="alpha", title=text, summary="Fixture read that writes inside",
                           input=Nothing, handler=peek))
    who = {"user_id": p.uid("alice"), "workspace_id": p.a, "via": "api"}

    with pytest.raises(ServiceError) as e:
        api.caps.invoke("alpha.peek", {}, scopes=frozenset({"alpha.read"}), **who)
    assert e.value.code == "insufficient_scope" and not e.value.detail and "alpha" not in e.value.message
    assert written == [], "the inner write did not run"
    both = api.caps.invoke("alpha.peek", {}, scopes=frozenset({"alpha.read", "alpha.write"}), **who)
    assert both["output"] == {"changed": True, "saw": {"rows": 1}} and written == ["ui"]
    # a write-only credential: its own handler's read of its own application is part of the write
    only_write = api.caps.invoke("alpha.change", {}, scopes=frozenset({"alpha.write"}), **who)
    assert only_write["output"] == {"changed": True, "saw": {"rows": 1}}
    # ... but that allowance is not a read scope: asked for directly, the read is refused
    with pytest.raises(ServiceError) as e:
        api.caps.invoke("alpha.look", {}, scopes=frozenset({"alpha.write"}), **who)
    assert e.value.code == "insufficient_scope" and e.value.detail["required_scope"] == "alpha.read"


def test_a_person_cannot_write_into_a_clients_idempotency_namespace(api):
    """The stored form of a client's key is `api:<client digest>:<key>`. From the page (or any
    caller that is not a credential) that prefix is refused; and a row that carries such a key
    without having been made by a credential is never replayed to the client as its own."""
    import hashlib
    p = api.people
    auth = bearer(api, "alice", "tables.read tables.write")
    table = m(api, auth, "tables.table.create", {"name": "Fixture contacts"}, key="t-1")["output"]["id"]
    stored = "api:" + hashlib.sha256(b"crm-sync").hexdigest()[:16] + ":order-7"
    assert p.store.one("SELECT idempotency_key FROM op_runs WHERE op='tables.table.create'")["idempotency_key"] \
        == stored.replace("order-7", "t-1")

    # the page, pre-seeding the key the client will use
    api.sign_in("alice")
    seeded = api.post("/api/suite/ops/tables.record.create", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                      json={"workspace_id": p.a, "input": {"table_id": table, "cells": {}}, "idempotency_key": stored})
    api.cookies.clear()
    assert seeded.status_code == 400 and seeded.json()["error"] == "bad_idempotency_key", seeded.text
    for via in ("ui", "flow", "agent", "automation", "system"):
        with pytest.raises(ServiceError) as e:
            api.caps.invoke("tables.record.create", {"table_id": table, "cells": {}}, user_id=p.uid("alice"),
                            workspace_id=p.a, via=via, idempotency_key=stored)
        assert e.value.code in ("bad_idempotency_key", "not_for_agent"), via
    assert p.store.one("SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?", (table,))["n"] == 0

    # a row with that key that no credential made (as one written before the prefix was reserved
    # would be): the page's own run, re-keyed in place
    page = api.caps.invoke("tables.record.create", {"table_id": table, "cells": {}}, user_id=p.uid("alice"),
                           workspace_id=p.a, idempotency_key="page-key")
    assert p.store.execute("UPDATE op_runs SET idempotency_key=? WHERE id=?", (stored, page["run_id"])) == 1
    clash = m(api, auth, "tables.record.create", {"table_id": table, "cells": {}}, key="order-7", expect=409)
    assert clash["error"]["code"] == "idempotency_conflict" and "details" not in clash["error"]
    assert page["run_id"] not in str(clash)
    assert p.store.one("SELECT COUNT(*) AS n FROM tb_records WHERE table_id=?", (table,))["n"] == 1
    # the client's own keys still work, and still replay to it
    first = m(api, auth, "tables.record.create", {"table_id": table, "cells": {}}, key="order-8")
    again = m(api, auth, "tables.record.create", {"table_id": table, "cells": {}}, key="order-8")
    assert (first["replayed"], again["replayed"], again["run_id"]) == (False, True, first["run_id"])
