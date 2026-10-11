import { useState } from "react";
import { get } from "@synwevo/auth-client";
import { fmtTime, t as tr, useI18n } from "@synwevo/ui";
import { ACCOUNT_STATUS, MAIL_STATE, errText } from "./labels";
import "./messages";

/**
 * One address's registration, stage by stage (admin coverage A27): what support needs to answer
 * "I registered and nothing arrived" — is the address acceptable, was it throttled (and is it
 * now), the account row, the verification link (never its value), the mail and what became of
 * it, the activation. The server withholds the audit events and the mail rows from roles that
 * cannot read those pages; this card explains each omission rather than hiding the stage.
 */
export type RegistrationDiagnosis = {
  email: string; valid: boolean; registration_mode: string;
  account: { id: string; status: string; created_at: number; email_verified_at: number | null; locale: string } | null;
  throttle: { count: number; first_at: number; last_at: number; blocked: boolean; blocked_until: number | null; limit: number; window_s: number } | null;
  tokens: { kind: string; created_at: number; expires_at: number; used_at: number | null; expired: boolean }[];
  audit: { permitted: boolean;
    events: { id: string; at: number; action: string; result: string; ip: string | null; detail: Record<string, unknown> }[];
    challenges: { id: string; at: number; result: string; ip: string | null; action: string | null; reason: string | null }[] };
  mail: { permitted: boolean; items: { id: string; kind: string | null; status: string; attempts: number; created_at: number; sent_at: number | null; error_code: string | null }[] };
  stages: { stage: string; state: string; at: number | null }[];
};

export const STAGE_LABELS: Record<string, string> = {
  validation: "地址校验", challenge: "人机验证", throttle: "频率限制", account: "账户", token: "验证链接", mail: "邮件", activation: "激活",
};
export const STAGE_STATES: Record<string, string> = {
  "validation.valid": "地址格式可接受",
  "challenge.ok": "通过", "challenge.denied": "未通过", "challenge.error": "验证服务出错", "challenge.none": "没有记录", "challenge.withheld": "需要审计查看权限",
  "throttle.clear": "未被限流", "throttle.refused": "曾被限流（已记录）", "throttle.blocked": "当前已限流",
  "account.none": "没有账户", "account.pending": "待激活", "account.active": "正常", "account.suspended": "已停用", "account.pending_delete": "待删除",
  "token.none": "没有签发过", "token.issued": "已签发，未使用", "token.used": "已使用", "token.expired": "已过期未使用",
  "mail.none": "没有邮件", "mail.queued": "已排队", "mail.sent": "已发送", "mail.failed": "发送失败", "mail.dropped": "已丢弃", "mail.withheld": "需要邮件查看权限",
  "activation.none": "没有账户", "activation.pending": "尚未激活", "activation.active": "已激活",
};
export const REGISTRATION_MODE_LABELS: Record<string, string> = { open: "开放注册", invite: "仅限邀请", closed: "关闭注册" };
const RESULT_LABELS: Record<string, string> = { ok: "成功", denied: "被拒绝", error: "错误" };
const DASH = "—";

export function stageStateText(stage: string, state: string): string {
  const key = `${stage}.${state}`;
  return STAGE_STATES[key] ? tr(STAGE_STATES[key]) : state;
}

export function RegistrationDiagnosisBody({ d }: { d: RegistrationDiagnosis }) {
  useI18n();
  const th = d.throttle;
  return <div className="col gap12">
    <p className="muted small">{tr("地址 {email} · 注册策略：{mode}", { email: d.email, mode: tr(REGISTRATION_MODE_LABELS[d.registration_mode] || d.registration_mode) })}</p>
    <div className="list-wrap"><table className="gov" aria-label={tr("注册阶段")}>
      <thead><tr><th scope="col">{tr("阶段")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("时间")}</th></tr></thead>
      <tbody>{d.stages.map((s) => <tr key={s.stage}>
        <th scope="row">{tr(STAGE_LABELS[s.stage] || s.stage)}</th><td>{stageStateText(s.stage, s.state)}</td><td>{s.at ? fmtTime(s.at) : DASH}</td>
      </tr>)}</tbody></table></div>
    {th && <p className="small">{th.blocked
      ? tr("该地址在最近一小时内请求了 {count} 次（上限 {limit}）；当前已限流，{until}。", { count: th.count, limit: th.limit, until: th.blocked_until ? tr("至 {time}", { time: fmtTime(th.blocked_until) }) : tr("窗口结束后恢复") })
      : tr("该地址在最近一小时内请求了 {count} 次（上限 {limit}）；未被限流。", { count: th.count, limit: th.limit })}</p>}
    {d.account ? <p className="small">{tr("账户 {id}：{status}，创建于 {time}", { id: d.account.id, status: ACCOUNT_STATUS[d.account.status] || d.account.status, time: fmtTime(d.account.created_at) })}
      {d.account.email_verified_at ? ` · ${tr("邮箱验证于 {time}", { time: fmtTime(d.account.email_verified_at) })}` : ""}</p>
      : <p className="muted small">{tr("该地址没有账户。")}</p>}
    <section className="col gap4"><h4 className="secttl sm">{tr("验证链接（{count} 个）", { count: d.tokens.length })}</h4>
      {d.tokens.length ? <ul>{d.tokens.map((k, i) => <li key={i}>{tr("签发于 {issued}，{until} 到期", { issued: fmtTime(k.created_at), until: fmtTime(k.expires_at) })} · {k.used_at ? tr("已于 {time} 使用", { time: fmtTime(k.used_at) }) : k.expired ? tr("已过期未使用") : tr("未使用")}</li>)}</ul>
        : <p className="muted small">{tr("没有签发过验证链接。")}</p>}
    </section>
    <section className="col gap4"><h4 className="secttl sm">{tr("邮件（{count} 封）", { count: d.mail.items.length })}</h4>
      {!d.mail.permitted ? <p className="notice" role="note">{tr("需要邮件查看权限才能显示邮件投递。")}</p>
        : d.mail.items.length ? <div className="list-wrap"><table className="gov" aria-label={tr("邮件")}>
          <thead><tr><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("尝试次数")}</th><th scope="col">{tr("排队时间")}</th><th scope="col">{tr("发送时间")}</th><th scope="col">{tr("错误")}</th></tr></thead>
          <tbody>{d.mail.items.map((m) => <tr key={m.id}><td>{m.kind || DASH}</td><td>{MAIL_STATE[m.status] || m.status}</td><td>{m.attempts}</td><td>{fmtTime(m.created_at)}</td><td>{m.sent_at ? fmtTime(m.sent_at) : DASH}</td><td>{m.error_code || DASH}</td></tr>)}</tbody>
        </table></div> : <p className="muted small">{tr("没有邮件。")}</p>}
    </section>
    <section className="col gap4"><h4 className="secttl sm">{tr("事件（{count} 条）", { count: d.audit.events.length })}</h4>
      {!d.audit.permitted ? <p className="notice" role="note">{tr("需要审计查看权限才能显示注册事件与人机验证结果。")}</p> : <>
        {d.audit.events.length ? <div className="list-wrap"><table className="gov" aria-label={tr("注册事件")}>
          <thead><tr><th scope="col">{tr("时间")}</th><th scope="col">{tr("操作")}</th><th scope="col">{tr("结果")}</th><th scope="col">{tr("来源地址")}</th><th scope="col">{tr("说明")}</th></tr></thead>
          <tbody>{d.audit.events.map((e) => <tr key={e.id}><td>{fmtTime(e.at)}</td><td>{e.action}</td><td>{tr(RESULT_LABELS[e.result] || e.result)}</td><td>{e.ip || DASH}</td>
            <td>{Object.keys(e.detail).length ? Object.entries(e.detail).map(([k, v]) => `${k}=${String(v)}`).join(" · ") : DASH}</td></tr>)}</tbody>
        </table></div> : <p className="muted small">{tr("没有相关事件。")}</p>}
        {d.audit.challenges.length > 0 && <><h4 className="secttl sm">{tr("人机验证（{count} 条，同一来源地址、前后 15 分钟内）", { count: d.audit.challenges.length })}</h4>
          <ul>{d.audit.challenges.map((c) => <li key={c.id}>{fmtTime(c.at)} · {c.action || DASH} · {tr(RESULT_LABELS[c.result] || c.result)}{c.reason ? ` · ${c.reason}` : ""}</li>)}</ul></>}
      </>}
    </section>
  </div>;
}

export function RegistrationDiagnostics() {
  useI18n();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<RegistrationDiagnosis | null>(null);
  const run = async () => {
    const address = email.trim();
    if (!address) return;
    setBusy(true); setError(null);
    try { setData(await get<RegistrationDiagnosis>(`/api/admin/diagnostics/registration?email=${encodeURIComponent(address)}`)); }
    catch (e) { setData(null); setError(errText(e)); }
    finally { setBusy(false); }
  };
  return <section className="card card-b col gap12" aria-labelledby="registration-diagnostics">
    <h2 id="registration-diagnostics">{tr("注册诊断")}</h2>
    <p className="muted small">{tr("输入邮箱地址，查看该地址注册的每个阶段：地址校验、人机验证、频率限制、账户、验证链接、邮件与激活。不显示验证令牌或邮件正文；查看不写审计。")}</p>
    <form className="row gap8 wrap" onSubmit={(e) => { e.preventDefault(); void run(); }}>
      <input type="email" className="input" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} placeholder={tr("邮箱地址")} aria-label={tr("要诊断的邮箱地址")} />
      <button type="submit" className="btn" disabled={busy || !email.trim()}>{busy ? tr("正在诊断…") : tr("诊断")}</button>
    </form>
    {error && <p className="notice err" role="alert">{error}</p>}
    {data && <RegistrationDiagnosisBody d={data} />}
  </section>;
}
