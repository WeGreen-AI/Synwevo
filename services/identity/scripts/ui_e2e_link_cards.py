"""Docs link cards in messages, resolved per reader: the owner shares links to a team project, a
job on it and a personal draft in a direct message; the member (on a phone) sees cards for the
team project and the job — title, workspace, outcome — and only a plain link for the draft
they cannot open. The owner sees all three cards. Isolated instance, file mail.

Since CI-66 the conversations live on Synwevo Message's own origin (reached through its sign-in
start from an account-origin session).

    .venv/bin/python services/identity/scripts/ui_e2e_link_cards.py http://accounts.localhost:31770 \
        http://docweave.localhost:31765 http://message.localhost:31768 <mail dir> [--webkit]
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
from ui_e2e_messaging import PW, csrf_of, enter_messages, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import build_personal_project, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "link-cards"


def send(page, text: str) -> None:
    box = page.locator(".msg-composer textarea")
    box.fill(text)
    box.press("Enter")
    expect(page.locator(".msg-pending")).to_have_count(0, timeout=10000)


def run(browser: Browser, engine: str, accounts: str, docs: str, messages: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)

    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Card Org {tag}", "workspace_name": f"Card Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-cards-{tag}"})
    assert r.status == 201, r.text()[:200]
    wid = r.json()["workspace"]["id"]
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

    # ---- the owner's projects: one in the team workspace, one in their personal space; a job on the team one
    assert pa.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    me = pa.request.get(f"{accounts}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    enter_docs(pa, docs)
    team_pid = build_personal_project(pa, a, docs, wid, f"团队合同 {tag}")
    mine_pid = build_personal_project(pa, a, docs, personal, f"私人草稿 {tag}")
    h = {"X-Synwevo-CSRF": next(c["value"] for c in a.cookies() if c["name"] == "dw_csrf" and "docweave" in c["domain"]), "X-Synwevo-Workspace": wid}
    jid = pa.request.post(f"{docs}/api/projects/{team_pid}/jobs", data={"formats": ["docx"]}, headers=h).json()["id"]
    for _ in range(400):
        j = pa.request.get(f"{docs}/api/jobs/{jid}", headers=h).json()
        if j["status"] not in ("queued", "running", "cancelling"):
            break
        time.sleep(0.3)
    assert j["status"] == "completed", j

    # ---- the owner shares the three links in a direct message (on the messages origin)
    enter_messages(pa, messages)
    pa.get_by_role("button", name=re.compile("新私聊")).click()
    dialog = pa.locator(".modal").last
    dialog.locator("#msg-who").fill("Member")
    dialog.get_by_role("button", name=re.compile("^选择$")).first.click()
    dialog.get_by_role("button", name=re.compile("开始私聊")).click()
    pa.wait_for_url(lambda u: "/c/cv_" in u)
    expect(pa.locator(".msg-status")).to_have_text(re.compile("实时"), timeout=15000)
    send(pa, f"团队项目在这里 {docs}/p/{team_pid}")
    send(pa, f"任务结果 {docs}/p/{team_pid}/jobs/{jid}")
    send(pa, f"我的草稿（你打不开） {docs}/p/{mine_pid}")
    cards = pa.locator(".msg-card")
    expect(cards).to_have_count(3, timeout=15000)
    expect(cards.nth(0)).to_contain_text(f"团队合同 {tag}")
    expect(cards.nth(0)).to_contain_text(f"Card Team {tag}")
    expect(cards.nth(1)).to_contain_text(re.compile("生成任务"))
    expect(cards.nth(1)).to_contain_text(re.compile("已完成 · 2 份成功，0 份失败"))
    expect(cards.nth(2)).to_contain_text(f"私人草稿 {tag}")
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner.png"))
    notes.append(f"{engine}: the owner's three links became cards — team project (with workspace), the finished job (outcome and counts), and their own personal draft")

    # ---- the member sees cards only for what they can open
    enter_messages(pb, messages)
    pb.locator(".msg-conv").first.click()
    expect(pb.locator(".msg-thread")).to_be_visible()
    expect(pb.locator(".msg-body", has_text="我的草稿")).to_be_visible(timeout=15000)
    cards_b = pb.locator(".msg-card")
    expect(cards_b).to_have_count(2, timeout=15000)
    expect(cards_b.nth(0)).to_contain_text(f"团队合同 {tag}")
    expect(cards_b.nth(1)).to_contain_text(re.compile("已完成 · 2 份成功，0 份失败"))
    assert cards_b.filter(has_text=f"私人草稿 {tag}").count() == 0, "a stranger's draft must not become a card"
    draft = pb.locator(".msg-body", has_text="我的草稿")
    expect(draft.locator("a")).to_have_attribute("href", f"{docs}/p/{mine_pid}")     # the plain link stays
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"cards scroll sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-02-member-phone.png"))
    # the card's own request from the member's session answers 404 for the draft, 200 for the team project
    assert pb.request.get(f"{messages}/api/messaging/link-card?url={docs}/p/{mine_pid}").status == 404
    assert pb.request.get(f"{messages}/api/messaging/link-card?url={docs}/p/{team_pid}").status == 200
    notes.append(f"{engine}: the member (phone) got cards for the team project and the job, and only the plain link for the owner's personal draft; nothing scrolls sideways")
    assert not violations, violations
    a.close(); b.close()
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 4:
        raise SystemExit(__doc__)
    accounts, docs, messages, mail_dir = require_test_instance(args[0]), require_test_instance(args[1]), require_test_instance(args[2]), Path(args[3])

    def show(engine: str, notes: list[str]):
        print(f"\n== {engine}")
        for n in notes:
            print(" -", n)

    with sync_playwright() as p:
        if "--webkit-only" not in sys.argv:
            chrome = p.chromium.launch(channel="chrome", headless=True)
            try:
                show("chromium (Google Chrome)", run(chrome, "chromium", accounts, docs, messages, mail_dir))
            finally:
                chrome.close()
        if "--webkit" in sys.argv or "--webkit-only" in sys.argv:
            with LocalhostProxy() as proxy:
                wk = p.webkit.launch(headless=True)
                try:
                    show("webkit", run(wk, "webkit", accounts, docs, messages, mail_dir, {"proxy": {"server": proxy.url}}))
                finally:
                    wk.close()


if __name__ == "__main__":
    main()
