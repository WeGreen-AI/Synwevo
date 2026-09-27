import { useEffect, useRef, useState } from "react";
import { ApiError, get, post, safeNext, signInUrl, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { b64urlToBuf, toJSON } from "../components/PasskeySection";
import { AuthFrame, FlowError, Loading, PasswordField, checkPassword, platformHomeUrl, saveIntent, signedInDestination, useAction, type FlowUser } from "./org/AuthFlow";
import { isChallengeError, useChallenge } from "./org/Challenge";

type Methods = { totp: boolean; recovery: boolean };
type Mode = "password" | "code";
/** Messages for the `ext_error` code an external-provider sign-in lands here with. */
const EXTERNAL_ERRORS: Record<string, string> = {
  cancelled: "已取消外部登录。",
  provider_refused: "外部身份提供商拒绝了这次登录，请重试。",
  bad_state: "登录状态校验失败，请重新开始。", bad_callback: "登录请求已过期或已使用，请重新开始。",
  bad_id_token: "外部身份提供商返回的身份信息无法验证，请重试。", token_exchange_failed: "外部身份提供商未接受这次登录，请重试。",
  idp_unreachable: "暂时无法连接外部身份提供商，请稍后再试。",
  email_unverified: "该提供商没有验证你的邮箱，无法用它创建或匹配账户。请用口令登录后，在「账户与安全」里关联。",
  registration_closed: "此服务暂未开放注册。", email_taken: "这个邮箱已有账户，请先用口令登录，再在「账户与安全」里关联。",
  account_unavailable: "账户不可用，请联系管理员。", admin_requires_totp: "平台管理员需先启用动态验证码，才能使用外部登录。",
  link_session_changed: "关联登录方式时登录状态发生了变化，请重新登录后再试。",
  identity_linked_elsewhere: "这个外部账户已关联到另一个 Synwevo 账户。",
  unknown_provider: "未启用这个外部身份提供商。",
};

/** A sign-in continued from another page: an MFA ticket after a provider sign-in, or a link offer. */
function fragment(): URLSearchParams {
  return new URLSearchParams(window.location.hash.replace(/^#/, ""));
}

export default function Login() {
  const { me, state, refresh, disabled, error } = useSession();
  const { t, locale } = useI18n();
  const query = new URLSearchParams(window.location.search);
  const [next, setNext] = useState(() => safeNext(query.get("next")));
  const [mode, setMode] = useState<Mode>("password");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [token, setToken] = useState(""); const [name, setName] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [methods, setMethods] = useState<Methods | null>(null);
  const [code, setCode] = useState(""); const [useRecovery, setUseRecovery] = useState(false);
  // Always unchecked to begin with: a shared or public computer must never inherit the last
  // person's choice, so nothing about it is remembered between sign-ins.
  const [remember, setRemember] = useState(false);
  const [setupRequested, setSetupRequested] = useState(false);
  const [codeSent, setCodeSent] = useState<{ email: string; expires: number } | null>(null);
  const [offer, setOffer] = useState<string | null>(null);
  const [offerInfo, setOfferInfo] = useState<{ label: string; email: string; totp: boolean; password: boolean } | null>(null);
  const [externalError, setExternalError] = useState<string | null>(null);
  const action = useAction(); const codeRef = useRef<HTMLInputElement>(null);
  // An unconfigured deployment may already have legitimate registered users. The server's
  // bootstrap flag enables an explicit setup choice; it must never replace ordinary sign-in.
  const bootstrap = !!state?.bootstrap_required && setupRequested;
  // Human verification: the mailed-code request is always challenged when the deployment has
  // it; the password form only after the server answered challenge_required.
  const codeChallenge = useChallenge("login_email", { enabled: mode === "code" && !codeSent && !bootstrap });
  const [loginChallengeArmed, setLoginChallengeArmed] = useState(false);
  const loginChallenge = useChallenge("login", { armed: loginChallengeArmed, enabled: mode !== "code" && !bootstrap && !offer && !ticket });
  useEffect(() => { if (isChallengeError(action.error)) { codeChallenge.reset(); loginChallenge.reset(); } }, [action.error]);   // eslint-disable-line react-hooks/exhaustive-deps
  const providers = state?.external_providers || [];
  // A sign-in that was not on the way to a particular page ends on the platform (user feedback
  // 2026-09-27: landing on the account settings after signing in read as "the logic is wrong").
  const home = platformHomeUrl(state?.portal_url, locale);

  useEffect(() => {
    // Continuations arrive in the fragment so they never reach server logs or referrers.
    const frag = fragment();
    if (frag.get("ticket")) {
      const m = (frag.get("methods") || "").split(",");
      setTicket(frag.get("ticket")); setMethods({ totp: m.includes("totp"), recovery: m.includes("recovery") });
      setUseRecovery(!m.includes("totp")); setRemember(frag.get("remember") === "1");
      if (frag.get("next")) setNext(safeNext(frag.get("next")));
    }
    if (frag.get("offer")) setOffer(frag.get("offer"));
    if (frag.get("ticket") || frag.get("offer")) window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    const ext = new URLSearchParams(window.location.search).get("ext_error");
    if (ext) {
      setExternalError(EXTERNAL_ERRORS[ext] || "外部登录失败，请重试。");
      const url = new URL(window.location.href); url.searchParams.delete("ext_error");
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    }
  }, []);
  useEffect(() => {
    if (!offer) return;
    post<{ label: string; email: string; totp: boolean; password: boolean }>("/api/auth/external/offer/preview", { offer })
      .then(setOfferInfo).catch(e => { setOffer(null); action.setError(e); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offer]);
  useEffect(() => { if (ticket) codeRef.current?.focus(); }, [ticket, useRecovery]);
  useEffect(() => {
    if (action.busy) return;
    if (disabled) window.location.assign(next);
    else if (me) window.location.assign(signedInDestination(me, next, home));
    else if (state?.password_login === false) window.location.replace(signInUrl(state, next));
  }, [me, disabled, state, next, action.busy, home]);

  async function finish(out?: { status?: string; user?: FlowUser; next?: string }) {
    saveIntent({ email: out?.user?.email || email, next });
    if ((out?.status === "verification_pending" || out?.status === "pending")) { window.location.assign("/verify-email"); return; }
    const user = out?.user || await get<FlowUser>("/api/auth/me");
    window.location.assign(signedInDestination(user, out?.next ? safeNext(out.next) : next, home));
  }
  function chooseRemember(value: boolean) { setRemember(value); }
  function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    void action.run(async () => {
      try {
        if (bootstrap) {
          checkPassword(password, email, name);
          await finish(await post("/api/auth/bootstrap", { token: token.trim(), email: email.trim(), password, display_name: name || null }));
        } else if (offer) {
          const body: Record<string, unknown> = { offer, password, remember };
          if (offerInfo?.totp) { if (useRecovery) body.recovery_code = code; else body.code = code; }
          await finish(await post("/api/auth/external/offer/confirm", body));
        } else if (ticket) {
          const out = await post<{ recovery_remaining?: number; user?: FlowUser }>("/api/auth/login/mfa", useRecovery ? { ticket, recovery_code: code, remember } : { ticket, code, remember });
          if (out.recovery_remaining !== undefined) { try { sessionStorage.setItem("dw_recovery_notice", String(out.recovery_remaining)); } catch { /* optional notice */ } }
          await finish(out);
        } else if (mode === "code" && !codeSent) {
          const out = await post<{ delivery: "requested" | "unavailable"; expires_min: number }>("/api/auth/login/email/start", { email: email.trim(), ...codeChallenge.body() });
          if (out.delivery === "unavailable") throw new Error("邮件服务尚未配置，请改用口令或安全密钥登录。");
          setCodeSent({ email: email.trim(), expires: out.expires_min }); setCode("");
        } else if (mode === "code" && codeSent) {
          const out = await post<{ mfa_required?: boolean; ticket?: string; methods?: Methods; user?: FlowUser }>("/api/auth/login/email/verify", { email: codeSent.email, code, remember });
          if (out.mfa_required && out.ticket) { setTicket(out.ticket); setMethods(out.methods || null); setUseRecovery(out.methods?.totp === false); setCode(""); return; }
          await finish(out);
        } else {
          const out = await post<{ mfa_required?: boolean; ticket?: string; methods?: Methods; status?: string; user?: FlowUser }>("/api/auth/login", { email: email.trim(), password, remember, ...loginChallenge.body() });
          if (out.mfa_required && out.ticket) {
            setTicket(out.ticket); setMethods(out.methods || null); setUseRecovery(out.methods?.totp === false); setPassword(""); return;
          }
          await finish(out);
        }
      } catch (e) {
        // The server asks for a solved challenge after repeated failures from this address: keep
        // the typed password, show the widget, and let the person submit again.
        if (e instanceof ApiError && e.code === "challenge_required") { setLoginChallengeArmed(true); throw e; }
        setPassword(""); throw e;
      }
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
        await finish(await post("/api/auth/login/passkey", { credential: toJSON(credential), remember }));
      } catch (e) {
        if (e instanceof DOMException && ["NotAllowedError", "AbortError"].includes(e.name)) throw new Error("已取消，或没有可用于本站的安全密钥。");
        throw e;
      }
    });
  }
  function externalUrl(id: string) {
    const p = new URLSearchParams({ next, lang: locale });
    if (remember) p.set("remember", "1");
    return `/auth/external/${encodeURIComponent(id)}/start?${p}`;
  }
  const title = t(offer ? "关联外部登录" : ticket ? "两步验证" : bootstrap ? "建立首位管理员" : mode === "code" ? "用邮箱验证码登录" : "欢迎回来");
  const description = offer ? t("这个 {label} 账户的邮箱已有 Synwevo 账户。输入该账户的口令以关联并登录；以后可以直接用 {label} 登录。", { label: offerInfo?.label || t("外部") })
    : t(ticket ? useRecovery ? "输入一个恢复码。每个恢复码只能用一次。" : "打开验证器应用，输入其中显示的 6 位数字。" : bootstrap ? "使用服务器生成的一次性引导令牌，建立这个部署的首位管理员。" : mode === "code" ? (codeSent ? "验证码已发送到你的邮箱（如果该邮箱有账户），请在 10 分钟内输入。" : "我们会把一次性验证码发到你的账户邮箱。") : "登录你的 Synwevo 账户，继续手头的工作。");
  const rememberBox = <label className="check flow-remember" htmlFor="login-remember"><input id="login-remember" type="checkbox" checked={remember} aria-describedby="login-remember-hint" onChange={e => chooseRemember(e.target.checked)} />
    <span>{t("在此设备上保持登录")}<small id="login-remember-hint" className="muted">{t("最长 14 天不活动仍保持登录；公共或共享设备请勿勾选。")}</small></span></label>;
  return <AuthFrame title={title} description={description}>
    {error ? <><FlowError error={error} /><button type="button" className="btn" onClick={() => void action.run(refresh)} disabled={action.busy}>{t("重试")}</button></>
      : disabled || me || state?.password_login === false ? <Loading>{t("正在前往…")}</Loading> : !state ? <Loading /> : <form onSubmit={submit} aria-busy={action.busy}>
      {externalError && !action.error && <div className="notice err flow-error" role="alert">{t(externalError)}</div>}
      <FlowError error={action.error} />
      {offer ? <>
        {!offerInfo ? <Loading /> : <>
          <div className="field"><label htmlFor="offer-email">{t("邮箱")}</label><input id="offer-email" className="input" value={offerInfo.email} readOnly autoComplete="username" /></div>
          {offerInfo.password ? <PasswordField value={password} onChange={setPassword} fresh={false} autoFocus /> : <div className="notice warn">{t("这个账户还没有口令。请先通过邮箱验证码或安全密钥登录，再在「账户与安全」里关联。")}</div>}
          {offerInfo.totp && <div className="field"><label htmlFor="offer-code">{t(useRecovery ? "恢复码" : "验证码")}</label>
            <input id="offer-code" className="input mono" value={code} inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" autoCapitalize="off" spellCheck={false} required maxLength={useRecovery ? 24 : 6} onChange={e => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))} />
            <button type="button" className="btn ghost sm" onClick={() => { setUseRecovery(v => !v); setCode(""); }}>{t(useRecovery ? "改用验证器应用" : "使用恢复码")}</button></div>}
          {rememberBox}
          <button className="btn primary" disabled={action.busy || !offerInfo.password || !password}>{t(action.busy ? "处理中…" : "关联并登录")}</button>
        </>}
        <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setOffer(null); setOfferInfo(null); setPassword(""); setCode(""); action.setError(null); }}>{t("不关联，返回登录")}</button>
      </> : ticket ? <>
        <div className="field"><label htmlFor="login-code">{t(useRecovery ? "恢复码" : "验证码")}</label>
          <input id="login-code" ref={codeRef} className="input mono" value={code} inputMode={useRecovery ? "text" : "numeric"} autoComplete="one-time-code" autoCapitalize="off" spellCheck={false} required maxLength={useRecovery ? 24 : 6} onChange={e => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))} /></div>
        <button className="btn primary" disabled={action.busy || (useRecovery ? code.trim().length < 6 : code.length !== 6)}>{t(action.busy ? "验证中…" : "验证并登录")}</button>
        {methods?.recovery && methods.totp && <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setUseRecovery(v => !v); setCode(""); action.setError(null); }}>{t(useRecovery ? "改用验证器应用" : "使用恢复码")}</button>}
        <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setTicket(null); setCode(""); setUseRecovery(false); setCodeSent(null); action.setError(null); }}>{t("返回，换个账户登录")}</button>
      </> : mode === "code" && !bootstrap ? <>
        {codeSent ? <>
          <div className="field"><label htmlFor="code-email">{t("邮箱")}</label><input id="code-email" className="input" value={codeSent.email} readOnly autoComplete="username" /></div>
          <div className="field"><label htmlFor="email-code">{t("邮箱验证码")}</label>
            <input id="email-code" ref={codeRef} className="input mono" value={code} inputMode="numeric" autoComplete="one-time-code" autoCapitalize="off" spellCheck={false} required maxLength={6} autoFocus onChange={e => setCode(e.target.value.replace(/\D/g, ""))} /></div>
          {rememberBox}
          <button className="btn primary" disabled={action.busy || code.length !== 6}>{t(action.busy ? "验证中…" : "验证并登录")}</button>
          <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setCodeSent(null); setCode(""); action.setError(null); }}>{t("重新发送或换个邮箱")}</button>
        </> : <>
          <div className="field"><label htmlFor="login-email">{t("邮箱")}</label><input id="login-email" className="input" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="username" inputMode="email" autoCapitalize="off" spellCheck={false} autoFocus required /></div>
          {codeChallenge.element}
          <button className="btn primary" disabled={action.busy || !email.trim()}>{t(action.busy ? "发送中…" : "发送验证码")}</button>
        </>}
        <button type="button" className="btn ghost" disabled={action.busy} onClick={() => { setMode("password"); setCodeSent(null); setCode(""); action.setError(null); }}>{t("改用口令登录")}</button>
      </> : <>
        {bootstrap && <div className="field"><label htmlFor="bootstrap-token">{t("一次性引导令牌")}</label><input id="bootstrap-token" className="input" value={token} onChange={e => setToken(e.target.value)} autoComplete="off" spellCheck={false} required /></div>}
        <div className="field"><label htmlFor="login-email">{t("邮箱")}</label><input id="login-email" className="input" type="email" value={email} onChange={e => setEmail(e.target.value)} autoComplete="username" inputMode="email" autoCapitalize="off" spellCheck={false} autoFocus required /></div>
        {bootstrap && <div className="field"><label htmlFor="bootstrap-name">{t("显示名称（可选）")}</label><input id="bootstrap-name" className="input" value={name} onChange={e => setName(e.target.value)} autoComplete="name" /></div>}
        <PasswordField value={password} onChange={setPassword} fresh={bootstrap} />
        {!bootstrap && <div className="row wrap" style={{ justifyContent: "space-between" }}><a className="authfoot" href="/forgot-password">{t("忘记口令？")}</a></div>}
        {!bootstrap && rememberBox}
        {!bootstrap && loginChallenge.element}
        <button className="btn primary" disabled={action.busy}>{t(action.busy ? "处理中…" : bootstrap ? "创建管理员并登录" : "登录")}</button>
        {!bootstrap && <>
          <div className="flow-divider">{t("其他登录方式")}</div>
          <div className="flow-alt">
            {typeof window.PublicKeyCredential !== "undefined" && <button type="button" className="btn" disabled={action.busy} onClick={() => void signInWithPasskey()}>{t("使用安全密钥登录")}</button>}
            <button type="button" className="btn" disabled={action.busy} onClick={() => { setMode("code"); setPassword(""); action.setError(null); }}>{t("用邮箱验证码登录")}</button>
            {providers.map(p => <a key={p.id} className="btn flow-provider" href={externalUrl(p.id)} aria-label={t("使用 {label} 登录", { label: p.label })}><ProviderMark id={p.id} />{t("使用 {label} 登录", { label: p.label })}</a>)}
          </div>
        </>}
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

/** Provider marks are the providers' own identifiers, drawn simply and monochrome: the button
 *  is a Synwevo control, the name says which provider, and no third-party artwork is embedded. */
export function ProviderMark({ id }: { id: string }) {
  const letter = id === "google" ? "G" : id === "microsoft" ? "M" : id.slice(0, 1).toUpperCase();
  return <span className="flow-provider-mark" aria-hidden="true">{letter}</span>;
}
