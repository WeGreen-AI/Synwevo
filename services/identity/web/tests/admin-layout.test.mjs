/** Isolated UI fixtures: no HTTP server, no accounts or user data. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.mjs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

const WEB = fileURLToPath(new URL('../', import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL('http://127.0.0.1/'), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };
let server, ui, nav, layout, admin;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule('@synwevo/ui');
  nav = await server.ssrLoadModule('/src/admin/navigation.ts');
  layout = await server.ssrLoadModule('/src/admin/AdminLayout.tsx');
  admin = await server.ssrLoadModule('/src/Admin.tsx');
});
after(async () => { await server?.close(); });

test('navigation uses effective permissions, keeps API administration in the shell, and omits empty groups', () => {
  const held = new Set(['platform.overview.view', 'api_platform.manage']);
  assert.deepEqual(nav.adminGroups(held).flatMap(group => group.items.map(item => item.id)), ['overview', 'api-platform']);
  assert.deepEqual(nav.adminGroups(new Set()), []);
  const support = new Set(['platform.users.view', 'platform.workspaces.view']);
  // Agent reports and suspensions are account governance (platform.users.view); its rule lists need platform.config.*.
  // A47: every workspace's invitations read with the workspace view.
  assert.deepEqual(nav.adminGroups(support).flatMap(group => group.items.map(item => item.id)), ['users', 'workspaces', 'invitations', 'agent-safety']);
  // B1/B2: the application catalogue reads with platform.apps.view, first in the applications group;
  // the run records reading with the same permission stay their own pages in their own group.
  const apps = nav.adminGroups(new Set(['platform.apps.view', 'platform.mail.view', 'api_platform.manage']));
  assert.deepEqual(apps.map(group => [group.label, group.items.map(item => item.id)]),
    [['应用与业务', ['apps', 'mail', 'api-platform']], ['运行与数据', ['app-runs', 'flow-runs']]]);
  assert.equal(nav.resolveAdminPage('/admin', 'apps', new Set(['platform.apps.view'])), 'apps');
  assert.equal(nav.resolveAdminPage('/admin', 'apps', new Set(['platform.apps.manage'])), null, 'managing is not reading');
  assert.equal(nav.resolveAdminPage('/admin', 'apps', new Set(['platform.billing.view'])), 'finance');
});

test('legacy links resolve only to a permitted view; API direct links cannot bypass the required permission', () => {
  const held = new Set(['platform.users.view', 'platform.workspaces.view']);
  assert.equal(nav.resolveAdminPage('/admin', 'workspaces', held), 'workspaces');
  assert.equal(nav.resolveAdminPage('/admin', 'admins', held), 'users');
  assert.equal(nav.resolveAdminPage('/admin', 'unknown', held), 'users');
  for (const path of ['/admin/api-platform', '/admin/api-platform/']) assert.equal(nav.resolveAdminPage(path, 'calls', held), null);
  assert.equal(nav.resolveAdminPage('/admin/api-platform/', 'calls', new Set(['api_platform.manage'])), 'api-platform');
});

test('model provider is a real config-governance page and preserves permission-filtered navigation defaults', () => {
  const read = new Set(['platform.config.view']);
  const groups = nav.adminGroups(read);
  assert.deepEqual(groups.find(group => group.label === '配置与治理').items.map(item => item.id),
    ['registration', 'model-provider', 'model-prices', 'model-costs', 'model-risk', 'mcp']);
  assert.equal(groups.flatMap(group => group.items).filter(item => item.id === 'model-provider').length, 1);
  // The paid-call risk page reads with the same config grant; without it the page is neither listed nor reachable.
  assert.equal(nav.resolveAdminPage('/admin', 'model-risk', read), 'model-risk');
  assert.equal(nav.resolveAdminPage('/admin', 'model-risk', new Set(['platform.users.view'])), 'users');
  assert.equal(nav.resolveAdminPage('/admin', 'model-provider', read), 'model-provider');
  // Adding a page must not change which existing page an unqualified /admin opens.
  for (const asked of [null, '', 'unknown']) assert.equal(nav.resolveAdminPage('/admin', asked, read), 'identity');
  assert.equal(nav.resolveAdminPage('/admin', null, new Set(['platform.overview.view', ...read])), 'overview');
  assert.equal(nav.resolveAdminPage('/admin', null, new Set(['api_platform.manage'])), 'api-platform');
  for (const permissions of [[], ['platform.config.manage'], ['owner'], ['admin']]) {
    const held = new Set(permissions);
    assert.equal(nav.resolveAdminPage('/admin', 'model-provider', held), null);
    assert.equal(nav.adminGroups(held).flatMap(group => group.items).some(item => item.id === 'model-provider'), false);
  }
  assert.equal(nav.resolveAdminPage('/admin', 'model-provider', new Set(['platform.users.view'])), 'users');
});

test('the actual Admin entry mounts ModelProviderTab and adapts view/manage grants independently', () => {
  // Render the real AdminLayout child factory with isolated effective-grant fixtures.
  // The layout's navigation decisions are exercised above; SSR runs no effects or HTTP.
  const shell = admin.default();
  assert.equal(shell.type, layout.AdminLayout);
  const render = permissions => renderToStaticMarkup(shell.props.children({ held: new Set(permissions), active: 'model-provider' }));
  const readonly = render(['platform.config.view']);
  assert.match(readonly, /模型提供方/);
  assert.match(readonly, /正在读取提供方配置/);
  assert.match(readonly, /你可以查看提供方配置和历史，不能修改或发布/);
  assert.doesNotMatch(readonly, /正在读取成本策略|<form|type="password"/);
  const manage = render(['platform.config.view', 'platform.models.credential']);
  assert.match(manage, /正在读取提供方配置/);
  assert.doesNotMatch(manage, /你可以查看提供方配置和历史，不能修改或发布/);
  assert.match(manage, /你保管密钥与路线/);
  // authz-6: the pricing permission decides the prices a route uses here, and nothing else on this page
  const pricing = render(['platform.config.view', 'platform.models.pricing']);
  assert.doesNotMatch(pricing, /你可以查看提供方配置和历史，不能修改或发布/);
  assert.match(pricing, /你负责定价/);
  const both = render(['platform.config.view', 'platform.models.credential', 'platform.models.pricing']);
  assert.doesNotMatch(both, /你可以查看提供方配置和历史，不能修改或发布|你保管密钥与路线|你负责定价/);
  // platform configuration's manage no longer changes anything here
  assert.match(render(['platform.config.view', 'platform.config.manage']), /你可以查看提供方配置和历史，不能修改或发布/);
  for (const permissions of [[], ['platform.config.manage'], ['platform.models.credential'], ['api_platform.manage']]) {
    const denied = render(permissions);
    assert.match(denied, /你没有查看模型提供方配置的权限/);
    assert.doesNotMatch(denied, /正在读取提供方配置|提供方发布历史/);
  }
});

test('authz-6: the price, cost and risk pages are changed with the pricing permission; the config view, config manage and the credential permission only read them', () => {
  const shell = admin.default();
  const render = (active, permissions) => renderToStaticMarkup(shell.props.children({ held: new Set(permissions), active }));
  const pages = [['model-prices', /你可以查看价格和试算，不能创建、修改或发布价格。/],
                 ['model-costs', /你可以查看成本策略和历史，不能修改或发布。/],
                 ['model-risk', /你可以查看风险控制状态，不能修改。/]];
  for (const [active, readOnly] of pages) {
    for (const permissions of [['platform.config.view'], ['platform.config.view', 'platform.config.manage'],
                               ['platform.config.view', 'platform.models.credential']]) {
      assert.match(render(active, permissions), readOnly, `${active}: ${permissions.join()}`);
    }
    assert.doesNotMatch(render(active, ['platform.config.view', 'platform.models.pricing']), readOnly, `${active}: pricing`);
    // without the view nothing of the page is mounted, whatever else is held
    for (const permissions of [[], ['platform.models.pricing'], ['platform.config.manage']]) {
      assert.doesNotMatch(render(active, permissions), readOnly, `${active}: ${permissions.join()} without the view`);
    }
  }
});

test('authz-6: each model surface tells a holder of the other permission it is read-only, and that sentence exists in the three languages', () => {
  const shell = admin.default();
  // These pages word themselves with `useI18n`, whose server snapshot is the Simplified source:
  // the markup proves who is told, the catalogue proves the other two languages have it.
  const render = (active, permissions) => renderToStaticMarkup(shell.props.children({ held: new Set(permissions), active }));
  const CREDENTIAL = ['platform.config.view', 'platform.models.credential'];
  const PRICING = ['platform.config.view', 'platform.models.pricing'];
  for (const [active, reader, holder, sentence] of [
    ['model-provider', ['platform.config.view', 'platform.config.manage'], CREDENTIAL, '你可以查看提供方配置和历史，不能修改或发布。'],
    ['model-provider', ['platform.config.view'], PRICING, '你可以查看提供方配置和历史，不能修改或发布。'],
    ['model-provider', CREDENTIAL, PRICING, '你保管密钥与路线：可以更换或撤销凭据、调整路线及其证据。路线采用的销售价、成本价及其成本依据和未完成调用的计费属于定价权限，这里只读。'],
    ['model-provider', PRICING, CREDENTIAL, '你负责定价：可以让路线采用已发布的销售价与成本价、更新成本依据，并决定未完成调用是否收费。凭据与其他路线字段属于密钥保管权限，这里只读。'],
    ['model-prices', CREDENTIAL, PRICING, '你可以查看价格和试算，不能创建、修改或发布价格。'],
    ['model-costs', CREDENTIAL, PRICING, '你可以查看成本策略和历史，不能修改或发布。'],
    ['model-risk', CREDENTIAL, PRICING, '你可以查看风险控制状态，不能修改。'],
  ]) {
    assert.ok(render(active, reader).includes(sentence), `${active}: the other permission reads only`);
    assert.ok(!render(active, holder).includes(sentence), `${active}: its own permission changes it`);
    assert.ok(ui.hasMessage(sentence), sentence);
    assert.doesNotMatch(ui.translate('en', sentence), /[㐀-鿿]/, `${active}: English`);
    assert.notEqual(ui.translate('zh-Hant', sentence), sentence, `${active}: Traditional Chinese`);
  }
});

test('the actual Admin entry mounts the application catalogue and offers its changes only with platform.apps.manage', () => {
  const shell = admin.default();
  // The catalogue words itself with the active locale (`t` outside the hook); pin it for the render.
  const render = permissions => { ui.setLocale('zh-Hans'); return renderToStaticMarkup(shell.props.children({ held: new Set(permissions), active: 'apps' })); };
  const readonly = render(['platform.apps.view']);
  assert.match(readonly, /应用目录/);
  assert.match(readonly, /正在加载应用目录…/);
  assert.match(readonly, /你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。/);
  assert.doesNotMatch(render(['platform.apps.view', 'platform.apps.manage']), /你的平台角色可以查看应用目录/);
  // the run pages are not the catalogue
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(MemoryRouter, null, shell.props.children({ held: new Set(['platform.apps.view']), active: 'app-runs' }))), /应用目录/);
});

test('navigation links preserve language and origin context without carrying a previous view filter', () => {
  assert.equal(nav.adminHref('users', '?lang=zh-Hant&from=platform&tab=audit&q=example'), '/admin?lang=zh-Hant&from=platform&tab=users');
  assert.equal(nav.adminHref('api-platform', '?lang=en&tab=audit'), '/admin/api-platform?lang=en');
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    const href = nav.adminHref('model-provider', `?lang=${locale}&from=platform&tab=model-costs&q=fixture-query&api_key=fixture-never-forward`);
    assert.equal(href, `/admin?lang=${locale}&from=platform&tab=model-provider`);
    assert.equal(nav.resolveAdminPage('/admin', new URLSearchParams(href.split('?')[1]).get('tab'), new Set(['platform.config.view'])), 'model-provider');
  }
});

test('navigation has one current link and every label has both translations', () => {
  const held = new Set(['platform.overview.view', 'platform.users.view', 'platform.roles.view', 'platform.workspaces.view', 'platform.orgs.view', 'platform.config.view', 'platform.mail.view', 'platform.apps.view', 'platform.audit.view', 'api_platform.manage']);
  const groups = nav.adminGroups(held);
  for (const group of groups) for (const label of [group.label, ...group.items.map(item => item.label)]) {
    assert.ok(ui.hasMessage(label), label);
    for (const locale of ['en', 'zh-Hant']) assert.ok(ui.translate(locale, label), `${locale}: ${label}`);
    assert.ok(!/[㐀-鿿]/.test(ui.translate('en', label)), label);
  }
  // The i18n hook intentionally uses Simplified Chinese during server rendering. Actual
  // locale switching is exercised by browser_admin_review.py against the built application.
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null,
    React.createElement(layout.AdminNavigation, { groups, active: 'mail', search: '?lang=zh-Hant' })));
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1);
  assert.ok(html.includes('/admin/api-platform?lang=zh-Hant'));
});

test('model provider has one active navigation link and its real catalogue title in all three languages', () => {
  const groups = nav.adminGroups(new Set(['platform.config.view']));
  const item = groups.flatMap(group => group.items).find(item => item.id === 'model-provider');
  for (const [locale, title] of [['zh-Hans', '模型提供方'], ['zh-Hant', '模型提供方'], ['en', 'Model provider']]) {
    assert.equal(ui.translate(locale, item.label), title);
  }
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null,
    React.createElement(layout.AdminNavigation, { groups, active: 'model-provider', search: '?lang=en' })));
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1);
  const activeLink = html.match(/<a\b[^>]*aria-current="page"[^>]*>[^<]+<\/a>/)?.[0];
  assert.ok(activeLink?.includes('/admin?lang=en&amp;tab=model-provider'), activeLink);
});
