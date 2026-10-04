import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// The narrow-screen rules of Synwevo Tables, read from its stylesheet. A browser check of the
// same pages at 390 px is in the release acceptance; this keeps the rules from regressing.
const CSS = readFileSync(join(import.meta.dirname, "..", "src", "apps", "tables", "tables.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The body of the first `@media` block whose condition contains `condition`. */
function media(css: string, condition: string): string {
  const start = css.indexOf(`@media ${condition}`);
  assert.ok(start >= 0, `no @media ${condition}`);
  let depth = 0;
  for (let i = css.indexOf("{", start); i < css.length; i++) {
    if (css[i] === "{") depth++;
    else if (css[i] === "}" && --depth === 0) return css.slice(css.indexOf("{", start) + 1, i);
  }
  throw new Error("unbalanced @media block");
}

/** Declarations of `selector` (rules listing it among several selectors count), later ones win. */
function declarations(css: string, selector: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1].split(",").map(s => s.trim()).includes(selector)) continue;
    for (const d of m[2].split(";")) {
      const at = d.indexOf(":");
      if (at > 0) out[d.slice(0, at).trim()] = d.slice(at + 1).trim();
    }
  }
  return out;
}

const NARROW = media(CSS, "(max-width: 720px)");

test("field settings: on a phone the field list scrolls inside the page instead of widening it", () => {
  // A bare `1fr` track has an automatic minimum: the horizontal field list's full width
  // becomes the track's minimum and the page grows to it (1040 px at 390 px).
  const columns = declarations(NARROW, ".tables-schema")["grid-template-columns"];
  assert.ok(columns, "the narrow layout sets the schema columns");
  for (const track of columns.split(/\s+(?![^(]*\))/)) {
    assert.match(track, /^minmax\(0(px)?,/, `track "${track}" lets content widen the page`);
  }
  assert.equal(declarations(NARROW, ".tables-schema aside")["min-width"], "0");
  assert.equal(declarations(NARROW, ".tables-schema aside ul")["overflow-x"], "auto");
});

test("export menu: on a phone it spans the toolbar row instead of running past the screen", () => {
  // Anchored to its own button, a 340 px menu starting at "Export" (further right in English)
  // ran to 449 px. On a phone the toolbar is its containing block and it spans that row.
  assert.equal(declarations(NARROW, ".tables-menu").position, "static");
  assert.equal(declarations(NARROW, ".tables-toolbar").position, "relative");
  const popover = declarations(NARROW, ".tables-popover");
  assert.equal(popover.left, "0");
  assert.equal(popover.right, "0");
  assert.equal(popover.width, "auto");
});
