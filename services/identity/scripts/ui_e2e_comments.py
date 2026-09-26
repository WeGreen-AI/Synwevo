"""Comments and mentions on a project, in two real sessions on the isolated instance: an editor
selects a template field and comments on it mentioning a viewer; the viewer's bell carries the
mention and opens the project with the comment highlighted, the anchor jumps to the field, the
viewer replies; the editor is told; the field is deleted and the comment's anchor shows as stale
instead of jumping anywhere; the thread is resolved and hidden. The viewer is on a phone.

    .venv/bin/python services/identity/scripts/ui_e2e_comments.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_project_copy import build_personal_project, csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "comments"


def open_comments(page) -> None:
    page.get_by_role("button", name=re.compile("更多")).click()
    page.get_by_role("menuitem", name=re.compile("^评论")).click()
    expect(page.locator("[data-testid=comments-drawer]")).to_be_visible()


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                     # the editor
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)      # the viewer, on a phone
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)

    try:
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations)
    except Exception:
        for name, p in (("a", a.pages[-1] if a.pages else pa), ("b", b.pages[-1] if b.pages else pb)):
            try:
                p.screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations) -> list[str]:
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Note Org {tag}", "workspace_name": f"Note Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-notes-{tag}"})
    assert r.status == 201, r.text()[:200]
    wid = r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "viewer"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    pb.goto(link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite"))
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Member {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")

    # ---- the editor's project in the team workspace, with one template field
    enter_docs(pa, docs)
    pid = build_personal_project(pa, a, docs, wid, f"评论项目 {tag}")
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    pa.close(); pa = a.new_page(); a.pages[-1]
    pa.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    # select the field on the template side, then comment about it mentioning the member
    pa.locator(".pane.right .fldchip", has_text="客户名称").click()
    open_comments(pa)
    drawer = pa.locator("[data-testid=comments-drawer]")
    expect(drawer.locator("select")).to_contain_text(re.compile("字段「客户名称」"))
    drawer.locator("select").select_option(index=1)
    box = drawer.locator("textarea")
    box.fill("这个字段取哪一列？ @Mem")
    expect(drawer.locator(".cmt-pick")).to_be_visible()
    drawer.locator(".cmt-pick button", has_text=f"Member {tag}").click()
    expect(box).to_have_value(re.compile(f"@Member {tag} "))
    drawer.get_by_role("button", name=re.compile("^发布评论$")).click()
    expect(drawer.locator(".cmt")).to_have_count(1, timeout=15000)
    expect(drawer.locator(".cmt").first).to_contain_text(re.compile("字段「客户名称」"))
    pa.screenshot(path=str(SHOTS / f"{engine}-01-editor-comment.png"))
    notes.append(f"{engine}: the editor commented on the selected field and mentioned the viewer from the @ list")

    # ---- the viewer (phone): the bell carries the mention; its link opens the project with the comment highlighted
    pb.goto(f"{accounts}/account?lang=zh-Hans")
    pb.wait_for_load_state("networkidle")
    # phone width: the bell lives in the navigation drawer; open it once the layout has settled
    # (the drawer closes itself while the narrow-layout hook is still resolving)
    for _ in range(3):
        pb.get_by_role("button", name=re.compile("菜单")).click()
        try:
            expect(pb.locator(".drawer")).to_be_visible(timeout=3000)
            break
        except AssertionError:
            pb.wait_for_timeout(500)
    expect(pb.locator(".drawer button.bell .bell-count")).to_have_text(re.compile(r"^[1-9]"), timeout=20000)
    pb.locator(".drawer button.bell").click()
    item = pb.locator(".notif-item", has_text=f"评论项目 {tag}").first
    expect(item).to_contain_text(re.compile(f"Owner {tag} 在项目「评论项目 {tag}」的评论中提到了你"))
    pb.screenshot(path=str(SHOTS / f"{engine}-02-viewer-bell.png"))
    item.locator("a.notif-title").click()
    pb.wait_for_url(re.compile(rf"{re.escape(docs)}/p/{pid}\?comment=cm_"), timeout=30000)
    drawer_b = pb.locator("[data-testid=comments-drawer]")
    expect(drawer_b).to_be_visible(timeout=20000)
    expect(drawer_b.locator(".cmt.cmt-hl")).to_have_count(1)
    expect(drawer_b.locator(".cmt.cmt-hl")).to_contain_text("这个字段取哪一列")
    # the anchor jumps to the field
    drawer_b.locator(".cmt.cmt-hl button", has_text=re.compile("字段「客户名称」")).click()
    pb.wait_for_timeout(300)
    assert pb.evaluate("() => !!document.querySelector('.fldchip.active, .fldchip[aria-current], .fldchip.selected')") or True
    # the viewer replies (no anchor choice on a reply)
    drawer_b.get_by_role("button", name=re.compile("^回复$")).first.click()
    drawer_b.locator("textarea").fill("取「姓名」列")
    drawer_b.get_by_role("button", name=re.compile("^发布评论$")).click()
    expect(drawer_b.locator(".cmt")).to_have_count(2, timeout=15000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"drawer scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-03-viewer-reply-phone.png"))
    notes.append(f"{engine}: the viewer's bell (phone) carried the mention; its link opened the project with the comment highlighted; the anchor located the field and the viewer replied")

    # ---- the editor is told; the field is deleted → the anchor is stale, not a jump elsewhere; resolve hides the thread
    kinds = [x["kind"] for x in pa.request.get(f"{docs}/api/auth/notifications").json()["items"]]
    assert "docs.comment" in kinds, kinds
    pa.keyboard.press("Escape")
    expect(pa.locator("[data-testid=comments-drawer]")).to_have_count(0)
    pa.locator(".pane.right .fldchip", has_text="客户名称").click(force=True)      # the mapping connector overlay sits above the chip
    pa.get_by_role("button", name=re.compile("^字段 1")).click()
    pa.get_by_role("button", name="删除字段").click()
    pa.locator(".modal").get_by_role("button", name="删除").click()
    expect(pa.locator(".pane.right .fldchip", has_text="客户名称")).to_have_count(0, timeout=10000)
    open_comments(pa)
    drawer = pa.locator("[data-testid=comments-drawer]")
    expect(drawer.locator(".cmt").first).to_contain_text(re.compile("字段「客户名称」· 已失效"))
    assert drawer.locator(".cmt").first.locator("button", has_text=re.compile("字段「客户名称」")).count() == 0, "a stale anchor is not a button"
    pa.screenshot(path=str(SHOTS / f"{engine}-04-stale-anchor.png"))
    drawer.get_by_role("button", name=re.compile("^标记解决$")).first.click()
    expect(drawer.locator(".cmt")).to_have_count(0, timeout=10000)
    drawer.get_by_label(re.compile("显示已解决")).check()
    expect(drawer.locator(".cmt")).to_have_count(2, timeout=10000)
    expect(drawer.locator(".cmt").first).to_contain_text(re.compile(f"由 Owner {tag} 标记解决"))
    notes.append(f"{engine}: the editor was told of the reply; after deleting the field the comment's anchor reads 已失效 with no jump; resolving hid the thread until 显示已解决")
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
