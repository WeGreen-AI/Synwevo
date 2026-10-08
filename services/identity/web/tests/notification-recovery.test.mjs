/** Isolated Node fixtures. No browser, backend, real notification, session or delivery.
 * Exercise the component's handlers with the real useLoad and ActionDialog; the small hook
 * runner supplies state/effect lifetimes without installing a DOM or opening a Vite server.
 */
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const src = new URL('../src/', import.meta.url);
const source = path => readFile(new URL(path, src), 'utf8');
const moduleURL = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const runtime = moduleURL(`
import * as React from ${JSON.stringify(import.meta.resolve('react'))};
export const fixture = { messages: {}, locale: 'zh-Hans', reads: [], writes: [], scopes: [], get: null, post: null };
export const registerMessages = entries => Object.assign(fixture.messages, entries);
export const t = (key, args = {}) => (fixture.locale === 'zh-Hans' ? key : fixture.messages[key]?.[fixture.locale] || key)
  .replace(/\\{([^}]+)\\}/g, (_, name) => String(args[name] ?? name));
export const useI18n = () => ({ locale: fixture.locale, t });
export const fmtTime = seconds => new Date(seconds * 1000).toISOString();
export const fmtDuration = seconds => String(seconds);
export class ApiError extends Error {
  constructor(status, code, message = '', detail, upstream = false) {
    super(message); Object.assign(this, { status, code, originalMessage: message, detail, upstream });
  }
}
export const errText = err => t(err.originalMessage || err.code || '无法连接服务，请检查连接后重试。');
export const isAbort = err => err?.name === 'AbortError';
export const get = (path, signal) => { fixture.reads.push({ path, signal }); return fixture.get(path, signal); };
export const post = (path, body) => { fixture.writes.push({ path, body: structuredClone(body) }); return fixture.post(path, body); };
export const useSession = () => ({ me: { email: 'fixture-account@example.invalid' } });
export const useReauthMethods = scope => { fixture.scopes.push(scope); return { methods: ['password'], reload() {} }; };
export class ReauthClientError extends Error {}
export const reauthenticateWithPasskey = () => { throw new Error('No browser ceremony in Node fixtures'); };
export const Modal = props => React.createElement('section', { role: props.role }, props.children, props.footer);
let active = null;
const same = (a, b) => a && b && a.length === b.length && a.every((v, i) => Object.is(v, b[i]));
export function withHooks(h, run) { const prev = active; active = h; h.index = 0; try { return run(); } finally { active = prev; } }
export function useState(initial) {
  if (!active) return React.useState(initial);
  const h = active, i = h.index++;
  if (!(i in h.slots)) h.slots[i] = typeof initial === 'function' ? initial() : initial;
  return [h.slots[i], value => { const next = typeof value === 'function' ? value(h.slots[i]) : value;
    if (!Object.is(next, h.slots[i])) { h.slots[i] = next; h.dirty = true; } }];
}
export function useRef(initial) { if (!active) return React.useRef(initial); return useState(() => ({ current: initial }))[0]; }
export function useId() { if (!active) return React.useId(); return useRef('fixture-' + active.index).current; }
export function useEffect(run, deps) {
  if (!active) return React.useEffect(run, deps);
  const h = active, i = h.index++, previous = h.slots[i];
  if (!previous || !same(previous.deps, deps)) { h.slots[i] = { deps, cleanup: previous?.cleanup };
    h.effects.push(() => { h.slots[i].cleanup?.(); h.slots[i].cleanup = run(); }); }
}
export function useCallback(fn, deps) {
  if (!active) return React.useCallback(fn, deps);
  const h = active, i = h.index++, previous = h.slots[i];
  if (!previous || !same(previous.deps, deps)) h.slots[i] = { deps, fn };
  return h.slots[i].fn;
}
`);
const support = await import(runtime);
const { fixture, ApiError } = support;
async function compile(path, imports = {}) {
  const code = (await transformWithOxc(await source(path), fileURLToPath(new URL(path, src)), { jsx: { runtime: 'automatic' } })).code;
  const mappings = { react: runtime, 'react/jsx-runtime': import.meta.resolve('react/jsx-runtime'),
    '@synwevo/ui': runtime, '@synwevo/auth-client': runtime, './labels': runtime, '../lib/reauth': runtime, ...imports };
  return moduleURL(code.replace(/(from\s*|import\s*)(['"])([^'"]+)\2/g,
    (match, prefix, _quote, name) => mappings[name] ? `${prefix}${JSON.stringify(mappings[name])}` : match));
}
const sharedMessages = await compile('governance/messages.ts');
const dialogURL = await compile('governance/ActionDialog.tsx', { './messages': sharedMessages });
const hooksURL = await compile('lib/hooks.ts');
const accessURL = await compile('governance/access.ts');
const messagesURL = await compile('governance/notification-recovery.messages.ts');
const component = await import(await compile('governance/NotificationRecovery.tsx', {
  './ActionDialog': dialogURL, '../lib/hooks': hooksURL, './access': accessURL, './notification-recovery.messages': messagesURL,
}));
const { ActionDialog } = await import(dialogURL);

globalThis.window = { location: { pathname: '/admin/mail', search: '' }, addEventListener() {}, removeEventListener() {} };
const VIEW = ['platform.mail.view'];
const MANAGE = [...VIEW, 'platform.mail.manage'];
const row = overrides => ({ notification_id: 'n_fixture', user_id: 'u_fixture', category: 'platform', status: 'failed',
  attempts: 5, updated_at: 1790000000, error_code: 'notification_enqueue_failed', recoverable: true, blocked_reason: null, ...overrides });
const page = (overrides = {}) => ({ items: [row()], total: 1, limit: 25, offset: 0, snapshot: 100, next_offset: null, ...overrides });
const preview = (overrides = {}) => ({ action: 'admin.notification.recover_enqueue', scope: 'platform', risk: 'high',
  target: row(), expires_at: Date.now() / 1000 + 60, preview_token: 'fixture-signed-preview', ...overrides });
const receipt = (overrides = {}) => ({ ok: true, state: 'succeeded', operation_id: 'ao_fixture', notification_id: 'n_fixture',
  status: 'pending', attempts: 5, recovery_scheduled: true, ...overrides });
function nodes(tree) {
  return React.Children.toArray(tree).flatMap(node => React.isValidElement(node) ? [node, ...nodes(node.props.children)] : []);
}
const byText = (tree, label) => nodes(tree).find(node => node.type === 'button' && node.props.children === label);
const markup = tree => renderToStaticMarkup(tree);
const preventDefault = () => {};
const mounted = new Set();
function hooks(render) {
  const h = { slots: [], effects: [], index: 0, dirty: true, tree: null,
    draw() { this.dirty = false; this.tree = support.withHooks(this, render); this.effects.splice(0).forEach(run => run()); return this.tree; },
    dispose() { this.slots.forEach(slot => slot?.cleanup?.()); mounted.delete(this); } };
  mounted.add(h); return h;
}
afterEach(() => { for (const h of mounted) h.dispose(); });
async function screen(permissions = MANAGE, data = page()) {
  Object.assign(fixture, { reads: [], writes: [], scopes: [], locale: 'zh-Hans',
    get: async () => data, post: async path => path.endsWith('/preview') ? preview() : receipt() });
  let denied = 0;
  const props = { held: new Set(permissions), onDenied: () => denied++ };
  const root = hooks(() => {
    const element = component.NotificationRecovery(props);
    return typeof element.type === 'function' ? element.type(element.props) : element;
  });
  let dialog = null;
  const view = { props, root, get denied() { return denied; },
    get tree() { return root.tree; },
    get dialogProps() { return nodes(root.tree).find(node => node.type === ActionDialog)?.props; },
    html() { return markup(root.tree); },
    async flush() {
      for (let i = 0; i < 6; i++) {
        root.draw();
        if (this.dialogProps) { dialog ??= hooks(() => ActionDialog(this.dialogProps)); dialog.draw(); }
        else if (dialog) { dialog.dispose(); dialog = null; }
        await new Promise(resolve => setImmediate(resolve));
      }
    },
    async click(label) { const button = byText(root.tree, label); assert.ok(button, label); assert.ok(!button.props.disabled, label); button.props.onClick(); await this.flush(); },
    async open() { await this.click('预览恢复影响'); },
    async confirm() { nodes(this.dialogProps.children).find(node => node.props.type === 'checkbox').props.onChange({ target: { checked: true } }); await this.flush(); },
    async reason(value) { nodes(dialog.tree.props.children).find(node => node.type === 'textarea').props.onChange({ target: { value } }); await this.flush(); },
    async submit() { await nodes(dialog.tree.props.children).find(node => node.type === 'form').props.onSubmit({ preventDefault }); await this.flush(); },
    async password(value) { nodes(dialog.tree.props.children).find(node => node.props.type === 'password').props.onChange({ target: { value } }); await this.flush(); },
  };
  await view.flush(); return view;
}

test('view/manage gates, all read-only reasons, and metadata privacy survive rendering', async () => {
  let view = await screen(['platform.mail.manage']);
  assert.equal(fixture.reads.length, 0);
  assert.match(view.html(), /没有查看/);
  const blocked = ['not_failed', 'mail_already_queued', 'historical_delivery', 'unverified_failure', 'retry_not_exhausted'];
  view = await screen(MANAGE, page({ items: blocked.map((reason, i) => row({ notification_id: `fixture-${i}`, recoverable: true, blocked_reason: reason,
    body: 'fixture-private-body', email: 'private@example.invalid', target: '/private/target' })) }));
  for (const button of nodes(view.tree).filter(node => node.type === 'button' && node.props.children === '预览恢复影响')) {
    assert.equal(button.props.disabled, true);
    button.props.onClick(); // A stale handler must enforce eligibility too.
  }
  await view.flush(); assert.equal(fixture.writes.length, 0);
  assert.doesNotMatch(view.html(), /fixture-private-body|private@example|private\/target/);
  assert.doesNotMatch(view.html(), /<dl/, 'the list must not repeat the stacked confirmation details in each row');
  const firstRow = nodes(view.tree).find(node => node.type === 'tbody').props.children[0][0];
  const cells = React.Children.toArray(firstRow.props.children);
  assert.equal(cells.length, 4);
  assert.deepEqual(cells.slice(1).map(cell => cell.props['data-label']), ['通知类别', '累计尝试次数', '恢复操作']);
  assert.equal(cells[3].props.className, 'act', 'shared phone card rules keep the action in normal document flow');
  assert.match(markup(cells[0]), /通知编号.*fixture-0.*用户编号.*u_fixture/s);
  assert.match(markup(cells[1]), /平台运行.*入队失败.*notification_enqueue_failed.*不再是入队失败/s);
  assert.match(markup(cells[2]), /累计尝试次数.*5.*更新时间.*<time dateTime=/s);
  const recoveryButton = nodes(cells[3]).find(node => node.type === 'button');
  for (const id of recoveryButton.props['aria-describedby'].split(' ')) assert.ok(nodes(firstRow).some(node => node.props.id === id));
  for (const reason of [/不再是入队失败/, /可能已经入队/, /历史投递/, /无法核实失败来源/, /自动重试尚未耗尽/]) assert.match(view.html(), reason);
  view = await screen(VIEW);
  assert.match(view.html(), /但不能恢复/);
  assert.equal(byText(view.tree, '预览恢复影响').props.disabled, true);
});

test('real ActionDialog requires review and 8–500 characters, reauth replays the frozen original request', async () => {
  const view = await screen(); await view.open();
  assert.equal(view.dialogProps.ready, false);
  assert.equal(view.dialogProps.reauthScope, 'admin');
  assert.match(markup(view.dialogProps.impact), /<dl/, 'confirmation retains the detailed metadata');
  assert.match(markup(view.dialogProps.impact), /n_fixture.*u_fixture.*累计尝试次数.*5/s);
  await view.reason('fixture reason'); await view.submit();
  assert.equal(fixture.writes.length, 1, 'preview only before the explicit checkbox');
  await view.confirm(); await view.reason('短'); await view.submit();
  assert.equal(fixture.writes.length, 1);
  await view.reason('x'.repeat(501)); await view.submit();
  assert.equal(fixture.writes.length, 1);
  await view.reason('  fixture recovery reason  ');
  fixture.post = async path => {
    if (path.endsWith('/execute') && fixture.writes.filter(x => x.path.endsWith('/execute')).length === 1)
      throw new ApiError(403, 'reauth_required', '', { scope: 'admin' });
    return path === '/api/auth/reauth' ? { ok: true } : receipt();
  };
  await view.submit();
  assert.ok(fixture.scopes.includes('admin'));
  assert.equal(view.dialogProps.reason, 'none', 'the attempted reason can no longer change');
  await view.password('fixture-password-not-a-real-credential'); await view.submit();
  const requests = fixture.writes.filter(x => x.path.endsWith('/execute'));
  assert.equal(requests.length, 2); assert.deepEqual(requests[0], requests[1]);
  assert.deepEqual(Object.keys(requests[0].body).sort(), ['idempotency_key', 'preview_token', 'reason']);
  assert.equal(requests[0].body.reason, 'fixture recovery reason');
  assert.match(requests[0].body.idempotency_key, /^[a-z0-9-]{36}$/);
  assert.match(view.html(), /已安排重新检查并排队，不代表已寄出/);
  assert.match(view.html(), /ao_fixture/);
});

test('unknown outcome retains token, reason and key across close, refresh, expiry and repeated refusal', async () => {
  const view = await screen(); await view.open(); await view.confirm(); await view.reason('fixture retry reason');
  fixture.post = async () => { throw new TypeError('fixture connection lost'); };
  await view.submit();
  const first = fixture.writes.at(-1);
  assert.match(view.html(), /恢复结果尚不确定/);
  assert.equal(byText(view.tree, '预览恢复影响').props.disabled, true);
  assert.equal(byText(view.dialogProps.children, '重新预览恢复影响'), undefined);
  view.dialogProps.onClose(); await view.flush(); await view.click('刷新通知列表');
  await view.click('核对并重试原请求'); await view.confirm();
  const realNow = Date.now; Date.now = () => realNow() + 120000;
  try {
    fixture.post = async () => { throw new ApiError(409, 'admin_preview_expired'); };
    await view.submit(); assert.deepEqual(fixture.writes.at(-1), first);
    assert.match(view.html(), /恢复结果尚不确定/);
    fixture.post = async () => receipt();
    await view.submit(); assert.deepEqual(fixture.writes.at(-1), first);
  } finally { Date.now = realNow; }
  assert.match(view.html(), /不代表已寄出/);
});

test('stale/expired/changed previews need a new review; a missing object stays inside the mail page', async () => {
  for (const code of ['admin_preview_stale', 'admin_preview_expired', 'notification_delivery_changed']) {
    const view = await screen(); await view.open(); await view.confirm(); await view.reason('fixture recovery reason');
    fixture.post = async () => { throw new ApiError(409, code); };
    await view.submit(); assert.equal(view.dialogProps.ready, false); assert.equal(view.denied, 0);
    assert.match(markup(view.dialogProps.children), /重新预览/);
    fixture.post = async () => preview({ preview_token: 'fixture-new-preview' });
    await view.click('重新预览恢复影响');
    assert.equal(view.dialogProps.ready, false, 'new preview requires a fresh checkbox');
    await view.confirm(); fixture.post = async () => receipt(); await view.submit();
    assert.notEqual(fixture.writes.filter(x => x.path.endsWith('/execute'))[0].body.idempotency_key, fixture.writes.at(-1).body.idempotency_key);
    assert.equal(fixture.writes.at(-1).body.preview_token, 'fixture-new-preview');
  }
  const view = await screen();
  fixture.post = async () => { throw new ApiError(404, 'notification_delivery_missing'); };
  await view.open();
  assert.equal(view.denied, 0); assert.equal(view.dialogProps.ready, false);
  assert.match(markup(view.dialogProps.children), /记录已不存在/);
  byText(view.dialogProps.children, '刷新通知列表').props.onClick(); await view.flush();
  assert.equal(view.dialogProps, undefined); assert.equal(view.denied, 0);
});

test('a failed preview can reload; denied permission cannot execute a retained confirmation', async () => {
  const view = await screen();
  fixture.post = async () => { throw new TypeError('fixture preview offline'); };
  await view.open(); assert.equal(view.dialogProps.confirmLabel, '读取恢复预览');
  fixture.post = async () => preview(); await view.submit(); await view.confirm(); await view.reason('fixture recovery reason');
  view.props.held = new Set(VIEW); await view.flush();
  assert.equal(view.dialogProps.ready, false);
  const count = fixture.writes.length; await view.submit(); assert.equal(fixture.writes.length, count);
  assert.match(markup(view.dialogProps.impact), /管理权限已变化/);
});

test('a pending execute cannot be submitted twice, and unmount during reauth never executes again', async () => {
  let view = await screen(); await view.open(); await view.confirm(); await view.reason('fixture slow request');
  let finish;
  fixture.post = () => new Promise(resolve => { finish = resolve; });
  const first = view.submit(); await view.flush(); await view.submit();
  assert.equal(fixture.writes.filter(x => x.path.endsWith('/execute')).length, 1);
  view.dialogProps.onClose(); await view.flush(); assert.ok(view.dialogProps, 'busy confirmation cannot be discarded');
  finish(receipt()); await first;
  view = await screen(); await view.open(); await view.confirm(); await view.reason('fixture authentication reason');
  fixture.post = async () => { throw new ApiError(403, 'reauth_required', '', { scope: 'admin' }); };
  await view.submit(); await view.password('fixture-password-not-a-real-credential');
  fixture.post = () => new Promise(resolve => { finish = resolve; });
  const proving = view.submit(); await view.flush();
  for (const h of [...mounted]) h.dispose();
  finish({ ok: true }); await proving;
  assert.equal(fixture.writes.filter(x => x.path.endsWith('/execute')).length, 1, 'unmounted reauth must not send a second execute');
});

test('search and refresh reset pagination; pages preserve the high-water mark and use next_offset', async () => {
  const view = await screen(MANAGE, page({ total: 26, next_offset: 25 }));
  fixture.get = async path => {
    const params = new URL(path, 'https://fixture.invalid').searchParams;
    return page({ offset: Number(params.get('offset')), total: 26, next_offset: null });
  };
  await view.click('下一页');
  let params = new URL(fixture.reads.at(-1).path, 'https://fixture.invalid').searchParams;
  assert.equal(params.get('snapshot'), '100'); assert.equal(params.get('offset'), '25'); assert.equal(params.get('limit'), '25');
  await view.click('上一页');
  assert.match(fixture.reads.at(-1).path, /offset=0&snapshot=100/);
  nodes(view.tree).find(node => node.props.type === 'search').props.onChange({ target: { value: '  n_fixture & u_fixture  ' } });
  await view.flush(); nodes(view.tree).find(node => node.type === 'form').props.onSubmit({ preventDefault }); await view.flush();
  params = new URL(fixture.reads.at(-1).path, 'https://fixture.invalid').searchParams;
  assert.equal(params.get('q'), 'n_fixture & u_fixture'); assert.equal(params.get('snapshot'), null); assert.equal(params.get('offset'), '0');
  fixture.get = async () => page({ items: [], total: 0 }); await view.click('刷新通知列表');
  assert.match(view.html(), /没有匹配的通知入队失败/);
  fixture.get = async () => { throw new TypeError('fixture offline'); }; await view.click('刷新通知列表');
  assert.match(view.html(), /role="alert"/); assert.equal(byText(view.tree, '下一页').props.disabled, true);
  fixture.get = async () => page(); await view.click('重试读取通知列表');
  assert.match(view.html(), /n_fixture/);
});

test('unverified responses never offer recovery or claim completion', async () => {
  const view = await screen(MANAGE, page({ snapshot: '100' }));
  assert.match(view.html(), /无法核实/); assert.equal(byText(view.tree, '预览恢复影响'), undefined);
  fixture.get = async () => page(); await view.click('重试读取通知列表');
  fixture.post = async () => preview({ target: row({ user_id: 'u_other' }) });
  await view.open(); assert.equal(view.dialogProps.confirmLabel, '读取恢复预览');
  fixture.post = async () => preview(); await view.submit(); await view.confirm(); await view.reason('fixture valid reason');
  fixture.post = async () => receipt({ attempts: 0 }); await view.submit();
  assert.match(view.html(), /恢复结果尚不确定/);
  assert.doesNotMatch(view.html(), /已安排重新检查并排队，不代表已寄出/);
});

test('list loading hides old actionable rows; aborted responses cannot replace a newer result', async () => {
  const view = await screen();
  let finish;
  fixture.get = () => new Promise(resolve => { finish = resolve; });
  await view.click('刷新通知列表');
  assert.match(view.html(), /正在读取通知入队失败/);
  assert.equal(byText(view.tree, '预览恢复影响'), undefined);
  const pending = fixture.reads.at(-1);
  // Submit a new query while the earlier read is still pending (e.g. form keyboard submission).
  nodes(view.tree).find(node => node.props.type === 'search').props.onChange({ target: { value: 'u_new_fixture' } });
  await view.flush(); fixture.get = async () => page({ items: [row({ user_id: 'u_new_fixture' })] });
  nodes(view.tree).find(node => node.type === 'form').props.onSubmit({ preventDefault }); await view.flush();
  assert.equal(pending.signal.aborted, true);
  finish(page({ items: [row({ user_id: 'u_obsolete_fixture' })] })); await view.flush();
  assert.match(view.html(), /u_new_fixture/); assert.doesNotMatch(view.html(), /u_obsolete_fixture/);
  fixture.get = async () => { throw new ApiError(404, 'notification_delivery_missing'); };
  await view.click('刷新通知列表'); assert.equal(view.denied, 0);
  assert.match(view.html(), /记录已不存在/);
});

test('business category names, messages and placeholders work in all three locales', async () => {
  const categoryNames = ['security', 'organization', 'members', 'approval', 'conversation', 'docs', 'automation', 'platform'];
  const view = await screen(VIEW, page({ items: categoryNames.map((category, i) => row({ category, notification_id: `n_fixture_${i}` })) }));
  const code = await source('governance/NotificationRecovery.tsx');
  const tokens = text => [...text.matchAll(/\{([^}]+)\}/g)].map(match => match[1]).sort();
  for (const [, key] of code.matchAll(/'([^'\n]*[\u3400-\u9fff][^'\n]*)'/g)) {
    for (const locale of ['en', 'zh-Hant']) {
      const translated = fixture.messages[key]?.[locale]; assert.ok(translated, `${locale}: ${key}`);
      assert.deepEqual(tokens(translated), tokens(key));
    }
  }
  for (const [locale, label] of [['zh-Hans', '账户安全'], ['zh-Hant', '帳戶安全'], ['en', 'Account security']]) {
    fixture.locale = locale; await view.flush(); const html = view.html();
    assert.ok(html.includes(label));
    for (const category of categoryNames) assert.ok(!html.includes(`>${category}<`), category);
    if (locale === 'en') assert.doesNotMatch(html, /[\u3400-\u9fff]/);
    assert.match(html, /class="list-wrap admin-cards-wrap" style="min-width:0;max-width:100%;overflow-wrap:anywhere"/);
    const table = nodes(view.tree).find(node => node.type === 'table');
    assert.equal(table.props.className, 'gov admin-cards-table');
    assert.equal(table.props.style?.minWidth, undefined, 'no inline width may override the shared phone card layout');
    assert.match(html, /class="pager wrap"/);
  }
});
