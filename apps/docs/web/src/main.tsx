import { serverText, issueText } from "./lib/presentation";
import "./lib/presentation";
import { messages } from "./messages";
import { t as tr, useI18n, I18nProvider, LanguageMenu, registerMessages } from "@synwevo/ui";
import { StrictMode, lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { DialogProvider, ToastProvider, initTheme } from "@synwevo/ui";
import { RequireSession, SessionProvider, useSession } from "@synwevo/auth-client";
import Home from "./pages/Home";
import Workspace from "./pages/Workspace";
import JobPage from "./pages/JobPage";
import AboutPage from "./pages/About";
import RegressionPage from "./pages/Regression";
import Capabilities from "./pages/Capabilities";
import "@synwevo/ui/styles.css";
import "./styles.css";

const DocEditor = lazy(() => import("./pages/DocEditor"));
const SheetEditor = lazy(() => import("./pages/SheetEditor"));

registerMessages(messages);
initTheme();

function DocsRoot() {
  useI18n();
  return <I18nProvider title="Synwevo Docs · 文档工作台">
  <StrictMode>
    <ToastProvider>
      <SessionProvider>
        <DialogProvider>
        <BrowserRouter>
          <Suspense fallback={<div className="empty">{tr("加载中…")}</div>}>
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
              <Route path="/templates/new/doc" element={<RequireSession><RequireEditor><DocEditor /></RequireEditor></RequireSession>} />
              <Route path="/templates/new/sheet" element={<RequireSession><RequireEditor><SheetEditor /></RequireEditor></RequireSession>} />
              <Route path="/templates/:tid/edit" element={<RequireSession><RequireEditor><EditorSwitch /></RequireEditor></RequireSession>} />
              <Route path="*" element={<div className="empty"><LanguageMenu /><div className="big">{tr("页面不存在")}</div><a href="/">{tr("返回首页")}</a></div>} />
            </Routes>
          </Suspense>
        </BrowserRouter>
        </DialogProvider>
      </SessionProvider>
    </ToastProvider>
  </StrictMode>
  </I18nProvider>;
}
createRoot(document.getElementById("root")!).render(<DocsRoot />);

import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { get } from "@synwevo/auth-client";
import { errorText, permissions, READ_ONLY_TEXT } from "./lib/perm";

function RequireEditor({ children }: { children: React.ReactNode }) {
  useI18n();
  const { me, disabled } = useSession();
  if (!permissions(me, disabled).canWrite) return <div className="empty"><div className="notice info">{tr(READ_ONLY_TEXT)}</div><Link to="/">{tr("返回首页")}</Link></div>;
  return children;
}

/** Opens the right editor for a template. A failed lookup (not found, another workspace's
 *  template, the network) is reported as what it is — it used to be shown as "not a built-in
 *  template, set fields on the preview instead", which sent people looking in the wrong place. */
function EditorSwitch() {
  useI18n();
  const { tid } = useParams();
  const [kind, setKind] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setKind(null);
    setErr(null);
    get(`/api/templates/${tid}`).then((t) => alive && setKind(t.kind)).catch((e) => alive && setErr(errorText(e)));
    return () => { alive = false; };
  }, [tid]);
  if (err) return <div className="empty" role="alert"><div className="big">{tr("无法打开模板")}</div><div className="notice err" style={{ maxWidth: 480 }}>{serverText(err)}</div><Link to="/">{tr("返回首页")}</Link></div>;
  if (!kind) return <div className="empty" role="status">{tr("加载中…")}</div>;
  if (kind === "builtin_doc") return <DocEditor key={tid} />;
  if (kind === "builtin_sheet") return <SheetEditor key={tid} />;
  return <div className="empty"><div>{tr("该模板是上传的文件，不是内置编辑器创建的：请在项目中直接在模板预览上设置字段。")}</div><Link to="/">{tr("返回首页")}</Link></div>;
}
