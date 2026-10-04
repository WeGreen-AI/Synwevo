import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useI18n } from "@synwevo/ui";
import { canWrite } from "../../shared/AppFrame";
import { Empty, Loading } from "../../shared/states";
import { base, ErrorBox, events, modes, operators, Pager, StageStatus, stamp, Status, useAction, useRead } from "./common";
import { Fields } from "./Fields";
import { PeoplePicker } from "./Selector";
import type { ApprovalRequest, Definition, Page, RequestRow, Snapshot, Template, Timeline, Workspace } from "./types";

export function Library({ ws }: { ws: Workspace }) {
  const { t } = useI18n(); const navigate = useNavigate(); const [status, setStatus] = useState("active");
  const [q, setQ] = useState(""); const [offset, setOffset] = useState(0); const [name, setName] = useState(""); const action = useAction(ws.id);
  const result = useRead<Page<Template>>("template.list", { status, q, offset }, ws.id);
  return <><header className="suite-head"><div><h1>{t("审批模板")}</h1><p className="lede">{t("将稳定的审批规则发布为版本，每次申请独立执行。")}</p></div></header>
    {["owner", "admin"].includes(ws.role) && <form className="ap-toolbar ap-panel" onSubmit={e => { e.preventDefault(); void action.run<Template>("template.create", { title: name }, v => navigate(`${base(ws.id)}/t/${v.id}`)); }}>
      <label className="grow">{t("新模板名称")}<input className="input" value={name} onChange={e => setName(e.target.value)} required maxLength={200} /></label><button className="btn primary" disabled={action.busy || !name.trim()}>{t("创建审批模板")}</button></form>}
    <ErrorBox error={action.error} />
    <div className="ap-toolbar"><label>{t("搜索模板")}<input className="input" maxLength={100} value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} /></label>
      <label>{t("模板状态")}<select className="input" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="active">{t("可用模板")}</option><option value="archived">{t("已归档")}</option></select></label></div>
    {result.error ? <ErrorBox error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : <>
      {!result.data.items.length ? <Empty title={t("没有匹配的审批模板")}><p>{t("管理员可以创建规则、选择独立审批人，然后发布供成员使用。")}</p></Empty> : <div className="suite-list">{result.data.items.map(row => <div className="suite-row" key={row.id}>
        <div className="grow"><Link to={`${base(ws.id)}/t/${row.id}`} className="title">{row.title}</Link><p className="sub">{row.description}</p><span className="muted">{row.published_version ? t("版本 {v}", { v: row.published_version }) : t("尚未发布")}</span></div>
        {canWrite(ws.role) && row.published_version && row.status === "active" && <Link className="btn primary sm" to={`${base(ws.id)}/new/${row.id}`}>{t("发起申请")}</Link>}
      </div>)}</div>}<Pager page={result.data} offset={offset} setOffset={setOffset} /></>}
  </>;
}
export function Requests({ ws, view }: { ws: Workspace; view: string }) {
  const { t } = useI18n(); const [q, setQ] = useState(""); const [offset, setOffset] = useState(0); const [status, setStatus] = useState("");
  const result = useRead<Page<RequestRow>>("request.list", { view, q, offset, status: status || null }, ws.id);
  const title = view === "todo" ? "我的待办" : view === "sent" ? "已发起" : view === "handled" ? "已处理" : "全部申请";
  return <><header className="suite-head"><div><h1>{t(title)}</h1><p className="lede">{t("核实当前修订的内容，再作出自己的决定。")}</p></div><div className="actions"><button className="btn" onClick={result.reload}>{t("刷新")}</button><Link className="btn primary" to={`${base(ws.id)}/templates`}>{t("发起申请")}</Link></div></header>
    <div className="ap-toolbar"><label>{t("搜索申请")}<input className="input" maxLength={100} value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} /></label>
      <label>{t("申请状态")}<select className="input" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">{t("全部状态")}</option>{["in_review", "stalled", "returned", "approved", "rejected", "withdrawn"].map(v => <option key={v} value={v}>{t(({ in_review: "审批中", stalled: "等待修正审批人", returned: "退回补充", approved: "已批准", rejected: "最终驳回", withdrawn: "已撤回" } as Record<string, string>)[v])}</option>)}</select></label></div>
    {result.error ? <ErrorBox error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : <>
      {!result.data.items.length ? <Empty title={t("此视图暂无申请")}><p>{t("提交、退回和审批决定会出现在相应的列表中。")}</p></Empty> : <div className="suite-list">{result.data.items.map(r => <Link className="suite-row" key={r.id} to={`${base(ws.id)}/r/${r.id}`}>
        <div className="grow"><div className="title">{r.title}</div><div className="sub">{t("修订 {n}", { n: r.revision })} · {stamp(r.updated_at)}</div></div><Status value={r.status} /></Link>)}</div>}
      <Pager page={result.data} offset={offset} setOffset={setOffset} /></>}
  </>;
}
export function NewRequest({ ws }: { ws: Workspace }) {
  const { tid } = useParams(); const result = useRead<Template>("template.get", { template_id: tid }, ws.id);
  const [chosen, setChosen] = useState<number | null>(null); const { t } = useI18n();
  if (result.error) return <ErrorBox error={result.error} retry={result.reload} />;
  if (!result.data) return <Loading />; const template = result.data; const version = chosen ?? template.published_version;
  return <><Link to={`${base(ws.id)}/templates`}>{t("审批模板")}</Link><header className="suite-head"><h1>{t("发起申请")}</h1></header>
    {template.status !== "active" || !version ? <Empty title={t("这个模板尚无可用的发布版本")} /> : <>
      <label>{t("使用版本")}<select className="input" value={version} onChange={e => setChosen(Number(e.target.value))}>{template.versions.filter(v => v.status === "published").map(v => <option key={v.version} value={v.version}>{v.title} · {v.version}</option>)}</select></label>
      <Compose key={version} ws={ws} tid={template.id} version={version} />
    </>}</>;
}
function Compose({ ws, tid, version }: { ws: Workspace; tid: string; version: number }) {
  const result = useRead<{ title: string; definition: Definition }>("template.version", { template_id: tid, version }, ws.id);
  const { t } = useI18n(); const navigate = useNavigate(); const action = useAction(ws.id);
  const [title, setTitle] = useState(""); const [values, setValues] = useState<Record<string, string>>({}); const [basis, setBasis] = useState("");
  if (result.error) return <ErrorBox error={result.error} retry={result.reload} />; if (!result.data) return <Loading />;
  return <form className="ap-panel ap-fields" onSubmit={e => { e.preventDefault(); void action.run<ApprovalRequest>("request.create", { template_id: tid, version, title, values, basis }, r => navigate(`${base(ws.id)}/r/${r.id}`)); }}>
    <h2>{result.data.title}</h2><ErrorBox error={action.error} /><label>{t("申请标题")}<input className="input" value={title} onChange={e => setTitle(e.target.value)} required maxLength={200} disabled={action.busy} /></label>
    <Fields fields={result.data.definition.fields} values={values} setValues={setValues} disabled={action.busy} />
    <label>{t("审批依据与影响内容")}<textarea className="input" rows={8} maxLength={30000} value={basis} onChange={e => setBasis(e.target.value)} disabled={action.busy} /></label>
    <p className="muted">{t("提交时保存不可变快照。之后修改内容将产生新修订，所有阶段重新审批。")}</p>
    <ol>{result.data.definition.stages.map(s => <li key={s.id}>{s.name} · {t(modes[s.mode])}{s.condition ? ` · ${t("条件路由")}` : ""}</li>)}</ol>
    <button className="btn primary" disabled={action.busy || !canWrite(ws.role)}>{t(action.busy ? "提交中…" : "提交申请")}</button>
  </form>;
}
export function Detail({ ws }: { ws: Workspace }) {
  const { rid } = useParams(); const result = useRead<ApprovalRequest>("request.get", { request_id: rid }, ws.id);
  if (result.error) return <ErrorBox error={result.error} retry={result.reload} />; if (!result.data) return <Loading />;
  return <RequestDetail key={`${ws.id}:${rid}:${result.data.state_version}`} ws={ws} r={result.data} reload={result.reload} />;
}
export function RequestDetail({ ws, r, reload }: { ws: Workspace; r: ApprovalRequest; reload: () => void }) {
  const { t } = useI18n(); const action = useAction(ws.id); const [note, setNote] = useState(""); const [selected, setSelected] = useState<string[]>([]);
  const [taskId, setTaskId] = useState(r.permissions.task_ids[0] ?? ""); const [decision, setDecision] = useState("approve");
  const [revision, setRevision] = useState(r.revision); const [revise, setRevise] = useState(false);
  const [values, setValues] = useState(r.snapshot.fields); const [basis, setBasis] = useState(r.snapshot.basis);
  const change = { request_id: r.id, state_version: r.state_version, note };
  const open = ["in_review", "stalled"].includes(r.status);
  const disabled = action.busy || !canWrite(ws.role);
  return <><Link to={base(ws.id)}>{t("我的待办")}</Link><header className="suite-head"><div><h1>{r.title}</h1><p className="lede">{r.people[r.requester_id]} · {t("修订 {n}", { n: r.revision })} · {t("版本 {v}", { v: r.template_version })}</p></div>
    <div className="actions"><Status value={r.status} /><button className="btn" onClick={reload} disabled={action.busy}>{t("刷新状态")}</button></div></header>
    {r.status === "stalled" && <div className="notice warn">{t("有效审批人不足，申请正在等待。独立管理员可补充审批人；不会减少所需票数或自动批准。")}</div>}
    {r.status === "returned" && <div className="notice warn">{t("申请已退回补充。发起人修改后提交新修订，所有阶段重新审批。")}</div>}
    <ErrorBox error={action.error} retry={reload} />
    <div className="ap-detail-grid"><div>
      <section className="ap-panel"><div className="ap-toolbar"><h2>{t("审批依据与影响内容")}</h2><label>{t("查看修订")}<select className="input" value={revision} onChange={e => setRevision(Number(e.target.value))}>{r.revisions.map(v => <option key={v.revision} value={v.revision}>{t("修订 {n}", { n: v.revision })}</option>)}</select></label></div>
        {revision === r.revision ? <SnapshotView r={r} snapshot={r.snapshot} previous={r.previous} /> : <OldSnapshot ws={ws.id} r={r} revision={revision} />}
      </section>
      {canWrite(ws.role) && <section className="ap-panel ap-fields"><h2>{t("审批意见与操作")}</h2>
        <label>{t("意见或原因")}<textarea className="input" maxLength={2000} rows={4} value={note} disabled={disabled} onChange={e => setNote(e.target.value)} /></label>
        <button className="btn" disabled={disabled || !note.trim()} onClick={() => action.run("request.comment", change, reload)}>{t("仅添加意见")}</button>
        {!!r.permissions.task_ids.length && open && <div className="ap-condition ap-fields">
          <p>{t("你正在决定修订 {n}。此决定只适用于上方展示的当前内容。", { n: r.revision })}</p>
          {r.permissions.task_ids.length > 1 && <select aria-label={t("审批任务")} value={taskId} onChange={e => setTaskId(e.target.value)}>{r.permissions.task_ids.map(x => <option key={x}>{x}</option>)}</select>}
          <label>{t("审批决定")}<select className="input" value={decision} disabled={disabled} onChange={e => setDecision(e.target.value)}><option value="approve">{t("批准")}</option><option value="return">{t("退回补充")}</option><option value="reject">{t("最终驳回")}</option></select></label>
          <button className="btn primary" disabled={disabled || revision !== r.revision || (decision !== "approve" && !note.trim())} onClick={() => action.run("request.decide", { ...change, task_id: taskId, decision }, reload)}>{t("提交决定")}</button>
          <details><summary>{t("转交给其他审批人")}</summary><PeoplePicker ws={ws.id} value={selected} onChange={setSelected} single disabled={disabled} exclude={[r.requester_id, ...r.tasks.filter(x => ["pending", "approved"].includes(x.status)).map(x => x.assignee_id)]} />
            <button className="btn" disabled={disabled || selected.length !== 1 || !note.trim()} onClick={() => action.run("request.transfer", { ...change, task_id: taskId, to_user_id: selected[0] }, reload)}>{t("确认转交")}</button></details>
        </div>}
        {r.permissions.manage && r.status === "stalled" && <details open><summary>{t("修正审批人")}</summary><PeoplePicker ws={ws.id} value={selected} onChange={setSelected} disabled={disabled} exclude={[r.requester_id, ...r.tasks.filter(x => ["pending", "approved"].includes(x.status)).map(x => x.assignee_id)]} />
          <button className="btn" disabled={disabled || !selected.length || !note.trim()} onClick={() => action.run("request.repair", { ...change, users: selected }, reload)}>{t("补充审批人")}</button></details>}
        {r.permissions.requester && r.status !== "withdrawn" && <>
          <button className="btn" disabled={disabled} onClick={() => setRevise(!revise)}>{t("修订并重新审批")}</button>
          {revise && <div className="ap-condition ap-fields"><p className="notice warn">{t("新修订会使旧修订的决定失效，并从第一阶段重新开始。")}</p><Fields fields={r.definition.fields} values={values} setValues={setValues} disabled={disabled} />
            <label>{t("审批依据与影响内容")}<textarea className="input" rows={8} maxLength={30000} value={basis} onChange={e => setBasis(e.target.value)} disabled={disabled} /></label>
            <button className="btn primary" disabled={disabled || !note.trim()} onClick={() => action.run("request.revise", { ...change, values, basis }, reload)}>{t("提交新修订")}</button></div>}
          {["in_review", "stalled", "returned"].includes(r.status) && <button className="btn" disabled={disabled} onClick={() => {
            if (confirm(t("撤回后本申请将停止，未完成的审批和等待都会取消。确定撤回？"))) void action.run("request.withdraw", change, reload);
          }}>{t("撤回申请")}</button>}
        </>}
      </section>}
      <History ws={ws.id} r={r} />
    </div><aside>
      <section className="ap-panel"><h2>{t("审批路径")}</h2><ol className="ap-stages">{r.definition.stages.map((s, n) => {
        const run = r.stages[n];
        return <li key={s.id}><strong>{s.name}</strong><p>{t(modes[s.mode])}</p><StageStatus value={run?.status ?? "not_started"} />
          {run && <p className="muted">{t("需要 {n} 票同意", { n: run.required })}</p>}
          {run?.due_at && <p className="muted">{t("截止时间")}<br /><time>{stamp(run.due_at)}</time></p>}
          {!!run?.reminded_at && <p>{t("已提醒")}</p>}{!!run?.escalated_at && <p>{t("已升级")}</p>}</li>;
      })}</ol></section>
      <section className="ap-panel"><h2>{t("当前阶段参与人")}</h2>{r.tasks.map(task => <div className="ap-participant" key={task.id}><strong>{r.people[task.assignee_id] ?? task.assignee_id}</strong><Status value={task.status} />
        {!task.eligible && ["pending", "approved"].includes(task.status) && <p className="notice warn">{t("当前权限已失效，此人的票不会计入未完成阶段。")}</p>}</div>)}</section>
    </aside></div>
  </>;
}
function SnapshotView({ r, snapshot, previous }: { r: ApprovalRequest; snapshot: Snapshot; previous: Snapshot | null }) {
  const { t } = useI18n();
  return <><p className="muted">{stamp(snapshot.submitted_at)} · {snapshot.note}</p>
    <dl className="ap-snapshot">{r.definition.fields.map(f => {
      const changed = previous && previous.fields[f.id] !== snapshot.fields[f.id];
      const show = (v: string | undefined) => f.type === "select" ? f.options.find(o => o.value === v)?.label ?? v ?? "—" : v ?? "—";
      return <div className={changed ? "ap-changed" : ""} key={f.id}><dt>{f.label}{f.currency ? ` (${f.currency})` : ""}</dt><dd>{show(snapshot.fields[f.id])}
        {changed && <div className="muted small">{t("之前：{value}", { value: show(previous?.fields[f.id]) })}</div>}</dd></div>;
    })}</dl>
    <h3>{t("影响内容")}</h3><div className="ap-prose">{snapshot.basis || t("未提供额外依据")}</div>
    {previous && snapshot.basis_hash !== previous.basis_hash && <details className="ap-changed"><summary>{t("依据已改变，查看之前的内容")}</summary><div className="ap-prose">{previous.basis || t("未提供额外依据")}</div></details>}
    <details><summary>{t("快照校验摘要")}</summary><p className="muted">{t("此摘要用于识别审批时的准确内容，不代表外部来源已验证。")}</p><code className="ap-digest">{snapshot.fields_hash}<br />{snapshot.basis_hash}</code></details>
  </>;
}
function OldSnapshot({ ws, r, revision }: { ws: string; r: ApprovalRequest; revision: number }) {
  const { t } = useI18n(); const result = useRead<Snapshot>("request.revision", { request_id: r.id, revision }, ws);
  if (result.error) return <ErrorBox error={result.error} />; if (!result.data) return <Loading />;
  return <><p className="notice warn">{t("这是历史修订，不能在这个内容上提交当前决定。")}</p><SnapshotView r={r} snapshot={result.data} previous={null} /></>;
}
function History({ ws, r }: { ws: string; r: ApprovalRequest }) {
  const { t } = useI18n(); const [cursors, setCursors] = useState([0]);
  const result = useRead<{ items: Timeline[]; next_after: number | null }>("request.timeline", { request_id: r.id, after: cursors.at(-1), limit: 50 }, ws);
  return <section className="ap-panel"><h2>{t("完整时间线")}</h2>{result.error ? <ErrorBox error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : <>
    <ol className="ap-timeline">{result.data.items.map(item => <li key={item.seq}><div className="ap-toolbar"><strong>{t(events[item.kind] ?? item.kind)}</strong><span className="badge">{t("修订 {n}", { n: item.revision })}</span></div>
      <small className="muted">{stamp(item.at)} · {item.actor_name || (item.actor_id ? r.people[item.actor_id] ?? item.actor_id : t("系统处理"))}</small>
      {item.detail.note && <p className="ap-prose">{item.detail.note}</p>}
      {item.detail.decision && <p><Status value={item.detail.decision === "approve" ? "approved" : item.detail.decision === "return" ? "returned" : "rejected"} /></p>}
      {item.detail.checks?.map((check, n) => <p key={n}>{check.label} · {t(operators[check.op] ?? check.op)} · {String(check.value ?? "")} · {t("实际值：{value}", { value: String(check.actual ?? "—") })} · {t(check.result ? "满足" : "不满足")}</p>)}
    </li>)}</ol><div className="ap-toolbar"><button className="btn" disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>{t("上一页")}</button>
      <button className="btn" disabled={result.data.next_after === null} onClick={() => setCursors([...cursors, result.data!.next_after!])}>{t("下一页")}</button></div>
  </>}</section>;
}
export function Alerts({ ws }: { ws: Workspace }) {
  const { t } = useI18n(); const [offset, setOffset] = useState(0);
  const result = useRead<Page<{ id: string; request_id: string; kind: string; created_at: number }>>("alerts", { offset }, ws.id);
  return <><header className="suite-head"><h1>{t("审批提醒")}</h1><div className="actions"><button className="btn" onClick={result.reload}>{t("刷新")}</button></div></header><p className="muted">{t("到期与升级提醒保存在这里；提醒并不代表审批完成。")}</p>
    {result.error ? <ErrorBox error={result.error} retry={result.reload} /> : !result.data ? <Loading /> : <>
      {!result.data.items.length ? <Empty title={t("暂无审批提醒")} /> : <div className="suite-list">{result.data.items.map(a => <Link key={a.id} className="suite-row" to={`${base(ws.id)}/r/${a.request_id}`}><span className="grow">{t(a.kind === "reminder" ? "审批已到期" : "审批已升级")}</span><time>{stamp(a.created_at)}</time></Link>)}</div>}
      <Pager page={result.data} offset={offset} setOffset={setOffset} /></>}
  </>;
}
