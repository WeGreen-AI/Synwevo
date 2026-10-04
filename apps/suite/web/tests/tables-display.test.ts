import { test } from "node:test";
import assert from "node:assert/strict";
import { translate } from "@synwevo/ui/i18n-core";
import "../src/apps/tables/messages.ts";
import { issueText } from "../src/apps/tables/display.ts";

// How Tables words values and refusals for people. Fixture values only.
const en = (source: string, params?: Record<string, any>) => translate("en", source, params);
const zh = (source: string, params?: Record<string, any>) => translate("zh-Hans", source, params);

test("an import row whose related record cannot be found or told apart names the column and the value", () => {
  assert.equal(issueText(en, "link_not_found", {column: "Customer", value: "Nobody"}),
    'Column "Customer": no related record is named "Nobody".');
  assert.equal(issueText(zh, "link_ambiguous", {column: "客户", value: "Twin"}),
    "列「客户」：有多条关联记录名为“Twin”，无法确定是哪一条。");
  assert.equal(issueText(en, "link_ambiguous", {column: "Customer", value: "Twin"}),
    'Column "Customer": several related records are named "Twin", so the import cannot tell which one.');
});
