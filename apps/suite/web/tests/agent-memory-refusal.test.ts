/** A memory refused by an input content rule (agent/safety.py `content_blocked_memory`) reads as a
 * sentence in every language, where the person meets it: Agent settings (adding or editing a
 * memory) and the chat's action card (a memory the model proposed and the person confirmed). */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { testServer } from "./_vite.ts";

const SENTENCE = "这条记忆触发了内容安全规则，没有保存。请修改后再保存。";
let server: any, errors: any, chat: any, settings: any;
before(async () => {
  server = await testServer();
  errors = await server.ssrLoadModule("/src/apps/agent/errors.tsx");
  chat = await server.ssrLoadModule("/src/apps/agent/messages.ts");
  settings = await server.ssrLoadModule("/src/apps/agent/settings/messages.ts");
});
after(async () => { await server?.close(); });

test("the chat's action card words a refused memory in every language", () => {
  const zh = errors.agentErrorText({ code: "content_blocked_memory" }, (key: string) => key);
  assert.equal(zh, SENTENCE);
  const entry = chat.agentMessages[zh];
  assert.ok(entry, "missing chat translations");
  assert.match(entry.en, /content safety rule/);
  assert.match(entry.en, /not saved/);
  assert.match(entry["zh-Hant"], /沒有儲存/);
});

test("Agent settings words a refused memory in every language", () => {
  const source = readFileSync(fileURLToPath(new URL("../src/apps/agent/Settings.tsx", import.meta.url)), "utf8");
  assert.ok(source.includes(`content_blocked_memory: "${SENTENCE}"`), "settings does not map the code");
  const entry = settings.agentSettingsMessages[SENTENCE];
  assert.ok(entry, "missing settings translations");
  assert.match(entry.en, /not saved/);
  assert.match(entry["zh-Hant"], /內容安全規則/);
});
