/** A37: the ownership recovery panel (governance/OwnershipRecovery.tsx) — what the workspace and
 *  organization lists say about an object without a usable owner, who may be named in its place,
 *  and what a role that only views is told.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data: no API, no user data. Run from the
 *  package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// Explicit browser-environment fixture, as in admin-legacy.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for the panel: answers each path from `globalThis.__fixtures`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}`;

let server, ui, mod;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "ownership-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && importer?.endsWith("/src/governance/OwnershipRecovery.tsx")) return "\0ownership-fixture-data"; },
      load(id) { if (id === "\0ownership-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  mod = await server.ssrLoadModule("/src/governance/OwnershipRecovery.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// FIXTURE: one orphan of each kind in the server's shape (GET /api/admin/ownership/orphans).
const T = 1_790_000_000;
const READY = { user_id: "u_ready", email: "ready@example.test", display_name: "Ready Member", role: "admin", status: "active",
  second_factor: true, organization_member: true, platform_admin: false, eligible: true, blockers: [] };
const NO_FACTOR = { user_id: "u_nofactor", email: "nofactor@example.test", display_name: null, role: "editor", status: "active",
  second_factor: false, organization_member: true, platform_admin: false, eligible: false, blockers: ["second_factor_required"] };
const WORKSPACE = { id: "w_orphan", name: "Fixture studio", kind: "team", status: "active", organization_id: "o_1", created_at: T, members: 3, projects: 4,
  owners: [{ user_id: "u_former", email: "former@example.test", status: "suspended", deleted: false, organization_member: true }],
  candidates: [READY, NO_FACTOR] };
const ORGANIZATION = { id: "o_orphan", name: "Fixture organization", status: "frozen", created_at: T, recover_until: null, members: 2, workspaces: 1,
  owners: [{ user_id: "u_gone", email: "gone@example.test", status: "deleted", deleted: true }],
  candidates: [{ ...READY, role: "member" }, { ...NO_FACTOR, role: "member" }] };
const orphans = ({ workspaces = [WORKSPACE], organizations = [ORGANIZATION], totals = {} } = {}) => ({
  as_of: T, limit: 50,
  workspaces: { total: totals.workspaces ?? workspaces.length, items: workspaces },
  organizations: { total: totals.organizations ?? organizations.length, items: organizations },
});
const VIEW = new Set(["platform.workspaces.view", "platform.orgs.view"]);
const MANAGE = new Set([...VIEW, "platform.workspaces.manage", "platform.orgs.manage"]);

function render(locale, element) {
  ui.setLocale(locale);
  try { return renderToStaticMarkup(element); } finally { ui.setLocale("zh-Hans"); }
}
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
function panel(locale, kind, { data = orphans(), held = MANAGE, loading = false, err = null } = {}) {
  globalThis.__fixtures = { "/api/admin/ownership/orphans": { data, loading, err } };
  return render(locale, React.createElement(mod.OwnershipRecovery, { kind, held, onDenied() {}, onChanged() {} }));
}
const picker = (locale, kind, candidates, value = "") =>
  render(locale, React.createElement(mod.CandidatePicker, { kind, candidates, value, onChange() {} }));
const tx = (locale, source, params) => ui.translate(locale, source, params);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;

test("each kind lists its orphan, says why the owner does not count and offers the handover, in every language", () => {
  for (const locale of LOCALES) {
    const ws = panel(locale, "workspace"); const wsText = text(ws);
    assert.ok(wsText.includes(tx(locale, "没有有效拥有者的工作空间")), `${locale}: ${wsText}`);
    assert.ok(ws.includes("former@example.test") && wsText.includes(tx(locale, "账户已停用")), `${locale}: the unusable owner and why`);
    assert.match(ws, new RegExp(`<button[^>]*class="btn sm"[^>]*>${tx(locale, "指定拥有者")}</button>`), `${locale}: the handover is offered`);
    assert.ok(wsText.includes(tx(locale, "{count} 个项目", { count: 4 })), `${locale}: counts, never contents`);
    assert.ok(!ws.includes("o_orphan"), "the organization's orphan belongs to the other panel");
    const org = panel(locale, "organization"); const orgText = text(org);
    assert.ok(orgText.includes(tx(locale, "没有有效拥有者的组织")), `${locale}: ${orgText}`);
    assert.ok(org.includes("gone@example.test") && orgText.includes(tx(locale, "账户已删除")), `${locale}: the deleted owner and why`);
    assert.ok(orgText.includes(tx(locale, "已冻结")), `${locale}: the organization's status`);
    assert.match(org, new RegExp(`<button[^>]*class="btn sm"[^>]*>${tx(locale, "指定拥有者")}</button>`));
    assert.ok(!org.includes("w_orphan"));
  }
  const en = text(panel("en", "workspace")) + text(panel("en", "organization"));
  assert.ok(!HAN.test(en), `untranslated in English: ${en}`);
  // the outcome's live region is on the page before any outcome, so the announcement is heard
  assert.match(panel("en", "workspace"), /<div role="status" aria-live="polite"><\/div>/);
});

test("a member without a second factor is listed but cannot be chosen, with the reason", () => {
  for (const locale of LOCALES) {
    const html = picker(locale, "workspace", [READY, NO_FACTOR]);
    const blocked = html.match(/<option value="u_nofactor"([^>]*)>([^<]*)<\/option>/);
    assert.ok(blocked && /(^|\s)disabled=""/.test(blocked[1]), `${locale}: the ineligible member is disabled: ${html}`);
    assert.ok(blocked[2].includes("nofactor@example.test") && blocked[2].includes(tx(locale, "未启用第二因素")), `${locale}: ${blocked[2]}`);
    const ready = html.match(/<option value="u_ready"([^>]*)>([^<]*)<\/option>/);
    assert.ok(ready && !ready[1].includes("disabled"), `${locale}: the eligible member can be chosen`);
    for (const part of ["Ready Member", "ready@example.test", tx(locale, "管理员"), tx(locale, "已启用第二因素")]) {
      assert.ok(ready[2].includes(part), `${locale}: ${part} in ${ready[2]}`);
    }
    const org = picker(locale, "organization", ORGANIZATION.candidates);
    assert.ok(org.match(/<option value="u_ready"[^>]*>([^<]*)<\/option>/)[1].includes(tx(locale, "成员")), `${locale}: the organization role`);
  }
  assert.ok(!HAN.test(text(picker("en", "organization", ORGANIZATION.candidates))));
  // nobody qualifies, or nobody is there: the picker says what has to happen first
  const none = text(picker("en", "workspace", [NO_FACTOR]));
  assert.ok(none.includes(tx("en", "当前没有符合条件的成员：请先让其中一位启用第二因素，或恢复其账户。")), none);
  assert.ok(text(picker("en", "organization", [])).includes(tx("en", "这个组织没有可指定的成员。")));
  assert.ok(!text(picker("en", "workspace", [READY, NO_FACTOR])).includes("No member qualifies"));
});

const HANDOVER_BUTTON = /<button[^>]*>指定拥有者<\/button>/;
test("without the manage permission the handover is not offered and the role is told why", () => {
  const html = panel("zh-Hans", "workspace", { held: VIEW });
  assert.ok(!HANDOVER_BUTTON.test(html), "no handover button");
  assert.ok(text(html).includes("你的平台角色可以查看这份清单，但不能指定拥有者。"), text(html));
  assert.ok(html.includes("former@example.test"), "the list itself is the view permission's");
  assert.ok(!html.includes('class="act"'), "no empty action column");
  // the organization panel is platform.orgs.manage's, not the workspace permission's
  const org = panel("zh-Hans", "organization", { held: new Set([...VIEW, "platform.workspaces.manage"]) });
  assert.ok(!HANDOVER_BUTTON.test(org) && text(org).includes("你的平台角色可以查看这份清单，但不能指定拥有者。"), text(org));
  assert.match(panel("zh-Hans", "workspace", { held: new Set([...VIEW, "platform.workspaces.manage"]) }), HANDOVER_BUTTON);
  // without both view permissions nothing is drawn, so no request the server would refuse is made
  assert.equal(panel("zh-Hans", "workspace", { held: new Set(["platform.workspaces.view"]) }), "");
});

test("nothing to recover says so, per kind", () => {
  for (const locale of LOCALES) {
    const ws = text(panel(locale, "workspace", { data: orphans({ workspaces: [] }) }));
    assert.ok(ws.includes(tx(locale, "当前没有缺少有效拥有者的工作空间。")), `${locale}: ${ws}`);
    assert.ok(!ws.includes("former@example.test") && !ws.includes("gone@example.test"));
    const org = text(panel(locale, "organization", { data: orphans({ organizations: [] }) }));
    assert.ok(org.includes(tx(locale, "当前没有缺少有效拥有者的组织。")), `${locale}: ${org}`);
    assert.ok(!org.includes("former@example.test"), "the workspace orphan does not make the organization list look populated");
  }
});

test("a truncated list says how many there are; loading and failure each offer a way on", () => {
  const more = text(panel("en", "workspace", { data: orphans({ totals: { workspaces: 3 } }) }));
  assert.ok(more.includes("Showing the first 1 of 3."), more);
  assert.ok(!text(panel("en", "workspace")).includes("Showing the first"));
  const loading = panel("en", "workspace", { data: null, loading: true });
  assert.ok(text(loading).includes(tx("en", "正在读取拥有者情况…")) && !loading.includes("former@example.test"), text(loading));
  const failed = panel("en", "workspace", { data: null, err: "Fixture failure" });
  assert.match(failed, new RegExp(`role="alert"[^>]*>Fixture failure <button[^>]*>${tx("en", "重试")}</button>`));
  assert.ok(!failed.includes("No workspace is without"), "a failed read is not an empty list");
});
