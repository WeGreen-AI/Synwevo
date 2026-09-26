"""A version operation never throws other people's unsaved live edits away unasked (CI-49).

Two real accounts on the isolated instance, in one organization workspace. The editor (on a phone)
has the document template open in the live editor and has typed text nobody saved as a version.
The owner, on the project page:

* opens the template's version history and asks to restore v1 — the dialog says the editor holds
  live edits not yet saved (naming the editor) and the confirmation reads "restore and discard";
  after confirming, the editor's page shows the restored content and a notice naming the owner;
* types-then-undo: the editor types again, the owner presses the template's undo button — the step
  is refused, the toast names the editor and points at the version history, the head is unchanged.

    .venv/bin/python services/identity/scripts/ui_e2e_restore_live.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_collab import SEED, body_editor, note
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "restore-live"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"editor.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                # the owner
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)  # the editor, on a phone
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations)
    except Exception:
        for name, ctx, p in (("a", a, pa), ("b", b, pb)):
            try:
                (ctx.pages[-1] if ctx.pages else p).screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations) -> list[str]:
    # ---- two real accounts in one organization workspace
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Restore Org {tag}", "workspace_name": f"Restore Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-restore-{tag}"})
    assert r.status == 201, r.text()[:200]
    wid = r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    pb.goto(link_in(mail_to(mail_dir, member, started, subject_re="邀请|邀請|Join"), accounts, "/invite"))
    pb.get_by_role("link", name=re.compile("创建账户并继续|建立帳戶並繼續|Create an account")).click()
    pb.wait_for_url(lambda u: "/register" in u)
    pb.locator("#register-name").fill(f"Editor {tag}")
    pb.locator("input[type=password]").first.fill(PW)
    pb.locator("form button.btn.primary").first.click()
    pb.wait_for_function("() => location.pathname.startsWith('/onboarding') || document.body.innerText.includes('已加入工作空间')", timeout=20000)
    try:
        pb.wait_for_url(lambda u: "/onboarding" in u, timeout=10000)
    except Exception:
        pass
    pb.wait_for_load_state("networkidle")

    # ---- the owner: a document template with two versions, and a project that follows it
    enter_docs(pa, docs)
    h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"恢复合同 {tag}"}, headers=h)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]
    v2 = {**SEED, "body": {"type": "doc", "content": SEED["body"]["content"][:-1] + [{"type": "paragraph", "content": [{"type": "text", "text": "第二版备注"}]}]}}
    r = pa.request.put(f"{docs}/api/templates/{tid}/builtin", data={"editor": v2, "base_version": 1}, headers=h)
    assert r.ok and r.json()["version"]["no"] == 2, r.text()[:300]
    r = pa.request.post(f"{docs}/api/projects", data={"name": f"恢复项目 {tag}"}, headers=h)
    assert r.ok, r.text()[:200]
    pid = r.json()["id"]
    assert pa.request.patch(f"{docs}/api/projects/{pid}", data={"template_id": tid}, headers=h).ok
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok

    # ---- the editor, on a phone, is in the live editor and types something nobody saves
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pb.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    ed_b = body_editor(pb)
    expect(ed_b).to_contain_text("第二版备注")
    ed_b.locator("p", has_text="第二版备注").click()
    pb.keyboard.press("End")
    pb.keyboard.type("——手机上未保存的补充")
    expect(pb.locator(".topbar .badge.warn")).to_have_text(re.compile("有未保存为版本的修改"), timeout=10000)
    note(notes, f"{engine}: the editor typed on a phone; the top bar says the live document has edits not saved as a version")

    # ---- the owner opens the project page and the version history: the dialog names the editor before anything happens
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.get_by_role("button", name="版本")).to_be_visible(timeout=20000)
    time.sleep(1.2)                                                   # the room's change log row (1.5 s coalescing) — only used for the "last by" wording
    pa.get_by_role("button", name="版本").click()
    dialog = pa.get_by_role("dialog", name="模板版本")
    expect(dialog).to_be_visible()
    expect(dialog.locator("tr[data-version='2'] .badge", has_text="当前")).to_be_visible()
    dialog.locator("tr[data-version='1']").get_by_role("button", name="恢复此版本").click()
    warning = dialog.locator("[data-testid=restore-live-warning]")
    expect(warning).to_be_visible(timeout=10000)
    expect(warning).to_contain_text("尚未保存为版本的实时修改")
    expect(warning).to_contain_text(f"Editor {tag}")
    confirm = dialog.get_by_role("button", name="确认恢复并丢弃实时修改")
    expect(confirm).to_be_visible()
    # the dialog also lists what those unsaved edits are, in the editor's own words (CI-54)
    fold = dialog.locator("[data-testid=live-changes]")
    expect(fold).to_be_visible()
    expect(fold.locator("summary")).to_contain_text("尚未保存为版本的实时修改")
    fold.locator("summary").click()
    expect(fold.locator(".collab-log li").first).to_contain_text(f"Editor {tag}")
    expect(fold.locator(".collab-log li ins.diff-ins").first).to_contain_text("手机上未保存的补充")
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner-warned-before-restore.png"))
    note(notes, f"{engine}: before restoring v1 the dialog said the editor (by name) holds live edits not saved as a version, listed that edit with its inserted text, and the confirmation reads 'restore and discard'")

    # ---- the owner confirms: v3 is the restored v1; the editor's page follows and is told who did it
    confirm.click()
    expect(pa.locator(".toasts")).to_contain_text(re.compile("已把第 1 版的内容恢复为第 3 版"), timeout=15000)
    expect(body_editor(pb)).not_to_contain_text("手机上未保存的补充", timeout=15000)
    expect(body_editor(pb)).to_contain_text("备注段")
    expect(pb.locator(".toasts")).to_contain_text(re.compile(f"Owner {tag} 已恢复模板版本（现为 v3）"), timeout=15000)
    pb.screenshot(path=str(SHOTS / f"{engine}-02-editor-phone-after-restore.png"))
    detail = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()
    assert detail["head"] == 3 and detail["version"]["no"] == 3, detail["head"]
    note(notes, f"{engine}: after the explicit confirmation v1 became v3 (the head); the editor's phone shows the restored content and a notice naming the owner")

    # ---- a quick undo from the project page never discards live edits: refused, named, pointed at the history
    ed_b = body_editor(pb)
    ed_b.locator("p", has_text="备注段").click()
    pb.keyboard.press("End")
    pb.keyboard.type(" 再次输入")
    expect(pb.locator(".topbar .badge.warn")).to_have_text(re.compile("有未保存为版本的修改"), timeout=10000)
    pa.get_by_role("button", name="撤销模板修改").click()
    expect(pa.locator(".toasts")).to_contain_text(re.compile(f"尚未保存为版本的实时修改（Editor {tag}），这一步没有执行"), timeout=15000)
    pa.screenshot(path=str(SHOTS / f"{engine}-03-owner-undo-refused.png"))
    assert pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()["head"] == 3
    expect(body_editor(pb)).to_contain_text("备注段 再次输入")
    note(notes, f"{engine}: the owner's undo was refused while the editor had unsaved live edits; the toast named the editor and the head stayed at v3, the editor's text intact")

    # ---- machine contract: the same rule for the SDK's route — a plain restore is 409 live_edits, discard_live goes through
    r = pa.request.post(f"{docs}/api/templates/{tid}/restore", data={"version": 2, "base_head": 3}, headers=h)
    assert r.status == 409 and r.json()["error"] == "live_edits" and f"Editor {tag}" in r.json()["detail"]["online"], r.text()[:300]
    note(notes, f"{engine}: the browser API refused a bare restore with 409 live_edits naming the editor while the edits were unsaved")

    assert not violations, violations
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
