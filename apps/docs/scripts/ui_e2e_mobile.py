"""The main Synwevo Docs flow on a phone-sized viewport (390×844, touch), in a real browser.

Same business path as ui_e2e.py — create project → upload XLSX → upload DOCX template → mark
fields → define samples → map cells to fields → preview → generate → download and read back — but
driven through the phone layout: one panel at a time (文件 / 来源 / 目标 / 映射 switch), the
selection bar carrying both selections across the switch, dialogs at full width. The page is a
Chromium (installed Google Chrome) mobile emulation: a real engine, real layout, real touch
target sizes — not an iPhone or an Android phone. The real-device checklist is in
docs/quality/verification.md.

    .venv/bin/python apps/docs/scripts/ui_e2e_mobile.py              # isolated test instance
    .venv/bin/python apps/docs/scripts/ui_e2e_mobile.py <base_url>   # a test-marked instance only
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import docx
from playwright.sync_api import Page, expect, sync_playwright

# Run as a script: Python puts this directory first on the import path, so the sibling helpers
# import directly (the same way ui_e2e.py finds _testenv).
from _testenv import SHOTS, TESTDATA, target  # noqa: E402  isolated test instance; never the user's real data
from ui_e2e import SELECT_JS, click_cell  # noqa: E402  the same selection helpers as the desktop flow

ARG_BASE = sys.argv[1] if len(sys.argv) > 1 else None
DATA = TESTDATA
WIDTH, HEIGHT = 390, 844


def pane(page: Page, name: str):
    """Switch the phone layout to one panel and wait until it is the one on screen."""
    page.locator(".pane-switch").get_by_role("tab", name=re.compile(f"^{name}")).click()
    visible = {"文件": ".work > .side:not(.right)", "来源": ".work > .pane.left", "目标": ".work > .pane.right", "预览": ".work > .pane.right", "映射": ".work > .side.right"}[name]
    expect(page.locator(visible)).to_be_visible()


def shot(page: Page, name: str):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def make_field(page: Page, needle: str, name: str, ctx: str | None = None):
    pane(page, "目标")
    page.evaluate(SELECT_JS, [".pane.right", needle, ctx])
    page.get_by_role("button", name="✎ 设为待填区域").click()
    dlg = page.locator(".modal")
    dlg.get_by_placeholder("例如：客户名称").fill(name)
    dlg.get_by_role("button", name="创建字段").click()
    expect(page.locator(".pane.right .fldchip", has_text=name)).to_be_visible(timeout=10000)


def map_field(page: Page, cell_text: str, field: str, date_fmt: str | None = None):
    pane(page, "来源")
    click_cell(page, ".pane.left", cell_text)
    # the selection bar keeps the source selection (as sheet!cell) while the target panel is shown
    expect(page.locator(".selbar")).to_contain_text(re.compile(r"来源：.*!\w+"))
    pane(page, "目标")
    page.locator(".pane.right .fldchip", has_text=field).click()
    btn = page.get_by_role("button", name="⇄ 建立映射")
    box = btn.bounding_box()
    assert box and box["height"] >= 40, ("mapping button too small for touch", box)
    btn.click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("随样本变化：每份输出读取当前样本对应的值", exact=False)).to_be_visible()
    if date_fmt:
        dlg.locator("select").filter(has_text="添加转换规则").select_option("date_format")
        dlg.locator("select").filter(has_text="yyyy-mm-dd").first.select_option(date_fmt)
    dlg.get_by_role("button", name="保存映射").click()
    expect(page.locator(".modal")).to_have_count(0, timeout=10000)


def main():
    with target(ARG_BASE) as base:
        t0 = time.time()
        with sync_playwright() as p:
            # E2E_ENGINE=webkit runs the same flow in Playwright's WebKit build (the instance is on
            # 127.0.0.1, so no hostname resolution is involved); default is the installed Chrome.
            engine = os.environ.get("E2E_ENGINE", "chromium")
            browser = p.webkit.launch(headless=True) if engine == "webkit" else p.chromium.launch(channel="chrome", headless=True)
            ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2, is_mobile=True,
                                      has_touch=True, accept_downloads=True, locale="zh-CN")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            if os.environ.get("E2E_DEBUG"):   # print the browser console live while diagnosing a failure
                page.on("console", lambda m: print(f"[console.{m.type}] {m.text[:300]}", flush=True))
                page.on("pageerror", lambda e: print(f"[pageerror] {str(e)[:300]}", flush=True))
            page.on("console", lambda m: errors.append("CSP: " + m.text[:200]) if "Content Security Policy" in m.text else None)
            page.goto(base + "/")
            # 1. project
            page.get_by_role("button", name="＋ 新建项目").click()
            page.locator(".modal input").fill("手机验收：客户合同")
            page.locator(".modal").get_by_role("button", name="确定").click()
            page.wait_for_url(re.compile(r"/p/p_"))
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
            # 2. source: the file panel is one of the four panels on a phone
            pane(page, "文件")
            page.locator(".side input[type=file]").set_input_files(str(DATA / "测试_客户100.xlsx"))
            expect(page.locator(".fileitem", has_text="测试_客户100.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
            shot(page, "20_手机_文件面板")
            # 3. template
            pane(page, "目标")
            page.get_by_role("button", name="设置模板").first.click()
            page.locator(".modal input[type=file]").set_input_files(str(DATA / "测试_合同模板.docx"))
            expect(page.locator(".pane.right p.doc-p").first).to_be_visible(timeout=20000)
            shot(page, "21_手机_目标模板")
            # 4. fields
            make_field(page, "示例客户名称", "客户名称")
            make_field(page, "000000", "客户编号", "客户编号 000000")
            make_field(page, "2020-01-01", "签约日期")
            make_field(page, "0.00", "合同金额")
            # 5. samples (the step bar is visible on phones)
            page.locator("button.step", has_text="定义样本").click()
            dlg = page.locator(".modal")
            expect(dlg.get_by_text("识别样本")).to_be_visible()
            dlg.locator("select").filter(has_text="（不设置）").select_option("客户编号")
            expect(dlg.locator(".statbox .v").first).to_have_text("100", timeout=10000)
            dlg.get_by_role("button", name="确认样本规则").click()
            try:
                expect(page.locator(".modal")).to_have_count(0)
            except AssertionError:
                print("[diag] rule dialog still open:", page.locator(".modal").first.inner_text()[:500].replace("\n", " | "), flush=True)
                shot(page, "25_手机_样本规则_失败")
                raise
            # 6. mappings across the panel switch
            map_field(page, "测试钱磊敏", "客户名称")
            map_field(page, "000001", "客户编号")
            map_field(page, "2025-01-04", "签约日期", date_fmt="yyyy年m月d日")
            map_field(page, "1,123.45", "合同金额")
            pane(page, "映射")
            page.locator(".tabs button", has_text="映射").click()
            expect(page.locator(".mapitem")).to_have_count(4)
            shot(page, "22_手机_映射列表")
            # 7. preview
            page.get_by_role("button", name="👁 预览").click()
            pane(page, "预览")
            expect(page.locator(".pane.right p.doc-p", has_text="测试钱磊敏")).to_be_visible(timeout=20000)
            body = page.locator(".pane.right .doc-page").inner_text()
            assert "000001" in body and "2025年1月4日" in body and "1,123.45" in body, body[:400]
            shot(page, "23_手机_预览")
            page.get_by_role("button", name="返回模板").click()
            # 8. generate
            page.get_by_role("button", name="▶ 生成").click()
            dlg = page.locator(".modal")
            expect(dlg.get_by_text("有效样本")).to_be_visible(timeout=20000)
            dlg.locator("input.mono").fill("{客户编号}_{客户名称}")
            expect(dlg.get_by_text("000001_测试钱磊敏.docx")).to_be_visible(timeout=10000)
            start = dlg.get_by_role("button", name=re.compile("开始生成 100 个文件"))
            sb = start.bounding_box()
            assert sb and sb["y"] + sb["height"] <= HEIGHT, ("primary action below the fold", sb)
            start.click()
            page.wait_for_url(re.compile(r"/jobs/j_"))
            expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
            shot(page, "24_手机_任务完成")
            # 9. download and read back
            with page.expect_download() as dl:
                page.get_by_text("⬇ 下载全部（ZIP）").click()
            z = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes()))
            names = [n for n in z.namelist() if n.endswith(".docx")]
            assert len(names) == 100, len(names)
            d1 = docx.Document(io.BytesIO(z.read("000001_测试钱磊敏.docx")))
            text = "\n".join(par.text for par in d1.paragraphs)
            assert "甲方（客户）：测试钱磊敏，客户编号 000001。" in text, text
            assert "签约日期：2025年1月4日；合同金额：人民币 1,123.45 元。" in text, text
            assert not errors, errors
            browser.close()
        print(f"phone flow ok in {time.time() - t0:.1f}s: 100 documents generated and read back at {WIDTH}×{HEIGHT} ({os.environ.get('E2E_ENGINE', 'chromium')})")


if __name__ == "__main__":
    main()
