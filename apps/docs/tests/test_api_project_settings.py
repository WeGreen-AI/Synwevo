"""/api/v1 project settings (API-2): rename, archive and output defaults through the workbench's
service, with the settings revision required so a machine client never overwrites unseen edits."""
import threading

from .test_api_v1 import _ok, keys  # noqa: F401  (keys is a fixture)


def _project(c, h, name="settings fixture"):
    pid = _ok(c.post("/api/v1/projects", headers=h, json={"name": name}))["id"]
    return _ok(c.get(f"/api/v1/projects/{pid}", headers=h))


def test_rename_archive_and_output_defaults_advance_the_revision(api, keys):
    c, h = api.c, keys["rw"]
    p = _project(c, h)
    assert p["settings_rev"] == 0 and p["archived_at"] is None
    out = _ok(c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={
        "base_settings_rev": 0, "name": "  季度报表  ", "output": {"formats": ["docx"], "layout": "warn"}}))
    assert out["name"] == "季度报表" and out["output"] == {"formats": ["docx"], "layout": "warn"}
    assert out["settings_rev"] == 1
    archived = _ok(c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"base_settings_rev": 1, "archived": True}))
    assert archived["archived_at"] and archived["settings_rev"] == 1       # archiving is not a settings edit
    restored = _ok(c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"base_settings_rev": 1, "archived": False}))
    assert restored["archived_at"] is None
    # The workbench reads the same project.
    assert _ok(c.get(f"/api/projects/{p['id']}"))["name"] == "季度报表"


def test_a_stale_revision_changes_nothing(api, keys):
    c, h = api.c, keys["rw"]
    p = _project(c, h)
    _ok(c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"base_settings_rev": 0, "name": "first"}))
    r = c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"base_settings_rev": 0, "name": "second"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_project"
    assert r.json()["error"]["details"]["settings_rev"] == 1
    assert _ok(c.get(f"/api/v1/projects/{p['id']}", headers=h))["name"] == "first"


def test_two_writers_on_one_revision_cannot_both_win(api, keys):
    c, h = api.c, keys["rw"]
    p = _project(c, h)
    codes = []

    def rename(n):
        codes.append(c.patch(f"/api/v1/projects/{p['id']}", headers=h, json={"base_settings_rev": 0, "name": f"writer {n}"}).status_code)

    threads = [threading.Thread(target=rename, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(codes) == [200] + [409] * 5, codes
    assert _ok(c.get(f"/api/v1/projects/{p['id']}", headers=h))["settings_rev"] == 1


def test_bad_requests_are_named_and_scoped(api, keys):
    c, h = api.c, keys["rw"]
    p = _project(c, h)
    url = f"/api/v1/projects/{p['id']}"
    cases = [({"name": "x"}, "base_settings_rev"), ({"base_settings_rev": "0", "name": "x"}, "base_settings_rev"),
             ({"base_settings_rev": 0}, "body"), ({"base_settings_rev": 0, "name": "  "}, "name"),
             ({"base_settings_rev": 0, "archived": "yes"}, "archived"), ({"base_settings_rev": 0, "record_rule": {}}, "record_rule"),
             ({"base_settings_rev": 0, "template_id": "t"}, "template_id")]
    for body, field in cases:
        r = c.patch(url, headers=h, json=body)
        assert r.status_code == 400 and r.json()["error"]["field"] == field, (body, r.text)
    r = c.patch(url, headers=h, json={"base_settings_rev": 0, "output": {"layout": "fitt"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_layout"
    assert c.patch(url, headers=keys["ro"], json={"base_settings_rev": 0, "name": "y"}).status_code == 403
    assert c.patch(url, headers=keys["other"], json={"base_settings_rev": 0, "name": "y"}).status_code == 404
    assert _ok(c.get(url, headers=h))["settings_rev"] == 0
