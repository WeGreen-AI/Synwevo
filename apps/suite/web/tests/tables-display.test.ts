import { test } from "node:test";
import assert from "node:assert/strict";
import { translate } from "@synwevo/ui/i18n-core";
import "../src/apps/tables/messages.ts";
import { historyText, issueText, relationLabel } from "../src/apps/tables/display.ts";

// How Tables words values and refusals for people. Fixture values only.
const en = (source: string, params?: Record<string, any>) => translate("en", source, params);
const zh = (source: string, params?: Record<string, any>) => translate("zh-Hans", source, params);
const fixtureField = (type: string, config: Record<string, any> = {}): any =>
  ({id: "fld_000000000001", name: "Fixture", description: "", type, config, required: false, is_unique: false,
    default: null, read_role: "viewer", write_role: "editor", can_write: true});

test("change history shows option labels and related record names, never their ids", () => {
  const status = fixtureField("single_select", {choices: [{id: "opt_00000001", label: "Open"}, {id: "opt_00000002", label: "Done"}]});
  assert.equal(historyText(en, status, "opt_00000002"), "Done");
  assert.equal(historyText(en, status, "opt_0000dead"), "(Deleted option)");
  assert.equal(historyText(zh, status, "opt_0000dead"), "（已删除的选项）");
  // A value from before the field became a select is shown as it was typed.
  assert.equal(historyText(en, status, "typed earlier"), "typed earlier");
  const tags = fixtureField("multi_select", {choices: [{id: "opt_00000001", label: "Red"}]});
  assert.equal(historyText(en, tags, ["opt_00000001", "opt_0000dead"]), "Red, (Deleted option)");
  const customer = fixtureField("link", {table_id: "tbl_000000000001", multiple: true});
  const titles = {rec_000000000001: "Acme East"};
  assert.equal(historyText(en, customer, ["rec_000000000001", "rec_00000000dead"], titles), "Acme East, (Unavailable record)");
  assert.equal(historyText(en, customer, null, titles), "—");
  assert.equal(historyText(en, fixtureField("money"), {amount: "12.50", currency: "CNY"}), "12.50 CNY");
  assert.equal(historyText(en, fixtureField("boolean"), false), "No");
});

test("the relation picker names a chosen record by its title", () => {
  assert.equal(relationLabel(en, "rec_000000000001", {rec_000000000001: "Acme East"}), "Acme East");
  assert.equal(relationLabel(en, "rec_00000000dead", {}), "(Unavailable record)");
  assert.equal(relationLabel(zh, "rec_00000000dead", {}), "（不可用的记录）");
});

test("an import row whose related record cannot be found or told apart names the column and the value", () => {
  assert.equal(issueText(en, "link_not_found", {column: "Customer", value: "Nobody"}),
    'Column "Customer": no related record is named "Nobody".');
  assert.equal(issueText(zh, "link_ambiguous", {column: "客户", value: "Twin"}),
    "列「客户」：有多条关联记录名为“Twin”，无法确定是哪一条。");
  assert.equal(issueText(en, "link_ambiguous", {column: "Customer", value: "Twin"}),
    'Column "Customer": several related records are named "Twin", so the import cannot tell which one.');
});

test("a refused import row names its column, and a refused save names its field", () => {
  const orderNo = {...fixtureField("text"), id: "fld_000000000009", name: "Order No"};
  assert.equal(issueText(en, "invalid_cell", {reason: "not_number", column: "Quantity"}),
    'Column "Quantity": The value does not match its field type. Check the number, date or option.');
  assert.equal(issueText(zh, "unique_conflict", {field_id: orderNo.id, column: "订单号"}, [orderNo]),
    "列「订单号」：该值已被使用，请输入唯一值。");
  assert.equal(issueText(en, "unique_conflict", {field_id: orderNo.id}, [orderNo]),
    'Field "Order No": That value is already in use. Enter a unique value.');
  assert.equal(translate("zh-Hant", "字段「{field}」：{message}", {field: "Order No", message: "x"}), "欄位「Order No」：x");
  // A field the reader is not shown is never named; the refusal stays general.
  assert.equal(issueText(en, "unique_conflict", {}, [orderNo]), "That value is already in use. Enter a unique value.");
  assert.equal(issueText(en, "required_cell", {field_id: "fld_00000000dead"}, [orderNo]), "Complete all required fields.");
});
