import { test } from "node:test";
import assert from "node:assert/strict";
import { GROUP_WINDOW_S, colorSlot, filterConversations, highlightParts, layout, linksIn, maxSeq, mergeMessages, preview, snippet } from "../src/lib/thread.ts";
import { dayLabel, listTime } from "../src/lib/time.ts";
import type { Conversation, Message } from "../src/types.ts";

const T0 = Date.UTC(2026, 8, 28, 9, 0, 0) / 1000;
function msg(seq: number, sender: string, at: number, extra: Partial<Message> = {}): Message {
  return { id: `m${seq}`, conversation_id: "cv_1", seq, sender_id: sender, sender: sender, kind: "text", body: `b${seq}`, reply_to: null, mentions: [],
    edited_at: null, deleted: false, created_at: at, client_id: null, reactions: [], attachment: null, ...extra };
}

test("messages from one sender within the window share a head; a gap, a sender change, a system line or a new day starts a new group", () => {
  const items = [
    msg(1, "a", T0), msg(2, "a", T0 + 60), msg(3, "b", T0 + 90), msg(4, "b", T0 + 90 + GROUP_WINDOW_S + 1),
    msg(5, "b", T0 + 90 + GROUP_WINDOW_S + 2, { kind: "system", body: "", system: { event: "renamed" } }), msg(6, "b", T0 + 90 + GROUP_WINDOW_S + 3),
    msg(7, "b", T0 + 86_400 * 2),
  ];
  const rows = layout(items, "me", null);
  const types = rows.map(r => r.type);
  assert.deepEqual(types.filter(t => t === "day").length, 2, "one day separator per calendar day");
  const heads = rows.filter(r => r.type === "message").map(r => (r.type === "message" ? [r.message.seq, r.head, r.tail] : null));
  // 3→4 is one second past the window, so both stand alone; 4→5 is a system line, so 6 starts afresh
  assert.deepEqual(heads, [[1, true, false], [2, false, true], [3, true, true], [4, true, true], [6, true, true], [7, true, true]]);
  assert.equal(rows.find(r => r.type === "unread"), undefined, "no divider without a read position");
});

test("the unread divider sits before the first message above the reader's position that is not their own", () => {
  const items = [msg(1, "a", T0), msg(2, "me", T0 + 10), msg(3, "a", T0 + 20), msg(4, "a", T0 + 30)];
  const rows = layout(items, "me", 2);
  const i = rows.findIndex(r => r.type === "unread");
  assert.ok(i > 0);
  assert.equal(rows[i + 1].type, "message");
  assert.equal((rows[i + 1] as any).message.seq, 3);
  // the message before the divider closes its group, the one after opens a new one
  assert.equal((rows[i - 1] as any).tail, true);
  assert.equal((rows[i + 1] as any).head, true);
  assert.equal(layout(items, "me", 4).some(r => r.type === "unread"), false, "nothing unread → no divider");
  const ownOnly = layout([msg(1, "a", T0), msg(2, "me", T0 + 10)], "me", 1);
  assert.equal(ownOnly.some(r => r.type === "unread"), false, "the reader's own messages are never 'unread'");
});

test("merging replaces the pending copy by the stored message with the same client id and keeps seq order", () => {
  const pending: Message = { ...msg(0, "me", T0 + 100), id: "pending-c1", client_id: "c1", pending: true };
  const cur = [msg(1, "a", T0), pending];
  const stored = { ...msg(2, "me", T0 + 101), client_id: "c1" };
  const out = mergeMessages(cur, [stored]);
  assert.deepEqual(out.map(m => m.id), ["m1", "m2"]);
  assert.equal(out[1].pending, false);
  assert.equal(maxSeq([...out, pending]), 2, "a pending message has no seq");
  const edited = mergeMessages(out, [{ ...stored, body: "changed", edited_at: T0 + 200 }]);
  assert.equal(edited[1].body, "changed");
});

test("the list preview names the sender in a group and only marks the reader's own line in a private conversation", () => {
  const base = { id: "cv", organization_id: "o", name: "X", description: null, archived: false, last_seq: 3, announcement: null, announcement_at: null, last_message_at: T0,
    me: { role: "member", joined_seq: 0, last_read_seq: 0, muted: false, pinned: false, notify: "all" as const }, unread: 1, peer: null, members: [], member_count: 2 };
  const last = { seq: 3, sender_id: "a", sender: "Ann", kind: "text", body: "hi", deleted: false, created_at: T0, attachment: false };
  const group: Conversation = { ...base, kind: "group", last_message: last };
  const dm: Conversation = { ...base, kind: "dm", last_message: last };
  assert.deepEqual(preview(group, "me"), { kind: "text", sender: "Ann", body: "hi" });
  assert.deepEqual(preview(dm, "me"), { kind: "text", sender: null, body: "hi" });
  assert.deepEqual(preview({ ...dm, last_message: { ...last, sender_id: "me" } }, "me"), { kind: "text", sender: "me", body: "hi" });
  assert.deepEqual(preview({ ...group, last_message: { ...last, deleted: true, body: "" } }, "me"), { kind: "deleted", sender: "Ann" });
  assert.deepEqual(preview({ ...group, last_message: { ...last, body: "", attachment: true } }, "me"), { kind: "attachment", sender: "Ann" });
  assert.deepEqual(preview({ ...group, archived: true }, "me"), { kind: "archived" });
  assert.deepEqual(preview({ ...group, last_message: null }, "me"), { kind: "none" });
  // filters: archived only under its own filter; unread ignores muted conversations
  const muted = { ...group, id: "m", unread: 5, me: { ...group.me, muted: true } };
  const archived = { ...group, id: "ar", archived: true };
  assert.deepEqual(filterConversations([group, muted, archived], "all", "").map(c => c.id), ["cv", "m"]);
  assert.deepEqual(filterConversations([group, muted, archived], "unread", "").map(c => c.id), ["cv"]);
  assert.deepEqual(filterConversations([group, muted, archived], "archived", "").map(c => c.id), ["ar"]);
  assert.deepEqual(filterConversations([group, { ...muted, name: "Zed" }], "all", "ze").map(c => c.id), ["m"]);
});

test("search highlighting is case-insensitive and snippets stay around the first hit", () => {
  assert.deepEqual(highlightParts("Hello hello", "HELLO"), [{ text: "Hello", hit: true }, { text: " ", hit: false }, { text: "hello", hit: true }]);
  assert.deepEqual(highlightParts("abc", ""), [{ text: "abc", hit: false }]);
  const long = "x".repeat(300) + "needle" + "y".repeat(300);
  const s = snippet(long, "needle", 60);
  assert.ok(s.includes("needle") && s.length <= 62 + 2);
  assert.equal(snippet("short", "sh"), "short");
});

test("links: the first three distinct http(s) links; colour slots are stable and within 1..8", () => {
  assert.deepEqual(linksIn("see https://a.example/x and https://a.example/x then http://b.example/ and https://c.example https://d.example"),
    ["https://a.example/x", "http://b.example/", "https://c.example"]);
  assert.equal(colorSlot("usr_1"), colorSlot("usr_1"));
  for (const id of ["a", "usr_9f", "", "长名字"]) { const s = colorSlot(id); assert.ok(s >= 1 && s <= 8); }
});

test("day labels: today, yesterday, the weekday within a week, else a date", () => {
  const now = new Date(2026, 8, 28, 15, 0, 0).getTime();          // a local-time noon-ish instant
  const at = (daysAgo: number) => new Date(2026, 8, 28 - daysAgo, 8, 0, 0).getTime() / 1000;
  assert.deepEqual(dayLabel(at(0), now), { key: "今天" });
  assert.deepEqual(dayLabel(at(1), now), { key: "昨天" });
  assert.equal(dayLabel(at(3), now).key, "weekday");
  assert.equal(dayLabel(at(9), now).key, "date");
  assert.equal(listTime(at(0), now).key, "time");
  assert.deepEqual(listTime(at(1), now), { key: "昨天" });
});
