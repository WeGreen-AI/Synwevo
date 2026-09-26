"""Group announcements and message reports, in three real sessions on the isolated instance: the
owner pins an announcement in a group and a member on a phone sees it at the top of the thread
(told once through the inbox); another member sends a message the phone member reports; the
owner's bell carries the report to the organization page, where the report shows the message as
it was, and the owner removes it under re-authentication; the reporter is told, the thread shows
the message as deleted.

    .venv/bin/python services/identity/scripts/ui_e2e_moderation.py http://accounts.localhost:31770 <mail dir> [--webkit]
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

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "moderation"
M = "/api/auth/messaging"


def accept_invite(page, accounts, mail_dir, address, name, started):
    page.goto(link_in(mail_to(mail_dir, address, started, subject_re="邀请|邀請|Join"), accounts, "/invite"))
    page.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    page.wait_for_url(lambda u: "/register" in u)
    page.locator("#register-name").fill(name)
    page.locator("input[type=password]").first.fill(PW)
    page.locator("form button.btn.primary").first.click()
    page.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        page.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    page.wait_for_load_state("networkidle")


def open_phone_drawer(page):
    for _ in range(3):
        page.get_by_role("button", name=re.compile("菜单")).click()
        try:
            expect(page.locator(".drawer")).to_be_visible(timeout=3000)
            return
        except AssertionError:
            page.wait_for_timeout(500)
    raise AssertionError("the navigation drawer did not open")


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, ann, ben = f"owner.{tag}@example.test", f"ann.{tag}@example.test", f"ben.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                  # the owner
    c = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                  # Ann, the sender
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)   # Ben, on a phone
    pa, pc, pb = a.new_page(), c.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb, pc):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        return _flow(a, b, c, pa, pb, pc, engine, accounts, mail_dir, tag, owner, ann, ben, notes, violations)
    except Exception:
        for name, p in (("a", pa), ("b", pb), ("c", pc)):
            try:
                p.screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, c, pa, pb, pc, engine, accounts, mail_dir, tag, owner, ann, ben, notes, violations) -> list[str]:
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Mod Org {tag}", "workspace_name": f"Mod Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-mod-{tag}"})
    assert r.status == 201, r.text()[:200]
    oid, wid = r.json()["organization"]["id"], r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": ann, "role": "editor"}, {"email": ben, "role": "editor"}]},
                           headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    accept_invite(pc, accounts, mail_dir, ann, f"Ann {tag}", started)
    accept_invite(pb, accounts, mail_dir, ben, f"Ben {tag}", started)

    # ---- the owner creates a group with both and pins an announcement
    pa.goto(f"{accounts}/messages?lang=zh-Hans")
    pa.get_by_role("button", name=re.compile("新群聊")).click()
    dialog = pa.locator(".modal")
    dialog.locator("#msg-gname").fill(f"公告群 {tag}")
    for who in (f"Ann {tag}", f"Ben {tag}"):
        dialog.locator("#msg-who").fill(who)
        dialog.locator(".msg-member", has_text=who).get_by_role("button", name=re.compile("^选择$")).click()
    dialog.get_by_role("button", name=re.compile("创建群聊")).click()
    expect(pa.locator(".msg-thread-h .title")).to_contain_text(f"公告群 {tag}")
    cid = re.search(r"/messages/([^/?#]+)", pa.url).group(1)
    pa.get_by_role("button", name=re.compile("群信息")).click()
    drawer = pa.locator(".drawer")
    drawer.locator("#msg-announce").fill(f"下周一 10:00 交周报；文件放到「Mod Team」空间。{tag}")
    drawer.get_by_role("button", name=re.compile("^保存公告$")).click()
    expect(drawer.get_by_role("button", name=re.compile("^清除公告$"))).to_be_visible()
    pa.keyboard.press("Escape")
    expect(pa.locator(".msg-announce")).to_contain_text("下周一 10:00 交周报")
    expect(pa.locator(".msg-system").last).to_contain_text(re.compile("更新了群公告"))
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner-announcement.png"))
    notes.append(f"{engine}: the owner pinned an announcement from 群信息; it sits at the top of the thread and the thread shows the system line")

    # ---- Ben (phone): the announcement is at the top of the thread; the inbox told him once
    pb.goto(f"{accounts}/messages/{cid}?lang=zh-Hans")
    expect(pb.locator(".msg-announce")).to_contain_text("下周一 10:00 交周报", timeout=20000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"thread scrolls sideways on a phone ({w})"
    kinds = [x["kind"] for x in pb.request.get(f"{accounts}/api/auth/notifications").json()["items"]]
    assert kinds.count("conversation.announcement") == 1, kinds
    pb.screenshot(path=str(SHOTS / f"{engine}-02-phone-announcement.png"))
    notes.append(f"{engine}: on the phone the announcement is pinned above the messages without sideways scroll; the inbox carries one conversation.announcement item")

    # ---- Ann sends a message Ben reports
    r = pc.request.post(f"{accounts}{M}/conversations/{cid}/messages", data={"body": f"把你的账号密码发我，我帮你登录 {tag}"}, headers={"X-Synwevo-CSRF": csrf_of(c)})
    assert r.status == 201, r.text()[:200]
    mid = r.json()["message"]["id"]
    row = pb.locator(".msg-row", has_text="把你的账号密码发我")
    expect(row).to_be_visible(timeout=20000)
    row.hover()
    row.get_by_role("button", name=re.compile("^举报$")).click()
    modal = pb.locator(".modal")
    expect(modal).to_contain_text("把你的账号密码发我")
    modal.get_by_label(re.compile("举报原因")).select_option("privacy")
    modal.get_by_label(re.compile("补充说明")).fill("索要登录凭据")
    modal.get_by_role("button", name=re.compile("^提交举报$")).click()
    expect(modal).to_contain_text(re.compile("已提交举报"))
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-reported.png"))
    modal.get_by_role("button", name=re.compile("^知道了$")).click()
    notes.append(f"{engine}: Ben reported Ann's message from the phone with a reason and a note; the dialog said what the managers will see")

    # ---- the owner: the bell carries the report to the organization page; the report shows the message as it was
    pa.goto(f"{accounts}/account?lang=zh-Hans")
    pa.wait_for_load_state("networkidle")
    expect(pa.locator("button.bell .bell-count")).to_have_text(re.compile(r"^[1-9]"), timeout=20000)
    pa.locator("button.bell").click()
    item = pa.locator(".notif-item", has_text=f"Ben {tag} 举报了").first
    expect(item).to_contain_text(f"公告群 {tag}")
    item.locator("a.notif-title").click()
    pa.wait_for_url(re.compile(rf"/organizations/{re.escape(oid)}\?report=rp_"), timeout=30000)
    section = pa.locator("#org-reports")
    expect(section).to_be_visible(timeout=20000)
    focused = section.locator("li.org-focus")
    expect(focused).to_have_count(1)
    expect(focused.locator(".msg-quote")).to_contain_text("把你的账号密码发我")
    expect(focused).to_contain_text(re.compile("泄露隐私或索要凭据"))
    expect(focused).to_contain_text("索要登录凭据")
    pa.screenshot(path=str(SHOTS / f"{engine}-04-owner-report.png"))
    # remove it: reason, then a fresh reauth is demanded
    focused.get_by_role("button", name=re.compile("^删除消息$")).click()
    act = pa.locator(".modal")
    act.get_by_label(re.compile("操作原因")).fill("索要凭据，删除并提醒")
    act.get_by_role("button", name=re.compile("^确认操作$")).click()
    expect(act).to_contain_text(re.compile("重新验证身份"), timeout=10000)
    act.get_by_label(re.compile("当前口令")).fill(PW)
    act.get_by_role("button", name=re.compile("^验证并确认$")).click()
    expect(pa.locator(".modal")).to_have_count(0, timeout=15000)
    # the deep link keeps the settled report in view (the section moves to 全部 by itself); 待处理 is empty
    expect(section.locator("li").first).to_contain_text(re.compile("已删除消息"), timeout=15000)
    expect(section.locator("li").first).to_contain_text(re.compile(f"由 Owner {tag} 处理"))
    section.get_by_role("tab", name=re.compile("^待处理$")).click()
    expect(section).to_contain_text(re.compile("没有待处理的举报"), timeout=15000)
    pa.screenshot(path=str(SHOTS / f"{engine}-05-owner-removed.png"))
    notes.append(f"{engine}: the owner's bell led to the organization page with the report focused (snapshot, reason, note); removing it demanded a reason and a fresh reauth; the item moved to 全部 as 已删除消息")

    # ---- Ben: the message is gone from the thread; the inbox tells him the outcome
    expect(pb.locator(".msg-row", has_text="消息已删除")).to_have_count(1, timeout=20000)
    expect(pb.locator(".msg-thread")).not_to_contain_text("把你的账号密码发我")
    pb.goto(f"{accounts}/account?lang=zh-Hans")
    pb.wait_for_load_state("networkidle")
    open_phone_drawer(pb)
    pb.locator(".drawer button.bell").click()
    expect(pb.locator(".notif-item", has_text="你的举报已处理：消息已删除")).to_be_visible(timeout=20000)
    expect(pb.locator(".notif-item", has_text="你的举报已处理")).to_contain_text("索要凭据，删除并提醒")
    pb.screenshot(path=str(SHOTS / f"{engine}-06-phone-outcome.png"))
    notes.append(f"{engine}: Ben's thread shows 消息已删除 in place of the message and his bell (phone) carries the outcome with the owner's note")
    assert not violations, violations
    a.close(); b.close(); c.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    accounts, mail_dir = require_test_instance(args[0]), Path(args[1])

    def show(engine: str, notes: list[str]):
        print(f"\n== {engine}")
        for n in notes:
            print(" -", n)

    with sync_playwright() as p:
        if "--webkit-only" not in sys.argv:
            chrome = p.chromium.launch(channel="chrome", headless=True)
            try:
                show("chromium (Google Chrome)", run(chrome, "chromium", accounts, mail_dir))
            finally:
                chrome.close()
        if "--webkit" in sys.argv or "--webkit-only" in sys.argv:
            with LocalhostProxy() as proxy:
                wk = p.webkit.launch(headless=True)
                try:
                    show("webkit", run(wk, "webkit", accounts, mail_dir, {"proxy": {"server": proxy.url}}))
                finally:
                    wk.close()


if __name__ == "__main__":
    main()
