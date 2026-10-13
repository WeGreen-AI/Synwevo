/** A44-5: an organization's own audit log (pages/org/OrganizationAudit.tsx) words the platform's
 *  lifecycle acts on one of its workspaces — `organization.workspace.rename`, `.delete` and
 *  `.recover`, each recorded with `by: platform` (synwevo_core.workspace_lifecycle._record) — with
 *  their name or state change, "由平台管理执行" and the reason, in the three languages.
 *
 *  The rows are FIXTURE items shaped as GET /api/auth/organizations/{id}/audit answers them
 *  (synwevo_core.organization_audit.read: `detail` is the allowlisted metadata — reason, before,
 *  after, by). No API, no user data. Run from the package: `npm test`. */
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
const REASON = "Fixture reason for the platform act";
// FIXTURE: the `detail` of each act as the organization's log answers it
const DETAIL = {
  "organization.workspace.rename": { by: "platform", reason: REASON, before: { name: "Fixture Before" }, after: { name: "Fixture After" } },
  "organization.workspace.delete": { by: "platform", reason: REASON, before: { status: "active" }, after: { status: "deleted" } },
  "organization.workspace.recover": { by: "platform", reason: REASON, before: { status: "deleted" }, after: { status: "frozen" } },
};
const tFor = (locale) => (source, params) => ui.translate(locale, source, params);

test("the platform's rename, deletion and recovery of a workspace are labelled in the organization's log, in every language", () => {
  const labels = { "organization.workspace.rename": "重命名了工作空间", "organization.workspace.delete": "删除了工作空间",
    "organization.workspace.recover": "恢复了工作空间" };
  for (const [action, label] of Object.entries(labels)) {
    assert.equal(audit.ACTION_LABEL[action], label, action);
    assert.ok(catalogue[label]?.en && catalogue[label]?.["zh-Hant"], `${label} is catalogued`);
    for (const locale of LOCALES) {
      const said = audit.actionText(action, tFor(locale));
      assert.ok(said && said !== action, `${locale} ${action}: ${said}`);
      if (locale === "en") assert.ok(!HAN.test(said), said);
    }
  }
  // every lifecycle act core records for an organization's workspace has its label here
  const core = readFileSync(join(ROOT, "packages/core/synwevo_core/workspace_lifecycle.py"), "utf8");
  assert.match(core, /event = "organization\.workspace\." \+ action/);
  const actions = [...readFileSync(join(ROOT, "packages/core/synwevo_core/workspace_lifecycle.py"), "utf8")
    .matchAll(/^ACTIONS\s*=\s*\(([^)]*)\)/gm)].flatMap((m) => [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]));
  assert.deepEqual(actions.sort(), ["delete", "recover", "rename"], "core's actions, as read from workspace_lifecycle.py");
  for (const a of actions) assert.ok(audit.ACTION_LABEL[`organization.workspace.${a}`], a);
});

test("each act shows its name or state change, that the platform performed it, and the reason", () => {
  for (const locale of LOCALES) {
    const t = tFor(locale);
    const said = (action) => audit.auditDetails(DETAIL[action], t);
    assert.deepEqual(said("organization.workspace.delete"), [
      t("状态：{from} → {to}", { from: t("正常"), to: t("已删除") }), t("由平台管理执行"), t("原因：{reason}", { reason: REASON })], locale);
    assert.deepEqual(said("organization.workspace.recover"), [
      t("状态：{from} → {to}", { from: t("已删除"), to: t("已冻结") }), t("由平台管理执行"), t("原因：{reason}", { reason: REASON })], locale);
    assert.deepEqual(said("organization.workspace.rename"), [
      t("名称：{from} → {to}", { from: "Fixture Before", to: "Fixture After" }), t("由平台管理执行"), t("原因：{reason}", { reason: REASON })], locale);
    if (locale === "en") {
      for (const action of Object.keys(DETAIL)) {
        const line = said(action).join(" · ").replace(/Fixture (Before|After)/g, "");
        assert.ok(!HAN.test(line), `${action}: ${line}`);
      }
      assert.match(said("organization.workspace.delete")[0], /^Status: \S.* → \S/);
    }
  }
  // an owner's own act carries no `by: platform` and is not said to be the platform's
  const own = audit.auditDetails({ reason: REASON, before: { status: "active" }, after: { status: "frozen" } }, tFor("zh-Hans"));
  assert.ok(!own.includes("由平台管理执行"), own.join(" · "));
  // an unchanged state says nothing about it; a role change still reads as before
  assert.deepEqual(audit.auditDetails({ before: { status: "active" }, after: { status: "active" } }, tFor("zh-Hans")), []);
  assert.deepEqual(audit.auditDetails({ before: { role: "member" }, after: { role: "admin" } }, tFor("zh-Hans")), ["成员 → 管理员"]);
});

test("the new sentences are catalogued with both translations and their parameters", () => {
  const added = ["状态：{from} → {to}", "重命名了工作空间", "删除了工作空间", "恢复了工作空间"];
  const subset = Object.fromEntries(added.map((k) => [k, catalogue[k]]));
  assert.deepEqual(ui.validateMessages(subset), []);
});
