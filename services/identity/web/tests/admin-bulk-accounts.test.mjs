/** A18: bulk suspension and bulk session revocation on the platform account list — the selection
 *  column and bar (Admin.tsx UsersTab) by the caller's platform permissions, the preview dialog
 *  (governance/BulkAccountDialog.tsx) with every selected account and its eligibility, the
 *  confirmation that cannot be sent with nothing eligible, and the item-by-item result that offers
 *  a new preview of the failed accounts.
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with
 *  the administration data loader replaced by fixture data and the preview and the batch result
 *  given as fixtures: no API, no user data. Run from the package: `npm test`. */
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

// Explicit browser-environment fixture, as in admin-legacy.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

/** Fixture loader in place of `./governance/data` for Admin.tsx: the account list answers the rows
 *  in `globalThis.__fixtures`. */
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

let server, ui, bulk, messages, labels, results, admin;
before(async () => {
  server = await testServer({
    root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{
      name: "bulk-accounts-fixture", enforce: "pre",
      resolveId(id, importer) { if (id === "./governance/data" && importer?.endsWith("/src/Admin.tsx")) return "\0bulk-accounts-fixture-data"; },
      load(id) { if (id === "\0bulk-accounts-fixture-data") return FIXTURE_LOADER; },
      // the account list, reachable for rendering on its own
      transform(code, id) { if (id.endsWith("/src/Admin.tsx")) return code + "\nexport { UsersTab };\n"; },
    }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  bulk = await server.ssrLoadModule("/src/governance/BulkAccountDialog.tsx");
  messages = await server.ssrLoadModule("/src/governance/bulk-accounts.messages.ts");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  results = await server.ssrLoadModule("/src/governance/BulkResult.tsx");
  admin = await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// FIXTURE: the server's preview (POST /api/admin/users/bulk-preview) for a mixed selection, and
// the batch result (POST /api/admin/users/bulk). Synthetic addresses on example.test.
const T = 1_790_000_000;
const item = (id, email, status, code, sessions = 0, tokens = 0) => ({ id, email, status, eligible: code === null, code, sessions, tokens });
const ITEMS = [
  item("u_fx_one", "one-fixture@example.test", "active", null, 2, 1),
  item("u_fx_two", "two-fixture@example.test", "pending", null, 0, 3),
  item("u_fx_admin", "platform-admin-fixture@example.test", "active", "demote_first", 1, 0),
  item("u_fx_owner", "sole-owner-fixture@example.test", "active", "ownership_required", 1, 0),
  item("u_fx_org", "org-owner-fixture@example.test", "active", "organization_ownership_required"),
  item("u_fx_self", "self-fixture@example.test", "active", "self_suspend", 1, 0),
  item("u_fx_state", "suspended-fixture@example.test", "suspended", "account_state_conflict"),
  item("u_fx_higher", "higher-fixture@example.test", "active", "target_outranks_actor", 4, 2),
  item("u_fx_last", "last-super-fixture@example.test", "active", "last_super_admin", 1, 0),
  item("u_fx_app", "app-fixture@example.test", "active", "application_identity"),
  item("u_fx_missing", null, null, "no_user"),
];
const plan = (over = {}) => ({ operation: "suspend", scope: "platform", risk: "high", expires_at: T + 300, preview_token: "fixture-signed-preview",
  total: ITEMS.length, eligible: 2, impact: { sessions: 2, tokens: 4 }, items: ITEMS, ...over });
const NONE_ELIGIBLE = plan({ eligible: 0, impact: { sessions: 0, tokens: 0 }, items: ITEMS.filter((i) => !i.eligible), total: ITEMS.length - 2 });
/** The worded refusal each code reads as in the preview (governance/labels.ts `errors`). */
const REFUSAL = {
  demote_first: "请先撤销该账户的全部平台权限",
  ownership_required: "该账户是共享工作空间唯一可用的拥有者或管理者，需先为该空间另设拥有者或管理者。",
  organization_ownership_required: "该账户是组织唯一可用的拥有者或管理者，需先为该组织另设拥有者或管理者。",
  self_suspend: "不能停用自己的账户",
  account_state_conflict: "账户状态已变化，请刷新后重试。",
  target_outranks_actor: "该账户持有你没有的平台权限",
  last_super_admin: "必须保留至少一位可用的最高管理员。",
  application_identity: "请通过应用身份管理操作该账户。",
  no_user: "找不到该账户。",
};
const STATUS = { active: "正常", pending: "待激活", suspended: "已停用" };
const RESPONSE = { ok: false, operation_id: "ao_fixture", operation: "suspend", succeeded: 2, failed: 3, results: [
  { id: "u_fx_one", ok: true, code: "ok", message: "账户已停用", sessions_revoked: 2, tokens_revoked: 1 },
  { id: "u_fx_two", ok: true, code: "already_applied", message: "这一项已在本批次此前的尝试中完成", sessions_revoked: 0, tokens_revoked: 3 },
  { id: "u_fx_admin", ok: false, code: "demote_first", message: "请先撤销该账户的全部平台权限，再停用它" },
  { id: "u_fx_state", ok: false, code: "admin_preview_stale", message: "账户或访问权限已变化，请重新预览" },
  { id: "u_fx_missing", ok: false, code: "no_user", message: "找不到该账户" },
] };
const NAMES = Object.fromEntries(ITEMS.map((i) => [i.id, i.email || i.id]));
const ROWS = [
  { id: "u_fx_one", email: "one-fixture@example.test", display_name: "Fixture One", status: "active", platform_role: "none", platform_roles: [], created_at: T, workspaces: 1, sessions: 2 },
  { id: "u_fx_admin", email: "platform-admin-fixture@example.test", display_name: null, status: "active", platform_role: "support", platform_roles: ["support"], created_at: T, workspaces: 0, sessions: 1 },
];

const VIEW = new Set(["platform.users.view"]);
const MANAGE = new Set(["platform.users.view", "platform.users.manage"]);
const SUPPORT = new Set(["platform.users.view", "platform.users.support"]);
const BOTH = new Set(["platform.users.view", "platform.users.manage", "platform.users.support"]);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿]/;
const FIXTURE_TEXT = /[a-z-]+-fixture@example\.test|Fixture One|u_fx_[a-z]+|ao_fixture/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const tx = (locale, source, params) => ui.translate(locale, source, params);
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const bar = (locale, held, count = 3, full = false) => render(locale, React.createElement(bulk.BulkAccountsBar, { held, count, full, onStart() {}, onClear() {} }));
const preview = (locale, p = plan(), operation = "suspend", selfId) => render(locale, React.createElement(bulk.BulkAccountPreview, { operation, plan: p, selfId }));
const confirm = (locale, p, { operation = "suspend", invalid = false, error = "" } = {}) => render(locale, React.createElement(bulk.BulkAccountConfirm, {
  operation, count: ITEMS.length, plan: p, error, invalid, busy: false, onPreviewAgain() {}, run: async () => RESPONSE, onClose() {}, onDone() {} }));
const result = (locale, response = RESPONSE, operation = "suspend") => render(locale, React.createElement(bulk.BulkAccountResult, {
  operation, response, labelOf: (id) => NAMES[id] || id, onClose() {}, onRetryFailed() {} }));
function tab(locale, held) {
  globalThis.__fixtures = { "/api/admin/users": { items: ROWS } };
  return render(locale, React.createElement(admin.UsersTab, { onDenied() {}, onAccess() {}, revision: 0, held }));
}
const label = (locale, op) => tx(locale, bulk.BULK_OPERATION_LABELS[op]);
const button = (html, name) => new RegExp(`<button[^>]*>${esc(name)}</button>`).test(html);
const submit = (html) => html.match(/<button type="submit"[^>]*>[^<]*<\/button>/)?.[0] ?? "";

test("the selection bar offers each bulk operation only with its permission, and says so otherwise", () => {
  for (const locale of LOCALES) {
    const cases = [[MANAGE, ["suspend"], ["revoke_sessions"]], [SUPPORT, ["revoke_sessions"], ["suspend"]], [BOTH, ["suspend", "revoke_sessions"], []], [VIEW, [], ["suspend", "revoke_sessions"]]];
    for (const [held, offered, missing] of cases) {
      const html = bar(locale, held);
      const who = `${locale} ${[...held].join(",")}`;
      assert.ok(html.includes(tx(locale, "已选 {count} 个账户", { count: 3 })), `${who}: the count\n${html}`);
      assert.ok(button(html, tx(locale, "清除选择")), `${who}: the way out`);
      for (const op of offered) assert.ok(button(html, label(locale, op)), `${who}: ${op} offered\n${html}`);
      for (const op of missing) assert.ok(!button(html, label(locale, op)), `${who}: ${op} not offered without its permission\n${html}`);
      if (!offered.length) {
        assert.ok(html.includes(tx(locale, "你的平台角色不包含这项操作")), `${who}: the usual hint`);
      } else {
        for (const op of missing) assert.ok(html.includes(tx(locale, bulk.BULK_OPERATION_MISSING[op])), `${who}: the hint for ${op}`);
        assert.ok(!html.includes(tx(locale, "你的平台角色不包含这项操作")), `${who}: no blanket hint while something is offered`);
      }
      for (const op of offered) assert.ok(!html.includes(tx(locale, bulk.BULK_OPERATION_MISSING[op])), `${who}: no hint for ${op}`);
    }
    assert.ok(!bar(locale, BOTH).includes(tx(locale, "一次最多选择 {limit} 个账户；已达到上限。", { limit: 100 })), `${locale}: no cap note below the cap`);
    assert.ok(bar(locale, BOTH, 100, true).includes(tx(locale, "一次最多选择 {limit} 个账户；已达到上限。", { limit: 100 })), `${locale}: the cap note`);
  }
  assert.ok(bar("en", BOTH, 1).includes("1 account selected") && bar("en", BOTH, 2).includes("2 accounts selected"), "English counts agree in number");
  assert.equal(bulk.BULK_LIMIT, 100, "one batch is the server's limit");
});

test("the account list has a keyboard-reachable checkbox per row and for the page, only for a role that can act", () => {
  for (const locale of LOCALES) {
    for (const held of [MANAGE, SUPPORT, BOTH]) {
      const html = tab(locale, held);
      assert.match(html, /<table class="gov admin-users-table selectable"/, `${locale}: ${html}`);
      for (const row of ROWS) {
        assert.match(html, new RegExp(`<input type="checkbox" data-pick="${row.id}"[^>]*aria-label="${esc(tx(locale, "选择 {p0}", { p0: row.email }))}"`), `${locale}: ${row.id}`);
      }
      assert.match(html, new RegExp(`<th scope="col" class="pick"><label class="pick-page"><input type="checkbox"[^>]*/><span class="pick-page-text">${esc(tx(locale, "选择本页的账户"))}</span></label></th>`), `${locale}: select this page\n${html}`);
      assert.doesNotMatch(html, /tabindex="-1"[^>]*data-pick|data-pick[^>]*tabindex="-1"/, "no checkbox is taken out of the tab order");
      assert.ok(!html.includes('class="acct-bulk"'), `${locale}: no bar before anything is chosen`);
    }
    const view = tab(locale, VIEW);
    assert.ok(!view.includes("data-pick") && !view.includes(tx(locale, "选择本页的账户")), `${locale}: no selection for a role that cannot act\n${view}`);
    assert.match(view, /<table class="gov admin-users-table"/);
  }
});

test("the preview lists every selected account with its state and its eligibility, in every language", () => {
  for (const locale of LOCALES) {
    const html = preview(locale);
    const rows = text(html);
    assert.ok(rows.includes(tx(locale, "{eligible} / {total} 个账户可以执行", { eligible: 2, total: ITEMS.length })), `${locale}: the totals\n${rows}`);
    assert.ok(rows.includes(tx(locale, "符合条件的账户合计：{sessions} 个会话、{tokens} 个应用令牌将被撤销。", { sessions: 2, tokens: 4 })), `${locale}: sessions and tokens`);
    assert.ok(rows.includes(tx(locale, "{count} 个账户不符合条件，不会被处理；原因见下方列表。", { count: ITEMS.length - 2 })), `${locale}: how many are left out`);
    assert.ok(rows.includes(tx(locale, "正在运行的任务和连接会在下一次权限检查时停止；已经生成的数据保留。")), `${locale}: what a suspension stops`);
    const lines = html.split("<li").slice(1).filter((part) => part.includes('class="who"'));
    assert.equal(lines.length, ITEMS.length, `${locale}: one line per selected account`);
    ITEMS.forEach((i, n) => {
      const line = text("<li" + lines[n]);
      assert.ok(line.includes(i.email || i.id), `${locale}: ${i.id} named, in the order sent\n${line}`);
      const status = i.status ? tx(locale, STATUS[i.status]) : "—";
      assert.ok(line.includes(status), `${locale} ${i.id}: its status\n${line}`);
      const expected = i.eligible ? tx(locale, "可以执行：{sessions} 个会话，{tokens} 个应用令牌", { sessions: i.sessions, tokens: i.tokens })
        : tx(locale, "不处理：{reason}", { reason: tx(locale, REFUSAL[i.code]) });
      assert.ok(line.includes(expected), `${locale} ${i.id}: ${expected}\n${line}`);
      assert.ok(!line.includes(i.code ?? "\0"), `${locale} ${i.id}: no bare code`);
    });
    assert.match(html, /<ul class="bulkres bulk-preview" tabindex="0" aria-label="[^"]+"/, `${locale}: the list scrolls on its own and from the keyboard`);
    // revoke_sessions: no suspension sentence; a warning when the caller's own account would be signed out
    const revoke = text(preview(locale, plan({ operation: "revoke_sessions" }), "revoke_sessions", "u_fx_one"));
    assert.ok(!revoke.includes(tx(locale, "正在运行的任务和连接会在下一次权限检查时停止；已经生成的数据保留。")), `${locale}: ${revoke}`);
    assert.ok(revoke.includes(tx(locale, "所选账户包含你自己的账户，完成后可能需要重新登录或离开平台管理页。")), `${locale}: own account warning`);
    assert.ok(!text(preview(locale, plan(), "suspend", "u_fx_one")).includes(tx(locale, "所选账户包含你自己的账户，完成后可能需要重新登录或离开平台管理页。")));
  }
});

test("the confirmation cannot be sent before the preview, with nothing eligible, or with a refused preview", () => {
  for (const locale of LOCALES) {
    for (const op of ["suspend", "revoke_sessions"]) {
      const name = label(locale, op);
      const loaded = confirm(locale, plan({ operation: op }), { operation: op });
      assert.ok(loaded.includes('role="alertdialog"'), `${locale} ${op}: a confirmation dialog`);
      assert.match(submit(loaded), new RegExp(`class="btn danger"[^>]*>${esc(name)}</button>$`), `${locale} ${op}: the confirm label\n${loaded}`);
      assert.doesNotMatch(submit(loaded), /disabled=""/, `${locale} ${op}: confirmable with eligible accounts`);
      assert.ok(loaded.includes(tx(locale, bulk.BULK_OPERATION_IMPACT[op])) && loaded.includes(tx(locale, bulk.BULK_NOTE)), `${locale} ${op}: what it does`);
      assert.ok(loaded.includes(tx(locale, "预览有效至 {time}。账户变化后必须重新预览。", { time: inLocale(locale, () => ui.fmtTime(T + 300)) })), `${locale} ${op}: the expiry`);
      assert.ok(button(loaded, tx(locale, "重新预览")), `${locale} ${op}: a new preview is offered`);
      assert.ok(loaded.includes("<textarea"), `${locale} ${op}: a reason is asked for`);

      const none = confirm(locale, plan({ ...NONE_ELIGIBLE, operation: op }), { operation: op });
      assert.match(submit(none), /disabled=""/, `${locale} ${op}: nothing eligible, nothing to confirm\n${none}`);
      assert.ok(none.includes(tx(locale, "所选账户都不符合条件，没有可以执行的账户。")), `${locale} ${op}: says why`);

      const pending = confirm(locale, null, { operation: op });
      assert.match(submit(pending), /disabled=""/, `${locale} ${op}: not before the preview`);
      assert.ok(pending.includes(tx(locale, "正在核对所选账户与操作影响…")), `${locale} ${op}: the preview is being read`);

      const refused = confirm(locale, plan({ operation: op }), { operation: op, invalid: true });
      assert.match(submit(refused), /disabled=""/, `${locale} ${op}: a refused preview cannot be confirmed`);
      assert.ok(refused.includes(tx(locale, "这份预览已失效，请重新预览后再确认。")), `${locale} ${op}: and it says so`);

      const failed = confirm(locale, null, { operation: op, error: "fixture preview error" });
      assert.ok(failed.includes("fixture preview error") && button(failed, tx(locale, "重新预览")), `${locale} ${op}: a failed preview can be retried`);
    }
  }
});

test("one idempotency key per preview: a retry of the same preview reuses it, a new preview gets a new one", () => {
  const first = bulk.bulkAttempt(null, "fixture-token-1");
  assert.match(first.key, /^[A-Za-z0-9_-]{16,128}$/, "the server's key format");
  assert.equal(bulk.bulkAttempt(first, "fixture-token-1"), first);
  const next = bulk.bulkAttempt(first, "fixture-token-2");
  assert.notEqual(next.key, first.key);
  assert.equal(next.token, "fixture-token-2");
});

test("the result lists every account, failures worded, and offers a new preview of only the failed ones", () => {
  for (const locale of LOCALES) {
    const html = result(locale);
    const rows = text(html);
    for (const r of RESPONSE.results) assert.ok(rows.includes(NAMES[r.id]), `${locale}: ${r.id} listed\n${rows}`);
    assert.equal((html.match(/<li class="(ok|err)"/g) || []).length, RESPONSE.results.length, `${locale}: one line per account`);
    assert.ok(rows.includes(tx(locale, "已停用，撤销 {sessions} 个会话和 {tokens} 个应用令牌", { sessions: 2, tokens: 1 })), `${locale}: what the suspension did`);
    assert.ok(rows.includes(tx(locale, "此前已完成，本次未重复执行。")), `${locale}: done by an earlier attempt`);
    for (const r of RESPONSE.results.filter((x) => !x.ok)) {
      const worded = inLocale(locale, () => labels.resultText(r.code, r.message));
      assert.ok(worded && rows.includes(worded), `${locale} ${r.code}: ${worded}`);
    }
    assert.ok(rows.includes(tx(locale, "成功{p0}项，失败{p1}项。请查看失败原因，并核对刷新后的列表。", { p0: 2, p1: 3 })), `${locale}: the summary`);
    assert.ok(button(html, tx(locale, "重新预览失败的账户")), `${locale}: the failed accounts can be previewed again\n${html}`);
    assert.ok(html.includes(tx(locale, bulk.BULK_OPERATION_LABELS.suspend)), `${locale}: titled by the operation`);
    const revoked = text(result(locale, { ...RESPONSE, operation: "revoke_sessions" }, "revoke_sessions"));
    assert.ok(revoked.includes(tx(locale, "已撤销 {sessions} 个会话和 {tokens} 个应用令牌", { sessions: 2, tokens: 1 })), `${locale}: what the revocation did`);
    const clean = result(locale, { ...RESPONSE, ok: true, succeeded: 2, failed: 0, results: RESPONSE.results.filter((r) => r.ok) });
    assert.ok(!button(clean, tx(locale, "重新预览失败的账户")), `${locale}: nothing failed, nothing to preview again`);
  }
  // The button hands over exactly the failed ids, in the result's order, and leaves closing to the caller.
  let handed = null, closed = false;
  const dialog = results.BulkResultDialog({ title: "停用所选账户", response: RESPONSE, labelOf: (id) => id, onClose() { closed = true; },
    retryFailed: { label: "重新预览失败的账户", run(ids) { handed = ids; } } });
  const footer = React.Children.toArray(dialog.props.footer.props.children);
  footer.find((el) => el?.props?.children === "重新预览失败的账户").props.onClick();
  assert.deepEqual(handed, ["u_fx_admin", "u_fx_state", "u_fx_missing"]);
  assert.equal(closed, false);
});

test("the English render has no untranslated text", () => {
  const en = [bar("en", MANAGE), bar("en", SUPPORT), bar("en", VIEW), bar("en", BOTH, 100, true), tab("en", BOTH), tab("en", VIEW),
    preview("en"), preview("en", plan({ operation: "revoke_sessions" }), "revoke_sessions", "u_fx_one"), preview("en", NONE_ELIGIBLE),
    confirm("en", plan()), confirm("en", NONE_ELIGIBLE, { invalid: true }), confirm("en", null, { operation: "revoke_sessions" }),
    result("en"), result("en", { ...RESPONSE, operation: "revoke_sessions" }, "revoke_sessions")]
    .map((html) => text(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split("\n").filter((l) => HAN.test(l)).join("\n")}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("the refusals of the preview and the batch read as sentences on the administration page", () => {
  const codes = ["bad_operation", "missing_users", "too_many", "already_applied", "ownership_required", "organization_ownership_required",
    "admin_operation_failed", "admin_confirmation_required", "admin_preview_invalid", "admin_preview_expired", "admin_preview_stale",
    "idempotency_conflict", "missing_reason", "no_user", "target_outranks_actor", "demote_first", "self_suspend", "last_super_admin",
    "application_identity", "account_state_conflict"];
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  for (const code of codes) {
    for (const locale of LOCALES) {
      const worded = inLocale(locale, () => labels.resultText(code, ""));
      assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
    }
  }
  // `too_many` with the limit the server sends is worded with that number
  assert.ok(inLocale("en", () => labels.resultText("too_many", "一次最多处理 {BULK_MAX} 个账户", { field: "user_ids", limit: 100 })).includes("100"));
});

test("every sentence of the bulk dialog and bar is catalogued in English and Traditional Chinese", () => {
  assert.deepEqual(ui.validateMessages(messages.bulkAccountMessages), []);
  const src = readFileSync(new URL("../src/governance/BulkAccountDialog.tsx", import.meta.url), "utf8");
  const sentences = new Set([...src.matchAll(/\btr\("([^"]+)"/g)].map((m) => m[1]));
  for (const map of [bulk.BULK_OPERATION_LABELS, bulk.BULK_OPERATION_IMPACT, bulk.BULK_OPERATION_MISSING]) for (const s of Object.values(map)) sentences.add(s);
  sentences.add(bulk.BULK_NOTE);
  const tabSrc = readFileSync(new URL("../src/Admin.tsx", import.meta.url), "utf8");
  const users = tabSrc.slice(tabSrc.indexOf("function UsersTab"), tabSrc.indexOf("function WorkspacesTab"));
  for (const m of users.matchAll(/\btr\("([^"]+)"/g)) sentences.add(m[1]);
  sentences.add("已清除选择"); // announced by components/selection.ts escapeClears
  assert.ok(sentences.size > 40, `expected the dialog's and the list's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const s of sentences) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    if (HAN.test(ui.translate("en", s, { count: 2 }))) untranslated.push(s);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
