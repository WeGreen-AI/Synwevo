/** B1/B2: the application catalogue on the administration page — the tab is offered by
 *  `platform.apps.view` (governance/access.ts, admin/navigation.ts), each application shows its
 *  status, policy and counts (governance/AppsTab.tsx), the per-workspace list shows each
 *  workspace's state for it, and every change — offered only for `platform.apps.manage` — opens a
 *  preview dialog (governance/AppChangeDialog.tsx) that states who loses or gains access, the
 *  workspaces, the running work and the server's notes before the reason and re-authentication.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data and the dialog's preview rendered from
 *  a fixture plan: no API, no user data. The roles are read from platform_access.py itself, as in
 *  admin-access.test.mjs. Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

// ------------------------------------------------------------------------------------ roles
const PY = readFileSync(join(ROOT, "packages/core/synwevo_core/platform_access.py"), "utf8");
const block = (start, end) => { const i = PY.indexOf(start); assert.ok(i >= 0, start); return PY.slice(i, PY.indexOf(end, i)); };
const PERMISSIONS = [...block("PERMISSIONS: dict[str, str] = {", "\n}\n").matchAll(/^\s+"([a-z_.]+)":/gm)].map((m) => m[1]);
const ROLES = { super_admin: new Set(PERMISSIONS) };
for (const m of block("ROLES: dict[str, frozenset[str]] = {", "\n}\n").matchAll(/"(\w+)": frozenset\(\{([^}]*)\}\)/g)) {
  ROLES[m[1]] = new Set([...m[2].matchAll(/"([a-z_.]+)"/g)].map((x) => x[1]));
}

// Explicit browser-environment fixture, as in admin-invitations.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin?tab=apps"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for AppsTab.tsx: answers each path from `globalThis.__fixtures`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  const items = f.items ?? [];
  return { data: { items, total: items.length, offset: 0, limit: 50, snapshot: 0 }, err: null, loading: false, reload() {}, offset: 0, setOffset() {} };
}`;

let server, ui, client, access, nav, tab, dialog, messages, labels;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "apps-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && importer?.endsWith("/src/governance/AppsTab.tsx")) return "\0apps-fixture-data"; },
      load(id) { if (id === "\0apps-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  access = await server.ssrLoadModule("/src/governance/access.ts");
  nav = await server.ssrLoadModule("/src/admin/navigation.ts");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  messages = await server.ssrLoadModule("/src/governance/apps.messages.ts");
  dialog = await server.ssrLoadModule("/src/governance/AppChangeDialog.tsx");
  tab = await server.ssrLoadModule("/src/governance/AppsTab.tsx");
  // the page's whole catalogue, as the browser has it on the administration page
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// ------------------------------------------------------------------------------------ fixtures
// FIXTURE: GET /api/admin/apps, GET /api/admin/apps/{id}/workspaces and the preview, as the brief's
// contract has the server answer them — counts and metadata only. Synthetic ids and names.
const T = 1_790_000_000;
// `scope` and `changes` are the server's additive fields (admin_apps.py `_item`): how access is decided,
// and the platform-wide changes it would accept now.
const APP = { id: "fx_docs", name: "Fixture Docs", status: "active", access_policy: "members", launch_url: "https://docs.example.test/",
  workspaces_enabled: 3, members_with_access: 12, explicit_grants: 2, explicit_revocations: 1, running_jobs: 4,
  scope: "workspace", changes: ["disable", "policy_explicit"] };
const OFF = { ...APP, id: "fx_tools", name: "Fixture Tools", status: "disabled", access_policy: "explicit", launch_url: null, running_jobs: null,
  changes: ["enable", "policy_members"] };
const ORG = { ...APP, id: "fx_chat", name: "Fixture Chat", workspaces_enabled: 0, explicit_grants: 0, explicit_revocations: 0, running_jobs: null,
  scope: "organization", changes: ["disable"] };
const HELD = { ...OFF, id: "fx_held", name: "Fixture Held", changes: ["policy_members"] };
const WS = { id: "ws_fx_on", name: "Fixture Workspace On", kind: "team", status: "active", organization_id: "org_fx_one",
  enabled: true, enabled_at: T, members: 5, members_with_access: 4 };
const WORKSPACES = [WS,
  { ...WS, id: "ws_fx_off", name: "Fixture Workspace Off", enabled: false, enabled_at: null, members_with_access: 0 },
  { ...WS, id: "ws_fx_frozen", name: "Fixture Workspace Frozen", status: "frozen", enabled: false, enabled_at: null, members_with_access: 0 },
  { ...WS, id: "ws_fx_legacy", name: "Fixture Workspace Legacy", kind: "personal", status: "pending_claim", organization_id: null, enabled: true }];
// people: distinct people losing (gaining) access in at least one workspace; memberships: person × workspace pairs
const IMPACT = { people_losing_access: 7, memberships_losing_access: 11, people_gaining_access: 6, memberships_gaining_access: 8,
  workspaces_affected: 3, running_jobs: 4, sessions_in_app: 9, credentials_stopping: 2 };
const NOTES = ["data_kept", "links_stop", "jobs_continue_or_stop", "explicit_grants_kept", "revocations_kept_on_loosen",
  "webhooks_disabled", "delegations_end_at_refresh", "integrations_need_reauthorization", "approved_credentials_stop"];
const plan = (change, over = {}) => ({ change, scope: "platform", target: { ...APP }, impact: IMPACT, notes: NOTES,
  expires_at: T + 300, preview_token: "fixture-signed-preview", ...over });
const wsPlan = (change) => plan(change, { scope: "workspace", target: { ...APP, workspace: WS }, impact: { ...IMPACT, workspaces_affected: 1 } });

const VIEW = new Set(["platform.apps.view"]);
// Per-workspace switches: platform.apps.manage; platform-scope changes also need platform.config.manage.
const WORKSPACE_MANAGE = new Set(["platform.apps.view", "platform.apps.manage"]);
const MANAGE = new Set(["platform.apps.view", "platform.apps.manage", "platform.config.manage"]);
const PLATFORM_HINT = "平台范围的变更（全平台启用、停用和访问策略）需要最高管理员；你可以在「按工作空间」里为单个工作空间启用或停用应用。";
const CHANGES = ["disable", "enable", "policy_explicit", "policy_members"];
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /Fixture (?:Docs|Tools|Chat|Held|Workspace (?:On|Off|Frozen|Legacy))|fx_[a-z]+|ws_fx_[a-z]+|org_fx_[a-z]+|https:\/\/docs\.example\.test\//g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const row = (html, id) => html.split("<tr").find((part) => part.includes(id)).split("</tr>")[0];
const label = (locale, key) => tx(locale, dialog.CHANGE_LABELS[key]);
function appsTab(locale, held, items = [APP, OFF, ORG, HELD]) {
  globalThis.__fixtures = { "/api/admin/apps": { data: { items } } };
  return render(locale, React.createElement(tab.AppsTab, { onDenied() {}, held }));
}
function drawer(locale, held, items = WORKSPACES) {
  globalThis.__fixtures = { [`/api/admin/apps/${APP.id}/workspaces`]: { items } };
  return render(locale, React.createElement(tab.AppWorkspacesDrawer, { app: APP, held, onDenied() {}, onClose() {}, onChanged() {} }));
}
const impact = (locale, change, p = plan(change)) => render(locale, React.createElement(dialog.AppChangeImpact, { change, plan: p }));
const fullDialog = (locale, change, workspace) => render(locale, React.createElement(dialog.AppChangeDialog,
  { app: APP, change, workspace, onClose() {}, onDone() {} }));

// ------------------------------------------------------------------------------------ tests
test("the catalogue is offered by platform.apps.view: the auditor and support see it, finance and the API administrator do not", () => {
  assert.equal(access.TAB_PERMISSION.apps, "platform.apps.view");
  assert.ok(PERMISSIONS.includes("platform.apps.view"));
  const ids = (held) => nav.adminGroups(held).flatMap((group) => group.items.map((item) => item.id));
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, held]) => [role, ids(held).includes("apps")]));
  assert.deepEqual(offered, { super_admin: true, access_admin: true, support: true, auditor: true, api_platform_admin: false, finance: false });
  for (const [role, held] of Object.entries(ROLES)) {
    assert.equal(access.visibleTabs(held).includes("apps"), held.has("platform.apps.view"), role);
    assert.equal(nav.resolveAdminPage("/admin", "apps", held) === "apps", held.has("platform.apps.view"), role);
  }
  // a separate page from the run records, in the applications group
  const group = nav.adminGroups(ROLES.auditor).find((g) => g.items.some((item) => item.id === "apps"));
  assert.equal(group.label, "应用与业务");
  assert.notEqual(group, nav.adminGroups(ROLES.auditor).find((g) => g.items.some((item) => item.id === "app-runs")));
  for (const locale of ["en", "zh-Hant"]) assert.ok(!HAN.test(tx("en", "应用目录")) && tx(locale, "应用目录"), locale);
  // the permission and the manage permission have their own sentences for the platform-access dialog
  for (const p of ["platform.apps.view", "platform.apps.manage"]) {
    const src = readFileSync(new URL("../src/governance/PlatformAccess.tsx", import.meta.url), "utf8");
    const sentence = new RegExp(`"${esc(p)}": "([^"]+)"`).exec(src)?.[1];
    assert.ok(sentence && ui.hasMessage(sentence) && !HAN.test(tx("en", sentence)), `${p}: ${sentence}`);
  }
});

test("each application shows its status, policy and counts; without platform.apps.manage no change is offered", () => {
  for (const locale of LOCALES) {
    const html = appsTab(locale, VIEW);
    const on = text(row(html, APP.id)), off = text(row(html, OFF.id));
    assert.ok(on.includes(tx(locale, "已启用")) && on.includes(tx(locale, "成员均可使用")), `${locale}: ${on}`);
    assert.ok(on.includes(tx(locale, dialog.POLICY_MEANING.members)), `${locale}: the policy explained`);
    for (const n of [3, 12, 2, 1, 4]) assert.ok(on.includes(` ${n} `), `${locale}: count ${n}\n${on}`);
    assert.ok(off.includes(tx(locale, "全平台已停用")) && off.includes(tx(locale, "仅限明确授权")), `${locale}: ${off}`);
    assert.ok(off.includes(tx(locale, "不统计")), `${locale}: running jobs not counted for this application`);
    for (const key of Object.keys(dialog.CHANGE_LABELS)) assert.ok(!html.includes(`>${label(locale, key)}</button>`), `${locale}: ${key} offered without the permission`);
    assert.ok(row(html, APP.id).includes(`>${tx(locale, "按工作空间")}</button>`), `${locale}: the per-workspace list is read with the view`);
    assert.ok(html.includes(tx(locale, "你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")), `${locale}: the hint`);
  }
});

test("platform-scope changes need platform.config.manage too: an account and access administrator gets only the per-workspace switches", () => {
  // the coordinator's split (admin_apps): super_admin alone holds both; access_admin the catalogue's manage alone
  assert.ok(ROLES.access_admin.has("platform.apps.manage") && !ROLES.access_admin.has("platform.config.manage"));
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, held]) => [role, [access.mayChangeAppPlatform(held), access.mayChangeAppWorkspace(held)]]));
  assert.deepEqual(offered, { super_admin: [true, true], access_admin: [false, true], support: [false, false], auditor: [false, false],
    api_platform_admin: [false, false], finance: [false, false] });
  for (const locale of LOCALES) {
    for (const held of [ROLES.access_admin, WORKSPACE_MANAGE]) {
      const html = appsTab(locale, held);
      for (const key of Object.keys(dialog.CHANGE_LABELS)) assert.ok(!html.includes(`>${label(locale, key)}</button>`), `${locale}: ${key} offered without platform.config.manage`);
      assert.ok(html.includes(tx(locale, PLATFORM_HINT)), `${locale}: the hint says who may`);
      assert.ok(!html.includes(tx(locale, "你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")), `${locale}: not the view-only hint`);
      assert.ok(row(html, APP.id).includes(`>${tx(locale, "按工作空间")}</button>`), `${locale}: the per-workspace list`);
      assert.ok(!html.includes(tx(locale, "当前部署没有发布这个应用，不能在这里启用。")), `${locale}: no note on a switch not offered`);
      const ws = drawer(locale, held);
      assert.ok(row(ws, ">Fixture Workspace On<").includes(`>${label(locale, "workspace_disable")}</button>`), `${locale}: per-workspace disable`);
      assert.ok(row(ws, ">Fixture Workspace Off<").includes(`>${label(locale, "workspace_enable")}</button>`), `${locale}: per-workspace enable`);
    }
    const all = appsTab(locale, ROLES.super_admin);
    assert.ok(row(all, APP.id).includes(`>${label(locale, "disable")}</button>`) && !all.includes(tx(locale, PLATFORM_HINT)), `${locale}: the super administrator`);
  }
});

test("with platform.apps.manage and platform.config.manage each row offers switching it and its policy, the taking-away ones as danger", () => {
  for (const locale of LOCALES) {
    const html = appsTab(locale, MANAGE);
    const on = row(html, APP.id), off = row(html, OFF.id);
    for (const [r, key, danger] of [[on, "disable", true], [on, "policy_explicit", true], [off, "enable", false], [off, "policy_members", false]]) {
      assert.match(r, new RegExp(`<button type="button" class="btn sm${danger ? " danger" : ""}"[^>]*aria-label="${esc(tx(locale, "{action}：{p0}", { action: label(locale, key), p0: r === on ? APP.name : OFF.name }))}"[^>]*>${esc(label(locale, key))}</button>`), `${locale} ${key}\n${r}`);
    }
    assert.ok(!on.includes(`>${label(locale, "enable")}</button>`) && !off.includes(`>${label(locale, "disable")}</button>`), `${locale}: only the change away from the current state`);
    // an application every live account uses: only the platform-wide switch, no policy, no per-workspace list
    const org = row(html, ORG.id);
    assert.ok(org.includes(`>${label(locale, "disable")}</button>`), `${locale}: ${org}`);
    for (const key of ["policy_explicit", "policy_members"]) assert.ok(!org.includes(`>${label(locale, key)}</button>`), `${locale}: ${key} for ${ORG.id}`);
    assert.ok(!org.includes(`>${tx(locale, "按工作空间")}</button>`), `${locale}: no per-workspace list for ${ORG.id}`);
    assert.ok(text(org).includes(tx(locale, "这个应用不按工作空间开通或授权：每个有效账户都可以使用，只能在全平台启用或停用。")), `${locale}: ${org}`);
    // the server offers no enable for an application the deployment holds back, and the row says why
    const held = row(html, HELD.id);
    assert.ok(!held.includes(`>${label(locale, "enable")}</button>`) && held.includes(`>${label(locale, "policy_members")}</button>`), `${locale}: ${held}`);
    assert.ok(held.includes(tx(locale, "当前部署没有发布这个应用，不能在这里启用。")), `${locale}: ${held}`);
    assert.ok(!off.includes(tx(locale, "当前部署没有发布这个应用，不能在这里启用。")), `${locale}: only where enable is withheld`);
    assert.ok(!html.includes(tx(locale, "你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")), `${locale}: no hint for a role that may act`);
  }
});

test("the per-workspace list offers enable or disable per workspace only with the permission, and says why not", () => {
  for (const locale of LOCALES) {
    const html = drawer(locale, MANAGE);
    assert.ok(html.includes(tx(locale, "「{app}」的工作空间", { app: APP.name })), `${locale}: the title names the application`);
    assert.match(html, new RegExp(`<input[^>]*type="search"`), `${locale}: the search`);
    assert.ok(row(html, ">Fixture Workspace On<").includes(`>${label(locale, "workspace_disable")}</button>`), `${locale}: disable where enabled`);
    assert.ok(text(row(html, ">Fixture Workspace On<")).includes(tx(locale, "其中可使用 {count}", { count: 4 })), `${locale}: members with access`);
    assert.ok(row(html, ">Fixture Workspace Off<").includes(`>${label(locale, "workspace_enable")}</button>`), `${locale}: enable where not`);
    const frozen = row(html, ">Fixture Workspace Frozen<"), legacy = row(html, ">Fixture Workspace Legacy<");
    assert.ok(!frozen.includes("</button>") && frozen.includes(tx(locale, "工作空间不可用时不能为它启用应用。")), `${locale}: ${frozen}`);
    assert.ok(!legacy.includes("</button>") && legacy.includes(tx(locale, "待认领的历史数据由认领流程管理，不能在这里启用或停用应用。")), `${locale}: ${legacy}`);
    const viewOnly = drawer(locale, VIEW);
    for (const key of ["workspace_disable", "workspace_enable"]) assert.ok(!viewOnly.includes(`>${label(locale, key)}</button>`), `${locale}: ${key} without the permission`);
    assert.ok(viewOnly.includes(tx(locale, "你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")), `${locale}: the hint`);
  }
});

test("the preview shows who loses or gains access, the workspaces, the running work and every note, in every language", () => {
  for (const locale of LOCALES) {
    for (const change of CHANGES) {
      const rows = text(impact(locale, change));
      assert.ok(rows.includes("Fixture Docs") && rows.includes("fx_docs"), `${locale} ${change}: the target`);
      for (const [key, name] of dialog.impactRows(change, IMPACT)) {
        assert.ok(rows.includes(tx(locale, name)), `${locale} ${change} ${key}: the label\n${rows}`);
        const meaning = tx(locale, dialog.impactMeaning(change, key, IMPACT));
        const count = inLocale(locale, () => dialog.countText(key, IMPACT));
        assert.ok(rows.includes(`${count} · ${meaning}`), `${locale} ${change} ${key}: the count and its meaning\n${rows}`);
      }
      // both counts of people are shown here (the fixture counts some of each)
      assert.ok(rows.includes(tx(locale, "将失去访问的人")) && rows.includes(tx(locale, "将获得访问的人")), `${locale} ${change}`);
      for (const code of NOTES) {
        const entry = dialog.NOTE_TEXT[code];
        const note = change === "policy_explicit" && entry.tighten ? entry.tighten : change === "policy_members" && entry.loosen ? entry.loosen
          : entry[dialog.takesAway(change) ? "away" : "back"];
        assert.ok(rows.includes(tx(locale, note)), `${locale} ${change} ${code}\n${rows}`);
      }
    }
    // the transition in words
    assert.ok(text(impact(locale, "disable")).includes(tx(locale, "状态：{before} → {after}", { before: tx(locale, "已启用"), after: tx(locale, "全平台已停用") })), locale);
    assert.ok(text(impact(locale, "policy_explicit")).includes(tx(locale, "访问策略：{before} → {after}", { before: tx(locale, "成员均可使用"), after: tx(locale, "仅限明确授权") })), locale);
    const scoped = text(impact(locale, "disable", wsPlan("disable")));
    assert.ok(scoped.includes(tx(locale, "在这个空间：{before} → {after}", { before: tx(locale, "已开通"), after: tx(locale, "未开通") })), `${locale}: ${scoped}`);
    assert.ok(scoped.includes(tx(locale, "目标工作空间")) && scoped.includes("Fixture Workspace On"), `${locale}: the workspace`);
    // a count the server could not take, and a note this page does not know
    const unknown = text(impact(locale, "disable", plan("disable", { impact: { ...IMPACT, running_jobs: null, sessions_in_app: null }, notes: ["fixture_unknown_note"] })));
    for (const key of ["running_jobs", "sessions_in_app"]) assert.ok(unknown.includes(`— · ${tx(locale, dialog.NOT_COUNTED[key])}`), `${locale} ${key}: ${unknown}`);
    assert.ok(unknown.includes(tx(locale, "另有一项说明（{code}），本页面无法解释，请刷新页面后重新预览。", { code: "fixture_unknown_note" })), `${locale}: ${unknown}`);
  }
  // people are shown with their memberships; a count means the same whichever the direction, and
  // only people and memberships both at zero say nobody
  for (const locale of LOCALES) {
    const tighten = text(impact(locale, "policy_explicit"));
    assert.ok(tighten.includes(tx(locale, "{people} 人 · {memberships} 个成员关系", { people: "7", memberships: "11" }))
      && tighten.includes(tx(locale, "{people} 人 · {memberships} 个成员关系", { people: "6", memberships: "8" })), `${locale}: ${tighten}`);
    assert.ok(!tighten.includes(tx(locale, "没有人因此获得访问。")) && !tighten.includes(tx(locale, "没有人因此失去访问。")), `${locale}: ${tighten}`);
    const zero = { ...IMPACT, people_losing_access: 0, memberships_losing_access: 0, people_gaining_access: 0, memberships_gaining_access: 0 };
    const nobody = text(impact(locale, "policy_explicit", plan("policy_explicit", { impact: zero })));
    assert.ok(nobody.includes(`${tx(locale, "{people} 人 · {memberships} 个成员关系", { people: "0", memberships: "0" })} · ${tx(locale, "没有人因此失去访问。")}`)
      && !nobody.includes(tx(locale, "将获得访问的人")), `${locale}: ${nobody}`);
    // memberships counted with no person counted is still somebody
    const odd = text(impact(locale, "disable", plan("disable", { impact: { ...zero, memberships_losing_access: 2 } })));
    assert.ok(!odd.includes(tx(locale, "没有人因此失去访问。")), `${locale}: ${odd}`);
    // a server without the memberships counts: the people alone
    const older = { ...IMPACT }; delete older.memberships_losing_access; delete older.memberships_gaining_access;
    assert.ok(text(impact(locale, "disable", plan("disable", { impact: older }))).includes(`7 · ${tx(locale, dialog.impactMeaning("disable", "people_losing_access", older))}`), locale);
  }
  // what does not come back by itself is said before an enable or a loosening, and in the notes
  for (const locale of LOCALES) {
    for (const key of ["enable", "policy_members", "workspace_enable", "disable", "workspace_disable", "policy_explicit"]) {
      assert.match(dialog.CHANGE_WARNINGS[key], /Webhook 订阅/, key);
      assert.match(dialog.CHANGE_WARNINGS[key], /重新授权/, key);
    }
    const loosen = text(impact(locale, "policy_members"));
    assert.ok(loosen.includes(tx(locale, dialog.NOTE_TEXT.integrations_need_reauthorization.back)), `${locale}: ${loosen}`);
    // a policy change says API keys and service accounts follow their approving owner (re-checked on
    // every use), never that they are unaffected, and never "重新启用后"
    for (const change of ["policy_explicit", "policy_members"]) {
      const said = text(impact(locale, change));
      assert.ok(!said.includes(tx(locale, dialog.NOTE_TEXT.links_stop.away)) && !said.includes(tx(locale, dialog.NOTE_TEXT.links_stop.back)), `${locale} ${change}`);
    }
    assert.match(dialog.NOTE_TEXT.links_stop.tighten, /API 密钥或服务账户/);
    assert.match(dialog.NOTE_TEXT.links_stop.loosen, /API 密钥[或和]服务账户/);
    assert.doesNotMatch(dialog.NOTE_TEXT.links_stop.tighten, /重新启用后/);
    for (const wording of [dialog.NOTE_TEXT.links_stop.tighten, dialog.NOTE_TEXT.links_stop.loosen, dialog.NOTE_TEXT.jobs_continue_or_stop.tighten]) {
      assert.match(wording, /批准/);
      assert.doesNotMatch(wording, /不受访问策略变化影响|提交的任务不受影响/);
    }
    // credentials whose approver loses access: said for the change that takes access away, and how they recover
    const tighten = text(impact(locale, "policy_explicit"));
    assert.ok(tighten.includes(tx(locale, dialog.NOTE_TEXT.approved_credentials_stop.away)), `${locale}: ${tighten}`);
    assert.ok(tx(locale, dialog.NOTE_TEXT.approved_credentials_stop.away) !== dialog.NOTE_TEXT.approved_credentials_stop.away || locale === "zh-Hans", locale);
  }
  // API credentials: counted for Synwevo Docs, shown where something can stop, absent elsewhere
  for (const locale of LOCALES) {
    const away = text(impact(locale, "policy_explicit"));
    assert.ok(away.includes(tx(locale, "将停止的 API 凭据")), `${locale}: ${away}`);
    assert.ok(away.includes(`2 · ${tx(locale, dialog.impactMeaning("policy_explicit", "credentials_stopping", IMPACT))}`), `${locale}: ${away}`);
    const none = text(impact(locale, "disable", plan("disable", { impact: { ...IMPACT, credentials_stopping: null } })));
    assert.ok(!none.includes(tx(locale, "将停止的 API 凭据")), `${locale}: ${none}`);
    const older = { ...IMPACT }; delete older.credentials_stopping;
    assert.ok(!text(impact(locale, "disable", plan("disable", { impact: older }))).includes(tx(locale, "将停止的 API 凭据")), locale);
    const back = text(impact(locale, "enable", plan("enable", { impact: { ...IMPACT, credentials_stopping: 0 } })));
    assert.ok(!back.includes(tx(locale, "将停止的 API 凭据")), `${locale}: ${back}`);
  }
  // an application not enabled per workspace: its workspace and session counts are null, and say why
  for (const locale of LOCALES) {
    const org = text(impact(locale, "disable", plan("disable", { target: { ...ORG }, impact: { ...IMPACT, workspaces_affected: null, running_jobs: null, sessions_in_app: null }, notes: ["data_kept", "links_stop"] })));
    for (const key of ["workspaces_affected", "running_jobs", "sessions_in_app"]) assert.ok(org.includes(`— · ${tx(locale, dialog.NOT_COUNTED[key])}`), `${locale} ${key}: ${org}`);
  }
  // tightening stops only what a person without a grant submitted
  for (const locale of LOCALES) {
    assert.ok(text(impact(locale, "policy_explicit")).includes(tx(locale, dialog.NOTE_TEXT.jobs_continue_or_stop.tighten)), locale);
    assert.ok(!text(impact(locale, "policy_explicit")).includes(tx(locale, dialog.NOTE_TEXT.jobs_continue_or_stop.away)), locale);
    assert.ok(text(impact(locale, "disable")).includes(tx(locale, dialog.NOTE_TEXT.jobs_continue_or_stop.away)), locale);
  }
  // the rows a change shows without counts on the other side
  const none = { ...IMPACT, people_losing_access: 0, memberships_losing_access: 0, people_gaining_access: 0, memberships_gaining_access: 0, credentials_stopping: 0 };
  assert.deepEqual(dialog.impactRows("disable", none).map(([key]) => key), ["people_losing_access", "workspaces_affected", "running_jobs", "sessions_in_app", "credentials_stopping"]);
  assert.deepEqual(dialog.impactRows("policy_members", none).map(([key]) => key), ["people_gaining_access", "workspaces_affected", "running_jobs", "sessions_in_app"]);
});

test("the confirmation stays disabled until the preview is loaded, with the warning, and danger for what takes access away", () => {
  for (const locale of LOCALES) {
    for (const [change, workspace, key] of [["disable", undefined, "disable"], ["enable", undefined, "enable"], ["policy_explicit", undefined, "policy_explicit"],
      ["policy_members", undefined, "policy_members"], ["disable", WS, "workspace_disable"], ["enable", WS, "workspace_enable"]]) {
      const html = fullDialog(locale, change, workspace);
      assert.ok(html.includes('role="alertdialog"'), `${locale} ${key}: a confirmation dialog`);
      assert.ok(html.includes(tx(locale, dialog.CHANGE_WARNINGS[key])), `${locale} ${key}: the warning\n${html}`);
      assert.ok(html.includes(tx(locale, dialog.APP_PLATFORM_NOTE)), `${locale} ${key}: no membership or content access for the administrator`);
      assert.ok(html.includes(tx(locale, "正在核对应用与操作影响…")), `${locale} ${key}: the preview is being loaded`);
      const danger = dialog.takesAway(change);
      assert.match(html, new RegExp(`<button type="submit"[^>]*class="btn ${danger ? "danger" : "primary"}"[^>]*disabled=""[^>]*>${esc(label(locale, key))}</button>`),
        `${locale} ${key}: the confirm label, disabled until the preview is loaded and checked\n${html}`);
      if (workspace) assert.ok(text(html).includes(`${APP.name} · ${WS.name}`), `${locale} ${key}: the application and the workspace`);
    }
  }
});

test("the refusals of the preview and the change read as sentences, worded for an application in the dialog", () => {
  const codes = ["no_app", "no_workspace", "legacy_workspace", "workspace_unavailable", "no_change", "bad_change", "portal_required", "app_not_released",
    "admin_preview_invalid", "admin_preview_expired", "admin_preview_stale", "admin_confirmation_required", "idempotency_conflict", "missing_reason"];
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const code of codes) {
    for (const locale of LOCALES) {
      for (const word of [labels.errText, dialog.appErrText]) {
        const worded = inLocale(locale, () => word(new client.ApiError(409, code, "")));
        assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
        if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
      }
    }
  }
  // the dialog names the application where the page-wide sentence cannot; a catalogued server sentence wins
  inLocale("en", () => {
    assert.equal(dialog.appErrText(new client.ApiError(409, "portal_required", "")), tx("en", dialog.APP_REFUSALS.portal_required));
    assert.equal(dialog.appErrText(new client.ApiError(409, "admin_preview_stale", "")), tx("en", dialog.APP_REFUSALS.admin_preview_stale));
    assert.equal(dialog.appErrText(new client.ApiError(404, "no_workspace", "找不到该工作空间")), labels.errText(new client.ApiError(404, "no_workspace", "找不到该工作空间")));
  });
});

test("the English render has no untranslated text", () => {
  const en = [appsTab("en", MANAGE), appsTab("en", WORKSPACE_MANAGE), appsTab("en", VIEW), drawer("en", MANAGE), drawer("en", VIEW),
    ...CHANGES.map((change) => fullDialog("en", change)), fullDialog("en", "disable", WS), fullDialog("en", "enable", WS),
    ...CHANGES.map((change) => impact("en", change)), impact("en", "disable", wsPlan("disable")), impact("en", "enable", wsPlan("enable"))]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split("\n").filter((l) => HAN.test(l)).join("\n").slice(0, 2000)}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("every sentence of the tab and the dialog is catalogued in English and Traditional Chinese, and this catalogue's wording is the one in effect", () => {
  assert.deepEqual(ui.validateMessages(messages.appsMessages), []);
  for (const [source, entry] of Object.entries(messages.appsMessages)) {
    for (const locale of ["en", "zh-Hant"]) assert.equal(tx(locale, source), entry[locale], `${locale}: ${source} is worded by another catalogue`);
  }
  const sentences = new Set();
  for (const file of ["AppsTab.tsx", "AppChangeDialog.tsx"]) {
    const src = readFileSync(new URL(`../src/governance/${file}`, import.meta.url), "utf8")
      .split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
    for (const m of src.matchAll(/"([^"\n]*[㐀-鿿][^"\n]*)"/g)) sentences.add(m[1]);
    for (const m of src.matchAll(/\btr\("([^"]+)"/g)) sentences.add(m[1]);
  }
  assert.ok(sentences.size > 80, `expected the tab's and the dialog's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(tx("en", s, { count: 2 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
