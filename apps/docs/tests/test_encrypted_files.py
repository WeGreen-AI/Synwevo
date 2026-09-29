"""Requirement §8.7: an encrypted file is never a pass.

ClamAV cannot look inside what it cannot decrypt and answers OK, so a password-protected ZIP, 7z,
RAR, Office document or PDF used to be accepted as "clean" and served to everyone the file was
shared with. `synwevo_core.encrypted` looks at the container after the engine's answer, and every
upload path refuses an `encrypted` verdict with `encrypted_file` — not accepted, and not malware.

Part 1 holds the detector to the formats (fixtures from `encrypted_fixtures.py`, MuPDF for real
PDFs), including the 2026-09-29 security review's findings: formats found where readers find them,
fail closed when a structure cannot be evaluated, case-insensitive compound-file names, Excel's
default password, and time/memory bounds for every adversarial class. Part 2 is the scanner (the
clamd protocol double; a real engine when ``CLAMD_TEST_ADDRESS`` names one), part 3 the Synwevo Docs
upload paths (engine first, off the event loop), part 4 the rescan of files stored before scanning
(a finding, not a withdrawal: access is the owner's decision).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import random
import socket
import threading
import time
import tracemalloc
from urllib.parse import urlsplit

import pytest

from synwevo_core import malware as M
from synwevo_core import quarantine as Q
from synwevo_core.auth import Auth
from synwevo_core.encrypted import encrypted_kind, inspect

from . import encrypted_fixtures as F
from .clamd_double import EICAR, ClamdDouble
from .helpers import Api

SENTENCE = "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传"
MiB = 1 << 20


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
    # an encrypted archive stored inside a plain one cannot be checked either (the engine's
    # AlertEncryptedArchive reports it the same way); one compressed inside is left to the engine
    assert encrypted_kind(F.zip_bytes({"inner.zip": F.flag_encrypted(plain)})) == "archive"


def test_formats_are_found_where_readers_find_them():
    """Readers open a ZIP behind junk or appended to another file, the first of two concatenated
    archives, a 7z or RAR behind a self-extractor stub, a PDF whose header is late or missing."""
    enc_zip = F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST FIXTURE"}))
    plain_zip = F.zip_bytes({"b.txt": b"TEST FIXTURE"})
    assert encrypted_kind(b"X" + enc_zip) == "archive"
    assert encrypted_kind(F.pdf() + enc_zip) == "archive"                  # a PDF with a ZIP appended
    assert encrypted_kind(enc_zip + plain_zip) == "archive"                # the last directory is the plain one
    stub = b"MZ" + bytes(62)
    assert encrypted_kind(stub + F.sevenzip(encrypted=True)) == "archive"
    assert encrypted_kind(stub + F.rar5(file_encrypted=True)) == "archive"
    assert encrypted_kind(stub + F.rar4(file_encrypted=True)) == "archive"
    assert encrypted_kind(stub + F.sevenzip(encrypted=False)) is None and encrypted_kind(stub + plain_zip) is None
    user = _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u")
    assert encrypted_kind(b"\n" * 2048 + user) == "pdf"                    # header past the first KiB, offsets off
    assert encrypted_kind(b"%XYZ-1.7" + user[8:]) == "pdf"                 # no %PDF- header at all
    assert encrypted_kind(plain_zip + user) == "pdf"                       # a ZIP/PDF polyglot
    owner = _mupdf_pdf("PDF_ENCRYPT_AES_256")
    assert encrypted_kind(b"\n" * 2048 + owner) is None and encrypted_kind(b"%XYZ-1.7" + owner[8:]) is None


def test_a_pdf_trailer_is_read_however_long_it_is():
    """The first version gave up on a trailer dictionary over 256 KiB and answered "clean"."""
    for method in ("PDF_ENCRYPT_AES_256", "PDF_ENCRYPT_RC4_128"):
        user, owner = _mupdf_pdf(method, user="u"), _mupdf_pdf(method)
        for src, want in ((user, "pdf"), (owner, None)):
            i = src.index(b"<<", src.rfind(b"trailer")) + 2
            assert encrypted_kind(src[:i] + b" " * (300 * 1024) + src[i:]) == want
            assert encrypted_kind(src[:i] + b"\n%" + b"A" * (300 * 1024) + b"\n" + src[i:]) == want
    xs = _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u", use_objstms=1)
    import re
    k = xs.rfind(b"<<", 0, re.search(rb"/Type\s*/XRef", xs).start()) + 2
    assert encrypted_kind(xs[:k] + b" " * (300 * 1024) + xs[k:]) == "pdf"
    # the encryption key's name written with #xx escapes is still the key
    assert encrypted_kind(F.pdf(encrypt=b"<< /Filter /Adobe.PubSec >>").replace(b"/Encrypt 5", b"/Encr#79pt 5")) == "pdf"


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


def test_compound_file_names_compare_case_insensitively():
    """MS-CFB compares names without case, so readers find `encryptedpackage` or `WORKBOOK`."""
    pkg = F.cfb({"encryptedpackage": os.urandom(6000), "ENCRYPTIONINFO": os.urandom(300)})
    assert encrypted_kind(pkg) == "office"
    assert encrypted_kind(F.cfb({"WORKBOOK": F.xls_workbook(filepass=True)})) == "office"
    assert encrypted_kind(F.cfb({"worddocument": F.word_document(encrypted=True)})) == "office"
    assert encrypted_kind(F.cfb({"current user": _current_user(True), "PowerPoint Document": bytes(5000)})) == "office"
    assert encrypted_kind(F.cfb({"WORKBOOK": F.xls_workbook(filepass=False)})) is None


def _current_user(encrypted: bool) -> bytes:
    import struct
    return struct.pack("<HHI", 0, 0x0FF6, 0x24) + struct.pack("<II", 0x14, 0xF3D1C4DF if encrypted else 0xE391C05F) + bytes(28)


def test_workbook_structure_protection_opens_with_the_default_password():
    """Excel (and LibreOffice) write a workbook protected only in its structure as encryption under
    the default password; it opens without asking, so it is not "encrypted". Any other password is."""
    assert encrypted_kind(F.lo_xls("structure")) is None                    # real LibreOffice output, RC4
    assert encrypted_kind(F.lo_xls("password")) == "office"                 # real LibreOffice output, a password
    for scheme in ("xor", "rc4", "cryptoapi"):
        assert encrypted_kind(F.xls_filepass(scheme, "VelvetSweatshop")) is None, scheme
        assert encrypted_kind(F.xls_filepass(scheme, "TEST-secret")) == "office", scheme


def test_what_cannot_be_evaluated_fails_closed():
    """The first version answered "clean" whenever it could not finish; now that is "could not be
    checked", which refuses the file as well (and `inspect` says which of the two it was)."""
    # a 7z header packed with Copy or Deflate is unpacked; a coder chain it does not unpack fails closed
    for coder in ("copy", "deflate"):
        assert inspect(F.sevenzip(encrypted=True, header_coder=coder)) == ("archive", True)
        assert inspect(F.sevenzip(encrypted=False, header_coder=coder)) == (None, True)
    assert inspect(F.sevenzip(encrypted=True, header_coder="delta+lzma")) == ("archive", False)
    # an OpenDocument manifest over any old cap is streamed through, not skipped
    assert encrypted_kind(F.odf(True, pad=1_100_000)) == "office" and encrypted_kind(F.odf(False, pad=1_100_000)) is None
    assert inspect(F.odf_manifest_bomb()) == ("office", False)
    # the /Encrypt name with no trailer a reader could use: cannot be checked
    no_trailer = b"%PDF-1.7\n1 0 obj << /Encrypt 2 0 R /Size (" + b"x" * 100 + b"\n"
    assert inspect(no_trailer) == ("pdf", False)
    # a truncated compound file whose directory cannot be followed
    assert inspect(F.encrypted_ooxml()[:1536]) == ("office", False)


def test_text_and_programs_that_mention_pdf_internals_are_not_pdfs():
    """Round-2 review: notes, CSV cells, source code, a PDF library's jar and Ghostscript's dylibs
    mention `/Encrypt`; the first fix read every such file as a PDF and refused it. A PDF needs PDF
    structure: its header where readers look, or indirect objects and a cross-reference keyword."""
    notes = F.text_about_pdf_internals()
    for name, data in notes.items():
        assert inspect(data) == (None, True), name
    assert inspect(F.zip_bytes(notes)) == (None, True)                    # the same notes stored in a ZIP
    assert inspect(F.program_with_pdf_fragments()) == (None, True)
    # real encrypted PDFs are still told apart, with or without their header
    user = _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u")
    assert encrypted_kind(user) == "pdf" and encrypted_kind(b"%XYZ-1.7" + user[8:]) == "pdf"
    assert encrypted_kind(F.program_with_pdf_fragments() + user) == "pdf"   # a PDF appended to a program


def test_a_zip_signature_in_machine_code_is_not_an_archive():
    """Round-2 review: four signature bytes inside a real DLL, with the encrypted bit set by chance,
    were refused as an encrypted archive. A header counts when the other record names the same
    entry, or when it is coherent on its own (full version field, a real name, consistent sizes)."""
    assert inspect(F.machine_code_with_zip_signature()) == (None, True)
    # a genuine archive without its central directory still reads from its local headers
    assert inspect(F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST FIXTURE"}))[:-22]) == ("archive", True)
    # and one whose local header is odd but whose directory names it is still encrypted
    plain = F.zip_bytes({"a.txt": b"TEST"})
    odd = bytearray(F.flag_encrypted(plain, central=False))
    odd[4:6] = b"\x14\x85"                                                  # high version byte a writer would not set
    assert encrypted_kind(bytes(odd)) == "archive"


def test_an_ordinary_zip_with_many_entries_is_read_not_refused():
    """Round-2 review: the header cap counted local and central headers, so ~200 000 entries — an
    ordinary 18 MiB ZIP — was refused. The cap now follows what the 25 MiB scan limit can hold."""
    data = F.zip_entries(210_000)
    assert len(data) < 25 * MiB
    t = time.perf_counter()
    assert inspect(data) == (None, True)
    assert time.perf_counter() - t < 3.0
    import io
    import zipfile
    assert len(zipfile.ZipFile(io.BytesIO(F.zip_entries(50))).infolist()) == 50     # the builder writes real ZIPs


def test_could_not_be_checked_is_reported_as_such(double):
    """Round-2 review: "could not be checked" used the password sentence and was not recorded."""
    sc = M.ClamdScanner(double.address, timeout_s=5)
    res = sc.scan(F.sevenzip(encrypted=False, header_coder="delta+lzma"))
    assert (res.status, res.reason, res.checked) == ("encrypted", "archive", False) and res.detail
    assert res.as_record()["checked"] is False and M.unchecked(res) and M.unchecked(res.as_record())
    enc = sc.scan(F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST"})))
    assert (enc.status, enc.checked) == ("encrypted", True) and not M.unchecked(enc)


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
    assert encrypted_kind(F.pdf(xref=False)) is None
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


def test_readable_files_stay_readable():
    """The negative set: files that open without a password must not be refused."""
    import zipfile
    for data in (F.zip_bytes({"a.txt": b"TEST"}), F.ooxml(), F.odf(False), F.xls(filepass=False), F.doc(encrypted=False),
                 F.ppt(), F.pdf(), F.sevenzip(encrypted=False), F.rar4(), F.rar5(), F.lo_xls("structure"),
                 F.xls_filepass("rc4", "VelvetSweatshop"), _mupdf_pdf("PDF_ENCRYPT_AES_128"),
                 F.pdf(content=b"BT (see /Encrypt and EncryptedPackage and PK) Tj ET"),
                 F.doc(encrypted=False, text="Rar! 7z EncryptedPackage /Encrypt")):
        assert encrypted_kind(data) is None, data[:16]
    # an EPUB with obfuscated fonts (META-INF/encryption.xml is font obfuscation, not a password) and a JAR
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", b"application/epub+zip")
        z.writestr("META-INF/encryption.xml", b'<encryption><enc:EncryptedData><enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/></enc:EncryptedData></encryption>')
        z.writestr("OEBPS/font.otf", os.urandom(200))
    assert encrypted_kind(buf.getvalue()) is None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
        z.writestr("a/B.class", os.urandom(300))
    assert encrypted_kind(buf.getvalue()) is None


def test_malformed_input_never_raises():
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
    # a signature alone, with nothing a reader would accept after it, is not a format
    for magic in (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00", b"%PDF-1.7"):
        assert encrypted_kind(magic) is None and encrypted_kind(magic + b"\xff" * 4096) is None
        assert encrypted_kind(magic + os.urandom(2048)) in (None, "archive", "office", "pdf")
    assert encrypted_kind(b"") is None and encrypted_kind(b"name,email\nTEST,t@example.test\n") is None
    assert seen > 500


ADVERSARIAL = {
    # the review's measurements before the bounds: 2.1 s / ~350 MB for the 7z header bomb; 1.2 s /
    # ~1 GB for the FAT bomb, 3.6 s / ~870 MB for the central directory, 1.9 s / ~1.1 GB for the
    # trailer keywords (100 MiB inputs); now every class answers within the budget below
    "7z header of millions of coders": (F.sevenzip_header_bomb, ("archive", False)),
    "7z asking for a 4 GiB dictionary": (F.sevenzip_bigdict, (None, True)),
    "CFB whose FAT repeats one sector": (lambda: F.cfb_fat_bomb(25 * MiB), (None, True)),
    "ZIP with a huge central directory": (lambda: F.zip_many(25 * MiB), (None, True)),
    "ZIP of more headers than 25 MiB of entries hold": (lambda: F.zip_local_flood(25 * MiB), ("archive", False)),
    "PDF of trailer keywords naming /Encrypt": (lambda: F.pdf_trailers(25 * MiB, True), ("pdf", True)),
    "PDF of trailer keywords": (lambda: F.pdf_trailers(25 * MiB, False), (None, True)),
    "PDF with millions of '1 0 obj'": (lambda: F.pdf_object_bomb(25 * MiB), ("pdf", True)),
    "PDF of missing references": (lambda: F.pdf_missing_references(25 * MiB), ("pdf", True)),
    "PDF of near-miss key names": (lambda: F.pdf_name_noise(25 * MiB), (None, True)),
    "RAR signatures with endless blocks": (lambda: F.rar_blocks(25 * MiB), ("archive", False)),
    "ODF manifest inflating to 64 MiB": (F.odf_manifest_bomb, ("office", False)),
    "more 7z signatures than followed": (F.many_7z_signatures, ("archive", False)),
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_inputs_stay_within_time_and_memory(name):
    build, want = ADVERSARIAL[name]
    data = build()
    tracemalloc.start()
    t = time.perf_counter()
    try:
        got = inspect(data)
        elapsed = time.perf_counter() - t
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert got == want, got
    assert elapsed < 3.0, f"{name}: {elapsed:.2f}s"                  # measured well under 0.5 s (tracing costs more)
    assert peak < 96 * MiB, f"{name}: {peak / MiB:.0f} MiB"          # measured ≤ 48 MiB (the 16 MiB 7z header cap)


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
    plain = F.zip_bytes({"inner.zip": b"TEST FIXTURE"})
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
    # beyond the scan limit nothing is inspected: the file is too large, not encrypted
    assert M.ClamdScanner(double.address, timeout_s=5, max_bytes=64).scan(enc + bytes(64)).status == "too_large"


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
    reference compose configures it: an encrypted archive compressed inside another one is the
    engine's to find, a PDF with only an owner password stays clean."""
    sc = _real_clamd()
    enc = F.flag_encrypted(F.zip_bytes({"TEST.txt": b"TEST FIXTURE " * 40}))
    assert sc.scan(enc).status == "encrypted"
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:          # compressed: the detector cannot see it
        z.writestr("inner.zip", enc)
        z.writestr("readme.txt", b"TEST FIXTURE")
    assert encrypted_kind(buf.getvalue()) is None
    nested = sc.scan(buf.getvalue())
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


def _ends_unread(c, pid: str, sid: str, kind: str) -> dict:
    f = _wait(c, pid, sid, ("error", "ready", "rejected", "needs_ocr"))
    assert f["status"] == "error" and f["error"] == SENTENCE, f
    assert f["scan"]["status"] == "encrypted" and f["scan"]["reason"] == kind and not f["structure"], f
    r = c.get(f"/api/sources/{sid}/download")
    assert r.status_code == 410 and r.json()["error"] == "encrypted_file"
    assert Q.verdict(c.store, f["sha256"]) is None, "not malware: no quarantine verdict"
    return f


def test_an_encrypted_source_is_checked_after_the_engine_and_never_served(api, double):
    """Sources are accepted asynchronously: the engine is asked first (a signature wins), then the
    container; a password-protected file ends in `error` and nothing reads it."""
    c = api
    pid = c.api.project("加密")
    for name, data, kind in (("客户.xlsx", F.encrypted_ooxml(), "office"), ("旧表.xls", F.xls(filepass=True), "office"),
                             ("合同.pdf", _mupdf_pdf("PDF_ENCRYPT_AES_256", user="u"), "pdf"),
                             ("资料.docx", F.ooxml(encrypted_entry=True), "office")):
        before = len(double.scans)
        out = c.post(f"/api/projects/{pid}/sources", files=[("files", (name, data))]).json()[0]
        assert out["status"] == "scanning"
        _ends_unread(c, pid, out["id"], kind)
        assert len(double.scans) == before + 1, "the engine saw the bytes first"
    # a PDF with only an owner password opens without one: accepted and read
    owner = c.post(f"/api/projects/{pid}/sources", files=[("files", ("只读.pdf", _mupdf_pdf("PDF_ENCRYPT_AES_256")))]).json()[0]
    f = _wait(c, pid, owner["id"], ("ready", "needs_ocr", "error"))
    assert f["status"] in ("ready", "needs_ocr") and f["scan"]["status"] == "clean", f
    assert c.get(f"/api/sources/{owner['id']}/download").status_code == 200
    # a workbook protected only in its structure (default password) is read as usual
    struct_xls = c.post(f"/api/projects/{pid}/sources", files=[("files", ("结构保护.xls", F.lo_xls("structure")))]).json()[0]
    assert _wait(c, pid, struct_xls["id"], ("ready", "error", "needs_ocr", "unsupported"))["scan"]["status"] == "clean"


def test_a_signature_on_encrypted_bytes_is_malware_and_quarantined(api, double):
    """The review found the first version refusing a ZIP with EICAR next to an encrypted entry as
    merely "encrypted" — no rejected record, no quarantine of bytes shared with older rows."""
    c = api
    pid = c.api.project("签名优先")
    data = F.zip_bytes({"eicar.com": EICAR, "inner.zip": F.flag_encrypted(F.zip_bytes({"a.txt": b"TEST"}))})
    assert encrypted_kind(data) == "archive"
    sha = hashlib.sha256(data).hexdigest()
    double.infected[sha] = "Win.Test.EICAR_HDB-1"                      # what the engine answers for the member
    out = c.post(f"/api/projects/{pid}/sources", files=[("files", ("打包.zip", data))]).json()[0]
    f = _wait(c, pid, out["id"], ("rejected", "error", "ready"))
    assert f["status"] == "rejected" and f["scan"]["status"] == "infected", f
    assert not c.store.has_blob(sha)
    again = c.post(f"/api/projects/{pid}/sources", files=[("files", ("再传.zip", data))])
    assert again.status_code == 400 and again.json()["error"] == "malware_detected"


def test_machine_uploads_resumable_uploads_and_server_imports_meet_the_same_gate(api, monkeypatch, tmp_path):
    c, h = api, api.key
    pid = c.post("/api/v1/projects", json={"name": "加密"}, headers=h).json()["id"]
    data = F.encrypted_ooxml()
    first = c.post(f"/api/v1/projects/{pid}/files", files={"file": ("客户.xlsx", data)}, headers=h)
    assert first.status_code == 201, first.text
    _ends_unread(c, pid, first.json()["id"], "office")
    assert c.get(f"/api/v1/files/{first.json()['id']}/content", headers=h).status_code == 410
    # a resumable upload: complete stores the verified bytes; using them as a source meets the gate
    other = F.xls(filepass=True, write_protect=True)
    up = c.post("/api/v1/uploads", json={"filename": "旧表.xls", "size": len(other), "purpose": "source"}, headers=h).json()
    assert c.put(f"/api/v1/uploads/{up['id']}", content=other, headers={**h, "Content-Range": f"bytes 0-{len(other) - 1}/{len(other)}",
                                                                           "Content-Type": "application/octet-stream"}).status_code == 200
    assert c.post(f"/api/v1/uploads/{up['id']}/complete", json={}, headers=h).status_code == 200
    used = c.post(f"/api/v1/projects/{pid}/files", json={"upload_id": up["id"]}, headers=h)
    assert used.status_code == 201, used.text
    _ends_unread(c, pid, used.json()["id"], "office")
    # a server-side import from an allowed directory
    import synwevo_docs.api.v1 as v1
    monkeypatch.setattr(v1, "IMPORT_ROOTS", [str(tmp_path)])
    (tmp_path / "合同.docx").write_bytes(F.ooxml(encrypted_entry=True))
    imported = c.post(f"/api/v1/projects/{pid}/files", json={"path": str(tmp_path / "合同.docx")}, headers=h)
    assert imported.status_code == 201, imported.text
    _ends_unread(c, pid, imported.json()["id"], "office")
    # templates are checked on the request
    _refused(c.post("/api/v1/templates", files={"file": ("合同.docx", data)}, headers=h), "office")
    assert c.store.one("SELECT COUNT(*) AS n FROM templates")["n"] == 0


def test_an_encrypted_template_is_refused_with_the_acceptance_code(api):
    c = api
    r = c.post("/api/templates/upload", files={"file": ("合同.docx", F.encrypted_ooxml())})
    _refused(r, "office", "合同.docx")
    r = c.post("/api/templates/upload", files={"file": ("发货单.pdf", _mupdf_pdf("PDF_ENCRYPT_RC4_128", user="u"))})
    _refused(r, "pdf", "发货单.pdf")
    assert c.store.one("SELECT COUNT(*) AS n FROM templates")["n"] == 0


UNCHECKED = "文件的结构无法安全地完成检查（可能已损坏、被截断或结构异常），因此未被接纳；如果它没有设密码，请用生成它的程序重新保存（或拆分成较小的文件）后重新上传"


def test_a_file_that_could_not_be_checked_says_so_and_text_about_pdfs_is_accepted(api):
    """Round-2 review: a CSV whose cell mentions /Encrypt ended in error as "encrypted"; and a file
    the check could not finish told its reader to remove a password it does not have."""
    c = api
    pid = c.api.project("无法检查")
    out = c.post(f"/api/projects/{pid}/sources", files=[("files", ("打包.7z", F.sevenzip(encrypted=False, header_coder="delta+lzma")))]).json()[0]
    f = _wait(c, pid, out["id"], ("error", "ready", "unsupported"))
    assert f["status"] == "error" and f["error"] == UNCHECKED, f
    assert (f["scan"]["status"], f["scan"]["reason"], f["scan"]["checked"]) == ("encrypted", "archive", False), f["scan"]
    row = c.store.one("SELECT scan FROM source_files WHERE id=?", (out["id"],))
    assert row["scan"]["detail"], "what stopped the check is recorded for the operator"
    r = c.get(f"/api/sources/{out['id']}/download")
    assert r.status_code == 410 and r.json()["error"] == "unchecked_file" and r.json()["message"] == UNCHECKED
    t = c.post("/api/templates/upload", files={"file": ("打包.7z", F.sevenzip(encrypted=False, header_coder="delta+lzma"))})
    assert t.status_code == 400 and t.json()["error"] == "unchecked_file" and t.json()["message"] == UNCHECKED
    assert t.json()["detail"] == {"filename": "打包.7z", "kind": "archive", "checked": False}
    # text that talks about PDF internals is read as the CSV it is
    csv = c.post(f"/api/projects/{pid}/sources", files=[("files", ("笔记.csv", F.text_about_pdf_internals()["log.csv"]))]).json()[0]
    g = _wait(c, pid, csv["id"], ("ready", "error"))
    assert g["status"] == "ready" and g["scan"]["status"] == "clean", g


class _SlowScanner:
    """TEST DOUBLE wrapper: the real adapter against the protocol double, one second slower — as a
    busy clamd (or a large file) is. Whatever runs it on the event loop holds every other request."""

    def __init__(self, inner):
        self.inner = inner
        self.max_bytes = inner.max_bytes

    def scan(self, data, sha256=None):
        time.sleep(1.0)
        return self.inner.scan(data, sha256=sha256)


def _concurrent_get(c, send) -> float:
    """Start `send` in a thread, then time a cheap GET issued while it is running."""
    done = threading.Event()
    th = threading.Thread(target=lambda: (send(), done.set()))
    th.start()
    time.sleep(0.2)
    t = time.perf_counter()
    assert c.get("/api/projects").status_code == 200
    waited = time.perf_counter() - t
    th.join(timeout=30)
    assert done.is_set()
    return waited


def test_uploads_are_checked_off_the_event_loop(api):
    """The review measured a concurrent GET waiting 37.9 s behind one upload request: the check
    ran on the event loop of the single worker. A slow scan now holds only its own request."""
    c = api
    c.app_.scanner = _SlowScanner(c.app_.scanner)
    tpl = F.encrypted_ooxml()
    assert _concurrent_get(c, lambda: c.post("/api/templates/upload", files={"file": ("合同.docx", tpl)})) < 0.6
    assert _concurrent_get(c, lambda: c.post("/api/v1/templates", files={"file": ("合同.docx", tpl)}, headers=c.key)) < 0.6


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


def test_an_engine_only_encrypted_verdict_leaves_the_source_unread_but_not_malware(api, double):
    """Bytes the detector does not recognise but the engine alerts on (AlertEncryptedArchive on
    an archive compressed inside a document): the row ends in `error`, is never parsed or served,
    and is not treated as malware — no quarantine, no rejected digest, nobody told of malware."""
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


def test_a_rescan_says_which_findings_could_not_be_checked(hist, double, monkeypatch, capsys):
    c = hist
    pid = c.api.project("历史")
    src = c.api.upload_bytes(pid, "打包.7z", F.sevenzip(encrypted=False, header_coder="delta+lzma"))[0]
    monkeypatch.setenv("SYNWEVO_CLAMD_ADDRESS", double.address)
    monkeypatch.setenv("SYNWEVO_CLAMD_TIMEOUT_S", "5")
    code, out = _rescan(c, capsys)
    assert code == 1 and src["id"] in out and "结构无法检查" in out, out
    rec = c.store.one("SELECT scan FROM source_files WHERE id=?", (src["id"],))["scan"]
    assert (rec["status"], rec["checked"], rec["access"]) == ("encrypted", False, "unchanged") and rec["detail"]
    assert c.get(f"/api/sources/{src['id']}/download").status_code == 200


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
