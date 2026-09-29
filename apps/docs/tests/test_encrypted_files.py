"""Requirement §8.7: an encrypted file is never a pass.

ClamAV cannot look inside what it cannot decrypt and answers OK, so a password-protected ZIP, 7z,
RAR, Office document or PDF used to be accepted as "clean" and served to everyone the file was
shared with. `synwevo_core.encrypted` looks at the container after the engine's answer, and every
upload path refuses an `encrypted` verdict with `encrypted_file` — not accepted, and not malware.

Part 1 holds the detector to the formats (fixtures from `encrypted_fixtures.py`, MuPDF for real
PDFs), part 2 the scanner (the clamd protocol double; a real engine when
``CLAMD_TEST_ADDRESS`` names one), part 3 the Synwevo Docs upload paths, part 4 the rescan
of files stored before scanning (a finding, not a withdrawal: access is the owner's decision).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import socket
import time
from urllib.parse import urlsplit

import pytest

from synwevo_core import malware as M
from synwevo_core import quarantine as Q
from synwevo_core.auth import Auth
from synwevo_core.encrypted import encrypted_kind

from . import encrypted_fixtures as F
from .clamd_double import EICAR, ClamdDouble
from .helpers import Api

SENTENCE = "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传"


def _mupdf_pdf(method=None, user: str | None = None, **kw) -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "TEST FIXTURE")
    if method is None:
        out = doc.tobytes()
    else:
        out = doc.tobytes(encryption=getattr(pymupdf, method), owner_pw="owner-secret", user_pw=user or "",
                          permissions=pymupdf.PDF_PERM_PRINT, **kw)
    doc.close()
    return out


# =============================================================================== 1. the detector
def test_zip_archives_and_packages_with_encrypted_entries():
    plain = F.zip_bytes({"a.txt": b"TEST", "b.txt": b"FIXTURE"})
    assert encrypted_kind(plain) is None
    assert encrypted_kind(F.flag_encrypted(plain)) == "archive"
    # one entry is enough, whichever record carries the flag
    assert encrypted_kind(F.flag_encrypted(plain, local=False, only="b.txt")) == "archive"
    assert encrypted_kind(F.flag_encrypted(plain, central=False, only="b.txt")) == "archive"
    # a damaged central directory: extractors walk the local headers, so does the detector
    assert encrypted_kind(F.flag_encrypted(plain)[:-22]) == "archive"
    # OOXML and OpenDocument packages are office documents
    assert encrypted_kind(F.ooxml()) is None and encrypted_kind(F.ooxml(encrypted_entry=True)) == "office"
    assert encrypted_kind(F.odf(False)) is None and encrypted_kind(F.odf(True)) == "office"
    # a plain archive that merely stores an encrypted one is not itself password-protected (the
    # engine's AlertEncryptedArchive reports the nested one: see the scanner tests)
    assert encrypted_kind(F.zip_bytes({"inner.zip": F.flag_encrypted(plain)})) is None


def test_compound_files_are_parsed_not_searched():
    import io

    import olefile
    for blob in (F.encrypted_ooxml(), F.xls(filepass=True), F.doc(encrypted=True), F.ppt(encrypted_token=True)):
        assert olefile.OleFileIO(io.BytesIO(blob)).listdir(), "the fixture is a real compound file"
    # password-protected OOXML (EncryptionInfo + EncryptedPackage, MS-OFFCRYPTO)
    assert encrypted_kind(F.encrypted_ooxml()) == "office"
    # Excel: FilePass right after BOF (or after WriteProtect); small streams live in the mini stream
    assert encrypted_kind(F.xls(filepass=True)) == "office"
    assert encrypted_kind(F.xls(filepass=True, write_protect=True)) == "office"
    assert encrypted_kind(F.xls(filepass=True, pad=6000)) == "office"
    assert encrypted_kind(F.xls(filepass=False)) is None and encrypted_kind(F.xls(filepass=False, pad=6000)) is None
    # Word: the FIB's fEncrypted bit
    assert encrypted_kind(F.doc(encrypted=True)) == "office"
    # PowerPoint: the Current User header token, or an EncryptedSummary stream
    assert encrypted_kind(F.ppt(encrypted_token=True)) == "office"
    assert encrypted_kind(F.ppt(encrypted_summary=True)) == "office"
    assert encrypted_kind(F.ppt()) is None
    # a legacy .doc keeps its text as UTF-16: a document that talks about these names is not encrypted
    talk = F.doc(encrypted=False, text="EncryptedPackage EncryptionInfo EncryptedSummary FilePass")
    assert "EncryptedPackage".encode("utf-16-le") in talk and encrypted_kind(talk) is None
    # an encrypted object embedded in a readable document is not the document being encrypted
    embedded = F.cfb({"WordDocument": F.word_document(encrypted=False),
                      "ObjectPool": {"_1": {"EncryptionInfo": os.urandom(100), "EncryptedPackage": os.urandom(5000)}}})
    assert encrypted_kind(embedded) is None


@pytest.mark.parametrize("method", ["PDF_ENCRYPT_RC4_40", "PDF_ENCRYPT_RC4_128", "PDF_ENCRYPT_AES_128", "PDF_ENCRYPT_AES_256"])
def test_a_pdf_that_needs_a_password_is_encrypted_one_with_only_an_owner_password_is_not(method):
    # revisions 2, 3, 4 (MD5/RC4 key and check) and 6 (ISO 32000-2 algorithm 2.B), as MuPDF writes them
    assert encrypted_kind(_mupdf_pdf(method, user="user-secret")) == "pdf"
    assert encrypted_kind(_mupdf_pdf(method)) is None            # opens with the empty password: readable
    assert encrypted_kind(_mupdf_pdf()) is None


def test_pdf_cross_reference_streams_other_handlers_and_damaged_files():
    # object streams and a cross-reference stream carry the trailer
    assert encrypted_kind(_mupdf_pdf("PDF_ENCRYPT_AES_256", user="u", use_objstms=1)) == "pdf"
    assert encrypted_kind(_mupdf_pdf("PDF_ENCRYPT_AES_256", use_objstms=1)) is None
    # a public-key (or any non-standard) handler cannot be opened without a key
    assert encrypted_kind(F.pdf(encrypt=b"<< /Filter /Adobe.PubSec /SubFilter /adbe.pkcs7.s5 /V 4 /R 4 >>")) == "pdf"
    # an encryption dictionary that cannot be evaluated does not open either
    assert encrypted_kind(F.pdf(encrypt=b"<< /Filter /Standard /V 2 /R 3 /O (short) /U (short) /P -4 >>")) == "pdf"
    # "/Encrypt" as text on a page is not a trailer entry
    assert encrypted_kind(F.pdf(content=b"BT (/Encrypt 5 0 R trailer << /Encrypt 5 0 R >>) Tj ET")) is None
    # without startxref readers repair from the trailer keyword; so does the detector
    assert encrypted_kind(F.pdf(encrypt=b"<< /Filter /Adobe.PubSec >>", xref=False)) == "pdf"
    assert encrypted_kind(F.pdf(xref=False, content=b"BT (/Encrypt 5 0 R) Tj ET")) is None
    user, owner = _mupdf_pdf("PDF_ENCRYPT_RC4_128", user="u"), _mupdf_pdf("PDF_ENCRYPT_RC4_128")
    broken = lambda b: b[:b.rindex(b"startxref")] + b"startxref\n999999999\n%%EOF\n"   # noqa: E731
    assert encrypted_kind(broken(user)) == "pdf" and encrypted_kind(broken(owner)) is None


def test_seven_zip_and_rar():
    # 7-Zip packs its header with LZMA by default: the AES coder is inside it
    assert encrypted_kind(F.sevenzip(encrypted=True)) == "archive"
    assert encrypted_kind(F.sevenzip(encrypted=True, encode_header=False)) == "archive"
    assert encrypted_kind(F.sevenzip(encrypted=True, encrypt_header=True)) == "archive"      # -mhe=on
    assert encrypted_kind(F.sevenzip(encrypted=False)) is None
    assert encrypted_kind(F.sevenzip(encrypted=False, encode_header=False)) is None
    for build in (F.rar4, F.rar5):
        assert encrypted_kind(build()) is None
        assert encrypted_kind(build(file_encrypted=True)) == "archive"
        assert encrypted_kind(build(headers_encrypted=True)) == "archive"


def test_malformed_input_is_none_and_never_raises():
    rnd = random.Random(20260929)
    samples = [F.flag_encrypted(F.zip_bytes({"a": b"x" * 300})), F.odf(True), F.encrypted_ooxml(), F.xls(filepass=True, pad=6000),
               F.doc(encrypted=True), F.ppt(encrypted_token=True), F.pdf(encrypt=b"<< /Filter /Standard /V 2 /R 3 >>"),
               F.sevenzip(encrypted=True), F.rar4(file_encrypted=True), F.rar5(file_encrypted=True)]
    seen = 0
    for s in samples:
        for cut in sorted({0, 1, 7, 8, 30, 64, 511, 512, 513, len(s) // 2, len(s) - 1} | {rnd.randrange(len(s)) for _ in range(20)}):
            assert encrypted_kind(s[:max(0, cut)]) in (None, "archive", "office", "pdf")
            seen += 1
        for _ in range(40):
            d = bytearray(s)
            for _ in range(rnd.randrange(1, 12)):
                d[rnd.randrange(len(d))] = rnd.randrange(256)
            assert encrypted_kind(bytes(d)) in (None, "archive", "office", "pdf")
            seen += 1
    for magic in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00", b"%PDF-1.7"):
        assert encrypted_kind(magic) is None and encrypted_kind(magic + b"\xff" * 4096) is None
        assert encrypted_kind(magic + os.urandom(2048)) in (None, "archive", "office", "pdf")
    assert encrypted_kind(b"") is None and encrypted_kind(b"name,email\nTEST,t@example.test\n") is None
    assert seen > 500


# =============================================================================== 2. the scanner
@pytest.fixture()
def double():
    d = ClamdDouble()
    yield d
    d.close()


def test_the_scanner_reports_encrypted_never_clean_and_never_infected(double):
    sc = M.ClamdScanner(double.address, timeout_s=5)
    enc = F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST FIXTURE"}))
    before = len(double.scans)
    res = sc.scan(enc, sha256="abc")
    assert (res.status, res.reason, res.sha256) == ("encrypted", "archive", "abc") and res.engine.startswith("ClamAV")
    assert len(double.scans) == before + 1, "the engine is asked first: a real signature on an encrypted file wins"
    # the engine's own alert (AlertEncryptedArchive, e.g. an encrypted archive nested in a document)
    plain = F.zip_bytes({"inner.zip": enc})
    double.infected[hashlib.sha256(plain).hexdigest()] = "Heuristics.Encrypted.Zip"
    res = sc.scan(plain)
    assert (res.status, res.signature, res.reason) == ("encrypted", "Heuristics.Encrypted.Zip", "archive")
    double.infected[hashlib.sha256(b"x" * 90).hexdigest()] = "Heuristics.Encrypted.PDF"
    assert sc.scan(b"x" * 90).reason == "pdf"
    # a real signature on encrypted bytes is malware
    double.infected[hashlib.sha256(enc).hexdigest()] = "Win.Test.Encrypted-Dropper"
    assert sc.scan(enc).status == "infected"
    assert sc.scan(EICAR).status == "infected"
    # readable files stay clean, an owner-password-only PDF included
    assert sc.scan(F.zip_bytes({"a.txt": b"TEST"})).status == "clean"
    assert sc.scan(_mupdf_pdf("PDF_ENCRYPT_AES_256")).status == "clean"
    assert M.encrypted_refusal(res.as_record()) and not M.encrypted_refusal(res.as_record() | {"rescan": True})
    assert not M.encrypted_refusal({"status": "clean"}) and not M.encrypted_refusal(None)


def _real_clamd() -> M.ClamdScanner:
    address = os.environ.get("CLAMD_TEST_ADDRESS")
    if not address:
        pytest.skip("CLAMD_TEST_ADDRESS is not set (a clamd with AlertEncryptedArchive yes)")
    parts = urlsplit(address)
    try:
        socket.create_connection((parts.hostname, parts.port or 3310), timeout=2).close()
    except OSError:
        pytest.skip(f"no clamd at {address}")
    return M.ClamdScanner(address, timeout_s=30)


def test_a_real_engine_with_alert_encrypted_archive():
    """Run against ClamAV with `AlertEncryptedArchive yes` (and not AlertEncryptedDoc), as the
    reference compose configures it: the engine alerts on a nested encrypted archive the detector
    does not look into, and a PDF with only an owner password stays clean."""
    sc = _real_clamd()
    enc = F.flag_encrypted(F.zip_bytes({"TEST.txt": b"TEST FIXTURE " * 40}))
    assert sc.scan(enc).status == "encrypted"
    nested = sc.scan(F.zip_bytes({"inner.zip": enc, "readme.txt": b"TEST FIXTURE"}))
    assert (nested.status, nested.reason) == ("encrypted", "archive") and nested.signature.startswith("Heuristics.Encrypted."), nested
    assert sc.scan(_mupdf_pdf("PDF_ENCRYPT_RC4_128")).status == "clean"
    assert sc.scan(_mupdf_pdf("PDF_ENCRYPT_RC4_128", user="u")).status == "encrypted"
    assert sc.scan(F.zip_bytes({"a.txt": b"TEST FIXTURE"})).status == "clean"
    assert sc.scan(EICAR).status == "infected"


# =============================================================================== 3. Synwevo Docs uploads
@pytest.fixture()
def api(tmp_path, monkeypatch, double):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    app = create_app(str(tmp_path / "d"), start_jobs=False)
    with TestClient(app) as c:
        c.app_ = app.state.app
        c.store = app.state.app.store
        c.api = Api(c)
        tok, _ = Auth(c.store).create_key("rw", "default", ("read", "write"))
        c.key = {"Authorization": f"Bearer {tok}"}
        yield c


def _wait(c, pid, sid, until, timeout=10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = next(x for x in c.get(f"/api/projects/{pid}/sources").json() if x["id"] == sid)
        if f["status"] in until:
            return f
        time.sleep(0.05)
    raise AssertionError(f"source {sid} did not reach {until}: {f}")


def _refused(r, kind: str, filename: str | None = None) -> None:
    assert r.status_code == 400, r.text
    body = r.json()
    err = body["error"] if isinstance(body.get("error"), dict) else body
    code = err.get("code") or err.get("error")
    assert code == "encrypted_file" and err["message"] == SENTENCE, body
    detail = err.get("detail") or err.get("details") or {}
    assert detail.get("kind") == kind, body
    if filename:
        assert detail.get("filename") == filename, body


def test_an_encrypted_source_is_refused_before_anything_is_stored(api, double):
    c = api
    pid = c.api.project("加密")
    before = len(double.scans)
    for name, data, kind in (("客户.xlsx", F.encrypted_ooxml(), "office"), ("旧表.xls", F.xls(filepass=True), "office"),
                             ("合同.pdf", _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u"), "pdf"),
                             ("资料.docx", F.ooxml(encrypted_entry=True), "office")):
        r = c.post(f"/api/projects/{pid}/sources", files=[("files", (name, data))])
        _refused(r, kind, name)
        assert not c.store.has_blob(hashlib.sha256(data).hexdigest()), "refused bytes are not stored"
        assert Q.verdict(c.store, hashlib.sha256(data).hexdigest()) is None, "not malware: no quarantine verdict"
    assert c.get(f"/api/projects/{pid}/sources").json() == []
    assert len(double.scans) == before, "refused before anything was stored or streamed"
    # a PDF with only an owner password opens without one: accepted and read
    owner = c.post(f"/api/projects/{pid}/sources", files=[("files", ("只读.pdf", _mupdf_pdf("PDF_ENCRYPT_AES_256")))]).json()[0]
    f = _wait(c, pid, owner["id"], ("ready", "needs_ocr", "error"))
    assert f["status"] in ("ready", "needs_ocr") and f["scan"]["status"] == "clean", f
    assert c.get(f"/api/sources/{owner['id']}/download").status_code == 200


def test_machine_uploads_resumable_uploads_and_server_imports_are_refused_too(api, monkeypatch, tmp_path):
    c, h = api, api.key
    pid = c.post("/api/v1/projects", json={"name": "加密"}, headers=h).json()["id"]
    data = F.encrypted_ooxml()
    _refused(c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data)}, headers=h), "office", "客户.xlsx")
    # a resumable upload: complete verifies size and digest, using it as a source meets the gate
    up = c.post("/api/v1/uploads", json={"filename": "客户.xlsx", "size": len(data), "purpose": "source"}, headers=h).json()
    assert c.put(f"/api/v1/uploads/{up['id']}", content=data, headers={**h, "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}",
                                                                          "Content-Type": "application/octet-stream"}).status_code == 200
    assert c.post(f"/api/v1/uploads/{up['id']}/complete", json={}, headers=h).status_code == 200
    _refused(c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": up["id"]}, headers=h), "office")
    # a server-side import from an allowed directory
    import synwevo_docs.api.v1 as v1
    monkeypatch.setattr(v1, "IMPORT_ROOTS", [str(tmp_path)])
    (tmp_path / "旧表.xls").write_bytes(F.xls(filepass=True))
    _refused(c.post(f"/api/v1/projects/{pid}/files", json={"path": str(tmp_path / "旧表.xls")}, headers=h), "office", "旧表.xls")
    # machine template upload
    _refused(c.post("/api/v1/templates", files={"file": ("合同.docx", data)}, headers=h), "office")
    assert c.store.one("SELECT COUNT(*) AS n FROM source_files")["n"] == 0
    assert c.store.one("SELECT COUNT(*) AS n FROM templates")["n"] == 0


def test_an_encrypted_template_is_refused_with_the_acceptance_code(api):
    c = api
    r = c.post("/api/templates/upload", files={"file": ("合同.docx", F.encrypted_ooxml())})
    _refused(r, "office", "合同.docx")
    r = c.post("/api/templates/upload", files={"file": ("发货单.pdf", _mupdf_pdf("PDF_ENCRYPT_RC4_128", user="u"))})
    _refused(r, "pdf", "发货单.pdf")
    assert c.store.one("SELECT COUNT(*) AS n FROM templates")["n"] == 0


def test_an_engine_only_encrypted_verdict_leaves_the_source_unread_but_not_malware(api, double):
    """Bytes the detector does not recognise but the engine alerts on (AlertEncryptedArchive on
    an archive nested in a document): the row ends in `error`, is never parsed or served, and is
    not treated as malware — no quarantine, no rejected digest, nobody told of malware."""
    c = api
    pid = c.api.project("嵌套")
    data = "姓名,邮箱\n测试,t@example.test\n".encode() * 5            # TEST FIXTURE standing in for such a document
    sha = hashlib.sha256(data).hexdigest()
    double.infected[sha] = "Heuristics.Encrypted.Zip"
    out = c.post(f"/api/projects/{pid}/sources", files=[("files", ("名单.csv", data))]).json()[0]
    assert out["status"] == "scanning"
    f = _wait(c, pid, out["id"], ("error", "ready", "rejected"))
    assert f["status"] == "error" and f["error"] == SENTENCE, f
    assert f["scan"]["status"] == "encrypted" and f["scan"]["reason"] == "archive" and f["scan"]["signature"] == "Heuristics.Encrypted.Zip"
    assert f["scan"]["sha256"] == sha and not f["structure"], "never parsed"
    r = c.get(f"/api/sources/{out['id']}/download")
    assert r.status_code == 410 and r.json()["error"] == "encrypted_file" and r.json()["message"] == SENTENCE
    view = c.get(f"/api/sources/{out['id']}/view").json()
    assert view["view"] == "error" and "grid" not in view
    assert c.get(f"/api/sources/{out['id']}/search", params={"q": "测试"}).status_code == 410
    from synwevo_docs import authz
    assert not authz.blob_readable(c.store, sha, "default"), "the blob id is not readable either"
    opt = c.put(f"/api/sources/{out['id']}/options", json={"csv": {"delimiter": ","}})
    assert opt.status_code == 410 and opt.json()["error"] == "encrypted_file"
    row = c.store.one("SELECT * FROM source_files WHERE id=?", (out["id"],))
    assert row["status"] == "error" and row["parsed_blob"] is None and row["working_blob"] is None
    # not malware: no quarantine, no rejected digest (the bytes stay where the row points, unreadable)
    assert Q.verdict(c.store, sha) is None and c.store.has_blob(sha)
    # the same bytes again: judged again, not "already identified as malicious"
    again = c.post(f"/api/projects/{pid}/sources", files=[("files", ("名单.csv", data))])
    assert again.status_code == 200 and again.json()[0]["status"] == "scanning"
    assert _wait(c, pid, again.json()[0]["id"], ("error", "ready"))["scan"]["status"] == "encrypted"
    # desensitizing "all the project's files" leaves it out instead of failing the request on it
    ok = c.post(f"/api/projects/{pid}/sources", files=[("files", ("另一份.csv", "姓名\n王五\n".encode()))]).json()[0]
    _wait(c, pid, ok["id"], ("ready",))
    d = c.post(f"/api/projects/{pid}/desensitize", json={})
    assert d.status_code == 200, d.text
    assert "名单" not in d.text


def test_options_cannot_reparse_a_file_the_scan_has_not_accepted(api, double):
    """Changing a CSV's options reparses it. On a file still being checked that flipped it to
    `parsing` — the parser opened unscanned bytes, the file became downloadable, and the scan's
    later verdict was discarded as stale."""
    c = api
    double.down = True
    pid = c.api.project("旁路")
    out = c.post(f"/api/projects/{pid}/sources", files=[("files", ("名单.csv", EICAR + b"\n"))]).json()[0]
    f = _wait(c, pid, out["id"], ("scan_failed", "ready", "error"))
    assert f["status"] == "scan_failed"
    r = c.put(f"/api/sources/{out['id']}/options", json={"csv": {"delimiter": ","}})
    assert r.status_code == 409 and r.json()["error"] == "not_yet_accepted", r.text
    row = c.store.one("SELECT status, parsed_blob FROM source_files WHERE id=?", (out["id"],))
    assert row["status"] == "scan_failed" and row["parsed_blob"] is None
    assert c.get(f"/api/sources/{out['id']}/download").status_code == 409


def test_without_a_scanner_nothing_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    monkeypatch.delenv("SYNWEVO_MALWARE_SCAN", raising=False)
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    app = create_app(str(tmp_path / "d"), start_jobs=False)
    with TestClient(app) as c:
        a = Api(c)
        pid = a.project("未配置")
        out = a.upload_bytes(pid, "客户.xlsx", F.encrypted_ooxml())[0]
        f = next(x for x in c.get(f"/api/projects/{pid}/sources").json() if x["id"] == out["id"])
        assert f["scan"]["status"] == "not_configured" and f["status"] == "error"      # the parser's own refusal, as before
        r = c.post("/api/templates/upload", files={"file": ("合同.docx", F.encrypted_ooxml())})
        assert r.status_code == 400 and r.json()["error"] == "encrypted"                # the structure check, as before


# =============================================================================== 4. files stored before scanning
@pytest.fixture()
def hist(tmp_path, monkeypatch):
    data = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.delenv("SYNWEVO_CLAMD_ADDRESS", raising=False)
    monkeypatch.delenv("SYNWEVO_MALWARE_SCAN", raising=False)
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    app = create_app(str(data), start_jobs=False)
    with TestClient(app) as c:
        c.data, c.store, c.api = str(data), app.state.app.store, Api(c)
        yield c


def _rescan(c, capsys, **kw) -> tuple[int, str]:
    from synwevo_docs.rescan import main_rescan
    capsys.readouterr()
    code = main_rescan(argparse.Namespace(**({"apply": True, "limit": None, "workspace": None, "only": None} | kw)), c.data)
    return code, capsys.readouterr().out


def test_a_rescan_reports_encrypted_historical_files_and_leaves_their_access(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("历史")
    enc = c.api.upload_bytes(pid, "旧表.xls", F.xls(filepass=True))[0]
    pdf = c.api.upload_bytes(pid, "合同.pdf", _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u"))[0]
    clean = c.api.upload_bytes(pid, "名单.csv", "姓名\n测试\n".encode())[0]
    before = {r["id"]: r for r in c.store.all("SELECT id, status, error, updated_at FROM source_files")}
    assert all(c.get(f"/api/sources/{s['id']}/download").status_code == 200 for s in (enc, pdf, clean))

    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    code, out = _rescan(c, capsys)
    assert code == 1, out                                   # someone should look
    assert "设有密码或含有加密内容" in out and "由所有者决定" in out and enc["id"] in out and pdf["id"] in out
    assert "旧表" not in out and "合同" not in out, "ids and digests only, never file names"
    for s, kind in ((enc, "office"), (pdf, "pdf")):
        row = c.store.one("SELECT * FROM source_files WHERE id=?", (s["id"],))
        assert row["status"] == before[s["id"]]["status"] and row["error"] == before[s["id"]]["error"], "state unchanged"
        rec = row["scan"]
        assert (rec["status"], rec["reason"], rec["rescan"], rec["access"], rec["previous"]) == ("encrypted", kind, True, "unchanged", "not_configured")
        assert not M.encrypted_refusal(rec)
        assert c.get(f"/api/sources/{s['id']}/download").status_code == 200, "access is the owner's decision"
        assert Q.verdict(c.store, row["blob"]) is None
    assert c.store.one("SELECT scan FROM source_files WHERE id=?", (clean["id"],))["scan"]["status"] == "clean"
    # answered: a second run has nothing to look at
    code, out = _rescan(c, capsys)
    assert code == 0 and "没有需要补扫的内容" in out, out


def test_a_release_does_not_put_back_encrypted_bytes(hist, double, monkeypatch, capsys):
    """A signature on encrypted bytes quarantines them like any other; once the signature is
    withdrawn, the engine's answer is still not "clean" — nobody can check the contents."""
    c = hist
    pid = c.api.project("隔离")
    data = F.xls(filepass=True)
    sha = hashlib.sha256(data).hexdigest()
    src = c.api.upload_bytes(pid, "旧表.xls", data)[0]
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    double.infected[sha] = "Xls.Test.Encrypted-1"
    code, out = _rescan(c, capsys)
    assert code == 1 and "Xls.Test.Encrypted-1" in out                   # the signature wins over "encrypted"
    assert c.store.one("SELECT status FROM source_files WHERE id=?", (src["id"],))["status"] == "rejected"
    del double.infected[sha]
    from synwevo_docs.rescan import main_quarantine
    capsys.readouterr()
    code = main_quarantine(argparse.Namespace(action="release", sha256=sha, yes=False), c.data)
    out = capsys.readouterr().out
    assert code == 1 and "设有密码或含有加密内容" in out and "不恢复" in out, out
    assert c.store.one("SELECT status FROM source_files WHERE id=?", (src["id"],))["status"] == "rejected"
    assert Q.verdict(c.store, sha), "still held"
