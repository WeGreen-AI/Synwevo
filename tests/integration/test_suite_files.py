"""Real Forms attachment -> Extract parser/review, using the actual server composition."""
import hashlib
import pytest
from synwevo_core import quarantine
from synwevo_core.errors import ServiceError
from tests.integration.test_suite_adapters import world, sign_in

pytestmark = pytest.mark.parametrize('world', ['forms,extract,dataprep,agent'], indirect=True)
CSRF = {"X-Synwevo-CSRF": "fixture-csrf"}
DATA = b"Reference,Amount\n000127,123456789.01\n"


def submitted(w):
    w.apps.enable_for_workspace(w.a, "forms")
    w.apps.enable_for_workspace(w.a, "extract")
    form = w.read("form.create", {"title": "Isolated file handoff", "definition": {"fields": [
        {"id": "f_file0001", "type": "file", "label": "Invoice", "accept": [".csv"], "required": True}]}}, app="forms")
    w.read("form.publish", {"form_id": form["id"], "draft_rev": 1}, app="forms")
    req = w.read("request.create", {"form_id": form["id"]}, app="forms")
    base = "/api/suite/public/forms/r/" + req["url"].rsplit("/", 1)[1]
    sign_in(w, "editor")
    up = w.client.post(base + "/files", data={"field_id": "f_file0001"},
                       files={"file": ("invoice.csv", DATA, "text/csv")}, headers=CSRF)
    assert up.status_code == 200, up.text
    fid = up.json()["id"]
    sub = w.client.post(base + "/submit", json={"version": 1, "answers": {"f_file0001": [fid]},
                                              "idempotency_key": "form-file-submission"}, headers=CSRF)
    assert sub.status_code == 200, sub.text
    return {"submission_id": sub.json()["submission_id"], "file_id": fid}, base


def test_file_handoff_real_parse_review_independent_requests(world):
    w = world
    payload, _ = submitted(w)
    copied = w.invoke("file.from_forms", payload, app="extract", key="handoff")
    assert copied["status"] == "succeeded"
    file = copied["output"]
    assert file["content_digest"] == hashlib.sha256(DATA).hexdigest()
    assert file["source"]["submission_id"] == payload["submission_id"]
    replay = w.invoke("file.from_forms", payload, app="extract", key="handoff")
    assert replay["replayed"] and replay["output"]["id"] == file["id"]
    second = w.read("file.from_forms", payload, app="extract", key="independent-handoff")
    assert second["id"] != file["id"]  # same bytes may be used in separate legitimate runs
    tpl = w.read("template.create", {"title": "Receipt", "definition": {"fields": [
        {"id": "reference", "label": "Reference", "rule": {"kind": "cell", "row": 2, "column": 1}},
        {"id": "amount", "label": "Amount", "type": "decimal", "rule": {"kind": "cell", "row": 2, "column": 2}}]}}, app="extract")
    w.read("template.publish", {"template_id": tpl["id"], "draft_rev": 1}, app="extract")
    task = w.read("task.create", {"template_id": tpl["id"], "version": 1, "file_id": file["id"]}, app="extract", key="parse")
    w.api.state.extract.tick()
    out = w.read("task.get", {"task_id": task["id"]}, app="extract")
    assert out["status"] == "waiting_input", out
    assert out["values"]["reference"]["value"] == "000127"
    assert out["values"]["amount"]["value"] == "123456789.01"
    assert out["values"]["amount"]["candidates"][0]["evidence"]["source"]["row"] == 2
    w.read("task.confirm", {"task_id": task["id"], "revision": 1, "note": "Compared with the authorized fixture"}, app="extract")
    confirmed = w.read("task.result", {"task_id": task["id"], "revision": 2}, app="extract")
    assert confirmed["file"]["source"] == file["source"]
    assert w.store.one("SELECT COUNT(*) AS n FROM extract_files")["n"] == 2
    w.apps.enable_for_workspace(w.a, "dataprep")
    dataset = w.read("dataset.from_extract", {"name": "Reviewed invoice", "tasks": [
        {"task_id": task["id"], "revision": 2}], "field_ids": ["reference", "amount"]}, app="dataprep", key="reviewed-input")
    rows = w.read("dataset.get", {"dataset_id": dataset["id"]}, app="dataprep")
    assert rows["rows"][0]["values"] == {"reference": "000127", "amount": "123456789.01"}
    digest = hashlib.sha256(DATA).hexdigest()
    quarantine.hold(w.store, digest, DATA, signature="isolated-test-verdict", engine="test-injection", rows=[], by="test")
    for operation, args, app in (
        ("task.get", {"task_id": task["id"]}, "extract"),
        ("task.result", {"task_id": task["id"], "revision": 2}, "extract"),
        ("task.segments", {"task_id": task["id"]}, "extract"),
        ("task.review_version", {"task_id": task["id"], "revision": 2}, "extract"),
        ("dataset.get", {"dataset_id": dataset["id"]}, "dataprep"),
    ):
        with pytest.raises(ServiceError) as denied:
            w.read(operation, args, app=app)
        assert denied.value.code == "file_quarantined"


def test_file_handoff_rechecks_scope_and_holds_even_restored_blob(world):
    w = world
    payload, base = submitted(w)
    for who, ws in (("viewer", w.a), ("other", w.b), ("admin", w.a)):
        with pytest.raises(ServiceError):
            w.read("file.from_forms", payload, app="extract", user=who, ws=ws, key=who)
    with pytest.raises(ServiceError) as wrong:
        w.read("file.from_forms", {**payload, "submission_id": "fsub_" + "0" * 12}, app="extract", key="wrong")
    assert wrong.value.status == 404
    copy = w.read("file.from_forms", payload, app="extract", key="copy-before-hold")
    digest = hashlib.sha256(DATA).hexdigest()
    quarantine.hold(w.store, digest, DATA, signature="isolated-test-verdict", engine="test-injection", rows=[], by="test")
    assert w.store.get_blob(digest) == DATA  # crash/restore window deliberately retains bytes
    for path in (base + "/files/" + payload["file_id"],
                 "/api/suite/forms/files/" + payload["file_id"],
                 "/api/suite/extract/files/" + copy["id"]):
        r = w.client.get(path, params={"workspace_id": w.a})
        assert r.status_code == 409 and r.json()["error"] == "file_quarantined", r.text
    for key in ("copy-before-hold", "new-copy-after-hold"):
        with pytest.raises(ServiceError):
            w.read("file.from_forms", payload, app="extract", key=key)
    reup = w.client.post("/api/suite/extract/files", params={"workspace_id": w.a},
                        headers={**CSRF, "Idempotency-Key": "reupload-held"}, files={"file": ("invoice.csv", DATA, "text/csv")})
    assert reup.status_code == 409 and reup.json()["error"] == "file_quarantined"
    assert w.store.one("SELECT COUNT(*) AS n FROM extract_files")["n"] == 1
