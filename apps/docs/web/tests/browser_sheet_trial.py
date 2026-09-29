"""The live-table trial in real Chrome (docs-sheet-live.md §15): one workspace listed in
SYNWEVO_SHEET_LIVE (written with spaces, an empty entry and an id that is no workspace), another
workspace not listed; the deleted-sheets restore list on a 390 px phone and on a desktop, announced
to screen readers, in three languages.

Synthetic accounts on a throwaway, test-marked server this script starts itself (never the everyday
data, never the isolated 317xx instance); no request mocking. Artifacts go under --evidence.

    .venv/bin/python apps/docs/web/tests/browser_sheet_trial.py --evidence <temporary-dir>

Needs the built Docs shell (npm run build) and Chrome for Playwright."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from browser_review import load

PASSWORD = "Synthetic-field-river-4729!"  # isolated fixture only
# TEST FIXTURE: a two-sheet table
SHEET = {"sheets": [
    {"name": "结果", "cols": [{"w": 100}] * 4, "rows": [{"h": 24}] * 6, "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}}, "merges": [], "fields": []},
    {"name": "明细", "cols": [{"w": 100}] * 3, "rows": [{"h": 24}] * 4, "cells": {"A1": {"v": "明细行"}}, "merges": [], "fields": []},
]}


def prepare(data: Path, out: Path) -> None:
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.context import as_tenant
    from synwevo_core.identity import Identity
    from synwevo_core.store import Store
    from synwevo_docs.builtin_service import create_builtin
    from synwevo_docs.services import App

    assert not data.exists(), "Only a new synthetic database may be seeded"
    store = Store(data)
    assert store.instance_kind() == "test"
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    owner = ident.create_user("owner@example.test", PASSWORD, status="active", display_name="Owner", with_personal_workspace=False)
    trial = ident.create_workspace("Trial workspace", owner_id=owner["id"])
    editor = ident.create_user("editor@example.test", PASSWORD, status="active", display_name="Editor", with_personal_workspace=False)
    ident.add_member(trial["id"], editor["id"], "editor")
    other = ident.create_user("other@example.test", PASSWORD, status="active", display_name="Other", with_personal_workspace=False)
    outside = ident.create_workspace("Not in the trial", owner_id=other["id"])
    app = App(store)
    with as_tenant(trial["id"]):
        t1 = create_builtin(app, {"kind": "builtin_sheet", "editor": SHEET, "name": "试用表格"})
    with as_tenant(outside["id"]):
        t2 = create_builtin(app, {"kind": "builtin_sheet", "editor": SHEET, "name": "未试用表格"})
    out.write_text(json.dumps({"trial": trial["id"], "outside": outside["id"], "t1": t1["id"], "t2": t2["id"]}))


def exercise(base: str, ids: dict, evidence: Path) -> list[str]:
    from playwright.sync_api import expect, sync_playwright

    passed: list[str] = []
    errors: list[str] = []

    def done(name: str) -> None:
        passed.append(name)
        print("PASS " + name, flush=True)
        (evidence / "checks.json").write_text(json.dumps({"passed": passed, "page_errors": errors}, ensure_ascii=False, indent=2))

    def login(browser, who: str, phone: bool = False):
        opts = dict(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2) if phone \
            else dict(viewport={"width": 1440, "height": 900})
        ctx = browser.new_context(locale="zh-CN", **opts)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"{who}: {e}"))
        pg.on("console", lambda m: errors.append(f"{who}: {m.text[:160]}") if "Content Security Policy" in m.text else None)
        pg.goto(base + "/login?next=/")
        pg.get_by_label("邮箱", exact=True).fill(who + "@example.test")
        pg.locator("input[type=password]").fill(PASSWORD)
        pg.get_by_role("button", name="登录", exact=True).click()
        expect(pg).not_to_have_url(re.compile("/login"), timeout=15000)
        return ctx, pg

    def status(pg):
        return pg.locator("[data-testid=sheet-sync]")

    def no_sideways_scroll(pg) -> bool:
        return pg.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")

    def within_width(pg, locator, width: int) -> None:
        box = locator.bounding_box()
        assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width + 1, box

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception:                                   # noqa: BLE001 — no Chrome here: Playwright's own Chromium
            browser = pw.chromium.launch(headless=True)
        try:
            _ca, pa = login(browser, "owner")
            _cb, pb = login(browser, "editor", phone=True)
            _cc, pc = login(browser, "other")
            t1, t2 = ids["t1"], ids["t2"]

            # ---- the listed workspace is live; the other one is today's table editor
            pa.goto(f"{base}/templates/{t1}/edit?lang=zh-Hans")
            expect(status(pa)).to_have_text("实时协同中", timeout=20000)
            pb.goto(f"{base}/templates/{t1}/edit?lang=zh-Hans")
            expect(status(pb)).to_have_text("实时协同中", timeout=20000)
            pc.goto(f"{base}/templates/{t2}/edit?lang=zh-Hans")
            expect(pc.get_by_role("button", name="保存模板")).to_be_enabled(timeout=20000)
            expect(pc.locator("[data-testid=sheet-presence]")).to_be_visible(timeout=10000)
            pc.wait_for_timeout(1500)
            expect(status(pc)).to_have_count(0)                  # never live: no live status line at all
            expect(pc.get_by_text("实时协同中")).to_have_count(0)
            done("a table in the listed workspace opens live on a desktop and a 390 px phone; a table in the workspace not listed opens in the save-and-merge editor")

            # ---- the owner deletes a sheet: the phone shows the restore list and hears it
            pa.locator(".sheettab").filter(has_text=re.compile("^明细$")).click()
            pa.get_by_role("button", name="删除当前表").click()
            pa.locator(".modal").get_by_role("button", name="删除工作表").click()
            expect(pa.locator(".sheettab").filter(has_text=re.compile("^明细$"))).to_have_count(0, timeout=10000)
            gone_b = pb.locator("[data-testid=sheet-gone]")
            expect(gone_b).to_be_visible(timeout=10000)
            expect(gone_b).to_have_text(re.compile("已删除的工作表（1）：保存为版本之前，可以在这里恢复。"))
            expect(gone_b).to_be_in_viewport()
            within_width(pb, gone_b, 390)
            within_width(pb, pb.locator("[data-testid=sheet-restore]"), 390)
            assert no_sideways_scroll(pb), "the phone page scrolls sideways"
            expect(pb.locator("[data-testid=sheet-announce]")).to_contain_text("工作表「明细」已被删除；保存为版本之前，可以从「已删除的工作表」恢复。", timeout=6000)
            pb.screenshot(path=str(evidence / "phone-deleted-sheets-zh-Hans.png"))
            done("phone (390 px): someone else's delete shows the deleted-sheets list above the grid, inside the screen, no sideways scroll; screen readers hear which sheet and where to restore it")
            gone_a = pa.locator("[data-testid=sheet-gone]")
            expect(gone_a).to_be_visible()
            expect(gone_a).to_be_in_viewport()
            expect(pa.locator("[data-testid=sheet-announce]")).to_contain_text("工作表「明细」已被删除", timeout=6000)
            pa.screenshot(path=str(evidence / "desktop-deleted-sheets-zh-Hans.png"))
            done("desktop: the person who deleted the sheet sees the same list and hears the same sentence")

            # ---- three languages (the page's own language menu)
            language = pb.get_by_label(re.compile("^(语言|語言|Language)$"))
            language.select_option("en")
            expect(status(pb)).to_have_text("Live", timeout=20000)
            expect(gone_b).to_have_text(re.compile(r"Deleted sheet \(1\): until the table is saved as a version, it can be restored here\."), timeout=10000)
            txt = gone_b.inner_text()
            assert not re.search(r"[一-鿿]", txt.replace("明细", "")), txt
            assert no_sideways_scroll(pb)
            opts = pb.locator("[data-testid=sheet-restore] option").all_inner_texts()
            assert opts == ["Choose a sheet to restore…", "Restore “明细”"], opts
            assert pb.locator("[data-testid=sheet-gone]").get_attribute("aria-label") == "Deleted sheet (1)"
            pb.screenshot(path=str(evidence / "phone-deleted-sheets-en.png"))
            language.select_option("zh-Hant")
            expect(status(pb)).to_have_text("即時協同中", timeout=20000)
            expect(gone_b).to_have_text(re.compile("已刪除的工作表（1）：儲存為版本之前，可以在這裡恢復。"), timeout=10000)
            assert no_sideways_scroll(pb)
            pb.screenshot(path=str(evidence / "phone-deleted-sheets-zh-Hant.png"))
            done("the list reads in English (no Chinese besides the sheet's own name) and Traditional Chinese on the phone")

            # ---- restored from the phone: back for both, the list gone
            language.select_option("zh-Hans")
            expect(status(pb)).to_have_text("实时协同中", timeout=20000)
            pb.locator("[data-testid=sheet-restore]").select_option(label="恢复「明细」")
            expect(pa.locator(".sheettab").filter(has_text=re.compile("^明细$"))).to_have_count(1, timeout=10000)
            expect(pb.locator("[data-testid=sheet-gone]")).to_have_count(0, timeout=10000)
            expect(pa.locator("[data-testid=sheet-gone]")).to_have_count(0, timeout=10000)
            done("restored from the phone: the sheet is back on both pages and the list is gone")
            assert not errors, errors
            done("no page errors and no CSP violations")
        finally:
            browser.close()
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--prepare", type=Path)
    parser.add_argument("--ids", type=Path)
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare, args.ids)
        return
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="sheet-trial-", dir=evidence))
    env = load("_testenv")
    port = env._free_port()
    base = f"http://127.0.0.1:{port}"
    child = env._child_env(root, port)
    child.update(SYNWEVO_ROLE="all", SYNWEVO_UI_AUTH="session", SYNWEVO_COOKIE_SECURE="0",
                 SYNWEVO_ISSUER=base, SYNWEVO_PUBLIC_URL=base, SYNWEVO_APP_URL=base,
                 SYNWEVO_MAIL_MODE="file", SYNWEVO_MAIL_DIR=str(root / "mail"))
    ids_file = root / "ids.json"
    subprocess.run([sys.executable, __file__, "--prepare", str(root / "data"), "--ids", str(ids_file)], env=child, check=True)
    ids = json.loads(ids_file.read_text())
    # the trial: the listed workspace, written the way an operator might (spaces, an empty entry, a typo'd id)
    child["SYNWEVO_SHEET_LIVE"] = f" {ids['trial']} ,, ws_not_here "
    log = root / "server.log"
    with log.open("w") as output:
        proc = subprocess.Popen([sys.executable, "-m", "synwevo_server.run"], cwd=root, env=child, stdout=output, stderr=subprocess.STDOUT)
    try:
        env._wait(base, proc, log)
        assert env.health(base)["instance"] == "test"
        (evidence / "instance.json").write_text(json.dumps({"base": base, "data": str(root / "data"), "instance": "test"}, indent=2))
        passed = exercise(base, ids, evidence)
        said = log.read_text(errors="replace")
        assert "ws_not_here" in said, "the start-up log warns about the listed id that is no workspace"
        print("PASS the start-up log warns about the listed id that is no workspace", flush=True)
        print(f"{len(passed) + 1} checks passed")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


if __name__ == "__main__":
    main()
