import { test } from "node:test";
import assert from "node:assert/strict";
import { brandLinkName } from "../src/brand-name.ts";

// WCAG 2.5.3: a link's accessible name contains the words shown on it, so "click Synwevo Account"
// works for voice-control users; and the brand is not announced twice.
test("a purpose-only label is prefixed with the visible brand and product", () => {
  assert.equal(brandLinkName("Account", "返回平台首页"), "Synwevo Account · 返回平台首页");
  assert.equal(brandLinkName("Account", "Back to platform home"), "Synwevo Account · Back to platform home");
});

test("a label that already names the brand gets the visible product, never a second Synwevo", () => {
  assert.equal(brandLinkName("Workspace", "Synwevo 首页"), "Synwevo Workspace 首页");
  assert.equal(brandLinkName("Workspace", "Synwevo home"), "Synwevo Workspace home");
  assert.equal(brandLinkName(undefined, "Synwevo 首页"), "Synwevo 首页");
  assert.equal(brandLinkName("Docs", "Synwevo Docs 首页"), "Synwevo Docs 首页");
});

test("no label means the name comes from the logo's alt text and the product text", () => {
  assert.equal(brandLinkName("Docs", undefined), undefined);
});
