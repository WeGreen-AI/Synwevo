import { useEffect, useRef, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { safeNext, signInUrl, useSession } from "@synwevo/auth-client";
import { b64urlToBuf, toJSON } from "../components/PasskeySection";

/** Sign in, and — on a deployment that has never been set up — create the first administrator.
 *
 *  Both live on one screen because they are the same moment for the person in front of it: they
 *  opened the app and need to get in. Which form appears is decided by the server
 *  (`bootstrap_required`), not by anything the browser can choose.
 */
export default function Login() {
  const { me, state, refresh, disabled } = useSession();
  const next = safeNext(new URLSearchParams(window.location.search).get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [name, setName] = useState("");
  const [show, setShow] = useState(false);
  // Set when the password was right but a second factor is still owed.
  const [ticket, setTicket] = useState<string | null>(null);
  const [methods, setMethods] = useState<{ totp: boolean; recovery: boolean } | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const errRef = useRef<HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  const bootstrap = !!state?.bootstrap_required;

  useEffect(() => { if (disabled || me) window.location.assign(next); }, [me, disabled, next]);
  useEffect(() => { emailRef.current?.focus(); }, [bootstrap]);
  useEffect(() => { if (ticket) codeRef.current?.focus(); }, [ticket]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;                       // a second Enter must not send a second request
    setBusy(true);
    setErr(null);
    try {
      if (bootstrap) {
        await post("/api/auth/bootstrap", { token: token.trim(), email, password, display_name: name || null });
      } else if (ticket) {
        const body = useRecovery ? { ticket, recovery_code: code } : { ticket, code };
        const r = await post<{ recovery_remaining?: number }>("/api/auth/login/mfa", body);
        if (r.recovery_remaining !== undefined) {
          // Said at the moment it happens: one fewer way back in, and they are signed in now
          // and able to do something about it.
          sessionStorage.setItem("dw_recovery_notice", String(r.recovery_remaining));
        }
      } else {
        const r = await post<{ mfa_required?: boolean; ticket?: string; methods?: any }>(
          "/api/auth/login", { email, password });
        if (r.mfa_required) {
          setTicket(r.ticket!);
          setMethods(r.methods);
          setPassword("");
          setBusy(false);
          return;                       // second step, same page — no navigation in between
        }
      }
      await refresh();
      window.location.assign(next);
    } catch (e2: any) {
      const status = e2 instanceof ApiError ? e2.status : 0;
      setErr(status === 429
        ? "尝试过于频繁，请稍后再试。"
        : e2?.message || "登录失败，请重试。");
      // The password is cleared, the email is not: a wrong password does not mean the address
      // was wrong, and making someone retype it on every attempt is its own small punishment.
      setPassword("");
      // Announce to a screen reader and move focus so the message is not missed.
      requestAnimationFrame(() => errRef.current?.focus());
    } finally {
      setBusy(false);
    }
  };

  const passkeySupported = typeof window !== "undefined"
    && typeof window.PublicKeyCredential !== "undefined";

  async function signInWithPasskey() {
    setBusy(true);
    setErr(null);
    try {
      const options = await post<Record<string, unknown>>(
        "/api/auth/passkeys/authenticate/begin", {});
      const publicKey = "parseRequestOptionsFromJSON" in PublicKeyCredential
        ? (PublicKeyCredential as unknown as {
            parseRequestOptionsFromJSON: (o: unknown) => PublicKeyCredentialRequestOptions;
          }).parseRequestOptionsFromJSON(options)
        : { ...(options as unknown as PublicKeyCredentialRequestOptions),
            challenge: b64urlToBuf(options.challenge as string) };
      const cred = await navigator.credentials.get({ publicKey }) as PublicKeyCredential | null;
      if (!cred) throw new Error("没有收到安全密钥的响应");
      await post("/api/auth/login/passkey", { credential: toJSON(cred) });
      await refresh();
      window.location.assign(safeNext(new URLSearchParams(window.location.search).get("next")));
    } catch (e) {
      if (e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "AbortError")) {
        setErr("已取消，或没有可用于本站的安全密钥。");
      } else {
        setErr(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
    }
  }

  if (disabled || me) return <div className="empty">正在前往…</div>;

  // A relying party has no password of its own to check. Anyone who lands on /login here — from
  // a bookmark, or a link written before the platform existed — goes on to the account service
  // rather than being shown a form that cannot work.
  if (state && state.password_login === false) {
    window.location.replace(signInUrl(state, safeNext(new URLSearchParams(window.location.search).get("next"))));
    return <div className="empty">正在前往统一登录…</div>;
  }

  // On a dedicated account service this screen belongs to the platform; on the single-process
  // deployment it is still Synwevo Docs' own login, and calling it the platform there would be
  // the opposite mistake. The server says which it is — the client cannot deduce it from the URL.
  const platformSide = state?.role === "accounts" || state?.role === "portal";
  const brandName = platformSide ? state?.platform_name || "Synwevo Workspace" : "Synwevo Docs";
  const brandMark = platformSide ? "◫" : "S";

  return (
    <div className="authpage">
      <form className="authcard" onSubmit={submit} noValidate>
        {/* The account service signs people in to the whole platform, so it carries the
            platform's name. On a single-process deployment (role "all") it is still Synwevo Docs'
            own login screen, and calling it the platform there would be the opposite mistake. */}
        <div className="authbrand">
          <span className="logo" aria-hidden="true">{brandMark}</span>
          {brandName}
        </div>

        {ticket ? (
          <>
            <h1>两步验证</h1>
            <p className="authlede">
              {useRecovery
                ? "输入一个恢复码。每个恢复码只能用一次。"
                : "打开你的验证器应用，输入其中显示的 6 位数字。"}
            </p>
          </>
        ) : bootstrap ? (
          <>
            <h1>建立首位管理员</h1>
            <p className="authlede">
              这个部署还没有任何账户。请粘贴服务器上
              <code className="mono"> manage.py bootstrap token </code>
              生成的一次性令牌，并设置你自己的口令。
            </p>
          </>
        ) : (
          <>
            <h1>登录</h1>
            <p className="authlede">使用你的邮箱与口令继续。</p>
          </>
        )}

        {/* role=alert so the message is announced; tabIndex so focus can be moved to it */}
        {err && <div className="notice err autherr" role="alert" tabIndex={-1} ref={errRef}>{err}</div>}

        {ticket ? (
          <>
            <label className="field">
              <span>{useRecovery ? "恢复码" : "验证码"}</span>
              <input ref={codeRef} className="input mono" value={code}
                inputMode={useRecovery ? "text" : "numeric"}
                // one-time-code lets iOS and Android offer the code from the notification
                autoComplete="one-time-code" autoCapitalize="off" spellCheck={false}
                maxLength={useRecovery ? 24 : 6} required
                onChange={(e) => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))} />
            </label>
            <button className="btn primary authsubmit" type="submit"
              disabled={busy || (useRecovery ? code.trim().length < 6 : code.length !== 6)}>
              {busy ? "验证中…" : "验证并登录"}
            </button>
            {methods?.recovery && (
              <button type="button" className="btn ghost sm"
                onClick={() => { setUseRecovery((v) => !v); setCode(""); setErr(null); }}>
                {useRecovery ? "改用验证器应用的验证码" : "手机不在身边？使用恢复码"}
              </button>
            )}
            <button type="button" className="btn ghost sm"
              onClick={() => { setTicket(null); setCode(""); setUseRecovery(false); setErr(null); }}>
              ← 返回，换个账户登录
            </button>
          </>
        ) : (
          <>
        {bootstrap && (
          <label className="field">
            <span>一次性引导令牌</span>
            <input className="input" value={token} onChange={(e) => setToken(e.target.value)}
              autoComplete="off" spellCheck={false} required
              placeholder="从服务器终端复制" />
          </label>
        )}

        <label className="field">
          <span>邮箱</span>
          <input ref={emailRef} className="input" type="email" value={email} inputMode="email"
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username" autoCapitalize="off" spellCheck={false} required />
        </label>

        {bootstrap && (
          <label className="field">
            <span>显示名称<span className="muted">（可选）</span></span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
          </label>
        )}

        <label className="field">
          <span>口令</span>
          <div className="authpw">
            <input className="input" type={show ? "text" : "password"} value={password}
              onChange={(e) => setPassword(e.target.value)}
              // new-password on bootstrap so a manager offers to generate one rather than
              // filling an existing entry; current-password on sign-in so it fills.
              autoComplete={bootstrap ? "new-password" : "current-password"} required />
            <button type="button" className="btn ghost sm authshow" onClick={() => setShow((s) => !s)}
              aria-pressed={show} aria-label={show ? "隐藏口令" : "显示口令"}>
              {show ? "隐藏" : "显示"}
            </button>
          </div>
          {bootstrap && <span className="tiny muted">至少 15 个字符。长句子比复杂符号更好记也更安全。</span>}
        </label>

        <button className="btn primary authsubmit" type="submit" disabled={busy}>
          {busy ? "处理中…" : bootstrap ? "创建管理员并登录" : "登录"}
        </button>

        {!bootstrap && (
          <a className="tiny muted authfoot" href="/forgot-password">忘记口令？</a>
        )}

        {!bootstrap && passkeySupported && (
          <>
            <div className="author" aria-hidden="true"><span>或</span></div>
            <button className="btn authsubmit" type="button" disabled={busy}
                    onClick={() => void signInWithPasskey()}>
              使用安全密钥登录
            </button>
            {/* No email is typed first, and none is sent: the browser picks the credential and
                the server learns who it belongs to from the assertion. Asking for an address
                here would also answer "does this address have an account". */}
            <span className="tiny muted authfoot">不需要输入邮箱——设备会选择对应的账户。</span>
          </>
        )}

        {!bootstrap && state?.registration === "invite" && (
          <p className="tiny muted authfoot">本服务目前为邀请制，账户由管理员或工作空间所有者邀请创建。</p>
        )}
        {!bootstrap && state?.registration === "closed" && (
          <p className="tiny muted authfoot">本服务已关闭新账户注册。</p>
        )}
          </>
        )}
      </form>
    </div>
  );
}
