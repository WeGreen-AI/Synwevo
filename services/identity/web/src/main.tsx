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
import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider, initTheme, I18nProvider, LanguageMenu, useI18n, registerMessages } from "@synwevo/ui";
import { RequireSession, SessionProvider, useSession } from "@synwevo/auth-client";
import Login from "./pages/Login";
import Account, { AccountRecovery } from "./pages/Account";
import Invite from "./pages/Invite";
import { ForgotPassword, ResetPassword } from "./pages/Recover";
import Admin from "./Admin";
import Members from "./pages/Members";
import Register from "./pages/Register";
import VerifyEmail from "./pages/VerifyEmail";
import Onboarding from "./pages/Onboarding";
import Organizations from "./pages/Organizations";
import { AccountCapabilityGate } from "./pages/org/AuthFlow";
import ApiPlatform, { OwnerApiApprovals } from "./pages/ApiPlatform";
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
});

function AdminOnly({children}: {children: ReactNode}) {
  const {me} = useSession(); const {t} = useI18n();
  return me?.permissions?.includes("api_platform.manage") ? children : <div className="empty" role="alert">{t("仅平台管理员可访问")}</div>;
}

function SignedOut() {
  const { t } = useI18n();
  return (
    <div className="authpage">
      <div className="authcard">
        <LanguageMenu /><h1>{t("已退出")}</h1>
        <p className="authlede">
          {t("当前设备的统一登录与关联应用会话已退出。其他设备不受影响。")}
        </p>
        <a className="btn primary" href="/login">{t("重新登录")}</a>
      </div>
    </div>
  );
}
function NotFound() {
  const {t} = useI18n();
  return <div className="empty"><LanguageMenu /><div className="big">{t("页面不存在")}</div><a href="/account">{t("前往账户设置")}</a></div>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider title="Synwevo Account">
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
          <BrowserRouter>
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
          </BrowserRouter>
        </DialogProvider>
      </SessionProvider>
    </ToastProvider>
    </I18nProvider>
  </StrictMode>,
);
