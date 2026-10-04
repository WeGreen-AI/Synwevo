import { test } from "node:test";
import assert from "node:assert/strict";
import { stepLabels, type Step } from "../src/apps/dataprep/model.ts";

// Explicit fixtures: recipe steps as the editor holds them; not a stand-in for browser acceptance.
const steps: Step[] = [
  { id: "s_trim0001", type: "trim", columns: ["note"] },
  { id: "s_filter01", type: "filter", condition: { column: "region", op: "ne", value: "Test", numeric: false } },
  { id: "s_cast0001", type: "cast", column: "amount", to: "decimal", scale: 2, rounding: "reject", offset: null },
];
const keyOnly = (key: string) => key;

test("an issue's step reads as its position and type, the same in the editor preview and the run page", () => {
  // Release acceptance: the editor's preview issue list said only "步骤 / Step" for every row.
  assert.deepEqual(stepLabels(steps, keyOnly), { s_trim0001: "1. 清理空白", s_filter01: "2. 过滤", s_cast0001: "3. 类型转换" });
  assert.deepEqual(stepLabels([{ id: "s_future01", type: "future" }], keyOnly), { s_future01: "1. 步骤" });
  assert.deepEqual(stepLabels([], keyOnly), {});
});
