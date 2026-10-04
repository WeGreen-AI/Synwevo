/**
 * The business applications' shell on the workplace origin: Forms, Tables, DataPrep, Approvals,
 * Extract and the personal Agent. One build, one route tree per application; each application is
 * loaded on demand and never imports another's pages (scripts/check_boundaries.py).
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { initTheme } from "@synwevo/ui";
import { App } from "./App";
import "@synwevo/ui/styles.css";
import "./suite.css";
import "./shared/messages";

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
