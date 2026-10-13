import { useEffect, useId, useRef, useState } from "react";
import { qs } from "@synwevo/auth-client";
import { fmtTime, formatNumber, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useDebounced } from "../lib/hooks";
import { useAdminData, useAdminPage } from "./data";
import { PageControls } from "./ListControls";
import { WORKSPACE_KIND, WORKSPACE_STATUS } from "./labels";
import { mayChangeAppPlatform, mayChangeAppWorkspace, type Held } from "./access";
import { AppChangeDialog, CHANGE_LABELS, DONE_TEXT, POLICY_MEANING, appPolicyLabel, appStatusLabel, changeKey, workspaceScoped,
  type AppChange, type AppChangeResult, type AppWorkspace, type CatalogueApp } from "./AppChangeDialog";
import "./messages";
import "./apps.messages";

/**
 * B1/B2: the application catalogue as platform administration governs it — which applications
 * exist, whether each is on for the platform, who may use it (its access policy), and in which
 * workspaces it is enabled (`GET /api/admin/apps`, `platform.apps.view`). Counts and metadata
 * only: no business content, and seeing an application here is not being allowed to use it.
 *
 * Every change goes through `AppChangeDialog`: an impact preview, a
 * written reason, fresh identity proof, one idempotency key per preview. This is the catalogue;
 * the applications' run records are the separate run pages (RunsTab.tsx).
 */

export type AppsList = { items: CatalogueApp[] };
/** The platform changes a row offers: the server's own list (`changes`) — switch it off or on, and
 *  tighten or loosen the policy of an application decided per workspace. Without that list, the
 *  same rule from the row. */
export function rowChanges(app: Pick<CatalogueApp, "status" | "access_policy" | "scope" | "changes">): AppChange[] {
  if (Array.isArray(app.changes)) return app.changes;
  const out: AppChange[] = [app.status === "active" ? "disable" : "enable"];
  if (workspaceScoped(app)) out.push(app.access_policy === "explicit" ? "policy_members" : "policy_explicit");
  return out;
}
/** Why a switched-off application offers no "启用应用": the deployment does not release it, so its
 *  operations do not exist and its next start would switch it off again (`app_not_released`). */
export const unreleased = (app: Pick<CatalogueApp, "status" | "changes">) =>
  app.status !== "active" && Array.isArray(app.changes) && !app.changes.includes("enable");
/** Whether a workspace row offers switching the application on or off, and why not where it does
 *  not: the historical data's workspace belongs to its claim flow (`legacy_workspace`), and a
 *  workspace that is not active cannot have an application switched on (`workspace_unavailable`). */
export function workspaceOffer(w: Pick<AppWorkspace, "status" | "enabled">): { change: AppChange | null; hint?: string } {
  if (w.status === "pending_claim") return { change: null, hint: "待认领的历史数据由认领流程管理，不能在这里启用或停用应用。" };
  if (w.enabled) return { change: "disable" };
  if (w.status !== "active") return { change: null, hint: "工作空间不可用时不能为它启用应用。" };
  return { change: "enable" };
}

/** The live region and the notice inside it: present from the first render (a region inserted
 *  together with its text is not announced by many screen readers), focused when a change ends,
 *  because the button that opened the dialog may be gone or changed by then. */
function useOutcome() {
  const [notice, setNotice] = useState("");
  const [focus, setFocus] = useState(0);
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (focus) ref.current?.focus(); }, [focus]);
  const region = <div role="status" aria-live="polite">{notice && <p className="notice ok" ref={ref} tabIndex={-1}>{notice}</p>}</div>;
  return { region, say: (message: string) => { setNotice(message); setFocus((n) => n + 1); } };
}
function doneText(result: AppChangeResult, change: AppChange, app: { name: string }, workspace?: { name: string }) {
  const key = changeKey(change, workspace ? "workspace" : "platform");
  return `${tr(DONE_TEXT[key], { app: result.app?.name || app.name, workspace: result.workspace?.name || workspace?.name || "" })} ${tr("操作编号：{id}", { id: result.operation_id })}`;
}

export function AppsTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  useI18n();
  const toast = useToast();
  const { data, err, loading, reload } = useAdminData<AppsList>("/api/admin/apps", onDenied);
  // Per-workspace switches need platform.apps.manage; the platform-scope ones (on or off
  // platform-wide, the access policy) also platform.config.manage (access.ts).
  const canWorkspace = mayChangeAppWorkspace(held), canPlatform = mayChangeAppPlatform(held);
  const items = loading || err ? [] : data?.items || [];
  const [change, setChange] = useState<{ app: CatalogueApp; change: AppChange } | null>(null);
  const [drawer, setDrawer] = useState<CatalogueApp | null>(null);
  const outcome = useOutcome();
  const busy = loading || !!err;
  return <section className="card card-b col gap12" aria-labelledby="platform-apps">
    <div className="row gap12 wrap"><h2 className="grow" id="platform-apps">{tr("应用目录")}</h2>
      <button type="button" className="btn sm" disabled={loading} onClick={reload}>{tr("刷新应用目录")}</button></div>
    <p className="muted small">{tr("这里列出平台上登记的应用：是否在全平台启用、谁可以使用（访问策略）、在哪些工作空间开通，以及当前可以使用的人数。只显示数量和元数据，不显示任何业务内容。")}</p>
    <p className="muted small">{tr("停用应用或收紧访问策略不会删除任何数据。在这里看到应用不等于有权使用它：每次访问仍按成员关系、应用开通状态和授权判断。")}</p>
    {!canWorkspace && <p className="notice" role="note">{tr("你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")}</p>}
    {canWorkspace && !canPlatform && <p className="notice" role="note">{tr("平台范围的变更（全平台启用、停用和访问策略）需要最高管理员；你可以在「按工作空间」里为单个工作空间启用或停用应用。")}</p>}
    {outcome.region}
    {err && <div className="notice err" role="alert">{err} <button type="button" className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载应用目录…")}</p>}
    <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("应用目录")} aria-busy={loading}>
      <thead><tr>
        <th scope="col">{tr("应用")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("访问策略")}</th>
        <th scope="col">{tr("已开通的工作空间")}</th><th scope="col">{tr("当前可使用的人")}</th>
        <th scope="col">{tr("明确授权")}</th><th scope="col">{tr("单独撤销访问")}</th><th scope="col">{tr("排队或运行中的任务")}</th>
        <th scope="col" className="act">{tr("操作")}</th>
      </tr></thead>
      <tbody>{items.map((app) => <tr key={app.id}>
        <th scope="row">{app.name}<div className="who2 mono">{app.id}</div>
          {app.launch_url && <span className="cell-note">{tr("入口地址：{url}", { url: app.launch_url })}</span>}</th>
        <td data-label={tr("状态")}><span className={`badge ${app.status === "active" ? "ok" : "warn"}`}>{appStatusLabel(app.status)}</span></td>
        <td data-label={tr("访问策略")}>{workspaceScoped(app) ? <div>{appPolicyLabel(app.access_policy)}
          {POLICY_MEANING[app.access_policy] && <span className="cell-note">{tr(POLICY_MEANING[app.access_policy])}</span>}</div>
          : <div>{tr("每个有效账户")}<span className="cell-note">{tr("这个应用不按工作空间开通或授权：每个有效账户都可以使用，只能在全平台启用或停用。")}</span></div>}</td>
        <td data-label={tr("已开通的工作空间")}>{formatNumber(app.workspaces_enabled)}</td>
        <td data-label={tr("当前可使用的人")}>{formatNumber(app.members_with_access)}</td>
        <td data-label={tr("明确授权")}>{formatNumber(app.explicit_grants)}</td>
        <td data-label={tr("单独撤销访问")}>{formatNumber(app.explicit_revocations)}</td>
        <td data-label={tr("排队或运行中的任务")}>{app.running_jobs === null ? <span className="muted">{tr("不统计")}</span> : formatNumber(app.running_jobs)}</td>
        <td className="act" data-label={tr("操作")}><div className="row gap4 wrap">
          {workspaceScoped(app) && <button type="button" className="btn sm" disabled={busy} aria-label={tr("按工作空间查看：{p0}", { p0: app.name })}
            onClick={() => setDrawer(app)}>{tr("按工作空间")}</button>}
          {canPlatform && rowChanges(app).map((c) => <button key={c} type="button" className={`btn sm${c === "disable" || c === "policy_explicit" ? " danger" : ""}`}
            disabled={busy} aria-label={tr("{action}：{p0}", { action: tr(CHANGE_LABELS[c]), p0: app.name })}
            onClick={() => setChange({ app, change: c })}>{tr(CHANGE_LABELS[c])}</button>)}
          {canPlatform && unreleased(app) && <span className="cell-note">{tr("当前部署没有发布这个应用，不能在这里启用。")}</span>}
        </div></td>
      </tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={9}>{tr("还没有登记任何应用。")}</td></tr>}</tbody>
    </table></div>
    {drawer && <AppWorkspacesDrawer app={drawer} held={held} onDenied={onDenied} onClose={() => setDrawer(null)} onChanged={reload} />}
    {change && <AppChangeDialog app={change.app} change={change.change} onClose={() => setChange(null)}
      onDone={(result) => {
        const message = doneText(result, change.change, change.app);
        setChange(null); reload(); outcome.say(message); toast(message, "ok");
      }} />}
  </section>;
}

/** "按工作空间": every workspace with this application enabled or not, how many of its members may
 *  use it, and — with `platform.apps.manage` — switching it on or off for one workspace. */
export function AppWorkspacesDrawer({ app, held, onDenied, onClose, onChanged }: {
  app: CatalogueApp; held: Held; onDenied: () => void; onClose: () => void; onChanged: () => void;
}) {
  useI18n();
  const toast = useToast();
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const searchId = useId();
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<AppWorkspace>(
    `/api/admin/apps/${encodeURIComponent(app.id)}/workspaces${qs({ q: query })}`, onDenied);
  const canManage = mayChangeAppWorkspace(held);
  const items = loading || err ? [] : data?.items || [];
  const [change, setChange] = useState<{ workspace: AppWorkspace; change: AppChange } | null>(null);
  const outcome = useOutcome();
  const busy = loading || !!err;
  return <Modal title={tr("「{app}」的工作空间", { app: app.name })} size="wide" onClose={onClose}>
    <div className="col gap12">
      <p className="muted small">{tr("每个工作空间是否开通了这个应用，以及其中可以使用它的人数。只显示数量，不显示成员或内容。为一个空间停用不影响其他空间，也不删除任何数据。")}</p>
      {!canManage && <p className="notice" role="note">{tr("你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。")}</p>}
      <div className="field"><label htmlFor={searchId}>{tr("搜索工作空间")}</label>
        <input id={searchId} type="search" className="input" value={q} maxLength={100} aria-describedby={`${searchId}-hint`}
          onChange={(e) => setQ(e.target.value)} data-autofocus />
        <span id={`${searchId}-hint`} className="tiny muted">{tr("输入工作空间名称的一部分，或完整的工作空间编号。")}</span></div>
      {outcome.region}
      {err && <div className="notice err" role="alert">{err} <button type="button" className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
      {loading && <p role="status">{tr("正在加载工作空间…")}</p>}
      <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={tr("「{app}」的工作空间", { app: app.name })} aria-busy={loading}>
        <thead><tr>
          <th scope="col">{tr("工作空间")}</th><th scope="col">{tr("空间状态")}</th><th scope="col">{tr("这个应用")}</th>
          <th scope="col">{tr("成员人数")}</th><th scope="col" className="act">{tr("操作")}</th>
        </tr></thead>
        <tbody>{items.map((w) => { const offer = workspaceOffer(w);
          return <tr key={w.id}>
            <th scope="row">{w.name}
              <div className="who2">{WORKSPACE_KIND[w.kind] || w.kind}{w.organization_id && <> · {tr("所属组织 {id}", { id: w.organization_id })}</>}</div></th>
            <td data-label={tr("空间状态")}>{w.status === "active" ? WORKSPACE_STATUS.active
              : <span className="badge warn">{WORKSPACE_STATUS[w.status] || w.status}</span>}</td>
            <td data-label={tr("这个应用")}><div>{w.enabled ? <span className="badge ok">{tr("已开通")}</span> : <span className="badge">{tr("未开通")}</span>}
              {w.enabled && w.enabled_at && <span className="cell-note">{tr("开通于 {time}", { time: fmtTime(w.enabled_at) })}</span>}</div></td>
            <td data-label={tr("成员人数")}><div>{formatNumber(w.members)}
              <span className="cell-note">{tr("其中可使用 {count}", { count: w.members_with_access })}</span></div></td>
            <td className="act" data-label={tr("操作")}><div className="row gap4 wrap">
              {canManage && offer.change && <button type="button" className={`btn sm${offer.change === "disable" ? " danger" : ""}`} disabled={busy}
                aria-label={tr("{action}：{p0}", { action: tr(CHANGE_LABELS[changeKey(offer.change, "workspace")]), p0: w.name })}
                onClick={() => setChange({ workspace: w, change: offer.change! })}>{tr(CHANGE_LABELS[changeKey(offer.change, "workspace")])}</button>}
              {canManage && offer.hint && <span className="cell-note">{tr(offer.hint)}</span>}
            </div></td>
          </tr>; })}
        {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={5}>{tr("没有匹配的工作空间。")}</td></tr>}</tbody>
      </table></div>
      <PageControls offset={offset} total={data?.total ?? offset + items.length} busy={busy} onPage={setOffset} />
    </div>
    {change && <AppChangeDialog app={app} change={change.change} workspace={change.workspace} onClose={() => setChange(null)}
      onDone={(result) => {
        const message = doneText(result, change.change, app, change.workspace);
        setChange(null); reload(); onChanged(); outcome.say(message); toast(message, "ok");
      }} />}
  </Modal>;
}
