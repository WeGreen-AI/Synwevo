/** Real Settings components rendered with explicit policy/credential fixtures; no API/model calls. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const storage = new Map();
globalThis.window ??= { location: new URL("http://127.0.0.1/agent/settings"), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} } as any;
globalThis.sessionStorage ??= { getItem: (k: string) => storage.get(k) ?? null, setItem: (k: string, v: string) => storage.set(k, v), removeItem: (k: string) => storage.delete(k) } as any;
globalThis.localStorage ??= globalThis.sessionStorage;
let server: any, ui: any, settings: any, client: any, messages: any;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
    logLevel: "error", appType: "custom", server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: "settings-test-exports", enforce: "pre", transform(code, id) {
      if (id.endsWith("/src/apps/agent/Settings.tsx")) return code + "\nexport { PolicyRow, AccessForm, ErrorNotice };";
      // The production SSR snapshot is always Simplified Chinese; exercise each client locale
      // against the real translation dictionaries without needing a browser or HTTP server.
      if (id.endsWith("/ui/src/i18n.tsx")) return code.replace("() => 'zh-Hans' as const", "getLocale");
    } }],
  });
  ui = await server.ssrLoadModule("@synwevo/ui");
  client = await server.ssrLoadModule("@synwevo/auth-client");
  settings = await server.ssrLoadModule("/src/apps/agent/Settings.tsx");
  messages = await server.ssrLoadModule("/src/apps/agent/messages.ts");
});
after(async () => { ui?.setLocale("zh-Hans"); await server?.close(); });
const workspace = { workspace_id: "fixture-workspace", name: "Fixture", role: "owner", kind: "organization",
  processing: "any", explicit: true, can_change: true, providers: ["anthropic"], providers_explicit: false };
const access = { effort: "medium", prefer_personal_key: true, memory_enabled: true, personal_key: { set: true },
  personal_key_supported: false, model_configured: true, platform_configured: true,
  active: { provider: "deepseek", credential: "platform", model: "fixture-model" }, efforts: ["low", "medium", "high"] };
function render(name: string, props: object, locale = "en") {
  ui.setLocale(locale);
  return renderToStaticMarkup(React.createElement(settings[name], { onChanged() {}, ...props }));
}
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const providerInputs = (html: string) => [...html.matchAll(/<input\b[^>]*name="provider"[^>]*>/g)].map(m => m[0]);

test("legacy policy displays Anthropic-only consent and does not preselect DeepSeek", () => {
  const html = render("PolicyRow", { workspace });
  const inputs = providerInputs(html);
  assert.equal(inputs.length, 2);
  assert.match(inputs.find(v => v.includes('value="anthropic"'))!, /checked/);
  assert.doesNotMatch(inputs.find(v => v.includes('value="deepseek"'))!, /checked/);
  assert.match(text(html), /Legacy.*Anthropic/);
});

test("explicit empty consent is visible and never appears checked", () => {
  const html = render("PolicyRow", { workspace: { ...workspace, providers: [], providers_explicit: true } });
  assert.equal(providerInputs(html).length, 2);
  assert.ok(providerInputs(html).every(v => !v.includes("checked")));
  assert.match(text(html), /No providers allowed/);
});

test("a viewer sees the actual provider scope without policy controls", () => {
  const html = render("PolicyRow", { workspace: { ...workspace, role: "viewer", can_change: false,
    providers: ["deepseek"], providers_explicit: true } });
  assert.match(text(html), /DeepSeek/);
  assert.doesNotMatch(html, /<form|<select|<input/);
});

test("paid DeepSeek shows the provider and disables personal-key entry while keeping removal", () => {
  for (const locale of ["zh-Hans", "zh-Hant", "en"]) {
    const html = render("AccessForm", { settings: access }, locale);
    assert.match(text(html), /DeepSeek/);
    assert.match(html, /<input[^>]*type="password"[^>]*disabled/);
    assert.match(html, /<input[^>]*type="checkbox"[^>]*disabled[^>]*checked/);
    const removal = html.match(/<button[^>]*class="btn danger"[^>]*>/)?.[0];
    assert.ok(removal && !removal.includes("disabled"));
    if (locale === "en") {
      assert.match(text(html), /does not support personal API keys/);
      assert.doesNotMatch(text(html), /[㐀-鿿]/);
    }
  }
});

test("all policy guidance and the stable personal-key refusal are translated", () => {
  for (const locale of ["zh-Hans", "zh-Hant", "en"]) {
    const html = render("PolicyRow", { workspace }, locale);
    const error = render("ErrorNotice", { error: new client.ApiError(409, "model_personal_key_unsupported", "fixture secret must not render") }, locale);
    assert.doesNotMatch(error, /fixture secret/);
    if (locale === "en") {
      assert.doesNotMatch(text(html + error), /[㐀-鿿]/);
      assert.match(text(error), /does not support personal API keys/);
    }
  }
  for (const value of Object.values(messages.agentMessages) as any[]) {
    assert.ok(value.en && value["zh-Hant"]);
  }
});
