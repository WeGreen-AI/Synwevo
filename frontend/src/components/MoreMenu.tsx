import { useEffect, useRef, useState } from "react";

export interface MenuItem { label: string; onClick: () => void; disabled?: boolean }

/** Accessible overflow menu: click / Enter opens, arrows move, Escape or outside click closes. */
export default function MoreMenu({ items, label = "更多" }: { items: MenuItem[]; label?: string }) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  useEffect(() => {
    if (open) ref.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[focus]?.focus();
  }, [open, focus]);
  const onKey = (e: React.KeyboardEvent) => {
    if (!open) return;
    const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0);
    const pos = enabled.indexOf(focus);
    if (e.key === "Escape") { setOpen(false); e.stopPropagation(); }
    if (e.key === "ArrowDown") { e.preventDefault(); setFocus(enabled[(pos + 1) % enabled.length]); }
    if (e.key === "ArrowUp") { e.preventDefault(); setFocus(enabled[(pos - 1 + enabled.length) % enabled.length]); }
  };
  return (
    <div className="menu-wrap" ref={ref} onKeyDown={onKey}>
      <button className={`btn ghost sm${open ? " active" : ""}`} aria-haspopup="menu" aria-expanded={open}
        onClick={() => { setOpen(!open); setFocus(items.findIndex((i) => !i.disabled)); }}>{label} ▾</button>
      {open && (
        <div className="menu" role="menu">
          {items.map((it, i) => (
            <button key={it.label} role="menuitem" className="menu-item" disabled={it.disabled} tabIndex={-1}
              onClick={() => { setOpen(false); it.onClick(); }} onMouseEnter={() => !it.disabled && setFocus(i)}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
