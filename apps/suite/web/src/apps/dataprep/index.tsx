import { Route, Routes, useLocation } from "react-router-dom";
import { RequireSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { WorkspaceApp, canWrite } from "../../shared/AppFrame";
import { Library, ImportPage, DatasetPage, RunPage } from "./Pages";
import { Editor } from "./Editor";
import "./messages";
import "./dataprep.css";

function DataPrepApp() {
  const { t } = useI18n(), location = useLocation();
  return <WorkspaceApp appId="dataprep" product="DataPrep" sections={base => [
    { to: base, label: t("数据集"), end: true }, { to: `${base}/recipes`, label: t("处理方案") }, { to: `${base}/runs`, label: t("运行历史") },
  ]}>{ws => <Routes key={`${ws.id}:${location.pathname}`}>
    <Route index element={<Library ws={ws} write={canWrite(ws.role)} kind="dataset" />} />
    <Route path="import" element={<ImportPage ws={ws} write={canWrite(ws.role)} />} />
    <Route path="datasets/:id" element={<DatasetPage ws={ws} />} />
    <Route path="recipes" element={<Library ws={ws} write={canWrite(ws.role)} kind="recipe" />} />
    <Route path="recipes/:id" element={<Editor ws={ws} write={canWrite(ws.role)} />} />
    <Route path="runs" element={<Library ws={ws} write={canWrite(ws.role)} kind="run" />} />
    <Route path="runs/:id" element={<RunPage ws={ws} write={canWrite(ws.role)} />} />
  </Routes>}</WorkspaceApp>;
}
export function dataprepRoutes() {
  return [<Route key="dataprep" path="/dataprep" element={<RequireSession><DataPrepApp /></RequireSession>} />,
    <Route key="dataprep-ws" path="/dataprep/w/:wid/*" element={<RequireSession><DataPrepApp /></RequireSession>} />];
}
