import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider } from "./lib/ui";
import { initTheme } from "./lib/theme";
import Home from "./pages/Home";
import Workspace from "./pages/Workspace";
import JobPage from "./pages/JobPage";
import AboutPage from "./pages/About";
import RegressionPage from "./pages/Regression";
import Capabilities from "./pages/Capabilities";
import "./styles.css";

const DocEditor = lazy(() => import("./pages/DocEditor"));
const SheetEditor = lazy(() => import("./pages/SheetEditor"));

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <DialogProvider>
        <BrowserRouter>
          <Suspense fallback={<div className="empty">加载中…</div>}>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/capabilities" element={<Capabilities />} />
              <Route path="/about" element={<AboutPage />} />
              <Route path="/regression" element={<RegressionPage />} />
              <Route path="/p/:pid" element={<Workspace />} />
              <Route path="/p/:pid/jobs/:jid" element={<JobPage />} />
              <Route path="/templates/new/doc" element={<DocEditor />} />
              <Route path="/templates/new/sheet" element={<SheetEditor />} />
              <Route path="/templates/:tid/edit" element={<EditorSwitch />} />
              <Route path="*" element={<div className="empty"><div className="big">页面不存在</div><a href="/">返回首页</a></div>} />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </DialogProvider>
    </ToastProvider>
  </StrictMode>,
);

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { get } from "./lib/api";

function EditorSwitch() {
  const { tid } = useParams();
  const [kind, setKind] = useState<string | null>(null);
  useEffect(() => { get(`/api/templates/${tid}`).then((t) => setKind(t.kind)).catch(() => setKind("error")); }, [tid]);
  if (!kind) return <div className="empty">加载中…</div>;
  if (kind === "builtin_doc") return <DocEditor />;
  if (kind === "builtin_sheet") return <SheetEditor />;
  return <div className="empty">该模板不是内置编辑器创建的，请在项目中直接在预览上设置字段。</div>;
}
