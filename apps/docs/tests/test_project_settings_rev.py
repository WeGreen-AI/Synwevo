"""A project's settings carry a revision: a screen that edited revision N is refused when someone
else's edit landed first, instead of silently overwriting it. Callers that say nothing keep the
old behaviour."""
from __future__ import annotations


def test_stale_settings_are_refused_and_fresh_ones_advance_the_revision(client):
    pid = client.post("/api/projects", json={"name": "并发项目"}).json()["id"]
    p = client.get(f"/api/projects/{pid}").json()
    assert p["settings_rev"] == 0 and p["copied_from"] is None
    # window A saves a rule at revision 0
    r = client.patch(f"/api/projects/{pid}", json={"record_rule": {"mode": "per_file"}, "base_settings_rev": 0})
    assert r.status_code == 200 and r.json()["settings_rev"] == 1
    # window B, still on revision 0, tries to save its own rule: refused with the current revision
    r = client.patch(f"/api/projects/{pid}", json={"record_rule": {"mode": "per_file"}, "base_settings_rev": 0})
    assert r.status_code == 409 and r.json()["error"] == "stale_project" and r.json()["detail"]["settings_rev"] == 1
    assert client.get(f"/api/projects/{pid}").json()["record_rule"]["mode"] == "per_file"
    # a rename and an output change are settings too; a step change is navigation, not a setting
    assert client.patch(f"/api/projects/{pid}", json={"name": "改名", "base_settings_rev": 1}).json()["settings_rev"] == 2
    assert client.patch(f"/api/projects/{pid}", json={"output": {"naming": "{序号}"}, "base_settings_rev": 2}).json()["settings_rev"] == 3
    assert client.patch(f"/api/projects/{pid}", json={"step": "mapping", "base_settings_rev": 3}).json()["settings_rev"] == 3
    # not an integer
    assert client.patch(f"/api/projects/{pid}", json={"name": "x", "base_settings_rev": "abc"}).status_code == 400
    # a caller that does not say which revision it edited is not checked (machine clients)
    assert client.patch(f"/api/projects/{pid}", json={"name": "机器改名"}).json()["settings_rev"] == 4
    # the listing carries the revision so the home page's rename can send it back
    listed = client.get("/api/projects").json()
    items = listed["items"] if isinstance(listed, dict) else listed
    assert next(x for x in items if x["id"] == pid)["settings_rev"] == 4
