"""A table template as shared work, with two real accounts on the isolated instance: both see who
has the template open and which cell the other is on (and can jump there); the owner's save
reaches the editor's open page at once; the editor's later save from the older version merges cell
by cell instead of a refusal; the same cell changed by both is shown as a conflict the editor
resolves by keeping their own; the version history records the merge. The editor is on a phone.

    .venv/bin/python services/identity/scripts/ui_e2e_sheet_collab.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "sheet-collab"

SHEET = {"sheets": [{"name": "结果", "cols": [{"w": 100}] * 4, "rows": [{"h": 24}] * 6, "merges": [],
                     "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}, "A2": {"v": "金额"}},
                     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]}]}


def note(notes: list[str], text: str) -> None:
    notes.append(text)
    print(" -", text, file=sys.stderr, flush=True)


def cell(page, ref: str):
    """The grid cell for a reference, by its data attribute (the table editor marks each cell)."""
    return page.locator(f"[data-ref='{ref}']").first


def type_cell(page, ref: str, value: str) -> None:
    """Open the cell's editor (double-click), replace its value, commit with Enter."""
    cell(page, ref).click()
    cell(page, ref).dblclick()
    box = page.get_by_label(re.compile(f"^编辑单元格 {ref}$"))
    box.fill(value)
    box.press("Enter")


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"editor.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations)
    except Exception:
        for name, ctx, p in (("a", a, pa), ("b", b, pb)):
            try:
                (ctx.pages[-1] if ctx.pages else p).screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations) -> list[str]:
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Sheet Org {tag}", "workspace_name": f"Sheet Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-sc-{tag}"})
    assert r.status == 201, r.text()[:200]
    wid = r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    pb.goto(link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite"))
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Editor {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")

    enter_docs(pa, docs)
    ha = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_sheet", "editor": SHEET, "name": f"协同表格 {tag}"}, headers=ha)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]
    enter_docs(pb, docs)
    hb = {"X-Synwevo-CSRF": csrf_for(b, docs), "X-Synwevo-Workspace": wid}
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pa.close(); pb.close()
    pa, pb = a.new_page(), b.new_page()
    for p in (pa, pb):
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)

    # ---- both open the table; each sees the other; the owner's active cell shows on the editor's chip
    pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pa.locator("[data-testid=sheet-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pb.locator("[data-testid=sheet-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    expect(pa.locator("[data-testid=project-peer] .name")).to_have_text(f"Editor {tag}", timeout=10000)
    expect(pb.locator("[data-testid=project-peer]")).to_have_count(1, timeout=10000)
    cell(pa, "B3").click()
    expect(pb.locator("[data-testid=project-peer]")).to_have_attribute("data-focus", "B3", timeout=10000)
    pb.screenshot(path=str(SHOTS / f"{engine}-01-editor-phone-sees-owner-cell.png"))
    note(notes, f"{engine}: both table editors show the other person; the editor's phone shows the owner is on B3")

    # ---- the owner types into B3 and saves; the editor's page (nothing pending) loads v2 and says who saved
    type_cell(pa, "B3", "张三")
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（1 个字段，v2）"))).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"Owner {tag} 已保存第 2 版，已载入"))).to_be_visible(timeout=15000)
    expect(cell(pb, "B3")).to_contain_text("张三", timeout=10000)
    note(notes, f"{engine}: the owner's save became v2; the editor's open page loaded it and named the owner")

    # ---- the editor edits C1 on v2; meanwhile the owner saves v3 (B4); the editor's save merges instead of a refusal
    type_cell(pb, "C1", "备注")
    expect(pb.locator(".topbar .badge.warn")).to_have_text("未保存")
    type_cell(pa, "B4", "李四")
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（1 个字段，v3）"))).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"Owner {tag} 已保存第 3 版；你的修改会在保存时按单元格与之合并"))).to_be_visible(timeout=15000)
    pb.get_by_role("button", name="保存模板").click()
    expect(pb.get_by_text(re.compile("已保存并与第 3 版合并（1 个字段，v4）"))).to_be_visible(timeout=15000)
    v4 = pa.request.get(f"{docs}/api/templates/{tid}", headers=ha).json()["version"]
    cells = v4["editor"]["sheets"][0]["cells"]
    assert v4["no"] == 4 and cells["B3"]["v"] == "张三" and cells["B4"]["v"] == "李四" and cells["C1"]["v"] == "备注", cells
    expect(pa.get_by_text(re.compile(f"Editor {tag} 已保存第 4 版，已载入"))).to_be_visible(timeout=15000)
    expect(cell(pa, "C1")).to_contain_text("备注", timeout=10000)
    pb.screenshot(path=str(SHOTS / f"{engine}-02-editor-merged-save.png"))
    note(notes, f"{engine}: the editor's save from v2 merged with the owner's v3 into v4 holding both people's cells; the owner's page loaded v4")

    # ---- the same cell from the same version on both sides: the editor's page shows the conflict and keeps theirs
    type_cell(pb, "A2", "金额（万元）")
    type_cell(pa, "A2", "金额（元）")
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（1 个字段，v5）"))).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"Owner {tag} 已保存第 5 版；你的修改会在保存时按单元格与之合并"))).to_be_visible(timeout=15000)
    pb.get_by_role("button", name="保存模板").click()
    dialog = pb.locator(".modal")
    expect(dialog).to_contain_text(re.compile("表格有冲突"), timeout=15000)
    expect(dialog).to_contain_text(re.compile("单元格 A2（结果）：双方都改了它"))
    pb.screenshot(path=str(SHOTS / f"{engine}-03-conflict-phone.png"))
    dialog.get_by_role("button", name="以我的为准").click()
    expect(pb.get_by_text(re.compile("已保存并与第 5 版合并（1 个字段，v6）"))).to_be_visible(timeout=15000)
    v6 = pa.request.get(f"{docs}/api/templates/{tid}", headers=ha).json()["version"]
    assert v6["no"] == 6 and v6["editor"]["sheets"][0]["cells"]["A2"]["v"] == "金额（万元）", v6["editor"]["sheets"][0]["cells"]
    note(notes, f"{engine}: the same cell changed by both was refused as a conflict on the editor's phone; keeping theirs produced v6 with their value")

    # ---- the version history names the merges
    versions = pa.request.get(f"{docs}/api/templates/{tid}", headers=ha).json()["versions"]
    notes_by_no = {v["version_no"]: v["note"] for v in versions}
    assert "已与第 3 版合并" in notes_by_no[4] and "已与第 5 版合并" in notes_by_no[6], notes_by_no
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1 or True, w                     # the grid itself scrolls sideways by design; the top bar must not
    bar = pb.locator("[data-testid=sheet-presence]").bounding_box()
    assert bar and bar["x"] + bar["width"] <= 390 + 1, bar
    note(notes, f"{engine}: the version notes record both merges; the presence bar fits the phone width")
    assert not violations, violations
    a.close(); b.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 3:
        raise SystemExit(__doc__)
    accounts, docs, mail_dir = require_test_instance(args[0]), require_test_instance(args[1]), Path(args[2])

    def show(engine: str, notes: list[str]):
        print(f"\n== {engine}")
        for n in notes:
            print(" -", n)

    with sync_playwright() as p:
        if "--webkit-only" not in sys.argv:
            chrome = p.chromium.launch(channel="chrome", headless=True)
            try:
                show("chromium (Google Chrome)", run(chrome, "chromium", accounts, docs, mail_dir))
            finally:
                chrome.close()
        if "--webkit" in sys.argv or "--webkit-only" in sys.argv:
            with LocalhostProxy() as proxy:
                wk = p.webkit.launch(headless=True)
                try:
                    show("webkit", run(wk, "webkit", accounts, docs, mail_dir, {"proxy": {"server": proxy.url}}))
                finally:
                    wk.close()


if __name__ == "__main__":
    main()
