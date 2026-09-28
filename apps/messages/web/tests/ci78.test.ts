import { test } from "node:test";
import assert from "node:assert/strict";
import { ALL_ORGS, conversationsPath, readScope, scopeKey, writeScope } from "../src/lib/scope.ts";
import { coverSquare } from "../src/lib/avatar.ts";
import { countsAsUnread, filterConversations } from "../src/lib/thread.ts";
import type { Conversation } from "../src/types.ts";

function memory(): { getItem: (k: string) => string | null; setItem: (k: string, v: string) => void; removeItem: (k: string) => void; data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: k => data.get(k) ?? null, setItem: (k, v) => { data.set(k, v); }, removeItem: k => { data.delete(k); } };
}

test("the organization scope is remembered per account and dropped when it no longer applies", () => {
  const s = memory();
  writeScope(s, "u_a", "org_2");
  assert.equal(s.data.get(scopeKey("u_a")), "org_2");
  assert.equal(readScope(s, "u_a", null), "org_2", "kept while the organizations are not known yet");
  assert.equal(readScope(s, "u_a", ["org_1", "org_2"]), "org_2");
  assert.equal(readScope(s, "u_a", ["org_1", "org_3"]), ALL_ORGS, "an organization the person left is not applied");
  assert.equal(readScope(s, "u_a", ["org_2"]), ALL_ORGS, "one organization: no scope at all");
  assert.equal(readScope(s, "u_b", ["org_1", "org_2"]), ALL_ORGS, "another account on this browser has its own choice");
  writeScope(s, "u_a", ALL_ORGS);
  assert.equal(s.data.has(scopeKey("u_a")), false);
  // storage that throws (a private window, blocked site data) means "every organization", never an error
  const broken = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); }, removeItem: () => { throw new Error("denied"); } };
  assert.equal(readScope(broken, "u_a", ["org_1", "org_2"]), ALL_ORGS);
  assert.doesNotThrow(() => writeScope(broken, "u_a", "org_1"));
  assert.equal(readScope(null, "u_a", null), ALL_ORGS);
  // the server filters: the scope is a query parameter, escaped
  assert.equal(conversationsPath("/api/messaging", ""), "/api/messaging/conversations");
  assert.equal(conversationsPath("/api/messaging", "org 1&x"), "/api/messaging/conversations?organization_id=org%201%26x");
});

test("a group picture uses the centred square of what was chosen", () => {
  assert.deepEqual(coverSquare(400, 300), { sx: 50, sy: 0, side: 300 });
  assert.deepEqual(coverSquare(300, 401), { sx: 0, sy: 50, side: 300 });
  assert.deepEqual(coverSquare(64, 64), { sx: 0, sy: 0, side: 64 });
  assert.deepEqual(coverSquare(0, 0), { sx: 0, sy: 0, side: 1 });
});

test("未读 is one rule for the row, the badge and the filter; the open conversation stays in view after it is read", () => {
  const T = 1_800_000_000;
  const base: Conversation = { id: "a", organization_id: "o", kind: "group", name: "A", description: null, archived: false, last_seq: 5, announcement: null, announcement_at: null,
    last_message_at: T, me: { role: "member", joined_seq: 0, last_read_seq: 5, muted: false, pinned: false, notify: "all" }, unread: 0, peer: null, members: [], member_count: 2,
    last_message: null, unread_mentions: 0 };
  const unread = { ...base, id: "u", unread: 2, unread_mentions: 1 };
  const marked = { ...base, id: "k", me: { ...base.me, marked_unread: true } };
  const timed = { ...base, id: "t", unread: 3, me: { ...base.me, muted_until: T + 60 } };
  const expired = { ...base, id: "e", unread: 1, me: { ...base.me, muted_until: T - 60 } };
  const read = { ...base, id: "r" };
  assert.deepEqual([unread, marked, timed, expired, read].map(c => countsAsUnread(c, T)), [true, true, false, true, false]);
  const all = [unread, marked, timed, expired, read];
  assert.deepEqual(filterConversations(all, "unread", "", undefined, T).map(c => c.id), ["u", "k", "e"]);
  assert.deepEqual(filterConversations(all, "unread", "", "r", T).map(c => c.id), ["u", "k", "e", "r"], "the conversation being read keeps its row");
  assert.deepEqual(filterConversations(all, "all", "", "r", T).map(c => c.id), ["u", "k", "t", "e", "r"]);
});
