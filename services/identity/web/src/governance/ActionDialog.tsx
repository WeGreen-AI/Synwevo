import { t as tr, useI18n } from "@synwevo/ui";
import "./messages";
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
 * (403 `reauth_required`): the dialog reads the available methods, proves the named scope with
 * the account password or a user-verifying passkey, and retries the same action/reason. A re-authentication that is
 * still fresh is not asked for again. The password field is an ordinary
 * `autocomplete="current-password"` input next to a hidden `autocomplete="username"` holding
 * the signed-in address, so password managers fill it and paste works (WCAG 3.3.8).
 */
import { FormEvent, ReactNode, useEffect, useId, useRef, useState } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { Modal } from "@synwevo/ui";
import { errText, fmtDuration } from "./labels";
import { ReauthClientError, reauthenticateWithPasskey, useReauthMethods, type ReauthMethod } from "../lib/reauth";

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
  useI18n();
  const { me } = useSession();
  const formId = useId();
  const reasonId = useId();
  const pwId = useId();
  const errId = useId();
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [scope, setScope] = useState<string | null>(null);
  const reauth = useReauthMethods(scope);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [retryUntil, setRetryUntil] = useState(0);
  const [retrySeconds, setRetrySeconds] = useState(0);
  const inFlight = useRef(false);
  const pendingProof = useRef<AbortController | null>(null);
  // A same-document route change can unmount this dialog while WebAuthn is still waiting.
  // Cancel the ceremony and never execute its action from a discarded confirmation.
  useEffect(() => () => pendingProof.current?.abort(), []);
  const [signedOut, setSignedOut] = useState(false);
  const pwRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const passkeyRef = useRef<HTMLButtonElement>(null);
  const mode = p.reason || "none";
  const reasonShort = mode === "required" && Array.from(reason.trim()).length < REASON_MIN;

  // Method discovery is asynchronous; focus the password only when it is actually available.
  useEffect(() => {
    if (!scope || !reauth.methods) return;
    if (reauth.methods.includes("password")) pwRef.current?.focus();
    else passkeyRef.current?.focus();
  }, [scope, reauth.methods]);

  useEffect(() => {
    const update = () => setRetrySeconds(Math.max(0, Math.ceil((retryUntil - Date.now()) / 1000)));
    update();
    if (retryUntil <= Date.now()) return;
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [retryUntil]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    await submitAction(scope && !reauth.methods?.includes("password") ? "passkey_uv" : "password");
  }

  async function submitAction(method: ReauthMethod) {
    if (inFlight.current || busy || signedOut || p.ready === false || Date.now() < retryUntil) return;
    if (reasonShort) {
      setErr(tr("请写明理由（至少 {p0} 个字）。它会原样写入审计记录。", {p0: REASON_MIN}));
      reasonRef.current?.focus();
      return;
    }
    if (scope && (!reauth.methods?.includes(method) || reauth.error)) return;
    if (scope && method === "password" && !password) {
      setErr(tr("请输入你的口令以确认身份。"));
      pwRef.current?.focus();
      return;
    }
    inFlight.current = true;
    const proof = new AbortController();
    pendingProof.current = proof;
    setBusy(true);
    setErr(null);
    try {
      if (scope) {
        if (method === "passkey_uv") await reauthenticateWithPasskey(scope, proof.signal);
        else await post("/api/auth/reauth", { password, scope });
        if (proof.signal.aborted) return;
        setPassword("");
        setScope(null);
      }
      if (proof.signal.aborted) return;
      const out = await p.run(reason.trim());
      p.onDone(out);
    } catch (e2) {
      if (proof.signal.aborted) return;
      const ae = e2 instanceof ApiError ? e2 : null;
      if (ae?.code === "reauth_required") {
        // The server decides whether this needs fresh identity proof, and for which purpose.
        setScope((ae.detail && typeof ae.detail === "object" && ae.detail.scope) || p.reauthScope || "admin");
        setErr(null);
      } else if (ae?.code === "missing_reason") {
        setErr(errText(ae));
        reasonRef.current?.focus();
      } else if (ae && !ae.upstream && ae.status === 401 && !["bad_credentials", "bad_assertion", "unknown_passkey", "uv_required"].includes(ae.code)) {
        // Too many wrong passwords end the session on purpose; nothing in this dialog can work
        // after that, so say so and offer the way back instead of another password field.
        setSignedOut(true);
        setErr(errText(ae));
      } else {
        if (ae?.retryAfter) setRetryUntil(Date.now() + ae.retryAfter * 1000);
        setErr(e2 instanceof ReauthClientError ? e2.message : errText(e2));
        if (ae?.code === "bad_credentials") { setPassword(""); pwRef.current?.focus(); }
      }
    } finally {
      if (pendingProof.current === proof) pendingProof.current = null;
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
          <a className="btn primary" href={`/login?next=${encodeURIComponent(here)}`}>{tr("重新登录")}</a>
        </>
      ) : (
        <>
          <span className="spacer" />
          <button type="button" className="btn" onClick={close} disabled={busy}
            data-autofocus={noFields ? true : undefined}>{tr("取消")}</button>
          <button type="submit" form={formId} className={`btn ${p.danger ? "danger" : "primary"}`}
            disabled={busy || p.ready === false || retrySeconds > 0 || !!scope && !reauth.methods?.length}>
            {busy ? tr("处理中…") : retrySeconds > 0 ? tr("{p0}后可重试", {p0: fmtDuration(retrySeconds)}) : scope ? tr("验证并{p0}", {p0: p.confirmLabel}) : p.confirmLabel}
          </button>
        </>
      )}>
      <form id={formId} className="col gap12" onSubmit={submit} noValidate aria-describedby={err ? errId : undefined}>
        <div id={`${formId}-impact`} className={`impact${p.danger ? " danger" : ""}`}>{p.impact}</div>

        <fieldset disabled={busy || signedOut} className="col gap12" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        {p.children}

        {mode !== "none" && (
          <div className="field">
            <label htmlFor={reasonId}>{tr("理由")}{mode === "required" ? <span className="req">{tr("（必填，至少{p0}个字）", {p0: REASON_MIN})}</span> : <span className="muted">{tr("（可选）")}</span>}
            </label>
            <textarea id={reasonId} ref={reasonRef} className="input" rows={2} value={reason}
              placeholder={p.reasonPlaceholder || tr("为什么要这样做？")} maxLength={500}
              aria-invalid={reasonShort && !!err ? true : undefined}
              onChange={(e) => setReason(e.target.value)} />
            <span className="tiny muted">{tr("会原样写入审计记录{p0}。", {p0: mode === "required" && Array.from(reason.trim()).length > 0 && reasonShort
                ? tr("，还差 {p0} 个字", {p0: REASON_MIN - Array.from(reason.trim()).length}) : ""})}</span>
          </div>
        )}

        {scope && (
          <div className="notice info col gap8">
            <b>{tr("这一步需要重新验证身份。")}</b>
            <span className="small">{tr("验证只对这类操作有效，并在几分钟后失效。")}</span>
            {reauth.methods?.includes("password") && <span className="small">{tr("连续输错口令会结束当前登录。")}</span>}
            {/* Present for password managers, which pick the credential by the username field
                beside the password; never shown and never focusable. */}
            <input type="text" name="username" autoComplete="username" value={me?.email || ""}
              readOnly hidden tabIndex={-1} aria-hidden="true" />
            {!reauth.methods && !reauth.error && <p role="status">{tr("正在读取可用的身份验证方式…")}</p>}
            {reauth.error != null && <div role="alert"><p>{errText(reauth.error)}</p><button type="button" className="btn sm" onClick={reauth.reload}>{tr("重新读取验证方式")}</button></div>}
            {reauth.methods?.length === 0 && <p role="alert">{tr("此账户没有可用的近期身份验证方式。请在账户页检查已登记的验证方式。")}</p>}
            {reauth.methods?.includes("password") && <div className="field">
              <label htmlFor={pwId}>{tr("你的口令")}</label>
              <input id={pwId} ref={pwRef} className="input" type="password" name="password"
                autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </div>}
            {reauth.methods?.includes("passkey_uv") && <button ref={passkeyRef} type="button" className="btn" disabled={busy || p.ready === false || retrySeconds > 0}
              onClick={() => void submitAction("passkey_uv")}>{tr("使用安全密钥确认并继续")}</button>}
          </div>
        )}

        </fieldset>
        {err && <div id={errId} className="notice err" role="alert">{tr(err)}</div>}
      </form>
    </Modal>
  );
}
