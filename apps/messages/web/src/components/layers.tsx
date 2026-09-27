/** The two floating layers this page needs beyond the shared `Menu`: a popover (reaction bar,
 *  emoji picker, mention suggestions) and an action menu that can be opened by something other
 *  than its own trigger — a long press or a right-click on a message.
 *
 *  Both are placed by the shared `useAnchoredLayer` (viewport coordinates at the document root,
 *  re-measured on scroll, flipped above when there is no room below), so nothing is clipped by
 *  the scrolling thread. Escape closes and returns focus to the anchor wherever focus is (WebKit
 *  does not focus a button on click, so the listener is on `document`). */
import { useCallback, useEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { FOCUSABLE, isImeKey, useAnchoredLayer } from "@synwevo/ui";

export function Popover({ open, anchor, onClose, children, label, align = "start", className = "", role = "dialog", autoFocus = true }: {
  open: boolean; anchor: RefObject<HTMLElement | null>; onClose: (refocus: boolean) => void; children: ReactNode; label: string;
  align?: "start" | "end"; className?: string; role?: "dialog" | "group" | "listbox"; autoFocus?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const style = useAnchoredLayer(open, anchor, ref, () => onClose(false), align);
  const placed = style.visibility === "visible";
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!anchor.current?.contains(t) && !ref.current?.contains(t)) onClose(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (isImeKey(e)) return;
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(true); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey, true); };
  }, [open, anchor, onClose]);
  useEffect(() => {
    if (!open || !placed || !autoFocus) return;
    const root = ref.current; if (!root) return;
    const target = root.querySelector<HTMLElement>("[data-autofocus]") || root.querySelector<HTMLElement>(FOCUSABLE);
    target?.focus({ preventScroll: true });
  }, [open, placed, autoFocus]);
  if (!open) return null;
  return createPortal(
    <div ref={ref} className={`msg-layer ${className}`} role={role} aria-label={label} style={style}>{children}</div>,
    document.body);
}

export interface ActionItem { key: string; label: ReactNode; onSelect: () => unknown; danger?: boolean; disabled?: boolean; hint?: string; icon?: ReactNode }

/** A menu of message actions. Keyboard as the shared `Menu`: arrows move and wrap, Home/End jump,
 *  Escape closes and returns focus, Tab closes. Opened by the ⋯ button, a right-click or a long
 *  press; `anchor` is the element it hangs from (the button, or the bubble for pointer gestures). */
export function ActionMenu({ open, anchor, items, label, onClose, align = "end" }: {
  open: boolean; anchor: RefObject<HTMLElement | null>; items: ActionItem[]; label: string; onClose: (refocus: boolean) => void; align?: "start" | "end";
}) {
  const list = useRef<HTMLDivElement>(null);
  const style = useAnchoredLayer(open, anchor, list, () => onClose(false), align);
  const placed = style.visibility === "visible";
  const enabled = items.map((it, i) => (it.disabled ? -1 : i)).filter(i => i >= 0);
  const focusItem = useCallback((i: number) => { list.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]")[i]?.focus(); }, []);
  useEffect(() => { if (open && placed) focusItem(enabled[0] ?? 0); }, [open, placed]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!anchor.current?.contains(t) && !list.current?.contains(t)) onClose(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (isImeKey(e)) return;
      const current = Array.from(list.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") || []).indexOf(document.activeElement as HTMLButtonElement);
      const pos = enabled.indexOf(current);
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(true); }
      else if (e.key === "Tab") onClose(false);
      else if (e.key === "ArrowDown" && enabled.length) { e.preventDefault(); focusItem(enabled[(pos + 1) % enabled.length]); }
      else if (e.key === "ArrowUp" && enabled.length) { e.preventDefault(); focusItem(enabled[(pos - 1 + enabled.length) % enabled.length]); }
      else if (e.key === "Home" && enabled.length) { e.preventDefault(); focusItem(enabled[0]); }
      else if (e.key === "End" && enabled.length) { e.preventDefault(); focusItem(enabled[enabled.length - 1]); }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey, true); };
  }, [open, anchor, onClose, enabled.join(","), focusItem]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!open) return null;
  return createPortal(
    <div ref={list} className="menu layer msg-menu" role="menu" aria-label={label} style={style}>
      {items.map((it) => (
        <button type="button" key={it.key} role="menuitem" tabIndex={-1} className={`menu-item${it.danger ? " danger" : ""}`} disabled={it.disabled}
          title={it.disabled ? it.hint : undefined} aria-description={it.disabled ? it.hint : undefined}
          onClick={() => { onClose(true); Promise.resolve(it.onSelect()).catch(() => { /* callers report their own errors */ }); }}>
          {it.icon && <span className="msg-menu-ic" aria-hidden="true">{it.icon}</span>}{it.label}
          {it.disabled && it.hint && <span className="menu-hint">{it.hint}</span>}
        </button>
      ))}
    </div>, document.body);
}
