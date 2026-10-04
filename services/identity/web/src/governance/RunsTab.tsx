import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { get, qs } from "@synwevo/auth-client";
import { fmtTime, formatNumber, Modal, useI18n } from "@synwevo/ui";
import { useDebounced, useLoad } from "../lib/hooks";
import { useAdminData, useAdminPage } from "./data";
import { PageControls } from "./ListControls";
import { errText, fmtDuration } from "./labels";
import { ADMIN_HEADING_ID } from "../admin/navigation";
import { DELEGATION_LABEL, FLOW_STATES, FLOW_VIEW_KEYS, HISTORY_LABEL, PRINCIPAL_LABEL, RUN_STATES, RUN_VIEW_KEYS, STATE_LABEL, VIA,
  VIA_LABEL, WINDOWS_S, WINDOW_LABEL, appName, closedWithoutFocus, elapsedSeconds, isCorrelationDigest, readView, runsHref,
  stateTone, windowOf, withView,
  type FlowDetail, type FlowRun, type FlowStep, type Run, type RunDetail, type RunSummary } from "./runs";
import "./messages";
import "./runs.messages";
/**
 * Operation runs and flow runs of the business applications, as platform administration sees
 * them (synwevo_identity.admin_runs): what ran, for whom, through which entry, how it ended.
 *
 * Read-only, and metadata only — the server never sends an input, an output, a failure sentence
 * or a name a person chose, and these pages say so where a reader would look for them. The
 * filters and the open run are kept in the address (runs.ts), so a reload, the Back button and a
 * link from another page show the same thing.
 */

type Props = { onDenied: () => void };
type Translate = ReturnType<typeof useI18n>["t"];
const label = (t: Translate, map: Record<string, string>, key: string | null | undefined) =>
  !key ? "—" : map[key] ? t(map[key]) : key;

/** The page's view state, read from and written to the address. Opening a run adds a history
 *  entry (Back closes it); changing a filter replaces the current one. */
function useView<K extends string>(keys: readonly K[]) {
  const location = useLocation();
  const navigate = useNavigate();
  const view = readView(location.search, keys) as Record<K | "run", string>;
  const set = (changes: Partial<Record<K, string>>) =>
    navigate({ pathname: location.pathname, search: withView(location.search, changes as Record<string, string>) }, { replace: true });
  // From the list this adds an entry; from inside an open run it replaces it, so closing (or
  // Back) returns to the list whichever run the reader ended on.
  const open = (run: string) =>
    navigate({ pathname: location.pathname, search: withView(location.search, { run }) },
      view.run ? { replace: true, state: location.state } : { state: { opened: true } });
  const close = () => (location.state as { opened?: boolean } | null)?.opened ? navigate(-1)
    : navigate({ pathname: location.pathname, search: withView(location.search, { run: "" }) }, { replace: true });
  // The dialog's own cleanup has already returned focus to its opener when this runs (cleanups
  // run before effects); what is left is the case it cannot handle (runs.ts).
  const shown = useRef(view.run);
  useEffect(() => {
    const lost = closedWithoutFocus(shown.current, view.run, !!document.activeElement && document.activeElement !== document.body);
    shown.current = view.run;
    if (lost) document.getElementById(ADMIN_HEADING_ID)?.focus();
  }, [view.run]);
  return { view, set, open, close, search: location.search };
}

/** A search box whose text reaches the address once the person stops typing, and follows the
 *  address when it changes without them (Back, a link). */
function useSearchText(value: string, write: (text: string) => void) {
  const [text, setText] = useState(value);
  const written = useRef(value);
  const typed = useDebounced(text.trim());
  useEffect(() => {
    if (typed !== written.current) { written.current = typed; write(typed); }
  }, [typed]);
  useEffect(() => {
    if (value !== written.current) { written.current = value; setText(value); }
  }, [value]);
  return [text, setText] as const;
}

function StateBadge({ status, flagged = false }: { status: string; flagged?: boolean }) {
  const { t } = useI18n();
  return <span className={`badge ${stateTone(status, flagged)}`}>{label(t, STATE_LABEL, status)}</span>;
}

function Stat({ label: name, value, tone }: { label: string; value: number; tone?: "err" | "warn" }) {
  return <div className="card stat">
    <div className="muted small">{name}</div>
    <div className="v" style={tone && value > 0 ? { color: `var(--${tone})` } : undefined}>{formatNumber(value)}</div>
  </div>;
}

const who = (row: { user_email: string | null; user_id: string }) => row.user_email || row.user_id;

// ------------------------------------------------------------------------------------ operation runs
export function AppRunsTab({ onDenied }: Props) {
  const { t } = useI18n();
  const { view, set, open, close, search } = useView(RUN_VIEW_KEYS);
  const window_s = windowOf(view.window);
  const summary = useAdminData<RunSummary>(`/api/admin/app-runs/summary${qs({ window_s })}`, onDenied);
  const list = useAdminPage<Run>(`/api/admin/app-runs${qs({ status: view.status, app: view.app, via: view.via,
    workspace_id: view.workspace, user_id: view.user, q: view.q })}`, onDenied);
  const [q, setQ] = useSearchText(view.q, (text) => set({ q: text }));
  const items = list.loading || list.err ? [] : list.data?.items || [];
  const s = summary.data;
  // the application filter offers what ran in the window, plus the one already chosen
  const apps = [...new Set([...(s?.runs.by_app.map((a) => a.app) || []), ...(view.app ? [view.app] : [])])];
  const filtered = !!(view.status || view.app || view.via || view.workspace || view.user || view.q);
  const stale = s ? fmtDuration(s.stale_after_s) : "";
  const staleAfter = (list.data as (typeof list.data & { stale_after_s?: number }) | null)?.stale_after_s ?? s?.stale_after_s ?? 600;
  return <>
    <section className="card card-b col gap12" aria-labelledby="runs-summary-title">
      <div className="row gap12 wrap"><h2 className="grow" id="runs-summary-title">{t("运行概况")}</h2>
        <label className="field">{t("时间范围")}<select className="input" value={window_s}
          onChange={(e) => set({ window: e.target.value === String(86400) ? "" : e.target.value })}>
          {WINDOWS_S.map((w) => <option key={w} value={w}>{t(WINDOW_LABEL[w])}</option>)}</select></label>
        <button className="btn sm" disabled={summary.loading} onClick={summary.reload}>{t("刷新概况")}</button></div>
      {summary.err && <div className="notice err" role="alert">{summary.err} <button className="btn sm" onClick={summary.reload}>{t("重试")}</button></div>}
      {summary.loading && !s && <p role="status">{t("正在读取运行概况…")}</p>}
      {s && !summary.err && <>
        <div className="row gap12 wrap">
          <Stat label={t("运行")} value={s.runs.total} />
          <Stat label={t("失败")} value={s.runs.by_status.failed || 0} tone="err" />
          <Stat label={t("结果不明")} value={s.runs.by_status.unknown || 0} tone="warn" />
          <Stat label={t("此刻已停滞")} value={s.runs.stalled} tone="warn" />
        </div>
        <p className="muted small">{t("按最后更新时间落在所选范围内的运行统计。「此刻已停滞」是仍在排队或执行、且超过 {duration} 没有更新的运行，按现在计算，不受时间范围限制。数据时间：{time}。",
          { duration: stale, time: fmtTime(s.generated_at) })}</p>
        {s.runs.total === 0 ? <p className="muted">{t("所选时间范围内没有运行。")}</p>
          : <div className="list-wrap"><table className="gov" aria-label={t("各应用的运行")}>
            <thead><tr><th scope="col">{t("应用")}</th><th scope="col">{t("运行")}</th><th scope="col">{t("失败")}</th>
              <th scope="col">{t("结果不明")}</th><th scope="col" className="act">{t("操作")}</th></tr></thead>
            <tbody>{s.runs.by_app.map((a) => <tr key={a.app}>
              <th scope="row">{appName(a.app)}</th><td>{formatNumber(a.total)}</td><td>{formatNumber(a.failed)}</td>
              <td>{formatNumber(a.unknown)}</td>
              <td className="act"><button className="btn sm" onClick={() => set({ app: a.app, status: a.failed + a.unknown > 0 ? "attention" : "" })}>
                {t("查看这个应用的运行")}</button></td></tr>)}</tbody>
          </table></div>}
      </>}
    </section>
    <section className="card card-b col gap12" aria-labelledby="runs-list-title">
      <div className="row gap12 wrap"><h2 className="grow" id="runs-list-title">{t("运行记录")}</h2>
        {filtered && <button className="btn sm" onClick={() => { set({ status: "", app: "", via: "", workspace: "", user: "", q: "" }); }}>{t("清除筛选")}</button>}
        <button className="btn sm" disabled={list.loading} onClick={list.reload}>{t("刷新运行记录")}</button></div>
      <p className="muted small">{t("只显示运行的元数据：操作、状态、时间和错误代码。输入、输出和失败说明属于发起人，平台管理不显示，也不能由此打开业务内容。")}</p>
      <div className="row gap12 wrap" style={{ alignItems: "flex-end" }}>
        <label className="field">{t("状态")}<select className="input" value={view.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">{t("全部状态")}</option><option value="attention">{t("需要关注（失败、结果不明或已停滞）")}</option>
          {RUN_STATES.map((state) => <option key={state} value={state}>{t(STATE_LABEL[state])}</option>)}</select></label>
        <label className="field">{t("应用")}<select className="input" value={view.app} onChange={(e) => set({ app: e.target.value })}>
          <option value="">{t("全部应用")}</option>{apps.map((app) => <option key={app} value={app}>{appName(app)}</option>)}</select></label>
        <label className="field">{t("入口")}<select className="input" value={view.via} onChange={(e) => set({ via: e.target.value })}>
          <option value="">{t("全部入口")}</option>{VIA.map((via) => <option key={via} value={via}>{t(VIA_LABEL[via])}</option>)}</select></label>
        <label className="field grow">{t("运行编号、关联编号或操作名开头")}<input className="input" type="search" maxLength={100} value={q}
          onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      {(view.workspace || view.user) && <p className="notice" role="status">
        {view.user && <>{t("只显示账户 {id} 的运行。", { id: view.user })} </>}
        {view.workspace && <>{t("只显示工作空间 {id} 的运行。", { id: view.workspace })} </>}
        <button className="btn sm" onClick={() => set({ workspace: "", user: "" })}>{t("显示所有账户和工作空间")}</button></p>}
      {list.err && <div className="notice err" role="alert">{list.err} <button className="btn sm" onClick={list.reload}>{t("重试")}</button></div>}
      {list.loading && <p role="status">{t("正在加载运行记录…")}</p>}
      <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={t("运行记录")} aria-busy={list.loading}>
        <thead><tr><th scope="col">{t("操作名")}</th><th scope="col">{t("状态")}</th><th scope="col">{t("入口")}</th>
          <th scope="col">{t("账户")}</th><th scope="col">{t("工作空间")}</th><th scope="col">{t("创建时间")}</th>
          <th scope="col" className="act">{t("详情")}</th></tr></thead>
        <tbody>{items.map((run) => <tr key={run.id}>
          <th scope="row"><span className="mono">{run.op}</span><div className="who2">{appName(run.app)}</div></th>
          <td data-label={t("状态")}><span><StateBadge status={run.status} flagged={run.stalled} />
            {run.stalled && <span className="cell-note">{t("已停滞：超过 {duration} 没有更新", { duration: fmtDuration(staleAfter) })}</span>}
            {run.error_code && <span className="cell-note"><code>{run.error_code}</code></span>}</span></td>
          <td data-label={t("入口")}><span>{label(t, VIA_LABEL, run.via)}{run.client_id && <span className="cell-note mono">{run.client_id}</span>}</span></td>
          <td data-label={t("账户")}>{who(run)}</td>
          <td data-label={t("工作空间")}>{run.workspace_id ? run.workspace_name || run.workspace_id : t("个人（不属于工作空间）")}</td>
          <td data-label={t("创建时间")}>{fmtTime(run.created_at)}</td>
          <td className="act"><button className="btn sm" onClick={() => open(run.id)} aria-label={t("查看运行 {id}", { id: run.id })}>{t("查看")}</button></td>
        </tr>)}
        {!list.loading && !list.err && !items.length && <tr className="empty-row"><td colSpan={7}>
          {filtered ? t("没有符合筛选条件的运行。") : t("还没有任何运行。")}</td></tr>}</tbody>
      </table></div>
      <PageControls offset={list.offset} total={list.data?.total || 0} shown={list.data?.items.length} capped={list.data?.total_capped}
        more={list.data?.more} busy={list.loading || !!list.err} onPage={list.setOffset} />
    </section>
    {view.run && <RunDialog id={view.run} search={search} onClose={close} onOpen={open} onFilter={(changes) => set({ ...changes, run: "" })} />}
  </>;
}

function Row({ name, children }: { name: string; children: ReactNode }) {
  return <><dt>{name}</dt><dd>{children}</dd></>;
}

function DigestNote({ id }: { id: string }) {
  const { t } = useI18n();
  return isCorrelationDigest(id)
    ? <span className="cell-note">{t("调用方自己指定的关联编号，这里只显示它的摘要；用摘要或原文都能搜索到这些运行。")}</span> : null;
}

function RunDialog({ id, search, onClose, onOpen, onFilter }: {
  id: string; search: string; onClose: () => void; onOpen: (id: string) => void;
  onFilter: (changes: Partial<Record<"q" | "user" | "workspace" | "status" | "app" | "via", string>>) => void;
}) {
  const { t } = useI18n();
  const load = useLoad<RunDetail>(`/api/admin/app-runs/${encodeURIComponent(id)}`,
    (signal) => get<RunDetail>(`/api/admin/app-runs/${encodeURIComponent(id)}`, signal));
  const run = load.data?.run.id === id ? load.data.run : null;
  const spent = run ? elapsedSeconds(run.started_at ?? run.created_at, run.finished_at) : null;
  return <Modal title={t("运行详情")} onClose={onClose} size="wide"
    footer={<><span className="grow muted small">{t("平台管理只读取运行的元数据，不能由此取消、重试或打开业务内容。")}</span>
      <button className="btn" onClick={onClose}>{t("关闭")}</button></>}>
    {load.error ? <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{t("重试")}</button></div>
      : !run ? <p role="status">{t("正在读取运行详情…")}</p> : <div className="col gap16">
        <dl className="kv">
          <Row name={t("运行编号")}><span className="mono">{run.id}</span></Row>
          <Row name={t("操作名")}><span className="mono">{run.op}</span> · {t("版本 {n}", { n: run.op_version })} · {appName(run.app)}</Row>
          <Row name={t("状态")}><StateBadge status={run.status} flagged={run.stalled} />
            {run.stalled && <span className="cell-note">{t("已停滞：超过 {duration} 没有更新", { duration: fmtDuration(load.data!.stale_after_s) })}</span>}</Row>
          <Row name={t("错误代码")}>{run.error_code ? <code>{run.error_code}</code> : "—"}</Row>
          <Row name={t("入口")}>{label(t, VIA_LABEL, run.via)} · {label(t, PRINCIPAL_LABEL, run.principal_kind)}
            {run.delegation_kind && <> · {label(t, DELEGATION_LABEL, run.delegation_kind)}</>}</Row>
          {run.client_id && <Row name={t("客户端")}><span className="mono">{run.client_id}</span></Row>}
          <Row name={t("账户")}>{who(run)}{run.user_email && <span className="cell-note mono">{run.user_id}</span>}
            <button className="btn sm" onClick={() => onFilter({ user: run.user_id, workspace: "", q: "" })}>{t("只看这个账户的运行")}</button></Row>
          <Row name={t("工作空间")}>{run.workspace_id ? <>{run.workspace_name || run.workspace_id}
            {run.workspace_name && <span className="cell-note mono">{run.workspace_id}</span>}
            <button className="btn sm" onClick={() => onFilter({ workspace: run.workspace_id!, user: "", q: "" })}>{t("只看这个工作空间的运行")}</button></>
            : t("个人（不属于工作空间）")}</Row>
          <Row name={t("涉及的对象")}>{run.object_id ? <span className="mono">{run.object_kind} · {run.object_id}</span> : "—"}</Row>
          <Row name={t("关联编号")}><span className="mono">{run.correlation_id}</span><DigestNote id={run.correlation_id} /></Row>
          {run.flow_run_id && <Row name={t("所属流程运行")}><Link className="mono" to={runsHref("flow-runs", search, { run: run.flow_run_id })}>{run.flow_run_id}</Link></Row>}
          {run.parent_run_id && run.parent_run_id !== run.flow_run_id && <Row name={t("上一级运行")}>
            <button className="btn sm mono" onClick={() => onOpen(run.parent_run_id!)}>{run.parent_run_id}</button></Row>}
          <Row name={t("创建时间")}>{fmtTime(run.created_at)}</Row>
          <Row name={t("结束时间")}>{run.finished_at ? fmtTime(run.finished_at) : t("尚未结束")}</Row>
          <Row name={t("耗时")}>{spent === null ? "—" : fmtDuration(spent)}</Row>
          <Row name={t("最后更新")}>{fmtTime(run.updated_at)}</Row>
        </dl>
        <section className="col gap8" aria-labelledby="run-related-title">
          <h3 id="run-related-title">{t("同一关联编号的其他运行")}</h3>
          {!load.data!.related.length ? <p className="muted">{t("没有其他运行使用这个关联编号。")}</p>
            : <div className="list-wrap"><table className="gov" aria-label={t("同一关联编号的其他运行")}>
              <thead><tr><th scope="col">{t("操作名")}</th><th scope="col">{t("状态")}</th><th scope="col">{t("入口")}</th>
                <th scope="col">{t("创建时间")}</th><th scope="col" className="act">{t("详情")}</th></tr></thead>
              <tbody>{load.data!.related.map((other) => <tr key={other.id}>
                <th scope="row"><span className="mono">{other.op}</span><div className="who2 mono">{other.id}</div></th>
                <td><StateBadge status={other.status} flagged={other.stalled} />{other.error_code && <span className="cell-note"><code>{other.error_code}</code></span>}</td>
                <td>{label(t, VIA_LABEL, other.via)}</td><td>{fmtTime(other.created_at)}</td>
                <td className="act"><button className="btn sm" onClick={() => onOpen(other.id)} aria-label={t("查看运行 {id}", { id: other.id })}>{t("查看")}</button></td>
              </tr>)}</tbody></table></div>}
          {load.data!.related_truncated && <p className="muted small">{t("只列出最早的 {n} 个；用关联编号在运行记录里搜索可以看到全部。", { n: load.data!.related.length })}
            <button className="btn sm" onClick={() => onFilter({ q: run.correlation_id, user: "", workspace: "", status: "", app: "", via: "" })}>{t("按关联编号搜索")}</button></p>}
        </section>
      </div>}
  </Modal>;
}

// ------------------------------------------------------------------------------------ flow runs
export function FlowRunsTab({ onDenied }: Props) {
  const { t } = useI18n();
  const { view, set, open, close, search } = useView(FLOW_VIEW_KEYS);
  const summary = useAdminData<RunSummary>("/api/admin/app-runs/summary", onDenied);
  const list = useAdminPage<FlowRun>(`/api/admin/flow-runs${qs({ status: view.status, user_id: view.user,
    flow_id: view.flow, q: view.q })}`, onDenied);
  const [q, setQ] = useSearchText(view.q, (text) => set({ q: text }));
  const items = list.loading || list.err ? [] : list.data?.items || [];
  const staleAfter = (list.data as (typeof list.data & { stale_after_s?: number }) | null)?.stale_after_s ?? 600;
  const filtered = !!(view.status || view.user || view.flow || view.q);
  const f = summary.data?.flows;
  return <>
    <section className="card card-b col gap12" aria-labelledby="flows-summary-title">
      <div className="row gap12 wrap"><h2 className="grow" id="flows-summary-title">{t("流程概况")}</h2>
        <button className="btn sm" disabled={summary.loading} onClick={summary.reload}>{t("刷新概况")}</button></div>
      {summary.err && <div className="notice err" role="alert">{summary.err} <button className="btn sm" onClick={summary.reload}>{t("重试")}</button></div>}
      {summary.loading && !f && <p role="status">{t("正在读取流程概况…")}</p>}
      {f && !summary.err && <FlowStats flows={f} time={summary.data!.generated_at} />}
    </section>
    <section className="card card-b col gap12" aria-labelledby="flows-list-title">
      <div className="row gap12 wrap"><h2 className="grow" id="flows-list-title">{t("流程运行记录")}</h2>
        {filtered && <button className="btn sm" onClick={() => set({ status: "", user: "", flow: "", q: "" })}>{t("清除筛选")}</button>}
        <button className="btn sm" disabled={list.loading} onClick={list.reload}>{t("刷新流程运行")}</button></div>
      <p className="muted small">{t("流程属于创建它的人。这里只显示运行的状态、时间和它调用了哪些操作；流程的名称、步骤名称、参数和结果不显示。")}</p>
      <div className="row gap12 wrap" style={{ alignItems: "flex-end" }}>
        <label className="field">{t("状态")}<select className="input" value={view.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="">{t("全部状态")}</option><option value="attention">{t("需要关注（失败、部分成功、受阻、结果不明或已超过期限）")}</option>
          <option value="active">{t("未结束")}</option>
          {FLOW_STATES.map((state) => <option key={state} value={state}>{t(STATE_LABEL[state])}</option>)}</select></label>
        <label className="field grow">{t("流程运行编号或关联编号")}<input className="input" type="search" maxLength={100} value={q}
          onChange={(e) => setQ(e.target.value)} /></label>
      </div>
      {(view.user || view.flow) && <p className="notice" role="status">
        {view.user && <>{t("只显示账户 {id} 的流程运行。", { id: view.user })} </>}
        {view.flow && <>{t("只显示流程 {id} 的运行。", { id: view.flow })} </>}
        <button className="btn sm" onClick={() => set({ user: "", flow: "" })}>{t("显示所有账户和流程")}</button></p>}
      {list.err && <div className="notice err" role="alert">{list.err} <button className="btn sm" onClick={list.reload}>{t("重试")}</button></div>}
      {list.loading && <p role="status">{t("正在加载流程运行…")}</p>}
      <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={t("流程运行记录")} aria-busy={list.loading}>
        <thead><tr><th scope="col">{t("流程运行编号")}</th><th scope="col">{t("状态")}</th><th scope="col">{t("账户")}</th>
          <th scope="col">{t("已调用操作")}</th><th scope="col">{t("创建时间")}</th><th scope="col">{t("截止时间")}</th>
          <th scope="col" className="act">{t("详情")}</th></tr></thead>
        <tbody>{items.map((run) => <tr key={run.id}>
          <th scope="row"><span className="mono">{run.id}</span><div className="who2 mono">{run.flow_id} · {t("版本 {n}", { n: run.version })}</div></th>
          <td data-label={t("状态")}><span><StateBadge status={run.status} flagged={run.overdue || run.stalled} />
            {run.overdue && <span className="cell-note">{t("已超过期限仍未结束")}</span>}
            {run.stalled && <span className="cell-note">{t("已停滞：到期超过 {duration} 仍无执行器处理", { duration: fmtDuration(staleAfter) })}</span>}
            {run.leased && <span className="cell-note">{t("执行器正在处理")}</span>}
            {run.error_code && <span className="cell-note"><code>{run.error_code}</code></span>}</span></td>
          <td data-label={t("账户")}>{who(run)}</td>
          <td data-label={t("已调用操作")}>{formatNumber(run.calls_used)}</td>
          <td data-label={t("创建时间")}>{fmtTime(run.created_at)}</td>
          <td data-label={t("截止时间")}>{fmtTime(run.deadline)}</td>
          <td className="act"><button className="btn sm" onClick={() => open(run.id)} aria-label={t("查看流程运行 {id}", { id: run.id })}>{t("查看")}</button></td>
        </tr>)}
        {!list.loading && !list.err && !items.length && <tr className="empty-row"><td colSpan={7}>
          {filtered ? t("没有符合筛选条件的流程运行。") : t("还没有任何流程运行。")}</td></tr>}</tbody>
      </table></div>
      <PageControls offset={list.offset} total={list.data?.total || 0} shown={list.data?.items.length} capped={list.data?.total_capped}
        more={list.data?.more} busy={list.loading || !!list.err} onPage={list.setOffset} />
    </section>
    {view.run && <FlowDialog id={view.run} search={search} onClose={close} onFilter={(changes) => set({ ...changes, run: "" })} />}
  </>;
}

/** The flow figures of the summary. The parked ones — blocked, outcome unknown, paused — are
 *  counted as of now whatever the window: such a run waits for its owner until they act. */
export function FlowStats({ flows, time }: { flows: RunSummary["flows"]; time: number }) {
  const { t } = useI18n();
  return <>
    <div className="row gap12 wrap">
      <Stat label={t("未结束")} value={flows.active} />
      <Stat label={t("已超过期限仍未结束")} value={flows.overdue} tone="warn" />
      <Stat label={t("已停滞（无执行器处理）")} value={flows.stalled} tone="warn" />
      <Stat label={t("受阻")} value={flows.parked.blocked || 0} tone="warn" />
      <Stat label={t("结果不明")} value={flows.parked.unknown || 0} tone="warn" />
      <Stat label={t("已暂停")} value={flows.parked.paused || 0} />
      <Stat label={t("24 小时内失败")} value={flows.finished.failed || 0} tone="err" />
      <Stat label={t("24 小时内部分成功")} value={flows.finished.partial || 0} tone="warn" />
      <Stat label={t("24 小时内成功")} value={flows.finished.succeeded || 0} />
    </div>
    <p className="muted small">{t("「未结束」是执行器还会继续处理的流程运行；「受阻」「结果不明」「已暂停」的运行停在原处，等所有者继续或取消。这几项和「已超过期限仍未结束」按现在计算；其余按最近 24 小时内结束的流程运行统计。数据时间：{time}。",
      { time: fmtTime(time) })}</p>
  </>;
}

/** The steps of one flow run, by position and by the operation run each one recorded. A step
 *  without one is not necessarily a step that calls nothing: a call that was refused, failed
 *  before its run was kept, or is still executing has none either. */
export function FlowSteps({ steps, search }: { steps: FlowStep[]; search: string }) {
  const { t } = useI18n();
  return <div className="list-wrap"><table className="gov" aria-label={t("步骤")}>
    <thead><tr><th scope="col">{t("顺序")}</th><th scope="col">{t("调用的操作")}</th><th scope="col">{t("状态")}</th>
      <th scope="col">{t("尝试次数")}</th><th scope="col">{t("最后更新")}</th><th scope="col" className="act">{t("操作运行")}</th></tr></thead>
    <tbody>{steps.map((step) => <tr key={step.position}>
      <th scope="row">{step.position}</th>
      <td>{step.op ? <><span className="mono">{step.op}</span><div className="who2">{appName(step.app)}</div></> : t("没有记录到操作运行")}</td>
      <td><StateBadge status={step.status} />{step.error_code && <span className="cell-note"><code>{step.error_code}</code></span>}</td>
      <td>{formatNumber(step.attempt)}</td><td>{fmtTime(step.updated_at)}</td>
      <td className="act">{step.op_run_id ? <Link className="btn sm" to={runsHref("app-runs", search, { run: step.op_run_id })}
        aria-label={t("查看运行 {id}", { id: step.op_run_id })}>{t("查看")}</Link> : "—"}</td>
    </tr>)}</tbody></table></div>;
}

function FlowDialog({ id, search, onClose, onFilter }: {
  id: string; search: string; onClose: () => void; onFilter: (changes: Partial<Record<"user" | "flow" | "q" | "status", string>>) => void;
}) {
  const { t } = useI18n();
  const load = useLoad<FlowDetail>(`/api/admin/flow-runs/${encodeURIComponent(id)}`,
    (signal) => get<FlowDetail>(`/api/admin/flow-runs/${encodeURIComponent(id)}`, signal));
  const run = load.data?.run.id === id ? load.data.run : null;
  return <Modal title={t("流程运行详情")} onClose={onClose} size="wide"
    footer={<><span className="grow muted small">{t("平台管理只读取流程运行的元数据，不能由此取消、重试或查看流程内容。")}</span>
      <button className="btn" onClick={onClose}>{t("关闭")}</button></>}>
    {load.error ? <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{t("重试")}</button></div>
      : !run ? <p role="status">{t("正在读取流程运行详情…")}</p> : <div className="col gap16">
        <dl className="kv">
          <Row name={t("流程运行编号")}><span className="mono">{run.id}</span></Row>
          <Row name={t("流程")}><span className="mono">{run.flow_id}</span> · {t("版本 {n}", { n: run.version })}
            <button className="btn sm" onClick={() => onFilter({ flow: run.flow_id, user: "", q: "", status: "" })}>{t("只看这个流程的运行")}</button></Row>
          <Row name={t("状态")}><StateBadge status={run.status} flagged={run.overdue || run.stalled} />
            {run.overdue && <span className="cell-note">{t("已超过期限仍未结束")}</span>}
            {run.stalled && <span className="cell-note">{t("已停滞：到期超过 {duration} 仍无执行器处理", { duration: fmtDuration(load.data!.stale_after_s) })}</span>}
            {run.leased && <span className="cell-note">{t("执行器正在处理")}</span>}</Row>
          <Row name={t("错误代码")}>{run.error_code ? <code>{run.error_code}</code> : "—"}</Row>
          <Row name={t("账户")}>{who(run)}{run.user_email && <span className="cell-note mono">{run.user_id}</span>}
            <button className="btn sm" onClick={() => onFilter({ user: run.user_id, flow: "", q: "", status: "" })}>{t("只看这个账户的流程运行")}</button></Row>
          <Row name={t("触发方式")}>{run.trigger_id ? <>{t("由触发器启动")} · <span className="mono">{run.trigger_id}</span></> : t("手动或由接口启动")}</Row>
          <Row name={t("已调用操作")}>{formatNumber(run.calls_used)}</Row>
          <Row name={t("关联编号")}><span className="mono">{run.correlation_id}</span><DigestNote id={run.correlation_id} />
            <Link className="btn sm" to={runsHref("app-runs", search, { q: run.correlation_id })}>{t("查看这次流程运行的操作")}</Link></Row>
          <Row name={t("创建时间")}>{fmtTime(run.created_at)}</Row>
          <Row name={t("截止时间")}>{fmtTime(run.deadline)}</Row>
          <Row name={t("结束时间")}>{run.finished_at ? fmtTime(run.finished_at) : t("尚未结束")}</Row>
          <Row name={t("最后更新")}>{fmtTime(run.updated_at)}</Row>
        </dl>
        <section className="col gap8" aria-labelledby="flow-steps-title">
          <h3 id="flow-steps-title">{t("步骤")}</h3>
          {!load.data!.steps.length ? <p className="muted">{t("这次流程运行还没有开始任何步骤。")}</p>
            : <FlowSteps steps={load.data!.steps} search={search} />}
        </section>
        <section className="col gap8" aria-labelledby="flow-history-title">
          <h3 id="flow-history-title">{t("运行经过")}</h3>
          {!load.data!.history.length ? <p className="muted">{t("还没有记录。")}</p>
            : <div className="list-wrap"><table className="gov" aria-label={t("运行经过")}>
              <thead><tr><th scope="col">{t("时间")}</th><th scope="col">{t("事件")}</th><th scope="col">{t("状态")}</th>
                <th scope="col">{t("错误代码")}</th></tr></thead>
              <tbody>{load.data!.history.map((event) => <tr key={event.seq}>
                <td>{fmtTime(event.created_at)}</td><td>{label(t, HISTORY_LABEL, event.type)}</td>
                <td>{label(t, STATE_LABEL, event.status)}</td><td>{event.error_code ? <code>{event.error_code}</code> : "—"}</td>
              </tr>)}</tbody></table></div>}
          {load.data!.history_truncated && <p className="muted small">{t("只列出最近的 {n} 条。", { n: load.data!.history.length })}</p>}
        </section>
      </div>}
  </Modal>;
}
