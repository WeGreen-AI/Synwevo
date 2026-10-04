"""What link holders of a form see, and when a write carries content in front of them
(docs/platform/data-flow.md §3, rule 8; CR-047 F2 / AGENT-DF-1).

A form's fill page is read by people outside its workspace: anyone holding the public link, and the
recipient of each personal request. They see a published version, so saving the editors' draft
must not change that page; and publishing, when the public link follows the latest version, puts
the new version in front of all of them — an outward send for any content the write carried from
another workspace.

Real Forms operations through the installed flow engine and capability layer on the isolated
fixture store; fixture people and workspaces only (``World``: Alice owns A, default policy
``audience``; ``small`` holds only her, so A's content may go there, but not outward).
"""
from __future__ import annotations

import pytest

from synwevo_core import data_flow
from synwevo_core.errors import ServiceError
from .test_data_flow_runs import SECRET, TITLE, World, branch, read
from .test_forms import CSRF, DEFINITION, good_answers, token_of
from .test_forms import op as ws_op

ORIGINAL = "Fixture travel claim"
NOTE = "Fixture: what the page said when it was published"


@pytest.fixture()
def world(suite):
    return World(suite)


def exposed_form(w: World, *, pinned: bool = False) -> dict:
    """A published form in ``small`` with a public link (following the latest version unless
    pinned) and a personal request link, as Alice leaves it before any flow runs."""
    s = w.suite
    form = ws_op(s, "forms.form.create", w.small, {"title": ORIGINAL, "description": NOTE, "definition": DEFINITION})["output"]
    ws_op(s, "forms.form.publish", w.small, {"form_id": form["id"], "draft_rev": form["draft_rev"]})
    public = ws_op(s, "forms.public_link.set", w.small,
                   {"form_id": form["id"], "enabled": True, **({"version": 1} if pinned else {})})["output"]
    personal = ws_op(s, "forms.request.create", w.small, {"form_id": form["id"]})["output"]
    rev = w.store.one("SELECT draft_rev FROM forms WHERE id=?", (form["id"],))["draft_rev"]
    return {"id": form["id"], "rev": rev, "public": token_of(public["url"]), "personal": token_of(personal["url"])}


def page(w: World, token: str) -> dict:
    """The fill page as a link holder with no session loads it."""
    jar = dict(w.suite.cookies)
    w.suite.cookies.clear()
    try:
        r = w.suite.get(f"/api/suite/public/forms/r/{token}", headers={"X-Synwevo-Filler": "f" * 32, **CSRF})
        assert r.status_code == 200, r.text
        return r.json()
    finally:
        for k, v in jar.items():
            w.suite.cookies.set(k, v)


def edit(form, title=TITLE):
    return {"id": "edit", "kind": "call", "operation": "forms.form.update_draft", "workspace_id": None,
            "input": {"form_id": form["id"], "draft_rev": form["rev"], "title": title, "description": title}}


def publish(form):
    return {"id": "publish", "kind": "call", "operation": "forms.form.publish", "workspace_id": None,
            "input": {"form_id": form["id"], "draft_rev": {"$ref": "steps.edit.draft_rev"}}}


def in_small(w, *steps):
    return [read(w.p.a)] + [{**s, "workspace_id": w.small} for s in steps]


# ---------------------------------------------------------------- the page shows a version

def test_a_draft_save_does_not_change_what_link_holders_see(world):
    w = world
    form = exposed_form(w)
    # A -> small is allowed (only Alice is in small): the draft may hold A's content.
    assert w.run(in_small(w, edit(form)), "draft") == ("succeeded", None, "end")
    assert w.store.one("SELECT title FROM forms WHERE id=?", (form["id"],))["title"] == SECRET
    for token in (form["public"], form["personal"]):
        shown = page(w, token)
        assert shown["form"] == {"title": ORIGINAL, "description": NOTE}, token
        assert SECRET not in str(shown)


def test_a_closed_link_shows_its_version_not_the_draft(world):
    w = world
    form = exposed_form(w)
    rid =w.store.one("SELECT id FROM form_requests WHERE form_id=? AND kind='personal'", (form["id"],))["id"]
    ws_op(w.suite, "forms.request.close", w.small, {"request_id": rid, "mode": "close"})
    assert w.run(in_small(w, edit(form)), "closed-draft") == ("succeeded", None, "end")
    shown = page(w, form["personal"])
    assert shown["closed"]["code"] == "request_closed"
    assert shown["form"] == {"title": ORIGINAL, "description": NOTE} and SECRET not in str(shown)


def test_a_submitter_outside_the_workspace_is_told_the_title_they_answered(world):
    w = world
    form = exposed_form(w)
    w.suite.sign_in("out")                                   # not a member of A or small
    sid = w.suite.post(f"/api/suite/public/forms/r/{form['personal']}/submit", headers=CSRF,
                       json={"version": 1, "answers": good_answers(), "idempotency_key": "exposure-submit-1"}).json()["submission_id"]
    w.suite.sign_in("alice")
    assert w.run(in_small(w, edit(form)), "notify-draft") == ("succeeded", None, "end")
    ws_op(w.suite, "forms.submission.accept", w.small, {"submission_id": sid})
    told = w.store.all("SELECT params FROM notifications WHERE user_id=? AND kind='forms.submission.accepted'",
                       (w.p.uid("out"),))
    assert len(told) == 1 and ORIGINAL in str(told[0]["params"]) and SECRET not in str(told[0]["params"])


# ---------------------------------------------------------------- publishing is the send

def test_publishing_carried_content_to_a_following_public_link_is_an_outward_send(world):
    w = world
    form = exposed_form(w)
    assert w.refused(w.p.a, w.small, external=True) == "transfer_audience_external"
    assert w.run(in_small(w, edit(form), publish(form)), "publish") == ("blocked", "transfer_audience_external", "publish")
    assert w.store.one("SELECT MAX(version) AS v FROM form_versions WHERE form_id=?", (form["id"],))["v"] == 1
    assert page(w, form["public"])["form"]["title"] == ORIGINAL


def test_a_pinned_public_link_is_not_reached_by_a_new_version(world):
    w = world
    form = exposed_form(w, pinned=True)
    assert w.run(in_small(w, edit(form), publish(form)), "pinned") == ("succeeded", None, "end")
    shown = page(w, form["public"])
    assert shown["version"] == 1 and shown["form"]["title"] == ORIGINAL and SECRET not in str(shown)
    # The personal request pinned version 1 when it was issued, too.
    assert page(w, form["personal"])["form"]["title"] == ORIGINAL


def test_a_source_that_allows_outward_sends_may_be_published(world):
    w = world
    form = exposed_form(w)
    data_flow.set_workspace_outbound(w.store, w.p.a, "any", actor_id=w.alice,
                                     revision=data_flow.workspace_outbound(w.store, w.p.a)["revision"])
    assert w.run(in_small(w, edit(form), publish(form)), "open") == ("succeeded", None, "end")
    shown = page(w, form["public"])
    assert shown["version"] == 2 and shown["form"] == {"title": SECRET, "description": SECRET}


def test_a_person_publishing_their_own_form_is_no_transfer(world):
    w = world
    form = exposed_form(w)
    saved = ws_op(w.suite, "forms.form.update_draft", w.small,
                  {"form_id": form["id"], "draft_rev": form["rev"], "description": "Fixture: second edition"})["output"]
    ws_op(w.suite, "forms.form.publish", w.small, {"form_id": form["id"], "draft_rev": saved["draft_rev"]})
    shown = page(w, form["public"])
    # A description-only change is a new version, and the page shows it once published.
    assert shown["version"] == 2 and shown["form"]["description"] == "Fixture: second edition"


def test_every_caller_with_provenance_is_asked_not_only_flows(world):
    """The Agent records the sources a write carried as ``delegation.transfer_from``; the Forms
    service decides on that, so the Agent path and the flow path give the same answer."""
    w = world
    form = exposed_form(w)
    saved = ws_op(w.suite, "forms.form.update_draft", w.small,
                  {"form_id": form["id"], "draft_rev": form["rev"], "title": "Fixture: carried"})["output"]
    with pytest.raises(ServiceError) as err:
        w.suite.caps.invoke("forms.form.publish", {"form_id": form["id"], "draft_rev": saved["draft_rev"]},
                            user_id=w.alice, workspace_id=w.small, via="agent", idempotency_key="exposure-agent",
                            delegation={"kind": "agent_conversation", "id": "fixture", "transfer_from": [w.p.a]})
    assert err.value.code == "transfer_audience_external"
    assert page(w, form["public"])["form"]["title"] == ORIGINAL


# ---------------------------------------------------------------- upgrade

def test_versions_published_before_the_upgrade_keep_the_description_shown(world):
    w = world
    form = exposed_form(w)
    from synwevo_core.suite_schema import forms as schema
    with w.store.tx() as t:
        t.execute("UPDATE form_versions SET description=NULL WHERE form_id=?", (form["id"],))
        schema.migrate(t)
        schema.migrate(t)                                   # idempotent: a second start changes nothing
    assert w.store.one("SELECT description FROM form_versions WHERE form_id=?", (form["id"],))["description"] == NOTE


# ---------------------------------------------------------------- second review round (R2)

def anonymous_post(w: World, path: str, body: dict):
    jar = dict(w.suite.cookies)
    w.suite.cookies.clear()
    try:
        return w.suite.post(path, headers={"X-Synwevo-Filler": "g" * 32, **CSRF}, json=body)
    finally:
        for k, v in jar.items():
            w.suite.cookies.set(k, v)


def test_a_pinned_public_link_answers_only_for_the_version_it_serves(world):
    """R2-F1: publishing behind a pinned link is not an outward send only if nobody holding the link
    can reach the new version: naming it on submit used to return its rule messages."""
    w = world
    form = exposed_form(w, pinned=True)
    carried = {**DEFINITION,
               "rules": [{"id": "r_one", "field": "f_amount01", "op": "gt", "other": "f_amount01",
                          "message": {"$ref": "steps.read.forms.0.title"}}],
               "confirmation": {"$ref": "steps.read.forms.0.title"}}
    steps = in_small(w, {"id": "edit", "kind": "call", "operation": "forms.form.update_draft", "workspace_id": None,
                         "input": {"form_id": form["id"], "draft_rev": form["rev"], "definition": carried}}, publish(form))
    assert w.run(steps, "pinned-carry") == ("succeeded", None, "end")
    base = f"/api/suite/public/forms/r/{form['public']}/submit"
    for n, answers in enumerate((good_answers(), {**good_answers(), "f_amount01": "1"})):
        r = anonymous_post(w, base, {"version": 2, "answers": answers, "idempotency_key": f"exposure-v2-{n:04d}"})
        assert r.status_code == 409 and r.json()["error"] == "form_version_changed", r.text
        assert SECRET not in r.text
    ok = anonymous_post(w, base, {"version": 1, "answers": good_answers(), "idempotency_key": "exposure-v1-0001"})
    assert ok.status_code in (200, 201) and SECRET not in ok.text, ok.text


def test_a_request_is_named_after_the_version_it_pins(world):
    """R2-F5: the request page and the recipient's notification used the editors' draft title."""
    w = world
    form = exposed_form(w)
    assert w.run(in_small(w, edit(form)), "draft-title") == ("succeeded", None, "end")
    req = ws_op(w.suite, "forms.request.create", w.small,
                {"form_id": form["id"], "version": 1, "recipient": {"user_id": w.p.uid("out")}})["output"]
    shown = page(w, token_of(req["url"]))
    assert shown["request"]["title"] == ORIGINAL and shown["form"]["title"] == ORIGINAL
    told = w.store.all("SELECT params FROM notifications WHERE user_id=? AND kind='forms.request.assigned'", (w.p.uid("out"),))
    assert len(told) == 1 and ORIGINAL in str(told[0]["params"]) and SECRET not in str(told[0]["params"])


def test_links_of_a_frozen_organizations_workspace_close(world):
    """R2-F8: the organization gate closed every operation of its workspaces, but not their links."""
    from synwevo_core.organizations import Organizations
    w = world
    orgs = Organizations(w.store, w.p.ident)
    created = orgs.create(w.alice, "Fixture Org", "Fixture Org main", "fixture-exposure-org")
    oid, main = created["organization"]["id"], created["workspace"]["id"]
    w.suite.sign_in("alice")
    form = ws_op(w.suite, "forms.form.create", main, {"title": ORIGINAL, "definition": DEFINITION})["output"]
    ws_op(w.suite, "forms.form.publish", main, {"form_id": form["id"], "draft_rev": form["draft_rev"]})
    token = token_of(ws_op(w.suite, "forms.public_link.set", main, {"form_id": form["id"], "enabled": True})["output"]["url"])
    assert page(w, token)["form"]["title"] == ORIGINAL
    orgs.transition(oid, w.alice, "freeze", reason="Fixture lifecycle check")
    jar = dict(w.suite.cookies)
    w.suite.cookies.clear()
    try:
        r = w.suite.get(f"/api/suite/public/forms/r/{token}", headers={"X-Synwevo-Filler": "f" * 32, **CSRF})
    finally:
        for k, v in jar.items():
            w.suite.cookies.set(k, v)
    assert r.status_code == 410 and r.json()["error"] == "link_unavailable"


# ---------------------------------------------------------------- third review round (R3)

DRAFT_ONLY = "Fixture unpublished reorg plan"
APPLICANT = "Fixture Applicant Carol"


def bobs_public_form(w: World) -> dict:
    """A form Bob (an editor of A) built, published and opened to the public in A; Alice has
    since renamed its draft to something nobody outside A has been shown."""
    s = w.suite
    s.sign_in("bob")
    form = ws_op(s, "forms.form.create", w.p.a, {"title": ORIGINAL, "definition": DEFINITION})["output"]
    ws_op(s, "forms.form.publish", w.p.a, {"form_id": form["id"], "draft_rev": form["draft_rev"]})
    url = ws_op(s, "forms.public_link.set", w.p.a, {"form_id": form["id"], "enabled": True})["output"]["url"]
    s.sign_in("alice")
    ws_op(s, "forms.form.update_draft", w.p.a, {"form_id": form["id"], "draft_rev": 1, "title": DRAFT_ONLY})
    return {"id": form["id"], "public": token_of(url)}


def told_bob(w: World) -> list[dict]:
    return w.store.all("SELECT kind, params FROM notifications WHERE user_id=? AND kind='forms.submission.received'",
                       (w.p.uid("bob"),))


def submit_publicly(w: World, token: str, key: str):
    r = anonymous_post(w, f"/api/suite/public/forms/r/{token}/submit",
                       {"version": 1, "answers": good_answers(), "idempotency_key": key, "submitter_label": APPLICANT})
    assert r.status_code in (200, 201), r.text
    return r.json()["submission_id"]


def test_the_link_owner_is_told_the_title_of_the_version_answered(world):
    """R3-DF-2: the submission notice named the editors' live draft, not the version answered."""
    w = world
    form = bobs_public_form(w)
    submit_publicly(w, form["public"], "r3-df2-member-0001")
    told = told_bob(w)
    assert len(told) == 1, told
    assert ORIGINAL in str(told[0]["params"]) and DRAFT_ONLY not in str(told[0]["params"])


def test_a_removed_member_is_not_told_of_later_submissions(world):
    """R3-DF-2: whoever opened the public link kept hearing of every submission — who answered,
    and what the editors had renamed the form to — after being removed from the workspace."""
    w = world
    form = bobs_public_form(w)
    w.p.ident.remove_member(w.p.a, w.p.uid("bob"))
    submit_publicly(w, form["public"], "r3-df2-removed-0001")
    assert told_bob(w) == []
    # Switching the link off and on again keeps the public request, and its creator, as they were.
    ws_op(w.suite, "forms.public_link.set", w.p.a, {"form_id": form["id"], "enabled": False})
    again = token_of(ws_op(w.suite, "forms.public_link.set", w.p.a, {"form_id": form["id"], "enabled": True})["output"]["url"])
    submit_publicly(w, again, "r3-df2-removed-0002")
    assert told_bob(w) == []
    # Nobody was told the draft's title: it is not on any page or notice outside the editor.
    assert w.store.all("SELECT id FROM notifications WHERE params LIKE ?", (f"%{DRAFT_ONLY}%",)) == []


#: A fixture deadline (2100-01-01) and the one a branch would pick instead.
FAR = 4102444800.0
FARTHER = FAR + 86400


def personal_request_of(w: World, form: dict) -> str:
    return w.store.one("SELECT id FROM form_requests WHERE form_id=? AND kind='personal'", (form["id"],))["id"]


def test_a_deadline_decided_on_a_source_is_an_outward_send(world):
    """R3-DF-1: the fill page shows a request's deadline to whoever holds its link. Changing it
    after reading A put a value decided on A's content in front of someone outside the workspace,
    where sending that link with the same value was refused."""
    w = world
    form = exposed_form(w)
    rid = personal_request_of(w, form)
    before = page(w, form["personal"])["request"]["due_at"]

    def extend(step_id, due):
        return {"id": step_id, "kind": "call", "operation": "forms.request.extend", "workspace_id": w.small,
                "input": {"request_id": rid, "due_at": due}, **({"next": "end"} if step_id == "yes" else {})}

    steps = [read(w.p.a), branch("guess", TITLE, SECRET, "yes", "no"), extend("yes", FAR), extend("no", FARTHER)]
    assert w.run(steps, "extend-branch") == ("blocked", "transfer_audience_external", "yes")
    assert page(w, form["personal"])["request"]["due_at"] == before
    # The Agent records what it carried the same way; the Forms service gives the same answer.
    with pytest.raises(ServiceError) as err:
        w.suite.caps.invoke("forms.request.extend", {"request_id": rid, "due_at": FAR}, user_id=w.alice,
                            workspace_id=w.small, via="agent", idempotency_key="exposure-extend-agent",
                            delegation={"kind": "agent_conversation", "id": "fixture", "transfer_from": [w.p.a]})
    assert err.value.code == "transfer_audience_external"
    assert page(w, form["personal"])["request"]["due_at"] == before
    # A person changing the deadline by hand carries no recorded source.
    ws_op(w.suite, "forms.request.extend", w.small, {"request_id": rid, "due_at": FAR})
    assert page(w, form["personal"])["request"]["due_at"] == FAR


def test_a_source_that_allows_outward_sends_may_set_the_deadline(world):
    w = world
    form = exposed_form(w)
    rid = personal_request_of(w, form)
    data_flow.set_workspace_outbound(w.store, w.p.a, "any", actor_id=w.alice,
                                     revision=data_flow.workspace_outbound(w.store, w.p.a)["revision"])
    steps = in_small(w, {"id": "extend", "kind": "call", "operation": "forms.request.extend", "workspace_id": None,
                         "input": {"request_id": rid, "due_at": FAR}})
    assert w.run(steps, "extend-open") == ("succeeded", None, "end")
    assert page(w, form["personal"])["request"]["due_at"] == FAR


def link_hash(w: World, form: dict):
    return w.store.one("SELECT public_token_hash FROM forms WHERE id=?", (form["id"],))["public_token_hash"]


def test_a_run_that_read_a_source_may_still_switch_a_public_link_off(world):
    """R3-DF-5: switching a public link off, or replacing a request's link, reaches nobody; yet a
    run that had read A was refused both as outward sends, and the link stayed open. Switching a
    link on is still one."""
    w = world
    form = exposed_form(w)
    off = {"id": "off", "kind": "call", "operation": "forms.public_link.set", "workspace_id": None,
           "input": {"form_id": form["id"], "enabled": False}}
    assert w.run(in_small(w, off), "link-off") == ("succeeded", None, "end")
    assert link_hash(w, form) is None
    on = {**off, "id": "on", "input": {"form_id": form["id"], "enabled": True}}
    assert w.run(in_small(w, on), "link-on") == ("blocked", "transfer_audience_external", "on")
    assert link_hash(w, form) is None
    reissue = {"id": "reissue", "kind": "call", "operation": "forms.request.reissue_link", "workspace_id": None,
               "input": {"request_id": personal_request_of(w, form)}}
    assert w.run(in_small(w, reissue), "reissue") == ("succeeded", None, "end")
    jar = dict(w.suite.cookies)
    w.suite.cookies.clear()
    try:
        gone = w.suite.get(f"/api/suite/public/forms/r/{form['personal']}", headers={"X-Synwevo-Filler": "f" * 32, **CSRF})
    finally:
        for k, v in jar.items():
            w.suite.cookies.set(k, v)
    assert gone.status_code == 404 and gone.json()["error"] == "link_not_found"
