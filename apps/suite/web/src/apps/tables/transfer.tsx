import { useState } from "react";
import { upload } from "@synwevo/auth-client";
import { useDialog, useI18n } from "@synwevo/ui";
import { Loading, Empty, RunBadge } from "../../shared/states";
import { useRead, useMutation, TableFailure, issueText } from "./common";
import type { Workspace, Table, RecordRow } from "./types";

const actionKeys: Record<string, string> = {created: "已创建", updated: "已更新", archived: "已归档", restored: "已恢复",
  migrated: "已转换", field_saved: "已保存字段", field_archived: "已删除字段", field_restored: "已恢复字段", saved: "已保存视图", deleted: "已删除视图"};

export function History({ws, table, record, onRestore}: {ws: Workspace; table: Table; record?: RecordRow; onRestore?: () => void}) {
  const {t, locale} = useI18n();
  const dialog = useDialog();
  const [before, setBefore] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const mutation = useMutation(ws.id);
  const {data, error} = useRead<{changes: any[]; next_before: number | null}>("tables.history.list", {table_id: table.id, record_id: record?.id ?? null, before}, ws.id, tick);
  function value(v: any): string {
    if (v == null) return "—";
    if (typeof v === "boolean") return t(v ? "是" : "否");
    // Historical values may predate a field type change; format their own shape.
    if (typeof v === "object" && typeof v.amount === "string" && typeof v.currency === "string") return `${v.amount} ${v.currency}`;
    if (Array.isArray(v)) return v.map(value).join(", ");
    return typeof v === "object" ? JSON.stringify(v) : String(v);
  }
  return <section className="tables-history"><h2>{t("变更记录")}</h2>
    <TableFailure error={error || mutation.error} retry={() => setTick(n => n+1)} />
    {!data && !error && <Loading />}
    {data && !data.changes.length && <Empty title={t("还没有可见的变更")} />}
    <ol>{data?.changes.map(change => <li key={change.id}><header><strong>{t(actionKeys[change.action] ?? "数据变更")}</strong><span>{change.actor_name || t("未知操作者")}</span><time dateTime={new Date(change.at*1000).toISOString()}>{new Intl.DateTimeFormat(locale, {dateStyle: "medium", timeStyle: "short"}).format(change.at*1000)}</time>{change.rev && <span>{t("修订 {n}", {n: change.rev})}</span>}</header>
      {change.record_id && !record && <a href={`/tables/w/${ws.id}/t/${table.id}/r/${change.record_id}`}>{t("查看记录")}</a>}
      <dl>{Object.entries(change.diff ?? {}).map(([fid, diff]: [string, any]) => <div key={fid}><dt>{table.fields.find(f => f.id === fid)?.name ?? t("字段")}</dt><dd><del>{value(diff.before)}</del><span aria-hidden="true"> → </span><ins>{value(diff.after)}</ins></dd></div>)}</dl>
      {record && record.can_write && table.can_write && !record.deleted_at && Object.keys(change.diff ?? {}).length > 0 && <button className="btn sm" disabled={mutation.busy} onClick={async () => {if (await dialog.confirm(t("恢复变更前的值"), <><p>{table.name} · {t("记录 {n}", {n: record.seq})} · {t("修订 {n}", {n: change.rev})}</p><p>{t("按当前权限与约束恢复这次变更之前的值？")}</p></>, t("确定"), true)) void mutation.run("tables.record.restore_change", {table_id: table.id, record_id: record.id, rev: record.rev, change_id: change.id}).then(() => {setTick(n => n+1); onRestore?.();}).catch(() => {});}}>{t("恢复变更前的值")}</button>}
    </li>)}</ol>
    <div className="tables-pagination"><button className="btn sm" disabled={before === null} onClick={() => setBefore(null)}>{t("最新记录")}</button><button className="btn sm" disabled={!data?.next_before} onClick={() => setBefore(data?.next_before ?? null)}>{t("更早记录")}</button></div>
  </section>;
}

export function ImportPanel({ws, table}: {ws: Workspace; table: Table; reload?: () => void}) {
  const {t} = useI18n();
  const [stage, setStage] = useState<{id: string; columns: string[]; rows: string[][]; row_count: number}>();
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<number>();
  const [error, setError] = useState<unknown>();
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [atomic, setAtomic] = useState(true);
  const [preview, setPreview] = useState<any>();
  const [outcome, setOutcome] = useState<any>();
  const mutation = useMutation(ws.id);
  const writable = table.fields.filter(f => f.can_write);
  async function stageFile() {
    if (!file) return;
    setBusy(true); setError(undefined); setStage(undefined); setPreview(undefined); setOutcome(undefined); setProgress(0);
    try {
      const body = new FormData(); body.append("file", file);
      const result = await upload<any>(`/api/suite/tables/${table.id}/imports?workspace_id=${encodeURIComponent(ws.id)}`, body, setProgress);
      setStage(result); setMapping(Object.fromEntries(result.columns.map((name: string) => [name, writable.find(f => f.name === name)?.id ?? ""])));
    } catch(e) {setError(e);} finally {setBusy(false); setProgress(undefined);}
  }
  const args = {table_id: table.id, import_id: stage?.id, mapping: Object.fromEntries(Object.entries(mapping).filter(([, fid]) => fid)), atomic};
  return <section><h2>{t("导入数据")}</h2><p className="muted">{t("上传 UTF-8 CSV 或单工作表 XLSX，最多 2 MB、500 行、100 列。首行是列名，原始文本保留前导零。")}</p>
    <p className="muted">{t("导入文件仅本人可见，1 小时后过期。文件公式不会执行。")}</p>
    <form className="tables-toolbar" onSubmit={e => {e.preventDefault(); void stageFile();}}><label>{t("选择文件")}<input className="input" type="file" accept=".csv,.xlsx" disabled={busy || !table.can_write} onChange={e => setFile(e.target.files?.[0])} /></label><button className="btn primary" disabled={!file || busy || !table.can_write}>{busy ? t("上传中…") : t("上传并预览")}</button></form>
    {progress !== undefined && <progress aria-label={t("上传进度")} max={100} value={progress} />}
    <TableFailure error={error || mutation.error} />
    {stage && <><h3>{t("列映射")}</h3><p>{t("文件共有 {n} 行，以下展示前 20 行。", {n: stage.row_count})}</p>
      <div className="tables-grid-wrap" tabIndex={0} role="region" aria-label={t("导入预览")}><table className="tables-grid"><thead><tr>{stage.columns.map(col => <th key={col}><label>{col}<select className="input" aria-label={t("将 {name} 映射到字段", {name: col})} value={mapping[col] ?? ""} disabled={!!outcome} onChange={e => {setMapping({...mapping, [col]: e.target.value}); setPreview(undefined);}}><option value="">{t("跳过此列")}</option>{writable.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label></th>)}</tr></thead><tbody>{stage.rows.map((row, n) => <tr key={n}>{row.map((v, j) => <td key={j}>{v}</td>)}</tr>)}</tbody></table></div>
      <label className="tables-check"><input type="checkbox" checked={atomic} disabled={!!outcome} onChange={e => {setAtomic(e.target.checked); setPreview(undefined);}} />{t("原子导入：任一行失败则整批不写入")}</label>
      {!atomic && <p className="notice">{t("有效行会写入；失败行逐项报告。再次导入失败行时请上传修正后的文件。")}</p>}
      {!outcome && <button className="btn" disabled={mutation.busy || !Object.keys(args.mapping).length} onClick={() => void mutation.run("tables.import.apply", {...args, preview: true}).then(setPreview).catch(() => {})}>{t("验证映射")}</button>}
      {preview && !outcome && <section className="tables-preview"><h3>{t("导入检查")}</h3><p>{t("可写入 {ok} 行，需修复 {failed} 行", {ok: preview.valid, failed: preview.issues.length})}</p>{preview.issues.map((x: any) => <p key={x.index}>{t("文件第 {n} 行", {n: x.index+2})}: {issueText(t, x.error, x.detail)}</p>)}<button className="btn primary" disabled={mutation.busy || (atomic && preview.issues.length > 0) || !preview.valid} onClick={() => void mutation.run("tables.import.apply", {...args, preview: false}).then(setOutcome).catch(() => {})}>{t("确认导入")}</button></section>}
      {outcome && <section className="notice" role="status"><RunBadge status={outcome.failed ? "partial" : "succeeded"} /><p>{t("成功 {ok} 项，失败 {failed} 项", {ok: outcome.succeeded, failed: outcome.failed})}</p>{outcome.items.filter((x: any) => !x.ok).map((x: any) => <p key={x.index}>{t("文件第 {n} 行", {n: x.index+2})}: {issueText(t, x.error, x.detail)}</p>)}<a className="btn" href={`/tables/w/${ws.id}/t/${table.id}`}>{t("查看导入结果")}</a></section>}
    </>}
  </section>;
}
