import assert from "node:assert/strict";
import { test } from "node:test";
import { charDiff } from "../src/lib/diff.ts";

const join = (runs: { kind: string; text: string }[]) => runs.map((r) => (r.kind === "equal" ? r.text : r.kind === "ins" ? `[+${r.text}]` : `[-${r.text}]`)).join("");

test("typing at the end of a block is one insertion after the unchanged text", () => {
  assert.equal(join(charDiff("客户：【客户】", "客户：第一次第二次【客户】")), "客户：[+第一次第二次]【客户】");
});

test("a replaced word shows the deletion and the insertion side by side", () => {
  assert.equal(join(charDiff("总价含税", "总价不含税")), "总价[+不]含税");
  assert.equal(join(charDiff("交付期 30 天", "交付期 45 天")), "交付期 [-30][+45] 天");
});

test("empty sides are a whole insertion or a whole deletion; nothing to diff is nothing", () => {
  assert.deepEqual(charDiff("", "新段落"), [{ kind: "ins", text: "新段落" }]);
  assert.deepEqual(charDiff("旧段落", ""), [{ kind: "del", text: "旧段落" }]);
  assert.deepEqual(charDiff("", ""), []);
  assert.deepEqual(charDiff("一样", "一样"), [{ kind: "equal", text: "一样" }]);
});

test("code points stay whole (an emoji is not split into surrogate halves)", () => {
  const runs = charDiff("签字 ✍️", "签字 ✅");
  assert.ok(runs.every((r) => Array.from(r.text).every((ch) => !/[\uD800-\uDFFF]/.test(ch) || ch.length === 2)));
  assert.equal(join(runs), "签字 [-✍️][+✅]");
});
