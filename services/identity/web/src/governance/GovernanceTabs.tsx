import { useEffect, useState } from "react";
import { post, qs } from "@synwevo/auth-client";
import { fmtTime, useI18n, useToast } from "@synwevo/ui";
import { useDebounced } from "../lib/hooks";
import { useAdminData, useAdminPage } from "./data";
import { ActionDialog } from "./ActionDialog";
import { PageControls } from "./ListControls";
import { MAIL_STATE } from "./labels";
import { permitted, type Held } from "./access";
import "./messages";
import "./platform.messages";

/** `held`: the caller's platform permissions (access.ts). Writes the caller's role does not reach
 *  are disabled with the reason, so they neither dead-end nor fill the audit log with refusals. */
type Props = { onDenied: () => void; held: Held };
type Policy = { mode: "open" | "invite" | "closed"; source: "database" | "environment" };
export function RegistrationTab({ onDenied, held }: Props) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.config.manage");
  const load = useAdminData<Policy>("/api/admin/registration", onDenied);
  const [mode, setMode] = useState<Policy["mode"]>("invite");
  const [change, setChange] = useState<{ mode: Policy["mode"]; before: Policy["mode"] } | null>(null);
  const toast = useToast();
  useEffect(() => { if (load.data) setMode(load.data.mode); }, [load.data]);
  const names = { open: t("开放注册"), invite: t("仅限邀请"), closed: t("关闭注册") };
  return <section className="card card-b col gap12">
    <h2>{t("注册策略")}</h2>
    <p className="muted">{t("策略保存后对新的注册请求生效，不改变已有账户、成员关系或文档权限。")}</p>
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    <button className="btn sm" disabled={load.loading} onClick={load.reload}>{t("刷新")}</button>
    {load.loading ? <p role="status">{t("加载中…")}</p> : load.data && <>
      <p>{t("当前策略：{mode}；来源：{source}", { mode: names[load.data.mode], source: load.data.source === "database" ? t("平台设置") : t("环境配置或默认值") })}</p>
      <fieldset className="col gap12" disabled={!!load.err || manage.disabled}><legend>{t("新的注册策略")}</legend>
        {(["open", "invite", "closed"] as const).map(value => <label key={value} className="check"><input type="radio" name="registration-mode" value={value} checked={mode === value} onChange={() => setMode(value)} />{names[value]}</label>)}
      </fieldset>
      <p className="small muted">{t("开放注册允许任何人创建账户；其他两种策略只允许受邀者。")}</p>
      {manage.hint && <p className="notice" role="note">{t("你的平台角色可以查看注册策略，但不能修改。")}</p>}
      <button className="btn primary" disabled={!!load.err || mode === load.data.mode || manage.disabled} onClick={() => setChange({ mode, before: load.data!.mode })}>{t("保存注册策略")}</button>
    </>}
    {change && <ActionDialog title={t("保存注册策略")} confirmLabel={t("保存策略")} reason="required"
      impact={t("注册策略将从「{before}」改为「{after}」。此设置会覆盖环境配置。", { before: names[change.before], after: names[change.mode] })}
      run={reason => post<Policy>("/api/admin/registration", { mode: change.mode, expected_mode: change.before, reason })}
      onClose={() => setChange(null)} onDone={() => { setChange(null); load.reload(); toast(t("注册策略已保存"), "ok"); }} />}
  </section>;
}

type Delivery = { id: string; to_addr: string; kind: string; ref_id: string | null; status: string; attempts: number; created_at: number; sent_at: number | null; next_attempt_at: number | null; error_code: string | null; retryable: boolean };
export function MailTab({ onDenied, held }: Props) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.mail.manage");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const query = useDebounced(q.trim());
  const load = useAdminPage<Delivery>(`/api/admin/mail${qs({ q: query, status })}`, onDenied);
  const [retry, setRetry] = useState<Delivery | null>(null);
  const [notice, setNotice] = useState("");
  const mode = (load.data as (typeof load.data & { delivery_mode?: string }))?.delivery_mode;
  const kinds: Record<string, string> = { reset: t("账户找回"), verify: t("邮箱验证"), invite: t("邀请"), invitation: t("邀请"), security: t("安全通知"), email_change: t("邮箱变更") };
  const failure = (code: string | null) => !code ? "—" : code === "delivery_unknown" || code === "delivery_in_progress" ? t("投递结果不确定，禁止自动重发") : code === "superseded" ? t("已生成替代邮件") : t("投递失败；请检查邮件服务配置。");
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{t("邮件投递")}</h2><button className="btn sm" disabled={load.loading} onClick={load.reload}>{t("刷新")}</button></div>
    {mode && <p className="notice" role="status">{mode === "file" ? t("当前为本地文件模式：邮件仅写入本地文件，没有真正寄出。") : mode === "disabled" ? t("邮件服务尚未配置。") : t("当前为 SMTP 模式；已寄出表示邮件服务器已接受，不代表已到达收件箱。")}</p>}
    <p className="muted small">{t("只显示投递记录，不显示邮件内容。重试找回和验证邮件会生成新链接并使旧链接失效；邀请由工作空间管理员在「成员与邀请」中重发。安全通知和投递结果不确定的邮件不能重发。")}</p>
    <div className="row gap12 wrap"><label className="field grow">{t("搜索收件人或邮件编号")}<input type="search" className="input" value={q} maxLength={100} onChange={e => setQ(e.target.value)} /></label>
      <label className="field">{t("邮件状态")}<select className="input" value={status} onChange={e => setStatus(e.target.value)}><option value="">{t("全部状态")}</option>{["queued", "sent", "failed"].map(value => <option key={value} value={value}>{MAIL_STATE[value]}</option>)}</select></label></div>
    {notice && <p className="notice ok" role="status">{t(notice)}</p>}
    {manage.hint && <p className="notice" role="note">{t("你的平台角色可以查看邮件投递，但不能重试。")}</p>}
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    {load.loading && <p role="status">{t("加载中…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={t("邮件投递")} aria-busy={load.loading}><thead><tr>{["收件人", "类型", "状态", "尝试次数", "时间", "操作"].map(label => <th scope="col" className={label === "操作" ? "act" : undefined} key={label}>{t(label)}</th>)}</tr></thead>
      <tbody>{!load.loading && !load.err && load.data?.items.map(row => <tr key={row.id}>
        <th scope="row">{row.to_addr}<div className="who2 mono">{row.id}</div></th><td>{kinds[row.kind] || t("其他邮件")}</td>
        <td>{MAIL_STATE[row.status] || row.status}{row.error_code && <span className="cell-note">{failure(row.error_code)} <code>{row.error_code}</code></span>}</td>
        <td>{row.attempts}</td><td>{fmtTime(row.created_at)}{row.sent_at && <span className="cell-note">{t("寄出时间：{time}", { time: fmtTime(row.sent_at) })}</span>}{row.next_attempt_at && <span className="cell-note">{t("下一次尝试：{time}", { time: fmtTime(row.next_attempt_at) })}</span>}</td>
        <td className="act"><button className="btn sm" disabled={!row.retryable || mode === "disabled" || manage.disabled} onClick={() => setRetry(row)}>{t("生成新链接并重试")}</button></td>
      </tr>)}{!load.loading && !load.err && !load.data?.items.length && <tr><td colSpan={6}>{t("没有匹配的邮件。")}</td></tr>}</tbody>
    </table></div><PageControls offset={load.offset} total={load.data?.total || 0} busy={load.loading || !!load.err} onPage={load.setOffset} />
    {retry && <ActionDialog title={t("生成新链接并重试")} confirmLabel={t("重新排队")} reason="required"
      impact={t("为原收件人 {email} 对应账户的当前邮箱生成新链接。旧链接失效，结果会写入审计记录。", { email: retry.to_addr })}
      run={reason => post<{ delivery: Delivery }>(`/api/admin/mail/${encodeURIComponent(retry.id)}/retry`, { reason })}
      onClose={() => setRetry(null)} onDone={result => { setRetry(null); load.reload(); setNotice(t("新邮件已排队：{email}。请刷新查看投递状态。", { email: result.delivery.to_addr })); }} />}
  </section>;
}

type Organization = { id: string; name: string; status: string; role: string | null; members: number; workspaces: number; recover_until: number | null };
export function OrganizationsTab({ onDenied, held }: Props) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.orgs.manage");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const query = useDebounced(q.trim());
  const load = useAdminPage<Organization>(`/api/admin/organizations${qs({ q: query, status })}`, onDenied);
  const [action, setAction] = useState<{ org: Organization; kind: string } | null>(null);
  const toast = useToast();
  const names: Record<string, string> = { active: t("正常"), frozen: t("已冻结"), deleted: t("已删除") };
  const actions: Record<string, string> = { freeze: t("冻结组织"), unfreeze: t("解冻组织"), delete: t("删除组织"), recover: t("恢复组织") };
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{t("组织治理")}</h2><button className="btn sm" onClick={load.reload} disabled={load.loading}>{t("刷新")}</button></div>
    <p className="muted">{t("平台管理员可查看组织元数据与数量。只有组织所有者可执行生命周期操作；平台管理不会授予成员身份或业务文档访问权。")}</p>
    {manage.hint && <p className="notice" role="note">{t("你的平台角色可以查看组织，但不能在管理后台执行组织的生命周期操作。")}</p>}
    <div className="row gap12 wrap"><label className="field grow">{t("搜索组织名称或编号")}<input className="input" type="search" maxLength={100} value={q} onChange={e => setQ(e.target.value)} /></label>
      <label className="field">{t("状态")}<select className="input" value={status} onChange={e => setStatus(e.target.value)}><option value="">{t("全部状态")}</option>{Object.entries(names).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
    {load.err && <p className="notice err" role="alert">{load.err}</p>}{load.loading && <p role="status">{t("加载中…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={t("组织治理")} aria-busy={load.loading}><thead><tr>{["组织", "状态", "成员", "工作空间", "操作"].map(label => <th scope="col" className={label === "操作" ? "act" : undefined} key={label}>{t(label)}</th>)}</tr></thead>
      <tbody>{!load.loading && !load.err && load.data?.items.map(org => <tr key={org.id}>
        <th scope="row">{org.name}<div className="who2 mono">{org.id}</div></th><td>{names[org.status]}{org.recover_until && <span className="cell-note">{t("恢复期限：{time}", { time: fmtTime(org.recover_until) })}</span>}</td><td>{org.members}</td><td>{org.workspaces}</td>
        <td className="act"><div className="row gap4 wrap">{org.role && <a className="btn sm" href={`/organizations/${encodeURIComponent(org.id)}`}>{t("组织设置")}</a>}
          {org.role === "owner" ? (org.status === "deleted" ? ["recover"] : [org.status === "active" ? "freeze" : "unfreeze", "delete"]).map(kind => <button className="btn sm" key={kind} disabled={manage.disabled || (kind === "recover" && (!org.recover_until || org.recover_until * 1000 <= Date.now()))} onClick={() => setAction({ org, kind })}>{actions[kind]}</button>) : <span className="muted small">{t("生命周期操作需要组织所有者权限")}</span>}</div></td>
      </tr>)}{!load.loading && !load.err && !load.data?.items.length && <tr><td colSpan={5}>{t("没有匹配的组织。")}</td></tr>}</tbody>
    </table></div><PageControls offset={load.offset} total={load.data?.total || 0} busy={load.loading || !!load.err} onPage={load.setOffset} />
    {action && <ActionDialog title={actions[action.kind]} confirmLabel={actions[action.kind]} danger reason="required"
      impact={<><b>{action.org.name}</b><p>{action.kind === "delete" ? t("组织将停止使用，并保留七天恢复期限。所属工作空间的访问会被收回，内容不会因此提供给管理员。") : action.kind === "freeze" ? t("组织及其工作空间将停止应用访问，已有会话和令牌会被撤销。") : t("组织将按原有成员关系恢复可用。已撤销的会话和令牌不会恢复；删除前已冻结的组织恢复后仍为冻结状态。")}</p></>}
      run={reason => post(`/api/admin/organizations/${encodeURIComponent(action.org.id)}/${action.kind}`, { reason })}
      onClose={() => setAction(null)} onDone={() => { setAction(null); load.reload(); toast(t("组织状态已更新"), "ok"); }} />}
  </section>;
}
