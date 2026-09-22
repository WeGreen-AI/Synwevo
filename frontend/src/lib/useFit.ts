import { RefObject, useLayoutEffect, useState } from "react";

/** Scale factor (<= 1) so content of ``width`` px fits the element's width. */
export function useFit(ref: RefObject<HTMLElement | null>, width: number, margin = 48) {
  const [fit, setFit] = useState(1);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !width) return;
    const upd = () => setFit(Math.min(1, Math.max(0.3, (el.clientWidth - margin) / width)));
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, width, margin]);
  return fit;
}
