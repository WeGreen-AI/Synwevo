import { useCallback, useEffect, useRef, useState } from "react";
import { read } from "../../shared/ops";
import type { Conversation } from "./types";

/** Events only invalidate an authorized snapshot. Reconnection never submits a turn or a tool. */
export function useConversation(id: string | undefined) {
  const [value, setValue] = useState<Conversation>();
  const [error, setError] = useState<unknown>();
  const [connection, setConnection] = useState("connecting");
  const refreshRef = useRef<() => Promise<void>>(async () => undefined);
  const refresh = useCallback(() => refreshRef.current(), []);
  useEffect(() => {
    setValue(undefined); setError(undefined);
    if (!id) { refreshRef.current = async () => undefined; return; }
    let disposed = false, seq = 0, busy = false, pending = false;
    let source: EventSource | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    async function load() {
      if (disposed) return;
      if (busy) { pending = true; return; }
      busy = true;
      try {
        const out = await read<Conversation>("agent.conversation.get", { conversation_id: id }, null, abort.signal);
        if (!disposed) {
          seq = Math.max(seq, out.event_seq);
          setValue(out); setError(undefined);
        }
      } catch (e) {
        if (!disposed && !abort.signal.aborted) { setError(e); setValue(undefined); }
      } finally {
        busy = false;
        if (pending && !disposed) { pending = false; invalidate(); }
      }
    }
    refreshRef.current = load;
    function invalidate() {
      // Throttle instead of debouncing: a continuous token stream must still render.
      if (timer) return;
      timer = setTimeout(() => { timer = undefined; void load(); }, 150);
    }
    function connect() {
      if (disposed) return;
      source = new EventSource(`/api/suite/agent/conversations/${encodeURIComponent(id!)}/events?after=${seq}`);
      source.onopen = () => { setConnection("connected"); void load(); };
      source.onerror = () => {
        source?.close();
        if (!disposed) { setConnection("offline"); reconnect = setTimeout(connect, 2000); }
      };
      const event = (e: MessageEvent) => {
        const next = Number(e.lastEventId);
        if (Number.isFinite(next)) seq = Math.max(seq, next);
        invalidate();
      };
      for (const name of ["text", "tool", "turn.started", "turn.waiting", "turn.resumed", "turn.finished", "access.changed"])
        source.addEventListener(name, event as EventListener);
      source.addEventListener("reconnect", () => { source?.close(); connect(); });
    }
    setConnection("connecting");
    void load().then(connect);
    // A quiet conversation must also reflect revocation by another user or service instance.
    const reauthorize = () => { if (document.visibilityState === "visible") void load(); };
    const authorityTimer = setInterval(reauthorize, 15000);
    document.addEventListener("visibilitychange", reauthorize);
    return () => {
      disposed = true; abort.abort(); source?.close();
      clearInterval(authorityTimer); clearTimeout(timer); clearTimeout(reconnect);
      document.removeEventListener("visibilitychange", reauthorize);
    };
  }, [id]);
  return { value, error, connection, refresh };
}
