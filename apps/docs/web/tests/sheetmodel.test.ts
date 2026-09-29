/** The browser's live-table model agrees with the server's (docs-sheet-live.md §6): every sequence of
 *  the shared corpus — generated from `collab_sheet.py` by `apps/docs/tests/sheet_live_corpus.py` —
 *  replayed here gives the same result at every step (patch and inverse, or refusal code and detail),
 *  the same content and the same rendered model at the end. The server's patches applied to a mirror
 *  build the same content, and each inverse restores what was there before. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { applyOp, applyPatch, copy, cpCompare, cpLen, echo, project, pyFloat, pyStrip, sheetTitle, Unpredictable, type Content } from "../src/lib/sheetmodel.ts";

const corpus = JSON.parse(readFileSync(new URL("./fixtures/sheet_live_corpus.json", import.meta.url), "utf8"));

test("the corpus is the server model's and covers every refusal a tab can predict", () => {
  assert.equal(corpus.format, 1);
  assert.equal(corpus.model, "apps/docs/synwevo_docs/collab_sheet.py");
  const codes = new Set<string>();
  let changed = 0;
  for (const s of corpus.sequences) for (const st of s.steps) {
    if (!st.result.ok) codes.add(st.result.code);
    else if (st.result.patch.length) changed++;
  }
  for (const code of ["invalid", "not_found", "sheet_deleted", "covered", "too_large", "merge_overlap", "covers_values", "merged_inner",
    "merged", "vertical_merge", "duplicate_repeat", "field_overlap", "id_taken", "sheet_name_taken", "last_sheet"]) {
    assert.ok(codes.has(code), `the corpus has no ${code}`);
  }
  assert.ok(changed > 400, `only ${changed} accepted changes`);
});

for (const seq of corpus.sequences) {
  test(`corpus: ${seq.name}`, () => {
    const content: Content = copy(seq.content);
    const mirror: Content = copy(seq.content);
    const who = new Map<string, string>();
    seq.steps.forEach((step: any, i: number) => {
      const before = copy(content);
      const got = applyOp(content, step.op, { by: step.by, who, maxCells: seq.cap });
      assert.deepStrictEqual(got, step.result, `${seq.name} step ${i}: ${JSON.stringify(step.op).slice(0, 200)}`);
      if (got.ok) {
        applyPatch(mirror, got.patch);
        assert.deepStrictEqual(applyPatch(copy(content), got.before), before, `${seq.name} step ${i}: the inverse restores`);
      } else {
        assert.deepStrictEqual(content, before, `${seq.name} step ${i}: a refusal changes nothing`);
      }
    });
    assert.deepStrictEqual(content, seq.final_content);
    assert.deepStrictEqual(mirror, seq.final_content, "patches applied to a mirror build the same content");
    assert.deepStrictEqual(project(content), seq.final);
  });
}

test("an operation that needs an id only the server can make up is not predicted", () => {
  const seq = corpus.sequences.find((s: any) => s.name === "groups_and_invalid");
  const content: Content = copy(seq.content);
  const sid = content.order[0];
  assert.throws(() => applyOp(content, { op: "sheet_add", name: "新表" }, { by: "u1" }), Unpredictable);
  assert.throws(() => applyOp(content, { op: "merge", sheet: sid, range: [content.sheets[sid].rows[0], content.sheets[sid].cols[0], content.sheets[sid].rows[1], content.sheets[sid].cols[0]] }, { by: "u1" }), Unpredictable);
  assert.deepStrictEqual(content, seq.content, "and changes nothing");
});

test("Python's text rules: length in code points, strip, float(), titles, echo", () => {
  assert.equal(cpLen("😀a"), 2);
  assert.equal(cpLen("\ud800"), 1);
  assert.equal(pyStrip("　 a \x1c"), "a");
  assert.equal(pyStrip("﻿a"), "﻿a");
  assert.equal(pyFloat(" 1_000 "), 1000);
  assert.equal(pyFloat("１２"), 12);
  assert.equal(pyFloat("٣.٥"), 3.5);
  assert.equal(pyFloat("1__0"), null);
  assert.equal(pyFloat("_1"), null);
  assert.equal(pyFloat("0x10"), null);
  assert.equal(pyFloat("-Infinity"), -Infinity);
  assert.ok(Number.isNaN(pyFloat("nan")));
  assert.equal(pyFloat(".5e-1"), 0.05);
  assert.equal(sheetTitle(" a/b "), "a_b");
  assert.equal(sheetTitle(""), "Sheet");
  assert.equal(sheetTitle("x".repeat(40)).length, 31);
  assert.equal(echo("x".repeat(65)), null);
  assert.equal(echo(1.5), null);
  assert.equal(echo(undefined), null);
  assert.ok(cpCompare("￿", "😀") < 0, "code point order, not UTF-16 units");
});
