/** The frame every application page sits in: the brand position with the application's name, the
 *  shared platform entries, the workspace this page works in, and the application's sections.
 *
 *  The workspace is part of the address (`/<app>/w/<workspace>/…`) rather than a remembered
 *  preference, so a deep link, a refresh or a second tab lands in the same workspace, and a page
 *  can only ever show and change one workspace's records — the one its address names. */
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Navigate, useNavigate, useParams } from "react-router-dom";
import { PlatformNav, setWorkspaceContext, useSession, workspaceContext } from "@synwevo/auth-client";
import { Brand, useI18n } from "@synwevo/ui";
import { useApp } from "./catalog";
import { Empty, Failure, Loading } from "./states";
import type { CatalogApp } from "./ops";

export interface Section { to: string; label: string; end?: boolean }

export function roleLabel(t: (k: string) => string, role: string | undefined): string {
  return role === "owner" ? t("所有者") : role === "admin" ? t("管理员") : role === "editor" ? t("编辑者")
    : role === "viewer" ? t("查看者") : "";
}

export function canWrite(role: string | undefined): boolean {
  return role === "owner" || role === "admin" || role === "editor";
}

function Topbar({ app, product }: { app: string; product: string }) {
  const { t } = useI18n();
  return <header className="topbar suite-topbar">
    <Brand href={`/${app}`} product={product} label={t("{app} 首页", { app: `Synwevo ${product}` })} markBelow={720} />
    <span className="spacer" />
    <PlatformNav />
  </header>;
}

/** A workspace-scoped application: resolves `:wid` against the catalogue and renders `children`
 *  with the workspace and the person's role there. Without a workspace in the address it moves to
 *  the one the session is in when the application is usable there, else the first usable one. */
export function WorkspaceApp({ appId, product, sections, children }: {
  appId: string; product: string; sections: (base: string) => Section[];
  children: (ws: { id: string; name: string; role: string }, app: CatalogApp) => ReactNode;
}) {
  const { t } = useI18n();
  const { wid } = useParams();
  const { me, refresh } = useSession();
  const { app, error, reload } = useApp(appId);
  const navigate = useNavigate();
  const ws = app?.workspaces.find((w) => w.id === wid);
  // The tab's workspace binding follows the address. Changing it invalidates responses still in
  // flight (auth-client's session generation), so the page renders only once it is bound — a
  // page that asked first would never see its answer — and the platform bar is refreshed so it
  // names the same workspace as the page.
  const [bound, setBound] = useState<string | null>(() => workspaceContext());
  useEffect(() => {
    if (!ws) return;
    if (workspaceContext() !== ws.id) {
      setWorkspaceContext(ws.id);
      void refresh();
    }
    setBound(ws.id);
  }, [ws?.id]);

  if (error) return <Frame appId={appId} product={product}><Failure error={error} onRetry={reload} /></Frame>;
  if (app === undefined) return <Frame appId={appId} product={product}><Loading /></Frame>;
  if (!app || app.workspaces.length === 0) {
    return <Frame appId={appId} product={product}>
      <Empty title={t("你目前没有可以使用{app}的工作空间。加入一个工作空间，或请工作空间管理员启用它。", { app: product })} />
    </Frame>;
  }
  if (!wid) {
    const preferred = app.workspaces.find((w) => w.id === me?.workspace_id) ?? app.workspaces[0];
    return <Navigate to={`/${appId}/w/${preferred.id}`} replace />;
  }
  if (!ws) {
    return <Frame appId={appId} product={product}>
      <Empty title={t("你不能在这个工作空间使用{app}，或它已不可用。", { app: product })}>
        <p>{t("你在这些工作空间可以使用{app}：", { app: product })}</p>
        <ul className="suite-ws-list">{app.workspaces.map((w) => <li key={w.id}>
          <button type="button" className="btn ghost sm" onClick={() => navigate(`/${appId}/w/${w.id}`)}>{w.name}</button>
        </li>)}</ul>
      </Empty>
    </Frame>;
  }
  if (bound !== ws.id) return <Frame appId={appId} product={product}><Loading /></Frame>;
  const base = `/${appId}/w/${ws.id}`;
  return <Frame appId={appId} product={product} bar={<>
    <label className="suite-ws">
      <span className="sr-only">{t("在哪个工作空间使用")}</span>
      <select className="input sm" value={ws.id} aria-label={t("在哪个工作空间使用")}
              onChange={(e) => navigate(`/${appId}/w/${e.target.value}`)}>
        {app.workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
      </select>
      <span className="badge">{roleLabel(t, ws.role)}</span>
    </label>
    <nav className="suite-sections" aria-label={t("应用导航")}>
      {sections(base).map((s) => <NavLink key={s.to} to={s.to} end={s.end}
                                           className={({ isActive }) => `suite-section${isActive ? " on" : ""}`}>{s.label}</NavLink>)}
    </nav>
  </>}>
    {!canWrite(ws.role) && <div className="notice suite-readonly">{t("只读：你在这个工作空间是查看者。")}</div>}
    {children(ws, app)}
  </Frame>;
}

export function Frame({ appId, product, bar, children }: { appId: string; product: string; bar?: ReactNode; children: ReactNode }) {
  return <div className="app suite-app">
    <Topbar app={appId} product={product} />
    {bar && <div className="suite-bar">{bar}</div>}
    <main className="suite-main">{children}</main>
  </div>;
}
