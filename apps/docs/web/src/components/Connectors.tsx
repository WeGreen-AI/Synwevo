import { useEffect, useRef, useState } from "react";
import type { Mapping } from "../lib/types";
import { markColor } from "../lib/css";

/** Curved lines linking source and target highlights of each mapping. */
export default function Connectors({ container, mappings, active, visible, tick }: { container: HTMLElement | null; mappings: Mapping[]; active: string | null; visible: boolean; tick: number }) {
  const [paths, setPaths] = useState<{ id: string; d: string; color: string }[]>([]);
  const raf = useRef(0);

  useEffect(() => {
    if (!container || !visible) {
      setPaths([]);
      return;
    }
    const compute = () => {
      const base = container.getBoundingClientRect();
      const left = container.querySelector(".pane.left .pane-b") as HTMLElement | null;
      const right = container.querySelector(".pane.right .pane-b") as HTMLElement | null;
      if (!left || !right) return;
      const lr = left.getBoundingClientRect(), rr = right.getBoundingClientRect();
      const out: { id: string; d: string; color: string }[] = [];
      for (const m of mappings) {
        if (!m.enabled) continue;
        const a = left.querySelector(`[data-mark="src:${m.id}"]`) as HTMLElement | null;
        const b = right.querySelector(`[data-mark="tgt:${m.id}"]`) as HTMLElement | null;
        if (!a || !b) continue;
        const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
        const ay = clamp(ar.top + Math.min(ar.height, 22) / 2, lr.top, lr.bottom);
        const by = clamp(br.top + Math.min(br.height, 22) / 2, rr.top, rr.bottom);
        const aVisible = ar.bottom >= lr.top && ar.top <= lr.bottom;
        const bVisible = br.bottom >= rr.top && br.top <= rr.bottom;
        if (!aVisible && !bVisible) continue;
        const x1 = Math.min(ar.right, lr.right) - base.left;
        const x2 = Math.max(br.left, rr.left) - base.left;
        const y1 = ay - base.top, y2 = by - base.top;
        const mx = (x1 + x2) / 2;
        out.push({ id: m.id, color: m.color, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` });
      }
      setPaths(out);
    };
    const schedule = () => {
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(compute);
    };
    schedule();
    container.addEventListener("scroll", schedule, true);
    window.addEventListener("resize", schedule);
    const iv = setInterval(schedule, 800);
    return () => {
      container.removeEventListener("scroll", schedule, true);
      window.removeEventListener("resize", schedule);
      clearInterval(iv);
      cancelAnimationFrame(raf.current);
    };
  }, [container, mappings, visible, tick]);

  if (!visible) return null;
  return (
    <svg className="connectors" aria-hidden="true">
      {paths.map((p) => (
        <g key={p.id}>
          <path d={p.d} stroke={markColor(p.color)} className={p.id === active ? "active" : ""} strokeDasharray={p.id === active ? undefined : "5 4"} />
          <circle cx={p.d.split(" ")[0].slice(1).split(",")[0]} cy={p.d.split(" ")[0].split(",")[1]} r={3.5} fill={markColor(p.color)} />
        </g>
      ))}
    </svg>
  );
}

const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));
