"""Synwevo's own screens name capabilities, not the components underneath: a real session walks
the Docs capability matrix, the About page, the home page, a project's generate dialog and
preview, and a finished job, in all three languages, and reads every visible text and every
tooltip. The only place a component name may appear is the About page's licence table, where
the attribution has to stay true.

    .venv/bin/python services/identity/scripts/ui_e2e_component_names.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import build_personal_project, enter_docs

expect.set_options(timeout=15000)
NAMES = re.compile(r"(?<![A-Za-z0-9_])(LibreOffice|soffice|PyMuPDF|MuPDF|pdfium|pypdfium2|Tesseract|Cloudflare|Turnstile|ClamAV|clamd|xlrd|openpyxl|python-docx|python-pptx|Pillow|uvicorn|Starlette|FastAPI)(?![A-Za-z0-9_])", re.I)
LANGS = ("zh-Hans", "zh-Hant", "en")
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "component-names"


def switch_language(page, lang: str) -> None:
    """A signed-in person's language is the account's, so `?lang=` alone does not change a page;
    the language menu does (and syncs the account, which later pages then follow)."""
    menu = page.get_by_role("combobox", name=re.compile("^(语言|語言|Language)$")).first
    if menu.input_value() != lang:
        menu.select_option(lang)
    page.wait_for_function(f"() => document.documentElement.lang === '{lang}'", timeout=10000)
    page.wait_for_timeout(800)                       # the account sync is debounced


def visible_text(page) -> str:
    return page.evaluate("() => document.body.innerText")


def tooltips(page) -> list[str]:
    return page.evaluate("() => [...document.querySelectorAll('[title]')].map(e => e.getAttribute('title') || '')")


def assert_clean(where: str, texts: list[str]) -> None:
    bad = sorted({m.group(1) for t in texts for m in NAMES.finditer(t or "")})
    assert not bad, f"{where}: component names visible: {bad}"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    who = f"names.{tag}@example.test"
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **(ctx_extra or {}))
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    register_and_activate(page, accounts, mail_dir, who, f"Names {tag}")
    sign_in(page, accounts, who)
    r = page.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")})
    assert r.ok, r.text()[:200]
    me = page.request.get(f"{accounts}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    enter_docs(page, docs)
    pid = build_personal_project(page, ctx, docs, personal, f"命名检查 {tag}")

    for lang in LANGS:
        # the capability matrix: every cell is server prose translated by the shell
        page.goto(f"{docs}/capabilities")
        expect(page.locator("table.tbl")).to_be_visible(timeout=20000)
        switch_language(page, lang)
        expect(page.locator("table.tbl")).to_be_visible(timeout=20000)
        text = visible_text(page)
        assert_clean(f"capabilities[{lang}]", [text] + tooltips(page))
        assert re.search(r"排版引擎|layout engine", text, re.I), f"capabilities[{lang}] does not name the layout capability"
        page.screenshot(path=str(SHOTS / f"{engine}-{lang}-capabilities.png"))
        # the About page: the licence table keeps real names, the product prose around it does not
        page.goto(f"{docs}/about")
        expect(page.locator("table")).to_be_visible(timeout=20000)
        assert page.evaluate("() => document.documentElement.lang") == lang
        table = page.locator("table").inner_text()
        assert "PyMuPDF" in table or "pdfium" in table.lower(), f"about[{lang}]: the attribution table lost its component names"
        outside = page.evaluate("() => { const b = document.body.cloneNode(true); b.querySelectorAll('table').forEach(t => t.remove()); return b.innerText; }")
        assert_clean(f"about[{lang}] outside the licence table", [outside] + tooltips(page))
        # home
        page.goto(f"{docs}/")
        expect(page.get_by_text(f"命名检查 {tag}")).to_be_visible()
        assert page.evaluate("() => document.documentElement.lang") == lang
        assert_clean(f"home[{lang}]", [visible_text(page)] + tooltips(page))
    notes.append(f"{engine}: capability matrix, About page prose and home are free of component names in zh-Hans / zh-Hant / en; the licence table still attributes the PDF engine")

    # the project: preview badge, generate dialog and the finished job's verification tooltip
    page.goto(f"{docs}/p/{pid}")
    switch_language(page, "zh-Hans")
    expect(page.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    assert_clean("project", [visible_text(page)] + tooltips(page))
    page.get_by_role("button", name="👁 预览").click()
    expect(page.locator(".pane.right p.doc-p", has_text="张三")).to_be_visible(timeout=20000)
    assert_clean("preview", [visible_text(page)] + tooltips(page))
    page.get_by_role("button", name="返回模板").click()
    page.get_by_role("button", name="▶ 生成").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("有效样本")).to_be_visible(timeout=20000)
    assert_clean("generate dialog", [dlg.inner_text()] + tooltips(page))
    assert re.search(r"排版引擎", dlg.inner_text()), "the generate dialog no longer explains the layout option"
    page.screenshot(path=str(SHOTS / f"{engine}-generate-dialog.png"))
    dlg.get_by_role("button", name=re.compile("开始生成 2 个文件")).click()
    page.wait_for_url(re.compile(r"/jobs/j_"))
    expect(page.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
    titles = tooltips(page)
    assert any("重新打开" in t for t in titles), "the verification badge lost its tooltip"
    assert_clean("job page", [visible_text(page)] + titles)
    for lang in ("zh-Hant", "en"):
        switch_language(page, lang)
        expect(page.locator(".statbox").first).to_be_visible(timeout=20000)
        assert_clean(f"job page[{lang}]", [visible_text(page)] + tooltips(page))
        page.screenshot(path=str(SHOTS / f"{engine}-{lang}-job.png"))
    notes.append(f"{engine}: project, preview, generate dialog and the finished job (three languages, tooltips included) name readers and engines by capability only")
    assert not violations, violations
    ctx.close()
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
