"""Is this file password-protected? A bounded look at the container (requirement §8.7).

Why this exists: ClamAV cannot look inside a file it cannot decrypt, and without an
``AlertEncrypted*`` option it answers ``OK`` — so an encrypted ZIP, 7z, RAR, Office document or PDF
used to pass the acceptance gate as "clean" and could be downloaded by other members (a Synwevo
Message attachment is shared as it is). The structure checks did not help either: an encrypted
XLSX is a perfectly valid compound file. "Encrypted" is not "clean": nobody has looked at the
contents. `synwevo_core.malware.ClamdScanner.scan` asks this module after the engine's answer and
reports ``encrypted`` instead of ``clean`` (docs/security/file-acceptance.md §3.1).

`encrypted_kind(data)` answers ``"archive"``, ``"office"``, ``"pdf"`` or ``None``. Two rules shape it
(the 2026-09-29 security review found both broken in the first version):

* **Fail closed.** Once a format's structure is there — a ZIP header, a compound-file header, a 7z
  or RAR signature, the ``/Encrypt`` name in a PDF — anything that stops the check from reaching an
  answer (a parse failure, a coder it cannot unpack, any internal bound) is an answer of its own:
  "could not be checked", reported as that kind, which refuses the file like an encrypted one.
  ``None`` means "looked, and it opens without a password" or "none of these formats is there".
* **Every applicable format, where readers find it.** Readers do not stop at the first bytes: they
  open a ZIP behind junk or a stub (self-extracting archives, polyglots), a 7z or RAR behind an
  executable stub, a PDF without its header. So each format is looked for across the whole file,
  and all of them are asked.

The formats:

* **ZIP** (any ZIP, including OOXML and OpenDocument packages, and archives stored uncompressed
  inside another file): every local and central-directory header in the file, found by signature
  and checked for plausibility; general-purpose flag bit 0 (ZipCrypto, WinZip AES) → ``archive``
  (``office`` for an OOXML or OpenDocument package). OpenDocument encrypts its parts without that
  flag and says so in ``META-INF/manifest.xml`` (``encryption-data``): the manifest is inflated as a
  stream with a size budget. `zipfile` is not used — it reads a whole central directory into
  objects, which a crafted file makes cost gigabytes.
* **OLE2 / Compound File Binary** (MS-CFB, at offset 0 as every reader requires): header, FAT via
  the DIFAT (entries read on demand, never materialised) and the directory chain — never a byte
  search, since a legacy ``.doc`` keeps its text as UTF-16. Names compare case-insensitively, as
  MS-CFB says. Only children of the root storage count: ``EncryptedPackage``/``EncryptionInfo``
  (encrypted or rights-managed OOXML), Excel's ``FilePass`` after the first ``BOF`` of
  ``Workbook``/``Book`` (after an optional ``WriteProtect``) **unless it opens with Excel's default
  password** — structure protection is written that way and opens freely (XOR obfuscation, RC4,
  RC4 CryptoAPI are verified with "VelvetSweatshop"), Word's FIB ``fEncrypted`` bit, PowerPoint's
  ``EncryptedSummary`` stream or ``Current User`` header token ``0xF3D1C4DF`` → ``office``.
* **PDF** (wherever it is — readers repair a missing header or broken offsets): only a file that
  carries the ``/Encrypt`` name (``#xx`` escapes included) is looked at. The trailer readers use —
  the ``startxref`` chain through ``/Prev``, or, when that is damaged, every ``trailer`` and
  cross-reference-stream dictionary near the end and the dictionaries around each ``/Encrypt`` —
  decides; the standard security handler is checked against the empty user password (revisions
  2–4: ISO 32000-1 algorithms 2, 4, 5 with RC4 implemented here; 5–6: SHA-256 and ISO 32000-2
  algorithm 2.B with AES-128-CBC from ``cryptography``). Only an owner password → ``None`` (such
  PDFs are common and fully readable). Other handlers, an unresolvable dictionary, the name present
  but no readable trailer carrying it → ``pdf``.
* **7z** (every signature in the file, e.g. behind a self-extractor stub): any folder's coders
  include AES-256 (``06 F1 07 01``) → ``archive``; a packed header is unpacked when it uses one
  Copy, Deflate, LZMA or LZMA2 coder (dictionary clamped to the output size), anything else fails
  closed.
* **RAR 4 / RAR 5** (every signature): main-header password flag or a file header's password flag
  (RAR 4); archive encryption header or a file/service header's encryption record (RAR 5).

Bounds (time and memory are linear in the input, with fixed caps): headers per format, 7z folders
and coders, CFB directory entries, PDF parse tokens and dictionary candidates, inflated manifest and
7z header bytes. Callers pass at most the scan limit (`ClamdScanner.scan` refuses larger files as
``too_large`` first). ``encrypted_kind`` never raises.
"""
from __future__ import annotations

import hashlib
import lzma
import re
import struct
import zlib

ARCHIVE, OFFICE, PDF = "archive", "office", "pdf"


class _Unchecked(Exception):
    """The structure that would say whether the file is encrypted is there, and could not be
    evaluated within the bounds: that refuses the file ("could not be checked")."""

    def __init__(self, kind: str, why: str = ""):
        super().__init__(why)
        self.kind = kind


def encrypted_kind(data: bytes) -> str | None:
    """``"archive"`` | ``"office"`` | ``"pdf"`` when the file cannot be read without a password or
    could not be checked; ``None`` when it opens without one. Never raises."""
    return inspect(data)[0]


def inspect(data: bytes) -> tuple[str | None, bool]:
    """(kind, checked): ``checked`` is False when the kind stands for "could not be checked"."""
    data = data if isinstance(data, bytes) else bytes(data)
    for check in (_cfb, _zip, _pdf, _sevenzip, _rar):
        try:
            kind = check(data)
        except _Unchecked as e:
            return e.kind, False
        except Exception:  # noqa: BLE001 - a checker that got this far found its format's structure
            return _KIND_OF[check], False
        if kind:
            return kind, True
    return None, True


def kind_of_signature(signature: str | None) -> str:
    """The kind behind a ClamAV ``Heuristics.Encrypted.*`` name (Zip, RAR, 7Zip, PDF, OLE2…)."""
    tail = (signature or "").rsplit(".", 1)[-1].lower()
    if tail.startswith("pdf"):
        return PDF
    if tail in ("doc", "ole2", "office", "xls", "ppt", "docx", "xlsx", "pptx"):
        return OFFICE
    return ARCHIVE


def _guard(kind: str):
    """Decorator: once a checker is past its applicability test, any failure fails closed."""
    def wrap(fn):
        def run(data: bytes, *a):
            try:
                return fn(data, *a)
            except _Unchecked:
                raise
            except Exception as e:  # noqa: BLE001
                raise _Unchecked(kind, type(e).__name__) from e
        return run
    return wrap


# =================================================================================== ZIP
_ZIP_SIGS = re.compile(rb"PK(\x03\x04|\x01\x02)")
_ZIP_MAX_HEADERS = 400_000
#: Compression methods of real ZIP entries (APPNOTE 4.4.5), 99 being WinZip AES.
_ZIP_METHODS = frozenset({0, 1, 6, 8, 9, 12, 14, 19, 93, 95, 96, 97, 98, 99})
_MANIFEST = b"META-INF/manifest.xml"
_MANIFEST_BUDGET = 16 * 1024 * 1024
_ODF_ENCRYPTED = re.compile(rb"<(?:[\w.-]+:)?encryption-data\b")


def _zip(data: bytes) -> str | None:
    """Every local and central header in the file, wherever it is."""
    seen = 0
    manifests: list[tuple[int, int, int, int]] = []       # (data offset, method, csize, flags) of local manifest entries
    sizes: dict[bytes, int] = {}                          # central-directory sizes, for local headers with a descriptor
    for m in _ZIP_SIGS.finditer(data):
        seen += 1
        if seen > _ZIP_MAX_HEADERS:
            raise _Unchecked(ARCHIVE, "too many ZIP headers")
        p = m.start()
        if m.group(1) == b"\x03\x04":
            if p + 30 > len(data):
                continue
            ver, flags, method, _t, _d, _crc, csize, _usize, nlen, xlen = struct.unpack_from("<HHHHHIIIHH", data, p + 4)
            if (ver & 0xFF) > 63 or method not in _ZIP_METHODS or not 0 < nlen <= 4096 or p + 30 + nlen > len(data):
                continue                          # not a header a reader would accept
            if flags & 0x1:
                return _zip_kind(data)
            if data[p + 30:p + 30 + nlen] == _MANIFEST:
                manifests.append((p + 30 + nlen + xlen, method, csize, flags))
        else:
            if p + 46 > len(data):
                continue
            _made, ver, flags, method = struct.unpack_from("<HHHH", data, p + 4)
            csize = struct.unpack_from("<I", data, p + 20)[0]
            nlen = struct.unpack_from("<H", data, p + 28)[0]
            if (ver & 0xFF) > 63 or method not in _ZIP_METHODS or not 0 < nlen <= 4096 or p + 46 + nlen > len(data):
                continue
            if flags & 0x1:
                return _zip_kind(data)
            name = data[p + 46:p + 46 + nlen]
            if name == _MANIFEST:
                sizes.setdefault(name, csize)
    for start, method, csize, flags in manifests[:64]:
        if _manifest_encrypted(data, start, method, csize if csize or not flags & 0x8 else sizes.get(_MANIFEST)):
            return OFFICE
    return None


def _zip_kind(data: bytes) -> str:
    return OFFICE if b"[Content_Types].xml" in data or b"application/vnd.oasis.opendocument" in data else ARCHIVE


def _manifest_encrypted(data: bytes, start: int, method: int, csize: int | None) -> bool:
    """Stream the OpenDocument manifest through the budget and look for encryption-data. Any entry
    that cannot be read that way fails closed: LibreOffice would read it and ask for a password."""
    try:
        return _manifest_scan(data, start, method, csize)
    except _Unchecked:
        raise
    except Exception as e:  # noqa: BLE001 - a damaged stream is not an answer
        raise _Unchecked(OFFICE, type(e).__name__) from e


def _manifest_scan(data: bytes, start: int, method: int, csize: int | None) -> bool:
    if start > len(data):
        return False                              # cut off before the entry: nobody reads it
    if method == 0:
        if csize is None:
            raise _Unchecked(OFFICE, "stored manifest of unknown size")
        if csize > _MANIFEST_BUDGET:
            raise _Unchecked(OFFICE, "manifest over the budget")
        return bool(_ODF_ENCRYPTED.search(data, start, min(len(data), start + csize)))
    if method != 8:
        raise _Unchecked(OFFICE, "manifest compressed with an unexpected method")
    inflate = zlib.decompressobj(-15)
    end = min(len(data), start + csize) if csize else len(data)
    pos, total, tail = start, 0, b""
    while not inflate.eof:
        if inflate.unconsumed_tail:
            chunk = inflate.decompress(inflate.unconsumed_tail, 1 << 20)
        elif pos < end:
            chunk = inflate.decompress(data[pos:min(end, pos + (1 << 16))], 1 << 20)
            pos = min(end, pos + (1 << 16))
        else:
            break
        total += len(chunk)
        if total > _MANIFEST_BUDGET:
            raise _Unchecked(OFFICE, "manifest over the budget")
        if _ODF_ENCRYPTED.search(tail + chunk):
            return True
        tail = (tail + chunk)[-64:]
    return False


# =================================================================================== OLE2 / CFB
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAXREGSECT = 0xFFFFFFFA
_NOSTREAM = 0xFFFFFFFF
_CFB_MAX_DIR_ENTRIES = 200_000


class _Cfb:
    """Just enough of MS-CFB: header, FAT via the DIFAT (read on demand), the directory, and the
    first bytes of a stream. Chains are followed at most once per sector."""

    def __init__(self, data: bytes):
        _minor, major, _bom, sshift, _mshift = struct.unpack_from("<HHHHH", data, 24)
        (_ndir, n_fat, first_dir, _txn, cutoff, first_minifat, n_minifat,
         first_difat, n_difat) = struct.unpack_from("<IIIIIIIII", data, 40)
        self.data = data
        self.ssize = 1 << sshift
        self.per = self.ssize // 4
        self.v3 = major == 3
        self.cutoff = cutoff
        self.nsect = max(0, (len(data) - self.ssize + self.ssize - 1) // self.ssize)
        if n_fat > self.nsect + 1:
            raise ValueError("FAT larger than the file")
        # FAT sector locations: 109 in the header, the rest in the DIFAT chain (at most one pass).
        fat_sids = [s for s in struct.unpack_from("<109I", data, 76) if s <= _MAXREGSECT]
        sid, hops, seen = first_difat, 0, set()
        while len(fat_sids) < n_fat and sid <= _MAXREGSECT and hops <= min(n_difat, self.nsect):
            if sid in seen:
                raise ValueError("DIFAT loop")
            seen.add(sid)
            entries = struct.unpack_from(f"<{self.per}I", self._sector(sid))
            fat_sids += [s for s in entries[:-1] if s <= _MAXREGSECT]
            sid, hops = entries[-1], hops + 1
        self.fat_sids = fat_sids[:n_fat]
        self.first_minifat, self.n_minifat = first_minifat, n_minifat
        chain = self._chain(first_dir)
        if len(chain) * (self.ssize // 128) > _CFB_MAX_DIR_ENTRIES:
            raise _Unchecked(OFFICE, "directory over the bound")
        raw = b"".join(self._sector(s) for s in chain)
        self.entries = [raw[i:i + 128] for i in range(0, len(raw) - 127, 128)]
        if not self.entries or self.entries[0][66] != 5:
            raise ValueError("no root storage")
        self._minifat_sids: list[int] | None = None
        self._ministream: list[int] | None = None

    def _sector(self, sid: int) -> bytes:
        off = (sid + 1) * self.ssize
        if sid > _MAXREGSECT or off >= len(self.data):
            raise ValueError("sector outside the file")
        return self.data[off:off + self.ssize].ljust(self.ssize, b"\0")

    def _fat(self, sid: int) -> int:
        k, i = divmod(sid, self.per)
        if k >= len(self.fat_sids):
            raise ValueError("sector beyond the FAT")
        off = (self.fat_sids[k] + 1) * self.ssize + 4 * i
        if off + 4 > len(self.data):
            raise ValueError("FAT sector outside the file")
        return struct.unpack_from("<I", self.data, off)[0]

    def _chain(self, start: int, nxt=None, limit: int | None = None) -> list[int]:
        nxt = nxt or self._fat
        out: list[int] = []
        seen: set[int] = set()
        sid = start
        while sid <= _MAXREGSECT:
            if sid in seen or len(out) > self.nsect * max(1, self.ssize // 64):
                raise ValueError("broken chain")
            seen.add(sid)
            out.append(sid)
            if limit is not None and len(out) >= limit:
                break
            sid = nxt(sid)
        return out

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
        """Upper-cased name → entry for the children of the root storage (names compare
        case-insensitively in MS-CFB, so readers find ``encryptedpackage`` too)."""
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
                out.setdefault(e["name"].upper(), e)
            stack += [e["left"], e["right"]]
        return out

    def prefix(self, e: dict, n: int) -> bytes:
        n = min(n, e["size"])
        if n <= 0:
            return b""
        if e["size"] < self.cutoff:
            return self._mini_prefix(e["start"], n)
        need = (n + self.ssize - 1) // self.ssize
        return b"".join(self._sector(s) for s in self._chain(e["start"], limit=need))[:n]

    def _minifat(self, msid: int) -> int:
        k, i = divmod(msid, self.per)
        if k >= len(self._minifat_sids):
            raise ValueError("mini sector beyond the mini FAT")
        return struct.unpack_from("<I", self._sector(self._minifat_sids[k]), 4 * i)[0]

    def _mini_prefix(self, start: int, n: int) -> bytes:
        if self._minifat_sids is None:
            self._minifat_sids = self._chain(self.first_minifat, limit=max(1, min(self.n_minifat, self.nsect)))
            self._ministream = self._chain(self.entry(0)["start"])
        out = bytearray()
        for msid in self._chain(start, self._minifat, limit=(n + 63) // 64):
            k, within = divmod(msid * 64, self.ssize)
            if k >= len(self._ministream):
                raise ValueError("mini sector outside the mini stream")
            out += self._sector(self._ministream[k])[within:within + 64]
        return bytes(out[:n])


def _cfb_header_ok(data: bytes) -> bool:
    if len(data) < 512 or data[:8] != _CFB_MAGIC:
        return False
    _minor, major, bom, sshift, mshift = struct.unpack_from("<HHHHH", data, 24)
    return bom == 0xFFFE and (major, sshift) in ((3, 9), (4, 12)) and mshift == 6


def _cfb(data: bytes) -> str | None:
    if not _cfb_header_ok(data):
        return None                                   # no reader opens it as a compound file
    return _cfb_checked(data)


@_guard(OFFICE)
def _cfb_checked(data: bytes) -> str | None:
    cfb = _Cfb(data)
    top = cfb.root_children()
    if {"ENCRYPTEDPACKAGE", "ENCRYPTIONINFO", "ENCRYPTEDSUMMARY"} & top.keys():
        return OFFICE
    for name in ("WORKBOOK", "BOOK"):
        if name in top and top[name]["type"] == 2 and _xls_encrypted(cfb.prefix(top[name], 9000)):
            return OFFICE
    wd = top.get("WORDDOCUMENT")
    if wd and wd["type"] == 2:
        fib = cfb.prefix(wd, 12)
        if len(fib) >= 12 and struct.unpack_from("<H", fib, 0)[0] == 0xA5EC and struct.unpack_from("<H", fib, 10)[0] & 0x0100:
            return OFFICE
    cu = top.get("CURRENT USER")
    if cu and cu["type"] == 2:
        atom = cfb.prefix(cu, 16)
        if len(atom) >= 16 and struct.unpack_from("<H", atom, 2)[0] == 0x0FF6 and struct.unpack_from("<I", atom, 12)[0] == 0xF3D1C4DF:
            return OFFICE
    return None


_BIFF_BOF = (0x0809, 0x0409, 0x0209, 0x0009)
#: Excel writes workbook-structure protection as encryption with this password (MS-OFFCRYPTO 2.3.7.7).
_XLS_DEFAULT_PASSWORD = "VelvetSweatshop"


def _xls_encrypted(stream: bytes) -> bool:
    """BOF, an optional WriteProtect, then FilePass (MS-XLS): encrypted — unless the default
    password opens it."""
    if len(stream) < 4:
        return False
    rtype, rlen = struct.unpack_from("<HH", stream, 0)
    if rtype not in _BIFF_BOF:
        return False
    biff8 = rlen >= 2 and struct.unpack_from("<H", stream, 4)[0] >= 0x0600
    pos = 4 + rlen
    for _ in range(2):
        if pos + 4 > len(stream):
            return False
        rtype, rlen = struct.unpack_from("<HH", stream, pos)
        if rtype == 0x002F:
            payload = stream[pos + 4:pos + 4 + rlen]
            if len(payload) < rlen:
                raise _Unchecked(OFFICE, "FilePass record cut short")
            return not _xls_default_opens(payload, biff8)
        if rtype != 0x0086:                 # WriteProtect may come first
            return False
        pos += 4 + rlen
    return False


def _xls_default_opens(fp: bytes, biff8: bool) -> bool:
    """Whether Excel's default password opens a FilePass (XOR obfuscation, RC4, RC4 CryptoAPI)."""
    pw = _XLS_DEFAULT_PASSWORD
    if not biff8:                                   # BIFF5: key, verifier (XOR)
        return len(fp) >= 4 and struct.unpack_from("<H", fp, 2)[0] == _xor_verifier(pw)
    kind = struct.unpack_from("<H", fp, 0)[0]
    if kind == 0:                                   # XOR obfuscation: key, verificationBytes
        return len(fp) >= 6 and struct.unpack_from("<H", fp, 4)[0] == _xor_verifier(pw)
    if kind != 1 or len(fp) < 6:
        return False
    major, minor = struct.unpack_from("<HH", fp, 2)
    utf16 = pw.encode("utf-16-le")
    if (major, minor) == (1, 1):                    # RC4 (Office 97/2000), MS-OFFCRYPTO 2.3.6
        if len(fp) < 54:
            return False
        salt, verifier, verifier_hash = fp[6:22], fp[22:38], fp[38:54]
        h1 = hashlib.md5((hashlib.md5(utf16).digest()[:5] + salt) * 16).digest()
        key = hashlib.md5(h1[:5] + struct.pack("<I", 0)).digest()
        plain = _rc4(key, verifier + verifier_hash)
        return hashlib.md5(plain[:16]).digest() == plain[16:32]
    if major in (2, 3, 4) and minor == 2:           # RC4 CryptoAPI, MS-OFFCRYPTO 2.3.5
        if len(fp) < 14:
            return False
        header_size = struct.unpack_from("<I", fp, 10)[0]
        h = 14
        if header_size < 20 or len(fp) < h + header_size + 4:
            return False
        key_bits = struct.unpack_from("<I", fp, h + 16)[0] or 40
        v = h + header_size
        salt_size = struct.unpack_from("<I", fp, v)[0]
        if salt_size != 16 or len(fp) < v + 4 + 16 + 16 + 4 + 20:
            return False
        salt = fp[v + 4:v + 20]
        enc_verifier = fp[v + 20:v + 36]
        hash_size = struct.unpack_from("<I", fp, v + 36)[0]
        enc_hash = fp[v + 40:v + 40 + max(20, hash_size)]
        h0 = hashlib.sha1(salt + utf16).digest()
        hfinal = hashlib.sha1(h0 + struct.pack("<I", 0)).digest()
        key = hfinal[:5] + b"\0" * 11 if key_bits == 40 else hfinal[:key_bits // 8]
        plain = _rc4(key, enc_verifier + enc_hash)
        return hashlib.sha1(plain[:16]).digest() == plain[16:36]
    return False


def _xor_verifier(password: str) -> int:
    """MS-OFFCRYPTO 2.3.7.1 CreatePasswordVerifier_Method1."""
    raw = password.encode("latin-1", "replace")[:15]
    verifier = 0
    for b in reversed(bytes([len(raw)]) + raw):
        verifier = (((verifier >> 14) & 1) | ((verifier << 1) & 0x7FFF)) ^ b
    return verifier ^ 0xCE4B


# =================================================================================== PDF
_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")
_DELIMS = b"()<>[]{}/%"
_WS_RE = re.compile(rb"(?:[ \t\r\n\x0c\x00]+|%[^\r\n]*)*")
_NUM_RE = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)")
_REF_RE = re.compile(rb"\s+(\d+)\s+R(?![A-Za-z0-9])")
_LITERAL_SPECIAL = re.compile(rb"[()\\]")
_MAX_STRING = 1 << 20
_TOKENS_PER_PDF = 60_000
_MAX_OBJECT_SEARCHES = 4                          # each may scan the whole file backwards
_REGEX_SCAN_BUDGET = 64 << 20                     # bytes the slow object search may cover per file
_MAX_CANDIDATES = 32


def _name_re(name: str) -> re.Pattern:
    """A PDF name with any of its characters written as #xx, ending where a name ends."""
    parts = []
    for ch in name:
        h = f"{ord(ch):02x}"
        hexpart = f"#{h[0]}[{h[1].lower()}{h[1].upper()}]" if h[1].isalpha() else f"#{h}"
        parts.append(f"(?:{re.escape(ch)}|{hexpart})")
    return re.compile(("/" + "".join(parts) + r"(?![^\s()<>\[\]{}/%])").encode())


_ENCRYPT_NAME = _name_re("Encrypt")
_XREF_NAME = _name_re("XRef")
_STARTXREF = re.compile(rb"startxref\s+(\d+)")
_OBJ_HEAD = re.compile(rb"\d+\s+\d+\s+obj")
_TRAILER = re.compile(rb"trailer")


class _Ref(tuple):
    """An indirect reference `num gen R`."""


class _Name(str):
    pass


class _Budget:
    def __init__(self, tokens: int):
        self.tokens = tokens
        self.objects: dict = {}                   # (num, gen) -> resolved value, one search each
        self.scanned = 0

    def spend(self, n: int = 1) -> None:
        self.tokens -= n
        if self.tokens < 0:
            raise _Unchecked(PDF, "PDF parse budget spent")


class _Lexer:
    """The PDF objects a trailer and an encryption dictionary hold, on a token budget."""

    def __init__(self, data: bytes, pos: int, budget: _Budget):
        self.d, self.p, self.b = data, pos, budget

    def ws(self) -> None:
        self.p = _WS_RE.match(self.d, self.p).end()

    def value(self, depth: int = 0):
        if depth > 32:
            raise ValueError("nesting too deep")
        self.b.spend()
        self.ws()
        d, p = self.d, self.p
        if p >= len(d):
            raise ValueError("end of input")
        if d.startswith(b"<<", p):
            self.p += 2
            out: dict = {}
            while True:
                self.ws()
                if d.startswith(b">>", self.p):
                    self.p += 2
                    return out
                key = self.value(depth + 1)
                if not isinstance(key, _Name):
                    raise ValueError("dictionary key is not a name")
                out[str(key)] = self.value(depth + 1)
        c = d[p:p + 1]
        if c == b"<":
            close = d.find(b">", p)
            if close < 0:
                raise ValueError("unterminated hex string")
            if close - p > 2 * _MAX_STRING:
                raise _Unchecked(PDF, "hex string over the bound")
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
                if d.startswith(b"]", self.p):
                    self.p += 1
                    return arr
                arr.append(self.value(depth + 1))
        if c == b"/":
            q = p + 1
            while q < len(d) and q - p < 256 and d[q] not in b" \t\r\n\x0c\x00" and d[q] not in _DELIMS:
                q += 1
            self.p = q
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), d[p + 1:q])
            return _Name(raw.decode("latin-1"))
        m = _NUM_RE.match(d, p)
        if m:
            self.p = m.end()
            text = m.group(0)
            if b"." in text:
                return float(text)
            num = int(text)
            ref = _REF_RE.match(d, self.p)
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
        while True:
            m = _LITERAL_SPECIAL.search(d, p)
            if not m:
                raise ValueError("unterminated string")
            out += d[p:m.start()]
            if len(out) > _MAX_STRING:
                raise _Unchecked(PDF, "string over the bound")
            self.b.spend()
            p = m.start()
            c = d[p]
            if c == 0x5C:                    # backslash
                e = d[p + 1] if p + 1 < len(d) else None
                if e is None:
                    raise ValueError("unterminated string")
                if e in b"nrtbf":
                    out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[e])
                    p += 2
                elif 0x30 <= e <= 0x37:
                    q = p + 1
                    while q < min(p + 4, len(d)) and 0x30 <= d[q] <= 0x37:
                        q += 1
                    out.append(int(d[p + 1:q], 8) & 0xFF)
                    p = q
                elif e == 0x0D:
                    p += 3 if d[p + 2:p + 3] == b"\n" else 2
                elif e == 0x0A:
                    p += 2
                else:
                    out.append(e)
                    p += 2
                continue
            if c == 0x28:
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    self.p = p + 1
                    return bytes(out)
            out.append(c)
            p += 1


def _last_matches(rx: re.Pattern, data: bytes, k: int, before: int | None = None) -> list[re.Match]:
    """The last `k` matches, newest first, scanning backwards in growing windows: a file full of
    matches costs one window, a file without them one pass — never a list of every match."""
    out: list[re.Match] = []
    hi = len(data) if before is None else before
    limit, window = hi, 1 << 16
    while hi > 0 and len(out) < k:
        lo = max(0, hi - window)
        found = [m for m in rx.finditer(data, lo, hi) if m.start() < limit]
        out.extend(reversed(found))
        if lo == 0:
            break
        limit, hi = lo, min(len(data), lo + 512)
        window = min(window * 2, 1 << 22)
    return out[:k]


def _pdf(data: bytes) -> str | None:
    if not _ENCRYPT_NAME.search(data):
        return None                                   # no encryption dictionary can be named
    return _pdf_checked(data)


@_guard(PDF)
def _pdf_checked(data: bytes) -> str | None:
    budget = _Budget(_TOKENS_PER_PDF)
    chain = _startxref_chain(data, budget)
    if chain:
        carrying = [t for t in chain if t.get("Encrypt") is not None]
        if not carrying:
            return None                               # the trailer readers use names no encryption
        return _judge(data, carrying[0], budget)
    # Damaged or missing cross-reference: readers rebuild it from what they find.
    carrying: dict[str, dict] = {}
    for t in _repair_candidates(data, budget):
        if t.get("Encrypt") is not None:
            carrying.setdefault(repr((t.get("Encrypt"), t.get("ID"))), t)   # one verdict per distinct pair
    if not carrying:
        raise _Unchecked(PDF, "the /Encrypt name is there but no readable trailer carries it")
    verdicts = [_judge(data, t, budget) for t in carrying.values()]
    return PDF if PDF in verdicts else None


def _startxref_chain(data: bytes, budget: _Budget) -> list[dict]:
    at = data.rfind(b"startxref", max(0, len(data) - (1 << 20)))
    if at < 0:
        return []
    m = _STARTXREF.match(data, at)
    off = int(m.group(1)) if m else -1
    chain: list[dict] = []
    seen: set[int] = set()
    while 0 <= off < len(data) and off not in seen and len(chain) < 64:
        seen.add(off)
        t = _trailer_at(data, off, budget)
        if t is None:
            break
        chain.append(t)
        prev = t.get("Prev")
        off = prev if isinstance(prev, int) and not isinstance(prev, bool) else -1
    return chain


def _trailer_at(data: bytes, off: int, budget: _Budget) -> dict | None:
    lx = _Lexer(data, off, budget)
    lx.ws()
    if data.startswith(b"xref", lx.p):
        t = data.find(b"trailer", lx.p)
        return _dict_at(data, t + len(b"trailer"), budget) if t >= 0 else None
    m = _OBJ_HEAD.match(data, lx.p)
    if m:
        d = _dict_at(data, m.end(), budget)
        return d if d is not None and d.get("Type") == "XRef" else None
    return None


def _dict_at(data: bytes, pos: int, budget: _Budget, span: list | None = None) -> dict | None:
    lx = _Lexer(data, pos, budget)
    try:
        v = lx.value()
    except (ValueError, IndexError, KeyError, UnicodeDecodeError):
        return None
    if span is not None:
        span[:] = [pos, lx.p]
    return v if isinstance(v, dict) else None


def _repair_candidates(data: bytes, budget: _Budget) -> list[dict]:
    """Trailer dictionaries a repairing reader could use: after `trailer` keywords, cross-reference
    stream dictionaries, and whatever dictionary encloses each `/Encrypt` near the end."""
    out: list[dict] = []
    for m in _last_matches(_TRAILER, data, _MAX_CANDIDATES):
        t = _dict_at(data, m.end(), budget)
        if t is not None:
            out.append(t)
    for m in _last_matches(_XREF_NAME, data, _MAX_CANDIDATES):
        start = data.rfind(b"obj", max(0, m.start() - 4096), m.start())
        if start >= 0:
            t = _dict_at(data, start + 3, budget)
            if t is not None and t.get("Type") == "XRef":
                out.append(t)
    for m in _last_matches(_ENCRYPT_NAME, data, _MAX_CANDIDATES):
        lo, tried = m.start(), 0
        while tried < 16:
            lo = data.rfind(b"<<", max(0, m.start() - (1 << 16)), lo)
            if lo < 0:
                break
            tried += 1
            span: list = []
            t = _dict_at(data, lo, budget, span)
            if t is not None and span[0] < m.start() < span[1] and "Encrypt" in t:
                out.append(t)
                break
    return out


def _resolve(data: bytes, value, budget: _Budget, depth: int = 0):
    """A direct value, or the newest definition of the object an indirect reference names. The
    encryption dictionary is never inside an object stream (ISO 32000-2 §7.5.7)."""
    if not isinstance(value, _Ref) or depth > 4:
        return value
    if value in budget.objects:
        return budget.objects[value]
    if len(budget.objects) >= _MAX_OBJECT_SEARCHES:
        raise _Unchecked(PDF, "object search bound")
    budget.objects[value] = None                  # a reference cycle resolves to nothing
    at = _find_object(data, value[0], value[1], budget)
    out = _resolve(data, _Lexer(data, at, budget).value(), budget, depth + 1) if at is not None else None
    budget.objects[value] = out
    return out


def _find_object(data: bytes, num: int, gen: int, budget: _Budget) -> int | None:
    """Where the newest `num gen obj` starts its value: the spelling writers use (one space) by a
    fast backwards search, any other whitespace by a regular expression within a byte budget."""
    literal = b"%d %d obj" % (num, gen)
    end, tries = len(data), 0
    while tries < 64:
        at = data.rfind(literal, 0, end)
        if at < 0:
            break
        if at == 0 or not data[at - 1:at].isdigit():
            return at + len(literal)
        end, tries = at, tries + 1
    budget.scanned += len(data)
    if budget.scanned > _REGEX_SCAN_BUDGET:
        raise _Unchecked(PDF, "object search budget")
    found = _last_matches(re.compile(rb"(?<![0-9])%d\s+%d\s+obj\b" % (num, gen)), data, 1)
    return found[0].end() if found else None


def _judge(data: bytes, trailer: dict, budget: _Budget) -> str | None:
    enc = _resolve(data, trailer["Encrypt"], budget)
    if not isinstance(enc, dict):
        return PDF
    ids = _resolve(data, trailer.get("ID"), budget)
    id0 = _resolve(data, ids[0], budget) if isinstance(ids, list) and ids else b""
    id0 = id0 if isinstance(id0, bytes) else b""
    return None if _opens_without_password(data, enc, id0, budget) else PDF


def _num(data: bytes, value, default: int, budget: _Budget) -> int:
    v = _resolve(data, value, budget)
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _opens_without_password(data: bytes, enc: dict, id0: bytes, budget: _Budget) -> bool:
    """ISO 32000 standard security handler: does the empty user password open the document?"""
    if _resolve(data, enc.get("Filter"), budget) != "Standard":
        return False                          # public-key and other handlers need a key
    r = _num(data, enc.get("R"), 0, budget)
    v = _num(data, enc.get("V"), 0, budget)
    o = _resolve(data, enc.get("O"), budget)
    u = _resolve(data, enc.get("U"), budget)
    if not isinstance(o, bytes) or not isinstance(u, bytes):
        return False
    if r in (5, 6):
        if len(u) < 48:
            return False
        check = hashlib.sha256(u[32:40]).digest() if r == 5 else _hash_2b(b"", u[32:40], b"")
        return check == u[:32]
    if r not in (2, 3, 4) or len(o) < 32 or len(u) < 16:
        return False
    p = _num(data, enc.get("P"), 0, budget) & 0xFFFFFFFF
    if r == 2:
        n = 5
    else:
        bits = _num(data, enc.get("Length"), 40, budget)
        if v == 4:
            cf = _resolve(data, enc.get("CF"), budget)
            names = [str(_resolve(data, enc.get(k), budget) or "") for k in ("StmF", "StrF")]
            name = next((x for x in names if x and x != "Identity"), "StdCF")
            filt = _resolve(data, cf.get(name), budget) if isinstance(cf, dict) else None
            bits = _num(data, filt.get("Length"), 128, budget) if isinstance(filt, dict) else 128
            if bits < 40:
                bits *= 8                     # crypt filters give the length in bytes
        n = max(5, min(16, bits // 8))
    h = hashlib.md5(_PAD + o[:32] + struct.pack("<I", p) + id0)
    if r >= 4 and _resolve(data, enc.get("EncryptMetadata"), budget) is False:
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
_7Z_SIG = b"7z\xbc\xaf\x27\x1c"
_7Z_AES = b"\x06\xf1\x07\x01"
_7Z_LZMA = b"\x03\x01\x01"
_7Z_LZMA2 = b"\x21"
_7Z_COPY = b"\x00"
_7Z_DEFLATE = b"\x04\x01\x08"
_7Z_HEADER_CAP = 16 * 1024 * 1024
_7Z_MAX_CODERS = 100_000
_MAX_SIGNATURES = 64


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
        if n > len(self.b) * 8:
            raise ValueError("more digests than the header holds")
        defined = n if self.byte() else sum(bin(x).count("1") for x in self.take((n + 7) // 8))
        self.take(4 * defined)


class _Coders:
    """What the checks need from StreamsInfo, without keeping every folder: whether any coder is
    AES, the first folder's coders and unpack size, and the first packed stream."""

    def __init__(self, work: dict):
        self.aes = False
        self.work = work                          # shared by every header of one file
        self.first: list[tuple[bytes, bytes]] | None = None
        self.first_unpack: int | None = None
        self.pack_pos = 0
        self.first_pack: int | None = None


def _7z_streams(r: _SevenZipReader, c: _Coders) -> _Coders:
    """StreamsInfo, reading every coder (for AES) but keeping only the first folder. Counts are
    bounded: pack streams, coders and out streams together stay under `_7Z_MAX_CODERS`."""
    while True:
        prop = r.byte()
        if prop == 0x00:
            return c
        if prop == 0x06:                              # PackInfo
            c.pack_pos = r.number()
            n = r.number()
            c.work["coders"] += n
            if c.work["coders"] > _7Z_MAX_CODERS:
                raise _Unchecked(ARCHIVE, "pack stream bound")
            while True:
                sub = r.byte()
                if sub == 0x00:
                    break
                if sub == 0x09:
                    for i in range(n):
                        size = r.number()
                        if i == 0:
                            c.first_pack = size
                elif sub == 0x0A:
                    r.digests(n)
                else:
                    raise ValueError("unknown PackInfo property")
        elif prop == 0x07:                            # UnPackInfo
            if r.byte() != 0x0B:
                raise ValueError("expected folders")
            nf = r.number()
            if r.byte() != 0:
                raise _Unchecked(ARCHIVE, "folders stored outside the header")
            outs: list[int] = []
            for f in range(nf):
                nc = r.number()
                if not 0 < nc <= 64:
                    raise ValueError("coder count out of range")
                n_in = n_out = 0
                coders = []
                for _ in range(nc):
                    flags = r.byte()
                    cid = r.take(flags & 0x0F)
                    ci, co = (r.number(), r.number()) if flags & 0x10 else (1, 1)
                    props = r.take(r.number()) if flags & 0x20 else b""
                    if flags & 0x80:
                        raise ValueError("alternative coders")
                    if cid == _7Z_AES:
                        c.aes = True
                    if f == 0:
                        coders.append((cid, props))
                    n_in, n_out = n_in + ci, n_out + co
                if not 0 < n_out <= 64 or n_in > 64:
                    raise ValueError("stream counts out of range")
                c.work["coders"] += nc + n_out
                if c.work["coders"] > _7Z_MAX_CODERS:
                    raise _Unchecked(ARCHIVE, "coder bound")
                for _ in range(n_out - 1):              # bind pairs
                    r.number(), r.number()
                if n_in - (n_out - 1) > 1:              # packed stream indexes
                    for _ in range(n_in - (n_out - 1)):
                        r.number()
                if f == 0:
                    c.first = coders
                outs.append(n_out)
            while True:
                sub = r.byte()
                if sub == 0x00:
                    break
                if sub == 0x0C:                         # one size per out stream of every folder
                    for f, count in enumerate(outs):
                        for i in range(count):
                            size = r.number()
                            if f == 0 and i == 0:
                                c.first_unpack = size
                elif sub == 0x0A:
                    r.digests(nf)
                else:
                    raise ValueError("unknown UnPackInfo property")
        elif prop == 0x08:                            # SubStreamsInfo: nothing about coders
            return c
        else:
            raise ValueError("unknown StreamsInfo property")


def _sevenzip(data: bytes) -> str | None:
    pos, seen = 0, 0
    work = {"coders": 0, "unpacked": 0}           # one budget for every signature in the file
    while True:
        p = data.find(_7Z_SIG, pos)
        if p < 0:
            return None
        seen += 1
        if seen > _MAX_SIGNATURES:
            raise _Unchecked(ARCHIVE, "too many 7z signatures")
        if p + 32 <= len(data) and data[p + 6] == 0:
            off, size = struct.unpack_from("<QQ", data, p + 12)
            start = p + 32 + off
            if 0 < size and start + size <= len(data):   # a header readers can reach
                kind = _7z_checked(data, p, data[start:start + size], work) if size <= _7Z_HEADER_CAP else ARCHIVE
                if kind:
                    return kind
        pos = p + 1


@_guard(ARCHIVE)
def _7z_checked(data: bytes, base: int, header: bytes, work: dict, depth: int = 0) -> str | None:
    r = _SevenZipReader(header)
    kind = r.byte()
    if kind == 0x17:                                  # EncodedHeader: the real header is packed
        c = _7z_streams(r, _Coders(work))
        if c.aes:
            return ARCHIVE                            # the header itself is encrypted (-mhe=on)
        if depth > 2 or not c.first or len(c.first) != 1 or c.first_pack is None or not c.first_unpack:
            raise _Unchecked(ARCHIVE, "packed header layout not evaluated")
        cid, props = c.first[0]
        usize, start, psize = c.first_unpack, base + 32 + c.pack_pos, c.first_pack
        work["unpacked"] += usize
        if usize > _7Z_HEADER_CAP or work["unpacked"] > 2 * _7Z_HEADER_CAP:
            raise _Unchecked(ARCHIVE, "packed header over the bound")
        if start + psize > len(data):
            return None                               # truncated: no reader gets past this
        return _7z_checked(data, base, _7z_unpack(cid, props, data[start:start + psize], usize), work, depth + 1)
    if kind != 0x01:
        raise ValueError("not a header")
    c = _Coders(work)
    while True:
        prop = r.byte()
        if prop == 0x00:
            return None
        if prop == 0x02:                              # ArchiveProperties
            while r.number() != 0:
                r.take(r.number())
        elif prop in (0x03, 0x04):                    # AdditionalStreamsInfo / MainStreamsInfo
            _7z_streams(r, c)
            if c.aes:
                return ARCHIVE
            if prop == 0x04:
                return None
        elif prop == 0x05:                            # FilesInfo without streams: no data to encrypt
            return None
        else:
            raise ValueError("unknown header property")


def _7z_unpack(cid: bytes, props: bytes, packed: bytes, usize: int) -> bytes:
    if cid == _7Z_COPY:
        out = packed[:usize]
    elif cid == _7Z_DEFLATE:
        out = zlib.decompressobj(-15).decompress(packed, usize)
    elif cid == _7Z_LZMA and len(props) == 5:
        d = props[0]
        lc, d = d % 9, d // 9
        lp, pb = d % 5, d // 5
        # The dictionary never needs to be larger than the output: clamp what the file asks for.
        dict_size = max(4096, min(struct.unpack_from("<I", props, 1)[0], usize))
        out = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": lc, "lp": lp, "pb": pb}]).decompress(packed, max_length=usize)
    elif cid == _7Z_LZMA2 and len(props) == 1 and props[0] <= 40:
        p = props[0]
        declared = 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)
        out = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
            {"id": lzma.FILTER_LZMA2, "dict_size": max(4096, min(declared, usize))}]).decompress(packed, max_length=usize)
    else:
        raise _Unchecked(ARCHIVE, "header packed with a coder this check does not unpack")
    if len(out) != usize:
        raise _Unchecked(ARCHIVE, "packed header shorter than declared")
    return out


# =================================================================================== RAR
_RAR_SIG = b"Rar!\x1a\x07"
_RAR_MAX_BLOCKS = 100_000


def _rar(data: bytes) -> str | None:
    pos, seen = 0, 0
    work = {"blocks": 0}                          # one budget for every signature in the file
    while True:
        p = data.find(_RAR_SIG, pos)
        if p < 0:
            return None
        seen += 1
        if seen > _MAX_SIGNATURES:
            raise _Unchecked(ARCHIVE, "too many RAR signatures")
        kind = None
        if data[p + 6:p + 8] == b"\x01\x00":
            kind = _rar5(data, p + 8, work)
        elif data[p + 6:p + 7] == b"\x00":
            kind = _rar4(data, p + 7, work)
        if kind:
            return kind
        pos = p + 1


@_guard(ARCHIVE)
def _rar4(data: bytes, pos: int, work: dict) -> str | None:
    """RAR 2.9–4.x blocks: HEAD_CRC, HEAD_TYPE, HEAD_FLAGS, HEAD_SIZE [, ADD_SIZE]."""
    while pos + 7 <= len(data):
        work["blocks"] += 1
        if work["blocks"] > _RAR_MAX_BLOCKS:
            raise _Unchecked(ARCHIVE, "block bound")
        _crc, htype, flags, hsize = struct.unpack_from("<HBHH", data, pos)
        if hsize < 7:
            return None                              # readers stop at a broken block too
        add = 0
        if htype == 0x73 and flags & 0x0080:          # MAIN_HEAD, MHD_PASSWORD: headers encrypted
            return ARCHIVE
        if htype in (0x74, 0x7A):                     # FILE_HEAD / NEWSUB_HEAD
            if flags & 0x0004:                        # LHD_PASSWORD
                return ARCHIVE
            if pos + 11 > len(data):
                return None
            add = struct.unpack_from("<I", data, pos + 7)[0]
            if flags & 0x0100 and pos + 36 <= len(data):   # LHD_LARGE: HIGH_PACK_SIZE
                add |= struct.unpack_from("<I", data, pos + 32)[0] << 32
        elif flags & 0x8000 and pos + 11 <= len(data):
            add = struct.unpack_from("<I", data, pos + 7)[0]
        if htype == 0x7B:                             # ENDARC
            return None
        pos += hsize + add
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


@_guard(ARCHIVE)
def _rar5(data: bytes, pos: int, work: dict) -> str | None:
    while pos + 5 <= len(data):
        work["blocks"] += 1
        if work["blocks"] > _RAR_MAX_BLOCKS:
            raise _Unchecked(ARCHIVE, "block bound")
        try:
            hsize, hstart = _vint(data, pos + 4)
        except ValueError:
            return None
        hend = hstart + hsize
        if hsize < 2 or hend > len(data):
            return None                               # truncated: readers stop here as well
        htype, p = _vint(data, hstart)
        hflags, p = _vint(data, p)
        extra = dsize = 0
        if hflags & 0x0001:
            extra, p = _vint(data, p)
        if hflags & 0x0002:
            dsize, p = _vint(data, p)
        if htype == 4:                                # archive encryption header
            return ARCHIVE
        if htype in (2, 3) and 0 < extra <= hend - p:  # file / service header: extra records
            q, records = hend - extra, 0
            while q < hend and records < 64:
                records += 1
                rsize, r0 = _vint(data, q)
                rtype, _ = _vint(data, r0)
                if rtype == 0x01:                     # file encryption record
                    return ARCHIVE
                if rsize == 0:
                    break
                q = r0 + rsize
        if htype == 5:                                # end of archive
            return None
        pos = hend + dsize
    return None


_KIND_OF = {_cfb: OFFICE, _zip: ARCHIVE, _pdf: PDF, _sevenzip: ARCHIVE, _rar: ARCHIVE}
