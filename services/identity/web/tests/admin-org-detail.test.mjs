/** A34: one organization's governance metadata for a platform administrator holding
 *  `platform.orgs.view`, member or not — the organizations tab's 详情 action, the panel it opens
 *  (governance/OrganizationDetail.tsx) and the address that keeps it open across a reload, the
 *  panel's sections (overview, owners and administrators, workspaces, members, custom roles,
 *  unfinished imports and copies), the per-member permission explainer, its loading, refused,
 *  missing and failed states, and the wording of its refusals.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with the
 *  administration data loader replaced by fixture data shaped as the A34 contract has the server
 *  answer: no API, no user data. Run from the package: `npm test`. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const require = createRequire(WEB + "package.json");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { MemoryRouter } = require("react-router-dom");

// Explicit browser-environment fixture, as in admin-apps.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin?tab=organizations"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for the tab, the ownership panel and the detail's member
 *  list: answers each path from `globalThis.__fixtures` and records the paths asked for. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  (globalThis.__requested ||= []).push(path);
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage(path) {
  (globalThis.__requested ||= []).push(path);
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  const items = f.items ?? [];
  return { data: f.loading || f.err ? null : { items, total: f.total ?? items.length, offset: 0, limit: 50 }, err: f.err ?? null,
    loading: !!f.loading, reload() {}, offset: 0, setOffset() {} };
}`;
const DATA_IMPORTERS = ["/src/governance/GovernanceTabs.tsx", "/src/governance/OwnershipRecovery.tsx", "/src/governance/OrganizationDetail.tsx"];

let server, ui, client, detail, tabs, messages, labels, nav, runs;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "org-detail-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && DATA_IMPORTERS.some((p) => importer?.endsWith(p))) return "\0org-detail-fixture-data"; },
      load(id) { if (id === "\0org-detail-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  messages = await server.ssrLoadModule("/src/governance/org-detail.messages.ts");
  detail = await server.ssrLoadModule("/src/governance/OrganizationDetail.tsx");
  tabs = await server.ssrLoadModule("/src/governance/GovernanceTabs.tsx");
  nav = await server.ssrLoadModule("/src/admin/navigation.ts");
  runs = await server.ssrLoadModule("/src/governance/runs.ts");
  // the page's whole catalogue, as the browser has it on the administration page
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// ------------------------------------------------------------------------------------ fixtures
// FIXTURE: GET /api/admin/organizations/{oid} and its member page as the A34 contract has the server
// answer them — governance metadata only. Synthetic ids, names and addresses.
const T = 1_790_000_000;
const FAR = 4_102_444_800; // 2100-01-01: a recovery window still open whenever this runs
const OID = "org_fx_alpha";
const person = (id, name, over = {}) => ({ user_id: id, display_name: name, email: `${id}@fixture.example.test`,
  account_status: "active", joined_at: T, usable: true, ...over });
const WS = { id: "ws_fx_main", name: "Fixture Main Workspace", kind: "org", status: "active", created_at: T, members: 1500 };
const CATALOGUE = [
  { id: "org.audit.read", group: "governance", owner_only: false, label: "Fixture label audit", description: "Fixture description audit" },
  { id: "org.members.view", group: "members", owner_only: false, label: "Fixture label members", description: "Fixture description members" },
];
const DETAIL = {
  organization: { id: OID, name: "Fixture Alpha Organization", status: "active", created_at: T, deleted_at: null, recover_until: null,
    previous_status: null, version: 4, has_avatar: true },
  settings: { invitation_policy: "admins", transfer_policy: "organization" },
  counts: { members: 1234, owners: 2, admins: 1, usable_owners: 1, workspaces: 2, workspaces_frozen: 1, custom_roles: 2, pending_imports: 21, pending_copies: 1 },
  owners: [person("usr_fx_owner", "Fixture Owner"), person("usr_fx_former", "Fixture Former Owner", { account_status: "suspended", usable: false })],
  admins: [person("usr_fx_admin", "Fixture Admin")],
  owners_total: 2, admins_total: 1,
  workspaces: { items: [WS, { ...WS, id: "ws_fx_frozen", name: "Fixture Frozen Workspace", kind: "team", status: "frozen", members: 2 }], total: 2 },
  custom_roles: [{ id: "role_fx_auditors", name: "Fixture Auditors", permissions: ["org.audit.read", "org.members.view"], assigned: 3 },
    { id: "role_fx_empty", name: "Fixture Empty Role", permissions: [], assigned: 0 }],
  pending: {
    imports: [{ id: "imp_fx_one", state: "planned", rows: 1500, created_at: T, created_by: { user_id: "usr_fx_admin", display_name: "Fixture Admin" } },
      { id: "imp_fx_two", state: "running", rows: 2, created_at: T, created_by: null },
      // rows are counted once a preview is planned: an extracted import reports 0
      { id: "imp_fx_three", state: "extracted", rows: 0, created_at: T, created_by: null }],
    // `expires_at` is the current step's deadline: the approval's while approved, none while running
    copies: [{ id: "cpy_fx_one", status: "approved", source_workspace_id: "ws_fx_personal", target_workspace_id: "ws_fx_main", created_at: T,
      expires_at: T + 86400, requested_by: { user_id: "usr_fx_requester", display_name: null } },
      { id: "cpy_fx_two", status: "running", source_workspace_id: "ws_fx_personal", target_workspace_id: "ws_fx_frozen", created_at: T,
        expires_at: null, requested_by: { user_id: "usr_fx_requester", display_name: "Fixture Requester" } }],
  },
  viewer: { is_member: false, role: null },
};
const MEMBERS = [
  { ...person("usr_fx_owner", "Fixture Owner"), role: "owner", custom_roles: [] },
  { ...person("usr_fx_member", null), role: "member", custom_roles: [{ id: "role_fx_auditors", name: "Fixture Auditors" }] },
  { ...person("usr_fx_former", "Fixture Former Owner", { account_status: "suspended", usable: false }), role: "owner", custom_roles: [] },
];
/** The same people as a caller without platform.users.view receives them: no address at all. */
const noEmail = (p) => ({ ...p, email: null });
const HIDDEN = { ...DETAIL, owners: DETAIL.owners.map(noEmail), admins: DETAIL.admins.map(noEmail) };
const EFFECTIVE = { base_role: "admin", roles: [{ id: "role_fx_auditors", name: "Fixture Auditors", permissions: ["org.audit.read"] }],
  permissions: ["org.audit.read", "org.members.view"], sources: { "org.audit.read": ["base:admin", "role:role_fx_auditors"], "org.members.view": ["base:admin"] } };
const LIST = [
  { id: OID, name: "Fixture Alpha Organization", status: "active", role: null, members: 1234, workspaces: 2, recover_until: null },
  { id: "org_fx_mine", name: "Fixture Member Organization", status: "active", role: "admin", members: 4, workspaces: 1, recover_until: null },
];

const VIEW = new Set(["platform.orgs.view"]);
const WITH_USERS = new Set(["platform.orgs.view", "platform.users.view"]);
const MANAGE = new Set(["platform.orgs.view", "platform.orgs.manage", "platform.users.view", "platform.workspaces.view"]);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /Fixture [A-Za-z ]+|(?:org|usr|ws|role|imp|cpy)_fx_[a-z_]+|[a-z_]+@fixture\.example\.test|org\.[a-z]+\.[a-z]+/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const decode = (html) => html.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
const render = (locale, element) => inLocale(locale, () => decode(renderToStaticMarkup(element)));
// The text a reader sees: tags break words and runs of HTML white space (space, tab, LF, FF, CR) fold into one
// space, as a browser lays them out. A thin or no-break space a formatter emits is part of the text and stays:
// ICU 78 (Node 22) puts U+2009 between the date and the time in zh-Hant, ICU 76 (Node 23) a plain space, and the
// expected dates come from the same ui.fmtTime, so they match under either.
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/[ \t\n\f\r]+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const row = (html, id) => html.split("<tr").find((part) => part.includes(id)).split("</tr>")[0];
const section = (html, heading) => { const i = html.indexOf(`>${heading}</h3>`); assert.ok(i >= 0, heading); const j = html.indexOf("<h3", i); return html.slice(i, j < 0 ? undefined : j); };
function setFixtures(members = MEMBERS) {
  globalThis.__requested = [];
  globalThis.__fixtures = {
    "/api/admin/organizations": { items: LIST },
    [`/api/admin/organizations/${OID}/members`]: { items: members, total: 120 },
    "/api/admin/ownership/orphans": { data: { as_of: T, limit: 50, workspaces: { total: 0, items: [] }, organizations: { total: 0, items: [] } } },
  };
}
function body(locale, held, d = DETAIL, { members = MEMBERS, onAct = () => {} } = {}) {
  setFixtures(members);
  return render(locale, React.createElement(detail.OrganizationDetailBody, { detail: d, held, onAct, onExplain() {} }));
}
const view = (locale, state, held = VIEW) => render(locale, React.createElement(detail.OrganizationDetailView,
  { state, held, onRetry() {}, onAct() {}, onExplain() {} }));
function tab(locale, held, search = "?tab=organizations") {
  setFixtures();
  return render(locale, React.createElement(MemoryRouter, { initialEntries: [`/admin${search}`] },
    React.createElement(tabs.OrganizationsTab, { onDenied() {}, held })));
}
const effective = (locale, data = EFFECTIVE, catalogue = CATALOGUE) => render(locale,
  React.createElement(detail.EffectivePermissionsBody, { data, labels: detail.permissionLabels(catalogue) }));

// ------------------------------------------------------------------------------------ tests
test("every organization row offers 详情 with platform.orgs.view; the settings link stays the members'", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, VIEW);
    for (const org of LIST) {
      const r = row(html, org.id);
      assert.match(r, new RegExp(`<button type="button" class="btn sm" aria-label="${esc(tx(locale, "查看组织详情：{name}", { name: org.name }))}">${esc(tx(locale, "详情"))}</button>`), `${locale}: ${r}`);
    }
    assert.ok(!row(html, OID).includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: no settings link without a membership`);
    assert.ok(row(html, "org_fx_mine").includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: the member keeps the settings link`);
    // narrow screens: the list is the cards presentation, every cell labelled
    assert.match(html, /<table class="gov admin-cards-table"/);
    assert.equal((row(html, OID).match(/<td(?![^>]*data-label=)/g) || []).length, 0, `${locale}: an unlabelled cell`);
    assert.ok(!html.includes('role="dialog"'), `${locale}: no panel without an organization in the address`);
  }
});

test("the open organization is kept in the address: a reload shows the panel, and opening it is not a move to another page", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, VIEW, `?tab=organizations&org=${OID}`);
    assert.match(html, /<div class="modal wide" role="dialog" aria-modal="true" aria-labelledby="([^"]+)"/, `${locale}: the panel is a labelled dialog`);
    const labelled = /aria-labelledby="([^"]+)"/.exec(html.slice(html.indexOf('class="modal wide"')))[1];
    assert.match(html, new RegExp(`<h2 class="grow" id="${esc(labelled)}"><span class="org-detail-name">${esc(tx(locale, "组织详情"))}</span></h2>`), `${locale}: its heading`);
    assert.ok(html.includes(`role="status">${tx(locale, "正在读取组织详情…")}</p>`), `${locale}: the loading state`);
    assert.ok(html.includes(tx(locale, detail.ORG_DETAIL_NOTE)), `${locale}: what platform administration reads`);
  }
  assert.equal(detail.ORG_VIEW_KEY, "org");
  // the key joins the shell's own (tab, lang, from) and leaves them when it goes
  assert.equal(runs.withView("?tab=organizations&lang=en", { org: OID }), `?tab=organizations&lang=en&org=${OID}`);
  assert.equal(runs.withView(`?tab=organizations&lang=en&org=${OID}`, { org: "" }), "?tab=organizations&lang=en");
  // the page stays the organizations page with the panel open, so focus is not taken to the heading
  const before = nav.adminPlace("/admin", "?tab=organizations", "organizations");
  const open = nav.adminPlace("/admin", `?tab=organizations&org=${OID}`, "organizations");
  assert.equal(nav.movedPage(before, open), false);
  // after a close with nothing focused (opened by its link), focus goes to the page heading (runs.ts)
  assert.equal(runs.closedWithoutFocus(OID, "", false), true);
  assert.equal(runs.closedWithoutFocus(OID, "", true), false);
});

test("the overview shows status, settings and counts with the shared number format, and the viewer's standing", () => {
  for (const locale of LOCALES) {
    const html = body(locale, VIEW);
    const overview = text(section(html, tx(locale, "概况")));
    assert.ok(overview.includes(OID) && overview.includes(tx(locale, "正常")), `${locale}: ${overview}`);
    assert.ok(overview.includes(tx(locale, "所有者和管理员")) && overview.includes(tx(locale, "组织所有者和管理员可发送工作空间邀请。")), `${locale}: invitation policy`);
    assert.ok(overview.includes(tx(locale, "仅限组织内的工作空间")) && overview.includes(tx(locale, "组织内容只能在组织的工作空间之间转移。")), `${locale}: transfer policy`);
    assert.ok(overview.includes(tx(locale, "已设置")), `${locale}: the avatar`);
    const formatted = inLocale(locale, () => ui.formatNumber(1234));
    assert.ok(overview.includes(`${tx(locale, "成员人数")} ${formatted}`), `${locale}: members formatted (${formatted})\n${overview}`);
    for (const [name, value] of [["所有者人数", 2], ["管理员人数", 1], ["工作空间数", 2], ["自定义角色", 2], ["未完成的成员导入", 21], ["未完成的复制申请", 1]]) {
      assert.ok(overview.includes(`${tx(locale, name)} ${value}`), `${locale}: ${name}\n${overview}`);
    }
    assert.ok(overview.includes(tx(locale, "其中可用 {count}", { count: 1 })) && overview.includes(tx(locale, "其中已冻结 {count}", { count: 1 })), `${locale}: ${overview}`);
    // not a member: no settings link, and the panel says it grants nothing
    assert.ok(!html.includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: no settings link for a non-member`);
    assert.ok(text(html).includes(tx(locale, "你不是这个组织的成员：这里只显示治理元数据，不授予成员身份或内容访问；组织设置页对你不可用。")), locale);
    const member = body(locale, VIEW, { ...DETAIL, viewer: { is_member: true, role: "admin" } });
    assert.ok(member.includes(`<a class="btn sm" href="/organizations/${OID}">${tx(locale, "组织设置")}</a>`), `${locale}: the member's settings link`);
    assert.ok(text(member).includes(tx(locale, "你在这个组织的角色：{role}。组织设置页按这个成员角色开放；这里是平台治理视图。", { role: tx(locale, "管理员") })), locale);
    // counts the deployment does not keep are said to be uncounted, not zero
    const none = text(body(locale, VIEW, { ...DETAIL, counts: { ...DETAIL.counts, pending_imports: null, pending_copies: null } }));
    assert.ok(none.includes(`${tx(locale, "未完成的成员导入")} — ${tx(locale, "不统计")}`), `${locale}: ${none}`);
    assert.ok(none.includes(tx(locale, "这个部署没有成员导入记录，不统计。")) && none.includes(tx(locale, "这个部署没有项目复制记录，不统计。")), locale);
  }
});

test("a deleted organization shows when it was deleted, its status before, and its recovery window; recovery follows the window", () => {
  const deleted = (until) => ({ ...DETAIL, organization: { ...DETAIL.organization, status: "deleted", deleted_at: T, recover_until: until, previous_status: "frozen" } });
  for (const locale of LOCALES) {
    const open = body(locale, MANAGE, deleted(FAR));
    const facts = text(section(open, tx(locale, "概况")));
    assert.ok(facts.includes(tx(locale, "已删除")), locale);
    assert.ok(facts.includes(tx(locale, "删除时间：{time}", { time: inLocale(locale, () => ui.fmtTime(T)) })), `${locale}: ${facts}`);
    assert.ok(facts.includes(tx(locale, "删除前状态：{status}", { status: tx(locale, "已冻结") })), `${locale}: ${facts}`);
    assert.ok(facts.includes(tx(locale, "恢复期限：{time}", { time: inLocale(locale, () => ui.fmtTime(FAR)) })), `${locale}: ${facts}`);
    assert.match(open, new RegExp(`<button type="button" class="btn sm">${esc(tx(locale, "恢复组织"))}</button>`), `${locale}: recovery offered inside the window`);
    const closed = body(locale, MANAGE, deleted(T));
    assert.match(closed, new RegExp(`<button type="button" class="btn sm" disabled="">${esc(tx(locale, "恢复组织"))}</button>`), `${locale}: recovery disabled after the window`);
    assert.ok(text(closed).includes(tx(locale, "恢复期限已过（{time}），不能再恢复。", { time: inLocale(locale, () => ui.fmtTime(T)) })), locale);
    // a deleted organization's missing owners are not raised as a problem to fix
    assert.ok(!text(body(locale, MANAGE, { ...deleted(FAR), counts: { ...DETAIL.counts, usable_owners: 0 } })).includes(tx(locale, "这个组织没有可用的所有者：所有者的账户已停用或已删除。")), locale);
  }
});

test("the lifecycle buttons in the panel follow platform.orgs.manage, as on the list", () => {
  for (const locale of LOCALES) {
    const manage = body(locale, MANAGE);
    for (const label of ["冻结组织", "删除组织"]) assert.ok(manage.includes(`<button type="button" class="btn sm">${tx(locale, label)}</button>`), `${locale}: ${label}`);
    assert.ok(!manage.includes(`>${tx(locale, "解冻组织")}</button>`), `${locale}: only the transitions from active`);
    const frozen = body(locale, MANAGE, { ...DETAIL, organization: { ...DETAIL.organization, status: "frozen" } });
    assert.ok(frozen.includes(`>${tx(locale, "解冻组织")}</button>`) && !frozen.includes(`>${tx(locale, "冻结组织")}</button>`), locale);
    const read = body(locale, VIEW);
    for (const label of ["冻结组织", "解冻组织", "删除组织", "恢复组织"]) assert.ok(!read.includes(`>${tx(locale, label)}</button>`), `${locale}: ${label} offered without the permission`);
    assert.ok(read.includes(tx(locale, "你的平台角色可以查看组织，但不能在管理后台执行组织的生命周期操作。")), `${locale}: the hint`);
    // no handler, no buttons (the body rendered outside the dialog)
    setFixtures();
    const bare = render(locale, React.createElement(detail.OrganizationDetailBody, { detail: DETAIL, held: MANAGE, onExplain() {} }));
    assert.ok(!bare.includes(`>${tx(locale, "冻结组织")}</button>`), locale);
  }
});

test("owners and administrators are named with their account state and whether they count; caps say so", () => {
  for (const locale of LOCALES) {
    const html = body(locale, WITH_USERS);
    const people = section(html, tx(locale, "所有者与管理员"));
    assert.ok(people.includes(tx(locale, "所有者（{count} 位）", { count: 2 })) && people.includes(tx(locale, "管理员（{count} 位）", { count: 1 })), locale);
    const former = text(row(people, "Fixture Former Owner"));
    assert.ok(former.includes(tx(locale, "已停用")) && former.includes(tx(locale, "账户不可用")), `${locale}: ${former}`);
    assert.ok(text(row(people, ">Fixture Owner<")).includes(tx(locale, "账户可用")), locale);
    assert.ok(people.includes("usr_fx_owner@fixture.example.test"), `${locale}: the address for a caller who may read accounts`);
    const capped = text(body(locale, WITH_USERS, { ...DETAIL, owners_total: 75 }));
    assert.ok(capped.includes(tx(locale, "共 {total} 位，这里按加入时间显示前 {shown} 位；在下方成员列表中按角色筛选可以看到全部。", { total: 75, shown: 2 })), `${locale}: the cap`);
    // nobody usable: said, and the handover pointed to for a caller who can make it
    const orphan = { ...DETAIL, counts: { ...DETAIL.counts, usable_owners: 0 } };
    const warn = tx(locale, "这个组织没有可用的所有者：所有者的账户已停用或已删除。"), point = tx(locale, "可以在本页「没有有效拥有者的组织」中把一位现有成员指定为拥有者。");
    assert.ok(text(body(locale, MANAGE, orphan)).includes(`${warn} ${point}`), locale);
    const viewOnly = text(body(locale, VIEW, orphan));
    assert.ok(viewOnly.includes(warn) && !viewOnly.includes(point), `${locale}: no pointer to a handover the role cannot make`);
    assert.ok(!text(html).includes(warn), `${locale}: no warning while an owner is usable`);
  }
});

test("addresses are shown only when the server sends them: no placeholder, and the search says it matches names", () => {
  for (const locale of LOCALES) {
    const visible = body(locale, WITH_USERS);
    assert.ok(visible.includes(`>${tx(locale, "搜索姓名或邮箱")}</label>`), locale);
    assert.ok(!visible.includes(tx(locale, "你的平台角色不能查看账户邮箱：这里不显示邮箱，搜索只按姓名匹配。")), locale);
    const hidden = body(locale, VIEW, HIDDEN, { members: MEMBERS.map(noEmail) });
    assert.doesNotMatch(hidden, /@/, `${locale}: an address leaked`);
    assert.ok(hidden.includes(`>${tx(locale, "搜索姓名")}</label>`), `${locale}: the search label`);
    assert.ok(hidden.includes(tx(locale, "你的平台角色不能查看账户邮箱：这里不显示邮箱，搜索只按姓名匹配。")), `${locale}: the note`);
    // the person with no display name is named by the account id, not by an empty cell or a dash
    const r = row(hidden, "usr_fx_member");
    assert.match(r, /<th scope="row">usr_fx_member<\/th>/, `${locale}: ${r}`);
    assert.doesNotMatch(row(hidden, ">Fixture Owner<"), /class="who2"/, `${locale}: no second line where the address would be`);
  }
});

test("the member list searches, filters by role, shows custom roles and pages; each member explains their permissions", () => {
  for (const locale of LOCALES) {
    const html = body(locale, WITH_USERS);
    assert.ok(globalThis.__requested.includes(`/api/admin/organizations/${OID}/members`), `${locale}: ${globalThis.__requested}`);
    const members = section(html, tx(locale, "组织成员"));
    assert.match(members, /<input id="[^"]+" type="search" class="input" maxLength="100" value=""\/>/i, `${locale}: the search`);
    for (const role of ["owner", "admin", "member"]) assert.ok(members.includes(`<option value="${role}">${tx(locale, detail.orgRoleText(role))}</option>`) || members.includes(`<option value="${role}">${inLocale(locale, () => detail.orgRoleText(role))}</option>`), `${locale}: ${role}`);
    assert.ok(members.includes(`<option value="" selected="">${tx(locale, "全部角色")}</option>`), `${locale}: all roles first`);
    const r = row(members, "usr_fx_member");
    assert.ok(r.includes('<span class="badge info">Fixture Auditors</span>'), `${locale}: the custom role chip\n${r}`);
    assert.ok(text(r).includes(tx(locale, "成员")), locale);
    // no display name: named by the address the caller may read
    assert.match(r, /<th scope="row">usr_fx_member@fixture\.example\.test<\/th>/, `${locale}: ${r}`);
    assert.match(r, new RegExp(`<button type="button" class="btn sm" aria-label="${esc(tx(locale, "查看有效权限：{name}", { name: "usr_fx_member@fixture.example.test" }))}">${esc(tx(locale, "有效权限"))}</button>`), `${locale}: ${r}`);
    assert.ok(text(row(members, "usr_fx_former")).includes(tx(locale, "账户不可用")), locale);
    assert.ok(text(members).includes(tx(locale, "{p0}–{p1} / {p2} 项", { p0: 1, p1: 50, p2: 120 })), `${locale}: the pager\n${text(members)}`);
    assert.equal((members.match(/<td(?![^>]*data-label=)/g) || []).length, 0, `${locale}: an unlabelled cell`);
    // empty: with and without a filter
    assert.ok(text(body(locale, WITH_USERS, DETAIL, { members: [] })).includes(tx(locale, "这个组织没有成员。")), locale);
  }
});

test("the permission explainer shows each permission with its source: the base role or a custom role by name", () => {
  for (const locale of LOCALES) {
    const html = effective(locale);
    const t = text(html);
    assert.ok(t.includes(tx(locale, "基础角色：{role}", { role: tx(locale, "管理员") })), `${locale}: ${t}`);
    assert.ok(t.includes(tx(locale, "自定义角色：{names}", { names: "Fixture Auditors" })), locale);
    const audit = text(row(html, "org.audit.read"));
    assert.ok(audit.includes("Fixture label audit"), `${locale}: labelled from the catalogue`);
    assert.ok(audit.includes(tx(locale, "基础角色 {role}", { role: tx(locale, "管理员") })) && audit.includes(tx(locale, "角色「{name}」", { name: "Fixture Auditors" })), `${locale}: ${audit}`);
    assert.ok(t.includes(tx(locale, "按成员资格与角色计算得出，不是该成员登录后看到的内容。")), locale);
    // without the catalogue the stable id stands in, once
    const bare = effective(locale, EFFECTIVE, []);
    assert.match(row(bare, "org.audit.read"), /<th scope="row">org\.audit\.read<\/th>/, locale);
    // nothing held
    assert.ok(text(effective(locale, { base_role: "member", roles: [], permissions: [], sources: {} })).includes(tx(locale, "该成员没有组织级权限；工作空间内的权限另行管理。")), locale);
    // a suspended member: no base role and nothing applies — not "a member without permissions"
    const NONE = { base_role: null, roles: [], permissions: [], sources: {} };
    const suspended = text(render(locale, React.createElement(detail.EffectivePermissionsBody,
      { data: NONE, labels: detail.permissionLabels(CATALOGUE), member: { account_status: "suspended", usable: false } })));
    assert.ok(suspended.includes(tx(locale, "这个账户当前不可用（{status}），组织权限对它不生效。", { status: tx(locale, "已停用") })), `${locale}: ${suspended}`);
    assert.ok(!suspended.includes(tx(locale, "该成员没有组织级权限；工作空间内的权限另行管理。")) && !suspended.includes(tx(locale, "基础角色：{role}", { role: "—" })), locale);
    assert.ok(text(effective(locale, NONE)).includes(tx(locale, "这个账户当前没有生效的组织角色，组织权限对它不生效。")), locale);
  }
});

test("workspaces, custom roles and the unfinished imports and copies are listed with their states, never their contents", () => {
  for (const locale of LOCALES) {
    const html = body(locale, WITH_USERS);
    const ws = section(html, tx(locale, "工作空间（{count} 个）", { count: 2 }));
    assert.ok(text(row(ws, "ws_fx_frozen")).includes(tx(locale, "已冻结")) && text(row(ws, "ws_fx_frozen")).includes(tx(locale, "团队")), locale);
    assert.ok(text(row(ws, "ws_fx_main")).includes(inLocale(locale, () => ui.formatNumber(1500))), `${locale}: members formatted`);
    assert.ok(!ws.includes('class="pager"'), `${locale}: no pager for one page`);
    const many = body(locale, WITH_USERS, { ...DETAIL, workspaces: { ...DETAIL.workspaces, total: 130 } });
    assert.ok(section(many, tx(locale, "工作空间（{count} 个）", { count: 130 })).includes('class="pager"'), `${locale}: later pages`);
    assert.ok(text(body(locale, WITH_USERS, { ...DETAIL, workspaces: { items: [], total: 0 } })).includes(tx(locale, "这个组织还没有工作空间。")), locale);

    const roles = text(section(html, tx(locale, "自定义角色（{count} 个）", { count: 2 })));
    assert.ok(roles.includes("Fixture Auditors") && roles.includes(tx(locale, "已分配给 {count} 位成员", { count: 3 })), `${locale}: ${roles}`);
    assert.ok(roles.includes("org.audit.read") && roles.includes("org.members.view"), `${locale}: ids stand in without the catalogue`);
    assert.ok(roles.includes(tx(locale, "这个角色没有权限。")), locale);
    const labelled = text(section(body(locale, WITH_USERS, { ...DETAIL, permission_catalogue: CATALOGUE }), tx(locale, "自定义角色（{count} 个）", { count: 2 })));
    assert.ok(labelled.includes("Fixture label audit") && !labelled.includes("org.audit.read"), `${locale}: the tenant labels when the server sends them`);
    assert.ok(text(body(locale, WITH_USERS, { ...DETAIL, custom_roles: [] })).includes(tx(locale, "还没有自定义角色。")), locale);

    const pending = section(html, tx(locale, "未完成的成员导入与复制申请"));
    assert.ok(text(row(pending, "imp_fx_one")).includes(tx(locale, "已生成预览")) && text(row(pending, "imp_fx_one")).includes("Fixture Admin"), locale);
    assert.ok(text(row(pending, "imp_fx_one")).includes(inLocale(locale, () => ui.formatNumber(1500))), `${locale}: rows formatted`);
    assert.ok(text(row(pending, "imp_fx_two")).includes(tx(locale, "执行中")), locale);
    const copy = text(row(pending, "cpy_fx_one"));
    assert.ok(copy.includes(tx(locale, "已批准，等待复制")) && copy.includes("Fixture Main Workspace") && copy.includes("ws_fx_personal") && copy.includes("usr_fx_requester"), `${locale}: ${copy}`);
    assert.ok(copy.includes(inLocale(locale, () => ui.fmtTime(T + 86400))), `${locale}: the approval's deadline`);
    assert.ok(pending.includes(`<th scope="col">${tx(locale, "当前步骤截止")}</th>`), `${locale}: the deadline is the current step's`);
    const running = text(row(pending, "cpy_fx_two"));
    assert.ok(running.includes(tx(locale, "复制中")) && running.includes(tx(locale, "执行中，无截止")) && running.includes("Fixture Requester"), `${locale}: ${running}`);
    const extracted = text(row(pending, "imp_fx_three"));
    assert.ok(extracted.includes(tx(locale, "已读取")) && extracted.includes(tx(locale, "生成预览后计数")) && !/ 0 /.test(` ${extracted} `), `${locale}: 0 rows is not an empty import\n${extracted}`);
    assert.ok(text(pending).includes(tx(locale, "共 {total} 项，这里显示最新的 {shown} 项。", { total: 21, shown: 3 })), `${locale}: the cap`);
    assert.ok(text(pending).includes(tx(locale, "只显示状态、行数和时间：导入的文件、行内容和项目名称不显示。")), locale);
    const quiet = text(body(locale, WITH_USERS, { ...DETAIL, counts: { ...DETAIL.counts, pending_imports: 0, pending_copies: 0 }, pending: { imports: [], copies: [] } }));
    assert.ok(quiet.includes(tx(locale, "没有未完成的成员导入。")) && quiet.includes(tx(locale, "没有未完成的复制申请。")), locale);
    // every state of both tables is worded
    for (const [map, keys] of [[detail.IMPORT_STATE_LABELS, ["extracted", "planned", "running", "finished", "partial", "failed", "cancelled"]],
      [detail.COPY_STATUS_LABELS, ["pending", "approved", "rejected", "withdrawn", "expired", "running", "done", "failed", "stale"]]]) {
      for (const key of keys) assert.ok(map[key] && (locale !== "en" || !HAN.test(tx("en", map[key]))), `${key}`);
    }
    assert.equal((html.match(/<td(?![^>]*data-label=)/g) || []).length, 0, `${locale}: every cell labelled for the cards presentation`);
  }
});

test("loading, refused, missing and failed reads each have their own state", () => {
  for (const locale of LOCALES) {
    assert.ok(view(locale, { loading: true, error: null, data: null }).includes(`role="status">${tx(locale, "正在读取组织详情…")}</p>`), locale);
    const denied = view(locale, { loading: false, error: new client.ApiError(403, "platform_permission_denied", "你的平台角色不包含这项操作所需的权限"), data: null });
    assert.ok(denied.includes(tx(locale, detail.ORG_DETAIL_DENIED)) && !denied.includes("</button>"), `${locale}: ${denied}`);
    const missing = view(locale, { loading: false, error: new client.ApiError(404, "no_organization", "找不到该组织"), data: null });
    assert.ok(missing.includes(tx(locale, detail.ORG_DETAIL_REFUSALS.no_organization)), `${locale}: ${missing}`);
    const failed = view(locale, { loading: false, error: new client.ApiError(503, "unavailable", ""), data: null });
    assert.match(failed, /role="alert"/);
    assert.ok(failed.includes(`>${tx(locale, "重试")}</button>`), `${locale}: retry`);
    assert.ok(view(locale, { loading: false, error: null, data: DETAIL }).includes(tx(locale, "概况")), locale);
  }
  assert.equal(detail.detailProblem(new client.ApiError(403, "platform_permission_denied", "")), "denied");
  assert.equal(detail.detailProblem(new client.ApiError(404, "no_organization", "")), "missing");
  assert.equal(detail.detailProblem(new client.ApiError(403, "step_up_required", "")), "error");
  assert.equal(detail.detailProblem(new Error("network")), "error");
});

test("the refusals of the detail's routes read as sentences on the administration page", () => {
  const codes = ["no_organization", "not_member", "bad_role", "invalid_field", "platform_permission_denied"];
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。", "操作失败，请检查输入或重试。"].map((s) => tx(locale, s));
  for (const code of codes) {
    for (const locale of LOCALES) {
      for (const word of [labels.errText, detail.orgDetailErrText]) {
        const worded = inLocale(locale, () => word(new client.ApiError(400, code, "")));
        assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
        if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
      }
    }
  }
  // the server's own sentences (core `member_page`, admin_org_detail.py) are catalogued and read as sent
  inLocale("en", () => {
    for (const [code, sentence] of [["no_organization", "找不到该组织"], ["bad_role", "请选择有效的组织角色"], ["invalid_field", "搜索或分页参数无效"],
      ["not_member", "该账户不是这个组织的成员"]]) {
      const worded = labels.errText(new client.ApiError(400, code, sentence));
      assert.equal(worded, tx("en", sentence), code);
    }
    // the panel words an organization that is gone for an administrator, not for a member
    assert.equal(detail.orgDetailErrText(new client.ApiError(404, "no_organization", "")), tx("en", detail.ORG_DETAIL_REFUSALS.no_organization));
    assert.equal(detail.orgDetailErrText(new client.ApiError(404, "not_member", "")), tx("en", detail.ORG_DETAIL_REFUSALS.not_member));
  });
});

test("long names wrap inside the panel instead of widening it", () => {
  const css = readFileSync(new URL("../src/admin/admin.css", import.meta.url), "utf8");
  assert.match(css, /\.org-detail-name \{ overflow-wrap: anywhere; \}/);
  assert.match(css, /\.org-detail :is\(dd, th, td, strong, h4\) \{ overflow-wrap: anywhere; \}/);
  assert.match(css, /\.org-detail-chips \.badge, \.org-detail-roles \.badge \{ white-space: normal;/);
  const long = "Fixture" + "Unbroken".repeat(20);
  const html = body("en", WITH_USERS, { ...DETAIL, organization: { ...DETAIL.organization, name: long },
    custom_roles: [{ ...DETAIL.custom_roles[0], name: long }] });
  assert.ok(html.startsWith('<div class="col gap16 org-detail">'), "the body carries the wrapping rules");
  assert.ok(html.includes(`<strong class="grow">${long}</strong>`));
});

test("the English render has no untranslated text", () => {
  const en = [tab("en", MANAGE), tab("en", VIEW, `?tab=organizations&org=${OID}`), body("en", MANAGE), body("en", VIEW, HIDDEN, { members: MEMBERS.map(noEmail) }),
    body("en", MANAGE, { ...DETAIL, organization: { ...DETAIL.organization, status: "deleted", deleted_at: T, recover_until: T, previous_status: "active" },
      counts: { ...DETAIL.counts, usable_owners: 0, pending_imports: null, pending_copies: null }, owners_total: 80, viewer: { is_member: true, role: "owner" } }),
    body("en", WITH_USERS, { ...DETAIL, workspaces: { items: [], total: 0 }, custom_roles: [], pending: { imports: [], copies: [] } }, { members: [] }),
    effective("en"), effective("en", { base_role: "member", roles: [], permissions: [], sources: {} }), effective("en", { base_role: null, roles: [], permissions: [], sources: {} }),
    render("en", React.createElement(detail.EffectivePermissionsBody, { data: { base_role: null, roles: [], permissions: [], sources: {} },
      labels: detail.permissionLabels(undefined), member: { account_status: "suspended", usable: false } })),
    view("en", { loading: true, error: null, data: null }),
    view("en", { loading: false, error: new client.ApiError(403, "platform_permission_denied", ""), data: null }),
    view("en", { loading: false, error: new client.ApiError(404, "no_organization", ""), data: null }),
    view("en", { loading: false, error: new client.ApiError(503, "unavailable", ""), data: null })]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split(/(?<=[.。])\s/).filter((l) => HAN.test(l)).join("\n").slice(0, 2000)}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("every sentence of the panel and the list action is catalogued in English and Traditional Chinese, and this catalogue's wording is the one in effect", () => {
  assert.deepEqual(ui.validateMessages(messages.orgDetailMessages), []);
  for (const [source, entry] of Object.entries(messages.orgDetailMessages)) {
    for (const locale of ["en", "zh-Hant"]) assert.equal(tx(locale, source), entry[locale], `${locale}: ${source} is worded by another catalogue`);
  }
  const sentences = new Set();
  const src = readFileSync(new URL("../src/governance/OrganizationDetail.tsx", import.meta.url), "utf8")
    .split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
  for (const m of src.matchAll(/"([^"\n]*[㐀-鿿][^"\n]*)"/g)) sentences.add(m[1]);
  const tab = readFileSync(new URL("../src/governance/GovernanceTabs.tsx", import.meta.url), "utf8");
  for (const m of tab.slice(tab.indexOf("export function OrganizationsTab"), tab.indexOf("/** Admin coverage G19")).matchAll(/\bt\("([^"]+)"/g)) sentences.add(m[1]);
  assert.ok(sentences.size > 90, `expected the panel's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(tx("en", s, { count: 2 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
