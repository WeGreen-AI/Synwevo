import { test } from "node:test";
import assert from "node:assert/strict";
import { createHistoryLoader, type HistoryPage } from "../src/apps/agent/history.ts";

function harness() {
  const requests: { input: unknown; signal: AbortSignal; resolve: (page: HistoryPage) => void; reject: (e: unknown) => void }[] = [];
  const loader = createHistoryLoader((input, signal) => new Promise((resolve, reject) => requests.push({ input, signal, resolve, reject })));
  return { loader, requests };
}
const page = (ids: string[], cursor: string | null = null): HistoryPage => ({
  conversations: ids.map((id) => ({ id, title: id })), next_cursor: cursor,
});

test("changing search aborts old pagination and ignores its late failure", async () => {
  const { loader, requests } = harness();
  const first = loader.reset({ status: "active", q: "" })();
  requests[0].resolve(page(["a"], "cursor-a")); await first;
  const old = loader.more();
  const fresh = loader.reset({ status: "archived", q: "new" })();
  assert.equal(requests[1].signal.aborted, true);
  assert.deepEqual(loader.getSnapshot(), { cursor: null, loadingMore: false });
  requests[2].resolve(page(["new"])); await fresh;
  requests[1].reject(new Error("old network failure")); await old;
  assert.deepEqual(loader.getSnapshot().rows, page(["new"]).conversations);
  assert.equal(loader.getSnapshot().error, undefined);
});

test("late first-page success and old debounce callback cannot replace the new search", async () => {
  const { loader, requests } = harness();
  const oldLoad = loader.reset({ status: "active", q: "old" });
  const old = oldLoad();
  const fresh = loader.reset({ status: "active", q: "new" })();
  await oldLoad(); assert.equal(requests.length, 2);
  requests[1].resolve(page(["new"])); await fresh;
  requests[0].resolve(page(["old"], "old-cursor")); await old;
  assert.deepEqual(loader.getSnapshot().rows, page(["new"]).conversations);
  assert.equal(loader.getSnapshot().cursor, null);
});

test("load more admits one request, deduplicates moving rows and retains history on failure", async () => {
  const { loader, requests } = harness();
  const first = loader.reset({ status: "active", q: "" })();
  requests[0].resolve(page(["a", "b"], "cursor-1")); await first;
  const more = loader.more(); await loader.more();
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].input, { status: "active", q: "", cursor: "cursor-1" });
  requests[1].resolve(page(["b", "c"], "cursor-2")); await more;
  assert.deepEqual(loader.getSnapshot().rows?.map((x) => x.id), ["a", "b", "c"]);
  const fail = loader.more(); requests[2].reject(new Error("offline")); await fail;
  assert.equal(loader.getSnapshot().rows?.length, 3);
  assert.equal(loader.getSnapshot().cursor, "cursor-2");
  assert.equal(loader.getSnapshot().loadingMore, false);
  const retry = loader.more(); requests[3].resolve(page(["d"])); await retry;
  assert.equal(loader.getSnapshot().error, undefined);
  await loader.more(); assert.equal(requests.length, 4);
});

test("unmount ignores completion even if transport disregards abort", async () => {
  const { loader, requests } = harness();
  let updates = 0; loader.subscribe(() => { ++updates; });
  const pending = loader.reset({ status: "active", q: "" })();
  loader.cancel(); requests[0].resolve(page(["late"])); await pending;
  assert.equal(updates, 1);
  assert.equal(loader.getSnapshot().rows, undefined);
});
