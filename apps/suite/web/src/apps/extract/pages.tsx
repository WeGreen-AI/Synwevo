import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { Empty, Failure, Loading, RunBadge } from "../../shared/states";
import { newKey } from "../../shared/ops";
import { blankField, displayValue, useAction, useErrorText, useLoad } from "./model";
import type { Evidence, Field, Location, Props, Segment, Task, Template } from "./model";

function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const text = useErrorText(error);
  return <Failure error={new Error(text)} onRetry={retry} />;
}
function Status({ status }: { status: string }) {
  const { t } = useI18n();
  return status === "waiting_input" ? <span className="badge warn">{t("待人工复核")}</span> : <RunBadge status={status} />;
}
function LocationText({ source }: { source: Location }) {
  const { t } = useI18n();
  return <span>{source.page ? t("第 {page} 页，第 {line} 行", { page: source.page, line: source.line ?? "—" }) :
    source.table ? t("{table}，行 {row}，列 {column}", { table: source.table, row: source.row ?? "—", column: source.column ?? "—" }) : t("无可靠来源")}</span>;
}
const baseOf = (ws: Props["ws"]) => `/extract/w/${ws.id}`;

export function Library({ ws, canWrite }: Props) {
  const { t } = useI18n();
  const [after, setAfter] = useState(""); const [archived, setArchived] = useState(false);
  const { data, error, reload } = useLoad<{ items: Template[]; next: string | null }>("extract.template.list", { after, archived }, ws.id);
  return <>
    <header className="suite-head"><div><h1>{t("提取模板")}</h1><p className="lede">{t("发布固定规则，每次任务保留独立的文件、结果和复核记录。")}</p></div>
      {canWrite && <Link className="btn primary" to={`${baseOf(ws)}/templates/new`}>{t("新建提取模板")}</Link>}</header>
    <label className="extract-check"><input type="checkbox" checked={archived} onChange={e => { setArchived(e.target.checked); setAfter(""); }} />{t("显示已归档模板")}</label>
    {error ? <ErrorNotice error={error} retry={reload} /> : !data ? <Loading /> : data.items.length === 0 ? <Empty title={t("还没有提取模板")} /> :
      <div className="suite-list">{data.items.map(item => <Link className="suite-row" key={item.id} to={`${baseOf(ws)}/templates/${item.id}`}>
        <div className="grow"><div className="title">{item.title}</div><div className="sub">{item.published_version ? t("发布版本 {v}", { v: item.published_version }) : t("尚未发布")}</div></div>
      </Link>)}</div>}
    <div className="extract-actions">{after && <button className="btn" onClick={() => setAfter("")}>{t("返回第一页")}</button>}
      {data?.next && <button className="btn" onClick={() => setAfter(data.next!)}>{t("下一页")}</button>}</div>
  </>;
}

export function TemplateEditor(props: Props) {
  const { tid } = useParams();
  return tid === "new" ? <EditorForm key="new" {...props} /> : <ExistingEditor key={tid} {...props} tid={tid!} />;
}
function ExistingEditor({ tid, ...props }: Props & { tid: string }) {
  const load = useLoad<Template>("extract.template.get", { template_id: tid }, props.ws.id);
  return load.error ? <ErrorNotice error={load.error} retry={load.reload} /> : !load.data ? <Loading /> : <EditorForm {...props} initial={load.data} reload={load.reload} />;
}
function EditorForm({ ws, canWrite, initial, reload }: Props & { initial?: Template; reload?: () => void }) {
  const { t } = useI18n(); const navigate = useNavigate();
  const [title, setTitle] = useState(initial?.title ?? "");
  const [fields, setFields] = useState<Field[]>(initial?.draft.fields ?? [blankField()]);
  const persisted = useRef(initial);
  const persistedInput = useRef(initial ? JSON.stringify({ title: initial.title, definition: initial.draft }) : "");
  const action = useAction(ws.id); const [saved, setSaved] = useState(false);
  const disabled = !canWrite || action.busy || !!initial?.archived;
  const edit = (n: number, patch: Partial<Field>) => { setSaved(false); setFields(fs => fs.map((f, i) => i === n ? { ...f, ...patch } : f)); };
  const save = async (publish: boolean) => {
    const input = { title, definition: { fields } };
    const current = persisted.current;
    const encoded = JSON.stringify(input);
    const result = current && persistedInput.current === encoded ? current : current ?
      await action.execute<Template>("extract.template.save", { ...input, template_id: current.id, draft_rev: current.draft_rev }) :
      await action.execute<Template>("extract.template.create", input);
    if (!result) return;
    // Preserve the saved draft if publishing fails. Retrying publishes this same
    // revision instead of creating another template or silently hiding the error.
    persisted.current = result; persistedInput.current = encoded;
    if (publish && !await action.execute("extract.template.publish", { template_id: result.id, draft_rev: result.draft_rev })) return;
    setSaved(true);
    if (!initial) navigate(`${baseOf(ws)}/templates/${result.id}`); else reload?.();
  };
  return <>
    <header className="suite-head"><div><Link to={`${baseOf(ws)}/templates`}>{t("提取模板")}</Link><h1>{initial?.title ?? t("新建提取模板")}</h1></div></header>
    <p className="notice">{t("规则只从解析原文取值，不使用模型推测。日期使用年－月－日，小数不使用指数格式。")}</p>
    <form className="extract-editor" onSubmit={e => { e.preventDefault(); void save(false); }}>
      <label>{t("模板名称")}<input className="input" required maxLength={160} value={title} disabled={disabled} onChange={e => { setTitle(e.target.value); setSaved(false); }} /></label>
      {fields.map((field, n) => <fieldset className="extract-field" key={field.id} disabled={disabled}>
        <legend>{t("字段 {n}", { n: n + 1 })}</legend><div className="extract-grid">
          <label>{t("字段名称")}<input className="input" required maxLength={120} value={field.label} onChange={e => edit(n, { label: e.target.value })} /></label>
          <label>{t("字段类型")}<select className="input" value={field.type} onChange={e => edit(n, { type: e.target.value as Field["type"] })}>
            <option value="text">{t("文本")}</option><option value="decimal">{t("精确小数")}</option><option value="date">{t("日期")}</option><option value="boolean">{t("布尔值")}</option></select></label>
          <label>{t("取值方式")}<select className="input" value={field.rule.kind} onChange={e => edit(n, { rule: { ...field.rule, kind: e.target.value as Field["rule"]["kind"] } })}>
            <option value="prefix">{t("行首文字之后")}</option><option value="cell">{t("指定单元格")}</option><option value="column">{t("按列标题取值")}</option></select></label>
          {field.rule.kind === "prefix" ? <label>{t("精确行首文字")}<input className="input" required maxLength={120} value={field.rule.prefix} onChange={e => edit(n, { rule: { ...field.rule, prefix: e.target.value } })} /></label> : <>
            <label>{t("表格名称（留空匹配全部）")}<input className="input" maxLength={120} value={field.rule.table} onChange={e => edit(n, { rule: { ...field.rule, table: e.target.value } })} /></label>
            {field.rule.kind === "column" ? <label>{t("第一行的精确列标题")}<input className="input" required maxLength={120} value={field.rule.header} onChange={e => edit(n, { rule: { ...field.rule, header: e.target.value } })} /></label> : <>
              <label>{t("行号（从一开始）")}<input className="input" type="number" min={1} max={2000} value={field.rule.row} onChange={e => edit(n, { rule: { ...field.rule, row: Number(e.target.value) } })} /></label>
              <label>{t("列号（从一开始）")}<input className="input" type="number" min={1} max={100} value={field.rule.column} onChange={e => edit(n, { rule: { ...field.rule, column: Number(e.target.value) } })} /></label></>}
          </>}
          <label>{t("最大文字长度")}<input className="input" type="number" min={1} max={2000} value={field.max_length} onChange={e => edit(n, { max_length: Number(e.target.value) })} /></label>
          {field.type === "decimal" && <><label>{t("最小值")}<input className="input" maxLength={50} value={field.minimum ?? ""} onChange={e => edit(n, { minimum: e.target.value || null })} /></label>
            <label>{t("最大值")}<input className="input" maxLength={50} value={field.maximum ?? ""} onChange={e => edit(n, { maximum: e.target.value || null })} /></label></>}
        </div><div className="extract-actions">
          <label className="extract-check"><input type="checkbox" checked={field.required} onChange={e => edit(n, { required: e.target.checked })} />{t("必填字段")}</label>
          <label className="extract-check"><input type="checkbox" checked={field.many} onChange={e => edit(n, { many: e.target.checked })} />{t("保留重复明细")}</label>
          <button type="button" className="btn sm" disabled={fields.length === 1} onClick={() => { setFields(fs => fs.filter((_, i) => n !== i)); setSaved(false); }}>{t("移除字段")}</button>
        </div><small className="muted">{t("固定字段标识：{id}", { id: field.id })}</small>
      </fieldset>)}
      <div className="extract-actions"><button type="button" className="btn" disabled={disabled || fields.length >= 40} onClick={() => { setFields(fs => [...fs, blankField()]); setSaved(false); }}>{t("添加提取字段")}</button>
        <button type="submit" className="btn primary" disabled={disabled}>{action.busy ? t("保存中…") : t("保存草稿")}</button>
        <button type="button" className="btn" disabled={disabled} onClick={e => { if (e.currentTarget.form?.reportValidity()) void save(true); }}>{t("保存并发布新版本")}</button>
        {saved && <span role="status">{t("已保存")}</span>}
      </div>
    </form>
    {!!action.error && <ErrorNotice error={action.error} retry={reload} />}
    {initial && <section className="extract-section"><h2>{t("发布版本")}</h2>
      {initial.versions.map(v => <div className="suite-row" key={v.version}><span className="grow">{t("版本 {v}", { v: v.version })} {v.retired ? t("已停用") : ""}</span>
        {canWrite && !v.retired && <button className="btn sm" disabled={action.busy} onClick={async () => { if (await action.execute("extract.template.retire", { template_id: initial.id, version: v.version })) reload?.(); }}>{t("停用此版本")}</button>}</div>)}
      {canWrite && <button className="btn" disabled={action.busy} onClick={async () => { if (await action.execute("extract.template.archive", { template_id: initial.id, archived: !initial.archived })) reload?.(); }}>{initial.archived ? t("恢复模板") : t("归档模板")}</button>}
    </section>}
  </>;
}

type TaskSummary = { id: string; filename: string; title: string; status: string; template_version: number };
export function Tasks({ ws, canWrite, review = false }: Props & { review?: boolean }) {
  const { t } = useI18n(); const [after, setAfter] = useState(""); const [status, setStatus] = useState(review ? "waiting_input" : "");
  const load = useLoad<{ items: TaskSummary[]; next: string | null }>("extract.task.list", { after, status }, ws.id, true);
  return <><header className="suite-head"><div><h1>{review ? t("待复核队列") : t("提取文件")}</h1><p className="lede">{t("上传真实文件，按发布版本提取，在原文旁核对每个字段。")}</p></div></header>
    {canWrite && !review && <ImportFiles ws={ws} onDone={load.reload} />}
    {!review && <label className="extract-filter">{t("任务状态")}<select className="input" value={status} onChange={e => { setStatus(e.target.value); setAfter(""); }}>
      <option value="">{t("全部任务")}</option><option value="waiting_input">{t("待人工复核")}</option><option value="queued">{t("排队中")}</option><option value="running">{t("进行中")}</option>
      <option value="succeeded">{t("已完成")}</option><option value="failed">{t("失败")}</option><option value="cancelled">{t("已取消")}</option></select></label>}
    {load.error ? <ErrorNotice error={load.error} retry={load.reload} /> : !load.data ? <Loading /> : !load.data.items.length ? <Empty title={t("暂无提取任务")} /> :
      <div className="suite-list">{load.data.items.map(task => <Link key={task.id} className="suite-row" to={`${baseOf(ws)}/tasks/${task.id}`}><div className="grow"><div className="title">{task.filename}</div>
        <div className="sub">{task.title} · {t("版本 {v}", { v: task.template_version })}</div></div><Status status={task.status} /></Link>)}</div>}
    <div className="extract-actions">{after && <button className="btn" onClick={() => setAfter("")}>{t("返回第一页")}</button>}{load.data?.next && <button className="btn" onClick={() => setAfter(load.data!.next!)}>{t("下一页")}</button>}</div>
  </>;
}
function ImportFiles({ ws, onDone }: { ws: Props["ws"]; onDone: () => void }) {
  const { t } = useI18n(); const templates = useLoad<{ items: Template[]; next: string | null }>("extract.template.list", { limit: 100 }, ws.id);
  const [template, setTemplate] = useState(""); const [files, setFiles] = useState<File[]>([]); const [ocr, setOcr] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>();
  const [created, setCreated] = useState<{ id: string; filename: string }[]>([]); const lock = useRef(false);
  const uploadKeys = useRef(new WeakMap<File, string>());
  const action = useAction(ws.id);
  const submit = async () => {
    if (lock.current) return; lock.current = true; setBusy(true); setError(undefined);
    try {
      const selected = templates.data?.items.find(item => item.id === template);
      if (!selected?.published_version || files.length < 1 || files.length > 10) throw new Error("invalid_input");
      for (const file of files) {
        if (file.size > 12 * 1024 * 1024) throw "file_limit";
        const key = uploadKeys.current.get(file) ?? newKey(); uploadKeys.current.set(file, key);
        const body = new FormData(); body.append("file", file);
        const result = await api<{ id: string }>(`/api/suite/extract/files?workspace_id=${encodeURIComponent(ws.id)}`, {
          method: "POST", body, workspaceId: ws.id, headers: { "Idempotency-Key": key },
        });
        const task = await action.execute<{ id: string }>("extract.task.create", { template_id: selected.id, version: selected.published_version, file_id: result.id, ocr });
        if (!task) break;
        setCreated(old => [...old, { id: task.id, filename: file.name }]);
        setFiles(old => old.filter(f => f !== file)); onDone();
      }
    } catch (e) { setError(e); }
    finally { lock.current = false; setBusy(false); }
  };
  return <section className="extract-import">
    <h2>{t("导入文件")}</h2><p className="muted">{t("每批最多十个文件，每个十二兆字节；每个文件创建独立任务。扫描件需要已配置的文字识别服务。")}</p>
    {templates.error ? <ErrorNotice error={templates.error} retry={templates.reload} /> : !templates.data ? <Loading /> : <form onSubmit={e => { e.preventDefault(); void submit(); }}>
      <div className="extract-grid"><label>{t("选择已发布模板")}<select className="input" required value={template} disabled={busy} onChange={e => setTemplate(e.target.value)}><option value="">{t("请选择")}</option>
        {templates.data.items.filter(item => item.published_version).map(item => <option key={item.id} value={item.id}>{item.title} · {t("版本 {v}", { v: item.published_version! })}</option>)}</select></label>
        <label>{t("选择文件")}<input className="input" type="file" multiple accept=".pdf,.csv,.tsv,.png,.jpg,.jpeg,.webp,.tif,.tiff" disabled={busy} onChange={e => { setFiles(Array.from(e.target.files ?? [])); setCreated([]); }} /></label></div>
      <label className="extract-check"><input type="checkbox" checked={ocr} disabled={busy} onChange={e => setOcr(e.target.checked)} />{t("启用文字识别")}</label>
      <div className="extract-actions"><button className="btn primary" disabled={busy || !files.length || files.length > 10}>{busy ? t("正在接纳文件…") : t("开始提取")}</button>
        <Link to={`${baseOf(ws)}/templates/new`}>{t("新建提取模板")}</Link></div>
      {files.length > 10 && <p role="alert">{t("每批最多十个文件")}</p>}
      {created.length > 0 && <ul aria-live="polite">{created.map(item => <li key={item.id}><Link to={`${baseOf(ws)}/tasks/${item.id}`}>{item.filename}</Link> · {t("已排队")}</li>)}</ul>}
      {templates.data.next && <p>{t("这里只显示前一百个模板，请从模板库查找其他版本。")}</p>}
    </form>}
    {!!(error || action.error) && <ErrorNotice error={error || action.error} />}
  </section>;
}

const valueLabels: Record<string, string> = { extracted: "已定位取值", missing: "缺失", ambiguous: "有多个相同匹配", conflict: "取值冲突", invalid: "校验失败", unverified: "无可靠来源" };
export function TaskDetail({ ws, canWrite }: Props) {
  const { tid } = useParams(); const { t } = useI18n();
  const load = useLoad<Task>("extract.task.get", { task_id: tid }, ws.id, true);
  const action = useAction(ws.id);
  if (load.error) return <ErrorNotice error={load.error} retry={load.reload} />;
  if (!load.data) return <Loading />;
  const task = load.data;
  return <><header className="suite-head"><div><Link to={baseOf(ws)}>{t("提取文件")}</Link><h1 className="extract-long">{task.file.filename}</h1><p className="lede">{task.title} · {t("版本 {v}", { v: task.template_version })}</p></div><Status status={task.status} /></header>
    {task.error_code && <ErrorNotice error={task.error_code} />}
    {canWrite && (task.status === "queued" || task.status === "running") && <button className="btn" disabled={action.busy} onClick={async () => { if (await action.execute("extract.task.cancel", { task_id: task.id })) load.reload(); }}>{t("取消提取")}</button>}
    {!!action.error && <ErrorNotice error={action.error} />}
    {(task.status === "queued" || task.status === "running") && <p role="status">{t("任务状态来自服务器；完成解析后进入人工复核。")}</p>}
    {task.values && <Review key={`${task.id}:${task.revision}`} task={task} ws={ws} canWrite={canWrite} reload={load.reload} />}
    <details className="extract-section"><summary>{t("原文件与检查状态")}</summary><p className="extract-long"><code>{task.file.content_digest}</code></p>
      <p>{task.file.scan.status === "not_configured" ? t("安全扫描未配置，未声称扫描通过。") : t("文件接纳状态：{status}", { status: task.file.scan.status })}</p>
      {task.file.source?.app === "forms" && task.file.source.workspace_id === ws.id && task.file.source.submission_id &&
        <p><Link to={`/forms/w/${encodeURIComponent(ws.id)}/s/${encodeURIComponent(task.file.source.submission_id)}`}>{t("查看来源表单提交")}</Link></p>}
      <a className="btn" href={`/api/suite/extract/files/${task.file.id}?workspace_id=${encodeURIComponent(ws.id)}`}>{t("下载原文件（需要导出权限）")}</a></details>
  </>;
}
function Review({ task, ws, canWrite, reload }: Props & { task: Task; reload: () => void }) {
  const { t } = useI18n(); const [selected, setSelected] = useState<Evidence>(); const [offset, setOffset] = useState(0);
  const [edits, setEdits] = useState<Record<string, string>>({}); const [note, setNote] = useState(""); const [ack, setAck] = useState(false);
  const [history, setHistory] = useState<number>(); const action = useAction(ws.id);
  const segments = useLoad<{ items: Segment[]; total: number }>("extract.task.segments", { task_id: task.id, offset }, ws.id);
  const pick = (evidence: Evidence) => { setSelected(evidence); setOffset(Math.floor(Number(evidence.segment_id.slice(1)) / 50) * 50); };
  useEffect(() => { if (selected && segments.data) document.getElementById(`extract-${selected.segment_id}`)?.scrollIntoView({ block: "nearest", behavior: "instant" }); }, [selected, segments.data]);
  const changed = Object.keys(edits).length > 0;
  const correct = async () => {
    const values = Object.fromEntries(Object.entries(edits).map(([id, value]) => [id, task.definition.fields.find(f => f.id === id)?.many ? (value ? value.split("\n") : []) : value]));
    if (await action.execute("extract.task.correct", { task_id: task.id, revision: task.revision, values, note })) reload();
  };
  return <>
    <p className="notice">{t("所有结果须人工确认；无法定位的值保留“无可靠来源”，确认不会把它变成机器证据。")}</p>
    <div className="extract-review"><section className="extract-source" aria-label={t("解析原文")}><h2>{t("解析原文")}</h2>
      {segments.error ? <ErrorNotice error={segments.error} retry={segments.reload} /> : !segments.data ? <Loading /> : segments.data.items.map(seg => <article tabIndex={0} id={`extract-${seg.id}`} key={seg.id} className={`extract-segment${selected?.segment_id === seg.id ? " selected" : ""}`}>
        <small className="muted"><LocationText source={seg.source} /></small>
        <p>{selected?.segment_id === seg.id && seg.located ? <>{seg.text.slice(0, selected.start)}<mark>{seg.text.slice(selected.start, selected.end)}</mark>{seg.text.slice(selected.end)}</> : seg.text}</p>
      </article>)}
      <div className="extract-actions"><button className="btn sm" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 50))}>{t("上一页原文")}</button>
        <span>{t("从第 {n} 段开始", { n: offset + 1 })}</span><button className="btn sm" disabled={!segments.data || offset + 50 >= segments.data.total} onClick={() => setOffset(n => n + 50)}>{t("下一页原文")}</button></div>
    </section><section className="extract-values" aria-label={t("字段与来源")}><h2>{t("字段与来源")}</h2>
      {task.definition.fields.map(field => { const value = task.values![field.id]; return <article className="extract-result" key={field.id}>
        <div className="extract-actions"><h3>{field.label}</h3><span className={`badge ${value.status === "extracted" ? "ok" : "warn"}`}>{t(valueLabels[value.status] ?? "无可靠来源")}</span></div>
        <label>{field.many ? t("当前值（每行一项）") : t("当前值")}<textarea className="input" rows={field.many ? 4 : 2} maxLength={field.many ? 64000 : field.max_length}
          disabled={!canWrite || action.busy} value={edits[field.id] ?? (value.value === null ? "" : displayValue(value))}
          onChange={e => setEdits(old => ({ ...old, [field.id]: e.target.value }))} /></label>
        <p className="muted">{value.origin === "human" ? t("当前值由人工修正") : t("当前值来自确定性规则")}</p>
        {value.candidates.map((candidate, n) => <button key={n} className="extract-evidence" type="button" onClick={() => pick(candidate.evidence)}>
          <span>{candidate.value}</span><small><LocationText source={candidate.evidence.source} />{!candidate.verified && ` · ${t("无可靠来源")}`}</small></button>)}
        <details><summary>{t("最初机器结果")}</summary><pre>{displayValue(task.machine?.[field.id])}</pre></details>
      </article>; })}
    </section></div>
    {canWrite && <form className="extract-section" onSubmit={e => { e.preventDefault(); if (changed) void correct(); }}>
      <label>{t("复核说明")}<textarea className="input" required maxLength={1000} value={note} disabled={action.busy} onChange={e => setNote(e.target.value)} /></label>
      <label className="extract-check"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />{t("我已核对无可靠来源的值，并接受其来源限制")}</label>
      <div className="extract-actions"><button className="btn" type="submit" disabled={!changed || !note.trim() || action.busy}>{t("保存修正为新版本")}</button>
        <button className="btn primary" type="button" disabled={changed || task.status !== "waiting_input" || !note.trim() || action.busy} onClick={async () => {
          if (await action.execute("extract.task.confirm", { task_id: task.id, revision: task.revision, note, acknowledge_unverified: ack })) reload();
        }}>{t("确认当前复核版本")}</button></div>
      {changed && <p role="status">{t("先保存修正，再确认新版本。旧的确认不会沿用。")}</p>}
      {!!action.error && <ErrorNotice error={action.error} retry={reload} />}
    </form>}
    {task.confirmed_revision && <a className="btn primary" href={`/api/suite/extract/tasks/${task.id}/export?workspace_id=${encodeURIComponent(ws.id)}&revision=${task.confirmed_revision}`}>{t("导出已确认结果")}</a>}
    <section className="extract-section"><h2>{t("修正与确认历史")}</h2>{task.history.length === 0 ? <p className="muted">{t("尚无人工复核记录")}</p> : task.history.map(item => <div className="suite-row extract-history" key={item.revision}>
      <button className="btn sm" onClick={() => setHistory(item.revision)}>{t("复核版本 {v}", { v: item.revision })}</button>
      <div className="grow"><strong>{item.kind === "confirmation" ? t("人工确认") : t("人工修正")}</strong><p>{item.note}</p><small>{item.actor_id} · <time dateTime={new Date(item.created_at * 1000).toISOString()}>{new Date(item.created_at * 1000).toLocaleString()}</time></small></div>
    </div>)}{history && <History task={task} ws={ws} revision={history} onClose={() => setHistory(undefined)} />}</section>
  </>;
}
function History({ task, ws, revision, onClose }: { task: Task; ws: Props["ws"]; revision: number; onClose: () => void }) {
  const { t } = useI18n();
  const load = useLoad<{ values: Task["values"] }>("extract.task.review_version", { task_id: task.id, revision }, ws.id);
  return <section className="extract-history-detail"><h3>{t("复核版本 {v}", { v: revision })}</h3><button className="btn sm" onClick={onClose}>{t("关闭历史详情")}</button>
    {load.error ? <ErrorNotice error={load.error} retry={load.reload} /> : !load.data ? <Loading /> : task.definition.fields.map(field => <div key={field.id}><strong>{field.label}</strong><pre>{displayValue(load.data?.values?.[field.id])}</pre></div>)}
  </section>;
}
