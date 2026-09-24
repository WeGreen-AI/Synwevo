import { t as tr, useI18n, formatDate, formatNumber } from "./i18n";
import { createContext, ReactNode, RefObject, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { FOCUSABLE, isImeKey, isTopModal, popModal, pushModal, strayKeyAction, trapIndex } from "./a11y";
import { ToastTimers } from "./toasttimers";

/* ------------------------------------------------------------ toasts */
type Toast = { id: number; text: string; kind: "info" | "ok" | "err" };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});
const TOAST_MS: Record<Toast["kind"], number> = { info: 3500, ok: 3500, err: 8000 };
const EDGE = 16;

export function ToastProvider({ children }: { children: ReactNode }) {
  useI18n();
  const [items, setItems] = useState<Toast[]>([]);
  const [flip, setFlip] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  // Hovering or focusing the stack pauses every countdown (see toasttimers.ts for why the hold
  // is re-read from the page instead of trusted from mouseleave/blur alone).
  const focusInside = () => {
    const box = boxRef.current, a = document.activeElement;
    return !!box && !!a && a !== document.body && box.contains(a);
  };
  const hovered = () => {
    try {
      return !!boxRef.current?.matches(":hover");
    } catch {
      return false;
    }
  };
  const timers = useRef<ToastTimers | null>(null);
  if (!timers.current) {
    timers.current = new ToastTimers((id) => setItems((xs) => xs.filter((t) => t.id !== id)), () => hovered() || focusInside());
  }
  const clock = timers.current;

  const remove = useCallback((id: number) => {
    clock.forget(id);
    setItems((xs) => xs.filter((t) => t.id !== id));
  }, [clock]);

  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => {
      for (const old of xs.slice(0, -4)) clock.forget(old.id);
      return [...xs.slice(-4), { id, text, kind }];
    });
    clock.arm(id, TOAST_MS[kind]);
  }, [clock]);

  // After every change to the stack, once the DOM matches it: a ✕ that had focus is gone now, and
  // Firefox and Safari said nothing about it.
  useEffect(() => { clock.recheck(); }, [items, clock]);
  useEffect(() => () => clock.dispose(), [clock]);

  // WCAG 2.4.11: the stack sits bottom-right, which is where the side panel's bulk bar and the
  // end of the mapping list live. When the focused control would be hidden under it, the stack
  // moves to the other corner instead of covering the thing the keyboard user is on.
  useLayoutEffect(() => {
    const check = () => {
      const box = boxRef.current;
      const a = document.activeElement as HTMLElement | null;
      if (!box || !items.length || !a || a === document.body || box.contains(a)) { setFlip(false); return; }
      const b = box.getBoundingClientRect();
      const r = a.getBoundingClientRect();
      const right = window.innerWidth - EDGE, bottom = window.innerHeight - EDGE;
      const left = right - b.width, top = bottom - b.height;
      setFlip(r.left < right && r.right > left && r.top < bottom && r.bottom > top);
    };
    check();
    document.addEventListener("focusin", check);
    return () => document.removeEventListener("focusin", check);
  }, [items]);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div ref={boxRef} className={`toasts${flip ? " flip" : ""}`} role="status" aria-live="polite"
        onMouseEnter={() => clock.hold()}
        onMouseLeave={() => { if (!focusInside()) clock.release(); }}
        onFocus={() => clock.hold()}
        onBlur={(e) => { if (!boxRef.current?.contains(e.relatedTarget as Node | null) && !hovered()) clock.release(); }}>
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} onClick={() => remove(t.id)}>
            <span className="grow">{tr(t.text)}</span>
            <button type="button" className="toast-x" aria-label={tr("关闭提示")} onClick={(e) => { e.stopPropagation(); remove(t.id); }}><span aria-hidden="true">✕</span></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------ modal focus */
function visible(el: HTMLElement) {
  return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
}

/** Focus management for anything modal: the shared Modal and the command palette.
 *
 *  - initial focus runs once, when the dialog opens. It used to re-run whenever the parent passed
 *    a new `onClose` — every parent passes an inline arrow, and the workspace re-renders every
 *    900 ms while a file is parsing — so focus jumped back to the first field mid-sentence.
 *  - initial target: `[data-autofocus]`, then the first text field, then the primary button,
 *    then the dialog itself. A destructive confirm marks 取消 with data-autofocus.
 *  - Tab and Shift+Tab wrap inside the dialog.
 *  - Escape closes only the topmost dialog, and never while an IME is composing.
 *  - on close, focus returns to what had it before (the mapping list, a ⋯ button) if it still
 *    exists, so the keyboard user continues where they were instead of starting from <body>.
 */
export function useModalFocus(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const idRef = useRef("");
  useEffect(() => {
    const id = pushModal();
    idRef.current = id;
    const opener = document.activeElement as HTMLElement | null;
    const root = ref.current;
    if (root) {
      const fields = Array.from(root.querySelectorAll<HTMLElement>(
        'input:not([disabled]):not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select:not([disabled]), textarea:not([disabled])')).filter(visible);
      const target = root.querySelector<HTMLElement>("[data-autofocus]") || fields[0]
        || root.querySelector<HTMLElement>(".primary:not([disabled])") || root;
      target.focus({ preventScroll: false });
    }
    return () => {
      popModal(id);
      if (opener && opener !== document.body && opener.isConnected && typeof opener.focus === "function") {
        opener.focus({ preventScroll: true });
      }
    };
    // Runs once per open on purpose; see the comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keys pressed while this dialog is on top but focus is outside it (dropped to <body> when the
  // focused button was removed or disabled). The onKeyDown below sits on the dialog element and
  // never sees them; without this, Escape stopped working and Tab left the dialog.
  useEffect(() => {
    const onStrayKey = (e: KeyboardEvent) => {
      if (isImeKey(e)) return;
      const root = ref.current;
      if (!root || !isTopModal(idRef.current)) return;
      if (e.target instanceof Node && root.contains(e.target)) return;
      const what = strayKeyAction(e.key, e.shiftKey);
      if (!what) return;
      e.preventDefault();
      e.stopPropagation();
      if (what === "close") { closeRef.current(); return; }
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible);
      (items.length ? items[what === "first" ? 0 : items.length - 1] : root).focus();
    };
    document.addEventListener("keydown", onStrayKey, true);
    return () => document.removeEventListener("keydown", onStrayKey, true);
  }, [ref]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (isImeKey(e)) return;
    if (e.key === "Escape") {
      if (!isTopModal(idRef.current)) return;
      e.preventDefault();
      e.stopPropagation();
      closeRef.current();
      return;
    }
    if (e.key === "Tab") {
      const root = ref.current;
      if (!root) return;
      const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(visible);
      if (!items.length) { e.preventDefault(); root.focus(); return; }
      const at = items.indexOf(document.activeElement as HTMLElement);
      const next = trapIndex(items.length, at, e.shiftKey);
      // Let the browser move focus inside the dialog; step in only at the edges (and when focus
      // is on the container), so native order within the dialog stays untouched.
      if (at < 0 || (e.shiftKey && at === 0) || (!e.shiftKey && at === items.length - 1)) {
        e.preventDefault();
        items[next]?.focus();
      }
    }
  }, [ref]);

  const isTop = useCallback(() => isTopModal(idRef.current), []);
  return { onKeyDown, isTop };
}

/* ------------------------------------------------------------ modal */
export function Modal({ title, onClose, children, footer, size, role = "dialog", describedBy }: {
  title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "wide" | "narrow";
  role?: "dialog" | "alertdialog"; describedBy?: string;
}) {
  useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const { onKeyDown, isTop } = useModalFocus(ref, onClose);
  return (
    <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget && isTop()) onClose(); }}>
      <div className={`modal ${size || ""}`} ref={ref} role={role} aria-modal="true" aria-labelledby={titleId}
        aria-describedby={describedBy} tabIndex={-1} onKeyDown={onKeyDown}>
        <div className="modal-h">
          <h2 className="grow" id={titleId}>{title}</h2>
          <button type="button" className="btn ghost icon" onClick={onClose} aria-label={tr("关闭")}>✕</button>
        </div>
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ confirm / prompt */
type DialogState =
  | null
  | { n: number; kind: "confirm"; title: string; body: ReactNode; ok: string; danger?: boolean; resolve: (v: boolean) => void }
  | { n: number; kind: "prompt"; title: string; label: string; value: string; ok: string; resolve: (v: string | null) => void };
let dialogSeq = 0;
const DialogCtx = createContext<{ confirm: (t: string, b: ReactNode, ok?: string, danger?: boolean) => Promise<boolean>; prompt: (t: string, l: string, v?: string, ok?: string) => Promise<string | null> }>({
  confirm: async () => false,
  prompt: async () => null,
});

export function DialogProvider({ children }: { children: ReactNode }) {
  useI18n();
  const [st, setSt] = useState<DialogState>(null);
  const [val, setVal] = useState("");
  const cur = useRef<DialogState>(null);
  const bodyId = useId();
  // A second request while one is still open answers the first one "cancelled" instead of
  // leaving its promise pending forever (and its caller's `await` with it).
  const open = (next: Exclude<DialogState, null>) => {
    next.n = ++dialogSeq;              // a fresh dialog (and fresh initial focus) per question
    const prev = cur.current;
    if (prev) (prev.resolve as (v: any) => void)(prev.kind === "confirm" ? false : null);
    cur.current = next;
    setSt(next);
  };
  const confirm = useCallback((title: string, body: ReactNode, ok = "确定", danger = false) =>
    new Promise<boolean>((resolve) => open({ n: 0, kind: "confirm", title, body, ok, danger, resolve })), []);
  const prompt = useCallback((title: string, label: string, value = "", ok = "确定") => new Promise<string | null>((resolve) => {
    setVal(value);
    open({ n: 0, kind: "prompt", title, label, value, ok, resolve });
  }), []);
  const close = (v: any) => {
    const s = cur.current;
    if (!s) return;
    cur.current = null;
    setSt(null);
    (s.resolve as (x: any) => void)(v);
  };
  const cancel = () => close(st?.kind === "confirm" ? false : null);
  return (
    <DialogCtx.Provider value={{ confirm, prompt }}>
      {children}
      {st && (
        <Modal
          key={st.n}
          title={tr(st.title)}
          size="narrow"
          role={st.kind === "confirm" ? "alertdialog" : "dialog"}
          describedBy={st.kind === "confirm" ? bodyId : undefined}
          onClose={cancel}
          footer={
            <>
              <span className="spacer" />
              {/* For a destructive confirm the least destructive choice takes focus, so a stray
                  Enter or a second Delete press cancels rather than deletes. */}
              <button type="button" className="btn" onClick={cancel} data-autofocus={st.kind === "confirm" && st.danger ? true : undefined}>{tr("取消")}</button>
              <button type="button" className={`btn ${st.kind === "confirm" && st.danger ? "danger" : "primary"}`} onClick={() => close(st.kind === "confirm" ? true : val)}>{tr(st.ok)}</button>
            </>
          }
        >
          {st.kind === "confirm" ? (
            <div id={bodyId} style={{ lineHeight: 1.6 }}>{typeof st.body === 'string' ? tr(st.body) : st.body}</div>
          ) : (
            <label className="field">
              {tr(st.label)}
              <input className="input" value={val} data-autofocus onChange={(e) => setVal(e.target.value)}
                onKeyDown={(e) => {
                  // Enter that confirms a pinyin candidate is not a submit.
                  if (isImeKey(e)) return;
                  if (e.key === "Enter") { e.preventDefault(); close(val); }
                }} />
            </label>
          )}
        </Modal>
      )}
    </DialogCtx.Provider>
  );
}
export const useDialog = () => useContext(DialogCtx);

export function fmtSize(n: number) {
  if (n < 1024) return `${formatNumber(n)} B`;
  if (n < 1024 * 1024) return `${formatNumber(n / 1024, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} KB`;
  return `${formatNumber(n / 1024 / 1024, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} MB`;
}

export function fmtTime(t?: number | null) {
  if (!t) return "";
  return formatDate(t * 1000, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function colLetter(c: number) {
  let s = "";
  c += 1;
  while (c) {
    const rem = (c - 1) % 26;
    s = String.fromCharCode(65 + rem) + s;
    c = Math.floor((c - 1) / 26);
  }
  return s;
}
export const cellRef = (r: number, c: number) => `${colLetter(c)}${r + 1}`;
export const rangeRef = (r1: number, c1: number, r2: number, c2: number) => (r1 === r2 && c1 === c2 ? cellRef(r1, c1) : `${cellRef(r1, c1)}:${cellRef(r2, c2)}`);

export const FAMILY_ICON: Record<string, string> = { table: "▦", document: "¶", pdf: "⎙", image: "▣", slides: "▭", text: "≡", unknown: "?" };
export const STATUS_TEXT: Record<string, string> = { parsing: "解析中", ready: "可用", error: "错误", unsupported: "不支持", needs_ocr: "需 OCR" };
