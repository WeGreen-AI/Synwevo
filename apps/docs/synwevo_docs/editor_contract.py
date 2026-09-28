"""Validate the persistent editor model before either UI or API builds an office file.

Unknown content must not disappear in an otherwise successful save. UI-only metadata with
no output effect is accepted explicitly; unsupported node types are refused with a location.
"""
from __future__ import annotations

import math
import re

from synwevo_core.errors import ServiceError
from .engine.limits import LIMITS
from .engine.templates import EMPTY_POLICIES
from .engine.builtin import PAPER_MM


DOC_KEYS = {"body", "header", "footer", "page", "base_size", "font_ea"}
BLOCKS = {"paragraph", "heading", "bulletList", "orderedList", "table", "pageBreak", "horizontalRule", "image", "blockquote", "repeatBlock"}
CHILDREN = {
    "doc": BLOCKS, "blockquote": BLOCKS - {"repeatBlock"},
    "repeatBlock": {"paragraph", "heading", "bulletList", "orderedList", "image", "horizontalRule"},
    "bulletList": {"listItem"}, "orderedList": {"listItem"}, "listItem": BLOCKS - {"repeatBlock"},
    "table": {"tableRow"}, "tableRow": {"tableCell", "tableHeader"},
    "tableCell": {"paragraph", "heading"}, "tableHeader": {"paragraph", "heading"},
    "paragraph": {"text", "hardBreak", "field"}, "heading": {"text", "hardBreak", "field"},
    "text": set(), "hardBreak": set(), "field": set(), "image": set(), "pageBreak": set(), "horizontalRule": set(),
}
MARKS = {"bold", "italic", "underline", "strike", "textStyle", "highlight"}


def _bad(where, message, **extra):
    raise ServiceError(400, "bad_editor", message, {"field": f"editor.{where}".rstrip("."), **extra})


def _object(value, where, keys=None):
    if not isinstance(value, dict):
        _bad(where, "必须是对象")
    if keys is not None and (extra := set(value) - keys):
        _bad(f"{where}.{sorted(extra)[0]}".strip("."), "不支持的编辑器选项")
    return value


def _enum(obj, key, choices, where):
    if key in obj and obj[key] is not None and obj[key] not in choices:
        _bad(f"{where}.{key}", "取值只能是 " + " / ".join(choices))


def _number(value, where, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        _bad(where, f"必须是 {low}–{high} 范围内的有限数字", min=low, max=high)


def _list(value, where):
    if not isinstance(value, list):
        _bad(where, "必须是数组")
    return value


def _bool_fields(obj, names, where, *, legacy_numeric=False):
    for key in names:
        if key in obj and type(obj[key]) is not bool and not (legacy_numeric and type(obj[key]) is int and obj[key] in (0, 1)):
            _bad(f"{where}.{key}", "必须是布尔值")


def _colour(obj, key, where):
    value = obj.get(key)
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", value)):
        _bad(f"{where}.{key}", "必须是六位十六进制颜色")


def _document(part, where):
    stack = [(part, where, {"doc"}, 0)]
    count = 0
    while stack:
        node, path, allowed, depth = stack.pop()
        count += 1
        if depth > 40 or count > 100_000:
            _bad(path, "文档结构过深或节点过多")
        _object(node, path, {"type", "attrs", "content", "text", "marks"})
        typ = node.get("type")
        if not isinstance(typ, str) or typ not in allowed:
            _bad(path + ".type", "当前位置不支持该文档节点；未保存任何版本")
        attrs = _object(node.get("attrs", {}), path + ".attrs")
        _enum(attrs, "textAlign", ("left", "center", "right", "justify"), path + ".attrs")
        _enum(attrs, "dataType", ("auto", "text", "number", "date"), path + ".attrs")
        _bool_fields(attrs, ("required", "repeat"), path + ".attrs")
        if typ == "heading" and (type(attrs.get("level", 1)) is not int or not 1 <= attrs.get("level", 1) <= 6):
            _bad(path + ".attrs.level", "标题级别必须是 1–6 的整数")
        for key in ("colspan", "rowspan"):
            if key in attrs and (type(attrs[key]) is not int or attrs[key] < 1):
                _bad(path + ".attrs." + key, "合并跨度必须是正整数")
        if typ == "repeatBlock":
            _enum(attrs, "separator", ("none", "blank", "page_break"), path + ".attrs")
            _enum(attrs, "emptyPolicy", EMPTY_POLICIES["block"], path + ".attrs")
        if typ == "tableRow":
            _enum(attrs, "emptyPolicy", EMPTY_POLICIES["table"], path + ".attrs")
        if typ == "image":
            if not isinstance(attrs.get("src"), str) or not re.fullmatch(r"data:image/(png|jpeg|jpg|gif|bmp);base64,[A-Za-z0-9+/=\s]+", attrs["src"]):
                _bad(path + ".attrs.src", "只支持嵌入的 PNG/JPEG/GIF/BMP Base64 图片，不读取远程 URL")
            _enum(attrs, "align", ("left", "center", "right", "justify"), path + ".attrs")
            if attrs.get("width") is not None:
                _number(attrs["width"], path + ".attrs.width", 1, 10000)
        _colour(attrs, "background", path + ".attrs")
        if typ == "text" and not isinstance(node.get("text"), str):
            _bad(path + ".text", "文本节点必须提供字符串")
        for i, mark in enumerate(_list(node.get("marks", []), path + ".marks")):
            _object(mark, f"{path}.marks.{i}", {"type", "attrs"})
            if not isinstance(mark.get("type"), str) or mark["type"] not in MARKS:
                _bad(f"{path}.marks.{i}.type", "不支持的文字格式")
            mp = f"{path}.marks.{i}.attrs"
            ma = _object(mark.get("attrs", {}), mp)
            if mark["type"] == "textStyle":
                _colour(ma, "color", mp)
                if ma.get("fontSize") is not None:
                    size = str(ma["fontSize"])
                    if not re.fullmatch(r"\d+(?:\.\d+)?(?:pt|px)?", size):
                        _bad(mp + ".fontSize", "字号必须是数字，可带 pt 或 px 后缀")
                    _number(float(re.sub(r"(?:pt|px)$", "", size)), mp + ".fontSize", 4, 144)
            if mark["type"] == "highlight":
                _enum(ma, "color", ("yellow", "#ffff00", "#FFFF00"), mp)
        children = _list(node.get("content", []), path + ".content")
        for i, child in enumerate(children):
            stack.append((child, f"{path}.content.{i}", CHILDREN[typ], depth + 1))


def validate_editor(kind, editor):
    _object(editor, "")
    if kind == "builtin_doc":
        # Older clients sent a bare empty ProseMirror document. Preserve that form and
        # actually render its content instead of silently treating it as an empty template.
        if editor.get("type") == "doc":
            editor = {"body": editor}
        _object(editor, "", DOC_KEYS)
        page = _object(editor.get("page", {}), "page", {"size", "orientation", "margins"})
        _enum(page, "size", ("A4", "A3", "A5", "Letter", "Legal", "B5"), "page")
        _enum(page, "orientation", ("portrait", "landscape"), "page")
        margins = _object(page.get("margins", {}), "page.margins", {"top", "bottom", "left", "right"})
        for k, v in margins.items():
            _number(v, "page.margins." + k, 0, 100)
        width, height = PAPER_MM[page.get("size") or "A4"]
        if page.get("orientation") == "landscape":
            width, height = height, width
        if margins.get("left", 31.8) + margins.get("right", 31.8) >= width or margins.get("top", 25.4) + margins.get("bottom", 25.4) >= height:
            _bad("page.margins", "页边距必须为正文保留可用区域")
        if "base_size" in editor:
            _number(editor["base_size"], "base_size", 4, 144)
        for part in ("body", "header", "footer"):
            if part in editor:
                _document(editor[part], part)
    elif kind == "builtin_sheet":
        _object(editor, "", {"sheets"})
        sheets = _list(editor.get("sheets", []), "sheets")
        if len(sheets) > 100:
            _bad("sheets", "最多支持 100 个工作表")
        count = 0
        field_ids = set()
        for i, sheet in enumerate(sheets):
            where = f"sheets.{i}"
            _object(sheet, where, {"name", "cols", "rows", "cells", "merges", "fields"})
            cells = _object(sheet.get("cells", {}), where + ".cells")
            count += len(cells)
            if count > LIMITS.max_cells:
                _bad(where + ".cells", "单元格数超过服务上限")
            for ref, cell in cells.items():
                cp = where + ".cells." + ref
                _object(cell, cp, {"v", "t", "s"})
                _enum(cell, "t", ("text", "number", "auto"), cp)
                if cell.get("v") is not None and not isinstance(cell["v"], (str, int, float, bool)):
                    _bad(cp + ".v", "单元格值必须是文本、数字、布尔值或 null")
                if isinstance(cell.get("v"), float) and not math.isfinite(cell["v"]):
                    _bad(cp + ".v", "数字必须有限")
                if cell.get("t") == "number" and cell.get("v") not in (None, ""):
                    try:
                        numeric = float(cell["v"])
                    except (ValueError, TypeError, OverflowError):
                        _bad(cp + ".v", "声明为数字的单元格不能保存非数字文本")
                    if isinstance(cell["v"], bool) or not math.isfinite(numeric):
                        _bad(cp + ".v", "数字必须有限且不是布尔值")
                st = _object(cell.get("s", {}), cp + ".s", {"b", "i", "u", "sz", "color", "bg", "ha", "va", "wrap", "bd", "nf"})
                _enum(st, "ha", ("left", "center", "right", "justify", "fill", "distributed", "centerContinuous", "general"), cp + ".s")
                _enum(st, "va", ("top", "center", "bottom", "justify", "distributed"), cp + ".s")
                # The existing spreadsheet editor serializes these style toggles as 0/1.
                _bool_fields(st, ("b", "i", "u", "wrap", "bd"), cp + ".s", legacy_numeric=True)
                for key in ("color", "bg"):
                    _colour(st, key, cp + ".s")
                if "sz" in st:
                    _number(st["sz"], cp + ".s.sz", 4, 144)
            for j, field in enumerate(_list(sheet.get("fields", []), where + ".fields")):
                fp = f"{where}.fields.{j}"
                _object(field, fp, {"id", "name", "kind", "ref", "row", "c1", "c2", "required", "empty_policy"})
                _enum(field, "kind", ("cell", "region", "repeat"), fp)
                _enum(field, "empty_policy", EMPTY_POLICIES["table"], fp)
                _bool_fields(field, ("required",), fp)
                if field.get("id") is not None:
                    fid = field["id"]
                    if not isinstance(fid, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,99}", fid) or fid in field_ids:
                        _bad(fp + ".id", "字段 ID 必须有效且在整个模板中唯一")
                    field_ids.add(fid)
    else:
        raise ServiceError(400, "bad_kind", "kind 只能是 builtin_doc 或 builtin_sheet", {"field": "kind"})
    return editor


def editor_schemas():
    """Portable model shape for OpenAPI; contextual renderer checks remain server-side."""
    def obj(properties):
        return {"type": "object", "additionalProperties": False, "properties": properties}
    def arr(item):
        return {"type": "array", "items": item}
    def ref(name):
        return {"$ref": "#/components/schemas/" + name}
    string, number = {"type": "string"}, {"type": "number"}
    node = obj({"type": {"type": "string", "enum": sorted(CHILDREN)},
                "attrs": {"type": "object", "description": "按节点类型校验；字段 id/name/required/dataType、重复 repeatId/repeatName/emptyPolicy/separator、表格跨度、图片 src/width"},
                "text": string, "content": arr(ref("EditorNode")),
                "marks": arr(obj({"type": {"type": "string", "enum": sorted(MARKS)}, "attrs": {"type": "object"}}))})
    node["required"] = ["type"]
    document = obj({"body": ref("EditorNode"), "header": ref("EditorNode"), "footer": ref("EditorNode"),
                    "base_size": {"type": "number", "minimum": 4, "maximum": 144}, "font_ea": string,
                    "page": obj({"size": {"type": "string", "enum": list(PAPER_MM)},
                                 "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
                                 "margins": obj({k: {"type": "number", "minimum": 0, "maximum": 100} for k in ("top", "bottom", "left", "right")})})})
    legacy = {"allOf": [ref("EditorNode"), {"properties": {"type": {"const": "doc"}}}]}
    field = obj({"id": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_-]{0,99}$"}, "name": string,
                 "kind": {"type": "string", "enum": ["cell", "region", "repeat"]}, "ref": string,
                 "row": {"type": "integer", "minimum": 0}, "c1": {"type": "integer", "minimum": 0},
                 "c2": {"type": "integer", "minimum": 0}, "required": {"type": "boolean"},
                 "empty_policy": {"type": "string", "enum": list(EMPTY_POLICIES["table"])}})
    cell = obj({"v": {"type": ["string", "number", "boolean", "null"]},
                "t": {"type": ["string", "null"], "enum": ["text", "number", "auto", None]},
                "s": {"type": "object", "description": "样式 b/i/u/wrap/bd（bool 或兼容 0/1）、sz、color/bg（六位颜色）、ha/va、nf"}})
    sheet = obj({"name": string, "cols": arr(obj({"w": number})), "rows": arr(obj({"h": number})),
                 "cells": {"type": "object", "additionalProperties": cell}, "merges": arr(string), "fields": arr(field)})
    return {"EditorNode": node, "DocumentEditorModel": {"anyOf": [document, legacy]},
            "SpreadsheetEditorModel": obj({"sheets": {**arr(sheet), "maxItems": 100}}),
            "BuiltinEditorModel": {"anyOf": [ref("DocumentEditorModel"), ref("SpreadsheetEditorModel")]}}
