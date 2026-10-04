"""The cross-workspace transfer contract (synwevo_core.data_flow) on a real Store and identity.

Fixture people and workspaces only (conftest.People: Alice owns A and is a viewer in B, Oscar owns B).
"""
from __future__ import annotations

import pytest

from synwevo_core import data_flow
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from .test_agent import op


def refused(p, source, target, who="alice"):
    with pytest.raises(ServiceError) as err:
        data_flow.check(p.store, user_id=p.uid(who), source=source, target=target)
    assert err.value.status == 403
    return err.value


def revision(store, workspace):
    """The data-flow policy revision a person changing it is looking at."""
    return data_flow.workspace_outbound(store, workspace)["revision"]


def organization(p, owner, name, key):
    orgs = Organizations(p.store, p.ident)
    created = orgs.create(p.uid(owner), name, f"{name} main", key)
    return orgs, created["organization"]["id"], created["workspace"]["id"]


def test_defaults_follow_who_can_already_see_the_content(suite):
    p = suite.people
    personal = p.ident.create_workspace("Fixture personal", owner_id=p.uid("alice"), kind="personal")["id"]
    assert data_flow.workspace_outbound(p.store, personal)["outbound"] == "any"
    assert data_flow.workspace_outbound(p.store, p.a)["outbound"] == "audience"
    _, oid, inside = organization(p, "alice", "Fixture Org", "fixture-data-flow-org-1")
    assert data_flow.workspace_outbound(p.store, inside)["outbound"] == "organization"
    assert data_flow.organization_outbound(p.store, oid) == {"outbound": "organization", "explicit": False,
                                                              "revision": 0, "updated_at": None}


def test_reading_is_not_exporting(suite):
    p = suite.people
    # Alice only views B; A's people are not all B's people either, so open B's policy first.
    data_flow.set_workspace_outbound(p.store, p.b, "any", actor_id=p.uid("oscar"), revision=revision(p.store, p.b))
    assert refused(p, p.b, p.a).code == "transfer_export_denied"
    p.ident.change_role(p.b, p.uid("alice"), "viewer", can_export=True)
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=p.b, target=p.a)["outbound"] == "any"
    assert refused(p, p.b, p.a, who="out").code == "transfer_source_unavailable"  # not a member at all


def test_audience_means_nobody_gains_a_view_they_did_not_have(suite):
    p = suite.people
    small = p.ident.create_workspace("Fixture smaller team", owner_id=p.uid("alice"))["id"]
    p.ident.add_member(small, p.uid("bob"), "editor")
    # Everyone in the smaller team is already in A: allowed. The other way, Vera would gain a view.
    basis = data_flow.check(p.store, user_id=p.uid("alice"), source=p.a, target=small)
    assert basis["outbound"] == "audience" and basis["audience"] == 2
    err = refused(p, small, p.a)
    assert err.code == "transfer_audience_denied" and err.detail == {"outside": 1}


def test_none_and_organization_policies(suite):
    p = suite.people
    small = p.ident.create_workspace("Fixture smaller team", owner_id=p.uid("alice"))["id"]
    data_flow.set_workspace_outbound(p.store, p.a, "none", actor_id=p.uid("alice"), revision=revision(p.store, p.a))
    assert refused(p, p.a, small).code == "transfer_source_closed"
    data_flow.set_workspace_outbound(p.store, p.a, "organization", actor_id=p.uid("alice"), revision=revision(p.store, p.a))
    assert refused(p, p.a, small).code == "transfer_organization_denied"  # neither is in an organization
    assert data_flow.workspace_outbound(p.store, p.a)["revision"] == 2


def test_the_organization_cap_keeps_content_inside_until_its_owner_opens_it(suite):
    p = suite.people
    orgs, oid, main = organization(p, "alice", "Fixture Org", "fixture-data-flow-org-2")
    second = orgs.create_workspace(oid, p.uid("alice"), "Fixture second space")["id"]
    personal = p.ident.create_workspace("Fixture personal", owner_id=p.uid("alice"), kind="personal")["id"]
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=main, target=second)["same_organization"]
    data_flow.set_workspace_outbound(p.store, main, "any", actor_id=p.uid("alice"), revision=revision(p.store, main))
    assert refused(p, main, personal).code == "transfer_organization_policy_denied"  # the cap narrows "any"
    data_flow.set_organization_outbound(p.store, oid, "any", actor_id=p.uid("alice"))
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=main, target=personal)["organization_outbound"] == "any"
    # Content of the personal workspace may go into the organization: its only reader is its owner.
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=personal, target=main)["outbound"] == "any"


def test_invalid_values_are_refused(suite):
    p = suite.people
    with pytest.raises(ServiceError) as err:
        data_flow.set_workspace_outbound(p.store, p.a, "everyone", actor_id=p.uid("alice"), revision=revision(p.store, p.a))
    assert err.value.code == "invalid_field"
    with pytest.raises(ServiceError) as err:
        data_flow.set_organization_outbound(p.store, "org_fixture", "none", actor_id=p.uid("alice"))
    assert err.value.code == "invalid_field"


def test_only_owners_and_admins_set_a_workspace_policy_and_the_change_is_recorded(suite):
    p = suite.people
    for who, status in (("bob", 403), ("vera", 403)):
        suite.sign_in(who)
        assert op(suite, "agent.transfer_policy.set", {"outbound": "any", "revision": revision(p.store, p.a)}, ws=p.a, expect=status)["error"] == "manage_required"
    suite.sign_in("alice")
    out = op(suite, "agent.transfer_policy.set", {"outbound": "organization", "revision": revision(p.store, p.a)}, ws=p.a)["output"]
    assert out["outbound"] == "organization" and out["revision"] == 1
    again = op(suite, "agent.transfer_policy.set", {"outbound": "organization", "revision": revision(p.store, p.a)}, ws=p.a)["output"]
    assert again["revision"] == 1  # the same choice is not a new revision
    events = p.store.all("SELECT type, data FROM app_events WHERE type='agent.transfer_policy.changed'")
    assert len(events) == 1 and events[0]["data"]["outbound"] == "organization"
    listed = {w["workspace_id"]: w["transfer"] for w in op(suite, "agent.workspace_policy.list")["output"]["workspaces"]}
    assert listed[p.a]["outbound"] == "organization" and listed[p.a]["explicit"]
    assert listed[p.b] == {"outbound": "audience", "explicit": False, "revision": 0, "in_organization": False,
                           "organization_outbound": "any"}


def test_forms_duplicate_through_the_api_follows_the_same_contract(suite):
    p = suite.people
    suite.sign_in("oscar")
    fid = op(suite, "forms.form.create", {"title": "Fixture template"}, ws=p.b)["output"]["id"]
    suite.sign_in("alice")
    copy = {"form_id": fid, "target_workspace_id": p.a}
    assert op(suite, "forms.form.duplicate", copy, ws=p.b, expect=403)["error"] == "transfer_export_denied"
    p.ident.change_role(p.b, p.uid("alice"), "viewer", can_export=True)
    assert op(suite, "forms.form.duplicate", copy, ws=p.b, expect=403)["error"] == "transfer_audience_denied"
    assert not p.store.one("SELECT 1 FROM forms WHERE workspace_id=?", (p.a,))
    suite.sign_in("oscar")
    op(suite, "agent.transfer_policy.set", {"outbound": "any", "revision": revision(p.store, p.b)}, ws=p.b)
    suite.sign_in("alice")
    made = op(suite, "forms.form.duplicate", copy, ws=p.b)["output"]
    assert p.store.one("SELECT workspace_id FROM forms WHERE id=?", (made["form_id"],))["workspace_id"] == p.a


def test_the_target_must_be_a_workspace_the_person_belongs_to(suite):
    """A refusal says nothing about a workspace the person is not in: same answer as for none at all."""
    p = suite.people
    elsewhere = p.ident.create_workspace("Fixture not Alice's", owner_id=p.uid("out"))["id"]
    p.ident.add_member(elsewhere, p.uid("oscar"), "editor")
    answers = [refused(p, p.a, target) for target in (None, "ws_fixture_missing", elsewhere)]
    assert [(e.code, e.detail or None) for e in answers] == [("transfer_target_unavailable", None)] * 3


def test_a_personal_workspace_that_has_been_shared_no_longer_defaults_to_any(suite):
    p = suite.people
    personal = p.ident.create_workspace("Fixture personal", owner_id=p.uid("alice"), kind="personal")["id"]
    wide = p.ident.create_workspace("Fixture Bob's team", owner_id=p.uid("bob"))["id"]
    p.ident.add_member(wide, p.uid("out"), "viewer")
    assert data_flow.workspace_outbound(p.store, personal)["outbound"] == "any"
    p.ident.add_member(personal, p.uid("bob"), "editor")
    assert data_flow.workspace_outbound(p.store, personal)["outbound"] == "audience"
    # Bob may edit Alice's personal workspace; his own team has a member who never saw it.
    assert refused(p, personal, wide, who="bob").code == "transfer_audience_denied"


def test_a_value_this_code_does_not_know_is_the_strictest_one(suite, monkeypatch):
    p = suite.people
    orgs, oid, inside = organization(p, "alice", "Fixture Org", "fixture-data-flow-org-3")
    sibling = orgs.create_workspace(oid, p.uid("alice"), "Fixture sibling")["id"]
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=inside, target=sibling)["outbound"] == "organization"
    later = {"explicit": True, "revision": 7, "updated_at": 1.0}
    monkeypatch.setattr(data_flow, "workspace_outbound", lambda store, wid: {"outbound": "partners", **later})
    assert refused(p, inside, sibling).code == "transfer_source_closed"
    monkeypatch.undo()
    data_flow.set_workspace_outbound(p.store, inside, "any", actor_id=p.uid("alice"), revision=revision(p.store, inside))
    personal = p.ident.create_workspace("Fixture personal", owner_id=p.uid("alice"), kind="personal")["id"]
    monkeypatch.setattr(data_flow, "organization_outbound", lambda store, oid: {"outbound": "partners", **later})
    assert refused(p, inside, personal).code == "transfer_organization_policy_denied"


def test_audience_refuses_a_send_whose_recipients_are_not_the_targets_members(suite):
    """A message or a form request goes to whoever it is addressed to; ``audience`` cannot vouch for them."""
    p = suite.people
    small = p.ident.create_workspace("Fixture smaller team", owner_id=p.uid("alice"))["id"]
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=p.a, target=small)["outbound"] == "audience"
    with pytest.raises(ServiceError) as err:
        data_flow.check(p.store, user_id=p.uid("alice"), source=p.a, target=small, external=True)
    assert (err.value.status, err.value.code) == (403, "transfer_audience_external")
    # The owner can decide otherwise: a wider policy covers recipients the membership comparison cannot.
    data_flow.set_workspace_outbound(p.store, p.a, "any", actor_id=p.uid("alice"), revision=revision(p.store, p.a))
    assert data_flow.check(p.store, user_id=p.uid("alice"), source=p.a, target=small, external=True)["outbound"] == "any"


def test_a_policy_is_changed_only_against_the_revision_the_person_saw(suite):
    p = suite.people
    suite.sign_in("alice")
    stale = revision(p.store, p.a)
    assert stale == 0
    tightened = op(suite, "agent.transfer_policy.set", {"outbound": "none", "revision": stale}, ws=p.a)["output"]
    assert (tightened["outbound"], tightened["revision"]) == ("none", 1)
    # A second admin's page still shows the default; its change must not undo the tightening.
    assert op(suite, "agent.transfer_policy.set", {"outbound": "any", "revision": stale}, ws=p.a,
              expect=409)["error"] == "transfer_policy_changed"
    # Even a choice that matches the current value is refused: the person decided on a page that was no longer true.
    assert op(suite, "agent.transfer_policy.set", {"outbound": "none", "revision": stale}, ws=p.a,
              expect=409)["error"] == "transfer_policy_changed"
    assert op(suite, "agent.transfer_policy.set", {"outbound": "any"}, ws=p.a, expect=422)["error"] == "invalid_input"
    assert data_flow.workspace_outbound(p.store, p.a) | {"updated_at": None} == {
        "outbound": "none", "explicit": True, "revision": 1, "updated_at": None}
    with pytest.raises(ServiceError) as err:  # the write itself is conditional, not only the handler's read
        data_flow.set_workspace_outbound(p.store, p.a, "any", actor_id=p.uid("alice"), revision=stale)
    assert (err.value.status, err.value.code) == (409, "transfer_policy_changed")
    widened = op(suite, "agent.transfer_policy.set", {"outbound": "audience", "revision": 1}, ws=p.a)["output"]
    assert (widened["outbound"], widened["revision"]) == ("audience", 2)
    with pytest.raises(ServiceError) as err:  # an earlier chosen revision is as stale as the default
        data_flow.set_workspace_outbound(p.store, p.a, "any", actor_id=p.uid("alice"), revision=1)
    assert err.value.code == "transfer_policy_changed" and revision(p.store, p.a) == 2
    events = [e["data"] for e in p.store.all("SELECT data FROM app_events WHERE type='agent.transfer_policy.changed' ORDER BY seq")]
    assert [(e["before"], e["outbound"], e["revision"]) for e in events] == [("audience", "none", 1), ("none", "audience", 2)]


def test_personal_operations_are_not_flow_steps(suite):
    """They have no workspace, so no transfer question could be asked of what they take or give."""
    p = suite.people
    suite.sign_in("alice")
    conversation = op(suite, "agent.conversation.create")["output"]["id"]
    flows = suite.api.state.flows
    read = {"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": p.a, "input": {}}
    for step in ({"operation": "agent.memory.add", "input": {"body": {"$ref": "steps.read.forms.0.title"}}},
                 {"operation": "agent.turn.start", "input": {"conversation_id": conversation, "text": {"$ref": "steps.read.forms.0.title"}}},
                 {"operation": "agent.conversation.get", "input": {"conversation_id": conversation}}):
        with pytest.raises(ServiceError) as err:
            flows.create(user_id=p.uid("alice"), name="Fixture flow", definition={"steps": [read, {"id": "personal", "kind": "call", **step}]})
        assert (err.value.status, err.value.code) == (403, "person_required"), step["operation"]
    assert p.store.one("SELECT COUNT(*) AS n FROM flow_definitions")["n"] == 0


def test_a_hop_through_a_third_workspace_in_one_run_does_not_launder_a_source(suite):
    p = suite.people
    alice = p.uid("alice")
    suite.sign_in("alice")
    op(suite, "forms.form.create", {"title": "Fixture plan of A"}, ws=p.a)
    personal = p.ident.create_workspace("Fixture personal", owner_id=alice, kind="personal")["id"]
    wide = p.ident.create_workspace("Fixture wide team", owner_id=alice)["id"]
    p.ident.add_member(wide, p.uid("out"), "viewer")  # not a member of A
    assert refused(p, p.a, wide).code == "transfer_audience_denied"

    def create(step_id, workspace, title):
        return {"id": step_id, "kind": "call", "operation": "forms.form.create", "workspace_id": workspace, "input": {"title": title},
                "result_read": {"operation": "forms.form.get", "workspace_id": workspace, "input": {"form_id": {"$ref": "receipt.id"}}}}
    steps = [{"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": p.a, "input": {}},
             create("hop", personal, {"$ref": "steps.read.forms.0.title"}),
             create("land", wide, {"$ref": "steps.hop.title"})]
    flows = suite.api.state.flows
    flow_id = flows.create(user_id=alice, name="Fixture flow", definition={"steps": steps})["flow_id"]
    run_id = flows.start(flow_id, user_id=alice, version=1, parameters={}, idempotency_key="fixture-hop")["id"]
    flows.tick()
    run = p.store.one("SELECT status, error_code, step_id FROM flow_runs WHERE id=?", (run_id,))
    assert (run["status"], run["error_code"], run["step_id"]) == ("blocked", "transfer_audience_denied", "land")
    # A's content reached the personal workspace, as A's policy allows; it did not travel on.
    assert [r["title"] for r in p.store.all("SELECT title FROM forms WHERE workspace_id=?", (personal,))] == ["Fixture plan of A"]
    assert p.store.one("SELECT COUNT(*) AS n FROM forms WHERE workspace_id=?", (wide,))["n"] == 0


def test_a_flow_step_that_sends_to_other_people_is_held_to_the_same_rule(suite):
    p = suite.people
    alice = p.uid("alice")
    suite.sign_in("alice")
    op(suite, "forms.form.create", {"title": "Fixture plan of A"}, ws=p.a)
    small = p.ident.create_workspace("Fixture Alice only", owner_id=alice)["id"]
    form = op(suite, "forms.form.create", {"title": "Fixture request form"}, ws=small)["output"]["id"]
    read = {"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": p.a, "input": {}}
    title = {"$ref": "steps.read.forms.0.title"}
    send = {"id": "act", "kind": "call", "operation": "forms.request.create", "workspace_id": small,
            "input": {"form_id": form, "title": title, "recipient": {"email": "outside@example.test"}}}
    keep = {"id": "act", "kind": "call", "operation": "forms.form.create", "workspace_id": small, "input": {"title": title}}
    flows = suite.api.state.flows
    outcomes = {}
    for name, step in (("send", send), ("keep", keep)):
        flow_id = flows.create(user_id=alice, name="Fixture " + name, definition={"steps": [read, step]})["flow_id"]
        run_id = flows.start(flow_id, user_id=alice, version=1, parameters={}, idempotency_key="fixture-" + name)["id"]
        flows.tick()
        run = p.store.one("SELECT status, error_code FROM flow_runs WHERE id=?", (run_id,))
        outcomes[name] = (run["status"], run["error_code"])
    assert outcomes == {"send": ("blocked", "transfer_audience_external"), "keep": ("succeeded", None)}
    assert p.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 0


def test_organization_content_does_not_leave_the_organization_by_an_outward_send(suite):
    """CR-047 DFP-1: a sibling workspace's message or request goes to recipients who need not be
    anyone in the organization, so content kept inside it — by the defaults too — is refused there."""
    p = suite.people
    alice = p.uid("alice")
    orgs, oid, main = organization(p, "alice", "Fixture Org", "fixture-data-flow-org-ext")
    sibling = orgs.create_workspace(oid, alice, "Fixture sibling space")["id"]

    def external():
        with pytest.raises(ServiceError) as err:
            data_flow.check(p.store, user_id=alice, source=main, target=sibling, external=True)
        return err.value.status, err.value.code

    # Nobody has chosen anything: policy and cap are both the default "organization".
    assert data_flow.check(p.store, user_id=alice, source=main, target=sibling)["same_organization"]
    assert external() == (403, "transfer_organization_external")
    # The workspace opens up, but the organization's cap still keeps its content inside.
    data_flow.set_workspace_outbound(p.store, main, "any", actor_id=alice, revision=revision(p.store, main))
    assert external() == (403, "transfer_organization_external")
    # The organization opens up, but the workspace goes back to "organization".
    data_flow.set_organization_outbound(p.store, oid, "any", actor_id=alice)
    data_flow.set_workspace_outbound(p.store, main, "organization", actor_id=alice, revision=revision(p.store, main))
    assert external() == (403, "transfer_organization_external")
    # Both owners decided to let it out.
    data_flow.set_workspace_outbound(p.store, main, "any", actor_id=alice, revision=revision(p.store, main))
    assert data_flow.check(p.store, user_id=alice, source=main, target=sibling, external=True)["outbound"] == "any"
    # Sending from the source workspace itself is that workspace's own act, not a transfer.
    assert data_flow.check(p.store, user_id=alice, source=main, target=main, external=True)["same_workspace"]


def test_a_flow_cannot_send_organization_content_out_from_a_sibling_workspace(suite):
    p = suite.people
    alice = p.uid("alice")
    suite.sign_in("alice")
    orgs, oid, main = organization(p, "alice", "Fixture Org", "fixture-data-flow-org-flow")
    sibling = orgs.create_workspace(oid, alice, "Fixture sibling space")["id"]
    op(suite, "forms.form.create", {"title": "Fixture payroll of the organization"}, ws=main)
    form = op(suite, "forms.form.create", {"title": "Fixture request form"}, ws=sibling)["output"]["id"]
    read = {"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": main, "input": {}}
    title = {"$ref": "steps.read.forms.0.title"}
    send = {"id": "act", "kind": "call", "operation": "forms.request.create", "workspace_id": sibling,
            "input": {"form_id": form, "title": title, "recipient": {"email": "outside@example.test"}}}
    keep = {"id": "act", "kind": "call", "operation": "forms.form.create", "workspace_id": sibling, "input": {"title": title}}
    flows = suite.api.state.flows
    outcomes = {}
    for name, step in (("send", send), ("keep", keep)):
        flow_id = flows.create(user_id=alice, name="Fixture org " + name, definition={"steps": [read, step]})["flow_id"]
        run_id = flows.start(flow_id, user_id=alice, version=1, parameters={}, idempotency_key="fixture-org-" + name)["id"]
        flows.tick()
        run = p.store.one("SELECT status, error_code FROM flow_runs WHERE id=?", (run_id,))
        outcomes[name] = (run["status"], run["error_code"])
    # Inside the organization the content may move; out to an address it may not.
    assert outcomes == {"send": ("blocked", "transfer_organization_external"), "keep": ("succeeded", None)}
    assert p.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 0
