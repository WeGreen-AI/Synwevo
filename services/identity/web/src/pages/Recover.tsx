/** Recovery never discloses account existence. Preview is read-only; reset requires submission. */
import { useEffect, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, Loading, PasswordField, checkPassword, useAction, useLinkToken } from "./org/AuthFlow";
import { isChallengeError, useChallenge } from "./org/Challenge";
export function ForgotPassword() {
  const { t, locale } = useI18n(); const action = useAction(); const [email, setEmail] = useState(""); const [sent, setSent] = useState(false);
  const challenge = useChallenge("forgot");
  useEffect(() => { if (isChallengeError(action.error)) challenge.reset(); }, [action.error]);   // eslint-disable-line react-hooks/exhaustive-deps
  function submit(e: React.FormEvent) { e.preventDefault(); void action.run(async () => { await post("/api/auth/forgot", { email: email.trim(), locale, ...challenge.body() }); setSent(true); }); }
  return <AuthFrame title={t(sent ? "看看你的邮箱" : "找回账户")} description={t(sent ? "如果这个邮箱有账户，口令重置链接已发出。" : "输入账户邮箱，申请口令重置链接。")}>
    {sent ? <><p role="status">{email.trim()}</p><p className="tiny muted">{t("链接 2 小时内有效，只能用一次。没收到邮件？请检查垃圾邮件或确认邮箱地址。")}</p><a className="btn primary" href="/login">{t("返回登录")}</a><button type="button" className="btn ghost" onClick={() => setSent(false)}>{t("换个邮箱或重新申请")}</button></>
      : <form onSubmit={submit} aria-busy={action.busy}><FlowError error={action.error} /><div className="field"><label htmlFor="forgot-email">{t("邮箱")}</label><input id="forgot-email" className="input" type="email" autoComplete="username" inputMode="email" autoCapitalize="off" spellCheck={false} autoFocus required value={email} onChange={e => setEmail(e.target.value)} /></div>{challenge.element}<button className="btn primary" disabled={action.busy}>{t(action.busy ? "提交中…" : "发送重置链接")}</button><a className="authfoot" href="/login">{t("返回登录")}</a></form>}
  </AuthFrame>;
}
export function ResetPassword() {
  const token = useLinkToken();
  return <PasswordReset key={token} token={token} />;
}
function PasswordReset({ token }: { token: string }) {
  const { t } = useI18n(); const action = useAction();
  const [target, setTarget] = useState<{ email: string } | null>(null); const [error, setError] = useState<unknown>(null); const [retry, setRetry] = useState(0);
  const [password, setPassword] = useState(""); const [done, setDone] = useState<{ mfa_required: boolean } | null>(null);
  useEffect(() => {
    if (!token) { setError(new Error("链接无效或已过期，请重新申请。")); return; }
    const abort = new AbortController(); setError(null);
    post<{ email: string }>("/api/auth/reset/preview", { token }, abort.signal).then(setTarget)
      .catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [token, retry]);
  function submit(e: React.FormEvent) { e.preventDefault(); void action.run(async () => {
    checkPassword(password, target?.email);
    const out = await post<{ mfa_required: boolean }>("/api/auth/reset", { token, password }); setDone(out); setPassword("");
  }); }
  return <AuthFrame title={t(done ? "口令已更新" : "设置新口令")} description={t(done ? "所有设备上的登录都已结束，请使用新口令重新登录。" : "新口令生效后，所有设备都需要重新登录。")}>
    {done ? <>{done.mfa_required && <p role="status">{t("两步验证保持启用，下次登录仍然需要它。")}</p>}<a className="btn primary" href="/login">{t("去登录")}</a></>
      : error ? <><FlowError error={error} />{error instanceof ApiError && (error.status === 0 || error.status >= 500) && <button type="button" className="btn" onClick={() => setRetry(v => v + 1)}>{t("重试")}</button>}<a className="btn" href="/forgot-password">{t("重新申请一封")}</a></>
      : !target ? <Loading /> : <form onSubmit={submit} aria-busy={action.busy}><p className="tiny muted">{target.email}</p><FlowError error={action.error} /><PasswordField label={t("新口令")} value={password} onChange={setPassword} autoFocus /><button className="btn primary" disabled={action.busy}>{t(action.busy ? "处理中…" : "设置新口令并结束所有登录")}</button>{action.error instanceof ApiError && ["bad_token", "weak_password"].includes(action.error.code) && <a href="/forgot-password">{t("重新申请一封")}</a>}</form>}
  </AuthFrame>;
}
