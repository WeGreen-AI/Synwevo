/** B3: an organization's own audit log (pages/org/OrganizationAudit.tsx) words the platform
 *  switching Synwevo Message off or on for it — `organization.app.disable` / `.enable`, recorded
 *  with `by: platform`, the application and the reason (synwevo_identity.admin_apps `_apply`, and
 *  the CLI) — naming the application, in the three languages, instead of showing the action code.
 *
 *  The rows are FIXTURE items shaped as GET /api/auth/organizations/{id}/audit answers them
 *  (synwevo_core.organization_audit.read: `detail` is the allowlisted metadata — app_id, enabled,
 *  by, reason). No API, no user data. Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));

// Explicit browser-environment fixture, as in flow.validation.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/organizations/org_fx_one"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

let server, ui, audit, catalogue;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  catalogue = (await server.ssrLoadModule("/src/organization.messages.ts")).messages;
  audit = await server.ssrLoadModule("/src/pages/org/OrganizationAudit.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const REASON = "Fixture reason for the platform switch";
// FIXTURE: the `detail` of each act as the organization's log answers it
const DETAIL = {
  "organization.app.disable": { by: "platform", app_id: "messages", enabled: false, reason: REASON },
  "organization.app.enable": { by: "platform", app_id: "messages", enabled: true, reason: REASON },
};
const tFor = (locale) => (source, params) => ui.translate(locale, source, params);

test("the platform switching Synwevo Message off or on is labelled in the organization's log, naming it, in every language", () => {
  for (const [action, detail] of Object.entries(DETAIL)) {
    assert.ok(audit.ACTION_LABEL[action], `${action} has a label`);
    assert.ok(catalogue[audit.ACTION_LABEL[action]]?.en && catalogue[audit.ACTION_LABEL[action]]?.["zh-Hant"], `${action}: label catalogued`);
    for (const locale of LOCALES) {
      const t = tFor(locale);
      const bare = audit.actionText(action, t);
      assert.ok(bare && bare !== action, `${locale} ${action} without detail: ${bare}`);
      const named = audit.actionText(action, t, detail);
      assert.ok(named.includes("Synwevo Message"), `${locale} ${action} names the application: ${named}`);
      if (locale === "en") assert.ok(!HAN.test(named) && !HAN.test(bare), `${named} / ${bare}`);
    }
  }
  assert.equal(audit.actionText("organization.app.disable", tFor("en"), DETAIL["organization.app.disable"]), "turned Synwevo Message off for the organization");
  assert.equal(audit.actionText("organization.app.enable", tFor("zh-Hans"), DETAIL["organization.app.enable"]), "为组织启用了 Synwevo Message");
  // an application this page does not name is said by its id, never dropped
  assert.equal(audit.actionText("organization.app.disable", tFor("zh-Hans"), { app_id: "fixture_app" }), "为组织停用了 fixture_app");
  // every organization switch the account service records has its label here
  for (const file of ["services/identity/synwevo_identity/admin_apps.py", "services/identity/synwevo_identity/cli.py"]) {
    assert.match(readFileSync(join(ROOT, file), "utf8"), /f"organization\.app\.\{(change|args\.action)\}"/, file);
  }
  const changes = readFileSync(join(ROOT, "services/identity/synwevo_identity/admin_apps.py"), "utf8").match(/^ORGANIZATION_CHANGES\s*=\s*\(([^)]*)\)/m);
  assert.ok(changes, "ORGANIZATION_CHANGES read from admin_apps.py");
  for (const change of [...changes[1].matchAll(/"(\w+)"/g)].map((m) => m[1])) assert.ok(audit.ACTION_LABEL[`organization.app.${change}`], change);
});

test("each switch shows the new state, that the platform performed it, and the reason", () => {
  for (const locale of LOCALES) {
    const t = tFor(locale);
    assert.deepEqual(audit.auditDetails(DETAIL["organization.app.disable"], t), [t("已关闭"), t("由平台管理执行"), t("原因：{reason}", { reason: REASON })], locale);
    assert.deepEqual(audit.auditDetails(DETAIL["organization.app.enable"], t), [t("已开启"), t("由平台管理执行"), t("原因：{reason}", { reason: REASON })], locale);
  }
});

test("the new sentences are catalogued with both translations and their parameters", () => {
  const added = ["为组织停用了应用", "为组织启用了应用", "为组织停用了 {app}", "为组织启用了 {app}"];
  const subset = Object.fromEntries(added.map((k) => [k, catalogue[k]]));
  assert.deepEqual(ui.validateMessages(subset), []);
  for (const k of added) assert.ok(catalogue[k], k);
});
