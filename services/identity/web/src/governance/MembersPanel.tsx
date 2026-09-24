import { t as tr } from "@synwevo/ui";
import "./messages";
import { useEffect, useState } from "react";
import { patch, post, useSession } from "@synwevo/auth-client";
import { useToast } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { BulkResultDialog, bulkToast, type BulkResponse } from "./BulkResult";
import { PageControls, SelectPage } from "./ListControls";
import { RowMenu, type RowMenuItem } from "./RowMenu";
import { escapeClears, selectionKeys, useSelection } from "../components/selection";
import { ACCOUNT_STATUS, canExport, entitlementLabel, errText, ROLES, roleLabel } from "./labels";
import { BulkRoleDialog, LeaveDialog, RemoveDialog, RevokeEntitlementDialog, RoleDialog, TransferDialog, removedSummary } from "./dialogs";
import { memberName, type Member, type MemberPage, type Removed } from "./types";

export interface Filters { q: string; role: string; status: string; entitlement: string }
type Action = { kind: "role" | "remove" | "revoke" | "grant" | "export" | "transfer"; members: Member[] } | { kind: "leave" };

export function MembersPanel({ wid, page, loading, error, filters, setFilters, offset, setOffset, reload, announce, onInvitesChanged }: {
  wid: string; page: MemberPage; loading: boolean; error: unknown; filters: Filters;
  setFilters: (f: Filters) => void; offset: number; setOffset: (n: number) => void;
  reload: () => void; announce: (t: string) => void; onInvitesChanged: () => void;
}) {
  const { refresh } = useSession();
  const toast = useToast();
  const [action, setAction] = useState<Action | null>(null);
  const [result, setResult] = useState<{ response: BulkResponse; names: Record<string, string>; title: string } | null>(null);
  const blocked = loading || !!error;
  const selection = useSelection(page.items.filter((m) => m.manageable).map((m) => m.user_id));
  const filterKey = JSON.stringify(filters);
  useEffect(() => { selection.clear(); }, [wid, offset, filterKey, selection.clear]);
  useEffect(() => {
    if (!loading && !error && offset && !page.items.length) setOffset(Math.max(0, Math.ceil(page.total / page.limit) * page.limit - page.limit));
  }, [loading, error, offset, page, setOffset]);
  const chosen = page.items.filter((m) => selection.ids.includes(m.user_id));
  const frozen = page.workspace.status !== "active";
  const base = `/api/auth/workspaces/${encodeURIComponent(wid)}`;
  const close = () => setAction(null);
  const done = (message: string) => {
    close(); selection.clear(); reload(); onInvitesChanged(); announce(message); toast(message, "ok");
  };
  const bulkDone = (response: BulkResponse, title: string, ms: Member[]) => {
    setResult({ response, title, names: Object.fromEntries(ms.map((m) => [m.user_id, `${memberName(m)} (${m.email})`])) });
    close(); selection.clear(); reload(); onInvitesChanged();
    const [message, tone] = bulkToast(response, title); announce(message); toast(message, tone);
  };
  const begin = (kind: Exclude<Action["kind"], "leave">, ms: Member[]) => setAction({ kind, members: ms });
  const menu = (m: Member): RowMenuItem[] => {
    const hint = m.is_self ? tr("不能修改自己的权限，请使用退出空间或移交所有权") : tr("不能管理同级或更高角色的成员");
    return [
      { label: tr("修改角色"), disabled: !m.manageable || blocked, hint, onSelect: () => begin("role", [m]) },
      { label: m.role !== "viewer" ? tr("导出随角色授予") : m.can_export ? tr("关闭导出") : tr("允许导出"), disabled: !m.manageable || blocked || m.role !== "viewer" || (frozen && !m.can_export),
        hint: m.role !== "viewer" ? tr("编辑及以上角色自带导出权限；需先降低角色") : frozen && !m.can_export ? tr("空间冻结期间不能增加权限") : hint, onSelect: () => begin("export", [m]) },
      ...(page.app ? [
        { label: tr("授予应用使用资格"), disabled: !m.manageable || blocked || frozen || m.entitlement === "active",
          hint: frozen ? tr("空间冻结期间不能授予") : m.entitlement === "active" ? tr("已经授予") : hint, onSelect: () => begin("grant", [m]) },
        { label: tr("撤销应用使用资格"), danger: true, disabled: !m.manageable || blocked || m.entitlement === "revoked",
          hint: m.entitlement === "revoked" ? tr("已经撤销") : hint, onSelect: () => begin("revoke", [m]) },
      ] : []),
      { label: tr("移除成员"), danger: true, disabled: !m.manageable || blocked, hint, onSelect: () => begin("remove", [m]) },
      ...(page.caller.role === "owner" && !m.is_self ? [{ label: tr("移交所有权"), disabled: blocked || frozen || m.account_status !== "active",
        hint: frozen ? tr("空间冻结期间不能移交") : tr("接收方账户必须正常"), onSelect: () => begin("transfer", [m]) }] : []),
    ];
  };
  const ms = action && "members" in action ? action.members : [];

  return <div className="col gap12" onKeyDown={(e) => escapeClears(e, selection, announce)}>
    <div className="filters">
      <label className="field grow">{tr("搜索成员")}<input type="search" className="input" value={filters.q} maxLength={100}
        placeholder={tr("邮箱或姓名")} onChange={(e) => setFilters({ ...filters, q: e.target.value })} /></label>
      <label className="field">{tr("角色")}<select aria-label={tr("角色")} className="input" value={filters.role} onChange={(e) => setFilters({ ...filters, role: e.target.value })}>
        <option value="">{tr("全部角色")}</option>{ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}</select></label>
      <label className="field">{tr("账户状态")}<select aria-label={tr("账户状态")} className="input" value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
        <option value="">{tr("全部状态")}</option>{Object.entries(ACCOUNT_STATUS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select></label>
      {page.app && <label className="field">{tr("应用使用资格")}<select aria-label={tr("应用使用资格")} className="input" value={filters.entitlement}
        onChange={(e) => setFilters({ ...filters, entitlement: e.target.value })}>
        <option value="">{tr("全部资格")}</option><option value="active">{tr("已授予")}</option><option value="revoked">{tr("已撤销")}</option><option value="none">{tr("未单独设置")}</option>
      </select></label>}
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新")}</button>
      <button className="btn ghost sm" disabled={blocked} onClick={() => setAction({ kind: "leave" })}>{tr("退出空间")}</button>
    </div>
    {!!error && <div className="notice err" role="alert">{errText(error)} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在更新成员列表…")}</p>}
    <div className="list-wrap" aria-busy={loading}>
      <table className="gov" aria-label={tr("工作空间成员")} onKeyDown={(e) => selectionKeys(e, selection)}>
        <thead><tr><th className="pick"><SelectPage selection={selection} disabled={blocked} /></th><th scope="col">{tr("成员")}</th>
          <th scope="col">{tr("角色")}</th><th scope="col">{tr("导出")}</th><th scope="col">{tr("应用使用资格")}</th><th scope="col">{tr("操作")}</th></tr></thead>
        <tbody>{page.items.map((m) => {
          const entitlement = entitlementLabel(m.entitlement, page.app?.access_policy);
          return <tr key={m.user_id} className={selection.sel.has(m.user_id) ? "sel" : ""}>
            <td className="pick"><input type="checkbox" data-pick={m.user_id} checked={selection.ids.includes(m.user_id)}
              disabled={!m.manageable || blocked} aria-label={tr("选择 {p0}", {p0: m.email})} onChange={() => {}}
              onClick={(e) => selection.pick(m.user_id, { shift: e.shiftKey })} /></td>
            <th scope="row"><span className="who">{memberName(m)}{m.is_self && tr("（你）")}</span><div className="who2">{m.email}</div>
              {m.account_status !== "active" && <span className="badge warn">{ACCOUNT_STATUS[m.account_status] || m.account_status}</span>}</th>
            <td>{roleLabel(m.role)}</td><td>{canExport(m.role, m.can_export) ? tr("允许") : tr("关闭")}{m.role !== "viewer" && <span className="cell-note">{tr("随角色授予")}</span>}</td>
            <td>{page.app ? <><span className={`badge ${entitlement.tone}`}>{entitlement.text}</span>
              {(frozen || m.account_status !== "active" || page.app.status !== "active") && <span className="cell-note">{tr("账户、空间或应用当前不可用")}</span>}</> : tr("未配置应用")}</td>
            <td className="act"><RowMenu label={tr("{p0} 的操作", {p0: m.email})} items={menu(m)} /></td>
          </tr>;
        })}
        {!page.items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的成员。试试其他搜索条件。")}</td></tr>}</tbody>
      </table>
    </div>
    <PageControls offset={offset} total={page.total} limit={page.limit} busy={blocked} onPage={setOffset} />
    {selection.count > 0 && <div className="acct-bulk" role="region" aria-label={tr("批量成员操作")}>
      <span className="count" role="status">{tr("已选{p0}位（仅本页）", {p0: selection.count})}</span>
      <button className="btn sm" disabled={blocked} onClick={() => begin("role", chosen)}>{tr("修改角色")}</button>
      {page.app && <><button className="btn sm" disabled={blocked || frozen} onClick={() => begin("grant", chosen)}>{tr("授予应用使用资格")}</button>
        <button className="btn sm" disabled={blocked} onClick={() => begin("revoke", chosen)}>{tr("撤销应用使用资格")}</button></>}
      <button className="btn danger sm" disabled={blocked} onClick={() => begin("remove", chosen)}>{tr("移除成员")}</button>
      <button className="btn ghost sm" onClick={selection.clear}>{tr("清除选择")}</button>
    </div>}
    {action?.kind === "role" && (ms.length === 1
      ? <RoleDialog wid={wid} m={ms[0]} callerRole={page.caller.role} ws={page.workspace} onClose={close} onDone={() => done(tr("成员角色已更新"))} />
      : <BulkRoleDialog wid={wid} ms={ms} callerRole={page.caller.role} ws={page.workspace} onClose={close} onDone={(r) => bulkDone(r, tr("修改角色"), ms)} />)}
    {action?.kind === "remove" && <RemoveDialog wid={wid} ms={ms} ws={page.workspace} app={page.app} onClose={close}
      onDone={(r) => r.bulk ? bulkDone(r.bulk, tr("移除成员"), ms) : done(removedSummary(r.single))} />}
    {action?.kind === "revoke" && page.app && <RevokeEntitlementDialog wid={wid} ms={ms} ws={page.workspace} app={page.app} onClose={close}
      onDone={(r) => r.bulk ? bulkDone(r.bulk, tr("撤销使用资格"), ms) : done(tr("已撤销应用使用资格"))} />}
    {action?.kind === "grant" && page.app && <ActionDialog title={tr("授予应用使用资格")} confirmLabel={tr("授予使用资格")} reason="optional"
      impact={<>{tr("{members} 将获得在「{space}」使用 {app} 的资格。账户、空间和应用仍须正常可用。", {members: ms.map(memberName).join(", "), space: page.workspace.name, app: page.app.name})}</>}
      run={(reason) => post<BulkResponse>(`${base}/members/entitlements`, { app_id: page.app!.id, changes: ms.map((m) => ({ user_id: m.user_id, grant: true })), reason })}
      onClose={close} onDone={(r) => bulkDone(r, tr("授予使用资格"), ms)} />}
    {action?.kind === "export" && <ActionDialog title={ms[0].can_export ? tr("关闭导出") : tr("允许导出")} confirmLabel={tr("更新导出权限")} reason="optional"
      impact={<>{tr("{p0}在「{p1}」的导出权限将{p2}，角色不变。", {p0: memberName(ms[0]), p1: page.workspace.name, p2: ms[0].can_export ? tr("关闭") : tr("开启")})}</>}
      run={(reason) => patch(`${base}/members/${encodeURIComponent(ms[0].user_id)}`, { can_export: !ms[0].can_export, reason })}
      onClose={close} onDone={() => done(tr("导出权限已更新"))} />}
    {action?.kind === "transfer" && <TransferDialog wid={wid} m={ms[0]} ws={page.workspace} onClose={close}
      onDone={() => { done(tr("所有权已移交")); void refresh(); }} />}
    {action?.kind === "leave" && <LeaveDialog wid={wid} wsName={page.workspace.name}
      soleOwner={page.caller.role === "owner" && page.active_owner_count <= 1} onClose={close}
      onDone={() => { window.location.assign("/account"); }} />}
    {result && <BulkResultDialog title={result.title} response={result.response} labelOf={(id) => result.names[id] || id}
      detailOf={(r) => r.removed ? removedSummary(r.removed as Removed) : tr("完成")}
      onClose={() => setResult(null)} onSelectFailed={selection.replace} />}
  </div>;
}
