/** Isolated UI fixtures: no HTTP server, no accounts or user data. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

const WEB = fileURLToPath(new URL('../', import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL('http://127.0.0.1/'), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };
let server, ui, nav, layout;
before(async () => {
  server = await createServer({ root: WEB, configFile: false, logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule('@synwevo/ui');
  nav = await server.ssrLoadModule('/src/admin/navigation.ts');
  layout = await server.ssrLoadModule('/src/admin/AdminLayout.tsx');
});
after(async () => { await server?.close(); });

test('navigation uses effective permissions, keeps API administration in the shell, and omits empty groups', () => {
  const held = new Set(['platform.overview.view', 'api_platform.manage']);
  assert.deepEqual(nav.adminGroups(held).flatMap(group => group.items.map(item => item.id)), ['overview', 'api-platform']);
  assert.deepEqual(nav.adminGroups(new Set()), []);
  const support = new Set(['platform.users.view', 'platform.workspaces.view']);
  assert.deepEqual(nav.adminGroups(support).flatMap(group => group.items.map(item => item.id)), ['users', 'workspaces']);
});

test('legacy links resolve only to a permitted view; API direct links cannot bypass the required permission', () => {
  const held = new Set(['platform.users.view', 'platform.workspaces.view']);
  assert.equal(nav.resolveAdminPage('/admin', 'workspaces', held), 'workspaces');
  assert.equal(nav.resolveAdminPage('/admin', 'admins', held), 'users');
  assert.equal(nav.resolveAdminPage('/admin', 'unknown', held), 'users');
  for (const path of ['/admin/api-platform', '/admin/api-platform/']) assert.equal(nav.resolveAdminPage(path, 'calls', held), null);
  assert.equal(nav.resolveAdminPage('/admin/api-platform/', 'calls', new Set(['api_platform.manage'])), 'api-platform');
});

test('navigation links preserve language and origin context without carrying a previous view filter', () => {
  assert.equal(nav.adminHref('users', '?lang=zh-Hant&from=platform&tab=audit&q=example'), '/admin?lang=zh-Hant&from=platform&tab=users');
  assert.equal(nav.adminHref('api-platform', '?lang=en&tab=audit'), '/admin/api-platform?lang=en');
});

test('navigation has one current link and every label has both translations', () => {
  const held = new Set(['platform.overview.view', 'platform.users.view', 'platform.roles.view', 'platform.workspaces.view', 'platform.orgs.view', 'platform.config.view', 'platform.mail.view', 'platform.audit.view', 'api_platform.manage']);
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
