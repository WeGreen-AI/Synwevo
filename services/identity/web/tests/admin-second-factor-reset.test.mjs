/** A26: the platform-controlled reset of a lost second factor — the account detail's second-factor
 *  section (governance/SecondFactorResetDialog.tsx) with the factors as counts, the pending reset and
 *  the recent ones; the reset and cancel offered only with `platform.users.recover` and only where
 *  the server's preview could allow them; the confirmation that names every effect, the 72-hour
 *  delay and the mail with its cancel link, and cannot be sent before its preview is loaded; and the
 *  signed-out page the mail links to (pages/account/SecondFactorReset.tsx).
 *
 *  Rendered the way the browser renders it (vite's module graph, React's server renderer), with the
 *  server's answers given as fixtures: no API, no accounts, no user data. Run from the package: `npm test`. */
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

// Explicit browser-environment fixture, as in admin-bulk-accounts.test.mjs.
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/admin"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };
globalThis.localStorage ??= globalThis.sessionStorage;

let server, ui, client, sf, messages, labels, access, page, pageMessages;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  sf = await server.ssrLoadModule("/src/governance/SecondFactorResetDialog.tsx");
  messages = await server.ssrLoadModule("/src/governance/second-factor.messages.ts");
  labels = await server.ssrLoadModule("/src/governance/labels.ts");
  access = await server.ssrLoadModule("/src/governance/PlatformAccess.tsx");
  page = await server.ssrLoadModule("/src/pages/account/SecondFactorReset.tsx");
  pageMessages = await server.ssrLoadModule("/src/pages/account/second-factor-reset.messages.ts");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });

// FIXTURE: the server's second-factor state (GET /api/admin/users/{uid}/second-factor), its preview
// (POST …/second-factor-reset/preview) and the account the detail shows. Synthetic, on example.test.
const T = 1_790_000_000;
const DUE = T + 72 * 3600;
const TARGET = { id: "u_fx_person", email: "person-fixture@example.test", status: "active", platform_access: false, application_identity: false };
const PENDING = { id: "sfr_fx_pending", created_at: T, due_at: DUE, method: "video_call", requested_by_email: "super-fixture@example.test", include_passkeys: true };
const HISTORY = [
  { id: "sfr_fx_pending", status: "pending", created_at: T, due_at: DUE, method: "video_call" },
  { id: "sfr_fx_done", status: "completed", created_at: T - 900_000, due_at: T - 640_800, completed_at: T - 640_700, method: "in_person" },
  { id: "sfr_fx_cancel", status: "cancelled", created_at: T - 2_000_000, due_at: T - 1_740_800, cancelled_at: T - 1_900_000, method: "verified_document" },
  { id: "sfr_fx_void", status: "void", created_at: T - 3_000_000, due_at: T - 2_740_800, method: "other" },
];
const STATE = { totp: true, recovery_remaining: 3, passkeys: 2, pending_reset: PENDING, history: HISTORY };
const QUIET = { totp: true, recovery_remaining: 0, passkeys: 0, pending_reset: null, history: [] };
const plan = (over = {}) => ({ action: "admin.user.second_factor_reset.request", scope: "user", risk: "high",
  target: { id: TARGET.id, email: TARGET.email, status: "active" }, factors: { totp: true, recovery_remaining: 3, passkeys: 2 },
  effect: { removes_totp: true, removes_recovery_codes: true, removes_passkeys: 0, sessions: 4, tokens: 1 },
  due_at: DUE, expires_at: T + 300, preview_token: "fixture-signed-preview", ...over });
const WITH_PASSKEYS = plan({ effect: { removes_totp: true, removes_recovery_codes: true, removes_passkeys: 2, sessions: 4, tokens: 1 } });

const VIEW = new Set(["platform.users.view", "platform.users.manage", "platform.users.support"]);
const RECOVER = new Set(["platform.users.view", "platform.users.recover"]);
const LOCALES = ["zh-Hans", "zh-Hant", "en"];
const HAN = /[㐀-鿿、，]/;
const FIXTURE_TEXT = /[a-z-]+-fixture@example\.test|u_fx_[a-z]+|sfr_fx_[a-z]+/g;

function inLocale(locale, run) { ui.setLocale(locale); try { return run(); } finally { ui.setLocale("zh-Hans"); } }
const render = (locale, element) => inLocale(locale, () => renderToStaticMarkup(element).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
const text = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
/** The page's text without the language menu, whose language names are written in their own language. */
const pageText = (html) => text(html.replace(/<label class="language-menu[\s\S]*?<\/label>/, ""));
const tx = (locale, source, params) => ui.translate(locale, source, params);
const time = (locale, at) => inLocale(locale, () => ui.fmtTime(at));
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const button = (html, name) => new RegExp(`<button[^>]*>${esc(name)}</button>`).test(html);
const submit = (html) => html.match(/<button type="submit"[^>]*>[^<]*<\/button>/)?.[0] ?? "";

const section = (locale, state = STATE, { held = RECOVER, target = TARGET, selfId, handlers = true } = {}) =>
  render(locale, React.createElement(sf.SecondFactorSection, { state, target, held, selfId,
    onReset: handlers ? () => {} : undefined, onCancel: handlers ? () => {} : undefined }));
const impact = (locale, p = plan()) => render(locale, React.createElement(sf.SecondFactorResetImpact, { plan: p }));
const confirm = (locale, p, { method = "in_person", confirmed = true, invalid = false, error = "", passkeys = 2, includePasskeys = false } = {}) =>
  render(locale, React.createElement(sf.SecondFactorResetConfirm, { target: TARGET, plan: p, error, invalid, busy: false,
    passkeys, includePasskeys, onIncludePasskeys() {}, method, onMethod() {}, confirmed, onConfirmed() {},
    onPreviewAgain() {}, run: async () => ({ ok: true, operation_id: "ao_fixture", reset: { id: "sfr_fx_new", due_at: DUE, status: "pending" } }),
    onClose() {}, onDone() {} }));
const view = (locale, state, extra = {}) => render(locale, React.createElement(page.SecondFactorResetView, { state, onCancel() {}, ...extra }));
const utc = (locale, at) => inLocale(locale, () => new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" }).format(at * 1000)) + " UTC";

test("the section states the factors, the pending reset and the recent resets in every language", () => {
  for (const locale of LOCALES) {
    const html = section(locale);
    const rows = text(html);
    for (const source of ["第二因素", "动态验证码", "已登记", "剩余恢复码", "已登记的安全密钥", "有一次第二因素重置待生效", "生效时间",
      "移除范围", "动态验证码、恢复码和安全密钥", "核验方式", "申请人", "申请时间"]) {
      assert.ok(rows.includes(tx(locale, source)), `${locale}: ${source}\n${rows}`);
    }
    // it waits for the notice to be delivered, then 72 hours; what voids it is said
    assert.ok(rows.includes(tx(locale, "只有通知邮件送达本人且满 {hours} 小时后才会执行；本人可在此之前用邮件里的链接取消。期间邮箱变更、账户停用、第二因素被使用或申请人失去权限，都会使它作废。", { hours: 72 })), `${locale}: the waiting rule\n${rows}`);
    assert.match(html, /<dt>[^<]+<\/dt><dd>3<\/dd>/, `${locale}: the recovery codes left, as a count`);
    assert.match(html, /<dd>2<\/dd>/, `${locale}: the passkeys, as a count`);
    assert.ok(rows.includes(time(locale, DUE)) && rows.includes(time(locale, T)), `${locale}: when it takes effect and when it was asked for`);
    assert.ok(rows.includes(tx(locale, "视频通话核验")) && rows.includes("super-fixture@example.test"), `${locale}: how and by whom`);
    // the recent resets, one row each, with their status and end
    assert.equal((html.match(/<tr><th scope="row">/g) || []).length, HISTORY.length, `${locale}: one row per reset`);
    for (const [status, label] of Object.entries(sf.RESET_STATUS_LABELS)) assert.ok(rows.includes(tx(locale, label)), `${locale}: ${status}`);
    assert.ok(rows.includes(tx(locale, "{time} 完成", { time: time(locale, T - 640_700) })), `${locale}: completed at`);
    assert.ok(rows.includes(tx(locale, "{time} 取消", { time: time(locale, T - 1_900_000) })), `${locale}: cancelled at`);
    assert.ok(rows.includes(tx(locale, "到期时未执行（{time}）", { time: time(locale, T - 2_740_800) })), `${locale}: void`);
    assert.ok(rows.includes(tx(locale, "{time} 生效", { time: time(locale, DUE) })), `${locale}: pending`);
    for (const m of ["当面核验", "核验了证件", "其他（在理由中说明）"]) assert.ok(rows.includes(tx(locale, m)), `${locale}: ${m}`);
    // nothing but counts and states: no seed, code or credential id field
    assert.doesNotMatch(html, /secret|seed|otpauth|credential_id/i);
    const kept = text(section(locale, { ...STATE, pending_reset: { ...PENDING, include_passkeys: false } }));
    assert.ok(kept.includes(tx(locale, "动态验证码和恢复码（保留安全密钥）")), `${locale}: passkeys kept`);
    const quiet = text(section(locale, { ...QUIET, totp: false }, { held: VIEW }));
    assert.ok(quiet.includes(tx(locale, "未登记")) && quiet.includes(tx(locale, "没有第二因素重置记录。")), `${locale}: the quiet states\n${quiet}`);
    assert.ok(!quiet.includes("<table"), "no table without resets");
  }
});

test("the reset and the cancel are offered only with platform.users.recover", () => {
  for (const locale of LOCALES) {
    const reset = tx(locale, "重置第二因素"), cancel = tx(locale, "取消重置");
    const pending = section(locale);
    assert.ok(button(pending, cancel), `${locale}: a pending reset can be cancelled\n${pending}`);
    assert.ok(!button(pending, reset), `${locale}: no second reset while one is pending`);
    const free = section(locale, { ...STATE, pending_reset: null });
    assert.ok(button(free, reset) && !button(free, cancel), `${locale}: the reset when nothing is pending\n${free}`);
    assert.ok(free.includes(tx(locale, "用于本人同时丢失了动态验证码和全部恢复码：先核验本人身份再申请，{hours} 小时后生效，期间本人可以取消。", { hours: 72 })), `${locale}: what it is for`);
    for (const state of [STATE, { ...STATE, pending_reset: null }]) {
      const viewing = section(locale, state, { held: VIEW });
      assert.ok(!button(viewing, reset) && !button(viewing, cancel), `${locale}: absent without the permission\n${viewing}`);
      assert.ok(!section(locale, state, { handlers: false }).includes("<button"), `${locale}: no handler, no button`);
    }
    assert.ok(section(locale, { ...STATE, pending_reset: null }, { held: VIEW }).includes(tx(locale, "你的平台角色不能重置第二因素。")), `${locale}: says why`);
    assert.ok(!section(locale, { ...STATE, pending_reset: null }, { held: undefined }).includes(tx(locale, "你的平台角色不能重置第二因素。")), "no hint without a known role");
  }
});

test("the reset is not offered where the server's preview would refuse it, and the section says why", () => {
  const free = { ...STATE, pending_reset: null };
  const cases = [
    ["self", free, { selfId: TARGET.id }, "不能为自己的账户申请重置第二因素。"],
    ["platform administrator", free, { target: { ...TARGET, platform_access: true } }, sf.RESET_PLATFORM_ADMIN],
    ["not active", free, { target: { ...TARGET, status: "suspended" } }, "只能为状态正常的账户重置第二因素。"],
    ["application identity", free, { target: { ...TARGET, application_identity: true } }, "这是应用身份（例如 Docs 卡片的发送者），不是人：不能对它执行这项操作。"],
    ["nothing to reset", { ...QUIET, totp: false }, {}, "该账户没有动态验证码、恢复码或安全密钥，无需重置。"],
  ];
  for (const locale of LOCALES) {
    for (const [name, state, options, reason] of cases) {
      const html = section(locale, state, options);
      assert.ok(!button(html, tx(locale, "重置第二因素")), `${locale} ${name}: not offered\n${html}`);
      assert.ok(html.includes(tx(locale, reason)), `${locale} ${name}: ${reason}`);
    }
    // passkeys alone are something to reset (with "also remove passkeys")
    assert.ok(button(section(locale, { ...QUIET, totp: false, passkeys: 1 }), tx(locale, "重置第二因素")), `${locale}: passkeys only`);
  }
});

test("the confirmation names every effect, the 72-hour delay and the mailed cancel link", () => {
  for (const locale of LOCALES) {
    const rows = text(impact(locale));
    for (const [source, params] of [
      ["生效时间：{time}", { time: time(locale, DUE) }],
      ["删除动态验证码：身份验证器应用里的条目不再有效。"],
      ["删除剩余的 {count} 个恢复码。", { count: 3 }],
      ["保留 {count} 个安全密钥：本人仍可用它们登录。", { count: 2 }],
      ["结束该账户的全部会话并撤销应用令牌（当前 {sessions} 个会话、{tokens} 个应用令牌）。", { sessions: 4, tokens: 1 }],
      ["口令、资料、成员关系和数据不变；之后本人用口令登录，再重新设置第二因素。"],
      [sf.RESET_MAIL_NOTE],
    ]) assert.ok(rows.includes(tx(locale, source, params)), `${locale}: ${source}\n${rows}`);
    assert.ok(rows.includes("person-fixture@example.test"), `${locale}: the target`);
    const removing = text(impact(locale, WITH_PASSKEYS));
    assert.ok(removing.includes(tx(locale, "移除 {count} 个安全密钥。", { count: 2 })), `${locale}: passkeys removed`);
    assert.ok(!removing.includes(tx(locale, "保留 {count} 个安全密钥：本人仍可用它们登录。", { count: 2 })), `${locale}: and not kept`);
    const bare = text(impact(locale, plan({ factors: { totp: false, recovery_remaining: 0, passkeys: 1 },
      effect: { removes_totp: false, removes_recovery_codes: false, removes_passkeys: 1, sessions: 0, tokens: 0 } })));
    assert.ok(!bare.includes(tx(locale, "删除动态验证码：身份验证器应用里的条目不再有效。")), `${locale}: only what is there`);

    const dialog = text(confirm(locale, plan()));
    assert.ok(dialog.includes(tx(locale, sf.RESET_DELAY_NOTE, { hours: 72 })), `${locale}: the delay\n${dialog}`);
    assert.ok(dialog.includes(tx(locale, sf.RESET_PLATFORM_NOTE)), `${locale}: no seed, no code, never as the person`);
    assert.ok(dialog.includes(tx(locale, "预览有效至 {time}。账户变化后必须重新预览。", { time: time(locale, T + 300) })), `${locale}: the preview's expiry`);
  }
  assert.equal(sf.RESET_DELAY_HOURS, 72, "the server's SECOND_FACTOR_RESET_DELAY_S in hours");
});

test("the confirmation cannot be sent before its preview is loaded, a method is chosen and the check is ticked", () => {
  for (const locale of LOCALES) {
    const name = tx(locale, "重置第二因素");
    const ready = confirm(locale, plan());
    assert.ok(ready.includes('role="alertdialog"'), `${locale}: a confirmation dialog`);
    assert.match(submit(ready), new RegExp(`class="btn danger"[^>]*>${esc(name)}</button>$`), `${locale}: the confirm label\n${submit(ready)}`);
    assert.doesNotMatch(submit(ready), /disabled=""/, `${locale}: confirmable once everything is there`);
    assert.ok(ready.includes("<textarea") && ready.includes(tx(locale, "写明如何核验了本人身份，以及为什么需要重置")), `${locale}: a reason is asked for`);
    // the identity-confirmation method: a labelled select with the four methods and no default
    const select = ready.match(/<select[^>]*>[\s\S]*?<\/select>/)?.[0] ?? "";
    assert.match(ready, new RegExp(`<label for="([^"]+)">${esc(tx(locale, "如何核验了本人身份"))}[\\s\\S]*?<select id="\\1"`), `${locale}: the select is labelled`);
    assert.equal((select.match(/<option /g) || []).length, 5, `${locale}: a prompt and four methods`);
    for (const m of sf.RESET_METHODS) assert.ok(select.includes(`value="${m}"`) && select.includes(tx(locale, sf.RESET_METHOD_LABELS[m])), `${locale}: ${m}`);
    assert.ok(select.includes(`<option value="">${tx(locale, "请选择核验方式…")}</option>`), `${locale}: no method is chosen for the administrator`);
    assert.ok(button(ready, tx(locale, "重新预览")), `${locale}: a new preview is offered`);
    assert.ok(ready.includes(tx(locale, "同时移除该账户的安全密钥（{count} 个）", { count: 2 })), `${locale}: also remove passkeys`);
    assert.ok(!confirm(locale, plan(), { passkeys: 0 }).includes(tx(locale, "同时移除该账户的安全密钥（{count} 个）", { count: 0 })), `${locale}: no passkeys, no choice`);

    for (const [why, html] of [
      ["before the preview", confirm(locale, null)],
      ["without a method", confirm(locale, plan(), { method: "" })],
      ["without the check", confirm(locale, plan(), { confirmed: false })],
      ["with a refused preview", confirm(locale, plan(), { invalid: true })],
      ["with a failed preview", confirm(locale, null, { error: "fixture preview error" })],
    ]) assert.match(submit(html), /disabled=""/, `${locale}: not ${why}\n${submit(html)}`);
    assert.ok(confirm(locale, null).includes(tx(locale, "正在核对账户与重置影响…")), `${locale}: the preview is being read`);
    assert.ok(confirm(locale, plan(), { invalid: true }).includes(tx(locale, "这份预览已失效，请重新预览后再确认。")), `${locale}: says the preview is spent`);
    const failed = confirm(locale, null, { error: "fixture preview error", passkeys: 2 });
    assert.ok(failed.includes("fixture preview error") && button(failed, tx(locale, "重新预览")), `${locale}: a failed preview can be retried`);
    assert.ok(failed.includes(tx(locale, "同时移除该账户的安全密钥（{count} 个）", { count: 2 })), `${locale}: a refused preview (nothing_to_reset) can be asked again with passkeys`);
    // the real dialog, as it is first drawn: the preview is asked for and nothing can be sent yet
    const opening = render(locale, React.createElement(sf.SecondFactorResetDialog, { target: TARGET, passkeys: 2, onClose() {}, onDone() {} }));
    assert.match(submit(opening), /disabled=""/, `${locale}: the dialog opens unconfirmable`);
    assert.ok(opening.includes(tx(locale, "正在核对账户与重置影响…")), `${locale}: while its preview loads`);
  }
});

test("one idempotency key per preview: a retry of the same preview reuses it, a new preview gets a new one", () => {
  const first = sf.resetAttempt(null, "fixture-token-1");
  assert.match(first.key, /^[A-Za-z0-9_-]{16,128}$/, "the server's key format");
  assert.equal(sf.resetAttempt(first, "fixture-token-1"), first);
  const next = sf.resetAttempt(first, "fixture-token-2");
  assert.notEqual(next.key, first.key);
  assert.equal(next.token, "fixture-token-2");
});

test("the reset's refusals read as sentences about this act on the administration page", () => {
  const generic = (locale) => ["操作失败，请重试。", "操作未完成，请刷新后重试。"].map((s) => tx(locale, s));
  const codes = ["self_reset", "nothing_to_reset", "reset_pending", "bad_method", "target_is_platform_admin", "application_identity",
    "account_state_conflict", "no_user", "missing_reason", "admin_confirmation_required", "admin_preview_invalid", "admin_preview_expired",
    "admin_preview_stale", "idempotency_conflict"];
  for (const locale of LOCALES) {
    for (const code of codes) {
      const worded = inLocale(locale, () => labels.errText(sf.aboutReset(new client.ApiError(409, code, ""))));
      assert.ok(worded && !worded.includes(code) && !generic(locale).includes(worded), `${locale} ${code}: ${worded}`);
      if (locale === "en") assert.ok(!HAN.test(worded), `${code}: ${worded}`);
    }
    // a platform administrator's factor is the emergency process, not the ownership rule the code map words
    assert.equal(inLocale(locale, () => labels.errText(sf.aboutReset(new client.ApiError(409, "target_is_platform_admin", "")))), tx(locale, sf.RESET_PLATFORM_ADMIN));
    assert.equal(inLocale(locale, () => labels.errText(sf.aboutReset(new client.ApiError(409, "admin_preview_stale", "")))), tx(locale, sf.RESET_STALE));
  }
  // the server's own catalogued sentence is kept, and every other refusal passes through unchanged
  const catalogued = new client.ApiError(409, "admin_preview_stale", "账户或访问权限已变化，请重新预览影响。");
  assert.equal(sf.aboutReset(catalogued), catalogued);
  const other = new client.ApiError(403, "reauth_required", "", { scope: "admin" });
  assert.equal(sf.aboutReset(other), other, "re-authentication keeps its identity for ActionDialog");
  // the permission has a label of its own in every language
  for (const locale of ["zh-Hant", "en"]) {
    const label = inLocale(locale, () => access.permissionLabel("platform.users.recover"));
    assert.ok(label !== "platform.users.recover" && label === tx(locale, "核验身份后重置丢失的第二因素（延迟生效并通知本人）"), `${locale}: ${label}`);
    if (locale === "en") assert.ok(!HAN.test(label), label);
  }
});

test("the signed-out page says when the reset takes effect, what happens, and cancels it", () => {
  for (const locale of LOCALES) {
    const html = view(locale, { phase: "pending", due_at: DUE });
    const rows = text(html);
    assert.ok(rows.includes(tx(locale, "取消第二因素重置")), `${locale}: the title`);
    assert.ok(rows.includes(tx(locale, "重置将于 {date} 生效。", { date: utc(locale, DUE) })), `${locale}: the due time\n${rows}`);
    for (const source of ["删除账户的动态验证码和恢复码；如果申请包含安全密钥，也会一并移除。", "结束所有设备上的登录。",
      "口令、资料和数据不变；之后请用口令登录，再重新设置第二因素。", "取消后什么都不会改变。此操作不会自动登录。",
      "如果你没有请求平台帮你找回第二因素，请立即取消，并联系平台管理员。如果是你请求的，无需任何操作。"]) {
      assert.ok(rows.includes(tx(locale, source)), `${locale}: ${source}`);
    }
    assert.match(html, new RegExp(`<button type="button" class="btn primary">${esc(tx(locale, "取消重置"))}</button>`), `${locale}: the cancel button\n${html}`);
    assert.match(view(locale, { phase: "pending", due_at: DUE }, { busy: true }), new RegExp(`<button type="button" class="btn primary" disabled="">${esc(tx(locale, "处理中…"))}</button>`), `${locale}: busy`);
    assert.ok(html.includes('href="/login"') && rows.includes(tx(locale, "返回登录")), `${locale}: the way to sign in`);
    const done = view(locale, { phase: "done" });
    assert.ok(done.includes(tx(locale, "重置已取消：你的第二因素保持不变。")) && !done.includes("<button"), `${locale}: done`);
    const invalid = view(locale, { phase: "invalid" });
    assert.ok(invalid.includes(tx(locale, "这个链接无效或已失效：重置可能已经取消或完成，或链接不完整。请打开邮件里的完整链接；如有疑问，请联系平台管理员。")) && !invalid.includes("<button"), `${locale}: invalid link`);
    const expired = view(locale, { phase: "expired", due_at: T });
    assert.ok(expired.includes(tx(locale, "取消期限已过（{date}）：重置已经或即将生效，这个链接不能再取消它。之后请用口令登录，再重新设置第二因素。", { date: utc(locale, T) })) && !expired.includes("<button"), `${locale}: expired`);
    const failed = view(locale, { phase: "loading" }, { error: "fixture refusal", onRetry() {} });
    // the account pages' ErrorNotice words its button with the hook, which a server render reads as zh-Hans
    assert.ok(failed.includes("fixture refusal") && /<button class="btn sm">[^<]+<\/button>/.test(failed), `${locale}: another failure can be retried`);
  }
});

test("the page reads its link: without a token it is invalid at once, with one it asks the server", () => {
  const location = globalThis.window.location;
  try {
    globalThis.window.location = new URL("http://127.0.0.1/account/second-factor-reset?lang=en");
    const none = render("en", React.createElement(page.default));
    assert.ok(none.includes(tx("en", "这个链接无效或已失效：重置可能已经取消或完成，或链接不完整。请打开邮件里的完整链接；如有疑问，请联系平台管理员。")), none);
    globalThis.window.location = new URL("http://127.0.0.1/account/second-factor-reset?lang=en#token=fixture-capability-token-0000");
    const asking = render("en", React.createElement(page.default));
    assert.ok(asking.includes(tx("en", "加载中…")) && !asking.includes("<button"), asking);
  } finally { globalThis.window.location = location; }
  // the server says `bad_token` for every unusable link; past the due time that is the reset taking effect
  assert.deepEqual(page.previewState(DUE, T), { phase: "pending", due_at: DUE });
  // still pending on the server past the announced time (waiting for the notice): still cancellable
  assert.deepEqual(page.previewState(T, T + 3600), { phase: "pending", due_at: T });
  assert.deepEqual(page.refusedState({ phase: "pending", due_at: DUE }, DUE + 1), { phase: "expired", due_at: DUE });
  assert.deepEqual(page.refusedState({ phase: "pending", due_at: DUE }, T), { phase: "invalid" }, "cancelled elsewhere or spent");
  assert.deepEqual(page.refusedState({ phase: "loading" }, T), { phase: "invalid" });
});

test("the English renders have no untranslated text", () => {
  const en = [section("en"), section("en", { ...STATE, pending_reset: null }), section("en", { ...STATE, pending_reset: null }, { held: VIEW }),
    section("en", { ...QUIET, totp: false }), section("en", STATE, { target: { ...TARGET, platform_access: true } }),
    impact("en"), impact("en", WITH_PASSKEYS), confirm("en", plan()), confirm("en", null), confirm("en", plan(), { invalid: true }),
    view("en", { phase: "pending", due_at: DUE }), view("en", { phase: "done" }), view("en", { phase: "invalid" }), view("en", { phase: "expired", due_at: T })]
    .map((html) => pageText(html).replace(FIXTURE_TEXT, "")).join("\n");
  assert.ok(!HAN.test(en), `untranslated in English:\n${en.split("\n").filter((l) => HAN.test(l)).join("\n")}`);
  assert.doesNotMatch(en, /undefined|NaN|\[object/);
});

test("every sentence of the section, the dialogs and the page is catalogued in English and Traditional Chinese", () => {
  assert.deepEqual(ui.validateMessages(messages.secondFactorMessages), []);
  assert.deepEqual(ui.validateMessages(pageMessages.secondFactorResetMessages), []);
  const admin = new Set(sf.SECOND_FACTOR_SENTENCES);
  const src = readFileSync(new URL("../src/governance/SecondFactorResetDialog.tsx", import.meta.url), "utf8");
  for (const m of src.matchAll(/\btr\("([^"]+)"/g)) admin.add(m[1]);
  const pageSrc = readFileSync(new URL("../src/pages/account/SecondFactorReset.tsx", import.meta.url), "utf8");
  const signedOut = new Set([...pageSrc.matchAll(/\btr\('([^']+)'/g)].map((m) => m[1]));
  signedOut.add("取消第二因素重置"); // the person's own security activity row (ActivitySection.tsx)
  assert.ok(admin.size > 60 && signedOut.size > 12, `expected the sentences, found ${admin.size} and ${signedOut.size}`);
  const missing = [], untranslated = [];
  for (const s of [...admin, ...signedOut]) {
    if (!ui.hasMessage(s)) { missing.push(s); continue; }
    for (const locale of ["en", "zh-Hant"]) if (ui.translate(locale, s, { count: 2 }) === s && locale === "en") untranslated.push(s);
    if (HAN.test(ui.translate("en", s, { count: 2 }).replace(/Synwevo/g, ""))) untranslated.push(s);
  }
  // the administration sentences are in the catalogue the dialog itself loads, not borrowed from another page's
  const own = Object.keys(messages.secondFactorMessages);
  const common = new Set(["状态", "重试"]); // the administration pages' own words (governance/messages.ts, loaded through labels.ts)
  const borrowed = [...admin].filter((s) => !own.includes(s) && !common.has(s));
  assert.deepEqual(borrowed, [], "an administration sentence not in second-factor.messages.ts");
  const ownPage = Object.keys(pageMessages.secondFactorResetMessages);
  const shared = new Set(["加载中…", "处理中…", "返回登录", "重试"]); // the account pages' catalogue (account.messages.ts)
  assert.deepEqual([...signedOut].filter((s) => !ownPage.includes(s) && !shared.has(s)), [], "a page sentence not in second-factor-reset.messages.ts");
  assert.deepEqual(missing, [], "no catalogue entry");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
