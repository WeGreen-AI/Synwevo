/** Local component/contract validation. Fixtures are isolated; no API or user-data access.
 * Run from the repository root: node services/identity/web/src/pages/org/flow.validation.mjs
 */
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
const root = fileURLToPath(new URL('../../../', import.meta.url));
const storage = new Map();
// Explicit browser-environment fixture, not a mock account or mock API.
const location = new URL('http://127.0.0.1/verify-email#token=fixture-verification-token');
globalThis.window = { location, history: { state: null, replaceState(_state, _unused, url) { location.href = new URL(url, location).href; } } };
globalThis.sessionStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
const server = await createServer({ root, configFile: false, server: { middlewareMode: true, hmr: false, ws: false, watch: null }, appType: 'custom' });
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }
try {
  const ui = await server.ssrLoadModule('@synwevo/ui');
  const auth = await server.ssrLoadModule('/src/auth-flow.messages.ts');
  const org = await server.ssrLoadModule('/src/organization.messages.ts');
  const orgBilling = await server.ssrLoadModule('/src/org-billing.messages.ts');
  const billingView = await server.ssrLoadModule('/src/pages/org/OrganizationBilling.tsx');
  const flow = await server.ssrLoadModule('/src/pages/org/AuthFlow.tsx');
  const creation = await server.ssrLoadModule('/src/pages/org/OrganizationCreate.tsx');
  const verify = await server.ssrLoadModule('/src/pages/VerifyEmail.tsx');
  const workspaceView = await server.ssrLoadModule('/src/pages/org/OrganizationWorkspaces.tsx');
  const avatarView = await server.ssrLoadModule('/src/pages/org/OrganizationAvatar.tsx');
  const client = await server.ssrLoadModule('@synwevo/auth-client');
  const catalogs = { ...auth.messages, ...org.messages, ...orgBilling.messages };
  check('catalogs contain both translations and preserve every parameter', () => assert.deepEqual(ui.validateMessages(catalogs), []));
  check('all three locales interpolate user data without interpreting markup', () => {
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      const message = ui.translate(locale, '你正在接受「{workspace}」的邀请。', { workspace: '<script>{untrusted}</script>' });
      assert.ok(message.includes('<script>{untrusted}</script>')); assert.ok(!message.includes('{workspace}'));
    }
  });
  const sourceNames = ['Login.tsx', 'Register.tsx', 'Invite.tsx', 'Recover.tsx', 'VerifyEmail.tsx', 'Onboarding.tsx', 'Organizations.tsx'];
  const orgNames = (await readdir(new URL('./', import.meta.url))).filter(name => /\.(tsx|ts)$/.test(name));
  const files = [...sourceNames.map(name => new URL(`../${name}`, import.meta.url)), ...orgNames.map(name => new URL(name, import.meta.url))];
  const allowed = new Set(['口令至少需要', '口令过长', '请重新登录']); // Password validator recognizes these prefixes.
  const missing = [];
  for (const file of files) {
    const source = await readFile(file, 'utf8');
    for (const [, text] of source.matchAll(/"([^"\n]*[\u3400-\u9fff][^"\n]*)"/g)) if (!catalogs[text] && !allowed.has(text)) missing.push(`${file.pathname}: ${text}`);
  }
  check('every local source message has English and Traditional Chinese wording', () => assert.deepEqual(missing, []));
  check('pending or unverified accounts cannot continue to a business route', () => {
    for (const user of [{ status: 'pending' }, { email_verified: false }]) assert.equal(flow.signedInDestination(user, '/projects'), '/verify-email');
    assert.equal(flow.signedInDestination({ email_verified: true, onboarding_completed: false }, '/projects'), '/onboarding');
  });
  check('invitation continuation uses a fragment and preserves onboarding intent', () => {
    flow.saveIntent({ invite_token: 'fixture-invite', intent: 'organization', email: 'fixture@example.test' });
    assert.equal(flow.continuation(), '/invite#token=fixture-invite');
    assert.equal(flow.readIntent().intent, 'organization');
    flow.clearInvitation(); assert.equal(flow.readIntent().invite_token, undefined);
  });
  check('continuation rejects external redirect targets', () => {
    flow.saveIntent({ next: 'https://external.example.test/redirect' }); assert.equal(flow.continuation(), '/');
  });
  check('portal entry binds the selected workspace and each locale to SSO on the configured host', () => {
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      const url = new URL(flow.portalWorkspaceUrl('http://portal.localhost:29769/?next=//outside.invalid#old', 'fixture/space?&lang=other', locale));
      assert.equal(url.origin, 'http://portal.localhost:29769');
      assert.equal(url.pathname, '/auth/start'); assert.equal(url.hash, '');
      assert.deepEqual([...url.searchParams], [['next', '/workspace'], ['workspace_id', 'fixture/space?&lang=other'], ['lang', locale]]);
    }
    assert.equal(flow.portalWorkspaceUrl(undefined, 'fixture-space', 'en'), '/account');
    assert.throws(() => flow.portalWorkspaceUrl('/untrusted-relative-config', 'fixture-space', 'en'));
  });
  check('organization retries reuse a key only for the identical payload', () => {
    const first = creation.creationAttempt(null, 'Fixture organization', 'Fixture workspace');
    assert.equal(creation.creationAttempt(first, first.name, first.workspace_name).key, first.key);
    assert.notEqual(creation.creationAttempt(first, 'Another fixture organization', first.workspace_name).key, first.key);
  });
  check('password policy validates before a reset token is submitted', () => {
    assert.throws(() => flow.checkPassword('short'), /15/);
    assert.throws(() => flow.checkPassword('synwevo very long passphrase'), /服务/);
    assert.doesNotThrow(() => flow.checkPassword('lunar rivers carry quiet lanterns'));
  });
  check('password input has a separate label, hint, and non-submit visibility control', () => {
    const html = renderToStaticMarkup(React.createElement(flow.PasswordField, { value: '', onChange() {} }));
    const id = html.match(/<input id="([^"]+)"/)[1];
    assert.ok(html.includes(`for="${id}"`)); assert.ok(html.includes(`aria-describedby="${id}-hint"`));
    assert.ok(html.includes('type="button"')); assert.ok(html.includes('aria-pressed="false"')); assert.ok(html.includes('autoComplete="new-password"'));
  });
  check('verification page renders an explicit button, language selector and resend form', () => {
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(verify.default)));
    assert.match(html, /type="button"[^>]*>验证此邮箱/); assert.match(html, /<form/); assert.match(html, /type="email"/);
    assert.match(html, /lang="zh-Hant"/); assert.match(html, /lang="en"/);
    assert.ok(!html.includes('fixture-verification-token')); // The bearer token is not rendered in markup.
  });
  const css = await readFile(new URL('./flow.css', import.meta.url), 'utf8');
  const sharedCss = await readFile(new URL('../../../../../../packages/ui/src/styles.css', import.meta.url), 'utf8');
  check('legacy query and fragment verification links both present explicit confirmation', () => {
    location.hash = ''; location.search = '?token=fixture-legacy-token';
    const html = renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(verify.default)));
    assert.match(html, /type="button"[^>]*>验证此邮箱/); assert.ok(!html.includes('fixture-legacy-token'));
  });
  check('email-change bridge keeps fragment and legacy capabilities out of the login return address', () => {
    for (const suffix of ['?lang=en#token=fixture-email-change-capability&action=email-change', '?token=fixture-email-change-capability&action=email-change&lang=en']) {
      location.href = 'http://127.0.0.1/account' + suffix;
      flow.preserveEmailChangeForSignIn();
      const saved = JSON.parse(storage.get(flow.EMAIL_CHANGE_BRIDGE_KEY));
      assert.equal(saved.token, 'fixture-email-change-capability');
      assert.ok(Math.abs(Date.now() - saved.captured_at) < 1000);
      assert.equal(location.pathname + location.search + location.hash, '/account?lang=en');
    }
  });
  check('invalid or unpersistable email-change capabilities cannot become a login query', () => {
    location.href = 'http://127.0.0.1/account?token=short&action=email-change';
    assert.throws(() => flow.preserveEmailChangeForSignIn());
    assert.equal(location.pathname + location.search + location.hash, '/account');
    assert.equal(storage.has(flow.EMAIL_CHANGE_BRIDGE_KEY), false);
    const setItem = sessionStorage.setItem;
    try {
      sessionStorage.setItem = () => { throw new Error('Fixture storage unavailable'); };
      location.href = 'http://127.0.0.1/account?token=fixture-email-change-capability&action=email-change';
      assert.throws(() => flow.preserveEmailChangeForSignIn(), /无法保存/);
      assert.equal(location.pathname + location.search + location.hash, '/account');
    } finally { sessionStorage.setItem = setItem; }
  });
  check('ordinary organization members see no workspace administration controls', () => {
    const organization = { id: 'fixture-org', role: 'member', status: 'active', invitation_policy: 'admins' };
    const html = renderToStaticMarkup(React.createElement(workspaceView.default, { organization, workspaces: [], onChanged() {} }));
    assert.ok(!html.includes('创建工作空间')); assert.ok(!html.includes('纳入已有工作空间')); assert.ok(!html.includes('成员与邀请'));
  });
  check('avatar preflight rejects malformed, oversized and over-dimension images', () => {
    const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC', 'base64');
    assert.deepEqual(avatarView.avatarDimensions(png), { width: 1, height: 1 });
    assert.throws(() => avatarView.avatarDimensions(Buffer.from('<svg/>')), /PNG/);
    const tooWide = Buffer.from(png); tooWide.writeUInt32BE(513, 16);
    assert.throws(() => avatarView.avatarDimensions(tooWide), /PNG/);
    assert.throws(() => avatarView.avatarDimensions(Buffer.concat([png, Buffer.alloc(256 * 1024)])), /PNG/);
  });
  check('avatar is readable to members while only active managers see mutations', () => {
    const organization = { id: 'fixture-org', name: 'Fixture organization', role: 'member', status: 'active', has_avatar: true };
    const render = organization => renderToStaticMarkup(React.createElement(avatarView.default, { organization, onChanged() {} }));
    const member = render(organization);
    assert.match(member, /<img/); assert.ok(!member.includes('type="file"')); assert.ok(!member.includes('移除组织头像'));
    assert.match(render({ ...organization, role: 'admin' }), /type="file"/);
    assert.ok(!render({ ...organization, role: 'owner', status: 'frozen' }).includes('type="file"'));
  });
  check('organization payments are shown only to holders of org.billing.manage', () => {
    const organization = { id: 'fixture-org', name: 'Fixture organization', role: 'admin', status: 'active' };
    const render = mine => renderToStaticMarkup(React.createElement(billingView.default, { organization, mine }));
    assert.equal(render(['org.members.view', 'org.settings.manage']), '');
    const manager = render(['org.billing.manage']);
    assert.match(manager, /<section[^>]*aria-labelledby=/); assert.ok(manager.includes('组织付费'));
    assert.ok(!manager.includes('前往支付')); // no checkout control before the server has priced the options
  });
  check('MFA errors preserve remaining attempts in all three languages', () => {
    const error = new client.ApiError(401, 'bad_code', '验证码不正确，还可尝试 2 次');
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      const text = flow.errorText(error, (source, params) => ui.translate(locale, source, params));
      assert.ok(text.includes('2'), `${locale}: ${text}`); assert.ok(!text.includes('{count}'));
    }
  });
  check('reduced motion comes from the shared design system; narrow layouts have explicit scoped rules', () => {
    assert.match(sharedCss, /prefers-reduced-motion: reduce/); assert.doesNotMatch(css, /prefers-reduced-motion/); assert.match(css, /max-width:760px/);
  });
  console.log(`${checks} component/contract validations passed.`);
} finally { await server.close(); }
