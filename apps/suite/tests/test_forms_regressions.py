"""Defects found by the release acceptance of Synwevo Forms, each held by the behaviour a person
relies on (fixture people and workspaces only; isolated store under ``tmp_path``).

* D1 — publishing while the editor's autosave was refused published the last saved draft, and the
  field the person had just added disappeared.
"""
from __future__ import annotations

from .test_forms import DEFINITION, op

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
