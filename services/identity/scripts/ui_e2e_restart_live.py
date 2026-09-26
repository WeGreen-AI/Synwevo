"""A restart of the Docs service in the middle of live editing loses nothing (CI-51, 9.16 「弱网和多实例」).

Two real accounts on the isolated instance edit the same document template; the owner types; the
Docs service is restarted (`platform.py restart docs`: SIGTERM, uvicorn's graceful shutdown, a new
process on the same data directory) while the editor, on a phone, keeps typing through the outage.
Both editors say they are reconnecting, then are back in the room; the owner sees the text typed
during the outage; a save afterwards produces a version holding everything.

    .venv/bin/python services/identity/scripts/ui_e2e_restart_live.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]

Only ever pointed at the isolated instance: it restarts the service it is given.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
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
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "restart-live"
ROOT = Path(__file__).resolve().parents[3]


def restart_docs() -> float:
    """The real restart path of this instance; returns how long the service was gone."""
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "platform.py"), "restart", "docs"], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout[-400:] + p.stderr[-400:]
    return time.time() - t0


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"editor.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
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
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Restart Org {tag}", "workspace_name": f"Restart Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-restart-{tag}"})
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

    enter_docs(pa, docs)
    h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"重启合同 {tag}"}, headers=h)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok

    # ---- both in the room; the owner types and the editor sees it
    pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pb.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    ed_a = body_editor(pa)
    ed_a.locator("p", has_text="备注段").click()
    pa.keyboard.press("End")
    pa.keyboard.type("——重启前 ABC")
    expect(body_editor(pb)).to_contain_text("备注段——重启前 ABC", timeout=10000)
    note(notes, f"{engine}: both people are in the room; the owner's text reached the editor's phone")

    # ---- the service restarts underneath them; the editor keeps typing through the outage
    gone = restart_docs()
    ed_b = body_editor(pb)
    ed_b.locator("p", has_text="重启前 ABC").click()
    pb.keyboard.press("End")
    pb.keyboard.type(" +断线中输入")
    expect(body_editor(pb)).to_contain_text("重启前 ABC +断线中输入")
    saw_outage = False
    try:
        expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("连接中|同步中|断开|重试"), timeout=8000)
        saw_outage = True
    except AssertionError:
        pass                                                    # it may already be back: the restart takes seconds
    expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=45000)
    expect(pb.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=45000)
    expect(body_editor(pa)).to_contain_text("备注段——重启前 ABC +断线中输入", timeout=15000)
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner-after-restart.png"))
    pb.screenshot(path=str(SHOTS / f"{engine}-02-editor-phone-after-restart.png"))
    note(notes, f"{engine}: the service was restarted (down {gone:.1f} s; the owner's bar {'showed the outage' if saw_outage else 'was back before the check'}); "
                f"both reconnected, and the text the editor typed while the service was down reached the owner")
    # the same room, not a fresh one: the badge still says there are edits not saved as a version
    expect(pa.locator(".topbar .badge.warn")).to_have_text(re.compile("有未保存为版本的修改"), timeout=10000)

    # ---- a save afterwards holds everything, and the other person is told
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（2 个字段，v2）"))).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"Owner {tag} 已把当前内容保存为 v2"))).to_be_visible(timeout=15000)
    saved = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()
    text = str(saved["version"]["editor"])
    assert saved["head"] == 2 and "重启前 ABC +断线中输入" in text, saved["head"]
    note(notes, f"{engine}: the save after the restart became v2 and holds both what was typed before and what was typed during the outage")

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
