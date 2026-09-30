import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { upload } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { newKey } from "../../shared/ops";
import { Empty, Loading, RunBadge } from "../../shared/states";
import { useRead, useAction, ErrorState, Grid, IssueList, Field, Pager, when, errorKeys } from "./common";
import { stepNames, type Definition, type WS, type Dataset, type DataRow, type Recipe, type Run, type Page, type Issue } from "./model";

export function Library({ ws, write, kind }: { ws: WS; write: boolean; kind: "dataset" | "recipe" | "run" }) {
  const { t } = useI18n(), navigate = useNavigate();
  const [offset, setOffset] = useState(0), [name, setName] = useState("");
  const result = useRead<Page<Dataset & Recipe & Run>>(`dataprep.${kind}.list`, { offset, limit: 50 }, ws.id, kind === "run");
  const action = useAction(ws.id), base = `/dataprep/w/${ws.id}`;
  const title = { dataset: "数据集", recipe: "处理方案", run: "运行历史" }[kind];
  return <><div className="suite-head"><div><h1>{t(title)}</h1><p className="lede">{t("从原始数据到可追溯结果，每次处理固定来源与方案版本。")}</p></div>
    {write && kind === "dataset" && <Link className="btn primary" to={`${base}/import`}>{t("导入数据")}</Link>}</div>
    {kind === "recipe" && write && <form className="dp-actions dp-panel" onSubmit={e => { e.preventDefault(); void action.run<Recipe>("recipe.create", { name }, r => navigate(`${base}/recipes/${r.id}`)); }}>
      <Field label={t("方案名称")}><input className="input" value={name} required maxLength={120} onChange={e => setName(e.target.value)} /></Field><button className="btn primary" disabled={action.busy}>{t("新建方案")}</button></form>}
    {action.error && <ErrorState error={action.error} />}
    {result.error ? <ErrorState error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : !result.data.items.length ? <Empty title={t("这里还没有记录")} /> : <>
      <div className="suite-list">{result.data.items.map(item => <Link className="suite-row" key={item.id} to={`${base}/${kind === "dataset" ? "datasets" : kind === "recipe" ? "recipes" : "runs"}/${item.id}`}>
        <div className="grow"><div className="title">{item.name || item.output_name}</div><div className="sub">{when(item.created_at)}</div></div>
        {kind === "dataset" ? <span>{t("{n} 行 · {c} 列", { n: item.row_count, c: item.column_count })}</span> : kind === "run" ? <RunBadge status={item.status} /> : <span className="badge">{item.status === "archived" ? t("已归档") : item.published_version ? t("版本 {v}", { v: item.published_version }) : t("草稿")}</span>}
      </Link>)}</div><Pager offset={offset} next={result.data.next_offset} set={setOffset} /></>}
  </>;
}

export function ImportPage({ ws, write }: { ws: WS; write: boolean }) {
  const { t } = useI18n(), navigate = useNavigate(), action = useAction(ws.id);
  const [file, setFile] = useState<File | null>(null), [uploaded, setUploaded] = useState<any>(null), [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0), [error, setError] = useState<unknown>(null), [name, setName] = useState("");
  const [mode, setMode] = useState("file"), [ids, setIds] = useState("");
  const [options, setOptions] = useState<any>({ encoding: "utf-8-sig", delimiter: ",", sheet: null, start_row: 1, end_row: null, start_column: 1, end_column: null, null_tokens: ["\\N"], formulas: "reject" });
  const key = useRef(newKey());
  const set = (k: string, value: unknown) => setOptions((o: any) => ({ ...o, [k]: value }));
  async function send() {
    if (!file || busy) return;
    setBusy(true); setError(null); setProgress(0);
    const form = new FormData(); form.append("file", file); form.append("upload_key", key.current);
    try { const data = await upload(`/api/suite/dataprep/files?workspace_id=${encodeURIComponent(ws.id)}`, form, setProgress); setUploaded(data); setName(file.name); if (data.sheets?.length) set("sheet", data.sheets[0]); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  const finish = (d: Dataset) => navigate(`/dataprep/w/${ws.id}/datasets/${d.id}`);
  return <><div className="suite-head"><div><h1>{t("导入数据")}</h1><p className="lede">{t("文件最多 8 MB；每次处理最多 50,000 行、64 列、500,000 个单元格。")}</p></div></div>
    <p className="notice">{t("数据默认按文本保留。首行作为列名；空字符串、空值和缺失分别记录。XLSX 公式默认拒绝，宏和外部链接不接受。")}</p>
    <Field label={t("导入来源")}><select className="input" value={mode} onChange={e => setMode(e.target.value)}><option value="file">CSV / XLSX</option><option value="forms">{t("表单提交")}</option></select></Field>
    <fieldset className="dp-panel" disabled={!write || action.busy || busy}>
      {mode === "file" ? <><Field label={t("选择文件")}><input type="file" accept=".csv,.xlsx" onChange={e => { setFile(e.target.files?.[0] || null); setUploaded(null); key.current = newKey(); }} /></Field>
        <button type="button" className="btn" disabled={!file} onClick={() => void send()}>{t("上传并检查工作表")}</button>
        {uploaded && <><p className="notice">{uploaded.scan_status === "clean" ? t("文件检查通过") : t("未配置文件扫描服务；未声明扫描通过")}</p><div className="dp-fields">
          <Field label={t("数据集名称")}><input className="input" required value={name} maxLength={120} onChange={e => setName(e.target.value)} /></Field>
          {uploaded.format === "xlsx" ? <><Field label={t("工作表")}><select className="input" value={options.sheet || ""} onChange={e => set("sheet", e.target.value)}>{uploaded.sheets.map((s: string) => <option key={s}>{s}</option>)}</select></Field>
            <Field label={t("公式处理")}><select className="input" value={options.formulas} onChange={e => set("formulas", e.target.value)}><option value="reject">{t("拒绝公式")}</option><option value="cached">{t("使用缓存值（不重新计算）")}</option></select></Field></> : <>
            <Field label={t("文件编码")}><select className="input" value={options.encoding} onChange={e => set("encoding", e.target.value)}>{["utf-8-sig", "utf-16", "gb18030"].map(s => <option key={s}>{s}</option>)}</select></Field>
            <Field label={t("分隔符")}><select className="input" value={options.delimiter} onChange={e => set("delimiter", e.target.value)}>{[",", ";", "\t", "|"].map(s => <option key={s} value={s}>{s === "\t" ? t("制表符") : s}</option>)}</select></Field></>}
          {[["start_row", "标题行"], ["end_row", "结束行（包含）"], ["start_column", "起始列编号"], ["end_column", "结束列编号（包含）"]].map(([key, label]) => <Field key={key} label={t(label)}><input className="input" type="number" min={1} max={key.endsWith("row") ? 50001 : 16384} value={options[key] ?? ""} onChange={e => set(key, e.target.value ? Number(e.target.value) : null)} /></Field>)}
          <Field label={t("空值标记（每行一个）")}><textarea className="input" value={options.null_tokens.join("\n")} onChange={e => set("null_tokens", e.target.value ? e.target.value.split("\n") : [])} /></Field>
        </div><p className="muted">{t("结束范围留空表示到最后一行或列；日期单元格转换为本地 ISO 日期时间，毫秒精度。数值格式不自动补前导零。")}</p>
        <button className="btn primary" disabled={!name.trim()} onClick={() => void action.run("dataset.import", { file_id: uploaded.id, name, options }, finish)}>{t("导入完整范围")}</button></>}
      </> : <><Field label={t("数据集名称")}><input className="input" value={name} onChange={e => setName(e.target.value)} /></Field>
        <Field label={t("提交编号（每行一个，最多 100 个）")}><textarea className="input" rows={6} value={ids} onChange={e => setIds(e.target.value)} /></Field>
        <p className="muted">{t("仅导入当前空间中你有权读取的提交；创建独立数据快照，保留字段编号。")}</p>
        <button className="btn primary" disabled={!name.trim() || !ids.trim()} onClick={() => void action.run("dataset.from_forms", { name, submission_ids: ids.split("\n").map(s => s.trim()).filter(Boolean) }, finish)}>{t("导入表单提交")}</button></>}
    </fieldset>
    {busy && <div role="status"><progress max={1} value={progress} />{t("正在上传与读取…")}</div>}
    {(error || action.error) && <ErrorState error={error || action.error} />}{action.busy && <Loading />}
  </>;
}

export function DatasetPage({ ws }: { ws: WS }) {
  const { t } = useI18n(), { id = "" } = useParams();
  const [offset, setOffset] = useState(0), [issueOffset, setIssueOffset] = useState(0);
  const result = useRead<Dataset & { rows: DataRow[]; next_offset: number | null }>("dataprep.dataset.get", { dataset_id: id, offset }, ws.id);
  const issues = useRead<Page<Issue>>("dataprep.dataset.issues", { dataset_id: id, offset: issueOffset }, ws.id);
  if (result.error) return <ErrorState error={result.error} retry={result.reload} />;
  if (!result.data) return <Loading />;
  const data = result.data;
  return <><div className="suite-head"><div><h1>{data.name}</h1><p className="lede">{t("{n} 行 · {c} 列", { n: data.row_count, c: data.column_count })}</p></div></div>
    <div className="dp-actions">{["csv", "xlsx", "json"].map(format => <a className="btn" key={format} href={`/api/suite/dataprep/datasets/${id}/export?workspace_id=${encodeURIComponent(ws.id)}&format=${format}`}>{t("导出 {format}", { format: format.toUpperCase() })}</a>)}</div>
    <p className="muted">{t("CSV 防止公式注入；XLSX 单元格按文本导出以保留精度。JSON 保留空值、缺失和完整来源。下载仍受工作空间导出权限限制。")}</p>
    {data.read_notes?.includes("formula_cache_not_recalculated") && <p className="notice">{t("使用了文件缓存公式值，可能不是最新计算结果。")}</p>}
    <Grid columns={data.columns} rows={data.rows} caption={t("数据预览")} /><Pager offset={offset} next={data.next_offset} set={setOffset} />
    <h2>{t("导入异常")}{issues.data?.total ? ` (${issues.data.total})` : ""}</h2>
    {issues.error ? <ErrorState error={issues.error} retry={issues.reload} /> : !issues.data ? <Loading /> : <><IssueList items={issues.data.items} /><Pager offset={issueOffset} next={issues.data.next_offset} set={setIssueOffset} /></>}
  </>;
}

export function RunPage({ ws, write }: { ws: WS; write: boolean }) {
  const { t } = useI18n(), { id = "" } = useParams(), navigate = useNavigate();
  const action = useAction(ws.id), [offset, setOffset] = useState(0), [poll, setPoll] = useState(true);
  const result = useRead<Run>("dataprep.run.get", { run_id: id }, ws.id, poll);
  useEffect(() => { setPoll(true); }, [id]);
  useEffect(() => { if (result.data && !["queued", "running"].includes(result.data.status)) setPoll(false); }, [result.data]);
  const issues = useRead<Page<Issue>>("dataprep.run.issues", { run_id: id, offset }, ws.id, poll);
  const version = useRead<{ definition: Definition }>("dataprep.recipe.version", {
    recipe_id: result.data?.recipe_id, version: result.data?.recipe_version,
  }, ws.id, false, !!result.data);
  if (result.error) return <ErrorState error={result.error} retry={result.reload} />;
  if (!result.data) return <Loading />;
  const run = result.data, active = ["queued", "running"].includes(run.status), base = `/dataprep/w/${ws.id}`;
  const stepLabels = Object.fromEntries((version.data?.definition.steps ?? []).map((step, index) => [step.id, `${index + 1}. ${t(stepNames[step.type] || "步骤")}`]));
  return <><div className="suite-head"><div><h1>{run.output_name}</h1><p className="lede">{t("版本 {v}", { v: run.recipe_version })} · {when(run.created_at)}</p></div><RunBadge status={run.status} /></div>
    <div className="dp-actions"><Link className="btn" to={`${base}/recipes/${run.recipe_id}`}>{t("打开处理方案")}</Link>
      {write && <button className="btn" disabled={action.busy || !!(active && run.cancel_requested)} onClick={() => void action.run(active ? "run.cancel" : "run.retry", { run_id: id }, value => active ? result.reload() : navigate(`${base}/runs/${value.id}`))}>{active ? run.cancel_requested ? t("正在取消…") : t("取消运行") : t("以相同输入重新运行")}</button>}
      {run.output_dataset_id && <Link className="btn primary" to={`${base}/datasets/${run.output_dataset_id}`}>{t("打开结果")}</Link>}
      {!active && run.issue_total > 0 && <a className="btn" href={`/api/suite/dataprep/runs/${id}/issues?workspace_id=${encodeURIComponent(ws.id)}`}>{t("下载完整异常清单")}</a>}</div>
    {active && <p role="status" className="notice">{t("实际进度：步骤 {s}，已处理 {n} / {total} 行", { s: run.progress.step ?? 0, n: run.progress.processed ?? 0, total: run.progress.total ?? 0 })}</p>}
    {run.error_code && <ErrorState error={run.error_code} />}{action.error && <ErrorState error={action.error} />}
    {run.counts && <p className="dp-summary">{t("输出 {n} 行 · 异常 {errors} 项", { n: run.counts.output_rows, errors: run.counts.issues })}</p>}
    <section className="dp-panel"><h2>{t("固定输入")}</h2>{Object.entries(run.inputs).map(([name, input]) => <InputDataset key={name} ws={ws} name={name} id={input.dataset_id} />)}<p>{t("运行尝试次数：{n}", { n: run.attempts })}</p></section>
    {!!run.reports?.length && <div className="dp-grid"><table><caption>{t("逐步统计")}</caption><thead><tr>{["步骤", "输入行数", "输出行数", "异常"].map(s => <th key={s}>{t(s)}</th>)}</tr></thead><tbody>{run.reports.map(r => <tr key={r.step_id}><td>{stepLabels[r.step_id] || t("步骤")}</td><td>{r.input_rows}</td><td>{r.output_rows}</td><td>{r.issues}</td></tr>)}</tbody></table></div>}
    <h2>{t("异常行")}</h2>{issues.error ? <ErrorState error={issues.error} retry={issues.reload} /> : !issues.data ? <Loading /> : <><IssueList items={issues.data.items} stepLabels={stepLabels} /><Pager offset={offset} next={issues.data.next_offset} set={setOffset} /></>}
  </>;
}

function InputDataset({ ws, name, id }: { ws: WS; name: string; id: string }) {
  const { t } = useI18n();
  const result = useRead<Dataset>("dataprep.dataset.get", { dataset_id: id, offset: 0, limit: 1 }, ws.id);
  return <div>{name}: {result.error ? <ErrorState error={result.error} retry={result.reload} /> : <Link to={`/dataprep/w/${ws.id}/datasets/${id}`}>{result.data?.name || t("数据集")}</Link>}</div>;
}
