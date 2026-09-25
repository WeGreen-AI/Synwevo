import { useEffect, useState } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { AuthFrame, FlowError, PasswordField, checkPassword, continuation, signedInDestination, readIntent, useAction, useLinkToken } from "./org/AuthFlow";
import { isChallengeError, useChallenge } from "./org/Challenge";

export default function VerifyEmail() {
  const token = useLinkToken();
  return <EmailVerification key={token} token={token} />;
}
function EmailVerification({ token }: { token: string }) {
  const { t, locale } = useI18n(); const { me, refresh } = useSession(); const action = useAction();
  const [email, setEmail] = useState(() => me?.email || readIntent().email || "");
  const [verified, setVerified] = useState(false); const [resent, setResent] = useState(false);
  const [passwordRequired, setPasswordRequired] = useState(false); const [password, setPassword] = useState("");
  const [signInRequired, setSignInRequired] = useState(false);
  const activeSession = me?.status === "active";
  const [unavailable, setUnavailable] = useState(() => new URLSearchParams(window.location.search).get("delivery") === "unavailable");
  useEffect(() => { if (me?.email) setEmail(value => value || me.email); }, [me?.email]);
  function verify(e?: React.FormEvent) { e?.preventDefault(); void action.run(async () => {
    if (passwordRequired) checkPassword(password);
    // The token's account can differ from the current session. Let the server identify
    // pending activation; never infer permission to replace a password from `me`.
    try {
      const out = await post<{ sign_in_required: boolean }>("/api/auth/email/verify", {
        token, ...(passwordRequired ? { new_password: password } : {}),
      });
      setSignInRequired(out.sign_in_required); setPassword(""); setVerified(true);
      await refresh();
    } catch (error) {
      if (error instanceof ApiError && error.code === "new_password_required") {
        setPasswordRequired(true);
        return;
      }
      throw error;
    }
  }); }
  const challenge = useChallenge("resend", { enabled: !activeSession });
  useEffect(() => { if (isChallengeError(action.error)) challenge.reset(); }, [action.error]);   // eslint-disable-line react-hooks/exhaustive-deps
  function resend(e: React.FormEvent) { e.preventDefault(); if (me === undefined) return; void action.run(async () => {
    const out = activeSession
      ? await post<{ delivery?: "requested" | "unavailable"; already_verified?: boolean }>("/api/auth/verify-email/send", {})
      : await post<{ delivery?: "requested" | "unavailable"; already_verified?: boolean }>("/api/auth/email/resend", { email: email.trim(), locale, ...challenge.body() });
    setResent(true); setUnavailable(out.delivery === "unavailable");
    if (out.already_verified) { setVerified(true); await refresh(); }
  }); }
  const continueSignedIn = !!me && !signInRequired;
  return <AuthFrame title={t(verified ? "邮箱已验证" : "验证你的邮箱")} description={t(verified ? signInRequired ? "邮箱验证和新口令设置已完成。请使用新口令重新登录。" : "邮箱验证已完成，可以继续设置你的空间。" : token ? "确认这是你的邮箱后，点击下方按钮完成验证。" : "我们已收到请求。请检查邮箱，使用最新邮件中的验证链接。")}>
    <FlowError error={action.error} />
    {verified ? <><p className="flow-success" role="status">{t("验证完成")}</p><a className="btn primary" href={continueSignedIn ? signedInDestination(me!, continuation()) : `/login?next=${encodeURIComponent(readIntent().invite_token ? "/invite" : "/onboarding")}`}>{t(continueSignedIn ? "继续" : "登录并继续")}</a></>
      : <>{token && (passwordRequired ? <form onSubmit={verify} aria-busy={action.busy}>
          <p className="notice" role="status">{t("为确保只有邮箱持有人能使用账户，请设置由你掌控的新口令。此前的口令和登录会话将失效。")}</p>
          <PasswordField value={password} onChange={setPassword} label={t("新口令")} autoFocus disabled={action.busy} />
          <button className="btn primary" disabled={action.busy}>{t(action.busy ? "验证中…" : "设置新口令并验证邮箱")}</button>
        </form> : <button type="button" className="btn primary" disabled={action.busy} onClick={() => verify()}>{t(action.busy ? "验证中…" : "验证此邮箱")}</button>)}
        {unavailable && <p className="notice warn" role="status">{t("邮件服务暂不可用。请稍后重新发送验证邮件，或联系服务管理员。")}</p>}
        {resent && !unavailable && <p className="notice" role="status">{t("验证请求已收到。请检查收件箱和垃圾邮件；是否投递取决于账户状态和邮件服务。")}</p>}
        <form onSubmit={resend} aria-busy={action.busy}>
          <div className="field"><label htmlFor="verify-email">{t("邮箱")}</label><input id="verify-email" className="input" type="email" autoComplete="email" inputMode="email" value={activeSession ? me.email : email} readOnly={activeSession} onChange={e => { setEmail(e.target.value); setResent(false); }} autoCapitalize="off" spellCheck={false} required /></div>
          {challenge.element}
          <button className="btn" disabled={action.busy || me === undefined}>{t(action.busy ? "处理中…" : "重新发送验证邮件")}</button>
        </form>
        <p className="tiny muted">{t("验证完成前，账户无法进入工作空间。打开邮件链接不会自动验证邮箱。")}</p>
        <a className="authfoot" href="/login">{t("返回登录")}</a>{me && <button type="button" className="btn ghost" disabled={action.busy} onClick={() => void action.run(async () => { await post("/api/auth/logout", {}); window.location.assign("/login"); })}>{t("退出当前账户")}</button>}
      </>}
  </AuthFrame>;
}
