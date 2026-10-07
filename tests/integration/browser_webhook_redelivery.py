"""Opt-in D20: real Account UI -> console adapter -> Docs -> isolated Store.

Build account-web, then run with worktree packages on PYTHONPATH:
    python tests/integration/browser_webhook_redelivery.py --artifacts /tmp/synwevo-d20-browser

The only transport fault forwards a real request, verifies its committed response, then drops
that response. No fabricated successes or auth bypasses. Explicit fixture rows provide one
failed delivery; workers stay stopped and no webhook, SMTP or Push is sent. --data accepts only
a NEW directory. Reports/screenshots contain no credentials. Never run against an existing DB.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PW = "Isolated webhook redelivery browser fixture passphrase 2026!"
PAGE = "/admin/api-platform?tab=webhooks"
REQUEST = "/api/admin/api-platform/request"
LABELS = {
    "zh-Hans": ("服务访问令牌或访问密钥", "查看订阅", "查看投递", "重新投递", "确认操作", "取消",
                "将为这条事件安排一次新的投递。若响应中断，请在此确认框内重试；关闭后重新发起会创建另一次投递。"),
    "en": ("Service access token or API key", "View subscriptions", "View deliveries", "Redeliver", "Confirm action", "Cancel",
           "This schedules a new delivery for this event. If the response is interrupted, retry in this confirmation dialog. Closing it and starting again creates another delivery."),
    "zh-Hant": ("服務存取權杖或存取金鑰", "檢視訂閱", "檢視投遞", "重新投遞", "確認操作", "取消",
                "將為這條事件安排一次新的投遞。若回應中斷，請在此確認框內重試；關閉後重新發起會建立另一次投遞。"),
}
OVERLAP_LABELS = {
    "zh-Hans": ("轮换签名密钥", "签名密钥重叠期", "默认（24 小时）", "已安全保存，关闭"),
    "en": ("Rotate signing secret", "Signing secret overlap", "Default (24 hours)", "Saved securely — close"),
    "zh-Hant": ("輪換簽章金鑰", "簽章金鑰重疊期", "預設（24 小時）", "已安全儲存，關閉"),
}


def child_pids() -> set[int]:
    pairs = [tuple(map(int, line.split())) for line in subprocess.check_output(
        ["ps", "-axo", "pid=,ppid="], text=True).splitlines() if line.strip()]
    owned = {os.getpid()}
    while children := {pid for pid, parent in pairs if parent in owned} - owned:
        owned.update(children)
    return owned - {os.getpid()}


def run(artifacts: Path, data: Path | None = None):
    artifacts.mkdir(parents=True, exist_ok=True)
    if data is not None:
        data.mkdir(mode=0o700, parents=True, exist_ok=False)
    data_context = nullcontext(str(data)) if data is not None else tempfile.TemporaryDirectory(prefix="synwevo-d20-fixture-")
    checks, overlap_checks, errors, descendants, browser_closed = [], [], [], set(), False
    with data_context as tmp, socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        origin = "http://localhost:" + str(listener.getsockname()[1])
        for key, value in {"DATA_DIR": tmp, "UI_AUTH": "session", "COOKIE_SECURE": "0", "TEST_INSTANCE": "1",
                           "ROLE": "all", "MAIL_MODE": "file", "MAIL_DIR": tmp + "/mail", "SCANNER_MODE": "off",
                           "PUBLIC_URL": origin, "APP_URL": origin, "ISSUER": origin, "RP_ID": "localhost",
                           "RP_ORIGINS": origin, "ACCOUNTS_UI_DIR": str(ROOT / "services/identity/web/dist"),
                           "DOCS_WEBHOOK_ALLOW_PRIVATE": "127.0.0.1", "DOCS_WEBHOOK_ALLOW_HTTP": "1"}.items():
            os.environ["SYNWEVO_" + key] = value
        os.environ.pop("SYNWEVO_DOCS_WEBHOOK_URLS", None)
        from synwevo_core import authority
        from synwevo_core.auth import Auth
        from synwevo_core.platform_access import PlatformAccess
        from synwevo_core.store import new_id, now
        from synwevo_docs.webhooks import DEFAULT_OVERLAP_S, Subscriptions
        from synwevo_server.app import create_app

        assert DEFAULT_OVERLAP_S == 86400, "UI default label must match the backend"
        app = create_app(tmp, role="all", start_jobs=False)
        ident, store = app.state.identity, app.state.identity.store
        user = ident.create_user("fixture-d20-admin@example.test", PW, display_name="D20 fixture admin", status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        PlatformAccess(store).host_grant(user["id"], "api_platform_admin", source="fixture:d20-browser",
                                        reason="Isolated webhook redelivery browser verification")
        workspace = ident.workspaces_for(user["id"])[0]["id"]
        token, key = Auth(store).create_key("D20 isolated machine fixture", workspace, ("admin",))
        sub = Subscriptions(store).create(workspace, {"url": f"http://127.0.0.1:{listener.getsockname()[1]}/fixture-receiver",
            "description": "D20 isolated subscription", "events": ["job.completed"]},
            authority.describe({"kind": "api_key", "key_id": key["id"], "tenant": workspace}))
        sid, jid, eid, did, ts = sub["id"], new_id("j"), new_id("ev"), new_id("wh"), now()
        # Explicit fixture: one completed job, its retained event and a failed delivery.
        with store.tx() as c:
            c.execute("INSERT INTO jobs (id,project_id,status,created_at,counts,tenant,run_no) VALUES (?,?,'completed',?,?,?,1)",
                      (jid, "p_fixture", ts, json.dumps({"total": 1, "success": 1}), workspace))
            c.execute("INSERT INTO job_events (id,job_id,tenant,type,version,occurred_at,data) VALUES (?,?,?,'job.completed',1,?,?)",
                      (eid, jid, workspace, ts, json.dumps({"status": "completed", "run_no": 1})))
            c.execute("INSERT INTO webhook_deliveries (id,event_id,endpoint,status,attempts,created_at,subscription_id,tenant,event_type) "
                      "VALUES (?,?,?,'failed',1,?,?,?,'job.completed')", (did, eid, f"sub:{sid}", ts, sid, workspace))
        target = f"/api/v1/webhooks/subscriptions/{sid}/deliveries/{did}/redeliver"

        def delivery_ids():
            return {r["id"] for r in store.all("SELECT id FROM webhook_deliveries WHERE subscription_id=?", (sid,))}

        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="chrome", headless=True)
                try:
                    for locale, labels in LABELS.items():
                        credential, listing, deliveries, redeliver, confirm, cancel, explanation = labels
                        store.execute("UPDATE users SET locale=? WHERE id=?", (locale, user["id"]))
                        context = browser.new_context(viewport={"width": 1280, "height": 960}, locale=locale)
                        try:
                            # Observe real UUID generation without changing its output or timing.
                            context.add_init_script("""(() => {
                              const generate = crypto.randomUUID.bind(crypto);
                              window.__d20Uuids = [];
                              crypto.randomUUID = () => { const key = generate(); window.__d20Uuids.push(key); return key; };
                            })();""")
                            page = context.new_page()
                            page.on("pageerror", lambda error: errors.append(str(error)))
                            page.goto(origin + "/login?next=" + PAGE)
                            page.locator("#login-email").fill(user["email"])
                            page.locator("input[type=password]").fill(PW)
                            page.locator("form button.primary").click()
                            page.wait_for_url(origin + PAGE)
                            descendants.update(child_pids())
                            expect(page.locator("html")).to_have_attribute("lang", locale)
                            csrf = next(c["value"] for c in context.cookies() if c["name"] == "dw_csrf")
                            assert context.request.post(origin + "/api/auth/reauth", data={"password": PW, "scope": "admin"},
                                                        headers={"X-Synwevo-CSRF": csrf}).status == 200
                            page.get_by_label(credential, exact=True).fill(token)
                            attempts, lost = [], []

                            def intercept(route):
                                payload = route.request.post_data_json
                                if payload.get("path") != target:
                                    assert "idempotency_key" not in payload
                                    route.continue_()
                                    return
                                action_key = payload["idempotency_key"]
                                assert UUID(action_key).version == 4
                                # Forward to the real backend, including CSRF and real machine auth.
                                response = route.fetch()
                                answer = response.json()
                                status = answer.get("status", response.status)
                                attempts.append({"key": action_key, "outer_status": response.status, "status": status,
                                                 "delivery_id": answer.get("data", {}).get("id")})
                                if response.status == 200 and status == 202 and not lost:
                                    committed = answer["data"]["id"]
                                    assert committed in delivery_ids()
                                    lost.append(committed)
                                    route.abort("failed")  # Deliberately discard a committed response, once per locale.
                                else:
                                    route.fulfill(response=response)

                            page.route("**" + REQUEST, intercept)
                            dialog = page.get_by_role("alertdialog")

                            def press_confirm():
                                button = dialog.get_by_role("button", name=confirm, exact=True)
                                button.focus()
                                button.press("Enter")

                            def submit_read():
                                dialog.locator("textarea").fill("Isolated D20 browser read verification")
                                with page.expect_response(lambda r: urlsplit(r.url).path == REQUEST and r.request.method == "POST") as response:
                                    press_confirm()
                                assert response.value.status == 200 and response.value.json()["status"] == 200
                                expect(dialog).not_to_be_visible()

                            # D4 compatibility: use only this newly created fixture; never run against old evidence DBs.
                            rotate, overlap, default, close_secret = OVERLAP_LABELS[locale]

                            def open_rotation():
                                page.get_by_role("button", name=listing, exact=True).click()
                                submit_read()
                                page.get_by_role("button", name=rotate, exact=True).click()
                                control = dialog.get_by_role("combobox", name=overlap, exact=True)
                                expect(control).to_have_value("")
                                expect(control.locator('option[value=""]')).to_have_text(default)
                                dialog.locator("textarea").fill("Isolated D4 compatibility verification")
                                return control

                            version = store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"]
                            open_rotation().select_option("0")
                            dialog.get_by_role("button", name=cancel, exact=True).click()
                            expect(dialog).not_to_be_visible()
                            assert store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"] == version
                            for selected in (None, 0):
                                control = open_rotation()
                                if selected is not None:
                                    control.select_option(str(selected))
                                with page.expect_response(lambda r: urlsplit(r.url).path == REQUEST and r.request.method == "POST") as response:
                                    press_confirm()
                                answer = response.value
                                assert answer.status == 200 and answer.json()["status"] == 200
                                assert bool(answer.json()["data"].get("secret"))
                                payload = answer.request.post_data_json
                                assert payload["path"] == f"/api/v1/webhooks/subscriptions/{sid}/rotate-secret"
                                assert payload["body"] == ({} if selected is None else {"overlap_s": 0})
                                assert "idempotency_key" not in payload
                                row = store.one("SELECT version,updated_at,secret_prev_enc,secret_prev_expires_at "
                                                "FROM webhook_subscriptions WHERE id=?", (sid,))
                                version += 1
                                assert row["version"] == version
                                if selected == 0:
                                    assert row["secret_prev_enc"] is None and row["secret_prev_expires_at"] is None
                                else:
                                    assert row["secret_prev_enc"] and abs(row["secret_prev_expires_at"] - row["updated_at"] - DEFAULT_OVERLAP_S) < .01
                                page.get_by_role("button", name=close_secret, exact=True).click()
                                overlap_checks.append({"locale": locale, "selection": selected, "request_body": payload["body"],
                                                       "stored_overlap_seconds": DEFAULT_OVERLAP_S if selected is None else 0,
                                                       "cancelled_without_rotation": True})

                            def open_action():
                                page.get_by_role("button", name=listing, exact=True).click()
                                submit_read()
                                page.get_by_role("button", name=deliveries, exact=True).click()
                                submit_read()
                                before = page.evaluate("window.__d20Uuids.length")
                                button = page.locator("article").filter(has=page.get_by_text(did, exact=True)).get_by_role("button", name=redeliver, exact=True)
                                button.focus()
                                button.press("Enter")
                                expect(dialog).to_be_visible()
                                expect(dialog.get_by_text(explanation, exact=True)).to_be_visible()
                                generated = page.evaluate("window.__d20Uuids")
                                assert len(generated) == before + 1, "A confirmation must generate exactly one UUID at opening"
                                dialog.locator("textarea").fill("Isolated D20 browser redelivery verification")
                                return generated[-1]

                            original = delivery_ids()
                            cancelled_key = open_action()
                            dialog.get_by_role("button", name=cancel, exact=True).click()
                            expect(dialog).not_to_be_visible()
                            assert not attempts and delivery_ids() == original
                            action_key = open_action()
                            assert action_key != cancelled_key
                            for width in (1280, 390):
                                page.set_viewport_size({"width": width, "height": 960})
                                assert dialog.evaluate("e => e.scrollWidth <= e.clientWidth + 1")
                                assert dialog.locator("code").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
                                dialog.screenshot(path=str(artifacts / f"redeliver-{locale}-{width}.png"))
                            # Fixture-only expiry, after admission to the page: exercise real Confirm reauth.
                            store.execute("UPDATE sessions SET reauth_at=0,reauth_scope='' WHERE user_id=?", (user["id"],))
                            press_confirm()
                            expect(dialog.locator("input[type=password]")).to_be_visible()
                            assert len(attempts) == 1 and attempts[0]["status"] == 403
                            assert delivery_ids() == original
                            dialog.locator("input[type=password]").fill(PW)
                            press_confirm()
                            expect(dialog.get_by_role("alert")).to_be_visible()
                            expect(dialog.get_by_role("alert")).to_contain_text("network")
                            assert len(lost) == 1 and delivery_ids() - original == set(lost)
                            # Same open confirmation, now retry its real committed request.
                            press_confirm()
                            expect(dialog).not_to_be_visible()
                            assert len(attempts) == 3
                            assert [x["key"] for x in attempts] == [action_key] * 3
                            assert page.evaluate("window.__d20Uuids.slice(-1)[0]") == action_key
                            assert attempts[1]["delivery_id"] == attempts[2]["delivery_id"] == lost[0]
                            assert delivery_ids() - original == set(lost), "Retry must not create a second delivery"
                            expect(page.locator(".api-platform-json")).to_contain_text(lost[0])
                            next_key = open_action()
                            assert next_key not in (action_key, cancelled_key)
                            press_confirm()
                            expect(dialog).not_to_be_visible()
                            assert len(attempts) == 4 and attempts[-1]["key"] == next_key
                            assert attempts[-1]["status"] == 202 and attempts[-1]["delivery_id"] != lost[0]
                            assert len(delivery_ids() - original) == 2
                            checks.append({"locale": locale, "attempts": attempts, "cancelled_without_request": True,
                                           "new_rows_after_lost_response_retry": 1, "new_rows_after_new_action": 2,
                                           "keyboard": True, "viewport_widths": [1280, 390]})
                        finally:
                            context.close()
                finally:
                    browser.close()
                    browser_closed = not browser.is_connected()
            assert errors == [], errors
            report = {"checks": checks, "d4_overlap_checks": overlap_checks, "page_errors": errors,
                      "limits": ["Chromium only", "No receiver send; tests queue idempotent manual redelivery with workers stopped"]}
            (artifacts / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"locales": list(LABELS), "redelivery_attempts": sum(len(c["attempts"]) for c in checks),
                              "new_deliveries": len(delivery_ids()) - 1, "d4_rotations": len(overlap_checks),
                              "page_errors": len(errors), "artifacts": str(artifacts)}))
        finally:
            server.should_exit = True
            worker.join(10)
            cleanup = {"test_pid": os.getpid(), "server_pid": os.getpid(), "server_thread_stopped": not worker.is_alive(),
                       "browser_closed": browser_closed, "observed_child_pids": sorted(descendants),
                       "remaining_owned_children": sorted(child_pids() & descendants),
                       "retained_fixture_data": str(data) if data is not None else None}
            (artifacts / "cleanup.json").write_text(json.dumps(cleanup, indent=2) + "\n")
            assert not worker.is_alive(), "Fixture server did not stop"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=ROOT / "output/playwright/webhook-redelivery")
    parser.add_argument("--data", type=Path, help="Retain an isolated fixture in this NEW directory (must not exist)")
    args = parser.parse_args()
    run(args.artifacts, args.data)
