import { useEffect, useRef, useState } from "react";
import { get, qs } from "@synwevo/auth-client";
import { describeLocator, MAP_TYPE, mappingSlots, MODE_NAME, SLOT_KIND, variesText } from "../lib/describe";
import type { Mapping, Project, Slot, SourceFile } from "../lib/types";
import { useDialog } from "@synwevo/ui";
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
  onRelocate: (fileId: string, mappingId: string) => void;
  onClearOverride: (fileId: string, mappingId: string) => void;
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
}

export default function SidePanel(p: Props) {
  if (p.collapsed) {
    return (
      <div className="side right collapsed" style={{ width: 34 }}>
        <button className="btn ghost icon" title="展开映射列表" onClick={() => p.onCollapse(false)}>«</button>
        <div className="tiny muted" style={{ writingMode: "vertical-rl", margin: "8px auto" }}>映射 {p.mappings.length} · 字段 {p.slots.length}</div>
      </div>
    );
  }
  // drag the panel's left edge; the document panes keep the rest of the window
  const drag = (e: React.MouseEvent) => {
    e.preventDefault();
    const x0 = e.clientX;
    const w0 = (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect().width;
    document.body.classList.add("resizing");
    const move = (ev: MouseEvent) => p.onWidth(Math.max(240, Math.min(window.innerWidth - 560, w0 + x0 - ev.clientX)));
    const up = () => {
      document.body.classList.remove("resizing");
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  };
  return (
    <div className="side right" style={p.width ? { width: p.width } : undefined}>
      <div className="side-resizer" onMouseDown={drag} role="separator" aria-orientation="vertical" title="拖动调整面板宽度" />
      <div className="tabs">
        <button className={p.tab === "mappings" ? "on" : ""} onClick={() => p.onTab("mappings")}>映射 {p.mappings.length}</button>
        <button className={p.tab === "fields" ? "on" : ""} onClick={() => p.onTab("fields")}>字段 {p.slots.length}</button>
        <button className={p.tab === "samples" ? "on" : ""} onClick={() => p.onTab("samples")}>样本</button>
        <button className={p.tab === "check" ? "on" : ""} onClick={() => p.onTab("check")}>校验</button>
        <button className="btn ghost sm icon" style={{ flex: "none", width: 30 }} title="收起" onClick={() => p.onCollapse(true)}>»</button>
      </div>
      {p.tab === "mappings" && <MappingList {...p} />}
      {p.tab === "fields" && <FieldList {...p} />}
      {p.tab === "samples" && <Samples {...p} />}
      {p.tab === "check" && <Check {...p} />}
    </div>
  );
}

function MappingList(p: Props) {
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
  useEffect(() => { setPicked((xs) => xs.filter((x) => ids.includes(x))); }, [idKey]);
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
  const setEnabled = async (list: Mapping[], on: boolean) => {
    const set = new Set(list.map((m) => m.id));
    await p.onSaveMappings(p.mappings.map((x) => (set.has(x.id) ? { ...x, enabled: on } : x)),
      `${on ? "启用" : "禁用"} ${list.length} 个映射（#${list.map((m) => m.no).join("、#")}）`);
  };
  const removeMany = async (list: Mapping[]) => {
    const nos = list.map((m) => m.no);
    if (!(await confirm("删除映射", `删除 ${list.length} 个映射（#${nos.join("、#")}）？可以用「撤销」恢复。`, "删除", true))) return;
    const set = new Set(list.map((m) => m.id));
    await p.onSaveMappings(p.mappings.filter((x) => !set.has(x.id)), `删除 ${list.length} 个映射（#${nos.join("、#")}）`);
    setPicked([]);
  };
  const onListKey = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;
    const n = p.mappings.length;
    if (!n) return;
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key;
    const target = selection.length ? selection : [p.mappings[clamp(cursor)]];
    if (k === "ArrowDown" || (!mod && (k === "j" || k === "J"))) { e.preventDefault(); move(1, e.shiftKey); }
    else if (k === "ArrowUp" || (!mod && (k === "k" || k === "K"))) { e.preventDefault(); move(-1, e.shiftKey); }
    else if (mod && (k === "a" || k === "A")) { e.preventDefault(); setPicked(ids); }
    else if (!mod && (k === "x" || k === "X")) { e.preventDefault(); togglePick(clamp(cursor), true); }
    else if (k === "Enter") { e.preventDefault(); p.onEditMapping(p.mappings[clamp(cursor)]); }
    else if (!mod && (k === "d" || k === "D")) { e.preventDefault(); setEnabled(target, !target.every((m) => m.enabled)); }
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); removeMany(target); }
    else if (k === "Escape" && picked.length) { e.preventDefault(); e.stopPropagation(); setPicked([]); }
  };
  const rebind = async (m: Mapping) => {
    if (!selSlot) return;
    const b = selSlot.binding || selSlot.id;
    const taken = p.mappings.find((x) => x.id !== m.id && (x.target.binding || x.target.slot_id) === b);
    if (taken) {
      await confirm("无法改绑", `字段「${selSlot.name}」已被映射 #${taken.no} 使用。请先删除或合并该映射。`, "知道了");
      return;
    }
    await p.onSaveMappings(p.mappings.map((x) => (x.id === m.id ? { ...x, target: { binding: b } } : x)), `映射 #${m.no} 改绑到「${selSlot.name}」`);
  };
  const mode = p.project.record_rule?.mode;
  const toggle = (m: Mapping) => p.onSaveMappings(p.mappings.map((x) => (x.id === m.id ? { ...x, enabled: !x.enabled } : x)), `${m.enabled ? "禁用" : "启用"}映射 #${m.no}`);
  const remove = async (m: Mapping) => {
    if (await confirm("删除映射", `删除映射 #${m.no}？可以用「撤销」恢复。`, "删除", true)) await p.onSaveMappings(p.mappings.filter((x) => x.id !== m.id), `删除映射 #${m.no}`);
  };
  return (
    <>
      <div className="toolbar">
        <button className="btn sm" disabled={!p.canUndo} onClick={p.onUndo} title="撤销（Ctrl/⌘+Z）">↶ 撤销</button>
        <button className="btn sm" disabled={!p.canRedo} onClick={p.onRedo} title="重做（Ctrl/⌘+Shift+Z）">↷ 重做</button>
        <span className="spacer" />
        <span className="tiny muted" title="列表可用键盘操作：↑↓ 移动，X 多选，D 启用/禁用，Delete 删除">当前文件的实际取值</span>
      </div>
      <div className={`maplist${picked.length ? " picking" : ""}`} ref={listRef} tabIndex={0} role="listbox" aria-multiselectable aria-label="映射列表" onKeyDown={onListKey}>
        {!p.mappings.length && (
          <div className="empty small">
            <div className="big">还没有映射</div>
            <div>1. 在左侧选中来源数据（单元格、文字、区域）</div>
            <div>2. 在右侧点击字段（或选中内容后「设为待填区域」）</div>
            <div>3. 点击底部「建立映射」</div>
          </div>
        )}
        {p.mappings.map((m, i) => {
          const tslots = mappingSlots(m, p.slots);
          const loc = p.locate[m.id] || [];
          const bad = loc.find((x) => !x.ok);
          const val = loc.map((x) => x.value).filter((x) => x !== undefined && x !== null).join(" + ");
          const on = picked.includes(m.id);
          return (
            <div key={m.id} data-map role="option" aria-selected={on}
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
                <span className="pick" role="checkbox" aria-checked={on} tabIndex={-1} aria-label={`选择映射 #${m.no}`}
                  onClick={(e) => { e.stopPropagation(); setCursor(i); togglePick(i, true); }}>{on ? "✓" : ""}</span>
                <span className="num" style={{ background: m.color }}>{m.no}</span>
                <span className="badge">{MAP_TYPE[m.type]}</span>
                {!m.enabled && <span className="badge warn">已禁用</span>}
                {bad && <span className="badge err" title={bad.error}>当前文件无法定位</span>}
                {!tslots.length && <span className="badge err">目标已失效</span>}
                <span className="spacer" />
                <span className="acts" onClick={(e) => e.stopPropagation()}>
                  {selSlot && kindOf(selSlot) === m.type && (selSlot.binding || selSlot.id) !== (m.target.binding || m.target.slot_id) && (
                    <button className="btn xs ghost" title={`把此映射的目标改为当前选中的字段「${selSlot.name}」`} onClick={() => rebind(m)}>改绑</button>
                  )}
                  <button className="btn xs ghost" title="编辑" onClick={() => p.onEditMapping(m)}>编辑</button>
                  <button className="btn xs ghost" title={m.enabled ? "禁用" : "启用"} onClick={() => toggle(m)}>{m.enabled ? "禁用" : "启用"}</button>
                  <button className="btn xs ghost" title="删除" onClick={() => remove(m)}>✕</button>
                </span>
              </div>
              <div className="srcdesc" title={m.sources.map(describeLocator).join(" + ")}>来源：{m.sources.map(describeLocator).join(m.combine ? ` ${m.combine.op === "sum" ? "＋" : m.combine.op === "newline" ? "⏎" : "&"} ` : " + ")}</div>
              <div className="tgtdesc">目标：{tslots.map((s) => s.name).join("、") || "（已删除）"} <span className="muted">· {variesText(m.sources[0], mode)}</span></div>
              {m.type === "repeat" && m.columns && <div className="tiny muted ellipsis">列：{m.columns.filter((c) => c.source?.header).map((c) => `${c.source.header}→${tslots[0]?.columns?.find((x) => x.key === c.slot_col)?.name || c.slot_col}`).join("，")}</div>}
              {bad ? <div className="tiny" style={{ color: "var(--err)" }}>{bad.error}</div> : val ? <div className="sample mt8" title={val}>{val}</div> : null}
              {m.agg && m.agg !== "unique" && <div className="tiny muted">组内：{({ first: "取第一条", last: "取最后一条", join: "拼接", join_distinct: "去重拼接", sum: "求和", count: "计数" } as any)[m.agg]}</div>}
              {m.transforms && m.transforms.length > 0 && <div className="tiny muted">转换：{m.transforms.map((t) => t.op).join(" → ")}</div>}
            </div>
          );
        })}
      </div>
      {picked.length > 0 && (
        <div className="bulkbar" role="toolbar" aria-label="批量操作">
          <span className="small"><b>已选 {picked.length}</b> / {p.mappings.length}</span>
          <span className="spacer" />
          <button className="btn sm" onClick={() => setEnabled(selection, !selection.every((m) => m.enabled))}>
            {selection.every((m) => m.enabled) ? "禁用" : "启用"}
          </button>
          <button className="btn sm danger" onClick={() => removeMany(selection)}>删除</button>
          <button className="btn ghost sm" onClick={() => setPicked([])} title="Esc">取消选择</button>
        </div>
      )}
    </>
  );
}

function FieldList(p: Props) {
  const sel = p.slots.find((s) => s.id === p.activeSlot) || null;
  const mappedBy = (s: Slot) => p.mappings.find((m) => (m.target.binding || m.target.slot_id) === (s.binding || s.id));
  return (
    <div className="maplist">
      {!p.slots.length && <div className="empty small"><div className="big">模板还没有字段</div><div>在右侧模板中选中内容，点击「设为待填区域」</div></div>}
      {p.slots.map((s) => {
        const m = mappedBy(s);
        const shared = p.slots.filter((x) => (x.binding || x.id) === (s.binding || s.id)).length > 1;
        return (
          <div key={s.id}>
            <div className={`slotitem${s.id === p.activeSlot ? " on" : ""}`} onClick={() => p.onPickSlot(s.id)}>
              <span className="num" style={{ background: m ? m.color : "#94a3b8" }}>{m ? m.no : "–"}</span>
              <span className="grow ellipsis">{s.name}{s.required && <span style={{ color: "var(--err)" }}> *</span>}</span>
              {shared && <span className="badge info" title="与同名字段共享数据绑定">共享</span>}
              <span className="badge">{s.layout ? "重复明细区域" : s.block ? "重复内容块" : SLOT_KIND[s.kind] || s.kind}</span>
            </div>
            {sel?.id === s.id && <SlotEditor slot={s} {...p} />}
          </div>
        );
      })}
    </div>
  );
}

function SlotEditor(p: Props & { slot: Slot }) {
  const s = p.slot;
  const imageTpl = p.project.template?.format === "png" || p.project.template?.format === "jpeg";
  const [name, setName] = useState(s.name);
  const [def, setDef] = useState(s.default || "");
  useEffect(() => { setName(s.name); setDef(s.default || ""); }, [s.id, s.name, s.default]);
  const up = (patch: any) => p.onUpdateSlot(s.id, patch);
  return (
    <div className="col gap4" style={{ padding: "8px 10px 12px 34px", background: "#fafbfd", borderBottom: "1px solid var(--line)" }} onClick={(e) => e.stopPropagation()}>
      <label className="field">名称<input className="input sm" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== s.name && up({ name: name.trim() })} /></label>
      {s.original_text !== undefined && s.original_text !== "" && <div className="tiny muted">原内容：「{s.original_text.slice(0, 60)}」（生成时替换；删除字段可恢复）</div>}
      {s.warning && <div className="notice warn tiny">{s.warning}</div>}
      {s.kind !== "repeat" && s.kind !== "region" && (
        <>
          <label className="check small"><input type="checkbox" checked={s.required} onChange={(e) => up({ required: e.target.checked })} /> 必填（为空时阻止该样本）</label>
          <label className="field">数据类型
            <select className="input sm" value={s.data_type} onChange={(e) => up({ data_type: e.target.value })}>
              <option value="auto">自动（保持来源类型）</option><option value="text">文本</option><option value="number">数字</option><option value="date">日期</option>
            </select>
          </label>
          <label className="field">值为空时的默认值（可选，不会臆造内容）<input className="input sm" value={def} onChange={(e) => setDef(e.target.value)} onBlur={() => def !== (s.default || "") && up({ default: def || null })} /></label>
          {p.project.template?.format === "docx" && s.kind === "text" && (
            <div className="row gap8 small wrap">
              <label className="check"><input type="checkbox" checked={!!(s as any).shrink} onChange={(e) => up({ shrink: e.target.checked })} /> 超长导致分页增加时允许缩小字号</label>
              {(s as any).shrink && <label className="row gap4">最小<input className="input sm w64" type="number" min={4} max={72} defaultValue={(s as any).shrink_min || 7} onBlur={(e) => up({ shrink_min: Number(e.target.value) })} />号</label>}
            </div>
          )}
          <div className="small">字段格式（对所有映射生效）</div>
          <Transforms value={s.format || []} onChange={(v) => up({ format: v })} />
        </>
      )}
      {s.kind === "repeat" && s.layout && (
        <>
          <div className="small muted">PDF 明细区域：{s.layout.cols.length} 列，行高 {s.layout.row_height} 点，可容纳 {s.capacity ?? "?"} 行</div>
          <label className="field">超过可容纳行数时
            <select className="input sm" value={s.overflow || "error"} onChange={(e) => up({ overflow: e.target.value })}>
              <option value="error">视为错误（该样本不生成）</option>{s.locator?.page !== undefined && !imageTpl && <option value="continue">复制本页续写</option>}<option value="shrink">压缩行高放入（最多 50%）</option>
            </select>
          </label>
          <label className="field">零条明细时
            <select className="input sm" value={s.empty_policy || "blank"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="blank">区域留空</option><option value="placeholder">第一行显示占位文字</option><option value="error">视为错误</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">占位文字<input className="input sm" defaultValue={s.placeholder || "（无）"} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          <div className="small">明细列：</div>
          {s.layout.cols.map((c) => {
            const col = (s.columns || []).find((x) => x.key === c.key);
            return (
              <div key={c.key} className="row small gap4">
                <span className="badge">{c.key}</span>
                <input className="input sm grow" defaultValue={col?.name || c.name} onBlur={(e) => e.target.value && e.target.value !== col?.name && up({ columns: [{ key: c.key, name: e.target.value }] })} />
                <select className="input sm" style={{ width: 56 }} value={c.align} onChange={(e) => up({ columns: [{ key: c.key, align: e.target.value }] })}>
                  <option value="left">左</option><option value="center">中</option><option value="right">右</option>
                </select>
              </div>
            );
          })}
        </>
      )}
      {s.kind === "repeat" && s.block && (
        <>
          <div className="small muted">重复内容块：{s.paragraphs ?? "?"} 个段落，每条明细复制一份</div>
          <label className="field">相邻两份之间
            <select className="input sm" value={s.separator || "none"} onChange={(e) => up({ separator: e.target.value })}>
              <option value="none">直接相连</option><option value="blank">空一行</option><option value="page_break">分页（每条明细一页）</option>
            </select>
          </label>
          <label className="field">零条明细时
            <select className="input sm" value={s.empty_policy || "remove"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="remove">删除整个内容块</option><option value="placeholder">保留一份，第一列显示占位文字</option><option value="error">视为错误</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">占位文字<input className="input sm" defaultValue={s.placeholder || "（无）"} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          <div className="small">明细列（在块内划选文字可添加列）：</div>
          {(s.columns || []).length === 0 && <div className="tiny muted">还没有列。请在右侧内容块中划选要随明细变化的文字，选择「作为内容块的一列」。</div>}
          {(s.columns || []).map((c) => (
            <div key={c.key} className="row small gap4">
              <span className="badge">{c.key}</span>
              <input className="input sm grow" defaultValue={c.name} onBlur={(e) => e.target.value && e.target.value !== c.name && up({ columns: [{ key: c.key, name: e.target.value }] })} />
              <button className="btn xs" title="取消该列（恢复为原文字，不随明细变化）" onClick={() => up({ remove_column: c.key })}>移除</button>
            </div>
          ))}
        </>
      )}
      {s.kind === "repeat" && !s.layout && !s.block && (
        <>
          <label className="field">零条明细时
            <select className="input sm" value={s.empty_policy || "remove_row"} onChange={(e) => up({ empty_policy: e.target.value })}>
              <option value="remove_row">删除明细行，保留表头</option><option value="blank_row">保留一行空白</option><option value="placeholder">保留一行并显示占位文字</option>
              <option value="remove_table">删除整个表格（仅文档）</option><option value="error">视为错误</option>
            </select>
          </label>
          {s.empty_policy === "placeholder" && <label className="field">占位文字<input className="input sm" defaultValue={s.placeholder || "（无）"} onBlur={(e) => up({ placeholder: e.target.value })} /></label>}
          {s.header_rows !== undefined && <label className="check small"><input type="checkbox" checked={s.repeat_header !== false} onChange={(e) => up({ repeat_header: e.target.checked })} /> 跨页时重复表头（{s.header_rows} 行）</label>}
          <div className="small">明细列：</div>
          {(s.columns || []).map((c) => (
            <div key={c.key} className="row small"><span className="badge">{c.key}</span><input className="input sm grow" defaultValue={c.name} onBlur={(e) => e.target.value !== c.name && up({ columns: [{ key: c.key, name: e.target.value }] })} /></div>
          ))}
        </>
      )}
      {s.kind === "region" && <div className="small muted">固定区域：{s.rows} 行 × {s.cols} 列。来源区域必须同样大小。</div>}
      {(s.kind === "pdf_box" || s.layout) && (
        <label className="field">文字颜色
          <span className="row gap4">
            <input type="color" className="swatch" value={s.color || "#000000"} onChange={(e) => up({ color: e.target.value })} aria-label="文字颜色" />
            <span className="tiny muted">{s.color || "#000000"}（创建时取自原内容）</span>
          </span>
        </label>
      )}
      {s.kind === "pdf_box" && (
        <div className="grid3">
          <label className="field">字号<input className="input sm" type="number" defaultValue={s.font_size} onBlur={(e) => up({ font_size: Number(e.target.value) })} /></label>
          <label className="field">最小<input className="input sm" type="number" defaultValue={s.min_font_size} onBlur={(e) => up({ min_font_size: Number(e.target.value) })} /></label>
          <label className="field">对齐<select className="input sm" value={s.align || "left"} onChange={(e) => up({ align: e.target.value })}><option value="left">左</option><option value="center">中</option><option value="right">右</option></select></label>
        </div>
      )}
      <div className="row mt8">
        <span className="spacer" />
        <button className="btn sm danger" onClick={() => p.onDeleteSlot(s)}>删除字段</button>
      </div>
    </div>
  );
}

function Samples(p: Props) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [limit, setLimit] = useState(300);
  useEffect(() => {
    if (!p.project.record_rule?.mode) return;
    get(`/api/projects/${p.project.id}/records${qs({ limit })}`).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message));
  }, [p.project.rev, limit]);
  if (!p.project.record_rule?.mode) return <div className="empty small"><div className="big">尚未定义样本</div><button className="btn primary" onClick={p.onOpenRule}>定义样本</button></div>;
  return (
    <>
      <div className="toolbar">
        <span className="badge info">{MODE_NAME[p.project.record_rule.mode!]}</span>
        {data && <span className="small"><b>{data.total}</b> 条样本</span>}
        <span className="spacer" />
        <button className="btn sm" onClick={p.onOpenRule}>修改规则</button>
      </div>
      <div className="maplist">
        {err && <div className="notice err">{err}</div>}
        {data && (
          <div className="col gap4" style={{ padding: 8 }}>
            <div className="tiny muted">排除空白行 {data.excluded.blank} · 过滤 {data.excluded.filtered} · 分组值为空 {data.excluded.empty_key || 0} · 使用文件 {data.files_used}</div>
            {data.file_errors.map((e: any, i: number) => <div key={i} className="notice err tiny">{e.message}</div>)}
            {data.warnings.map((w: string, i: number) => <div key={i} className="notice warn tiny">{w}</div>)}
          </div>
        )}
        {data?.records.map((r: any) => (
          <div key={r.key} className="slotitem" style={{ cursor: "default" }}>
            <span className="tiny muted" style={{ width: 34 }}>{r.index + 1}</span>
            <span className="grow small ellipsis" title={r.label}>{r.label}</span>
            <span className="tiny muted">{r.where}</span>
          </div>
        ))}
        {data && data.total > data.records.length && <button className="btn sm" style={{ margin: 8 }} onClick={() => setLimit(limit + 1000)}>显示更多（共 {data.total} 条）</button>}
      </div>
    </>
  );
}

function Check(p: Props) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get(`/api/projects/${p.project.id}/validate`).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message));
  }, [p.project.rev]);
  const byId: Record<string, Mapping> = Object.fromEntries(p.mappings.map((m) => [m.id, m]));
  return (
    <div className="maplist" style={{ padding: 8 }}>
      <div className="small muted mb8">在代表文件上建立的规则会逐一应用到所有来源文件；无法定位的映射会被标出，不会静默选择附近内容。</div>
      {err && <div className="notice err">{err}</div>}
      {data?.rule_error && <div className="notice err small">{data.rule_error}</div>}
      {data?.static?.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
      {data?.files.map((f: any) => {
        const bad = Object.entries(f.results || {}).filter(([, r]: any) => r.ok === false);
        return (
          <div key={f.id} className="card" style={{ marginBottom: 8, padding: 8 }}>
            <div className="row">
              <b className="grow small ellipsis" title={f.filename}>{f.filename}</b>
              {f.representative && <span className="badge info">代表</span>}
              {f.status !== "ready" && f.status !== "needs_ocr" ? <span className="badge err">{f.status}</span> : bad.length ? <span className="badge err">{bad.length} 个问题</span> : f.record_error ? <span className="badge err">样本错误</span> : <span className="badge ok">通过</span>}
            </div>
            {f.error && <div className="tiny" style={{ color: "var(--err)" }}>{f.error}</div>}
            {f.record_error && <div className="tiny" style={{ color: "var(--err)" }}>{f.record_error}</div>}
            {!f.same_structure && f.status === "ready" && !f.representative && <div className="tiny muted">结构特征与代表文件不同（仅提示；以下逐项核对为准）</div>}
            {f.records !== undefined && <div className="tiny muted">样本 {f.records} 条（按首条样本核对）</div>}
            {bad.map(([mid, r]: any) => {
              const m = byId[mid];
              const ov = m?.overrides?.[f.id];
              return (
                <div key={mid} className="col gap4" style={{ borderTop: "1px dashed var(--line)", paddingTop: 4, marginTop: 4 }}>
                  <div className="row small"><span className="num" style={{ background: m?.color }}>{m?.no}</span><span className="grow" style={{ color: "var(--err)" }}>{r.message}</span></div>
                  <div className="row gap4">
                    <button className="btn xs" onClick={() => p.onRelocate(f.id, mid)}>在此文件中重新定位</button>
                    {r.code === "ocr_low_confidence" && <OcrFix fileId={f.id} mid={mid} initial={r.value || ""} onCorrect={p.onCorrect} />}
                    {ov && <button className="btn xs" onClick={() => p.onClearOverride(f.id, mid)}>清除该文件的单独定位</button>}
                  </div>
                </div>
              );
            })}
            {Object.entries(f.results || {}).filter(([mid]) => byId[mid]?.overrides?.[f.id]).map(([mid]) => (
              <div key={mid} className="tiny muted">映射 #{byId[mid]?.no} 在此文件使用单独定位 <button className="btn xs ghost" onClick={() => p.onClearOverride(f.id, mid)}>清除</button></div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function OcrFix({ fileId, mid, initial, onCorrect }: { fileId: string; mid: string; initial: string; onCorrect: Props["onCorrect"] }) {
  const { prompt } = useDialog();
  return <button className="btn xs" onClick={async () => { const v = await prompt("确认 / 修正识别值", `识别结果为「${initial}」。请对照左侧原图核对，确认无误直接保存，或改为正确的值：`, initial); if (v !== null) await onCorrect(fileId, mid, v); }}>确认/修正识别值</button>;
}
