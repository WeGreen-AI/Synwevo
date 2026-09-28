import { formatNumber } from "@synwevo/ui";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useMemo, useState } from "react";
import { describeLocator, MAP_TYPE } from "../lib/describe";
import { mergeSource, repeatColumns, sectionColumns, withSettings, type MappingSettings } from "../lib/mapping";
import { errorText } from "../lib/perm";
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
  const { locale } = useI18n();
  const mode = p.project.record_rule?.mode;
  const slotType = p.slot.kind === "repeat" ? "repeat" : p.slot.kind === "region" ? "region" : "single";
  const [pick, setPick] = useState(0);
  // Settings start from the mapping being edited or, when a source is being added to a field that
  // already has a mapping, from that mapping — the controls on screen are what gets saved.
  const seed = p.editing || (slotType === "single" ? p.existing : null);
  const [transforms, setTransforms] = useState<any[]>(seed?.transforms || []);
  const [agg, setAgg] = useState(seed?.agg || "unique");
  const [aggSep, setAggSep] = useState(seed?.agg_sep || "、");
  const [valueMode, setValueMode] = useState(seed?.value_mode || "auto");
  const [style, setStyle] = useState(seed?.style || "target");
  const [combineMode, setCombineMode] = useState<"merge" | "replace">("merge");
  const [combineOp, setCombineOp] = useState(p.existing?.combine?.op || "concat");
  const [combineSep, setCombineSep] = useState(p.existing?.combine?.sep ?? " ");
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // A Word repeat content block may be split by 分段字段: one copy per sub-group, and a detail
  // table inside it gets only that sub-group's rows in each copy (docs-nested-details.md).
  const isBlock = slotType === "repeat" && !!p.slot.block;
  const children = useMemo(() => p.slots.filter((s) => s.parent === p.slot.id), [p.slots, p.slot.id]);
  const parentBlock = p.slot.parent ? p.slots.find((s) => s.id === p.slot.parent) || null : null;
  const [split, setSplit] = useState<string>(p.editing?.group_by?.keys?.[0]?.header || "");
  const [emptyKey, setEmptyKey] = useState<"error" | "skip">(p.editing?.group_by?.empty_key || "error");
  const [aggs, setAggs] = useState<Record<string, string>>(() =>
    Object.fromEntries((p.editing?.columns || []).filter((c) => c.agg).map((c) => [c.slot_col, c.agg as string])));

  const usable = useMemo(() => p.proposals.filter((x) => (slotType === "repeat" ? x.detail : slotType === "region" ? x.locator.kind === "range" || x.locator.kind === "docx_table_block" : !x.detail && x.locator.kind !== "range")), [p.proposals, slotType]);
  const chosen = p.editing ? null : usable[Math.min(pick, usable.length - 1)];
  const loc = p.editing ? p.editing.sources[0] : chosen?.locator;

  // repeat columns: slot columns ← source headers (matched by name; explicit when different)
  const srcHeaders: string[] = useMemo(() => {
    if (p.editing) return Array.isArray(p.editing.sources[0]?.headers) ? (p.editing.sources[0].headers as string[]) : [];
    const h = chosen?.locator?.headers ?? (chosen?.detail ? chosen?.sample : null);
    return Array.isArray(h) ? (h as string[]) : [];
  }, [chosen, p.editing]);
  const initialCols = () => {
    const init: Record<string, string> = {};
    if (slotType !== "repeat") return init;
    for (const c of p.slot.columns || []) {
      const prev = p.editing?.columns?.find((x) => x.slot_col === c.key);
      if (prev) init[c.key] = prev.source?.kind === "constant" ? "" : prev.source?.header || "";
      else {
        const hit = srcHeaders.find((h) => norm(h) === norm(c.name));
        init[c.key] = hit || "";
      }
    }
    return init;
  };
  // filled on the first render (an edit used to show every column as 「留空」 for a moment), and
  // again when another source is picked
  const [cols, setCols] = useState<Record<string, string>>(initialCols);
  useEffect(() => { if (slotType === "repeat") setCols(initialCols()); }, [srcHeaders.join("|"), p.slot.id]);
  // A mapping made through the API or a definition file may carry no header list: what it has
  // saved stays a visible choice instead of the select showing 「留空」/「不分段」 over it.
  const headerOptions = useMemo(() => {
    const out = [...srcHeaders];
    for (const h of [...Object.values(cols), split]) if (h && !out.includes(h)) out.push(h);
    return out;
  }, [srcHeaders, cols, split]);

  const regionMismatch = useMemo(() => {
    if (slotType !== "region" || !loc || loc.kind !== "range") return null;
    const [a, b] = String(loc.range).split(":");
    const m1 = /^([A-Z]+)(\d+)$/.exec(a), m2 = /^([A-Z]+)(\d+)$/.exec(b || a);
    if (!m1 || !m2) return tr("来源区域无效，请重新选择单元格区域");
    const rows = Number(m2[2]) - Number(m1[2]) + 1;
    const colsN = colN(m2[1]) - colN(m1[1]) + 1;
    if (rows !== p.slot.rows || colsN !== p.slot.cols) return tr("来源区域为 {v1} 行 × {v2} 列，目标区域「{v3}」为 {v4} 行 × {v5} 列。尺寸不一致，不能保存：请重新选择来源区域，或修改目标区域大小。", { v1: rows, v2: colsN, v3: p.slot.name, v4: p.slot.rows, v5: p.slot.cols });
    return null;
  }, [loc, slotType, locale]);

  const save = async () => {
    setErr(null);
    if (!loc) {
      setErr(slotType === "repeat" ? tr("重复明细字段需要在左侧选择「明细表」：包含表头的多行区域，或文档中的整张表格") : tr("没有可用的来源"));
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
      base.columns = repeatColumns((p.slot.columns || []).map((c) => c.key), cols, p.editing?.columns);
      if (!p.editing && missing.length === (p.slot.columns || []).length) {
        setErr(tr("至少需要为一列设置来源列"));
        return;
      }
      if (isBlock && children.length && !split) {
        setErr(tr("内容块里有明细表：请先选择分段字段，每一份才会只写入自己那一段的明细行"));
        return;
      }
      if (isBlock) {
        base.columns = sectionColumns(base.columns, aggs, !!split);
        base.group_by = split ? { keys: [{ header: split }], empty_key: emptyKey } : null;
      }
    }
    // What the dialog shows, applied the same way to an edit and to a merge (lib/mapping.ts):
    // a merge used to drop these, and an edit re-enabled a disabled mapping.
    const settings: MappingSettings = {
      transforms: base.transforms,
      columns: base.columns,
      agg: base.agg, agg_sep: base.agg_sep, value_mode: base.value_mode,
      style: slotType === "single" ? (style === "source" ? "source" : "target") : undefined,
      group_by: isBlock ? base.group_by : undefined,
    };
    setSaving(true);
    try {
      if (p.editing) {
        await p.onSave(withSettings({ ...p.editing, type: base.type, target: base.target }, settings), null, `修改映射 #${p.editing.no}`);
      } else if (p.existing && combineMode === "merge" && slotType === "single") {
        await p.onSave(mergeSource(p.existing, loc, { op: combineOp, sep: combineSep }, settings), null, `映射 #${p.existing.no} 增加来源`);
      } else {
        const m: any = { ...base, sources: [loc], source_file: p.fileId, label: chosen?.label, varies: chosen?.varies };
        if (!m.group_by) delete m.group_by;
        await p.onSave(m, p.existing ? p.existing.id : null, `新建映射 → ${p.slot.name}`);
      }
      p.onClose();
    } catch (e: any) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const styleAllowed = p.templateFormat === "docx" && p.sourceFormat.startsWith("doc");

  return (
    <Modal title={p.editing ? tr("编辑映射 #{v1}", { v1: p.editing.no }) : tr("建立映射")} onClose={p.onClose} size="wide"
      footer={<>
        {err && <div className="notice err grow">{serverText(err)}</div>}
        <span className="spacer" />
        <button className="btn" onClick={p.onClose}>{tr("取消")}</button>
        <button className="btn primary" disabled={saving || (!p.editing && !usable.length)} onClick={save}>{saving ? tr("保存中…") : tr("保存映射")}</button>
      </>}>
      <div className="grid2" style={{ gridTemplateColumns: "1.1fr 1fr" }}>
        <div className="col">
          <h3>{tr("来源 ")}<span className="muted small">（{p.fileName}）</span></h3>
          {p.editing ? (
            <div className="col gap4">
              {p.editing.sources.map((s, i) => <div key={i} className="notice">{describeLocator(s)}</div>)}
              <div className="tiny muted">{tr("如需更换来源，请删除后重新建立，或在「校验」中为单个文件重新定位。")}</div>
            </div>
          ) : usable.length === 0 ? (
            <div className="notice warn">
              {slotType === "repeat" ? tr("目标是重复明细行：请在左侧选择包含表头的多行区域（例如表头行到最后一行），或点击文档表格左上角「▦」选择整张表。") :
                slotType === "region" ? tr("目标是固定区域：请在左侧框选同样大小的单元格区域。") : tr("当前选择不能作为单值来源；请选择单个单元格、文字片段或 PDF 区域。")}
            </div>
          ) : (
            usable.map((x, i) => (
              <label key={i} className={`opt${i === pick ? " on" : ""}`}>
                <input type="radio" checked={i === pick} onChange={() => setPick(i)} />
                <div className="col gap4 grow">
                  <div className="t">{describeLocator(x.locator)}</div>
                  <div className="small muted">
                    {x.detail ? (mode === "per_group" ? tr("✔ 重复明细：每份输出写入当前分组的全部明细行（按分组与排序规则取行，与所选行数无关）")
                      : mode === "per_row" ? tr("◆ 重复明细：所选明细行整体写入每一份输出（每行一份时，明细区域不随样本变化）")
                      : tr("◆ 重复明细：每个来源文件的该明细区域整体写入同一份文档"))
                      : x.varies === "per_record" ? tr("✔ 随样本变化：每份输出读取当前样本对应的值") : x.varies === "fixed" ? tr("● 固定值：同一文件中所有样本使用同一个值") : tr("◆ 随文件变化：每个来源文件在相同位置取值")}
                  </div>
                  {x.sample !== undefined && (
                    <div className="small">{tr("示例值：")}<span className="mono" style={{ background: "var(--surface-hover)", padding: "0 4px", borderRadius: 3 }}>{Array.isArray(x.sample) ? (Array.isArray(x.sample[0]) ? x.sample.map((r: any) => r.join(" | ")).join(" ⏎ ") : x.sample.join(" | ")) : String(x.sample) || tr("（空）")}</span></div>
                  )}
                </div>
              </label>
            ))
          )}
          {p.warnings.map((w, i) => <div key={i} className="notice warn">{diagnosticText(w)}</div>)}
          {!p.editing && p.existing && slotType !== "single" && <div className="notice info">{tr("将替换字段「")}{p.slot.name}{tr("」的原映射 #")}{formatNumber(p.existing.no)}。</div>}
          {!p.editing && p.existing && slotType === "single" && (
            <div className="notice info col gap4">
              <b>{tr("字段「")}{p.slot.name}{tr("」已有映射 #")}{formatNumber(p.existing.no)}</b>
              <label className="check"><input type="radio" checked={combineMode === "merge"} onChange={() => setCombineMode("merge")} />{tr(" 作为第 ")}{formatNumber(p.existing.sources.length + 1)}{tr(" 个来源合并写入：")}</label>
              {combineMode === "merge" && (
                <div className="row" style={{ paddingLeft: 22 }}>
                  <select className="input sm" value={combineOp} onChange={(e) => setCombineOp(e.target.value)}>
                    <option value="concat">{tr("拼接")}</option><option value="newline">{tr("换行拼接")}</option><option value="sum">{tr("求和（数值）")}</option>
                  </select>
                  {combineOp === "concat" && <>{tr("分隔符 ")}<input className="input sm" style={{ width: 60 }} value={combineSep} onChange={(e) => setCombineSep(e.target.value)} /></>}
                </div>
              )}
              <label className="check"><input type="radio" checked={combineMode === "replace"} onChange={() => setCombineMode("replace")} />{tr(" 替换原映射 #")}{formatNumber(p.existing.no)}</label>
            </div>
          )}
        </div>
        <div className="col">
          <h3>{tr("目标字段")}</h3>
          <div className="notice">
            <b>{p.slot.name}</b> <span className="badge">{tr(MAP_TYPE[slotType])}</span>
            {p.slot.kind === "region" && <span className="small muted"> · {formatNumber(p.slot.rows)}{tr(" 行 × ")}{formatNumber(p.slot.cols)}{tr(" 列")}</span>}
            {p.slot.required && <span className="badge err" style={{ marginLeft: 4 }}>{tr("必填")}</span>}
          </div>
          {regionMismatch && <div className="notice err">{regionMismatch}</div>}
          {slotType === "repeat" && parentBlock && (
            <div className="notice info">{tr("此表在内容块「{v1}」内：每一份内容块只写入它那一段的明细行（由内容块的分段字段决定）。", { v1: parentBlock.name })}</div>
          )}
          {isBlock && (
            <div className="col gap4">
              <label className="field">{tr("分段字段")}
                <select className="input" value={split} onChange={(e) => setSplit(e.target.value)}>
                  <option value="">{tr("不分段：每一行明细一份内容块")}</option>
                  {headerOptions.map((h, i) => <option key={i} value={h}>{h}</option>)}
                </select>
              </label>
              <div className="tiny muted">{tr("分段后，分段字段相同的明细行合成一份内容块（按每段第一次出现的顺序）；内容块里的明细表每份只写入该段的行。")}</div>
              {children.length > 0 && !split && (
                <div className="notice warn">{tr("内容块里有明细表「{v1}」：需要选择分段字段，每一份才会只写入自己那一段的明细行。", { v1: new Intl.ListFormat(locale, { type: "conjunction" }).format(children.map((c) => c.name)) })}</div>
              )}
              {split && (
                <label className="field">{tr("分段字段为空的明细行")}
                  <select className="input" value={emptyKey} onChange={(e) => setEmptyKey(e.target.value as "error" | "skip")}>
                    <option value="error">{tr("报错，不生成这一份（默认）")}</option>
                    <option value="skip">{tr("跳过这些行并提示")}</option>
                  </select>
                </label>
              )}
            </div>
          )}
          {slotType === "repeat" && (
            <div className="col gap4">
              <div className="small muted">{tr("列对应（按列名自动匹配；未匹配的列请手动选择）：")}</div>
              <table className="tbl">
                <thead><tr><th>{tr("目标列")}</th><th>{tr("来源列")}</th>{isBlock && split && <th>{tr("段内取值")}</th>}</tr></thead>
                <tbody>
                  {(p.slot.columns || []).map((c) => (
                    <tr key={c.key}>
                      <td>{c.name}</td>
                      <td>
                        <select className="input sm" value={cols[c.key] || ""} onChange={(e) => setCols({ ...cols, [c.key]: e.target.value })}>
                          <option value="">{p.editing?.columns?.find((x) => x.slot_col === c.key)?.source?.kind === "constant" ? tr("（保留原常量）") : tr("（留空）")}</option>
                          {headerOptions.map((h, i) => <option key={i} value={h}>{h}{srcHeaders.filter((x) => norm(x) === norm(h)).length > 1 ? tr("（重名）") : ""}</option>)}
                        </select>
                      </td>
                      {isBlock && split && (
                        <td>
                          <select className="input sm" aria-label={tr("「{v1}」的段内取值", { v1: c.name })} value={aggs[c.key] || "unique"}
                            disabled={!cols[c.key]} onChange={(e) => setAggs({ ...aggs, [c.key]: e.target.value })}>
                            <option value="unique">{tr("必须一致（默认）")}</option>
                            <option value="first">{tr("取段内第一条")}</option>
                            <option value="last">{tr("取段内最后一条")}</option>
                            <option value="join">{tr("全部拼接")}</option>
                            <option value="join_distinct">{tr("去重后拼接")}</option>
                            <option value="sum">{tr("求和")}</option>
                            <option value="count">{tr("计数（该列有值的行数）")}</option>
                          </select>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="tiny muted">{tr("零条明细时的处理方式、表头跨页重复等在「字段」面板中设置。")}</div>
            </div>
          )}
          {slotType !== "repeat" && (
            <>
              <h4 className="mt8">{tr("值转换")}</h4>
              <Transforms value={transforms} onChange={setTransforms} />
            </>
          )}
          {slotType === "single" && mode === "per_group" && (loc?.kind === "column" || p.editing) && (
            <label className="field mt8">{tr("组内取值规则（同一组有多行时）")}<select className="input" value={agg} onChange={(e) => setAgg(e.target.value)}>
                <option value="unique">{tr("必须一致，不一致时报冲突（默认）")}</option>
                <option value="first">{tr("取组内第一条（按排序规则）")}</option>
                <option value="last">{tr("取组内最后一条")}</option>
                <option value="join">{tr("全部拼接")}</option>
                <option value="join_distinct">{tr("去重后拼接")}</option>
                <option value="sum">{tr("求和")}</option>
                <option value="count">{tr("计数（行数）")}</option>
              </select>
              {(agg === "join" || agg === "join_distinct") && <input className="input sm mt8" value={aggSep} onChange={(e) => setAggSep(e.target.value)} placeholder={tr("分隔符")} />}
            </label>
          )}
          {slotType === "single" && p.templateFormat === "xlsx" && (
            <label className="field mt8">{tr("写入方式")}<select className="input" value={valueMode} onChange={(e) => setValueMode(e.target.value)}>
                <option value="auto">{tr("按值类型写入（数字仍是数字，日期仍是日期；显示格式沿用目标单元格）")}</option>
                <option value="display">{tr("写入来源显示文本（如 000123、¥1,000.00）")}</option>
              </select>
            </label>
          )}
          {slotType === "single" && (
            <label className="field mt8">{tr("样式")}<select className="input" value={style} onChange={(e) => setStyle(e.target.value)} disabled={!styleAllowed && style !== "source"}>
                <option value="target">{tr("只传递数据，沿用目标模板样式（默认）")}</option>
                <option value="source" disabled={!styleAllowed}>{tr("复制来源样式（加粗/斜体/下划线/颜色）")}{styleAllowed ? "" : tr(" — 仅 DOCX→DOCX 支持")}</option>
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
