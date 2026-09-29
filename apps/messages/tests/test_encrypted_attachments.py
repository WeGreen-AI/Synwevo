"""Requirement §8.7 for Synwevo Message: a password-protected attachment is never a pass.

An attachment is shared as it is with everyone in the conversation, so an encrypted ZIP that the
engine could not look into — and answered OK for — was the worst case of the blind spot: stored as
"clean" and downloadable by other members. With scanning on, the acceptance gate
(`member_import.accept_upload`, shared with member imports) refuses it with ``encrypted_file``:
nothing is stored, no quarantine, no "malicious" record. An attachment stored before scanning that
a rescan finds encrypted is reported and left readable — withdrawing it is its owner's decision.

The engine is the clamd protocol double (`clamd_double.py`); the encrypted files are built by
the Docs suite's fixture builders (`apps/docs/tests/encrypted_fixtures.py`, loaded by path).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

from synwevo_core import quarantine as Q

from .clamd_double import ClamdDouble

M = "/api/messaging"
SENTENCE = "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传"


def _fixtures():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location("_encrypted_fixtures", root / "apps" / "docs" / "tests" / "encrypted_fixtures.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = _fixtures()


@pytest.fixture()
def double():
    d = ClamdDouble()
    yield d
    d.close()


def _on(monkeypatch, double):
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")


def _dm(c, ha):
    return c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()


def _attach(c, cid, name, data, h):
    return c.post(f"{M}/conversations/{cid}/attachments", files={"file": (name, data, "application/octet-stream")}, headers=h)


def test_a_password_protected_attachment_is_refused_and_nothing_is_stored(org, double, monkeypatch):
    c = org
    _on(monkeypatch, double)
    ha = c.sign_in("ann")
    dm = _dm(c, ha)
    for name, data, kind in (("报价.zip", F.flag_encrypted(F.zip_bytes({"报价.xlsx": b"TEST FIXTURE " * 30})), "archive"),
                             ("资料.7z", F.sevenzip(encrypted=True), "archive"), ("资料.rar", F.rar5(file_encrypted=True), "archive"),
                             ("合同.docx", F.encrypted_ooxml(), "office"), ("旧表.xls", F.xls(filepass=True), "office")):
        r = _attach(c, dm["id"], name, data, ha)
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["error"] == "encrypted_file" and body["message"] == SENTENCE, body
        assert body["detail"] == {"filename": name, "kind": kind}, body
        sha = hashlib.sha256(data).hexdigest()
        assert not c.ident.store.has_blob(sha) and Q.verdict(c.ident.store, sha) is None
    # the engine's own alert on something nested is refused the same way
    nested = F.zip_bytes({"inner.zip": F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST"}))})
    double.infected[hashlib.sha256(nested).hexdigest()] = "Heuristics.Encrypted.Zip"
    r = _attach(c, dm["id"], "打包.zip", nested, ha)
    assert r.status_code == 400 and r.json()["error"] == "encrypted_file" and r.json()["detail"]["kind"] == "archive"
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM message_attachments")["n"] == 0
    # a readable archive goes through
    ok = _attach(c, dm["id"], "资料.zip", F.zip_bytes({"a.txt": b"TEST FIXTURE"}), ha)
    assert ok.status_code == 201 and ok.json()["scan"]["status"] == "clean", ok.text


def test_a_rescan_reports_an_encrypted_historical_attachment_and_leaves_it_readable(org, double, monkeypatch, capsys):
    c = org
    ha = c.sign_in("ann")
    dm = _dm(c, ha)
    data = F.flag_encrypted(F.zip_bytes({"工资.xlsx": os.urandom(400)}))
    r = _attach(c, dm["id"], "工资.zip", data, ha)          # stored before scanning: accepted, not_configured
    assert r.status_code == 201 and r.json()["scan"]["status"] == "not_configured"
    att = r.json()
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]})

    _on(monkeypatch, double)
    from synwevo_core.messaging_rescan import main_rescan
    capsys.readouterr()
    code = main_rescan(argparse.Namespace(apply=True, limit=None), str(c.ident.store.root))
    out = capsys.readouterr().out
    assert code == 1, out
    assert att["id"] in out and "设有密码或含有加密内容" in out and "由所有者决定" in out and "工资" not in out
    row = c.ident.store.one("SELECT status, scan FROM message_attachments WHERE id=?", (att["id"],))
    assert row["status"] == "accepted"
    assert (row["scan"]["status"], row["scan"]["reason"], row["scan"]["rescan"], row["scan"]["access"]) == ("encrypted", "archive", True, "unchanged")
    c.sign_in("ben")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 200, "access is unchanged"
    assert Q.entries(c.ident.store) == []
    capsys.readouterr()
    assert main_rescan(argparse.Namespace(apply=True, limit=None), str(c.ident.store.root)) == 0
    assert "没有需要补扫的内容" in capsys.readouterr().out
