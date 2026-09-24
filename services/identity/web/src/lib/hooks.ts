import { useCallback, useEffect, useRef, useState } from "react";
import { isAbort } from "./labels";

/** The value, once it has stopped changing for `ms`. Searches wait for the person to finish
 *  typing instead of sending one request per keystroke. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Load `fetcher` whenever `key` changes, cancelling the previous request.
 *
 *  Without the abort, a slow answer to an earlier query could arrive after a faster answer to
 *  the current one and overwrite it — the list would show results for what was typed a moment
 *  ago. `data` is kept while a new request is in flight so the table does not flash empty.
 */
export function useLoad<T>(key: string | null, fetcher: (signal: AbortSignal) => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(key !== null);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;
  const ctl = useRef<AbortController | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (key === null) { setLoading(false); return; }
    ctl.current?.abort();
    const c = new AbortController();
    ctl.current = c;
    setLoading(true);
    setError(null);
    fetchRef.current(c.signal)
      .then((d) => { if (!c.signal.aborted) { setData(d); setError(null); } })
      .catch((e) => { if (!c.signal.aborted && !isAbort(e)) setError(e); })
      .finally(() => { if (!c.signal.aborted) { setLoading(false); setLoadedKey(key); } });
    return () => c.abort();
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading: loading || key !== loadedKey, reload, setData };
}
