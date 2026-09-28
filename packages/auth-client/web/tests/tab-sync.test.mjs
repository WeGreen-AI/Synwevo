/** Run from the repository root:
 * node --test packages/auth-client/web/tests/tab-sync.test.mjs
 *
 * The real tabSync.ts, compiled by the repository's Rolldown; the other "tab" is a second
 * BroadcastChannel on the same name (Node implements the same API the browser does). */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { transformSync } from 'rolldown/utils';

const source = readFileSync(new URL('../src/tabSync.ts', import.meta.url), 'utf8');
const compiled = transformSync('tabSync.ts', source, { target: 'es2022' });
assert.equal(compiled.errors.length, 0);
const mod = await import('data:text/javascript,' + encodeURIComponent(compiled.code));
const turn = () => new Promise(resolve => setTimeout(resolve, 20));

test('a count posted in one tab reaches the other tabs of the origin, and nothing else does', async () => {
  const other = new BroadcastChannel(mod.MESSAGES_UNREAD);
  const got = [];
  other.onmessage = e => got.push(e.data);
  mod.broadcastCount(mod.MESSAGES_UNREAD, 3);
  mod.broadcastCount(mod.MESSAGES_UNREAD, -1);          // not a count
  mod.broadcastCount(mod.MESSAGES_UNREAD, 2.5);
  await turn();
  assert.deepEqual(got, [{ n: 3 }]);

  const heard = [];
  const off = mod.onBroadcastCount(mod.MESSAGES_UNREAD, n => heard.push(n));
  other.postMessage({ n: 7 });
  other.postMessage({ n: 'seven' });
  other.postMessage(null);
  other.postMessage({ n: 1e9 });
  await turn();
  assert.deepEqual(heard, [7]);
  off();
  other.postMessage({ n: 8 });
  await turn();
  assert.deepEqual(heard, [7], 'unsubscribed');

  const notifications = [];
  const offN = mod.onBroadcastCount(mod.NOTIFICATIONS_UNREAD, n => notifications.push(n));
  other.postMessage({ n: 4 });                          // the messages channel is not the notifications one
  await turn();
  assert.deepEqual(notifications, []);
  offN();
  other.close();
});
