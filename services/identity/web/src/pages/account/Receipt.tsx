import { useParams, Link } from 'react-router-dom';
import { get, useSession } from '@synwevo/auth-client';
import { useI18n, type Translator } from '@synwevo/ui';
import { Topbar } from '../../components/Topbar';
import { useLoad } from '../../lib/hooks';
import { ErrorNotice, errorText, timeText } from './common';
import { WALLET_API, paymentText, receiptText, type Receipt } from './walletModel';
import './wallet.messages';
import './account.css';
import './wallet.css';

export const METHOD_LABELS: Record<string, string> = { card: '银行卡', wechat_pay: '微信支付', alipay: '支付宝' };
const TERMS = '充值后不可退款；余额只能用于 Synwevo 的服务，不能转让或提现。';

/** The labelled lines of a receipt: the server's fields only, none computed here; a field the
 *  server did not send is left out rather than shown empty or guessed. */
export function receiptLines(data: Receipt, t: Translator, locale: string, timezone: string): { label: string; value: string }[] {
  return [
    { label: t('订单号'), value: data.order_id },
    { label: t('金额'), value: paymentText(data.amount_minor, data.currency || 'CNY', locale) },
    { label: t('付款方式'), value: data.method ? t(METHOD_LABELS[data.method] || data.method) : '' },
    { label: t('付款时间'), value: data.paid_at ? timeText(data.paid_at, locale, timezone) : '' },
    { label: t('入账时间'), value: data.credited_at ? timeText(data.credited_at, locale, timezone) : '' },
    { label: t('付款方'), value: data.payer_kind ? t(data.payer_kind === 'organization' ? '组织钱包' : '个人钱包') : '' },
    { label: t('支付服务商流水号'), value: data.provider_reference || '' },
    { label: t('收款方'), value: data.operator_name || '' },
  ].filter(line => line.value);
}

export function ReceiptView({ data, timezone, onDownload }: { data: Receipt; timezone: string; onDownload: () => void }) {
  const { t, locale } = useI18n();
  return <article className="card card-b col gap16 receipt" aria-labelledby="receipt-title">
    <header className="row wrap gap12"><div className="grow"><p className="account-eyebrow">Synwevo</p>
      <h1 id="receipt-title">{t('充值收据')}</h1></div>
      <p className="receipt-amount">{paymentText(data.amount_minor, data.currency || 'CNY', locale)}</p></header>
    <dl className="receipt-lines">{receiptLines(data, t, locale, timezone).map(line => <div key={line.label}><dt>{line.label}</dt><dd>{line.value}</dd></div>)}</dl>
    <p className="small muted">{t(TERMS)}</p>
    <div className="row wrap gap8 receipt-actions">
      <button type="button" className="btn primary" onClick={() => window.print()}>{t('打印或存为 PDF')}</button>
      <button type="button" className="btn" onClick={onDownload}>{t('下载收据')}</button>
      <Link className="btn ghost" to="/account/wallet">{t('返回钱包')}</Link>
    </div>
  </article>;
}

/** The receipt of one credited top-up, printable (or saved as PDF by the browser) and
 *  downloadable as plain text. Only the order's payer can read it; the server decides. */
export default function ReceiptPage() {
  const { orderId = '' } = useParams();
  const { me } = useSession();
  const { t, locale } = useI18n();
  const receipt = useLoad<Receipt>(me && orderId ? `${me.id}:receipt:${orderId}` : null,
    signal => get(`${WALLET_API}/receipts/${encodeURIComponent(orderId)}`, signal));
  const timezone = me?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const data = receipt.error ? null : receipt.data;
  function download() {
    if (!data) return;
    const text = receiptText(t('Synwevo 充值收据'), [...receiptLines(data, t, locale, timezone), { label: t('说明'), value: t(TERMS) }]);
    const url = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = `synwevo-receipt-${data.order_id.replace(/[^A-Za-z0-9_-]/g, '_')}.txt`;
    document.body.append(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  return <div className="app"><Topbar title={t('充值收据')} /><div className="wallet-scroll">
    <main className="account-center col gap24 wallet-page receipt-page">
      <ErrorNotice error={receipt.error ? errorText(receipt.error, t) : null} retry={receipt.reload} />
      {receipt.loading && !data && <p role="status">{t('正在读取收据…')}</p>}
      {data && <ReceiptView data={data} timezone={timezone} onDownload={download} />}
      {receipt.error !== null && <div><Link className="btn" to="/account/wallet">{t('返回钱包')}</Link></div>}
    </main>
  </div></div>;
}
