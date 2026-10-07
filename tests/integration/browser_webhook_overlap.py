"""Opt-in D4 browser regression: account UI -> composed API -> real subscription storage.

Build account-web, then run with the worktree packages on PYTHONPATH:
    python tests/integration/browser_webhook_overlap.py --artifacts /tmp/synwevo-d4-browser

Real Chromium and password sessions; no intercepted requests or manufactured success responses.
Accounts, keys and subscriptions are explicit temporary fixtures. Workers are stopped, the
receiver URL is loopback only, and no webhook/SMTP/Push is sent. Screenshots mask credentials;
the report records only selected values, request bodies without secrets, and expiry offsets.
This lives in integration because the browser uses Account plus the Docs control-plane adapter.
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

import uvicorn
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PW = "Isolated webhook overlap browser fixture passphrase 2026!"
PAGE = "/admin/api-platform?tab=webhooks"
REQUEST = "/api/admin/api-platform/request"
LABELS = {
    "zh-Hans": ("服务访问令牌或访问密钥", "查看订阅", "轮换签名密钥", "确认操作", "签名密钥重叠期",
                "默认（24 小时）", "已安全保存，关闭", "取消"),
    "en": ("Service access token or API key", "View subscriptions", "Rotate signing secret", "Confirm action",
           "Signing secret overlap", "Default (24 hours)", "Saved securely — close", "Cancel"),
    "zh-Hant": ("服務存取權杖或存取金鑰", "檢視訂閱", "輪換簽章金鑰", "確認操作", "簽章金鑰重疊期",
                "預設（24 小時）", "已安全儲存，關閉", "取消"),
}


def child_pids() -> set[int]:
    """Snapshot only PID/parent pairs, never browser command lines or credentials."""
    pairs = [tuple(map(int, line.split())) for line in subprocess.check_output(
        ["ps", "-axo", "pid=,ppid="], text=True).splitlines() if line.strip()]
    owned = {os.getpid()}
    while children := {pid for pid, parent in pairs if parent in owned} - owned:
        owned.update(children)
    return owned - {os.getpid()}


def run(artifacts: Path, data: Path | None = None):
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    descendants, browser_closed = set(), False
    if data is not None:
        # Refuse an existing directory: this option can never select a running/user database.
        data.mkdir(mode=0o700, parents=True, exist_ok=False)
    data_context = nullcontext(str(data)) if data is not None else tempfile.TemporaryDirectory(prefix="synwevo-d4-fixture-")
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
        from synwevo_core.store import now
        from synwevo_docs.webhooks import DEFAULT_OVERLAP_S, Subscriptions
        from synwevo_server.app import create_app

        # Keep the UI's description honest if the backend default changes later.
        assert DEFAULT_OVERLAP_S == 86400
        app = create_app(tmp, role="all", start_jobs=False)
        ident, store = app.state.identity, app.state.identity.store
        user = ident.create_user("fixture-d4-admin@example.test", PW, display_name="D4 fixture admin", status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        PlatformAccess(store).host_grant(user["id"], "api_platform_admin", source="fixture:d4-browser",
                                        reason="Isolated webhook overlap browser verification")
        workspace = ident.workspaces_for(user["id"])[0]["id"]
        auth = Auth(store)
        token, key = auth.create_key("D4 isolated machine fixture", workspace, ("admin",))
        sub = Subscriptions(store).create(workspace, {"url": f"http://127.0.0.1:{listener.getsockname()[1]}/fixture-receiver",
            "description": "D4 isolated subscription", "events": ["job.completed"]},
            authority.describe({"kind": "api_key", "key_id": key["id"], "tenant": workspace}))
        sid = sub["id"]
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
                        credential, listing, rotate, confirm, overlap, default, close_secret, cancel = labels
                        store.execute("UPDATE users SET locale=? WHERE id=?", (locale, user["id"]))
                        context = browser.new_context(viewport={"width": 1280, "height": 960}, locale=locale)
                        try:
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
                            reauth = context.request.post(origin + "/api/auth/reauth", data={"password": PW, "scope": "admin"},
                                                          headers={"X-Synwevo-CSRF": csrf})
                            assert reauth.status == 200
                            page.get_by_label(credential, exact=True).fill(token)

                            def submit():
                                dialog = page.get_by_role("alertdialog")
                                dialog.locator("textarea").fill("Isolated D4 browser request verification")
                                with page.expect_response(lambda r: urlsplit(r.url).path == REQUEST and r.request.method == "POST") as response:
                                    dialog.get_by_role("button", name=confirm, exact=True).click()
                                return response.value

                            def open_rotation():
                                page.get_by_role("button", name=listing, exact=True).click()
                                listed = submit()
                                assert listed.status == 200 and listed.json()["status"] == 200
                                page.get_by_role("button", name=rotate, exact=True).click()
                                control = page.get_by_role("combobox", name=overlap, exact=True)
                                expect(control).to_have_value("")
                                expect(control.locator('option[value=""]')).to_have_text(default)
                                return control

                            # Cancellation must not rotate or leak the selected value into the next action.
                            control = open_rotation()
                            version = store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"]
                            control.select_option("0")
                            page.get_by_role("alertdialog").get_by_role("button", name=cancel, exact=True).click()
                            assert store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"] == version

                            for selected in (None, 0, 600, 3600, 86400, "reset"):
                                control = open_rotation()
                                if selected == "reset":
                                    control.select_option("0")
                                    control.select_option("")
                                elif selected is not None:
                                    control.select_option(str(selected))
                                else:
                                    for width in (1280, 390):
                                        page.set_viewport_size({"width": width, "height": 960})
                                        dialog = page.get_by_role("alertdialog")
                                        assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                                        assert dialog.locator("code").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                                        dialog.screenshot(path=str(artifacts / f"overlap-{locale}-{width}.png"),
                                                          animations="disabled", mask=[dialog.locator('input[type="password"]')])
                                    page.set_viewport_size({"width": 1280, "height": 960})
                                version = store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"]
                                response = submit()
                                assert response.status == 200
                                request = response.request.post_data_json
                                expected_body = {} if selected in (None, "reset") else {"overlap_s": selected}
                                assert request["path"] == f"/api/v1/webhooks/subscriptions/{sid}/rotate-secret"
                                assert request["method"] == "POST" and request["body"] == expected_body
                                result = response.json()
                                assert result["status"] == 200 and bool(result["data"].get("secret"))
                                row = store.one("SELECT version,updated_at,secret_prev_enc,secret_prev_expires_at "
                                                "FROM webhook_subscriptions WHERE id=?", (sid,))
                                assert row["version"] == version + 1
                                seconds = DEFAULT_OVERLAP_S if selected in (None, "reset") else selected
                                if seconds == 0:
                                    assert row["secret_prev_enc"] is None and row["secret_prev_expires_at"] is None
                                else:
                                    assert row["secret_prev_enc"] and abs(row["secret_prev_expires_at"] - row["updated_at"] - seconds) < .01
                                page.get_by_role("button", name=close_secret, exact=True).click()
                                checks.append({"locale": locale, "selection": selected, "request_body": expected_body,
                                               "stored_overlap_seconds": seconds})
                            if locale == "zh-Hant":
                                # A real withdrawn credential must remain a refusal, never a success dialog.
                                open_rotation().select_option("600")
                                auth.revoke_key(key["id"], "fixture_d4_revoked")
                                version = store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"]
                                denied = submit()
                                assert denied.status == 401
                                expect(page.get_by_role("alertdialog").get_by_role("alert")).to_be_visible()
                                assert store.one("SELECT version FROM webhook_subscriptions WHERE id=?", (sid,))["version"] == version
                        finally:
                            context.close()
                finally:
                    browser.close()
                    browser_closed = not browser.is_connected()
            assert errors == [], errors
            report = {"checks": checks, "cancellation_locales": list(LABELS), "revoked_credential_refused": True,
                      "page_errors": errors, "limits": ["Chromium only", "No external delivery; signing is covered by backend tests"]}
            (artifacts / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"rotation_checks": len(checks), "locales": list(LABELS), "page_errors": len(errors),
                              "artifacts": str(artifacts)}, ensure_ascii=False))
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
    parser.add_argument("--artifacts", type=Path, default=ROOT / "output/playwright/webhook-overlap")
    parser.add_argument("--data", type=Path, help="Retain an isolated fixture in this NEW directory (must not exist)")
    args = parser.parse_args()
    run(args.artifacts, args.data)
