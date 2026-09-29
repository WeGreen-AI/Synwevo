/** The table editor's decisions about modes, saving alone, sheets that went away and what screen
 *  readers are told (pages/SheetEditor.tsx; docs-sheet-live.md §3 and §9), kept apart from the page
 *  so they can be tested without a browser. */
import { MAX_SHEET_NAME, type Content } from "./sheetmodel.ts";
import type { SyncStatus } from "./sheetsync.ts";

export interface ModeInput {
  /** the room says the table is live (and this tab is not in single-person mode) */
  live: boolean;
  /** this tab chose to stay alone */
  forcedAlone: boolean;
  /** edits made alone, not saved yet */
  dirty: boolean;
  /** how the room's connection ended, if it did */
  closedCode: number | null;
  /** this tab was in the live table before */
  wasLive: boolean;
}

export interface Mode {
  /** editing the shared table */
  liveMode: boolean;
  /** live became possible while edits made alone are unsaved: the tab stays alone until they are saved
   *  (going live would show the room's table and drop them) */
  holdAlone: boolean;
  /** the sign-in ended while live (4401): the page keeps the table it showed, read-only, and the
   *  list of what did not reach the server — loading anything now would send the person to the
   *  sign-in page and take that list with it */
  signedOut: boolean;
}

export function editorMode(m: ModeInput): Mode {
  // Alone only while there is something of this tab's own to keep: once nothing is unsaved (saved,
  // or the person chose the other version in a merge dialog and the saved one was loaded) the table
  // is live again (P7: `forcedAlone` with nothing unsaved used to keep the tab alone until a reload)
  const holdAlone = m.live && !m.forcedAlone && m.dirty;
  return { liveMode: m.live && !m.dirty, holdAlone, signedOut: m.wasLive && m.closedCode === 4401 };
}

export interface LeaveInput {
  liveMode: boolean;
  /** live: my changes not answered yet */
  unsynced: number;
  /** live: my changes answered but not yet written by the room (lost with this page if its process dies now) */
  unpersisted: number;
  /** alone: edits not saved */
  dirty: boolean;
  /** signed out while live: edits only in the "needs you" list */
  stranded: number;
}

/** Closing or reloading the page asks first: something of this person's exists only here. Live, that
 *  is also what the room acknowledged but has not written yet (P6: the page is the only one that can
 *  send it again after a crash — for the few seconds until the room's next draft write). */
export function leaveAtRisk(x: LeaveInput): boolean {
  return x.liveMode ? x.unsynced > 0 || x.unpersisted > 0 : x.dirty || x.stranded > 0;
}

/** The body of a whole-table save made alone. Every such save of an existing template says `solo`:
 *  the room may be live for others (whether or not this tab ever joined it), and without it the
 *  server answers 409 `live_room` for as long as the room stays live. */
export function aloneSaveBody(editor: unknown, name: string, note: string, base: number | null, forceIds?: string[]): Record<string, unknown> {
  return {
    editor, name, solo: true,
    ...(note.trim() ? { note: note.trim() } : {}),
    ...(base ? { base_version: base } : {}),
    ...(forceIds ? { force_ids: forceIds } : {}),
  };
}

/** Sheets deleted in the live table and not yet purged by a save: they can be restored. */
export function deletedSheets(content: Content | null): { sid: string; name: string }[] {
  if (!content) return [];
  return content.order.filter((sid) => content.sheets[sid]?.gone).map((sid) => ({ sid, name: content.sheets[sid].name ?? "" }));
}

/** The name a restored sheet takes: its own, or — when a live sheet took it meanwhile (names ignore
 *  case) — the first free "name (n)". */
export function restoreName(content: Content, sid: string): string {
  const own = content.sheets[sid]?.name ?? "Sheet";
  const taken = new Set(content.order.filter((x) => x !== sid && !content.sheets[x].gone).map((x) => (content.sheets[x].name ?? "").toLowerCase()));
  if (!taken.has(own.toLowerCase())) return own;
  for (let k = 2; ; k++) {
    const suffix = ` (${k})`;
    const name = Array.from(own).slice(0, MAX_SHEET_NAME - suffix.length).join("").trimEnd() + suffix;
    if (!taken.has(name.toLowerCase())) return name;
  }
}

/** Is the sheet a cell editor was opened on still there? */
export function sheetStillThere(content: Content | null, sid: string | null): boolean {
  return !!content && !!sid && !!content.sheets[sid] && !content.sheets[sid].gone;
}

/** What the connection's state means to someone listening: typing flips "syncing" and "live" on every
 *  keystroke, which is not news — only reaching, losing or leaving the live table is. */
export type StatusKind = "live" | "offline" | "single";
export function statusKind(s: SyncStatus | null | undefined): StatusKind | null {
  if (s === "live" || s === "syncing") return "live";
  if (s === "offline" || s === "single") return s;
  return null;
}
