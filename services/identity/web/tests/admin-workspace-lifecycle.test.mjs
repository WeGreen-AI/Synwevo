/** A44: the platform workspace lifecycle on the administration page — which rows of the workspace
 *  list (Admin.tsx WorkspacesTab) offer rename, delete or recover, by the caller's
 *  `platform.workspaces.manage` and by the workspace's kind and state; the status filter and the
 *  recovery deadline of deleted workspaces; what the preview dialog
 *  (governance/WorkspaceLifecycleDialog.tsx) says for each action and note; the result with what
 *  was withdrawn and the restore note; the new name's local check; the confirmed-operation
 *  protocol's keys and re-previews; and the refusals of the preview and the action.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data and the dialog's preview and result
 *  rendered from fixtures shaped like the A44 contract: no API, no user data. The roles are read
 *  from platform_access.py itself, as in admin-apps.test.mjs. Run from the package: `npm test`.
 *
 *  The review of A44 (2026-10-13) added the regressions at the end: credentials issued through an
 *  API approval are never promised back (A44-WEB-1); a recovery's job and webhook meanings and its
 *  notes say what stays stopped (A44-WEB-2); nothing implies a purge after the window (A44-WEB-3);
 *  a recovery promises no access where the state, the organization or a missing owner withholds it
 *  (A44-WEB-4, the probe recover_frozen_render.mjs); the ownership panel re-reads after an act
 *  (A44-WEB-5); focus settles on the outcome once the list has re-read (A44-WEB-6). */
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
const { MemoryRouter } = require("react-router-dom");

// ------------------------------------------------------------------------------------ roles
const PY = readFileSync(join(ROOT, "packages/core/synwevo_core/platform_access.py"), "utf8");
const block = (start, end) => { const i = PY.indexOf(start); assert.ok(i >= 0, start); return PY.slice(i, PY.indexOf(end, i)); };
const PY_WORKSPACES = readFileSync(join(ROOT, "services/identity/synwevo_identity/admin_workspaces.py"), "utf8");
const PERMISSIONS = [...block("PERMISSIONS: dict[str, str] = {", "\n}\n").matchAll(/^\s+"([a-z_.]+)":/gm)].map((m) => m[1]);
const ROLES = { super_admin: new Set(PERMISSIONS) };
for (const m of block("ROLES: dict[str, frozenset[str]] = {", "\n}\n").matchAll(/"(\w+)": frozenset\(\{([^}]*)\}\)/g)) {
  ROLES[m[1]] = new Set([...m[2].matchAll(/"([a-z_.]+)"/g)].map((x) => x[1]));
}

// Explicit browser-environment fixture, as in admin-organization-lifecycle.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin?tab=workspaces"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of the administration data loader for Admin.tsx and the ownership
 *  panel the workspace tab embeds: answers each path from `globalThis.__fixtures` and records the
 *  list paths asked for in `globalThis.__requested`. */
const FIXTURE_LOADER = `
export function useAdminData(path) {
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  return { data: f.data ?? null, err: f.err ?? null, loading: !!f.loading, reload() {} };
}
export function useAdminPage(path) {
  globalThis.__requested?.push(path);
  const f = globalThis.__fixtures[path.split("?")[0]] || {};
  const items = f.items ?? [];
  return { data: { items, total: items.length, offset: 0, limit: 50, snapshot: 0 }, err: f.err ?? null, loading: !!f.loading, reload() {}, offset: 0, setOffset() {} };
}`;

let server, ui, client, admin, dialog, messages, labels;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "workspace-lifecycle-fixture", enforce: "pre",
      resolveId(id, importer) {
        if ((id === "./governance/data" && importer?.endsWith("/src/Admin.tsx"))
          || (id === "./data" && importer?.endsWith("/src/governance/OwnershipRecovery.tsx"))) return "\0workspace-lifecycle-fixture-data";
      },
      load(id) { if (id === "\0workspace-lifecycle-fixture-data") return FIXTURE_LOADER; },
      // the page's workspace list, reachable for rendering on its own
      transform(code, id) { if (id.endsWith("/src/Admin.tsx")) return code + "\nexport { WorkspacesTab };\n"; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  messages = await server.ssrLoadModule("/src/governance/workspace-lifecycle.messages.ts");
  dialog = await server.ssrLoadModule("/src/governance/WorkspaceLifecycleDialog.tsx");
  // the page's whole catalogue, as the browser has it on the administration page
  admin = await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// ------------------------------------------------------------------------------------ fixtures
// FIXTURE: GET /api/admin/workspaces rows, the preview and the result as the A44 contract has the
// server answer them — metadata and counts only. Synthetic ids and names.
const T = 1_790_000_000;
const FAR = 4_102_444_800; // 2100-01-01: a recovery window still open whenever this runs
const ORG = { id: "org_fx_one", name: "Fixture Org", status: "active" };
const ws = (over = {}) => ({ id: "ws_fx_active", name: "Fixture Active", kind: "org", status: "active", organization_id: ORG.id,
  organization: ORG, created_at: T - 86400, deleted_at: null, recover_until: null, previous_status: null, members: 4, projects: 3, apps: ["docs"], ...over });
const ROWS = [
  ws(),
  ws({ id: "ws_fx_frozen", name: "Fixture Frozen", status: "frozen", organization: { ...ORG, status: "frozen" } }),
  ws({ id: "ws_fx_personal", name: "Fixture Personal", kind: "personal", organization_id: null, organization: null }),
  ws({ id: "ws_fx_unclaimed", name: "Fixture Unclaimed", kind: "legacy", status: "pending_claim", organization_id: null, organization: null }),
  ws({ id: "ws_fx_claimed", name: "Fixture Claimed", kind: "legacy", status: "active", organization_id: null, organization: null }),
];
const RECOVERABLE = ws({ id: "ws_fx_recoverable", name: "Fixture Recoverable", status: "deleted", deleted_at: T, recover_until: FAR, previous_status: "frozen" });
const EXPIRED = ws({ id: "ws_fx_expired", name: "Fixture Expired", status: "deleted", deleted_at: T - 8 * 86400, recover_until: T - 86400, previous_status: "active" });
const DELETED_ROWS = [RECOVERABLE, EXPIRED];
const IMPACT = { members: 4, usable_owners: 1, sessions: 3, tokens: 2, invitations: 1, approvals: 2, api_keys: 1, service_accounts: 1,
  running_jobs: 2, webhooks: 1, form_links: 2, projects: 5, org_templates: 1 };
const NOTES = {
  delete: ["data_kept", "sessions_moved", "tokens_revoked", "invitations_revoked", "approvals_revoked_for_good", "jobs_halt_for_good",
    "webhooks_disabled_for_good", "credentials_paused", "forms_links_paused", "org_templates_kept", "recover_within_7_days"],
  // FIXTURE: a recovery after a deletion that revoked approvals, halted jobs and disabled
  // subscriptions, with paused credentials, Forms links and organization templates left
  recover: ["data_kept", "approvals_revoked_for_good", "jobs_halt_for_good", "webhooks_disabled_for_good", "credentials_paused",
    "forms_links_paused", "org_templates_kept", "recover_not_journalled"],
  rename: ["rename_visible_everywhere", "rename_not_journalled"],
};
const TO = { delete: "deleted", recover: "active", rename: "active" };
const plan = (action, over = {}) => ({ action, transition: { from: action === "recover" ? "deleted" : "active", to: TO[action] },
  target: action === "recover" ? { ...RECOVERABLE, previous_status: "active" } : ws(), name: action === "rename" ? "Fixture Renamed" : null,
  impact: IMPACT, notes: NOTES[action], risk: action === "delete" ? "high" : "medium", expires_at: T + 300, preview_token: "fixture-signed-preview", ...over });
const WITHDRAWN = { sessions: 3, tokens: 2, codes: 1, invitations: 1, approvals: 2 };
const result = (action, over = {}) => ({ ok: true, operation_id: "op_fx_1", state: "succeeded", action,
  workspace: action === "delete" ? ws({ status: "deleted", deleted_at: T, recover_until: T + 7 * 86400, previous_status: "active" })
    : action === "rename" ? ws({ name: "Fixture Renamed" }) : ws(),
  withdrawn: action === "delete" ? WITHDRAWN : null, restore_note: action === "delete" ? null : `${action}_not_journalled`, ...over });

const VIEW = new Set(["platform.workspaces.view"]);
const MANAGE = new Set(["platform.workspaces.view", "platform.workspaces.manage"]);
const ACTIONS = ["rename", "delete", "recover"];
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /Fixture (?:Org|Active|Frozen|Personal|Unclaimed|Claimed|Recoverable|Expired|Renamed)|(?:ws|org|op)_fx_[a-z_0-9]+|fixture_unknown_note|fixture-signed-preview/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const row = (html, id) => html.split("<tr").find((part) => part.includes(id)).split("</tr>")[0];
const time = (locale, ts) => inLocale(locale, () => ui.fmtTime(ts));
function tab(locale, held, items = ROWS) {
  globalThis.__requested = [];
  globalThis.__fixtures = {
    "/api/admin/workspaces": { items },
    "/api/admin/ownership/orphans": { data: { as_of: T, limit: 50, workspaces: { total: 0, items: [] }, organizations: { total: 0, items: [] } } },
  };
  return render(locale, React.createElement(MemoryRouter, { initialEntries: ["/admin?tab=workspaces"] },
    React.createElement(admin.WorkspacesTab, { onDenied() {}, held })));
}
const menuLabel = (locale, name) => tx(locale, "{name} 的工作空间操作", { name });
const hasMenu = (html, locale, name) => html.includes(`aria-label="${menuLabel(locale, name)}"`);
const impact = (locale, action, p = plan(action)) => render(locale, React.createElement(dialog.WorkspaceLifecycleImpact, { action, plan: p }));
const fullDialog = (locale, action, workspace = ws()) => render(locale, React.createElement(dialog.WorkspaceLifecycleDialog, { workspace, action, onClose() {}, onDone() {} }));
const outcome = (locale, r) => render(locale, React.createElement(dialog.WorkspaceLifecycleOutcome, { result: r, onClose() {} }));
const label = (locale, action) => tx(locale, dialog.WORKSPACE_ACTION_LABELS[action]);

// ------------------------------------------------------------------------------------ tests
test("each row offers what the server would accept: personal rename only, the historical data nothing, a deleted one recover until its window closes", () => {
  const now = T * 1000;
  const offers = (w) => dialog.workspaceLifecycleOffers(w, now).map((o) => [o.action, o.disabled, o.hint ?? null]);
  assert.deepEqual(offers(ws()), [["rename", false, null], ["delete", false, null]]);
  assert.deepEqual(offers(ws({ status: "frozen" })), [["rename", false, null], ["delete", false, null]]);
  assert.deepEqual(offers(ws({ kind: "personal" })), [["rename", false, null]]);
  assert.deepEqual(offers(ws({ kind: "personal", status: "frozen" })), [["rename", false, null]]);
  assert.deepEqual(offers(ws({ kind: "legacy", status: "pending_claim" })), []);
  assert.deepEqual(offers(ws({ kind: "legacy", status: "active" })), [], "a claimed historical workspace keeps its claim lifecycle");
  assert.deepEqual(offers(ws({ kind: "org", status: "pending_claim" })), []);
  assert.deepEqual(offers(RECOVERABLE), [["recover", false, null]]);
  assert.deepEqual(offers(EXPIRED), [["recover", true, "恢复期限已过"]]);
  assert.deepEqual(offers({ ...RECOVERABLE, recover_until: null }), [["recover", true, "恢复期限已过"]], "no deadline is no window");
  assert.deepEqual(offers({ ...RECOVERABLE, kind: "personal" }), []);
  // the window closes at recover_until, not after it
  assert.equal(dialog.workspaceRecoverable({ recover_until: T }, T * 1000), false);
  assert.equal(dialog.workspaceRecoverable({ recover_until: T }, T * 1000 - 1), true);
  // the row's menu: nothing without the permission; with it rename, freeze or unfreeze (A43), then delete
  const actions = (w, manage = true) => dialog.workspaceRowActions(w, manage, now).map((a) => `${a.action}${a.danger ? "!" : ""}${a.disabled ? "-" : ""}`);
  assert.deepEqual(actions(ws(), false), []);
  assert.deepEqual(actions(RECOVERABLE, false), []);
  assert.deepEqual(actions(ws()), ["rename", "freeze!", "delete!"]);
  assert.deepEqual(actions(ws({ status: "frozen" })), ["rename", "unfreeze", "delete!"]);
  assert.deepEqual(actions(ws({ kind: "personal" })), ["rename", "freeze!"]);
  assert.deepEqual(actions(ws({ kind: "legacy", status: "active" })), ["freeze!"], "freezing the claimed historical data stays as before");
  assert.deepEqual(actions(ws({ kind: "legacy", status: "pending_claim" })), []);
  assert.deepEqual(actions(RECOVERABLE), ["recover"]);
  assert.deepEqual(actions(EXPIRED), ["recover-"]);
  // the roles that hold the permission
  const holders = Object.entries(ROLES).filter(([, held]) => held.has("platform.workspaces.manage")).map(([role]) => role).sort();
  assert.deepEqual(holders, ["access_admin", "super_admin"]);
});

test("the list offers the menu by platform.workspaces.manage and kind, shows the organization, and says why a view-only role cannot act", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, MANAGE);
    for (const name of ["Fixture Active", "Fixture Frozen", "Fixture Personal", "Fixture Claimed"]) assert.ok(hasMenu(html, locale, name), `${locale}: ${name}\n${html}`);
    assert.ok(!hasMenu(html, locale, "Fixture Unclaimed"), `${locale}: the unclaimed historical data has no lifecycle menu`);
    assert.ok(text(row(html, "Fixture Active")).includes(tx(locale, "所属组织：{name}", { name: ORG.name })), `${locale}: the organization`);
    assert.ok(text(row(html, "Fixture Frozen")).includes(`${tx(locale, "所属组织：{name}", { name: ORG.name })} · ${tx(locale, "已冻结")}`), `${locale}: a frozen organization`);
    assert.ok(!text(row(html, "Fixture Personal")).includes(tx(locale, "所属组织：{name}", { name: "" }).trim()), `${locale}: no organization for a personal workspace`);
    assert.ok(text(html).includes(tx(locale, "持有工作空间管理权限的平台管理员可以重命名、冻结、删除或恢复工作空间：重命名、删除和恢复都要先预览影响，再写明理由并重新验证身份。删除后的工作空间对所有人不可访问，七天内可以恢复；期满后数据仍然保存，只是不能再从控制台恢复。")), `${locale}: the explanation`);
    assert.ok(!html.includes(tx(locale, "你的平台角色可以查看工作空间，但不能冻结、解冻、重命名、删除或恢复。")), `${locale}: no hint for a role that may act`);
    // narrow screens: every cell carries its label for the card layout
    assert.match(html, /class="gov admin-cards-table"/);
    for (const name of ["类型", "状态", "成员", "项目", "操作"]) assert.ok(row(html, "Fixture Active").includes(`data-label="${tx(locale, name)}"`), `${locale}: ${name}`);
    for (const role of ["support", "auditor"]) {
      const viewOnly = tab(locale, ROLES[role]);
      for (const name of ["Fixture Active", "Fixture Frozen", "Fixture Personal", "Fixture Claimed"]) assert.ok(!hasMenu(viewOnly, locale, name), `${locale} ${role}: ${name}`);
      assert.ok(viewOnly.includes(tx(locale, "你的平台角色可以查看工作空间，但不能冻结、解冻、重命名、删除或恢复。")), `${locale} ${role}: the hint`);
    }
    assert.ok(hasMenu(tab(locale, ROLES.access_admin), locale, "Fixture Active"), `${locale}: the account and access administrator`);
  }
});

test("the status filter lists deleted workspaces on request, each with its recovery deadline, the expired ones saying so", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, MANAGE, DELETED_ROWS);
    // the default listing leaves the deleted out (the server's rule) and the filter offers them
    assert.deepEqual(globalThis.__requested.map((p) => p.split("?")[0]), ["/api/admin/workspaces"]);
    assert.ok(!globalThis.__requested[0].includes("status="), globalThis.__requested[0]);
    const select = html.slice(html.indexOf("<select"), html.indexOf("</select>"));
    assert.deepEqual([...select.matchAll(/<option value="([^"]*)"/g)].map((m) => m[1]), ["", "active", "frozen", "pending_claim", "deleted"]);
    assert.ok(select.includes(`>${tx(locale, "已删除")}</option>`) && select.includes(`>${tx(locale, "全部（不含已删除）")}</option>`), `${locale}: ${select}`);
    assert.ok(html.includes(`>${tx(locale, "工作空间状态")}`), `${locale}: the filter is labelled`);
    const open = text(row(html, "Fixture Recoverable")), closed = text(row(html, "Fixture Expired"));
    assert.ok(open.includes(tx(locale, "已删除")) && open.includes(tx(locale, "恢复期限：{time}", { time: time(locale, FAR) })), `${locale}: ${open}`);
    assert.ok(open.includes(tx(locale, "删除前状态：{status}", { status: tx(locale, "已冻结") })), `${locale}: the status before deletion`);
    assert.ok(!open.includes(tx(locale, "恢复期限已过")), `${locale}: ${open}`);
    assert.ok(closed.includes(tx(locale, "恢复期限：{time}", { time: time(locale, EXPIRED.recover_until) })) && closed.includes(tx(locale, dialog.RECOVERY_EXPIRED_KEPT)), `${locale}: ${closed}`);
    assert.ok(!open.includes(tx(locale, dialog.RECOVERY_EXPIRED_KEPT)), `${locale}: ${open}`);
    // recover is offered inside the window and shown disabled (with the reason) after it
    assert.ok(hasMenu(html, locale, "Fixture Recoverable") && hasMenu(html, locale, "Fixture Expired"), locale);
    assert.ok(!row(html, "Fixture Recoverable").includes(`>${tx(locale, "成员与邀请")}</a>`), `${locale}: no member page for a deleted workspace`);
  }
  // the list code asks the server for the chosen state (`?status=deleted`)
  const src = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  assert.match(src, /useAdminPage<Workspace>\(`\/api\/admin\/workspaces\$\{qs\(\{ q: query, status \}\)\}`/);
});

test("loading, error and empty states of the list", () => {
  for (const locale of LOCALES) {
    globalThis.__fixtures = { "/api/admin/workspaces": { items: [], loading: true }, "/api/admin/ownership/orphans": { data: null } };
    const loading = render(locale, React.createElement(MemoryRouter, null, React.createElement(admin.WorkspacesTab, { onDenied() {}, held: MANAGE })));
    assert.ok(loading.includes(tx(locale, "正在加载工作空间…")) && loading.includes('aria-busy="true"'), `${locale}: loading`);
    globalThis.__fixtures = { "/api/admin/workspaces": { items: ROWS, err: tx(locale, "无法连接服务，请检查连接后重试。") }, "/api/admin/ownership/orphans": { data: null } };
    const failed = render(locale, React.createElement(MemoryRouter, null, React.createElement(admin.WorkspacesTab, { onDenied() {}, held: MANAGE })));
    assert.match(failed, /class="notice err" role="alert"/, `${locale}: the error`);
    assert.ok(!hasMenu(failed, locale, "Fixture Active"), `${locale}: no actions on rows that failed to load`);
    assert.ok(text(tab(locale, MANAGE, [])).includes(tx(locale, "没有匹配的工作空间。")), `${locale}: empty`);
  }
});

test("a deletion's preview shows every count with what it means, what ends, what never comes back and the window, in every language", () => {
  for (const locale of LOCALES) {
    const rows = text(impact(locale, "delete"));
    assert.ok(rows.includes("Fixture Active") && rows.includes("ws_fx_active"), `${locale}: the target`);
    assert.ok(rows.includes(tx(locale, "状态：{before} → {after}", { before: tx(locale, "正常"), after: tx(locale, "已删除") })), `${locale}: ${rows}`);
    assert.ok(rows.includes(tx(locale, "高风险")), `${locale}: the risk`);
    assert.ok(rows.includes(tx(locale, "所属组织：{name}", { name: ORG.name })), `${locale}: the organization`);
    for (const [key, name, onDelete] of dialog.IMPACT_ROWS) {
      assert.ok(rows.includes(`${tx(locale, name)} ${inLocale(locale, () => ui.formatNumber(IMPACT[key]))} · ${tx(locale, onDelete)}`), `${locale} ${key}\n${rows}`);
    }
    for (const code of NOTES.delete) assert.ok(rows.includes(tx(locale, dialog.NOTE_TEXT[code].delete)), `${locale} ${code}\n${rows}`);
  }
  // the wording says what the brief requires, in the source language
  const said = (code, action) => dialog.NOTE_TEXT[code][action];
  assert.match(said("sessions_moved", "delete"), /不会退出登录/);
  assert.match(said("tokens_revoked", "delete"), /恢复后不会恢复/);
  assert.match(said("invitations_revoked", "delete"), /恢复后不会恢复/);
  assert.match(said("approvals_revoked_for_good", "delete"), /永久撤销.*API 访问密钥和服务账户从此停止工作.*不会重新生效/);
  assert.match(said("jobs_halt_for_good", "delete"), /永久停止.*不会自动继续/);
  assert.match(said("webhooks_disabled_for_good", "delete"), /下一次投递时被停用.*业务应用.*恢复不会重新启用/);
  assert.match(said("credentials_paused", "delete"), /不经 API 凭据批准签发的 API 访问密钥和服务账户.*恢复后.*重新可用.*经批准签发的.*永久停止/);
  assert.match(said("forms_links_paused", "delete"), /表单.*恢复后.*重新可用/);
  assert.match(said("data_kept", "delete"), /不会删除任何数据/);
  assert.match(said("recover_within_7_days", "delete"), /七天/);
  assert.match(dialog.WORKSPACE_ACTION_WARNINGS.delete, /七天恢复期/);
  // a count of zero usable owners: recovery will need one
  const ownerless = text(impact("en", "delete", plan("delete", { impact: { ...IMPACT, usable_owners: 0 } })));
  assert.ok(ownerless.includes(tx("en", dialog.NO_OWNER_NOW)), ownerless);
});

test("a recovery says what comes back, what stays revoked, the journal gap, a blocking organization and a missing owner", () => {
  for (const locale of LOCALES) {
    const rows = text(impact(locale, "recover"));
    assert.ok(rows.includes(tx(locale, "状态：{before} → {after}", { before: tx(locale, "已删除"), after: tx(locale, "正常") })), `${locale}: ${rows}`);
    assert.ok(rows.includes(tx(locale, "恢复期限：{time}", { time: time(locale, FAR) })) && rows.includes(tx(locale, "删除时间：{time}", { time: time(locale, T) })), `${locale}: the window`);
    for (const [key, name, , onRecover] of dialog.IMPACT_ROWS) {
      assert.ok(rows.includes(`${tx(locale, name)} ${inLocale(locale, () => ui.formatNumber(IMPACT[key]))} · ${tx(locale, onRecover)}`), `${locale} ${key}\n${rows}`);
    }
    for (const code of NOTES.recover) assert.ok(rows.includes(tx(locale, dialog.NOTE_TEXT[code].recover)), `${locale} ${code}`);
    assert.ok(!rows.includes(tx(locale, dialog.FROZEN_RECOVERY_NOTE)), `${locale}: back to active`);
    // deleted while frozen: it comes back frozen, and members are told they stay without access
    const frozen = text(impact(locale, "recover", plan("recover", { transition: { from: "deleted", to: "frozen" }, target: RECOVERABLE })));
    assert.ok(frozen.includes(tx(locale, dialog.FROZEN_RECOVERY_NOTE)) && frozen.includes(tx(locale, dialog.MEMBERS_STILL_FROZEN)), `${locale}: ${frozen}`);
    assert.ok(frozen.includes(tx(locale, "删除前状态：{status}", { status: tx(locale, "已冻结") })), locale);
    // the organization blocks access, and nobody is left to manage it: both said, the second pointing to the ownership section
    const blocked = text(impact(locale, "recover", plan("recover", { impact: { ...IMPACT, usable_owners: 0 },
      notes: [...NOTES.recover, "recover_blocked_by_organization", "recover_without_usable_owner"] })));
    for (const s of [dialog.NOTE_TEXT.recover_blocked_by_organization.recover, dialog.NOTE_TEXT.recover_without_usable_owner.recover,
      dialog.MEMBERS_BLOCKED_BY_ORGANIZATION, dialog.NO_OWNER_AFTER_RECOVERY]) assert.ok(blocked.includes(tx(locale, s)), `${locale}: ${s}\n${blocked}`);
  }
  // what the brief requires, in the source language
  assert.match(dialog.NOTE_TEXT.recover_not_journalled.recover, /不会写入安全日志.*备份.*再次处于已删除状态/);
  assert.match(dialog.NOTE_TEXT.approvals_revoked_for_good.recover, /不会恢复/);
  assert.match(dialog.NOTE_TEXT.webhooks_disabled_for_good.recover, /恢复不会重新启用/);
  assert.match(dialog.NOTE_TEXT.credentials_paused.recover, /不经 API 凭据批准签发.*随恢复重新可用.*经批准签发的仍然无效/);
  assert.match(dialog.NOTE_TEXT.forms_links_paused.recover, /随恢复重新可用/);
  // the ownership section it points to is the one on this page (governance/OwnershipRecovery.tsx)
  const ownership = readFileSync(new URL("../src/governance/OwnershipRecovery.tsx", import.meta.url), "utf8");
  const section = /tr\("(没有有效拥有者的工作空间)"\)/.exec(ownership)?.[1];
  assert.ok(section && dialog.NOTE_TEXT.recover_without_usable_owner.recover.includes(`「${section}」`) && dialog.NO_OWNER_AFTER_RECOVERY.includes(`「${section}」`));
});

test("a rename's preview shows the names, the members who see it, and that a restore brings the old name back", () => {
  for (const locale of LOCALES) {
    const rows = text(impact(locale, "rename"));
    assert.ok(rows.includes(tx(locale, "名称：{before} → {after}", { before: "Fixture Active", after: "Fixture Renamed" })), `${locale}: ${rows}`);
    assert.ok(rows.includes(`${tx(locale, "成员")} 4 · ${tx(locale, dialog.RENAME_MEMBERS)}`), `${locale}: ${rows}`);
    assert.ok(!rows.includes(tx(locale, "应用令牌")) && !rows.includes(tx(locale, "会话")), `${locale}: a rename counts only the members`);
    assert.ok(rows.includes(tx(locale, "中等风险")), locale);
    for (const code of NOTES.rename) assert.ok(rows.includes(tx(locale, dialog.NOTE_TEXT[code].rename)), `${locale} ${code}`);
  }
  assert.match(dialog.NOTE_TEXT.rename_not_journalled.rename, /不会写入安全日志.*备份.*原来的名称/);
  assert.match(dialog.NOTE_TEXT.rename_visible_everywhere.rename, /切换器.*邀请邮件/);
});

test("a count the deployment does not take is named once, and a note this page does not know says so with its code", () => {
  for (const locale of LOCALES) {
    const absent = { ...IMPACT, api_keys: null, service_accounts: null, running_jobs: null, webhooks: null, form_links: null, projects: null, org_templates: null };
    const rows = text(impact(locale, "delete", plan("delete", { impact: absent, notes: ["data_kept", "fixture_unknown_note"] })));
    const names = ["api_keys", "service_accounts", "running_jobs", "webhooks", "form_links", "projects", "org_templates"]
      .map((key) => tx(locale, dialog.IMPACT_ROWS.find(([k]) => k === key)[1])).join(" · ");
    assert.ok(rows.includes(tx(locale, dialog.NOT_COUNTED, { names })), `${locale}: ${rows}`);
    assert.ok(!rows.includes(`${tx(locale, "服务账户")} —`) && !/undefined|NaN/.test(rows), `${locale}: ${rows}`);
    assert.ok(rows.includes(tx(locale, dialog.UNKNOWN_NOTE, { code: "fixture_unknown_note" })), `${locale}: ${rows}`);
    // a known note with an action it was not written for is still worded, never as the unknown one
    assert.equal(inLocale(locale, () => dialog.workspaceNoteText("rename", "data_kept")), tx(locale, dialog.NOTE_TEXT.data_kept.rename));
    assert.ok(!inLocale(locale, () => dialog.workspaceNoteText("recover", "sessions_moved")).includes("sessions_moved"), locale);
  }
});

test("the dialog is a labelled confirmation: disabled until a preview is loaded and checked, danger for a deletion, a name field for a rename", () => {
  for (const locale of LOCALES) {
    for (const action of ACTIONS) {
      const html = fullDialog(locale, action, action === "recover" ? RECOVERABLE : ws());
      assert.match(html, /role="alertdialog" aria-modal="true" aria-labelledby="([^"]+)"/, `${locale} ${action}`);
      const titleId = /aria-labelledby="([^"]+)"/.exec(html)[1];
      assert.ok(html.includes(`id="${titleId}">${label(locale, action)}</h2>`), `${locale} ${action}: the title labels the dialog`);
      assert.ok(html.includes(tx(locale, dialog.WORKSPACE_ACTION_WARNINGS[action])), `${locale} ${action}: the warning`);
      assert.ok(html.includes(tx(locale, dialog.WORKSPACE_PLATFORM_NOTE)), `${locale} ${action}: no membership or content access for the administrator`);
      assert.match(html, new RegExp(`<button type="submit"[^>]*class="btn ${action === "delete" ? "danger" : "primary"}"[^>]*disabled=""[^>]*>${esc(label(locale, action))}</button>`),
        `${locale} ${action}: the confirm label, disabled until the preview is loaded and checked\n${html}`);
      assert.ok(html.includes(tx(locale, "理由")), `${locale} ${action}: a reason is asked for`);
    }
    assert.match(fullDialog(locale, "delete"), /class="impact danger"/, `${locale}: the deletion's impact is marked as danger`);
    assert.ok(fullDialog(locale, "delete").includes(tx(locale, "正在核对工作空间与操作影响…")), `${locale}: loading`);
    // a rename starts with the current name and previews only once a name is asked for
    const rename = fullDialog(locale, "rename");
    assert.match(rename, /<input id="([^"]+)" class="input grow" autoComplete="off" spellCheck="false" aria-describedby="\1-help" value="Fixture Active"\/>/, `${locale}\n${rename}`);
    assert.ok(rename.includes(`>${tx(locale, "新名称")}</label>`) && rename.includes(`>${tx(locale, "预览影响")}</button>`), locale);
    assert.ok(rename.includes(tx(locale, "填写新名称后预览影响；名称改动后需要重新预览。")) && !rename.includes(tx(locale, "正在核对工作空间与操作影响…")), locale);
    assert.ok(rename.includes(tx(locale, "1 至 100 个字符，不能包含控制字符；首尾空格会被去掉。")), locale);
  }
});

test("the new name is checked locally by the server's rule, and an unchanged name is refused", () => {
  const p = (value, current = "Fixture Active") => dialog.nameProblem(value, current)?.source ?? null;
  assert.equal(p(""), "请填写新名称。");
  assert.equal(p("   "), "请填写新名称。");
  assert.equal(p("Fixture Active"), "新名称与当前名称相同，无需重命名。");
  assert.equal(p("  Fixture Active  "), "新名称与当前名称相同，无需重命名。", "compared after trimming");
  assert.equal(p("Fixture\nActive"), "名称不能包含换行、制表符等控制字符。");
  assert.equal(p("Fixture\tActive"), "名称不能包含换行、制表符等控制字符。");
  assert.equal(p("Fixture​Active"), "名称不能包含换行、制表符等控制字符。", "a format character is category C, as the server reads it");
  assert.equal(p("x".repeat(100)), null);
  assert.equal(p(` ${"x".repeat(100)} `), null, "the spaces around do not count");
  assert.deepEqual(dialog.nameProblem("x".repeat(101), "a"), { source: "名称最多 {max} 个字符（当前 {count} 个）。", params: { max: 100, count: 101 } });
  assert.equal(p("😀".repeat(100)), null, "characters, not UTF-16 units, as the server counts");
  assert.equal(p("Fixture Renamed"), null);
  for (const locale of ["zh-Hant", "en"]) {
    for (const value of ["", "Fixture Active", "a\nb", "x".repeat(101)]) {
      const { source, params } = dialog.nameProblem(value, "Fixture Active");
      const said = tx(locale, source, params);
      assert.ok(said && (locale !== "en" || !HAN.test(said)), `${locale}: ${said}`);
    }
  }
});

test("one idempotency key per attempt: a retry replays it, a new preview, reason or name starts a new one, and a refused preview asks for a new one", () => {
  let n = 0; const key = () => `fixture-key-${++n}`;
  const first = dialog.attemptFor(null, "fixture-preview-1", "Fixture reason text", null, key);
  assert.equal(first.key, "fixture-key-1");
  assert.equal(dialog.attemptFor(first, "fixture-preview-1", "Fixture reason text", null, key), first, "the same request again replays the same key");
  assert.equal(dialog.attemptFor(first, "fixture-preview-2", "Fixture reason text", null, key).key, "fixture-key-2", "a new preview");
  assert.equal(dialog.attemptFor(first, "fixture-preview-1", "Another fixture reason", null, key).key, "fixture-key-3", "an edited reason");
  const named = dialog.attemptFor(null, "fixture-preview-1", "Fixture reason text", "Fixture Renamed", key);
  assert.notEqual(dialog.attemptFor(named, "fixture-preview-1", "Fixture reason text", "Fixture Other", key).key, named.key, "another name");
  const refusal = (code, upstream = false) => new client.ApiError(409, code, "", undefined, upstream);
  for (const code of ["admin_preview_expired", "admin_preview_stale", "admin_preview_invalid", "idempotency_conflict", "workspace_state_conflict",
    "workspace_recovery_expired", "workspace_name_unchanged", "no_workspace", "journal_unavailable"]) assert.equal(dialog.needsNewPreview(refusal(code)), true, code);
  for (const code of ["missing_reason", "reauth_required", "platform_permission_denied"]) assert.equal(dialog.needsNewPreview(refusal(code)), false, code);
  assert.equal(dialog.needsNewPreview(new client.ApiError(503, "service_unavailable", "", undefined, true)), false, "a gateway's 503 replays the same key");
  assert.equal(dialog.needsNewPreview(refusal("admin_preview_expired", true)), false, "a gateway's answer is not the server's refusal");
  assert.equal(dialog.needsNewPreview(new Error("network")), false);
  // the bodies of the contract
  assert.deepEqual(dialog.previewBody("delete", null), { action: "delete" });
  assert.deepEqual(dialog.previewBody("recover", null), { action: "recover" });
  assert.deepEqual(dialog.previewBody("rename", "Fixture Renamed"), { action: "rename", name: "Fixture Renamed" });
  assert.deepEqual(dialog.actionBody("delete", "fixture-preview-1", "Fixture reason text", "fixture-key-1", null),
    { reason: "Fixture reason text", preview_token: "fixture-preview-1", idempotency_key: "fixture-key-1" });
  assert.deepEqual(dialog.actionBody("rename", "fixture-preview-1", "Fixture reason text", "fixture-key-1", "Fixture Renamed"),
    { reason: "Fixture reason text", preview_token: "fixture-preview-1", idempotency_key: "fixture-key-1", name: "Fixture Renamed" });
  // the routes of the contract
  const src = readFileSync(new URL("../src/governance/WorkspaceLifecycleDialog.tsx", import.meta.url), "utf8");
  assert.ok(src.includes("`/api/admin/workspaces/${encodeURIComponent(workspace.id)}/lifecycle/preview`"));
  assert.ok(src.includes("`/api/admin/workspaces/${encodeURIComponent(workspace.id)}/lifecycle/${action}`"));
});

test("the result shows what a deletion withdrew, the recovery deadline, and the restore note for a rename or a recovery", () => {
  for (const locale of LOCALES) {
    const deleted = outcome(locale, result("delete"));
    assert.match(deleted, /role="dialog" aria-modal="true" aria-labelledby="[^"]+" aria-describedby="[^"]+"/, `${locale}: a labelled, described dialog`);
    assert.ok(deleted.includes(`>${tx(locale, "工作空间已删除")}</h2>`), locale);
    const said = text(deleted);
    assert.ok(said.includes(tx(locale, "「{name}」已删除，可在 {time} 前恢复。", { name: "Fixture Active", time: time(locale, T + 7 * 86400) })), `${locale}: ${said}`);
    for (const [key, name] of dialog.WITHDRAWN_ROWS) assert.ok(said.includes(`${tx(locale, name)} ${WITHDRAWN[key]}`), `${locale} ${key}: ${said}`);
    assert.ok(said.includes(tx(locale, "这些在删除时撤回的访问不会随恢复回来。")) && said.includes(tx(locale, "操作编号：{id}", { id: "op_fx_1" })), locale);
    assert.match(deleted, new RegExp(`<button type="button" class="btn primary" data-autofocus="true">${esc(tx(locale, "关闭"))}</button>`), `${locale}: focus lands on 关闭`);
    const recovered = text(outcome(locale, result("recover")));
    assert.ok(recovered.includes(tx(locale, "「{name}」已恢复，当前状态：{status}。", { name: "Fixture Active", status: tx(locale, "正常") })), `${locale}: ${recovered}`);
    assert.ok(recovered.includes(tx(locale, dialog.NOTE_TEXT.recover_not_journalled.recover)), `${locale}: the restore note`);
    assert.ok(!recovered.includes(tx(locale, "撤销的应用令牌")), `${locale}: nothing withdrawn`);
    const frozen = text(outcome(locale, result("recover", { workspace: ws({ status: "frozen" }) })));
    assert.ok(frozen.includes(tx(locale, dialog.FROZEN_RECOVERY_NOTE)), `${locale}: still frozen`);
    const renamed = text(outcome(locale, result("rename")));
    assert.ok(renamed.includes(tx(locale, "工作空间已重命名为「{name}」。", { name: "Fixture Renamed" })), `${locale}: ${renamed}`);
    assert.ok(renamed.includes(tx(locale, dialog.NOTE_TEXT.rename_not_journalled.rename)), `${locale}: the restore note`);
    // a restore note this page does not know is named by its code; a catalogued sentence is translated
    assert.ok(text(outcome(locale, result("rename", { restore_note: "fixture_unknown_note" }))).includes(tx(locale, dialog.UNKNOWN_NOTE, { code: "fixture_unknown_note" })), locale);
    assert.ok(text(outcome(locale, result("recover", { restore_note: "恢复只能在删除后七天内进行；期满后数据仍然保存，但不能再从控制台恢复。" })))
      .includes(tx(locale, "恢复只能在删除后七天内进行；期满后数据仍然保存，但不能再从控制台恢复。")), locale);
  }
  // the list's notice reads the same sentence
  inLocale("en", () => assert.equal(dialog.workspaceOutcomeText(result("rename")), tx("en", "工作空间已重命名为「{name}」。", { name: "Fixture Renamed" })));
});

test("every refusal of the contract reads as a sentence on the administration page, worded for a workspace in the dialog", () => {
  const codes = ["bad_action", "missing_reason", "invalid_field", "admin_confirmation_required", "no_workspace", "workspace_state_conflict",
    "workspace_recovery_expired", "workspace_personal", "workspace_legacy", "workspace_name_unchanged", "admin_preview_invalid",
    "admin_preview_expired", "admin_preview_stale", "idempotency_conflict", "journal_unavailable", "platform_permission_denied"];
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const code of codes) {
    for (const locale of LOCALES) {
      for (const word of [labels.errText, dialog.workspaceErrText]) {
        const worded = inLocale(locale, () => word(new client.ApiError(409, code, "")));
        assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
        if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
      }
    }
  }
  inLocale("en", () => {
    // the dialog names the workspace where the page-wide sentence names an organization or a field
    for (const code of Object.keys(dialog.WORKSPACE_REFUSALS)) {
      assert.equal(dialog.workspaceErrText(new client.ApiError(400, code, "")), tx("en", dialog.WORKSPACE_REFUSALS[code]), code);
    }
    assert.match(dialog.workspaceErrText(new client.ApiError(400, "bad_action", "")), /workspace/i);
    // a deletion whose journal entry failed is not said to be uncommitted: a restore may apply it (A44-J2)
    const journal = PY_WORKSPACES.match(/^JOURNAL_UNAVAILABLE = "([^"\n]*)"$/m)[1];
    assert.ok(ui.hasMessage(journal), journal);
    assert.equal(dialog.workspaceErrText(new client.ApiError(503, "journal_unavailable", journal)), tx("en", journal));
    for (const said of [tx("en", journal), dialog.workspaceErrText(new client.ApiError(503, "journal_unavailable", ""))]) {
      assert.doesNotMatch(said, /not committed/i, said);
      assert.match(said, /may still take effect/, said);
    }
    // a catalogued server sentence wins over the dialog's wording
    assert.equal(dialog.workspaceErrText(new client.ApiError(409, "admin_preview_stale", "工作空间或其成员已变化，请重新预览")),
      tx("en", "工作空间或其成员已变化，请重新预览"));
  });
});

test("the English render has no untranslated text", () => {
  const en = [tab("en", MANAGE), tab("en", VIEW), tab("en", MANAGE, DELETED_ROWS), tab("en", MANAGE, []),
    ...ACTIONS.map((action) => fullDialog("en", action, action === "recover" ? RECOVERABLE : ws())),
    ...ACTIONS.map((action) => impact("en", action)),
    impact("en", "recover", plan("recover", { transition: { from: "deleted", to: "frozen" }, impact: { ...IMPACT, usable_owners: 0 },
      notes: [...NOTES.recover, "recover_blocked_by_organization", "recover_without_usable_owner"] })),
    impact("en", "delete", plan("delete", { impact: { ...IMPACT, api_keys: null, usable_owners: 0 }, notes: ["fixture_unknown_note"] })),
    ...ACTIONS.map((action) => outcome("en", result(action)))]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split("\n").filter((l) => HAN.test(l)).join("\n").slice(0, 2000)}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("every sentence of the dialog and the workspace list is catalogued in English and Traditional Chinese, and this catalogue's wording is the one in effect", () => {
  assert.deepEqual(ui.validateMessages(messages.workspaceLifecycleMessages), []);
  for (const [source, entry] of Object.entries(messages.workspaceLifecycleMessages)) {
    for (const locale of ["en", "zh-Hant"]) assert.equal(tx(locale, source), entry[locale], `${locale}: ${source} is worded by another catalogue`);
  }
  const sentences = new Set();
  const literals = (src) => {
    src = src.split("\n").filter((line) => !/^\s*(\/\/|\*|\/\*\*)/.test(line)).join("\n");
    for (const m of src.matchAll(/"([^"\n]*[㐀-鿿][^"\n]*)"/g)) sentences.add(m[1]);
  };
  literals(readFileSync(new URL("../src/governance/WorkspaceLifecycleDialog.tsx", import.meta.url), "utf8"));
  const page = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  literals(page.slice(page.indexOf("const WORKSPACE_FILTER"), page.indexOf("type CatalogueItem")));
  const access = readFileSync(new URL("../src/governance/PlatformAccess.tsx", import.meta.url), "utf8");
  sentences.add(/"platform\.workspaces\.manage": "([^"]+)"/.exec(access)[1]);
  const codes = readFileSync(new URL("../src/governance/labels.ts", import.meta.url), "utf8");
  for (const code of ["workspace_state_conflict", "workspace_recovery_expired", "workspace_personal", "workspace_legacy", "workspace_name_unchanged"]) {
    sentences.add(new RegExp(`${code}: "([^"]+)"`).exec(codes)[1]);
  }
  assert.ok(sentences.size > 120, `expected the dialog's and the list's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(tx("en", s, { count: 2 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
  // the deleted state has its label wherever a workspace's status is shown
  for (const locale of ["en", "zh-Hant"]) assert.ok(inLocale(locale, () => labels.WORKSPACE_STATUS.deleted) && !HAN.test(inLocale("en", () => labels.WORKSPACE_STATUS.deleted)));
});

// ------------------------------------------------------------------------------------ review regressions (2026-10-13)
const sources = () => [...dialog.IMPACT_ROWS.flatMap(([, label, onDelete, onRecover]) => [label, onDelete, onRecover]),
  ...Object.values(dialog.NOTE_TEXT).flatMap((entry) => Object.values(entry)), ...Object.values(dialog.WORKSPACE_ACTION_WARNINGS),
  dialog.MEMBERS_WITHOUT_OWNER, dialog.MEMBERS_STILL_FROZEN, dialog.MEMBERS_BLOCKED_BY_ORGANIZATION, dialog.RECOVERED_BLOCKED_BY_ORGANIZATION,
  dialog.RECOVERY_EXPIRED_KEPT];

test("A44-WEB-1: a credential issued through an API approval is never promised back — it is counted and worded under the approvals; only the unbound keys and service accounts pause and return", () => {
  const meaning = (key) => dialog.IMPACT_ROWS.find(([k]) => k === key);
  // the counts the server sends: api_keys and service_accounts are the credentials no approval binds
  assert.match(meaning("api_keys")[1], /^不经批准签发的 API 访问密钥$/);
  assert.match(meaning("service_accounts")[1], /^不经批准签发的服务账户$/);
  assert.match(meaning("approvals")[2], /永久撤销.*通过它们签发的 API 访问密钥和服务账户从此停止工作.*不会重新生效/);
  assert.match(meaning("approvals")[3], /不会恢复.*通过它们签发的 API 访问密钥和服务账户仍然无效/);
  for (const key of ["api_keys", "service_accounts"]) {
    assert.match(meaning(key)[2], /暂停使用.*恢复后重新可用.*经 API 凭据批准签发的.*不在此列.*随批准永久停止/, key);
    assert.match(meaning(key)[3], /随恢复重新可用.*经 API 凭据批准签发的.*不在此列.*仍然无效/, key);
  }
  // no sentence says keys or service accounts come back without excluding the approval-issued ones
  for (const s of sources()) {
    if (/(密钥|服务账户).*重新可用|重新可用.*(密钥|服务账户)/.test(s)) assert.match(s, /经( API 凭据)?批准签发的/, s);
    const en = tx("en", s, { name: "x", status: "x" });
    if (/(keys|service accounts).*work again|work again.*(keys|service accounts)/i.test(en)) assert.match(en, /issued through an (API credential )?approval/i, en);
  }
  // rendered: the delete preview of a workspace with approvals, unbound keys and unbound service accounts
  for (const locale of LOCALES) {
    const said = text(impact(locale, "delete"));
    assert.ok(said.includes(`${tx(locale, "不经批准签发的 API 访问密钥")} ${IMPACT.api_keys} · ${tx(locale, meaning("api_keys")[2])}`), `${locale}\n${said}`);
    assert.ok(said.includes(`${tx(locale, "API 凭据批准")} ${IMPACT.approvals} · ${tx(locale, meaning("approvals")[2])}`), `${locale}\n${said}`);
    assert.ok(said.includes(tx(locale, dialog.NOTE_TEXT.credentials_paused.delete)) && said.includes(tx(locale, dialog.NOTE_TEXT.approvals_revoked_for_good.delete)), locale);
    const back = text(impact(locale, "recover"));
    assert.ok(back.includes(tx(locale, dialog.NOTE_TEXT.approvals_revoked_for_good.recover)), `${locale}: the recovery says the approval-issued credentials stay invalid\n${back}`);
  }
});

test("A44-WEB-2: a recovery's jobs and webhooks are not promised to carry on; what the deletion stopped or disabled stays so, and only the notes the server sends are shown", () => {
  const row = (key) => dialog.IMPACT_ROWS.find(([k]) => k === key);
  const [, , jobsOnDelete, jobsOnRecover] = row("running_jobs"), [, , hooksOnDelete, hooksOnRecover] = row("webhooks");
  assert.doesNotMatch(jobsOnRecover, /不受影响|照常|继续运行/);
  assert.match(jobsOnRecover, /删除期间已停止的任务保持停止.*需要有人重新运行/);
  assert.match(jobsOnRecover, /按提交者当前的授权核对.*授权已在删除时撤销的任务会停止/);
  assert.doesNotMatch(hooksOnRecover, /继续投递|不受影响/);
  assert.match(hooksOnRecover, /删除期间已被停用的订阅保持停用.*需要有人重新启用/);
  assert.match(hooksOnRecover, /按创建者当前的授权核对.*授权已在删除时撤销的会被停用/);
  assert.match(jobsOnDelete, /永久停止/);
  assert.match(hooksOnDelete, /Docs 的订阅.*业务应用的订阅.*恢复后仍保持停用.*重新启用/);
  // the server counts with a recovery what the deletion stopped and what stops because its authority
  // ended with the deletion (admin_workspaces._ended_by_deletion): both are said
  assert.match(dialog.NOTE_TEXT.jobs_halt_for_good.recover, /删除期间停止的 Docs 任务保持停止.*授权已随删除结束的.*下一次检查时停止.*不会随恢复继续.*需要有人重新运行/);
  assert.match(dialog.NOTE_TEXT.webhooks_disabled_for_good.recover, /删除期间被停用的 Webhook 订阅（Docs 和业务应用的）保持停用.*授权已随删除结束的订阅.*下一次投递时被停用.*重新启用/);
  assert.match(dialog.NOTE_TEXT.webhooks_disabled_for_good.delete, /Docs 的 Webhook 订阅.*业务应用的 Webhook 订阅/);
  // every code the server may now send with a recovery has a recovery wording of its own
  for (const code of ["approvals_revoked_for_good", "jobs_halt_for_good", "webhooks_disabled_for_good", "credentials_paused", "forms_links_paused",
    "org_templates_kept", "recover_not_journalled", "recover_blocked_by_organization", "recover_without_usable_owner", "data_kept"]) {
    assert.ok(dialog.NOTE_TEXT[code]?.recover, code);
  }
  const RECOVER_ONLY_IF_SENT = ["approvals_revoked_for_good", "jobs_halt_for_good", "webhooks_disabled_for_good", "credentials_paused", "forms_links_paused", "org_templates_kept"];
  for (const locale of LOCALES) {
    // the full set (fixture): each worded for a recovery, never the deletion's sentence
    const all = text(impact(locale, "recover"));
    for (const code of RECOVER_ONLY_IF_SENT) {
      assert.ok(all.includes(tx(locale, dialog.NOTE_TEXT[code].recover)), `${locale} ${code}\n${all}`);
      assert.ok(!all.includes(tx(locale, dialog.NOTE_TEXT[code].delete)), `${locale} ${code}: the deletion's wording`);
    }
    // a recovery the deletion left nothing stopped for: none of them is said
    const bare = text(impact(locale, "recover", plan("recover", { notes: ["data_kept", "recover_not_journalled"] })));
    for (const code of RECOVER_ONLY_IF_SENT) assert.ok(!bare.includes(tx(locale, dialog.NOTE_TEXT[code].recover)), `${locale} ${code}: not sent, not shown`);
    assert.ok(bare.includes(`${tx(locale, "排队或运行中的任务")} ${IMPACT.running_jobs} · ${tx(locale, jobsOnRecover)}`), `${locale}\n${bare}`);
    assert.ok(bare.includes(`${tx(locale, "Webhook 订阅")} ${IMPACT.webhooks} · ${tx(locale, hooksOnRecover)}`), `${locale}\n${bare}`);
  }
});

test("A44-WEB-3: nothing implies a purge after the seven days — the workspace stays deleted with its data stored and only cannot be recovered from the console", () => {
  const PURGE_ZH = /立即清除|会被清除|将被清除|自动删除数据|彻底删除|永久删除(数据|工作空间)/;
  const PURGE_EN = /\bpurg|\berase|removes no data immediately|deleted for good|permanently delet|clears? (the |its )?data (later|after)/i;
  for (const [source, entry] of Object.entries(messages.workspaceLifecycleMessages)) {
    assert.doesNotMatch(source, PURGE_ZH, source);
    assert.doesNotMatch(entry.en, PURGE_EN, entry.en);
  }
  // what is said about the window says the data stays
  const kept = [dialog.WORKSPACE_ACTION_WARNINGS.delete, dialog.NOTE_TEXT.recover_within_7_days.delete, dialog.NOTE_TEXT.recover_within_7_days.recover,
    dialog.RECOVERY_EXPIRED_KEPT, "持有工作空间管理权限的平台管理员可以重命名、冻结、删除或恢复工作空间：重命名、删除和恢复都要先预览影响，再写明理由并重新验证身份。删除后的工作空间对所有人不可访问，七天内可以恢复；期满后数据仍然保存，只是不能再从控制台恢复。",
    "已删除的工作空间不出现在工作空间切换器和其他列表中，任何人都不能打开；在恢复期限之前可以恢复，期限过后数据仍然保存，但不能再从控制台恢复。"];
  for (const s of kept) {
    assert.match(s, /数据仍然保存/, s);
    assert.ok(ui.hasMessage(s), s);
    assert.match(tx("en", s), /data stays stored|with its data stored/, s);
    assert.match(tx("zh-Hant", s), /資料仍然保存/, s);
  }
  assert.match(dialog.WORKSPACE_ACTION_WARNINGS.delete, /不会清除任何数据，之后也不会自动清除/);
  assert.match(dialog.NOTE_TEXT.data_kept.delete, /七天后也不会自动清除/);
  // the deleted filter says it where the expired rows are listed
  const src = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  assert.match(src, /status === "deleted" && <p className="muted small">\{tr\("已删除的工作空间[^"]*期限过后数据仍然保存，但不能再从控制台恢复。"\)\}<\/p>/);
  for (const locale of LOCALES) {
    const html = tab(locale, MANAGE, DELETED_ROWS);
    assert.ok(text(row(html, "Fixture Expired")).includes(tx(locale, dialog.RECOVERY_EXPIRED_KEPT)), locale);
    // the row's disabled recover still names why
    assert.deepEqual(dialog.workspaceLifecycleOffers(EXPIRED, T * 1000)[0].hint, dialog.RECOVERY_EXPIRED);
  }
});

test("A44-WEB-4: a recovery promises members no access where the state it returns to, the organization or a missing owner withholds it; the result names a blocking organization", () => {
  // what is said before anything is counted promises nothing
  assert.doesNotMatch(dialog.WORKSPACE_ACTION_WARNINGS.recover, /成员按原有成员关系重新访问/);
  assert.match(dialog.WORKSPACE_ACTION_WARNINGS.recover, /成员关系保留；成员能否重新访问取决于恢复后的状态、所属组织和各自的账户/);
  assert.doesNotMatch(dialog.NOTE_TEXT.recover_without_usable_owner.recover, /继续使用/);
  assert.doesNotMatch(dialog.NOTE_TEXT.data_kept.recover, /恢复后照常可用/);
  const membersBack = dialog.IMPACT_ROWS.find(([k]) => k === "members")[3];
  // the probe recover_frozen_render.mjs: back to frozen, no usable owner, as the server answers it
  const frozenOwnerless = plan("recover", { transition: { from: "deleted", to: "frozen" }, target: RECOVERABLE,
    impact: { ...IMPACT, usable_owners: 0 }, notes: ["data_kept", "recover_not_journalled", "recover_without_usable_owner"] });
  const ownerless = plan("recover", { impact: { ...IMPACT, usable_owners: 0 }, notes: ["data_kept", "recover_not_journalled", "recover_without_usable_owner"] });
  // the organization was frozen after the deletion: the row says so even before the note does
  const orgFrozen = plan("recover", { target: { ...RECOVERABLE, previous_status: "active", organization: { ...ORG, status: "frozen" } } });
  const orgGone = plan("recover", { target: { ...RECOVERABLE, previous_status: "active", organization: null } });
  for (const locale of LOCALES) {
    for (const [name, p, members] of [["frozen, ownerless", frozenOwnerless, dialog.MEMBERS_STILL_FROZEN], ["ownerless", ownerless, dialog.MEMBERS_WITHOUT_OWNER],
      ["organization frozen", orgFrozen, dialog.MEMBERS_BLOCKED_BY_ORGANIZATION], ["organization missing", orgGone, dialog.MEMBERS_BLOCKED_BY_ORGANIZATION]]) {
      const said = text(impact(locale, "recover", p));
      assert.ok(said.includes(`${tx(locale, "成员")} ${IMPACT.members} · ${tx(locale, members)}`), `${locale} ${name}\n${said}`);
      assert.ok(!said.includes(tx(locale, membersBack)), `${locale} ${name}: members are not promised access\n${said}`);
      assert.doesNotMatch(said, locale === "en" ? /can keep working|keep using/i : /继续使用|繼續使用/, `${locale} ${name}`);
    }
    assert.ok(text(impact(locale, "recover", ownerless)).includes(tx(locale, dialog.NO_OWNER_AFTER_RECOVERY)), locale);
    // the result, and the list's notice, name the organization that keeps members out
    for (const organization of [{ ...ORG, status: "frozen" }, { ...ORG, status: "deleted" }, null]) {
      const r = result("recover", { workspace: ws({ organization }) });
      const expected = tx(locale, dialog.RECOVERED_BLOCKED_BY_ORGANIZATION, { name: "Fixture Active", status: tx(locale, "正常") });
      assert.ok(text(outcome(locale, r)).includes(expected), `${locale} ${organization?.status ?? "missing"}`);
      assert.equal(inLocale(locale, () => dialog.workspaceOutcomeText(r)), expected);
    }
    // an active organization, or none at all (a personal workspace), adds nothing
    for (const w of [ws(), ws({ kind: "personal", organization_id: null, organization: null })]) {
      const blocked = tx(locale, dialog.RECOVERED_BLOCKED_BY_ORGANIZATION, { name: w.name, status: tx(locale, "正常") });
      assert.ok(!text(outcome(locale, result("recover", { workspace: w }))).includes(blocked), `${locale} ${w.kind}`);
      assert.equal(inLocale(locale, () => dialog.workspaceOutcomeText(result("recover", { workspace: w }))),
        tx(locale, "「{name}」已恢复，当前状态：{status}。", { name: w.name, status: tx(locale, "正常") }));
    }
  }
  assert.equal(dialog.organizationBlocks({ organization_id: null, organization: null }), false);
  assert.equal(dialog.organizationBlocks({ organization_id: "org_fx_one", organization: ORG }), false);
  assert.equal(dialog.organizationBlocks({ organization_id: "org_fx_one", organization: null }), true);
  assert.equal(dialog.organizationBlocks({ organization_id: "org_fx_one", organization: { ...ORG, status: "frozen" } }), true);
});

test("A44-WEB-5: the ownership panel below the workspace list re-reads after a lifecycle act or a freeze", async () => {
  const ownership = await server.ssrLoadModule("/src/governance/OwnershipRecovery.tsx");
  // the mount's own read is not repeated; any later revision is a re-read
  assert.equal(ownership.ownershipStale(0, 0), false);
  assert.equal(ownership.ownershipStale(0, 1), true);
  assert.equal(ownership.ownershipStale(3, 3), false);
  const panel = readFileSync(new URL("../src/governance/OwnershipRecovery.tsx", import.meta.url), "utf8");
  assert.match(panel, /const readAt = useRef\(revision\);\s*useEffect\(\(\) => \{\s*if \(!ownershipStale\(readAt\.current, revision\)\) return;\s*readAt\.current = revision;\s*reload\(\);\s*\}, \[revision, reload\]\);/);
  const page = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  const tabSrc = page.slice(page.indexOf("function WorkspacesTab"), page.indexOf("type CatalogueItem"));
  assert.match(tabSrc, /<OwnershipRecovery kind="workspace" held=\{held\} onDenied=\{onDenied\} onChanged=\{reload\} revision=\{ownership\} \/>/);
  const freezeSrc = tabSrc.slice(tabSrc.indexOf("{freeze && <ActionDialog"), tabSrc.indexOf("{lifecycle && <WorkspaceLifecycleDialog"));
  const lifecycleSrc = tabSrc.slice(tabSrc.indexOf("{lifecycle && <WorkspaceLifecycleDialog"), tabSrc.indexOf("</section>", tabSrc.indexOf("{lifecycle && <WorkspaceLifecycleDialog")));
  assert.match(freezeSrc.slice(freezeSrc.indexOf("onDone")), /setOwnership\(\(n\) => n \+ 1\)/, "after a freeze or an unfreeze");
  assert.match(lifecycleSrc.slice(lifecycleSrc.indexOf("onDone")), /setOwnership\(\(n\) => n \+ 1\)/, "after a rename, a deletion or a recovery");
  // the organization list does the same for its own panel
  const governance = readFileSync(new URL("../src/governance/GovernanceTabs.tsx", import.meta.url), "utf8");
  assert.match(governance, /onDone=\{\(\) => \{ setAction\(null\); load\.reload\(\); setOwnership\(\(n\) => n \+ 1\);/);
  assert.match(governance, /<OwnershipRecovery kind="organization" [^>]*revision=\{ownership\} \/>/);
  // the panel still renders with a revision (and without one)
  for (const revision of [undefined, 2]) {
    globalThis.__fixtures = { "/api/admin/ownership/orphans": { data: { as_of: T, limit: 50, workspaces: { total: 0, items: [] }, organizations: { total: 0, items: [] } } } };
    const html = render("en", React.createElement(ownership.OwnershipRecovery, { kind: "workspace", held: MANAGE_ALL, onDenied() {}, onChanged() {}, revision }));
    assert.ok(html.includes(tx("en", "没有有效拥有者的工作空间")), html);
  }
});
const MANAGE_ALL = new Set(["platform.workspaces.view", "platform.workspaces.manage", "platform.orgs.view"]);

test("A44-WEB-6: after an act focus waits for the list to re-read past the page it was taken on, then goes to the outcome if the row took it away", () => {
  const A = { items: ["fixture row"] }, B = { items: [] };
  const due = (wait, page) => dialog.noticeFocusDue(wait, { loading: false, data: A, err: null, ...page });
  assert.equal(due(null, {}), false, "nothing acted on");
  assert.equal(due({ stale: A }, {}), false, "the list has not re-read yet: the row and its menu are still there");
  assert.equal(due({ stale: A }, { loading: true }), false);
  assert.equal(due({ stale: A }, { loading: true, data: B }), false, "still loading");
  assert.equal(due({ stale: A }, { data: B }), true, "re-read");
  assert.equal(due({ stale: A }, { err: "fixture error" }), true, "the re-read failed: the rows are hidden");
  const body = { fixture: "body" }, button = { fixture: "button" };
  assert.equal(dialog.focusLost({ activeElement: body, body }), true);
  assert.equal(dialog.focusLost({ activeElement: null, body }), true);
  assert.equal(dialog.focusLost({ activeElement: button, body }), false);
  // the wiring: a freeze closes its dialog with the answer, so it waits from there; the lifecycle's
  // result stays open, so it waits from its close, against the page the act was taken on
  const page = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  const tabSrc = page.slice(page.indexOf("function WorkspacesTab"), page.indexOf("type CatalogueItem"));
  assert.match(tabSrc, /if \(!noticeFocusDue\(focusWait\.current, \{ loading, data, err \}\)\) return;\s*focusWait\.current = null;\s*if \(focusLost\(document\)\) noticeRef\.current\?\.focus\(\);\s*\}, \[focusTick, loading, data, err\]\);/);
  const freezeSrc = tabSrc.slice(tabSrc.indexOf("{freeze && <ActionDialog"), tabSrc.indexOf("{lifecycle && <WorkspaceLifecycleDialog"));
  assert.match(freezeSrc, /onDone=\{\(r\) => \{[\s\S]*settleFocus\(\{ stale: page\.current \}\);/);
  const lifecycleSrc = tabSrc.slice(tabSrc.indexOf("{lifecycle && <WorkspaceLifecycleDialog"));
  assert.match(lifecycleSrc, /onClose=\{\(\) => \{ setLifecycle\(null\); settleFocus\(actedOn\.current\); actedOn\.current = null; \}\}/);
  assert.match(lifecycleSrc, /onDone=\{\(r\) => \{\s*actedOn\.current = \{ stale: page\.current \};/);
  // the outcome it goes to is a focusable live region on the page from the first render
  for (const locale of LOCALES) assert.match(tab(locale, MANAGE), /<div role="status" aria-live="polite"><\/div>/);
  assert.match(tabSrc, /<div role="status" aria-live="polite">\{notice && <p className="notice" ref=\{noticeRef\} tabIndex=\{-1\}>/);
});
