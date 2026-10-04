import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { blankField, checkAnswers, draftRefusals, evaluate, owningField, visibleFields, type Definition } from "../src/apps/forms/logic.ts";
import { refusalText, requestHeading } from "../src/apps/forms/text.ts";

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

// ------------------------------------------------------------------ the editor (acceptance D1)
// The server refuses a draft in which any field or sub-field has no name (`Field.label`, at least
// one character): `test_forms_regressions.py` pins where it says so.
const SAY = (k: string, p: Record<string, any> = {}) => k.replace(/\{(\w+)\}/g, (_, n) => String(p[n]));
const PALETTE = ["text", "textarea", "number", "money", "date", "datetime", "select", "multiselect", "boolean", "file",
  "repeat", "section"] as const;

test("every field the editor adds saves as a draft, a repeating group's sub-field included", () => {
  for (const type of PALETTE) {
    const f = blankField(type, "Label", "Detail 1");
    for (const x of [f, ...(f.fields ?? [])]) assert.ok(x.label.length >= 1, `${type}: ${x.id} has no name`);
    if (type === "repeat") assert.equal(f.fields?.length, 1);
  }
});

test("a refused save names the group and the sub-field without a name", () => {
  const group = blankField("repeat", "Line items", "Detail 1");
  group.fields![0].label = "";
  const defn: Definition = { fields: [blankField("text", "Name", ""), group] };
  // The shape `synwevo_core.capabilities._validation_detail` sends (loc as strings).
  const detail = { fields: [{ loc: ["definition", "fields", "1", "fields", "0", "label"], type: "string_too_short", message: "…" }] };
  const found = draftRefusals(defn, detail);
  assert.deepEqual(found, [{ part: "label", field: group.fields![0].id, parent: group.id, index: 1, sub: 0 }]);
  assert.equal(refusalText(SAY, found[0], defn), "「Line items」里的第 1 个字段还没有名称");
  assert.equal(owningField(defn, group.fields![0].id)?.id, group.id);
  // A cleared top-level question is named by its place and type, since it has no name to show.
  defn.fields[0].label = "";
  const top = draftRefusals(defn, { fields: [{ loc: ["definition", "fields", "0", "label"] }] });
  assert.equal(refusalText(SAY, top[0], defn), "第 1 个字段（单行文本）还没有填写问题");
  // Unknown places still say something, never nothing.
  assert.deepEqual(draftRefusals(defn, { fields: [{ loc: ["definition"] }] }), [{ part: "other" }]);
});

// ------------------------------------------------------------------ request pages (acceptance D4)
test("the public link's request page is headed by its form, never left empty", () => {
  // `forms.request.get` of the public link's request: no title of its own, the form's title beside it.
  const shown = requestHeading(SAY, { kind: "public", title: "", form_title: "Expense claim" });
  assert.equal(shown, "「Expense claim」的公开填写");
  assert.equal(requestHeading(SAY, { kind: "personal", title: "", form_title: "Expense claim" }), "Expense claim");
  assert.equal(requestHeading(SAY, { kind: "personal", title: "Q3 claim", form_title: "Expense claim" }), "Q3 claim");
});
