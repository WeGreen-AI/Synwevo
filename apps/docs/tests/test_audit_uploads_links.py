"""Audit of resumable uploads, signed download links and regression cases (admin coverage G26,
decision D7, work package WP7).

Every state change of an upload — create, complete, abort, consume — leaves one ``audit_events``
row inside the transaction of its own INSERT or UPDATE; minting an artifact or archive link leaves
one row naming the object and the expiry, never the token or the URL; a regression case is
recorded *before* its directory exists (the row first, then the filesystem — D7 §7) and again
before it is removed. The v1 routes are driven with a machine credential, so the gate's principal
names the actor ``("machine", "api:<key id>")``. The regression routes are browser routes of the
Docs app, whose test client runs without sessions: the gate then records no principal ("so nothing
is attributed to an operator by mistake"), and the row the implementation produces names the actor
``("system", None)`` — the same as every other service-level writer without a principal (the jobs
service alone says ``"operator"``, because it passes an explicit request descriptor). These tests
assert that as such.

Every row carries identifiers and counts only: the file name, the digest, the link token, the
URL and the case name are asserted absent from the stored meta.
"""
import hashlib

from synwevo_core import quarantine as Q
from synwevo_docs import regression as RG
from synwevo_docs.api.v1 import LINK_TTL_S
from synwevo_docs.api.v1_uploads import part_path

from .test_api_contract_uploads import _put
from .test_api_v1 import _prepared, _wait
from .test_audit_jobs import _mark, _rows
from .test_audit_projects_v1 import AuditLog, _ok, _wait_ready, machine  # noqa: F401  (machine is a fixture)
from .test_regression_cases import _project as _regression_project

NAME = "审计_客户100.xlsx"            # the upload's file name: must never reach an audit row
STEM = "审计_客户100"


def _fail(*args, **kwargs):
    raise RuntimeError("synthetic audit storage failure")


def _no_secrets(text: str, *forbidden: str) -> None:
    for bad in forbidden:
        assert bad, "an empty needle would prove nothing"
        assert bad not in text, (bad, text)


def _path(url: str) -> str:
    return "/api/v1/" + url.split("/api/v1/", 1)[1]


def _machine_row(row: dict, machine: dict, *, object_kind: str, object_id: str) -> None:
    assert (row["actor_kind"], row["actor_id"]) == ("machine", f"api:{machine['key_id']}"), row
    assert row["workspace_id"] == machine["tenant"], row
    assert (row["object_kind"], row["object_id"]) == (object_kind, object_id), row
    assert row["result"] == "ok" and row["request_id"], row


# ------------------------------------------------------------------ uploads
def test_each_upload_transition_leaves_one_row_and_a_replay_none(client, machine, fixtures):
    c, h = client, machine["h"]
    data = (fixtures / "测试_客户100.xlsx").read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    pid = _ok(c.post("/api/v1/projects", json={"name": "上传审计"}, headers=h), 201)["id"]
    log = AuditLog(machine)                                  # past the project's own row

    # ---- POST /uploads → docs.upload.create; the keyed replay returns the stored response, no row
    body = {"filename": NAME, "size": len(data), "sha256": sha, "purpose": "source"}
    k = {**h, "Idempotency-Key": "audit-upload-create"}
    first = c.post("/api/v1/uploads", json=body, headers=k)
    uid = _ok(first, 201)["id"]
    meta, text = log.one("docs.upload.create", object_kind="upload", object_id=uid)
    assert meta == {"size": len(data), "purpose": "source", "sha256_declared": True}
    _no_secrets(text, STEM, sha)
    again = c.post("/api/v1/uploads", json=body, headers=k)
    assert again.status_code == 201 and again.json()["id"] == uid, again.text[:300]
    assert again.headers.get("Idempotent-Replayed") == "true" and "Idempotent-Replayed" not in first.headers
    log.nothing()

    # ---- PUT the bytes: a data write, not a lifecycle change — no row
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["received"] == len(data)
    log.nothing()

    # ---- POST /uploads/{uid}/complete → docs.upload.complete; completing twice returns the view, no row
    done = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={"sha256": sha}, headers=h))
    assert done["status"] == "complete", done
    meta, text = log.one("docs.upload.complete", object_kind="upload", object_id=uid)
    assert meta == {"size": len(data), "purpose": "source", "checksum_given": True,
                    "before": {"status": "open"}, "after": {"status": "complete"}, "changed": ["status"]}
    _no_secrets(text, STEM, sha)
    assert _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))["status"] == "complete"
    log.nothing()

    # ---- POST /projects/{pid}/files {upload_id} → the source's own docs.source.add (WP4), then
    #      docs.upload.consume saying which upload became it: exactly those two docs.* rows
    src = _ok(c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": uid}, headers=h), 201)
    rows = [r for r in log.new_rows() if r["action"].startswith("docs.")]
    assert [(r["action"], r["object_kind"], r["object_id"]) for r in rows] == \
        [("docs.source.add", "source", src["id"]), ("docs.upload.consume", "upload", uid)], \
        [(r["action"], r["object_id"]) for r in rows]
    _machine_row(rows[0], machine, object_kind="source", object_id=src["id"])
    _machine_row(rows[1], machine, object_kind="upload", object_id=uid)
    assert rows[1]["meta"] == {"kind": "source", "object_id": src["id"],
                               "before": {"status": "complete"}, "after": {"status": "consumed"}, "changed": ["status"]}
    assert rows[0]["request_id"] == rows[1]["request_id"]           # one call, one trace
    for row in rows:
        _no_secrets(row["meta_text"], STEM, sha)
    log.mark()
    assert _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))["status"] == "consumed"
    assert _wait_ready(c, h, src["id"])["status"] == "ready"
    log.nothing(prefix="docs.upload.")                       # parsing the source is not an upload change

    # ---- a second upload: POST /uploads/{uid}/abort → docs.upload.abort; aborting again, no row
    uid2 = _ok(c.post("/api/v1/uploads", json={"filename": NAME, "size": len(data)}, headers=h), 201)["id"]
    meta, text = log.one("docs.upload.create", object_kind="upload", object_id=uid2)
    assert meta == {"size": len(data), "purpose": None, "sha256_declared": False}
    _no_secrets(text, STEM)
    assert _ok(c.post(f"/api/v1/uploads/{uid2}/abort", headers=h))["status"] == "aborted"
    meta, text = log.one("docs.upload.abort", object_kind="upload", object_id=uid2)
    assert meta == {"reason": "requested", "before": {"status": "open"}, "after": {"status": "aborted"}, "changed": ["status"]}
    _no_secrets(text, STEM)
    again = c.post(f"/api/v1/uploads/{uid2}/abort", headers=h)
    assert again.status_code == 200 and again.json()["status"] == "aborted", again.text[:300]
    log.nothing()
    gone = c.post(f"/api/v1/uploads/{uid2}/complete", json={}, headers=h)
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "upload_aborted", gone.text[:300]
    log.nothing()


def test_a_standing_verdict_at_completion_leaves_an_abort_row(client, machine):
    """CI-90: bytes under a standing quarantine verdict are not stored by a completed upload. The
    verdict is a TEST FIXTURE entry written with `quarantine.hold` (the recipe the source
    operations tests use); the row says why the upload ended — never what the bytes were."""
    c, h, store = client, machine["h"], machine["store"]
    data = b"synthetic quarantined bytes (test fixture) " * 32
    sha = hashlib.sha256(data).hexdigest()
    uid = _ok(c.post("/api/v1/uploads", json={"filename": NAME, "size": len(data)}, headers=h), 201)["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    Q.hold(store, sha, data, signature="Synthetic.Test.Verdict", engine="fixture", rows=[], by="test")
    log = AuditLog(machine)                                  # past the create row

    r = c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "malware_detected", r.text[:300]
    meta, text = log.one("docs.upload.abort", object_kind="upload", object_id=uid)
    assert meta == {"reason": "malware_verdict", "before": {"status": "open"}, "after": {"status": "aborted"}, "changed": ["status"]}
    _no_secrets(text, STEM, sha, "Synthetic.Test.Verdict")
    live = _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))
    assert live["status"] == "aborted" and live["received"] == 0, live
    assert not part_path(store, uid).exists() and not store.blob_path(sha).exists()
    # Terminal: a second complete is refused and an abort is a no-op — neither writes a row.
    assert c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h).status_code == 410
    assert _ok(c.post(f"/api/v1/uploads/{uid}/abort", headers=h))["status"] == "aborted"
    log.nothing()


# ------------------------------------------------------------------ signed links
def test_minting_a_link_leaves_one_row_without_the_token(client, api, machine, fixtures):
    c, h = client, machine["h"]
    keys = {"rw": h}                                         # _prepared / _wait read only keys["rw"]
    pid = _prepared(api, keys, fixtures)
    job = _wait(api, keys, _ok(c.post(f"/api/v1/projects/{pid}/jobs", json={"formats": ["docx"]}, headers=h))["id"])
    assert job["status"] == "completed", job
    art = _ok(c.get(f"/api/v1/jobs/{job['id']}/artifacts", headers=h))["items"][0]
    log = AuditLog(machine)                                  # past every project, template and job row

    # ---- POST /artifacts/{aid}/link → docs.artifact.link
    k = {**h, "Idempotency-Key": "audit-artifact-link"}
    first = c.post(f"/api/v1/artifacts/{art['id']}/link", headers=k)
    link = _ok(first, 200)
    token = link["url"].rsplit("/", 1)[1]
    assert token and c.get(_path(link["url"])).status_code == 200     # a real export, not a dry run
    meta, text = log.one("docs.artifact.link", object_kind="artifact", object_id=art["id"])
    assert meta == {"expires_at": link["expires_at"], "ttl_s": LINK_TTL_S}
    _no_secrets(text, "token", "url", "/download/", token, art["filename"], art["sha256"])
    again = c.post(f"/api/v1/artifacts/{art['id']}/link", headers=k)
    assert again.status_code == 200 and again.json() == link, again.text[:300]
    assert again.headers.get("Idempotent-Replayed") == "true" and "Idempotent-Replayed" not in first.headers
    log.nothing()

    # ---- POST /jobs/{jid}/archive-link → docs.job.archive_link, with the format asked for
    zk = {**h, "Idempotency-Key": "audit-archive-link"}
    z = _ok(c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={"format": "docx"}, headers=zk), 200)
    ztoken = z["url"].rsplit("/", 1)[1]
    assert ztoken and c.get(_path(z["url"])).status_code == 200
    meta, text = log.one("docs.job.archive_link", object_kind="job", object_id=job["id"])
    assert meta == {"expires_at": z["expires_at"], "ttl_s": LINK_TTL_S, "format": "docx"}
    _no_secrets(text, "token", "url", "/download/", ztoken)
    again = c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={"format": "docx"}, headers=zk)
    assert again.status_code == 200 and again.json() == z and again.headers.get("Idempotent-Replayed") == "true", again.text[:300]
    log.nothing()

    # ---- without a format the row says so; a fresh (unkeyed) mint is a new export with its own row
    plain = _ok(c.post(f"/api/v1/jobs/{job['id']}/archive-link", json={}, headers=h), 200)
    meta, text = log.one("docs.job.archive_link", object_kind="job", object_id=job["id"])
    assert meta == {"expires_at": plain["expires_at"], "ttl_s": LINK_TTL_S, "format": None}
    _no_secrets(text, "token", "url", "/download/", plain["url"].rsplit("/", 1)[1])
    assert log.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.artifact.link' AND object_id=?",
                         (art["id"],))["n"] == 1
    assert log.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.job.archive_link' AND object_id=?",
                         (job["id"],))["n"] == 2


# ------------------------------------------------------------------ regression cases (browser routes)
def test_a_regression_case_is_recorded_before_its_directory_and_on_delete(api, fixtures, tmp_path, monkeypatch):
    from synwevo_core import auth

    monkeypatch.setenv("SYNWEVO_DOCS_REGRESSION_DIR", str(tmp_path / "regression"))
    c = api.c
    store = c.app_state.app.store
    pid = _regression_project(api, fixtures)
    cases = RG.cases_dir("default", store.root)              # honours the directory set above
    mark = _mark(store)

    # ---- capture → docs.regression_case.create, written before case.json existed
    case = api.ok(c.post(f"/api/projects/{pid}/regression-cases", json={"name": "审计用例（夹具）", "note": "备注不入审计"}))
    cid = case["id"]
    rows = _rows(store, mark, "docs.")
    assert [(r["action"], r["object_kind"], r["object_id"]) for r in rows] == \
        [("docs.regression_case.create", "regression_case", cid)], [(r["action"], r["object_id"]) for r in rows]
    row = rows[0]
    # The Docs test client runs without sessions, the gate records no principal, and the writer
    # takes the actor from the context: what the implementation produces here is ("system", None).
    assert (row["actor_kind"], row["actor_id"]) == ("system", None), row
    assert row["workspace_id"] == "default" and row["result"] == "ok" and row["request_id"], row
    assert case["records_total"] == 15 and case["samples"] == 15, case
    assert row["meta"] == {"project_id": pid, "records_total": 15, "samples": 15, "sources": 1}
    _no_secrets(row["meta_text"], "审计用例", "备注", "订单", "回归用例")
    case_json = cases / cid / "case.json"
    assert case_json.is_file()
    assert row["at"] <= case_json.stat().st_mtime, (row["at"], case_json.stat().st_mtime)
    mark = _mark(store)

    # ---- delete → docs.regression_case.delete, written before the directory went
    assert api.ok(c.delete(f"/api/regression-cases/{cid}"))["ok"] is True
    rows = _rows(store, mark, "docs.")
    assert [(r["action"], r["object_kind"], r["object_id"]) for r in rows] == \
        [("docs.regression_case.delete", "regression_case", cid)], [(r["action"], r["object_id"]) for r in rows]
    assert (rows[0]["actor_kind"], rows[0]["actor_id"]) == ("system", None), rows[0]
    assert rows[0]["workspace_id"] == "default" and rows[0]["result"] == "ok" and rows[0]["request_id"], rows[0]
    assert rows[0]["meta"] == {}
    assert not (cases / cid).exists()
    mark = _mark(store)

    # ---- the row comes first: a capture whose row cannot be written leaves no directory behind
    before = sorted(p.name for p in cases.iterdir()) if cases.exists() else []
    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", _fail)
    r = c.post(f"/api/projects/{pid}/regression-cases", json={"name": "写不进审计"})
    assert r.status_code >= 500 and "synthetic" not in r.text, (r.status_code, r.text[:300])
    assert r.json()["error"] == "internal_error" and r.json().get("request_id"), r.text[:300]
    monkeypatch.setattr(auth, "audit_event", original)
    after = sorted(p.name for p in cases.iterdir()) if cases.exists() else []
    assert after == before == [], (before, after)
    assert _rows(store, mark, "docs.") == []
    assert api.ok(c.get("/api/regression-cases")) == []

    # with the writer back, the same capture goes through once, with its row and its directory
    case2 = api.ok(c.post(f"/api/projects/{pid}/regression-cases", json={"name": "写得进审计"}))
    rows = _rows(store, mark, "docs.")
    assert [(r["action"], r["object_id"]) for r in rows] == [("docs.regression_case.create", case2["id"])]
    assert (cases / case2["id"] / "case.json").is_file()


# ------------------------------------------------------------------ atomicity
def test_a_failed_audit_undoes_the_upload_completion(client, machine, monkeypatch):
    from synwevo_core import auth

    c, h, store = client, machine["h"], machine["store"]
    data = b"synthetic upload bytes (test fixture) " * 64
    sha = hashlib.sha256(data).hexdigest()
    uid = _ok(c.post("/api/v1/uploads", json={"filename": NAME, "size": len(data)}, headers=h), 201)["id"]
    assert _put(c, h, uid, data, 0, len(data)).status_code == 200
    log = AuditLog(machine)                                  # past the create row

    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", _fail)
    r = c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h)
    assert r.status_code >= 500 and "synthetic" not in r.text, (r.status_code, r.text[:300])
    monkeypatch.setattr(auth, "audit_event", original)

    # the completion went with its row: still open with every byte, no blob bound, staging kept
    log.nothing()
    live = _ok(c.get(f"/api/v1/uploads/{uid}", headers=h))
    assert live["status"] == "open" and live["received"] == len(data), live
    raw = store.one("SELECT status, blob FROM uploads WHERE id=?", (uid,))
    assert (raw["status"], raw["blob"]) == ("open", None), raw
    assert part_path(store, uid).stat().st_size == len(data)

    # with the writer back, the same complete goes through once, with its one row
    done = _ok(c.post(f"/api/v1/uploads/{uid}/complete", json={}, headers=h))
    assert done["status"] == "complete", done
    meta, text = log.one("docs.upload.complete", object_kind="upload", object_id=uid)
    assert meta == {"size": len(data), "purpose": None, "checksum_given": False,
                    "before": {"status": "open"}, "after": {"status": "complete"}, "changed": ["status"]}
    _no_secrets(text, STEM, sha)
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='docs.upload.complete' AND object_id=?",
                     (uid,))["n"] == 1
    assert store.one("SELECT blob FROM uploads WHERE id=?", (uid,))["blob"] == sha
    assert not part_path(store, uid).exists()
