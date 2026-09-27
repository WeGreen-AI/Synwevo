import { test } from "node:test";
import assert from "node:assert/strict";
import { EMOJI_GROUPS, RECENT_MAX, pushRecent, readRecent } from "../src/lib/emoji.ts";

test("the catalogue has eight named groups, every item is a non-empty string and no item repeats inside a group", () => {
  assert.equal(EMOJI_GROUPS.length, 8);
  for (const g of EMOJI_GROUPS) {
    assert.ok(g.name.length > 0 && g.items.length >= 40, g.name);
    assert.equal(new Set(g.items).size, g.items.length, `${g.name} has duplicates`);
    // keycaps (0️⃣…9️⃣, #️⃣, *️⃣) carry an ASCII base character; nothing may be plain ASCII text
    for (const e of g.items) assert.ok(typeof e === "string" && e.length > 0 && !/^[\x20-\x7e]+$/.test(e), `${g.name}: ${JSON.stringify(e)}`);
  }
});

test("recently used: newest first, no repeats, capped; unreadable storage reads as empty", () => {
  let list: string[] = [];
  for (let i = 0; i < RECENT_MAX + 5; i++) list = pushRecent(list, String.fromCodePoint(0x1f600 + i));
  assert.equal(list.length, RECENT_MAX);
  list = pushRecent(list, list[3]);
  assert.equal(list[0], list.length && list[0]);
  assert.equal(new Set(list).size, list.length);
  assert.deepEqual(readRecent(null), []);
  assert.deepEqual(readRecent({ getItem: () => "not json" }), []);
  assert.deepEqual(readRecent({ getItem: () => JSON.stringify(["👍", 3, "❤️"]) }), ["👍", "❤️"]);
});
