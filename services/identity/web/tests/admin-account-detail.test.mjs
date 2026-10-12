/** The account detail dialog (admin coverage A13): the facts the server sends, worded in the
 *  reader's language, with every omission the server decided explained rather than hidden.
 *
 *  Isolated UI fixtures: no HTTP server, no accounts or user data. Run from the package: `npm test`. */
import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { testServer } from "./_vite.mjs";

const WEB = fileURLToPath(new URL("../", import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) };

let server, ui, detail;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  detail = await server.ssrLoadModule("/src/governance/AccountDetail.tsx");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { await server?.close(); });

// FIXTURE: a synthetic account detail in the server's shape (admin_routes.account_detail).
const T = 1_760_000_000;
const FIXTURE = {
  user: { id: "u_fixture_detail", email: "detail-fixture@example.test", display_name: "Fixture Person", status: "active",
    platform_role: "none", email_verified_at: T, created_at: T - 86400, updated_at: T },
  application_identity: false, blocked_by: [], outranks_caller: false,
  sign_in: {
    password_set: true, totp: true, recovery_remaining: 7,
    passkeys: [{ id: "pk_fixture", name: "Fixture key", created_at: T, last_used_at: null, backed_up: true, uv_capable: true }],
    external_identities: [{ id: "xi_fixture", provider: "google", label: "Google", available: false, email: "detail-fixture@example.test",
      email_verified: true, created_at: T, last_used_at: T }],
  },
  platform_roles: ["support"], platform_grants: [{ role: "support", granted_at: T, expires_at: T + 86400 }],
  workspaces: [{ id: "ws_fixture", name: "Fixture workspace", kind: "org", status: "active", role: "editor", can_export: true, joined_at: T, apps: [{ id: "docweave", name: "Synwevo Docs" }] }],
  organizations: [{ id: "org_fixture", name: null, status: "active", role: "member", joined_at: T }],
  sessions: [
    { id: "s_fixture_1", created_at: T, last_seen_at: T, expires_at: T + 3600, amr: "password+totp", client: "macOS 上的 Chrome",
      client_parts: { browser: "Chrome", platform: "macOS" }, ip: "203.0.113.9", persistent: true, auth_provider: null },
    { id: "s_fixture_2", created_at: T, last_seen_at: T, expires_at: T + 3600, amr: "idp:google+totp", client: "iPhone 上的 Safari",
      client_parts: { browser: "Safari", platform: "iPhone" }, ip: null, persistent: false, auth_provider: "google" },
  ],
  backoff: { failures: 4, first_failure_at: T - 60, last_failure_at: T, retry_after: 2 },
  agent_access: { suspended: true, suspension: { id: "asp_fixture", reason: "Fixture: pending report review", report_id: "arp_000000000001", created_at: T, created_by_email: "admin-fixture@example.test" } },
  push_subscriptions: [
    { id: "ps_fixture_1", user_agent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128.0 Safari/537.36", created_at: T, last_sent_at: T, fail_count: 0, active: true },
    { id: "ps_fixture_2", user_agent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile/15E148 Safari/604.1", created_at: T - 60, last_sent_at: null, fail_count: 3, active: false },
  ],
  deletion: { pending: true, requested_at: T - 3600, due_at: T + 6 * 86400, overdue: false, cancellation_link_valid: true, blockers: [] },
  audit: { permitted: true, limit: 50, items: [{ id: "ae_fixture_1", at: T, action: "auth.login", actor_kind: "anonymous", actor_email: null,
    object_kind: "user", object_id: "u_fixture_detail", result: "denied", reason: "bad_password" }] },
};
// Han characters, and the Chinese enumeration and full-width commas: a list joined with 、 or ，
// in an English sentence is untranslated punctuation.
const HAN = /[㐀-鿿、，]/;
const FIXTURE_TEXT = /detail-fixture@example\.test|Fixture (workspace|Person|key)|u_fixture_detail/g;

/** Static markup with the entities React escapes in text turned back into characters. */
function render(locale, d, held, onAct, onExplain) {
  ui.setLocale(locale);
  try {
    return renderToStaticMarkup(React.createElement(detail.AccountDetailBody, { detail: d, held, onAct, onExplain }))
      .replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&");
  } finally { ui.setLocale("zh-Hans"); }
}

test("the body states the account's facts in the reader's language and shows nothing undefined", () => {
  const html = render("en", FIXTURE);
  for (const expected of ["Sign-in methods", "Password and authenticator code", "External identity (google) and authenticator code", "Chrome on macOS", "Safari on iPhone", "Provider disabled",
    "Fixture workspace", "Name hidden: your role cannot view the organization list", "Synced across devices",
    "4 consecutive failures", "the next attempt must wait", "bad_password", "Security timeline", "(expires",
    "Deletion requested by the person on", "The cancellation link is still valid",
    "Agent suspended", "Suspended since", "Fixture: pending report review", "Linked report arp_000000000001", "Push devices (2)", "Chrome · macOS", "Safari · iOS", "Never pushed", "Last 3 pushes failed", "Session ended; no longer receiving", "Receiving"]) {
    assert.ok(html.includes(expected), `missing in English: ${expected}\n${html}`);
  }
  assert.ok(!HAN.test(html.replace(FIXTURE_TEXT, "")), `untranslated text in English:\n${html}`);
  assert.doesNotMatch(html, /undefined|NaN|\[object/);
  const hant = render("zh-Hant", FIXTURE);
  assert.ok(hant.includes("安全時間線") && hant.includes("工作空間（1 個）") && hant.includes("密碼與動態驗證碼") && hant.includes("macOS 上的 Chrome"), hant);
});

test("what the server withholds is explained, and the quiet states are sentences", () => {
  const quiet = render("en", { ...FIXTURE, outranks_caller: true, application_identity: true,
    audit: { permitted: false, limit: 50, items: [] }, backoff: null, workspaces: [], organizations: [], sessions: [],
    agent_access: { suspended: false, suspension: null }, push_subscriptions: [], sign_in: { ...FIXTURE.sign_in, totp: false, passkeys: [], external_identities: [] } });
  for (const expected of ["Showing the timeline needs the audit view permission", "No failed sign-ins on record.", "No device has push enabled.", "Normal: no suspension in force.",
    "Not a member of any workspace.", "Not a member of any organization.", "No active sessions right now.",
    "This account's platform permissions outrank yours", "Application identity: managed by its application", "Off"]) {
    assert.ok(quiet.includes(expected), `missing: ${expected}\n${quiet}`);
  }
  assert.ok(!quiet.includes("<table"), "no table for an empty or withheld section");
  const cleared = render("en", { ...FIXTURE, backoff: { ...FIXTURE.backoff, retry_after: 0 } });
  assert.ok(cleared.includes("a new attempt is allowed now"), cleared);
  // A20: the deletion request's states, and the rules a request would meet, as sentences
  const overdue = render("en", { ...FIXTURE, deletion: { ...FIXTURE.deletion, overdue: true, cancellation_link_valid: false } });
  assert.ok(overdue.includes("Past due and waiting for the background sweep"), overdue);
  const lost = render("en", { ...FIXTURE, deletion: { ...FIXTURE.deletion, cancellation_link_valid: false } });
  assert.ok(lost.includes("no longer valid; it can be re-sent"), lost);
  const refused = render("en", { ...FIXTURE, deletion: { pending: false, requested_at: null, due_at: null, overdue: false, cancellation_link_valid: false,
    blockers: ["last_super_admin", "ownership_required"] } });
  assert.ok(refused.includes("would be refused: the last usable super administrator, ownership or administration of a shared workspace not yet handed over."), refused);
  assert.ok(!HAN.test(refused.replace(FIXTURE_TEXT, "")), `untranslated text or punctuation in English:\n${refused}`);
  assert.ok(render("zh-Hant", { ...FIXTURE, deletion: { pending: false, requested_at: null, due_at: null, overdue: false, cancellation_link_valid: false,
    blockers: ["last_super_admin", "ownership_required"] } }).includes("最後一名可用的最高管理員、共享工作空間的所有權或管理權未移交"), "the enumeration comma for a Chinese reader");
  const free = render("en", { ...FIXTURE, deletion: { pending: false, requested_at: null, due_at: null, overdue: false, cancellation_link_valid: false, blockers: [] } });
  assert.ok(free.includes("the person could request one now"), free);
  assert.ok(!render("en", { ...FIXTURE, deletion: undefined }).includes("Deletion request"), "an older server without the block: no section");
});

test("the per-object actions follow the caller's permission and never show on an account that outranks them", () => {
  const manage = new Set(["platform.users.view", "platform.users.manage"]);
  const acting = render("en", FIXTURE, manage, () => {});
  for (const label of ["Sign this device out", "Remove this passkey", "Unlink this external identity", "Revoke all push subscriptions", "Restore Agent access"]) assert.ok(acting.includes(label), label);
  assert.equal((acting.match(/Sign this device out/g) || []).length, 2, "one per session");
  const viewing = render("en", FIXTURE, new Set(["platform.users.view"]), () => {});
  assert.ok(!viewing.includes("Sign this device out") && !viewing.includes("Revoke all push subscriptions") && !viewing.includes("Restore Agent access") && viewing.includes("Your platform role can view this, not act on it."), viewing);
  assert.ok(!render("en", { ...FIXTURE, push_subscriptions: [] }, manage, () => {}).includes("Revoke all push subscriptions"), "nothing to revoke, no button");
  const outranked = render("en", { ...FIXTURE, outranks_caller: true }, manage, () => {});
  assert.ok(!outranked.includes("Remove this passkey") && outranked.includes("outrank yours"), outranked);
  const application = render("en", { ...FIXTURE, application_identity: true }, manage, () => {});
  assert.ok(!application.includes("Unlink this external identity"));
  assert.ok(!render("en", FIXTURE, manage).includes("Sign this device out"), "no handler, no buttons");
});

// FIXTURE: the server's access explanation (admin_routes.explain_access) for the fixture workspace.
const EXPLANATION = {
  user: { id: "u_fixture_detail", email: "detail-fixture@example.test", status: "active" },
  workspace: { id: "ws_fixture", name: "Fixture workspace", status: "active" },
  app: { id: "docweave", name: "Synwevo Docs", status: "active", scope: "workspace" },
  gates: [
    { gate: "account", state: "pass", detail: { status: "active" } },
    { gate: "application", state: "pass", detail: { status: "active", access_policy: "members", scope: "workspace" } },
    { gate: "membership", state: "pass", detail: { role: "editor", can_export: true } },
    { gate: "workspace", state: "pass", detail: { status: "active", kind: "org" } },
    { gate: "organization", state: "pass", detail: { reason: null, organization_id: "org_fixture" } },
    { gate: "workspace_app", state: "pass", detail: { enabled_at: T } },
    { gate: "entitlement", state: "fail", detail: { entitlement: "revoked", access_policy: "members" } },
  ],
  verdict: { allowed: false, workspace_id: null, reason: "你在该工作空间使用「Synwevo Docs」的资格已被撤销，请联系工作空间管理员" },
  consistent: true,
};

test("the access explanation names each gate with its result, in the reader's language (A49)", () => {
  const roles = new Set(["platform.users.view", "platform.roles.view"]);
  assert.ok(render("en", FIXTURE, roles, undefined, () => {}).includes("Explain access"), "the button for a reader of platform access");
  assert.ok(!render("en", FIXTURE, new Set(["platform.users.view"]), undefined, () => {}).includes("Explain access"), "not without the permission");
  assert.ok(!render("en", FIXTURE, roles).includes("Explain access"), "no handler, no button");
  ui.setLocale("en");
  let html;
  try { html = renderToStaticMarkup(React.createElement(detail.AccessExplanationBody, { d: EXPLANATION })).replace(/&quot;/g, '"').replace(/&#x27;/g, "'"); }
  finally { ui.setLocale("zh-Hans"); }
  for (const expected of ["Cannot open: Your access to “Synwevo Docs” in this workspace has been revoked. Contact the workspace administrator.", "Account status", "Workspace membership",
    "Organization active and the person is a member", "Application enabled in the workspace",
    "Entitlement", "Revoked", "Members may use it", "Failed", "Passed", "never acts as the person"]) assert.ok(html.includes(expected), `missing: ${expected}\n${html}`);
  assert.ok(!HAN.test(html), `untranslated in English:\n${html}`);
  // C3: the verdict's reason is the server's Simplified-Chinese sentence for the person; the
  // administrator reads it translated, and an uncatalogued sentence still shows as sent.
  const refusedIn = (locale, reason) => {
    ui.setLocale(locale);
    try { return renderToStaticMarkup(React.createElement(detail.AccessExplanationBody, { d: { ...EXPLANATION, verdict: { allowed: false, workspace_id: null, reason } } })).replace(/&quot;/g, '"').replace(/&#x27;/g, "'"); }
    finally { ui.setLocale("zh-Hans"); }
  };
  const member = refusedIn("en", "你不是该工作空间的成员");
  assert.ok(member.includes("Cannot open: You are not a member of this workspace."), member);
  assert.doesNotMatch(member, /[一-鿿]/, `untranslated in English:\n${member}`);
  assert.ok(refusedIn("zh-Hant", "你不是该工作空间的成员").includes("不能開啟：你不是該工作空間的成員"));
  assert.ok(refusedIn("en", "你所在的工作空间尚未启用「Synwevo Docs」，或你没有使用它的资格").includes("Cannot open: Your workspace has not enabled “Synwevo Docs”, or you are not entitled to use it."));
  assert.ok(refusedIn("en", "服务器新增的一句话").includes("Cannot open: 服务器新增的一句话"), "an uncatalogued reason falls back to the source");
  assert.ok(refusedIn("en", null).includes("Cannot open: —"));
  ui.setLocale("en");
  try {
    const skip = renderToStaticMarkup(React.createElement(detail.AccessExplanationBody, { d: { ...EXPLANATION, app: { ...EXPLANATION.app, scope: "personal" },
      gates: EXPLANATION.gates.slice(0, 4).concat(["organization", "workspace_app", "entitlement"].map((gate) => ({ gate, state: "skip", detail: { note: "not_workspace_scoped" } }))),
      verdict: { allowed: true, workspace_id: "ws_fixture", reason: null }, consistent: false } }));
    assert.ok(skip.includes("Not applicable") && skip.includes("not authorized per workspace") && skip.includes("Can open:") && skip.includes("disagrees with the real check"), skip);
  } finally { ui.setLocale("zh-Hans"); }
});

test("every sentence of the dialog is catalogued in English and Traditional Chinese", () => {
  const src = readFileSync(new URL("../src/governance/AccountDetail.tsx", import.meta.url), "utf8");
  const sentences = new Set([...src.matchAll(/\btr\("([^"]+)"/g)].map((m) => m[1]));
  for (const map of [detail.AMR_LABELS, detail.FACTOR_SUFFIX_LABELS, detail.ORG_ROLE_LABELS, detail.DELETION_BLOCKER_LABELS, detail.GATE_LABELS, detail.GATE_STATE_LABELS]) for (const text of Object.values(map)) sentences.add(text);
  for (const text of detail.GATE_DETAIL_SENTENCES) sentences.add(text);
  for (const text of [...detail.ACCESS_REASON_SENTENCES, ...detail.ACCESS_REASON_TEMPLATES]) sentences.add(text);
  for (const text of detail.ACCOUNT_ACTION_SENTENCES) sentences.add(text);
  sentences.add("查看详情"); // the row-menu item in Admin.tsx that opens the dialog
  assert.ok(sentences.size > 40, `expected the dialog's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const text of sentences) {
    if (!ui.hasMessage(text)) { missing.push(text); continue; }
    for (const locale of ["en", "zh-Hant"]) if (ui.translate(locale, text, { count: 2 }) === undefined) missing.push(`${locale}: ${text}`);
    if (HAN.test(ui.translate("en", text, { count: 2 }))) untranslated.push(text);
  }
  // The catalogues the admin bundle loads, not the account pages' (a sentence they share must be
  // registered here too, or the administration page shows it in the source language).
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
