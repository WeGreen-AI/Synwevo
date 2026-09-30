"""Opt-in Admin Passkey review through a real isolated browser and account server.

Run after building account-web:
    .venv/bin/python -m services.identity.tests.browser_admin_passkey_review

Chromium's CDP virtual CTAP2 UV authenticators are TEST EQUIPMENT, not physical Passkeys.
The browser registers credentials and produces every signature. No routes, sessions, proofs
or credentials are mocked/imported. Direct database writes are limited to named temporary
fixture accounts/roles and removal of the fixture administrator's password to construct the
passwordless scenario. Only screenshots and a redacted report remain in output/playwright.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import TimeoutError as BrowserTimeout, expect, sync_playwright

PW = "Isolated Passkey browser review fixture passphrase 2026!"
PRIMARY = "Fixture virtual primary UV key"
BACKUP = "Fixture virtual backup UV key"


def run():
    artifacts = Path(__file__).resolve().parents[3] / "output/playwright/admin-passkey-review"
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, page_errors = [], []
    report = {
        "facility": "Chromium CDP virtual CTAP2 UV; real UI, server verification and browser signatures",
        "limitations": ["Not physical-device or biometric verification", "Chromium only",
                        "Cancellation covers dialog dismissal, full navigation and same-document browser Back"],
        "checks": checks, "page_errors": page_errors,
    }
    stage, page = "fixture setup", None

    with tempfile.TemporaryDirectory(prefix="synwevo-passkey-browser-fixture-") as tmp, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = "http://localhost:" + str(listener.getsockname()[1])
        for key, value in {"DATA_DIR": tmp, "UI_AUTH": "session", "COOKIE_SECURE": "0",
                           "TEST_INSTANCE": "1", "ROLE": "accounts", "MAIL_MODE": "file",
                           "MAIL_DIR": tmp + "/mail", "PUBLIC_URL": origin, "ISSUER": origin,
                           "RP_ID": "localhost", "RP_ORIGINS": origin, "REGISTRATION": "open"}.items():
            os.environ["SYNWEVO_" + key] = value
        from synwevo_identity.app import create_app
        from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
        app = create_app(tmp, start_jobs=False)
        ident, store = app.state.identity, app.state.identity.store

        def fixture(email, name, role=None):
            user = ident.create_user(email, PW, display_name=name, status="active")
            store.execute("UPDATE users SET email_verified_at=?,locale='en',timezone='UTC' WHERE id=?",
                          (time.time(), user["id"]))
            if role:
                PlatformAccess(store).host_grant(user["id"], role, source="fixture:admin-passkey-browser",
                    reason="Explicit isolated browser verification fixture")
            return user

        administrator = fixture("fixture-passkey-admin@example.test", "Fixture Passkey administrator", SUPER_ADMIN)
        member = fixture("fixture-passkey-member@example.test", "Fixture ordinary member")
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started, "Isolated account server did not start"
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    def context(touch=False):
                        ctx = browser.new_context(locale="en-US", has_touch=touch, is_mobile=touch,
                                                  viewport={"width": 1440, "height": 1000})
                        ctx.on("page", lambda p: p.on("pageerror", lambda error: page_errors.append(str(error))))
                        return ctx

                    def login(ctx, user):
                        p = ctx.new_page()
                        p.goto(origin + "/login?next=/account&lang=en")
                        p.locator("#login-email").fill(user["email"])
                        p.locator("input[type=password]").fill(PW)
                        p.locator("form button.primary").click()
                        p.wait_for_url("**/account")
                        expect(p.locator("html")).to_have_attribute("lang", "en")
                        return p

                    admin_ctx, member_ctx = context(touch=True), context()
                    page = login(admin_ctx, administrator)
                    login(member_ctx, member)
                    assert page.evaluate("window.isSecureContext")
                    assert admin_ctx.request.get(origin + "/healthz").json()["instance"] == "test"
                    assert member_ctx.request.get(origin + "/api/auth/me").status == 200
                    cdp = admin_ctx.new_cdp_session(page)
                    cdp.send("WebAuthn.enable")

                    def virtual_key(transport="internal"):
                        return cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
                            "protocol": "ctap2", "transport": transport, "hasResidentKey": True,
                            "hasUserVerification": True, "isUserVerified": True,
                            "automaticPresenceSimulation": True}})["authenticatorId"]

                    def presence(authenticator, enabled):
                        cdp.send("WebAuthn.setAutomaticPresenceSimulation",
                                 {"authenticatorId": authenticator, "enabled": enabled})

                    primary_device = virtual_key()

                    def keys():
                        return page.locator("#security > section").filter(
                            has=page.get_by_role("heading", name="Passkeys", exact=True))

                    def response(path, method="POST"):
                        return page.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == method)

                    def capture(name):
                        page.screenshot(path=str(artifacts / name), full_page=False, animations="disabled",
                                        mask=[page.locator("input[type=password]")])

                    def key_listing():
                        r = admin_ctx.request.get(origin + "/api/auth/passkeys")
                        assert r.status == 200
                        return r.json()["items"]

                    def register(name):
                        keys().get_by_role("textbox", name="Name (optional)", exact=True).fill(name)
                        with response("/api/auth/passkeys/register/finish") as registered:
                            keys().get_by_role("button", name="Register a passkey", exact=True).click()
                        assert registered.value.status == 200, registered.value.text()
                        expect(keys().locator("tbody tr")).to_have_count(len(key_listing()))

                    def successful_reauth(scope):
                        return [row for row in store.all(
                            "SELECT * FROM audit_events WHERE action='auth.reauth' AND result='ok' ORDER BY seq")
                            if json.loads(row["meta"]).get("scope") == scope]

                    stage = "register primary virtual UV key through account UI"
                    expect(keys().locator("input[type=password]")).to_be_visible()
                    keys().locator("input[type=password]").fill(PW)
                    with response("/api/auth/reauth") as confirmed:
                        keys().get_by_role("button", name="Verify with password", exact=True).click()
                    assert confirmed.value.status == 200 and confirmed.value.json()["scope"] == "mfa"
                    expect(keys().get_by_role("button", name="Register a passkey", exact=True)).to_be_enabled()
                    register(PRIMARY)
                    listing = key_listing()
                    assert len(listing) == 1 and listing[0]["uv_capable"] and listing[0]["usable_here"]
                    capture("primary-virtual-key-registered.png")
                    checks.append("Account UI password confirmation then browser-created UV key registration")

                    # Explicit fixture-only change. No session, reauth timestamp or signed credential is inserted.
                    with store.tx():
                        store.execute("DELETE FROM user_passwords WHERE user_id=?", (administrator["id"],))
                    assert not store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (administrator["id"],))
                    stage = "real logout and passwordless Passkey login"
                    page.locator("header.topbar").get_by_role("button", name="Sign out", exact=True).click()
                    page.wait_for_url(lambda url: urlsplit(str(url)).path in ("/login", "/logged-out"))
                    assert admin_ctx.request.get(origin + "/api/auth/me").status == 401
                    page.goto(origin + "/login?next=/account&lang=en")
                    with response("/api/auth/login/passkey") as logged_in:
                        page.get_by_role("button", name="Sign in with a security key", exact=True).click()
                    assert logged_in.value.status == 200, logged_in.value.text()
                    page.wait_for_url("**/account")
                    me = admin_ctx.request.get(origin + "/api/auth/me").json()
                    assert me["id"] == administrator["id"] and me["session"]["amr"] == "passkey_uv"
                    assert PlatformAccess(store).is_usable_super_admin(administrator["id"])
                    checks.append("Fixture password removed; actual logout and resident UV Passkey login succeeds")

                    stage = "mfa scoped Passkey confirmation and backup registration"
                    expect(keys().get_by_role("button", name="Verify with a registered passkey", exact=True)).to_be_visible()
                    expect(keys().locator("input[type=password]")).to_have_count(0)
                    before_count = len(key_listing())
                    with response("/api/auth/reauth/passkey/finish") as mfa_confirmation:
                        keys().get_by_role("button", name="Verify with a registered passkey", exact=True).click()
                    assert mfa_confirmation.value.status == 200, mfa_confirmation.value.text()
                    assert mfa_confirmation.value.json()["scope"] == "mfa"
                    assert len(key_listing()) == before_count  # Verification alone cannot register a key.
                    presence(primary_device, False)
                    backup_device = virtual_key("usb")
                    register(BACKUP)
                    presence(primary_device, True)
                    presence(backup_device, False)
                    expect(keys().locator("tbody tr")).to_have_count(2)
                    assert all(key["uv_capable"] and key["usable_here"] for key in key_listing())
                    capture("passwordless-mfa-confirmation-and-backup-key.png")
                    mfa_event = successful_reauth("mfa")[-1]
                    assert json.loads(mfa_event["meta"])["method"] == "passkey_uv"
                    assert json.loads(mfa_event["meta"])["amr"] == "passkey_uv"
                    checks.append("Account UI uses registered UV key for mfa purpose; only a separate action registers a backup")

                    stage = "last usable highest administrator key protection"
                    backup_row = keys().locator("tbody tr").filter(
                        has=page.locator(f'input[value="{BACKUP}"]'))
                    with page.expect_response(lambda r: r.request.method == "DELETE" and
                                              urlsplit(r.url).path.startswith("/api/auth/passkeys/")) as removed:
                        backup_row.get_by_role("button", name="Delete", exact=True).click()
                    assert removed.value.status == 200, removed.value.text()
                    expect(keys().locator("tbody tr")).to_have_count(1)
                    with page.expect_response(lambda r: r.request.method == "DELETE" and
                                              urlsplit(r.url).path.startswith("/api/auth/passkeys/")) as denied:
                        keys().locator("tbody tr").get_by_role("button", name="Delete", exact=True).click()
                    assert denied.value.status == 409 and denied.value.json()["error"] == "last_super_admin", denied.value.text()
                    assert len(key_listing()) == 1 and PlatformAccess(store).is_usable_super_admin(administrator["id"])
                    expect(keys().get_by_role("alert")).to_be_visible()
                    capture("last-usable-highest-administrator-key-protected.png")
                    checks.append("Backup removal succeeds; removing the sole remaining highest administrator UV method is refused")

                    status_path = f"/api/admin/users/{member['id']}/status"

                    labels = {
                        "en": ("Search accounts", "Suspend account", "Confirm with a passkey and continue", "Cancel"),
                        "zh-Hans": ("搜索账户", "停用账户", "使用安全密钥确认并继续", "取消"),
                        "zh-Hant": ("搜索帳戶", "停用帳戶", "使用安全金鑰確認並繼續", "取消"),
                    }

                    def prepare_action(*, navigate=True, locale="en"):
                        search_label, suspend_label, passkey_label, _ = labels[locale]
                        if navigate:
                            page.goto(origin + "/admin?tab=users")
                        page.get_by_role("searchbox", name=search_label, exact=True).fill(member["email"])
                        row = page.locator("table.gov tbody tr").filter(has_text=member["email"])
                        expect(row).to_have_count(1)
                        row.get_by_role("button").click()
                        page.get_by_role("menuitem", name=suspend_label, exact=True).click()
                        dialog = page.get_by_role("alertdialog")
                        expect(dialog.get_by_role("checkbox")).to_be_visible()
                        dialog.get_by_role("checkbox").check()
                        dialog.locator("textarea").fill("Isolated virtual Passkey review: confirmed member suspension")
                        with response(status_path) as refusal:
                            dialog.get_by_role("button", name=suspend_label, exact=True).click()
                        assert refusal.value.status == 403 and refusal.value.json()["error"] == "reauth_required", refusal.value.text()
                        expect(dialog.get_by_role("button", name=passkey_label, exact=True)).to_be_visible()
                        expect(dialog.locator("input[type=password]")).to_have_count(0)
                        return dialog

                    stage = "mfa scope cannot authorize Admin and dialog cancellation changes nothing"
                    dialog = prepare_action()
                    assert not successful_reauth("admin")
                    capture("admin-passkey-only-confirmation.png")
                    dialog.get_by_role("button", name="Cancel", exact=True).click()
                    expect(dialog).to_be_hidden()
                    assert ident.get_user(member["id"])["status"] == "active"
                    assert not store.all("SELECT * FROM admin_operations")
                    assert member_ctx.request.get(origin + "/api/auth/me").status == 200
                    checks.append("mfa-purpose proof cannot authorize Admin; passwordless dialog offers Passkey; Cancel leaves member active")

                    stage = "navigation cancels an actual pending browser ceremony"
                    dialog = prepare_action()
                    presence(primary_device, False)
                    with response("/api/auth/reauth/passkey/begin") as pending:
                        dialog.get_by_role("button", name="Confirm with a passkey and continue", exact=True).click()
                    assert pending.value.status == 200, pending.value.text()
                    # Leaving the document aborts the browser's pending credentials.get; no fake rejection is injected.
                    page.goto(origin + "/account")
                    presence(primary_device, True)
                    assert not successful_reauth("admin") and not store.all("SELECT * FROM admin_operations")
                    assert ident.get_user(member["id"])["status"] == "active"
                    assert member_ctx.request.get(origin + "/api/auth/me").status == 200
                    checks.append("Navigating away aborts a real pending virtual-authenticator request without executing the action")

                    stage = "same-document browser Back unmounts and cancels pending confirmation"
                    page.goto(origin + "/admin?tab=overview")
                    original_document = page.evaluate("performance.timeOrigin")
                    page.locator('.admin-navigation a[href*="tab=users"]').click()
                    expect(page).to_have_url(origin + "/admin?tab=users")
                    assert page.evaluate("performance.timeOrigin") == original_document
                    dialog = prepare_action(navigate=False)
                    presence(primary_device, False)
                    with response("/api/auth/reauth/passkey/begin") as spa_pending:
                        dialog.get_by_role("button", name="Confirm with a passkey and continue", exact=True).click()
                    assert spa_pending.value.status == 200, spa_pending.value.text()
                    late_requests = []
                    def track_late_request(request):
                        if request.method == "POST" and urlsplit(request.url).path in (
                                "/api/auth/reauth/passkey/finish", status_path):
                            late_requests.append(urlsplit(request.url).path)
                    page.on("request", track_late_request)
                    page.go_back()
                    expect(page).to_have_url(origin + "/admin?tab=overview")
                    expect(page.get_by_role("alertdialog")).to_have_count(0)
                    assert page.evaluate("performance.timeOrigin") == original_document, "Back reloaded the document"
                    # Give a real late authenticator response a bounded chance to expose a stale callback.
                    # The fixed UI aborts credentials.get on component unmount, so no POST can follow.
                    try:
                        with page.expect_request(lambda r: r.method == "POST" and urlsplit(r.url).path in (
                                "/api/auth/reauth/passkey/finish", status_path), timeout=1200):
                            presence(primary_device, True)
                    except BrowserTimeout:
                        pass
                    else:
                        raise AssertionError("Unmounted confirmation sent a late proof or business request")
                    page.remove_listener("request", track_late_request)
                    assert late_requests == []
                    assert not successful_reauth("admin") and not store.all("SELECT * FROM admin_operations")
                    assert ident.get_user(member["id"])["status"] == "active"
                    assert member_ctx.request.get(origin + "/api/auth/me").status == 200
                    report["same_document_back"] = {"document_preserved": True, "late_commit_requests": late_requests}
                    checks.append("SPA users navigation then browser Back preserves the document, unmounts the dialog and prevents late finish/business POSTs")

                    stage = "three-language touch viewport Passkey-only dialogs"
                    for locale in ("zh-Hans", "zh-Hant", "en"):
                        page.set_viewport_size({"width": 1440, "height": 1000})
                        page.goto(origin + "/admin?tab=users")
                        page.locator(".language-menu select").select_option(locale)
                        expect(page.locator("html")).to_have_attribute("lang", locale)
                        dialog = prepare_action(navigate=False, locale=locale)
                        for width in (320, 390):
                            page.set_viewport_size({"width": width, "height": 844})
                            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (locale, width)
                            assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (locale, width)
                            assert dialog.locator("textarea, button, input:not([hidden])").evaluate_all(
                                "nodes => nodes.every(node => {const r = node.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1;})"), (locale, width)
                            expect(dialog.locator("input[type=password]")).to_have_count(0)
                            # The modal body scrolls on a phone. Show the actual verification
                            # choice above its fixed footer, not just the top of the impact text.
                            dialog.get_by_role("button", name=labels[locale][2], exact=True).scroll_into_view_if_needed()
                            capture(f"admin-passkey-only-{locale}-{width}.png")
                        dialog.get_by_role("button", name=labels[locale][3], exact=True).click()
                        expect(dialog).to_be_hidden()
                        checks.append(f"{locale}: touch-context 320/390 Passkey-only confirmation, no horizontal overflow, cancellation leaves member active")
                        assert ident.get_user(member["id"])["status"] == "active"
                        assert not store.all("SELECT * FROM admin_operations")
                    page.set_viewport_size({"width": 1440, "height": 1000})

                    stage = "admin scoped real assertion executes the confirmed suspension"
                    dialog = prepare_action()
                    with response("/api/auth/reauth/passkey/finish") as proof, response(status_path) as executed:
                        dialog.get_by_role("button", name="Confirm with a passkey and continue", exact=True).click()
                    assert proof.value.status == 200 and proof.value.json()["scope"] == "admin", proof.value.text()
                    assert proof.value.json()["amr"] == "passkey_uv"
                    assert executed.value.status == 200, executed.value.text()
                    operation_id = executed.value.json()["operation_id"]
                    expect(dialog).to_be_hidden()
                    expect(page.locator(".notice[role=status]")).to_contain_text(operation_id)
                    assert ident.get_user(member["id"])["status"] == "suspended"
                    assert member_ctx.request.get(origin + "/api/auth/me").status == 401
                    operation = store.one("SELECT * FROM admin_operations WHERE id=?", (operation_id,))
                    assert operation["actor_id"] == administrator["id"] and operation["target_id"] == member["id"]
                    event = store.one("SELECT * FROM audit_events WHERE action='admin.user.status' AND result='ok' ORDER BY seq DESC LIMIT 1")
                    proof_event = successful_reauth("admin")[-1]
                    assert json.loads(event["meta"])["operation_id"] == operation_id
                    assert proof_event["seq"] < event["seq"] and event["request_id"] and proof_event["request_id"]
                    assert json.loads(proof_event["meta"]) == {"scope": "admin", "amr": "passkey_uv", "method": "passkey_uv"}
                    capture("passkey-confirmed-member-suspended.png")
                    checks.append("Real admin UV assertion precedes confirmed operation; member's separate session immediately returns 401")
                    report["operation_id"] = operation_id
                    report["audit"] = {"reauth_scope": "admin", "reauth_amr": "passkey_uv",
                                       "reauth_seq": proof_event["seq"], "operation_seq": event["seq"]}
                    assert not page_errors, page_errors
                    (artifacts / "failure.png").unlink(missing_ok=True)
                except Exception as error:
                    report["failure"] = {"stage": stage, "error": str(error).replace(PW, "[fixture password redacted]")}
                    if page is not None and not page.is_closed():
                        page.screenshot(path=str(artifacts / "failure.png"), full_page=False,
                                        animations="disabled", mask=[page.locator("input[type=password]")])
                    raise
                finally:
                    browser.close()
        finally:
            server.should_exit = True
            worker.join(timeout=5)
            (artifacts / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"artifacts": str(artifacts), **report}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    run()
