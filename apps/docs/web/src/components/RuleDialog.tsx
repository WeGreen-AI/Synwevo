import { useEffect, useMemo, useRef, useState } from "react";
import { get, post, qs } from "@synwevo/auth-client";
import { useLatest } from "../lib/latest";
import type { Project, RecordRule, SourceFile } from "../lib/types";
import { Modal } from "@synwevo/ui";

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
  const rule0: RecordRule = { ...(project.record_rule || {}), ...(initial || {}) } as RecordRule;
  const [mode, setMode] = useState<RecordRule["mode"]>(rule0.mode || "per_row");
  const rep = sources.find((s) => s.representative && s.status === "ready") || sources.find((s) => s.status === "ready");
  const tabular = sources.filter((s) => s.family === "table" || (s.family === "text" && s.structure?.text?.tables));
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
  const timer = useRef<any>(null);
  const latest = useLatest();

  // header names of the chosen header row (from the representative file)
  useEffect(() => {
    if (!rep || rep.family !== "table" && !(rep.family === "text")) return;
    if (noHeader) {
      setHeaders([]);
      return;
    }
    get(`/api/sources/${rep.id}/view${qs({ sheet, offset: Math.max(0, headerRow - 1), limit: 1 })}`)
      .then((v) => setHeaders((v.grid?.rows?.[0] || []).map((c: any) => c.v).filter((x: string) => x !== undefined)))
      .catch(() => setHeaders([]));
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

  useEffect(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (mode === "per_block" && !anchor.trim()) {
        setPreview(null);
        setPerr("请填写每个表单块开头的标记文字");
        return;
      }
      if (mode === "per_group" && !groupKeys.filter(Boolean).length) {
        setPreview(null);
        setPerr("请选择分组字段");
        return;
      }
      latest(
        (signal) => post(`/api/projects/${project.id}/rule-preview`, rule, signal),
        (x) => { setPreview(x); setPerr(null); },
        (e) => { setPreview(null); setPerr(e.message); },
      );
    }, 250);
    return () => clearTimeout(timer.current);
  }, [JSON.stringify(rule)]);

  const save = async () => {
    setBusy(true);
    try {
      await onSave(rule);
      onClose();
    } catch (e: any) {
      setPerr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const noTable = (mode === "per_row" || mode === "per_group") && !tabular.length;

  return (
    <Modal title="定义样本：每份输出对应什么数据？" size="wide" onClose={onClose}
      footer={<>
        {preview && <span className="small">将识别 <b>{preview.total}</b> 条样本 → 默认生成 <b>{preview.total}</b> 份文档</span>}
        <span className="spacer" />
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn primary" disabled={busy || noTable || !!perr} onClick={save}>确认样本规则</button>
      </>}>
      <div className="grid4">
        {MODES.map((m) => (
          <label key={m.key} className={`opt${mode === m.key ? " on" : ""}`} style={{ flexDirection: "column", gap: 4 }}>
            <div className="row"><input type="radio" checked={mode === m.key} onChange={() => setMode(m.key as any)} /><span className="t">{m.title}</span></div>
            <div className="small">{m.body}</div>
            <div className="small muted">{m.ex}</div>
          </label>
        ))}
      </div>
      <div className="notice info mt12 small">
        样本划分只决定「哪些数据构成一份输出」，与映射数量无关；多个来源文件默认各自独立提取样本后按文件顺序追加，不会做组合。
      </div>
      {noTable && <div className="notice warn mt12">「{mode === "per_row" ? "每行一份" : "每组一份"}」需要表格类来源（XLSX/CSV/XLS/ODS 或含表格的 HTML）。当前没有可用的表格文件。</div>}
      {mode === "per_block" && (
        <div className="grid2 mt12" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="col">
            <h3>如何切分表单块</h3>
            <label className="field">每块开头的标记文字
              <div className="row">
                <input className="input grow" value={anchor} onChange={(e) => setAnchor(e.target.value)} placeholder="例如：人员登记表、姓名：、检测报告编号" autoFocus />
                {selectedText && selectedText.trim() && selectedText.trim() !== anchor && (
                  <button className="btn sm" onClick={() => setAnchor(selectedText.trim().slice(0, 60))} title="使用左侧当前选中的文字">用选中文字「{selectedText.trim().slice(0, 10)}」</button>
                )}
              </div>
            </label>
            <div className="grid2">
              <label className="field">匹配方式
                <select className="input" value={match} onChange={(e) => setMatch(e.target.value)}>
                  <option value="contains">所在行 / 段落包含该文字</option>
                  <option value="starts">所在行 / 段落以该文字开头</option>
                  <option value="exact">所在行 / 段落正好是该文字</option>
                  <option value="regex">正则表达式</option>
                </select>
              </label>
              <label className="field">结束标记（可选）
                <input className="input" value={endAnchor} onChange={(e) => setEndAnchor(e.target.value)} placeholder="留空 = 到下一个开头标记" />
              </label>
            </div>
            {tabular.length > 0 && sheets.length > 1 && (
              <label className="field">表格文件使用的工作表
                <select className="input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
                  {sheets.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </label>
            )}
            <div className="notice small">
              每个标记出现的位置开始一个新块，直到下一个标记（或结束标记）为止。表格按行切分，Word 按段落/表格切分，PDF 按标记所在行切分（同一页可有多块，也可跨页），文本按行切分。
              建立映射时，在左侧用块导航切换到某一块并选取数据；每一块都按相对于块开头的同一位置、并以旁边的标签校验来取值。
            </div>
          </div>
          <div className="col">
            <h3>识别结果预览</h3>
            {perr && <div className="notice err">{perr}</div>}
            {preview && (
              <>
                <div className="grid3">
                  <div className="statbox"><div className="v">{preview.total}</div><div className="k">识别表单块</div></div>
                  <div className="statbox"><div className="v">{Object.keys(preview.blocks || {}).length}</div><div className="k">包含表单块的文件</div></div>
                  <div className="statbox"><div className="v">{preview.file_errors.length}</div><div className="k">未找到标记的文件</div></div>
                </div>
                {preview.file_errors.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
                <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <table className="tbl small">
                    <thead><tr><th>#</th><th>表单块</th><th>位置</th></tr></thead>
                    <tbody>{preview.records.map((r: any) => <tr key={r.key}><td>{r.index + 1}</td><td>{r.label}</td><td className="muted">{r.where}</td></tr>)}</tbody>
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
            <h3>数据范围 <span className="small muted">（以代表文件「{rep?.filename}」为准）</span></h3>
            <div className="grid2">
              <label className="field">工作表
                <select className="input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
                  {sheets.map((s) => <option key={s.name} value={s.name}>{s.name}（{s.rows} 行）</option>)}
                </select>
              </label>
              <label className="field">表头行（第几行）
                <div className="row">
                  <input className="input" type="number" min={1} value={headerRow} disabled={noHeader} onChange={(e) => setHeaderRow(Math.max(1, Number(e.target.value)))} style={{ width: 80 }} />
                  <label className="check small"><input type="checkbox" checked={noHeader} onChange={(e) => setNoHeader(e.target.checked)} />无表头</label>
                </div>
              </label>
              <label className="field">数据起始行（留空 = 表头下一行）<input className="input" type="number" min={1} value={dataStart} onChange={(e) => setDataStart(e.target.value)} placeholder={String(headerRow + 1)} /></label>
              <label className="field">数据结束行（留空 = 到最后一行）<input className="input" type="number" min={1} value={dataEnd} onChange={(e) => setDataEnd(e.target.value)} placeholder="末尾" /></label>
            </div>
            {!noHeader && headers.length > 0 && <div className="small muted">表头：{headers.slice(0, 12).map((h, i) => <span key={i} className="badge" style={{ margin: "2px 4px 0 0" }}>{h || "（空）"}</span>)}</div>}
            <label className="check"><input type="checkbox" checked={skipBlank} onChange={(e) => setSkipBlank(e.target.checked)} /> 排除完全空白的行（排除数量会显示）</label>
            <div className="col gap4">
              <div className="small">过滤条件（可选）</div>
              {filters.map((f, i) => (
                <div key={i} className="row">
                  <select className="input sm" value={f.column} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, column: e.target.value } : x)))}>
                    <option value="">选择列</option>{headers.map((h, k) => <option key={k} value={h}>{h}</option>)}
                  </select>
                  <select className="input sm" value={f.op} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, op: e.target.value } : x)))}>
                    <option value="nonempty">不为空</option><option value="empty">为空</option><option value="eq">等于</option><option value="neq">不等于</option><option value="contains">包含</option><option value="not_contains">不包含</option>
                  </select>
                  {["eq", "neq", "contains", "not_contains"].includes(f.op) && <input className="input sm" value={f.value || ""} onChange={(e) => setFilters(filters.map((x, k) => (k === i ? { ...x, value: e.target.value } : x)))} />}
                  <button className="btn xs ghost" onClick={() => setFilters(filters.filter((_, k) => k !== i))}>✕</button>
                </div>
              ))}
              <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => setFilters([...filters, { column: "", op: "nonempty" }])}>＋ 添加过滤</button>
            </div>
            {mode === "per_row" && (
              <label className="field">样本标识列（用于查找和命名，可选）
                <select className="input" value={idCol} onChange={(e) => setIdCol(e.target.value)}>
                  <option value="">（不设置）</option>{headers.map((h, i) => <option key={i} value={h}>{h}</option>)}
                </select>
              </label>
            )}
            {mode === "per_group" && (
              <div className="col">
                <h3>分组</h3>
                <label className="field">分组字段（可多选，按组合值分组）
                  <div className="row wrap gap4">
                    {headers.map((h, i) => (
                      <label key={i} className="check badge" style={{ height: 24 }}>
                        <input type="checkbox" checked={groupKeys.includes(h)} onChange={(e) => setGroupKeys(e.target.checked ? [...groupKeys, h] : groupKeys.filter((x) => x !== h))} />{h}
                      </label>
                    ))}
                  </div>
                </label>
                <div className="row wrap">
                  <label className="field grow">组内排序
                    <select className="input" value={sortCol} onChange={(e) => setSortCol(e.target.value)}>
                      <option value="">保持原始顺序</option>{headers.map((h, i) => <option key={i} value={h}>{h}</option>)}
                    </select>
                  </label>
                  {sortCol && <>
                    <select className="input" value={sortDir} onChange={(e) => setSortDir(e.target.value)} style={{ marginTop: 18 }}><option value="asc">升序</option><option value="desc">降序</option></select>
                    <label className="check small" style={{ marginTop: 18 }}><input type="checkbox" checked={sortNum} onChange={(e) => setSortNum(e.target.checked)} />按数值</label>
                  </>}
                </div>
                <label className="field">多文件时
                  <select className="input" value={scope} onChange={(e) => setScope(e.target.value as any)}>
                    <option value="file">每个文件内部单独分组（默认，不同文件的相同编号不会合并）</option>
                    <option value="cross_file">跨文件按编号合并分组（会展示合并结果与冲突）</option>
                  </select>
                </label>
                <div className="tiny muted">同一组内的单值字段若出现多个不同值，会报冲突，需在映射中明确选择取值规则。</div>
              </div>
            )}
          </div>
          <div className="col">
            <h3>识别结果预览</h3>
            {perr && <div className="notice err">{perr}</div>}
            {preview && (
              <>
                <div className="grid3">
                  <div className="statbox"><div className="v">{preview.total}</div><div className="k">识别样本</div></div>
                  <div className="statbox"><div className="v">{preview.excluded.blank}</div><div className="k">排除空白行</div></div>
                  <div className="statbox"><div className="v">{preview.excluded.filtered + (preview.excluded.empty_key || 0)}</div><div className="k">过滤 / 分组值为空</div></div>
                </div>
                {preview.file_errors.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
                {preview.warnings.map((w: string, i: number) => <div key={i} className="notice warn small">{w}</div>)}
                <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <table className="tbl small">
                    <thead><tr><th>#</th><th>样本</th><th>位置</th></tr></thead>
                    <tbody>{preview.records.map((r: any) => <tr key={r.key}><td>{r.index + 1}</td><td>{r.label}{r.merged_from && <div className="tiny muted">合并自：{r.merged_from.join("、")}</div>}</td><td className="muted">{r.where}</td></tr>)}</tbody>
                  </table>
                </div>
                {preview.total > preview.records.length && <div className="tiny muted">仅显示前 {preview.records.length} 条，完整清单在「样本」面板中查看。</div>}
              </>
            )}
          </div>
        </div>
      )}
      {mode === "per_file" && (
        <div className="mt12 col">
          <div className="notice">将为 {sources.filter((s) => s.status === "ready" || s.status === "needs_ocr").length} 个可用来源文件各生成一份输出。文件内不同位置的数据、多个明细区域可以共同填入同一份文档。</div>
          {preview?.file_errors?.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
        </div>
      )}
    </Modal>
  );
}
