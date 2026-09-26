"""Template version history with restore, on the isolated instance: the project page's 版本
button lists every version with the current one marked and says how many projects follow the
head or are pinned; restoring an earlier version creates a new current version whose fields are
the old ones, the later versions stay in the history, and undo takes it back. Desktop and phone.

    .venv/bin/python services/identity/scripts/ui_e2e_template_versions.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
"""
from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Browser, expect, sync_playwright

from localhost_proxy import LocalhostProxy
from ui_e2e_messaging import csrf_of, register_and_activate, require_test_instance, sign_in
from ui_e2e_project_copy import _walk, build_personal_project, csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "template-versions"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner = f"owner.{tag}@example.test"
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
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, notes, violations)
    except Exception:
        for name, p in (("a", pa), ("b", pb)):
            try:
                p.screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, notes, violations) -> list[str]:
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.status in (200, 201, 409), (r.status, r.text()[:200])
    enter_docs(pa, docs)
    me = pa.request.get(f"{docs}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    pid = build_personal_project(pa, a, docs, personal, f"版本项目 {tag}")          # template v2: one field 客户名称
    h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": personal}
    tid = pa.request.get(f"{docs}/api/projects/{pid}", headers=h).json()["template_id"]
    view = pa.request.get(f"{docs}/api/templates/{tid}/view", headers=h).json()
    block = next(bk for part in view["doc"]["parts"] for bk in _walk(part["blocks"]) if "您好" in bk["text"])
    s = block["text"].index("您好")
    assert pa.request.post(f"{docs}/api/templates/{tid}/slots", headers=h, data={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 2, "name": "问候"}).ok   # v3
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": personal}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok

    # ---- desktop: the dialog lists the versions and the projects that follow; restore v2 → v4
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    expect(pa.locator(".pane-h .badge", has_text=re.compile(r"v3$"))).to_be_visible()
    pa.get_by_role("button", name=re.compile("^版本$")).click()
    dlg = pa.locator(".modal")
    expect(dlg.locator("table[aria-label='模板版本'] tbody tr")).to_have_count(3)
    expect(dlg.locator("tr[data-version='3']")).to_contain_text("当前")
    expect(dlg).to_contain_text(re.compile("1 个跟随当前版本，0 个固定了版本"))
    dlg.locator("tr[data-version='2']").get_by_role("button", name=re.compile("^恢复此版本$")).click()
    expect(dlg).to_contain_text(re.compile("将以第 2 版的内容新建第 4 版"))
    pa.screenshot(path=str(SHOTS / f"{engine}-01-restore-confirm.png"))
    dlg.locator("tr[data-version='2']").get_by_role("button", name=re.compile("^确认恢复$")).click()
    expect(pa.locator(".modal")).to_have_count(0, timeout=15000)
    expect(pa.locator(".pane-h .badge", has_text=re.compile(r"v4$"))).to_be_visible(timeout=15000)
    expect(pa.locator(".pane.right .fldchip", has_text="问候")).to_have_count(0)
    expect(pa.locator(".pane.right .fldchip", has_text="客户名称")).to_have_count(1)
    d = pa.request.get(f"{docs}/api/templates/{tid}", headers=h).json()
    assert d["head"] == 4 and [v["version_no"] for v in d["versions"]] == [1, 2, 3, 4] and d["version"]["note"] == "恢复自 v2"
    pa.screenshot(path=str(SHOTS / f"{engine}-02-restored.png"))
    notes.append(f"{engine}: 版本 listed v1–v3 with v3 marked current and one following project; restoring v2 made v4 the current version with only 客户名称, and v3 stayed in the history")
    # undo takes it back to v3 (two fields)
    pa.get_by_role("button", name=re.compile("撤销模板修改")).click()
    expect(pa.locator(".pane-h .badge", has_text=re.compile(r"v3$"))).to_be_visible(timeout=15000)
    expect(pa.locator(".pane.right .fldchip", has_text="问候")).to_have_count(1)
    notes.append(f"{engine}: undo after the restore returned to v3 with both fields")

    # ---- phone: the same dialog from the 目标 pane, no sideways scroll
    sign_in(pb, accounts, owner)
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": personal}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pb.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    pb.wait_for_load_state("networkidle")
    pb.get_by_role("tab", name=re.compile("^目标")).click()
    pb.get_by_role("button", name=re.compile("^版本$")).click()
    dlg = pb.locator(".modal")
    expect(dlg.locator("table[aria-label='模板版本'] tbody tr")).to_have_count(4)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"dialog scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-versions.png"))
    notes.append(f"{engine}: on the phone the 目标 pane's 版本 button opened the same list (four versions) without sideways scroll")
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
