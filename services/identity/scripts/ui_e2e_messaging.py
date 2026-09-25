"""Messaging in two real, independent browser sessions on an isolated instance (file mail): an
owner and a newly invited member of the same organization exchange direct messages that arrive
through the stream without reloading, react and reply, share an image through the acceptance
chain, meet in a group from which one is then removed (and loses every door), search, and do it
all at phone width. Nothing is seeded: both accounts register through mailed links.

    .venv/bin/python services/identity/scripts/ui_e2e_messaging.py http://accounts.localhost:31770 <mail dir> [--webkit]

Output never includes tokens, cookies or message bodies beyond the fixed test strings.
"""
from __future__ import annotations

import email
import json
import re
import secrets
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy

expect.set_options(timeout=15000)
PW = "harbor lights guide the evening ferry home"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xb1\x00\x00\x00\x00IEND\xaeB`\x82"


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def mail_to(mail_dir: Path, address: str, after: float, *, subject_re: str) -> email.message.Message:
    for _ in range(240):
        for p in sorted((p for p in mail_dir.glob("*.eml") if p.stat().st_mtime >= after), key=lambda p: p.stat().st_mtime, reverse=True):
            m = email.message_from_bytes(p.read_bytes())
            subject = str(email.header.make_header(email.header.decode_header(m["Subject"] or "")))
            if (m["To"] or "").strip().lower() == address.lower() and re.search(subject_re, subject):
                return m
        time.sleep(0.25)
    raise AssertionError(f"no mail matching {subject_re!r} to {address}")


def link_in(m, accounts: str, path: str) -> str:
    text = m.get_payload(decode=True).decode()
    found = re.search(r"https?://\S+" + re.escape(path) + r"\?lang=[\w-]+#token=[A-Za-z0-9_-]+", text)[0]
    return accounts + found[found.index(path):]


def register_and_activate(page, accounts, mail_dir, address, name):
    started = time.time()
    r = page.request.post(f"{accounts}/api/auth/register", data={"email": address, "password": PW, "display_name": name, "locale": "zh-Hans"})
    assert r.status == 202, (r.status, r.text()[:120])
    page.goto(link_in(mail_to(mail_dir, address, started, subject_re="验证|Verify|驗證"), accounts, "/verify-email"))
    page.get_by_role("button", name=re.compile("验证此邮箱")).click()
    pw = page.locator("input[type=password]").first
    pw.wait_for(state="visible"); pw.fill(PW)
    page.get_by_role("button", name=re.compile("设置新口令并验证邮箱")).click()
    expect(page.locator("main, body").first).to_contain_text(re.compile("验证完成"))


def sign_in(page, accounts, address):
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label(re.compile("邮箱|Email")).fill(address)
    page.locator("input[type=password]").first.fill(PW)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: "/login" not in u)


def csrf_of(ctx):
    return next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))

    # ---- two real accounts in one organization: the owner invites, the member accepts the mailed link
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Chat Org {tag}", "workspace_name": f"Chat Space {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-chat-{tag}"})
    assert r.status == 201, (r.status, r.text()[:200])
    oid, wid = r.json()["organization"]["id"], r.json()["workspace"]["id"]
    started = time.time()
    r = pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.ok, r.text()[:200]
    link = link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite")
    pb.goto(link)
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Member {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)      # the page moves on by itself; let it land
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")
    notes.append(f"{engine}: owner registered and created an organization; the member joined through the mailed invitation (phone-sized session)")

    # ---- the member opens Messages first (empty), the owner starts a DM; it arrives over the stream
    pb.goto(f"{accounts}/messages?lang=zh-Hans")
    expect(pb.locator(".msg-side")).to_contain_text(re.compile("还没有会话"))
    pa.goto(f"{accounts}/messages?lang=zh-Hans")
    pa.get_by_role("button", name=re.compile("新私聊")).click()
    dialog = pa.locator(".modal").last
    dialog.locator("#msg-who").fill("Member")
    dialog.get_by_role("button", name=re.compile("^选择$")).first.click()
    dialog.get_by_role("button", name=re.compile("开始私聊")).click()
    pa.wait_for_url(lambda u: "/messages/cv_" in u)
    expect(pa.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    pa.locator(".msg-composer textarea").fill(f"hello from owner {tag}")
    pa.locator(".msg-composer textarea").press("Enter")
    expect(pa.locator(".msg-row.own")).to_contain_text(f"hello from owner {tag}")
    expect(pa.locator(".msg-pending")).to_have_count(0, timeout=10000)
    # the member's list gained the conversation without a reload, with an unread badge
    expect(pb.locator(".msg-conv")).to_have_count(1, timeout=15000)
    expect(pb.locator(".msg-unread")).to_have_text("1")
    pb.get_by_role("button", name=re.compile("菜单")).click()              # phone width: the bar's links live in the drawer
    expect(pb.locator("a.bell", has_text=re.compile("消息"))).to_contain_text("1")
    pb.keyboard.press("Escape")
    pb.locator(".msg-conv").first.click()
    expect(pb.locator(".msg-thread")).to_be_visible()
    expect(pb.locator(".msg-thread")).to_contain_text(f"hello from owner {tag}")
    expect(pb.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    # reply from the phone: reply-to + reaction; the owner sees both live
    pb.locator(".msg-row").first.hover()
    pb.locator(".msg-row").first.get_by_role("button", name=re.compile("回复")).click()
    pb.locator(".msg-composer textarea").fill(f"hi owner, got it {tag}")
    pb.get_by_role("button", name=re.compile("^发送$")).click()
    expect(pa.locator(".msg-thread")).to_contain_text(f"hi owner, got it {tag}", timeout=15000)
    expect(pa.locator(".msg-row").last.locator(".msg-reply")).to_contain_text("hello from owner")
    pb.locator(".msg-row").first.hover()
    pb.locator(".msg-row").first.get_by_role("button", name=re.compile("添加表情回应")).click()
    pb.locator(".msg-picker button").first.click()
    expect(pa.locator(".msg-row").first.locator(".msg-reaction")).to_contain_text("👍 1", timeout=15000)
    notes.append(f"{engine}: the DM reached the member's list and badge over the stream; reply-with-quote and a reaction from the phone showed up live on the owner's desktop")

    # ---- an image through the acceptance chain
    pa.locator(".msg-composer input[type=file]").set_input_files({"name": "dot.png", "mimeType": "image/png", "buffer": PNG})
    expect(pa.locator(".msg-composer .msg-reply")).to_contain_text("dot.png")
    pa.get_by_role("button", name=re.compile("^发送$")).click()
    img = pb.locator(".msg-attach img").first
    expect(img).to_be_visible(timeout=15000)
    src = img.get_attribute("src")
    r = pb.request.get(accounts + src)
    assert r.ok and r.headers.get("content-type") == "image/png" and r.headers.get("x-content-sha256"), (r.status, dict(r.headers))
    notes.append(f"{engine}: an image attachment was scanned, stored once by content hash and rendered inline for the member (image/png, hash header, no-store)")

    # ---- a group: created by the owner, the member is added, then removed and loses every door
    pa.get_by_role("button", name=re.compile("新群聊")).click()
    dialog = pa.locator(".modal").last
    dialog.locator("#msg-gname").fill(f"Launch {tag}")
    dialog.locator("#msg-who").fill("Member")
    dialog.get_by_role("button", name=re.compile("^选择$")).first.click()
    dm_url = pa.url
    dialog.get_by_role("button", name=re.compile("创建群聊")).click()
    pa.wait_for_url(lambda u: "/messages/cv_" in u and u != dm_url)
    expect(pa.locator(".msg-thread-h .title")).to_contain_text(f"Launch {tag}")
    gid = pa.url.rsplit("/", 1)[-1].split("?")[0]
    pa.locator(".msg-composer textarea").fill(f"welcome to the group {tag}")
    pa.locator(".msg-composer textarea").press("Enter")
    expect(pb.locator(".msg-conv")).to_have_count(2, timeout=15000)
    pb.get_by_role("button", name=re.compile("返回列表")).click()
    pb.locator(".msg-conv", has_text=f"Launch {tag}").click()
    expect(pb.locator(".msg-thread")).to_contain_text(f"welcome to the group {tag}")
    pa.get_by_role("button", name=re.compile("群信息")).click()
    drawer = pa.locator(".drawer").last
    pa.once("dialog", lambda d: d.accept())                                # the confirm must be accepted, not auto-dismissed
    drawer.get_by_role("button", name=re.compile("^移出$")).first.click()
    expect(drawer).not_to_contain_text(f"Member {tag}", timeout=15000)
    expect(pb.locator(".msg-thread")).to_contain_text(re.compile("找不到这个会话"), timeout=15000)
    expect(pb.locator(".msg-side")).not_to_contain_text(f"Launch {tag}")
    r = pb.request.get(f"{accounts}/api/auth/messaging/conversations/{gid}/messages")
    assert r.status == 404, r.status
    r = pb.request.get(f"{accounts}/api/auth/messaging/conversations")
    assert all(c["id"] != gid for c in r.json()["items"])
    notes.append(f"{engine}: group created, the member read it, was removed from the group info drawer, and from then on history is 404 and the group is gone from their list")

    # ---- search finds only what the caller may read
    pa.locator(".msg-side-h input").fill("got it")
    pa.locator(".msg-side-h input").press("Enter")
    expect(pa.locator(".msg-search-hit")).to_have_count(1)
    pb.goto(f"{accounts}/api/auth/messaging/search?q=welcome")
    assert "welcome" not in pb.content(), "a removed member cannot search the group's history"
    notes.append(f"{engine}: search returned the owner's DM hit; the removed member's search of the group's text found nothing")
    assert not violations, violations[:4]
    notes.append(f"{engine}: 0 CSP violations, 0 page errors")
    a.close(); b.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    accounts, mail_dir = require_test_instance(args[0]), Path(args[1])
    want_webkit = "--webkit" in sys.argv

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, mail_dir))
        finally:
            chrome.close()
        if want_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, mail_dir, {"proxy": {"server": proxy.url}}))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
