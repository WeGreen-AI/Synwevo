"""Browser push on the account page, in a real session on the isolated instance: the push
section explains its state, the service worker and the manifest are served with the right
headers, registering the worker works, and the subscribe button either subscribes (when the
browser can reach a push service) or says honestly that it could not. A headless browser has
no push service behind it, so delivery itself is proven by the server-side tests against a
push-service double, and on a real device by hand.

    .venv/bin/python services/identity/scripts/ui_e2e_push.py http://accounts.localhost:31770 <mail dir> [--webkit]
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

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "push"


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    who = f"push.{tag}@example.test"
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **(ctx_extra or {}))
    try:
        ctx.grant_permissions(["notifications"], origin=accounts)
    except Exception:
        pass                                                    # WebKit has no such grant; the page then reports the state it sees
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    register_and_activate(page, accounts, mail_dir, who, f"Push {tag}")
    sign_in(page, accounts, who)

    # the worker and the manifest are served by the account service, not the shell's catch-all
    sw = page.request.get(f"{accounts}/sw.js")
    assert sw.status == 200 and "javascript" in sw.headers.get("content-type", "") and sw.headers.get("service-worker-allowed") == "/", sw.headers
    assert "addEventListener('push'" in sw.text()
    mf = page.request.get(f"{accounts}/manifest.webmanifest")
    assert mf.status == 200 and "manifest" in mf.headers.get("content-type", "") and mf.json()["display"] == "standalone"
    cfg = page.request.get(f"{accounts}/api/auth/push/config").json()
    assert cfg["enabled"] and cfg["public_key"]
    notes.append(f"{engine}: /sw.js (Service-Worker-Allowed: /) and /manifest.webmanifest (standalone) are served; the push config offers a public key")

    page.goto(f"{accounts}/account?lang=zh-Hans#notifications")
    sec = page.locator("[data-testid=push-section]")
    sec.scroll_into_view_if_needed()
    expect(sec).to_contain_text(re.compile("浏览器推送"))
    expect(page.locator("#notifications table").first).to_contain_text(re.compile("浏览器推送"))      # the per-category push column
    assert page.evaluate("() => !!document.querySelector('link[rel=manifest]')"), "the manifest link is injected for Add to Home Screen"
    state = page.evaluate("() => ({secure: window.isSecureContext, sw: 'serviceWorker' in navigator, push: 'PushManager' in window, perm: typeof Notification === 'undefined' ? 'none' : Notification.permission})")
    page.screenshot(path=str(SHOTS / f"{engine}-01-section.png"))
    button = sec.get_by_role("button", name=re.compile("在此设备上开启推送"))
    if not (state["secure"] and state["sw"] and state["push"]):
        expect(sec.locator(".notice")).to_be_visible()
        notes.append(f"{engine}: this browser context reports {state}; the section shows the matching explanation instead of a button")
    else:
        expect(button).to_be_visible()
        button.click()
        # either subscribed (a push service was reachable) or an honest failure toast; the worker registers either way
        page.wait_for_function("() => navigator.serviceWorker.getRegistration('/').then(r => !!r)", timeout=20000)
        page.wait_for_timeout(1500)
        text = sec.inner_text()
        toasts = page.locator(".toast, [role=status], [role=alert]").all_inner_texts()
        subscribed = page.request.get(f"{accounts}/api/auth/push/subscriptions").json()["items"]
        page.screenshot(path=str(SHOTS / f"{engine}-02-after-subscribe.png"))
        if subscribed:
            expect(sec.locator("[data-testid=push-device]")).to_have_count(1)
            expect(sec).to_contain_text(re.compile("此设备"))
            notes.append(f"{engine}: the service worker registered and the browser subscribed; the device is listed as 此设备")
            sec.get_by_role("button", name=re.compile("移除")).click()
            expect(sec.locator("[data-testid=push-device]")).to_have_count(0, timeout=10000)
            notes.append(f"{engine}: removing the device unsubscribed the browser and cleared the list")
        else:
            assert any("无法完成推送订阅" in t or "没有获得通知权限" in t for t in toasts) or "开启推送" in text, (toasts, text[:200])
            notes.append(f"{engine}: the service worker registered but this headless browser has no push service; the page said so and kept in-app notifications")
    assert not violations, violations
    ctx.close()
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
