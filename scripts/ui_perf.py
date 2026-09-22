"""Measure real interaction and frame timings in a visible Chrome window.

A headless (or hidden) browser throttles requestAnimationFrame, so frame times measured
there are the throttle interval, not the app's.  This script drives a *headed* Chrome so
rAF runs at the display's rate, and reports:

  * time to first interactive view (navigation → grid cells on screen)
  * cell selection / mapping-list keyboard navigation, handler → next painted frame
  * frame intervals while scrolling a large sheet and while dragging the panel divider
  * long tasks (PerformanceObserver "longtask") during those interactions
  * JS heap before and after

    backend/.venv/bin/python scripts/ui_perf.py <project-id> [base_url]

Numbers go into docs/quality/verification.md with the machine they were measured on.
"""
from __future__ import annotations

import json
import platform
import statistics
import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8765"
PID = sys.argv[1]

# A frame recorder that keeps running while Python drives the mouse, so scroll and drag
# frames are sampled during the real interaction rather than around it.
RECORDER_JS = """
() => {
  window.__rec = { xs: [], on: false, last: 0 };
  const step = (t) => {
    if (!window.__rec.on) return;
    if (window.__rec.last) window.__rec.xs.push(t - window.__rec.last);
    window.__rec.last = t;
    requestAnimationFrame(step);
  };
  window.__recStart = () => { window.__rec.xs = []; window.__rec.last = 0; window.__rec.on = true; requestAnimationFrame(step); };
  window.__recStop = () => { window.__rec.on = false; return window.__rec.xs; };
}
"""

# Idle cadence: how fast this display actually paints, for comparison.
FRAME_JS = """
(ms) => new Promise((done) => {
  const xs = []; let last = performance.now(); const t0 = last;
  const step = (t) => { xs.push(t - last); last = t;
    if (t - t0 < ms) requestAnimationFrame(step); else done(xs); };
  requestAnimationFrame(step);
})
"""

INTERACT_JS = """
async ([sel, n]) => {
  const nextFrame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const els = [...document.querySelectorAll(sel)];
  if (!els.length) return null;
  const mouse = (el, type, x, y) => el.dispatchEvent(new MouseEvent(type,
      {bubbles: true, cancelable: true, clientX: x, clientY: y, button: 0, buttons: type === 'mouseup' ? 0 : 1}));
  const xs = [];
  for (let i = 0; i < n; i++) {
    const el = els[(i * 7) % els.length];
    const r = el.getBoundingClientRect();
    const t0 = performance.now();
    mouse(el, 'mousedown', r.x + 4, r.y + 4); mouse(el, 'mouseup', r.x + 4, r.y + 4); mouse(el, 'click', r.x + 4, r.y + 4);
    await nextFrame();
    xs.push(performance.now() - t0);
  }
  return xs;
}
"""

KEY_JS = """
async (n) => {
  const nextFrame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const list = document.querySelector('.maplist');
  if (!list) return null;
  list.focus();
  const xs = [];
  for (let i = 0; i < n; i++) {
    const t0 = performance.now();
    list.dispatchEvent(new KeyboardEvent('keydown', {key: i % 2 ? 'ArrowUp' : 'ArrowDown', bubbles: true, cancelable: true}));
    await nextFrame();
    xs.push(performance.now() - t0);
  }
  return xs;
}
"""


def summary(xs, unit="ms"):
    if not xs:
        return None
    xs = sorted(xs)
    return {"n": len(xs), "median": round(statistics.median(xs), 1),
            "p95": round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 1), "max": round(xs[-1], 1), "unit": unit}


def main() -> int:
    out: dict = {"machine": f"{platform.system()} {platform.machine()}", "base": BASE, "project": PID}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)      # a real window: rAF is not throttled
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE + "/")                                              # warm the bundle first
        t0 = time.perf_counter()
        page.goto(f"{BASE}/p/{PID}")
        page.wait_for_selector(".gcell", timeout=30000)
        out["first_interactive_view_ms"] = round((time.perf_counter() - t0) * 1000)
        page.wait_for_timeout(1200)
        page.evaluate(RECORDER_JS)

        out["display_frame_ms"] = summary(page.evaluate(FRAME_JS, 700))    # idle: the display's own cadence
        out["heap_mb_before"] = page.evaluate("performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null")
        out["cells_rendered"] = page.evaluate("document.querySelectorAll('.gcell').length")
        out["total_rows"] = page.evaluate("""() => {
            const el = [...document.querySelectorAll('.pane.left *')].find(e => e.scrollHeight > e.clientHeight + 50);
            return el ? Math.round(el.scrollHeight) : null; }""")

        page.evaluate("""() => { window.__long = []; new PerformanceObserver(l => {
            for (const e of l.getEntries()) window.__long.push(Math.round(e.duration));
        }).observe({type: 'longtask', buffered: true}); }""")

        out["cell_selection_ms"] = summary(page.evaluate(INTERACT_JS, [".gcell", 25]))
        out["mapping_key_nav_ms"] = summary(page.evaluate(KEY_JS, 25))

        # scrolling a large sheet: real wheel events on the grid, frames sampled meanwhile
        box = page.locator(".pane.left").bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.evaluate("window.__recStart()")
        for _ in range(30):
            page.mouse.move(cx, cy)
            page.mouse.wheel(0, 260)
            page.wait_for_timeout(40)
        out["scroll_frame_ms"] = summary(page.evaluate("window.__recStop()"))

        # dragging the side panel divider
        d = page.locator(".side-resizer").bounding_box()
        page.mouse.move(d["x"] + 4, d["y"] + 200)
        page.mouse.down()
        page.evaluate("window.__recStart()")
        for i in range(40):
            page.mouse.move(d["x"] + 4 - (i % 16) * 5, d["y"] + 200, steps=1)
            page.wait_for_timeout(16)
        out["drag_frame_ms"] = summary(page.evaluate("window.__recStop()"))
        page.mouse.up()

        out["long_tasks_ms"] = page.evaluate("window.__long || []")
        out["heap_mb_after"] = page.evaluate("performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null")
        out["page_errors"] = errors
        page.evaluate("() => localStorage.setItem('docweave.sideW', '0')")
        browser.close()

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 1 if out["page_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
