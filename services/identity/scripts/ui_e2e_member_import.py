"""Bulk member import in a real browser on an isolated instance (file mail): a fresh owner
registers through the mailed link, creates an organization, uploads a generated workbook on a
phone-sized viewport, maps the columns, reads the per-row preview, confirms with re-authentication,
watches the rows run, and the invited newcomer accepts the mailed invitation and lands in the
organization with the role the import named. Nothing here is a demo fixture: every row is the
server's decision.

    .venv/bin/python services/identity/scripts/ui_e2e_member_import.py http://accounts.localhost:31770 <mail dir> [--webkit]

Output never includes tokens, cookies or mail bodies.
"""
from __future__ import annotations

import email
import io
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

expect.set_options(timeout=15000)
PW = "harbor lights guide the evening ferry home"


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def mail_to(mail_dir: Path, address: str, after: float, *, subject_re: str) -> email.message.Message:
    for _ in range(240):                       # the file-mode worker ticks every 15 s when idle
        for p in sorted((p for p in mail_dir.glob("*.eml") if p.stat().st_mtime >= after), key=lambda p: p.stat().st_mtime, reverse=True):
            m = email.message_from_bytes(p.read_bytes())
            subject = str(email.header.make_header(email.header.decode_header(m["Subject"] or "")))
            if (m["To"] or "").strip().lower() == address.lower() and re.search(subject_re, subject):
                return m
        time.sleep(0.25)
    raise AssertionError(f"no mail matching {subject_re!r} to {address}")


def link_in(m: email.message.Message, accounts: str, path: str) -> str:
    text = mail_text(m)
    found = re.search(r"https?://\S+" + re.escape(path) + r"\?lang=[\w-]+#token=[A-Za-z0-9_-]+", text)[0]
    return accounts + found[found.index(path):]


def register_and_activate(page, accounts: str, mail_dir: Path, address: str, name: str) -> None:
    """Public registration → mailed link → mailbox-owner password → active account (signed out)."""
    started = time.time()
    r = page.request.post(f"{accounts}/api/auth/register", data={"email": address, "password": PW, "display_name": name, "locale": "zh-Hans"})
    assert r.status == 202, (r.status, r.text()[:120])
    link = link_in(mail_to(mail_dir, address, started, subject_re="验证|Verify|驗證"), accounts, "/verify-email")
    page.goto(link)
    page.get_by_role("button", name=re.compile("验证此邮箱")).click()
    pw = page.locator("input[type=password]").first
    pw.wait_for(state="visible")
    pw.fill(PW)
    page.get_by_role("button", name=re.compile("设置新口令并验证邮箱")).click()
    expect(page.locator("main, body").first).to_contain_text(re.compile("验证完成"))


def sign_in(page, accounts: str, address: str) -> None:
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label(re.compile("邮箱|Email")).fill(address)
    page.locator("input[type=password]").first.fill(PW)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: "/login" not in u)


def workbook(rows: list[list[str]]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Members"
    for r in rows:
        ws.append(r)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()


def run(browser: Browser, engine: str, accounts: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner = f"owner.{tag}@example.test"
    newcomer = f"newcomer.{tag}@example.test"
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))

    # ---- a fresh owner with an organization of their own
    register_and_activate(page, accounts, mail_dir, owner, "Import Owner")
    sign_in(page, accounts, owner)
    csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "dw_csrf")
    r = page.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Import Org {tag}", "workspace_name": f"Import Space {tag}"},
                          headers={"X-Synwevo-CSRF": csrf, "Idempotency-Key": f"e2e-import-{tag}"})
    assert r.status == 201, (r.status, r.text()[:200])
    oid = r.json()["organization"]["id"]
    assert page.request.post(f"{accounts}/api/auth/reauth", data={"password": PW, "scope": "admin"}, headers={"X-Synwevo-CSRF": csrf}).ok
    role = page.request.post(f"{accounts}/api/auth/organizations/{oid}/roles", data={"name": "Space builder", "permissions": ["org.workspaces.create", "org.members.view"]},
                             headers={"X-Synwevo-CSRF": csrf}).json()["role"]
    notes.append(f"{engine}: fresh owner registered through the mailed link, created an organization and a custom role")

    # ---- the import section, phone width
    page.goto(f"{accounts}/organizations/{oid}?lang=zh-Hans")
    section = page.locator("#org-import")
    expect(section).to_be_visible()
    expect(section).to_contain_text(re.compile("可读取：.*XLSX"))
    section.locator("#import-workspace").select_option(index=1)
    data = workbook([["姓名", "邮箱", "空间角色", "组织角色"],
                     [f"New {tag}", newcomer, "editor", "Space builder"],
                     ["Owner again", owner, "editor", ""],
                     ["Dup", newcomer, "viewer", ""],
                     ["Broken", "not-an-address", "viewer", ""]])
    section.locator("#import-file").set_input_files({"name": "members.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": data})
    section.get_by_role("button", name=re.compile("上传并读取")).click()
    expect(section).to_contain_text(re.compile("members.xlsx · XLSX · 已读取"))
    expect(section).to_contain_text(re.compile("安全检查"))
    page.screenshot(path=str(mail_dir.parent / f"import-read-{engine}.png"))
    # mapping was suggested from the header row; the organization role text must be mapped by hand
    assert section.locator("#import-col-email").input_value() == "1" and section.locator("#import-col-name").input_value() == "0"
    section.get_by_role("button", name=re.compile("生成预览")).click()
    expect(section).to_contain_text(re.compile("阻断 2"))              # unknown role text + bad address
    expect(section).to_contain_text(re.compile("组织角色未映射"))
    fieldset = section.locator("fieldset").filter(has_text=re.compile("组织角色写法"))
    expect(fieldset).to_be_visible()
    fieldset.locator("select").first.select_option(role["id"])
    section.get_by_role("button", name=re.compile("生成预览")).click()
    expect(section).to_contain_text(re.compile("邀请 1"))
    expect(section).to_contain_text(re.compile("阻断 1"))
    expect(section).to_contain_text(re.compile("跳过 2"))               # duplicate + the owner themselves
    page.screenshot(path=str(mail_dir.parent / f"import-preview-{engine}.png"))
    notes.append(f"{engine}: workbook read by the document engine; header mapping suggested; the role text needed an explicit mapping; preview: invite 1, skip 2, blocked 1")

    # ---- confirm: only-valid, reason, re-authentication in the dialog
    section.get_by_role("checkbox", name=re.compile("只导入有效行")).check()
    section.get_by_role("button", name=re.compile("确认导入")).click()
    dialog = page.locator("[role=alertdialog], .modal").last
    expect(dialog).to_be_visible()
    dialog.locator("textarea").fill("browser acceptance of bulk member import, isolated instance")
    dialog.get_by_role("button", name=re.compile("确认|Confirm")).last.click()
    pw = dialog.locator("input[type=password]")
    try:
        pw.first.wait_for(state="visible", timeout=6000)
        pw.first.fill(PW)
        dialog.get_by_role("button", name=re.compile("验证并确认|驗證並確認|Verify and confirm")).click()
    except Exception:
        pass                                                           # the session was already fresh
    started = time.time()
    expect(section).to_contain_text(re.compile("已完成|部分完成"), timeout=40000)
    expect(section).to_contain_text(re.compile("已发出邀请 1"))
    page.screenshot(path=str(mail_dir.parent / f"import-done-{engine}.png"))
    rows = page.request.get(f"{accounts}/api/auth/organizations/{oid}/imports").json()["items"]
    assert rows[0]["status"] == "finished" and rows[0]["summary"]["outcome"]["invited"] == 1, rows[0]["summary"]
    report = page.request.get(f"{accounts}/api/auth/organizations/{oid}/imports/{rows[0]['id']}/report.csv")
    assert report.ok and newcomer in report.text()
    notes.append(f"{engine}: confirmed with a reason and re-authentication; the runner finished (invited 1); the CSV report downloads")
    ctx.close()

    # ---- the newcomer accepts the mailed invitation and lands in the organization with the role
    m = mail_to(mail_dir, newcomer, started - 60, subject_re="邀请|邀請|Join")
    link = link_in(m, accounts, "/invite")
    ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    p2 = ctx2.new_page()
    p2.on("console", lambda mm: violations.append(mm.text[:160]) if "Content Security Policy" in mm.text else None)
    p2.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    p2.goto(link)
    # a person without an account is sent to create one with the invitation (the mailbox is
    # proven by the link; the page then accepts on their behalf and signs them in)
    p2.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    p2.wait_for_url(lambda u: "/register" in u, timeout=15000)
    expect(p2.locator("#register-email")).to_have_value(newcomer)
    p2.locator("#register-name").fill(f"New {tag}")
    p2.locator("input[type=password]").first.fill(PW)
    p2.locator("form button.btn.primary").first.click()
    # the page reports the join and then continues to onboarding on its own
    p2.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    me = p2.request.get(f"{accounts}/api/auth/me").json()
    assert me["email"].lower() == newcomer and me["status"] == "active", me
    perms = p2.request.get(f"{accounts}/api/auth/organizations/{oid}/permissions").json()
    assert set(perms["mine"]) == {"org.workspaces.create", "org.members.view"}, perms["mine"]
    inbox = p2.request.get(f"{accounts}/api/auth/notifications?lang=zh-Hans").json()
    assert any(i["kind"] == "member_import.roles_applied" for i in inbox["items"]), [i["kind"] for i in inbox["items"]]
    notes.append(f"{engine}: the newcomer accepted the mailed invitation, is an organization member with exactly the imported role's permissions, and was told in the inbox")
    ctx2.close()
    assert not violations, violations[:4]
    notes.append(f"{engine}: 0 CSP violations, 0 page errors")
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
