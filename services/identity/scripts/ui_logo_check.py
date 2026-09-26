"""Logo launch check on the isolated instance: every shell links fingerprinted PNG icons from the
confirmed mark, /favicon.ico answers, and the top-bar logo image is really loaded (natural size)
at desktop and phone widths; screenshots for the record. Signed in, so the Docs and portal
shells themselves are measured, not the sign-in redirect."""
import os, re, secrets, sys
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright
from ui_e2e_messaging import csrf_of, register_and_activate, sign_in
from ui_e2e_project_copy import enter_docs
accounts, portal, docs, mail_dir = sys.argv[1:5]   # usage: ui_logo_check.py <accounts> <portal> <docs> <mail dir>
shots = Path(os.environ.get("SYNWEVO_E2E_SHOTS", "/tmp")) / "logo"; shots.mkdir(parents=True, exist_ok=True)
tag = secrets.token_hex(3); owner = f"logo.{tag}@example.test"
def origin(u): s = urlsplit(u); return f"{s.scheme}://{s.netloc}"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    for width, label in ((1280, "desktop"), (390, "phone")):
        ctx = b.new_context(viewport={"width": width, "height": 800}, is_mobile=width < 800, locale="zh-CN")
        pg = ctx.new_page()
        if label == "desktop":
            register_and_activate(pg, accounts, Path(mail_dir), owner, f"Logo {tag}")
        sign_in(pg, accounts, owner)
        r = pg.request.post(f"{accounts}/api/auth/onboarding/personal", data={}, headers={"X-Synwevo-CSRF": csrf_of(ctx)})
        assert r.status in (200, 201, 409), r.status
        for name, url in (("accounts", f"{accounts}/account?lang=zh-Hans"), ("portal", f"{portal}/workspace?lang=zh-Hans"), ("docs", f"{docs}/")):
            if name == "docs":
                enter_docs(pg, docs)
            else:
                pg.goto(url); pg.wait_for_load_state("networkidle")
            base = origin(pg.url)
            assert base == origin(url), (name, pg.url)
            html = pg.content()
            links = re.findall(r'<link rel="(icon|apple-touch-icon)"[^>]*href="([^"]+)"', html)
            assert links and not any(h.startswith("data:") for _, h in links), (name, links)   # Vite inlines the tiny 1x mark in the DOM; the icon links must be files
            for _, href in links:
                rr = pg.request.get(href if href.startswith("http") else base + href)
                assert rr.status == 200 and rr.headers.get("content-type", "").startswith("image/png"), (name, href, rr.status, rr.headers.get("content-type"))
            ico = pg.request.get(base + "/favicon.ico")
            assert ico.status == 200 and "icon" in ico.headers.get("content-type", ""), (name, ico.status, ico.headers.get("content-type"))
            info = pg.evaluate("""() => [...document.querySelectorAll('img.logo-img')].filter(i => i.getBoundingClientRect().width > 0)
                .map(i => ({src: i.currentSrc.split('/').pop().replace(/-[A-Za-z0-9_-]{8}\\.png$/, '.png'), w: Math.round(i.getBoundingClientRect().width), h: Math.round(i.getBoundingClientRect().height), nat: i.naturalWidth, alt: i.alt}))""")
            assert info and all(i["nat"] > 0 for i in info), (name, width, info)
            if width < 800:
                # the compact mark: Vite inlines the tiny 1x rendition as a data URI, so judge by shape (square-ish) or by name
                compact = any("mark" in i["src"] or (i["w"] <= i["h"] + 4) for i in info)
                print(f"        phone brand position: {'mark' if compact else 'FULL LOGO (no markBelow)'}")
            pg.screenshot(path=str(shots / f"{label}-{name}.png"))
            print(f"{label:7} {name:8} icons={len(links)} favicon={ico.status} logos={info}")
        ctx.close()
    b.close()
print("LOGO CHECK PASSED")
