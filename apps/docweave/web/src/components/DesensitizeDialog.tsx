import { useEffect, useMemo, useState } from "react";
import { get, post, qs } from "../lib/api";
import type { SourceFile } from "../lib/types";
import { Modal } from "../lib/ui";

const PATTERNS = [
  { key: "mobile", label: "手机号" }, { key: "idcard", label: "身份证号（保留出生日期）" }, { key: "bankcard", label: "银行卡号" },
  { key: "email", label: "电子邮箱" }, { key: "landline", label: "固定电话" },
];
const STRATEGIES = [
  { key: "keep", label: "不变" }, { key: "pseudonym", label: "替换为代号（同值同代号）" }, { key: "mask", label: "保留首尾，中间打码" },
  { key: "digits", label: "数字随机替换（保持长度）" }, { key: "scale", label: "数值按同一比例缩放" }, { key: "date_shift", label: "日期整体平移" }, { key: "clear", label: "清空" },
];
const SUPPORTED = ["xlsx", "xlsm", "csv", "tsv", "docx", "docm", "txt", "md"];

export default function DesensitizeDialog({ projectId, sources, onDone, onClose }: { projectId: string; sources: SourceFile[]; onDone: () => void; onClose: () => void }) {
  const usable = sources.filter((s) => s.status !== "parsing");
  const [ids, setIds] = useState<string[]>(usable.filter((s) => SUPPORTED.includes(s.format)).map((s) => s.id));
  const [patterns, setPatterns] = useState<string[]>(PATTERNS.map((p) => p.key));
  const [words, setWords] = useState("");
  const [cols, setCols] = useState<Record<string, string>>({});
  const [headers, setHeaders] = useState<string[]>([]);
  const [addToProject, setAdd] = useState(true);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const table = usable.find((s) => ids.includes(s.id) && s.family === "table");

  useEffect(() => {
    if (!table) { setHeaders([]); return; }
    get(`/api/sources/${table.id}/view${qs({ limit: 1 })}`).then((v) => setHeaders((v.grid?.rows?.[0] || []).map((c: any) => String(c.v ?? "")).filter(Boolean))).catch(() => setHeaders([]));
  }, [table?.id]);

  const wordList = useMemo(() => words.split(/[\n,，、;；]/).map((w) => w.trim()).filter(Boolean), [words]);
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post(`/api/projects/${projectId}/desensitize`, { file_ids: ids, patterns, words: wordList, columns: Object.fromEntries(Object.entries(cols).filter(([, v]) => v !== "keep")), add_to_project: addToProject });
      setRes(r);
      if (addToProject) onDone();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="生成脱敏副本" size="wide" onClose={onClose}
      footer={res ? <><span className="spacer" /><button className="btn primary" onClick={onClose}>完成</button></>
        : <>{err && <div className="notice err grow">{err}</div>}<span className="spacer" /><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={busy || !ids.length} onClick={run}>{busy ? "正在处理…" : `生成 ${ids.length} 个脱敏副本`}</button></>}>
      {res ? (
        <div className="col gap8">
          <div className="notice ok">已生成 {res.files.length} 个脱敏副本{addToProject ? "，并已加入来源文件列表" : ""}。同一个原值在所有文件中替换为同一个代号（本次共 {res.mapping_size} 个）。</div>
          <table className="tbl small">
            <thead><tr><th>原文件</th><th>脱敏副本</th><th /></tr></thead>
            <tbody>{res.files.map((f: any) => (
              <tr key={f.blob}><td>{f.source}</td><td>{f.filename}</td><td><a className="btn xs" href={`/api/desensitized/${f.blob}${qs({ name: f.filename })}`}>下载</a></td></tr>
            ))}</tbody>
          </table>
          {res.skipped.map((s: any, i: number) => <div key={i} className="notice warn small">「{s.file}」未处理：{s.reason}</div>)}
          <div className="small">替换统计：{Object.entries(res.counts).map(([k, v]) => <span key={k} className="badge" style={{ marginRight: 4 }}>{k} × {v as number}</span>)}</div>
          <div className="tiny muted">替换对照表不会保存；请打开副本确认没有遗漏后再用于回归用例或发送给他人。</div>
        </div>
      ) : (
        <div className="grid2" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="col gap8">
            <h3>要处理的文件</h3>
            {usable.map((s) => {
              const ok = SUPPORTED.includes(s.format);
              return (
                <label key={s.id} className={`check${ok ? "" : " muted"}`} title={ok ? "" : "PDF / 图片 / 幻灯片中的文字无法在不破坏版式的前提下可靠替换"}>
                  <input type="checkbox" disabled={!ok} checked={ids.includes(s.id)} onChange={(e) => setIds(e.target.checked ? [...ids, s.id] : ids.filter((x) => x !== s.id))} />
                  {s.filename} {!ok && <span className="badge">不支持</span>}
                </label>
              );
            })}
            <h3 className="mt8">自动识别并替换</h3>
            <div className="row wrap gap8">{PATTERNS.map((p) => (
              <label key={p.key} className="check badge" style={{ height: 26 }}>
                <input type="checkbox" checked={patterns.includes(p.key)} onChange={(e) => setPatterns(e.target.checked ? [...patterns, p.key] : patterns.filter((x) => x !== p.key))} />{p.label}
              </label>
            ))}</div>
            <label className="field">另外要替换的词（人名、单位名等，每行一个或用逗号分隔）
              <textarea className="input" rows={4} value={words} onChange={(e) => setWords(e.target.value)} placeholder={"张三\n某某科技有限公司"} />
            </label>
            <label className="check"><input type="checkbox" checked={addToProject} onChange={(e) => setAdd(e.target.checked)} /> 把脱敏副本加入当前项目的来源文件</label>
          </div>
          <div className="col gap8">
            <h3>表格列的处理方式 {table && <span className="small muted">（按「{table.filename}」的表头）</span>}</h3>
            {!table && <div className="small muted">选择了表格文件（XLSX / CSV）时，可以逐列设置处理方式；其余单元格按左侧规则替换。</div>}
            {headers.map((h) => (
              <div key={h} className="row gap8">
                <span className="grow ellipsis small">{h}</span>
                <select className="input sm" value={cols[h] || "keep"} onChange={(e) => setCols({ ...cols, [h]: e.target.value })}>
                  {STRATEGIES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                </select>
              </div>
            ))}
            <div className="notice small">所有处理都在本服务器完成，文件不会发送到外部服务。数值缩放与日期平移对整列使用同一比例/天数，便于保留合计、排序等关系。</div>
          </div>
        </div>
      )}
    </Modal>
  );
}
