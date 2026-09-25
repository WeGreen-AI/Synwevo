"""The copy-approval surfaces on a phone: the requester works at 390 px with touch — project menu,
copy dialog (fits, no sideways scroll, every control reachable), the request's status, the copy
opened in the target with its provenance line, and the platform bell inside Docs with the
job-finished item. The owner approves at desktop width (the phone-sized approval is proven by
ui_e2e_project_copy.py).

    .venv/bin/python services/identity/scripts/ui_e2e_phone_copy.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import build_personal_project, csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "phone-copy"


def no_sideways_scroll(page, where: str) -> None:
    w = page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"{where}: page scrolls sideways ({w[0]} > {w[1]})"


def fits(page, locator, where: str) -> None:
    box = locator.bounding_box()
    vw = page.evaluate("() => window.innerWidth")
    assert box and box["x"] >= -1 and box["x"] + box["width"] <= vw + 1, f"{where}: {box} outside {vw}px"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                    # the owner, at a desk
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)     # the requester, on a phone
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)

    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Phone Org {tag}", "workspace_name": f"Phone Space {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-phone-{tag}"})
    assert r.status == 201, r.text()[:200]
    oid, wid = r.json()["organization"]["id"], r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    pb.goto(link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite"))
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Member {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")
    assert pb.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(b)}).ok
    me = pb.request.get(f"{accounts}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    enter_docs(pb, docs)
    pid = build_personal_project(pb, b, docs, personal, f"手机项目 {tag}")
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": personal}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pb.close(); pb = b.new_page()
    pb.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))

    # ---- the request, from a phone
    pb.goto(f"{docs}/?lang=zh-Hans")
    expect(pb.get_by_text(f"手机项目 {tag}")).to_be_visible()
    no_sideways_scroll(pb, "home")
    pb.get_by_role("button", name=re.compile(f"项目「手机项目 {tag}」的操作")).click()
    pb.get_by_role("menuitem", name=re.compile("复制到组织工作空间")).click()
    dlg = pb.locator(".modal").last
    expect(dlg).to_contain_text(re.compile("原项目保持不变"))
    fits(pb, dlg, "copy dialog")
    no_sideways_scroll(pb, "copy dialog")
    dlg.locator("select").select_option(wid)
    dlg.get_by_role("checkbox").nth(1).check()
    dlg.get_by_label("副本名称").fill(f"手机副本 {tag}")
    submit = dlg.get_by_role("button", name=re.compile("^提交申请$"))
    submit.scroll_into_view_if_needed()
    fits(pb, submit, "submit button")
    pb.screenshot(path=str(SHOTS / f"{engine}-01-dialog.png"))
    submit.click()
    expect(dlg).to_contain_text(re.compile("已有申请"))
    expect(dlg).to_contain_text(re.compile("待审批"))
    pb.screenshot(path=str(SHOTS / f"{engine}-02-requested.png"))
    req = pb.request.get(f"{docs}/api/projects/{pid}/copy-requests", headers={"X-Synwevo-Workspace": personal}).json()["items"][0]
    pb.keyboard.press("Escape")
    notes.append(f"{engine}: at 390 px the project menu, the copy dialog and its submit button fit without sideways scroll; the request shows as pending in the dialog")

    # ---- the owner approves at desk width
    r = pa.request.post(f"{accounts}/api/auth/reauth", data={"password": PW, "scope": "admin"}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.ok, r.text()[:200]
    r = pa.request.post(f"{accounts}/api/auth/organizations/{oid}/approvals/{req['id']}/decide", data={"action": "approve", "reason": "phone check", "version": req["version"]}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.ok and r.json()["status"] == "approved", r.text()[:200]
    for _ in range(200):
        r = pb.request.get(f"{docs}/api/projects/{pid}/copy-requests", headers={"X-Synwevo-Workspace": personal}).json()["items"][0]
        if r["status"] in ("done", "failed", "stale"):
            break
        time.sleep(0.3)
    assert r["status"] == "done", r
    new_pid = r["result"]["project_id"]

    # ---- the copy on the phone: provenance line, then the bell inside Docs after a job
    pb.get_by_role("button", name=re.compile(f"项目「手机项目 {tag}」的操作")).click()
    pb.get_by_role("menuitem", name=re.compile("复制到组织工作空间")).click()
    dlg = pb.locator(".modal").last
    expect(dlg).to_contain_text(re.compile("已完成"))
    dlg.get_by_role("button", name=re.compile("切换到目标空间并打开副本")).click()
    pb.wait_for_url(re.compile(rf"/p/{new_pid}"), timeout=30000)
    prov = pb.locator("[data-testid=copied-from]")
    expect(prov).to_contain_text(re.compile("经审批复制而来"))
    fits(pb, prov, "provenance line")
    no_sideways_scroll(pb, "copied project")
    pb.screenshot(path=str(SHOTS / f"{engine}-03-copy-provenance.png"))
    # phone layout: the source list is behind the「文件」tab; the workbook is already shown as 代表文件
    pb.locator(".pane-switch").get_by_role("tab", name=re.compile("^文件")).click()
    expect(pb.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    pb.get_by_role("button", name="▶ 生成").click()
    gen = pb.locator(".modal")
    expect(gen.get_by_text("有效样本")).to_be_visible(timeout=20000)
    fits(pb, gen, "generate dialog")
    start = gen.get_by_role("button", name=re.compile("开始生成 2 个文件"))
    start.scroll_into_view_if_needed()
    start.click()
    pb.wait_for_url(re.compile(r"/jobs/j_"))
    expect(pb.get_by_text("已完成", exact=True)).to_be_visible(timeout=120000)
    jid = pb.url.rsplit("/", 1)[-1].split("?")[0]
    no_sideways_scroll(pb, "job page")
    pb.goto(f"{docs}/")
    menu = pb.get_by_role("button", name=re.compile("菜单"))
    if menu.count():
        menu.click()                                                    # phone width: the bar's tools live in the drawer
    expect(pb.locator("button.bell .bell-count")).to_have_text(re.compile(r"^[1-9]"), timeout=20000)
    pb.locator("button.bell").click()
    item = pb.locator(".notif-item", has_text=f"手机副本 {tag}").first
    expect(item).to_be_visible()
    # the drawer slides in over 180 ms; measure it once it has settled, not mid-animation
    pb.wait_for_function("() => { const d = document.querySelector('.drawer'); if (!d) return false; const x = d.getBoundingClientRect().x; return new Promise(r => setTimeout(() => r(Math.abs(d.getBoundingClientRect().x - x) < 0.5), 120)); }", timeout=5000)
    fits(pb, pb.locator(".drawer"), "notification drawer")
    fits(pb, item, "notification item")
    pb.screenshot(path=str(SHOTS / f"{engine}-04-bell.png"))
    item.locator("a.notif-title").click()
    pb.wait_for_url(re.compile(rf"/p/{new_pid}/jobs/{jid}"), timeout=20000)
    notes.append(f"{engine}: the copy's provenance line, the generate dialog, the job page and the bell item all fit at 390 px; the bell item opened the job page")
    assert not violations, violations
    a.close(); b.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 3:
        raise SystemExit(__doc__)
    accounts, docs, mail_dir = require_test_instance(args[0]), require_test_instance(args[1]), Path(args[2])

    def show(engine: str, notes: list[str]):
        print(f"\n== {engine}")
        for n in notes:
            print(" -", n)

    with sync_playwright() as p:
        if "--webkit-only" not in sys.argv:
            chrome = p.chromium.launch(channel="chrome", headless=True)
            try:
                show("chromium (Google Chrome)", run(chrome, "chromium", accounts, docs, mail_dir))
            finally:
                chrome.close()
        if "--webkit" in sys.argv or "--webkit-only" in sys.argv:
            with LocalhostProxy() as proxy:
                wk = p.webkit.launch(headless=True)
                try:
                    show("webkit", run(wk, "webkit", accounts, docs, mail_dir, {"proxy": {"server": proxy.url}}))
                finally:
                    wk.close()


if __name__ == "__main__":
    main()
