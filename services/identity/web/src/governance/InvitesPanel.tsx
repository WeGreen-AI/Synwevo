import { t as tr } from "@synwevo/ui";
import "./messages";
import { useEffect, useState } from "react";
import { patch, post } from "@synwevo/auth-client";
import { fmtTime, useToast } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { BulkResultDialog, bulkToast, type BulkResponse } from "./BulkResult";
import { PageControls, SelectPage } from "./ListControls";
import { RowMenu } from "./RowMenu";
import { escapeClears, selectionKeys, useSelection } from "../components/selection";
import { canExport as hasExport, errText, INVITE_STATE, MAIL_STATE, ROLE_RANK, ROLES, roleLabel, type Role } from "./labels";
import { assignable } from "./dialogs";
import type { Invitation, InvitationList, WorkspaceInfo } from "./types";

type Action = { kind: "create" } | { kind: "edit"; invitation: Invitation } | { kind: "revoke" | "resend"; invitations: Invitation[] };
const PAGE = 50;

export function InvitesPanel({ wid, ws, callerRole, list, loading, error, reload, announce }: {
  wid: string; ws: WorkspaceInfo; callerRole: string; list: InvitationList | null;
  loading: boolean; error: unknown; reload: () => void; announce: (s: string) => void;
}) {
  const toast = useToast();
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState<Action | null>(null);
  const [result, setResult] = useState<{ response: BulkResponse; names: Record<string, string> } | null>(null);
  const blocked = loading || !!error;
  const frozen = ws.status !== "active";
  const canManage = (i: Invitation) => ROLE_RANK[i.role as Role] <= ROLE_RANK[callerRole as Role];
  const filtered = (list?.items || []).filter((i) => i.email_key.toLowerCase().includes(q.trim().toLowerCase())
    && (!state || (state === "mail_failed" ? i.state === "pending" && i.mail === "failed" : i.state === state)));
  const rows = filtered.slice(offset, offset + PAGE);
  const selection = useSelection(rows.filter((i) => canManage(i) && (i.state === "pending" || i.state === "expired")).map((i) => i.id));
  useEffect(() => { selection.clear(); }, [q, state, offset, wid, selection.clear]);
  useEffect(() => {
    if (offset && offset >= filtered.length) setOffset(Math.max(0, Math.ceil(filtered.length / PAGE) * PAGE - PAGE));
  }, [offset, filtered.length]);
  const chosen = rows.filter((i) => selection.ids.includes(i.id));
  const complete = (response: BulkResponse, names: Record<string, string>) => {
    setAction(null); selection.clear(); setResult({ response, names }); reload();
    const [message, tone] = bulkToast(response, tr("邀请操作")); toast(message, tone); announce(message);
  };
  const close = () => setAction(null);

  return <div className="col gap12" onKeyDown={(e) => escapeClears(e, selection, announce)}>
    <div className="filters">
      <label className="field grow">{tr("搜索邀请邮箱")}<input className="input" type="search" value={q}
        onChange={(e) => { setQ(e.target.value); setOffset(0); }} /></label>
      <label className="field">{tr("邀请状态")}<select aria-label={tr("邀请状态")} className="input" value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}>
        <option value="">{tr("全部状态")}</option>{Object.entries(INVITE_STATE).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        <option value="mail_failed">{tr("待接受且发送失败")}</option></select></label>
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新邀请")}</button>
      <button className="btn primary" disabled={blocked || !list || frozen} title={frozen ? tr("工作空间冻结期间不能邀请") : undefined}
        onClick={() => setAction({ kind: "create" })}>{tr("邀请成员")}</button>
    </div>
    <p className="small muted">{tr("对方接受邀请后才成为成员；重发会让旧链接失效。")}</p>
    {!!error && <div className="notice err" role="alert">{errText(error)} <button className="btn sm" onClick={reload}>{tr("重试邀请列表")}</button></div>}
    {loading && <p role="status">{tr("正在加载邀请…")}</p>}
    {list && <>
      <div className="list-wrap" aria-busy={loading}>
        <table className="gov" aria-label={tr("工作空间邀请")} onKeyDown={(e) => selectionKeys(e, selection)}>
          <thead><tr><th className="pick"><SelectPage selection={selection} disabled={blocked} /></th><th scope="col">{tr("邮箱")}</th>
            <th scope="col">{tr("角色 / 导出")}</th><th scope="col">{tr("邀请状态")}</th><th scope="col">{tr("邮件状态")}</th><th scope="col">{tr("有效期至")}</th><th scope="col">{tr("操作")}</th></tr></thead>
          <tbody>{rows.map((i) => {
            const editable = canManage(i) && i.state === "pending";
            const repeatable = canManage(i) && (i.state === "pending" || i.state === "expired");
            const hint = !canManage(i) ? tr("不能管理比自己角色更高的邀请") : tr("邀请已接受、已拒绝、已撤销或已过期");
            return <tr key={i.id} className={selection.ids.includes(i.id) ? "sel" : ""}>
              <td className="pick"><input type="checkbox" data-pick={i.id} checked={selection.ids.includes(i.id)}
                disabled={blocked || !repeatable} aria-label={tr("选择邀请 {p0}", {p0: i.email_key})} onChange={() => {}}
                onClick={(e) => selection.pick(i.id, { shift: e.shiftKey })} /></td>
              <th scope="row" className="who">{i.email_key}<span className="cell-note">{tr("邀请人：{p0}", {p0: i.inviter || i.invited_by_email || tr("无记录")})}</span></th>
              <td>{roleLabel(i.role)} / {hasExport(i.role, i.can_export) ? tr("允许导出") : tr("不允许导出")}</td>
              <td>{INVITE_STATE[i.state]}</td><td>{MAIL_STATE[i.mail] || i.mail}
                {i.mail === "failed" && <span className="cell-note err">{tr("发送失败；可重发或联系管理员。")}</span>}</td>
              <td>{fmtTime(i.expires_at)}</td>
              <td className="act"><RowMenu label={tr("{p0} 的邀请操作", {p0: i.email_key})} items={[
                { label: tr("修改邀请"), disabled: blocked || !editable, hint, onSelect: () => setAction({ kind: "edit", invitation: i }) },
                { label: tr("重发邀请"), disabled: blocked || !repeatable || frozen, hint: frozen ? tr("工作空间冻结期间不能重发") : hint,
                  onSelect: () => setAction({ kind: "resend", invitations: [i] }) },
                { label: tr("撤销邀请"), disabled: blocked || !repeatable, hint, danger: true,
                  onSelect: () => setAction({ kind: "revoke", invitations: [i] }) },
              ]} /></td>
            </tr>;
          })}
          {!rows.length && <tr className="empty-row"><td colSpan={7}>{tr("没有匹配的邀请。")}</td></tr>}</tbody>
        </table>
      </div>
      <PageControls offset={offset} total={filtered.length} busy={blocked} onPage={setOffset} />
    </>}
    {selection.count > 0 && <div className="acct-bulk" role="region" aria-label={tr("批量邀请操作")}>
      <span className="count" role="status">{tr("已选{p0}项（仅本页）", {p0: selection.count})}</span>
      <button className="btn sm" disabled={blocked || frozen} onClick={() => setAction({ kind: "resend", invitations: chosen })}>{tr("重发邀请")}</button>
      <button className="btn danger sm" disabled={blocked} onClick={() => setAction({ kind: "revoke", invitations: chosen })}>{tr("撤销邀请")}</button>
      <button className="btn ghost sm" onClick={selection.clear}>{tr("清除选择")}</button>
    </div>}
    {action?.kind === "create" && <CreateInvites wid={wid} ws={ws} callerRole={callerRole} onClose={close}
      onDone={(r) => complete(r, {})} />}
    {action?.kind === "edit" && <EditInvite invitation={action.invitation} callerRole={callerRole} frozen={frozen} onClose={close}
      onDone={() => { close(); reload(); toast(tr("邀请已更新，链接不变"), "ok"); announce(tr("邀请已更新")); }} />}
    {(action?.kind === "resend" || action?.kind === "revoke") && <InvitationAction action={action} wid={wid} onClose={close}
      onDone={(r) => complete(r, Object.fromEntries(action.invitations.map((i) => [i.id, i.email_key])))} />}
    {result && <BulkResultDialog title={tr("邀请操作结果")} response={result.response} labelOf={(id) => result.names[id] || id}
      detailOf={(r) => r.invitation ? tr("新邀请已排队发送，尚未接受") : tr("完成")} onClose={() => setResult(null)} onSelectFailed={Object.keys(result.names).length ? selection.replace : undefined} />}
  </div>;
}

function CreateInvites({ wid, ws, callerRole, onDone, onClose }: {
  wid: string; ws: WorkspaceInfo; callerRole: string; onDone: (r: BulkResponse) => void; onClose: () => void;
}) {
  const [emails, setEmails] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [canExport, setExport] = useState(false);
  const [days, setDays] = useState(7);
  const addresses = [...new Set(emails.split(/[\s,;，；]+/).map((s) => s.trim().toLowerCase()).filter(Boolean))];
  return <ActionDialog title={tr("邀请成员")} confirmLabel={tr("发送邀请")} ready={addresses.length > 0 && addresses.length <= 100 && days >= 1 && days <= 30}
    impact={<>{tr("向以下邮箱发出加入「{p0}」的邀请，角色为{p1}。已是成员的邮箱会单独报告失败；每个邮箱只发送一次。", {p0: ws.name, p1: roleLabel(role)})}</>}
    run={() => post<BulkResponse>(`/api/auth/workspaces/${encodeURIComponent(wid)}/invitations/batch`, {
      invitations: addresses.map((email) => ({ email, role, can_export: role !== "viewer" || canExport })), expires_in_days: days,
    })} onDone={onDone} onClose={onClose}>
    <label className="field">{tr("邮箱（每行一个，也可用逗号分隔）")}<textarea className="input" rows={5} value={emails} onChange={(e) => setEmails(e.target.value)} /></label>
    <span className="small muted">{tr("{p0} 个邮箱；一次最多 100 个。每个工作空间 24 小时最多创建 50 个邀请，超出部分会报告失败。", {p0: addresses.length})}</span>
    <label className="field">{tr("邀请角色")}<select aria-label={tr("邀请角色")} className="input" value={role} onChange={(e) => { setRole(e.target.value as Role); setExport(e.target.value !== "viewer"); }}>
      {ROLES.filter((r) => ROLE_RANK[r] <= ROLE_RANK[callerRole as Role]).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}</select></label>
    <label className="check"><input type="checkbox" checked={role !== "viewer" || canExport} disabled={role !== "viewer"} onChange={(e) => setExport(e.target.checked)} />{tr("允许导出（编辑者及以上角色自带）")}</label>
    <label className="field">{tr("有效天数")}<input className="input" type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>
  </ActionDialog>;
}

function EditInvite({ invitation: i, callerRole, frozen, onDone, onClose }: {
  invitation: Invitation; callerRole: string; frozen: boolean; onDone: () => void; onClose: () => void;
}) {
  const [role, setRole] = useState(i.role);
  const [canExport, setExport] = useState(i.can_export);
  return <ActionDialog title={tr("修改邀请")} confirmLabel={tr("保存邀请")} ready={role !== i.role || canExport !== i.can_export}
    impact={<>{tr("修改发给{p0}的待接受邀请。原链接保持有效；已经接受的邀请无法修改。", {p0: i.email_key})}</>}
    run={() => patch(`/api/auth/invitations/${encodeURIComponent(i.id)}`, { role, can_export: canExport })} onDone={onDone} onClose={onClose}>
    <label className="field">{tr("邀请角色")}<select aria-label={tr("邀请角色")} className="input" value={role} onChange={(e) => { setRole(e.target.value); if (e.target.value === "viewer") setExport(false); }}>
      {assignable(callerRole, i.role, frozen).map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}</select></label>
    <label className="check"><input type="checkbox" checked={role !== "viewer" || canExport} disabled={role !== "viewer" || (frozen && !i.can_export)} onChange={(e) => setExport(e.target.checked)} />{tr("允许导出（编辑者及以上角色自带）")}</label>
  </ActionDialog>;
}

function InvitationAction({ wid, action, onDone, onClose }: {
  wid: string; action: { kind: "revoke" | "resend"; invitations: Invitation[] }; onDone: (r: BulkResponse) => void; onClose: () => void;
}) {
  const [days, setDays] = useState(7);
  const resend = action.kind === "resend";
  return <ActionDialog title={resend ? tr("重发邀请") : tr("撤销邀请")} confirmLabel={resend ? tr("重发邀请") : tr("撤销邀请")} danger={!resend}
    ready={!resend || (days >= 1 && days <= 30)} impact={<>
      <ul className="names">{action.invitations.map((i) => <li key={i.id}>{i.email_key}</li>)}</ul>
      {resend ? tr("将发送新的邀请，旧链接会失效。") : tr("这些链接将失效，收件人将不能通过它们加入工作空间。已加入的成员不受影响。")}{tr("每项单独执行并报告结果。")}</>}
    run={() => post<BulkResponse>(`/api/auth/workspaces/${encodeURIComponent(wid)}/invitations/bulk`, {
      action: action.kind, ids: action.invitations.map((i) => i.id), expires_in_days: days,
    })} onDone={onDone} onClose={onClose}>
    {resend && <label className="field">{tr("新邀请有效天数")}<input className="input" type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>}
  </ActionDialog>;
}
