/** The organization page's top-up is the wallet's own dialog with the organization as payer.
 *  Isolated component fixtures: no browser, account, API or payment. A small hook runner supplies
 *  React state to OrganizationBilling's own components; everything else is the real module. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { testServer } from './_vite.mjs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const fixtureModule = 'virtual:org-topup-fixture';
const fixtureRuntime = `
import * as React from 'react';
import { t, getLocale } from '@synwevo/ui';
export const useI18n = () => ({ t, locale: getLocale() });
let active;
export function withHooks(h, render) {
  const previous = active; active = h; h.index = 0;
  try { return render(); } finally { active = previous; }
}
export function useState(initial) {
  if (!active) return React.useState(initial);
  const h = active, i = h.index++;
  if (!(i in h.slots)) h.slots[i] = typeof initial === 'function' ? initial() : initial;
  return [h.slots[i], next => { h.slots[i] = typeof next === 'function' ? next(h.slots[i]) : next; }];
}
export function useRef(initial) { return active ? useState(() => ({ current: initial }))[0] : React.useRef(initial); }
export function useId() { return active ? useRef('fixture-' + active.index).current : React.useId(); }
export function useEffect(run, deps) { if (!active) return React.useEffect(run, deps); active.index++; }
`;

const WEB = fileURLToPath(new URL('../', import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL('http://127.0.0.1/'), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };
let server, support, billing, dialog, ui;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: 'error', appType: 'custom',
    server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'org-topup-fixtures', enforce: 'pre',
      resolveId(id) { if (id === fixtureModule) return '\0' + fixtureModule; },
      load(id) { if (id === '\0' + fixtureModule) return fixtureRuntime; },
      transform(code, id) {
        if (!/\/pages\/org\/OrganizationBilling\.tsx$/.test(id)) return;
        return code.replace(/from "(react|@synwevo\/ui)"/g, `from "${fixtureModule}"`);
      },
    }],
  });
  support = await server.ssrLoadModule(fixtureModule);
  billing = await server.ssrLoadModule('/src/pages/org/OrganizationBilling.tsx');
  dialog = await server.ssrLoadModule('/src/pages/account/TopupDialog.tsx');
  ui = await server.ssrLoadModule('@synwevo/ui');
});
after(async () => { await server?.close(); });

function nodes(tree) {
  return React.Children.toArray(tree).flatMap(node => React.isValidElement(node) ? [node, ...nodes(node.props.children)] : []);
}
const organization = { id: 'org_fixture', name: 'Fixture organization', role: 'owner', status: 'active' };

test('the organization top-up opens the wallet dialog with the organization as payer; no second form exists', () => {
  ui.setLocale('zh-Hans');
  const h = { slots: [], index: 0 };
  const draw = () => support.withHooks(h, () => billing.OrganizationTopup({ organization }));
  let tree = draw();
  const html = renderToStaticMarkup(tree);
  assert.doesNotMatch(html, /<form|<input|type="radio"|前往支付|checkout/i, 'nothing is priced or posted on the page itself');
  assert.equal(nodes(tree).filter(node => node.type === dialog.default).length, 0);
  const open = nodes(tree).find(node => node.type === 'button' && node.props.children === '为组织钱包充值');
  assert.ok(open, html);
  open.props.onClick(); tree = draw();
  const opened = nodes(tree).filter(node => node.type === dialog.default);
  assert.equal(opened.length, 1);
  assert.deepEqual(opened[0].props.payer, { kind: 'organization', id: 'org_fixture' });
  assert.equal(opened[0].props.payerName, 'Fixture organization');
  opened[0].props.onClose(); tree = draw();
  assert.equal(nodes(tree).filter(node => node.type === dialog.default).length, 0);
});

test('the duplicate form and its unkeyed request are gone from the organization page', () => {
  const source = readFileSync(new URL('../src/pages/org/OrganizationBilling.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\bpost\b|\/api\/auth\/wallet\/topups"|checkout_url|checkout\.stripe|useChallenge|window\.location\.assign/);
  assert.match(source, /from "\.\.\/account\/TopupDialog"/);
});

test('the organization return words a refused or unknown creation like the wallet does, in all three languages', () => {
  const order = { order_id: 'top_fixture', status: 'failed', creation_status: 'resolved', amount_minor: 1000, currency: 'CNY' };
  const refused = billing.topupReturnMessage(false, { ...order, failure_reason: 'payment_method_not_enabled' }, false);
  assert.match(refused, /所选支付方式尚未在支付服务商处开通。没有扣款。/);
  assert.equal(billing.topupReturnMessage(false, order, false), '支付没有完成，没有扣款。可以重新发起充值。');
  const unknown = billing.topupReturnMessage(false, { ...order, status: 'pending', creation_status: 'verifying' }, true);
  assert.doesNotMatch(unknown, /正在等待支付服务确认到账/, 'no payment to wait for when no checkout address was ever returned');
  assert.match(unknown, /尚未确认支付服务商是否已创建付款页面/);
  assert.equal(billing.topupReturnMessage(false, { ...order, status: 'pending', creation_status: 'resolved' }, true), '正在等待支付服务确认到账…');
  for (const message of [refused, unknown]) {
    assert.ok(ui.hasMessage(message));
    assert.doesNotMatch(ui.translate('en', message), /[㐀-鿿]/);
    assert.notEqual(ui.translate('zh-Hant', message), message);
  }
});
