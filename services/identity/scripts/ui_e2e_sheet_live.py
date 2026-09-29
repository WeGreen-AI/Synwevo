"""A table template edited live by two real accounts on the isolated instance (docs-sheet-live.md B1,
steps ③–④; the Docs process runs with SYNWEVO_SHEET_LIVE=1). The owner is on a desktop, the editor
on a 390 px phone:

- presence: each sees the other in the room, the owner sees the editor's selection with their name;
- each sees the other's cell as it is typed, tinted for a moment;
- the same cell written by both (the phone offline meanwhile): the phone is told whose value it is
  and chooses "use my value";
- a change the table cannot hold (a range field over a merged range) is refused in a sentence;
- edits made offline are sent on reconnect exactly once (ten rows added offline are ten rows);
- the Docs service restarts while the phone keeps typing: both come back, nothing is lost or doubled;
- undo takes back the owner's own change and skips the cell the editor changed since, saying so;
- a save turns the room into a version holding all of it;
- the phone in English and in Traditional Chinese: no stray Simplified Chinese in the interface, no
  sideways scrolling.

    .venv/bin/python services/identity/scripts/ui_e2e_sheet_live.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]

Only ever pointed at the isolated instance: it restarts the Docs service it is given
(`platform.py restart docs`), which must come back with SYNWEVO_SHEET_LIVE=1.

Going offline is the phone's room socket closed and held down through Playwright's WebSocket
routing (`Link`): the page and the server both see the connection drop, and the page cannot
reconnect until the link is up again — Chrome's offline emulation leaves open WebSockets alone.
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs
from ui_e2e_restart_live import restart_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "sheet-live"

SHEET = {"sheets": [{"name": "结果", "cols": [{"w": 100}] * 5, "rows": [{"h": 24}] * 8, "merges": [],
                     "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}, "A2": {"v": "金额"}},
                     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]}]}
CJK = re.compile(r"[㐀-鿿]+")


def note(notes: list[str], text: str) -> None:
    notes.append(text)
    print(" -", text, file=sys.stderr, flush=True)


def cell(page, ref: str):
    """The grid cell for a reference, by its data attribute (the table editor marks each cell)."""
    return page.locator(f"[data-ref='{ref}']").first


def type_cell(page, ref: str, value: str) -> None:
    """Open the cell's editor (double-click), replace its value, commit with Enter."""
    cell(page, ref).click()
    cell(page, ref).dblclick()
    box = page.get_by_label(re.compile(f"^(编辑单元格|編輯單元格|Edit cell) {ref}$"))
    box.fill(value)
    box.press("Enter")


def status(page):
    return page.locator("[data-testid=sheet-sync]")


class Link:
    """The page's room socket through Playwright: `down()` closes it on both sides and holds new ones
    (they never reach the server and never answer) until `up()` lets them go and the page connects
    again for real. (Closing a socket from inside the route handler blocks Playwright's sync API,
    so held sockets are closed from here.)"""

    def __init__(self, page):
        self.is_up = True
        self.open: list = []
        self.held: list = []
        page.route_web_socket(re.compile(r"/api/collab/"), self._route)

    def _route(self, ws):
        if not self.is_up:
            self.held.append(ws)
            return
        self.open.append((ws, ws.connect_to_server()))

    def down(self) -> None:
        self.is_up = False
        for ws, server in self.open:
            for side in (server, ws):
                try:
                    side.close(code=1001, reason="e2e: network down")
                except Exception:
                    pass
        self.open = []

    def up(self) -> None:
        self.is_up = True
        for ws in self.held:
            try:
                ws.close(code=1001, reason="e2e: network back")
            except Exception:
                pass
        self.held = []


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"editor.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations)
    except Exception:
        for name, ctx in (("a", a), ("b", b)):
            try:
                ctx.pages[-1].screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"), full_page=True)
            except Exception:
                pass
        raise
    finally:
        a.close(); b.close()


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations) -> list[str]:
    # ---- two real accounts, one workspace, one table template
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Live Sheet Org {tag}", "workspace_name": f"Live Sheet Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-sl-{tag}"})
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
    ha = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_sheet", "editor": SHEET, "name": f"实时表格 {tag}"}, headers=ha)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pa.close(); pb.close()
    return live_flow(a, b, engine, docs, tid, ha, owner_name=f"Owner {tag}", editor_name=f"Editor {tag}", template_name=f"实时表格 {tag}",
                     notes=notes, violations=violations)


def live_flow(a, b, engine: str, docs: str, tid: str, ha: dict, *, owner_name: str, editor_name: str, template_name: str,
              notes: list[str], violations: list[str], restart=restart_docs) -> list[str]:
    """The table part: the owner's context `a` (desktop) and the editor's `b` (phone), both signed in
    to Docs in the template's workspace; `restart()` restarts the Docs service and says for how long."""
    pa, pb = a.new_page(), b.new_page()
    for p in (pa, pb):
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:160]))
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    link = Link(pb)                                              # the phone's room socket, so it can go offline

    # ---- both open the table in live mode; each sees the other
    pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    try:
        expect(status(pa)).to_have_text("实时协同中", timeout=20000)
    except AssertionError:
        raise AssertionError("the table did not open live: the Docs process needs SYNWEVO_SHEET_LIVE=1") from None
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(status(pb)).to_have_text("实时协同中", timeout=20000)
    expect(pa.locator("[data-testid=project-peer] .name")).to_have_text(editor_name, timeout=10000)
    expect(pb.locator("[data-testid=project-peer]")).to_have_count(1, timeout=10000)
    cell(pb, "C4").click()
    expect(pa.locator("[data-testid=sheet-peer-selection]")).to_have_attribute("data-name", editor_name, timeout=10000)
    cell(pa, "B3").click()
    expect(pb.locator("[data-testid=sheet-peer-selection] .gpeer-name .initial")).to_have_text(owner_name.strip()[:1].upper(), timeout=10000)
    note(notes, f"{engine}: both open the table live; the owner sees the editor's selection on C4 with their name, the phone the owner's initial")

    # ---- each sees the other's cell as it is typed, tinted for a moment
    type_cell(pa, "B3", "张三")
    expect(cell(pb, "B3")).to_contain_text("张三", timeout=10000)
    expect(cell(pb, "B3")).to_have_class(re.compile(r"\brecent\b"))
    type_cell(pb, "C4", "100")
    expect(cell(pa, "C4")).to_contain_text("100", timeout=10000)
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner.png"))
    pb.screenshot(path=str(SHOTS / f"{engine}-01-phone.png"))
    note(notes, f"{engine}: the owner's B3 reached the phone at once (tinted), the phone's C4 reached the owner")

    # ---- the same cell from both while the phone is offline: the phone is told, and chooses its own value
    expect(status(pb)).to_have_text("实时协同中", timeout=15000)     # nothing of the phone's left unanswered
    link.down()
    expect(status(pb)).to_contain_text("离线", timeout=20000)
    type_cell(pb, "A2", "手机离线写的")
    expect(status(pb)).to_have_text("离线：1 处修改待同步")
    type_cell(pa, "A2", "桌面写的")
    link.up()
    att = pb.locator("[data-testid=sheet-attention]")
    expect(att).to_contain_text(f"{owner_name} 已先修改了 A2（现在是「桌面写的」）。", timeout=30000)
    expect(cell(pb, "A2")).to_contain_text("桌面写的")
    pb.screenshot(path=str(SHOTS / f"{engine}-02-phone-conflict.png"), full_page=True)
    att.get_by_role("button", name="用我的值").click()
    expect(cell(pa, "A2")).to_contain_text("手机离线写的", timeout=10000)
    expect(att).to_have_count(0)
    note(notes, f"{engine}: A2 written offline on the phone and online by the owner: the phone named the owner and their value; 'use my value' won on both pages")

    # ---- edits made offline are sent once: ten rows added offline are ten rows, not twenty
    row_heads = pa.locator(".ghead").filter(has_text=re.compile(r"^\d+$"))
    rows_before = row_heads.count()                                 # (typing near the last rows adds some, as it always did)
    expect(status(pb)).to_have_text("实时协同中", timeout=15000)     # nothing of the phone's left unanswered
    link.down()
    expect(status(pb)).to_contain_text("离线", timeout=20000)
    pb.get_by_role("button", name="+10 行").click()
    type_cell(pb, "B8", "离线追加")
    expect(status(pb)).to_have_text("离线：2 处修改待同步")
    link.up()
    expect(status(pb)).to_have_text("实时协同中", timeout=30000)
    expect(cell(pa, "B8")).to_contain_text("离线追加", timeout=10000)
    expect(row_heads).to_have_count(rows_before + 10, timeout=10000)
    pa.wait_for_timeout(1500)                                       # a second copy would have arrived by now
    expect(row_heads).to_have_count(rows_before + 10)
    rows_after = rows_before + 10
    note(notes, f"{engine}: ten rows and a value added offline reached the owner once the phone was back — {rows_before} rows became {rows_after}, not {rows_before + 20}")

    # ---- the Docs service restarts while the phone keeps typing
    type_cell(pa, "D2", "重启前")
    expect(cell(pb, "D2")).to_contain_text("重启前", timeout=10000)
    expect(status(pb)).to_have_text("实时协同中", timeout=15000)
    took: dict = {}
    restarting = threading.Thread(target=lambda: took.setdefault("s", restart()), daemon=True)   # the page is driven from here meanwhile
    restarting.start()
    try:
        expect(status(pb)).to_contain_text("离线", timeout=30000)           # the service is down: type into the outage
        typed_during = True
    except AssertionError:
        typed_during = False                                                 # it came back before the phone noticed
    type_cell(pb, "D3", "重启中输入")
    restarting.join(180)
    assert "s" in took, "the restart did not finish"
    gone = took["s"]
    expect(status(pa)).to_have_text("实时协同中", timeout=45000)
    expect(status(pb)).to_have_text("实时协同中", timeout=45000)
    expect(cell(pa, "D3")).to_contain_text("重启中输入", timeout=15000)
    expect(cell(pb, "D2")).to_contain_text("重启前")
    expect(pa.locator(".topbar .badge.warn").filter(has_text=re.compile("^未保存$"))).to_have_count(1)
    pa.screenshot(path=str(SHOTS / f"{engine}-03-owner-after-restart.png"))
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-after-restart.png"))
    note(notes, f"{engine}: the Docs service restarted ({gone:.1f} s); the phone typed D3 {'while it was down' if typed_during else 'as it came back'}; both came back live, D3 reached the owner, D2 was kept, the table is still unsaved (the same room)")

    # ---- a change the table cannot hold is refused in a sentence
    cell(pa, "D6").click()
    cell(pa, "E6").click(modifiers=["Shift"])
    pa.get_by_role("button", name="合并", exact=True).click()
    expect(cell(pb, "E6")).to_have_count(0, timeout=10000)          # merged into D6 on the phone too
    cell(pb, "D6").click()
    pb.locator(".grid-scroll").press("Shift+ArrowDown")
    pb.get_by_role("button", name="设为固定区域").click()
    pb.locator(".modal input").fill("区域")
    pb.locator(".modal").get_by_role("button", name=re.compile("^(确定|確定|OK)$")).click()
    expect(att).to_contain_text("区域字段 D6:D7 不能包含合并区域 D6:E6。", timeout=10000)
    att.get_by_role("button", name="知道了").click()
    note(notes, f"{engine}: a range field over the owner's merged range was refused in a sentence on the phone")

    # ---- undo takes back my own change, and skips the cell someone changed since
    type_cell(pa, "B5", "甲")
    expect(cell(pb, "B5")).to_contain_text("甲", timeout=10000)
    type_cell(pb, "B5", "乙")
    expect(cell(pa, "B5")).to_contain_text("乙", timeout=10000)
    type_cell(pa, "C6", "要撤销")
    expect(cell(pb, "C6")).to_contain_text("要撤销", timeout=10000)
    pa.locator(".grid-scroll").press("Control+z")
    expect(cell(pb, "C6")).not_to_contain_text("要撤销", timeout=10000)
    pa.locator(".grid-scroll").press("Control+z")
    expect(pa.get_by_text("未撤销 B5：其他成员之后改过它。")).to_be_visible(timeout=10000)
    expect(cell(pb, "B5")).to_contain_text("乙")
    note(notes, f"{engine}: the owner's undo took back C6 on both pages, then skipped B5 (the editor changed it since) and said so")

    # ---- save: the room becomes a version holding all of it
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text("已保存为第 2 版。")).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"{re.escape(owner_name)} 已保存第 2 版"))).to_be_visible(timeout=15000)
    v = pa.request.get(f"{docs}/api/templates/{tid}", headers=ha).json()
    sh = v["version"]["editor"]["sheets"][0]
    cells = {k: c.get("v") for k, c in sh["cells"].items()}
    want = {"B3": "张三", "C4": 100, "A2": "手机离线写的", "B8": "离线追加", "D2": "重启前", "D3": "重启中输入", "B5": "乙"}
    assert v["head"] == 2 and all(str(cells.get(k)) == str(x) for k, x in want.items()), cells
    assert "C6" not in cells and len(sh["rows"]) == rows_after and "D6:E6" in (sh.get("merges") or []), (len(sh["rows"]), sh.get("merges"))
    expect(pa.locator(".topbar .badge.ok").filter(has_text=re.compile("^已保存$"))).to_have_count(1)
    note(notes, f"{engine}: the save became v2 with every cell above, {rows_after} rows, the merged D6:E6 and not the undone C6; the phone was told")

    # ---- the phone in English and Traditional Chinese: the interface is translated, nothing scrolls sideways
    # data stays as typed: cell values, names, the number format 「yyyy年m月d日」, the language names
    data = set(CJK.findall(str(SHEET) + str(want) + template_name + owner_name + editor_name + " " + owner_name[:1] + " " + editor_name[:1] + " yyyy年m月d日 区域 甲 桌面写的 要撤销 简体中文 繁體中文"))
    ui_text = """() => { const skip = new Set([...document.querySelectorAll('.toasts, [aria-live]')]);
      const walk = (n) => skip.has(n) ? '' : n.nodeType === 3 ? n.textContent : [...n.childNodes].map(walk).join(' ');
      return walk(document.body); }"""
    for lang, live_word in (("en", "Live"), ("zh-Hant", "即時協同中")):
        pb.get_by_label(re.compile("^(语言|語言|Language)$")).select_option(lang)
        expect(status(pb)).to_have_text(live_word)
        stray = sorted(set(CJK.findall(pb.evaluate(ui_text))) - data)
        if lang == "en":
            assert not stray, f"Chinese left in the English page: {stray}"
        pb.screenshot(path=str(SHOTS / f"{engine}-04-phone-{lang}.png"), full_page=True)
    pb.get_by_label(re.compile("^(语言|語言|Language)$")).select_option("zh-Hans")
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, w
    bar = pb.locator("[data-testid=sheet-presence]").bounding_box()
    assert bar and bar["x"] + bar["width"] <= 390 + 1, bar
    note(notes, f"{engine}: the phone reads 'Live' / '即時協同中' with no Chinese interface text left in English; no sideways scrolling at 390 px")
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
