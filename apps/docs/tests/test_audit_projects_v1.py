"""Audit of project and source changes made through the machine API (/api/v1).

Admin coverage G26, decision D7: the service writes the audit row inside the transaction of the
change, attributed to the request principal the gate put in the context. For a legacy ``dw_``
API key `authority.audit_actor` names that principal ``("machine", "api:<key id>")``. These tests
drive every v1 twin of the project and source write routes with such a key and read the rows
back from ``audit_events``: exactly one row per change, the machine identity, the key's
workspace, the request id, and identifiers or counts only — never a name, a rule or a word.
"""
import json
import time

import pytest

from synwevo_core.auth import Auth

FIXTURE_WORD = "夹具词"          # what the OCR test fixture "recognises"; must never reach an audit row
RULE = {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 5}}


@pytest.fixture()
def machine(client):
    """One machine credential of the default workspace with every scope the routes need
    (``admin`` for DELETE /projects/{pid})."""
    store = client.app_state.app.store
    token, row = Auth(store).create_key("audit-v1", "default", ("read", "write", "admin"))
    return {"h": {"Authorization": f"Bearer {token}"}, "key_id": row["id"], "tenant": "default", "store": store}


def _ok(r, status=None):
    assert r.status_code < 400, (r.status_code, r.text[:800])
    if status is not None:
        assert r.status_code == status, (r.status_code, r.text[:800])
    return r.json()


class AuditLog:
    """Reads ``audit_events`` after a mark. ``seq`` is the table's autoincrement, so "the rows this
    call wrote" is exact: everything above the mark taken before the call."""

    def __init__(self, machine):
        self.store = machine["store"]
        self.machine = machine
        self.mark()

    def mark(self):
        self.seq = self.store.one("SELECT COALESCE(MAX(seq), 0) AS m FROM audit_events")["m"]

    def new_rows(self):
        rows = [dict(r) for r in self.store.all("SELECT * FROM audit_events WHERE seq>? ORDER BY seq", (self.seq,))]
        for r in rows:
            r["meta_text"] = r["meta"] or ""
            r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        return rows

    def one(self, action, *, object_kind, object_id):
        """Exactly one new ``docs.*`` row, and it is this action on this object by the machine key.
        Returns ``(meta, meta_text)``; the next check counts from here."""
        rows = self.new_rows()
        docs = [r for r in rows if r["action"].startswith("docs.")]
        assert [r["action"] for r in docs] == [action], [(r["action"], r["object_id"]) for r in rows]
        row = docs[0]
        assert (row["actor_kind"], row["actor_id"]) == ("machine", f"api:{self.machine['key_id']}"), row
        assert row["workspace_id"] == self.machine["tenant"], row
        assert (row["object_kind"], row["object_id"]) == (object_kind, object_id), row
        assert row["result"] == "ok" and row["request_id"], row
        self.mark()
        return row["meta"], row["meta_text"]

    def nothing(self, *, prefix="docs."):
        rows = [r for r in self.new_rows() if r["action"].startswith(prefix)]
        assert rows == [], [(r["action"], r["object_id"]) for r in rows]
        self.mark()


def _upload(c, h, pid, name, data):
    src = _ok(c.post(f"/api/v1/projects/{pid}/files", files={"file": (name, data)}, headers=h), 201)
    return src


def _wait_ready(c, h, fid, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        src = _ok(c.get(f"/api/v1/files/{fid}", headers=h))
        if src["status"] not in ("parsing", "scanning"):
            return src
        time.sleep(0.05)
    raise AssertionError(f"source {fid} still not parsed after {timeout}s")


def test_each_v1_project_and_source_write_leaves_one_machine_row(client, machine, fixtures):
    c, h, app = client, machine["h"], client.app_state.app
    log = AuditLog(machine)
    xlsx = (fixtures / "测试_客户100.xlsx").read_bytes()

    # ---- POST /projects → docs.project.create
    pid = _ok(c.post("/api/v1/projects", json={"name": "审计项目"}, headers=h), 201)["id"]
    meta, text = log.one("docs.project.create", object_kind="project", object_id=pid)
    assert meta == {"origin": f"api:{machine['key_id']}"}
    assert "审计项目" not in text

    # ---- POST /projects/{pid}/files → docs.source.add (first file becomes the representative)
    s1 = _upload(c, h, pid, "测试_客户100.xlsx", xlsx)["id"]
    meta, text = log.one("docs.source.add", object_kind="source", object_id=s1)
    assert meta == {"project_id": pid, "size": len(xlsx), "ord": 1, "representative": True,
                    "scan": "pending" if app.scanner is not None else "not_configured"}
    assert "测试_客户100" not in text
    assert _wait_ready(c, h, s1)["status"] == "ready"

    s2 = _upload(c, h, pid, "测试_客户100副本.xlsx", xlsx)["id"]
    meta, text = log.one("docs.source.add", object_kind="source", object_id=s2)
    assert meta["ord"] == 2 and meta["representative"] is False and meta["project_id"] == pid
    assert "副本" not in text
    assert _wait_ready(c, h, s2)["status"] == "ready"

    # ---- PUT /projects/{pid}/files/order → docs.source.reorder (object: the project)
    out = _ok(c.put(f"/api/v1/projects/{pid}/files/order", json={"ids": [s2, s1]}, headers=h))
    assert [f["id"] for f in out] == [s2, s1]
    meta, _ = log.one("docs.source.reorder", object_kind="project", object_id=pid)
    assert meta == {"before": {"order": [s1, s2]}, "after": {"order": [s2, s1]}, "changed": ["order"]}

    # ---- POST /files/{fid}/representative → docs.source.representative
    assert _ok(c.post(f"/api/v1/files/{s2}/representative", headers=h))["representative"] is True
    meta, _ = log.one("docs.source.representative", object_kind="source", object_id=s2)
    assert meta == {"project_id": pid, "before": {"representative": s1}, "after": {"representative": s2},
                    "changed": ["representative"]}

    # ---- PATCH /projects/{pid} (archive) → docs.project.update over archived_at only
    rev = _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["settings_rev"]
    patched = _ok(c.patch(f"/api/v1/projects/{pid}", json={"base_settings_rev": rev, "archived": True}, headers=h))
    assert patched["archived_at"]
    meta, _ = log.one("docs.project.update", object_kind="project", object_id=pid)
    assert meta["fields"] == ["archived_at"] and meta["changed"] == ["archived_at"]
    assert meta["before"] == {"archived_at": None} and meta["after"]["archived_at"] == patched["archived_at"]

    # ---- PUT /projects/{pid}/rule → docs.project.update naming the field, never the rule
    # (the preview counts every live file: two copies of the workbook, five rows each)
    assert _ok(c.put(f"/api/v1/projects/{pid}/rule", json={"rule": RULE}, headers=h))["records"] == 10
    meta, text = log.one("docs.project.update", object_kind="project", object_id=pid)
    assert meta["fields"] == ["record_rule"]
    assert set(meta["changed"]) <= {"settings_rev"} and meta["changed"] == ["settings_rev"]
    assert meta["before"] == {"settings_rev": rev} and meta["after"] == {"settings_rev": rev + 1}
    assert "per_row" not in text and "客户" not in text and "header_row" not in text

    # ---- PUT /projects/{pid}/template → docs.project.update over template_id / template_version
    with open(fixtures / "测试_合同模板.docx", "rb") as f:
        tid = _ok(c.post("/api/v1/templates", files={"file": ("测试_合同模板.docx", f.read())}, headers=h), 201)["id"]
    log.mark()                                   # the template upload is not a project or source change
    bound = _ok(c.put(f"/api/v1/projects/{pid}/template", json={"template_id": tid}, headers=h))
    assert bound["template"]["id"] == tid and bound["settings_rev"] == rev + 2
    meta, text = log.one("docs.project.update", object_kind="project", object_id=pid)
    assert meta["fields"] == ["template_id", "template_version"]
    assert meta["changed"] == ["settings_rev", "template_id"]
    assert meta["before"] == {"settings_rev": rev + 1, "template_id": None, "template_version": None}
    assert meta["after"] == {"settings_rev": rev + 2, "template_id": tid, "template_version": None}
    assert "合同模板" not in text

    # ---- PUT /files/{fid}/corrections/{mid} → docs.source.correction (mapping id, never the text)
    # The route 404s unless the mapping exists in the project's current revision, so one is saved
    # first through the v1 mappings route (a binding target needs no template field); that save
    # is not one of the audited project/source changes and is marked past.
    saved = _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"mappings": [
        {"sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": "audit-target"}}]}))
    mid = saved["mappings"][0]["id"]
    log.mark()
    before_version = _ok(c.get(f"/api/v1/files/{s1}", headers=h))["version"]
    corr = _ok(c.put(f"/api/v1/files/{s1}/corrections/{mid}", json={"value": "更正后的文本", "confirmed": True}, headers=h))
    assert corr[mid]["value"] == "更正后的文本"
    meta, text = log.one("docs.source.correction", object_kind="source", object_id=s1)
    assert meta == {"project_id": pid, "mapping_id": mid, "cleared": False, "confirmed": True,
                    "before": {"version": before_version}, "after": {"version": before_version + 1}, "changed": ["version"]}
    assert "更正后的文本" not in text

    # ---- DELETE /files/{fid} → docs.source.remove (the representative moves to the survivor)
    assert _ok(c.delete(f"/api/v1/files/{s2}", headers=h)) == {"deleted": s2}
    meta, _ = log.one("docs.source.remove", object_kind="source", object_id=s2)
    assert meta == {"project_id": pid, "before": {"representative": s2}, "after": {"representative": s1},
                    "changed": ["representative"]}

    # ---- DELETE /projects/{pid} (admin scope) → docs.project.delete
    assert _ok(c.delete(f"/api/v1/projects/{pid}", headers=h)) == {"deleted": pid}
    meta, _ = log.one("docs.project.delete", object_kind="project", object_id=pid)
    assert meta == {}
    assert c.get(f"/api/v1/projects/{pid}", headers=h).status_code == 404


def test_an_idempotent_replay_of_create_project_writes_no_second_row(client, machine):
    c, h = client, machine["h"]
    log = AuditLog(machine)
    k = {**h, "Idempotency-Key": "audit-create-1"}
    first = c.post("/api/v1/projects", json={"name": "幂等审计"}, headers=k)
    again = c.post("/api/v1/projects", json={"name": "幂等审计"}, headers=k)
    assert first.status_code == 201 and again.status_code == 201, (first.text[:300], again.text[:300])
    assert first.json()["id"] == again.json()["id"]
    assert again.headers.get("Idempotent-Replayed") == "true" and "Idempotent-Replayed" not in first.headers
    meta, _ = log.one("docs.project.create", object_kind="project", object_id=first.json()["id"])
    assert meta["origin"] == f"api:{machine['key_id']}"
    assert log.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.project.create' AND object_id=?",
                         (first.json()["id"],))["n"] == 1


def test_a_stale_patch_writes_no_row(client, machine):
    c, h = client, machine["h"]
    log = AuditLog(machine)
    pid = _ok(c.post("/api/v1/projects", json={"name": "过期修改"}, headers=h), 201)["id"]
    log.one("docs.project.create", object_kind="project", object_id=pid)
    rev = _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["settings_rev"]
    r = c.patch(f"/api/v1/projects/{pid}", json={"base_settings_rev": rev + 7, "archived": True}, headers=h)
    assert r.status_code == 409, r.text[:300]
    err = r.json()["error"]
    assert err["code"] == "stale_project", err
    assert err["details"] == {"settings_rev": rev, "base_settings_rev": rev + 7}, err
    log.nothing()
    assert log.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.project.update' AND object_id=?",
                         (pid,))["n"] == 0
    assert _ok(c.get(f"/api/v1/projects/{pid}", headers=h))["archived_at"] is None


def test_ocr_through_v1_writes_one_row_without_words(client, machine, fixtures, monkeypatch):
    """The recognition engine is replaced by a TEST FIXTURE that returns one known word, so the
    assertion that no recognised text reaches the audit row is about a word we control."""
    import synwevo_docs.engine.ocr as ocr_module

    calls = []

    def fixture_ocr_png(png: bytes, langs: str = "chi_sim+eng", psm: int = 3) -> dict:
        calls.append(len(png))
        return {"words": [{"t": FIXTURE_WORD, "conf": 99, "x": 0, "y": 0, "w": 1, "h": 1}],
                "w": 1, "h": 1, "engine": "test-fixture"}

    monkeypatch.setattr(ocr_module, "ocr_png", fixture_ocr_png)
    c, h = client, machine["h"]
    log = AuditLog(machine)
    pid = _ok(c.post("/api/v1/projects", json={"name": "识别审计"}, headers=h), 201)["id"]
    fid = _upload(c, h, pid, "测试_申请表.pdf", (fixtures / "测试_申请表.pdf").read_bytes())["id"]
    src = _wait_ready(c, h, fid)
    assert src["status"] in ("ready", "needs_ocr"), src
    log.mark()                                   # past the create and add rows

    out = _ok(c.post(f"/api/v1/files/{fid}/ocr", json={"pages": [0]}, headers=h))
    assert calls, "the OCR fixture was not used: the row below would be about a real engine"
    assert out["recognized"] == [0] and out["words"] == 1 and out["version"] == src["version"] + 1
    meta, text = log.one("docs.source.ocr", object_kind="source", object_id=fid)
    assert meta["project_id"] == pid and meta["pages"] == 1 and meta["pages_total"] == 1
    assert meta["before"] == {"status": src["status"], "version": src["version"]}
    assert meta["after"] == {"status": "ready", "version": src["version"] + 1}
    assert "version" in meta["changed"]
    assert FIXTURE_WORD not in text and "test-fixture" not in text and "words" not in meta
