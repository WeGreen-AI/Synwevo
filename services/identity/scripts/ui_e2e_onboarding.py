"""The platform's own loop in a real browser: register → verify the mailed link → onboarding →
be invited into a team workspace → accept the mailed invitation → sign out.

Runs against an isolated, test-marked instance whose mail goes to a directory
(`SYNWEVO_MAIL_MODE=file`); the script reads the `.eml` files there for the two links, exactly as
a person would read their inbox. Chromium is the installed Google Chrome; `--webkit` also runs
Playwright's WebKit build through the local `*.localhost` proxy. Every page in this loop is a
lazily loaded route of the account shell, so this is also the regression for the route split.

    .venv/bin/python services/identity/scripts/ui_e2e_onboarding.py http://accounts.localhost:31770 \
        <mail dir> <inviter email> <inviter password> <inviter's team workspace id> [--webkit]

Output never includes tokens, codes or cookies.
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

from playwright.sync_api import Browser, Page, expect, sync_playwright

from localhost_proxy import LocalhostProxy  # sibling module; this directory heads the import path when run as a script

expect.set_options(timeout=10000)
LINK = re.compile(r"https?://[^\s<>\"]+")


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def mailed_link(mail_dir: Path, to: str, path: str, since: float, timeout: float = 60) -> str:
    """The first link containing `path` in a message to `to` written after `since`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for f in sorted(mail_dir.glob("*.eml"), key=lambda f: f.stat().st_mtime, reverse=True):
            if f.stat().st_mtime < since - 1:
                continue
            m = email.message_from_bytes(f.read_bytes())
            if to.lower() not in (m.get("To") or "").lower():
                continue
            for part in m.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", "replace")
                    for link in LINK.findall(body):
                        if path in link:
                            return link
        time.sleep(0.5)
    raise AssertionError(f"no mail with a {path} link for {to} within {timeout}s")


def text_of(page: Page) -> str:
    return page.locator("body").inner_text()[:600].replace("\n", " | ")


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, inviter: str, inviter_pw: str, team_wid: str,
        ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    ctx_extra = ctx_extra or {}
    tag = secrets.token_hex(3)
    newcomer = f"newcomer.{tag}@example.test"
    password = f"orchard signal {tag} lantern meadow 2026"

    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **ctx_extra)
    page = ctx.new_page()
    errors: list[str] = []
    api_log: list[tuple] = []
    def log_response(r):
        if "/api/" not in r.url:
            return
        try:
            body = r.text()[:160] if r.status >= 400 else ""
        except Exception:      # the context may already be closing when a late response lands
            body = ""
        api_log.append((r.request.method, r.url.split("/api/")[-1].split("?")[0], r.status, body))
    page.on("response", log_response)
    page.on("pageerror", lambda e: errors.append(str(e)[:160]))
    page.on("console", lambda m: errors.append("CSP: " + m.text[:160]) if "Content Security Policy" in m.text else None)

    # 1. register (open registration on the isolated instance)
    t0 = time.time()
    page.goto(f"{accounts}/register?lang=zh-Hans")
    page.locator("#register-name").fill("Newcomer " + tag)
    page.locator("#register-email").fill(newcomer)
    pw_fields = page.locator("input[type=password]")
    for i in range(pw_fields.count()):
        pw_fields.nth(i).fill(password)
    if page.locator(".challenge-field").count():
        # The deployment challenges registration: wait for the Turnstile widget (Cloudflare's
        # iframe) to hand the page a token before submitting, as a person would see it turn green.
        page.wait_for_selector('.challenge-field[data-status="solved"]', timeout=30000)
        notes.append(f"{engine}: human verification solved before registering")
    page.get_by_role("button", name=re.compile("创建账户")).click()
    page.wait_for_function("() => document.body.innerText.includes('邮件') || location.pathname !== '/register'", timeout=15000)
    notes.append(f"{engine}: registered {newcomer} → {page.url.split(accounts)[-1].split('?')[0]}")

    # 2. the verification mail, then the link it carries
    link = mailed_link(mail_dir, newcomer, "/verify-email", t0)
    page.goto(link.replace("http://accounts.localhost:8770", accounts) if accounts not in link else link)
    # The page offers one button first; the server answers that the mailbox holder must set a
    # final password ("此前的口令…将失效"), and the page then shows that form. Handle both stages.
    page.wait_for_selector("button:has-text('验证此邮箱'), input[type=password], .flow-success", timeout=20000)
    if page.get_by_role("button", name=re.compile("验证此邮箱")).count():
        page.get_by_role("button", name=re.compile("验证此邮箱")).click()
        page.wait_for_selector("input[type=password], .flow-success", timeout=20000)
    if page.locator("input[type=password]").count():
        field = page.locator("input[type=password]").first
        field.wait_for(state="visible")
        field.fill(password + " final")
        expect(field).to_have_value(password + " final")
        page.get_by_role("button", name=re.compile("设置新口令并验证邮箱")).click()
    try:
        page.wait_for_selector(".flow-success", timeout=20000)
    except Exception:
        print("[diag] verification did not complete:", page.url.split(accounts)[-1], "|", page.locator("body").inner_text()[:600].replace("\n", " | "), "| api:", api_log[-4:], flush=True)
        raise
    page.locator("a.btn.primary").first.click()      # the one continue link the page offers
    page.wait_for_load_state("networkidle")
    # A pending account is activated by the mailbox owner choosing its credential, and merely
    # verifying never signs anybody in (auth_routes.verify_email: a pre-registrant who knows the
    # old password must not be promoted). So the page's "登录并继续" leads to /login?next=…;
    # sign in with the final password, as the person would.
    if "/login" in page.url:
        page.get_by_label(re.compile("邮箱|電子郵件|Email")).fill(newcomer)
        page.locator("input[type=password]").first.fill(password + " final")
        page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
        page.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    notes.append(f"{engine}: e-mail verified, signed in with the chosen password → {page.url.split(accounts)[-1].split('?')[0]}")

    # 3. onboarding: a personal space
    if "/onboarding" not in page.url:
        page.goto(f"{accounts}/onboarding")
    try:
        page.wait_for_selector("article.org-card, section.org-section", timeout=15000)
    except Exception:
        print("[diag] onboarding page did not render:", page.url.split(accounts)[-1], "|", text_of(page), "| api:", api_log[-6:], flush=True)
        raise
    card = page.locator("article.org-card").filter(has_text=re.compile("个人使用|個人使用|Personal"))
    if card.count():
        # The button creates the personal workspace, selects it and hands over to the portal
        # (Onboarding.enter → window.location.assign(portal)), so the proof is the navigation.
        card.get_by_role("button").first.click()
        page.wait_for_url(lambda u: not u.startswith(accounts) or "/onboarding" not in u, timeout=20000)
        page.wait_for_load_state("domcontentloaded")
        notes.append(f"{engine}: personal workspace chosen → handed over to {page.url.split('//')[-1].split('/')[0]}")
    me = page.request.get(f"{accounts}/api/auth/me").json()
    assert me["status"] == "active" and me["email_verified"], me
    assert any(w["kind"] == "personal" for w in me["workspaces"]), me["workspaces"]
    notes.append(f"{engine}: onboarding done, personal workspace present, status {me['status']}")

    # 4. the inviter invites the newcomer from the members page (a second, independent context)
    inv = browser.new_context(viewport={"width": 1280, "height": 720}, locale="zh-CN", **ctx_extra)
    ip = inv.new_page()
    ip.goto(f"{accounts}/login?lang=zh-Hans")
    ip.get_by_label("邮箱").fill(inviter)
    ip.get_by_label("口令", exact=True).fill(inviter_pw)
    ip.get_by_role("button", name="登录", exact=True).click()
    ip.wait_for_url(lambda u: "/login" not in u, timeout=15000)
    t1 = time.time()
    ip.goto(f"{accounts}/account/workspaces/{team_wid}/members?tab=invites")
    ip.wait_for_selector("table.gov:visible", timeout=15000)
    ip.locator("button.btn.primary").filter(has_text=re.compile("邀请|邀請|Invite")).first.click()
    dlg = ip.locator(".modal, [role=dialog]").first
    expect(dlg).to_be_visible()
    box = dlg.locator("input[type=email], input[type=text], textarea").first
    box.fill(newcomer)
    dlg.get_by_role("button", name=re.compile("发送|發送|Send|邀请|邀請")).last.click()
    # The batch result dialog says per address what happened; a refused invitation (member
    # already, or the workspace's 50-per-24h budget spent) is a failure of this run, not noise.
    result = ip.locator(".modal, [role=dialog]").filter(has_text=re.compile("邀请操作结果|邀請操作結果|Invitation results"))
    expect(result.first).to_be_visible(timeout=15000)
    summary = result.first.inner_text()[:400].replace("\n", " | ")
    assert not re.search("失败|失敗|failed|Failed", summary), ("invitation refused: " + summary, [x for x in api_log if "invit" in x[1]][-3:])
    close = result.first.get_by_role("button", name=re.compile("关闭|關閉|Close|完成|确定|確定"))
    if close.count():
        close.first.click()
    expect(ip.locator("table.gov:visible")).to_contain_text(newcomer, timeout=15000)
    notes.append(f"{engine}: {inviter} invited {newcomer} through the members page")

    # 5. the newcomer opens the mailed invitation and joins
    ilink = mailed_link(mail_dir, newcomer, "/invite", t1)
    page.goto(ilink.replace("http://accounts.localhost:8770", accounts) if accounts not in ilink else ilink)
    page.wait_for_load_state("networkidle")
    join = page.get_by_role("button", name=re.compile("加入|確認|确认|Join|Accept"))
    expect(join.first).to_be_visible()
    join.first.click()
    page.wait_for_function("() => document.body.innerText.includes('已加入') || document.body.innerText.includes('已加入') || location.pathname === '/onboarding' || location.pathname === '/account'", timeout=20000)
    me = page.request.get(f"{accounts}/api/auth/me").json()
    assert any(w["id"] == team_wid for w in me["workspaces"]), ("not a member after accepting", [w["id"] for w in me["workspaces"]])
    notes.append(f"{engine}: invitation accepted; now a member of {team_wid}")

    # 6. sign out from the account page (drawer on a phone)
    page.goto(f"{accounts}/account")
    page.wait_for_selector("#devices, section.card", timeout=15000)
    menu = page.get_by_role("button", name=re.compile("菜单|選單|Menu"))
    if menu.count():
        menu.click()
    page.get_by_role("button", name=re.compile("退出登录|登出|Sign out")).first.click()
    page.wait_for_url(lambda u: "/logged-out" in u or "/login" in u, timeout=15000)
    assert page.request.get(f"{accounts}/api/auth/me").status == 401
    notes.append(f"{engine}: signed out → {page.url.split(accounts)[-1].split('?')[0]}; /api/auth/me 401")
    assert not errors, ("page errors / CSP violations", errors[:5])
    notes.append(f"{engine}: 0 page errors, 0 CSP violations")
    inv.close(); ctx.close()
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 5:
        raise SystemExit(__doc__)
    accounts = require_test_instance(args[0])
    mail_dir = Path(args[1])
    inviter, inviter_pw, team_wid = args[2], args[3], args[4]
    want_webkit = "--webkit" in sys.argv

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, mail_dir, inviter, inviter_pw, team_wid))
        finally:
            chrome.close()
        if want_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, mail_dir, inviter, inviter_pw, team_wid, {"proxy": {"server": proxy.url}}))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
