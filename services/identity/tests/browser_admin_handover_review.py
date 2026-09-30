"""Opt-in real-browser B4 acceptance, against the actual isolated identity server.

After the B4 implementation and account-web build are ready, run at the repository root:
    .venv/bin/python -m services.identity.tests.browser_admin_handover_review

CDP virtual CTAP2 UV authenticators are TEST EQUIPMENT. The browser registers the key
and signs every assertion. No routes, sessions, challenge responses or authentication
clocks are replaced. Direct writes only create named fixture people and the pre-existing
root grant. Three browser contexts remain independent. No email or production data is used.
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
from uuid import uuid4

import uvicorn
from playwright.sync_api import TimeoutError as BrowserTimeout, expect, sync_playwright

PW = "Isolated handover browser fixture passphrase 2026!"
KEY_NAME = "Fixture recipient virtual UV passkey"
OFFERS = "/api/auth/super-admin-offers"
ADMIN_OFFERS = "/api/admin/super-admin-offers"


class Review:
    """Real UI interactions and explicit read-only observations of the fixture Store."""
    def __init__(self, browser, origin, store, artifacts, report):
        self.browser, self.origin, self.store = browser, origin, store
        self.artifacts, self.report = artifacts, report
        self.page, self.stage = None, "fixture sign-in"

    def context(self, *, touch=False):
        ctx = self.browser.new_context(locale="en-US", has_touch=touch, is_mobile=touch,
                                       viewport={"width": 1440, "height": 1000})
        ctx.on("page", lambda p: p.on("pageerror", lambda error: self.report["page_errors"].append(str(error))))
        return ctx

    def login(self, ctx, user, *, page=None, destination="/account"):
        p = page or ctx.new_page()
        p.goto(self.origin + "/login?next=" + destination + "&lang=en")
        p.locator("#login-email").fill(user["email"])
        p.locator('input[type="password"]').fill(PW)
        p.locator("form button.primary").click()
        p.wait_for_url(self.origin + destination)
        expect(p.locator("html")).to_have_attribute("lang", "en")
        return p

    def request(self, ctx, path, body):
        csrf = next(cookie["value"] for cookie in ctx.cookies() if cookie["name"] == "dw_csrf")
        return ctx.request.post(self.origin + path, data=body, headers={"X-Synwevo-CSRF": csrf})

    def get(self, ctx, path):
        out = ctx.request.get(self.origin + path)
        assert out.status == 200, (path, out.status)
        return out.json()

    def response(self, page, path, *, method="POST"):
        return page.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == method)

    def capture(self, page, name):
        page.screenshot(path=str(self.artifacts / name), full_page=False, animations="disabled",
                        mask=[page.locator('input[type="password"]')])

    def mark(self, text):
        self.report["checks"].append(text)

    def ceremony_device(self, ctx, page):
        cdp = ctx.new_cdp_session(page)
        cdp.send("WebAuthn.enable")
        device = cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
            "hasUserVerification": True, "isUserVerified": True,
            "automaticPresenceSimulation": True}})["authenticatorId"]
        return cdp, device

    @staticmethod
    def presence(cdp, device, enabled):
        cdp.send("WebAuthn.setAutomaticPresenceSimulation", {"authenticatorId": device, "enabled": enabled})

    def register_key(self, page, ctx):
        keys = page.locator("#security > section").filter(has=page.get_by_role("heading", name="Passkeys", exact=True))
        expect(keys.locator('input[type="password"]')).to_be_visible()
        keys.locator('input[type="password"]').fill(PW)
        with self.response(page, "/api/auth/reauth") as proof:
            keys.get_by_role("button", name="Verify with password", exact=True).click()
        assert proof.value.status == 200 and proof.value.json()["scope"] == "mfa"
        keys.get_by_role("textbox", name="Name (optional)", exact=True).fill(KEY_NAME)
        with self.response(page, "/api/auth/passkeys/register/finish") as registered:
            keys.get_by_role("button", name="Register a passkey", exact=True).click()
        assert registered.value.status == 200
        listing = self.get(ctx, "/api/auth/passkeys")["items"]
        assert len(listing) == 1 and listing[0]["uv_capable"] and listing[0]["usable_here"]
        self.capture(page, "recipient-real-virtual-key-registered.png")
        self.mark("Recipient registers a browser-created virtual CTAP2 UV key through real account UI after mfa-purpose password verification")

    @staticmethod
    def browser_assertion(page, options):
        # This executes navigator.credentials.get with real server challenge bytes. It only
        # serializes the browser-created result; it never builds a signature or proof itself.
        return page.evaluate("""async options => {
            const decode = s => Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/') + '='.repeat((4-s.length%4)%4)), c => c.charCodeAt(0));
            const encode = value => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
            const publicKey = 'parseRequestOptionsFromJSON' in PublicKeyCredential
                ? PublicKeyCredential.parseRequestOptionsFromJSON(options)
                : {...options, challenge: decode(options.challenge), allowCredentials: (options.allowCredentials || []).map(c => ({...c,id:decode(c.id)}))};
            const credential = await navigator.credentials.get({publicKey});
            const r = credential.response;
            return {id:credential.id,rawId:encode(credential.rawId),type:credential.type,
                clientExtensionResults:credential.getClientExtensionResults(),
                response:{clientDataJSON:encode(r.clientDataJSON),authenticatorData:encode(r.authenticatorData),
                    signature:encode(r.signature),...(r.userHandle ? {userHandle:encode(r.userHandle)} : {})}};
        }""", options)

    def generic_proof_refused(self, page, ctx, offer_id, phase):
        begun = self.request(ctx, "/api/auth/reauth/passkey/begin", {"scope": "admin"})
        assert begun.status == 200
        credential = self.browser_assertion(page, begun.json())
        refused = self.request(ctx, f"{OFFERS}/{offer_id}/{phase}/finish" if phase == "accept"
                               else f"{ADMIN_OFFERS}/{offer_id}/ready/finish",
                               {"credential": credential, "idempotency_key": str(uuid4())})
        assert refused.status in (400, 403, 409), (refused.status, refused.json().get("error"))
        assert refused.json()["error"] in ("no_challenge", "bad_assertion")
        row = self.store.one("SELECT state FROM platform_super_admin_offers WHERE id=?", (offer_id,))
        assert row["state"] == ("pending" if phase == "accept" else "accepted")
        self.mark(f"Real generic admin UV assertion cannot substitute for the offer-specific {phase} ceremony")

    def open_access(self, page, user):
        page.goto(self.origin + "/admin?tab=users")
        page.get_by_role("searchbox", name="Search accounts", exact=True).fill(user["email"])
        row = page.locator("table.admin-users-table tbody tr").filter(has_text=user["email"])
        expect(row).to_have_count(1)
        row.get_by_role("button").click()
        page.get_by_role("menuitem", name="Platform access", exact=True).click()
        dialog = page.get_by_role("dialog", name="Platform access: " + user["email"], exact=True)
        expect(dialog).to_be_visible()
        return dialog

    def ordinary_confirmation(self, page, path, *, reason, fresh=False, passkey=False, expected=200):
        dialog = page.get_by_role("alertdialog")
        expect(dialog.locator("textarea")).to_be_visible()
        for checkbox in dialog.get_by_role("checkbox").all():
            checkbox.check()
        dialog.locator("textarea").fill(reason)
        with self.response(page, path) as executed:
            dialog.locator('button[type="submit"]').click()
        if fresh:
            assert executed.value.status == 403 and executed.value.json()["error"] == "reauth_required"
            with self.response(page, path) as final:
                if passkey:
                    with self.response(page, "/api/auth/reauth/passkey/finish") as proof:
                        dialog.get_by_role("button", name="Confirm with a passkey and continue", exact=True).click()
                else:
                    expect(dialog.locator('input[type="password"]')).to_be_visible()
                    dialog.locator('input[type="password"]').fill(PW)
                    with self.response(page, "/api/auth/reauth") as proof:
                        dialog.locator('button[type="submit"]').click()
            assert proof.value.status == 200 and proof.value.json()["scope"] == "admin"
            executed = final
        assert executed.value.status == expected, (executed.value.status, executed.value.json().get("error"))
        if expected == 200:
            expect(dialog).to_be_hidden()
        return executed.value.json()

    def propose(self, page, recipient, *, fresh=False):
        access = self.open_access(page, recipient)
        with self.response(page, ADMIN_OFFERS + "/preview") as preview:
            access.get_by_role("button", name="Propose super administrator", exact=True).click()
        assert preview.value.status == 200
        return self.ordinary_confirmation(page, ADMIN_OFFERS, reason="Browser fixture: nominate the designated independent recipient", fresh=fresh)["offer"]

    def row(self, page, offer_id, *, area="admin-handover"):
        row = page.locator(f'#{area} article[data-offer-id="{offer_id}"]')
        expect(row).to_be_visible()
        return row

    def dedicated(self, page, offer_id, phase):
        area = "admin-handover" if phase == "accept" else "admin-handover-ready"
        section = self.row(page, offer_id, area=area) if phase == "accept" else page.locator("#admin-handover-ready")
        section.get_by_role("button", name=("Accept with a passkey" if phase == "accept"
            else "Verify readiness with a passkey"), exact=True).click()
        dialog = page.get_by_role("alertdialog")
        expect(dialog.get_by_role("checkbox")).to_be_visible()
        dialog.get_by_role("checkbox").check()
        return dialog

    def ordinary_proof_state(self, user_id):
        return self.store.one("SELECT reauth_at,reauth_scope FROM sessions WHERE user_id=? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1", (user_id,))

    def revoke_dialog(self, page, root):
        access = self.open_access(page, root)
        access.locator("tbody tr").get_by_role("button", name="Revoke", exact=True).click()
        expect(page.get_by_role("alertdialog")).to_be_visible()

    def layouts(self, page, offer_id):
        # Current B4 labels are resolved from the rendered dedicated dialog; no translated
        # text is injected. Keyboard tabbing must stay in that actual modal at every size.
        for locale in ("zh-Hans", "zh-Hant", "en"):
            page.set_viewport_size({"width": 1440, "height": 1000})
            page.locator(".language-menu select").select_option(locale)
            expect(page.locator("html")).to_have_attribute("lang", locale)
            for width in (1440, 390, 320):
                page.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                section = page.locator("#admin-handover")
                section.scroll_into_view_if_needed()
                page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (locale, width)
                assert section.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (locale, width)
                self.capture(page, f"handover-recipient-{locale}-{width}.png")
                # Accept is the first actual action in the pending recipient article.
                opener = self.row(page, offer_id).get_by_role("button").first
                opener.focus(); page.keyboard.press("Enter")
                dialog = page.get_by_role("alertdialog")
                expect(dialog.get_by_role("checkbox")).to_be_visible()
                assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (locale, width)
                for _ in range(16):
                    page.keyboard.press("Tab")
                    assert dialog.evaluate("el => el.contains(document.activeElement)"), (locale, width)
                self.capture(page, f"handover-accept-preview-{locale}-{width}.png")
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                expect(opener).to_be_focused()
                self.report["layouts"].append({"locale": locale, "width": width, "overflow": False, "keyboard_modal": True})
        self.mark("Three languages at desktop 1440 and touch 390/320: real recipient offers and acceptance dialog, horizontal fit, keyboard entry/trap/Escape/return focus")
        page.set_viewport_size({"width": 1440, "height": 1000})


def review_handover(h, root, recipient, unrelated):
    from synwevo_core.platform_access import PlatformAccess
    root_ctx, recipient_ctx, other_ctx = h.context(), h.context(touch=True), h.context()
    root_page, recipient_page, other_page = h.login(root_ctx, root), h.login(recipient_ctx, recipient), h.login(other_ctx, unrelated)
    h.page = recipient_page
    assert h.get(root_ctx, "/healthz")["instance"] == "test"
    assert recipient_page.evaluate("window.isSecureContext")
    cdp, device = h.ceremony_device(recipient_ctx, recipient_page)
    h.register_key(recipient_page, recipient_ctx)
    access = PlatformAccess(h.store)
    root_grant = next(g for g in access.live_grants(root["id"]) if g["role"] == "super_admin")
    revoke_path = f"/api/admin/platform-grants/{root_grant['id']}/revoke"

    h.stage = "create proposal with real ordinary admin confirmation"
    cancelled = h.propose(root_page, recipient, fresh=True)
    offer_id = cancelled["id"]
    assert cancelled["status"] == "pending" and not access.live_grants(recipient["id"])
    recipient_page.reload()
    assert [o["id"] for o in h.get(recipient_ctx, OFFERS)["items"]] == [offer_id]
    assert h.get(other_ctx, OFFERS)["items"] == []
    assert h.request(other_ctx, f"{OFFERS}/{offer_id}/accept/begin", {}).status in (403, 404)
    assert other_ctx.request.get(h.origin + "/api/admin/overview").status == 404
    h.mark("Only the designated recipient sees a real pending proposal; creation grants no authority and unrelated account cannot begin or enter administration")

    h.stage = "cancel a pending browser acceptance without finishing it"
    dialog = h.dedicated(recipient_page, offer_id, "accept")
    h.presence(cdp, device, False)
    with h.response(recipient_page, f"{OFFERS}/{offer_id}/accept/begin") as begun:
        dialog.get_by_role("button", name="Verify and accept", exact=True).click()
    assert begun.value.status == 200
    late = []
    def watch(request):
        if request.method == "POST" and urlsplit(request.url).path == f"{OFFERS}/{offer_id}/accept/finish":
            late.append(True)
    recipient_page.on("request", watch)
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    expect(dialog).to_be_hidden()
    try:
        with recipient_page.expect_request(lambda r: r.method == "POST" and urlsplit(r.url).path == f"{OFFERS}/{offer_id}/accept/finish", timeout=1200):
            h.presence(cdp, device, True)
    except BrowserTimeout:
        pass
    recipient_page.remove_listener("request", watch)
    assert not late and not access.live_grants(recipient["id"])
    assert h.store.one("SELECT state FROM platform_super_admin_offers WHERE id=?", (offer_id,))["state"] == "pending"
    h.mark("Cancelling the actual waiting virtual-authenticator ceremony aborts the browser request, sends no finish and leaves the offer pending")

    h.stage = "proposer cancels its pending offer and stale recipient action is refused"
    root_page.goto(h.origin + "/admin?tab=admins")
    h.row(root_page, offer_id, area="admin-super-offers").get_by_role("button", name="Cancel proposal", exact=True).click()
    closed = h.ordinary_confirmation(root_page, f"{ADMIN_OFFERS}/{offer_id}/cancel", reason="Browser fixture: withdraw the first unused proposal")
    assert closed["offer"]["status"] == "cancelled"
    stale = h.request(recipient_ctx, f"{OFFERS}/{offer_id}/accept/begin", {})
    assert stale.status in (403, 404, 409)
    assert not access.live_grants(recipient["id"])
    h.mark("Actual proposer cancellation invalidates the old offer; a recipient request using its stale ID cannot create a grant")

    h.stage = "new offer, generic proof isolation and localized keyboard layouts"
    offer = h.propose(root_page, recipient)
    offer_id = offer["id"]
    recipient_page.reload()
    h.generic_proof_refused(recipient_page, recipient_ctx, offer_id, "accept")
    h.layouts(recipient_page, offer_id)

    h.stage = "recipient first dedicated UV accepts the proposal"
    before_accept_proof = h.ordinary_proof_state(recipient["id"])
    dialog = h.dedicated(recipient_page, offer_id, "accept")
    with h.response(recipient_page, f"{OFFERS}/{offer_id}/accept/finish") as accepted:
        dialog.get_by_role("button", name="Verify and accept", exact=True).click()
    assert accepted.value.status == 200
    accepted_body = accepted.value.json()
    assert accepted_body["offer"]["status"] == "accepted" and accepted_body["next"] == "admin_ready"
    assert h.ordinary_proof_state(recipient["id"]) == before_accept_proof
    expect(dialog).to_be_hidden()
    assert not access.is_usable_super_admin(recipient["id"]) and len(access.usable_super_admins()) == 1
    recipient_page.locator("#admin-handover").get_by_role("link", name="Continue to administrator center", exact=True).click()
    expect(recipient_page.locator("#admin-handover-ready")).to_be_visible()
    assert h.get(recipient_ctx, f"{ADMIN_OFFERS}/{offer_id}/readiness")["can_confirm"]
    assert h.get(recipient_ctx, "/api/admin/overview")
    h.capture(recipient_page, "recipient-accepted-entered-admin-not-ready.png")
    h.mark("First dedicated browser UV grants accepted authority and actual administrator entry, but recipient does not count as a usable replacement")

    h.stage = "accepted recipient cannot revoke the last ready root"
    h.revoke_dialog(recipient_page, root)
    refused = h.ordinary_confirmation(recipient_page, revoke_path, reason="Browser fixture: verify old root remains protected before readiness", fresh=True, expected=409)
    assert refused["error"] == "last_super_admin"
    h.capture(recipient_page, "old-root-protected-before-readiness.png")
    assert access.is_usable_super_admin(root["id"])
    h.mark("Before readiness, even a separate ordinary admin proof cannot revoke the last usable root")
    recipient_page.get_by_role("alertdialog").get_by_role("button", name="Cancel", exact=True).click()

    h.stage = "real logout and login isolate readiness from previous ordinary confirmation"
    # Log out through the existing UI, never rewrite authentication times or sessions.
    recipient_page.goto(h.origin + "/account")
    recipient_page.locator("header.topbar").get_by_role("button", name="Sign out", exact=True).click()
    recipient_page.wait_for_url(lambda u: urlsplit(str(u)).path in ("/login", "/logged-out"))
    assert recipient_ctx.request.get(h.origin + "/api/auth/me").status == 401
    h.login(recipient_ctx, recipient, page=recipient_page)
    recipient_page.locator("#admin-handover").get_by_role("link", name="Continue to administrator center", exact=True).click()
    expect(recipient_page.locator("#admin-handover-ready")).to_be_visible()
    h.generic_proof_refused(recipient_page, recipient_ctx, offer_id, "ready")

    h.stage = "second dedicated UV records persistent readiness"
    before_ready_proof = h.ordinary_proof_state(recipient["id"])
    dialog = h.dedicated(recipient_page, offer_id, "ready")
    with h.response(recipient_page, f"{ADMIN_OFFERS}/{offer_id}/ready/finish") as ready:
        dialog.get_by_role("button", name="Verify and finish handover", exact=True).click()
    assert ready.value.status == 200 and ready.value.json()["usable_super_admin"] is True
    assert h.ordinary_proof_state(recipient["id"]) == before_ready_proof
    row = h.store.one("SELECT * FROM platform_super_admin_offers WHERE id=?", (offer_id,))
    assert row["state"] == "ready" and row["accepted_challenge_id"] != row["ready_challenge_id"]
    assert row["accepted_session_id"] != row["ready_session_id"]
    assert len(access.usable_super_admins()) == 2
    # Readiness must refresh the adjacent roster and proposal history, preserving its
    # result notice. A stale "one usable" warning would contradict this completed step.
    roster = recipient_page.get_by_role("table", name="Platform administrator roster", exact=True)
    expect(roster).to_have_attribute("aria-busy", "false")
    successor = roster.locator("tbody tr").filter(has_text=recipient["email"])
    expect(successor).to_be_visible()
    expect(successor).not_to_contain_text("Not counted as a usable super administrator")
    expect(h.row(recipient_page, offer_id, area="admin-super-offers").get_by_text("Handover ready", exact=True)).to_be_visible()
    expect(recipient_page.get_by_text("There is only 1 usable super administrator.", exact=False)).to_have_count(0)
    expect(recipient_page.locator("#admin-handover-ready [role=status]")).to_contain_text("Handover ready. Operation:")
    h.mark("Readiness refreshes the actual roster and proposal status while retaining the completion receipt")
    h.capture(recipient_page, "recipient-ready-two-usable-administrators.png")
    h.mark("Second independent dedicated browser UV in a genuinely new session records readiness; both root and recipient now count as usable")

    h.stage = "independent ordinary admin proof is still needed before old grant revocation"
    h.revoke_dialog(recipient_page, root)
    revoked = h.ordinary_confirmation(recipient_page, revoke_path, reason="Browser fixture: complete verified handover by revoking the old root grant", fresh=True, passkey=True)
    assert revoked["ok"]
    assert not access.live_grants(root["id"]) and access.is_usable_super_admin(recipient["id"])
    assert root_ctx.request.get(h.origin + "/api/admin/overview").status == 404
    assert other_ctx.request.get(h.origin + "/api/admin/overview").status == 404
    h.capture(recipient_page, "old-root-revoked-after-separate-admin-proof.png")
    h.mark("Ready proof is not ordinary admin reauthentication: separate actual admin UV is required to revoke old root, whose independent context immediately loses management access")
    h.report["handover"] = {"offer_id": offer_id, "accepted_and_ready_challenges_distinct": True,
                            "new_session_for_readiness": True, "ordinary_scope_and_timestamp_preserved": True,
                            "usable_administrators_after_revocation": 1}
    assert not h.report["page_errors"], h.report["page_errors"]


def run():
    artifacts = Path(__file__).resolve().parents[3] / "output/playwright/admin-handover-review"
    artifacts.mkdir(parents=True, exist_ok=True)
    report = {"facility": "Real isolated HTTP and Chromium CDP virtual CTAP2 UV",
              "limitations": ["Virtual test equipment, not a physical authenticator or biometric test", "Chromium only", "No email delivery"],
              "checks": [], "page_errors": [], "layouts": []}
    h = None
    with tempfile.TemporaryDirectory(prefix="synwevo-handover-browser-fixture-") as tmp, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen(128)
        origin = "http://localhost:" + str(listener.getsockname()[1])
        for key, value in {"DATA_DIR": tmp, "UI_AUTH": "session", "COOKIE_SECURE": "0", "TEST_INSTANCE": "1",
                           "ROLE": "accounts", "MAIL_MODE": "file", "MAIL_DIR": tmp + "/mail", "PUBLIC_URL": origin,
                           "ISSUER": origin, "RP_ID": "localhost", "RP_ORIGINS": origin, "REGISTRATION": "open"}.items():
            os.environ["SYNWEVO_" + key] = value
        from synwevo_identity.app import create_app
        from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
        app = create_app(tmp, start_jobs=False)
        ident, store = app.state.identity, app.state.identity.store
        def fixture(email, name, *, root=False):
            user = ident.create_user(email, PW, display_name=name, status="active")
            store.execute("UPDATE users SET email_verified_at=?,locale='en',timezone='UTC' WHERE id=?", (time.time(), user["id"]))
            if root:
                PlatformAccess(store).host_grant(user["id"], SUPER_ADMIN, source="fixture:existing-handover-root",
                                               reason="Explicit isolated pre-existing administrator fixture")
            return user
        root = fixture("fixture-handover-root@example.test", "Fixture existing root", root=True)
        recipient = fixture("fixture-handover-recipient@example.test", "Fixture designated recipient")
        unrelated = fixture("fixture-handover-unrelated@example.test", "Fixture unrelated account")
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started: break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome", headless=True)
                try:
                    h = Review(browser, origin, store, artifacts, report)
                    review_handover(h, root, recipient, unrelated)
                    report["result"] = "passed"
                    (artifacts / "failure.png").unlink(missing_ok=True)
                except Exception as error:
                    report["result"] = "failed"
                    report["failure"] = {"stage": h.stage if h else "fixture setup", "error": str(error).replace(PW, "[fixture password redacted]")}
                    if h and h.page and not h.page.is_closed(): h.capture(h.page, "failure.png")
                    raise
                finally:
                    browser.close()
        finally:
            server.should_exit = True; thread.join(timeout=5)
            (artifacts / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"artifacts": str(artifacts), **report}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    run()
