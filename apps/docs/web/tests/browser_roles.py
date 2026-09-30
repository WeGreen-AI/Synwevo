"""Synthetic local accounts + real password login, Docs APIs and Chrome.

Tests readonly/export roles, preview failures, debounce, editor keyboard/IME and narrow layout.
All artifacts, databases and screenshots are placed under --evidence. No intercepted responses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from browser_review import load

PASSWORD = "Synthetic-field-river-4729!"  # isolated fixture only


def prepare(data: Path):
    from synwevo_core.store import Store
    from synwevo_core.identity import Identity
    from synwevo_core.apps import bootstrap_docs_app

    assert not data.exists(), "Only a new synthetic database may be seeded"
    store = Store(data)
    assert store.instance_kind() == "test"
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    owner = ident.create_user("owner@example.test", PASSWORD, status="active", with_personal_workspace=False)
    ws = ident.create_workspace("Synthetic Docs review", owner_id=owner["id"])
    for who, role, export in [("editor", "editor", None), ("viewer", "viewer", False), ("exporter", "viewer", True)]:
        user = ident.create_user(who + "@example.test", PASSWORD, status="active", with_personal_workspace=False)
        ident.add_member(ws["id"], user["id"], role, can_export=export)


def exercise(base: str, evidence: Path):
    from playwright.sync_api import expect, sync_playwright

    core = sys.modules["ui_e2e"]
    keys = load("ui_e2e_keyboard")
    keys.BASE = base
    passed = []
    errors = []

    def done(name):
        passed.append(name)
        print("PASS " + name, flush=True)
        (evidence / "checks.json").write_text(json.dumps({"passed": passed, "page_errors": errors}, ensure_ascii=False, indent=2))

    def login(browser, who, target="/"):
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN", accept_downloads=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "/login?next=" + target)
        pg.get_by_label("邮箱", exact=True).fill(who + "@example.test")
        pg.locator("input[type=password]").fill(PASSWORD)
        pg.get_by_role("button", name="登录", exact=True).click()
        expect(pg).not_to_have_url(re.compile("/login"), timeout=15000)
        return ctx, pg

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context, page = login(browser, "owner")
        try:
            pid = keys.build_project(page)
            tid = page.request.get(f"{base}/api/projects/{pid}").json()["template_id"]
            done("real password login; source/template upload; three mappings persisted")

            # The preview is genuinely rendered; an unknown sample must clear old output.
            page.get_by_role("button", name="👁 预览").click()
            expect(page.get_by_role("link", name="下载此预览")).to_be_visible(timeout=20000)
            expect(page.locator('.pane.right button[aria-label="选择整个表格"]')).to_have_count(0)
            search = page.get_by_label("按样本标识查找")
            search.fill("NONEXISTENT-SYNTHETIC-KEY")
            search.press("Enter")
            expect(page.locator(".pane.right [role=alert]")).to_be_visible(timeout=10000)
            expect(page.get_by_role("link", name="下载此预览")).to_have_count(0)
            expect(page.locator(".pane.right .doc-page")).to_have_count(0)
            page.screenshot(path=str(evidence / "preview-error-no-stale-output.png"))
            page.get_by_role("button", name="重试当前样本").click()
            expect(page.get_by_role("link", name="下载此预览")).to_be_visible(timeout=20000)
            page.get_by_label("样本序号").fill("1.5")
            page.get_by_label("样本序号").press("Enter")
            expect(page.get_by_label("样本序号")).to_have_value("1")
            done("real preview, absent readonly selectors, unknown-key failure clears output, integer index")

            # A genuine slower transport, without fake API responses.
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 250, "downloadThroughput": -1, "uploadThroughput": -1})
            page.get_by_role("button", name="▶ 生成").click()
            dlg = page.get_by_role("dialog")
            expect(dlg.get_by_label("文件命名规则")).to_be_visible(timeout=15000)
            dlg.get_by_label("文件命名规则").fill("changed_{序号}")
            expect(dlg.locator(".modal-f .primary")).to_be_disabled()
            expect(dlg.get_by_role("status")).to_contain_text("当前设置")
            expect(dlg.locator(".modal-f .primary")).to_be_enabled(timeout=15000)
            expect(dlg.locator("code").first).to_contain_text("changed_")
            dlg.get_by_label("文件命名规则").fill("final_{序号}")
            expect(dlg.locator(".modal-f .primary")).to_be_disabled()
            expect(dlg.locator(".modal-f .primary")).to_be_enabled(timeout=15000)
            page.screenshot(path=str(evidence / "generation-current-settings.png"))
            dlg.get_by_role("button", name="取消", exact=True).click()
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
            done("batch action waits for current naming validation over delayed real transport")

            page.get_by_role("button", name="返回模板").click()
            page.locator(".menu-wrap > button").click()
            expect(page.get_by_role("menu")).to_be_visible()
            page.keyboard.press("Tab")
            expect(page.get_by_role("menu")).to_have_count(0)
            expect(page.get_by_role("button", name="👁 预览")).to_be_focused()
            row = page.locator(".pane.right .rowhandle").first
            assert row.evaluate("e=>getComputedStyle(e).display") != "none"
            row.focus()
            row.press("Enter")
            expect(page.get_by_role("button", name="✎ 设为待填区域")).to_be_visible()
            done("menu Tab restores sequential focus; document row handle is keyboard reachable and Enter selects")
            page.locator(".tabs button", has_text="样本").click()
            page.get_by_role("button", name="修改规则", exact=True).click()
            dlg = page.get_by_role("dialog")
            expect(dlg.get_by_role("button", name="确认样本规则")).to_be_enabled(timeout=15000)
            dlg.locator("label.opt", has_text="每块一份").click()
            expect(dlg.get_by_role("button", name="确认样本规则")).to_be_disabled()
            expect(dlg.get_by_role("alert")).to_contain_text("标记文字")
            dlg.get_by_role("button", name="取消", exact=True).click()
            done("invalid sample rule immediately blocks save without a stale count")

            for who, export in [("viewer", False), ("exporter", True), ("editor", True)]:
                ctx, pg = login(browser, who, f"/p/{pid}")
                expect(pg.locator(".pane.right .doc-page")).to_be_visible(timeout=15000)
                pg.locator(".tabs button", has_text="样本").click()
                rule = pg.get_by_role("button", name="修改规则", exact=True)
                if who != "editor":
                    expect(rule).to_be_disabled()
                    expect(pg.get_by_role("button", name="▶ 生成")).to_be_disabled()
                else:
                    expect(rule).to_be_enabled()
                expect(pg.get_by_role("link", name="工作副本", exact=True)).to_have_count(1 if export else 0)
                pg.get_by_role("button", name="👁 预览").click()
                expect(pg.locator(".pane.right .doc-page")).to_be_visible(timeout=15000)
                expect(pg.get_by_role("link", name="下载此预览")).to_have_count(1 if export else 0)
                if export:
                    with pg.expect_download() as download:
                        pg.get_by_role("link", name="下载此预览").click()
                    assert Path(download.value.path()).stat().st_size > 100
                pg.screenshot(path=str(evidence / f"{who}-preview.png"))
                if who != "editor":
                    for path in ["/templates/new/doc", "/templates/new/sheet", f"/templates/{tid}/edit"]:
                        pg.goto(base + path)
                        expect(pg.get_by_text(re.compile("只能查看，不能修改"))).to_be_visible()
                        expect(pg.locator(".editor-shell")).to_have_count(0)
                done(f"{who}: real session, rule/write/export controls, preview download={export}, direct editor routes")
                ctx.close()

            # Narrow workspace retains upload, source, template and sample controls in scrollable sections.
            page.set_viewport_size({"width": 390, "height": 844})
            page.emulate_media(reduced_motion="reduce")
            page.reload()
            expect(page.locator(".pane.right .doc-page")).to_be_visible(timeout=15000)
            page.locator(".side .dropzone").scroll_into_view_if_needed()
            expect(page.locator(".side .dropzone")).to_be_in_viewport()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--dur-2').trim()") in ("0ms", "0s")
            page.screenshot(path=str(evidence / "narrow-source-controls.png"))
            page.get_by_role("button", name="▶ 生成").click()
            expect(page.get_by_role("dialog").get_by_label("文件命名规则")).to_be_visible(timeout=15000)
            assert page.get_by_role("dialog").evaluate("e=>e.scrollWidth <= e.clientWidth")
            page.screenshot(path=str(evidence / "narrow-generation.png"))
            page.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
            done("390px workspace controls reachable; modal fits width; reduced-motion tokens zero")

            page.set_viewport_size({"width": 1440, "height": 900})
            page.goto(base + "/templates/new/sheet")
            grid = page.locator(".grid-scroll")
            grid.click(position={"x": 60, "y": 30})
            expect(grid).to_be_focused()
            page.keyboard.type("Synthetic sheet")
            page.keyboard.press("Enter")
            page.keyboard.type("Amount")
            page.keyboard.press("Tab")
            assert grid.evaluate("e=>!e.contains(document.activeElement)")
            expect(page.locator(".cellinfo")).to_have_text("A2")
            page.get_by_role("button", name="设为字段单元格").click()
            page.get_by_role("dialog").locator("input").fill("金额")
            page.get_by_role("dialog").get_by_role("button", name="确定", exact=True).click()
            page.get_by_role("button", name="保存模板", exact=True).click()
            page.wait_for_url(re.compile(r"/templates/t_.*/edit"))
            expect(page.locator(".topbar [role=status]")).to_contain_text("已保存")
            sheet_id = page.url.split("/templates/")[1].split("/")[0]
            saved = page.request.get(f"{base}/api/templates/{sheet_id}").json()
            assert saved["version"]["slots"][0]["locator"]["cell"] == "A2"
            assert saved["version"]["editor"]["sheets"][0]["cells"]["A2"]["v"] == "Amount"
            done("Tab leaves spreadsheet without moving selection; A2 field/value actually persisted (legacy B2 assertion superseded)")
            grid.click(position={"x": 60, "y": 30})
            page.keyboard.press("ArrowDown")
            page.keyboard.press("ArrowRight")
            page.keyboard.press("Shift+ArrowDown")
            page.keyboard.press("Shift+ArrowDown")
            expect(page.locator(".cellinfo")).to_have_text("B2:B4")
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("F2")
            cell = page.get_by_label(re.compile("编辑单元格"))
            cell.fill("合成夹具中文")
            cell.dispatch_event("keydown", {"key": "Enter", "code": "Enter", "isComposing": True, "keyCode": 229})
            expect(cell).to_be_visible()
            cell.dispatch_event("keydown", {"key": "Escape", "isComposing": True, "keyCode": 229})
            expect(cell).to_have_value("合成夹具中文")
            cell.press("Escape")
            expect(cell).to_have_count(0)
            expect(grid).to_be_focused()
            page.keyboard.type("17")
            page.keyboard.press("Enter")
            page.get_by_role("button", name="保存模板", exact=True).click()
            page.wait_for_url(re.compile(r"/templates/t_.*/edit"))
            expect(page.locator(".topbar [role=status]")).to_contain_text("已保存")
            grid = page.locator(".grid-scroll")
            grid.click(position={"x": 60, "y": 30})
            page.keyboard.type("29")
            page.keyboard.press("Enter")
            page.get_by_role("button", name="保存模板", exact=True).click()
            expect(page.locator(".topbar [role=status]")).to_contain_text("已保存")
            page.get_by_role("button", name="撤销", exact=True).click()
            expect(page.locator(".topbar [role=status]")).to_contain_text("未保存")
            page.get_by_role("button", name="＋", exact=True).click()
            expect(page.locator(".cellinfo")).to_have_text("A1")
            page.get_by_label("列宽").fill("88")
            page.screenshot(path=str(evidence / "sheet-keyboard-undo.png"))
            done("sheet click focus, anchored Shift range, synthetic IME Enter/Escape, save/undo dirty, new-sheet bounds")
            assert not errors, errors
            done("no browser pageerror events")
        except Exception:
            page.screenshot(path=str(evidence / "failure.png"))
            (evidence / "failure-dom.txt").write_text(page.locator("body").inner_text())
            raise
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--prepare", type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare)
        return
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="roles-", dir=evidence))
    env = load("_testenv")
    load("ui_e2e")
    port = env._free_port()
    base = f"http://127.0.0.1:{port}"
    child = env._child_env(root, port)
    child.update(SYNWEVO_ROLE="all", SYNWEVO_UI_AUTH="session", SYNWEVO_COOKIE_SECURE="0",
                 SYNWEVO_ISSUER=base, SYNWEVO_PUBLIC_URL=base, SYNWEVO_APP_URL=base,
                 SYNWEVO_MAIL_MODE="file", SYNWEVO_MAIL_DIR=str(root / "mail"))
    subprocess.run([sys.executable, __file__, "--prepare", str(root / "data")], env=child, check=True)
    log = root / "server.log"
    with log.open("w") as output:
        proc = subprocess.Popen([sys.executable, "-m", "synwevo_server.run"], cwd=root, env=child, stdout=output, stderr=subprocess.STDOUT)
    try:
        env._wait(base, proc, log)
        assert env.health(base)["instance"] == "test"
        (evidence / "instance.json").write_text(json.dumps({"base": base, "data": str(root / "data"), "instance": "test"}, indent=2))
        exercise(base, evidence)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
