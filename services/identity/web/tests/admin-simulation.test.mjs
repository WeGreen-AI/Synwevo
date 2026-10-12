/** The permission simulation summary (admin coverage A5): the server's what-if answer in the
 *  reader's language. Isolated UI fixtures: no HTTP server, no accounts. */
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
  mod = await server.ssrLoadModule("/src/governance/PlatformAccess.tsx");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { await server?.close(); });

const SIM = { user_id: "u_fixture", change: "grant", role: "auditor", counts: true, before: { roles: [], permissions: [] },
  after: { roles: ["auditor"], permissions: ["platform.audit.view", "platform.users.view"] },
  gained: ["platform.audit.view", "platform.users.view"], lost: [], feasible: true, refusal: null };
const HAN = /[㐀-鿿]/;
function render(locale, sim) {
  ui.setLocale(locale);
  try { return renderToStaticMarkup(React.createElement(mod.SimulationSummary, { sim })).replace(/&quot;/g, '"').replace(/&#x27;/g, "'"); }
  finally { ui.setLocale("zh-Hans"); }
}

test("the simulation summary says what would change and whether the caller could do it", () => {
  const html = render("en", SIM);
  for (const expected of ["If “Audit viewer” were granted", "You could make this change now.", "Would gain:", "Would lose no permission.", "nothing is executed"]) assert.ok(html.includes(expected), `missing: ${expected}\n${html}`);
  assert.ok(!HAN.test(html), `untranslated in English:\n${html}`);
  const refused = render("en", { ...SIM, change: "revoke", role: "super_admin", feasible: false, refusal: "last_super_admin", gained: [], lost: ["platform.audit.view"], counts: false });
  assert.ok(refused.includes("were revoked") && refused.includes("You could not make this change now: At least one usable super administrator must remain.") && refused.includes("do not count at present") && refused.includes("Would lose:"), refused);
  for (const [refusal, expected] of [["super_admin_acceptance_required", "must accept the offer in person"], ["target_unverified", "is not verified yet"], ["target_cannot_reauth", "re-authentication that dangerous operations require"]]) {
    const html = render("en", { ...SIM, feasible: false, refusal, gained: [], lost: [] });
    assert.ok(html.includes(expected) && !HAN.test(html), `${refusal}:\n${html}`);
  }
  const hant = render("zh-Hant", SIM);
  assert.ok(hant.includes("若授予") && hant.includes("你現在可以執行這一變化。"), hant);
});

test("the simulation's sentences are catalogued in English and Traditional Chinese", () => {
  const sentences = new Set(Object.values(mod.SIMULATION_REFUSALS));
  for (const text of ["若授予「{role}」", "若撤销「{role}」", "你现在可以执行这一变化。", "你现在不能执行这一变化：{reason}", "该账户的授予目前不计入（邮箱未验证或账户不可用），所以有效权限不会变化。",
    "将新增权限：{list}", "不会新增权限。", "将失去权限：{list}", "不会失去权限。", "模拟只按角色目录与计入规则计算，不执行任何变更，也不改变任何授予。", "模拟一次变化", "变化", "授予", "正在模拟…", "模拟"]) sentences.add(text);
  const missing = [], untranslated = [];
  for (const text of sentences) {
    if (!ui.hasMessage(text)) { missing.push(text); continue; }
    for (const locale of ["en", "zh-Hant"]) if (ui.translate(locale, text, { count: 2 }) === undefined) missing.push(`${locale}: ${text}`);
    if (HAN.test(ui.translate("en", text, { count: 2 }))) untranslated.push(text);
  }
  assert.deepEqual(missing, [], JSON.stringify(missing));
  assert.deepEqual(untranslated, [], JSON.stringify(untranslated));
});
