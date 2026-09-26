"""ProseMirror document JSON ⇄ shared XML types of a live document.

The document editor keeps each part (body, header, footer) in a shared XML fragment of one live
document; the browser binds it with the same rules @tiptap/y-tiptap (y-prosemirror) uses:

* a block or inline element node becomes an XML element named after the node type, carrying the
  node's attributes except those that are ``null`` (``createTypeFromElementNode``);
* a run of adjacent text nodes becomes one XML text whose formatting attributes are the marks,
  keyed by mark name with the mark's attributes as the value (``marksToAttributes``);
* an inline non-text node (a field, a hard break) is an element of its own beside the text runs.

The server needs the same mapping so that it can seed a live document from a saved version, put a
restored version into an open room, and read the live document back for summaries. Reading back
normalises integral floats to integers: numbers travel through the CRDT as doubles.
"""
from __future__ import annotations

from typing import Any

from pycrdt import Doc, XmlElement, XmlFragment, XmlText

PARTS = ("body", "header", "footer")
EMPTY_DOC = {"type": "doc", "content": [{"type": "paragraph"}]}


def _norm(v: Any) -> Any:
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _attrs(node: dict) -> dict:
    return {k: v for k, v in (node.get("attrs") or {}).items() if v is not None and k != "ychange"}


def _marks(text_node: dict) -> dict | None:
    out = {}
    for mark in text_node.get("marks") or []:
        if not isinstance(mark, dict) or not mark.get("type") or mark["type"] == "ychange":
            continue
        out[mark["type"]] = mark.get("attrs") or {}
    return out or None


def _fill_element(target: XmlElement | XmlFragment, node: dict) -> None:
    """Append the children of a ProseMirror node to an integrated element or fragment."""
    children = node.get("content") or []
    i = 0
    while i < len(children):
        child = children[i]
        if child.get("type") == "text":
            text = target.children.append(XmlText())
            while i < len(children) and children[i].get("type") == "text":
                run = children[i]
                s = run.get("text") or ""
                if s:
                    # Append at the text's own length: its index unit is the library's, not
                    # Python's code points (non-ASCII text landed inside the previous run).
                    text.insert(len(text), s, _marks(run))
                i += 1
            continue
        el = target.children.append(XmlElement(child["type"], _attrs(child)))
        _fill_element(el, child)
        i += 1


def fill_fragment(fragment: XmlFragment, doc: dict | None) -> None:
    """Replace the fragment's content with a ProseMirror ``doc`` node's children."""
    if len(fragment.children):
        del fragment.children[:]
    _fill_element(fragment, doc or EMPTY_DOC)


def seed(ydoc: Doc, editor: dict | None) -> None:
    """Put the three parts of a saved editor document into a live document, in one transaction."""
    editor = editor or {}
    with ydoc.transaction():
        for part in PARTS:
            fill_fragment(ydoc.get(part, type=XmlFragment), editor.get(part) or EMPTY_DOC)


def _node_from(item: XmlElement | XmlText) -> list[dict]:
    if isinstance(item, XmlText):
        out = []
        for value, attrs in item.diff():
            if not isinstance(value, str) or not value:
                continue
            node: dict = {"type": "text", "text": value}
            if attrs:
                node["marks"] = [{"type": k, **({"attrs": _norm(v)} if v else {})} for k, v in attrs.items()]
            out.append(node)
        return out
    node = {"type": item.tag}
    attrs = _norm(dict(item.attributes))
    if attrs:
        node["attrs"] = attrs
    content = []
    for child in item.children:
        content.extend(_node_from(child))
    if content:
        node["content"] = content
    return [node]


def fragment_to_pm(fragment: XmlFragment) -> dict:
    """The fragment as a ProseMirror ``doc`` node."""
    content = []
    for child in fragment.children:
        content.extend(_node_from(child))
    return {"type": "doc", "content": content}


def read(ydoc: Doc) -> dict:
    """The three parts of a live document as the editor's saved shape (no page settings)."""
    with ydoc.transaction():
        return {part: fragment_to_pm(ydoc.get(part, type=XmlFragment)) for part in PARTS}


def strip_nulls(node: Any) -> Any:
    """Editor JSON with ``null`` attributes removed and empty ``attrs`` dropped — what a round
    trip through the shared types preserves (the schema fills the defaults back in)."""
    if isinstance(node, list):
        return [strip_nulls(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "attrs" and isinstance(v, dict):
            v = {a: b for a, b in v.items() if b is not None}
            if not v:
                continue
        elif k == "marks" and isinstance(v, list):
            v = [{"type": m["type"], **({"attrs": m["attrs"]} if m.get("attrs") else {})} for m in v]
        out[k] = strip_nulls(v)
    return out


def _text_of(item: XmlElement | XmlText) -> str:
    if isinstance(item, XmlText):
        return "".join(v for v, _ in item.diff() if isinstance(v, str))
    if item.tag == "field":
        return f"【{item.attributes.get('name') or ''}】"
    if item.tag == "hardBreak":
        return "\n"
    return "".join(_text_of(c) for c in item.children)


def block_texts(ydoc: Doc) -> dict[str, list[tuple[str, str]]]:
    """Per part, the top-level blocks as (node type, text): the unit the change log speaks in."""
    out = {}
    with ydoc.transaction():
        for part in PARTS:
            frag = ydoc.get(part, type=XmlFragment)
            blocks = []
            for child in frag.children:
                if isinstance(child, XmlElement):
                    blocks.append((child.tag or "", _text_of(child)))
                else:
                    blocks.append(("text", _text_of(child)))
            out[part] = blocks
    return out
