/** The platform overview's cards for the historical data (the pre-account workspace): what the
 *  assigned card and the undo's confirmation say, in each language, and how the page keeps working
 *  for the administrator who just undid an assignment.
 *
 *  Each page is rendered the way the browser renders it (vite's module graph, React's server
 *  renderer), with the administration data loader replaced by fixture data: no API, no user data.
 *  Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// Explicit browser-environment fixture, as in refusals.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./governance/data`: answers each path from `globalThis.__fixtures`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage() {
  return { data: { items: [], total: 0, offset: 0, limit: 50, snapshot: 0 }, err: null, loading: false, reload() {}, offset: 0, setOffset() {} };
}`;

let server, ui, client, admin;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "admin-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./governance/data" && importer?.endsWith("/src/Admin.tsx")) return "\0admin-fixture-data"; },
      load(id) { if (id === "\0admin-fixture-data") return FIXTURE_LOADER; },
      // the page's parts, reachable for rendering one at a time
      transform(code, id) { if (id.endsWith("/src/Admin.tsx")) return code + "\nexport { OverviewTab, UnclaimImpact };\n"; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  admin = await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

const PLAN = {
  workspace: { id: "default", name: "Legacy data", status: "active" }, name_after: "Legacy data (unassigned)", name_restored: true,
  members: [
    { user_id: "u_1", email: "owner@example.test", display_name: "Fixture Owner", role: "owner" },
    { user_id: "u_2", email: "second@example.test", display_name: null, role: "editor" },
  ],
  invitations: 0, tokens: 0, service_accounts: 0, approvals: 0, operator_keys: 0, jobs_stopping: 0, projects: 3, templates: 2,
  assigned: { at: 1790000000, by_email: "admin@example.test", by_name: "Fixture Admin", to_email: "owner@example.test" },
};
const overview = (status, extra = {}) => ({
  users: 3, suspended: 0, workspaces: 2, admins: 1,
  unclaimed: { id: "default", name: "Legacy data", kind: "legacy", status, members: 2, projects: 3, apps: [], ...extra },
});

function render(locale, element) {
  ui.setLocale(locale);
  try { return renderToStaticMarkup(element); } finally { ui.setLocale("zh-Hans"); }
}
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
// A super administrator's permissions (the claim, its undo and the account search); a role without
// them is covered in admin-access.test.mjs.
const SUPER = new Set(["platform.overview.view", "platform.users.view", "platform.workspaces.claim"]);
function page(locale, status, { plan = PLAN, loading = false, extra, held = SUPER } = {}) {
  globalThis.__fixtures = {
    "/api/admin/overview": { data: overview(status, extra), loading },
    "/api/admin/workspaces/default/unclaim-preview": { data: { ...plan, workspace: { ...plan.workspace, status } } },
  };
  return render(locale, React.createElement(admin.OverviewTab, { onDenied() {}, held }));
}
const impact = (locale, plan) => text(render(locale, React.createElement(admin.UnclaimImpact, { plan })));
const HAN = /[㐀-鿿]/;

test("a frozen assignment shows who holds it and offers the undo, without sending anyone to unfreeze it first", () => {
  for (const locale of ["zh-Hans", "zh-Hant", "en"]) {
    const html = page(locale, "frozen");
    assert.match(html, /owner@example\.test/, `${locale}: the owner is listed`);
    assert.match(html, /admin@example\.test/, `${locale}: and who assigned it`);
    assert.match(html, /<button[^>]*class="btn danger"[^>]*>/, `${locale}: the undo is offered`);
  }
  const zh = text(page("zh-Hans", "frozen"));
  assert.ok(zh.includes("撤销指派会同时结束冻结") && !zh.includes("请先"), zh);
  const en = text(page("en", "frozen"));
  assert.ok(en.includes("Undo assignment") && en.includes("ends the freeze with it"), en);
  assert.ok(!/unfreeze it first under/i.test(en), en);
  // the confirmation says it too
  assert.ok(impact("en", { ...PLAN, workspace: { ...PLAN.workspace, status: "frozen" } }).includes("no need to unfreeze it first"));
});

test("counts in the cards and the confirmation read as English", () => {
  assert.ok(impact("en", { ...PLAN, projects: 1, templates: 1 }).includes("No data is deleted: 1 project and 1 template stay as they are."));
  assert.ok(impact("en", PLAN).includes("No data is deleted: 3 projects and 2 templates stay as they are."));
  assert.ok(impact("zh-Hans", PLAN).includes("不会删除任何数据：3 个项目和 2 个模板原样保留。"));
  assert.ok(impact("zh-Hant", PLAN).includes("不會刪除任何資料：3 個專案和 2 個範本原樣保留。"));
  // the card that assigns the data, which read "1 projects, 1 members"
  const pending = text(page("en", "pending_claim", { extra: { projects: 1, members: 1 } }));
  assert.ok(pending.includes("1 project, 1 member. Choose an existing account"), pending);
  assert.ok(text(page("zh-Hans", "pending_claim")).includes("3 个项目，2 位成员。请选择"));
});

test("the confirmation says what keeps working, what stops and what comes back", () => {
  const plan = { ...PLAN, operator_keys: 2, jobs_stopping: 1, service_accounts: 1, approvals: 1 };
  const en = impact("en", plan);
  assert.ok(en.includes("Exception: 2 dw_ access keys issued by an operator are not affected by the undo."), en);
  assert.ok(en.includes("1 queued or running generation job stops"), en);
  assert.ok(en.includes("an approved credential works again only if the data goes back to the account that approved it"), en);
  assert.ok(en.includes("no account can open it, platform administrators included"), en);
  assert.ok(!/nobody can open it/i.test(en), "operator keys still can: the sentence says accounts");
  assert.ok(!HAN.test(en), `untranslated in English: ${en}`);
  for (const locale of ["zh-Hans", "zh-Hant"]) {
    const zh = impact(locale, plan);
    assert.ok(/dw_/.test(zh) && /2 個|2 个/.test(zh), zh);
  }
  const none = impact("en", PLAN);
  assert.ok(!none.includes("Exception:") && !none.includes("generation job"), none);
});

test("the outcome is announced from a live region that is on the page before it and through the reload", () => {
  for (const [status, loading] of [["active", false], ["pending_claim", false], ["active", true]]) {
    const html = page("en", status, { loading });
    assert.match(html, /^<div role="status" aria-live="polite"><\/div>/, `${status}${loading ? " (loading)" : ""}: ${html.slice(0, 120)}`);
  }
});

test("a tab whose workspace context names the unassigned workspace stops sending it", async () => {
  // what the overview calls once the undo succeeded (AssignedCard's onDone)
  const context = await server.ssrLoadModule("/src/governance/workspaceContext.ts");
  client.setWorkspaceContext("default", { initial: true });
  assert.equal(client.authHeaders("GET", false)["X-Synwevo-Workspace"], "default");
  assert.equal(context.releaseWorkspaceContext("default"), true);
  assert.equal(client.authHeaders("GET", false)["X-Synwevo-Workspace"], undefined);
  // another workspace's tab is left alone
  client.setWorkspaceContext("w_own", { initial: true });
  assert.equal(context.releaseWorkspaceContext("default"), false);
  assert.equal(client.authHeaders("GET", false)["X-Synwevo-Workspace"], "w_own");
  client.setWorkspaceContext(null, { initial: true });
});

test("without the claim permission the assigned card names no one and offers no undo", () => {
  const html = page("zh-Hans", "active", { held: new Set(["platform.overview.view"]) });
  assert.ok(text(html).includes("只有最高管理员可以查看指派详情或撤销指派。"), text(html));
  assert.ok(!/<button[^>]*>撤销指派</.test(html), "no undo button");
  assert.ok(!html.includes("owner@example.test"), "the members are the claim permission's to see");
});
