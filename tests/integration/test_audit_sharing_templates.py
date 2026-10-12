"""Shared (organization) templates leave one audit row per change, written by the service in
the transaction of the change (admin coverage G26, decision D7): publish, release, deprecate,
restore, use — identifiers only, never the template's name; a failed audit undoes the copy."""
from __future__ import annotations

import json

from tests.integration.test_org_template_transfer_policy import advance, publish, world  # noqa: F401  (world is a fixture)


def rows(store, prefix="docs.shared_template."):
    return [dict(r) for r in store.all("SELECT * FROM audit_events WHERE action LIKE ? ORDER BY at, id", (prefix + "%",))]


def test_each_shared_template_change_leaves_one_row_without_the_name(world):
    w = world
    pub = publish(w)
    pid, publisher = pub["id"], w["users"]["publisher"]["id"]
    advance(w)
    rel = w["clients"]["publisher"].post(f"/api/organization-templates/{pid}/update", json={})
    assert rel.status_code == 200 and rel.json()["released"] is True, rel.text
    assert w["clients"]["publisher"].post(f"/api/organization-templates/{pid}/deprecate", json={"deprecated": True, "note": "fixture note"}).status_code == 200
    assert w["clients"]["publisher"].post(f"/api/organization-templates/{pid}/deprecate", json={"deprecated": False}).status_code == 200
    used = w["clients"]["reader"].post(f"/api/organization-templates/{pid}/use", json={})
    assert used.status_code == 201, used.text
    got = rows(w["store"])
    assert [r["action"] for r in got] == ["docs.shared_template.publish", "docs.shared_template.release", "docs.shared_template.deprecate",
                                          "docs.shared_template.restore", "docs.shared_template.use"]
    for r in got[:4]:
        assert (r["actor_kind"], r["actor_id"], r["workspace_id"], r["object_id"]) == ("user", publisher, w["source"], pid), r
    use = got[4]
    assert (use["actor_kind"], use["actor_id"], use["workspace_id"], use["object_id"]) == ("user", w["users"]["reader"]["id"], w["target"], pid)
    meta = {r["action"]: json.loads(r["meta"]) for r in got}
    assert meta["docs.shared_template.publish"] == {"template_id": w["tid"], "organization_id": w["oid"], "version_no": 1, "release_no": 1}
    assert meta["docs.shared_template.release"]["release_no"] == 2 and meta["docs.shared_template.release"]["released"] is True
    assert meta["docs.shared_template.deprecate"]["note_given"] is True and "note" not in json.dumps(meta["docs.shared_template.deprecate"]).replace("note_given", "")
    assert meta["docs.shared_template.use"]["template_id"] == used.json()["id"] and meta["docs.shared_template.use"]["release_no"] == 2
    assert all(r["request_id"] for r in got) and all("fixture" not in r["meta"] for r in got), "no template name, no note text"


def test_a_failed_audit_undoes_the_copy_made_from_a_shared_template(world, monkeypatch):
    from synwevo_core import auth
    w = world
    pid = publish(w)["id"]
    before = w["store"].one("SELECT COUNT(*) AS n FROM templates WHERE tenant=?", (w["target"],))["n"]

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", fail)
    r = w["clients"]["reader"].post(f"/api/organization-templates/{pid}/use", json={})
    assert r.status_code >= 500 and "synthetic" not in r.text, r.text
    monkeypatch.setattr(auth, "audit_event", original)
    assert w["store"].one("SELECT COUNT(*) AS n FROM templates WHERE tenant=?", (w["target"],))["n"] == before
    assert w["store"].one("SELECT use_count FROM organization_templates WHERE id=?", (pid,))["use_count"] == 0
    assert not rows(w["store"], "docs.shared_template.use")
