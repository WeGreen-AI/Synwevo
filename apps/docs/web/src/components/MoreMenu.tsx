import { useEffect, useRef, useState } from "react";
import { isImeKey } from "@synwevo/ui";

export interface MenuItem { label: string; onClick: () => unknown; disabled?: boolean; title?: string }

/** Accessible overflow menu: click / Enter opens, arrows move, Escape or outside click closes.
 *  Escape hands focus back to the ⋯ button (focus used to drop to <body>), and Tab closes the
 *  menu instead of leaving it open behind the next control. */
export default function MoreMenu({ items, label = "更多", ariaLabel }: { items: MenuItem[]; label?: string; ariaLabel?: string }) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  useEffect(() => {
    if (open) ref.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[focus]?.focus();
  }, [open, focus]);
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) btn.current?.focus();
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (!open || isImeKey(e)) return;
    const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0);
    const pos = enabled.indexOf(focus);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") close(true);
    else if (e.key === "ArrowDown" && enabled.length) { e.preventDefault(); setFocus(enabled[(pos + 1) % enabled.length]); }
    else if (e.key === "ArrowUp" && enabled.length) { e.preventDefault(); setFocus(enabled[(pos - 1 + enabled.length) % enabled.length]); }
    else if (e.key === "Home" && enabled.length) { e.preventDefault(); setFocus(enabled[0]); }
    else if (e.key === "End" && enabled.length) { e.preventDefault(); setFocus(enabled[enabled.length - 1]); }
  };
  return (
    <div className="menu-wrap" ref={ref} onKeyDown={onKey}>
      <button ref={btn} type="button" className={`btn ghost sm${open ? " active" : ""}`} aria-haspopup="menu" aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => { setOpen(!open); setFocus(items.findIndex((i) => !i.disabled)); }}>{label} ▾</button>
      {open && (
        <div className="menu" role="menu" aria-label={ariaLabel || label}>
          {items.map((it, i) => (
            <button type="button" key={it.label} role="menuitem" className="menu-item" disabled={it.disabled} tabIndex={-1} title={it.title}
              onClick={() => { close(true); Promise.resolve().then(it.onClick).catch(() => { /* callers report their own errors */ }); }}
              onMouseEnter={() => !it.disabled && setFocus(i)}>
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
