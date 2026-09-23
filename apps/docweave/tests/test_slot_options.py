"""Slot enum options must be refused when unknown, never silently replaced.

DW-050: `empty_policy` decided whether a sample with zero detail rows is blocked
(`error`) or quietly produced anyway (`remove_row` / `blank` / `remove`).  Creation
accepted any string and PATCH validated only `separator` / `overflow`, so a caller
asking for `error` and mistyping it got a document that should have been stopped.
"""
from __future__ import annotations

import pytest


def _table(api, tid):
    v = api.ok(api.c.get(f"/api/templates/{tid}/view"))
    for part in v["doc"]["parts"]:
        for b in part["blocks"]:
            if b["t"] == "tbl":
                return part["part"], b
    raise AssertionError("模板中没有表格")


def _make(api, tid, **extra):
    part, tbl = _table(api, tid)
    return api.c.post(f"/api/templates/{tid}/slots", json={
        "kind": "repeat", "part": part, "table_path": tbl["path"], "row": len(tbl["rows"]) - 1,
        "name": extra.pop("name", "明细"), **extra})


def _repeat_slot(api, fixtures):
    """A DOCX template with a repeat-row slot (the common detail table case)."""
    pid = api.project("字段枚举校验")
    t = api.template(pid, fixtures / "测试_订单模板.docx")
    r = _make(api, t["id"])
    assert r.status_code < 400, r.text[:300]
    return pid, t["id"], r.json()["created_slot"]


@pytest.mark.parametrize("bad", ["erro", "ERROR", "blank", "remove", "", "nope"])
def test_creation_refuses_unknown_empty_policy(api, fixtures, bad):
    pid = api.project("创建校验")
    t = api.template(pid, fixtures / "测试_订单模板.docx")
    r = _make(api, t["id"], empty_policy=bad)
    assert r.status_code == 400, (bad, r.status_code, r.text[:200])
    assert "empty_policy" in r.text


def test_creation_accepts_the_documented_values(api, fixtures):
    for good in ("remove_row", "blank_row", "placeholder", "remove_table", "error"):
        pid = api.project(f"创建合法值-{good}")
        t = api.template(pid, fixtures / "测试_订单模板.docx")
        r = _make(api, t["id"], name=f"明细-{good}", empty_policy=good)
        assert r.status_code < 400, (good, r.text[:200])
        assert r.json()["created_slot"]["empty_policy"] == good


@pytest.mark.parametrize("bad", ["erro", "blank", "ERROR", "nope"])
def test_patch_refuses_unknown_empty_policy(api, fixtures, bad):
    pid, tid, slot = _repeat_slot(api, fixtures)
    r = api.c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"empty_policy": bad})
    assert r.status_code == 400, (bad, r.status_code, r.text[:200])
    body = r.json()
    assert body["detail"]["field"] == "empty_policy" and body["detail"]["allowed"], body


def test_patch_accepts_valid_and_keeps_working(api, fixtures):
    pid, tid, slot = _repeat_slot(api, fixtures)
    for good in ("error", "blank_row", "remove_row"):
        r = api.c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"empty_policy": good})
        assert r.status_code < 400, (good, r.text[:200])
        cur = api.ok(api.c.get(f"/api/templates/{tid}"))["version"]["slots"]
        assert next(s for s in cur if s["id"] == slot["id"])["empty_policy"] == good


@pytest.mark.parametrize("field,bad", [("data_type", "integer"), ("align", "middle"), ("valign", "center")])
def test_patch_refuses_other_unknown_enums(api, fixtures, field, bad):
    pid, tid, slot = _repeat_slot(api, fixtures)
    r = api.c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={field: bad})
    assert r.status_code == 400, (field, bad, r.status_code, r.text[:200])
