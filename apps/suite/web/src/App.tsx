/** The shell's tree. Public pages (a form's fill-in link) are matched first and rendered without
 *  the session and the application catalogue: those ask "who is signed in" and "which apps may
 *  they open", which a person without an account can only be refused (a 401 in their console on
 *  every load). Everything else is a signed-in page under both. */
import { Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, I18nProvider, PageState, StatusPage, ToastProvider, useI18n } from "@synwevo/ui";
import { SessionProvider, SignedOut } from "@synwevo/auth-client";
import { CatalogProvider } from "./shared/catalog";
import { APP_ROUTES, PUBLIC_ROUTES } from "./routes";

function RouteLoading() {
  const { t } = useI18n();
  return <PageState>{t("加载中…")}</PageState>;
}

function NotFound() {
  const { t } = useI18n();
  return <StatusPage brandHref="/" title={t("页面不存在")}>
    <p>{t("这个地址不存在。")}</p>
    <a className="btn" href="/workspace">{t("返回工作空间")}</a>
  </StatusPage>;
}

/** The signed-in pages, with the session and the catalogue. */
export function SignedInShell() {
  return <SessionProvider>
    <CatalogProvider>
      <Routes>
        {APP_ROUTES}
        <Route path="/signed-out" element={<SignedOut product="Workspace" />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </CatalogProvider>
  </SessionProvider>;
}

export function App() {
  return <I18nProvider title="Synwevo">
    <ToastProvider>
      <DialogProvider>
        <BrowserRouter>
          <Suspense fallback={<RouteLoading />}>
            <Routes>
              {PUBLIC_ROUTES}
              <Route path="*" element={<SignedInShell />} />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </DialogProvider>
    </ToastProvider>
  </I18nProvider>;
}
