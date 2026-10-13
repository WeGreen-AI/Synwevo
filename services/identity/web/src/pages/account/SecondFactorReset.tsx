import { useEffect, useState } from 'react';
import { ApiError, post } from '@synwevo/auth-client';
import { Brand, LanguageMenu, getLocale, t as tr, useI18n } from '@synwevo/ui';
import { ErrorNotice, errorText, timeText } from './common';
import { useAccountToken } from './linkToken';
import './second-factor-reset.messages';

/**
 * A26: the page the second-factor reset mail links to (`/account/second-factor-reset#token=…`).
 *
 * A platform administrator asked, after confirming the person's identity, to remove the account's
 * second factor in 72 hours; the mail went to the account's own address with this link. The page
 * works signed out — the person may be unable to sign in, and the link is the only credential it
 * uses: it says when the reset takes effect and what it will do, and cancels it with one button.
 * The server answers every unusable link the same way (410 `bad_token`: unknown, used, cancelled,
 * completed or voided). A link the server still previews is cancellable whatever this browser's
 * clock says: the reset runs only once its notice was delivered and 72 hours have passed since, so it
 * can stay pending past the time first announced. Only a refused link whose shown time has passed is
 * called expired rather than invalid — the reset has then most likely taken effect.
 */
export type ResetLinkState =
  | { phase: 'loading' } | { phase: 'pending'; due_at: number } | { phase: 'done' }
  | { phase: 'invalid' } | { phase: 'expired'; due_at: number };

const nowSeconds = () => Date.now() / 1000;
/** A loaded preview: the server still holds the reset pending, so it can be cancelled. `now` is
 *  kept for the callers' signature; the browser clock no longer decides. */
export const previewState = (due_at: number, _now = nowSeconds()): ResetLinkState => ({ phase: 'pending', due_at });
/** A `bad_token` answer: past the due time it is the reset taking effect, otherwise the link no longer works. */
export function refusedState(previous: ResetLinkState, now = nowSeconds()): ResetLinkState {
  return (previous.phase === 'pending' || previous.phase === 'expired') && previous.due_at <= now
    ? { phase: 'expired', due_at: previous.due_at } : { phase: 'invalid' };
}
const unusable = (e: unknown) => e instanceof ApiError && !e.upstream && e.code === 'bad_token';

/** The page for a given state. Pure, so it can be rendered on its own (tests/admin-second-factor-reset.test.mjs). */
export function SecondFactorResetView({ state, busy = false, error = null, onCancel, onRetry }: {
  state: ResetLinkState; busy?: boolean; error?: string | null; onCancel?: () => void; onRetry?: () => void;
}) {
  // Subscribed for a language switch; the sentences read the current language (as the
  // administration pages do), so the page renders in any language on its own.
  useI18n();
  const when = (at: number) => timeText(at, getLocale(), 'UTC') + ' UTC';
  return <main className="authpage"><section className="authcard col gap12">
    <div className="authcard-top"><Brand href="/login" product="Account" productBelow={380} /><LanguageMenu /></div>
    <h1>{tr('取消第二因素重置')}</h1>
    <ErrorNotice error={error} retry={onRetry} />
    {state.phase === 'loading' && !error && <p role="status">{tr('加载中…')}</p>}
    {state.phase === 'pending' && <>
      <p>{tr('Synwevo 平台管理员核验你的身份后，为这个账户申请了重置第二因素。')}</p>
      <p><b>{tr('重置将于 {date} 生效。', { date: when(state.due_at) })}</b></p>
      <p>{tr('生效时会发生：')}</p>
      <ul>
        <li>{tr('删除账户的动态验证码和恢复码；如果申请包含安全密钥，也会一并移除。')}</li>
        <li>{tr('结束所有设备上的登录。')}</li>
        <li>{tr('口令、资料和数据不变；之后请用口令登录，再重新设置第二因素。')}</li>
      </ul>
      <p>{tr('如果你没有请求平台帮你找回第二因素，请立即取消，并联系平台管理员。如果是你请求的，无需任何操作。')}</p>
      <p className="muted small">{tr('取消后什么都不会改变。此操作不会自动登录。')}</p>
      <button type="button" className="btn primary" disabled={busy} onClick={onCancel}>{busy ? tr('处理中…') : tr('取消重置')}</button>
    </>}
    {state.phase === 'done' && <p className="notice ok" role="status">{tr('重置已取消：你的第二因素保持不变。')}</p>}
    {state.phase === 'invalid' && <p className="notice warn" role="alert">{tr('这个链接无效或已失效：重置可能已经取消或完成，或链接不完整。请打开邮件里的完整链接；如有疑问，请联系平台管理员。')}</p>}
    {state.phase === 'expired' && <p className="notice warn" role="alert">{tr('取消期限已过（{date}）：重置已经或即将生效，这个链接不能再取消它。之后请用口令登录，再重新设置第二因素。', { date: when(state.due_at) })}</p>}
    <a href="/login">{tr('返回登录')}</a>
  </section></main>;
}

export default function SecondFactorReset() {
  useI18n();
  const token = useAccountToken();
  // Without a token there is nothing to ask the server about.
  const [state, setState] = useState<ResetLinkState>(() => token ? { phase: 'loading' } : { phase: 'invalid' });
  const [busy, setBusy] = useState(false);
  // The refusal itself, worded at render time: a language switch rewords it without asking again
  // (asking again after a cancellation would find the link spent).
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!token) { setState({ phase: 'invalid' }); return; }
    let live = true;
    setState({ phase: 'loading' }); setError(null);
    post<{ status: string; due_at: number }>('/api/auth/second-factor-reset/preview', { token })
      .then((r) => { if (live) setState(previewState(r.due_at)); })
      .catch((e) => { if (!live) return; if (unusable(e)) setState({ phase: 'invalid' }); else setError(e); });
    return () => { live = false; };
  }, [token, revision]);
  const cancel = () => {
    setBusy(true); setError(null);
    post('/api/auth/second-factor-reset/cancel', { token })
      .then(() => setState({ phase: 'done' }))
      .catch((e) => { if (unusable(e)) setState((s) => refusedState(s)); else setError(e); })
      .finally(() => setBusy(false));
  };
  return <SecondFactorResetView state={state} busy={busy} error={error ? errorText(error, tr) : null} onCancel={cancel}
    onRetry={state.phase === 'loading' ? () => setRevision((n) => n + 1) : undefined} />;
}
