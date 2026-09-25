/**
 * The account service's own shell.
 *
 * A third entry rather than a route inside Synwevo Docs' app. The account service must not serve
 * the document workbench: its API refuses those calls by role, so a shared shell would render
 * a project list that then fills with "this endpoint is not on the account service" — which is
 * a correct refusal presented as a broken page.
 *
 * Only what the account service is actually for: signing in, the account and security centre,
 * accepting an invitation, and the pages the redirect chain lands on.
 */
import { StrictMode, Suspense, lazy, type ComponentType, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider, initTheme, I18nProvider, StatusPage, useI18n, registerMessages } from "@synwevo/ui";
import { RequireSession, SessionProvider, useSession } from "@synwevo/auth-client";
import Login from "./pages/Login";
import { AccountCapabilityGate } from "./pages/org/AuthFlow";

/* Route-level code splitting. The sign-in page is what most visits start with, and it used to
 * download the whole shell — platform administration, the API console, member governance, the
 * MFA enrolment with its QR renderer — 710 KB of JavaScript before the first field could be
 * typed into. Only Login (and the two status pages) stay in the entry chunk; every other page
 * loads when its route is entered. A chunk that fails to load right after a deployment (the
 * page in the tab still names the previous build's file) reloads the page once, which fetches
 * the current HTML and its chunks; a second failure surfaces as an error. */
function lazyRoute<T extends ComponentType<any>>(load: () => Promise<{ default: T }>): T {
  return lazy(() => load().catch((error: unknown) => {
    const key = "synwevo.account.chunk-reload";
    let already = false;
    try { already = sessionStorage.getItem(key) === window.location.pathname; } catch { /* storage unavailable: no retry */ }
    if (!already) {
      try { sessionStorage.setItem(key, window.location.pathname); } catch { /* ignore */ }
      window.location.reload();
      return new Promise<never>(() => {});   // the reload takes over; never resolve
    }
    throw error;
  })) as unknown as T;
}
const pick = <M, K extends keyof M>(load: () => Promise<M>, name: K) => () => load().then((m) => ({ default: m[name] as unknown as ComponentType<any> }));
const Account = lazyRoute(() => import("./pages/Account"));
const AccountRecovery = lazyRoute(pick(() => import("./pages/Account"), "AccountRecovery"));
const Invite = lazyRoute(() => import("./pages/Invite"));
const ForgotPassword = lazyRoute(pick(() => import("./pages/Recover"), "ForgotPassword"));
const ResetPassword = lazyRoute(pick(() => import("./pages/Recover"), "ResetPassword"));
const Admin = lazyRoute(() => import("./Admin"));
const Members = lazyRoute(() => import("./pages/Members"));
const Register = lazyRoute(() => import("./pages/Register"));
const VerifyEmail = lazyRoute(() => import("./pages/VerifyEmail"));
const Onboarding = lazyRoute(() => import("./pages/Onboarding"));
const Organizations = lazyRoute(() => import("./pages/Organizations"));
const ApiPlatform = lazyRoute(() => import("./pages/ApiPlatform"));
const OwnerApiApprovals = lazyRoute(pick(() => import("./pages/ApiPlatform"), "OwnerApiApprovals"));
import "./account.css";
import "@synwevo/ui/styles.css";

initTheme();
registerMessages({
  "已退出": {en:"Signed out", "zh-Hant":"已登出"},
  "当前设备的统一登录与关联应用会话已退出。其他设备不受影响。": {en:"You have signed out of Synwevo and linked app sessions on this device. Other devices are unchanged.", "zh-Hant":"目前裝置的統一登入與關聯應用程式工作階段已登出。其他裝置不受影響。"},
  "重新登录": {en:"Sign in again", "zh-Hant":"重新登入"},
  "页面不存在": {en:"Page not found", "zh-Hant":"找不到頁面"},
  "前往账户设置": {en:"Go to account settings", "zh-Hant":"前往帳戶設定"},
  "仅平台管理员可访问": {en:"Platform administrators only", "zh-Hant":"僅平台管理員可存取"},
  "这个地址在 Synwevo Account 里不存在。": {en:"This address does not exist in Synwevo Account.", "zh-Hant":"這個地址在 Synwevo Account 裡不存在。"},
  "加载中…": {en:"Loading…", "zh-Hant":"載入中…"},
});

function RouteLoading() {
  const {t} = useI18n();
  return <div className="empty" role="status" aria-live="polite">{t("加载中…")}</div>;
}
function AdminOnly({children}: {children: ReactNode}) {
  const {me} = useSession(); const {t} = useI18n();
  // The role, not the current permission set: an administrator whose session proved one factor
  // (`admin_locked`) must reach the page to see what is missing and confirm the password there;
  // the page itself withholds every management view until then, and the server answers 403.
  return me?.platform_role === "admin" ? children : <div className="empty" role="alert">{t("仅平台管理员可访问")}</div>;
}

function SignedOut() {
  const { t } = useI18n();
  return <StatusPage product="Account" brandHref="/login" title={t("已退出")}>
    <p>{t("当前设备的统一登录与关联应用会话已退出。其他设备不受影响。")}</p>
    <a className="btn primary" href="/login">{t("重新登录")}</a>
  </StatusPage>;
}
function NotFound() {
  const {t} = useI18n();
  return <StatusPage product="Account" brandHref="/account" title={t("页面不存在")}>
    <p>{t("这个地址在 Synwevo Account 里不存在。")}</p>
    <a className="btn" href="/account">{t("前往账户设置")}</a>
  </StatusPage>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider title="Synwevo Account">
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
          <BrowserRouter>
            <Suspense fallback={<RouteLoading />}>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route path="/verify-email" element={<VerifyEmail />} />
              <Route path="/onboarding" element={<Onboarding />} />
              <Route path="/organizations" element={<RequireSession><Organizations /></RequireSession>} />
              <Route path="/organizations/:oid" element={<RequireSession><Organizations /></RequireSession>} />
              <Route path="/invite" element={<Invite />} />
              {/* Both render signed out by necessity: someone who has forgotten their password
                  cannot sign in to ask for a way back. */}
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              <Route path="/logged-out" element={<SignedOut />} />
              <Route path="/account/workspaces/:wid/members" element={<RequireSession><Members /></RequireSession>} />
              <Route path="/account" element={<AccountCapabilityGate><RequireSession><Account /></RequireSession></AccountCapabilityGate>} />
              <Route path="/account/recovery" element={<AccountRecovery />} />
              <Route path="/account/api-approvals" element={<RequireSession><OwnerApiApprovals /></RequireSession>} />
              {/* The route exists for everyone; the server answers 404 to a non-administrator,
                  and the page renders that same answer. Access control is on the route, not on
                  whether a link is drawn. */}
              <Route path="/admin" element={<RequireSession><AdminOnly><Admin /></AdminOnly></RequireSession>} />
              <Route path="/admin/api-platform" element={<RequireSession><AdminOnly><ApiPlatform /></AdminOnly></RequireSession>} />
              {/* There is no home page here. Someone who arrives at the root either has an
                  account to manage or needs to sign in; both are one redirect away. */}
              <Route path="/" element={<RequireSession><Navigate to="/account" replace /></RequireSession>} />
              <Route path="*" element={<NotFound />} />
            </Routes>
            </Suspense>
          </BrowserRouter>
        </DialogProvider>
      </SessionProvider>
    </ToastProvider>
    </I18nProvider>
  </StrictMode>,
);
