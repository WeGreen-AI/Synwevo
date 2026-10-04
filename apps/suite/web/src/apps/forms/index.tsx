/** Synwevo Forms' pages: `/forms/w/<workspace>/…` inside the workspace frame, and the fill-in link
 *  `/forms/fill/<token>` outside it (a filler may have no account). */
import { lazy } from "react";
import { Route, Routes } from "react-router-dom";
import { RequireSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { WorkspaceApp, canWrite } from "../../shared/AppFrame";
import { Library, RequestDetail, Requests, SubmissionDetail, Submissions } from "./Pages";
import "./messages";
import "./forms.css";

const Editor = lazy(() => import("./Editor"));
const Fill = lazy(() => import("./Fill"));

function FormsApp() {
  const { t } = useI18n();
  return <WorkspaceApp appId="forms" product="Forms" sections={(base) => [
    { to: base, label: t("表单库"), end: true },
    { to: `${base}/requests`, label: t("填写请求") },
    { to: `${base}/submissions`, label: t("提交记录") },
  ]}>
    {(ws) => {
      const w = canWrite(ws.role);
      return <Routes>
        <Route index element={<Library ws={ws} canWrite={w} />} />
        <Route path="f/:fid" element={<Editor ws={ws} canWrite={w} />} />
        <Route path="requests" element={<Requests ws={ws} canWrite={w} />} />
        <Route path="r/:rid" element={<RequestDetail ws={ws} canWrite={w} />} />
        <Route path="submissions" element={<Submissions ws={ws} />} />
        <Route path="s/:sid" element={<SubmissionDetail ws={ws} canWrite={w} />} />
      </Routes>;
    }}
  </WorkspaceApp>;
}

/** The fill-in link: for people without an account. The shell renders it outside the session and
 *  the application catalogue, which ask signed-in questions (/api/auth/me, /api/suite/catalog)
 *  an anonymous filler can only be refused. */
export function formsPublicRoutes() {
  return [<Route key="forms-fill" path="/forms/fill/:token" element={<Fill />} />];
}

export function formsRoutes() {
  return [
    <Route key="forms" path="/forms" element={<RequireSession><FormsApp /></RequireSession>} />,
    <Route key="forms-ws" path="/forms/w/:wid/*" element={<RequireSession><FormsApp /></RequireSession>} />,
  ];
}
