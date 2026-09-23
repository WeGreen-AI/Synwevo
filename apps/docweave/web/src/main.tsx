import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider, initTheme } from "@platform/ui";
import { RequireSession, SessionProvider } from "@platform/auth-client";
import Home from "./pages/Home";
import Workspace from "./pages/Workspace";
import JobPage from "./pages/JobPage";
import AboutPage from "./pages/About";
import RegressionPage from "./pages/Regression";
import Capabilities from "./pages/Capabilities";
import "@platform/ui/styles.css";

const DocEditor = lazy(() => import("./pages/DocEditor"));
const SheetEditor = lazy(() => import("./pages/SheetEditor"));

initTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
        <BrowserRouter>
          <Suspense fallback={<div className="empty">加载中…</div>}>
            <Routes>
              {/* No /login, /account, /invite or password-recovery route here. Those pages
                  belong to the account service and exist exactly once, in its own shell —
                  which the server serves for these paths in every role, including the
                  single-process deployment where both shells sit on one origin. A second copy
                  inside the workbench is how a relying party ends up with a password box that
                  cannot work. */}
              <Route path="/" element={<RequireSession><Home /></RequireSession>} />
              <Route path="/capabilities" element={<RequireSession><Capabilities /></RequireSession>} />
              <Route path="/about" element={<RequireSession><AboutPage /></RequireSession>} />
              <Route path="/regression" element={<RequireSession><RegressionPage /></RequireSession>} />
              <Route path="/p/:pid" element={<RequireSession><Workspace /></RequireSession>} />
              <Route path="/p/:pid/jobs/:jid" element={<RequireSession><JobPage /></RequireSession>} />
              <Route path="/templates/new/doc" element={<RequireSession><DocEditor /></RequireSession>} />
              <Route path="/templates/new/sheet" element={<RequireSession><SheetEditor /></RequireSession>} />
              <Route path="/templates/:tid/edit" element={<RequireSession><EditorSwitch /></RequireSession>} />
              <Route path="*" element={<div className="empty"><div className="big">页面不存在</div><a href="/">返回首页</a></div>} />
            </Routes>
          </Suspense>
        </BrowserRouter>
        </DialogProvider>
      </SessionProvider>
    </ToastProvider>
  </StrictMode>,
);

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { get } from "@platform/auth-client";

function EditorSwitch() {
  const { tid } = useParams();
  const [kind, setKind] = useState<string | null>(null);
  useEffect(() => { get(`/api/templates/${tid}`).then((t) => setKind(t.kind)).catch(() => setKind("error")); }, [tid]);
  if (!kind) return <div className="empty">加载中…</div>;
  if (kind === "builtin_doc") return <DocEditor />;
  if (kind === "builtin_sheet") return <SheetEditor />;
  return <div className="empty">该模板不是内置编辑器创建的，请在项目中直接在预览上设置字段。</div>;
}
