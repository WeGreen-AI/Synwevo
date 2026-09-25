"""Docs edit safety and outcome notifications in a real session on an isolated instance: the same
person has a project open in two tabs; the first tab saves the sample rule, the second tab's stale
save is refused and the screen reloads instead of overwriting; then a generation job ends and the
platform bell inside Docs shows the outcome with a link that opens the job page.

    .venv/bin/python services/identity/scripts/ui_e2e_docs_upgrades.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_messaging import csrf_of, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import build_personal_project, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "docs-upgrades"


def confirm_rule(page) -> None:
    page.locator("button.step", has_text="定义样本").click()
    dlg = page.locator(".modal")
    expect(dlg.get_by_text("识别样本")).to_be_visible()
    dlg.get_by_role("button", name="确认样本规则").click()


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    who = f"edit.{tag}@example.test"
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **(ctx_extra or {}))
    a = ctx.new_page()
    violations: list[str] = []

    def watch(p):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    watch(a)
    SHOTS.mkdir(parents=True, exist_ok=True)
    register_and_activate(a, accounts, mail_dir, who, f"Editor {tag}")
    sign_in(a, accounts, who)
    assert a.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(ctx)}).ok
    me = a.request.get(f"{accounts}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    enter_docs(a, docs)
    pid = build_personal_project(a, ctx, docs, personal, f"两个窗口 {tag}")

    b = ctx.new_page(); watch(b)
    try:
        return _flow(a, b, ctx, engine, accounts, docs, personal, pid, tag, notes, violations)
    except Exception:
        for name, p in (("a", a), ("b", b)):
            try:
                p.screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, ctx, engine, accounts, docs, personal, pid, tag, notes, violations) -> list[str]:
    # ---- two tabs on the same project; the second one's save is stale
    a.goto(f"{docs}/p/{pid}"); b.goto(f"{docs}/p/{pid}")
    for p in (a, b):
        expect(p.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    before = a.request.get(f"{docs}/api/projects/{pid}", headers={"X-Synwevo-Workspace": personal}).json()["settings_rev"]
    confirm_rule(a)
    expect(a.locator(".modal")).to_have_count(0)
    expect(a.get_by_text("样本规则已保存")).to_be_visible()
    after_a = a.request.get(f"{docs}/api/projects/{pid}", headers={"X-Synwevo-Workspace": personal}).json()["settings_rev"]
    assert after_a == before + 1, (before, after_a)
    confirm_rule(b)
    expect(b.get_by_text(re.compile("内容已在其他窗口或被其他人修改"))).to_be_visible(timeout=10000)      # the toast
    expect(b.locator(".modal .notice.err")).to_contain_text(re.compile("项目设置已在其他窗口或被其他人修改"))   # and the dialog says why
    b.screenshot(path=str(SHOTS / f"{engine}-01-stale-save.png"))
    after_b = b.request.get(f"{docs}/api/projects/{pid}", headers={"X-Synwevo-Workspace": personal}).json()["settings_rev"]
    assert after_b == after_a, "the stale save must not have written"
    # the screen reloaded behind the dialog; closing it and confirming again saves on the current revision
    b.locator(".modal").get_by_role("button", name="取消").click()
    expect(b.locator(".modal")).to_have_count(0, timeout=10000)
    confirm_rule(b)
    expect(b.get_by_text("样本规则已保存")).to_be_visible()
    assert b.request.get(f"{docs}/api/projects/{pid}", headers={"X-Synwevo-Workspace": personal}).json()["settings_rev"] == after_a + 1
    notes.append(f"{engine}: the second tab's stale rule save was refused and reloaded (revision unchanged); after the reload its save went through")

    # ---- a job ends: the platform bell inside Docs carries the outcome and opens the job page
    b.get_by_role("button", name="▶ 生成").click()
    gen = b.locator(".modal")
    expect(gen.get_by_text("有效样本")).to_be_visible(timeout=20000)
    gen.get_by_role("button", name=re.compile("开始生成 2 个文件")).click()
    b.wait_for_url(re.compile(r"/jobs/j_"))
    expect(b.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
    jid = b.url.rsplit("/", 1)[-1].split("?")[0]
    a.goto(f"{docs}/")
    expect(a.locator("button.bell .bell-count")).to_have_text(re.compile(r"^[1-9]"), timeout=20000)
    a.locator("button.bell").click()
    item = a.locator(".notif-item", has_text=f"两个窗口 {tag}").first
    expect(item).to_be_visible()
    expect(item).to_contain_text(re.compile("生成任务已完成"))
    expect(item).to_contain_text(re.compile("2 份成功，0 份失败"))
    a.screenshot(path=str(SHOTS / f"{engine}-02-job-notification.png"))
    item.locator("a.notif-title").click()
    a.wait_for_url(re.compile(rf"/p/{pid}/jobs/{jid}"), timeout=20000)
    expect(a.get_by_text("已完成", exact=True)).to_be_visible(timeout=20000)
    notes.append(f"{engine}: the job's end reached the bell inside Docs (title, counts) and its link opened the job page")
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
