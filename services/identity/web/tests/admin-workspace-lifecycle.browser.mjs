/** Local-only browser fixtures for the platform workspace list (Admin.tsx WorkspacesTab): where focus
 *  lands after an act whose row leaves the list (A44-WEB-6), that the ownerless-workspace panel
 *  below re-reads after each act (A44-WEB-5), and that a recovery's notice names an organization
 *  that keeps members out (A44-WEB-4). The real React components and auth-client request path run
 *  in a browser; every API response is a fixture answered here, and non-local network is blocked.
 *
 *  Run: node --test tests/admin-workspace-lifecycle.browser.mjs
 *  Requires Playwright and a Chromium: ADMIN_TEST_PLAYWRIGHT_MODULE can name an installed module URL,
 *  ADMIN_TEST_BROWSER_CHANNEL a browser channel (e.g. `chrome`) when Playwright's own build is absent. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = await import(process.env.ADMIN_TEST_PLAYWRIGHT_MODULE || 'playwright');
let server, browser, origin;

before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false, logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'isolated-workspace-lifecycle-fixture',
      // the page's workspace list, reachable for the fixture on its own (as the SSR tests do)
      transform(code, id) { if (id.endsWith('/src/Admin.tsx')) return code + '\nexport { WorkspacesTab };\n'; },
      configureServer(vite) {
        vite.middlewares.use((req, res, next) => {
          if (!req.url?.startsWith('/__workspace_fixture')) return next();
          res.setHeader('Content-Type', 'text/html');
          res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/admin-workspaces.tsx"></script></body></html>');
        });
      } }] });
  await server.listen();
  origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true, ...(process.env.ADMIN_TEST_BROWSER_CHANNEL ? { channel: process.env.ADMIN_TEST_BROWSER_CHANNEL } : {}) });
});
after(async () => { await browser?.close(); await server?.close(); });

// FIXTURE: synthetic workspaces of one synthetic organization, as GET /api/admin/workspaces lists them
const T = 1_790_000_000;
const ORG = { id: 'org_fx_one', name: 'Fixture Org', status: 'active' };
const row = (id, name, over = {}) => ({ id, name, kind: 'org', status: 'active', organization_id: ORG.id, organization: ORG,
  created_at: T, deleted_at: null, recover_until: null, previous_status: null, members: 3, projects: 1, apps: ['docs'], ...over });
const REASON = 'Fixture reason text';
const IMPACT = { members: 3, usable_owners: 1, sessions: 0, tokens: 0, invitations: 0, approvals: 0, api_keys: 0, service_accounts: 0,
  running_jobs: 0, webhooks: 0, form_links: 0, projects: 1, org_templates: 0 };

async function fixture(rows) {
  const context = await browser.newContext({ viewport: { width: 1100, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const state = new Map(rows.map((w) => [w.id, { ...w }]));
  const calls = [], errors = [], external = [];
  page.on('pageerror', (error) => errors.push(error.stack || error.message));
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { external.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const request = route.request();
    const body = request.method() === 'GET' ? null : request.postDataJSON();
    calls.push({ path: url.pathname, method: request.method(), body });
    const json = (out, status = 200) => route.fulfill({ status, contentType: 'application/json',
      body: JSON.stringify(status >= 400 ? { ...out, request_id: 'workspace-ui-fixture-request' } : out) });
    if (url.pathname === '/api/admin/workspaces') {
      // the list re-reads a moment after the act, as over a network: the dialog is gone by then
      await new Promise((r) => setTimeout(r, 150));
      const status = url.searchParams.get('status');
      const items = [...state.values()].filter((w) => status ? w.status === status : w.status !== 'deleted');
      return json({ items, total: items.length, offset: 0, limit: 50, snapshot: 1 });
    }
    if (url.pathname === '/api/admin/ownership/orphans') {
      return json({ as_of: T, limit: 50, workspaces: { total: 0, items: [] }, organizations: { total: 0, items: [] } });
    }
    const act = /^\/api\/admin\/workspaces\/([^/]+)\/(freeze|unfreeze|lifecycle\/preview|lifecycle\/(delete|recover))$/.exec(url.pathname);
    if (act) {
      const w = state.get(decodeURIComponent(act[1]));
      if (act[2] === 'freeze' || act[2] === 'unfreeze') {
        w.status = act[2] === 'freeze' ? 'frozen' : 'active';
        return json({ ok: true, status: w.status, restore_note: null });
      }
      if (act[2] === 'lifecycle/preview') {
        const to = body.action === 'delete' ? 'deleted' : w.previous_status;
        return json({ action: body.action, transition: { from: w.status, to }, target: w, name: null, impact: IMPACT,
          notes: ['data_kept', body.action === 'delete' ? 'recover_within_7_days' : 'recover_not_journalled'],
          risk: body.action === 'delete' ? 'high' : 'medium', expires_at: Date.now() / 1000 + 300, preview_token: 'fixture-signed-preview' });
      }
      if (body.reason !== REASON || body.preview_token !== 'fixture-signed-preview') errors.push(`Unexpected confirmation: ${JSON.stringify(body)}`);
      if (act[3] === 'delete') Object.assign(w, { previous_status: w.status, status: 'deleted', deleted_at: T, recover_until: Date.now() / 1000 + 7 * 86400 });
      else Object.assign(w, { status: w.previous_status, deleted_at: null, recover_until: null, previous_status: null });
      return json({ ok: true, operation_id: 'op_fx_1', state: 'succeeded', action: act[3], workspace: w,
        withdrawn: act[3] === 'delete' ? { sessions: 0, tokens: 0, codes: 0, invitations: 0, approvals: 0 } : null,
        restore_note: act[3] === 'delete' ? null : 'recover_not_journalled' });
    }
    errors.push(`Unexpected API request: ${url.pathname}`);
    return json({ error: 'fixture_unexpected_request', message: 'No such fixture endpoint' }, 500);
  });
  await page.goto(`${origin}/__workspace_fixture?locale=en`);
  await page.getByText('Isolated workspace lifecycle UI fixture', { exact: false }).waitFor();
  const orphanReads = () => calls.filter((c) => c.path === '/api/admin/ownership/orphans').length;
  const focused = () => page.evaluate(() => {
    const a = document.activeElement;
    return a === document.body || !a ? 'body' : `${a.tagName.toLowerCase()}${a.classList.contains('notice') ? '.notice' : ''}: ${(a.textContent || '').trim()}`;
  });
  const listed = async (name) => page.getByRole('table', { name: 'Platform workspaces' }).getByRole('rowheader').filter({ hasText: name }).count();
  const choose = async (name, item) => {
    await page.getByRole('button', { name: `Workspace actions for ${name}` }).click();
    await page.getByRole('menuitem', { name: item }).click();
  };
  const close = async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(external, []); };
  return { page, calls, orphanReads, focused, listed, choose, close };
}

test('a freeze that takes the row out of the filtered list leaves focus on the outcome, and the ownerless panel re-reads', async () => {
  const f = await fixture([row('ws_fx_a', 'Fixture Alpha'), row('ws_fx_b', 'Fixture Beta')]);
  try {
    const { page } = f;
    await page.getByRole('combobox', { name: 'Workspace status' }).selectOption('active');
    await page.getByRole('rowheader').filter({ hasText: 'Fixture Alpha' }).waitFor();
    const before = f.orphanReads();
    await f.choose('Fixture Alpha', 'Freeze workspace');
    const dialog = page.getByRole('alertdialog');
    await dialog.locator('textarea').fill(REASON);
    await dialog.locator('button[type="submit"]').click();
    await page.locator('p.notice').filter({ hasText: 'Workspace frozen.' }).waitFor();
    await page.getByRole('rowheader').filter({ hasText: 'Fixture Beta' }).waitFor();
    await page.waitForFunction(() => document.activeElement?.classList.contains('notice'), null, { timeout: 3000 }).catch(() => {});
    assert.equal(await f.listed('Fixture Alpha'), 0, 'the frozen workspace left the "active" list');
    assert.equal(await f.focused(), 'p.notice: Workspace frozen.');
    assert.equal(f.orphanReads(), before + 1, 'the ownerless-workspace panel re-read after the freeze');
  } finally { await f.close(); }
});

test('a deletion closed from its result leaves focus on the outcome, and the ownerless panel re-reads', async () => {
  const f = await fixture([row('ws_fx_a', 'Fixture Alpha'), row('ws_fx_b', 'Fixture Beta')]);
  try {
    const { page } = f;
    await page.getByRole('rowheader').filter({ hasText: 'Fixture Beta' }).waitFor();
    const before = f.orphanReads();
    await f.choose('Fixture Beta', 'Delete workspace');
    const dialog = page.getByRole('alertdialog');
    await dialog.getByRole('checkbox', { name: 'I have checked the target workspace and these effects.' }).check();
    await dialog.locator('textarea').fill(REASON);
    await dialog.locator('button[type="submit"]').click();
    const result = page.getByRole('dialog', { name: 'Workspace deleted' });
    await result.waitFor();
    // the list re-reads while the result is open; the row (and the menu that opened the dialog) goes
    await page.waitForFunction(() => ![...document.querySelectorAll('th[scope="row"]')].some((th) => th.textContent?.includes('Fixture Beta')));
    await result.locator('button.primary', { hasText: 'Close' }).click();
    await page.waitForFunction(() => document.activeElement?.classList.contains('notice'), null, { timeout: 3000 }).catch(() => {});
    assert.match(await f.focused(), /^p\.notice: “Fixture Beta” was deleted\. It can be recovered until /);
    assert.equal(f.orphanReads(), before + 1, 'the ownerless-workspace panel re-read after the deletion');
  } finally { await f.close(); }
});

test('a recovery into a frozen organization says in the notice that members still cannot get in', async () => {
  const frozenOrg = { ...ORG, status: 'frozen' };
  const f = await fixture([row('ws_fx_a', 'Fixture Alpha'),
    row('ws_fx_d', 'Fixture Deleted', { status: 'deleted', deleted_at: T, recover_until: Date.now() / 1000 + 86400, previous_status: 'active', organization: frozenOrg })]);
  try {
    const { page } = f;
    await page.getByRole('combobox', { name: 'Workspace status' }).selectOption('deleted');
    await page.getByRole('rowheader').filter({ hasText: 'Fixture Deleted' }).waitFor();
    const before = f.orphanReads();
    await f.choose('Fixture Deleted', 'Recover workspace');
    const dialog = page.getByRole('alertdialog');
    await dialog.getByRole('checkbox', { name: 'I have checked the target workspace and these effects.' }).check();
    // the preview's member row says the organization keeps them out, without the server's note
    await dialog.getByText('Its organization is not available: after the recovery members still have no access', { exact: false }).waitFor();
    await dialog.locator('textarea').fill(REASON);
    await dialog.locator('button[type="submit"]').click();
    const result = page.getByRole('dialog', { name: 'Workspace recovered' });
    await result.getByText('Its organization is frozen or deleted: members still cannot access this workspace', { exact: false }).waitFor();
    await result.locator('button.primary', { hasText: 'Close' }).click();
    await page.locator('p.notice').filter({ hasText: 'Its organization is frozen or deleted' }).waitFor();
    await page.waitForFunction(() => document.activeElement?.classList.contains('notice'), null, { timeout: 3000 }).catch(() => {});
    assert.match(await f.focused(), /^p\.notice: “Fixture Deleted” was recovered\. Status now: Active\. Its organization is frozen or deleted/);
    assert.equal(f.orphanReads(), before + 1);
  } finally { await f.close(); }
});
