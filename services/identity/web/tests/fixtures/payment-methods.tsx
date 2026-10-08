/** Local browser fixture only. All API responses are intercepted by payment-methods.browser.mjs. */
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, LanguageMenu, setLocale, type Locale } from '@synwevo/ui';
import { PaymentsTab } from '../../src/governance/PaymentsTab';
import TopupDialog from '../../src/pages/account/TopupDialog';
import { OrganizationTopup } from '../../src/pages/org/OrganizationBilling';
import type { Organization } from '../../src/pages/org/types';
import '@synwevo/ui/styles.css';
import '../../src/account.css';
import '../../src/pages/account/wallet.css';

const query = new URLSearchParams(window.location.search);
setLocale((query.get('locale') || 'en') as Locale);
const surface = query.get('surface');
const organization = { id: 'org_payment_fixture', name: 'Payment fixture', status: 'active' } as Organization;
createRoot(document.getElementById('root')!).render(<MemoryRouter><I18nProvider>
  <main className="account-center col gap16" style={{ padding: 16, maxWidth: 900, margin: 'auto' }}>
    <p>Isolated payment-method UI fixture — no real account or payment.</p>
    <LanguageMenu />
    {surface === 'wallet' ? <TopupDialog payer={{ kind: 'user', id: '' }} payerName="Fixture wallet" onClose={() => {}} />
      : surface === 'organization' ? <OrganizationTopup organization={organization} />
      : <PaymentsTab onDenied={() => { throw new Error('Unexpected fixture denial'); }} canManage={query.get('readonly') !== 'true'} />}
  </main>
</I18nProvider></MemoryRouter>);
