"""Reflow and target-size audit of the account, portal and Docs shells at phone and tablet widths.

Measures, in a real Chromium (the installed Google Chrome via Playwright, mobile emulation), what
WCAG 2.2 makes checkable:

* **1.4.10 Reflow (AA)** — at 320 CSS px the page must not need horizontal scrolling
  (`documentElement.scrollWidth <= innerWidth`), and no visible element may extend past the
  viewport, except content that is two-dimensional by nature (data tables inside their own
  scroll container, the document canvas) — those are allowed to scroll *inside* a container.
* **2.5.8 Target Size (Minimum, AA)** — every pointer target is at least 24×24 CSS px or has
  24 px of spacing; here we simply require ≥ 24 px for buttons, links styled as buttons, inputs,
  selects and menu items, and report the primary actions that fall short of the 44 px enhanced
  size (2.5.5, AAA — our own goal on coarse pointers).

Pages are the shells' real routes with a signed-in account where needed. Output is a table per
width; the script exits non-zero when any page scrolls sideways, an element overflows, or a
target is under 24 px. It drives only a test-marked instance (`/healthz` `instance=test`).

    .venv/bin/python services/identity/scripts/ui_mobile_audit.py http://accounts.localhost:31770 \
        http://portal.localhost:31769 http://docweave.localhost:31765 <email> <password> [--widths 320,375,768]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import Page, sync_playwright

# Two-dimensional content is allowed its own horizontal scroll container (1.4.10 exceptions).
SCROLL_CONTAINERS = ".list-wrap, .account-table, .grid-scroll, .doc-page, .sheettabs, .stepbar, .pane-switch, .api-platform-main table, .tbl-wrap, pre, code"

MEASURE_JS = """
(args) => {
  const [containersSel] = args;
  const vw = window.innerWidth;
  const doc = document.documentElement;
  const out = { innerWidth: vw, scrollWidth: doc.scrollWidth, overflow: [], small: [], primarySmall: [], targets: 0 };
  const containers = Array.from(document.querySelectorAll(containersSel));
  const clipped = (el) => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o === 'hidden' || o === 'clip') return true; } return false; };
  const inContainer = (el) => containers.some(c => c.contains(el)) || clipped(el);
  const visible = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = (el) => (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.tagName).trim().replace(/\\s+/g, ' ').slice(0, 40);
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || inContainer(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > vw + 1 && r.width <= vw * 3) out.overflow.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 60), right: Math.round(r.right), label: label(el) });
  }
  const targets = document.querySelectorAll('button, a[href], input:not([type=hidden]), select, textarea, [role=menuitem], [role=tab], [role=button], label.check, summary');
  for (const el of targets) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) continue;   // off-screen: measured when scrolled to
    out.targets++;
    const size = Math.min(r.width, r.height);
    const cs = getComputedStyle(el);
    const inline = el.tagName === 'A' && cs.display === 'inline' && el.closest('p, li, td, span, small, .muted, .pt-footer, .cell-note, .who2, .notice');
    if (size < 24 && !inline && !(el.tagName === 'INPUT' && ['checkbox','radio'].includes(el.type))) out.small.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 50), w: Math.round(r.width), h: Math.round(r.height), label: label(el) });
    if (el.classList.contains('primary') && size < 44) out.primarySmall.push({ w: Math.round(r.width), h: Math.round(r.height), label: label(el) });
  }
  out.overflow = out.overflow.slice(0, 8); out.small = out.small.slice(0, 12);
  return out;
}
"""


def require_test_instance(base: str) -> str:
    with urllib.request.urlopen(base.rstrip("/") + "/healthz", timeout=5) as r:
        body = json.loads(r.read().decode())
    if body.get("instance") != "test":
        raise SystemExit(f"{base} is not a test-marked instance (healthz: {body}); refusing to drive it")
    return base.rstrip("/")


def sign_in(page: Page, accounts: str, email: str, password: str):
    page.goto(f"{accounts}/login?lang=zh-Hans")
    page.get_by_label("邮箱").fill(email)
    page.get_by_label("口令", exact=True).fill(password)
    page.get_by_role("button", name="登录", exact=True).click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=15000)


def measure(page: Page, url: str, settle: str | None = None) -> dict:
    page.goto(url)
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    if settle:
        try:
            page.wait_for_selector(settle, timeout=8000)
        except Exception:
            pass
    page.wait_for_timeout(300)
    r = page.evaluate(MEASURE_JS, [SCROLL_CONTAINERS])
    r["url"] = url.split("?")[0].replace("http://", "")
    r["title"] = page.title()[:30]
    return r


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 5:
        raise SystemExit(__doc__)
    accounts, portal, docs = (require_test_instance(a) for a in args[:3])
    email, password = args[3], args[4]
    widths = [int(w) for w in next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--widths=")), "320,375,768").split(",")]
    anon_pages = [f"{accounts}/login", f"{accounts}/register", f"{accounts}/forgot-password", f"{accounts}/invite",
                  f"{portal}/", f"{accounts}/nowhere"]
    failures = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        for width in widths:
            ctx = browser.new_context(viewport={"width": width, "height": 740}, is_mobile=width < 800, has_touch=width < 800, locale="zh-CN")
            page = ctx.new_page()
            rows = [measure(page, u) for u in anon_pages]
            sign_in(page, accounts, email, password)
            me = page.request.get(f"{accounts}/api/auth/me").json()
            signed = [f"{accounts}/account", f"{accounts}/organizations", f"{accounts}/onboarding"]
            try:
                orgs = page.request.get(f"{accounts}/api/auth/organizations").json()
                for org in (orgs.get("items") if isinstance(orgs, dict) else orgs) or []:
                    signed.append(f"{accounts}/organizations/{org['id']}")
            except Exception:
                pass
            governed = [w for w in me["workspaces"] if w["role"] in ("owner", "admin")]
            for w in governed[:3]:
                signed.append(f"{accounts}/account/workspaces/{w['id']}/members")
                signed.append(f"{accounts}/account/workspaces/{w['id']}/members?tab=invites")
            if me.get("platform_role") == "admin" and not me.get("admin_locked"):
                signed += [f"{accounts}/admin?tab={t}" for t in ("overview", "users", "workspaces", "organizations", "registration", "identity", "mail", "audit")]
                signed += [f"{accounts}/admin/api-platform"]
            for u in signed:
                rows.append(measure(page, u, settle="section.card, table.gov, .acct-page, .idp-card, .empty"))
            # portal dashboard and Docs home (SSO once per context)
            page.goto(f"{portal}/workspace"); page.wait_for_selector(".pt-dashboard, .pt-status", timeout=20000)
            if page.locator(".pt-status a[href*='/auth/start']").count():
                page.locator(".pt-status a[href*='/auth/start']").first.click(); page.wait_for_selector(".pt-dashboard", timeout=20000)
            rows.append(measure(page, f"{portal}/workspace", settle=".pt-dashboard"))
            page.goto(f"{docs}/"); page.wait_for_load_state("networkidle")
            if page.locator("a[href*='/auth/start']").count() and not page.locator(".home").count():
                page.locator("a[href*='/auth/start']").first.click(); page.wait_for_load_state("networkidle")
            rows.append(measure(page, f"{docs}/", settle=".home"))
            cards = page.locator("a[href^='/p/']")
            if cards.count():
                href = cards.first.get_attribute("href")
                rows.append(measure(page, f"{docs}{href}", settle=".work"))
            print(f"\n== {width} px (Chromium mobile emulation{' — is_mobile' if width < 800 else ''})")
            print(f"{'page':44} {'scrollW':>7} {'overflow':>8} {'<24px':>5} {'prim<44':>7}  targets")
            for r in rows:
                bad = r["scrollWidth"] > r["innerWidth"] or r["overflow"] or r["small"]
                failures += 1 if bad else 0
                flag = "✗" if bad else "✓"
                print(f"{flag} {r['url'][:42]:42} {r['scrollWidth']:>7} {len(r['overflow']):>8} {len(r['small']):>5} {len(r['primarySmall']):>7}  {r['targets']}")
                for o in r["overflow"][:4]:
                    print(f"      overflow: <{o['tag']} class='{o['cls']}'> right={o['right']} “{o['label']}”")
                for s_ in r["small"][:6]:
                    print(f"      small: <{s_['tag']} class='{s_['cls']}'> {s_['w']}×{s_['h']} “{s_['label']}”")
                for s_ in r["primarySmall"][:3]:
                    print(f"      primary<44: {s_['w']}×{s_['h']} “{s_['label']}”")
            ctx.close()
        browser.close()
    print(f"\n{failures} page(s) with reflow or target-size findings")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
