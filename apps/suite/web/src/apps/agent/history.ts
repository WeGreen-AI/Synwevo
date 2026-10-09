import type { Conversation } from "./types";

export type HistoryItem = Pick<Conversation, "id" | "title">;
export type HistoryPage = { conversations: HistoryItem[]; next_cursor: string | null };
type Filter = { status: "active" | "archived"; q: string };
type Snapshot = { rows?: HistoryItem[]; cursor: string | null; loadingMore: boolean; error?: unknown };
type ReadPage = (input: Filter & { cursor?: string }, signal: AbortSignal) => Promise<HistoryPage>;

/** One search owns its requests. Aborting is advisory: late responses are also fenced. */
export function createHistoryLoader(read: ReadPage) {
  let state: Snapshot = { cursor: null, loadingMore: false };
  let generation = 0;
  let filter: Filter;
  let abort: AbortController | undefined;
  const listeners = new Set<() => void>();
  function publish(next: Snapshot) { state = next; listeners.forEach((listen) => listen()); }
  async function load(run: number, append: boolean) {
    if (run !== generation || !abort) return;
    const signal = abort.signal;
    const cursor = append ? state.cursor : null;
    if (append && (!state.rows || !cursor || state.loadingMore)) return;
    if (append) publish({ ...state, loadingMore: true, error: undefined });
    try {
      const page = await read({ ...filter, ...(cursor ? { cursor } : {}) }, signal);
      if (run !== generation || signal.aborted) return;
      // Concurrent changes make this a moving view. Never render a conversation twice.
      const rows = new Map((append ? state.rows ?? [] : []).map((row) => [row.id, row]));
      page.conversations.forEach((row) => rows.set(row.id, row));
      publish({ rows: [...rows.values()], cursor: page.next_cursor, loadingMore: false });
    } catch (error) {
      if (run === generation && !signal.aborted) publish({ ...state, error, loadingMore: false });
    }
  }
  return {
    getSnapshot: () => state,
    subscribe: (listen: () => void) => { listeners.add(listen); return () => { listeners.delete(listen); }; },
    reset(next: Filter) {
      abort?.abort(); abort = new AbortController(); filter = { ...next };
      const run = ++generation;
      publish({ cursor: null, loadingMore: false });
      return () => load(run, false);
    },
    more: () => load(generation, true),
    cancel() { ++generation; abort?.abort(); },
  };
}
