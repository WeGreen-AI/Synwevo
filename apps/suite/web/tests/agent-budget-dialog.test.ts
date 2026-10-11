/** The quote dialog's review of the chosen sources before any payment: the compiled production
 *  BudgetDialog with explicit read doubles. No live model, billing, payment or browser claims. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';

let server: any, ui: any, core: any, chatParts: any, controls: any, budgetCopy: any, contextCopy: any;
before(async () => {
  server = await testServer();
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  for (const path of ['/src/shared/messages.ts', '/src/apps/agent/messages.ts']) await server.ssrLoadModule(path);
  [chatParts, controls, budgetCopy, contextCopy] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/ChatParts.tsx'), server.ssrLoadModule('/src/apps/agent/ConsentControls.tsx'),
    server.ssrLoadModule('/src/apps/agent/budget.messages.ts'), server.ssrLoadModule('/src/apps/agent/context.messages.ts'),
  ]);
});
after(async () => { await server?.close(); });
const settle = async (rounds = 3) => { for (let i = 0; i < rounds; i++) await new Promise<void>((resolve) => setImmediate(resolve)); };
const en = (source: string, params?: any) => core.translate('en', source, params);
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node)
  ? [node, ...all(node.props.children), ...all(node.props.footer), ...all(node.props.aside)] : [];
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';

const business = { kind: 'form', workspace_id: 'ws_a', resource_id: 'frm_123456789abc', snapshot: 'a'.repeat(64) };
const file = { file_id: 'af_123456789abc', snapshot: 'b'.repeat(64) };
const intentWithSources = { conversation_id: 'conv_fixture', text: 'Synthetic question', context_refs: [business], file_refs: [file] };
const offer = { environment: 'test', model: 'fixture-flash', confirmed_route: 'fixture-flash', route_version: 'v1', price_id: 'price_fixture',
  unit: { code: 'TEST', scale: 2 }, rates: { input_uncached: { numerator: '200', denominator: '1' }, input_cached: { numerator: '4', denominator: '1' },
  output: { numerator: '800', denominator: '1' } }, per_tokens: 1000000, terms_ref: 'terms_fixture', per_call_hold: '20',
  max_calls_limit: 12, run_seconds_limit: 3600, run_seconds_min: 1 };
const contextResult = () => ({ reference: { ...business }, title: 'Quarterly form (long name) 季度表单名称很长很长', workspace_name: 'Source workspace', open_path: null });
const fileResult = () => ({ id: file.file_id, filename: 'Attachment-with-a-very-long-file-name-附件名称.txt', size: 4, created_at: 1791634501.123456,
  updated_at: 1791634502.654321, attempts: 1, status: 'ready', error_code: null, scan_status: 'not_configured',
  reference: { ...file }, download_path: `/api/suite/agent/files/${file.file_id}/download`, extraction: { lines: [{ text: 'synthetic private content', source: { line: 1 } }], tables: [] } });

/** Reads answered by explicit handlers; every call is recorded with the signal it was given. */
function reads(handlers: Record<string, (input: any) => any>) {
  const calls: { operation: string; input: any; workspace: any; signal: AbortSignal }[] = [];
  return { calls, handlers, read: async (operation: string, input: any, workspace: any, signal: AbortSignal) => {
    calls.push({ operation, input, workspace, signal });
    const handler = handlers[operation];
    if (!handler) throw new Error(`unexpected read ${operation}`);
    return handler(input);
  } };
}
const healthy = () => reads({ 'agent.billing.offer': () => offer, 'agent.context.resolve': contextResult, 'agent.file.get': fileResult });

/** Hooks are fixtures; the component body and its handlers are the compiled production code. */
async function dialog(props: any, read: any) {
  let index = 0, tree: any, dirty = false;
  const values: any[] = [], effects: any[] = [], pending: (() => void)[] = [];
  const hooks = { useId: () => 'budget-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (next: any) => { const value = typeof next === 'function' ? next(values[i]) : next;
        if (!Object.is(value, values[i])) { values[i] = value; dirty = true; } }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pending.push(() => { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; }); },
    useSyncExternalStore: (_subscribe: any, getSnapshot: any) => getSnapshot() };
  const i18n = () => ({ t: en, locale: 'en' });
  const imports: Record<string, any> = { react: { ...React, ...hooks }, 'react/jsx-runtime': jsxRuntime,
    '@synwevo/ui': { ...ui, useI18n: i18n }, '../../shared/ops': { read },
    './ChatParts': { ...chatParts, useChatText: () => ({ c: (key: string, params?: any) => en(chatParts.chatCopy?.[key]?.[0] ?? key, params), ...i18n() }) },
    './ConsentControls': { ...controls, useConsentText: () => ({ m: (key: string, params?: any) => en(key, params), ...i18n() }) } };
  const code = transformSync('BudgetDialog.tsx', readFileSync(new URL('../src/apps/agent/BudgetDialog.tsx', import.meta.url), 'utf8'),
    { target: 'es2022', jsx: { runtime: 'automatic' } });
  assert.equal(code.errors.length, 0);
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, Date, Intl, BigInt, window: { setInterval: () => 0, clearInterval() {} } });
  const module = new vm.SourceTextModule(code.code, { context });
  await module.link(async (name) => {
    const members = imports[name] ?? (name.endsWith('.css') ? {} : await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name));
    return new vm.SyntheticModule(Object.keys(members), function () {
      for (const [key, value] of Object.entries(members)) this.setExport(key, value);
    }, { context, identifier: name });
  });
  await module.evaluate();
  const fn = (module.namespace as any).default;
  const render = () => {
    let loops = 0;
    do { assert.ok(loops++ < 20, 'fixture render stabilizes'); dirty = false; index = 0; tree = fn(props); while (pending.length) pending.shift()!(); } while (dirty);
    return tree;
  };
  render();
  const elements = () => all(tree);
  return { render, elements, text: () => strings(tree), unmount: () => effects.forEach((effect) => effect?.cleanup?.()),
    submit: () => elements().filter((el) => el.type === 'button' && el.props.type === 'submit' && el.props.form === 'budget-fixture-form'),
    // Errors made inside the compiled module belong to the vm realm: match by shape, not instanceof.
    failure: () => elements().filter((el) => el.props.error != null && typeof el.props.error.message === 'string' && 'onRetry' in el.props),
    button: (label: string) => { const matches = elements().filter((el) => el.type === 'button' && strings(el.props.children) === label);
      assert.equal(matches.length, 1, label); return matches[0].props; } };
}
const props = (intent: any, onClose = () => {}) => ({ intent, onQuote: async () => { throw new Error('must not quote'); }, onConfirm: async () => { throw new Error('must not confirm'); }, onClose });

test('before a quote the dialog names each chosen source with its kind and workspace or “Personal file”, read through one shared signal', async () => {
  const r = healthy();
  const h = await dialog(props(intentWithSources), r.read);
  assert.equal(h.text().includes(en(budgetCopy.budgetCopy.sources[0])), true, 'the section exists while loading');
  await settle(); h.render();
  const text = h.text();
  for (const expected of ['Quarterly form (long name) 季度表单名称很长很长', en(contextCopy.contextCopy.form[0]), 'Source workspace',
    'Attachment-with-a-very-long-file-name-附件名称.txt', en(budgetCopy.budgetCopy.personalFile[0]), en(budgetCopy.budgetCopy.sourcesNote[0])]) {
    assert.ok(text.includes(expected), expected);
  }
  assert.ok(!text.includes('synthetic private content'), 'no source body is shown');
  assert.deepEqual(r.calls.map((c) => [c.operation, c.workspace]).sort(), [['agent.billing.offer', null], ['agent.context.resolve', null], ['agent.file.get', null]]);
  assert.equal(new Set(r.calls.map((c) => c.signal)).size, 1, 'one AbortSignal for the offer and the review');
  assert.equal(r.calls.find((c) => c.operation === 'agent.file.get')!.input.file_id, file.file_id);
  assert.equal(h.submit().length, 1); assert.equal(h.submit()[0].props.disabled, false);
  assert.equal(h.failure().length, 0);
  h.unmount();
});

test('a changed, deleted or revoked source blocks the quote with its own explanation; retry re-reads every source and recovers', async () => {
  for (const change of ['business', 'file', 'deleted', 'revoked'] as const) {
    const r = healthy();
    if (change === 'business') r.handlers['agent.context.resolve'] = () => ({ ...contextResult(), reference: { ...business, snapshot: 'c'.repeat(64) } });
    if (change === 'file') r.handlers['agent.file.get'] = () => ({ ...fileResult(), reference: { ...file, snapshot: 'c'.repeat(64) } });
    if (change === 'deleted') r.handlers['agent.file.get'] = () => ({ ...fileResult(), status: 'deleted', reference: null, extraction: null, download_path: null });
    if (change === 'revoked') r.handlers['agent.context.resolve'] = () => { throw new Error('source_access_denied'); };
    const h = await dialog(props(intentWithSources), r.read);
    await settle(); h.render();
    assert.equal(h.submit().length, 0, `${change}: no quote button`);
    assert.equal(h.failure().length, 1, change);
    assert.equal(h.failure()[0].props.error.message, en(budgetCopy.budgetCopy.sourcesUnavailable[0]), change);
    assert.ok(!h.text().includes('Source workspace') && !h.text().includes('source_access_denied'), `${change}: no partial list, no raw server text`);
    const before = r.calls.length;
    Object.assign(r.handlers, healthy().handlers);
    h.failure()[0].props.onRetry(); await settle(); h.render();
    assert.equal(r.calls.length - before, 3, `${change}: retry reads the offer and both sources again`);
    assert.equal(h.failure().length, 0, change); assert.equal(h.submit().length, 1, change);
    assert.ok(h.text().includes('Source workspace'), change);
    h.unmount();
  }
});

test('an offer failure with healthy sources keeps the offer wording, and a plain or regeneration intent reads no source', async () => {
  const failing = healthy(); failing.handlers['agent.billing.offer'] = () => { throw new Error('offer_unavailable_fixture'); };
  const h = await dialog(props(intentWithSources), failing.read);
  await settle(); h.render();
  assert.equal(h.failure()[0].props.error.message, en(budgetCopy.budgetCopy.offerError[0]));
  assert.equal(h.submit().length, 0); h.unmount();
  for (const intent of [{ conversation_id: 'conv_fixture', text: 'plain' }, { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' }]) {
    const r = healthy();
    const p = await dialog(props(intent), r.read);
    await settle(); p.render();
    assert.deepEqual(r.calls.map((c) => c.operation), ['agent.billing.offer'], 'only the offer is read');
    assert.ok(!p.text().includes(en(budgetCopy.budgetCopy.sources[0])), 'no source section');
    assert.equal(p.submit().length, 1); p.unmount();
  }
});

test('closing while the review is pending aborts it, and its late answer changes nothing', async () => {
  let release!: () => void;
  const slow = healthy();
  slow.handlers['agent.file.get'] = () => new Promise((resolve) => { release = () => resolve(fileResult()); });
  let closed = 0;
  const h = await dialog(props(intentWithSources, () => { closed++; }), slow.read);
  await settle();
  const signal = slow.calls[0].signal;
  assert.equal(signal.aborted, false);
  h.button(en(budgetCopy.budgetCopy.close[0])).onClick();
  assert.equal(closed, 1); assert.equal(signal.aborted, true, 'the shared signal is aborted on close');
  release(); await settle(); h.render();
  assert.ok(!h.text().includes('Source workspace'), 'a late review never reaches a closed dialog');
  assert.equal(h.submit().length, 0);
  h.unmount();
});
