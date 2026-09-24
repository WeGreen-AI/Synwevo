/**
 * The confirmation for anything that takes access away or hands authority over.
 *
 * One component so every such action says the same three things in the same place, before
 * anything happens: **who** is affected, **what they lose** (access, sessions, tokens), and
 * **why** (a written reason that goes into the audit record). It replaced `window.prompt`, which
 * showed the administrator's password in clear text, could not be filled by a password manager,
 * could be silently suppressed by the browser after a few prompts, and said nothing about the
 * effect.
 *
 * Re-authentication happens *inside* the dialog, and only when the server asks for it
 * (403 `reauth_required`): the dialog then shows a password field, proves it for the scope the
 * server named, and retries the same action with the same reason. A re-authentication that is
 * still fresh is not asked for again. The password field is an ordinary
 * `autocomplete="current-password"` input next to a hidden `autocomplete="username"` holding
 * the signed-in address, so password managers fill it and paste works (WCAG 3.3.8).
 */
import { FormEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { Modal } from "@synwevo/ui";
import { errText, fmtDuration } from "../lib/labels";

export const REASON_MIN = 8;

export interface ActionDialogProps<T> {
  title: string;
  /** Who is affected and what they lose. Rendered first, above every field. */
  impact: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  /** "required" for the dangerous operations (the server refuses without one). */
  reason?: "required" | "optional" | "none";
  reasonPlaceholder?: string;
  /** The scope to prove when the server asks and does not name one itself. */
  reauthScope?: "admin" | "mfa";
  /** Fields that shape the action (a role picker, a code), shown above the reason. */
  children?: ReactNode;
  /** False while `children` are incomplete. */
  ready?: boolean;
  size?: "wide" | "narrow";
  run: (reason: string) => Promise<T>;
  onDone: (result: T) => void;
  onClose: () => void;
}

export function ActionDialog<T>(p: ActionDialogProps<T>) {
  const { me } = useSession();
  const formId = useId();
  const reasonId = useId();
  const pwId = useId();
  const errId = useId();
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [scope, setScope] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [retryUntil, setRetryUntil] = useState(0);
  const [retrySeconds, setRetrySeconds] = useState(0);
  const inFlight = useRef(false);
  const [signedOut, setSignedOut] = useState(false);
  const pwRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const mode = p.reason || "none";
  const reasonShort = mode === "required" && Array.from(reason.trim()).length < REASON_MIN;

  // The password field appears only once the server has asked; move the keyboard to it then.
  useEffect(() => { if (scope) pwRef.current?.focus(); }, [scope]);

  useEffect(() => {
    const update = () => setRetrySeconds(Math.max(0, Math.ceil((retryUntil - Date.now()) / 1000)));
    update();
    if (retryUntil <= Date.now()) return;
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [retryUntil]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (inFlight.current || busy || signedOut || p.ready === false || Date.now() < retryUntil) return;
    if (reasonShort) {
      setErr(`请写明理由（至少 ${REASON_MIN} 个字）。它会原样写入审计记录。`);
      reasonRef.current?.focus();
      return;
    }
    if (scope && !password) {
      setErr("请输入你的口令以确认身份。");
      pwRef.current?.focus();
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setErr(null);
    try {
      if (scope) {
        await post("/api/auth/reauth", { password, scope });
        setPassword("");
        setScope(null);
      }
      const out = await p.run(reason.trim());
      p.onDone(out);
    } catch (e2) {
      const ae = e2 instanceof ApiError ? e2 : null;
      if (ae?.code === "reauth_required") {
        // The server decides whether this needs a password now, and for which purpose.
        setScope((ae.detail && typeof ae.detail === "object" && ae.detail.scope) || p.reauthScope || "admin");
        setErr(null);
      } else if (ae?.code === "missing_reason") {
        setErr(ae.message);
        reasonRef.current?.focus();
      } else if (ae && !ae.upstream && ae.status === 401 && ae.code !== "bad_credentials") {
        // Too many wrong passwords end the session on purpose; nothing in this dialog can work
        // after that, so say so and offer the way back instead of another password field.
        setSignedOut(true);
        setErr(ae.message);
      } else {
        if (ae?.retryAfter) setRetryUntil(Date.now() + ae.retryAfter * 1000);
        setErr(errText(e2));
        if (ae?.code === "bad_credentials") { setPassword(""); pwRef.current?.focus(); }
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const close = () => { if (!busy) p.onClose(); };
  const here = window.location.pathname + window.location.search;
  const noFields = mode === "none" && !p.children && !scope;

  return (
    <Modal title={p.title} onClose={close} size={p.size || "narrow"} role="alertdialog" describedBy={`${formId}-impact`}
      footer={signedOut ? (
        <>
          <span className="spacer" />
          <a className="btn primary" href={`/login?next=${encodeURIComponent(here)}`}>重新登录</a>
        </>
      ) : (
        <>
          <span className="spacer" />
          <button type="button" className="btn" onClick={close} disabled={busy}
            data-autofocus={noFields ? true : undefined}>取消</button>
          <button type="submit" form={formId} className={`btn ${p.danger ? "danger" : "primary"}`}
            disabled={busy || p.ready === false || retrySeconds > 0}>
            {busy ? "处理中…" : retrySeconds > 0 ? `${fmtDuration(retrySeconds)}后可重试` : scope ? `验证并${p.confirmLabel}` : p.confirmLabel}
          </button>
        </>
      )}>
      <form id={formId} className="col gap12" onSubmit={submit} noValidate aria-describedby={err ? errId : undefined}>
        <div id={`${formId}-impact`} className={`impact${p.danger ? " danger" : ""}`}>{p.impact}</div>

        <fieldset disabled={busy || signedOut} className="col gap12" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        {p.children}

        {mode !== "none" && (
          <div className="field">
            <label htmlFor={reasonId}>
              理由{mode === "required" ? <span className="req">（必填，至少 {REASON_MIN} 个字）</span> : <span className="muted">（可选）</span>}
            </label>
            <textarea id={reasonId} ref={reasonRef} className="input" rows={2} value={reason}
              placeholder={p.reasonPlaceholder || "为什么要这样做？"} maxLength={500}
              aria-invalid={reasonShort && !!err ? true : undefined}
              onChange={(e) => setReason(e.target.value)} />
            <span className="tiny muted">
              会原样写入审计记录{mode === "required" && Array.from(reason.trim()).length > 0 && reasonShort
                ? `，还差 ${REASON_MIN - Array.from(reason.trim()).length} 个字` : ""}。
            </span>
          </div>
        )}

        {scope && (
          <div className="notice info col gap8">
            <b>这一步需要重新验证身份。</b>
            <span className="small">
              验证只对这类操作有效，并在几分钟后失效。连续输错会结束当前登录。
            </span>
            {/* Present for password managers, which pick the credential by the username field
                beside the password; never shown and never focusable. */}
            <input type="text" name="username" autoComplete="username" value={me?.email || ""}
              readOnly hidden tabIndex={-1} aria-hidden="true" />
            <div className="field">
              <label htmlFor={pwId}>你的口令</label>
              <input id={pwId} ref={pwRef} className="input" type="password" name="password"
                autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </div>
          </div>
        )}

        </fieldset>
        {err && <div id={errId} className="notice err" role="alert">{err}</div>}
      </form>
    </Modal>
  );
}
