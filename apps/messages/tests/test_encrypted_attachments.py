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
    # the engine's own alert on something the detector does not see is refused the same way
    nested = F.zip_bytes({"inner.bin": b"TEST FIXTURE standing in for a compressed encrypted archive"})
    double.infected[hashlib.sha256(nested).hexdigest()] = "Heuristics.Encrypted.Zip"
    r = _attach(c, dm["id"], "打包.zip", nested, ha)
    assert r.status_code == 400 and r.json()["error"] == "encrypted_file" and r.json()["detail"]["kind"] == "archive"
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM message_attachments")["n"] == 0
    # a readable archive goes through
    ok = _attach(c, dm["id"], "资料.zip", F.zip_bytes({"a.txt": b"TEST FIXTURE"}), ha)
    assert ok.status_code == 201 and ok.json()["scan"]["status"] == "clean", ok.text


def _pdf(user: bool) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "TEST FIXTURE")
    out = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u" if user else "")
    doc.close()
    return out


def test_crafted_layouts_are_refused_and_files_that_open_freely_are_not(org, double, monkeypatch):
    """The 2026-09-29 security review sent eight crafted password-protected files through this gate
    and downloaded them byte for byte as another member. Each layout is here, refused; next to them
    files that open without a password, accepted."""
    c = org
    _on(monkeypatch, double)
    ha = c.sign_in("ann")
    dm = _dm(c, ha)
    enc_zip = F.flag_encrypted(F.zip_bytes({"报价.xlsx": b"TEST FIXTURE " * 30}))
    user = _pdf(True)
    i = user.index(b"<<", user.rfind(b"trailer")) + 2
    crafted = {
        "前缀.zip": b"X" + enc_zip,                                           # junk before the archive
        "伪装.pdf": F.pdf() + enc_zip,                                         # a PDF with the archive appended
        "长尾.pdf": user[:i] + b" " * (300 * 1024) + user[i:],                 # trailer past the old 256 KiB window
        "无头.pdf": b"\n" * 2048 + user,                                       # header late, offsets off
        "清单.odt": F.odf(True, pad=1_100_000),                                # manifest past the old 1 MiB cap
        "直存.7z": F.sevenzip(encrypted=True, header_coder="copy"),            # header packed with Copy
        "大写.xls": F.cfb({"WORKBOOK": F.xls_workbook(filepass=True)}),        # stream names in another case
        "小写.doc": F.cfb({"worddocument": F.word_document(encrypted=True)}),
    }
    for name, data in crafted.items():
        r = _attach(c, dm["id"], name, data, ha)
        assert r.status_code == 400 and r.json()["error"] == "encrypted_file", (name, r.status_code, r.text[:200])
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM message_attachments")["n"] == 0
    for name, data in {"结构保护.xls": F.lo_xls("structure"), "只读.pdf": _pdf(False), "资料.odt": F.odf(False, pad=1_100_000)}.items():
        r = _attach(c, dm["id"], name, data, ha)
        assert r.status_code == 201 and r.json()["scan"]["status"] == "clean", (name, r.text[:200])


UNCHECKED = "文件的结构无法安全地完成检查（可能已损坏、被截断或结构异常），因此未被接纳；如果它没有设密码，请用生成它的程序重新保存（或拆分成较小的文件）后重新上传"


def test_look_alikes_are_accepted_and_an_unfinished_check_has_its_own_sentence(org, double, monkeypatch):
    """Notes and code that mention /Encrypt and an ordinary ZIP of 210 000 entries are accepted.
    Round-3 trade-off: a DLL whose machine code holds a ZIP signature with the encryption bit set
    by chance can no longer be told apart from a real encrypted entry, so it is refused ("could not
    be checked") — as is a file whose structure the check cannot finish. Both get that sentence."""
    c = org
    _on(monkeypatch, double)
    ha = c.sign_in("ann")
    dm = _dm(c, ha)
    looks = {**F.text_about_pdf_internals(), "许多文件.zip": F.zip_entries(210_000)}
    for name, data in looks.items():
        r = _attach(c, dm["id"], name, data, ha)
        assert r.status_code == 201 and r.json()["scan"]["status"] == "clean", (name, r.status_code, r.text[:200])
    for name, data in {"打包.7z": F.sevenzip(encrypted=False, header_coder="delta+lzma"),
                       "profiler.dll": F.machine_code_with_zip_signature()}.items():
        r = _attach(c, dm["id"], name, data, ha)
        assert r.status_code == 400, (name, r.text)
        assert r.json()["error"] == "unchecked_file" and r.json()["message"] == UNCHECKED, name
        assert r.json()["detail"] == {"filename": name, "kind": "archive", "checked": False}, name


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
