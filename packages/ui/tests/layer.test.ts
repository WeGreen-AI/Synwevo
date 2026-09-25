// Tests for anchored-layer placement (packages/ui/src/layer.ts): the decisions behind every
// row menu and popover. Run: node --test packages/ui/tests/
import { test } from "node:test";
import assert from "node:assert/strict";
import { LAYER_GAP, LAYER_MARGIN, anchorVisible, placeLayer } from "../src/layer.ts";

const viewport = { width: 1200, height: 800 };
const menu = { width: 200, height: 160 };

test("a menu near the top opens below its button, right edges aligned", () => {
  const p = placeLayer({ top: 100, left: 900, width: 60, height: 24 }, menu, viewport, { align: "end" });
  assert.equal(p.side, "bottom");
  assert.equal(p.top, 100 + 24 + LAYER_GAP);
  assert.equal(p.left, 900 + 60 - 200);
  assert.ok(p.maxHeight >= menu.height);
});

test("the last row's menu flips above the button when there is no room below", () => {
  const p = placeLayer({ top: 760, left: 900, width: 60, height: 24 }, menu, viewport, { align: "end" });
  assert.equal(p.side, "top");
  assert.equal(p.top + menu.height + LAYER_GAP, 760);
  assert.ok(p.top >= LAYER_MARGIN);
});

test("a short viewport caps the height so the menu scrolls inside itself", () => {
  const short = { width: 375, height: 300 };
  const p = placeLayer({ top: 150, left: 300, width: 60, height: 24 }, { width: 200, height: 400 }, short, { align: "end" });
  assert.ok(p.maxHeight < 400);
  assert.ok(p.maxHeight > 0);
  assert.ok(p.top + p.maxHeight <= short.height - LAYER_MARGIN + 1);
});

test("a menu is shifted inside the viewport on a narrow screen", () => {
  const phone = { width: 375, height: 700 };
  const end = placeLayer({ top: 100, left: 330, width: 40, height: 24 }, menu, phone, { align: "end" });
  assert.ok(end.left >= LAYER_MARGIN);
  assert.ok(end.left + menu.width <= phone.width - LAYER_MARGIN);
  const start = placeLayer({ top: 100, left: 5, width: 40, height: 24 }, menu, phone, { align: "start" });
  assert.equal(start.left, LAYER_MARGIN);
});

test("an anchor that scrolled out of view is reported so the layer can close", () => {
  assert.equal(anchorVisible({ top: -30, left: 10, width: 40, height: 24 }, viewport), false);
  assert.equal(anchorVisible({ top: 810, left: 10, width: 40, height: 24 }, viewport), false);
  assert.equal(anchorVisible({ top: 790, left: 10, width: 40, height: 24 }, viewport), true);
});
