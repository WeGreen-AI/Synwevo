"""Detect the real format of an uploaded file from its bytes (not its extension)."""
from __future__ import annotations

import io
import re
import struct
import zipfile

from .limits import LIMITS, LimitError

# Parts read while sniffing a zip.  Both are tiny in any real file; their declared size is
# checked before a single byte is inflated, so a crafted archive cannot make detection itself
# expand gigabytes (detection runs before the package limits in ooxml.Package).
_MAX_MIMETYPE_BYTES = 256
_MAX_CONTENT_TYPES_BYTES = 1024 * 1024
_PDF_HEADER = re.compile(rb"%PDF-\d\.\d")

FAMILY = {
    "csv": "table", "tsv": "table", "xlsx": "table", "xlsm": "table", "xls": "table", "ods": "table",
    "docx": "document", "docm": "document", "doc": "document", "odt": "document", "rtf": "document",
    "pdf": "pdf",
    "png": "image", "jpeg": "image", "tiff": "image", "bmp": "image", "webp": "image", "gif": "image",
    "pptx": "slides", "pptm": "slides", "ppt": "slides", "odp": "slides",
    "txt": "text", "md": "text", "html": "text",
}

# formats read through a LibreOffice conversion to an OOXML equivalent
CONVERT_VIA = {"doc": "docx", "odt": "docx", "rtf": "docx", "ods": "xlsx", "ppt": "pptx", "odp": "pptx"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def detect(data: bytes, filename: str) -> dict:
    """Return {'format', 'family', 'encrypted', 'macros', 'notes': [...]} or raise ValueError."""
    ext = _ext(filename)
    notes: list[str] = []
    head = data[:2048]
    fmt = None
    encrypted = False
    macros = False
    if not data:
        raise ValueError("文件为空")
    if _PDF_HEADER.match(data) or (ext == "pdf" and _PDF_HEADER.search(data[:1024])):
        # the header belongs at offset 0; readers tolerate a little junk before it, which we
        # accept only for a file that also says it is a PDF (a CSV quoting "%PDF-1.4" is text)
        fmt = "pdf"
    elif head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        fmt, macros = _zip_format(data)
    elif head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        fmt, encrypted, macros = _ole_format(data, ext)
    elif head.startswith(b"{\\rtf"):
        fmt = "rtf"
    elif head.startswith(b"\x89PNG\r\n\x1a\n"):
        fmt = "png"
    elif head.startswith(b"\xff\xd8\xff"):
        fmt = "jpeg"
    elif head[:4] in (b"II*\x00", b"MM\x00*"):
        fmt = "tiff"
    elif head.startswith(b"BM") and _is_bmp(head):
        fmt = "bmp"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        fmt = "webp"
    elif head[:6] in (b"GIF87a", b"GIF89a"):
        fmt = "gif"
    else:
        fmt = _text_format(data, ext)
    if fmt is None:
        raise ValueError("无法识别的文件格式（不是受支持的文档、表格、PDF、图片或文本文件）")
    if ext and ext != fmt and not (ext in ("jpg", "jpeg") and fmt == "jpeg") and not (ext in ("htm", "html") and fmt == "html") \
            and not (ext in ("tif", "tiff") and fmt == "tiff") and not (ext in ("markdown",) and fmt == "md"):
        notes.append(f"扩展名为 .{ext}，实际内容是 {fmt.upper()}，按实际格式处理")
    if macros:
        notes.append("文件包含宏（VBA）。本软件不会执行宏；宏代码在输出中的保留情况见格式说明")
    return {"format": fmt, "family": FAMILY.get(fmt, "unknown"), "encrypted": encrypted, "macros": macros, "notes": notes,
            "convert_via": CONVERT_VIA.get(fmt)}


def _is_bmp(head: bytes) -> bool:
    """A real BITMAPFILEHEADER, not just text that starts with "BM" (a CSV column "BMI")."""
    if len(head) < 26:
        return False
    reserved = head[6:10]
    offset, dib = struct.unpack("<II", head[10:18])
    return reserved == b"\0\0\0\0" and dib in (12, 16, 40, 52, 56, 64, 108, 124) and 14 + dib <= offset


def _bounded_read(z: zipfile.ZipFile, name: str, cap: int) -> bytes:
    info = z.getinfo(name)
    if info.file_size > cap:
        raise LimitError(f"压缩包中的「{name}」声明大小 {info.file_size:,} 字节，超过正常文件的上限，已拒绝")
    with z.open(info) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise LimitError(f"压缩包中的「{name}」实际大小超过上限，已拒绝")
    return data


def _zip_format(data: bytes) -> tuple[str | None, bool]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("压缩结构损坏，无法打开")
    infos = z.infolist()
    if len(infos) > LIMITS.max_zip_entries:
        raise LimitError("压缩包条目数量超过上限")
    if sum(i.file_size for i in infos) > LIMITS.max_unzipped_bytes:
        raise LimitError(f"压缩包展开后超过上限 {LIMITS.max_unzipped_bytes // (1024 * 1024)} MB")
    names = set(z.namelist())
    if "mimetype" in names:
        mt = _bounded_read(z, "mimetype", _MAX_MIMETYPE_BYTES).decode("ascii", "ignore").strip()
        if mt == "application/vnd.oasis.opendocument.text":
            return "odt", False
        if mt == "application/vnd.oasis.opendocument.spreadsheet":
            return "ods", False
        if mt == "application/vnd.oasis.opendocument.presentation":
            return "odp", False
    if "[Content_Types].xml" in names:
        ct = _bounded_read(z, "[Content_Types].xml", _MAX_CONTENT_TYPES_BYTES).decode("utf-8", "ignore")
        macros = any(n.endswith("vbaProject.bin") for n in names)
        if "word/document.xml" in names or "wordprocessingml.document" in ct:
            return ("docm" if "macroEnabled" in ct else "docx"), macros
        if "xl/workbook.xml" in names or "spreadsheetml" in ct:
            return ("xlsm" if "macroEnabled" in ct else "xlsx"), macros
        if "ppt/presentation.xml" in names or "presentationml" in ct:
            return ("pptm" if "macroEnabled" in ct else "pptx"), macros
    return None, False


def _ole_format(data: bytes, ext: str) -> tuple[str | None, bool, bool]:
    try:
        import olefile
        ole = olefile.OleFileIO(io.BytesIO(data))
        streams = {"/".join(s) for s in ole.listdir()}
        ole.close()
    except Exception:
        return ({"doc": "doc", "xls": "xls", "ppt": "ppt"}.get(ext), False, False)
    if "EncryptedPackage" in streams or "EncryptionInfo" in streams:
        fmt = {"docx": "docx", "xlsx": "xlsx", "pptx": "pptx"}.get(ext, "docx" if ext.startswith("doc") else "xlsx" if ext.startswith("xl") else "pptx")
        return fmt, True, False
    macros = any(s.startswith("Macros") or s.startswith("_VBA_PROJECT_CUR") or "VBA" in s for s in streams)
    if "WordDocument" in streams:
        return "doc", False, macros
    if "Workbook" in streams or "Book" in streams:
        encrypted = False
        return "xls", encrypted, macros
    if "PowerPoint Document" in streams:
        return "ppt", False, macros
    return None, False, False


def _text_format(data: bytes, ext: str) -> str | None:
    sample = data[:65536]
    text = None
    for enc in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            text = sample.decode(enc)
            if enc == "utf-16" and not (sample.startswith(b"\xff\xfe") or sample.startswith(b"\xfe\xff")):
                text = None
                continue
            break
        except UnicodeDecodeError:
            # the sample may cut a multi-byte char at the end
            try:
                text = sample[:-3].decode(enc)
                break
            except UnicodeDecodeError:
                text = None
    if text is None:
        return None
    ctrl = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t\f")
    if ctrl > max(8, len(text) // 100):
        return None
    low = text[:4096].lower()
    if ext in ("html", "htm") or "<!doctype html" in low or "<html" in low:
        return "html"
    if ext in ("md", "markdown"):
        return "md"
    if ext == "tsv":
        return "tsv"
    if ext == "csv":
        return "csv"
    if ext in ("txt", "text", "log", ""):
        return "txt"
    return "txt"
