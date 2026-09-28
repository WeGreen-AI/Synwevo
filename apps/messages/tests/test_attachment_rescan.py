"""CI-90: rescanning the message attachments stored before malware scanning was switched on.

The `org` fixture has no scanner — as production had none before 2026-09-28 — so every upload
here is stored with ``scan.status=not_configured`` and served as usual. Then scanning is switched
on (the clamd protocol double, `clamd_double.py`) and `python -m synwevo_messages.cli rescan` runs
in-process (one test runs the real entry point): a clean attachment gets a clean record and nothing
else; an infected one is rejected on every row that shares its bytes — download, inline display
and forwarding refuse it with 410 to members and 404 to everyone else — and its bytes move to the
quarantine, from which a reviewed false positive is released.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pytest

from synwevo_core import quarantine as Q

from .clamd_double import EICAR, ClamdDouble

M = "/api/messaging"
SIGNATURE = "Win.Test.EICAR_HDB-1"


@pytest.fixture()
def double():
    d = ClamdDouble()
    yield d
    d.close()


def _on(monkeypatch, double, **extra):
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def _data(c) -> str:
    return str(c.ident.store.root)


def _rescan(c, capsys, **kw) -> tuple[int, str]:
    from synwevo_core.messaging_rescan import main_rescan
    capsys.readouterr()
    code = main_rescan(argparse.Namespace(**({"apply": True, "limit": None} | kw)), _data(c))
    return code, capsys.readouterr().out


def _quarantine(c, capsys, action, sha=None, yes=False) -> tuple[int, str]:
    from synwevo_core.messaging_rescan import main_quarantine
    capsys.readouterr()
    code = main_quarantine(argparse.Namespace(action=action, sha256=sha, yes=yes), _data(c))
    return code, capsys.readouterr().out


def _row(c, aid) -> dict:
    return c.ident.store.one("SELECT * FROM message_attachments WHERE id=?", (aid,))


def _send(c, cid, name, data, h) -> tuple[dict, dict]:
    r = c.post(f"{M}/conversations/{cid}/attachments", files={"file": (name, data, "application/octet-stream")}, headers=h)
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["scan"]["status"] == "not_configured"
    msg = c.post(f"{M}/conversations/{cid}/messages", headers=h, json={"body": "", "attachment_id": att["id"]}).json()["message"]
    return att, msg


def test_clean_and_infected_attachments(org, double, monkeypatch, capsys):
    c = org
    U = c.users
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    group = c.post(f"{M}/groups", headers=ha, json={"organization_id": c.oid, "name": "转发目标", "member_ids": [U["cid"]["id"]]}).json()
    clean, _ = _send(c, dm["id"], "notes.txt", b"plain words\n", ha)
    bad, bad_msg = _send(c, dm["id"], "报价单-机密.txt", EICAR + b"\n", ha)
    fwd = c.post(f"{M}/messages/{bad_msg['id']}/forward", headers=ha, json={"conversation_ids": [group["id"]]}).json()["items"][0]["message"]
    copy_id = fwd["attachment"]["id"]
    sha = _row(c, bad["id"])["blob"]
    assert _row(c, copy_id)["blob"] == sha                       # a forward re-binds the same bytes
    c.sign_in("ben")
    assert c.get(f"{M}/attachments/{bad['id']}").status_code == 200    # before: served, as in production

    _on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1, out
    assert f"sha256={sha}" in out and SIGNATURE in out and "上传者没有收到通知" in out
    assert "报价单" not in out and "EICAR-STANDARD" not in out
    # clean: only the record changes
    r = _row(c, clean["id"])
    assert r["status"] == "accepted" and r["scan"]["status"] == "clean" and r["scan"]["rescan"] is True
    assert r["scan"]["previous"] == "not_configured"
    assert c.get(f"{M}/attachments/{clean['id']}").status_code == 200
    # infected: every row on the bytes, and the bytes moved (not deleted)
    for aid in (bad["id"], copy_id):
        r = _row(c, aid)
        assert r["status"] == "rejected" and r["scan"]["status"] == "infected" and r["scan"]["signature"] == SIGNATURE
    assert not c.ident.store.has_blob(sha) and Q.bytes_path(c.ident.store, sha).is_file()
    entry = Q.load(c.ident.store, sha)
    assert {(x["kind"], x["id"]) for x in entry["rows"]} == {("message_attachments", bad["id"]), ("message_attachments", copy_id)}
    assert "报价单" not in json.dumps(entry, ensure_ascii=False)
    # a member is told why; someone who could not read it learns nothing
    r = c.get(f"{M}/attachments/{bad['id']}")
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    assert c.get(f"{M}/attachments/{bad['id']}?inline=1").status_code == 410
    item = next(m for m in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"] if m["id"] == bad_msg["id"])
    assert item["attachment"]["rejected"] is True and item["attachment"]["inline"] is False
    c.sign_in("cid")
    assert c.get(f"{M}/attachments/{bad['id']}").status_code == 404
    assert c.get(f"{M}/attachments/{copy_id}").status_code == 410
    # it cannot be forwarded any more
    ha = c.sign_in("ann")
    r = c.post(f"{M}/messages/{bad_msg['id']}/forward", headers=ha, json={"conversation_ids": [group["id"]]})
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='attachment.rescan_rejected'")["n"] == 2
    # idempotent
    code, out = _rescan(c, capsys)
    assert code == 0 and "没有需要补扫的内容" in out


def test_an_engine_that_is_down_changes_nothing_and_the_dry_run_writes_nothing(org, double, monkeypatch, capsys):
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    a, _ = _send(c, dm["id"], "a.txt", b"one\n", ha)
    b, _ = _send(c, dm["id"], "b.txt", EICAR + b"\n", ha)
    _on(monkeypatch, double)
    code, out = _rescan(c, capsys, apply=False)
    assert code == 0 and "附件：2 份内容（2 行）" in out and "not_configured 2" in out and "扫描器：可用" in out
    assert double.scans == []
    double.down = True
    assert _rescan(c, capsys)[0] == 3
    assert _rescan(c, capsys, apply=False)[0] == 3
    assert _row(c, a["id"])["scan"] == {"status": "not_configured", "sha256": _row(c, a["id"])["blob"]}
    assert _row(c, b["id"])["status"] == "accepted" and Q.entries(c.ident.store) == []


def test_too_large_and_engine_errors_are_recorded_and_left_as_they_were(org, double, monkeypatch, capsys):
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    big, _ = _send(c, dm["id"], "big.txt", b"x" * 200, ha)
    small, _ = _send(c, dm["id"], "small.txt", b"y\n", ha)
    double.errors.add(_row(c, small["id"])["blob"])
    _on(monkeypatch, double, SYNWEVO_CLAMD_MAX_BYTES="100")
    code, out = _rescan(c, capsys)
    assert code == 1 and "超过扫描上限" in out and "扫描出错" in out
    assert _row(c, big["id"])["scan"]["status"] == "too_large" and _row(c, big["id"])["status"] == "accepted"
    assert _row(c, small["id"])["scan"]["status"] == "error" and _row(c, small["id"])["scan"]["attempts"] == 1
    assert c.get(f"{M}/attachments/{big['id']}").status_code == 200
    double.errors.clear()
    assert _rescan(c, capsys)[0] == 0
    assert _row(c, small["id"])["scan"]["status"] == "clean"


def test_a_reviewed_false_positive_is_released(org, double, monkeypatch, capsys):
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    bad, _ = _send(c, dm["id"], "fp.txt", EICAR + b"\n", ha)
    sha = _row(c, bad["id"])["blob"]
    _on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    assert _quarantine(c, capsys, "release", sha)[0] == 1           # still flagged: stays where it is
    assert _row(c, bad["id"])["status"] == "rejected"
    double.eicar_withdrawn = True
    code, out = _quarantine(c, capsys, "release", sha)
    assert code == 0, out
    r = _row(c, bad["id"])
    assert r["status"] == "accepted" and r["scan"]["status"] == "clean" and r["scan"]["released"] is True
    c.sign_in("ben")
    assert c.get(f"{M}/attachments/{bad['id']}").status_code == 200
    assert Q.load(c.ident.store, sha)["state"] == "released" and not Q.bytes_path(c.ident.store, sha).exists()


def test_the_command_line_entry_point(org, double):
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    _send(c, dm["id"], "a.txt", b"one\n", ha)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SYNWEVO_")}
    env.update({"SYNWEVO_CLAMD_ADDRESS": double.address, "SYNWEVO_CLAMD_TIMEOUT_S": "5"})
    run = lambda *a: subprocess.run([sys.executable, "-m", "synwevo_messages.cli", "--data", _data(c), *a],  # noqa: E731
                                    env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    dry = run("rescan")                                        # without --apply: a preview
    assert dry.returncode == 0 and "附件：1 份内容（1 行）" in dry.stdout, dry.stderr
    real = run("rescan", "--apply")
    assert real.returncode == 0 and "干净 1" in real.stdout, real.stderr + real.stdout
    assert run("quarantine", "purge", "0" * 64).returncode == 2


def test_a_standing_verdict_refuses_attachments_even_with_the_bytes_back_in_the_store(org, double, monkeypatch, capsys):
    """Review finding 5: the verdict (the quarantine entry) refuses the attachment whether or not
    a copy of the bytes is back in the blob store — a restored backup, a crash between marking
    and moving — and a new upload of those bytes is refused even where scanning is off."""
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    att, _ = _send(c, dm["id"], "a.txt", EICAR + b"\n", ha)
    store = c.ident.store
    sha = _row(c, att["id"])["blob"]
    # Synwevo Docs' rescan judged the same bytes first: its row is held, this attachment is not marked
    Q.hold(store, sha, EICAR + b"\n", signature=SIGNATURE, engine="ClamAV fixture",
           rows=[{"kind": "source_files", "id": "s_fixture", "workspace": None, "previous": {"status": "ready", "scan": None}}], by="test")
    Q.remove_from_store(store, sha)
    store.put_blob(EICAR + b"\n")                         # and a copy came back (a restored backup)
    c.sign_in("ben")
    r = c.get(f"{M}/attachments/{att['id']}")
    assert r.status_code == 410 and r.json()["error"] == "rejected_malware"
    ha = c.sign_in("ann")
    r = c.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("again.txt", EICAR + b"\n", "text/plain")}, headers=ha)
    assert r.status_code == 400 and r.json()["error"] == "malware_detected"
    # this application's rescan marks its row and finishes the move
    _on(monkeypatch, double)
    code, out = _rescan(c, capsys)
    assert code == 1 and "已从存储移走" in out and "已有隔离结论" in out, out
    assert _row(c, att["id"])["status"] == "rejected" and not store.has_blob(sha)


def test_release_refuses_to_run_beside_a_rescan_and_marks_only_what_it_restored(org, double, monkeypatch, capsys):
    """Review finding 9: release and rescan take the same run lock; a row the release could not
    restore stays held in the index."""
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    one, _ = _send(c, dm["id"], "a.txt", EICAR + b"\n", ha)
    two, two_msg = _send(c, dm["id"], "b.txt", EICAR + b"\n", ha)
    _on(monkeypatch, double)
    assert _rescan(c, capsys)[0] == 1
    store = c.ident.store
    sha = _row(c, one["id"])["blob"]
    double.eicar_withdrawn = True
    from synwevo_core import rescan as RS
    with RS.run_lock(store, "messages"):                    # a rescan is running
        code, _ = _quarantine(c, capsys, "release", sha)
        assert code == RS.EXIT_USAGE and _row(c, one["id"])["status"] == "rejected"
    # someone else changed the second row's record meanwhile: it is not restored, and stays held
    store.execute("UPDATE message_attachments SET scan=? WHERE id=?", (json.dumps({"status": "infected", "sha256": "0" * 64}), two["id"]))
    code, out = _quarantine(c, capsys, "release", sha)
    assert code == 0 and two["id"] in out, out
    assert _row(c, one["id"])["status"] == "accepted" and _row(c, two["id"])["status"] == "rejected"
    held = {x["id"] for x in Q.pending(Q.load(store, sha))}
    assert held == {two["id"]} and Q.bytes_path(store, sha).is_file()        # its copy stays until it is released


def test_an_attachment_recorded_clean_is_still_marked_under_the_other_application_s_verdict(org, double, monkeypatch, capsys):
    """Second review, HIGH-2 (mirror): a clean record from before does not keep a row out of the
    rescan once a verdict on its bytes stands."""
    c = org
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    att, _ = _send(c, dm["id"], "a.txt", EICAR + b"\n", ha)
    store = c.ident.store
    sha = _row(c, att["id"])["blob"]
    _on(monkeypatch, double)
    double.eicar_withdrawn = True
    assert _rescan(c, capsys)[0] == 0 and _row(c, att["id"])["scan"]["status"] == "clean"
    Q.hold(store, sha, EICAR + b"\n", signature=SIGNATURE, engine="ClamAV fixture",
           rows=[{"kind": "source_files", "id": "s_fixture", "workspace": None, "previous": {"status": "ready", "scan": None}}], by="test")
    Q.remove_from_store(store, sha)
    code, out = _rescan(c, capsys)
    assert code == 1 and "已有隔离结论" in out, out
    assert _row(c, att["id"])["status"] == "rejected"
