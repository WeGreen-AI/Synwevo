import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------ toasts */
type Toast = { id: number; text: string; kind: "info" | "ok" | "err" };
const ToastCtx = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs.slice(-4), { id, text, kind }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), kind === "err" ? 8000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ------------------------------------------------------------ modal */
export function Modal({ title, onClose, children, footer, size }: { title: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode; size?: "wide" | "narrow" }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button.primary")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${size || ""}`} ref={ref} role="dialog" aria-modal="true">
        <div className="modal-h">
          <h2 className="grow">{title}</h2>
          <button className="btn ghost icon" onClick={onClose} aria-label="关闭">✕</button>
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
  | { kind: "confirm"; title: string; body: ReactNode; ok: string; danger?: boolean; resolve: (v: boolean) => void }
  | { kind: "prompt"; title: string; label: string; value: string; ok: string; resolve: (v: string | null) => void };
const DialogCtx = createContext<{ confirm: (t: string, b: ReactNode, ok?: string, danger?: boolean) => Promise<boolean>; prompt: (t: string, l: string, v?: string, ok?: string) => Promise<string | null> }>({
  confirm: async () => false,
  prompt: async () => null,
});

export function DialogProvider({ children }: { children: ReactNode }) {
  const [st, setSt] = useState<DialogState>(null);
  const [val, setVal] = useState("");
  const confirm = useCallback((title: string, body: ReactNode, ok = "确定", danger = false) => new Promise<boolean>((resolve) => setSt({ kind: "confirm", title, body, ok, danger, resolve })), []);
  const prompt = useCallback((title: string, label: string, value = "", ok = "确定") => new Promise<string | null>((resolve) => {
    setVal(value);
    setSt({ kind: "prompt", title, label, value, ok, resolve });
  }), []);
  const close = (v: any) => {
    if (!st) return;
    (st.resolve as any)(v);
    setSt(null);
  };
  return (
    <DialogCtx.Provider value={{ confirm, prompt }}>
      {children}
      {st && (
        <Modal
          title={st.title}
          size="narrow"
          onClose={() => close(st.kind === "confirm" ? false : null)}
          footer={
            <>
              <span className="spacer" />
              <button className="btn" onClick={() => close(st.kind === "confirm" ? false : null)}>取消</button>
              <button className={`btn ${st.kind === "confirm" && st.danger ? "danger" : "primary"}`} onClick={() => close(st.kind === "confirm" ? true : val)}>{st.ok}</button>
            </>
          }
        >
          {st.kind === "confirm" ? (
            <div style={{ lineHeight: 1.6 }}>{st.body}</div>
          ) : (
            <label className="field">
              {st.label}
              <input className="input" value={val} autoFocus onChange={(e) => setVal(e.target.value)} onKeyDown={(e) => e.key === "Enter" && close(val)} />
            </label>
          )}
        </Modal>
      )}
    </DialogCtx.Provider>
  );
}
export const useDialog = () => useContext(DialogCtx);

export function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(t?: number | null) {
  if (!t) return "";
  const d = new Date(t * 1000);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
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
