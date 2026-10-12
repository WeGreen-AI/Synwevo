import { useEffect, useState } from "react";
import { post, qs } from "@synwevo/auth-client";
import { fmtTime, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useDebounced } from "../lib/hooks";
import { useAdminData, useAdminPage } from "./data";
import { ActionDialog } from "./ActionDialog";
import { NotificationRecovery } from "./NotificationRecovery";
import { PageControls } from "./ListControls";
import { MAIL_STATE, fmtDuration } from "./labels";
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

type SecondFactorPolicy = { mode: "off" | "required"; source: "database" | "default"; usable_super_admins: number; enrolled_super_admins: number;
  caller_second_factor: boolean; caller_session_strong: boolean; grace_until?: number | null; grace_max_days?: number };
/** Whether saving would change anything: the mode changes, or `required` stays and the save sets a
 *  grace window — or, with 0 days, ends the window still running (the server deletes it and the
 *  requirement applies at once). Unchanged `required` with no window and 0 days changes nothing. */
export function secondFactorSaveChanges(d: Pick<SecondFactorPolicy, "mode" | "grace_until">, mode: SecondFactorPolicy["mode"], graceDays: number): boolean {
  return mode !== d.mode || (mode === "required" && (graceDays > 0 || !!d.grace_until));
}
/** The confirmation's grace sentence: a new window, the end of the running one, or none at all. */
export function secondFactorGraceSentence(graceDays: number, graceBefore: number | null | undefined): string {
  if (graceDays > 0) return tr("宽限期 {days} 天：期内只提醒，不锁定。", { days: graceDays });
  return graceBefore ? tr("现有宽限期将结束：要求立即生效。") : tr("没有宽限期：保存后立即生效。");
}
/** Admin coverage A11: whether platform administrators must prove a second factor. The server
 *  refuses to require it while no usable super administrator could meet it. */
export function AdminSecondFactorTab({ onDenied, held }: Props) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.config.manage");
  const load = useAdminData<SecondFactorPolicy>("/api/admin/security/second-factor", onDenied);
  const [mode, setMode] = useState<SecondFactorPolicy["mode"]>("off");
  const [graceDays, setGraceDays] = useState("0");
  const [change, setChange] = useState<{ mode: SecondFactorPolicy["mode"]; before: SecondFactorPolicy["mode"]; graceDays: number; graceBefore: number | null } | null>(null);
  const toast = useToast();
  useEffect(() => { if (load.data) { setMode(load.data.mode); setGraceDays("0"); } }, [load.data]);
  const names = { off: t("不要求（沿用现有规则）"), required: t("要求管理员使用第二因素") };
  const d = load.data;
  const graceMax = d?.grace_max_days ?? 30;
  const graceValue = Number(graceDays);
  const graceValid = Number.isInteger(graceValue) && graceValue >= 0 && graceValue <= graceMax;
  const dirty = !!d && secondFactorSaveChanges(d, mode, graceValue);
  return <section className="card card-b col gap12">
    <h2>{t("管理员第二因素")}</h2>
    <p className="muted">{t("要求后，平台管理员的登录必须包含第二个因素（动态验证码或经用户验证的安全密钥）才能使用平台管理与 API 管理；只验证口令的登录会被锁在管理之外，账户本身不受影响。")}</p>
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    <button className="btn sm" disabled={load.loading} onClick={load.reload}>{t("刷新")}</button>
    {load.loading ? <p role="status">{t("加载中…")}</p> : d && <>
      <p>{t("当前策略：{mode}；来源：{source}", { mode: names[d.mode], source: d.source === "database" ? t("平台设置") : t("环境配置或默认值") })}
        {d.grace_until ? ` · ${t("宽限期至 {time}：期内只提醒，不锁定", { time: fmtTime(d.grace_until) })}` : ""}</p>
      <p className="small muted">{t("可用最高管理员 {total} 人，其中 {enrolled} 人已登记安全密钥或动态验证码。", { total: d.usable_super_admins, enrolled: d.enrolled_super_admins })}</p>
      {!d.caller_second_factor && <p className="notice" role="note">{t("你自己尚未登记安全密钥或动态验证码：要求第二因素后，你的下一次管理操作会被锁定，直到登记并重新登录。")}</p>}
      {d.caller_second_factor && !d.caller_session_strong && <p className="notice" role="note">{t("你本次登录只验证了口令：要求第二因素后，需要用安全密钥确认或退出后用口令和动态验证码重新登录，才能继续管理。")}</p>}
      <fieldset className="col gap12" disabled={!!load.err || manage.disabled}><legend>{t("新的第二因素策略")}</legend>
        {(["off", "required"] as const).map(value => <label key={value} className="check"><input type="radio" name="second-factor-mode" value={value} checked={mode === value} onChange={() => setMode(value)} />{names[value]}</label>)}
        {mode === "required" && <label className="field">{t("宽限期（天，0 表示立即生效，最多 {max} 天）", { max: graceMax })}
          <input className="input" type="number" min={0} max={graceMax} step={1} value={graceDays} onChange={e => setGraceDays(e.target.value)} aria-invalid={graceValid ? undefined : true} /></label>}
      </fieldset>
      <p className="small muted">{t("只有至少一名可用的最高管理员已登记第二因素时才能开启；策略只约束平台管理，不改变账户登录本身。")}</p>
      <p className="small muted">{t("宽限期内，未登记第二因素的管理员只会在账户页看到提醒；到期后才被锁在管理之外。再次保存可重设宽限期。")}</p>
      {manage.hint && <p className="notice" role="note">{t("你的平台角色可以查看第二因素策略，但不能修改。")}</p>}
      <button className="btn primary" disabled={!!load.err || !dirty || !graceValid || manage.disabled} onClick={() => setChange({ mode, before: d.mode, graceDays: mode === "required" ? graceValue : 0, graceBefore: d.grace_until ?? null })}>{t("保存第二因素策略")}</button>
    </>}
    {change && <ActionDialog title={t("保存第二因素策略")} confirmLabel={t("保存策略")} reason="required" danger={change.mode === "required"}
      impact={<>{t("第二因素策略将从「{before}」改为「{after}」。", { before: names[change.before], after: names[change.mode] })}
        {change.mode === "required" && ` ${secondFactorGraceSentence(change.graceDays, change.graceBefore)}`}</>}
      run={reason => post<SecondFactorPolicy>("/api/admin/security/second-factor", { mode: change.mode, expected_mode: change.before, grace_days: change.graceDays, reason })}
      onClose={() => setChange(null)} onDone={() => { setChange(null); load.reload(); toast(t("第二因素策略已保存"), "ok"); }} />}
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
  return <div className="col gap16" style={{ minWidth: 0 }}><section className="card card-b col gap12">
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
  </section><NotificationRecovery held={held} onDenied={onDenied} /></div>;
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

/** Admin coverage G19: the throttles the server holds (`auth_attempts`), and their release. */
export type Throttle = { id: string; kind: string; subject: string | null; user_id: string | null; count: number; first_at: number; last_at: number;
  blocked_until: number | null; retry_after: number; active: boolean };
export const THROTTLE_KIND_LABELS: Record<string, string> = {
  account_login: "账户登录退避", address_login: "来源地址登录窗口", session_reauth: "会话重认证失败", registration_address: "注册（按地址）",
  registration_source: "注册（按来源地址）", login_code_address: "邮箱验证码登录（按地址）", login_code_account: "邮箱验证码登录（按账户）",
  account_second_factor: "第二因素验证退避",
  recovery_source: "找回口令（按来源地址）", bootstrap_source: "初始化（按来源地址）", passkey_source: "安全密钥登录（按来源地址）",
  passkey_reauth: "安全密钥重认证", account_action: "账户敏感操作", push: "推送订阅", other: "其他",
};
export function ThrottleTable({ items, mayLift, onLift }: { items: Throttle[]; mayLift: boolean; onLift?: (row: Throttle) => void }) {
  useI18n();
  const t = tr;
  if (!items.length) return <p className="muted">{t("没有生效中的限流记录。")}</p>;
  return <div className="list-wrap"><table className="gov" aria-label={t("限流记录")}>
    <thead><tr><th scope="col">{t("类型")}</th><th scope="col">{t("对象")}</th><th scope="col">{t("次数")}</th><th scope="col">{t("首次")}</th><th scope="col">{t("最近")}</th><th scope="col">{t("状态")}</th>{mayLift && <th scope="col" className="act">{t("操作")}</th>}</tr></thead>
    <tbody>{items.map(r => <tr key={r.id}>
      <td>{t(THROTTLE_KIND_LABELS[r.kind] || r.kind)}</td>
      <td>{r.subject ?? <span className="muted">{t("不可见")}</span>}</td>
      <td>{r.count}</td><td>{fmtTime(r.first_at)}</td><td>{fmtTime(r.last_at)}</td>
      <td>{r.active ? (r.blocked_until ? t("已限流，至 {time}", { time: fmtTime(r.blocked_until) }) : t("等待 {wait}", { wait: fmtDuration(r.retry_after) })) : t("未生效（下次失败将延长等待）")}</td>
      {mayLift && <td className="act"><button type="button" className="btn sm" onClick={() => onLift?.(r)}>{t("解除")}</button></td>}
    </tr>)}</tbody></table></div>;
}
export function ThrottlesTab({ onDenied, held }: Props) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.config.manage");
  const [activeOnly, setActiveOnly] = useState(true);
  const load = useAdminData<{ items: Throttle[] }>(`/api/admin/security/throttles${activeOnly ? "?active=true" : ""}`, onDenied);
  const [lift, setLift] = useState<Throttle | null>(null);
  const toast = useToast();
  return <section className="card card-b col gap12" aria-labelledby="throttles-heading">
    <h2 id="throttles-heading">{t("限流状态")}</h2>
    <p className="muted">{t("登录退避、来源地址窗口、注册与找回窗口等由服务器持有的限流记录。来源地址只以哈希显示；账户只对能查看账户的角色显示。解除需要理由与重新验证身份，只让下一次尝试从零开始。")}</p>
    <div className="row gap12 wrap"><label className="check"><input type="checkbox" checked={activeOnly} onChange={e => setActiveOnly(e.target.checked)} />{t("仅显示生效中")}</label>
      <button className="btn sm" disabled={load.loading} onClick={load.reload}>{t("刷新")}</button></div>
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    {load.loading ? <p role="status">{t("加载中…")}</p> : load.data && <ThrottleTable items={load.data.items} mayLift={!manage.disabled} onLift={setLift} />}
    {manage.hint && <p className="notice" role="note">{t("你的平台角色可以查看限流记录，但不能解除。")}</p>}
    {lift && <ActionDialog title={t("解除限流")} confirmLabel={t("解除限流")} reason="required" danger
      impact={t("将解除「{kind}」的这条限流记录（对象：{subject}）。下一次尝试从零开始；不改变口令、会话或任何权限。", { kind: t(THROTTLE_KIND_LABELS[lift.kind] || lift.kind), subject: lift.subject ?? t("不可见") })}
      run={reason => post("/api/admin/security/throttles/lift", { id: lift.id, reason })}
      onClose={() => setLift(null)} onDone={() => { setLift(null); load.reload(); toast(t("限流已解除"), "ok"); }} />}
  </section>;
}
