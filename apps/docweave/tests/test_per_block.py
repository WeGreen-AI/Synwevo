"""「每块一份」: one file holding several irregular forms, split at a start marker."""
import io

import docx
import pytest

from .helpers import _walk

PEOPLE = [
    {"姓名": "测试赵一", "电话": "13900000001", "地址": "测试市一路 1 号"},
    {"姓名": "测试钱二", "电话": "13900000002", "地址": "测试市二路 2 号"},
    {"姓名": "测试孙三", "地址": "测试市三路 3 号"},                  # no phone → this block must fail, not guess
    {"姓名": "测试李四", "电话": "13900000004", "地址": "测试市四路 4 号"},
]
RULE = {"mode": "per_block", "block": {"anchor": "人员登记表", "match": "contains"}}


def _template(api, pid, fixtures):
    t = api.template(pid, fixtures / "测试_登记卡模板.docx")
    return t["id"], {n: api.text_slot(t["id"], s, n) for n, s in (("姓名", "某某"), ("电话", "00000000"), ("地址", "某地"))}


def _loc(api, pid, sid, sel, block=0, want="固定"):
    res = api.ok(api.c.post(f"/api/sources/{sid}/locator", json={"project_id": pid, "selection": sel, "block": block}))
    return next(p["locator"] for p in res["proposals"] if want in p["label"]) if want else res["proposals"][0]["locator"]


def _locators(api, pid, sid, fmt):
    if fmt == "xlsx":
        cell = lambda r: {"kind": "cells", "sheet": "登记", "r1": r, "c1": 1, "r2": r, "c2": 1}  # noqa: E731
        return {"姓名": _loc(api, pid, sid, cell(1), want="每个表单块"), "电话": _loc(api, pid, sid, cell(2), want="每个表单块"), "地址": _loc(api, pid, sid, cell(3), want="每个表单块")}
    if fmt == "docx":
        v = api.ok(api.c.get(f"/api/sources/{sid}/view", params={"block": 0}))
        paras = {b["text"]: b for part in v["doc"]["parts"] if part["kind"] == "body" for b in _walk(part["blocks"])}
        out = {}
        for k in ("姓名", "电话", "地址"):
            b = next(p for t, p in paras.items() if t.startswith(k + "："))
            out[k] = _loc(api, pid, sid, {"kind": "docx_text", "part": b["part"], "path": b["path"], "start": len(k) + 1, "end": len(b["text"])}, want=None)
        return out
    if fmt == "pdf":
        words = api.ok(api.c.get(f"/api/sources/{sid}/words/0", params={"block": 0}))["words"]
        out = {}
        for k in ("姓名", "电话", "地址"):
            label = next(w for w in words if w["t"].startswith(k))
            vals = [w for w in words if abs(w["r"][1] - label["r"][1]) < 0.005 and w["r"][0] > label["r"][2]]
            r = [min(w["r"][0] for w in vals) - 0.003, min(w["r"][1] for w in vals) - 0.003, max(w["r"][2] for w in vals) + 0.003, max(w["r"][3] for w in vals) + 0.003]
            out[k] = _loc(api, pid, sid, {"kind": "pdf_region", "page": 0, "nrect": r}, want=None)
        return out
    v = api.ok(api.c.get(f"/api/sources/{sid}/view", params={"block": 0}))
    out = {}
    for k in ("姓名", "电话", "地址"):
        li = next(i for i, t in enumerate(v["lines"]) if t.startswith(k))
        out[k] = _loc(api, pid, sid, {"kind": "text_span", "line": li, "start": len(k) + 1, "end": len(v["lines"][li])}, want=None)
    return out


@pytest.mark.parametrize("fmt", ["xlsx", "docx", "pdf", "txt"])
def test_split_map_and_generate(api, fixtures, fmt):
    pid = api.project(f"每块一份-{fmt}")
    api.upload(pid, fixtures / f"测试_多表单登记表.{fmt}")
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    tid, slots = _template(api, pid, fixtures)
    api.rule(pid, RULE)
    pv = api.ok(api.c.post(f"/api/projects/{pid}/rule-preview", json=RULE))
    assert pv["total"] == 4 and not pv["file_errors"], pv
    blocks = api.ok(api.c.get(f"/api/sources/{sid}/blocks"))["blocks"]
    assert len(blocks) == 4 and all("人员登记表" in b["label"] for b in blocks)
    locs = _locators(api, pid, sid, fmt)
    api.maps(pid, [{"type": "single", "sources": [locs[k]], "target": {"binding": slots[k]["binding"]}} for k in ("姓名", "电话", "地址")])
    r = api.c.post(f"/api/projects/{pid}/jobs", json={})
    assert r.status_code == 409 and "1 个样本存在错误" in r.text             # block 3 has no phone: stops by default
    assert "第 3 块" in r.text and "电话" in r.text
    job = api.job(pid, naming="{块号}_{姓名}", error_policy="skip")
    assert job["counts"]["success"] == 3
    outs = api.outputs(job["id"])
    for i, p in enumerate(PEOPLE):
        name = f"{i + 1}_{p['姓名']}.docx"
        if "电话" not in p:
            assert name not in outs
            continue
        text = [x.text for x in docx.Document(io.BytesIO(outs[name])).paragraphs]
        assert f"姓名：{p['姓名']}" in text and f"电话：{p['电话']}" in text and f"地址：{p['地址']}" in text


def test_block_views_and_errors(api, fixtures):
    pid = api.project("每块一份-视图")
    api.upload(pid, fixtures / "测试_多表单登记表.pdf", fixtures / "测试_客户100.xlsx")
    srcs = {s["filename"]: s["id"] for s in api.ok(api.c.get(f"/api/projects/{pid}/sources"))}
    api.rule(pid, RULE)
    pv = api.ok(api.c.post(f"/api/projects/{pid}/rule-preview", json=RULE))
    assert pv["total"] == 4 and pv["file_errors"][0]["file"] == "测试_客户100.xlsx" and "找不到标记" in pv["file_errors"][0]["message"]
    pdf = srcs["测试_多表单登记表.pdf"]
    v = api.ok(api.c.get(f"/api/sources/{pdf}/view", params={"block": 1}))
    assert v["view"] == "pdf" and v["block"] == 1 and v["pdf"]["pages"][0]["w"] == 595
    words = api.ok(api.c.get(f"/api/sources/{pdf}/words/0", params={"block": 1}))["words"]
    text = "".join(w["t"] for w in words)
    assert "测试钱二" in text and "测试赵一" not in text and "测试孙三" not in text   # only this block's content
    assert api.c.get(f"/api/sources/{pdf}/page/0.png", params={"block": 1}).status_code == 200
    assert api.c.get(f"/api/sources/{pdf}/view", params={"block": 9}).status_code == 404
    hits = api.ok(api.c.get(f"/api/sources/{pdf}/search", params={"q": "测试", "block": 3}))
    assert hits["total"] >= 1 and all(h["page"] == 0 for h in hits["hits"])
    # marker regex and validation
    bad = api.c.patch(f"/api/projects/{pid}", json={"record_rule": {"mode": "per_block", "block": {"anchor": "([", "match": "regex"}}})
    assert bad.status_code == 400
    rx = {"mode": "per_block", "block": {"anchor": r"^人员登记表", "match": "regex"}}
    assert api.ok(api.c.post(f"/api/projects/{pid}/rule-preview", json=rx))["total"] == 4
    # an end marker limits each block
    endm = {"mode": "per_block", "block": {"anchor": "人员登记表", "end_anchor": "姓名"}}
    api.rule(pid, endm)
    words = api.ok(api.c.get(f"/api/sources/{pdf}/words/0", params={"block": 0}))["words"]
    assert "测试赵一" in "".join(w["t"] for w in words) and "测试市一路" not in "".join(w["t"] for w in words)


def test_pdf_blocks_have_no_blank_pages(api, fixtures):
    pid = api.project("每块一份-空白页")
    api.upload(pid, fixtures / "测试_多表单登记表.pdf")
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]
    api.rule(pid, RULE)
    for k in range(4):
        v = api.ok(api.c.get(f"/api/sources/{sid}/view", params={"block": k}))
        assert len(v["pdf"]["pages"]) == 1, (k, len(v["pdf"]["pages"]))    # every form fits on its own page part
