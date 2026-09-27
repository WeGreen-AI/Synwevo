"""Institutional-mailbox registration in a real browser, on an isolated instance with file mail:
a CUHK-form address (numeric local part, four-label domain) registers, the "check your inbox" screen
names the sender and the gateway/quarantine guidance in all three languages, the written mail is
quoted-printable and carries the link, the link activates the account (mailbox owner sets the
password), the account signs in, and registering the same address again mails an "account already
exists" notice instead of leaving the person waiting. A trailing-dot address is a 400, not a crash.

    .venv/bin/python services/identity/scripts/ui_e2e_institutional_mail.py http://accounts.localhost:31770 <mail dir> [--webkit]

Output never includes tokens, cookies or the full mail body.
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
from ui_e2e_messaging import mail_text

expect.set_options(timeout=10000)
PW = "harbor lights guide the evening ferry home"
PW2 = "the mailbox owner picks a second long passphrase"


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def newest_mail_to(mail_dir: Path, address: str, after: float, *, with_token: bool) -> email.message.Message:
    """The newest .eml to `address` written after `after` — of the wanted kind (link mail or notice)."""
    for _ in range(200):          # the file-mode worker ticks every 15 s when idle
        cands = sorted((p for p in mail_dir.glob("*.eml") if p.stat().st_mtime >= after), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in cands:
            m = email.message_from_bytes(p.read_bytes())
            if (m["To"] or "").strip().lower() == address.lower() and ("token=" in mail_text(m)) == with_token:
                return m
        time.sleep(0.25)
    raise AssertionError(f"no {'link' if with_token else 'notice'} mail to {address} appeared in {mail_dir}")


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    address = f"1155{secrets.randbelow(900000) + 100000}@link.cuhk.edu.hk"
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))

    # ---- a malformed address is refused with a 400 (the validator used to raise)
    r = page.request.post(f"{accounts}/api/auth/register", data={"email": address + ".", "password": PW, "locale": "zh-Hans"})
    assert r.status == 400 and r.json()["error"] == "bad_email", (r.status, r.text()[:120])
    notes.append(f"{engine}: trailing-dot address → 400 bad_email (not a 500)")

    # ---- register with the CUHK-form address
    started = time.time()
    page.goto(f"{accounts}/register?lang=zh-Hans")
    page.locator("#register-email").fill(address)
    page.locator("#register-name").fill("Link Student")
    page.locator("input[type=password]").first.fill(PW)
    page.locator("form button.btn.primary").first.click()
    page.wait_for_url(lambda u: "/verify-email" in u, timeout=15000)
    body = page.locator("main, body").first
    expect(body).to_contain_text("Synwevo <synwevo@example.test>")
    expect(body).to_contain_text(re.compile("安全网关"))
    expect(body).to_contain_text(re.compile("最多发送 3 封"))
    expect(page.get_by_role("link", name=re.compile("换一个邮箱注册"))).to_be_visible()
    page.screenshot(path=str(mail_dir.parent / f"verify-email-{engine}-zh-Hans.png"))
    for lang, needle in (("en", "security gateway"), ("zh-Hant", "安全閘道")):
        page.goto(f"{accounts}/verify-email?lang={lang}")
        expect(page.locator("main, body").first).to_contain_text(re.compile(needle))
        expect(page.locator("main, body").first).to_contain_text("synwevo@example.test")
    notes.append(f"{engine}: registered {address[:4]}…@link.cuhk.edu.hk on a 390px viewport; the inbox screen names the sender and the gateway/quarantine guidance in zh-Hans, en and zh-Hant")

    # ---- the mail: quoted-printable, with the link
    m = newest_mail_to(mail_dir, address, started, with_token=True)
    assert m["Content-Transfer-Encoding"] == "quoted-printable", m["Content-Transfer-Encoding"]
    text = mail_text(m)
    link = re.search(r"https?://\S+/verify-email\?lang=zh-Hans#token=[A-Za-z0-9_-]+", text)[0]
    link = accounts + link[link.index("/verify-email"):]
    subject = str(email.header.make_header(email.header.decode_header(m["Subject"] or "")))
    notes.append(f"{engine}: the verification mail is quoted-printable UTF-8 with a fragment-carried link (subject: {subject})")

    # ---- opening the link does not verify; confirming with a new password does
    page.goto(link)
    expect(page.get_by_role("button", name=re.compile("验证此邮箱"))).to_be_visible()
    st = page.request.get(f"{accounts}/api/auth/state").json()
    page.get_by_role("button", name=re.compile("验证此邮箱")).click()
    pw = page.locator("input[type=password]").first
    pw.wait_for(state="visible", timeout=10000)
    pw.fill(PW2)
    page.get_by_role("button", name=re.compile("设置新口令并验证邮箱")).click()
    expect(page.locator("main, body").first).to_contain_text(re.compile("验证完成"))
    notes.append(f"{engine}: the link only landed; confirming with a mailbox-owner password activated the account")

    # ---- sign in with the institutional address and the new password, then out
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label(re.compile("邮箱|Email")).fill(address)
    page.locator("input[type=password]").first.fill(PW2)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: not re.search(r"/(login|auth/start|auth/callback|oidc/)", u), timeout=15000)
    me = page.request.get(f"{accounts}/api/auth/me").json()
    assert me["email"].lower() == address.lower() and me["status"] == "active", me
    csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")
    assert page.request.post(f"{accounts}/api/auth/logout", data={}, headers={"X-Synwevo-CSRF": csrf}).ok
    notes.append(f"{engine}: signed in with the institutional address (status active), signed out")

    # ---- registering the same address again: same screen, an "account exists" mail instead of silence
    started = time.time()
    page.goto(f"{accounts}/register?lang=zh-Hans")
    page.locator("#register-email").fill(address)
    page.locator("#register-name").fill("Someone Again")
    page.locator("input[type=password]").first.fill(PW)
    page.locator("form button.btn.primary").first.click()
    page.wait_for_url(lambda u: "/verify-email" in u, timeout=15000)
    m2 = newest_mail_to(mail_dir, address, started, with_token=False)
    subject = str(email.header.make_header(email.header.decode_header(m2["Subject"] or "")))
    assert "token=" not in mail_text(m2), "the notice must carry no token"
    assert re.search("已经存在|already exists|已經存在", subject), subject
    notes.append(f"{engine}: re-registering the now-active address showed the same screen and mailed an \"account already exists\" notice without any token")
    assert not violations, violations[:4]
    notes.append(f"{engine}: 0 CSP violations, 0 page errors")
    ctx.close()
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
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
