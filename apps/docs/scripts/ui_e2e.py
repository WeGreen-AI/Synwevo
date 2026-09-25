"""Browser end-to-end test of the main user flow (acceptance scenario 1 via the UI).

Drives the real web UI in local Google Chrome with Playwright:
create project → upload XLSX (file input) → upload DOCX template → select text in
the template and "设为待填区域" (4 fields) → define samples (每行一份) → click grid
cells + field chips and "建立映射" (4 mappings, one with a date transform) →
preview (prev/next) → generate 100 DOCX → job page → download ZIP → verify files.

    .venv/bin/python apps/docs/scripts/ui_e2e.py              # isolated test instance (default)
    .venv/bin/python apps/docs/scripts/ui_e2e.py <base_url>   # only a test-marked instance
Screenshots are written to <repo>/docs/screenshots/.
"""
from __future__ import annotations

import io
import re
import sys
import time
import zipfile
from pathlib import Path

import docx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
from _testenv import SHOTS, TESTDATA, target  # noqa: E402  isolated test instance; never the user's real data

ARG_BASE = sys.argv[1] if len(sys.argv) > 1 else None
BASE = ""                     # bound in main() to an isolated (or explicitly test-marked) server
DATA = TESTDATA                # <repo>/testdata (apps/docs/scripts/make_fixtures.py)

SELECT_JS = """
([pane, needle, ctx]) => {
  const ps = [...document.querySelectorAll(pane + ' p.doc-p')];
  const p = ps.find(x => (x.dataset.text || '').includes(ctx || needle));
  if (!p) throw new Error('paragraph not found: ' + needle);
  const start = p.dataset.text.indexOf(needle), end = start + needle.length;
  const atoms = [...p.querySelectorAll('[data-off]')].filter(a => a.firstChild && a.firstChild.nodeType === 3);
  const find = (off, isEnd) => { for (const a of atoms) { const s = +a.dataset.off, l = a.firstChild.textContent.length;
    if (off >= s && (isEnd ? off <= s + l : off < s + l)) return [a.firstChild, off - s]; } };
  const [n1, o1] = find(start, false), [n2, o2] = find(end, true);
  const r = document.createRange(); r.setStart(n1, o1); r.setEnd(n2, o2);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  p.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
}
"""


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def click_cell(page: Page, pane: str, text: str):
    cell = page.locator(f"{pane} .gcell", has_text=re.compile(f"^{re.escape(text)}$")).first
    box = cell.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.up()


def make_field(page: Page, needle: str, name: str, ctx: str | None = None):
    page.evaluate(SELECT_JS, [".pane.right", needle, ctx])
    page.get_by_role("button", name="✎ 设为待填区域").click()
    dlg = page.locator(".modal")
    dlg.get_by_placeholder("例如：客户名称").fill(name)
    dlg.get_by_role("button", name="创建字段").click()
    expect(page.locator(".pane.right .fldchip", has_text=name)).to_be_visible(timeout=10000)


def map_field(page: Page, cell_text: str, field: str, date_fmt: str | None = None):
    click_cell(page, ".pane.left", cell_text)
    page.locator(".pane.right .fldchip", has_text=field).click()
    page.get_by_role("button", name="⇄ 建立映射").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("随样本变化：每份输出读取当前样本对应的值", exact=False)).to_be_visible()
    if date_fmt:
        dlg.locator("select").filter(has_text="添加转换规则").select_option("date_format")
        dlg.locator("select").filter(has_text="yyyy-mm-dd").first.select_option(date_fmt)
    dlg.get_by_role("button", name="保存映射").click()
    expect(page.locator(".modal")).to_have_count(0, timeout=10000)


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
            page.goto(BASE + "/")
            # 1. create project
            page.get_by_role("button", name="＋ 新建项目").click()
            page.locator(".modal input").fill("UI验收：客户合同")
            page.locator(".modal").get_by_role("button", name="确定").click()
            page.wait_for_url(re.compile(r"/p/p_"))
            pid = page.url.rsplit("/", 1)[-1]
            # 2. upload source through the file input
            page.locator(".side input[type=file]").set_input_files(str(DATA / "测试_客户100.xlsx"))
            expect(page.locator(".fileitem", has_text="测试_客户100.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
            # 3. template upload via the chooser
            page.get_by_role("button", name="设置模板").first.click()
            page.locator(".modal input[type=file]").set_input_files(str(DATA / "测试_合同模板.docx"))
            expect(page.locator(".pane.right p.doc-p").first).to_be_visible(timeout=20000)
            shot(page, "01_上传来源与模板")
            # 4. fields by selecting template text (incl. a selection across 3 differently formatted runs)
            make_field(page, "示例客户名称", "客户名称")
            make_field(page, "000000。", "客户编号", "客户编号 000000")  # includes the full stop; fixed below
            # delete and recreate without the full stop (tests delete + restore)
            page.locator(".pane.right .fldchip", has_text="客户编号").click()
            page.get_by_role("button", name=re.compile("字段 2")).click()
            page.once("dialog", lambda d: d.accept())
            page.get_by_role("button", name="删除字段").click()
            page.locator(".modal").get_by_role("button", name="删除").click()
            expect(page.locator(".pane.right .fldchip", has_text="客户编号")).to_have_count(0, timeout=10000)
            make_field(page, "000000", "客户编号", "客户编号 000000")
            make_field(page, "HT-000000", "合同编号", "合同编号：HT")  # a field inside the page header
            make_field(page, "2020-01-01", "签约日期")
            make_field(page, "0.00", "合同金额")
            # 5. samples
            page.locator("button.step", has_text="定义样本").click()
            dlg = page.locator(".modal")
            expect(dlg.get_by_text("识别样本")).to_be_visible()
            dlg.locator("select").filter(has_text="（不设置）").select_option("客户编号")
            expect(dlg.locator(".statbox .v").first).to_have_text("100", timeout=10000)
            shot(page, "02_定义样本")
            dlg.get_by_role("button", name="确认样本规则").click()
            expect(page.locator(".modal")).to_have_count(0)
            # 6. mappings (click real cells in the grid and field chips)
            map_field(page, "测试钱磊敏", "客户名称")
            map_field(page, "000001", "客户编号")
            map_field(page, "2025-01-04", "签约日期", date_fmt="yyyy年m月d日")
            map_field(page, "1,123.45", "合同金额")
            # header field gets two sources combined: fixed prefix is not allowed, so map 客户编号 → 合同编号 too (one source, many targets)
            map_field(page, "000001", "合同编号")
            page.locator(".tabs button", has_text="映射").click()
            expect(page.locator(".mapitem")).to_have_count(5)
            page.wait_for_timeout(800)
            shot(page, "03_映射与连线")
            # find in the source: row 101 is outside the first loaded window
            page.get_by_label("在当前文件中查找").fill("000100")
            page.get_by_label("在当前文件中查找").press("Enter")
            expect(page.locator(".pane.left [data-mark='src:__find']")).to_be_visible(timeout=10000)
            expect(page.locator(".pane.left .gcell", has_text=re.compile("^000100$"))).to_be_visible()
            page.get_by_label("在当前文件中查找").press("Escape")
            # 7. preview
            page.get_by_role("button", name="👁 预览").click()
            expect(page.get_by_text("真实样本预览")).to_be_visible()
            expect(page.locator(".pane.right p.doc-p", has_text="测试钱磊敏")).to_be_visible(timeout=20000)
            body = page.locator(".pane.right .doc-page").inner_text()
            assert "000001" in body and "2025年1月4日" in body and "1,123.45" in body, body[:400]
            page.get_by_role("button", name="下一条 ›").click()
            expect(page.locator(".pane.right p.doc-p", has_text="客户编号 000002")).to_be_visible(timeout=20000)
            shot(page, "04_预览")
            page.locator(".pane.right input[placeholder='按样本标识查找']").fill("000100")
            page.locator(".pane.right input[placeholder='按样本标识查找']").press("Enter")
            expect(page.locator(".pane.right p.doc-p", has_text="客户编号 000100")).to_be_visible(timeout=20000)
            page.get_by_role("button", name="返回模板").click()
            # 8. generate
            page.get_by_role("button", name="▶ 生成").click()
            dlg = page.locator(".modal")
            expect(dlg.get_by_text("有效样本")).to_be_visible(timeout=20000)
            name_input = dlg.locator("input.mono")
            name_input.fill("{客户编号}_{客户名称}")
            expect(dlg.get_by_text("000001_测试钱磊敏.docx")).to_be_visible(timeout=10000)
            shot(page, "05_生成确认")
            dlg.get_by_role("button", name=re.compile("开始生成 100 个文件")).click()
            page.wait_for_url(re.compile(r"/jobs/j_"))
            expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
            shot(page, "06_任务完成")
            stats = page.locator(".statbox .v").all_inner_texts()
            assert stats[:3] == ["100", "100", "0"], stats
            with page.expect_download() as dl:
                page.get_by_text("⬇ 下载全部（ZIP）").click()
            z = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes()))
            names = [n for n in z.namelist() if n.endswith(".docx")]
            assert len(names) == 100, len(names)
            d1 = docx.Document(io.BytesIO(z.read("000001_测试钱磊敏.docx")))
            text = "\n".join(p.text for p in d1.paragraphs)
            assert "甲方（客户）：测试钱磊敏，客户编号 000001。" in text, text
            assert "签约日期：2025年1月4日；合同金额：人民币 1,123.45 元。" in text, text
            assert "乙方：测试服务有限公司（固定文字，不应被修改）。" in text
            assert d1.sections[0].header.paragraphs[0].text == "测试公司 · 合同编号：000001", d1.sections[0].header.paragraphs[0].text
            assert d1.sections[0].footer.paragraphs[0].text == "本文件为测试模板，仅用于软件验收"
            assert len(d1.inline_shapes) == 1, "image lost"
            # refresh keeps job state
            page.reload()
            expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=20000)
            # 9. small screen: job & downloads still usable
            mob = ctx.new_page()
            mob.set_viewport_size({"width": 390, "height": 844})
            mob.goto(page.url)
            expect(mob.get_by_text("⬇ 下载全部（ZIP）")).to_be_visible(timeout=20000)
            shot(mob, "07_手机查看任务")
            assert not errors, errors
            browser.close()
        print(f"UI E2E passed in {time.time() - t0:.1f}s (project {pid})")


if __name__ == "__main__":
    main()
