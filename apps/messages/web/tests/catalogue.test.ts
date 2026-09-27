import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { validateMessages } from "@synwevo/ui/i18n-core";
import { messages } from "../src/messages.messages.ts";
import { EMOJI_GROUPS } from "../src/lib/emoji.ts";

const SRC = join(import.meta.dirname, "..", "src");
function walk(dir: string, out: string[] = []): string[] {
  for (const f of readdirSync(dir)) { const p = join(dir, f); if (statSync(p).isDirectory()) walk(p, out); else if (/\.tsx?$/.test(p)) out.push(p); }
  return out;
}

test("every entry has en and zh-Hant with the same parameters", () => {
  assert.deepEqual(validateMessages(messages), []);
});

test("every Chinese string literal in the shell, and every emoji group name, is in the catalogue", () => {
  const keys = new Set(Object.keys(messages));
  // ApiError diagnostics are not rendered as-is (the shared client presents them by code)
  const notUi = new Set(["上传失败：网络错误", "服务没有返回会话编号"]);
  const missing: string[] = [];
  for (const f of walk(SRC)) {
    if (f.endsWith("messages.messages.ts") || f.endsWith("emoji.ts")) continue;
    const src = readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    for (const m of src.matchAll(/"([^"\\\n]*[㐀-鿿][^"\\\n]*)"/g)) if (!keys.has(m[1]) && !notUi.has(m[1])) missing.push(`${f}: ${m[1]}`);
  }
  for (const g of EMOJI_GROUPS) if (!keys.has(g.name)) missing.push(`emoji group: ${g.name}`);
  assert.deepEqual(missing, []);
});

test("vocabulary: the shell never claims a state the server has no fact for (delivery, last seen)", () => {
  // recall, read receipts, presence and typing are server facts since CI-74 (§7); "delivered" and
  // "last seen" are not and must not appear in any language
  const all = Object.entries(messages).flatMap(([k, v]) => [k, v.en, v["zh-Hant"]]) as string[];
  for (const s of all) {
    assert.ok(!/已送达|最后在线|最後上線|已送達/.test(s), s);
    assert.ok(!/delivered|last seen/i.test(s), s);
  }
});
