/** A35: the platform-scope organization lifecycle on the administration page — what the preview
 *  dialog (governance/OrganizationLifecycleDialog.tsx) says about a freeze, a deletion, an
 *  unfreeze and a recovery, and which controls the organizations tab offers by the caller's
 *  platform permission rather than by a membership role.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data and the dialog's preview rendered from
 *  a fixture plan: no API, no user data. Run from the package: `npm test`. */
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
// A34: the tab keeps the open organization's detail in the address, so it renders inside a router.
const { MemoryRouter } = require("react-router-dom");

// Explicit browser-environment fixture, as in admin-legacy.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./data` for the tab and the ownership panel it embeds: answers
 *  each path from `globalThis.__fixtures`. */
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
const DATA_IMPORTERS = ["/src/governance/GovernanceTabs.tsx", "/src/governance/OwnershipRecovery.tsx"];

let server, ui, client, tabs, dialog, messages, labels;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "organizations-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./data" && DATA_IMPORTERS.some((p) => importer?.endsWith(p))) return "\0organizations-fixture-data"; },
      load(id) { if (id === "\0organizations-fixture-data") return FIXTURE_LOADER; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  dialog = await server.ssrLoadModule("/src/governance/OrganizationLifecycleDialog.tsx");
  messages = await server.ssrLoadModule("/src/governance/organization-lifecycle.messages.ts");
  tabs = await server.ssrLoadModule("/src/governance/GovernanceTabs.tsx");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// FIXTURE: the server's preview (POST /api/admin/organizations/{id}/preview) and list rows.
const T = 1_790_000_000;
const FAR = 4_102_444_800; // 2100-01-01: a recovery window still open whenever this runs
const COUNTS = { members: 4, workspaces: 2, sessions: 3, tokens: 1, invitations: 2, approvals: 1, running_jobs: 1 };
const target = (over = {}) => ({ id: "org_fixture", name: "Fixture organization", status: "active", deleted_at: null, recover_until: null, previous_status: null, version: 3, ...over });
const RESULT = { freeze: "frozen", unfreeze: "active", delete: "deleted", recover: "frozen" };
const plan = (action, over = {}) => ({ action, scope: "platform", risk: "high", target: target(), result_status: RESULT[action],
  impact: COUNTS, expires_at: T + 300, preview_token: "fixture-signed-preview", ...over });
const DELETED = target({ status: "deleted", deleted_at: T, recover_until: T + 7 * 86400, previous_status: "frozen" });
const ORG = { id: "org_fixture", name: "Fixture organization", status: "active", role: "member", members: 4, workspaces: 2, recover_until: null };
const ROWS = [ORG,
  { ...ORG, id: "org_frozen", name: "Frozen fixture", status: "frozen", role: null },
  { ...ORG, id: "org_expired", name: "Expired fixture", status: "deleted", role: null, recover_until: T },
  { ...ORG, id: "org_recoverable", name: "Recoverable fixture", status: "deleted", role: null, recover_until: FAR }];
const VIEW = new Set(["platform.orgs.view"]);
const MANAGE = new Set(["platform.orgs.view", "platform.orgs.manage"]);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /Fixture organization|Frozen fixture|Expired fixture|Recoverable fixture|org_[a-z_]+/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
// The text a reader sees: tags break words and runs of HTML white space (space, tab, LF, FF, CR) fold into one
// space, as a browser lays them out. A thin or no-break space a formatter emits is part of the text and stays:
// ICU 78 (Node 22) puts U+2009 between the date and the time in zh-Hant, ICU 76 (Node 23) a plain space, and the
// expected dates come from the same ui.fmtTime, so they match under either.
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/[ \t\n\f\r]+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const impact = (locale, action, p = plan(action)) => render(locale, React.createElement(dialog.OrganizationLifecycleImpact, { action, plan: p }));
const fullDialog = (locale, action, org = ORG) => render(locale, React.createElement(dialog.OrganizationLifecycleDialog, { org, action, onClose() {}, onDone() {} }));
function tab(locale, held, items = ROWS) {
  globalThis.__fixtures = {
    "/api/admin/organizations": { items },
    "/api/admin/ownership/orphans": { data: { as_of: T, limit: 50, workspaces: { total: 0, items: [] }, organizations: { total: 0, items: [] } } },
  };
  return render(locale, React.createElement(MemoryRouter, { initialEntries: ["/admin?tab=organizations"] },
    React.createElement(tabs.OrganizationsTab, { onDenied() {}, held })));
}
const row = (html, id) => html.split("<tr").find((part) => part.includes(id)).split("</tr>")[0];
const label = (locale, action) => tx(locale, dialog.LIFECYCLE_ACTION_LABELS[action]);

test("a freeze and a deletion show every count with its meaning, the warning and the confirm label, in every language", () => {
  for (const locale of LOCALES) {
    for (const action of ["freeze", "delete"]) {
      const rows = text(impact(locale, action));
      assert.ok(rows.includes(tx(locale, "状态：{before} → {after}", { before: tx(locale, "正常"), after: tx(locale, action === "freeze" ? "已冻结" : "已删除") })), `${locale} ${action}: ${rows}`);
      assert.ok(rows.includes("Fixture organization") && rows.includes("org_fixture"), `${locale} ${action}: the target's name and id`);
      for (const [key, name, stop] of dialog.IMPACT_ROWS) {
        assert.ok(rows.includes(tx(locale, name)), `${locale} ${action} ${key}: the label\n${rows}`);
        assert.ok(rows.includes(`${COUNTS[key]} · ${tx(locale, stop)}`), `${locale} ${action} ${key}: the count and what it means\n${rows}`);
      }
      const html = fullDialog(locale, action);
      assert.ok(html.includes('role="alertdialog"'), `${locale} ${action}: a confirmation dialog`);
      assert.ok(html.includes(tx(locale, dialog.LIFECYCLE_WARNINGS[action])), `${locale} ${action}: the warning\n${html}`);
      // G2 (B3): what the action does to the organization's Synwevo Message conversations
      assert.ok(html.includes(tx(locale, dialog.LIFECYCLE_MESSAGES_NOTES[action])), `${locale} ${action}: the Message note\n${html}`);
      assert.ok(html.includes(tx(locale, dialog.PLATFORM_NOTE)), `${locale} ${action}: no membership or content access for the administrator`);
      assert.match(html, new RegExp(`<button type="submit"[^>]*class="btn danger"[^>]*disabled=""[^>]*>${esc(label(locale, action))}</button>`),
        `${locale} ${action}: the confirm label, disabled until the preview is loaded and checked\n${html}`);
      assert.ok(html.includes(tx(locale, "正在核对组织与操作影响…")), `${locale} ${action}: the preview is being loaded`);
    }
  }
});

test("an unfreeze and a recovery revoke nothing, and a deleted organization shows its window", () => {
  for (const locale of LOCALES) {
    // Deleted while frozen, it comes back frozen: members and workspaces stay closed until an unfreeze.
    const rows = text(impact(locale, "recover", plan("recover", { target: DELETED })));
    for (const [key, , , restore] of dialog.IMPACT_ROWS) {
      const expected = dialog.FROZEN_AFTER_RECOVERY[key] ?? restore;
      assert.ok(rows.includes(`${COUNTS[key]} · ${tx(locale, expected)}`), `${locale} ${key}: ${rows}`);
    }
    assert.ok(!rows.includes(tx(locale, dialog.IMPACT_ROWS[0][3])), `${locale}: no promise that members regain access`);
    assert.ok(rows.includes(tx(locale, dialog.FROZEN_RECOVERY_NOTE)), `${locale}: the still-frozen note`);
    // Deleted while active, it comes back active: every count restores, and no still-frozen note.
    const active = text(impact(locale, "recover", plan("recover", { target: { ...DELETED, previous_status: "active" }, result_status: "active" })));
    for (const [key, , , restore] of dialog.IMPACT_ROWS) assert.ok(active.includes(`${COUNTS[key]} · ${tx(locale, restore)}`), `${locale} ${key}: ${active}`);
    assert.ok(!active.includes(tx(locale, dialog.FROZEN_RECOVERY_NOTE)), `${locale}: no still-frozen note when it comes back active`);
    assert.ok(rows.includes(tx(locale, "状态：{before} → {after}", { before: tx(locale, "已删除"), after: tx(locale, "已冻结") })), `${locale}: back to the status before deletion`);
    assert.ok(rows.includes(tx(locale, "恢复期限：{time}", { time: inLocale(locale, () => ui.fmtTime(DELETED.recover_until)) })), `${locale}: the window\n${rows}`);
    assert.ok(rows.includes(tx(locale, "删除前状态：{status}", { status: tx(locale, "已冻结") })), `${locale}: the previous status`);
    assert.ok(!text(impact(locale, "freeze")).includes(tx(locale, "删除时间：{time}", { time: "" }).trim()), `${locale}: no deletion facts for an active organization`);
    const html = fullDialog(locale, "unfreeze", { ...ORG, status: "frozen" });
    assert.ok(html.includes(tx(locale, dialog.LIFECYCLE_WARNINGS.unfreeze)), `${locale}: the unfreeze warning`);
    assert.match(html, new RegExp(`<button type="submit"[^>]*class="btn primary"[^>]*>${esc(label(locale, "unfreeze"))}</button>`), `${locale}: unfreeze is not a danger action`);
    assert.match(fullDialog(locale, "recover", { ...ORG, status: "deleted" }), new RegExp(`<button type="submit"[^>]*class="btn primary"[^>]*>${esc(label(locale, "recover"))}</button>`));
  }
});

test("the lifecycle buttons follow platform.orgs.manage, and a recovery past its window is disabled with the note", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, MANAGE);
    const active = row(html, "org_fixture");
    assert.ok(active.includes(`>${label(locale, "freeze")}</button>`) && active.includes(`>${label(locale, "delete")}</button>`), `${locale}: ${active}`);
    assert.ok(!active.includes(`>${label(locale, "unfreeze")}</button>`) && !active.includes(`>${label(locale, "recover")}</button>`), `${locale}: only the transitions from active`);
    assert.ok(active.includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: the member keeps the settings link`);
    const frozen = row(html, "org_frozen");
    assert.ok(frozen.includes(`>${label(locale, "unfreeze")}</button>`) && frozen.includes(`>${label(locale, "delete")}</button>`) && !frozen.includes(`>${label(locale, "freeze")}</button>`), `${locale}: ${frozen}`);
    assert.ok(!frozen.includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: no settings link without a membership`);
    const expired = row(html, "org_expired");
    assert.match(expired, new RegExp(`<button type="button" class="btn sm" disabled=""[^>]*>${esc(label(locale, "recover"))}</button>`), `${locale}: ${expired}`);
    assert.ok(expired.includes(tx(locale, "恢复期限已过，不能再恢复。")), `${locale}: the note`);
    const open = row(html, "org_recoverable");
    assert.match(open, new RegExp(`<button type="button" class="btn sm"[^>]*>${esc(label(locale, "recover"))}</button>`), `${locale}: ${open}`);
    assert.doesNotMatch(open, /disabled=""/, `${locale}: a recovery inside its window is offered`);
    assert.ok(!open.includes(tx(locale, "恢复期限已过，不能再恢复。")));
    assert.ok(!html.includes(tx(locale, "你的平台角色可以查看组织，但不能在管理后台执行组织的生命周期操作。")), `${locale}: no hint for a role that may act`);
    assert.ok(text(html).includes(tx(locale, "平台管理员可查看组织元数据与数量。持有组织管理权限的平台管理员可以在这里冻结、解冻、删除或恢复任何组织：先预览影响，再写明理由并重新验证身份；这不会授予成员身份或业务文档访问权。")), `${locale}: the explanation`);
  }
});

test("without platform.orgs.manage no lifecycle control renders and the hint says so", () => {
  for (const locale of LOCALES) {
    const html = tab(locale, VIEW);
    for (const action of Object.keys(dialog.LIFECYCLE_ACTION_LABELS)) assert.ok(!html.includes(`>${label(locale, action)}</button>`), `${locale}: ${action} offered without the permission\n${html}`);
    assert.ok(!html.includes(tx(locale, "恢复期限已过，不能再恢复。")), `${locale}: no note for a control that is not offered`);
    assert.ok(html.includes(tx(locale, "你的平台角色可以查看组织，但不能在管理后台执行组织的生命周期操作。")), `${locale}: the hint`);
    assert.ok(row(html, "org_fixture").includes(`>${tx(locale, "组织设置")}</a>`), `${locale}: the member's settings link stays`);
  }
});

test("the English render has no untranslated text", () => {
  const en = [tab("en", MANAGE), tab("en", VIEW), fullDialog("en", "freeze"), fullDialog("en", "delete"), fullDialog("en", "unfreeze", { ...ORG, status: "frozen" }),
    fullDialog("en", "recover", { ...ORG, status: "deleted" }), impact("en", "freeze"), impact("en", "delete"), impact("en", "unfreeze"), impact("en", "recover", plan("recover", { target: DELETED }))]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("the refusals of the preview and the transition read as sentences on the administration page", () => {
  const codes = ["bad_action", "organization_state_conflict", "organization_recovery_expired", "admin_confirmation_required",
    "admin_preview_invalid", "admin_preview_expired", "admin_preview_stale", "idempotency_conflict", "no_organization", "missing_reason"];
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const code of codes) {
    for (const locale of LOCALES) {
      const worded = inLocale(locale, () => labels.errText(new client.ApiError(409, code, "")));
      assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
    }
  }
});

test("every sentence of the dialog and of the tab's lifecycle controls is catalogued in English and Traditional Chinese", () => {
  assert.deepEqual(ui.validateMessages(messages.organizationLifecycleMessages), []);
  const src = readFileSync(new URL("../src/governance/OrganizationLifecycleDialog.tsx", import.meta.url), "utf8");
  const sentences = new Set([...src.matchAll(/\btr\("([^"]+)"/g)].map((m) => m[1]));
  for (const map of [dialog.LIFECYCLE_ACTION_LABELS, dialog.ORGANIZATION_STATUS_LABELS, dialog.LIFECYCLE_WARNINGS, dialog.LIFECYCLE_MESSAGES_NOTES]) for (const s of Object.values(map)) sentences.add(s);
  for (const [, name, stop, restore] of dialog.IMPACT_ROWS) { sentences.add(name); sentences.add(stop); sentences.add(restore); }
  sentences.add(dialog.PLATFORM_NOTE);
  const tabSrc = readFileSync(new URL("../src/governance/GovernanceTabs.tsx", import.meta.url), "utf8");
  const orgTab = tabSrc.slice(tabSrc.indexOf("export function OrganizationsTab"), tabSrc.indexOf("/** Admin coverage G19"));
  for (const m of orgTab.matchAll(/\bt\("([^"]+)"/g)) sentences.add(m[1]);
  for (const s of ["组织", "状态", "成员", "工作空间", "操作", "组织状态已更新"]) sentences.add(s);
  assert.ok(sentences.size > 40, `expected the dialog's and the tab's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(ui.translate("en", s, { count: 2 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
