import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { get, useSession } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { Topbar } from '../../components/Topbar';
import { useLoad } from '../../lib/hooks';
import { ErrorNotice, errorText, timeText } from './common';
import TopupDialog from './TopupDialog';
import TopupStatusPanel from './TopupStatus';
import { LimitsSection, ReminderSection } from './WalletSettings';
import { WalletGate } from './WalletGate';
import { WALLET_API, entryKindLabel, ledgerText, moneyText, parsePayer, payerKey, payerQuery, withQuery,
  type Entry, type Overview, type Payer, type PayerOption, type Statement } from './walletModel';
import './wallet.messages';
import './topup.messages';
import './account.css';
import './wallet.css';

const NEWEST = '9223372036854775807';
const PAGE = '10';

/** The payers this person may look at, from the server; the person is always first. */
export function usePayers(meId: string | undefined) {
  const payers = useLoad<{ items: PayerOption[] }>(meId ? `${meId}:payers` : null, signal => get(`${WALLET_API}/payers`, signal));
  return { list: payers.data?.items || [], error: payers.error, loading: payers.loading };
}
export function payerName(payer: Payer, list: PayerOption[], t: (s: string) => string): string {
  if (payer.kind === 'user') return t('个人钱包');
  return list.find(item => item.kind === 'organization' && item.id === payer.id)?.name || t('组织钱包');
}
export function PayerPicker({ payer, list, onChange }: { payer: Payer; list: PayerOption[]; onChange: (payer: Payer) => void }) {
  const { t } = useI18n();
  if (list.length < 2) return null;
  return <label className="field wallet-payer"><span>{t('付款方')}</span>
    <select className="input" value={payerKey(payer)} onChange={e => onChange(parsePayer(e.target.value))}>
      {list.map(item => <option key={payerKey(item)} value={payerKey(item)}>
        {item.kind === 'user' ? t('个人钱包') : t('组织：{name}', { name: item.name || item.id })}</option>)}
    </select></label>;
}

/** The balance as the ledger states it, per unit: available first, then how it is made up. Every figure
 *  is exact, to the digit the unit holds, so "available = booked − held" can be checked on the page. */
export function BalanceFigures({ overview }: { overview: Overview }) {
  const { t, locale } = useI18n();
  return <>
    {overview.environment === 'test' && <p className="notice info">{t('测试环境：以下记录不代表真实充值或正式账单。')}</p>}
    {!overview.balances.length && <p className="muted">{t('这个钱包还没有可用的记账单位。')}</p>}
    {overview.balances.map(balance => <article className="wallet-unit" key={balance.unit} aria-label={t('{unit} 余额', { unit: balance.unit })}>
      <p className="wallet-available-label">{t('可用余额')}</p>
      {/* Keyed by the amount: a changed balance re-enters with the shared motion tokens (none under reduced motion). */}
      <p className="wallet-available" key={balance.available}>{ledgerText(balance.available, balance.scale, balance.unit, locale)}</p>
      {balance.frozen && <p className="notice err" role="status">{t('这个钱包因一笔支付争议已被冻结，暂时不能发起新的付费调用。')}</p>}
      <dl className="wallet-amounts">
        {([['held', '预留中'], ['booked', '已入账余额'], ['used', '累计消费']] as const).map(([key, label]) => <div key={key}>
          <dt>{t(label)}</dt><dd>{ledgerText(balance[key], balance.scale, balance.unit, locale)}</dd></div>)}
      </dl>
    </article>)}
    <p className="muted small">{t('可用余额 = 已入账余额 − 预留中。预留是正在进行的调用按上限冻结的金额，结算后只扣实际用量，余下的退回可用余额。')}</p>
  </>;
}

function RecentActivity({ payer, timezone, generation }: { payer: Payer; timezone: string; generation: number }) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<Entry[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const load = (before: string, append: boolean, signal?: AbortSignal) => {
    setLoading(true); setError(null);
    return get<Statement>(withQuery(`${WALLET_API}/statement`, { ...payerQuery(payer), before_seq: before, limit: PAGE }), signal)
      .then(page => { setItems(old => (append ? [...old, ...page.items] : page.items)); setNext(page.next_before_seq || null); })
      .catch(e => { if (!signal?.aborted) setError(e); })
      .finally(() => { if (!signal?.aborted) setLoading(false); });
  };
  useEffect(() => {
    const abort = new AbortController();
    void load(NEWEST, false, abort.signal);
    return () => abort.abort();
  }, [payerKey(payer), generation]);   // eslint-disable-line react-hooks/exhaustive-deps
  return <section className="card card-b col gap12" aria-labelledby="wallet-history-title" aria-busy={loading}>
    <div className="row wrap gap8"><h2 id="wallet-history-title" className="secttl grow">{t('最近动态')}</h2>
      <Link className="btn ghost sm" to={withQuery('/account/wallet/usage', payer.kind === 'organization' ? { payer: payerKey(payer) } : {})}>{t('查看用量明细')}</Link></div>
    <p className="small muted">{t('已入账的充值、赠送与已结算消费，最新的在前。时间采用 {timezone}。', { timezone })}</p>
    <ErrorNotice error={error ? errorText(error, t) : null} retry={() => void load(NEWEST, false)} />
    {!loading && !error && !items.length && <p className="muted">{t('还没有任何入账或消费记录。')}</p>}
    {!!items.length && <ol className="wallet-history" aria-label={t('钱包流水')}>{items.map(entry => <li key={entry.id}>
      <div><b>{t(entryKindLabel(entry.kind, entry.delta))}</b>
        <p className="muted small"><time dateTime={new Date(entry.created_at * 1000).toISOString()}>{timeText(entry.created_at, locale, timezone)}</time></p></div>
      <span className={`wallet-delta${entry.delta.startsWith('-') ? '' : ' in'}`}>{moneyText(entry.delta, entry.scale, entry.unit, locale, { signed: true })}</span>
    </li>)}</ol>}
    {loading && <p role="status" className="small muted">{t('正在读取钱包记录…')}</p>}
    {next && !loading && <div><button type="button" className="btn" onClick={() => void load(next, true)}>{t('加载更早的记录')}</button></div>}
  </section>;
}

export default function Wallet() {
  return <WalletGate><WalletPage /></WalletGate>;
}

function WalletPage() {
  const { me } = useSession();
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const payer = parsePayer(params.get('payer'));
  const payers = usePayers(me?.id);
  const [generation, setGeneration] = useState(0);
  const [topup, setTopup] = useState(false);
  const order = params.get('topup');
  const summary = useLoad<Overview>(me ? `${me.id}:${payerKey(payer)}:${generation}` : null,
    signal => get(withQuery(WALLET_API, payerQuery(payer)), signal));
  const timezone = me?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const name = payerName(payer, payers.list, t);
  // Kept while a refresh is in flight (no flash), but never shown under another payer's name.
  const overview = summary.data && !summary.error && payerKey(summary.data.payer) === payerKey(payer) ? summary.data : null;
  const choose = (next: Payer) => setParams(old => {
    const query = new URLSearchParams(old);
    if (next.kind === 'user') query.delete('payer'); else query.set('payer', payerKey(next));
    return query;
  });
  const closeStatus = () => setParams(old => { const query = new URLSearchParams(old); query.delete('topup'); query.delete('result'); return query; });

  return <div className="app"><Topbar title={t('钱包')} /><div className="wallet-scroll">
    <main className="account-center col gap24 wallet-page">
      <header className="row wrap gap12 wallet-head"><div className="grow"><h1>{t('钱包')}</h1>
        <p className="muted">{t('余额、充值、最近动态和你自己的支出设置。金额以服务端账本为准。')}</p></div>
        <PayerPicker payer={payer} list={payers.list} onChange={choose} />
      </header>
      {order && <TopupStatusPanel orderId={order} cancelled={params.get('result') === 'cancel'} onDismiss={closeStatus}
        onPaid={() => setGeneration(n => n + 1)} onRetry={() => { closeStatus(); setTopup(true); }} />}
      <section className="card card-b col gap16 wallet-balance" aria-labelledby="wallet-balance-title" aria-busy={summary.loading}>
        <div className="row wrap gap8"><h2 id="wallet-balance-title" className="secttl grow">{name}</h2>
          <button type="button" className="btn ghost sm" disabled={summary.loading} onClick={() => setGeneration(n => n + 1)}>{t('刷新')}</button></div>
        <ErrorNotice error={summary.error ? errorText(summary.error, t) : null} retry={summary.reload} />
        {summary.loading && !overview && <p role="status">{t('正在读取钱包额度…')}</p>}
        {overview && <BalanceFigures overview={overview} />}
        <div className="row wrap gap8">
          <button type="button" className="btn primary" onClick={() => setTopup(true)} disabled={!overview}>{t('充值')}</button>
          <Link className="btn" to={withQuery('/account/wallet/usage', payer.kind === 'organization' ? { payer: payerKey(payer) } : {})}>{t('用量与明细')}</Link>
        </div>
      </section>
      <RecentActivity payer={payer} timezone={timezone} generation={generation} />
      {payer.kind === 'user' && <><LimitsSection /><ReminderSection /></>}
      {payer.kind === 'organization' && <p className="small muted">{t('组织钱包由组织的付费管理者充值和查看。成员的对话内容不在这里显示，用量只按成员与模型汇总。')}</p>}
      <div><Link className="btn" to="/account">{t('返回账户设置')}</Link></div>
    </main>
    {topup && <TopupDialog payer={payer} payerName={name} onClose={() => setTopup(false)} />}
  </div></div>;
}
