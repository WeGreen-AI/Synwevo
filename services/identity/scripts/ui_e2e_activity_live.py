"""Traceability on the project page (CI-50): comments arrive live through the project room, and the
activity record can be filtered by member and object, marks what happened since the last look, and
jumps to the object a row changed.

Two real accounts on the isolated instance, one organization workspace. The owner keeps the project
page open on a desktop; the editor works from a phone.

* the owner has the comments drawer open; the editor posts a comment — it appears in the owner's
  drawer within seconds (no 30 s poll) and the owner is told who wrote, without a page reload;
* the activity record lists the comment attributed to the editor; filtering by member and by
  object narrows the list; a second visit shows "since you last looked"; clicking the comment row
  opens the comments drawer on that comment.

    .venv/bin/python services/identity/scripts/ui_e2e_activity_live.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_collab import SEED, note
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "activity-live"
CSV = "客户,金额\n甲公司,100\n乙公司,200\n"


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
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Activity Org {tag}", "workspace_name": f"Activity Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-activity-{tag}"})
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

    # ---- a project with one source and a template, in the team workspace
    enter_docs(pa, docs)
    ha = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/projects", data={"name": f"活动项目 {tag}"}, headers=ha)
    assert r.ok, r.text()[:200]
    pid = r.json()["id"]
    r = pa.request.post(f"{docs}/api/projects/{pid}/sources", headers=ha, multipart={"files": {"name": "客户.csv", "mimeType": "text/csv", "buffer": CSV.encode()}})
    assert r.ok, r.text()[:300]
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"活动合同 {tag}", "project_id": pid}, headers=ha)
    assert r.ok, r.text()[:300]
    enter_docs(pb, docs)
    hb = {"X-Synwevo-CSRF": csrf_for(b, docs), "X-Synwevo-Workspace": wid}
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok

    # ---- the owner's project page, comments drawer open; the editor comments from the phone (same session, API)
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.locator("[data-testid=project-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    pa.get_by_role("button", name="打开命令面板").click()
    pa.get_by_role("option", name=re.compile("^评论")).first.click()
    drawer = pa.get_by_role("dialog", name=re.compile("评论"))
    expect(drawer).to_be_visible()
    t0 = time.time()
    r = pb.request.post(f"{docs}/api/projects/{pid}/comments", data={"body": f"这一列的金额单位是元吗？{tag}"}, headers=hb)
    assert r.status == 201, r.text()[:300]
    cid = r.json()["id"]
    expect(drawer.locator(f"[data-comment='{cid}']")).to_be_visible(timeout=12000)
    arrived = time.time() - t0
    assert arrived < 12, arrived
    expect(pa.locator(".toasts")).to_contain_text(re.compile(f"Editor {tag} 添加了评论"), timeout=10000)
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner-sees-comment-live.png"))
    note(notes, f"{engine}: the editor's comment appeared in the owner's open comments drawer in {arrived:.1f} s (no 30 s poll) and the owner was told who wrote it")
    pa.keyboard.press("Escape")
    expect(drawer).not_to_be_visible()

    # ---- the activity record: attributed, filterable, and a row jumps to its comment
    pa.get_by_role("button", name="活动记录").click()
    act = pa.locator("[data-testid=project-activity]")
    expect(act).to_be_visible()
    expect(act.locator(".collab-log li[data-kind='comment.add']")).to_have_count(1, timeout=10000)
    expect(act.locator(".collab-log li[data-kind='comment.add']")).to_contain_text(f"Editor {tag}")
    total = act.locator(".collab-log li[data-kind]").count()
    assert total >= 3, total                                          # the source, the template and the comment at least
    pa.locator("[data-testid=activity-who]").select_option(label=f"Editor {tag}")
    expect(act.locator(".collab-log li[data-kind]")).to_have_count(1)
    pa.locator("[data-testid=activity-who]").select_option(value="")
    pa.locator("[data-testid=activity-group]").select_option(value="sources")
    expect(act.locator(".collab-log li[data-kind='source.add']")).to_have_count(1)
    expect(act.locator(".collab-log li[data-kind='comment.add']")).to_have_count(0)
    pa.locator("[data-testid=activity-group]").select_option(value="all")
    pa.screenshot(path=str(SHOTS / f"{engine}-02-activity-filters.png"))
    note(notes, f"{engine}: the activity record attributed the comment to the editor; filtering by member left one row, by object (files) left the source row only")
    act.locator(".collab-log li[data-kind='comment.add'] button").click()
    drawer = pa.get_by_role("dialog", name=re.compile("评论"))
    expect(drawer).to_be_visible(timeout=10000)
    expect(drawer.locator(f"[data-comment='{cid}']")).to_be_visible()
    note(notes, f"{engine}: clicking the comment's activity row opened the comments drawer on that comment")
    pa.keyboard.press("Escape")

    # ---- since you last looked: the editor changes something after the owner's visit; the next visit marks it
    r = pb.request.post(f"{docs}/api/projects/{pid}/comments", data={"body": f"再补一句 {tag}", "parent_id": cid}, headers=hb)
    assert r.status == 201, r.text()[:300]
    expect(pa.locator(".toasts")).to_contain_text(re.compile(f"Editor {tag} 回复了评论"), timeout=10000)
    pa.get_by_role("button", name="活动记录").click()
    act = pa.locator("[data-testid=project-activity]")
    expect(act.locator("[data-testid=activity-fresh]")).to_have_text(re.compile("上次查看后 1 条"), timeout=10000)
    expect(act.locator(".collab-log li.fresh[data-kind='comment.reply']")).to_have_count(1)
    expect(act.locator(".collab-log li.collab-seen-line")).to_have_count(1)
    pa.screenshot(path=str(SHOTS / f"{engine}-03-activity-since-last-look.png"))
    note(notes, f"{engine}: on the next visit the record counted one change since the owner last looked, marked the reply and drew the line under it")
    pa.keyboard.press("Escape")

    # ---- the editor's phone: the activity record is usable at 390 px
    pb.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pb.locator("[data-testid=project-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    pb.get_by_role("button", name="活动记录").click()
    actb = pb.locator("[data-testid=project-activity]")
    expect(actb.locator(".collab-log li[data-kind='comment.add']")).to_have_count(1, timeout=10000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"activity drawer scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-04-editor-phone-activity.png"))
    note(notes, f"{engine}: the editor's phone shows the same record without sideways scrolling")

    assert not violations, violations
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
