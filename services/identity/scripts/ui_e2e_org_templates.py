"""Organization-shared templates, in two real sessions on the isolated instance: the owner of an
organization publishes a template from workspace A's library to the organization; a member of
workspace B, on a phone, sets up a project from the shared template (a copy in B); the owner
releases a new version and then deprecates the publication; the member's copy is untouched and
the deprecated publication is no longer offered.

    .venv/bin/python services/identity/scripts/ui_e2e_org_templates.py http://accounts.localhost:31770 http://docweave.localhost:31765 <mail dir> [--webkit]
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
from ui_e2e_messaging import csrf_of, register_and_activate, require_test_instance, sign_in
from ui_e2e_moderation import accept_invite
from ui_e2e_project_copy import _walk, build_personal_project, csrf_for, enter_docs

expect.set_options(timeout=15000)
SHOTS = Path(os.environ.get("SYNWEVO_E2E_SHOTS") or Path(tempfile.gettempdir()) / "synwevo-e2e") / "org-templates"


def run(browser: Browser, engine: str, accounts: str, docs: str, mail_dir: Path, ctx_extra: dict | None = None) -> list[str]:
    notes: list[str] = []
    tag = secrets.token_hex(3)
    owner, member = f"owner.{tag}@example.test", f"member.{tag}@example.test"
    extra = ctx_extra or {}
    a = browser.new_context(viewport={"width": 1280, "height": 800}, locale="zh-CN", **extra)                                  # the owner
    b = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="zh-CN", **extra)   # the member, on a phone
    pa, pb = a.new_page(), b.new_page()
    violations: list[str] = []
    for p in (pa, pb):
        p.on("console", lambda m: violations.append(m.text[:160]) if "Content Security Policy" in m.text else None)
        p.on("pageerror", lambda e: violations.append("pageerror: " + str(e)[:120]))
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        return _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations)
    except Exception:
        for name, p in (("a", pa), ("b", pb)):
            try:
                p.screenshot(path=str(SHOTS / f"{engine}-fail-{name}.png"))
            except Exception:
                pass
        raise


def _flow(a, b, pa, pb, engine, accounts, docs, mail_dir, tag, owner, member, notes, violations) -> list[str]:
    register_and_activate(pa, accounts, mail_dir, owner, f"Owner {tag}")
    sign_in(pa, accounts, owner)
    r = pa.request.post(f"{accounts}/api/auth/organizations", data={"name": f"Tpl Org {tag}", "workspace_name": f"Team A {tag}"},
                        headers={"X-Synwevo-CSRF": csrf_of(a), "Idempotency-Key": f"e2e-tpl-{tag}"})
    assert r.status == 201, r.text()[:200]
    oid, wid_a = r.json()["organization"]["id"], r.json()["workspace"]["id"]
    r = pa.request.post(f"{accounts}/api/auth/organizations/{oid}/workspaces", data={"name": f"Team B {tag}"}, headers={"X-Synwevo-CSRF": csrf_of(a)})
    assert r.status == 201, r.text()[:200]
    wid_b = r.json()["workspace"]["id"]
    started = time.time()
    assert pa.request.post(f"{accounts}/api/auth/workspaces/{wid_b}/invitations/batch", data={"invitations": [{"email": member, "role": "editor"}]}, headers={"X-Synwevo-CSRF": csrf_of(a)}).ok
    accept_invite(pb, accounts, mail_dir, member, f"Member {tag}", started)

    # ---- the owner: a template with one field in Team A, published to the organization from the library
    enter_docs(pa, docs)
    build_personal_project(pa, a, docs, wid_a, f"函件项目 {tag}")
    assert pa.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid_a}, headers={"X-Synwevo-CSRF": csrf_for(a, docs)}).ok
    pa.goto(f"{docs}/?lang=zh-Hans")
    lib = pa.locator("table[aria-label='模板库']")
    row = lib.locator("tr", has_text="合同模板")
    expect(row).to_have_count(1, timeout=20000)
    row.get_by_role("button", name=re.compile("模板「合同模板」的操作")).click()
    pa.get_by_role("menuitem", name=re.compile("^发布到组织")).click()
    dlg = pa.locator(".modal")
    dlg.get_by_label(re.compile("说明")).fill("客户函件的标准模板")
    dlg.get_by_role("button", name=re.compile("^发布$")).click()
    expect(row.locator(".badge")).to_contain_text(re.compile("已共享 · 第 1 次发布"), timeout=15000)
    shared_tbl = pa.locator("table[aria-label='组织共享模板']")
    srow = shared_tbl.locator("tr", has_text="合同模板")
    expect(srow).to_contain_text(re.compile("第 1 次发布 · 模板第 2 版"))
    expect(srow).to_contain_text(f"Team A {tag}")
    pa.screenshot(path=str(SHOTS / f"{engine}-01-owner-published.png"))
    notes.append(f"{engine}: the owner published the library template to the organization from its row menu; the row shows 已共享 and the shared section lists release 1 (template v2) from Team A")

    # ---- the member (phone) in Team B: a new project takes the shared template as a copy in B
    enter_docs(pb, docs)
    hb = {"X-Synwevo-CSRF": csrf_for(b, docs), "X-Synwevo-Workspace": wid_b}
    assert pb.request.post(f"{docs}/api/auth/workspace", data={"workspace_id": wid_b}, headers={"X-Synwevo-CSRF": csrf_for(b, docs)}).ok
    r = pb.request.post(f"{docs}/api/projects", data={"name": f"B 项目 {tag}"}, headers=hb)
    assert r.ok, r.text()[:200]
    pid_b = r.json()["id"]
    pb.goto(f"{docs}/p/{pid_b}?lang=zh-Hans")
    pb.get_by_role("tab", name=re.compile("^目标$")).click()
    pb.get_by_role("button", name=re.compile("^设置模板$")).last.click()     # the step list carries the same label; the pane's button is last
    chooser = pb.locator(".modal")
    chooser.get_by_role("button", name=re.compile("^从组织共享选择")).click()
    line = chooser.locator("tr", has_text="合同模板")
    expect(line).to_contain_text("客户函件的标准模板")
    w = pb.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")
    assert w[0] <= w[1] + 1, f"chooser scrolls sideways on a phone ({w})"
    pb.screenshot(path=str(SHOTS / f"{engine}-02-phone-chooser.png"))
    line.get_by_role("button", name=re.compile("^使用$")).click()
    expect(pb.locator(".modal")).to_have_count(0, timeout=15000)
    expect(pb.locator(".fldchip", has_text="客户名称")).to_be_visible(timeout=20000)
    mine = pb.request.get(f"{docs}/api/templates", headers=hb).json()
    assert len(mine) == 1 and mine[0]["info"]["origin"]["release_no"] == 1 and mine[0]["slot_count"] == 1, mine
    copy_id = mine[0]["id"]
    pb.screenshot(path=str(SHOTS / f"{engine}-03-phone-project.png"))
    notes.append(f"{engine}: on the phone the member chose 从组织共享选择 → 使用; the project got a copy of release 1 in Team B with the field 客户名称")

    # ---- the owner: a new field in the source → release 2; then deprecation
    ha = {"X-Synwevo-CSRF": csrf_for(a, docs), "X-Synwevo-Workspace": wid_a}
    tid = next(t["id"] for t in pa.request.get(f"{docs}/api/templates", headers=ha).json() if t["name"] == "合同模板")
    view = pa.request.get(f"{docs}/api/templates/{tid}/view", headers=ha).json()
    block = next(bk for part in view["doc"]["parts"] for bk in _walk(part["blocks"]) if "您好" in bk["text"])
    s = block["text"].index("您好")
    assert pa.request.post(f"{docs}/api/templates/{tid}/slots", headers=ha, data={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 2, "name": "问候"}).ok
    pa.reload()
    srow = pa.locator("table[aria-label='组织共享模板']").locator("tr", has_text="合同模板")
    expect(srow).to_be_visible(timeout=20000)
    srow.get_by_role("button", name=re.compile("模板「合同模板」的共享操作")).click()
    pa.get_by_role("menuitem", name=re.compile("^更新组织发布$")).click()
    expect(srow).to_contain_text(re.compile("第 2 次发布 · 模板第 3 版"), timeout=15000)
    srow.get_by_role("button", name=re.compile("模板「合同模板」的共享操作")).click()
    pa.get_by_role("menuitem", name=re.compile("^弃用共享")).click()
    pa.locator(".modal").get_by_role("button", name=re.compile("^弃用$")).click()
    expect(srow.locator(".badge")).to_contain_text("已弃用", timeout=15000)
    expect(pa.locator("table[aria-label='模板库']").locator("tr", has_text="合同模板").locator(".badge")).to_contain_text("共享已弃用")
    pa.screenshot(path=str(SHOTS / f"{engine}-04-owner-deprecated.png"))
    notes.append(f"{engine}: the owner released version 2 after adding a field, then deprecated the publication; both tables say so")

    # ---- the member: the copy is untouched; the deprecated publication is no longer offered
    copy = pb.request.get(f"{docs}/api/templates/{copy_id}", headers=hb).json()
    assert copy["head"] == 1 and len(copy["version"]["slots"]) == 1, "the copy keeps release 1"
    assert pb.request.get(f"{docs}/api/organization-templates", headers=hb).json()["items"] == []
    listed = pb.request.get(f"{docs}/api/organization-templates?include_deprecated=1", headers=hb).json()["items"]
    assert len(listed) == 1 and listed[0]["deprecated"] and listed[0]["release_no"] == 2 and listed[0]["can_manage"] is False, listed
    r = pb.request.post(f"{docs}/api/organization-templates/{listed[0]['id']}/use", data={}, headers=hb)
    assert r.status == 409 and r.json()["error"] == "deprecated", (r.status, r.text()[:200])
    notes.append(f"{engine}: the member's copy still has one field and release 1; the deprecated publication is hidden from the picker and refuses new uses")
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
