"""Preflight issues lead to their fix: in the generate dialog every issue that names a field or a
mapping carries a 定位 button; pressing it closes the dialog and puts that object in front of the
person (field panel with the field selected, mapping list with the mapping active). Desktop and
phone, on the isolated instance.

    .venv/bin/python services/identity/scripts/ui_e2e_preflight_fix.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "preflight-fix"


def open_generate(page):
    page.get_by_role("button", name=re.compile("生成")).first.click()
    dlg = page.locator(".modal")
    expect(dlg).to_be_visible()
    return dlg


def anchor_options(page) -> str:
    """What the comments drawer offers as 关于 reflects the workspace's current selection."""
    page.get_by_role("button", name=re.compile("更多")).click()
    page.get_by_role("menuitem", name=re.compile("^评论")).click()
    drawer = page.locator("[data-testid=comments-drawer]")
    expect(drawer).to_be_visible()
    text = drawer.locator("select").inner_text() if drawer.locator("select").count() else ""
    page.keyboard.press("Escape")
    expect(page.locator("[data-testid=comments-drawer]")).to_have_count(0)
    return text


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
    # a fresh account finishes onboarding with a personal workspace before Docs will admit it
    r = pa.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.status in (200, 201, 409), (r.status, r.text()[:200])
    enter_docs(pa, docs)
    me = pa.request.get(f"{docs}/api/auth/me").json()
    personal = next(w["id"] for w in me["workspaces"] if w["kind"] == "personal")
    pid = build_personal_project(pa, a, docs, personal, f"预检项目 {tag}")
    h = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": personal}
    tid = pa.request.get(f"{docs}/api/projects/{pid}", headers=h).json()["template_id"]
    # a second, required field with no mapping → the preflight's 必填未映射
    view = pa.request.get(f"{docs}/api/templates/{tid}/view", headers=h).json()
    block = next(bk for part in view["doc"]["parts"] for bk in _walk(part["blocks"]) if "您好" in bk["text"])
    s = block["text"].index("您好")
    r = pa.request.post(f"{docs}/api/templates/{tid}/slots", headers=h, data={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 2, "name": "问候", "required": True})
    assert r.ok, r.text()[:200]
    greet = r.json()["created_slot"]["id"]
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": personal}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok

    # ---- desktop: the issue names the field; 定位到字段 closes the dialog and selects it
    pa.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    expect(pa.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    dlg = open_generate(pa)
    issue = dlg.locator(".notice.err", has_text=re.compile("必填未映射|问候"))
    expect(issue.first).to_be_visible(timeout=20000)
    pa.screenshot(path=str(SHOTS / f"{engine}-01-issue-with-fix.png"))
    issue.first.get_by_role("button", name=re.compile("^定位到字段")).first.click()
    expect(pa.locator(".modal")).to_have_count(0)
    assert "字段「问候」" in anchor_options(pa), "the field the issue named is now the selection"
    notes.append(f"{engine}: the generate dialog listed 必填未映射 for 问候 with 定位到字段; pressing it closed the dialog and selected that field")

    # ---- a mapping that names a column the source does not have → 定位到映射
    maps = pa.request.get(f"{docs}/api/projects/{pid}/mappings", headers=h).json()["mappings"]
    broken = [{**m, "sources": [{"kind": "column", "sheet": "客户", "header": "不存在的列"}]} if m["target"].get("binding") else m for m in maps]
    r = pa.request.put(f"{docs}/api/projects/{pid}/mappings", headers=h, data={"mappings": broken})
    assert r.ok, r.text()[:200]
    pa.reload()
    expect(pa.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    dlg = open_generate(pa)
    issue = dlg.locator(".notice.err", has_text=re.compile("不存在的列|缺少列"))
    expect(issue.first).to_be_visible(timeout=20000)
    issue.first.get_by_role("button", name=re.compile("^定位到映射")).first.click()
    expect(pa.locator(".modal")).to_have_count(0)
    assert "映射" in anchor_options(pa), "the mapping the issue named is now active"
    pa.screenshot(path=str(SHOTS / f"{engine}-02-mapping-located.png"))
    notes.append(f"{engine}: a mapping naming a missing column showed 缺少列 with 定位到映射; pressing it closed the dialog and made that mapping the active one")

    # ---- a file-level error (the rule names a sheet the file does not have) → 定位到文件
    assert pa.request.put(f"{docs}/api/projects/{pid}/mappings", headers=h, data={"mappings": maps}).ok
    rule = pa.request.get(f"{docs}/api/projects/{pid}", headers=h).json()["record_rule"]
    assert pa.request.patch(f"{docs}/api/projects/{pid}", headers=h, data={"record_rule": {**rule, "table": {**rule["table"], "sheet": "不存在的表"}}}).ok
    pa.reload()
    expect(pa.locator(".fileitem", has_text="客户.xlsx").get_by_text("可用")).to_be_visible(timeout=20000)
    dlg = open_generate(pa)
    issue = dlg.locator(".notice.err", has_text=re.compile("缺少工作表|不存在的表"))
    expect(issue.first).to_be_visible(timeout=20000)
    issue.first.get_by_role("button", name=re.compile("^定位到文件")).first.click()
    expect(pa.locator(".modal")).to_have_count(0)
    expect(pa.locator(".fileitem.on")).to_contain_text("客户.xlsx")
    notes.append(f"{engine}: a rule naming a missing sheet showed 缺少工作表 with 定位到文件; pressing it closed the dialog and made that file the active one")
    assert pa.request.patch(f"{docs}/api/projects/{pid}", headers=h, data={"record_rule": rule}).ok

    # ---- phone: the same fix path from the dialog at 390 px (mapping and rule mended, so the
    # first issue of every sample is the unmapped required field again)
    sign_in(pb, accounts, owner)          # sign_in navigates itself; an extra goto first left WebKit reporting the cancelled probes as errors
    enter_docs(pb, docs)
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": personal}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    pb.goto(f"{docs}/p/{pid}?lang=zh-Hans")
    pb.wait_for_load_state("networkidle")
    dlg = open_generate(pb)
    issue = dlg.locator(".notice.err", has_text=re.compile("必填未映射|问候"))
    expect(issue.first).to_be_visible(timeout=20000)
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"dialog scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-issue.png"))
    issue.first.get_by_role("button", name=re.compile("^定位到字段")).first.click()
    expect(pb.locator(".modal")).to_have_count(0)
    expect(pb.get_by_role("tab", name=re.compile("^目标"))).to_contain_text("●", timeout=10000)
    pb.screenshot(path=str(SHOTS / f"{engine}-04-phone-located.png"))
    notes.append(f"{engine}: on the phone the same button closed the dialog and the 目标 pane tab shows a selection (●)")
    # ---- the job page: a batch run with error_policy=skip leaves the invalid samples as skipped
    # items whose issues link back into the project (?locate=…), selecting the named field
    r = pa.request.post(f"{docs}/api/projects/{pid}/jobs", headers=h, data={"formats": ["docx"], "error_policy": "skip"})
    assert r.status in (200, 201, 202), r.text()[:200]
    jid = r.json()["id"]
    pa.goto(f"{docs}/p/{pid}/jobs/{jid}?lang=zh-Hans")
    link = pa.get_by_role("link", name=re.compile("^定位到字段")).first
    expect(link).to_be_visible(timeout=30000)
    pa.screenshot(path=str(SHOTS / f"{engine}-05-job-page-link.png"))
    link.click()
    pa.wait_for_url(re.compile(rf"/p/{pid}(\?|$)"), timeout=20000)
    expect(pa.locator(".fileitem", has_text="客户.xlsx")).to_be_visible(timeout=20000)
    assert "locate=" not in pa.url, "the deep link parameter is dropped once applied"
    assert "字段「问候」" in anchor_options(pa), "the job page's link selected the field it named"
    notes.append(f"{engine}: the job page's skipped sample linked 定位到字段 into the project, which opened with that field selected and the locate parameter dropped")
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
