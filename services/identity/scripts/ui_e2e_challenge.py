"""Human verification in a real browser against an isolated instance configured with
Cloudflare's *published test keys* (never production keys, never a production host).

What it proves: the account shell loads Cloudflare's widget under the enforced CSP, the widget
hands the page a token, the server verifies that token with Cloudflare's real siteverify
endpoint (the test secret makes Cloudflare answer "pass" or "fail" deterministically), and the
registration, recovery and mailed-code forms behave accordingly in three languages on a phone
viewport. Run once with the passing secret and once with the failing one:

    .venv/bin/python services/identity/scripts/ui_e2e_challenge.py http://accounts.localhost:31770 pass|fail [--webkit]

What Cloudflare's test keys cannot prove (observed 2026-09-25 against the real siteverify):
a token from a test site key comes back with `success: true`, `hostname: "example.com"` and no
`action`, so the server's binding checks refuse it — `reason: action` once `example.com` is
allowed on the isolated instance, `reason: hostname` before that. The passing run therefore ends
in that refusal, which is the evidence: the widget produced a token, the server took it to
Cloudflare, Cloudflare accepted it, and the action binding still held. A full browser happy
path needs a real widget on a real hostname (production keys; the account holder's step).
The failing secret proves the other half: `success: false` → `reason: invalid`.

The instance must report `instance=test` on /healthz. Output never includes tokens or secrets.
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import urllib.request

from playwright.sync_api import Browser, Page, expect, sync_playwright

from localhost_proxy import LocalhostProxy

expect.set_options(timeout=10000)


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def state(base: str) -> dict:
    with urllib.request.urlopen(base + "/api/auth/state", timeout=5) as r:
        return json.loads(r.read().decode())


def run(browser: Browser, engine: str, accounts: str, expect_pass: bool, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    st = state(accounts)
    assert st.get("challenge") and st["challenge"]["provider"] == "turnstile", ("challenge not configured", st.get("challenge"))
    assert "secret" not in json.dumps(st).lower().replace("site_key", ""), "state must never carry the secret"
    notes.append(f"{engine}: state exposes site key ending …{st['challenge']['site_key'][-4:]} and actions {sorted(st['challenge']['actions'])}; no secret")
    tag = secrets.token_hex(3)
    for locale, lang, register_button, solved_hint in (("zh-Hans", "zh-Hans", "创建账户", "人机验证"), ("zh-Hant", "zh-Hant", "建立帳戶", "人機驗證"), ("en", "en", "Create account", "Human verification")):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
        page = ctx.new_page()
        violations: list[str] = []
        cloudflare: list[str] = []
        page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
        page.on("request", lambda r: cloudflare.append(r.url.split("?")[0]) if r.url.startswith("https://challenges.cloudflare.com/") else None)
        api: list[tuple] = []
        page.on("response", lambda r: api.append((r.url.split("/api/")[-1], r.status, r.text()[:300] if r.status >= 400 else "")) if "/api/auth/register" in r.url or "/api/auth/forgot" in r.url else None)
        page.goto(f"{accounts}/register?lang={lang}")
        field = page.locator(".challenge-field")
        expect(field).to_be_visible()
        expect(field.locator(".label")).to_contain_text(re.compile(solved_hint))
        page.wait_for_selector('.challenge-field[data-status="solved"]', timeout=30000)
        # The token came from Cloudflare's widget, not from the page: its script and challenge
        # frame were fetched from the one allowed origin.
        assert any(u.endswith("/turnstile/v0/api.js") for u in cloudflare), cloudflare[:3]
        assert len(cloudflare) >= 2, cloudflare
        email = f"challenge.{locale.lower()}.{tag}@example.test"
        page.locator("#register-name").fill("Challenge " + tag)
        page.locator("#register-email").fill(email)
        for i in range(page.locator("input[type=password]").count()):
            page.locator("input[type=password]").nth(i).fill(f"orchard signal {tag} lantern meadow 2026")
        page.get_by_role("button", name=re.compile(register_button)).click()
        alert = page.locator(".flow-error[role=alert]")
        expect(alert).to_be_visible(timeout=20000)
        text = alert.inner_text()
        assert re.search("人机验证未通过|人機驗證未通過|did not pass", text), text
        assert "/register" in page.url and api and api[-1][1] == 400, api[-1:]
        detail = json.loads(api[-1][2])["detail"]
        want = "action" if expect_pass else "invalid"
        assert detail == {"action": "register", "reason": want}, (detail, api[-1])
        if expect_pass:
            notes.append(f"{engine}/{locale}: widget solved → token sent → Cloudflare siteverify accepted it (test key: hostname example.com, no action) → server refused on the action binding; message in the page's language")
        else:
            notes.append(f"{engine}/{locale}: widget solved → token sent → Cloudflare siteverify rejected it (failing test secret) → challenge_failed/invalid; form stays, message in the page's language")
        assert not violations, violations[:4]
        notes.append(f"{engine}/{locale}: 0 CSP violations, 0 page errors with the widget script and iframe loaded")
        ctx.close()
    # recovery and mailed-code forms show the widget too; no submission (no mail needed)
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, **(ctx_extra or {}))
    page = ctx.new_page()
    page.goto(f"{accounts}/forgot-password?lang=zh-Hans")
    expect(page.locator(".challenge-field")).to_be_visible()
    page.goto(f"{accounts}/login?lang=zh-Hans")
    assert page.locator(".challenge-field").count() == 0, "an ordinary sign-in must not be challenged"
    page.get_by_role("button", name=re.compile("邮箱验证码|驗證碼|code")).first.click()
    expect(page.locator(".challenge-field")).to_be_visible()
    notes.append(f"{engine}: recovery and mailed-code forms show the widget; the password sign-in form does not")
    ctx.close()
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2 or args[1] not in ("pass", "fail"):
        raise SystemExit(__doc__)
    accounts = require_test_instance(args[0])
    expect_pass = args[1] == "pass"
    want_webkit = "--webkit" in sys.argv

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, expect_pass))
        finally:
            chrome.close()
        if want_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, expect_pass, {"proxy": {"server": proxy.url}}))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
