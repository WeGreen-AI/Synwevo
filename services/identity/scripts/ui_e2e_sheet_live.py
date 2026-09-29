"""A table template edited live by two real accounts on the isolated instance (docs-sheet-live.md B1,
steps ③–④; the Docs process runs with SYNWEVO_SHEET_LIVE=1). The owner is on a desktop, the editor
on a 390 px phone:

- presence: each sees the other in the room, the owner sees the editor's selection with their name;
- each sees the other's cell as it is typed, tinted for a moment;
- the same cell written by both (the phone offline meanwhile): the phone is told whose value it is
  and chooses "use my value";
- a change the table cannot hold (a range field over a merged range) is refused in a sentence;
- edits made offline are sent on reconnect exactly once (ten rows added offline are ten rows);
- the Docs service is stopped, the phone types into the outage (it says so: offline, one change
  waiting), the service starts again: both come back, nothing is lost or doubled;
- the Docs process is killed (SIGKILL: no closing write) right after an acknowledged edit, the phone
  types meanwhile: after the start both edits are there on both pages;
- whenever both pages are at rest, every cell on both reads exactly as in the room's own state (the
  hello a fresh connection gets), docs-sheet-live.md §6;
- undo takes back the owner's own change and skips the cell the editor changed since, saying so;
- a save turns the room into a version holding all of it;
- the phone in English and in Traditional Chinese: no Chinese left in English, no Simplified-only
  character left in Traditional Chinese (derived from the catalogues), no sideways scrolling.

    .venv/bin/python services/identity/scripts/ui_e2e_sheet_live.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]

Only ever pointed at the isolated instance: it stops, kills and starts the Docs service — through
`SYNWEVO_E2E_DOCS_CTL` (the instance's own control command; `stop`, `kill` or `start` is appended)
when set, else through this checkout's `platform.py` (`down docs`, `down docs --kill`, `up docs`), and
then only when `SYNWEVO_DATA_DIR` points outside the repository — it refuses before anything else
otherwise. The service must come back with SYNWEVO_SHEET_LIVE=1.

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
import time
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import PW, csrf_of, link_in, mail_to, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import csrf_for, enter_docs
from ui_e2e_restart_live import docs_ctl, restart_refusal

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "sheet-live"

SHEET = {"sheets": [{"name": "结果", "cols": [{"w": 100}] * 5, "rows": [{"h": 24}] * 8, "merges": [],
                     "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}, "A2": {"v": "金额"}},
                     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]}]}
CJK = re.compile(r"[㐀-鿿]+")
ROOT = Path(__file__).resolve().parents[3]
CATALOGUES = ["apps/docs/web/src/messages.ts", "apps/docs/web/src/messages.server.ts", "apps/docs/web/src/messages.refusals.ts",
              "packages/ui/src/messages.ts", "packages/auth-client/web/src/api.messages.ts",
              "packages/auth-client/web/src/notifications.messages.ts", "packages/auth-client/web/src/session.messages.ts"]


def simplified_only() -> set[str]:
    """Characters the Simplified sources use that no Traditional translation ever does (实, 时, 协, 这 …):
    one of them on a Traditional Chinese page is an untranslated string."""
    key = re.compile(r"""(["'])((?:\\.|(?!\1).)*?)\1\s*:\s*\{""")
    hant = re.compile(r"""["']zh-Hant["']\s*:\s*(?:(["'])((?:\\.|(?!\1).)*?)\1|\{([^}]*)\})""")
    simp, trad = set(), set()
    for rel in CATALOGUES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for m in key.finditer(text):
            simp.update("".join(CJK.findall(m.group(2))))
        for m in hant.finditer(text):
            trad.update("".join(CJK.findall(m.group(2) or m.group(3) or "")))
    return simp - trad


ROOM_STATE = """async ([tid, wid]) => {
  const u = new URL(`/api/collab/templates/${tid}?workspace=${encodeURIComponent(wid)}`, location.href);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(u);
  ws.binaryType = "arraybuffer";
  try {
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("no hello from the room")), 15000);
      ws.onerror = () => { clearTimeout(timer); reject(new Error("the room socket failed")); };
      ws.onmessage = (e) => {
        const b = new Uint8Array(e.data);
        if (b[0] !== 100) return;                                   // the room's own frames (MESSAGE_META)
        let i = 1, len = 0, mul = 1;
        for (;;) { const x = b[i++]; len += (x & 0x7f) * mul; if (x < 0x80) break; mul *= 128; }
        const f = JSON.parse(new TextDecoder().decode(b.subarray(i, i + len)));
        if (f.type === "hello") { clearTimeout(timer); resolve({ live: f.live, seq: f.seq, content: f.content }); }
      };
    });
  } finally { ws.close(); }
}"""
PAGE_CELLS = """() => Object.fromEntries([...document.querySelectorAll('.grid-canvas .gcell[data-ref]')]
  .map((el) => [el.dataset.ref, (el.lastElementChild ? el.lastElementChild.textContent : el.textContent) || ''])
  .filter(([, t]) => t !== ''))"""


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def room_cells(page, tid: str, wid: str) -> dict[str, str]:
    """The first sheet's cells as the room holds them now — from the hello a fresh connection is
    given (the room's own state, not either page's) — written the way the grid shows them."""
    r = page.evaluate(ROOM_STATE, [tid, wid])
    assert r["live"], r
    c = r["content"]
    sid = [x for x in c["order"] if not c["sheets"][x]["gone"]][0]
    sh = c["sheets"][sid]
    rows, cols = {x: i for i, x in enumerate(sh["rows"])}, {x: i for i, x in enumerate(sh["cols"])}
    out = {}
    for k, v in sh["cells"].items():
        rid, cid = k.split(":")
        val = v.get("v")
        if val is None or val == "":
            continue
        shown = "TRUE" if val is True else "FALSE" if val is False else str(int(val)) if isinstance(val, float) and val.is_integer() else str(val)
        out[f"{col_letter(cols[cid])}{rows[rid] + 1}"] = shown
    return out


def at_rest(pa, pb, tid: str, wid: str, label: str) -> int:
    """§6: once both pages have nothing unanswered, every cell on both reads as in the room."""
    for p in (pa, pb):
        expect(status(p)).to_have_text("实时协同中", timeout=30000)
    deadline = time.time() + 15
    while True:
        room = room_cells(pa, tid, wid)
        got = {"owner": pa.evaluate(PAGE_CELLS), "phone": pb.evaluate(PAGE_CELLS)}
        if all(g == room for g in got.values()):
            return len(room)
        if time.time() > deadline:
            bad = {who: {k: (g.get(k), room.get(k)) for k in set(g) | set(room) if g.get(k) != room.get(k)} for who, g in got.items()}
            raise AssertionError(f"{label}: at rest, a page differs from the room (page, room): {bad}")
        pa.wait_for_timeout(300)


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
              notes: list[str], violations: list[str], ctl=docs_ctl) -> list[str]:
    """The table part: the owner's context `a` (desktop) and the editor's `b` (phone), both signed in
    to Docs in the template's workspace; `ctl("stop" | "kill" | "start")` controls the Docs service."""
    wid = ha["X-Synwevo-Workspace"]
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
    n = at_rest(pa, pb, tid, wid, "after typing")
    note(notes, f"{engine}: the owner's B3 reached the phone at once (tinted), the phone's C4 reached the owner; at rest both pages read the room's {n} cells")

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
    at_rest(pa, pb, tid, wid, "after the conflict")
    note(notes, f"{engine}: A2 written offline on the phone and online by the owner: the phone named the owner and their value; 'use my value' won on both pages (at rest, as in the room)")

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
    at_rest(pa, pb, tid, wid, "after the offline rows")
    note(notes, f"{engine}: ten rows and a value added offline reached the owner once the phone was back — {rows_before} rows became {rows_after}, not {rows_before + 20}")

    # ---- the Docs service is stopped; the phone types into the outage; the service starts again
    type_cell(pa, "D2", "重启前")
    expect(cell(pb, "D2")).to_contain_text("重启前", timeout=10000)
    expect(status(pb)).to_have_text("实时协同中", timeout=15000)
    t0 = time.time()
    ctl("stop")                                                      # graceful: the room writes itself out
    expect(status(pb)).to_contain_text("离线", timeout=30000)        # down for as long as this script says
    type_cell(pb, "D3", "重启中输入")
    expect(status(pb)).to_have_text("离线：1 处修改待同步")
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-during-outage.png"))
    ctl("start")
    gone = time.time() - t0
    expect(status(pa)).to_have_text("实时协同中", timeout=45000)
    expect(status(pb)).to_have_text("实时协同中", timeout=45000)
    expect(cell(pa, "D3")).to_contain_text("重启中输入", timeout=15000)
    expect(cell(pb, "D2")).to_contain_text("重启前")
    expect(pa.locator(".topbar .badge.warn").filter(has_text=re.compile("^未保存$"))).to_have_count(1)
    at_rest(pa, pb, tid, wid, "after the restart")
    pa.screenshot(path=str(SHOTS / f"{engine}-03-owner-after-restart.png"))
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-after-restart.png"))
    note(notes, f"{engine}: the Docs service was stopped for {gone:.1f} s; the phone said offline with one change waiting and typed D3 into the outage; both came back live, D3 reached the owner, D2 was kept, the table is still unsaved (the same room), both pages read as the room")

    # ---- the Docs process is killed (no closing write) just after an acknowledged edit
    type_cell(pa, "E2", "崩溃前")
    expect(status(pa)).to_have_text("实时协同中", timeout=10000)     # acknowledged (the draft is written ~1 s later)
    t0 = time.time()
    ctl("kill")
    expect(status(pb)).to_contain_text("离线", timeout=30000)
    type_cell(pb, "E3", "崩溃中输入")
    expect(status(pb)).to_have_text("离线：1 处修改待同步")
    ctl("start")
    killed = time.time() - t0
    expect(status(pa)).to_have_text("实时协同中", timeout=45000)
    expect(status(pb)).to_have_text("实时协同中", timeout=45000)
    for p in (pa, pb):
        expect(cell(p, "E2")).to_contain_text("崩溃前", timeout=15000)
        expect(cell(p, "E3")).to_contain_text("崩溃中输入", timeout=15000)
        expect(cell(p, "D3")).to_contain_text("重启中输入")        # acknowledged before, perhaps not yet in the draft
        expect(cell(p, "D2")).to_contain_text("重启前")
    expect(pa.locator("[data-testid=sheet-attention]")).to_have_count(0)
    at_rest(pa, pb, tid, wid, "after the kill")
    note(notes, f"{engine}: the Docs process was killed right after the owner's E2 was acknowledged (down {killed:.1f} s, no closing write); the phone typed E3 meanwhile; after the start both pages hold D2, D3, E2 and E3, nobody was asked anything, and both read as the room")

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
    at_rest(pa, pb, tid, wid, "after undo")
    note(notes, f"{engine}: the owner's undo took back C6 on both pages, then skipped B5 (the editor changed it since) and said so")

    # ---- a sheet deleted while someone types on it: nothing is written anywhere else, and it can be restored
    pb.get_by_role("button", name="添加工作表").click()
    expect(pb.locator(".sheettab[aria-current=true]")).to_have_text("Sheet2")
    expect(pa.locator(".sheettab").filter(has_text=re.compile("^Sheet2$"))).to_have_count(1, timeout=10000)
    cell(pb, "A1").click()
    cell(pb, "A1").dblclick()
    pb.get_by_label(re.compile("^编辑单元格 A1$")).fill("写在新表上")        # typing, not committed yet
    pa.locator(".sheettab").filter(has_text=re.compile("^Sheet2$")).click()
    pa.get_by_role("button", name="删除当前表").click()
    pa.locator(".modal").get_by_role("button", name="删除工作表").click()
    expect(pb.get_by_text("工作表「Sheet2」已被删除，你在 A1 输入的「写在新表上」没有写入。")).to_be_visible(timeout=10000)
    expect(pb.locator(".sheettab[aria-current=true]")).to_have_text("结果")
    expect(cell(pb, "A1")).to_have_text("客户")                             # not written into the sheet shown now
    pb.locator("[data-testid=sheet-restore]").select_option(label="恢复「Sheet2」")
    expect(pa.locator(".sheettab").filter(has_text=re.compile("^Sheet2$"))).to_have_count(1, timeout=10000)
    expect(pb.locator(".sheettab[aria-current=true]")).to_have_text("Sheet2")
    pb.locator(".sheettab").filter(has_text=re.compile("^结果$")).click()
    at_rest(pa, pb, tid, wid, "after the restore")
    note(notes, f"{engine}: the owner deleted the sheet the phone was typing on: the phone said what it had typed was not written, showed 结果 unchanged, and restored the sheet from 「已删除的工作表」 — back on both pages")

    # ---- save: the room becomes a version holding all of it
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text("已保存为第 2 版。")).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"{re.escape(owner_name)} 已保存第 2 版"))).to_be_visible(timeout=15000)
    v = pa.request.get(f"{docs}/api/templates/{tid}", headers=ha).json()
    sh = v["version"]["editor"]["sheets"][0]
    cells = {k: c.get("v") for k, c in sh["cells"].items()}
    want = {"B3": "张三", "C4": 100, "A2": "手机离线写的", "B8": "离线追加", "D2": "重启前", "D3": "重启中输入", "E2": "崩溃前", "E3": "崩溃中输入", "B5": "乙"}
    assert v["head"] == 2 and all(str(cells.get(k)) == str(x) for k, x in want.items()), cells
    assert "C6" not in cells and len(sh["rows"]) == rows_after and "D6:E6" in (sh.get("merges") or []), (len(sh["rows"]), sh.get("merges"))
    expect(pa.locator(".topbar .badge.ok").filter(has_text=re.compile("^已保存$"))).to_have_count(1)
    note(notes, f"{engine}: the save became v2 with every cell above, {rows_after} rows, the merged D6:E6 and not the undone C6; the phone was told")

    # ---- the phone in English and Traditional Chinese: the interface is translated, nothing scrolls sideways
    # data is left out of the walk (the grid, sheet names, field names, and toasts or announcements
    # still in the language they were said in); what is left is interface text, apart from the language
    # names, people's and sheets' names (a presence label says where someone is) and the number format 「yyyy年m月d日」
    sheet_names = " ".join(x["name"] for x in SHEET["sheets"])
    data = set("".join(CJK.findall(f"简体中文 繁體中文 yyyy年m月d日 {owner_name} {editor_name} {sheet_names}")))
    simplified = simplified_only()
    assert {"实", "时", "协", "这", "删", "设"} <= simplified, "the catalogues were not read"
    ui_text = """() => { const skip = new Set([...document.querySelectorAll('.toasts, [aria-live], .grid-canvas, .sheettabs .sheettab, .maplist .grow, .fldchip')]);
      const walk = (n) => skip.has(n) ? '' : n.nodeType === 3 ? n.textContent : [...n.childNodes].map(walk).join(' ');
      return walk(document.body); }"""
    for lang, live_word in (("en", "Live"), ("zh-Hant", "即時協同中")):
        pb.get_by_label(re.compile("^(语言|語言|Language)$")).select_option(lang)
        expect(status(pb)).to_have_text(live_word)
        seen = set("".join(CJK.findall(pb.evaluate(ui_text)))) - data
        stray = sorted(seen if lang == "en" else seen & simplified)
        assert not stray, f"{'Chinese left in the English page' if lang == 'en' else 'Simplified-only characters in the Traditional Chinese page'}: {stray}"
        pb.screenshot(path=str(SHOTS / f"{engine}-04-phone-{lang}.png"), full_page=True)
    pb.get_by_label(re.compile("^(语言|語言|Language)$")).select_option("zh-Hans")
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, w
    bar = pb.locator("[data-testid=sheet-presence]").bounding_box()
    assert bar and bar["x"] + bar["width"] <= 390 + 1, bar
    note(notes, f"{engine}: the phone reads 'Live' / '即時協同中' with no Chinese interface text left in English and no Simplified-only character ({len(simplified)} known from the catalogues) in Traditional Chinese; no sideways scrolling at 390 px")
    assert not violations, violations
    return notes


def main():
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(args) != 3:
        raise SystemExit(__doc__)
    accounts, docs, mail_dir = require_test_instance(args[0]), require_test_instance(args[1]), Path(args[2])
    if restart_refusal():
        raise SystemExit(restart_refusal())

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
