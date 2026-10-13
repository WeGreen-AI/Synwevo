import { useEffect, useRef, useState, type Ref } from 'react';
import { Link } from 'react-router-dom';
import { ApiError, get } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { errorText } from './common';
import { POLL_LIMIT_MS, WALLET_API, paymentText, pollDelay, topupFailureText, topupFinal, topupView,
  type TopupStatus as Status, type TopupView } from './walletModel';
import './wallet.messages';
import './topup.messages';

/** What each state says. A cancelled checkout is still asked about: a payment can complete after
 *  the browser came back, and only the provider's signed confirmation decides. */
export const TOPUP_TEXT: Record<TopupView, { title: string; body: string; tone: 'info' | 'ok' | 'warn' | 'err' }> = {
  verifying: { title: '充值请求结果待核实', body: '尚未确认支付服务商是否已创建付款页面。原充值请求与订单已保留，请勿重复充值。状态可能需要管理员核实；你可以稍后刷新，或向管理员提供订单号。', tone: 'warn' },
  pending: { title: '正在确认付款', body: '支付服务商确认后余额会自动更新，通常只需几秒。可以留在此页，也可以稍后回来查看。', tone: 'info' },
  paid: { title: '充值已到账', body: '金额已计入钱包，可以继续使用。', tone: 'ok' },
  failed: { title: '付款没有完成', body: '这笔订单没有扣款，余额未变化。可以重新发起充值。', tone: 'err' },
  expired: { title: '订单已过期', body: '付款页面已超时关闭，没有扣款。需要时请重新发起充值。', tone: 'warn' },
  cancelled: { title: '你离开了付款页面', body: '如果没有完成付款，余额不会变化。我们仍在核对这笔订单，若付款其实已完成会自动入账。', tone: 'info' },
  slow: { title: '确认时间比平常长', body: '付款结果还没有送达。不需要重复付款；稍后刷新即可看到最新状态。', tone: 'warn' },
};

/** One state of an order, as the server reported it. Final states take focus once (the heading),
 *  so a keyboard or screen-reader user lands on the outcome; waiting states announce politely. */
export function TopupStatusView({ view, status, error, canRefresh, onRefresh, onDismiss, onRetry, headingRef }: {
  view: TopupView; status: Status | null; error: string | null; canRefresh: boolean;
  onRefresh: () => void; onDismiss: () => void; onRetry: () => void; headingRef?: Ref<HTMLHeadingElement>;
}) {
  const { t, locale } = useI18n();
  const text = TOPUP_TEXT[view];
  // An order the provider refused to create says why (and that nothing was charged).
  const body = (view === 'failed' && topupFailureText(status?.failure_reason)) || text.body;
  const final = topupFinal(status?.status);
  return <section className={`card card-b col gap12 topup-status tone-${text.tone}`} aria-labelledby="topup-status-title" data-state={view}>
    <div className="row gap12">
      <span className={`topup-mark ${view}`} aria-hidden="true">
        {view === 'paid' ? <svg viewBox="0 0 24 24" width="28" height="28"><circle cx="12" cy="12" r="11" /><path d="M7 12.5l3.2 3.2L17 9" /></svg>
          : view === 'pending' || view === 'cancelled' ? <span className="topup-spinner" />
            : <svg viewBox="0 0 24 24" width="28" height="28"><circle cx="12" cy="12" r="11" /><path d="M12 7v6M12 16.5v.5" /></svg>}
      </span>
      <div className="grow">
        <h2 id="topup-status-title" ref={headingRef} tabIndex={-1}>{t(text.title)}</h2>
        <p className="muted" role="status" aria-live="polite">{t(body)}</p>
      </div>
    </div>
    {status && <dl className="topup-facts">
      <div><dt>{t('金额')}</dt><dd>{paymentText(status.amount_minor, status.currency || 'CNY', locale)}</dd></div>
      <div><dt>{t('订单号')}</dt><dd><code>{status.order_id}</code></dd></div>
    </dl>}
    {error && <p className="notice err" role="alert">{error}</p>}
    <div className="row wrap gap8">
      {view === 'paid' && status && <Link className="btn primary" to={`/account/wallet/receipts/${encodeURIComponent(status.order_id)}`}>{t('查看收据')}</Link>}
      {(view === 'failed' || view === 'expired') && <button type="button" className="btn primary" onClick={onRetry}>{t('重新充值')}</button>}
      {canRefresh && <button type="button" className="btn" onClick={onRefresh}>{t('刷新状态')}</button>}
      <button type="button" className="btn ghost" onClick={onDismiss}>{t(final ? '完成' : '稍后再看')}</button>
    </div>
  </section>;
}

/** The return page of the hosted checkout (`/account/wallet?topup=<order>&result=success|cancel`):
 *  asks the server for the order's state until it is final, announces each change politely, and
 *  plays one restrained success mark (none under reduced motion). */
export default function TopupStatusPanel({ orderId, cancelled, onPaid, onDismiss, onRetry }: {
  orderId: string; cancelled: boolean; onPaid: () => void; onDismiss: () => void; onRetry: () => void;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [slow, setSlow] = useState(false);
  const [tick, setTick] = useState(0);
  const paidOnce = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const abort = new AbortController();
    const started = Date.now();
    let attempt = 0, timer = 0;
    setSlow(false);
    const again = () => {
      if (Date.now() - started >= POLL_LIMIT_MS) { setSlow(true); return; }
      timer = window.setTimeout(ask, pollDelay(attempt++));
    };
    const ask = () => {
      get<Status>(`${WALLET_API}/topups/${encodeURIComponent(orderId)}`, abort.signal).then(result => {
        setStatus(result); setError(null);
        if (!topupFinal(result.status)) { again(); return; }
        if (result.status === 'paid' && !paidOnce.current) { paidOnce.current = true; onPaid(); }
      }).catch(e => {
        if (abort.signal.aborted) return;
        setError(e);
        // The service's own 4xx (an unknown or foreign order) is an answer; a network fault, a
        // 5xx or something in front of the service is retried until the time limit.
        if (!(e instanceof ApiError && !e.upstream && e.status >= 400 && e.status < 500)) again();
      });
    };
    ask();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [orderId, tick]);   // eslint-disable-line react-hooks/exhaustive-deps

  const view = topupView(status?.status, slow, cancelled, status?.creation_status);
  useEffect(() => { if (topupFinal(status?.status)) heading.current?.focus(); }, [status?.status]);
  const answered = error instanceof ApiError && !error.upstream && error.status >= 400 && error.status < 500;
  return <TopupStatusView view={view} status={status} headingRef={heading}
    error={error !== null && !status ? errorText(error, t) : null}
    canRefresh={slow || (error !== null && !answered && !topupFinal(status?.status))}
    onRefresh={() => setTick(n => n + 1)} onDismiss={onDismiss} onRetry={onRetry} />;
}
