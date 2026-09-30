import { Link, useSearchParams } from 'react-router-dom';
import { get, useSession } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { Topbar } from '../../components/Topbar';
import { useLoad } from '../../lib/hooks';
import { ErrorNotice, errorText, timeText } from './common';
import { walletAmount } from './money';
import './wallet.messages';
import './account.css';
import './wallet.css';

type Balance = { unit: string; scale: number; booked: string; used: string; held: string; available: string };
type Overview = { environment: 'test' | 'live'; payer_user_id: string; balances: Balance[]; used_through_seq: string };
type Entry = { seq: string; id: string; kind: 'paid' | 'test_payment' | 'sale'; unit: string; scale: number; delta: string; created_at: number };
type Statement = { environment: 'test' | 'live'; payer_user_id: string; items: Entry[]; next_after_seq: string | null };
const API = '/api/auth/wallet';
const kinds = { paid: '充值到账', test_payment: '测试到账', sale: '已结算消费' };

export default function Wallet() {
  const { me } = useSession();
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const cursor = params.get('after') || '0';
  const path = `${API}/statement?after_seq=${encodeURIComponent(cursor)}&limit=50`;
  const summary = useLoad<Overview>(me ? `${me.id}:overview` : null, signal => get(API, signal));
  const history = useLoad<Statement>(me ? `${me.id}:${path}` : null, signal => get(path, signal));
  const timezone = me?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const selectPage = (after: string) => setParams(old => {
    const next = new URLSearchParams(old);
    if (after === '0') next.delete('after'); else next.set('after', after);
    return next;
  });
  const overview = !summary.loading && !summary.error && summary.data?.payer_user_id === me?.id ? summary.data : null;
  const entries = !history.loading && !history.error && history.data?.payer_user_id === me?.id ? history.data : null;
  return <div className="app"><Topbar title={t('个人钱包')} /><div className="wallet-scroll">
    <main className="account-center col gap24">
      <header className="row wrap gap12"><div className="grow"><h1>{t('个人钱包')}</h1>
        <p className="muted">{t('查看你本人的额度和已入账记录。工作空间切换不会改变付款人。')}</p></div>
        <button className="btn" disabled={summary.loading || history.loading} onClick={() => { summary.reload(); history.reload(); }}>{t('刷新')}</button>
      </header>
      <section className="col gap16" aria-labelledby="wallet-balance-title" aria-busy={summary.loading}>
        <h2 id="wallet-balance-title">{t('钱包额度')}</h2>
        <ErrorNotice error={summary.error ? errorText(summary.error, t) : null} retry={summary.reload} />
        {summary.loading && <p role="status">{t('正在读取钱包额度…')}</p>}
        {overview && <>
          <p className="notice info">{t(overview.environment === 'test' ? '测试环境：以下记录不代表真实充值或正式账单。' : '正式环境：金额以服务端已入账记录为准。')}</p>
          {overview.balances.map(balance => <article className="wallet-unit" key={balance.unit}>
            <h3>{balance.unit}</h3><dl className="wallet-amounts">
              {([['available', '可用额度'], ['held', '预留额度'], ['booked', '已入账余额'], ['used', '累计消费']] as const).map(([key, label]) => <div key={key}>
                <dt>{t(label)}</dt><dd>{walletAmount(balance[key], balance.scale, locale)} <small>{balance.unit}</small></dd>
              </div>)}
            </dl>
          </article>)}
          <p className="muted small">{t('可用额度 = 已入账余额 − 预留额度。预留尚未最终收费；累计消费仅包括已结算扣费，不是充值总额。')}</p>
          <p className="muted small">{t('不同单位分别记账，不合并为一个总额。')}</p>
        </>}
      </section>
      <section className="col gap16" aria-labelledby="wallet-history-title" aria-busy={history.loading}>
        <h2 id="wallet-history-title">{t('入账与消费记录')}</h2>
        <p className="small muted">{t('记录从早到晚排列，每页最多 50 条。时间采用 {timezone}。', { timezone })}</p>
        <ErrorNotice error={history.error ? errorText(history.error, t) : null} retry={history.reload} />
        {history.loading && <p role="status">{t('正在读取钱包记录…')}</p>}
        {entries && <>
          <p className="notice info">{t(entries.environment === 'test' ? '测试环境：以下记录不代表真实充值或正式账单。' : '正式环境：金额以服务端已入账记录为准。')}</p>
          {!entries.items.length && <p>{t('这一页没有已入账记录。')}</p>}
          <ol className="wallet-history" aria-label={t('钱包流水')}>{entries.items.map(entry => <li key={entry.id}>
            <div><b>{t(kinds[entry.kind])}</b><p className="muted small"><time dateTime={new Date(entry.created_at * 1000).toISOString()}>{timeText(entry.created_at, locale, timezone)}</time></p>
              <details className="small"><summary>{t('查看记录编号')}</summary><code>{entry.id}</code></details></div>
            <span className="wallet-delta">{walletAmount(entry.delta, entry.scale, locale, true)} {entry.unit}</span>
          </li>)}</ol>
          <nav className="row wrap gap8" aria-label={t('钱包记录分页')}>
            {cursor !== '0' && <button className="btn" onClick={() => selectPage('0')}>{t('从最早记录开始')}</button>}
            {entries.next_after_seq && <button className="btn" onClick={() => selectPage(entries.next_after_seq!)}>{t('查看下一页记录')}</button>}
          </nav>
        </>}
      </section>
      <p className="notice">{t('充值与退款服务尚未开放。此页不会新增支付订单或消费扣款。')}</p>
      <div><Link className="btn" to="/account">{t('返回账户设置')}</Link></div>
    </main>
  </div></div>;
}
