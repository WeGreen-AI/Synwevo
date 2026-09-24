import { useCallback, useEffect, useRef } from "react";

/** Run a request whose answer is only worth having while it is still the newest one.
 *
 *  Several panels fire a request as the user types — the sample rule in 定义样本, the naming
 *  pattern in 生成 — behind a short debounce. A debounce spaces requests out; it does not stop
 *  them overlapping, and the network never promised to answer them in order. So a slower earlier
 *  response can land after a faster later one, and the panel is left showing the answer to a
 *  question the user has already moved past: the form says 数据起始行 3 while the preview beside
 *  it counts the rows for 2, or the 生成 dialog reports the file count for a naming pattern that
 *  is no longer in the box. Nothing looks broken, which is what makes it bad — those numbers are
 *  what the user checks before committing a batch.
 *
 *  Starting a new request aborts the one before it, an answer that is no longer current is
 *  dropped, and leaving the view cancels whatever is still in flight. Aborts are swallowed
 *  rather than reported: a request the code itself cancelled is not a failure to show anyone.
 */
export function useLatest() {
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);
  return useCallback(<T>(run: (signal: AbortSignal) => Promise<T>, ok: (v: T) => void, fail?: (e: any) => void) => {
    ctl.current?.abort();
    const mine = (ctl.current = new AbortController());
    run(mine.signal).then(
      (v) => { if (!mine.signal.aborted) ok(v); },
      (e) => { if (!mine.signal.aborted && e?.name !== "AbortError") fail?.(e); },
    );
    // Effect cleanup invalidates the answer immediately, including the next debounce window.
    return () => mine.abort();
  }, []);
}
