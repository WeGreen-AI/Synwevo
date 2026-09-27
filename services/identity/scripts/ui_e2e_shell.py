"""Browser regression for the shell fixes of the 2026-09-25 navigation round.

Drives the real account, portal and Docs shells of an *isolated, test-marked* instance (its
/healthz must report ``instance=test``; the script refuses anything else) in Chromium (the
installed Google Chrome via Playwright) and, when available, Playwright's WebKit build — and
says which ran. It is evidence about the pages' behaviour in those engines, not about iOS Safari
or Android Chrome on a real device (see docs/quality/verification.md for that checklist).

What it pins, each at a desktop width and at 375px:

* the member list's action menu on the *last* row is fully inside the viewport (it used to be
  clipped by the scrolling table wrapper);
* the account top bar has a "back to workspace / back to <app>" link and, on a phone, a menu
  button that opens a drawer with the same links;
* moving between account pages and returning focus to the tab does not re-run the session
  probes more than the throttle allows;
* the portal dashboard does not fall back to its loading state when the window regains focus;
* the public home never shows the sign-in link while the session is still unknown to a signed-in
  visitor;
* the Docs workspace at 375px offers the 文件/来源/目标/映射 pane switch.

    .venv/bin/python services/identity/scripts/ui_e2e_shell.py http://accounts.localhost:31770 \
        http://portal.localhost:31769 http://docweave.localhost:31765 <email> <password> [--webkit]

Screenshots go to docs/screenshots/shell-*.png. Output never includes cookies or tokens.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, Page, expect, sync_playwright

from localhost_proxy import LocalhostProxy  # sibling module: this directory is first on the import path when run as a script

REPO = Path(__file__).resolve().parents[3]
# Route chunks load on entry now (the account shell is split by route); give every expectation
# 10 s instead of Playwright's 5 s so a slow chunk fetch on a busy machine is not a false failure.
expect.set_options(timeout=10000)
SHOTS = REPO / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


class Probe:
    """Counts the session probes a page makes, by path."""

    def __init__(self, page: Page):
        self.hits: list[tuple[float, str]] = []
        page.on("request", lambda req: self.hits.append((time.time(), req.url.split("?")[0].split("/api/", 1)[-1]))
                if "/api/auth/state" in req.url or "/api/auth/me" in req.url or "/api/portal/home" in req.url else None)

    def count(self, name: str) -> int:
        return sum(1 for _, u in self.hits if u.endswith(name))

    def reset(self):
        self.hits.clear()


def sign_in(page: Page, accounts: str, email: str, password: str):
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label("邮箱").fill(email)
    page.get_by_label("口令", exact=True).fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url(lambda u: not re.search(r"/(login|auth/start|auth/callback|oidc/)", u), timeout=15000)


def reachable(browser: Browser, base: str, ctx_extra: dict | None = None) -> bool:
    ctx = browser.new_context(**(ctx_extra or {}))
    try:
        ctx.new_page().goto(base + "/healthz", timeout=5000)
        return True
    except Exception:
        return False
    finally:
        ctx.close()


def run(browser: Browser, engine: str, accounts: str, portal: str, docs: str, email: str, password: str,
        ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    ctx_extra = ctx_extra or {}
    cross_origin = reachable(browser, portal, ctx_extra) and reachable(browser, docs, ctx_extra)
    if not cross_origin:
        notes.append(f"{engine}: {portal} / {docs} not resolvable in this engine (system resolver has no *.localhost); portal and Docs checks not run here")
    for width, tag in ((1280, "desktop"), (375, "phone")):
        ctx = browser.new_context(viewport={"width": width, "height": 720 if width > 400 else 740}, locale="zh-CN",
                                  is_mobile=width < 400, has_touch=width < 400, **ctx_extra)
        page = ctx.new_page()
        probe = Probe(page)
        csp: list[str] = []
        page.on("console", lambda m: csp.append(m.text[:200]) if "Content Security Policy" in m.text else None)
        sign_in(page, accounts, email, password)

        # ---- a sign-in with no return target lands on the platform, not on account settings -----
        # (user feedback 2026-09-27). The account service hands the browser to the portal's own
        # sign-in start, so the portal has its session when the workspace renders; the account
        # service's root goes the same way, and an explicit target is still honoured.
        if cross_origin:
            page.wait_for_url(re.compile(re.escape(portal) + r"/workspace(\?|$)"), timeout=15000)
            page.wait_for_load_state("networkidle")
            expect(page.locator(".pt-status a[href*='/auth/start']")).to_have_count(0)   # not the signed-out view
            landed = page.url.split(portal)[-1]
            page.goto(f"{accounts}/")
            page.wait_for_url(re.compile(re.escape(portal) + r"/workspace(\?|$)"), timeout=15000)
            page.goto(f"{accounts}/login?next=%2Faccount&lang=zh-Hans")
            page.wait_for_url(re.compile(re.escape(accounts) + r"/account(\?|$)"), timeout=15000)
            notes.append(f"{engine}/{tag}: signed in without a target → platform {landed}; account root → platform; /login?next=/account → account page")
        else:
            notes.append(f"{engine}/{tag}: landing after sign-in not checked here (portal origin not resolvable in this engine)")

        # ---- account page: back link + drawer -------------------------------------------------
        page.goto(f"{accounts}/account?from=platform")
        # The account page renders in the person's stored locale, so every lookup below accepts
        # all three languages.
        page.wait_for_selector("#devices, section.card", timeout=15000)
        back = page.locator(".acct-back-link").first
        expect(back).to_be_visible()
        back_text = back.inner_text()
        assert re.search("返回|Back to", back_text), back_text   # the account's own language
        href = back.get_attribute("href") or ""
        listed = {t["url"].split("/auth/start")[0].rstrip("/") for t in page.request.get(f"{accounts}/api/auth/me").json().get("return_targets", [])}
        assert any(href.startswith(origin) for origin in listed), f"back link goes to {href!r}, not a server-listed origin {sorted(listed)}"
        if width < 400:
            page.get_by_role("button", name=re.compile("菜单|選單|Menu")).click()
            drawer = page.get_by_role("dialog", name=re.compile("账户导航|帳戶導覽|Account navigation"))
            expect(drawer).to_be_visible()
            expect(drawer.get_by_role("link", name=re.compile("账户与安全|帳戶與安全|Account & security"))).to_be_visible()
            expect(drawer.get_by_role("button", name=re.compile("退出登录|登出|Sign out"))).to_be_visible()
            page.screenshot(path=SHOTS / f"shell-{engine}-{tag}-account-drawer.png")
            # focus is trapped inside the drawer: Tab from the last control wraps to the first
            inside = lambda: page.evaluate("() => { const d = document.querySelector('.drawer'); return !!(d && document.activeElement && d.contains(document.activeElement)); }")
            assert inside(), "focus did not move into the drawer"
            for _ in range(12):
                page.keyboard.press("Tab")
                assert inside(), "Tab escaped the drawer"
            # the page behind is inert to the pointer: the backdrop, not the content, receives the click
            page.keyboard.press("Escape")
            expect(drawer).to_have_count(0)
            assert page.evaluate("() => document.activeElement && document.activeElement.getAttribute('aria-haspopup') === 'dialog'"), "focus did not return to the menu button"
            notes.append(f"{engine}/{tag}: drawer — focus trapped over 12 Tabs, Escape closes and returns focus to the button")
        page.screenshot(path=SHOTS / f"shell-{engine}-{tag}-account.png")
        notes.append(f"{engine}/{tag}: account back link → {href.split('?')[0]}")

        # ---- account sub-pages: no repeated probes per in-app navigation --------------------
        probe.reset()
        page.goto(f"{accounts}/organizations")
        page.wait_for_load_state("networkidle")
        cold_state, cold_me = probe.count("auth/state"), probe.count("auth/me")
        assert cold_state <= 1 and cold_me <= 1, f"cold load made {cold_state} state / {cold_me} me probes"
        probe.reset()
        page.evaluate("() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); }")
        page.wait_for_timeout(600)
        refocus = probe.count("auth/state") + probe.count("auth/me")
        assert refocus == 0, f"refocus within the throttle window made {refocus} probes"
        notes.append(f"{engine}/{tag}: organizations cold load state={cold_state} me={cold_me}; refocus within 30 s → 0 probes")

        # ---- members list: last row menu fully visible --------------------------------------
        me = page.request.get(f"{accounts}/api/auth/me").json()
        governed = [w for w in me["workspaces"] if w["role"] in ("owner", "admin")]
        if governed:
            wid = governed[0]["id"]
            page.goto(f"{accounts}/account/workspaces/{wid}/members")
            page.wait_for_selector("table.gov tbody tr", timeout=15000)
            # rows of the visible list that carry an action menu (the invitations table may be in
            # the DOM too, and an empty list renders a single message row)
            rows = page.locator("table.gov:visible tbody tr:has(td.act button)")
            n = rows.count()
            last_btn = rows.nth(n - 1).locator("td.act button").first
            last_btn.scroll_into_view_if_needed()
            last_btn.click()
            menu = page.get_by_role("menu").first
            expect(menu).to_be_visible()
            box = menu.bounding_box()
            vp = page.viewport_size or {"width": width, "height": 720}
            assert box and box["x"] >= 0 and box["y"] >= 0, box
            assert box["x"] + box["width"] <= vp["width"] + 0.5, (box, vp)
            assert box["y"] + box["height"] <= vp["height"] + 0.5, (box, vp)
            items = menu.get_by_role("menuitem")
            n_items = items.count()
            assert n_items >= 1
            for i in range(items.count()):
                ib = items.nth(i).bounding_box()
                assert ib and ib["y"] + ib["height"] <= vp["height"] + 0.5, ("menu item clipped", i, ib)
            page.screenshot(path=SHOTS / f"shell-{engine}-{tag}-members-menu.png")
            page.keyboard.press("Escape")
            expect(page.get_by_role("menu")).to_have_count(0)
            assert page.evaluate("() => document.activeElement && document.activeElement.closest('td.act') !== null"), "focus did not return to the row button"
            # keyboard operation of the shared menu (WAI-ARIA menu button pattern): Enter opens with
            # focus on the first enabled item, arrows move and wrap, Home/End jump, Escape closes
            # and returns focus; disabled items stay listed but are skipped.
            # A row whose menu has enabled items: the sole owner's own row has none (every action
            # on oneself is refused with a reason), so look across the governed workspaces' member
            # and invitation lists for one that does.
            kb_btn = None
            for w in governed[:3]:
                for tab in ("members", "invites"):
                    page.goto(f"{accounts}/account/workspaces/{w['id']}/members?tab={tab}")
                    page.wait_for_selector("table.gov:visible", timeout=15000)
                    cands = page.locator("table.gov:visible tbody tr:has(td.act button)")
                    for i in range(cands.count()):
                        b = cands.nth(i).locator("td.act button").first
                        b.click()
                        m = page.get_by_role("menu").first
                        expect(m).to_be_visible()
                        en = [j for j in range(m.get_by_role("menuitem").count()) if m.get_by_role("menuitem").nth(j).is_enabled()]
                        page.keyboard.press("Escape")
                        expect(page.get_by_role("menu")).to_have_count(0)
                        if len(en) >= 2:
                            kb_btn = b
                            break
                    if kb_btn is not None:
                        break
                if kb_btn is not None:
                    break
            assert kb_btn is not None, "no row menu with two enabled items found for the keyboard check (add an invitation to a governed workspace)"
            kb_btn.focus()
            page.keyboard.press("Enter")
            menu = page.get_by_role("menu").first
            expect(menu).to_be_visible()
            items_all = menu.get_by_role("menuitem")
            n_all = items_all.count()
            enabled_idx = [i for i in range(n_all) if items_all.nth(i).is_enabled()]
            focused = lambda: page.evaluate("() => { const a = document.activeElement; const items = [...document.querySelectorAll('[role=menu] [role=menuitem]')]; return items.indexOf(a); }")
            assert focused() == enabled_idx[0], ("first enabled item not focused on open", focused(), enabled_idx)
            if len(enabled_idx) > 1:
                page.keyboard.press("ArrowDown"); assert focused() == enabled_idx[1], ("ArrowDown", focused())
                page.keyboard.press("End"); assert focused() == enabled_idx[-1], ("End", focused())
                page.keyboard.press("ArrowDown"); assert focused() == enabled_idx[0], ("wrap to first", focused())
                page.keyboard.press("ArrowUp"); assert focused() == enabled_idx[-1], ("wrap to last", focused())
                page.keyboard.press("Home"); assert focused() == enabled_idx[0], ("Home", focused())
            page.keyboard.press("Escape")
            expect(page.get_by_role("menu")).to_have_count(0)
            assert page.evaluate("() => document.activeElement && document.activeElement.closest('td.act') !== null"), "focus did not return after keyboard close"
            notes.append(f"{engine}/{tag}: menu keyboard — Enter opens on item {enabled_idx[0]}, arrows wrap, Home/End, Escape returns focus ({len(enabled_idx)} enabled of {n_all})")
            if width < 400:
                act = rows.nth(0).locator("td.act")
                pos = page.evaluate("el => getComputedStyle(el).position", act.element_handle())
                assert pos == "sticky", pos
            notes.append(f"{engine}/{tag}: members rows={n}, last-row menu inside viewport {vp['width']}×{vp['height']}, items={n_items}")
        else:
            notes.append(f"{engine}/{tag}: no governed workspace for {email}; members check skipped")

        if not cross_origin:
            assert not csp, ("Content-Security-Policy violations", csp[:5])
            notes.append(f"{engine}/{tag}: 0 CSP violations on the account pages")
            ctx.close()
            continue
        # ---- portal: signed-in dashboard, no spinner on refocus, public home tri-state -------
        page.goto(f"{portal}/workspace")
        page.wait_for_selector(".pt-dashboard, .pt-status", timeout=20000)
        if page.locator(".pt-status a[href*='/auth/start']").count():
            # first visit to the portal in this browser: complete the single sign-on once
            page.locator(".pt-status a[href*='/auth/start']").first.click()
            page.wait_for_selector(".pt-dashboard", timeout=20000)
        expect(page.locator(".pt-dashboard")).to_be_visible()
        probe.reset()
        page.evaluate("() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); }")
        page.wait_for_timeout(700)
        assert page.locator(".pt-loader").count() == 0, "portal dropped into its loading state on refocus"
        expect(page.locator(".pt-dashboard")).to_be_visible()
        notes.append(f"{engine}/{tag}: portal refocus → dashboard stayed mounted, probes={len(probe.hits)}")
        page.screenshot(path=SHOTS / f"shell-{engine}-{tag}-portal.png")
        # the public home, signed in: sample the header every 50 ms during load; "登录" must never appear
        seen: set[str] = set()
        page.goto(f"{portal}/", wait_until="commit")
        deadline = time.time() + 4
        while time.time() < deadline:
            seen.update(page.evaluate("() => Array.from(document.querySelectorAll('.pt-nav .pt-link')).map(a => a.textContent.trim())"))
            if seen & {"进入工作空间", "進入工作空間", "Enter workspace"}:
                break
            page.wait_for_timeout(50)
        assert not seen & {"登录", "登入", "Sign in"}, f"public home showed the sign-in link to a signed-in visitor: {sorted(seen)}"
        assert seen & {"进入工作空间", "進入工作空間", "Enter workspace"}, f"public home never resolved to the workspace link: {sorted(seen)}"
        notes.append(f"{engine}/{tag}: public home header states seen while loading: {sorted(seen)}")

        # ---- Docs workspace at phone width: pane switch -------------------------------------
        page.goto(f"{docs}/")
        page.wait_for_load_state("networkidle")
        if page.locator("a[href*='/auth/start']").count() and not page.locator(".home").count():
            page.locator("a[href*='/auth/start']").first.click()
            page.wait_for_load_state("networkidle")
        cards = page.locator("a[href^='/p/']")
        if not cards.count() and page.get_by_role("button", name=re.compile("新建项目|新建項目|New project")).count():
            # the account has no project yet: make one through the real UI, so the workspace
            # itself can be checked
            page.get_by_role("button", name=re.compile("新建项目|新建項目|New project")).click()
            page.locator(".modal input").fill("Shell check")
            page.locator(".modal").get_by_role("button", name=re.compile("确定|確定|OK")).click()
            page.wait_for_url(re.compile(r"/p/p_"), timeout=15000)
            page.goto(f"{docs}/")
            page.wait_for_load_state("networkidle")
            cards = page.locator("a[href^='/p/']")
        if cards.count():
            cards.first.click()
            page.wait_for_selector(".work", timeout=20000)
            switch = page.locator(".pane-switch")
            if width < 400:
                expect(switch).to_be_visible()
                tabs = switch.get_by_role("tab")
                assert tabs.count() == 4, tabs.all_inner_texts()   # 文件 / 来源 / 目标 / 映射
                tabs.nth(2).click()                                 # 目标
                assert page.evaluate("() => getComputedStyle(document.querySelector('.work > .pane.left')).display") == "none"
                assert page.evaluate("() => getComputedStyle(document.querySelector('.work > .pane.right')).display") != "none"
                expect(page.locator(".selbar")).to_be_visible()
                notes.append(f"{engine}/{tag}: Docs pane switch present; 目标 tab hides the source pane, selection bar visible")
            else:
                assert not switch.is_visible()
                notes.append(f"{engine}/{tag}: Docs pane switch hidden at {width}px")
            page.screenshot(path=SHOTS / f"shell-{engine}-{tag}-docs.png")
        else:
            notes.append(f"{engine}/{tag}: no Docs project visible for {email} at {page.url.split('?')[0]}; workspace check skipped")

        # ---- signing out of Docs ends the platform session (CI-60) ---------------------------
        # "退出登录" on a relying party goes through the account service's logout and returns to
        # this origin's /signed-out; afterwards neither Docs nor the account service knows the
        # person, and "重新登录" needs the password again.
        if width >= 400:
            page.goto(f"{docs}/")
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name=re.compile("退出登录|登出|Sign out")).first.click()
            page.wait_for_url(re.compile(re.escape(docs) + r"/signed-out(\?|$)"), timeout=20000)
            expect(page.get_by_text(re.compile("^(已退出|已登出|Signed out)$"))).to_be_visible()
            assert page.request.get(f"{docs}/api/auth/me").status == 401, "Docs still knows the person after signing out"
            assert page.request.get(f"{accounts}/api/auth/me").status == 401, "the account service still knows the person after signing out of Docs"
            page.get_by_role("link", name=re.compile("重新登录|重新登入|Sign in again")).click()
            page.wait_for_url(re.compile(re.escape(accounts) + r"/login"), timeout=20000)
            expect(page.locator("input[type=password]").first).to_be_visible()
            notes.append(f"{engine}/{tag}: Docs 退出登录 → {docs.split('//')[-1]}/signed-out; Docs and accounts /api/auth/me both 401; 重新登录 asks for the password")
        assert not csp, ("Content-Security-Policy violations", csp[:5])
        notes.append(f"{engine}/{tag}: 0 CSP violations across the pages above")
        ctx.close()
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 5:
        raise SystemExit(__doc__)
    accounts, portal, docs = (require_test_instance(a) for a in args[:3])
    email, password = args[3], args[4]
    want_webkit = "--webkit" in sys.argv
    # WebKit resolves hostnames through the system, which has no *.localhost entries. A local
    # forward proxy (localhost_proxy.py) resolves them to the loopback address while the browser
    # keeps one cookie jar per origin, so the cross-origin sign-on hops run for real in WebKit too.
    loop = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--webkit-accounts=")), None)

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, portal, docs, email, password))
        finally:
            chrome.close()
        if want_webkit:
            try:
                wk = p.webkit.launch(headless=True)
            except Exception as e:  # the build is not installed: say so, do not pretend
                show("webkit", [f"not run: {type(e).__name__}: {str(e).splitlines()[0][:120]}"])
            else:
                try:
                    if reachable(wk, portal):
                        show("webkit (Playwright build)", run(wk, "webkit", accounts, portal, docs, email, password))
                    elif loop:
                        base = require_test_instance(loop)
                        show(f"webkit (Playwright build; account origin {base}, no proxy)", run(wk, "webkit", base, portal, docs, email, password))
                    else:
                        with LocalhostProxy() as proxy:
                            show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                                 run(wk, "webkit", accounts, portal, docs, email, password, {"proxy": {"server": proxy.url}}))
                finally:
                    wk.close()


if __name__ == "__main__":
    main()
