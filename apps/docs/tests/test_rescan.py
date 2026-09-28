"""CI-90: rescanning the Synwevo Docs files stored before malware scanning was switched on.

Every test builds a "historical" instance the way production was before 2026-09-28 — files
uploaded while no scanner was configured, so their record says ``not_configured`` and they are
readable — then switches scanning on (the clamd protocol double, `clamd_double.py`) and runs
`python -m synwevo_docs.cli rescan` in-process (one test runs the real entry point).

What is held: a clean verdict changes only the scan record; an infected one rejects every row on
the same bytes and moves the bytes to the quarantine (not deleted); every read path refuses the
rejected source and template; an engine that is down changes nothing; too-large and erroring
files are recorded and left as they were; a digest mismatch changes nothing; the run is
idempotent, resumable in batches, has a dry run; a reviewed false positive can be released and
a confirmed one purged; backups stay restorable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from synwevo_core import quarantine as Q
from synwevo_core.errors import ServiceError

from .clamd_double import EICAR, ClamdDouble
from .helpers import Api

INFECTED_CSV = EICAR + b"\n"
SIGNATURE = "Win.Test.EICAR_HDB-1"


@pytest.fixture()
def double():
    d = ClamdDouble()
    yield d
    d.close()


@pytest.fixture()
def hist(tmp_path, monkeypatch):
    """A Docs instance whose files were stored while scanning was off (production before CI-87)."""
    data = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    monkeypatch.delenv("SYNWEVO_MALWARE_SCAN", raising=False)
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    app = create_app(str(data), start_jobs=False)
    with TestClient(app) as c:
        c.app_state = app.state
        c.data = str(data)
        c.store = app.state.app.store
        c.api = Api(c)
        yield c


def _scanning_on(monkeypatch, double, **extra):
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def _rescan(c, capsys, **kw) -> tuple[int, str]:
    from synwevo_docs.rescan import main_rescan
    args = argparse.Namespace(**({"apply": True, "limit": None, "workspace": None, "only": None} | kw))
    capsys.readouterr()
    code = main_rescan(args, c.data)
    return code, capsys.readouterr().out


def _quarantine(c, capsys, action, sha=None, yes=False) -> tuple[int, str]:
    from synwevo_docs.rescan import main_quarantine
    capsys.readouterr()
    code = main_quarantine(argparse.Namespace(action=action, sha256=sha, yes=yes), c.data)
    return code, capsys.readouterr().out


def _source(c, sid) -> dict:
    return c.store.one("SELECT * FROM source_files WHERE id=?", (sid,))


def _upload(c, pid, name, data) -> dict:
    out = c.api.upload_bytes(pid, name, data)[0]
    assert out["scan"]["status"] == "not_configured"
    return _source(c, out["id"])


def _built_project(c, fixtures) -> tuple[str, dict]:
    """A project that can preview and generate: workbook source, DOCX template with one field
    (so the template has two versions), a rule and a mapping."""
    a = c.api
    pid = a.project("历史项目")
    a.upload(pid, fixtures / "测试_客户100.xlsx")
    t = a.template(pid, fixtures / "测试_合同模板.docx")
    s = a.text_slot(t["id"], "示例客户名称", "客户名称")
    a.ok(c.patch(f"/api/projects/{pid}", json={"template_id": t["id"]}))
    a.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 3}})
    a.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return pid, c.store.one("SELECT * FROM templates WHERE id=?", (t["id"],))


# ---------------------------------------------------------------- clean
def test_clean_historical_files_get_a_clean_record_and_nothing_else_changes(hist, double, monkeypatch, capsys, fixtures):
    c = hist
    pid, tpl = _built_project(c, fixtures)
    src = c.store.one("SELECT * FROM source_files WHERE project_id=?", (pid,))
    assert src["scan"] == {"status": "not_configured"} and tpl["scan"] == {"status": "not_configured"}
    versions = c.store.all("SELECT working_blob FROM template_versions WHERE template_id=?", (tpl["id"],))
    assert len({v["working_blob"] for v in versions} | {tpl["original_blob"]}) == 2      # upload + the version with the field

    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 0, out
    after = _source(c, src["id"])
    assert after["scan"]["status"] == "clean" and after["scan"]["rescan"] is True
    assert after["scan"]["sha256"] == src["blob"] and after["scan"]["previous"] == "not_configured"
    assert after["scan"]["engine"].startswith("ClamAV")
    # the row's state and everything users see stay as they are
    assert {k: after[k] for k in ("status", "error", "version", "updated_at", "blob")} == \
           {k: src[k] for k in ("status", "error", "version", "updated_at", "blob")}
    t2 = c.store.one("SELECT * FROM templates WHERE id=?", (tpl["id"],))
    assert t2["scan"]["status"] == "clean" and t2["scan"]["blobs"] == 2 and t2["updated_at"] == tpl["updated_at"]
    assert len(double.scans) == 3                         # one scan per distinct content
    assert c.get(f"/api/sources/{src['id']}/download").status_code == 200
    assert c.get(f"/api/projects/{pid}/sources").json()[0]["scan"]["status"] == "clean"
    # a second run finds nothing new
    code, out = _rescan(c, capsys)
    assert code == 0 and "没有需要补扫的内容" in out and len(double.scans) == 3


# ---------------------------------------------------------------- infected source
def test_an_infected_historical_source_is_rejected_quarantined_and_refused_everywhere(hist, double, monkeypatch, capsys):
    c = hist
    from synwevo_core.identity import Identity
    uploader = Identity(c.store).create_user("uploader@example.test", "harbor lights guide the evening ferry home", status="active")
    app = c.app_state.app
    p1 = c.api.project("发票")
    p2 = c.api.project("发票副本")
    first = app.add_source(p1, "客户发票-机密.csv", INFECTED_CSV, actor=uploader["id"])
    second = app.add_source(p2, "客户发票-机密.csv", INFECTED_CSV)             # the same bytes, a machine upload
    c.api.wait_parsed(p1)
    c.api.wait_parsed(p2)
    row = _source(c, first["id"])
    sha = row["blob"]
    assert row["status"] == "ready" and row["parsed_blob"]
    assert c.get(f"/api/sources/{row['id']}/download").status_code == 200       # before: readable, as in production

    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1, out
    assert f"sha256={sha}" in out and SIGNATURE in out
    assert "客户发票" not in out and "EICAR-STANDARD" not in out                   # ids and digests, never names or contents
    for sid in (first["id"], second["id"]):
        r = _source(c, sid)
        assert r["status"] == "rejected" and "恶意" in r["error"]
        assert r["scan"]["status"] == "infected" and r["scan"]["signature"] == SIGNATURE
        assert r["scan"]["quarantined"] is True and r["scan"]["rescan"] is True and r["scan"]["sha256"] == sha
    # moved, not deleted
    assert not c.store.has_blob(sha)
    held = Q.bytes_path(c.store, sha)
    assert held.is_file() and hashlib.sha256(held.read_bytes()).hexdigest() == sha
    entry = Q.load(c.store, sha)
    assert entry["state"] == "quarantined" and entry["signature"] == SIGNATURE
    assert {(r["kind"], r["id"]) for r in entry["rows"]} == {("source_files", first["id"]), ("source_files", second["id"])}
    assert all(r["previous"]["status"] == "ready" for r in entry["rows"])
    assert "客户发票" not in json.dumps(entry, ensure_ascii=False)

    # every read path refuses it
    r = c.get(f"/api/sources/{first['id']}/download")
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    view = c.get(f"/api/sources/{first['id']}/view").json()
    assert view["view"] == "error" and view["status"] == "rejected"
    r = c.get(f"/api/sources/{first['id']}/search", params={"q": "X5O"})
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    # the parsed workbook left over from before is not reachable by its id either
    assert c.get(f"/api/preview-files/{row['parsed_blob']}").status_code == 404
    with pytest.raises(ServiceError) as e:
        app.desensitize_sources(p1, {"file_ids": [first["id"]]})
    assert e.value.code == "rejected_malware"
    assert app.desensitize_sources(p1, {})["files"] == []
    # nothing parses it again
    app._parse_source(first["id"])
    assert _source(c, first["id"])["status"] == "rejected"

    # the uploader of the live row is told; the machine upload has nobody to tell
    notes = c.store.all("SELECT kind, object_id, params FROM notifications WHERE user_id=?", (uploader["id"],))
    assert [(n["kind"], n["object_id"]) for n in notes] == [("docs.file_rejected", first["id"])]
    assert "已通知上传者 1 行" in out and "1 行没有上传者记录" in out
    audits = c.store.all("SELECT object_id FROM audit_events WHERE action='source.rescan_rejected'")
    assert {a["object_id"] for a in audits} == {first["id"], second["id"]}

    # the same bytes uploaded again, with scanning on: refused, and not written back into the store
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    live = create_app(c.data, start_jobs=False)
    with TestClient(live) as lc:
        again = lc.post(f"/api/projects/{p1}/sources", files=[("files", ("again.csv", INFECTED_CSV))])
        assert again.status_code == 400 and again.json()["error"] == "malware_detected"
    assert not c.store.has_blob(sha)

    # a backup of this instance is still complete: the withdrawn bytes are not "missing"
    audit = _blob_audit().audit(Path(c.data))
    assert audit["referenced_but_missing"] == [] and sha in audit["withdrawn_absent"]

    # idempotent: the rejected rows are never selected again
    code, out = _rescan(c, capsys)
    assert code == 0 and "没有需要补扫的内容" in out


def _blob_audit():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "blob_audit.py"
    spec = importlib.util.spec_from_file_location("_rescan_blob_audit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- infected template
def test_an_infected_template_is_refused_by_every_read_path(hist, double, monkeypatch, capsys, fixtures):
    c = hist
    pid, tpl = _built_project(c, fixtures)
    tid = tpl["id"]
    head = c.store.one("SELECT working_blob FROM template_versions WHERE template_id=? AND version_no=2", (tid,))["working_blob"]
    assert c.get(f"/api/projects/{pid}/preview", params={"index": 0}).status_code == 200
    # a job created before the verdict (not yet run)
    from synwevo_docs.jobs import JobService
    svc = JobService(c.app_state.app, start=False)
    job = svc.create(pid, {"formats": ["docx"]})
    assert svc._claim(job["id"]) == job["id"]

    # A DOCX cannot carry the EICAR marker at offset 0; the double flags the uploaded file's
    # digest, standing in for a signature that matches it.
    double.infected[tpl["original_blob"]] = "Doc.Test.Template-1"
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys, only="templates")
    assert code == 1, out
    assert f"sha256={tpl['original_blob']}" in out and "模板没有记录上传者" in out
    t = c.store.one("SELECT * FROM templates WHERE id=?", (tid,))
    assert t["scan"]["status"] == "infected" and t["scan"]["signature"] == "Doc.Test.Template-1"
    assert t["scan"]["sha256"] == tpl["original_blob"]
    assert not c.store.has_blob(tpl["original_blob"]) and Q.bytes_path(c.store, tpl["original_blob"]).is_file()
    assert c.store.has_blob(head)                         # only the infected content moved

    def refused(r):
        assert r.status_code == 410 and r.json()["error"] == "rejected_malware", (r.status_code, r.text[:200])

    refused(c.get(f"/api/templates/{tid}"))
    refused(c.get(f"/api/templates/{tid}/view"))
    refused(c.get(f"/api/templates/{tid}/download"))
    refused(c.get(f"/api/templates/{tid}/download", params={"which": "original"}))
    refused(c.post(f"/api/templates/{tid}/duplicate", json={}))
    refused(c.get(f"/api/projects/{pid}/preview", params={"index": 0}))
    with pytest.raises(ServiceError) as e:                # generation: the same service the jobs route calls
        svc.create(pid, {"formats": ["docx"]})
    assert e.value.code == "rejected_malware" and e.value.status == 410
    assert c.get(f"/api/preview-files/{head}").status_code == 404                # the clean version's bytes too
    # the project still opens and says why there is no template
    p = c.get(f"/api/projects/{pid}").json()
    assert p["template"] is None and p["template_rejected"] is True and not p.get("template_missing")
    assert c.get(f"/api/projects/{pid}/records").status_code == 200
    # the job created before the verdict stops instead of filling the template
    with pytest.raises(ServiceError) as e:
        svc._run(job["id"])
    assert e.value.code == "rejected_malware"
    # listed with its verdict; deleted once no project uses it
    listed = next(x for x in c.get("/api/templates").json() if x["id"] == tid)
    assert listed["scan"]["status"] == "infected"
    assert c.delete(f"/api/templates/{tid}").status_code == 409
    c.api.ok(c.patch(f"/api/projects/{pid}", json={"template_id": None}))
    assert c.delete(f"/api/templates/{tid}").status_code in (200, 204)


# ---------------------------------------------------------------- the scanner decides nothing when it is not there
def test_an_engine_that_is_down_at_the_start_changes_nothing(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("停机")
    good = _upload(c, pid, "a.csv", b"a,b\n1,2\n")
    bad = _upload(c, pid, "b.csv", INFECTED_CSV)
    double.down = True
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 3 and "没有需要补扫的内容" not in out
    assert _source(c, good["id"])["scan"] == {"status": "not_configured"}
    assert _source(c, bad["id"])["status"] == "ready" and c.store.has_blob(bad["blob"])
    assert Q.entries(c.store) == []
    assert _rescan(c, capsys, apply=False)[0] == 3                # the preview says a real run would stop
    # scanning off entirely is the same answer
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS")
    assert _rescan(c, capsys)[0] == 3
    assert _source(c, bad["id"])["scan"] == {"status": "not_configured"}


def test_a_file_over_the_scan_limit_is_recorded_and_left_as_it_was(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("超限")
    big = _upload(c, pid, "big.csv", b"a,b\n" + b"1,2\n" * 100)
    _scanning_on(monkeypatch, double, SYNWEVO_CLAMD_MAX_BYTES="64")
    code, out = _rescan(c, capsys)
    assert code == 0 and "超过扫描上限" in out and big["id"] in out
    r = _source(c, big["id"])
    assert r["status"] == "ready" and r["scan"]["status"] == "too_large" and r["scan"]["limit"] == 64
    assert r["scan"]["previous"] == "not_configured" and double.scans == []        # never streamed
    assert c.get(f"/api/sources/{big['id']}/download").status_code == 200
    # same limit, same answer: reported again, nothing rewritten
    code, out = _rescan(c, capsys)
    assert code == 0 and "超过扫描上限" in out and _source(c, big["id"])["scan"] == r["scan"]
    # a raised limit gets it checked
    monkeypatch.setenv("SYNWEVO_CLAMD_MAX_BYTES", str(1 << 20))
    code, out = _rescan(c, capsys)
    assert code == 0 and _source(c, big["id"])["scan"]["status"] == "clean"
    assert _source(c, big["id"])["scan"]["previous"] == "not_configured"


def test_engine_errors_are_recorded_and_retried(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("出错")
    r0 = _upload(c, pid, "a.csv", b"a,b\n1,2\n")
    double.errors.add(r0["blob"])
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1 and "扫描出错" in out
    r = _source(c, r0["id"])
    assert r["status"] == "ready" and r["scan"]["status"] == "error" and r["scan"]["attempts"] == 1
    assert c.get(f"/api/sources/{r0['id']}/download").status_code == 200
    assert _rescan(c, capsys)[0] == 1
    assert _source(c, r0["id"])["scan"]["attempts"] == 2
    double.errors.clear()
    assert _rescan(c, capsys)[0] == 0
    assert _source(c, r0["id"])["scan"]["status"] == "clean"


def test_a_digest_mismatch_is_reported_and_changes_nothing(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("不符")
    r0 = _upload(c, pid, "a.csv", b"a,b\n1,2\n")
    c.store.blob_path(r0["blob"]).write_bytes(EICAR)          # the stored bytes are not what the row recorded
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1 and "摘要不符" in out
    r = _source(c, r0["id"])
    assert r["scan"] == {"status": "not_configured"} and r["status"] == "ready"
    assert double.scans == [] and Q.entries(c.store) == []


def test_dry_run_counts_and_writes_nothing(hist, double, monkeypatch, capsys, fixtures):
    c = hist
    pid = c.api.project("预览")
    a = _upload(c, pid, "a.csv", b"a,b\n1,2\n")
    _upload(c, c.api.project("另一个"), "a-copy.csv", b"a,b\n1,2\n")
    _upload(c, pid, "b.csv", INFECTED_CSV)
    c.api.template(pid, fixtures / "测试_合同模板.docx")
    _scanning_on(monkeypatch, double)
    before = c.store.all("SELECT id, status, scan FROM source_files ORDER BY id")
    code, out = _rescan(c, capsys, apply=False)
    assert code == 0, out
    assert "来源：2 份内容（3 行）" in out and "not_configured 3" in out
    assert "模板：1 个（1 份不同内容）" in out and "扫描器：可用" in out
    assert double.scans == [] and Q.entries(c.store) == []
    assert c.store.all("SELECT id, status, scan FROM source_files ORDER BY id") == before
    assert _source(c, a["id"])["scan"] == {"status": "not_configured"}


def test_limit_batches_and_the_next_run_resumes(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("分批")
    rows = [_upload(c, pid, f"{i}.csv", f"a,b\n{i},2\n".encode()) for i in range(3)]
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys, limit=2)[0] == 0
    assert sum(_source(c, r["id"])["scan"]["status"] == "clean" for r in rows) == 2
    code, out = _rescan(c, capsys, limit=2)
    assert code == 0 and "[1/1]" in out
    assert all(_source(c, r["id"])["scan"]["status"] == "clean" for r in rows)
    assert "没有需要补扫的内容" in _rescan(c, capsys, limit=2)[1]


def test_workspace_filter(hist, double, monkeypatch, capsys):
    c = hist
    r0 = _upload(c, c.api.project("默认空间"), "a.csv", b"a,b\n1,2\n")
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys, workspace="no-such-workspace")[0] == 2
    code, out = _rescan(c, capsys, workspace="default")
    assert code == 0 and _source(c, r0["id"])["scan"]["status"] == "clean"


# ---------------------------------------------------------------- after review
def test_a_reviewed_false_positive_is_released_and_a_confirmed_one_purged(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("复核")
    fp = _upload(c, pid, "fp.csv", INFECTED_CSV)
    real = _upload(c, pid, "real.csv", EICAR + b"\nsecond\n")
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    assert _source(c, fp["id"])["status"] == "rejected" and _source(c, real["id"])["status"] == "rejected"

    code, out = _quarantine(c, capsys, "list")
    assert code == 0 and fp["blob"] in out and real["blob"] in out and "source_files 1" in out
    # the engine still flags it: no release
    code, out = _quarantine(c, capsys, "release", fp["blob"])
    assert code == 1 and "签名撤回之前不能恢复" in out
    assert _source(c, fp["id"])["status"] == "rejected" and not c.store.has_blob(fp["blob"])
    # the signature was withdrawn after the false-positive report: released exactly as it was
    double.eicar_withdrawn = True
    code, out = _quarantine(c, capsys, "release", fp["blob"])
    assert code == 0, out
    r = _source(c, fp["id"])
    assert r["status"] == "ready" and r["error"] is None
    assert r["scan"]["status"] == "clean" and r["scan"]["released"] is True and r["scan"]["previous"] == "not_configured"
    assert c.store.has_blob(fp["blob"]) and c.get(f"/api/sources/{fp['id']}/download").status_code == 200
    entry = Q.load(c.store, fp["blob"])
    assert entry["state"] == "released" and not Q.bytes_path(c.store, fp["blob"]).exists()
    assert c.store.one("SELECT 1 AS x FROM audit_events WHERE action='quarantine.released' AND object_id=?", (fp["blob"],))

    # purge needs --yes; then the bytes are gone for good and the rows stay rejected
    assert _quarantine(c, capsys, "purge", real["blob"])[0] == 2
    assert Q.bytes_path(c.store, real["blob"]).is_file()
    code, out = _quarantine(c, capsys, "purge", real["blob"], yes=True)
    assert code == 0 and not Q.bytes_path(c.store, real["blob"]).exists()
    assert Q.load(c.store, real["blob"])["state"] == "purged"
    assert _source(c, real["id"])["status"] == "rejected"
    assert c.get(f"/api/sources/{real['id']}/download").status_code == 410
    assert _quarantine(c, capsys, "release", real["blob"])[0] == 2        # nothing left to release
    assert _blob_audit().audit(Path(c.data))["referenced_but_missing"] == []


def test_a_regression_case_holding_the_bytes_moves_aside_and_comes_back(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("回归")
    src = _upload(c, pid, "a.csv", INFECTED_CSV)
    # TEST FIXTURE: a captured regression case on disk (regression.py's layout), holding a copy
    case = Path(c.data) / "regression" / "default" / "20260901-000000-abcdef"
    (case / "sources").mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"id": case.name, "name": "历史用例", "workspace": "default",
                                                "sources": [{"id": src["id"], "filename": "a.csv", "file": "sources/01_a.csv"}]}),
                                    encoding="utf-8")
    (case / "sources" / "01_a.csv").write_bytes(INFECTED_CSV)
    from synwevo_docs.regression import list_cases
    assert [x["id"] for x in list_cases("default", c.data)] == [case.name]
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1 and f"default/{case.name}" in out
    assert not case.exists() and list_cases("default", c.data) == []
    assert (Q.aside_dir(c.store, src["blob"]) / "regression" / "default" / case.name / "case.json").is_file()
    double.eicar_withdrawn = True
    assert _quarantine(c, capsys, "release", src["blob"])[0] == 0
    assert (case / "sources" / "01_a.csv").read_bytes() == INFECTED_CSV
    assert [x["id"] for x in list_cases("default", c.data)] == [case.name]


# ---------------------------------------------------------------- the real entry point
def test_the_command_line_entry_point(hist, double, tmp_path):
    c = hist
    _upload(c, c.api.project("命令行"), "a.csv", b"a,b\n1,2\n")
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNWEVO_")}
    env.update({"SYNWEVO_CLAMD_ADDRESS": double.address, "SYNWEVO_CLAMD_TIMEOUT_S": "5"})
    run = lambda *a: subprocess.run([sys.executable, "-m", "synwevo_docs.cli", "--data", c.data, *a],  # noqa: E731
                                    env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    dry = run("rescan")                                        # without --apply: a preview
    assert dry.returncode == 0, dry.stderr
    assert "来源：1 份内容（1 行）" in dry.stdout and "--apply" in dry.stdout
    real = run("rescan", "--apply")
    assert real.returncode == 0, real.stderr + real.stdout
    assert "干净 1" in real.stdout and "退出码：0" in real.stdout
    listed = run("quarantine", "list")
    assert listed.returncode == 0 and "隔离区是空的" in listed.stdout


# ---------------------------------------------------------------- review fixes (CI-90 independent review)
def _completed_job(c, pid) -> tuple[dict, dict]:
    """A job that ran to its end before the verdict, and one of its artifacts."""
    from synwevo_docs.jobs import JobService
    svc = JobService(c.app_state.app, start=False)
    job = svc.create(pid, {"formats": ["docx"]})
    assert svc._claim(job["id"]) == job["id"]
    svc._run(job["id"])
    art = c.store.one("SELECT * FROM artifacts WHERE job_id=? AND deleted_at IS NULL LIMIT 1", (job["id"],))
    assert art, "the fixture job produced no artifact"
    return svc, job, art


@pytest.mark.parametrize("what", ["template", "source"])
def test_outputs_made_from_an_input_a_rescan_rejects_are_refused(hist, double, monkeypatch, capsys, fixtures, what):
    """Review finding 1 (high): a generated file is the template's package filled with the
    sources' values; once either input is rejected its outputs, archive and links are refused,
    and a preview filled from a rejected template is no longer served."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    src = c.store.one("SELECT * FROM source_files WHERE project_id=?", (pid,))
    pblob = c.get(f"/api/projects/{pid}/preview", params={"index": 0}).json()["output"]["blob"]
    assert c.get(f"/api/preview-files/{pblob}").status_code == 200
    svc, job, art = _completed_job(c, pid)
    # This instance runs no job service (start_jobs=False): the artifact and archive routes are
    # `svc.artifact` / `svc.zip_file`, called here directly.
    assert svc.artifact(art["id"])[1] and svc.zip_file(job["id"]).is_file()

    bad = tpl["original_blob"] if what == "template" else src["blob"]
    double.infected[bad] = "Doc.Test.Input-1"
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1, out
    if what == "source":
        assert job["id"] in out                              # the summary names the jobs that used the bytes
    for call in (lambda: svc.artifact(art["id"]), lambda: svc.zip_file(job["id"])):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 410 and e.value.code == "rejected_malware"
    from synwevo_docs import authz
    assert not authz.blob_readable(c.store, art["blob"], "default")
    from synwevo_docs.api.v1 import _artifact_meta
    with pytest.raises(ServiceError) as e:                # no machine-API download link is minted either
        _artifact_meta(c.store, art["id"])
    assert e.value.code == "rejected_malware"
    assert c.get(f"/api/preview-files/{pblob}").status_code == 404     # a preview is filled from both

def test_a_standing_verdict_holds_before_docs_marks_its_rows_and_when_the_bytes_come_back(hist, double, monkeypatch, capsys):
    """Review findings 4, 5 and 8: another application's rescan (or a restored backup, or a crash
    between marking and moving) can leave a Docs row unmarked on bytes whose verdict stands, and
    the bytes can even be back in the store. The verdict — not the absence of the bytes — refuses
    every read and every new copy; the next Docs rescan marks the rows and takes the copy out."""
    c = hist
    app = c.app_state.app
    pid = c.api.project("另一应用先判定")
    row = _upload(c, pid, "a.csv", INFECTED_CSV)
    c.api.wait_parsed(pid)
    row = _source(c, row["id"])
    sha = row["blob"]
    assert c.get(f"/api/sources/{row['id']}/download").status_code == 200
    assert c.get(f"/api/sources/{row['id']}/search", params={"q": "X5O"}).status_code == 200     # cached in this process
    # Synwevo Message's rescan found the bytes first: its rows are held, no Docs row is marked
    Q.hold(c.store, sha, INFECTED_CSV, signature=SIGNATURE, engine="ClamAV fixture",
           rows=[{"kind": "message_attachments", "id": "att_fixture", "workspace": None,
                  "previous": {"status": "accepted", "scan": None}}], by="test")
    Q.remove_from_store(c.store, sha)
    assert _source(c, row["id"])["status"] == "ready"
    for r in (c.get(f"/api/sources/{row['id']}/download"), c.get(f"/api/sources/{row['id']}/search", params={"q": "X5O"})):
        assert r.status_code == 410 and r.json()["error"] == "rejected_malware", (r.status_code, r.text[:200])
    assert c.get(f"/api/preview-files/{row['parsed_blob']}").status_code == 404
    # the bytes come back into the store (a restored backup): still refused
    c.store.put_blob(INFECTED_CSV)
    assert c.get(f"/api/sources/{row['id']}/download").status_code == 410
    # and never stored again under a new row or template, with scanning off as well
    for call in (lambda: app.add_source(pid, "again.csv", INFECTED_CSV), lambda: app.upload_template("again.docx", INFECTED_CSV)):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.code == "malware_detected" and e.value.detail.get("earlier") is True
    assert c.store.one("SELECT COUNT(*) AS n FROM source_files")["n"] == 1

    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1 and "已从存储移走" in out and "已有隔离结论" in out, out
    assert _source(c, row["id"])["status"] == "rejected" and not c.store.has_blob(sha)
    assert {(r["kind"], r["id"]) for r in Q.pending(Q.load(c.store, sha))} == {
        ("message_attachments", "att_fixture"), ("source_files", row["id"])}


def test_a_completed_resumable_upload_of_judged_bytes_is_refused(hist, double, monkeypatch, capsys):
    """Review finding 5: finishing a v1 resumable upload used to write the bytes back into the
    store before anything looked at them."""
    c = hist
    pid = c.api.project("断点上传")
    row = _upload(c, pid, "a.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    sha = row["blob"]
    from synwevo_docs.api import v1_uploads
    # TEST FIXTURE: an upload row completed before the verdict (the state `use_upload` meets)
    c.store.insert("uploads", {"id": "up_0123456789ab", "tenant": "default", "purpose": "source", "filename": "a.csv",
                               "status": "complete", "blob": sha, "sha256": sha, "size": len(INFECTED_CSV),
                               "received": len(INFECTED_CSV), "created_at": 1, "updated_at": 1, "expires_at": 4102444800})
    with pytest.raises(ServiceError) as e:
        v1_uploads.use_upload(c.store, "up_0123456789ab", lambda b, name: pytest.fail("the bytes were used"), "source")
    assert e.value.code == "malware_detected"
    assert not c.store.has_blob(sha)


def test_purge_deletes_only_the_held_bytes_never_a_regression_case(hist, double, monkeypatch, capsys):
    """Review finding 2: a regression case moved aside holds other files too; a purge keeps it."""
    c = hist
    pid = c.api.project("回归清除")
    src = _upload(c, pid, "a.csv", INFECTED_CSV)
    case = Path(c.data) / "regression" / "default" / "20260902-000000-abcdef"      # TEST FIXTURE
    (case / "sources").mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"id": case.name, "workspace": "default", "sources": []}), encoding="utf-8")
    (case / "sources" / "01_a.csv").write_bytes(INFECTED_CSV)
    (case / "sources" / "02_other.csv").write_bytes(b"a,b\n1,2\n")
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    aside = Q.aside_dir(c.store, src["blob"]) / "regression" / "default" / case.name
    assert (aside / "sources" / "02_other.csv").is_file()
    code, out = _quarantine(c, capsys, "purge", src["blob"])
    assert code == 2 and "回归用例不会被删除" in out and f"default/{case.name}" in out
    code, out = _quarantine(c, capsys, "purge", src["blob"], yes=True)
    assert code == 0 and not Q.bytes_path(c.store, src["blob"]).exists()
    assert (aside / "sources" / "02_other.csv").read_bytes() == b"a,b\n1,2\n" and (aside / "case.json").is_file()


def test_release_looks_at_every_other_content_of_the_template_again(hist, double, monkeypatch, capsys, fixtures):
    """Review finding 6: releasing the verdict on one content must not mark the whole template
    clean when another content was never scanned (an engine error at the time)."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    head = c.store.one("SELECT working_blob FROM template_versions WHERE template_id=? AND version_no=2", (tpl["id"],))["working_blob"]
    double.infected[tpl["original_blob"]] = "Doc.Test.Template-1"
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys, only="templates")[0] == 1
    assert c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]["status"] == "infected"
    del double.infected[tpl["original_blob"]]            # the signature is withdrawn...
    double.errors.add(head)                               # ...and the other content errors on the release scan
    code, out = _quarantine(c, capsys, "release", tpl["original_blob"])
    assert code == 0, out
    rec = c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]
    assert rec["status"] == "error" and rec["released"] is True           # not "clean": the next rescan looks again
    assert c.get(f"/api/templates/{tpl['id']}").status_code == 200        # usable as it was before the rescan
    double.errors.clear()
    assert _rescan(c, capsys, only="templates")[0] == 0
    assert c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]["status"] == "clean"


def test_a_template_under_two_verdicts_stays_rejected_until_both_are_released(hist, double, monkeypatch, capsys, fixtures):
    """Review finding 9: releasing one verdict releases only the rows it restored."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    head = c.store.one("SELECT working_blob FROM template_versions WHERE template_id=? AND version_no=2", (tpl["id"],))["working_blob"]
    double.infected[tpl["original_blob"]] = "Doc.Test.First-1"
    double.infected[head] = "Doc.Test.Second-1"
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys, only="templates")[0] == 1
    for sha in (tpl["original_blob"], head):              # each verdict lists the template
        assert ("templates", tpl["id"]) in {(r["kind"], r["id"]) for r in Q.pending(Q.load(c.store, sha))}
    del double.infected[tpl["original_blob"]]
    code, out = _quarantine(c, capsys, "release", tpl["original_blob"])
    assert code == 1 and "仍被其他隔离结论拒绝" in out, out     # released, but nothing came back: look
    rec = c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]
    assert rec["status"] == "infected" and rec["sha256"] == head
    assert c.get(f"/api/templates/{tpl['id']}").status_code == 410
    del double.infected[head]
    assert _quarantine(c, capsys, "release", head)[0] == 0
    assert c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]["status"] == "clean"
    assert c.get(f"/api/templates/{tpl['id']}").status_code == 200


def test_a_scan_in_flight_does_not_overwrite_a_rescan_rejection(hist, double, monkeypatch, capsys):
    """Review finding 11: the server's own scan or retry of a row finishing after the rescan
    rejected it must not turn it back into an error or a parse."""
    c = hist
    app = c.app_state.app
    pid = c.api.project("并发")
    row = _upload(c, pid, "a.csv", INFECTED_CSV)
    stale = _source(c, row["id"])
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    app._publish_scan(stale, {"status": "error", "reason": "fixture"}, "scan_failed", None)
    after = _source(c, row["id"])
    assert after["status"] == "rejected" and after["scan"]["status"] == "infected"


def test_limit_is_not_starved_by_files_over_the_scan_limit(hist, double, monkeypatch, capsys):
    """Review finding 13: files that cannot be scanned sort last, so `--limit N` batches reach
    the rows that can."""
    c = hist
    pid = c.api.project("超限与出错")
    big = [_upload(c, pid, f"big{i}.csv", b"a,b\n" + f"{i},2\n".encode() * 40) for i in range(2)]
    small = _upload(c, pid, "small.csv", b"a,b\n1,2\n")
    double.errors.add(small["blob"])
    _scanning_on(monkeypatch, double, SYNWEVO_CLAMD_MAX_BYTES="64")
    assert _rescan(c, capsys)[0] == 1                     # big: too_large; small: error
    double.errors.clear()
    code, out = _rescan(c, capsys, limit=1)
    assert code == 0 and small["id"] in out, out
    assert _source(c, small["id"])["scan"]["status"] == "clean"
    assert all(_source(c, b["id"])["scan"]["status"] == "too_large" for b in big)


def test_the_backup_audit_excuses_only_references_that_are_themselves_rejected(hist, double, monkeypatch, capsys, fixtures):
    """Review finding 10: a missing digest is 'withdrawn' only when every row naming it is
    rejected, and only while the held copy exists; otherwise it is loss."""
    c = hist
    pid = c.api.project("审计")
    row = _upload(c, pid, "a.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    audit = _blob_audit()
    r = audit.audit(Path(c.data))
    assert r["referenced_but_missing"] == [] and row["blob"] in r["withdrawn_absent"] and r["quarantine_copy_missing"] == []
    # TEST FIXTURE: a live template row naming the same digest (a reference nobody rejected)
    c.store.insert("templates", {"id": "t_fixture", "tenant": "default", "name": "fixture", "kind": "uploaded", "format": "docx",
                                 "original_blob": row["blob"], "original_name": "f.docx", "head": 1, "created_at": 1,
                                 "updated_at": 1, "info": {}, "scan": {"status": "clean"}})
    r = audit.audit(Path(c.data))
    assert row["blob"] in r["referenced_but_missing"]
    c.store.execute("DELETE FROM templates WHERE id='t_fixture'")
    Q.bytes_path(c.store, row["blob"]).unlink()           # the held copy is lost
    r = audit.audit(Path(c.data))
    assert r["quarantine_copy_missing"] == [row["blob"]] and row["blob"] in r["referenced_but_missing"]


def test_a_backup_carries_the_quarantine_and_a_restore_brings_it_back(hist, double, monkeypatch, capsys, tmp_path):
    """Review finding 3: the held bytes and their index travel with the database, so a reviewed
    false positive survives the loss of the host."""
    c = hist
    pid = c.api.project("备份")
    row = _upload(c, pid, "a.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    import importlib.util
    spec = importlib.util.spec_from_file_location("_rescan_snapshot", Path(__file__).resolve().parents[1] / "scripts" / "snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    files = {p.relative_to(Path(c.data) / "quarantine").as_posix() for p in snap._quarantine_files(Path(c.data))}
    assert files == {row["blob"], f"{row['blob']}.json"}                 # no lock files, no temporaries
    staging, into = tmp_path / "staging" / "quarantine", tmp_path / "into" / "quarantine"
    for f in snap._quarantine_files(Path(c.data)):         # what the archive carries, unpacked
        (staging / f.name).parent.mkdir(parents=True, exist_ok=True)
        (staging / f.name).write_bytes(f.read_bytes())
    assert snap._merge_quarantine(staging, into) == 2
    assert Q.read(type("S", (), {"root": str(into.parent)})(), row["blob"]) == INFECTED_CSV
    assert snap._quarantine_member_ok(("quarantine", row["blob"])) and not snap._quarantine_member_ok(("quarantine", "x.sh"))


def test_regression_case_files_no_row_vouches_for_are_scanned(hist, double, monkeypatch, capsys):
    """Review finding 12: a case keeps verbatim copies after the source row is removed; those
    copies are downloadable with the case, so the rescan looks at them from the case itself."""
    c = hist
    pid = c.api.project("回归来源已移除")
    src = _upload(c, pid, "a.csv", INFECTED_CSV)
    clean = _upload(c, pid, "b.csv", b"a,b\n1,2\n")
    case = Path(c.data) / "regression" / "default" / "20260903-000000-abcdef"       # TEST FIXTURE
    (case / "sources").mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"id": case.name, "workspace": "default", "sources": []}), encoding="utf-8")
    (case / "sources" / "01_a.csv").write_bytes(INFECTED_CSV)
    (case / "sources" / "02_b.csv").write_bytes(b"a,b\n1,2\n")
    c.api.ok(c.delete(f"/api/sources/{src['id']}"))        # the row is removed; the case still holds the bytes
    c.store.blob_path(src["blob"]).unlink(missing_ok=True)
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys, apply=False)
    assert "回归用例：1 个用例里 2 份" in out, out
    code, out = _rescan(c, capsys)
    assert code == 1 and f"default/{case.name}" in out and f"sha256={src['blob']}" in out, out
    assert not case.exists()
    assert (Q.aside_dir(c.store, src["blob"]) / "regression" / "default" / case.name / "sources" / "02_b.csv").is_file()
    assert Q.bytes_path(c.store, src["blob"]).read_bytes() == INFECTED_CSV
    # the other file's row is clean now; a new case holding only it is vouched for and not scanned
    assert _source(c, clean["id"])["scan"]["status"] == "clean"
    other = Path(c.data) / "regression" / "default" / "20260903-000001-abcdef"
    (other / "sources").mkdir(parents=True)
    (other / "case.json").write_text(json.dumps({"id": other.name, "workspace": "default", "sources": []}), encoding="utf-8")
    (other / "sources" / "01_b.csv").write_bytes(b"a,b\n1,2\n")
    scans = len(double.scans)
    code, out = _rescan(c, capsys)
    assert code == 0 and len(double.scans) == scans and other.exists(), out


# ---------------------------------------------------------------- second review
def test_release_purge_and_rescan_of_either_application_never_run_at_once(hist, double, monkeypatch, capsys):
    """Second review, HIGH-1: a rescan of one application takes every store copy of a standing
    digest out (settle) — beside a release of the other it removed the file just restored. One
    lock covers every rescan --apply, release and purge of both applications."""
    c = hist
    pid = c.api.project("互斥")
    src = _upload(c, pid, "a.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    double.eicar_withdrawn = True
    from synwevo_core import rescan as RS
    with RS.run_lock(c.store, "messages"):                   # Synwevo Message is running its rescan
        code, _ = _quarantine(c, capsys, "release", src["blob"])
        assert code == RS.EXIT_USAGE
        assert _quarantine(c, capsys, "purge", src["blob"], yes=True)[0] == RS.EXIT_USAGE
        assert _rescan(c, capsys)[0] == RS.EXIT_USAGE
    assert _source(c, src["id"])["status"] == "rejected" and Q.bytes_path(c.store, src["blob"]).is_file()
    assert _quarantine(c, capsys, "release", src["blob"])[0] == 0
    assert c.store.has_blob(src["blob"]) and _source(c, src["id"])["status"] != "rejected"


def test_a_clean_row_on_bytes_the_other_application_judged_is_marked_and_its_project_keeps_working(hist, double, monkeypatch, capsys, fixtures):
    """Second review, HIGH-2: Docs recorded the bytes clean, a later signature made Synwevo
    Message's rescan quarantine them. The project's other files keep working (the row is left
    out, not read), and the next Docs rescan selects the row despite its clean record."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    extra = _upload(c, pid, "b.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    double.eicar_withdrawn = True                            # at the time, the engine said clean
    assert _rescan(c, capsys)[0] == 0
    assert _source(c, extra["id"])["scan"]["status"] == "clean"
    # a signature update later; Synwevo Message's rescan finds the same bytes first
    Q.hold(c.store, extra["blob"], INFECTED_CSV, signature=SIGNATURE, engine="ClamAV fixture",
           rows=[{"kind": "message_attachments", "id": "att_fixture", "workspace": None,
                  "previous": {"status": "accepted", "scan": None}}], by="test")
    Q.remove_from_store(c.store, extra["blob"])
    assert c.get(f"/api/projects/{pid}/records").status_code == 200           # the workbook still drives the project
    assert c.get(f"/api/projects/{pid}/preview", params={"index": 0}).status_code == 200
    assert c.get(f"/api/sources/{extra['id']}/download").status_code == 410
    code, out = _rescan(c, capsys, apply=False)
    assert "来源：1 份内容（1 行）" in out, out
    code, out = _rescan(c, capsys)
    assert code == 1 and "已有隔离结论" in out, out
    assert _source(c, extra["id"])["status"] == "rejected"
    assert Q.unmarked_references(c.store, extra["blob"])["docs"] == 0


def test_a_desensitized_copy_goes_with_its_own_source_only(hist, double, monkeypatch, capsys):
    """Second review, MEDIUM-1: a desensitized copy names its source; rejecting one source leaves
    the copies of the other files readable."""
    c = hist
    app = c.app_state.app
    pid = c.api.project("脱敏")
    good = _upload(c, pid, "good.csv", "姓名,电话\n张三,13800000000\n".encode())
    bad = _upload(c, pid, "bad.csv", INFECTED_CSV)
    c.api.wait_parsed(pid)
    out = app.desensitize_sources(pid, {"file_ids": [good["id"]]})
    copy = out["files"][0]["blob"]
    from synwevo_docs import authz
    assert authz.blob_readable(c.store, copy, "default")
    assert c.store.one("SELECT kind FROM blob_grants WHERE blob=?", (copy,))["kind"] == f"desensitized:{good['id']}"
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1 and _source(c, bad["id"])["status"] == "rejected"
    assert authz.blob_readable(c.store, copy, "default")
    # TEST FIXTURE: a copy of the rejected file itself, as desensitize would have granted it
    authz.grant_blob(c.store, "f" * 64, f"desensitized:{bad['id']}", "default")
    assert not authz.blob_readable(c.store, "f" * 64, "default")


def test_limit_counts_scans_so_missing_files_never_block_a_batch(hist, double, monkeypatch, capsys):
    """Second review, MEDIUM-2: units that need no scan (missing bytes) do not use up --limit."""
    c = hist
    pid = c.api.project("缺失与分批")
    gone = [_upload(c, pid, f"gone{i}.csv", f"a,b\n{i},9\n".encode()) for i in range(2)]
    for g in gone:
        c.store.blob_path(g["blob"]).unlink()
    bad = _upload(c, pid, "bad.csv", INFECTED_CSV)
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys, limit=1)
    assert code == 1 and _source(c, bad["id"])["status"] == "rejected", out
    assert "内容不在存储中" in out and len(double.scans) == 1


def test_the_standing_set_follows_every_index_write_even_on_a_coarse_clock(hist):
    """Second review, MEDIUM-3: the servers' cached set of standing verdicts is keyed on the
    index's generation counter, not only on the directory's timestamp."""
    import os
    import time as _t
    c = hist
    sha = hashlib.sha256(INFECTED_CSV).hexdigest()
    c.store.put_blob(INFECTED_CSV)
    Q.hold(c.store, sha, INFECTED_CSV, signature=SIGNATURE, engine="fixture",
           rows=[{"kind": "source_files", "id": "s_fixture", "workspace": None, "previous": {}}], by="test")
    d = Q.directory(c.store)
    old = _t.time() - 60
    os.utime(d, (old, old))                                 # not "fresh": the set is cached
    assert sha in Q.standing(c.store)
    entry = Q.load(c.store, sha)
    entry["state"] = "released"                             # TEST FIXTURE: a release written by another process
    Q._write_entry(c.store, sha, entry)
    os.utime(d, (old, old))                                 # the same timestamp as the cached one
    assert sha not in Q.standing(c.store)


def test_a_job_s_inputs_are_read_without_decoding_its_whole_snapshot(hist, fixtures):
    """Second review, MEDIUM-4: artifact refusals read only ids and digests of a snapshot."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    svc, job, art = _completed_job(c, pid)
    from synwevo_docs.jobs import snapshot_inputs
    full = c.store.one("SELECT snapshot FROM jobs WHERE id=?", (job["id"],))["snapshot"]
    light = snapshot_inputs(c.store, job["id"])
    assert light["template"]["id"] == full["template"]["id"] == tpl["id"]
    assert light["template"]["working_blob"] == full["template"]["working_blob"]
    assert [(s["id"], s["blob"]) for s in light["sources"]] == [(s["id"], s["blob"]) for s in full["sources"]]


def test_a_case_file_scanned_clean_is_not_scanned_again(hist, double, monkeypatch, capsys):
    """Second review, MEDIUM-2: a case file with no row has its verdict kept by digest, so later
    runs (and --limit batches) move on to other files."""
    c = hist
    case = Path(c.data) / "regression" / "default" / "20260904-000000-abcdef"      # TEST FIXTURE
    (case / "sources").mkdir(parents=True)
    (case / "case.json").write_text(json.dumps({"id": case.name, "workspace": "default", "sources": []}), encoding="utf-8")
    (case / "sources" / "01_x.csv").write_bytes(b"x,y\n1,2\n")
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 0 and len(double.scans) == 1
    code, out = _rescan(c, capsys)
    assert code == 0 and len(double.scans) == 1 and "没有需要补扫的内容" in out, out


# ---------------------------------------------------------------- third review
def test_a_re_upload_judged_malicious_holds_the_earlier_source_s_bytes(hist, double, monkeypatch, capsys):
    """Third review, HIGH: the same file uploaded again after scanning was switched on, and
    flagged, used to take the earlier (historical) source's only copy with it. The upload gate
    now holds the bytes, recording the earlier row as it was, so a release brings it back."""
    c = hist
    app = c.app_state.app
    pid = c.api.project("重新上传")
    from synwevo_core.identity import Identity
    uploader = Identity(c.store).create_user("earlier@example.test", "harbor lights guide the evening ferry home", status="active")
    old = _source(c, app.add_source(pid, "a.csv", INFECTED_CSV, actor=uploader["id"])["id"])
    c.api.wait_parsed(pid)
    assert _source(c, old["id"])["status"] == "ready"
    _scanning_on(monkeypatch, double)
    from synwevo_core import malware as MW
    app.scan_policy = MW.policy()                           # TEST SETUP: the server restarted with scanning on
    app.scanner = MW.scanner_for(app.scan_policy)
    new = c.post(f"/api/projects/{pid}/sources", files=[("files", ("again.csv", INFECTED_CSV))]).json()[0]
    import time as _t
    sha = old["blob"]
    deadline = _t.time() + 30                               # the gate marks the rows first, then moves the bytes
    while _t.time() < deadline and not (Q.load(c.store, sha) and not c.store.has_blob(sha)):
        _t.sleep(0.1)
    assert _source(c, new["id"])["status"] == "rejected" and _source(c, old["id"])["status"] == "rejected"
    assert not c.store.has_blob(sha) and Q.bytes_path(c.store, sha).read_bytes() == INFECTED_CSV     # held, not deleted
    prev = {r["id"]: r["previous"]["status"] for r in Q.load(c.store, sha)["rows"]}
    assert prev == {old["id"]: "ready", new["id"]: "scanning"}
    # the earlier copy's uploader is told it was withdrawn, as after a rescan
    notes = c.store.all("SELECT kind, object_id FROM notifications WHERE user_id=?", (uploader["id"],))
    assert [(n["kind"], n["object_id"]) for n in notes] == [("docs.file_rejected", old["id"])]
    double.eicar_withdrawn = True                           # a reviewed false positive
    assert _quarantine(c, capsys, "release", sha)[0] == 0
    assert _source(c, old["id"])["status"] == "ready" and c.get(f"/api/sources/{old['id']}/download").status_code == 200


def test_limit_counts_scans_that_fail_too(hist, double, monkeypatch, capsys):
    """Third review, MEDIUM: a timeout or an engine error is a scan sent; it counts toward --limit,
    so a batch over failing files is still bounded."""
    c = hist
    pid = c.api.project("出错分批")
    rows = [_upload(c, pid, f"{i}.csv", f"a,b\n{i},3\n".encode()) for i in range(3)]
    for r in rows:
        double.errors.add(r["blob"])
    _scanning_on(monkeypatch, double)
    code, out = _rescan(c, capsys, limit=2)
    assert code == 1 and len(double.scans) == 2 and "还有 1 份留待下次运行" in out, out


def test_a_template_held_by_two_verdicts_from_two_applications_comes_back_after_both_releases(hist, double, monkeypatch, capsys, fixtures):
    """Third review, LOW: rejected for one content by Docs, the template was never listed under a
    verdict another application later made on its other content; releasing the first now lists it
    under the second, whose release then brings it back."""
    c = hist
    pid, tpl = _built_project(c, fixtures)
    head = c.store.one("SELECT working_blob FROM template_versions WHERE template_id=? AND version_no=2", (tpl["id"],))["working_blob"]
    double.infected[tpl["original_blob"]] = "Doc.Test.First-1"
    _scanning_on(monkeypatch, double)
    assert _rescan(c, capsys, only="templates")[0] == 1
    # the other content judged later by Synwevo Message (TEST FIXTURE: its verdict and row)
    Q.hold(c.store, head, c.store.get_blob(head), signature="Doc.Test.Second-1", engine="ClamAV fixture",
           rows=[{"kind": "message_attachments", "id": "att_fixture", "workspace": None, "previous": {"status": "accepted", "scan": None}}],
           by="test")
    Q.remove_from_store(c.store, head)
    del double.infected[tpl["original_blob"]]
    assert _quarantine(c, capsys, "release", tpl["original_blob"])[0] == 1           # still held by the other verdict
    assert ("templates", tpl["id"]) in {(r["kind"], r["id"]) for r in Q.pending(Q.load(c.store, head))}
    assert _quarantine(c, capsys, "release", head)[0] == 0
    assert c.store.one("SELECT scan FROM templates WHERE id=?", (tpl["id"],))["scan"]["status"] == "clean"
    assert c.get(f"/api/templates/{tpl['id']}").status_code == 200
