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

import base64
import hashlib
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


def odf(encrypted: bool, pad: int = 0) -> bytes:
    """An OpenDocument text package: encrypted parts carry no ZIP flag, the manifest says so.
    `pad` puts that many spaces in a comment before the declaration (a manifest over any cap)."""
    enc = (b'<manifest:encryption-data manifest:checksum-type="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0#sha256-1k" '
           b'manifest:checksum="AAAA"><manifest:algorithm manifest:algorithm-name="http://www.w3.org/2001/04/xmlenc#aes256-cbc" '
           b'manifest:initialisation-vector="AAAA"/></manifest:encryption-data>') if encrypted else b""
    manifest = (b'<?xml version="1.0" encoding="UTF-8"?>' + (b"<!--" + b" " * pad + b"-->" if pad else b"")
                + b'<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
                b'<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                b'<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml" manifest:size="40">' + enc
                + b"</manifest:file-entry></manifest:manifest>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), b"application/vnd.oasis.opendocument.text")
        z.writestr(zipfile.ZipInfo("content.xml"), os.urandom(40) if encrypted else b"<office:document-content>TEST</office:document-content>")
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


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


def _rc4(key: bytes, data: bytes) -> bytes:
    s, j, out = list(range(256)), 0, bytearray()
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    for b in data:
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) & 0xFF])
    return bytes(out)


def filepass(scheme: str, password: str) -> bytes:
    """A BIFF8 FilePass payload written the way MS-OFFCRYPTO describes the writer's side, for
    `password`: "xor" (obfuscation), "rc4" (Office 97/2000) or "cryptoapi" (RC4 CryptoAPI).
    Excel writes structure protection as "VelvetSweatshop" - such a file opens without asking."""
    if scheme == "xor":
        raw = password.encode("latin-1")
        v = 0
        for b in reversed(bytes([len(raw)]) + raw):
            v = (((v >> 14) & 1) | ((v << 1) & 0x7FFF)) ^ b
        return struct.pack("<HHH", 0, 0x1234, v ^ 0xCE4B)
    salt, verifier = os.urandom(16), os.urandom(16)
    pw = password.encode("utf-16-le")
    if scheme == "rc4":
        h1 = hashlib.md5((hashlib.md5(pw).digest()[:5] + salt) * 16).digest()
        key = hashlib.md5(h1[:5] + struct.pack("<I", 0)).digest()
        enc = _rc4(key, verifier + hashlib.md5(verifier).digest())
        return struct.pack("<HHH", 1, 1, 1) + salt + enc
    key = hashlib.sha1(hashlib.sha1(salt + pw).digest() + struct.pack("<I", 0)).digest()[:16]
    enc = _rc4(key, verifier + hashlib.sha1(verifier).digest())
    csp = "Microsoft Enhanced Cryptographic Provider v1.0\0".encode("utf-16-le")
    header = struct.pack("<IIIIIIII", 0x04, 0, 0x6801, 0x8004, 128, 1, 0, 0) + csp
    return (struct.pack("<HHH", 1, 4, 2) + struct.pack("<II", 0x04, len(header)) + header
            + struct.pack("<I", 16) + salt + enc[:16] + struct.pack("<I", 20) + enc[16:])


def xls_filepass(scheme: str, password: str) -> bytes:
    stream = (_rec(0x0809, struct.pack("<HHHHII", 0x0600, 0x0005, 0x0DBB, 0x07CC, 0, 6)) + _rec(0x002F, filepass(scheme, password))
              + _rec(0x00E1, b"\xb0\x04") + _rec(0x000A))
    return cfb({"Workbook": stream})


# Real LibreOffice 25.x output (TEST FIXTURE content, no personal metadata), made for the 2026-09-29
# security review: an XLS with workbook-structure protection only - written as RC4 encryption under
# the default password, and opens without asking - and one saved with a real password.
LO_XLS_STRUCTURE_PROTECTED = (
    "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"
)
LO_XLS_PASSWORD = (
    "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"
)


def lo_xls(which: str) -> bytes:
    return zlib.decompress(base64.b64decode("".join(LO_XLS_STRUCTURE_PROTECTED if which == "structure" else LO_XLS_PASSWORD)))


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


def sevenzip(*, encrypted: bool, encode_header: bool = True, encrypt_header: bool = False, header_coder: str = "lzma") -> bytes:
    """`header_coder` packs the header: "lzma" (7-Zip's default), "copy", "deflate", or
    "delta+lzma" (two coders)."""
    payload = os.urandom(48)
    aes_props = bytes([0x40 | 19, 0x0F]) + os.urandom(16)
    coders = [(LZMA2, b"\x10"), (AES, aes_props)] if encrypted else [(LZMA2, b"\x10")]
    name = b"\x00" + "TEST.txt\0".encode("utf-16-le")
    header = (b"\x01\x04" + _streams(0, len(payload), coders, [60, len(payload)][:len(coders)], crc=0x12345678)
              + b"\x05" + _num(1) + b"\x11" + _num(len(name)) + name + b"\x00" + b"\x00")
    body, next_header = payload, header
    if encode_header or encrypt_header:              # 7-Zip's default: the header itself LZMA-packed
        props = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 16)

        def lz(b: bytes) -> bytes:
            return lzma.compress(b, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3, "lp": 0, "pb": 2}])
        if header_coder == "copy":
            packed, hcoders, sizes = header, [(b"\x00", b"")], [len(header)]
        elif header_coder == "deflate":
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            packed, hcoders, sizes = c.compress(header) + c.flush(), [(b"\x04\x01\x08", b"")], [len(header)]
        elif header_coder == "delta+lzma":             # Delta (distance 1) reads LZMA's output
            packed = lz(bytes((header[i] - (header[i - 1] if i else 0)) & 0xFF for i in range(len(header))))
            hcoders, sizes = [(b"\x03", b"\x00"), (LZMA, props)], [len(header), len(header)]
        else:
            packed = lz(header)
            hcoders = [(LZMA, props)] + ([(AES, aes_props)] if encrypt_header else [])
            sizes = [len(header), len(packed)][:len(hcoders)]
        next_header = b"\x17" + _streams(len(payload), len(packed), hcoders, sizes)
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


# ============================================================================ adversarial inputs (bounds)
MiB = 1 << 20


def sevenzip_header_bomb() -> bytes:
    """A few KB whose LZMA-packed header unpacks to ~16 MiB of folders with 64 coders each."""
    folder = b"\x40" + b"\x01\x00" * 64 + b"".join(_num(i) + _num(i + 1) for i in range(63))
    nf = (16 * MiB - 64) // len(folder)
    header = (b"\x01\x04\x07\x0b" + _num(nf) + b"\x00" + folder * nf + b"\x00\x00\x00")[:16 * MiB]
    return _packed_header_7z(header, 1 << 24)


def sevenzip_bigdict() -> bytes:
    """A packed header that asks the decoder for a 4 GiB dictionary."""
    return _packed_header_7z(b"\x01" + bytes(16 * MiB - 2) + b"\x00", 0xFFFFFFFF)


def _packed_header_7z(header: bytes, dict_size: int) -> bytes:
    packed = lzma.compress(header, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2}])
    props = bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", dict_size)
    nh = b"\x17" + _streams(0, len(packed), [(LZMA, props)], [len(header)])
    start = struct.pack("<QQI", len(packed), len(nh), zlib.crc32(nh))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + packed + nh


def cfb_fat_bomb(size: int) -> bytes:
    """v3 CFB whose FAT is as many sectors as the file has, every one the same sector (DIFAT repeats it)."""
    ss, nsect = 512, size // 512 - 1
    n_fat = nsect
    n_difat = (n_fat - 109 + 126) // 127
    hdr = bytearray(512)
    hdr[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", hdr, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", hdr, 40, 0, n_fat, 0, 0, 4096, ENDOFCHAIN, 0, 1, n_difat)
    struct.pack_into("<109I", hdr, 76, *([2] * 109))
    body = bytearray(nsect * ss)
    root = bytearray(128)
    name = "Root Entry".encode("utf-16-le") + b"\0\0"
    root[:len(name)] = name
    struct.pack_into("<H", root, 64, len(name))
    root[66] = 5
    struct.pack_into("<III", root, 68, NOSTREAM, NOSTREAM, NOSTREAM)
    struct.pack_into("<I", root, 116, ENDOFCHAIN)
    body[0:128] = root
    struct.pack_into("<128I", body, 2 * ss, *([ENDOFCHAIN] + [FREESECT] * 127))
    sids = [1] + list(range(3, 3 + n_difat - 1))
    for k, sid in enumerate(sids):
        struct.pack_into("<128I", body, sid * ss, *([2] * 127 + [sids[k + 1] if k + 1 < len(sids) else ENDOFCHAIN]))
    return bytes(hdr + body)


def zip_many(size: int) -> bytes:
    """A central directory of tiny entries filling `size`, all pointing at one local header."""
    lh = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + b"a"
    n = (size - len(lh) - 120) // 47
    cd = (b"PK\x01\x02" + struct.pack("<HHHHHHIIIHHHHHII", 20, 20, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0) + b"a") * n
    z64 = b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 45, 45, 0, 0, n, n, len(cd), len(lh))
    loc = b"PK\x06\x07" + struct.pack("<IQI", 0, len(lh) + len(cd), 1)
    return lh + cd + z64 + loc + b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)


def zip_local_flood(size: int) -> bytes:
    """Minimal local headers back to back: more headers than any real archive of this size has."""
    lh = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, 0, 0, 0, 0, 0, 0, 1, 0) + b"a"
    return lh * (size // len(lh))


def pdf_trailers(size: int, encrypt: bool) -> bytes:
    """A PDF that is nothing but trailer keywords (each naming /Encrypt when `encrypt`)."""
    unit = b"trailer<< /Encrypt 1 0 R /ID [<00><00>] >>\n" if encrypt else b"trailer<<>>\n"
    return b"%PDF-1.4\n" + unit * ((size - 9) // len(unit))


def pdf_object_bomb(size: int) -> bytes:
    """A trailer naming /Encrypt 1 0 R and millions of '1 0 obj' to look through."""
    body = b"1 0 obj\n" * ((size - 400) // 8)
    enc = b"1 0 obj <</Filter/Standard/V 2/R 3/Length 128/O<" + b"00" * 32 + b">/U<" + b"00" * 32 + b">/P -4>> endobj\n"
    at = 9 + len(body) + len(enc)
    return b"%PDF-1.4\n" + body + enc + b"xref\n0 0\ntrailer<</Encrypt 1 0 R/ID[<00><00>]>>\nstartxref\n" + str(at).encode() + b"\n%%EOF\n"


def pdf_missing_references(size: int) -> bytes:
    """An encryption dictionary whose every value is a missing reference, in digits and spaces."""
    filler = b"1 0 2 0 3 0 4 0 5 0 6 0 7 0 8 0 9 0\n" * (size // 36)
    enc = b"1 0 obj << /Filter 2 0 R /R 3 0 R /V 4 0 R /O 5 0 R /U 6 0 R /P 7 0 R /Length 8 0 R >> endobj\n"
    return b"%PDF-1.7\n" + enc + filler + b"trailer << /Encrypt 1 0 R /ID [9 0 R] >>\n"


def pdf_name_noise(size: int) -> bytes:
    """Near-misses of the /Encrypt name everywhere: the name scan must stay linear."""
    return b"%PDF-1.7\n" + b"/Encryp /E#6e " * (size // 14)


def rar_blocks(size: int) -> bytes:
    """64 RAR 4 signatures, each followed by minimal blocks."""
    part = b"Rar!\x1a\x07\x00" + struct.pack("<HBHH", 0, 0x75, 0, 7) * ((size // 64) // 7)
    return part * 64


def odf_manifest_bomb() -> bytes:
    """An OpenDocument manifest that inflates to 64 MiB before it names its encryption."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("mimetype"), b"application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", b"<!--" + b" " * (64 * MiB) + b"--><manifest:encryption-data/>")
    return buf.getvalue()


def many_7z_signatures() -> bytes:
    """Seventy plain 7z archives back to back: more signatures than the check follows."""
    return b"".join(sevenzip(encrypted=False, encode_header=False) for _ in range(70))


# ============================================================================ look-alikes (round-2 review)
def zip_entries(n: int) -> bytes:
    """An ordinary stored ZIP with `n` tiny entries, written header by header (zipfile is slow at this)."""
    local, central, off = [], [], 0
    for i in range(n):
        name, body = b"f%06d.txt" % i, b"x"
        crc = zlib.crc32(body)
        lh = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, 0, 0, 0, crc, 1, 1, len(name), 0) + name + body
        central.append(b"PK\x01\x02" + struct.pack("<HHHHHHIIIHHHHHII", 20, 20, 0, 0, 0, 0, crc, 1, 1, len(name), 0, 0, 0, 0, 0, off) + name)
        local.append(lh)
        off += len(lh)
    cd = b"".join(central)
    return (b"".join(local) + cd + b"PK\x06\x06" + struct.pack("<QHHIIQQQQ", 44, 45, 45, 0, 0, n, n, len(cd), off)
            + b"PK\x06\x07" + struct.pack("<IQI", 0, off + len(cd), 1)
            + b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))


def machine_code_with_zip_signature() -> bytes:
    """What the review found in a real DLL: the local-header signature inside machine code, with a
    version of 0x850F, method 0, the encrypted bit set by chance and a binary name."""
    junk = bytes(range(256)) * 64
    fake = (b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 0x850F, 0x0001, 0, 0x8B48, 0x0845, 0x0FC08548, 0x8B480000, 0x74, 12, 0)
            + b"\x8b\x45\x08\x0f\x85\x01\x00\x00\x48\x8b\x4d\x10")
    return b"MZ" + bytes(62) + junk + fake + junk


def text_about_pdf_internals() -> dict[str, bytes]:
    """Notes, a CSV and source code that talk about encrypted PDFs (TEST FIXTURES). None of these
    has PDF structure — no indirect object and cross-reference keyword — so none is read as a PDF."""
    return {
        "note.md": b"# PDF security notes\n\nAn encrypted PDF names its handler in the trailer: `/Encrypt 5 0 R`.\n",
        "log.csv": b"file,note\nreport.pdf,has /Encrypt entry\n",
        "check.py": b"def is_encrypted(trailer):\n    return '/Encrypt' in trailer  # checks the /Encrypt key\n",
        "dict.txt": b"the trailer looks like << /Size 9 /Root 1 0 R /Encrypt 5 0 R /ID [<ab><cd>] >>\n",
    }


def pdf_fragment_in_text() -> bytes:
    """Prose that embeds a whole PDF fragment: an indirect object, `endobj`, a `trailer` that names
    `/Encrypt 5 0 R` and `startxref`. Unlike a casual `/Encrypt` mention this passes the "has PDF
    structure" gate, and the encryption dictionary it names (`/Filter /Standard` with no /O, /U or
    /R) cannot be evaluated — so, fail closed, it is refused ("could not be checked"). Round-2
    accepted this shape while it had no `%PDF-` header; round-3 does not."""
    return (b"A trailer: trailer << /Size 9 /Root 1 0 R /Encrypt 5 0 R >> and startxref\n"
            b"then the objects: 5 0 obj << /Filter /Standard >> endobj\n")


def program_with_pdf_fragments() -> bytes:
    """A program (like a PDF library) whose data holds PDF fragments and the /Encrypt name, with no
    PDF header at the start: repairing readers find no trailer that names an encryption dictionary."""
    strings = (b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\nxref\ntrailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%ld\n%%EOF\n"
               b"\0/Encrypt\0/EncryptMetadata\0Standard\0" + b"2 0 obj\n<< /Length %d >>\nendobj\n")
    return b"\xcf\xfa\xed\xfe" + bytes(4096) + os.urandom(8192) + strings + os.urandom(8192)


# ============================================================================ fail-closed (round-3 review)
def zip_local_only_encrypted() -> bytes:
    """A single ZIP local header with the encryption flag set (general-purpose bit 0) and no central
    directory. A writer always appends a central directory, so nothing here confirms it as a real
    archive — but a reader that walks local headers (libarchive) still opens it with a password, so
    it is refused ("could not be checked"). Plausible fields: stored, a short printable name."""
    name, body = b"secret.txt", os.urandom(24)
    return b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0x0001, 0, 0, 0, 0, len(body), len(body), len(name), 0) + name + body


def pdf_headerless_missing_o() -> bytes:
    """A real one-page PDF (indirect objects, xref, a trailer that names /Encrypt) whose encryption
    dictionary has no /O entry, and whose `%PDF-` header is overwritten so the file is headerless.
    Repairing readers accept a headerless PDF, but none can evaluate a standard-handler dictionary
    with no /O, so it is refused ("could not be checked"). Round-3 replaces the round-2 acceptance
    of a headerless PDF whose encryption dictionary cannot be evaluated."""
    enc = b"<< /Filter /Standard /V 2 /R 3 /U <" + b"00" * 16 + b"> /P -4 >>"          # no /O
    return pdf(encrypt=enc).replace(b"%PDF-1.7", b"%XXX-1.7", 1)
