"""Live co-editing of a document template by two real accounts on the isolated instance: the
owner and an invited editor open the same template in the same organization workspace, see each
other in the top bar and each other's caret, one types and the other sees it at once, the change
log attributes the edits, a save becomes a version the other is told about, the seeded content
read through the browser saves back identical to the version it came from, a demotion turns the
editor's connection read-only, and a page whose socket cannot reach the room falls back to editing
alone with a clear notice. The editor is on a phone.

    .venv/bin/python services/identity/scripts/ui_e2e_collab.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
"""
from __future__ import annotations

import json
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

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "collab"

SEED = {"page": {"size": "A4", "orientation": "portrait", "margins": {"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 31.8}},
        "body": {"type": "doc", "content": [
            {"type": "heading", "attrs": {"level": 2, "textAlign": "center"}, "content": [{"type": "text", "text": "服务 "}, {"type": "text", "text": "合同", "marks": [{"type": "bold"}]}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "客户："}, {"type": "field", "attrs": {"id": "f_customer", "name": "客户名称", "required": True}}, {"type": "text", "text": "，金额：", "marks": [{"type": "italic"}]}, {"type": "field", "attrs": {"id": "f_amount", "name": "金额", "required": False}}]},
            {"type": "table", "content": [{"type": "tableRow", "content": [
                {"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "项目"}]}]},
                {"type": "tableHeader", "attrs": {"colspan": 1, "rowspan": 1}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "数量"}]}]}]}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "备注段"}]}]},
        "header": {"type": "doc", "content": [{"type": "paragraph", "attrs": {"textAlign": "right"}, "content": [{"type": "text", "text": "页眉文字"}]}]},
        "footer": {"type": "doc", "content": [{"type": "paragraph"}]}}


# Stand-ins for the browser's WebSocket, installed before the page's scripts run: one is refused
# for good by "the server" (a 44xx close), the other never answers. They exercise the editor's own
# fallback without touching the real room.
WS_REFUSED = """
window.WebSocket = class FakeSocket {
  constructor(url) { this.url = url; this.readyState = 0; this.binaryType = "blob";
    setTimeout(() => { this.readyState = 3; this.onclose && this.onclose({ code: 4400, reason: "proof", wasClean: true }); }, 30); }
  send() {} close() {} addEventListener() {} removeEventListener() {}
};
"""
WS_SILENT = """
window.WebSocket = class FakeSocket {
  constructor(url) { this.url = url; this.readyState = 0; this.binaryType = "blob"; }
  send() {} close() {} addEventListener() {} removeEventListener() {}
};
"""


def strip_nulls(node):
    """What the schema fills back in on the way out (null attrs) is not content."""
    if isinstance(node, list):
        return [strip_nulls(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for k, v in node.items():
        if k == "attrs" and isinstance(v, dict):
            v = {a: b for a, b in v.items() if b is not None}
            if not v:
                continue
        elif k == "marks" and isinstance(v, list):
            v = sorted(({"type": m["type"], **({"attrs": {a: b for a, b in m["attrs"].items() if b is not None}} if m.get("attrs") and any(b is not None for b in m["attrs"].values()) else {})} for m in v), key=lambda m: m["type"])
        out[k] = strip_nulls(v)
    return out


def note(notes: list[str], text: str) -> None:
    """Kept for the summary and printed at once, so a long run shows how far it got."""
    notes.append(text)
    print(" -", text, file=sys.stderr, flush=True)


def body_editor(page):
    """The body's editing surface (the page holds header, body and footer editors in that order)."""
    return page.locator(".editor-page .ProseMirror").nth(1)


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"editor.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                     # the owner
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)      # the invited editor, on a phone
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    if os.environ.get("SYNWEVO_E2E_DEBUG"):
        def watch(ctx):
            def on_page(page):
                page.on("console", lambda m: print(f"[console {m.type}] {m.text[:200]}", file=sys.stderr, flush=True))
                page.on("websocket", lambda ws: (print(f"[ws open] {ws.url}", file=sys.stderr, flush=True),
                                                 ws.on("socketerror", lambda e: print(f"[ws error] {e}", file=sys.stderr, flush=True)),
                                                 ws.on("close", lambda w: print(f"[ws close] {w.url}", file=sys.stderr, flush=True))))
            ctx.on("page", on_page)
            for pg in ctx.pages:
                on_page(pg)
        watch(a); watch(b)
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
    # ---- two real accounts in one organization workspace: the owner invites the second as an editor
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Collab Org {tag}", "workspace_name": f"Collab Team {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-collab-{tag}"})
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

    # ---- the owner creates a document template in the team workspace
    enter_docs(pa, docs)
    h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid}
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    r = pa.request.post(f"{docs}/api/templates/builtin", data={"kind": "builtin_doc", "editor": SEED, "name": f"协同合同 {tag}"}, headers=h)
    assert r.ok, r.text()[:300]
    tid = r.json()["id"]

    # ---- reference: the single-person path (room refused for good → the saved version is set into the editor) saves v2
    pr = a.new_page()
    pr.add_init_script(WS_REFUSED)
    pr.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pr.locator("[data-testid=collab-bar] .collab-status")).to_have_text("单人编辑", timeout=20000)
    expect(pr.locator(".collab-notice")).to_contain_text(re.compile("实时协同不可用（proof）"))
    expect(body_editor(pr).locator(".field-node")).to_have_count(2)
    pr.get_by_role("button", name="保存模板").click()
    expect(pr.get_by_text(re.compile("已保存（2 个字段，v2）"))).to_be_visible(timeout=15000)
    reference = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()["version"]["editor"]
    pr.close()
    # the schema fills defaults (alignment, cell attributes) into what the browser saves, so the
    # reference is compared for content only; the live document is compared to it exactly below
    ref_text = json.dumps(reference["body"], ensure_ascii=False)
    assert all(x in ref_text for x in ("服务 ", "合同", "客户名称", "金额", "项目", "数量", "备注段")), ref_text[:400]

    # ---- the owner opens the template in the room
    pa.close(); pa = a.new_page()
    pa.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    pa.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    pa.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    bar_a = pa.locator("[data-testid=collab-bar]")
    expect(bar_a.locator(".collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    expect(body_editor(pa)).to_contain_text("客户：")
    expect(body_editor(pa).locator(".field-node")).to_have_count(2)
    expect(pa.locator(".editor-page .ProseMirror").nth(0)).to_contain_text("页眉文字")
    note(notes, f"{engine}: the owner opened the template; the room delivered the seeded document (heading, fields, table, header)")

    # ---- parity: the document the browser reads out of the room saves back byte-for-byte as the single-person path did
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（2 个字段，v3）"))).to_be_visible(timeout=15000)
    saved = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()["version"]["editor"]
    for part in ("body", "header", "footer"):
        if saved[part] != reference[part]:
            (SHOTS / f"{engine}-parity-{part}.json").write_text(json.dumps({"reference": reference[part], "live": saved[part]}, ensure_ascii=False, indent=1), encoding="utf-8")
        assert saved[part] == reference[part], (part, "see the parity json under the screenshots directory")
    note(notes, f"{engine}: saving the room's document (v3) produced exactly the editor JSON the single-person path saved (v2), for body, header and footer")

    # ---- the editor joins on a phone; both see each other
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pb.close(); pb = b.new_page()
    pb.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    pb.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
    pb.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    bar_b = pb.locator("[data-testid=collab-bar]")
    expect(bar_b.locator(".collab-status")).to_have_text(re.compile("实时协同中"), timeout=20000)
    expect(bar_b.locator("[data-testid=collab-peer]")).to_have_attribute("title", f"Owner {tag}")
    expect(bar_a.locator("[data-testid=collab-peer]")).to_have_attribute("title", f"Editor {tag}", timeout=10000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"editor scrolls sideways on a phone ({w})"
    note(notes, f"{engine}: the editor joined from a phone; each top bar lists the other person by name")

    # ---- the owner types; the editor sees it at once, with the owner's caret label
    ed_a = body_editor(pa)
    ed_a.locator("p", has_text="备注段").click()
    pa.keyboard.press("End")
    pa.keyboard.type("——协同追加 XYZ")
    t_typed = time.time()
    expect(body_editor(pb)).to_contain_text("备注段——协同追加 XYZ", timeout=10000)
    remote_ms = (time.time() - t_typed) * 1000
    expect(pb.locator(".collaboration-carets__label", has_text=f"Owner {tag}")).to_be_visible(timeout=10000)
    expect(bar_a.locator(".badge, .collab-status")).to_be_visible()
    expect(pa.locator(".topbar .badge.warn")).to_have_text(re.compile("有未保存为版本的修改"), timeout=10000)
    pb.screenshot(path=str(SHOTS / f"{engine}-01-editor-phone-sees-owner.png"))
    # the editor types too; the owner sees it and the editor's caret
    ed_b = body_editor(pb)
    ed_b.locator("p", has_text="备注段").click()
    pb.keyboard.press("End")
    pb.keyboard.type(" +手机补充")
    t_typed = time.time()
    expect(body_editor(pa)).to_contain_text("协同追加 XYZ +手机补充", timeout=10000)
    remote_ms_back = (time.time() - t_typed) * 1000
    expect(pa.locator(".collaboration-carets__label", has_text=f"Editor {tag}")).to_be_visible(timeout=10000)
    pa.screenshot(path=str(SHOTS / f"{engine}-02-owner-sees-editor-caret.png"))
    note(notes, f"{engine}: text typed by either person appeared in the other's editor within the wait, each with the other's named caret "
                f"(remote visibility after the last keystroke: desktop→phone {remote_ms:.0f} ms, phone→desktop {remote_ms_back:.0f} ms; same machine, loopback)")

    # ---- the change log attributes the edits to the people who made them
    pa.get_by_role("button", name="变更记录").click()
    log = pa.locator("[data-testid=collab-log]")
    expect(log.locator(".collab-log li")).to_have_count(2, timeout=15000)
    expect(log).to_contain_text(re.compile(f"Owner {tag}.*修改了正文第 4 个段落", re.S))
    expect(log).to_contain_text(re.compile(f"Editor {tag}.*修改了正文第 4 个段落", re.S))
    expect(log.locator(".excerpt").first).to_contain_text("协同追加 XYZ +手机补充")
    pa.screenshot(path=str(SHOTS / f"{engine}-03-change-log.png"))
    pa.keyboard.press("Escape")
    note(notes, f"{engine}: the change log lists one entry per person for the paragraph they edited, with the excerpt")

    # ---- the owner saves; the editor is told and the room is clean for both
    pa.get_by_role("button", name="保存模板").click()
    expect(pa.get_by_text(re.compile("已保存（2 个字段，v4）"))).to_be_visible(timeout=15000)
    expect(pb.get_by_text(re.compile(f"Owner {tag} 已把当前内容保存为 v4"))).to_be_visible(timeout=15000)
    expect(pb.locator(".topbar .badge.ok")).to_have_text("已保存", timeout=10000)
    v4 = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()
    assert v4["version"]["no"] == 4 and "协同追加 XYZ +手机补充" in json.dumps(v4["version"]["editor"]["body"], ensure_ascii=False)
    # the editor saves right after without a conflict: the room kept both editors on the current version
    pb.keyboard.type("。")
    expect(body_editor(pa)).to_contain_text("手机补充。", timeout=10000)
    pb.get_by_role("button", name="保存模板").click()
    expect(pb.get_by_text(re.compile("已保存（2 个字段，v5）"))).to_be_visible(timeout=15000)
    note(notes, f"{engine}: the owner's save became v4 (the editor was told); the editor's own save right after became v5 without a stale-version dialog")

    # ---- the owner demotes the editor to viewer: the open connection turns read-only
    # both origins hold a same-named CSRF cookie by now: take the account origin's explicitly
    r = pa.request.post(f"{accounts}/api/auth/workspaces/{wid}/members/roles", headers={"X-Synwevo-CSRF": csrf_for(a, accounts)},
                        data={"changes": [{"user_id": pb.request.get(f"{docs}/api/auth/me").json()["id"], "role": "viewer"}]})
    assert r.ok, r.text()[:300]
    expect(pb.locator(".topbar .badge.warn")).to_have_text("只读", timeout=45000)
    expect(pb.locator(".collab-notice")).to_contain_text(re.compile("你现在只能查看"))
    expect(pb.get_by_role("button", name="保存模板")).to_be_disabled()
    pb.screenshot(path=str(SHOTS / f"{engine}-04-editor-demoted-readonly.png"))
    note(notes, f"{engine}: after the owner changed the editor's role to viewer, the editor's open connection turned read-only within the re-check interval")

    # ---- a page whose socket never reaches the room falls back to editing the saved version alone
    pc = a.new_page()
    pc.add_init_script(WS_SILENT)
    pc.goto(f"{docs}/templates/{tid}/edit?lang=zh-Hans")
    expect(pc.locator(".collab-notice")).to_contain_text(re.compile("无法连接实时协同"), timeout=25000)
    expect(pc.locator("[data-testid=collab-bar] .collab-status")).to_have_text("单人编辑")
    expect(body_editor(pc)).to_contain_text("手机补充。")
    pc.screenshot(path=str(SHOTS / f"{engine}-05-alone-fallback.png"))
    note(notes, f"{engine}: with the socket never answering, the editor said so after the wait and worked alone on the saved version (v5 content present)")
    pc.close()

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
