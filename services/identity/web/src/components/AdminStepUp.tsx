import { useRef, useState } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { registerMessages, useI18n } from "@synwevo/ui";
import { refusalText } from "../lib/refusals";
import { ReauthClientError, reauthenticateWithPasskey, useReauthMethods, type ReauthMethod } from "../lib/reauth";

registerMessages({
  "你是平台管理员，但本次登录（{method}）只验证了一个因素。完成近期身份验证后，才能使用平台管理和 API 管理；普通账户功能不受影响。": {
    en: "You are a platform administrator, but this sign-in ({method}) proved one factor only. Confirm your identity before using platform or API management; ordinary account features are unaffected.",
    "zh-Hant": "你是平台管理員，但本次登入（{method}）只驗證了一個因素。完成近期身分驗證後，才能使用平台管理和 API 管理；一般帳戶功能不受影響。" },
  "当前登录只验证了一个因素": { en: "This sign-in proved one factor only", "zh-Hant": "目前登入只驗證了一個因素" },
  "你是平台管理员，但本次登录（{method}）只验证了一个因素。输入账户口令确认身份后，才能使用平台管理和 API 管理；普通账户功能不受影响。": {
    en: "You are a platform administrator, but this sign-in ({method}) proved one factor only. Enter your account password to confirm it is you before using platform or API management; ordinary account features are unaffected.",
    "zh-Hant": "你是平台管理員，但本次登入（{method}）只驗證了一個因素。輸入帳戶口令確認身分後，才能使用平台管理和 API 管理；一般帳戶功能不受影響。" },
  "邮箱验证码": { en: "e-mail code", "zh-Hant": "電郵驗證碼" },
  "外部账户登录": { en: "external account sign-in", "zh-Hant": "外部帳戶登入" },
  "账户口令": { en: "Account password", "zh-Hant": "帳戶口令" },
  "确认身份": { en: "Confirm identity", "zh-Hant": "確認身分" },
  "已确认，平台管理已可使用。": { en: "Confirmed. Platform management is available.", "zh-Hant": "已確認，平台管理已可使用。" },
  "口令不正确。": { en: "The password is not correct.", "zh-Hant": "口令不正確。" },
  "这个账户没有设置口令。请用已绑定的安全密钥或动态验证码重新登录。": { en: "This account has no password. Sign in again with a registered security key or authenticator code.", "zh-Hant": "這個帳戶沒有設定口令。請用已綁定的安全金鑰或動態驗證碼重新登入。" },
  "前往账户页确认身份": { en: "Confirm your identity on the account page", "zh-Hant": "前往帳戶頁確認身分" },
});

/** The step-up prompt for a platform administrator whose session proved one factor.
 *
 *  Mirrors the server rule in `sessions.admin_capable`: the role is real, the management
 *  routes answer 403 `step_up_required` until a second, distinct factor is on the session. The
 *  password or user-verified Passkey ceremony proves that factor and records it on `amr`. The page
 *  does not hide the role or pretend the person is not an administrator; it says what is
 *  missing and offers the one step that completes it. */
export default function AdminStepUp({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const session = useSession();
  const me = session.me;
  const reauth = useReauthMethods(!compact && me?.admin_locked ? "admin" : null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  if (!me?.admin_locked) return done ? <p className="notice ok" role="status">{t("已确认，平台管理已可使用。")}</p> : null;
  const amr = me.session?.amr || "";
  const method = amr.startsWith("idp:") ? t("外部账户登录") : t("邮箱验证码");
  if (compact) {
    return <section className="notice warn col gap8" role="region" aria-label={t("当前登录只验证了一个因素")}>
      <b>{t("当前登录只验证了一个因素")}</b>
      <span>{t("你是平台管理员，但本次登录（{method}）只验证了一个因素。完成近期身份验证后，才能使用平台管理和 API 管理；普通账户功能不受影响。", { method })}</span>
      <a className="btn primary" href="/account#step-up" style={{ alignSelf: "flex-start" }}>{t("前往账户页确认身份")}</a>
    </section>;
  }
  const verify = async (method: ReauthMethod) => {
    if (busy || !reauth.methods?.includes(method) || method === "password" && !password) return;
    setBusy(true); setError(null);
    try {
      if (method === "passkey_uv") await reauthenticateWithPasskey("admin");
      else await post("/api/auth/reauth", { password, scope: "admin" });
      setPassword(""); setDone(true);
      await session.refresh();
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      // Not every 401 here is a wrong password: after too many the sign-in itself has ended.
      setError(code === "no_password" ? t("这个账户没有设置口令。请用已绑定的安全密钥或动态验证码重新登录。")
        : err instanceof ApiError ? refusalText(err, { bad_credentials: "口令不正确。" }) : err instanceof ReauthClientError ? err.message : String(err));
      requestAnimationFrame(() => input.current?.focus());
    } finally { setBusy(false); }
  };
  return <section id="step-up" className="card card-b col gap12 account-section" aria-labelledby="step-up-title">
    <h2 id="step-up-title" className="secttl">{t("当前登录只验证了一个因素")}</h2>
    <p className="muted">{t("你是平台管理员，但本次登录（{method}）只验证了一个因素。完成近期身份验证后，才能使用平台管理和 API 管理；普通账户功能不受影响。", { method })}</p>
    {!reauth.methods && !reauth.error && <p role="status">{t("正在读取可用的身份验证方式…")}</p>}
    {reauth.error != null && <div role="alert"><p>{reauth.error instanceof ApiError ? refusalText(reauth.error) : String(reauth.error)}</p><button type="button" className="btn sm" onClick={reauth.reload}>{t("重新读取验证方式")}</button></div>}
    {reauth.methods?.length === 0 && <p role="alert">{t("此账户没有可用的近期身份验证方式。请在账户页检查已登记的验证方式。")}</p>}
    {reauth.methods?.includes("password") && <form className="row gap8 wrap" onSubmit={(e) => { e.preventDefault(); void verify("password"); }}>
      <div className="field grow" style={{ maxWidth: 360 }}>
        <label htmlFor="step-up-password">{t("账户口令")}</label>
        <input id="step-up-password" ref={input} className="input" type="password" autoComplete="current-password" value={password}
          onChange={(e) => setPassword(e.target.value)} aria-invalid={error ? true : undefined} aria-describedby={error ? "step-up-error" : undefined} />
      </div>
      <button type="submit" className="btn primary" disabled={busy || !password} style={{ alignSelf: "flex-end" }}>{t("确认身份")}</button>
    </form>}
    {reauth.methods?.includes("passkey_uv") && <button type="button" className="btn primary" disabled={busy}
      onClick={() => void verify("passkey_uv")} style={{ alignSelf: "flex-start" }}>{t("使用安全密钥确认并继续")}</button>}
    {error && <p id="step-up-error" className="notice err" role="alert">{error}</p>}
  </section>;
}
