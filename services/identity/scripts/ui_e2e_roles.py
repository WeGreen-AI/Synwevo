"""Organization custom roles in a real browser, on an isolated instance: the owner creates a role
whose boxes are limited to what they hold, assigns it to a member, reads the member's effective
permissions; the member then sees exactly the new capability (and not the others).

    .venv/bin/python services/identity/scripts/ui_e2e_roles.py http://accounts.localhost:31770 <org id> <owner email> <owner password> <member email> <member password> [--webkit]

Both accounts must be members of the organization already. Output never includes tokens or cookies.
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


def sign_in(page: Page, accounts: str, email: str, password: str) -> None:
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label(re.compile("邮箱|Email")).fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.get_by_role("button", name=re.compile("^(登录|登入|Sign in)$")).click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)


def fill_reason_and_confirm(page: Page, dialog, password: str | None = None) -> None:
    dialog.locator("textarea").fill("browser acceptance of custom roles, isolated instance")
    dialog.get_by_role("button", name=re.compile("确认|確認|Confirm|保存|儲存|Save|创建|建立|Create|删除|刪除|Delete|分配|指派|Assign")).last.click()
    # the first sensitive write asks for the password once
    # the first sensitive write of a session is refused with reauth_required and the dialog then
    # shows a password field: wait for it rather than checking before the answer arrives
    pw = dialog.locator("input[type=password]")
    if password:
        try:
            pw.first.wait_for(state="visible", timeout=6000)
        except Exception:
            return
        pw.first.fill(password)
        dialog.get_by_role("button", name=re.compile("验证并确认|驗證並確認|Verify and confirm")).click()


def run(browser: Browser, engine: str, accounts: str, oid: str, owner: tuple[str, str], member: tuple[str, str], ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(2)
    role_name = f"Space builder {tag}"
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    page = ctx.new_page()
    violations: list[str] = []
    page.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    page.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))

    # ---- owner: create a role limited to two permissions
    sign_in(page, accounts, *owner)
    page.goto(f"{accounts}/organizations/{oid}?lang=zh-Hans")
    roles = page.locator("#org-roles")
    expect(roles).to_be_visible(timeout=15000)
    roles.get_by_role("button", name=re.compile("创建角色|建立角色|Create role")).click()
    dialog = page.locator("[role=alertdialog], .modal").last
    expect(dialog).to_be_visible()
    dialog.locator("#role-name").fill(role_name)
    # owner-only permissions are shown but disabled
    locked = dialog.locator("#role-perm-org\\.ownership\\.transfer")
    assert locked.is_disabled(), "owner-only permission must not be selectable"
    dialog.locator("#role-perm-org\\.workspaces\\.create").check()
    dialog.locator("#role-perm-org\\.members\\.view").check()
    fill_reason_and_confirm(page, dialog, owner[1])
    try:
        expect(page.locator("#org-roles")).to_contain_text(role_name, timeout=15000)
    except Exception:
        print("[diag] create dialog alert:", dialog.locator(".flow-error, .notice").all_inner_texts()[:3] if dialog.count() else "(closed)",
              "| buttons:", [b.inner_text() for b in dialog.get_by_role("button").all()] if dialog.count() else [],
              "| password field:", dialog.locator("input[type=password]").count() if dialog.count() else 0, flush=True)
        raise
    notes.append(f"{engine}: owner created role “{role_name}” with workspaces.create + members.view; owner-only boxes were disabled")

    # ---- owner: assign it to the member and read the member's effective permissions
    row = page.locator(".org-list li").filter(has_text=member[0]).first
    expect(row).to_be_visible()
    row.get_by_role("button", name=re.compile("分配角色|指派角色|Assign roles")).click()
    dialog = page.locator("[role=alertdialog], .modal").last
    try:
        dialog.locator("label.check").filter(has_text=role_name).locator("input[type=checkbox]").check(timeout=10000)
    except Exception:
        print("[diag] assign dialog:", dialog.inner_text()[:400].replace("\n", " | ") if dialog.count() else "(none)", flush=True)
        raise
    fill_reason_and_confirm(page, dialog, owner[1])
    row = page.locator(".org-list li").filter(has_text=member[0]).first
    expect(row).to_contain_text(role_name, timeout=15000)
    row.get_by_role("button", name=re.compile("查看权限|檢視權限|View permissions")).click()
    eff = page.locator(".modal").last
    expect(eff).to_be_visible()
    expect(eff).to_contain_text(re.compile("创建工作空间|建立工作空間|Create workspaces"))
    expect(eff).to_contain_text(role_name)
    assert not re.search("管理组织设置|管理組織設定|Manage organization settings", eff.inner_text()), "no settings permission was granted"
    page.keyboard.press("Escape")
    notes.append(f"{engine}: assigned; the effective-permissions view lists the two permissions and names the role as their source")
    api = page.request.get(f"{accounts}/api/auth/organizations/{oid}/members").json()
    target = next(m for m in api["items"] if m["email"] == member[0])
    assert [r["name"] for r in target["custom_roles"]] == [role_name], target["custom_roles"]
    ctx.close()

    # ---- member: can create a workspace now, cannot touch settings or roles
    ctx2 = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **(ctx_extra or {}))
    p2 = ctx2.new_page()
    p2.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    p2.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    sign_in(p2, accounts, *member)
    p2.goto(f"{accounts}/organizations/{oid}?lang=zh-Hans")
    p2.wait_for_selector("#org-roles, .org-section", timeout=15000)
    mine = p2.request.get(f"{accounts}/api/auth/organizations/{oid}/permissions").json()
    assert set(mine["mine"]) == {"org.workspaces.create", "org.members.view"}, mine["mine"]
    assert p2.locator("#org-roles").count() == 1, "members.view shows the roles list read-only"
    assert p2.locator("#org-roles").get_by_role("button", name=re.compile("创建角色|建立角色|Create role")).count() == 0
    assert p2.get_by_role("button", name=re.compile("编辑组织设置|編輯組織設定|Edit organization settings")).count() == 0, "no settings entry without settings.manage"
    csrf = next(c["value"] for c in ctx2.cookies() if c["name"] == "dw_csrf")
    r = p2.request.post(f"{accounts}/api/auth/organizations/{oid}/workspaces", data={"name": f"Built by role {tag}"}, headers={"X-Synwevo-CSRF": csrf})
    assert r.status == 201, (r.status, r.text()[:160])
    r2 = p2.request.patch(f"{accounts}/api/auth/organizations/{oid}", data={"name": "nope", "reason": "trying to rename without permission"}, headers={"X-Synwevo-CSRF": csrf})
    assert r2.status == 403, (r2.status, r2.text()[:160])
    inbox = p2.request.get(f"{accounts}/api/auth/notifications?lang=en").json()
    assert any(i["kind"] == "organization.roles_assigned" and role_name in i["body"] for i in inbox["items"]), [i["kind"] for i in inbox["items"]][:5]
    notes.append(f"{engine}: member holds exactly the two permissions, created a workspace (201), was refused a rename (403), and found the assignment in the inbox in English")
    ctx2.close()

    # ---- owner: delete the role (assigned → the dialog says so and unassigns)
    ctx3 = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **(ctx_extra or {}))
    p3 = ctx3.new_page()
    sign_in(p3, accounts, *owner)
    p3.goto(f"{accounts}/organizations/{oid}?lang=zh-Hans")
    item = p3.locator("#org-roles .org-list li").filter(has_text=role_name).first
    expect(item).to_be_visible(timeout=15000)
    item.get_by_role("button", name=re.compile("^(删除|刪除|Delete)$")).click()
    dialog = p3.locator("[role=alertdialog], .modal").last
    expect(dialog).to_contain_text(re.compile("1 位成员|1 位成員|1 member"))
    fill_reason_and_confirm(p3, dialog, owner[1])
    expect(p3.locator("#org-roles")).not_to_contain_text(role_name, timeout=15000)
    after = p3.request.get(f"{accounts}/api/auth/organizations/{oid}/members/{target['user_id']}/permissions").json()
    assert after["permissions"] == [] and after["roles"] == [], after
    notes.append(f"{engine}: deleting the assigned role said 1 member was affected and left the member with no organization permissions")
    ctx3.close()
    assert not violations, violations[:4]
    notes.append(f"{engine}: 0 CSP violations, 0 page errors")
    return notes


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 6:
        raise SystemExit(__doc__)
    accounts = require_test_instance(args[0])
    oid, owner, member = args[1], (args[2], args[3]), (args[4], args[5])
    want_webkit = "--webkit" in sys.argv

    def show(engine: str, notes: list[str]):
        print(f"== {engine}", flush=True)
        for n in notes:
            print("  -", n, flush=True)

    with sync_playwright() as p:
        chrome = p.chromium.launch(channel="chrome", headless=True)
        try:
            show("chromium (Google Chrome)", run(chrome, "chromium", accounts, oid, owner, member))
        finally:
            chrome.close()
        if want_webkit:
            wk = p.webkit.launch(headless=True)
            try:
                with LocalhostProxy() as proxy:
                    show(f"webkit (Playwright build; *.localhost through {proxy.url})",
                         run(wk, "webkit", accounts, oid, owner, member, {"proxy": {"server": proxy.url}}))
            finally:
                wk.close()


if __name__ == "__main__":
    main()
