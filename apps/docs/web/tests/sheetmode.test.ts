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
  assert.equal(editorMode({ live: true, forcedAlone: true, dirty: false, closedCode: null, wasLive: false }).liveMode, false);
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
