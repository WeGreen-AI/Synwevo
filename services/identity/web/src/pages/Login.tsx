import { useEffect, useRef, useState } from "react";
import { ApiError, get, post, safeNext, signInUrl, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { b64urlToBuf, toJSON } from "../components/PasskeySection";
import { AuthFrame, FlowError, Loading, PasswordField, checkPassword, saveIntent, signedInDestination, useAction, type FlowUser } from "./org/AuthFlow";

export default function Login() {
  const { me, state, refresh, disabled, error } = useSession();
  const { t } = useI18n();
  const next = safeNext(new URLSearchParams(window.location.search).get("next"));
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [token, setToken] = useState(""); const [name, setName] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [methods, setMethods] = useState<{ totp: boolean; recovery: boolean } | null>(null);
  const [code, setCode] = useState(""); const [useRecovery, setUseRecovery] = useState(false);
  const [setupRequested, setSetupRequested] = useState(false);
  const action = useAction(); const codeRef = useRef<HTMLInputElement>(null);
  // An unconfigured deployment may already have legitimate registered users. The server's
  // bootstrap flag enables an explicit setup choice; it must never replace ordinary sign-in.
  const bootstrap = !!state?.bootstrap_required && setupRequested;
  useEffect(() => { if (ticket) codeRef.current?.focus(); }, [ticket, useRecovery]);
  useEffect(() => {
    if (action.busy) return;
    if (disabled) window.location.assign(next);
    else if (me) window.location.assign(signedInDestination(me, next));
    else if (state?.password_login === false) window.location.replace(signInUrl(state, next));
  }, [me, disabled, state, next, action.busy]);
  async function finish(out?: { status?: string; user?: FlowUser }) {
    saveIntent({ email: out?.user?.email || email, next });
    if ((out?.status === "verification_pending" || out?.status === "pending")) { window.location.assign("/verify-email"); return; }
    const user = out?.user || await get<FlowUser>("/api/auth/me");
    window.location.assign(signedInDestination(user, next));
  }
  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void action.run(async () => {
      try {
        if (bootstrap) {
          checkPassword(password, email, name);
          await finish(await post("/api/auth/bootstrap", { token: token.trim(), email: email.trim(), password, display_name: name || null }));
        } else if (ticket) {
          const out = await post<{ recovery_remaining?: number; user?: FlowUser }>("/api/auth/login/mfa", useRecovery ? { ticket, recovery_code: code } : { ticket, code });
          if (out.recovery_remaining !== undefined) { try { sessionStorage.setItem("dw_recovery_notice", String(out.recovery_remaining)); } catch { /* optional notice */ } }
          await finish(out);
        } else {
          const out = await post<{ mfa_required?: boolean; ticket?: string; methods?: { totp: boolean; recovery: boolean }; status?: string; user?: FlowUser }>("/api/auth/login", { email: email.trim(), password });
          if (out.mfa_required && out.ticket) {
            setTicket(out.ticket); setMethods(out.methods || null); setUseRecovery(out.methods?.totp === false); setPassword(""); return;
          }
          await finish(out);
        }
      } catch (e) { setPassword(""); throw e; }
    });
  }
  async function signInWithPasskey() {
    await action.run(async () => {
      try {
        const options = await post<Record<string, unknown>>("/api/auth/passkeys/authenticate/begin", {});
        const publicKey = "parseRequestOptionsFromJSON" in PublicKeyCredential
          ? (PublicKeyCredential as unknown as { parseRequestOptionsFromJSON: (o: unknown) => PublicKeyCredentialRequestOptions }).parseRequestOptionsFromJSON(options)
          : { ...(options as unknown as PublicKeyCredentialRequestOptions), challenge: b64urlToBuf(options.challenge as string),
              allowCredentials: (options.allowCredentials as { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] }[] | undefined)?.map(c => ({ ...c, id: b64urlToBuf(c.id) })) };
        const credential = await navigator.credentials.get({ publicKey }) as PublicKeyCredential | null;
        if (!credential) throw new Error("没有收到安全密钥的响应。");
        await finish(await post("/api/auth/login/passkey", { credential: toJSON(credential) }));
      } catch (e) {
        if (e instanceof DOMException && ["NotAllowedError", "AbortError"].includes(e.name)) throw new Error("已取消，或没有可用于本站的安全密钥。");
        throw e;
      }
    });
  }
  const title = t(ticket ? "两步验证" : bootstrap ? "建立首位管理员" : "欢迎回来");
  const description = t(ticket ? useRecovery ? "输入一个恢复码。每个恢复码只能用一次。" : "打开验证器应用，输入其中显示的 6 位数字。" : bootstrap ? "使用服务器生成的一次性引导令牌，建立这个部署的首位管理员。" : "登录你的 Synwevo 账户，继续手头的工作。");
  return <AuthFrame title={title} description={description}>
    {error ? <><FlowError error={error} /><button type="button" className="btn" onClick={() => void action.run(refresh)} disabled={action.busy}>{t("重试")}</button></>
      : disabled || me || state?.password_login === false ? <Loading>{t("正在前往…")}</Loading> : !state ? <Loading /> : <form onSubmit={submit} aria-busy={action.busy}>
      <FlowError error={action.error} />
      {ticket ? <>
        <div className="field"><label htmlFor="login-code">{t(useRecovery ? "恢复码" : "验证码")}</label>
          <input id="login-code" ref={codeRef} className="input mono" value={code} inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" autoCapitalize="off" spellCheck={false} required maxLength={useRecovery ? 24 : 6} onChange={e => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))} /></div>
        <button className="btn primary" disabled={action.busy || (useRecovery ? code.trim().length < 6 : code.length !== 6)}>{t(action.busy ? "验证中…" : "验证并登录")}</button>
        {methods?.recovery && methods.totp && <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setUseRecovery(v => !v); setCode(""); action.setError(null); }}>{t(useRecovery ? "改用验证器应用" : "使用恢复码")}</button>}
        <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setTicket(null); setCode(""); setUseRecovery(false); action.setError(null); }}>{t("返回，换个账户登录")}</button>
      </> : <>
        {bootstrap && <div className="field"><label htmlFor="bootstrap-token">{t("一次性引导令牌")}</label><input id="bootstrap-token" className="input" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" spellCheck={false} required /></div>}
        <div className="field"><label htmlFor="login-email">{t("邮箱")}</label><input id="login-email" className="input" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="username" inputMode="email" autoCapitalize="off" spellCheck={false} autoFocus required /></div>
        {bootstrap && <div className="field"><label htmlFor="bootstrap-name">{t("显示名称（可选）")}</label><input id="bootstrap-name" className="input" value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></div>}
        <PasswordField value={password} onChange={setPassword} fresh={bootstrap} />
        {!bootstrap && <a className="authfoot" href="/forgot-password">{t("忘记口令？")}</a>}
        <button className="btn primary" disabled={action.busy}>{t(action.busy ? "处理中…" : bootstrap ? "创建管理员并登录" : "登录")}</button>
        {!bootstrap && typeof window.PublicKeyCredential !== "undefined" && <><div className="flow-divider">{t("或")}</div><button type="button" className="btn" disabled={action.busy} onClick={() => void signInWithPasskey()}>{t("使用安全密钥登录")}</button></>}
        {!bootstrap && state.registration === "open" && <p className="authfoot">{t("还没有账户？")} <a href={`/register?next=${encodeURIComponent(next)}`}>{t("创建账户")}</a></p>}
        {!bootstrap && state.registration === "invite" && <p className="authfoot muted">{t("本服务目前为邀请制，请使用邀请邮件创建账户。")}</p>}
        {!bootstrap && state.registration === "closed" && <p className="authfoot muted">{t("此服务暂未开放注册。")}</p>}
        {state.bootstrap_required && <>
          {!bootstrap && <p className="tiny muted">{t("已有账户可直接登录。部署初始化需要服务器提供的一次性令牌。")}</p>}
          <button type="button" className="btn ghost" disabled={action.busy}
            onClick={() => { setSetupRequested(value => !value); setPassword(""); setToken(""); action.setError(null); }}>
            {t(bootstrap ? "返回普通登录" : "设置此部署的首位管理员")}
          </button>
        </>}
      </>}
    </form>}
  </AuthFrame>;
}
