"""Defects found by the release acceptance of Synwevo Forms, each held by the behaviour a person
relies on (fixture people and workspaces only; isolated store under ``tmp_path``).

* D1 — publishing while the editor's autosave was refused published the last saved draft, and the
  field the person had just added disappeared.
* D3 — after a resubmission that kept the earlier attachment, revision 1 showed no attachment: the
  file was moved to revision 2.
* D4 — the request page behind a public submission offered the personal-request actions;
  重新生成链接 there minted a link that opened to 链接无效 while the old public link kept working.
* D8 — a submission on a personal request to a named outside recipient was headed 匿名填写人.
"""
from __future__ import annotations

import pytest

from synwevo_core.errors import ServiceError
from .test_forms import CSRF, DEFINITION, F_AMOUNT, F_FILE, good_answers, op, published_form, token_of

GROUP = {"id": "f_lines001", "type": "repeat", "label": "Line items",
         "fields": [{"id": "f_lndesc01", "type": "text", "label": "Description"},
                    {"id": "f_lnamt001", "type": "number", "label": "Amount"},
                    {"id": "f_lndate01", "type": "date", "label": "Date"}]}


def _fields(definition: dict) -> list[str]:
    return [f["id"] for f in definition["fields"]]


# ---------------------------------------------------------------- D1: publish what is seen

def test_publishing_a_canvas_that_was_never_saved_is_refused_and_publishes_nothing(suite):
    p = suite.people
    suite.sign_in("bob")
    form = op(suite, "forms.form.create", p.a, {"title": "Claim", "definition": DEFINITION})["output"]
    seen = {**DEFINITION, "fields": DEFINITION["fields"] + [GROUP]}
    # The editor's save of `seen` was refused (or never arrived): the stored draft is still the
    # old one, at the same revision the page holds. Publishing what the page shows must not
    # publish the stored draft in its place.
    refused = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": form["draft_rev"],
                                                    "title": "Claim", "description": "", "definition": seen},
                 expect=409)
    assert refused["error"] == "draft_unsaved"
    assert refused["detail"]["draft_rev"] == form["draft_rev"]
    got = op(suite, "forms.form.get", p.a, {"form_id": form["id"]})["output"]
    assert got["published_version"] is None and got["versions"] == []
    # A title the page shows but the server never stored is refused the same way.
    title = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": form["draft_rev"],
                                                  "title": "Claim (renamed)", "definition": DEFINITION},
               expect=409)
    assert title["error"] == "draft_unsaved"
    # Once the canvas is saved, the same publish goes through and the version is the canvas.
    saved = op(suite, "forms.form.update_draft", p.a, {"form_id": form["id"], "draft_rev": form["draft_rev"],
                                                       "definition": seen})["output"]
    out = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": saved["draft_rev"],
                                                "title": "Claim", "description": "", "definition": seen})["output"]
    version = op(suite, "forms.form.version", p.a, {"form_id": form["id"], "version": out["version"]})["output"]
    assert _fields(version["definition"]) == _fields(seen)
    group = version["definition"]["fields"][-1]
    assert [s["label"] for s in group["fields"]] == ["Description", "Amount", "Date"]


def test_a_sub_field_without_a_name_is_refused_where_the_editor_can_name_it(suite):
    """The editor words this refusal by the path the server reports (Editor.tsx
    `draftRefusals`): the group's index, the sub-field's index and the property."""
    p = suite.people
    suite.sign_in("bob")
    form = op(suite, "forms.form.create", p.a, {"title": "Claim", "definition": DEFINITION})["output"]
    unnamed = {**GROUP, "fields": [{"id": "f_lndesc01", "type": "text", "label": ""}]}
    seen = {**DEFINITION, "fields": DEFINITION["fields"] + [unnamed]}
    r = op(suite, "forms.form.update_draft", p.a, {"form_id": form["id"], "draft_rev": form["draft_rev"],
                                                   "definition": seen}, expect=422)
    assert r["error"] == "invalid_input"
    at = len(DEFINITION["fields"])
    assert ["definition", "fields", str(at), "fields", "0", "label"] in [f["loc"] for f in r["detail"]["fields"]]
    # Nothing was stored, so nothing can be published as if it had been.
    assert op(suite, "forms.form.get", p.a, {"form_id": form["id"]})["output"]["draft_rev"] == form["draft_rev"]


# ---------------------------------------------------------------- D3: revisions keep their files

def test_each_revision_keeps_showing_the_attachment_it_was_submitted_with(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    base = f"/api/suite/public/forms/r/{token_of(op(suite, 'forms.request.create', p.a, {'form_id': fid})['output']['url'])}"
    pdf = b"%PDF-1.4 fixture receipt"
    up = suite.post(base + "/files", headers=CSRF, data={"field_id": F_FILE},
                    files={"file": ("receipt.pdf", pdf, "application/pdf")}).json()
    rev1 = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**{F_FILE: [up["id"]]}),
                                                         "idempotency_key": "d3-submit-0001"}).json()["submission_id"]
    op(suite, "forms.submission.request_changes", p.a, {"submission_id": rev1, "items": [{"field_id": F_AMOUNT, "note": "Check"}]})
    # The filler keeps the file as it is and corrects the amount.
    page = suite.get(base).json()
    assert page["review"]["previous_answers"][F_FILE] == [up["id"]] and [f["id"] for f in page["files"]] == [up["id"]]
    rev2 = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**{F_AMOUNT: "15", F_FILE: [up["id"]]}),
                                                         "idempotency_key": "d3-submit-0002"}).json()
    assert rev2["revision"] == 2
    forms = suite.api.state.forms
    for sid in (rev1, rev2["submission_id"]):
        got = op(suite, "forms.submission.get", p.a, {"submission_id": sid})["output"]
        assert [(f["id"], f["filename"]) for f in got["files"]] == [(up["id"], "receipt.pdf")], sid
        assert suite.get(got["files"][0]["url"]).content == pdf
        # The export that names the revision (an Extract import) finds it too.
        assert forms.file_bytes(p.uid("bob"), p.a, up["id"], submission_id=sid)[1] == pdf
    # Kept, not copied: one file record and one stored blob for both revisions.
    assert p.store.one("SELECT COUNT(*) AS n FROM form_files")["n"] == 1
    # The filler sees the file once, and may still replace nothing they did not keep.
    assert [f["id"] for f in suite.get(base).json()["files"]] == [up["id"]]


def test_a_file_is_exported_only_under_a_submission_that_used_it(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    subs = {}
    for n in (1, 2):
        base = f"/api/suite/public/forms/r/{token_of(op(suite, 'forms.request.create', p.a, {'form_id': fid})['output']['url'])}"
        extra = {}
        if n == 1:
            up = suite.post(base + "/files", headers=CSRF, data={"field_id": F_FILE},
                            files={"file": ("r.pdf", b"%PDF-1.4 one", "application/pdf")}).json()
            extra = {F_FILE: [up["id"]]}
        subs[n] = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**extra),
                                                                   "idempotency_key": f"d3-other-000{n}"}).json()["submission_id"]
    forms = suite.api.state.forms
    with pytest.raises(ServiceError) as refused:
        forms.file_bytes(p.uid("bob"), p.a, up["id"], submission_id=subs[2])
    assert refused.value.status == 404 and refused.value.code == "file_not_found"
    assert op(suite, "forms.submission.get", p.a, {"submission_id": subs[2]})["output"]["files"] == []


# ---------------------------------------------------------------- D4: the public link's request

def test_the_public_links_request_is_managed_in_the_share_settings_only(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    public = token_of(op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": True})["output"]["url"])
    base = f"/api/suite/public/forms/r/{public}"
    filler = {"X-Synwevo-Filler": "p" * 32, **CSRF}
    sid = suite.post(base + "/submit", headers=filler, json={"version": 1, "answers": good_answers(),
                                                             "idempotency_key": "d4-submit-0001"}).json()["submission_id"]
    rid = op(suite, "forms.submission.get", p.a, {"submission_id": sid})["output"]["request_id"]
    req = op(suite, "forms.request.get", p.a, {"request_id": rid})["output"]
    assert req["kind"] == "public" and req["form_title"] == "Expense claim"
    # A personal request's actions do not apply: each is refused with a reason, and none of them
    # changes what the public link does.
    for name, extra in (("forms.request.reissue_link", {}), ("forms.request.extend", {"due_at": None}),
                        ("forms.request.close", {"mode": "close"}), ("forms.request.close", {"mode": "revoke"})):
        refused = op(suite, name, p.a, {"request_id": rid, **extra}, expect=409)
        assert refused["error"] == "public_link_request", name
    row = p.store.one("SELECT status, token_hash, due_at FROM form_requests WHERE id=?", (rid,))
    assert row == {"status": "open", "token_hash": None, "due_at": None}
    assert "definition" in suite.get(base, headers=filler).json()
    # The share settings are where it changes: a new public link replaces the old one at once.
    new = token_of(op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": True})["output"]["url"])
    assert suite.get(base, headers=filler).status_code == 404
    assert "definition" in suite.get(f"/api/suite/public/forms/r/{new}", headers=filler).json()


# ---------------------------------------------------------------- D8: who a submission is from

def test_a_personal_requests_submission_names_the_recipient_the_request_names(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    req = op(suite, "forms.request.create", p.a, {"form_id": fid, "recipient": {"email": "carol@outside.example",
                                                                                "name": "Carol Fixture"}})["output"]
    suite.cookies.clear()                      # Carol has no account: she fills the link signed out
    sid = suite.post(f"/api/suite/public/forms/r/{token_of(req['url'])}/submit", headers=CSRF,
                     json={"version": 1, "answers": good_answers(), "idempotency_key": "d8-submit-0001"}).json()["submission_id"]
    suite.sign_in("bob")
    named = {"name": "Carol Fixture", "email": "carol@outside.example"}
    assert op(suite, "forms.submission.get", p.a, {"submission_id": sid})["output"]["recipient"] == named
    listed = op(suite, "forms.submission.list", p.a, {"form_id": fid})["output"]["submissions"]
    assert [s["recipient"] for s in listed] == [named]
    on_request = op(suite, "forms.request.get", p.a, {"request_id": req["request_id"]})["output"]["submissions"]
    assert [s["recipient"] for s in on_request] == [named]
    # A public link names nobody.
    public = token_of(op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": True})["output"]["url"])
    suite.cookies.clear()
    anon = suite.post(f"/api/suite/public/forms/r/{public}/submit", headers={"X-Synwevo-Filler": "q" * 32, **CSRF},
                      json={"version": 1, "answers": good_answers(), "idempotency_key": "d8-submit-0002"}).json()["submission_id"]
    suite.sign_in("bob")
    assert op(suite, "forms.submission.get", p.a, {"submission_id": anon})["output"]["recipient"] is None
