import { type ReactNode, useEffect, useMemo, useRef } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { ApiError, get, useSession } from "@synwevo/auth-client";
import { PageState, useI18n, useMediaQuery } from "@synwevo/ui";
import { Topbar } from "../components/Topbar";
import AdminStepUp from "../components/AdminStepUp";
import { useLoad } from "../lib/hooks";
import { errText } from "../governance/labels";
import type { Held } from "../governance/access";
import { adminGroups, adminHref, resolveAdminPage, type AdminPage, type AdminGroup } from "./navigation";
import "../governance/messages";
import "./messages";
import "./admin.css";

type AdminSession = {
  user: { id: string; email: string };
  permissions: { permission: string; sources: unknown[] }[];
  step_up: { required: boolean; amr: string };
  environment: { instance: string };
};

export function AdminNavigation({ groups, active, search }: { groups: AdminGroup[]; active: AdminPage; search: string }) {
  const { t } = useI18n();
  return <nav className="admin-navigation" aria-label={t("管理员导航")}>
    {groups.map(group => <section className="admin-nav-group" key={group.label}>
      <h2>{t(group.label)}</h2>
      {group.items.map(item => <Link className="admin-nav-link" key={item.id} to={adminHref(item.id, search)}
        aria-current={active === item.id ? "page" : undefined}>{t(item.label)}</Link>)}
    </section>)}
  </nav>;
}

/** The session endpoint is authoritative for navigation and the step-up state. Locked
 * administrators may read it; no management child mounts until they complete step-up. */
export function AdminLayout({ children, contentClassName = "" }: {
  children: (state: { held: Held; active: AdminPage }) => ReactNode;
  contentClassName?: string;
}) {
  const { t } = useI18n();
  const { me } = useSession();
  const { pathname, search } = useLocation();
  const narrow = useMediaQuery("(max-width: 720px)");
  const key = `${me?.id}:${me?.admin_locked}:${me?.platform_permissions?.join(",")}:${pathname}:${search}`;
  const session = useLoad<AdminSession>(key, signal => get("/api/admin/session", signal));
  const held = useMemo(() => new Set(session.data?.permissions.map(item => item.permission) || []), [session.data]);
  const groups = adminGroups(held);
  const active = resolveAdminPage(pathname, new URLSearchParams(search).get("tab"), held);
  const group = groups.find(group => group.items.some(item => item.id === active));
  const title = group?.items.find(item => item.id === active)?.label || "管理员中心";
  const locked = !!me?.admin_locked || !!session.data?.step_up.required;
  const heading = useRef<HTMLHeadingElement>(null);
  const previous = useRef(pathname + search);
  useEffect(() => { document.title = `${t(title)} · Synwevo Admin`; }, [t, title]);
  useEffect(() => {
    // Re-entering an old browser tab re-checks grants. Business APIs still decide each request.
    const check = () => session.reload();
    window.addEventListener("focus", check);
    return () => window.removeEventListener("focus", check);
  }, [session.reload]);
  useEffect(() => {
    const location = pathname + search;
    if (previous.current !== location && !session.loading) {
      heading.current?.focus(); previous.current = location;
    }
  }, [pathname, search, session.loading]);
  const missing = session.error instanceof ApiError && !session.error.upstream && session.error.status === 404;
  if (missing || (session.data && !session.loading && !session.error && !active)) {
    return <PageState as="main"><h1>{t("页面不存在")}</h1><a href="/account">{t("前往账户设置")}</a></PageState>;
  }
  if (active === "api-platform" && pathname === "/admin" && !locked && !session.error) {
    return <Navigate to={adminHref(active, search)} replace />;
  }
  const ready = !!session.data && !session.error;
  const navigation = ready && !locked && active ? <AdminNavigation groups={groups} active={active} search={search} /> : null;
  const instance = session.data?.environment.instance;
  const environment = !session.data ? t("环境信息暂不可用") : instance === "test" ? t("测试实例")
    : instance === "normal" ? t("常规实例") : t("实例类型未标注");
  return <div className="app admin-app">
    <a className="admin-skip" href="#admin-main">{t("跳到主要内容")}</a>
    <Topbar title={t("管理员中心")} product="Admin" mobileNavigation={navigation} />
    <div className={`admin-frame${!navigation ? " admin-frame-single" : ""}`}>
      {!narrow && navigation && <aside className="admin-sidebar">{navigation}</aside>}
      <main id="admin-main" className={`admin-main ${contentClassName}`} tabIndex={-1}>
        <div className="admin-context">
          <nav className="admin-breadcrumb" aria-label={t("当前位置")}>
            <Link to={adminHref("overview", search)}>{t("管理员中心")}</Link>
            {group && <><span aria-hidden="true">/</span><span>{t(group.label)}</span></>}
            {active && <><span aria-hidden="true">/</span><span aria-current="page">{t(title)}</span></>}
          </nav>
          <div className="admin-scope"><span className="badge">{t("作用范围：平台")}</span>
            <span className={`badge${instance === "test" ? " warn" : ""}`}>{environment}</span>
            <span className="admin-host muted">{window.location.host}</span>
          </div>
        </div>
        <div className="admin-content acct-page">
          <div className="admin-page-heading"><div><h1 ref={heading} tabIndex={-1}>{t(title)}</h1>
            <p className="muted">{t("平台治理不授予组织、工作空间或文档内容的访问权。")}</p></div>
            <button className="btn sm" disabled={session.loading} onClick={session.reload}>{t("刷新管理权限")}</button>
          </div>
          {session.error ? <div className="notice err" role="alert">{errText(session.error)} <button className="btn sm" onClick={session.reload}>{t("重试")}</button></div>
            : !ready ? <p role="status">{t("正在核实管理权限…")}</p>
            : locked ? <AdminStepUp compact />
            : active && children({ held, active })}
        </div>
      </main>
    </div>
  </div>;
}
