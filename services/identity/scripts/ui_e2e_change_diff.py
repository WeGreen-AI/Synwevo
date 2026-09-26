"""The change log shows what changed, not only where (CI-53, 9.13 D 「文本展示插入/删除等可理解差异」).

Two real accounts on the isolated instance edit the same document template. The editor (phone)
appends to a paragraph; the owner replaces two characters at its start. The owner's change log
then shows, per person, the insertions and deletions inside the block: the editor's row as one
inserted run, the owner's row as a deleted run beside an inserted one.

    .venv/bin/python services/identity/scripts/ui_e2e_change_diff.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "change-diff"


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
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Diff Org {tag}", "workspace_name": f"Diff Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-diff-{tag}"})
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
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"差异合同 {tag}"}, headers=h)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok

    pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pa.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pb.locator("[data-testid=collab-bar] .collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)

    # ---- the editor appends; the owner replaces the first two characters of the same paragraph
    ed_b = body_editor(pb)
    ed_b.locator("p", has_text="备注段").click()
    pb.keyboard.press("End")
    pb.keyboard.type(" +手机补充")
    expect(body_editor(pa)).to_contain_text("备注段 +手机补充", timeout=10000)
    time.sleep(0.5)                                                # the editor's run is closed before the owner's starts
    ed_a = body_editor(pa)
    ed_a.locator("p", has_text="备注段").click()
    # select exactly 注段 (offsets 1–3 of the text node) the way a mouse drag would, then type over it
    found = pa.evaluate("""([needle, s, e]) => {
      const root = document.querySelectorAll('.editor-page .ProseMirror')[1];
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let n; while ((n = walker.nextNode())) {
        const i = n.data.indexOf(needle);
        if (i >= 0) { const r = document.createRange(); r.setStart(n, i + s); r.setEnd(n, i + e);
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); return n.data; }
      }
      return null;
    }""", ["备注段", 1, 3])
    assert found and found.startswith("备注段"), found
    pa.keyboard.type("忘录")
    # the other person's caret label sits inside the paragraph, so match around it
    expect(body_editor(pa)).to_contain_text(re.compile(r"备忘录[\s\S]*\+手机补充"), timeout=10000)
    expect(body_editor(pb)).to_contain_text(re.compile(r"备忘录[\s\S]*\+手机补充"), timeout=10000)
    note(notes, f"{engine}: the editor appended to the paragraph and the owner replaced two characters at its start; both see 备忘录 +手机补充")

    # ---- the owner's change log: one row per person, each showing its insertions and deletions
    pa.get_by_role("button", name="变更记录").click()
    log = pa.locator("[data-testid=collab-log]")
    expect(log.locator(".collab-log li")).to_have_count(2, timeout=15000)
    editor_row = log.locator(".collab-log li", has_text=f"Editor {tag}")
    owner_row = log.locator(".collab-log li", has_text=f"Owner {tag}")
    expect(editor_row.locator("ins.diff-ins")).to_have_text(" +手机补充")
    expect(editor_row.locator("del.diff-del")).to_have_count(0)
    expect(owner_row.locator("del.diff-del")).to_have_text("注段")
    expect(owner_row.locator("ins.diff-ins")).to_have_text("忘录")
    expect(owner_row.locator("[data-testid=change-diff]")).to_contain_text("备")
    expect(owner_row.locator("[data-testid=change-diff]")).to_contain_text("+手机补充")
    pa.screenshot(path=str(SHOTS / f"{engine}-01-change-log-diff.png"))
    note(notes, f"{engine}: the change log shows the editor's row as one inserted run (+手机补充) and the owner's row as 注段 deleted beside 忘录 inserted, with the unchanged text around them")

    # ---- the API carries the same before/after texts
    items = pa.request.get(f"{docs}/api/templates/{tid}/collab/changes", headers=h).json()["items"]
    owner_it = next(it for it in items if it["user"]["name"] == f"Owner {tag}")
    assert owner_it["before"] == "备注段 +手机补充" and owner_it["excerpt"] == "备忘录 +手机补充", owner_it
    note(notes, f"{engine}: the change API returns the block's text before and after the owner's run")

    # ---- the editor's phone shows the same log without sideways scrolling
    pb.get_by_role("button", name="变更记录").click()
    expect(pb.locator("[data-testid=collab-log] .collab-log li")).to_have_count(2, timeout=15000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"change log scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-02-phone-change-log.png"))
    note(notes, f"{engine}: the editor's phone shows the same diffs without sideways scrolling")
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
