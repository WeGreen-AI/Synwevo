import { t as tr } from "@synwevo/ui";
import "./messages";
/**
 * The per-row action menu on the member and invitation lists.
 *
 * A menu rather than a row of buttons because a member can have six actions and a table with
 * six buttons per row is one nobody reads. Keyboard: Enter/Space opens, arrows move, Home/End
 * jump, Escape closes and returns focus to the button, Tab closes. Each item that is not
 * available says why (`title`) instead of disappearing, so the menu has the same shape for
 * everyone and nothing is learned by its absence.
 */
import { useEffect, useRef, useState } from "react";
import { isImeKey } from "@synwevo/ui";

export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** Why it is disabled. */
  hint?: string;
  danger?: boolean;
}

export function RowMenu({ items, label }: { items: RowMenuItem[]; label: string }) {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  useEffect(() => {
    if (open) wrap.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[focus]?.focus();
  }, [open, focus]);

  const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0);
  const close = (refocus: boolean) => { setOpen(false); if (refocus) btn.current?.focus(); };
  const onKey = (e: React.KeyboardEvent) => {
    if (!open || isImeKey(e)) return;
    const pos = enabled.indexOf(focus);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") close(false);
    else if (e.key === "ArrowDown" && enabled.length) { e.preventDefault(); setFocus(enabled[(pos + 1) % enabled.length]); }
    else if (e.key === "ArrowUp" && enabled.length) { e.preventDefault(); setFocus(enabled[(pos - 1 + enabled.length) % enabled.length]); }
    else if (e.key === "Home" && enabled.length) { e.preventDefault(); setFocus(enabled[0]); }
    else if (e.key === "End" && enabled.length) { e.preventDefault(); setFocus(enabled[enabled.length - 1]); }
  };

  return (
    <div className="menu-wrap" ref={wrap} onKeyDown={onKey}>
      <button ref={btn} type="button" className={`btn ghost sm${open ? " active" : ""}`}
        aria-haspopup="menu" aria-expanded={open} aria-label={label}
        onClick={() => { setOpen((o) => !o); setFocus(enabled[0] ?? 0); }}>{tr("操作")}<span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="menu" role="menu" aria-label={label}>
          {items.map((it, i) => (
            <button type="button" key={it.label} role="menuitem" tabIndex={-1}
              className={`menu-item${it.danger ? " danger" : ""}`}
              disabled={it.disabled} title={it.disabled ? it.hint : undefined}
              aria-description={it.disabled ? it.hint : undefined}
              onMouseEnter={() => !it.disabled && setFocus(i)}
              onClick={() => { close(true); it.onSelect(); }}>
              {it.label}
              {it.disabled && it.hint && <span className="menu-hint">{it.hint}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
