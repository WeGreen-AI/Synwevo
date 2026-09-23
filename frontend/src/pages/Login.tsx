import { useEffect, useRef, useState } from "react";
import { ApiError, post } from "../lib/api";
import { safeNext, useSession } from "../lib/session";

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
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const errRef = useRef<HTMLDivElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  const bootstrap = !!state?.bootstrap_required;

  useEffect(() => { if (disabled || me) window.location.assign(next); }, [me, disabled, next]);
  useEffect(() => { emailRef.current?.focus(); }, [bootstrap]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;                       // a second Enter must not send a second request
    setBusy(true);
    setErr(null);
    try {
      if (bootstrap) await post("/api/auth/bootstrap", { token: token.trim(), email, password, display_name: name || null });
      else await post("/api/auth/login", { email, password });
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

  if (disabled || me) return <div className="empty">正在前往…</div>;

  return (
    <div className="authpage">
      <form className="authcard" onSubmit={submit} noValidate>
        <div className="authbrand"><span className="logo">W</span>DocWeave</div>

        {bootstrap ? (
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

        {!bootstrap && state?.registration === "invite" && (
          <p className="tiny muted authfoot">本服务目前为邀请制，账户由管理员或工作空间所有者邀请创建。</p>
        )}
        {!bootstrap && state?.registration === "closed" && (
          <p className="tiny muted authfoot">本服务已关闭新账户注册。</p>
        )}
      </form>
    </div>
  );
}
