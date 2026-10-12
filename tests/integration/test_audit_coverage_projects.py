"""Every browser write to a Synwevo Docs project or source leaves exactly one audit row, written by
the service inside the transaction of the change (admin coverage G26, decision D7, work package
WP4): identifiers and counts only — never the project's name, a file name, a rule, recognised or
corrected text; a step change is navigation and leaves nothing; a failed audit undoes the change
and its activity entry; a refused write leaves no ``docs.project.*`` row.

The composed server with real sessions (``team`` from test_gate_roles): the gate puts the signed-in
person in the request context, which is the only way the service learns who acted.
"""
from __future__ import annotations

import io
import json
import time

from tests.integration.test_gate_roles import _send, _xlsx, team  # noqa: F401  (team is a fixture)

#: The browser write routes under /api/projects and /api/sources that this work package audits,
#: with the action each one leaves. ``POST /api/sources/{sid}/scan/retry`` (docs.source.scan.retry)
#: is audited too but needs an exhausted scan state; its own tests cover it.
AUDITED = [
    ("POST", "/api/projects", "docs.project.create"),
    ("PATCH", "/api/projects/{pid}", "docs.project.update"),
    ("DELETE", "/api/projects/{pid}", "docs.project.delete"),
    ("POST", "/api/projects/{pid}/sources", "docs.source.add"),
    ("PUT", "/api/projects/{pid}/sources/order", "docs.source.reorder"),
    ("DELETE", "/api/sources/{sid}", "docs.source.remove"),
    ("POST", "/api/sources/{sid}/representative", "docs.source.representative"),
    ("PUT", "/api/sources/{sid}/options", "docs.source.options"),
    ("POST", "/api/sources/{sid}/ocr", "docs.source.ocr"),
    ("PUT", "/api/sources/{sid}/corrections/{mid}", "docs.source.correction"),
    ("POST", "/api/projects/{pid}/desensitize", "docs.source.desensitize"),
]
COVERED_ELSEWHERE = {("POST", "/api/sources/{sid}/scan/retry")}

#: The only keys a row's ``changed`` may name: identifier-valued settings and counters.
IDENTIFIER_FIELDS = {"template_id", "template_version", "archived_at", "settings_rev", "representative", "order", "version", "status"}

#: Content that must never reach a row: the fixture's project and file names, the names this test
#: gives, the corrected text, the fixture OCR engine's name.
CONTENT = ("团队项目", "名单.xlsx", "第二个.xlsx", "扫描夹具.png", "审计夹具项目", "改过的夹具名", "fixture")


def _png() -> bytes:
    """Test fixture: a blank image, generated here (an image source needs OCR)."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 32), "white").save(buf, "PNG")
    return buf.getvalue()


def _mark(store) -> int:
    return store.one("SELECT COALESCE(MAX(seq), 0) AS n FROM audit_events")["n"]


def _since(store, mark: int, prefix: str = "") -> list[dict]:
    return [dict(r) for r in store.all("SELECT * FROM audit_events WHERE seq > ? AND action LIKE ? ORDER BY seq",
                                       (mark, prefix + "%"))]


def _wait_parsed(c, pid: str, sid: str, timeout: float = 10.0) -> str:
    """The parse worker runs in its own thread; a source that is still ``parsing`` has no family yet."""
    deadline = time.monotonic() + timeout
    while True:
        rows = c.get(f"/api/projects/{pid}/sources").json()
        status = next(r["status"] for r in rows if r["id"] == sid)
        if status not in ("parsing", "scanning") or time.monotonic() > deadline:
            return status
        time.sleep(0.1)


# ---------------------------------------------------------------- one call, one row
def test_every_project_and_source_write_route_leaves_exactly_one_row(team, monkeypatch):
    from synwevo_docs import authz
    for method, path, _action in AUDITED:
        assert authz.ROUTE_POLICY.get((method, path)) == "write", f"{method} {path} is not a classified write route"
    for method, path in COVERED_ELSEWHERE:
        assert authz.ROUTE_POLICY.get((method, path)) == "write", f"{method} {path} is not a classified write route"

    store, ws, pid, sid = team["store"], team["ws"], team["pid"], team["sid"]
    owner, owner_id = team["c"]["owner"], team["people"]["owner"]["id"]
    seen: list[tuple[str, str]] = []

    def one(method: str, path: str, action: str, object_kind: str, object_id, **kw):
        """One successful call as the owner, then exactly one new audit row, attributed and clean."""
        label = f"{method} {path}"
        names = {"pid": pid, "sid": sid, **kw.pop("ids", {})}
        mark = _mark(store)
        r = _send(owner, method.lower(), path.format(**names), **kw)
        assert r.status_code == 200, f"{label} → {r.status_code} {r.text[:300]}"
        new = _since(store, mark)
        assert [x["action"] for x in new] == [action], f"{label}: expected one {action} row, got {[x['action'] for x in new]}"
        row = new[0]
        want_id = object_id(r) if callable(object_id) else object_id
        assert (row["actor_kind"], row["actor_id"], row["workspace_id"], row["object_kind"], row["object_id"], row["result"]) \
            == ("user", owner_id, ws, object_kind, want_id, "ok"), f"{label}: {row}"
        assert row["request_id"], f"{label}: the row does not name the request"
        meta = json.loads(row["meta"])
        assert set(meta.get("changed", [])) <= IDENTIFIER_FIELDS, f"{label}: changed names content: {meta}"
        for text in CONTENT:
            assert text not in row["meta"], f"{label}: {text!r} reached the audit row: {row['meta']}"
        seen.append((method, path))
        return r, meta

    # A fresh project: created, renamed, and deleted at the end.
    r, meta = one("POST", "/api/projects", "docs.project.create", "project", lambda r: r.json()["id"], json={"name": "审计夹具项目"})
    pid_new = r.json()["id"]
    assert meta == {"origin": "ui"}
    _, meta = one("PATCH", "/api/projects/{pid}", "docs.project.update", "project", pid_new, ids={"pid": pid_new}, json={"name": "改过的夹具名"})
    assert meta["fields"] == ["name"] and meta["changed"] == ["settings_rev"], meta
    assert meta["after"]["settings_rev"] == meta["before"]["settings_rev"] + 1, meta

    # A second file in the team project: uploaded, ordered first, made representative, removed.
    xlsx_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    r, meta = one("POST", "/api/projects/{pid}/sources", "docs.source.add", "source", lambda r: r.json()[0]["id"],
                  files=[("files", ("第二个.xlsx", _xlsx(), xlsx_type))])
    sid2 = r.json()[0]["id"]
    assert meta == {"project_id": pid, "size": len(_xlsx()), "ord": 2, "representative": False, "scan": "not_configured"}, meta
    _, meta = one("PUT", "/api/projects/{pid}/sources/order", "docs.source.reorder", "project", pid, json={"ids": [sid2, sid]})
    assert meta == {"before": {"order": [sid, sid2]}, "after": {"order": [sid2, sid]}, "changed": ["order"]}, meta
    _, meta = one("POST", "/api/sources/{sid}/representative", "docs.source.representative", "source", sid2, ids={"sid": sid2})
    assert meta == {"project_id": pid, "before": {"representative": sid}, "after": {"representative": sid2}, "changed": ["representative"]}, meta

    # Reading options and a correction on the fixture workbook: the option names and the mapping id, never the values.
    assert _wait_parsed(owner, pid, sid) == "ready"
    _, meta = one("PUT", "/api/sources/{sid}/options", "docs.source.options", "source", sid, json={"sheet": "客户"})
    assert meta["keys"] == ["sheet"] and meta["project_id"] == pid and meta["changed"] == ["version"], meta
    assert meta["after"]["version"] == meta["before"]["version"] + 1 and "客户" not in json.dumps(meta, ensure_ascii=False), meta
    _, meta = one("PUT", "/api/sources/{sid}/corrections/{mid}", "docs.source.correction", "source", sid, ids={"mid": "m1"},
                  json={"value": "fixture-correction", "confirmed": True})
    assert (meta["mapping_id"], meta["cleared"], meta["confirmed"], meta["changed"]) == ("m1", False, True, ["version"]), meta

    # OCR on an image source, with the recognition engine replaced by a test fixture: page counts only.
    from synwevo_docs.engine import ocr as ocr_engine
    monkeypatch.setattr(ocr_engine, "ocr_png", lambda png, **kw: {"words": [], "w": 1, "h": 1, "engine": "test-fixture"})
    up = _send(owner, "post", f"/api/projects/{pid}/sources", files=[("files", ("扫描夹具.png", _png(), "image/png"))])
    assert up.status_code == 200, up.text
    sid_png = up.json()[0]["id"]
    assert _wait_parsed(owner, pid, sid_png) == "needs_ocr"
    r, meta = one("POST", "/api/sources/{sid}/ocr", "docs.source.ocr", "source", sid_png, ids={"sid": sid_png}, json={})
    assert r.json()["recognized"] == [0] and r.json()["status"] == "ready", r.text
    assert (meta["project_id"], meta["pages"], meta["pages_total"]) == (pid, 1, 1), meta
    assert meta["before"] == {"status": "needs_ocr", "version": 1} and meta["after"]["status"] == "ready", meta
    assert meta["changed"] == ["status", "version"], meta

    # A desensitized copy of the workbook, not added to the project: one row for the batch, counts only.
    r, meta = one("POST", "/api/projects/{pid}/desensitize", "docs.source.desensitize", "project", pid,
                  json={"file_ids": [sid], "add_to_project": False})
    assert (meta["requested"], meta["produced"], meta["skipped"]) == (1, 1, 0), f"{meta} ← {r.text[:300]}"
    assert meta["added_to_project"] is False and meta["sources"] == [], meta

    # Removing the representative hands the role to the next file by order; deleting the fresh project.
    _, meta = one("DELETE", "/api/sources/{sid}", "docs.source.remove", "source", sid2, ids={"sid": sid2})
    assert meta == {"project_id": pid, "before": {"representative": sid2}, "after": {"representative": sid}, "changed": ["representative"]}, meta
    _, meta = one("DELETE", "/api/projects/{pid}", "docs.project.delete", "project", pid_new, ids={"pid": pid_new})
    assert meta == {}

    assert sorted(seen) == sorted((m, p) for m, p, _ in AUDITED), "the inventory and the calls made disagree"


# ---------------------------------------------------------------- navigation is not a change
def test_a_step_change_is_navigation_and_leaves_no_row(team):
    store, pid, owner = team["store"], team["pid"], team["c"]["owner"]

    mark = _mark(store)
    r = _send(owner, "patch", f"/api/projects/{pid}", json={"step": "mapping"})
    assert r.status_code == 200 and r.json()["step"] == "mapping", r.text
    assert _since(store, mark) == [], "moving the workbench to another step is not a change of the project"

    mark = _mark(store)
    assert _send(owner, "patch", f"/api/projects/{pid}", json={"archived": True}).status_code == 200
    rows = _since(store, mark)
    assert [x["action"] for x in rows] == ["docs.project.update"]
    meta = json.loads(rows[0]["meta"])
    assert meta["fields"] == ["archived_at"] and meta["changed"] == ["archived_at"], meta
    assert meta["before"]["archived_at"] is None and meta["after"]["archived_at"] is not None, meta

    mark = _mark(store)
    assert _send(owner, "patch", f"/api/projects/{pid}", json={"name": "改名夹具"}).status_code == 200
    rows = _since(store, mark)
    assert [x["action"] for x in rows] == ["docs.project.update"]
    meta = json.loads(rows[0]["meta"])
    assert meta["fields"] == ["name"] and set(meta["changed"]) <= {"settings_rev"}, meta
    assert "改名夹具" not in rows[0]["meta"] and "团队项目" not in rows[0]["meta"], rows[0]["meta"]


# ---------------------------------------------------------------- the row and the change are one transaction
def test_a_failed_audit_undoes_the_project_change_and_its_activity(team, monkeypatch):
    from synwevo_core import auth
    store, pid, owner = team["store"], team["pid"], team["c"]["owner"]

    def project():
        return store.one("SELECT name, settings_rev FROM projects WHERE id=?", (pid,))

    def activity():
        return {r["id"]: r["kind"] for r in store.all("SELECT id, kind FROM project_activity WHERE project_id=?", (pid,))}

    before, acts, mark = project(), activity(), _mark(store)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", fail)
    r = _send(owner, "patch", f"/api/projects/{pid}", json={"name": "不会落地"})
    assert r.status_code >= 500 and "synthetic" not in r.text, r.text
    monkeypatch.setattr(auth, "audit_event", original)

    assert project() == before and before["name"] == "团队项目", project()
    assert _since(store, mark) == [], "a change that was undone has no audit row"
    assert activity() == acts, "the activity hook runs after the commit, so a rolled-back change announces nothing"

    r = _send(owner, "patch", f"/api/projects/{pid}", json={"name": "会落地"})
    assert r.status_code == 200, r.text
    assert [x["action"] for x in _since(store, mark)] == ["docs.project.update"]
    new = {k: v for k, v in activity().items() if k not in acts}
    assert list(new.values()) == ["project.update"], new
    assert project()["settings_rev"] == before["settings_rev"] + 1


# ---------------------------------------------------------------- a refusal is not a change
def test_a_viewer_refusal_leaves_no_row(team):
    store, pid, viewer = team["store"], team["pid"], team["c"]["viewer"]
    mark = _mark(store)
    r = _send(viewer, "patch", f"/api/projects/{pid}", json={"name": "查看者改名"})
    assert r.status_code == 403 and r.json()["error"] == "read_only_role", r.text
    assert _since(store, mark, "docs.project.") == [], "the gate refused before the service ran"
    assert store.one("SELECT name FROM projects WHERE id=?", (pid,))["name"] == "团队项目"
