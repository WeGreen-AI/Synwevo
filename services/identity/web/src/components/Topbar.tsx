import { type ReactNode, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { ApiError, platformHandoffUrl, useSession, MessagesLink, messagesHref, NotificationBell } from "@synwevo/auth-client";
import { Brand, Drawer, LanguageMenu, useI18n, useMediaQuery } from "@synwevo/ui";
import { chooseReturnTarget, rememberOrigin } from "../lib/returnTarget";
import "./Topbar.messages";

/** Account and governance share one header.
 *
 *  Every page under the account origin has the same two ways out: back to the work this person
 *  came from ("返回 Synwevo Docs" / "返回工作空间", chosen from the server's list — see
 *  lib/returnTarget.ts) and, on sub-pages, back to the account overview. Neither depends on
 *  browser history, so a page opened from a link, a refresh or a bookmark still has an exit.
 *  Below 720px the navigation moves into a drawer; the back link stays in the bar. */
/** The parent page of an account sub-page: product navigation, never history.back(), so a page
 *  opened directly, from a bookmark, a mail or after a provider round trip still has a way up.
 *  Labels are catalogue keys (the parent page's own name). */
export function parentOf(pathname: string): { href: string; label: string } | null {
  if (pathname === "/account" || pathname === "/") return null;
  if (/^\/organizations\/[^/]+/.test(pathname)) return { href: "/organizations", label: "组织" };
  if (/^\/admin\/[^/]+/.test(pathname)) return { href: "/admin", label: "平台管理" };
  if (pathname === "/admin" || pathname === "/organizations") return { href: "/account", label: "账户与安全" };
  if (pathname.startsWith("/account/")) return { href: "/account", label: "账户与安全" };
  return { href: "/account", label: "账户与安全" };
}

export function Topbar({ title, children }: { title: ReactNode; children?: ReactNode }) {
  const { t, locale } = useI18n();
  const { state, me, signOut } = useSession();
  const { pathname, search } = useLocation();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ error: unknown } | null>(null);
  const [open, setOpen] = useState(false);
  const narrow = useMediaQuery("(max-width: 720px)");
  useEffect(() => { rememberOrigin(search); }, [search]);
  useEffect(() => { if (!narrow) setOpen(false); }, [narrow]);
  useEffect(() => { setOpen(false); }, [pathname]);
  const link = (destination: string) => {
    try {
      const url = new URL(destination, window.location.origin);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid destination");
      url.searchParams.set("lang", locale);
      return url.origin === window.location.origin ? url.pathname + url.search + url.hash : url.href;
    } catch { return `/?lang=${locale}`; }
  };
  // Two ways back to the work, always in the bar (not only in the drawer, not only the logo):
  // the workspace — through the portal's own sign-in start so the portal has its session and the
  // current workspace is restored — and, when the person arrived from an application, that
  // application. Sub-pages also get a link to their real parent page, so a page opened from a
  // bookmark, a mail or a refresh has a way up that does not depend on browser history.
  const workspaceUrl = platformHandoffUrl(state?.portal_url, { next: "/workspace", workspaceId: me?.workspace_id || undefined, lang: locale });
  const back = chooseReturnTarget(me);
  const appBack = back && back.id !== "portal" ? back : null;
  const parent = parentOf(pathname);
  const backLabel = appBack ? t("返回 {app}", { app: appBack.name }) : null;
  const overview = pathname !== "/account";
  const isAdmin = me?.platform_role === "admin";
  const canApi = isAdmin && !!me?.permissions?.includes("api_platform.manage");
  const current = (path: string) => (pathname === path || pathname.startsWith(path + "/") ? "page" : undefined);
  const items = (
    <>
      <a className="btn ghost sm" href={link("/account")} aria-current={pathname === "/account" ? "page" : undefined}>{t("账户与安全")}</a>
      <a className="btn ghost sm" href={link("/organizations")} aria-current={current("/organizations")}>{t("组织")}</a>
      {/* Conversations are Synwevo Message's (CI-66): the registry address through its sign-in
          start; the account origin's own /messages (a server redirect) only while it is not registered. */}
      <MessagesLink href={messagesHref(state, { lang: locale })} />
      {isAdmin && <a className="btn ghost sm" href={link("/admin")} aria-current={pathname === "/admin" ? "page" : undefined}>{t("平台管理")}</a>}
      {canApi && <a className="btn ghost sm" href={link("/admin/api-platform")} aria-current={current("/admin/api-platform")}>{t("API 管理")}</a>}
    </>
  );
  const signOutButton = (
    <button type="button" className="btn ghost sm" disabled={busy} onClick={async () => {
      setBusy(true); setFailure(null);
      try { await signOut(); }
      catch (error) { setFailure({ error }); setBusy(false); }
    }}>{t(busy ? "正在退出…" : "退出登录")}</button>
  );
  return <><header className="topbar wrap acct-topbar">
    <Brand href={platformHandoffUrl(state?.portal_url, { next: "/workspace", lang: locale }) ?? link("/")} product="Account" label={t("返回工作空间")} markBelow={720} />
    <nav className="acct-back row gap4" aria-label={t("返回")}>
      {workspaceUrl && <a className="btn ghost sm acct-back-link acct-back-workspace" href={workspaceUrl}>← {t(narrow ? "工作空间" : "返回工作空间")}</a>}
      {appBack && <a className="btn ghost sm acct-back-link" href={link(appBack.url)} title={appBack.workspace_name ? t("回到「{name}」继续之前的任务", { name: appBack.workspace_name }) : undefined}>← {backLabel}</a>}
      {parent && <a className="btn ghost sm acct-back-link acct-back-parent" href={link(parent.href)}>← {t(parent.label)}</a>}
    </nav>
    <b className="grow acct-title">{title}</b>{children}
    {narrow ? (
      <>
        <button type="button" className="btn ghost sm" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(true)}>{t("菜单")} <span aria-hidden="true">☰</span></button>
        {open && <Drawer title={t("账户导航")} onClose={() => setOpen(false)}>
          {me && <div className="drawer-label" aria-hidden="true">{me.email}</div>}
          {workspaceUrl && <a className="btn ghost" href={workspaceUrl}>← {t("返回工作空间")}</a>}
          {appBack && <a className="btn ghost" href={link(appBack.url)}>← {backLabel}</a>}
          {items}
          <hr />
          <div className="row gap8 wrap" style={{ padding: "4px 8px" }}><NotificationBell preferencesHref={link("/account#notifications")} /><LanguageMenu /></div>
          {signOutButton}
        </Drawer>}
      </>
    ) : (
      <nav className="row wrap gap4" aria-label={t("账户导航")}>
        {items}
        <NotificationBell preferencesHref={link("/account#notifications")} />
        <LanguageMenu />
        {signOutButton}
      </nav>
    )}
  </header>{failure && <p className="notice err" role="alert">{failure.error instanceof ApiError ? failure.error.message : t("退出登录失败，请稍后重试。")}</p>}</>;
}
