import { formatNumber } from "@synwevo/ui";
import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n, LanguageMenu, PageState } from "@synwevo/ui";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get, post, put, workspaceContext } from "@synwevo/auth-client";
import { cellRef, colLetter, isImeKey, rangeRef, useDialog, useToast } from "@synwevo/ui";
import { initialState, openRoom, type Focus, type Room, type RoomState } from "../lib/collab";
import { PresenceBar } from "../components/ProjectPresence";
import { moveCursor, rectOf } from "../lib/gridsel";
import { cssColor } from "../lib/css";
import { errorText } from "../lib/perm";
import { loadedVersion, saveOverStale, STALE_TITLE, staleText } from "../lib/templatesave";

interface Cell { v?: string; t?: string; s?: any }
interface Field { id: string; name: string; kind: "cell" | "region" | "repeat"; ref?: string; row?: number; c1?: number; c2?: number; required?: boolean; empty_policy?: string }
interface SheetData { name: string; cols: { w: number }[]; rows: { h: number }[]; cells: Record<string, Cell>; merges: string[]; fields: Field[] }

const rid = () => "f" + Math.random().toString(16).slice(2, 12);
const COLORS = ["#e11d48", "#2563eb", "#059669", "#d97706", "#7c3aed", "#0891b2"];
const newSheet = (name: string): SheetData => ({ name, cols: Array.from({ length: 10 }, () => ({ w: 100 })), rows: Array.from({ length: 30 }, () => ({ h: 24 })), cells: {}, merges: [], fields: [] });

function parseRef(ref: string) {
  const [a, b] = ref.split(":");
  const p = (x: string) => { const m = /([A-Z]+)(\d+)/.exec(x)!; let c = 0; for (const ch of m[1]) c = c * 26 + ch.charCodeAt(0) - 64; return [Number(m[2]) - 1, c - 1]; };
  const [r1, c1] = p(a), [r2, c2] = p(b || a);
  return { r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) };
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
  // Save stays disabled until the stored template has actually arrived. Before, a slow or failed
  // load left the blank starting sheet on screen and 保存 overwrote the template with it.
  const [loaded, setLoaded] = useState(!tid);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [sheets, setSheets] = useState<SheetData[]>([newSheet("Sheet1")]);
  const [si, setSi] = useState(0);
  const [sel, setSel] = useState({ r1: 0, c1: 0, r2: 0, c2: 0 });
  const [editing, setEditing] = useState<{ r: number; c: number; v: string } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");                     // names the version this save creates (a checkpoint in the history)
  const [hist, setHist] = useState<SheetData[][]>([]);
  // ---- the template's room: who else has it open and where they are; their saves as they happen
  const roomRef = useRef<Room | null>(null);
  if (tid && !roomRef.current) roomRef.current = openRoom(tid, { origin: window.location.origin, workspaceId: workspaceContext(), kind: "template" });
  const [room, setRoom] = useState<RoomState>(initialState());
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
  const sh = sheets[si];

  const loadTemplate = (quiet = false) => {
    if (!tid) return Promise.resolve();
    if (!quiet) { setLoaded(false); setLoadErr(null); }
    return get(`/api/templates/${tid}`).then((t) => {
      if (t.kind !== "builtin_sheet" || !t.version.editor?.sheets) {
        setLoadErr(tr("这个模板不是用表格编辑器创建的，不能在这里编辑。"));
        return;
      }
      setName(t.name);
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
    const r = roomRef.current;
    if (!r) return;
    const off = r.subscribe(setRoom);
    return () => { off(); r.destroy(); roomRef.current = null; };
  }, []);
  useEffect(() => {
    if (!roomRef.current || !room.me) return;
    roomRef.current.setUser({ ...room.me, slot: room.me.color, readonly: room.readonly } as any);
  }, [room.me, room.readonly]);
  // where we are: the active cell of the active sheet (a stable address, not a screen position)
  useEffect(() => {
    if (!roomRef.current || !loaded) return;
    roomRef.current.setFocus(sh ? { kind: "cell", id: `${sh.name}!${cellRef(sel.r1, sel.c1)}` } : null);
  }, [sel.r1, sel.c1, si, loaded, room.me]);   // eslint-disable-line react-hooks/exhaustive-deps
  // someone else saved (or restored) a version: take it when nothing is pending here, say so otherwise
  useEffect(() => {
    const h = room.lastHead;
    if (!h || !room.me || !loaded) return;
    if (h.byId && h.byId === room.me.id) return;             // our own save: the response already carried it
    if (h.head && h.head === baseVersion.current) return;
    const who = h.by || tr("成员");
    const what = h.reason === "save" ? tr("保存") : h.reason === "restore" ? tr("恢复") : h.reason === "undo" ? tr("撤销") : h.reason === "redo" ? tr("重做") : tr("更换");
    if (dirty) {
      toast(tr("{v1} 已{v2}第 {v3} 版；你的修改会在保存时按单元格与之合并", { v1: who, v2: what, v3: h.head }), "info");
    } else {
      void loadTemplate(true).then(() => toast(tr("{v1} 已{v2}第 {v3} 版，已载入", { v1: who, v2: what, v3: h.head }), "info"));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.lastHead]);
  const focusLabel = (f: Focus): string | null => {
    if (f.kind !== "cell" && f.kind !== "sheet") return null;
    const [sheetName, ref] = f.id.includes("!") ? [f.id.slice(0, f.id.lastIndexOf("!")), f.id.slice(f.id.lastIndexOf("!") + 1)] : [f.id, ""];
    if (!sheets.some((x) => x.name === sheetName)) return null;
    return ref ? (sheets.length > 1 ? tr("{v1} 的 {v2}", { v1: sheetName, v2: ref }) : ref) : sheetName;
  };
  const locateFocus = (f: Focus) => {
    const sheetName = f.id.includes("!") ? f.id.slice(0, f.id.lastIndexOf("!")) : f.id;
    const ref = f.id.includes("!") ? f.id.slice(f.id.lastIndexOf("!") + 1) : "";
    const i = sheets.findIndex((x) => x.name === sheetName);
    if (i < 0) return;
    if (i !== si) chooseSheet(i);
    if (ref) { const x = parseRef(ref); select({ r1: x.r1, c1: x.c1, r2: x.r1, c2: x.c1 }); }
  };

  // Closing the tab or reloading with unsaved edits asks first (this editor had no guard at all).
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // In-app navigation does not fire beforeunload, so 返回 asks itself.
  const back = async () => {
    if (dirty && !(await confirm(tr("放弃未保存的修改？"), tr("模板有尚未保存的修改，离开后这些修改会丢失。"), tr("放弃修改并离开"), true))) return;
    nav(projectId ? `/p/${projectId}` : "/");
  };

  const mutate = (fn: (s: SheetData) => SheetData) => {
    setHist((h) => [...h.slice(-50), sheets]);
    setSheets(sheets.map((s, i) => (i === si ? fn(structuredClone(s)) : s)));
    setDirty(true);
  };
  const undo = () => { const h = hist[hist.length - 1]; if (h) { setSheets(h); setHist(hist.slice(0, -1)); chooseSheet(Math.min(si, h.length - 1)); setDirty(true); } };

  const cellsInSel = () => { const out: string[] = []; for (let r = sel.r1; r <= sel.r2; r++) for (let c = sel.c1; c <= sel.c2; c++) out.push(cellRef(r, c)); return out; };
  const style = (patch: any) => mutate((s) => { for (const ref of cellsInSel()) { const c = s.cells[ref] || {}; c.s = { ...(c.s || {}), ...patch }; for (const k of Object.keys(c.s)) if (c.s[k] === null) delete c.s[k]; s.cells[ref] = c; } return s; });
  const cur = sh.cells[cellRef(sel.r1, sel.c1)] || {};

  // The edit being committed, read through a ref: Enter commits and then moves focus back to the
  // grid, which blurs the input and would otherwise commit the same edit a second time.
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const commit = () => {
    const cur0 = editingRef.current;
    if (!cur0) return;
    editingRef.current = null;
    const { r, c, v } = cur0;
    mutate((s) => {
      const ref = cellRef(r, c);
      const cell = s.cells[ref] || {};
      cell.v = v;
      cell.t = /^-?\d+(\.\d+)?$/.test(v) && !/^0\d/.test(v) ? "number" : undefined;
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
  const cancelEdit = () => {
    editingRef.current = null;
    setEditing(null);
  };

  const chooseSheet = (i: number) => {
    cancelEdit();
    drag.current = null;
    select({ r1: 0, c1: 0, r2: 0, c2: 0 });
    setSi(i);
  };

  const merge = () => {
    if (sel.r1 === sel.r2 && sel.c1 === sel.c2) return;
    const ref = rangeRef(sel.r1, sel.c1, sel.r2, sel.c2);
    mutate((s) => { s.merges = s.merges.filter((m) => { const x = parseRef(m); return x.r2 < sel.r1 || x.r1 > sel.r2 || x.c2 < sel.c1 || x.c1 > sel.c2; }); s.merges.push(ref); return s; });
  };
  const unmerge = () => mutate((s) => { s.merges = s.merges.filter((m) => { const x = parseRef(m); return x.r2 < sel.r1 || x.r1 > sel.r2 || x.c2 < sel.c1 || x.c1 > sel.c2; }); return s; });

  const overlaps = (f: Field) => {
    const x = f.kind === "repeat" ? { r1: f.row!, c1: f.c1!, r2: f.row!, c2: f.c2! } : parseRef(f.ref!);
    return !(x.r2 < sel.r1 || x.r1 > sel.r2 || x.c2 < sel.c1 || x.c1 > sel.c2);
  };
  const addField = async (kind: Field["kind"]) => {
    if (sh.fields.some(overlaps)) { toast(tr("选区与已有字段重叠"), "err"); return; }
    const single = sel.r1 === sel.r2 && sel.c1 === sel.c2;
    if (kind === "cell" && !single) { toast(tr("字段单元格只能是一个单元格（合并单元格请选左上角）"), "err"); return; }
    if (kind === "repeat" && sel.r1 !== sel.r2) { toast(tr("重复明细行只能选择一行中的连续单元格"), "err"); return; }
    const n = await prompt(kind === "cell" ? tr("字段单元格") : kind === "region" ? tr("固定区域") : tr("重复明细行"), tr("名称"), kind === "repeat" ? tr("明细") : "");
    if (!n) return;
    const f: Field = kind === "repeat" ? { id: rid(), name: n, kind, row: sel.r1, c1: sel.c1, c2: sel.c2, empty_policy: "remove_row" } : { id: rid(), name: n, kind, ref: rangeRef(sel.r1, sel.c1, sel.r2, sel.c2) };
    mutate((s) => { s.fields.push(f); return s; });
  };

  const saveTpl = async () => {
    if (!loaded || busy) return;
    setBusy(true);
    try {
      const editor = { sheets };
      // Someone else's save since this template was loaded is merged cell by cell on the server;
      // only a cell, a field or a sheet's layout both sides changed comes back as a conflict.
      const send = async (base: number | null) => {
        try {
          return await put(`/api/templates/${tid}/builtin`, { editor, name, ...(note.trim() ? { note: note.trim() } : {}), ...(base ? { base_version: base } : {}) });
        } catch (e: any) {
          const conflicts: any[] | undefined = e?.code === "stale_template" ? e.detail?.conflicts : undefined;
          if (!conflicts?.length) throw e;
          const reasons: Record<string, string> = { both_changed: tr("双方都改了它"), deleted_by_other: tr("对方已删除它"), deleted_by_you: tr("你删除了它，对方改了它"), both_added: tr("双方各自新增") };
          const list = conflicts.map((c) => (c.kind === "cell" ? tr("单元格 {v1}（{v2}）", { v1: c.ref, v2: c.sheet }) : c.kind === "field" ? tr("字段「{v1}」", { v1: c.name || c.id }) : c.kind === "layout" ? tr("工作表「{v1}」的行列与合并", { v1: c.sheet }) : tr("工作表「{v1}」", { v1: c.sheet })) + "：" + (reasons[c.reason] || c.reason)).join("；");
          const mine = await confirm(tr("表格有冲突"), tr("其他成员在你之后也保存了这个模板。没有冲突的单元格已可合并；下面这些双方都动过，请选择保留哪一方：{v1}", { v1: list }), tr("以我的为准"), true);
          if (!mine) return null;
          const force = conflicts.map((c) => (c.kind === "cell" ? `cell:${c.sheet}!${c.ref}` : c.kind === "field" ? `field:${c.id}` : c.kind === "layout" ? `layout:${c.sheet}` : `sheet:${c.sheet}`));
          return await put(`/api/templates/${tid}/builtin`, { editor, name, ...(note.trim() ? { note: note.trim() } : {}), ...(base ? { base_version: base } : {}), force_ids: force });
        }
      };
      const t = tid
        ? await saveOverStale(send, baseVersion.current, (theirs, mine) => confirm(tr(STALE_TITLE), staleText(theirs, mine), tr("仍然保存"), true))
        : await post("/api/templates/builtin", { kind: "builtin_sheet", name, editor, project_id: projectId });
      if (!t) { toast(tr("没有保存：已改为对方的版本，你的修改仍在编辑器中"), "info"); await loadTemplate(true); return; }
      baseVersion.current = loadedVersion(t);
      setDirty(false);
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
  const fieldAt = (r: number, c: number) => sh.fields.find((f) => { const x = f.kind === "repeat" ? { r1: f.row!, c1: f.c1!, r2: f.row!, c2: f.c2! } : parseRef(f.ref!); return r >= x.r1 && r <= x.r2 && c >= x.c1 && c <= x.c2; });

  const onKey = (e: React.KeyboardEvent) => {
    if (busy || !loaded || editing || e.target !== e.currentTarget) return;
    if (isImeKey(e)) {
      // An input method starting on the grid: open the cell editor and let the composition
      // continue there, rather than turning the keystroke into a character of its own.
      setEditing({ r: sel.r1, c: sel.c1, v: "" });
      return;
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "z") { e.preventDefault(); undo(); return; }
    // Tab is not captured: it leaves the grid like everywhere else (WCAG 2.1.2 No Keyboard Trap).
    // Enter moves down, arrows move; F2 or typing edits.
    const mv: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1], Enter: [1, 0] };
    if (mv[e.key]) {
      e.preventDefault();
      const next = moveCursor(cursor.current.anchor, cursor.current.active, ...mv[e.key], e.shiftKey, sh.rows.length, sh.cols.length);
      cursor.current = next;
      setSel(next.rect);
    } else if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      mutate((s) => { for (const ref of cellsInSel()) if (s.cells[ref]) delete s.cells[ref].v; return s; });
    } else if (e.key.length === 1 && !e.metaKey && !e.ctrlKey) {
      setEditing({ r: sel.r1, c: sel.c1, v: e.key });
      e.preventDefault();
    } else if (e.key === "F2") {
      setEditing({ r: sel.r1, c: sel.c1, v: cur.v || "" });
    }
  };

  if (loadErr) return (
    <PageState role="alert">
      <div className="big">{tr("无法打开模板")}</div>
      <div className="notice err" style={{ maxWidth: 480 }}>{serverText(loadErr)}</div>
      <button type="button" className="btn" onClick={() => nav(projectId ? `/p/${projectId}` : "/")}>{tr("返回")}</button>
    </PageState>
  );

  return (
    <div className="editor-shell" inert={busy || !loaded} onMouseUp={() => (drag.current = null)}>
      <div className="topbar">
        <LanguageMenu />
        <button type="button" className="btn ghost sm" onClick={back}>{tr("← 返回")}</button>
        <input className="input" value={name} onChange={(e) => { setName(e.target.value); setDirty(true); }} style={{ width: 240, fontWeight: 600 }} aria-label={tr("模板名称")} />
        <span className="badge">{tr("表格型模板 → XLSX")}</span>
        {!loaded ? <span className="badge info" role="status">{tr("加载中…")}</span> : dirty ? <span className="badge warn" role="status">{tr("未保存")}</span> : <span className="badge ok" role="status">{tr("已保存")}</span>}
        <span className="spacer" />
        {tid && <PresenceBar room={room} labelFor={focusLabel} onLocate={locateFocus} testId="sheet-presence" />}
        {tid && <input className="input sm version-note" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder={tr("版本说明（可选）")} aria-label={tr("这次保存的版本说明，可留空")} disabled={busy} data-testid="version-note" />}
        <button type="button" className="btn primary" disabled={busy || !loaded} onClick={saveTpl}>{busy ? tr("保存中…") : tr("保存模板")}</button>
      </div>
      <div className="toolbar">
        <span className="cellinfo">{rangeRef(sel.r1, sel.c1, sel.r2, sel.c2)}</span>
        <button type="button" className={`btn sm${cur.s?.b ? " active" : ""}`} aria-pressed={!!cur.s?.b} aria-label={tr("加粗")} title={tr("加粗")} onClick={() => style({ b: cur.s?.b ? null : 1 })}><b>B</b></button>
        <button type="button" className={`btn sm${cur.s?.i ? " active" : ""}`} aria-pressed={!!cur.s?.i} aria-label={tr("斜体")} title={tr("斜体")} onClick={() => style({ i: cur.s?.i ? null : 1 })}><i>I</i></button>
        <button type="button" className={`btn sm${cur.s?.u ? " active" : ""}`} aria-pressed={!!cur.s?.u} aria-label={tr("下划线")} title={tr("下划线")} onClick={() => style({ u: cur.s?.u ? null : 1 })}><u>U</u></button>
        <select className="input sm" aria-label={tr("字号")} value={cur.s?.sz || 11} onChange={(e) => style({ sz: Number(e.target.value) })}>{[9, 10, 11, 12, 14, 16, 18, 20, 24].map((x) => <option key={x}>{x}</option>)}</select>
        <label className="small">{tr("字")}<input type="color" value={cur.s?.color || "#000000"} onChange={(e) => style({ color: e.target.value })} style={{ width: 26, height: 22, border: 0 }} /></label>
        <label className="small">{tr("底")}<input type="color" value={cur.s?.bg || "#ffffff"} onChange={(e) => style({ bg: e.target.value })} style={{ width: 26, height: 22, border: 0 }} /></label>
        <button className="btn sm" onClick={() => style({ bg: null })}>{tr("清底色")}</button>
        {(["left", "center", "right"] as const).map((a) => <button key={a} className={`btn sm${cur.s?.ha === a ? " active" : ""}`} onClick={() => style({ ha: a })}>{({ left: tr("左"), center: tr("中"), right: tr("右") })[a]}</button>)}
        <button className={`btn sm${cur.s?.wrap ? " active" : ""}`} onClick={() => style({ wrap: cur.s?.wrap ? null : 1 })}>{tr("换行")}</button>
        <button className={`btn sm${cur.s?.bd ? " active" : ""}`} onClick={() => style({ bd: cur.s?.bd ? null : 1 })}>{tr("边框")}</button>
        <select className="input sm" aria-label={tr("数字格式")} value={cur.s?.nf || ""} onChange={(e) => style({ nf: e.target.value || null })}>
          <option value="">{tr("常规")}</option><option value="@">{tr("文本")}</option><option value="0">0</option><option value="0.00">0.00</option><option value="#,##0.00">#,##0.00</option><option value="¥#,##0.00">¥#,##0.00</option><option value="0%">0%</option><option value="yyyy-mm-dd">yyyy-mm-dd</option><option value='yyyy"年"m"月"d"日"'>yyyy年m月d日</option>
        </select>
        <button className="btn sm" onClick={merge}>{tr("合并")}</button>
        <button className="btn sm" onClick={unmerge}>{tr("取消合并")}</button>
        <label className="small">{tr("列宽")}<input className="input sm" type="number" style={{ width: 56 }} value={sh.cols[sel.c1]?.w || 100} onChange={(e) => mutate((s) => { for (let c = sel.c1; c <= sel.c2; c++) s.cols[c].w = Math.max(20, Number(e.target.value)); return s; })} /></label>
        <label className="small">{tr("行高")}<input className="input sm" type="number" style={{ width: 56 }} value={sh.rows[sel.r1]?.h || 24} onChange={(e) => mutate((s) => { for (let r = sel.r1; r <= sel.r2; r++) s.rows[r].h = Math.max(12, Number(e.target.value)); return s; })} /></label>
        <button className="btn sm" onClick={() => mutate((s) => { s.rows.push(...Array.from({ length: 10 }, () => ({ h: 24 }))); return s; })}>{tr("+10 行")}</button>
        <button className="btn sm" onClick={() => mutate((s) => { s.cols.push({ w: 100 }); return s; })}>{tr("+1 列")}</button>
        <button type="button" className="btn sm" onClick={undo} disabled={!hist.length} aria-label={tr("撤销")} title={tr("撤销（⌘Z）")}>↶</button>
      </div>
      <div className="toolbar">
        <b className="small">{tr("字段：")}</b>
        <button className="btn sm primary" onClick={() => addField("cell")}>{tr("设为字段单元格")}</button>
        <button className="btn sm" onClick={() => addField("region")}>{tr("设为固定区域")}</button>
        <button className="btn sm" onClick={() => addField("repeat")}>{tr("设为重复明细行")}</button>
        <span className="tiny muted">{tr("重复明细行：选中一行中的连续单元格；上一行作为表头名称。生成时有几条明细就插入几行，公式与合并随之平移。")}</span>
      </div>
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
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
              return (
                <div key={ref} className={`gcell${s.wrap ? " wrap" : ""}`} data-ref={ref}
                  style={{ left: 46 + colX[ci], top: 22 + rowY[ri], width: w, height: h, fontWeight: s.b ? 700 : undefined, fontStyle: s.i ? "italic" : undefined, textDecoration: s.u ? "underline" : undefined,
                    fontSize: s.sz ? s.sz * 1.1 : undefined, color: cssColor(s.color), background: f ? `${COLORS[fi % COLORS.length]}22` : cssColor(s.bg), textAlign: s.ha, border: s.bd ? "1px solid #333" : undefined,
                    boxShadow: f ? `inset 0 0 0 2px ${COLORS[fi % COLORS.length]}` : undefined }}
                  onMouseDown={(e) => { if (e.button !== 0) return; e.preventDefault(); if (editing) commit(); gridRef.current?.focus({ preventScroll: true }); const p = { r: ri, c: ci }; const anchor = e.shiftKey ? cursor.current.anchor : p; drag.current = anchor; cursor.current = { anchor, active: p }; setSel(rectOf(anchor, p)); }}
                  onMouseEnter={() => { if (drag.current) { const d = drag.current; cursor.current = { anchor: d, active: { r: ri, c: ci } }; setSel(rectOf(d, cursor.current.active)); } }}
                  onDoubleClick={() => { setEditing({ r: ri, c: ci, v: cell.v || "" }); }}
                  title={f ? tr("字段：{v1}", { v1: f.name }) : undefined}>
                  {f && ((f.kind === "repeat" && ci === f.c1) || (f.kind !== "repeat" && ref === (f.ref || "").split(":")[0])) && <span className="fldchip" style={{ background: COLORS[fi % COLORS.length], position: "absolute", left: 1, top: 1 }}>{f.name}</span>}
                  <span style={{ paddingLeft: f ? 4 : 0 }}>{cell.v}</span>
                </div>
              );
            }))}
            <div className="gsel" style={{ left: 46 + colX[sel.c1], top: 22 + rowY[sel.r1], width: colX[sel.c2 + 1] - colX[sel.c1], height: rowY[sel.r2 + 1] - rowY[sel.r1] }} />
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
                style={{ position: "absolute", left: 46 + colX[editing.c], top: 22 + rowY[editing.r], width: Math.max(120, sh.cols[editing.c].w), height: sh.rows[editing.r].h + 2, zIndex: 10 }} />
            )}
          </div>
        </div>
        <div className="side right" style={{ width: 260 }}>
          <div className="pane-h"><span className="title">{tr("字段 ")}{formatNumber(sh.fields.length)}</span></div>
          <div className="maplist">
            {sh.fields.map((f, i) => (
              <div key={f.id} className="slotitem" onClick={() => { if (f.kind === "repeat") select({ r1: f.row!, c1: f.c1!, r2: f.row!, c2: f.c2! }); else select(parseRef(f.ref!)); }}>
                <span className="dot" style={{ background: COLORS[i % COLORS.length] }} />
                <span className="grow ellipsis">{f.name}{f.required && <span style={{ color: "var(--err)" }}> *</span>}</span>
                <span className="badge">{f.kind === "cell" ? f.ref : f.kind === "region" ? tr("区域 {v1}", { v1: f.ref }) : tr("明细行 {v1}", { v1: f.row! + 1 })}</span>
                <label className="check tiny" onClick={(e) => e.stopPropagation()} title={tr("必填")}><input type="checkbox" checked={!!f.required} onChange={(e) => mutate((s) => { s.fields[i].required = e.target.checked; return s; })} />{tr("必填")}</label>
                <button type="button" className="btn xs ghost" aria-label={tr("删除字段「{v1}」", { v1: f.name })} title={tr("删除字段")} onClick={(e) => { e.stopPropagation(); mutate((s) => { s.fields.splice(i, 1); return s; }); }}>✕</button>
              </div>
            ))}
            {!sh.fields.length && <div className="empty small">{tr("选中单元格后点击上方「设为字段单元格 / 固定区域 / 重复明细行」")}</div>}
          </div>
        </div>
      </div>
      <div className="sheettabs">
        {sheets.map((s, i) => <button key={i} className={`sheettab${i === si ? " on" : ""}`} onClick={() => chooseSheet(i)} onDoubleClick={async () => { const n = await prompt(tr("重命名工作表"), tr("名称"), s.name); if (n) { setHist([...hist.slice(-50), sheets]); setSheets(sheets.map((x, k) => (k === i ? { ...x, name: n } : x))); setDirty(true); } }}>{s.name}</button>)}
        <button className="sheettab" onClick={() => { setHist([...hist.slice(-50), sheets]); setSheets([...sheets, newSheet(`Sheet${sheets.length + 1}`)]); chooseSheet(sheets.length); setDirty(true); }}>＋</button>
        {sheets.length > 1 && <button className="sheettab" onClick={() => { setHist([...hist.slice(-50), sheets]); setSheets(sheets.filter((_, k) => k !== si)); chooseSheet(0); setDirty(true); }}>{tr("删除当前表")}</button>}
        <span className="tiny muted" style={{ alignSelf: "center", marginLeft: 8 }}>{tr("双击单元格或直接输入以编辑；双击工作表标签可重命名")}</span>
      </div>
    </div>
  );
}
