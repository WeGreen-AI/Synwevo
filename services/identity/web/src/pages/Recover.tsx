/**
 * Forgetting a password, and setting a new one.
 *
 * Two screens that share one rule: **the page never says whether an address has an account.**
 * The request screen shows the same confirmation either way, because a screen that said "no
 * such account" would be an account-existence oracle anyone could query.
 *
 * The reset screen reads the token without spending it, then spends it only when the person
 * submits. Mail security scanners follow links; a token consumed by a scanner's GET is a reset
 * the person never asked for and can no longer do.
 */
import { useEffect, useState } from "react";
import { ApiError, get, post } from "@platform/auth-client";
import { useSession } from "@platform/auth-client";

export function ForgotPassword() {
  const { state } = useSession();
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await post("/api/auth/forgot", { email: email.trim() });
      setSent(true);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <div className="authpage">
        <div className="authcard">
          <h1>看看你的邮箱</h1>
          {/* Deliberately the same words whether or not an account exists. */}
          <p className="authlede">
            如果 <b>{email.trim()}</b> 有账户，我们已经把重置链接发过去了。链接 2 小时内有效，只能用一次。
          </p>
          <p className="tiny muted">没收到？检查垃圾邮件，或者确认这个邮箱就是注册时用的那个。</p>
          <a className="btn authsubmit" href="/login">返回登录</a>
        </div>
      </div>
    );
  }

  return (
    <div className="authpage">
      <form className="authcard" onSubmit={submit} noValidate>
        <div className="authbrand">
          <span className="logo" aria-hidden="true">◫</span>{state?.platform_name || "工作平台"}
        </div>
        <h1>重置口令</h1>
        <p className="authlede">输入你的邮箱，我们会发一个重置链接过去。</p>
        {err && <div className="notice err">{err}</div>}
        <label className="col gap4">
          <span>邮箱</span>
          <input className="input" type="email" value={email} autoComplete="username" autoFocus
                 onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <button className="btn primary authsubmit" type="submit" disabled={busy || !email.trim()}>
          {busy ? "发送中…" : "发送重置链接"}
        </button>
        <a className="tiny muted authfoot" href="/login">想起来了？返回登录</a>
      </form>
    </div>
  );
}

export function ResetPassword() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [target, setTarget] = useState<{ email: string } | null | "bad">(null);
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ mfa_required: boolean } | null>(null);

  useEffect(() => {
    if (!token) return setTarget("bad");
    // Reads only. The token is spent by the submit below, never by opening the page.
    get<{ email: string }>(`/api/auth/reset/preview?token=${encodeURIComponent(token)}`)
      .then(setTarget)
      .catch(() => setTarget("bad"));
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const out = await post<{ mfa_required: boolean }>("/api/auth/reset", { token, password });
      setDone(out);
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  }

  if (target === null) return <div className="empty">加载中…</div>;

  if (target === "bad") {
    return (
      <div className="authpage">
        <div className="authcard">
          <h1>链接无效</h1>
          <p className="authlede">这个重置链接已经过期、已经用过，或者被一封更新的邮件取代了。</p>
          <a className="btn primary authsubmit" href="/forgot-password">重新申请一封</a>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="authpage">
        <div className="authcard">
          <h1>口令已更新</h1>
          <p className="authlede">
            所有设备上的登录都已结束。
            {done.mfa_required && "你的两步验证没有变化，下次登录仍然需要它。"}
          </p>
          <a className="btn primary authsubmit" href="/login">去登录</a>
        </div>
      </div>
    );
  }

  return (
    <div className="authpage">
      <form className="authcard" onSubmit={submit} noValidate>
        <h1>设置新口令</h1>
        <p className="authlede">为 <b>{target.email}</b> 设置一个新口令。</p>
        {err && <div className="notice err">{err}</div>}
        <label className="col gap4">
          <span>新口令</span>
          <div className="row gap4">
            <input className="input grow" type={show ? "text" : "password"} value={password}
                   autoComplete="new-password" autoFocus minLength={15}
                   onChange={(e) => setPassword(e.target.value)} required />
            <button className="btn sm" type="button" onClick={() => setShow((v) => !v)}
                    aria-pressed={show} aria-label={show ? "隐藏口令" : "显示口令"}>
              {show ? "隐藏" : "显示"}
            </button>
          </div>
          <span className="tiny muted">至少 15 个字符。长句子比复杂符号更好记也更安全。</span>
        </label>
        <button className="btn primary authsubmit" type="submit" disabled={busy || !password}>
          {busy ? "处理中…" : "设置新口令并结束所有登录"}
        </button>
      </form>
    </div>
  );
}
