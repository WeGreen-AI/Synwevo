"""The platform inbox in a real browser: the bell on the account pages and on the portal shows one
list, items render in the reader's language, marking read is the reader's own state, and the
preferences section saves. Runs against an isolated, test-marked instance; the reader must have at
least one notification (running `ui_e2e_onboarding.py` first gives the inviter an
`invitation.accepted` item).

    .venv/bin/python services/identity/scripts/ui_e2e_inbox.py http://accounts.localhost:31770 http://portal.localhost:31769 <email> <password> [--webkit] [--docs=http://docweave.localhost:31765]

Output never includes tokens or cookies.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy

expect.set_options(timeout=10000)


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def run(browser: Browser, engine: str, accounts: str, portal: str, email: str, password: str, ctx_extra: dict | None = None, docs: str | None = None) -> list[str]:
    notes: list[str] = []
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    page.goto(f"{accounts}/login")
    page.get_by_label(re.compile("邮箱|電子郵件|Email")).fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    page.goto(f"{accounts}/account")
    page.wait_for_selector("#devices", timeout=15000)
    me = page.request.get(f"{accounts}/api/auth/me").json()
    count = page.request.get(f"{accounts}/api/auth/notifications/unread-count").json()
    listing = page.request.get(f"{accounts}/api/auth/notifications").json()
    assert listing["items"], "the reader needs at least one notification (run the onboarding loop first)"
    notes.append(f"{engine}: {me['email']} ({me.get('locale')}) has {len(listing['items'])} notifications, {count['unread_count']} unread")

    # the bell (inside the phone menu drawer on a narrow viewport)
    menu = page.get_by_role("button", name=re.compile("菜单|選單|Menu"))
    if menu.count():
        menu.click()
    bell = page.locator("button.bell")
    expect(bell.first).to_be_visible()
    badge = bell.first.locator(".bell-count")
    if count["unread_count"] > 0:
        expect(badge).to_be_visible()                       # the count is fetched when the bell mounts
        assert badge.inner_text() == str(count["unread_count"]), (badge.inner_text(), count)
    else:
        expect(badge).to_have_count(0)
    bell.first.click()
    drawer = page.locator(".drawer[role=dialog]").last
    expect(drawer).to_be_visible()
    expect(drawer.locator(".notif-item").first).to_be_visible()
    first_title = drawer.locator(".notif-item .notif-title").first.inner_text()
    expected_first = listing["items"][0]["title"]
    assert first_title == expected_first, (first_title, expected_first)
    notes.append(f"{engine}: drawer lists the same first item the API renders for this reader: {first_title[:60]}")
    # filters
    drawer.get_by_role("tab", name=re.compile("未读|未讀|Unread")).click()
    page.wait_for_timeout(300)
    drawer.get_by_role("tab", name=re.compile("全部|All")).click()
    # mark the first item read through the UI and see the count follow
    unread_before = count["unread_count"]
    item = drawer.locator(".notif-item").first
    if "unread" in (item.get_attribute("class") or ""):
        item.get_by_role("button", name=re.compile("标为已读|標為已讀|Mark as read")).click()
        page.wait_for_function(f"() => fetch('/api/auth/notifications/unread-count').then(r => r.json()).then(j => j.unread_count === {max(unread_before - 1, 0)})", timeout=10000)
        notes.append(f"{engine}: marking one item read took the unread count from {unread_before} to {max(unread_before - 1, 0)}")
        item.get_by_role("button", name=re.compile("标为未读|標為未讀|Mark as unread")).click()
        page.wait_for_function(f"() => fetch('/api/auth/notifications/unread-count').then(r => r.json()).then(j => j.unread_count === {unread_before})", timeout=10000)
        notes.append(f"{engine}: and back to {unread_before} after marking it unread")
    # read-all only covers what the list was opened with
    drawer.get_by_role("button", name=re.compile("全部标为已读|全部標為已讀|Mark all as read")).click()
    page.wait_for_function("() => fetch('/api/auth/notifications/unread-count').then(r => r.json()).then(j => j.unread_count === 0)", timeout=10000)
    notes.append(f"{engine}: mark-all-read → 0 unread; the items stay listed")
    # restore: mark them unread again so the run is repeatable
    ids = [x["id"] for x in listing["items"][:5]]
    csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")
    r = page.request.post(f"{accounts}/api/auth/notifications/mark", data={"ids": ids, "read": False}, headers={"X-Synwevo-CSRF": csrf})
    assert r.ok
    page.keyboard.press("Escape")

    # preferences on the account page
    page.goto(f"{accounts}/account#notifications")
    sect = page.locator("#notifications")
    expect(sect).to_be_visible()
    expect(sect.locator("table")).to_be_visible()
    sel = sect.locator("select").nth(1)          # the second category row's e-mail mode (organization)
    before = sel.input_value()
    target = "digest" if before != "digest" else "immediate"
    sel.select_option(target)
    page.wait_for_function(f"() => fetch('/api/auth/notifications/preferences').then(r => r.json()).then(p => p.categories.organization.email === '{target}')", timeout=10000)
    sel.select_option(before)
    page.wait_for_function(f"() => fetch('/api/auth/notifications/preferences').then(r => r.json()).then(p => p.categories.organization.email === '{before}')", timeout=10000)
    security_row = sect.locator("tbody tr").first
    # every channel box of the security row (in-app, push — the row gained a push column with Web Push) stays locked on
    boxes = security_row.locator("input[type=checkbox]").all()
    assert boxes and security_row.locator("select").is_disabled() and all(b.is_disabled() for b in boxes), "security stays locked on"
    notes.append(f"{engine}: preferences section saves (organization e-mail {before}→{target}→{before}); the security row is locked on")

    # the portal shows the same count from the same table (through single sign-on: the account
    # session already exists, so the portal's sign-in returns without a second password)
    page.goto(f"{portal}/")
    page.wait_for_load_state("networkidle")
    if not page.locator("button.bell").count():
        entry = page.get_by_role("link", name=re.compile("^(登录|登入|Sign in)")).or_(page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)")))
        if entry.count():
            entry.first.click()
            page.wait_for_url(lambda u: u.startswith(portal) and "/auth/" not in u, timeout=20000)
            page.wait_for_load_state("networkidle")
    pbell = page.locator("button.bell")
    if pbell.count():
        expect(pbell.first).to_be_visible()
        portal_count = page.request.get(f"{portal}/api/auth/notifications/unread-count").json()
        notes.append(f"{engine}: portal bell present; portal origin reports {portal_count['unread_count']} unread from the shared table")
    else:
        notes.append(f"{engine}: the portal's own top bar has no bell yet (it does not use PlatformNav) — not asserted")
    # Synwevo Docs: its origin signs in through the account session (single sign-on) and its
    # project list carries the platform bar, so the same inbox shows there from the same table.
    if docs:
        page.goto(f"{docs}/")
        page.wait_for_url(lambda u: u.startswith(docs) and "/auth/" not in u, timeout=30000)
        page.wait_for_load_state("networkidle")
        dbell = page.locator("button.bell")
        expect(dbell.first).to_be_visible(timeout=15000)
        docs_count = page.request.get(f"{docs}/api/auth/notifications/unread-count").json()
        acct_count = page.request.get(f"{accounts}/api/auth/notifications/unread-count").json()
        assert docs_count["unread_count"] == acct_count["unread_count"], (docs_count, acct_count)
        dbell.first.click()
        ddrawer = page.locator(".drawer[role=dialog]").last
        expect(ddrawer).to_be_visible()
        assert ddrawer.locator(".notif-item .notif-title").first.inner_text() == first_title
        page.keyboard.press("Escape")
        notes.append(f"{engine}: Synwevo Docs (after single sign-on) shows the same bell, count {docs_count['unread_count']} and the same first item from its own origin")
    assert not violations, violations[:4]
    notes.append(f"{engine}: 0 CSP violations, 0 page errors")
    ctx.close()
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 4:
        raise SystemExit(__doc__)
    accounts, portal = require_test_instance(args[0]), args[1].rstrip("/")
    email, password = args[2], args[3]
    want_webkit = "--webkit" in sys.argv
    docs = next((a.split("=", 1)[1].rstrip("/") for a in sys.argv[1:] if a.startswith("--docs=")), None)

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, portal, email, password, docs=docs))
        finally:
            chrome.close()
        if want_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, portal, email, password, {"proxy": {"server": proxy.url}}, docs=docs))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
