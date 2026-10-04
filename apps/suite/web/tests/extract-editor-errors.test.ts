/** Release acceptance defect 4: the template editor answered a minimum above the maximum, or a
 *  minimum of 1e3, with the generic "操作未完成…" sentence. A refusal now names its input.
 *  The details below have the shape the operation layer returns (apps/suite/tests/test_extract.py
 *  asserts the server side of it); they are fixtures, not a server. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { EDITOR_ERROR_KEYS, FALLBACK, editorProblems, problemAt } from "../src/apps/extract/editorErrors.ts";
import { extract } from "../src/apps/extract/messages.ts";

const refusal = (...fields: { loc: string[]; type: string }[]) => ({ code: "invalid_input", detail: { fields } });

test("each refusal is placed beside the input it concerns", () => {
  const problems = editorProblems(refusal(
    { loc: ["definition", "fields", "1", "maximum"], type: "bounds_order" },
    { loc: ["definition", "fields", "0", "minimum"], type: "string_pattern_mismatch" },
    { loc: ["definition", "fields", "2", "rule", "prefix"], type: "prefix_required" },
    { loc: ["title"], type: "string_too_short" },
  ));
  assert.ok(problems && problems.placed);
  assert.equal(problemAt(problems, 1, "maximum"), "最大值不能小于最小值");
  assert.equal(problemAt(problems, 0, "minimum"), "请填写不带指数的十进制数，例如 1000 或 -2.5");
  assert.equal(problemAt(problems, 2, "rule.prefix"), "请填写行首文字（不能只有空格）");
  assert.equal(problems.title, "请填写此项");
  assert.equal(problemAt(problems, 0, "maximum"), undefined);
});

test("an unknown type still names its input; an unplaceable one is reported as such", () => {
  const problems = editorProblems(refusal(
    { loc: ["definition", "fields", "3", "label"], type: "something_new" },
    { loc: ["definition", "fields", "4"], type: "value_error" },
    { loc: ["definition", "fields"], type: "too_long" },
  ));
  assert.equal(problemAt(problems, 3, "label"), FALLBACK);
  assert.equal(problemAt(problems, 4, "field"), FALLBACK);
  assert.equal(problems?.placed, false);
});

test("other refusals are not field refusals", () => {
  assert.equal(editorProblems({ code: "draft_conflict", detail: {} }), null);
  assert.equal(editorProblems(new Error("network")), null);
  assert.equal(editorProblems(undefined), null);
});

test("every sentence the editor can show is catalogued in three languages", () => {
  for (const key of EDITOR_ERROR_KEYS) {
    const entry = (extract as Record<string, { en?: string; "zh-Hant"?: string }>)[key];
    assert.ok(entry?.en && entry["zh-Hant"], key);
  }
});
