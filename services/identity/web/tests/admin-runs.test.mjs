/** The run pages of platform administration (governance/RunsTab.tsx, governance/runs.ts): who is
 *  offered them, what the address carries, where focus goes, how a capped count and a parked
 *  flow run read, and that every word on them exists in three languages.
 *
 *  The roles are read from platform_access.py, the states from admin_runs.py and from the flow
 *  engine, so a change on the server is checked against the pages without editing this file.
 *  Isolated fixtures: no HTTP server, no account, no run data (the pages render their loading
 *  state; server rendering runs no effects; the parts drawn from an answer are drawn from a
 *  fixture written here). Run from the package: `npm test`.
 */
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../../", import.meta.url));
const read = (file) => readFileSync(join(ROOT, file), "utf8");

const ACCESS = read("packages/core/synwevo_core/platform_access.py");
const roleBlock = ACCESS.slice(ACCESS.indexOf("ROLES: dict[str, frozenset[str]] = {"));
const ROLES = { super_admin: null };
for (const m of roleBlock.slice(0, roleBlock.indexOf("\n}\n")).matchAll(/"(\w+)": frozenset\(\{([^}]*)\}\)/g)) {
  ROLES[m[1]] = new Set([...m[2].matchAll(/"([a-z_.]+)"/g)].map((x) => x[1]));
}
const PERMISSIONS = [...ACCESS.slice(ACCESS.indexOf("PERMISSIONS: dict[str, str] = {"), ACCESS.indexOf("\n}\n", ACCESS.indexOf("PERMISSIONS: dict[str, str] = {")))
  .matchAll(/^\s+"([a-z_.]+)":/gm)].map((m) => m[1]);
ROLES.super_admin = new Set(PERMISSIONS);

const SERVER = read("services/identity/synwevo_identity/admin_runs.py");
const tuple = (name) => {
  const m = SERVER.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"));
  assert.ok(m, name);
  return [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]);
};

const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };

const ENGINE = read("packages/core/synwevo_core/flows/engine.py");
const engineTuple = (name) => {
  const m = ENGINE.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"));
  assert.ok(m, name);
  return [...m[1].matchAll(/"(\w+)"/g)].map((x) => x[1]);
};

let server, ui, nav, access, runs, tab, admin, dialog, lists, data;
before(async () => {
  server = await createServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  nav = await server.ssrLoadModule("/src/admin/navigation.ts");
  access = await server.ssrLoadModule("/src/governance/access.ts");
  runs = await server.ssrLoadModule("/src/governance/runs.ts");
  tab = await server.ssrLoadModule("/src/governance/RunsTab.tsx");
  dialog = await server.ssrLoadModule("/src/governance/PlatformAccess.tsx");
  admin = await server.ssrLoadModule("/src/Admin.tsx");
  lists = await server.ssrLoadModule("/src/governance/ListControls.tsx");
  data = await server.ssrLoadModule("/src/governance/data.ts");
});
after(async () => { await server?.close(); });

const HAN = /[㐀-鿿]/;
function translated(text) {
  assert.ok(ui.hasMessage(text), `no catalogue entry: ${text}`);
  const en = ui.translate("en", text, { n: 2, id: "x", duration: "x", time: "x" });
  assert.ok(!HAN.test(en), `untranslated in English: ${text} → ${en}`);
  assert.ok(ui.translate("zh-Hant", text, { n: 2, id: "x", duration: "x", time: "x" }), text);
}

test("the run pages are offered to exactly the roles that hold platform.apps.view", () => {
  assert.equal(access.TAB_PERMISSION["app-runs"], "platform.apps.view");
  assert.equal(access.TAB_PERMISSION["flow-runs"], "platform.apps.view");
  assert.ok(PERMISSIONS.includes("platform.apps.view"));
  const offered = Object.fromEntries(Object.entries(ROLES).map(([role, held]) =>
    [role, nav.adminGroups(held).flatMap((g) => g.items.map((i) => i.id)).filter((id) => id.endsWith("-runs"))]));
  assert.deepEqual(offered, { super_admin: ["app-runs", "flow-runs"], access_admin: ["app-runs", "flow-runs"],
    support: ["app-runs", "flow-runs"], auditor: ["app-runs", "flow-runs"], api_platform_admin: [] });
  // nothing but the permission opens them: not a role name, not another permission's "manage"
  for (const held of [new Set(), new Set(["platform.config.manage"]), new Set(["api_platform.manage"]), new Set(["owner", "admin"])]) {
    assert.equal(nav.resolveAdminPage("/admin", "app-runs", held) === "app-runs", false);
    assert.equal(nav.adminGroups(held).some((g) => g.label === "运行与数据"), false);
  }
  const only = new Set(["platform.apps.view"]);
  assert.deepEqual(nav.adminGroups(only).map((g) => [g.label, g.items.map((i) => i.id)]), [["运行与数据", ["app-runs", "flow-runs"]]]);
  assert.equal(nav.resolveAdminPage("/admin", "flow-runs", only), "flow-runs");
  assert.equal(nav.resolveAdminPage("/admin", null, only), "app-runs");
  // adding the pages does not change where an unqualified /admin opens for the existing roles
  assert.equal(nav.resolveAdminPage("/admin", null, ROLES.support), "overview");
  assert.equal(nav.resolveAdminPage("/admin", null, new Set(["platform.config.view"])), "identity");
});

test("the group, its two pages and the permission are named in three languages", () => {
  for (const label of ["运行与数据", "应用运行", "流程运行"]) translated(label);
  assert.equal(ui.translate("en", "应用运行"), "Application runs");
  assert.equal(ui.translate("zh-Hant", "流程运行"), "流程執行");
  ui.setLocale("en");
  try {
    assert.equal(dialog.permissionLabel("platform.apps.view"), "View business application and flow run records (no content)");
  } finally { ui.setLocale("zh-Hans"); }
});

test("the states and entries the pages word are the server's", () => {
  assert.deepEqual([...runs.RUN_STATES], tuple("RUN_STATES"));
  assert.deepEqual([...runs.VIA], tuple("VIA"));
  // the whole vocabulary: the states a run is claimed in, the ones it is parked in, the ones it ends in
  assert.deepEqual([...runs.FLOW_STATES], [...tuple("FLOW_ACTIVE"), ...tuple("FLOW_PARKED"), ...tuple("FLOW_TERMINAL")]);
  assert.deepEqual([...runs.FLOW_STATES], [...engineTuple("ACTIVE"), ...engineTuple("PARKED"), ...engineTuple("TERMINAL")]);
  assert.deepEqual([...runs.FLOW_PARKED], engineTuple("PARKED"));
  assert.deepEqual([...runs.FLOW_PARKED].sort(), ["blocked", "paused", "unknown"]);
  const windows = SERVER.match(/^WINDOWS_S = \(([^)]*)\)/m)[1].split(",").map((x) => Function(`return ${x}`)());
  assert.deepEqual([...runs.WINDOWS_S], windows);
  assert.equal(runs.DEFAULT_WINDOW_S, 86400);
  for (const state of [...runs.RUN_STATES, ...runs.FLOW_STATES]) translated(runs.STATE_LABEL[state]);
  for (const via of runs.VIA) translated(runs.VIA_LABEL[via]);
  for (const w of runs.WINDOWS_S) translated(runs.WINDOW_LABEL[w]);
  for (const map of [runs.PRINCIPAL_LABEL, runs.DELEGATION_LABEL, runs.STATE_LABEL]) for (const text of Object.values(map)) translated(text);
});

test("every sentence on the run pages has an English and a Traditional Chinese form with the same placeholders", () => {
  const source = read("services/identity/web/src/governance/RunsTab.tsx");
  const sentences = new Set([...source.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]));
  assert.ok(sentences.size > 90, `only ${sentences.size} sentences found`);
  for (const text of sentences) translated(text);
  // the two refusals of admin_runs.py are worded for the administration pages
  for (const m of SERVER.matchAll(/ServiceError\(\d+, "\w+", "([^"]+)"/g)) translated(m[1]);
  assert.equal(ui.translate("en", "找不到这次运行"), "This run could not be found.");
});

test("the address carries the filters and the open run, and nothing of another page's view", () => {
  const view = runs.readView("?tab=app-runs&lang=en&status=attention&app=forms&run=run_1&unknown=x", runs.RUN_VIEW_KEYS);
  assert.deepEqual(view, { status: "attention", app: "forms", via: "", workspace: "", user: "", q: "", window: "", run: "run_1" });
  assert.equal(runs.withView("?tab=app-runs&lang=en&status=failed&run=run_1", { status: "", run: "", q: "tables." }),
    "?tab=app-runs&lang=en&q=tables.");
  assert.equal(runs.withView("?tab=app-runs", { q: "a b&c=d" }), "?tab=app-runs&q=a+b%26c%3Dd");
  assert.equal(runs.withView("?status=failed", { status: "" }), "");
  // a link to the other page keeps language and origin, drops this page's filters
  assert.equal(runs.runsHref("flow-runs", "?tab=app-runs&lang=zh-Hant&from=platform&status=failed&q=corr_1", { run: "fr_1" }),
    "/admin?lang=zh-Hant&from=platform&tab=flow-runs&run=fr_1");
  assert.equal(runs.runsHref("app-runs", "", { q: "corr 1", run: "" }), "/admin?tab=app-runs&q=corr+1");
  for (const [raw, expected] of [["", 86400], ["3600", 3600], ["2592000", 2592000], ["60", 86400], ["abc", 86400], ["86400.5", 86400]]) {
    assert.equal(runs.windowOf(raw), expected, raw);
  }
});

test("a state is drawn by what it means, and a flagged run is never drawn as fine", () => {
  assert.equal(runs.stateTone("failed"), "err");
  assert.equal(runs.stateTone("failed", true), "err");
  for (const state of ["unknown", "partial"]) assert.equal(runs.stateTone(state), "warn");
  assert.equal(runs.stateTone("running", true), "warn");
  assert.equal(runs.stateTone("queued", true), "warn");
  assert.equal(runs.stateTone("succeeded"), "ok");
  assert.equal(runs.stateTone("cancelled"), "");
  for (const state of ["queued", "running", "waiting_input", "waiting_approval", "retrying"]) assert.equal(runs.stateTone(state), "info");
  assert.equal(runs.elapsedSeconds(100, 160.5), 60.5);
  for (const [from, to] of [[null, 5], [5, null], [9, 5], [undefined, undefined]]) assert.equal(runs.elapsedSeconds(from, to), null);
  assert.equal(runs.appName("forms"), "Synwevo Forms");
  assert.equal(runs.appName("unknown_app"), "unknown_app");
  assert.equal(runs.appName(null), "—");
});

test("the real Admin entry mounts the run pages, which say what they do not show before any data arrives", () => {
  const shell = admin.default();
  const render = (active, search = "") => renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [`/admin${search}`] },
    shell.props.children({ held: ROLES.auditor, active })));
  const runsPage = render("app-runs", "?tab=app-runs&status=attention&q=corr_1");
  assert.match(runsPage, /运行概况/);
  assert.match(runsPage, /正在读取运行概况/);
  assert.match(runsPage, /正在加载运行记录/);
  assert.match(runsPage, /输入、输出和失败说明属于发起人，平台管理不显示/);
  assert.match(runsPage, /清除筛选/, "a filter in the address is offered for clearing");
  assert.match(runsPage, /value="corr_1"/, "the search box shows the address's text");
  assert.doesNotMatch(runsPage, /还没有任何运行|没有符合筛选条件的运行/, "an empty table is not announced while loading");
  assert.doesNotMatch(render("app-runs", "?tab=app-runs"), /清除筛选/);
  const flowsPage = render("flow-runs", "?tab=flow-runs");
  assert.match(flowsPage, /流程概况/);
  assert.match(flowsPage, /流程的名称、步骤名称、参数和结果不显示/);
  assert.doesNotMatch(flowsPage, /运行概况/);
  // an open run is a dialog over the list, addressed by the URL
  const open = render("app-runs", "?tab=app-runs&run=run_1");
  assert.match(open, /role="dialog"/);
  assert.match(open, /正在读取运行详情/);
  assert.match(open, /不能由此取消、重试或打开业务内容/);
  assert.equal(typeof tab.AppRunsTab, "function");
  assert.equal(typeof tab.FlowRunsTab, "function");
});

// ---------------------------------------------------------------- parked flow runs (blocked, unknown, paused)
const stats = (html) => Object.fromEntries([...html.matchAll(/<div class="muted small">([^<]+)<\/div><div class="v"[^>]*>([^<]+)<\/div>/g)]
  .map((m) => [m[1], m[2]]));

test("a parked flow run has a word and a tone, is counted in the summary and can be filtered for", () => {
  assert.equal(ui.translate("en", runs.STATE_LABEL.blocked), "Blocked");
  assert.equal(ui.translate("en", runs.STATE_LABEL.paused), "Paused");
  assert.equal(ui.translate("zh-Hant", runs.STATE_LABEL.paused), "已暫停");
  assert.equal(ui.translate("en", runs.STATE_LABEL.unknown), "Outcome unknown");
  // blocked and unknown wait for their owner after something went wrong; paused is the owner's own choice
  assert.equal(runs.stateTone("blocked"), "warn");
  assert.equal(runs.stateTone("unknown"), "warn");
  assert.equal(runs.stateTone("paused"), "");
  // the summary, drawn from an answer of the server's shape (fixture numbers)
  const flows = { finished: { succeeded: 5, partial: 0, failed: 2, cancelled: 1 }, active: 3, overdue: 1,
    parked: { paused: 4, blocked: 6, unknown: 7 } };
  const html = renderToStaticMarkup(React.createElement(tab.FlowStats, { flows, time: 1760000000 }));
  assert.deepEqual(stats(html), { "未结束": "3", "已超过期限仍未结束": "1", "受阻": "6", "结果不明": "7", "已暂停": "4",
    "24 小时内失败": "2", "24 小时内部分成功": "0", "24 小时内成功": "5" });
  assert.match(html, /「受阻」「结果不明」「已暂停」的运行停在原处/);
  // the filter offers the three states, and says what "attention" covers
  const page = renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: ["/admin?tab=flow-runs"] },
    admin.default().props.children({ held: ROLES.auditor, active: "flow-runs" })));
  for (const [state, word] of [["paused", "已暂停"], ["blocked", "受阻"], ["unknown", "结果不明"]]) {
    assert.ok(page.includes(`<option value="${state}">${word}</option>`), state);
  }
  assert.match(page, /需要关注（失败、部分成功、受阻、结果不明、已超过期限或已停滞）/);
  assert.equal(ui.translate("en", "需要关注（失败、部分成功、受阻、结果不明、已超过期限或已停滞）"),
    "Needs attention (failed, partly succeeded, blocked, outcome unknown, past its deadline or stalled)");
});

// ---------------------------------------------------------------- the capped count
test("a list past the count cap reads “10,000+”, pages on by the server's word, and is not sent back", () => {
  const pager = (props) => renderToStaticMarkup(React.createElement(lists.PageControls, { onPage() {}, ...props }));
  const next = (html) => !/<button[^>]*disabled=""[^>]*>下一页<\/button>/.test(html);
  // counted exactly: unchanged
  assert.match(pager({ offset: 0, total: 120 }), />1–50 \/ 120 项</);
  assert.equal(next(pager({ offset: 50, total: 120 })), true);
  assert.equal(next(pager({ offset: 100, total: 120 })), false);
  // capped: "more than", and the next page exists as long as the server says so — also past the cap
  const first = pager({ offset: 0, total: 10000, shown: 50, capped: true, more: true });
  assert.match(first, />1–50 \/ 10,000\+ 项</);
  assert.equal(next(first), true);
  const edge = pager({ offset: 9950, total: 10000, shown: 50, capped: true, more: true });
  assert.equal(next(edge), true, "the last counted page is not the last page");
  const last = pager({ offset: 10000, total: 10000, shown: 1, capped: true, more: false });
  assert.match(last, />10,001–10,001 \/ 10,000\+ 项</);
  assert.equal(next(last), false);
  assert.deepEqual(lists.pageRange({ offset: 10000, total: 10000, shown: 1, capped: true, more: false }), { first: 10001, last: 10001, next: false });
  assert.deepEqual(lists.pageRange({ offset: 50, total: 120 }), { first: 51, last: 100, next: true });
  assert.equal(ui.translate("en", "{p0}–{p1} / {p2}+ 项", { p0: 1, p1: 50, p2: 10000 }), "1–50 of 10,000+ items");
  assert.equal(ui.translate("zh-Hant", "{p0}–{p1} / {p2}+ 项", { p0: 1, p1: 50, p2: 10000 }), "1–50 / 10,000+ 項");
  // a page with rows on it past the cap is a page; only an empty one is past the end
  assert.equal(data.pastTheEnd({ items: [{}], total: 10000, total_capped: true }, 10000), false);
  assert.equal(data.pastTheEnd({ items: [], total: 10000, total_capped: true }, 10050), true);
  assert.equal(data.pastTheEnd({ items: [], total: 7 }, 50), true);
  assert.equal(data.pastTheEnd({ items: [{}], total: 70 }, 50), false);
  assert.equal(data.pastTheEnd({ items: [], total: 0 }, 0), false);
  // the run pages hand the pager what the server said
  const source = read("services/identity/web/src/governance/RunsTab.tsx");
  assert.equal((source.match(/capped=\{list\.data\?\.total_capped\}\s+more=\{list\.data\?\.more\}/g) || []).length, 2);
});

// ---------------------------------------------------------------- where focus goes
test("arriving on a page is not a move: the first place to resolve takes no focus, a later page does", () => {
  const held = ROLES.auditor;
  const linked = "?tab=app-runs&lang=en&run=run_1";
  // before the session answers nobody holds anything, so no page — and no place — is resolved
  const unresolved = nav.adminPlace("/admin", linked, nav.resolveAdminPage("/admin", "app-runs", new Set()));
  assert.equal(unresolved, null);
  const arrived = nav.adminPlace("/admin", linked, nav.resolveAdminPage("/admin", "app-runs", held));
  assert.equal(arrived, "/admin?tab=app-runs");
  assert.equal(nav.movedPage(null, arrived), false, "the first resolution: focus stays in the run's dialog");
  assert.equal(nav.movedPage(arrived, null), false, "the session is being read again: nothing moved yet");
  assert.equal(nav.movedPage(null, null), false);
  // an unqualified /admin resolves to the first page the person has: also an arrival
  assert.equal(nav.movedPage(null, nav.adminPlace("/admin", "", nav.resolveAdminPage("/admin", null, held))), false);
  // the page's own view in the address — filters, the open run, a typed search — is the same place
  for (const search of ["?tab=app-runs", "?tab=app-runs&status=attention&q=tables.", "?tab=app-runs&lang=en&run=run_2"]) {
    assert.equal(nav.movedPage(arrived, nav.adminPlace("/admin", search, "app-runs")), false, search);
  }
  // another page is a move, whatever view it opens with
  assert.equal(nav.movedPage(arrived, nav.adminPlace("/admin", "?tab=flow-runs&run=fr_1", "flow-runs")), true);
  assert.equal(nav.movedPage(arrived, nav.adminPlace("/admin", "?tab=users", "users")), true);
  // the API console navigates inside `search`: every change there is still a move, arriving is not
  const keys = nav.adminPlace("/admin/api-platform", "?view=keys", "api-platform");
  assert.equal(nav.movedPage(null, keys), false);
  assert.equal(nav.movedPage(keys, nav.adminPlace("/admin/api-platform", "?view=calls", "api-platform")), true);
  assert.equal(nav.movedPage(keys, keys), false);
  // the layout decides with these and starts with no previous place
  const layout = read("services/identity/web/src/admin/AdminLayout.tsx");
  assert.match(layout, /const previous = useRef<string \| null>\(null\);/);
  assert.match(layout, /if \(movedPage\(previous\.current, place\)\) heading\.current\?\.focus\(\);/);
  assert.match(layout, /<h1 ref=\{heading\} id=\{ADMIN_HEADING_ID\} tabIndex=\{-1\}>/);
});

test("a run dialog that closes with nothing to return focus to sends it to the page heading", () => {
  // closed, and the button that opened it is gone (the list was read again) or never existed (a link)
  assert.equal(runs.closedWithoutFocus("run_1", "", false), true);
  // closed, and the shared dialog returned focus to its opener
  assert.equal(runs.closedWithoutFocus("run_1", "", true), false);
  // another run in the same dialog, opening one, and nothing open at all are not a close
  assert.equal(runs.closedWithoutFocus("run_1", "run_2", false), false);
  assert.equal(runs.closedWithoutFocus("", "run_1", false), false);
  assert.equal(runs.closedWithoutFocus("", "", false), false);
  assert.equal(nav.ADMIN_HEADING_ID, "admin-page-title");
  const source = read("services/identity/web/src/governance/RunsTab.tsx");
  assert.match(source, /if \(lost\) document\.getElementById\(ADMIN_HEADING_ID\)\?\.focus\(\);/);
});

// ---------------------------------------------------------------- words that meant something else
test("the operation column and the deadline are not worded as the actions column and a duration", () => {
  const headers = (html, table) => {
    const head = html.slice(html.indexOf(`aria-label="${table}"`)).match(/<thead><tr>(.*?)<\/tr><\/thead>/)[1];
    return [...head.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => ui.translate("en", m[1]));
  };
  const render = (active) => renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [`/admin?tab=${active}`] },
    admin.default().props.children({ held: ROLES.auditor, active })));
  const runHeaders = headers(render("app-runs"), "运行记录");
  assert.equal(runHeaders.length, 7);
  assert.equal(runHeaders[0], "Operation");
  assert.equal(runHeaders[6], "Details");
  const flowHeaders = headers(render("flow-runs"), "流程运行记录");
  assert.equal(flowHeaders.length, 7);
  assert.equal(flowHeaders[5], "Deadline");
  for (const header of [...runHeaders, ...flowHeaders]) assert.ok(!["Actions", "Duration"].includes(header), header);
  assert.equal(ui.translate("zh-Hant", "操作名"), "操作名稱");
  assert.equal(ui.translate("zh-Hant", "截止时间"), "截止時間");
  // in the dialogs too: "操作" is left to the one column of buttons, "期限" is not used at all
  const source = read("services/identity/web/src/governance/RunsTab.tsx");
  assert.deepEqual(source.match(/<th[^>]*>\{t\("操作"\)\}<\/th>/g), ['<th scope="col" className="act">{t("操作")}</th>']);
  assert.equal((source.match(/t\("操作"\)/g) || []).length, 1);
  assert.equal(source.includes('t("期限")'), false);
  assert.equal(ui.translate("en", "耗时"), "Duration", "the time a run took is a duration; a deadline is not");
});

test("a step with no recorded operation run says so, whatever kind of step it is", () => {
  const step = (position, more) => ({ position, status: "succeeded", attempt: 1, checks: 0, error_code: null, op_run_id: null,
    op: null, app: null, op_status: null, started_at: 1760000000, updated_at: 1760000000, ...more });
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(tab.FlowSteps, { search: "?tab=flow-runs", steps: [
    step(1, { status: "failed", error_code: "read_only_role" }),      // a call the operation refused: no run was kept
    step(2, { status: "executing" }),                                // a call still out
    step(3, { op_run_id: "run_1", op: "forms.request.create", app: "forms", op_status: "succeeded" })] })));
  const rows = html.match(/<tr><th scope="row">.*?<\/tr>/g);
  assert.equal(rows.length, 3);
  for (const row of rows.slice(0, 2)) {
    assert.match(row, /<td>没有记录到操作运行<\/td>/);
    assert.match(row, /<td class="act">—<\/td>/);
  }
  assert.match(rows[0], /<code>read_only_role<\/code>/);
  assert.match(rows[2], /forms\.request\.create/);
  assert.match(rows[2], /href="\/admin\?tab=app-runs&amp;run=run_1"/);
  assert.doesNotMatch(html, /不调用操作/);
  assert.equal(ui.translate("en", "没有记录到操作运行"), "No operation run was recorded");
  assert.equal(ui.translate("zh-Hant", "没有记录到操作运行"), "沒有記錄到操作執行");
});

test("a correlation id the caller chose is recognised as the server's digest, and the page says what it is", () => {
  assert.equal(runs.isCorrelationDigest("ext-2b6b54ceaa180d19be45f03a"), true);
  for (const made of ["run_0123456789ab", "flowrun_0123456789ab", "ext-2b6b54ceaa180d19be45f03", "ext-2B6B54CEAA180D19BE45F03A", "next-2b6b54ceaa180d19be45f03a", ""]) {
    assert.equal(runs.isCorrelationDigest(made), false, made);
  }
  // the form is the server's: "ext-" and 24 hex digits
  assert.match(SERVER, /^_TOKEN = re\.compile\(r"ext-\[0-9a-f\]\{24\}"\)$/m);
  translated("调用方自己指定的关联编号，这里只显示它的摘要；按摘要可以搜索到这些运行，按原文搜不到。");
  // R3-AR-4: the server never searches by the caller's original text (only by the digest), so the
  // page must not tell an administrator that the original finds the runs
  for (const lang of ["zh-Hans", "en", "zh-Hant"]) {
    const said = ui.translate(lang, "调用方自己指定的关联编号，这里只显示它的摘要；按摘要可以搜索到这些运行，按原文搜不到。", {});
    assert.doesNotMatch(said, /或原文都能|or by the original text finds|或原文都能搜尋/, lang);
  }
});
