/** The one-time "Start using Synwevo Agent" step (design D-5): an inline region that shows only
 *  what is still missing — the separate DeepSeek data-sharing consent, the terms / AI use rules /
 *  pricing, and (personal payer) the spending limits prefilled with the suggested values — with
 *  two separate unticked checkboxes, writes in the order consent → terms → limits, and a draft
 *  that survives any failure. Every response is an explicit FIXTURE in the contract's shapes. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import * as router from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, ui: any, core: any, setup: any, copy: any, consentCopy: any, providerConsent: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'setup-fixture-locale', enforce: 'pre', transform(code, id) {
    if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
  } }] });
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  [setup, copy, consentCopy, providerConsent] = await Promise.all([server.ssrLoadModule('/src/apps/agent/AgentSetup.tsx'),
    server.ssrLoadModule('/src/apps/agent/chat.messages.ts'), server.ssrLoadModule('/src/apps/agent/provider-consent.messages.ts'),
    server.ssrLoadModule('/src/apps/agent/ProviderConsent.tsx')]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));
const plain = (value: any) => JSON.parse(JSON.stringify(value));
const LOCALES = ['zh-Hans', 'en', 'zh-Hant'] as const;
const UNIT = { code: 'CNY', scale: 2 };
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node) ? [node, ...all(node.props.children)] : [];
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';

// ---------------------------------------------------------------- FIXTURE responses (agent.setup.get, agent.provider_consent.get)
const documents = (accepted: boolean) => [{ id: 'terms', version: '2026-10-24', effective: '2026-10-24', accepted },
  { id: 'ai-use', version: '2026-10-05', effective: '2026-10-05', accepted }, { id: 'pricing', version: '2026-10-24', effective: '2026-10-24', accepted }];
function fixtureSetup(overrides: Record<string, unknown> = {}) {
  return { required: true, complete: false,
    provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: false, accepted_version: null },
    terms: { complete: false, documents: documents(false) },
    limits: { configured: false, enabled: false, revision: 0, unit: UNIT, suggested: { daily_budget: '1000', weekly_budget: '3000', monthly_budget: '10000' } },
    ...overrides };
}
const consentFacts = { provider: 'deepseek', version: '2026-10-05', effective_at: '2026-10-05', processor: { 'zh-Hans': 'Fixture DeepSeek 处理方', en: 'Fixture DeepSeek processor' },
  data_location: 'cn', accepted: false, accepted_version: null, accepted_at: null, required: true, history: [] };

/** Hooks are fixtures; the body and handlers are compiled production code. A ref that starts null gets a focus spy. */
async function compiled(initialProps: any, overrides: Record<string, any> = {}) {
  let index = 0, props = initialProps, tree: any, dirty = false;
  const values: any[] = [], effects: any[] = [], pending: (() => void)[] = [];
  const focused: string[] = [];
  const hooks = { useId: () => 'setup-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (next: any) => { const value = typeof next === 'function' ? next(values[i]) : next;
        if (!Object.is(value, values[i])) { values[i] = value; dirty = true; } }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial === null ? { focus: () => focused.push('heading') } : initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
    useMemo: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, value: fn() }; return values[i].value; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pending.push(() => {
        effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() };
      }); },
  };
  const imports = { react: { ...React, ...hooks }, 'react/jsx-runtime': jsxRuntime, 'react-router-dom': router,
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (source: string, params: any) => core.translate('en', source, params), locale: 'en' }) }, ...overrides };
  const code = transformSync('AgentSetup.tsx', readFileSync(new URL('../src/apps/agent/AgentSetup.tsx', import.meta.url), 'utf8'),
    { target: 'es2022', jsx: { runtime: 'automatic' } });
  assert.equal(code.errors.length, 0);
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, JSON, Promise });
  const module = new vm.SourceTextModule(code.code, { context });
  await module.link(async (name) => {
    const members = imports[name] ?? (name.endsWith('.css') ? {} : await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name));
    return new vm.SyntheticModule(Object.keys(members), function () {
      for (const [key, value] of Object.entries(members)) this.setExport(key, value);
    }, { context, identifier: name });
  });
  await module.evaluate();
  const fn = (module.namespace as any).default;
  const rerender = (next = {}) => {
    props = { ...props, ...next };
    let loops = 0;
    do { assert.ok(loops++ < 20, 'fixture render stabilizes'); dirty = false; index = 0; tree = fn(props); while (pending.length) pending.shift()!(); } while (dirty);
    return tree;
  };
  const find = (predicate: (el: any) => boolean) => { const matches = all(tree).filter(predicate); assert.equal(matches.length, 1, 'one matching element'); return matches[0]; };
  rerender();
  return { render: rerender, find, tree: () => tree, elements: () => all(tree), text: () => strings(tree), focused,
    checkboxes: () => all(tree).filter((el) => el.type === 'input' && el.props.type === 'checkbox'),
    inputs: () => all(tree).filter((el) => el.type === 'input' && el.props.type !== 'checkbox'),
    submit: () => find((el) => el.type === 'button' && el.props.type === 'submit').props,
    unmount: () => effects.forEach((effect) => effect?.cleanup?.()) };
}

async function cardFixture(state = fixtureSetup(), extra: Record<string, unknown> = {}) {
  const writes: { operation: string; input: any }[] = [];
  const failures = new Map<string, unknown>();
  let completed = 0, changed = 0;
  const h = await compiled({ setup: state, organizationPayer: false, hasDraft: true, maximum: { amount: '468', unit: UNIT }, siteUrl: 'https://site.fixture.test',
    read: async (operation: string) => { assert.equal(operation, 'agent.provider_consent.get'); return consentFacts; },
    write: async (operation: string, input: any) => { writes.push({ operation, input }); if (failures.has(operation)) throw failures.get(operation); return {}; },
    onComplete: async () => { completed += 1; }, onChanged: () => { changed += 1; }, focus: true, ...extra });
  await settle(); h.render();
  return { ...h, writes, fail: (operation: string, error: unknown) => failures.set(operation, error), completed: () => completed, changed: () => changed };
}

test('the card is a labelled region that takes focus, shows only the missing items and offers two separate unticked checkboxes', async () => {
  const h = await cardFixture();
  const region = h.tree();
  assert.equal(region.props.role, 'region'); assert.equal(region.props['aria-labelledby'], 'setup-fixture-title');
  assert.deepEqual(h.focused, ['heading']);
  assert.equal(h.checkboxes().length, 2);
  for (const box of h.checkboxes()) assert.equal(box.props.checked, false);
  assert.equal(h.inputs().length, 3);
  assert.deepEqual(h.inputs().map((el) => el.props.value), ['10.00', '30.00', '100.00']);
  assert.equal(h.submit().disabled, true);
  assert.equal(strings(h.submit().children), core.translate('en', copy.chatCopy.setupAgreeSend[0]));
  // The provider's facts are the very notice the Settings card shows (ProviderConsentFacts), not a
  // paraphrase, with its links opening in a new tab so the kept message is not lost.
  const facts = h.find((el) => el.type === providerConsent.ProviderConsentFacts);
  assert.equal(facts.props.facts, consentFacts);
  assert.equal(facts.props.siteUrl, 'https://site.fixture.test');
  ui.setLocale('en');
  const notice = renderToStaticMarkup(React.createElement(router.MemoryRouter, null, facts));
  assert.ok(notice.includes(core.translate('en', consentCopy.providerConsentCopy.intro[0])), notice);
  assert.ok(notice.includes('Fixture DeepSeek processor'), notice);
  assert.equal(notice.match(/target="_blank" rel="noopener noreferrer"/g)?.length, 2, notice);
  ui.setLocale('zh-Hans');
  // The three documents are linked on the public site.
  const links = h.elements().filter((el) => el.type === 'a').map((el) => el.props.href);
  for (const page of ['terms', 'ai-use', 'pricing']) assert.ok(links.includes(`https://site.fixture.test/en/${page}`), page);
  h.unmount();
  // Only the missing items are shown.
  const consented = await cardFixture(fixtureSetup({ provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: true, accepted_version: '2026-10-05' } }));
  assert.equal(consented.checkboxes().length, 1); assert.equal(consented.inputs().length, 3); consented.unmount();
  const agreed = await cardFixture(fixtureSetup({ terms: { complete: true, documents: documents(true) } }));
  assert.equal(agreed.checkboxes().length, 1); assert.equal(agreed.inputs().length, 3); agreed.unmount();
  const organization = await cardFixture(fixtureSetup(), { organizationPayer: true });
  assert.equal(organization.checkboxes().length, 2); assert.equal(organization.inputs().length, 0); organization.unmount();
  const noDraft = await cardFixture(fixtureSetup(), { hasDraft: false, focus: false });
  assert.equal(strings(noDraft.submit().children), core.translate('en', copy.chatCopy.setupAgreeStart[0]));
  assert.deepEqual(noDraft.focused, []); noDraft.unmount();
});

test('writes run in the order consent, terms, limits, and only then the draft is sent', async () => {
  const h = await cardFixture();
  const [consent, terms] = h.checkboxes();
  consent.props.onChange({ target: { checked: true } }); h.render();
  assert.equal(h.submit().disabled, true, 'one box is not enough');
  h.checkboxes()[1].props.onChange({ target: { checked: true } }); h.render();
  assert.equal(h.submit().disabled, false);
  assert.notEqual(consent.props.id, terms.props.id);
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); await settle(); h.render();
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.provider_consent.accept', 'agent.terms.accept', 'agent.billing.budget.set']);
  assert.deepEqual(plain(h.writes[0].input), { version: '2026-10-05' });
  assert.deepEqual(plain(h.writes[1].input), { documents: [{ id: 'terms', version: '2026-10-24' }, { id: 'ai-use', version: '2026-10-05' }, { id: 'pricing', version: '2026-10-24' }] });
  assert.deepEqual(plain(h.writes[2].input), { unit_code: 'CNY', expected_revision: 0, enabled: true, daily_budget: '1000', weekly_budget: '3000', monthly_budget: '10000' });
  assert.equal(h.completed(), 1);
  h.unmount();
});

test('edited limits are sent as the person typed them; a malformed amount stops before any write', async () => {
  const h = await cardFixture();
  for (const box of h.checkboxes()) box.props.onChange({ target: { checked: true } });
  h.render();
  h.inputs()[0].props.onChange({ target: { value: '5' } }); h.render();
  h.inputs()[2].props.onChange({ target: { value: 'abc' } }); h.render();
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); h.render();
  assert.deepEqual(h.writes, []); assert.equal(h.completed(), 0);
  assert.ok(h.elements().some((el) => el.props.role === 'alert'));
  h.inputs()[2].props.onChange({ target: { value: '250.5' } }); h.render();
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); await settle(); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { unit_code: 'CNY', expected_revision: 0, enabled: true, daily_budget: '500', weekly_budget: '3000', monthly_budget: '25050' });
  assert.equal(h.completed(), 1);
  h.unmount();
});

test('a failure in the middle stops there, keeps the draft, says one step is unfinished and asks for the state again', async () => {
  const h = await cardFixture();
  h.fail('agent.terms.accept', { code: 'terms_version_changed', detail: { documents: documents(false) } });
  for (const box of h.checkboxes()) box.props.onChange({ target: { checked: true } });
  h.render();
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); await settle(); h.render();
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.provider_consent.accept', 'agent.terms.accept']);
  assert.equal(h.completed(), 0);
  assert.ok(h.changed() >= 1);
  const alert = h.find((el) => el.props.role === 'alert');
  assert.ok(strings(alert.props.children).includes(core.translate('en', copy.chatCopy.setupPartial[0])), strings(alert.props.children));
  assert.ok(h.text().includes(core.translate('en', copy.chatCopy.setupTermsUpdated[0])));
  h.unmount();
});

test('the card reads the same in three languages, with the agreed wording and an unticked terms box', () => {
  const words = { 'zh-Hans': ['开始使用 Synwevo Agent', '只需设置一次', '我已阅读并接受《服务条款》《AI 使用规范》与《定价》', '每条回答最多', '个人支出上限：每天', '同意并发送'],
    en: ['Start using Synwevo Agent', 'You only do this once', 'I have read and accept the Terms of service, the AI use rules and the Pricing', 'each answer costs at most', 'Personal spending limits:', 'Agree and send'],
    'zh-Hant': ['開始使用 Synwevo Agent', '只需設定一次', '我已閱讀並接受《服務條款》《AI 使用規範》與《定價》', '每則回答最多', '個人支出上限：每天', '同意並傳送'] } as const;
  for (const locale of LOCALES) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(React.createElement(router.MemoryRouter, null, React.createElement(ui.I18nProvider ?? React.Fragment, null,
      React.createElement(setup.default, { setup: fixtureSetup(), organizationPayer: false, hasDraft: true, maximum: { amount: '468', unit: UNIT },
        siteUrl: 'https://site.fixture.test', read: async () => consentFacts, write: async () => ({}), onComplete: async () => {}, onChanged() {} }))));
    for (const text of words[locale]) assert.ok(html.includes(text), `${locale}: ${text} in ${html}`);
    assert.match(html, /<section[^>]*role="region"[^>]*aria-labelledby="[^"]+-title"/);
    assert.equal(html.match(/type="checkbox"/g)?.length, 2);
    assert.ok(!/type="checkbox"[^>]*checked/.test(html), 'nothing is pre-ticked');
    assert.match(html, /<button type="submit"[^>]*disabled=""/);
  }
  assert.deepEqual(core.validateMessages(copy.chatMessages), []);
});

test('what is still needed is decided from the server state and the payer', () => {
  assert.deepEqual(setup.setupItems(fixtureSetup(), false), { consent: true, terms: true, limits: true });
  assert.deepEqual(setup.setupItems(fixtureSetup(), true), { consent: true, terms: true, limits: false });
  assert.equal(setup.setupNeeded(fixtureSetup(), false), true);
  const done = fixtureSetup({ complete: true, provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: true, accepted_version: '2026-10-05' },
    terms: { complete: true, documents: documents(true) }, limits: { configured: true, enabled: true, revision: 2, unit: UNIT, suggested: null } });
  assert.equal(setup.setupNeeded(done, false), false);
  assert.equal(setup.setupNeeded(fixtureSetup({ required: false }), false), false);
  // No provider consent to ask for (another provider), disabled limits are a settings matter, not setup.
  assert.deepEqual(setup.setupItems({ ...done, provider_consent: null, limits: { ...done.limits, enabled: false } }, false), { consent: false, terms: false, limits: false });
  assert.equal(setup.setupNeeded(null, false), false); assert.equal(setup.setupNeeded(undefined, false), false);
  for (const bad of [null, {}, { required: true }, { ...fixtureSetup(), terms: { complete: false, documents: [{ id: 'terms' }] } },
    { ...fixtureSetup(), limits: { configured: false, enabled: false, revision: -1, unit: UNIT, suggested: null } }]) {
    assert.throws(() => setup.validateSetup(bad), undefined, JSON.stringify(bad));
  }
  assert.deepEqual(setup.validateSetup(fixtureSetup()), fixtureSetup());
});

test('the step keeps its layout as an information or a warning notice, and touch-sized buttons only under a coarse pointer', async () => {
  // Both variants are .notice regions: the readable step (info) and the step that could not be read (warn).
  const readable = await cardFixture();
  assert.equal(readable.tree().props.className, 'notice info ag-setup');
  readable.unmount();
  const unreadable = await cardFixture(null as any, { onRetryRead() {}, onDismiss() {} });
  assert.equal(unreadable.tree().props.className, 'notice warn ag-setup');
  unreadable.unmount();
  const css = readFileSync(new URL('../src/apps/agent/agent.css', import.meta.url), 'utf8');
  const rule = (selector: string) => css.match(new RegExp(`(?:^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{([^}]*)\\}`))?.[1] ?? '';
  // The layout outranks the shared .notice (any stylesheet order) and holds for both variants;
  // only the information variant turns its body text to the text colour.
  assert.match(rule('.notice.ag-setup'), /display: grid/);
  assert.match(rule('.notice.ag-setup'), /padding: var\(--sp-3\) var\(--sp-4\)/);
  assert.doesNotMatch(rule('.notice.ag-setup'), /color:/);
  assert.match(rule('.notice.info.ag-setup'), /color: var\(--text\)/);
  // design.md: 44 px targets under a coarse pointer, the usual button under a mouse.
  for (const selector of ['.ag-setup-actions .btn', '.ag-price-notice .btn']) assert.doesNotMatch(rule(selector), /min-height/, selector);
  assert.match(css, /@media \(pointer: coarse\) \{ \.ag-price-notice \.btn \{ min-height: 44px; \} \}/);
  // A wrapped legend is the item's first row, not a break in its border.
  assert.match(rule('.ag-setup-item > legend:not(.sr-only)'), /float: left; width: 100%/);
  const reduced = css.indexOf('@media (prefers-reduced-motion: reduce) {');
  assert.ok(reduced >= 0 && css.indexOf('  .notice.ag-setup, .ag-price-notice { animation: none; }', reduced) > reduced);
});

test('W4: a step with nothing left to record never offers an "Agree and send" that writes nothing: it is a plain re-check', async () => {
  let rechecks = 0;
  const done = fixtureSetup({ complete: true, provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: true, accepted_version: '2026-10-05' },
    terms: { complete: true, documents: documents(true) }, limits: { ...fixtureSetup().limits, configured: true, enabled: true } });
  const h = await cardFixture(done, { onRetryRead: () => { rechecks += 1; }, onDismiss: () => {} });
  assert.equal(h.checkboxes().length, 0);
  const buttons = h.elements().filter((el) => el.type === 'button');
  const labels = buttons.map((el) => strings(el.props.children));
  assert.ok(!labels.includes('Agree and send') && !labels.includes('Agree and start'), labels.join());
  assert.ok(!buttons.some((el) => el.props.type === 'submit'), 'nothing to submit');
  assert.ok(h.text().includes(core.translate('en', copy.chatCopy.setupNothing[0])), h.text());
  assert.ok(h.text().includes(core.translate('en', copy.chatCopy.setupNothingDraft[0])), 'the message waits in the composer');
  const recheck = buttons.find((el) => strings(el.props.children) === core.translate('en', copy.chatCopy.setupRecheck[0]));
  assert.ok(recheck, labels.join());
  recheck.props.onClick(); await settle(); h.render();
  assert.equal(rechecks, 1); assert.deepEqual(h.writes, []); assert.equal(h.completed(), 0, 'nothing is sent from here');
  for (const key of ['setupNothing', 'setupNothingDraft', 'setupRecheck']) {
    const [source, en, hant] = copy.chatCopy[key];
    assert.equal(core.translate('en', source), en, key); assert.equal(core.translate('zh-Hant', source), hant, key);
  }
  h.unmount();
});

test('W5: the "updated" wording appears only when the server reports an earlier accepted version; otherwise it is the first-use wording', async () => {
  const updated = core.translate('en', copy.chatCopy.setupTermsUpdated[0]), intro = core.translate('en', copy.chatCopy.setupIntro[0]);
  // First use: no document was ever accepted (S4: accepted_version null), or a server without the field.
  const fresh = documents(false).map((d) => ({ ...d, accepted_version: null }));
  for (const state of [fixtureSetup({ terms: { complete: false, documents: fresh } }), fixtureSetup()]) {
    const h = await cardFixture(state);
    assert.ok(h.text().includes(intro), 'the "only once" intro');
    assert.ok(!h.text().includes(updated), h.text());
    h.unmount();
  }
  // A new version of a document the person accepted before (FIXTURE: terms 2026-10-05 accepted, 2026-10-24 in force).
  const bumped = documents(true).map((d) => d.id === 'terms' ? { ...d, accepted: false, accepted_version: '2026-10-05' } : { ...d, accepted_version: d.version });
  const again = await cardFixture(fixtureSetup({ provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: true, accepted_version: '2026-10-05' },
    terms: { complete: false, documents: bumped }, limits: { ...fixtureSetup().limits, configured: true, enabled: true } }));
  assert.ok(again.text().includes(updated), again.text());
  assert.ok(again.elements().some((el) => el.props.role === 'status' && strings(el.props.children).includes(updated)));
  assert.ok(!again.text().includes(intro), 'no "you only do this once" for a re-acceptance');
  assert.equal(again.checkboxes().length, 1, 'only the documents are asked for');
  again.unmount();
  // A new version of the data-sharing notice someone agreed to before is a re-acceptance too: no "only once".
  const consentAgain = await cardFixture(fixtureSetup({ provider_consent: { provider: 'deepseek', version: '2026-11-01', accepted: false, accepted_version: '2026-10-05' },
    terms: { complete: true, documents: documents(true) }, limits: { ...fixtureSetup().limits, configured: true, enabled: true } }));
  assert.ok(!consentAgain.text().includes(intro), consentAgain.text());
  assert.ok(!consentAgain.text().includes(updated), 'the documents did not change');
  consentAgain.unmount();
  // The field is checked like every other one.
  assert.throws(() => setup.validateSetup(fixtureSetup({ terms: { complete: false, documents: documents(false).map((d) => ({ ...d, accepted_version: 5 })) } })));
  assert.doesNotThrow(() => setup.validateSetup(fixtureSetup({ terms: { complete: false, documents: bumped } })));
});

test('W5: the terms refusal reads neutrally (first use or a new version), in three languages', () => {
  const [zh, en, hant] = copy.chatCopy.refusedTerms;
  assert.equal(zh, '需要先接受当前版本的《服务条款》《AI 使用规范》与《定价》。消息没有发送，也没有扣费。');
  assert.equal(hant, '需要先接受目前版本的《服務條款》《AI 使用規範》與《定價》。訊息沒有傳送，也沒有扣費。');
  assert.equal(en, 'You need to accept the current version of the Terms of service, the AI use rules and the Pricing first. The message was not sent and nothing was charged.');
  assert.equal(core.translate('en', zh), en); assert.equal(core.translate('zh-Hant', zh), hant);
  assert.doesNotMatch(zh + en + hant, /已更新|updated/);
});

test('W6: the step names Agent settings as the place to withdraw the consent, and never "here" (it has no withdraw control)', async () => {
  const h = await cardFixture();
  const facts = h.find((el) => el.type === providerConsent.ProviderConsentFacts);
  const here = consentCopy.providerConsentCopy.withdrawNote;
  const render = (locale: string, element: any) => { ui.setLocale(locale); return renderToStaticMarkup(React.createElement(router.MemoryRouter, null, element)); };
  for (const [i, locale] of LOCALES.entries()) {
    const notice = render(locale, facts);
    assert.ok(!notice.includes(here[i]), `${locale}: no "withdraw here" in the step: ${notice}`);
    assert.doesNotMatch(notice, /在这里撤回|withdraw here|在這裡撤回/, locale);
  }
  const later = consentCopy.providerConsentCopy.withdrawNoteSettings;
  assert.deepEqual([...later], ['之后可以在 Agent 设置中撤回。', 'You can withdraw it later in Agent settings.', '之後可以在 Agent 設定中撤回。']);
  for (const [i, locale] of LOCALES.entries()) assert.ok(render(locale, facts).includes(later[i]), locale);
  ui.setLocale('zh-Hans');
  // The withdraw control lives only in Agent settings, which keeps its own sentence.
  const buttons = h.elements().filter((el) => el.type === 'button').map((el) => strings(el.props.children));
  assert.ok(!buttons.some((label) => /withdraw|撤回/i.test(label)), buttons.join());
  const settings = render('zh-Hans', React.createElement(providerConsent.ProviderConsentFacts, { facts: consentFacts, siteUrl: 'https://site.fixture.test' }));
  assert.ok(settings.includes(here[0]) && !settings.includes(later[0]), settings);
  assert.equal(core.translate('en', later[0]), later[1]); assert.equal(core.translate('zh-Hant', later[0]), later[2]);
  h.unmount();
});
