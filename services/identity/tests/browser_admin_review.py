"""Isolated real-browser proof for Synwevo Admin; no intercepted APIs or fake sign-in.

Build @synwevo/account-web and run from the repository root:
    .venv/bin/python -m services.identity.tests.browser_admin_review
All accounts, role grants, email and data are explicit temporary fixtures. Only screenshots
and a result report remain under the ignored output/playwright/admin-review directory.
"""
from pathlib import Path
import json
import os
import re
import socket
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright, expect

PW = "Temporary administrator browser fixture passphrase 2026!"


def run():
    artifacts = Path(__file__).resolve().parents[3] / "output/playwright/admin-review"
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with tempfile.TemporaryDirectory(prefix="synwevo-admin-browser-fixture-") as tmp, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = "http://127.0.0.1:" + str(listener.getsockname()[1])
        for key, value in {"DATA_DIR": tmp, "UI_AUTH": "session", "COOKIE_SECURE": "0", "TEST_INSTANCE": "1",
                           "ROLE": "accounts", "MAIL_MODE": "file", "MAIL_DIR": tmp + "/mail",
                           "PUBLIC_URL": origin, "REGISTRATION": "open"}.items():
            os.environ["SYNWEVO_" + key] = value
        from synwevo_identity.app import create_app
        from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
        app = create_app(tmp, start_jobs=False)
        ident = app.state.identity

        def fixture(email, name, role=None):
            user = ident.create_user(email, PW, display_name=name, status="active")
            ident.store.execute("UPDATE users SET email_verified_at=?,locale='en',timezone='UTC' WHERE id=?", (time.time(), user["id"]))
            if role:
                PlatformAccess(ident.store).host_grant(user["id"], role, source="fixture:admin-browser-review", reason="Explicit isolated browser review fixture")
            return user

        admin = fixture("fixture-admin@example.test", "Fixture administrator", SUPER_ADMIN)
        ordinary = fixture("fixture-member@example.test", "Fixture ordinary member")
        auditor = fixture("fixture-auditor@example.test", "Fixture audit reviewer", "auditor")
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started, "Isolated account server failed to start"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)

                def context(touch=False):
                    ctx = browser.new_context(locale="en-US", has_touch=touch, is_mobile=touch, viewport={"width": 390 if touch else 1440, "height": 844 if touch else 1000})
                    ctx.on("page", lambda p: p.on("pageerror", lambda e: errors.append(str(e))))
                    return ctx

                def login(ctx, user, destination, locale="en"):
                    page = ctx.new_page()
                    page.goto(origin + "/login?next=" + destination)
                    page.locator("#login-email").fill(user["email"])
                    page.locator("input[type=password]").fill(PW)
                    page.locator("form button.primary").click()
                    page.wait_for_url(origin + destination)
                    expect(page.locator("html")).to_have_attribute("lang", locale)
                    return page

                def within_viewport(page):
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Page exceeds viewport"
                    assert page.locator(".admin-content > .card").evaluate_all("cards => cards.every(card => card.getBoundingClientRect().right <= innerWidth + 1)"), "Admin card exceeds viewport"

                admin_ctx = context()
                ordinary_ctx = context()
                page = login(admin_ctx, admin, "/admin?tab=users")
                member_page = login(ordinary_ctx, ordinary, "/account")
                expect(page.locator(".admin-page-heading h1")).to_have_text(re.compile(r"^Accounts?$"))
                assert admin_ctx.request.get(origin + "/healthz").json()["instance"] == "test"
                assert ordinary_ctx.request.get(origin + "/api/auth/me").status == 200
                checks.append("Separate administrator and ordinary-user contexts signed in with real passwords")

                for locale in ("zh-Hans", "zh-Hant", "en"):
                    page.set_viewport_size({"width": 1440, "height": 1000})
                    page.locator(".language-menu select").select_option(locale)
                    expect(page.locator("html")).to_have_attribute("lang", locale)
                    expect(page.locator("table.gov tbody tr")).to_have_count(3)
                    # A real mobile browser context exercises the shared coarse-pointer sizing.
                    mobile_ctx = context(touch=True)
                    mobile = login(mobile_ctx, admin, "/admin?tab=users", locale)
                    expect(mobile.locator("table.gov tbody tr")).to_have_count(3)
                    for width in (1440, 390, 320):
                        screen = page if width == 1440 else mobile
                        screen.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                        screen.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        within_viewport(screen)
                        if width < 720:
                            assert screen.locator("table.admin-users-table").evaluate("el => el.scrollWidth <= innerWidth - 20"), "Account cards hide a field off screen"
                        screen.screenshot(path=str(artifacts / f"admin-users-{locale}-{width}.png"), full_page=False, animations="disabled")
                        if width < 720:
                            opener = screen.locator(".acct-topbar button[aria-haspopup=dialog]")
                            opener.focus()
                            screen.keyboard.press("Enter")
                            drawer = screen.get_by_role("dialog")
                            expect(drawer).to_be_visible()
                            expect(drawer.locator(".admin-navigation")).to_be_visible()
                            screen.screenshot(path=str(artifacts / f"admin-navigation-{locale}-{width}.png"), full_page=False, animations="disabled")
                            for _ in range(35):
                                screen.keyboard.press("Tab")
                                assert drawer.evaluate("el => el.contains(document.activeElement)"), "Drawer focus escaped"
                            screen.keyboard.press("Escape")
                            expect(drawer).to_be_hidden()
                            expect(opener).to_be_focused()
                    mobile_ctx.close()
                    checks.append(f"{locale}: 1440/390/320 layout, shared drawer focus trap, Escape and return focus")

                page.set_viewport_size({"width": 390, "height": 844})
                page.locator(".acct-topbar button[aria-haspopup=dialog]").click()
                drawer = page.get_by_role("dialog")
                drawer.locator(".admin-navigation").get_by_role("link", name="Mail delivery", exact=True).click()
                expect(drawer).to_be_hidden()
                expect(page).to_have_url(re.compile(r"tab=mail"))
                expect(page.locator(".admin-page-heading h1")).to_have_text("Mail delivery")
                page.reload()
                expect(page.locator(".admin-page-heading h1")).to_have_text("Mail delivery")
                checks.append("Mobile section navigation closes the drawer; legacy ?tab= deep link survives reload")

                page.goto(origin + "/admin/api-platform?tab=calls")
                expect(page.locator(".admin-page-heading h1")).to_have_text("API management")
                expect(page.locator('nav[aria-label="API administration navigation"] button[aria-current=page]')).to_have_text("Call records")
                page.reload()
                expect(page.locator('nav[aria-label="API administration navigation"] button[aria-current=page]')).to_have_text("Call records")
                within_viewport(page)
                page.locator('nav[aria-label="API administration navigation"]').scroll_into_view_if_needed()
                page.screenshot(path=str(artifacts / "admin-api-calls-en-390.png"), full_page=False, animations="disabled")
                checks.append("API console shares the Admin shell and retains its calls tab after reload")

                page.set_viewport_size({"width": 1440, "height": 1000})
                page.goto(origin + "/admin?tab=users")
                page.get_by_role("searchbox", name="Search accounts", exact=True).fill(ordinary["email"])
                row = page.locator("table.gov tbody tr").filter(has_text=ordinary["email"])
                expect(row).to_have_count(1)
                row.get_by_role("button").click()
                page.get_by_role("menuitem", name="Suspend account", exact=True).click()
                dialog = page.get_by_role("alertdialog")
                expect(dialog.get_by_role("checkbox")).to_be_visible()
                expect(dialog).to_contain_text(ordinary["email"])
                expect(dialog).to_contain_text("Sessions to revoke: 1")
                dialog.get_by_role("checkbox").check()
                dialog.locator("textarea").fill("Browser fixture: exercise confirmed suspension")
                page.screenshot(path=str(artifacts / "account-suspend-preview-en.png"), full_page=False, animations="disabled")
                dialog.get_by_role("button", name="Suspend account", exact=True).click()
                expect(dialog.locator("input[type=password]")).to_be_visible()
                dialog.locator("input[type=password]").fill(PW)
                dialog.locator("button[type=submit]").click()
                expect(dialog).to_be_hidden()
                expect(page.locator(".notice[role=status]")).to_contain_text("Operation")
                assert ident.get_user(ordinary["id"])["status"] == "suspended"
                assert ordinary_ctx.request.get(origin + "/api/auth/me").status == 401
                page.screenshot(path=str(artifacts / "account-suspended-en.png"), full_page=False, animations="disabled")
                checks.append("UI search → impact preview → confirmation → reason → password reauthentication → suspension; existing user session immediately returns 401")

                row.get_by_role("button").click()
                page.get_by_role("menuitem", name="Restore account", exact=True).click()
                dialog = page.get_by_role("alertdialog")
                expect(dialog.get_by_role("checkbox")).to_be_visible()
                expect(dialog).to_contain_text("Previous sessions, tokens and recovery links remain invalid")
                dialog.get_by_role("checkbox").check()
                dialog.locator("textarea").fill("Browser fixture: restore verified member after suspension")
                dialog.get_by_role("button", name="Restore account", exact=True).click()
                expect(dialog).to_be_hidden()
                expect(row).to_contain_text("Active")
                assert ident.get_user(ordinary["id"])["status"] == "active"
                assert ordinary_ctx.request.get(origin + "/api/auth/me").status == 401
                fresh_ctx = context()
                login(fresh_ctx, ordinary, "/account")
                assert fresh_ctx.request.get(origin + "/api/auth/me").status == 200
                checks.append("UI restoration leaves revoked sessions invalid; a new password login succeeds")

                audit_ctx = context()
                audit_page = login(audit_ctx, auditor, "/admin?tab=users")
                expect(audit_page.locator(".admin-page-heading h1")).to_have_text(re.compile(r"^Accounts?$"))
                assert audit_page.locator('.admin-navigation a[href*="api-platform"]').count() == 0
                token = next(cookie["value"] for cookie in audit_ctx.cookies() if cookie["name"] == "dw_csrf")
                denied = audit_ctx.request.post(origin + f'/api/admin/users/{ordinary["id"]}/status',
                    data={"status": "suspended", "reason": "Browser fixture: auditor must be refused"},
                    headers={"X-Synwevo-CSRF": token})
                assert denied.status == 403, denied.text()
                assert denied.json()["error"] == "platform_permission_denied", denied.text()
                assert ident.get_user(ordinary["id"])["status"] == "active"
                checks.append("Auditor has no API navigation and a direct account mutation is rejected by the server")
                locked_ctx = context(touch=True)
                locked = locked_ctx.new_page()
                locked.goto(origin + "/login?next=/admin&lang=en")
                locked.get_by_role("button", name="Sign in with an email code", exact=True).click()
                locked.locator("#login-email").fill(admin["email"])
                locked.locator("form button.primary").click()
                expect(locked.locator("#email-code")).to_be_visible()
                mail = ident.store.one("SELECT body FROM mail_outbox WHERE kind='login_code' AND to_addr=? ORDER BY created_at DESC LIMIT 1", (admin["email"],))
                code = re.search(r"\b[0-9]{6}\b", mail["body"])[0]
                locked.locator("#email-code").fill(code)
                locked.locator("form button.primary").click()
                locked.wait_for_url(origin + "/admin")
                expect(locked.get_by_role("region", name="This sign-in proved one factor only")).to_be_visible()
                assert locked.locator(".admin-navigation").count() == 0
                assert locked_ctx.request.get(origin + "/api/admin/overview").status == 403
                locked.screenshot(path=str(artifacts / "admin-locked-email-code-en-390.png"), full_page=False, animations="disabled")
                locked.get_by_role("link", name="Confirm your identity on the account page", exact=True).click()
                locked.locator("#step-up-password").fill(PW)
                locked.locator("#step-up button[type=submit]").click()
                expect(locked.get_by_role("status").filter(has_text="Confirmed. Platform management is available.")).to_be_visible()
                locked.goto(origin + "/admin?tab=users")
                expect(locked.locator("table.gov")).to_be_visible()
                checks.append("Real emailed-code admin login is locked, exposes no management views, and unlocks through the existing account password step-up")
                assert not errors, errors
                (artifacts / "report.json").write_text(json.dumps({"checks": checks, "page_errors": errors}, ensure_ascii=False, indent=2))
                print(json.dumps({"artifacts": str(artifacts), "checks": checks, "page_errors": errors}, ensure_ascii=False, indent=2))
                browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=5)


if __name__ == "__main__":
    run()
