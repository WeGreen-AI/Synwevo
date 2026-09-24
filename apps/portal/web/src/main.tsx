import React from "react";
import { createRoot } from "react-dom/client";
import "@synwevo/ui/styles.css";
import "./portal.css";
import { Portal } from "./Portal";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Portal />
  </React.StrictMode>,
);
