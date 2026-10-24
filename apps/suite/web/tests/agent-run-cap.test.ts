/** Agent settings, send-and-go (design §3, §4 B): "每条回答的最高金额" — the default bound the server
 *  reports (`agent.billing.status.run`) or a lower maximum of the person's own, saved through
 *  `agent.settings.update` (`run_budget_cap` / `clear_run_budget_cap`) and never below one call's
 *  hold — and the read-only list of the document versions accepted in the one-time setup step.
 *  Every response is an explicit FIXTURE in the contract's shapes; no server is involved. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as router from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/settings'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, ui: any, core: any, client: any, copy: any;
before(async () => {
  server = await testServer();
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  for (const path of ['/src/shared/messages.ts', '/src/apps/agent/messages.ts']) await server.ssrLoadModule(path);
  [client, copy] = await Promise.all([server.ssrLoadModule('@synwevo/auth-client'), server.ssrLoadModule('/src/apps/agent/chat.messages.ts')]);
});
after(async () => { await server?.close(); });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));
const plain = (value: any) => JSON.parse(JSON.stringify(value));
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node) ? [node, ...all(node.props.children)] : [];
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';

// ---------------------------------------------------------------- FIXTURE responses (design §3)
const UNIT = { code: 'CNY', scale: 2 };
const run = (overrides: Record<string, unknown> = {}) => ({ maximum: '468', default: '468', cap: null, source: 'default', per_call_hold: '39',
  max_calls: 12, run_seconds: 3600, usable: true, ...overrides });
const status = (overrides: Record<string, unknown> = {}) => ({ available: true, environment: 'test', unit: UNIT, per_call_hold: '39', run: run(), price_notice: null, ...overrides });
const settings = (cap: unknown = null) => ({ effort: 'high', prefer_personal_key: false, memory_enabled: true, personal_key: { set: false },
  personal_key_supported: false, billing: { required: true, run_budget_cap: cap }, model_configured: true, platform_configured: true,
  active: { credential: 'platform', model: 'fixture-model', provider: 'deepseek' }, efforts: ['high'] });

/** Hooks are fixtures; the component bodies and handlers are compiled production code. */
async function compiled(entry: string, initialProps: any, overrides: Record<string, any> = {}) {
  let index = 0, props = initialProps, tree: any, dirty = false;
  const values: any[] = [], effects: any[] = [], pending: (() => void)[] = [];
  const hooks = { useId: () => 'cap-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (next: any) => { const value = typeof next === 'function' ? next(values[i]) : next;
        if (!Object.is(value, values[i])) { values[i] = value; dirty = true; } }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
    useMemo: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, value: fn() }; return values[i].value; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pending.push(() => {
        effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() };
      }); },
  };
  let locale = 'en';
  const imports = { react: { ...React, ...hooks }, 'react/jsx-runtime': jsxRuntime, 'react-router-dom': router,
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (source: string, params: any) => core.translate(locale, source, params), locale }), useDialog: () => ({}) },
    '@synwevo/auth-client': { ...client, useSession: () => ({ me: { id: 'usr_fixture' }, state: { site_url: 'https://site.fixture.test' } }) }, ...overrides };
  const code = transformSync('Settings.tsx', readFileSync(new URL('../src/apps/agent/Settings.tsx', import.meta.url), 'utf8'),
    { target: 'es2022', jsx: { runtime: 'automatic' } });
  assert.equal(code.errors.length, 0);
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, JSON, Promise, BigInt });
  const module = new vm.SourceTextModule(code.code, { context });
  await module.link(async (name) => {
    const members = imports[name] ?? (name.endsWith('.css') ? {} : await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name));
    return new vm.SyntheticModule(Object.keys(members), function () {
      for (const [key, value] of Object.entries(members)) this.setExport(key, value);
    }, { context, identifier: name });
  });
  await module.evaluate();
  const fn = (module.namespace as any)[entry];
  const rerender = (next = {}) => {
    props = { ...props, ...next };
    let loops = 0;
    do { assert.ok(loops++ < 20, 'fixture render stabilizes'); dirty = false; index = 0; tree = fn(props); while (pending.length) pending.shift()!(); } while (dirty);
    return tree;
  };
  rerender();
  return { render: rerender, tree: () => tree, elements: () => all(tree), text: () => strings(tree), locale: (next: string) => { locale = next; rerender(); },
    radios: () => all(tree).filter((el) => el.type === 'input' && el.props.type === 'radio'),
    amount: () => all(tree).find((el) => el.type === 'input' && el.props.type === 'text'),
    submit: () => all(tree).find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }),
    unmount: () => effects.forEach((effect) => effect?.cleanup?.()) };
}

async function capFixture(cap: unknown = null, money: unknown = status()) {
  const writes: { operation: string; input: any }[] = [];
  const changes: any[] = [];
  let reply: any = (input: any) => settings(input.clear_run_budget_cap ? null : { amount: input.run_budget_cap, unit: UNIT });
  const h = await compiled('RunCapSettings', { settings: settings(cap), status: money, onChanged: (value: any) => changes.push(value) }, {
    '../../shared/ops': { newKey: () => `fixture_key_${writes.length}`, read: async () => null,
      op: async (operation: string, input: any) => { writes.push({ operation, input }); return { status: 'succeeded', output: reply(input) }; } },
  });
  return { ...h, writes, changes, reply: (fn: any) => { reply = fn; } };
}

test('the default maximum is the server\'s bound at the current price; a custom one is saved in sale units and applies afterwards', async () => {
  const h = await capFixture();
  const [byDefault, custom] = h.radios();
  assert.equal(byDefault.props.checked, true); assert.equal(custom.props.checked, false);
  assert.ok(h.text().includes('Maximum per answer'), h.text());
  assert.ok(h.text().includes('Default: at most CN¥4.68 at the current price (the hold for 12 model calls)'), h.text());
  assert.equal(h.amount(), undefined);
  // Nothing to save while the default is kept.
  h.submit(); await settle(); assert.deepEqual(h.writes, []);
  custom.props.onChange(); h.render();
  assert.ok(h.text().includes('Cannot be lower than one call’s hold, CN¥0.39'), h.text());
  h.amount().props.onChange({ target: { value: '2.50' } }); h.render();
  h.submit(); await settle(); await settle(); h.render();
  assert.deepEqual(plain(h.writes), [{ operation: 'agent.settings.update', input: { run_budget_cap: '250' } }]);
  assert.equal(h.changes.length, 1);
  h.unmount();
});

test('a cap below one call\'s hold, or a malformed amount, is not saved and says why', async () => {
  const h = await capFixture();
  h.radios()[1].props.onChange(); h.render();
  for (const [value, words] of [['0.38', 'Cannot be lower than one call’s hold, CN¥0.39'], ['abc', 'Enter an amount above zero'], ['0', 'Enter an amount above zero'],
    ['1.234', 'Enter an amount above zero']] as const) {
    h.amount().props.onChange({ target: { value } }); h.render();
    h.submit(); await settle(); h.render();
    const alert = h.elements().find((el) => el.props.role === 'alert');
    assert.ok(alert && strings(alert.props.children).includes(words), `${value}: ${alert && strings(alert.props.children)}`);
    assert.equal(h.amount().props['aria-invalid'], true);
  }
  assert.deepEqual(h.writes, []);
  h.amount().props.onChange({ target: { value: '0.39' } }); h.render();
  h.submit(); await settle(); await settle(); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { run_budget_cap: '39' });
  h.unmount();
});

test('an existing cap is shown and can be cleared back to the default', async () => {
  const h = await capFixture({ amount: '200', unit: UNIT }, status({ run: run({ maximum: '200', cap: '200', source: 'personal_cap' }) }));
  assert.equal(h.radios()[1].props.checked, true);
  assert.equal(h.amount().props.value, '2.00');
  assert.ok(h.text().includes('Current: CN¥2.00'), h.text());
  h.radios()[0].props.onChange(); h.render();
  h.submit(); await settle(); await settle(); h.render();
  assert.deepEqual(plain(h.writes), [{ operation: 'agent.settings.update', input: { clear_run_budget_cap: true } }]);
  assert.equal(h.radios()[0].props.checked, true);
  h.unmount();
});

test('an unusable cap or a changed unit is said plainly; without the status no amount is invented', async () => {
  const low = await capFixture({ amount: '10', unit: UNIT }, status({ run: run({ maximum: '10', cap: '10', source: 'personal_cap', usable: false }) }));
  assert.ok(low.elements().some((el) => el.props.role === 'status' && strings(el.props.children).includes('Cannot be lower than one call’s hold, CN¥0.39')));
  low.unmount();
  const moved = await capFixture({ amount: '10', unit: { code: 'CNY', scale: 4 } }, status({ run: run({ usable: false }) }));
  assert.ok(moved.text().includes(core.translate('en', copy.chatCopy.runCapUnitChanged[0])), moved.text());
  assert.equal(moved.amount().props.value, '');
  moved.unmount();
  const unknown = await capFixture(null, null);
  assert.ok(unknown.text().includes('Default: worked out from the current price'), unknown.text());
  assert.equal(unknown.radios()[1].props.disabled, true);
  unknown.unmount();
});

test('the accepted document versions are listed read-only in three languages, linked to the public pages', async () => {
  // FIXTURE agent.setup.get (terms block).
  const setup = { required: true, complete: false, provider_consent: null,
    terms: { complete: false, documents: [{ id: 'terms', version: '2026-10-24', effective: '2026-10-24', accepted: true },
      { id: 'ai-use', version: '2026-10-05', effective: '2026-10-05', accepted: true }, { id: 'pricing', version: '2026-10-25', effective: '2026-10-25', accepted: false }] },
    limits: { configured: true, enabled: true, revision: 1, unit: UNIT, suggested: null } };
  const h = await compiled('AcceptedDocuments', { setup });
  const words = { en: ['Accepted document versions', 'Version 2026-10-24 (in effect from 2026-10-24) · accepted', 'Version 2026-10-25 (in effect from 2026-10-25) · not accepted yet'],
    'zh-Hans': ['已接受的文件版本', '版本 2026-10-24（自 2026-10-24 起生效）· 已接受', '版本 2026-10-25（自 2026-10-25 起生效）· 尚未接受'],
    'zh-Hant': ['已接受的文件版本', '版本 2026-10-24（自 2026-10-24 起生效）· 已接受', '版本 2026-10-25（自 2026-10-25 起生效）· 尚未接受'] } as const;
  for (const locale of ['en', 'zh-Hans', 'zh-Hant'] as const) {
    h.locale(locale);
    for (const text of words[locale]) assert.ok(h.text().includes(text), `${locale}: ${text} in ${h.text()}`);
  }
  const links = h.elements().filter((el) => el.type === 'a').map((el) => el.props.href);
  assert.deepEqual(links, ['https://site.fixture.test/zh-Hant/terms', 'https://site.fixture.test/zh-Hant/ai-use', 'https://site.fixture.test/zh-Hant/pricing']);
  // Nothing on this list can be ticked or changed: acceptance happens only in the setup step.
  assert.ok(!h.elements().some((el) => el.type === 'input' || el.type === 'button'));
  h.render({ setup: null });
  assert.ok(h.text().includes('暫時無法讀取文件版本'), h.text());
  h.unmount();
  const settingsSource = readFileSync(new URL('../src/apps/agent/Settings.tsx', import.meta.url), 'utf8');
  assert.match(settingsSource, /billing\.required && <AnswerBilling /);
});

test('the choices are one group for assistive technology without a second frame inside the card', async () => {
  const h = await capFixture();
  const groups = h.elements().filter((el) => el.type === 'fieldset');
  assert.equal(groups.length, 1);
  assert.match(groups[0].props.className, /\bag-plain-fieldset\b/);
  assert.equal(h.elements().filter((el) => el.type === 'legend' && el.props.className === 'sr-only').length, 1);
  const css = readFileSync(new URL('../src/apps/agent/agent.css', import.meta.url), 'utf8');
  assert.match(css, /\n\.ag-plain-fieldset \{ margin: 0; padding: 0; border: 0; min-width: 0; \}/);
  h.unmount();
});
