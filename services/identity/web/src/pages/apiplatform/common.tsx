import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ApiError, get, post, useSession } from '@synwevo/auth-client';
import { Modal, useI18n } from '@synwevo/ui';
import { useLoad } from '../../lib/hooks';
import { refusalText } from '../../lib/refusals';
import '../api-platform.messages';

export const BASE = '/api/admin/api-platform';
export type Page<T> = { items: T[]; total: number; offset: number; limit: number; next_offset: number | null };
export type Approval = { id: string; name: string; workspace_id: string; credential_kind: string;
  requested_scopes: string[]; approved_scopes: string[]; requested_resources: string[];
  status: string; approved_by: string | null; expires_at: number; consumed_at: number | null;
  reason: string; decision_reason: string | null };
export type Outcome = Record<string, unknown>;
export type Action = { title: string; impact: ReactNode; run: (reason: string) => Promise<Outcome> };

export function usePage<T>(path: string) {
  const [position, setPosition] = useState({ path, offset: 0 });
  const offset = path === position.path ? position.offset : 0;
  const url = `${path}${path.includes('?') ? '&' : '?'}limit=25&offset=${offset}`;
  const load = useLoad<Page<T>>(url, signal => get(url, signal));
  return { ...load, offset, move: (offset: number) => setPosition({ path, offset }),
    refresh: () => { setPosition({ path, offset: 0 }); load.reload(); } };
}

/** A refusal as a sentence in the reader's language (lib/refusals.ts). A console call's answer
 *  from Synwevo Docs is the thing being debugged, so its own message is kept beside it verbatim. */
export function failureText(error: unknown, t: (source: string) => string): string {
  return error instanceof ApiError ? refusalText(error, CONSOLE_CODES) : t('操作失败，请检查输入或重试。');
}
/** A rotation refusal names the client id in its sentence; the console shows the credential it
 *  acted on beside the notice, so the code says the rest. */
const CONSOLE_CODES: Record<string, string> = {
  no_client: '找不到该服务客户端。',
  client_disabled: '该服务客户端已停用，没有可轮换的密钥。',
  public_client: '这是公共客户端，没有密钥可以轮换。',
  // the sentence names the key that replaced it
  already_rotated: '该密钥已经轮换过；请改用新密钥，或对新密钥再次轮换。',
};

export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  if (!error) return null;
  const code = error instanceof ApiError ? error.code : 'request_failed';
  const answered = error instanceof ApiError && error.detail?.console_request === true;
  return <div className="notice err" role="alert">
    {failureText(error, t)} <code>{code}</code>
    {answered && <code> · {(error as ApiError).originalMessage}</code>}
    {error instanceof ApiError && error.requestId && <code> · {error.requestId}</code>}
    {retry && <button className="btn" onClick={retry}>{t('重试')}</button>}
  </div>;
}

export function Pager({ page, move, busy }: { page: Page<unknown> | null; move: (n: number) => void; busy: boolean }) {
  const { t } = useI18n();
  return <div className="row gap8 wrap">
    <span className="muted">{t('共 {count} 条', { count: page?.total ?? 0 })}</span>
    <button className="btn sm" disabled={busy || !page || page.offset === 0} onClick={() => move(Math.max(0, (page?.offset ?? 0) - 25))}>{t('上一页')}</button>
    <button className="btn sm" disabled={busy || page?.next_offset == null} onClick={() => move(page?.next_offset ?? 0)}>{t('下一页')}</button>
  </div>;
}

export function DataState({ loading, error, empty, retry }: { loading: boolean; error: unknown; empty: boolean; retry: () => void }) {
  const { t } = useI18n();
  if (loading) return <p role="status">{t('加载中…')}</p>;
  if (error) return <ErrorNotice error={error} retry={retry} />;
  return empty ? <p className="muted">{t('没有记录。')}</p> : null;
}

export function Confirm({ action, close, done }: { action: Action; close: () => void; done: (out: Outcome) => void }) {
  const { t } = useI18n();
  const { me } = useSession();
  const id = useId();
  const [reason, setReason] = useState('');
  const [password, setPassword] = useState('');
  const [reauth, setReauth] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [until, setUntil] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const pending = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (reauth) input.current?.focus(); }, [reauth]);
  useEffect(() => { const tick = () => setRemaining(Math.max(0, Math.ceil((until - Date.now()) / 1000)));
    tick(); const timer = setInterval(tick, 1000); return () => clearInterval(timer); }, [until]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending.current || remaining || Array.from(reason.trim()).length < 8) return;
    pending.current = true; setBusy(true); setError(null);
    try {
      if (reauth) { await post('/api/auth/reauth', { password, scope: 'admin' }); setPassword(''); setReauth(false); }
      done(await action.run(reason.trim()));
    } catch (e) {
      if (e instanceof ApiError && e.code === 'reauth_required') setReauth(true);
      else { setError(e); if (e instanceof ApiError && e.retryAfter) setUntil(Date.now() + e.retryAfter * 1000); }
      setPassword('');
    } finally { pending.current = false; setBusy(false); }
  }
  return <Modal title={action.title} role="alertdialog" onClose={() => { if (!busy) close(); }} size="narrow"
    footer={<><button className="btn" disabled={busy} onClick={close}>{t('取消')}</button>
      <button form={id} type="submit" className="btn primary" disabled={busy || !!remaining || Array.from(reason.trim()).length < 8 || (reauth && !password)}>
        {busy ? t('处理中…') : remaining ? t('{count} 秒后重试', { count: remaining }) : t('确认操作')}</button></>}>
    <form id={id} className="col gap12" onSubmit={submit}>
      <div className="notice info">{action.impact}</div>
      <label className="field">{t('操作理由（8 至 500 个字，将写入审计）')}
        <textarea className="input" required minLength={8} maxLength={500} value={reason} disabled={busy} onChange={e => setReason(e.target.value)} />
      </label>
      {reauth && <><p>{t('请重新验证身份后继续。')}</p>
        <input hidden readOnly autoComplete="username" value={me?.email || ''} />
        <label className="field">{t('你的口令')}<input ref={input} className="input" type="password" autoComplete="current-password" required disabled={busy} value={password} onChange={e => setPassword(e.target.value)} /></label></>}
      <ErrorNotice error={error} />
    </form>
  </Modal>;
}

export function Secret({ result, close }: { result: Outcome; close: () => void }) {
  const { t } = useI18n();
  const [show, setShow] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const value = String(result.key || result.client_secret || result.secret || '');
  return <Modal title={t('密钥仅显示一次')} onClose={close} size="wide" footer={<button className="btn primary" onClick={close}>{t('已安全保存，关闭')}</button>}>
    <div className="col gap12"><p className="notice info">{t('关闭后无法再次查看，请立即保存到你的密钥管理器。')}</p>
      {result.client_id != null && <code>{String(result.client_id)}</code>}
      <label className="field">{t('密钥')}<input className="input" readOnly autoComplete="off" spellCheck={false} type={show ? 'text' : 'password'} value={value} /></label>
      <div className="row gap8"><button className="btn" onClick={() => setShow(!show)}>{show ? t('隐藏') : t('显示')}</button>
        <button className="btn" onClick={async () => { try { await navigator.clipboard.writeText(value); setCopied(true); setCopyFailed(false); } catch { setCopyFailed(true); } }}>{copied ? t('已复制') : t('复制密钥')}</button></div>
      {copyFailed && <p role="alert">{t('复制失败，请手动选择并保存密钥。')}</p>}
    </div>
  </Modal>;
}

export async function requestMachine(credential: string, reason: string, method: string, path: string, query = {}, body?: unknown, idempotencyKey?: string): Promise<Outcome> {
  const out = await post<{ status: number; data: Outcome; request_id?: string }>(BASE + '/request', {
    credential, reason, method, path, query, body,
    ...(idempotencyKey === undefined ? {} : { idempotency_key: idempotencyKey }),
  });
  if (out.status >= 400) {
    const detail = out.data.error as { code?: string; message?: string } | undefined;
    const e = new ApiError(out.status, detail?.code || 'machine_request_failed', detail?.message || 'machine_request_failed', { console_request: true });
    e.requestId = out.request_id; throw e;
  }
  return out.data;
}
