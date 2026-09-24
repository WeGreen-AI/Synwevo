import { useEffect, useMemo, useState } from "react";
import { describeLocator, MAP_TYPE } from "../lib/describe";
import type { Mapping, Project, Slot } from "../lib/types";
import { Modal } from "@synwevo/ui";
import Transforms from "./Transforms";

export interface Proposal { label: string; locator: any; varies?: string; sample?: any; detail?: boolean }

interface Props {
  project: Project;
  slot: Slot;
  slots: Slot[];
  fileId: string | null;
  fileName: string;
  proposals: Proposal[];
  warnings: string[];
  existing: Mapping | null;      // mapping already targeting this binding
  editing: Mapping | null;       // editing an existing mapping (no new source)
  templateFormat: string;
  sourceFormat: string;
  onSave: (m: Mapping, replaceId?: string | null, note?: string) => Promise<void>;
  onClose: () => void;
}

const norm = (s: string) => (s || "").normalize("NFKC").replace(/\s+/g, " ").trim();

export default function MappingDialog(p: Props) {
  const mode = p.project.record_rule?.mode;
  const slotType = p.slot.kind === "repeat" ? "repeat" : p.slot.kind === "region" ? "region" : "single";
  const [pick, setPick] = useState(0);
  const [transforms, setTransforms] = useState<any[]>(p.editing?.transforms || []);
  const [agg, setAgg] = useState(p.editing?.agg || "unique");
  const [aggSep, setAggSep] = useState(p.editing?.agg_sep || "、");
  const [valueMode, setValueMode] = useState(p.editing?.value_mode || "auto");
  const [style, setStyle] = useState(p.editing?.style || "target");
  const [combineMode, setCombineMode] = useState<"merge" | "replace">("merge");
  const [combineOp, setCombineOp] = useState(p.existing?.combine?.op || "concat");
  const [combineSep, setCombineSep] = useState(p.existing?.combine?.sep ?? " ");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const usable = useMemo(() => p.proposals.filter((x) => (slotType === "repeat" ? x.detail : slotType === "region" ? x.locator.kind === "range" || x.locator.kind === "docx_table_block" : !x.detail && x.locator.kind !== "range")), [p.proposals, slotType]);
  const chosen = p.editing ? null : usable[Math.min(pick, usable.length - 1)];
  const loc = p.editing ? p.editing.sources[0] : chosen?.locator;

  // repeat columns: slot columns ← source headers (matched by name; explicit when different)
  const srcHeaders: string[] = useMemo(() => {
    if (p.editing) return Array.isArray(p.editing.sources[0]?.headers) ? (p.editing.sources[0].headers as string[]) : [];
    const h = chosen?.locator?.headers ?? (chosen?.detail ? chosen?.sample : null);
    return Array.isArray(h) ? (h as string[]) : [];
  }, [chosen, p.editing]);
  const [cols, setCols] = useState<Record<string, string>>({});
  useEffect(() => {
    if (slotType !== "repeat") return;
    const init: Record<string, string> = {};
    for (const c of p.slot.columns || []) {
      const prev = p.editing?.columns?.find((x) => x.slot_col === c.key);
      if (prev) init[c.key] = prev.source?.kind === "constant" ? "" : prev.source?.header || "";
      else {
        const hit = srcHeaders.find((h) => norm(h) === norm(c.name));
        init[c.key] = hit || "";
      }
    }
    setCols(init);
  }, [srcHeaders.join("|"), p.slot.id]);

  const regionMismatch = useMemo(() => {
    if (slotType !== "region" || !loc || loc.kind !== "range") return null;
    const [a, b] = String(loc.range).split(":");
    const m1 = /([A-Z]+)(\d+)/.exec(a)!, m2 = /([A-Z]+)(\d+)/.exec(b || a)!;
    const rows = Number(m2[2]) - Number(m1[2]) + 1;
    const colsN = colN(m2[1]) - colN(m1[1]) + 1;
    if (rows !== p.slot.rows || colsN !== p.slot.cols) return `来源区域为 ${rows} 行 × ${colsN} 列，目标区域「${p.slot.name}」为 ${p.slot.rows} 行 × ${p.slot.cols} 列。尺寸不一致，不能保存：请重新选择来源区域，或修改目标区域大小（系统不会自动截断、补齐或转置）。`;
    return null;
  }, [loc, slotType]);

  const save = async () => {
    setErr(null);
    if (!loc) {
      setErr(slotType === "repeat" ? "重复明细字段需要在左侧选择「明细表」：包含表头的多行区域，或文档中的整张表格" : "没有可用的来源");
      return;
    }
    if (regionMismatch) {
      setErr(regionMismatch);
      return;
    }
    const base: any = {
      type: slotType,
      target: { binding: p.slot.binding || p.slot.id },
      transforms: slotType === "repeat" ? [] : transforms,
      enabled: true,
    };
    if (slotType === "single") {
      if (mode === "per_group") {
        base.agg = agg;
        base.agg_sep = aggSep;
      }
      if (p.templateFormat === "xlsx") base.value_mode = valueMode;
      if (style === "source") base.style = "source";
    }
    if (slotType === "repeat") {
      const missing = (p.slot.columns || []).filter((c) => !cols[c.key]);
      base.columns = (p.slot.columns || []).map((c) => ({ slot_col: c.key, source: cols[c.key] ? { header: cols[c.key] } : { kind: "constant", value: "" } }));
      if (missing.length === (p.slot.columns || []).length) {
        setErr("至少需要为一列设置来源列");
        return;
      }
    }
    setSaving(true);
    try {
      if (p.editing) {
        await p.onSave({ ...p.editing, ...base, sources: p.editing.sources }, null, `修改映射 #${p.editing.no}`);
      } else if (p.existing && combineMode === "merge" && slotType === "single") {
        const merged: Mapping = { ...p.existing, sources: [...p.existing.sources, loc], combine: { op: combineOp, sep: combineSep } };
        await p.onSave(merged, null, `映射 #${p.existing.no} 增加来源`);
      } else {
        const m: any = { ...base, sources: [loc], source_file: p.fileId, label: chosen?.label, varies: chosen?.varies };
        await p.onSave(m, p.existing && combineMode === "replace" ? p.existing.id : null, `新建映射 → ${p.slot.name}`);
      }
      p.onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setSaving(false);
    }
  };

  const styleAllowed = p.templateFormat === "docx" && p.sourceFormat.startsWith("doc");

  return (
    <Modal title={p.editing ? `编辑映射 #${p.editing.no}` : "建立映射"} onClose={p.onClose} size="wide"
      footer={<>
        {err && <div className="notice err grow">{err}</div>}
        <span className="spacer" />
        <button className="btn" onClick={p.onClose}>取消</button>
        <button className="btn primary" disabled={saving || (!p.editing && !usable.length)} onClick={save}>{saving ? "保存中…" : "保存映射"}</button>
      </>}>
      <div className="grid2" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        <div className="col">
          <h3>来源 <span className="muted small">（{p.fileName}）</span></h3>
          {p.editing ? (
            <div className="col gap4">
              {p.editing.sources.map((s, i) => <div key={i} className="notice">{describeLocator(s)}</div>)}
              <div className="tiny muted">如需更换来源，请删除后重新建立，或在「校验」中为单个文件重新定位。</div>
            </div>
          ) : usable.length === 0 ? (
            <div className="notice warn">
              {slotType === "repeat" ? "目标是重复明细行：请在左侧选择包含表头的多行区域（例如表头行到最后一行），或点击文档表格左上角「▦」选择整张表。" :
                slotType === "region" ? "目标是固定区域：请在左侧框选同样大小的单元格区域。" : "当前选择不能作为单值来源；请选择单个单元格、文字片段或 PDF 区域。"}
            </div>
          ) : (
            usable.map((x, i) => (
              <label key={i} className={`opt${i === pick ? " on" : ""}`}>
                <input type="radio" checked={i === pick} onChange={() => setPick(i)} />
                <div className="col gap4 grow">
                  <div className="t">{x.label}</div>
                  <div className="small muted">
                    {x.varies === "per_record" ? "✔ 随样本变化：每份输出读取当前样本对应的值" : x.varies === "fixed" ? "● 固定值：同一文件中所有样本使用同一个值" : "◆ 随文件变化：每个来源文件在相同位置取值"}
                  </div>
                  {x.sample !== undefined && (
                    <div className="small">示例值：<span className="mono" style={{ background: "#f3f4f6", padding: "0 4px" }}>{Array.isArray(x.sample) ? (Array.isArray(x.sample[0]) ? x.sample.map((r: any) => r.join(" | ")).join(" ⏎ ") : x.sample.join(" | ")) : String(x.sample) || "（空）"}</span></div>
                  )}
                </div>
              </label>
            ))
          )}
          {p.warnings.map((w, i) => <div key={i} className="notice warn">{w}</div>)}
          {!p.editing && p.existing && slotType === "single" && (
            <div className="notice info col gap4">
              <b>字段「{p.slot.name}」已有映射 #{p.existing.no}</b>
              <label className="check"><input type="radio" checked={combineMode === "merge"} onChange={() => setCombineMode("merge")} /> 作为第 {p.existing.sources.length + 1} 个来源合并写入：</label>
              {combineMode === "merge" && (
                <div className="row" style={{ paddingLeft: 22 }}>
                  <select className="input sm" value={combineOp} onChange={(e) => setCombineOp(e.target.value)}>
                    <option value="concat">拼接</option><option value="newline">换行拼接</option><option value="sum">求和（数值）</option>
                  </select>
                  {combineOp === "concat" && <>分隔符 <input className="input sm" style={{ width: 60 }} value={combineSep} onChange={(e) => setCombineSep(e.target.value)} /></>}
                </div>
              )}
              <label className="check"><input type="radio" checked={combineMode === "replace"} onChange={() => setCombineMode("replace")} /> 替换原映射 #{p.existing.no}</label>
            </div>
          )}
        </div>
        <div className="col">
          <h3>目标字段</h3>
          <div className="notice">
            <b>{p.slot.name}</b> <span className="badge">{MAP_TYPE[slotType]}</span>
            {p.slot.kind === "region" && <span className="small muted"> · {p.slot.rows} 行 × {p.slot.cols} 列</span>}
            {p.slot.required && <span className="badge err" style={{ marginLeft: 4 }}>必填</span>}
          </div>
          {regionMismatch && <div className="notice err">{regionMismatch}</div>}
          {slotType === "repeat" && (
            <div className="col gap4">
              <div className="small muted">列对应（按列名匹配，列顺序不同也不会错位；未匹配的列请手动选择）：</div>
              <table className="tbl">
                <thead><tr><th>目标列</th><th>来源列</th></tr></thead>
                <tbody>
                  {(p.slot.columns || []).map((c) => (
                    <tr key={c.key}>
                      <td>{c.name}</td>
                      <td>
                        <select className="input sm" value={cols[c.key] || ""} onChange={(e) => setCols({ ...cols, [c.key]: e.target.value })}>
                          <option value="">（留空）</option>
                          {srcHeaders.map((h, i) => <option key={i} value={h}>{h}{srcHeaders.filter((x) => norm(x) === norm(h)).length > 1 ? "（重名）" : ""}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="tiny muted">零条明细时的处理方式、表头跨页重复等在「字段」面板中设置。</div>
            </div>
          )}
          {slotType !== "repeat" && (
            <>
              <h4 className="mt8">值转换</h4>
              <Transforms value={transforms} onChange={setTransforms} />
            </>
          )}
          {slotType === "single" && mode === "per_group" && (loc?.kind === "column" || p.editing) && (
            <label className="field mt8">组内取值规则（同一组有多行时）
              <select className="input" value={agg} onChange={(e) => setAgg(e.target.value)}>
                <option value="unique">必须一致，不一致时报冲突（默认）</option>
                <option value="first">取组内第一条（按排序规则）</option>
                <option value="last">取组内最后一条</option>
                <option value="join">全部拼接</option>
                <option value="join_distinct">去重后拼接</option>
                <option value="sum">求和</option>
                <option value="count">计数（行数）</option>
              </select>
              {(agg === "join" || agg === "join_distinct") && <input className="input sm mt8" value={aggSep} onChange={(e) => setAggSep(e.target.value)} placeholder="分隔符" />}
            </label>
          )}
          {slotType === "single" && p.templateFormat === "xlsx" && (
            <label className="field mt8">写入方式
              <select className="input" value={valueMode} onChange={(e) => setValueMode(e.target.value)}>
                <option value="auto">按值类型写入（数字仍是数字，日期仍是日期；显示格式沿用目标单元格）</option>
                <option value="display">写入来源显示文本（如 000123、¥1,000.00）</option>
              </select>
            </label>
          )}
          {slotType === "single" && (
            <label className="field mt8">样式
              <select className="input" value={style} onChange={(e) => setStyle(e.target.value)} disabled={!styleAllowed && style !== "source"}>
                <option value="target">只传递数据，沿用目标模板样式（默认）</option>
                <option value="source" disabled={!styleAllowed}>复制来源样式（加粗/斜体/下划线/颜色）{styleAllowed ? "" : " — 仅 DOCX→DOCX 支持"}</option>
              </select>
            </label>
          )}
        </div>
      </div>
    </Modal>
  );
}

function colN(letters: string) {
  let n = 0;
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}
