/** The refusal sentences for an upstream 402 and a closed model route (gateway.py), in every
 * language. The operator's provider balance is never presented as the person's balance. */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

let server: any, errors: any, messages: any;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), configFile: false,
    logLevel: "error", appType: "custom", server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  errors = await server.ssrLoadModule("/src/apps/agent/errors.tsx");
  messages = await server.ssrLoadModule("/src/apps/agent/messages.ts");
});
after(async () => { await server?.close(); });

test("an upstream 402 or a closed route reads as unavailable and not charged, never as the person's balance", () => {
  for (const code of ["provider_balance", "billing_risk_breaker_open", "billing_provider_model_missing"]) {
    const zh = errors.agentErrorText({ code }, (key: string) => key);
    assert.match(zh, /未扣费/, code);
    assert.doesNotMatch(zh, /余额不足/, code);
    const entry = messages.agentMessages[zh];
    assert.ok(entry, `missing translations for ${code}`);
    assert.match(entry.en, /not charged/, code);
    assert.doesNotMatch(entry.en, /insufficient|your balance/i, code);
    assert.match(entry["zh-Hant"], /未扣費/, code);
    assert.doesNotMatch(entry["zh-Hant"], /餘額不足/, code);
  }
});
