/** Release acceptance defect 5: at 390 px the application frame's section strip scrolled
 *  sideways with its scrollbar hidden, so later sections were cut off with no sign that the
 *  strip scrolls (Extract's "Extraction templates" and "Review queue" ended at 492 and 609 px).
 *  Every application uses this one frame (shared/AppFrame.tsx), so the rule is held here: the
 *  sections wrap onto further rows instead of hiding behind an invisible scroll. The rendered
 *  check at phone width is in the commit's browser evidence. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const CSS = readFileSync(join(import.meta.dirname, "..", "src", "suite.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** Declarations of every rule (media queries included) whose selector list names `selector`. */
function declarations(selector: string): Record<string, string>[] {
  const out: Record<string, string>[] = [];
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1].split(",").some((s) => s.trim() === selector)) continue;
    const decl: Record<string, string> = {};
    for (const part of m[2].split(";")) {
      const at = part.indexOf(":");
      if (at > 0) decl[part.slice(0, at).trim()] = part.slice(at + 1).trim();
    }
    out.push(decl);
  }
  return out;
}

test("the section strip wraps instead of scrolling behind a hidden scrollbar", () => {
  const strip = declarations(".suite-sections");
  assert.ok(strip.length > 0, "the strip's rule exists");
  assert.ok(strip.some((d) => d["flex-wrap"] === "wrap"), "sections wrap onto further rows");
  for (const d of strip) {
    assert.notEqual(d["scrollbar-width"], "none", "the strip does not hide a scrollbar");
    assert.ok(!["auto", "scroll", "hidden"].includes(d["overflow-x"] ?? d["overflow"] ?? ""), "the strip does not clip or scroll its sections");
  }
  assert.deepEqual(declarations(".suite-sections::-webkit-scrollbar"), [], "no hidden-scrollbar rule is left behind");
});

test("a section label that does not fit a phone row may break instead of widening the page", () => {
  const tab = declarations(".suite-section");
  assert.ok(tab.some((d) => d["max-width"] === "100%"), "a section is never wider than the strip");
});
