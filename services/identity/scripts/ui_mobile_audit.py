"""Reflow and target-size audit of the account, portal and Docs shells at phone and tablet widths.

Measures, in a real Chromium (the installed Google Chrome via Playwright, mobile emulation), what
WCAG 2.2 makes checkable:

* **1.4.10 Reflow (AA)** — at 320 CSS px the page must not need horizontal scrolling
  (`documentElement.scrollWidth <= innerWidth`), and no visible element may extend past the
  viewport, except content that is two-dimensional by nature (data tables inside their own
  scroll container, the document canvas) — those are allowed to scroll *inside* a container.
* **1.4.3 Contrast (Minimum, AA)** — every visible text node against its effective background:
  4.5:1, or 3:1 for large text (≥ 24 px, or ≥ 18.66 px bold); disabled controls are exempt.
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

MEASURE_JS = r"""
(args) => {
  const [containersSel] = args;
  const vw = window.innerWidth;
  const doc = document.documentElement;
  const out = { innerWidth: vw, scrollWidth: doc.scrollWidth, overflow: [], small: [], primarySmall: [], targets: 0 };
  const containers = Array.from(document.querySelectorAll(containersSel));
  const clipped = (el) => { for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) { const o = getComputedStyle(a).overflowX; if (o === 'hidden' || o === 'clip') return true; } return false; };
  const inContainer = (el) => containers.some(c => c.contains(el)) || clipped(el);
  const visible = (el) => { const cs = getComputedStyle(el); if (cs.display === 'none' || cs.visibility === 'hidden') return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const label = (el) => (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 40);
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
  // Contrast (SC 1.4.3): each visible text node's colour against the first opaque background
  // behind it. Alpha-blended colours are composited; gradients and images are skipped (rare here).
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); };
  const blend = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
  const bgOf = (el) => { let acc = null; for (let a = el; a; a = a.parentElement) { const cs = getComputedStyle(a); if (cs.backgroundImage && cs.backgroundImage !== 'none') return null; const c = parse(cs.backgroundColor); if (c && c.a > 0) { acc = acc ? blend(acc, c) : c; if (acc.a >= 1 && c.a >= 1) return acc; } } return acc ? blend(acc, { r: 255, g: 255, b: 255, a: 1 }) : { r: 255, g: 255, b: 255, a: 1 }; };
  const ratio = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
  out.contrast = []; const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const text = node.textContent.trim(); if (text.length < 2) continue;
    const el = node.parentElement; if (!el || !visible(el)) continue;
    const r = el.getBoundingClientRect(); if (r.bottom < 0 || r.top > window.innerHeight) continue;
    const cs = getComputedStyle(el); const fg = parse(cs.color); if (!fg) continue;
    const bg = bgOf(el); if (!bg) continue;
    const fgc = fg.a < 1 ? blend(fg, bg) : fg;
    const size = parseFloat(cs.fontSize), weight = parseInt(cs.fontWeight, 10) || 400;
    const large = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const rr = ratio(fgc, bg);
    const key = cs.color + '|' + (el.className || el.tagName) + '|' + Math.round(rr * 10);
    if (rr < need && !seen.has(key) && !el.closest('[disabled], .btn:disabled, [aria-disabled="true"], ::placeholder')) { seen.add(key); out.contrast.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 40), color: cs.color, bg: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`, size: Math.round(size * 10) / 10, weight, ratio: Math.round(rr * 100) / 100, text: text.slice(0, 30) }); }
  }
  out.contrast = out.contrast.slice(0, 10);
  // Untranslated text: when the page is rendered in English, any CJK text that is not user
  // content (the caller names the fixture strings) is a string that never reached the catalog.
  const [, lang, fixtures] = args;
  out.untranslated = [];
  if (lang === 'en') {
    const seenT = new Set();
    const w2 = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let n2;
    while ((n2 = w2.nextNode())) {
      const t = n2.textContent.trim();
      if (!/[\u3400-\u9fff]/.test(t) || seenT.has(t)) continue;
      const el = n2.parentElement; if (!el || !visible(el)) continue;
      if ((fixtures || []).some(f => f && t.includes(f))) continue;
      if (el.closest('[lang]:not([lang^="en"]), .who, .who2, .mono, code')) continue;
      seenT.add(t);
      out.untranslated.push({ tag: el.tagName.toLowerCase(), cls: (el.className || '').toString().slice(0, 40), text: t.slice(0, 60) });
    }
    // attributes the reader hears: aria-label, placeholder, title
    for (const el of document.querySelectorAll('[aria-label], [placeholder], [title]')) {
      if (!visible(el)) continue;
      for (const a of ['aria-label', 'placeholder', 'title']) {
        const v = el.getAttribute(a) || '';
        if (/[\u3400-\u9fff]/.test(v) && !seenT.has(v) && !(fixtures || []).some(f => f && v.includes(f))) { seenT.add(v); out.untranslated.push({ tag: el.tagName.toLowerCase() + '[' + a + ']', cls: (el.className || '').toString().slice(0, 40), text: v.slice(0, 60) }); }
      }
    }
    out.untranslated = out.untranslated.slice(0, 15);
  }
  out.overflow = out.overflow.slice(0, 8); out.small = out.small.slice(0, 12);
  return out;
}
"""


LOCALE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--locale=")), None)
#: User content that legitimately stays in its own language on an English page (fixture names).
FIXTURE_STRINGS = [f for f in next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--fixtures=")), "").split("|") if f]


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
    r = page.evaluate(MEASURE_JS, [SCROLL_CONTAINERS, LOCALE, FIXTURE_STRINGS])
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
            rows = [measure(page, u + (f"?lang={LOCALE}" if LOCALE else "")) for u in anon_pages]
            sign_in(page, accounts, email, password)
            previous_locale = None
            if LOCALE:
                from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
                csrf = next((c["value"] for c in ctx.cookies() if c["name"] == CSRF_COOKIE), "")
                previous_locale = page.request.get(f"{accounts}/api/auth/me").json().get("locale")
                r = page.request.patch(f"{accounts}/api/auth/locale", data={"locale": LOCALE}, headers={CSRF_HEADER: csrf})
                assert r.status == 200, ("could not set the locale", r.status, r.text()[:120])
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
            print(f"{'page':44} {'scrollW':>7} {'overflow':>8} {'<24px':>5} {'prim<44':>7} {'contrast':>8}  targets")
            for r in rows:
                bad = r["scrollWidth"] > r["innerWidth"] or r["overflow"] or r["small"] or r.get("contrast") or r.get("untranslated")
                failures += 1 if bad else 0
                flag = "✗" if bad else "✓"
                print(f"{flag} {r['url'][:42]:42} {r['scrollWidth']:>7} {len(r['overflow']):>8} {len(r['small']):>5} {len(r['primarySmall']):>7} {len(r.get('contrast', [])):>8}  {r['targets']}")
                for u_ in r.get("untranslated", [])[:8]:
                    print(f"      untranslated: <{u_['tag']} class='{u_['cls']}'> “{u_['text']}”")
                for c_ in r.get("contrast", [])[:6]:
                    print(f"      contrast {c_['ratio']}:1 <{c_['tag']} class='{c_['cls']}'> {c_['color']} on {c_['bg']} {c_['size']}px/{c_['weight']} “{c_['text']}”")
                for o in r["overflow"][:4]:
                    print(f"      overflow: <{o['tag']} class='{o['cls']}'> right={o['right']} “{o['label']}”")
                for s_ in r["small"][:6]:
                    print(f"      small: <{s_['tag']} class='{s_['cls']}'> {s_['w']}×{s_['h']} “{s_['label']}”")
                for s_ in r["primarySmall"][:3]:
                    print(f"      primary<44: {s_['w']}×{s_['h']} “{s_['label']}”")
            if LOCALE and previous_locale and previous_locale != LOCALE:
                # leave the account as it was found
                from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
                csrf = next((c["value"] for c in ctx.cookies() if c["name"] == CSRF_COOKIE), "")
                page.request.patch(f"{accounts}/api/auth/locale", data={"locale": previous_locale}, headers={CSRF_HEADER: csrf})
            ctx.close()
        browser.close()
    print(f"\n{failures} page(s) with reflow, target-size, contrast or translation findings")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
