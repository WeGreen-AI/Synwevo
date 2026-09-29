/** The table editor's mode, alone-save and sheet decisions (lib/sheetmode.ts, used by
 *  pages/SheetEditor.tsx) — the page-level findings of the review of live-table steps ③–⑤.
 *
 *  TEST FIXTURE: the content below is the shared corpus's seed table (fixtures/sheet_live_corpus.json)
 *  with sheets added and deleted by the browser model itself. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { applyOp, copy, liveSheetIds, type Content } from "../src/lib/sheetmodel.ts";
import { aloneSaveBody, deletedSheets, editorMode, restoreName, sheetStillThere, statusKind } from "../src/lib/sheetmode.ts";

const corpus = JSON.parse(readFileSync(new URL("./fixtures/sheet_live_corpus.json", import.meta.url), "utf8"));
const SEED: Content = corpus.sequences.find((s: any) => s.name === "groups_and_invalid").content;

function withSheets(): Content {
  const c = copy(SEED);
  const add = (id: string, name: string) => {
    const r = applyOp(c, { op: "sheet_add", id, name, rows: 1, cols: 1, row_ids: [`r${id.slice(1)}`], col_ids: [`c${id.slice(1)}`] }, { by: "u1" });
    assert.ok(r.ok, JSON.stringify(r));
  };
  add("s0000001", "明细");
  add("s0000002", "汇总");
  assert.ok(applyOp(c, { op: "sheet_delete", sheet: "s0000001" }, { by: "u1" }).ok);
  return c;
}

test("live → single → live keeps edits made alone: the tab stays alone until they are saved", () => {
  // before: going live again showed the room's table and dropped them without a word
  const back = editorMode({ live: true, forcedAlone: false, dirty: true, closedCode: null, wasLive: true });
  assert.equal(back.liveMode, false);
  assert.equal(back.holdAlone, true);
  const saved = editorMode({ live: true, forcedAlone: false, dirty: false, closedCode: null, wasLive: true });
  assert.equal(saved.liveMode, true, "once saved, the tab goes live");
  // P7: alone with nothing unsaved (the person chose the other version in a merge dialog) is live again
  assert.equal(editorMode({ live: true, forcedAlone: true, dirty: false, closedCode: null, wasLive: false }).liveMode, true);
  assert.equal(editorMode({ live: true, forcedAlone: true, dirty: true, closedCode: null, wasLive: true }).liveMode, false);
});

test("P6: closing the page asks while the room has answered changes it has not written yet", async () => {
  const { leaveAtRisk } = await import("../src/lib/sheetmode.ts");
  const live = { liveMode: true, unsynced: 0, unpersisted: 0, dirty: false, stranded: 0 };
  assert.equal(leaveAtRisk(live), false);
  assert.equal(leaveAtRisk({ ...live, unsynced: 1 }), true);
  assert.equal(leaveAtRisk({ ...live, unpersisted: 1 }), true, "answered, but only this page could send it again after a crash");
  assert.equal(leaveAtRisk({ ...live, liveMode: false, dirty: true }), true);
  assert.equal(leaveAtRisk({ ...live, liveMode: false, stranded: 2 }), true);
});

test("signed out while live (4401): the page keeps the table read-only instead of loading into the sign-in page", () => {
  const m = editorMode({ live: false, forcedAlone: false, dirty: false, closedCode: 4401, wasLive: true });
  assert.equal(m.signedOut, true);
  assert.equal(m.liveMode, false);
  assert.equal(editorMode({ live: false, forcedAlone: false, dirty: false, closedCode: 4410, wasLive: true }).signedOut, false);
  assert.equal(editorMode({ live: false, forcedAlone: false, dirty: false, closedCode: 4401, wasLive: false }).signedOut, false);
});

test("a whole-table save made alone always says solo (a tab that never went live was refused live_room forever)", () => {
  assert.deepEqual(aloneSaveBody({ sheets: [] }, "表", "", null), { editor: { sheets: [] }, name: "表", solo: true });
  assert.deepEqual(aloneSaveBody({ sheets: [] }, "表", " 说明 ", 3, ["cell:Sheet1!A1"]),
    { editor: { sheets: [] }, name: "表", solo: true, note: "说明", base_version: 3, force_ids: ["cell:Sheet1!A1"] });
});

test("deleted sheets are listed for restoring until a save purges them; a taken name gets a free one", () => {
  const c = withSheets();
  assert.deepEqual(deletedSheets(c), [{ sid: "s0000001", name: "明细" }]);
  assert.equal(restoreName(c, "s0000001"), "明细");
  assert.ok(applyOp(c, { op: "sheet_rename", sheet: "s0000002", name: "明细" }, { by: "u2" }).ok);   // someone took the name
  const name = restoreName(c, "s0000001");
  assert.equal(name, "明细 (2)");
  const r = applyOp(c, { op: "sheet_restore", sheet: "s0000001", name }, { by: "u1" });
  assert.ok(r.ok, JSON.stringify(r));
  assert.deepEqual(deletedSheets(c), []);
  assert.ok(liveSheetIds(c).includes("s0000001"));
  assert.deepEqual(deletedSheets(null), []);
});

test("a cell editor opened on a sheet that is gone writes nowhere", () => {
  const c = withSheets();
  assert.equal(sheetStillThere(c, "s0000002"), true);
  assert.equal(sheetStillThere(c, "s0000001"), false, "deleted while the person typed");
  assert.equal(sheetStillThere(c, "nosuch01"), false);
  assert.equal(sheetStillThere(null, "s0000002"), false);
});

test("screen readers hear reaching or losing the live table, not every edit's syncing ↔ live", () => {
  assert.equal(statusKind("syncing"), statusKind("live"));
  assert.notEqual(statusKind("offline"), statusKind("live"));
  assert.equal(statusKind("single"), "single");
  assert.equal(statusKind("connecting"), null);
});

test("selection labels use the name and colour the server gave, not what the other page claims", async () => {
  const { othersFrom } = await import("../src/lib/sheetroom.ts");
  const sel = { sheet: "s0000001", r1: "r1", c1: "c1", r2: "r1", c2: "c1" };
  const states = new Map<number, any>([
    [1, { user: { id: "u1", name: "我自己" }, sel }],                           // this tab
    [2, { user: { id: "u2", name: "冒充的名字", color: 7 }, sel }],             // claims another name
    [3, { user: { id: "u1", name: "我的另一个标签页" }, sel }],                 // my own other tab
    [4, { user: { id: "u9", name: "不认识的人" }, sel }],                       // not known to the server (yet)
    [5, { user: { id: "u3" }, sel: { sheet: "s0000001", r1: 1 } }],            // not a selection
  ]);
  const known: Record<string, any> = { u2: { name: "李四", color: 2 } };
  const out = othersFrom(states, 1, "u1", (id) => known[id] ?? null);
  assert.deepEqual(out.map((o) => [o.user.id, o.user.name, o.user.color]), [["u2", "李四", 2], ["u9", "—", 1]]);
});

test("a save refused while an outside version cannot be folded in does not say 'save again in a moment'", async () => {
  await import("../src/lib/presentation.ts");
  const { saveRefusalText } = await import("../src/lib/sheettext.ts");
  const { setLocale } = await import("@synwevo/ui/i18n-core");
  setLocale("zh-Hans" as any);
  const e = { code: "stale_template" };
  assert.match(saveRefusalText(e, true)!, /确认放弃/);
  assert.doesNotMatch(saveRefusalText(e, true)!, /稍后再保存/);
  assert.match(saveRefusalText(e, false)!, /稍后再保存/, "while a version is being folded in, waiting does help");
  setLocale("en" as any);
  assert.doesNotMatch(saveRefusalText(e, true)!, /[一-鿿]/);
  setLocale("zh-Hans" as any);
});

test("a sheet deleted while the page is open is announced once, with where to restore it, in three languages", async () => {
  await import("../src/lib/presentation.ts");
  const { newlyDeleted } = await import("../src/lib/sheetmode.ts");
  const { deletedText, deletedListText } = await import("../src/lib/sheettext.ts");
  const { setLocale } = await import("@synwevo/ui/i18n-core");
  const c = withSheets();                                       // 明细 deleted before the page looked
  assert.deepEqual(newlyDeleted(null, c), [], "the first look: what was deleted before is the visible list's, not news");
  const seen = new Set(deletedSheets(c).map((g) => g.sid));
  assert.deepEqual(newlyDeleted(seen, c), []);
  assert.ok(applyOp(c, { op: "sheet_delete", sheet: "s0000002" }, { by: "u2" }).ok);   // someone deletes 汇总
  assert.deepEqual(newlyDeleted(seen, c), [{ sid: "s0000002", name: "汇总" }]);
  const after = new Set(deletedSheets(c).map((g) => g.sid));
  assert.ok(applyOp(c, { op: "sheet_restore", sheet: "s0000001" }, { by: "u1" }).ok);    // a restore is not a deletion
  assert.deepEqual(newlyDeleted(after, c), []);
  const said: Record<string, [string, string]> = {};
  for (const lang of ["zh-Hans", "en", "zh-Hant"]) {
    setLocale(lang as any);
    said[lang] = [deletedText(["汇总"]), deletedListText(2)];
  }
  setLocale("zh-Hans" as any);
  assert.equal(said["zh-Hans"][0], "工作表「汇总」已被删除；保存为版本之前，可以从「已删除的工作表」恢复。");
  assert.equal(said["zh-Hans"][1], "已删除的工作表（2）：保存为版本之前，可以在这里恢复。");
  assert.equal(said.en[0], "Sheet “汇总” was deleted. Until the table is saved as a version, it can be restored from “Deleted sheets”.");
  assert.equal(said.en[1], "Deleted sheets (2): until the table is saved as a version, they can be restored here.");
  for (const s of said.en) assert.doesNotMatch(s.replace("汇总", ""), /[一-鿿]/, "no Chinese left in the English sentences");
  assert.equal(said["zh-Hant"][1], "已刪除的工作表（2）：儲存為版本之前，可以在這裡恢復。");
  assert.ok(said["zh-Hant"][0].includes("儲存為版本之前"));
});
