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
from .test_data_flow_runs import SECRET, TITLE, World, read
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
