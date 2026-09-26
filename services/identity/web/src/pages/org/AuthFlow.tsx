import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { ApiError, safeNext, useSession } from "@synwevo/auth-client";
import { Brand, LanguageMenu, Logo, hasMessage, useI18n, type Locale, type Translator } from "@synwevo/ui";
import { passwordProblem } from "../../lib/password";
import "../../auth-flow.messages";
import "./flow.css";

export interface FlowIntent { intent?: "personal" | "organization"; invite_token?: string; email?: string; next?: string }
const FLOW_KEY = "synwevo.auth.intent";
export function readIntent(): FlowIntent {
  try { return JSON.parse(sessionStorage.getItem(FLOW_KEY) || "{}") || {}; } catch { return {}; }
}
export function saveIntent(value: FlowIntent) {
  try { sessionStorage.setItem(FLOW_KEY, JSON.stringify({ ...readIntent(), ...value })); } catch { /* The current page remains usable without storage. */ }
}
export function clearInvitation() { saveIntent({ invite_token: undefined }); }
// EmailSection consumes this once after sign-in; never put its capability into `next`.
export const EMAIL_CHANGE_BRIDGE_KEY = "synwevo.account.email-change.pending";
export function preserveEmailChangeForSignIn() {
  const url = new URL(window.location.href);
  if (url.pathname !== "/account") return;
  const fragment = new URLSearchParams(url.hash.slice(1));
  const params = fragment.has("token") ? fragment : url.searchParams;
  if (!params.has("token")) return;
  const token = params.get("token") || "";
  const action = params.get("action");
  // Scrub even malformed/unsupported capabilities before any login redirect is rendered.
  fragment.delete("token"); fragment.delete("action");
  url.searchParams.delete("token"); url.searchParams.delete("action"); url.hash = fragment.toString();
  window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  try { sessionStorage.removeItem(EMAIL_CHANGE_BRIDGE_KEY); } catch { /* A valid link below still requires writable tab storage. */ }
  if (action !== "email-change" || token.length < 20 || token.length > 256) {
    throw new Error("链接无效或已过期，请重新申请。");
  }
  try { sessionStorage.setItem(EMAIL_CHANGE_BRIDGE_KEY, JSON.stringify({ token, captured_at: Date.now() })); }
  catch { throw new Error("无法保存此验证链接。请先登录，再重新打开邮件链接。"); }
}
/** Hold the session gate until the capability has been moved out of the address. */
export function AccountCapabilityGate({ children }: { children: ReactNode }) {
  const location = useLocation(); const { t } = useI18n();
  const stamp = `${location.key}:${location.search}:${location.hash}`;
  const [captured, setCaptured] = useState<{ stamp: string; error: unknown } | null>(null);
  const processed = useRef<{ stamp: string; error: unknown } | null>(null);
  useEffect(() => {
    if (processed.current?.stamp !== stamp) {
      try { preserveEmailChangeForSignIn(); processed.current = { stamp, error: null }; }
      catch (error) { processed.current = { stamp, error }; }
    }
    setCaptured(processed.current);
  }, [stamp]);
  if (captured?.stamp !== stamp) return <Loading />;
  if (captured.error) return <AuthFrame title={t("无法继续邮箱验证")}>
    <FlowError error={captured.error} /><a className="btn primary" href="/login?next=%2Faccount">{t("登录并继续")}</a>
  </AuthFrame>;
  return <>{children}</>;
}
export function continuation(): string {
  const flow = readIntent();
  return flow.invite_token ? `/invite#token=${encodeURIComponent(flow.invite_token)}` : safeNext(flow.next || "/onboarding");
}
/** Keep bearer tokens out of subsequent navigation/referrers. Reads never consume a token. */
export function useLinkToken(name = "token", fallback = "") {
  const location = useLocation();
  const [token, setToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get(name)
    || new URLSearchParams(window.location.search).get(name) || fallback);
  useEffect(() => {
    function capture() {
      const url = new URL(window.location.href);
      const fragment = new URLSearchParams(url.hash.slice(1));
      if (fragment.has(name) || url.searchParams.has(name)) {
        setToken(fragment.get(name) ?? url.searchParams.get(name) ?? "");
        fragment.delete(name); url.searchParams.delete(name); url.hash = fragment.toString();
        window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
      }
    }
    // A mail link may navigate within an already mounted page. Capture before erasing,
    // and retain the in-memory token when the cleaned URL is observed again.
    capture();
    window.addEventListener("hashchange", capture);
    window.addEventListener("popstate", capture);
    return () => { window.removeEventListener("hashchange", capture); window.removeEventListener("popstate", capture); };
  }, [name, location.key, location.search, location.hash]);
  return token;
}
export type FlowUser = { email?: string; email_verified?: boolean; status?: string; onboarding_completed?: boolean; workspaces?: unknown[] };
export function needsVerification(user: FlowUser | null | undefined): boolean {
  return !!user && (user.email_verified === false || user.status === "pending" || user.status === "pending_verification");
}
export function signedInDestination(user: FlowUser, next: string): string {
  if (needsVerification(user)) return "/verify-email";
  if (readIntent().invite_token) return continuation();
  if (user.onboarding_completed === false || user.workspaces?.length === 0) return "/onboarding";
  return next;
}
/** Start a new RP handoff so its server-bound choice replaces any stale portal tab context. */
export function portalWorkspaceUrl(portalUrl: string | undefined, workspaceId: string, locale: Locale): string {
  if (!portalUrl) return "/account";
  const url = new URL("/auth/start", portalUrl);
  url.search = new URLSearchParams({ next: "/workspace", workspace_id: workspaceId, lang: locale }).toString();
  return url.href;
}

export function AuthFrame({ title, description, children, wide = false }: {
  title: string; description?: string; children: ReactNode; wide?: boolean;
}) {
  const { t } = useI18n();
  const { state } = useSession();
  const headingId = useId();
  useEffect(() => { document.title = `${title} · Synwevo Account`; }, [title]);
  return <main className={`flow-page${wide ? " flow-wide" : ""}`}>
    <header className="flow-top"><Brand href="/login" product="Account" size="lg" /><LanguageMenu /></header>
    <div className="flow-layout">
      <aside className="flow-intro"><span className="flow-eyebrow">{state?.platform_name || "Synwevo Workspace"}</span>
        <h2>{t("一个账户，连接你的工作。")}</h2>
        <p>{t("从个人空间到团队协作，让每一次开始都井然有序。")}</p>
        <div className="flow-art" aria-hidden="true"><span>01</span><div /><span>02</span><div /><span>03</span></div>
        <p className="flow-caption">{t("你的账户。你的空间。你的团队。")}</p>
      </aside>
      <section className="flow-card" aria-labelledby={headingId}>
        <header className="flow-heading"><h1 id={headingId}>{title}</h1>{description && <p>{description}</p>}</header>
        {children}
      </section>
    </div>
    <footer className="flow-footer"><Logo version="wordmark" height={13} /><span aria-hidden="true">/</span> {t("为专注的工作留出空间")}</footer>
  </main>;
}

const ERROR_SOURCES: Record<string, string> = {
  network: "无法连接服务器，请检查网络后重试。",
  bad_credentials: "邮箱或口令不正确。", login_failed: "邮箱或口令不正确。",
  unauthorized: "请先登录后继续。", sign_in_required: "请先登录后继续。",
  ticket_invalid: "验证已超时，请返回并重新登录。", bad_code: "验证码不正确，请重试。",
  weak_password: "这个口令不符合要求，请使用更长且不包含个人信息的口令。",
  bad_email: "请输入有效的邮箱地址。", registration_closed: "此服务暂未开放注册。",
  registration_disabled: "此服务暂未开放注册。", email_not_verified: "请先验证邮箱后继续。",
  verification_required: "请先验证邮箱后继续。", email_verification_required: "请先验证邮箱后继续。",
  wrong_account: "当前登录账户与邀请邮箱不一致，请退出后重试。",
  bad_invite: "邀请链接无效，请使用最新的邀请邮件。",
  invite_expired: "邀请已过期，请让邀请人重新发送。", invite_revoked: "邀请已被撤销，请使用最新的邀请邮件。",
  invite_accepted: "此邀请已被接受。", bad_token: "链接无效或已过期，请重新申请。",
  forbidden: "你没有执行此操作的权限。", not_found: "找不到此内容，或你没有访问权限。",
  challenge_required: "请先完成人机验证。", challenge_failed: "人机验证未通过，请重试。",
  challenge_unavailable: "人机验证服务暂不可用，请稍后重试。",
};
export function errorText(error: unknown, t: Translator): string {
  if (error instanceof ApiError) {
    if (error.code === "bad_code") {
      const left = /还可尝试 (\d+) 次/.exec(error.originalMessage);
      if (left) return t("验证码不正确，还可尝试 {count} 次。", { count: Number(left[1]) });
      if (error.originalMessage.includes("请重新登录")) return t("验证码不正确，请重新登录。");
    }
    if (error.code === "uv_required") return t("此账户需要设备验证，请使用 PIN、指纹或面容，或改用口令和验证码登录。");
    const source = error.status === 429 ? "尝试过于频繁，请稍后再试。" : ERROR_SOURCES[error.code];
    let message = hasMessage(error.originalMessage) ? t(error.originalMessage) : source ? t(source) : error.message;
    if (error.retryAfter) message += ` ${t("请在 {seconds} 秒后重试。", { seconds: error.retryAfter })}`;
    return message;
  }
  return error instanceof Error ? t(error.message) : t("操作失败，请重试。");
}
export function FlowError({ error }: { error: unknown }) {
  const { t } = useI18n(); const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) ref.current?.focus(); }, [error]);
  return error ? <div ref={ref} className="notice err flow-error" role="alert" tabIndex={-1}>
    {errorText(error, t)}{error instanceof ApiError && error.requestId && !errorText(error, t).includes(error.requestId) && <div className="tiny">{t("请求编号：{id}", { id: error.requestId })}</div>}
  </div> : null;
}
/** A synchronous lock also covers two submissions before React has rendered busy=true. */
export function useAction() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null); const lock = useRef(false);
  async function run(action: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(null);
    try { await action(); } catch (e) { setError(e); } finally { lock.current = false; setBusy(false); }
  }
  return { busy, error, setError, run };
}
export function checkPassword(password: string, email?: string, name?: string) {
  const problem = passwordProblem(password, { email, name });
  if (problem?.startsWith("口令至少需要")) throw new Error("口令至少需要 15 个字符。");
  if (problem?.startsWith("口令过长")) throw new Error("口令最多可以有 1024 个字符。");
  if (problem) throw new Error(problem);
}
export function PasswordField({ value, onChange, label, fresh = true, autoFocus = false, disabled = false }: {
  value: string; onChange: (value: string) => void; label?: string; fresh?: boolean; autoFocus?: boolean; disabled?: boolean;
}) {
  const { t } = useI18n(); const id = useId(); const [show, setShow] = useState(false);
  return <div className="field"><label htmlFor={id}>{label || t("口令")}</label><div className="authpw">
    <input id={id} className="input" type={show ? "text" : "password"} autoComplete={fresh ? "new-password" : "current-password"}
      value={value} onChange={e => onChange(e.target.value)} required autoFocus={autoFocus} disabled={disabled}
      aria-describedby={fresh ? `${id}-hint` : undefined} />
    <button type="button" className="btn ghost authshow" aria-controls={id} aria-pressed={show}
      aria-label={t(show ? "隐藏口令" : "显示口令")} onClick={() => setShow(v => !v)}>{t(show ? "隐藏" : "显示")}</button>
  </div>{fresh && <span id={`${id}-hint`} className="tiny muted">{t("至少 15 个字符。使用一句容易记住的长句子。")}</span>}</div>;
}
export function Loading({ children }: { children?: ReactNode }) {
  const { t } = useI18n(); return <p className="flow-status" role="status">{children || t("加载中…")}</p>;
}
