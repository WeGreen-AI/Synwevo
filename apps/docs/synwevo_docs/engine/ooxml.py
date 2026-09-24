"""Minimal OPC (zip) package access that rewrites only the parts we touch.

All other entries are copied byte-for-byte with their original compression so
that objects we do not understand (charts, SmartArt, custom XML, signatures ...)
survive a round trip.
"""
from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass

from lxml import etree

from .limits import LIMITS, LimitError

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "v": "urn:schemas-microsoft-com:vml",
    "x14": "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main",
    "xm": "http://schemas.microsoft.com/office/excel/2006/main",
}

REL_OFFICE_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True, remove_blank_text=False)


def q(prefix: str, local: str) -> str:
    return "{%s}%s" % (NS[prefix], local)


@dataclass
class Rel:
    id: str
    type: str
    target: str
    external: bool


class Package:
    def __init__(self, data: bytes):
        if len(data) > LIMITS.max_file_bytes:
            raise LimitError(f"文件超过大小上限 {LIMITS.max_file_bytes // (1024 * 1024)} MB")
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise ValueError("文件不是有效的 Office Open XML 压缩包（可能已损坏或被加密）") from e
        total = 0
        for info in self._zip.infolist():
            total += info.file_size
            if info.filename.startswith("/") or ".." in info.filename.split("/"):
                raise ValueError(f"压缩包包含不安全的路径：{info.filename}")
        if total > LIMITS.max_unzipped_bytes:
            raise LimitError(f"压缩包展开后超过上限 {LIMITS.max_unzipped_bytes // (1024 * 1024)} MB")
        if len(self._zip.infolist()) > LIMITS.max_zip_entries:
            raise LimitError("压缩包条目数量超过上限")
        self._infos = {i.filename: i for i in self._zip.infolist()}
        self._order = [i.filename for i in self._zip.infolist()]
        self._xml: dict[str, etree._Element] = {}
        self._raw: dict[str, bytes] = {}
        self._deleted: set[str] = set()

    # ---- entries -------------------------------------------------------
    @property
    def names(self) -> list[str]:
        return [n for n in self._order if n not in self._deleted] + [n for n in self._raw if n not in self._infos and n not in self._deleted]

    def has(self, name: str) -> bool:
        return (name in self._infos or name in self._raw or name in self._xml) and name not in self._deleted

    def read(self, name: str) -> bytes:
        if name in self._xml:
            return self._serialize(self._xml[name])
        if name in self._raw:
            return self._raw[name]
        return self._zip.read(name)

    def xml(self, name: str) -> etree._Element:
        if name not in self._xml:
            info = self._infos.get(name)
            if info is not None and name not in self._raw and info.file_size > LIMITS.max_xml_part_bytes:
                # huge_tree lifts libxml2's own depth/size guards, so the size is checked here,
                # before one part becomes a DOM many times its size
                raise LimitError(f"部件「{name}」展开后 {info.file_size // (1024 * 1024)} MB，超过单个部件上限 "
                                 f"{LIMITS.max_xml_part_bytes // (1024 * 1024)} MB")
            self._xml[name] = etree.fromstring(self.read(name), _PARSER)
        return self._xml[name]

    def set_xml(self, name: str, root: etree._Element) -> None:
        self._xml[name] = root
        self._deleted.discard(name)

    def mark_dirty(self, name: str) -> None:
        # xml() already caches the parsed tree; saving re-serialises cached trees.
        self.xml(name)

    def write(self, name: str, data: bytes) -> None:
        self._raw[name] = data
        self._xml.pop(name, None)
        self._deleted.discard(name)

    def delete(self, name: str) -> None:
        self._deleted.add(name)
        self._xml.pop(name, None)
        self._raw.pop(name, None)

    @staticmethod
    def _serialize(root) -> bytes:
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            written = set()
            for name in self._order:
                if name in self._deleted:
                    continue
                info = self._infos[name]
                if name in self._xml:
                    data = self._serialize(self._xml[name])
                elif name in self._raw:
                    data = self._raw[name]
                else:
                    data = self._zip.read(name)
                zi = zipfile.ZipInfo(name, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                z.writestr(zi, data)
                written.add(name)
            for name in list(self._raw) + list(self._xml):
                if name in written or name in self._deleted:
                    continue
                data = self._raw[name] if name in self._raw else self._serialize(self._xml[name])
                z.writestr(zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
                written.add(name)
        return out.getvalue()

    # ---- relationships --------------------------------------------------
    @staticmethod
    def rels_name(part: str) -> str:
        d, f = posixpath.split(part)
        return posixpath.join(d, "_rels", f + ".rels")

    def rels(self, part: str) -> list[Rel]:
        rn = self.rels_name(part) if part else "_rels/.rels"
        if not self.has(rn):
            return []
        root = self.xml(rn)
        out = []
        for el in root.findall(q("pr", "Relationship")):
            out.append(Rel(el.get("Id"), el.get("Type"), el.get("Target"), el.get("TargetMode") == "External"))
        return out

    def resolve(self, part: str, target: str) -> str:
        if target.startswith("/"):
            return target.lstrip("/")
        base = posixpath.dirname(part)
        return posixpath.normpath(posixpath.join(base, target))

    def rel_target(self, part: str, rid: str) -> str | None:
        for r in self.rels(part):
            if r.id == rid and not r.external:
                return self.resolve(part, r.target)
        return None

    def main_part(self) -> str:
        for r in self.rels(""):
            if r.type == REL_OFFICE_DOC:
                return r.target.lstrip("/")
        raise ValueError("找不到主文档部件")

    def content_types(self) -> etree._Element:
        return self.xml("[Content_Types].xml")

    def remove_part(self, part: str, owner: str | None = None) -> None:
        """Delete a part, its relationship from ``owner`` and its content-type override."""
        self.delete(part)
        ct = self.content_types()
        for el in list(ct):
            if el.get("PartName") == "/" + part:
                ct.remove(el)
        if owner:
            rn = self.rels_name(owner)
            if self.has(rn):
                root = self.xml(rn)
                for el in list(root):
                    if el.get("Target") and self.resolve(owner, el.get("Target")) == part:
                        root.remove(el)

    def add_part(self, part: str, data: bytes | etree._Element, content_type: str, owner: str, rel_type: str) -> str:
        if isinstance(data, bytes):
            self.write(part, data)
        else:
            self.set_xml(part, data)
        ct = self.content_types()
        if not any(el.get("PartName") == "/" + part for el in ct):
            o = etree.SubElement(ct, q("ct", "Override"))
            o.set("PartName", "/" + part)
            o.set("ContentType", content_type)
        rn = self.rels_name(owner)
        if self.has(rn):
            root = self.xml(rn)
        else:
            root = etree.Element(q("pr", "Relationships"), nsmap={None: NS["pr"]})
            self.set_xml(rn, root)
        ids = {el.get("Id") for el in root}
        i = 1
        while f"rId{i}" in ids:
            i += 1
        rid = f"rId{i}"
        el = etree.SubElement(root, q("pr", "Relationship"))
        el.set("Id", rid)
        el.set("Type", rel_type)
        el.set("Target", posixpath.relpath(part, posixpath.dirname(owner)))
        return rid

    def find_parts(self, prefix: str) -> list[str]:
        return [n for n in self.names if n.startswith(prefix)]

    def inventory(self) -> dict[str, int]:
        """Count notable object kinds by part name – used for loss detection."""
        kinds = {
            "charts": "/charts/", "drawings": "/drawings/", "comments": "comments", "vml": ".vml",
            "embeddings": "/embeddings/", "media": "/media/", "pivot": "pivot", "vba": "vbaProject",
            "customXml": "customXml/", "signatures": "_xmlsignatures/", "diagrams": "/diagrams/",
            "externalLinks": "externalLink", "slicers": "slicer", "ctrlProps": "ctrlProps", "tables": "/tables/",
            "activeX": "activeX", "ink": "/ink/",
        }
        out: dict[str, int] = {}
        for n in self.names:
            for k, pat in kinds.items():
                if pat in n and not n.endswith(".rels"):
                    out[k] = out.get(k, 0) + 1
        return out


# ------------------------------------------------------------------ macros
_MACRO_FREE_MAIN = {
    "application/vnd.ms-word.document.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-word.template.macroEnabledTemplate.main+xml":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-excel.template.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-excel.addin.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "application/vnd.ms-powerpoint.template.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    "application/vnd.ms-powerpoint.addin.macroEnabled.main+xml":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
}
_MACRO_PART_TYPES = {
    "application/vnd.ms-office.vbaProject", "application/vnd.ms-word.vbaData+xml",
    "application/vnd.ms-word.keyMapCustomizations+xml", "application/vnd.ms-word.attachedToolbars",
    "application/vnd.ms-office.vbaProjectSignature", "application/vnd.ms-office.vbaProjectSignatureAgile",
    "application/vnd.ms-office.vbaProjectSignatureV3",
}
_MACRO_REL_SUFFIXES = ("/vbaProject", "/wordVbaData", "/keyMapCustomizations", "/attachedToolbars",
                       "/vbaProjectSignature", "/vbaProjectSignatureAgile", "/vbaProjectSignatureV3")


def strip_vba(data: bytes) -> bytes:
    """The package with its VBA project removed and a macro-free main-part content type;
    everything else is left as it was.  Engine-side twin of ``services.strip_macros`` (the
    engine cannot import the service layer); a package without macros comes back unchanged."""
    pkg = Package(data)
    ct = pkg.content_types()
    defaults = {el.get("Extension", "").lower(): el.get("ContentType") for el in ct if el.tag == q("ct", "Default")}
    overrides = {el.get("PartName"): el.get("ContentType") for el in ct if el.tag == q("ct", "Override")}

    def ctype(name: str) -> str | None:
        return overrides.get("/" + name) or defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())

    macro_parts = {n for n in pkg.names if not n.endswith(".rels") and (ctype(n) in _MACRO_PART_TYPES or n.endswith("vbaProject.bin"))}
    macro_main = any(v in _MACRO_FREE_MAIN for v in overrides.values())
    if not macro_parts and not macro_main:
        return data
    for rn in [n for n in pkg.names if n.endswith(".rels")]:
        owner_dir = posixpath.dirname(posixpath.dirname(rn))
        owner = posixpath.join(owner_dir, posixpath.basename(rn)[:-len(".rels")]) if posixpath.basename(rn) != ".rels" else ""
        if owner in macro_parts:
            continue
        root = pkg.xml(rn)
        for el in list(root):
            target = el.get("Target") or ""
            resolved = pkg.resolve(owner, target) if owner or not target.startswith("/") else target.lstrip("/")
            if (el.get("Type") or "").endswith(_MACRO_REL_SUFFIXES) or resolved in macro_parts:
                root.remove(el)
    for n in macro_parts:
        pkg.delete(n)
        rn = pkg.rels_name(n)
        if pkg.has(rn):
            pkg.delete(rn)
    remaining_exts = {posixpath.splitext(n)[1].lstrip(".").lower() for n in pkg.names if "/" + n not in overrides}
    for el in list(ct):
        if el.tag == q("ct", "Override"):
            if el.get("PartName", "").lstrip("/") in macro_parts:
                ct.remove(el)
            elif el.get("ContentType") in _MACRO_FREE_MAIN:
                el.set("ContentType", _MACRO_FREE_MAIN[el.get("ContentType")])
        elif el.tag == q("ct", "Default") and el.get("ContentType") in _MACRO_PART_TYPES \
                and el.get("Extension", "").lower() not in remaining_exts:
            ct.remove(el)
    return pkg.save()
