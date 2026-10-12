/** The registration diagnosis card (admin coverage A27): one address's stages in the reader's
 *  language, each withheld section explained. Isolated UI fixtures: no HTTP server, no accounts. */
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

let server, ui, mod;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: "error", appType: "custom",
    server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule("@synwevo/ui");
  mod = await server.ssrLoadModule("/src/governance/RegistrationDiagnostics.tsx");
  await server.ssrLoadModule("/src/Admin.tsx");
});
after(async () => { await server?.close(); });

const T = 1_760_000_000;
// FIXTURE: a synthetic diagnosis in the server's shape (admin_routes.registration_diagnostics).
const FIXTURE = {
  email: "1155000000@link.example.test", valid: true, registration_mode: "open",
  account: { id: "u_fixture_reg", status: "pending", created_at: T, email_verified_at: null, locale: "zh-Hans" },
  throttle: { count: 4, first_at: T, last_at: T + 60, blocked: true, blocked_until: T + 3600, limit: 3, window_s: 3600 },
  tokens: [{ kind: "verify", created_at: T, expires_at: T + 86400, used_at: null, expired: false }],
  audit: { permitted: true,
    events: [{ id: "ae_1", at: T + 60, action: "auth.registration.throttled", result: "denied", ip: "203.0.113.9", detail: { scope: "email", retry_after: 3540 } },
      { id: "ae_2", at: T, action: "auth.registration", result: "ok", ip: "203.0.113.9", detail: {} }],
    challenges: [{ id: "ae_3", at: T - 1, result: "ok", ip: "203.0.113.9", action: "register", reason: null }] },
  mail: { permitted: true, items: [{ id: "ml_1", kind: "verify", status: "queued", attempts: 0, created_at: T, sent_at: null, error_code: null }] },
  stages: [{ stage: "validation", state: "valid", at: null }, { stage: "challenge", state: "ok", at: T - 1 }, { stage: "throttle", state: "blocked", at: T + 60 },
    { stage: "account", state: "pending", at: T }, { stage: "token", state: "issued", at: T }, { stage: "mail", state: "queued", at: T }, { stage: "activation", state: "pending", at: null }],
};
const HAN = /[㐀-鿿]/;
function render(locale, d) {
  ui.setLocale(locale);
  try { return renderToStaticMarkup(React.createElement(mod.RegistrationDiagnosisBody, { d })).replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&"); }
  finally { ui.setLocale("zh-Hans"); }
}

test("the stages, throttle, link, mail and events read as sentences in the reader's language", () => {
  const html = render("en", FIXTURE);
  for (const expected of ["Address validation", "Address format accepted", "Human verification", "Passed", "Throttled now", "Pending activation", "Issued, unused",
    "Queued", "Not activated yet", "made 4 requests in the last hour (limit 3)", "Account u_fixture_reg", "issued", "expires", "Events (2)", "auth.registration.throttled",
    "scope=email", "Denied", "same source address within 15 minutes"]) {
    assert.ok(html.includes(expected), `missing in English: ${expected}\n${html}`);
  }
  assert.ok(!HAN.test(html.replace(/1155000000@link\.example\.test|u_fixture_reg/g, "")), `untranslated text in English:\n${html}`);
  assert.doesNotMatch(html, /undefined|NaN|\[object/);
  const hant = render("zh-Hant", FIXTURE);
  assert.ok(hant.includes("地址校驗") && hant.includes("目前已限流") && hant.includes("驗證連結（1 個）"), hant);
});

test("withheld sections are explained and the quiet states are sentences", () => {
  const quiet = render("en", { ...FIXTURE, account: null, throttle: null, tokens: [], audit: { permitted: false, events: [], challenges: [] }, mail: { permitted: false, items: [] },
    stages: FIXTURE.stages.map((s) => ({ ...s, state: s.stage === "validation" ? "valid" : s.stage === "challenge" || s.stage === "mail" ? "withheld" : s.stage === "throttle" ? "clear" : "none", at: null })) });
  for (const expected of ["No account for this address.", "No verification link has been issued.", "Showing mail delivery needs the mail view permission.",
    "Showing registration events and human-verification results needs the audit view permission.", "Needs the audit view permission", "Needs the mail view permission", "Not throttled", "No account"]) {
    assert.ok(quiet.includes(expected), `missing: ${expected}\n${quiet}`);
  }
  assert.ok(!quiet.includes("<table") || quiet.split("<table").length === 2, "only the stage table remains");
});

test("every sentence of the card is catalogued in English and Traditional Chinese", () => {
  const src = readFileSync(new URL("../src/governance/RegistrationDiagnostics.tsx", import.meta.url), "utf8");
  const sentences = new Set([...src.matchAll(/\btr\("([^"]+)"/g)].map((m) => m[1]));
  for (const map of [mod.STAGE_LABELS, mod.STAGE_STATES, mod.REGISTRATION_MODE_LABELS]) for (const text of Object.values(map)) sentences.add(text);
  assert.ok(sentences.size > 40, `expected the card's sentences, found ${sentences.size}`);
  const missing = [], untranslated = [];
  for (const text of sentences) {
    if (!ui.hasMessage(text)) { missing.push(text); continue; }
    for (const locale of ["en", "zh-Hant"]) if (ui.translate(locale, text, { count: 2 }) === undefined) missing.push(`${locale}: ${text}`);
    if (HAN.test(ui.translate("en", text, { count: 2 }))) untranslated.push(text);
  }
  assert.deepEqual(missing, [], "no catalogue entry in the admin bundle");
  assert.deepEqual(untranslated, [], "untranslated in English");
});
