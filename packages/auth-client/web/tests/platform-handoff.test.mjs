/** Run from the repository root:
 * node --experimental-vm-modules --test packages/auth-client/web/tests/platform-handoff.test.mjs
 *
 * The actual handoff.ts, compiled by the repository's locked toolchain. Pure URL building: where
 * a sign-in with no return target sends the browser, and what it refuses to build.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';

const source = readFileSync(new URL('../src/handoff.ts', import.meta.url), 'utf8');
const compiled = transformSync('handoff.ts', source, { target: 'es2022' });
assert.equal(compiled.errors.length, 0, 'handoff.ts compiles');
const context = vm.createContext({ URL, URLSearchParams });
const module = new vm.SourceTextModule(compiled.code, { context });
await module.link(() => { throw new Error('handoff.ts must not import anything'); });
await module.evaluate();
const { platformHandoffUrl, isDefaultNext } = module.namespace;

test('a configured portal is entered through its own sign-in start, workspace first', () => {
  assert.equal(platformHandoffUrl('https://synwevo.example', { lang: 'zh-Hans' }),
    'https://synwevo.example/auth/start?next=%2Fworkspace&lang=zh-Hans');
  assert.equal(platformHandoffUrl('http://portal.localhost:31769/', { next: '/workspace', workspaceId: 'w_1', lang: 'en' }),
    'http://portal.localhost:31769/auth/start?next=%2Fworkspace&workspace_id=w_1&lang=en');
});

test('the portal origin replaces any path the configuration carried', () => {
  assert.equal(platformHandoffUrl('https://synwevo.example/some/where', {}),
    'https://synwevo.example/auth/start?next=%2Fworkspace');
});

test('no portal, no handoff: the caller keeps its local fallback', () => {
  assert.equal(platformHandoffUrl(undefined), null);
  assert.equal(platformHandoffUrl(null), null);
  assert.equal(platformHandoffUrl('   '), null);
});

test('a configured value that is not an absolute http(s) origin is refused, not guessed at', () => {
  assert.throws(() => platformHandoffUrl('/untrusted-relative-config'));
  assert.throws(() => platformHandoffUrl('not a url'));
  assert.throws(() => platformHandoffUrl('javascript:alert(1)'));   // the URL parser refuses it before the scheme check
  assert.throws(() => platformHandoffUrl('ftp://portal.example'), /http/);
});

test('"/" and nothing are the default next; a real page is not', () => {
  assert.equal(isDefaultNext('/'), true);
  assert.equal(isDefaultNext(''), true);
  assert.equal(isDefaultNext(null), true);
  assert.equal(isDefaultNext('/account'), false);
  assert.equal(isDefaultNext('/oidc/authorize?client_id=x'), false);
});
