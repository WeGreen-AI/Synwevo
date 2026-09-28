// Regression tests for the Synwevo Docs workbench's pure decision logic (apps/docs/web/src/lib).
// Run: npm test -w @synwevo/docs-web   (node --test; Node >= 23.6 strips the TypeScript types)
import { test } from "node:test";
import assert from "node:assert/strict";
import { cssColor, highlightColor } from "../src/lib/css.ts";
import { canCancel, canRetry, haltText, planning, progress, shouldPoll, verificationBadge } from "../src/lib/jobview.ts";
import { errorText, isPermissionError, NO_EXPORT_TEXT, permissions, READ_ONLY_TEXT } from "../src/lib/perm.ts";
import { clampCursor, listQuery, toPage } from "../src/lib/projects.ts";
import { moveCursor } from "../src/lib/gridsel.ts";
import { formatValueMap, parseValueMap } from "../src/lib/valuemap.ts";
import { mergeSource, repeatColumns, sectionColumns, withSettings } from "../src/lib/mapping.ts";

/* ---------------------------------------------------------------- F249 / F215: CSS from files */
test("colours from uploaded files cannot carry url() into a style attribute", () => {
  assert.equal(cssColor("000 url(https://attacker.example/x)"), undefined);
  assert.equal(cssColor("#000 url(https://attacker.example/x)"), undefined);
  assert.equal(cssColor("url(https://attacker.example/x)"), undefined);
  assert.equal(cssColor("red;background-image:url(//x)"), undefined);
  assert.equal(cssColor("image-set('x' 1x)"), undefined);
  assert.equal(cssColor("var(--x)"), undefined);
  assert.equal(highlightColor("url(//attacker.example/beacon)"), undefined);
});

test("ordinary colours still pass", () => {
  assert.equal(cssColor("#FFCC00"), "#FFCC00");
  assert.equal(cssColor("ffcc00"), "#ffcc00");                 // OOXML fill without '#'
  assert.equal(cssColor("rgba(37, 99, 235, .15)"), "rgba(37, 99, 235, .15)");
  assert.equal(cssColor("transparent"), "transparent");
  assert.equal(cssColor("auto"), undefined);                    // OOXML 'auto' is not CSS
  assert.equal(highlightColor("darkYellow"), "#808000");
  assert.equal(highlightColor("yellow"), "#ffff00");
  assert.equal(cssColor(42), undefined);
});

/* ---------------------------------------------------------------- F222 / F238: job page */
test("the job page keeps polling while a job is cancelling or being planned", () => {
  assert.equal(shouldPoll({ status: "cancelling" }), true);
  assert.equal(shouldPoll({ status: "queued", phase: "planning" }), true);
  assert.equal(shouldPoll({ status: "running" }), true);
  assert.equal(shouldPoll({ status: "completed" }), false);
  assert.equal(shouldPoll({ status: "cancelled", halt_reason: "authorization_revoked:role_insufficient" }), false);
  // a job stopped while planning keeps phase=planning but no longer changes: no endless poll
  assert.equal(shouldPoll({ status: "cancelled", phase: "planning" }), false);
});

test("planning progress comes from counts.planned / counts.total while the job is active", () => {
  assert.deepEqual(planning({ status: "running", phase: "planning", counts: { planned: 500, total: 2000 } }), { planned: 500, total: 2000, pct: 25 });
  assert.equal(planning({ status: "running", phase: "generating", counts: { planned: 5, total: 5 } }), null);
  assert.equal(planning({ status: "cancelled", phase: "planning", counts: { planned: 5, total: 50 } }), null);
});

test("retry is offered only when the server would accept it", () => {
  // cancelling is ACTIVE on the server: retry answers 409 job_active
  assert.equal(canRetry({ status: "cancelling", counts: { cancelled: 3 } }), false);
  assert.equal(canRetry({ status: "running", counts: { failed: 1 } }), false);
  // nothing failed or cancelled: 409 nothing_to_retry
  assert.equal(canRetry({ status: "completed_with_errors", counts: { skipped: 4, success: 1 } }), false);
  assert.equal(canRetry({ status: "completed_with_errors", counts: { failed: 2 } }), true);
  // a viewer gets 403 read_only_role
  assert.equal(canRetry({ status: "cancelled", counts: { cancelled: 2 } }, false), false);
  // stopped while planning: the unplanned remainder is retryable (jobs.py counts it)
  assert.equal(canRetry({ status: "cancelled", phase: "planning", counts: { planned: 10, total: 40, unplanned: 30 } }), true);
  assert.equal(canRetry({ status: "running", phase: "planning", counts: { planned: 10, total: 40, unplanned: 30 } }), false);
  assert.equal(canCancel({ status: "cancelling", cancel_requested: true }), false);
  assert.equal(canCancel({ status: "running" }), true);
});

test("progress counts every finished item and never exceeds 100%", () => {
  assert.deepEqual(progress({ counts: { total: 4, success: 1, failed: 1, skipped: 1, cancelled: 1 } }), { done: 4, total: 4, pct: 100 });
  assert.deepEqual(progress({ counts: {} }), { done: 0, total: 0, pct: 0 });
});

test("failed-only retry excludes cancelled, skipped and unplanned samples", () => {
  assert.equal(canRetry({ status: "cancelled", phase: "planning", counts: { cancelled: 2, skipped: 1, unplanned: 9 } }, true, true), false);
  assert.equal(canRetry({ status: "completed_with_errors", counts: { failed: 2, cancelled: 1 } }, true, true), true);
  assert.equal(canRetry({ status: "running", counts: { failed: 2 } }, true, true), false);
  assert.equal(canRetry({ status: "failed", counts: { failed: 2 } }, false, true), false);
});

test("editing detail columns preserves constants, occurrence and transforms", () => {
  const old = [
    { slot_col: "amount", source: { header: "金额", occurrence: 2 }, transforms: [{ op: "round", digits: 2 }] },
    { slot_col: "unit", source: { kind: "constant", value: "元" } },
  ];
  assert.deepEqual(repeatColumns(["amount", "unit"], { amount: "金额", unit: "" }, old), old);
  const changed = repeatColumns(["amount", "unit"], { amount: "价税合计", unit: "单位" }, old);
  assert.deepEqual(changed[0].source, { header: "价税合计" });
  assert.deepEqual(changed[0].transforms, old[0].transforms);
  assert.deepEqual(changed[1].source, { header: "单位" });
  assert.deepEqual(old[0].source, { header: "金额", occurrence: 2 });
});

test("a split block keeps one rule per column; removing the split removes them and the group_by", () => {
  const cols = [
    { slot_col: "no", source: { header: "订单号" } },
    { slot_col: "total", source: { header: "金额" }, agg: "sum" },
    { slot_col: "note", source: { kind: "constant", value: "" } },
  ];
  const split = sectionColumns(cols, { total: "sum" }, true);
  assert.deepEqual(split.map((c) => c.agg), ["unique", "sum", undefined]);   // constants need no rule
  assert.deepEqual(sectionColumns(split, { total: "sum" }, false).map((c) => "agg" in c), [false, false, false]);
  const m: any = { id: "m1", no: 1, type: "repeat", sources: [], target: { binding: "b" }, group_by: { keys: [{ header: "订单号" }] } };
  assert.equal("group_by" in withSettings(m, { group_by: null }), false);
  assert.deepEqual(withSettings(m, {}).group_by, m.group_by);                // not touched unless the dialog says so
  assert.deepEqual(withSettings({ ...m, group_by: undefined }, { group_by: { keys: [{ header: "客户" }], empty_key: "skip" } }).group_by,
    { keys: [{ header: "客户" }], empty_key: "skip" });
});

test("an output is called 已校验 only when values were compared", () => {
  assert.equal(verificationBadge({ reopened: true, checks: 0, engine: "pdf" })?.label, "已重新打开");
  assert.equal(verificationBadge({ reopened: true, checks: 0, engine: "image", note: "图片" })?.label, "已重新打开");
  assert.equal(verificationBadge({ reopened: true, checks: 5, engine: "xlsx" })?.label, "✓ 已校验");
  assert.equal(verificationBadge({ error: "输出文件无法重新打开" })?.tone, "err");
  assert.equal(verificationBadge({ reopened: true, checks: 3, missing: [{}] })?.tone, "warn");
  assert.equal(verificationBadge(null), null);
});

test("a halted job says why in words", () => {
  assert.match(haltText("authorization_revoked:role_insufficient") || "", /角色/);
  assert.match(haltText("authorization_revoked:something_new") || "", /something_new/);
  assert.equal(haltText(null), null);
});

/* ---------------------------------------------------------------- permissions */
test("a viewer may neither write nor export; can_export lets a viewer download", () => {
  const me = (role: string, can_export = false) => ({ workspace_id: "w1", workspaces: [{ id: "w0", role: "owner" }, { id: "w1", role, can_export }] });
  assert.deepEqual(permissions(me("viewer"), false), { canWrite: false, canExport: false, role: "viewer" });
  assert.deepEqual(permissions(me("viewer", true), false), { canWrite: false, canExport: true, role: "viewer" });
  assert.deepEqual(permissions(me("editor"), false), { canWrite: true, canExport: true, role: "editor" });
  // no account system, or no idea which workspace: offer everything, the server decides
  assert.equal(permissions(me("viewer"), true).canWrite, true);
  assert.equal(permissions({ workspace_id: "zz", workspaces: [{ id: "a", role: "viewer" }, { id: "b", role: "viewer" }] }, false).canWrite, true);
});

test("the two permission refusals get a clear sentence; other errors keep their message", () => {
  assert.equal(errorText({ code: "read_only_role", message: "forbidden" }), READ_ONLY_TEXT);
  assert.equal(errorText({ code: "export_not_allowed", message: "forbidden" }), NO_EXPORT_TEXT);
  assert.equal(errorText({ code: "conflict", message: "映射已被修改" }), "映射已被修改");
  assert.equal(isPermissionError({ code: "read_only_role" }), true);
  assert.equal(isPermissionError({ code: "not_found" }), false);
});

/* ---------------------------------------------------------------- F233: project list */
test("project list: a paged server answer is used as is", () => {
  const p = { q: "", sort: "updated" as const, order: "desc" as const, offset: 50, limit: 50, archived: false };
  const page = toPage({ items: [{ name: "a" }], total: 51, offset: 50, limit: 50, next_offset: null }, p);
  assert.equal(page.total, 51);
  assert.equal(page.next_offset, null);
  assert.deepEqual(listQuery({ ...p, q: "  合同 " }), { sort: "updated", order: "desc", offset: 50, limit: 50, archived: 0, q: "合同" });
});

test("project list: an older server's plain array is searched, sorted and windowed on the client", () => {
  const rows = [
    { name: "乙", template_name: "合同", updated_at: 3, created_at: 1 },
    { name: "甲", template_name: null, updated_at: 5, created_at: 2 },
    { name: "丙", template_name: "发票", updated_at: 1, created_at: 3, archived_at: 9 },
  ];
  const base = { q: "", sort: "updated" as const, order: "desc" as const, offset: 0, limit: 1, archived: false };
  const first = toPage(rows, base);
  assert.deepEqual(first.items.map((r) => r.name), ["甲"]);
  assert.equal(first.total, 2);                         // archived hidden unless asked for
  assert.equal(first.next_offset, 1);
  assert.equal(toPage(rows, { ...base, offset: 1 }).next_offset, null);
  assert.deepEqual(toPage(rows, { ...base, q: "合同", limit: 50 }).items.map((r) => r.name), ["乙"]);
  assert.deepEqual(toPage(rows, { ...base, archived: true, sort: "created", order: "asc", limit: 50 }).items.map((r) => r.name), ["乙", "甲", "丙"]);
});

test("F246: the keyboard cursor is clamped after rows disappear", () => {
  assert.equal(clampCursor(5, 3), 2);
  assert.equal(clampCursor(-1, 3), 0);
  assert.equal(clampCursor(4, 0), 0);
});

/* ---------------------------------------------------------------- F260: grid keyboard range */
test("Shift+Arrow grows the range from a fixed anchor in every direction", () => {
  // start at C5 (row 4, col 2); Shift+Up twice must give rows 3..5, not collapse to row 4
  let s = moveCursor({ r: 4, c: 2 }, { r: 4, c: 2 }, -1, 0, true, 100, 10);
  s = moveCursor(s.anchor, s.active, -1, 0, true, 100, 10);
  assert.deepEqual(s.rect, { r1: 2, c1: 2, r2: 4, c2: 2 });
  s = moveCursor(s.anchor, s.active, 0, -1, true, 100, 10);   // and left
  assert.deepEqual(s.rect, { r1: 2, c1: 1, r2: 4, c2: 2 });
  s = moveCursor(s.anchor, s.active, 3, 0, true, 100, 10);    // back down past the anchor
  assert.deepEqual(s.rect, { r1: 4, c1: 1, r2: 5, c2: 2 });
  const plain = moveCursor(s.anchor, s.active, 0, 1, false, 100, 10);
  assert.deepEqual(plain.rect, { r1: 5, c1: 2, r2: 5, c2: 2 });
  assert.deepEqual(moveCursor({ r: 0, c: 0 }, { r: 0, c: 0 }, -1, -1, false, 5, 5).rect, { r1: 0, c1: 0, r2: 0, c2: 0 });
});

/* ---------------------------------------------------------------- F251: value map typing */
test("the value map keeps unfinished lines as typed and reports them", () => {
  const typed = "TRUE=是\nFALSE=否\n1";
  const { table, bad } = parseValueMap(typed);
  assert.deepEqual(table, { TRUE: "是", FALSE: "否" });
  assert.deepEqual(bad, [3]);
  assert.deepEqual(parseValueMap("1=男\n\n2=女\n").table, { 1: "男", 2: "女" });
  assert.equal(formatValueMap({ a: "b" }), "a=b");
});

/* ---------------------------------------------------------------- F258: mapping merge */
test("merging a source applies the dialog's transforms instead of dropping them", () => {
  const existing: any = { id: "m1", no: 1, color: "#000", enabled: false, type: "single", sources: [{ kind: "cell" }], target: { binding: "b" }, transforms: [{ op: "trim" }], style: "source" };
  const merged = mergeSource(existing, { kind: "column", header: "日期" }, { op: "concat", sep: " " },
    { transforms: [{ op: "date_format", pattern: "yyyy年m月d日" }], style: "target", value_mode: "display" });
  assert.equal(merged.sources.length, 2);
  assert.deepEqual(merged.transforms, [{ op: "date_format", pattern: "yyyy年m月d日" }]);
  assert.equal(merged.value_mode, "display");
  assert.equal("style" in merged, false);            // switched back to the template's style
  assert.equal(merged.enabled, false);               // a disabled mapping is not re-enabled
  const edited = withSettings(existing, { transforms: [], style: "source" });
  assert.equal(edited.style, "source");
  assert.equal(edited.enabled, false);
});
