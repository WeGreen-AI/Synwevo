import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { checkAnswers, evaluate, visibleFields } from "../src/apps/forms/logic.ts";

const VECTORS = JSON.parse(readFileSync(join(import.meta.dirname, "forms-vectors.json"), "utf8"));

for (const c of VECTORS.cases) {
  test(`vector: ${c.name}`, () => {
    const got = checkAnswers(c.definition, c.answers).map((e) => [e.field, e.code]).sort();
    assert.deepEqual(got, [...c.errors].sort());
  });
}

test("conditions compare decimals exactly and hidden fields stop counting", () => {
  assert.equal(evaluate({ field: "a", op: "gt", value: "0.3" }, { a: "0.30000000000000001" }), true);
  assert.equal(evaluate({ field: "a", op: "eq", value: "1.0" }, { a: "1" }), true);
  const defn = { fields: [
    { id: "f_a0000001", type: "boolean", label: "A" },
    { id: "f_b0000001", type: "text", label: "B", visible_if: { field: "f_a0000001", op: "eq", value: true } },
    { id: "f_c0000001", type: "text", label: "C", visible_if: { field: "f_b0000001", op: "filled" } },
  ] } as any;
  assert.deepEqual([...visibleFields(defn, { f_a0000001: false, f_b0000001: "typed" })], ["f_a0000001"]);
});
