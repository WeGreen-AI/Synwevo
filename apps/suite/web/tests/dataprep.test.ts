import { test } from "node:test";
import assert from "node:assert/strict";
import { translate, type Params } from "@synwevo/ui/i18n-core";
import { draftProblems, stepLabels, type Step } from "../src/apps/dataprep/model.ts";
import "../src/apps/dataprep/messages.ts";

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

test("an unfinished draft names the step and the field to fix, in every language", () => {
  // Release acceptance: saving a draft with an unfinished step only said "请检查必填字段与步骤参数".
  // `fields` is the `detail` of the registry's invalid_input refusal, as dataprep.recipe.save returns it.
  const draft: Step[] = [
    { id: "s_trim0001", type: "trim", columns: [] },
    { id: "s_cast0001", type: "cast", column: "" },
    { id: "s_filter01", type: "filter", condition: { column: "", op: "eq", value: "" } },
    { id: "s_split001", type: "split", column: "a", into: ["x", ""], separator: "" },
    { id: "s_join0001", type: "join", source: "other", left: ["a"], right: ["a", "b"] },
    { id: "s_agg00001", type: "aggregate", group_by: [], measures: [{ op: "sum", column: null, target: "total" }] },
    { id: "s_cast0002", type: "cast", column: "a", to: "datetime", offset: "8" },
  ];
  const detail = { fields: [
    { loc: ["name"], type: "string_too_short" },
    { loc: ["definition", "source"], type: "string_pattern_mismatch" },
    { loc: ["definition", "steps", "0", "trim", "columns"], type: "too_short" },
    { loc: ["definition", "steps", "1", "cast", "column"], type: "string_too_short" },
    { loc: ["definition", "steps", "2", "filter", "condition", "column"], type: "string_too_short" },
    { loc: ["definition", "steps", "3", "split", "into", "1"], type: "string_too_short" },
    { loc: ["definition", "steps", "3", "split", "separator"], type: "string_too_short" },
    { loc: ["definition", "steps", "4", "join"], type: "value_error" },
    { loc: ["definition", "steps", "5", "aggregate", "measures", "0"], type: "value_error" },
    { loc: ["definition", "steps", "6", "cast", "offset"], type: "string_pattern_mismatch" },
    { loc: ["definition"], type: "value_error" },
  ] };
  const zh = (key: string, params?: Params) => translate("zh-Hans", key, params);
  assert.deepEqual(draftProblems(detail, draft, zh), [
    "请填写「方案名称」",
    "「主来源名称」的值无效",
    "步骤 1. 清理空白：请填写「选择列」",
    "步骤 2. 类型转换：请填写「列」",
    "步骤 3. 过滤：请填写「列」",
    "步骤 4. 拆分字段：请填写「输出列（每行一个）」",
    "步骤 4. 拆分字段：请填写「分隔文本」",
    "步骤 5. 关联匹配：本表关联键与来源关联键的数量必须相同",
    "步骤 6. 分组汇总：请填写「数值列」",
    "步骤 7. 类型转换：「无时区时间的固定偏移」的值无效",
  ]);
  const en = (key: string, params?: Params) => translate("en", key, params);
  assert.deepEqual(draftProblems({ fields: detail.fields.slice(3, 4) }, draft, en), ["Step 2. Convert type: fill in “Column”"]);
  const hant = (key: string, params?: Params) => translate("zh-Hant", key, params);
  assert.deepEqual(draftProblems({ fields: detail.fields.slice(9, 10) }, draft, hant), ["步驟 7. 類型轉換：「無時區時間的固定偏移」的值無效"]);
  // Anything the page cannot place keeps the general message only.
  assert.deepEqual(draftProblems(undefined, draft, zh), []);
  assert.deepEqual(draftProblems({ fields: [{ loc: ["definition", "steps", "99", "cast", "column"], type: "missing" }] }, draft, zh), []);
});
