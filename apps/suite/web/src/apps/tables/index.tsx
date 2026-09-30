import { Route, Routes } from "react-router-dom";
import { RequireSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { WorkspaceApp } from "../../shared/AppFrame";
import { Library, TablePage, RecordLink } from "./pages";
import "./messages";
import "./tables.css";

function TablesApp() {
  const {t} = useI18n();
  return <WorkspaceApp appId="tables" product="Tables" sections={base => [{to: base, label: t("数据表目录"), end: true}]}>
    {ws => <Routes key={ws.id}>
      <Route index element={<Library ws={ws} />} />
      <Route path="r/:rid" element={<RecordLink ws={ws} />} />
      <Route path="t/:tid/*" element={<TablePage ws={ws} />} />
    </Routes>}
  </WorkspaceApp>;
}
export function tablesRoutes() {
  return [<Route key="tables" path="/tables" element={<RequireSession><TablesApp /></RequireSession>} />,
    <Route key="tables-ws" path="/tables/w/:wid/*" element={<RequireSession><TablesApp /></RequireSession>} />];
}
