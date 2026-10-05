import { useEffect, useState } from "react";
import { post, put } from "@synwevo/auth-client";
import { fmtTime, useI18n } from "@synwevo/ui";
import { permitted, type Held } from "./access";
import { useAdminData } from "./data";
import { errText } from "./labels";
import "./agent-safety.messages";

/** Platform governance of the personal Agent's content safety (synwevo_identity.admin_agent_safety):
 *  the governed rule lists (platform.config.*), people's reports, per-account stop counts and
 *  Agent suspensions (platform.users.*). No conversation is visible here. */
type Rule = { id: string; kind: "keyword" | "pattern"; pattern: string; applies_to: "input" | "output" | "both"; category: string };
type Rules = { version: number; rules: Rule[]; reason: string | null; created_at: number | null;
  history: { version: number; rules: number; reason: string; created_at: number }[] };
type Report = { id: string; user_id: string; reporter_email: string | null; reporter_name: string | null; category: string;
  note: string | null; excerpt: string | null; status: "open" | "resolved" | "dismissed"; created_at: number; suspended: boolean };
type Stop = { user_id: string; email: string | null; display_name: string | null; stops: number; last_at: number; suspended: boolean };
type Suspension = { id: string; user_id: string; email: string | null; display_name: string | null; reason: string; created_at: number };

const BASE = "/api/admin/agent-safety";
const CATEGORIES: Record<string, string> = { illegal: "违法", violence: "暴力", sexual_minors: "涉及未成年人的色情", self_harm: "自残",
  hate: "仇恨", fraud: "诈骗", privacy: "隐私", malware: "恶意软件", other: "其它" };
const DIRECTIONS: Record<Rule["applies_to"], string> = { both: "消息与回答", input: "仅消息", output: "仅回答" };
const REPORT_CATEGORIES: Record<string, string> = { harmful: "有害", illegal: "违法", privacy: "隐私", wrong: "回答错误", other: "其它" };

export function AgentSafetyTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  return <div className="col gap16">
    {held.has("platform.config.view") && <RulesPanel onDenied={onDenied} held={held} />}
    <ReportsPanel onDenied={onDenied} held={held} />
    <StopsPanel onDenied={onDenied} held={held} />
  </div>;
}

function RulesPanel({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const { t } = useI18n();
  const load = useAdminData<Rules>(`${BASE}/rules`, onDenied);
  const [draft, setDraft] = useState<Rule[] | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const manage = permitted(held, "platform.config.manage");
  useEffect(() => { if (load.data) setDraft(load.data.rules); }, [load.data]);
  if (load.loading && !load.data) return <div className="empty">{t("加载中…")}</div>;
  if (load.err && !load.data) return <div className="notice err" role="alert">{load.err} <button className="btn" onClick={load.reload}>{t("重试")}</button></div>;
  if (!load.data || !draft) return null;
  const data = load.data;
  const change = (index: number, patch: Partial<Rule>) => setDraft(draft.map((rule, i) => i === index ? { ...rule, ...patch } : rule));
  const publish = async () => {
    setBusy(true); setMessage(null);
    try {
      const out = await put<Rules>(`${BASE}/rules`, { rules: draft.map(({ id, ...rest }) => rest.pattern ? { id, ...rest } : null).filter(Boolean),
        reason, expected_version: data.version });
      setReason(""); setMessage({ ok: true, text: t("已发布版本 {version}。", { version: String(out.version) }) }); load.reload();
    } catch (e) { setMessage({ ok: false, text: errText(e) }); } finally { setBusy(false); }
  };
  return <section className="card card-b col gap12" aria-labelledby="as-rules">
    <div className="row gap8 wrap"><h2 id="as-rules" className="grow">{t("内容安全规则")}</h2>
      <span className="badge">{data.version ? t("当前版本 {version}", { version: String(data.version) }) : t("尚未发布规则")}</span></div>
    <p className="muted">{t("检查用户发给 Agent 的消息与模型的回答。命中时消息不发送、回答停止且不再显示；只记录规则、方向与长度，不记录内容。每次发布是一个带理由的新版本。")}</p>
    <div className="table-wrap"><table className="tbl">
      <thead><tr><th scope="col">{t("类型")}</th><th scope="col">{t("检查")}</th><th scope="col">{t("类别")}</th><th scope="col">{t("内容")}</th><th scope="col"><span className="sr-only">{t("移除")}</span></th></tr></thead>
      <tbody>{draft.map((rule, index) => <tr key={`${rule.id}-${index}`}>
        <td><select aria-label={t("类型")} value={rule.kind} disabled={manage.disabled} onChange={e => change(index, { kind: e.target.value as Rule["kind"] })}>
          <option value="keyword">{t("关键词")}</option><option value="pattern">{t("模式（正则）")}</option></select></td>
        <td><select aria-label={t("检查")} value={rule.applies_to} disabled={manage.disabled} onChange={e => change(index, { applies_to: e.target.value as Rule["applies_to"] })}>
          {Object.entries(DIRECTIONS).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</select></td>
        <td><select aria-label={t("类别")} value={rule.category} disabled={manage.disabled} onChange={e => change(index, { category: e.target.value })}>
          {Object.entries(CATEGORIES).map(([value, label]) => <option key={value} value={value}>{t(label)}</option>)}</select></td>
        <td><input aria-label={t("内容")} value={rule.pattern} maxLength={200} disabled={manage.disabled} onChange={e => change(index, { pattern: e.target.value })} /></td>
        <td><button type="button" className="btn sm" disabled={manage.disabled} onClick={() => setDraft(draft.filter((_, i) => i !== index))}>{t("移除")}</button></td>
      </tr>)}</tbody>
    </table></div>
    {manage.disabled ? <p className="muted small">{t("你的平台角色可以查看规则，但不能发布")}</p> : <>
      <div><button type="button" className="btn sm" onClick={() => setDraft([...draft, { id: `r${Date.now().toString(36)}`, kind: "keyword", pattern: "", applies_to: "both", category: "other" }])}>{t("添加规则")}</button></div>
      <label className="col gap4">{t("发布理由")}<input value={reason} maxLength={500} onChange={e => setReason(e.target.value)} /></label>
      <div><button type="button" className="btn primary" disabled={busy || reason.trim().length < 4} onClick={() => void publish()}>
        {busy ? t("正在发布…") : t("发布为版本 {version}", { version: String(data.version + 1) })}</button></div>
    </>}
    <div aria-live="polite">{message && <p className={`notice ${message.ok ? "ok" : "err"}`} role={message.ok ? "status" : "alert"}>{message.text}</p>}</div>
    {data.history.length > 0 && <details><summary>{t("发布记录")}</summary><ul className="small">{data.history.map(h => <li key={h.version}>
      {t("版本 {version} · {count} 条 · {at}", { version: String(h.version), count: String(h.rules), at: fmtTime(h.created_at) })} — {h.reason}</li>)}</ul></details>}
  </section>;
}

function SuspendForm({ userId, onDone }: { userId: string; onDone: () => void }) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true); setError(null);
    try { await post(`${BASE}/users/${encodeURIComponent(userId)}/suspend`, { reason }); onDone(); }
    catch (e) { setError(errText(e)); } finally { setBusy(false); }
  };
  return <details><summary>{t("暂停 Agent 使用权限")}</summary><div className="col gap8">
    <p className="muted small">{t("暂停后该账户不能再给 Agent 发消息，进行中的回答会停止；理由会记录在审计中。")}</p>
    <label className="col gap4">{t("暂停理由")}<input value={reason} maxLength={500} onChange={e => setReason(e.target.value)} /></label>
    <div><button type="button" className="btn danger sm" disabled={busy || reason.trim().length < 4} onClick={() => void submit()}>{t("确认暂停")}</button></div>
    {error && <p className="notice err" role="alert">{error}</p>}
  </div></details>;
}

function ReportsPanel({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<"open" | "all">("open");
  const load = useAdminData<{ items: Report[] }>(`${BASE}/reports?status=${status}`, onDenied);
  const manage = permitted(held, "platform.users.manage");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const decide = async (id: string, decision: "resolved" | "dismissed") => {
    setError(null);
    try { await post(`${BASE}/reports/${encodeURIComponent(id)}/decide`, { decision, resolution: notes[id] || null }); load.reload(); }
    catch (e) { setError(errText(e)); }
  };
  return <section className="card card-b col gap12" aria-labelledby="as-reports">
    <div className="row gap8 wrap"><h2 id="as-reports" className="grow">{t("举报")}</h2>
      <select aria-label={t("举报")} value={status} onChange={e => setStatus(e.target.value as "open" | "all")}>
        <option value="open">{t("待处理")}</option><option value="all">{t("全部")}</option></select></div>
    <p className="muted">{t("用户对 Agent 回答的举报。只有举报人选择附上时才有回答原文；这里看不到对话的其它内容。")}</p>
    {error && <p className="notice err" role="alert">{error}</p>}
    {load.err && <p className="notice err" role="alert">{load.err}</p>}
    {load.loading && !load.data ? <div className="empty">{t("加载中…")}</div> : !load.data?.items.length ? <p className="muted">{t("没有举报。")}</p> :
      <ul className="col gap12">{load.data.items.map(r => <li key={r.id} className="card card-b col gap8">
        <div className="row gap8 wrap"><b className="grow">{t(REPORT_CATEGORIES[r.category] ?? "其它")}</b>
          <span className="badge">{t(r.status === "open" ? "待处理" : r.status === "resolved" ? "已处理" : "已驳回")}</span>
          {r.suspended && <span className="badge warn">{t("已暂停 Agent")}</span>}<span className="muted small">{fmtTime(r.created_at)}</span></div>
        <dl className="idp-facts">
          <dt>{t("举报人")}</dt><dd>{r.reporter_name || r.reporter_email || r.user_id}</dd>
          {r.note && <><dt>{t("备注")}</dt><dd>{r.note}</dd></>}
          <dt>{t("回答原文（举报人附上）")}</dt><dd>{r.excerpt ? <blockquote>{r.excerpt}</blockquote> : <span className="muted">{t("未附回答原文")}</span>}</dd>
        </dl>
        {r.status === "open" && !manage.disabled && <div className="col gap8">
          <label className="col gap4">{t("处理说明（可选）")}<input value={notes[r.id] ?? ""} maxLength={1000} onChange={e => setNotes({ ...notes, [r.id]: e.target.value })} /></label>
          <div className="row gap8"><button type="button" className="btn sm primary" onClick={() => void decide(r.id, "resolved")}>{t("标记为已处理")}</button>
            <button type="button" className="btn sm" onClick={() => void decide(r.id, "dismissed")}>{t("驳回")}</button></div>
        </div>}
      </li>)}</ul>}
  </section>;
}

function StopsPanel({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const { t } = useI18n();
  const stops = useAdminData<{ items: Stop[] }>(`${BASE}/stops?days=7`, onDenied);
  const suspended = useAdminData<{ items: Suspension[] }>(`${BASE}/suspensions`, onDenied);
  const manage = permitted(held, "platform.users.manage");
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const reload = () => { stops.reload(); suspended.reload(); };
  const lift = async (userId: string) => {
    setError(null);
    try { await post(`${BASE}/users/${encodeURIComponent(userId)}/lift`, { reason: reasons[userId] || "" }); reload(); }
    catch (e) { setError(errText(e)); }
  };
  return <>
    <section className="card card-b col gap12" aria-labelledby="as-stops">
      <h2 id="as-stops">{t("近 7 天被拦下的次数")}</h2>
      <p className="muted">{t("按账户统计规则拦下的消息与回答，只有次数与时间。")}</p>
      {stops.err && <p className="notice err" role="alert">{stops.err}</p>}
      {!stops.data?.items.length ? <p className="muted">{t("没有被拦下的内容。")}</p> :
        <ul className="col gap8">{stops.data.items.map(s => <li key={s.user_id} className="col gap4">
          <div className="row gap8 wrap"><span className="grow">{s.display_name || s.email || s.user_id}</span>
            <span className="muted small">{t("{count} 次，最近 {at}", { count: String(s.stops), at: fmtTime(s.last_at) })}</span>
            {s.suspended && <span className="badge warn">{t("已暂停 Agent")}</span>}</div>
          {!s.suspended && !manage.disabled && <SuspendForm userId={s.user_id} onDone={reload} />}
        </li>)}</ul>}
    </section>
    <section className="card card-b col gap12" aria-labelledby="as-suspended">
      <h2 id="as-suspended">{t("已暂停的账户")}</h2>
      {error && <p className="notice err" role="alert">{error}</p>}
      {suspended.err && <p className="notice err" role="alert">{suspended.err}</p>}
      {!suspended.data?.items.length ? <p className="muted">{t("没有被暂停的账户。")}</p> :
        <ul className="col gap12">{suspended.data.items.map(s => <li key={s.id} className="col gap4">
          <div className="row gap8 wrap"><b className="grow">{s.display_name || s.email || s.user_id}</b><span className="muted small">{fmtTime(s.created_at)}</span></div>
          <p className="small">{t("理由")}：{s.reason}</p>
          {!manage.disabled && <div className="row gap8 wrap">
            <input aria-label={t("恢复理由")} placeholder={t("恢复理由")} value={reasons[s.user_id] ?? ""} maxLength={500}
              onChange={e => setReasons({ ...reasons, [s.user_id]: e.target.value })} />
            <button type="button" className="btn sm" disabled={(reasons[s.user_id] ?? "").trim().length < 4} onClick={() => void lift(s.user_id)}>{t("恢复 Agent 使用权限")}</button></div>}
        </li>)}</ul>}
    </section>
  </>;
}
