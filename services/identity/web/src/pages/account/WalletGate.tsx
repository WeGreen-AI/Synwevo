import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useSession } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { Topbar } from '../../components/Topbar';
import { walletOffered } from '../../lib/walletOffered';
import './wallet.messages';
import './account.css';
import './wallet.css';

export { walletOffered };

/** What the wallet pages say on a deployment that does not offer the wallet: plainly that it is
 *  not offered here — not an outage, so no retry and no "try again later". */
export function WalletNotOffered() {
  const { t } = useI18n();
  return <section className="card card-b col gap12" aria-labelledby="wallet-off-title">
    <h1 id="wallet-off-title">{t('钱包')}</h1>
    <p className="notice info" role="status">{t('这个部署没有提供付费 Agent 钱包')}</p>
    <p className="muted">{t('这里没有余额、充值或用量可看，也不会产生任何费用。')}</p>
    <div><Link className="btn" to="/account">{t('返回账户设置')}</Link></div>
  </section>;
}

/** The wallet, usage and receipt pages, only where the deployment offers the wallet. */
export function WalletGate({ children }: { children: ReactNode }) {
  const { state } = useSession();
  const { t } = useI18n();
  if (state && !walletOffered(state)) return <div className="app"><Topbar title={t('钱包')} /><div className="wallet-scroll">
    <main className="account-center col gap24 wallet-page"><WalletNotOffered /></main>
  </div></div>;
  return <>{children}</>;
}
