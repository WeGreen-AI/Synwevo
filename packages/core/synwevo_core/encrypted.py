"""Is this file password-protected? A cheap, bounded look at the container (requirement §8.7).

Why this exists: ClamAV cannot look inside a file it cannot decrypt, and without an
``AlertEncrypted*`` option it answers ``OK`` — so an encrypted ZIP, 7z, RAR, Office document or PDF
used to pass the acceptance gate as "clean" and could be downloaded by other members (a Synwevo
Message attachment is shared as it is). The structure checks did not help either: an encrypted
XLSX is a perfectly valid compound file. "Encrypted" is not "clean": nobody has looked at the
contents. `synwevo_core.malware.ClamdScanner.scan` asks this module after the engine's answer and
reports ``encrypted`` instead of ``clean`` (docs/security/file-acceptance.md §3, §9).

`encrypted_kind(data)` answers ``"archive"``, ``"office"``, ``"pdf"`` or ``None``:

* **ZIP** (any ZIP, including OOXML and OpenDocument packages): an entry whose general-purpose flag
  bit 0 is set, in the central directory or in its local header (ZipCrypto and WinZip AES both set
  it) → ``archive`` (``office`` for an OOXML or OpenDocument package). OpenDocument encrypts its
  parts without that flag and says so in ``META-INF/manifest.xml`` (``encryption-data``), which is
  read with a size cap. The central directory is read, nothing is inflated.
* **OLE2 / Compound File Binary** (MS-CFB): the header, the FAT through the DIFAT and the directory
  chain are parsed properly — never a byte search of the file, since a legacy ``.doc`` keeps its
  text as UTF-16 and could merely *mention* a stream name. Only children of the root storage count
  (an encrypted object embedded in a readable document is not the document being encrypted):
  ``EncryptedPackage`` (encrypted or rights-managed OOXML, MS-OFFCRYPTO), Excel's ``FilePass``
  record right after the first ``BOF`` of the ``Workbook``/``Book`` stream (after an optional
  ``WriteProtect``; MS-XLS), Word's FIB ``fEncrypted`` bit (MS-DOC), PowerPoint's
  ``EncryptedSummary`` stream or a ``Current User`` header token of ``0xF3D1C4DF`` (MS-PPT) →
  ``office``. Anything uncertain → ``None``.
* **PDF**: an ``/Encrypt`` entry in a trailer (a classic ``trailer`` or a cross-reference stream,
  following ``/Prev``) whose standard security handler does **not** open with the empty user
  password. Revisions 2–4 use the MD5/RC4 algorithms of ISO 32000-1 §7.6.3 (algorithms 2, 4, 5; RC4
  is implemented here), revisions 5–6 the SHA-256 check and ISO 32000-2 algorithm 2.B (AES-128-CBC
  from ``cryptography``). A PDF with only an owner password opens without one and is ``None`` — it
  is common (permissions on bank statements, forms) and fully readable. Any other security handler
  (public-key and the like), or an encryption dictionary that cannot be evaluated → ``pdf``: such a
  file does not open without a key either way.
* **7z**: the coders of every folder in the (encoded) header — the header is LZMA-decompressed
  with a size cap when it is compressed, which is 7-Zip's default — include AES-256 + SHA-256
  (coder id ``06 F1 07 01``) → ``archive``.
* **RAR 4 / RAR 5**: the main-header password flag or a file header's password flag (RAR 4); an
  archive encryption header or a file/service header's encryption record (RAR 5) → ``archive``.

Every reader is bounded (declared sizes are checked against the file, chains are followed at most
once per sector) and ``encrypted_kind`` never raises: malformed input is ``None`` and the existing
structure checks and parsers deal with it.
"""
from __future__ import annotations

import hashlib
import io
import lzma
import re
import struct
import zipfile

ARCHIVE, OFFICE, PDF = "archive", "office", "pdf"


def encrypted_kind(data: bytes) -> str | None:
    """``"archive"`` | ``"office"`` | ``"pdf"`` when the file cannot be read without a password,
    else ``None``. Never raises."""
    try:
        return _kind(bytes(data) if not isinstance(data, bytes) else data)
    except Exception:  # noqa: BLE001 - a malformed file is the structure checks' business, not ours
        return None


def kind_of_signature(signature: str | None) -> str:
    """The kind behind a ClamAV ``Heuristics.Encrypted.*`` name (Zip, RAR, 7Zip, PDF, Doc…)."""
    tail = (signature or "").rsplit(".", 1)[-1].lower()
    if tail.startswith("pdf"):
        return PDF
    if tail in ("doc", "ole2", "office", "xls", "ppt", "docx", "xlsx", "pptx"):
        return OFFICE
    return ARCHIVE


def _kind(data: bytes) -> str | None:
    if len(data) < 8:
        return None
    head = data[:8]
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return _zip(data)
    if head == _CFB_MAGIC:
        return _cfb(data)
    if head[:6] == b"7z\xbc\xaf\x27\x1c":
        return _sevenzip(data)
    if head == b"Rar!\x1a\x07\x01\x00":
        return _rar5(data)
    if head[:7] == b"Rar!\x1a\x07\x00":
        return _rar4(data)
    if b"%PDF-" in data[:1024]:
        return _pdf(data)
    return None


# =================================================================================== ZIP
_MANIFEST_CAP = 1024 * 1024
#: Compression methods of real ZIP entries (APPNOTE 4.4.5), 99 being WinZip AES.
_ZIP_METHODS = frozenset({0, 1, 6, 8, 9, 12, 14, 19, 93, 95, 96, 97, 98, 99})
_ODF_ENCRYPTED = re.compile(rb"<(?:[\w.-]+:)?encryption-data\b")


def _zip(data: bytes) -> str | None:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except Exception:  # noqa: BLE001 - no readable central directory: walk the local headers
        return _zip_local_headers(data)
    names = {i.filename for i in infos}
    office = "[Content_Types].xml" in names or "mimetype" in names
    for i in infos:
        if i.flag_bits & 0x1:
            return OFFICE if office else ARCHIVE
        off = i.header_offset
        if 0 <= off <= len(data) - 30 and data[off:off + 4] == b"PK\x03\x04":
            if struct.unpack_from("<H", data, off + 6)[0] & 0x1:
                return OFFICE if office else ARCHIVE
    if "META-INF/manifest.xml" in names:
        # OpenDocument: parts are encrypted without the ZIP flag; the manifest says so.
        info = z.getinfo("META-INF/manifest.xml")
        if info.file_size <= _MANIFEST_CAP:
            with z.open(info) as f:
                manifest = f.read(_MANIFEST_CAP + 1)
            if len(manifest) <= _MANIFEST_CAP and _ODF_ENCRYPTED.search(manifest):
                return OFFICE
    return None


def _zip_local_headers(data: bytes) -> str | None:
    """A ZIP whose central directory is damaged: extractors fall back to the local headers."""
    pos, seen = 0, 0
    while pos <= len(data) - 30 and data[pos:pos + 4] == b"PK\x03\x04" and seen < 100_000:
        version, flags, method, _t, _d, _crc, csize, _usize, nlen, xlen = struct.unpack_from("<HHHHHIIIHH", data, pos + 4)
        if (version & 0xFF) > 63 or method not in _ZIP_METHODS or not 0 < nlen or pos + 30 + nlen + xlen > len(data):
            return None                      # not a local header a reader would accept: garbage, not a guess
        if flags & 0x1:
            return ARCHIVE
        if flags & 0x8 and csize == 0:
            return None                      # sizes only in a trailing descriptor: cannot step on
        pos += 30 + nlen + xlen + csize
        seen += 1
    return None


# =================================================================================== OLE2 / CFB
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAXREGSECT = 0xFFFFFFFA
_NOSTREAM = 0xFFFFFFFF


class _Cfb:
    """Just enough of MS-CFB: header, FAT via the DIFAT, the directory, a stream's first bytes."""

    def __init__(self, data: bytes):
        if len(data) < 512:
            raise ValueError("short header")
        _minor, major, bom, sshift, mshift = struct.unpack_from("<HHHHH", data, 24)
        if bom != 0xFFFE or (major, sshift) not in ((3, 9), (4, 12)) or mshift != 6:
            raise ValueError("not a compound file header")
        (_ndir, n_fat, first_dir, _txn, cutoff, first_minifat, n_minifat,
         first_difat, n_difat) = struct.unpack_from("<IIIIIIIII", data, 40)
        self.data = data
        self.ssize = 1 << sshift
        self.v3 = major == 3
        self.cutoff = cutoff
        self.nsect = max(0, (len(data) - self.ssize + self.ssize - 1) // self.ssize)
        if n_fat > self.nsect + 1:
            raise ValueError("FAT larger than the file")
        # FAT sector locations: 109 in the header, the rest in the DIFAT chain.
        per = self.ssize // 4
        fat_sids = [s for s in struct.unpack_from("<109I", data, 76) if s <= _MAXREGSECT]
        sid, hops = first_difat, 0
        while len(fat_sids) < n_fat and sid <= _MAXREGSECT and hops <= min(n_difat, self.nsect):
            entries = struct.unpack_from(f"<{per}I", self._sector(sid))
            fat_sids += [s for s in entries[:-1] if s <= _MAXREGSECT]
            sid, hops = entries[-1], hops + 1
        fat: list[int] = []
        for s in fat_sids[:n_fat]:
            fat.extend(struct.unpack_from(f"<{per}I", self._sector(s)))
        self.fat = fat
        self.first_minifat, self.n_minifat = first_minifat, n_minifat
        raw = b"".join(self._sector(s) for s in self._chain(first_dir))
        self.entries = [raw[i:i + 128] for i in range(0, len(raw) - 127, 128)]
        if not self.entries or self.entries[0][66] != 5:
            raise ValueError("no root storage")
        self._minifat: list[int] | None = None
        self._ministream: list[int] | None = None

    def _sector(self, sid: int) -> bytes:
        off = (sid + 1) * self.ssize
        if sid > _MAXREGSECT or off >= len(self.data):
            raise ValueError("sector outside the file")
        return self.data[off:off + self.ssize].ljust(self.ssize, b"\0")

    def _chain(self, start: int, table: list[int] | None = None, limit: int | None = None) -> list[int]:
        table = self.fat if table is None else table
        out: list[int] = []
        seen: set[int] = set()
        sid = start
        while sid <= _MAXREGSECT:
            if sid in seen or sid >= len(table):
                raise ValueError("broken chain")
            seen.add(sid)
            out.append(sid)
            if limit is not None and len(out) >= limit:
                break
            sid = table[sid]
        return out

    # ---------------------------------------------------------------- directory
    def entry(self, i: int) -> dict:
        e = self.entries[i]
        nlen = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(0, min(nlen, 64) - 2)].decode("utf-16-le", "replace")
        left, right, child = struct.unpack_from("<III", e, 68)
        start = struct.unpack_from("<I", e, 116)[0]
        size = struct.unpack_from("<Q", e, 120)[0]
        if self.v3:
            size &= 0xFFFFFFFF
        return {"name": name, "type": e[66], "left": left, "right": right, "child": child, "start": start, "size": size}

    def root_children(self) -> dict[str, dict]:
        """Name → entry for the children of the root storage (a red-black tree of siblings)."""
        out: dict[str, dict] = {}
        stack = [self.entry(0)["child"]]
        seen: set[int] = set()
        while stack:
            i = stack.pop()
            if i == _NOSTREAM or i in seen or i >= len(self.entries):
                continue
            seen.add(i)
            e = self.entry(i)
            if e["type"] in (1, 2):
                out.setdefault(e["name"], e)
            stack += [e["left"], e["right"]]
        return out

    # ---------------------------------------------------------------- stream contents
    def prefix(self, e: dict, n: int) -> bytes:
        """The first `n` bytes of a stream (fewer if it is shorter)."""
        n = min(n, e["size"])
        if n <= 0:
            return b""
        if e["size"] < self.cutoff:
            return self._mini_prefix(e["start"], n)
        need = (n + self.ssize - 1) // self.ssize
        return b"".join(self._sector(s) for s in self._chain(e["start"], limit=need))[:n]

    def _mini_prefix(self, start: int, n: int) -> bytes:
        if self._minifat is None:
            per = self.ssize // 4
            table: list[int] = []
            for s in self._chain(self.first_minifat, limit=max(1, min(self.n_minifat, self.nsect))):
                table.extend(struct.unpack_from(f"<{per}I", self._sector(s)))
            self._minifat = table
            self._ministream = self._chain(self.entry(0)["start"])
        out = bytearray()
        for msid in self._chain(start, self._minifat, limit=(n + 63) // 64):
            off = msid * 64
            k, within = divmod(off, self.ssize)
            if k >= len(self._ministream):
                raise ValueError("mini sector outside the mini stream")
            out += self._sector(self._ministream[k])[within:within + 64]
        return bytes(out[:n])


_BIFF_BOF = (0x0809, 0x0409, 0x0209, 0x0009)


def _cfb(data: bytes) -> str | None:
    try:
        cfb = _Cfb(data)
        top = cfb.root_children()
    except (ValueError, struct.error, IndexError):
        return None
    if "EncryptedPackage" in top or "EncryptedSummary" in top:
        return OFFICE
    for name in ("Workbook", "Book"):
        if name in top and top[name]["type"] == 2 and _xls_filepass(cfb.prefix(top[name], 256)):
            return OFFICE
    wd = top.get("WordDocument")
    if wd and wd["type"] == 2:
        fib = cfb.prefix(wd, 12)
        if len(fib) >= 12 and struct.unpack_from("<H", fib, 0)[0] == 0xA5EC and struct.unpack_from("<H", fib, 10)[0] & 0x0100:
            return OFFICE
    cu = top.get("Current User")
    if cu and cu["type"] == 2:
        atom = cfb.prefix(cu, 16)
        if len(atom) >= 16 and struct.unpack_from("<H", atom, 2)[0] == 0x0FF6 and struct.unpack_from("<I", atom, 12)[0] == 0xF3D1C4DF:
            return OFFICE
    return None


def _xls_filepass(stream: bytes) -> bool:
    """BOF, an optional WriteProtect, then FilePass (0x002F): the workbook is encrypted (MS-XLS)."""
    if len(stream) < 4:
        return False
    rtype, rlen = struct.unpack_from("<HH", stream, 0)
    if rtype not in _BIFF_BOF:
        return False
    pos = 4 + rlen
    for _ in range(2):
        if pos + 4 > len(stream):
            return False
        rtype, rlen = struct.unpack_from("<HH", stream, pos)
        if rtype == 0x002F:
            return True
        if rtype != 0x0086:                 # WriteProtect may come first
            return False
        pos += 4 + rlen
    return False


# =================================================================================== PDF
_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
_WS = b" \t\r\n\x0c\x00"
_DELIMS = b"()<>[]{}/%"
_TAIL = 64 * 1024
_MAX_HOPS = 64
_MAX_OBJECT = 256 * 1024


class _Ref(tuple):
    """An indirect reference `num gen R`."""


class _Name(str):
    pass


class _Lexer:
    """A recursive-descent reader for the PDF objects a trailer and an encryption dictionary hold."""

    def __init__(self, data: bytes, pos: int):
        self.d, self.p = data, pos
        self.end = min(len(data), pos + _MAX_OBJECT)

    def ws(self) -> None:
        d = self.d
        while self.p < self.end:
            c = d[self.p]
            if c in _WS:
                self.p += 1
            elif c == 0x25:                 # % comment to end of line
                while self.p < self.end and d[self.p] not in b"\r\n":
                    self.p += 1
            else:
                return

    def value(self, depth: int = 0):
        if depth > 32:
            raise ValueError("nesting too deep")
        self.ws()
        d, p = self.d, self.p
        if p >= self.end:
            raise ValueError("end of input")
        c = d[p:p + 1]
        if d[p:p + 2] == b"<<":
            self.p += 2
            out: dict = {}
            while True:
                self.ws()
                if self.d[self.p:self.p + 2] == b">>":
                    self.p += 2
                    return out
                key = self.value(depth + 1)
                if not isinstance(key, _Name):
                    raise ValueError("dictionary key is not a name")
                out[str(key)] = self.value(depth + 1)
        if c == b"<":
            close = d.find(b">", p, self.end)
            if close < 0:
                raise ValueError("unterminated hex string")
            hexs = re.sub(rb"[^0-9A-Fa-f]", b"", d[p + 1:close])
            self.p = close + 1
            return bytes.fromhex((hexs + (b"0" if len(hexs) % 2 else b"")).decode())
        if c == b"(":
            return self._literal()
        if c == b"[":
            self.p += 1
            arr = []
            while True:
                self.ws()
                if self.d[self.p:self.p + 1] == b"]":
                    self.p += 1
                    return arr
                arr.append(self.value(depth + 1))
        if c == b"/":
            q = p + 1
            while q < self.end and d[q] not in _WS and d[q] not in _DELIMS:
                q += 1
            self.p = q
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), d[p + 1:q])
            return _Name(raw.decode("latin-1"))
        m = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)").match(d, p, self.end)
        if m:
            self.p = m.end()
            text = m.group(0)
            if b"." in text:
                return float(text)
            num = int(text)
            ref = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z0-9])").match(d, self.p, self.end)
            if ref and num >= 0:
                self.p = ref.end()
                return _Ref((num, int(ref.group(1))))
            return num
        for word, val in ((b"true", True), (b"false", False), (b"null", None)):
            if d.startswith(word, p):
                self.p = p + len(word)
                return val
        raise ValueError("unexpected token")

    def _literal(self) -> bytes:
        d, p = self.d, self.p + 1
        out, depth = bytearray(), 1
        while p < self.end:
            c = d[p]
            if c == 0x5C:                    # backslash
                p += 1
                if p >= self.end:
                    break
                e = d[p]
                if e in b"nrtbf":
                    out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[e])
                    p += 1
                elif 0x30 <= e <= 0x37:
                    q = p
                    while q < min(p + 3, self.end) and 0x30 <= d[q] <= 0x37:
                        q += 1
                    out.append(int(d[p:q], 8) & 0xFF)
                    p = q
                elif e == 0x0D:              # line continuation
                    p += 2 if d[p + 1:p + 2] == b"\n" else 1
                elif e == 0x0A:
                    p += 1
                else:
                    out.append(e)
                    p += 1
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    self.p = p + 1
                    return bytes(out)
            out.append(c)
            p += 1
        raise ValueError("unterminated string")


def _pdf(data: bytes) -> str | None:
    trailer = next((t for t in _pdf_trailers(data) if "Encrypt" in t), None)
    if trailer is None or trailer.get("Encrypt") is None:
        return None
    try:
        enc = _resolve(data, trailer["Encrypt"])
        if not isinstance(enc, dict):
            return PDF
        ids = _resolve(data, trailer.get("ID"))
        id0 = _resolve(data, ids[0]) if isinstance(ids, list) and ids else b""
        id0 = id0 if isinstance(id0, bytes) else b""
        return None if _opens_without_password(data, enc, id0) else PDF
    except Exception:  # noqa: BLE001 - an encryption dictionary we cannot evaluate: it does not open without a key
        return PDF


def _pdf_trailers(data: bytes):
    """Trailer dictionaries, newest first: from `startxref` through `/Prev`, or — when the
    cross-reference information is damaged, as readers repair it — every `trailer` keyword and
    cross-reference stream near the end."""
    found = False
    tail_start = max(0, len(data) - _TAIL)
    at = data.rfind(b"startxref", tail_start)
    if at >= 0:
        m = re.compile(rb"startxref\s+(\d+)").match(data, at)
        off = int(m.group(1)) if m else -1
        seen: set[int] = set()
        while 0 <= off < len(data) and off not in seen and len(seen) < _MAX_HOPS:
            seen.add(off)
            t = _trailer_at(data, off)
            if t is None:
                break
            found = True
            yield t
            prev = t.get("Prev")
            off = prev if isinstance(prev, int) else -1
    if found:
        return
    for m in list(re.finditer(rb"trailer\s*<<", data))[-32:]:
        t = _dict_at(data, m.end() - 2)
        if t is not None:
            yield t
    for m in list(re.finditer(rb"/Type\s*/XRef\b", data))[-32:]:
        start = data.rfind(b"obj", max(0, m.start() - 4096), m.start())
        if start >= 0:
            t = _dict_at(data, start + 3)
            if t is not None and t.get("Type") == "XRef":
                yield t


def _trailer_at(data: bytes, off: int) -> dict | None:
    lx = _Lexer(data, off)
    lx.ws()
    if data.startswith(b"xref", lx.p):
        t = data.find(b"trailer", lx.p, lx.p + 64 * 1024 * 1024)
        return _dict_at(data, t + len(b"trailer")) if t >= 0 else None
    m = re.compile(rb"\d+\s+\d+\s+obj").match(data, lx.p)
    if m:
        d = _dict_at(data, m.end())
        return d if d is not None and d.get("Type") == "XRef" else None
    return None


def _dict_at(data: bytes, pos: int) -> dict | None:
    try:
        v = _Lexer(data, pos).value()
    except (ValueError, IndexError, KeyError):
        return None
    return v if isinstance(v, dict) else None


def _resolve(data: bytes, value, depth: int = 0):
    """A direct value, or the newest definition of the object an indirect reference names. The
    encryption dictionary is never inside an object stream (ISO 32000-2 §7.5.7)."""
    if not isinstance(value, _Ref) or depth > 4:
        return value
    num, gen = value
    last = None
    for m in re.finditer(rb"(?<![0-9])%d\s+%d\s+obj\b" % (num, gen), data):
        last = m
    if last is None:
        return None
    lx = _Lexer(data, last.end())
    return _resolve(data, lx.value(), depth + 1)


def _num(data: bytes, value, default: int) -> int:
    v = _resolve(data, value)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _opens_without_password(data: bytes, enc: dict, id0: bytes) -> bool:
    """ISO 32000 standard security handler: does the empty user password open the document?"""
    if _resolve(data, enc.get("Filter")) != "Standard":
        return False                          # public-key and other handlers need a key
    r = _num(data, enc.get("R"), 0)
    v = _num(data, enc.get("V"), 0)
    o = _resolve(data, enc.get("O"))
    u = _resolve(data, enc.get("U"))
    if not isinstance(o, bytes) or not isinstance(u, bytes):
        return False
    if r in (5, 6):
        if len(u) < 48:
            return False
        check = hashlib.sha256(u[32:40]).digest() if r == 5 else _hash_2b(b"", u[32:40], b"")
        return check == u[:32]
    if r not in (2, 3, 4) or len(o) < 32 or len(u) < 16:
        return False
    p = _num(data, enc.get("P"), 0) & 0xFFFFFFFF
    if r == 2:
        n = 5
    else:
        bits = _num(data, enc.get("Length"), 40)
        if v == 4:
            cf = _resolve(data, enc.get("CF"))
            names = [str(_resolve(data, enc.get(k)) or "") for k in ("StmF", "StrF")]
            name = next((x for x in names if x and x != "Identity"), "StdCF")
            filt = _resolve(data, cf.get(name)) if isinstance(cf, dict) else None
            bits = _num(data, filt.get("Length"), 128) if isinstance(filt, dict) else 128
            if bits < 40:
                bits *= 8                     # crypt filters give the length in bytes
        n = max(5, min(16, bits // 8))
    h = hashlib.md5(_PAD + o[:32] + struct.pack("<I", p) + id0)
    if r >= 4 and _resolve(data, enc.get("EncryptMetadata")) is False:
        h.update(b"\xff\xff\xff\xff")
    key = h.digest()
    if r >= 3:
        for _ in range(50):
            key = hashlib.md5(key[:n]).digest()
    key = key[:n]
    if r == 2:
        return _rc4(key, _PAD) == u[:32]
    x = _rc4(key, hashlib.md5(_PAD + id0).digest())
    for i in range(1, 20):
        x = _rc4(bytes(b ^ i for b in key), x)
    return x == u[:16]


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 0xFF
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray(len(data))
    for k, b in enumerate(data):
        i = (i + 1) & 0xFF
        j = (j + s[i]) & 0xFF
        s[i], s[j] = s[j], s[i]
        out[k] = b ^ s[(s[i] + s[j]) & 0xFF]
    return bytes(out)


def _hash_2b(password: bytes, salt: bytes, udata: bytes) -> bytes:
    """ISO 32000-2 algorithm 2.B (revision 6)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    k = hashlib.sha256(password + salt + udata).digest()
    rounds = 0
    while True:
        k1 = (password + k + udata) * 64
        enc = Cipher(algorithms.AES(k[:16]), modes.CBC(k[16:32])).encryptor()
        e = enc.update(k1) + enc.finalize()
        k = (hashlib.sha256, hashlib.sha384, hashlib.sha512)[sum(e[:16]) % 3](e).digest()
        rounds += 1
        if rounds >= 64 and e[-1] <= rounds - 32:
            return k[:32]


# =================================================================================== 7z
_7Z_AES = b"\x06\xf1\x07\x01"
_7Z_LZMA = b"\x03\x01\x01"
_7Z_LZMA2 = b"\x21"
_7Z_HEADER_CAP = 16 * 1024 * 1024


class _SevenZipReader:
    def __init__(self, buf: bytes):
        self.b, self.p = buf, 0

    def byte(self) -> int:
        if self.p >= len(self.b):
            raise ValueError("end of header")
        v = self.b[self.p]
        self.p += 1
        return v

    def number(self) -> int:
        first = self.byte()
        mask, value = 0x80, 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.b):
            raise ValueError("field outside the header")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def digests(self, n: int) -> None:
        if self.byte() == 0:
            defined = sum(bin(x).count("1") for x in self.take((n + 7) // 8))
        else:
            defined = n
        self.take(4 * defined)


def _7z_streams(r: _SevenZipReader) -> dict:
    """StreamsInfo: pack position and sizes, and every folder's coders with their properties."""
    out = {"pack_pos": 0, "pack_sizes": [], "folders": [], "unpack_sizes": []}
    while True:
        prop = r.byte()
        if prop == 0x00:
            return out
        if prop == 0x06:                      # PackInfo
            out["pack_pos"] = r.number()
            n = r.number()
            if n > 1_000_000:
                raise ValueError("too many pack streams")
            while True:
                sub = r.byte()
                if sub == 0x00:
                    break
                if sub == 0x09:
                    out["pack_sizes"] = [r.number() for _ in range(n)]
                elif sub == 0x0A:
                    r.digests(n)
                else:
                    raise ValueError("unknown PackInfo property")
        elif prop == 0x07:                    # UnPackInfo
            if r.byte() != 0x0B:
                raise ValueError("expected folders")
            nf = r.number()
            if nf > 1_000_000 or r.byte() != 0:
                raise ValueError("external folders")
            outs_per_folder = []
            for _ in range(nf):
                coders, n_in, n_out = [], 0, 0
                nc = r.number()
                if nc > 64:
                    raise ValueError("too many coders")
                for _ in range(nc):
                    flags = r.byte()
                    cid = r.take(flags & 0x0F)
                    ci, co = (r.number(), r.number()) if flags & 0x10 else (1, 1)
                    props = r.take(r.number()) if flags & 0x20 else b""
                    if flags & 0x80:
                        raise ValueError("alternative coders")
                    coders.append((cid, props))
                    n_in, n_out = n_in + ci, n_out + co
                for _ in range(n_out - 1):
                    r.number(), r.number()
                packed = n_in - (n_out - 1)
                if packed > 1:
                    for _ in range(packed):
                        r.number()
                out["folders"].append(coders)
                outs_per_folder.append(n_out)
            while True:
                sub = r.byte()
                if sub == 0x00:
                    break
                if sub == 0x0C:
                    out["unpack_sizes"] = [[r.number() for _ in range(k)] for k in outs_per_folder]
                elif sub == 0x0A:
                    r.digests(nf)
                else:
                    raise ValueError("unknown UnPackInfo property")
        elif prop == 0x08:                    # SubStreamsInfo: nothing about coders
            return out
        else:
            raise ValueError("unknown StreamsInfo property")


def _7z_aes(streams: dict) -> bool:
    return any(cid == _7Z_AES for coders in streams["folders"] for cid, _ in coders)


def _sevenzip(data: bytes, depth: int = 0) -> str | None:
    if len(data) < 32:
        return None
    off, size = struct.unpack_from("<QQ", data, 12)
    start = 32 + off
    if size == 0 or size > _7Z_HEADER_CAP or start + size > len(data):
        return None
    return _7z_header(data, data[start:start + size], depth)


def _7z_header(data: bytes, header: bytes, depth: int) -> str | None:
    try:
        r = _SevenZipReader(header)
        kind = r.byte()
        if kind == 0x17:                      # EncodedHeader: the real header is packed
            streams = _7z_streams(r)
            if _7z_aes(streams):
                return ARCHIVE                # the header itself is encrypted (-mhe=on)
            if depth > 1 or len(streams["folders"]) != 1 or len(streams["folders"][0]) != 1 or not streams["pack_sizes"]:
                return None
            cid, props = streams["folders"][0][0]
            usize = streams["unpack_sizes"][0][0] if streams["unpack_sizes"] else 0
            pos, psize = 32 + streams["pack_pos"], streams["pack_sizes"][0]
            if usize <= 0 or usize > _7Z_HEADER_CAP or pos + psize > len(data):
                return None
            return _7z_header(data, _7z_unpack(cid, props, data[pos:pos + psize], usize), depth + 1)
        if kind != 0x01:
            return None
        while True:
            prop = r.byte()
            if prop == 0x02:                  # ArchiveProperties
                while r.number() != 0:
                    r.take(r.number())
            elif prop in (0x03, 0x04):        # AdditionalStreamsInfo / MainStreamsInfo
                if _7z_aes(_7z_streams(r)):
                    return ARCHIVE
                if prop == 0x04:
                    return None
            else:
                return None
    except (ValueError, IndexError, struct.error, lzma.LZMAError):
        return None


def _7z_unpack(cid: bytes, props: bytes, packed: bytes, usize: int) -> bytes:
    if cid == _7Z_LZMA and len(props) == 5:
        d = props[0]
        lc, d = d % 9, d // 9
        lp, pb = d % 5, d // 5
        filters = [{"id": lzma.FILTER_LZMA1, "dict_size": struct.unpack_from("<I", props, 1)[0], "lc": lc, "lp": lp, "pb": pb}]
    elif cid == _7Z_LZMA2 and len(props) == 1 and props[0] <= 40:
        p = props[0]
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)}]
    else:
        raise ValueError("header coder not supported")
    out = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters).decompress(packed, max_length=usize)
    if len(out) != usize:
        raise ValueError("short header")
    return out


# =================================================================================== RAR
def _rar4(data: bytes) -> str | None:
    """RAR 2.9–4.x blocks: HEAD_CRC, HEAD_TYPE, HEAD_FLAGS, HEAD_SIZE [, ADD_SIZE]."""
    pos, blocks = 7, 0
    while pos + 7 <= len(data) and blocks < 100_000:
        _crc, htype, flags, hsize = struct.unpack_from("<HBHH", data, pos)
        if hsize < 7:
            return None
        add = 0
        if htype == 0x73 and flags & 0x0080:             # MAIN_HEAD, MHD_PASSWORD: headers encrypted
            return ARCHIVE
        if htype in (0x74, 0x7A):                        # FILE_HEAD / NEWSUB_HEAD
            if flags & 0x0004:                           # LHD_PASSWORD
                return ARCHIVE
            if pos + 11 > len(data):
                return None
            add = struct.unpack_from("<I", data, pos + 7)[0]
            if flags & 0x0100 and pos + 36 <= len(data):  # LHD_LARGE: HIGH_PACK_SIZE
                add |= struct.unpack_from("<I", data, pos + 32)[0] << 32
        elif flags & 0x8000 and pos + 11 <= len(data):
            add = struct.unpack_from("<I", data, pos + 7)[0]
        if htype == 0x7B:                                # ENDARC
            return None
        pos += hsize + add
        blocks += 1
    return None


def _vint(data: bytes, pos: int) -> tuple[int, int]:
    value, shift = 0, 0
    for i in range(10):
        if pos + i >= len(data):
            raise ValueError("end of data")
        b = data[pos + i]
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            return value, pos + i + 1
        shift += 7
    raise ValueError("vint too long")


def _rar5(data: bytes) -> str | None:
    pos, blocks = 8, 0
    try:
        while pos + 5 <= len(data) and blocks < 100_000:
            hsize, hstart = _vint(data, pos + 4)
            hend = hstart + hsize
            if hsize < 2 or hend > len(data):
                return None
            htype, p = _vint(data, hstart)
            hflags, p = _vint(data, p)
            extra = dsize = 0
            if hflags & 0x0001:
                extra, p = _vint(data, p)
            if hflags & 0x0002:
                dsize, p = _vint(data, p)
            if htype == 4:                               # archive encryption header
                return ARCHIVE
            if htype in (2, 3) and 0 < extra <= hend - p:  # file / service header: extra records
                q = hend - extra
                while q < hend:
                    rsize, r0 = _vint(data, q)
                    rtype, _ = _vint(data, r0)
                    if rtype == 0x01:                    # file encryption record
                        return ARCHIVE
                    if rsize == 0:
                        break
                    q = r0 + rsize
            if htype == 5:                               # end of archive
                return None
            pos = hend + dsize
            blocks += 1
    except ValueError:
        return None
    return None
