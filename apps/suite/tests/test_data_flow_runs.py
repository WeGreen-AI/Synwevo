"""What a flow run may carry from one workspace into another (docs/platform/data-flow.md §3, Flows).

Inside a run nobody traces which value came from where: a literal re-read of a workspace the run wrote
into, a branch or a wait that decided on a value, and a run started by an event of another all carry
content with no reference to follow. So every workspace the run has read counts for every later write
elsewhere, in the run and in the runs its events start.

Real Forms operations through the installed flow engine on the isolated fixture store. Fixture people
and workspaces only (conftest.People: Alice owns A, whose default policy is ``audience``, and only
views B). The refused writes below all landed while the engine followed references from step to
step; the legal cases at the end must keep working.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core import data_flow
from synwevo_core.capabilities import record_event
from synwevo_core.errors import ServiceError
from .test_agent import op
from .test_flows import fixture_operation, flow  # noqa: F401 — the isolated fixture application, for a two-phase operation
from .test_forms import DEFINITION

SECRET = "Fixture plan of A"
TITLE = {"$ref": "steps.read.forms.0.title"}
OUTSIDE = "outside@example.test"


def read(workspace, step_id="read", **input):
    return {"id": step_id, "kind": "call", "operation": "forms.form.list", "workspace_id": workspace, "input": input}


def create(step_id, workspace, title, **extra):
    return {"id": step_id, "kind": "call", "operation": "forms.form.create", "workspace_id": workspace,
            "input": {"title": title}, **extra}


def branch(step_id, left, right, then, otherwise, op_="eq"):
    return {"id": step_id, "kind": "branch", "condition": {"left": left, "op": op_, "right": right},
            "then": then, "otherwise": otherwise}


class World:
    """Alice's workspaces around A. ``personal`` and ``small`` hold only her, so A's policy lets its
    content go there; ``wide`` has Oz, who is not in A, so A -> wide is refused."""

    def __init__(self, suite):
        self.suite, self.p, self.store = suite, suite.people, suite.people.store
        self.alice, self.flows = self.p.uid("alice"), suite.api.state.flows
        suite.sign_in("alice")
        op(suite, "forms.form.create", {"title": SECRET}, ws=self.p.a)
        self.personal = self.p.ident.create_workspace("Fixture personal", owner_id=self.alice, kind="personal")["id"]
        self.small = self.p.ident.create_workspace("Fixture Alice only", owner_id=self.alice)["id"]
        self.wide = self.p.ident.create_workspace("Fixture wide team", owner_id=self.alice)["id"]
        self.p.ident.add_member(self.wide, self.p.uid("out"), "viewer")
        assert self.refused(self.p.a, self.wide) == "transfer_audience_denied"
        assert data_flow.check(self.store, user_id=self.alice, source=self.p.a, target=self.personal)["outbound"] == "audience"

    def refused(self, source, target, **kw):
        with pytest.raises(ServiceError) as err:
            data_flow.check(self.store, user_id=self.alice, source=source, target=target, **kw)
        return err.value.code

    def close(self, workspace):
        data_flow.set_workspace_outbound(self.store, workspace, "none", actor_id=self.alice,
                                         revision=data_flow.workspace_outbound(self.store, workspace)["revision"])
        assert self.refused(workspace, self.wide) == "transfer_source_closed"

    def flow(self, steps, name, **definition):
        return self.flows.create(user_id=self.alice, name="Fixture " + name, definition={"steps": steps, **definition})["flow_id"]

    def run(self, steps, key, *, given=None, **definition):
        """Start a flow of these steps and let the engine work; the run's (status, error, step)."""
        rid = self.flows.start(self.flow(steps, key, **definition), user_id=self.alice, version=1,
                               parameters=given or {}, idempotency_key=key)["id"]
        self.flows.tick()
        return self.outcome(rid)

    def outcome(self, run_id):
        row = self.store.one("SELECT status, error_code, step_id FROM flow_runs WHERE id=?", (run_id,))
        return row["status"], row["error_code"], row["step_id"]

    def on_event(self, steps, name, workspace, *, tools, workspaces, parameters=None, input=None):
        """A flow of Alice's that an event ``forms.form.published`` of ``workspace`` starts."""
        fid = self.flow(steps, name, parameters=parameters or {})
        self.flows.create_trigger(fid, user_id=self.alice, version=1, trigger={
            "kind": "event", "event_type": "forms.form.published", "workspace_id": workspace, "input": input or {}},
            policy={"tools": tools, "workspaces": workspaces, "max_runs": 5, "until_revoked": True})
        return fid

    def runs_of(self, flow_id):
        return self.store.all("SELECT id, status, error_code, step_id, correlation_id, trigger_id FROM flow_runs "
                              "WHERE flow_id=? ORDER BY created_at", (flow_id,))

    def titles(self, workspace):
        return sorted(r["title"] for r in self.store.all("SELECT title FROM forms WHERE workspace_id=?", (workspace,)))

    def delegation(self, operation, workspace):
        """The delegation on the run record a flow's step left in ``workspace``."""
        row = self.store.one("SELECT delegation FROM op_runs WHERE op=? AND workspace_id=? AND via='flow'", (operation, workspace))
        return row["delegation"] if isinstance(row["delegation"], dict) else json.loads(row["delegation"])


@pytest.fixture()
def world(suite):
    return World(suite)


# ---------------------------------------------------------------- one run

def test_a_literal_reread_of_the_hop_workspace_does_not_launder_a_source(world):
    w = world
    steps = [read(w.p.a), create("hop", w.personal, TITLE),
             # No reference to "hop": the list of the workspace that now holds what "hop" wrote.
             read(w.personal, "relist"),
             create("land", w.wide, {"$ref": "steps.relist.forms.0.title"})]
    assert w.run(steps, "reread") == ("blocked", "transfer_audience_denied", "land")
    # A's content reached the personal workspace, as A's policy allows; it did not travel on.
    assert w.titles(w.personal) == [SECRET] and w.titles(w.wide) == []


def test_a_reread_inside_the_target_does_not_turn_a_write_into_an_outward_send(world):
    w = world
    form = op(w.suite, "forms.form.create", {"title": "Fixture request form", "definition": DEFINITION}, ws=w.small)["output"]
    op(w.suite, "forms.form.publish", {"form_id": form["id"], "draft_rev": form["draft_rev"]}, ws=w.small)
    assert w.refused(w.p.a, w.small, external=True) == "transfer_audience_external"
    steps = [read(w.p.a), create("hop", w.small, TITLE),  # a plain write: audience allows it
             read(w.small, "relist", q=SECRET),
             {"id": "send", "kind": "call", "operation": "forms.request.create", "workspace_id": w.small,
              "input": {"form_id": form["id"], "title": {"$ref": "steps.relist.forms.0.title"}, "recipient": {"email": OUTSIDE}}}]
    assert w.run(steps, "outward") == ("blocked", "transfer_audience_external", "send")
    assert SECRET in w.titles(w.small)
    assert w.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 0


def test_a_branch_on_what_a_closed_workspace_holds_cannot_be_followed_by_a_write_elsewhere(world):
    w = world
    w.close(w.p.a)
    guess = branch("guess", TITLE, SECRET, "yes", "no")
    literal = [read(w.p.a), guess, create("yes", w.wide, "A's first form is the plan", next="end"),
               create("no", w.wide, "A's first form is something else")]
    assert w.run(literal, "branch") == ("blocked", "transfer_source_closed", "yes")
    # The branch's own value names the step it chose: a step id can spell out an answer.
    always = {"left": 1, "op": "eq", "right": 1}
    named = [read(w.p.a), branch("guess", TITLE, SECRET, "title-is-the-plan", "land"),
             {"id": "title-is-the-plan", "kind": "branch", "condition": always, "then": "land", "otherwise": "land"},
             create("land", w.wide, {"$ref": "steps.guess.target"})]
    assert w.run(named, "branch-target") == ("blocked", "transfer_source_closed", "land")
    assert w.titles(w.wide) == []


def test_a_branch_on_a_workspace_the_person_only_views_cannot_decide_a_write_elsewhere(world):
    """Reading is not exporting: Alice views B and has no export right there."""
    w = world
    w.suite.sign_in("oscar")
    op(w.suite, "forms.form.create", {"title": "Fixture decision of B"}, ws=w.p.b)
    assert w.refused(w.p.b, w.p.a) == "transfer_export_denied"
    steps = [read(w.p.b), branch("test", TITLE, ["Fixture decision of B", "Fixture other"], "yes", "no", op_="in"),
             create("yes", w.p.a, "B says: decided", next="end"), create("no", w.p.a, "B says: something else")]
    assert w.run(steps, "viewer") == ("blocked", "transfer_export_denied", "yes")
    assert w.titles(w.p.a) == [SECRET]


def test_a_wait_that_decides_on_closed_content_cannot_be_followed_by_a_write_elsewhere(world):
    w = world
    w.close(w.p.a)
    land = create("land", w.wide, "A's first form is the plan")
    # The wait reads the target with a literal input; its conditions compare what A holds.
    gate = {"id": "gate", "kind": "wait", "read": {"operation": "forms.form.list", "workspace_id": w.wide, "input": {}},
            "until": {"left": TITLE, "op": "eq", "right": SECRET}, "reject": {"left": TITLE, "op": "ne", "right": SECRET}}
    assert w.run([read(w.p.a), gate, land], "wait-compare") == ("blocked", "transfer_source_closed", "land")
    # The wait itself is the run's only look at A.
    look = {"id": "look", "kind": "wait", "read": {"operation": "forms.form.list", "workspace_id": w.p.a, "input": {}},
            "until": {"left": {"$ref": "record.forms.0.title"}, "op": "eq", "right": SECRET}}
    assert w.run([look, land], "wait-read") == ("blocked", "transfer_source_closed", "land")
    assert w.titles(w.wide) == []


def test_a_receipt_an_outcome_and_a_handled_failure_count_once_the_run_uses_them(world):
    """A write nobody refers to carries nothing. Its receipt, its outcome and its failure are answers
    of the workspace it ran in: once a step decides on one, that workspace is a source like a read."""
    w = world
    mine = op(w.suite, "forms.form.create", {"title": "Fixture note of A"}, ws=w.p.a)["output"]
    w.close(w.p.a)
    edit = {"id": "edit", "kind": "call", "operation": "forms.form.update_draft", "workspace_id": w.p.a,
            "input": {"form_id": mine["id"], "draft_rev": mine["draft_rev"], "description": "Fixture edit"}}
    yes, no = create("yes", w.wide, "A answered yes", next="end"), create("no", w.wide, "A answered no")
    receipt = [edit, branch("guess", {"$ref": "steps.edit.title"}, "Fixture note of A", "yes", "no"), yes, no]
    assert w.run(receipt, "receipt") == ("blocked", "transfer_source_closed", "yes")
    outcome = [create("make", w.p.a, "Fixture literal"), branch("how", {"$ref": "outcome.make"}, "succeeded", "yes", "no"), yes, no]
    assert w.run(outcome, "outcome") == ("blocked", "transfer_source_closed", "yes")
    probe = {"id": "probe", "kind": "call", "operation": "forms.form.get", "workspace_id": w.p.a,
             "input": {"form_id": "frm_000000000000"}, "on_error": "absent"}
    failure = [probe, create("present", w.wide, "A has that form", next="end"), create("absent", w.wide, "A has no such form")]
    assert w.run(failure, "failure") == ("blocked", "transfer_source_closed", "absent")
    assert w.titles(w.wide) == []
    # Two writes and nothing taken from the first: no question to ask, even of a closed workspace.
    assert w.run([create("make", w.p.a, "Fixture literal two"), create("also", w.wide, "Fixture literal")], "apart") == ("succeeded", None, "end")
    assert w.titles(w.wide) == ["Fixture literal"]


def test_a_result_read_in_another_workspace_is_asked_for_everything_the_run_holds(world):
    w = world
    hop = create("hop", w.personal, TITLE, result_read={"operation": "forms.form.list", "workspace_id": w.wide,
                                                         "input": {"q": {"$ref": "receipt.title"}}})
    # personal -> wide is allowed, and that was all the step used to be asked; A's title rode in the receipt.
    assert data_flow.check(w.store, user_id=w.alice, source=w.personal, target=w.wide)["outbound"] == "any"
    assert w.run([read(w.p.a), hop], "receipt-read") == ("blocked", "transfer_audience_denied", "hop")
    # Asked before the step runs: nothing was written and no read of the other workspace was stored.
    assert w.titles(w.personal) == []
    assert not w.store.one("SELECT 1 FROM flow_steps WHERE reader_json LIKE ?", (f"%{SECRET}%",))
    # The same shape with a read every source may reach still runs.
    back = create("hop", w.personal, TITLE, result_read={"operation": "forms.form.list", "workspace_id": w.p.a,
                                                          "input": {"q": {"$ref": "receipt.title"}}})
    assert w.run([read(w.p.a), back], "receipt-read-back") == ("succeeded", None, "end")


def test_a_read_is_asked_only_when_its_input_takes_a_value_from_the_run(world):
    w = world
    w.suite.sign_in("oscar")
    op(w.suite, "forms.form.create", {"title": "Fixture form of B"}, ws=w.p.b)
    # Neither workspace may give its content to the other; reading both to compare them is no transfer.
    assert (w.refused(w.p.a, w.p.b), w.refused(w.p.b, w.p.a)) == ("transfer_audience_denied", "transfer_export_denied")
    compare = [read(w.p.a), read(w.p.b, "other"),
               branch("same", TITLE, {"$ref": "steps.other.forms.0.title"}, "end", "end")]
    assert w.run(compare, "compare") == ("succeeded", None, "end")
    assert w.run([read(w.p.a), read(w.wide, "look", q="Fixture words")], "literal-read") == ("succeeded", None, "end")
    # A parameter the person gave at the start was fixed before the run read anything.
    given = [read(w.p.a), read(w.wide, "look", q={"$ref": "params.q"})]
    assert w.run(given, "given", given={"q": "Fixture words"}, parameters={"q": {"type": "string"}}) == ("succeeded", None, "end")
    # A's value as the question put to another workspace is a transfer: in a read step, in a wait's
    # read and in a step's result read alike.
    assert w.run([read(w.p.a), read(w.wide, "probe", q=TITLE)], "probe") == ("blocked", "transfer_audience_denied", "probe")
    poll = {"id": "poll", "kind": "wait", "read": {"operation": "forms.form.list", "workspace_id": w.wide, "input": {"q": TITLE}},
            "until": {"left": 1, "op": "eq", "right": 1}}
    assert w.run([read(w.p.a), poll], "poll") == ("blocked", "transfer_audience_denied", "poll")
    after = read(w.wide, "after") | {"result_read": {"operation": "forms.form.list", "workspace_id": w.wide, "input": {"q": TITLE}}}
    assert w.run([read(w.p.a), after], "result-read") == ("blocked", "transfer_audience_denied", "after")
    assert not w.store.one("SELECT 1 FROM flow_steps WHERE reader_json LIKE ?", (f"%{SECRET}%",))


def test_a_write_that_carried_sources_names_them_on_the_targets_run_record(world):
    w = world
    assert w.run([read(w.p.a), create("keep", w.small, TITLE)], "provenance") == ("succeeded", None, "end")
    kept = w.delegation("forms.form.create", w.small)
    assert kept["kind"] == "flow" and kept["transfer_from"] == [w.p.a]  # an ID, no content
    assert SECRET not in json.dumps(kept)
    assert w.run([create("alone", w.personal, "Fixture literal")], "nothing-carried") == ("succeeded", None, "end")
    assert "transfer_from" not in w.delegation("forms.form.create", w.personal)
    # Every workspace the run has read is named, not only the one the written value was taken from.
    both = [read(w.p.a), read(w.personal, "relist"), create("land", w.p.b, {"$ref": "steps.relist.forms.0.title"})]
    w.p.ident.change_role(w.p.b, w.alice, "editor")
    data_flow.set_workspace_outbound(w.store, w.p.a, "any", actor_id=w.alice, revision=0)
    assert w.run(both, "two-sources") == ("succeeded", None, "end")
    assert w.delegation("forms.form.create", w.p.b)["transfer_from"] == sorted([w.p.a, w.personal])


def test_a_two_phase_write_is_asked_again_immediately_before_it_is_dispatched(flow, monkeypatch):  # noqa: F811
    """A fixture operation that runs outside the engine's transaction (nothing is really sent): its
    dispatch marker is committed first, and the source's policy may change before the dispatch."""
    f = flow
    f.store.execute("UPDATE memberships SET role='editor' WHERE workspace_id=? AND user_id=?", (f.p.b, f.uid))
    sent = []
    fixture_operation(f, "outward", lambda c, i: sent.append(c.workspace_id) or {"ok": True}, transactional=False)
    steps = [f.write(), f.write("send", operation="fixtureflow.outward", workspace_id=f.p.b, result_read=None)]
    data_flow.set_workspace_outbound(f.store, f.ws, "any", actor_id=f.uid, revision=0)
    carried = f.start(f.create(steps))
    f.tick()
    assert f.row(carried)["status"] == "succeeded" and sent == [f.p.b]
    receipt = f.store.one("SELECT delegation FROM op_runs WHERE op='fixtureflow.outward'")
    assert json.loads(receipt["delegation"])["transfer_from"] == [f.ws]
    dispatch = f.e._external

    def tighten(*args):
        data_flow.set_workspace_outbound(f.store, f.ws, "none", actor_id=f.uid, revision=1)
        return dispatch(*args)
    monkeypatch.setattr(f.e, "_external", tighten)
    closed = f.start(f.create(steps), key="closed")
    f.tick()
    assert (f.row(closed)["status"], f.row(closed)["error_code"]) == ("blocked", "transfer_source_closed")
    assert sent == [f.p.b]  # the second run's call never left


# ---------------------------------------------------------------- runs started by events

def second_flow(w):
    """Whenever a form is published in the personal workspace, copy its title into ``wide``."""
    steps = [{"id": "get", "kind": "call", "operation": "forms.form.get", "workspace_id": w.personal,
              "input": {"form_id": {"$ref": "params.form_id"}}},
             create("land", w.wide, {"$ref": "steps.get.title"})]
    return w.on_event(steps, "second", w.personal, tools=["forms.form.get", "forms.form.create"],
                      workspaces=[w.personal, w.wide], parameters={"form_id": {"type": "string"}},
                      input={"form_id": {"$ref": "event.object_id"}})


def test_a_chain_of_event_started_runs_carries_what_the_first_run_read(world):
    w = world
    second = second_flow(w)
    first = [read(w.p.a),
             {"id": "hop", "kind": "call", "operation": "forms.form.create", "workspace_id": w.personal,
              "input": {"title": TITLE, "definition": DEFINITION}},
             {"id": "announce", "kind": "call", "operation": "forms.form.publish", "workspace_id": w.personal,
              "input": {"form_id": {"$ref": "steps.hop.id"}, "draft_rev": {"$ref": "steps.hop.draft_rev"}}}]
    first_id = w.flow(first, "first")
    rid = w.flows.start(first_id, user_id=w.alice, version=1, parameters={}, idempotency_key="chain")["id"]
    for _ in range(3):
        w.flows.tick()
    assert w.outcome(rid) == ("succeeded", None, "end")
    [started] = w.runs_of(second)
    # The engine started it from the first run's event: same chain, nobody in between.
    assert started["trigger_id"] and started["correlation_id"] == rid
    assert (started["status"], started["error_code"], started["step_id"]) == ("blocked", "transfer_audience_denied", "land")
    assert w.titles(w.wide) == []
    # A form the person publishes there by hand is a separate action: its chain has read only the
    # personal workspace, whose policy lets its content go anywhere (the documented limit, §5).
    note = op(w.suite, "forms.form.create", {"title": "Fixture personal note", "definition": DEFINITION}, ws=w.personal)["output"]
    op(w.suite, "forms.form.publish", {"form_id": note["id"], "draft_rev": note["draft_rev"]}, ws=w.personal)
    for _ in range(2):
        w.flows.tick()
    by_hand = w.runs_of(second)[1]
    assert by_hand["correlation_id"] != rid and by_hand["status"] == "succeeded"
    assert w.titles(w.wide) == ["Fixture personal note"]


def test_an_event_that_names_only_its_run_stays_in_the_chain(world):
    """An application's worker may settle a receipt later and record the event with the operation run
    alone (the fixture event below stands for that). The run it starts still belongs to the chain."""
    w = world
    second = second_flow(w)
    first = [read(w.p.a), {"id": "hop", "kind": "call", "operation": "forms.form.create", "workspace_id": w.personal,
                           "input": {"title": TITLE, "definition": DEFINITION}}]
    rid = w.flows.start(w.flow(first, "first"), user_id=w.alice, version=1, parameters={}, idempotency_key="late")["id"]
    w.flows.tick()
    assert w.outcome(rid) == ("succeeded", None, "end")
    made = w.store.one("SELECT id, correlation_id FROM op_runs WHERE op='forms.form.create' AND workspace_id=?", (w.personal,))
    form = w.store.one("SELECT id FROM forms WHERE workspace_id=?", (w.personal,))
    assert made["correlation_id"] == rid
    with w.store.tx() as t:
        record_event(t, type_="forms.form.published", app="forms", workspace_id=w.personal, object_kind="form",
                     object_id=form["id"], user_id=w.alice, run_id=made["id"])
    for _ in range(2):
        w.flows.tick()
    [started] = w.runs_of(second)
    assert started["correlation_id"] == rid
    assert (started["status"], started["error_code"], started["step_id"]) == ("blocked", "transfer_audience_denied", "land")
    assert w.titles(w.wide) == []


def test_an_event_of_a_closed_workspace_cannot_start_a_write_elsewhere(world):
    w = world
    w.close(w.p.a)
    scope = {"tools": ["forms.form.create", "forms.form.list"], "workspaces": [w.p.a, w.wide]}
    routed = {"parameters": {"oid": {"type": "string"}, "kind": {"type": "string"}},
              "input": {"oid": {"$ref": "event.object_id"}, "kind": {"$ref": "event.type"}}}
    named = w.on_event([{"id": "land", "kind": "call", "operation": "forms.form.create", "workspace_id": w.wide,
                         "input": {"title": {"$ref": "params.oid"}, "description": {"$ref": "params.kind"}}}],
                       "named", w.p.a, **scope, **routed)
    # That something was published in A, and when, is information about A too.
    plain = w.on_event([create("land", w.wide, "A published something")], "plain", w.p.a, **scope)
    asked = w.on_event([read(w.wide, "probe", q={"$ref": "params.oid"})], "asked", w.p.a, **scope, **routed)
    inside = w.on_event([create("land", w.p.a, {"$ref": "params.oid"})], "inside", w.p.a, **scope, **routed)
    w.suite.sign_in("bob")  # another member of A publishes something there
    made = op(w.suite, "forms.form.create", {"title": "Fixture form of Bob", "definition": DEFINITION}, ws=w.p.a)["output"]
    op(w.suite, "forms.form.publish", {"form_id": made["id"], "draft_rev": made["draft_rev"]}, ws=w.p.a)
    for _ in range(2):
        w.flows.tick()
    answers = {name: [(r["status"], r["error_code"], r["step_id"]) for r in w.runs_of(fid)]
               for name, fid in (("named", named), ("plain", plain), ("asked", asked), ("inside", inside))}
    assert answers == {"named": [("blocked", "transfer_source_closed", "land")],
                       "plain": [("blocked", "transfer_source_closed", "land")],
                       "asked": [("blocked", "transfer_source_closed", "probe")],
                       "inside": [("succeeded", None, "end")]}  # the event's own workspace: no transfer
    assert w.titles(w.wide) == [] and made["id"] in w.titles(w.p.a)


# ---------------------------------------------------------------- what must keep working

def test_chains_inside_one_workspace_are_not_asked_anything(world):
    w = world
    w.close(w.p.a)  # even a closed workspace: nothing leaves it
    kept = {"operation": "forms.form.get", "workspace_id": w.p.a, "input": {"form_id": {"$ref": "receipt.id"}}}
    steps = [read(w.p.a), create("one", w.p.a, TITLE, result_read=kept),
             branch("check", {"$ref": "steps.one.title"}, SECRET, "two", "end"),
             create("two", w.p.a, {"$ref": "steps.one.title"})]
    assert w.run(steps, "same") == ("succeeded", None, "end")
    assert w.titles(w.p.a) == [SECRET] * 3
    runs = w.store.all("SELECT delegation FROM op_runs WHERE op='forms.form.create' AND via='flow'")
    assert len(runs) == 2 and not any("transfer_from" in json.dumps(r["delegation"]) for r in runs)
    # A viewer without the export right reads their workspace twice: no transfer either.
    w.suite.sign_in("oscar")
    op(w.suite, "forms.form.create", {"title": "Fixture form of B"}, ws=w.p.b)
    twice = [read(w.p.b, "list"), {"id": "get", "kind": "call", "operation": "forms.form.get", "workspace_id": w.p.b,
                                   "input": {"form_id": {"$ref": "steps.list.forms.0.id"}}}]
    assert w.run(twice, "viewer") == ("succeeded", None, "end")


def test_content_travels_where_every_source_allows_it(world):
    w = world
    kept = lambda workspace: {"operation": "forms.form.get", "workspace_id": workspace, "input": {"form_id": {"$ref": "receipt.id"}}}  # noqa: E731
    # A's default policy (audience) lets it into a workspace whose members are all in A, hop or no hop.
    down = [read(w.p.a), create("hop", w.personal, TITLE, result_read=kept(w.personal)),
            read(w.personal, "relist"), create("land", w.small, {"$ref": "steps.relist.forms.0.title"})]
    assert w.run(down, "subset") == ("succeeded", None, "end")
    assert w.titles(w.small) == [SECRET]
    # Once its owner opens A, its content may go on to a workspace with people A does not have.
    wider = [read(w.p.a), create("hop", w.personal, TITLE), read(w.personal, "relist"),
             create("land", w.wide, {"$ref": "steps.relist.forms.0.title"})]
    data_flow.set_workspace_outbound(w.store, w.p.a, "any", actor_id=w.alice, revision=0)
    assert w.run(wider, "any") == ("succeeded", None, "end")
    assert w.titles(w.wide) == [SECRET]


# ---------------------------------------------------------------- second review round (R2)

def test_a_chain_is_read_once_for_a_page_of_its_runs(world, monkeypatch):
    """R2-F4: carried() rescanned the whole chain once per run, under the write lock."""
    w = world
    fid = w.flow([read(w.p.a)], "page")
    runs = [w.flows.start(fid, user_id=w.alice, version=1, parameters={}, idempotency_key=f"page-{n}",
                          correlation_id="fixture-one-chain")["id"] for n in range(5)]
    w.flows.tick()
    calls = []
    real = w.flows._sources
    monkeypatch.setattr(w.flows, "_sources", lambda run: calls.append(run["id"]) or real(run))
    assert w.flows.carried(runs, user_id=w.alice) == [w.p.a]
    assert len(calls) == 1


def test_a_person_starting_a_run_does_not_choose_its_chain(world):
    """R2-F4: a correlation named by the caller is tracing; a run the person starts begins its own chain."""
    w = world
    fid = w.flow([read(w.p.a)], "own-chain")
    from .test_forms import CSRF
    r = w.suite.post("/api/suite/ops/agent.flow.start", headers=CSRF,
                     json={"input": {"flow_id": fid, "version": 1, "idempotency_key": "own-chain"},
                           "correlation_id": "fixture-chosen-chain"})
    assert r.status_code == 200, r.text
    run = w.store.one("SELECT id, correlation_id FROM flow_runs WHERE flow_id=?", (fid,))
    assert run["correlation_id"] == run["id"]


def test_a_run_that_stopped_at_a_step_counts_that_steps_workspace(world):
    """R2-F7: the error code of the step a run stopped at was decided there."""
    w = world
    failing = {"id": "get", "kind": "call", "operation": "forms.form.get", "workspace_id": w.p.b,
               "input": {"form_id": "frm_000000000000"}}
    fid = w.flow([failing], "stopped")
    rid = w.flows.start(fid, user_id=w.alice, version=1, parameters={}, idempotency_key="stopped")["id"]
    w.flows.tick()
    status, code, step = w.outcome(rid)
    assert status in ("failed", "blocked") and step == "get", (status, code)
    assert w.flows.carried([rid], user_id=w.alice) == [w.p.b]


# ---------------------------------------------------------------- third review round (R3)

def test_a_result_read_that_failed_counts_where_it_read(world):
    """R3-DF-4: A takes no transfers. A literal write in the personal workspace whose result read
    asks A for a guessed form failed there, and the run went on through ``on_error``: whether A
    holds that form decided the next write in ``wide``, and A was never asked. The same probe as a
    plain read step in A is refused."""
    w = world
    w.close(w.p.a)
    guess = {"form_id": "frm_000000000000"}
    probe = create("probe", w.personal, "Fixture literal", on_error="absent",
                   result_read={"operation": "forms.form.get", "workspace_id": w.p.a, "input": guess})
    after = [create("present", w.wide, "A has that form", next="end"), create("absent", w.wide, "A has no such form")]
    assert w.run([probe, *after], "result-probe") == ("blocked", "transfer_source_closed", "absent")
    plain = {"id": "probe", "kind": "call", "operation": "forms.form.get", "workspace_id": w.p.a, "input": guess,
             "on_error": "absent"}
    assert w.run([plain, *after], "plain-probe") == ("blocked", "transfer_source_closed", "absent")
    assert w.titles(w.wide) == []
    # A write that failed before its result read was reached told the run nothing about A.
    early = create("early", w.personal, "", on_error="land",
                   result_read={"operation": "forms.form.get", "workspace_id": w.p.a, "input": guess})
    assert w.run([early, create("land", w.wide, "Fixture literal")], "early-failure") == ("partial", None, "end")


def test_a_correlation_another_member_names_does_not_gather_the_owners_triggered_runs(flow):
    """R3-FE-07: Bob, an editor of A, names one correlation on each of his writes there. Alice's
    two event triggers on A start runs for every write; none of them joins Bob's chain, and the
    runs of different writes do not share one: an event another person's operation caused begins
    a chain of its own, shared only by the runs that same event starts side by side."""
    f = flow
    spec = {"kind": "event", "event_type": "fixtureflow.created", "workspace_id": f.ws, "object_kind": "fixture_approval"}
    for name in ("one", "two"):
        fid = f.create([f.write(name, result_read=None, input={"text": "triggered"})])
        f.e.create_trigger(fid, user_id=f.uid, version=1, trigger=spec, policy=f.policy(runs=10))
    for n in range(3):
        f.caps.invoke("fixtureflow.create", {"text": f"Fixture write {n} of Bob"}, user_id=f.p.uid("bob"),
                      workspace_id=f.ws, via="ui", correlation_id="fixture-bobs-chain")
    f.e.trigger_tick()
    chains = {}
    for r in f.store.all("SELECT correlation_id, event_json FROM flow_runs WHERE user_id=?", (f.uid,)):
        chains.setdefault(r["correlation_id"], []).append(json.loads(r["event_json"])["id"])
    assert "fixture-bobs-chain" not in chains
    assert len(chains) == 3 and all(len(ids) == 2 and len(set(ids)) == 1 for ids in chains.values())


def test_an_application_event_keeps_the_owners_chain_only(flow):
    """An application that records an event itself (a submission to a request the run made, a
    decision on its approval) names the correlation of the record, with no operation run. A
    correlation the owner has runs in is their chain and the run joins it; one they have none in
    is somebody else's naming, and the run begins its own chain."""
    f = flow
    origin = f.start(f.create([f.write()]), key="origin")
    f.tick()
    assert f.row(origin)["status"] == "succeeded"
    fid = f.create([f.write("second", result_read=None)])
    f.e.create_trigger(fid, user_id=f.uid, version=1, policy=f.policy(runs=5),
                       trigger={"kind": "event", "event_type": "fixtureflow.decided", "workspace_id": f.ws})
    record = f.result(origin, "write")["id"]
    with f.store.tx() as t:
        for chain in (origin, "fixture-named-elsewhere"):
            record_event(t, type_="fixtureflow.decided", app="fixtureflow", workspace_id=f.ws,
                         object_kind="fixture_approval", object_id=record, correlation_id=chain)
    f.e.trigger_tick()
    started = sorted(r["correlation_id"] for r in f.store.all("SELECT correlation_id FROM flow_runs WHERE flow_id=?", (fid,)))
    assert len(started) == 2 and origin in started and "fixture-named-elsewhere" not in started
