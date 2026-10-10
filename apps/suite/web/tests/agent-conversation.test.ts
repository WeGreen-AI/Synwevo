/** Actual hook compiled by the locked toolchain. Hook scheduling, reads, SSE and timers are
 * explicit test doubles; browser verification is separate. No model or business write occurs. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import { transformSync } from 'rolldown/utils';

const compiled = transformSync('useConversation.ts', readFileSync(new URL('../src/apps/agent/useConversation.ts', import.meta.url), 'utf8'), { target: 'es2022' });
assert.equal(compiled.errors.length, 0);
const turn = () => new Promise<void>((resolve) => setImmediate(resolve));
const snapshot = (id: string, seq = 1) => ({ id, title: `Synthetic ${id}`, event_seq: seq, messages: [], head_id: null });

async function setup() {
  let index = 0, timerId = 0;
  const values: any[] = [], effects: any[] = [], pendingEffects: (() => void)[] = [], requests: any[] = [], streams: any[] = [];
  const timers = new Map<number, () => void>();
  const react = {
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = initial;
      return [values[i], (value: any) => { values[i] = typeof value === 'function' ? value(values[i]) : value; }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pendingEffects.push(() => {
        effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() };
      }); },
  };
  class Stream {
    url: string; closed = false; onopen?: () => void; onerror?: () => void;
    listeners = new Map<string, (e: any) => void>();
    constructor(url: string) { this.url = url; streams.push(this); }
    close() { this.closed = true; }
    addEventListener(name: string, fn: (e: any) => void) { this.listeners.set(name, fn); }
    emit(name: string, id = '1') { this.listeners.get(name)?.({ lastEventId: id }); }
  }
  const document = new EventTarget();
  Object.assign(document, { visibilityState: 'visible' });
  const context = vm.createContext({ AbortController, EventSource: Stream, document,
    setTimeout: (fn: () => void) => { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout: (id: number) => timers.delete(id),
    setInterval: () => ++timerId, clearInterval: () => {} });
  const synthetic = (name: string, members: Record<string, any>) => new vm.SyntheticModule(Object.keys(members), function () {
    for (const [key, value] of Object.entries(members)) this.setExport(key, value);
  }, { context, identifier: name });
  const modules: Record<string, any> = { react: synthetic('react', react), '../../shared/ops': synthetic('ops', {
    read: (operation: string, input: any, workspace: any, signal: AbortSignal) => new Promise((resolve, reject) => requests.push({ operation, input, workspace, signal, resolve, reject })),
  }) };
  const module = new vm.SourceTextModule(compiled.code, { context });
  await module.link((name) => { assert(modules[name], name); return modules[name]; }); await module.evaluate();
  const flush = () => { while (pendingEffects.length) pendingEffects.shift()!(); };
  const render = (id?: string, runEffects = true) => { index = 0; const out = (module.namespace as any).useConversation(id); if (runEffects) flush(); return out; };
  const answer = async (i: number, value: any) => { requests[i].resolve(value); await turn(); };
  const fail = async (i: number) => { requests[i].reject(new Error('Synthetic unavailable')); await turn(); };
  const start = async (id = 'A') => { render(id); await answer(0, snapshot(id)); return render(id); };
  return { render, flush, answer, fail, start, requests, streams, timers, document };
}

test('route changes never expose the previous conversation before effects run', async () => {
  const h = await setup(); await h.start();
  const next = h.render('B', false);
  assert.equal(next.value, undefined);
  assert.equal(next.error, undefined);
  assert.equal(next.connection, 'connecting');
  h.flush(); await h.answer(1, snapshot('B'));
  assert.equal(h.render('B').value.id, 'B');
  assert.equal(h.render(undefined, false).value, undefined);
});

test('explicit refresh waits for a read started after an existing request, coalescing concurrent refreshes', async () => {
  const h = await setup(); await h.start();
  const first = h.render('A').refresh(); // a periodic read already in flight
  let refreshed = false;
  const second = h.render('A').refresh().then(() => { refreshed = true; });
  const third = h.render('A').refresh();
  await turn(); assert.equal(refreshed, false);
  await h.answer(1, snapshot('A', 2)); await first;
  assert.equal(refreshed, false);
  assert.equal(h.requests.length, 3);
  await h.answer(2, snapshot('A', 3)); await Promise.all([second, third]);
  assert.equal(refreshed, true);
  assert.equal(h.render('A').value.event_seq, 3);
});

test('late events from a replaced stream cannot close its successor or mark it offline', async () => {
  const h = await setup(); await h.start();
  const old = h.streams[0]; old.emit('reconnect');
  const current = h.streams[1]; current.onopen();
  old.onerror(); old.emit('reconnect'); old.emit('text', '900');
  assert.equal(current.closed, false);
  assert.equal(h.streams.length, 2);
  assert.equal(h.render('A').connection, 'connected');
  assert.equal(h.timers.size, 0);
  await h.answer(1, snapshot('A', 2));
});

test('navigation aborts reads and ignores old errors and stream callbacks', async () => {
  const h = await setup(); await h.start();
  const old = h.streams[0];
  const pending = h.render('A').refresh(); h.render('B');
  assert.equal(h.requests[1].signal.aborted, true);
  await h.answer(2, snapshot('B')); h.streams[1].onopen();
  await h.fail(1); await pending; old.onopen(); old.onerror();
  assert.equal(h.render('B').value.id, 'B');
  assert.equal(h.render('B').error, undefined);
  assert.equal(h.render('B').connection, 'connected');
  assert.equal(h.requests.length, 4);
  await h.answer(3, snapshot('B', 2));
});

test('a current read failure hides business content until successful reauthorization', async () => {
  const h = await setup(); await h.start();
  const pending = h.render('A').refresh(); await h.fail(1); await pending;
  assert.equal(h.render('A').value, undefined); assert.ok(h.render('A').error);
  const retry = h.render('A').refresh(); await h.answer(2, snapshot('A', 2)); await retry;
  assert.equal(h.render('A').value.id, 'A'); assert.equal(h.render('A').error, undefined);
  assert.ok(h.requests.every((r) => r.operation === 'agent.conversation.get' && r.workspace === null));
});
