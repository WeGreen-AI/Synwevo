"""Cleanup must act on evidence, never on a name.

The real database has 68 projects created before provenance existed, so they carry no
origin at all.  Three test-NAMED projects were edited by the user hours after any script
ran.  A tool that deleted by name prefix would therefore destroy real work, which is why
`unknown` is never eligible for automatic action and dry-run is the default.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from docweave.services import App
from docweave.store import Store, now

ROOT = Path(__file__).resolve().parents[2]
MANAGE = ROOT / "backend" / "manage.py"


def _run(data_dir, *args):
    r = subprocess.run([sys.executable, str(MANAGE), "--data", str(data_dir), "projects", *args],
                       capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout + r.stderr


@pytest.fixture()
def seeded(tmp_path):
    d = tmp_path / "data"
    store = Store(d)
    App(store)
    t = now()
    rows = [
        ("p_test", "UI验收：客户合同", "test:run-abc", t, t),          # provably a test run
        ("p_ui", "真实项目", "ui", t, t),                              # created in the UI
        ("p_unknown", "UI验收：订单分组", None, t, t),                  # pre-v3: no evidence at all
        ("p_edited", "UI验收：被改过的", None, t, t + 4000),            # touched by a human later
    ]
    for pid, name, origin, created, updated in rows:
        store.execute("INSERT INTO projects (id, name, origin, created_at, updated_at) VALUES (?,?,?,?,?)",
                      (pid, name, origin, created, updated))
    return d, store


def test_list_classifies_on_evidence_not_on_name(seeded):
    d, _ = seeded
    code, out = _run(d, "list")
    assert code == 0, out
    assert "可自动处理 1" in out, out          # only the test:-tagged one
    assert "来源不明 1" in out, out            # p_unknown
    assert "明确保留 2" in out, out            # ui + edited-after-create


def test_prune_never_auto_selects_unknown_origin(seeded):
    d, store = seeded
    code, out = _run(d, "prune", "--apply")
    assert code == 0, out
    assert store.one("SELECT archived_at FROM projects WHERE id='p_unknown'")["archived_at"] is None
    assert store.one("SELECT archived_at FROM projects WHERE id='p_edited'")["archived_at"] is None
    assert store.one("SELECT archived_at FROM projects WHERE id='p_ui'")["archived_at"] is None
    assert store.one("SELECT archived_at FROM projects WHERE id='p_test'")["archived_at"] is not None


def test_dry_run_is_the_default_and_changes_nothing(seeded):
    d, store = seeded
    code, out = _run(d, "prune")
    assert code == 0 and "dry-run" in out, out
    assert store.one("SELECT archived_at FROM projects WHERE id='p_test'")["archived_at"] is None


def test_deleting_requires_an_explicit_acknowledgement(seeded):
    d, store = seeded
    code, out = _run(d, "prune", "--id", "p_test", "--apply", "--delete")
    assert code == 2, out
    assert "不可逆" in out and "归档" in out
    assert store.one("SELECT deleted FROM projects WHERE id='p_test'")["deleted"] == 0, "不得在未确认时删除"


def test_explicit_id_can_act_on_an_unknown_project_but_only_when_asked(seeded):
    """A human can still decide; the tool just refuses to decide for them."""
    d, store = seeded
    code, out = _run(d, "prune", "--id", "p_unknown", "--apply")
    assert code == 0, out
    assert store.one("SELECT archived_at FROM projects WHERE id='p_unknown'")["archived_at"] is not None


def test_unknown_id_is_reported_rather_than_silently_skipped(seeded):
    d, _ = seeded
    code, out = _run(d, "prune", "--id", "p_does_not_exist", "--apply")
    assert code == 2 and "找不到" in out, out


# ---------------------------------------------------------------- blob reference inventory
def test_the_blob_audit_sees_references_hidden_inside_json(tmp_path):
    """A per-column scan cannot see a blob id inside `jobs.snapshot`. That is the whole reason
    the inventory exists, so it has to be the thing the test proves."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from blob_audit import audit
    from docweave.store import Store, now

    store = Store(tmp_path)
    only_in_json = "a" * 64
    also_direct = "b" * 64
    (tmp_path / "blobs").mkdir(exist_ok=True)
    for digest in (only_in_json, also_direct, "c" * 64):
        (tmp_path / "blobs" / digest).write_bytes(b"x")

    store.execute(
        "INSERT INTO jobs (id,project_id,status,tenant,created_at,run_no,snapshot) "
        "VALUES ('j1','p1','completed','default',?,1,?)",
        (now(), json.dumps({"template": {"working_blob": only_in_json},
                            "sources": [{"blob": also_direct}]})))
    store.execute(
        "INSERT INTO templates (id,name,kind,format,tenant,created_at,updated_at,original_blob,deleted) "
        "VALUES ('t1','t','builtin_doc','docx','default',?,?,?,0)", (now(), now(), also_direct))

    r = audit(tmp_path)
    assert only_in_json in r["reachable_only_from_json"], \
        "只能从 JSON 到达的 blob 必须被报出来——六列扫描正是在这里出错"
    assert also_direct not in r["reachable_only_from_json"]
    assert r["unreferenced_on_disk"] == ["c" * 64]
    assert r["referenced_but_missing"] == []


def test_the_blob_audit_does_not_offer_to_delete():
    """A delete path written against an incomplete mark set destroys user data. The inventory
    ships first, on purpose."""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "blob_audit.py").read_text("utf-8")
    for forbidden in ("unlink(", "os.remove", "shutil.rmtree", "DELETE FROM"):
        assert forbidden not in src, f"这个脚本不应当能删除任何东西，但出现了 {forbidden}"
