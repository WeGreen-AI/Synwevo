import { formatNumber } from "@synwevo/ui";
import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n, LanguageMenu, PageState } from "@synwevo/ui";
import { useEffect, useLayoutEffect, useMemo, useReducer, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get, patch, post, put, workspaceContext } from "@synwevo/auth-client";
import { cellRef, colLetter, isImeKey, rangeRef, useDialog, useToast } from "@synwevo/ui";
import { initialState, type Focus, type RoomState } from "../lib/collab";
import { PresenceBar, peerClass } from "../components/ProjectPresence";
import { moveCursor, rectOf } from "../lib/gridsel";
import { cssColor } from "../lib/css";
import { errorText } from "../lib/perm";
import { loadedVersion, saveOverStale, STALE_TITLE, staleText } from "../lib/templatesave";
import { openSheetRoom, type SheetRoom } from "../lib/sheetroom";
import { COL_WIDTH, MAX_TX, ROW_HEIGHT, filled, liveSheetIds, projectSheet, type Content } from "../lib/sheetmodel";
import { randomId, type Attention, type SyncSnapshot, type UndoResult } from "../lib/sheetsync";
import { announcementText, attentionText, closeText, liveReasonText, retryText, saveRefusalText, saveText, skippedText, statusText } from "../lib/sheettext";
import { aloneSaveBody, deletedSheets, editorMode, leaveAtRisk, restoreName, sheetStillThere, statusKind, type StatusKind } from "../lib/sheetmode";

interface Cell { v?: string | number | boolean | null; t?: string | null; s?: any }
interface Field { id: string; name: string; kind: "cell" | "region" | "repeat"; ref?: string; row?: number; c1?: number; c2?: number; required?: boolean; empty_policy?: string }
interface SheetData { name: string; cols: { w: number }[]; rows: { h: number }[]; cells: Record<string, Cell>; merges: string[]; fields: Field[] }

const rid = () => "f" + Math.random().toString(16).slice(2, 12);
const COLORS = ["#e11d48", "#2563eb", "#059669", "#d97706", "#7c3aed", "#0891b2"];
const newSheet = (name: string): SheetData => ({ name, cols: Array.from({ length: 10 }, () => ({ w: 100 })), rows: Array.from({ length: 30 }, () => ({ h: 24 })), cells: {}, merges: [], fields: [] });
const NO_SHEET: SheetData = { name: "", cols: [], rows: [], cells: {}, merges: [], fields: [] };
/** How long the page waits for the room to say whether the table is live before editing alone. */
const ROOM_TIMEOUT_MS = 10000;

function parseRef(ref: string) {
  const [a, b] = ref.split(":");
  const p = (x: string) => { const m = /([A-Z]+)(\d+)/.exec(x)!; let c = 0; for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64; return [Number(m[2]) - 1, c - 1]; };
  const [r1, c1] = p(a), [r2, c2] = p(b || a);
  return { r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) };
}
const shown = (v: Cell["v"]) => (typeof v === "boolean" ? String(v).toUpperCase() : v);
const initialOf = (name: string) => (name || "—").trim().slice(0, 1).toUpperCase() || "—";

/** One sheet of the live table as the grid draws it (sizes defaulted as the editor writes them). */
function sheetOf(content: Content, sid: string): SheetData {
  const p = projectSheet(content, sid);
  return {
    name: p.name ?? "",
    cols: (p.cols || []).map((c: any) => ({ w: typeof c?.w === "number" ? c.w : 100 })),
    rows: (p.rows || []).map((r: any) => ({ h: typeof r?.h === "number" ? r.h : 24 })),
    cells: (p.cells || {}) as Record<string, Cell>,
    merges: p.merges || [],
    fields: (p.fields || []) as Field[],
  };
}

/** A row height or column width typed in: taken when the field is left or Enter is pressed. */
function SizeInput({ label, value, min, max, disabled, onCommit }: { label: string; value: number; min: number; max: number; disabled?: boolean; onCommit: (v: number) => void }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const take = () => {
    const n = Number(draft);
    if (!Number.isFinite(n) || draft.trim() === "") { setDraft(String(value)); return; }
    const v = Math.min(max, Math.max(min, Math.round(n)));
    setDraft(String(v));
    if (v !== value) onCommit(v);
  };
  return (
    <label className="small">{label}<input className="input sm" type="number" style={{ width: 56 }} value={draft} min={min} max={max} disabled={disabled}
      onChange={(e) => setDraft(e.target.value)} onBlur={take} onKeyDown={(e) => { if (isImeKey(e)) return; if (e.key === "Enter") { e.preventDefault(); take(); } }} /></label>
  );
}

export default function SheetEditor() {
  useI18n();
  const { tid } = useParams();
  const [sp] = useSearchParams();
  const projectId = sp.get("project");
  const nav = useNavigate();
  const toast = useToast();
  const { prompt, confirm } = useDialog();
  // The version this editor loaded (or last saved): sent as base_version so a save never
  // replaces a newer version without asking.
  const baseVersion = useRef<number | null>(null);
  const [name, setName] = useState("表格模板");
  const savedName = useRef<string | null>(null);
  // Save stays disabled until the stored template has actually arrived. Before, a slow or failed
  // load left the blank starting sheet on screen and 保存 overwrote the template with it.
  const [loaded, setLoaded] = useState(!tid);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [sheets, setSheets] = useState<SheetData[]>([newSheet("Sheet1")]);
  const [si, setSi] = useState(0);
  const [sel, setSel] = useState({ r1: 0, c1: 0, r2: 0, c2: 0 });
  // live, the cell editor belongs to the sheet it was opened on: deleted meanwhile, nothing is written elsewhere
  const [editing, setEditing] = useState<{ r: number; c: number; v: string; sid?: string | null } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");                     // names the version this save creates (a checkpoint in the history)
  const [hist, setHist] = useState<SheetData[][]>([]);
  // ---- the template's room: a live table (docs-sheet-live.md: everyone edits one shared table and
  // sees each other's changes as they happen) when the room says so, else who else has it open and
  // their saves (CI-47: each edits a copy, merged cell by cell on save)
  const sroomRef = useRef<SheetRoom | null>(null);
  if (tid && !sroomRef.current) sroomRef.current = openSheetRoom(tid, { origin: window.location.origin, workspaceId: workspaceContext() });
  const sroom = sroomRef.current;
  const [room, setRoom] = useState<RoomState>(initialState());
  const [snap, setSnap] = useState<SyncSnapshot | null>(null);
  const [roomTimedOut, setRoomTimedOut] = useState(false);
  const [forcedAlone, setForcedAlone] = useState(false);
  const [liveSid, setLiveSid] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [, force] = useReducer((n: number) => n + 1, 0);
  const wasLive = useRef(false);
  const cursor = useRef({ anchor: { r: 0, c: 0 }, active: { r: 0, c: 0 } });
  const select = (rect: typeof sel) => {
    cursor.current = { anchor: { r: rect.r1, c: rect.c1 }, active: { r: rect.r2, c: rect.c2 } };
    setSel(rect);
  };
  const drag = useRef<{ r: number; c: number } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [!!editing]);

  // ---- which mode: live once the room has said so; alone (CI-47) when it says not, or never answers
  const live = !!snap && snap.live && snap.status !== "single";
  const mode = editorMode({ live, forcedAlone, dirty, closedCode: snap?.closed?.code ?? null, wasLive: wasLive.current });
  const liveMode = mode.liveMode;
  // signed out while live: the table stays on the page as it was, read-only, with what did not reach the server
  const showLive = liveMode || mode.signedOut;
  const decided = !tid || roomTimedOut || (!!snap && snap.status !== "connecting");
  const ready = loaded && decided;
  const ro = (liveMode && !!snap?.readonly) || mode.signedOut;
  const content: Content | null = showLive && sroom ? sroom.sync.content : null;
  const sids = content ? liveSheetIds(content) : [];
  const activeSid = content ? (liveSid && sids.includes(liveSid) ? liveSid : sids[0] ?? null) : null;
  const liveSheets = useMemo(
    () => (content ? sids.map((sid) => (sid === activeSid ? sheetOf(content, sid) : { ...NO_SHEET, name: content.sheets[sid].name ?? "" })) : []),
    // the content changes in place: its version says when
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [snap?.version, activeSid, showLive],
  );
  const viewSheets = showLive ? liveSheets : sheets;
  const viewSi = showLive ? Math.max(0, activeSid ? sids.indexOf(activeSid) : 0) : si;
  const sh = viewSheets[viewSi] || NO_SHEET;
  const liveSheet = content && activeSid ? content.sheets[activeSid] : null;

  const loadTemplate = (quiet = false) => {
    if (!tid) return Promise.resolve();
    if (!quiet) { setLoaded(false); setLoadErr(null); }
    return get(`/api/templates/${tid}`).then((t) => {
      if (t.kind !== "builtin_sheet" || !t.version.editor?.sheets) {
        setLoadErr(tr("这个模板不是用表格编辑器创建的，不能在这里编辑。"));
        return;
      }
      setName(t.name);
      savedName.current = t.name;
      baseVersion.current = loadedVersion(t);
      setSheets(t.version.editor.sheets);
      setHist([]);
      setDirty(false);
      setLoaded(true);
    }).catch((e) => setLoadErr(errorText(e)));
  };
  useEffect(() => {
    if (!tid) return;
    let alive = true;
    loadTemplate().then(() => { if (!alive) return; });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tid]);

  useEffect(() => {
    const r = sroomRef.current;
    if (!r) return;
    const offRoom = r.room.subscribe(setRoom);
    const offSync = r.sync.subscribe(setSnap);
    return () => { offRoom(); offSync(); r.destroy(); sroomRef.current = null; };
  }, []);
  useEffect(() => {
    if (!tid) return;
    const timer = window.setTimeout(() => setRoomTimedOut(true), ROOM_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [tid]);
  useEffect(() => {
    if (!sroomRef.current || !room.me) return;
    sroomRef.current.room.setUser({ ...room.me, slot: room.me.color, readonly: room.readonly } as any);
  }, [room.me, room.readonly]);
  // where we are: the active cell of the active sheet (a stable address, not a screen position)
  useEffect(() => {
    if (!sroomRef.current || !ready) return;
    sroomRef.current.room.setFocus(sh ? { kind: "cell", id: `${sh.name}!${cellRef(sel.r1, sel.c1)}` } : null);
  }, [sel.r1, sel.c1, viewSi, ready, room.me, sh.name]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- live mode: coming in, leaving, and what the others do
  useEffect(() => {
    // live became possible while edits made alone are unsaved — the room answered after the page gave up
    // waiting, or came back after single-person mode: stay alone until they are saved (going live
    // would show the room's table instead and drop them)
    if (!mode.holdAlone) return;
    setForcedAlone(true);
    toast(wasLive.current ? tr("实时协同已恢复，但你在单人编辑中有未保存的修改：继续单人编辑，保存后回到实时协同。")
      : tr("实时协同已连上，但你已经开始单人编辑：继续单人编辑，保存时按单元格合并。"), "info");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode.holdAlone]);
  const prevLive = useRef(false);
  useEffect(() => {
    if (liveMode) {
      prevLive.current = true;
      wasLive.current = true;
      setDirty(false);
      setHist([]);
      return;
    }
    if (prevLive.current) {
      // left live mode (the table outgrew it, a version could not be held) or the connection ended:
      // from here the saved version, edited alone and merged on save
      prevLive.current = false;
      cancelEdit();
      // signed out (4401): loading would answer 401 and replace the page — and its list of unsent
      // edits — with the sign-in page; the table stays as it was, read-only
      if (!mode.signedOut) void loadTemplate(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode]);
  useEffect(() => { if (liveMode && sroom) sroom.sync.view(activeSid); }, [liveMode, activeSid, sroom]);
  // the sheet on screen was deleted (by someone, or undone): say so, and what was being typed there is
  // not written anywhere else
  const shownSid = useRef<string | null>(null);
  const myDelete = useRef<string | null>(null);
  useEffect(() => {
    if (!liveMode || !content) { shownSid.current = null; return; }
    const was = shownSid.current;
    shownSid.current = activeSid;
    if (!was || was === activeSid || !content.sheets[was]?.gone) return;
    const name = content.sheets[was].name ?? "";
    setLiveSid(activeSid);                         // stays on the sheet shown now, even if that one comes back
    select({ r1: 0, c1: 0, r2: 0, c2: 0 });
    const ed = editingRef.current;
    if (ed && ed.sid === was) {
      cancelEdit();
      if (ed.v) toast(tr("工作表「{v1}」已被删除，你在 {v2} 输入的「{v3}」没有写入。", { v1: name, v2: cellRef(ed.r, ed.c), v3: ed.v }), "err");
    } else if (myDelete.current !== was) {
      toast(tr("工作表「{v1}」已被删除；保存为版本之前，可以从「已删除的工作表」恢复。", { v1: name }), "info");
    }
    myDelete.current = null;
  });
  // tell the others where this tab is (row and column ids: stable while rows are added)
  const lastSel = useRef("");
  useEffect(() => {
    if (!liveMode || !sroom || !liveSheet || !activeSid) return;
    const at = { sheet: activeSid, r1: liveSheet.rows[sel.r1], c1: liveSheet.cols[sel.c1], r2: liveSheet.rows[sel.r2], c2: liveSheet.cols[sel.c2], typing: !!editing };
    if (![at.r1, at.c1, at.r2, at.c2].every(Boolean)) return;
    const key = JSON.stringify(at);
    if (key === lastSel.current) return;
    lastSel.current = key;
    sroom.select(at);
  });
  // cells others changed are tinted for ~3 s: draw again when that has passed
  useEffect(() => {
    if (!liveMode) return;
    const timer = window.setTimeout(force, 3200);
    return () => window.clearTimeout(timer);
  }, [snap?.version, liveMode]);
  // screen readers hear what others changed, at most every 2 s
  useEffect(() => {
    if (!liveMode || !sroom) return;
    let quiet = 0;
    let kind: StatusKind | null = statusKind(sroom.sync.status());
    const timer = window.setInterval(() => {
      const now = statusKind(sroom.sync.status());
      const lead = now && kind && now !== kind ? [statusText(sroom.sync.status(), sroom.sync.pendingCount)] : [];
      if (now) kind = now;
      const items = sroom.sync.takeAnnouncements();
      if (items.length || lead.length) { setSaid([...lead, ...items.slice(-3).map(announcementText)].join("；")); quiet = 0; }
      else if (++quiet === 3) setSaid("");                // said: an announcement is not left behind for later
    }, 2000);
    return () => window.clearInterval(timer);
  }, [liveMode, sroom]);

  // someone else saved (or restored) a version: live, the table already shows it; alone, take it
  // when nothing is pending here, say so otherwise
  useEffect(() => {
    const h = room.lastHead;
    if (!h || !room.me || !ready) return;
    if (h.byId && h.byId === room.me.id) return;             // our own save: the response already carried it
    if (h.head && h.head === baseVersion.current) return;
    const who = h.by || tr("成员");
    const what = h.reason === "save" ? tr("保存") : h.reason === "restore" ? tr("恢复") : h.reason === "undo" ? tr("撤销") : h.reason === "redo" ? tr("重做") : tr("更换");
    if (liveMode) {
      if (h.head) baseVersion.current = h.head;
      toast(tr("{v1} 已{v2}第 {v3} 版", { v1: who, v2: what, v3: h.head }), "info");
    } else if (dirty) {
      toast(tr("{v1} 已{v2}第 {v3} 版；你的修改会在保存时按单元格与之合并", { v1: who, v2: what, v3: h.head }), "info");
    } else {
      void loadTemplate(true).then(() => toast(tr("{v1} 已{v2}第 {v3} 版，已载入", { v1: who, v2: what, v3: h.head }), "info"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.lastHead]);
  const focusLabel = (f: Focus): string | null => {
    if (f.kind !== "cell" && f.kind !== "sheet") return null;
    const [sheetName, ref] = f.id.includes("!") ? [f.id.slice(0, f.id.lastIndexOf("!")), f.id.slice(f.id.lastIndexOf("!") + 1)] : [f.id, ""];
    if (!viewSheets.some((x) => x.name === sheetName)) return null;
    return ref ? (viewSheets.length > 1 ? tr("{v1} 的 {v2}", { v1: sheetName, v2: ref }) : ref) : sheetName;
  };
  const locateFocus = (f: Focus) => {
    const sheetName = f.id.includes("!") ? f.id.slice(0, f.id.lastIndexOf("!")) : f.id;
    const ref = f.id.includes("!") ? f.id.slice(f.id.lastIndexOf("!") + 1) : "";
    const i = viewSheets.findIndex((x) => x.name === sheetName);
    if (i < 0) return;
    if (i !== viewSi) chooseSheet(i);
    if (ref) { const x = parseRef(ref); select({ r1: x.r1, c1: x.c1, r2: x.r1, c2: x.c1 }); }
  };

  // Closing the tab or reloading with unsaved edits asks first (this editor had no guard at all);
  // live, only what has not reached the server yet is at risk (the room keeps the rest)
  const unsynced = liveMode ? snap?.pending ?? 0 : 0;
  // signed out while live: what did not reach the server exists only in the list on this page
  const stranded = mode.signedOut ? snap?.attention.length ?? 0 : 0;
  const unpersisted = liveMode ? snap?.unpersisted ?? 0 : 0;
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (leaveAtRisk({ liveMode, unsynced, unpersisted, dirty, stranded })) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, liveMode, unsynced, unpersisted, stranded]);

  // In-app navigation does not fire beforeunload, so 返回 asks itself.
  const back = async () => {
    if (liveMode) {
      if (unsynced > 0 && !(await confirm(tr("还有修改没有同步？"), tr("有 {count} 处修改还没有送到服务器，离开后会丢失。", { count: unsynced }), tr("仍然离开"), true))) return;
      if (unsynced === 0 && unpersisted > 0 && sroom) {
        // answered, not yet in the room's draft: the room writes it within seconds — wait for that
        // (this page is what could send it again if the server died meanwhile)
        setBusy(true);
        await new Promise<void>((resolve) => {
          let off: (() => void) | null = null;
          let settled = false;
          const end = () => { if (settled) return; settled = true; off?.(); resolve(); };
          off = sroom.sync.subscribe((s) => { if (!s.unpersisted) end(); });   // (called at once with the current state)
          if (settled) off();
          window.setTimeout(end, 7000);
        });
        setBusy(false);
      }
    } else if ((dirty || stranded > 0) && !(await confirm(tr("放弃未保存的修改？"), tr("模板有尚未保存的修改，离开后这些修改会丢失。"), tr("放弃修改并离开"), true))) return;
    nav(projectId ? `/p/${projectId}` : "/");
  };

  // ---- editing alone (CI-47): the editor's own copy of the model
  const mutate = (fn: (s: SheetData) => SheetData) => {
    setHist((h) => [...h.slice(-50), sheets]);
    setSheets(sheets.map((s, i) => (i === si ? fn(structuredClone(s)) : s)));
    setDirty(true);
  };
  // ---- editing live: operations on the shared table, shown at once, sent in order
  const send = (ops: any[]): boolean => {
    if (!ops.length || !sroom) return true;
    if (!liveSheet) { toast(tr("先添加一个工作表。"), "info"); return false; }
    for (let i = 0; i < ops.length; i += MAX_TX) {
      const chunk = ops.slice(i, i + MAX_TX);
      if (!sroom.sync.submit(chunk.length === 1 ? chunk[0] : { op: "tx", ops: chunk })) {
        toast(ro ? tr("你现在只能查看这个模板。") : tr("现在不能修改：实时协同尚未就绪。"), "err");
        return false;
      }
    }
    return true;
  };
  const at = (r: number, c: number) => ({ sheet: activeSid, row: liveSheet?.rows[r], col: liveSheet?.cols[c] });
  /** rows and columns added at the end when typing near the edge (as the editor always did) */
  const grow = (r: number, c: number): any[] => {
    const s = liveSheet!;
    const ops: any[] = [];
    if (r + 5 >= s.nr) { const n = r + 6 - s.nr; ops.push({ op: "append_rows", sheet: activeSid, count: n, ids: Array.from({ length: n }, randomId), h: 24 }); }
    if (c + 2 >= s.nc) { const n = c + 3 - s.nc; ops.push({ op: "append_cols", sheet: activeSid, count: n, ids: Array.from({ length: n }, randomId), w: 100 }); }
    return ops;
  };

  const undone = (r: UndoResult | null) => {
    if (!r) toast(tr("没有可撤销的修改。"), "info");
    else if (r.skipped.length) toast(r.skipped.map((s) => skippedText(s, sids.length)).join(" "), "info");
  };
  // an undo pressed while my change was still on its way happens once the server has answered it
  useEffect(() => {
    if (!sroom) return;
    sroom.sync.onUndone = undone;
    return () => { sroom.sync.onUndone = null; };
  });
  const undo = () => {
    if (liveMode && sroom) {
      const r = sroom.sync.undo();
      if (r !== "deferred") undone(r);
      return;
    }
    const h = hist[hist.length - 1]; if (h) { setSheets(h); setHist(hist.slice(0, -1)); chooseSheet(Math.min(si, h.length - 1)); setDirty(true); }
  };

  const cellsInSel = () => { const out: string[] = []; for (let r = sel.r1; r <= sel.r2; r++) for (let c = sel.c1; c <= sel.c2; c++) out.push(cellRef(r, c)); return out; };
  const style = (stylePatch: any) => {
    if (liveMode) {
      const ops: any[] = [];
      for (let r = sel.r1; r <= sel.r2; r++) for (let c = sel.c1; c <= sel.c2; c++) ops.push({ op: "style", ...at(r, c), s: stylePatch });
      send(ops);
      return;
    }
    mutate((s) => { for (const ref of cellsInSel()) { const c = s.cells[ref] || {}; c.s = { ...(c.s || {}), ...stylePatch }; for (const k of Object.keys(c.s)) if (c.s[k] === null) delete c.s[k]; s.cells[ref] = c; } return s; });
  };
  const cur = sh.cells[cellRef(sel.r1, sel.c1)] || {};

  // The edit being committed, read through a ref: Enter commits and then moves focus back to the
  // grid, which blurs the input and would otherwise commit the same edit a second time.
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const numeric = (v: string) => /^-?\d+(\.\d+)?$/.test(v) && !/^0\d/.test(v);
  const commit = () => {
    const cur0 = editingRef.current;
    if (!cur0) return;
    editingRef.current = null;
    const { r, c, v } = cur0;
    if (liveMode) {
      if (cur0.sid && (cur0.sid !== activeSid || !sheetStillThere(content, cur0.sid))) {
        // opened on a sheet that is gone (or no longer shown): never written into another one
        setEditing(null);
        const name = cur0.sid && content?.sheets[cur0.sid] ? content.sheets[cur0.sid].name ?? "" : "";
        if (v) toast(tr("工作表「{v1}」已被删除，你在 {v2} 输入的「{v3}」没有写入。", { v1: name, v2: cellRef(r, c), v3: v }), "err");
        return;
      }
      if (liveSheet && r < liveSheet.rows.length && c < liveSheet.cols.length) {
        const old = sh.cells[cellRef(r, c)];
        const same = (old?.v ?? "") === v && (old?.t ?? null) === (v && numeric(v) ? "number" : null);
        if (!same) send([...grow(r, c), v ? { op: "set", ...at(r, c), v, ...(numeric(v) ? { t: "number" } : {}) } : { op: "clear", ...at(r, c) }]);
      }
      setEditing(null);
      return;
    }
    mutate((s) => {
      const ref = cellRef(r, c);
      const cell = s.cells[ref] || {};
      cell.v = v;
      cell.t = numeric(v) ? "number" : undefined;
      if (!v) delete cell.v;
      s.cells[ref] = cell;
      while (s.rows.length <= r + 5) s.rows.push({ h: 24 });
      while (s.cols.length <= c + 2) s.cols.push({ w: 100 });
      return s;
    });
    setEditing(null);
  };
  /** Escape: throw the edit away. The ref is cleared before focus goes back to the grid — that
   *  focus change blurs the input, and the blur's commit() would otherwise read the edit from the
   *  ref (state only catches up on the next render) and write the keystroke that was cancelled. */
  function cancelEdit() {
    editingRef.current = null;
    setEditing(null);
  }

  function chooseSheet(i: number) {
    cancelEdit();
    drag.current = null;
    select({ r1: 0, c1: 0, r2: 0, c2: 0 });
    if (liveMode) setLiveSid(sids[i] ?? null);
    else setSi(i);
  }

  const overlapsSel = (x: { r1: number; c1: number; r2: number; c2: number }) => !(x.r2 < sel.r1 || x.r1 > sel.r2 || x.c2 < sel.c1 || x.c1 > sel.c2);
  const merge = async () => {
    if (sel.r1 === sel.r2 && sel.c1 === sel.c2) return;
    const ref = rangeRef(sel.r1, sel.c1, sel.r2, sel.c2);
    if (liveMode && liveSheet && content) {
      // a merged range keeps only its top-left value: the others are cleared first, in the same group, once confirmed
      const hidden = cellsInSel().filter((x) => x !== cellRef(sel.r1, sel.c1) && filled(sh.cells[x]));
      if (hidden.length && !(await confirm(tr("合并会清除其他单元格的值"), tr("合并 {v1} 只保留左上角的值；{v2} 的内容会被清除。", { v1: ref, v2: hidden.slice(0, 6).join("、") + (hidden.length > 6 ? "…" : "") }), tr("清除并合并"), true))) return;
      const replaces = liveSheet.merges.filter((mid, k) => overlapsSel(parseRef(sh.merges[k])));
      const clears = hidden.map((x) => { const p = parseRef(x); return { op: "clear", ...at(p.r1, p.c1) }; });
      send([...clears, { op: "merge", sheet: activeSid, range: [liveSheet.rows[sel.r1], liveSheet.cols[sel.c1], liveSheet.rows[sel.r2], liveSheet.cols[sel.c2]], id: randomId(), ...(replaces.length ? { replaces } : {}) }]);
      return;
    }
    mutate((s) => { s.merges = s.merges.filter((m) => !overlapsSel(parseRef(m))); s.merges.push(ref); return s; });
  };
  const unmerge = () => {
    if (liveMode && liveSheet) {
      send(liveSheet.merges.filter((mid, k) => overlapsSel(parseRef(sh.merges[k]))).map((id) => ({ op: "unmerge", id })));
      return;
    }
    mutate((s) => { s.merges = s.merges.filter((m) => !overlapsSel(parseRef(m))); return s; });
  };
  const setWidth = (w: number) => {
    if (liveMode) { const ops: any[] = []; for (let c = sel.c1; c <= sel.c2; c++) ops.push({ op: "size", sheet: activeSid, col: liveSheet!.cols[c], w }); send(ops); return; }
    mutate((s) => { for (let c = sel.c1; c <= sel.c2; c++) s.cols[c].w = w; return s; });
  };
  const setHeight = (h: number) => {
    if (liveMode) { const ops: any[] = []; for (let r = sel.r1; r <= sel.r2; r++) ops.push({ op: "size", sheet: activeSid, row: liveSheet!.rows[r], h }); send(ops); return; }
    mutate((s) => { for (let r = sel.r1; r <= sel.r2; r++) s.rows[r].h = h; return s; });
  };
  const addRows = () => {
    if (liveMode) { send([{ op: "append_rows", sheet: activeSid, count: 10, ids: Array.from({ length: 10 }, randomId), h: 24 }]); return; }
    mutate((s) => { s.rows.push(...Array.from({ length: 10 }, () => ({ h: 24 }))); return s; });
  };
  const addCol = () => {
    if (liveMode) { send([{ op: "append_cols", sheet: activeSid, count: 1, ids: [randomId()], w: 100 }]); return; }
    mutate((s) => { s.cols.push({ w: 100 }); return s; });
  };

  const fieldRect = (f: Field) => (f.kind === "repeat" ? { r1: f.row!, c1: f.c1!, r2: f.row!, c2: f.c2! } : parseRef(f.ref!));
  const addField = async (kind: Field["kind"]) => {
    if (sh.fields.some((f) => overlapsSel(fieldRect(f)))) { toast(tr("选区与已有字段重叠"), "err"); return; }
    const single = sel.r1 === sel.r2 && sel.c1 === sel.c2;
    if (kind === "cell" && !single) { toast(tr("字段单元格只能是一个单元格（合并单元格请选左上角）"), "err"); return; }
    if (kind === "repeat" && sel.r1 !== sel.r2) { toast(tr("重复明细行只能选择一行中的连续单元格"), "err"); return; }
    const n = await prompt(kind === "cell" ? tr("字段单元格") : kind === "region" ? tr("固定区域") : tr("重复明细行"), tr("名称"), kind === "repeat" ? tr("明细") : "");
    if (!n) return;
    if (liveMode && liveSheet) {
      const place = kind === "cell" ? { row: liveSheet.rows[sel.r1], col: liveSheet.cols[sel.c1] }
        : kind === "region" ? { range: [liveSheet.rows[sel.r1], liveSheet.cols[sel.c1], liveSheet.rows[sel.r2], liveSheet.cols[sel.c2]] }
        : { row: liveSheet.rows[sel.r1], c1: liveSheet.cols[sel.c1], c2: liveSheet.cols[sel.c2] };
      send([{ op: "field_add", sheet: activeSid, field: { id: rid(), name: n, kind, ...(kind === "repeat" ? { empty_policy: "remove_row" } : {}) }, ...place }]);
      return;
    }
    const f: Field = kind === "repeat" ? { id: rid(), name: n, kind, row: sel.r1, c1: sel.c1, c2: sel.c2, empty_policy: "remove_row" } : { id: rid(), name: n, kind, ref: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2) };
    mutate((s) => { s.fields.push(f); return s; });
  };
  const setRequired = (i: number, required: boolean) => {
    if (liveMode && liveSheet) { send([{ op: "field_set", field: liveSheet.fields[i], set: { required } }]); return; }
    mutate((s) => { s.fields[i].required = required; return s; });
  };
  const deleteField = (i: number) => {
    if (liveMode && liveSheet) { send([{ op: "field_del", field: liveSheet.fields[i] }]); return; }
    mutate((s) => { s.fields.splice(i, 1); return s; });
  };

  const renameSheet = async (i: number) => {
    const s = viewSheets[i];
    const n = await prompt(tr("重命名工作表"), tr("名称"), s.name);
    if (!n || n === s.name) return;
    if (liveMode) { send([{ op: "sheet_rename", sheet: sids[i], name: n }]); return; }
    setHist([...hist.slice(-50), sheets]); setSheets(sheets.map((x, k) => (k === i ? { ...x, name: n } : x))); setDirty(true);
  };
  const addSheet = () => {
    const taken = new Set(viewSheets.map((s) => s.name.toLowerCase()));
    let k = viewSheets.length + 1;
    while (taken.has(`sheet${k}`)) k++;
    if (liveMode) {
      const sid = randomId();
      if (sroom?.sync.submit({ op: "sheet_add", id: sid, name: `Sheet${k}`, rows: 30, cols: 10, row_ids: Array.from({ length: 30 }, randomId), col_ids: Array.from({ length: 10 }, randomId), h: 24, w: 100 })) {
        cancelEdit(); select({ r1: 0, c1: 0, r2: 0, c2: 0 }); setLiveSid(sid);
      }
      return;
    }
    setHist([...hist.slice(-50), sheets]); setSheets([...sheets, newSheet(`Sheet${k}`)]); chooseSheet(sheets.length); setDirty(true);
  };
  const deleteSheet = async () => {
    if (liveMode) {
      if (!(await confirm(tr("删除工作表「{v1}」？", { v1: sh.name }), tr("正在这张表上编辑的成员会一起看到它被删除；保存为版本之前，可以从「已删除的工作表」恢复。"), tr("删除工作表"), true))) return;
      myDelete.current = activeSid;
      if (send([{ op: "sheet_delete", sheet: activeSid }])) chooseSheet(0);
      return;
    }
    setHist([...hist.slice(-50), sheets]); setSheets(sheets.filter((_, k) => k !== si)); chooseSheet(0); setDirty(true);
  };

  const restoreSheet = (sid: string) => {
    if (!liveMode || !content || !sid || !content.sheets[sid]?.gone) return;
    const name = restoreName(content, sid);
    if (send([{ op: "sheet_restore", sheet: sid, ...(name !== content.sheets[sid].name ? { name } : {}) }])) {
      cancelEdit(); select({ r1: 0, c1: 0, r2: 0, c2: 0 }); setLiveSid(sid);
    }
  };
  const gone = liveMode ? deletedSheets(content) : [];

  // The template's name is not part of the live table: live, it is renamed on its own.
  const renameTemplate = async () => {
    if (!liveMode || !tid || !name.trim() || name === savedName.current) return;
    try { await patch(`/api/templates/${tid}`, { name: name.trim() }); savedName.current = name.trim(); }
    catch (e) { toast(errorText(e), "err"); setName(savedName.current || name); }
  };

  const saveLive = async () => {
    if (!sroom || busy) return;
    setBusy(true);
    try {
      // what was typed before pressing save is in the save: wait (briefly) until the server has it
      if (sroom.sync.pendingCount) {
        await new Promise<void>((resolve) => {
          const off = sroom.sync.subscribe((s) => { if (!s.pending) { off(); resolve(); } });
          window.setTimeout(() => { off(); resolve(); }, 5000);
        });
      }
      if (sroom.sync.pendingCount) { toast(tr("还有 {count} 处修改没有同步，稍后再保存。", { count: sroom.sync.pendingCount }), "err"); return; }
      const res = await post(`/api/templates/${tid}/collab/save`, note.trim() ? { note: note.trim() } : {});
      if (res?.head) baseVersion.current = res.head;
      const [msg, tone] = saveText(res);
      toast(msg, tone);
      setNote("");
    } catch (e: any) {
      toast(saveRefusalText(e, !!snap?.stale) || errorText(e), "err");
    } finally {
      setBusy(false);
    }
  };

  const saveTpl = async () => {
    if (!ready || busy) return;
    if (liveMode) { await saveLive(); return; }
    setBusy(true);
    try {
      const editor = { sheets };
      // Someone else's save since this template was loaded is merged cell by cell on the server;
      // only a cell, a field or a sheet's layout both sides changed comes back as a conflict.
      const send1 = async (base: number | null) => {
        try {
          // saved alone: `solo` always — the room may be live for others (docs-sheet-live.md §3)
          return await put(`/api/templates/${tid}/builtin`, aloneSaveBody(editor, name, note, base));
        } catch (e: any) {
          const conflicts: any[] | undefined = e?.code === "stale_template" ? e.detail?.conflicts : undefined;
          if (!conflicts?.length) throw e;
          const reasons: Record<string, string> = { both_changed: tr("双方都改了它"), deleted_by_other: tr("对方已删除它"), deleted_by_you: tr("你删除了它，对方改了它"), both_added: tr("双方各自新增") };
          const list = conflicts.map((c) => (c.kind === "cell" ? tr("单元格 {v1}（{v2}）", { v1: c.ref, v2: c.sheet }) : c.kind === "field" ? tr("字段「{v1}」", { v1: c.name || c.id }) : c.kind === "layout" ? tr("工作表「{v1}」的行列与合并", { v1: c.sheet }) : tr("工作表「{v1}」", { v1: c.sheet })) + "：" + (reasons[c.reason] || c.reason)).join("；");
          const mine = await confirm(tr("表格有冲突"), tr("其他成员在你之后也保存了这个模板。没有冲突的单元格已可合并；下面这些双方都动过，请选择保留哪一方：{v1}", { v1: list }), tr("以我的为准"), true);
          if (!mine) return null;
          const forceIds = conflicts.map((c) => (c.kind === "cell" ? `cell:${c.sheet}!${c.ref}` : c.kind === "field" ? `field:${c.id}` : c.kind === "layout" ? `layout:${c.sheet}` : `sheet:${c.sheet}`));
          return await put(`/api/templates/${tid}/builtin`, aloneSaveBody(editor, name, note, base, forceIds));
        }
      };
      const t = tid
        ? await saveOverStale(send1, baseVersion.current, (theirs, mine, why) => confirm(tr(STALE_TITLE), staleText(theirs, mine) + (why ? tr("两边的修改合在一起无法生成模板：{v1}", { v1: issueText({ code: "bad_editor", message: why }) }) : ""), tr("仍然保存"), true))
        : await post("/api/templates/builtin", { kind: "builtin_sheet", name, editor, project_id: projectId });
      if (!t) { toast(tr("没有保存：已改为对方的版本，你的修改仍在编辑器中"), "info"); await loadTemplate(true); return; }
      baseVersion.current = loadedVersion(t);
      savedName.current = t.name ?? name;
      setDirty(false);
      setForcedAlone(false);                       // saved: nothing of this tab's own is left to keep it alone
      setNote("");
      if (t.unchanged) toast(tr("内容与已保存的第 {v1} 版相同，无需新版本", { v1: t.version.no }), "info");
      else toast(t.merged_with ? tr("已保存并与第 {v1} 版合并（{v2} 个字段，v{v3}）", { v1: t.merged_with, v2: t.version.slots.length, v3: t.version.no }) : tr("已保存（{v1} 个字段，v{v2}）", { v1: t.version.slots.length, v2: t.version.no }), "ok");
      if (!tid) nav(`/templates/${t.id}/edit${projectId ? `?project=${projectId}` : ""}`, { replace: true });
    } catch (e: any) {
      toast(errorText(e), "err");
    } finally {
      setBusy(false);
    }
  };

  const colX = [0];
  for (const c of sh.cols) colX.push(colX[colX.length - 1] + c.w);
  const rowY = [0];
  for (const r of sh.rows) rowY.push(rowY[rowY.length - 1] + r.h);
  const covered = new Map<string, string>();
  for (const m of sh.merges) { const x = parseRef(m); for (let r = x.r1; r <= x.r2; r++) for (let c = x.c1; c <= x.c2; c++) covered.set(cellRef(r, c), m); }
  const fieldAt = (r: number, c: number) => sh.fields.find((f) => { const x = fieldRect(f); return r >= x.r1 && r <= x.r2 && c >= x.c1 && c <= x.c2; });
  // live: where the others are on this sheet, and which cells they just changed
  const rowIndex = useMemo(() => new Map((liveSheet?.rows ?? []).map((x, i) => [x, i])), [snap?.version, activeSid, showLive]);   // eslint-disable-line react-hooks/exhaustive-deps
  const colIndex = useMemo(() => new Map((liveSheet?.cols ?? []).map((x, i) => [x, i])), [snap?.version, activeSid, showLive]);   // eslint-disable-line react-hooks/exhaustive-deps
  const others = liveMode && sroom && activeSid ? sroom.others().filter((o) => o.sel.sheet === activeSid) : [];
  const dots = liveMode && sroom ? sroom.sync.changedSheets() : new Map<string, Set<string>>();

  const onKey = (e: React.KeyboardEvent) => {
    if (busy || !ready || editing || e.target !== e.currentTarget) return;
    if ((e.metaKey || e.ctrlKey) && e.key === "z") { e.preventDefault(); if (!ro) undo(); return; }
    // Tab is not captured: it leaves the grid like everywhere else (WCAG 2.1.2 No Keyboard Trap).
    // Enter moves down, arrows move; F2 or typing edits.
    const mv: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1], Enter: [1, 0] };
    if (mv[e.key]) {
      e.preventDefault();
      const next = moveCursor(cursor.current.anchor, cursor.current.active, ...mv[e.key], e.shiftKey, sh.rows.length, sh.cols.length);
      cursor.current = next;
      setSel(next.rect);
      return;
    }
    if (ro) return;
    if (isImeKey(e)) {
      // An input method starting on the grid: open the cell editor and let the composition
      // continue there, rather than turning the keystroke into a character of its own.
      setEditing({ r: sel.r1, c: sel.c1, v: "", sid: activeSid });
      return;
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      if (liveMode) {
        const ops: any[] = [];
        for (let r = sel.r1; r <= sel.r2; r++) for (let c = sel.c1; c <= sel.c2; c++) if (filled(sh.cells[cellRef(r, c)])) ops.push({ op: "clear", ...at(r, c) });
        send(ops);
      } else mutate((s) => { for (const ref of cellsInSel()) if (s.cells[ref]) delete s.cells[ref].v; return s; });
    } else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) {
      setEditing({ r: sel.r1, c: sel.c1, v: e.key, sid: activeSid });
      e.preventDefault();
    } else if (e.key === "F2") {
      setEditing({ r: sel.r1, c: sel.c1, v: cur.v == null ? "" : String(cur.v), sid: activeSid });
    }
  };

  if (loadErr) return (
    <PageState role="alert">
      <div className="big">{tr("无法打开模板")}</div>
      <div className="notice err" style={{ maxWidth: 480 }}>{serverText(loadErr)}</div>
      <button type="button" className="btn" onClick={() => nav(projectId ? `/p/${projectId}` : "/")}>{tr("返回")}</button>
    </PageState>
  );

  const showSync = liveMode || wasLive.current;
  const retry = sroom?.retrying() ?? null;
  const notice = mode.signedOut && stranded > 0 ? { tone: "err", text: tr("登录已失效：实时协同已停止。下面没有同步的修改只在这个页面里：先复制需要的值，再重新登录并刷新。") }
    : snap?.closed && wasLive.current ? { tone: "err", text: closeText(snap.closed.code, snap.closed.reason) }
    : wasLive.current && !liveMode && snap && !snap.live && snap.liveReason ? { tone: "info", text: liveReasonText(snap.liveReason) }
    : !wasLive.current && snap && snap.status === "single" && !snap.live && snap.liveReason ? { tone: "info", text: liveReasonText(snap.liveReason) }
    : null;
  const attention: Attention[] = liveMode || wasLive.current ? snap?.attention ?? [] : [];
  const copyValue = (v: string) => { void navigator.clipboard?.writeText(v).then(() => toast(tr("已复制"), "ok"), () => toast(v, "info")); };

  return (
    <div className="editor-shell" inert={busy || !ready} onMouseUp={() => (drag.current = null)}>
      <div className="topbar">
        <LanguageMenu />
        <button type="button" className="btn ghost sm" onClick={back}>{tr("← 返回")}</button>
        <input className="input" value={name} onChange={(e) => { setName(e.target.value); if (!liveMode) setDirty(true); }} onBlur={renameTemplate}
          onKeyDown={(e) => { if (isImeKey(e)) return; if (e.key === "Enter" && liveMode) (e.target as HTMLInputElement).blur(); }} style={{ width: 240, fontWeight: 600 }} aria-label={tr("模板名称")} disabled={ro} />
        <span className="badge">{tr("表格型模板 → XLSX")}</span>
        {!ready ? <span className="badge info" role="status">{tr("加载中…")}</span>
          : (liveMode ? room.dirty : dirty) ? <span className="badge warn" role="status">{tr("未保存")}</span> : <span className="badge ok" role="status">{tr("已保存")}</span>}
        {showSync && snap && (
          <span className={`badge ${snap.status === "live" ? "ok" : snap.status === "offline" || snap.status === "single" ? "warn" : "info"}`} data-testid="sheet-sync"
            title={snap.status === "offline" ? retryText(retry ?? 1006) : snap.status === "live" && snap.persisted < snap.seq ? tr("服务器正在保存草稿") : undefined}>
            {statusText(snap.status, snap.pending)}
          </span>
        )}
        {ro && <span className="badge" role="status">{tr("只能查看")}</span>}
        <span className="spacer" />
        {tid && <PresenceBar room={room} labelFor={focusLabel} onLocate={locateFocus} testId="sheet-presence" />}
        {tid && <input className="input sm version-note" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder={tr("版本说明（可选）")} aria-label={tr("这次保存的版本说明，可留空")} disabled={busy || ro} data-testid="version-note" />}
        <button type="button" className="btn primary" disabled={busy || !ready || ro || (liveMode && snap?.status === "offline")} onClick={saveTpl} data-testid="sheet-save">{busy ? tr("保存中…") : tr("保存模板")}</button>
      </div>
      {notice && <div className={`collab-notice ${notice.tone === "err" ? "err" : "info"}`} role="status">{notice.text}</div>}
      {liveMode && snap?.status === "offline" && <div className="collab-notice" role="status">{retryText(retry ?? 1006)}</div>}
      {liveMode && snap?.stale && <div className="collab-notice" role="status">{tr("外部保存的新版本无法并入这里的修改：你们的修改都还在；保存会被拒绝，直到有权限的成员确认放弃它们。")}</div>}
      <fieldset className="toolbar-set" disabled={ro} style={{ display: "contents" }}>
        <div className="toolbar">
          <span className="cellinfo">{rangeRef(sel.r1, sel.c1, sel.r2, sel.c2)}</span>
          <button type="button" className={`btn sm${cur.s?.b ? " active" : ""}`} aria-pressed={!!cur.s?.b} aria-label={tr("加粗")} title={tr("加粗")} onClick={() => style({ b: cur.s?.b ? null : 1 })}><b>B</b></button>
          <button type="button" className={`btn sm${cur.s?.i ? " active" : ""}`} aria-pressed={!!cur.s?.i} aria-label={tr("斜体")} title={tr("斜体")} onClick={() => style({ i: cur.s?.i ? null : 1 })}><i>I</i></button>
          <button type="button" className={`btn sm${cur.s?.u ? " active" : ""}`} aria-pressed={!!cur.s?.u} aria-label={tr("下划线")} title={tr("下划线")} onClick={() => style({ u: cur.s?.u ? null : 1 })}><u>U</u></button>
          <select className="input sm" aria-label={tr("字号")} value={cur.s?.sz || 11} onChange={(e) => style({ sz: Number(e.target.value) })}>{[9, 10, 11, 12, 14, 16, 18, 20, 24].map((x) => <option key={x}>{x}</option>)}</select>
          <label className="small">{tr("字")}<input type="color" value={cur.s?.color || "#000000"} onChange={(e) => style({ color: e.target.value })} style={{ width: 26, height: 22, border: 0 }} aria-label={tr("文字颜色")} /></label>
          <label className="small">{tr("底")}<input type="color" value={cur.s?.bg || "#ffffff"} onChange={(e) => style({ bg: e.target.value })} style={{ width: 26, height: 22, border: 0 }} aria-label={tr("底色")} /></label>
          <button type="button" className="btn sm" onClick={() => style({ bg: null })}>{tr("清底色")}</button>
          {(["left", "center", "right"] as const).map((a) => <button type="button" key={a} className={`btn sm${cur.s?.ha === a ? " active" : ""}`} aria-pressed={cur.s?.ha === a} onClick={() => style({ ha: a })}>{({ left: tr("左"), center: tr("中"), right: tr("右") })[a]}</button>)}
          <button type="button" className={`btn sm${cur.s?.wrap ? " active" : ""}`} aria-pressed={!!cur.s?.wrap} onClick={() => style({ wrap: cur.s?.wrap ? null : 1 })}>{tr("换行")}</button>
          <button type="button" className={`btn sm${cur.s?.bd ? " active" : ""}`} aria-pressed={!!cur.s?.bd} onClick={() => style({ bd: cur.s?.bd ? null : 1 })}>{tr("边框")}</button>
          <select className="input sm" aria-label={tr("数字格式")} value={cur.s?.nf || ""} onChange={(e) => style({ nf: e.target.value || null })}>
            <option value="">{tr("常规")}</option><option value="@">{tr("文本")}</option><option value="0">0</option><option value="0.00">0.00</option><option value="#,##0.00">#,##0.00</option><option value="¥#,##0.00">¥#,##0.00</option><option value="0%">0%</option><option value="yyyy-mm-dd">yyyy-mm-dd</option><option value='yyyy"年"m"月"d"日"'>yyyy年m月d日</option>
          </select>
          <button type="button" className="btn sm" onClick={merge}>{tr("合并")}</button>
          <button type="button" className="btn sm" onClick={unmerge}>{tr("取消合并")}</button>
          <SizeInput label={tr("列宽")} value={sh.cols[sel.c1]?.w || 100} min={20} max={COL_WIDTH[1]} onCommit={setWidth} disabled={!sh.cols.length} />
          <SizeInput label={tr("行高")} value={sh.rows[sel.r1]?.h || 24} min={12} max={ROW_HEIGHT[1]} onCommit={setHeight} disabled={!sh.rows.length} />
          <button type="button" className="btn sm" onClick={addRows}>{tr("+10 行")}</button>
          <button type="button" className="btn sm" onClick={addCol}>{tr("+1 列")}</button>
          <button type="button" className="btn sm" onClick={undo} disabled={liveMode ? !snap?.canUndo : !hist.length} aria-label={liveMode ? tr("撤销我的上一步修改") : tr("撤销")} title={liveMode ? tr("撤销我的上一步修改（⌘Z）；别人之后改过的单元格不会被撤销") : tr("撤销（⌘Z）")}>↶</button>
        </div>
        <div className="toolbar">
          <b className="small">{tr("字段：")}</b>
          <button type="button" className="btn sm primary" onClick={() => addField("cell")}>{tr("设为字段单元格")}</button>
          <button type="button" className="btn sm" onClick={() => addField("region")}>{tr("设为固定区域")}</button>
          <button type="button" className="btn sm" onClick={() => addField("repeat")}>{tr("设为重复明细行")}</button>
          <span className="tiny muted">{tr("重复明细行：选一行中的连续单元格，上一行为表头；生成时按明细条数插入行。")}</span>
        </div>
      </fieldset>
      {attention.length > 0 && (
        <section className="sheet-attn" aria-label={tr("需要你处理")} data-testid="sheet-attention">
          <h2 className="sheet-attn-h">{tr("需要你处理（{count}）", { count: attention.length })}</h2>
          <ul>
            {attention.map((a) => (
              <li key={a.id} data-kind={a.kind} data-code={a.code}>
                <span className="grow">{attentionText(a, Math.max(sids.length, viewSheets.length))}</span>
                {a.retry && (a.kind === "conflict" || a.kind === "overwritten") ? (
                  <>
                    <button type="button" className="btn xs primary" onClick={() => sroom?.sync.useMine(a.id)}>{tr("用我的值")}</button>
                    <button type="button" className="btn xs" onClick={() => sroom?.sync.dismiss(a.id)}>{tr("保留对方的")}</button>
                  </>
                ) : a.retry ? (
                  <>
                    <button type="button" className="btn xs primary" onClick={() => sroom?.sync.useMine(a.id)}>{tr("再做一次")}</button>
                    <button type="button" className="btn xs" onClick={() => sroom?.sync.dismiss(a.id)}>{tr("不用了")}</button>
                  </>
                ) : (
                  <>
                    {a.label.value && <button type="button" className="btn xs" onClick={() => copyValue(a.label.value!)}>{tr("复制我的值")}</button>}
                    <button type="button" className="btn xs" onClick={() => sroom?.sync.dismiss(a.id)}>{tr("知道了")}</button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="sr-only" aria-live="polite" role="status" data-testid="sheet-announce">{said}</div>
      <div className="sheet-body" style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div className="grid-scroll" ref={gridRef} tabIndex={0} onKeyDown={onKey}
          aria-label={tr("表格编辑区，当前 {v1}。方向键移动，Shift+方向键扩展选区，直接输入或 F2 编辑，Tab 离开表格", { v1: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2) })}>
          <div className="grid-canvas" style={{ width: 46 + colX[colX.length - 1], height: 22 + rowY[rowY.length - 1] }}>
            {sh.cols.map((c, ci) => <div key={`h${ci}`} className={`ghead${ci >= sel.c1 && ci <= sel.c2 ? " on" : ""}`} style={{ left: 46 + colX[ci], top: 0, width: c.w, height: 22 }}>{colLetter(ci)}</div>)}
            {sh.rows.map((r, ri) => <div key={`r${ri}`} className={`ghead${ri >= sel.r1 && ri <= sel.r2 ? " on" : ""}`} style={{ left: 0, top: 22 + rowY[ri], width: 46, height: r.h, lineHeight: `${r.h}px` }}>{formatNumber(ri + 1)}</div>)}
            {sh.rows.map((row, ri) => sh.cols.map((col, ci) => {
              const ref = cellRef(ri, ci);
              const m = covered.get(ref);
              let w = col.w, h = row.h;
              if (m) { const x = parseRef(m); if (x.r1 !== ri || x.c1 !== ci) return null; w = colX[x.c2 + 1] - colX[x.c1]; h = rowY[x.r2 + 1] - rowY[x.r1]; }
              const cell = sh.cells[ref] || {};
              const s = cell.s || {};
              const f = fieldAt(ri, ci);
              const fi = f ? sh.fields.indexOf(f) : -1;
              const recent = liveMode && liveSheet && activeSid ? sroom!.sync.recentAt(activeSid, liveSheet.rows[ri], liveSheet.cols[ci]) : null;
              return (
                <div key={ref} className={`gcell${s.wrap ? " wrap" : ""}${recent ? ` recent ${peerClass(recent.by.color)}` : ""}`} data-ref={ref} data-by={recent?.by.name || undefined}
                  style={{ left: 46 + colX[ci], top: 22 + rowY[ri], width: w, height: h, fontWeight: s.b ? 700 : undefined, fontStyle: s.i ? "italic" : undefined, textDecoration: s.u ? "underline" : undefined,
                    fontSize: s.sz ? s.sz * 1.1 : undefined, color: cssColor(s.color), background: f ? `${COLORS[fi % COLORS.length]}22` : cssColor(s.bg), textAlign: s.ha, border: s.bd ? "1px solid #333" : undefined,
                    boxShadow: f ? `inset 0 0 0 2px ${COLORS[fi % COLORS.length]}` : undefined }}
                  onMouseDown={(e) => { if (e.button !== 0) return; e.preventDefault(); if (editing) commit(); gridRef.current?.focus({ preventScroll: true }); const p = { r: ri, c: ci }; const anchor = e.shiftKey ? cursor.current.anchor : p; drag.current = anchor; cursor.current = { anchor, active: p }; setSel(rectOf(anchor, p)); }}
                  onMouseEnter={() => { if (drag.current) { const d = drag.current; cursor.current = { anchor: d, active: { r: ri, c: ci } }; setSel(rectOf(d, cursor.current.active)); } }}
                  onDoubleClick={() => { if (!ro) setEditing({ r: ri, c: ci, v: cell.v == null ? "" : String(cell.v), sid: activeSid }); }}
                  title={f ? tr("字段：{v1}", { v1: f.name }) : recent?.by.name ? tr("{v1} 刚修改", { v1: recent.by.name }) : undefined}>
                  {f && ((f.kind === "repeat" && ci === f.c1) || (f.kind !== "repeat" && ref === (f.ref || "").split(":")[0])) && <span className="fldchip" style={{ background: COLORS[fi % COLORS.length], position: "absolute", left: 1, top: 1 }}>{f.name}</span>}
                  <span style={{ paddingLeft: f ? 4 : 0 }}>{shown(cell.v)}</span>
                </div>
              );
            }))}
            {others.map((o) => {
              const r1 = rowIndex.get(o.sel.r1), c1 = colIndex.get(o.sel.c1), r2 = rowIndex.get(o.sel.r2), c2 = colIndex.get(o.sel.c2);
              if (r1 === undefined || c1 === undefined || r2 === undefined || c2 === undefined) return null;
              const [a, b, c, d] = [Math.min(r1, r2), Math.min(c1, c2), Math.max(r1, r2), Math.max(c1, c2)];
              if (c >= sh.rows.length || d >= sh.cols.length) return null;
              return (
                <div key={o.user.id} className={`gpeer ${peerClass(o.user.color)}${o.sel.typing ? " typing" : ""}`} data-testid="sheet-peer-selection" data-name={o.user.name} aria-hidden="true"
                  style={{ left: 46 + colX[b], top: 22 + rowY[a], width: colX[d + 1] - colX[b], height: rowY[c + 1] - rowY[a] }}>
                  <span className="gpeer-name"><span className="full">{o.user.name}</span><span className="initial">{initialOf(o.user.name)}</span></span>
                </div>
              );
            })}
            {sh.rows.length > 0 && sh.cols.length > 0 && <div className="gsel" style={{ left: 46 + colX[sel.c1], top: 22 + rowY[sel.r1], width: colX[sel.c2 + 1] - colX[sel.c1], height: rowY[sel.r2 + 1] - rowY[sel.r1] }} />}
            {editing && (
              <input ref={inputRef} className="input" value={editing.v} onChange={(e) => setEditing({ ...editing, v: e.target.value })}
                aria-label={tr("编辑单元格 {v1}", { v1: cellRef(editing.r, editing.c) })}
                onKeyDown={(e) => {
                  // Enter confirms a pinyin candidate and Escape cancels one: neither may commit
                  // or discard the cell while the input method is composing.
                  if (isImeKey(e)) return;
                  if (e.key === "Enter") { e.preventDefault(); commit(); select({ r1: editing.r + 1, c1: editing.c, r2: editing.r + 1, c2: editing.c }); gridRef.current?.focus(); }
                  if (e.key === "Escape") { e.preventDefault(); cancelEdit(); gridRef.current?.focus(); }
                  // Commit first, then let the browser continue Tab from the grid. Removing the
                  // focused input in onBlur alone made Chrome return to the grid on the first Tab.
                  if (e.key === "Tab") { commit(); gridRef.current?.focus(); }
                }}
                onBlur={commit}
                style={{ position: "absolute", left: 46 + colX[editing.c], top: 22 + rowY[editing.r], width: Math.max(120, sh.cols[editing.c]?.w || 100), height: (sh.rows[editing.r]?.h || 24) + 2, zIndex: 10 }} />
            )}
          </div>
        </div>
        <div className="side right" style={{ width: 260 }}>
          <div className="pane-h"><span className="title">{tr("字段 ")}{formatNumber(sh.fields.length)}</span></div>
          <div className="maplist">
            {sh.fields.map((f, i) => (
              <div key={f.id || i} className="slotitem" onClick={() => select(fieldRect(f))}>
                <span className="dot" style={{ background: COLORS[i % COLORS.length] }} />
                <span className="grow ellipsis">{f.name}{f.required && <span style={{ color: "var(--err)" }}> *</span>}</span>
                <span className="badge">{f.kind === "cell" ? f.ref : f.kind === "region" ? tr("区域 {v1}", { v1: f.ref }) : tr("明细行 {v1}", { v1: f.row! + 1 })}</span>
                <label className="check tiny" onClick={(e) => e.stopPropagation()} title={tr("必填")}><input type="checkbox" checked={!!f.required} disabled={ro} onChange={(e) => setRequired(i, e.target.checked)} />{tr("必填")}</label>
                <button type="button" className="btn xs ghost" disabled={ro} aria-label={tr("删除字段「{v1}」", { v1: f.name })} title={tr("删除字段")} onClick={(e) => { e.stopPropagation(); deleteField(i); }}>✕</button>
              </div>
            ))}
            {!sh.fields.length && <div className="empty small">{tr("还没有字段。选中单元格后用上方的「设为字段单元格 / 固定区域 / 重复明细行」。")}</div>}
          </div>
        </div>
      </div>
      <div className="sheettabs" aria-label={tr("工作表")}>
        {viewSheets.map((s, i) => {
          const changed = liveMode && dots.has(sids[i]);
          return (
            <button type="button" aria-current={i === viewSi ? "true" : undefined} key={liveMode ? sids[i] : i} className={`sheettab${i === viewSi ? " on" : ""}`} onClick={() => chooseSheet(i)} onDoubleClick={() => { if (!ro) void renameSheet(i); }}
              aria-label={changed ? tr("{v1}（有其他成员的新修改）", { v1: s.name }) : undefined} data-changed={changed || undefined}>
              {s.name}{changed && <span className="tabdot" aria-hidden="true" />}
            </button>
          );
        })}
        {!ro && <button type="button" className="sheettab" onClick={addSheet} aria-label={tr("添加工作表")}>＋</button>}
        {!ro && viewSheets.length > 1 && <button type="button" className="sheettab" onClick={deleteSheet}>{tr("删除当前表")}</button>}
        {!ro && gone.length > 0 && (
          <select className="input sm" value="" onChange={(e) => restoreSheet(e.target.value)} aria-label={tr("恢复已删除的工作表")} data-testid="sheet-restore" style={{ alignSelf: "center", width: "auto" }}>
            <option value="">{tr("已删除的工作表（{count}）", { count: gone.length })}</option>
            {gone.map((g) => <option key={g.sid} value={g.sid}>{tr("恢复「{v1}」", { v1: g.name })}</option>)}
          </select>
        )}
        <span className="tiny muted" style={{ alignSelf: "center", marginLeft: 8 }}>{tr("双击单元格或直接输入以编辑；双击工作表标签可重命名")}</span>
      </div>
    </div>
  );
}
