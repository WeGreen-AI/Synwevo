"""Naming a version when saving (CI-52, 9.13 D 「命名检查点」): the person saving a document or table
template may give the version a note; the history on the project page shows it; the machine API
carries the same field. One real account on the isolated instance; the phone viewport must keep the
top bar within the screen with the note field present.

    .venv/bin/python services/identity/scripts/ui_e2e_version_note.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_collab import SEED, body_editor, note
from ui_e2e_messaging import csrf_of, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "version-note"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner = f"owner.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    pa = a.new_page()
    violations: list[str] = []
    pa.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    pa.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
        sign_in(pa, accounts, owner)
        r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Note Org {tag}", "workspace_name": f"Note Team {tag}"},
                            headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-note-{tag}"})
        assert r.status == 201, r.text()[:200]
        wid = r.json()["workspace"]["id"]
        enter_docs(pa, docs)
        h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
        assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
        r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"命名合同 {tag}"}, headers=h)
        assert r.ok, r.text()[:300]
        tid = r.json()["id"]
        r = pa.request.post(f"{docs}/api/projects", data={"name": f"命名项目 {tag}"}, headers=h)
        assert r.ok, r.text()[:200]
        pid = r.json()["id"]
        assert pa.request.patch(f"{docs}/api/projects/{pid}", data={"template_id": tid}, headers=h).ok

        # ---- the editor: type, name the version, save
        pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
        expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
        ed = body_editor(pa)
        ed.locator("p", has_text="备注段").click()
        pa.keyboard.press("End")
        pa.keyboard.type("——定稿内容")
        field = pa.locator("[data-testid=version-note]")
        expect(field).to_be_visible()
        field.fill("合同定稿（法务已审）")
        pa.get_by_role("button", name="保存模板").click()
        expect(pa.get_by_text(re.compile("已保存（2 个字段，v2）"))).to_be_visible(timeout=15000)
        expect(field).to_have_value("")
        pa.screenshot(path=str(SHOTS / f"{engine}-01-editor-saved-with-note.png"))
        note(notes, f"{engine}: the save with a version note became v2 and the note field cleared for the next checkpoint")

        # ---- the history on the project page shows the name
        pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
        pa.get_by_role("button", name="版本").click()
        dialog = pa.get_by_role("dialog", name="模板版本")
        expect(dialog.locator("tr[data-version='2']")).to_contain_text("合同定稿（法务已审）")
        expect(dialog.locator("tr[data-version='1']")).to_contain_text("在软件内创建")
        pa.screenshot(path=str(SHOTS / f"{engine}-02-history-shows-note.png"))
        note(notes, f"{engine}: the template history lists v2 under the name given at save time")
        pa.keyboard.press("Escape")

        # ---- a save without a note keeps the default wording; the API shows both
        pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
        expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
        ed = body_editor(pa)
        ed.locator("p", has_text="定稿内容").click()
        pa.keyboard.press("End")
        pa.keyboard.type(" 再改")
        pa.get_by_role("button", name="保存模板").click()
        expect(pa.get_by_text(re.compile("已保存（2 个字段，v3）"))).to_be_visible(timeout=15000)
        versions = {v["version_no"]: v["note"] for v in pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()["versions"]}
        assert versions[2] == "合同定稿（法务已审）" and versions[3] == "在编辑器中保存", versions
        note(notes, f"{engine}: a save without a note keeps the default wording; the API lists v2 named and v3 default")

        # ---- the phone: the top bar with the note field stays within the screen
        b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
        pb = b.new_page()
        pb.context.add_cookies(a.cookies())
        pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
        expect(pb.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
        expect(pb.locator("[data-testid=version-note]")).to_be_visible()
        w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
        assert w[0] <= w[1] + 1, f"editor top bar scrolls sideways on a phone with the note field ({w})"
        pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-topbar.png"))
        note(notes, f"{engine}: on a 390 px phone the top bar keeps the note field and does not scroll sideways")
        b.close()
        assert not violations, violations
        return notes
    except Exception:
        try:
            pa.screenshot(path=str(SHOTS / f"{engine}-fail.png"))
        except Exception:
            pass
        raise


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
