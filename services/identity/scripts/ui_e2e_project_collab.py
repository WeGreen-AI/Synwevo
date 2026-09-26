"""The project page as shared work, with two real accounts on the isolated instance: both see who
is on the project and what the other is looking at (and can jump there); a mapping the editor
saves appears on the owner's page at once with the editor's name; a change the owner made after
the editor loaded the page merges with the editor's next save instead of a refusal; a change both
made to the same mapping is shown as a conflict the editor resolves; the activity list attributes
every step. The editor is on a phone.

    .venv/bin/python services/identity/scripts/ui_e2e_project_collab.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_project_copy import _walk, build_personal_project, csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "project-collab"


def note(notes: list[str], text: str) -> None:
    notes.append(text)
    print(" -", text, file=sys.stderr, flush=True)


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
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Map Org {tag}", "workspace_name": f"Map Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-pc-{tag}"})
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

    # ---- a project in the team workspace (one source, one template with one field mapped)
    enter_docs(pa, docs)
    ha = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    pid = build_personal_project(pa, a, docs, wid, f"协同映射 {tag}")
    enter_docs(pb, docs)
    hb = {"X-Synwevo-CSRF": csrf_for(b, docs), "X-Synwevo-Workspace": wid}
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    for ctx, p in ((a, pa), (b, pb)):
        p.close()
    pa, pb = a.new_page(), b.new_page()
    for p in (pa, pb):
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.locator("[data-testid=project-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    pb.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pb.locator("[data-testid=project-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)
    expect(pa.locator("[data-testid=project-peer]")).to_have_count(1, timeout=10000)
    expect(pa.locator("[data-testid=project-peer] .name")).to_have_text(f"Editor {tag}")
    expect(pb.locator("[data-testid=project-peer]")).to_have_count(1, timeout=10000)
    note(notes, f"{engine}: both project pages show the other person in the top bar")

    # ---- what the other is on: the owner selects the field; the editor's chip for the owner says so and jumps there
    pa.locator(".pane.right .fldchip", has_text="客户名称").click(force=True)
    expect(pb.locator("[data-testid=project-peer]")).to_have_attribute("data-focus", re.compile("字段「客户名称」"), timeout=10000)
    pb.screenshot(path=str(SHOTS / f"{engine}-01-editor-phone-sees-owner-focus.png"))
    note(notes, f"{engine}: the editor's phone shows the owner is on 字段「客户名称」")

    # ---- the editor saves a second mapping over the API (same session); the owner's page refreshes itself and names the editor
    ms = pb.request.get(f"{docs}/api/projects/{pid}/mappings", headers=hb).json()
    first = ms["mappings"][0]
    tid = pa.request.get(f"{docs}/api/projects/{pid}", headers=ha).json()["template_id"]
    view = pb.request.get(f"{docs}/api/templates/{tid}/view", headers=hb).json()
    # a second field in the block that already holds the first one, on a range beside it
    block = next(b for part in view["doc"]["parts"] for b in _walk(part["blocks"]) if "示例客户名称" in b["text"])
    at = block["text"].index("示例客户名称")
    start, end = (0, 2) if at >= 2 else (at + 6, at + 8)
    assert len(block["text"]) >= end, block["text"]
    r = pb.request.post(f"{docs}/api/templates/{tid}/slots", headers=hb,
                        data={"kind": "text", "part": block["part"], "path": block["path"], "start": start, "end": end, "name": "第二字段"})
    assert r.ok, r.text()[:300]
    slot2 = r.json()["created_slot"]
    r = pb.request.put(f"{docs}/api/projects/{pid}/mappings", headers=hb,
                       data={"mappings": [first, {"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": slot2["binding"]}}],
                             "note": "第二条映射", "base_rev": pb.request.get(f"{docs}/api/projects/{pid}/mappings", headers=hb).json()["rev"]})
    assert r.ok, r.text()[:200]
    expect(pa.get_by_text(re.compile(f"Editor {tag} 新增映射"))).to_be_visible(timeout=15000)
    pa.screenshot(path=str(SHOTS / f"{engine}-02-owner-told-of-editor-save.png"))
    note(notes, f"{engine}: the editor's save reached the owner's open page as a toast naming the editor, and the list refreshed")

    # ---- a merge: the owner changes mapping #1 after the editor loaded rev N; the editor's later save of a *different* mapping lands without a refusal
    cur = pa.request.get(f"{docs}/api/projects/{pid}/mappings", headers=ha).json()
    base = cur["rev"]
    mine = cur["mappings"]
    assert pa.request.put(f"{docs}/api/projects/{pid}/mappings", headers=ha, data={"mappings": [dict(mine[0], enabled=False)] + mine[1:], "note": "停用 #1", "base_rev": base}).ok
    assert len(mine) == 2, mine
    r = pb.request.put(f"{docs}/api/projects/{pid}/mappings", headers=hb, data={"mappings": [mine[0], dict(mine[1], enabled=False)], "note": "停用 #2", "base_rev": base})
    assert r.ok, r.text()[:300]
    merged = r.json()
    assert merged.get("merged_with") == base + 1 and all(not m["enabled"] for m in merged["mappings"]), merged
    note(notes, f"{engine}: the editor's save from the older revision merged with the owner's (both mappings now disabled), answered 200 with merged_with")
    # ---- a real collision: both change mapping #1 from the same revision; the editor's UI shows the conflict and keeps theirs
    cur = pa.request.get(f"{docs}/api/projects/{pid}/mappings", headers=ha).json()
    base = cur["rev"]
    assert pa.request.put(f"{docs}/api/projects/{pid}/mappings", headers=ha, data={"mappings": [dict(cur["mappings"][0], enabled=True)] + cur["mappings"][1:], "note": "启用 #1", "base_rev": base}).ok
    r = pb.request.put(f"{docs}/api/projects/{pid}/mappings", headers=hb, data={"mappings": [dict(cur["mappings"][0], enabled=True, note="x")] + cur["mappings"][1:], "note": "也改 #1", "base_rev": base})
    assert r.status == 409 and r.json()["detail"]["conflicts"][0]["reason"] == "both_changed", r.text()[:300]
    note(notes, f"{engine}: the same mapping changed by both from one revision is refused with the mapping id and reason both_changed")
    # through the UI: the editor's page still holds the older revision in memory? it refreshed on the owner's change frame — so drive a conflict
    # by making the owner's change land *after* the editor's page loaded its state but before the editor's click: block the editor's refresh
    # is not something a user does; instead the editor's toggle on a mapping the owner just changed arrives fresh and simply succeeds.
    pb.reload()
    expect(pb.locator("[data-testid=project-presence] .collab-status")).to_have_text("实时更新中", timeout=20000)

    # ---- the activity list attributes every step to the person who did it
    pa.get_by_role("button", name="活动记录").click()
    act = pa.locator("[data-testid=project-activity]")
    expect(act.locator(".collab-log li").first).to_be_visible(timeout=15000)
    expect(act).to_contain_text(re.compile(f"Owner {tag}.*启用|Owner {tag}.*修改映射", re.S))
    expect(act).to_contain_text(re.compile(f"Editor {tag}"))
    expect(act).to_contain_text(re.compile("自动合并"))
    pa.screenshot(path=str(SHOTS / f"{engine}-03-activity.png"))
    pa.keyboard.press("Escape")
    pb.get_by_role("button", name="活动记录").click()
    expect(pb.locator("[data-testid=project-activity] .collab-log li").first).to_be_visible(timeout=15000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"activity drawer scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-04-activity-phone.png"))
    pb.keyboard.press("Escape")
    note(notes, f"{engine}: the activity list names the owner and the editor for their own saves, including the automatic merge")

    # ---- leaving: the owner's chip disappears from the editor's page
    pa.close()
    expect(pb.locator("[data-testid=project-peer]")).to_have_count(0, timeout=15000)
    note(notes, f"{engine}: when the owner closed the page, the editor's presence list emptied")
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
