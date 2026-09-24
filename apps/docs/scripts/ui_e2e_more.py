"""More browser end-to-end flows (real Chrome via Playwright):

A. 每组一份: grouped order details → XLSX template; detail row created by selecting a
   template row in the grid; source detail table selected by dragging header→last row.
B. PDF template: draw a box with the mouse on the page → field; text PDF source region.
C. Built-in document editor: type text, insert a field, insert a table, mark a repeat
   row, save → template usable in the workspace.
D. Built-in spreadsheet editor: type cells, mark a field cell, save.

    .venv/bin/python apps/docs/scripts/ui_e2e_more.py              # isolated test instance (default)
    .venv/bin/python apps/docs/scripts/ui_e2e_more.py <base_url>   # only a test-marked instance
"""
from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from pathlib import Path

import openpyxl
import pymupdf
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
from _testenv import SHOTS, TESTDATA, target  # noqa: E402  isolated test instance; never the user's real data

ARG_BASE = sys.argv[1] if len(sys.argv) > 1 else None
BASE = ""                     # bound in main() to an isolated (or explicitly test-marked) server
DATA = TESTDATA                # <repo>/testdata (apps/docs/scripts/make_fixtures.py)


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def new_project(page: Page, name: str) -> str:
    page.goto(BASE + "/")
    page.get_by_role("button", name="＋ 新建项目").click()
    page.locator(".modal input").fill(name)
    page.locator(".modal").get_by_role("button", name="确定").click()
    page.wait_for_url(re.compile(r"/p/p_"))
    return page.url.rsplit("/", 1)[-1]


def cell_box(page: Page, pane: str, text: str):
    return page.locator(f"{pane} .gcell", has_text=re.compile(f"^{re.escape(text)}$")).first.bounding_box()


def drag(page: Page, a, b):
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, steps=6)
    page.mouse.up()


def flow_a(page: Page):
    pid = new_project(page, "UI验收：订单分组")
    page.locator(".side input[type=file]").set_input_files(str(DATA / "测试_订单明细100.xlsx"))
    expect(page.locator(".fileitem").get_by_text("可用")).to_be_visible(timeout=20000)
    page.get_by_role("button", name="设置模板").first.click()
    page.locator(".modal input[type=file]").set_input_files(str(DATA / "测试_订单模板.xlsx"))
    expect(page.locator(".pane.right .gcell", has_text="订单确认单（测试）")).to_be_visible(timeout=20000)
    # samples: per group by 订单号, sorted by 行号
    page.locator("button.step", has_text="定义样本").click()
    dlg = page.locator(".modal")
    dlg.locator("label.opt", has_text="每组一份").click()
    dlg.locator("label.badge", has_text="订单号").locator("input").check()
    dlg.locator("select").filter(has_text="保持原始顺序").select_option("行号")
    dlg.locator("label.check", has_text="按数值").locator("input").check()
    expect(dlg.locator(".statbox .v").first).to_have_text("15", timeout=10000)
    dlg.get_by_role("button", name="确认样本规则").click()
    # template field B2 (订单号)
    page.locator(".pane.right .gcell", has_text=re.compile("^DD-000$")).click()
    page.get_by_role("button", name="✎ 设为待填区域").click()
    page.locator(".modal").get_by_placeholder("例如：客户名称").fill("订单号")
    page.locator(".modal").get_by_role("button", name="创建字段").click()
    expect(page.locator(".modal")).to_have_count(0)
    # template detail row: drag A5:D5 (empty cells: use positions under the header cells)
    h1, h4 = cell_box(page, ".pane.right", "行号"), cell_box(page, ".pane.right", "单价")
    a = {**h1, "y": h1["y"] + h1["height"]}
    b = {**h4, "y": h4["y"] + h4["height"]}
    drag(page, a, b)
    page.get_by_role("button", name="✎ 设为待填区域").click()
    dlg = page.locator(".modal")
    dlg.locator("label.opt", has_text="重复明细行").click()
    dlg.get_by_placeholder("例如：客户名称").fill("明细")
    dlg.get_by_role("button", name="创建字段").click()
    expect(page.locator(".modal")).to_have_count(0)
    # mapping 1: 订单号 column → 订单号 cell field
    page.locator(".pane.right .gmark .tag").first.click()          # select the 订单号 field through its marker
    b1 = cell_box(page, ".pane.left", page.locator(".pane.left .gcell").nth(6).inner_text())  # first data cell (A2)
    page.mouse.click(b1["x"] + 5, b1["y"] + 5)
    page.get_by_role("button", name="⇄ 建立映射").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("随样本变化").first).to_be_visible()
    dlg.get_by_role("button", name="保存映射").click()
    expect(page.locator(".modal")).to_have_count(0)
    # mapping 2: source table (header row → a data row, marked "明细表") → repeat field
    page.locator(".pane.right .gmark .tag").nth(1).click()
    hb = cell_box(page, ".pane.left", "订单号")
    last = cell_box(page, ".pane.left", "单价")
    drag(page, hb, {**last, "y": last["y"] + 22 * 5})
    page.get_by_role("button", name="⇄ 建立映射").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("明细表（表头：第 1 行）：每份输出写入当前分组的全部明细行", exact=False)).to_be_visible()
    shot(page, "10_明细映射对话框")
    dlg.get_by_role("button", name="保存映射").click()
    expect(page.locator(".modal")).to_have_count(0)
    page.locator(".tabs button", has_text="映射").click()
    expect(page.locator(".mapitem")).to_have_count(2)
    # the detail table locator was created "to row 7" – widen to the end through the mapping JSON is not a UI step;
    # instead verify the preview shows the group's own rows
    page.get_by_role("button", name="👁 预览").click()
    expect(page.get_by_text("真实样本预览")).to_be_visible()
    expect(page.locator(".pane.right .gcell", has_text=re.compile(r"^DD-\d{3}$"))).to_be_visible(timeout=20000)
    page.locator(".pane.right input[placeholder='按样本标识查找']").fill("DD-001")
    page.locator(".pane.right input[placeholder='按样本标识查找']").press("Enter")
    expect(page.locator(".pane.right .gcell", has_text=re.compile("^DD-001$"))).to_be_visible(timeout=20000)
    shot(page, "11_分组预览")
    page.get_by_role("button", name="▶ 生成").click()
    dlg = page.locator(".modal")
    dlg.locator("input.mono").fill("{订单号}")
    dlg.get_by_role("button", name=re.compile("开始生成 15 个文件")).click()
    page.wait_for_url(re.compile(r"/jobs/j_"))
    expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
    with page.expect_download() as dl:
        page.get_by_text("⬇ 下载全部（ZIP）").click()
    z = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes()))
    ws = openpyxl.load_workbook(io.BytesIO(z.read("DD-001.xlsx")))["订单"]
    assert ws["B2"].value == "DD-001"
    rows = [r for r in openpyxl.load_workbook(DATA / "测试_订单明细100.xlsx")["明细"].iter_rows(min_row=2, values_only=True) if r[0] == "DD-001"]
    got = [ws.cell(5 + i, 2).value for i in range(len(rows))]
    assert sorted(got) == sorted(r[3] for r in rows), got
    return pid


def flow_b(page: Page):
    pid = new_project(page, "UI验收：PDF 模板框选")
    page.locator(".side input[type=file]").set_input_files(str(DATA / "测试_申请表.pdf"))
    expect(page.locator(".fileitem").get_by_text("可用")).to_be_visible(timeout=20000)
    page.get_by_role("button", name="设置模板").first.click()
    page.locator(".modal input[type=file]").set_input_files(str(DATA / "测试_通知书模板.pdf"))
    expect(page.locator(".pane.right .pdf-page").first).to_be_visible(timeout=20000)
    page.wait_for_timeout(800)
    # draw a box around "旧姓名样例" on page 1 using the rendered page geometry
    tpl = pymupdf.open(DATA / "测试_通知书模板.pdf")
    w = [x for x in tpl[0].get_text("words") if x[4] == "旧姓名样例"][0]
    pg = page.locator(".pane.right .pdf-page").first
    box = pg.bounding_box()
    sx, sy = box["width"] / tpl[0].rect.width, box["height"] / tpl[0].rect.height
    x0, y0, x1, y1 = box["x"] + (w[0] - 3) * sx, box["y"] + (w[1] - 3) * sy, box["x"] + (w[2] + 60) * sx, box["y"] + (w[3] + 3) * sy
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x1, y1, steps=8)
    page.mouse.up()
    page.get_by_role("button", name="✎ 设为待填区域").click()
    dlg = page.locator(".modal")
    dlg.get_by_placeholder("例如：客户名称").fill("收件人")
    dlg.get_by_role("button", name="创建字段").click()
    expect(page.locator(".modal")).to_have_count(0)
    expect(page.locator(".pane.right .pdf-box")).to_have_count(1)
    shot(page, "12_PDF模板框选")
    # template working copy no longer contains the old value in that box
    tid = page.evaluate("fetch(location.pathname.replace('/p/','/api/projects/')).then(r=>r.json()).then(p=>p.template_id)")
    work = page.request.get(f"{BASE}/api/templates/{tid}/download?which=working").body()
    wdoc = pymupdf.open(stream=work, filetype="pdf")
    assert "旧姓名样例" not in wdoc[0].get_text()
    return pid


def flow_c(page: Page):
    page.goto(BASE + "/templates/new/doc")
    ed = page.locator(".editor-page .ProseMirror").nth(1)
    ed.click()
    page.keyboard.type("客户名称：")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="＋ 插入字段").click()
    page.locator(".modal input").fill("客户名称")
    page.locator(".modal").get_by_role("button", name="确定").click()
    expect(page.locator(".field-node", has_text="客户名称")).to_be_visible()
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.get_by_role("button", name="▦ 表格").click()
    page.locator(".editor-page table tr").nth(1).locator("td").first.click()
    page.get_by_role("button", name="＋ 插入字段").click()
    page.locator(".modal input").fill("品名")
    page.locator(".modal").get_by_role("button", name="确定").click()
    page.get_by_role("button", name="设为重复明细行").click()
    expect(page.locator(".editor-page tr[data-repeat='true']")).to_have_count(1)
    shot(page, "13_内置文档编辑器")
    page.get_by_role("button", name="保存模板").click()
    page.wait_for_url(re.compile(r"/templates/t_.*/edit"), timeout=20000)
    tid = page.url.split("/templates/")[1].split("/")[0]
    t = page.request.get(f"{BASE}/api/templates/{tid}").json()
    kinds = sorted(s["kind"] for s in t["version"]["slots"])
    assert kinds == ["repeat", "text"], kinds
    return tid


def flow_d(page: Page):
    page.goto(BASE + "/templates/new/sheet")
    grid = page.locator(".grid-scroll")
    grid.click(position={"x": 60, "y": 30})
    expect(grid).to_be_focused()
    expect(page.locator(".cellinfo")).to_have_text("A1")
    # Playwright inserts non-ASCII text without the IME keydown that opens an editor.
    # Use the supported F2 entry point before typing Chinese; keep actual value assertions.
    page.keyboard.press("F2")
    expect(page.get_by_role("textbox", name="编辑单元格 A1", exact=True)).to_be_focused()
    page.keyboard.type("报表标题")
    page.keyboard.press("Enter")
    expect(page.locator(".cellinfo")).to_have_text("A2")
    expect(grid.locator(".gcell", has_text=re.compile("^报表标题$"))).to_have_count(1)
    page.keyboard.press("F2")
    expect(page.get_by_role("textbox", name="编辑单元格 A2", exact=True)).to_be_focused()
    page.keyboard.type("金额")
    expect(page.get_by_role("textbox", name="编辑单元格 A2", exact=True)).to_have_value("金额")
    # One native Tab commits and leaves the grid; it must not move the selection to B2.
    page.keyboard.press("Tab")
    expect(grid.locator("input")).to_have_count(0)
    expect(page.get_by_role("button", name="Sheet1", exact=True)).to_be_focused()
    assert grid.evaluate("el => !el.contains(document.activeElement)")
    expect(page.locator(".cellinfo")).to_have_text("A2")
    expect(grid.locator(".gcell", has_text=re.compile("^金额$"))).to_have_count(1)
    # Re-enter without changing the selection, then explicitly move to the field cell.
    grid.focus()
    expect(page.locator(".cellinfo")).to_have_text("A2")
    page.keyboard.press("ArrowRight")
    expect(page.locator(".cellinfo")).to_have_text("B2")
    page.keyboard.type("123.45")
    expect(page.get_by_role("textbox", name="编辑单元格 B2", exact=True)).to_have_value("123.45")
    page.keyboard.press("Tab")
    expect(page.get_by_role("button", name="Sheet1", exact=True)).to_be_focused()
    expect(page.locator(".cellinfo")).to_have_text("B2")
    page.get_by_role("button", name="设为字段单元格").click()
    page.locator(".modal input").fill("金额")
    page.locator(".modal").get_by_role("button", name="确定").click()
    expect(page.locator(".slotitem", has_text="金额")).to_be_visible()
    shot(page, "14_内置表格编辑器")
    page.get_by_role("button", name="保存模板").click()
    page.wait_for_url(re.compile(r"/templates/t_.*/edit"), timeout=20000)
    tid = page.url.split("/templates/")[1].split("/")[0]
    response = page.request.get(f"{BASE}/api/templates/{tid}")
    assert response.ok, response.text()
    t = response.json()
    sheet = t["version"]["editor"]["sheets"][0]
    assert sheet["name"] == "Sheet1", sheet
    assert {ref: sheet["cells"][ref]["v"] for ref in ("A1", "A2", "B2")} == {
        "A1": "报表标题", "A2": "金额", "B2": "123.45",
    }, sheet["cells"]
    assert sheet["cells"]["B2"]["t"] == "number", sheet["cells"]["B2"]
    assert [(f["name"], f["kind"], f["ref"]) for f in sheet["fields"]] == [("金额", "cell", "B2")], sheet["fields"]
    slots = t["version"]["slots"]
    assert [(s["name"], s["kind"], s["locator"]["sheet"], s["locator"]["cell"]) for s in slots] == [
        ("金额", "cell", "Sheet1", "B2"),
    ], slots
    # Read the generated file too: creating a field must preserve the neighbouring labels.
    response = page.request.get(f"{BASE}/api/templates/{tid}/download?which=working")
    assert response.ok, response.status
    wb = openpyxl.load_workbook(io.BytesIO(response.body()))
    assert wb["Sheet1"]["A1"].value == "报表标题"
    assert wb["Sheet1"]["A2"].value == "金额"
    wb.close()
    page.reload()
    expect(page.get_by_role("button", name="保存模板", exact=True)).to_be_enabled()
    expect(grid.locator(".gcell", has_text=re.compile("^报表标题$"))).to_have_count(1)
    expect(grid.locator(".gcell", has_text=re.compile("^金额$"))).to_have_count(1)
    field = page.locator(".slotitem", has_text="金额")
    expect(field.locator(".badge")).to_have_text("B2")
    field.click()
    expect(page.locator(".cellinfo")).to_have_text("B2")
    grid.focus()
    page.keyboard.press("F2")
    expect(page.get_by_role("textbox", name="编辑单元格 B2", exact=True)).to_have_value("123.45")
    page.keyboard.press("Escape")
    shot(page, "14b_内置表格保存回读")
    return tid


def main():
    global BASE
    with target(ARG_BASE) as _base:
        BASE = _base
        t0 = time.time()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True, locale="zh-CN")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for name, fn in (("A 分组+XLSX明细", flow_a), ("B PDF框选", flow_b), ("C 内置文档编辑器", flow_c), ("D 内置表格编辑器", flow_d)):
                t = time.time()
                fn(page)
                print(f"  {name}: ok ({time.time() - t:.1f}s)")
            assert not errors, errors
            browser.close()
        print(f"UI E2E (more) passed in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
