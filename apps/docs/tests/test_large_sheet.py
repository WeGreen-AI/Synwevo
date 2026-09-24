"""A dense workbook must page without re-parsing: each grid window is a slice, not a rebuild.

Guards the fix for DW-040 — ``workbook_window`` rebuilt the whole workbook object model on
every request (~0.4 s per scroll on a real 20k x 30 sheet, measured against the running
server).  All data here is synthetic.
"""
from __future__ import annotations

import io

import openpyxl

from synwevo_docs.engine import tabular


def _big(rows: int = 4000, cols: int = 20) -> bytes:
    wb = openpyxl.Workbook(write_only=True)
    ws = wb.create_sheet("明细")
    ws.append([f"列{c}" for c in range(cols)])
    for r in range(rows):
        ws.append([f"{r:06d}-{c}" for c in range(cols)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_windows_are_slices_not_rebuilds(api, monkeypatch):
    pid = api.project("大表分页")
    api.upload_bytes(pid, "大表.xlsx", _big())
    sid = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["id"]

    first = api.ok(api.c.get(f"/api/sources/{sid}/view", params={"sheet": "明细", "offset": 0, "limit": 200}))
    grid = first["grid"]
    assert grid["total_rows"] == 4001 and grid["cols_shown"] == 20
    assert len(grid["rows"]) == 200
    assert grid["rows"][1][0]["v"] == "000000-0"        # row 0 is the header

    builds = []
    real = tabular.Workbook.from_json
    monkeypatch.setattr(tabular.Workbook, "from_json", staticmethod(lambda d: (builds.append(1), real(d))[1]))

    for off in (400, 800, 1200, 1600, 2000, 2400):
        w = api.ok(api.c.get(f"/api/sources/{sid}/view", params={"sheet": "明细", "offset": off, "limit": 200}))["grid"]
        assert w["offset"] == off
        assert w["rows"][0][0]["v"] == f"{off - 1:06d}-0"   # the window really starts where it was asked to
        assert w["rows"][-1][0]["v"] == f"{off + 198:06d}-0"
    assert not builds, f"分页时重建了 {len(builds)} 次工作簿对象；应复用已解析的缓存"
