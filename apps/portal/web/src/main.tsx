import React from "react";
import { createRoot } from "react-dom/client";
import "@synwevo/ui/styles.css";
import "./portal.css";
import { SessionProvider } from "@synwevo/auth-client";
import { I18nProvider, setLocale } from "@synwevo/ui";
import { Portal } from "./Portal";

// A public address names its language first: /en, /zh-Hant/privacy, /en/service …
const pathLocale = window.location.pathname.split("/")[1];
if (pathLocale === "en" || pathLocale === "zh-Hans" || pathLocale === "zh-Hant") setLocale(pathLocale);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider><SessionProvider><Portal /></SessionProvider></I18nProvider>
  </React.StrictMode>,
);
