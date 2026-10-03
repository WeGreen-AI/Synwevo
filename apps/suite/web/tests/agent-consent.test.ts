/** Explicit consent/accounting fixtures. No live model, billing service, funds or browser claims. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Consent, ConsentInput } from '../src/apps/agent/consent.ts';
import type { Quote, QuoteParams } from '../src/apps/agent/budget.ts';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, model: any, controls: any, copy: any, ui: any, client: any, core: any;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'consent-test-locale', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  [model, controls, copy, ui, client, core] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/consent.ts'), server.ssrLoadModule('/src/apps/agent/ConsentControls.tsx'),
    server.ssrLoadModule('/src/apps/agent/consent.messages.ts'), server.ssrLoadModule('@synwevo/ui'),
    server.ssrLoadModule('@synwevo/auth-client'), server.ssrLoadModule('@synwevo/ui/i18n-core'),
  ]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });

const unit = { code: 'FIXTURE', scale: 2 };
const rate = { numerator: '1', denominator: '1' };
const now = 1_790_000_000_000;
const pricing = { environment: 'test' as const, model: 'fixture-model', confirmed_route: 'fixture-route', route_version: 'fixture-v1',
  price_id: 'price_fixture', unit, rates: { input_uncached: rate, input_cached: rate, output: rate }, per_tokens: 1000,
  terms_ref: 'fixture:terms', per_call_hold: '100' };
function quote(overrides: Partial<Quote> = {}): Quote {
  return { ...pricing, id: 'quote_aaaaaaaaaaaa', payer_user_id: 'usr_fixture', conversation_id: 'conv_fixture', budget: '5000',
    max_calls: 3, deadline: now / 1000 + 300, expires_at: now / 1000 + 300, turn_id: null, status: 'quoted', ...overrides };
}
const params: QuoteParams = { budget: '5000', max_calls: 3, run_seconds: 300 };
function consent(overrides: Partial<Consent> = {}): Consent {
  return { ...pricing, id: 'consent_aaaaaaaaaaaa', conversation_id: 'conv_fixture', scope: ['ws_a'], max_runs: 2, used_runs: 0,
    remaining_runs: 2, status: 'active', created_at: now / 1000, expires_at: null, until_revoked: true, budget: '5000',
    total_maximum: '10000', max_calls: 3, run_seconds: 300, ...overrides };
}
function local(offsetMs: number) {
  const d = new Date(now + offsetMs);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}T${two(d.getHours())}:${two(d.getMinutes())}`;
}

test('maximum potential total is exact and refuses int64 overflow', () => {
  assert.equal(model.consentTotal('5000', 1000), '5000000');
  assert.equal(model.consentTotal('9223372036854775807', 1), '9223372036854775807');
  assert.equal(model.consentTotal('4611686018427387903', 2), '9223372036854775806');
  assert.throws(() => model.consentTotal('4611686018427387904', 2), /consent_total_overflow/);
  for (const runs of [0, 1001, 1.5, NaN]) assert.throws(() => model.consentTotal('1', runs), /consent_invalid_limits/);
  for (const budget of ['0', '-1', '1.0', '01', '']) assert.throws(() => model.consentTotal(budget, 1), /consent_invalid_limits/);
});

test('the lifetime is an explicit future time within 366 days or explicitly until revoked', () => {
  assert.equal(model.consentExpiry('', true, now), null);
  assert.throws(() => model.consentExpiry(local(3600_000), true, now), /consent_expiry_required/);
  assert.equal(model.consentExpiry(local(3600_000), false, now), Math.floor(new Date(local(3600_000)).getTime() / 1000));
  for (const value of ['', local(-60_000), local(367 * 86400_000), '2026-02-30T10:00', '2026-10-03 10:00', 'tomorrow']) {
    assert.throws(() => model.consentExpiry(value, false, now), /consent_invalid_expiry/, value);
  }
});

test('a consent needs an explicit scope, a fresh unused quote and bounded runs', () => {
  const input: ConsentInput = model.consentInput(quote(), ['ws_a'], '2', '', true, now);
  assert.deepEqual(input, { conversation_id: 'conv_fixture', quote_id: 'quote_aaaaaaaaaaaa', max_runs: 2, expires_at: null, until_revoked: true });
  assert.equal(model.consentInput(quote(), [], '1', '', true, now).max_runs, 1);  // Personal-only scope is explicit.
  assert.throws(() => model.consentInput(quote(), null, '2', '', true, now), /consent_explicit_scope_required/);
  for (const bad of [quote({ status: 'revoked' }), quote({ turn_id: 'turn_fixture' }), quote({ expires_at: now / 1000 })]) {
    assert.throws(() => model.consentInput(bad, ['ws_a'], '2', '', true, now), /quote_expired_or_revoked/);
  }
  for (const runs of ['', '0', '1001', '1.5', '-1', ' 2']) assert.throws(() => model.consentInput(quote(), ['ws_a'], runs, '', true, now), undefined, runs);
});

test('responses must match the reviewed quote, scope and limits exactly', () => {
  const input: ConsentInput = model.consentInput(quote(), ['ws_a'], '2', '', true, now);
  model.validateCreatedConsent(consent(), input, quote(), ['ws_a'], params);
  for (const changed of [{ total_maximum: '10001' }, { max_runs: 3, remaining_runs: 3, total_maximum: '15000' }, { scope: ['ws_b'] },
    { price_id: 'price_other' }, { budget: '6000', total_maximum: '12000' }, { run_seconds: 299 }, { until_revoked: false, expires_at: now / 1000 + 60 },
    { conversation_id: 'conv_other' }, { used_runs: 1, remaining_runs: 2 }, { status: 'unknown' as any }]) {
    assert.throws(() => model.validateCreatedConsent(consent(changed), input, quote(), ['ws_a'], params), /invalid_consent_response/, JSON.stringify(changed));
  }
  model.validateConsentList({ consents: [consent(), consent({ id: 'consent_bbbbbbbbbbbb', status: 'revoked' })] }, 'conv_fixture');
  assert.throws(() => model.validateConsentList({ consents: [consent(), consent()] }, 'conv_fixture'), /invalid_consent_response/);
});

test('local status never treats an expired, rescoped or used-up consent as active', () => {
  assert.equal(model.effectiveConsentStatus(consent(), ['ws_a'], now), 'active');
  assert.equal(model.effectiveConsentStatus(consent(), ['ws_a', 'ws_b'], now), 'scope_changed');
  assert.equal(model.effectiveConsentStatus(consent(), null, now), 'scope_changed');
  assert.equal(model.effectiveConsentStatus(consent({ used_runs: 2, remaining_runs: 0 }), ['ws_a'], now), 'exhausted');
  assert.equal(model.effectiveConsentStatus(consent({ until_revoked: false, expires_at: now / 1000 }), ['ws_a'], now), 'expired');
  assert.equal(model.effectiveConsentStatus(consent({ status: 'model_changed' }), ['ws_a'], now), 'model_changed');
  assert.ok(model.sameScope(['b', 'a'], ['a', 'b']) && !model.sameScope(['a'], ['a', 'b']) && !model.sameScope(null, []));
});

test('an unresolved outcome locks the conversation and is checked only with its original key', async () => {
  const calls: [string, string][] = [];
  let reply: () => Promise<any> = async () => { throw new client.ApiError(504, 'gateway_timeout', '', undefined, true); };
  let keys = 0;
  const writer = model.createConsentWriter(async (operation: string, _input: unknown, key: string) => {
    calls.push([operation, key]); return reply();
  }, () => `fixture-key-${++keys}`);
  await assert.rejects(writer.start('agent.turn.start', { conversation_id: 'conv_fixture', consent_id: 'consent_aaaaaaaaaaaa' }, () => {}));
  assert.equal(writer.getSnapshot().phase, 'uncertain');
  assert.ok(writer.pending());
  // No other payment action or message while unresolved, and nothing retried automatically.
  await assert.rejects(writer.start('agent.consent.revoke', { consent_id: 'consent_aaaaaaaaaaaa' }, () => {}), (e: any) => e.code === 'consent_pending_operation');
  assert.equal(calls.length, 1);
  reply = async () => ({ status: 'succeeded', output: { turn_id: 'turn_fixture' } });
  assert.deepEqual(await writer.retry(), { turn_id: 'turn_fixture' });
  assert.deepEqual(calls, [['agent.turn.start', 'fixture-key-1'], ['agent.turn.start', 'fixture-key-1']]);
  assert.equal(writer.getSnapshot().phase, 'succeeded');
  await assert.rejects(writer.retry(), (e: any) => e.code === 'consent_pending_operation');
});

test('an explicit refusal unlocks; an unverifiable success stays unresolved', async () => {
  let reply: () => Promise<any> = async () => { throw new client.ApiError(409, 'billing_consent_exhausted', ''); };
  let keys = 0;
  const writer = model.createConsentWriter(async () => reply(), () => `fixture-key-${++keys}`);
  await assert.rejects(writer.start('agent.turn.start', { conversation_id: 'conv_fixture' }, () => {}));
  assert.equal(writer.getSnapshot().phase, 'refused');
  assert.equal(model.consentCode(writer.getSnapshot().error), 'consent_exhausted');
  assert.equal(controls.consentErrorKey(writer.getSnapshot().error), 'exhausted');
  assert.ok(!writer.pending());
  reply = async () => ({ status: 'succeeded', output: {} });
  await assert.rejects(writer.start('agent.turn.start', { conversation_id: 'conv_fixture' }, (value: any) => {
    if (typeof value.turn_id !== 'string') throw new Error('invalid_turn_response');
  }));
  assert.equal(writer.getSnapshot().phase, 'uncertain');
  assert.equal(keys, 2);
  for (const [error, uncertain] of [[new client.ApiError(500, 'internal', ''), true], [new client.ApiError(429, 'rate_limited', ''), true],
    [new client.ApiError(403, 'forbidden', '', undefined, true), true], [new client.ApiError(404, 'billing_consent_not_found', ''), false],
    [new client.ApiError(409, 'idempotency_conflict', ''), true], [new TypeError('fetch failed'), true]] as const) {
    assert.equal(model.uncertainConsentError(error, false), uncertain, String((error as any).code ?? error));
  }
});

test('consent copy has three languages with the same parameters', () => {
  assert.deepEqual(core.validateMessages(copy.consentMessages), []);
  for (const [key, [zh, en, hant]] of Object.entries(copy.consentCopy) as [string, string[]][]) {
    assert.ok(zh && en && hant && !/[㐀-鿿]/.test(en), key);
  }
});

test('payment terms render the exact maximum, run count and lifetime in each language', () => {
  const props = { consent: consent({ used_runs: 1, remaining_runs: 1 }), payer: 'Fixture Person',
    spaces: [{ workspace_id: 'ws_a', name: 'Fixture Space' }] };
  for (const [locale, expected] of [['en', ['Maximum potential total', '50.00 FIXTURE × 2 runs = 100.00 FIXTURE', 'Until I revoke it', '1 of 2 runs remaining']],
    ['zh-Hans', ['最大潜在总额', '50.00 FIXTURE × 2 次 = 100.00 FIXTURE', '直到本人撤销', '剩余 1 / 2 次']],
    ['zh-Hant', ['最大潛在總額', '50.00 FIXTURE × 2 次 = 100.00 FIXTURE', '直到本人撤銷', '剩餘 1 / 2 次']]] as const) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(React.createElement(ui.I18nProvider ?? React.Fragment, null,
      React.createElement(controls.ConsentTerms, props)));
    for (const text of expected) assert.ok(html.includes(text), `${locale}: ${text}`);
    if (locale === 'en') assert.ok(!/[㐀-鿿]/.test(html.replace(/Fixture Space/g, '')), html);
  }
});
