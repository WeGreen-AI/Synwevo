"""Synwevo Forms, end to end through its operations and its fill-in links.

Acceptance covered here: A2 (one published form, independent requests), A3 (versions pinned,
copies carry no data), D5 (public link / personal prefill / revoke / expiry / changes requested do
not leak other requests), plus the data rules of `forms.definition` (hidden fields cleared at
submission, money as exact decimals) and attachments through the platform's upload check.
"""
from __future__ import annotations

import time

import pytest

CSRF = {"X-Synwevo-CSRF": "fixture-csrf"}
F_NAME, F_KIND, F_REASON, F_AMOUNT, F_FILE, F_AGREE = "f_name0001", "f_kind0001", "f_reason01", "f_amount01", "f_file0001", "f_agree001"

DEFINITION = {
    "fields": [
        {"id": F_NAME, "type": "text", "label": "Full name", "required": True, "max_length": 100},
        {"id": F_KIND, "type": "select", "label": "Kind", "required": True,
         "options": [{"value": "travel", "label": "Travel"}, {"value": "other", "label": "Other"}]},
        {"id": F_REASON, "type": "textarea", "label": "Why other?", "required": True,
         "visible_if": {"field": F_KIND, "op": "eq", "value": "other"}},
        {"id": F_AMOUNT, "type": "money", "label": "Amount", "required": True, "currency": "CNY",
         "min": "0", "max": "100000"},
        {"id": F_FILE, "type": "file", "label": "Receipt", "accept": [".pdf", ".png"], "max_files": 2, "max_size_mb": 1},
        {"id": F_AGREE, "type": "boolean", "label": "I confirm", "required": True},
    ],
    "confirmation": "Thanks, we will check it.",
}


def op(c, name, ws, input=None, *, key=None, expect=200):
    body = {"workspace_id": ws, "input": input or {}}
    if key:
        body["idempotency_key"] = key
    r = c.post(f"/api/suite/ops/{name}", json=body, headers=CSRF)
    assert r.status_code == expect, (name, r.status_code, r.text)
    return r.json()


def published_form(c, ws, definition=DEFINITION):
    form = op(c, "forms.form.create", ws, {"title": "Expense claim", "definition": definition})["output"]
    op(c, "forms.form.publish", ws, {"form_id": form["id"], "draft_rev": form["draft_rev"]})
    return form["id"]


def token_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


def good_answers(**extra):
    return {F_NAME: "Bob  Smith", F_KIND: "travel", F_AMOUNT: "12.5", F_AGREE: True, **extra}


def test_draft_publish_versions_and_pinned_requests(suite):
    p = suite.people
    suite.sign_in("bob")
    form = op(suite, "forms.form.create", p.a, {"title": "Empty"})["output"]
    r = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": 1}, expect=422)
    assert r["error"] == "form_not_publishable" and r["detail"]["problems"][0]["code"] == "no_fields"
    saved = op(suite, "forms.form.update_draft", p.a, {"form_id": form["id"], "draft_rev": 1, "definition": DEFINITION})["output"]
    assert saved["draft_rev"] == 2
    stale = op(suite, "forms.form.update_draft", p.a, {"form_id": form["id"], "draft_rev": 1, "title": "x"}, expect=409)
    assert stale["error"] == "draft_conflict" and stale["detail"]["current"]["draft_rev"] == 2
    v1 = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": 2})["output"]
    assert v1["version"] == 1
    again = op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": 2}, expect=409)
    assert again["error"] == "nothing_to_publish"
    req1 = op(suite, "forms.request.create", p.a, {"form_id": form["id"]})["output"]
    changed = {**DEFINITION, "fields": DEFINITION["fields"] + [{"id": "f_extra001", "type": "text", "label": "Extra", "required": True}]}
    op(suite, "forms.form.update_draft", p.a, {"form_id": form["id"], "draft_rev": 2, "definition": changed})
    assert op(suite, "forms.form.publish", p.a, {"form_id": form["id"], "draft_rev": 3})["output"]["version"] == 2
    req2 = op(suite, "forms.request.create", p.a, {"form_id": form["id"]})["output"]
    assert (req1["version"], req2["version"]) == (1, 2)
    # The old request still serves version 1: the new required field is not asked of it.
    loaded = suite.get(f"/api/suite/public/forms/r/{token_of(req1['url'])}").json()
    assert loaded["version"] == 1 and "f_extra001" not in {f["id"] for f in loaded["definition"]["fields"]}
    # Retiring version 1 stops new requests on it; the open one continues.
    op(suite, "forms.form.retire_version", p.a, {"form_id": form["id"], "version": 1})
    assert op(suite, "forms.request.create", p.a, {"form_id": form["id"], "version": 1}, expect=409)["error"] == "form_version_retired"
    assert suite.get(f"/api/suite/public/forms/r/{token_of(req1['url'])}").json()["version"] == 1


def test_one_published_form_serves_independent_requests(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    reqs = [op(suite, "forms.request.create", p.a, {"form_id": fid, "prefill": {F_NAME: f"Person {i}"},
                                                    "title": f"Flow {i}"})["output"] for i in range(3)]
    assert len({r["request_id"] for r in reqs}) == 3
    first = token_of(reqs[0]["url"])
    loaded = suite.get(f"/api/suite/public/forms/r/{first}").json()
    assert loaded["prefill"] == {F_NAME: "Person 0"}
    sub = suite.post(f"/api/suite/public/forms/r/{first}/submit", headers=CSRF,
                     json={"version": 1, "answers": good_answers(), "idempotency_key": "submit-key-0001"})
    assert sub.status_code == 200, sub.text
    states = {r["id"]: r["status"] for r in op(suite, "forms.request.list", p.a, {"form_id": fid})["output"]["requests"]}
    assert states[reqs[0]["request_id"]] == "submitted"
    assert states[reqs[1]["request_id"]] == states[reqs[2]["request_id"]] == "open"
    # Each request's page shows only its own prefill.
    assert suite.get(f"/api/suite/public/forms/r/{token_of(reqs[1]['url'])}").json()["prefill"] == {F_NAME: "Person 1"}


def test_submission_rules_hidden_fields_money_and_required(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    tok = token_of(op(suite, "forms.request.create", p.a, {"form_id": fid})["output"]["url"])
    base = f"/api/suite/public/forms/r/{tok}"
    missing = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": {F_KIND: "other", F_AMOUNT: "12.345"},
                                                 "idempotency_key": "submit-key-0002"})
    assert missing.status_code == 422
    codes = {(e["field"], e["code"]) for e in missing.json()["detail"]["errors"]}
    assert {(F_NAME, "required"), (F_REASON, "required"), (F_AMOUNT, "too_many_decimals"), (F_AGREE, "required")} <= codes
    # The reason was typed while "other" was chosen, then the kind changed: it is not submitted.
    ok = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**{F_REASON: "typed earlier"}),
                                            "idempotency_key": "submit-key-0003"})
    assert ok.status_code == 200, ok.text
    sid = ok.json()["submission_id"]
    got = op(suite, "forms.submission.get", p.a, {"submission_id": sid})["output"]
    assert F_REASON not in got["answers"]
    assert got["answers"][F_AMOUNT] == {"amount": "12.50", "currency": "CNY"}
    assert got["answers"][F_NAME] == "Bob  Smith"                    # not rewritten
    replay = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(), "idempotency_key": "submit-key-0003"})
    assert replay.json()["replayed"] is True and replay.json()["submission_id"] == sid
    twice = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(), "idempotency_key": "submit-key-0004"})
    assert twice.status_code == 409 and twice.json()["error"] == "already_submitted"
    ev = p.store.all("SELECT type FROM app_events WHERE object_id=?", (sid,))
    assert [e["type"] for e in ev] == ["forms.submission.created"]


def test_drafts_are_not_submissions_and_conflicts_are_reported(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    tok = token_of(op(suite, "forms.request.create", p.a, {"form_id": fid})["output"]["url"])
    base = f"/api/suite/public/forms/r/{tok}"
    d1 = suite.put(base + "/draft", headers=CSRF, json={"answers": {F_NAME: "Half"}, "step": 0, "rev": 0}).json()
    assert d1["rev"] == 1
    assert suite.get(base).json()["draft"]["answers"] == {F_NAME: "Half"}
    stale = suite.put(base + "/draft", headers=CSRF, json={"answers": {F_NAME: "Other tab"}, "rev": 5})
    assert stale.status_code == 409 and stale.json()["detail"]["rev"] == 1
    assert p.store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"] == 0
    assert p.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type LIKE 'forms.submission.%'")["n"] == 0


def test_changes_requested_reopen_for_a_new_revision(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    tok = token_of(op(suite, "forms.request.create", p.a, {"form_id": fid})["output"]["url"])
    base = f"/api/suite/public/forms/r/{tok}"
    sid = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(), "idempotency_key": "submit-key-0005"}).json()["submission_id"]
    suite.sign_in("vera")
    assert op(suite, "forms.submission.accept", p.a, {"submission_id": sid}, expect=403)["error"] == "read_only_role"
    suite.sign_in("bob")
    op(suite, "forms.submission.request_changes", p.a, {"submission_id": sid, "note": "Please attach",
                                                       "items": [{"field_id": F_AMOUNT, "note": "Amount looks wrong"}]})
    loaded = suite.get(base).json()
    assert loaded["review"]["items"][0]["field_id"] == F_AMOUNT
    assert loaded["review"]["previous_answers"][F_AMOUNT]["amount"] == "12.50"
    r2 = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**{F_AMOUNT: "15"}),
                                            "idempotency_key": "submit-key-0006"}).json()
    assert r2["revision"] == 2
    first = p.store.one("SELECT status FROM form_submissions WHERE id=?", (sid,))
    assert first["status"] == "superseded"
    got = op(suite, "forms.submission.get", p.a, {"submission_id": r2["submission_id"]})["output"]
    assert [h["revision"] for h in got["history"]] == [1, 2]
    op(suite, "forms.submission.accept", p.a, {"submission_id": r2["submission_id"]})
    assert op(suite, "forms.request.list", p.a, {"form_id": fid})["output"]["requests"][0]["status"] == "accepted"
    types = [e["type"] for e in p.store.all("SELECT type FROM app_events WHERE type LIKE 'forms.submission.%' ORDER BY seq")]
    assert types == ["forms.submission.created", "forms.submission.changes_requested",
                     "forms.submission.resubmitted", "forms.submission.accepted"]


def test_expiry_revoke_and_reissue(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    req = op(suite, "forms.request.create", p.a, {"form_id": fid, "due_at": time.time() + 3600})["output"]
    tok = token_of(req["url"])
    p.store.execute("UPDATE form_requests SET due_at=? WHERE id=?", (time.time() - 1, req["request_id"]))
    closed = suite.get(f"/api/suite/public/forms/r/{tok}").json()
    assert closed["closed"]["code"] == "link_expired" and "definition" not in closed
    assert suite.get(f"/api/suite/public/forms/r/{tok}").json()["closed"]["code"] == "link_expired"
    assert p.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='forms.request.expired'")["n"] == 1
    sub = suite.post(f"/api/suite/public/forms/r/{tok}/submit", headers=CSRF, json={"version": 1, "answers": good_answers(),
                                                                       "idempotency_key": "submit-key-0007"})
    assert sub.status_code == 410
    op(suite, "forms.request.extend", p.a, {"request_id": req["request_id"], "due_at": time.time() + 3600})
    assert "definition" in suite.get(f"/api/suite/public/forms/r/{tok}").json()
    new = op(suite, "forms.request.reissue_link", p.a, {"request_id": req["request_id"]})["output"]
    assert suite.get(f"/api/suite/public/forms/r/{tok}").status_code == 404
    new_tok = token_of(new["url"])
    assert "definition" in suite.get(f"/api/suite/public/forms/r/{new_tok}").json()
    op(suite, "forms.request.close", p.a, {"request_id": req["request_id"], "mode": "revoke"})
    assert suite.get(f"/api/suite/public/forms/r/{new_tok}").json()["closed"]["code"] == "link_revoked"
    assert op(suite, "forms.request.get", p.a, {"request_id": req["request_id"]})["output"]["url"] is None


def test_public_link_isolates_fillers_and_shows_no_prefill(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    op(suite, "forms.request.create", p.a, {"form_id": fid, "prefill": {F_NAME: "Private Person"}})
    link = op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": True})["output"]
    tok = token_of(link["url"])
    suite.cookies.clear()
    base = f"/api/suite/public/forms/r/{tok}"
    assert suite.get(base).status_code == 400                      # no filler key
    ann = {"X-Synwevo-Filler": "a" * 32, **CSRF}
    ben = {"X-Synwevo-Filler": "b" * 32, **CSRF}
    first = suite.get(base, headers=ann).json()
    assert first["prefill"] == {} and first["request"]["id"] is None
    suite.put(base + "/draft", json={"answers": {F_NAME: "Ann secret"}}, headers=ann)
    assert suite.get(base, headers=ben).json()["draft"] is None
    up = suite.post(base + "/files", data={"field_id": F_FILE}, files={"file": ("r.pdf", b"%PDF-1.4 ann", "application/pdf")},
                    headers=ann)
    assert up.status_code == 200, up.text
    assert up.json()["scan"] == "not_configured"
    fid_ann = up.json()["id"]
    assert suite.get(base + f"/files/{fid_ann}", headers=ben).status_code == 404
    stolen = suite.post(base + "/submit", headers=ben, json={"version": 1, "idempotency_key": "submit-key-0008",
                                                             "answers": good_answers(**{F_FILE: [fid_ann]})})
    assert stolen.status_code == 422 and stolen.json()["detail"]["errors"][0]["code"] == "file_missing"
    for who, key in ((ann, "submit-key-0009"), (ben, "submit-key-0010")):
        answers = good_answers(**({F_FILE: [fid_ann]} if who is ann else {}))
        r = suite.post(base + "/submit", headers=who, json={"version": 1, "answers": answers, "idempotency_key": key})
        assert r.status_code == 200, r.text
    # A public link accepts many submissions; each filler sees only their own.
    assert len(suite.get(base, headers=ann).json()["submissions"]) == 1
    suite.sign_in("bob")
    op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": False})
    suite.cookies.clear()
    assert suite.get(base, headers=ann).status_code == 404


def test_attachments_are_checked_and_downloads_follow_export_rights(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    tok = token_of(op(suite, "forms.request.create", p.a, {"form_id": fid})["output"]["url"])
    base = f"/api/suite/public/forms/r/{tok}"
    bad = suite.post(base + "/files", headers=CSRF, data={"field_id": F_FILE}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and bad.json()["error"] == "file_type_not_accepted"
    big = suite.post(base + "/files", headers=CSRF, data={"field_id": F_FILE}, files={"file": ("x.pdf", b"0" * (1024 * 1024 + 1), "application/pdf")})
    assert big.status_code == 413
    ok = suite.post(base + "/files", headers=CSRF, data={"field_id": F_FILE}, files={"file": ("../../etc/receipt.pdf", b"%PDF-1.4 x", "application/pdf")}).json()
    assert ok["filename"] == "receipt.pdf"
    sid = suite.post(base + "/submit", headers=CSRF, json={"version": 1, "answers": good_answers(**{F_FILE: [ok["id"]]}),
                                             "idempotency_key": "submit-key-0011"}).json()["submission_id"]
    got = op(suite, "forms.submission.get", p.a, {"submission_id": sid})["output"]
    assert got["files"][0]["scan"] == "not_configured"
    url = got["files"][0]["url"]
    assert suite.get(url).content == b"%PDF-1.4 x"
    suite.sign_in("vera")
    assert suite.get(url).status_code == 403
    suite.sign_in("out")
    assert suite.get(url).status_code == 403


def test_sign_in_required_links_open_only_for_the_recipient(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    req = op(suite, "forms.request.create", p.a, {"form_id": fid, "require_sign_in": True,
                                                  "recipient": {"email": "vera@example.test"}})["output"]
    tok = token_of(req["url"])
    assert p.store.one("SELECT recipient_user_id FROM form_requests WHERE id=?", (req["request_id"],))["recipient_user_id"] == p.uid("vera")
    note = p.store.one("SELECT kind, user_id FROM notifications WHERE object_id=?", (req["request_id"],))
    assert note == {"kind": "forms.request.assigned", "user_id": p.uid("vera")}
    suite.cookies.clear()
    assert suite.get(f"/api/suite/public/forms/r/{tok}").status_code == 401
    suite.sign_in("out")
    assert suite.get(f"/api/suite/public/forms/r/{tok}").status_code == 403
    suite.sign_in("vera")
    assert suite.get(f"/api/suite/public/forms/r/{tok}").status_code == 200


def test_workspaces_do_not_see_each_others_forms_and_copies_carry_no_data(suite):
    p = suite.people
    suite.sign_in("alice")
    fid = published_form(suite, p.a)
    op(suite, "forms.request.create", p.a, {"form_id": fid})
    # Alice reads B (viewer) — the form of A is not there.
    assert op(suite, "forms.form.get", p.b, {"form_id": fid}, expect=404)["error"] == "form_not_found"
    # She cannot create in B, so she cannot copy into it either.
    assert op(suite, "forms.form.duplicate", p.a, {"form_id": fid, "target_workspace_id": p.b}, expect=403)["error"] == "read_only_role"
    copy = op(suite, "forms.form.duplicate", p.a, {"form_id": fid, "title": "Copy"})["output"]
    got = op(suite, "forms.form.get", p.a, {"form_id": copy["form_id"]})["output"]
    assert got["published_version"] is None and got["versions"] == [] and got["public_link"] is None
    assert op(suite, "forms.request.list", p.a, {"form_id": copy["form_id"]})["output"]["requests"] == []
    # Oscar (owner of B) can copy a B form into B; a stranger cannot read A at all.
    suite.sign_in("out")
    assert op(suite, "forms.form.list", p.a, expect=403)["error"] == "app_access_denied"


def test_request_creation_is_idempotent_per_caller(suite):
    p = suite.people
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    a = op(suite, "forms.request.create", p.a, {"form_id": fid}, key="req-key-1")
    b = op(suite, "forms.request.create", p.a, {"form_id": fid}, key="req-key-1")
    assert b["replayed"] and a["output"]["request_id"] == b["output"]["request_id"]
    assert p.store.one("SELECT COUNT(*) AS n FROM form_requests WHERE kind='personal'")["n"] == 1


@pytest.mark.parametrize("scope", ["platform", "workspace"])
def test_switching_forms_off_closes_its_fill_links_until_it_is_switched_on(suite, scope):
    """B1/B2: `/api/suite/public/` passes the per-application gate by, so a fill link — public or
    personal — asks on every use whether Synwevo Forms is on, platform-wide and in the form's
    workspace, through the service methods the admin console and the command line call. Off, the
    page and every write are refused (410 `link_unavailable`); on again, the same link works."""
    from synwevo_core.apps import Apps
    p = suite.people
    apps = Apps(p.store, p.ident)
    suite.sign_in("bob")
    fid = published_form(suite, p.a)
    personal = token_of(op(suite, "forms.request.create", p.a, {"form_id": fid})["output"]["url"])
    public = token_of(op(suite, "forms.public_link.set", p.a, {"form_id": fid, "enabled": True})["output"]["url"])
    suite.cookies.clear()
    ann = {"X-Synwevo-Filler": "a" * 32, **CSRF}
    links = [f"/api/suite/public/forms/r/{public}", f"/api/suite/public/forms/r/{personal}"]

    def calls(n):
        for base in links:
            yield suite.get(base, headers=ann)
            yield suite.put(base + "/draft", json={"answers": {F_NAME: "Ann"}}, headers=ann)
            yield suite.post(base + "/files", data={"field_id": F_FILE},
                             files={"file": ("r.pdf", b"%PDF-1.4 fixture", "application/pdf")}, headers=ann)
            yield suite.post(base + "/submit", headers=ann, json={"version": 1, "answers": good_answers(),
                                                                  "idempotency_key": f"submit-switch-{scope}-{n}-{base[-6:]}"})

    for r in calls(0):
        assert r.status_code == 200, r.text
    if scope == "platform":
        assert apps.set_status("forms", "disabled")
    else:
        assert apps.disable_for_workspace(p.a, "forms")
    for r in calls(1):
        assert r.status_code == 410 and r.json()["error"] == "link_unavailable", r.text
    submitted = p.store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"]
    if scope == "platform":
        assert apps.set_status("forms", "active")
    else:
        apps.enable_for_workspace(p.a, "forms")
    assert suite.get(links[0], headers=ann).status_code == 200
    assert suite.get(links[1], headers=ann).json()["submissions"], "the personal request kept its submission"
    again = suite.post(links[0] + "/submit", headers=ann, json={"version": 1, "answers": good_answers(),
                                                                "idempotency_key": f"submit-switch-{scope}-again"})
    assert again.status_code == 200, again.text
    assert p.store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"] == submitted + 1
