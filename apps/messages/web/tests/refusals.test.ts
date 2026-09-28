/** CI-83: every refusal the messaging server can give a person reaches them as a sentence in
 *  their language — never as "请求未完成（code）". The shared presentation shows a server sentence
 *  only when this shell's catalogue has it, and words the numbered ones from `detail` through
 *  `lib/refusals.ts`; this test reads the server's `ServiceError(...)` calls and holds both to it.
 *  A refusal only an API client can cause (a malformed field this shell never sends) is listed
 *  below instead, so adding a server refusal means deciding which of the two it is. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { setLocale } from "@synwevo/ui/i18n-core";
import { apiFailureText } from "@synwevo/auth-client/api-messages";
import { messages } from "../src/messages.messages.ts";
import { REFUSAL_TEMPLATES } from "../src/lib/refusals.ts";
import { mayRetry } from "../src/lib/objects.ts";
import { ApiError } from "@synwevo/auth-client/api";

const ROOT = join(import.meta.dirname, "..", "..", "..", "..");
const SERVER = [
  "packages/core/synwevo_core/messaging.py", "packages/core/synwevo_core/messaging_bridge.py",
  "packages/core/synwevo_core/messaging_presence.py", "packages/core/synwevo_core/messaging_search.py",
  ...readdirSync(join(ROOT, "apps/messages/synwevo_messages")).filter(f => f.endsWith(".py")).map(f => `apps/messages/synwevo_messages/${f}`),
];

/** Field-shape refusals: the shell sends these fields from its own controls, so a person cannot
 *  cause them; an API client reads the sentence and the code. */
const API_ONLY = new Set([
  "client_id 无效", "conversation_ids 必须是会话 id 列表", "duration_ms 必须是整数毫秒", "history_from 只能是 join 或 all",
  "member_ids 必须是用户 id 列表", "mention_ids 无效", "muted_until 必须是时间戳（秒）", "muted_until 需要是未来一年内的时间",
  "notify 只能是 all、mentions 或 none", "seq 必须是整数", "state 只能是 start 或 stop", "user_ids 必须是 1–50 个用户 id",
  "只有语音附件可以声明时长", "只能修改 name 和 description", "只能提交 conversation_ids 和 client_id", "只能提交 image_base64",
  "只能提交 reason 和 note", "只能提交 user_ids 和 history_from", "只能设置 muted、pinned、notify、muted_until",
  "只能设置 show_presence 和 read_receipts", "只能设置 text", "处理结果只能是 removed 或 dismissed", "消息包含不支持的字段",
  "角色只能是 admin 或 member", "举报原因无效",
]);
/** Codes the shared presentation already words for every shell (`API_ERROR_SOURCE`). */
const SHARED_CODES = new Set(["unauthorized", "too_many_attempts"]);
/** Numbered refusals no person can reach: an unknown application in the address, a boolean field,
 *  a recorder-produced duration; `moved` is handled by code where it can occur (Messages.tsx). */
const API_ONLY_NUMBERED = new Set(["unknown_app", "moved", "invalid_field:duration_ms", "invalid_field:key"]);

type Refusal = { file: string; status: number; code: string; fstring: boolean; text: string; field: string | null };
function refusals(): Refusal[] {
  const out: Refusal[] = [];
  for (const file of SERVER) {
    const src = readFileSync(join(ROOT, file), "utf8");
    for (const m of src.matchAll(/ServiceError\(\s*(\d{3})\s*,\s*"([a-z_]+)"\s*,\s*(f?)"((?:[^"\\\n]|\\.)*)"([^\n]*)/g)) {
      const field = /"field":\s*"([a-z_]+)"/.exec(m[5])?.[1] ?? (/\{key\}/.test(m[4]) ? "key" : null);
      out.push({ file, status: Number(m[1]), code: m[2], fstring: m[3] === "f", text: m[4], field });
    }
  }
  return out;
}

test("the scan finds the server's refusals (guards the pattern itself)", () => {
  const all = refusals();
  assert.ok(all.length > 80, `only ${all.length} refusals found`);
  assert.ok(all.some(r => r.code === "owner_must_transfer"));
  assert.ok(all.some(r => r.code === "recall_window_closed" && r.fstring));
});

test("every refusal a person can meet is in the catalogue or worded from its detail", () => {
  const keys = new Set(Object.keys(messages));
  const missing: string[] = [];
  for (const r of refusals()) {
    if (SHARED_CODES.has(r.code)) continue;
    if (r.fstring) {
      if (API_ONLY_NUMBERED.has(r.code) || API_ONLY_NUMBERED.has(`${r.code}:${r.field}`)) continue;
      if (!Object.hasOwn(REFUSAL_TEMPLATES, r.code)) missing.push(`${r.file}: ${r.code} (numbered, no template): ${r.text}`);
      continue;
    }
    if (API_ONLY.has(r.text)) continue;
    if (!keys.has(r.text)) missing.push(`${r.file}: ${r.code}: ${r.text}`);
  }
  assert.deepEqual(missing, []);
});

test("the API-only list names nothing the server no longer says", () => {
  const said = new Set(refusals().map(r => r.text));
  assert.deepEqual([...API_ONLY].filter(s => !said.has(s)), []);
});

test("numbered refusals read in every language, from the detail the server sends", () => {
  const cases: [string, number, object, Record<string, string>][] = [
    ["recall_window_closed", 403, { recall_window_s: 120 }, { "zh-Hans": "发出超过 2 分钟的消息不能再撤回，仍可以「删除（仅自己）」", en: "Messages older than 2 minutes can no longer be recalled; you can still delete them for yourself" }],
    ["recall_window_closed", 403, { recall_window_s: 90 }, { "zh-Hant": "發出超過 90 秒的訊息不能再撤回，仍可以「刪除（僅自己）」" }],
    ["edit_window_closed", 403, { edit_window_s: 900 }, { en: "Messages older than 15 minutes can no longer be edited" }],
    ["too_many_members", 400, { limit: 200 }, { en: "A group can have at most 200 members" }],
    ["file_too_large", 413, { limit: 20 * 1024 * 1024 }, { en: "The attachment is over the 20 MB limit" }],
    ["file_too_large", 413, { limit: 10 * 1024 * 1024, kind: "voice" }, { "zh-Hans": "语音超过 10 MB 上限" }],
    ["invalid_field", 400, { field: "name", limit: 80 }, { en: "A group name needs 1–80 characters" }],
    ["too_many_targets", 400, { field: "conversation_ids", limit: 5 }, { "zh-Hant": "一次最多轉發到 5 個會話" }],
  ];
  try {
    for (const [code, status, detail, expected] of cases) {
      for (const [locale, text] of Object.entries(expected)) {
        setLocale(locale as "en");
        assert.equal(apiFailureText({ code, status, detail, originalMessage: "服务器原句" }), text, `${code} ${locale}`);
      }
    }
    // a template without its number falls back to the server's sentence, catalogued or not
    setLocale("en");
    assert.equal(apiFailureText({ code: "invalid_field", status: 400, detail: { field: "user_ids" }, originalMessage: "请用「退出群聊」离开" }), "Use “Leave group” to leave");
    assert.equal(apiFailureText({ code: "owner_must_transfer", status: 409, originalMessage: "群主退出前请先把群主交接给其他成员，或归档群聊" }),
      "Before leaving, hand over ownership to another member or archive the group");
  } finally { setLocale("zh-Hans"); }
});

test("the object page offers a retry only when trying again can change the answer", () => {
  assert.equal(mayRetry(new TypeError("offline")), true);
  assert.equal(mayRetry(new ApiError(503, "unavailable", "x")), true);
  assert.equal(mayRetry(new ApiError(429, "too_many_attempts", "x")), true);
  assert.equal(mayRetry(new ApiError(403, "forbidden", "x", undefined, true)), true);   // something in front of Synwevo
  assert.equal(mayRetry(new ApiError(409, "no_organization", "x")), false);
  assert.equal(mayRetry(new ApiError(409, "archived", "x")), false);
});
