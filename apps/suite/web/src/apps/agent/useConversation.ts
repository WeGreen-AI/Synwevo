import { useCallback, useEffect, useRef, useState } from "react";
import { read } from "../../shared/ops";
import type { Conversation } from "./types";

/** Events only invalidate an authorized snapshot. Reconnection never submits a turn or a tool. */
export function useConversation(id: string | undefined) {
  const [snapshot, setSnapshot] = useState<{ id?: string; value?: Conversation; error?: unknown; connection: string }>({ connection: "connecting" });
  const refreshRef = useRef<{ id?: string; load: () => Promise<void> }>({ load: async () => undefined });
  // A late action from another page must not refresh whichever conversation is now open.
  const refresh = useCallback(() => refreshRef.current.id === id ? refreshRef.current.load() : Promise.resolve(), [id]);
  useEffect(() => {
    setSnapshot({ id, connection: "connecting" });
    if (!id) { refreshRef.current = { load: async () => undefined }; return; }
    let disposed = false, seq = 0, busy = false;
    let pending: { done: Promise<void>; resolve: () => void } | undefined;
    let source: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    async function load() {
      if (disposed) return;
      if (busy) {
        // All refreshes requested during this read share one subsequent read. Awaiters
        // finish after that snapshot, not immediately and not after an entire token stream.
        if (!pending) {
          let resolve!: () => void;
          const done = new Promise<void>((finish) => { resolve = finish; });
          pending = { done, resolve };
        }
        return pending.done;
      }
      busy = true;
      try {
        const out = await read<Conversation>("agent.conversation.get", { conversation_id: id }, null, abort.signal);
        if (!disposed) {
          seq = Math.max(seq, out.event_seq);
          setSnapshot((old) => ({ ...old, id, value: out, error: undefined }));
        }
      } catch (e) {
        if (!disposed && !abort.signal.aborted) setSnapshot((old) => ({ ...old, id, error: e, value: undefined }));
      } finally {
        busy = false;
        const next = pending; pending = undefined;
        if (next) void load().then(next.resolve);
      }
    }
    refreshRef.current = { id, load };
    function invalidate() {
      // Throttle instead of debouncing: a continuous token stream must still render.
      if (timer) return;
      timer = setTimeout(() => { timer = undefined; void load(); }, 150);
    }
    function connect() {
      if (disposed) return;
      clearTimeout(reconnect);
      const current = new EventSource(`/api/suite/agent/conversations/${encodeURIComponent(id!)}/events?after=${seq}`);
      source = current;
      const live = () => !disposed && source === current;
      current.onopen = () => {
        if (!live()) return;
        setSnapshot((old) => ({ ...old, id, connection: "connected" })); void load();
      };
      current.onerror = () => {
        if (!live()) return;
        current.close(); source = undefined;
        setSnapshot((old) => ({ ...old, id, connection: "offline" })); reconnect = setTimeout(connect, 2000);
      };
      const event = (e: MessageEvent) => {
        if (!live()) return;
        const next = Number(e.lastEventId);
        if (Number.isFinite(next)) seq = Math.max(seq, next);
        invalidate();
      };
      for (const name of ["text", "tool", "turn.started", "turn.waiting", "turn.resumed", "turn.finished", "usage.reconciled", "access.changed"])
        current.addEventListener(name, event as EventListener);
      current.addEventListener("reconnect", () => { if (live()) { current.close(); connect(); } });
    }
    void load().then(connect);
    // A quiet conversation must also reflect revocation by another user or service instance.
    const reauthorize = () => { if (document.visibilityState === "visible") void load(); };
    const authorityTimer = setInterval(reauthorize, 15000);
    document.addEventListener("visibilitychange", reauthorize);
    return () => {
      disposed = true; abort.abort(); source?.close();
      pending?.resolve(); pending = undefined;
      clearInterval(authorityTimer); clearTimeout(timer); clearTimeout(reconnect);
      document.removeEventListener("visibilitychange", reauthorize);
    };
  }, [id]);
  // React renders a changed route before running the old effect's cleanup. Bind every
  // visible snapshot to its route during render, including the empty/new-chat route.
  const current = snapshot.id === id ? snapshot : { connection: "connecting" };
  return { value: current.value, error: current.error, connection: current.connection, refresh };
}
