"""Template changes as events (API-5): a workspace's event feed hears template.created / updated /
deleted and, for the organization library, published / released / deprecated — each written in the
transaction that changed the row (synwevo_core.template_events). Composed app, the organization
publication fixture of the transfer-policy tests; synthetic accounts and documents only."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.context import as_tenant
from synwevo_core.template_events import TEMPLATE_EVENT_TYPES
from tests.integration.test_org_template_transfer_policy import document, world  # noqa: F401 (fixture)

TYPES = ",".join(TEMPLATE_EVENT_TYPES)


def feed(w, tenant):
    key, _ = Auth(w["store"]).create_key(f"events {tenant}", tenant, ("read",))
    with TestClient(w["api"]) as c:
        r = c.get(f"/api/v1/events?types={TYPES}", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_template_changes_and_publications_are_announced_to_the_workspace_with_ids_only(world):
    w = world
    app, tid, publisher = w["api"].state.app, w["tid"], w["users"]["publisher"]["id"]
    created = feed(w, w["source"])
    assert [e["type"] for e in created] == ["template.created"]
    assert created[0]["data"] == {"template_id": tid, "kind": created[0]["data"]["kind"], "format": "docx", "head": 1}
    with as_tenant(w["source"]):
        app.new_version(tid, document("Synthetic release two"), [], "fixture next head")      # head 1 → 2
        pub = w["shared"].publish(tid, publisher)             # release 1 of version 2
        app.new_version(tid, document("Synthetic release three"), [], "fixture third head")   # head 2 → 3
        release = w["shared"].update(pub["id"], publisher)    # release 2 of version 3
        w["shared"].set_deprecated(pub["id"], publisher, True, "synthetic deprecation")
        app.delete_template(tid)
    events = feed(w, w["source"])[1:]
    assert [e["type"] for e in events] == ["template.updated", "template.published", "template.updated",
                                           "template.released", "template.deprecated", "template.deleted"]
    updated, published, updated_again, released, deprecated, deleted = events
    assert updated["data"] == {"template_id": tid, "head": 2, "previous_head": 1}
    assert published["data"] == {"template_id": tid, "publication_id": pub["id"], "organization_id": w["oid"], "release_no": 1, "version_no": 2}
    assert updated_again["data"] == {"template_id": tid, "head": 3, "previous_head": 2}
    assert released["data"] == {"template_id": tid, "publication_id": pub["id"], "organization_id": w["oid"], "release_no": 2, "version_no": 3}
    assert release["release_no"] == 2
    assert deprecated["data"] == {"template_id": tid, "publication_id": pub["id"], "organization_id": w["oid"], "release_no": 2}
    assert deleted["data"] == {"template_id": tid, "head": 3}
    assert all(e["job_id"] is None for e in events)
    # Identifiers and numbers only: no template name, field name, note or document text.
    everything = json.dumps(events) + json.dumps(w["store"].all("SELECT data FROM job_events WHERE tenant=?", (w["source"],)), default=str)
    for secret in ("fixture", "fixture next head", "fixture third head", "synthetic deprecation", "Synthetic release"):
        assert secret not in everything, secret
    # The organization's other workspace hears nothing about the source's templates.
    assert feed(w, w["target"]) == []
