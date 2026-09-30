"""Opt-in real-browser review of versioned message digest settings.

Build account-web, then run from the repository root:
    .venv/bin/python -m services.identity.tests.browser_admin_settings_review

Real isolated account server, four independent signed-in browser contexts and actual HTTP;
no intercepted routes, manufactured sessions, authentication timestamps or signed previews.
Explicit temporary fixtures create accounts/roles/messages and age notification timestamps
instead of waiting thirty minutes. Outbox enqueue is verified; SMTP delivery is not claimed.
Only screenshots and a redacted report remain under ignored output/playwright.
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
from playwright.sync_api import expect, sync_playwright

PW = "Isolated settings browser review fixture passphrase 2026!"
INTERVAL = "notifications.conversation_digest_interval_s"
CAP = "notifications.conversation_digest_daily_cap"
LABELS = {
    "en": ("Preview settings changes", "Cancel"),
    "zh-Hans": ("预览配置差异", "取消"),
    "zh-Hant": ("預覽設定差異", "取消"),
}


def run():
    artifacts = Path(__file__).resolve().parents[3] / "output/playwright/admin-settings-review"
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, page_errors, layouts = [], [], []
    report = {"facility": "Real Chromium, isolated account server, real HTTP and SQLite outbox",
              "limitations": ["Chromium only", "Temporary message age fixtures replace elapsed aggregation time",
                              "Queued means committed outbox, not SMTP delivery"],
              "checks": checks, "page_errors": page_errors, "layouts": layouts}
    stage, page = "isolated fixtures", None
    with tempfile.TemporaryDirectory(prefix="synwevo-settings-browser-fixture-") as tmp, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = "http://localhost:" + str(listener.getsockname()[1])
        for key, value in {"DATA_DIR": tmp, "UI_AUTH": "session", "COOKIE_SECURE": "0", "TEST_INSTANCE": "1",
                           "ROLE": "accounts", "MAIL_MODE": "file", "MAIL_DIR": tmp + "/mail",
                           "PUBLIC_URL": origin, "ISSUER": origin, "RP_ID": "localhost",
                           "RP_ORIGINS": origin, "REGISTRATION": "open"}.items():
            os.environ["SYNWEVO_" + key] = value
        from synwevo_core.identity import Identity
        from synwevo_core.messaging import Messaging
        from synwevo_core.notifications import Notifier
        from synwevo_core.organizations import Organizations
        from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
        from synwevo_core.store import Store, now
        from synwevo_identity.app import create_app
        from synwevo_identity.mail import Mailer
        from synwevo_identity.notify import NotificationDispatcher
        app = create_app(tmp, start_jobs=False)
        ident, store = app.state.identity, app.state.identity.store

        def fixture(email, name, role=None):
            user = ident.create_user(email, PW, display_name=name, status="active")
            store.execute("UPDATE users SET email_verified_at=?,locale='en',timezone='UTC' WHERE id=?", (now(), user["id"]))
            if role:
                PlatformAccess(store).host_grant(user["id"], role, source="fixture:admin-settings-browser",
                    reason="Explicit isolated settings browser verification fixture")
            return user

        admin = fixture("fixture-settings-admin-a@example.test", "Fixture settings administrator A", SUPER_ADMIN)
        other = fixture("fixture-settings-admin-b@example.test", "Fixture settings administrator B", SUPER_ADMIN)
        auditor = fixture("fixture-settings-auditor@example.test", "Fixture settings auditor", "auditor")
        member = fixture("fixture-settings-member@example.test", "Fixture settings member")
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        dispatcher = None
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
                        ctx.on("page", lambda p: p.on("pageerror", lambda e: page_errors.append(str(e))))
                        return ctx

                    def login(ctx, user, destination="/admin?tab=registration", locale="en"):
                        p = ctx.new_page()
                        p.goto(origin + "/login?next=" + destination)
                        p.locator("#login-email").fill(user["email"])
                        p.locator("input[type=password]").fill(PW)
                        p.locator("form button.primary").click()
                        p.wait_for_url(origin + destination)
                        expect(p.locator("html")).to_have_attribute("lang", locale)
                        return p

                    def csrf(ctx):
                        return {"X-Synwevo-CSRF": next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")}

                    def post(ctx, path, body):
                        return ctx.request.post(origin + path, data=body, headers=csrf(ctx))

                    def response(p, path, method="POST"):
                        return p.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == method)

                    def policy(p):
                        return p.locator('section[aria-labelledby="settings-heading"]')

                    def history(p):
                        return p.locator('section[aria-labelledby="settings-history-heading"]')

                    def edit(p, interval, cap):
                        box = policy(p)
                        expect(box.locator('input[type="number"]')).to_have_count(2)
                        for index, value in enumerate((interval, cap)):
                            box.locator('input[type="checkbox"]').nth(index).uncheck()
                            box.locator('input[type="number"]').nth(index).fill(str(value))

                    def capture(p, name):
                        p.screenshot(path=str(artifacts / name), full_page=False, animations="disabled",
                                     mask=[p.locator('input[type="password"]')])

                    def within_viewport(p, locale, width, surface, dialog=None):
                        p.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
                        sizes = p.evaluate("({viewport:innerWidth, document:document.documentElement.scrollWidth})")
                        assert sizes["document"] <= sizes["viewport"] + 1, (locale, width, surface, sizes)
                        target = dialog or policy(p)
                        assert target.evaluate("el => el.scrollWidth <= el.clientWidth + 1"), (locale, width, surface)
                        assert target.locator('input:not([hidden]), textarea, button').evaluate_all(
                            "nodes => nodes.every(el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; })"), (locale, width, surface)
                        layouts.append({"language": locale, "width": width, "surface": surface, "horizontal_overflow": False})

                    def publish_ui(p, reason, *, fresh=False, validate_reason=False):
                        dialog = p.get_by_role("alertdialog")
                        expect(dialog).to_be_visible()
                        expect(dialog.locator('button[type="submit"]')).to_be_disabled()
                        dialog.get_by_role("checkbox").check()
                        if validate_reason:
                            dialog.locator('button[type="submit"]').click()
                            expect(dialog.locator("textarea")).to_have_attribute("aria-invalid", "true")
                            assert store.one("SELECT COUNT(*) AS n FROM platform_settings_versions")["n"] == 0
                        dialog.locator("textarea").fill(reason)
                        with response(p, "/api/admin/settings/publish") as attempt:
                            dialog.locator('button[type="submit"]').click()
                        if fresh:
                            assert attempt.value.status == 403 and attempt.value.json()["error"] == "reauth_required"
                            expect(dialog.locator('input[type="password"]')).to_be_visible()
                            capture(p, "settings-real-password-confirmation.png")
                            dialog.locator('input[type="password"]').fill(PW)
                            with response(p, "/api/auth/reauth") as proof, response(p, "/api/admin/settings/publish") as executed:
                                dialog.locator('button[type="submit"]').click()
                            assert proof.value.status == 200 and proof.value.json()["scope"] == "admin"
                            attempt = executed
                        assert attempt.value.status == 200, (attempt.value.status, attempt.value.json().get("error"))
                        payload, out = attempt.value.request.post_data_json, attempt.value.json()
                        expect(dialog).to_be_hidden()
                        expect(policy(p).get_by_role("status")).to_contain_text(out["operation_id"])
                        return out, payload

                    admin_ctx, other_ctx, auditor_ctx, member_ctx = context(), context(), context(), context()
                    page, other_page = login(admin_ctx, admin), login(other_ctx, other)
                    audit_page = login(auditor_ctx, auditor)
                    member_page = login(member_ctx, member, "/account")
                    assert admin_ctx.request.get(origin + "/healthz").json()["instance"] == "test"
                    expect(policy(page).get_by_role("button", name="Preview settings changes", exact=True)).to_be_disabled()
                    expect(policy(audit_page)).to_contain_text("Your platform role can view settings but cannot publish changes.")
                    assert auditor_ctx.request.get(origin + "/api/admin/settings").status == 200
                    assert post(auditor_ctx, "/api/admin/settings/preview", {"expected_revision": 0, "changes": {CAP: 2}}).status == 403
                    for path in ("/api/admin/settings", "/api/admin/settings/history"):
                        assert member_ctx.request.get(origin + path).status == 404
                    assert post(member_ctx, "/api/admin/settings/preview", {"expected_revision": 0, "changes": {CAP: 2}}).status == 404
                    assert policy(audit_page).locator('input[type="number"]').evaluate_all("nodes => nodes.every(el => el.disabled)")
                    capture(audit_page, "settings-auditor-read-only.png")
                    checks.append("Two independent administrators, read-only auditor and ordinary account use real password logins; server rejects unauthorized configuration access")

                    stage = "ordinary opt-in and real pending outbox fixtures"
                    prefs_path = "/api/auth/notifications/preferences"
                    initial = member_ctx.request.get(origin + prefs_path).json()
                    assert initial["categories"]["conversation"]["email"] == "off"
                    with response(member_page, prefs_path, "PUT") as opted_in:
                        member_page.get_by_role("combobox", name="Messages: e-mail mode", exact=True).select_option("digest")
                    assert opted_in.value.status == 200
                    org = Organizations(store, ident).create(admin["id"], "Fixture settings organization", "Fixture space", "fixture-settings-browser")
                    oid = org["organization"]["id"]
                    for participant in (member, other, auditor):
                        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',?,?,?)",
                                      (oid, participant["id"], now(), now(), admin["id"]))
                    messaging, notifier, fixture_mailer = Messaging(store, ident), Notifier(store), Mailer(store, start=False)

                    def unread_fixture(sender, *, aged=False):
                        cid = messaging.open_dm(oid, sender["id"], member["id"])["id"]
                        messaging.send(cid, sender["id"], body="Explicit temporary browser review unread message")
                        item = notifier.emit(member["id"], "conversation.dm", params={"sender": "Fixture sender"},
                                             organization_id=oid, object_kind="conversation", object_id=cid)
                        if aged:
                            # Only these named temporary items have finished initial aggregation.
                            store.execute("UPDATE notifications SET created_at=? WHERE id=?", (now() - 3600, item["id"]))
                            store.execute("UPDATE notification_deliveries SET not_before=? WHERE notification_id=?", (now() - 1, item["id"]))
                        return item
                    prior = notifier.emit(member["id"], "conversation.added", params={"conversation": "Fixture history"})
                    prior_mail = fixture_mailer.enqueue(member["email"], "Fixture prior digest", "Temporary fixture only", kind="notification", ref_id=prior["id"])
                    notifier.finish_delivery(prior["id"], "queued", mail_id=prior_mail["id"])
                    store.execute("UPDATE notification_deliveries SET updated_at=? WHERE notification_id=?", (now() - 600, prior["id"]))
                    pending = unread_fixture(admin, aged=True)
                    combined = unread_fixture(other, aged=True)
                    initial_window = unread_fixture(auditor)
                    worker_store = Store(store.root)
                    dispatcher = NotificationDispatcher(worker_store, Identity(worker_store), Mailer(worker_store, start=False), start=False)
                    initial_dispatch = dispatcher.run_once()
                    assert initial_dispatch["queued"] == 0, initial_dispatch
                    waiting = store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (pending["id"],))
                    untouched = store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (initial_window["id"],))
                    assert waiting["defer_reason"] == "budget" and waiting["policy_revision"] == 0, (initial_dispatch, waiting, notifier.preferences(member["id"]))
                    assert untouched["defer_reason"] == "aggregation"
                    edit(other_page, 600, 9)  # Preserve a genuine concurrent draft based on version zero.

                    stage = "first UI publish with written reason and fresh admin verification"
                    edit(page, 300, 2)
                    with response(page, "/api/admin/settings/preview") as previewed:
                        policy(page).get_by_role("button", name="Preview settings changes", exact=True).click()
                    assert previewed.value.status == 200
                    assert store.one("SELECT COUNT(*) AS n FROM platform_settings_versions")["n"] == 0
                    capture(page, "settings-first-publish-preview.png")
                    first, first_body = publish_ui(page, "Browser fixture: relax unread digest interval to five minutes", fresh=True, validate_reason=True)
                    assert first["revision"] == 1 and first["state"]["values"] == {INTERVAL: 300, CAP: 2}
                    assert store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (pending["id"],)) == waiting
                    dispatch_result = dispatcher.run_once()
                    assert dispatch_result["queued"] == 2 and dispatch_result["digested"] == 1
                    delivered = store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (pending["id"],))
                    assert delivered["status"] == "queued" and delivered["policy_revision"] == 1 and delivered["mail_id"]
                    combined_delivery = store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (combined["id"],))
                    assert combined_delivery["mail_id"] == delivered["mail_id"] and combined_delivery["policy_revision"] == 1
                    assert store.one("SELECT id FROM mail_outbox WHERE id=?", (delivered["mail_id"],))
                    assert store.one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (initial_window["id"],)) == untouched
                    assert dispatcher.run_once()["queued"] == 0
                    report["outbox"] = {"wait_revision": 0, "queued_revision": 1, "initial_aggregation_preserved": True, "combined_unread_conversations": 2,
                                        "notification_mail_count": store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='notification'")["n"],
                                        "smtp_delivery_claimed": False}
                    checks.append("UI preview is read-only; acknowledgement, reason and real admin password verification publish version 1; existing second-Store dispatcher rechecks old budget wait and combines two unread conversations into one outbox mail while a third initial aggregation stays pending")

                    stage = "safe ordinary preference values and exact idempotent retry"
                    summary = member_ctx.request.get(origin + prefs_path).json()["conversation_digest_policy"]
                    assert summary == {"revision": 1, "interval_s": 300, "daily_cap": 2, "aggregation_window_s": 1800, "window_s": 86400}
                    denied = member_ctx.request.put(origin + prefs_path, data={"conversation_digest_policy": {"daily_cap": 100}}, headers=csrf(member_ctx))
                    assert denied.status == 400
                    member_page.reload()
                    expect(member_page.locator("#notifications")).to_contain_text("at least 300 seconds")
                    expect(member_page.locator("#notifications")).to_contain_text("2 emails")
                    member_page.locator("#notifications").scroll_into_view_if_needed()
                    capture(member_page, "ordinary-effective-digest-budget.png")
                    retry = post(admin_ctx, "/api/admin/settings/publish", first_body)
                    assert retry.status == 200 and retry.json() == first
                    conflicting_retry = post(admin_ctx, "/api/admin/settings/publish", {**first_body, "reason": "Browser fixture: altered request must not replay"})
                    assert conflicting_retry.status == 409 and conflicting_retry.json()["error"] == "idempotency_conflict"
                    assert store.one("SELECT COUNT(*) AS n FROM platform_settings_versions")["n"] == 1
                    assert store.one("SELECT COUNT(*) AS n FROM admin_operations WHERE action='admin.settings.publish'")["n"] == 1
                    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action='admin.settings.publish' AND result='ok'")["n"] == 1
                    checks.append("Ordinary preferences expose only current revision and effective limits, cannot change policy; exact actual browser request replay returns original operation with one version and success audit")

                    stage = "competing administrator conflict preserves and rebases input"
                    with response(other_page, "/api/admin/settings/preview") as conflict:
                        policy(other_page).get_by_role("button", name="Preview settings changes", exact=True).click()
                    assert conflict.value.status == 409 and conflict.value.json()["error"] == "settings_revision_conflict"
                    expect(policy(other_page)).to_contain_text("Your input has been kept.")
                    expect(policy(other_page).locator('input[type="number"]').nth(0)).to_have_value("600")
                    expect(policy(other_page).locator('input[type="number"]').nth(1)).to_have_value("9")
                    capture(other_page, "settings-conflict-keeps-input.png")
                    policy(other_page).get_by_role("button", name="Keep my input and compare with the latest version", exact=True).click()
                    policy(other_page).get_by_role("button", name="Preview settings changes", exact=True).click()
                    second, _ = publish_ui(other_page, "Browser fixture: publish independently rebased digest limits", fresh=True)
                    assert second["revision"] == 2 and second["state"]["values"] == {INTERVAL: 600, CAP: 9}
                    checks.append("Second administrator's stale version is rejected, both draft fields stay intact, explicit rebase and new preview publish version 2")

                    stage = "UI history rollback and default restoration"
                    page.reload()
                    first_version = history(page).locator("article").filter(has=page.get_by_text("Version 1", exact=True))
                    first_version.get_by_role("button", name="Preview restoring this version", exact=True).click()
                    restored, _ = publish_ui(page, "Browser fixture: restore the first reviewed digest policy")
                    assert restored["revision"] == 3 and restored["state"]["values"] == first["state"]["values"]
                    history(page).get_by_role("button", name="Preview restoring all defaults", exact=True).click()
                    reset, _ = publish_ui(page, "Browser fixture: restore inherited built-in digest defaults")
                    assert reset["revision"] == 4 and reset["state"]["overrides"] == {}
                    assert reset["state"]["values"] == {INTERVAL: 1800, CAP: 8}
                    assert store.one("SELECT rollback_revision FROM platform_settings_versions WHERE revision=3")["rollback_revision"] == 1
                    assert store.one("SELECT rollback_revision FROM platform_settings_versions WHERE revision=4")["rollback_revision"] == 0
                    expect(history(page).locator("article")).to_have_count(4)
                    assert member_ctx.request.get(origin + prefs_path).json()["categories"]["conversation"]["email"] == "digest"
                    assert member_ctx.request.get(origin + prefs_path).json()["conversation_digest_policy"]["revision"] == 4
                    capture(page, "settings-restored-defaults-with-history.png")
                    checks.append("History rollback appends version 3; restoring inherited defaults appends version 4 and retains all four records plus ordinary opt-in")

                    stage = "three-language desktop and touch layouts with real preview dialogs"
                    for locale in ("zh-Hans", "zh-Hant", "en"):
                        page.locator(".language-menu select").select_option(locale)
                        expect(page.locator("html")).to_have_attribute("lang", locale)
                        mobile_ctx = context(touch=True)
                        mobile = login(mobile_ctx, admin, locale=locale)
                        for width in (1440, 390, 320):
                            screen = page if width == 1440 else mobile
                            screen.set_viewport_size({"width": width, "height": 1000 if width == 1440 else 844})
                            # Admin content scrolls inside #admin-main. Scrolling a large policy
                            # card into view hides its breadcrumb/title above the viewport.
                            screen.locator("#admin-main").evaluate("node => {node.scrollTop=0;node.scrollLeft=0;}")
                            screen.evaluate("window.scrollTo(0,0)")
                            within_viewport(screen, locale, width, "settings")
                            capture(screen, f"settings-{locale}-{width}.png")
                            edit(screen, 900, 7)
                            policy(screen).get_by_role("button", name=LABELS[locale][0], exact=True).click()
                            dialog = screen.get_by_role("alertdialog")
                            expect(dialog.get_by_role("checkbox")).to_be_visible()
                            within_viewport(screen, locale, width, "preview", dialog)
                            capture(screen, f"settings-preview-{locale}-{width}.png")
                            dialog.get_by_role("button", name=LABELS[locale][1], exact=True).click()
                            expect(dialog).to_be_hidden()
                        mobile_ctx.close()
                        checks.append(f"{locale}: settings and real preview dialog at desktop 1440 and touch 390/320, no horizontal overflow; cancelled previews create no versions")
                    assert store.one("SELECT COUNT(*) AS n FROM platform_settings_versions")["n"] == 4
                    successes = store.all("SELECT seq,meta FROM audit_events WHERE action='admin.settings.publish' AND result='ok' ORDER BY seq")
                    assert len(successes) == 4
                    report["publication_audit"] = [{"seq": r["seq"], "revision": json.loads(r["meta"])["after"]["revision"]} for r in successes]
                    assert not page_errors, page_errors
                    report["result"] = "passed"
                    (artifacts / "failure.png").unlink(missing_ok=True)
                except Exception as error:
                    report["result"] = "failed"
                    report["failure"] = {"stage": stage, "error": str(error).replace(PW, "[fixture password redacted]")}
                    if page is not None and not page.is_closed():
                        capture(page, "failure.png")
                    raise
                finally:
                    browser.close()
        finally:
            if dispatcher:
                dispatcher.stop()
                dispatcher.mailer.stop()
            server.should_exit = True
            worker.join(timeout=5)
            (artifacts / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps({"artifacts": str(artifacts), **report}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    run()
