import { formatNumber } from "@synwevo/ui";
import { DownloadLink } from "./ApiResource";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useMemo, useState } from "react";
import { get, post, qs } from "@synwevo/auth-client";
import type { SourceFile } from "../lib/types";
import { Modal } from "@synwevo/ui";
import { useSession } from "@synwevo/auth-client";
import { errorText, NO_EXPORT_TEXT, permissions } from "../lib/perm";

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
  useI18n();
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
  const session = useSession();
  const perm = permissions(session.me, session.disabled);

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
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={tr("生成脱敏副本")} size="wide" onClose={onClose}
      footer={res ? <><span className="spacer" /><button type="button" className="btn primary" onClick={onClose}>{tr("完成")}</button></>
        : <>{err && <div className="notice err grow" role="alert">{serverText(err)}</div>}<span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("取消")}</button><button type="button" className="btn primary" disabled={busy || !ids.length} onClick={run}>{busy ? tr("正在处理…") : tr("生成 {v1} 个脱敏副本", { v1: ids.length })}</button></>}>
      {res ? (
        <div className="col gap8">
          <div className="notice ok">{tr("已生成 ")}{formatNumber(res.files.length)}{tr(" 个脱敏副本")}{addToProject ? tr("，并已加入来源文件列表") : ""}{tr("。同一个原值在所有文件中替换为同一个代号（本次共 ")}{formatNumber(res.mapping_size)}{tr(" 个）。")}</div>
          <table className="tbl small">
            <thead><tr><th>{tr("原文件")}</th><th>{tr("脱敏副本")}</th><th /></tr></thead>
            <tbody>{res.files.map((f: any) => (
              <tr key={f.blob}><td>{f.source}</td><td>{f.filename}</td><td>{perm.canExport
                ? <DownloadLink className="btn xs" href={`/api/desensitized/${f.blob}${qs({ name: f.filename })}`}>{tr("下载")}</DownloadLink>
                : <span className="tiny muted" title={tr(NO_EXPORT_TEXT)}>{tr("无下载权限")}</span>}</td></tr>
            ))}</tbody>
          </table>
          {res.skipped.map((s: any, i: number) => <div key={i} className="notice warn small">「{s.file}{tr("」未处理：")}{diagnosticText(s.reason)}</div>)}
          <div className="small">{tr("替换统计：")}{Object.entries(res.counts).map(([k, v]) => <span key={k} className="badge" style={{ marginRight: 4 }}>{k} × {v as number}</span>)}</div>
          <div className="tiny muted">{tr("替换对照表不会保存；请打开副本确认没有遗漏后再用于回归用例或发送给他人。")}</div>
        </div>
      ) : (
        <div className="grid2" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="col gap8">
            <h3>{tr("要处理的文件")}</h3>
            {usable.map((s) => {
              const ok = SUPPORTED.includes(s.format);
              return (
                <label key={s.id} className={`check${ok ? "" : " muted"}`} title={ok ? "" : tr("PDF / 图片 / 幻灯片中的文字无法在不破坏版式的前提下可靠替换")}>
                  <input type="checkbox" disabled={!ok} checked={ids.includes(s.id)} onChange={(e) => setIds(e.target.checked ? [...ids, s.id] : ids.filter((x) => x !== s.id))} />
                  {s.filename} {!ok && <span className="badge">{tr("不支持")}</span>}
                </label>
              );
            })}
            <h3 className="mt8">{tr("自动识别并替换")}</h3>
            <div className="row wrap gap8">{PATTERNS.map((p) => (
              <label key={p.key} className="check badge" style={{ height: 26 }}>
                <input type="checkbox" checked={patterns.includes(p.key)} onChange={(e) => setPatterns(e.target.checked ? [...patterns, p.key] : patterns.filter((x) => x !== p.key))} />{tr(p.label)}
              </label>
            ))}</div>
            <label className="field">{tr("另外要替换的词（人名、单位名等，每行一个或用逗号分隔）")}<textarea className="input" rows={4} value={words} onChange={(e) => setWords(e.target.value)} placeholder={tr("张三\n某某科技有限公司")} />
            </label>
            <label className="check"><input type="checkbox" checked={addToProject} onChange={(e) => setAdd(e.target.checked)} />{tr(" 把脱敏副本加入当前项目的来源文件")}</label>
          </div>
          <div className="col gap8">
            <h3>{tr("表格列的处理方式 ")}{table && <span className="small muted">{tr("（按「")}{table.filename}{tr("」的表头）")}</span>}</h3>
            {!table && <div className="small muted">{tr("选择了表格文件（XLSX / CSV）时，可以逐列设置处理方式；其余单元格按左侧规则替换。")}</div>}
            {headers.map((h) => (
              <div key={h} className="row gap8">
                <span className="grow ellipsis small">{h}</span>
                <select className="input sm" value={cols[h] || "keep"} onChange={(e) => setCols({ ...cols, [h]: e.target.value })}>
                  {STRATEGIES.map((s) => <option key={s.key} value={s.key}>{tr(s.label)}</option>)}
                </select>
              </div>
            ))}
            <div className="notice small">{tr("所有处理都在本服务器完成，文件不发送到外部服务。数值缩放与日期平移对整列使用同一比例或天数，保留合计与排序关系。")}</div>
          </div>
        </div>
      )}
    </Modal>
  );
}
