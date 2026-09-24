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
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider, initTheme } from "@synwevo/ui";
import { RequireSession, SessionProvider, useSession } from "@synwevo/auth-client";
import Login from "./pages/Login";
import Account from "./pages/Account";
import Invite from "./pages/Invite";
import { ForgotPassword, ResetPassword } from "./pages/Recover";
import Admin from "./Admin";
import "@synwevo/ui/styles.css";

initTheme();

function SignedOut() {
  const { state } = useSession();
  const name = state?.platform_name || "Synwevo Workspace";
  return (
    <div className="authpage">
      <div className="authcard">
        <h1>已退出</h1>
        <p className="authlede">
          你已从{name}和所有已关联的应用中退出。关闭此页面即可，或再次登录。
        </p>
        <a className="btn primary" href="/login">重新登录</a>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route path="/invite" element={<Invite />} />
              {/* Both render signed out by necessity: someone who has forgotten their password
                  cannot sign in to ask for a way back. */}
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password" element={<ResetPassword />} />
              <Route path="/logged-out" element={<SignedOut />} />
              <Route path="/account" element={<RequireSession><Account /></RequireSession>} />
              {/* The route exists for everyone; the server answers 404 to a non-administrator,
                  and the page renders that same answer. Access control is on the route, not on
                  whether a link is drawn. */}
              <Route path="/admin" element={<RequireSession><Admin /></RequireSession>} />
              {/* There is no home page here. Someone who arrives at the root either has an
                  account to manage or needs to sign in; both are one redirect away. */}
              <Route path="/" element={<RequireSession><Navigate to="/account" replace /></RequireSession>} />
              <Route path="*" element={
                <div className="empty">
                  <div className="big">页面不存在</div>
                  <a href="/account">前往账户设置</a>
                </div>
              } />
            </Routes>
          </BrowserRouter>
        </DialogProvider>
      </SessionProvider>
    </ToastProvider>
  </StrictMode>,
);
