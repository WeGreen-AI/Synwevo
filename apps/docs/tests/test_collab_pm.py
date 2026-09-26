"""The editor's document JSON survives the trip into the shared document and back, for every node
and mark the editor contract allows — including non-ASCII text, whose positions are counted in the
library's own unit, not Python's."""
from __future__ import annotations

import copy

from pycrdt import Doc

from synwevo_docs.collab_pm import EMPTY_DOC, block_texts, read, seed, strip_nulls

FULL = {"body": {"type": "doc", "content": [
    {"type": "heading", "attrs": {"level": 2, "textAlign": "center"}, "content": [
        {"type": "text", "text": "合同 "},
        {"type": "text", "text": "标题", "marks": [{"type": "bold"}, {"type": "textStyle", "attrs": {"color": "#ff0000", "fontSize": None, "fontFamily": None}}]},
        {"type": "text", "text": " 尾巴 emoji 🙂 end", "marks": [{"type": "italic"}, {"type": "highlight", "attrs": {"color": None}}]}]},
    {"type": "paragraph", "attrs": {"textAlign": None}, "content": [
        {"type": "text", "text": "客户："}, {"type": "field", "attrs": {"id": "f1", "name": "客户", "required": True}},
        {"type": "text", "text": " 结尾"}, {"type": "hardBreak"}, {"type": "text", "text": "第二行", "marks": [{"type": "underline"}, {"type": "strike"}]}]},
    {"type": "paragraph"},
    {"type": "table", "content": [{"type": "tableRow", "attrs": {"repeat": True, "repeatId": "r1", "repeatName": "明细", "emptyPolicy": "remove_row"}, "content": [
        {"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None, "background": None}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "列"}]}]},
        {"type": "tableCell", "attrs": {"colspan": 2, "rowspan": 1, "colwidth": [120, 80], "background": "#eeeeee"}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}]}]}]},
    {"type": "repeatBlock", "attrs": {"repeatId": "b1", "repeatName": "明细", "separator": "none", "emptyPolicy": "remove"}, "content": [
        {"type": "paragraph", "content": [{"type": "field", "attrs": {"id": "f2", "name": "金额", "required": False}}]}]},
    {"type": "image", "attrs": {"src": "data:image/png;base64,iVBORw0KGgo=", "alt": None, "title": None, "width": 600}},
    {"type": "pageBreak"}, {"type": "horizontalRule"},
    {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "项"}]}]}]},
    {"type": "orderedList", "attrs": {"start": 3}, "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "二"}]}]}]},
    {"type": "blockquote", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "引"}]}]},
]}, "header": {"type": "doc", "content": [{"type": "paragraph", "attrs": {"textAlign": "right"}, "content": [{"type": "text", "text": "页眉"}]}]},
    "footer": {"type": "doc", "content": [{"type": "paragraph"}]}}


def canon(node):
    """Marks in name order: the schema sorts them by rank on the way back, the CRDT does not."""
    if isinstance(node, list):
        return [canon(n) for n in node]
    if isinstance(node, dict):
        out = {k: canon(v) for k, v in node.items()}
        if isinstance(out.get("marks"), list):
            out["marks"] = sorted(out["marks"], key=lambda m: m["type"])
        return out
    return node


def test_every_node_and_mark_survives_the_round_trip():
    d = Doc()
    seed(d, copy.deepcopy(FULL))
    back = read(d)
    for part in ("body", "header", "footer"):
        assert canon(strip_nulls(back[part])) == canon(strip_nulls(FULL[part])), part
    # a second document that only ever saw the update reads the same
    e = Doc()
    e.apply_update(d.get_update())
    assert canon(read(e)) == canon(back)


def test_integers_and_json_values_keep_their_types():
    d = Doc()
    seed(d, {"body": {"type": "doc", "content": [
        {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "t"}]},
        {"type": "table", "content": [{"type": "tableRow", "content": [{"type": "tableCell", "attrs": {"colspan": 1, "rowspan": 2, "colwidth": [100]}, "content": [{"type": "paragraph"}]}]}]}]}})
    body = read(d)["body"]
    level = body["content"][0]["attrs"]["level"]
    assert level == 3 and type(level) is int
    cell = body["content"][1]["content"][0]["content"][0]["attrs"]
    assert cell == {"colspan": 1, "rowspan": 2, "colwidth": [100]} and type(cell["rowspan"]) is int


def test_missing_parts_become_an_empty_paragraph_and_reseeding_replaces():
    d = Doc()
    seed(d, {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "旧"}]}]}})
    assert read(d)["header"] == EMPTY_DOC and read(d)["footer"] == EMPTY_DOC
    seed(d, {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "新"}]}, {"type": "paragraph"}]}})
    assert block_texts(d)["body"] == [("paragraph", "新"), ("paragraph", "")]
    assert read(d)["body"]["content"][0]["content"][0]["text"] == "新"


def test_block_texts_read_fields_and_breaks_as_a_person_would():
    d = Doc()
    seed(d, copy.deepcopy(FULL))
    blocks = block_texts(d)
    assert blocks["body"][0] == ("heading", "合同 标题 尾巴 emoji 🙂 end")
    assert blocks["body"][1] == ("paragraph", "客户：【客户】 结尾\n第二行")
    assert blocks["body"][3][0] == "table" and "列" in blocks["body"][3][1]
    assert blocks["header"] == [("paragraph", "页眉")]
