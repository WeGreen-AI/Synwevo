/** Local browser fixture only: the platform administration's workspace list (Admin.tsx
 *  WorkspacesTab, exported for this fixture by admin-workspace-lifecycle.browser.mjs's test server)
 *  with the ownerless-workspace panel below it. Every API response is intercepted by that test;
 *  no account, no user data. */
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, setLocale, type Locale } from '@synwevo/ui';
import { WorkspacesTab } from '../../src/Admin';
import '@synwevo/ui/styles.css';
import '../../src/account.css';
import '../../src/admin/admin.css';

const query = new URLSearchParams(window.location.search);
setLocale((query.get('locale') || 'en') as Locale);
// FIXTURE: a platform role holding the workspace management permission and both view permissions
const held: ReadonlySet<string> = new Set(['platform.workspaces.view', 'platform.workspaces.manage', 'platform.orgs.view']);
createRoot(document.getElementById('root')!).render(<MemoryRouter initialEntries={['/admin?tab=workspaces']}><I18nProvider>
  <main className="col gap16" style={{ padding: 16, maxWidth: 1100, margin: 'auto' }}>
    <p>Isolated workspace lifecycle UI fixture — no real account or workspace.</p>
    <WorkspacesTab onDenied={() => { throw new Error('Unexpected fixture denial'); }} held={held} />
  </main>
</I18nProvider></MemoryRouter>);
