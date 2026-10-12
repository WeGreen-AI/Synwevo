/** The throttle card (admin coverage G19): rows as the server sends them, in the reader's language;
 *  release only for the configuration role. Isolated UI fixtures: no HTTP server, no accounts. */
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };

let server, ui, mod;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  mod = await server.ssrLoadModule("/src/governance/GovernanceTabs.tsx");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { await server?.close(); });

const T = 1_760_000_000;
// FIXTURE: synthetic throttle rows in the server's shape (admin_routes.list_throttles).
const ITEMS = [
  { id: "a".repeat(24), kind: "account_login", subject: "fixture@example.test", user_id: "u_fixture", count: 5, first_at: T, last_at: T + 30, blocked_until: null, retry_after: 4, active: true },
  { id: "b".repeat(24), kind: "registration_address", subject: "0123456789ab", user_id: null, count: 4, first_at: T, last_at: T + 60, blocked_until: T + 3600, retry_after: 3500, active: true },
  { id: "c".repeat(24), kind: "address_login", subject: null, user_id: null, count: 2, first_at: T, last_at: T, blocked_until: null, retry_after: 0, active: false },
];
const HAN = /[㐀-鿿]/;
function render(locale, props) {
  ui.setLocale(locale);
  try { return renderToStaticMarkup(React.createElement(mod.ThrottleTable, props)).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"); }
  finally { ui.setLocale("zh-Hans"); }
}

test("rows read as sentences in the reader's language and release follows the permission", () => {
  const html = render("en", { items: ITEMS, mayLift: true, onLift: () => {} });
  for (const expected of ["Account sign-in backoff", "fixture@example.test", "Waiting 4 seconds", "Registration (by address)", "Throttled until", "Source-address sign-in window",
    "Not visible", "Not in force (the next failure lengthens the wait)", "Release"]) assert.ok(html.includes(expected), `missing: ${expected}\n${html}`);
  assert.ok(!HAN.test(html.replace(/fixture@example\.test/g, "")), `untranslated text in English:\n${html}`);
  assert.equal((html.match(/>Release</g) || []).length, 3, "one release button per row");
  assert.ok(!render("en", { items: ITEMS, mayLift: false }).includes(">Release<"), "no release without the permission");
  assert.ok(render("en", { items: [], mayLift: true }).includes("No throttle is in force."));
  const hant = render("zh-Hant", { items: ITEMS, mayLift: true, onLift: () => {} });
  assert.ok(hant.includes("帳戶登入退避") && hant.includes("已限流，至") && hant.includes("解除"), hant);
});

test("the card's sentences are catalogued in English and Traditional Chinese", () => {
  const sentences = new Set(Object.values(mod.THROTTLE_KIND_LABELS));
  for (const text of ["没有生效中的限流记录。", "限流记录", "对象", "次数", "首次", "最近", "不可见", "已限流，至 {time}", "等待 {wait}", "未生效（下次失败将延长等待）", "解除", "限流状态",
    "仅显示生效中", "你的平台角色可以查看限流记录，但不能解除。", "解除限流", "将解除「{kind}」的这条限流记录（对象：{subject}）。下一次尝试从零开始；不改变口令、会话或任何权限。", "限流已解除", "解除登录退避"]) sentences.add(text);
  const missing = [], untranslated = [];
  for (const text of sentences) {
    if (!ui.hasMessage(text)) { missing.push(text); continue; }
    for (const locale of ["en", "zh-Hant"]) if (ui.translate(locale, text, { count: 2 }) === undefined) missing.push(`${locale}: ${text}`);
    if (HAN.test(ui.translate("en", text, { count: 2 }))) untranslated.push(text);
  }
  assert.deepEqual(missing, [], JSON.stringify(missing));
  assert.deepEqual(untranslated, [], JSON.stringify(untranslated));
});
