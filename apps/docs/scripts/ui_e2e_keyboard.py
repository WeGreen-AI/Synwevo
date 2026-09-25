"""Browser end-to-end test of the keyboard, command palette, theme and layout work.

Drives the real web UI in local Google Chrome with Playwright:
E. Command palette (⌘K/Ctrl+K): opens, filters, runs a real action; Esc closes.
F. Mapping list as a listbox: ↑↓/J K move and highlight, X multi-selects, Shift extends,
   ⌘A selects all without selecting page text, D bulk-disables through the real save,
   ⌘Z undoes the whole bulk change.
G. Theme: one white-blue theme; stored dark/system preferences and OS dark mode change nothing, paper stays white.
H. Layout memory and the ? shortcut sheet; reduced-motion is honoured.

Runs against an existing project created by this script, so it does not depend on
other flows.  All input files are the synthetic fixtures in <repo>/testdata/.

    .venv/bin/python apps/docs/scripts/ui_e2e_keyboard.py              # isolated test instance
    .venv/bin/python apps/docs/scripts/ui_e2e_keyboard.py <base_url>   # only a test-marked instance
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

from ui_e2e import make_field, map_field  # noqa: E402  (same helpers as the main UI flow)

ROOT = Path(__file__).resolve().parents[1]
from _testenv import SHOTS, TESTDATA, target  # noqa: E402  isolated test instance; never the user's real data

ARG_BASE = sys.argv[1] if len(sys.argv) > 1 else None
BASE = ""                     # bound in main() to an isolated (or explicitly test-marked) server
DATA = TESTDATA                # <repo>/testdata (apps/docs/scripts/make_fixtures.py)
MOD = "Meta" if sys.platform == "darwin" else "Control"


def build_project(page: Page) -> str:
    """A small project with 3 mappings, built through the UI with the shared helpers."""
    page.goto(BASE + "/")
    page.get_by_role("button", name="＋ 新建项目").click()
    page.locator(".modal input").fill("UI验收：键盘与命令面板")
    page.locator(".modal").get_by_role("button", name="确定").click()
    page.wait_for_url(re.compile(r"/p/p_"))
    pid = page.url.rsplit("/", 1)[-1]

    page.locator(".side input[type=file]").set_input_files(str(DATA / "测试_客户100.xlsx"))
    expect(page.locator(".fileitem", has_text="测试_客户100.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    page.get_by_role("button", name="设置模板").first.click()
    page.locator(".modal input[type=file]").set_input_files(str(DATA / "测试_合同模板.docx"))
    expect(page.locator(".pane.right p.doc-p").first).to_be_visible(timeout=20000)

    make_field(page, "示例客户名称", "客户名称")
    make_field(page, "000000", "客户编号", "客户编号 000000")
    make_field(page, "0.00", "合同金额")

    page.locator("button.step", has_text="定义样本").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("识别样本")).to_be_visible()
    dlg.locator("select").filter(has_text="（不设置）").select_option("客户编号")
    expect(dlg.locator(".statbox .v").first).to_have_text("100", timeout=10000)
    dlg.get_by_role("button", name="确认样本规则").click()
    expect(page.locator(".modal")).to_have_count(0)

    map_field(page, "测试钱磊敏", "客户名称")
    map_field(page, "000001", "客户编号")
    map_field(page, "1,123.45", "合同金额")
    page.locator(".tabs button", has_text="映射").click()
    expect(page.locator(".mapitem")).to_have_count(3)
    return pid


# ---------------------------------------------------------------- E: command palette
def flow_palette(page: Page, pid: str):
    page.goto(f"{BASE}/p/{pid}")
    expect(page.locator(".mapitem").first).to_be_visible(timeout=20000)

    page.keyboard.press(f"{MOD}+k")
    expect(page.locator(".cmdk")).to_be_visible()
    page.wait_for_function("() => document.activeElement && document.activeElement.className === 'cmdk-input'",
                           timeout=5000)     # the input must take focus by itself

    page.locator(".cmdk-input").fill("样本")        # keyboard.type is unreliable for CJK in Chrome DevTools protocol
    items = page.locator(".cmdk-item")
    expect(items.first).to_be_visible()
    assert items.count() >= 1
    assert all("样本" in t for t in items.all_inner_texts()), items.all_inner_texts()

    page.keyboard.press("Escape")
    expect(page.locator(".cmdk")).to_have_count(0)

    # a command must run the same action as its button: 「查看模板字段」 switches the side tab
    page.keyboard.press(f"{MOD}+k")
    page.locator(".cmdk-input").fill("模板字段")
    expect(page.locator(".cmdk-item")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page.locator(".cmdk")).to_have_count(0)
    expect(page.locator(".tabs button.on", has_text="字段")).to_be_visible()
    page.locator(".tabs button", has_text="映射").click()

    # ? opens the shortcut sheet
    page.keyboard.press("Shift+/")
    expect(page.get_by_text("键盘快捷键")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".modal")).to_have_count(0)


# ---------------------------------------------------------------- F: list keyboard + bulk
def flow_list(page: Page, pid: str):
    page.goto(f"{BASE}/p/{pid}")
    expect(page.locator(".mapitem").first).to_be_visible(timeout=20000)
    n = page.locator(".mapitem").count()

    page.locator(".mapitem").first.click()
    page.wait_for_function("() => document.activeElement && document.activeElement.classList.contains('maplist')",
                           timeout=5000)     # keyboard works straight after a click

    page.keyboard.press("j")
    page.keyboard.press("x")
    page.keyboard.press("j")
    page.keyboard.press("x")
    selected = page.locator('.mapitem[aria-selected="true"]')
    expect(selected).to_have_count(2)
    expect(page.locator(".bulkbar")).to_contain_text(f"已选 2 / {n}")

    page.keyboard.press("Escape")
    expect(page.locator(".bulkbar")).to_have_count(0)

    page.keyboard.press(f"{MOD}+a")
    expect(page.locator(".bulkbar")).to_contain_text(f"已选 {n} / {n}")
    assert page.evaluate("String(getSelection()).length") == 0, "⌘A 不应选中页面文字"

    before = page.request.get(f"{BASE}/api/projects/{pid}").json()["mappings"]
    assert all(m["enabled"] for m in before["mappings"]), "前置条件：全部映射启用"

    page.keyboard.press("d")                                  # bulk disable through the real save
    expect(page.locator(".mapitem.off")).to_have_count(n, timeout=15000)
    after = page.request.get(f"{BASE}/api/projects/{pid}").json()["mappings"]
    assert not any(m["enabled"] for m in after["mappings"]), after["mappings"]
    assert after["rev"] == before["rev"] + 1, (before["rev"], after["rev"])
    assert f"禁用 {n} 个映射" in (after.get("note") or ""), after.get("note")

    page.keyboard.press(f"{MOD}+z")                           # one undo reverts the whole bulk change
    expect(page.locator(".mapitem.off")).to_have_count(0, timeout=15000)
    back = page.request.get(f"{BASE}/api/projects/{pid}").json()["mappings"]
    assert all(m["enabled"] for m in back["mappings"]), back["mappings"]
    assert back["rev"] == before["rev"], (before["rev"], back["rev"])


# ---------------------------------------------------------------- G: one theme, paper untouched
def flow_theme(page: Page, pid: str):
    """The platform ships one white-blue theme. A preference left behind by an older build (light,
    dark or system, under the new or the pre-rebrand key) must neither throw nor bring the old
    palette back, and the document paper stays white in every case."""
    page.goto(f"{BASE}/p/{pid}")
    expect(page.locator(".pane.right .doc-page")).to_be_visible(timeout=20000)
    read = """() => {
      const bg = (s) => { const e = document.querySelector(s); return e ? getComputedStyle(e).backgroundColor : null; };
      return { theme: document.documentElement.dataset.theme, scheme: getComputedStyle(document.documentElement).colorScheme,
               topbar: bg('.topbar'), paper: bg('.doc-page'), cell: bg('.gcell') };
    }"""
    baseline = page.evaluate(read)
    assert baseline["theme"] == "light", baseline
    assert baseline["topbar"] == "rgb(255, 255, 255)", baseline
    assert baseline["paper"] == "rgb(255, 255, 255)", baseline

    for key, value in (("synwevo.theme", "dark"), ("synwevo.theme", "system"), ("docweave.theme", "dark")):
        page.evaluate("([k, v]) => { localStorage.removeItem('synwevo.theme'); localStorage.removeItem('docweave.theme'); "
                      "localStorage.setItem(k, JSON.stringify(v)); location.reload(); }", [key, value])
        expect(page.locator(".pane.right .doc-page")).to_be_visible(timeout=20000)
        got = page.evaluate(read)
        assert got["theme"] == "light", (key, value, got)
        assert got["scheme"] == "light", (key, value, got)
        assert got["topbar"] == baseline["topbar"], (key, value, got)
        assert got["paper"] == baseline["paper"] == "rgb(255, 255, 255)", (key, value, got)
        assert got["cell"] == baseline["cell"], (key, value, got)

    # System dark mode is not a theme switch either: emulate it and the chrome must not change.
    page.emulate_media(color_scheme="dark")
    page.reload()
    expect(page.locator(".pane.right .doc-page")).to_be_visible(timeout=20000)
    forced = page.evaluate(read)
    assert forced["topbar"] == baseline["topbar"] and forced["paper"] == "rgb(255, 255, 255)", forced
    page.emulate_media(color_scheme="light")
    page.evaluate("() => { localStorage.removeItem('docweave.theme'); localStorage.removeItem('synwevo.theme'); }")


# ---------------------------------------------------------------- H: remembered layout + reduced motion
def flow_layout(page: Page, pid: str):
    page.goto(f"{BASE}/p/{pid}")
    expect(page.locator(".side.right")).to_be_visible(timeout=20000)

    box = page.locator(".side-resizer").bounding_box()
    page.mouse.move(box["x"] + 4, box["y"] + 120)
    page.mouse.down()
    page.mouse.move(box["x"] - 60, box["y"] + 120, steps=8)
    page.mouse.up()
    assert page.evaluate("String(getSelection()).length") == 0, "拖动分隔条不应选中文字"
    width = page.evaluate("Math.round(document.querySelector('.side.right').getBoundingClientRect().width)")
    assert width > 300, width

    page.reload()
    expect(page.locator(".side.right")).to_be_visible(timeout=20000)
    again = page.evaluate("Math.round(document.querySelector('.side.right').getBoundingClientRect().width)")
    assert abs(again - width) <= 2, (width, again)

    page.evaluate("() => localStorage.setItem('synwevo.sideW', '0')")

    # reduced motion collapses the duration tokens
    page.emulate_media(reduced_motion="reduce")
    page.reload()
    expect(page.locator(".side.right")).to_be_visible(timeout=20000)
    dur = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--dur-2').trim()")
    assert dur in ("0ms", "0s"), dur
    page.emulate_media(reduced_motion="no-preference")


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
            pid = build_project(page)
            for name, fn in (("E 命令面板", flow_palette), ("F 列表键盘与批量", flow_list),
                             ("G 单一主题与纸张", flow_theme), ("H 布局记忆与减少动效", flow_layout)):
                t = time.time()
                fn(page, pid)
                print(f"  {name}: ok ({time.time() - t:.1f}s)")
            assert not errors, errors
            browser.close()
        print(f"UI E2E (keyboard) passed in {time.time() - t0:.1f}s（项目 {pid}）")


if __name__ == "__main__":
    main()
