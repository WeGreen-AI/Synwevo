import { formatNumber } from "@synwevo/ui";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useMemo, useState } from "react";
import { get, post, qs } from "@synwevo/auth-client";
import { useLatest } from "../lib/latest";
import type { Project, RecordRule, SourceFile } from "../lib/types";
import { Modal } from "@synwevo/ui";
import { errorText } from "../lib/perm";

const MODES = [
  { key: "per_row", title: "每行一份", body: "表格中每条有效数据行 = 一份输出", ex: "例：客户表 100 行 → 生成 100 份合同" },
  { key: "per_file", title: "每文件一份", body: "每个来源文件 = 一份输出（文件内多处数据、多个明细表共同填入）", ex: "例：20 份检测表 → 生成 20 份报告" },
  { key: "per_group", title: "每组一份", body: "按选定字段（如订单号）分组，每组 = 一份输出，组内多行作为明细", ex: "例：100 条明细属于 15 个订单 → 生成 15 份订单" },
  { key: "per_block", title: "每块一份", body: "一个文件里有多张表单：按每块开头的标记文字切开，每块 = 一份输出", ex: "例：一个 PDF 里 30 张登记表 → 生成 30 份" },
] as const;

interface Props {
  project: Project;
  sources: SourceFile[];
  initial?: Partial<RecordRule> | null;
  /** text currently selected in the source pane – offered as the block start marker */
  selectedText?: string;
  onSave: (r: RecordRule) => Promise<void>;
  onClose: () => void;
}

export default function RuleDialog({ project, sources, initial, selectedText, onSave, onClose }: Props) {
  useI18n();
  const rule0: RecordRule = { ...(project.record_rule || {}), ...(initial || {}) } as RecordRule;
  const [mode, setMode] = useState<RecordRule["mode"]>(rule0.mode || "per_row");
  const tabular = sources.filter((s) => s.family === "table" || (s.family === "text" && s.structure?.text?.tables));
  // Sheets and header names come from a tabular file: the representative when it is one,
  // otherwise the first ready table. A PDF/DOCX representative next to XLSX sources used to
  // leave per_row / per_group without any sheet or column to choose.
  const isTab = (s: SourceFile) => tabular.includes(s) && s.status === "ready";
  const rep = sources.find((s) => s.representative && isTab(s)) || sources.find(isTab)
    || sources.find((s) => s.representative && s.status === "ready") || sources.find((s) => s.status === "ready");
  const sheets: any[] = rep?.structure?.sheets || [];
  const [sheet, setSheet] = useState<string>(rule0.table?.sheet || sheets[0]?.name || "");
  const [headerRow, setHeaderRow] = useState<number>((rule0.table?.header_row ?? 0) + 1);
  const [noHeader, setNoHeader] = useState(rule0.table?.header_row === null);
  const [dataStart, setDataStart] = useState<string>(rule0.table?.data_start != null ? String(rule0.table.data_start + 1) : "");
  const [dataEnd, setDataEnd] = useState<string>(rule0.table?.data_end != null ? String(rule0.table.data_end + 1) : "");
  const [skipBlank, setSkipBlank] = useState(rule0.table?.skip_blank !== false);
  const [filters, setFilters] = useState<any[]>(rule0.table?.filters || []);
  const [idCol, setIdCol] = useState<string>(rule0.id_column?.header || "");
  const [groupKeys, setGroupKeys] = useState<string[]>((rule0.group?.keys || []).map((k) => k.header));
  const [sortCol, setSortCol] = useState<string>(rule0.group?.sort?.[0]?.header || "");
  const [sortDir, setSortDir] = useState<string>(rule0.group?.sort?.[0]?.dir || "asc");
  const [sortNum, setSortNum] = useState<boolean>(!!rule0.group?.sort?.[0]?.numeric);
  const [scope, setScope] = useState<"file" | "cross_file">(rule0.group?.scope || "file");
  const [anchor, setAnchor] = useState<string>(rule0.block?.anchor || "");
  const [match, setMatch] = useState<string>(rule0.block?.match || "contains");
  const [endAnchor, setEndAnchor] = useState<string>(rule0.block?.end_anchor || "");
  const [headers, setHeaders] = useState<string[]>([]);
  const [preview, setPreview] = useState<any>(null);
  const [perr, setPerr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = useLatest();
  const [checkedKey, setCheckedKey] = useState("");

  // header names of the chosen header row (from the representative file)
  useEffect(() => {
    setHeaders([]);
    if (!rep || rep.family !== "table" && !(rep.family === "text")) return;
    if (noHeader) {
      setHeaders([]);
      return;
    }
    const ctl = new AbortController();
    get(`/api/sources/${rep.id}/view${qs({ sheet, offset: Math.max(0, headerRow - 1), limit: 1 })}`, ctl.signal)
      .then((v) => !ctl.signal.aborted && setHeaders((v.grid?.rows?.[0] || []).map((c: any) => c.v).filter((x: string) => x !== undefined)))
      .catch(() => { if (!ctl.signal.aborted) setHeaders([]); });
    return () => ctl.abort();
  }, [rep?.id, sheet, headerRow, noHeader]);

  const rule: RecordRule = useMemo(() => {
    const r: RecordRule = { mode };
    if (mode === "per_block") {
      r.block = { anchor: anchor.trim(), match: match as any, ...(endAnchor.trim() ? { end_anchor: endAnchor.trim() } : {}), ...(sheet && tabular.length ? { sheet } : {}) };
      return r;
    }
    if (mode !== "per_file") {
      r.table = { sheet, header_row: noHeader ? null : headerRow - 1, data_start: dataStart ? Number(dataStart) - 1 : null, data_end: dataEnd ? Number(dataEnd) - 1 : null, skip_blank: skipBlank, filters: filters.filter((f) => f.column) };
      if (idCol) r.id_column = { header: idCol };
    }
    if (mode === "per_group") r.group = { keys: groupKeys.filter(Boolean).map((h) => ({ header: h })), sort: sortCol ? [{ header: sortCol, dir: sortDir, numeric: sortNum }] : [], scope };
    return r;
  }, [mode, sheet, headerRow, noHeader, dataStart, dataEnd, skipBlank, filters, idCol, groupKeys, sortCol, sortDir, sortNum, scope, anchor, match, endAnchor]);

  const ruleKey = JSON.stringify(rule);
  const ready = checkedKey === ruleKey;
  useEffect(() => {
    let cancel = () => {};
    setPreview(null);
    setCheckedKey("");
    setPerr(null);
    const timer = setTimeout(() => {
      if (mode === "per_block" && !anchor.trim()) {
        setPreview(null);
        setPerr(tr("请填写每个表单块开头的标记文字"));
        return;
      }
      if (mode === "per_group" && !groupKeys.filter(Boolean).length) {
        setPreview(null);
        setPerr(tr("请选择分组字段"));
        return;
      }
      cancel = latest(
        (signal) => post(`/api/projects/${project.id}/rule-preview`, rule, signal),
        (x) => { setPreview(x); setCheckedKey(ruleKey); setPerr(null); },
        (e) => { setPreview(null); setCheckedKey(""); setPerr(errorText(e)); },
      );
    }, 250);
    return () => { clearTimeout(timer); cancel(); };
  }, [project.id, ruleKey, latest]);

  const save = async () => {
    if (!ready || busy || perr) return;
    setBusy(true);
    try {
      await onSave(rule);
      onClose();
    } catch (e: any) {
      setPerr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const noTable = (mode === "per_row" || mode === "per_group") && !tabular.length;

  return (
    <Modal title={tr("定义样本：每份输出对应什么数据？")} size="wide" onClose={onClose}
      footer={<>
        {preview && <span className="small">{tr("将识别 ")}<b>{formatNumber(preview.total)}</b>{tr(" 条样本 → 默认生成 ")}<b>{formatNumber(preview.total)}</b>{tr(" 份文档")}</span>}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>{tr("取消")}</button>
        <button className="btn primary" disabled={busy || !ready || noTable || !!perr} onClick={save}>{tr("确认样本规则")}</button>
      </>}>
      {perr && <div className="notice err" role="alert">{serverText(perr)}</div>}
      {!ready && !perr && <div className="notice info" role="status">{tr("正在识别当前规则下的样本…")}</div>}
      <div className="grid4">
        {MODES.map((m) => (
          <label key={m.key} className={`opt${mode === m.key ? " on" : ""}`} style={{ flexDirection: "column", gap: 4 }}>
            <div className="row"><input type="radio" checked={mode === m.key} onChange={() => setMode(m.key as any)} /><span className="t">{tr(m.title)}</span></div>
            <div className="small">{tr(m.body)}</div>
            <div className="small muted">{tr(m.ex)}</div>
          </label>
        ))}
      </div>
      <div className="notice info mt12 small">{tr("样本只决定哪些数据构成一份输出；多个来源文件各自提取样本后按文件顺序追加。")}</div>
      {noTable && <div className="notice warn mt12">「{mode === "per_row" ? tr("每行一份") : tr("每组一份")}{tr("」需要表格类来源（XLSX/CSV/XLS/ODS 或含表格的 HTML）。当前没有可用的表格文件。")}</div>}
      {mode === "per_block" && (
        <div className="grid2 mt12" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="col">
            <h3>{tr("如何切分表单块")}</h3>
            <label className="field">{tr("每块开头的标记文字")}<div className="row">
                <input className="input grow" value={anchor} onChange={(e) => setAnchor(e.target.value)} placeholder={tr("例如：人员登记表、姓名：、检测报告编号")} autoFocus />
                {selectedText && selectedText.trim() && selectedText.trim() !== anchor && (
                  <button className="btn sm" onClick={() => setAnchor(selectedText.trim().slice(0, 60))} title={tr("使用左侧当前选中的文字")}>{tr("用选中文字「")}{selectedText.trim().slice(0, 10)}」</button>
                )}
              </div>
            </label>
            <div className="grid2">
              <label className="field">{tr("匹配方式")}<select className="input" value={match} onChange={(e) => setMatch(e.target.value)}>
                  <option value="contains">{tr("所在行 / 段落包含该文字")}</option>
                  <option value="starts">{tr("所在行 / 段落以该文字开头")}</option>
                  <option value="exact">{tr("所在行 / 段落正好是该文字")}</option>
                  <option value="regex">{tr("正则表达式")}</option>
                </select>
              </label>
              <label className="field">{tr("结束标记（可选）")}<input className="input" value={endAnchor} onChange={(e) => setEndAnchor(e.target.value)} placeholder={tr("留空 = 到下一个开头标记")} />
              </label>
            </div>
            {tabular.length > 0 && sheets.length > 1 && (
              <label className="field">{tr("表格文件使用的工作表")}<select className="input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
                  {sheets.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </label>
            )}
            <div className="notice small">{tr("从每个标记出现的位置开始一个块，到下一个标记（或结束标记）为止；同一页可有多块，块也可跨页。建立映射时，用左侧的块导航切换到某一块再选取数据。")}</div>
          </div>
          <div className="col">
            <h3>{tr("识别结果预览")}</h3>
            {preview && (
              <>
                <div className="grid3">
                  <div className="statbox"><div className="v">{formatNumber(preview.total)}</div><div className="k">{tr("识别表单块")}</div></div>
                  <div className="statbox"><div className="v">{formatNumber(Object.keys(preview.blocks || {}).length)}</div><div className="k">{tr("包含表单块的文件")}</div></div>
                  <div className="statbox"><div className="v">{formatNumber(preview.file_errors.length)}</div><div className="k">{tr("未找到标记的文件")}</div></div>
                </div>
                {preview.file_errors.map((e: any, i: number) => <div key={i} className="notice err small">{issueText(e)}</div>)}
                <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <table className="tbl small">
                    <thead><tr><th>#</th><th>{tr("表单块")}</th><th>{tr("位置")}</th></tr></thead>
                    <tbody>{preview.records.map((r: any) => <tr key={r.key}><td>{formatNumber(r.index + 1)}</td><td>{r.label}</td><td className="muted">{serverText(r.where)}</td></tr>)}</tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      )}
      {(mode === "per_row" || mode === "per_group") && !noTable && (
        <div className="grid2 mt12" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="col">
            <h3>{tr("数据范围 ")}<span className="small muted">{tr("（以")}{rep?.representative ? tr("代表文件") : tr("表格文件")}「{rep?.filename}{tr("」为准）")}</span></h3>
            <div className="grid2">
              <label className="field">{tr("工作表")}<select className="input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
                  {sheets.map((s) => <option key={s.name} value={s.name}>{s.name}（{formatNumber(s.rows)}{tr(" 行）")}</option>)}
                </select>
              </label>
              <label className="field">{tr("表头行（第几行）")}<div className="row">
                  <input className="input" type="number" min={1} value={headerRow} disabled={noHeader} onChange={(e) => setHeaderRow(Math.max(1, Number(e.target.value)))} style={{ width: 80 }} />
                  <label className="check small"><input type="checkbox" checked={noHeader} onChange={(e) => setNoHeader(e.target.checked)} />{tr("无表头")}</label>
                </div>
              </label>
              <label className="field">{tr("数据起始行（留空 = 表头下一行）")}<input className="input" type="number" min={1} value={dataStart} onChange={(e) => setDataStart(e.target.value)} placeholder={String(headerRow + 1)} /></label>
              <label className="field">{tr("数据结束行（留空 = 到最后一行）")}<input className="input" type="number" min={1} value={dataEnd} onChange={(e) => setDataEnd(e.target.value)} placeholder={tr("末尾")} /></label>
            </div>
            {!noHeader && headers.length > 0 && <div className="small muted">{tr("表头：")}{headers.slice(0, 12).map((h, i) => <span key={i} className="badge" style={{ margin: "2px 4px 0 0" }}>{h || tr("（空）")}</span>)}</div>}
            <label className="check"><input type="checkbox" checked={skipBlank} onChange={(e) => setSkipBlank(e.target.checked)} />{tr(" 排除完全空白的行")}</label>
            <div className="col gap4">
              <div className="small">{tr("过滤条件（可选）")}</div>
              {filters.map((f, i) => (
                <div key={i} className="row">
                  <select className="input sm" value={f.column} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, column: e.target.value } : x)))}>
                    <option value="">{tr("选择列")}</option>{headers.map((h, k) => <option key={k} value={h}>{h}</option>)}
                  </select>
                  <select className="input sm" value={f.op} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, op: e.target.value } : x)))}>
                    <option value="nonempty">{tr("不为空")}</option><option value="empty">{tr("为空")}</option><option value="eq">{tr("等于")}</option><option value="neq">{tr("不等于")}</option><option value="contains">{tr("包含")}</option><option value="not_contains">{tr("不包含")}</option>
                  </select>
                  {["eq", "neq", "contains", "not_contains"].includes(f.op) && <input className="input sm" value={f.value || ""} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, value: e.target.value } : x)))} />}
                  <button className="btn xs ghost" onClick={() => setFilters(filters.filter((_, k) => k !== i))}>✕</button>
                </div>
              ))}
              <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => setFilters([...filters, { column: "", op: "nonempty" }])}>{tr("＋ 添加过滤")}</button>
            </div>
            {mode === "per_row" && (
              <label className="field">{tr("样本标识列（用于查找和命名，可选）")}<select className="input" value={idCol} onChange={(e) => setIdCol(e.target.value)}>
                  <option value="">{tr("（不设置）")}</option>{headers.map((h, i) => <option key={i} value={h}>{h}</option>)}
                </select>
              </label>
            )}
            {mode === "per_group" && (
              <div className="col">
                <h3>{tr("分组")}</h3>
                <label className="field">{tr("分组字段（可多选，按组合值分组）")}<div className="row wrap gap4">
                    {headers.map((h, i) => (
                      <label key={i} className="check badge" style={{ height: 24 }}>
                        <input type="checkbox" checked={groupKeys.includes(h)} onChange={(e) => setGroupKeys(e.target.checked ? [...groupKeys, h] : groupKeys.filter((x) => x !== h))} />{h}
                      </label>
                    ))}
                  </div>
                </label>
                <div className="row wrap">
                  <label className="field grow">{tr("组内排序")}<select className="input" value={sortCol} onChange={(e) => setSortCol(e.target.value)}>
                      <option value="">{tr("保持原始顺序")}</option>{headers.map((h, i) => <option key={i} value={h}>{h}</option>)}
                    </select>
                  </label>
                  {sortCol && <>
                    <select className="input" value={sortDir} onChange={(e) => setSortDir(e.target.value)} style={{ marginTop: 18 }}><option value="asc">{tr("升序")}</option><option value="desc">{tr("降序")}</option></select>
                    <label className="check small" style={{ marginTop: 18 }}><input type="checkbox" checked={sortNum} onChange={(e) => setSortNum(e.target.checked)} />{tr("按数值")}</label>
                  </>}
                </div>
                <label className="field">{tr("多文件时")}<select className="input" value={scope} onChange={(e) => setScope(e.target.value as any)}>
                    <option value="file">{tr("每个文件内部单独分组（默认，不同文件的相同编号不会合并）")}</option>
                    <option value="cross_file">{tr("跨文件按编号合并分组（会展示合并结果与冲突）")}</option>
                  </select>
                </label>
                <div className="tiny muted">{tr("组内同一字段出现不同值时会报冲突；可在映射中选择取值规则。")}</div>
              </div>
            )}
          </div>
          <div className="col">
            <h3>{tr("识别结果预览")}</h3>
            {preview && (
              <>
                <div className="grid3">
                  <div className="statbox"><div className="v">{formatNumber(preview.total)}</div><div className="k">{tr("识别样本")}</div></div>
                  <div className="statbox"><div className="v">{formatNumber(preview.excluded.blank)}</div><div className="k">{tr("排除空白行")}</div></div>
                  <div className="statbox"><div className="v">{formatNumber(preview.excluded.filtered + (preview.excluded.empty_key || 0))}</div><div className="k">{tr("过滤 / 分组值为空")}</div></div>
                </div>
                {preview.file_errors.map((e: any, i: number) => <div key={i} className="notice err small">{issueText(e)}</div>)}
                {preview.warnings.map((w: string, i: number) => <div key={i} className="notice warn small">{diagnosticText(w)}</div>)}
                <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <table className="tbl small">
                    <thead><tr><th>#</th><th>{tr("样本")}</th><th>{tr("位置")}</th></tr></thead>
                    <tbody>{preview.records.map((r: any) => <tr key={r.key}><td>{formatNumber(r.index + 1)}</td><td>{r.label}{r.merged_from && <div className="tiny muted">{tr("合并自：")}{r.merged_from.join("、")}</div>}</td><td className="muted">{serverText(r.where)}</td></tr>)}</tbody>
                  </table>
                </div>
                {preview.total > preview.records.length && <div className="tiny muted">{tr("仅显示前 ")}{formatNumber(preview.records.length)}{tr(" 条，完整清单在「样本」面板中查看。")}</div>}
              </>
            )}
          </div>
        </div>
      )}
      {mode === "per_file" && (
        <div className="mt12 col">
          <div className="notice">{tr("将为 ")}{sources.filter((s) => s.status === "ready" || s.status === "needs_ocr").length}{tr(" 个可用来源文件各生成一份输出。文件内不同位置的数据、多个明细区域可以共同填入同一份文档。")}</div>
          {preview?.file_errors?.map((e: any, i: number) => <div key={i} className="notice err small">{issueText(e)}</div>)}
        </div>
      )}
    </Modal>
  );
}
