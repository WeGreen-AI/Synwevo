/**
 * Synwevo Message's own shell.
 *
 * A separate build on its own origin (message.synwevo.com in production, message.localhost here),
 * not a route in the account shell it moved out of in CI-66. The account service refuses the
 * conversation API by role, so a shared shell would render a page whose every call is a correct
 * refusal presented as a broken page — the same reason the workbench and the portal are their
 * own builds.
 *
 * Only what the application is for: the conversation list, a thread, and the two pages the
 * sign-on chain lands on. Sign-in, the account and security centre and the moderation of
 * reports stay on the account service; `/login` and `/account` are server redirects here.
 */
import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, I18nProvider, PageState, StatusPage, ToastProvider, initTheme, useI18n } from "@synwevo/ui";
import { RequireSession, SessionProvider, SignedOut } from "@synwevo/auth-client";
import Messages from "./Messages";
import ObjectConversation from "./pages/ObjectConversation";
import "@synwevo/ui/styles.css";
import "./messages.css";
import "./messages.messages";
import "./lib/refusals";

initTheme();

function RouteLoading() {
  const { t } = useI18n();
  return <PageState>{t("加载中…")}</PageState>;
}

function NotFound() {
  const { t } = useI18n();
  return <StatusPage product="Message" brandHref="/" title={t("页面不存在")}>
    <p>{t("这个地址在 Synwevo Message 里不存在。")}</p>
    <a className="btn" href="/">{t("返回会话列表")}</a>
  </StatusPage>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider title="Synwevo Message · 消息">
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
          <BrowserRouter>
            <Suspense fallback={<RouteLoading />}>
            <Routes>
              {/* The list and a thread. `/messages[/…]` are the addresses the pages had on the
                  account origin: bookmarks and mails written before the move still land, and on
                  the single-process deployment (one origin for every shell) this shell is served
                  under exactly those paths. */}
              <Route path="/" element={<RequireSession><Messages /></RequireSession>} />
              <Route path="/messages" element={<RequireSession><Messages /></RequireSession>} />
              <Route path="/c/:cid" element={<RequireSession><Messages /></RequireSession>} />
              <Route path="/messages/:cid" element={<RequireSession><Messages /></RequireSession>} />
              {/* The conversation about an application object (Docs links here; CI-73). The page
                  asks the server which conversation that is and moves to it. */}
              <Route path="/objects/:app/:kind/:id" element={<RequireSession><ObjectConversation /></RequireSession>} />
              {/* Where the account service's logout returns to after "退出登录" here ended the
                  platform session (auth-client signOut → /api/auth/sign-out?everywhere=true). */}
              <Route path="/signed-out" element={<SignedOut product="Message" />} />
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
