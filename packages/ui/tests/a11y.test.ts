// Tests for the shared keyboard/focus/motion decisions (packages/ui/src/a11y.ts).
// Run: node --test packages/ui/tests/   (Node >= 23.6 strips the TypeScript types itself)
import { test } from "node:test";
import assert from "node:assert/strict";
import { anyModalOpen, isImeKey, isTopModal, popModal, pushModal, scrollBehavior, trapIndex } from "../src/a11y.ts";

test("IME: isComposing on the native event counts as composing", () => {
  assert.equal(isImeKey({ key: "Enter", nativeEvent: { isComposing: true } }), true);
  assert.equal(isImeKey({ key: "Escape", isComposing: true }), true);
});

test("IME: Safari commits a candidate with keyCode 229 and isComposing=false", () => {
  assert.equal(isImeKey({ key: "Enter", keyCode: 229, isComposing: false }), true);
  assert.equal(isImeKey({ key: "Enter", nativeEvent: { isComposing: false, keyCode: 229 } }), true);
});

test("IME: an ordinary Enter/Escape is not composition", () => {
  assert.equal(isImeKey({ key: "Enter", keyCode: 13, nativeEvent: { isComposing: false, keyCode: 13 } }), false);
  assert.equal(isImeKey({ key: "Escape", keyCode: 27 }), false);
  assert.equal(isImeKey(null), false);
});

test("Tab trap wraps at both ends and enters from the container", () => {
  assert.equal(trapIndex(3, 2, false), 0);      // Tab on the last control -> first
  assert.equal(trapIndex(3, 0, true), 2);       // Shift+Tab on the first -> last
  assert.equal(trapIndex(3, 1, false), 2);
  assert.equal(trapIndex(3, -1, false), 0);     // focus on the dialog itself
  assert.equal(trapIndex(3, -1, true), 2);
  assert.equal(trapIndex(0, -1, false), -1);
});

test("Only the topmost modal is on top; nothing is open once all are closed", () => {
  const a = pushModal();
  const b = pushModal();                        // a confirm raised over a dialog
  assert.equal(isTopModal(b), true);
  assert.equal(isTopModal(a), false);           // Escape must not close the dialog underneath
  assert.equal(anyModalOpen(), true);
  popModal(b);
  assert.equal(isTopModal(a), true);
  popModal(a);
  assert.equal(anyModalOpen(), false);
  popModal(a);                                  // closing twice is harmless
  assert.equal(anyModalOpen(), false);
});

test("Reduced motion: programmatic scrolling does not animate", () => {
  const g = globalThis as any;
  const before = g.window;
  try {
    g.window = { matchMedia: (q: string) => ({ matches: q.includes("reduce") }) };
    assert.equal(scrollBehavior(), "auto");
    g.window = { matchMedia: () => ({ matches: false }) };
    assert.equal(scrollBehavior(), "smooth");
    g.window = { matchMedia: () => { throw new Error("blocked"); } };
    assert.equal(scrollBehavior(), "smooth");
  } finally {
    g.window = before;
  }
});
