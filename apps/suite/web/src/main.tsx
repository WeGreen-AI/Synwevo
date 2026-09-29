/**
 * The business applications' shell on the workplace origin: Forms, Tables, DataPrep, Approvals,
 * Extract and the personal Agent. One build, one route tree per application; each application is
 * loaded on demand and never imports another's pages (scripts/check_boundaries.py).
 */
import { StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, I18nProvider, PageState, StatusPage, ToastProvider, initTheme, useI18n } from "@synwevo/ui";
import { SessionProvider, SignedOut } from "@synwevo/auth-client";
import { CatalogProvider } from "./shared/catalog";
import { APP_ROUTES } from "./routes";
import "@synwevo/ui/styles.css";
import "./suite.css";
import "./shared/messages";

initTheme();

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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <I18nProvider title="Synwevo">
      <ToastProvider>
        <SessionProvider>
          <DialogProvider>
            <CatalogProvider>
              <BrowserRouter>
                <Suspense fallback={<RouteLoading />}>
                  <Routes>
                    {APP_ROUTES}
                    <Route path="/signed-out" element={<SignedOut product="Workspace" />} />
                    <Route path="*" element={<NotFound />} />
                  </Routes>
                </Suspense>
              </BrowserRouter>
            </CatalogProvider>
          </DialogProvider>
        </SessionProvider>
      </ToastProvider>
    </I18nProvider>
  </StrictMode>,
);
