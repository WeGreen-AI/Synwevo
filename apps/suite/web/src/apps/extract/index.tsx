import { Route, Routes } from "react-router-dom";
import { RequireSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { WorkspaceApp, canWrite } from "../../shared/AppFrame";
import { Library, TemplateEditor, Tasks, TaskDetail } from "./pages";
import "./messages";
import "./extract.css";

function ExtractApp() {
  const { t } = useI18n();
  return <WorkspaceApp appId="extract" product="Extract" sections={base => [
    { to: base, label: t("提取文件"), end: true },
    { to: `${base}/templates`, label: t("提取模板") },
    { to: `${base}/review`, label: t("待复核队列") },
  ]}>{ws => <Routes>
    <Route index element={<Tasks key={ws.id} ws={ws} canWrite={canWrite(ws.role)} />} />
    <Route path="review" element={<Tasks key={`${ws.id}-review`} ws={ws} canWrite={canWrite(ws.role)} review />} />
    <Route path="templates" element={<Library key={ws.id} ws={ws} canWrite={canWrite(ws.role)} />} />
    <Route path="templates/:tid" element={<TemplateEditor key={ws.id} ws={ws} canWrite={canWrite(ws.role)} />} />
    <Route path="tasks/:tid" element={<TaskDetail key={ws.id} ws={ws} canWrite={canWrite(ws.role)} />} />
  </Routes>}</WorkspaceApp>;
}
export function extractRoutes() {
  return [<Route key="extract" path="/extract" element={<RequireSession><ExtractApp /></RequireSession>} />,
    <Route key="extract-ws" path="/extract/w/:wid/*" element={<RequireSession><ExtractApp /></RequireSession>} />];
}
