import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { post, qs } from "@synwevo/auth-client";
import { fmtTime, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useDebounced } from "../lib/hooks";
import { escapeClears, selectionKeys, usePagedSelection, type PagedSelection } from "../components/selection";
import { useAdminPage } from "./data";
import { ActionDialog } from "./ActionDialog";
import { BulkResultDialog, bulkToast, type BulkItemResult, type BulkResponse } from "./BulkResult";
import { PageControls } from "./ListControls";
import { INVITE_STATE, WORKSPACE_KIND, WORKSPACE_STATUS, roleLabel } from "./labels";
import { permitted, type Held } from "./access";
import "./messages";
import "./invitations.messages";

/**
 * A47: platform oversight of the invitations every workspace has sent.
 *
 * Long-pending, expired and undelivered invitations, and abusive waves of them, are found here
 * across workspaces (`GET /api/admin/invitations`, `platform.workspaces.view`). The invitee's
 * address is never on this page: the server sends it masked (first character and domain), and
 * no token or link either. A revocation (`platform.workspaces.manage`) is the tenant's own
 * revocation done by the platform — the link stops at preview and at acceptance, the row lands in
 * the workspace's own audit log as done by platform administration — so each one asks for a
 * written reason and fresh identity proof inside `ActionDialog`, and a batch answers every
 * invitation on its own (`BulkResultDialog`).
 */

export type InvitationState = "pending" | "expired" | "revoked" | "accepted" | "declined";
/** `state` is always one of the five real states: an undelivered invitation is `pending` with a
 *  `failed` or `dropped` mail (`mail_failed` is only a filter and a count). */
export type PlatformInvitation = {
  id: string; state: InvitationState | string; role: string;
  created_at: number; expires_at: number | null; revoked_at: number | null; accepted_at: number | null; declined_at: number | null;
  invitee: { masked: string; domain: string; has_account: boolean };
  /** Invitations of a deleted workspace are still listed; `status` says so. */
  workspace: { id: string; name: string | null; kind: string; status: string; organization_id: string | null };
  inviter: { id: string; email: string; display_name: string | null } | null;
  /** The integration that sent it, already worded by the server (workspace_actor.actor_label). */
  via: string | null;
  mail: { status: string; attempts: number; last_error_code: string | null } | null;
};
export type InvitationGroups = Record<"pending" | "expired" | "mail_failed" | "revoked" | "accepted" | "declined", number>;
/** One revocation's answer; an invitation found already revoked is `already: true`, nothing written. */
export type RevokeResult = { ok: boolean; already: boolean; invitation: { id: string; state: string; revoked_at: number | null }; journaled: unknown };
/** The batch answer: BulkResult's shape with the batch's audit id. */
type BulkRevokeResponse = BulkResponse & { batch_id?: string };

/** The size of one batch (the server refuses more: `too_many`). */
export const INVITATION_BULK_LIMIT = 100;
export type StateFilter = InvitationState | "mail_failed" | "all";
/** The state chips, in the order an administrator works through them. */
export const STATE_FILTERS: StateFilter[] = ["pending", "mail_failed", "expired", "revoked", "accepted", "declined", "all"];
export const STATE_FILTER_LABELS: Record<StateFilter, string> = {
  pending: "待接受", mail_failed: "待接受但投递失败", expired: "已过期", revoked: "已撤销",
  accepted: "已接受", declined: "已拒绝", all: "全部",
};
/** How many invitations a chip stands for. The server counts every state under the search and
 *  the workspace, whichever chip is pressed; `mail_failed` is a part of `pending`, so the whole is
 *  the five states alone. */
export function stateCount(groups: InvitationGroups | null | undefined, state: StateFilter): number | null {
  if (!groups) return null;
  if (state === "all") return (["pending", "expired", "revoked", "accepted", "declined"] as const).reduce((sum, key) => sum + (groups[key] || 0), 0);
  return groups[state] ?? 0;
}

/** The mail's own state; `dropped` is the outbox giving up without another attempt. */
export const INVITE_MAIL_STATE: Record<string, string> = {
  queued: "排队发送中", sent: "已寄出", failed: "投递失败", dropped: "已放弃投递",
};
/** A failure by its code (`mail.public_error_code`, the mail tab's classification: a known code or
 *  `smtp_*rejected_4xx/5xx` as it is, anything else `mail_delivery_failed`). The mail tab's own
 *  sentences for the uncertain and superseded deliveries and for everything on the platform's side;
 *  a refusal by a mail server is named as one, the case an invitation wave to made-up addresses
 *  leaves behind. Raw SMTP text never reaches this page — only the code. */
export function mailFailureText(code: string | null | undefined): string | null {
  if (!code) return null;
  if (code === "delivery_unknown" || code === "delivery_in_progress") return tr("投递结果不确定，禁止自动重发");
  if (code === "superseded") return tr("已生成替代邮件");
  if (/^smtp_recipient_rejected_[45]\d\d$/.test(code)) return tr("收件服务器拒收了这个地址");
  if (/^smtp_(?:(?:sender|data)_)?rejected_[45]\d\d$/.test(code)) return tr("邮件服务器拒收了这封邮件");
  return tr("投递失败；请检查邮件服务配置。");
}

/** The workspace's state; a deleted workspace's invitations are still listed. */
export function workspaceStatusText(status: string): string {
  return WORKSPACE_STATUS[status] || (status === "deleted" ? tr("已删除") : status);
}

/** Pending and expired invitations can be revoked; an accepted or declined one is refused
 *  (`invite_not_pending`), and a revoked one has nothing left to do. */
export const revocable = (invitation: { state: string }) => invitation.state === "pending" || invitation.state === "expired";

/** What a revocation does, said in the explanation and in both confirmations. */
const LINK_STOPS = "撤销后，邀请链接在预览和接受时都会失效；邀请人之后可以重新邀请。";
const FILED_IN_WORKSPACE = "撤销会写入邀请所在空间自己的审计记录，标明由平台管理执行，你填写的理由对能查看该组织审计的人可见；不会授予你该空间的任何访问权。";

/** "Select this page" for the invitation list: adds the page's revocable rows to what is chosen on
 *  other pages, or unticks only them (the same control as the account list's, A18). */
export function SelectInvitationsPage({ selection, disabled }: { selection: PagedSelection; disabled?: boolean }) {
  useI18n();
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = selection.some; }, [selection.some]);
  const unticks = selection.all || (selection.full && selection.some);
  return <label className="pick-page">
    <input ref={ref} type="checkbox" checked={selection.all} disabled={disabled || (selection.full && !selection.all && !selection.some)}
      onChange={() => unticks ? selection.clearPage() : selection.selectAll()} />
    <span className="pick-page-text">{tr("选择本页可撤销的邀请")}</span>
  </label>;
}

/** The selection bar: how many invitations are chosen, the revocation (only with
 *  `platform.workspaces.manage`, otherwise the reason it is not offered), and the way out. */
export function InvitationsBar({ held, count, full, onStart, onClear }: {
  held: Held; count: number; full?: boolean; onStart: () => void; onClear: () => void;
}) {
  useI18n();
  const manage = permitted(held, "platform.workspaces.manage");
  return <div className="acct-bulk" role="region" aria-label={tr("批量撤销邀请")}>
    <span className="count" role="status">{tr("已选 {count} 个邀请", { count })}</span>
    {!manage.disabled && <button type="button" className="btn danger sm" onClick={onStart}>{tr("撤销所选邀请")}</button>}
    <button type="button" className="btn ghost sm" onClick={onClear}>{tr("清除选择")}</button>
    {manage.hint && <span className="muted small">{tr(manage.hint)}</span>}
    <span className="muted small">{tr("翻页或更换筛选时，已选邀请保留。")}</span>
    {full && <span className="small" role="note">{tr("一次最多选择 {limit} 个邀请；已达到上限。", { limit: INVITATION_BULK_LIMIT })}</span>}
  </div>;
}

/** The state of one invitation and when it got there. */
function StateCell({ invitation: i }: { invitation: PlatformInvitation }) {
  const when = i.state === "revoked" && i.revoked_at ? tr("撤销于 {time}", { time: fmtTime(i.revoked_at) })
    : i.state === "accepted" && i.accepted_at ? tr("接受于 {time}", { time: fmtTime(i.accepted_at) })
    : i.state === "declined" && i.declined_at ? tr("拒绝于 {time}", { time: fmtTime(i.declined_at) }) : null;
  return <div>{INVITE_STATE[i.state] || i.state}{when && <span className="cell-note">{when}</span>}</div>;
}

/** Whether the latest mail will not reach the invitee (the `mail_failed` group, on a pending row). */
export const undelivered = (i: Pick<PlatformInvitation, "state" | "mail">) =>
  i.state === "pending" && (i.mail?.status === "failed" || i.mail?.status === "dropped");

/** The latest invitation mail: its state, how often it was tried, and a failure worded by code. A
 *  pending invitation whose mail failed or was dropped is the one someone still waits for, so its
 *  state stands out. */
export function MailCell({ mail, pending = false }: { mail: PlatformInvitation["mail"]; pending?: boolean }) {
  useI18n();
  if (!mail) return <div className="muted">{tr("无发送记录")}</div>;
  const failure = mailFailureText(mail.last_error_code);
  const label = INVITE_MAIL_STATE[mail.status];
  const lost = mail.status === "failed" || mail.status === "dropped";
  const name = label ? tr(label) : mail.status;
  return <div>{pending && lost ? <span className="badge err">{name}</span> : name}
    <span className="cell-note">{tr("已尝试 {count} 次", { count: mail.attempts })}</span>
    {failure && <span className={`cell-note${lost ? " err" : ""}`}>{failure} <code>{mail.last_error_code}</code></span>}
  </div>;
}

export function InvitationsTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  useI18n();
  const toast = useToast();
  const [state, setState] = useState<StateFilter>("pending");
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const searchId = useId();
  // "Only this workspace", from a row: the id goes to the server, the name stays for the notice.
  const [workspace, setWorkspace] = useState<{ id: string; name: string } | null>(null);
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<PlatformInvitation>(
    `/api/admin/invitations${qs({ state, q: query, workspace_id: workspace?.id })}`, onDenied);
  const groups = (data as (typeof data & { groups?: InvitationGroups }) | null)?.groups;
  const items = loading || err ? [] : data?.items || [];
  const manage = permitted(held, "platform.workspaces.manage");
  const canRevoke = !manage.disabled;
  // The selection outlives the page and the filters (one batch is at most INVITATION_BULK_LIMIT),
  // so the rows chosen on other pages are remembered by what this page last showed of them.
  const selection = usePagedSelection(items.filter(revocable).map((i) => i.id), INVITATION_BULK_LIMIT);
  const known = useRef(new Map<string, PlatformInvitation>());
  for (const i of items) known.current.set(i.id, i);
  const labelOf = (id: string): ReactNode => {
    const i = known.current.get(id);
    return i ? <>{i.invitee.masked}<span className="muted"> · {i.workspace.name || i.workspace.id}</span></> : id;
  };
  const [revoke, setRevoke] = useState<PlatformInvitation | null>(null);
  const [bulk, setBulk] = useState<string[] | null>(null);
  const [outcome, setOutcome] = useState<BulkResponse | null>(null);
  const [notice, setNotice] = useState("");
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const [noticeFocus, setNoticeFocus] = useState(0);
  // The button that opened a dialog is gone once its row is revoked or the batch clears the
  // selection, so focus goes to the outcome, which is then read out.
  useEffect(() => { if (noticeFocus) noticeRef.current?.focus(); }, [noticeFocus]);
  const filterRef = useRef<HTMLParagraphElement>(null);
  // Choosing a workspace reloads the list under the button that chose it: focus goes to the
  // filter's own line, which says what is shown and offers the way back.
  useEffect(() => { if (workspace) filterRef.current?.focus(); }, [workspace?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const said = (message: string) => { setNotice(message); setNoticeFocus((n) => n + 1); };
  // Escape in the search box clears the box (the browser's own behaviour), never the selection.
  const clearsSelection = (e: ReactKeyboardEvent) => !(e.target instanceof HTMLElement
    && e.target.closest('input[type="search"], input[type="text"], select, textarea'));
  const busy = loading || !!err;
  const columns = canRevoke ? 9 : 8;
  return <section className="card card-b col gap12" aria-labelledby="platform-invitations"
    onKeyDown={(e) => { if (canRevoke && clearsSelection(e)) escapeClears(e, selection, (m) => setNotice(tr(m))); }}>
    <div className="row gap12 wrap"><h2 className="grow" id="platform-invitations">{tr("全部空间的邀请")}</h2>
      <button type="button" className="btn sm" disabled={loading} onClick={reload}>{tr("刷新邀请")}</button></div>
    <p className="muted small">{tr("这里列出所有工作空间发出的邀请，用来发现长期没有处理、已过期或投递失败的邀请，以及滥用的邀请。受邀地址只显示首字符和域名；这里看不到完整地址、邀请链接或令牌。")}</p>
    <p className="muted small">{tr(LINK_STOPS)} {tr(FILED_IN_WORKSPACE)}</p>
    {manage.hint && <p className="notice" role="note">{tr("你的平台角色可以查看邀请，但不能撤销。")}</p>}
    <div className="row gap8 wrap" role="group" aria-label={tr("按状态筛选邀请")}>
      {STATE_FILTERS.map((s) => { const count = stateCount(groups, s);
        return <button key={s} type="button" className={`btn sm${state === s ? " primary" : ""}`} aria-pressed={state === s}
          onClick={() => setState(s)}>{count === null ? tr(STATE_FILTER_LABELS[s]) : tr("{label}（{count}）", { label: tr(STATE_FILTER_LABELS[s]), count })}</button>; })}
    </div>
    <div className="field"><label htmlFor={searchId}>{tr("搜索邀请")}</label>
      <input id={searchId} type="search" className="input" value={q} maxLength={100} aria-describedby={`${searchId}-hint`} onChange={(e) => setQ(e.target.value)} />
      <span id={`${searchId}-hint`} className="tiny muted">{tr("输入完整邮箱、@域名，或工作空间名称的一部分；邮箱的一部分不会匹配。")}</span></div>
    {workspace && <p className="notice row gap8 wrap" ref={filterRef} tabIndex={-1}>
      <span className="grow">{tr("只显示「{name}」的邀请。", { name: workspace.name })}</span>
      <button type="button" className="btn sm" onClick={() => setWorkspace(null)}>{tr("显示全部空间")}</button></p>}
    <div role="status" aria-live="polite">{notice && <p className="notice" ref={noticeRef} tabIndex={-1}>{notice}</p>}</div>
    {err && <div className="notice err" role="alert">{err} <button type="button" className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载邀请…")}</p>}
    <div className="list-wrap admin-cards-wrap"><table className={`gov admin-cards-table admin-invitations-table${canRevoke ? " selectable" : ""}`}
      aria-label={tr("全部空间的邀请")} aria-busy={loading} onKeyDown={(e) => { if (canRevoke) selectionKeys(e, selection); }}>
      <thead><tr>
        {canRevoke && <th scope="col" className="pick"><SelectInvitationsPage selection={selection} disabled={busy || !items.some(revocable)} /></th>}
        <th scope="col">{tr("所在空间")}</th><th scope="col">{tr("受邀人")}</th><th scope="col">{tr("邀请角色")}</th>
        <th scope="col">{tr("邀请人")}</th><th scope="col">{tr("发出与到期")}</th><th scope="col">{tr("状态")}</th>
        <th scope="col">{tr("邀请邮件")}</th><th scope="col" className="act">{tr("操作")}</th>
      </tr></thead>
      <tbody>{items.map((i) => {
        const picked = selection.sel.has(i.id);
        const w = { ...i.workspace, name: i.workspace.name || i.workspace.id };
        return <tr key={i.id} className={picked ? "sel" : undefined}>
          {canRevoke && <td className="pick">{revocable(i) && <input type="checkbox" data-pick={i.id} checked={picked}
            disabled={busy || (selection.full && !picked)} aria-label={tr("选择邀请 {p0}", { p0: `${i.invitee.masked} · ${w.name}` })}
            onChange={() => {}} onClick={(e) => selection.pick(i.id, { shift: e.shiftKey })} />}</td>}
          <th scope="row">{w.name}
            <div className="who2">{WORKSPACE_KIND[w.kind] || w.kind}{w.organization_id && <> · {tr("所属组织 {id}", { id: w.organization_id })}</>}</div>
            {w.status !== "active" && <span className={`badge ${w.status === "deleted" ? "err" : "warn"}`}>{workspaceStatusText(w.status)}</span>}</th>
          <td data-label={tr("受邀人")}><div><span className="mono">{i.invitee.masked}</span>
            {i.invitee.has_account && <> <span className="badge info">{tr("已有账户")}</span></>}</div></td>
          <td data-label={tr("邀请角色")}>{roleLabel(i.role)}</td>
          <td data-label={tr("邀请人")}><div>{i.inviter ? <>{i.inviter.display_name || i.inviter.email}
              {i.inviter.display_name && <span className="cell-note">{i.inviter.email}</span>}</> : !i.via && <span className="muted">{tr("无记录")}</span>}
            {i.via && <span className="cell-note">{i.via}</span>}</div></td>
          <td data-label={tr("发出与到期")}><div>{fmtTime(i.created_at)}
            {i.expires_at && <span className="cell-note">{tr("有效期至 {time}", { time: fmtTime(i.expires_at) })}</span>}</div></td>
          <td data-label={tr("状态")}><StateCell invitation={i} /></td>
          <td data-label={tr("邀请邮件")}><MailCell mail={i.mail} pending={i.state === "pending"} /></td>
          <td className="act" data-label={tr("操作")}><div className="row gap4 wrap">
            {!workspace && <button type="button" className="btn sm" disabled={busy} aria-label={tr("只看这个空间：{p0}", { p0: w.name })}
              onClick={() => setWorkspace({ id: w.id, name: w.name })}>{tr("只看这个空间")}</button>}
            {canRevoke && revocable(i) && <button type="button" className="btn sm danger" disabled={busy}
              aria-label={tr("撤销邀请：{p0}", { p0: `${i.invitee.masked} · ${w.name}` })} onClick={() => setRevoke(i)}>{tr("撤销邀请")}</button>}
          </div></td>
        </tr>; })}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={columns}>{tr("没有匹配的邀请。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={busy} onPage={setOffset} />
    {canRevoke && selection.count > 0 && <InvitationsBar held={held} count={selection.count} full={selection.full}
      onStart={() => setBulk(selection.ids)} onClear={selection.clear} />}
    {revoke && <RevokeInvitationDialog invitation={revoke} onClose={() => setRevoke(null)}
      onDone={(r) => {
        const target = revoke;
        setRevoke(null);
        if (selection.sel.has(target.id)) selection.replace(selection.ids.filter((id) => id !== target.id));
        reload();
        const message = r.already ? tr("这个邀请此前已被撤销，没有重复操作。") : tr("已撤销发给 {who} 的邀请。", { who: target.invitee.masked });
        said(message); toast(message, "ok");
      }} />}
    {bulk && <BulkRevokeDialog ids={bulk} labelOf={labelOf} onClose={() => setBulk(null)}
      onDone={(response) => {
        // A completed batch clears the selection; the result dialog keeps every item on screen.
        setBulk(null); selection.clear(); reload(); setOutcome(response);
        const [message, tone] = bulkToast(response, tr("撤销所选邀请"));
        setNotice(message); toast(message, tone);
      }} />}
    {outcome && <InvitationsBulkResult response={outcome} labelOf={labelOf}
      onClose={() => { setOutcome(null); setNoticeFocus((n) => n + 1); }} />}
  </section>;
}

/** One invitation's revocation: who it was for, where, what stops, and where it is recorded —
 *  then a reason and fresh identity proof (`dangerous` on the server). */
export function RevokeInvitationDialog({ invitation: i, onClose, onDone }: {
  invitation: PlatformInvitation; onClose: () => void; onDone: (result: RevokeResult) => void;
}) {
  useI18n();
  return <ActionDialog<RevokeResult> title={tr("撤销邀请")} confirmLabel={tr("撤销邀请")} reason="required" reauthScope="admin" danger
    reasonPlaceholder={tr("例如：同一来源短时间内大量邀请陌生地址")}
    impact={<><p><b>{tr("发给 {who} 的邀请将被撤销，对方不能再用它加入「{workspace}」（{role}）。", { who: i.invitee.masked, workspace: i.workspace.name, role: roleLabel(i.role) })}</b></p>
      <p>{tr(LINK_STOPS)}</p><p className="small muted">{tr(FILED_IN_WORKSPACE)}</p></>}
    run={(reason) => post<RevokeResult>(`/api/admin/invitations/${encodeURIComponent(i.id)}/revoke`, { reason })}
    onClose={onClose} onDone={onDone} />;
}

/** The revocation of every chosen invitation: each one is revoked and audited on its own on the
 *  server, and the result answers each of them. */
export function BulkRevokeDialog({ ids, labelOf, onClose, onDone }: {
  ids: string[]; labelOf: (id: string) => ReactNode; onClose: () => void; onDone: (response: BulkRevokeResponse) => void;
}) {
  useI18n();
  return <ActionDialog<BulkRevokeResponse> title={tr("撤销所选邀请")} confirmLabel={tr("撤销所选邀请")} reason="required" reauthScope="admin" danger
    impact={<><p><b>{tr("已选 {count} 个邀请", { count: ids.length })}</b></p>
      <p>{tr("所选邀请将逐个撤销，每个邀请单独写入审计记录；已接受或已拒绝的邀请不会被处理。")}</p>
      {/* Up to a hundred lines: the list scrolls on its own and can be scrolled from the keyboard. */}
      <ul className="names" tabIndex={0} aria-label={tr("所选邀请")}>{ids.map((id) => <li key={id}>{labelOf(id)}</li>)}</ul>
      <p>{tr(LINK_STOPS)}</p><p className="small muted">{tr(FILED_IN_WORKSPACE)}</p></>}
    run={(reason) => post<BulkRevokeResponse>("/api/admin/invitations/revoke", { ids, reason })}
    onClose={onClose} onDone={onDone} />;
}

/** What a success did to one invitation: revoked now, or found already revoked. */
export function bulkRevokeDetail(r: BulkItemResult): string {
  return r.code === "already_revoked" ? tr("此前已撤销，本次未重复执行。") : tr("已撤销");
}

/** Every invitation of the batch, failures first, each worded by its code (labels.ts). */
export function InvitationsBulkResult({ response, labelOf, onClose }: {
  response: BulkResponse; labelOf: (id: string) => ReactNode; onClose: () => void;
}) {
  useI18n();
  return <BulkResultDialog title="撤销所选邀请" response={response} labelOf={labelOf} detailOf={bulkRevokeDetail} onClose={onClose} />;
}
