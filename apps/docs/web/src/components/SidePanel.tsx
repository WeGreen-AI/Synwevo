import { formatNumber, STATUS_TEXT } from "@synwevo/ui";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { errorText } from "../lib/perm";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useId, useRef, useState } from "react";
import { get, qs } from "@synwevo/auth-client";
import { describeLocator, MAP_TYPE, mappingSlots, SLOT_KIND, variesText } from "../lib/describe";
import { ruleMode } from "../lib/status";
import { READ_ONLY_TEXT } from "../lib/perm";
import { markColor } from "../lib/css";
import type { Mapping, Project, Slot, SourceFile } from "../lib/types";
import { isImeKey, useDialog } from "@synwevo/ui";
import Transforms from "./Transforms";

export type SideTab = "mappings" | "fields" | "samples" | "check";

interface Props {
  tab: SideTab;
  onTab: (t: SideTab) => void;
  project: Project;
  sources: SourceFile[];
  mappings: Mapping[];
  slots: Slot[];
  activeMap: string | null;
  activeSlot: string | null;
  locate: Record<string, any[]>;
  activeFile: string | null;
  onPickMapping: (id: string) => void;
  onPickSlot: (id: string) => void;
  onEditMapping: (m: Mapping) => void;
  onSaveMappings: (ms: Mapping[], note: string) => Promise<void>;
  onUpdateSlot: (id: string, patch: any) => Promise<void>;
  onDeleteSlot: (s: Slot) => Promise<void>;
  /** the template is kept by a built-in editor: its fields are added and removed there (CI-112) */
  builtinTemplate?: boolean;
  onRelocate: (fileId: string, mappingId: string) => void;
  /** `key` is the override's own key: the file id, or `<file id>~<block>` in 「每块一份」. */
  onClearOverride: (key: string, mappingId: string) => void;
  onOpenRule: () => void;
  onCorrect: (fileId: string, mappingId: string, value: string | null) => Promise<void>;
  collapsed: boolean;
  onCollapse: (v: boolean) => void;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  width: number;
  onWidth: (w: number) => void;
  /** Viewer role: every edit is offered disabled, with the reason, instead of failing on click. */
  readOnly?: boolean;
}

const SIDE_MIN = 240;
const sideMax = () => Math.max(SIDE_MIN, window.innerWidth - 560);

export default function SidePanel(p: Props) {
  useI18n();
  if (p.collapsed) {
    return (
      <div className="side right collapsed" style={{ width: 34 }}>
        <button type="button" className="btn ghost icon" title={tr("展开映射列表")} aria-label={tr("展开映射列表")} onClick={() => p.onCollapse(false)}>«</button>
        <div className="tiny muted" style={{ writingMode: "vertical-rl", margin: "8px auto" }}>{tr("映射 ")}{formatNumber(p.mappings.length)}{tr(" · 字段 ")}{formatNumber(p.slots.length)}</div>
      </div>
    );
  }
  // drag the panel's left edge; the document panes keep the rest of the window.
  // Pointer events with capture work for mouse, pen and touch alike.
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const el = e.currentTarget;
    el.setPointerCapture?.(e.pointerId);
    const x0 = e.clientX;
    const w0 = (el.parentElement as HTMLElement).getBoundingClientRect().width;
    document.body.classList.add("resizing");
    const move = (ev: PointerEvent) => p.onWidth(Math.max(SIDE_MIN, Math.min(sideMax(), w0 + x0 - ev.clientX)));
    const up = () => {
      document.body.classList.remove("resizing");
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };
  // The keyboard alternative to dragging (WCAG 2.1.1 / 2.5.7, APG window splitter):
  // ← widens the panel, → narrows it, Shift for bigger steps, Home/End for the limits, Enter collapses.
  const cur = () => p.width || (document.querySelector(".side.right") as HTMLElement | null)?.getBoundingClientRect().width || 330;
  const onSepKey = (e: React.KeyboardEvent) => {
    if (isImeKey(e)) return;
    const step = e.shiftKey ? 64 : 16;
    const set = (w: number) => { e.preventDefault(); p.onWidth(Math.round(Math.max(SIDE_MIN, Math.min(sideMax(), w)))); };
    if (e.key === "ArrowLeft") set(cur() + step);
    else if (e.key === "ArrowRight") set(cur() - step);
    else if (e.key === "Home") set(SIDE_MIN);
    else if (e.key === "End") set(sideMax());
    else if (e.key === "Enter") { e.preventDefault(); p.onCollapse(true); }
  };
  const w = Math.round(p.width || 330);
  return (
    <div className="side right" id="side-panel" style={p.width ? { width: p.width } : undefined}>
      <div className="side-resizer" onPointerDown={drag} onKeyDown={onSepKey} tabIndex={0} role="separator" aria-orientation="vertical"
        aria-label={tr("调整右侧面板宽度")} aria-controls="side-panel" aria-valuemin={SIDE_MIN} aria-valuemax={Math.round(sideMax())} aria-valuenow={w}
        aria-valuetext={tr("{v1} 像素", { v1: w })} title={tr("拖动或用 ← / → 调整面板宽度，Enter 收起")} />
      <div className="tabs">
        <button type="button" aria-pressed={p.tab === "mappings"} className={p.tab === "mappings" ? "on" : ""} onClick={() => p.onTab("mappings")}>{tr("映射 ")}{formatNumber(p.mappings.length)}</button>
        <button type="button" aria-pressed={p.tab === "fields"} className={p.tab === "fields" ? "on" : ""} onClick={() => p.onTab("fields")}>{tr("字段 ")}{formatNumber(p.slots.length)}</button>
        <button type="button" aria-pressed={p.tab === "samples"} className={p.tab === "samples" ? "on" : ""} onClick={() => p.onTab("samples")}>{tr("样本")}</button>
        <button type="button" aria-pressed={p.tab === "check"} className={p.tab === "check" ? "on" : ""} onClick={() => p.onTab("check")}>{tr("校验")}</button>
        <button type="button" className="btn ghost sm icon" style={{ flex: "none", width: 30 }} title={tr("收起右侧面板")} aria-label={tr("收起右侧面板")} onClick={() => p.onCollapse(true)}>»</button>
      </div>
      {p.tab === "mappings" && <MappingList {...p} />}
      {p.tab === "fields" && <FieldList {...p} />}
      {p.tab === "samples" && <Samples {...p} />}
      {p.tab === "check" && <Check {...p} />}
    </div>
  );
}

function MappingList(p: Props) {
  useI18n();
  const { confirm } = useDialog();
  const selSlot = p.slots.find((s) => s.id === p.activeSlot) || null;
  const kindOf = (s: Slot) => (s.kind === "repeat" ? "repeat" : s.kind === "region" ? "region" : "single");
  /* ---- keyboard navigation and multi-select (↑↓ / J K, X, Shift range, ⌘A, D, Delete) ---- */
  const ids = p.mappings.map((m) => m.id);
  const idKey = ids.join(",");
  const [cursor, setCursor] = useState(0);
  const [picked, setPicked] = useState<string[]>([]);
  const [anchor, setAnchor] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const ro = !!p.readOnly;
  useEffect(() => { setPicked((xs) => xs.filter((x) => ids.includes(x))); }, [idKey]);
  // keep the cursor on a real row after mappings are deleted
  useEffect(() => { if (cursor > 0 && cursor >= ids.length) setCursor(Math.max(0, ids.length - 1)); }, [idKey]);
  useEffect(() => {
    const i = ids.indexOf(p.activeMap || "");
    if (i >= 0 && i !== cursor) setCursor(i);
  }, [p.activeMap, idKey]);
  useEffect(() => {
    listRef.current?.querySelectorAll<HTMLElement>("[data-map]")[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);
  const clamp = (i: number) => Math.max(0, Math.min(p.mappings.length - 1, i));
  const range = (a: number, b: number) => ids.slice(Math.min(a, b), Math.max(a, b) + 1);
  const move = (d: number, extend: boolean) => {
    const i = clamp(cursor + d);
    setCursor(i);
    if (extend) setPicked(range(anchor, i));
    else setAnchor(i);
    p.onPickMapping(ids[i]);
  };
  const togglePick = (i: number, additive: boolean) => {
    const id = ids[i];
    setAnchor(i);
    setPicked((xs) => (additive || xs.includes(id) ? (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]) : [id]));
  };
  const selection = picked.length ? p.mappings.filter((m) => picked.includes(m.id)) : [];
  // The workspace's save already reports a refusal (toast, save badge); here a refusal only
  // means "nothing changed", so the list keeps its selection instead of pretending it worked.
  const save = (ms: Mapping[], note: string) => p.onSaveMappings(ms, note).then(() => true, () => false);
  const setEnabled = async (list: Mapping[], on: boolean) => {
    const set = new Set(list.map((m) => m.id));
    await save(p.mappings.map((x) => (set.has(x.id) ? { ...x, enabled: on } : x)),
      `${on ? "启用" : "禁用"} ${list.length} 个映射（#${list.map((m) => m.no).join("、#")}）`);
  };
  const removeMany = async (list: Mapping[]) => {
    const nos = list.map((m) => m.no);
    if (!(await confirm(tr("删除映射"), tr("删除 {v1} 个映射（#{v2}）？可以用「撤销」恢复。", { v1: list.length, v2: nos.join("、#") }), tr("删除"), true))) return;
    const set = new Set(list.map((m) => m.id));
    if (await save(p.mappings.filter((x) => !set.has(x.id)), `删除 ${list.length} 个映射（#${nos.join("、#")}）`)) setPicked([]);
  };
  const onListKey = (e: React.KeyboardEvent) => {
    if (isImeKey(e)) return;
    // Keys pressed on a button inside a row (编辑 / 禁用 / ✕) belong to that button. Enter used to
    // be caught here, cancelled, and turned into "edit the cursor mapping" instead.
    if (e.target !== e.currentTarget) return;
    const n = p.mappings.length;
    if (!n) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key;
    const target = selection.length ? selection : [p.mappings[clamp(cursor)]];
    if (k === "ArrowDown" || (!mod && (k === "j" || k === "J"))) { e.preventDefault(); move(1, e.shiftKey); }
    else if (k === "ArrowUp" || (!mod && (k === "k" || k === "K"))) { e.preventDefault(); move(-1, e.shiftKey); }
    else if (mod && (k === "a" || k === "A")) { e.preventDefault(); setPicked(ids); }
    else if (!mod && (k === "x" || k === "X" || (k === " " && !e.shiftKey))) { e.preventDefault(); togglePick(clamp(cursor), true); }
    else if (k === " " && e.shiftKey) { e.preventDefault(); setPicked(range(anchor, clamp(cursor))); }
    else if (k === "Enter") { e.preventDefault(); if (!ro) p.onEditMapping(p.mappings[clamp(cursor)]); }
    else if (!mod && (k === "d" || k === "D")) { e.preventDefault(); if (!ro) setEnabled(target, !target.every((m) => m.enabled)); }
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); if (!ro) removeMany(target); }
    else if (k === "Escape" && picked.length) { e.preventDefault(); e.stopPropagation(); setPicked([]); }
  };
  const rebind = async (m: Mapping) => {
    if (!selSlot) return;
    const b = selSlot.binding || selSlot.id;
    const taken = p.mappings.find((x) => x.id !== m.id && (x.target.binding || x.target.slot_id) === b);
    if (taken) {
      await confirm(tr("无法改绑"), tr("字段「{v1}」已被映射 #{v2} 使用。请先删除或合并该映射。", { v1: selSlot.name, v2: taken.no }), tr("知道了"));
      return;
    }
    await save(p.mappings.map((x) => (x.id === m.id ? { ...x, target: { binding: b } } : x)), `映射 #${m.no} 改绑到「${selSlot.name}」`);
  };
  const mode = p.project.record_rule?.mode;
  const toggle = (m: Mapping) => save(p.mappings.map((x) => (x.id === m.id ? { ...x, enabled: !x.enabled } : x)), `${m.enabled ? "禁用" : "启用"}映射 #${m.no}`);
  const remove = async (m: Mapping) => {
    if (await confirm(tr("删除映射"), tr("删除映射 #{v1}？可以用「撤销」恢复。", { v1: m.no }), tr("删除"), true)) await save(p.mappings.filter((x) => x.id !== m.id), `删除映射 #${m.no}`);
  };
  return (
    <>
      <div className="toolbar">
        <button type="button" className="btn sm" disabled={ro || !p.canUndo} onClick={p.onUndo} title={ro ? tr(READ_ONLY_TEXT) : tr("撤销（Ctrl/⌘+Z）")}>{tr("↶ 撤销")}</button>
        <button type="button" className="btn sm" disabled={ro || !p.canRedo} onClick={p.onRedo} title={ro ? tr(READ_ONLY_TEXT) : tr("重做（Ctrl/⌘+Shift+Z）")}>{tr("↷ 重做")}</button>
        <span className="spacer" />
        <span className="tiny muted" title={tr("列表可用键盘操作：↑↓ 移动，X 或空格多选，D 启用/禁用，Delete 删除")}>{tr("当前文件的实际取值")}</span>
      </div>
      {ro && <div className="notice small" style={{ borderRadius: 0 }}>{tr(READ_ONLY_TEXT)}</div>}
      <div className={`maplist${picked.length ? " picking" : ""}`} ref={listRef} tabIndex={0} role="listbox" aria-multiselectable aria-label={tr("映射列表")}
        aria-activedescendant={p.mappings.length ? `${uid}-m-${p.mappings[clamp(cursor)]?.id}` : undefined} onKeyDown={onListKey}>
        {!p.mappings.length && (
          <div className="empty small">
            <div className="big">{tr("还没有映射")}</div>
            <div>{tr("选中左侧的数据和右侧的字段后，点「建立映射」。")}</div>
          </div>
        )}
        {p.mappings.map((m, i) => {
          const tslots = mappingSlots(m, p.slots);
          const loc = p.locate[m.id] || [];
          const bad = loc.find((x) => !x.ok);
          const val = loc.map((x) => x.value).filter((x) => x !== undefined && x !== null).join(" + ");
          const on = picked.includes(m.id);
          return (
            <div key={m.id} id={`${uid}-m-${m.id}`} data-map role="option" aria-selected={on}
              aria-label={tr("映射 #{v1}：{v2}{v3}", { v1: m.no, v2: tslots.map((s) => s.name).join("、") || tr("目标已失效"), v3: m.enabled ? "" : tr("（已禁用）") })}
              className={`mapitem${m.id === p.activeMap ? " on" : ""}${m.enabled ? "" : " off"}${i === cursor ? " cursor" : ""}${on ? " picked" : ""}`}
              onClick={(e) => {
                listRef.current?.focus({ preventScroll: true });   // keyboard works straight after a click
                if (e.shiftKey) { setCursor(i); setPicked(range(anchor, i)); return; }
                if (e.metaKey || e.ctrlKey) { setCursor(i); togglePick(i, true); return; }
                setCursor(i);
                setAnchor(i);
                if (picked.length) setPicked([]);
                p.onPickMapping(m.id);
              }}>
              <div className="line1">
                <span className="pick" role="checkbox" aria-checked={on} tabIndex={-1} aria-label={tr("选择映射 #{v1}", { v1: m.no })}
                  onClick={(e) => { e.stopPropagation(); setCursor(i); togglePick(i, true); }}>{on ? "✓" : ""}</span>
                <span className="num" style={{ background: markColor(m.color) }}>{formatNumber(m.no)}</span>
                <span className="badge">{tr(MAP_TYPE[m.type])}</span>
                {!m.enabled && <span className="badge warn">{tr("已禁用")}</span>}
                {bad && <span className="badge err" title={diagnosticText(bad.error)}>{tr("当前文件无法定位")}</span>}
                {!tslots.length && <span className="badge err">{tr("目标已失效")}</span>}
                <span className="spacer" />
                <span className="acts" onClick={(e) => e.stopPropagation()}>
                  {!ro && selSlot && kindOf(selSlot) === m.type && (selSlot.binding || selSlot.id) !== (m.target.binding || m.target.slot_id) && (
                    <button type="button" className="btn xs ghost" title={tr("把此映射的目标改为当前选中的字段「{v1}」", { v1: selSlot.name })} onClick={() => rebind(m)}>{tr("改绑")}</button>
                  )}
                  {!ro && <>
                    <button type="button" className="btn xs ghost" title={tr("编辑映射 #{v1}", { v1: m.no })} onClick={() => p.onEditMapping(m)}>{tr("编辑")}</button>
                    <button type="button" className="btn xs ghost" title={tr("{v1}映射 #{v2}", { v1: m.enabled ? tr("禁用") : tr("启用"), v2: m.no })} onClick={() => toggle(m)}>{m.enabled ? tr("禁用") : tr("启用")}</button>
                    <button type="button" className="btn xs ghost" title={tr("删除映射 #{v1}", { v1: m.no })} aria-label={tr("删除映射 #{v1}", { v1: m.no })} onClick={() => remove(m)}>✕</button>
                  </>}
                </span>
              </div>
              <div className="srcdesc" title={m.sources.map(describeLocator).join(" + ")}>{tr("来源：")}{m.sources.map(describeLocator).join(m.combine ? ` ${m.combine.op === "sum" ? "＋" : m.combine.op === "newline" ? "⏎" : "&"} ` : " + ")}</div>
              <div className="tgtdesc">{tr("目标：")}{tslots.map((s) => s.name).join("、") || tr("（已删除）")} <span className="muted">· {variesText(m.sources[0], mode)}</span></div>
              {m.type === "repeat" && m.columns && <div className="tiny muted ellipsis">{tr("列：")}{m.columns.filter((c) => c.source?.header).map((c) => `${c.source.header}→${tslots[0]?.columns?.find((x) => x.key === c.slot_col)?.name || c.slot_col}`).join("，")}</div>}
              {bad ? <div className="tiny" style={{ color: "var(--err)" }}>{diagnosticText(bad.error)}</div> : val ? <div className="sample mt8" title={val}>{val}</div> : null}
              {m.agg && m.agg !== "unique" && <div className="tiny muted">{tr("组内：")}{({ first: tr("取第一条"), last: tr("取最后一条"), join: tr("拼接"), join_distinct: tr("去重拼接"), sum: tr("求和"), count: tr("计数") } as any)[m.agg]}</div>}
              {m.transforms && m.transforms.length > 0 && <div className="tiny muted">{tr("转换：")}{m.transforms.map((t) => t.op).join(" → ")}</div>}
            </div>
          );
        })}
      </div>
      {picked.length > 0 && (
        <div className="bulkbar" role="toolbar" aria-label={tr("批量操作")}>
          <span className="small"><b>{tr("已选 ")}{formatNumber(picked.length)}</b> / {formatNumber(p.mappings.length)}</span>
          <span className="spacer" />
          <button type="button" className="btn sm" disabled={ro} title={ro ? tr(READ_ONLY_TEXT) : undefined} onClick={() => setEnabled(selection, !selection.every((m) => m.enabled))}>
            {selection.every((m) => m.enabled) ? tr("禁用") : tr("启用")}
          </button>
          <button type="button" className="btn sm danger" disabled={ro} title={ro ? tr(READ_ONLY_TEXT) : undefined} onClick={() => removeMany(selection)}>{tr("删除")}</button>
          <button type="button" className="btn ghost sm" onClick={() => setPicked([])} title="Esc">{tr("取消选择")}</button>
        </div>
      )}
    </>
  );
}

function FieldList(p: Props) {
  useI18n();
  const sel = p.slots.find((s) => s.id === p.activeSlot) || null;
  const mappedBy = (s: Slot) => p.mappings.find((m) => (m.target.binding || m.target.slot_id) === (s.binding || s.id));
  return (
    <div className="maplist">
      {!p.slots.length && <div className="empty small"><div className="big">{tr("模板还没有字段")}</div><div>{tr("在右侧模板中选中内容，点击「设为待填区域」")}</div></div>}
      {p.slots.map((s) => {
        const m = mappedBy(s);
        const shared = p.slots.filter((x) => (x.binding || x.id) === (s.binding || s.id)).length > 1;
        return (
          <div key={s.id}>
            <div className={`slotitem${s.id === p.activeSlot ? " on" : ""}`} onClick={() => p.onPickSlot(s.id)} role="button" tabIndex={0}
              aria-pressed={s.id === p.activeSlot} aria-label={tr("字段「{v1}」{v2}", { v1: s.name, v2: m ? tr("（映射 #{v1}）", { v1: m.no }) : tr("（未映射）") })}
              onKeyDown={(e) => { if (isImeKey(e) || e.target !== e.currentTarget) return; if (e.key === "Enter" || e.key === " ") { e.preventDefault(); p.onPickSlot(s.id); } }}>
              <span className="num" style={{ background: markColor(m?.color) }}>{m ? m.no : "–"}</span>
              <span className="grow ellipsis">{s.name}{s.required && <span style={{ color: "var(--err)" }}> *</span>}</span>
              {shared && <span className="badge info" title={tr("与同名字段共享数据绑定")}>{tr("共享")}</span>}
              <span className="badge">{s.layout ? tr("重复明细区域") : s.block ? tr("重复内容块") : SLOT_KIND[s.kind] || s.kind}</span>
            </div>
            {sel?.id === s.id && <SlotEditor slot={s} {...p} />}
          </div>
        );
      })}
    </div>
  );
}

function SlotEditor(p: Props & { slot: Slot }) {
  useI18n();
  const s = p.slot;
  const imageTpl = p.project.template?.format === "png" || p.project.template?.format === "jpeg";
  const [name, setName] = useState(s.name);
  const [def, setDef] = useState(s.default || "");
  useEffect(() => { setName(s.name); setDef(s.default || ""); }, [s.id, s.name, s.default]);
  // The field shows what the server holds: a refused change (a viewer, a conflict) puts the
  // stored value back instead of leaving the typed one on screen looking saved.
  const up = (patch: any) => p.onUpdateSlot(s.id, patch).catch(() => { setName(s.name); setDef(s.default || ""); });
  return (
    <fieldset disabled={!!p.readOnly} className="col gap4" style={{ padding: "8px 10px 12px 34px", margin: 0, border: 0, borderBottom: "1px solid var(--line)", background: "var(--panel-2)", minWidth: 0 }} onClick={(e) => e.stopPropagation()}>
      {p.readOnly && <div className="tiny muted">{tr(READ_ONLY_TEXT)}</div>}
      <label className="field">{tr("名称")}<input className="input sm" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== s.name && up({ name: name.trim() })} /></label>
      {s.original_text !== undefined && s.original_text !== "" && <div className="tiny muted">{tr("原内容：「")}{s.original_text.slice(0, 60)}{tr("」（生成时替换；删除字段可恢复）")}</div>}
      {s.warning && <div className="notice warn tiny">{diagnosticText(s.warning)}</div>}
      {s.kind !== "repeat" && s.kind !== "region" && (
        <>
          <label className="check small"><input type="checkbox" checked={s.required} onChange={(e) => up({ required: e.target.checked })} />{tr(" 必填（为空时阻止该样本）")}</label>
          <label className="field">{tr("数据类型")}<select className="input sm" value={s.data_type} onChange={(e) => up({ data_type: e.target.value })}>
              <option value="auto">{tr("自动（保持来源类型）")}</option><option value="text">{tr("文本")}</option><option value="number">{tr("数字")}</option><option value="date">{tr("日期")}</option>
            </select>
          </label>
          <label className="field">{tr("值为空时的默认值（可选）")}<input className="input sm" value={def} onChange={(e) => setDef(e.target.value)} onBlur={() => def !== (s.default || "") && up({ default: def || null })} /></label>
          {p.project.template?.format === "docx" && s.kind === "text" && (
            <div className="row gap8 small wrap">
              <label className="check"><input type="checkbox" checked={!!(s as any).shrink} onChange={(e) => up({ shrink: e.target.checked })} />{tr(" 超长导致分页增加时允许缩小字号")}</label>
              {(s as any).shrink && <label className="row gap4">{tr("最小")}<input className="input sm w64" type="number" min={4} max={72} defaultValue={(s as any).shrink_min || 7} onBlur={(e) => up({ shrink_min: Number(e.target.value) })} />{tr("号")}</label>}
            </div>
          )}
          <div className="small">{tr("字段格式（对所有映射生效）")}</div>
          <Transforms value={s.format || []} onChange={(v) => up({ format: v })} />
        </>
      )}
      {s.kind === "repeat" && s.layout && (
        <>
          <div className="small muted">{tr("PDF 明细区域：")}{formatNumber(s.layout.cols.length)}{tr(" 列，行高 ")}{formatNumber(s.layout.row_height)}{tr(" 点，可容纳 ")}{s.capacity ?? "?"}{tr(" 行")}</div>
          <label className="field">{tr("超过可容纳行数时")}<select className="input sm" value={s.overflow || "error"} onChange={(e) => up({ overflow: e.target.value })}>
              <option value="error">{tr("视为错误（该样本不生成）")}</option>{s.locator?.page !== undefined && !imageTpl && <option value="continue">{tr("复制本页续写")}</option>}<option value="shrink">{tr("压缩行高放入（最多 50%）")}</option>
            </select>
          </label>
          <label className="field">{tr("零条明细时")}<select className="input sm" value={s.empty_policy || "blank"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="blank">{tr("区域留空")}</option><option value="placeholder">{tr("第一行显示占位文字")}</option><option value="error">{tr("视为错误")}</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">{tr("占位文字")}<input className="input sm" defaultValue={s.placeholder || tr("（无）")} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          <div className="small">{tr("明细列：")}</div>
          {s.layout.cols.map((c) => {
            const col = (s.columns || []).find((x) => x.key === c.key);
            return (
              <div key={c.key} className="row small gap4">
                <span className="badge">{c.key}</span>
                <input className="input sm grow" defaultValue={col?.name || c.name} onBlur={(e) => e.target.value && e.target.value !== col?.name && up({ columns: [{ key: c.key, name: e.target.value }] })} />
                <select className="input sm" style={{ width: 56 }} value={c.align} onChange={(e) => up({ columns: [{ key: c.key, align: e.target.value }] })}>
                  <option value="left">{tr("左")}</option><option value="center">{tr("中")}</option><option value="right">{tr("右")}</option>
                </select>
              </div>
            );
          })}
        </>
      )}
      {s.kind === "repeat" && s.block && (
        <>
          {p.project.template?.format === "xlsx" ? (
            <>
              <div className="small muted">{tr("重复内容块：第 {v1}–{v2} 行，每条明细（或每段）复制一份", { v1: (s.locator?.first ?? 0) + 1, v2: (s.locator?.last ?? 0) + 1 })}</div>
              <div className="tiny muted">{tr("块内公式引用块外的单元格时，每一份都指向同一个单元格（不论有没有 $）；单独的重复明细行则像 Excel 向下填充一样移动相对引用。")}</div>
            </>
          ) : <div className="small muted">{tr("重复内容块：")}{s.paragraphs ?? "?"}{tr(" 个段落，每条明细复制一份")}</div>}
          <label className="field">{tr("相邻两份之间")}<select className="input sm" value={s.separator || "none"} onChange={(e) => up({ separator: e.target.value })}>
              <option value="none">{tr("直接相连")}</option><option value="blank">{tr("空一行")}</option><option value="page_break">{tr("分页（每条明细一页）")}</option>
            </select>
          </label>
          <label className="field">{tr("零条明细时")}<select className="input sm" value={s.empty_policy || "remove"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="remove">{tr("删除整个内容块")}</option><option value="placeholder">{tr("保留一份，第一列显示占位文字")}</option><option value="error">{tr("视为错误")}</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">{tr("占位文字")}<input className="input sm" defaultValue={s.placeholder || tr("（无）")} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          <div className="small">{p.project.template?.format === "xlsx" ? tr("块的列（在块内选中单元格可添加列）：") : tr("明细列（在块内划选文字可添加列）：")}</div>
          {(s.columns || []).length === 0 && <div className="tiny muted">{p.project.template?.format === "xlsx"
            ? tr("还没有列。请在右侧内容块中选中要随明细变化的单元格，选择「作为内容块的一列」。")
            : tr("还没有列。请在右侧内容块中划选要随明细变化的文字，选择「作为内容块的一列」。")}</div>}
          {(s.columns || []).map((c) => (
            <div key={c.key} className="row small gap4">
              <span className="badge">{c.cell || c.key}</span>
              <input className="input sm grow" defaultValue={c.name} onBlur={(e) => e.target.value && e.target.value !== c.name && up({ columns: [{ key: c.key, name: e.target.value }] })} />
              <button className="btn xs" title={tr("取消该列（恢复为原文字，不随明细变化）")} onClick={() => up({ remove_column: c.key })}>{tr("移除")}</button>
            </div>
          ))}
        </>
      )}
      {s.kind === "repeat" && !s.layout && !s.block && (
        <>
          <label className="field">{tr("零条明细时")}<select className="input sm" value={s.empty_policy || "remove_row"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="remove_row">{tr("删除明细行，保留表头")}</option><option value="blank_row">{tr("保留一行空白")}</option><option value="placeholder">{tr("保留一行并显示占位文字")}</option>
              <option value="remove_table">{tr("删除整个表格（仅文档）")}</option><option value="error">{tr("视为错误")}</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">{tr("占位文字")}<input className="input sm" defaultValue={s.placeholder || tr("（无）")} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          {p.project.template?.format === "pptx" && (
            <>
              <label className="field">{tr("表格超出幻灯片时")}<select className="input sm" value={s.overflow || "warn"} onChange={(e) => up({ overflow: e.target.value })}>
                  <option value="warn">{tr("照常生成并提示")}</option><option value="error">{tr("视为错误（该样本不生成）")}</option>
                  <option value="continue">{tr("复制本张幻灯片续写")}</option>
                </select>
              </label>
              {s.overflow === "continue" && <div className="tiny muted">{tr("表头行在每张幻灯片上重复，表格下方的行只放在最后一张，幻灯片上的其他字段同样填写。按模板中的行高计算：文字换行使行变高时仍可能超出。")}</div>}
            </>
          )}
          {s.header_rows !== undefined && <label className="check small"><input type="checkbox" checked={s.repeat_header !== false} onChange={(e) => up({ repeat_header: e.target.checked })} />{tr(" 跨页时重复表头（")}{formatNumber(s.header_rows)}{tr(" 行）")}</label>}
          <div className="small">{tr("明细列：")}</div>
          {(s.columns || []).map((c) => (
            <div key={c.key} className="row small"><span className="badge">{c.key}</span><input className="input sm grow" defaultValue={c.name} onBlur={(e) => e.target.value !== c.name && up({ columns: [{ key: c.key, name: e.target.value }] })} /></div>
          ))}
        </>
      )}
      {s.kind === "region" && <div className="small muted">{tr("固定区域：")}{formatNumber(s.rows)}{tr(" 行 × ")}{formatNumber(s.cols)}{tr(" 列。来源区域必须同样大小。")}</div>}
      {(s.kind === "pdf_box" || s.layout) && (
        <label className="field">{tr("文字颜色")}<span className="row gap4">
            <input type="color" className="swatch" value={s.color || "#000000"} onChange={(e) => up({ color: e.target.value })} aria-label={tr("文字颜色")} />
            <span className="tiny muted">{s.color || "#000000"}{tr("（创建时取自原内容）")}</span>
          </span>
        </label>
      )}
      {s.kind === "pdf_box" && (
        <div className="grid3">
          <label className="field">{tr("字号")}<input className="input sm" type="number" defaultValue={s.font_size} onBlur={(e) => up({ font_size: Number(e.target.value) })} /></label>
          <label className="field">{tr("最小")}<input className="input sm" type="number" defaultValue={s.min_font_size} onBlur={(e) => up({ min_font_size: Number(e.target.value) })} /></label>
          <label className="field">{tr("对齐")}<select className="input sm" value={s.align || "left"} onChange={(e) => up({ align: e.target.value })}><option value="left">{tr("左")}</option><option value="center">{tr("中")}</option><option value="right">{tr("右")}</option></select></label>
        </div>
      )}
      <div className="row mt8">
        <span className="spacer" />
        {p.builtinTemplate
          ? <span className="tiny muted">{tr("这个模板的字段在「编辑模板」里添加或删除")}</span>
          : <button type="button" className="btn sm danger" onClick={() => p.onDeleteSlot(s)}>{tr("删除字段")}</button>}
      </div>
    </fieldset>
  );
}

const SAMPLE_PAGE = 300;

function Samples(p: Props) {
  useI18n();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  // The first page is reloaded whenever the project changes; 显示更多 appends the next page by
  // offset. It used to re-request everything from offset 0 with a growing limit, which the
  // server caps at 5000 — the fifth click answered 422 and the list stopped at ~4300.
  useEffect(() => {
    if (!p.project.record_rule?.mode) return;
    let alive = true;
    get(`/api/projects/${p.project.id}/records${qs({ offset: 0, limit: SAMPLE_PAGE })}`)
      .then((d) => { if (alive) { setData(d); setErr(null); } })
      .catch((e) => alive && setErr(errorText(e)));
    return () => { alive = false; };
  }, [p.project.rev]);
  const loadMore = async () => {
    if (!data) return;
    setMore(true);
    try {
      const d = await get(`/api/projects/${p.project.id}/records${qs({ offset: data.records.length, limit: SAMPLE_PAGE })}`);
      setData((cur: any) => (cur ? { ...cur, total: d.total, records: [...cur.records, ...d.records] } : d));
    } catch (e: any) {
      setErr(errorText(e));
    } finally {
      setMore(false);
    }
  };
  if (!p.project.record_rule?.mode) return <div className="empty small"><div className="big">{tr("尚未定义样本")}</div><button type="button" className="btn primary" disabled={p.readOnly} title={p.readOnly ? tr(READ_ONLY_TEXT) : undefined} onClick={p.onOpenRule}>{tr("定义样本")}</button></div>;
  return (
    <>
      <div className="toolbar">
        <span className="badge info">{ruleMode(p.project.record_rule.mode)}</span>
        {data && <span className="small"><b>{formatNumber(data.total)}</b>{tr(" 条样本")}</span>}
        <span className="spacer" />
        <button type="button" className="btn sm" disabled={p.readOnly} title={p.readOnly ? tr(READ_ONLY_TEXT) : undefined} onClick={p.onOpenRule}>{tr("修改规则")}</button>
      </div>
      <div className="maplist">
        {err && <div className="notice err">{serverText(err)}</div>}
        {data && (
          <div className="col gap4" style={{ padding: 8 }}>
            <div className="tiny muted">{tr("排除空白行 ")}{formatNumber(data.excluded.blank)}{tr(" · 过滤 ")}{formatNumber(data.excluded.filtered)}{tr(" · 分组值为空 ")}{formatNumber(data.excluded.empty_key || 0)}{tr(" · 使用文件 ")}{formatNumber(data.files_used)}</div>
            {data.file_errors.map((e: any, i: number) => <div key={i} className="notice err tiny">{issueText(e)}</div>)}
            {data.warnings.map((w: string, i: number) => <div key={i} className="notice warn tiny">{diagnosticText(w)}</div>)}
          </div>
        )}
        {data?.records.map((r: any) => (
          <div key={r.key} className="slotitem" style={{ cursor: "default" }}>
            <span className="tiny muted" style={{ width: 34 }}>{formatNumber(r.index + 1)}</span>
            <span className="grow small ellipsis" title={r.label}>{r.label}</span>
            <span className="tiny muted">{serverText(r.where)}</span>
          </div>
        ))}
        {data && data.total > data.records.length && (
          <button type="button" className="btn sm" style={{ margin: 8 }} disabled={more} onClick={loadMore}>
            {more ? tr("加载中…") : tr("显示更多（已显示 {v1} / {v2} 条）", { v1: data.records.length, v2: data.total })}
          </button>
        )}
      </div>
    </>
  );
}

function Check(p: Props) {
  useI18n();
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get(`/api/projects/${p.project.id}/validate`).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(errorText(e)));
  }, [p.project.rev]);
  const byId: Record<string, Mapping> = Object.fromEntries(p.mappings.map((m) => [m.id, m]));
  return (
    <div className="maplist" style={{ padding: 8 }}>
      <div className="small muted mb8">{tr("每个来源文件都按代表文件上建立的映射逐一核对；无法定位的映射会标出。")}</div>
      {err && <div className="notice err">{serverText(err)}</div>}
      {data?.rule_error && <div className="notice err small">{diagnosticText(data.rule_error)}</div>}
      {data?.static?.map((e: any, i: number) => <div key={i} className="notice err small">{issueText(e)}</div>)}
      {data?.files.map((f: any) => {
        const bad = Object.entries(f.results || {}).filter(([, r]: any) => r.ok === false);
        return (
          <div key={f.id} className="card" style={{ marginBottom: 8, padding: 8 }}>
            <div className="row">
              <b className="grow small ellipsis" title={f.filename}>{f.filename}</b>
              {f.representative && <span className="badge info">{tr("代表")}</span>}
              {f.status !== "ready" && f.status !== "needs_ocr" ? <span className="badge err">{tr(STATUS_TEXT[f.status] || f.status)}</span> : bad.length ? <span className="badge err">{formatNumber(bad.length)}{tr(" 个问题")}</span> : f.record_error ? <span className="badge err">{tr("样本错误")}</span> : <span className="badge ok">{tr("通过")}</span>}
            </div>
            {f.error && <div className="tiny" style={{ color: "var(--err)" }}>{diagnosticText(f.error)}</div>}
            {f.record_error && <div className="tiny" style={{ color: "var(--err)" }}>{diagnosticText(f.record_error)}</div>}
            {!f.same_structure && f.status === "ready" && !f.representative && <div className="tiny muted">{tr("结构特征与代表文件不同（仅提示；以下逐项核对为准）")}</div>}
            {f.records !== undefined && <div className="tiny muted">{tr("样本 ")}{formatNumber(f.records)}{tr(" 条（按首条样本核对）")}</div>}
            {bad.map(([mid, r]: any) => {
              const m = byId[mid];
              const ov = m?.overrides?.[f.id] ? f.id : null;
              return (
                <div key={mid} className="col gap4" style={{ borderTop: "1px dashed var(--line)", paddingTop: 4, marginTop: 4 }}>
                  <div className="row small"><span className="num" style={{ background: markColor(m?.color) }}>{m?.no == null ? null : formatNumber(m.no)}</span><span className="grow" style={{ color: "var(--err)" }}>{issueText(r)}</span></div>
                  <div className="row gap4">
                    <button type="button" className="btn xs" disabled={p.readOnly} onClick={() => p.onRelocate(f.id, mid)}>{tr("在此文件中重新定位")}</button>
                    {r.code === "ocr_low_confidence" && !p.readOnly && <OcrFix fileId={f.id} mid={mid} initial={r.value || ""} onCorrect={p.onCorrect} />}
                    {ov && !p.readOnly && <button type="button" className="btn xs" onClick={() => p.onClearOverride(ov, mid)}>{tr("清除该文件的单独定位")}</button>}
                  </div>
                </div>
              );
            })}
            {/* Overrides saved for the whole file (`<file>`) and, in 「每块一份」, for one block of
                it (`<file>~<block>`). Block overrides used to be invisible here and could only be
                removed by undo. */}
            {p.mappings.flatMap((m) => overrideKeys(m, f.id).map((key) => ({ m, key }))).map(({ m, key }) => (
              <div key={`${m.id}/${key}`} className="tiny muted">{tr("映射 #")}{formatNumber(m.no)}{tr(" 在此文件")}{key === f.id ? "" : tr("第 {v1} 块", { v1: Number(key.slice(f.id.length + 1)) + 1 })}{tr("使用单独定位")}{" "}
                {!p.readOnly && <button type="button" className="btn xs ghost" onClick={() => p.onClearOverride(key, m.id)}>{tr("清除")}</button>}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

/** Override keys of mapping `m` that belong to file `fid`: the file itself and its blocks. */
function overrideKeys(m: Mapping, fid: string): string[] {
  return Object.keys(m.overrides || {}).filter((k) => k === fid || (k.startsWith(`${fid}~`) && /^\d+$/.test(k.slice(fid.length + 1))));
}

function OcrFix({ fileId, mid, initial, onCorrect }: { fileId: string; mid: string; initial: string; onCorrect: Props["onCorrect"] }) {
  useI18n();
  const { prompt } = useDialog();
  return <button type="button" className="btn xs" onClick={async () => { const v = await prompt(tr("确认 / 修正识别值"), tr("识别结果为「{v1}」。请对照左侧原图核对，确认无误直接保存，或改为正确的值：", { v1: initial }), initial); if (v !== null) await onCorrect(fileId, mid, v); }}>{tr("确认/修正识别值")}</button>;
}
