import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { ApiError, get, post } from '@synwevo/auth-client';
import { fmtTime, useI18n } from '@synwevo/ui';
import { useLoad, useUnsavedChanges } from '../lib/hooks';
import { ActionDialog } from './ActionDialog';
import { permitted, type Held } from './access';
import { errText } from './labels';
import './notification-recovery.messages';

const API = '/api/admin/notification-recovery';
const LIMIT = 25;
const wrap: CSSProperties = { minWidth: 0, maxWidth: '100%', overflowWrap: 'anywhere' };
const blockedMessages = {
  not_failed: '状态已变化，不再是入队失败，只能查看。',
  mail_already_queued: '邮件可能已经入队，只能查看，不能再次恢复。',
  historical_delivery: '这是历史投递记录，只能查看，不能再次恢复。',
  unverified_failure: '无法核实失败来源，只能查看，不能恢复。',
  retry_not_exhausted: '自动重试尚未耗尽，请等待当前调度器处理。',
} as const;
const categories: Record<string, string> = {
  security: '账户安全', organization: '组织与工作空间', members: '成员导入', approval: '审批',
  conversation: '消息', docs: '文档处理', automation: '自动化与接口', platform: '平台运行',
};
type NotificationRecoveryItem = {
  notification_id: string; user_id: string; category: string; status: string;
  attempts: number; updated_at: number; error_code: string | null;
  recoverable: boolean; blocked_reason: string | null;
};
type RecoveryPage = { items: NotificationRecoveryItem[]; total: number; limit: number; offset: number;
  snapshot: number; next_offset: number | null };
type RecoveryPreview = { target: NotificationRecoveryItem; expires_at: number; preview_token: string };
type RecoveryAttempt = { notification_id: string; attempts: number;
  body: Readonly<{ preview_token: string; reason: string; idempotency_key: string }> };
type RecoveryResult = { ok: true; operation_id: string; state: 'succeeded'; notification_id: string;
  status: 'pending'; attempts: number; recovery_scheduled: true };
type DialogResult = { preview: RecoveryPreview } | { result: RecoveryResult };
type Position = { q: string; offset: number; snapshot?: number };
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const timestamp = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 253402300799;
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0 && v.length <= 512 && !/[\u0000-\u001f\u007f]/.test(v);
const code = (v: unknown): v is string => text(v) && /^[a-zA-Z0-9_.:-]+$/.test(v);

/** Retain only the agreed safe metadata, including in signed previews. Never render extras. */
function readRecoveryItem(raw: unknown): NotificationRecoveryItem | null {
  if (!record(raw) || !text(raw.notification_id) || !text(raw.user_id) || !code(raw.category) || !code(raw.status)
    || !integer(raw.attempts) || !timestamp(raw.updated_at) || !(raw.error_code === null || code(raw.error_code))
    || typeof raw.recoverable !== 'boolean' || !(raw.blocked_reason === null || code(raw.blocked_reason))) return null;
  return { notification_id: raw.notification_id, user_id: raw.user_id, category: raw.category, status: raw.status,
    attempts: raw.attempts, updated_at: raw.updated_at, error_code: raw.error_code,
    recoverable: raw.recoverable, blocked_reason: raw.blocked_reason };
}
const canRecoverNotification = (item: NotificationRecoveryItem) =>
  item.status === 'failed' && item.recoverable === true && item.blocked_reason === null;
function recoveryBlockedMessage(item: NotificationRecoveryItem): string | null {
  if (canRecoverNotification(item)) return null;
  if (item.blocked_reason && Object.hasOwn(blockedMessages, item.blocked_reason)) return blockedMessages[item.blocked_reason as keyof typeof blockedMessages];
  return item.status !== 'failed' ? blockedMessages.not_failed : blockedMessages.unverified_failure;
}
function readRecoveryPage(raw: unknown, position: Position): RecoveryPage | null {
  if (!record(raw) || !Array.isArray(raw.items) || !integer(raw.total) || raw.limit !== LIMIT || raw.offset !== position.offset
    || !integer(raw.snapshot) || (position.snapshot !== undefined && raw.snapshot > position.snapshot)
    || !(raw.next_offset === null || integer(raw.next_offset) && raw.next_offset > position.offset)
    || raw.items.length > LIMIT) return null;
  const items = raw.items.map(readRecoveryItem);
  if (items.some(item => !item) || new Set(items.map(item => item!.notification_id)).size !== items.length) return null;
  return { items: items as NotificationRecoveryItem[], total: raw.total, limit: LIMIT, offset: position.offset,
    snapshot: raw.snapshot, next_offset: raw.next_offset as number | null };
}
function readRecoveryPreview(raw: unknown, selected: NotificationRecoveryItem): RecoveryPreview | null {
  if (!record(raw) || raw.action !== 'admin.notification.recover_enqueue' || raw.scope !== 'platform' || raw.risk !== 'high'
    || typeof raw.preview_token !== 'string' || !raw.preview_token.trim() || raw.preview_token.length > 16384
    || !timestamp(raw.expires_at) || raw.expires_at * 1000 <= Date.now()) return null;
  const target = readRecoveryItem(raw.target);
  if (!target || target.notification_id !== selected.notification_id || target.user_id !== selected.user_id
    || !canRecoverNotification(target)) return null;
  return { target, expires_at: raw.expires_at, preview_token: raw.preview_token };
}
function readRecoveryResult(raw: unknown, attempt: RecoveryAttempt): RecoveryResult | null {
  if (!record(raw) || raw.ok !== true || !text(raw.operation_id) || raw.state !== 'succeeded'
    || raw.notification_id !== attempt.notification_id || raw.status !== 'pending'
    || !integer(raw.attempts) || raw.attempts < attempt.attempts || raw.recovery_scheduled !== true) return null;
  return { ok: true, operation_id: raw.operation_id, state: 'succeeded', notification_id: raw.notification_id,
    status: 'pending', attempts: raw.attempts, recovery_scheduled: true };
}

function Metadata({ item }: { item: NotificationRecoveryItem }) {
  const { t, locale } = useI18n();
  return <dl className="col gap8" style={wrap}>
    <div><dt>{t('通知编号')}</dt><dd className="mono" style={{ margin: 0 }}>{item.notification_id}</dd></div>
    <div><dt>{t('用户编号')}</dt><dd className="mono" style={{ margin: 0 }}>{item.user_id}</dd></div>
    <div><dt>{t('通知类别')}</dt><dd style={{ margin: 0 }}>{t(categories[item.category] || '其他通知')}</dd></div>
    <div><dt>{t('累计尝试次数')}</dt><dd style={{ margin: 0 }}>{item.attempts.toLocaleString(locale)}</dd></div>
    <div><dt>{t('更新时间')}</dt><dd style={{ margin: 0 }}>{fmtTime(item.updated_at)}</dd></div>
    <div><dt>{t('错误代码')}</dt><dd className="mono" style={{ margin: 0 }}>{item.error_code || '—'}</dd></div>
  </dl>;
}

type Props = { held: Held; onDenied: () => void };
export function NotificationRecovery(props: Props) {
  const { t } = useI18n();
  if (permitted(props.held, 'platform.mail.view').disabled) return <p className="notice" role="note">{t('你的平台角色没有查看通知入队失败的权限。')}</p>;
  return <RecoverySection {...props} />;
}

function RecoverySection({ held, onDenied }: Props) {
  const { t, locale } = useI18n();
  const id = useId();
  const manage = permitted(held, 'platform.mail.manage');
  const [input, setInput] = useState('');
  const [position, setPosition] = useState<Position>({ q: '', offset: 0 });
  const [revision, setRevision] = useState(0);
  const params = new URLSearchParams({ q: position.q, limit: String(LIMIT), offset: String(position.offset) });
  if (position.snapshot !== undefined) params.set('snapshot', String(position.snapshot));
  const path = `${API}?${params}`;
  // useAdminPage fixes its page size at 50. This endpoint's 25-row, next_offset contract
  // uses the same cancellable loader without changing the shared hook for other pages.
  const load = useLoad<RecoveryPage>(`${path}#${revision}`, async signal => {
    const raw = await get<unknown>(path, signal);
    const page = readRecoveryPage(raw, position);
    if (!page) throw new ApiError(502, 'notification_recovery_invalid_page', '');
    return page;
  });
  const [selected, setSelected] = useState<NotificationRecoveryItem | null>(null);
  const [plan, setPlan] = useState<RecoveryPreview | null>(null);
  const [attempt, setAttempt] = useState<RecoveryAttempt | null>(null);
  const attemptRef = useRef<RecoveryAttempt | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const flight = useRef(false);
  const alive = useRef(true);
  const [uncertain, setUncertain] = useState(false);
  const uncertainRef = useRef(false);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<RecoveryResult | null>(null);
  const [now, setNow] = useState(Date.now());
  const permission = useRef(!manage.disabled);
  permission.current = !manage.disabled;
  useUnsavedChanges(uncertain || busy);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (load.error instanceof ApiError && !load.error.upstream && load.error.status === 404
      && load.error.code !== 'notification_delivery_missing') onDenied();
  }, [load.error, onDenied]);
  useEffect(() => {
    if (!plan) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [plan]);
  const expired = !!plan && plan.expires_at * 1000 <= now;
  const usablePage = !load.loading && !load.error ? load.data : null;
  const objectUnavailable = error instanceof ApiError && !error.upstream
    && ['notification_delivery_missing', 'notification_not_recoverable'].includes(error.code);
  function errorMessage(err: unknown): string {
    if (!(err instanceof ApiError) || err.upstream) return errText(err);
    if (err.code === 'notification_recovery_invalid_page') return t('无法核实通知入队失败列表，请刷新后重试。');
    if (err.code === 'notification_delivery_missing') return t('这条通知投递记录已不存在，请刷新通知列表。');
    if (['notification_delivery_changed', 'admin_preview_stale'].includes(err.code)) return t('这条通知的状态已变化，请重新预览恢复影响。');
    if (err.code === 'notification_not_recoverable') {
      const blocked = err.detail?.blocked_reason;
      return t(typeof blocked === 'string' && Object.hasOwn(blockedMessages, blocked)
        ? blockedMessages[blocked as keyof typeof blockedMessages] : '此通知当前不能恢复，请刷新通知列表查看原因。');
    }
    return errText(err);
  }

  function refresh(q = position.q) { setPosition({ q, offset: 0 }); setRevision(n => n + 1); }
  function clearAttempt() { attemptRef.current = null; setAttempt(null); }
  async function preview(item: NotificationRecoveryItem): Promise<RecoveryPreview> {
    const raw = await post<unknown>(`${API}/${encodeURIComponent(item.notification_id)}/preview`, {});
    const next = readRecoveryPreview(raw, item);
    if (!next) throw new ApiError(409, 'admin_preview_invalid', '');
    return next;
  }
  function acceptPreview(next: RecoveryPreview) {
    if (!alive.current) return;
    setPlan(next); setConfirmed(false); setInvalid(false); setError(null); setNow(Date.now());
  }
  async function open(item: NotificationRecoveryItem) {
    if (!permission.current || flight.current || uncertainRef.current || !canRecoverNotification(item)) return;
    flight.current = true; setBusy(true); setSelected(item); setPlan(null); clearAttempt();
    setConfirmed(false); setInvalid(false); setError(null); setResult(null); setDialogOpen(true);
    try { acceptPreview(await preview(item)); }
    catch (err) { if (alive.current) setError(err); }
    finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  async function run(reason: string): Promise<DialogResult> {
    if (!alive.current || !permission.current || flight.current || !selected) throw new ApiError(403, 'platform_permission_denied', '');
    if (!plan) {
      flight.current = true; setBusy(true); setError(null);
      try { return { preview: await preview(selected) }; }
      catch (err) { if (alive.current) setError(err); throw err; }
      finally { flight.current = false; if (alive.current) setBusy(false); }
    }
    if (!confirmed) throw new ApiError(400, 'admin_confirmation_required', '');
    if (!uncertainRef.current && (invalid || plan.expires_at * 1000 <= Date.now())) {
      setInvalid(true); throw new ApiError(409, 'admin_preview_expired', '');
    }
    let original = attemptRef.current;
    if (!original) {
      const length = Array.from(reason.trim()).length;
      if (length < 8 || length > 500) throw new ApiError(400, 'missing_reason', '');
      original = { notification_id: plan.target.notification_id, attempts: plan.target.attempts,
        body: Object.freeze({ preview_token: plan.preview_token, reason: reason.trim(), idempotency_key: crypto.randomUUID() }) };
      attemptRef.current = original; setAttempt(original);
    }
    flight.current = true; setBusy(true); setError(null);
    try {
      const raw = await post<unknown>(`${API}/${encodeURIComponent(original.notification_id)}/execute`, original.body);
      const out = readRecoveryResult(raw, original);
      if (!out) throw new Error('Unverified recovery result');
      return { result: out };
    } catch (err) {
      if (alive.current) {
        // A later refusal cannot disprove an earlier commit whose response was lost.
        if (uncertainRef.current || !(err instanceof ApiError) || err.upstream || err.status < 400 || err.status === 408 || err.status >= 500) {
          uncertainRef.current = true; setUncertain(true);
        } else if (err.code !== 'reauth_required' && err.status !== 429) {
          setInvalid(true); setConfirmed(false); clearAttempt();
        }
        if (!(err instanceof ApiError) || err.code !== 'reauth_required') setError(err);
      }
      throw err;
    } finally { flight.current = false; if (alive.current) setBusy(false); }
  }
  function done(out: DialogResult) {
    if (!alive.current) return;
    if ('preview' in out) { acceptPreview(out.preview); return; }
    setResult(out.result); uncertainRef.current = false; setUncertain(false); clearAttempt();
    setPlan(null); setSelected(null); setDialogOpen(false); setError(null); refresh();
  }
  function close() {
    if (flight.current) return;
    setDialogOpen(false); setConfirmed(false);
    if (!uncertainRef.current) { setPlan(null); setSelected(null); clearAttempt(); setError(null); }
  }

  return <section className="card card-b col gap12" style={wrap} aria-labelledby={`${id}-title`}>
    <div className="row gap12 wrap"><h2 id={`${id}-title`} className="grow">{t('通知入队失败')}</h2>
      <button type="button" className="btn sm" disabled={load.loading} onClick={() => refresh()}>{t('刷新通知列表')}</button></div>
    <p className="small muted">{t('仅显示安全元数据。恢复会交给当前调度器重新检查通知偏好、已读、失效、免打扰和摘要预算；累计尝试次数保留，符合条件时才会重新排队。')}</p>
    <p className="small muted">{t('分页沿用首次读取的高水位，不纳入随后新增的记录；搜索或刷新会重新读取。记录状态仍以当前服务端检查为准。')}</p>
    {manage.disabled && <p className="notice" role="note">{t('你的平台角色可以查看通知入队失败，但不能恢复。')}</p>}
    <form className="row gap12 wrap" onSubmit={event => { event.preventDefault(); refresh(input.trim()); }}>
      <label className="field grow" style={{ minWidth: 0 }}>{t('搜索通知编号或用户编号')}
        <input type="search" className="input" value={input} maxLength={100} onChange={event => setInput(event.target.value)} /></label>
      <button type="submit" className="btn" disabled={load.loading}>{t('搜索通知')}</button>
    </form>
    {result && <div className="notice ok col gap8" role="status" style={wrap}>
      <p>{t('已安排重新检查并排队，不代表已寄出。')}</p>
      <p>{t('通知编号：{id}；累计尝试次数：{attempts}；操作编号：{operation}', { id: result.notification_id, attempts: result.attempts.toLocaleString(locale), operation: result.operation_id })}</p>
    </div>}
    {uncertain && <div className="notice warn col gap8" role="alert" style={wrap}>
      <p>{t('恢复结果尚不确定。请保留此页面，仅重试原请求；刷新列表不能证明原请求失败。')}</p>
      {attempt && <p>{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p>}
      <button type="button" className="btn sm" disabled={manage.disabled || busy || dialogOpen} onClick={() => { setConfirmed(false); setDialogOpen(true); }}>{t('核对并重试原请求')}</button>
    </div>}
    {load.error != null && <div className="notice err col gap8" role="alert"><p>{errorMessage(load.error)}</p>
      <button type="button" className="btn sm" onClick={load.reload} disabled={load.loading}>{t('重试读取通知列表')}</button></div>}
    {load.loading && <p role="status">{t('正在读取通知入队失败…')}</p>}
    <div className="list-wrap admin-cards-wrap" style={wrap} tabIndex={0} role="region" aria-label={t('通知入队失败记录')}>
      <table className="gov admin-cards-table" aria-label={t('通知入队失败记录')} aria-busy={load.loading} style={{ tableLayout: 'fixed' }}>
        <colgroup><col style={{ width: '30%' }} /><col style={{ width: '32%' }} /><col style={{ width: '22%' }} /><col style={{ width: '16%' }} /></colgroup>
        <thead><tr>{['通知与用户', '类别与入队状态', '尝试与更新时间', '恢复操作'].map(label => <th scope="col" key={label}>{t(label)}</th>)}</tr></thead>
        <tbody>{usablePage?.items.map((item, index) => {
          const blocked = recoveryBlockedMessage(item);
          return <tr key={item.notification_id}>
            <th scope="row" id={`${id}-row-${index}`}>
              <div><span className="small muted">{t('通知编号')} </span><code>{item.notification_id}</code></div>
              <div className="cell-note">{t('用户编号')} <code>{item.user_id}</code></div>
            </th>
            <td data-label={t('通知类别')}><div style={{ minWidth: 0 }}><div><span>{t(categories[item.category] || '其他通知')}</span>
              <span className="muted"> · {t(item.status === 'failed' ? '入队失败' : '状态已变化')}</span></div>
              <div className="cell-note">{t('错误代码')} <code>{item.error_code || '—'}</code></div>
              {blocked && <div id={`${id}-blocked-${index}`} className="cell-note">{t(blocked)}</div>}</div></td>
            <td data-label={t('累计尝试次数')}><div style={{ minWidth: 0 }}><b>{item.attempts.toLocaleString(locale)}</b>
              <div className="cell-note">{t('更新时间')} <time dateTime={new Date(item.updated_at * 1000).toISOString()}>{fmtTime(item.updated_at)}</time></div></div></td>
            <td className="act" data-label={t('恢复操作')} style={{ whiteSpace: 'normal' }}><button type="button" className="btn sm"
              aria-describedby={`${id}-row-${index}${blocked ? ` ${id}-blocked-${index}` : ''}`}
              disabled={manage.disabled || !!blocked || busy || uncertain || dialogOpen} onClick={() => void open(item)}>{t('预览恢复影响')}</button></td>
          </tr>;
        })}{usablePage && !usablePage.items.length && <tr className="empty-row"><td colSpan={4}>{t(position.q ? '没有匹配的通知入队失败记录。' : '当前没有通知入队失败记录。')}</td></tr>}</tbody>
      </table>
    </div>
    <nav className="pager wrap" style={wrap} aria-label={t('通知入队失败分页')}>
      <span role="status">{usablePage ? t('第 {page} 页，共 {total} 项', { page: Math.floor(position.offset / LIMIT) + 1, total: usablePage.total.toLocaleString(locale) }) : t('等待列表结果')}</span>
      <button type="button" className="btn sm" disabled={!usablePage || !position.offset} onClick={() => {
        if (usablePage) setPosition({ ...position, offset: Math.max(0, position.offset - LIMIT), snapshot: usablePage.snapshot });
      }}>{t('上一页')}</button>
      <button type="button" className="btn sm" disabled={!usablePage || usablePage.next_offset === null} onClick={() => {
        if (usablePage?.next_offset != null) setPosition({ ...position, offset: usablePage.next_offset, snapshot: usablePage.snapshot });
      }}>{t('下一页')}</button>
      {usablePage && position.offset > 0 && !usablePage.items.length && <button type="button" className="btn sm" onClick={() => refresh()}>{t('返回最新通知列表')}</button>}
    </nav>
    {dialogOpen && selected && <ActionDialog<DialogResult> title={t('确认恢复通知入队')} size="wide" reauthScope="admin"
      confirmLabel={t(!plan ? '读取恢复预览' : uncertain ? '重试原请求' : '安排重新检查')}
      reason={plan && !attempt ? 'required' : 'none'}
      ready={!manage.disabled && !busy && (uncertain || !objectUnavailable) && (!plan || confirmed && (uncertain || !expired && !invalid))}
      impact={<div className="col gap12" style={wrap}>
        <Metadata item={plan?.target || selected} />
        <p>{t('恢复会将这条通知标记为待检查，再由当前调度器检查偏好、已读、失效、免打扰和摘要预算。可能跳过或延后，累计尝试次数不会清零。')}</p>
        <p><b>{t('安排恢复不代表已经入队或寄出。')}</b></p>
        {manage.disabled && <p className="notice warn" role="alert">{t('管理权限已变化，恢复与重试已禁用。')}</p>}
      </div>}
      run={run} onDone={done} onClose={close}>
      {busy && !plan && <p role="status">{t('正在核对通知与恢复影响…')}</p>}
      {error != null && <p className="notice err" role="alert">{errorMessage(error)}</p>}
      {objectUnavailable && !uncertain && <button type="button" className="btn sm" disabled={busy} onClick={() => { close(); refresh(); }}>{t('刷新通知列表')}</button>}
      {plan && <>
        <p>{t('预览有效至 {time}。状态变化或预览过期后需重新预览。', { time: fmtTime(plan.expires_at) })}</p>
        {attempt && <div className="col gap8" style={wrap}><p style={{ whiteSpace: 'pre-wrap' }}>{t('原恢复理由：{reason}', { reason: attempt.body.reason })}</p>
          <p>{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p></div>}
        {uncertain && <p className="notice warn" role="alert">{t('结果未知时将沿用原预览令牌、理由和请求编号，即使预览已到期。不会创建新的恢复请求。')}</p>}
        {!uncertain && (expired || invalid) && <p className="notice warn" role="alert">{t('此恢复预览已失效，请重新预览后再确认。')}</p>}
        {!uncertain && <button type="button" className="btn sm" disabled={busy || manage.disabled} onClick={() => void open(selected)}>{t('重新预览恢复影响')}</button>}
        <label className="row gap8" style={{ alignItems: 'flex-start', ...wrap }}><input type="checkbox" checked={confirmed}
          disabled={busy || manage.disabled || !uncertain && (expired || invalid)} onChange={event => setConfirmed(event.target.checked)} />
          <span>{t('我已核对通知、用户、累计尝试次数和恢复影响。')}</span></label>
      </>}
    </ActionDialog>}
  </section>;
}
