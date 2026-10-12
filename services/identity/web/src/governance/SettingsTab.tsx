import { useEffect, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { formatNumber, fmtTime, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { AdminSecondFactorTab, RegistrationTab, ThrottlesTab } from "./GovernanceTabs";
import { permitted, type Held } from "./access";
import { useAdminData } from "./data";
import { errText } from "./labels";
import { useUnsavedChanges } from "../lib/hooks";
import "./settings.messages";

type State = { revision: number; overrides: Record<string, number>; values: Record<string, number>; sources: Record<string, string> };
type Field = { key: string; type: string; unit: string; default: number; min: number; max: number };
type Draft = Record<string, { inherit: boolean; value: string }>;
type Request = { expected_revision: number; changes?: Record<string, number | null>; rollback_revision?: number };
type Plan = { expected_revision: number; before: State; after: State; expires_at: number; preview_token: string };
type Version = State & { actor_id: string; reason: string; operation_id: string; created_at: number; rollback_revision: number | null };
const INTERVAL = "notifications.conversation_digest_interval_s";
const CAP = "notifications.conversation_digest_daily_cap";
const NAMES: Record<string, string> = { [INTERVAL]: "两封消息摘要的最短间隔", [CAP]: "24小时内最多发送摘要数" };
const draftFrom = (state: State): Draft => Object.fromEntries(Object.entries(state.values).map(([key, value]) =>
  [key, { inherit: !Object.hasOwn(state.overrides, key), value: String(value) }]));

/** Real versioned notification settings; registration retains its existing service below. */
export function SettingsTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const { t } = useI18n();
  const manage = permitted(held, "platform.config.manage");
  const load = useAdminData<{ catalogue: Field[]; state: State }>("/api/admin/settings", onDenied);
  const [before, setBefore] = useState<number | null>(null);
  const history = useAdminData<{ items: Version[]; next_before_revision: number | null }>(`/api/admin/settings/history?limit=20${before === null ? "" : `&before_revision=${before}`}`, onDenied);
  const [base, setBase] = useState<State | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [conflict, setConflict] = useState<State | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmation, setConfirmation] = useState<{ request: Request; plan: Plan; key: string } | null>(null);
  const [result, setResult] = useState<{ operation_id: string; revision: number } | null>(null);
  useEffect(() => {
    if (!load.data) return;
    if (!base) { setBase(load.data.state); setDraft(draftFrom(load.data.state)); }
    else if (load.data.state.revision > base.revision) setConflict(load.data.state);
  }, [load.data, base]);
  const fields = load.data?.catalogue || [];
  const source = (state: State, key: string) => t(Object.hasOwn(state.overrides, key) ? "平台覆盖" : "内置默认值");
  const value = (state: State, key: string) => `${formatNumber(state.values[key])} ${t(key === INTERVAL ? "秒" : "封")}`;
  const changes: Record<string, number | null> = {};
  let valid = !!base && fields.length > 0;
  for (const field of fields) {
    const item = draft[field.key];
    if (!item) { valid = false; continue; }
    const number = Number(item.value);
    if (!item.inherit && (!/^\d+$/.test(item.value) || !Number.isSafeInteger(number) || number < field.min || number > field.max)) valid = false;
    if (item.inherit) { if (base && Object.hasOwn(base.overrides, field.key)) changes[field.key] = null; }
    else if (base?.overrides[field.key] !== number) changes[field.key] = number;
  }
  useUnsavedChanges(Object.keys(changes).length > 0);
  function recordFailure(err: unknown) {
    if (err instanceof ApiError && err.code === "settings_revision_conflict" && err.detail?.current) setConflict(err.detail.current);
    setError(errText(err));
  }
  async function preview(request: Request, retainKey?: string) {
    if (previewing || manage.disabled) return;
    setPreviewing(true); setError(null);
    try {
      const plan = await post<Plan>("/api/admin/settings/preview", request);
      setConfirmation({ request, plan, key: retainKey || crypto.randomUUID() }); setConfirmed(false);
    } catch (err) { recordFailure(err); } finally { setPreviewing(false); }
  }
  const rebase = () => {
    if (!conflict) return;
    setBase(conflict); setConflict(null); setError(null); setConfirmation(null);
  };
  return <div className="col gap16">
    <section className="card card-b col gap16" aria-labelledby="settings-heading">
      <div className="row wrap gap12"><h2 id="settings-heading" className="grow">{t("消息摘要策略")}</h2>
        <button className="btn sm" disabled={load.loading || previewing} onClick={() => { load.reload(); history.reload(); }}>{t("刷新当前配置")}</button></div>
      <p className="muted">{t("仅调整已主动开启邮件摘要的用户。私信邮件默认关闭，不逐条发送，已读、静音、撤回或无权访问的内容不会寄出。")}</p>
      <p className="small muted">{t("配置发布后，后续派发读取新版本；尚未发送的摘要会重新核对预算。首次聚合窗口、免打扰和失败重试仍须满足。已经寄出的邮件无法收回。")}</p>
      {result && <p className="notice ok" role="status">{t("版本 {version} 已发布，操作编号：{operation}。后续派发将读取此版本或更新版本。", { version: result.revision, operation: result.operation_id })}</p>}
      {(load.err || error) && <p className="notice err" role="alert">{load.err || error}</p>}
      {load.loading && !base && <p role="status">{t("加载中…")}</p>}
      {base && <>
        <p className="small">{t("编辑基于版本 {version} · 作用域：全平台", { version: base.revision })}</p>
        {manage.disabled && <p className="notice" role="note">{t("你的平台角色可以查看配置，不能发布更改。")}</p>}
        {conflict && <div className="notice warn col gap8" role="alert">
          <b>{t("配置已更新为版本 {version}，你的输入已保留。", { version: conflict.revision })}</b>
          <ul>{fields.map(field => <li key={field.key}>{t(NAMES[field.key] || field.key)}：{value(base, field.key)} → {value(conflict, field.key)}（{source(conflict, field.key)}）</li>)}</ul>
          <button className="btn" onClick={rebase}>{t("保留我的输入，按最新版本重新比较")}</button>
        </div>}
        <fieldset disabled={manage.disabled || previewing || !!confirmation} className="col gap16" style={{ border: 0, padding: 0, minWidth: 0 }}>
          {fields.map(field => {
            const item = draft[field.key];
            if (!item) return null;
            const unit = t(field.key === INTERVAL ? "秒" : "封");
            return <div className="col gap8" key={field.key}>
              <label className="field"><span>{t(NAMES[field.key] || field.key)}（{unit}）</span>
                <input className="input" type="number" inputMode="numeric" min={field.min} max={field.max} step={1}
                  value={item.inherit ? field.default : item.value} disabled={item.inherit}
                  onChange={event => setDraft({ ...draft, [field.key]: { ...item, value: event.target.value } })}
                  aria-describedby={`setting-${field.key}`} /></label>
              <p className="small muted" id={`setting-${field.key}`}>{t("当前：{current}；来源：{source}；允许 {min}–{max} {unit}。", { current: value(base, field.key), source: source(base, field.key), min: field.min, max: field.max, unit })}</p>
              <label className="check"><input type="checkbox" checked={item.inherit} onChange={event => setDraft({ ...draft, [field.key]: { ...item, inherit: event.target.checked } })} />{t("使用内置默认值：{value} {unit}", { value: field.default, unit })}</label>
            </div>;
          })}
        </fieldset>
        <div><button className="btn primary" disabled={!valid || !Object.keys(changes).length || manage.disabled || previewing || !!conflict || !!load.err}
          onClick={() => void preview({ expected_revision: base.revision, changes })}>{previewing ? t("正在预览…") : t("预览配置差异")}</button></div>
      </>}
    </section>
    <section className="card card-b col gap12" aria-labelledby="settings-history-heading">
      <h2 id="settings-history-heading">{t("消息摘要配置历史")}</h2>
      <p className="small muted">{t("恢复历史配置会创建新版本，保留现有记录。只恢复摘要预算，不更改用户通知偏好或任何授权。")}</p>
      {history.err && <p className="notice err" role="alert">{history.err}</p>}
      {history.loading ? <p role="status">{t("加载中…")}</p> : history.data && <>
        {!history.data.items.length && <p className="muted">{t("尚无发布记录，当前采用内置默认值。")}</p>}
        {history.data.items.map(item => <article className="notice col gap8" key={item.revision}>
          <div className="row wrap gap8"><b>{t("版本 {version}", { version: item.revision })}</b><span className="small muted">{fmtTime(item.created_at)}</span></div>
          <ul>{fields.map(field => <li key={field.key}>{t(NAMES[field.key] || field.key)}：{value(item, field.key)}（{source(item, field.key)}）</li>)}</ul>
          <p className="small">{t("发布理由：{reason}", { reason: item.reason })}</p>
          <p className="tiny muted">{t("操作者：{actor}；操作编号：{operation}", { actor: item.actor_id, operation: item.operation_id })}</p>
          <div><button className="btn sm" disabled={!base || manage.disabled || previewing || !!conflict || item.revision === base.revision}
            onClick={() => base && void preview({ expected_revision: base.revision, rollback_revision: item.revision })}>{t("预览恢复此版本")}</button></div>
        </article>)}
        <div className="row wrap gap8">
          {before !== null && <button className="btn" onClick={() => setBefore(null)}>{t("返回最新记录")}</button>}
          {history.data.next_before_revision !== null && <button className="btn" onClick={() => setBefore(history.data!.next_before_revision)}>{t("更早记录")}</button>}
          <button className="btn" disabled={!base || !Object.keys(base.overrides).length || manage.disabled || previewing || !!conflict}
            onClick={() => base && void preview({ expected_revision: base.revision, rollback_revision: 0 })}>{t("预览恢复全部默认值")}</button>
        </div>
      </>}
    </section>
    <RegistrationTab onDenied={onDenied} held={held} />
    <AdminSecondFactorTab onDenied={onDenied} held={held} />
    <ThrottlesTab onDenied={onDenied} held={held} />
    {confirmation && <ActionDialog key={confirmation.key} title={t("发布消息摘要策略")} confirmLabel={t("发布配置")} reason="required" ready={confirmed && !previewing && !conflict}
      impact={<div className="col gap8"><p>{t("仅影响全平台已开启的消息摘要；不打开邮件偏好，不撤回已发送邮件。")}</p>
        <ul>{fields.map(field => <li key={field.key}>{t(NAMES[field.key] || field.key)}：{value(confirmation.plan.before, field.key)}（{source(confirmation.plan.before, field.key)}） → {value(confirmation.plan.after, field.key)}（{source(confirmation.plan.after, field.key)}）</li>)}</ul>
        <p className="small muted">{t("预览有效至 {time}，配置变化后需重新预览。", { time: fmtTime(confirmation.plan.expires_at) })}</p>
        {conflict && <p className="notice warn" role="alert">{t("请关闭确认框，比较最新配置后重新预览。")}</p>}
      </div>}
      run={async reason => {
        try { return await post<{ operation_id: string; revision: number; state: State }>("/api/admin/settings/publish", {
          ...confirmation.request, preview_token: confirmation.plan.preview_token, idempotency_key: confirmation.key, reason,
        }); } catch (err) { recordFailure(err); throw err; }
      }}
      onClose={() => setConfirmation(null)}
      onDone={out => { setResult(out); setBase(out.state); setDraft(draftFrom(out.state)); setConflict(null); setConfirmation(null); setError(null); setBefore(null); load.reload(); history.reload(); }}>
      <button type="button" className="btn sm" disabled={previewing || !!conflict} onClick={() => void preview(confirmation.request)}>{t("重新预览配置差异")}</button>
      <label className="check"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />{t("我已核对配置差异及其影响。")}</label>
    </ActionDialog>}
  </div>;
}
