import { lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { RequireSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { WorkspaceApp } from "../../shared/AppFrame";
import { Empty } from "../../shared/states";
import { Alerts, Detail, Library, NewRequest, Requests } from "./Pages";
import "./messages";
import "./approvals.css";
const Editor = lazy(() => import("./Editor"));
function App() {
  const { t } = useI18n();
  return <WorkspaceApp appId="approvals" product="Approvals" sections={base => [
    { to: base, label: t("我的待办"), end: true }, { to: `${base}/sent`, label: t("已发起") },
    { to: `${base}/handled`, label: t("已处理") }, { to: `${base}/all`, label: t("全部申请") },
    { to: `${base}/templates`, label: t("审批模板") }, { to: `${base}/alerts`, label: t("审批提醒") },
  ]}>{ws => <Routes key={ws.id}>
    <Route index element={<Requests key="todo" ws={ws} view="todo" />} />
    <Route path="sent" element={<Requests key="sent" ws={ws} view="sent" />} />
    <Route path="handled" element={<Requests key="handled" ws={ws} view="handled" />} />
    <Route path="all" element={<Requests key="all" ws={ws} view="all" />} />
    <Route path="templates" element={<Library ws={ws} />} />
    <Route path="t/:tid" element={<Editor ws={ws} />} />
    <Route path="new/:tid" element={<NewRequest ws={ws} />} />
    <Route path="r/:rid" element={<Detail ws={ws} />} />
    <Route path="alerts" element={<Alerts ws={ws} />} />
    <Route path="*" element={<Empty title={t("页面不存在")} />} />
  </Routes>}</WorkspaceApp>;
}
export function approvalsRoutes() {
  return [<Route key="approvals" path="/approvals" element={<RequireSession><App /></RequireSession>} />,
    <Route key="approvals-ws" path="/approvals/w/:wid/*" element={<RequireSession><App /></RequireSession>} />];
}
