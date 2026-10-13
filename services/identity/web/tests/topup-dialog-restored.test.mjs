/** The real TopupDialog, mounted with a hook runner that runs its effects, opening on a tab's restored
 *  intent: what it says about the earlier order next to the top-up options it reads (UX-3, UX-4, UX-5).
 *  Isolated FIXTURES only: every API answer and the sessionStorage below are test doubles; no browser,
 *  account service, payment provider or network. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { testServer } from './_vite.mjs';

const fx = 'virtual:topup-dialog-restored-fixture';
const runtime = `
import * as React from 'react';
import { ApiError as RealApiError } from '@synwevo/auth-client';
import { t as tt, getLocale } from '@synwevo/ui';
export const useI18n = () => ({ t: tt, locale: getLocale() });
export const ApiError = RealApiError;
let active;
export const H = { answers: {}, posts: [], me: { id: 'u_fixture' } };
export function withHooks(h, render) { const p = active; active = h; h.index = 0; try { return render(); } finally { active = p; } }
export function useState(initial) {
  if (!active) return React.useState(initial);
  const h = active, i = h.index++;
  if (!(i in h.slots)) h.slots[i] = typeof initial === 'function' ? initial() : initial;
  return [h.slots[i], next => { h.slots[i] = typeof next === 'function' ? next(h.slots[i]) : next; }];
}
export function useRef(initial) { if (!active) return React.useRef(initial); return useState(() => ({ current: initial }))[0]; }
export function useId() { if (!active) return React.useId(); return 'fx-' + active.index++; }
export function useEffect(run, deps) {
  if (!active) return React.useEffect(run, deps);
  const h = active, i = h.index++;
  const prev = h.slots[i];
  if (!prev || !deps || deps.some((d, k) => d !== prev.deps[k])) { prev?.cleanup?.(); h.slots[i] = { deps }; h.effects.push(() => { h.slots[i].cleanup = run(); }); }
}
export const useSession = () => ({ me: H.me, state: null });
export async function get(path) {
  const a = H.answers[path.split('?')[0]];
  if (a === undefined) throw new Error('fixture: no answer for ' + path);
  if (a instanceof Error) throw a;
  return JSON.parse(JSON.stringify(a));
}
export async function post(path, body) { H.posts.push({ path, body }); throw new Error('fixture: no write expected'); }
export const isChallengeError = () => false;
export function useChallenge() { return { required: false, token: null, element: null, body: () => ({}), reset() {} }; }
`;
const storage = new Map();
globalThis.sessionStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };

let server, F, D, ui;
before(async () => {
  server = await testServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false, logLevel: 'error',
    appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'topup-dialog-restored-fixture', enforce: 'pre',
      resolveId(id) { if (id === fx) return '\0' + fx; },
      load(id) { if (id === '\0' + fx) return runtime; },
      transform(code, id) {
        if (!/\/pages\/account\/TopupDialog\.tsx$/.test(id)) return;
        return code.replace(/import \{ useEffect, useId, useRef, useState \} from 'react';/, `import { useEffect, useId, useRef, useState } from '${fx}';`)
          .replace(/import \{ ApiError, get, post, useSession \} from '@synwevo\/auth-client';/, `import { ApiError, get, post, useSession } from '${fx}';`)
          .replace(/import \{ Modal, useI18n, type Translator \} from '@synwevo\/ui';/, `import { Modal, type Translator } from '@synwevo/ui'; import { useI18n } from '${fx}';`)
          .replace(/import \{ isChallengeError, useChallenge \} from '..\/org\/Challenge';/, `import { isChallengeError, useChallenge } from '${fx}';`);
      } }] });
  F = await server.ssrLoadModule(fx);
  D = await server.ssrLoadModule('/src/pages/account/TopupDialog.tsx');
  ui = await server.ssrLoadModule('@synwevo/ui');
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });

const ctx = { environment: 'live', initiator_id: 'u_fixture', payer_kind: 'user', payer_id: 'u_fixture' };
const optionsOpen = { ...ctx, available: true, currency: 'CNY', payment_methods: ['card'],
  packages: [{ id: 'cny-10', amount_minor: 1000, label: 'Fixture 10' }], custom_amount: false };
const closed = reason => ({ ...ctx, available: false, reason, currency: 'CNY', payment_methods: [], packages: [] });
const order = (overrides = {}) => ({ order_id: 'top_fixture', status: 'pending', creation_status: 'resolved', amount_minor: 1000,
  currency: 'CNY', payer_kind: 'user', payer_id: 'u_fixture', created_at: 1_790_000_000, expires_at: 1_790_003_600, ...overrides });
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const text = html => html.replace(/<[^>]+>/g, ' | ').replace(/(\s*\|\s*)+/g, ' | ').replace(/&#x27;/g, "'").trim();

/** Open the dialog in a tab whose sessionStorage remembers order top_fixture. */
async function open(locale, options, status) {
  ui.setLocale(locale);
  storage.clear();
  storage.set(D.topupStorageKey(ctx), JSON.stringify({ ...ctx, version: 1, idempotency_key: 'fixture-key', package_id: 'cny-10',
    quoted_amount_minor: 1000, order_id: 'top_fixture' }));
  F.H.answers = { '/api/auth/wallet/topup-options': options, '/api/auth/wallet/topups/top_fixture': status };
  F.H.posts = [];
  const h = { slots: [], index: 0, effects: [] };
  const draw = () => {
    const tree = F.withHooks(h, () => D.default({ payer: { kind: 'user' }, payerName: 'Fixture', onClose() {} }));
    h.effects.splice(0).forEach(run => run());
    return tree;
  };
  let tree = draw();
  for (let i = 0; i < 6; i++) { await settle(); tree = draw(); }
  assert.deepEqual(F.H.posts, [], 'opening only reads');
  return { body: text(renderToStaticMarkup(React.createElement(React.Fragment, null, tree.props.children))),
    footer: text(renderToStaticMarkup(React.createElement(React.Fragment, null, tree.props.footer))),
    kept: storage.has(D.topupStorageKey(ctx)) };
}

test('top-ups closed, restored order still pending: no promise of a usable payment page, no contradiction (UX-3)', async () => {
  for (const [locale, closedLine, neutral, promise] of [
    ['zh-Hans', '充值暂未开放', '原充值订单仍在等待付款；继续时会核实付款页面是否仍可使用', /付款页面仍可使用。继续会回到/],
    ['zh-Hant', '充值暫未開放', '原儲值訂單仍在等待付款；繼續時會核實付款頁面是否仍可使用', /付款頁面仍可使用。繼續會回到/],
    ['en', 'Top-ups are not available yet', 'The original order is still awaiting payment. Continuing first checks', /still open|takes you back to it/],
  ]) {
    const shown = await open(locale, closed('not_configured'), order());
    assert.ok(shown.body.includes(closedLine), shown.body);
    assert.ok(shown.body.includes(neutral), `${locale}: ${shown.body}`);
    assert.doesNotMatch(shown.body, promise);
    assert.equal(shown.kept, true, 'a pending order keeps the original request');
  }
});

test('a restored order whose outcome is unknown makes no claim about money (UX-5)', async () => {
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    const shown = await open(locale, optionsOpen, order({ creation_status: 'verifying' }));
    assert.match(shown.body, /暂时无法确认付款页面是否已创建|暫時無法確認付款頁面是否已建立|cannot confirm yet whether the payment page was created/);
    assert.doesNotMatch(shown.body, /扣款|charged/, `${locale}: ${shown.body}`);
  }
});

test('a final restored order offers a new amount only when top-ups are open here (UX-4)', async () => {
  const opened = await open('en', optionsOpen, order({ status: 'expired' }));
  assert.equal(opened.kept, false);
  assert.match(opened.body, /Your previous top-up expired\. \| You can choose a new amount\./);
  assert.match(opened.footer, /Continue to payment/);
  const cases = [
    ['en', closed('wallet_frozen'), order({ status: 'failed', failure_reason: 'payment_method_not_enabled' }),
      /This wallet is frozen/, /Nothing was charged/, /choose a new amount/i],
    ['zh-Hans', closed('not_configured'), order({ status: 'paid', credited_at: 1_790_000_100 }), /充值暂未开放/, /上一笔充值已到账。/, /选择新的金额/],
    ['zh-Hant', closed('not_permitted'), order({ status: 'expired' }), /只有組織的付費管理者/, /上一筆儲值訂單已過期。/, /選擇新的金額/],
  ];
  for (const [locale, options, status, closedLine, outcome, invitation] of cases) {
    const shown = await open(locale, options, status);
    assert.equal(shown.kept, false, 'a final order releases the intent');
    assert.match(shown.body, closedLine, shown.body);
    assert.match(shown.body, outcome, shown.body);
    assert.doesNotMatch(shown.body, invitation, `${locale}: ${shown.body}`);
    assert.doesNotMatch(shown.footer, /前往付款|Continue to payment|重新选择金额|Choose an amount again/);
  }
});
