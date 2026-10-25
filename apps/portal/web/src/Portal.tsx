import { useEffect, useMemo, useState, type ReactNode } from "react";
import { AppIcon, Brand, LanguageMenu, Logo, Menu, useI18n, useMediaQuery } from "@synwevo/ui";
import { ApiError, get, holdsPlatformAccess, post, setWorkspaceContext, SignedOut, type Me, useSession } from "@synwevo/auth-client";
import { PublicHome } from "./PublicHome";
import { PublicPage } from "./PublicPages";
import { PUBLIC_META, isWorkspacePath, publicPath, publicRoute, siteHref, workspaceSurface } from "./publicRoutes";
import "./messages";
import { MessagesLink, messagesHref, NotificationBell } from "@synwevo/auth-client";

/** What each application on a workspace card is for, in one line. */
function appDescription(t: (key: string) => string, id: string): string {
  switch (id) {
    case "docweave": return t("从来源数据到可交付的文档。");
    case "messages": return t("组织内的私聊与群聊。");
    case "forms": return t("设计表单、发出填写请求并审阅提交。");
    case "tables": return t("带类型字段与修订记录的业务数据表。");
    case "dataprep": return t("导入、清理与合并数据，逐行报告问题。");
    case "approvals": return t("多阶段审批、退回修订与完整时间线。");
    case "extract": return t("从 PDF 与图片提取字段，并标出原文位置。");
    default: return t("打开已授权的应用。");
  }
}

export const signInUrl = "/auth/start?next=%2Fworkspace";
type Workspace = { id: string; name: string; kind: string; role: string; status?: string; org_id?: string | null; org_name?: string | null; organization_id?: string | null; organization_name?: string | null };
type App = { id: string; name: string; icon: string | null; launch_url: string; workspace_id: string | null; workspace_name: string | null };
type Invitation = { id: string; workspace_id: string; workspace_name: string; role: string; expires_at: number };
type Home = {
  platform: { name: string }; user: Pick<Me, "id" | "email" | "display_name" | "platform_role" | "platform_permissions">; workspaces: Workspace[]; current_workspace_id: string | null;
  apps: App[]; account_url: string; pending_invitations?: Invitation[];
  organizations?: {id: string; name: string; status: string; role: string}[];
};
type State = { phase: "loading" } | { phase: "signed-out" } | { phase: "error"; status?: number } | { phase: "ready"; home: Home; me: Me };

export function safeLink(value: string, fallback = "/account"): string {
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : fallback;
  } catch { return fallback; }
}
/** A link into the account service. `from=platform` lets the account page's "back" link return
 *  to the portal; the registration page takes no such hint. */
export function accountLink(base: string, path: string): string {
  const joiner = path.includes("?") ? "&" : "?";
  return safeLink(`${base.replace(/\/$/, "")}${path}${path.startsWith("/register") ? "" : `${joiner}from=platform`}`, path);
}
export function Portal() {
  const { t, locale } = useI18n();
  const session = useSession();
  const route = useMemo(() => (workspaceSurface() ? null : publicRoute(window.location.pathname)), []);
  const publicPage = route !== null;
  const [state, setState] = useState<State>({ phase: "loading" });
  const [revision, setRevision] = useState(0);
  const [switching, setSwitching] = useState<string | null>(null);
  const [switchError, setSwitchError] = useState(false);
  const accountBase = session.state?.account_url?.replace(/\/account\/?$/, "") || "";
  const narrow = useMediaQuery("(max-width: 760px)");

  // The dashboard request does not wait for the session probe: the two leave together on a
  // fresh page, and the one that answers second completes the picture. A later revalidation
  // (returning to the tab) replaces the data in place — the page never drops back to a
  // spinner while it already shows a dashboard, which is what made the sidebar "not respond":
  // it was being unmounted under the pointer.
  const [dash, setDash] = useState<{ value: Home | null; error: ApiError | null; at: number }>({ value: null, error: null, at: 0 });
  const signedOut = session.me === null;
  const meId = session.me?.id;
  // The dashboard belongs to one person: when the session turns out to be someone else's (a
  // different account signed in meanwhile), fetch again. Learning *who* after a cold start is not
  // a change of person, so it does not refetch — one request per load, not two.
  useEffect(() => {
    if (meId && dash.value && dash.value.user.id !== meId) setRevision(n => n + 1);
  }, [meId, dash.value]);
  useEffect(() => {
    if (publicPage || signedOut) return;
    const controller = new AbortController();
    void get<Home>("/api/portal/home", controller.signal)
      .then(value => { if (!controller.signal.aborted) setDash({ value, error: null, at: Date.now() }); })
      .catch(error => { if (!controller.signal.aborted) setDash(h => ({ value: error instanceof ApiError && error.status === 401 ? null : h.value, error: error instanceof ApiError ? error : new ApiError(0, "network", String(error)), at: Date.now() })); });
    return () => controller.abort();
  }, [publicPage, revision, signedOut]);
  useEffect(() => {
    const me = session.me;
    if (session.error) { setState({ phase: "error", status: session.error.status }); return; }
    if (me === null || (dash.error && dash.error.status === 401 && !dash.error.upstream)) { setState({ phase: "signed-out" }); return; }
    if (me === undefined) { setState(s => (s.phase === "ready" ? s : { phase: "loading" })); return; }
    if (dash.value && dash.value.user.id === me.id) { setState({ phase: "ready", me, home: dash.value }); return; }
    if (dash.error) { setState(s => (s.phase === "ready" ? s : { phase: "error", status: dash.error?.status })); return; }
    setState(s => (s.phase === "ready" ? s : { phase: "loading" }));
  }, [session.me, session.error, dash]);

  useEffect(() => {
    // A page restored from the back-forward cache may show another account's dashboard: reload.
    const restore = (event: PageTransitionEvent) => { if (event.persisted) window.location.reload(); };
    window.addEventListener("pageshow", restore);
    return () => window.removeEventListener("pageshow", restore);
  }, []);

  useEffect(() => {
    const meta = route ? PUBLIC_META[route.page] : null;
    document.title = meta ? t(meta.title) : t("Synwevo Workspace · 你的工作空间");
    document.querySelector('meta[name="description"]')?.setAttribute("content", t(meta ? meta.description : PUBLIC_META.home.description));
    document.querySelector('meta[name="robots"]')?.setAttribute("content", publicPage ? "index, follow" : "noindex, nofollow");
    // Keep the shareable public URL and canonical metadata aligned with the language menu.
    if (route) {
      const path = publicPath(route.page, locale);
      window.history.replaceState(null, "", path + window.location.hash);
      const canonical = document.querySelector<HTMLLinkElement>('link[rel="canonical"]');
      if (canonical) canonical.href = new URL(path, canonical.href).href;
    }
  }, [locale, publicPage, route, t]);

  async function switchWorkspace(id: string) {
    if (switching !== null) return;
    setSwitchError(false); setSwitching(id);
    try {
      await post("/api/auth/workspace", { workspace_id: id });
      setWorkspaceContext(id);
      // A full navigation clears all application/context state before a different tenant renders.
      window.location.assign("/workspace");
    } catch { setSwitching(null); setSwitchError(true); await session.refresh(); setRevision(n => n + 1); }
  }

  const account = state.phase === "ready" ? state.home.account_url : accountBase;
  if (route) {
    // Three-valued: undefined while the session is still being confirmed, so a signed-in person
    // never sees a sign-in link flash before the page knows who they are.
    const signedIn = session.error ? false : session.me === undefined ? undefined : session.me !== null;
    if (route.page === "home") return <PublicHome signedIn={signedIn} registerUrl={accountLink(account, "/register")} accountBase={account} />;
    return <PublicPage page={route.page} signedIn={signedIn} registerUrl={accountLink(account, "/register")} accountBase={account} />;
  }
  // Where the account service's logout returns to after "退出登录" ended the platform session.
  if (window.location.pathname === "/signed-out") return <SignedOut product="Workspace" />;
  const siteHome = siteHref(session.state?.site_url, "/");
  if (!isWorkspacePath(window.location.pathname)) return <Shell right={<a className="pt-link" href={siteHome}>{t("返回首页")}</a>}>
    <section className="pt-status" role="status"><p className="pt-eyebrow">Synwevo Workspace</p><h1>{t("页面不存在")}</h1>
      <p>{t("这个地址在 Synwevo Workspace 里不存在。")}</p>
      <div className="pt-actions"><a className="btn primary" href="/workspace">{t("进入工作空间")}</a><a className="btn" href={siteHome}>{t("返回首页")}</a></div>
    </section>
  </Shell>;
  if (state.phase !== "ready") return <Shell right={<a className="pt-link" href={siteHome}>{t("返回首页")}</a>}>
    <section className="pt-status">
      {state.phase === "loading" ? <><span className="pt-loader" aria-hidden="true" /><h1 role="status">{t("正在载入工作空间…")}</h1></> : state.phase === "signed-out" ? <>
        <p className="pt-eyebrow">Synwevo Workspace</p><h1>{t("欢迎回来")}</h1>
        <p>{t("登录统一账户，继续你的工作。")}</p>
        <div className="pt-actions"><a className="btn primary lg" href={signInUrl}>{t("登录")}</a><a className="btn lg" href={accountLink(account, "/register")}>{t("注册账户")}</a></div>
      </> : <><h1>{t("暂时无法载入工作空间")}</h1><p role="alert">{t("请检查网络连接后重试。")}{state.status ? ` (HTTP ${state.status})` : null}</p>
        <button className="btn lg" onClick={() => { void session.refresh(); setRevision(n => n + 1); }}>{t("重试")}</button></>}
    </section>
  </Shell>;

  const { home, me } = state;
  const current = home.workspaces.find(w => w.id === home.current_workspace_id);
  const invitations = home.pending_invitations;
  // Any platform grant draws 平台管理 (the account service decides each view); both answers must agree.
  const isPlatformAdmin = holdsPlatformAccess(me) && holdsPlatformAccess(home.user);
  const canManageApi = isPlatformAdmin && me.permissions?.includes("api_platform.manage");
  const role = (value: string) => t(({ owner: "所有者", admin: "管理员", editor: "编辑者", viewer: "查看者" } as Record<string, string>)[value] || "成员");
  const unavailableOrganizations = new Set(home.organizations?.filter(org => org.status !== "active").map(org => org.id));
  const unavailable = (workspace: Workspace) => (workspace.status !== undefined && workspace.status !== "active") || unavailableOrganizations.has(workspace.organization_id || workspace.org_id || "");
  const groups = new Map<string, { name: string; workspaces: Workspace[] }>();
  for (const workspace of home.workspaces) {
    const key = workspace.organization_id || workspace.org_id || (workspace.kind === "personal" ? "__personal" : "__other");
    const group = groups.get(key) || { name: workspace.organization_name || workspace.org_name || t(workspace.kind === "personal" ? "个人工作空间" : "团队工作空间"), workspaces: [] };
    group.workspaces.push(workspace); groups.set(key, group);
  }
  const accountItems = [
    { label: <span className="app-identity"><AppIcon id="/account" />{t("账户与安全")}</span>, onSelect: () => window.location.assign(accountLink(account, "/account")) },
    ...(isPlatformAdmin ? [{ label: <span className="app-identity"><AppIcon id="/admin" />{t("平台管理")}</span>, onSelect: () => window.location.assign(accountLink(account, "/admin")) }] : []),
    ...(canManageApi ? [{ label: <span className="app-identity"><AppIcon id="/admin/api-platform" />{t("API 管理")}</span>, onSelect: () => window.location.assign(accountLink(account, "/admin/api-platform")) }] : []),
    // Leaving from the platform home itself: the shared sign-out ends the platform session (the
    // portal is a relying party) and returns to this origin's /signed-out page.
    { label: <span className="app-identity"><span className="app-icon-space" aria-hidden="true" />{t("退出登录")}</span>, onSelect: () => void session.signOut().catch(() => undefined) },
  ];
  return <Shell right={narrow ? <Menu ariaLabel={t("账户菜单")} buttonClass="btn ghost pt-account-btn" items={accountItems}
      trigger={<><span className="pt-avatar" aria-hidden="true">{(me.display_name || me.email).slice(0, 1).toUpperCase()}</span>{t("账户")}</>} /> : <div className="pt-account">
    <a className="pt-link" href={accountLink(account, "/account")}><AppIcon id="/account" />{t("账户与安全")}</a>
    {isPlatformAdmin && <><a className="pt-link" href={accountLink(account, "/admin")}><AppIcon id="/admin" />{t("平台管理")}</a>{canManageApi && <a className="pt-link" href={accountLink(account, "/admin/api-platform")}><AppIcon id="/admin/api-platform" />{t("API 管理")}</a>}</>}
    <span className="pt-avatar" title={me.email} aria-label={me.display_name || me.email}>{(me.display_name || me.email).slice(0, 1).toUpperCase()}</span>
    <button type="button" className="pt-link" onClick={() => void session.signOut().catch(() => undefined)}>{t("退出登录")}</button>
  </div>}>
    <div className="pt-dashboard-head"><div><p className="pt-eyebrow">Synwevo Workspace</p><h1>{t("你的工作，从这里继续。")}</h1><p className="pt-muted">{me.display_name || me.email}</p></div>
      <a className="btn" href={accountLink(account, "/onboarding")}>{t("创建或加入工作空间")}</a></div>
    <div className="pt-dashboard">
      <aside className="pt-spaces" aria-label={t("工作空间")}>
        <h2 className="app-identity"><AppIcon id="platform" size={24} />{t("工作空间")}</h2><p className="pt-hint">{t("只显示你已加入的工作空间。")}</p>
        {[...groups].map(([key, group]) => <div className="pt-space-group" key={key}><h3>{group.name}</h3>{group.workspaces.map(workspace => <button key={workspace.id}
          className={`pt-space${workspace.id === current?.id ? " is-current" : ""}`} aria-pressed={workspace.id === current?.id}
          disabled={switching !== null || unavailable(workspace)}
          onClick={() => { if (workspace.id !== current?.id) void switchWorkspace(workspace.id); }}>
          <span className="pt-space-icon" aria-hidden="true">{workspace.kind === "personal" ? "○" : "◇"}</span><span><strong>{workspace.name}</strong><small>{role(workspace.role)}{unavailable(workspace) ? ` · ${t("暂不可用")}` : ""}</small></span>
          {workspace.id === current?.id && <span className="pt-current-dot" aria-hidden="true" />}
        </button>)}</div>)}
        {!!home.organizations?.length && <div className="pt-orgs"><h3>{t("你的组织")}</h3>{home.organizations.map(org => <div key={org.id} className="pt-org"><a className="pt-link" href={accountLink(account, `/organizations/${encodeURIComponent(org.id)}`)}>{org.name}</a><small>{role(org.role)}{org.status !== "active" ? ` · ${t("暂不可用")}` : ""}</small></div>)}<p className="pt-hint">{t("组织成员身份不自动授予每个工作空间的访问权。")}</p></div>}
        {!home.workspaces.length && <p className="pt-small-empty">{t("你还没有加入工作空间。")}</p>}
      </aside>
      <div className="pt-content">
        <section aria-labelledby="pt-apps-title"><div className="pt-section-head"><div><p className="pt-eyebrow">{current?.organization_name || current?.org_name || t("当前工作空间")}</p><h2 id="pt-apps-title">{current?.name || t("选择你的工作空间")}</h2></div>
          {current && ["owner", "admin"].includes(current.role) && <a className="pt-link" href={accountLink(account, `/account/workspaces/${encodeURIComponent(current.id)}/members`)}>{t("成员与邀请")} <span aria-hidden="true">↗</span></a>}</div>
          {switchError && <p className="pt-error" role="alert">{t("切换失败，已重新核对当前工作空间，请重试。")}</p>}
          {switching !== null ? <div className="pt-empty" role="status"><span className="pt-loader" aria-hidden="true" /><h3>{t("正在切换工作空间…")}</h3><p>{t("页面将重新载入以更新权限。")}</p></div> : home.apps.length ? <ul className="pt-apps">{home.apps.map(app => <li key={`${app.id}:${app.workspace_id}`}>
            <a className="pt-app-card" href={safeLink(app.launch_url, "/workspace")}><AppIcon id={app.id} size={48} /><span className="pt-app-body"><strong>{app.name}</strong><span>{app.workspace_name}</span><span className="pt-app-description">{appDescription(t, app.id)}</span></span><span className="pt-card-arrow" aria-hidden="true">↗</span></a>
          </li>)}</ul> : <div className="pt-empty"><DocumentIcon /><h3>{t(home.workspaces.length ? "当前工作空间暂无可用应用" : "先加入一个工作空间")}</h3><p>{t(home.workspaces.length ? "请联系工作空间管理员确认应用是否启用，以及你的使用资格。" : "创建工作空间，或用发到当前邮箱的邀请加入团队。")}</p><a className="pt-link" href={accountLink(account, "/onboarding")}>{t("前往设置工作空间")} <span aria-hidden="true">→</span></a></div>}
          <p className="pt-context-note">{t("应用与文档权限按工作空间分别授予；切换不会合并内容。")}</p>
        </section>
        <section className="pt-invites" aria-labelledby="pt-invites-title"><div className="pt-section-head"><h2 id="pt-invites-title">{t("下一步")}</h2><a className="pt-link" href={accountLink(account, "/onboarding")}>{t("查看邀请")}</a></div>
          {invitations?.length ? <><p className="pt-muted">{t("邀请通过邮件中的链接确认后才生效；账户页可核对状态。")}</p><ul className="pt-invite-list">{invitations.map(invite => <li key={invite.id}><div><strong>{invite.workspace_name}</strong><p>{role(invite.role)} · {t("有效期至 {date}", { date: new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: me.timezone || "UTC" }).format(invite.expires_at * 1000) })}</p></div><a className="btn sm" href={accountLink(account, "/onboarding")}>{t("查看加入方式")}</a></li>)}</ul></> : <p className="pt-muted">{t(invitations ? "当前没有待接受的邀请。" : "到账户页查看邀请并完成工作空间设置。")}</p>}
        </section>
        {isPlatformAdmin && <p className="pt-admin-note">{t("平台管理权限不授予业务文档访问权。")}</p>}
      </div>
    </div>
  </Shell>;
}

export function DocumentIcon() {
  return <svg viewBox="0 0 32 36" width="32" height="36" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M7 3h12l6 6v24H7z" strokeLinejoin="round"/><path d="M19 3v7h6M12 17h8M12 22h8M12 27h5" strokeLinecap="round"/></svg>;
}
export function Shell({ right, children, publicHome = false }: { right?: ReactNode; children: ReactNode; publicHome?: boolean }) {
  const { t, locale } = useI18n();
  const { state, me } = useSession();      // the bell renders nothing without a session
  // The messages entry beside the bell (the same pair Docs shows in its platform bar): the
  // workspace is where people start, and it had no way into their conversations. Its target is
  // Synwevo Message's registered address (CI-66), through that origin's sign-in start.
  return <div className={`pt${publicHome ? " pt-public" : ""}`}><a className="pt-skip" href="#pt-main">{t("跳到主要内容")}</a>
    <header className="pt-top"><Brand href={siteHref(state?.site_url, "/")} size="lg" product={publicHome ? undefined : "Workspace"} label={t("Synwevo 首页")} />
      <nav className="pt-nav" aria-label={t("主导航")}>{right}{me && <MessagesLink href={messagesHref(state, { lang: locale })} />}<NotificationBell preferencesHref={(state?.account_url || "/account") + "#notifications"} /><LanguageMenu /></nav></header>
    <main className="pt-main" id="pt-main" tabIndex={-1}>{children}</main>
    <footer className="pt-footer"><Logo version="wordmark" height={14} /><a href={siteHref(state?.site_url, publicPath("privacy", locale))}>{t("数据与隐私")}</a><a href={siteHref(state?.site_url, publicPath("service", locale))}>{t("服务说明")}</a><a href={siteHref(state?.site_url, publicPath("terms", locale))}>{t("服务条款")}</a><a href={siteHref(state?.site_url, publicPath("ai-use", locale))}>{t("AI 使用规范")}</a><a href={siteHref(state?.site_url, publicPath("pricing", locale))}>{t("定价")}</a><a href="/api/source-code" download title={t("下载正在运行版本的对应源代码")}>{t("源代码（AGPL-3.0）")}</a><span>{t("协同 · 编织 · 持续完善")}</span></footer>
  </div>;
}
