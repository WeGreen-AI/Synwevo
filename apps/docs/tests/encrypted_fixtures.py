"""TEST FIXTURES: password-protected (and look-alike plain) files, built from the format
descriptions in pure Python so no archiver or office suite is needed in CI.

Each builder writes the structure a real writer produces; the payloads are stand-ins (random or
plain bytes where ciphertext would be). What makes them realistic was checked against real
readers and writers while this was written (2026-09-29): the CFB files open in `olefile`
(asserted again in the tests), the 7z and RAR archives list in libarchive 3.7.4 (`bsdtar -tvf`,
which reports "The file content is encrypted" / "Encryption is not supported" for the encrypted
ones), and the detector answers the same for files written by LibreOffice 25.x (DOC, XLS, DOCX,
XLSX, PPTX, ODT, ODS and PDF with an open password; owner-password PDF), MuPDF (PDF revisions 2,
3, 4, 6 with and without a user password), Info-ZIP `zip -P` and libarchive's WinZip AES.

Shared by test_encrypted_files.py here and loaded by path from tests/integration and
apps/messages/tests (as clamd_double.py is).
"""
from __future__ import annotations

import io
import lzma
import os
import struct
import zipfile
import zlib

# ============================================================================ ZIP
def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def flag_encrypted(data: bytes, *, central: bool = True, local: bool = True, only: str | None = None) -> bytes:
    """Set general-purpose bit 0 ("encrypted", as ZipCrypto and WinZip AES do) on the entries —
    in the central directory, the local headers or both."""
    out = bytearray(data)
    z = zipfile.ZipFile(io.BytesIO(data))
    for info in z.infolist():
        if only and info.filename != only:
            continue
        if local:
            off = info.header_offset
            struct.pack_into("<H", out, off + 6, struct.unpack_from("<H", out, off + 6)[0] | 1)
    if central:
        pos = data.rfind(b"PK\x05\x06")
        count, _size, cd = struct.unpack_from("<HII", data, pos + 10)
        p = cd
        for _ in range(count):
            nlen, xlen, clen = struct.unpack_from("<HHH", data, p + 28)
            name = data[p + 46:p + 46 + nlen].decode()
            if not only or name == only:
                struct.pack_into("<H", out, p + 8, struct.unpack_from("<H", out, p + 8)[0] | 1)
            p += 46 + nlen + xlen + clen
    return bytes(out)


def ooxml(encrypted_entry: bool = False) -> bytes:
    data = zip_bytes({"[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      b'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
                      "word/document.xml": b"<w:document>TEST FIXTURE</w:document>"})
    return flag_encrypted(data, only="word/document.xml") if encrypted_entry else data


def odf(encrypted: bool) -> bytes:
    """An OpenDocument text package: encrypted parts carry no ZIP flag, the manifest says so."""
    enc = (b'<manifest:encryption-data manifest:checksum-type="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha256-1k" '
           b'manifest:checksum="AAAA"><manifest:algorithm manifest:algorithm-name="http://www.w3.org/2001/04/xmlenc#aes256-cbc" '
           b'manifest:initialisation-vector="AAAA"/></manifest:encryption-data>') if encrypted else b""
    manifest = (b'<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                b'<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                b'<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml" manifest:size="40">' + enc
                + b"</manifest:file-entry></manifest:manifest>")
    return zip_bytes({"mimetype": b"application/vnd.oasis.opendocument.text",
                      "content.xml": os.urandom(40) if encrypted else b"<office:document-content>TEST</office:document-content>",
                      "META-INF/manifest.xml": manifest})


# ============================================================================ OLE2 / CFB (MS-CFB, version 3)
FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


def cfb(tree: dict) -> bytes:
    """A compound file. `tree`: name → bytes (a stream) or dict (a storage). Streams under 4096
    bytes go to the mini stream, as real writers put them."""
    SS, MS, CUTOFF = 512, 64, 4096
    entries: list[dict] = [{"name": "Root Entry", "type": 5, "items": list(tree.items())}]
    queue = [0]
    while queue:
        i = queue.pop(0)
        kids = []
        for name, val in entries[i].pop("items", []):
            kids.append(len(entries))
            entries.append({"name": name, "type": 1, "items": list(val.items())} if isinstance(val, dict) else {"name": name, "type": 2, "data": val})
            if isinstance(val, dict):
                queue.append(kids[-1])
        entries[i]["kids"] = kids
    for e in entries:
        e.setdefault("left", NOSTREAM), e.setdefault("right", NOSTREAM), e.setdefault("child", NOSTREAM)

    def bst(ids: list[int]) -> int:                   # siblings: a binary search tree ordered as MS-CFB says
        if not ids:
            return NOSTREAM
        m = len(ids) // 2
        entries[ids[m]]["left"], entries[ids[m]]["right"] = bst(ids[:m]), bst(ids[m + 1:])
        return ids[m]
    for e in entries:
        if "kids" in e:
            e["child"] = bst(sorted(e["kids"], key=lambda k: (len(entries[k]["name"]), entries[k]["name"].upper())))

    sectors: list[bytes] = []
    fat: list[int] = []

    def alloc(data: bytes) -> int:
        if not data:
            return ENDOFCHAIN
        first = len(sectors)
        chunks = [data[i:i + SS].ljust(SS, b"\0") for i in range(0, len(data), SS)]
        for k, ch in enumerate(chunks):
            sectors.append(ch)
            fat.append(first + k + 1 if k + 1 < len(chunks) else ENDOFCHAIN)
        return first

    mini = bytearray()
    minifat: list[int] = []
    for e in entries:
        if e["type"] != 2:
            continue
        d = e["data"]
        e["size"] = len(d)
        if not d:
            e["start"] = ENDOFCHAIN
        elif len(d) >= CUTOFF:
            e["start"] = alloc(d)
        else:
            first = len(minifat)
            n = (len(d) + MS - 1) // MS
            mini += d.ljust(n * MS, b"\0")
            minifat += [first + k + 1 if k + 1 < n else ENDOFCHAIN for k in range(n)]
            e["start"] = first
    entries[0]["start"], entries[0]["size"] = (alloc(bytes(mini)), len(mini)) if mini else (ENDOFCHAIN, 0)
    first_minifat = alloc(b"".join(struct.pack("<I", x) for x in minifat).ljust(((len(minifat) * 4 + SS - 1) // SS) * SS, b"\xff")) if minifat else ENDOFCHAIN
    n_minifat = (len(minifat) * 4 + SS - 1) // SS
    raw = b""
    for e in entries:
        name = e["name"].encode("utf-16-le") + b"\0\0"
        raw += (name.ljust(64, b"\0") + struct.pack("<HBB", len(name), e["type"], 1)
                + struct.pack("<III", e["left"], e["right"], e["child"]) + bytes(16) + struct.pack("<I", 0) + bytes(16)
                + struct.pack("<IQ", e.get("start", ENDOFCHAIN) if e["type"] != 1 else 0, e.get("size", 0)))
    while len(raw) % SS:
        raw += bytes(64) + struct.pack("<HBB", 0, 0, 0) + struct.pack("<III", NOSTREAM, NOSTREAM, NOSTREAM) + bytes(48)
    first_dir = alloc(raw)
    n_fat = 1
    while (len(sectors) + n_fat) > n_fat * (SS // 4):
        n_fat += 1
    fat_sids = list(range(len(sectors), len(sectors) + n_fat))
    fat += [FATSECT] * n_fat
    fat += [FREESECT] * (n_fat * (SS // 4) - len(fat))
    fat_bytes = b"".join(struct.pack("<I", x) for x in fat)
    sectors += [fat_bytes[i:i + SS] for i in range(0, len(fat_bytes), SS)]
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16) + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
              + struct.pack("<IIIIIIIII", 0, n_fat, first_dir, 0, CUTOFF, first_minifat, n_minifat, ENDOFCHAIN, 0)
              + b"".join(struct.pack("<I", s) for s in (fat_sids + [FREESECT] * (109 - n_fat))))
    return header + b"".join(sectors)


def _rec(rtype: int, payload: bytes = b"") -> bytes:
    return struct.pack("<HH", rtype, len(payload)) + payload


def xls_workbook(*, filepass: bool, write_protect: bool = False, pad: int = 0) -> bytes:
    """A BIFF8 workbook globals substream: BOF, [WriteProtect], [FilePass], InterfaceHdr, EOF."""
    out = _rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 6))
    if write_protect:
        out += _rec(0x0086)
    if filepass:
        out += _rec(0x002F, struct.pack("<HHH", 1, 4, 2) + os.urandom(48))       # RC4 CryptoAPI header stand-in
    out += _rec(0x00E1, b"\xb0\x04") + _rec(0x000A)
    return out + bytes(pad)


def xls(**kw) -> bytes:
    return cfb({"Workbook": xls_workbook(**kw), "\x05SummaryInformation": bytes(200)})


def word_document(*, encrypted: bool, text: str = "TEST FIXTURE") -> bytes:
    """A WordDocument stream: the FIB (wIdent 0xA5EC; fEncrypted is bit 8 of the flags at 0x0A)
    followed by the document's text as UTF-16, as Word stores it."""
    fib = bytearray(1472)
    struct.pack_into("<HHHHH", fib, 0, 0xA5EC, 0x00C1, 0, 0x0409, 0)
    struct.pack_into("<H", fib, 10, 0x0200 | (0x0100 if encrypted else 0))
    return (bytes(fib) + text.encode("utf-16-le")).ljust(5120, b"\0")


def doc(*, encrypted: bool, text: str = "TEST FIXTURE") -> bytes:
    return cfb({"WordDocument": word_document(encrypted=encrypted, text=text), "1Table": bytes(700)})


def ppt(*, encrypted_token: bool = False, encrypted_summary: bool = False) -> bytes:
    """Current User: RecordHeader (type 0x0FF6), size 0x14, headerToken — 0xF3D1C4DF when encrypted."""
    token = 0xF3D1C4DF if encrypted_token else 0xE391C05F
    cu = struct.pack("<HHI", 0, 0x0FF6, 0x24) + struct.pack("<II", 0x14, token) + bytes(28)
    tree = {"Current User": cu, "PowerPoint Document": bytes(5000)}
    if encrypted_summary:
        tree["EncryptedSummary"] = os.urandom(300)
    return cfb(tree)


def encrypted_ooxml() -> bytes:
    """What Office (and LibreOffice) write for a password-protected DOCX/XLSX/PPTX: a compound file
    with EncryptionInfo and EncryptedPackage (MS-OFFCRYPTO)."""
    return cfb({"EncryptionInfo": b"\x04\x00\x04\x00\x40\x00\x00\x00" + os.urandom(300), "EncryptedPackage": os.urandom(6000),
                "\x06DataSpaces": {"Version": bytes(76), "DataSpaceMap": bytes(112)}})


# ============================================================================ PDF (hand-written)
def pdf(*, encrypt: bytes | None = None, content: bytes = b"BT /F1 12 Tf 72 720 Td (TEST FIXTURE) Tj ET", xref: bool = True) -> bytes:
    """A one-page PDF; `encrypt` is the encryption dictionary's text (object 5)."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"]
    if encrypt is not None:
        objs.append(encrypt)
    out = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    trailer = b"<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R"
    if encrypt is not None:
        trailer += b" /Encrypt 5 0 R /ID [<00112233445566778899aabbccddeeff> <00112233445566778899aabbccddeeff>]"
    trailer += b" >>"
    if not xref:
        return out + b"trailer\n" + trailer + b"\n%%EOF\n"
    at = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n" + trailer + b"\nstartxref\n" + str(at).encode() + b"\n%%EOF\n"


# ============================================================================ 7z (7zFormat.txt)
AES, LZMA, LZMA2 = b"\x06\xf1\x07\x01", b"\x03\x01\x01", b"\x21"


def _num(v: int) -> bytes:
    """7z UINT64: the leading 1-bits of the first byte say how many little-endian bytes follow."""
    for n in range(9):
        if n == 8 or v < (1 << (7 * (n + 1))):
            break
    if n == 0:
        return bytes([v])
    if n == 8:
        return b"\xff" + struct.pack("<Q", v)
    return bytes([((0xFF << (8 - n)) & 0xFF) | (v >> (8 * n))]) + (v & ((1 << (8 * n)) - 1)).to_bytes(n, "little")


def _streams(pack_pos: int, pack_size: int, coders: list[tuple[bytes, bytes]], unpack_sizes: list[int], crc: int | None = None) -> bytes:
    """One folder: coder 0 gives the folder's output and coder i reads coder i+1's output, so the
    packed stream enters the last coder ([LZMA2, AES]: packed → AES → LZMA2 → file)."""
    folder = _num(len(coders)) + b"".join(bytes([len(c) | (0x20 if p else 0)]) + c + (_num(len(p)) + p if p else b"") for c, p in coders)
    for i in range(len(coders) - 1):
        folder += _num(i) + _num(i + 1)
    sub = (b"\x08\x0a\x01" + struct.pack("<I", crc) + b"\x00") if crc is not None else b""
    return (b"\x06" + _num(pack_pos) + _num(1) + b"\x09" + _num(pack_size) + b"\x00"
            + b"\x07\x0b" + _num(1) + b"\x00" + folder + b"\x0c" + b"".join(_num(s) for s in unpack_sizes) + b"\x00" + sub + b"\x00")


def sevenzip(*, encrypted: bool, encode_header: bool = True, encrypt_header: bool = False) -> bytes:
    payload = os.urandom(48)
    aes_props = bytes([0x40 | 19, 0x0F]) + os.urandom(16)
    coders = [(LZMA2, b"\x10"), (AES, aes_props)] if encrypted else [(LZMA2, b"\x10")]
    name = b"\x00" + "TEST.txt\0".encode("utf-16-le")
    header = (b"\x01\x04" + _streams(0, len(payload), coders, [60, len(payload)][:len(coders)], crc=0x12345678)
              + b"\x05" + _num(1) + b"\x11" + _num(len(name)) + name + b"\x00" + b"\x00")
    body, next_header = payload, header
    if encode_header or encrypt_header:              # 7-Zip's default: the header itself LZMA-packed
        props = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 16)
        packed = lzma.compress(header, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3, "lp": 0, "pb": 2}])
        hcoders = [(LZMA, props)] + ([(AES, aes_props)] if encrypt_header else [])
        next_header = b"\x17" + _streams(len(payload), len(packed), hcoders, [len(header), len(packed)][:len(hcoders)])
        body = payload + packed
    start = struct.pack("<QQI", len(body), len(next_header), zlib.crc32(next_header))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + body + next_header


# ============================================================================ RAR 4 (technote) and RAR 5 (rarlab.com)
def rar4(*, file_encrypted: bool = False, headers_encrypted: bool = False) -> bytes:
    def block(htype: int, flags: int, body: bytes) -> bytes:
        head = struct.pack("<BHH", htype, flags, 7 + len(body)) + body
        return struct.pack("<H", zlib.crc32(head) & 0xFFFF) + head
    out = b"Rar!\x1a\x07\x00" + block(0x73, 0x0080 if headers_encrypted else 0, bytes(6))
    if headers_encrypted:
        return out + os.urandom(64)
    data, name = b"TEST FIXTURE\n", b"TEST.txt"
    body = struct.pack("<IIBIIBBHI", len(data), len(data), 3, zlib.crc32(data), 0, 29, 0x30, len(name), 0x81A4) + name
    if file_encrypted:
        body += os.urandom(8)                        # salt
    out += block(0x74, 0x8000 | (0x0004 | 0x0400 if file_encrypted else 0), body) + data
    return out + block(0x7B, 0x4000, b"")


def _vint(v: int) -> bytes:
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def rar5(*, file_encrypted: bool = False, headers_encrypted: bool = False) -> bytes:
    def header(htype: int, fields: bytes, extra: bytes = b"", data_size: int | None = None) -> bytes:
        body = _vint(htype) + _vint((0x0001 if extra else 0) | (0x0002 if data_size is not None else 0))
        body += (_vint(len(extra)) if extra else b"") + (_vint(data_size) if data_size is not None else b"") + fields + extra
        sized = _vint(len(body)) + body
        return struct.pack("<I", zlib.crc32(sized)) + sized
    out = b"Rar!\x1a\x07\x01\x00"
    if headers_encrypted:
        return out + header(4, _vint(0) + _vint(0x0001) + bytes([15]) + os.urandom(28)) + os.urandom(64)
    out += header(1, _vint(0))
    data, name = b"TEST FIXTURE\n", b"TEST.txt"
    fields = _vint(0x0004) + _vint(len(data)) + _vint(0o644) + struct.pack("<I", zlib.crc32(data)) + _vint(0) + _vint(1) + _vint(len(name)) + name
    extra = b""
    if file_encrypted:
        rec = _vint(0x01) + _vint(0) + _vint(0x0001) + bytes([15]) + os.urandom(16 + 16 + 12)
        extra = _vint(len(rec)) + rec
    return out + header(2, fields, extra, data_size=len(data)) + data + header(5, _vint(0))
